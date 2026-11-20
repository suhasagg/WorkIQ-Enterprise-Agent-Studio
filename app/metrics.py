"""Prometheus metrics exposed at /metrics."""
from prometheus_client import Counter, Histogram

RUNS = Counter("workplace_agent_runs_total", "Workplace agent runs", ["workflow", "status"])
TOOL_CALLS = Counter("workiq_tool_calls_total", "MCP tool calls", ["server", "tool", "status"])
TOOL_LATENCY = Histogram("workiq_tool_latency_seconds", "MCP tool latency", ["server"])
LLM_CALLS = Counter("workiq_llm_calls_total", "Model calls", ["provider", "model"])
NODE_LATENCY = Histogram("workiq_node_latency_seconds", "LangGraph node latency", ["node"])
