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

`vite.config.ts` sets `base: "./"` so `dist/` can be mounted by FastAPI at `/` (or any sub-path). Routing uses the URL hash (`/#/runs/abc`) so no SPA fallback route is needed on the server.

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
