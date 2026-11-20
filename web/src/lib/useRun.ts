"use client";
import { useEffect, useRef, useState } from "react";
import useSWR from "swr";

export type RunEvent = { seq: number; ts: string; type: string; node: string | null; agent: string | null; payload: any };
export const TERMINAL = ["completed", "failed", "cancelled"];
const LIVE = ["queued", "running"];

/** Parses one text/event-stream response body, calling onEvent for every `data:` frame. */
async function readStream(url: string, signal: AbortSignal, onEvent: (e: RunEvent) => void) {
  const res = await fetch(url, { credentials: "include", signal, headers: { accept: "text/event-stream" } });
  if (!res.ok || !res.body) throw new Error(`stream failed (${res.status})`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += decoder.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = frame.split("\n").find((l) => l.startsWith("data: "));
      if (data) onEvent(JSON.parse(data.slice(6)));
    }
  }
}

/**
 * Live view of one run: the run record (polled while active) plus its persisted event log, streamed over SSE.
 * The stream is resumed from the last seen sequence number, so reconnects never drop or duplicate events.
 */
export function useRun(runId: string | null | undefined) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);
  const lastSeq = useRef(0);
  const { data: run, error, mutate } = useSWR<any>(runId ? `/api/runs/${runId}` : null, {
    refreshInterval: (r: any) => (r && (TERMINAL.includes(r.status) || r.status === "paused") ? 0 : 1500),
  });
  const status: string | undefined = run?.status;
  const live = !!status && LIVE.includes(status);
  const open = live || status === "waiting_approval";

  useEffect(() => {
    setEvents([]);
    lastSeq.current = 0;
  }, [runId]);

  useEffect(() => {
    if (!runId || !status) return;
    const ctl = new AbortController();
    let stopped = false;
    const push = (e: RunEvent) => {
      if (e.seq <= lastSeq.current) return;
      lastSeq.current = e.seq;
      setEvents((prev) => [...prev, e]);
    };
    (async () => {
      if (!open) {
        // Finished or paused: the log is complete, read it once instead of holding a stream open.
        try {
          const rows: RunEvent[] = await (await fetch(`/api/runs/${runId}/events?after=${lastSeq.current}`, { credentials: "include", signal: ctl.signal })).json();
          if (Array.isArray(rows)) rows.forEach(push);
        } catch {
          /* aborted on unmount */
        }
        return;
      }
      // The server keeps the stream open until a terminal event; on a dropped connection resume from lastSeq.
      for (let attempt = 0; !stopped; attempt++) {
        try {
          await readStream(`/api/runs/${runId}/stream?after=${lastSeq.current}`, ctl.signal, push);
          setStreamError(null);
          mutate();
          return;
        } catch (e) {
          if (stopped) return;
          setStreamError(e instanceof Error ? e.message : "stream error");
        }
        await new Promise((r) => setTimeout(r, Math.min(1000 * (attempt + 1), 5000)));
      }
    })();
    return () => {
      stopped = true;
      ctl.abort();
    };
    // Re-subscribe whenever the run changes state (e.g. waiting_approval -> running after a decision).
  }, [runId, status, open, mutate]);

  return { run, events, error, streamError, refresh: mutate, live };
}
