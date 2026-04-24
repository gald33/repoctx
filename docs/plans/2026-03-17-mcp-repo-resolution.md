# MCP Repo Resolution Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow RepoCtx to run from global MCP configuration without requiring a hardcoded absolute repo path, while safely resolving to the active repository's git root.

**Architecture:** Introduce a small repo-root resolution helper that chooses a candidate path from explicit overrides, environment context, or the current working directory, then normalizes that path to the nearest enclosing git root. Keep `--repo` as the strongest override, fail clearly when no git root can be found, and update MCP setup docs to make auto-detection the default path.

**Tech Stack:** Python 3.11, pathlib, git-root discovery logic, pytest, README-based setup docs

---

### Task 1: Add repo-root resolution helper

**Files:**
- Modify: `repoctx/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Step 1: Write the failing tests**

Add tests that cover:

- explicit `repo_root` still wins
- omitted `repo_root` resolves from the current working directory
- nested repositories resolve to the nearest git root
- resolution fails outside a git repository

Use temporary directories that create lightweight git markers such as `.git` directories or files to model plain and nested repositories.

**Step 2: Run the targeted tests to confirm failure**

Run: `python3 -m pytest tests/test_mcp_server.py -q`
Expected: FAIL because the server still uses `Path.cwd()` directly and does not normalize to a git root or reject non-git folders.

**Step 3: Implement minimal repo-root resolution**

Add a helper in `repoctx/mcp_server.py` that:

- accepts the explicit repo override if present
- otherwise checks `REPOCTX_REPO_ROOT`
- otherwise checks a small allowlist of host-provided workspace env vars if available
- otherwise starts from `Path.cwd()`
- walks upward to the nearest enclosing git root
- raises a clear `RuntimeError` if no git root is found

Support both `.git` directories and `.git` files so nested worktrees and submodules are not accidentally excluded.

**Step 4: Wire the helper into server creation**

Update `create_server()` so the resolved repo root is computed once and used consistently for tool calls and startup logging.

**Step 5: Re-run the targeted tests**

Run: `python3 -m pytest tests/test_mcp_server.py -q`
Expected: PASS.

### Task 2: Tighten server startup and error messaging

**Files:**
- Modify: `repoctx/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Step 6: Add actionable failure messages**

Make the repo-resolution error message explain:

- that RepoCtx could not resolve a git repository
- which candidate path was used
- that the user can fix it with `--repo` or `REPOCTX_REPO_ROOT`

**Step 7: Add or update tests for error text**

Assert that failure outside a repo includes the override guidance so users can self-correct quickly.

**Step 8: Run the targeted tests again**

Run: `python3 -m pytest tests/test_mcp_server.py -q`
Expected: PASS with assertions covering the failure message.

### Task 3: Update setup documentation

**Files:**
- Modify: `README.md`

**Step 9: Rewrite the primary MCP config examples**

Change the Cursor, Claude Desktop, and Codex examples so the primary example is:

```json
{
  "mcpServers": {
    "repoctx": {
      "command": "python3",
      "args": ["-m", "repoctx.mcp_server"]
    }
  }
}
```

Then add short follow-up examples or notes for:

- `${workspaceFolder}` where supported
- `--repo /path/to/repo` as an explicit override
- `REPOCTX_REPO_ROOT` for hosts with weak workspace context

**Step 10: Update the FAQ**

Document that:

- absolute paths are no longer required for the normal case
- RepoCtx resolves to the nearest enclosing git root
- nested repositories use the nearest repo, not the outermost parent repo

**Step 11: Inspect the docs diff**

Run: `git diff -- README.md`
Expected: the setup flow now leads with auto-detection and explains the fallback overrides clearly.

### Task 4: Verify the complete change

**Files:**
- Modify: `repoctx/mcp_server.py`
- Modify: `README.md`
- Test: `tests/test_mcp_server.py`

**Step 12: Run the focused test suite**

Run: `python3 -m pytest tests/test_mcp_server.py tests/test_main.py -q`
Expected: PASS.

**Step 13: Run the full test suite**

Run: `python3 -m pytest -q`
Expected: PASS.

**Step 14: Do a quick manual smoke check**

Run the server from the repository root without `--repo` and confirm it starts without a repo-resolution error.

Suggested command: `python3 -m repoctx.mcp_server --help`

If an interactive runtime smoke test is needed, start the server from the repo root and confirm the resolved root shown in logs is this repository.

**Step 15: Commit**

```bash
git add repoctx/mcp_server.py tests/test_mcp_server.py README.md
git commit -m "feat: auto-resolve MCP repo root"
```
