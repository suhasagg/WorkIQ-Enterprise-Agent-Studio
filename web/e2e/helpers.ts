import { expect, type Page } from "@playwright/test";

export const USERS = { admin: "Avery Chen", approver: "Priya Raman", operator: "Jordan Blake", viewer: "Sam Okafor" } as const;

export async function login(page: Page, role: keyof typeof USERS = "admin") {
  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(`· Meridian Dynamics\\s*${role}`, "i") }).click();
  await expect(page.getByRole("heading", { name: "AI Operations Command Center" })).toBeVisible();
}

export async function startWorkflow(page: Page, workflow: string, opts: { fault?: string; request?: string } = {}) {
  await page.goto("/");
  const launcher = page.getByTestId("launcher");
  await launcher.getByLabel("Workflow", { exact: true }).selectOption(workflow);
  if (opts.request) await launcher.getByLabel(/^Request/).fill(opts.request);
  if (opts.fault) await launcher.getByLabel("Failure simulation (demo)").selectOption(opts.fault);
  await launcher.getByRole("button", { name: "Run workflow" }).click();
  await page.waitForURL(/\/runs\/run_/);
  return page.url().split("/runs/")[1];
}

export const runStatus = (page: Page, status: string) => expect(page.getByTestId("run-view")).toHaveAttribute("data-run-status", status, { timeout: 45_000 });

/** Decide every pending approval of a run in the Approval Center. */
export async function approveAll(page: Page, runId: string) {
  await page.goto(`/approvals?run=${runId}`);
  const list = page.getByTestId("approval-list");
  await expect(list).toBeVisible();
  const items = list.getByRole("listitem");
  for (let left = await items.count(); left > 0; left--) {
    await page.getByRole("button", { name: "Approve", exact: true }).click(); // auto-waits for the detail to load
    await expect(items).toHaveCount(left - 1);
  }
}
