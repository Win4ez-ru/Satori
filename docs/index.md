# Карта документации

Читайте только необходимый контекст, но всегда начинайте с `PROJECT_SATORI.md`, этого файла и `progress.md`.

| Файл | Назначение | Когда читать |
|---|---|---|
| `../PROJECT_SATORI.md` | Долговременная продуктовая конституция | Перед любой задачей |
| `../AGENTS.md` | Operational rules, проверки и stage gate | Перед любой задачей |
| `progress.md` | Текущий Stage/checkpoint, завершённое и ближайший разрешённый scope | Перед любой задачей |
| `vision.md` | Product experience, non-goals и v0.1 story | Продуктовые решения, UX, eval scenarios |
| `architecture.md` | Компоненты, зависимости, transactions, context, providers, security и export | Любое изменение системных boundaries |
| `state-model.md` | Семейства persistent state, ownership и mutation policy | Domain model, persistence, mutations |
| `personality.md` | Seed характера, values, стиль, независимость и evolution limits | Personality, prompts, behavior evals |
| `personality-evolution.md` | Stage 14 contract: evidence policy, drift budgets, checkpoints, restore and expression projection | Trait evolution, persistence, reflection routing and stability evals |
| `memory.md` | Типы памяти, provenance, retrieval, consolidation, forgetting и conflicts | Memory/retrieval/context задачи |
| `relationship.md` | Stage 8 axes, evidence, mutation caps, lifecycle and safety boundaries | Relationship state/appraisal/expression tasks |
| `models.md` | Stage 9 User/World Model ownership, vocabulary, validity, expiry and context | User/world claims, projections, persistence and evals |
| `positions.md` | Stage 11 Satori fact/belief/opinion/hypothesis ownership, evidence and lifecycle | Durable Satori positions, context and evals |
| `inclinations.md` | Stage 13 preference/interest ownership, affect-linked evidence, decay and context | Satori inclinations, independence, longitudinal evolution and evals |
| `reflection.md` | Stage 12 trigger, fixed evidence set, lifecycle, owner routing and cycle policy | Reflection implementation and acceptance |
| `cognition.md` | Пошаговые контракты interaction lifecycle | Conversation pipeline, LLM orchestration |
| `evaluation.md` | Behavioral eval design, metrics и release gates | До и после behavioral changes |
| `performance/stage-7.7.md` | Stage 7.7 benchmark method, distributions, hardware and model evidence | Inference/runtime optimization |
| `performance/stage-8.md` | Stage 8 foreground/background distributions and multi-session relationship evidence | Relationship performance/regression review |
| `performance/stage-8.1.md` | Stage 8.1 sampled replies, semantic review, retry/token/latency evidence and residual limits | Dialogue-coherence acceptance/regression review |
| `performance/stage-10.md` | Stage 10 planning distributions, live trace inspection and quality evidence | Structured cognition acceptance/regression review |
| `performance/stage-14.md` | Stage 14 deterministic evolution/stability gates, anchor A/B/restore and real-Ollama evidence | Personality evolution acceptance/regression review |
| `performance/stage-14.1.md` | Checkpoint 14.1 cross-provider foreground A/B, tariffs, quality and state evidence | Provider-portability acceptance/regression review |
| `performance/stage-14.2.md` | Checkpoint 14.2 grounded absence, natural affect, character-expression candidates and dialogue evidence | Dialogue calibration, raw sampled-reply review and residual hallucination limits |
| `performance/stage-14.3.md` | Checkpoint 14.3 request-local Character Agency Kernel, offline causal gate and future paired A/B | Character agency architecture, provider-fit diagnosis and Stage 15 gate |
| `provider-portability.md` | Checkpoint 14.1 cloud-provider scope, secure configuration, A/B gate and deferred routing | Provider/model replacement work |
| `threat-model.md` | Failure modes, mitigations, detection и stage gates | Security, reliability, state mutation |
| `roadmap.md` | Scope и exit condition каждого Stage | Планирование и проверка границ |
| `open-questions.md` | Нерешённые вопросы и сроки решений | Перед Stage gate или новым ADR |
| `decisions/README.md` | Реестр ADR и правила их изменения | Перед архитектурным решением |

## Правило изменений

- Изменение product invariant обновляет `PROJECT_SATORI.md` и требует явного согласования.
- Изменение принятого архитектурного решения создаёт новый ADR, который supersedes старый; историю не переписывать.
- Изменение state/cognition/memory contract обновляет соответствующий документ, eval plan и при необходимости threat model.
- Завершение deliverable отмечается в `progress.md`, но следующий Stage не открывается без отдельной команды пользователя.
