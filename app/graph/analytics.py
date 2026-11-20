"""Analytics agent — KPI subgraph: fetch -> (analyze_kpis -> correlate | analyze_tickets)."""
from __future__ import annotations

import statistics
from collections import defaultdict

from app.context import RunCtx
from app.graph.runtime import StepError, linear_subgraph, node
from app.graph.state import AgentState, current_step
from app.mcp.gateway import call_tool

AGENT = "analytics"
CATEGORY_LABELS = {"sso_login": "SSO login failures", "data_sync": "Data sync delays", "payments": "Payments errors",
                   "billing_export": "Billing export defects", "notifications": "Notification delays"}


def analyse_series(metric: str, points: list[dict], definition: dict) -> dict:
    values = [p["value"] for p in points]
    latest, prev, target = values[-1], values[-2] if len(values) > 1 else values[-1], points[-1].get("target")
    up = definition.get("direction", "up") == "up"
    base = values[:-1] if len(values) > 2 else values
    mean, sd = statistics.fmean(base), (statistics.pstdev(base) if len(base) > 1 else 0.0)
    z = 0.0 if sd == 0 else (latest - mean) / sd
    off = target is not None and ((latest < target) if up else (latest > target))
    gap = 0.0 if not target else (latest - target) / abs(target) * 100
    streak = 0
    for a, b in zip(reversed(values[:-1]), reversed(values[1:]), strict=False):
        if (b < a) if up else (b > a):
            streak += 1
        else:
            break
    adverse_z = -z if up else z
    anomaly = bool(off and (abs(gap) >= 5 or adverse_z >= 1.5 or streak >= 3))
    severity = "high" if anomaly and (abs(gap) >= 10 or adverse_z >= 2.5) else "medium" if anomaly else "none"
    return {"metric": metric, "label": definition.get("label", metric), "unit": definition.get("unit", ""),
            "description": definition.get("description", ""), "direction": definition.get("direction", "up"),
            "latest": latest, "previous": prev, "target": target, "wow_pct": round((latest - prev) / abs(prev) * 100, 2) if prev else 0.0,
            "gap_pct": round(gap, 2), "zscore": round(z, 2), "adverse_streak": streak, "off_target": bool(off),
            "anomaly": anomaly, "severity": severity, "series": points}


def pearson(a: list[float], b: list[float]) -> float:
    if len(a) < 3 or len(a) != len(b):
        return 0.0
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return 0.0 if den == 0 else sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / den


@node("analytics.fetch", AGENT, step=True)
async def fetch(state: AgentState, ctx: RunCtx) -> dict:
    step = current_step(state)
    if step.get("mode") == "tickets":
        res = await call_tool(ctx, "helpdesk", "search_tickets", {"status": "unresolved", "limit": 100})
    else:
        res = await call_tool(ctx, "metrics", "get_weekly_kpis", {"weeks": int(step.get("params", {}).get("weeks", 12))})
    if res["status"] != "ok":
        raise StepError(f"{res['server']}.{res['tool']} {res['status']}: {res.get('reason')}")
    return {"scratch": {"raw": res["result"], "mode": step.get("mode")}, "step_result": None}


@node("analytics.analyze_kpis", AGENT, step=True)
async def analyze_kpis(state: AgentState, ctx: RunCtx) -> dict:
    raw = state["scratch"]["raw"]
    if not raw.get("series"):
        raise StepError("KPI source returned no data")
    kpis = [analyse_series(m, pts, raw["definitions"].get(m, {})) for m, pts in sorted(raw["series"].items())]
    anomalies = sorted([k for k in kpis if k["anomaly"]], key=lambda k: (k["severity"] != "high", -abs(k["gap_pct"])))
    ctx.emit({"type": "analysis", "kind": "kpi", "metrics": len(kpis),
              "anomalies": [{"metric": a["metric"], "severity": a["severity"], "gap_pct": a["gap_pct"]} for a in anomalies]})
    return {"analytics": {"kind": "kpi", "kpis": kpis, "anomalies": [{k: v for k, v in a.items() if k != "series"} for a in anomalies],
                          "weeks": raw["weeks"], "source": "metrics.get_weekly_kpis", "synthetic": bool(raw.get("synthetic"))}}


@node("analytics.correlate", AGENT, step=True)
async def correlate(state: AgentState, ctx: RunCtx) -> dict:
    an = state["analytics"]
    series = {k["metric"]: [p["value"] for p in k["series"]] for k in an["kpis"]}
    labels = {k["metric"]: k["label"] for k in an["kpis"]}
    names = [a["metric"] for a in an["anomalies"]]
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            r = pearson(series[a], series[b])
            if abs(r) >= 0.8:
                pairs.append({"a": a, "b": b, "a_label": labels[a], "b_label": labels[b], "r": round(r, 2)})
    pairs.sort(key=lambda p: -abs(p["r"]))
    n = len(an["anomalies"])
    summary = f"{n} of {len(an['kpis'])} KPIs anomalous" + (f"; strongest link {pairs[0]['a_label']} ~ {pairs[0]['b_label']} (r={pairs[0]['r']})" if pairs else "")
    return {"analytics": {**an, "correlations": pairs[:6]}, "step_result": {"status": "completed", "summary": summary}}


@node("analytics.analyze_tickets", AGENT, step=True)
async def analyze_tickets(state: AgentState, ctx: RunCtx) -> dict:
    tickets = state["scratch"]["raw"].get("tickets", [])
    if not tickets:
        return {"analytics": {"kind": "tickets", "tickets": [], "clusters": [], "breaches": [], "totals": {"open": 0}},
                "step_result": {"status": "completed", "summary": "No unresolved tickets"}}
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for t in tickets:
        by_cat[t.get("category", "other")].append(t)
    clusters = sorted(({"category": c, "label": CATEGORY_LABELS.get(c, c.replace("_", " ").title()), "count": len(ts),
                        "p1": sum(t["priority"] == "p1" for t in ts), "breached": sum(bool(t.get("sla_breached")) for t in ts),
                        "acv_usd": sum(t.get("acv_usd", 0) for t in {t["customer"]: t for t in ts}.values()),
                        "customers": sorted({t["customer"] for t in ts}), "tickets": [t["id"] for t in ts],
                        "recurring": len(ts) >= 3} for c, ts in by_cat.items()), key=lambda c: (-c["count"], -c["acv_usd"]))
    breaches = sorted([t for t in tickets if t.get("sla_breached")],
                      key=lambda t: (t["priority"], -t.get("acv_usd", 0), -t.get("age_hours", 0)))
    slim = lambda t: {k: t.get(k) for k in ("id", "priority", "status", "category", "customer", "acv_usd", "age_hours",  # noqa: E731
                                            "sla_hours", "sla_breached", "subject", "escalated", "tier")}
    totals = {"open": len(tickets), "p1": sum(t["priority"] == "p1" for t in tickets), "breached": len(breaches),
              "p1_breached": sum(t["priority"] == "p1" for t in breaches), "customers": len({t["customer"] for t in tickets})}
    ctx.emit({"type": "analysis", "kind": "tickets", **totals, "recurring": [c["category"] for c in clusters if c["recurring"]]})
    summary = f"{totals['open']} unresolved tickets, {totals['p1_breached']} P1 in SLA breach, " \
              f"{sum(c['recurring'] for c in clusters)} recurring problem(s)"
    return {"analytics": {"kind": "tickets", "tickets": [slim(t) for t in tickets], "clusters": clusters,
                          "breaches": [slim(t) for t in breaches], "totals": totals, "source": "helpdesk.search_tickets",
                          "synthetic": True},
            "step_result": {"status": "completed", "summary": summary}}


analyze_tickets.terminal = True  # type: ignore[attr-defined]


def build():
    return linear_subgraph(
        [("fetch", fetch), ("analyze_tickets", analyze_tickets), ("analyze_kpis", analyze_kpis), ("correlate", correlate)],
        branches={"fetch": (lambda s: "analyze_tickets" if s["scratch"].get("mode") == "tickets" else "analyze_kpis",
                            {"analyze_tickets": "analyze_tickets", "analyze_kpis": "analyze_kpis"})},
    ).compile()
