import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAction } from "../lib/hooks";
import { Badge, Button, Card, DataTable, Empty, ErrorBox, Field, Input, Modal, PageHeader, Spinner } from "../components/ui";
import { fmtDate, inr } from "../lib/format";
import { statusTone, type InvoiceRow } from "./Billing";

interface OrgBilling { id: string; name: string; status: string; suspended_reason: string | null; billing_enabled: boolean; rate_paise: number | null; gst_percent: string | null; free_until: string | null; due_days: number | null; grace_days: number | null; outstanding_paise: number; overdue_invoices: number }
type PInvoice = InvoiceRow & { organization_id: string; organization_name: string };
const thisMonth = () => new Date().toISOString().slice(0, 7);

/** Platform operator: set each organization's rate and free period, issue invoices, record payments. */
export default function PlatformBilling() {
  const orgs = useQuery({ queryKey: ["pb-orgs"], queryFn: () => api<OrgBilling[]>("/platform/billing") });
  const invoices = useQuery({ queryKey: ["pb-invoices"], queryFn: () => api<PInvoice[]>("/platform/invoices", { query: { limit: 100 } }) });
  const [edit, setEdit] = useState<OrgBilling | null>(null);
  const [form, setForm] = useState({ enabled: false, rupees: "50", gst: "18", free_until: "", due_days: "7", grace_days: "7", month: thisMonth() });
  const [pay, setPay] = useState<PInvoice | null>(null);
  const [ref, setRef] = useState("");
  const refresh = [["pb-orgs"], ["pb-invoices"]];
  const open = (o: OrgBilling) => {
    setEdit(o);
    setForm({ enabled: o.billing_enabled, rupees: String((o.rate_paise ?? 5000) / 100), gst: o.gst_percent ?? "18", free_until: o.free_until ?? "", due_days: String(o.due_days ?? 7), grace_days: String(o.grace_days ?? 7), month: thisMonth() });
  };
  const save = useAction(() => api(`/platform/billing/${edit!.id}`, { method: "PUT", body: { billing_enabled: form.enabled, rate_paise: Math.round(Number(form.rupees) * 100), gst_percent: form.gst, free_until: form.free_until || null, due_days: Number(form.due_days), grace_days: Number(form.grace_days) } }),
    { success: "Billing settings saved", invalidate: refresh, onSuccess: () => setEdit(null) });
  const generate = useAction(() => api(`/platform/billing/${edit!.id}/invoices`, { method: "POST", body: { month: form.month } }), { success: "Invoice ready", invalidate: refresh });
  const markPaid = useAction(() => api(`/platform/invoices/${pay!.id}/mark-paid`, { method: "POST", body: { payment_ref: ref } }), { success: "Payment recorded", invalidate: refresh, onSuccess: () => { setPay(null); setRef(""); } });
  const voidInv = useAction((id: string) => api(`/platform/invoices/${id}/void`, { method: "POST" }), { success: "Invoice voided", invalidate: refresh });
  return (
    <>
      <PageHeader title="Billing & invoices" subtitle="Per cab-day charge. Invoices are issued automatically after month end by the worker; payment is recorded here after UPI/bank transfer. Overdue organizations are paused after their grace period." />
      <Card title="Organizations">
        {orgs.isPending ? <Spinner /> : orgs.isError ? <ErrorBox error={orgs.error} /> : orgs.data.length === 0 ? <Empty title="No organizations" /> : (
          <DataTable caption="Organization billing" rows={orgs.data} columns={[
            { key: "n", header: "Organization", render: (o) => o.name },
            { key: "b", header: "Billing", render: (o) => <Badge tone={o.billing_enabled ? "ok" : "neutral"}>{o.billing_enabled ? `${inr(o.rate_paise)}/cab-day` : "off"}</Badge> },
            { key: "f", header: "Free until", render: (o) => fmtDate(o.free_until) },
            { key: "o", header: "Outstanding", render: (o) => inr(o.outstanding_paise) },
            { key: "s", header: "Access", render: (o) => <Badge tone={o.status === "active" ? "ok" : "bad"}>{o.status === "active" ? "active" : `paused (${o.suspended_reason ?? "?"})`}</Badge> },
            { key: "a", header: "", render: (o) => <Button variant="secondary" className="min-h-8 px-3" onClick={() => open(o)}>Manage</Button> },
          ]} />)}
      </Card>
      <div className="mt-5"><Card title="Recent invoices">
        {invoices.isPending ? <Spinner /> : invoices.isError ? <ErrorBox error={invoices.error} /> : invoices.data.length === 0 ? <Empty title="No invoices yet" /> : (
          <DataTable caption="Invoices" rows={invoices.data} columns={[
            { key: "n", header: "Invoice", render: (r) => r.invoice_number }, { key: "o", header: "Organization", render: (r) => r.organization_name },
            { key: "t", header: "Total", render: (r) => inr(r.total_paise) }, { key: "d", header: "Due", render: (r) => fmtDate(r.due_date) },
            { key: "s", header: "Status", render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> },
            { key: "a", header: "", render: (r) => r.status === "issued" ? <span className="flex gap-2"><Button className="min-h-8 px-3" onClick={() => setPay(r)}>Mark paid</Button><Button variant="ghost" className="min-h-8 px-2 text-bad" onClick={() => { if (window.confirm(`Void ${r.invoice_number}? This cannot be undone.`)) voidInv.mutate(r.id); }}>Void</Button></span> : null },
          ]} />)}
      </Card></div>
      <Modal open={edit !== null} onClose={() => setEdit(null)} title={edit ? `Billing: ${edit.name}` : ""} wide>
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />Charge this organization</label>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Rupees per cab-day">{(id) => <Input id={id} inputMode="decimal" value={form.rupees} onChange={(e) => setForm({ ...form, rupees: e.target.value })} />}</Field>
            <Field label="GST %">{(id) => <Input id={id} inputMode="decimal" value={form.gst} onChange={(e) => setForm({ ...form, gst: e.target.value })} />}</Field>
            <Field label="Free until (optional)">{(id) => <Input id={id} type="date" value={form.free_until} onChange={(e) => setForm({ ...form, free_until: e.target.value })} />}</Field>
            <Field label="Due after (days)">{(id) => <Input id={id} inputMode="numeric" value={form.due_days} onChange={(e) => setForm({ ...form, due_days: e.target.value })} />}</Field>
            <Field label="Grace before pause (days)">{(id) => <Input id={id} inputMode="numeric" value={form.grace_days} onChange={(e) => setForm({ ...form, grace_days: e.target.value })} />}</Field>
          </div>
          <div className="flex flex-wrap items-end gap-2 border-t border-line pt-3">
            <Field label="Issue invoice for month">{(id) => <Input id={id} type="month" value={form.month} onChange={(e) => setForm({ ...form, month: e.target.value })} />}</Field>
            <Button variant="secondary" busy={generate.isPending} disabled={!edit?.billing_enabled} onClick={() => generate.mutate()}>Issue / find invoice</Button>
          </div>
          <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setEdit(null)}>Close</Button><Button busy={save.isPending} onClick={() => save.mutate()}>Save settings</Button></div>
        </div>
      </Modal>
      <Modal open={pay !== null} onClose={() => setPay(null)} title={pay ? `Record payment for ${pay.invoice_number}` : ""}>
        <div className="space-y-3">
          <p className="text-sm">{pay && `${pay.organization_name}: ${inr(pay.total_paise)}`}</p>
          <Field label="UPI / bank reference">{(id) => <Input id={id} value={ref} onChange={(e) => setRef(e.target.value)} />}</Field>
          <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setPay(null)}>Cancel</Button><Button busy={markPaid.isPending} disabled={ref.trim().length < 3} onClick={() => markPaid.mutate()}>Mark paid</Button></div>
        </div>
      </Modal>
    </>
  );
}
