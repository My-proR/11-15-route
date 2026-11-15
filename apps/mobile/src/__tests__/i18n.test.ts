import { describe, expect, it, vi } from "vitest";
vi.mock("expo-secure-store", () => ({ getItemAsync: async () => null, setItemAsync: async () => undefined }));
import { translate } from "../i18n";

describe("mobile i18n", () => {
  it("interpolates and falls back to Tamil strings", () => {
    expect(translate("en", "joinBody", { org: "Acme", role: "driver" })).toBe("Acme invited you to join as driver.");
    expect(translate("ta", "tabTrips")).toBe("பயணங்கள்");
    expect(translate("ta", "joinBody", { org: "Acme", role: "x" })).toContain("Acme");
  });
});
