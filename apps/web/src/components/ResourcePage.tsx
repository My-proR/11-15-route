import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useForm, type FieldValues } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z, type ZodTypeAny } from "zod";
import { Plus, Search } from "lucide-react";
import { api, buildQuery } from "../lib/api";
import { useAction, useMe } from "../lib/hooks";
import type { Page } from "../lib/types";
import { Badge, Button, Confirm, DataTable, Empty, ErrorBox, Field, Input, Modal, PageHeader, Pager, Select, Spinner, type Column } from "./ui";

export type LookupKey = "branches" | "shifts" | "drivers" | "routes" | "locations";
export interface FieldDef {
  name: string; label: string; type: "text" | "email" | "tel" | "number" | "date" | "time" | "select" | "checkbox" | "tags";
  required?: boolean; options?: { value: string; label: string }[]; lookup?: LookupKey; hint?: string; min?: number; max?: number; step?: number; wide?: boolean;
  multi?: boolean; // tags: comma separated; lookup+multi: multi-select of ids
  pair?: string;   // lat/lon pairs must be given together
}
type Row = Record<string, unknown> & { id: string };

const LOOKUPS: Record<LookupKey, { path: string; query?: Record<string, string>; label: (r: Row) => string }> = {
  branches: { path: "/locations", query: { kind: "branch" }, label: (r) => String(r.name) },
  locations: { path: "/locations", label: (r) => `${String(r.name)} (${String(r.kind)})` },
  shifts: { path: "/shifts", label: (r) => `${String(r.name)} ${String(r.start_time).slice(0, 5)}–${String(r.end_time).slice(0, 5)}` },
  drivers: { path: "/drivers", label: (r) => `${String(r.name)} (${String(r.driver_code)})` },
  routes: { path: "/routes", label: (r) => String(r.name) },
};

export function useLookup(key?: LookupKey) {
  const { data: me } = useMe();
  return useQuery({
    queryKey: ["lookup", key, me?.active_organization_id], enabled: Boolean(key && me?.active_organization_id), staleTime: 60_000,
    queryFn: async () => { const l = LOOKUPS[key!]; const r = await api<Page<Row>>(`/${l.path.slice(1)}${buildQuery({ page_size: 200, is_active: true, ...l.query })}`); return r.items.map((x) => ({ value: x.id, label: l.label(x) })); },
  });
}

export function buildSchema(fields: FieldDef[]) {
  const shape: Record<string, ZodTypeAny> = {};
  for (const f of fields) {
    let s: ZodTypeAny;
    switch (f.type) {
      case "number": s = z.string().trim().refine((v) => v === "" || !Number.isNaN(Number(v)), "Enter a number").refine((v) => v === "" || f.min === undefined || Number(v) >= f.min, `Minimum ${f.min}`).refine((v) => v === "" || f.max === undefined || Number(v) <= f.max, `Maximum ${f.max}`); break;
      case "email": s = z.string().trim().refine((v) => v === "" || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v), "Enter a valid email"); break;
      case "checkbox": s = z.boolean(); break;
      case "tags": s = z.string(); break;
      default: s = z.string().trim();
    }
    if (f.multi && f.lookup) s = z.array(z.string());
    if (f.required && f.type !== "checkbox" && !(f.multi && f.lookup)) s = s.refine((v: unknown) => String(v ?? "").trim() !== "", `${f.label} is required`);
    shape[f.name] = s;
  }
  return z.object(shape).superRefine((v, ctx) => {
    for (const f of fields) if (f.pair) {
      const a = String(v[f.name] ?? "").trim(), b = String(v[f.pair] ?? "").trim();
      if ((a === "") !== (b === "")) ctx.addIssue({ code: "custom", path: [f.name], message: "Provide latitude and longitude together" });
    }
  });
}

export function toDefaults(fields: FieldDef[], row?: Row): FieldValues {
  const out: FieldValues = {};
  for (const f of fields) {
    const v = row?.[f.name];
    if (f.type === "checkbox") out[f.name] = row ? Boolean(v) : f.name === "is_active" || f.name === "is_available";
    else if (f.multi && f.lookup) out[f.name] = Array.isArray(v) ? (v as string[]) : [];
    else if (f.type === "tags") out[f.name] = Array.isArray(v) ? (v as string[]).join(", ") : "";
    else if (f.type === "time") out[f.name] = v ? String(v).slice(0, 5) : "";
    else out[f.name] = v == null ? "" : String(v);
  }
  return out;
}

/** Converts form strings into the API payload. Empty optional fields are omitted on create and sent as null on update. */
export function toPayload(fields: FieldDef[], values: FieldValues, mode: "create" | "update"): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const v = values[f.name];
    if (f.type === "checkbox") { out[f.name] = Boolean(v); continue; }
    if (f.multi && f.lookup) { out[f.name] = v ?? []; continue; }
    if (f.type === "tags") { out[f.name] = String(v ?? "").split(",").map((x) => x.trim().toLowerCase()).filter(Boolean); continue; }
    const s = String(v ?? "").trim();
    if (s === "") { if (mode === "update" && !f.required) out[f.name] = f.type === "text" && f.name.endsWith("address") ? "" : null; continue; }
    out[f.name] = f.type === "number" ? Number(s) : s;
  }
  return out;
}

function FieldInput({ f, form }: { f: FieldDef; form: ReturnType<typeof useForm> }) {
  const lookup = useLookup(f.lookup);
  const opts = f.options ?? lookup.data ?? [];
  const err = form.formState.errors[f.name]?.message as string | undefined;
  if (f.type === "checkbox") return (
    <label className="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" className="size-4" {...form.register(f.name)} />{f.label}</label>
  );
  if (f.multi && f.lookup) {
    const sel = (form.watch(f.name) as string[]) ?? [];
    return (
      <Field label={f.label} error={err} hint={f.hint}>
        {() => (
          <div className="flex flex-wrap gap-2" role="group" aria-label={f.label}>
            {opts.length === 0 && <span className="text-sm text-muted">None available</span>}
            {opts.map((o) => {
              const on = sel.includes(o.value);
              return <button type="button" key={o.value} aria-pressed={on} onClick={() => form.setValue(f.name, on ? sel.filter((x) => x !== o.value) : [...sel, o.value], { shouldDirty: true })}
                className={`min-h-9 rounded-full border px-3 text-sm ${on ? "border-accent bg-accentsoft text-accent" : "border-line"}`}>{o.label}</button>;
            })}
          </div>
        )}
      </Field>
    );
  }
  return (
    <Field label={f.label + (f.required ? " *" : "")} error={err} hint={f.hint}>
      {(id) => f.type === "select" || f.lookup ? (
        <Select id={id} {...form.register(f.name)}><option value="">{f.required ? "Select…" : "— none —"}</option>{opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</Select>
      ) : (
        <Input id={id} type={f.type === "tags" ? "text" : f.type === "number" ? "number" : f.type} inputMode={f.type === "number" ? "decimal" : undefined} step={f.step ?? (f.type === "number" ? "any" : undefined)} {...form.register(f.name)} />
      )}
    </Field>
  );
}

export function RecordForm({ fields, row, onSaved, endpoint, onCancel }: { fields: FieldDef[]; row?: Row; endpoint: string; onSaved: () => void; onCancel: () => void }) {
  const schema = useMemo(() => buildSchema(fields), [fields]);
  const form = useForm({ resolver: zodResolver(schema), defaultValues: toDefaults(fields, row) });
  const save = useAction((values: FieldValues) => row
    ? api(`${endpoint}/${row.id}`, { method: "PATCH", body: toPayload(fields, values, "update") })
    : api(endpoint, { method: "POST", body: toPayload(fields, values, "create") }), { success: row ? "Saved" : "Created", onSuccess: onSaved, invalidate: [[endpoint]] });
  return (
    <form className="space-y-4" onSubmit={form.handleSubmit((v) => save.mutate(v))} noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        {fields.map((f) => <div key={f.name} className={f.wide ? "sm:col-span-2" : undefined}><FieldInput f={f} form={form as never} /></div>)}
      </div>
      <div className="flex justify-end gap-2 border-t border-line pt-4">
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
        <Button type="submit" busy={save.isPending}>{row ? "Save changes" : "Create"}</Button>
      </div>
    </form>
  );
}

export interface ResourceProps {
  title: string; subtitle?: string; endpoint: string; singular: string; fields: FieldDef[]; columns: Column<Row>[]; canWrite?: boolean; extraActions?: ReactNode; filters?: ReactNode;
}
export function ResourcePage({ title, subtitle, endpoint, singular, fields, columns, canWrite = true, extraActions }: ResourceProps) {
  const { data: me } = useMe();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<Row | "new" | null>(null);
  const [removing, setRemoving] = useState<Row | null>(null);
  const orgId = me?.active_organization_id;
  const list = useQuery({ queryKey: [endpoint, orgId, q, page, showInactive], enabled: Boolean(orgId), placeholderData: (p) => p,
    queryFn: () => api<Page<Row>>(endpoint + buildQuery({ q, page, page_size: 25, is_active: showInactive ? undefined : true })) });
  const remove = useAction((r: Row) => api(`${endpoint}/${r.id}`, { method: "DELETE" }), { success: `${singular} deactivated`, invalidate: [[endpoint]], onSuccess: () => setRemoving(null) });
  const cols: Column<Row>[] = [...columns,
    { key: "is_active", header: "Status", render: (r) => <Badge tone={r.is_active === false ? "neutral" : "ok"}>{r.is_active === false ? "inactive" : "active"}</Badge> },
    ...(canWrite ? [{ key: "_a", header: "", className: "text-right", render: (r: Row) => (
      <div className="flex justify-end gap-1">
        <Button variant="ghost" className="min-h-8 px-2" aria-label={`Edit ${String(r.name ?? r.id)}`} onClick={() => setEditing(r)}>Edit</Button>
        {r.is_active !== false && <Button variant="ghost" className="min-h-8 px-2 text-bad" onClick={() => setRemoving(r)}>Deactivate</Button>}
      </div>) }] : [])];
  return (
    <>
      <PageHeader title={title} subtitle={subtitle} actions={<>{extraActions}{canWrite && <Button onClick={() => setEditing("new")}><Plus className="size-4" />Add {singular}</Button>}</>} />
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="relative w-full max-w-xs"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden /><Input aria-label={`Search ${title}`} className="pl-9" placeholder="Search…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="size-4" checked={showInactive} onChange={(e) => { setShowInactive(e.target.checked); setPage(1); }} />Show inactive</label>
      </div>
      {list.isPending ? <Spinner /> : list.isError ? <ErrorBox error={list.error} retry={() => void list.refetch()} /> :
        list.data.items.length === 0 ? <Empty title={`No ${title.toLowerCase()} found`}>{q ? "Try a different search." : canWrite ? `Add your first ${singular}.` : undefined}</Empty> :
        <><DataTable rows={list.data.items} columns={cols} caption={title} /><Pager page={page} size={list.data.page_size} total={list.data.total} onPage={setPage} /></>}
      <Modal open={editing !== null} onClose={() => setEditing(null)} title={editing === "new" ? `Add ${singular}` : `Edit ${singular}`} wide>
        {editing !== null && <RecordForm fields={fields} endpoint={endpoint} row={editing === "new" ? undefined : editing} onSaved={() => setEditing(null)} onCancel={() => setEditing(null)} />}
      </Modal>
      <Confirm open={removing !== null} danger title={`Deactivate ${singular}?`} confirmLabel="Deactivate" busy={remove.isPending} onClose={() => setRemoving(null)} onConfirm={() => removing && remove.mutate(removing)}
        message={<>This hides <strong>{String(removing?.name ?? "")}</strong> from planning. Existing trips and history are kept.</>} />
    </>
  );
}
export type { Row };
