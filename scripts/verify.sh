#!/usr/bin/env bash
set -euo pipefail

export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"

uv sync --frozen --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy services packages/contracts tests/contracts
uv run pytest

pnpm install --frozen-lockfile
pnpm contracts:generate
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm build

if ! git diff --quiet -- packages/contracts/generated/contracts.d.ts; then
  echo "Generated TypeScript contracts are stale." >&2
  git diff -- packages/contracts/generated/contracts.d.ts
  exit 1
fi
