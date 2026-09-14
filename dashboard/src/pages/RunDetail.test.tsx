/**
 * RunDetail: approve/reject controls only appear while the run is awaiting_review
 * and the user is at least an operator. useRunEvents and the api are mocked so the
 * page renders deterministically.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type * as ApiModule from "@/lib/api";
import type { Role } from "@/lib/types";
import { makeFinding, makeRun } from "@/test/fixtures";

const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };
let role: Role = "admin";

vi.mock("@/hooks/useSession", () => ({
  useSession: () => ({
    mode: "mock",
    me: { name: "tester", role },
    meError: null,
    health: null,
    healthOk: true,
    can: (r: Role) => ROLE_RANK[role] >= ROLE_RANK[r],
    reloadMe: () => undefined,
  }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof ApiModule>();
  return {
    ...actual,
    api: {
      ...actual.api,
      decide: vi.fn(),
      cancelRun: vi.fn(),
      reportUrl: (id: string, fmt: string) => `/api/v1/runs/${id}/report.${fmt}`,
    },
  };
});

const runEvents = {
  run: makeRun(),
  findings: [makeFinding()],
  events: [],
  connection: "live" as const,
  loading: false,
  error: null,
  lastEventId: null,
  refresh: vi.fn(),
  setFinding: vi.fn(),
  setRun: vi.fn(),
};

vi.mock("@/lib/useRunEvents", () => ({
  useRunEvents: () => runEvents,
}));

import { api } from "@/lib/api";
import { ToastProvider } from "@/hooks/useToast";
import RunDetail from "./RunDetail";

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/runs/r-test"]}>
      <ToastProvider>
        <Routes>
          <Route path="/runs/:id" element={<RunDetail />} />
        </Routes>
      </ToastProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  role = "admin";
  runEvents.run = makeRun();
  runEvents.findings = [makeFinding()];
  runEvents.setFinding.mockClear();
  vi.mocked(api.decide).mockReset();
});

describe("RunDetail", () => {
  it("renders header, pipeline and findings for a running run without decision controls", () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("acme/payments-api");
    expect(screen.getByRole("list", { name: "Pipeline stages" })).toBeInTheDocument();
    expect(screen.getByText("Currently in hunt")).toBeInTheDocument();
    expect(screen.getByRole("row", { name: "Open finding f-1" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reject/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cancel run/ })).toBeInTheDocument();
  });

  it("shows the review banner and approve/reject buttons when awaiting_review", async () => {
    const user = userEvent.setup();
    runEvents.run = makeRun({ status: "awaiting_review", current_node: null });
    const updated = makeFinding({ status: "approved" });
    vi.mocked(api.decide).mockResolvedValue(updated);

    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent(/Awaiting review.*1 fix pending/);
    const row = screen.getByRole("row", { name: "Open finding f-1" });
    expect(within(row).getByRole("button", { name: "Approve f-1" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Reject f-1" })).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Approve f-1" }));
    expect(api.decide).toHaveBeenCalledWith("r-test", "f-1", { decision: "approve", note: "" });
    // optimistic update followed by the server response
    expect(runEvents.setFinding).toHaveBeenCalledWith(expect.objectContaining({ id: "f-1", status: "approved" }));
    expect(runEvents.setFinding).toHaveBeenLastCalledWith(updated);
  });

  it("hides decision controls from viewers even when awaiting_review", () => {
    role = "viewer";
    runEvents.run = makeRun({ status: "awaiting_review", current_node: null });
    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("Operator role required to decide.");
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cancel run/ })).not.toBeInTheDocument();
  });

  it("does not offer decisions on completed runs and hides Cancel", () => {
    runEvents.run = makeRun({ status: "completed", current_node: null, progress: 1, finished_at: "2026-09-14T10:00:00.000Z" });
    renderPage();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cancel run/ })).not.toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("opens the finding drawer when a row is clicked", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("row", { name: "Open finding f-1" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("tab", { name: "Patch" })).toBeInTheDocument();
  });
});
