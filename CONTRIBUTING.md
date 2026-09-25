# Contributing

Thanks for helping. IndexScout stays small, read-only, and evidence-first.

## Setup

```bash
git clone https://github.com/jpow18/indexscout
cd indexscout
uv sync
uv run pytest
```

## Before you open a pull request

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest
uv run indexscout doctor
```

Tests use a fake Search Console (`tests/conftest.py`). Never add live credentials, real property
names, private URLs, or real GSC exports to tests, fixtures, issues, or docs. Use `example.com`.

## Ground rules

- **No write capability.** Do not add tools or code that call write methods or the Indexing API,
  and do not widen the OAuth scope. Pull requests that do will be closed.
- **No causal claims.** Analyses report associations; wording must not claim why traffic changed.
- **Deterministic analysis.** New scores or classifications need a documented formula, returned in
  `provenance`, and tests that pin the numbers.
- **Bounded responses.** New lists need a limit and, when large, pagination.
- **Keep it simple.** No databases, telemetry, dashboards, or speculative abstractions.

## Releases

Update `CHANGELOG.md` and the version in `pyproject.toml`, `src/indexscout/__init__.py`, the plugin
manifests, `mcpb/manifest.json`, and the pinned tag in `.mcp.json`. Tag `vX.Y.Z` after CI passes.
