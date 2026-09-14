/**
 * Captures README media (screenshots + a demo video) from the dashboard in demo mode.
 *   npx playwright test -c playwright.capture.config.ts
 * Output: ../docs/media/*.png and a .webm under capture-out/ (converted to GIF by scripts/make-gif).
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 5176);
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /capture\.ts$/,
  timeout: 180_000,
  fullyParallel: false,
  retries: 0,
  reporter: "list",
  outputDir: "capture-out",
  use: {
    baseURL: BASE_URL,
    viewport: { width: 1280, height: 800 },
    colorScheme: "dark",
    video: { mode: "on", size: { width: 1280, height: 800 } },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } }],
  webServer: {
    command: `npx vite --port ${PORT} --strictPort --host 127.0.0.1`,
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 120_000,
    env: { ...process.env, VITE_MOCK: "1" },
  },
});
