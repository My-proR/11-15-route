import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy.exc import DBAPIError

from .common import error_body, limiter
from .config import get_settings
from .db import dispose_engine
from .routers import core, dispatch, imports, masterdata, notifications, realtime, reports, sos, tracking, trip_credentials, driver_self, fleet, invites, billing
from .services.realtime import hub

log = logging.getLogger("routepilot")
REQS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
LAT = Histogram("http_request_seconds", "HTTP latency", ["method", "route"], buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logging.basicConfig(level=s.log_level, format='{"ts":"%(asctime)s","lvl":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    await hub.start()
    stop_worker = asyncio.Event()
    worker_task = None
    if s.run_worker_in_api:
        from . import worker  # imported lazily: only needed in single-process mode
        worker_task = asyncio.create_task(worker.run_loop(stop_worker), name="embedded-worker")
    yield
    stop_worker.set()
    if worker_task is not None:
        try:
            await asyncio.wait_for(worker_task, timeout=10)
        except asyncio.TimeoutError:
            worker_task.cancel()
    await hub.stop()
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version="1.0.0", lifespan=lifespan,
                  docs_url="/docs" if s.app_env != "production" else None, redoc_url=None,
                  openapi_url="/openapi.json" if s.app_env != "production" else None)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=False, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Organization-Id"], max_age=600)

    @app.middleware("http")
    async def wrap(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        start = time.perf_counter()
        # coarse per-client limit; authenticated user limits are applied per endpoint where it matters
        if request.url.path.startswith(s.api_v1_prefix) and request.method != "OPTIONS":
            # Coarse pre-authentication limit keys by client address, never by a caller-controlled token. It is per IP, so offices sharing one
            # address share it: raise RATE_LIMIT_PER_MINUTE accordingly. Sensitive authenticated routes add a principal-scoped limit.
            ident = request.client.host if request.client else "anon"
            if not await limiter.hit(f"g:{ident}", s.rate_limit_per_minute):
                return JSONResponse(status_code=429, content={"error": {"code": "rate_limited", "message": "Too many requests", "details": None}}, headers={"Retry-After": "30"})
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error rid=%s path=%s", rid, request.url.path)
            response = JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "Unexpected error", "details": {"request_id": rid}}})
        route = request.scope.get("route")
        label = getattr(route, "path", "unmatched")
        REQS.labels(request.method, label, response.status_code).inc()
        LAT.labels(request.method, label).observe(time.perf_counter() - start)
        response.headers["X-Request-Id"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_exc(_: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.status_code, exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_exc(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(x) for x in e["loc"][1:]) or str(e["loc"][0]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "Request validation failed", "details": details}})

    @app.exception_handler(DBAPIError)
    async def db_exc(_: Request, exc: DBAPIError):
        code = getattr(exc.orig, "sqlstate", None) or getattr(getattr(exc.orig, "__cause__", None), "sqlstate", None)
        msg = str(exc.orig)
        if code == "23505":
            return JSONResponse(status_code=409, content={"error": {"code": "duplicate", "message": "A record with these unique values already exists", "details": None}})
        if code == "23503":
            return JSONResponse(status_code=422, content={"error": {"code": "invalid_reference", "message": "A referenced record does not exist in this organization", "details": None}})
        if code == "23514":
            if "capacity exceeded" in msg:
                return JSONResponse(status_code=409, content={"error": {"code": "capacity_exceeded", "message": "Vehicle capacity exceeded", "details": None}})
            if "disabled" in msg:
                return JSONResponse(status_code=403, content={"error": {"code": "feature_disabled", "message": "This feature is disabled", "details": None}})
            return JSONResponse(status_code=409, content={"error": {"code": "constraint_violation", "message": "The change violates a data rule", "details": None}})
        if code == "42501":
            return JSONResponse(status_code=403, content={"error": {"code": "forbidden", "message": "Not permitted", "details": None}})
        if code == "22P02" or code == "22007" or code == "22008":
            return JSONResponse(status_code=422, content={"error": {"code": "invalid_value", "message": "A value has an invalid format", "details": None}})
        if code == "57014":
            return JSONResponse(status_code=503, content={"error": {"code": "timeout", "message": "The database took too long; retry", "details": None}})
        log.error("database error sqlstate=%s", code)
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "Unexpected error", "details": None}})

    prefix = s.api_v1_prefix
    for r in (core.router, masterdata.router, imports.router, dispatch.router, sos.router, tracking.router, trip_credentials.router, driver_self.router, fleet.router, invites.router, billing.router, notifications.router, reports.router, realtime.router):
        app.include_router(r, prefix=prefix)
    app.add_api_route("/health/live", core.live, methods=["GET"], tags=["health"])
    app.add_api_route("/health/ready", core.ready, methods=["GET"], tags=["health"])

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        tok = get_settings().metrics_token
        if not tok or request.headers.get("authorization") != f"Bearer {tok}":
            return PlainTextResponse("not found", status_code=404)
        return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
