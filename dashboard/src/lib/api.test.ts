/**
 * HTTP client tests. Before initApi() runs the `api` facade delegates to the
 * HttpClient, so we exercise it through the facade with a mocked global fetch.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_KEY_STORAGE, ApiError, BASE, api, errorMessage, getApiKey, initApi, isApiError, onApiKeyChange, setApiKey, toEnvelope } from "./api";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" }, ...init });
}

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  localStorage.clear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("request()", () => {
  it("GETs JSON with Accept header and no API key when none is stored", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ runs_total: 3 }));
    const stats = await api.stats();
    expect(stats.runs_total).toBe(3);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${BASE}/stats`);
    const headers = init.headers as Record<string, string>;
    expect(headers.Accept).toBe("application/json");
    expect(headers["X-API-Key"]).toBeUndefined();
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("sends X-API-Key and a JSON content type on POST bodies", async () => {
    setApiKey("secret-key");
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "r1" }));
    await api.createRun({ repo: "x", sha: null, open_pr: false, review: false, max_usd: 5, max_minutes: 60, max_findings: 25, arm: "full" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${BASE}/runs`);
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-API-Key"]).toBe("secret-key");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(String(init.body))).toMatchObject({ repo: "x", arm: "full" });
  });

  it("serialises query params, skipping empty values, and encodes path segments", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], total: 0 }));
    await api.listRuns({ limit: 20, offset: 0, status: undefined });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`${BASE}/runs?limit=20&offset=0`);

    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [] }));
    await api.listFindings("run/with slash", { status: "fixed", category: "" });
    expect(fetchMock.mock.calls[1]?.[0]).toBe(`${BASE}/runs/run%2Fwith%20slash/findings?status=fixed`);
  });

  it("turns a structured error body into ApiError with code, message and Retry-After", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "rate_limited", message: "Slow down" } }, { status: 429, statusText: "Too Many Requests", headers: { "Content-Type": "application/json", "Retry-After": "7" } }),
    );
    const err = await api.stats().catch((e: unknown) => e);
    expect(isApiError(err)).toBe(true);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(429);
    expect(apiErr.code).toBe("rate_limited");
    expect(apiErr.message).toBe("Slow down");
    expect(apiErr.retryAfter).toBe(7);
    expect(errorMessage(apiErr)).toBe("Slow down");
  });

  it("falls back to status text for non-JSON error bodies", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>nope</html>", { status: 502, statusText: "Bad Gateway", headers: { "Content-Type": "text/html" } }));
    const err = (await api.getRun("abc").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(502);
    expect(err.code).toBe("http_error");
    expect(err.message).toBe("502 Bad Gateway");
    expect(err.retryAfter).toBeNull();
  });

  it("resolves undefined for 204 responses", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(api.deleteRun("r1")).resolves.toBeUndefined();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${BASE}/runs/r1`);
    expect(init.method).toBe("DELETE");
  });

  it("propagates network failures as plain errors", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(api.health()).rejects.toThrow("Failed to fetch");
    expect(errorMessage("boom")).toBe("boom");
  });
});

describe("reportUrl()", () => {
  it("builds the report path without a key and appends api_key when one is stored", () => {
    expect(api.reportUrl("r 1", "html")).toBe(`${BASE}/runs/r%201/report.html`);
    setApiKey("k123");
    expect(api.reportUrl("r1", "json")).toBe(`${BASE}/runs/r1/report.json?api_key=k123`);
    expect(api.reportUrl("r1", "md")).toBe(`${BASE}/runs/r1/report.md?api_key=k123`);
  });
});

describe("API key storage", () => {
  it("persists to localStorage and notifies listeners", () => {
    const listener = vi.fn();
    const off = onApiKeyChange(listener);
    setApiKey("abc");
    expect(getApiKey()).toBe("abc");
    expect(localStorage.getItem(API_KEY_STORAGE)).toBe("abc");
    setApiKey("");
    expect(getApiKey()).toBe("");
    expect(localStorage.getItem(API_KEY_STORAGE)).toBeNull();
    expect(listener).toHaveBeenCalledTimes(2);
    off();
    setApiKey("zzz");
    expect(listener).toHaveBeenCalledTimes(2);
  });
});

describe("toEnvelope()", () => {
  it("parses known event types and uses numeric ids as seq", () => {
    const env = toEnvelope("42", "node.start", JSON.stringify({ node: "hunt", at: "2026-09-14T00:00:00Z" }));
    expect(env).not.toBeNull();
    expect(env?.id).toBe("42");
    expect(env?.seq).toBe(42);
    expect(env?.event.type).toBe("node.start");
  });

  it("rejects unknown types and malformed JSON", () => {
    expect(toEnvelope("1", "bogus.type", "{}")).toBeNull();
    expect(toEnvelope("1", "log", "{not json")).toBeNull();
  });
});

describe("initApi()", () => {
  it("falls back to the mock client when /health is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const mode = await initApi();
    expect(mode).toBe("mock");
    // the facade now resolves against the mock server, not fetch
    fetchMock.mockClear();
    const me = await api.me();
    expect(me.role).toBe("admin");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
