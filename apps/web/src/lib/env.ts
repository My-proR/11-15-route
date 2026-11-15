// Only PUBLIC values are read here. The Supabase service-role key must never be configured for the browser.
export const env = {
  apiBase: (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, ""),
  supabaseUrl: import.meta.env.VITE_SUPABASE_URL ?? "",
  supabaseAnonKey: import.meta.env.VITE_SUPABASE_ANON_KEY ?? "",
};
export const apiUrl = (path: string) => `${env.apiBase}/api/v1${path}`;
