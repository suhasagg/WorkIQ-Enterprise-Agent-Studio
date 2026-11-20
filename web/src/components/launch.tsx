"use client";
import { Play } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, useAction } from "./ui";

/** Workflow launcher: starts a real run through POST /api/runs and reports the new run id. */
export function Launcher({ onStarted, compact = false }: { onStarted: (run: any) => void; compact?: boolean }) {
  const { can, user } = useAuth();
  const { data: workflows } = useSWR<any[]>("/api/workflows");
  const { data: sys } = useSWR<any>("/api/system");
  const { busy, run } = useAction();
  const [wf, setWf] = useState("weekly_business_review");
  const [request, setRequest] = useState("");
  const [dryRun, setDryRun] = useState(false);
  const [fault, setFault] = useState("");
  const selected = workflows?.find((w) => w.id === wf);
  const allowed = can("agent:run");

  const start = () =>
    run("start", async () => {
      const options: any = { dry_run: dryRun };
      if (fault) options.fault_injection = fault.includes(".") && !fault.startsWith("research") ? { tool: fault, times: 1 } : { node: fault, times: 2 };
      const created = await post("/api/runs", { workflow: wf === "auto" ? null : wf, request: request.trim() || null, options });
      onStarted(created);
    }, "Workflow started");

  return (
    <div className="space-y-3" data-testid="launcher">
      <div>
        <label className="label" htmlFor="wf">Workflow</label>
        <select id="wf" className="input" value={wf} onChange={(e) => setWf(e.target.value)}>
          {(workflows || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          <option value="auto">Free-form request (supervisor classifies intent)</option>
        </select>
        {selected && !compact && (
          <p className="mt-1.5 text-xs text-muted">{selected.description} {selected.requires_approval && <Badge tone="warn">Writes need approval</Badge>}</p>
        )}
      </div>
      <div>
        <label className="label" htmlFor="req">{wf === "auto" ? "Request" : "Request (optional — leave empty to use the workflow default)"}</label>
        <textarea id="req" className="input min-h-[4.5rem]" value={request} onChange={(e) => setRequest(e.target.value)} placeholder={selected?.prompt || "Ask a question or describe the task"} />
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} /> Dry run (propose only, write nothing)</label>
        {sys?.fault_injection && (
          <div className="min-w-[14rem] flex-1">
            <label className="label" htmlFor="fault">Failure simulation (demo)</label>
            <select id="fault" className="input" value={fault} onChange={(e) => setFault(e.target.value)}>
              <option value="">None</option>
              <option value="research.retrieve">Retrieval outage → retry, then replan</option>
              <option value="metrics.get_weekly_kpis">Metrics tool transient error → tool retry</option>
              <option value="helpdesk.search_tickets">Helpdesk tool transient error → tool retry</option>
            </select>
          </div>
        )}
        <button className="btn btn-primary" onClick={start} disabled={!allowed || !!busy || (wf === "auto" && request.trim().length < 3)} title={allowed ? undefined : "Your role cannot start workflows"}>
          <Play className="h-4 w-4" /> {busy ? "Starting…" : "Run workflow"}
        </button>
      </div>
      {!allowed && <p className="text-xs text-muted">Your role ({user?.role}) is read-only. Sign in as an operator, approver or admin to run workflows.</p>}
    </div>
  );
}
