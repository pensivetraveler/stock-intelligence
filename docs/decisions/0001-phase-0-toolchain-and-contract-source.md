# ADR 0001 — Phase 0 toolchain and canonical contract source

- **Status:** accepted
- **Date:** 2026-10-12

## Context

Phase 0 needs one reproducible Python environment, one JavaScript workspace, cross-language validation, and a future Tauri desktop boundary without adding database or provider infrastructure early. The inspected machine provides Python 3.10.19 with uv 0.12.23 and Node 22.23.2 with pnpm 11.25.0. Rust is not installed.

## Decision

- Use one root `pyproject.toml` and `uv.lock` for the FastAPI service, Collector and Python contract tests.
- Use one pnpm workspace and `pnpm-lock.yaml` for the React desktop shell and TypeScript contract package.
- Keep JSON Schema Draft 2020-12 in `packages/contracts/schemas/contracts.schema.json` as the canonical language-neutral contract.
- Generate TypeScript declarations from that schema and validate the same fixture corpus in Python and TypeScript. Cross-field rules use matching semantic validators tested by the shared fixtures.
- Keep the Collector as an independent Python module/CLI. Do not add SQLite, Parquet, migrations, schedulers or live adapters in Phase 0.
- Check in a Tauri 2 shell configuration, but report native execution as blocked until Rust/Cargo and platform prerequisites are installed and verified.

## Alternatives

- Separate Python projects per service: stronger dependency isolation, but unnecessary duplication before service dependencies diverge.
- npm or yarn instead of pnpm: viable, but pnpm is already available and provides strict workspace dependency resolution.
- Hand-written Pydantic and TypeScript models: rejected because definitions can drift from the canonical schema.
- OpenAPI as the only contract source: insufficient for non-HTTP records and the standalone Collector boundary.

## Trade-offs

The root Python environment is simple now but may need service-specific packages when deployment boundaries diverge. Generated TypeScript declarations improve drift detection, while semantic rules still require two small implementations whose shared fixtures must remain mandatory. Tauri configuration is present without claiming a native build passed.

## Evidence/verification

Phase 0 quality gates validate schema metaschema, valid/invalid fixtures, Python/TypeScript semantic behavior, API contracts, Collector lifecycle and React connection states. Lock files pin resolved dependencies.

## Revisit when

Service dependencies or deployments require independent Python lock files; schema generation cannot express required types; a database migration system is introduced; or Tauri packaging targets are finalized.
