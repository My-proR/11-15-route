import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, newKey } from "../lib/api";
import { useAction } from "../lib/hooks";
import { fmtDate, tomorrow } from "../lib/format";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, Spinner, StatusBadge } from "../components/ui";
import AllocationDetail from "./AllocationDetail";

interface Summary { service_date: string; routes: { id: string; code: string; name: string; shift_name: string | null; branch_name: string | null; cabs: number; cabs_available: number; trips: number; trips_needing_driver: number }[];
  unrouted_cabs: { id: string; vehicle_code: string; registration_number: string; capacity: number; availability: string }[] }
interface Trip { id: string; status: string; direction: string; shift_name: string; start_time: string; passengers: number; capacity: number; estimate_status: string;
  driver: { id: string; name: string; phone: string | null; available: boolean } | null; needs_driver: boolean }
interface Cab { id: string; vehicle_code: string; registration_number: string; capacity: number; availability: string; overridden_today: boolean;
  default_driver: { id: string; name: string; phone: string | null; available: boolean } | null; trips: Trip[] }
interface Detail { service_date: string; route: { id: string; code: string; name: string; shift_name: string | null; stops: { sequence: number; name: string; address: string | null }[] }; cabs: Cab[] }
interface Cand { driver_id: string; name: string; rank: number; score: number }

/** Routes -> cabs and their drivers -> trips for a date. Staff can assign or change the driver of any trip (the driver then accepts or declines in their app). */
export default function Fleet() {
  const [date, setDate] = useState(tomorrow());
  const [route, setRoute] = useState<string>("");
  const [open, setOpen] = useState<string>("");
  const sum = useQuery({ queryKey: ["fleet", date], queryFn: () => api<Summary>(`/fleet/routes?service_date=${date}`), refetchInterval: 30_000 });
  const det = useQuery({ queryKey: ["fleet-route", route, date], enabled: Boolean(route), queryFn: () => api<Detail>(`/fleet/routes/${route}?service_date=${date}`), refetchInterval: 30_000 });
  return (
    <>
      <PageHeader title="Routes & fleet" subtitle="See each route's cabs, their drivers and trips, and assign drivers." actions={<Field label="Date">{(id) => <Input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>} />
      {sum.isPending ? <Spinner /> : sum.isError ? <ErrorBox error={sum.error} retry={() => void sum.refetch()} /> : (
        <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
          <Card title={`Routes on ${fmtDate(date)}`}>
            {sum.data.routes.length === 0 ? <Empty title="No routes yet">Create routes under Routes, then link cabs to them in Vehicles.</Empty> : (
              <ul className="space-y-2">{sum.data.routes.map((r) => (
                <li key={r.id}><button type="button" onClick={() => setRoute(r.id)} aria-pressed={route === r.id} className={`w-full rounded-lg border px-3 py-2 text-left ${route === r.id ? "border-accent bg-surface2" : "border-line"}`}>
                  <span className="flex items-center justify-between gap-2"><strong>{r.code}</strong>{r.trips_needing_driver > 0 && <Badge tone="warn">{r.trips_needing_driver} need driver</Badge>}</span>
                  <span className="block text-sm text-muted">{r.name}{r.shift_name ? ` · ${r.shift_name}` : ""}</span>
                  <span className="block text-xs text-muted">{r.cabs} cab{r.cabs === 1 ? "" : "s"} ({r.cabs_available} available) · {r.trips} trip{r.trips === 1 ? "" : "s"}</span></button></li>))}</ul>)}
            {sum.data.unrouted_cabs.length > 0 && <div className="mt-4"><Notice tone="info">{sum.data.unrouted_cabs.length} cab{sum.data.unrouted_cabs.length === 1 ? " is" : "s are"} not linked to any route: {sum.data.unrouted_cabs.map((c) => c.vehicle_code).join(", ")}. Set a default route on the cab in Vehicles.</Notice></div>}
          </Card>
          <div>
            {!route ? <Card><Empty title="Choose a route">Its cabs, drivers and trips appear here.</Empty></Card> : det.isPending ? <Spinner /> : det.isError ? <ErrorBox error={det.error} retry={() => void det.refetch()} /> : (
              <div className="space-y-4">
                <Card title={`${det.data.route.code} · ${det.data.route.name}`}>
                  <p className="text-sm text-muted">{det.data.route.stops.length > 0 ? det.data.route.stops.map((s) => s.name).join(" → ") : "No stops defined yet."}</p>
                </Card>
                {det.data.cabs.length === 0 && <Card><Empty title="No cabs on this route for this date">Link cabs to the route in Vehicles, or move one for the day from the Vehicle board.</Empty></Card>}
                {det.data.cabs.map((c) => <CabCard key={c.id} cab={c} onOpen={setOpen} />)}
              </div>)}
          </div>
        </div>)}
      <Modal wide open={Boolean(open)} onClose={() => setOpen("")} title="Trip">{open && <AllocationDetail id={open} />}</Modal>
    </>
  );
}

function CabCard({ cab, onOpen }: { cab: Cab; onOpen: (id: string) => void }) {
  return (
    <Card title={`${cab.vehicle_code} · ${cab.registration_number}`}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge>{cab.capacity} seats</Badge><StatusBadge status={cab.availability} />{cab.overridden_today && <Badge tone="info">moved here for today</Badge>}
        <span className="text-muted">Regular driver: {cab.default_driver ? <>{cab.default_driver.name}{cab.default_driver.available ? "" : " (off duty)"}</> : "none set"}</span>
      </div>
      {cab.trips.length === 0 ? <p className="mt-3 text-sm text-muted">No trips for this cab on this date.</p> : (
        <ul className="mt-3 divide-y divide-line rounded-lg border border-line">{cab.trips.map((t) => <TripRow key={t.id} trip={t} onOpen={onOpen} />)}</ul>)}
    </Card>
  );
}

function TripRow({ trip, onOpen }: { trip: Trip; onOpen: (id: string) => void }) {
  const [assigning, setAssigning] = useState(false);
  const [pick, setPick] = useState("");
  const editable = ["PROPOSED", "PENDING_DRIVER_ACCEPTANCE", "DRIVER_ACCEPTED", "CONFIRMED", "REQUIRES_REASSIGNMENT"].includes(trip.status);
  const cands = useQuery({ queryKey: ["fleet-eligible", trip.id], enabled: assigning, queryFn: () => api<{ candidates: Cand[] }>(`/allocations/${trip.id}/eligible-drivers`) });
  const assign = useAction(() => api(`/allocations/${trip.id}/reassign-driver`, { method: "POST", idempotencyKey: newKey(), body: { driver_id: pick, emergency: false } }),
    { success: "Offer sent to the driver", invalidate: [["fleet"], ["fleet-route"], ["allocations"]], onSuccess: () => { setAssigning(false); setPick(""); } });
  return (
    <li className="space-y-2 px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex flex-wrap items-center gap-2"><strong>{trip.start_time.slice(0, 5)} {trip.direction}</strong><StatusBadge status={trip.status} /><span className="text-muted">{trip.passengers}/{trip.capacity} passengers</span>
          {trip.estimate_status !== "final" && <Badge tone="warn">provisional route</Badge>}</span>
        <span className="flex items-center gap-2">
          <span>{trip.driver ? <>{trip.driver.name}{trip.driver.available ? "" : " (off duty)"}</> : <em className="text-warn">No driver</em>}</span>
          {editable && <Button variant="secondary" className="min-h-9" onClick={() => setAssigning((v) => !v)}>{trip.driver ? "Change driver" : "Assign driver"}</Button>}
          <Button variant="ghost" className="min-h-9" onClick={() => onOpen(trip.id)}>Details</Button>
        </span>
      </div>
      {assigning && (
        <div className="flex flex-wrap items-end gap-2 rounded-lg bg-surface2 p-2">
          {cands.isPending ? <Spinner /> : cands.isError ? <span className="text-bad">Couldn't load drivers.</span> : cands.data.candidates.length === 0 ? <span className="text-muted">No eligible available drivers for this slot.</span> : (
            <>
              <Field label="Driver (fairest first)">{(id) => <Select id={id} value={pick} onChange={(e) => setPick(e.target.value)}><option value="">Choose…</option>{cands.data.candidates.map((c) => <option key={c.driver_id} value={c.driver_id}>{c.rank}. {c.name}</option>)}</Select>}</Field>
              <Button disabled={!pick} busy={assign.isPending} onClick={() => assign.mutate()}>Send offer</Button>
            </>)}
          <Button variant="ghost" onClick={() => setAssigning(false)}>Close</Button>
        </div>)}
    </li>
  );
}
