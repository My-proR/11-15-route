import { create } from "zustand";
import { supabase, supabaseConfigured } from "./supabase";

const DEV_KEY = "rp_dev_token";
const ORG_KEY = "rp_active_org";

const safe = {
  get: (s: Storage, k: string) => { try { return s.getItem(k); } catch { return null; } },
  set: (s: Storage, k: string, v: string) => { try { s.setItem(k, v); } catch { /* storage unavailable */ } },
  del: (s: Storage, k: string) => { try { s.removeItem(k); } catch { /* storage unavailable */ } },
};

interface SessionState {
  token: string | null;
  ready: boolean;
  activeOrgId: string | null;
  init: () => Promise<void>;
  signIn: (email: string, password: string) => Promise<void>;
  signInWithGoogle: (returnPath?: string) => Promise<void>;
  signInWithDevToken: (token: string) => void;
  signOut: () => Promise<void>;
  setActiveOrg: (id: string | null) => void;
}

export const useSession = create<SessionState>((set, get) => ({
  token: null,
  ready: false,
  activeOrgId: safe.get(localStorage, ORG_KEY),
  async init() {
    if (get().ready) return;
    if (import.meta.env.DEV) {
      const dev = safe.get(sessionStorage, DEV_KEY);
      if (dev) { set({ token: dev, ready: true }); return; }
    }
    if (!supabaseConfigured) { set({ ready: true }); return; }
    const sb = supabase();
    const { data } = await sb.auth.getSession();
    set({ token: data.session?.access_token ?? null, ready: true });
    sb.auth.onAuthStateChange((_evt, session) => set({ token: session?.access_token ?? null }));
  },
  async signIn(email, password) {
    const { data, error } = await supabase().auth.signInWithPassword({ email, password });
    if (error) throw new Error(error.message);
    set({ token: data.session?.access_token ?? null });
  },
  async signInWithGoogle(returnPath = "/") {
    // Redirect flow: Supabase returns here with the session in the URL and supabase-js stores it (persistSession) so the user stays signed in.
    const { error } = await supabase().auth.signInWithOAuth({ provider: "google", options: { redirectTo: `${window.location.origin}${returnPath}` } });
    if (error) throw new Error(error.message);
  },
  signInWithDevToken(token) {
    if (!import.meta.env.DEV) return; // never available in production builds
    safe.set(sessionStorage, DEV_KEY, token.trim());
    set({ token: token.trim() });
  },
  async signOut() {
    safe.del(sessionStorage, DEV_KEY);
    if (supabaseConfigured) await supabase().auth.signOut();
    set({ token: null });
  },
  setActiveOrg(id) {
    if (id) safe.set(localStorage, ORG_KEY, id); else safe.del(localStorage, ORG_KEY);
    set({ activeOrgId: id });
  },
}));
