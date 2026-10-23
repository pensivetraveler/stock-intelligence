#!/usr/bin/env bash
set -euo pipefail

export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"

uv sync --frozen --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy services packages/contracts tests/contracts
uv run pytest
# Paper-order code must name no live broker domain (ADR 0037).
uv run python scripts/check_paper_only.py services/paper_trading

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

# Package scripts call `pnpm` through `sh`, so a shell function is not enough. When pnpm
# is only reachable through corepack (packageManager in package.json), put a shim on PATH.
if ! command -v pnpm >/dev/null 2>&1; then
  if ! command -v corepack >/dev/null 2>&1; then
    echo "pnpm is not installed and corepack is unavailable; install Node with corepack." >&2
    exit 1
  fi
  mkdir -p "$work_dir/bin"
  printf '#!/bin/sh\nexec corepack pnpm "$@"\n' >"$work_dir/bin/pnpm"
  chmod +x "$work_dir/bin/pnpm"
  export PATH="$work_dir/bin:$PATH"
  export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
fi

pnpm install --frozen-lockfile
generated_contract_snapshot="$work_dir/contracts.d.ts"
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
