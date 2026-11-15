import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, buildQuery } from "../lib/api";
import { useSession } from "../lib/session";

describe("api client", () => {
  beforeEach(() => useSession.setState({ token: "tok", activeOrgId: "org-1", ready: true }));
  afterEach(() => vi.restoreAllMocks());

  it("builds queries without empty values", () => {
    expect(buildQuery({ a: 1, b: "", c: undefined, d: null, e: false })).toBe("?a=1&e=false");
    expect(buildQuery({})).toBe("");
  });

  it("sends bearer token, organization header and idempotency key", async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", f);
    await api("/x", { method: "POST", body: { a: 1 }, idempotencyKey: "k-1" });
    const [, init] = f.mock.calls[0]!;
    expect(init.headers).toMatchObject({ Authorization: "Bearer tok", "X-Organization-Id": "org-1", "Idempotency-Key": "k-1", "Content-Type": "application/json" });
  });

  it("maps structured server errors to ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "driver_declined", message: "This driver declined" } }), { status: 409 })));
    await expect(api("/x")).rejects.toMatchObject({ status: 409, code: "driver_declined", message: "This driver declined" });
  });

  it("reports network failures with a friendly message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fail")));
    const e = await api("/x").catch((x: unknown) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect((e as ApiError).code).toBe("network");
  });

  it("signs out on 401", async () => {
    const out = vi.fn().mockResolvedValue(undefined);
    useSession.setState({ signOut: out });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));
    await api("/x").catch(() => undefined);
    expect(out).toHaveBeenCalled();
  });
});
