import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, buildQuery } from "../lib/api";
import { useAction, useFeatures, useMe } from "../lib/hooks";
import { fmtDateTime, today } from "../lib/format";
import type { Page } from "../lib/types";
import { Badge, Button, Card, Confirm, DataTable, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, Pager, Select, Spinner, StatusBadge } from "../components/ui";
import { FeatureNotice } from "../components/FeatureNotice";
import { useQueryClient } from "@tanstack/react-query";

// ---- Notifications inbox + delivery log ------------------------------------------------------------------------
interface Notif { id: string; title: string; body: string; critical: boolean; read_at: string | null; created_at: string }
export function Notifications() {
  const { data: me } = useMe();
  const staff = me?.role === "org_admin" || me?.role === "dispatcher";
  const [page, setPage] = useState(1);
  const [tab, setTab] = useState<"inbox" | "deliveries">("inbox");
  const inbox = useQuery({ queryKey: ["notifications", page], queryFn: () => api<Page<Notif> & { unread: number }>("/notifications" + buildQuery({ page })), refetchInterval: 30_000 });
  const read = useAction((id: string) => api(`/notifications/${id}/read`, { method: "POST" }), { invalidate: [["notifications"]] });
  const readAll = useAction(() => api("/notifications/read-all", { method: "POST" }), { invalidate: [["notifications"]] });
  return (
    <>
      <PageHeader title="Notifications" actions={tab === "inbox" ? <Button variant="secondary" onClick={() => readAll.mutate()}>Mark all read</Button> : undefined} />
      {staff && <div className="mb-4 flex gap-2" role="tablist">{(["inbox", "deliveries"] as const).map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`min-h-9 rounded-full px-4 text-sm ${tab === t ? "bg-accentsoft font-medium text-accent" : "hover:bg-surface2"}`}>{t === "inbox" ? "My inbox" : "Delivery log"}</button>)}</div>}
      {tab === "deliveries" ? <Deliveries /> : inbox.isPending ? <Spinner /> : inbox.isError ? <ErrorBox error={inbox.error} retry={() => void inbox.refetch()} /> : inbox.data.items.length === 0 ? <Empty title="You're all caught up" /> : (
        <><ul className="divide-y divide-line rounded-xl border border-line bg-surface">{inbox.data.items.map((n) => (
          <li key={n.id} className={`flex items-start gap-3 p-3 ${n.read_at ? "" : "bg-accentsoft/40"}`}>
            <div className="min-w-0 flex-1"><p className="flex items-center gap-2 font-medium">{n.title}{n.critical && <Badge tone="bad">critical</Badge>}</p><p className="text-sm text-muted">{n.body}</p><p className="mt-0.5 text-xs text-muted">{fmtDateTime(n.created_at)}</p></div>
            {!n.read_at && <Button variant="ghost" className="min-h-8 px-2" onClick={() => read.mutate(n.id)}>Mark read</Button>}
          </li>))}</ul><Pager page={page} size={inbox.data.page_size} total={inbox.data.total} onPage={setPage} /></>)}
    </>
  );
}
interface Delivery { id: string; channel: string; status: string; attempts: number; last_error: string | null; event_type: string; critical: boolean; created_at: string }
function Deliveries() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const q = useQuery({ queryKey: ["deliveries", status, page], queryFn: () => api<Page<Delivery>>("/notifications/deliveries" + buildQuery({ status, page })) });
  const retry = useAction((id: string) => api(`/notifications/deliveries/${id}/retry`, { method: "POST" }), { success: "Queued for retry", invalidate: [["deliveries"]] });
  return (
    <>
      <Notice tone="info">“Sent” means the provider accepted the message. SMS/WhatsApp become “delivered” only when the provider confirms; push and email are never reported as delivered.</Notice>
      <div className="my-3 max-w-xs"><Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}><option value="">All statuses</option>{["pending", "sent", "delivered", "failed", "skipped"].map((s) => <option key={s}>{s}</option>)}</Select></div>
      {q.isPending ? <Spinner /> : q.isError ? <ErrorBox error={q.error} /> : q.data.items.length === 0 ? <Empty title="No deliveries" /> : (
        <><DataTable caption="Deliveries" rows={q.data.items} columns={[{ key: "created_at", header: "When", render: (d) => fmtDateTime(d.created_at) }, { key: "event_type", header: "Event" }, { key: "channel", header: "Channel" },
          { key: "status", header: "Status", render: (d) => <StatusBadge status={d.status} /> }, { key: "attempts", header: "Attempts" }, { key: "last_error", header: "Note", render: (d) => d.last_error ?? "—" },
          { key: "act", header: "", render: (d) => d.status === "failed" ? <Button variant="ghost" className="min-h-8 px-2" onClick={() => retry.mutate(d.id)}>Retry</Button> : null }]} /><Pager page={page} size={q.data.page_size} total={q.data.total} onPage={setPage} /></>)}
    </>
  );
}

// ---- Reports -----------------------------------------------------------------------------------------------
const REPORTS: [string, string][] = [["vehicle-utilization", "Vehicle utilization"], ["passenger-utilization", "Passenger utilization"], ["unassigned-employees", "Unassigned employees"], ["driver-workloads", "Driver workloads"],
  ["leave-replacements", "Leave & replacements"], ["cancelled-trips", "Cancelled trips"], ["sos-incidents", "SOS incidents"], ["notification-delivery", "Notification delivery"], ["operational-exceptions", "Operational exceptions"]];
export function Reports() {
  const { data: me } = useMe();
  const [name, setName] = useState(REPORTS[0]![0]);
  const [end, setEnd] = useState(today());
  const [start, setStart] = useState(() => { const d = new Date(); d.setDate(d.getDate() - 6); return d.toISOString().slice(0, 10); });
  const q = useQuery({ queryKey: ["report", name, start, end], queryFn: () => api<{ rows: Record<string, unknown>[]; note: string | null; row_count: number }>(`/reports/${name}` + buildQuery({ start, end })) });
  const exp = useAction(async () => { const blob = await api<Blob>(`/reports/${name}` + buildQuery({ start, end, format: "csv" }), { raw: true });
    const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `${name}-${start}-${end}.csv`; a.click(); URL.revokeObjectURL(url); }, { success: "Export downloaded (audit-logged)" });
  const cols = q.data?.rows[0] ? Object.keys(q.data.rows[0]) : [];
  return (
    <>
      <PageHeader title="Reports" subtitle="Computed from live records for the selected range." actions={me?.role === "org_admin" ? <Button variant="secondary" busy={exp.isPending} onClick={() => exp.mutate()}>Export CSV</Button> : undefined} />
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Field label="Report">{(id) => <Select id={id} value={name} onChange={(e) => setName(e.target.value)}>{REPORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select>}</Field>
        <Field label="From">{(id) => <Input id={id} type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} />}</Field>
        <Field label="To">{(id) => <Input id={id} type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />}</Field>
      </div>
      {q.isPending ? <Spinner /> : q.isError ? <ErrorBox error={q.error} retry={() => void q.refetch()} /> : (
        <div className="space-y-3">{q.data.note && <Notice tone="info">{q.data.note}</Notice>}
          {q.data.rows.length === 0 ? <Empty title="No data in this range" /> : <DataTable caption={name} rows={q.data.rows.map((r, i) => ({ ...r, id: i }))} columns={cols.map((c) => ({ key: c, header: c.replace(/_/g, " "), render: (r: Record<string, unknown>) => String(r[c] ?? "—") }))} />}</div>)}
    </>
  );
}

// ---- Audit logs ------------------------------------------------------------------------------------------------
interface AuditRow { id: number; actor_user_id: string | null; action: string; entity_type: string; entity_id: string | null; details: Record<string, unknown>; created_at: string }
export function AuditLogs() {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const q = useQuery({ queryKey: ["audit", page, action], queryFn: () => api<Page<AuditRow>>("/audit-logs" + buildQuery({ page, action })), placeholderData: (p) => p });
  return (
    <>
      <PageHeader title="Audit logs" subtitle="Append-only. Entries cannot be edited or deleted." />
      <div className="mb-3 max-w-xs"><Input aria-label="Filter by action prefix" placeholder="Action prefix, e.g. sos." value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} /></div>
      {q.isPending ? <Spinner /> : q.isError ? <ErrorBox error={q.error} /> : q.data.items.length === 0 ? <Empty title="No audit entries" /> : (
        <><DataTable caption="Audit logs" rows={q.data.items} columns={[{ key: "created_at", header: "When", render: (r) => fmtDateTime(r.created_at) }, { key: "action", header: "Action" }, { key: "entity", header: "Entity", render: (r) => `${r.entity_type} ${r.entity_id?.slice(0, 8) ?? ""}` },
          { key: "actor_user_id", header: "Actor", render: (r) => r.actor_user_id?.slice(0, 8) ?? "system" }, { key: "details", header: "Details", render: (r) => <code className="text-xs">{JSON.stringify(r.details).slice(0, 120)}</code> }]} /><Pager page={page} size={q.data.page_size} total={q.data.total} onPage={setPage} /></>)}
    </>
  );
}

// ---- Settings & privacy ------------------------------------------------------------------------------------------
interface Settings { sos_gps_enabled: boolean; live_tracking_enabled: boolean; live_tracking_interval_seconds: number; live_tracking_history_enabled: boolean; live_tracking_history_retention_days: number; live_tracking_employee_view: boolean;
  sos_retention_days: number; max_detour_ratio: number; max_trip_minutes: number; require_dispatcher_confirmation: boolean; driver_acceptance_timeout_minutes: number; last_drop_policy_enabled: boolean;
  local_trip_target_per_day: number; local_trip_max_km: number | null; local_trips_excluded_from_km_fairness: boolean; fairness_weights: Record<string, number>; fairness_window_days: number; notification_channels: Record<string, boolean>; boarding_code_required: boolean; auto_dispatch_enabled: boolean; auto_dispatch_cutoff: string; auto_dispatch_days_ahead: number; auto_dispatch_send_to_drivers: boolean;
  updated_by_name: string | null; updated_by_email: string | null; features: { sos_gps: { effective: boolean; blocked_by: string | null; deployment_enabled: boolean; platform_enabled: boolean }; live_tracking: { effective: boolean; blocked_by: string | null; deployment_enabled: boolean; platform_enabled: boolean } } }

function Toggle({ label, desc, checked, disabled, onChange, why }: { label: string; desc: string; checked: boolean; disabled?: boolean; onChange: (v: boolean) => void; why?: string }) {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div><p className="font-medium">{label}</p><p className="text-sm text-muted">{desc}</p>{disabled && why && <p className="mt-1 text-sm text-warn">{why}</p>}</div>
      <input type="checkbox" role="switch" aria-label={label} className="mt-1 size-5 shrink-0" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
    </div>
  );
}
const blockedText = (b: string | null) => (b === "deployment" ? "Disabled for this deployment (server flag). Ask your platform operator." : b === "platform" ? "Not enabled by the platform administrator." : "");

export function SettingsPage() {
  const qc = useQueryClient();
  const features = useFeatures();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/admin/settings") });
  const [pending, setPending] = useState<{ key: "sos_gps_enabled" | "live_tracking_enabled"; value: boolean } | null>(null);
  const [form, setForm] = useState<Partial<Settings>>({});
  const save = useAction((body: Record<string, unknown>) => api<Settings>("/admin/settings", { method: "PUT", body }), { success: "Settings saved", onSuccess: () => { setPending(null); setForm({}); void qc.invalidateQueries({ queryKey: ["settings"] }); void qc.invalidateQueries({ queryKey: ["features"] }); } });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => void q.refetch()} />;
  const s = { ...q.data, ...form } as Settings;
  const f = q.data.features;
  const num = (k: keyof Settings, label: string, min: number, max: number, step = 1) => (
    <Field label={label}>{(id) => <Input id={id} type="number" min={min} max={max} step={step} value={String(s[k] ?? "")} onChange={(e) => setForm({ ...form, [k]: e.target.value === "" ? undefined : Number(e.target.value) })} />}</Field>);
  const dirty = Object.keys(form).length > 0;
  return (
    <>
      <PageHeader title="Settings & privacy" subtitle={q.data.updated_by_email ? `Last changed by ${q.data.updated_by_name || q.data.updated_by_email}` : undefined} />
      <div className="space-y-5">
        <Card title="Location features (off by default)">
          <FeatureNotice name="SOS GPS" state={features.data?.sos_gps} />
          <div className="divide-y divide-line">
            <Toggle label="SOS GPS location" desc="When someone raises an SOS, their phone may attach its current location. The alert is always delivered even if location is off, denied or unavailable. Stored only for the retention period below." checked={s.sos_gps_enabled} disabled={!f.sos_gps.deployment_enabled || !f.sos_gps.platform_enabled} why={blockedText(f.sos_gps.blocked_by)} onChange={(v) => setPending({ key: "sos_gps_enabled", value: v })} />
            <Toggle label="Live vehicle tracking" desc="Drivers' phones share the vehicle position only while a trip is in progress. Admins and dispatchers can then follow any vehicle. Turning it off stops sharing and clears live positions." checked={s.live_tracking_enabled} disabled={!f.live_tracking.deployment_enabled || !f.live_tracking.platform_enabled} why={blockedText(f.live_tracking.blocked_by)} onChange={(v) => setPending({ key: "live_tracking_enabled", value: v })} />
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            {num("sos_retention_days", "SOS location retention (days)", 1, 3650)}
            {num("live_tracking_interval_seconds", "Tracking interval (seconds)", 10, 300)}
            {num("live_tracking_history_retention_days", "Tracking history retention (days)", 1, 365)}
          </div>
          <div className="mt-2 divide-y divide-line">
            <Toggle label="Let employees see their own cab" desc="Passengers see only the vehicle on their own in-progress trip." checked={s.live_tracking_employee_view} onChange={(v) => setForm({ ...form, live_tracking_employee_view: v })} />
            <Toggle label="Keep tracking history" desc="Stores breadcrumb points for the retention period. Off by default; only the latest position is kept otherwise." checked={s.live_tracking_history_enabled} onChange={(v) => setForm({ ...form, live_tracking_history_enabled: v })} />
          </div>
        </Card>
        <Card title="Dispatch policy">
          <div className="grid gap-4 sm:grid-cols-3">
            {num("max_detour_ratio", "Max detour ratio", 1, 5, 0.05)}{num("max_trip_minutes", "Max trip minutes", 10, 480)}{num("driver_acceptance_timeout_minutes", "Driver response timeout (min)", 1, 1440)}
            {num("local_trip_target_per_day", "Local trips per driver per day", 0, 10)}{num("fairness_window_days", "Fairness window (days)", 1, 90)}
          </div>
          <div className="mt-2 divide-y divide-line">
            <Toggle label="Require dispatcher confirmation" desc="Employees are notified only after a dispatcher confirms an accepted trip." checked={s.require_dispatcher_confirmation} onChange={(v) => setForm({ ...form, require_dispatcher_confirmation: v })} />
            <Toggle label="Last-drop policy" desc="Soft preference for the last drop of a route (never overrides road distance, capacity or detour limits)." checked={s.last_drop_policy_enabled} onChange={(v) => setForm({ ...form, last_drop_policy_enabled: v })} />
            <Toggle label="Exclude local trips from kilometre fairness" desc="Short local trips do not count toward a driver's kilometre total." checked={s.local_trips_excluded_from_km_fairness} onChange={(v) => setForm({ ...form, local_trips_excluded_from_km_fairness: v })} />
          </div>
        </Card>
        <Card title="Boarding verification (off by default)">
          <div className="divide-y divide-line">
            <Toggle label="Require passenger boarding code" desc="Each passenger sees a 4-digit code in their app; the driver must enter it to mark them boarded. Dispatchers can board someone manually (audited)." checked={Boolean(s.boarding_code_required)} onChange={(v) => setForm({ ...form, boarding_code_required: v })} />
          </div>
        </Card>
        <Card title="Auto-dispatch (off by default)">
          <div className="divide-y divide-line">
            <Toggle label="Plan tomorrow's trips automatically" desc="Every day at the cutoff time the system plans all shifts, keeping the same cab and driver for the same people when possible, and tells you what needs attention. Slots you already planned are never changed." checked={Boolean(s.auto_dispatch_enabled)} onChange={(v) => setForm({ ...form, auto_dispatch_enabled: v })} />
            <Toggle label="Send trips to drivers automatically" desc="Drivers get the trip offer straight away. Turn off to review plans first; they stay as proposals for you to send." checked={Boolean(s.auto_dispatch_send_to_drivers)} onChange={(v) => setForm({ ...form, auto_dispatch_send_to_drivers: v })} />
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <Field label="Cutoff time (organization time zone)">{(id) => <Input id={id} type="time" value={String(s.auto_dispatch_cutoff ?? "18:00").slice(0, 5)} onChange={(e) => setForm({ ...form, auto_dispatch_cutoff: e.target.value })} />}</Field>
            <Field label="Plan for">{(id) => <Select id={id} value={String(s.auto_dispatch_days_ahead ?? 1)} onChange={(e) => setForm({ ...form, auto_dispatch_days_ahead: Number(e.target.value) })}><option value="1">Next day</option><option value="2">2 days ahead</option><option value="3">3 days ahead</option></Select>}</Field>
          </div>
        </Card>
        <Card title="Notification channels">
          <div className="flex flex-wrap gap-4">{(["push", "email", "sms", "whatsapp"] as const).map((c) => (
            <label key={c} className="flex items-center gap-2 text-sm"><input type="checkbox" className="size-4" checked={Boolean((s.notification_channels ?? {})[c])} onChange={(e) => setForm({ ...form, notification_channels: { ...(s.notification_channels ?? {}), [c]: e.target.checked } })} />{c.toUpperCase()}</label>))}</div>
          <p className="mt-2 text-xs text-muted">SMS and WhatsApp also require the platform administrator to enable them and provider credentials on the server.</p>
        </Card>
        <div className="flex justify-end"><Button disabled={!dirty} busy={save.isPending && !pending} onClick={() => save.mutate(form)}>Save changes</Button></div>
      </div>
      <Confirm open={Boolean(pending)} title={pending?.value ? "Turn on this feature?" : "Turn off this feature?"} confirmLabel={pending?.value ? "Turn on" : "Turn off"} busy={save.isPending} onClose={() => setPending(null)}
        onConfirm={() => pending && save.mutate({ [pending.key]: pending.value, confirm: true })}
        message={pending?.key === "sos_gps_enabled" ? (pending.value ? "People will be asked for permission to attach their location to an SOS. Location data is retained for the configured period and access is audit-logged." : "SOS alerts continue to work, but no location is collected or stored.") : (pending?.value ? "Drivers' phones will share vehicle position during trips in progress. This increases hosting usage. Admins and dispatchers will be able to view it." : "Drivers stop sharing location immediately and live positions are deleted.")} />
    </>
  );
}

// ---- Members ----------------------------------------------------------------------------------------------------
interface Member { id: string; user_id: string; role: string; is_active: boolean; email: string; full_name: string }
export function Members() {
  const [role, setRole] = useState("");
  const q = useQuery({ queryKey: ["members", role], queryFn: () => api<Page<Member>>("/members" + buildQuery({ role, page_size: 100 })) });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ email: "", full_name: "", role: "dispatcher" });
  const [rm, setRm] = useState<Member | null>(null);
  const add = useAction(() => api("/members", { method: "POST", body: form }), { success: "Member added. New users receive an email invitation.", invalidate: [["members"]], onSuccess: () => { setOpen(false); setForm({ email: "", full_name: "", role: "dispatcher" }); } });
  const remove = useAction((u: string) => api(`/members/${u}`, { method: "DELETE" }), { success: "Member deactivated", invalidate: [["members"]], onSuccess: () => setRm(null) });
  return (
    <>
      <PageHeader title="Members" subtitle="People who can sign in to this organization. Link drivers and employees to their records from the Drivers / Employees pages." actions={<Button onClick={() => setOpen(true)}>Add member</Button>} />
      <div className="mb-3 max-w-xs"><Select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value)}><option value="">All roles</option>{["org_admin", "dispatcher", "driver", "employee"].map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}</Select></div>
      {q.isPending ? <Spinner /> : q.isError ? <ErrorBox error={q.error} /> : <DataTable caption="Members" rows={q.data.items} columns={[{ key: "full_name", header: "Name" }, { key: "email", header: "Email" }, { key: "role", header: "Role", render: (m) => <Badge>{m.role.replace("_", " ")}</Badge> },
        { key: "is_active", header: "Status", render: (m) => <Badge tone={m.is_active ? "ok" : "neutral"}>{m.is_active ? "active" : "inactive"}</Badge> }, { key: "act", header: "", render: (m) => m.is_active ? <Button variant="ghost" className="min-h-8 px-2 text-bad" onClick={() => setRm(m)}>Deactivate</Button> : null }]} />}
      <Modal open={open} onClose={() => setOpen(false)} title="Add member"><div className="space-y-3">
        <Field label="Email *">{(id) => <Input id={id} type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />}</Field>
        <Field label="Full name">{(id) => <Input id={id} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />}</Field>
        <Field label="Role">{(id) => <Select id={id} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{["org_admin", "dispatcher", "driver", "employee"].map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}</Select>}</Field>
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button busy={add.isPending} disabled={!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)} onClick={() => add.mutate()}>Add</Button></div></div></Modal>
      <Confirm open={Boolean(rm)} danger title="Deactivate member?" confirmLabel="Deactivate" busy={remove.isPending} onClose={() => setRm(null)} onConfirm={() => rm && remove.mutate(rm.user_id)} message={<>{rm?.email} will no longer be able to use this organization. The last administrator cannot be removed.</>} />
    </>
  );
}

// ---- Platform ----------------------------------------------------------------------------------------------------
interface Org { id: string; name: string; slug: string; timezone: string; status: string; created_at: string }
export function Organizations() {
  const [page, setPage] = useState(1);
  const q = useQuery({ queryKey: ["orgs", page], queryFn: () => api<Page<Org>>("/organizations" + buildQuery({ page })) });
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", slug: "", timezone: "Asia/Kolkata", admin_email: "", admin_name: "" });
  const create = useAction(() => api("/organizations", { method: "POST", body: f }), { success: "Organization created", invalidate: [["orgs"]], onSuccess: () => setOpen(false) });
  const status = useAction((v: { id: string; s: "active" | "suspended" }) => api(`/organizations/${v.id}/status` + buildQuery({ status: v.s }), { method: "POST" }), { success: "Status updated", invalidate: [["orgs"]] });
  return (
    <>
      <PageHeader title="Organizations" subtitle="Each organization's data is isolated from every other." actions={<Button onClick={() => setOpen(true)}>New organization</Button>} />
      {q.isPending ? <Spinner /> : q.isError ? <ErrorBox error={q.error} /> : q.data.items.length === 0 ? <Empty title="No organizations yet" /> : (
        <><DataTable caption="Organizations" rows={q.data.items} columns={[{ key: "name", header: "Name" }, { key: "slug", header: "Slug" }, { key: "timezone", header: "Timezone" }, { key: "status", header: "Status", render: (o) => <StatusBadge status={o.status === "active" ? "approved" : "rejected"} /> },
          { key: "act", header: "", render: (o) => <Button variant="ghost" className="min-h-8 px-2" onClick={() => status.mutate({ id: o.id, s: o.status === "active" ? "suspended" : "active" })}>{o.status === "active" ? "Suspend" : "Reactivate"}</Button> }]} /><Pager page={page} size={q.data.page_size} total={q.data.total} onPage={setPage} /></>)}
      <Modal open={open} onClose={() => setOpen(false)} title="New organization"><div className="space-y-3">
        <Field label="Name *">{(id) => <Input id={id} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
        <Field label="Slug *" hint="lowercase letters, numbers, hyphens">{(id) => <Input id={id} value={f.slug} onChange={(e) => setF({ ...f, slug: e.target.value.toLowerCase() })} />}</Field>
        <Field label="Timezone">{(id) => <Input id={id} value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} />}</Field>
        <Field label="Administrator email *">{(id) => <Input id={id} type="email" value={f.admin_email} onChange={(e) => setF({ ...f, admin_email: e.target.value })} />}</Field>
        <Field label="Administrator name">{(id) => <Input id={id} value={f.admin_name} onChange={(e) => setF({ ...f, admin_name: e.target.value })} />}</Field>
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button busy={create.isPending} disabled={!f.name || !/^[a-z0-9][a-z0-9-]{1,62}$/.test(f.slug) || !f.admin_email} onClick={() => create.mutate()}>Create</Button></div></div></Modal>
    </>
  );
}

interface Flag { key: string; enabled: boolean; description: string | null; deployment_enabled: boolean | null; updated_at: string | null }
export function Flags() {
  const q = useQuery({ queryKey: ["flags"], queryFn: () => api<Flag[]>("/platform/flags") });
  const [pending, setPending] = useState<{ key: string; enabled: boolean } | null>(null);
  const set = useAction(() => api(`/platform/flags/${pending!.key}`, { method: "PUT", body: { enabled: pending!.enabled, confirm: true } }), { success: "Flag updated", invalidate: [["flags"], ["features"]], onSuccess: () => setPending(null) });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorBox error={q.error} />;
  return (
    <>
      <PageHeader title="Platform feature flags" subtitle="A feature runs for an organization only when the deployment flag, this platform flag and the organization's own setting are all on. Everything defaults to off." />
      <div className="space-y-3">{q.data.map((fl) => (
        <Card key={fl.key}><div className="flex items-start justify-between gap-4"><div><p className="font-medium">{fl.key}</p><p className="text-sm text-muted">{fl.description}</p>
          {fl.deployment_enabled !== null && <p className="mt-1 text-sm">Deployment (server env): <Badge tone={fl.deployment_enabled ? "ok" : "neutral"}>{fl.deployment_enabled ? "on" : "off"}</Badge></p>}</div>
          <input type="checkbox" role="switch" aria-label={fl.key} className="mt-1 size-5" checked={fl.enabled} onChange={(e) => setPending({ key: fl.key, enabled: e.target.checked })} /></div></Card>))}</div>
      <Confirm open={Boolean(pending)} title="Change platform flag?" busy={set.isPending} onClose={() => setPending(null)} onConfirm={() => set.mutate()} confirmLabel="Change" message={<>Set <strong>{pending?.key}</strong> to <strong>{pending?.enabled ? "on" : "off"}</strong> for the whole platform. This is audit-logged.</>} />
    </>
  );
}

export function Health() {
  const q = useQuery({ queryKey: ["health"], queryFn: () => api<Record<string, unknown>>("/health/status"), refetchInterval: 15_000 });
  return (
    <>
      <PageHeader title="System health" />
      {q.isPending ? <Spinner /> : q.isError ? <ErrorBox error={q.error} retry={() => void q.refetch()} /> : <Card><pre className="overflow-x-auto text-sm">{JSON.stringify(q.data, null, 2)}</pre></Card>}
    </>
  );
}
export { ApiError };
