# PyPI Trusted Publishing Design

**Date:** 2026-03-17

**Goal:** Publish `repoctx` to PyPI from GitHub Actions without storing a PyPI API token in GitHub.

## Scope

Set up a GitHub Actions workflow that builds the package on version tags and publishes it to PyPI using trusted publishing via OpenID Connect.

## Chosen Approach

Use a tag-triggered workflow at `.github/workflows/publish-pypi.yml` that:

- runs on tags matching `v*`
- builds both the wheel and sdist
- uploads the built artifacts between jobs
- publishes with `pypa/gh-action-pypi-publish`
- grants the publish job `id-token: write`

## PyPI-Side Setup

Create a trusted publisher in PyPI for:

- owner: `gald33`
- repository: `repoctx`
- workflow file: `publish-pypi.yml`

No GitHub secret is required for publishing once that PyPI configuration is in place.

## Verification

- Keep the existing local packaging test green.
- Inspect the workflow file and release documentation locally.
- After merge, create a test release tag once PyPI trusted publishing is configured.
