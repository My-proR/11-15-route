import { useEffect, useRef, useState } from "react";
import { SafeAreaView, Text, View, ActivityIndicator } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, errMsg } from "../../src/api";
import { useAuth } from "../../src/auth";
import { useT } from "../../src/i18n";
import { clearPendingInvite, savePendingInvite } from "../../src/pendingInvite";
import { parseInviteToken } from "../../src/links";
import { Btn, LanguageSwitch, Notice, useTheme } from "../../src/ui";

interface Preview { organization: string; role: "driver" | "employee" | "dispatcher"; first_name: string | null }

/** Opened from a WhatsApp link (routepilot://join/<token> or the https App Link): sign in with Google, then the account links automatically. */
export default function Join() {
  const { token: raw = "" } = useLocalSearchParams<{ token: string }>();
  const token = parseInviteToken(String(raw)) ?? "";
  const t = useTheme(); const tr = useT(); const router = useRouter(); const qc = useQueryClient();
  const { token: session, ready, signInWithGoogle, signOut } = useAuth();
  const preview = useQuery({ queryKey: ["invite-preview", token], queryFn: () => api<Preview>(`/invites/preview?token=${encodeURIComponent(token)}`), enabled: Boolean(token), retry: false });
  const [state, setState] = useState<"idle" | "working" | "done" | "error">("idle");
  const [err, setErr] = useState<Error | null>(null);
  const started = useRef(false);

  useEffect(() => { if (token) void savePendingInvite(token); }, [token]);
  const accept = async () => {
    setState("working"); setErr(null);
    try {
      await api("/invites/accept", { method: "POST", body: { token } });
      await clearPendingInvite(); setState("done"); qc.clear();
      setTimeout(() => router.replace("/"), 700);
    } catch (e) { setErr(e as Error); setState("error"); }
  };
  useEffect(() => {
    if (ready && session && preview.isSuccess && !started.current) { started.current = true; void accept(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, session, preview.isSuccess]);

  const invalid = !token || (preview.isError && state === "idle");
  useEffect(() => { if (!token || preview.isError) void clearPendingInvite(); }, [token, preview.isError]);
  const google = async () => { try { await signInWithGoogle(); } catch (e) { setErr(e as Error); setState("error"); } };
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: t.bg, justifyContent: "center", padding: 24, gap: 14 }}>
      <Text style={{ color: t.text, fontSize: 28, fontWeight: "800" }}>RoutePilot</Text>
      {!ready || (token && preview.isPending) ? <ActivityIndicator color={t.accent} /> : invalid ? (
        <><Notice tone="bad">{tr("joinInvalid")}</Notice><Btn kind="secondary" label={session ? tr("tabTrips") : tr("signIn")} onPress={() => router.replace(session ? "/" : "/login")} /></>) : (
        <>
          <View style={{ gap: 4 }}>
            <Text style={{ color: t.text, fontSize: 20, fontWeight: "700" }}>{tr("joinTitle")}{preview.data?.first_name ? `, ${preview.data.first_name}` : ""}</Text>
            {preview.data && <Text style={{ color: t.muted }}>{tr("joinBody", { org: preview.data.organization, role: tr(`role_${preview.data.role}` as const) })}</Text>}
          </View>
          {!session && <><Text style={{ color: t.text }}>{tr("joinSignInFirst")}</Text><Btn label={tr("google")} onPress={() => void google()} /><Text style={{ color: t.muted, textAlign: "center", fontSize: 13 }}>{tr("stayIn")}</Text></>}
          {session && state === "working" && <View style={{ flexDirection: "row", gap: 10, alignItems: "center" }}><ActivityIndicator color={t.accent} /><Text style={{ color: t.text }}>{tr("joinSetup")}</Text></View>}
          {state === "done" && <Notice tone="ok">{tr("joinDone")}</Notice>}
          {state === "error" && (
            <View style={{ gap: 10 }}>
              <Notice tone="bad">{err instanceof ApiError && err.code === "invite_invalid" ? tr("joinInvalid") : errMsg(err)}</Notice>
              {session && <Btn kind="secondary" label={tr("joinTryAgain")} onPress={() => { started.current = false; void accept(); }} />}
              {session && <Btn kind="secondary" label={tr("signOut")} onPress={() => void signOut()} />}
            </View>)}
        </>)}
      <LanguageSwitch />
    </SafeAreaView>
  );
}
