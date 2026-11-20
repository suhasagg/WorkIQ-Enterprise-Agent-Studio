"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import useSWR from "swr";
import { GraphView, nodeStates, type Topology } from "@/components/graph";
import { Launcher } from "@/components/launch";
import { RunControls } from "@/components/runview";
import { AGENT_COLORS, Timeline } from "@/components/timeline";
import { Async, Card, Empty, PageHeader, StatusBadge } from "@/components/ui";
import { ago, ms, title } from "@/lib/format";
import { useRun } from "@/lib/useRun";

function Studio() {
  const router = useRouter();
  const selected = useSearchParams().get("run");
  const { data: topology, error } = useSWR<Topology>("/api/graph/topology");
  const { data: runs, mutate } = useSWR<any[]>("/api/runs?limit=30", { refreshInterval: 4000 });
  const { run, events, refresh } = useRun(selected);
  const [expand, setExpand] = useState<string | null>(null);
  const { states, timings } = useMemo(() => nodeStates(events, run?.status), [events, run?.status]);
  const select = (id: string) => router.replace(`/studio?run=${id}`);

  return (
    <>
      <PageHeader title="LangGraph Workflow Studio" subtitle="The compiled StateGraph as it runs: supervisor routing, agent subgraphs, the approval interrupt and the evaluator loop. Dashed edges are conditional." />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="space-y-4">
          <Card
            title={expand && topology ? `Subgraph: ${topology.groups.find((g) => g.id === expand)?.label}` : run ? `Graph — ${title(run.intent || run.workflow)}` : "Graph topology"}
            action={
              <div className="flex items-center gap-2">
                {run && <StatusBadge status={run.status} />}
                {expand ? <button className="btn" onClick={() => setExpand(null)}>Back to full graph</button> : <span className="hidden text-xs text-muted sm:inline">Click an agent to open its subgraph</span>}
              </div>
            }>
            <Async data={topology} error={error}>
              {(t) => <GraphView topology={t} states={states} timings={timings} expand={expand} onSelect={(id) => t.groups.some((g) => g.id === id) && setExpand(id)} />}
            </Async>
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
              {Object.entries(AGENT_COLORS).map(([a, c]) => <span key={a} className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: c }} />{a}</span>)}
            </div>
          </Card>
          {run ? (
            <Card title="Execution" pad={false} action={<div className="flex items-center gap-2"><RunControls run={run} onChange={() => { refresh(); mutate(); }} /><Link className="btn" href={`/runs/${run.id}`}>Open run</Link></div>}>
              <div className="border-b border-line px-4 py-2 text-xs text-muted">{run.request} · {ms(run.duration_ms)} · {run.tool_calls} tool calls</div>
              <div className="max-h-96 overflow-auto p-2"><Timeline events={events} /></div>
            </Card>
          ) : (
            <Card><Empty title="No run selected" hint="Start a workflow or pick a run on the right to watch it move through the graph." /></Card>
          )}
        </div>
        <div className="space-y-4">
          <Card title="Run a workflow"><Launcher compact onStarted={(r) => { mutate(); select(r.id); }} /></Card>
          <Card title="Runs" pad={false}>
            <Async data={runs} error={null} empty={<Empty title="No runs yet" />}>
              {(rs) => (
                <ul className="max-h-[30rem] divide-y divide-line overflow-auto">
                  {rs.map((r) => (
                    <li key={r.id}>
                      <button onClick={() => select(r.id)} aria-current={selected === r.id} className={`w-full px-4 py-2.5 text-left hover:bg-raised ${selected === r.id ? "bg-accent/10" : ""}`}>
                        <div className="flex items-center justify-between gap-2"><span className="text-sm font-medium">{title(r.intent || r.workflow || "request")}</span><StatusBadge status={r.status} /></div>
                        <div className="line-clamp-1 text-xs text-muted">{r.request}</div>
                        <div className="text-xs text-muted">{ago(r.created_at)} · {ms(r.duration_ms)}</div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Async>
          </Card>
        </div>
      </div>
    </>
  );
}

export default function StudioPage() {
  return <Suspense><Studio /></Suspense>;
}
