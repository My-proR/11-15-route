export const fmtDate = (d?: string | null) => (d ? new Date(d.length === 10 ? `${d}T00:00:00` : d).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }) : "—");
export const fmtDateTime = (d?: string | null) => (d ? new Date(d).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
export const fmtKm = (n?: number | null) => (n == null ? "not available" : `${Number(n).toFixed(1)} km`);
export const fmtMin = (n?: number | null) => (n == null ? "not available" : `${Math.round(Number(n))} min`);
export const isoDate = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export const tomorrow = () => { const d = new Date(); d.setDate(d.getDate() + 1); return isoDate(d); };
export const today = () => isoDate(new Date());
export const titleCase = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
export const inr = (paise?: number | string | null) => (paise == null ? "—" : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(Number(paise) / 100));
