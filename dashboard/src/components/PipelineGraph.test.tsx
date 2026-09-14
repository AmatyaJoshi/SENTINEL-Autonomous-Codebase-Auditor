import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PipelineGraph } from "./PipelineGraph";
import { NODE_ORDER } from "@/lib/types";
import { makeNodes } from "@/test/fixtures";

describe("PipelineGraph", () => {
  it("renders all 11 pipeline stages in canonical order", () => {
    render(<PipelineGraph nodes={makeNodes()} />);
    const list = screen.getByRole("list", { name: "Pipeline stages" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(NODE_ORDER.length);
    items.forEach((li, i) => expect(li).toHaveAccessibleName(new RegExp(`^${NODE_ORDER[i]}: pending`, "i")));
  });

  it("labels done nodes with their duration", () => {
    render(<PipelineGraph nodes={makeNodes({ ingest: { status: "done", duration_s: 3.2 }, index: { status: "done", duration_s: 125 } })} />);
    expect(screen.getByRole("listitem", { name: "Ingest: done, 3.2s" })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: "Index: done, 2m 05s" })).toBeInTheDocument();
  });

  it("marks the running node and shows the running caption", () => {
    render(<PipelineGraph nodes={makeNodes({ ingest: { status: "done", duration_s: 2 }, index: { status: "running" } })} currentNode="index" />);
    const running = screen.getByRole("listitem", { name: /^Index: running/ });
    expect(within(running).getByText("running")).toBeInTheDocument();
    expect(within(running).getByText("index")).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: /^Analyze: running/ })).not.toBeInTheDocument();
  });

  it("renders skipped and error states", () => {
    render(<PipelineGraph nodes={makeNodes({ triage: { status: "skipped" }, verify: { status: "error", duration_s: 4 } })} />);
    const skipped = screen.getByRole("listitem", { name: "Triage: skipped" });
    expect(within(skipped).getByText("skipped")).toBeInTheDocument();
    const errored = screen.getByRole("listitem", { name: "Verify: error, 4.0s" });
    expect(within(errored).getByText("4.0s")).toBeInTheDocument();
  });
});
