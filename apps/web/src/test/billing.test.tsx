import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import Billing, { InvoiceView } from "../pages/Billing";
import PlatformBilling from "../pages/PlatformBilling";
import { inr } from "../lib/format";

const wrap = (ui: React.ReactNode, path: string, route = "*") => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={ui} /></Routes></MemoryRouter></QueryClientProvider>
);
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const invoice = { id: "i1", invoice_number: "RP/2026-27/000007", period_start: "2026-10-01", period_end: "2026-10-31", cab_days: 40, billable_cab_days: 30, rate_paise: 5000, subtotal_paise: 150000, gst_percent: "18.0", gst_paise: 27000, total_paise: 177000, status: "issued", issued_at: "2026-11-01T00:00:00Z", due_date: "2026-11-08", paid_at: null, payment_ref: null };

afterEach(() => vi.unstubAllGlobals());

describe("billing", () => {
  it("formats paise as Indian rupees", () => { expect(inr(177000)).toBe("₹1,770.00"); expect(inr(null)).toBe("—"); });

  it("org admin sees usage, rate, outstanding amount and the pay instructions", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ enabled: true, suspended: false, suspended_reason: null, seller: { name: "RC", gstin: null, payment_instructions: "UPI: rc@bank" }, outstanding_paise: 177000, invoices: [invoice],
      rate_paise: 5000, gst_percent: "18.0", free_until: null, due_days: 7, grace_days: 7, month: { start: "2026-11-01", end: "2026-11-30", cab_days: 12, billable_cab_days: 12, subtotal_paise: 60000, gst_paise: 10800, total_paise: 70800, days: [] } })));
    render(wrap(<Billing />, "/billing"));
    expect(await screen.findByText("This month so far")).toBeInTheDocument();
    expect(screen.getByText("UPI: rc@bank")).toBeInTheDocument();
    expect(screen.getByText("₹1,770.00", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "RP/2026-27/000007" })).toHaveAttribute("href", "/billing/invoices/i1");
    expect(screen.queryByText(/paused/)).toBeNull();
  });

  it("shows a clear 'access paused' message with the way out when an invoice is overdue", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ enabled: true, suspended: true, suspended_reason: "billing", seller: { name: null, gstin: null, payment_instructions: null }, outstanding_paise: 177000, invoices: [invoice], month: null })));
    render(wrap(<Billing />, "/billing"));
    expect(await screen.findByText(/Access is paused because an invoice is overdue/)).toBeInTheDocument();
    expect(screen.getByText(/Ask your RoutePilot contact/)).toBeInTheDocument();
  });

  it("invoice view lists the tax lines and totals", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ ...invoice, organization_name: "Acme Corp", seller: { name: "RC", gstin: "33ABCDE1234F1Z5", payment_instructions: null }, detail: [{ date: "2026-10-01", cabs: 2 }] })));
    render(wrap(<InvoiceView />, "/billing/invoices/i1", "/billing/invoices/:id"));
    expect(await screen.findByText("Tax invoice")).toBeInTheDocument();
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("GSTIN 33ABCDE1234F1Z5")).toBeInTheDocument();
    expect(screen.getByText("₹1,770.00")).toBeInTheDocument();
    expect(screen.getByText("₹270.00")).toBeInTheDocument();
  });

  it("platform: recording a payment needs a reference and calls the API", async () => {
    const f = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/mark-paid")) return Promise.resolve(json({ status: "paid", organization_reinstated: true }));
      if (url.includes("/platform/invoices")) return Promise.resolve(json([{ ...invoice, organization_id: "o1", organization_name: "Acme Corp" }]));
      if (url.includes("/platform/billing")) return Promise.resolve(json([]));
      return Promise.resolve(json({}, init ? 200 : 404));
    });
    vi.stubGlobal("fetch", f);
    render(wrap(<PlatformBilling />, "/platform/billing"));
    await userEvent.click(await screen.findByRole("button", { name: "Mark paid" }));
    const confirm = screen.getAllByRole("button", { name: "Mark paid" }).at(-1)!;
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText("UPI / bank reference"), "UPI998877");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() => expect(f.mock.calls.some((c) => String(c[0]).includes("/invoices/i1/mark-paid"))).toBe(true));
    const call = f.mock.calls.find((c) => String(c[0]).includes("/mark-paid"))!;
    expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({ payment_ref: "UPI998877" });
  });
});
