import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { translate, useLang } from "../lib/i18n";
import LanguageSwitch from "../components/LanguageSwitch";
import InstallPrompt from "../components/InstallPrompt";
import Join from "../pages/Join";
import { useSession } from "../lib/session";
import { whatsappLink } from "../pages/Invites";

vi.mock("../lib/supabase", () => ({ supabaseConfigured: true, supabase: () => ({ auth: { signInWithOAuth: vi.fn().mockResolvedValue({ error: null }), getSession: vi.fn(), onAuthStateChange: vi.fn(), signOut: vi.fn() } }) }));

const wrap = (ui: React.ReactNode, path = "/") => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={[path]}><Routes><Route path="/join/:token" element={ui} /><Route path="*" element={ui} /></Routes></MemoryRouter></QueryClientProvider>
);

describe("i18n", () => {
  it("has a Tamil string for every English key, and fills variables", () => {
    const en = translate("en", "joinBody", { org: "Acme", role: "driver" });
    const ta = translate("ta", "joinBody", { org: "Acme", role: "ஓட்டுநர்" });
    expect(en).toBe("Acme invited you to join as driver.");
    expect(ta).toContain("Acme");
    expect(ta).not.toContain("{");
    expect(translate("ta", "signOut")).not.toBe(translate("en", "signOut"));
  });
  it("language switch changes the language and remembers it", async () => {
    localStorage.clear(); useLang.setState({ lang: "en" }); useSession.setState({ token: null });
    render(<LanguageSwitch />);
    await userEvent.click(screen.getByRole("button", { name: "தமிழ்" }));
    expect(useLang.getState().lang).toBe("ta");
    expect(localStorage.getItem("rp_lang")).toBe("ta");
    expect(document.documentElement.lang).toBe("ta");
    useLang.getState().setLang("en");
  });
  it("adopts the server language only when the device has no explicit choice", () => {
    localStorage.clear(); useLang.setState({ lang: "en" });
    useLang.getState().adopt("ta");
    expect(useLang.getState().lang).toBe("ta");
    useLang.getState().setLang("en");           // explicit choice
    useLang.getState().adopt("ta");
    expect(useLang.getState().lang).toBe("en");
  });
});

describe("WhatsApp invite link", () => {
  it("adds +91 to 10-digit numbers and url-encodes the message", () => {
    const u = whatsappLink("Ravi Kumar", "driver", "https://x.test/join/abc", "98765 43210");
    expect(u.startsWith("https://wa.me/919876543210?text=")).toBe(true);
    expect(decodeURIComponent(u)).toContain("Hi Ravi");
    expect(decodeURIComponent(u)).toContain("https://x.test/join/abc");
  });
  it("works without a phone number", () => { expect(whatsappLink(null, "employee", "https://x.test/j", null).startsWith("https://wa.me/?text=")).toBe(true); });
});

describe("Join page", () => {
  beforeEach(() => { localStorage.clear(); useLang.setState({ lang: "en" }); });
  afterEach(() => { vi.unstubAllGlobals(); useSession.setState({ token: null, ready: false }); });

  it("signed out: shows who invited you and a Google button", async () => {
    useSession.setState({ token: null, ready: true });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ organization: "Acme Corp", role: "driver", first_name: "Ravi" }), { status: 200 })));
    render(wrap(<Join />, "/join/tok123456789012345678"));
    expect(await screen.findByText(/Acme Corp invited you to join as driver/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Continue with Google/ })).toBeEnabled();
  });
  it("invalid link shows a clear message", async () => {
    useSession.setState({ token: null, ready: true });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "invite_invalid", message: "x" } }), { status: 404 })));
    render(wrap(<Join />, "/join/tok123456789012345678"));
    expect(await screen.findByText(/not valid/)).toBeInTheDocument();
  });
  it("signed in: accepts the invite automatically, once", async () => {
    useSession.setState({ token: "jwt", ready: true });
    const f = vi.fn().mockImplementation((url: string) => Promise.resolve(url.includes("/invites/preview") ? new Response(JSON.stringify({ organization: "Acme", role: "employee", first_name: null }), { status: 200 }) : new Response(JSON.stringify({ role: "employee" }), { status: 200 })));
    vi.stubGlobal("fetch", f);
    render(wrap(<Join />, "/join/tok123456789012345678"));
    await waitFor(() => expect(screen.getByText(/All set/)).toBeInTheDocument());
    expect(f.mock.calls.filter((c) => String(c[0]).includes("/invites/accept"))).toHaveLength(1);
  });
  it("shows the server's message when the invite is for another account", async () => {
    useSession.setState({ token: "jwt", ready: true });
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => Promise.resolve(url.includes("/invites/preview") ? new Response(JSON.stringify({ organization: "Acme", role: "driver", first_name: "R" }), { status: 200 })
      : new Response(JSON.stringify({ error: { code: "invite_email_mismatch", message: "Sign in with ravi@x.test." } }), { status: 403 }))));
    render(wrap(<Join />, "/join/tok123456789012345678"));
    expect(await screen.findByText("Sign in with ravi@x.test.")).toBeInTheDocument();
  });
});

describe("InstallPrompt", () => {
  it("stays hidden until the browser offers installation, then installs on tap", async () => {
    localStorage.clear();
    render(<InstallPrompt />);
    expect(screen.queryByRole("dialog")).toBeNull();
    const prompt = vi.fn().mockResolvedValue(undefined);
    const evt = Object.assign(new Event("beforeinstallprompt", { cancelable: true }), { prompt, userChoice: Promise.resolve({ outcome: "accepted" as const }) });
    act(() => { window.dispatchEvent(evt); });
    await userEvent.click(await screen.findByRole("button", { name: "Install" }));
    expect(prompt).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
  it("remembers dismissal", async () => {
    localStorage.clear();
    const { unmount } = render(<InstallPrompt />);
    act(() => { window.dispatchEvent(Object.assign(new Event("beforeinstallprompt", { cancelable: true }), { prompt: vi.fn(), userChoice: Promise.resolve({ outcome: "dismissed" as const }) })); });
    await userEvent.click(await screen.findByRole("button", { name: "Not now" }));
    unmount();
    render(<InstallPrompt />);
    act(() => { window.dispatchEvent(Object.assign(new Event("beforeinstallprompt", { cancelable: true }), { prompt: vi.fn(), userChoice: Promise.resolve({ outcome: "dismissed" as const }) })); });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
