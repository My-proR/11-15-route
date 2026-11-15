import type { ReactNode } from "react";
import { useLang } from "./i18n";
import { ActivityIndicator, Pressable, StyleSheet, Text, View, useColorScheme, type ViewStyle } from "react-native";

export function useTheme() {
  const dark = useColorScheme() === "dark";
  return dark
    ? { bg: "#0e1120", card: "#161a2d", line: "#2a3050", text: "#e8ebf7", muted: "#9aa3c2", accent: "#8b85ff", accentFg: "#0e1120", bad: "#ff7a80", ok: "#4cd694", warn: "#f1b44c" }
    : { bg: "#f6f7fb", card: "#ffffff", line: "#e2e5ef", text: "#161a2b", muted: "#5d6580", accent: "#4f46e5", accentFg: "#ffffff", bad: "#b4232a", ok: "#0f7b4f", warn: "#9a6200" };
}

export function Btn({ label, onPress, kind = "primary", busy, disabled, style }: { label: string; onPress: () => void; kind?: "primary" | "danger" | "secondary"; busy?: boolean; disabled?: boolean; style?: ViewStyle }) {
  const t = useTheme();
  const bg = kind === "primary" ? t.accent : kind === "danger" ? t.bad : t.card;
  const fg = kind === "secondary" ? t.text : kind === "danger" ? "#fff" : t.accentFg;
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled: Boolean(disabled || busy), busy }} disabled={disabled || busy} onPress={onPress}
      style={({ pressed }) => [{ minHeight: 48, borderRadius: 12, paddingHorizontal: 16, alignItems: "center", justifyContent: "center", backgroundColor: bg, opacity: disabled ? 0.5 : pressed ? 0.85 : 1, borderWidth: kind === "secondary" ? 1 : 0, borderColor: t.line }, style]}>
      {busy ? <ActivityIndicator color={fg} /> : <Text style={{ color: fg, fontWeight: "600", fontSize: 16 }}>{label}</Text>}
    </Pressable>
  );
}
export function Card({ children, title }: { children: ReactNode; title?: string }) {
  const t = useTheme();
  return <View style={[s.card, { backgroundColor: t.card, borderColor: t.line }]}>{title ? <Text style={{ color: t.text, fontWeight: "700", marginBottom: 8, fontSize: 16 }}>{title}</Text> : null}{children}</View>;
}
export function Txt({ children, muted, bold, color }: { children: ReactNode; muted?: boolean; bold?: boolean; color?: string }) {
  const t = useTheme();
  return <Text style={{ color: color ?? (muted ? t.muted : t.text), fontWeight: bold ? "700" : "400", fontSize: 15 }}>{children}</Text>;
}
export function Notice({ tone, children }: { tone: "info" | "warn" | "bad" | "ok"; children: ReactNode }) {
  const t = useTheme();
  const c = tone === "bad" ? t.bad : tone === "warn" ? t.warn : tone === "ok" ? t.ok : t.accent;
  return <View accessibilityRole="alert" style={{ borderLeftWidth: 4, borderLeftColor: c, backgroundColor: t.card, padding: 12, borderRadius: 8 }}><Text style={{ color: t.text }}>{children}</Text></View>;
}
const s = StyleSheet.create({ card: { borderWidth: 1, borderRadius: 14, padding: 14, gap: 6 } });

/** Small EN/தமிழ் toggle. Saves on the device immediately and on the server profile when signed in (best-effort). */
export function LanguageSwitch({ onChange }: { onChange?: (l: "en" | "ta") => void }) {
  const t = useTheme();
  const { lang, setLang } = useLang();
  return (
    <View accessibilityRole="radiogroup" style={{ flexDirection: "row", alignSelf: "center", borderWidth: 1, borderColor: t.line, borderRadius: 999, overflow: "hidden" }}>
      {([["en", "English"], ["ta", "தமிழ்"]] as const).map(([k, label]) => (
        <Pressable key={k} accessibilityRole="radio" accessibilityState={{ selected: lang === k }} onPress={() => { setLang(k); onChange?.(k); }}
          style={{ minHeight: 44, minWidth: 88, alignItems: "center", justifyContent: "center", backgroundColor: lang === k ? t.accent : t.card }}>
          <Text style={{ color: lang === k ? t.accentFg : t.text, fontWeight: "600" }}>{label}</Text>
        </Pressable>))}
    </View>
  );
}
