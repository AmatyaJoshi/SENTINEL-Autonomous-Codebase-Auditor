/**
 * Not a test: drives the demo-mode dashboard to produce README media.
 * Run with `npx playwright test -c playwright.capture.config.ts`.
 */
import { test, expect } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "media");

test("record demo tour", async ({ page }) => {
  mkdirSync(OUT, { recursive: true });
  const settle = (ms: number) => page.waitForTimeout(ms);

  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  await settle(2200);
  await page.screenshot({ path: `${OUT}/overview.png` });

  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Runs" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Runs" })).toBeVisible();
  await settle(1500);
  await page.screenshot({ path: `${OUT}/runs.png` });

  // the seeded live run: pipeline stages animate as the mock server advances it
  const running = page.getByRole("row").filter({ hasText: /running/i }).first();
  const target = (await running.count()) ? running : page.getByRole("row").nth(1);
  await target.getByRole("link").first().click();
  await expect(page.getByRole("list", { name: "Pipeline stages" })).toBeVisible();
  await settle(6000);
  await page.screenshot({ path: `${OUT}/run-detail.png` });

  const finding = page.getByRole("button", { name: /^Open finding / }).first();
  if (await finding.count()) {
    await finding.click();
    await settle(2500);
    await page.screenshot({ path: `${OUT}/finding.png` });
    await page.keyboard.press("Escape");
    await settle(500);
  }

  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Benchmark" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Benchmark" })).toBeVisible();
  await settle(2500);
  await page.screenshot({ path: `${OUT}/bench.png` });
});
