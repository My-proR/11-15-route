import { useEffect } from "react";
import { Stack, useRouter, useSegments } from "expo-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StatusBar } from "expo-status-bar";
import { useAuth } from "../src/auth";
import { useLang } from "../src/i18n";
import { registerForPush } from "../src/push";
import { readPendingInvite } from "../src/pendingInvite";

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 10_000 } } });

function Gate() {
  const { token, ready, init } = useAuth();
  const load = useLang((s) => s.load);
  const segs = useSegments();
  const router = useRouter();
  useEffect(() => { void load(); void init(); }, [init, load]);
  useEffect(() => {
    if (!ready) return;
    const open = segs[0] === "login" || segs[0] === "join"; // join works signed-out: it offers Google sign-in itself
    if (!token && !open) router.replace("/login");
    if (token && segs[0] === "login") {
      // Resume an invite interrupted by sign-in, otherwise go home.
      void readPendingInvite().then(async (inv) => { if (inv) router.replace(`/join/${inv}`); else router.replace("/"); });
    }
    if (token) void registerForPush();
  }, [ready, token, segs, router]);
  return <Stack screenOptions={{ headerShown: false }} />;
}
export default function Root() {
  return <QueryClientProvider client={qc}><StatusBar style="auto" /><Gate /></QueryClientProvider>;
}
