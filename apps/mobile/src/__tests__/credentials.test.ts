import { describe, expect, it } from "vitest";
import { digitsOnly, isBoardingCode } from "../credentials";

describe("credential input rules", () => {
  it("keeps digits only and caps length", () => {
    expect(digitsOnly("12a-3 45678", 4)).toBe("1234");
    expect(digitsOnly("९९9", 4)).toBe("9");
  });
  it("validates exact lengths", () => {
    expect(isBoardingCode("1234")).toBe(true);
    expect(isBoardingCode("12345")).toBe(false);
    expect(isBoardingCode("12a4")).toBe(false);
  });
});

import { isTripQr } from "../credentials";
describe("isTripQr", () => {
  it("accepts RoutePilot tokens only", () => {
    expect(isTripQr("rp1.eyJvIjoiYSJ9.c2ln-_")).toBe(true);
    for (const bad of ["", "http://evil.example", "rp2.a.b", "rp1.a", "rp1.a.b.c", "rp1.a b.c", "rp1." + "a".repeat(700) + ".b"]) expect(isTripQr(bad)).toBe(false);
  });
});
