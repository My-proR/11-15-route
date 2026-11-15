import { useState } from "react";
import { KeyboardAvoidingView, Platform, SafeAreaView, ScrollView, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { configured, useAuth } from "../src/auth";
import { Btn, LanguageSwitch, Notice, useTheme } from "../src/ui";
import { errMsg } from "../src/api";
import { useT } from "../src/i18n";
import { parseInviteToken } from "../src/links";

export default function Login() {
  const t = useTheme();
  const tr = useT();
  const router = useRouter();
  const { signIn, signInWithGoogle } = useAuth();
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [showEmail, setShowEmail] = useState(false);
  const [invite, setInvite] = useState("");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const valid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) && password.length > 0;
  const inviteToken = parseInviteToken(invite);
  const input = { borderWidth: 1, borderColor: t.line, backgroundColor: t.card, color: t.text, borderRadius: 12, minHeight: 48, paddingHorizontal: 14, fontSize: 16 } as const;
  const run = async (fn: () => Promise<unknown>) => { setBusy(true); setErr(""); try { await fn(); } catch (e) { setErr(errMsg(e) || tr("googleFailed")); } finally { setBusy(false); } };
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: t.bg }}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ flexGrow: 1, justifyContent: "center", padding: 24, gap: 14 }} keyboardShouldPersistTaps="handled">
          <Text style={{ color: t.text, fontSize: 28, fontWeight: "800" }}>RoutePilot</Text>
          <Text style={{ color: t.muted }}>{tr("loginHelp")}</Text>
          {!configured && <Notice tone="warn">This build has no Supabase configuration (EXPO_PUBLIC_SUPABASE_URL / ANON_KEY).</Notice>}
          <Btn label={tr("google")} busy={busy && !showEmail} disabled={!configured} onPress={() => void run(signInWithGoogle)} />
          <Text style={{ color: t.muted, textAlign: "center", fontSize: 13 }}>{tr("stayIn")}</Text>
          {err ? <Notice tone="bad">{err}</Notice> : null}
          {!showEmail ? <Btn kind="secondary" label={tr("orEmail")} onPress={() => setShowEmail(true)} /> : (
            <View style={{ gap: 12 }}>
              <TextInput accessibilityLabel={tr("email")} autoCapitalize="none" autoComplete="email" keyboardType="email-address" placeholder={tr("email")} placeholderTextColor={t.muted} style={input} value={email} onChangeText={setEmail} />
              <TextInput accessibilityLabel={tr("password")} secureTextEntry autoComplete="password" placeholder={tr("password")} placeholderTextColor={t.muted} style={input} value={password} onChangeText={setPassword} />
              <Btn label={tr("signIn")} busy={busy} disabled={!valid || !configured} onPress={() => void run(() => signIn(email.trim(), password))} />
            </View>)}
          <View style={{ gap: 8, marginTop: 8 }}>
            <Text style={{ color: t.muted, fontSize: 13 }}>{tr("pasteInvite")}</Text>
            <TextInput accessibilityLabel={tr("pasteInvite")} autoCapitalize="none" autoCorrect={false} placeholder="https://…/join/…" placeholderTextColor={t.muted} style={input} value={invite} onChangeText={setInvite} />
            <Btn kind="secondary" label={tr("pasteInviteGo")} disabled={!inviteToken} onPress={() => inviteToken && router.push(`/join/${inviteToken}`)} />
          </View>
          <LanguageSwitch />
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}
