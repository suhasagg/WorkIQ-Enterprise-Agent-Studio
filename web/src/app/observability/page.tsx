"use client";
import Link from "next/link";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import useSWR from "swr";
import { Timeline } from "@/components/timeline";
import { Async, Badge, Card, Empty, PageHeader, Stat, useAction } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { ago, ms, num, pct, title, usd } from "@/lib/format";

const tip = { contentStyle: { background: "rgb(var(--surface))", border: "1px solid rgb(var(--line))", borderRadius: 8, fontSize: 12 }, cursor: { fill: "rgb(var(--raised))" } };

function Audit() {
  const { data, error } = useSWR<any[]>("/api/audit?limit=40");
  const [verdict, setVerdict] = useState<any>(null);
  const { busy, run } = useAction();
  return (
    <Card title="Audit log (hash-chained)" pad={false} action={
      <div className="flex items-center gap-2">
        {verdict && <Badge tone={verdict.valid ? "ok" : "bad"}>{verdict.valid ? `Chain intact · ${verdict.events} events` : `Chain broken at #${verdict.broken_at_seq ?? "?"}`}</Badge>}
        <button className="btn" disabled={!!busy} onClick={() => run("verify", async () => setVerdict(await (await fetch("/api/audit/verify", { credentials: "include" })).json()))}>Verify chain</button>
      </div>}>
      <Async data={data} error={error} empty={<Empty title="No audit events" />}>
        {(rows) => (
          <div className="max-h-80 overflow-auto"><table className="w-full min-w-[640px]">
            <thead><tr><th className="th">#</th><th className="th">Action</th><th className="th">Actor</th><th className="th">Decision</th><th className="th">Resource</th><th className="th">Hash</th><th className="th">When</th></tr></thead>
            <tbody className="divide-y divide-line">{rows.map((e) => (
              <tr key={e.seq}><td className="td tabular-nums">{e.seq}</td><td className="td font-mono text-xs">{e.action}</td><td className="td text-xs">{e.actor}</td><td className="td text-xs">{e.decision}</td><td className="td max-w-[14rem] truncate font-mono text-xs">{e.resource}</td><td className="td font-mono text-xs text-muted">{e.hash.slice(0, 10)}</td><td className="td text-xs text-muted">{ago(e.ts)}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </Async>
    </Card>
  );
}

export default function Observability() {
  const { can } = useAuth();
  const [hours, setHours] = useState(24);
  const { data, error, mutate } = useSWR<any>(`/api/observability?hours=${hours}`, { refreshInterval: 5000 });
  return (
    <>
      <PageHeader title="AI Observability Dashboard" subtitle="Latency, tokens, tool reliability, failures, guardrails and quality, aggregated from persisted run events and tool telemetry.">
        <label className="sr-only" htmlFor="window">Time window</label>
        <select id="window" className="input !w-auto" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          <option value={1}>Last hour</option><option value={24}>Last 24 hours</option><option value={168}>Last 7 days</option>
        </select>
        <a className="btn" href="/metrics" target="_blank" rel="noreferrer">Prometheus metrics</a>
      </PageHeader>
      <Async data={data} error={error} retry={() => mutate()}>
        {(o) => (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-6">
              <Stat label="Run latency p50" value={ms(o.run_latency_ms.p50)} sub={`${o.run_latency_ms.n} finished runs · wall clock`} />
              <Stat label="Run latency p95" value={ms(o.run_latency_ms.p95)} sub={`max ${ms(o.run_latency_ms.max)} · includes time waiting for approval`} />
              <Stat label="Groundedness" value={pct(o.quality.groundedness)} sub={`${o.quality.n} evaluated runs`} tone={o.quality.groundedness >= 0.9 ? "ok" : o.quality.groundedness == null ? undefined : "warn"} />
              <Stat label="Tool success" value={pct(o.quality.tool_success)} />
              <Stat label="Policy compliance" value={pct(o.quality.policy_compliance)} />
              <Stat label="Retrieval" value={ms(o.retrieval.avg_ms)} sub={`${o.retrieval.queries} queries · ${o.retrieval.avg_candidates ?? 0} avg candidates`} />
            </div>

            <div className="grid gap-4 xl:grid-cols-2">
              <Card title={`Runs per ${o.bucket}`}>
                {o.series.length ? (
                  <div className="h-56"><ResponsiveContainer><BarChart data={o.series.map((s: any) => ({ ...s, label: new Date(s.t).toLocaleString("en-GB", o.bucket === "day" ? { day: "2-digit", month: "short" } : { hour: "2-digit", minute: "2-digit" }) }))}>
                    <CartesianGrid stroke="rgb(var(--line))" vertical={false} /><XAxis dataKey="label" tick={{ fontSize: 11, fill: "rgb(var(--muted))" }} /><YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "rgb(var(--muted))" }} width={28} /><Tooltip {...tip} />
                    <Bar dataKey="completed" stackId="a" fill="rgb(var(--ok))" name="Completed" /><Bar dataKey="failed" stackId="a" fill="rgb(var(--bad))" name="Failed" />
                  </BarChart></ResponsiveContainer></div>
                ) : <Empty title="No runs in this window" />}
              </Card>
              <Card title="Average node latency (ms)">
                {o.nodes.length ? (
                  <div className="h-56"><ResponsiveContainer><BarChart data={o.nodes.slice(0, 12)} layout="vertical" margin={{ left: 40 }}>
                    <CartesianGrid stroke="rgb(var(--line))" horizontal={false} /><XAxis type="number" tick={{ fontSize: 11, fill: "rgb(var(--muted))" }} /><YAxis type="category" dataKey="node" width={130} tick={{ fontSize: 10, fill: "rgb(var(--muted))" }} /><Tooltip {...tip} />
                    <Bar dataKey="avg_ms" fill="rgb(var(--accent))" name="avg ms" />
                  </BarChart></ResponsiveContainer></div>
                ) : <Empty title="No node timings yet" />}
              </Card>
            </div>

            <div className="grid gap-4 xl:grid-cols-2">
              <Card title="MCP tools" pad={false}>
                {o.tools.length ? (
                  <div className="overflow-x-auto"><table className="w-full min-w-[520px]">
                    <thead><tr><th className="th">Tool</th><th className="th">Calls</th><th className="th">OK</th><th className="th">Failed</th><th className="th">Held</th><th className="th">Denied</th><th className="th">Retries</th><th className="th">Avg</th></tr></thead>
                    <tbody className="divide-y divide-line">{o.tools.map((t: any) => (
                      <tr key={`${t.server}.${t.tool}`}><td className="td font-mono text-xs">{t.server}.{t.tool}</td><td className="td tabular-nums">{t.calls}</td><td className="td tabular-nums">{t.ok}</td><td className="td tabular-nums">{t.failed}</td><td className="td tabular-nums">{t.held}</td><td className="td tabular-nums">{t.denied}</td><td className="td tabular-nums">{t.retries}</td><td className="td tabular-nums">{ms(t.avg_ms)}</td></tr>
                    ))}</tbody>
                  </table></div>
                ) : <Empty title="No tool calls in this window" />}
              </Card>
              <Card title="Model usage" pad={false}>
                {o.llm.length ? (
                  <div className="overflow-x-auto"><table className="w-full min-w-[480px]">
                    <thead><tr><th className="th">Purpose</th><th className="th">Model</th><th className="th">Calls</th><th className="th">Tokens in / out</th><th className="th">Cost</th></tr></thead>
                    <tbody className="divide-y divide-line">{o.llm.map((l: any) => (
                      <tr key={`${l.model}-${l.purpose}`}><td className="td">{title(l.purpose)}</td><td className="td font-mono text-xs">{l.model}</td><td className="td tabular-nums">{l.calls}</td><td className="td tabular-nums">{num(l.prompt_tokens)} / {num(l.completion_tokens)}{l.estimated && <span className="text-xs text-muted"> est.</span>}</td><td className="td tabular-nums">{usd(l.cost_usd)}</td></tr>
                    ))}</tbody>
                  </table></div>
                ) : <Empty title="No model calls in this window" />}
                <p className="border-t border-line px-4 py-2 text-xs text-muted">{o.cost_note}</p>
              </Card>
            </div>

            <div className="grid gap-4 xl:grid-cols-2">
              <Card title="Failures, retries and replans" pad={false}>
                {o.failures.length ? <div className="max-h-72 overflow-auto p-2">{o.failures.map((f: any) => (
                  <Link key={`${f.run_id}-${f.seq}`} href={`/runs/${f.run_id}`} className="block rounded hover:bg-raised"><Timeline events={[f]} /></Link>
                ))}</div> : <Empty title="No failures in this window" hint="Use “Failure simulation” in the workflow launcher to see retry and replanning." />}
              </Card>
              <Card title="Guardrail events" pad={false}>
                {o.guardrails.length ? (
                  <ul className="max-h-72 divide-y divide-line overflow-auto">{o.guardrails.map((g: any, i: number) => (
                    <li key={i} className="px-4 py-2 text-sm"><Link href={`/runs/${g.run_id}`} className="hover:underline"><span className="font-medium">{title(g.kind)}</span></Link><span className="ml-2 text-xs text-muted">{g.payload?.action} · {ago(g.ts)}</span></li>
                  ))}</ul>
                ) : <Empty title="No guardrail events in this window" />}
              </Card>
            </div>

            <Card title="Tracing">
              <div className="flex flex-wrap gap-2 text-sm">
                <Badge tone={o.telemetry.opentelemetry.enabled ? "ok" : "muted"}>OpenTelemetry spans: {o.telemetry.opentelemetry.enabled ? `${num(o.telemetry.opentelemetry.spans_created)} created` : "off"}</Badge>
                <Badge tone={o.telemetry.opentelemetry.exporting ? "ok" : "muted"}>OTLP export: {o.telemetry.opentelemetry.exporting ? "on" : "no endpoint configured"}</Badge>
                <Badge tone={o.telemetry.langsmith.tracing ? "ok" : "muted"}>LangSmith: {o.telemetry.langsmith.tracing ? "tracing" : "not configured"}</Badge>
              </div>
              <p className="mt-2 text-xs text-muted">Spans are created for every graph node and tool call. They are exported only when OTEL_EXPORTER_OTLP_ENDPOINT is set; LangSmith tracing turns on when LANGSMITH_API_KEY is set.</p>
            </Card>
            {can("audit:read") && <Audit />}
          </div>
        )}
      </Async>
    </>
  );
}
