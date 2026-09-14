# Sentinel dashboard

Single-page frontend for Sentinel, the autonomous codebase auditor. Built with Vite, React 18, TypeScript, Tailwind CSS, framer-motion, recharts and react-router. It talks to the FastAPI backend strictly via the contract in `../docs/API.md`.

## Requirements

- Node 20+ (Node 24 tested) and npm.

## Develop

```bash
cd dashboard
npm install
npm run dev            # http://localhost:5173
```

The dev server proxies `/api` and `/health` to `http://127.0.0.1:8000`, so run `sentinel serve` alongside it.

### Demo mode (no backend)

```bash
VITE_MOCK=1 npm run dev       # PowerShell: $env:VITE_MOCK="1"; npm run dev
```

If the backend is unreachable at startup the app also switches to the in-memory mock server automatically and shows a "Demo data" badge in the header. The mock seeds historical runs, findings and benchmark results and simulates a live run that walks through all pipeline nodes with SSE-shaped events. Starting a new audit in demo mode spawns another simulated run; enabling "Require review" pauses it in `awaiting_review` so approve/reject can be exercised.

## Build

```bash
npm run typecheck      # tsc --noEmit
npm run lint           # eslint (flat config, typescript-eslint, react-hooks)
npm run build          # typecheck + vite build -> dashboard/dist
```

`vite.config.ts` sets `base: "/"`: the FastAPI backend serves `dashboard/dist` from `/` with static assets under `/assets` and an SPA fallback route (any non-`/api` path returns `index.html`). Routing uses `BrowserRouter`, so deep links such as `/runs/abc` work when pasted directly into the address bar. The Vite dev server provides the same history fallback out of the box.

## Test

```bash
npm test               # vitest run (jsdom + Testing Library)
npm run test:watch     # vitest in watch mode
npm run test:coverage  # v8 coverage report in coverage/
```

Unit and component tests live next to their sources as `*.test.ts(x)` under `src/`; shared fixtures and the jsdom setup (jest-dom matchers, `matchMedia`/`ResizeObserver` stubs) are in `src/test/`. Coverage includes the formatting helpers, the HTTP client (mocked `fetch`), the in-memory mock server (sequence-numbered events, `Last-Event-ID` replay, decisions, cancel), `useRunEvents` (SSE to polling fallback with fake timers), `PipelineGraph`, `FindingDrawer`, `RunDetail` approve/reject gating, and a smoke render of every page through the real `App` under the mock client.

### End-to-end (Playwright)

```bash
npm run e2e:install    # one-time: downloads Chromium
npm run e2e            # boots vite with VITE_MOCK=1 on :5175 and runs e2e/smoke.spec.ts
```

The smoke suite checks that the five pages plus the 404 page render, that a hard navigation to a `/runs/:id` deep link resolves, and that the seeded live run's pipeline graph progresses. Set `E2E_PORT` to use a different port.

### CI

The `dashboard` job in `.github/workflows/ci.yml` runs `npm run lint`, `npm run typecheck`, `npm test` and `npm run build` on every push and pull request. The Playwright suite is not part of CI.

## Authentication

The API key is stored in `localStorage` under `sentinel.apiKey` and sent as `X-API-Key` on every request (Settings page). When a key is present the SSE stream uses a `fetch`-based reader (browsers' `EventSource` cannot send custom headers); without a key it uses native `EventSource`. Reconnects send `Last-Event-ID`; after repeated failures the run page falls back to polling every 5 seconds.

## Layout

```
src/
  lib/api.ts           typed client + live/mock facade
  lib/types.ts         models mirroring docs/API.md
  lib/mock.ts          in-memory mock server + run simulator
  lib/useRunEvents.ts  SSE hook (reconnect, polling fallback)
  lib/format.ts        relative time, USD, percent, durations
  hooks/               theme, toast, session (me/health/mode), async, hotkey
  components/          shell, pipeline graph, activity feed, finding drawer, charts, ui primitives
  pages/               Overview, Runs, RunDetail, Bench, Settings, NotFound
```

## Keyboard

- `/` focuses the runs search (from any page).
- `Esc` closes dialogs and the finding drawer.
