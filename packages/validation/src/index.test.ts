import { describe, expect, it } from "vitest";
import { leaveIn, positionIn, registration, sosCreate } from "./index";

describe("validation", () => {
  it("SOS: obtained needs coordinates; denied does not", () => {
    expect(sosCreate.safeParse({ location: { status: "obtained", latitude: 1 } }).success).toBe(false);
    expect(sosCreate.safeParse({ location: { status: "denied" } }).success).toBe(true);
    expect(sosCreate.safeParse({}).success).toBe(true);
    expect(sosCreate.safeParse({ location: { status: "obtained", latitude: 13, longitude: 80, captured_at: new Date().toISOString() } }).success).toBe(true);
    expect(sosCreate.safeParse({ location: { status: "obtained", latitude: 91, longitude: 80, captured_at: new Date().toISOString() } }).success).toBe(false);
  });
  it("position bounds", () => {
    const base = { allocation_id: "00000000-0000-4000-8000-000000000000", latitude: 1, longitude: 1, captured_at: new Date().toISOString() };
    expect(positionIn.safeParse(base).success).toBe(true);
    expect(positionIn.safeParse({ ...base, speed_kmh: 301 }).success).toBe(false);
    expect(positionIn.safeParse({ ...base, heading: 361 }).success).toBe(false);
  });
  it("leave dates ordered", () => {
    expect(leaveIn.safeParse({ starts_on: "2026-01-02", ends_on: "2026-01-01" }).success).toBe(false);
    expect(leaveIn.safeParse({ starts_on: "2026-01-01", ends_on: "2026-01-01" }).success).toBe(true);
  });
  it("registration normalises", () => {
    expect(registration.parse("tn 01 ab 1234")).toBe("TN01AB1234");
    expect(registration.safeParse("a!").success).toBe(false);
  });
});
