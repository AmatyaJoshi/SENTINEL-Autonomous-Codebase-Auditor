/**
 * useRunEvents — subscribes to a run's SSE stream, keeps a reduced view of the
 * run (nodes/status/progress), its findings, and a capped activity feed.
 *
 * Reconnects carry Last-Event-ID; when the stream fails fatally we fall back to
 * polling GET /runs/:id and /findings every 5 s.
 */
import { useCallback, useEffect, useReducer, useRef } from "react";
import { api } from "./api";
import type { Finding, Run, RunEventEnvelope, RunStatus } from "./types";
import { NODE_ORDER } from "./types";

export type Connection = "idle" | "connecting" | "live" | "polling" | "closed" | "error";

export interface RunEventsState {
  run: Run | null;
  findings: Finding[];
  events: RunEventEnvelope[];
  connection: Connection;
  loading: boolean;
  error: string | null;
  lastEventId: string | null;
}

type Action =
  | { type: "loaded"; run: Run; findings: Finding[] }
  | { type: "run"; run: Run }
  | { type: "findings"; findings: Finding[] }
  | { type: "finding"; finding: Finding }
  | { type: "event"; env: RunEventEnvelope }
  | { type: "connection"; connection: Connection }
  | { type: "error"; error: string };

const TERMINAL: ReadonlySet<RunStatus> = new Set<RunStatus>(["completed", "failed", "cancelled"]);
const MAX_EVENTS = 500;

function upsertFinding(list: Finding[], f: Finding): Finding[] {
  const idx = list.findIndex((x) => x.id === f.id);
  if (idx === -1) return [f, ...list];
  const next = list.slice();
  next[idx] = f;
  return next;
}

function applyEvent(state: RunEventsState, env: RunEventEnvelope): RunEventsState {
  const { event } = env;
  let run = state.run;
  let findings = state.findings;
  switch (event.type) {
    case "run.status":
      if (run) run = { ...run, status: event.data.status, current_node: event.data.current_node, progress: event.data.progress };
      break;
    case "node.start":
      if (run) {
        run = {
          ...run,
          current_node: event.data.node,
          status: run.status === "created" ? "running" : run.status,
          nodes: run.nodes.map((n) => (n.name === event.data.node ? { ...n, status: "running", started_at: event.data.at } : n)),
        };
      }
      break;
    case "node.end":
      if (run) {
        run = {
          ...run,
          nodes: run.nodes.map((n) =>
            n.name === event.data.node
              ? { ...n, status: event.data.ok ? "done" : "error", finished_at: event.data.at, duration_s: event.data.duration_s }
              : n,
          ),
        };
      }
      break;
    case "finding.new":
    case "finding.update":
      findings = upsertFinding(findings, event.data);
      break;
    case "llm.call":
      if (run) run = { ...run, cost_usd: Math.round((run.cost_usd + event.data.cost_usd) * 1000) / 1000 };
      break;
    case "run.end":
      run = event.data;
      break;
    default:
      break;
  }
  const events = state.events.length >= MAX_EVENTS ? [...state.events.slice(-MAX_EVENTS + 1), env] : [...state.events, env];
  return { ...state, run, findings, events, lastEventId: env.id };
}

function reducer(state: RunEventsState, action: Action): RunEventsState {
  switch (action.type) {
    case "loaded":
      return { ...state, run: normalizeRun(action.run), findings: action.findings, loading: false, error: null };
    case "run":
      return { ...state, run: normalizeRun(action.run) };
    case "findings":
      return { ...state, findings: action.findings };
    case "finding":
      return { ...state, findings: upsertFinding(state.findings, action.finding) };
    case "event":
      return applyEvent(state, action.env);
    case "connection":
      return { ...state, connection: action.connection };
    case "error":
      return { ...state, error: action.error, loading: false };
    default:
      return state;
  }
}

/** Guarantee all 11 nodes exist in canonical order (servers may omit pending ones). */
function normalizeRun(run: Run): Run {
  const byName = new Map(run.nodes.map((n) => [n.name, n]));
  return {
    ...run,
    nodes: NODE_ORDER.map((name) => byName.get(name) ?? { name, status: "pending", started_at: null, finished_at: null, duration_s: null }),
  };
}

export function useRunEvents(runId: string | undefined) {
  const [state, dispatch] = useReducer(reducer, {
    run: null,
    findings: [],
    events: [],
    connection: "idle",
    loading: true,
    error: null,
    lastEventId: null,
  });
  const lastIdRef = useRef<string | null>(null);
  const unsubRef = useRef<(() => void) | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  const refresh = useCallback(async () => {
    if (!runId) return;
    try {
      const [run, findings] = await Promise.all([api.getRun(runId), api.listFindings(runId)]);
      dispatch({ type: "loaded", run, findings: findings.items });
      return run;
    } catch (e) {
      dispatch({ type: "error", error: e instanceof Error ? e.message : String(e) });
      return null;
    }
  }, [runId]);

  const stopStream = useCallback(() => {
    unsubRef.current?.();
    unsubRef.current = null;
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    dispatch({ type: "connection", connection: "polling" });
    pollRef.current = setInterval(async () => {
      const run = await refresh();
      if (run && TERMINAL.has(run.status)) {
        stopPolling();
        dispatch({ type: "connection", connection: "closed" });
      }
    }, 5000);
  }, [refresh, stopPolling]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    lastIdRef.current = null;

    (async () => {
      const run = await refresh();
      if (cancelled || !run) return;
      if (TERMINAL.has(run.status)) {
        dispatch({ type: "connection", connection: "closed" });
        return;
      }
      dispatch({ type: "connection", connection: "connecting" });
      unsubRef.current = api.streamEvents(runId, lastIdRef.current, {
        onOpen: () => dispatch({ type: "connection", connection: "live" }),
        onEvent: (env) => {
          lastIdRef.current = env.id;
          dispatch({ type: "event", env });
          if (env.event.type === "run.end") {
            stopStream();
            dispatch({ type: "connection", connection: "closed" });
          }
        },
        onError: (_err, fatal) => {
          if (fatal) {
            stopStream();
            startPolling();
          } else {
            dispatch({ type: "connection", connection: "connecting" });
          }
        },
      });
    })();

    return () => {
      cancelled = true;
      stopStream();
      stopPolling();
    };
  }, [runId, refresh, startPolling, stopPolling, stopStream]);

  /** Optimistic local update used by approve/reject. */
  const setFinding = useCallback((f: Finding) => dispatch({ type: "finding", finding: f }), []);
  const setRun = useCallback((r: Run) => dispatch({ type: "run", run: r }), []);

  return { ...state, refresh, setFinding, setRun };
}
