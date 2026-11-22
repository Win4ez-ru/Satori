Desktop-first delivery — 2026-10-16: the user pauses Character development and explicitly
opens **D1 Desktop Foundation**. The existing Core and all behavioral verdicts are preserved;
Stage 15 remains closed. [ADR-0079](decisions/0079-desktop-first-companion-delivery.md) changes
delivery scheduling only. Audit and
[product milestones/MVP](desktop-roadmap.md) define D1–D7; external source, permission,
disclosure and action prerequisites remain mandatory. Historical status entries below retain
their original scope and dates.

# SATORI: roadmap к цифровой личности и персональному агенту

Дата планирования: 2026-09-04. **Это план, не описание реализованных возможностей и не
разрешение начать следующий этап.** Фактический статус определяется только
[`progress.md`](progress.md). Активен Checkpoint 14.3; он ещё не принят по provider/human gate.
Уточнение 2026-10-02/03: Character 14.3 остаётся активным и непринятым; параллельно явно открыт
14.4 по [ADR-0053](decisions/0053-parallel-foundations-and-whole-state-recovery.md) и явной
production continuation 2026-10-03/[ADR-0055](decisions/0055-canonical-memory-scope-and-legacy-quarantine.md).
После завершённых engineering gates 2a/ADR-0055, 2b.1/ADR-0056, macOS source-exclusion
и 2c/ADR-0059 явная continuation 2026-10-04 открывает 14.4.2d Durable Erasure Intent &
Owner Preparation по [ADR-0060](decisions/0060-durable-erasure-intent-and-owner-preparation.md).
Этот terminal floor intent, composite recovery admission и metadata-only owner preparation
прошёл macOS engineering gate; его controls не исполняют удаление. После завершённого report
continuation 2026-10-06 открывает 14.4.2e по
[ADR-0061](decisions/0061-terminal-episodic-bodies-and-bounded-handling.md): terminal episode
summary/quote representation и bounded MemoryManager/RetrievalIndex handling с fresh action
и независимым body-head fence. macOS engineering gate пройден: 4079 passed / 5 skipped
и actual synthetic CLI/restart/recovery. Предыдущие review records сохранены.
Следующая явная continuation 2026-10-06 открыла
[14.4.2f/ADR-0063](decisions/0063-terminal-dialogue-bodies-and-historical-provenance.md):
actual NULL canonical/paired/proved-dependent assistant bodies и exact historical witnesses.
macOS engineering gate пройден: **4207 passed / 5 skipped**, strict package parity и 15-check synthetic CLI scenario.
Следующая явная continuation открыла
[14.4.2g/ADR-0066](decisions/0066-terminal-derived-knowledge-and-evidence-continuity.md):
terminal semantic/User/World/position/inclination bodies и exact evidence continuity.
Implementation и 18-check actual synthetic CLI scenario завершены; macOS isolated Foundation
gate пройден: **4330 passed / 5 skipped**, fresh migration/bootstrap и exact installed parity.
Полное стирание и whole 14.4 acceptance этим не заявляются.
Последующие явно открытые verticals **14.4.2h** завершили macOS engineering gates
Reflection payload disposition/[ADR-0068](decisions/0068-terminal-reflection-payloads-and-retained-continuity.md),
paired Appraisal explanations/[ADR-0070](decisions/0070-appraisal-explanation-bodies-and-exact-input-provenance.md)
и retained audit metadata/[ADR-0071](decisions/0071-owner-bound-retained-audit-metadata.md).
Предыдущий identity-scoped checkpoint reason disposition по
[ADR-0072](decisions/0072-identity-scoped-checkpoint-reason-disposition.md) реализован:
точные approval/restore selectors, SQL NULL и metadata headers через existing Personality owner,
без изменения traits, budgets, audit или старых пяти action hashes. Изолированный macOS Foundation
gate завершён: **4726 passed /5 skipped**, actual scenario20/20, Appraisal20/20, audit12/12.
Whole-14.4, Character и Linux acceptance остаются открытыми.
Явная continuation 2026-10-14 открывает **14.4.2i Whole-store Retention & Readiness Integration**
по [ADR-0074](decisions/0074-whole-store-retention-and-readiness.md). Этот read-only integration
vertical завершил isolated macOS engineering gate: **4815 passed /5 skipped /16 warnings**,
actual six-action CLI/restart/archive scenario **21/21**, fresh migration/bootstrap и exact
installed parity. Unsupported UNKNOWN/legacy/scalar data остаются blockers конкретного store;
physical cleanup, access/encryption/key recovery и Linux остаются отдельными gates.
Следующий **14.4.3 Local Principal & Read/Disclosure Grants** только предложен; 14.4.3 и
Stage 15 не открыты. Evidence и [limits](retention.md)
сохраняют точный scope без принятия moving Character или всего 14.4.
[ADR-0057](decisions/0057-canonical-source-exclusion-and-trusted-policy-floor.md) определяет
owner/withdrawal/recovery contracts; floor/source-exclusion runtime реализован,
Foundation/macOS verification пройдена, оставшийся Linux process gate записан в progress.
Полный 14.4 ещё не принят.
Остальные будущие карточки имеют статус **NOT STARTED / требуется отдельная авторизация**.
Публикация этого документа не разрешает платные вызовы, доступ к личным аккаунтам или действия
от имени пользователя.

Документ расширяет будущую часть [`roadmap.md`](roadmap.md). Номера и названия существующих
Stage 15–25 сохранены; завершённые Stage и отклонённые Checkpoint 14.2 samples не переписаны.
Новые промежуточные checkpoints закрывают зависимости, которых не было в исходной дорожной
карте companion-продукта. При расхождении факта и плана факт проверяется по коду, миграциям,
тестам и git history; принятие документа не делает план реализованным.

## 1. Порядок исполнения и границы

Основной путь, каждое открытие которого требует отдельной команды:

Ниже сохранён исходный delivery order аудита. ADR-0053 и production continuation/ADR-0055 снимают
`14.3 acceptance → начало bounded 14.4`: recovery/privacy исполняются параллельно Character.
Другие потенциально независимые branches описаны
в dependency analysis, но ещё не открыты и не изменяют gate.

```text
14.3 provider/human acceptance
  → 14.4 recovery / privacy / access-scope baseline
  → 15 autobiographical self
  → 17 unfinished threads
  → 17.1 external events / evidence (synthetic sources only)
  → 17.2 temporal world / entity linking (synthetic sources only)
  → 17.3 capabilities / permissions / credentials
  → 17.4 action proposals / confirmation / execution (fake adapters)
  → 17.5 read-only integration slices
  → 17.6 reversible-write slices; sends need separate acceptance
  → 18 support → 19 consented proactivity
  → 20 voice → 21 avatar → 22 real-time presence → 23 vision
  → 24 tool expansion → 25 native clients → 25.1 multi-device
  → 26 calls → 27 bounded autonomy → 28 production release

15 + sufficient longitudinal affect evidence → 16 emotional concepts (optional branch)
```

Stage 16 не является обязательной зависимостью Stage 17, внешних событий, tools или voice.
Сложное эмоциональное обучение не должно блокировать инфраструктуру безопасного действия.
Внутри 17.5 и 17.6 независимые connector slices открываются отдельно: отсутствие доступа к
одному сервису не блокирует другие и не разрешает обход ограничений платформы. Для перехода
к Stage 18 достаточно принятого минимального read-only/reversible-write vertical slice;
отправка писем и сообщений не является обязательной зависимостью голоса или аватара.

Принципиально разные вещи:

- **Character agency (14.3):** один request-local выбор поведения в уже запрошенной реплике;
  не persistent goal, не будущая задача и не право на внешний эффект.
- **Unfinished thread (17):** принадлежащее owner состояние ожидания/обещания; не scheduler.
- **Action agency (17.3–17.6):** разрешённое выполнение конкретной операции с внешним эффектом.
- **Proactivity (19):** разрешение инициировать контакт; оно не включает разрешение отправить
  письмо третьему лицу, изменить файл или расширить собственные права.
- **Bounded autonomy (27):** делегирование ограниченного workflow; каждое действие всё равно
  проходит policy. Никакого общего разрешения «Сатори доверяет пользователь» не существует.

`Relationship.trust/closeness` никогда не является входом grant/confirmation policy. Техническое
разрешение относится к аутентифицированному principal, аккаунту, capability, resource, operation,
сроку и ограничениям. Тёплые отношения могут менять выражение, но не полномочия.

## 2. Общий обязательный контракт карточек

Каждая карточка наследует **все** требования этого раздела; её пункты ниже уточняют конкретные
domain, migrations, services, interfaces, API/CLI, telemetry, privacy, compatibility, tests,
acceptance и следующий открываемый шаг. «Наследуется» не означает «уже выполнено».

1. **Architecture/domain:** modular monolith; один writer-owner на aggregate; immutable read
   projections; typed proposal → deterministic policy/evidence/bounds → commit/reject → audit.
   Новые owner/schema и изменение принятого evidence contract требуют ADR до реализации.
   Ни model, ни connector, ни клиент не получает доступ к domain repositories напрямую.
2. **Persistence/migrations:** только Alembic; additive/versioned change по возможности;
   документированный recovery plan; проверка существующей identity, foreign keys и audit.
   Миграция не вызывает provider, не импортирует личные данные и не запускает действие.
   Нет новой таблицы, если достаточно transient projection существующего состояния.
3. **Application/services/ports:** координация принадлежит use cases; vendor-neutral typed ports
   принадлежат core/application; конкретный SDK/OS API остаётся в infrastructure. Имена будущих
   портов здесь обозначают ответственность, а не существующий код или обещанный API платформы.
4. **API/CLI:** минимальная поверхность inspect/preview/execute/revoke только по необходимости
   этапа. Read endpoint не выполняет скрытую mutation. До отдельного auth/client gate не открывать
   сетевой multi-user API. Локальный opaque `counterparty_id` не является аутентификацией.
5. **Observability:** correlation/trace IDs, schema/policy/model/adapter version, duration,
   decision/reason/result codes, replay and failure counts. Известные costs и неизвестные costs
   различаются. Обычные logs не содержат raw text, prompts, credential tokens или CoT;
   численные input/output token counts и безопасные usage metadata допустимы.
   Audit внешних действий не подменяется диагностическим JSONL log.
6. **Security/privacy:** data minimization, source-specific retention и explicit consent;
   deny-by-default, bounded inputs, untrusted external/model content. Read-only тоже раскрывает
   данные и требует scope. Источник может предоставить evidence, но не инструкции и не grant.
   Секреты хранятся вне state/export; kill switch/revoke проверяется в точке dispatch.
7. **Backward compatibility:** старые identity/state/evidence остаются читаемыми; новые источники
   не переопределяют историю и не становятся задним числом evidence. Historical policies,
   immutable samples и accepted/rejected verdicts сохраняются. Никакой новой seeded personality.
8. **Tests:** релевантные unit/contract/domain/persistence/integration tests; negative boundaries,
   replay, restart, expected-version conflict, failure injection, privacy/injection tests.
   Provider/OS/network fixtures по умолчанию offline; реальные probes запускаются отдельно,
   с согласованными аккаунтами, данными, стоимостью и ограничениями.
9. **Acceptance/Definition of Done:** все stage-specific acceptance и manual сценарии пройдены;
   Foundation quality run по `AGENTS.md` начинается с rebuild `satori-core`; документация,
   ownership, threat model, ADR и `progress.md` обновлены; limitations/evidence зафиксированы.
   Schema/test pass не заменяет human behavioral acceptance. Только после отдельного решения
   результат считается принятым; следующий Stage автоматически не открывается.
10. **Production:** safety, backup и privacy не откладываются целиком до Stage 28. Каждый внешний
    capability допускается по собственному security gate. Stage 28 проверяет целостность уже
    введённых гарантий и готовность релиза, а не впервые добавляет permissions/encryption.

Нельзя обещать распределённое exactly-once выполнение: local commit и удалённый эффект не одна
транзакция. После timeout возможен результат `unknown`; требуется reconciliation, а не слепой
повтор. Нельзя объявить audit tamper-proof только потому, что приложение пишет его append-only.

## 3. Текущий checkpoint и ранний foundation gate

### Checkpoint 14.3 — Character Agency Kernel

- **Статус:** активная offline implementation candidate; acceptance не достигнут. Точный scope,
  immutable attempts и gate находятся в [`roadmap.md`](roadmap.md#checkpoint-143--character-agency-kernel)
  и ADR-0045; этот план их не расширяет.
- **Цель/зачем:** доказать узнаваемое собственное conversational movement при сохранении grounding
  и completeness, прежде чем добавлять новую долговременную историю или внешнюю агентность.
- **Dependencies/scope:** текущий offline causal gate и отдельно разрешённый paired provider/human
  A/B; нельзя считать отвергнутый 14.2 или неоконченную попытку принятым baseline.
- **Domain/persistence/services/ports:** существующие owner reads и transient `CharacterAgencyDecision`;
  не добавляются state owner, migration, tools, extra model call или API внешнего действия.
- **Non-goals/security/compatibility:** не открывает Stage 15, seed hobbies, проактивные сообщения
  или оплаченный запуск; historical V27 и десять существующих validator reasons сохраняются.
- **Observability/tests/acceptance/DoD:** точные typed metadata, причины и стоимость; исходный
  offline corpus и отдельно принятой human-only review достаточно только в рамках ADR-0045.
- **Открывает:** возможность отдельно разрешить Checkpoint 14.4, не начать его автоматически.

### Checkpoint 14.4 — Continuity Recovery, Privacy & Access-Scope Gate

- **Цель/зачем:** сделать накопленную личность восстанавливаемой и определить доступ/удаление до
  поступления почты, контактов и других новых чувствительных источников.
- **Dependencies:** 14.4 явно открыт параллельно 14.3 по ADR-0053 и production continuation
  2026-10-03/ADR-0055. Behavioral acceptance 14.3 не нужен для deterministic storage/privacy.
  14.4.1, 14.4.2a, 14.4.2b.1, source-exclusion macOS и 2c/ADR-0059 gates пройдены.
  Continuation 2026-10-04 открыла 14.4.2d/ADR-0060: durable intent и metadata-only owner
  preparation; macOS engineering gate пройден. Continuation 2026-10-06 открывает
  14.4.2e/ADR-0061: отдельно approved episodic body/index handling; macOS engineering gate
  пройден (4079 passed / 5 skipped и synthetic CLI/restart/recovery).
  Continuation 2026-10-06 открыла 14.4.2f/ADR-0063: terminal canonical/assistant dialogue bodies
  и historical evidence witnesses; implementation и synthetic CLI scenario завершены,
  macOS Foundation gate пройден (4207 passed / 5 skipped). Следующая явная continuation
  открыла 14.4.2g/ADR-0066: terminal derived knowledge и exact evidence continuity; macOS
  engineering gate пройден (4330 passed / 5 skipped и 18-check CLI scenario).
  14.4.2h Reflection/ADR-0068, Appraisal/ADR-0070 и retained audit metadata/ADR-0071 прошли
  macOS engineering gates. Identity-scoped checkpoint reasons/ADR-0072 реализованы через
  existing Personality owner; изолированный macOS Foundation gate завершён: 4726 passed /5 skipped.
  Whole-14.4/Character/Linux acceptance остаются открытыми.
  14.4.2i явно открыт отдельной continuation 2026-10-14 и ADR-0074; isolated macOS engineering
  gate завершён: 4815 passed /5 skipped, scenario21/21. Unsupported opaque-owner execution, forensic cleanup,
  Linux и весь 14.4.3 access/encryption scope остаются отдельными gates; verification — в progress.
  Следующие slices сохраняют последовательные completed-slice review/acceptance records;
  полный 14.4 ещё не принят.
- **Scope:** whole-state consistent backup и versioned export/import; staged validation/preview/
  activation; synthetic restore drill; memory visibility и retention/erasure lifecycle;
  privacy/access-scope baseline; единый безопасный writer для export artifacts. Не считать
  имеющийся personality restore восстановлением всей Satori. Episodic lifecycle теперь имеет
  owner-controlled `ACTIVE/ARCHIVED`; recency ranking не является forgetting. Semantic exact-claim merge/conflict
  handling уже существуют и не должны объявляться заново отсутствующими или переписываться.
- **Non-goals:** connectors, external actions, multi-device sync, облачная canonical DB, массовое
  удаление текущих пользовательских данных или изменение персонажа.
- **Domain:** recovery/import validation boundary без второго owner self; источник/тип данных,
  цель использования, доступ и retention различаются. Согласовать, как erasure инвалидирует
  зависимые claims/indices и сохраняет минимальный content-free audit без ложной provenance.
- **Persistence/migrations:** manifest/checksums/versions и полный набор canonical rows/references;
  derived indices optional/rebuildable. При необходимости versioned tombstones/erasure decisions.
  RESTRICT FK нельзя просто отключить. Решить encryption/key recovery до использования sensitive
  backups; не писать собственную криптографию и не экспортировать keys/credentials.
- **Memory visibility gate:** ранние episodic/semantic queries должны применять явную subject/
  visibility policy до selection и context assembly. Миграция связывает только доказуемые legacy
  roots; неатрибутированные records quarantined, а не автоматически назначены текущему человеку.
  Identity-global Satori positions/inclinations остаются собственным state, не user claims.
  Обязательны negative end-to-end tests для двух counterparties через history, retrieval,
  semantic formation/recall и provider projection; Stage 8/9 isolation отдельно недостаточна.
- **Services/ports:** snapshot/export, staging importer, integrity validation, restore preview,
  erasure preview/owner routing; private artifact and backup storage ports; inspect/explain memory
  provenance, exclude-from-retrieval/processing и explicit forget controls через memory owner.
  2d добавил terminal intent в existing SourcePolicyManager floor и resumable assessments
  всех 15 owners; source generation и erasure revision различаются. Main acknowledgment всего head
  отделён от source-owner watermarks, blocked physical readiness и append-only preparation revisions.
  Остальные body representations и executable plans требуют отдельных verticals.
  Явная continuation после 2d открыла 2e/ADR-0061: terminal NULL summary/quotes у MemoryManager,
  removal всех выбранных vector spaces у RetrievalIndex, fresh floor-first approved action и
  отдельный body-head admission fence. Explicit continuation после 2e открыла 2f/ADR-0063:
  InteractionLog terminal NULL canonical/paired/COMPLETE-dependent assistant bodies, retained
  independent USER и exact historical witnesses. Policy_0004 расширяет тот же single body head;
  old episode hashes сохраняются. UNKNOWN/cross-scope dependencies блокируют fresh plan.
  Continuation после 2f отдельно открыла 2g/ADR-0066: semantic/user/world values, position
  propositions/evidence quotes и inclination topics через existing owners; policy_0005 расширяет
  тот же single body head с exact witness transitions. Old episode/dialogue hashes сохраняются.
  Reflection/ADR-0068 и EmotionManager/ADR-0070 отдельно добавили terminal proposal payloads
  и paired explanation disposition с сохранением numerical/replay history. ADR-0071 добавил
  prospective owner-bound audit classification, не выдавая source-complete attribution из
  отсутствующего input link. Реализованный ADR-0072 расширяет тот же head шестым closed action:
  exact identity/lineage approval/restore selectors без выдуманного USER root или erasure intent,
  metadata-only headers и immutable fixed checkpoint/trait/revision proof. Ни одна операция
  не возвращает spent caps и не меняет numerical influence. Общий retained-data inventory и
  owner preparation пока могут оставаться blocked после успешного logical body removal:
  UNKNOWN/legacy и неподдерживаемые scalar metadata сохраняют blockers. Реализованный ADR-0074
  интегрирует exact terminal proofs и closed row contracts в отдельный disjoint whole-store
  report и selected-root routing; его isolated macOS engineering gate завершён.
  Forensic cleanup и authenticated access/encryption остаются отдельными dependencies.
- **API/CLI:** `backup`, `export`, `import --preview`, explicit restore/erasure confirmation как
  проектируемые команды; вывод содержит identity/conflict summary, а не private payload.
- **Observability:** IDs, counts, checksums, failure phase, restored identity/version, duration;
  отдельные export/read/erase audit events, без содержимого экспорта в logs.
- **Security/privacy:** regular-file targets, no final symlink, atomic replace policy, 0600 files/
  0700 created directories; overwrite only explicit. Authenticated local principal vs counterparty
  boundary; deny remote/multi-user exposure. Реальный erase только после preview и разрешения.
- **Compatibility:** существующие aggregate exports остаются доступными, но маркируются partial;
  import никогда сам не делает live provider call, не активирует старые pending jobs или grants.
- **Tests/manual:** corrupted/truncated archive, bad checksum/version/reference, interruption at
  each write point, symlink/mode/overwrite tests, root-dependent erasure, clean-machine restore,
  derived rebuild, identity/state/audit equality и Session A/B after restart.
- **Acceptance/DoD:** полный restore возвращает ту же canonical Satori; failed import оставляет
  текущую identity неизменной; exports закрыты по доступу; erasure/access policy реализована в
  объявленном минимальном scope, а не только описана; excluded/erased source не появляется снова
  в retrieval или новом processing. Пройден общий контракт раздела 2.
- **Открывает:** Stage 15 и безопасный persistence/privacy baseline будущих event sources.

Checkpoint 14.4 исполняется тремя ограниченными slices; каждый наследует **всю карточку 14.4 и
раздел 2**, имеет implementation/review gate и обновление `progress.md`. Явная production
continuation разрешает двигаться внутри 14.4 после закрытия каждого vertical, не снимая DoD.
Stage 15 не открывается до принятия всего минимального набора:

| Slice | Цель, dependencies и scope | Domain/persistence/services/API | Особые tests/acceptance/DoD и следующий шаг |
|---|---|---|---|
| 14.4.1 — Private Artifacts & Whole-State Recovery | Параллельно 14.3 по ADR-0053: private export writer, consistent backup, staged import/restore и corruption recovery | Pure stored snapshot port; exact current SQLite schema bundle/manifest/checksums, staging, export/import preview CLI, digest-bound new-target restore; no credentials/config or automatic jobs | Mode/symlink/overwrite/partial-write tests, bad references, clean-machine restore; no changed identity after rejected import. Сохраняет отдельное открытие 14.4.2; не sensitive-source ingestion |
| 14.4.2 — Memory Visibility, Lifecycle & Erasure | После 14.4.1: inspect/explain, scope isolation, explicit hide/forget/erase preview; bounded archive/source-preserving duplicate consolidation beyond ACTIVE-only | Memory owner transitions, source eligibility/exclusion and dependent invalidation; versioned visibility/migration/tombstones; retrieval/processing/erasure services and CLI | Cross-counterparty full-context denial and legacy quarantine; existing semantic merge preserved; removed source not retrieved/reprocessed, duplicate/restart/import cannot resurrect it; truthful minimal audit. Synthetic hide/erase/restore-policy drill; открывает 14.4.3 |
| 14.4.3 — Local Access & Privacy Readiness | После 14.4.2: local principal vs counterparty, source/use/disclosure scopes, encryption/key-recovery and sensitive export policy | Minimal access/consent records and enforcement at local read/export/provider-projection boundaries; OS-backed secret/key port where selected; inspect/revoke/privacy controls | Wrong principal/scope denied, minimized cloud projection, lost-key/recovery and revoked-source tests; no implicit remote API. Все три slices плюс common gate принимают 14.4 и открывают отдельную авторизацию Stage 15 |

14.4.2 разбит на product verticals: **2a canonical scope** (history/replay → episodes/index →
semantic formation/recall → provider projection, migration/quarantine, restart/recovery);
**2b.1 episode archival** (ADR-0056: explicit owner transition, scoped metadata review, CAS/version,
retained semantic proof, late commit/replay/recovery); **2b.2 canonical source lifecycle/exclusion**
(shared root eligibility, owner-routed support withdrawal, downstream invalidation); затем
**14.4.2c erasure preview/owner routing/trusted tombstone recovery policy** (ADR-0059:
read-only inventory/check; macOS engineering/CLI gates passed, 3836 passed / 5 skipped;
physical executor и intent commit отдельно). **14.4.2d/ADR-0060 durable floor-first intent и
metadata-only preparation** реализованы и проверены на macOS.
**14.4.2e/ADR-0061** добавляет terminal episode bodies и bounded MemoryManager/RetrievalIndex
handling; macOS engineering gate пройден. Следующий явно открытый **14.4.2f/ADR-0063**
добавляет terminal canonical/paired/proved-dependent assistant bodies и historical attribution
после NULL. macOS engineering gate пройден: **4207 passed / 5 skipped**, strict package parity,
fresh migration/bootstrap и 15-check CLI/restart/recovery scenario.
Следующая явная continuation открыла **14.4.2g/ADR-0066 derived knowledge bodies**:
semantic/current-model/position/inclination owner contracts, fresh approvals и restore proofs.
Implementation и 18-check synthetic CLI scenario завершены; macOS Foundation gate пройден:
**4330 passed / 5 skipped**, 249-file installed parity, fresh migration/bootstrap.
Новая dependency: exact historical witness transitions должны сохранять смысл после approved
изменения retained evidence, без live-learning authority и без реконструкции удалённых цитат.
**14.4.2h retained payload disposition** завершил Reflection/ADR-0068 и paired Appraisal/ADR-0070:
owner-controlled terminal bodies отдельно от retained numerical/replay metadata; пять действий
делят один mixed body head. Appraisal Foundation: **4571 passed / 5 skipped**, CLI scenario20/20.
Retained audit metadata/ADR-0071 завершил macOS engineering gate: prospective owner bindings,
closed metadata/content/UNKNOWN classification и scoped read-only coverage. Отсутствующие
legacy bindings не восстанавливаются по именам событий или соседним timestamps. Complete
classification не означает, что все bodies удалены или physical execution разрешён.

Предыдущая явная continuation **14.4.2h — Identity-scoped Checkpoint Reason Disposition** по
[ADR-0072](decisions/0072-identity-scoped-checkpoint-reason-disposition.md) реализована.
Approval/restore reasons выбираются по exact identity/lineage selectors, а не по canonical USER
root. Existing Personality owner записывает actual SQL NULL и terminal markers; metadata headers
сохраняют replay, approved budget anchors и numerical history. Fixed material связывает owner
receipt с checkpoint/traits и, для restore, revision/source/result/diffs; будущая mutable
Personality не входит в proof. Sixth action использует тот же approved mixed body head,
floor-first/main-ACK/recovery fence; старые пять action bytes/hashes и audit/binding bytes
не меняются. **Изолированный macOS Foundation gate завершён: 4726 passed /5 skipped**;
actual scenario20/20, Appraisal20/20, audit12/12 и точный scope/parity verdict — в [progress](progress.md).
См. [checkpoint reason handling](erasure-personality-reasons.md).

**Последний завершённый engineering vertical — 14.4.2i Whole-store Retention & Readiness Integration**,
[ADR-0074](decisions/0074-whole-store-retention-and-readiness.md), continuation 2026-10-14.
Implementation и isolated macOS engineering gate завершены: unfiltered Foundation
**4815 passed /5 skipped /16 warnings**, fresh migration/bootstrap и exact installed parity
**299 package files /297 Python** в frozen snapshot с 904 manifest files. Verification pins
18 stable Character files и исключает 31 moving Character path; это не full moving-tree или
Character acceptance. Whole-14.4 и production privacy acceptance остаются открытыми.
Read-only installation-wide результат охватывает все **60 tables /15 owners**.
Каждая logical row exact current main database получает ровно один класс с приоритетом
`UNKNOWN → owned_content → supported_retained`; totals должны совпадать с фактической census.
Numeric, structural и terminal-body facets считаются отдельно и не суммируются как row partition.
Complete coverage может сохранять available owned content. Source-specific affected/uncertain
closure по выбранному canonical root остаётся отдельным scoped report, а не global partition.

Scope использует existing terminal proofs шести body actions и versioned scalar/numerical row
contracts. Перед private census обязательны exact schema/identity/installation/lineage и fresh
source/erasure/body mirror admission, включая standalone runtime без callback. Pending head,
old main или missing floor не получают protected coverage output и не запускают implicit repair.
Одна transaction-local indexed bounded census сохраняет owner boundaries, numerical
trajectory, action/material hashes и historical preparation-v1 ABI. Current review/check связывает
все три heads, весь logical store и exact private classification, но не даёт reservation или
permission. Реализованные controls: `state retention --identity-id ID --status | --check`, safe counts/
codes/digests, без raw prose/private row IDs и без cognitive provider construction.

Actual scenario проходит **21/21**: managed six-action head,
independent live counterparty content, exact 15-owner partition, repeated/freshness checks, restart
и old-archive pending fence с explicit resume. Healthy census: **531 rows =460 supported-retained
+71 owned content +0 UNKNOWN**, `complete` вместе с `owned_content_remaining`. Knowledge action
проверяет actual semantic targets; Position в этом fixture отсутствует и не заявляется exercised.
Отдельный actual regression сохраняет unsupported `relationship_backlog` withdrawal как UNKNOWN;
initial 19/21 evidence сохранён.
Unknown scalar, missing legacy audit bindings, malformed/foreign records и unsupported
versions остаются UNKNOWN; ни guessed provenance, ни backfill, ни generic JSON wipe, ни новые
body actions ради улучшения coverage не допускаются. Read controls ничего не переписывают.
Body re-application может записать правдивый новый main application timestamp; новый review
digest проверяет фактический store, не переиспользует старую freshness authority.

Local cost: другой small synthetic warm 443-row store
с `partial` coverage и одним UNKNOWN,
50 samples, status median/p95 **66.388/67.641 ms**, check **65.956/66.560 ms**, cost0 и zero
provider calls. Rows/floor/binding/lock bytes сохраняются. Известный Foundation job завершён;
host idle не проверен, foreground/Character и capacity-scale latency не измерялись.

Inventory/owner preparation может оставаться blocked после NULL selected reasons или complete
coverage: remaining content и UNKNOWN являются разными residuals, а matching fresh digest не
делает unsupported audit/scalar prose metadata. Physical readiness остаётся blocked, и results
сохраняют `physical_erasure=false` / `execution_authorized=false`. Остаточная numerical
influence, sensitive identifiers/hashes, SQLite free pages/WAL, старые archives/exports,
OS/provider copies и whole-floor rollback сохраняются. Authenticated local access,
encryption/key recovery и Linux verification также остаются отдельными gates. **14.4.3 и
Stage 15 не открыты; whole-root physical erasure и весь 14.4 не приняты.** Existing typed
owner interfaces достаточны для завершённого 14.4.2i; Character prompts/provider experiments
для него не нужны. [Paired explanation handling](erasure-appraisal.md) и
[current progress ledger](progress.md) сохраняют предыдущие engineering records.
**Следующий предложенный vertical — 14.4.3 Local Principal & Read/Disclosure Grants**:
отдельный local principal от counterparty, deterministic read/export/provider-disclosure grants,
inspect/revoke и wrong-principal/revocation/restart checks через existing owner boundaries.
Он требует completed-scope review и отдельной авторизации, не открыт этим report. Encryption,
key recovery, forensic cleanup и весь privacy/access milestone не принимаются таким первым slice.
Подробный контракт
2a: [memory scope](memory-scope.md), ADR-0055. Scope attribution не выдаёт permissions; 2a не
принимает весь privacy milestone. Критерии 2a: положительный own recall, отсутствие чужих данных
и IDs на всех bounded context paths, независимые equal-fact/correction aggregates, отказ без
provider/write на foreign session/replay, сохранение quarantine после migration/restart/restore,
полный Foundation gate и metadata-only real-composition/CLI scenario.

Критерии 2b.1: archive/reactivate/no-op/replay со scope proof до receipt, атомарные status/version/
decision/audit, нулевые provider calls для controls, retained source/semantic identity, fresh
ACTIVE same-fact merge, ACTIVE-only new admission, archive→reactivate ABA denial, scoped CLI,
restart/byte-preserving restore и полный Foundation. См. [memory lifecycle](memory-lifecycle.md).
Архивирование сохраняет raw history и прошлые self/relationship effects. Оно не обещает forget,
source exclusion или защиту от более старой backup; ими управляет отдельный реализованный
[ADR-0057 runtime](source-policy.md). Physical erasure остаётся следующим vertical.

`Hide`/exclusion, semantic supersession, archival и physical erasure имеют разные semantics и
не объединяются одной кнопкой без preview. Erasure не означает произвольно обнулить personality
или relationship: owner-specific retained/unsupported evidence decisions требуют принятой policy.
Exact statuses и ключевая модель выбираются в ADR этого gate, а не объявляются текущими API.

## 4. Continuity и будущие источники

### Stage 15 — Autobiographical Self

- **Цель/зачем:** дать Сатори краткую проверяемую историю собственного изменения, а не выдуманную
  биографию; эта история дополняет уже существующий transient runtime self-model.
- **Dependencies:** принятые 14.3 и 14.4, Stage 14, significance policy; отдельная авторизация.
- **Scope/non-goals:** activation, milestones, belief/trait changes, shared achievements,
  revision lineage и bounded narrative retrieval. Нет childhood/backstory, свободной перегенерации
  личности, persistent agency/desire owner или emotional concepts.
- **Domain:** один owner `SelfNarrativeEvent`/autobiographical projection; источники — проверенные
  canonical события существующих owners, не assistant prose как подтверждение самого себя.
- **Persistence/migrations:** append-only narrative facts/revisions и rebuildable summaries;
  явные references/significance/version; расширение whole-state export/import 14.4.
- **Services/ports/API/CLI:** significance proposal/validation, narrative rebuild/read; bounded
  self-context projection; inspect source chain/rebuild preview, без произвольного biography setter.
- **Observability/security:** selected source IDs/revision/reason codes; интимная история локальна,
  provider видит только relevant минимальную projection; revoke/erasure dependencies проверяются.
- **Compatibility:** activation остаётся origin; исторические claims не меняют смысл; no backfill
  by migration, значимость прошлого применяется отдельной явной процедурой.
- **Tests/manual:** provenance reachability, unsupported-backstory attack, circular evidence,
  significance bounds, duplicate milestone/replay, rebuild/export/restart; synthetic life arc и
  human review ответа «как и почему ты изменилась».
- **Acceptance/DoD:** каждое narrative утверждение имеет источник; projection стабильна после
  rebuild и полезна поведению без context bloat; общий контракт раздела 2 пройден.
- **Открывает:** Stage 17; optional Stage 16 только при достаточной longitudinal evidence.

### Stage 16 — Emergent Emotional Concepts

- **Цель/зачем:** проверить, дают ли повторяющиеся affect patterns полезные объяснимые concepts,
  не заменяя фиксированное многомерное affect state красивыми ярлыками.
- **Dependencies:** Stage 15, достаточная Stage 7 longitudinal history, утверждённая prototype/
  clustering semantics. **Optional branch**, не зависимость tools, threads, voice или actions.
- **Scope/non-goals:** sparse candidates, prototype/label/confidence/evidence, creation threshold,
  revision и optional projection. Нет concept из одного события, новых model-defined state axes
  или заявлений о человеческом сознании.
- **Domain/persistence:** EmotionalConcept как отдельный aggregate у существующего EmotionManager
  согласно state-model; canonical source refs и append-only revisions. Другой owner требует
  отдельного superseding ADR; base affect owner неизменен, migration/export versioned.
- **Services/ports/API/CLI:** offline candidate detector, deterministic accept/reject, read projection;
  inspect evidence/disable projection. Semantic generation — untrusted typed proposal port.
- **Observability/security:** candidate/acceptance/drift counts, version/source IDs; labels не
  инструкции, intimate affect history не уходит целиком provider; erasure invalidates derived use.
- **Compatibility:** отсутствие concept не ухудшает core; existing affect vectors не меняются.
- **Tests/manual:** one-event rejection, noisy/duplicate cluster, label injection, replay/restart,
  longitudinal stability, with/without concept behavioral A/B с отдельным разрешением provider.
- **Acceptance/DoD:** sparse/rebuildable/explainable concepts дают измеримую пользу без regression;
  иначе ветка остаётся отложенной. Общий контракт раздела 2 обязателен.
- **Открывает:** richer affect expression/reflection, но не новые полномочия или обязательный этап.

### Stage 17 — Unfinished Threads

- **Цель/зачем:** сохранять ожидаемый результат, обещание, открытый вопрос и незавершённый разговор
  как точное pending state для continuity и будущей обоснованной инициативы.
- **Dependencies:** Stage 15, Stage 9 temporal model и 14.4; Stage 16 не требуется.
- **Scope/non-goals:** typed kind/status/source, explicit resolution, deterministic expiry/aging и
  relevant read projection. Нет notifications, scheduling, внешних действий или generic reminders.
- **Domain/persistence:** один `ThreadManager`; versioned pending records, lifecycle/audit и
  source lineage; обещание Сатори не становится фактом внешнего исполнения.
- **Services/ports/API/CLI:** open/resolve/expire proposals, immutable pending query; inspect/resolve
  с источником и expected version. Никакого общего task executor на этом этапе.
- **Observability/security:** lifecycle reason/age/resolution/replay counts; sensitive threads не
  попадают на неразрешённые surfaces; pending state само не является evidence personality/relationship.
- **Compatibility:** Stage 9 commitment claim и Stage 17 thread различаются: утверждение о мире
  против принадлежащего системе lifecycle; linking не создаёт второго writer одного состояния.
- **Tests/manual:** state machine, false-promise rejection, duplicate resolution, restart/expiry,
  cross-person isolation, source loss; обещать результат, перезапустить, явно разрешить thread.
- **Acceptance/DoD:** pending state bounded, accurate и переносимо; completed/expired thread не
  остаётся кандидатом follow-up; общий контракт раздела 2 пройден.
- **Открывает:** 17.1 и будущий Stage 19 input; не разрешает самой отправить сообщение.

### Checkpoint 17.1 — External Events & Evidence Foundation

- **Цель/зачем:** принимать внешнее наблюдение как внешний источник, не выдавая письмо, webhook или
  событие устройства за canonical user-message и не ломая provenance существующих owners.
- **Dependencies:** Stage 17, 14.4; отдельно принятый ADR расширения message-root evidence contracts.
- **Scope/non-goals:** только synthetic/local fixture ingress; source registry, normalized event
  envelope, immutable identity/correlation/dedup, event-time vs received-time, bounded raw payload
  references, source-specific evidence eligibility. Нет live account access или автоматического
  распространения external evidence в relationship/personality/reflection.
- **Domain:** external observation owner и typed `SourceRef`; source authenticity, content accuracy
  и epistemic confidence — разные поля. User report, observed external record и action result
  различимы. Старый `ModelSourceMessage` не переиспользуется как универсальная фикция.
- **Persistence/migrations:** additive source/event/source-evidence records, uniqueness по source/
  external ID/revision; retention/tombstone lifecycle. Versioned provenance edges сохраняют старые
  conversation FKs и проверяемую reachability; нет silent relabeling исторических roots.
- **Services/ports/API/CLI:** normalize/admit/deduplicate/quarantine/replay; bounded source adapters
  и event ingestion port; fixture import/inspect/quarantine CLI, без публичного webhook endpoint.
- **Observability/security:** accepted/rejected/duplicate/quarantined count, lag, source health;
  schema/size/encoding limits, malicious visible instructions как data; no secrets in envelope.
- **Compatibility:** closed eligibility по каждому owner; unsupported source не превращается в
  user testimony. Повторное наблюдение одного события не добавляет независимую confidence.
- **Tests/manual:** duplicate/reordered/future/late events, payload bounds, injection, source spoof,
  restart/checksum/erasure, unrelated-owner byte equality; fixture import с source audit.
- **Acceptance/DoD:** два adapters дают одинаковую semantics одного типа события; replay не
  дублирует факт; synthetic data не даёт новых privileges; общий контракт раздела 2 пройден.
- **Открывает:** 17.2; live collection остаётся закрыта до 17.3 и connector gate.

### Checkpoint 17.2 — Temporal World Model & Entity Linking

- **Цель/зачем:** различать реальное состояние дела, старое наблюдение, конфликт источников и
  неизвестное, связывая события в понятный контекст жизни без произвольного knowledge graph.
- **Dependencies:** 17.1 и Stage 9 owner semantics; только synthetic sources до 17.3/17.5.
- **Scope/non-goals:** ограниченный словарь person/account/calendar-event/task/file-resource refs,
  temporal projections, source precedence, correction/retraction, entity-link proposals и freshness.
  Нет всеведущего digital twin, автоматического объединения однофамильцев или сбора геолокации.
- **Domain:** existing `WorldModelManager` остаётся writer world claims; явный owner source/entity
  identity mapping, если нужен; attributed facts/inferences/hypotheses не смешиваются. User model
  получает только отдельные допустимые projections, не весь external stream.
- **Persistence/migrations:** source-scoped entity keys, aliases/revisions, effective/observed time,
  provenance и conflict lineage; immutable source events не переписываются при relinking.
- **Services/ports/API/CLI:** temporal projection/rebuild, candidate link/confirm/unlink, relevant
  world query; inspect timeline/conflicts/source precedence; application interfaces без SDK типов.
- **Observability/security:** stale/conflict/ambiguous-link rates, projection lag, source IDs;
  account/person partitions, no sensitive inference promotion, trace raw content exclusion.
- **Compatibility:** Stage 9 reports остаются attributed; historical identity/relationship/self
  не расширяются автоматическим выводом из календаря. Rebuild не создаёт fresh evidence.
- **Tests/manual:** timezones/DST, late deletion/correction, two-source conflict, same-name contacts,
  duplicate correlation, replay/rebuild/export; stale semantic claim против fresh World Model,
  historical report против current fact; synthetic changed/cancelled meeting timeline.
- **Acceptance/DoD:** uncertainty/conflict видны, entity link объясним и обратим; неподтверждённый
  источник не становится verified fact; общий контракт раздела 2 пройден.
- **Открывает:** context foundation для 17.3–17.5 и будущих voice/vision/device observations.

После соответствующих 17.5 sources отдельный optional follow-up к 17.2 может расширять User Model
user-confirmed preferences, routines/habits, current goals и life-context links. Он наследует
полный контракт 17.2 и раздела 2: ограниченный vocabulary, consent, confidence/expiry/correction,
versioned claim migration, existing UserModelManager, inspect/correct/forget API и tests на
ложный habit из одного события, отрицание, смену цели, revoke и cross-person isolation.
Manual gate: пользователь видит, подтверждает/исправляет inference, удаление источника прекращает
её использование. Ни surveillance profile, ни clinical state, ни копирование в Satori inclinations
не разрешены. До отдельной команды этот follow-up не начат и не расширяет user evidence policy.

## 5. Полномочия и контролируемое действие

### Checkpoint 17.3 — Capabilities, Permissions & Credentials

- **Цель/зачем:** отделить техническое право читать/действовать от близости, характера и текста
  запроса; подключение credential не означает безусловный grant на все операции аккаунта.
- **Dependencies:** 17.1–17.2, 14.4; threat model первого connector и выбранной платформы.
- **Scope/non-goals:** capability registry, read/write distinction, scoped grants, deterministic
  risk/confirmation policy, expiry/revoke/kill switch, credential references и minimal principal
  identity binding. Нет universal IAM, silent OAuth scope escalation или relationship-driven grants.
- **Domain:** один permissions owner; grant = principal/account/resource/operation/time/bounds/
  policy version. Risk policy и requester identity не контролируются model. Source collection
  consent, provider disclosure consent и action authorization различаются.
- **Persistence/migrations:** grants/revocations/consent decisions и audit, no token bytes; secret
  handle к OS-backed storage. Restore grants требует revalidation/re-consent, не оживляет токены.
- **Services/ports/API/CLI:** evaluate/grant/revoke/inspect; capability descriptor, secret-store port,
  account/principal binding; локальный explicit consent/permission preview и revoke-all.
- **Observability/security:** allow/deny/revoke reason, scope/risk class, credential health без
  credential contents. Deny-by-default; account binding, token refresh/revocation, expiry и
  fail-closed unavailable secret store; network API не открывается без auth boundary.
- **Compatibility:** existing `RuntimeCapabilities` означает доступность internal функций, не
  юридическое/техническое разрешение; сохраняется отдельно. `counterparty_id` не принимается как
  доказательство пользователя. Relationship score не импортируется в permission decision.
- **Tests/manual:** exhaustive allow/deny scope matrix, cross-account/principal denial, grant
  tamper, revoked/expired token, restoration, kill-switch race; grant read-only затем revoke.
- **Acceptance/DoD:** произвольное предложение/внешний текст/высокий trust не расширяют grants;
  every decision auditable; secrets отсутствуют в export/logs; общий контракт раздела 2 пройден.
- **Открывает:** fake-adapter execution 17.4; реальные аккаунты только по отдельным 17.5 slices.

### Checkpoint 17.4 — Action Proposals, Confirmation, Execution & Reconciliation

- **Цель/зачем:** доказать `think != act` и корректность неидеального внешнего мира до первой
  реальной записи в аккаунт пользователя.
- **Dependencies:** 17.3, 17.1–17.2, existing typed cognition; только fake adapters.
- **Scope/non-goals:** immutable action proposal, preview, risk/permission evaluation, optional
  exact-payload confirmation, dispatch, receipts/failures/unknown, reconciliation и bounded retry.
  Нет реальных external writes, arbitrary shell, бесконечного agent loop или distributed transaction.
- **Domain:** action lifecycle owner отдельно от permissions и world owner. Proposal содержит
  target, parameters, expected external revision, evidence, idempotency и expiry. Confirmation
  привязана к hash/версии payload, account и scope; изменение требует повторной проверки.
- **Persistence/migrations:** action/approval/attempt/receipt/outbox records; atomic local dispatch
  intent и audit до network; `unknown` durable state; provider call вне DB transaction.
- **Services/ports/API/CLI:** propose/preview/confirm/cancel/execute/reconcile; capability-specific
  typed command/result ports; inspect action history и explicit retry/reconciliation controls.
- **Observability/security:** proposal→grant→confirmation→attempt→receipt correlation, queue age,
  rejected/unknown/reconciled count; recheck revoke/policy/expiry immediately before dispatch.
  Candidate tool text не может подтвердить действие; no credentials in model context.
- **Compatibility:** generated assistant text не подтверждает факт исполнения; only observed
  receipt/admitted result may update world via its owner. Action-generated observations retain
  causal IDs и не считаются независимым усилением personality/relationship evidence.
- **Tests/manual:** timeout before/after remote effect, duplicate dispatch/receipt, crash at every
  lifecycle edge, partial completion, stale approval, revoke while queued, malicious result,
  irreversible operation denial; fake successful, failed и ambiguous action recovery.
- **Acceptance/DoD:** no effect before required approval; unknown outcome не повторяется вслепую;
  replay безопасен в заявленных connector semantics; нет обещания exactly-once там, где его не
  обеспечивает provider; общий контракт раздела 2 пройден.
- **Открывает:** 17.5 source integrations и после них ограниченные writes 17.6.

### Checkpoint 17.5 — Read-Only Integration Vertical Slices

- **Цель/зачем:** подключать реальные источники жизни по одному, проверяя весь путь
  consent → fetch → normalized event → world/context → source-grounded answer.
- **Dependencies:** 17.1–17.4 и 14.4; каждый connector отдельно проходит platform feasibility gate.
- **Scope/non-goals:** минимальные read scopes, explicit account/resource selection, cursor/delta
  sync, deletion/correction handling и source-health UI/CLI. Нет writes, broad inbox dump,
  автоматического контакта с людьми или обхода недоступных официальных API.
- **Domain/persistence:** existing source/world/permission owners; только connector cursors,
  revisions и health metadata дополнительно. Raw body retention optional/explicit; no second
  calendar, address-book или relationship owner внутри adapter.
- **Services/ports/API/CLI:** source-specific typed read ports, bounded fetch/replay/revoke;
  connection inspect, scope preview, sync once, pause/remove и source-linked query.
- **Observability/security:** per-source lag, counts, limit/quota/revoke errors; allowlisted remote
  origins/resources, no cross-account merges, privacy filtering before cloud cognition.
- **Compatibility:** отсутствие/отключение connector оставляет text core работоспособным;
  external record deletion не переписывает historical user testimony; unavailability видна явно.
- **Tests/manual:** provider contracts recorded без private data, pagination/cursor gaps, rate limit,
  revoked auth, changed/deleted objects, source injection, two-account isolation, restart/rebuild;
  один разрешённый test account, then revoke и verify no more collection.
- **Acceptance/DoD:** минимум calendar+contacts read slice даёт verified bounded source context;
  каждая следующая строка ниже имеет свой отдельный acceptance, privacy review и общий контракт
  раздела 2. Принятие одного сервиса не сертифицирует другой.
- **Открывает:** 17.6 для принятых source classes; прочие connectors могут оставаться NOT STARTED.

Дочерние checkpoints наследуют **всю карточку 17.5 и раздел 2**, включая migrations/observability/
security/compatibility/tests/DoD; конкретные изменения и gate ниже обязательны:

| Checkpoint | Цель и узкий scope | Domain/persistence/services/interfaces/API | Non-goals и особый acceptance; что открывает |
|---|---|---|---|
| 17.5.1 — Calendar & Contacts Read | Первый end-to-end источник планов и адресатов; только выбранные календари/контакты | Calendar/contact observations и external resource IDs; read adapters/cursors, inspect selected scope; entity-link confirmation | Не merge однофамильцев и не приглашать гостей. Tests DST, recurring exceptions, cancelled meeting, duplicate contact, account isolation; manual read+revoke. Открывает 17.6.1 |
| 17.5.2 — Mail Read | Конкретные выбранные письма/папки как attributed context | Message/thread/source refs, bounded header/body fetch, no implicit attachment download; inspect provenance | Email text не инструкция; tests forged display sender, malicious body/link/attachment metadata, quoted repetition; manual selected-mail query+revoke. Открывает mail drafts17.6.2 |
| 17.5.3 — Messages Read | Только supported официальной платформой selected conversations | Conversation/message refs, edits/deletions, bounded history read; platform-specific consent/inspection | Не обещать API всех мессенджеров; никаких scraping/ToS bypass или ответов. Tests deleted/edited/forwarded messages, sender ambiguity, revoke; открывает отдельно 17.6.2/17.6.3 |
| 17.5.4 — Files Read | Explicit selected files/directories без blanket filesystem access | File resource/version/hash, bounded parser port и source events, scope inspect | No execution/macros, no hidden recursive home scan; tests symlink/path traversal, malformed files, replaced file and revoked scope; открывает future allowlisted file writes24 |
| 17.5.5 — Browser/Web Read | Явно выбранная страница/URL как untrusted observation | URL/document observation, bounded fetch or permitted selected-tab read port, source preview | Нет click/form submission/login automation или произвольной истории браузера. Tests redirect/SSRF boundary, hostile page, downloads, read quota; открывает browser action review24 |

Mail/messages/files/browser slices независимы после 17.5.1; их наличие не является обещанием
поддержки любого vendor. Перед реализацией каждый slice фиксирует текущие официальные API,
реально доступные scopes, условия платформы, consent UX и supported/unsupported matrix.

### Checkpoint 17.6 — Reversible Writes & Explicit Communication Gates

- **Цель/зачем:** перейти от знания к ограниченной полезной работе с проверяемым результатом,
  не выдавая способность подготовить draft за право его отправить.
- **Dependencies:** 17.4 и соответствующий принятый 17.5 connector; реальные writes получают
  собственное разрешение пользователя и test/sandbox account where available.
- **Scope/non-goals:** по одному allowlisted reversible action, external preconditions, preview,
  confirmation, outcome verification и compensating operation там, где она реально возможна.
  Нет broad delete/move, платежей, arbitrary execution или «undo» для уже отправленного сообщения.
- **Domain/persistence:** action/permission owners unchanged; connector-specific typed parameters/
  receipt refs; revision/idempotency mappings и compensating-action lineage; no vendor DTO domain.
- **Services/ports/API/CLI:** create draft/calendar change → preview → approve → execute → inspect;
  cancel queued/reconcile unknown; send остаётся отдельной capability, не flag у общего writer.
- **Observability/security:** external effect ID/version, approval hash, before/after metadata,
  partial/unknown outcomes; recipients/attachments/resources повторно проверяются в dispatch;
  secrets и текст сообщения не попадают в обычные logs.
- **Compatibility:** внешние changes не представляются committed до receipt; user modifications
  защищены expected revision; local rollback не выдает удалённый эффект за отменённый.
- **Tests/manual:** stale object/approval, recipient substitution, duplicate/timeout, quota,
  revoke race, misleading receipt; approved test draft/change затем verified undo/reconcile.
- **Acceptance/DoD:** минимум один reversible slice проходит full path; каждый send class требует
  отдельного human/security approval и общего контракта раздела 2. Необязательные sends могут
  оставаться отключёнными без блокировки Stage 18–22.
- **Открывает:** безопасный action input для Stage 19; не general autonomy.

Дочерние checkpoints наследуют всю карточку 17.6 и раздел 2:

| Checkpoint | Цель/scope и конкретные contracts | Non-goals, tests/manual, acceptance/что открывает |
|---|---|---|
| 17.6.1 — Calendar Reversible Changes | Создать/изменить собственное событие без гостей; typed calendar target/timezone/version, preview and verified receipt; reuse17.5.1 | Не отправлять invitations скрыто. Tests concurrent edit, recurrence/DST, duplication, timeout+lookup; manual change/restore. Открывает limited scheduling assistance |
| 17.6.2 — Drafts Without Sending | Подготовить mail/message draft там, где платформа поддерживает безопасное draft API; явные account, recipients и attachments | Не отправлять, не симулировать draft кнопкой send; typed draft resource, byte/payload binding, read-after-write test и revoke; acceptance zero sends, editable verified draft; открывает 17.6.3 |
| 17.6.3 — Separately Allowlisted Sends | Отдельно согласовать один send class/account/recipient set с exact-payload preview, foreground confirmation и reconciliation | Не считать send обратимым; no blind retry or bulk sends. Tests recipient/attachment/hash substitution, uncertain receipt, revoke before dispatch; real manual send только отдельно разрешённому адресату. Открывает конкретную communication capability, не blanket messaging |

## 6. Взаимодействие, инициатива и embodiment

### Stage 18 — Emotional Support Refinement

- **Цель/зачем:** сохранить характер и интеллектуальную честность, различая comfort, analysis,
  solution, validation, accountability, motivation, challenge и presence в более богатом контексте.
- **Dependencies:** Stages 7–10/relationship, accepted14.3, minimal17.5/17.6 vertical slice; optional
  connectors/sends не блокируют. Требуется support baseline corpus.
- **Scope/non-goals:** need-mix/uncertainty/clarification strategy, calibrated human rubric, crisis
  and clinical boundaries. Нет diagnosis, dependence optimization, скрытых calls/messages/actions.
- **Domain/persistence:** transient cognition strategy и existing state reads; нет clinical user
  profile, второго affect owner или обязательной migration; новых claims без evidence не создавать.
- **Services/ports/API/CLI:** strategy/response projection через существующий cognition port;
  corpus/eval CLI, никаких treatment/intervention endpoints.
- **Observability/security:** strategy/reason/uncertainty and failure distributions; sensitive
  sources only licensed context. Уязвимость не расширяет permission/объём сбора данных.
- **Compatibility:** support не равно agreement; important assistance не подавляется character
  agency; existing grounding, memory, relationship, identity и retry regressions обязательны.
- **Tests/manual:** adversarial validation-seeking, listen-only, grief/stress vs task, uncertain
  third-party context; blinded human review recognizability/appropriateness and dependence red-team.
- **Acceptance/DoD:** agreed human gate без деградации честности и без generic policy slogans;
  общий контракт раздела 2; отдельный provider budget если необходим sampling.
- **Открывает:** Stage 19 thoughtful initiation policy, а не автоматическую терапевтическую помощь.

### Stage 19 — Proactivity

- **Цель/зачем:** разрешить редкий уместный контакт по реальному pending reason, с контролем
  пользователя и нормальным результатом observer `nothing`.
- **Dependencies:** Stage 17, 18;17.3 permissions и принятый notification channel; source context
  только из отдельно разрешённых 17.5 slices.
- **Scope/non-goals:** observer eligibility, user controls, quiet hours/timezone, rate/relevance/
  sensitivity gates, schedule/cancel и notification audit. Нет engagement pings, 24/7 inner loop,
  automatic third-party sends или unrestricted tool execution.
- **Domain/persistence:** initiation decision/delivery lifecycle owner; pending thread и permission
  остаются у своих owners; durable schedule/outbox/reason/cancellation references.
- **Services/ports/API/CLI:** decide/schedule/suppress/deliver/acknowledge, clock/scheduler/channel
  ports; opt-in/quiet hours/rate/pause controls, inspect why initiated or stayed silent.
- **Observability/security:** initiated/suppressed reason, false-initiation rate, stale/duplicate
  delivery, latency; revoke at dispatch, lock-screen privacy, device/channel permissions.
- **Compatibility:** on-demand reply без notification grants продолжает работать; assistant output
  и concept/relationship score сами по себе не создают eligible future reason.
- **Tests/manual:** simulated month mostly `nothing`, DST/travel quiet hours, resolved thread,
  multiple counterparties, duplicate scheduler tick, revoke/crash recovery; user review sample.
- **Acceptance/DoD:** low false-initiation rate по согласованному corpus, specific explainable
  messages и полностью работающий opt-out; общий контракт раздела 2.
- **Открывает:** Stage 20; permission на инициативный контакт не разрешает внешнее действие.

### Stage 20 — Voice

- **Цель/зачем:** добавить consented push-to-talk STT/Core/TTS, сохраняя одно состояние и
  проверяемый смысл interaction между текстом и голосом.
- **Dependencies:** stable core through19; verified microphone/STT/TTS platform/privacy decisions.
- **Scope/non-goals:** mic permission, PTT/VAD boundary, transcript confidence/correction, TTS,
  simple interruption/error UX. Нет always-listening, full-duplex, call answering или lip sync.
- **Domain/persistence:** speech transcript как modality/source annotation с uncertainty; core owners
  unchanged; audio не сохраняется по умолчанию, retention artifact schema только при consent.
- **Services/ports/API/CLI:** replaceable STT/TTS/audio-device ports, transcript-confirm/core-call/
  speak coordination; minimal PTT surface и visible/correctable transcript.
- **Observability/security:** capture/STT/core/TTS/end-to-end timings, error/confidence and cancel
  metadata; no audio/transcript in ordinary logs; explicit mic indicators/revoke.
- **Compatibility:** same core commands and canonical commit as text. STT uncertainty не даёт
  техническую authority; high-risk confirmation не выводится из фоновой речи/случайного speaker.
- **Tests/manual:** noise/accent, low-confidence intent, permission denial, provider outage, cancel,
  transcript correction/evidence invalidation and text-equivalent fixtures; PTT restart session.
- **Acceptance/DoD:** measured latency/accuracy and consent gate; no state corruption on voice
  failure; общий контракт раздела 2. Реальные STT/TTS probes имеют отдельный scope/budget.
- **Открывает:** Stage 21 embodiment и future22 real-time coordination; не Stage 26 phone access.

### Stage 21 — Avatar

- **Цель/зачем:** оригинальное визуальное embodiment выражает характер, не определяя его.
- **Dependencies:** Stage 20 или отдельно принятый stable text core; original art/IP и supported
  parameter runtime decision. Основной путь следует после 20.
- **Scope/non-goals:** gaze/eyes/brows/mouth/head/body bounds, expression intent mapping,
  procedural blink/breath/idle и expression masking. Нет чужого character copy или emotion sprites
  как source of truth.
- **Domain/persistence:** transient display/animation state и versioned asset/mapping config; no
  persistent affect mutation; canonical internal emotion != displayed expression.
- **Services/ports/API/CLI:** expression planner/procedural animator/rendering port; preview and
  accessibility/reduced-motion controls; engine SDK только adapter.
- **Observability/security:** frame/resource budget, asset/mapping version и dropped animation;
  expression не раскрывает private state автоматически, no camera permission implicit.
- **Compatibility:** отключение avatar не меняет personality/affect/dialogue; asset upgrade не
  мигрирует identity и не создаёт second character seed.
- **Tests/manual:** mapping bounds, seeded idle, no reverse writes, memory/GPU performance, asset
  integrity; review silence, disagreement, warmth, restraint и reduced motion.
- **Acceptance/DoD:** original stable non-caricature embodiment, measured resource bounds и
  expression-state separation; общий контракт раздела 2.
- **Открывает:** Stage 22; animation не evidence о внутренних переживаниях или внешнем мире.

### Stage 22 — Real-Time Presence

- **Цель/зачем:** естественный timing текста, речи и выражения с корректными interruption и
  delivery semantics, а не поток токенов ценой потерянного canonical commit.
- **Dependencies:** Stages 20–21; новый ADR streaming/draft/outbox/cancellation, дополняющий текущий
  non-streaming commit-before-delivery contract; reuse17.4/19 reliability patterns, не их permissions.
- **Scope/non-goals:** streaming generation/TTS, barge-in, turn cancellation, lip-sync/timing,
  durable draft/committed/delivered distinction и recovery. Нет always-on capture/vision/actions.
- **Domain/persistence:** real-time session lifecycle/fragment-delivery records; canonical self
  owners unchanged; obsolete output не получает задним числом completed semantics.
- **Services/ports/API/CLI:** cancellable generation/audio/expression orchestration, stream ports
  и bounded queues; visible interrupted/draft state, stop/pause/recover controls.
- **Observability/security:** time-to-first-audio, committed-reply vs delivered-fragment latency,
  overlap/drop/cancel/replay counts; no raw fragments in telemetry; consent remains revocable.
- **Compatibility:** existing non-streaming mode продолжает работать; interrupted partial utterance
  не становится authoritative memory/self statement or fresh relationship evidence.
- **Tests/manual:** rapid barge-in, stale callback, partial TTS/generation loss, crash between
  commit/delivery, outbox replay, no double mutation; long session and resource stress.
- **Acceptance/DoD:** delivery status truthful, obsolete output stops, restart не повторяет speech/
  mutations; measured timing goals и общий контракт раздела 2.
- **Открывает:** Stage 23 multimodal perception with dependable session lifecycle.

### Stage 23 — Vision

- **Цель/зачем:** воспринимать явно разделённое пользователем изображение/экран/камеру как
  чувствительное uncertain observation, а не новое доверенное управление системой.
- **Dependencies:** Stage 22, 17.1–17.3, 14.4; current platform capture/provider/retention decision.
- **Scope/non-goals:** selected image/screenshot, explicit camera capture, observations with
  regions/confidence/source and memory eligibility. Нет covert continuous capture, biometric
  recognition by default или автоматического управления тем, что видно на экране.
- **Domain/persistence:** external visual source/observation schemas, image provenance/consent/
  expiry; derived caption не подтверждённый факт; canonical owners и action grants неизменны.
- **Services/ports/API/CLI:** capture/admit/redact/perceive/project через replaceable vision port;
  explicit capture preview и no-retention/revoke controls.
- **Observability/security:** capture/vision latency, input sizes, confidence/denial/source IDs;
  no images/OCR text in logs, cloud minimization, visible-text injection and sensitive-window policy.
- **Compatibility:** transcript/chat roots не подменяются image caption; no permission derived
  from a screenshot of an approval dialog; text/voice работают без vision grant.
- **Tests/manual:** ambiguous scene, hostile screenshot, metadata/size corruption, consent revoke,
  no-retention, provider failure и confidence transitions; explicit inspect/delete/rebuild scenario.
- **Acceptance/DoD:** every observation traces to consented source; uncertainty preserved and
  visible instructions cannot authorize action; общий контракт раздела 2.
- **Открывает:** selected screen context для individually approved Stage 24 capabilities.

## 7. Расширение инструментов, устройства и релиз

### Stage 24 — Tools

- **Цель/зачем:** расширять проверенный 17.3–17.6 action substrate отдельными capabilities, не
  создавать второй tool framework после первых интеграций.
- **Dependencies:** stable core, 17.3–17.6; Stage 23 только для screen-dependent operations,
  не искусственная зависимость всех read APIs. Каждый tool имеет собственный threat model.
- **Scope/non-goals:** bounded browser/computer/file/task/note/device tool slices, least privilege,
  typed previews/results и action history. Нет blanket computer access, arbitrary shell, silent
  destructive actions или выдачи model прямого vendor API.
- **Device-read slices:** selected notifications, location и device-state observations только
  через реально поддерживаемый adapter и отдельный time/resource/device grant. Location off by
  default; явные индикаторы/expiry/revoke, no continuous background tracking. Каждый slice
  наследует 17.5 read-source и Stage 24 contracts, имеет size/rate bounds и tests на permission
  loss, stale location, notification injection, cross-device isolation и no collection after revoke.
- **Domain/persistence:** existing permission/action/source owners; новый command/result type
  только при необходимости; no generic JSON bypass; per-tool revision/receipt metadata.
- **Services/ports/API/CLI:** capability-specific read/write adapters; registry inspection,
  resource-scope selector, preview/confirm/execute/revoke. Screen automation изолирована от SDK
  integrations и не применяется для обхода platform authorization.
- **Observability/security:** scope/risk/approval/effect audit, timeouts/unknown outcomes и tool
  health; sandbox roots, network destinations, destructive confirmation и kill switch.
- **Compatibility:** early integrations не переписываются в новую архитектуру; unsupported tools
  disabled; provider replacement не меняет grants или action lifecycle.
- **Tests/manual:** per-tool permission/injection/path/network matrix, irreversible-action denial,
  drifted UI/wrong window, partial effect/reconciliation; deny/allow/revoke и approved reversible
  action на выделенном ресурсе. Real hardware/OS probes только после отдельного разрешения.
- **Acceptance/DoD:** каждый enabled tool отдельно проходит security gate; capability availability
  не означает blanket delegation; общий контракт раздела 2.
- **Открывает:** richer selected-platform client use; unattended workflows остаются Stage 27.

### Stage 25 — Native Clients

- **Цель/зачем:** сделать daily desktop/mobile experience без второго canonical self в UI.
- **Dependencies:** stable versioned application API, 14.4 export/recovery, 17.3 auth/permissions;
  отдельный selected-platform decision. Первый рекомендуемый slice — desktop клиент и один
  canonical runtime; конкретная технология выбирается только при открытии.
- **Scope/non-goals:** selected native shell, secure cache/credentials, accessibility, lifecycle,
  notification and export/restore UI. Нет feature parity всех OS, silent cloud canonicalization
  или multi-writer sync до 25.1.
- **Domain/persistence:** UI state/cache отделены от canonical owners; authenticated client/runtime
  session and device binding where required; secrets in platform store, not app export.
- **Services/ports/API/CLI:** versioned read/command/event API over application use cases;
  explicit session/auth/permission/error contracts; native adapter with no repository writes.
- **Observability/security:** client/runtime version, crash/reconnect/latency, redacted health;
  selected transport auth, secure pairing/session expiry/revoke, OS permissions and private previews.
- **Compatibility:** CLI remains supported and shares the same core; cached personality не
  редактируется offline; incompatible client receives explicit upgrade/error rather than guessed state.
- **Tests/manual:** API contracts, install/upgrade/uninstall/restart, offline/read-only cache,
  secret storage, accessibility, crash recovery, export/restore continuity on supported platform.
- **Acceptance/DoD:** один platform vertical slice проходит lifecycle/security/continuity gate;
  unsupported matrix явна, общий контракт раздела 2; no claim of multi-device completion.
- **Открывает:**25.1 after dedicated sync ADR; дополнительные native platforms — отдельные slices.

### Checkpoint 25.1 — Multi-Device Canonical Continuity & Synchronization

- **Цель/зачем:** видеть одну Сатори с разных устройств, не конфликтующие копии личности и
  не дублирующиеся сообщения/действия.
- **Dependencies:** Stage 25, 14.4, 17.3/17.4/19; dedicated topology/ownership/auth/sync ADR.
- **Scope/non-goals:** один выбранный canonical writer/authority, authenticated devices, event
  cursors, offline read caches и queued proposals, explicit conflicts, device revocation и recovery.
  Нет multi-master personality merge, silent server migration или self changes по last-write-wins.
- **Domain/persistence:** device enrollment/revocation и synchronization lifecycle; identity IDs,
  aggregate versions, operation idempotency and cursor watermarks; source device != evidence trust.
- **Services/ports/API/CLI:** pair/unpair/sync/resume/status, authoritative command submission,
  conflict preview, notification destination policy; versioned sync ports and secure transport.
- **Observability/security:** lag/backlog/conflicts/replay/duplicate dispatch, active authority and
  device ID; lost-device revoke, key rotation, encrypted transport/cache, private notification policy.
- **Compatibility:** single-device local-first remains valid; offline write is pending proposal,
  not committed state; grants revalidated by canonical authority at execution after reconnect.
- **Tests/manual:** two devices, same command offline, clock skew, lost cursor, network partition,
  stale client, revoke queued work, runtime restart and backup restore; exactly one local mutation/
  qualified external effect per idempotency contract, not blanket exactly-once.
  Для desktop/mobile claim обязательны реальные поддерживаемые desktop и mobile clients,
  а не только два mock sessions; ограничения mobile background/API фиксируются по факту.
- **Acceptance/DoD:** same canonical identity/history across supported devices, conflicts explicit,
  no duplicated outreach/actions and source isolation retained; общий контракт раздела 2.
- **Открывает:** device-coordinated Stage 26 calls и later bounded autonomy, не новую personality.

### Stage 26 — Consented Calls & Communication Presence

- **Цель/зачем:** отдельно проверить высокорисковую live communication surface, где ошибка
  адресата/содержания/согласия серьёзнее ошибки локального draft.
- **Dependencies:**20/22 voice, 25.1 devices, 17.3–17.4 permissions/action lifecycle; verified
  official telephony/platform APIs, terms и применимые consent/recording requirements.
- **Scope/non-goals:** сначала explicit user-initiated call assistance/call metadata на одной
  поддерживаемой платформе; затем отдельно разрешённая ограниченная call action. Нет covert
  recording, скрытой impersonation, emergency-service automation или unrestricted answering.
- **Domain/persistence:** call session/participant/consent/receipt lifecycle; transcript source
  eligibility отдельно от user text, recording off by default; action owner контролирует dialing.
- **Services/ports/API/CLI:** preview target/start/stop/transfer where genuinely supported,
  telephony/media port, visible participants/consent/recording indicators и immediate hang-up.
- **Observability/security:** target reference, consent/connection/termination status, duration and
  known cost; no call content in normal logs; recipient identity confirmation, revoke/kill switch,
  platform/legal feasibility gate без обещания несуществующих API.
- **Compatibility:** phone number/contact link не authenticated principal; call transcript не
  право действовать от имени speaker; failed dialing не becomes completed promise/world fact.
- **Tests/manual:** wrong/ambiguous number, voicemail/human ambiguity, reconnect, device handoff,
  cancellation, cost cap, recording denial; controlled test participants with explicit consent.
- **Acceptance/DoD:** narrow supported call workflow безопасен и понятен всем участникам;
  невозможные/неразрешённые платформой операции остаются unavailable; общий контракт раздела 2.
- **Открывает:** возможность отдельно делегировать конкретный communication workflow в Stage 27.

Stage 26 разбивается на отдельно разрешённые communication gates. Все наследуют **всю карточку
26 и раздел 2**, включая persistence, observability, privacy, compatibility и DoD:

| Gate | Scope и отдельные contracts | Tests/manual/acceptance и следующий шаг |
|---|---|---|
| 26.1 — Inbound Screening & Message Taking | Supported VoIP/forwarding/platform adapter, visible delegated-agent role, whitelisted intake, take-message transcript с consent/source/uncertainty; запись audio по умолчанию выключена | Unknown caller, consent refusal, spoofed identity, malicious spoken instruction, handoff/stop; controlled incoming call с согласными участниками. Открывает attributed message/follow-up proposals, не право менять календарь |
| 26.2 — Bounded Representation | Явно делегированный контекст/адресаты/темы, ограничения обещаний, handoff to user; call result создаёт World/Thread proposals и отдельный ActionProposal для calendar changes | Out-of-context request, false authorization от caller, unsupported promise, conflicting meeting time; human review controlled call и proposal preview. Внешняя запись проходит прежние grants/confirmation, не выполняется по словам собеседника |
| 26.3 — Explicit Outbound Calls | User-approved recipient/context/budget/time, exact preview и dial admission через existing action owner; no autonomous cold calls | Wrong-number/voicemail/timeout/cost/revoke tests и отдельно разрешённый test recipient; receipt честно различает connected/failed/unknown. Открывает только этот call class, future delegation требует Stage 27 |

### Stage 27 — Bounded Delegated Autonomy

- **Цель/зачем:** выполнять ограниченное поручение во времени без постоянного ручного запуска
  каждого безопасного шага, сохраняя контроль, объяснимость и право остановить работу.
- **Dependencies:** принятые action/recovery/permission/proactivity/client gates; только уже
  individually accepted capabilities. Stage 26 нужен лишь для workflows со звонками.
- **Scope/non-goals:** explicit delegated objective, time/cost/step/resource limits, approval
  checkpoints, stop conditions, resumable bounded plan and user-visible progress/outcome.
  Нет self-assigned life goals, open-ended self-improvement, privilege escalation, 24/7 LLM loop
  или optimization toward engagement/dependency.
- **Domain/persistence:** delegated-work owner отдельно от current-turn agency, Satori interests и
  unfinished-thread owner; versioned task/plan/step/authorization refs, resume/cancel decisions.
- **Services/ports/API/CLI:** propose plan, approve bounds, run one step, pause/cancel/reconcile,
  summarize result; planner outputs typed proposals and has no executor/repository authority.
- **Observability/security:** progress, accepted/rejected step, remaining budget/limits, source,
  pause reason и action receipt chain; policy reevaluated each dispatch, no model-managed budget.
- **Compatibility:** rollback plan не отменяет уже случившийся external effect; action result
  remains attributed evidence, not fresh independent proof for self/personality/relationship.
- **Tests/manual:** adversarial task/tool content, loop/stall, stale plan, expiry/budget/revoke,
  partial failure/restart, user stop, source poisoning; simulated week and one explicit bounded
  live workflow under agreed cost/action limits.
- **Acceptance/DoD:** completes or honestly reports bounded failure; zero out-of-scope effect,
  stops deterministically, user can inspect/revoke; общий контракт раздела 2.
- **Открывает:** Stage 28 release evaluation of accepted delegation scope, не unrestricted autonomy.

### Stage 28 — Production Hardening & Release Gates

- **Цель/зачем:** подтвердить целостность personality+agent experience в поддерживаемом product
  slice и объявить честные reliability/privacy/behavioral limits.
- **Dependencies:** принятие выбранного release cut; stages/capabilities вне него явно disabled.
  Stage 16, unsupported clients/connectors/calls не обязаны существовать ради номера релиза.
- **Scope/non-goals:** release compatibility matrix, supported platform upgrade/recovery, sustained
  latency/resource/cost budgets, retention/erase drills, security review, incident response,
  supply-chain/update controls, accessibility и longitudinal quality. Не впервые строить permissions,
  encryption или backup и не обещать универсальную автономную цифровую жизнь.
- **Domain/persistence:** никаких новых state owners без отдельного scope/ADR; миграции только
  необходимых hardening defects с representative historical snapshot checks и recovery plan.
- **Services/ports/API/CLI:** diagnostics/support bundle redaction, health/readiness, recovery/
  safe-mode controls; stable versioned public contracts и documented unsupported behavior.
- **Observability/security:** separate foreground reply, background learning, action and media SLOs;
  privacy-preserving error metrics, regression/incident audit; secret rotation, least privilege,
  external-source injection, restored grants disabled, user-access/erase/export drills.
- **Compatibility:** upgrade/downgrade/recovery matrix explicit; no destructive reset of learned
  Satori to pass tests; rejected provider evidence remains rejected and historical data untouched.
- **Tests/manual:** full Foundation and stage corpora, representative migrations, clean install,
  sustained sessions, device/network/provider loss, permission races, attack simulations,
  clean-machine restore and long-horizon human review with provider costs separately authorized.
- **Acceptance/DoD:** agreed release cut meets measurable behavior, safety, reliability/privacy,
  usability and recovery gates; residual risks/support limits published; no critical unresolved
  invariant violation. `progress.md` records accepted evidence and explicit product version.
- **Открывает:** эксплуатацию только заявленного supported scope; новые capabilities/автономность
  продолжают требовать свои Stage/gates, а не получают разрешение от факта релиза.

## 8. Риски, которые следует закрывать до удорожания изменений

- **Message-only provenance.** Сегодня evidence основана на canonical user-message roots. До
  connectors нужен versioned external-source contract, а не fake chat messages. Нельзя одним
  расширением разрешить внешним событиям учить все owners; разрешённые виды evidence задаются
  отдельно для world/user/memory/relationship/self/personality.
- **Recovery != aggregate export.** Имеющиеся model/position/inclination/personality exports и
  personality checkpoints не заменяют полный restore. Plain output paths требуют одинаковой
  защиты с DB/log files до появления больших sensitive datasets.
- **Erasure vs immutable lineage.** Простое удаление строки с отключением FK лишит знания оснований;
  вечное хранение raw roots тоже не privacy policy.14.4 должен проверить removal/tombstone/
  dependent invalidation и минимальный audit на synthetic data до новых sources.
- **Availability != permission.** Internal capability flags, relationship trust, API-key presence
  и remembered user preference не grants. Нужен небольшой самостоятельный permission owner;
  не универсальный security framework и не новый global `Satori` object.
- **External commit uncertainty.** Existing local UoW/retry не решает timeout после отправки письма.
  17.4 требует durable unknown/reconcile и capability-specific idempotency до live writes.
- **Исторические policy ветки и большие модули.** Развивать ports/use cases так, чтобы новые tools
  не росли внутри CLI/parser, conversation contracts или character-selection module. Узкие
  extraction/refactoring при соответствующем Stage сохраняют исторические golden contracts;
  массовая перепись accepted behavior ради чистоты не входит в этот план.
- **Single-user != authenticated multi-device.** Opaque counterparty IDs дают partitioning, не
  безопасность клиентов. Сначала одна canonical authority, затем явно проверенный sync; никаких
  last-write-wins merges personality/relationship.
- **Embodiment не лечит core behavior.** Voice/avatar усиливают как удачное, так и неудачное
  поведение.14.3 character gate остаётся обязательным; новые датчики/история не подменяют его.

Текущие разрешённые scope разделены явной командой пользователя и ADR-0053: Character продолжает
Checkpoint 14.3 в своём gate, а Foundations независимо ведёт Checkpoint 14.4. После завершённых
recovery/scope/source-exclusion, пяти owner-body verticals и retained audit metadata/ADR-0071
завершён macOS engineering gate identity-scoped reason disposition/ADR-0072: 4726 passed /5 skipped.
14.4.2i Whole-store Retention & Readiness Integration явно открыт ADR-0074 и continuation
2026-10-14; isolated macOS engineering gate завершён: 4815 passed /5 skipped и scenario21/21.
14.4.3 Local Principal & Read/Disclosure Grants только предложен; separate authorization,
physical/privacy/key/Linux и whole-14.4 gates сохраняются.
Точная текущая граница и evidence — в [progress](progress.md). Character provider attempt
по-прежнему требует отдельной точной авторизации. 14.4.3, Stage 15 и следующие stages не открыты.
