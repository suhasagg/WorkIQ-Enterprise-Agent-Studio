"use client";
import clsx from "clsx";
import { ShieldAlert, TrendingDown, TrendingUp } from "lucide-react";
import { useState } from "react";
import { Line, LineChart, ReferenceLine, ResponsiveContainer } from "recharts";
import { num, pct } from "@/lib/format";
import { Badge, RiskBadge, SimulatedBadge, StatusBadge } from "./ui";

type Citation = { n: number; title: string; heading?: string; page?: number | null; snippet: string; filename: string; highlights?: string[]; score?: number };

function highlight(text: string, terms: string[] = []) {
  const words = terms.filter((t) => t.length > 2).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!words.length) return text;
  const re = new RegExp(`\\b(${words.join("|")})`, "gi");
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i} className="rounded bg-warn/25 px-0.5 text-fg">{part}</mark> : part));
}

function Marks({ nums, onCite }: { nums: number[]; onCite: (n: number) => void }) {
  return (
    <>
      {nums.map((n) => (
        <button key={n} onClick={() => onCite(n)} aria-label={`Show source ${n}`}
          className="ml-1 rounded bg-accent/10 px-1 align-baseline font-mono text-xs text-accent hover:bg-accent/20">[{n}]</button>
      ))}
    </>
  );
}

function Kpis({ items }: { items: any[] }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
      {items.map((k) => {
        const good = !k.off_target;
        const Trend = k.wow_pct >= 0 ? TrendingUp : TrendingDown;
        return (
          <div key={k.metric} className={clsx("rounded-lg border p-3", k.anomaly ? "border-bad/40 bg-bad/5" : "border-line")}>
            <div className="flex items-start justify-between gap-2">
              <div className="text-xs font-medium text-muted">{k.label}</div>
              {k.anomaly && <Badge tone="bad">Anomaly · {k.severity}</Badge>}
            </div>
            <div className="mt-1 flex items-end justify-between gap-2">
              <div>
                <span className="text-lg font-semibold tabular-nums">{num(k.latest)}</span>
                <span className="ml-1 text-xs text-muted">{k.unit}</span>
                <div className={clsx("flex items-center gap-1 text-xs", good ? "text-ok" : "text-bad")}>
                  <Trend className="h-3 w-3" aria-hidden />
                  {k.wow_pct > 0 ? "+" : ""}{k.wow_pct}% WoW · target {num(k.target)}
                </div>
              </div>
              <div className="h-10 w-24" aria-hidden>
                <ResponsiveContainer>
                  <LineChart data={k.series}>
                    {k.target != null && <ReferenceLine y={k.target} stroke="rgb(var(--muted))" strokeDasharray="3 3" />}
                    <Line dataKey="value" dot={false} strokeWidth={1.75} stroke={good ? "rgb(var(--ok))" : "rgb(var(--bad))"} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Section({ s, onCite }: { s: any; onCite: (n: number) => void }) {
  const items: any[] = s.items || [];
  if (!items.length) return <p className="text-sm text-muted">Nothing to report.</p>;
  if (s.kind === "kpis") return <Kpis items={items} />;
  if (s.kind === "tickets")
    return (
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px]">
          <thead><tr><th className="th">Ticket</th><th className="th">Customer</th><th className="th">Priority</th><th className="th">Age</th><th className="th">SLA</th></tr></thead>
          <tbody className="divide-y divide-line">
            {items.map((t) => (
              <tr key={t.id}>
                <td className="td"><span className="font-mono text-xs">{t.id}</span> {t.subject}</td>
                <td className="td">{t.customer}</td>
                <td className="td uppercase">{t.priority}</td>
                <td className="td tabular-nums">{t.age_hours} h</td>
                <td className="td">{t.sla_breached ? <Badge tone="bad">Breached ({t.sla_hours} h)</Badge> : <Badge tone="ok">Within SLA</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  if (s.kind === "action_items")
    return (
      <ul className="space-y-2">
        {items.map((a) => (
          <li key={a.id} className="rounded-lg border border-line p-3 text-sm">
            <div className="font-medium">{a.task}</div>
            <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-muted">
              <span>Owner: <span className="text-fg">{a.owner || "unassigned"}</span></span>
              <span>Due: <span className="text-fg">{a.due_date || "not stated"}</span></span>
            </div>
            {a.quote && <blockquote className="mt-1.5 border-l-2 border-line pl-2 text-xs italic text-muted">“{a.quote}”</blockquote>}
          </li>
        ))}
      </ul>
    );
  if (s.kind === "actions")
    return (
      <ul className="space-y-2">
        {items.map((a, i) => (
          <li key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-line p-3 text-sm">
            <div className="min-w-0 flex-1">
              <div className="font-medium">{a.text}</div>
              <div className="font-mono text-xs text-muted">{a.tool}{a.reference ? ` → ${a.reference}` : ""}{a.decided_by ? ` · decided by ${a.decided_by}` : ""}</div>
              {a.detail && <div className="text-xs text-muted">{a.detail}</div>}
            </div>
            <RiskBadge risk={a.risk} />
            <StatusBadge status={a.status} />
          </li>
        ))}
      </ul>
    );
  return (
    <ul className="space-y-2">
      {items.map((it, i) => (
        <li key={i} className="flex gap-2 text-sm leading-relaxed">
          <span className={clsx("mt-2 h-1.5 w-1.5 shrink-0 rounded-full", it.severity === "high" ? "bg-bad" : it.severity === "medium" ? "bg-warn" : "bg-muted")} aria-hidden />
          <span>
            {it.label && <span className="font-medium">{it.label}: </span>}
            {it.text}
            {it.marks && <Marks nums={(String(it.marks).match(/\d+/g) || []).map(Number)} onCite={onCite} />}
            {it.source && <span className="ml-1.5 font-mono text-xs text-muted">{it.source}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Renders a run result: summary, typed sections, quality scores and the cited evidence. */
export function Report({ result }: { result: any }) {
  const [active, setActive] = useState<number | null>(null);
  const citations: Citation[] = result.citations || [];
  const scores = result.evaluation?.scores || {};
  const cite = (n: number) => {
    setActive(n);
    document.getElementById(`cite-${n}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  return (
    <article className="space-y-5" data-testid="report">
      <header>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold">{result.title}</h2>
          {result.partial && <Badge tone="warn">Partial result</Badge>}
          {result.dry_run && <Badge>Dry run</Badge>}
          {result.simulated_sources && <SimulatedBadge />}
        </div>
        {result.summary && <p className="mt-1 text-sm leading-relaxed text-muted">{result.summary}</p>}
      </header>

      {(result.sections || []).map((s: any, i: number) => (
        <section key={i}>
          <h3 className="mb-2 text-sm font-semibold">{s.title}</h3>
          <Section s={s} onCite={cite} />
        </section>
      ))}

      {!!result.guardrails?.length && (
        <section>
          <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold"><ShieldAlert className="h-4 w-4 text-warn" aria-hidden /> Guardrail events</h3>
          <ul className="space-y-1.5">
            {result.guardrails.map((g: any, i: number) => (
              <li key={i} className="rounded-lg border border-warn/30 bg-warn/5 p-2 text-xs">
                <span className="font-medium">{String(g.type).replace(/_/g, " ")}</span> — {g.action}
                {g.document ? ` (${g.document})` : ""}{g.flags?.length ? ` · ${g.flags.join(", ")}` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      {result.evaluation && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">Quality checks</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(scores).map(([k, v]) => (
              <Badge key={k} tone={(v as number) >= 0.8 ? "ok" : "warn"}>{k.replace(/_/g, " ")} {pct(v as number)}</Badge>
            ))}
            <Badge>{result.evaluation.claims_checked} claims checked against evidence</Badge>
          </div>
          <p className="mt-1.5 text-xs text-muted">{result.evaluation.method}</p>
        </section>
      )}

      {!!citations.length && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">Sources</h3>
          <ol className="space-y-2">
            {citations.map((c) => (
              <li key={c.n} id={`cite-${c.n}`} className={clsx("rounded-lg border p-3 text-sm transition", active === c.n ? "border-accent bg-accent/5" : "border-line")}>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-mono text-xs text-accent">[{c.n}]</span>
                  <span className="font-medium">{c.title}</span>
                  {c.heading && <span className="text-xs text-muted">§ {c.heading}</span>}
                  {c.page != null && <span className="text-xs text-muted">p. {c.page}</span>}
                  <span className="ml-auto font-mono text-xs text-muted">{c.filename}</span>
                </div>
                <p className="mt-1 text-xs leading-relaxed text-muted">{highlight(c.snippet, c.highlights)}</p>
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>
  );
}

export { highlight };
