"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { Launcher } from "@/components/launch";
import { AGENT_COLORS, Timeline } from "@/components/timeline";
import { Async, Card, Empty, PageHeader, SimulatedBadge, Stat, StatusBadge } from "@/components/ui";
import { ago, ms, num, pct, title, usd } from "@/lib/format";

export default function CommandCenter() {
  const router = useRouter();
  const { data, error, mutate } = useSWR<any>("/api/dashboard", { refreshInterval: 4000 });
  return (
    <>
      <PageHeader title="AI Operations Command Center" subtitle="Live state of agents, workflow runs, approvals, tools and knowledge for this workspace. All figures are computed from persisted runs." />
      <Async data={data} error={error} retry={() => mutate()}>
        {(d) => (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-6">
              <Stat label="Runs (24 h)" value={num(d.runs.total)} sub={`${d.runs.active} active · ${d.runs.failed} failed`} />
              <Stat label="Success rate" value={pct(d.runs.success_rate)} sub="completed ÷ finished" tone={d.runs.success_rate == null ? undefined : d.runs.success_rate >= 0.9 ? "ok" : "warn"} />
              <Stat label="Pending approvals" value={num(d.approvals.pending)} sub={<Link className="text-accent hover:underline" href="/approvals">Open Approval Center</Link>} tone={d.approvals.pending ? "warn" : undefined} />
              <Stat label="Tool success" value={pct(d.tools.success_rate)} sub={`${d.tools.total} calls · ${d.tools.held} held · ${d.tools.denied} denied`} />
              <Stat label="Avg quality" value={pct(d.runs.avg_quality)} sub="evaluator overall score" />
              <Stat label="Tokens / cost" value={num(d.runs.tokens)} sub={`${usd(d.runs.cost_usd)} · avg run ${ms(d.runs.avg_duration_ms)}`} />
            </div>

            <div className="grid gap-4 xl:grid-cols-3">
              <Card title="Launch a workflow" className="xl:col-span-2"><Launcher onStarted={(r) => router.push(`/runs/${r.id}`)} /></Card>
              <Card title="Agents">
                <ul className="space-y-2">
                  {d.agents.map((a: any) => (
                    <li key={a.agent} className="flex items-center gap-2 text-sm">
                      <span className="h-2 w-2 rounded-full" style={{ background: AGENT_COLORS[a.agent] || AGENT_COLORS.system }} aria-hidden />
                      <span className="flex-1 font-medium">{title(a.agent)}</span>
                      <span className="text-xs text-muted">{a.activations} activations{a.errors ? ` · ${a.errors} errors` : ""} · {ago(a.last_active)}</span>
                      <StatusBadge status={a.state} />
                    </li>
                  ))}
                  {!d.agents.length && <Empty title="No agent activity yet" hint="Run a workflow to see agents here." />}
                </ul>
              </Card>
            </div>

            <div className="grid gap-4 xl:grid-cols-3">
              <Card title="Recent runs" className="xl:col-span-2" pad={false} action={<Link className="text-xs text-accent hover:underline" href="/studio">All runs</Link>}>
                {d.recent_runs.length ? (
                  <div className="overflow-x-auto"><table className="w-full min-w-[560px]">
                    <thead><tr><th className="th">Request</th><th className="th">Workflow</th><th className="th">Status</th><th className="th">Quality</th><th className="th">Started</th></tr></thead>
                    <tbody className="divide-y divide-line">
                      {d.recent_runs.map((r: any) => (
                        <tr key={r.id} className="hover:bg-raised">
                          <td className="td max-w-[22rem]"><Link className="line-clamp-1 hover:underline" href={`/runs/${r.id}`}>{r.request}</Link></td>
                          <td className="td text-xs">{title(r.intent || r.workflow)}</td>
                          <td className="td"><StatusBadge status={r.status} /></td>
                          <td className="td tabular-nums">{pct(r.score)}</td>
                          <td className="td text-xs text-muted">{ago(r.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table></div>
                ) : <Empty title="No runs yet" hint="Launch a workflow above." />}
              </Card>
              <div className="space-y-4">
                <Card title="MCP servers" action={<Link className="text-xs text-accent hover:underline" href="/integrations">Manage</Link>}>
                  <ul className="space-y-1.5">
                    {d.mcp.servers.map((s: any) => (
                      <li key={s.id} className="flex items-center gap-2 text-sm"><span className="flex-1">{s.name}</span>{s.kind === "demo" && <SimulatedBadge />}<StatusBadge status={s.status} /></li>
                    ))}
                  </ul>
                </Card>
                <Card title="Knowledge" action={<Link className="text-xs text-accent hover:underline" href="/knowledge">Open</Link>}>
                  <div className="flex gap-6 text-sm"><div><div className="text-xl font-semibold tabular-nums">{d.knowledge.documents}</div><div className="text-xs text-muted">documents</div></div><div><div className="text-xl font-semibold tabular-nums">{d.knowledge.chunks}</div><div className="text-xs text-muted">indexed chunks</div></div></div>
                </Card>
              </div>
            </div>

            <Card title="Live activity" pad={false}>
              <div className="max-h-80 overflow-auto p-2"><Timeline events={[...d.activity].reverse()} /></div>
            </Card>
          </div>
        )}
      </Async>
    </>
  );
}
