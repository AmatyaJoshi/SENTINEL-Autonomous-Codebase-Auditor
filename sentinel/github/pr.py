"""Open one PR per fixed finding (or one grouped PR) via GitPython + PyGithub (SPEC §3.2 report)."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from sentinel.graph.state import AuditState, Finding
from sentinel.tools.context import RunContext
from sentinel.tools.toolbox import apply_unified_diff

PR_BODY = """## Sentinel verified bug fix

**Category:** `{category}` · **Severity:** {severity} · **Confidence:** {confidence:.2f} · **Blast radius:** {blast} callers

{explanation}

### Triggering input
{hypothesis}

### Proof
A failing test was generated and executed in a network-isolated sandbox **before** the fix
(`{test_path}`), then re-run after the fix. The repository's existing test suite passed with the
patch applied ({regress_summary}).

<details><summary>Failing test</summary>

```{lang}
{test_code}
```
</details>

### Audit trail
- Run: `{run_id}` · commit audited: `{commit}` · Sentinel {version}
- Verify log excerpt:
```
{verify_excerpt}
```

---
*Opened by [Sentinel](https://github.com/AmatyaJoshi/SENTINEL-Autonomous-Codebase-Auditor). A human must review and merge; Sentinel never merges.*
"""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def parse_github_url(url: str) -> tuple[str, str] | None:
    m = re.match(r"(?:https://github\.com/|git@github\.com:)([^/]+)/([^/.]+)(?:\.git)?/?$", url)
    return (m.group(1), m.group(2)) if m else None


def open_pull_requests(ctx: RunContext, state: AuditState, fixed: list[Finding]) -> list[Finding]:
    from git import Repo
    from github import Auth, Github

    from sentinel import __version__

    token = ctx.settings.github_token
    if token is None:
        raise RuntimeError("SENTINEL_GITHUB_TOKEN not configured")
    target = parse_github_url(state.get("repo_url", ""))
    if target is None:
        raise RuntimeError("repo_url is not a GitHub URL; cannot open PRs")
    owner, name = target
    gh = Github(auth=Auth.Token(token.get_secret_value()))
    gh_repo = gh.get_repo(f"{owner}/{name}")
    base_branch = gh_repo.default_branch
    grouped = ctx.settings.pr_grouped and len(fixed) > 1

    with tempfile.TemporaryDirectory(prefix="sentinel-pr-") as tmp:
        clone = Repo.clone_from(
            f"https://x-access-token:{token.get_secret_value()}@github.com/{owner}/{name}.git",
            tmp,
            branch=base_branch,
            depth=50,
        )
        clone.config_writer().set_value("user", "name", "sentinel-bot").release()
        clone.config_writer().set_value(
            "user", "email", "sentinel-bot@users.noreply.github.com"
        ).release()
        updates: list[Finding] = []
        groups = [fixed] if grouped else [[f] for f in fixed]
        for group in groups:
            head = f"sentinel/{ctx.run_id[:8]}/{_slug(group[0].category + '-' + Path(group[0].file).stem)}"
            clone.git.checkout(base_branch)
            clone.git.checkout("-b", head)
            for f in group:
                ok, msg = apply_unified_diff(Path(tmp), f.patch_diff or "")
                if not ok:
                    ctx.log(
                        f"PR skipped for {f.id}: patch does not apply on {base_branch}: {msg}",
                        level="warning",
                    )
                    continue
                if f.test_path and f.test_code:
                    tp = Path(tmp) / f.test_path
                    tp.parent.mkdir(parents=True, exist_ok=True)
                    tp.write_text(f.test_code, encoding="utf-8")
            clone.git.add(A=True)
            if not clone.is_dirty(untracked_files=True):
                continue
            title = (
                (
                    f"fix({_slug(Path(group[0].file).stem)}): {group[0].category.replace('_', ' ')} in "
                    f"{group[0].symbol or group[0].file}"
                )
                if len(group) == 1
                else f"fix: {len(group)} Sentinel-verified bugs"
            )
            clone.index.commit(
                title + "\n\nVerified by a generated failing test executed in a sandbox."
            )
            clone.git.push("--set-upstream", "origin", head, force=True)
            body = "\n\n---\n\n".join(_body(ctx, state, f, __version__) for f in group)
            pr = gh_repo.create_pull(
                title=title, body=body, head=head, base=base_branch, draft=True
            )
            for f in group:
                nf = f.model_copy(update={"status": "pr_opened", "pr_url": pr.html_url})
                ctx.emit("finding.update", nf.model_dump())
                updates.append(nf)
            ctx.log(f"opened PR {pr.html_url}")
    return updates


def _body(ctx: RunContext, state: AuditState, f: Finding, version: str) -> str:
    return PR_BODY.format(
        category=f.category,
        severity=f.severity,
        confidence=f.confidence,
        blast=f.blast_radius,
        explanation=f.explanation or f.description,
        hypothesis=f.hypothesis,
        test_path=f.test_path,
        regress_summary=(f.regress_log or "").strip().split("\n")[-1][:120],
        lang="python" if ctx.language == "python" else "ts",
        test_code=(f.test_code or "")[:6000],
        run_id=ctx.run_id,
        commit=(state.get("commit_sha") or "")[:10],
        version=version,
        verify_excerpt=(f.verify_log or "")[-800:],
    )
