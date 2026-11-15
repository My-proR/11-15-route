import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { dueToReport, shouldShare, toPositionBody } from "../tracking";

describe("shouldShare", () => {
  it("never shares unless driver + server-enabled + trip in progress", () => {
    expect(shouldShare("driver", true, "a1")).toBe("a1");
    expect(shouldShare("driver", false, "a1")).toBeUndefined();
    expect(shouldShare("driver", true, undefined)).toBeUndefined();
    for (const r of ["employee", "dispatcher", "org_admin", null, undefined]) expect(shouldShare(r, true, "a1")).toBeUndefined();
  });
});
describe("dueToReport", () => {
  it("respects the interval", () => {
    expect(dueToReport(10_000, 0, 30)).toBe(false);
    expect(dueToReport(29_600, 0, 30)).toBe(true);
    expect(dueToReport(30_000, 0, 30)).toBe(true);
  });
});
describe("toPositionBody", () => {
  it("converts units and drops invalid speed/heading", () => {
    const b = toPositionBody("a", { timestamp: 0, coords: { latitude: 1, longitude: 2, accuracy: 5, speed: 10, heading: -1 } });
    expect(b).toMatchObject({ allocation_id: "a", speed_kmh: 36, heading: null, accuracy_m: 5, captured_at: "1970-01-01T00:00:00.000Z" });
    expect(toPositionBody("a", { timestamp: 0, coords: { latitude: 1, longitude: 2, accuracy: null, speed: -1, heading: 90 } })).toMatchObject({ speed_kmh: null, heading: 90, accuracy_m: null });
  });
});
describe("no continuous / background GPS", () => {
  const root = join(__dirname, "..", "..");
  const walk = (d: string): string[] => readdirSync(d).flatMap((f) => { const p = join(d, f); return statSync(p).isDirectory() ? (f === "node_modules" || f === "__tests__" ? [] : walk(p)) : /\.(ts|tsx)$/.test(p) ? [p] : []; });
  it("app config disables background location", async () => {
    const cfg = (await import("../../app.config")).default as unknown as { android: { permissions: string[] }; plugins: unknown[] };
    expect(cfg.android.permissions).not.toContain("ACCESS_BACKGROUND_LOCATION");
    const loc = (cfg.plugins.find((p: unknown) => Array.isArray(p) && p[0] === "expo-location") as [string, Record<string, unknown>])[1];
    expect(loc.isAndroidBackgroundLocationEnabled).toBe(false);
    expect(loc.isIosBackgroundLocationEnabled).toBe(false);
  });
  it("source never uses background/task location APIs", () => {
    const src = [...walk(join(root, "src")), ...walk(join(root, "app"))].map((f) => readFileSync(f, "utf8")).join("\n");
    expect(src).not.toMatch(/startLocationUpdatesAsync|requestBackgroundPermissionsAsync|TaskManager|startGeofencingAsync/);
  });
  it("only watchPositionAsync usage is inside the in-progress-trip hook", () => {
    const home = readFileSync(join(root, "app", "(tabs)", "index.tsx"), "utf8");
    expect((home.match(/watchPositionAsync/g) ?? []).length).toBe(1);
  });
  it("location permission is requested only from the SOS screen and the in-progress-trip hook", () => {
    const callers = walk(join(root, "app")).filter((f) => /requestForegroundPermissionsAsync|getCurrentPositionAsync/.test(readFileSync(f, "utf8"))).map((f) => f.replaceAll("\\", "/"));
    expect(callers.sort()).toEqual([expect.stringContaining("(tabs)/index.tsx"), expect.stringContaining("(tabs)/sos.tsx")]);
    expect(readFileSync(join(root, "app", "(tabs)", "index.tsx"), "utf8")).toMatch(/useTrackingShare/);
  });
});
