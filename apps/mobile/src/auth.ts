import "react-native-url-polyfill/auto";
import * as SecureStore from "expo-secure-store";
import * as WebBrowser from "expo-web-browser";
import { createClient } from "@supabase/supabase-js";
import { create } from "zustand";
import { config } from "./config";
import { parseAuthCallback } from "./links";

WebBrowser.maybeCompleteAuthSession();
export const REDIRECT_URL = "routepilot://auth/callback";

// Tokens live in the OS keystore (Keychain / Keystore), not AsyncStorage.
const storage = {
  getItem: (k: string) => SecureStore.getItemAsync(k),
  setItem: (k: string, v: string) => SecureStore.setItemAsync(k, v),
  removeItem: (k: string) => SecureStore.deleteItemAsync(k),
};
export const configured = Boolean(config.supabaseUrl && config.supabaseAnonKey);
export const supabase = createClient(config.supabaseUrl || "http://invalid.local", config.supabaseAnonKey || "unset", { auth: { storage, autoRefreshToken: true, persistSession: true, detectSessionInUrl: false, flowType: "pkce" } });

interface S { token: string | null; ready: boolean; orgId: string | null; init: () => Promise<void>; signIn: (e: string, p: string) => Promise<void>; signInWithGoogle: () => Promise<boolean>; signOut: () => Promise<void>; setOrg: (id: string) => void }
export const useAuth = create<S>((set, get) => ({
  token: null, ready: false, orgId: null,
  async init() {
    if (get().ready) return;
    if (!configured) { set({ ready: true }); return; }
    const { data } = await supabase.auth.getSession();
    set({ token: data.session?.access_token ?? null, ready: true });
    supabase.auth.onAuthStateChange((_e, s) => set({ token: s?.access_token ?? null }));
    const org = await SecureStore.getItemAsync("rp_org");
    if (org) set({ orgId: org });
  },
  async signIn(email, password) {
    const { data, error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) throw new Error(error.message);
    set({ token: data.session?.access_token ?? null });
  },
  /** Google sign-in in the system browser (Chrome Custom Tab) with PKCE; Google blocks embedded WebViews. Returns false if the user cancelled. */
  async signInWithGoogle() {
    const { data, error } = await supabase.auth.signInWithOAuth({ provider: "google", options: { redirectTo: REDIRECT_URL, skipBrowserRedirect: true } });
    if (error || !data.url) throw new Error(error?.message ?? "Google sign-in is not available");
    const res = await WebBrowser.openAuthSessionAsync(data.url, REDIRECT_URL);
    if (res.type !== "success") return false;
    const cb = parseAuthCallback(res.url);
    if (!cb) throw new Error("Google sign-in did not complete");
    if ("error" in cb) throw new Error(cb.error);
    const ex = await supabase.auth.exchangeCodeForSession(cb.code);
    if (ex.error) throw new Error(ex.error.message);
    set({ token: ex.data.session?.access_token ?? null });
    return true;
  },
  async signOut() { await supabase.auth.signOut(); set({ token: null }); },
  setOrg(id) { void SecureStore.setItemAsync("rp_org", id); set({ orgId: id }); },
}));
