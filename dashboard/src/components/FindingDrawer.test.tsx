import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FindingDrawer } from "./FindingDrawer";
import { makeFinding } from "@/test/fixtures";

const noop = () => undefined;

describe("FindingDrawer", () => {
  it("renders nothing when no finding is selected", () => {
    render(<FindingDrawer finding={null} onClose={noop} canDecide={false} onDecide={noop} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows header metadata and the description tab by default", () => {
    const f = makeFinding();
    render(<FindingDrawer finding={f} onClose={noop} canDecide={false} onDecide={noop} />);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("high")).toBeInTheDocument();
    expect(within(dialog).getByText("Off By One")).toBeInTheDocument();
    expect(within(dialog).getByText(f.id)).toBeInTheDocument();
    expect(within(dialog).getByText(/app\/utils\/pagination\.py/)).toBeInTheDocument();
    expect(within(dialog).getByRole("tab", { name: "Description" })).toHaveAttribute("aria-selected", "true");
    expect(within(dialog).getByText(f.description)).toBeInTheDocument();
    expect(within(dialog).getByText(f.hypothesis)).toBeInTheDocument();
    expect(within(dialog).getByText("3 callers")).toBeInTheDocument();
  });

  it("switches to the Test tab and renders the generated test", async () => {
    const user = userEvent.setup();
    const f = makeFinding();
    render(<FindingDrawer finding={f} onClose={noop} canDecide={false} onDecide={noop} />);
    await user.click(screen.getByRole("tab", { name: "Test" }));
    expect(screen.getByRole("tab", { name: "Test" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("tests/test_sentinel_first_n.py")).toBeInTheDocument();
    expect(screen.getByText(/def test_first_n_boundary_is_inclusive/)).toBeInTheDocument();
  });

  it("renders the unified diff on the Patch tab with +/- counts", async () => {
    const user = userEvent.setup();
    render(<FindingDrawer finding={makeFinding()} onClose={noop} canDecide={false} onDecide={noop} />);
    await user.click(screen.getByRole("tab", { name: "Patch" }));
    expect(screen.getByText("+1")).toBeInTheDocument();
    expect(screen.getByText("-1")).toBeInTheDocument();
    // diff rows are whitespace-significant, so bypass RTL's whitespace-collapsing normalizer
    const exact = { normalizer: (s: string) => s };
    expect(screen.getByText("+    return items[:n]", exact)).toBeInTheDocument();
    expect(screen.getByText("-    return items[: n - 1]", exact)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy patch" })).toBeInTheDocument();
  });

  it("shows empty states when test or patch are missing", async () => {
    const user = userEvent.setup();
    render(<FindingDrawer finding={makeFinding({ status: "candidate", test_code: null, test_path: null, patch_diff: null })} onClose={noop} canDecide={false} onDecide={noop} />);
    await user.click(screen.getByRole("tab", { name: "Test" }));
    expect(screen.getByText("No test yet")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Patch" }));
    expect(screen.getByText("No patch yet")).toBeInTheDocument();
  });

  it("offers approve/reject only when the caller can decide and the finding is pending review", async () => {
    const user = userEvent.setup();
    const onDecide = vi.fn();
    const f = makeFinding({ status: "fixed" });

    const { rerender } = render(<FindingDrawer finding={f} onClose={noop} canDecide={false} onDecide={onDecide} />);
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();

    rerender(<FindingDrawer finding={f} onClose={noop} canDecide onDecide={onDecide} />);
    await user.click(screen.getByRole("button", { name: /Approve/ }));
    expect(onDecide).toHaveBeenCalledWith(f, "approve");
    await user.click(screen.getByRole("button", { name: /Reject/ }));
    expect(onDecide).toHaveBeenCalledWith(f, "reject");

    rerender(<FindingDrawer finding={makeFinding({ status: "pr_opened" })} onClose={noop} canDecide onDecide={onDecide} />);
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
  });

  it("closes on Escape and via the close button", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<FindingDrawer finding={makeFinding()} onClose={onClose} canDecide={false} onDecide={noop} />);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Close panel" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
