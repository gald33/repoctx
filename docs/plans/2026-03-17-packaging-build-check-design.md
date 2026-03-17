# Packaging Build Check Design

**Date:** 2026-03-17

**Goal:** Add a local test-suite check that verifies `repoctx` can build publication-ready wheel and sdist artifacts.

## Scope

The check will run inside `pytest`, not in CI. It will exercise the real packaging path by invoking `python -m build --sdist --wheel` from the repository root and writing artifacts into a temporary directory.

## Chosen Approach

Add a dedicated packaging test that:

- runs `python -m build --sdist --wheel --outdir <tmpdir>`
- asserts the build command exits successfully
- asserts exactly one wheel and one sdist are produced
- asserts the generated filenames are for the `repoctx` package

This gives high confidence that the project metadata and setuptools configuration are publishable without adding the cost and brittleness of archive-content inspection or wheel-install smoke tests.

## Files Expected To Change

- `pyproject.toml`
- `tests/test_packaging.py`

## Verification

- Run the new packaging test directly first.
- Run the full `pytest` suite after the implementation passes.
