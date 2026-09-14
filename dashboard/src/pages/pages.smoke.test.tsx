/**
 * Smoke-renders every page through the real App (lazy routes, shell, providers)
 * against the in-memory mock client, exactly as VITE_MOCK=1 would.
 */
import { render, screen, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import App from "@/App";
import { SessionProvider } from "@/hooks/useSession";
import { ToastProvider } from "@/hooks/useToast";
import { api, getApiMode, initApi } from "@/lib/api";

let runId = "";

// Lazy route chunks (and recharts) are transformed on first import, which can take
// a few seconds in jsdom; warm them up so per-test timeouts stay tight.
const WAIT = { timeout: 5000 };

beforeAll(async () => {
  vi.stubEnv("VITE_MOCK", "1");
  await Promise.all([initApi(), import("@/pages/Overview"), import("@/pages/Runs"), import("@/pages/RunDetail"), import("@/pages/Bench"), import("@/pages/Settings"), import("@/pages/NotFound")]);
  const { items } = await api.listRuns({ status: "completed", limit: 1 });
  runId = items[0]?.id ?? "";
}, 30_000);

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SessionProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

describe("pages (mock client)", () => {
  it("uses the mock client when VITE_MOCK=1", () => {
    expect(getApiMode()).toBe("mock");
    expect(runId).not.toBe("");
  });

  it("renders Overview with stat tiles and recent runs", async () => {
    renderAt("/");
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" }, WAIT)).toBeInTheDocument();
    expect(screen.getByText("Verified bugs")).toBeInTheDocument();
    expect(await screen.findByText("Demo data", {}, WAIT)).toBeInTheDocument();
    expect(await screen.findAllByRole("link", { name: /acme|northwind|contoso|repos/ }, WAIT)).not.toHaveLength(0);
  });

  it("renders Runs with the status filter and a populated table", async () => {
    renderAt("/runs");
    expect(await screen.findByRole("heading", { level: 1, name: "Runs" }, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Filter by status" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Search runs" })).toBeInTheDocument();
    expect(await screen.findByText(/audits$/, {}, WAIT)).toBeInTheDocument();
  });

  it("renders RunDetail for a deep link like /runs/:id", async () => {
    renderAt(`/runs/${runId}`);
    expect(await screen.findByRole("list", { name: "Pipeline stages" }, WAIT)).toBeInTheDocument();
    expect(await screen.findByText(runId, {}, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/\//);
  });

  it("renders Benchmark with the headline comparison", async () => {
    renderAt("/bench");
    expect(await screen.findByRole("heading", { level: 1, name: "Benchmark" }, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Suite" })).toBeInTheDocument();
    expect(await screen.findByText(/pts/, {}, WAIT)).toBeInTheDocument();
  });

  it("renders Settings with identity and API key controls", async () => {
    renderAt("/settings");
    expect(await screen.findByRole("heading", { level: 1, name: "Settings" }, WAIT)).toBeInTheDocument();
    expect(screen.getByLabelText("API key")).toBeInTheDocument();
    // the name also appears in the header avatar, so scope to the page body
    const main = screen.getByRole("main");
    expect(await within(main).findByText("demo-admin", {}, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Theme" })).toBeInTheDocument();
  });

  it("renders NotFound for unknown paths", async () => {
    renderAt("/does/not/exist");
    expect(await screen.findByRole("heading", { level: 1, name: "Page not found" }, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to overview" })).toHaveAttribute("href", "/");
  });
});
