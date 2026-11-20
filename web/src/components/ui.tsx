"use client";
import clsx from "clsx";
import { AlertTriangle, Inbox, Loader2, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

const TONES: Record<string, string> = {
  ok: "border-ok/30 bg-ok/10 text-ok",
  warn: "border-warn/30 bg-warn/10 text-warn",
  bad: "border-bad/30 bg-bad/10 text-bad",
  info: "border-info/30 bg-info/10 text-info",
  accent: "border-accent/30 bg-accent/10 text-accent",
  muted: "border-line bg-raised text-muted",
};

export function Badge({ tone = "muted", children, title }: { tone?: keyof typeof TONES | string; children: React.ReactNode; title?: string }) {
  return (
    <span title={title} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium", TONES[tone] || TONES.muted)}>
      {children}
    </span>
  );
}

const STATUS: Record<string, [string, string]> = {
  completed: ["ok", "Completed"], ok: ["ok", "OK"], approved: ["ok", "Approved"], connected: ["ok", "Connected"], indexed: ["ok", "Indexed"],
  executed: ["ok", "Executed"], edited: ["ok", "Approved with edits"], allow: ["ok", "Allowed"], passed: ["ok", "Passed"],
  running: ["info", "Running"], queued: ["info", "Queued"], processing: ["info", "Processing"], connecting: ["info", "Connecting"],
  waiting_approval: ["warn", "Waiting for approval"], pending: ["warn", "Pending"], paused: ["warn", "Paused"],
  approval_required: ["warn", "Approval required"], needs_approval: ["warn", "Awaiting approval"], changes_requested: ["warn", "Changes requested"],
  not_configured: ["muted", "Not configured"], idle: ["muted", "Idle"], cancelled: ["muted", "Cancelled"], skipped: ["muted", "Skipped"], disabled: ["muted", "Disabled"],
  superseded: ["muted", "Superseded"], dry_run: ["muted", "Dry run"], proposed: ["muted", "Proposed"],
  failed: ["bad", "Failed"], error: ["bad", "Error"], denied: ["bad", "Denied"], rejected: ["bad", "Rejected"], deny: ["bad", "Denied"],
};

export function StatusBadge({ status }: { status?: string | null }) {
  const [tone, label] = STATUS[status || ""] || ["muted", (status || "unknown").replace(/_/g, " ")];
  return (
    <Badge tone={tone}>
      {(status === "running" || status === "processing") && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
      {label}
    </Badge>
  );
}

export const RiskBadge = ({ risk }: { risk?: string }) => (
  <Badge tone={risk === "high" ? "bad" : risk === "medium" ? "warn" : "muted"}>{risk || "low"} risk</Badge>
);

export const SimulatedBadge = () => (
  <Badge tone="accent" title="Local demo MCP server with synthetic data. Not a live integration.">Simulated</Badge>
);

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 p-8 text-sm text-muted">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg border border-bad/30 bg-bad/10 p-3 text-sm text-bad">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div className="flex-1">{error instanceof Error ? error.message : String(error)}</div>
      {retry && <button className="btn" onClick={retry}>Retry</button>}
    </div>
  );
}

export function Empty({ title, hint, children }: { title: string; hint?: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 p-8 text-center">
      <Inbox className="h-6 w-6 text-muted" aria-hidden />
      <div className="text-sm font-medium">{title}</div>
      {hint && <div className="max-w-sm text-xs text-muted">{hint}</div>}
      {children}
    </div>
  );
}

/** Standard async section wrapper: loading, error and (optional) empty states. */
export function Async<T>({ data, error, retry, empty, children }: {
  data: T | undefined; error: unknown; retry?: () => void; empty?: React.ReactNode; children: (d: T) => React.ReactNode;
}) {
  if (error && data === undefined) return <ErrorBox error={error} retry={retry} />;
  if (data === undefined) return <Spinner />;
  if (empty && Array.isArray(data) && data.length === 0) return <>{empty}</>;
  return <>{children(data)}</>;
}

export function Card({ title, action, children, className, pad = true }: {
  title?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode; className?: string; pad?: boolean;
}) {
  return (
    <section className={clsx("card", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </header>
      )}
      <div className={pad ? "p-4" : ""}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "ok" | "warn" | "bad" }) {
  return (
    <div className="card p-4">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className={clsx("mt-1 text-2xl font-semibold tabular-nums", tone === "ok" && "text-ok", tone === "warn" && "text-warn", tone === "bad" && "text-bad")}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-0.5 max-w-3xl text-sm text-muted">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange, label }: { tabs: { id: T; label: string; count?: number }[]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={clsx("-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium", value === t.id ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg")}>
          {t.label}
          {t.count != null && <span className="ml-1.5 rounded-full bg-raised px-1.5 text-xs">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 sm:p-8" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} className={clsx("card w-full shadow-2xl", wide ? "max-w-3xl" : "max-w-lg")}>
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button className="btn !p-1" aria-label="Close dialog" onClick={onClose}><X className="h-4 w-4" /></button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export const Json = ({ value }: { value: unknown }) => (
  <pre className="max-h-72 overflow-auto rounded-lg bg-raised p-3 font-mono text-xs leading-relaxed">{JSON.stringify(value, null, 2)}</pre>
);

// ---- toasts
type Toast = { id: number; text: string; tone: "ok" | "bad" };
const ToastCtx = createContext<(text: string, tone?: "ok" | "bad") => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: "ok" | "bad" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} data-testid="toast" className={clsx("pointer-events-auto rounded-lg border bg-surface px-3 py-2 text-sm shadow-lg", t.tone === "ok" ? "border-ok/40" : "border-bad/40 text-bad")}>{t.text}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/** Runs an async action with pending state and toast-reported errors. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const run = useCallback(async <T,>(key: string, fn: () => Promise<T>, okText?: string): Promise<T | undefined> => {
    setBusy(key);
    try {
      const out = await fn();
      if (okText) toast(okText);
      return out;
    } catch (e) {
      toast(e instanceof Error ? e.message : "Request failed", "bad");
    } finally {
      setBusy(null);
    }
  }, [toast]);
  return { busy, run };
}
