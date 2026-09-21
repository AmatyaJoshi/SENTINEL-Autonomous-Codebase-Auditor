# ZERO-COST POLICY

## Non-negotiable
This project must cost the team **$0**. Before adding ANY external service, dependency with a hosted
component, cloud resource, model API, domain, or analytics tool, confirm it is free for our usage and
record it in this file (service · free-tier limit · in-code guard · what happens when the limit is hit).
If the only way to meet a requirement costs money, STOP and ask; propose the free alternative first.
Never add a payment card to any account. Never create resources outside the free tier.

## Approved free stack (use these; ask before deviating)
- **LLM inference:** Ollama on our Docker host (default: `qwen2.5-coder:7b` for code, `llama3.1:8b` for
  text; `mistral` fallback). Cloud fallbacks only via free tiers with no card: Google Gemini free tier,
  Groq free tier. Every LLM call goes through a budget guard: per-run call cap, per-day token cap, and a
  `DEGRADED` mode that switches to the deterministic/template path and says so in the UI.
- **AWS (Vigil only):** Amazon Bedrock only if the $150 hackathon credits are confirmed active; otherwise
  Strands SDK with the Ollama model provider (qualifies for AWS Builder) or Kiro. AWS Budget alert at $1.
  Delete IAM keys at submission.
- **Compute/hosting:** Oracle Cloud Always Free ARM VM (primary) running Docker Compose; Render free tier
  (backup, accept sleep); frontends on Vercel Hobby or Cloudflare Pages. No Fly.io, Railway, Hetzner, DO.
- **Data:** Postgres/Redis/ClickHouse in Compose on the free VM; or Neon/Supabase free Postgres and
  Upstash free Redis. Sizes must stay under free limits; add retention/GC jobs to guarantee it.
- **Domains/TLS:** platform subdomains or DuckDNS + Caddy (Let's Encrypt). No purchased domains.
- **Analytics:** self-hosted Umami on the VM, or PostHog free tier. Not Plausible.
- **Payments:** Stripe test mode only.
- **CI:** GitHub Actions on public repos (free minutes); keep jobs under 10 minutes.
- **Third-party APIs:** Ring developer (free), Evorozen Pulse (free tier + requested quota; cache reads,
  batch writes, hard call budget), CISA KEV / EPSS / OSV / NVD (free, unauthenticated; respect rate limits).
- **Tooling:** OBS for video, k6 for load tests, draw.io/Mermaid for diagrams.

## In-code guards required
- `BUDGET_*` env vars for LLM calls, external API calls and storage; defaults set to free-tier-safe values.
- A `/health` field `cost_mode: normal|degraded` and a UI badge when degraded.
- Startup log listing every outbound host; anything not on the allow-list fails closed.
- Tests that assert the guards trip (call cap reached → degraded, not an exception or a paid retry).

## Reporting
At the end of each build step, print a one-line cost check: services touched, calls made, credits
used (should be 0 or "within free tier"), and any risk of crossing a limit before the deadline.

---

## Onix service register (keep current)

| Service | Used for | Free-tier limit | In-code guard | When the limit is hit |
|---|---|---|---|---|
| OpenRouter `:free` models (`ONIX_OPENROUTER_API_KEY`) | default LLM pool when only an OpenRouter key is set (`Settings.OPENROUTER_DEFAULTS`) | free models: rate-limited per minute/day, no card needed | model rotation with failover, `llm_backoff_s`, per-run `budget.max_usd` / `max_minutes` / `max_findings`, LLM response cache | next model in pool; after a full pass the run sleeps `llm_backoff_s` and retries; run stops at budget |
| Ollama (`ollama/…` models) | local inference, no key | unlimited, local | same budget guard | n/a |
| GitHub API (`ONIX_GITHUB_TOKEN`) | clone, open draft PRs | 5k requests/h per token | one PR per run (or grouped) | PR step fails, run still reports |
| GitHub Actions | CI, nightly bench | free on public repos | `ci.yml` jobs are parallel and short | none |
| Docker Hub `python:3.12-slim`, `node:20` | sandbox base images | anonymous pulls 100/6h | images cached locally, built once per host | build waits/fails, sandbox unavailable |
| ghcr.io/amatyajoshi/onix | Helm image reference | free for public packages | none needed | none |
| Jaeger / Postgres / pgvector | compose services | self-hosted, local | `retention_days`, `gc_on_startup` | n/a |
| Anthropic / OpenAI / DeepSeek / Voyage keys | **optional, paid** providers | none | only used if their key is set; never a fallback from a keyless provider | do not set these keys |

### Known gaps against this policy (follow-up work, not done in the rename PR)
- `ONIX_PRIMARY_MODEL` / `ONIX_CHEAP_MODEL` / `ONIX_FALLBACK_MODEL` defaults name paid Anthropic/OpenAI
  models. They are inert without keys (the router drops keyless providers and uses the OpenRouter free
  pool), but the defaults should become `ollama/qwen2.5-coder:7b`, `ollama/llama3.1:8b`, `ollama/mistral`.
- No `BUDGET_*` env vars, no per-day token cap, no `cost_mode` in `/health`, no outbound-host allow-list.
  Existing guards are per-run only (`ONIX_BUDGET__MAX_USD` etc.).
- Gemini / Groq free tiers are not wired as cloud fallbacks.
