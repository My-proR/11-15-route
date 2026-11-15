import { SafeAreaView, View } from "react-native";
import Constants from "expo-constants";
import { useAuth } from "../../src/auth";
import { api } from "../../src/api";
import { useMe } from "../../src/hooks";
import { useT } from "../../src/i18n";
import { Btn, Card, LanguageSwitch, Txt, useTheme } from "../../src/ui";

export default function Account() {
  const t = useTheme();
  const tr = useT();
  const { data: me } = useMe();
  const signOut = useAuth((s) => s.signOut);
  const setOrg = useAuth((s) => s.setOrg);
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: t.bg, padding: 16, gap: 12 }}>
      <Card title={me?.full_name || me?.email || tr("tabAccount")}><Txt muted>{me?.email}</Txt><Txt muted>{me?.role?.replace("_", " ")}</Txt></Card>
      {(me?.organizations.length ?? 0) > 1 && <Card title={tr("organization")}>{me!.organizations.map((o) => <Btn key={o.organization_id} kind={o.organization_id === me!.active_organization_id ? "primary" : "secondary"} label={o.name} onPress={() => setOrg(o.organization_id)} />)}</Card>}
      <Card title={tr("language")}><LanguageSwitch onChange={(language) => { void api("/me/language", { method: "PUT", body: { language } }).catch(() => undefined); }} /></Card>
      <Txt muted>{tr("version")}: {Constants.expoConfig?.version ?? "1.0.0"}</Txt>
      <View style={{ flex: 1 }} />
      <Btn kind="secondary" label={tr("signOut")} onPress={() => void signOut()} />
    </SafeAreaView>
  );
}
