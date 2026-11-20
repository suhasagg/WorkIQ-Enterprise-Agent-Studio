/**
 * Reproducible screenshot tour of every feature, taken from the real running application.
 * Nothing is mocked: each screen shows data produced by actually running workflows, approvals, uploads and evaluations.
 */
import { expect, type Page, test } from "@playwright/test";
import path from "node:path";

const OUT = path.resolve(__dirname, "../../docs/screenshots");
const USERS = { admin: /Avery Chen/, approver: /Priya Raman/, operator: /Jordan Blake/, viewer: /Sam Okafor/, globex: /Globex Retail/ };

async function shot(page: Page, name: string, fullPage = false) {
  await expect(page.getByTestId("toast")).toHaveCount(0, { timeout: 10_000 }); // do not capture transient notifications
  await page.waitForTimeout(700); // let charts and transitions settle
  // A sticky header would be painted mid-page in a full-page capture.
  const style = fullPage ? await page.addStyleTag({ content: "header.sticky{position:static!important}" }) : null;
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage });
  await style?.evaluate((el) => (el as HTMLElement).remove());
}

async function login(page: Page, who: keyof typeof USERS) {
  await page.context().clearCookies();
  await page.goto("/");
  await page.getByRole("button", { name: USERS[who] }).first().click();
  await expect(page.getByRole("heading", { name: "AI Operations Command Center" })).toBeVisible();
}

async function startRun(page: Page, body: Record<string, unknown>): Promise<string> {
  const res = await page.request.post("/api/runs", { data: body });
  expect(res.status()).toBe(201);
  return (await res.json()).id;
}

async function waitRun(page: Page, id: string, until: string[]) {
  await expect.poll(async () => (await (await page.request.get(`/api/runs/${id}`)).json()).status, { timeout: 60_000 }).toMatch(new RegExp(`^(${until.join("|")})$`));
}

const status = (page: Page, s: string) => expect(page.getByTestId("run-view")).toHaveAttribute("data-run-status", s, { timeout: 60_000 });

test("capture the feature tour", async ({ page }) => {
  // ---------------------------------------------------------------- sign-in
  await page.goto("/");
  await expect(page.getByText("Demo accounts")).toBeVisible();
  await shot(page, "11-login-demo-accounts");

  // ---------------------------------------------------------------- operator starts work
  await login(page, "operator");
  const wbr = await startRun(page, { workflow: "weekly_business_review" });
  await waitRun(page, wbr, ["waiting_approval"]);
  const meeting = await startRun(page, { workflow: "meeting_to_execution" });
  await waitRun(page, meeting, ["waiting_approval"]);
  const faulty = await startRun(page, { workflow: "knowledge_research", options: { fault_injection: { node: "research.retrieve", times: 2 } } });
  await waitRun(page, faulty, ["completed"]);
  const injected = await startRun(page, { workflow: "knowledge_research", request: "Summarise the latest Halcyon Freight Data vendor newsletter and its pricing changes." });
  await waitRun(page, injected, ["completed"]);
  const unknown = await startRun(page, { request: "What is the capital expenditure budget for Project Lighthouse in Antarctica?" });
  await waitRun(page, unknown, ["completed"]);

  // workflow studio with the paused run on the graph
  await page.goto(`/studio?run=${wbr}`);
  await expect(page.locator('[data-testid="graph-node-Human approval gate"]')).toHaveAttribute("data-state", "waiting");
  await shot(page, "02-langgraph-workflow");
  await page.locator('[data-testid="graph-node-Research agent"]').click();
  await expect(page.getByText(/Subgraph: Research agent/)).toBeVisible();
  await shot(page, "12-research-subgraph");

  // run page: timeline while paused at the interrupt
  await page.goto(`/runs/${wbr}`);
  await status(page, "waiting_approval");
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await shot(page, "09-workflow-execution");
  await page.getByRole("tab", { name: "Graph" }).click();
  await shot(page, "13-run-graph-paused-at-interrupt");
  await page.getByRole("tab", { name: "State & checkpoints" }).click();
  await expect(page.getByText(/PostgreSQL checkpoints \(\d+\)/)).toBeVisible();
  await shot(page, "16-checkpointed-state");

  // failure recovery
  await page.goto(`/runs/${faulty}`);
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await expect(page.getByTestId("timeline")).toContainText("Replanned");
  await shot(page, "18-failure-retry-and-replan");

  // guardrails
  await page.goto(`/runs/${injected}`);
  await expect(page.getByTestId("report")).toContainText("Guardrail events");
  await shot(page, "26-prompt-injection-quarantined", true);
  await page.goto(`/runs/${unknown}`);
  await expect(page.getByTestId("report")).toContainText("No sufficiently relevant evidence");
  await shot(page, "37-unanswerable-question-abstains");

  // chat: one grounded answer, then a request that pauses for approval
  await page.goto("/chat");
  await page.getByLabel("Message", { exact: true }).fill("What are our major operational risks and who owns them?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByTestId("messages").getByTestId("report")).toContainText("Sources", { timeout: 60_000 });
  await page.getByTestId("messages").evaluate((el) => el.scrollTo(0, 0));
  await shot(page, "03-multi-agent-chat");
  await page.getByLabel("Message", { exact: true }).fill("Which priority-one tickets are breaching SLA and what should we escalate?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByTestId("live-run")).toContainText("need human approval", { timeout: 60_000 });
  await shot(page, "19-chat-live-agents-awaiting-approval");

  // knowledge studio
  await page.goto("/knowledge");
  await expect(page.getByTestId("documents")).toBeVisible();
  await page.getByLabel(/^Document/).setInputFiles({
    name: "incident-communication-playbook.md", mimeType: "text/markdown",
    buffer: Buffer.from("# Incident Communication Playbook\n\n## Customer updates\n\nDuring a priority-one incident the duty manager sends a customer update every sixty minutes until service is restored.\n\n## Status page\n\nThe status page is updated within fifteen minutes of incident declaration and again at resolution.\n"),
  });
  await page.getByLabel("Tags (comma separated)").fill("support, incident");
  await page.getByRole("button", { name: "Upload and index" }).click();
  await expect(page.getByTestId("documents").getByRole("row").filter({ hasText: "incident-communication-playbook.md" })).toContainText("Indexed");
  await page.getByLabel("Query").fill("SSO login failure workaround");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByTestId("search-results")).toBeVisible();
  await shot(page, "06-knowledge-studio");
  await shot(page, "23-hybrid-search-highlights-and-uploaded-document", true);
  await page.getByRole("button", { name: /^Known Issues Knowledge Base/ }).click();
  await expect(page.getByRole("dialog")).toContainText("chunks");
  await shot(page, "24-document-chunks");
  await page.getByRole("button", { name: "Close dialog" }).click();

  // MCP hub
  await page.goto("/integrations");
  await page.getByRole("button", { name: /Work IQ/ }).click();
  await expect(page.getByTestId("server-detail")).toContainText("search_paths");
  await shot(page, "05-mcp-integrations");
  await page.getByRole("button", { name: /Business Metrics/ }).click();
  await page.getByRole("row").filter({ hasText: "get_weekly_kpis" }).getByRole("button", { name: "Try" }).click();
  await page.getByRole("button", { name: "Invoke through gateway" }).click();
  await expect(page.getByTestId("tool-result")).toContainText("OK");
  await shot(page, "20-tool-console-read-allowed");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: /Work Tracker/ }).click();
  await page.getByRole("row").filter({ hasText: "create_issue" }).getByRole("button", { name: "Try" }).click();
  await page.getByLabel(/^Arguments/).fill('{\n  "title": "Rotate SSO certificates",\n  "description": "Created from the tool console"\n}');
  await page.getByRole("button", { name: "Invoke through gateway" }).click();
  await expect(page.getByTestId("tool-result")).toContainText("Approval required");
  await shot(page, "21-tool-console-write-held-for-approval");

  // ---------------------------------------------------------------- approver decides
  await login(page, "approver");
  await page.goto(`/approvals?run=${wbr}`);
  await expect(page.getByTestId("approval-detail")).toBeVisible();
  await shot(page, "04-human-approval");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await shot(page, "14-approval-edit-parameters");
  await page.getByRole("button", { name: "Discard edits" }).click();
  // reject the first action with a reason, approve the others
  await page.getByLabel(/^Comment/).fill("Hold external communication until the root cause is confirmed.");
  const list = page.getByTestId("approval-list");
  const before = await list.getByRole("listitem").count();
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(list.getByRole("listitem")).toHaveCount(before - 1);
  for (let left = before - 1; left > 0; left--) {
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    if (left > 1) await expect(list.getByRole("listitem")).toHaveCount(left - 1);
  }
  await waitRun(page, wbr, ["completed"]);
  await page.getByRole("tab", { name: /Decided/ }).click();
  await page.getByTestId("approval-list").getByRole("listitem").first().click();
  await page.getByText(/Audit trail/).click();
  await shot(page, "15-approval-decided-with-audit-trail", true);

  // the finished executive report
  await page.goto(`/runs/${wbr}`);
  await status(page, "completed");
  await expect(page.getByTestId("report")).toContainText("KPI scorecard");
  await shot(page, "10-executive-report", true);
  await page.getByRole("tab", { name: "Telemetry" }).click();
  await expect(page.getByText(/MCP tool calls \(\d+\)/)).toBeVisible();
  await shot(page, "17-run-telemetry", true);
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await page.getByTestId("timeline").getByRole("listitem").last().scrollIntoViewIfNeeded();
  await shot(page, "38-timeline-after-resume");

  // meeting-to-execution: approve everything, show the report
  for (const a of (await (await page.request.get(`/api/runs/${meeting}`)).json()).approvals)
    await page.request.post(`/api/approvals/${a.id}/decision`, { data: { decision: "approve", comment: "Approved" } });
  await waitRun(page, meeting, ["completed"]);
  await page.goto(`/runs/${meeting}`);
  await expect(page.getByTestId("report")).toContainText("Action items");
  await shot(page, "35-meeting-to-execution-report", true);

  // support escalation report (seeded run is still waiting: approve it)
  const runs = await (await page.request.get("/api/runs?limit=100")).json();
  const support = runs.find((r: any) => r.workflow === "support_escalation" && r.status === "waiting_approval" && r.origin === "seed") || runs.find((r: any) => r.workflow === "support_escalation");
  for (const a of (await (await page.request.get(`/api/runs/${support.id}`)).json()).approvals.filter((x: any) => x.status === "pending"))
    await page.request.post(`/api/approvals/${a.id}/decision`, { data: { decision: "approve", comment: "Approved" } });
  await waitRun(page, support.id, ["completed"]);
  await page.goto(`/runs/${support.id}`);
  await expect(page.getByTestId("report")).toContainText("SLA breaches");
  await shot(page, "36-support-escalation-report", true);

  // ---------------------------------------------------------------- admin views
  await login(page, "admin");
  await page.goto("/evals");
  await page.getByTestId("dataset-agent_workflows").getByRole("button", { name: "Run evaluation" }).click();
  await expect(page.getByText("Evaluation started")).toBeVisible();
  await expect(page.getByTestId("eval-run")).toContainText(/8 of 8 cases passed/, { timeout: 90_000 });
  await expect(page.getByTestId("eval-run")).toContainText("Quality gate passed");
  await shot(page, "08-evaluation-studio");
  await page.getByTestId("eval-run").getByRole("button", { name: /wf-01/ }).click();
  await shot(page, "27-evaluation-case-detail", true);
  await page.getByLabel("Retrieval mode").selectOption("vector");
  await page.getByLabel("Rerank").uncheck();
  await page.getByTestId("dataset-retrieval_quality").getByRole("button", { name: "Run evaluation" }).click();
  await expect(page.getByTestId("eval-run")).toContainText("retrieval vector", { timeout: 60_000 });
  await expect(page.getByTestId("eval-run")).toContainText("Quality gate", { timeout: 60_000 });
  await shot(page, "28-experiment-compared-with-baseline", true);

  await page.goto("/integrations");
  await page.getByRole("button", { name: "Register server" }).click();
  await page.getByLabel("Identifier").fill("acme-crm");
  await page.getByLabel("Display name").fill("Acme CRM");
  await page.getByLabel("Streamable HTTP endpoint").fill("https://mcp.acme.example/mcp");
  await page.getByLabel(/Credential environment variable/).fill("ACME_MCP_TOKEN");
  await shot(page, "22-register-remote-mcp-server");
  await page.getByRole("button", { name: "Close dialog" }).click();

  await page.goto("/knowledge");
  await expect(page.getByTestId("documents")).toContainText("board-compensation-memo.md");
  await page.getByTestId("documents").scrollIntoViewIfNeeded();
  await shot(page, "25-document-library-with-restricted-and-untrusted", true);

  await page.goto("/observability");
  await expect(page.getByText("Run latency p50")).toBeVisible();
  await shot(page, "07-observability");
  await page.getByRole("button", { name: "Verify chain" }).click();
  await expect(page.getByText(/Chain intact/)).toBeVisible();
  await shot(page, "29-observability-full-with-verified-audit-chain", true);

  await page.goto("/");
  await expect(page.getByText("Runs (24 h)")).toBeVisible();
  await shot(page, "01-command-center");
  await shot(page, "39-command-center-full", true);

  // dark theme
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await shot(page, "30-dark-command-center");
  await page.goto(`/studio?run=${wbr}`);
  await expect(page.locator('[data-testid="graph-node-Finalize"]')).toHaveAttribute("data-state", "done");
  await shot(page, "31-dark-workflow-studio-completed-run");
  await page.goto(`/runs/${wbr}`);
  await expect(page.getByTestId("report")).toContainText("KPI scorecard");
  await shot(page, "40-dark-executive-report");
  await page.getByRole("button", { name: "Switch to light theme" }).click();

  // responsive
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByText("Runs (24 h)")).toBeVisible();
  await shot(page, "32-mobile-command-center");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await shot(page, "33-mobile-navigation");
  await page.goto("/approvals");
  await shot(page, "41-mobile-approvals");
  await page.setViewportSize({ width: 1440, height: 900 });

  // ---------------------------------------------------------------- access control
  await login(page, "viewer");
  await shot(page, "34-viewer-read-only");
  await login(page, "globex");
  await page.goto("/knowledge");
  await expect(page.getByTestId("documents")).toContainText("globex-expansion-plan.md");
  await shot(page, "42-second-workspace-isolated-knowledge");
});
