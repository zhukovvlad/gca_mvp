# План: варианты и промоушен работ (фича 3а)

**Спека:** `docs/superpowers/specs/2026-10-02-catalog-variants-design.md` (гейт 2 закрыт 03.10.2026, восемь кругов ревью, редакция `6f50a0e`)
**Ветка:** `feat/catalog-variants`

> **Круг 1 гейта 3 (03.10)**: 15 замечаний Codex, все подтверждены (три — по
> коду: в корне нет `package.json`; `_estimate_totals` и preview зовут
> `tariffs_from(settings)` без вида, `semantic_reconcile.py:404`,
> `semantic_decisions.py:504`; `Claim.attempt_id` обязателен,
> `semantic_worker.py:82`). Числа проверок — накопительные нижние границы, как в
> плане фичи 2: точное ПОСЛЕ неизвестно до написания тестов.
>
> **Круг 2 гейта 3 (03.10)**: 4 замечания, все подтверждены (по коду:
> `_assign_decided` зовёт `assign_family` напрямую, `semantic_decisions.py:183`;
> сверка берёт задания под доменными блокировками, `semantic_reconcile.py:431`, а
> `record_result` — задание первым, `semantic_worker.py:314`). Вердикт новых
> видов вынесен под доменные блокировки внутрь `apply_values`/`freeze_schema`;
> автопринятие — отдельной транзакцией после записи предложения (решение
> плана 7); подтверждения человека идут через `request_family_change`; значения
> контекста заменяются удалением; событий шесть.
>
> **Круг 3 гейта 3 (03.10)**: 3 замечания, все подтверждены (по коду:
> `_lock_for_decision` берёт целевую семью раньше контекста и не знает текущей,
> `semantic_decisions.py:172`): `lost_claim` закрывает только свою попытку, не
> задание нового владельца (Task 5, 7); тест гонки вердикта синхронизирован
> барьером (Task 5); предварительные блокировки решений человека — все семьи
> контекстов по `id` до контекстов, и одиночных, и групповых (Task 8).
>
> Исполнителю: задачи идут снизу вверх и по порядку; каждая — цикл TDD
> (`superpowers:test-driven-development`) и ревью задачи
> (`docs/process/implementation.md`) до следующей. Адреса `§N` без уточнения —
> разделы спеки.

## Global Constraints

- Всё новое на `/api/v1/semantic` — только `admin` (`Depends(require_admin)`);
  `member` получает `403` (спека §2.12).
- **Наружу — только перечень §2.2**: имя, единица, определение семьи,
  наименования строк, пути разделов, имена параметров и списки значений схемы.
  Цен, объёмов, количеств, подрядчиков, договоров, объектов в теле нет ни в
  одной задаче; тело каждого нового вида проходит `find_privacy_matches`.
- Деньги и тарифы — `Decimal`/`numeric`, никаких `float` (`AGENTS.md` §3).
- **Тело `family_suggestion` не меняется ни байтом** (§2.3); настройка
  `SEMANTIC_MODEL` и её тарифы — тоже.
- **Порядок блокировок во всей фиче: строка каталога → семья (по `id`) →
  вариант (по `id`) → контекст (по `id`)** (§2.6). Режим семьи: `FOR UPDATE`
  сразу — в операциях, меняющих семью, её схему или варианты (обработчик
  значений — включая переключение ожидающей семьи, жизнь схемы, слияние семей,
  глобальная пометка); `FOR SHARE` — в `assign_family` (назначение и снятие
  семьи у контекста без варианта, как сегодня) и в массовом автопринятии (§2.12).
  Повышение `FOR SHARE → FOR UPDATE` внутри одной транзакции запрещено везде.
- Сверка вызывается **до** существующего `commit` каждой точки, в той же
  транзакции (спека 2 §2.7); вызов модели — никогда внутри открытой транзакции.
- Промоушен односторонний: ни одна операция не пишет `kind='TO_REVIEW'` строке,
  бывшей `POSITION` (§2.10); `matching_cache` фича не пишет, кроме ручной записи
  глобальной пометки, как в `set_kind` (§2.11).
- Ни одна строка `work_variants`, `family_parameter_*` не удаляется — только
  архивируется (§2.4), кроме каскада в тестах. **`context_parameter_values` —
  исключение**: это текущее состояние контекста, а не история; набор
  заменяется целиком (удалить все строки контекста → вставить полный ответ,
  §2.6 шаг 3) и удаляется при снятии семьи, «не работа» и глобальной пометке.
  История значений — в событии `context_variant_assigned`.
- **Задание очереди блокируется после доменных строк**: строка каталога → семья
  → версия схемы → вариант → контекст → задание. Так уже ходит сверка
  (`_lock_jobs` под доменными блокировками, `semantic_reconcile.py:431`); обратный
  порядок «задание → домен» с ней замыкается в цикл.
- В тестах `RUN_SEMANTIC_WORKER = false`, клиент модели внедряется (`ModelClient`),
  сеть не открывается.
- Фронтенд — только shadcn/ui, недостающее — `npx shadcn add`.
- `docs/reference/screens.md` `## 9.`: заголовок и `EXPECTED_SCREEN_ANCHORS` не
  трогаются. Номер ревизии `AGENTS.md` называется только в коммите ревизии
  (Task 18).

## Review Focus

Входы, которые спека подразумевает, а задачи легко пропустить; тест на каждый
стоит в задаче-владельце:

1. **Семья, ставшая без единой живой строки, и семья с одной строкой** — схема
   строится и замораживается (в т. ч. из нуля параметров), а не зависает в
   `building` (Task 6, Task 7).
2. **Ответ модели со значением, совпадающим с уже слитым синонимом в другом
   регистре и с `ё`** — канонизация по `value_norm`, а не по сырой строке
   (Task 5).
3. **Импорт новой сметы, где у известного контекста поменялся порядок частот
   путей, а набор тот же** — перезапрос значений ставится (Task 6).
4. **Контекст, чья строка каталога уже `HEADER`/`TRASH`** — автопринятие и
   значения к нему не применяются, промоушен не трогает вид (Task 5, Task 8).
5. **Повторная заморозка той же версии** (дубль результата `family_schema` после
   `lost_claim`) — вторая запись отвергается вердиктом публикации, версия одна
   (Task 7).

## Структура файлов

```
backend/
  alembic/versions/2026_10_03_0019-work_variants.py   создаётся: шесть таблиц, колонки контекста, kind заданий, пачки, решения, события
  alembic/env.py                    правка: RAW_SQL_INDEXES (две частичные уникальности версий, ключ заданий)
  models.py                         правка: шесть моделей, перечисления, CK_*, колонки CatalogContext/SemanticJob/FamilySuggestion, SEMANTIC_EVENT_TYPES
  config.py                         правка: профили SEMANTIC_SCHEMA_*/SEMANTIC_VALUES_*, тарифы, SEMANTIC_AUTO_ACCEPT_THRESHOLD
  services/variant_request.py       создаётся: материал и тела family_schema / context_values, render_request_for
  services/variant_answer.py        создаётся: строгий разбор ответов двух видов
  services/work_variants.py         создаётся: значения, варианты, заморозка, обработка значений, промоушен, жизнь схемы
  services/family_change.py         создаётся: request_family_change, ожидания, автопринятие, массовое автопринятие
  services/semantic_cost.py         правка: tariffs_from(settings, kind)
  services/semantic_request.py      правка: is_applicable без work_family_id IS NULL
  services/semantic_reconcile.py    правка: три вида, paths_hash, готовность схемы, пачки нового формата, allowlist
  services/semantic_worker.py       правка: захват по виду, нулевая схема, запись по виду, done до шага 8
  services/semantic_decisions.py    правка: пачки по видам, отбрасывание отменяет building, задержанные/повтор по предмету, решения через request_family_change
  services/work_families.py         правка: assign_family(auto_suggestion, снятие варианта), archive_family/set_unit по ожиданиям, merge_families с вариантами
  services/review.py                правка: предупреждение о вариантах при слиянии, _mark_contexts_not_applicable, set_position_kind_global
  services/semantic_events.py       правка: шесть новых типов, payload context_family_assigned
  crud/rate_standards.py            правка: _require_refs FOR SHARE
  crud/semantic_queue.py            правка: list_jobs по виду, queue=change, счётчики статуса
  crud/work_variants.py             создаётся: чтение схемы, вариантов, варианта контекста
  routers/semantic.py               правка: маршруты §2.12
  cli.py                            правка: semantic-auto-accept, semantic-schemas-backfill
  tests/conftest.py                 правка: _DOMAIN_TABLES
  tests/{unit,integration}/test_work_variants_*.py            создаются
  tests/integration/test_semantic_queue_hooks_work_variants.py создаётся (матрица точек инварианта)
frontend/src/
  pages/families/SchemaBlock.tsx, VariantsTable.tsx, SchemaEditDialog.tsx, MergeValuesDialog.tsx   создаются
  pages/families/ContextVariant.tsx, PendingFamilyBlock.tsx, MarkPositionDialog.tsx, ChangeQueue.tsx  создаются
  pages/families/FamiliesTab.tsx, ContextCard.tsx, ContextsTab.tsx, SuggestionsTab.tsx, SuggestionsHeader.tsx, labels.ts   правка
  services/api/domain.ts, services/queries.ts, services/queryKeys.ts, types/domain.ts, test/handlers.ts   правка
  pages/families/*.test.tsx         создаются/правятся
docs/reference/schema.md            правка: блок «§7. Варианты и схемы семей»
docs/reference/screens.md           правка: ## 9. — «Схема и варианты», «Вариант контекста», «Смена семьи»
docs/product-roadmap.md             правка: А1, «Решения за пользователем»
AGENTS.md, docs/AGENTS-revisions.md  правка: §3, §5, преамбула; архив v6.26
docs/devlog/2026-10-03-catalog-variants.md   создаётся
```

## Решения плана, которых нет в спеке

1. **Три новых модуля по ответственности**: `variant_request.py` (что уходит
   модели), `variant_answer.py` (что пришло), `work_variants.py` (доменные
   правила варианта и схемы) и `family_change.py` (смена семьи, ожидания,
   автопринятие). Фича 2 держит запрос и ответ в `semantic_request.py` /
   `semantic_answer.py`; новые виды туда не добавляются, чтобы тело
   `family_suggestion` осталось байт в байт (снимок хэша — Task 3).
2. **`request_family_change` живёт в `services/family_change.py`**, а не в
   `work_families.py`, как назвала спека §2.5: `work_families.py` уже 1 300
   строк, а ожидания тянут за собой предложения и задания очереди. Спека
   поправляется той же веткой (Task 18).
3. **Все новые тесты — в файлах `test_work_variants_*.py`**, а матрица точек
   инварианта — в `test_semantic_queue_hooks_work_variants.py`: команда
   `-k work_variants` выбирает их все (ДО — 0 в обоих наборах, проверено
   03.10.2026), а структурный тест фичи 2 видит новый файл по `HOOKS_TESTS_GLOB`.
4. **`values_key` — строка `"1=17|2=?|3=42"`** (ordinal по возрастанию, `?` —
   пусто); для нулевой схемы — пустая строка. Строит одна функция
   `values_key_of`, и её же вызывает тест паритета с `work_variant_values`.
5. **Порядок задач**: схема (1) → профили и тарифы (2) → запрос (3) → ответ (4) →
   ядро варианта (5) → сверка (6) → исполнитель (7) → автопринятие и ожидания (8)
   → массовое автопринятие (9) → снятие и смена семьи, «не работа», Review (10) →
   жизнь схемы (11) → слияние семей (12) → глобальная пометка и нормативы (13) →
   точки инварианта (14) → API (15) → фронтенд (16, 17) → ревизия и справочник
   (18) → стенд и финал (19). Исполнитель идёт после сверки, автопринятие —
   после исполнителя: таблица публикации — шаг внутри `record_result`.
6. **Промпты** `SCHEMA_PROMPT` и `VALUES_PROMPT` — константы в
   `variant_request.py`; текст — из пилота (`tasks/catalog-variants-work/pilot/run_pilot.py`
   и `paths_pilot.py`) с двумя правками: теги ответа вместо строковых маркеров
   (§2.6) и запрет пустых значений.
7. **Автопринятие — отдельной транзакцией после записи предложения**, а не «в
   той же транзакции», как сказала спека §2.5. `record_result` фичи 2 держит
   задание `FOR UPDATE` с начала; `assign_family` внутри неё взял бы семью и
   контекст после задания — обратный порядок к сверке, которую зовут операции
   под доменными блокировками (`semantic_reconcile.py:431`), и возможен
   deadlock. Поэтому `record_result` публикует предложение и коммитит, затем
   `apply_publication_rules` новой транзакцией: доменные блокировки → перепроверка
   (опубликовано, решения нет, отпечаток текущий) → решение. Если между ними
   что-то изменилось или процесс упал — предложение остаётся опубликованным и
   видно человеку, а массовое автопринятие (Task 9) подберёт его. Согласовано
   пользователем 03.10.2026; спека §2.5 поправлена тем же коммитом.
8. **Вердикт новых видов — внутри `apply_values` и `freeze_schema`, под
   доменными блокировками**, а не в `record_result` до них: иначе между
   проверкой и записью другая транзакция могла бы сменить ожидание или схему, и
   устаревший ответ применился бы (§2.6: блокировки (0) → вердикт (1) → запись).

## Задачи

### Task 1: схема — миграция `0019`, модели, справочник

**Files**
- Create: `backend/alembic/versions/2026_10_03_0019-work_variants.py`
- Edit: `backend/models.py`, `backend/alembic/env.py`, `backend/tests/conftest.py`, `docs/reference/schema.md`
- Test: `backend/tests/integration/test_work_variants_schema.py`

**Interfaces**
- Потребляет: `Base`, `_sql_str_list`, `RAW_SQL_INDEXES`, `_DOMAIN_TABLES`, `CatalogContext`, `WorkFamily`, `SemanticJob`, `FamilySuggestion`, `SemanticReconcileBatch`, `SEMANTIC_EVENT_TYPES`, `CK_CONTEXT_FAMILY_PROVENANCE`, `FamilySource`, `SuggestionDecision` (существуют).
- Производит:

```python
class SchemaStatus(str, enum.Enum): building, frozen, superseded, cancelled
class SchemaOrigin(str, enum.Enum): model, manual
class ValueOrigin(str, enum.Enum): schema, extension, manual
class VariantStatus(str, enum.Enum): active, archived
class ValueSource(str, enum.Enum): name, path, manual, path_conflict, none
class SemanticJobKind(str, enum.Enum): family_suggestion, family_schema, context_values
# FamilySource += auto_suggestion
# SuggestionDecision += accepted_pending, auto_accepted, auto_pending, auto_superseded

class FamilyParameterSchema(Base): ...   # "family_parameter_schemas"
class FamilyParameter(Base): ...         # "family_parameters"
class FamilyParameterValue(Base): ...    # "family_parameter_values"
class WorkVariant(Base): ...             # "work_variants"
class WorkVariantValue(Base): ...        # "work_variant_values"
class ContextParameterValue(Base): ...   # "context_parameter_values"
# CatalogContext: work_variant_id, variant_at, variant_paths_hash, variant_split_hint,
#                 pending_family_id, pending_family_source, pending_suggestion_id, pending_by, pending_threshold, pending_at
# SemanticJob: kind, family_id, schema_id, paths_hash; context_id nullable
CK_CONTEXT_PENDING: str
CK_CONTEXT_VARIANT_NEEDS_FAMILY: str
# SEMANTIC_EVENT_TYPES += context_variant_assigned, context_family_pending, context_not_work,
#                         family_schema_frozen, family_schema_value_added, family_variants_merged
# CK_EVENT_SUBJECT_BY_TYPE: три family_* — предмет семья, три context_* — предмет контекст
```

**Утверждения**
- `alembic upgrade head` и `downgrade -1` проходят на пустой базе; `upgrade` на
  базе с данными фичи 2 (задания, предложения, пачка в старом формате пар) даёт
  всем заданиям `kind='family_suggestion'`, пачке — список объектов с
  `kind='family_suggestion'` и пересчитанный `fingerprints_hash`;
- каждое ограничение §2.4 отвергает свой запрещённый вход отдельной вставкой и
  пропускает соседний допустимый (`IntegrityError`, одно нарушение на вход):
  составной FK контекст → вариант; «вариант есть, семья пуста» (лазейка `MATCH
  SIMPLE`); `CK_CONTEXT_PENDING` — по входу на каждую из шести колонок, пустую при
  заполненных остальных, и на каждое несоответствие источника автору,
  предложению и порогу; параметр чужой схемы в `work_variant_values` и в
  `context_parameter_values`; значение чужого параметра; `merged_into_id` на
  значение другого параметра и на себя; пустое и пробельное значение;
  `superseded` без `frozen_at`; `cancelled` без `cancelled_at`; вторая `frozen` и
  вторая `building` у семьи (вторая `superseded` и вторая `cancelled` проходят);
  `variant_split_hint` без варианта; `accepted_pending` без автора и
  `auto_accepted` с автором; два задания одной версии с одним хэшем — отказ, двух
  версий — проходят; `family_schema` с `context_id` и `context_values` без
  `schema_id`;
- отложенные FK: вставка ложной пары контекст → вариант проходит `flush` и
  падает на `commit`;
- `downgrade` при каждом непустом новом носителе (варианты, версии, значения
  контекста, контекст с вариантом, контекст с ожиданием, задание нового вида,
  предложение с новым решением, событие нового типа, пачка с отпечатком
  `context_values`) поднимает `RuntimeError`, называя носитель, — по входу на
  носитель; на пустых проходит, возвращает `uq_semantic_jobs_context_request_hash`
  и пачке предложений — пары и прежний хэш;
- паритет литералов `IN (...)` и CHECK-выражений миграции с `models.py` для всех
  новых и расширенных перечислений — против независимого литерала в тесте;
  перечень событий — все шесть типов §2.13, и событие каждого типа с чужим
  предметом отвергается `CK_EVENT_SUBJECT_BY_TYPE` (по входу на тип);
- шесть таблиц в `_DOMAIN_TABLES`; блок «§7. Варианты и схемы семей» в
  `docs/reference/schema.md` в форме §6; `just check-agents-index` — 18 из 18.

**Имена**
- Заводятся: перечисления и модели выше, `CK_CONTEXT_PENDING`, `CK_CONTEXT_VARIANT_NEEDS_FAMILY`, миграция `0019`.
- Существуют, проверено `grep`-ом: `_sql_str_list`, `RAW_SQL_INDEXES`, `_DOMAIN_TABLES` (`tests/conftest.py:389`), `CK_CONTEXT_FAMILY_PROVENANCE` (`models.py:1366`), `SEMANTIC_EVENT_TYPES` (`models.py:1319`).

**Проверка**
- `just test-int-local-k work_variants` — ДО 0, ПОСЛЕ ≥ 60.
- `just test-int-local-k "semantic_schema or semantic_queue_schema"` — ДО 191, ПОСЛЕ ≥ 191 (паритет фич 1–2 не сломан).
- `just check-agents-index` — 18 из 18.

### Task 2: профили моделей, тарифы, порог

**Files**
- Edit: `backend/config.py`, `backend/services/semantic_cost.py`
- Test: `backend/tests/unit/test_work_variants_settings.py`

**Interfaces**
- Потребляет: `Settings`, `Tariffs`, `tariffs_from` (существуют), `SemanticJobKind` (Task 1).
- Производит:

```python
# Settings:
SEMANTIC_SCHEMA_MODEL: str            # "anthropic/claude-sonnet-5.5"
SEMANTIC_VALUES_MODEL: str            # "anthropic/claude-sonnet-5.5"
SEMANTIC_VARIANTS_REASONING_EFFORT: Literal["low", "medium", "high"]   # "low"
SEMANTIC_SCHEMA_MAX_TOKENS: int       # 20000
SEMANTIC_VALUES_MAX_TOKENS: int       # 600
SEMANTIC_SCHEMA_PRICE_{INPUT,CACHE_WRITE,CACHE_READ,OUTPUT}_PER_M: Decimal
SEMANTIC_VALUES_PRICE_{INPUT,CACHE_WRITE,CACHE_READ,OUTPUT}_PER_M: Decimal
SEMANTIC_AUTO_ACCEPT_THRESHOLD: Decimal | None   # None, 0 < x <= 1

def tariffs_from(settings: Settings, kind: SemanticJobKind = SemanticJobKind.family_suggestion) -> Tariffs
```

**Утверждения**
- `tariffs_from(settings)` без вида возвращает ровно прежние тарифы
  `SEMANTIC_PRICE_*` — существующие вызовы фичи 2 не меняются; вызовы,
  считающие задания новых видов, передают вид — оценка сверки (Task 6), preview
  (Task 7) и резерв захвата (Task 7);
- три вида дают три независимых набора: вход, где тарифы схемы и значений
  различны, даёт различные `Tariffs`;
- порог `None` по умолчанию; `0`, отрицательный и `> 1` отвергаются при старте;
  `Decimal`, а не `float`, сквозь всю цепочку.

**Имена**
- Заводятся: настройки выше.
- Существуют: `tariffs_from` (`services/semantic_cost.py:59`), `Tariffs` (`:41`).

**Проверка**
- `just test-unit-k work_variants` — ДО 0, ПОСЛЕ ≥ 8.
- `just test-unit-k semantic_queue` — ДО 364, ПОСЛЕ ≥ 364.

### Task 3: запрос — материал и тела `family_schema`, `context_values`

**Files**
- Create: `backend/services/variant_request.py`
- Test: `backend/tests/unit/test_work_variants_request.py`, `backend/tests/integration/test_work_variants_material.py`

**Interfaces**
- Потребляет: `chapter_paths` (`services/context_routing.py:235`), `top_path`, `SERIALIZATION_VERSION`, `_sha256_hex`-эквивалент, `RenderedRequest` (`services/semantic_request.py`), настройки Task 2.
- Производит:

```python
SCHEMA_PROMPT: str
VALUES_PROMPT: str
SCHEMA_PROMPT_VERSION: int
VALUES_PROMPT_VERSION: int
MAX_PATHS: Final = 10

@dataclass(frozen=True)
class SchemaRequestMaterial:
    family_id: int; schema_id: int; title: str; unit_code: str | None
    definition: str; names: tuple[str, ...]          # отсортированы, без повторов

@dataclass(frozen=True)
class SchemaParameterIn:
    ordinal: int; name: str; values: tuple[str, ...]  # без слитых значений

@dataclass(frozen=True)
class ValuesRequestMaterial:
    context_id: int; schema_id: int; title: str; unit_code: str | None
    article: str | None; paths: tuple[str, ...]        # до 10, порядок модели
    parameters: tuple[SchemaParameterIn, ...]

def load_schema_material(db: Session, family_id: int, schema_id: int) -> SchemaRequestMaterial
def load_values_material(db: Session, context_ids: Collection[int]) -> dict[int, ValuesRequestMaterial]
def top_paths(path_counts: Sequence[tuple[str, int]], limit: int = MAX_PATHS) -> tuple[str, ...]
def paths_hash_of(paths: Sequence[str]) -> str
def render_schema_request(material: SchemaRequestMaterial, *, settings: Settings) -> RenderedRequest
def render_values_request(material: ValuesRequestMaterial, *, settings: Settings) -> RenderedRequest
def render_request_for(db: Session, job: SemanticJob, *, settings: Settings) -> RenderedRequest
```

**Утверждения**
- одно тело — один `request_hash`; смена каждой оси меняет хэш — имена семьи,
  определение, схема, каждый из десяти путей, профиль модели, рассуждение,
  `response_format`; смена одиннадцатого пути и идентификаторов — нет;
  перестановка частот двух путей меняет и тело, и `paths_hash`;
- `top_paths`: частота по убыванию, при равенстве лексикографически, не больше
  десяти, по всем членствам независимо от `membership_state`;
- состав имён `family_schema` (§2.3): привязанные контексты и контексты с
  опубликованным предложением этой семьи с решением `NULL`, `accepted`,
  `auto_accepted`, `auto_pending`, `accepted_pending` при текущей или ожидаемой
  семье, равной этой; не входят — по входу на каждое: `other_family`,
  `family_created`, `rejected`, `auto_superseded`, `accepted` контекста,
  переназначенного вручную; архивный контекст не входит;
- список значений в теле `context_values` не содержит слитых значений;
- тело `family_suggestion` (`render_context_request`) не изменилось: снимок
  `request_hash` на фикстуре фичи 2 равен записанному до задачи;
- каждое тело проходит `find_privacy_matches` без отказа на фикстуре без
  совпадений и с отказом при имени объекта в пути (по входу на вид).

**Имена**
- Заводятся: всё перечисленное в `Производит`.
- Существуют: `chapter_paths`, `top_path` (`semantic_request.py:193`), `RenderedRequest` (`:147`), `render_context_request` (`:426`), `find_privacy_matches` (`semantic_privacy.py:260`).

**Проверка**
- `just test-unit-k work_variants` — ДО ≥ 8, ПОСЛЕ ≥ 22.
- `just test-int-local-k work_variants` — ДО ≥ 60, ПОСЛЕ ≥ 68.
- `just test-unit-k semantic_queue_request` — ДО 59, ПОСЛЕ ≥ 59.

### Task 4: ответ — строгий разбор двух видов

**Files**
- Create: `backend/services/variant_answer.py`
- Test: `backend/tests/unit/test_work_variants_answer.py`

**Interfaces**
- Потребляет: `AnswerSchemaError` (`services/semantic_answer.py:67`), `SchemaParameterIn` (Task 3).
- Производит:

```python
@dataclass(frozen=True)
class SchemaAnswer:
    parameters: tuple[SchemaParameterIn, ...]          # 0–3

ValueKind = Literal["value", "new", "conflict", "none"]

@dataclass(frozen=True)
class ValueItem:
    ordinal: int; kind: ValueKind; value: str | None; source: Literal["name", "path"] | None

@dataclass(frozen=True)
class ValuesAnswer:
    items: tuple[ValueItem, ...]

SCHEMA_RESPONSE_FORMAT: dict
VALUES_RESPONSE_FORMAT: dict

def parse_schema_answer(raw: str) -> SchemaAnswer
def parse_values_answer(raw: str, parameters: Sequence[SchemaParameterIn]) -> ValuesAnswer
```

**Утверждения**
- `parse_schema_answer`: 0–3 параметра, у каждого 1–8 значений; пустое или
  пробельное имя или значение, дубль `ordinal`, `ordinal` вне 1–3, повторяющийся
  ключ JSON, текст вокруг объекта — `AnswerSchemaError` (по входу на каждое);
  нуль параметров — законный ответ;
- `parse_values_answer`: множество `ordinal` равно множеству параметров схемы —
  пропуск, дубль, чужой — по входу; `value`/`new` требуют непустого `value` и
  `source`, `conflict`/`none` — запрещают оба; `value` вне списка с тегом `value`
  — ошибка; значение списка, текстуально равное `path_conflict` или
  начинающееся с `new:`, при теге `value` — законно;
- оба формата `response_format` — `strict: true`, и ответ, валидный по формату,
  но нарушающий правила выше, всё равно отвергается разбором.

**Имена**
- Заводятся: всё перечисленное.
- Существуют: `AnswerSchemaError`.

**Проверка**
- `just test-unit-k work_variants` — ДО ≥ 22, ПОСЛЕ ≥ 47.

### Task 5: ядро варианта — значения, варианты, заморозка, обработка значений, промоушен

**Files**
- Create: `backend/services/work_variants.py`
- Edit: `backend/services/semantic_events.py`
- Test: `backend/tests/integration/test_work_variants_core.py`, `backend/tests/integration/test_work_variants_concurrency.py`

**Interfaces**
- Потребляет: модели Task 1, `SchemaAnswer`, `ValuesAnswer` (Task 4), `record_event` (`services/semantic_events.py:254`), `_lock_families`, `_lock_contexts` (`services/work_families.py:623`, `:644`), `run_import_job` (`services/import_pipeline.py`) и `_count` (`services/matching.py:372`) — для входа инварианта кэша.
- Производит:

```python
def normalize_value(text: str) -> str
def values_key_of(value_ids_by_ordinal: Mapping[int, int | None]) -> str
def canonical_value_id(db: Session, value_id: int) -> int
def get_or_create_value(db: Session, *, parameter_id: int, text: str, origin: ValueOrigin) -> tuple[int, bool]
def get_or_create_variant(db: Session, *, family_id: int, schema_id: int,
                          value_ids_by_ordinal: Mapping[int, int | None]) -> tuple[WorkVariant, bool]
def archive_variant_if_empty(db: Session, variant_id: int) -> bool
@dataclass(frozen=True)
class JobGuard:
    job_id: int; claim_token: UUID; expected_request_hash: str

UnappliedReason = Literal["lost_claim", "stale_fingerprint", "not_applicable"]

def freeze_schema(db: Session, *, schema_id: int, answer: SchemaAnswer,
                  guard: JobGuard | None, settings: Settings) -> FreezeOutcome
def apply_values(db: Session, *, context_id: int, schema_id: int, answer: ValuesAnswer,
                 paths_hash: str, guard: JobGuard | None, settings: Settings) -> ApplyValuesOutcome
# Вердикт ВНУТРИ, под доменными блокировками: шаг (0) — строка → семья(и) → версия →
# прежний вариант → контекст, затем задание FOR UPDATE (порядок сверки); шаг (1) —
# захват ещё наш (claim_token, status='running'), предмет применим, отпечаток, пересчитанный
# render_request_for под этими блокировками, равен expected_request_hash. guard=None —
# только для вызовов без задания (ручная правка схемы, тестовые фикстуры).

@dataclass(frozen=True)
class FreezeOutcome:
    applied: bool; unapplied_reason: UnappliedReason | None; schema_id: int

@dataclass(frozen=True)
class ApplyValuesOutcome:
    applied: bool; unapplied_reason: UnappliedReason | None
    variant_id: int | None; previous_variant_id: int | None; promoted: bool
    family_switched: bool; values_added: tuple[int, ...]; previous_archived: bool
```

**Утверждения**
- `apply_values` держит шаги §2.6 (0)–(8) в указанном порядке, каждый —
  отдельным входом; полнота `ordinal` (1а) — в разборе, Task 4;
- **вердикт под блокировками** (решение плана 8): `lost_claim`,
  `stale_fingerprint`, `not_applicable` — по входу на исход и на функцию
  (`apply_values`, `freeze_schema`); при каждом доменные таблицы не тронуты;
- **`lost_claim` не трогает задание**: захват уже принадлежит другому
  обработчику (новый `claim_token`), поэтому старый закрывает только свою
  попытку исходом `lost_claim`, а статус, токен, `result_suggestion_id` и
  результат задания нового владельца остаются как были (вход: задание повторно
  захвачено между захватом старого и его записью — после записи старого у
  задания токен и статус нового владельца, а его результат применяется);
  `stale_fingerprint` и `not_applicable` при своём захвате закрывают задание
  (`cancelled/input_changed` и `cancelled/not_applicable`);
- **вход гонки вердикта синхронизирован барьером**: поток A строит `JobGuard`
  и останавливается на барьере в начале `apply_values`, до шага (0) (обёртка
  над внутренним шагом блокировок `_acquire_domain_locks`); поток B меняет
  ожидание контекста (для `freeze_schema` — замораживает другую версию) и
  коммитит; A продолжает — результат не применён. Снятие защиты — перенос
  проверки отпечатка до барьера — делает тест красным (A проверил старое и
  применяет устаревший ответ);
- `source` ответа (`name`/`path`) записан в `context_parameter_values` для
  `value` и `new`, `none` и `path_conflict` — для пустых (по входу на каждый);
- `new` при параллельных обработчиках одной семьи с одним значением — ровно одна
  строка (гонка проверена снятием `ON CONFLICT`); `new` со слитым синонимом
  (другой регистр и `ё`) даёт канонический `value_id`;
- после смены версии у контекста ровно столько строк значений, сколько
  параметров текущей версии; `conflict` → пустое значение и
  `variant_split_hint`;
- одинаковый набор у двух контекстов — один `work_variant_id`; все пустые —
  вариант «не уточнено»; нулевая схема — `values_key = ''`, один вариант на
  семью, `variant_paths_hash` = sha256 пустого списка; архивный неслитый вариант
  с тем же набором реактивируется, слитый заменяется целью;
- промоушен: строка `TO_REVIEW` → `POSITION` в той же транзакции,
  `payload.promoted = true`; строка `POSITION`, `HEADER`, `TRASH` вид не меняет;
- инвариант кэша (DoD §5.8): после промоушена повторный импорт той же сметы
  через `run_import_job` пишет `matching_cache` на строку `POSITION`, и `_count`
  не пишет `log.error` (перехват журнала в тесте);
- две гонки под блокировкой варианта — «последние два контекста уходят
  параллельно» → архив; «последний уходит, новый приходит» → активный вариант с
  одним контекстом (обе проверены снятием `FOR UPDATE` варианта);
- `freeze_schema`: версия `building` → `frozen` с `frozen_at`, прежняя текущая →
  `superseded` с сохранённым `frozen_at`; параметры и значения с
  `origin='schema'`; повторная заморозка той же версии — исход `not_applicable`;
- проверка `payload` в `semantic_events.py` — для всех шести новых типов §2.13
  (по входу на тип: полный `payload` принят, без обязательного ключа —
  отвергнут); эта задача пишет `context_variant_assigned`,
  `family_schema_frozen`, `family_schema_value_added`; остальные три пишут их
  владельцы — Task 8 (`context_family_pending`), Task 10 (`context_not_work`),
  Task 11 (`family_variants_merged`).

**Имена**
- Заводятся: всё перечисленное и внутренний шаг `_acquire_domain_locks` — точка барьера теста гонки.
- Существуют: `record_event`, `_lock_families`, `_lock_contexts`, `render_request_for` (Task 3).

**Проверка**
- `just test-int-local-k work_variants` — ДО ≥ 68, ПОСЛЕ ≥ 118.
- `just test-int-local-k work_families` — ДО 135, ПОСЛЕ ≥ 135.

### Task 6: сверка — три вида, пути, готовность схемы, пачки нового формата

**Files**
- Edit: `backend/services/semantic_reconcile.py`
- Test: `backend/tests/integration/test_work_variants_reconcile.py`

**Interfaces**
- Потребляет: `reconcile_semantic_jobs` (`:554`), `get_or_create_held_batch` (`:287`), `fingerprints_hash` (`:198`), `held_fingerprints` (`:270`), Task 3, Task 5.
- Производит:

```python
@dataclass(frozen=True)
class Fingerprint:
    kind: SemanticJobKind; context_id: int | None; family_id: int | None
    schema_id: int | None; request_hash: str

def fingerprints_hash(fingerprints: Sequence[Fingerprint]) -> str          # новый формат
def schema_ready_to_build(db: Session, family_id: int) -> bool
def reconcile_family_schemas(db: Session, family_ids: Collection[int], *, cap, source: str) -> ReconcileReport
def reconcile_context_values(db: Session, context_ids: Collection[int], *, cap, source: str) -> ReconcileReport
def schedule_extension_wave(db: Session, *, family_id: int, parameter_id: int) -> int
# reconcile_semantic_jobs(...) зовёт обе ветви для тех же context_ids
```

**Утверждения**
- таблица исходов фичи 2 действует для каждого вида по его предмету: строка за
  строкой предъявлена для `family_schema` и `context_values`;
- `context_values` неприменим при версии не текущей → `cancelled/input_changed`,
  новое по текущей; `family_schema` неприменим при семье не `active` или версии
  не `building`;
- изменение набора или порядка десяти путей контекста ставит `context_values`,
  неизменные пути — нет; контексты нулевой схемы вне проверки;
- `schema_ready_to_build`: ложно при `pending`/`running` предложениях единицы и
  при удержанной пачке с отпечатком предложения этой единицы или `mass`; истинно
  при пачке импорта другой единицы, при пачке из одних `context_values` этой
  единицы, при остатке только `privacy_hold` и `error` (пять входов);
- волна после расширений ставится ровно один раз — последним обработчиком
  семьи (собственное задание к шагу 8 уже `done`, отдельного исключения нет —
  снято ревью реализации как эквивалентное);
- сверх потолка — удержанная пачка нового формата; её `fingerprints_hash`
  совпадает при перестановке входа (подтверждение пачки — Task 7: оно живёт в
  `semantic_decisions.py`);
- оценка потолка (`_estimate_totals`) считает резерв каждого задания тарифами
  его вида: вход с различными тарифами схемы, значений и предложений даёт сумму,
  равную сумме по видам, а не по тарифу `family_suggestion`;
- `RECONCILE_ALLOWLIST` содержит `services.work_variants`; сверка вызывается из
  `apply_values` и `freeze_schema`, обе точки — в матрице
  `test_semantic_queue_hooks_work_variants.py` (перенесено из Task 14: без них
  ветка красна на архитектурном тесте); `services.family_change` вносит Task 8,
  когда модуль появится.

**Имена**
- Заводятся: всё перечисленное.
- Существуют: `RECONCILE_ALLOWLIST` (`:101`), `ReconcileReport` (`:128`), `NO_CAP` (`:73`), `_estimate_totals` (`:396`).

**Проверка**
- `just test-int-local-k work_variants` — ДО ≥ 118, ПОСЛЕ ≥ 143.
- `just test-int-local-k semantic_queue_reconcile` — ДО 65, ПОСЛЕ ≥ 65.

### Task 7: исполнитель — захват и запись по виду, нулевая схема, задержанные

**Files**
- Edit: `backend/services/semantic_worker.py`, `backend/services/semantic_decisions.py`, `backend/crud/semantic_queue.py`
- Test: `backend/tests/integration/test_work_variants_worker.py`

**Interfaces**
- Потребляет: `claim_next` (`:161`), `record_result` (`:302`), `record_failure` (`:398`), `_hold_verdict` (`semantic_decisions.py:367`), `list_jobs` (`crud/semantic_queue.py:652`), `approve_batch`, `discard_batch` (`:618`, `:644`), Task 3–6.
- Производит:

```python
@dataclass(frozen=True)
class Claim:
    job_id: int
    attempt_id: int | None          # None ⟺ synthesized: у нулевой схемы попытки нет
    claim_token: UUID
    rendered: RenderedRequest | None   # None ⟺ synthesized
    candidates: tuple[CandidateFamily, ...]
    kind: SemanticJobKind
    synthesized: bool
def complete_without_model(db: Session, claim: Claim, *, now: datetime) -> None   # предусловие: claim.synthesized
```

**Утверждения**
- захват каждого вида: применимость, отпечаток, приватность, бюджет — каждая
  ветвь отдельным входом; резерв захвата и preview пачки/перезапроса
  (`_preview_and_pairs`) считаются тарифами вида (вход: различные тарифы —
  различные резервы и различные суммы preview);
- синтетический захват нулевой схемы — `attempt_id is None`, `rendered is None`,
  ни строки попытки, ни резерва;
- `context_values` нулевой схемы завершается без вызова модели, без попытки и
  резерва, полным протоколом `apply_values`;
- задание переходит в `done` до шага (8) `apply_values`;
- для новых видов `record_result` **не берёт задание первым** и вердикта сам не
  выносит: строит `JobGuard` из захвата и передаёт его в `freeze_schema` /
  `apply_values` (Task 5), которые блокируют задание после доменных строк;
  исход `applied=False` при `lost_claim` закрывает только свою попытку (задание
  нового владельца не тронуто), при `stale_fingerprint`/`not_applicable` —
  задание как отменённое — по входу на каждую причину и вид; дубль результата
  после `lost_claim` не создаёт второй версии;
- путь `family_suggestion` в `record_result` не меняется (снимок порядка
  блокировок и записи — тесты фичи 2 зелёные без правок);
- `family_schema` в `error` и `privacy_hold` показывается в `list_jobs` строкой
  по семье; «Отправить», «Не отправлять», «Повторить» работают по предмету — по
  входу на статус; «Не отправлять» и «Отбросить» пачки отменяют версию
  `building`, если её отпечаток не представлен в другой пачке `held`/`approved`
  или живом задании (два входа); `error` версию не трогает;
- `Fingerprint` нового формата в `approve_batch`/`discard_batch`: подтверждение
  пачки из трёх видов ставит задания всех трёх; пачка, перенесённая миграцией из
  старого формата, подтверждается.

**Имена**
- Заводятся: `complete_without_model`; расширение `Claim`.
- Существуют: перечисленные в `Потребляет`.

**Проверка**
- `just test-int-local-k work_variants` — ДО ≥ 143, ПОСЛЕ ≥ 168.
- `just test-int-local-k "semantic_queue_worker or semantic_queue_decisions"` — ДО 182, ПОСЛЕ ≥ 182.

### Task 8: автопринятие, предикат, ожидающее назначение

**Files**
- Create: `backend/services/family_change.py`
- Edit: `backend/services/semantic_request.py`, `backend/services/semantic_worker.py`, `backend/services/work_families.py`, `backend/services/semantic_events.py`, `backend/services/semantic_decisions.py`
- Test: `backend/tests/integration/test_work_variants_family_change.py`, `backend/tests/integration/test_work_variants_decisions.py`

**Interfaces**
- Потребляет: `assign_family` (`work_families.py:650`), `archive_family` (`:886`), `set_unit` (`:789`), `is_applicable` (`semantic_request.py:205`), `record_result`, `_lock_for_decision` (`semantic_decisions.py:172`), `_assign_decided` (`:183`), `confirm_suggestions` (`:209`), `assign_other_family` (`:271`), `create_family_from_suggestion` (`:292`), Task 5–7.
- Производит:

```python
FamilyChangeSource = Literal["manual", "suggestion", "auto_suggestion"]

@dataclass(frozen=True)
class FamilyChangeOutcome:
    kind: Literal["assigned", "pending", "unchanged"]
    context_id: int; family_id: int | None; superseded_suggestion_id: int | None

def request_family_change(db: Session, *, context_id: int, family_id: int | None, actor_id: int | None,
                          source: FamilyChangeSource, suggestion_id: int | None = None,
                          threshold: Decimal | None = None) -> FamilyChangeOutcome
def cancel_pending_family(db: Session, *, context_id: int, actor_id: int) -> None
def apply_publication_rules(db: Session, *, suggestion_id: int, threshold: Decimal | None) -> FamilyChangeOutcome | None
# своя транзакция после commit record_result (решение плана 7): доменные блокировки →
# предложение FOR UPDATE с перепроверкой (опубликовано, решения нет, отпечаток текущий) → таблица §2.5

# services/work_families.py — изменённый контракт:
def assign_family(db: Session, *, context_id: int, family_id: int | None, actor_id: int | None,
                  source: FamilySource = FamilySource.manual, suggestion_id: int | None = None,
                  threshold: Decimal | None = None, confidence: Decimal | None = None) -> CatalogContext
# actor_id is None ⟺ source == auto_suggestion; threshold и confidence обязательны при auto_suggestion
# и запрещены при прочих источниках; оба уходят в payload context_family_assigned
```

**Утверждения**
- `is_applicable` без условия `work_family_id IS NULL`; привязанный контекст
  получает задание при перезапросе единицы;
- таблица публикации §2.5 — каждая из семи строк отдельным входом, включая «та
  же семья» при автоожидании (снято, `auto_superseded`) и при ручном (не тронуто);
  граница порога — «ровно на пороге» принято, «на единицу ниже» нет; при пустом
  пороге — ни одной автопривязки;
- автопринятие идёт отдельной транзакцией после записи предложения: предложение,
  ставшее устаревшим или решённым человеком между ними, правило не трогает (вход);
  порядок «задание → домен» в `record_result` не возникает: вставка предложения
  берёт неявный `FOR KEY SHARE` семьи ответа и контекста по внешним ключам,
  поэтому `record_result` берёт их `FOR KEY SHARE` сам **до** задания, в
  порядке домена (тест: параллельные операция над контекстом или семьёй и
  запись ответа по нему, в том числе при потерянном захвате, завершаются без
  deadlock);
- **решения человека идут через `request_family_change`**: `_assign_decided`
  больше не зовёт `assign_family` напрямую. Через существующие маршруты
  `/suggestions/confirm`, `/suggestions/{id}/other-family`,
  `/suggestions/{id}/create-family` контекст **с вариантом** получает ожидание, а
  не смену семьи: подтверждение — `decision=accepted_pending`, «Другая семья» —
  `other_family` с ожиданием `pending_family_source=manual`, «Завести семью» —
  `family_created` с ожиданием на новую семью; контекст **без варианта** —
  назначение сразу и прежние решения (`accepted`, `other_family`,
  `family_created`); по входу на маршрут и на наличие варианта; составной FK не
  нарушается ни в одном;
- **предварительные блокировки решений переработаны** (`_lock_for_decision` и
  групповое `confirm_suggestions`): сначала без блокировок читаются текущие
  семьи контекстов решения, затем **все** семьи — целевые и текущие —
  берутся `FOR SHARE` одним запросом по возрастанию `id`, затем контексты
  `FOR UPDATE` по возрастанию `id`, затем предложения с перепроверкой.
  **Попытка захвата обёрнута в savepoint, открытый до первой её блокировки**
  (`db.begin_nested()`; предварительное чтение блокировок не берёт, так что до
  savepoint строки попытки не заблокированы). Если под блокировкой текущая
  семья контекста оказалась иной, чем при чтении, — откат к savepoint (Postgres
  снимает строчные блокировки, взятые после него), повторное чтение без
  блокировок и **полный** перезахват семей и контекстов в установленном
  порядке; новая семья никогда не запрашивается при удерживаемом контексте.
  Повтор один; если семья сменилась снова, новых блокировок не берётся:
  одиночное решение откатывает попытку и отвечает `409`, группа относит такие
  предложения в `skipped`, как непрошедшие перепроверку. Групповое
  подтверждение блокирует объединение семей всей группы, а не по предложению;
  savepoint у группы один, и повтор освобождает блокировки всей группы. Тем же
  помощником пользуется всякая точка фичи, читающая текущую семью без
  блокировки перед захватом. Входы: (а) подтверждение A→B на контексте с
  вариантом параллельно с обработчиком значений, держащим A и B, — без
  deadlock (проверено возвратом прежнего `_lock_for_decision`: тест становится
  красным или зависает — жёсткий таймаут теста); (б) **смена семьи между
  предварительным чтением и блокировкой контекста**: барьер после чтения,
  параллельная сессия назначает контексту C и фиксирует, решение берёт
  прежний набор и контекст и встаёт на втором барьере, обработчик значений
  берёт C `FOR UPDATE` и ждёт контекст; после отпускания решение
  откатывается к savepoint, обработчик завершается, повтор берёт C и
  контекст — обе операции заканчиваются без ошибки БД (решение — успехом
  или `409` перепроверки); проверено заменой отката на повтор поверх
  удерживаемых блокировок — deadlock или жёсткий таймаут; (в) семья
  сменилась и после повтора — одиночное решение: `409`, решение не записано,
  блокировки сняты (параллельная сессия после ответа берёт контекст
  `FOR UPDATE NOWAIT`); группа: это предложение в `skipped`, прочие
  подтверждены;
- `assign_family(source=auto_suggestion)` — `family_by = NULL`, событие с
  `threshold` и `confidence`; их отсутствие при `auto_suggestion` и присутствие при
  прочих источниках отвергается;
- ожидающее назначение — каждый из шести пунктов §2.5 отдельным входом;
  переключение семьи и варианта — одной транзакцией в `apply_values` (снятие
  транзакционности делает тест красным); порог в событии — из
  `pending_threshold`, а не из изменённой настройки; ошибка и `privacy_hold`
  оставляют прежние семью и вариант; исходы `auto_accepted` / `accepted` /
  `rejected` / `auto_superseded` терминальны и не воскресают тем же отпечатком;
- `archive_family` и `set_unit` отказывают при одних лишь ожиданиях; семья,
  архивированная в обход между ожиданием и применением, — результат не
  применён, ожидание снято;
- встречные ожидания A→B и B→A параллельно — без deadlock (проверено снятием
  блокировки второй семьи).

**Имена**
- Заводятся: всё перечисленное, модуль `services/family_change.py`; контракт `assign_family` расширен (`actor_id: int | None`, `threshold`, `confidence`); `_assign_decided` переведён на `request_family_change`; `_lock_for_decision` и блокировки `confirm_suggestions` переработаны.
- Существуют: перечисленные в `Потребляет`.

**Проверка**
- `just test-int-local-k work_variants` — ДО ≥ 168, ПОСЛЕ ≥ 210.
- `just test-int-local-k semantic_queue_decisions` — тесты подтверждения фичи 2 на контекстах без варианта зелёные без правок.
- `just test-int-local-k "work_families or semantic_queue_assign"` — ДО 160, ПОСЛЕ ≥ 160.

### Task 9: массовое автопринятие — preview, apply, CLI

**Files**
- Edit: `backend/services/family_change.py`, `backend/cli.py`
- Test: `backend/tests/integration/test_work_variants_auto_accept.py`

**Interfaces**
- Потребляет: `apply_publication_rules` (Task 8), `_guard` (`cli.py:24`).
- Производит:

```python
@dataclass(frozen=True)
class AutoAcceptPreview:
    preview_hash: str; threshold: Decimal; by_outcome: Mapping[str, int]; total: int

def preview_auto_accept(db: Session) -> AutoAcceptPreview
def apply_auto_accept(db: Session, *, preview_hash: str) -> Mapping[str, int]
# без actor_id: автопринятие пишется без автора, и ни одно событие его не несёт
# cli: semantic-auto-accept (preview → подтверждение → apply), semantic-schemas-backfill
```

**Утверждения**
- `preview_hash` меняется от каждой оси кортежа §2.12 и от порога — входы: ручная
  привязка, выдача варианта, ожидание, слияние предложенной семьи между preview
  и apply; несовпавший хэш — отказ, ничего не применено;
- apply берёт семьи `FOR SHARE` по `id`, затем контексты `FOR UPDATE` по `id`;
  при работающем обработчике значений той же семьи — без deadlock;
- без порога preview отказывает понятной ошибкой;
- apply подбирает и предложения, опубликованные, но не автопринятые из-за сбоя
  между записью и отдельной транзакцией автопринятия (решение плана 7; вход);
- `semantic-schemas-backfill` ставит `family_schema` всем активным семьям без
  текущей версии пачкой `mass`, сверх потолка — удержанной.

**Имена**
- Заводятся: всё перечисленное.
- Существуют: `semantic_enqueue_all` (`cli.py:118`).

**Проверка**
- `just test-int-local-k work_variants` — ДО ≥ 210, ПОСЛЕ ≥ 220.

### Task 10: снятие и смена семьи, «не работа», слияние в Review

**Files**
- Edit: `backend/services/work_variants.py`, `backend/services/review.py`, `backend/services/work_families.py`
- Test: `backend/tests/integration/test_work_variants_removal.py`

**Interfaces**
- Потребляет: `reconcile_contexts` (`review.py:446`), `merge_into_position_outcome` (`:588`), `_mark_contexts_not_applicable` (`:726`), `archive_variant_if_empty` (Task 5).
- Производит:

```python
def mark_context_not_work(db: Session, *, context_id: int, actor_id: int) -> None
def clear_variant(db: Session, *, context_id: int) -> int | None   # возвращает прежний вариант
# без reason: события снятия варианта спека не заводит, потребителя у причины нет
```

**Утверждения**
- «не работа»: `NOT_APPLICABLE`, сняты семья, вариант, ожидание, значения,
  `variant_split_hint`; задания отменены сверкой; событие `context_not_work`;
  вид строки не меняется;
- снятие семьи снимает вариант, ожидание, значения **и `variant_split_hint`**
  (вход: контекст с конфликтом путей — снятие семьи проходит, а не падает на
  CHECK «подсказка только при варианте»); строка остаётся `POSITION`;
- на каждом пути снятия (не работа, снятие семьи, слияние в Review) опустевший
  вариант архивируется — по входу на путь;
- слияние в Review добавляет в `warnings` строку о расхождении вариантов и не
  добавляет при равных; тесты Review фичи 1 зелёные без правок.

**Имена**
- Заводятся: `mark_context_not_work`, `clear_variant`.
- Существуют: перечисленные в `Потребляет`.

**Проверка**
- `just test-int-local-k work_variants` — ДО ≥ 220, ПОСЛЕ ≥ 232.
- `just test-int-local-k review` — ДО 161, ПОСЛЕ ≥ 161.

### Task 11: жизнь схемы — пересборка, отмена, правка, слияние синонимов

**Files**
- Edit: `backend/services/work_variants.py`
- Test: `backend/tests/integration/test_work_variants_schema_life.py`

**Interfaces**
- Потребляет: Task 5–7.
- Производит:

```python
@dataclass(frozen=True)
class ParameterEdit:
    ordinal: int; name: str; values: tuple[str, ...]

def rebuild_schema(db: Session, *, family_id: int, actor_id: int) -> FamilyParameterSchema
def cancel_schema_build(db: Session, *, family_id: int, actor_id: int) -> None
def update_schema(db: Session, *, family_id: int, parameters: Sequence[ParameterEdit], actor_id: int) -> FamilyParameterSchema
def merge_parameter_values(db: Session, *, parameter_id: int, source_value_id: int,
                           target_value_id: int, actor_id: int) -> Mapping[int, int]   # слитые варианты
```

**Утверждения**
- пересборка: при `building` возвращается та же версия; два параллельных вызова
  дают одну; после заморозки старая версия `superseded`, контексты держат
  старый вариант до готовности, опустевшие старые варианты архивируются;
- отмена: версия `cancelled`, задание `cancelled`; не мешает новой пересборке и
  слиянию семей;
- правка: косметическое переименование (та же нормализованная форма) принято и
  новых обращений к модели не ставит — контекст с вариантом на текущей схеме
  задания не получает, уже ждущее задание сверка заменяет (тело запроса несёт
  отображаемые имена, его отпечаток меняется); смысловое — отказ; добавление параметра, **удаление
  параметра** и добавление значения — каждое новая версия `origin='manual'`,
  замороженная сразу, с заданиями значений всем контекстам семьи (по входу на
  каждое); удаление значения — отказ (только слияние); при `building` — отказ;
- слияние синонимов: при коллизии наборов контексты переведены, источник
  архивирован с нетронутыми `values_key` и построчными значениями; без коллизии
  — ключ и строки переписаны, вариант тот же; цель-предок — отказ; цель-синоним
  канонизирована; архивная цель реактивирована;
- расширение списка ставит одну волну перезапроса пустых (с Task 6).

**Имена**
- Заводятся: всё перечисленное.

**Проверка**
- `just test-int-local-k work_variants` — ДО ≥ 232, ПОСЛЕ ≥ 252.

### Task 12: слияние семей с вариантами

**Files**
- Edit: `backend/services/work_families.py`
- Test: `backend/tests/integration/test_work_variants_family_merge.py`

**Interfaces**
- Потребляет: `merge_families` (`work_families.py:948`), `WorkFamilyError`, Task 5, Task 8.
- Производит:

```python
REFUSE_MERGE_SCHEMA_BUILDING: Final = "merge_schema_building"
```

**Утверждения**
- отказ при `building` у источника и у цели (два входа);
- **у цели есть текущая версия**: замороженные версии источника у цели —
  `superseded` с новыми номерами, отменённые — `cancelled`; варианты источника с
  `family_id` цели; задания значений переехавшим контекстам с вариантом;
- **у цели нет текущей версии**: замороженная версия источника становится
  текущей у цели; контексты и варианты источника не меняются и **заданий не
  получают**, задания значений ставятся прежним контекстам цели;
- **обе без схемы**: у цели версия `building` и задание `family_schema`;
- оба составных FK истинны при `commit` (`SET CONSTRAINTS ALL IMMEDIATE` перед
  ним) — в каждой из трёх ветвей;
- чужие ожидания на источник перенаправлены на цель с новым заданием и событием
  `redirected`; у предложений с семьёй-источником `family_id` стал целью;
- тесты слияния семей фичи 1 зелёные без правок.

**Имена**
- Заводятся: `REFUSE_MERGE_SCHEMA_BUILDING`.
- Существуют: `merge_families`, `WorkFamilyError` (`:102`).

**Проверка**
- `just test-int-local-k work_variants` — ДО ≥ 252, ПОСЛЕ ≥ 265.
- `just test-int-local-k work_families` — ДО 135, ПОСЛЕ ≥ 135.

### Task 13: глобальная пометка строки и нормативы

**Files**
- Edit: `backend/services/review.py`, `backend/crud/rate_standards.py`
- Test: `backend/tests/integration/test_work_variants_position_kind.py`

**Interfaces**
- Потребляет: `_write_manual_cache` (`review.py:145`), `_mark_contexts_not_applicable`, `_require_refs` (`crud/rate_standards.py:150`), `create_rate_standard` (`:197`).
- Производит:

```python
def set_position_kind_global(db: Session, *, position_id: int, kind: str, actor_id: int) -> None
```

**Утверждения**
- для любой строки `POSITION` (продвинутой правилом и утверждённой человеком);
  `TO_REVIEW`, `HEADER`, `TRASH` — отказ;
- при нормативе — отказ `409` с перечнем (`id`, класс, период); без них —
  `HEADER`/`TRASH`, контексты `NOT_APPLICABLE`, опустевшие варианты
  архивированы, ручная запись кэша на строку;
- порядок блокировок «строка → семьи → варианты → контексты»: параллельные
  пометка и `apply_values` одного контекста — без deadlock;
- гонка с `create_rate_standard`: при параллельном выполнении ровно одна
  операция успешна (тест проверен снятием `FOR SHARE` в `_require_refs`);
- тесты нормативов зелёные без правок.

**Имена**
- Заводятся: `set_position_kind_global`.

**Проверка**
- `just test-int-local-k work_variants` — ДО ≥ 265, ПОСЛЕ ≥ 275.
- `just test-int-local-k rate_standards` — ДО 33, ПОСЛЕ ≥ 33; `just test-int-local-k test_matching` — ДО 44, ПОСЛЕ ≥ 44.

### Task 14: точки инварианта — матрица, архитектурный и структурный тесты

**Files**
- Create: `backend/tests/integration/test_semantic_queue_hooks_work_variants.py`
- Edit: `backend/tests/unit/test_semantic_queue_architecture.py`, перечисленные модули — только вызов сверки, где его нет

**Interfaces**
- Потребляет: всё из Task 5–13.
- Производит: класс-точку на каждую новую операцию §2.7.

**Утверждения**
- каждая новая точка §2.7 (обработка результата, `request_family_change`,
  `cancel_pending_family`, `merge_families`, `freeze_schema`, `rebuild_schema`,
  `cancel_schema_build`, `update_schema`, `merge_parameter_values`,
  `mark_context_not_work`, `set_position_kind_global`, `apply_auto_accept`)
  после себя оставляет задания по предикату и текущему отпечатку — интеграционным
  тестом, и снятие вызова сверки делает тест красным;
- точки фичи 2 сравнивают `paths_hash`: импорт, изменивший пути, ставит
  `context_values`;
- архитектурный тест: запись в `catalog_contexts.work_variant_id|pending_family_id`,
  `work_variants`, `context_parameter_values`, `family_parameter_*`,
  `catalog_positions.kind` — только из allowlist; структурный тест видит новый
  модуль в матрице.

**Проверка**
- `just test-int-local-k semantic_queue_hooks` — ДО 74, ПОСЛЕ ≥ 87 (по классу на каждую из 12 новых точек и вход путей).
- `just test-unit-k semantic_queue_architecture` — ДО 54, ПОСЛЕ ≥ 58.
- `just test-int-local-k work_variants` — ДО ≥ 275, ПОСЛЕ ≥ 288 (файл матрицы попадает и в эту выборку).

### Task 15: API и чтение для экрана

**Files**
- Create: `backend/crud/work_variants.py`
- Edit: `backend/routers/semantic.py`, `backend/crud/semantic_queue.py`, `backend/crud/semantic.py`
- Test: `backend/tests/integration/test_work_variants_api.py`

**Interfaces**
- Потребляет: `_mutating` (`routers/semantic.py:177`), `_deciding` (`:214`), `_domain_error` (`:163`), `ContextFilters` (`crud/semantic.py:119`), `list_contexts` (`:595`), Task 5–13.
- Производит: маршруты таблицы §2.12; форма ответов:

```python
class SchemaOut(TypedDict):
    family_id: int; status: str | None; version: int | None; ready_to_build: bool
    parameters: list["ParameterOut"]
class ParameterOut(TypedDict):
    ordinal: int; name: str; values: list["ValueOut"]
class ValueOut(TypedDict):
    id: int; value: str; origin: str; merged_into_id: int | None
class VariantOut(TypedDict):
    id: int; values: list[str | None]; contexts: int; status: str
class ContextVariantOut(TypedDict):
    variant_id: int | None; values: list["ContextValueOut"]; split_hint: bool
    pending: "PendingOut | None"
    values_job_status: str | None  # живое задание context_values (Task 17)
class FamilyChangeOut(TypedDict):
    outcome: Literal["assigned", "pending", "unchanged"]

# crud/semantic.py — ContextFilters расширяется:
variant_state: Literal["with", "without"] | None
pending: bool | None
split_hint: bool | None
# GET /contexts принимает variant_state, pending, split_hint; предикаты — в SQL list_contexts,
# до пагинации, и total считается по ним же
```

**Утверждения**
- каждый маршрут §2.12 — `admin`; `member` — `403` (по входу на маршрут);
- `POST /contexts/{id}/family` отвечает `assigned` без варианта и `pending` с
  вариантом;
- `GET /suggestions?queue=change` — ручные и подтверждённые привязки с
  предложением другой семьи любой уверенности, автопривязки — ниже порога;
- карточка контекста несёт вариант, значения с источником, ожидание, пометку
  «к делению»; `/status` — счётчики `TO_REVIEW`/`POSITION`, вариантов, ожиданий,
  семей без схемы;
- `409` с перечнем нормативов при глобальной пометке; `409` при слиянии семей с
  `building`;
- `list_jobs` отдаёт строку `family_schema` по семье;
- фильтры контекстов `variant_state`, `pending`, `split_hint` сужают выборку в
  SQL до пагинации: `total` равен числу подходящих контекстов на всех страницах,
  а не на текущей (вход: подходящих больше одной страницы).

**Имена**
- Заводятся: модуль `crud/work_variants.py`, типы выше.

**Проверка**
- `just test-int-local-k work_variants` — ДО ≥ 288, ПОСЛЕ ≥ 318.
- `just test-int-local-k semantic_queue_api` — ДО 197, ПОСЛЕ ≥ 197.

### Task 16: фронтенд — схема и варианты семьи

**Files**
- Create: `frontend/src/pages/families/SchemaBlock.tsx`, `VariantsTable.tsx`, `SchemaEditDialog.tsx`, `MergeValuesDialog.tsx` и их `*.test.tsx`
- Edit: `FamiliesTab.tsx`, `labels.ts`, `services/api/domain.ts`, `services/queries.ts`, `services/queryKeys.ts`, `types/domain.ts`, `test/handlers.ts`

**Interfaces**
- Потребляет: маршруты Task 15, `PreviewDialog.tsx` (существует).
- Производит: `useFamilySchema`, `useFamilyVariants`, `useRebuildSchema`, `useCancelSchemaBuild`, `useUpdateSchema`, `useMergeValues`.

**Утверждения**
- блок «Схема и варианты» на карточке семьи: параметры и значения с
  происхождением, пометки «схема ждёт перезапроса единицы» / «схема строится»;
- «Пересобрать…» через `PreviewDialog` с `preview_hash`; «Отменить пересборку»;
  «Править схему…» отказывает на смысловое переименование подписью ответа;
  «Слить значения…» — источник и цель одного параметра;
- таблица вариантов: набор (пусто — «не уточнено»), число контекстов, статус;
- коды на экран не выходят — только подписи `labels.ts`.

**Проверка**
- `cd frontend && npx vitest run src/pages/families` — ДО 459, ПОСЛЕ ≥ 475.
- `just typecheck-frontend`, `just lint-frontend` — зелёные.

### Task 17: фронтенд — карточка контекста, «Смена семьи», шапка

**Files**
- Create: `ContextVariant.tsx`, `PendingFamilyBlock.tsx`, `MarkPositionDialog.tsx`, `ChangeQueue.tsx` и их `*.test.tsx`
- Edit: `ContextCard.tsx`, `ContextsTab.tsx`, `SuggestionsTab.tsx`, `SuggestionsHeader.tsx`, `labels.ts`, API-слой как в Task 16

**Interfaces**
- Потребляет: маршруты Task 15, включая параметры `variant_state`, `pending`, `split_hint` у `GET /contexts`.
- Производит: `useMarkNotWork`, `useCancelPendingFamily`, `useMarkPositionKind`, `useChangeQueue`; `ContextsParams` расширен тремя фильтрами.

**Утверждения**
- карточка: вариант со значениями и источником каждого; «к делению: разделы
  расходятся» с переходом к членствам; «Ожидает семьи: … (кто, когда, порог)» с
  «Отменить»; «вариант пересчитывается» / «ожидает значений по схеме цели»;
- «Не работа»; «Другая семья…» показывает исход `assigned` / `pending`;
- «Пометить написание целиком…» с предупреждением о будущих вхождениях и
  выводом перечня нормативов при `409`;
- фильтры контекстов «с вариантом / без варианта / ожидает / к делению» уходят
  в запрос параметрами, а не фильтруют загруженную страницу (вход: обработчик
  MSW получает параметры);
- четвёртая очередь «Смена семьи» с группами «семья → семья + полоса»;
- счётчики шапки и пометка «принято автоматически»;
- вид сверен снимками стенда против перечня элементов §2.12 (макета нет).

**Проверка**
- `cd frontend && npx vitest run src/pages/families` — ДО ≥ 475, ПОСЛЕ ≥ 495.
- `just typecheck-frontend`, `just lint-frontend` — зелёные.

### Task 18: ревизия `AGENTS.md`, справочник, дорожная карта, правка спеки

**Files**
- Edit: `AGENTS.md`, `docs/AGENTS-revisions.md`, `docs/reference/screens.md`, `docs/reference/schema.md` (сверка с Task 1), `docs/product-roadmap.md`, спека (решение плана 2)

**Утверждения**
- §3 и §5 — по §2.14 спеки; преамбула — новая ревизия, врезка v6.26 в архиве;
- §9.1 (решение пользователя 03.10.2026, вне спеки): фраза «Внешнее ревью
  гейта — пуш ветки и комментарий `@codex review` в PR, при желании с
  указанием, что смотреть» заменяется на: внешнее ревью гейта и реализации
  делает Codex, которого пользователь запускает на удалённой машине; результат
  приходит в PR обычным комментарием от его аккаунта; сессия ничего не
  вызывает — пушит ветку, сообщает, что ушло, и разбирает комментарии,
  проверяя каждое замечание по фактам (правило «замечания проверяются по
  фактам» §9.1 сохраняется). Абзац «Чем выстрадано» дополняется причиной
  смены: способ дешевле; локальный Codex по-прежнему зависает. После правки в
  `AGENTS.md` вне архива ни одного вхождения `@codex review` (`grep -c` — 0);
- `screens.md` `## 9.` — три абзаца, якорь не тронут;
- дорожная карта — А1 сдвинут, «3а сделана, 3б следующая»; вопрос о смысле
  `TO_REVIEW` снят;
- спека: `request_family_change` назван в `services/family_change.py`;
  §2.5 об отдельной транзакции автопринятия уже поправлен решением
  пользователя 03.10.2026 (коммит гейта 3); вердикт новых видов под
  блокировками (решение плана 8) совпадает с §2.6 и правки не требует.

**Проверка**
- `just check-agents-index` — 18 из 18.
- `grep -c "@codex review" AGENTS.md` — 0.

### Task 19: стенд, замеры, разметка, devlog, PR

**Files**
- Create: `docs/devlog/2026-10-03-catalog-variants.md`

**Утверждения**
- **Замер:** доля `schema_error` на ≥ 50 вызовах `family_schema` и ≥ 200
  `context_values` — в devlog; больше 2 % — разбор до мержа;
- **стенд:** порог из issue #56 задан (без него DoD не выполнен);
  `semantic-auto-accept` через preview; `semantic-schemas-backfill`; значения
  сверкой; в devlog — строк вне `TO_REVIEW`, вариантов, доля «не уточнено»,
  `path_conflict`, стоимость трёх шагов;
- **разметка:** 100 контекстов пользователем — доля значений, которые на деле
  место, — в devlog;
- матрица непуста, правило цены наблюдаемо; перемерены долги 34 и 13;
- devlog называет тронутые области и файлы граблей (`db.md`, `backend.md`,
  `frontend.md`, `runs.md`, `process.md`);
- `just ci` зелёный;
- **финальное ревью ветки — Codex**, которого пользователь запускает на
  удалённой машине; результат приходит в PR #57 комментарием от его аккаунта.
  Сессия ничего не вызывает (`@codex review` не ставится; отдельного ревью
  Fable нет — решение пользователя 07.10.2026): пушит ветку, сообщает, что
  ушло, и разбирает комментарии по фактам — каждая находка проверена по коду,
  принятая — правкой и коммитом, непринятая — доводом пользователю; после
  правок — снова `just ci`;
- PR #57 переводится из черновика **решением пользователя** после ревью;
  мерж — тоже пользователь.

**Проверка**
- `just ci` — зелёный.
- Итоги разбора комментариев Codex — в devlog: число находок, принятые и
  отвергнутые с доводом.

## Команды проверки

- По задаче: в самой задаче.
- По фиче: `just ci`; стенд `gca_dev` по Task 19.
