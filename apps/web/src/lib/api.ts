import { apiUrl } from "./env";
import { useSession } from "./session";
import type { ApiErrorBody } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;
interface Opts { method?: string; body?: unknown; query?: Query; idempotencyKey?: string; form?: FormData; signal?: AbortSignal; raw?: boolean }

export const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

export function buildQuery(q?: Query): string {
  if (!q) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function api<T = unknown>(path: string, o: Opts = {}): Promise<T> {
  const { token, activeOrgId } = useSession.getState();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (activeOrgId) headers["X-Organization-Id"] = activeOrgId;
  if (o.idempotencyKey) headers["Idempotency-Key"] = o.idempotencyKey;
  let body: BodyInit | undefined;
  if (o.form) body = o.form;
  else if (o.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(o.body); }
  let res: Response;
  try {
    res = await fetch(apiUrl(path) + buildQuery(o.query), { method: o.method ?? (body ? "POST" : "GET"), headers, body, signal: o.signal });
  } catch {
    throw new ApiError(0, "network", "Cannot reach the server. Check your connection and try again.");
  }
  if (res.status === 401) { void useSession.getState().signOut(); }
  if (!res.ok) {
    let parsed: Partial<ApiErrorBody> & { detail?: unknown } = {};
    try { parsed = await res.json(); } catch { /* non-JSON error */ }
    const e = parsed.error;
    const detail = typeof parsed.detail === "string" ? parsed.detail : undefined;
    throw new ApiError(res.status, e?.code ?? `http_${res.status}`, e?.message ?? detail ?? res.statusText, e?.details);
  }
  if (res.status === 204) return undefined as T;
  if (o.raw) return (await res.blob()) as T;
  return (await res.json()) as T;
}

export const errMsg = (e: unknown): string => (e instanceof Error ? e.message : "Something went wrong");
