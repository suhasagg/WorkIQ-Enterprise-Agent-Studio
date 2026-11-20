"use client";
import { Ban, Pause, Play, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, ms, num, title } from "@/lib/format";
import { TERMINAL, useRun } from "@/lib/useRun";
import { GraphView, nodeStates, type Topology } from "./graph";
import { Report } from "./report";
import { Timeline } from "./timeline";
import { Async, Badge, Card, ErrorBox, Json, RiskBadge, Spinner, StatusBadge, Tabs, useAction } from "./ui";

export function RunControls({ run, onChange }: { run: any; onChange: () => void }) {
  const { can } = useAuth();
  const { busy, run: act } = useAction();
  if (!can("agent:run")) return null;
  const call = (verb: string, ok: string) => act(verb, async () => { await post(`/api/runs/${run.id}/${verb}`); onChange(); }, ok);
  const s = run.status;
  return (
    <div className="flex flex-wrap gap-2">
      {(s === "running" || s === "queued") && <button className="btn" disabled={!!busy} onClick={() => call("pause", "Pause requested; the run stops at the next step boundary")}><Pause className="h-4 w-4" /> Pause</button>}
      {s === "paused" && <button className="btn btn-primary" disabled={!!busy} onClick={() => call("resume", "Run resumed from its checkpoint")}><Play className="h-4 w-4" /> Resume</button>}
      {!TERMINAL.includes(s) && <button className="btn btn-danger" disabled={!!busy} onClick={() => call("cancel", "Run cancelled")}><Ban className="h-4 w-4" /> Cancel</button>}
      {(s === "failed" || s === "cancelled") && <button className="btn btn-primary" disabled={!!busy} onClick={() => call("retry", "Retrying from the last checkpoint")}><RotateCcw className="h-4 w-4" /> Retry</button>}
    </div>
  );
}

function Plan({ plan }: { plan: any[] }) {
  return (
    <ol className="space-y-1.5">
      {plan.map((s, i) => (
        <li key={s.id} className="flex items-start gap-2 text-sm">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-raised text-xs">{i + 1}</span>
          <div className="min-w-0 flex-1">
            <div>{s.title} <span className="text-xs text-muted">· {s.agent}{s.optional ? " · optional" : ""}{s.attempts > 1 ? ` · ${s.attempts} attempts` : ""}</span></div>
            {(s.summary || s.error) && <div className="text-xs text-muted">{s.error || s.summary}</div>}
          </div>
          <StatusBadge status={s.status} />
        </li>
      ))}
    </ol>
  );
}

function Telemetry({ runId }: { runId: string }) {
  const { data, error } = useSWR(`/api/runs/${runId}/telemetry`);
  return (
    <Async data={data} error={error}>
      {(t: any) => (
        <div className="space-y-4">
          <div>
            <h3 className="mb-1 text-sm font-semibold">MCP tool calls ({t.tools.length})</h3>
            {t.tools.length ? (
              <div className="overflow-x-auto"><table className="w-full min-w-[560px]">
                <thead><tr><th className="th">Tool</th><th className="th">Node</th><th className="th">Decision</th><th className="th">Status</th><th className="th">Attempt</th><th className="th">Latency</th></tr></thead>
                <tbody className="divide-y divide-line">{t.tools.map((x: any) => (
                  <tr key={x.id}><td className="td font-mono text-xs">{x.server}.{x.tool}{x.is_write && <span className="ml-1 text-warn">write</span>}</td><td className="td text-xs">{x.node}</td><td className="td text-xs">{x.decision}</td><td className="td"><StatusBadge status={x.status} /></td><td className="td tabular-nums">{x.attempt}</td><td className="td tabular-nums">{ms(x.latency_ms)}</td></tr>
                ))}</tbody>
              </table></div>
            ) : <p className="text-sm text-muted">This run made no tool calls.</p>}
          </div>
          <div>
            <h3 className="mb-1 text-sm font-semibold">Model calls ({t.llm.length})</h3>
            <div className="overflow-x-auto"><table className="w-full min-w-[480px]">
              <thead><tr><th className="th">Purpose</th><th className="th">Node</th><th className="th">Model</th><th className="th">Tokens in / out</th><th className="th">Latency</th></tr></thead>
              <tbody className="divide-y divide-line">{t.llm.map((x: any) => (
                <tr key={x.id}><td className="td">{title(x.purpose)}</td><td className="td text-xs">{x.node}</td><td className="td font-mono text-xs">{x.model}</td><td className="td tabular-nums">{x.prompt_tokens} / {x.completion_tokens}{x.estimated && <span className="text-xs text-muted"> est.</span>}</td><td className="td tabular-nums">{ms(x.latency_ms)}</td></tr>
              ))}</tbody>
            </table></div>
          </div>
          {!!t.actions.length && (
            <div>
              <h3 className="mb-1 text-sm font-semibold">Executed actions ({t.actions.length})</h3>
              <ul className="space-y-1 text-xs">{t.actions.map((a: any) => (
                <li key={a.idempotency_key} className="flex flex-wrap items-center gap-2 rounded border border-line p-2"><span className="font-mono">{a.server}.{a.tool}</span><StatusBadge status={a.status} /><span className="text-muted">attempts {a.attempts} · idempotency key {a.idempotency_key.slice(0, 16)}…</span></li>
              ))}</ul>
            </div>
          )}
        </div>
      )}
    </Async>
  );
}

function StateTab({ runId, status }: { runId: string; status: string }) {
  const { data: st, error } = useSWR([`/api/runs/${runId}/state`, status], ([u]) => fetch(u, { credentials: "include" }).then((r) => r.json()));
  const { data: cps } = useSWR<any[]>([`/api/runs/${runId}/checkpoints`, status], ([u]: [string]) => fetch(u, { credentials: "include" }).then((r) => r.json()));
  return (
    <Async data={st} error={error}>
      {(s: any) => (
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <h3 className="mb-1 text-sm font-semibold">Checkpointed graph state</h3>
            <div className="mb-2 flex flex-wrap gap-2 text-xs">
              <Badge>next: {s.next.length ? s.next.join(", ") : "none (finished)"}</Badge>
              <Badge>replans {s.values.replans ?? 0}</Badge><Badge>revisions {s.values.revisions ?? 0}</Badge>
              <Badge>evidence {s.values.evidence?.length ?? 0}</Badge><Badge>proposed actions {s.values.proposed_actions?.length ?? 0}</Badge>
            </div>
            {!!s.interrupts.length && <div className="mb-2"><div className="label">Pending interrupt</div><Json value={s.interrupts} /></div>}
            <Json value={{ ...s.values, evidence: undefined, findings: undefined, analytics: undefined, extracted: undefined }} />
          </div>
          <div>
            <h3 className="mb-1 text-sm font-semibold">PostgreSQL checkpoints ({cps?.length ?? "…"})</h3>
            <ol className="max-h-[28rem] space-y-1 overflow-auto text-xs">
              {(cps || []).map((c) => (
                <li key={c.checkpoint_id} className="flex items-center gap-2 rounded border border-line px-2 py-1">
                  <span className="w-8 tabular-nums text-muted">#{c.step}</span>
                  <span className="flex-1 truncate">{c.next.length ? `→ ${c.next.join(", ")}` : "end"}</span>
                  <span className="font-mono text-muted">{c.checkpoint_id.slice(0, 13)}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </Async>
  );
}

function Approvals({ approvals }: { approvals: any[] }) {
  if (!approvals.length) return <p className="text-sm text-muted">This run requested no approvals.</p>;
  return (
    <ul className="space-y-2">
      {approvals.map((a) => (
        <li key={a.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-line p-3 text-sm">
          <div className="min-w-0 flex-1">
            <div className="font-medium">{a.title}</div>
            <div className="text-xs text-muted"><span className="font-mono">{a.server}.{a.tool}</span> · round {a.round}{a.decided_by_name ? ` · ${a.decided_by_name}${a.comment ? `: “${a.comment}”` : ""}` : ""}</div>
          </div>
          <RiskBadge risk={a.risk} /><StatusBadge status={a.status} />
          {a.status === "pending" && <Link className="btn btn-primary" href={`/approvals?id=${a.id}`}>Review</Link>}
        </li>
      ))}
    </ul>
  );
}

type Tab = "report" | "timeline" | "graph" | "approvals" | "telemetry" | "state";

/** Full live view of one run: header, controls, graph, streaming timeline, report, approvals, telemetry and state. */
export function RunView({ runId, compact = false }: { runId: string; compact?: boolean }) {
  const { run, events, error, refresh, streamError } = useRun(runId);
  const { data: topology } = useSWR<Topology>("/api/graph/topology");
  const [tab, setTab] = useState<Tab | null>(null);
  const [expand, setExpand] = useState<string | null>(null);
  const [verbose, setVerbose] = useState(false);
  const { states, timings } = useMemo(() => nodeStates(events, run?.status), [events, run?.status]);

  if (error) return <ErrorBox error={error} retry={() => refresh()} />;
  if (!run) return <Spinner label="Loading run" />;
  const pending = run.approvals.filter((a: any) => a.status === "pending").length;
  const current: Tab = tab ?? (run.result ? "report" : "timeline");

  return (
    <div className="space-y-4" data-testid="run-view" data-run-status={run.status}>
      <div className="card p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={run.status} />
              <Badge>{title(run.intent || run.workflow || "classifying")}</Badge>
              {run.options?.dry_run && <Badge>Dry run</Badge>}
              {run.options?.fault_injection && <Badge tone="warn">Fault injection on</Badge>}
              <span className="font-mono text-xs text-muted">{run.id}</span>
            </div>
            <p className="mt-2 text-sm">{run.request}</p>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
              <span>Started {ago(run.started_at || run.created_at)}</span>
              <span>Duration {ms(run.duration_ms)}</span>
              <span>{run.tool_calls} tool calls</span>
              <span>{num(run.tokens_in + run.tokens_out)} tokens</span>
              {run.retry_count > 0 && <span>{run.retry_count} retries</span>}
            </div>
          </div>
          <RunControls run={run} onChange={() => refresh()} />
        </div>
        {run.error && <div className="mt-3"><ErrorBox error={run.error} /></div>}
        {run.status === "waiting_approval" && (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm">
            <span className="flex-1">The graph is paused at <span className="font-mono">interrupt()</span> with its state checkpointed. {pending} action{pending === 1 ? "" : "s"} need a decision before anything is written.</span>
            <Link className="btn btn-primary" href={`/approvals?run=${run.id}`}>Open Approval Center</Link>
          </div>
        )}
        {streamError && <p className="mt-2 text-xs text-warn">Live stream interrupted ({streamError}); reconnecting.</p>}
      </div>

      <Tabs<Tab> label="Run details" value={current} onChange={setTab} tabs={[
        { id: "report", label: "Report" }, { id: "timeline", label: "Timeline", count: events.length }, ...(compact ? [] : [{ id: "graph" as Tab, label: "Graph" }]),
        { id: "approvals", label: "Approvals", count: run.approvals.length }, { id: "telemetry", label: "Telemetry" }, { id: "state", label: "State & checkpoints" },
      ]} />

      {current === "report" && (
        <Card>
          {run.result ? <Report result={run.result} /> : (
            <div className="space-y-3">
              <p className="text-sm text-muted">{TERMINAL.includes(run.status) ? "This run did not produce a report." : "The report appears here when the run finishes. Current plan:"}</p>
              {run.plan?.length ? <Plan plan={run.plan} /> : null}
            </div>
          )}
        </Card>
      )}
      {current === "timeline" && (
        <Card title="Execution timeline" pad={false} action={<label className="flex items-center gap-1.5 text-xs text-muted"><input type="checkbox" checked={verbose} onChange={(e) => setVerbose(e.target.checked)} /> Show node and model events</label>}>
          <div className="grid gap-0 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <div className="max-h-[36rem] overflow-auto p-2"><Timeline events={events} verbose={verbose} /></div>
            <div className="border-t border-line p-4 lg:border-l lg:border-t-0"><h3 className="mb-2 text-sm font-semibold">Plan</h3>{run.plan?.length ? <Plan plan={run.plan} /> : <p className="text-sm text-muted">Planning…</p>}</div>
          </div>
        </Card>
      )}
      {current === "graph" && topology && (
        <Card title={expand ? `Subgraph: ${topology.groups.find((g) => g.id === expand)?.label}` : "LangGraph execution"} action={expand ? <button className="btn" onClick={() => setExpand(null)}>Back to full graph</button> : <span className="text-xs text-muted">Click an agent to open its subgraph</span>}>
          <GraphView topology={topology} states={states} timings={timings} expand={expand} height={520} onSelect={(id) => topology.groups.some((g) => g.id === id) && setExpand(id)} />
        </Card>
      )}
      {current === "approvals" && <Card><Approvals approvals={run.approvals} /></Card>}
      {current === "telemetry" && <Card><Telemetry runId={run.id} /></Card>}
      {current === "state" && <Card><StateTab runId={run.id} status={run.status} /></Card>}
    </div>
  );
}
