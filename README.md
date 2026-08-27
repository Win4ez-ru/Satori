# SATORI

SATORI — local-first система долгоживущего цифрового персонажа с устойчивой идентичностью, памятью, собственной позицией и объяснимым развитием. LLM в этой системе заменяема и не владеет личностью персонажа.

**Stage 14 — Personality Evolution: реализация и оба acceptance gate завершены. Stage 15 остаётся заблокирован.**
Активированная Сатори поддерживает long-lived CLI chat, bounded immediate
continuity, source-grounded memory, continuous bounded affect/mood, trusted runtime self-model и
отдельное медленное counterparty-specific relationship state. Relationship формируется
только из canonical user evidence после доставки ответа, ограничено
maturity/event/session policy и не создаёт любовь, зависимость,
эксклюзивность или послушание. Qwen/Ollama остаются заменяемыми
компонентами, а не её identity. Stage 9 отдельно хранит минимальные evidence-typed сведения о
собеседнике и текущих проектах/ситуациях: provider только предлагает claims, а два domain owner
валидируют provenance, epistemic kind, confidence, correction, lifecycle и expiry.
Stage 10 добавляет поверх существующего conversation lifecycle transient typed pipeline:
perception, weighted need mix, retrieval plan, affect-appraisal handoff, недолговечную внутреннюю
позицию, intent и response strategy. Он не создаёт второй foreground LLM-вызов, новый state owner
или скрытый chain-of-thought. Stage 11 добавляет identity-global evidence-linked
позиции Сатори: provider только предлагает belief/opinion/hypothesis, а
`PositionManager` детерминированно проверяет provenance, anti-mirroring thresholds,
confidence, revision и competition. Факты без independently verified source не создаются.
Stage 12 реализует зафиксированный ADR-0025 fixed-source reflection: редкий deterministic
trigger, hard cost caps, strict proposals и per-proposal target-owner decisions без
personality/value mutation или 24/7 inner monologue. В V1 только `PositionManager` может принять
изменение; остальные известные owners получают явный audited rejection.
Stage 13 по ADR-0026 добавляет отдельный identity-global aggregate интересов и сравнительных
предпочтений: только Reflection V2 с проверенной привязкой к уже committed affect может предложить
семантическую тему, а `PositionManager` самостоятельно применяет diversity, anti-mirroring,
bounds, cooldown, rolling budget, stability и pure decay. Inclinations не становятся evidence для
affect, memory, relationship или самих себя и влияют только на релевантный текущий ответ.
Stage 14 по ADR-0027 вводит отдельный personality-purpose Reflection V3 и единственного
`PersonalityManager`: только независимая canonical evidence-выборка минимум за 90 дней может
предложить exact trait/direction, а owner либо применяет ровно `±0.005`, либо reject. Endpoint и
cumulative path drift ограничены относительно activation и explicit approved checkpoint; каждый
delta имеет immutable checkpoint и append-only restore. Values и Stage 15 остаются закрыты.

## С чего начать

- [PROJECT_SATORI.md](PROJECT_SATORI.md) — продуктовая конституция.
- [AGENTS.md](AGENTS.md) — обязательные правила работы.
- [docs/index.md](docs/index.md) — карта документации.
- [docs/progress.md](docs/progress.md) — текущее состояние проекта.
- [docs/roadmap.md](docs/roadmap.md) — границы и exit conditions всех этапов.

## Что должна доказать v0.1

После закрытия и нового запуска Сатори остаётся тем же персонажем: сохраняет identity и personality, вспоминает релевантное с provenance, учитывает отношения и эмоциональный контекст, не выдумывает отсутствующее и не копирует позицию пользователя автоматически.

## Toolchain

Требования: Python 3.12+ и `uv`. Primary environment — macOS Apple Silicon; core не использует macOS-specific API и должен оставаться Linux-portable.

```bash
uv sync --frozen --all-groups --no-editable --reinstall-package satori-core
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync mypy src tests
uv run --no-sync pytest
```

Runtime-настройки имеют безопасные development defaults и могут быть переопределены через `SATORI_*` environment variables. Полный пример, включая conversation provider/model/timeout/bounds, находится в `.env.example`; секретов там нет.

## Persistence, bootstrap and activation

```bash
uv run --no-sync alembic upgrade head
uv run --no-sync satori bootstrap
uv run --no-sync satori status
uv run --no-sync satori activate
```

`bootstrap` и migrations только подготавливают SQLite и никогда не создают Сатори. До явной activation `status` выводит `Satori: not activated`. Первая команда `activate` атомарно создаёт одну identity, personality, values, seed provenance и audit event; повторная команда сообщает safe no-op и не применяет seed заново.

Для ручной проверки в отдельной БД:

```bash
SATORI_DATABASE_URL=sqlite+pysqlite:////absolute/path/satori-stage2.db uv run --no-sync satori status
SATORI_DATABASE_URL=sqlite+pysqlite:////absolute/path/satori-stage2.db uv run --no-sync satori activate
SATORI_DATABASE_URL=sqlite+pysqlite:////absolute/path/satori-stage2.db uv run --no-sync satori status
```

После activation authoritative state находится в нормализованных DB-таблицах. Versioned JSON seed — только валидируемый initial input; изменение файла не сбрасывает уже живущую Сатори.

## Persistent conversation, memory and affect

Conversation generation, structured episode/semantic formation и embeddings используют отдельные
capability ports. Defaults остаются полностью локальными: Ollama с `qwen3:4b-instruct` и
`embeddinggemma:300m`. Checkpoint 14.1 позволяет явно выбрать Yandex AI Studio, а ADR-0031 —
OpenAI Responses только для foreground conversation; все background/owner capabilities остаются
Ollama. Для reasoning-enabled OpenAI видимый лимит ответа отделён от ограниченного
provider-local reasoning allowance. Секретная настройка, privacy boundary и A/B gate описаны в
[`docs/provider-portability.md`](docs/provider-portability.md).
Ollama и модели устанавливаются отдельно, проект не скачивает их при install/test.

С запущенным Ollama и загруженной моделью:

```bash
ollama pull qwen3:4b-instruct
ollama pull embeddinggemma:300m
uv run --no-sync satori talk "Привет, Сатори"
```

Для обычного живого диалога из корня проекта:

```bash
uv run --no-sync satori chat
uv run --no-sync satori chat --debug
uv run --no-sync satori chat --session SESSION_ID
```

`chat` держит один process/runtime и одну explicit session. Команды `/help`, `/status`, `/new`,
`/exit` и `/quit` распознаются только как полная CLI-строка. EOF и `Ctrl+C` завершают session
аккуратно. `/new` переносит синхронные database transition в worker thread, чтобы ожидание SQLite
не останавливало event loop и завершение уже запущенной фоновой обработки. Обычные human-readable
сообщения интерактивного режима выводятся по-русски, а
стабильные debug labels и metadata fields остаются техническими. Structured metadata-only logs
пишутся в `SATORI_CHAT_LOG_PATH`, а `--debug` показывает phase/Ollama timings без prompt/context
dumps.
`/help` печатает краткое назначение каждой команды; command-like текст внутри обычной реплики
по-прежнему передаётся Сатори как пользовательский текст.
До первой пользовательской реплики `chat` явно показывает выбранные foreground provider/model;
это делает cloud boundary заметной без вывода endpoint или credentials.
Для каждого успешного foreground-ответа `--debug` также показывает provider/model, finish status,
число provider attempts и token counts выбранной попытки. При bounded retry это не полный расход
обоих вызовов; стоимость по изменяемому внешнему тарифу также не вычисляется.
`/status` также показывает фактически выбранные foreground provider/model без endpoint или
credential values и число фоновых задач памяти, которые находятся в очереди или уже выполняются.
Счётчики фоновой обработки относятся ко всему текущему запуску `satori chat`, поэтому после
`/new` они могут ещё учитывать работу или ошибки предыдущей сессии этого же процесса.
В обычном режиме временная недоступность foreground provider сообщается provider-neutral текстом;
точные provider/model и тип ошибки доступны только через `--debug`.
При Yandex или OpenAI foreground та же команда используется без изменений; provider выбирается
через `SATORI_CONVERSATION_PROVIDER`, а API key не передаётся аргументом командной строки.

Каждый `talk` без `--session` создаёт и закрывает implicit one-turn session. Для явного multi-turn container:

```bash
SESSION_ID="$(uv run --no-sync satori session start)"
uv run --no-sync satori talk --session "$SESSION_ID" --request-id turn-1 "Привет, Сатори"
uv run --no-sync satori talk --session "$SESSION_ID" --request-id turn-2 "Я впервые запустил проект"
uv run --no-sync satori session close "$SESSION_ID"
uv run --no-sync satori history --session "$SESSION_ID"
uv run --no-sync satori memories
uv run --no-sync satori memories index
uv run --no-sync satori memories rebuild
uv run --no-sync satori memories search "Помнишь мой первый запуск?"
uv run --no-sync satori semantic list
uv run --no-sync satori semantic inspect CLAIM_ID
uv run --no-sync satori semantic process
uv run --no-sync satori semantic process --memory MEMORY_ID
uv run --no-sync satori emotion status
uv run --no-sync satori emotion history --limit 20
uv run --no-sync satori models user list
uv run --no-sync satori models world list --all --counterparty COUNTERPARTY_ID
uv run --no-sync satori models world inspect CLAIM_ID --counterparty COUNTERPARTY_ID
uv run --no-sync satori models export --counterparty COUNTERPARTY_ID --output ./var/models-export.json
uv run --no-sync satori models process
uv run --no-sync satori models process --interaction INTERACTION_ID
uv run --no-sync satori positions list
uv run --no-sync satori positions inspect POSITION_ID
uv run --no-sync satori positions export --output ./var/positions-export.json
uv run --no-sync satori positions inclinations-list --as-of 2026-08-22T12:00:00+00:00
uv run --no-sync satori positions inclination-inspect INCLINATION_ID
uv run --no-sync satori positions inclination-export --output ./var/inclinations-export.json
uv run --no-sync satori positions process
uv run --no-sync satori positions process --interaction INTERACTION_ID
uv run --no-sync satori reflection list --limit 20
uv run --no-sync satori reflection inspect REFLECTION_RUN_ID
uv run --no-sync satori reflection inspect REFLECTION_RUN_ID --show-sources
uv run --no-sync satori reflection process
uv run --no-sync satori personality inspect
uv run --no-sync satori personality compare CHECKPOINT_ID
uv run --no-sync satori personality export --output ./var/personality-export.json
uv run --no-sync satori personality process
uv run --no-sync satori personality approve CHECKPOINT_ID --hash CHECKPOINT_HASH --expected-version VERSION --reason "reviewed anchor behavior"
uv run --no-sync satori personality restore CHECKPOINT_ID --hash CHECKPOINT_HASH --expected-version VERSION --reason "restore reviewed anchor"
```

`reflection process` выполняет только bounded explicit-local eligibility и не имеет `--force`.
Обычный `reflection inspect` показывает handles, hashes, attempts, proposals и owner outcomes без
исходных цитат. `--show-sources` является явным локальным opt-in и может вывести чувствительный
пользовательский текст. Цитаты выводятся как однострочные JSON strings, поэтому embedded newline
не может имитировать отдельную proposal/outcome строку; `reflection list --limit` принимает только
положительное число.

`personality process` запускает только bounded explicit-local eligibility Reflection V3 и также не
имеет `--force`. `personality inspect|compare|export` показывают typed vector, drift budgets,
checkpoint hashes и provenance IDs без source quotes или provider text. `approve` и `restore`
требуют точные checkpoint ID/hash, текущую aggregate version и явное локальное основание;
restore добавляет новую версию и audit, не удаляя историю и не возвращая потраченный path budget.

`--request-id` — caller-owned idempotency key; без него CLI генерирует новый. Replay completed request возвращает stored assistant reply без appraisal/generation/affect/post-processing side effects. Stage 5 формирует query только из current input, exact-scan выбирает совместимые prior episodes и передаёт их отдельным bounded/untrusted memory section. Полная session history модели не отправляется: explicit session даёт только последние canonical completed пары в отдельных turn/character bounds.

Generation request содержит trusted policy, compact trusted character projection,
отдельные explicitly untrusted episodic/semantic/current-model envelopes и untrusted current user
message.
Полный versioned runtime self-model, 15 traits, 9 values, capability truth и source-linked
expression strengths остаются application state. Context schema v16 сохраняет Stage 8.1
conversation calibration, Stage 9 bounded релевантный user/world model envelope, Stage 11
позиции и bounded-проекцию topic-relevant Stage 13 inclinations. Personality Expression
Projection V2 может добавить не более двух стабильных qualitative cues; numeric traits,
evidence, budgets и evolution history в provider context не входят.
Transient typed cognition strategy выбирает position/intent/response shape после weighted need mix
и existing affect handoff; релевантный interest может добавить не более `0.20` curiosity influence
без второго foreground LLM call или обязательного вопроса. Основной conversation mode и все
требуемые authoritative facets по-прежнему гарантируют, что смешанный вопрос не теряет identity,
memory, affect, embodiment, provider или relationship границы. Transient
`DialogueCoherenceContext` отмечает повтор,
коррекцию, шаблонное закрытие и текущую тему только в границах
сессии; он не является memory, preference или User Model. Behavior policy v9
делает встречный вопрос необязательным, не превращает policy в слоган и
отделяет физическую способность от интереса к опыту пользователя.

Ошибочная прошлая assistant-реплика остаётся continuity data, но не становится
фактом о Сатори. Перед commit narrow deterministic validator проверяет только десять
типизированных нарушений: changed-dialogue duplicate, routine reciprocal question
после коррекции, masculine self-reference, human/biological self claim, blanket affect/memory
denial, превращение текущего creator claim в факт, invented origin backstory, blanket
prompt/policy denial и activity-interest false negative. Одна причина может
запустить ровно один повторный provider call в том же interaction с тем же
tentative affect и evidence set; normal path по-прежнему использует один call. Это не
output rewrite и не judge LLM: только один validated/grounded draft попадает в
canonical history, а metadata-only `self_consistency_violation_detected` для violation,
не связанного с duplicate, не содержит prompt или candidate text. Declared past claim
обязан сослаться на supplied memory/semantic-claim ID;
retrieval outage не блокирует conversation.

После canonical reply/affect commit ответ сразу становится видимым. Отдельный post-response processor затем предлагает create/skip episode, индексирует его и запускает semantic consolidation. `MemoryManager` принимает memory только с bounded summary/importance/confidence и exact quote из user message; assistant output не является event evidence. Downstream failure не отменяет completed conversation и остаётся retryable через явный processor/backfill, а не через completed replay.

После episode/index attempt отдельный structured provider может предложить до четырёх semantic claims
из нового episode и bounded recent evidence window. `SemanticMemoryManager` разрешает только
зарегистрированные user-subject predicates, проверяет typed value/polarity, exact root-user
lineage, независимость inference, deterministic confidence, dedup/conflict/correction и только
затем атомарно пишет terminal decision, claim/evidence/revision/audit. Assistant output и
retrieved semantic repetition не являются evidence; valid output часто содержит zero claims.

Semantic recall не сканирует все claims и не имеет отдельного vector index: он выбирает bounded
active claims только через evidence episodes, которые уже нашёл Stage 5, и передаёт их отдельным
untrusted JSON section. `semantic list` показывает active claims, `--all` — историю, `inspect` —
evidence/revisions, `process` — restartable backfill missing source/version decisions.

Перед generation current user event получает provider-neutral structured appraisal.
`EmotionManager` детерминированно проверяет source IDs/confidence, применяет
personality reactivity, per-event caps и bounds, а затем выводит малый mood impulse. Быстрое
affect и более медленный mood затухают лениво по exact half-life formula. Tentative state
влияет только на тон текущего ответа и коммитится атомарно с canonical reply;
generation failure или completed replay не создают второй мутации. Stage 8 хранит relationship
отдельно от affect: canonical reply показывается раньше background appraisal, поэтому slow state
влияет только на будущие turns. Provider предлагает категории, а `RelationshipManager` применяет
maturity ceilings, saturation и per-event/session caps. Retrieved memory, assistant output и replay
не становятся новым relationship evidence.

Developer read models:

```bash
uv run --no-sync satori relationship status
uv run --no-sync satori relationship history --limit 20
```

Normal chat получает только qualitative projection; numeric axes/IDs остаются в developer output.
Свежее/low-maturity relationship означает мало evidence, а не холод, недоверие или
неприязнь: friendly openness и curiosity остаются personality baseline, а mature/damaged
состояние лишь мягко модулирует релевантную интонацию. Persistent creator relation
ещё нет: текущее заявление пользователя можно атрибутировать только текущему
input, но не превращать в долгосрочный факт без future provenance/correction schema.
Полный контракт и safety boundaries описаны в [`docs/relationship.md`](docs/relationship.md).

Raw accepted user text, committed assistant text, episode summary/evidence quote и semantic typed
values/provenance хранятся как local SQLite plaintext без automatic expiry/redaction.
System/developer prompts и full provider request не записываются как dialogue; normal logs не
содержат message/reply/summary/semantic value/quote. Deletion/export/encryption UI отсутствует, и
система не считается production-ready для sensitive real-user data.

Основные настройки:

```text
SATORI_CONVERSATION_PROVIDER=ollama
SATORI_CONVERSATION_MODEL=qwen3:4b-instruct
SATORI_CONVERSATION_PROVIDER_BASE_URL=http://127.0.0.1:11434
SATORI_CONVERSATION_TIMEOUT_SECONDS=120
SATORI_CONVERSATION_TEMPERATURE=0.3
SATORI_EMBEDDING_PROVIDER=ollama
SATORI_EMBEDDING_MODEL=embeddinggemma:300m
SATORI_EMBEDDING_DIMENSIONS=768
SATORI_RETRIEVAL_MINIMUM_SIMILARITY=0.55
SATORI_RETRIEVAL_TOP_K=4
SATORI_RETRIEVAL_MAX_CONTEXT_CHARS=2400
SATORI_SEMANTIC_MAX_CLAIMS_PER_MEMORY=4
SATORI_SEMANTIC_MAX_SOURCE_MEMORIES=6
SATORI_SEMANTIC_RETRIEVAL_TOP_K=4
SATORI_SEMANTIC_RETRIEVAL_MAX_CONTEXT_CHARS=2000
SATORI_EPISODE_FORMATION_MODEL=qwen3:4b-instruct
SATORI_SEMANTIC_FORMATION_MODEL=qwen3:4b-instruct
SATORI_AFFECTIVE_APPRAISAL_MODEL=qwen3:4b-instruct
SATORI_AFFECTIVE_APPRAISAL_MAX_OUTPUT_TOKENS=96
SATORI_RELATIONSHIP_APPRAISAL_MODEL=qwen3:4b-instruct
SATORI_RELATIONSHIP_APPRAISAL_MAX_OUTPUT_TOKENS=64
SATORI_REFLECTION_PROVIDER=ollama
SATORI_REFLECTION_MODEL=qwen3:4b-instruct
SATORI_REFLECTION_PROVIDER_BASE_URL=http://127.0.0.1:11434
SATORI_REFLECTION_TIMEOUT_SECONDS=180
SATORI_REFLECTION_MAX_OUTPUT_TOKENS=768
SATORI_DEFAULT_COUNTERPARTY_ID=local-default
SATORI_RECENT_CONVERSATION_MAX_TURNS=8
SATORI_RECENT_CONVERSATION_MAX_CHARS=6000
SATORI_OLLAMA_KEEP_ALIVE=10m
SATORI_OLLAMA_SERIALIZE_INFERENCE=true
SATORI_OLLAMA_BACKGROUND_AGING_SECONDS=30
SATORI_OLLAMA_BACKGROUND_GRACE_SECONDS=2
SATORI_CHAT_LOG_PATH=./var/satori-runtime.jsonl
```

Metadata-only developer benchmarks use versioned scenarios and never write fixture text, prompts,
retrieved context or replies to the result artifact:

```bash
uv run --no-sync satori benchmark inference --repetitions 5 --output /tmp/stage77.json
uv run --no-sync satori benchmark appraisal --model qwen3:4b-instruct --repetitions 3
uv run --no-sync satori benchmark contention --scheduled --repetitions 3
```

Target-Mac methodology, cold/warm distributions, model comparison and known limits are recorded in
[`docs/performance/stage-7.7.md`](docs/performance/stage-7.7.md).

Normal tests используют deterministic fake/HTTP fixtures. Optional real smoke:

```bash
SATORI_RUN_OLLAMA_INTEGRATION=1 uv run --no-sync pytest
```

Quality run сначала принудительно пересобирает non-editable `satori-core`, после чего команды `--no-sync` проверяют неизменное окружение. Так workflow не зависит от platform-specific обработки editable `.pth`, не использует stale local wheel и проверяет фактически собранный artifact.

## Repository structure

```text
src/satori/
├── application/       # conversation plus memory/affect/relationship/model/position/personality orchestration
├── core/              # portable primitives and provider-neutral conversation contracts
├── domain/            # typed state and single-owner memory/affect/model/position/personality policies
├── infrastructure/    # SQLAlchemy, seed and Ollama adapters
├── observability/     # structured logging and trace context
├── resources/seeds/   # canonical versioned Satori seed
├── bootstrap.py       # migration/connectivity check; never activates
├── config.py          # typed SATORI_* settings
└── __main__.py        # full local CLI, including Stage 14 inspection and recovery

migrations/            # guarded schema history through 0012 personality evolution
tests/                 # quality, transaction, restart, provenance and boundary enforcement
```

Архитектура и решения подробно описаны в `docs/`.
