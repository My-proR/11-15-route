import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Play, Sparkles } from "lucide-react";
import { api, buildQuery, newKey } from "../lib/api";
import { useAction } from "../lib/hooks";
import type { Allocation, Page } from "../lib/types";
import { fmtKm, fmtMin, tomorrow } from "../lib/format";
import { Badge, Button, Card, Confirm, DataTable, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, Pager, Select, Spinner, StatusBadge } from "../components/ui";
import { useLookup } from "../components/ResourcePage";
import AllocationDetail from "./AllocationDetail";

interface PlanStop { request_id: string; employee_id: string; order: number; eta_minutes: number | null; ride_minutes: number | null }
interface PlanRoute { vehicle_id: string; driver_id: string | null; capacity: number; passengers: number; is_local_trip: boolean; distance_km: number | null; duration_minutes: number | null; estimate_status: string; stops: PlanStop[];
  explanation: { vehicle: string; policy_notes: string[] | Record<string, string>; fairness: { driver_id: string; name: string; rank: number; score: number; components: Record<string, { value: number; share?: number; weighted?: number }> | unknown; local_trip_note?: string }[] } }
interface Plan { routing_provider: string; estimate_status: string; can_finalize: boolean; routes: PlanRoute[]; unassigned?: { request_id: string; employee_code: string; name: string; reason: string }[]; conflicts?: { type: string }[]; excluded_vehicles?: { vehicle_code: string; reason: string }[]; summary?: { requests: number; assigned: number } }
const notesOf = (n: string[] | Record<string, string> | null | undefined): string[] => (Array.isArray(n) ? n : n ? Object.values(n).map(String) : []);
interface RequestRow { id: string; employee_id: string; name: string; employee_code: string; status: string }

export default function Allocations() {
  const [sp, setSp] = useSearchParams();
  const date = sp.get("date") ?? tomorrow();
  const shift = sp.get("shift") ?? "";
  const direction = (sp.get("dir") ?? "pickup") as "pickup" | "drop";
  const open = sp.get("open");
  const [page, setPage] = useState(1);
  const shifts = useLookup("shifts");
  const set = (k: string, v: string) => setSp((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n; }, { replace: true });
  const [plan, setPlan] = useState<Plan | null>(null);
  const [confirmCommit, setConfirmCommit] = useState(false);

  const slotReady = Boolean(shift);
  const reqs = useQuery({ queryKey: ["dispatch-requests", date, shift, direction], enabled: slotReady, queryFn: () => api<Page<RequestRow>>("/dispatch/requests" + buildQuery({ service_date: date, shift_id: shift, direction, page_size: 200 })) });
  const names = useMemo(() => new Map((reqs.data?.items ?? []).map((r) => [r.employee_id, `${r.name} (${r.employee_code})`])), [reqs.data]);
  const list = useQuery({ queryKey: ["allocations", date, page], queryFn: () => api<Page<Allocation>>("/allocations" + buildQuery({ service_date: date, page, page_size: 25 })), placeholderData: (p) => p });
  const vehicles = useQuery({ queryKey: ["vehicles-lookup"], queryFn: () => api<Page<{ id: string; vehicle_code: string }>>("/vehicles?page_size=200") });
  const drivers = useQuery({ queryKey: ["drivers-lookup"], queryFn: () => api<Page<{ id: string; name: string }>>("/drivers?page_size=200") });
  const vName = (id: string) => vehicles.data?.items.find((v) => v.id === id)?.vehicle_code ?? id.slice(0, 8);
  const dName = (id: string | null) => (id ? drivers.data?.items.find((d) => d.id === id)?.name ?? id.slice(0, 8) : "No driver");

  const body = { service_date: date, shift_id: shift, direction, generate_requests: true };
  const preview = useAction(() => api<{ plan: Plan }>("/dispatch/allocate", { method: "POST", body: { ...body, dry_run: true } }), { onSuccess: (r) => setPlan(r.plan), invalidate: [["dispatch-requests"]] });
  const commit = useAction(() => api("/dispatch/allocate", { method: "POST", idempotencyKey: newKey(), body: { ...body, dry_run: false, replace_proposed: true } }),
    { success: "Proposed allocations saved. Nothing has been sent to drivers yet.", invalidate: [["allocations"], ["dispatch-requests"]], onSuccess: () => { setPlan(null); setConfirmCommit(false); } });

  return (
    <>
      <PageHeader title="Daily allocations" subtitle="Preview a plan, save it as proposals, then send each trip to its driver. Employees are notified only after the driver accepts and you confirm." />
      <Card className="mb-5" title="Plan a slot">
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="Service date">{(id) => <Input id={id} type="date" value={date} onChange={(e) => { set("date", e.target.value); setPlan(null); }} />}</Field>
          <Field label="Shift">{(id) => <Select id={id} value={shift} onChange={(e) => { set("shift", e.target.value); setPlan(null); }}><option value="">Select shift…</option>{shifts.data?.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</Select>}</Field>
          <Field label="Direction">{(id) => <Select id={id} value={direction} onChange={(e) => { set("dir", e.target.value); setPlan(null); }}><option value="pickup">Pickup (home → office)</option><option value="drop">Drop (office → home)</option></Select>}</Field>
          <div className="flex items-end"><Button className="w-full" disabled={!slotReady} busy={preview.isPending} onClick={() => preview.mutate()}><Sparkles className="size-4" />Preview plan</Button></div>
        </div>
        {slotReady && reqs.data && <p className="mt-3 text-sm text-muted">{reqs.data.total} transport requests for this slot ({reqs.data.items.filter((r) => r.status === "open").length} open).</p>}
      </Card>

      {plan && (
        <Card className="mb-5" title="Proposed plan (not saved)" actions={<Button disabled={plan.routes.length === 0} onClick={() => setConfirmCommit(true)}><Play className="size-4" />Save as proposals</Button>}>
          <div className="space-y-4">
            {plan.estimate_status !== "final" && <Notice tone="warn"><strong>Road distances are not final.</strong> The road-routing service was unavailable, so distances and times are not shown and these trips cannot be sent or confirmed until routing is restored and the plan is re-run. Routing: {plan.routing_provider}.</Notice>}
            {plan.excluded_vehicles && plan.excluded_vehicles.length > 0 && <Notice tone="info">Vehicles excluded from this plan: {plan.excluded_vehicles.map((v) => `${v.vehicle_code} (${v.reason})`).join("; ")}</Notice>}
            {plan.conflicts && plan.conflicts.length > 0 && <Notice tone="warn">Conflicts: {plan.conflicts.map((c) => c.type.replace(/_/g, " ")).join("; ")}</Notice>}
            {plan.routes.length === 0 && <Empty title="No trips could be planned">Check that vehicles, eligible drivers, confirmed coordinates and open requests exist for this slot.</Empty>}
            {plan.routes.map((r, i) => (
              <div key={i} className="rounded-lg border border-line p-3">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <strong>{vName(r.vehicle_id)}</strong><span className="text-muted">· {dName(r.driver_id)}</span>
                  <Badge tone="info">{r.passengers}/{r.capacity} seats</Badge>{r.is_local_trip && <Badge tone="ok">local trip</Badge>}
                  <span className="ml-auto text-muted">{fmtKm(r.distance_km)} · {fmtMin(r.duration_minutes)}</span>
                </div>
                <ol className="mt-2 space-y-1 text-sm">
                  {r.stops.map((s) => <li key={s.request_id} className="flex justify-between gap-3"><span>{s.order + 1}. {names.get(s.employee_id) ?? s.employee_id.slice(0, 8)}</span><span className="text-muted">{s.eta_minutes != null ? `ETA +${s.eta_minutes} min` : "ETA n/a"}{s.ride_minutes != null ? ` · ride ${s.ride_minutes} min` : ""}</span></li>)}
                </ol>
                <details className="mt-2 text-sm"><summary className="cursor-pointer text-muted">Why this vehicle and driver</summary>
                  <p className="mt-1">{r.explanation.vehicle}.</p>
                  {notesOf(r.explanation.policy_notes).map((n) => <p key={n} className="text-muted">{n}</p>)}
                  <ul className="mt-1 space-y-0.5">{r.explanation.fairness.map((f) => <li key={f.driver_id}>#{f.rank} {f.name} — score {Number(f.score).toFixed(3)}{f.local_trip_note ? ` · ${f.local_trip_note}` : ""}</li>)}</ul>
                </details>
              </div>
            ))}
            {plan.unassigned && plan.unassigned.length > 0 && <Notice tone="bad"><strong>{plan.unassigned.length} employee(s) could not be assigned:</strong> {plan.unassigned.map((u) => `${u.name} (${u.reason.replace(/_/g, " ")})`).join("; ")}</Notice>}
          </div>
        </Card>
      )}

      <h2 className="mb-2 text-sm font-semibold">Allocations on {date}</h2>
      {list.isPending ? <Spinner /> : list.isError ? <ErrorBox error={list.error} retry={() => void list.refetch()} /> : list.data.items.length === 0 ? <Empty title="No allocations for this date" /> : (
        <>
          <DataTable caption="Allocations" rows={list.data.items} onRowClick={(a) => set("open", a.id)} columns={[
            { key: "vehicle_code", header: "Vehicle" }, { key: "driver_name", header: "Driver", render: (a) => a.driver_name ?? <span className="text-muted">unassigned</span> },
            { key: "direction", header: "Direction" }, { key: "passenger_count", header: "Passengers", render: (a) => `${a.passenger_count ?? 0}/${a.capacity_snapshot ?? "?"}` },
            { key: "planned_distance_km", header: "Distance", render: (a) => fmtKm(a.planned_distance_km) }, { key: "status", header: "Status", render: (a) => <StatusBadge status={a.status} /> }]} />
          <Pager page={page} size={list.data.page_size} total={list.data.total} onPage={setPage} />
        </>
      )}
      <Confirm open={confirmCommit} title="Save proposed allocations?" confirmLabel="Save proposals" busy={commit.isPending} onClose={() => setConfirmCommit(false)} onConfirm={() => commit.mutate()}
        message="This replaces any unpublished proposals for this slot. Drivers and employees are not notified until you send each trip and the driver accepts." />
      <Modal wide open={Boolean(open)} onClose={() => set("open", "")} title="Allocation">{open && <AllocationDetail id={open} />}</Modal>
    </>
  );
}
