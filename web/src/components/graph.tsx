"use client";
import { Background, Controls, type Edge, Handle, MarkerType, type Node, Position, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import clsx from "clsx";
import { useMemo } from "react";
import { AGENT_COLORS } from "./timeline";

export type Topology = { nodes: { id: string; label: string; agent: string; parent: string | null }[]; edges: { source: string; target: string; conditional: boolean }[]; groups: { id: string; label: string; agent: string }[] };
export type NodeState = "idle" | "active" | "done" | "error" | "waiting";

const W = 204, H = 40, GAP_X = 28, GAP_Y = 30;

/** Longest-path layering (ignoring back edges found by DFS) – enough for this mostly-linear supervisor graph. */
function layout(ids: string[], edges: { source: string; target: string }[]) {
  const out = new Map<string, string[]>(ids.map((i) => [i, []]));
  edges.forEach((e) => out.has(e.source) && out.has(e.target) && out.get(e.source)!.push(e.target));
  const back = new Set<string>(), state = new Map<string, number>();
  const dfs = (n: string) => {
    state.set(n, 1);
    for (const m of out.get(n)!) {
      if (state.get(m) === 1) back.add(`${n}>${m}`);
      else if (!state.has(m)) dfs(m);
    }
    state.set(n, 2);
  };
  ids.forEach((i) => !state.has(i) && dfs(i));
  const rank = new Map<string, number>(ids.map((i) => [i, 0]));
  for (let pass = 0; pass < ids.length; pass++) {
    let changed = false;
    for (const e of edges) {
      if (!rank.has(e.source) || !rank.has(e.target) || back.has(`${e.source}>${e.target}`) || e.source === e.target) continue;
      if (rank.get(e.target)! < rank.get(e.source)! + 1) {
        rank.set(e.target, rank.get(e.source)! + 1);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const rows = new Map<number, string[]>();
  ids.forEach((i) => rows.set(rank.get(i)!, [...(rows.get(rank.get(i)!) || []), i]));
  const pos = new Map<string, { x: number; y: number }>();
  const width = Math.max(...[...rows.values()].map((r) => r.length));
  const order = ["analytics", "research", "task", "communication"];
  rows.forEach((row) => row.sort((a, b) => order.indexOf(a) - order.indexOf(b)));
  rows.forEach((row, r) => row.forEach((id, i) => pos.set(id, { x: (i + (width - row.length) / 2) * (W + GAP_X), y: r * (H + GAP_Y) })));
  return { pos, back };
}

function AgentNode({ data }: { data: { label: string; agent: string; state: NodeState; sub?: string } }) {
  const color = AGENT_COLORS[data.agent] || AGENT_COLORS.system;
  return (
    <div data-state={data.state} data-testid={`graph-node-${data.label}`}
      className={clsx("flex items-center gap-2 rounded-lg border bg-surface px-2.5 text-xs shadow-sm transition",
        data.state === "active" && "border-info ring-2 ring-info/40", data.state === "waiting" && "border-warn ring-2 ring-warn/40",
        data.state === "done" && "border-ok/60", data.state === "error" && "border-bad ring-2 ring-bad/30", data.state === "idle" && "border-line opacity-80")}
      style={{ width: W, height: H }}>
      <Handle type="target" position={Position.Top} className="!h-1 !w-1 !border-0 !bg-transparent" />
      <span className={clsx("h-2 w-2 shrink-0 rounded-full", data.state === "active" && "animate-pulse")} style={{ background: color }} />
      <span className="min-w-0 flex-1 truncate font-medium">{data.label}</span>
      {data.sub && <span className="font-mono text-[10px] text-muted">{data.sub}</span>}
      <Handle type="source" position={Position.Bottom} className="!h-1 !w-1 !border-0 !bg-transparent" />
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

/** Renders the compiled LangGraph topology. `expand` selects one subgraph to show in place of the top-level view. */
export function GraphView({ topology, states = {}, timings = {}, expand = null, onSelect, height = 560 }: {
  topology: Topology; states?: Record<string, NodeState>; timings?: Record<string, string>; expand?: string | null; onSelect?: (id: string) => void; height?: number;
}) {
  const { nodes, edges } = useMemo(() => {
    // Top level: each agent subgraph is collapsed into one node; `expand` shows the inside of one subgraph.
    const group = (id: string) => (id.includes(":") ? id.split(":")[0] : id);
    const visible = expand
      ? topology.nodes.filter((n) => n.parent === expand)
      : [...topology.nodes.filter((n) => n.parent === null), ...topology.groups.map((g) => ({ id: g.id, label: g.label.split(" (")[0], agent: g.agent, parent: null }))];
    const ids = visible.map((n) => n.id);
    const seen = new Set<string>();
    const es = (expand ? topology.edges : topology.edges.map((e) => ({ ...e, source: group(e.source), target: group(e.target) })))
      .filter((e) => ids.includes(e.source) && ids.includes(e.target) && e.source !== e.target)
      .filter((e) => !seen.has(`${e.source}>${e.target}`) && !!seen.add(`${e.source}>${e.target}`));
    // Layout hint only: keep the compliance gate on the row below the worker agents.
    const hints = expand ? [] : ["research", "analytics", "task", "communication"].map((a) => ({ source: a, target: "compliance" }));
    const { pos, back } = layout(ids, [...es, ...hints]);
    const nodes: Node[] = visible.map((n) => ({
      id: n.id, type: "agent", position: pos.get(n.id)!, draggable: false,
      data: { label: n.label, agent: n.agent, state: states[n.id] || "idle", sub: timings[n.id] },
    }));
    const edges: Edge[] = es.map((e, i) => {
      const isBack = back.has(`${e.source}>${e.target}`);
      const hot = states[e.source] === "done" && ["active", "done", "waiting"].includes(states[e.target] || "");
      return {
        id: `${e.source}-${e.target}-${i}`, source: e.source, target: e.target, type: isBack ? "default" : "smoothstep",
        animated: states[e.target] === "active", markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
        style: { strokeDasharray: e.conditional ? "5 4" : undefined, stroke: hot ? "rgb(var(--ok))" : "rgb(var(--muted))", strokeWidth: hot ? 1.75 : 1, opacity: isBack ? 0.45 : 0.9 },
      };
    });
    return { nodes, edges };
  }, [topology, states, timings, expand]);

  return (
    <div style={{ height }} className="rounded-lg border border-line bg-bg" data-testid="graph">
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.12 }} minZoom={0.3} nodesConnectable={false}
        proOptions={{ hideAttribution: true }} onNodeClick={(_, n) => onSelect?.(n.id)} key={expand || "top"}>
        <Background gap={20} color="rgb(var(--line))" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

/** Derives per-node execution state from the persisted node_start/node_end events of a run. */
export function nodeStates(events: { type: string; node: string | null; payload: any }[], runStatus?: string) {
  const states: Record<string, NodeState> = {}, timings: Record<string, string> = {};
  const total: Record<string, number> = {};
  for (const e of events) {
    if (!e.node) continue;
    const top = e.node.split(".")[0], sub = e.node.includes(".") ? `${top}:${e.node.split(".")[1]}` : null;
    for (const id of [top, sub]) {
      if (!id) continue;
      if (e.type === "node_start") states[id] = "active";
      else if (e.type === "node_end") {
        states[id] = e.payload?.status === "ok" ? "done" : "error";
        if (id === (sub || top)) total[id] = (total[id] || 0) + (e.payload?.duration_ms || 0);
      }
    }
  }
  Object.entries(total).forEach(([k, v]) => (timings[k] = `${v}ms`));
  if (events.length) states["__start__"] = "done";
  if (runStatus === "waiting_approval") states["approval_gate"] = "waiting";
  if (runStatus === "completed") states["__end__"] = "done";
  if (runStatus && ["completed", "failed", "cancelled", "paused"].includes(runStatus))
    Object.keys(states).forEach((k) => states[k] === "active" && (states[k] = runStatus === "completed" ? "done" : "error"));
  return { states, timings };
}
