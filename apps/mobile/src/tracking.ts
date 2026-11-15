/** Pure helpers for foreground-only vehicle position sharing (unit-testable without a device). */
export interface Coords { latitude: number; longitude: number; accuracy: number | null; speed: number | null; heading: number | null }
export interface Fix { coords: Coords; timestamp: number }

/** Location is requested/collected only for a driver, with tracking effective on the server, and a trip in progress. */
export function shouldShare(role: string | null | undefined, trackingEnabled: boolean, inProgressAllocationId: string | undefined): string | undefined {
  return role === "driver" && trackingEnabled ? inProgressAllocationId : undefined;
}

/** True when enough time has passed since the last report (small tolerance for timer jitter). */
export function dueToReport(now: number, last: number, intervalSeconds: number): boolean {
  return now - last >= intervalSeconds * 1000 - 500;
}

export function toPositionBody(allocationId: string, pos: Fix) {
  const c = pos.coords;
  return {
    allocation_id: allocationId, latitude: c.latitude, longitude: c.longitude, accuracy_m: c.accuracy ?? null,
    speed_kmh: c.speed != null && c.speed >= 0 ? c.speed * 3.6 : null,
    heading: c.heading != null && c.heading >= 0 ? c.heading : null,
    captured_at: new Date(pos.timestamp).toISOString(),
  };
}
