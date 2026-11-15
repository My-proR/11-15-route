import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import { api } from "../lib/api";
import { useAction, useMe } from "../lib/hooks";
import type { Page } from "../lib/types";
import { Badge, Button, DataTable, Empty, ErrorBox, Field, Input, Modal, PageHeader, Select, Spinner } from "../components/ui";
import { useLookup } from "../components/ResourcePage";

interface RouteRow { id: string; code: string; name: string; branch_id: string | null; shift_id: string | null; is_active: boolean; stop_count: number }
interface RouteDetail extends RouteRow { stops: { location_id: string; name: string }[] }

export default function Routes() {
  const { data: me } = useMe();
  const admin = me?.role === "org_admin";
  const q = useQuery({ queryKey: ["routes"], queryFn: () => api<Page<RouteRow>>("/routes?page_size=200") });
  const [edit, setEdit] = useState<RouteDetail | "new" | null>(null);
  const open = async (id: string) => setEdit(await api<RouteDetail>(`/routes/${id}`));
  return (
    <>
      <PageHeader title="Routes" subtitle="Recurring routes: an ordered list of stops. One-day changes are made from the Vehicle board." actions={admin ? <Button onClick={() => setEdit("new")}><Plus className="size-4" />Add route</Button> : undefined} />
      {q.isPending ? <Spinner /> : q.isError ? <ErrorBox error={q.error} /> : q.data.items.length === 0 ? <Empty title="No routes yet" /> :
        <DataTable caption="Routes" rows={q.data.items} onRowClick={admin ? (r) => void open(r.id) : undefined} columns={[{ key: "code", header: "Code" }, { key: "name", header: "Name" }, { key: "stop_count", header: "Stops" }, { key: "is_active", header: "Status", render: (r) => <Badge tone={r.is_active ? "ok" : "neutral"}>{r.is_active ? "active" : "inactive"}</Badge> }]} />}
      <Modal wide open={edit !== null} onClose={() => setEdit(null)} title={edit === "new" ? "Add route" : "Edit route"}>{edit !== null && <RouteForm route={edit === "new" ? undefined : edit} onDone={() => setEdit(null)} />}</Modal>
    </>
  );
}

function RouteForm({ route, onDone }: { route?: RouteDetail; onDone: () => void }) {
  const [code, setCode] = useState(route?.code ?? "");
  const [name, setName] = useState(route?.name ?? "");
  const [branch, setBranch] = useState(route?.branch_id ?? "");
  const [shift, setShift] = useState(route?.shift_id ?? "");
  const [stops, setStops] = useState<string[]>(route?.stops.map((s) => s.location_id) ?? []);
  const [active, setActive] = useState(route?.is_active ?? true);
  const [add, setAdd] = useState("");
  const branches = useLookup("branches"); const shifts = useLookup("shifts"); const locs = useLookup("locations");
  const label = (id: string) => locs.data?.find((l) => l.value === id)?.label ?? route?.stops.find((s) => s.location_id === id)?.name ?? id.slice(0, 8);
  const body = { code, name, branch_id: branch || null, shift_id: shift || null, stops, is_active: active };
  const save = useAction(() => (route ? api(`/routes/${route.id}`, { method: "PUT", body }) : api("/routes", { method: "POST", body })), { success: "Route saved", invalidate: [["routes"], ["lookup"]], onSuccess: onDone });
  const move = (i: number, d: number) => setStops((s) => { const n = [...s]; const j = i + d; if (j < 0 || j >= n.length) return s; [n[i], n[j]] = [n[j]!, n[i]!]; return n; });
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Code *">{(id) => <Input id={id} value={code} onChange={(e) => setCode(e.target.value)} />}</Field><Field label="Name *">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Office / branch">{(id) => <Select id={id} value={branch} onChange={(e) => setBranch(e.target.value)}><option value="">— none —</option>{branches.data?.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}</Select>}</Field>
        <Field label="Shift">{(id) => <Select id={id} value={shift} onChange={(e) => setShift(e.target.value)}><option value="">— none —</option>{shifts.data?.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}</Select>}</Field>
      </div>
      <div><p className="mb-1 text-sm font-medium">Stops in order</p>
        <ol className="space-y-1">{stops.map((s, i) => <li key={s + i} className="flex items-center gap-2 rounded-lg border border-line px-3 py-1.5 text-sm"><span className="w-6 text-muted">{i + 1}.</span><span className="flex-1">{label(s)}</span>
          <button aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp className="size-4" /></button><button aria-label="Move down" disabled={i === stops.length - 1} onClick={() => move(i, 1)}><ArrowDown className="size-4" /></button><button aria-label="Remove stop" onClick={() => setStops(stops.filter((_, k) => k !== i))}><X className="size-4" /></button></li>)}</ol>
        <div className="mt-2 flex gap-2"><Select aria-label="Add stop" value={add} onChange={(e) => setAdd(e.target.value)}><option value="">Add a stop…</option>{locs.data?.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}</Select><Button variant="secondary" disabled={!add} onClick={() => { setStops([...stops, add]); setAdd(""); }}>Add</Button></div></div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="size-4" checked={active} onChange={(e) => setActive(e.target.checked)} />Active</label>
      <div className="flex justify-end gap-2 border-t border-line pt-4"><Button variant="secondary" onClick={onDone}>Cancel</Button><Button busy={save.isPending} disabled={!code.trim() || !name.trim()} onClick={() => save.mutate()}>Save route</Button></div>
    </div>
  );
}
