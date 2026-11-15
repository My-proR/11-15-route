// EXPO_PUBLIC_* values are bundled into the app: PUBLIC values only (never the Supabase service-role key).
export const config = {
  apiBase: (process.env.EXPO_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, ""),
  supabaseUrl: process.env.EXPO_PUBLIC_SUPABASE_URL ?? "",
  supabaseAnonKey: process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY ?? "",
};
