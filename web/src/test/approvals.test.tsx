import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ApprovalsPage from "@/app/approvals/page";
import { mockApi, renderApp, USER } from "./util";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/approvals", useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));

const APPROVAL = {
  id: "apr_1", run_id: "run_1", status: "pending", risk: "high", server: "workiq", tool: "do_action", title: "Send leadership summary email",
  rationale: "Leadership asked for a weekly summary.", policy_reason: "mutation requires explicit approval", params: { entityUrl: "/me/sendMail", body: { subject: "WBR" } },
  edited_params: null, params_hash: "abcdef0123456789", evidence: [{ title: "Risk Register", snippet: "Vendor concentration risk." }], requesting_agent: "communication",
  requested_by: "Jordan Blake", created_at: new Date().toISOString(), round: 0, workflow: "weekly_business_review", run_status: "waiting_approval", audit: [], execution: null,
};
const routes = (me: any) => ({
  "GET /api/auth/me": { body: me }, "GET /api/approvals": { body: [APPROVAL] }, "GET /api/approvals/apr_1": { body: APPROVAL },
  "GET /api/mcp/servers": { body: [{ id: "workiq", kind: "demo" }] }, "POST /api/approvals/apr_1/decision": { body: { status: "rejected", run_resumed: true } },
});

describe("Approval Center", () => {
  it("requires a comment to reject, then posts the decision", async () => {
    const calls = mockApi(routes({ ...USER, role: "approver", scopes: [...USER.scopes, "approval:decide"] }));
    renderApp(<ApprovalsPage />);
    expect(await screen.findByTestId("approval-detail")).toHaveTextContent("Leadership asked for a weekly summary.");
    expect(screen.getByText("Simulated")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Add a comment");
    expect(calls.some((c) => c.key.startsWith("POST"))).toBe(false);
    await userEvent.type(screen.getByLabelText(/^Comment/), "Wrong recipients");
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(calls.find((c) => c.key === "POST /api/approvals/apr_1/decision")?.body).toMatchObject({ decision: "reject", comment: "Wrong recipients" }));
  });

  it("rejects invalid JSON when approving with edits and sends edited parameters when valid", async () => {
    const calls = mockApi(routes({ ...USER, role: "approver", scopes: [...USER.scopes, "approval:decide"] }));
    renderApp(<ApprovalsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const box = screen.getByLabelText("Edited parameters (JSON)");
    await userEvent.clear(box);
    await userEvent.type(box, "not json");
    await userEvent.click(screen.getByRole("button", { name: "Approve with edits" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("valid JSON");
    await userEvent.clear(box);
    await userEvent.paste('{"entityUrl":"/me/sendMail","body":{"subject":"Edited"}}');
    await userEvent.click(screen.getByRole("button", { name: "Approve with edits" }));
    await waitFor(() => expect(calls.find((c) => c.key.startsWith("POST"))?.body).toMatchObject({ decision: "edit", edited_params: { body: { subject: "Edited" } } }));
  });

  it("hides decision controls from roles without approval:decide", async () => {
    mockApi(routes(USER));
    renderApp(<ApprovalsPage />);
    expect(await screen.findByText(/Your role cannot decide approvals/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});
