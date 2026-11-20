"""Demo business-metrics MCP server (weekly KPI warehouse). Simulated provider backed by synthetic data."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.mcp.servers._base import READ, conn, run_server, ws

mcp = FastMCP("metrics-demo", instructions="Simulated KPI warehouse. Data is synthetic.")

DEFINITIONS = {
    "net_new_arr": {"label": "Net new ARR", "unit": "USD", "direction": "up", "description": "Net new annual recurring revenue booked in the week."},
    "pipeline_coverage": {"label": "Pipeline coverage", "unit": "x", "direction": "up", "description": "Qualified pipeline divided by remaining quarterly quota."},
    "gross_margin": {"label": "Gross margin", "unit": "%", "direction": "up", "description": "Gross margin on recognised revenue."},
    "logo_churn": {"label": "Logo churn", "unit": "%", "direction": "down", "description": "Annualised customer logo churn."},
    "nps": {"label": "NPS", "unit": "pts", "direction": "up", "description": "Net promoter score, trailing four weeks."},
    "uptime": {"label": "Platform uptime", "unit": "%", "direction": "up", "description": "Weekly availability of the production platform."},
    "p1_open": {"label": "Open P1 tickets", "unit": "count", "direction": "down", "description": "Unresolved priority-1 support tickets at week end."},
    "resolution_hours": {"label": "Median resolution time", "unit": "h", "direction": "down", "description": "Median time to resolve support tickets."},
    "on_time_delivery": {"label": "On-time delivery", "unit": "%", "direction": "up", "description": "Share of roadmap commitments delivered on schedule."},
    "cloud_cost": {"label": "Cloud spend", "unit": "USD", "direction": "down", "description": "Weekly infrastructure spend."},
}


@mcp.tool(annotations=READ)
def list_metrics(context: dict | None = None) -> dict:
    """List available KPI definitions (label, unit, desired direction)."""
    return {"metrics": DEFINITIONS}


@mcp.tool(annotations=READ)
def get_weekly_kpis(weeks: int = 8, metrics: list[str] | None = None, context: dict | None = None) -> dict:
    """Return weekly KPI values and targets for the most recent `weeks` weeks (max 26)."""
    weeks = max(1, min(int(weeks), 26))
    rows = conn().execute(
        """SELECT week_start, metric, value, target FROM kpi_weekly WHERE workspace_id=%s
           AND week_start IN (SELECT DISTINCT week_start FROM kpi_weekly WHERE workspace_id=%s ORDER BY week_start DESC LIMIT %s)
           ORDER BY week_start, metric""", (ws(context), ws(context), weeks)).fetchall()
    series: dict[str, list[dict]] = {}
    for r in rows:
        if metrics and r["metric"] not in metrics:
            continue
        series.setdefault(r["metric"], []).append(
            {"week_start": r["week_start"].isoformat(), "value": r["value"], "target": r["target"]})
    return {"series": series, "definitions": {k: v for k, v in DEFINITIONS.items() if k in series},
            "weeks": weeks, "synthetic": True}


if __name__ == "__main__":
    run_server(mcp)
