"""Assemble the LangGraph StateGraph: supervisor -> agent subgraphs -> compliance -> approval -> execution -> synthesis -> evaluation."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.graph import analytics, nodes, research, tasks
from app.graph.state import AgentState

AGENT_NODES = ("research", "analytics", "task", "communication")
NODE_META = {
    "intake": ("Intake guardrails", "supervisor"), "supervisor": ("Supervisor / planner", "supervisor"),
    "research": ("Research agent (RAG subgraph)", "research"), "analytics": ("Analytics agent (KPI subgraph)", "analytics"),
    "task": ("Task agent (action subgraph)", "task"), "communication": ("Communication agent", "communication"),
    "compliance": ("Compliance agent / policy engine", "compliance"), "approval_gate": ("Human approval gate", "compliance"),
    "revise_actions": ("Revise actions", "communication"), "execute_actions": ("MCP action execution", "task"),
    "synthesizer": ("Synthesizer agent", "synthesizer"), "evaluator": ("Evaluator agent", "evaluator"),
    "finalize": ("Finalize", "supervisor"),
}


def build_graph() -> StateGraph:
    g = StateGraph(AgentState)
    g.add_node("intake", nodes.intake)
    g.add_node("supervisor", nodes.supervisor)
    g.add_node("research", research.build())
    g.add_node("analytics", analytics.build())
    g.add_node("task", tasks.build())
    g.add_node("communication", tasks.communication)
    g.add_node("compliance", nodes.compliance)
    g.add_node("approval_gate", nodes.approval_gate)
    g.add_node("revise_actions", nodes.revise_actions)
    g.add_node("execute_actions", nodes.execute_actions)
    g.add_node("synthesizer", nodes.synthesizer)
    g.add_node("evaluator", nodes.evaluator)
    g.add_node("finalize", nodes.finalize)

    g.add_edge(START, "intake")
    g.add_edge("intake", "supervisor")
    g.add_conditional_edges("supervisor", lambda s: s["route"], {**{a: a for a in AGENT_NODES}, "compliance": "compliance"})
    for a in AGENT_NODES:
        g.add_edge(a, "supervisor")
    g.add_conditional_edges("compliance", nodes.after_compliance,
                            {"approval_gate": "approval_gate", "execute_actions": "execute_actions", "synthesizer": "synthesizer"})
    g.add_conditional_edges("approval_gate", nodes.after_approval,
                            {"revise_actions": "revise_actions", "execute_actions": "execute_actions", "synthesizer": "synthesizer"})
    g.add_edge("revise_actions", "compliance")
    g.add_edge("execute_actions", "synthesizer")
    g.add_edge("synthesizer", "evaluator")
    g.add_conditional_edges("evaluator", lambda s: s["route"], {"synthesizer": "synthesizer", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g


def topology() -> dict:
    """Graph structure for the Workflow Studio, derived from the compiled graph (including subgraph internals)."""
    drawable = build_graph().compile().get_graph(xray=1)
    out_nodes, out_edges = [], []
    for nid in drawable.nodes:
        if nid in ("__start__", "__end__"):
            out_nodes.append({"id": nid, "label": "Start" if nid == "__start__" else "End", "agent": "system", "parent": None})
            continue
        parent, _, leaf = nid.rpartition(":")
        if parent:
            out_nodes.append({"id": nid, "label": leaf.replace("_", " "), "agent": NODE_META[parent][1], "parent": parent,
                              "event_node": f"{parent}.{leaf}"})
        else:
            label, agent = NODE_META.get(nid, (nid, "system"))
            out_nodes.append({"id": nid, "label": label, "agent": agent, "parent": None, "event_node": nid})
    for e in drawable.edges:
        out_edges.append({"source": e.source, "target": e.target, "conditional": bool(e.conditional)})
    groups = [{"id": a, "label": NODE_META[a][0], "agent": NODE_META[a][1]} for a in ("research", "analytics", "task")]
    return {"nodes": out_nodes, "edges": out_edges, "groups": groups}
