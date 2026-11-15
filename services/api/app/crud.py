"""Generic tenant-scoped CRUD used by master-data routers.

Column names come only from the Pydantic model fields (never from request text); values are always bound parameters;
organization_id always comes from the authenticated principal. RLS enforces the same boundaries again in PostgreSQL.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .auth import Principal, db_for, require_roles
from .common import AppError, audit, client_ip, page_params, page_response, sort_clause


@dataclass
class Entity:
    table: str
    name: str                                  # audit/entity label, e.g. "employee"
    create_model: type[BaseModel]
    patch_model: type[BaseModel]
    sort_map: dict[str, str]
    default_sort: str
    search_cols: list[str] = field(default_factory=list)
    filters: dict[str, str] = field(default_factory=dict)     # query param -> column (equality)
    read_roles: tuple[str, ...] = ("org_admin", "dispatcher")
    write_roles: tuple[str, ...] = ("org_admin",)
    soft_delete: bool = True                    # DELETE => is_active=false
    hidden_cols: tuple[str, ...] = ()
    after_write: Callable[..., Any] | None = None


def _row(r, hidden: tuple[str, ...]) -> dict:
    d = dict(r._mapping)
    for h in hidden:
        d.pop(h, None)
    return d


def build_router(e: Entity, prefix: str, tag: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])
    cols = list(e.create_model.model_fields)

    @router.get("")
    async def list_items(request: Request, p: Principal = Depends(require_roles(*e.read_roles)), conn: AsyncConnection = Depends(db_for),
                         q: str | None = None, is_active: bool | None = None, sort: str | None = None,
                         page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
        where, params = ["organization_id = :o"], {"o": p.org_id}
        if q and e.search_cols:
            where.append("(" + " OR ".join(f"{c} ILIKE :q" for c in e.search_cols) + ")")
            params["q"] = f"%{q.replace('%', '').replace('_', '')[:80]}%"
        if is_active is not None and "is_active" in cols:
            where.append("is_active = :act"); params["act"] = is_active
        for qp, col in e.filters.items():
            v = request.query_params.get(qp)
            if v is not None:
                where.append(f"{col} = :f_{qp}"); params[f"f_{qp}"] = v
        order = sort_clause(sort, e.sort_map, e.default_sort)
        size, off = page_params(page, page_size)
        w = " AND ".join(where)
        total = await conn.scalar(text(f"SELECT count(*) FROM {e.table} WHERE {w}"), params)
        rows = (await conn.execute(text(f"SELECT * FROM {e.table} WHERE {w} ORDER BY {order} LIMIT :lim OFFSET :off"), {**params, "lim": size, "off": off})).all()
        return page_response([_row(r, e.hidden_cols) for r in rows], total, page, size)

    @router.get("/{item_id}")
    async def get_item(item_id: uuid.UUID, p: Principal = Depends(require_roles(*e.read_roles)), conn: AsyncConnection = Depends(db_for)):
        r = (await conn.execute(text(f"SELECT * FROM {e.table} WHERE organization_id=:o AND id=:i"), {"o": p.org_id, "i": item_id})).first()
        if r is None:
            raise AppError(404, "not_found", f"{e.name} not found")
        return _row(r, e.hidden_cols)

    @router.post("", status_code=201)
    async def create_item(payload: e.create_model, request: Request, p: Principal = Depends(require_roles(*e.write_roles)),   # type: ignore[name-defined]
                          conn: AsyncConnection = Depends(db_for)):
        data = payload.model_dump()
        names = list(data)
        r = (await conn.execute(text(f"INSERT INTO {e.table} (organization_id, {', '.join(names)}) VALUES (:o, {', '.join(':' + n for n in names)}) RETURNING *"),
                                {"o": p.org_id, **data})).first()
        if e.after_write:
            await e.after_write(conn, p, r, "create")
        await audit(conn, org_id=p.org_id, actor=p.user_id, action=f"{e.name}.created", entity_type=e.name, entity_id=r.id, ip=client_ip(request))
        return _row(r, e.hidden_cols)

    @router.patch("/{item_id}")
    async def update_item(item_id: uuid.UUID, payload: e.patch_model, request: Request, p: Principal = Depends(require_roles(*e.write_roles)),   # type: ignore[name-defined]
                          conn: AsyncConnection = Depends(db_for)):
        data = payload.model_dump(exclude_unset=True)
        data = {k: v for k, v in data.items() if k in cols}
        if not data:
            raise AppError(422, "empty_update", "No updatable fields supplied")
        sets = ", ".join(f"{k} = :{k}" for k in data)
        r = (await conn.execute(text(f"UPDATE {e.table} SET {sets} WHERE organization_id=:o AND id=:i RETURNING *"), {"o": p.org_id, "i": item_id, **data})).first()
        if r is None:
            raise AppError(404, "not_found", f"{e.name} not found")
        if e.after_write:
            await e.after_write(conn, p, r, "update")
        await audit(conn, org_id=p.org_id, actor=p.user_id, action=f"{e.name}.updated", entity_type=e.name, entity_id=item_id,
                    details={"fields": sorted(data)}, ip=client_ip(request))
        return _row(r, e.hidden_cols)

    @router.delete("/{item_id}", status_code=204)
    async def delete_item(item_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles(*e.write_roles)), conn: AsyncConnection = Depends(db_for)):
        if e.soft_delete and "is_active" in cols:
            n = (await conn.execute(text(f"UPDATE {e.table} SET is_active=false WHERE organization_id=:o AND id=:i"), {"o": p.org_id, "i": item_id})).rowcount
        else:
            n = (await conn.execute(text(f"DELETE FROM {e.table} WHERE organization_id=:o AND id=:i"), {"o": p.org_id, "i": item_id})).rowcount
        if not n:
            raise AppError(404, "not_found", f"{e.name} not found")
        await audit(conn, org_id=p.org_id, actor=p.user_id, action=f"{e.name}.deleted", entity_type=e.name, entity_id=item_id,
                    details={"soft": e.soft_delete}, ip=client_ip(request))

    return router
