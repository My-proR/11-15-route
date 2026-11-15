import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, newKey } from "../lib/api";
import { useAction, useMe } from "../lib/hooks";
import { fmtDateTime, fmtKm, fmtMin } from "../lib/format";
import { Badge, Button, Confirm, ErrorBox, Field, Input, Notice, Select, Spinner, StatusBadge } from "../components/ui";
import type { Allocation } from "../lib/types";

interface Passenger { passenger_id: string; employee_id: string; name: string; phone: string | null; stop_order: number; address: string; needs: string[]; status: string; eta_minutes: number | null }
interface Assignment { id: string; driver_id: string; driver_name: string; status: string; expires_at: string | null; replacement_for_leave_id: string | null }
interface Detail extends Allocation { passengers: Passenger[]; assignments: Assignment[]; cancelled_reason?: string | null; explanation?: { policy_notes?: string[] } }
interface Candidate { driver_id: string; name: string; rank: number; score: number; local_trip_note?: string; components: Record<string, { value?: number; weighted?: number }> }

export default function AllocationDetail({ id }: { id: string }) {
  const { data: me } = useMe();
  const q = useQuery({ queryKey: ["allocation", id], queryFn: () => api<Detail>(`/allocations/${id}`), refetchInterval: 15_000 });
  const [cancelOpen, setCancelOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [driverPick, setDriverPick] = useState("");
  const [emergency, setEmergency] = useState(false);
  const inv = [["allocation", id], ["allocations"], ["vehicle-board"], ["exceptions"]];
  const send = useAction(() => api(`/allocations/${id}/send`, { method: "POST" }), { success: "Sent to driver", invalidate: inv });
  const confirm = useAction(() => api(`/allocations/${id}/confirm`, { method: "POST", idempotencyKey: newKey() }), { success: "Confirmed — passengers notified", invalidate: inv });
  const cancel = useAction(() => api(`/allocations/${id}/cancel`, { method: "POST", body: { reason } }), { success: "Trip cancelled", invalidate: inv, onSuccess: () => setCancelOpen(false) });
  const reassign = useAction(() => api(`/allocations/${id}/reassign-driver`, { method: "POST", body: { driver_id: driverPick, emergency } }), { success: "Offer sent to the new driver", invalidate: inv });
  const needsDriver = q.data && ["REQUIRES_REASSIGNMENT", "PROPOSED", "PENDING_DRIVER_ACCEPTANCE", "DRIVER_ACCEPTED", "CONFIRMED"].includes(q.data.status);
  const cands = useQuery({ queryKey: ["eligible", id, q.data?.status], enabled: Boolean(needsDriver), queryFn: () => api<{ candidates: Candidate[] }>(`/allocations/${id}/eligible-drivers`) });
  const canStaff = me?.role === "org_admin" || me?.role === "dispatcher";
  const [boardReason, setBoardReason] = useState<{ pid: string; name: string } | null>(null);
  const [why, setWhy] = useState("");
  const manual = useAction((pid: string) => api(`/allocations/${id}/passengers/${pid}/manual-board`, { method: "POST", body: { reason: why } }), { success: "Marked as boarded (audited)", invalidate: inv, onSuccess: () => { setBoardReason(null); setWhy(""); } });

  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => void q.refetch()} />;
  const a = q.data;
  const pending = a.assignments.find((x) => x.status === "PENDING");
  const estimateFinal = a.estimate_status === "final";
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2"><StatusBadge status={a.status} /><Badge>{a.direction}</Badge>{a.is_local_trip && <Badge tone="ok">local trip</Badge>}
        <span className="ml-auto text-sm text-muted">{a.vehicle_code} · {a.driver_name ?? "no driver"} · {fmtKm(a.planned_distance_km)} · {fmtMin(a.planned_duration_minutes)}</span></div>
      {!estimateFinal && <Notice tone="warn">Road distance/time estimate is <strong>{a.estimate_status}</strong>. This trip cannot be sent or confirmed until the plan is re-run with the routing service available.</Notice>}
      {a.status === "REQUIRES_REASSIGNMENT" && <Notice tone="bad">This trip needs a new driver. Choose an eligible driver below.</Notice>}
      {pending && <Notice tone="info">Waiting for {pending.driver_name} to respond{pending.expires_at ? ` until ${fmtDateTime(pending.expires_at)}` : ""}. Passengers have not been notified.</Notice>}

      <section><h3 className="mb-1 text-sm font-semibold">Passengers ({a.passengers.length}/{a.capacity_snapshot})</h3>
        <ul className="divide-y divide-line rounded-lg border border-line text-sm">
          {a.passengers.map((p) => <li key={p.passenger_id} className="flex flex-wrap justify-between gap-2 px-3 py-2"><span>{p.stop_order + 1}. <strong>{p.name}</strong> <span className="text-muted">{p.address}</span></span>
            <span className="text-muted">{p.eta_minutes != null ? `ETA +${Number(p.eta_minutes).toFixed(0)} min` : ""} {p.needs.length > 0 && <Badge tone="info">{p.needs.join(", ")}</Badge>} {p.phone && <span>{p.phone}</span>} {canStaff && a.status === "IN_PROGRESS" && p.status !== "boarded" && <Button variant="ghost" className="min-h-8 px-2" onClick={() => setBoardReason({ pid: p.passenger_id, name: p.name })}>Board manually</Button>}</span></li>)}
          {a.passengers.length === 0 && <li className="px-3 py-2 text-muted">No passengers</li>}
        </ul></section>

      {a.assignments.length > 0 && <section><h3 className="mb-1 text-sm font-semibold">Driver offers</h3>
        <ul className="space-y-1 text-sm">{a.assignments.map((x) => <li key={x.id} className="flex items-center gap-2"><StatusBadge status={x.status.toLowerCase()} />{x.driver_name}{x.replacement_for_leave_id && <Badge tone="warn">leave replacement</Badge>}</li>)}</ul></section>}

      {canStaff && needsDriver && a.status !== "IN_PROGRESS" && (
        <section className="space-y-2 rounded-lg border border-line p-3"><h3 className="text-sm font-semibold">Change driver</h3>
          <p className="text-xs text-muted">Fairest eligible driver first. Drivers on leave, with expired licences or conflicting trips are excluded.</p>
          {cands.isPending ? <Spinner /> : cands.isError ? <ErrorBox error={cands.error} /> : (
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Driver">{(i) => <Select id={i} value={driverPick} onChange={(e) => setDriverPick(e.target.value)}><option value="">Select…</option>{cands.data.candidates.map((c) => <option key={c.driver_id} value={c.driver_id}>#{c.rank} {c.name} (score {Number(c.score).toFixed(2)}){c.local_trip_note ? ` — ${c.local_trip_note}` : ""}</option>)}</Select>}</Field>
              <label className="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" className="size-4" checked={emergency} onChange={(e) => setEmergency(e.target.checked)} />Emergency (5-min response)</label>
              <Button variant="secondary" disabled={!driverPick} busy={reassign.isPending} onClick={() => reassign.mutate()}>Offer to driver</Button>
            </div>)}
        </section>)}

      {canStaff && (
        <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-4">
          {["PROPOSED", "REQUIRES_REASSIGNMENT"].includes(a.status) && <Button disabled={!estimateFinal || !a.driver_id} busy={send.isPending} onClick={() => send.mutate()}>Send to driver</Button>}
          {a.status === "DRIVER_ACCEPTED" && <Button disabled={!estimateFinal} busy={confirm.isPending} onClick={() => confirm.mutate()}>Confirm &amp; notify passengers</Button>}
          {!["COMPLETED", "CANCELLED"].includes(a.status) && <Button variant="danger" onClick={() => setCancelOpen(true)}>Cancel trip</Button>}
        </div>)}
      <Confirm open={boardReason !== null} title={`Board ${boardReason?.name ?? ""} manually?`} confirmLabel="Mark boarded" busy={manual.isPending} onClose={() => setBoardReason(null)} onConfirm={() => boardReason && why.trim().length >= 3 && manual.mutate(boardReason.pid)}
        message={<Field label="Reason (required, audited)">{(fid) => <Input id={fid} value={why} onChange={(e) => setWhy(e.target.value)} />}</Field>} />
      <Confirm open={cancelOpen} danger title="Cancel this trip?" confirmLabel="Cancel trip" busy={cancel.isPending} onClose={() => setCancelOpen(false)} onConfirm={() => reason.trim().length >= 3 && cancel.mutate()}
        message={<div className="space-y-2"><p>Passengers become unassigned again. Provide a reason (min 3 characters).</p><Input aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} /></div>} />
    </div>
  );
}
