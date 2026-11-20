"use client";
import Link from "next/link";
import { RunView } from "@/components/runview";
import { PageHeader } from "@/components/ui";

export default function RunPage({ params }: { params: { id: string } }) {
  return (
    <>
      <PageHeader title="Workflow run" subtitle="Live execution, report, approvals, telemetry and checkpointed state for one LangGraph thread.">
        <Link className="btn" href="/studio">Workflow Studio</Link>
      </PageHeader>
      <RunView runId={params.id} />
    </>
  );
}
