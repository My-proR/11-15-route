import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SosPage } from "../pages/Portal";
import { useSession } from "../lib/session";

const me = { user_id: "u", email: "e@x.co", full_name: "E", is_platform_admin: false, active_organization_id: "o", role: "employee", organizations: [] };
const off = { deployment_enabled: false, platform_enabled: false, organization_enabled: false, effective: false, blocked_by: "deployment" };
const on = { ...off, deployment_enabled: true, platform_enabled: true, organization_enabled: true, effective: true, blocked_by: null };

function setup(gpsEffective: boolean, routes: Record<string, (init: RequestInit) => unknown> = {}) {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    const path = url.replace(/^.*\/api\/v1/, "").split("?")[0]!;
    const key = `${init.method ?? "GET"} ${path}`;
    const body = routes[key]?.(init) ?? (path === "/me" ? me : path === "/features" ? { sos_gps: gpsEffective ? on : off, live_tracking: off } : {});
    return new Response(JSON.stringify(body), { status: 200 });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><SosPage /></MemoryRouter></QueryClientProvider>);
  return calls;
}

describe("SOS page", () => {
  const getCurrentPosition = vi.fn();
  beforeEach(() => {
    useSession.setState({ token: "t", activeOrgId: "o", ready: true });
    getCurrentPosition.mockReset();
    vi.stubGlobal("navigator", { ...navigator, geolocation: { getCurrentPosition, watchPosition: vi.fn(), clearWatch: vi.fn() } });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("sends the alert without ever touching GPS when the feature is off", async () => {
    const calls = setup(false, { "POST /sos": () => ({ id: "inc-1" }) });
    await waitFor(() => expect(calls.some((c) => c.url.includes("/features"))).toBe(true));
    await userEvent.click(await screen.findByRole("button", { name: /^send sos$/i }));
    expect(await screen.findByText(/your alert was sent/i)).toBeInTheDocument();
    expect(getCurrentPosition).not.toHaveBeenCalled();
    expect(calls.filter((c) => c.url.includes("/location"))).toHaveLength(0);
    expect(screen.getByText(/location was not collected|not collected|sent\. help has been notified/i)).toBeInTheDocument();
  });

  it("explains first, and still sends the alert when the user declines location", async () => {
    const calls = setup(true, { "POST /sos": () => ({ id: "inc-2" }), "PUT /sos/inc-2/location": () => ({ location_status: "denied" }) });
    await waitFor(() => expect(calls.some((c) => c.url.includes("/features"))).toBe(true));
    await userEvent.click(await screen.findByRole("button", { name: /^send sos$/i }));
    expect(await screen.findByText(/share your location/i)).toBeInTheDocument();
    expect(getCurrentPosition).not.toHaveBeenCalled();           // no browser prompt before the explanation is accepted
    await userEvent.click(screen.getByRole("button", { name: /send without location/i }));
    expect(await screen.findByText(/your alert was sent/i)).toBeInTheDocument();
    expect(getCurrentPosition).not.toHaveBeenCalled();
  });

  it("sends the alert first, then attaches the location when allowed; a denied permission does not undo the alert", async () => {
    getCurrentPosition.mockImplementation((_ok: unknown, fail: (e: { code: number; PERMISSION_DENIED: number }) => void) => fail({ code: 1, PERMISSION_DENIED: 1 }));
    const calls = setup(true, { "POST /sos": () => ({ id: "inc-3" }), "PUT /sos/inc-3/location": () => ({ location_status: "denied" }) });
    await waitFor(() => expect(calls.some((c) => c.url.includes("/features"))).toBe(true));
    await userEvent.click(await screen.findByRole("button", { name: /^send sos$/i }));
    await userEvent.click(await screen.findByRole("button", { name: /send with my location/i }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/sos/inc-3/location"))).toBe(true));
    const order = calls.map((c) => `${c.init.method ?? "GET"} ${c.url.replace(/^.*\/api\/v1/, "")}`);
    expect(order.indexOf("POST /sos")).toBeLessThan(order.indexOf("PUT /sos/inc-3/location"));
    const put = calls.find((c) => c.url.endsWith("/location"))!;
    expect(JSON.parse(String(put.init.body))).toEqual({ status: "denied" });
    expect(await screen.findByText(/could not be shared/i)).toBeInTheDocument();
  });

  it("uses one Idempotency-Key per visit so a retry cannot duplicate the alert", async () => {
    let n = 0;
    const calls = setup(false, { "POST /sos": () => { n += 1; if (n === 1) throw new Error("boom"); return { id: "inc-4" }; } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("/features"))).toBe(true));
    const btn = await screen.findByRole("button", { name: /^send sos$/i });
    await userEvent.click(btn);
    await userEvent.click(await screen.findByRole("button", { name: /try again/i }));
    await screen.findByText(/your alert was sent/i);
    const keys = calls.filter((c) => c.url.endsWith("/sos") && c.init.method === "POST").map((c) => (c.init.headers as Record<string, string>)["Idempotency-Key"]);
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(1);
  });
});
