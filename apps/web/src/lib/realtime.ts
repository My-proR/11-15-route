import { useEffect, useRef } from "react";
import { api } from "./api";
import { apiUrl } from "./env";
import { useSession } from "./session";

type Handler = (event: string, data: unknown) => void;

/** Subscribes to server-pushed events using a short-lived single-use ticket (the bearer token never appears in the URL). Reconnects with backoff. */
export function useRealtime(handler: Handler) {
  const token = useSession((s) => s.token);
  const org = useSession((s) => s.activeOrgId);
  const h = useRef(handler);
  h.current = handler;
  useEffect(() => {
    if (!token) return;
    let ws: WebSocket | null = null;
    let stopped = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ping: ReturnType<typeof setInterval> | undefined;
    const connect = async () => {
      try {
        const { ticket } = await api<{ ticket: string }>("/realtime/ticket", { method: "POST" });
        const base = apiUrl("/ws").replace(/^http/, "ws");
        const absolute = base.startsWith("ws") ? base : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${base}`;
        ws = new WebSocket(`${absolute}?ticket=${encodeURIComponent(ticket)}`);
        ws.onopen = () => { attempt = 0; ping = setInterval(() => ws?.readyState === 1 && ws.send("ping"), 25_000); };
        ws.onmessage = (m) => { if (m.data === "pong") return; try { const j = JSON.parse(String(m.data)) as { event: string; data: unknown }; h.current(j.event, j.data); } catch { /* ignore malformed */ } };
        ws.onclose = () => { clearInterval(ping); if (!stopped) { attempt += 1; timer = setTimeout(() => void connect(), Math.min(30_000, 1000 * 2 ** attempt)); } };
      } catch {
        if (!stopped) { attempt += 1; timer = setTimeout(() => void connect(), Math.min(30_000, 1000 * 2 ** attempt)); }
      }
    };
    void connect();
    return () => { stopped = true; clearTimeout(timer); clearInterval(ping); ws?.close(); };
  }, [token, org]);
}
