"use client";
import clsx from "clsx";
import { FlaskConical, Star } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Async, Badge, Card, Empty, ErrorBox, Json, PageHeader, StatusBadge, useAction } from "@/components/ui";
import { post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, ms, title } from "@/lib/format";

const fmt = (v: unknown) => (typeof v === "number" ? (v <= 1 ? `${(v * 100).toFixed(1)}%` : String(Math.round(v))) : String(v));
const metricKeys = (m: any) => Object.keys(m || {}).filter((k) => typeof m[k] === "number" && k !== "avg_latency_ms");

function RunDetail({ id, onChange }: { id: string; onChange: () => void }) {
  const { can } = useAuth();
  const { data: r, error, mutate } = useSWR<any>(`/api/evals/runs/${id}`, { refreshInterval: (d: any) => (d && ["completed", "failed"].includes(d.status) ? 0 : 700) });
  const { data: ds } = useSWR<any>(r ? `/api/evals/datasets/${r.dataset}` : null);
  const { busy, run } = useAction();
  const [open, setOpen] = useState<string | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (!r) return <Card><div className="p-6 text-sm text-muted">Loading evaluation…</div></Card>;
  const base = r.baseline && r.baseline.id !== r.id ? r.baseline.metrics : null;
  return (
    <div className="space-y-4" data-testid="eval-run">
      <Card title={`${ds?.name || title(r.dataset)} — run ${r.id.slice(-6)}`} action={
        <div className="flex items-center gap-2">
          <StatusBadge status={r.status} />
          {r.status === "completed" && <Badge tone={r.metrics.gate_passed ? "ok" : "bad"}>{r.metrics.gate_passed ? "Quality gate passed" : "Quality gate failed"}</Badge>}
          {r.is_baseline ? <Badge tone="accent"><Star className="h-3 w-3" /> Baseline</Badge> : can("eval:run") && r.status === "completed" && (
            <button className="btn" disabled={!!busy} onClick={() => run("baseline", async () => { await post(`/api/evals/runs/${id}/baseline`); mutate(); onChange(); }, "Set as the baseline for this dataset")}>Set as baseline</button>
          )}
        </div>}>
        {r.error && <ErrorBox error={r.error} />}
        <div className="text-xs text-muted">{r.passed} of {r.total} cases passed · started {ago(r.created_at)}{r.metrics?.avg_latency_ms != null ? ` · avg case ${ms(r.metrics.avg_latency_ms)}` : ""}{r.config?.mode ? ` · retrieval ${r.config.mode}${r.config.rerank === false ? ", no rerank" : ""}` : ""}</div>
        {r.status === "completed" && (
          <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[420px]">
            <thead><tr><th className="th">Metric</th><th className="th">This run</th><th className="th">Threshold</th><th className="th">Baseline</th><th className="th">Change</th><th className="th">Gate</th></tr></thead>
            <tbody className="divide-y divide-line">
              {metricKeys(r.metrics).map((k) => {
                const delta = base && typeof base[k] === "number" ? r.metrics[k] - base[k] : null;
                const gate = r.metrics.gates?.[k];
                return (
                  <tr key={k}>
                    <td className="td">{title(k)}</td><td className="td font-semibold tabular-nums">{fmt(r.metrics[k])}</td>
                    <td className="td tabular-nums text-muted">{ds?.thresholds?.[k] != null ? `≥ ${fmt(ds.thresholds[k])}` : "—"}</td>
                    <td className="td tabular-nums text-muted">{base ? fmt(base[k]) : "—"}</td>
                    <td className={clsx("td tabular-nums", delta == null || Math.abs(delta) < 0.0005 ? "text-muted" : delta > 0 ? "text-ok" : "text-bad")}>{delta == null ? "—" : `${delta > 0 ? "+" : ""}${(delta * 100).toFixed(1)} pt`}</td>
                    <td className="td">{gate == null ? "—" : <Badge tone={gate ? "ok" : "bad"}>{gate ? "pass" : "fail"}</Badge>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table></div>
        )}
        {!!r.regressions?.length && (
          <div className="mt-3 rounded-lg border border-bad/30 bg-bad/10 p-3 text-sm text-bad" role="alert">
            Regression against the baseline: {r.regressions.map((x: any) => `${title(x.metric)} ${fmt(x.baseline)} → ${fmt(x.current)}`).join("; ")}
          </div>
        )}
      </Card>
      <Card title="Cases" pad={false}>
        {r.results.length ? (
          <ul className="divide-y divide-line">
            {r.results.map((c: any) => (
              <li key={c.case_id}>
                <button className="flex w-full flex-wrap items-center gap-2 px-4 py-2.5 text-left hover:bg-raised" aria-expanded={open === c.case_id} onClick={() => setOpen(open === c.case_id ? null : c.case_id)}>
                  <Badge tone={c.passed ? "ok" : "bad"}>{c.passed ? "pass" : "fail"}</Badge>
                  <span className="font-mono text-xs text-muted">{c.case_id}</span>
                  <span className="min-w-0 flex-1 truncate text-sm">{c.name}</span>
                  <span className="flex flex-wrap gap-1">{Object.entries(c.scores).map(([k, v]) => <Badge key={k}>{title(k)} {fmt(v)}</Badge>)}</span>
                  <span className="text-xs tabular-nums text-muted">{ms(c.latency_ms)}</span>
                </button>
                {open === c.case_id && (
                  <div className="space-y-2 px-4 pb-3">
                    {c.detail?.run_id && <Link className="text-xs text-accent hover:underline" href={`/runs/${c.detail.run_id}`}>Open the workflow run this case executed</Link>}
                    <Json value={c.detail} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : <Empty title={r.status === "completed" ? "No case results" : "Cases are running…"} />}
      </Card>
    </div>
  );
}

export default function Evals() {
  const { can } = useAuth();
  const { data: datasets, error, mutate } = useSWR<any[]>("/api/evals/datasets", { refreshInterval: 4000 });
  const { data: runs, mutate: mutateRuns } = useSWR<any[]>("/api/evals/runs", { refreshInterval: 3000 });
  const { busy, run } = useAction();
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState("hybrid");
  const [rerank, setRerank] = useState(true);
  const current = selected || runs?.[0]?.id;
  const start = (dataset: string) =>
    run(dataset, async () => {
      const r = await post("/api/evals/runs", { dataset, config: { mode, rerank } });
      setSelected(r.id);
      mutateRuns();
      mutate();
    }, "Evaluation started");

  return (
    <>
      <PageHeader title="Evaluation Studio" subtitle="Deterministic test datasets executed against the live retrieval pipeline, the real LangGraph graph and the policy layer. Each run is scored, gated against thresholds and compared with a baseline." />
      <div className="space-y-4">
        <Async data={datasets} error={error} retry={() => mutate()}>
          {(ds) => (
            <div className="grid gap-3 lg:grid-cols-3">
              {ds.map((d) => (
                <div key={d.id} className="card flex flex-col p-4" data-testid={`dataset-${d.id}`}>
                  <div className="flex items-start justify-between gap-2"><h2 className="text-sm font-semibold">{d.name}</h2><Badge>{d.kind}</Badge></div>
                  <p className="mt-1 flex-1 text-xs text-muted">{d.description}</p>
                  <div className="mt-2 text-xs text-muted">{d.cases} cases · thresholds {Object.entries(d.thresholds).map(([k, v]) => `${title(k)} ≥ ${fmt(v)}`).join(", ")}</div>
                  <div className="mt-2 flex items-center gap-2 text-xs">
                    {d.last_run ? <><Badge tone={d.last_run.metrics?.gate_passed ? "ok" : d.last_run.status === "completed" ? "bad" : "info"}>{d.last_run.status === "completed" ? `${d.last_run.passed}/${d.last_run.total} passed` : d.last_run.status}</Badge><span className="text-muted">{ago(d.last_run.created_at)}</span></> : <span className="text-muted">Never run</span>}
                  </div>
                  <button className="btn btn-primary mt-3" disabled={!can("eval:run") || !!busy} onClick={() => start(d.id)}><FlaskConical className="h-4 w-4" /> {busy === d.id ? "Starting…" : "Run evaluation"}</button>
                </div>
              ))}
            </div>
          )}
        </Async>
        <Card title="Experiment configuration (applies to the retrieval dataset)">
          <div className="flex flex-wrap items-end gap-4 text-sm">
            <div><label className="label" htmlFor="emode">Retrieval mode</label><select id="emode" className="input" value={mode} onChange={(e) => setMode(e.target.value)}><option value="hybrid">Hybrid</option><option value="vector">Vector only</option><option value="keyword">Keyword only</option></select></div>
            <label className="flex items-center gap-1.5 pb-2"><input type="checkbox" checked={rerank} onChange={(e) => setRerank(e.target.checked)} /> Rerank</label>
            <p className="max-w-xl pb-2 text-xs text-muted">Change the configuration and run the retrieval dataset again to compare it with the baseline. A drop of more than two points on any metric is reported as a regression.</p>
            {!can("eval:run") && <p className="pb-2 text-xs text-muted">Your role cannot start evaluations.</p>}
          </div>
        </Card>
        <div className="grid gap-4 xl:grid-cols-[22rem_minmax(0,1fr)]">
          <Card title="Evaluation runs" pad={false}>
            <Async data={runs} error={null} empty={<Empty title="No evaluation runs yet" hint="Run a dataset above." />}>
              {(rs) => (
                <ul className="max-h-[40rem] divide-y divide-line overflow-auto">
                  {rs.map((r) => (
                    <li key={r.id}>
                      <button onClick={() => setSelected(r.id)} aria-current={current === r.id} className={clsx("w-full px-4 py-2.5 text-left hover:bg-raised", current === r.id && "bg-accent/10")}>
                        <div className="flex items-center justify-between gap-2"><span className="text-sm font-medium">{title(r.dataset)}</span><StatusBadge status={r.status === "completed" ? (r.metrics?.gate_passed ? "passed" : "failed") : r.status} /></div>
                        <div className="flex items-center gap-2 text-xs text-muted">{r.passed}/{r.total} cases · {ago(r.created_at)}{r.is_baseline && <Badge tone="accent">baseline</Badge>}{r.origin === "seed" && <span>· demo seed</span>}</div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Async>
          </Card>
          {current ? <RunDetail key={current} id={current} onChange={() => { mutateRuns(); mutate(); }} /> : <Card><Empty title="Select an evaluation run" /></Card>}
        </div>
      </div>
    </>
  );
}
