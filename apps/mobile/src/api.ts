import { useAuth } from "./auth";
import { config } from "./config";

export class ApiError extends Error { constructor(public status: number, public code: string, message: string) { super(message); } }
export const newKey = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;

export async function api<T = unknown>(path: string, o: { method?: string; body?: unknown; idempotencyKey?: string } = {}): Promise<T> {
  const { token, orgId } = useAuth.getState();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (orgId) headers["X-Organization-Id"] = orgId;
  if (o.idempotencyKey) headers["Idempotency-Key"] = o.idempotencyKey;
  if (o.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try { res = await fetch(`${config.apiBase}/api/v1${path}`, { method: o.method ?? (o.body !== undefined ? "POST" : "GET"), headers, body: o.body !== undefined ? JSON.stringify(o.body) : undefined }); }
  catch { throw new ApiError(0, "network", "No connection. Check your internet and try again."); }
  if (res.status === 401) void useAuth.getState().signOut();
  if (!res.ok) {
    let j: { error?: { code?: string; message?: string } } = {};
    try { j = await res.json(); } catch { /* not json */ }
    throw new ApiError(res.status, j.error?.code ?? `http_${res.status}`, j.error?.message ?? res.statusText);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}
export const errMsg = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong");
