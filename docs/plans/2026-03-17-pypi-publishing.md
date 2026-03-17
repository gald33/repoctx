# PyPI Trusted Publishing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish `repoctx` to PyPI from GitHub Actions using trusted publishing on version tags.

**Architecture:** Add a GitHub Actions workflow that builds distribution artifacts in one job and publishes them in a second job with OIDC credentials. Document the exact PyPI trusted publisher settings and the tag-based release flow in `README.md`, and ignore local build outputs in git.

**Tech Stack:** GitHub Actions, PyPI trusted publishing, Python build backend, setuptools

---

### Task 1: Ignore generated release artifacts

**Files:**
- Modify: `.gitignore`

**Step 1: Add ignore entries**

Add:

```gitignore
build/
dist/
```

**Step 2: Verify ignored outputs are not tracked**

Run: `git status --short`
Expected: `build/` no longer appears as an untracked change.

### Task 2: Add the publish workflow

**Files:**
- Create: `.github/workflows/publish-pypi.yml`

**Step 3: Add workflow**

Create a workflow that:

- triggers on tags `v*`
- checks out the repo
- installs Python and `build`
- runs `python -m build`
- uploads `dist/` as an artifact
- downloads the artifact in a publish job
- publishes with `pypa/gh-action-pypi-publish@release/v1`
- grants `id-token: write` in the publish job

**Step 4: Inspect workflow for correctness**

Run: `git diff -- .github/workflows/publish-pypi.yml`
Expected: workflow contains the tag trigger, build step, artifact handoff, and OIDC publish step.

### Task 3: Document release setup

**Files:**
- Modify: `README.md`

**Step 5: Add trusted publishing instructions**

Document:

- the PyPI trusted publisher values for this repo
- the tag-based release command sequence
- the fact that no GitHub secret is required

**Step 6: Verify docs and tests**

Run: `python3 -m pytest -q`
Expected: PASS.
