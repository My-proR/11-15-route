import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useAction } from "../lib/hooks";
import { Badge, Button, Card, DataTable, Notice, PageHeader } from "../components/ui";

interface Preview { job_id: string; mapping: Record<string, string>; unmapped_headers: string[]; total_rows: number; valid_rows: number; error_rows: number; duplicate_rows: number; errors: { row: number; kind: string; messages: string[] }[] }
const MAX_BYTES = 2_000_000;

export default function ImportEmployees() {
  const [file, setFile] = useState<File | null>(null);
  const [prev, setPrev] = useState<Preview | null>(null);
  const [done, setDone] = useState<{ inserted: number; skipped_conflicts?: number } | null>(null);
  const [fileErr, setFileErr] = useState("");
  const preview = useAction((f: File) => { const fd = new FormData(); fd.append("file", f); return api<Preview>("/employees/import/preview", { form: fd }); }, { onSuccess: (p) => { setPrev(p); setDone(null); } });
  const commit = useAction((id: string) => api<{ inserted: number; skipped_conflicts?: number }>(`/employees/import/${id}/commit`, { method: "POST" }), { success: "Import complete", invalidate: [["/employees"]], onSuccess: (r) => { setDone(r); setPrev(null); setFile(null); } });
  const pick = (f: File | null) => {
    setFileErr(""); setPrev(null); setDone(null);
    if (!f) { setFile(null); return; }
    if (!/\.(csv|xlsx)$/i.test(f.name)) { setFileErr("Choose a .csv or .xlsx file."); return; }
    if (f.size > MAX_BYTES) { setFileErr("File is larger than 2 MB."); return; }
    setFile(f);
  };
  return (
    <>
      <PageHeader title="Import employees" subtitle="Upload, review, then confirm. Existing employees are never overwritten; duplicates are skipped." actions={<Link to="/employees"><Button variant="secondary">Back to employees</Button></Link>} />
      <div className="space-y-5">
        <Card title="1. Choose a file">
          <p className="mb-3 text-sm text-muted">Required columns: employee code, name, work email. Optional: phone, department, designation, gender, branch, shift, pickup/drop address and latitude/longitude. Column names are matched automatically (e.g. “Employee ID”, “Email”).</p>
          <input type="file" aria-label="Employees file" accept=".csv,.xlsx" onChange={(e) => pick(e.target.files?.[0] ?? null)} className="block w-full text-sm" />
          {fileErr && <p role="alert" className="mt-2 text-sm text-bad">{fileErr}</p>}
          <div className="mt-3"><Button disabled={!file} busy={preview.isPending} onClick={() => file && preview.mutate(file)}>Preview import</Button></div>
        </Card>
        {prev && (
          <Card title="2. Review">
            <div className="mb-3 flex flex-wrap gap-2"><Badge tone="ok">{prev.valid_rows} ready</Badge><Badge tone="warn">{prev.duplicate_rows} duplicates (skipped)</Badge><Badge tone="bad">{prev.error_rows} with errors</Badge><Badge>{prev.total_rows} rows</Badge></div>
            {prev.unmapped_headers.length > 0 && <div className="mb-3"><Notice tone="info">Ignored columns: {prev.unmapped_headers.join(", ")}</Notice></div>}
            {prev.errors.length > 0 && <DataTable caption="Row problems" rows={prev.errors.map((e) => ({ ...e, id: e.row }))} columns={[{ key: "row", header: "Row" }, { key: "kind", header: "Type" }, { key: "messages", header: "Problem", render: (e) => e.messages.join("; ") }]} />}
            <div className="mt-4 flex justify-end"><Button disabled={prev.valid_rows === 0} busy={commit.isPending} onClick={() => commit.mutate(prev.job_id)}>Import {prev.valid_rows} employees</Button></div>
          </Card>)}
        {done && <Notice tone="ok">Imported {done.inserted} employees{done.skipped_conflicts ? `; ${done.skipped_conflicts} skipped because they already existed` : ""}.</Notice>}
      </div>
    </>
  );
}
