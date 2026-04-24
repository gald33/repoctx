# MCP Repo Resolution Design

**Date:** 2026-03-17

**Goal:** Let RepoCtx run as a global MCP server without requiring a hardcoded absolute repository path in client config.

## Problem

Current setup guidance tells users to configure the MCP server with `--repo /absolute/path/to/your/repo`. That works, but it is a poor fit for global MCP server configuration in apps like Cursor because:

- every project needs a different absolute path
- the server can be configured globally but still wants project-local information
- hardcoded paths make setup harder to share and harder to keep portable across machines

At the same time, automatic repo selection must stay safe: RepoCtx should scope itself to the correct repository, not scan arbitrary parent directories.

## Chosen Approach

RepoCtx should treat repository selection as a resolution pipeline rather than a required explicit argument.

When the MCP server starts, it should resolve the repository root in this order:

1. `--repo`, if explicitly provided
2. `REPOCTX_REPO_ROOT`, if set
3. host-provided workspace environment variables, if available
4. process current working directory
5. walk upward from the selected candidate path until the nearest enclosing git root is found

Once a git root is found, RepoCtx should normalize to that root and scan only that repository.

## Safety Rules

- Never scan above the resolved git root.
- If the starting path is inside a git repository, resolve to the nearest enclosing git root.
- If the starting path is not inside any git repository, fail with a clear error instead of scanning a plain directory.
- If multiple candidate sources are available, honor the resolution order and stop at the first source that yields a valid git-rooted repo.

These rules ensure that global MCP setup remains bounded to the active project instead of drifting into unrelated directories.

## Nested Repository Behavior

Nested repositories should be handled by RepoCtx itself, not delegated to the client.

If the launch context points inside a nested repository, RepoCtx should select the nearest enclosing git root and stop there. It should not continue searching upward for a larger parent repository. This keeps focus aligned with the subproject the user is actively working in.

Examples:

- If the candidate path is `/workspace/app/packages/sdk`, and `packages/sdk/.git` exists, use `packages/sdk`.
- If the candidate path is `/workspace/app/src`, and only `/workspace/app/.git` exists, use `/workspace/app`.
- If the candidate path is outside any git repo, return an actionable error.

## Ambiguity Handling

RepoCtx should prefer explicit failure over guessing in ambiguous environments.

Normal single-repo workspaces should resolve automatically. For unusual environments, including hosts that provide weak or inconsistent workspace context, RepoCtx should only auto-select a repo if the chosen candidate path leads to exactly one nearest enclosing git root. If not, it should fail and instruct the user to use `--repo` or `REPOCTX_REPO_ROOT`.

The error should explain:

- which resolution inputs were attempted
- which candidate path was selected
- why repo resolution failed or was ambiguous
- how to override the result explicitly

## Client UX

Documentation should move from "always pass an absolute path" to "auto-detect by default, override when needed."

Recommended setup guidance:

- Default global config: `python3 -m repoctx.mcp_server`
- Optional portable explicit config where supported: use workspace interpolation such as `${workspaceFolder}`
- Explicit override for edge cases: `--repo /path/to/repo` or `REPOCTX_REPO_ROOT=/path/to/repo`

This gives users one global MCP server entry that follows the active project in the common case while preserving precise control when a host does not provide enough context.

## Implementation Notes

The current server already defaults to `Path.cwd()` when `--repo` is omitted. The main change is to formalize root discovery and enforce git-root scoping before server creation or request handling.

Implementation should include:

- a shared helper to resolve the effective repo root
- tests for explicit override, cwd-based discovery, env-var override, nested repos, and failure outside git
- README updates that make auto-detection the primary setup path

## Verification

- Add automated tests for repo-root resolution edge cases.
- Verify that explicit `--repo` behavior still works.
- Verify that running the server without `--repo` inside this repository resolves to the current repo root.
