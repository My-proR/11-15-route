import { useEffect, useRef, useState } from "react";
import { Alert, Linking, RefreshControl, ScrollView, Text, View } from "react-native";
import * as Location from "expo-location";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errMsg, newKey } from "../../src/api";
import { useMe } from "../../src/hooks";
import { useT } from "../../src/i18n";
import { Btn, Card, Notice, Txt, useTheme } from "../../src/ui";
import { dueToReport, shouldShare, toPositionBody } from "../../src/tracking";
import { CodeEntry } from "../../src/CodeEntry";
import { ScanSheet, TripQrSheet } from "../../src/TripQr";

interface Trip { id: string; service_date: string; direction: string; status: string; shift_name: string; start_time: string;
  crew: { driver_name: string | null; driver_phone: string | null; vehicle_code: string; registration_number: string } | null;
  passengers?: { passenger_id: string; name: string; phone: string | null; address: string; stop_order: number; status: string }[];
  assignment?: { id: string; status: string; expires_at: string | null } | null; requires_boarding_code?: boolean }
interface Req { id: string; service_date: string; direction: string; status: string }

const tomorrow = () => { const d = new Date(); d.setDate(d.getDate() + 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

export default function Home() {
  const t = useTheme();
  const tr = useT();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const trips = useQuery({ queryKey: ["trips"], queryFn: () => api<Trip[]>("/trips/mine"), enabled: me?.role === "driver" || me?.role === "employee", refetchInterval: 30_000 });
  const reqs = useQuery({ queryKey: ["reqs"], queryFn: () => api<Req[]>("/me/transport-requests"), enabled: me?.role === "employee" });
  const tracking = useQuery({ queryKey: ["tracking-status"], queryFn: () => api<{ enabled: boolean; interval_seconds: number | null }>("/tracking/status"), enabled: me?.role === "driver", refetchInterval: 60_000 });
  const [qrFor, setQrFor] = useState<string | null>(null);
  const [scanFor, setScanFor] = useState<string | null>(null);
  const [codeFor, setCodeFor] = useState<{ alloc: string; pid: string; name: string } | null>(null);
  const avail = useQuery({ queryKey: ["availability"], queryFn: () => api<{ available: boolean; upcoming_trips: number }>("/me/availability"), enabled: me?.role === "driver", refetchInterval: 60_000 });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["trips"] }); void qc.invalidateQueries({ queryKey: ["reqs"] }); };
  const act = useMutation({ mutationFn: (fn: () => Promise<unknown>) => fn(), onSuccess: () => { refresh(); setCodeFor(null); }, onError: (e) => Alert.alert(tr("couldNot"), errMsg(e)) });

  const setAvail = useMutation({ mutationFn: (available: boolean) => api("/me/availability", { method: "PUT", body: { available } }), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["availability"] }); }, onError: (e) => Alert.alert("Couldn't change duty status", errMsg(e)) });
  const active = trips.data?.find((x) => x.status === "IN_PROGRESS");
  const share = useTrackingShare(shouldShare(me?.role, Boolean(tracking.data?.enabled), active?.id), tracking.data?.interval_seconds ?? 30);

  if (me && me.role !== "driver" && me.role !== "employee") return <View style={{ flex: 1, padding: 20, backgroundColor: t.bg }}><Notice tone="info">{tr("webOnly")}</Notice></View>;
  return (
    <ScrollView style={{ backgroundColor: t.bg }} contentContainerStyle={{ padding: 16, gap: 14 }} refreshControl={<RefreshControl refreshing={trips.isFetching} onRefresh={refresh} />}>
      {me?.role === "driver" && avail.data && (
        <Card title={avail.data.available ? tr("availOn") : tr("availOff")}>
          <Txt muted>{avail.data.available ? tr("availOnHint") : tr("availOffHint")}{avail.data.upcoming_trips > 0 ? ` ${avail.data.upcoming_trips} upcoming.` : ""}</Txt>
          <Btn kind={avail.data.available ? "secondary" : "primary"} label={avail.data.available ? tr("goOff") : tr("goAvail")} busy={setAvail.isPending} onPress={() => setAvail.mutate(!avail.data!.available)} />
        </Card>)}
      {me?.role === "driver" && tracking.data?.enabled && <Notice tone={share === "sharing" ? "ok" : share === "denied" ? "warn" : "info"}>{share === "sharing" ? "Sharing vehicle location while this trip is in progress." : share === "denied" ? "Location permission denied. The trip still works; dispatch just can't see the vehicle." : active ? "Starting location sharing…" : "Location sharing runs only while a trip is in progress."}</Notice>}
      {trips.isError && <Notice tone="bad">{errMsg(trips.error)}</Notice>}
      {trips.data?.length === 0 && <Card><Txt muted>{me?.role === "driver" ? tr("noTripsDriver") : tr("noTripsEmployee")}</Txt></Card>}
      {trips.data?.map((x) => (
        <Card key={x.id} title={`${x.service_date} · ${x.direction} · ${x.shift_name}`}>
          <Txt muted>{x.status.replace(/_/g, " ").toLowerCase()}{x.crew ? ` · ${x.crew.vehicle_code} (${x.crew.registration_number})` : ""}</Txt>
          {me?.role === "employee" && x.crew && <><Txt>Driver: {x.crew.driver_name ?? "to be assigned"}</Txt>{x.crew.driver_phone ? <Btn kind="secondary" label={`Call ${x.crew.driver_phone}`} onPress={() => void Linking.openURL(`tel:${x.crew!.driver_phone}`)} /> : null}</>}
          {me?.role === "employee" && (x.status === "CONFIRMED" || x.status === "IN_PROGRESS") && <BoardingCode allocationId={x.id} />}
          {me?.role === "employee" && x.status === "IN_PROGRESS" && <Btn kind="secondary" label={tr("scanQr")} onPress={() => setScanFor(x.id)} />}
          {x.assignment?.status === "PENDING" && (
            <View style={{ gap: 8 }}><Notice tone="warn">{tr("acceptPrompt")}</Notice>
              <Btn label={tr("accept")} busy={act.isPending} onPress={() => act.mutate(() => api(`/driver-assignments/${x.assignment!.id}/respond`, { method: "POST", idempotencyKey: newKey(), body: { accept: true } }))} />
              <Btn kind="danger" label={tr("decline")} busy={act.isPending} onPress={() => act.mutate(() => api(`/driver-assignments/${x.assignment!.id}/respond`, { method: "POST", idempotencyKey: newKey(), body: { accept: false, reason: "Unavailable" } }))} /></View>)}
          {x.passengers?.map((p) => <View key={p.passenger_id} style={{ paddingVertical: 4 }}><Txt bold>{p.stop_order + 1}. {p.name}</Txt><Txt muted>{p.address}</Txt>
            {x.status === "IN_PROGRESS" && <View style={{ flexDirection: "row", gap: 8, marginTop: 6 }}>{(["boarded", "dropped", "no_show"] as const).map((s) => <Btn key={s} kind={p.status === s ? "primary" : "secondary"} label={s.replace("_", " ")} style={{ flex: 1, paddingHorizontal: 4 }} onPress={() => (s === "boarded" && x.requires_boarding_code ? setCodeFor({ alloc: x.id, pid: p.passenger_id, name: p.name }) : act.mutate(() => api(`/allocations/${x.id}/passengers/${p.passenger_id}/status`, { method: "POST", body: { status: s } })))} />)}</View>}</View>)}
          {me?.role === "driver" && x.status === "CONFIRMED" && <Btn label={tr("startTrip")} busy={act.isPending} onPress={() => act.mutate(() => api(`/allocations/${x.id}/start`, { method: "POST" }))} />}
          {me?.role === "driver" && x.status === "IN_PROGRESS" && <Btn kind="secondary" label={tr("showQr")} onPress={() => setQrFor(x.id)} />}
          {me?.role === "driver" && x.status === "IN_PROGRESS" && <Btn label={tr("completeTrip")} busy={act.isPending} onPress={() => act.mutate(() => api(`/allocations/${x.id}/complete`, { method: "POST" }))} />}
        </Card>))}
      {qrFor && <TripQrSheet allocationId={qrFor} visible onClose={() => setQrFor(null)} />}
      <ScanSheet visible={scanFor !== null} busy={act.isPending} onClose={() => setScanFor(null)}
        onToken={(token) => scanFor && act.mutate(() => api(`/me/trips/${scanFor}/board-by-qr`, { method: "POST", body: { token } }).then(() => { setScanFor(null); Alert.alert("You're boarded", "Have a safe trip!"); void qc.invalidateQueries({ queryKey: ["boarding-code"] }); }))} />
      <CodeEntry visible={codeFor !== null} title={`Boarding code${codeFor ? ` · ${codeFor.name}` : ""}`} hint="Ask the passenger for the 4-digit code in their app." length={4} busy={act.isPending} onCancel={() => setCodeFor(null)}
        onSubmit={(code) => codeFor && act.mutate(() => api(`/allocations/${codeFor.alloc}/passengers/${codeFor.pid}/status`, { method: "POST", body: { status: "boarded", code } }))} />
      {me?.role === "employee" && (
        <Card title={tr("myRequests")}>
          <Btn label={tr("reqPickup")} busy={act.isPending} onPress={() => act.mutate(() => api("/me/transport-requests", { method: "POST", body: { service_date: tomorrow(), direction: "pickup" } }))} />
          <Btn kind="secondary" label={tr("reqDrop")} busy={act.isPending} onPress={() => act.mutate(() => api("/me/transport-requests", { method: "POST", body: { service_date: tomorrow(), direction: "drop" } }))} />
          {reqs.data?.map((r) => <View key={r.id} style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: 4 }}><Txt>{r.service_date} · {r.direction} · {r.status}</Txt>{r.status === "open" && <Text accessibilityRole="button" style={{ color: t.bad, padding: 8 }} onPress={() => act.mutate(() => api(`/me/transport-requests/${r.id}`, { method: "DELETE" }))}>Cancel</Text>}</View>)}
        </Card>)}
    </ScrollView>
  );
}

function BoardingCode({ allocationId }: { allocationId: string }) {
  const t = useTheme();
  const tr = useT();
  const q = useQuery({ queryKey: ["boarding-code", allocationId], queryFn: () => api<{ code: string; boarded: boolean; required: boolean }>(`/me/boarding-code/${allocationId}`), refetchInterval: 30_000 });
  if (!q.data) return null;
  if (q.data.boarded) return <Txt muted>{tr("boarded")}</Txt>;
  return <View accessibilityLabel={`Boarding code ${q.data.code.split("").join(" ")}`} style={{ backgroundColor: t.bg, borderRadius: 10, padding: 12 }}>
    <Txt muted>{q.data.required ? tr("boardCode") : "Boarding code (optional for your organization)."}</Txt>
    <Text style={{ color: t.text, fontSize: 34, fontWeight: "800", letterSpacing: 10 }}>{q.data.code}</Text></View>;
}

/** Foreground-only position sharing for the driver's in-progress trip. Requests permission only when the server says tracking is on and a trip is active. */
function useTrackingShare(allocationId: string | undefined, interval: number): "idle" | "sharing" | "denied" | "error" {
  const [state, setState] = useState<"idle" | "sharing" | "denied" | "error">("idle");
  const last = useRef(0);
  useEffect(() => {
    if (!allocationId) { setState("idle"); return; }
    let sub: Location.LocationSubscription | undefined;
    let cancelled = false;
    void (async () => {
      const perm = await Location.requestForegroundPermissionsAsync();
      if (perm.status !== "granted") { setState("denied"); return; }
      sub = await Location.watchPositionAsync({ accuracy: Location.Accuracy.High, timeInterval: interval * 1000, distanceInterval: 0 }, (pos) => {
        const now = Date.now();
        if (cancelled || !dueToReport(now, last.current, interval)) return;
        last.current = now;
        api("/tracking/position", { method: "POST", body: toPositionBody(allocationId, pos) })
          .then(() => setState("sharing")).catch(() => setState("error"));
      });
    })();
    return () => { cancelled = true; sub?.remove(); };
  }, [allocationId, interval]);
  return state;
}
