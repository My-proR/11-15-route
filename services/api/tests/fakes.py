"""TEST-ONLY routing stub. Deterministic grid metric (Manhattan x 1.3 detour factor, 30 km/h). Never used outside tests."""
from app.services.routing import Matrix, RouteGeometry

class GridProvider:
    name = "test-grid"
    def __init__(self, fail: bool = False): self.fail = fail; self.calls = 0
    @staticmethod
    def _d(a, b): return (abs(a[0]-b[0]) + abs(a[1]-b[1])) * 111_000 * 1.3
    async def matrix(self, points):
        self.calls += 1
        if self.fail: return None
        d = [[self._d(a, b) for b in points] for a in points]
        return Matrix(d, [[x / (30_000 / 3600) for x in row] for row in d])
    async def route(self, points):
        if self.fail: return None
        dist = sum(self._d(points[i], points[i+1]) for i in range(len(points)-1))
        return RouteGeometry(dist, dist / (30_000/3600), {"type": "LineString", "coordinates": [[p[1], p[0]] for p in points]})
