import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { MessageCircle, Copy } from "lucide-react";
import { api } from "../lib/api";
import { useAction } from "../lib/hooks";
import { Badge, Button, Card, DataTable, Empty, ErrorBox, Modal, Notice, PageHeader, Spinner } from "../components/ui";
import type { Page } from "../lib/types";
import { fmtDateTime } from "../lib/format";
import { useT } from "../lib/i18n";

interface Person { id: string; name: string; phone: string | null; user_id: string | null }
interface Created { id: string; token: string; path: string; role: string; name: string | null; expires_at: string }
interface InviteRow { id: string; role: string; name: string | null; email: string | null; status: string; expires_at: string }

export const inviteUrl = (path: string) => `${window.location.origin}${path}`;
export function whatsappLink(name: string | null, role: string, url: string, phone?: string | null): string {
  const text = `Hi${name ? ` ${name.split(" ")[0]}` : ""}, please open this link to join RoutePilot as ${role}. Sign in with Google and you are set:\n${url}`;
  const digits = (phone ?? "").replace(/\D/g, "");
  const to = digits.length === 10 ? `91${digits}` : digits;       // 10-digit Indian mobile numbers get the +91 country code
  return `https://wa.me/${to}?text=${encodeURIComponent(text)}`;
}

/** Admin screen: create a link for each driver / employee without an account and send it on WhatsApp. */
export default function Invites() {
  const t = useT();
  const drivers = useQuery({ queryKey: ["inv-drivers"], queryFn: () => api<Page<Person>>("/drivers", { query: { page_size: 200 } }) });
  const employees = useQuery({ queryKey: ["inv-employees"], queryFn: () => api<Page<Person>>("/employees", { query: { page_size: 200 } }) });
  const list = useQuery({ queryKey: ["invites"], queryFn: () => api<InviteRow[]>("/invites") });
  const [created, setCreated] = useState<{ c: Created; phone: string | null } | null>(null);
  const [copied, setCopied] = useState(false);
  const create = useAction((v: { role: "driver" | "employee"; id: string; phone: string | null }) =>
    api<Created>("/invites", { method: "POST", body: { role: v.role, ...(v.role === "driver" ? { driver_id: v.id } : { employee_id: v.id }) } }).then((c) => ({ c, phone: v.phone })),
    { invalidate: [["invites"]], onSuccess: (r) => setCreated(r as { c: Created; phone: string | null }) });
  const revoke = useAction((id: string) => api(`/invites/${id}`, { method: "DELETE" }), { success: "Invite revoked", invalidate: [["invites"]] });
  const unlinked = (p?: Page<Person>) => (p?.items ?? []).filter((x) => !x.user_id);
  const url = created ? inviteUrl(created.c.path) : "";
  const block = (title: string, role: "driver" | "employee", q: typeof drivers) => (
    <Card title={`${title} without an account`}>
      {q.isPending ? <Spinner /> : q.isError ? <ErrorBox error={q.error} /> : unlinked(q.data).length === 0 ? <Empty title="Everyone here already has an account" /> : (
        <ul className="divide-y divide-line">{unlinked(q.data).map((p) => (
          <li key={p.id} className="flex items-center justify-between gap-3 py-2 text-sm"><span><strong>{p.name}</strong>{p.phone ? <span className="text-muted"> · {p.phone}</span> : null}</span>
            <Button variant="secondary" busy={create.isPending && create.variables?.id === p.id} onClick={() => create.mutate({ role, id: p.id, phone: p.phone })}><MessageCircle className="size-4" />Invite</Button></li>))}</ul>)}
    </Card>);
  return (
    <>
      <PageHeader title="Invite links" subtitle="Send each person a link on WhatsApp. They sign in with Google once and stay signed in. Each link works once and expires in 7 days." />
      <div className="grid gap-5 lg:grid-cols-2">{block("Drivers", "driver", drivers)}{block("Employees", "employee", employees)}</div>
      <div className="mt-5"><Card title="Recent invites">
        {list.isPending ? <Spinner /> : list.isError ? <ErrorBox error={list.error} /> : list.data.length === 0 ? <Empty title="No invites yet" /> : (
          <DataTable caption="Invites" rows={list.data} columns={[{ key: "name", header: "For", render: (r) => r.name ?? r.role }, { key: "role", header: "Role", render: (r) => <Badge>{r.role}</Badge> },
            { key: "status", header: "Status", render: (r) => <Badge tone={r.status === "used" ? "ok" : r.status === "active" ? "info" : "neutral"}>{r.status}</Badge> }, { key: "expires_at", header: "Expires", render: (r) => fmtDateTime(r.expires_at) },
            { key: "act", header: "", render: (r) => r.status === "active" ? <Button variant="ghost" className="min-h-8 px-2 text-bad" onClick={() => revoke.mutate(r.id)}>Revoke</Button> : null }]} />)}
      </Card></div>
      <Modal open={created !== null} onClose={() => { setCreated(null); setCopied(false); }} title={created ? `Invite ${created.c.name ?? created.c.role}` : ""}>
        {created && <div className="space-y-3">
          <Notice tone="warn">This link is shown only now. Anyone who opens it first can claim the account, so send it only to {created.c.name ?? "the person"}.</Notice>
          <p className="break-all rounded-lg bg-surface2 p-2 font-mono text-xs">{url}</p>
          <div className="flex flex-wrap gap-2">
            <a className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-accent px-4 text-sm font-semibold text-accentfg" href={whatsappLink(created.c.name, created.c.role, url, created.phone)} target="_blank" rel="noopener noreferrer"><MessageCircle className="size-4" />{t("shareWhatsapp")}</a>
            <Button variant="secondary" onClick={() => { void navigator.clipboard?.writeText(url).then(() => setCopied(true)); }}><Copy className="size-4" />{copied ? t("copied") : t("copyLink")}</Button>
          </div></div>}
      </Modal>
    </>
  );
}
