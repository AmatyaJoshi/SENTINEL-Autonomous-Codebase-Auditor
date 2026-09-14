/**
 * Smoke e2e: every page renders under the mock client (VITE_MOCK=1) and the
 * seeded live run's pipeline graph visibly progresses.
 */
import { expect, test, type Page } from "@playwright/test";

async function expectShell(page: Page) {
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  await expect(page.getByText("Demo data")).toBeVisible();
}

test.describe("pages render", () => {
  test("Overview", async ({ page }) => {
    await page.goto("/");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
    await expect(main.getByText("Verified bugs").first()).toBeVisible();
    await expect(main.getByRole("link", { name: /acme\/|northwind\/|contoso\/|repos\// }).first()).toBeVisible();
    await expectShell(page);
  });

  test("Runs", async ({ page }) => {
    await page.goto("/runs");
    await expect(page.getByRole("heading", { level: 1, name: "Runs" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Filter by status" })).toBeVisible();
    await expect(page.getByRole("row").nth(1)).toBeVisible();
    await expectShell(page);
  });

  test("RunDetail via deep link (BrowserRouter)", async ({ page }) => {
    await page.goto("/runs?status=completed");
    const first = page.getByRole("row").nth(1).getByRole("link").first();
    await expect(first).toBeVisible();
    const href = await first.getAttribute("href");
    expect(href).toMatch(/^\/runs\/[a-z0-9]+$/);
    // hard navigation to the deep link: the dev server (and FastAPI in prod) must
    // serve index.html for non-/api paths.
    await page.goto(href as string);
    await expect(page.getByRole("list", { name: "Pipeline stages" })).toBeVisible();
    await expect(page.getByRole("listitem", { name: /^Ingest: done/ })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("/");
    expect(new URL(page.url()).pathname).toBe(href);
  });

  test("Benchmark", async ({ page }) => {
    await page.goto("/bench");
    await expect(page.getByRole("heading", { level: 1, name: "Benchmark" })).toBeVisible();
    await expect(page.getByText(/pts/)).toBeVisible();
    await expect(page.getByRole("group", { name: "Suite" })).toBeVisible();
    await expectShell(page);
  });

  test("Settings", async ({ page }) => {
    await page.goto("/settings");
    const main = page.getByRole("main");
    await expect(main.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
    await expect(main.getByLabel("API key")).toBeVisible();
    // the user name also appears in the header avatar, so scope to the page body
    await expect(main.getByText("demo-admin")).toBeVisible();
    await expect(main.getByRole("radiogroup", { name: "Theme" })).toBeVisible();
  });

  test("unknown route shows 404", async ({ page }) => {
    await page.goto("/definitely/not/here");
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
    await page.getByRole("link", { name: "Back to overview" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/");
  });
});

test("live run pipeline graph progresses", async ({ page }) => {
  // The mock server seeds one live run per page load, started "now", so it sorts
  // first in the unfiltered list. (It is still `created` for the first ~0.8s, so
  // the Running filter would hide it on first render.)
  await page.goto("/runs");
  const firstRow = page.getByRole("row").nth(1);
  await expect(firstRow).toBeVisible();
  await expect(firstRow.getByText(/running|created/)).toBeVisible();
  await firstRow.getByRole("link").first().click();

  const stages = page.getByRole("list", { name: "Pipeline stages" });
  await expect(stages).toBeVisible();
  await expect(page.getByText("Live", { exact: true }).or(page.getByText(/events$/))).toBeVisible();

  const doneCount = async () => stages.getByRole("listitem", { name: /: done/ }).count();
  const runningCount = async () => stages.getByRole("listitem", { name: /: running/ }).count();

  // a node is running (or the run already finished a couple of stages)
  await expect.poll(async () => (await runningCount()) + (await doneCount()), { timeout: 15_000 }).toBeGreaterThan(0);
  const before = await doneCount();

  // the seeded live run walks ingest (3s) -> index (5s) -> analyze (7s): within
  // 25s at least one more stage must complete.
  await expect.poll(doneCount, { timeout: 25_000, intervals: [500, 1000] }).toBeGreaterThan(before);

  // progress percentage in the card header is a number and the feed is populated
  await expect(page.getByText(/^\d{1,3}%$/).first()).toBeVisible();
  await expect(page.getByText(/^\d+ events$/)).toBeVisible();
});
