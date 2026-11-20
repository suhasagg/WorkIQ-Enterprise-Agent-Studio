import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { nodeStates } from "@/components/graph";
import { Launcher } from "@/components/launch";
import { Login } from "@/components/login";
import { Report } from "@/components/report";
import { Timeline } from "@/components/timeline";
import { Async, StatusBadge } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { ago, ms, pct, title } from "@/lib/format";
import { mockApi, renderApp, USER } from "./util";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/", useSearchParams: () => new URLSearchParams() }));

const WORKFLOWS = [{ id: "weekly_business_review", name: "Weekly Executive Business Review", description: "d", prompt: "p", agents: [], requires_approval: true }];
const SYSTEM = { fault_injection: true, demo_mode: true, model: { model: "m" }, embeddings: { model: "e" } };

describe("format helpers", () => {
  it("formats durations, percentages and titles", () => {
    expect(ms(420)).toBe("420 ms");
    expect(ms(2400)).toBe("2.4 s");
    expect(ms(null)).toBe("—");
    expect(pct(0.857, 1)).toBe("85.7%");
    expect(title("waiting_approval")).toBe("Waiting approval");
    expect(ago(new Date(Date.now() - 5 * 60_000).toISOString())).toBe("5 min ago");
  });
});

describe("api client", () => {
  it("raises ApiError with the server's detail message", async () => {
    mockApi({ "GET /api/x": { status: 403, body: { detail: "missing scope agent:run" } } });
    await expect(api("/api/x")).rejects.toMatchObject({ status: 403, message: "missing scope agent:run" });
    await expect(api("/api/x")).rejects.toBeInstanceOf(ApiError);
  });
  it("joins validation errors", async () => {
    mockApi({ "POST /api/y": { status: 422, body: { detail: [{ msg: "too short" }, { msg: "bad mode" }] } } });
    await expect(api("/api/y", { method: "POST", json: {} })).rejects.toThrow("too short; bad mode");
  });
});

describe("Async states", () => {
  it("shows loading, error with retry, empty and data", () => {
    const retry = vi.fn();
    const { rerender } = render(<Async data={undefined} error={null}>{() => <p>data</p>}</Async>);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    rerender(<Async data={undefined} error={new Error("backend down")} retry={retry}>{() => <p>data</p>}</Async>);
    expect(screen.getByRole("alert")).toHaveTextContent("backend down");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalled();
    rerender(<Async data={[]} error={null} empty={<p>nothing here</p>}>{() => <p>data</p>}</Async>);
    expect(screen.getByText("nothing here")).toBeInTheDocument();
    rerender(<Async data={[1]} error={null}>{(d) => <p>rows {d.length}</p>}</Async>);
    expect(screen.getByText("rows 1")).toBeInTheDocument();
  });
  it("labels statuses for people", () => {
    render(<><StatusBadge status="waiting_approval" /><StatusBadge status="approval_required" /></>);
    expect(screen.getByText("Waiting for approval")).toBeInTheDocument();
    expect(screen.getByText("Approval required")).toBeInTheDocument();
  });
});

describe("Timeline", () => {
  const ev = (seq: number, type: string, payload: any = {}, agent: string | null = null) => ({ seq, ts: "2026-11-20T08:00:00Z", type, node: null, agent, payload });
  const events = [
    ev(1, "run_started"), ev(2, "node_start"), ev(3, "plan", { steps: [{ agent: "research", title: "Retrieve" }] }, "supervisor"),
    ev(4, "step_retry", { attempt: 2, error: "simulated retrieval outage" }, "research"), ev(5, "replan", { change: "added s1b", reason: "step s1 failed" }),
    ev(6, "run_waiting_approval"), ev(7, "approval_decision", { by: "Priya", decision: "rejected", title: "Send email", comment: "no" }),
  ];
  it("renders readable entries and hides node-level noise by default", () => {
    render(<Timeline events={events} />);
    expect(screen.getByText("Plan created: 1 steps")).toBeInTheDocument();
    expect(screen.getByText("Retrying step (attempt 2)")).toBeInTheDocument();
    expect(screen.getByText(/Replanned — added s1b/)).toBeInTheDocument();
    expect(screen.getByText(/waiting for human approval/)).toBeInTheDocument();
    expect(screen.getByText("Priya rejected: Send email")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
  });
  it("shows node events in verbose mode and an empty state", () => {
    const { rerender } = render(<Timeline events={events} verbose />);
    expect(screen.getAllByRole("listitem")).toHaveLength(7);
    rerender(<Timeline events={[]} />);
    expect(screen.getByText("No events yet.")).toBeInTheDocument();
  });
});

describe("graph node states", () => {
  const e = (type: string, node: string, status = "ok") => ({ type, node, payload: { status, duration_ms: 5 } });
  it("derives active, done, waiting and error states from events", () => {
    const { states, timings } = nodeStates([e("node_start", "intake"), e("node_end", "intake"), e("node_start", "research.retrieve"), e("node_end", "research.retrieve"), e("node_start", "approval_gate")], "waiting_approval");
    expect(states.intake).toBe("done");
    expect(states["research:retrieve"]).toBe("done");
    expect(states.research).toBe("done");
    expect(states.approval_gate).toBe("waiting");
    expect(timings["research:retrieve"]).toBe("5ms");
    expect(nodeStates([e("node_start", "x"), e("node_end", "x", "error")], "failed").states.x).toBe("error");
  });
});

describe("Report", () => {
  const result = {
    title: "Weekly executive business review", summary: "Nine KPIs need attention.", partial: true, simulated_sources: true,
    sections: [
      { kind: "claims", title: "Operational risks", items: [{ text: "Two engineers hold most knowledge.", marks: "[1]" }] },
      { kind: "actions", title: "Actions", items: [{ text: "Create follow-up", tool: "tracker.create_issue", risk: "medium", status: "executed", reference: "WIQ-1" }] },
    ],
    citations: [{ n: 1, title: "Operational Risk Register", heading: "Risks", snippet: "Two engineers hold most of the operational knowledge.", filename: "risk.md", highlights: ["engineers"] }],
    evaluation: { scores: { groundedness: 1, completeness: 0.5 }, claims_checked: 1, method: "deterministic lexical support" },
    guardrails: [{ type: "prompt_injection_in_document", action: "instructions quarantined", document: "Vendor newsletter" }],
  };
  it("renders sections, labels, scores, guardrails and highlighted evidence", async () => {
    render(<Report result={result} />);
    expect(screen.getByText("Partial result")).toBeInTheDocument();
    expect(screen.getByText("Simulated")).toBeInTheDocument();
    expect(screen.getByText("Executed")).toBeInTheDocument();
    expect(screen.getByText(/groundedness 100%/)).toBeInTheDocument();
    expect(screen.getByText(/prompt injection in document/)).toBeInTheDocument();
    expect(screen.getByText("engineers", { selector: "mark" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show source 1" }));
    expect(document.getElementById("cite-1")).toHaveClass("border-accent");
  });
});

describe("Launcher", () => {
  it("starts a run with the selected options", async () => {
    const calls = mockApi({
      "GET /api/auth/me": { body: USER }, "GET /api/workflows": { body: WORKFLOWS }, "GET /api/system": { body: SYSTEM },
      "POST /api/runs": { status: 201, body: { id: "run_1" } },
    });
    const onStarted = vi.fn();
    renderApp(<Launcher onStarted={onStarted} />);
    const button = screen.getByRole("button", { name: /Run workflow/ });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(screen.getByLabelText(/Dry run/));
    await userEvent.selectOptions(await screen.findByLabelText("Failure simulation (demo)"), "research.retrieve");
    await userEvent.click(button);
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith({ id: "run_1" }));
    expect(calls.find((c) => c.key === "POST /api/runs")!.body).toEqual({
      workflow: "weekly_business_review", request: null, options: { dry_run: true, fault_injection: { node: "research.retrieve", times: 2 } },
    });
  });
  it("is disabled for a read-only role", async () => {
    mockApi({ "GET /api/auth/me": { body: { ...USER, role: "viewer", scopes: ["workiq:read"] } }, "GET /api/workflows": { body: WORKFLOWS }, "GET /api/system": { body: SYSTEM } });
    renderApp(<Launcher onStarted={vi.fn()} />);
    expect(await screen.findByText(/read-only/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Run workflow/ })).toBeDisabled();
  });
  it("surfaces a backend error as a toast", async () => {
    mockApi({ "GET /api/auth/me": { body: USER }, "GET /api/workflows": { body: WORKFLOWS }, "GET /api/system": { body: SYSTEM }, "POST /api/runs": { status: 429, body: { detail: "rate limit exceeded" } } });
    renderApp(<Launcher onStarted={vi.fn()} />);
    const button = screen.getByRole("button", { name: /Run workflow/ });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);
    expect(await screen.findByText("rate limit exceeded")).toBeInTheDocument();
  });
});

describe("Login", () => {
  it("shows the server error for bad credentials", async () => {
    mockApi({ "GET /api/auth/me": { status: 401, body: { detail: "not authenticated" } }, "GET /api/auth/demo-users": { body: { demo_mode: false, users: [] } }, "POST /api/auth/login": { status: 401, body: { detail: "invalid email or password" } } });
    renderApp(<Login />);
    await userEvent.type(screen.getByLabelText("Email"), "a@b.co");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid email or password");
  });
});
