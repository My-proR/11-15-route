import { useEffect } from "react";
import { SafeAreaView, View } from "react-native";
import { Tabs } from "expo-router";
import { useMe } from "../../src/hooks";
import { useAuth } from "../../src/auth";
import { useLang, useT } from "../../src/i18n";
import { Btn, LanguageSwitch, Notice, Txt, useTheme } from "../../src/ui";

export default function TabsLayout() {
  const t = useTheme();
  const tr = useT();
  const { data: me, refetch, isFetching } = useMe();
  const adopt = useLang((s) => s.adopt);
  const signOut = useAuth((s) => s.signOut);
  useEffect(() => { adopt(me?.language); }, [me?.language, adopt]);

  // Billing/admin suspension is enforced by the server; this just explains it instead of showing broken screens.
  if (me?.organization_suspended) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: t.bg, justifyContent: "center", padding: 24, gap: 14 }}>
        <Txt bold>{tr("pausedTitle")}</Txt>
        <Notice tone="warn">{tr("paused")}</Notice>
        <Btn label={tr("refresh")} busy={isFetching} onPress={() => void refetch()} />
        <Btn kind="secondary" label={tr("signOut")} onPress={() => void signOut()} />
        <View /><LanguageSwitch />
      </SafeAreaView>
    );
  }
  return (
    <Tabs screenOptions={{ headerStyle: { backgroundColor: t.card }, headerTitleStyle: { color: t.text }, tabBarStyle: { backgroundColor: t.card, borderTopColor: t.line }, tabBarActiveTintColor: t.accent, tabBarInactiveTintColor: t.muted }}>
      <Tabs.Screen name="index" options={{ title: tr("tabTrips") }} />
      <Tabs.Screen name="sos" options={{ title: tr("tabSos") }} />
      <Tabs.Screen name="alerts" options={{ title: tr("tabAlerts") }} />
      <Tabs.Screen name="account" options={{ title: tr("tabAccount") }} />
    </Tabs>
  );
}
