import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAction } from "../lib/hooks";
import { Button, Field, Input, Modal, Notice } from "./ui";
import { useT } from "../lib/i18n";

/** Employee: the 4-digit code to show the driver when boarding. */
export function BoardingCodeCard({ allocationId }: { allocationId: string }) {
  const t = useT();
  const q = useQuery({ queryKey: ["boarding-code", allocationId], queryFn: () => api<{ code: string; boarded: boolean; required: boolean }>(`/me/boarding-code/${allocationId}`), refetchInterval: 30_000 });
  if (q.isPending || q.isError) return null;
  if (q.data.boarded) return <p className="mt-2 text-sm text-ok">{t("boarded")}</p>;
  return (
    <div className="mt-2 rounded-lg bg-surface2 p-3" aria-label="Boarding code">
      <p className="text-xs text-muted">{q.data.required ? t("boardCode") : `${t("boardCode")}`}</p>
      <p className="font-mono text-3xl font-bold tracking-[0.4em]" data-testid="boarding-code">{q.data.code}</p>
      <p className="text-xs text-muted">{t("boardCodeSafe")}</p>
    </div>
  );
}

/** Driver: enter the passenger's 4-digit boarding code. */
export function BoardingCodeModal({ name, open, onClose, onSubmit, busy }: { name: string; open: boolean; onClose: () => void; onSubmit: (code: string) => void; busy: boolean }) {
  const [code, setCode] = useState("");
  const ok = /^\d{4}$/.test(code);
  return (
    <Modal open={open} onClose={() => { setCode(""); onClose(); }} title={`Board ${name}`}>
      <div className="space-y-3">
        <p className="text-sm text-muted">Ask the passenger for the 4-digit code shown in their app.</p>
        <Field label="Boarding code">{(id) => <Input id={id} inputMode="numeric" maxLength={4} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />}</Field>
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => { setCode(""); onClose(); }}>Cancel</Button><Button disabled={!ok} busy={busy} onClick={() => { onSubmit(code); setCode(""); }}>Mark boarded</Button></div>
      </div>
    </Modal>
  );
}

/** Driver: on/off duty. Auto-dispatch and admin assignment only consider drivers who are available. */
export function AvailabilityToggle() {
  const t = useT();
  const q = useQuery({ queryKey: ["availability"], queryFn: () => api<{ available: boolean; upcoming_trips: number }>("/me/availability"), refetchInterval: 60_000 });
  const set = useAction((available: boolean) => api("/me/availability", { method: "PUT", body: { available } }), { invalidate: [["availability"]] });
  if (!q.data) return null;
  const on = q.data.available;
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface p-3">
      <div><p className="font-medium">{on ? t("availOn") : t("availOff")}</p>
        <p className="text-sm text-muted">{on ? t("availOnHint") : t("availOffHint")}{q.data.upcoming_trips > 0 ? ` You have ${q.data.upcoming_trips} upcoming trip${q.data.upcoming_trips > 1 ? "s" : ""}.` : ""}</p></div>
      <Button variant={on ? "secondary" : "primary"} busy={set.isPending} aria-pressed={on} onClick={() => set.mutate(!on)}>{on ? t("goOff") : t("goAvail")}</Button>
      {!on && q.data.upcoming_trips > 0 && <div className="basis-full"><Notice tone="warn">Trips already assigned to you stay yours. Tell your dispatcher if you cannot do them.</Notice></div>}
    </div>
  );
}
