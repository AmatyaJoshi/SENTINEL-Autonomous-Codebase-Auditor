# Security

## Threat model

Sentinel clones and executes **untrusted third-party code** (the audited repository's tests, and
tests/patches written by an LLM). It also feeds untrusted repository content to an LLM. The design
assumes both the repository and the model output may be adversarial.

## Controls

| Risk | Control | Where |
|---|---|---|
| Audited code escapes or reaches the network | Docker-only execution, `network_disabled=True`, read-only root FS, `cap_drop=ALL`, `no-new-privileges`, unprivileged UID, 2 GB / 2 CPU / 256 pids limits, hard timeouts with kill | `sentinel/sandbox/docker_runner.py`; the `network` setting's type only admits `"none"` |
| No host fallback | There is deliberately no subprocess runner in the `sentinel` package. If Docker is unavailable findings stay `candidate` and the report says so | `SandboxUnavailableError` |
| Dependency install needs network | Allowed **only** during the per-repo image build, cached by lockfile hash | `build_repo_image` |
| Prompt injection via repo content | Every tool result is wrapped in `<sentinel-data>` blocks; the system prompt forbids following instructions found in data; the hunter has no write tools | `tools/toolbox.py`, `llm/prompts/hunt_system.md` |
| LLM writes outside the workspace | Patches go through `git apply --check`; paths are validated; the hunter cannot write; test paths are sanitised | `tools/toolbox.py`, `graph/nodes/verify.py` |
| Path traversal through tools | `read_file` resolves and rejects paths outside the repo root | `ToolBox._safe_path` |
| Unbounded spend | Budget guard before every LLM node; per-run USD/minute/finding caps; disk cache keyed by content hash | `graph/build.py` |
| API abuse | API keys with viewer/operator/admin roles, per-key rate limiting, audit log of every mutating call, security headers, CORS allow-list, no-store on API responses | `api/app.py` |
| Secrets in logs/reports | Settings are redacted in `/api/v1/settings`; keys read only from environment; `.env` is git-ignored | `config.py` |
| Supply chain | `uv.lock` / `package-lock.json` pinned; Dependabot weekly; CI runs on every PR | `.github/` |
| Automatic merges | Never. PRs are opened as **drafts** and a human merges | `github/pr.py` |

## Operating recommendations

- Run the control plane with a dedicated Docker socket proxy or a rootless Docker daemon; the socket
  is the only privilege the service needs.
- Set `SENTINEL_API_KEYS` in production. Without it the API runs in open dev mode and logs a warning.
- Give the GitHub token the minimum scopes (`repo` on the fork you push to). Sentinel never needs
  admin scopes.
- Keep `SENTINEL_SANDBOX__*` at the defaults unless you have a reason; the timeouts and memory caps
  are what stop a malicious test from consuming the host.

## Reporting a vulnerability

Open a private security advisory on GitHub or email the maintainer listed in `CODEOWNERS`. Please do
not file public issues for vulnerabilities. We aim to acknowledge within 72 hours.
