import { expect, test } from "@playwright/test";
import { approveAll, login, runStatus, startWorkflow } from "./helpers";

test.beforeEach(({ page }) => {
  page.on("pageerror", (e) => console.log("PAGEERROR", (e.stack || String(e)).slice(0, 400)));
});

test("navigation reaches all eight screens and the theme toggles", async ({ page }) => {
  await login(page);
  const screens: [string, string][] = [
    ["Workflow Studio", "LangGraph Workflow Studio"], ["Agent Chat", "Multi-Agent Chat Workspace"], ["Approvals", "Human Approval Center"],
    ["MCP Integrations", "MCP Integration Hub"], ["Knowledge", "Enterprise Knowledge Studio"], ["Observability", "AI Observability Dashboard"],
    ["Evaluations", "Evaluation Studio"], ["Command Center", "AI Operations Command Center"],
  ];
  for (const [link, heading] of screens) {
    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: new RegExp(link) }).click();
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeAttached();
  }
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
});

test("weekly business review: live execution, approval pause, resume and executive report", async ({ page }) => {
  await login(page);
  const runId = await startWorkflow(page, "weekly_business_review");
  await runStatus(page, "waiting_approval");
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await expect(page.getByTestId("timeline")).toContainText("Plan created");
  await expect(page.getByTestId("timeline")).toContainText("waiting for human approval");
  await page.getByRole("tab", { name: "Graph" }).click();
  await expect(page.locator('[data-testid="graph-node-Human approval gate"]')).toHaveAttribute("data-state", "waiting");

  // reject one action with a comment, approve the rest
  await page.goto(`/approvals?run=${runId}`);
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Add a comment explaining the decision.")).toBeVisible();
  await page.getByLabel(/^Comment/).fill("Not needed this week");
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Rejected", { exact: true }).first()).toBeVisible();
  await approveAll(page, runId);

  await page.goto(`/runs/${runId}`);
  await runStatus(page, "completed");
  const report = page.getByTestId("report");
  await expect(report).toContainText("KPI scorecard");
  await expect(report).toContainText("Sources");
  await expect(report.getByText("Executed").first()).toBeVisible();
  await expect(report.getByText("Rejected").first()).toBeVisible();
  await report.getByRole("button", { name: "Show source 1" }).first().click();
  await expect(page.locator("#cite-1")).toHaveClass(/border-accent/);
  await page.getByRole("tab", { name: "State & checkpoints" }).click();
  await expect(page.getByText(/PostgreSQL checkpoints \(\d+\)/)).toBeVisible();
});

test("failure simulation shows retry and replanning, and the run still completes", async ({ page }) => {
  await login(page, "operator");
  await startWorkflow(page, "knowledge_research", { fault: "research.retrieve" });
  await runStatus(page, "completed");
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await expect(page.getByTestId("timeline")).toContainText("Retrying step");
  await expect(page.getByTestId("timeline")).toContainText("Replanned");
});

test("chat answers with citations and streams agent activity", async ({ page }) => {
  await login(page, "operator");
  await page.goto("/chat");
  await page.getByLabel("Message", { exact: true }).fill("What are our major operational risks and who owns them?");
  await page.getByRole("button", { name: "Send message" }).click();
  const report = page.getByTestId("messages").getByTestId("report");
  await expect(report).toBeVisible({ timeout: 45_000 });
  await expect(report).toContainText("Sources");
  await expect(page.getByRole("link", { name: "View execution trace" })).toBeVisible();
});

test("knowledge: upload, index, retrieve with highlights, delete", async ({ page }) => {
  await login(page, "operator");
  await page.goto("/knowledge");
  const name = `axolotl-${Date.now()}.md`;
  await page.getByLabel(/^Document/).setInputFiles({ name, mimeType: "text/markdown", buffer: Buffer.from(`# Axolotl Runbook ${Date.now()}\n\n## Restart\n\nTo restart the axolotl ledger service, drain the heron queue and redeploy.\n`) });
  await page.getByRole("button", { name: "Upload and index" }).click();
  const row = page.getByTestId("documents").getByRole("row").filter({ hasText: name });
  await expect(row).toContainText("Indexed", { timeout: 30_000 });
  await page.getByLabel("Query").fill("restart the axolotl ledger service");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  const first = page.getByTestId("search-results").getByRole("listitem").first();
  await expect(first).toContainText("Axolotl Runbook");
  await expect(first.locator("mark").first()).toBeVisible();
  await row.getByRole("button", { name: /^Delete/ }).click();
  await expect(row).toHaveCount(0);
});

test("MCP hub: tool console runs reads and holds writes; restricted doc hidden from operator", async ({ page }) => {
  await login(page, "operator");
  await page.goto("/integrations");
  await page.getByRole("button", { name: /Business Metrics/ }).click();
  await expect(page.getByTestId("server-detail")).toContainText("Simulated");
  await page.getByRole("row").filter({ hasText: "get_weekly_kpis" }).getByRole("button", { name: "Try" }).click();
  await page.getByRole("button", { name: "Invoke through gateway" }).click();
  await expect(page.getByTestId("tool-result")).toContainText("OK");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: /Work Tracker/ }).click();
  await page.getByRole("row").filter({ hasText: "create_issue" }).getByRole("button", { name: "Try" }).click();
  await page.getByLabel(/^Arguments/).fill('{"title": "console test", "description": "should be held"}');
  await page.getByRole("button", { name: "Invoke through gateway" }).click();
  await expect(page.getByTestId("tool-result")).toContainText("Approval required");
  await page.goto("/knowledge");
  await expect(page.getByTestId("documents")).toBeVisible();
  await expect(page.getByTestId("documents")).not.toContainText("board-compensation-memo.md");
});

test("evaluation run executes and passes its quality gate", async ({ page }) => {
  await login(page);
  await page.goto("/evals");
  await page.getByTestId("dataset-safety_guardrails").getByRole("button", { name: "Run evaluation" }).click();
  await expect(page.getByTestId("eval-run")).toContainText("Quality gate passed", { timeout: 60_000 });
  await expect(page.getByTestId("eval-run")).toContainText("8 of 8 cases passed");
});

test("viewer is read-only and API errors surface in the UI", async ({ page }) => {
  await login(page, "viewer");
  await expect(page.getByTestId("launcher").getByRole("button", { name: "Run workflow" })).toBeDisabled();
  await page.goto("/runs/run_does_not_exist");
  await expect(page.getByRole("alert").filter({ hasText: /not found/i })).toBeVisible();
  await page.goto("/approvals");
  await expect(page.getByText("Your role cannot decide approvals")).toBeVisible();
});
