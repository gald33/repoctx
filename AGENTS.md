# Guidance for AI agents (RepoCtx repository & product)

This file is for **coding agents** (Cursor, Codex, Claude, etc.) working in this repo or advising users who run RepoCtx.

## Product shape

- **CLI:** `repoctx`, `repoctx query`, `repoctx index`, …
- **MCP:** module `repoctx.mcp_server` — tool `get_task_context(task)`.
- **Experiments:** `repoctx experiment` — resumable wizard; paired git worktrees (control vs treatment).
- **Channels:** stable (default) and canary (`pip install --pre repoctx-mcp`). Stable installs are silent / no network unless the user opts in to reporting; canary defaults to reporting-on with a one-time disclosure.

## Experiments: what to tell the user

1. Run **`repoctx experiment`** from the **main repo** (or a worktree); the wizard stores progress under telemetry and resumes on the next run.
2. **Control lane first:** open the printed **control** worktree, paste the **exact** prompt, run the agent, then run **`repoctx experiment` again** to record costs and continue.
3. **Treatment lane:** RepoCtx writes **`.cursor/mcp.json`** in the treatment worktree; the user may need to **restart Cursor** when opening that folder.

## Experiment MCP suppression (control lane)

When the control lane is active, RepoCtx may **stub** `get_task_context` (empty lists, explanatory `context_markdown`, `experiment_mcp_suppressed: true`) so the control agent does not get retrieval, even if RepoCtx stays in global Cursor MCP config.

**Agent-relevant facts:**

- Suppression is **time-bounded** and **CLI-extended**. If the user runs `repoctx experiment` periodically during the control run, the window stays open.
- If tools suddenly “work again” during control, the idle TTL may have expired — user can rerun `repoctx experiment` to re-arm or shorten `experiment_mcp_idle_ttl_seconds` in config.
- **Disable stubbing:** set `experiment_mcp_suppress` to `false` in `~/.repoctx/config.json` (or unset file and rely on defaults only if that key is omitted — default is `true`).
- State and timing: see **[docs/experiment-mcp-suppression.md](docs/experiment-mcp-suppression.md)**.

## Repo layout hints for contributors

- CLI entry: `repoctx/main.py`
- Experiment wizard + handoff: `repoctx/main.py` (experiment helpers), `repoctx/experiment.py`, `repoctx/telemetry.py` (active session + experiment JSONL)
- Suppression logic: `repoctx/experiment_mcp.py`
- MCP tool: `repoctx/mcp_server.py`
- Index location (identity-keyed, worktree-shared) + migration: `repoctx/index_location.py`
- origin/main-pinned scan from git objects: `repoctx/git_tree.py`; base refresh/TTL + retrieval status live in `repoctx/embeddings.py`
- Worktree overlay (your delta on the base): `repoctx/overlay.py`
- Advisory lane (opt-in, in-flight branches): `repoctx/advisory.py`

## Releasing to PyPI

Releases are fully automated by `.github/workflows/publish-pypi.yml`. **Do not run `twine upload` locally** — the workflow uses PyPI Trusted Publishing (OIDC), there's no token in `~/.pypirc`, and a manual upload either fails with `EOFError` (no TTY in agent contexts) or, if creds happen to be set, just duplicates what the workflow already did.

Full release flow:

1. Land the feature on `main` (squash-merge the PR).
2. Bump `project.version` in `pyproject.toml`.
3. Add a `[<x.y.z>] — YYYY-MM-DD` entry to `CHANGELOG.md` above the prior release.
4. Commit (`chore(release): <x.y.z> — <one-line summary>`) and push to `main`.
5. Tag the release commit locally: `git tag v<x.y.z>` (must match `project.version` exactly — the workflow's tag-vs-version check fails otherwise).
6. Push the tag: `git push origin v<x.y.z>`. That push triggers the workflow.
7. **Post-release: bump `project.version` to the next planned stable** (e.g. `1.7.0` after releasing `1.6.0`). Commit as `chore: bump pyproject to <next> for canary version ordering` and push to `main`. **Without this step canary builds become invisible to `pip install --pre`** — `pyproject.version` is what canary builds compute `<version>.devN` from, and PEP 440 puts `1.6.0.devN < 1.6.0`, so a canary based on the just-released stable version sorts below stable and `--pre` resolvers pick stable instead. Pick the next bump (patch / minor / major) based on what the next release is likely to be; canary lives in that gap.

The workflow fires on `v*` tag push, verifies tag-vs-version, builds wheel + sdist, and publishes via OIDC. Typical run is ~40s. Verify with `gh run list --limit 1` or `curl -fsSL https://pypi.org/pypi/repoctx-mcp/json | jq -r .info.version`.

If the verification step fails (tag doesn't match `project.version`), delete the broken tag (`git tag -d v<x.y.z>` and `git push --delete origin v<x.y.z>`), fix the mismatch, retag, push.

### Stable release without a tag push (GitHub Actions tab)

The same workflow has a `workflow_dispatch` trigger whose channel **defaults to `stable`**. On a non-tag run the tag-vs-version check is skipped, so it builds and publishes straight from `main`'s `pyproject.version`. Use this when a tag push isn't available:

- **GitHub UI:** Actions → **Publish PyPI** → **Run workflow** → branch `main`, channel `stable` → Run.
- **CLI:** `gh workflow run publish-pypi.yml -f channel=stable --ref main`.

`main` must already carry the release commit (`pyproject.version` bumped + dated changelog) before dispatching — the build publishes whatever version is on `main`. This path does **not** create a `v<x.y.z>` tag; add one afterwards if you want the record (pushing it re-triggers the workflow, which then fails on the duplicate PyPI upload — expected, harmless).

**Agent note:** a sandboxed agent environment typically **cannot** fire either trigger — the git proxy `403`s tag (and `main`) pushes, and the MCP integration token lacks `actions: write` (dispatch returns "Resource not accessible by integration"). Prepare the release commit on a feature branch and merge it via PR, then hand the final tag-push / `Run workflow` dispatch to a human.



Canary releases share the same workflow file but trigger via manual dispatch:

```bash
gh workflow run publish-pypi.yml -f channel=canary
```

The workflow then:

1. Skips the tag-vs-version check (canary versions are CI-computed, not tagged in source).
2. Runs `python scripts/release.py --channel canary --prepare-only --skip-clean-check`, which rewrites `repoctx/_build_channel.py` (`CHANNEL = "canary"`, `BUILD_ID` includes the short SHA) and bumps `pyproject.toml` to `<base>.dev<YYYYMMDDhhmmss>`.
3. Builds wheel + sdist and publishes via the same OIDC trusted publisher.

There is no need to bump `pyproject.toml` in source for canary — the workflow rewrites it ephemerally. Users get canary builds via `pip install --pre repoctx-mcp`.

`scripts/release.py` can also be run locally for testing (with `--dry-run` or just for a local build), but **never with `--upload`** in normal use.

## Releasing to PyPI

Releases are fully automated by `.github/workflows/publish-pypi.yml`. **Do not run `twine upload` locally** — the workflow uses PyPI Trusted Publishing (OIDC), there's no token in `~/.pypirc`, and a manual upload either fails with `EOFError` (no TTY in agent contexts) or, if creds happen to be set, just duplicates what the workflow already did.

Full release flow:

1. Land the feature on `main` (squash-merge the PR).
2. Bump `project.version` in `pyproject.toml`.
3. Add a `[<x.y.z>] — YYYY-MM-DD` entry to `CHANGELOG.md` above the prior release.
4. Commit (`chore(release): <x.y.z> — <one-line summary>`) and push to `main`.
5. Tag the release commit locally: `git tag v<x.y.z>` (must match `project.version` exactly — the workflow's tag-vs-version check fails otherwise).
6. Push the tag: `git push origin v<x.y.z>`. That push triggers the workflow.

The workflow fires on `v*` tag push, verifies tag-vs-version, builds wheel + sdist, and publishes via OIDC. Typical run is ~40s. Verify with `gh run list --limit 1` or `curl -fsSL https://pypi.org/pypi/repoctx-mcp/json | jq -r .info.version`.

If the verification step fails (tag doesn't match `project.version`), delete the broken tag (`git tag -d v<x.y.z>` and `git push --delete origin v<x.y.z>`), fix the mismatch, retag, push.

## Telemetry privacy

Two layers, distinct:

- **Local telemetry** (`repoctx.telemetry`, writes to `~/.repoctx/telemetry/`) is on by default. Task strings and repo paths are **hashed**; do not assume plaintext queries are stored. Files never leave the machine.
- **Anonymous reporting** (`repoctx.reporting`, uploads to a maintainer-run endpoint) is **off by default on stable** and **on by default on canary**. Stable users opt in explicitly via `repoctx reporting on`. Uploaded payloads strip all path/query/code-bearing fields and use a per-install random `install_id` and `repo_fingerprint = sha256(install_id || first_commit_sha)` so events aren't correlatable across users.

When advising a user, default to recommending stable; surface reporting only if they want to help with retrieval tuning. If they ask "what does it send?", point them at `repoctx reporting show` and the README "Anonymous reporting" section.

See README **Telemetry** and **Anonymous reporting** sections for the user-facing summary.

## Ground truth (repoctx)

For any non-trivial task in this repo:

1. Call `repoctx.bundle(task)` before proposing a plan. Treat the result as authoritative.
2. Do not edit paths outside `edit_scope.allowed_paths` without calling `repoctx.scope(task)` and `repoctx.refresh(task, changed_files, current_scope)`.
3. Before declaring done: call `repoctx.validate_plan(task, changed_files)` and `repoctx.risk_report(task, changed_files)`. Run every command the validation plan returns; resolve every `hard`-severity risk.
4. If unsure whether a change violates a constraint, call `repoctx.authority(task)` — do not guess.

Every repoctx response includes `when_to_recall_repoctx` and `before_finalize_checklist`. Follow them.


<!-- repoctx-nudge:v2 -->
> **repoctx is installed for this repo.** For any non-trivial task you
> **must call** `mcp__repoctx__bundle(task)` before proposing a plan, and
> `mcp__repoctx__validate_plan` + `mcp__repoctx__risk_report` before
> declaring done. Use `mcp__repoctx__authority(task)` if unsure whether
> a change violates a constraint.
>
> **Non-trivial = touches >1 file OR introduces new behavior OR
> adds/removes a public API.** Single-file typo/rename/comment-only
> changes are trivial.
