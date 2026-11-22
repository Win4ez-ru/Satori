# Satori

[![CI](https://github.com/Win4ez-ru/Satori/actions/workflows/ci.yml/badge.svg)](https://github.com/Win4ez-ru/Satori/actions/workflows/ci.yml)

Satori is a local-first personal assistant and persistent digital character. The
application owns its identity, memory, affect and relationships. Replaceable language
models generate replies and propose updates, which typed application services validate
and commit with provenance.

## Satori 26

- **Conversation and continuity.** The CLI and macOS desktop client share the Core, with
  persistent chat, source-grounded retrieval and metadata-only diagnostics.
- **Connected desktop.** The macOS client supports scoped EventKit Calendar reads,
  Gmail and Telegram connectors, and contextual read requests from chat. External
  actions retain separate review and permission checks.
- **Local tools.** The tree includes reviewed schedule import, reminders, voice
  adapters, selected-file and public-page readers, and macOS MapKit helpers.
- **Model providers.** Ollama is the default. OpenAI and Yandex AI Studio adapters are
  available for explicit configuration. The desktop OpenAI runtime has a reviewed
  pricing policy and local usage cap.

A live Calendar lookup passed through the ordinary macOS composer in the installed
26 build. See the [release status](docs/progress.md) for the scope of account, platform
and native checks. GitHub provides source archives. The public release has no signed
macOS installer.

## Run from source

Use Python 3.12 and [uv](https://docs.astral.sh/uv/). The desktop path targets
Apple Silicon macOS. For local model inference, install Ollama separately.

```bash
git clone https://github.com/Win4ez-ru/Satori.git
cd Satori
uv sync --frozen --no-editable --reinstall-package satori-core
uv run --no-sync alembic upgrade head
uv run --no-sync satori bootstrap
uv run --no-sync satori activate
uv run --no-sync satori status
```

With Ollama running:

```bash
ollama pull qwen3:4b-instruct
ollama pull embeddinggemma:300m
uv run --no-sync satori chat
```

For the macOS source client, install the desktop dependency group and open the app:

```bash
uv sync --frozen --all-groups --no-editable --reinstall-package satori-core
uv run --no-sync python -m satori.desktop
```

Select a local profile in the desktop client. Native integrations can require compiled
helpers, provider registration and OS permissions. See the
[desktop architecture and platform status](docs/desktop-roadmap.md).

## Develop

```bash
uv sync --frozen --all-groups --no-editable --reinstall-package satori-core
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync mypy src tests
QT_QPA_PLATFORM=offscreen uv run --no-sync pytest --ignore=tests/test_voice_engines.py
QT_QPA_PLATFORM=offscreen uv run --no-sync pytest tests/test_voice_engines.py
```

The automated checks run against synthetic data without cloud credentials. Live model
and account acceptance is tracked separately. See the [documentation index](docs/index.md)
for the Core boundaries, state model, architecture decisions and roadmap.
