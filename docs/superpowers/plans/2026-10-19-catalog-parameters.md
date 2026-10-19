# План: фича 3в «Параметры семьи без предела»

**Спека:** `docs/superpowers/specs/2026-10-19-catalog-parameters-design.md` (гейт 2 одобрен 19.10.2026, три круга Codex, редакция 3, `6602b0a`)
**Ветка:** `feat/catalog-parameters`, черновой PR #71
**Гейт 3 одобрен пользователем 20.10.2026** после второго круга ревью Codex в
PR #71 (коммит `a1d4b7d`: три замечания круга 1 закрыты, новых блокирующих
нет). Следующий шаг — реализация.

**Редакция 2, 19.10.2026.** Круг 1 гейта 3 —
3 замечания Codex на `086d765`, все подтверждены по коду: `PATCH` и
предпросмотр правки переключаются в Task 4, а не в Task 6 (иначе маршрут
вызывал бы удалённый `update_schema`); Task 7 передаёт предел в
`_record_variant_result` и заводит путь без модели при `H = 10`, проверки —
через настоящий путь обработки ответа; проверка предела 10 и проверка
сериализации правок в Task 4 разделены, снятия названы по цепочке замков.

**Редакция 1, 19.10.2026.**
**Макет:** [`docs/superpowers/design/2026-10-19-catalog-parameters/schema-dialog.html`](../design/2026-10-19-catalog-parameters/schema-dialog.html) — десять сценариев; фронт (Task 8) и стенд (Task 9) идут по нему.

> Исполнителю: задачи идут по порядку; каждая — цикл TDD
> (`superpowers:test-driven-development`) и ревью задачи
> (`docs/process/implementation.md`) до следующей. Адреса `§N` без уточнения —
> разделы спеки; «DoD N» — пункт §5 спеки. Числа проверок — накопительные
> нижние границы; ДО сняты 19.10.2026 на `6602b0a` командами
> `uv run pytest tests --collect-only -q -k <шаблон>` (бэкенд, из `backend/`) и
> `npx vitest list src/pages/families` (фронт, из `frontend/`). Новые тестовые
> файлы бэкенда называются `test_catalog_parameters_*.py` — так их выбирает
> шаблон `catalog_parameters`.

## Global Constraints

- **Права**: все новые и изменённые маршруты — `Depends(require_admin)`,
  `member` получает `403`; сторож `tests/test_auth_coverage.py` зелёный после
  каждой задачи с маршрутами (§2.10).
- **Порядок блокировок модуля** (`services/work_variants.py:5-8`, §2.5): семья
  `FOR UPDATE` первой у **каждого** писателя схемы и у записи ответа
  (`_lock_family_for_schema_life`, `_acquire_domain_locks` →
  `_lock_families(..., exclusive=True)`) → действующая версия → параметры и
  значения по id → варианты по id → контексты по id → задания последними.
  Повышение режима замка внутри транзакции запрещено.
- **Байтовая идентичность** (§2.4, §2.8, DoD 2): тело `context_values` полной
  схемы без диапазонов и тело `family_schema` без параметров человека —
  прежние до байта; снимок `request_hash` на фикстуре снимается **до** правок
  Task 3 и Task 7.
- **Наружу уходят только** имена параметров и списки значений (`AGENTS.md`
  §3); вид задания не добавляется — частичный запрос остаётся
  `context_values` (§2.12). Ревизии `AGENTS.md` нет.
- **Ничего не удаляется** (спека вариантов §2.4): параметр архивируется
  (`archived_at`), значение выводится (`retired_at`); перестройка не пишет
  `merged_into_id` (§2.6).
- **Номер параметра внутри версии не повторяется** (§2.2); предел 10 активных
  — под замком семьи.
- **Перечисления и CHECK** — литералами в миграции `0022`, паритет с
  `models.py` держит тест; каждая равносильность CHECK записана тотально, без
  атома, дающего `NULL` (`docs/insights/state-the-rule-as-an-equivalence.md`).
- **Каждый писатель действующей версии на месте увеличивает `revision`**
  (§2.2): правка, слияние значений, «не из списка», расширение моделью,
  косметика.
- **Ключи существующих ответов API** не удаляются и не переименовываются —
  только добавляются.
- **Каждый новый код отказа** — в картах статусов `routers/semantic.py`
  (`_STATUS_NOT_FOUND`/`_STATUS_CONFLICT`/`_STATUS_UNPROCESSABLE`; незнакомый
  код — `AssertionError`) и в `SCHEMA_REFUSAL_LABEL`
  (`frontend/src/pages/families/labels.ts`).
- **Фронтенд** — только shadcn/ui; нужные примитивы установлены
  (`toggle-group`, `checkbox`, `badge`, `dropdown-menu`, `dialog`, `input`);
  базовый `DialogContent` не трогается.
- **Стенд**: только чтение из сессии; записи в учётки `gca_dev` готовит
  скрипт, запускает пользователь; опросчик включает пользователь (Task 9).

## Review Focus

Входы, которые спека подразумевает, а задачи легко пропустить; тест на каждый
стоит в задаче-владельце:

1. **Контекст в процессе ответа во время правки** — ждущее и `running`
   задание значений семьи, чей рендер правка изменила или не изменила: первое
   пересчитано, второе нет; `running` записывается `stale_fingerprint` только
   при изменившемся рендере (Task 3, Task 4).
2. **Контекст со сменой семьи** (`pending_family_id`) в момент правки схемы
   целевой семьи — строки прежней семьи удаляются при записи ответа, а не
   сохраняются как «известные» (Task 3).
3. **Число в наименовании с единицей и запятой** — «131,73 кг/м3», «5,1»,
   «4 мм» — разбирается одной функцией и в ответе модели (§2.4), и в
   перестройке (§2.6); «до 100 мм», «40-50», «1/2» — не число (Task 3).
4. **Пустой список точных значений** у параметра, переведённого из
   диапазонов в точные, — допустим, все контексты «уточняется» (Task 5).
5. **Окно на узком экране и при 10 параметрах** — сводка и «Сохранить»
   видны, колонки прокручиваются каждая сама; замер в браузере (Task 8,
   Task 9).

## Структура файлов

```text
backend/
  alembic/versions/2026_10_19_0022-catalog_parameters.py   Create — §2.2
  models.py                                                Edit — колонки, перечисления, CK_*
  services/parameter_ranges.py                             Create — числа и диапазоны (§2.4, §2.6)
  services/work_variants.py                                Edit — ключ из главных, пересчёт, запись частичного ответа,
                                                                  слияние, правка на месте, пересборка
  services/schema_edit.py                                  Create — план правки, предпросмотр, применение (§2.5–§2.7)
  services/semantic_reconcile.py                           Edit — needed_ordinals, variant_due, план, волна
  services/variant_request.py                              Edit — рендер подмножества, строка правила, блок человека
  services/variant_answer.py                               Edit — разбор по подмножеству, предел N схемы
  services/semantic_worker.py                              Edit — рендер по заданию, запись, завершение без модели
  services/semantic_decisions.py                           Edit — сопоставление пачек по подмножеству
  services/semantic_events.py                              Edit — family_schema_edited
  crud/work_variants.py                                    Edit — SchemaOut, варианты, карточка, предпросмотры
  routers/semantic.py                                      Edit — PATCH, edit-preview, off-list/resolve, коды
  tests/integration/test_catalog_parameters_*.py           Create — по задачам
  tests/unit/test_catalog_parameters_ranges.py             Create
  tests/integration/test_semantic_api.py                   Edit — литералы маршрутов
frontend/src/
  pages/families/SchemaEditDialog.tsx                      Rewrite — «Список и редактор»
  pages/families/schema-edit/*.tsx                         Create — список, редактор, сводка, диапазоны, «не из списка»
  pages/families/SchemaBlock.tsx, VariantsTable.tsx,
    ContextVariant.tsx, FamiliesTab.tsx (MergeFamilyDialog),
    labels.ts                                              Edit
  types/domain.ts, services/api/domain.ts,
    services/queries.ts, test/handlers.ts                  Edit
docs/reference/schema.md, docs/reference/screens.md,
  docs/product-roadmap.md, docs/devlog/2026-10-19-catalog-parameters.md   Edit/Create (Task 1, Task 9)
```

`services/work_families.py` в фиче не меняется: окно слияния семей читает
схему источника существующим маршрутом (§2.9).

## Решения плана, которых нет в спеке

1. **Девять задач** снизу вверх: схема → идентичность варианта → частичный
   запрос → правка на месте → перестройка диапазонов → «не из списка» и API
   → пересборка → фронт → стенд. Пересборка идёт после API, потому что
   переиспользует копирование строк и `revision` правки; фронт — одной
   задачей, потому что окно, блок и карточка читают одну форму `SchemaOut`.
2. **Новый модуль `services/schema_edit.py`** держит план, предпросмотр и
   применение правки; `work_variants.py` (1 900 строк) получает только
   общие процедуры (ключ, пересчёт, запись частичного ответа). Старый
   `update_schema` и `_plan_edit` удаляются в Task 4 вместе со своими
   тестами-закрепителями новой версии `manual` (тесты переписываются на
   правку на месте, а не удаляются молча — перечень в Task 4).
3. **Новый модуль `services/parameter_ranges.py`** — чистые функции без БД:
   разбор числа, построение значений диапазона, отнесение числа и диапазона;
   одна реализация на ответ модели (Task 3) и перестройку (Task 5).
4. **Маршрут предпросмотра правки** — `POST /families/{id}/schema/edit-preview`,
   а не `?dry_run` у `PATCH`: так же устроена пересборка, и тело ответа другое.
5. **Подписи диапазонов** — десятичная запятая, без лишних нулей, тире `–`
   (U+2013): «до 120», «120–140», «200 и более»; норма значения считается
   существующей `normalize_value`.
6. **Предпросмотр на фронте** запрашивается с задержкой 400 мс после
   изменения тела; ответ старше текущего тела отбрасывается по счётчику
   запроса.

## Задачи

### Task 1: схема `0022`

**Files**
- Create: `backend/alembic/versions/2026_10_19_0022-catalog_parameters.py`
- Edit: `backend/models.py`, `backend/services/semantic_events.py`, `docs/reference/schema.md`
- Test: `backend/tests/integration/test_catalog_parameters_schema.py`

**Interfaces**
- Потребляет: `FamilyParameter`, `FamilyParameterValue`, `ContextParameterValue`,
  `FamilyParameterSchema`, `SemanticJob`, `ValueSource`, `_sql_str_list`,
  `CK_PARAMETER_ORDINAL_RANGE`, `CK_CONTEXT_VALUE_SOURCE_PAIR`,
  `EVENT_REQUIRED_KEYS`, `FAMILY_EVENT_TYPES` (существуют).
- Производит:

```python
class ParameterRole(str, enum.Enum): main = "main"; secondary = "secondary"
class ParameterValueKind(str, enum.Enum): exact = "exact"; range = "range"
class ParameterOrigin(str, enum.Enum): model = "model"; human = "human"
# ValueSource.off_list = "off_list"
CK_PARAMETER_ORDINAL_RANGE: str          # "ordinal BETWEEN 1 AND 100"
CK_PARAMETER_ROLE: str
CK_PARAMETER_VALUE_KIND: str
CK_PARAMETER_ORIGIN: str
CK_PARAMETER_RANGE_SHAPE: str            # §2.2: exact ⇔ три NULL; range ⇔ три NOT NULL, step>0, from<to, mod=0, ≤28, closed
CK_PARAMETER_VALUE_RANGE_ORDER: str      # range_lo < range_hi, когда оба заданы
CK_PARAMETER_VALUE_RETIRED_NOT_MERGED: str
CK_CONTEXT_VALUE_SOURCE_PAIR: str        # переписан: + 'off_list'
CK_CONTEXT_VALUE_OFF_LIST_TEXT: str      # (source = 'off_list') = (off_list_text IS NOT NULL)
CK_SEMANTIC_JOBS_REQUESTED_ORDINALS: str # requested_ordinals IS NULL OR kind = 'context_values'
# FamilyParameter: role, value_kind, closed, range_from, range_to, range_step, origin, archived_at
# FamilyParameterValue: range_lo, range_hi, retired_at
# ContextParameterValue: off_list_text, refresh_requested_at
# FamilyParameterSchema: revision: int = 0
# SemanticJob: requested_ordinals: list[int] | None
# SEMANTIC_EVENT_TYPES += "family_schema_edited" (субъект — семья)
```

**Утверждения**
- `upgrade` на пустой базе и на базе с данными всех шести таблиц §2.2 и
  заданиями всех четырёх видов: ни одна строка `semantic_jobs`,
  `work_variants` (`values_key`), `context_parameter_values` не меняется
  (сравнение до/после); все параметры — `main`, `exact`, `closed = false`,
  `origin = 'model'`, без архива; `revision = 0` у всех версий;
- каждое ограничение пробито ровно одним нарушением на вход, имя — по
  `diag.constraint_name` (DoD 1): роль и вид вне перечня; диапазон без
  `range_from`, без `range_to`, без `range_step` (по входу), с `step = 0`,
  `step < 0`, `from = to`, `from > to`, нецелым числом шагов, 29 внутренними,
  `closed = false`; точный с любой заданной границей (по входу); ordinal 0 и
  101; два параметра версии с одним номером, в том числе один архивный;
  значение `range_lo >= range_hi`; `retired_at` вместе с `merged_into_id`;
  `off_list` без текста, текст без `off_list`, `off_list` со значением;
  `requested_ordinals` у `family_suggestion`;
- граница: 28 внутренних проходят, 29 — нет; ordinal 100 проходит, 101 — нет;
- снятие `closed` из `CK_PARAMETER_RANGE_SHAPE` делает вход «диапазон без
  `closed`» красным;
- выражения CHECK и списки перечислений в миграции равны модели — против
  независимого литерала в тесте;
- downgrade при каждом непустом носителе §2.2 (параметр `secondary`, `range`,
  `closed`, `human`, архивный, ordinal > 3; версия `revision > 0`; значение с
  границей, с `retired_at`; строка `off_list`, с `refresh_requested_at`;
  задание с `requested_ordinals`; событие `family_schema_edited`) — по входу
  `RuntimeError`; на пустых — проходит и возвращает прежние
  `CK_PARAMETER_ORDINAL_RANGE`, `CK_CONTEXT_VALUE_SOURCE_PAIR` и список типов
  событий;
- `record_event("family_schema_edited", ...)` без обязательного ключа —
  отказ, с полным набором — запись;
- `docs/reference/schema.md` §7 — колонки, CHECK-и, тип события.

**Имена**
- Заводятся: всё из «Производит».
- Существуют, проверено `grep`-ом: `FamilyParameter` (`models.py:2726`),
  `FamilyParameterValue` (`:2749`), `ContextParameterValue` (`:2859`),
  `FamilyParameterSchema` (`:2671`), `SemanticJob` (`:2394`), `ValueSource`
  (`:1578`), `_sql_str_list` (`:233`), `CK_PARAMETER_ORDINAL_RANGE`,
  `CK_CONTEXT_VALUE_SOURCE_PAIR` (миграция `0019` :122, :131),
  `EVENT_REQUIRED_KEYS` (`semantic_events.py:98`), `FAMILY_EVENT_TYPES`
  (`:59`).

**Проверка**
- `uv run pytest tests -k catalog_parameters` — зелёная; ДО 0, ПОСЛЕ ≥ 40.
- `uv run pytest tests -k work_variants` — зелёная; ДО 1 879, ПОСЛЕ 1 879
  (паритет CHECK `0019` переписан на новые литералы, не удалён).

### Task 2: вариант из активных главных, пересчёт, `revision` у писателей

**Files**
- Edit: `backend/services/work_variants.py`
- Test: `backend/tests/integration/test_catalog_parameters_identity.py`

**Interfaces**
- Потребляет: Task 1; `values_key_of`, `_resolve_variant`,
  `_parameter_ids_by_ordinal`, `archive_variant_if_empty`,
  `merge_parameter_values`, `_extend_with_value`, `get_or_create_value`
  (существуют).
- Производит:

```python
def active_main_ordinals(db: Session, schema_id: int) -> dict[int, int]   # ordinal -> parameter_id
def recompute_variants(db: Session, *, family_id: int, schema_id: int,
                       context_ids: Sequence[int] | None = None) -> RecomputeResult
@dataclass(frozen=True)
class RecomputeResult:
    variants_before: int
    variants_after: int
    moved_contexts: int
    waiting_contexts: int        # без полного набора главных — в прежнем варианте
def bump_revision(db: Session, schema_id: int) -> int
```

**Утверждения**
- `_resolve_variant` принимает набор активных главных; ключ существующих
  вариантов фикстуры (все параметры главные) не меняется ни символом;
- вариант из параметров с ролью `secondary` не строится: две строки,
  различающиеся только второстепенным значением, — один вариант;
  `work_variant_values` пишет строки только главных;
- `recompute_variants`: контекст без строки главного или со строкой
  «уточняется» — `waiting_contexts`, вариант прежний; опустевшие варианты
  архивируются без `merged_into_id`;
- смена роли туда и обратно (на уровне роли в БД + `recompute_variants`)
  возвращает те же идентификаторы вариантов (DoD 5);
- старый архивный и новый параметр с «не уточнено» дают разные ключи при
  номерах `max + 1` (вход ревью Codex, DoD 6);
- `merge_parameter_values` второстепенного параметра не меняет ни одного
  варианта (идентификаторы, ключи, `merged_into_id`), только
  `context_parameter_values`; снятие ветки «только главный» — тест красный
  (DoD 5);
- `merge_parameter_values`, `_extend_with_value` и косметическое
  переименование увеличивают `revision` на 1 (по входу на каждого);
  слияние с выведенным и выведенного — `merge_value_retired`.

**Имена**
- Заводятся: `active_main_ordinals`, `recompute_variants`, `RecomputeResult`,
  `bump_revision`, `REFUSE_MERGE_VALUE_RETIRED`.
- Существуют, проверено `grep`-ом: `values_key_of` (`work_variants.py:134`),
  `_resolve_variant` (`:243`), `_parameter_ids_by_ordinal` (`:232`),
  `archive_variant_if_empty` (`:315`), `merge_parameter_values` (`:1629`),
  `_extend_with_value` (`:809`), `get_or_create_value` (`:161`).

**Проверка**
- `uv run pytest tests -k catalog_parameters` — ДО ≥ 40, ПОСЛЕ ≥ 60.
- `uv run pytest tests -k work_variants` — зелёная, ДО 1 879, ПОСЛЕ ≥ 1 879.

### Task 3: частичный запрос значений

**Files**
- Create: `backend/services/parameter_ranges.py`
- Edit: `backend/services/semantic_reconcile.py`, `variant_request.py`, `variant_answer.py`,
  `semantic_worker.py`, `semantic_decisions.py`, `work_variants.py`, `crud/work_variants.py`
- Test: `backend/tests/unit/test_catalog_parameters_ranges.py`,
  `backend/tests/integration/test_catalog_parameters_values.py`

**Interfaces**
- Потребляет: Task 1, Task 2; `_values_needed`, `_values_applicable`,
  `_plan_context_values`, `_plan_applicable`, `without_discarded_values`,
  `_mark_batch_jobs`, `load_values_material`, `ValuesRequestMaterial`,
  `render_values_request`, `_schema_block_text`, `values_response_format_for`,
  `parse_values_answer`, `render_request_for`, `render_job_request`,
  `_record_variant_result`, `complete_without_model`, `_schema_parameters`,
  `apply_values`, `_switch_pending_family`, `schedule_extension_wave`,
  `rebuild_preview`, `estimate_enqueue` (существуют).
- Производит:

```python
# services/parameter_ranges.py — без БД
@dataclass(frozen=True)
class RangeValue:
    label: str
    lo: Decimal | None      # None — «до …»
    hi: Decimal | None      # None — «… и более»
def parse_single_number(text: str) -> Decimal | None
def build_range_values(start: Decimal, stop: Decimal, step: Decimal) -> tuple[RangeValue, ...]
def range_for_number(values: Sequence[RangeValue], number: Decimal) -> RangeValue
def containing_range(values: Sequence[RangeValue], lo: Decimal | None, hi: Decimal | None) -> RangeValue | None
RANGE_RULE_LINE: str        # строка правила диапазона в блоке схемы (§2.4)

# services/semantic_reconcile.py
@dataclass(frozen=True)
class ValuesNeed:
    ordinals: tuple[int, ...]   # needed_ordinals
    variant_due: bool
def values_need(db: Session, context_id: int, schema_id: int) -> ValuesNeed   # вместо _values_needed

# services/variant_request.py
# ValuesRequestMaterial += requested_ordinals: tuple[int, ...]
def load_values_material(db, context_id, *, requested_ordinals: Sequence[int] | None = None) -> ValuesRequestMaterial | None

# services/variant_answer.py
def parse_values_answer(raw: str, *, parameters: Sequence[SchemaParameter]) -> ValuesAnswer   # parameters — запрошенные
```

**Утверждения**
- **Снимок до правок**: `request_hash` полного запроса значений и запроса
  схемы на фикстуре фичи 3а записан литералом в тест до первой правки
  рендера; после задачи полное подмножество без диапазонов даёт тот же хэш
  (DoD 2);
- `parse_single_number`: «131,73», «131.73», «5,1», «4 мм», «131,73 кг/м3»
  — число; «до 100 мм», «от 40 мм», «40-50», «1/2», «», «В25» — `None`;
  `build_range_values(120, 200, 20)` — 6 значений, подписи «до 120»,
  «120–140», …, «200 и более», `lo`/`hi` у крайних `None`;
  `range_for_number`: 139,99 → «120–140», 140 → «140–160», 200 → «200 и
  более», 119,99 → «до 120»; `containing_range`: «120–140» внутри «120–160»,
  «120–160» не внутри ни одного при шаге 20, бесконечные края учтены;
- `values_need` — по входу на каждую ветвь §2.4 (DoD 3): изменившиеся пути —
  все активные; недостающий параметр; «уточняется»; ждущая семья, нет
  варианта, чужая версия — со строками и без (`variant_due`); полный набор —
  `ordinals = ()`, `variant_due = False`; архивный параметр в `ordinals` не
  попадает;
- план по таблице §2.4 — по входу на строку; **завершение без модели**: новая
  строка семьи без параметров и контекст после пересборки без параметров
  модели со строками человека — вариант назначен, ждущая семья применена,
  промоушен выполнен, `context_variant_assigned` записан, задания
  `context_values` нет; снятие ветки «пусто и `variant_due`» — тест красный;
- рендер подмножества: блок схемы и перечисление номеров формата — только
  запрошенные, по возрастанию; `RANGE_RULE_LINE` — только в разделе
  параметра-диапазона; роль в теле не встречается;
- задание с `requested_ordinals` воспроизводится `render_request_for` и
  `render_job_request` без изменений входа — не `stale_fingerprint`;
  правка параметра подмножества между захватом и записью —
  `stale_fingerprint`; правка параметра вне подмножества — не
  `stale_fingerprint`; сверка не отменяет частичное задание как
  `input_changed`; сопоставление пачек и `without_discarded_values` видят
  частичные задания (DoD 3);
- запись частичного ответа (DoD 4): строки незапрошенных параметров
  действующей версии не меняются (идентификаторы значений и источники);
  строки параметров других версий удалены (вход: контекст со сменой семьи —
  Review Focus 2); `new` у открытого — расширение (`origin = extension`), у
  закрытого — `off_list` с текстом, список не меняется; `new` с числом у
  диапазона — значение диапазона с источником ответа, без числа —
  `off_list`; главного всё ещё нет — вариант прежний; `variant_split_hint`
  по всем активным строкам; `refresh_requested_at` снят только у
  запрошенных;
- волна расширения спрашивает один расширенный параметр (номера задания);
- `rebuild_preview` и `estimate_enqueue` считают рендеры по тем же
  подмножествам, что план (одна фикстура — равные числа).

**Имена**
- Заводятся: всё из «Производит»; `requested_ordinals` в рендере.
- Существуют, проверено `grep`-ом: `_values_needed`
  (`semantic_reconcile.py:572`), `_values_applicable` (`:559`),
  `_plan_context_values` (`:587`), `_plan_applicable` (`:430`),
  `without_discarded_values` (`:889`), `schedule_extension_wave` (`:1695`),
  `estimate_enqueue` (`:1225`), `_mark_batch_jobs`
  (`semantic_decisions.py:879`), `load_values_material`
  (`variant_request.py:232`), `ValuesRequestMaterial` (`:145`),
  `render_values_request` (`:428`), `_schema_block_text` (`:361`),
  `render_request_for` (`:461`), `values_response_format_for`
  (`variant_answer.py:169`), `parse_values_answer` (`:354`),
  `render_job_request` (`semantic_worker.py:246`), `_schema_parameters`
  (`:671`), `_record_variant_result` (`:697`), `complete_without_model`
  (`:915`), `apply_values` (`work_variants.py:871`),
  `_switch_pending_family` (`:830`), `rebuild_preview`
  (`crud/work_variants.py:437`).

**Проверка**
- `uv run pytest tests -k catalog_parameters` — ДО ≥ 60, ПОСЛЕ ≥ 120.
- `uv run pytest tests -k "work_variants or semantic_queue"` — зелёная; ДО
  3 104 (1 879 + 1 225), ПОСЛЕ ≥ 3 104; тесты, закреплявшие «ответ покрывает
  всю схему» и волну полной схемой, переписаны на подмножество, а не
  удалены (перечень — в отчёте задачи).

### Task 4: правка на месте — план, предпросмотр, применение, маршруты правки

**Files**
- Create: `backend/services/schema_edit.py`
- Edit: `backend/services/work_variants.py` (удаляются `update_schema`, `_checked_edits`, `_plan_edit`,
  `_MAX_PARAMETERS`), `backend/services/semantic_events.py`, `backend/routers/semantic.py`
  (`PATCH /families/{id}/schema` переключается на `apply_schema_edit`, новый
  `POST /families/{id}/schema/edit-preview`, коды отказа в картах статусов),
  `backend/tests/integration/test_semantic_api.py` (литералы маршрутов +1)
- Test: `backend/tests/integration/test_catalog_parameters_edit.py`; переписываются
  `test_work_variants_schema_life.py` (`UpdateSchemaCosmetic`, `NewVersion`, `Refusals`) и
  `test_work_variants_api.py` (`TestSchemaEdit` — тело с `preview_hash`)

**Interfaces**
- Потребляет: Task 1–3; `_lock_family_for_schema_life`, `reconcile_or_defer`,
  `NO_CAP`, `_estimate`, `RESERVE_FORMULA_VERSION`, `record_event`,
  `WorkFamilyError` (существуют).
- Производит:

```python
@dataclass(frozen=True)
class RangeSpec:
    start: Decimal
    stop: Decimal
    step: Decimal
@dataclass(frozen=True)
class ParameterDraft:
    ordinal: int | None            # None — новый
    name: str
    role: ParameterRole
    value_kind: ParameterValueKind
    closed: bool
    values: tuple[str, ...]        # для exact
    range: RangeSpec | None        # для range
@dataclass(frozen=True)
class EditSummary:
    variants_before: int
    variants_after_now: int
    variants_pending_answers: bool
    asked_by_parameter: tuple[tuple[int, str, int], ...]   # (ordinal, name, contexts)
    reserve_usd: Decimal
    expected_cached_usd: Decimal
    value_moves: tuple[tuple[str, str, str, int], ...]     # (parameter, from, to, contexts)
    retired: tuple[tuple[str, str, int], ...]              # (parameter, value, contexts)
    refreshing_contexts: int
    repriced_pending_jobs: int
    preview_hash: str
def preview_schema_edit(db: Session, *, family_id: int, drafts: Sequence[ParameterDraft]) -> EditSummary
def apply_schema_edit(db: Session, *, family_id: int, drafts: Sequence[ParameterDraft],
                      preview_hash: str, actor_id: int) -> EditSummary
REFUSE_SCHEMA_TOO_MANY_PARAMETERS = "schema_too_many_parameters"
REFUSE_SCHEMA_DUPLICATE_PARAMETER = "schema_duplicate_parameter"
REFUSE_SCHEMA_BAD_RANGE = "schema_bad_range"
REFUSE_SCHEMA_RANGE_TOO_MANY_VALUES = "schema_range_too_many_values"
REFUSE_SCHEMA_ORDINALS_EXHAUSTED = "schema_ordinals_exhausted"
class EditSummaryOut(TypedDict): ...   # поля EditSummary, деньги строками (crud/work_variants.py)
```

```text
POST  /api/v1/semantic/families/{id}/schema/edit-preview   тело: {parameters}               → EditSummaryOut
PATCH /api/v1/semantic/families/{id}/schema                тело: {parameters, preview_hash} → SchemaOut (форма до Task 6 — прежняя)
```

**Маршрут переключается в этой задаче** (ревью Codex гейта 3): удаление
`update_schema` без переключения `PATCH` оставило бы маршрут вызывать
несуществующую функцию до Task 6. Окно фронта до Task 8 шлёт тело без
`preview_hash` и получает `422`; ветка до Task 9 на стенде не
разворачивается.

**Утверждения**
- по входу на каждую строку таблицы §2.5 (кроме перестройки — Task 5):
  идентификаторы вариантов до и после и `requested_ordinals` поставленных
  заданий совпадают с таблицей (DoD 5); добавление второстепенного — ни один
  `work_variant_id` контекстов не изменился, ни одна строка главных не
  тронута; убрать главный — пересчёт, запросов нет; добавить значение
  точного — «уточняется» и запрос одного параметра только у `none` и
  `off_list`, `off_list` с тем же текстом по норме получает значение
  `source = 'manual'` без модели;
- **предел** (DoD 6): тело с 11 активными — `schema_too_many_parameters`, с
  10 — проходит; проверка считает активные параметры **итогового состояния**
  (тело — полное состояние); снятие проверки — тело с 11 сохраняется, тест
  красный; новый параметр после архивного получает `max + 1`; исчерпание —
  `schema_ordinals_exhausted`; дубль имени по норме, смысловое
  переименование, исчезновение точного значения без смены вида, пустое имя и
  значение, каждое нарушение диапазона — свой код; `building` —
  `schema_building`;
- **сериализация правок** (отдельно от предела, ревью Codex гейта 3): две
  правки одной семьи с одним и тем же действительным `preview_hash`,
  запущенные одновременно (барьер после предпросмотра): первая сохраняется,
  вторая ждёт замок семьи и после `commit` первой отказывает
  `preview_changed` — `revision` перечитывается под замком; снятие замка
  семьи — вторая всё равно ждёт `FOR UPDATE` версии и отказывает (тест
  зелёный, второй рубеж); снятие замка семьи **и** `FOR UPDATE` версии —
  вторая читает `revision` до `commit` первой и затирает её правку, тест
  красный;
- происхождение: новый — `human`; модельный, которого коснулась правка
  (имя, в том числе косметически; значения; роль; «только из списка»; вид) —
  `human` (по входу на признак); нетронутый — `model`;
- предпросмотр (DoD 9а): числа `EditSummary` равны итогу `apply_schema_edit`
  на той же фикстуре; `preview_changed` при изменении `revision` (вход:
  косметическое переименование между вызовами; снятие `revision` из хэша —
  тест красный), строк контекстов затронутых параметров и тарифов; задания
  ставятся `NO_CAP`;
- охват (DoD 9): правка не меняет ни одного `request_hash` заданий
  `family_suggestion` единицы и не ставит заданий контекстам другой семьи
  (единица с двумя семьями); ждущее задание значений своей семьи с
  изменившимся рендером пересчитано, с неизменившимся — нет;
  `repriced_pending_jobs` равно числу пересчитанных;
- гонки (DoD 10): запись ответа, запущенная при барьере внутри правки, ждёт
  `commit` (таймаут на второй стороне), затем `stale_fingerprint`; снятие
  замка семьи в правке — запись всё равно ждёт (`FOR UPDATE` версии); снятие
  замка семьи и `FOR UPDATE` версии — запись проходит под правкой, тест
  красный;
- событие `family_schema_edited` несёт изменения по параметрам, варианты до
  и после, число запросов; `revision` +1 на правку;
- маршруты: `PATCH` и `edit-preview` — право (`member` 403), каждый новый код
  отказа со статусом, `PATCH` без `preview_hash` — `422`, устаревший —
  `409 preview_changed`; литеральные множества маршрутов
  `test_semantic_api.py:421-437`, `:472` — +1; на границе задачи
  `PATCH` отвечает (тест маршрута проходит через `apply_schema_edit`).

**Имена**
- Заводятся: всё из «Производит».
- Существуют, проверено `grep`-ом: `_lock_family_for_schema_life`
  (`work_variants.py:1283`), `update_schema` (`:1509`), `_checked_edits`
  (`:1410`), `_plan_edit` (`:1443`), `reconcile_or_defer`
  (`semantic_reconcile.py:1751`), `NO_CAP` (`:108`), `_estimate`
  (`crud/work_variants.py:416`), `RESERVE_FORMULA_VERSION`
  (`semantic_cost.py:35`), `record_event` (`semantic_events.py:352`).

**Проверка**
- `uv run pytest tests -k catalog_parameters` — ДО ≥ 120, ПОСЛЕ ≥ 175.
- `uv run pytest tests -k "work_variants or semantic_api"` — зелёная; ДО
  2 069 (1 879 + 190), число может убыть только на тесты новой версии
  `manual`, переписанные в `catalog_parameters` (перечень с соответствием —
  в отчёте задачи).
- `uv run pytest tests/test_auth_coverage.py` — зелёная.

### Task 5: перестройка значений диапазона

**Files**
- Edit: `backend/services/schema_edit.py`
- Test: `backend/tests/integration/test_catalog_parameters_rebuild_values.py`

**Interfaces**
- Потребляет: Task 3 (`parameter_ranges`), Task 4 (`ParameterDraft`, `EditSummary`, `apply_schema_edit`).
- Производит:

```python
@dataclass(frozen=True)
class ValueResolution:
    value_id: int
    target_value_id: int | None   # None — неразрешимо
    unchanged: bool               # цель совпадает с источником
def resolve_values(db: Session, *, parameter_id: int, new_kind: ParameterValueKind,
                   new_range: RangeSpec | None) -> tuple[ValueResolution, ...]
```

**Утверждения** (DoD 7)
- точные числа в диапазоны — все разрешены, переносы с прежним источником,
  прежние значения выведены, запросов 0;
- вложенный прежний диапазон — перенос; делящийся — значение выведено,
  строки сохраняют его с «уточняется», вариант прежний, запрос одного
  параметра, после ответа — новый вариант;
- **цель совпадает с источником** (смена шага с теми же `start`/`stop`):
  крайние сохраняют идентификаторы, `retired_at` пуст, строки без отметки,
  запросов по ним нет;
- не число — неразрешимо; возврат в точные — все выведены и «уточняются»,
  пустой список точных допустим (Review Focus 4);
- «4» → «4–6» → возврат в точные с «4»: «4» возвращается из истории
  (`retired_at := NULL`), а не канонизируется в «4–6»; шаг 2 → 4 → 2: «4–6»
  возвращается, «4–8» делится, после ответов варианты с «4–6» возвращаются
  с прежними идентификаторами;
- ни одного `merged_into_id` (значений и вариантов) от перестройки;
  инвариант «строка на выведенном значении несёт отметку» после каждой
  операции;
- у главного — пересчёт, у второстепенного — варианты не меняются;
- предпросмотр называет переносы, выведенные и «уточняется» теми же числами.

**Имена**
- Заводятся: `ValueResolution`, `resolve_values`.
- Существуют: из Task 3, Task 4.

**Проверка**
- `uv run pytest tests -k catalog_parameters` — ДО ≥ 175, ПОСЛЕ ≥ 200.

### Task 6: «не из списка» и API

**Files**
- Edit: `backend/services/schema_edit.py`, `backend/crud/work_variants.py`, `backend/routers/semantic.py`,
  `backend/tests/integration/test_semantic_api.py`, `backend/tests/integration/test_work_variants_api.py`
- Test: `backend/tests/integration/test_catalog_parameters_api.py`

**Interfaces**
- Потребляет: Task 1–5; `schema_out`, `_parameters_of`, `variants_out`,
  `context_variant_out`, `SchemaOut`, `ParameterOut`, `ValueOut`,
  `ContextValueOut`, `VariantOut`, `UpdateSchemaRequest`,
  `SchemaParameterEdit`, `_STATUS_UNPROCESSABLE` (существуют).
- Производит:

```python
def resolve_off_list(db: Session, *, family_id: int, parameter_id: int, text_norm: str,
                     target_value_id: int, actor_id: int) -> None
REFUSE_OFF_LIST_NOT_FOUND = "off_list_not_found"
REFUSE_OFF_LIST_TARGET_INVALID = "off_list_target_invalid"

class OffListOut(TypedDict): text_norm: str; text: str; contexts: int; rows: int
class RangeOut(TypedDict): start: str; stop: str; step: str          # Decimal строками, как числа в JSON проекта
# ParameterOut += role, value_kind, closed, range: RangeOut | None, origin, off_list: list[OffListOut], refreshing: int
# ValueOut += range_lo: str | None, range_hi: str | None, retired: bool, contexts: int, rows: int
# SchemaOut += revision: int
# ContextValueOut += role, refreshing: bool, off_list_text: str | None
# VariantOut.values — только главные
```

```text
POST  /api/v1/semantic/families/{id}/schema/off-list/resolve  тело: {parameter_id, text_norm, target_value_id} → SchemaOut
# PATCH и edit-preview заведены в Task 4; здесь их ответ SchemaOut получает новые поля
```

**Утверждения**
- «Слить с…» (`resolve_off_list`, DoD 8): строки `off_list` с текстом по
  норме получают значение, `source = 'manual'`, список не меняется, у
  главного — пересчёт затронутых; `revision` +1; событие с изменением
  «текст не из списка отнесён»; `off_list_not_found`, `off_list_target_invalid`
  (чужой параметр, слитое, выведенное — по входу);
- агрегат «не из списка» по норме: два написания одной нормы — одна строка с
  суммой контекстов и строк;
- маршрут `off-list/resolve` — право (`member` 403), форма ответа, каждый
  отказ со статусом; расширенный `SchemaOut` — в ответах `GET`, `PATCH`,
  `off-list/resolve`, `cancel` одной формой (DoD 12);
- числа на значение и «не из списка» — постоянным числом запросов: счётчик
  запросов на семье с 5 и с 30 значениями равен;
- выведенные значения и архивные параметры в `SchemaOut` не отдаются в
  списке правки (`retired` у значения — признак истории); варианты — подписи
  только из главных; карточка — `role`, `refreshing`, `off_list_text`;
- литеральные множества маршрутов `test_semantic_api.py:421-437`, `:472` —
  ещё +1 (всего +2 за фичу); `_status_for_code` знает каждый новый код.

**Имена**
- Заводятся: всё из «Производит».
- Существуют, проверено `grep`-ом: `schema_out` (`crud/work_variants.py:137`),
  `_parameters_of` (`:183`), `variants_out` (`:230`), `context_variant_out`
  (`:281`), `SchemaOut` (`:75`), `ParameterOut` (`:68`), `ValueOut` (`:61`),
  `ContextValueOut` (`:99`), `VariantOut` (`:92`), `UpdateSchemaRequest`
  (`routers/semantic.py:446`), `SchemaParameterEdit` (`:440`),
  `_STATUS_UNPROCESSABLE` (`:166`).

**Проверка**
- `uv run pytest tests -k "catalog_parameters or semantic_api"` — ДО ≥ 200 + 190,
  ПОСЛЕ ≥ 240 + 190.
- `uv run pytest tests/test_auth_coverage.py` — зелёная.

### Task 7: пересборка схемы с параметрами человека

**Files**
- Edit: `backend/services/variant_request.py`, `variant_answer.py`, `work_variants.py`, `crud/work_variants.py`,
  `backend/services/semantic_worker.py` (`_record_variant_result` передаёт предел; путь без модели),
  `backend/services/semantic_reconcile.py` (`_plan_family_schemas`: при пределе 0 — путь без модели)
- Test: `backend/tests/integration/test_catalog_parameters_rebuild.py`

**Interfaces**
- Потребляет: Task 1–6; `load_schema_material`, `render_schema_request`,
  `parse_schema_answer`, `_write_frozen_schema`, `freeze_schema`,
  `rebuild_schema`, `rebuild_preview`, `SCHEMA_PROMPT_VERSION`,
  `_record_variant_result`, `render_job_request`, `complete_without_model`,
  `_plan_family_schemas` (существуют).
- Производит:

```python
# SchemaRequestMaterial += human_parameters: tuple[tuple[str, tuple[str, ...]], ...]
def model_parameter_limit(human_count: int) -> int      # min(3, 10 - human_count), не меньше 0
def parse_schema_answer(raw: str, *, limit: int) -> SchemaAnswer
def complete_schema_without_model(db: Session, job: SemanticJob) -> None   # semantic_worker.py
SCHEMA_PROMPT_VERSION = 4
```

**Предел ответа — из материала задания** (ревью Codex гейта 3): вызывающий
код опросчика `_record_variant_result` (`semantic_worker.py:726`) передаёт
`limit = model_parameter_limit(len(material.human_parameters))`, где
`material` — тот же, по которому `render_job_request` воспроизводит тело
задания; иначе разбор ответа схемы падал бы на обязательном аргументе. При
пределе 0 задание схемы модели не уходит: планировщик кладёт его в путь без
модели (как `plan.free` у значений, стоимость 0), опросчик выполняет
`complete_schema_without_model` — заморозка версии из одних параметров
человека тем же `_write_frozen_schema`.

**Утверждения** (DoD 11)
- **снимок до правок**: тело запроса схемы без параметров человека — прежний
  хэш (DoD 2);
- блок «уже заданы человеком» и текст «от 0 до N» — только при `H > 0`;
  `model_parameter_limit` при `H = 0, 3, 8, 10` — 3, 3, 2, 0;
- **через настоящий путь обработки ответа** (`_record_variant_result` из
  прохода опросчика, ответ модели подменён на уровне клиента): при `H = 8`
  ответ с 2 параметрами замораживает версию из 8 + 2, ответ с 3 — схемная
  ошибка, версия не заморожена; при `H = 0` — прежнее поведение с пределом 3;
- **при `H = 10`** — вызова модели нет (счётчик вызовов клиента 0), задание
  завершено путём без модели, версия заморожена из 10 параметров человека,
  стоимость 0; снятие ветки «предел 0 — без модели» — вызов модели
  происходит, тест красный;
- новая версия: номера уплотнены (человек 1..H по прежним номерам, модель
  H+1..H+N); параметры человека и правленые модельные перенесены с ролью,
  видом, границами, «только из списка», происхождением, активными
  значениями; модельные прежней версии — нет;
- строки контекстов человека: значение, «не уточнено», «разделы
  расходятся», `off_list` — скопированы с переводом идентификаторов по
  норме; «уточняется» — не скопированы, и этот параметр спрошен вместе с
  параметрами модели;
- запросы значений после заморозки — только параметры модели и прерванные
  «уточняется»; без них — завершение без модели (Task 3);
- предпросмотр пересборки оценивает значения по нынешним параметрам модели
  и называет приближение.

**Имена**
- Заводятся: `model_parameter_limit`, `complete_schema_without_model`, поле
  `human_parameters`.
- Существуют, проверено `grep`-ом: `_record_variant_result`
  (`semantic_worker.py:697`, вызов разбора схемы — `:726`),
  `render_job_request` (`:246`), `complete_without_model` (`:915`),
  `_plan_family_schemas` (`semantic_reconcile.py:941`), `load_schema_material`
  (`variant_request.py:174`), `render_schema_request` (`:407`),
  `SCHEMA_PROMPT_VERSION` (`:63`), `parse_schema_answer`
  (`variant_answer.py:278`), `_write_frozen_schema` (`work_variants.py:472`),
  `freeze_schema` (`:559`), `rebuild_schema` (`:1353`), `rebuild_preview`
  (`crud/work_variants.py:437`).

**Проверка**
- `uv run pytest tests -k catalog_parameters` — ДО ≥ 240, ПОСЛЕ ≥ 265.
- `uv run pytest tests -k work_variants` — зелёная.

### Task 8: фронт — окно «Список и редактор», блок, карточка, слияние семей

**Files**
- Rewrite: `frontend/src/pages/families/SchemaEditDialog.tsx`
- Create: `frontend/src/pages/families/schema-edit/ParameterList.tsx`, `ParameterEditor.tsx`,
  `RangeFields.tsx`, `OffListBlock.tsx`, `EditSummaryPanel.tsx`, `useSchemaEditPreview.ts`
- Edit: `SchemaBlock.tsx`, `VariantsTable.tsx`, `ContextVariant.tsx`, `FamiliesTab.tsx`
  (`MergeFamilyDialog`), `labels.ts`, `MergeValuesDialog.tsx`, `types/domain.ts`,
  `services/api/domain.ts`, `services/queries.ts`, `test/handlers.ts`
- Test: соседние `*.test.tsx` по каждому компоненту

**Interfaces**
- Потребляет: Task 6 (форма ответов); `useFamilySchema`, `useUpdateSchema`,
  `invalidateSchema`, `useMergeValues`, `schemaRefusalLabel`,
  `SCHEMA_REFUSAL_LABEL`, `MergeValuesDialog`, `variantSetLabel`
  (существуют).
- Производит:

```ts
export type ParameterRole = "main" | "secondary";
export type ParameterValueKind = "exact" | "range";
export interface SchemaEditDraft { ordinal: number | null; name: string; role: ParameterRole;
  value_kind: ParameterValueKind; closed: boolean; values: string[]; range: { start: string; stop: string; step: string } | null }
export interface SchemaEditSummary { /* поля EditSummaryOut */ }
export function useSchemaEditPreview(familyId: number, drafts: SchemaEditDraft[]): { summary: SchemaEditSummary | null; pending: boolean; refusal: string | null }
export function useResolveOffList(): UseMutationResult<FamilySchema, Error, { familyId: number; parameter_id: number; text_norm: string; target_value_id: number }>
```

**Утверждения**
- окно по макету `schema-dialog.html`: группы «Главные — определяют
  вариант» / «Второстепенные — вариант не делят»; строка списка — имя и
  строка пометок; «Добавить параметр» со счётчиком «N из 10», на 10
  недоступен; редактор — имя, роль, вид, точные (плашки с числом строк,
  пунктир без строк, отметка «дописано моделью», «⋯» → окно слияния с
  подставленными параметром и значением, «добавить значение», «Только из
  списка», блок «не из списка») или диапазоны (поля, превью «N диапазонов от
  … до … и 2 крайних», правило, «Только из списка» отмечен и недоступен,
  таблица переносов); «Убрать параметр»;
- сводка — ответ предпросмотра; запрос с задержкой 400 мс; ответ на
  устаревшее тело отброшен; «Сохранить» недоступна до ответа и при отказе
  проверки; `preview_changed` — пересчёт без потери правки;
- блок схемы — роли, вид, происхождение; таблица вариантов — подпись из
  главных; карточка контекста — главные и второстепенные раздельно,
  «уточняется», «не из списка: «…»»; окно слияния семей перечисляет
  параметры источника `human` или `secondary` «пропадут вместе со схемой»;
- каждый новый код отказа — подпись в `SCHEMA_REFUSAL_LABEL`;
- MSW-обработчики `test/handlers.ts` повторяют новые маршруты; проверки
  номеров 1..3 в обработчиках сняты;
- вёрстка (DoD 13, `AGENTS.md` §12 «Утверждение, которого прогон не может
  опровергнуть»): замер в браузере на стенде — Task 9; здесь — классы окна
  `max-h-[90vh]`, закреплённые шапка и низ, колонки `min-h-0
  overflow-y-auto`.

**Имена**
- Заводятся: всё из «Производит», компоненты `schema-edit/*`.
- Существуют, проверено `grep`-ом: `useFamilySchema` (`queries.ts:2373`),
  `useUpdateSchema` (`:2458`), `invalidateSchema` (`:2413`), `useMergeValues`
  (`:2470`), `SCHEMA_REFUSAL_LABEL` (`labels.ts:248`), `schemaRefusalLabel`
  (`:267`), `MergeValuesDialog` (`MergeValuesDialog.tsx:35`),
  `MergeFamilyDialog` (`FamiliesTab.tsx:720`), `variantSetLabel`
  (`VariantsTable.tsx:21`), `FamilySchema` (`types/domain.ts:2899`).

**Проверка**
- `npx vitest list src/pages/families` — ДО: SchemaEditDialog 29, SchemaBlock
  45, VariantsTable 8, ContextVariant 14, MergeValuesDialog 16, FamiliesTab 95;
  ПОСЛЕ — каждый не меньше, `schema-edit` ≥ 40.
- `npx vitest run src/pages/families`, `npx tsc --noEmit`, `npx eslint src` —
  зелёные.

### Task 9: стенд, справочник, карта, devlog

**Files**
- Edit: `docs/reference/screens.md` (§9: :95, :117, :119), `docs/product-roadmap.md` (А1 п. 3в — сделано)
- Create: `docs/devlog/2026-10-19-catalog-parameters.md`, скрипт сверки армирования
  `tasks/catalog-parameters-work/check_arm.py` (вне гита, только чтение)

**Interfaces**
- Потребляет: всё.
- Производит: снимки стенда рядом с макетом, числа в devlog.

**Утверждения** (DoD 14, 15, 16)
- миграция на стенде: `alembic upgrade head` из основного дерева после
  остановки бэкенда; ни один `request_hash` и ключ варианта не изменился
  (запрос до и после);
- пункты приёмки дизайна §5 1–7 с числами в devlog: армирование
  второстепенным — вариантов 72 до и после; сверка диапазонов договора 14
  скриптом ≥ 95 % строк (меньше — правка `RANGE_RULE_LINE` до приёмки);
  «Тип гидроизоляции»; смена роли армирования туда и обратно; пересборка
  гидроизоляции; закрытый список «ЦПС → Толщина стяжки»; «Толщина, мм» в
  диапазоны (18 → 17, запросов 0) и шаг 1 (30 «уточняется»);
- замер в браузере (`tasks/management-deck/tools`, playwright-core,
  системный Chrome): окно на семье из 10 параметров при высоте окна
  браузера 800 px — сводка и «Сохранить» в видимой области, у колонок
  `scrollHeight > clientHeight` и каждая прокручивается сама; ширина
  390 px — без горизонтальной прокрутки страницы; снимки рядом со
  сценариями макета;
- `screens.md` §9, дорожная карта; devlog называет тронутые области и
  прочитанные файлы граблей (`db.md`, `backend.md`, `frontend.md`,
  `runs.md`, `process.md`);
- `just check-agents-index` зелёный; `just ci` зелёный в GitHub Actions.

**Имена**
- Существуют: файлы справочника и карты.

**Проверка**
- `just check-agents-index` — 18 из 18.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: `just ci` — в GitHub Actions после пуша (локально — только
  затронутые наборы, решение пользователя 13.10.2026); стенд — Task 9.
