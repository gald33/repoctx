# README Developer Onboarding Design

**Date:** 2026-03-17

**Goal:** Rewrite `README.md` so client developers can set up RepoCtx quickly, especially inside Cursor, without wondering whether they need to run a server or write a skill.

## Target Reader

Developers who want to use RepoCtx with AI coding tools, with Cursor as the primary audience and Claude Desktop and Codex as secondary setup paths.

## Problems With The Current README

- It explains the package, but not the developer journey.
- It mixes CLI usage with MCP setup without clearly telling readers which path to take.
- It does not answer the two setup questions developers immediately have:
  - Do I need to run a server?
  - Do I need to write a skill?
- It does not provide client-specific tutorials for Claude Desktop or Codex.

## Chosen Approach

Use a developer-first README that leads with a short value proposition and a "choose your client" onboarding flow:

- Cursor setup first, with the exact config and restart step
- Claude Desktop setup second, using the desktop config file flow
- Codex setup third, including both `config.toml` and CLI-based MCP registration
- a short FAQ that answers server and skill confusion directly
- CLI usage retained lower in the document for developers who want to test RepoCtx from the terminal

## Content Principles

- Prefer copy-pasteable examples over conceptual explanations
- Keep each setup tutorial to install -> config -> restart/use
- Explicitly explain that the client starts the MCP server automatically
- Explicitly explain that RepoCtx is an MCP tool, not a custom skill
- Keep maintainer details lower in the document

## Verification

- Read the updated README end to end for setup clarity
- Verify the client setup snippets match current documented config formats for Cursor, Claude Desktop, and Codex
