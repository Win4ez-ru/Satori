# ADR-0009: Stage 1 toolchain and package layout

- Status: Accepted
- Date: 2026-07-27

## Context

Foundation needs one reproducible workflow and a package layout that preserves Satori Core as the product rather than an HTTP application. The primary machine is macOS Apple Silicon, while future local deployments must remain portable to ordinary Linux. The project is private.

## Decision

Use Python 3.12 as the minimum version, `uv` with `pyproject.toml` and committed `uv.lock`, and a `src/satori` package layout. A quality run force-reinstalls a non-editable `satori-core`, then uses `uv run --no-sync`; package verification therefore depends on neither platform-specific editable `.pth` behavior nor a stale local wheel. macOS Apple Silicon is the primary development environment, but core/application layers use no macOS-specific API. Do not add an open-source license. FastAPI is intentionally absent from Stage 1 because no HTTP use case exists yet.

The physical dependency direction is:

```text
CLI/composition root → infrastructure/application → core
```

`core` and `application` may not import SQLAlchemy, Alembic, FastAPI, Ollama or concrete provider SDKs. A lightweight AST test enforces obvious violations.

## Consequences

Clean environments install from one lockfile and tests run on the declared Python baseline. The package can later be used by CLI, HTTP, voice or native adapters without making FastAPI its center. Linux portability is an explicit review requirement. Contributors need `uv`; private distribution and deployment mechanics remain deferred.

## Alternatives rejected

Generic `app/` layout would conflate core and interface. Poetry or ad-hoc `pip` requirements would create a second workflow without benefit. Adding FastAPI for a health endpoint would introduce an unused interface dependency. An open-source license cannot be inferred for a private project.
