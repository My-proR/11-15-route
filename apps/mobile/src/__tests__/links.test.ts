import { describe, expect, it } from "vitest";
import { parseAuthCallback, parseInviteToken } from "../links";

const T = "abcDEF123456_-ghIJKL7890mnop";
describe("parseInviteToken", () => {
  it("reads https, custom-scheme and bare tokens", () => {
    expect(parseInviteToken(`https://app.example.com/join/${T}`)).toBe(T);
    expect(parseInviteToken(`routepilot://join/${T}?x=1`)).toBe(T);
    expect(parseInviteToken(`  ${T}  `)).toBe(T);
  });
  it("rejects junk", () => {
    expect(parseInviteToken("")).toBeNull();
    expect(parseInviteToken("hello world")).toBeNull();
    expect(parseInviteToken("https://x.com/join/short")).toBeNull();
    expect(parseInviteToken("https://x.com/other/" + T)).toBeNull();
  });
});
describe("parseAuthCallback", () => {
  it("returns the code", () => expect(parseAuthCallback("routepilot://auth/callback?code=abc123")).toEqual({ code: "abc123" }));
  it("returns provider errors", () => expect(parseAuthCallback("routepilot://auth/callback?error=access_denied&error_description=No")).toEqual({ error: "No" }));
  it("ignores fragment tokens and empty urls", () => {
    expect(parseAuthCallback("routepilot://auth/callback#access_token=evil")).toBeNull();
    expect(parseAuthCallback("routepilot://auth/callback")).toBeNull();
  });
});
