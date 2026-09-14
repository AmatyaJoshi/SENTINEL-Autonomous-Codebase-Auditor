/**
 * useRunEvents: SSE subscription, reduction of events into run state, and the
 * polling fallback after a fatal transport error. The api module is mocked.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EventStreamHandlers } from "./api";
import type { RunEventEnvelope } from "./types";
import { makeFinding, makeRun } from "@/test/fixtures";

vi.mock("./api", () => ({
  api: {
    getRun: vi.fn(),
    listFindings: vi.fn(),
    streamEvents: vi.fn(),
  },
}));

import { api } from "./api";
import { useRunEvents } from "./useRunEvents";

const getRun = vi.mocked(api.getRun);
const listFindings = vi.mocked(api.listFindings);
const streamEvents = vi.mocked(api.streamEvents);

let handlers: EventStreamHandlers | null = null;
const unsubscribe = vi.fn();

function envelope(seq: number, event: RunEventEnvelope["event"]): RunEventEnvelope {
  return { id: String(seq), seq, receivedAt: Date.now(), event };
}

beforeEach(() => {
  vi.useFakeTimers();
  handlers = null;
  unsubscribe.mockClear();
  getRun.mockReset();
  listFindings.mockReset();
  streamEvents.mockReset();
  streamEvents.mockImplementation((_id, _last, h) => {
    handlers = h;
    return unsubscribe;
  });
  listFindings.mockResolvedValue({ items: [makeFinding()] });
});

afterEach(() => {
  vi.useRealTimers();
});

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

describe("useRunEvents", () => {
  it("loads the run and findings, then opens the SSE stream", async () => {
    getRun.mockResolvedValue(makeRun());
    const { result } = renderHook(() => useRunEvents("r-test"));
    expect(result.current.loading).toBe(true);
    await flush();
    expect(result.current.loading).toBe(false);
    expect(result.current.run?.id).toBe("r-test");
    expect(result.current.findings).toHaveLength(1);
    expect(result.current.connection).toBe("connecting");
    expect(streamEvents).toHaveBeenCalledWith("r-test", null, expect.any(Object));

    act(() => handlers?.onOpen?.());
    expect(result.current.connection).toBe("live");
  });

  it("does not stream when the run is already terminal", async () => {
    getRun.mockResolvedValue(makeRun({ status: "completed", finished_at: "2026-09-14T10:00:00.000Z" }));
    const { result } = renderHook(() => useRunEvents("r-test"));
    await flush();
    expect(result.current.connection).toBe("closed");
    expect(streamEvents).not.toHaveBeenCalled();
  });

  it("reduces node.start / node.end / llm.call / run.end into run state", async () => {
    getRun.mockResolvedValue(makeRun({ status: "created", current_node: null, cost_usd: 0, nodes: makeRun().nodes.map((n) => ({ ...n, status: "pending" })) }));
    const { result } = renderHook(() => useRunEvents("r-test"));
    await flush();

    act(() => {
      handlers?.onEvent(envelope(1, { type: "node.start", data: { node: "ingest", at: "2026-09-14T09:00:01Z" } }));
      handlers?.onEvent(envelope(2, { type: "llm.call", data: { node: "ingest", model: "m", tokens_in: 1, tokens_out: 1, cost_usd: 0.25, duration_s: 1 } }));
    });
    expect(result.current.run?.status).toBe("running");
    expect(result.current.run?.current_node).toBe("ingest");
    expect(result.current.run?.nodes[0]?.status).toBe("running");
    expect(result.current.run?.cost_usd).toBe(0.25);
    expect(result.current.lastEventId).toBe("2");
    expect(result.current.events).toHaveLength(2);

    act(() => {
      handlers?.onEvent(envelope(3, { type: "node.end", data: { node: "ingest", at: "2026-09-14T09:00:04Z", duration_s: 3, ok: true } }));
      handlers?.onEvent(envelope(4, { type: "finding.new", data: makeFinding({ id: "f-9", status: "candidate" }) }));
    });
    expect(result.current.run?.nodes[0]).toMatchObject({ status: "done", duration_s: 3 });
    expect(result.current.findings.map((f) => f.id)).toEqual(["f-9", "f-1"]);

    const finalRun = makeRun({ status: "completed", progress: 1 });
    act(() => handlers?.onEvent(envelope(5, { type: "run.end", data: finalRun })));
    expect(result.current.run?.status).toBe("completed");
    expect(result.current.connection).toBe("closed");
    expect(unsubscribe).toHaveBeenCalled();
  });

  it("falls back to polling every 5s after a fatal stream error and stops when terminal", async () => {
    getRun.mockResolvedValue(makeRun());
    const { result } = renderHook(() => useRunEvents("r-test"));
    await flush();
    expect(getRun).toHaveBeenCalledTimes(1);

    act(() => handlers?.onError?.(new Error("transient"), false));
    expect(result.current.connection).toBe("connecting");

    act(() => handlers?.onError?.(new Error("gave up"), true));
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    expect(result.current.connection).toBe("polling");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(getRun).toHaveBeenCalledTimes(2);
    expect(result.current.connection).toBe("polling");

    getRun.mockResolvedValue(makeRun({ status: "completed", progress: 1 }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(getRun).toHaveBeenCalledTimes(3);
    await flush();
    expect(result.current.connection).toBe("closed");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(getRun).toHaveBeenCalledTimes(3);
  });

  it("surfaces load errors and cleans up on unmount", async () => {
    getRun.mockRejectedValue(new Error("Run r-test not found"));
    const { result, unmount } = renderHook(() => useRunEvents("r-test"));
    await flush();
    expect(result.current.error).toBe("Run r-test not found");
    expect(result.current.run).toBeNull();
    expect(streamEvents).not.toHaveBeenCalled();

    getRun.mockResolvedValue(makeRun());
    const { unmount: unmount2 } = renderHook(() => useRunEvents("r-test"));
    await flush();
    unmount2();
    expect(unsubscribe).toHaveBeenCalled();
    unmount();
  });
});
