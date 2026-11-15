import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, buildQuery } from "../lib/api";
import { useAction, useMe } from "../lib/hooks";
import { fmtDate, tomorrow, today } from "../lib/format";
import type { Page } from "../lib/types";
import { Badge, Button, Card, DataTable, Empty, ErrorBox, Field, Input, Modal, PageHeader, Pager, Select, Spinner, StatusBadge, Notice } from "../components/ui";
import { useLookup } from "../components/ResourcePage";

// ---- Overview --------------------------------------------------------------------------------------------
export function Overview() {
  const q = useQuery({ queryKey: ["overview"], queryFn: () => api<Record<string, number | string>>("/reports"), refetchInterval: 30_000 });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => void q.refetch()} />;
  const d = q.data;
  const tiles: [string, string, boolean?][] = [
    ["trips_today", "Trips today"], ["trips_in_progress", "In progress"], ["pending_driver_acceptance", "Awaiting driver"], ["trips_requiring_reassignment", "Need reassignment", true],
    ["unassigned_requests_today", "Unassigned requests", true], ["open_sos", "Open SOS", true], ["drivers_on_leave_today", "Drivers on leave"], ["vehicles_unavailable", "Vehicles unavailable"],
    ["employees_active", "Active employees"], ["drivers_active", "Active drivers"], ["vehicles_active", "Active vehicles"], ["failed_notifications_24h", "Failed notifications (24h)", true],
  ];
  return (
    <>
      <PageHeader title="Overview" subtitle={`Today in your organization's timezone: ${fmtDate(String(d.today))}`} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
        {tiles.map(([k, label, warn]) => { const v = Number(d[k] ?? 0); return (
          <div key={k} className="rounded-xl border border-line bg-surface p-4"><p className="text-sm text-muted">{label}</p><p className={`mt-1 text-2xl font-semibold ${warn && v > 0 ? "text-bad" : ""}`}>{v}</p></div>); })}
      </div>
      <div className="mt-6 flex flex-wrap gap-2"><Link to="/allocations"><Button>Plan tomorrow's trips</Button></Link><Link to="/exceptions"><Button variant="secondary">Review exceptions</Button></Link></div>
    </>
  );
}

// ---- Exceptions --------------------------------------------------------------------------------------------
interface Exc { allocations: { id: string; service_date: string; direction: string; status: string; estimate_status: string; vehicle_code: string; issue: string }[]; open_requests: number; recent_declines_or_expiries: { allocation_id: string; driver_id: string; responded_at: string }[] }
export function Exceptions() {
  const q = useQuery({ queryKey: ["exceptions"], queryFn: () => api<Exc>("/dispatch/exceptions"), refetchInterval: 30_000 });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => void q.refetch()} />;
  const d = q.data;
  return (
    <>
      <PageHeader title="Dispatch exceptions" subtitle="Things that need a dispatcher before they become service failures." />
      <div className="space-y-5">
        <Card title="Unassigned requests">{d.open_requests > 0 ? <p className="text-sm"><strong>{d.open_requests}</strong> open transport requests from today onwards are not on any trip. <Link className="underline" to="/allocations">Open planner</Link></p> : <p className="text-sm text-muted">None.</p>}</Card>
        <Card title="Trips that need attention">
          {d.allocations.length === 0 ? <p className="text-sm text-muted">None.</p> : <DataTable caption="Exceptions" rows={d.allocations} onRowClick={() => undefined} columns={[
            { key: "service_date", header: "Date", render: (r) => fmtDate(r.service_date) }, { key: "vehicle_code", header: "Vehicle" }, { key: "direction", header: "Direction" },
            { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> }, { key: "issue", header: "Issue", render: (r) => <Badge tone="bad">{r.issue.replace(/_/g, " ")}</Badge> },
            { key: "open", header: "", render: (r) => <Link className="underline" to={`/allocations?date=${r.service_date}&open=${r.id}`}>Open</Link> }]} />}
        </Card>
        <Card title="Recent declines / expired offers (48h)">{d.recent_declines_or_expiries.length === 0 ? <p className="text-sm text-muted">None.</p> :
          <ul className="space-y-1 text-sm">{d.recent_declines_or_expiries.map((r) => <li key={r.allocation_id + r.responded_at}><Link className="underline" to={`/allocations?open=${r.allocation_id}`}>Trip {r.allocation_id.slice(0, 8)}</Link> — driver {r.driver_id.slice(0, 8)}</li>)}</ul>}</Card>
      </div>
    </>
  );
}

// ---- Vehicle board ------------------------------------------------------------------------------------------
interface BoardRow { vehicle_id: string; vehicle_code: string; registration_number: string; capacity: number; status: string; max_passengers: number; allocations: number; default_route_id: string | null; override_route_id: string | null }
export function VehicleBoard() {
  const [date, setDate] = useState(tomorrow());
  const [target, setTarget] = useState<BoardRow | null>(null);
  const q = useQuery({ queryKey: ["vehicle-board", date], queryFn: () => api<{ vehicles: BoardRow[] }>("/vehicles-board" + buildQuery({ service_date: date })) });
  const routes = useLookup("routes");
  const [route, setRoute] = useState("");
  const [reason, setReason] = useState("");
  const set = useAction(() => api(`/vehicles/${target!.vehicle_id}/route-override`, { method: "POST", body: { service_date: date, to_route_id: route, reason } }), { success: "Route changed for this date only", invalidate: [["vehicle-board"]], onSuccess: () => setTarget(null) });
  const clear = useAction((v: string) => api(`/vehicles/${v}/route-override/${date}`, { method: "DELETE" }), { success: "Back to the default route", invalidate: [["vehicle-board"]] });
  return (
    <>
      <PageHeader title="Vehicle board" subtitle="An assigned-but-empty cab is not free. Route changes here apply to the selected date only; the default route is never altered." actions={<Input aria-label="Service date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />} />
      {q.isPending ? <Spinner /> : q.isError ? <ErrorBox error={q.error} retry={() => void q.refetch()} /> : q.data.vehicles.length === 0 ? <Empty title="No vehicles" /> : (
        <DataTable caption="Vehicle board" rows={q.data.vehicles.map((v) => ({ ...v, id: v.vehicle_id }))} columns={[
          { key: "vehicle_code", header: "Vehicle", render: (v) => <span><strong>{v.vehicle_code}</strong> <span className="text-muted">{v.registration_number}</span></span> },
          { key: "status", header: "Status", render: (v) => <StatusBadge status={v.status} /> }, { key: "seats", header: "Seats used", render: (v) => `${v.max_passengers}/${v.capacity}` }, { key: "allocations", header: "Trips" },
          { key: "route", header: "Route", render: (v) => v.override_route_id ? <span><Badge tone="warn">date-specific route</Badge> <button className="ml-2 text-xs underline" onClick={() => clear.mutate(v.vehicle_id)}>Revert</button></span> : <span className="text-muted">default</span> },
          { key: "act", header: "", render: (v) => <Button variant="ghost" className="min-h-8 px-2" onClick={() => { setTarget(v); setRoute(""); setReason(""); }}>Change route for {date === today() ? "today" : "this date"}</Button> }]} />)}
      <Modal open={Boolean(target)} onClose={() => setTarget(null)} title={`Change route — ${target?.vehicle_code ?? ""}`}>
        <div className="space-y-3"><Notice tone="info">Applies to {fmtDate(date)} only and is audited. The recurring default route stays unchanged.</Notice>
          <Field label="Route for this date">{(id) => <Select id={id} value={route} onChange={(e) => setRoute(e.target.value)}><option value="">Select route…</option>{routes.data?.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}</Select>}</Field>
          <Field label="Reason">{(id) => <Input id={id} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
          <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setTarget(null)}>Cancel</Button><Button disabled={!route || reason.trim().length < 3} busy={set.isPending} onClick={() => set.mutate()}>Apply</Button></div></div>
      </Modal>
    </>
  );
}

// ---- Driver leave -----------------------------------------------------------------------------------------------
interface Leave { id: string; driver_id: string; driver_name: string; starts_on: string; ends_on: string; status: string; reason: string }
export function LeavePage() {
  const { data: me } = useMe();
  const staff = me?.role === "org_admin" || me?.role === "dispatcher";
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ driver_id: "", starts_on: tomorrow(), ends_on: tomorrow(), reason: "" });
  const drivers = useLookup("drivers");
  const q = useQuery({ queryKey: ["leave", page, status], queryFn: () => api<Page<Leave>>("/driver-leave" + buildQuery({ page, status })), placeholderData: (p) => p });
  const [outcome, setOutcome] = useState<{ allocation_id: string; outcome: string }[] | null>(null);
  const file = useAction(() => api("/driver-leave", { method: "POST", body: { ...(staff ? { driver_id: form.driver_id } : {}), starts_on: form.starts_on, ends_on: form.ends_on, reason: form.reason } }), { success: "Leave requested", invalidate: [["leave"]], onSuccess: () => setOpen(false) });
  const decide = useAction((v: { id: string; approve: boolean }) => api<{ affected_allocations: { allocation_id: string; outcome: string }[] }>(`/driver-leave/${v.id}/decision`, { method: "POST", body: { approve: v.approve } }),
    { success: "Decision recorded", invalidate: [["leave"], ["allocations"], ["exceptions"]], onSuccess: (r) => setOutcome(r.affected_allocations ?? []) });
  const cancel = useAction((id: string) => api(`/driver-leave/${id}/cancel`, { method: "POST" }), { success: "Leave cancelled", invalidate: [["leave"]] });
  return (
    <>
      <PageHeader title="Driver leave" subtitle="Approving leave offers affected trips to the fairest eligible replacement; the trip is not final until that driver accepts." actions={<Button onClick={() => setOpen(true)}>Request leave</Button>} />
      {outcome && <div className="mb-4"><Notice tone={outcome.some((o) => o.outcome !== "offered") ? "warn" : "ok"}>{outcome.length === 0 ? "No trips were affected." : outcome.map((o) => `Trip ${o.allocation_id.slice(0, 8)}: ${o.outcome.replace(/_/g, " ")}`).join(" · ")}</Notice></div>}
      <div className="mb-3 max-w-xs"><Select aria-label="Filter by status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}><option value="">All statuses</option>{["requested", "approved", "rejected", "cancelled"].map((s) => <option key={s}>{s}</option>)}</Select></div>
      {q.isPending ? <Spinner /> : q.isError ? <ErrorBox error={q.error} retry={() => void q.refetch()} /> : q.data.items.length === 0 ? <Empty title="No leave records" /> : (
        <><DataTable caption="Driver leave" rows={q.data.items} columns={[{ key: "driver_name", header: "Driver" }, { key: "dates", header: "Dates", render: (l) => `${fmtDate(l.starts_on)} → ${fmtDate(l.ends_on)}` }, { key: "reason", header: "Reason" },
          { key: "status", header: "Status", render: (l) => <StatusBadge status={l.status} /> },
          { key: "act", header: "", className: "text-right", render: (l) => (
            <div className="flex justify-end gap-1">
              {staff && l.status === "requested" && <><Button className="min-h-8 px-2" onClick={() => decide.mutate({ id: l.id, approve: true })}>Approve</Button><Button variant="secondary" className="min-h-8 px-2" onClick={() => decide.mutate({ id: l.id, approve: false })}>Reject</Button></>}
              {["requested", "approved"].includes(l.status) && <Button variant="ghost" className="min-h-8 px-2" onClick={() => cancel.mutate(l.id)}>Cancel</Button>}
            </div>) }]} /><Pager page={page} size={q.data.page_size} total={q.data.total} onPage={setPage} /></>)}
      <Modal open={open} onClose={() => setOpen(false)} title="Request leave">
        <div className="space-y-3">
          {staff && <Field label="Driver *">{(id) => <Select id={id} value={form.driver_id} onChange={(e) => setForm({ ...form, driver_id: e.target.value })}><option value="">Select…</option>{drivers.data?.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}</Select>}</Field>}
          <div className="grid grid-cols-2 gap-3"><Field label="From">{(id) => <Input id={id} type="date" value={form.starts_on} onChange={(e) => setForm({ ...form, starts_on: e.target.value })} />}</Field><Field label="To" error={form.ends_on < form.starts_on ? "End must not be before start" : undefined}>{(id) => <Input id={id} type="date" value={form.ends_on} onChange={(e) => setForm({ ...form, ends_on: e.target.value })} />}</Field></div>
          <Field label="Reason">{(id) => <Input id={id} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} />}</Field>
          <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button busy={file.isPending} disabled={form.ends_on < form.starts_on || (staff && !form.driver_id)} onClick={() => file.mutate()}>Submit</Button></div>
        </div>
      </Modal>
    </>
  );
}
