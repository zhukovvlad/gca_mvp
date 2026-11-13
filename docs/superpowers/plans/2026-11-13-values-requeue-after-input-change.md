# План: перезапрос значений после изменившегося входа

**Спека:** `docs/superpowers/specs/2026-11-13-values-requeue-after-input-change-design.md` (гейт 2 одобрен 13.11.2026, редакция 2, `9128dcb`)
**Дизайн:** `docs/superpowers/design/2026-11-13-values-requeue-after-input-change.md` (редакция 3)
**Ветка:** `fix/values-requeue-after-input-change`, черновой PR #77
**Статус:** редакция 2, 13.11.2026. Круг 1 Codex (на `e476343`): потоков
исполнителя четыре, а не один — замок новой функции проверяется гонкой двух
сессий (решение 5), снятия С13а/С13б снимают и переносят сам вызов в новой
функции, а не режим помощника.

> Исполнителю: задачи по порядку; Task 1 — цикл TDD
> (`superpowers:test-driven-development`: тесты первыми, красный прогон
> показать). Адреса `§N` без уточнения — разделы спеки, «проверка N» — её §3;
> «решение N» — решения этого плана. Защит, дублирующих правило, не класть.
> ДО сняты 13.11.2026 на `9128dcb`: шаблон `-k "values_requeue or ValuesRequeue"`
> выбирает **0** тестов из 12 774 в `tests/unit` и `tests/integration`;
> затронутые наборы (команда в Task 1) — **1 107** тестов.

## Режим реализации (ускоренный, как в прошлой фиче)

- **Исполнитель** Task 1 — Sonnet; оркестратор (Opus) принимает, коммитит,
  ведёт `progress.md`. Task 2 — исполнитель Sonnet на правку фронта,
  стенд и devlog — оркестратор.
- **Ревью — только у Task 1** (очередь, барьер, замки): Opus с узким
  брифом — правила §2.1–§2.5 и «что сделало бы этот тест ложно-зелёным».
  Task 2 — однострочная правка класса, ревью не нужно, проверка — снимки.
- **Снятия защит делает оркестратор лично** — таблица ниже, в копии дерева,
  по протоколу `BASELINE_GREEN / MUTATION_APPLIED / EXPECTED_TEST_FAILURE /
  RESTORED`, `-n 1`, жёсткий таймаут на прогон; итог — в `progress.md` и
  devlog. Отступление от `docs/process/implementation.md` (снятия — ревьюером)
  по решению пользователя 28.10.2026, как в прошлых фичах; процессный
  документ не меняется.
- **Прогоны.** Исполнитель в итерациях гоняет только тесты задачи; затронутые
  наборы — один раз в конце Task 1; ревьюер их не повторяет. Полный бэкенд и
  `just ci` локально не гоняются — GitHub Actions на PR (`gh pr checks 77`).
- **Без остановок** между задачами; стоп — пуш и внешнее ревью.

## Global Constraints

- Схема БД, миграции, форма ответов API, `RECONCILE_ALLOWLIST` — без изменений
  (§2.6). Исполнитель очереди (`services/semantic_worker.py`) домен не пишет.
- Сигнатуры существующих функций меняются только добавлением именованного
  параметра со значением по умолчанию, сохраняющим нынешнее поведение
  (`values_barrier=False`).
- Существующие тесты затронутых наборов зелёные **без правок**, кроме тех, что
  ломаются ровно потому, что проверка конца пачки теперь ставит контексту
  задание по его собственной нужде (§2.2), — каждый такой тест исполнитель
  перечисляет в отчёте с причиной, ревьюер подтверждает, что причина — эта.
  Ожидаемый кандидат: `TestExtensionWave.test_jobs_that_are_not_pending_or_running_do_not_stop_the_wave`
  в `test_work_variants_reconcile.py` (у контекста `other` нет строки
  параметра → нужда → задание; было `posted == 1`). Любая другая поломка —
  дефект реализации.
- Субагентам: кириллицу в терминал не печатать, вывод pytest — в файл,
  `PYTHONIOENCODING=utf-8`.

## Структура файлов

- Edit: `backend/services/semantic_reconcile.py` — `values_batch_in_work`,
  `values_batch_candidates`, `reconcile_after_values_batch`, параметр
  `values_barrier` у `reconcile_semantic_jobs` и протяжка до
  `_plan_values_jobs`, `schedule_extension_wave` по §2.4 п. 2.
- Edit: `backend/services/work_variants.py` — обе сверки `apply_values` при
  `guard is not None` — с барьером (§2.4а).
- Edit: `backend/services/semantic_worker.py` — сбор семей заданий значений
  в `claim_next` и `process_one`, вызов проверки конца пачки в `finally`;
  барьер семьи в `_sweep_context_values`.
- Create: `backend/tests/integration/test_values_requeue.py`.
- Edit (правка ожиданий по Global Constraints, если понадобится):
  `backend/tests/integration/test_work_variants_reconcile.py`.
- Edit: `frontend/src/pages/families/schema-edit/ParameterEditor.tsx`,
  `frontend/src/pages/families/schema-edit/ParameterEditor.test.tsx`.
- Create: `docs/devlog/2026-11-13-values-requeue-after-input-change.md`.

## Решения плана, которых нет в спеке

1. **Две задачи:** весь бэкенд — Task 1 (барьер, проверка конца пачки, волна
   и сверка обработчика друг без друга не проверяются); переключатели, стенд
   и devlog — Task 2.
2. **Все новые бэкенд-тесты — в одном файле `test_values_requeue.py`**,
   классы с префиксом `TestValuesRequeue…`. Сценарии через исполнитель
   (проверки 1–5, 7–9, 11, 12, 14) — на закоммиченных сессиях:
   `process_one(committing_session_factory, client, …)` с клиентом-сценарием
   по образцу `_FakeClient` из `test_semantic_queue_worker.py` (класс
   копируется в новый файл, а не импортируется: он приватный); тест замка
   (решение 5) — на двух закоммиченных сессиях из
   `committing_session_factory`; проверки на функциях (6, 10, 13) — на
   `db_session`.
3. **«Задание в работе» другого контекста** в сценариях барьера — задание
   `running`, захваченное `claim_next` и не доведённое до записи результата,
   либо `pending` с `next_attempt_at` в будущем (исполнитель его не
   захватывает). Что выбрано — в докстринге класса.
4. **Подставная модель различает задания по контексту в теле запроса**, а не
   по порядку вызовов: порядок захвата — деталь `claim_next`, на которую
   тест опираться не должен.
5. **Замок семьи в `reconcile_after_values_batch` проверяется гонкой двух
   сессий, а не компиляцией помощника.** Потоков исполнителя
   `SEMANTIC_CONCURRENCY` (по умолчанию 4, `SemanticRunner`), и проверки
   конца пачки идут параллельно; компиляция `_lock_families_statement`
   доказала бы режим существующего помощника, но не то, что новая функция
   его вызывает, и вызывает до чтения пачки (ревью Codex гейта 3). Сценарий
   «чтение пачки — после замка», две закоммиченные сессии:
   - сессия A берёт семью тем же оператором (`FOR UPDATE`), вставляет
     задание значений семьи `pending` и **не коммитит**;
   - сессия B в потоке зовёт `reconcile_after_values_batch`; тест ждёт, пока
     B встанет на замок (`pg_stat_activity.wait_event_type = 'Lock'` по её
     `pid`, с таймаутом — шаблон `test_catalog_discovery_races.py`), и
     только потом коммитит A;
   - B возвращает `0` и заданий не ставит: пачка в работе (задание A).

   Без замка B не ждёт (ожидание `Lock` не наступает — тест падает по
   таймауту ожидания, не зависая); с замком **после** чтения пачки B успевает
   прочитать «пачки нет» до коммита A, затем ждёт замок и ставит задания —
   тест падает на «заданий нет». Отрицательная половина: тот же сценарий без
   задания у A — B после коммита A ставит задания кандидатам.
6. **Проверка 3 краснеет до починки.** Первым коммитом задачи в рабочее
   дерево кладётся только тест проверки 3; исполнитель прогоняет его на
   нетронутом коде и прикладывает вывод (ожидается: заданий волны нет). Если
   он зелёный — стоп, к оркестратору: дыра 2 вычёркивается (§3 проверка 3).

## Обязательные снятия (оркестратор, после приёмки Task 1)

| # | Проверка | Что снять | Что обязано покраснеть |
|---|---|---|---|
| С1 | 4 | сбор семьи на пути ошибки вызова (`record_failure`) в `process_one` | последнее задание с постоянной ошибкой — волны нет |
| С2 | 4 | сбор семьи на пути невалидного ответа (`_record_schema_error`) | последнее задание с невалидным ответом — волны нет |
| С3 | 2 | сбор семей заданий, отменённых `claim_next` | отмена при захвате — повтора нет |
| С4 | 5 | проверку «пачка в работе» в `reconcile_after_values_batch` | задания ставятся при открытом задании семьи |
| С5 | 9 | исключение `privacy_hold` из «пачки в работе» (считать его открытым) | `privacy_hold` держит пачку — повторов нет |
| С6 | 7 | кандидаты — только контексты с отмеченной строкой | новый параметр без строк — повтора нет |
| С7 | 12 | кандидаты — отмеченные или без строки (без расхождения путей) | оживлённое старое задание — повтора нет |
| С8 | 13 | `without_discarded_values` в проверке конца пачки | отброшенный предмет вернулся |
| С9 | 3 | `schedule_extension_wave`: ранний выход до отметки при пачке в работе (как на `main`) | волна теряется, расширившее задание не последнее |
| С10 | 11 | учёт `values_barrier` в `_plan_values_jobs` | повтор по A посреди пачки |
| С11 | 11 | `values_barrier=True` в сверках `apply_values` (передать `False`) | то же |
| С12 | 8 | барьер семьи в `_sweep_context_values` | проход ставит повтор посреди пачки |
| С13а | замок (решение 5) | вызов замка семьи в `reconcile_after_values_batch` (помощник `_lock_families_statement` не трогать) | B не встаёт на замок — падение по таймауту ожидания `Lock` |
| С13б | замок (решение 5) | перенос замка в `reconcile_after_values_batch` **после** `values_batch_in_work` | B ставит задания при пачке в работе — падение на «заданий нет» |
| С14 | 14 | перехват исключения проверки в `process_one` | исключение выходит из `process_one` |

## Задачи

### Task 1: барьер пачки, проверка конца пачки, волна и сверка обработчика — **ревью**

**Files**
- Edit: `backend/services/semantic_reconcile.py`,
  `backend/services/work_variants.py`, `backend/services/semantic_worker.py`
- Test: `backend/tests/integration/test_values_requeue.py`; правка ожиданий —
  по Global Constraints

**Interfaces**
- Потребляет: `reconcile_semantic_jobs`, `_plan_all`, `_plan_context_values`,
  `_plan_values_jobs`, `_plan_postanovka`, `_plan_applicable`,
  `values_need_of`, `load_value_rows`, `_load_values_columns`,
  `_values_applicable`, `without_discarded_values`, `schedule_extension_wave`,
  `event_cap_from` (`services/semantic_reconcile.py`); `load_values_material`,
  `load_request_material`; `apply_values`, `mark_rows_for_refresh`
  (`services/work_variants.py`); `_lock_families_statement`
  (`services/work_families.py`); `claim_next`, `_note_closed`, `_cancel`,
  `process_one`, `_run_claim`, `record_result`, `record_failure`,
  `_record_schema_error`, `_reconcile_schemas_of_unit`,
  `_sweep_context_values` (`services/semantic_worker.py`); фикстуры
  `committing_session_factory`, `committing_db`, `committing_factories`,
  `db_session`, `factories`.
- Производит:

```python
# services/semantic_reconcile.py
def values_batch_in_work(db: Session, family_id: int) -> bool
def values_batch_candidates(db: Session, family_id: int) -> list[int]
def reconcile_after_values_batch(db: Session, *, family_id: int) -> int

def reconcile_semantic_jobs(
    db: Session, context_ids: Collection[int], *, cap: EventCap | object, source: str,
    import_job_id: int | None = None, values_barrier: bool = False,
) -> ReconcileReport

# services/semantic_worker.py
def claim_next(
    db: Session, *, settings: Settings, now: datetime,
    closed_units: list[int | None] | None = None,
    closed_values_families: list[int] | None = None,
) -> Claim | None
```

**Утверждения**
- `values_batch_in_work` — ровно §2.1: `pending`/`running` задания
  `context_values` версии схемы семьи; `privacy_hold`, `done`, `cancelled`,
  `error` — нет; задание другой семьи — нет.
- `values_batch_candidates` — ровно §2.2: неархивные контексты семьи
  (`coalesce(pending_family_id, work_family_id)`), применимые
  (`_values_applicable`), с непустыми `ordinals` или `variant_due` по
  `values_need_of`, после `without_discarded_values`; по возрастанию `id`.
- `reconcile_after_values_batch` — ровно §2.3: семья `FOR UPDATE` оператором
  `_lock_families_statement(..., exclusive=True)`; пачка в работе → `0` без
  записей; иначе сверка кандидатов с `cap=event_cap_from(settings)`,
  `source="operation"`; возвращает `created + revived` отчёта; пустые
  кандидаты → `0` без вызова сверки. Не коммитит.
- `schedule_extension_wave` — §2.4 п. 2: архивный параметр или не
  действующая версия → `0` без отметок (как сейчас); иначе всегда
  `mark_rows_for_refresh` пустых строк (`none`/`off_list`) параметра у
  контекстов семьи, затем `return reconcile_after_values_batch(...)`.
- `reconcile_semantic_jobs(..., values_barrier=True)` — §2.4а: ветка
  `context_values` для контекста, у семьи которого пачка в работе, не кладёт
  в план ни создание, ни оживление; отмены нетекущих отпечатков и остальные
  виды — как при `False`. При `False` поведение побайтно прежнее (все
  существующие тесты сверки зелёные).
- `apply_values` при `guard is not None` — обе сверки (шаг 8 и ветка
  «ожидаемая семья недоступна») через `reconcile_semantic_jobs(db,
  [context_id], cap=event_cap_from(settings), source="operation",
  values_barrier=True)`; при `guard is None` — `reconcile_or_defer`, как
  сейчас.
- `claim_next` дописывает в `closed_values_families` семью каждого задания
  `context_values`, которое он отменил (`_cancel`) или перевёл в
  `privacy_hold`, — рядом с каждым нынешним `_note_closed`.
- `process_one` копит семьи заданий значений: из `claim_next` и семью
  захваченного задания значений при любом исходе `_run_claim`; в `finally`
  для каждой семьи (без повторов, порядок первого появления) — новая сессия,
  `reconcile_after_values_batch`, `commit`; исключение — `logger.exception`,
  наружу не уходит, следующая семья обрабатывается.
- `_sweep_context_values` пропускает контекст, у семьи которого пачка в работе
  (`NOT EXISTS` по заданиям `context_values` `pending`/`running` версий схем
  семьи `coalesce(pending_family_id, work_family_id)`), в дополнение к
  нынешнему условию.
- Проверки 1–14 спеки — каждая отдельным тестом (проверка 4 — двумя: ошибка
  вызова и невалидный ответ; проверка 5 — с `pending` и с `running`), плюс
  тест замка (решение 5). Каждый тест барьера несёт и отрицательную
  половину: до конца пачки повтора нет, после — ровно одно задание на
  контекст.
- Тест замка (решение 5): две закоммиченные сессии, ожидание `Lock` у B
  наблюдается до коммита A; при задании A в пачке — B возвращает `0`, без
  него — ставит задания. Каждое ожидание с таймаутом; поток B — `join` с
  таймаутом, зависание = падение.
- Проверка 1, кроме нового задания: строк `context_parameter_values` и
  отметок у отменённого контекста отмена не добавила и не изменила
  (снимок до и после).
- Проверка 3 краснеет на нетронутом коде (решение 6), вывод — в отчёте.

**Имена**
- Заводятся этой задачей: `values_batch_in_work`, `values_batch_candidates`,
  `reconcile_after_values_batch`, параметры `values_barrier`,
  `closed_values_families`, файл `test_values_requeue.py`, классы
  `TestValuesRequeue…`.
- Существуют, проверено `grep`-ом 13.11.2026: `reconcile_semantic_jobs`,
  `reconcile_context_values`, `_plan_values_jobs`, `_plan_postanovka`,
  `_plan_applicable`, `values_need_of`, `load_value_rows`,
  `_load_values_columns`, `_values_applicable`, `without_discarded_values`,
  `_revive_jobs`, `schedule_extension_wave`, `reconcile_or_defer`,
  `event_cap_from`, `apply_values`, `mark_rows_for_refresh`,
  `_close_job_unapplied`, `_lock_families_statement`, `claim_next`,
  `_note_closed`, `_cancel`, `process_one`, `_run_claim`, `record_result`,
  `record_failure`, `_record_schema_error`, `_reconcile_schemas_of_unit`,
  `_sweep_context_values`, `sweep_semantic_queue`, `_FakeClient`
  (`test_semantic_queue_worker.py`), `committing_session_factory`.

**Проверка**
- `cd backend && TEST_DATABASE_URL=… uv run pytest tests/unit tests/integration -k "values_requeue or ValuesRequeue" -v`
  — зелёная; выбирает **0** тестов ДО и не меньше **18** ПОСЛЕ (14 проверок,
  4 и 5 по два теста, замок — два; число — нижняя граница).
- Затронутые наборы, один раз в конце задачи:
  `uv run pytest -n 4 tests/integration/test_catalog_parameters_values.py tests/integration/test_work_variants_reconcile.py tests/integration/test_work_variants_worker.py tests/integration/test_semantic_queue_sweep.py tests/integration/test_semantic_queue_worker.py tests/integration/test_catalog_parameters_rebuild.py tests/integration/test_work_variants_family_change.py tests/integration/test_analog_zone_job.py tests/integration/test_simplify_auto_split.py tests/integration/test_work_variants_auto_accept.py tests/integration/test_catalog_discovery_schema_timing.py tests/unit/test_semantic_queue_architecture.py tests/integration/test_values_requeue.py`
  — **1 107** ДО, 1 107 + новые ПОСЛЕ, все зелёные (правки ожиданий — по
  Global Constraints, перечислены).
- `uv run ruff check .` — чисто.

### Task 2: переключатели, стенд и devlog

**Files**
- Edit: `frontend/src/pages/families/schema-edit/ParameterEditor.tsx`,
  `frontend/src/pages/families/schema-edit/ParameterEditor.test.tsx`
- Create: `docs/devlog/2026-11-13-values-requeue-after-input-change.md`

**Interfaces**
- Потребляет: `ToggleGroupItem` (`components/ui/toggle-group.tsx`), класс
  нажатого из `pages/families/ZonesQueue.tsx`; стенд `gca_dev`, бэкенд :8260
  (перезапуск — `Start-Process`, без `--reload`), фронт :5175, снимки —
  системный Chrome через пакет `playwright` (MCP не работает).
- Производит: класс нажатого у элементов двух групп; devlog.

**Утверждения**
- §2.8: все `ToggleGroupItem` групп «Роль» и «Вид значений» несут
  `aria-pressed:bg-primary aria-pressed:text-primary-foreground`; тест
  компонента проверяет класс у нажатого элемента обеих групп (проверка 15),
  как `ZonesQueue.test.tsx`.
- Снимки окна «Править схему» в светлой и тёмной теме — нажатое отличимо
  (проверка 15); снимки — в `tasks/values-requeue/`, не в гите.
- Стенд §4: правка схемы с новым параметром на семье с несколькими
  контекстами; время от конца пачки до постановки повторов и их число — в
  devlog. Модель зовётся платно: сценарий — на той семье и в том объёме, на
  который пользователь согласится; без согласия — только снимки и журнал
  исполнителя, и это прямо записано в devlog.
- Devlog: что сделано, отступление спеки от дизайна (§1), итог снятий
  С1–С14, правки ожиданий существующих тестов с причинами, стенд, тронутые
  области и прочитанные файлы граблей (`docs/pitfalls/backend.md`,
  `docs/pitfalls/db.md`, `docs/pitfalls/runs.md`, `docs/pitfalls/frontend.md`)
  — AGENTS §10.

**Имена**
- Заводятся этой задачей: файл devlog.
- Существуют: `ParameterEditor`, `ToggleGroupItem`, `ZonesQueue`.

**Проверка**
- `cd frontend && npx vitest run src/pages/families/schema-edit` — зелёная.
- `npm run lint` и `npx tsc --noEmit` — чисто.
- `just check-agents-index` — без новых провалов.

## Команды проверки

- По задаче: в самой задаче.
- По фиче целиком: GitHub Actions на PR #77 (`gh pr checks 77`); стенд —
  Task 2.
