import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Siren, MapPin } from "lucide-react";
import { api, errMsg, newKey } from "../lib/api";
import { useAction, useFeatures, useMe } from "../lib/hooks";
import { fmtDate, fmtDateTime, tomorrow } from "../lib/format";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, Spinner, StatusBadge } from "../components/ui";
import { useLookup } from "../components/ResourcePage";
import { useRealtime } from "../lib/realtime";
import { LeavePage } from "./Operations";
import { TripQrModal, ScanToBoard } from "../components/TripQr";
import { useT } from "../lib/i18n";
import { AvailabilityToggle, BoardingCodeCard, BoardingCodeModal } from "../components/TripCredentials";

interface Trip { id: string; service_date: string; direction: string; status: string; shift_name: string; start_time: string; end_time: string; planned_duration_minutes: number | null;
  crew: { driver_name: string | null; driver_phone: string | null; vehicle_code: string; registration_number: string } | null;
  passengers?: { passenger_id: string; name: string; phone: string | null; address: string; stop_order: number; status: string; needs: string[] }[]; assignment?: { id: string; status: string; expires_at: string | null } | null; requires_boarding_code?: boolean }
interface Req { id: string; service_date: string; direction: string; status: string }

export default function Portal() {
  const { data: me } = useMe();
  return me?.role === "driver" ? <DriverHome /> : <EmployeeHome />;
}

function SosButton() {
  const L = useT();
  return <a href="/me/sos" className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-bad px-4 text-sm font-semibold text-white"><Siren className="size-4" />{L("sos")}</a>;
}

// ---- Employee ----------------------------------------------------------------------------------------------------
function EmployeeHome() {
  const L = useT();
  const trips = useQuery({ queryKey: ["my-trips"], queryFn: () => api<Trip[]>("/trips/mine"), refetchInterval: 30_000 });
  const reqs = useQuery({ queryKey: ["my-requests"], queryFn: () => api<Req[]>("/me/transport-requests") });
  const features = useFeatures();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ service_date: tomorrow(), direction: "pickup", shift_id: "" });
  const shifts = useLookup("shifts");
  const create = useAction(() => api("/me/transport-requests", { method: "POST", body: { service_date: f.service_date, direction: f.direction, ...(f.shift_id ? { shift_id: f.shift_id } : {}) } }), { success: "Request submitted", invalidate: [["my-requests"]], onSuccess: () => setOpen(false) });
  const cancel = useAction((id: string) => api(`/me/transport-requests/${id}`, { method: "DELETE" }), { success: "Request cancelled", invalidate: [["my-requests"]] });
  const track = useQuery({ queryKey: ["my-trip-track"], enabled: Boolean(features.data?.live_tracking.effective), refetchInterval: 20_000, queryFn: () => api<{ enabled: boolean; position: { latitude: number; longitude: number; captured_at: string } | null }>("/tracking/my-trip") });
  useRealtime((e) => { if (e.startsWith("trip.") || e === "allocation.updated") void trips.refetch(); });
  return (
    <>
      <PageHeader title={L("myTransport")} actions={<><SosButton /><Button onClick={() => setOpen(true)}>{L("requestRide")}</Button></>} />
      <div className="space-y-5">
        <Card title={L("upcoming")}>
          {trips.isPending ? <Spinner /> : trips.isError ? <ErrorBox error={trips.error} /> : trips.data.length === 0 ? <Empty title={L("noTrips")}>{L("noTripsHint")}</Empty> : (
            <ul className="space-y-3">{trips.data.map((t) => (
              <li key={t.id} className="rounded-lg border border-line p-3"><div className="flex flex-wrap items-center gap-2"><strong>{fmtDate(t.service_date)}</strong><Badge>{t.direction}</Badge><StatusBadge status={t.status} /><span className="text-sm text-muted">{t.shift_name}</span></div>
                {t.crew && <p className="mt-1 text-sm">Cab <strong>{t.crew.vehicle_code}</strong> ({t.crew.registration_number}) · Driver {t.crew.driver_name ?? "to be assigned"}{t.crew.driver_phone ? ` · ${t.crew.driver_phone}` : ""}</p>}
                {["CONFIRMED", "IN_PROGRESS"].includes(t.status) && <BoardingCodeCard allocationId={t.id} />}
                {t.status === "IN_PROGRESS" && <ScanToBoard allocationId={t.id} />}</li>))}</ul>)}
          {track.data?.enabled && <div className="mt-3 text-sm">{track.data.position ? <span className="inline-flex items-center gap-1"><MapPin className="size-4" />Your cab was last seen {fmtDateTime(track.data.position.captured_at)} at {track.data.position.latitude.toFixed(4)}, {track.data.position.longitude.toFixed(4)}.</span> : <span className="text-muted">Live cab location appears once your trip starts.</span>}</div>}
        </Card>
        <Card title={L("myRequests")}>
          {reqs.isPending ? <Spinner /> : reqs.isError ? <ErrorBox error={reqs.error} /> : reqs.data.length === 0 ? <Empty title="No requests" /> : (
            <ul className="divide-y divide-line">{reqs.data.map((r) => <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-sm"><span>{fmtDate(r.service_date)} · {r.direction}</span><span className="flex items-center gap-2"><StatusBadge status={r.status} />{r.status === "open" && <Button variant="ghost" className="min-h-8 px-2 text-bad" onClick={() => cancel.mutate(r.id)}>{L("cancel")}</Button>}</span></li>)}</ul>)}
        </Card>
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title={L("requestRide")}><div className="space-y-3">
        <Field label={L("date")}>{(id) => <Input id={id} type="date" min={tomorrow()} value={f.service_date} onChange={(e) => setF({ ...f, service_date: e.target.value })} />}</Field>
        <Field label="Direction">{(id) => <Select id={id} value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })}><option value="pickup">{L("pickup")}</option><option value="drop">{L("drop")}</option></Select>}</Field>
        <Field label="Shift" hint="Leave blank to use your default shift.">{(id) => <Select id={id} value={f.shift_id} onChange={(e) => setF({ ...f, shift_id: e.target.value })}><option value="">My default shift</option>{shifts.data?.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</Select>}</Field>
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setOpen(false)}>{L("cancel")}</Button><Button busy={create.isPending} onClick={() => create.mutate()}>{L("submit")}</Button></div></div></Modal>
    </>
  );
}

// ---- Driver ---------------------------------------------------------------------------------------------------------
function DriverHome() {
  const L = useT();
  const trips = useQuery({ queryKey: ["my-trips"], queryFn: () => api<Trip[]>("/trips/mine"), refetchInterval: 20_000 });
  const features = useFeatures();
  const tracking = useQuery({ queryKey: ["tracking-status"], queryFn: () => api<{ enabled: boolean; interval_seconds: number | null }>("/tracking/status"), refetchInterval: 60_000 });
  const inv = [["my-trips"]];
  const respond = useAction((v: { id: string; accept: boolean; reason?: string }) => api(`/driver-assignments/${v.id}/respond`, { method: "POST", idempotencyKey: newKey(), body: { accept: v.accept, reason: v.reason ?? null } }), { success: "Response sent", invalidate: inv });
  const [codeFor, setCodeFor] = useState<{ alloc: string; pid: string; name: string } | null>(null);
  const [qrFor, setQrFor] = useState<string | null>(null);
  const start = useAction((id: string) => api(`/allocations/${id}/start`, { method: "POST" }), { success: "Trip started", invalidate: inv });
  const complete = useAction((id: string) => api(`/allocations/${id}/complete`, { method: "POST" }), { success: "Trip completed", invalidate: inv });
  const pax = useAction((v: { alloc: string; pid: string; status: string; code?: string }) => api(`/allocations/${v.alloc}/passengers/${v.pid}/status`, { method: "POST", body: { status: v.status, ...(v.code ? { code: v.code } : {}) } }), { invalidate: inv, onSuccess: () => setCodeFor(null) });
  useRealtime((e) => { if (e.startsWith("assignment.") || e.startsWith("trip.")) void trips.refetch(); });
  const active = trips.data?.find((t) => t.status === "IN_PROGRESS");
  return (
    <>
      <PageHeader title={L("myTrips")} actions={<SosButton />} />
      <AvailabilityToggle />
      {features.data && <div className="mb-4"><TrackingReporter allocationId={active?.id} enabled={Boolean(tracking.data?.enabled)} interval={tracking.data?.interval_seconds ?? 30} /></div>}
      {trips.isPending ? <Spinner /> : trips.isError ? <ErrorBox error={trips.error} retry={() => void trips.refetch()} /> : trips.data.length === 0 ? <Empty title={L("noAssigned")}>{L("noAssignedHint")}</Empty> : (
        <ul className="space-y-4">{trips.data.map((t) => (
          <li key={t.id} className="rounded-xl border border-line bg-surface p-4">
            <div className="flex flex-wrap items-center gap-2"><strong>{fmtDate(t.service_date)}</strong><Badge>{t.direction}</Badge><StatusBadge status={t.status} /><span className="text-sm text-muted">{t.shift_name} {t.start_time.slice(0, 5)}–{t.end_time.slice(0, 5)}</span></div>
            {t.crew && <p className="mt-1 text-sm text-muted">Vehicle {t.crew.vehicle_code} ({t.crew.registration_number})</p>}
            {t.assignment?.status === "PENDING" && <div className="mt-3 space-y-2"><Notice tone="warn">Please respond{t.assignment.expires_at ? ` before ${fmtDateTime(t.assignment.expires_at)}` : ""}.</Notice>
              <div className="flex gap-2"><Button busy={respond.isPending} onClick={() => respond.mutate({ id: t.assignment!.id, accept: true })}>{L("accept")}</Button><Button variant="danger" busy={respond.isPending} onClick={() => respond.mutate({ id: t.assignment!.id, accept: false, reason: "Unavailable" })}>{L("decline")}</Button></div></div>}
            {t.passengers && t.passengers.length > 0 && <ol className="mt-3 space-y-1 text-sm">{t.passengers.map((p) => <li key={p.passenger_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-surface2 px-3 py-2"><span>{p.stop_order + 1}. <strong>{p.name}</strong> <span className="text-muted">{p.address}</span> {p.needs.length > 0 && <Badge tone="info">{p.needs.join(", ")}</Badge>}</span>
              <span className="flex items-center gap-2">{p.phone && <a className="underline" href={`tel:${p.phone}`}>{p.phone}</a>}{t.status === "IN_PROGRESS" && <Select aria-label={`Status for ${p.name}`} className="min-h-9 w-32" value={["boarded", "dropped", "no_show"].includes(p.status) ? p.status : ""} onChange={(e) => { const v = e.target.value; if (!v) return; if (v === "boarded" && t.requires_boarding_code) setCodeFor({ alloc: t.id, pid: p.passenger_id, name: p.name }); else pax.mutate({ alloc: t.id, pid: p.passenger_id, status: v }); }}><option value="">Not boarded</option>{["boarded", "dropped", "no_show"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}</Select>}</span></li>)}</ol>}
            <div className="mt-3 flex justify-end gap-2">{t.status === "CONFIRMED" && <Button busy={start.isPending} onClick={() => start.mutate(t.id)}>{L("startTrip")}</Button>}{t.status === "IN_PROGRESS" && <Button variant="secondary" onClick={() => setQrFor(t.id)}>{L("showQr")}</Button>}{t.status === "IN_PROGRESS" && <Button busy={complete.isPending} onClick={() => complete.mutate(t.id)}>{L("completeTrip")}</Button>}</div>
          </li>))}</ul>)}
      {qrFor && <TripQrModal allocationId={qrFor} open onClose={() => setQrFor(null)} />}
      <BoardingCodeModal name={codeFor?.name ?? ""} open={codeFor !== null} onClose={() => setCodeFor(null)} busy={pax.isPending} onSubmit={(code) => codeFor && pax.mutate({ alloc: codeFor.alloc, pid: codeFor.pid, status: "boarded", code })} />
      <div className="mt-8"><LeavePage /></div>
    </>
  );
}

/** Shares the vehicle position ONLY when the server says tracking is effective AND a trip is in progress. Nothing is requested otherwise. */
function TrackingReporter({ allocationId, enabled, interval }: { allocationId?: string; enabled: boolean; interval: number }) {
  const [state, setState] = useState<"idle" | "sharing" | "denied" | "error">("idle");
  const last = useRef(0);
  const active = enabled && Boolean(allocationId);
  useEffect(() => {
    if (!active || !allocationId || !("geolocation" in navigator)) { setState("idle"); return; }
    const id = navigator.geolocation.watchPosition(
      (pos) => {
        const now = Date.now();
        if (now - last.current < interval * 1000) return;
        last.current = now;
        void api("/tracking/position", { method: "POST", body: { allocation_id: allocationId, latitude: pos.coords.latitude, longitude: pos.coords.longitude, accuracy_m: pos.coords.accuracy, speed_kmh: pos.coords.speed != null ? pos.coords.speed * 3.6 : null, heading: pos.coords.heading ?? null, captured_at: new Date(pos.timestamp).toISOString() } })
          .then(() => setState("sharing")).catch(() => setState("error"));
      },
      (err) => setState(err.code === err.PERMISSION_DENIED ? "denied" : "error"), { enableHighAccuracy: true, maximumAge: 10_000, timeout: 20_000 });
    return () => navigator.geolocation.clearWatch(id);
  }, [active, allocationId, interval]);
  if (!enabled) return null;
  if (!allocationId) return <Notice tone="info">Location sharing is enabled by your organization. It runs only while a trip is in progress.</Notice>;
  return <Notice tone={state === "sharing" ? "ok" : state === "idle" ? "info" : "warn"}>{state === "sharing" ? "Sharing this vehicle's location while the trip is in progress." : state === "denied" ? "Location permission was denied. The trip still works; your dispatcher just can't see the vehicle." : state === "error" ? "Couldn't send location right now; will retry." : "Starting location sharing for this trip…"}</Notice>;
}

// ---- SOS ----------------------------------------------------------------------------------------------------------------
export function SosPage() {
  const features = useFeatures();
  const gps = features.data?.sos_gps.effective ?? false;
  const key = useMemo(() => newKey(), []);          // one key per screen visit: retries can never create duplicate alerts
  const [stage, setStage] = useState<"idle" | "explain" | "sending" | "sent" | "failed">("idle");
  const [result, setResult] = useState<string>("");
  const [message, setMessage] = useState("");
  const [err, setErr] = useState("");
  const incident = useRef<string | null>(null);

  const raise = async (askLocation: boolean) => {
    setStage("sending"); setErr("");
    try {
      const inc = await api<{ id: string }>("/sos", { method: "POST", idempotencyKey: key, body: { message } });   // the alert goes first, without waiting for GPS
      incident.current = inc.id;
      setResult("Your alert was sent. Help has been notified.");
      setStage("sent");
      if (askLocation) await attachLocation(inc.id);
    } catch (e) { setErr(errMsg(e)); setStage("failed"); }
  };
  const attachLocation = (id: string) => new Promise<void>((resolve) => {
    if (!("geolocation" in navigator)) { void put(id, { status: "unavailable" }).finally(resolve); return; }
    navigator.geolocation.getCurrentPosition(
      (p) => void put(id, { status: "obtained", latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy_m: p.coords.accuracy, captured_at: new Date(p.timestamp).toISOString() }).finally(resolve),
      (e) => void put(id, { status: e.code === e.PERMISSION_DENIED ? "denied" : "unavailable" }).finally(resolve), { enableHighAccuracy: true, timeout: 15_000, maximumAge: 5_000 });
  });
  const put = async (id: string, body: Record<string, unknown>) => {
    try { const r = await api<{ location_status: string }>(`/sos/${id}/location`, { method: "PUT", body }); setResult(r.location_status === "obtained" ? "Your alert was sent with your location." : "Your alert was sent. Your location could not be shared."); }
    catch { setResult("Your alert was sent. Your location could not be shared."); }
  };

  return (
    <div className="mx-auto max-w-md space-y-5">
      <PageHeader title="Emergency SOS" />
      {stage === "sent" ? <Notice tone="ok">{result}</Notice> : (
        <>
          <p className="text-sm text-muted">This alerts your transport team immediately. {gps ? "With your permission your current location is attached to help them find you." : "Your location is not collected."}</p>
          <Field label="What's happening? (optional)">{(id) => <Input id={id} maxLength={500} value={message} onChange={(e) => setMessage(e.target.value)} />}</Field>
          {err && <ErrorBox error={new Error(err)} />}
          <button disabled={stage === "sending"} onClick={() => (gps ? setStage("explain") : void raise(false))} className="grid h-40 w-full place-items-center rounded-3xl bg-bad text-2xl font-bold text-white shadow-lg active:scale-[.99] disabled:opacity-60">{stage === "sending" ? "Sending…" : stage === "failed" ? "Try again" : "SEND SOS"}</button>
        </>)}
      <Modal open={stage === "explain"} onClose={() => setStage("idle")} title="Share your location?">
        <div className="space-y-4 text-sm"><p>Your organization allows attaching your location to an SOS. Your browser will ask for permission. We use it only for this alert; it's visible to your dispatchers and administrators and deleted automatically after the retention period.</p>
          <p className="text-muted">Your alert is sent right away either way, even if you decline or location is unavailable.</p>
          <div className="flex flex-col gap-2 sm:flex-row sm:justify-end"><Button variant="secondary" onClick={() => void raise(false)}>Send without location</Button><Button onClick={() => void raise(true)}>Send with my location</Button></div></div>
      </Modal>
    </div>
  );
}
