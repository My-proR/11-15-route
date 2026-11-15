"""Routing provider abstraction. Road-network distances only; straight-line distance is never returned as a road estimate.

Providers: OSRM (self-hosted or hosted, OSRM HTTP API) and `none`. Add another by implementing `RoutingProvider`.
Results are cached in-process (TTL) and calls are time-limited. Failure => None, never a guessed value.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Protocol, Sequence

import httpx

from ..config import get_settings

Point = tuple[float, float]  # (lat, lon)


@dataclass(frozen=True)
class Matrix:
    distances_m: list[list[float]]
    durations_s: list[list[float]]


@dataclass(frozen=True)
class RouteGeometry:
    distance_m: float
    duration_s: float
    geojson: dict


class RoutingProvider(Protocol):
    name: str
    async def matrix(self, points: Sequence[Point]) -> Matrix | None: ...
    async def route(self, points: Sequence[Point]) -> RouteGeometry | None: ...


class NullProvider:
    name = "none"
    async def matrix(self, points): return None
    async def route(self, points): return None


class OSRMProvider:
    name = "osrm"
    MAX_POINTS = 100   # OSRM default --max-table-size

    def __init__(self, base_url: str, timeout: float, cache_ttl: int):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.ttl = cache_ttl
        self._cache: dict[str, tuple[float, object]] = {}
        self._client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "RoutePilot/1.0"})

    def _get(self, key: str):
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        return None

    def _put(self, key: str, value: object) -> None:
        if len(self._cache) > 2000:
            self._cache.clear()
        self._cache[key] = (time.monotonic() + self.ttl, value)

    @staticmethod
    def _coords(points: Sequence[Point]) -> str:
        return ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)

    async def matrix(self, points):
        n = len(points)
        if n < 2 or n > self.MAX_POINTS or not self.base:
            return None
        coords = self._coords(points)
        key = "m:" + hashlib.sha1(coords.encode(), usedforsecurity=False).hexdigest()
        if (cached := self._get(key)) is not None:
            return cached  # type: ignore[return-value]
        try:
            r = await self._client.get(f"{self.base}/table/v1/driving/{coords}", params={"annotations": "distance,duration"})
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
            return None
        d, t = data.get("distances"), data.get("durations")
        if data.get("code") != "Ok" or not _square(d, n) or not _square(t, n):
            return None
        if any(v is None or v < 0 for row in d for v in row) or any(v is None or v < 0 for row in t for v in row):
            return None   # unreachable pair: treat the whole matrix as unusable rather than guess
        m = Matrix(d, t)
        self._put(key, m)
        return m

    async def route(self, points):
        if len(points) < 2 or not self.base:
            return None
        coords = self._coords(points)
        key = "r:" + hashlib.sha1(coords.encode(), usedforsecurity=False).hexdigest()
        if (cached := self._get(key)) is not None:
            return cached  # type: ignore[return-value]
        try:
            r = await self._client.get(f"{self.base}/route/v1/driving/{coords}", params={"overview": "full", "geometries": "geojson"})
            r.raise_for_status()
            data = r.json()
            route = data["routes"][0]
            result = RouteGeometry(float(route["distance"]), float(route["duration"]), route["geometry"])
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, asyncio.TimeoutError):
            return None
        self._put(key, result)
        return result


def _square(m, n: int) -> bool:
    return isinstance(m, list) and len(m) == n and all(isinstance(row, list) and len(row) == n for row in m)


_provider: RoutingProvider | None = None


def get_routing_provider() -> RoutingProvider:
    global _provider
    if _provider is None:
        s = get_settings()
        _provider = OSRMProvider(s.osrm_base_url, s.routing_timeout_seconds, s.routing_cache_ttl_seconds) \
            if s.routing_provider == "osrm" and s.osrm_base_url else NullProvider()
    return _provider


def set_routing_provider(provider: RoutingProvider | None) -> None:
    """Test / deployment hook to inject a provider."""
    global _provider
    _provider = provider


def haversine_km(a: Point, b: Point) -> float:
    """Straight-line distance, used ONLY to pre-cluster candidates. Never reported as a road distance."""
    from math import asin, cos, radians, sin, sqrt
    (la1, lo1), (la2, lo2) = a, b
    dla, dlo = radians(la2 - la1), radians(lo2 - lo1)
    h = sin(dla / 2) ** 2 + cos(radians(la1)) * cos(radians(la2)) * sin(dlo / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(h))
