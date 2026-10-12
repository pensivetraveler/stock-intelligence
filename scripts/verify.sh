#!/usr/bin/env bash
set -euo pipefail

export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"

uv sync --frozen --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy services packages/contracts tests/contracts
uv run pytest

pnpm install --frozen-lockfile
generated_contract_snapshot="$(mktemp)"
trap 'rm -f "$generated_contract_snapshot"' EXIT
cp packages/contracts/generated/contracts.d.ts "$generated_contract_snapshot"
pnpm contracts:generate
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm build

if ! cmp -s "$generated_contract_snapshot" packages/contracts/generated/contracts.d.ts; then
  echo "Generated TypeScript contracts are stale." >&2
  diff -u "$generated_contract_snapshot" packages/contracts/generated/contracts.d.ts || true
  exit 1
fi
