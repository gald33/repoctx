# RepoCtx Experiment Mode Design

**Date:** 2026-03-18

**Goal:** Add a super-simple experiment mode that compares a `control` run against a `repoctx` run using the exact same prompt, separate worktrees, manual cost checkpoints, and objective git diff stats.

## Product Shape

The experiment UX should stay CLI-first and intentionally guided.

Default entry should be an interactive wizard:

```bash
repoctx experiment
```

The wizard should collect a multiline prompt, optionally attach strict comparison guardrails, show a confirmation summary, then create the experiment session and paired worktrees.

The one-line fast path should remain available for users who already have the prompt ready:

```bash
repoctx experiment "your task prompt"
```

Both flows should store the canonical prompt and print the exact next commands for both lanes.

After each lane, the user records the total cost shown by their external agent UI before and after the run. RepoCtx calculates the delta instead of asking the user to do the math.

## Why Separate Worktrees

Diff-based metrics like changed files or edited lines only make sense if both lanes start from the same clean baseline.

To make the comparison controlled:

- create one `control` worktree
- create one `repoctx` worktree
- pin both to the same base commit
- require the exact same prompt text for both lanes

This avoids polluted stats from sequential edits in one checkout.

## Metrics To Collect

### Manual, Structured Inputs

- `cost_before_usd`
- `cost_after_usd`
- `completion_status`
- `verification_status`
- optional `outcome_summary`
- optional `notes`

### RepoCtx-Controlled Metrics

- `prompt`
- `prompt_hash`
- `base_commit`
- `worktree_path`

### Objective Git Metrics

- `files_changed`
- `lines_added`
- `lines_deleted`
- `net_lines`
- `new_files`
- `modified_files`
- `source_files_changed`
- `test_files_changed`
- `docs_files_changed`
- `config_files_changed`

## Storage

Store experiment data separately from the existing telemetry events:

- `~/.repoctx/telemetry/experiment-runs.jsonl`

Use append-only JSONL records for:

- `experiment_session`
- `experiment_lane`

This keeps experiment UX data separate from lower-level `repoctx_invocation` and `agent_run` telemetry.

## Summary Output

The summary should compare both lanes in plain language and include more than cost:

- cost deltas
- savings amount and percentage
- changed files
- line additions/deletions
- completion status
- verification status
- a note that both lanes share the same base commit and prompt hash

## Limitations

This first version should explicitly avoid pretending to measure things it cannot measure reliably:

- elapsed time is not a core metric because prompt submission is manual
- quality is not automatically scored
- cost accuracy depends on the numbers entered by the user
