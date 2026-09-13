/**
 * Sentinel API client. Mirrors docs/API.md exactly.
 *
 * The exported `api` facade delegates to either the live HTTP client or the
 * in-memory mock server (see ./mock.ts). Mode is decided once at startup by
 * `initApi()`: VITE_MOCK=1 forces mock; otherwise we probe /health and fall back
 * to mock if the backend is unreachable.
 */
import type {
  ApiErrorBody,
  BenchListResponse,
  CreateRunRequest,
  DecisionRequest,
  Finding,
  FindingFilters,
  FindingListResponse,
  Health,
  Me,
  Run,
  RunEvent,
  RunEventEnvelope,
  RunEventType,
  RunListResponse,
  SettingsSnapshot,
  Stats,
} from "./types";
import { RUN_EVENT_TYPES } from "./types";

export const API_KEY_STORAGE = "sentinel.apiKey";
export const BASE = "/api/v1";

/* ------------------------------------------------------------------ */
/* Errors                                                              */
/* ------------------------------------------------------------------ */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfter: number | null;
  constructor(status: number, code: string, message: string, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

export function errorMessage(e: unknown): string {
  if (isApiError(e)) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

/* ------------------------------------------------------------------ */
/* API key                                                             */
/* ------------------------------------------------------------------ */

export function getApiKey(): string {
  try {
    return localStorage.getItem(API_KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

export function setApiKey(key: string): void {
  try {
    if (key) localStorage.setItem(API_KEY_STORAGE, key);
    else localStorage.removeItem(API_KEY_STORAGE);
  } catch {
    /* ignore */
  }
  notifyKeyListeners();
}

const keyListeners = new Set<() => void>();
function notifyKeyListeners() {
  keyListeners.forEach((l) => l());
}
export function onApiKeyChange(l: () => void): () => void {
  keyListeners.add(l);
  return () => keyListeners.delete(l);
}

/* ------------------------------------------------------------------ */
/* Client interface                                                    */
/* ------------------------------------------------------------------ */

export interface RunListParams {
  limit?: number;
  offset?: number;
  status?: string;
}

export interface BenchParams {
  suite?: "small" | "full";
  limit?: number;
}

export type ReportFormat = "html" | "json" | "md";

export interface EventStreamHandlers {
  onEvent: (envelope: RunEventEnvelope) => void;
  onOpen?: () => void;
  /** Called on each transport error; `fatal` means the client gave up and the caller should poll. */
  onError?: (err: unknown, fatal: boolean) => void;
}

export type Unsubscribe = () => void;

export interface ApiClient {
  health(): Promise<Health>;
  stats(): Promise<Stats>;
  listRuns(params?: RunListParams): Promise<RunListResponse>;
  getRun(id: string): Promise<Run>;
  createRun(body: CreateRunRequest): Promise<Run>;
  cancelRun(id: string): Promise<Run>;
  deleteRun(id: string): Promise<void>;
  listFindings(runId: string, filters?: FindingFilters): Promise<FindingListResponse>;
  getFinding(runId: string, fid: string): Promise<Finding>;
  decide(runId: string, fid: string, body: DecisionRequest): Promise<Finding>;
  benchResults(params?: BenchParams): Promise<BenchListResponse>;
  settings(): Promise<SettingsSnapshot>;
  me(): Promise<Me>;
  reportUrl(runId: string, fmt: ReportFormat): string;
  streamEvents(runId: string, lastEventId: string | null, handlers: EventStreamHandlers): Unsubscribe;
}

/* ------------------------------------------------------------------ */
/* HTTP implementation                                                 */
/* ------------------------------------------------------------------ */

function qs(params: object): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params) as Array<[string, unknown]>) {
    if (v === undefined || v === null || v === "") continue;
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

function authHeaders(extra: Record<string, string> = {}): HeadersInit {
  const h: Record<string, string> = { Accept: "application/json", ...extra };
  const key = getApiKey();
  if (key) h["X-API-Key"] = key;
  return h;
}

async function parseError(res: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as Partial<ApiErrorBody>;
    if (body && typeof body === "object" && body.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
    }
  } catch {
    /* non-JSON error body */
  }
  const ra = res.headers.get("Retry-After");
  return new ApiError(res.status, code, message, ra ? Number(ra) : null);
}

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 20_000): Promise<T> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      ...init,
      headers: authHeaders(
        init.body ? { "Content-Type": "application/json", ...(init.headers as Record<string, string>) } : (init.headers as Record<string, string>),
      ),
      signal: ctrl.signal,
    });
    if (!res.ok) throw await parseError(res);
    if (res.status === 204) return undefined as T;
    // JSON boundary: the server contract is trusted here.
    return (await res.json()) as T;
  } finally {
    clearTimeout(t);
  }
}

/** Parses a text/event-stream chunk stream into events. Used when EventSource cannot send headers. */
async function consumeSseStream(
  body: ReadableStream<Uint8Array>,
  onMessage: (id: string | null, type: string, data: string) => void,
  signal: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let curId: string | null = null;
  let curType = "message";
  let curData: string[] = [];
  const flush = () => {
    if (curData.length) onMessage(curId, curType, curData.join("\n"));
    curType = "message";
    curData = [];
  };
  while (!signal.aborted) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx).replace(/\r$/, "");
      buf = buf.slice(idx + 1);
      if (line === "") {
        flush();
        continue;
      }
      if (line.startsWith(":")) continue; // heartbeat comment
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") curType = value;
      else if (field === "data") curData.push(value);
      else if (field === "id") curId = value;
    }
  }
}

function isRunEventType(t: string): t is RunEventType {
  return (RUN_EVENT_TYPES as readonly string[]).includes(t);
}

let seqCounter = 0;
export function toEnvelope(id: string | null, type: string, raw: string): RunEventEnvelope | null {
  if (!isRunEventType(type)) return null;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  // JSON boundary: cast trusted server payloads to the typed union.
  const event = { type, data } as RunEvent;
  const seq = id !== null && /^\d+$/.test(id) ? Number(id) : ++seqCounter;
  return { id: id ?? String(seq), seq, receivedAt: Date.now(), event };
}

class HttpClient implements ApiClient {
  health() {
    return request<Health>("/health", {}, 5000);
  }
  stats() {
    return request<Stats>(`${BASE}/stats`);
  }
  listRuns(params: RunListParams = {}) {
    return request<RunListResponse>(`${BASE}/runs${qs(params)}`);
  }
  getRun(id: string) {
    return request<Run>(`${BASE}/runs/${encodeURIComponent(id)}`);
  }
  createRun(body: CreateRunRequest) {
    return request<Run>(`${BASE}/runs`, { method: "POST", body: JSON.stringify(body) });
  }
  cancelRun(id: string) {
    return request<Run>(`${BASE}/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
  }
  deleteRun(id: string) {
    return request<void>(`${BASE}/runs/${encodeURIComponent(id)}`, { method: "DELETE" });
  }
  listFindings(runId: string, filters: FindingFilters = {}) {
    return request<FindingListResponse>(`${BASE}/runs/${encodeURIComponent(runId)}/findings${qs(filters)}`);
  }
  getFinding(runId: string, fid: string) {
    return request<Finding>(`${BASE}/runs/${encodeURIComponent(runId)}/findings/${encodeURIComponent(fid)}`);
  }
  decide(runId: string, fid: string, body: DecisionRequest) {
    return request<Finding>(`${BASE}/runs/${encodeURIComponent(runId)}/findings/${encodeURIComponent(fid)}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  benchResults(params: BenchParams = {}) {
    return request<BenchListResponse>(`${BASE}/bench/results${qs(params)}`);
  }
  settings() {
    return request<SettingsSnapshot>(`${BASE}/settings`);
  }
  me() {
    return request<Me>(`${BASE}/me`);
  }
  reportUrl(runId: string, fmt: ReportFormat) {
    // Plain navigation/download requests cannot carry X-API-Key, so the backend
    // also accepts the key as ?api_key=<key>; append it when one is configured.
    const key = getApiKey();
    return `${BASE}/runs/${encodeURIComponent(runId)}/report.${fmt}${qs({ api_key: key || undefined })}`;
  }

  streamEvents(runId: string, lastEventId: string | null, handlers: EventStreamHandlers): Unsubscribe {
    const url = `${BASE}/runs/${encodeURIComponent(runId)}/events`;
    const key = getApiKey();
    let closed = false;
    let lastId = lastEventId;
    let failures = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let es: EventSource | null = null;
    let ctrl: AbortController | null = null;

    const MAX_FAILURES = 4;

    const handleMessage = (id: string | null, type: string, data: string) => {
      if (id) lastId = id;
      const env = toEnvelope(id, type, data);
      if (env) handlers.onEvent(env);
    };

    const scheduleRetry = (err: unknown) => {
      if (closed) return;
      failures += 1;
      const fatal = failures >= MAX_FAILURES;
      handlers.onError?.(err, fatal);
      if (fatal) {
        stop();
        return;
      }
      const delay = Math.min(1000 * 2 ** (failures - 1), 8000);
      retryTimer = setTimeout(connect, delay);
    };

    const connectEventSource = () => {
      // EventSource cannot set custom headers, so it is used only for the initial
      // connection in open dev mode (no API key). The browser re-sends Last-Event-ID
      // itself on its native reconnects; if the stream is hard-closed we fall through
      // to the fetch-based transport, which sends Last-Event-ID as a header.
      const src = new EventSource(url);
      es = src;
      src.onopen = () => {
        failures = 0;
        handlers.onOpen?.();
      };
      for (const type of RUN_EVENT_TYPES) {
        src.addEventListener(type, (ev: MessageEvent<string>) => {
          handleMessage(ev.lastEventId || null, type, ev.data);
        });
      }
      src.onerror = (ev) => {
        if (src.readyState === EventSource.CONNECTING) {
          // native reconnect in flight; count it, and give up after too many
          failures += 1;
          handlers.onError?.(ev, false);
          if (failures >= MAX_FAILURES) {
            src.close();
            if (es === src) es = null;
            stop();
            handlers.onError?.(ev, true);
          }
          return;
        }
        src.close();
        if (es === src) es = null;
        scheduleRetry(ev);
      };
    };

    const connectFetch = () => {
      const c = new AbortController();
      ctrl = c;
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (key) headers["X-API-Key"] = key;
      if (lastId) headers["Last-Event-ID"] = lastId;
      fetch(url, { headers, signal: c.signal })
        .then(async (res) => {
          if (!res.ok || !res.body) throw await parseError(res);
          failures = 0;
          handlers.onOpen?.();
          await consumeSseStream(res.body, handleMessage, c.signal);
          if (!closed) scheduleRetry(new Error("stream closed"));
        })
        .catch((err: unknown) => {
          if (c.signal.aborted) return;
          scheduleRetry(err);
        });
    };

    const connect = () => {
      if (closed) return;
      retryTimer = null;
      // fetch transport whenever we must send headers (API key or Last-Event-ID)
      if (key || lastId) connectFetch();
      else connectEventSource();
    };

    const stop = () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
      ctrl?.abort();
    };

    connect();
    return stop;
  }
}

/* ------------------------------------------------------------------ */
/* Mode + facade                                                       */
/* ------------------------------------------------------------------ */

export type ApiMode = "live" | "mock" | "detecting";

let mode: ApiMode = "detecting";
let impl: ApiClient = new HttpClient();
const modeListeners = new Set<(m: ApiMode) => void>();

export function getApiMode(): ApiMode {
  return mode;
}
export function onApiModeChange(l: (m: ApiMode) => void): () => void {
  modeListeners.add(l);
  return () => modeListeners.delete(l);
}
function setMode(m: ApiMode, client: ApiClient) {
  mode = m;
  impl = client;
  modeListeners.forEach((l) => l(m));
}

/** Decide live vs mock. Resolves once a mode is chosen. */
export async function initApi(): Promise<ApiMode> {
  const forced = import.meta.env.VITE_MOCK === "1" || import.meta.env.VITE_MOCK === "true";
  if (forced) {
    const { createMockClient } = await import("./mock");
    setMode("mock", createMockClient());
    return mode;
  }
  const http = new HttpClient();
  try {
    await http.health();
    setMode("live", http);
  } catch {
    const { createMockClient } = await import("./mock");
    setMode("mock", createMockClient());
  }
  return mode;
}

/** Stable facade; every call resolves against the current implementation. */
export const api: ApiClient = {
  health: () => impl.health(),
  stats: () => impl.stats(),
  listRuns: (p) => impl.listRuns(p),
  getRun: (id) => impl.getRun(id),
  createRun: (b) => impl.createRun(b),
  cancelRun: (id) => impl.cancelRun(id),
  deleteRun: (id) => impl.deleteRun(id),
  listFindings: (id, f) => impl.listFindings(id, f),
  getFinding: (id, fid) => impl.getFinding(id, fid),
  decide: (id, fid, b) => impl.decide(id, fid, b),
  benchResults: (p) => impl.benchResults(p),
  settings: () => impl.settings(),
  me: () => impl.me(),
  reportUrl: (id, fmt) => impl.reportUrl(id, fmt),
  streamEvents: (id, last, h) => impl.streamEvents(id, last, h),
};
