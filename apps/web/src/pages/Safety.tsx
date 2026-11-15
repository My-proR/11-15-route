import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { api, buildQuery } from "../lib/api";
import { useAction, useFeatures } from "../lib/hooks";
import { fmtDateTime } from "../lib/format";
import type { Page } from "../lib/types";
import { Badge, Button, DataTable, Empty, ErrorBox, Input, Modal, Notice, PageHeader, Pager, Select, Spinner, StatusBadge, Confirm } from "../components/ui";
import { FeatureNotice } from "../components/FeatureNotice";
import { useRealtime } from "../lib/realtime";
import { useMe } from "../lib/hooks";

const OSM_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" } },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

function MapView({ points, center }: { points: { id: string; lat: number; lon: number; label: string; stale?: boolean }[]; center?: [number, number] }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markers = useRef(new Map<string, maplibregl.Marker>());
  useEffect(() => {
    if (!el.current) return;
    map.current = new maplibregl.Map({ container: el.current, style: OSM_STYLE, center: center ?? [80.27, 13.08], zoom: 11 });
    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    const m = markers.current;
    return () => { m.clear(); map.current?.remove(); map.current = null; };
  }, [center]);
  useEffect(() => {
    const m = map.current; if (!m) return;
    const seen = new Set<string>();
    for (const p of points) {
      seen.add(p.id);
      const ex = markers.current.get(p.id);
      if (ex) { ex.setLngLat([p.lon, p.lat]); ex.getPopup()?.setText(p.label); continue; }
      const dot = document.createElement("div");
      dot.style.cssText = `width:18px;height:18px;border-radius:50%;background:${p.stale ? "#9ca3af" : "#4f46e5"};border:3px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4)`;
      dot.setAttribute("aria-label", p.label);
      markers.current.set(p.id, new maplibregl.Marker({ element: dot }).setLngLat([p.lon, p.lat]).setPopup(new maplibregl.Popup({ offset: 12 }).setText(p.label)).addTo(m));
    }
    for (const [id, mk] of markers.current) if (!seen.has(id)) { mk.remove(); markers.current.delete(id); }
    if (points.length === 1 && points[0]) m.easeTo({ center: [points[0].lon, points[0].lat], duration: 400 });
  }, [points]);
  return <div ref={el} role="img" aria-label="Map" className="h-96 w-full overflow-hidden rounded-xl border border-line" />;
}

// ---- SOS -----------------------------------------------------------------------------------------------------
interface Incident { id: string; status: string; location_status: string; message: string; raised_by_role: string; raised_by_name?: string; created_at: string; acknowledged_at: string | null; resolved_at: string | null }
interface IncidentDetail extends Incident { location: { latitude: number; longitude: number; accuracy_m: number | null; captured_at: string; purge_after: string; accuracy_poor: boolean } | null }
const locLabel: Record<string, string> = { disabled: "GPS sharing is off", denied: "Permission denied on the device", unavailable: "Location unavailable", obtained: "Location shared", not_requested: "No location sent" };

export function SosIncidents() {
  const { data: me } = useMe();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const features = useFeatures();
  const q = useQuery({ queryKey: ["sos", page, status], queryFn: () => api<Page<Incident>>("/sos" + buildQuery({ page, status })), refetchInterval: 15_000, placeholderData: (p) => p });
  useRealtime((evt) => { if (evt === "sos.created" || evt === "sos.updated") void q.refetch(); });
  return (
    <>
      <PageHeader title="SOS incidents" subtitle="Alerts always arrive. A location appears only when GPS sharing is enabled and the person allowed it." />
      <div className="mb-3 space-y-3"><FeatureNotice name="SOS GPS location" state={features.data?.sos_gps} />
        <div className="max-w-xs"><Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}><option value="">All</option><option value="raised">Raised</option><option value="acknowledged">Acknowledged</option><option value="resolved">Resolved</option></Select></div></div>
      {q.isPending ? <Spinner /> : q.isError ? <ErrorBox error={q.error} retry={() => void q.refetch()} /> : q.data.items.length === 0 ? <Empty title="No SOS incidents" /> : (
        <><DataTable caption="SOS incidents" rows={q.data.items} onRowClick={(r) => setOpen(r.id)} columns={[
          { key: "created_at", header: "Raised", render: (r) => fmtDateTime(r.created_at) }, { key: "raised_by_name", header: "By", render: (r) => `${r.raised_by_name ?? "—"} (${r.raised_by_role})` },
          { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> }, { key: "location_status", header: "Location", render: (r) => <Badge tone={r.location_status === "obtained" ? "ok" : "neutral"}>{locLabel[r.location_status] ?? r.location_status}</Badge> },
          { key: "message", header: "Message" }]} /><Pager page={page} size={q.data.page_size} total={q.data.total} onPage={setPage} /></>)}
      <Modal wide open={Boolean(open)} onClose={() => setOpen(null)} title="SOS incident">{open && <IncidentPanel id={open} canDelete={me?.role === "org_admin"} />}</Modal>
    </>
  );
}

function IncidentPanel({ id, canDelete }: { id: string; canDelete: boolean }) {
  const q = useQuery({ queryKey: ["sos", id], queryFn: () => api<IncidentDetail>(`/sos/${id}`), refetchInterval: 10_000 });
  const [note, setNote] = useState("");
  const [del, setDel] = useState(false);
  const inv = [["sos", id], ["sos"], ["overview"]];
  const ack = useAction(() => api(`/sos/${id}/acknowledge`, { method: "POST" }), { success: "Acknowledged — the reporter was notified", invalidate: inv });
  const resolve = useAction(() => api(`/sos/${id}/resolve`, { method: "POST", body: { note: note || null } }), { success: "Resolved", invalidate: inv });
  const purge = useAction(() => api(`/sos/${id}/location`, { method: "DELETE" }), { success: "Location data deleted", invalidate: inv, onSuccess: () => setDel(false) });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => void q.refetch()} />;
  const i = q.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2"><StatusBadge status={i.status} /><span className="text-sm text-muted">{fmtDateTime(i.created_at)} · {i.raised_by_role}</span></div>
      {i.message && <p className="rounded-lg bg-surface2 p-3 text-sm">“{i.message}”</p>}
      {i.location ? (
        <div className="space-y-2">
          {i.location.accuracy_poor && <Notice tone="warn">Poor GPS accuracy (±{Math.round(i.location.accuracy_m ?? 0)} m). Treat the position as approximate.</Notice>}
          <MapView center={[i.location.longitude, i.location.latitude]} points={[{ id: "sos", lat: i.location.latitude, lon: i.location.longitude, label: "SOS location" }]} />
          <p className="text-xs text-muted">Captured {fmtDateTime(i.location.captured_at)} · accuracy {i.location.accuracy_m != null ? `±${Math.round(i.location.accuracy_m)} m` : "unknown"} · auto-deleted {fmtDateTime(i.location.purge_after)}. Viewing this location is audit-logged.</p>
        </div>
      ) : <Notice tone="info">{locLabel[i.location_status] ?? i.location_status}. No coordinates are stored for this incident.</Notice>}
      <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-4">
        {canDelete && i.location && <Button variant="secondary" onClick={() => setDel(true)}>Delete location now</Button>}
        {i.status === "raised" && <Button busy={ack.isPending} onClick={() => ack.mutate()}>Acknowledge</Button>}
        {i.status !== "resolved" && <><Input aria-label="Resolution note" className="max-w-56" placeholder="Resolution note (optional)" value={note} onChange={(e) => setNote(e.target.value)} /><Button variant="secondary" busy={resolve.isPending} onClick={() => resolve.mutate()}>Resolve</Button></>}
      </div>
      <Confirm open={del} danger title="Delete stored location?" confirmLabel="Delete" busy={purge.isPending} onClose={() => setDel(false)} onConfirm={() => purge.mutate()} message="The coordinates are removed permanently. The incident record stays." />
    </div>
  );
}

// ---- Live tracking ----------------------------------------------------------------------------------------
interface Veh { vehicle_id: string; vehicle_code: string; registration_number: string; driver_name: string | null; latitude: number; longitude: number; speed_kmh: number | null; captured_at: string; stale: boolean }
export function LiveTracking() {
  const features = useFeatures();
  const t = features.data?.live_tracking;
  const q = useQuery({ queryKey: ["tracking"], enabled: Boolean(t?.effective), queryFn: () => api<{ enabled: boolean; vehicles: Veh[] }>("/tracking/vehicles"), refetchInterval: 20_000 });
  const [live, setLive] = useState<Record<string, Veh>>({});
  useRealtime((evt, data) => {
    if (evt !== "tracking.position") return;
    const d = data as { vehicle_id: string; latitude: number; longitude: number; speed_kmh: number | null; captured_at: string };
    setLive((p) => ({ ...p, [d.vehicle_id]: { ...(p[d.vehicle_id] ?? q.data?.vehicles.find((v) => v.vehicle_id === d.vehicle_id) ?? { vehicle_code: d.vehicle_id.slice(0, 6), registration_number: "", driver_name: null }), ...d, stale: false } as Veh }));
  });
  if (features.isPending) return <Spinner />;
  if (!t?.effective) return <><PageHeader title="Live tracking" /><FeatureNotice name="Live vehicle tracking" state={t} /></>;
  const vehicles = Object.values({ ...Object.fromEntries((q.data?.vehicles ?? []).map((v) => [v.vehicle_id, v])), ...live });
  return (
    <>
      <PageHeader title="Live tracking" subtitle="Only vehicles on an in-progress trip appear. Positions are not stored as history unless your organization enabled that." />
      <MapView points={vehicles.map((v) => ({ id: v.vehicle_id, lat: v.latitude, lon: v.longitude, stale: v.stale, label: `${v.vehicle_code} · ${v.driver_name ?? "driver"}${v.stale ? " (stale)" : ""}` }))} />
      <div className="mt-4">{q.isPending ? <Spinner /> : vehicles.length === 0 ? <Empty title="No vehicles on a trip right now" /> :
        <DataTable caption="Tracked vehicles" rows={vehicles.map((v) => ({ ...v, id: v.vehicle_id }))} columns={[{ key: "vehicle_code", header: "Vehicle" }, { key: "driver_name", header: "Driver" },
          { key: "speed_kmh", header: "Speed", render: (v) => (v.speed_kmh != null ? `${Math.round(v.speed_kmh)} km/h` : "—") }, { key: "captured_at", header: "Last update", render: (v) => <span>{fmtDateTime(v.captured_at)} {v.stale && <Badge tone="warn">stale</Badge>}</span> }]} />}</div>
    </>
  );
}
