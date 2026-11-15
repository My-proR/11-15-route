import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Printer } from "lucide-react";
import { api } from "../lib/api";
import { Badge, Button, Card, DataTable, Empty, ErrorBox, Notice, PageHeader, Spinner } from "../components/ui";
import { fmtDate, inr } from "../lib/format";

export interface Seller { name: string | null; gstin: string | null; payment_instructions: string | null }
export interface InvoiceRow { id: string; invoice_number: string; period_start: string; period_end: string; cab_days: number; billable_cab_days: number; rate_paise: number; subtotal_paise: number; gst_percent: string; gst_paise: number; total_paise: number; status: "issued" | "paid" | "void"; issued_at: string; due_date: string; paid_at: string | null; payment_ref: string | null }
interface MyBilling {
  enabled: boolean; suspended: boolean; suspended_reason: "billing" | "manual" | null; seller: Seller; outstanding_paise: number; invoices: InvoiceRow[];
  rate_paise?: number; gst_percent?: string; free_until?: string | null; due_days?: number; grace_days?: number;
  month: null | { start: string; end: string; cab_days: number; billable_cab_days: number; subtotal_paise: number; gst_paise: number; total_paise: number; days: { date: string; cabs: number }[] };
}
export const statusTone = (s: string) => (s === "paid" ? "ok" : s === "issued" ? "warn" : "neutral") as "ok" | "warn" | "neutral";

/** Organization admin: this month's usage, invoices and how to pay. Stays reachable while access is paused. */
export default function Billing() {
  const q = useQuery({ queryKey: ["billing"], queryFn: () => api<MyBilling>("/billing") });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => void q.refetch()} />;
  const b = q.data;
  return (
    <>
      <PageHeader title="Billing" subtitle="You pay per cab-day: one vehicle with at least one confirmed trip on a day counts once." />
      {b.suspended && b.suspended_reason === "billing" && <div className="mb-4"><Notice tone="bad">Access is paused because an invoice is overdue. Pay the outstanding amount below and send the payment reference to your RoutePilot contact; access resumes as soon as it is recorded.</Notice></div>}
      {b.suspended && b.suspended_reason === "manual" && <div className="mb-4"><Notice tone="bad">Access to this organization has been paused by the platform operator. Please contact them.</Notice></div>}
      {!b.enabled ? <Notice>Billing is not active for your organization yet.</Notice> : (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card title="This month so far">
            {b.month && <dl className="grid grid-cols-2 gap-3 text-sm">
              <div><dt className="text-muted">Cab-days</dt><dd className="text-2xl font-semibold">{b.month.cab_days}</dd></div>
              <div><dt className="text-muted">Billable (after free period)</dt><dd className="text-2xl font-semibold">{b.month.billable_cab_days}</dd></div>
              <div><dt className="text-muted">Rate</dt><dd>{inr(b.rate_paise)} per cab-day + {b.gst_percent}% GST</dd></div>
              <div><dt className="text-muted">Estimated invoice</dt><dd className="font-semibold">{inr(b.month.total_paise)}</dd></div>
            </dl>}
            {b.free_until && <p className="mt-3 text-xs text-muted">Free pilot: cab-days up to {fmtDate(b.free_until)} are not charged. Invoices are issued after month end and are due {b.due_days} days later.</p>}
          </Card>
          <Card title="How to pay">
            <p className="whitespace-pre-line text-sm">{b.seller.payment_instructions ?? "Ask your RoutePilot contact for payment details."}</p>
            <p className="mt-3 text-sm">Outstanding: <strong>{inr(b.outstanding_paise)}</strong></p>
          </Card>
        </div>
      )}
      <div className="mt-5"><Card title="Invoices">
        {b.invoices.length === 0 ? <Empty title="No invoices yet" /> : (
          <DataTable caption="Invoices" rows={b.invoices} columns={[
            { key: "n", header: "Invoice", render: (r) => <Link className="text-accent underline" to={`/billing/invoices/${r.id}`}>{r.invoice_number}</Link> },
            { key: "p", header: "Period", render: (r) => `${fmtDate(r.period_start)} – ${fmtDate(r.period_end)}` },
            { key: "d", header: "Cab-days", render: (r) => r.billable_cab_days },
            { key: "t", header: "Total", render: (r) => inr(r.total_paise) },
            { key: "due", header: "Due", render: (r) => fmtDate(r.due_date) },
            { key: "s", header: "Status", render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> },
          ]} />)}
      </Card></div>
    </>
  );
}

interface InvoiceFull extends InvoiceRow { organization_name: string | null; seller: Seller; detail: { date: string; cabs: number }[] }

/** Printable invoice (use the browser's Print → Save as PDF). */
export function InvoiceView() {
  const { id = "" } = useParams();
  const q = useQuery({ queryKey: ["invoice", id], queryFn: () => api<InvoiceFull>(`/billing/invoices/${id}`) });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => void q.refetch()} />;
  const i = q.data;
  return (
    <div className="mx-auto max-w-2xl space-y-4 rounded-2xl border border-line bg-surface p-6 print:border-0">
      <div className="flex items-start justify-between gap-4">
        <div><h1 className="text-xl font-semibold">Tax invoice</h1><p className="text-sm text-muted">{i.invoice_number}</p></div>
        <div className="flex items-center gap-2 print:hidden"><Badge tone={statusTone(i.status)}>{i.status}</Badge><Button variant="secondary" onClick={() => window.print()}><Printer className="size-4" />Print / PDF</Button></div>
      </div>
      <div className="grid gap-4 text-sm sm:grid-cols-2">
        <div><p className="text-muted">From</p><p className="font-medium">{i.seller.name ?? "RoutePilot"}</p>{i.seller.gstin && <p>GSTIN {i.seller.gstin}</p>}</div>
        <div><p className="text-muted">Billed to</p><p className="font-medium">{i.organization_name}</p></div>
        <div><p className="text-muted">Period</p><p>{fmtDate(i.period_start)} – {fmtDate(i.period_end)}</p></div>
        <div><p className="text-muted">Issued / due</p><p>{fmtDate(i.issued_at)} / {fmtDate(i.due_date)}</p></div>
      </div>
      <table className="w-full text-sm"><tbody>
        <tr><td className="py-1">Cab-days used</td><td className="text-right">{i.cab_days}</td></tr>
        <tr><td className="py-1">Billable cab-days × {inr(i.rate_paise)}</td><td className="text-right">{i.billable_cab_days} → {inr(i.subtotal_paise)}</td></tr>
        <tr><td className="py-1">GST {i.gst_percent}%</td><td className="text-right">{inr(i.gst_paise)}</td></tr>
        <tr className="border-t border-line font-semibold"><td className="py-2">Total</td><td className="text-right">{inr(i.total_paise)}</td></tr>
      </tbody></table>
      {i.paid_at && <p className="text-sm text-ok">Paid on {fmtDate(i.paid_at)} (ref {i.payment_ref}).</p>}
      {i.status === "issued" && i.seller.payment_instructions && <p className="whitespace-pre-line rounded-lg bg-surface2 p-3 text-sm">{i.seller.payment_instructions}</p>}
      <details className="text-sm"><summary className="cursor-pointer text-muted">Day-by-day cab count</summary>
        <ul className="mt-2 columns-2 text-xs">{i.detail.map((d) => <li key={d.date}>{fmtDate(d.date)}: {d.cabs}</li>)}</ul></details>
      <Link className="text-sm text-accent underline print:hidden" to="/billing">Back to billing</Link>
    </div>
  );
}
