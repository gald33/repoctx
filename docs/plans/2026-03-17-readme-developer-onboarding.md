# README Developer Onboarding Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rewrite `README.md` so developers can install RepoCtx and connect it to Cursor, Claude Desktop, or Codex with minimal confusion.

**Architecture:** Reorganize the README around the developer setup flow instead of the package internals. Lead with Cursor onboarding, add short client-specific tutorials for Claude Desktop and Codex, answer the "server vs skill" confusion in a FAQ, and keep CLI/testing details lower in the document.

**Tech Stack:** Markdown, MCP client configuration, Python CLI packaging

---

### Task 1: Capture the approved documentation intent

**Files:**
- Create: `docs/plans/2026-03-17-readme-developer-onboarding-design.md`

**Step 1: Save the approved design**

Document the target audience, chosen README structure, and the setup confusion the rewrite must resolve.

**Step 2: Verify the design file exists**

Run: `git status --short docs/plans/2026-03-17-readme-developer-onboarding-design.md`
Expected: the design file appears as a new tracked change.

### Task 2: Rewrite the README around the developer journey

**Files:**
- Modify: `README.md`

**Step 3: Replace the top-level structure**

Rewrite the README to include:

- a short developer-facing value proposition
- a Cursor-first onboarding path
- separate tutorials for Claude Desktop and Codex
- a short FAQ that answers whether users need to run a server or write a skill
- standalone CLI usage lower in the document

**Step 4: Use current MCP config formats**

Ensure the examples follow the current documented config structures:

- JSON `mcpServers` config for Cursor
- JSON `claude_desktop_config.json` style config for Claude Desktop
- TOML or `codex mcp add` usage for Codex

### Task 3: Verify the README is clear and reviewable

**Files:**
- Modify: `README.md`

**Step 5: Review the markdown for clarity**

Read the updated README end to end and confirm:

- the default setup path is Cursor
- the README explicitly says no manual server process is required
- the README explicitly says no custom skill is required
- Claude Desktop and Codex readers each get a concrete tutorial

**Step 6: Verify git diff**

Run: `git diff -- README.md docs/plans/2026-03-17-readme-developer-onboarding-design.md docs/plans/2026-03-17-readme-developer-onboarding.md`
Expected: the diff shows a README rewrite plus the new design and plan documents.
