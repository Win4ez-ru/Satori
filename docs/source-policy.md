# Canonical Source Eligibility — Checkpoint 14.4.2b.2

Дата: 2026-10-03. Основание: [ADR-0057](decisions/0057-canonical-source-exclusion-and-trusted-policy-floor.md).
Runtime реализован в текущем рабочем дереве; итоговые результаты focused/full Foundation,
нативного сценария и проверки process barrier фиксируются отдельно в [progress.md](progress.md).
Наличие реализации и этого документа само по себе не означает прохождение quality gate или
приёмку всего Checkpoint 14.4. Character остаётся отдельным workstream.

## Продуктовая операция

`sources exclude` терминально исключает один доказанный canonical USER root из обычного
раскрытия, нового provider context и нового evidence. Target включает lineage, identity,
counterparty, session, interaction и message. Произвольный текст, assistant output, внешний
event или provider-supplied ID не становятся canonical user evidence.

Операция закрывает и текстовые производные, чей фактический input зависел от исключённого
источника. Например, поздний ответ мог прочитать старое воспоминание, а extraction эпизода —
получить paired assistant reply. Даже если принятые quotations относятся только к другому
сообщению, такой payload нельзя объявить независимым от исключённого root. Контроль выполняется
по provenance целого текстового payload; phrase rewriting и LLM-проверка «текст очищен» отсутствуют.

| Операция | Recall и новое episode processing | Ранее сформированный semantic support | Canonical dialogue и private evidence bytes | Обратимость |
|---|---|---|---|---|
| Archive Memory, ADR-0056 | Эпизод исключён из обычного recall, index/backfill и нового processing | Точные старые support edges остаются допустимы при действительном source policy | Сохраняются | Явное reactivate с новой версией |
| Canonical Source Exclusion, ADR-0057 | Источник и зависимые payloads не допускаются | Небезопасные активные text aggregates скрываются и выводятся владельцами из активного состояния | Сохраняются, включая private whole-state backup | Терминальная операция в этом scope |
| Selected episodic body handling, ADR-0061 | Выбранные episodes остаются terminal и не возвращаются в recall/indexing | Исторические ссылки сохраняются; доступность quotation evidence не утверждается | Выбранные summary/quote поля становятся SQL NULL, все их vectors удаляются; другие surfaces остаются | Обычный owner upsert/reactivate не восстанавливает body |
| Selected dialogue body handling, ADR-0063 | Terminal dialogue не возвращается в history/context, replay или новое processing | Точные approved witnesses сохраняют историческую attribution; нового literal evidence не создают | Выбранные canonical USER, paired и COMPLETE-dependent assistant content становятся SQL NULL; independent USER остаются | Обычная запись/replay не восстанавливает body |
| Selected knowledge body handling, ADR-0066 | Terminal semantic/model/position/inclination content не допускается в context или новые inputs | Exact approved transitions сохраняют историческую attribution без нового learning | Значения, normalized copies, proposition/topics и все выбранные position quotes/signatures становятся SQL NULL; numeric history и IDs остаются | Owners не восстанавливают body и не освобождают retained keys/caps |
| Selected Reflection payload handling, ADR-0068 | Terminal proposal не допускается в dispatch, новый owner commit или contentful inspection | Exact retained run/source/attempt/outcome metadata сохраняет attribution и completed replay | Весь выбранный proposal JSON становится SQL NULL; parent metadata, logical version 1, numerical effects и budgets остаются | Sole writer не восстанавливает body; повторный apply не создаёт нового learning |
| Physical erasure | Отдельный будущий контракт | Требует отдельного решения по остаточным данным | В этой реализации не удаляются | Не реализовано |

Archive/reactivate не снимает source exclusion и не исправляет legacy attribution quarantine.
Новое независимое USER сообщение получает новый root и проходит обычную policy/evidence
проверку. Совпадение его текста со старым фактом само по себе не является ни replay старого
evidence, ни доказательством независимости производного payload.

## Владельцы и устойчивые интерфейсы

Source policy владеет допуском источников и независимым ledger. InteractionLog остаётся
владельцем canonical dialogue, MemoryManager — archive/reactivate, SemanticMemoryManager —
semantic lifecycle. User/World Model, positions/inclinations, reflection и relationship backlog
обрабатывают отзыв поддержки через своих существующих владельцев. Компонент privacy не
переписывает произвольные domain aggregates напрямую.

Общий контракт не зависит от Character prompts, renderer, версии эксперимента или провайдера:

- `CanonicalRootRef` — доказанная идентичность canonical USER source без его текста.
- `SourceEligibilitySnapshot` — trusted floor, installation/lineage/identity и policy generation.
- `PayloadRef(kind, payload_id, version)` — точная immutable версия текстового aggregate.
- `ProtectedContentFootprint` — actual roots и прямые зависимости, со статусом
  `COMPLETE`, `UNKNOWN` или `OVERFLOW`.
- Application source admission — prepare input receipt, immediate dispatch admission,
  повторная проверка перед записью и atomically recorded payload receipt.
- Strict frozen preview/command/receipt — metadata, ожидаемая generation, digest и request ID.

Обычная память и recall дополнительно ограничены `MemoryScope(identity_id, counterparty_id)`.
Global own state не превращается в память любого собеседника. Его текстовые positions,
inclinations и модели тоже проходят admission по собственным receipts. Character получает
уже допустимый typed context либо bounded capability/unavailable metadata; выбирать floor,
разрешать disclosure или менять privacy policy он не может. Relationship trust не является
техническим разрешением.

## Actual-input provenance и SQL filtering

Main migration `0016_source_policy` хранит derived binding/excluded-root mirror, immutable
input/payload receipts, owner-withdrawal decisions и отдельные owner repair generations.
Input receipt записывается до dispatch и перечисляет весь фактически выбранный input window,
а не только evidence, которое LLM затем процитировала. Payload receipt фиксируется в той же
транзакции, что и соответствующая owner mutation.

Формирование episode учитывает canonical USER root и paired assistant payload. Semantic
formation учитывает все выбранные episodes и их точные версии. Retrieval и semantic context
передают наружу `source_payload_refs` именно выбранных версий; их prompt-проекция не становится
вторым источником persistent state. Изменение aggregate наследует dependency на предыдущую
версию: обновлённая формулировка сама по себе не снимает старую зависимость.

Mirror вычисляет transitive eligibility по DAG прямых immutable dependencies. Прямой footprint
ограничен 512 roots и 128 payload handles; текущая reconciliation ограничена 100 000 payloads.
Missing parent/version, cycle, overflow и недоказанный canonical root не дают допуск. Это bounded
консервативное withholding, а не попытка автоматически переписать текст без старой информации.

Canonical scope и source admission применяются **до SQL limits и provider selection**:

- history/recent continuity и interaction replay;
- episode lookup и ordinary inspection, включая `--include-archived`;
- retrieval candidate precheck, vector candidates, index/rebuild и backfill;
- semantic source window, active claims, inspect/recall и semantic replay;
- User/World Model, positions/inclinations и reflection/relationship processing.

Исключённые строки не расходуют ограниченное окно полезных результатов и не попадают в request
body через vector candidate или stale in-memory object. Provider input и окончательная owner
запись используют повторное доказательство; первоначальный eligible snapshot не является grant
на будущую запись.

`memories search TEXT` — явный свежий локальный query. Его query-only admission не создаёт
canonical USER message и не допускает этот query как новое self/relationship/semantic evidence.
Conversation retrieval, напротив, привязывает query к фактическому текущему canonical root.

## Dispatch, очереди и завершение операции

Source policy использует короткий shared/exclusive POSIX barrier до main/floor SQLite
transaction. Локальный selection, actual dispatch и final commit заново читают trusted policy;
lease и SQL transaction не удерживаются во время inference.

Application делает immediate admission и для заменяемых provider ports. Адаптеры повторяют
проверку после scheduler/connection-pool wait, при retry и fallback перед каждой transport
попыткой. ContextVar связывает попытку с immutable input receipt; он не заменяет проверку.
На actual dispatch заново доказываются текущие **top-level** aggregate versions и ACTIVE status
эпизодов. Поэтому archive → reactivate не делает старый queued request актуальным: version уже
изменилась. При этом immutable parents ранее сформированного semantic support сохраняют
исторический смысл, включая разрешённые архивные episodes.

Если exclusion завершился после provider dispatch, удалённый provider мог уже получить input.
Эта передача не может быть отозвана задним числом. Final commit и protected delivery/replay
имеют собственный admission; недопустимый результат нельзя автоматически записать или
повторно раскрыть. Уже допущенная и начатая delivery также не может быть отозвана. Новый retry,
deferred delivery или повторный запрос требует новой проверки; правило не является разрешением
на будущие раскрытия.

## Независимый trusted floor и восстановление

Ledger — installation-local private SQLite store с отдельной migration stream; исходный
`policy_0001` поддерживается для source operations. Явный upgrade до `policy_0002` по
[ADR-0060](decisions/0060-durable-erasure-intent-and-owner-preparation.md) добавляет отдельный
erasure revision и terminal intent, сохраняя original exclusion generation и тот же binding.
`policy_0003` добавляет body action head; explicit `policy_0004` по
[ADR-0063](decisions/0063-terminal-dialogue-bodies-and-historical-provenance.md) расширяет этот
же head закрытым dialogue action type. Existing episode JSON, approval hashes и episode-only
head digests сохраняются точно; отдельный dialogue ledger не создаётся.
Обычные reads ничего не мигрируют; main ACK erasure intent отдельно от source repair.
Ledger хранится вне main database и вне whole-state archive. Installation binding и fixed-inode lock
проверяются вместе с ledger schema; отсутствие, подмена, повреждение или неподдерживаемый
platform barrier возвращают typed content-free reason. Protected runtime не создаёт пустой
replacement floor автоматически.

Default location — `./var/source-policy`; явная настройка:
`SATORI_SOURCE_POLICY_DIRECTORY=/absolute/private/source-policy`. При выборе другого
`SATORI_DATABASE_URL` для той же trusted installation следует сохранить эту независимую
настройку, а не создавать отдельный пустой ledger. Ledger/binding/lock и SQLite sidecars
зарезервированы: export/restore и partial artifact writers не используют их как output targets.

Exclusion линейно вступает в силу при durable commit ledger. Затем main mirror обновляется,
после чего отдельные владельцы восстанавливают свои projections в независимо возобновляемых
транзакциях. Общей distributed transaction между floor и main DB нет. Ошибка main/owner repair
не отменяет уже committed exclusion.

Mirror generation и owner repair generation означают разные вещи. Пока mirror отстаёт,
protected operational admission отклоняется. После обновления mirror допустимые независимые
reads могут продолжаться; отставший owner не может выполнять новые mutations, включая
same-key merge с hidden ACTIVE claim. Успешная owner repair отмечается отдельным watermark.
`exclusion_committed_repair_pending` сообщает именно это частично завершённое состояние.

Current backup сохраняет main mirror/receipts/owner history, но **не содержит trusted floor**.
Offline `state import --preview/--activate` переносит проверенные main bytes и не запускает
providers/jobs. Старый main backup, выбранный затем для работы в той же installation, обязан
связаться с сохранившимся current floor и пройти reconciliation. Его старый mirror не отменяет
исключение. Повторный apply с исходным request ID или `sources repair` может возобновить repair
после выбора старого main snapshot.

Эта гарантия предполагает сохранённый независимый trusted floor. Одновременный откат всей
installation, потеря ledger/binding, перенос на другую машину и recovery ключей/прав доступа
не решены этим slice. Checksum/signature подтверждают целостность/происхождение, но сами по себе
не доказывают, что floor является самым новым. Trusted freshness anchor и authenticated recovery
остаются отдельными gates. Не следует называть old-main anti-resurrection защитой от полного
отката installation.

ADR-0091 добавляет отдельное offline adoption при строго ограниченном device-only drift:
обе сохранённые пары расходятся только по `st_dev`, inode прежние, exact schemas/identity
согласованы, policy/erasure generations равны нулю и sidecars отсутствуют. Команда
`python -m satori.binding_recovery_cli --policy-directory ABS --canonical-database ABS --preview`
только проверяет metadata/integrity/hashes. Последующий `--adopt` требует точного private plan,
его review digest, request ID, явного решения владельца принять текущие файлы и подтверждённой
остановки runtime. Ledger owner сохраняет backup/одноразовый claim/receipt и обновляет только
два device-компонента descriptor. Обычная admission сохраняет полное сравнение device/inode;
автоматического fallback, reset/enrollment, доказательства прежнего volume или freshness нет.
После неоднозначного результата запрещён слепой повтор. Подробные границы и причины отказа
даны в [ADR-0091](decisions/0091-reviewed-device-binding-recovery.md).

## Legacy, owner retirement и остаточные данные

Явно reviewed pre-policy enrollment сохраняет известные accepted canonical roots и owner scope,
но не восстанавливает отсутствующие actual-input receipts. Поэтому даже доказанный legacy
payload остаётся `UNKNOWN`. Conditional compatibility допустима только при доказанном
owner/evidence scope и **нуле исключений во всей lineage**. Недоказанный legacy payload
не получает допуск даже при generation zero. Отсутствующая новая version receipt не является
legacy exemption и не становится COMPLETE по наличию current watermark.

Операторское enrollment на известных predecessor schemas `0016_source_policy`,
`0017_erasure_intent` и `0018_erasure_bodies` читает только нужные исторические proof columns
и ID/version projections.
Оно не материализует current ORM aggregate и не обновляет БД автоматически. На текущей схеме
отдельно доказывается доступность dialogue content, summary/quote и согласованность body markers; NULL bodies
не могут стать legacy evidence. Неполная body schema или неизвестная revision не получают
predecessor exemption. Обычный current memory runtime требует полную текущую representation.

После первого lineage exclusion все UNKNOWN payloads и их descendants удерживаются. Это может
скрыть больше исторического текста, чем один выбранный source. Принятый citation subset или
оставшиеся evidence не доказывают независимость каждой старой формулировки. Свежий independent
context может работать с новыми COMPLETE receipts; LLM не вызывается для «очистки» legacy.

Semantic owner переводит небезопасные ACTIVE/DISPUTED claims в RETRACTED через typed support
withdrawal, освобождая active key. Старые confidence, value, evidence и formation revisions
сохраняются; новый metadata receipt/audit сообщает source withdrawal, а не «факт опровергнут».
Receipt перечисляет точные непосредственно withdrawn roots; transitive/UNKNOWN withholding
может не иметь прямого excluded root в accepted evidence. Новый независимо grounded same-key
claim создаётся обычным owner policy и не наследует retired confidence только из-за совпадения
значения. Если owner identity не доказана ни canonical evidence, ни прежней immutable receipt,
repair не объявляется завершённой.

User/World Model и positions выполняют своё owner retirement. Inclinations сохраняют постоянную
aggregate identity/key и numeric trajectory, вводя disclosure hold; hidden label не даёт права
создать дубликат или переиспользовать формулировку без независимого proof. Reflection и
relationship backlog terminalize недопустимые источники без нового self/relationship evidence.

Численные historical effects личности, эмоций, mood и relationship не откатываются. Spent
budgets/caps не возвращаются. Source exclusion регулирует дальнейшее раскрытие и использование
данных, а не утверждает, что прошлый опыт Сатори никогда не происходил.

Raw canonical dialogue, generated payload bytes, immutable evidence, revisions и совместимые
vectors могут физически оставаться в main DB, WAL/sidecars и private backup. Direct SQL,
чтение файлов и уже выгруженные архивы не превращаются в безопасный public disclosure канал.
Этот slice не шифрует их и не выполняет физическое удаление. Shared core fixtures без bound DB
могут явно использовать unmanaged TEST composition; operational bound DB без реального
SourcePolicyRuntime отклоняется, даже если кто-то передал static snapshot.

## Локальный operator workflow

Новая activation явно создаёт первую installation policy. Для существующего pre-policy store
нужен отдельный reviewed enrollment. Для привязки к уже существующему trusted ledger:

```bash
uv run --no-sync satori sources enroll --reviewed
uv run --no-sync satori sources status
```

Если installation policy создаётся впервые и независимая continuity проверена оператором,
используется `sources enroll --reviewed --new-installation`. Здесь installation означает
первую установку policy floor, а не отсутствие прежней истории Сатори.
`--new-installation` предусмотрен только для действительно впервые unbound policy installation.
Он не является восстановлением потерянного floor ранее enrolled lineage. Enrollment сохраняет
attribution/proof ограничения и выполняет owner reconciliation; это не перезапуск истории
с нулевой privacy policy.

Target выбирается в настроенном counterparty partition. Команды возвращают metadata, а не
текст источника. Сначала следует проверить exact scope/effects/generation/digest:

```bash
uv run --no-sync satori sources exclude MESSAGE_ID --preview
uv run --no-sync satori sources exclude MESSAGE_ID --apply \
  --expected-generation GENERATION_FROM_PREVIEW \
  --sha256 DIGEST_FROM_PREVIEW --request-id STABLE_LOCAL_REQUEST_ID
```

Apply требует все три review bindings. Digest связывает target/scope, installation/floor,
generation, policy version и effects; trace/time/request ID не подменяют проверку. Перед receipt
lookup доказывается canonical target scope. Повтор того же request/payload возвращает исходную
exclusion и возобновляет нужную repair; другое содержимое с тем же request ID отклоняется.
Stale generation или неверный digest не выполняют новый floor commit.

`phase=complete` означает завершённую repair, exit code 0. `phase=exclusion_committed_repair_pending`
означает durable exclusion при незавершённой repair, exit code 2. Не следует повторять операцию
с выдуманной новой generation или удалять ledger, чтобы обойти это состояние:

```bash
uv run --no-sync satori sources repair --request-id STABLE_LOCAL_REQUEST_ID
```

`sources status` показывает floor/lineage IDs, generation и число excluded sources; он не
сертифицирует завершённость каждой owner repair. Метка `local_operator` в receipt — обозначение
локального approval workflow, а не реализованная user authentication или technical scoped grant.

## Проверка и ограничения acceptance

Control/admission/repair добавляют ноль LLM calls. Их локальную стоимость следует измерять
отдельно от provider latency: floor/binding validation, SQL selection, dispatch, final commit,
closure и owner reconciliation. Здесь не заявлен неподтверждённый latency budget.

Memory/semantic regressions используют fake providers и controlled synthetic DAG: exclusion
до LIMIT, paired reply и uncited actual input, early replay denial, owner key retirement,
late first-SQL-write rollback, audit failure, legacy UNKNOWN, отсутствующая current receipt,
owner attribution failure, независимый replacement, archive/reactivate и queued ABA. Эти тесты
не являются оценкой Character или доказательством native ledger/process safety.

Native production-composition scenario, actual cross-process macOS lock checks, restore/restart,
adapter retry/queue admission и полный Foundation учитываются отдельно в [progress.md](progress.md).
Полный Foundation и macOS process/scenario gates пройдены. Linux adapter реализован, но фактическое
Linux process execution на текущем macOS host не проверено. Итоговые passing counts и границы
готовности записаны в progress; Linux process gate и полная privacy acceptance 14.4 остаются открыты.

Связанные документы: [memory lifecycle](memory-lifecycle.md), [recovery](recovery.md),
[read-only erasure preview](erasure.md),
parallel workstream dependency map,
source eligibility design, [roadmap](roadmap.md).


## Approved body action head — ADR-0061

Явный policy_0003 upgrade сохраняет source generation и intent erasure_revision, добавляя
независимый body_revision и opaque body_head_digest. Fresh body approval фиксирует private
selectors/fingerprints до первой мутации. Нормальный provider/context/job admission проверяет
source mirror, intent preparation и materialized body head независимо. Source repair и intent
resume не могут подтвердить удаление тел. Restore после intent, но до body application, теперь
блокируется даже при current source/intent heads. Explicit body resume проверяет фактическое
состояние всех effective targets; исходный intent сам ничего не удаляет.

Первое покрытие — [эпизодические summary/quotes и все их vectors](erasure-bodies.md).
Следующее реализованное покрытие — terminal dialogue по ADR-0063 ниже. Другие
knowledge/appraisal/audit bodies и внешние копии сохраняют прежнюю exclusion policy и явно
остаются residual surfaces. UNKNOWN episode ancestry блокирует body readiness без удаления
чужих/неатрибутированных данных. Character использует прежние eligibility contracts.

## Terminal dialogue и historical provenance — 14.4.2f / ADR-0063

Реализация [ADR-0063](decisions/0063-terminal-dialogue-bodies-and-historical-provenance.md)
завершена; macOS engineering gate 2f пройден (4207 passed / 5 skipped) и записан отдельно в
[progress.md](progress.md). Это не новый verdict Character и не приёмка всего 14.4.

InteractionLog записывает actual SQL NULL для выбранного excluded canonical USER, его paired
assistant и всех доказанных COMPLETE-dependent assistant replies. Independent later USER
сохраняются. UNKNOWN assistant ancestry и affected cross-counterparty replies блокируют fresh
plan; отсутствующие зависимости не восстанавливаются по сходству текста. Выбранные episode
dependencies должны уже иметь действительное terminal episode proof.

Отдельное operation-specific approval фиксирует точные preimages и closed historical witnesses
в independent floor до первой main mutation. Explicit resume материализует текущий effective
approved mixed head атомарно через существующих владельцев, включая поздние approved actions
или supersession, без расширения за пределы этого head. Pre-commit failure откатывает main
transaction; uncertain commit outcome или ошибка последующей проверки сообщает pending, а не
доказывает доступность прежних bodies. Actual status/resume proof подтверждает завершение.
Actual NULL bodies, неизменный retained skeleton,
точные witness rows и полный action-head digest проверяются независимо от ACK. Wrong/forged ACK,
receipt без удаления и old-main backup не дают ordinary history/context/provider/job admission;
source repair и intent preparation не подтверждают body head и не запускают его исполнение.

Main `0019_dialogue_bodies` имеет exact schema pin: 59 tables, 13 triggers, digest
`69c87475bb8ec301ed0d723aa0902f359c14f6d0def5b1c13c340a659f2c3611`.
Floor `policy_0004` сохраняет single mixed body head и прежние exact episode hashes.
Проверки body admission не мигрируют floor/main schema и не делают implicit repair.

Historical witness связывает closed evidence table/ID с fingerprint всей точной retained row.
Memory/position quote membership доказывается до NULL, quote-free model/inclination evidence —
через точную canonical/scope/source chain. Already-erased episode edges используют approved
episode proof. Changed/new evidence не наследует witness. Этот контракт допускает только
историческую attribution; live evidence, новое learning и provider content по-прежнему требуют
available body и обычный source admission. Metadata-only header не превращается в пустую
canonical реплику или replacement text при replay.

Control operations вызывают ноль providers. Provider/model/Character policies не меняются;
Character читает только already-admitted available history через прежний abstract interface.
Покрытие остаётся partial logical removal с `physical_erasure=false`: knowledge/numeric/audit
history, SQLite free pages/sidecars, archives и external copies не очищаются. Authenticated access,
encryption/key recovery, whole-floor freshness и actual Linux process gate остаются открытыми.
Подробный operator contract: [terminal dialogue bodies](erasure-dialogue.md).

## Terminal derived knowledge — 14.4.2g / ADR-0066

The separately opened knowledge slice extends the same source/intent/body barriers through
explicit `policy_0005` and a closed `knowledge_body_erasure_v1` envelope. SourceEligibilitySnapshot
adds no counter. Existing owners remove selected claim values, position propositions/quotes and
inclination topics, retaining identifiers, versions, numerical history, evidence/revisions and caps.
JSON scalar number/boolean values count as source content and become SQL NULL.

Exact before/after position evidence fingerprints link preceding Dialogue/Knowledge approvals
or explicit same-scope live origin. This proves retained historical attribution only; available
context and fresh learning require independent live-source/body admission. Ordinary main admission
proves the complete floor head and all actual terminal material. Exclusive reviewed preview may
inspect a pending projection without writing an ACK. Owner current-input admission independently
checks available bodies even when aggregate versions have not changed.

Cross-counterparty support and UNKNOWN ancestry block scoped body execution. Historical actions
and their hashes remain unchanged. Provider, Character and permission semantics remain separate.
Controls, production verification status and residuals are in [erasure knowledge](erasure-knowledge.md).

## Terminal Reflection payloads — 14.4.2h / ADR-0068

The existing Reflection owner removes complete selected proposal JSON through the same mixed
body head, explicitly extended by `policy_0006`. Main `0021_reflection_bodies` retains and freezes
the entire completed/exhausted parent run, fixed sources, attempts, outcomes and proposal headers.
An uncited root in the fixed window or COMPLETE actual-input closure can select a whole proposal;
candidate citations alone cannot prove independence. Unknown or affected foreign support blocks
destructive approval. Pending resume rechecks strict upstream causal proof before any owner write.

Normal reads and dispatch/final commit separately require available bodies at immutable payload
version 1. Completed apply replay returns metadata before payload or live quote reads. Terminal
inspection returns a separate header, never an empty contentful candidate. Ordinary installation
admission proves actual NULL and exact retained material before historical Dialogue witness reads;
source repair and intent resume cannot acknowledge this body head. Conflicting SQL replacement,
changed parent membership, forged receipts and restored original bodies remain denied.

This is logical content removal, with `physical_erasure=false`, and does not undo numerical
learning or spent budgets. No Character implementation, provider, expression or emotion policy
changes. Exact contracts, scenario results and remaining surfaces are in
[retained Reflection handling](erasure-reflection.md).

## Paired appraisal explanation disposition — 14.4.2h / ADR-0070

A transition now prospectively retains its exact prepared actual-input receipt and owned audit
event. Managed contentful reads require historical eligibility for that actual receipt, including
uncited dependencies; numerical history is separately retained. Legacy links are not guessed.

The fifth closed body action through explicit `policy_0007` removes only SQL reason bodies and
the exact audit explanation key. Whole input closure is fingerprint-bound independently of
mutable eligibility/policy-generation mirrors. Mixed-head preflight, sole-owner paired writes
and main acknowledgement remain one transaction under the existing floor-first installation
barrier. Ordinary admission proves actual terminal state before historical protected reads.
No new source-generation counter, generic payload kind, Character prompt or affect formula is
introduced. Numeric influence and unrelated audit routes remain distinct residuals.
See [paired explanation handling](erasure-appraisal.md).

## Retained audit metadata — 14.4.2h / ADR-0071

The read-only `sources audit-metadata MESSAGE_ID --status` control proves the configured
counterparty's canonical root before private census. It returns closed family/reason counts,
coverage and freshness revisions without raw event names, selectors, JSON or explanation text.
It constructs no cognitive providers and neither migrates nor enrolls an installation.

Supported prospective bindings certify only closed accountability metadata against exact owner
receipts. Supplied COMPLETE actual-input ancestry is checked; no link is guessed when absent.
Source exclusion does not discard valid retained accountability metadata, and its presence does
not allow excluded information into retrieval, prompts, owner learning or Character context.
UNKNOWN coverage adds an erasure completeness blocker, not a new conversation permission gate.
Existing source exclusion and complete mixed-head admission remain authoritative.
See [retained audit metadata](retained-audit-metadata.md).
