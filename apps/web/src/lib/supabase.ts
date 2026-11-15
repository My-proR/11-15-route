import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { env } from "./env";

let client: SupabaseClient | null = null;
export const supabaseConfigured = Boolean(env.supabaseUrl && env.supabaseAnonKey);

export function supabase(): SupabaseClient {
  if (!supabaseConfigured) throw new Error("Supabase is not configured (VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY).");
  client ??= createClient(env.supabaseUrl, env.supabaseAnonKey, { auth: { persistSession: true, autoRefreshToken: true } });
  return client;
}
