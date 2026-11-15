import { useMemo, useRef, useState } from "react";
import { Alert, SafeAreaView, Text, TextInput, View } from "react-native";
import * as Location from "expo-location";
import { api, errMsg, newKey } from "../../src/api";
import { useFeatures } from "../../src/hooks";
import { Btn, Notice, Txt, useTheme } from "../../src/ui";

export default function Sos() {
  const t = useTheme();
  const gps = useFeatures().data?.sos_gps.effective ?? false;
  const key = useMemo(() => newKey(), []);              // one key per screen visit: a retry can never create a second alert
  const [msg, setMsg] = useState("");
  const [stage, setStage] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [result, setResult] = useState("");
  const [err, setErr] = useState("");
  const sent = useRef<string | null>(null);

  const put = async (id: string, body: Record<string, unknown>) => {
    try { const r = await api<{ location_status: string }>(`/sos/${id}/location`, { method: "PUT", body }); setResult(r.location_status === "obtained" ? "Your alert was sent with your location." : "Your alert was sent. Your location could not be shared."); }
    catch { setResult("Your alert was sent. Your location could not be shared."); }
  };
  const attach = async (id: string) => {
    try {
      const perm = await Location.requestForegroundPermissionsAsync();
      if (perm.status !== "granted") return void (await put(id, { status: "denied" }));
      const p = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      await put(id, { status: "obtained", latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy_m: p.coords.accuracy ?? null, captured_at: new Date(p.timestamp).toISOString() });
    } catch { await put(id, { status: "unavailable" }); }
  };
  const raise = async (withLocation: boolean) => {
    setStage("sending"); setErr("");
    try {
      const inc = await api<{ id: string }>("/sos", { method: "POST", idempotencyKey: key, body: { message: msg } });   // the alert never waits for GPS
      sent.current = inc.id; setResult("Your alert was sent. Help has been notified."); setStage("sent");
      if (withLocation) await attach(inc.id);
    } catch (e) { setErr(errMsg(e)); setStage("failed"); }
  };
  const press = () => {
    if (!gps) return void raise(false);
    Alert.alert("Share your location?", "Your organization lets you attach your location to an SOS so help can find you. It is visible to your dispatchers and administrators and deleted automatically after the retention period. Your alert is sent right away either way.",
      [{ text: "Send without location", onPress: () => void raise(false) }, { text: "Send with my location", onPress: () => void raise(true) }, { text: "Cancel", style: "cancel" }]);
  };
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: t.bg, padding: 20, gap: 16 }}>
      {stage === "sent" ? <Notice tone="ok">{result}</Notice> : (
        <>
          <Txt muted>This alerts your transport team immediately. {gps ? "With your permission your location is attached." : "Your location is not collected."}</Txt>
          <TextInput accessibilityLabel="What is happening (optional)" placeholder="What's happening? (optional)" placeholderTextColor={t.muted} maxLength={500} value={msg} onChangeText={setMsg}
            style={{ borderWidth: 1, borderColor: t.line, backgroundColor: t.card, color: t.text, borderRadius: 12, minHeight: 48, paddingHorizontal: 14 }} />
          {err ? <Notice tone="bad">{err}</Notice> : null}
          <View style={{ flex: 1, justifyContent: "center" }}>
            <Btn kind="danger" label={stage === "sending" ? "Sending…" : stage === "failed" ? "Try again" : "SEND SOS"} disabled={stage === "sending"} onPress={press} style={{ minHeight: 160, borderRadius: 28 }} />
          </View>
        </>)}
      <Text style={{ color: t.muted, fontSize: 12, textAlign: "center" }}>In immediate danger, call your local emergency number.</Text>
    </SafeAreaView>
  );
}
