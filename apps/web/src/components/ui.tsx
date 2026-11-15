import { forwardRef, useEffect, useId, useRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { AlertTriangle, CheckCircle2, Info, Loader2, X } from "lucide-react";
import { create } from "zustand";

const cx = (...a: (string | false | null | undefined)[]) => a.filter(Boolean).join(" ");
export { cx };

type Variant = "primary" | "secondary" | "danger" | "ghost";
const variants: Record<Variant, string> = {
  primary: "bg-accent text-accentfg hover:opacity-90",
  secondary: "bg-surface text-ink border border-line hover:bg-surface2",
  danger: "bg-bad text-white hover:opacity-90",
  ghost: "text-ink hover:bg-surface2",
};
export function Button({ variant = "primary", busy, className, children, disabled, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button {...p} type={p.type ?? "button"} disabled={disabled || busy}
      className={cx("inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50", variants[variant], className)}>
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

const fieldCls = "w-full min-h-10 rounded-lg border border-line bg-surface px-3 text-sm text-ink placeholder:text-muted disabled:opacity-60";
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...p }, ref) {
  return <input ref={ref} {...p} className={cx(fieldCls, className)} />;
});
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, ...p }, ref) {
  return <select ref={ref} {...p} className={cx(fieldCls, className)} />;
});
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...p }, ref) {
  return <textarea ref={ref} {...p} className={cx(fieldCls, "py-2", className)} />;
});

export function Field({ label, error, hint, children }: { label: string; error?: string; hint?: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-sm font-medium">{label}</label>
      {children(id)}
      {hint && !error && <p className="text-xs text-muted">{hint}</p>}
      {error && <p role="alert" className="text-xs text-bad">{error}</p>}
    </div>
  );
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-xl border border-line bg-surface", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const tones = { ok: "bg-oksoft text-ok", warn: "bg-warnsoft text-warn", bad: "bg-badsoft text-bad", info: "bg-infosoft text-info", neutral: "bg-surface2 text-muted" };
export type Tone = keyof typeof tones;
export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", tones[tone])}>{children}</span>;
}

const statusTone: Record<string, Tone> = {
  CONFIRMED: "ok", COMPLETED: "ok", DRIVER_ACCEPTED: "ok", IN_PROGRESS: "info", PROPOSED: "neutral", DRAFT: "neutral", PENDING_DRIVER_ACCEPTANCE: "warn",
  REQUIRES_REASSIGNMENT: "bad", CANCELLED: "bad", open: "warn", allocated: "ok", cancelled: "bad", requested: "warn", approved: "ok", rejected: "bad",
  acknowledged: "info", resolved: "ok", raised: "bad", delivered: "ok", sent: "info", pending: "warn", failed: "bad", skipped: "neutral",
  available: "ok", assigned_empty: "info", partially_filled: "info", full: "warn", unavailable: "bad", under_maintenance: "bad", pending_driver_acceptance: "warn",
};
export const StatusBadge = ({ status }: { status: string }) => <Badge tone={statusTone[status] ?? "neutral"}>{status.replace(/_/g, " ").toLowerCase()}</Badge>;

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <div role="status" className="flex items-center justify-center gap-2 p-8 text-sm text-muted"><Loader2 className="size-4 animate-spin" aria-hidden />{label}…</div>;
}
export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : "Something went wrong";
  return (
    <div role="alert" className="flex items-start gap-3 rounded-lg border border-line bg-badsoft p-3 text-sm text-bad">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="flex-1">{msg}</div>
      {retry && <button onClick={retry} className="font-medium underline">Retry</button>}
    </div>
  );
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-line p-8 text-center"><p className="font-medium">{title}</p>{children && <div className="mt-1 text-sm text-muted">{children}</div>}</div>;
}
export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "bad" | "ok"; children: ReactNode }) {
  const Icon = tone === "ok" ? CheckCircle2 : tone === "info" ? Info : AlertTriangle;
  return <div className={cx("flex items-start gap-2 rounded-lg p-3 text-sm", tones[tone])}><Icon className="mt-0.5 size-4 shrink-0" aria-hidden /><div>{children}</div></div>;
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} onClose={onClose} onCancel={onClose} onClick={(e) => { if (e.target === ref.current) onClose(); }}
      className={cx("m-auto w-[calc(100%-2rem)] rounded-2xl border border-line bg-surface p-0 text-ink shadow-xl backdrop:bg-black/40", wide ? "max-w-3xl" : "max-w-lg")}>
      {open && (
        <div className="flex max-h-[90vh] flex-col">
          <header className="flex items-center justify-between border-b border-line px-5 py-3">
            <h2 className="font-semibold">{title}</h2>
            <button aria-label="Close" onClick={onClose} className="rounded-md p-1 hover:bg-surface2"><X className="size-5" /></button>
          </header>
          <div className="overflow-y-auto p-5">{children}</div>
        </div>
      )}
    </dialog>
  );
}

export function Confirm({ open, title, message, confirmLabel = "Confirm", danger, busy, onConfirm, onClose }:
  { open: boolean; title: string; message: ReactNode; confirmLabel?: string; danger?: boolean; busy?: boolean; onConfirm: () => void; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <div className="space-y-4">
        <div className="text-sm">{message}</div>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button variant={danger ? "danger" : "primary"} busy={busy} onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </div>
    </Modal>
  );
}

// ---- toasts --------------------------------------------------------------------------------------------
interface Toast { id: number; tone: "ok" | "bad" | "info"; text: string }
export const useToasts = create<{ items: Toast[]; push: (tone: Toast["tone"], text: string) => void; drop: (id: number) => void }>((set) => ({
  items: [],
  push: (tone, text) => {
    const id = Date.now() + Math.random();
    set((s) => ({ items: [...s.items, { id, tone, text }] }));
    setTimeout(() => set((s) => ({ items: s.items.filter((t) => t.id !== id) })), 5000);
  },
  drop: (id) => set((s) => ({ items: s.items.filter((t) => t.id !== id) })),
}));
export const toast = { ok: (t: string) => useToasts.getState().push("ok", t), bad: (t: string) => useToasts.getState().push("bad", t), info: (t: string) => useToasts.getState().push("info", t) };
export function Toaster() {
  const { items, drop } = useToasts();
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} role="status" className={cx("pointer-events-auto flex max-w-md items-center gap-3 rounded-lg border border-line px-4 py-2.5 text-sm shadow-lg", t.tone === "ok" ? "bg-oksoft text-ok" : t.tone === "bad" ? "bg-badsoft text-bad" : "bg-infosoft text-info")}>
          <span>{t.text}</span><button aria-label="Dismiss" onClick={() => drop(t.id)}><X className="size-4" /></button>
        </div>
      ))}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div><h1 className="text-xl font-semibold">{title}</h1>{subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}</div>
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
    </div>
  );
}

export function Pager({ page, size, total, onPage }: { page: number; size: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="flex items-center justify-between pt-3 text-sm text-muted">
      <span>{total} total</span>
      <div className="flex items-center gap-2">
        <Button variant="secondary" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
        <span>Page {page} of {pages}</span>
        <Button variant="secondary" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button>
      </div>
    </div>
  );
}

export interface Column<T> { key: string; header: string; render?: (row: T) => ReactNode; className?: string }
export function DataTable<T extends { id?: string | number }>({ rows, columns, onRowClick, caption }: { rows: T[]; columns: Column<T>[]; onRowClick?: (r: T) => void; caption?: string }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-line">
      <table className="w-full text-left text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="bg-surface2 text-xs uppercase tracking-wide text-muted">
          <tr>{columns.map((c) => <th key={c.key} scope="col" className={cx("px-3 py-2 font-medium", c.className)}>{c.header}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-line bg-surface">
          {rows.map((r, i) => (
            <tr key={r.id ?? i} onClick={onRowClick ? () => onRowClick(r) : undefined} className={cx(onRowClick && "cursor-pointer hover:bg-surface2")}>
              {columns.map((c) => <td key={c.key} className={cx("px-3 py-2.5 align-top", c.className)}>{c.render ? c.render(r) : String((r as Record<string, unknown>)[c.key] ?? "—")}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
