"use client";
import clsx from "clsx";
import { MessageSquarePlus, Send, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { Report } from "@/components/report";
import { Timeline } from "@/components/timeline";
import { Empty, ErrorBox, Spinner, StatusBadge, useAction } from "@/components/ui";
import { api, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago } from "@/lib/format";
import { TERMINAL, useRun } from "@/lib/useRun";

const SUGGESTIONS = [
  "Why are customers churning this quarter?",
  "What are our major operational risks and who owns them?",
  "Which priority-one tickets are breaching SLA and what should we escalate?",
  "Turn the latest Project Atlas weekly sync into tracked action items.",
];

/** The in-flight run of a conversation: live agent activity until the assistant message is persisted. */
function LiveRun({ runId, onDone }: { runId: string; onDone: () => void }) {
  const { run, events } = useRun(runId);
  const status = run?.status;
  useEffect(() => {
    if (status && TERMINAL.includes(status)) onDone();
  }, [status, onDone]);
  if (!run) return <Spinner label="Starting agents" />;
  return (
    <div className="card p-3" data-testid="live-run">
      <div className="mb-1 flex items-center gap-2 text-xs text-muted">
        <StatusBadge status={run.status} />
        <span>Agents are working — every step below is a persisted graph event.</span>
        <Link className="ml-auto text-accent hover:underline" href={`/runs/${run.id}`}>Open run</Link>
      </div>
      <Timeline events={events} />
      {run.status === "waiting_approval" && (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-warn/40 bg-warn/10 p-2.5 text-sm">
          <span className="flex-1">Proposed actions need human approval before anything is written. The answer continues after the decision.</span>
          <Link className="btn btn-primary" href={`/approvals?run=${run.id}`}>Review approvals</Link>
        </div>
      )}
    </div>
  );
}

function Chat() {
  const router = useRouter();
  const cid = useSearchParams().get("c");
  const { can } = useAuth();
  const { data: convs, mutate: mutateList } = useSWR<any[]>("/api/conversations");
  const { data: conv, error, mutate } = useSWR<any>(cid ? `/api/conversations/${cid}` : null);
  const { busy, run } = useAction();
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const allowed = can("agent:run");
  const activeRun = conv?.runs?.findLast?.((r: any) => !TERMINAL.includes(r.status));

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conv?.messages?.length, activeRun?.id]);

  const send = (content: string) =>
    run("send", async () => {
      let id = cid;
      if (!id) {
        id = (await post("/api/conversations", { title: "New conversation" })).id;
        router.replace(`/chat?c=${id}`);
      }
      await post(`/api/conversations/${id}/messages`, { content });
      setText("");
      await Promise.all([mutate(), mutateList()]);
    });

  const remove = (id: string) =>
    run("delete", async () => {
      await api(`/api/conversations/${id}`, { method: "DELETE" });
      if (id === cid) router.replace("/chat");
      mutateList();
    }, "Conversation deleted");

  return (
    <div data-testid="chat" className="grid h-[calc(100vh-6.5rem)] gap-4 lg:grid-cols-[17rem_minmax(0,1fr)]">
      <aside className="card hidden flex-col overflow-hidden lg:flex">
        <div className="border-b border-line p-2"><button className="btn w-full" onClick={() => router.replace("/chat")}><MessageSquarePlus className="h-4 w-4" /> New conversation</button></div>
        <ul className="flex-1 divide-y divide-line overflow-auto" aria-label="Conversations">
          {(convs || []).map((c) => (
            <li key={c.id} className={clsx("group flex items-center", c.id === cid && "bg-accent/10")}>
              <button className="min-w-0 flex-1 px-3 py-2 text-left hover:bg-raised" onClick={() => router.replace(`/chat?c=${c.id}`)}>
                <div className="truncate text-sm font-medium">{c.title}</div>
                <div className="text-xs text-muted">{c.message_count} messages · {ago(c.updated_at)}</div>
              </button>
              <button className="p-2 text-muted opacity-0 hover:text-bad focus:opacity-100 group-hover:opacity-100" aria-label={`Delete conversation ${c.title}`} onClick={() => remove(c.id)}><Trash2 className="h-4 w-4" /></button>
            </li>
          ))}
          {convs && !convs.length && <li className="p-4 text-xs text-muted">No conversations yet.</li>}
        </ul>
      </aside>

      <section className="card flex min-h-0 flex-col" aria-label="Conversation">
        <h1 className="sr-only">Multi-Agent Chat Workspace</h1>
        <div className="flex-1 space-y-4 overflow-auto p-4" data-testid="messages">
          {error && <ErrorBox error={error} />}
          {!cid && (
            <Empty title="Multi-Agent Chat Workspace" hint="Ask a business question. The supervisor classifies it, delegates to research, analytics, task and communication agents, and answers with cited evidence.">
              <div className="mt-2 grid w-full max-w-2xl gap-2 sm:grid-cols-2">
                {SUGGESTIONS.map((s) => <button key={s} className="btn !justify-start text-left" disabled={!allowed || !!busy} onClick={() => send(s)}>{s}</button>)}
              </div>
            </Empty>
          )}
          {cid && !conv && !error && <Spinner />}
          {conv?.messages.map((m: any) => (
            <div key={m.id} className={clsx("flex", m.role === "user" ? "justify-end" : "justify-start")}>
              {m.role === "user" ? (
                <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-3.5 py-2 text-sm text-white dark:text-slate-950">{m.content}</div>
              ) : (
                <div className="w-full max-w-4xl rounded-2xl rounded-bl-sm border border-line p-4">
                  {m.result ? <Report result={m.result} /> : <p className="whitespace-pre-wrap text-sm">{m.content}</p>}
                  {m.run_id && <Link className="mt-3 inline-block text-xs text-accent hover:underline" href={`/runs/${m.run_id}`}>View execution trace</Link>}
                </div>
              )}
            </div>
          ))}
          {activeRun && <LiveRun key={activeRun.id} runId={activeRun.id} onDone={() => { mutate(); mutateList(); }} />}
          <div ref={bottom} />
        </div>
        <form className="flex gap-2 border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); if (text.trim()) send(text.trim()); }}>
          <label className="sr-only" htmlFor="chat-input">Message</label>
          <input id="chat-input" className="input" value={text} onChange={(e) => setText(e.target.value)} disabled={!allowed || !!activeRun}
            placeholder={!allowed ? "Your role is read-only" : activeRun ? "Waiting for the current run to finish…" : "Ask the agents…"} />
          <button className="btn btn-primary" disabled={!allowed || !!busy || !!activeRun || !text.trim()} aria-label="Send message"><Send className="h-4 w-4" /></button>
        </form>
      </section>
    </div>
  );
}

export default function ChatPage() {
  return <Suspense><Chat /></Suspense>;
}
