"use client";
import clsx from "clsx";
import { Check, MessageSquareWarning, Pencil, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import useSWR from "swr";
import { Async, Badge, Card, Empty, ErrorBox, Json, PageHeader, RiskBadge, SimulatedBadge, StatusBadge, Tabs, useAction } from "@/components/ui";
import { post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, title } from "@/lib/format";

function Detail({ id, onDecided }: { id: string; onDecided: () => void }) {
  const { can } = useAuth();
  const { data: a, error, mutate } = useSWR<any>(`/api/approvals/${id}`, { refreshInterval: 3000 });
  const { data: servers } = useSWR<any[]>("/api/mcp/servers");
  const { busy, run } = useAction();
  const [comment, setComment] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  useEffect(() => { setComment(""); setEditing(false); setFormError(null); }, [id]);
  if (error) return <ErrorBox error={error} />;
  if (!a) return <Card><div className="p-6 text-sm text-muted">Loading approval…</div></Card>;
  const allowed = can("approval:decide") && a.status === "pending";

  const decide = (decision: string) => {
    setFormError(null);
    let edited_params: any;
    if (decision === "edit") {
      try { edited_params = JSON.parse(draft); } catch { return setFormError("Edited parameters must be valid JSON."); }
    }
    if ((decision === "reject" || decision === "request_changes") && !comment.trim()) return setFormError("Add a comment explaining the decision.");
    run(decision, async () => {
      const res = await post(`/api/approvals/${id}/decision`, { decision, comment, edited_params });
      await mutate();
      onDecided();
      return res;
    }, decision === "approve" || decision === "edit" ? "Approved" : decision === "reject" ? "Rejected" : "Changes requested").then((res: any) => {
      if (res?.run_resumed) setFormError(null);
    });
  };

  return (
    <Card title={a.title} action={<div className="flex gap-2"><RiskBadge risk={a.risk} /><StatusBadge status={a.status} /></div>}>
      <div className="space-y-4 text-sm" data-testid="approval-detail">
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
          <div><dt className="label">Tool</dt><dd className="flex items-center gap-2 font-mono text-xs">{a.server}.{a.tool} {servers?.find((s) => s.id === a.server)?.kind === "demo" && <SimulatedBadge />}</dd></div>
          <div><dt className="label">Requested by</dt><dd>{title(a.requesting_agent)} agent · for {a.requested_by} · {ago(a.created_at)}</dd></div>
          <div className="sm:col-span-2"><dt className="label">Why the agent proposes this</dt><dd>{a.rationale}</dd></div>
          <div className="sm:col-span-2"><dt className="label">Why approval is required</dt><dd>{a.policy_reason}</dd></div>
        </dl>

        <div>
          <div className="mb-1 flex items-center justify-between">
            <span className="label !mb-0">Exact parameters that will be sent (bound by hash {a.params_hash.slice(0, 12)}…)</span>
            {allowed && !editing && <button className="btn" onClick={() => { setDraft(JSON.stringify(a.params, null, 2)); setEditing(true); }}><Pencil className="h-3.5 w-3.5" /> Edit</button>}
          </div>
          {editing ? (
            <textarea aria-label="Edited parameters (JSON)" className="input min-h-[14rem] font-mono text-xs" value={draft} onChange={(e) => setDraft(e.target.value)} />
          ) : <Json value={a.edited_params || a.params} />}
          {a.edited_params && <p className="mt-1 text-xs text-muted">Parameters were edited by the reviewer; the edited version was executed.</p>}
        </div>

        {!!a.evidence?.length && (
          <div>
            <div className="label">Evidence</div>
            <ul className="space-y-1.5">
              {a.evidence.map((e: any, i: number) => (
                <li key={i} className="rounded-lg border border-line p-2 text-xs"><span className="font-medium">{e.title || e.source || e.kind}</span>{e.heading ? ` § ${e.heading}` : ""}<p className="mt-0.5 text-muted">{e.snippet || e.text || e.detail}</p></li>
              ))}
            </ul>
          </div>
        )}

        {a.status === "pending" ? (
          can("approval:decide") ? (
            <div className="space-y-2 border-t border-line pt-4">
              <label className="label" htmlFor="comment">Comment (required to reject or request changes)</label>
              <textarea id="comment" className="input" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Reason, or instructions for the agent to revise the action" />
              {formError && <ErrorBox error={formError} />}
              <div className="flex flex-wrap gap-2">
                {editing ? (
                  <>
                    <button className="btn btn-primary" disabled={!!busy} onClick={() => decide("edit")}><Check className="h-4 w-4" /> Approve with edits</button>
                    <button className="btn" onClick={() => setEditing(false)}>Discard edits</button>
                  </>
                ) : <button className="btn btn-primary" disabled={!!busy} onClick={() => decide("approve")}><Check className="h-4 w-4" /> Approve</button>}
                <button className="btn" disabled={!!busy} onClick={() => decide("request_changes")}><MessageSquareWarning className="h-4 w-4" /> Request changes</button>
                <button className="btn btn-danger" disabled={!!busy} onClick={() => decide("reject")}><X className="h-4 w-4" /> Reject</button>
              </div>
              <p className="text-xs text-muted">The workflow resumes from its checkpoint once every pending action of the run has a decision.</p>
            </div>
          ) : <p className="border-t border-line pt-4 text-xs text-muted">Your role cannot decide approvals. Sign in as an approver or admin.</p>
        ) : (
          <div className="border-t border-line pt-4 text-xs text-muted">
            {a.decided_by_name ? `${title(a.status)} by ${a.decided_by_name} ${ago(a.decided_at)}` : title(a.status)}{a.comment ? ` — “${a.comment}”` : ""}
            {a.execution && <div className="mt-2"><div className="label">Execution result</div><Json value={a.execution} /></div>}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3 text-xs">
          <Link className="text-accent hover:underline" href={`/runs/${a.run_id}`}>Open workflow run</Link>
          <span className="text-muted">Run status: {title(a.run_status)}</span>
        </div>
        {!!a.audit?.length && (
          <details>
            <summary className="cursor-pointer text-xs text-muted">Audit trail ({a.audit.length} hash-chained events)</summary>
            <ul className="mt-2 space-y-1 text-xs">{a.audit.map((e: any) => <li key={e.seq} className="flex gap-2"><span className="font-mono text-muted">#{e.seq}</span><span>{e.action}</span><span className="text-muted">{e.actor} · {e.decision}</span></li>)}</ul>
          </details>
        )}
      </div>
    </Card>
  );
}

function Approvals() {
  const router = useRouter();
  const sp = useSearchParams();
  const selected = sp.get("id"), runFilter = sp.get("run");
  const [tab, setTab] = useState<"pending" | "decided">("pending");
  const { data, error, mutate } = useSWR<any[]>("/api/approvals?limit=200", { refreshInterval: 4000 });
  const all = (data || []).filter((a) => !runFilter || a.run_id === runFilter);
  const pending = all.filter((a) => a.status === "pending"), decided = all.filter((a) => a.status !== "pending");
  const list = tab === "pending" ? pending : decided;
  const current = selected || list[0]?.id;
  const select = (id: string) => router.replace(`/approvals?id=${id}${runFilter ? `&run=${runFilter}` : ""}`);

  return (
    <>
      <PageHeader title="Human Approval Center" subtitle="Every state-changing tool call stops here. The graph is paused at a LangGraph interrupt with its state in PostgreSQL, and resumes only after a decision.">
        {runFilter && <Link className="btn" href="/approvals">Show all runs</Link>}
      </PageHeader>
      <div className="grid gap-4 lg:grid-cols-[24rem_minmax(0,1fr)]">
        <Card pad={false}>
          <div className="px-2 pt-1"><Tabs label="Approval status" value={tab} onChange={(t) => { setTab(t); router.replace(`/approvals${runFilter ? `?run=${runFilter}` : ""}`); }} tabs={[{ id: "pending", label: "Pending", count: pending.length }, { id: "decided", label: "Decided", count: decided.length }]} /></div>
          <Async data={data} error={error} retry={() => mutate()}>
            {() => list.length ? (
              <ul className="max-h-[42rem] divide-y divide-line overflow-auto" data-testid="approval-list">
                {list.map((a) => (
                  <li key={a.id}>
                    <button onClick={() => select(a.id)} aria-current={current === a.id} className={clsx("w-full px-4 py-3 text-left hover:bg-raised", current === a.id && "bg-accent/10")}>
                      <div className="line-clamp-2 text-sm font-medium">{a.title}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5"><RiskBadge risk={a.risk} />{a.status !== "pending" && <StatusBadge status={a.status} />}{a.round > 0 && <Badge>revision {a.round}</Badge>}</div>
                      <div className="mt-1 text-xs text-muted"><span className="font-mono">{a.server}.{a.tool}</span> · {title(a.workflow)} · {ago(a.created_at)}</div>
                    </button>
                  </li>
                ))}
              </ul>
            ) : <Empty title={tab === "pending" ? "Nothing is waiting for approval" : "No decisions yet"} hint={tab === "pending" ? "Run a workflow that proposes actions, such as the Weekly Executive Business Review." : undefined} />}
          </Async>
        </Card>
        {current ? <Detail key={current} id={current} onDecided={() => mutate()} /> : <Card><Empty title="Select an approval" /></Card>}
      </div>
    </>
  );
}

export default function ApprovalsPage() {
  return <Suspense><Approvals /></Suspense>;
}
