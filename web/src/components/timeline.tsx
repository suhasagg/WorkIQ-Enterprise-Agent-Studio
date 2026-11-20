"use client";
import clsx from "clsx";
import { AlertTriangle, Brain, CheckCircle2, CircleDot, FileSearch, Hand, ListChecks, Play, RotateCcw, ShieldAlert, Wrench } from "lucide-react";
import { clock, ms, title } from "@/lib/format";
import type { RunEvent } from "@/lib/useRun";

export const AGENT_COLORS: Record<string, string> = {
  supervisor: "#6366f1", research: "#0ea5e9", analytics: "#f59e0b", task: "#10b981", communication: "#ec4899",
  compliance: "#ef4444", synthesizer: "#8b5cf6", evaluator: "#14b8a6", system: "#64748b",
};

const HIDDEN = new Set(["node_start", "node_end", "model_call"]);

function describe(e: RunEvent): { icon: any; text: string; tone?: string; detail?: string } {
  const p = e.payload || {};
  switch (e.type) {
    case "run_started": return { icon: Play, text: "Run started" };
    case "run_resumed": return { icon: Play, text: "Run resumed from checkpoint" };
    case "plan": return { icon: ListChecks, text: `Plan created: ${p.steps?.length ?? 0} steps`, detail: p.steps?.map((s: any) => `${s.agent}: ${s.title}`).join(" → ") };
    case "replan": return { icon: RotateCcw, text: `Replanned — ${p.change}`, tone: "warn", detail: p.reason };
    case "step_start": return { icon: CircleDot, text: `${title(p.agent)} agent: ${p.title}${p.attempt > 1 ? ` (attempt ${p.attempt})` : ""}` };
    case "step_end": return { icon: CheckCircle2, text: `Step ${p.status}`, detail: p.summary, tone: p.status === "completed" ? "ok" : "warn" };
    case "step_retry": return { icon: RotateCcw, text: `Retrying step (attempt ${p.attempt})`, tone: "warn", detail: p.error };
    case "queries_planned": return { icon: FileSearch, text: `Planned ${p.queries?.length} search queries`, detail: p.queries?.join(" · ") };
    case "retrieval": return { icon: FileSearch, text: `Retrieved ${p.results?.length ?? 0} passages (${p.mode})`, detail: p.query };
    case "grading": return { icon: FileSearch, text: `Graded evidence: kept ${p.kept}, dropped ${p.dropped}` };
    case "analysis": return { icon: Brain, text: p.kind === "kpi" ? `Analysed ${p.metrics} KPIs, ${p.anomalies?.length ?? 0} anomalies` : `Analysis: ${p.kind}` };
    case "extraction": return { icon: Brain, text: `Extracted ${p.decisions} decisions, ${p.action_items} action items, ${p.open_questions} open questions` };
    case "tool_call": return { icon: Wrench, text: `Tool ${p.server ? p.server + "." : ""}${p.tool} → ${p.status}`, tone: p.status === "ok" ? undefined : "warn", detail: p.latency_ms != null ? ms(p.latency_ms) : p.error };
    case "actions_proposed": return { icon: ListChecks, text: `Proposed ${p.actions?.length} actions`, detail: p.actions?.map((a: any) => a.title).join(" · ") };
    case "compliance_review": return { icon: ShieldAlert, text: `Policy: ${p.decision?.replace(/_/g, " ")} — ${p.title}`, detail: p.reason, tone: p.decision === "deny" ? "bad" : undefined };
    case "approval_requested": return { icon: Hand, text: `Approval requested for ${p.approvals?.length} actions`, tone: "warn" };
    case "run_waiting_approval": return { icon: Hand, text: "Paused: waiting for human approval (state checkpointed)", tone: "warn" };
    case "approval_decision": return { icon: Hand, text: `${p.by} ${String(p.decision).replace(/_/g, " ")}: ${p.title}`, detail: p.comment, tone: p.decision === "rejected" ? "bad" : "ok" };
    case "action_executed": return { icon: Wrench, text: `Executed: ${p.title}`, detail: p.error || p.result?.id, tone: p.error ? "bad" : "ok" };
    case "draft": return { icon: Brain, text: `Drafted report with ${p.sections?.length} sections and ${p.citations} citations` };
    case "evaluation": return { icon: CheckCircle2, text: `Evaluation ${p.passed ? "passed" : "needs revision"} (overall ${Math.round((p.overall ?? 0) * 100)}%)`, tone: p.passed ? "ok" : "warn" };
    case "guardrail": return { icon: ShieldAlert, text: `Guardrail: ${title(p.kind)}`, detail: p.action, tone: "warn" };
    case "run_completed": return { icon: CheckCircle2, text: p.partial ? "Run completed with partial results" : "Run completed", tone: "ok" };
    case "run_failed": return { icon: AlertTriangle, text: "Run failed", detail: p.error, tone: "bad" };
    case "run_cancelled": return { icon: AlertTriangle, text: "Run cancelled", tone: "bad" };
    case "run_paused": return { icon: Hand, text: "Run paused", tone: "warn" };
    default: return { icon: CircleDot, text: title(e.type), detail: typeof p === "object" ? p.action || p.reason || p.error : undefined };
  }
}

export function Timeline({ events, verbose = false, className }: { events: RunEvent[]; verbose?: boolean; className?: string }) {
  const shown = verbose ? events : events.filter((e) => !HIDDEN.has(e.type));
  if (!shown.length) return <p className="p-4 text-sm text-muted">No events yet.</p>;
  return (
    <ol className={clsx("space-y-0.5", className)} aria-label="Execution timeline" data-testid="timeline">
      {shown.map((e) => {
        const d = describe(e);
        const Icon = d.icon;
        return (
          <li key={e.seq} className="flex gap-2.5 rounded-md px-2 py-1.5 text-sm hover:bg-raised">
            <Icon className={clsx("mt-0.5 h-4 w-4 shrink-0", d.tone === "ok" ? "text-ok" : d.tone === "warn" ? "text-warn" : d.tone === "bad" ? "text-bad" : "text-muted")} aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="break-words">{d.text}</div>
              {d.detail && <div className="break-words text-xs text-muted">{d.detail}</div>}
            </div>
            <div className="shrink-0 text-right text-xs text-muted">
              {e.agent && <span className="mr-2 inline-flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full" style={{ background: AGENT_COLORS[e.agent] || AGENT_COLORS.system }} />{e.agent}</span>}
              <time className="font-mono">{clock(e.ts)}</time>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
