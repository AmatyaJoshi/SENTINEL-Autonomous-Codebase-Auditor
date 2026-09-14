/**
 * Mock server tests. Fake timers are installed before the singleton is created so
 * the seeded live-run simulation only advances when we tell it to.
 */
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import type { ApiClient } from "./api";
import { ApiError } from "./api";
import type { RunEventEnvelope } from "./types";
import { NODE_ORDER } from "./types";

let mock: ApiClient;

beforeAll(async () => {
  vi.useFakeTimers();
  const { createMockClient } = await import("./mock");
  mock = createMockClient();
});

afterAll(() => {
  vi.useRealTimers();
});

/** Resolve a mock call whose internal delay is driven by fake timers. */
async function settle<T>(p: Promise<T>, ms = 1000): Promise<T> {
  // Mark an early rejection as observed so it is not reported as unhandled while
  // timers advance; the caller still receives the original rejection below.
  p.catch(() => undefined);
  await vi.advanceTimersByTimeAsync(ms);
  return p;
}

describe("mock server", () => {
  it("returns the same singleton from createMockClient()", async () => {
    const { createMockClient } = await import("./mock");
    expect(createMockClient()).toBe(mock);
  });

  it("seeds historical runs with a live one already running", async () => {
    const { items, total } = await settle(mock.listRuns({ limit: 50 }));
    expect(total).toBeGreaterThanOrEqual(12);
    expect(items.length).toBe(total);
    expect(items.some((r) => r.status === "completed")).toBe(true);
    expect(items.some((r) => r.status === "awaiting_review")).toBe(true);
    expect(items.some((r) => r.status === "running" || r.status === "created")).toBe(true);
    for (const r of items) expect(r.nodes.map((n) => n.name)).toEqual([...NODE_ORDER]);
  });

  it("filters by status and paginates", async () => {
    const completed = await settle(mock.listRuns({ status: "completed" }));
    expect(completed.items.every((r) => r.status === "completed")).toBe(true);
    const page = await settle(mock.listRuns({ limit: 2, offset: 1 }));
    expect(page.items).toHaveLength(2);
    expect(page.total).toBe(completed.total + (await settle(mock.listRuns({ status: "failed,cancelled,awaiting_review,running,created" }))).total);
  });

  it("rejects unknown runs and findings with 404 ApiError", async () => {
    await expect(settle(mock.getRun("nope"))).rejects.toMatchObject({ status: 404, code: "not_found" });
    await expect(settle(mock.getFinding("nope", "x"))).rejects.toBeInstanceOf(ApiError);
    await expect(settle(mock.cancelRun("nope"))).rejects.toMatchObject({ status: 404 });
  });

  it("simulates a new run: events are sequence-numbered and nodes progress", async () => {
    const run = await settle(mock.createRun({ repo: "https://github.com/acme/ledger-core", sha: null, open_pr: false, review: false, max_usd: 5, max_minutes: 60, max_findings: 10, arm: "full" }));
    expect(run.status).toBe("created");

    const received: RunEventEnvelope[] = [];
    const onOpen = vi.fn();
    const unsub = mock.streamEvents(run.id, null, { onEvent: (e) => received.push(e), onOpen });
    await vi.advanceTimersByTimeAsync(100);
    expect(onOpen).toHaveBeenCalledTimes(1);

    // ingest (3s) + index (5s) comfortably finish inside 15s of simulated time
    await vi.advanceTimersByTimeAsync(15_000);
    unsub();

    expect(received.length).toBeGreaterThan(5);
    received.forEach((env, i) => {
      expect(env.seq).toBe(i + 1);
      expect(env.id).toBe(String(env.seq));
    });
    const types = received.map((e) => e.event.type);
    expect(types).toContain("run.status");
    expect(types).toContain("node.start");
    expect(types).toContain("node.end");
    expect(types).toContain("sandbox.exec");

    const latest = await settle(mock.getRun(run.id));
    expect(latest.status).toBe("running");
    expect(latest.nodes[0]?.status).toBe("done");
    expect(latest.nodes.filter((n) => n.status === "done").length).toBeGreaterThanOrEqual(2);
    expect(latest.progress).toBeGreaterThan(0);
  });

  it("replays only events after Last-Event-ID and keeps streaming live ones", async () => {
    const run = await settle(mock.createRun({ repo: "https://github.com/northwind/inventory-svc", sha: null, open_pr: false, review: false, max_usd: 5, max_minutes: 60, max_findings: 10, arm: "analyzers" }));
    await vi.advanceTimersByTimeAsync(6_000); // ingest + part of index have emitted

    const all: RunEventEnvelope[] = [];
    const unsubAll = mock.streamEvents(run.id, null, { onEvent: (e) => all.push(e) });
    await vi.advanceTimersByTimeAsync(100);
    unsubAll();
    expect(all.length).toBeGreaterThan(3);
    const cursor = all[2] as RunEventEnvelope;

    const tail: RunEventEnvelope[] = [];
    const unsubTail = mock.streamEvents(run.id, cursor.id, { onEvent: (e) => tail.push(e) });
    await vi.advanceTimersByTimeAsync(100);
    const replayed = tail.length;
    expect(replayed).toBe(all.length - 3);
    expect(tail[0]?.seq).toBe(cursor.seq + 1);
    tail.forEach((e) => expect(e.seq).toBeGreaterThan(cursor.seq));

    // live events continue to arrive after the replay
    await vi.advanceTimersByTimeAsync(5_000);
    expect(tail.length).toBeGreaterThan(replayed);
    unsubTail();
    const afterUnsub = tail.length;
    await vi.advanceTimersByTimeAsync(3_000);
    expect(tail.length).toBe(afterUnsub);
  });

  it("reports a fatal error when streaming an unknown run", async () => {
    const onError = vi.fn();
    mock.streamEvents("missing", null, { onEvent: () => undefined, onError });
    await vi.advanceTimersByTimeAsync(10);
    expect(onError).toHaveBeenCalledWith(expect.any(ApiError), true);
  });

  it("only accepts decisions while awaiting review", async () => {
    const { items } = await settle(mock.listRuns({ status: "completed", limit: 1 }));
    const done = items[0];
    expect(done).toBeDefined();
    const findings = await settle(mock.listFindings(done!.id));
    const target = findings.items[0];
    expect(target).toBeDefined();
    await expect(settle(mock.decide(done!.id, target!.id, { decision: "approve", note: "" }))).rejects.toMatchObject({ status: 409, code: "conflict" });

    const review = (await settle(mock.listRuns({ status: "awaiting_review", limit: 1 }))).items[0];
    expect(review).toBeDefined();
    const pending = (await settle(mock.listFindings(review!.id, { status: "fixed" }))).items[0];
    expect(pending).toBeDefined();
    const decided = await settle(mock.decide(review!.id, pending!.id, { decision: "reject", note: "false positive" }));
    expect(decided.status).toBe("rejected");
  });

  it("cancels an active run and emits run.end", async () => {
    const run = await settle(mock.createRun({ repo: "https://github.com/contoso/scheduler", sha: null, open_pr: false, review: false, max_usd: 5, max_minutes: 60, max_findings: 10, arm: "full" }));
    const events: RunEventEnvelope[] = [];
    mock.streamEvents(run.id, null, { onEvent: (e) => events.push(e) });
    await vi.advanceTimersByTimeAsync(2_000);
    const cancelled = await settle(mock.cancelRun(run.id));
    expect(cancelled.status).toBe("cancelled");
    expect(events.at(-1)?.event.type).toBe("run.end");
    await expect(settle(mock.cancelRun(run.id))).rejects.toMatchObject({ status: 409 });
  });

  it("returns a data: URL for reports", async () => {
    const { items } = await settle(mock.listRuns({ limit: 1 }));
    const url = mock.reportUrl(items[0]!.id, "json");
    expect(url.startsWith("data:application/json")).toBe(true);
    expect(mock.reportUrl(items[0]!.id, "html").startsWith("data:text/html")).toBe(true);
  });
});
