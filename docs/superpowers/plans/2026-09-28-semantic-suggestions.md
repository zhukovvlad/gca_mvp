# План: семантические предложения

**Спека:** `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md` (гейт 2 пройден 28.09.2026, два круга ревью)
**Ветка:** `feat/semantic-suggestions`

## Global Constraints

- Всё новое — только `admin` (`Depends(require_admin)`, как у соседей в
  `routers/semantic.py`); `member` получает `403` (спека §2.1).
- **Наружу — только то, что перечисляет спека §2.2**: наименование строки,
  единица, статья, один путь разделов, семьи единицы. Цен, объёмов, количеств,
  подрядчиков, договоров, объектов в теле запроса нет — ни в одной задаче.
- Деньги — `Decimal` и `numeric`, никаких `float` (`AGENTS.md` §3); тарифы — настройки.
- Операции фичи 1 не меняют ни отказов, ни блокировок; меняется ровно то, что
  называет спека: `assign_family` получает `source`/`suggestion_id` (§2.9), точки
  перечня §2.7 получают вызов сверки, `run_startup_maintenance` — возврат
  `running` (§2.5).
- Порядок блокировок: семья раньше контекста (`assign_family`), корзины — через
  `lock_buckets`; захват — строка `semantic_worker_state` раньше задания (§2.5).
- Сверка вызывается **до** существующего `commit` каждой точки перечня, в той же
  транзакции; её исключение откатывает операцию целиком.
- Вызов модели — никогда внутри открытой транзакции.
- В тестах `RUN_SEMANTIC_WORKER = false`; сеть в тестах не открывается —
  клиент модели внедряется (`ModelClient`).
- Фронтенд — только shadcn/ui; недостающие компоненты — `npx shadcn add`.
- `docs/reference/screens.md` `## 9.`: заголовок и `EXPECTED_SCREEN_ANCHORS` не
  трогаются; меняется только тело раздела (Task 16).
- Номер ревизии `AGENTS.md` называется только в коммите самой ревизии (Task 16).

## Структура файлов

```
backend/
  alembic/versions/2026_09_28_0018-semantic_queue.py   создаётся
  alembic/env.py                    правка: RAW_SQL_INDEXES (два частичных уникальных индекса: опубликованное предложение, held-пачка)
  models.py                         правка: пять моделей, перечисления, CK_*
  config.py                         правка: OPENROUTER_API_KEY, RUN_SEMANTIC_WORKER, SEMANTIC_*
  services/semantic_request.py      создаётся: материал, путь, применимость, render
  services/semantic_answer.py       создаётся: строгий разбор и схема ответа
  services/semantic_privacy.py      создаётся: словарь и поиск совпадений
  services/semantic_cost.py         создаётся: тарифы, резерв, бюджет, потолок
  services/semantic_reconcile.py    создаётся: reconcile_semantic_jobs, пачки
  services/semantic_worker.py       создаётся: захват, запись, повторы, предохранитель
  services/semantic_client.py       создаётся: ModelClient, OpenRouterClient
  services/semantic_runner.py       создаётся: поток-опросчик
  services/semantic_decisions.py    создаётся: решения admin, preview, пачки, остановка
  services/work_families.py         правка: assign_family(source, suggestion_id), вызов сверки
  services/semantic_events.py       правка: payload context_family_assigned.suggestion_id
  services/estimate_import.py, round_import.py, context_operations.py,
  services/review.py, catalog_backfill.py                правка: вызов сверки
  crud/contracts.py, crud/tenders.py                     правка: сбор контекстов до удаления, сверка
  crud/semantic_queue.py            создаётся: чтение очередей, статуса, заданий
  services/maintenance.py           правка: возврат running → pending
  main.py                           правка: запуск/остановка опросчика в lifespan
  routers/semantic.py               правка: маршруты §2.13
  cli.py                            правка: semantic-enqueue-all
  tests/unit/test_semantic_queue_*.py, tests/integration/test_semantic_queue_*.py   создаются
  tests/conftest.py                 правка: _DOMAIN_TABLES
frontend/src/
  pages/families/SuggestionsTab.tsx, SuggestionsHeader.tsx, SuggestionGroups.tsx,
  pages/families/NewQueue.tsx, ErrorsQueue.tsx, PreviewDialog.tsx, CreateFamilyDialog.tsx   создаются
  pages/families/FamiliesPage.tsx   правка: третья вкладка
  pages/families/labels.ts          правка: подписи очередей и причин
  services/api/domain.ts, services/queries.ts, types/domain.ts, test/handlers.ts   правка
  pages/families/*.test.tsx         создаются/правятся
docs/reference/schema.md            правка: блок «Очередь семантических предложений»
docs/reference/screens.md           правка: ## 9. — вкладка «Предложения»
docs/product-roadmap.md             правка: пункт «семантические предложения»
AGENTS.md                           правка: §3, §5, преамбула; docs/AGENTS-revisions.md — архив v6.24
docs/devlog/2026-09-28-semantic-suggestions.md   создаётся
```

## Решения плана, которых нет в спеке

1. **Сверка называется `reconcile_semantic_jobs`**, а не `reconcile_contexts` как в
   дизайне: имя занято `services/review.reconcile_contexts` (сведение корзин при
   слиянии в Review). Спека поправлена той же веткой.
2. **Модули по слоям**: запрос, ответ, проверка, расход, сверка, исполнитель,
   клиент, поток, решения — отдельными файлами `services/semantic_*.py`; чтение для
   экрана — `crud/semantic_queue.py`. Так каждая задача правит свой модуль и
   тестируется без соседних.
3. **Порядок задач — снизу вверх**: схема (1) → запрос (2) → ответ (3) → проверка
   (4) → расход (5) → сверка (6) → `assign_family` (7) → точки инварианта (8, 9) →
   исполнитель (10) → поток и восстановление (11) → решения (12) → API (13) →
   фронтенд (14, 15) → ревизия и справочник (16) → стенд и финал (17). Точки
   инварианта идут после сверки, а исполнитель — после точек: иначе тест захвата
   «отпечаток не совпал» опирался бы на сверку, которой ещё нет.
4. **Промпт — константа `SEMANTIC_PROMPT` в `services/semantic_request.py`**
   с `PROMPT_VERSION = 1`; текст — буквально из
   `tasks/catalog-pilot-2026-09-18/exp_family_assign.py`, с одной правкой: единица в
   списке семей подписана кодом (как в замерах).
5. **Клиент модели синхронный** (`httpx.Client`), параллельность — пул потоков
   размера `SEMANTIC_CONCURRENCY` внутри опросчика: ORM синхронный (`AGENTS.md` §3),
   и асинхронный цикл пришлось бы мостить к нему.
6. **Словарь проверки строится при каждом захвате**, без кэша: 65 записей на стенде,
   запрос дешевле инвалидации.
7. **Все новые тесты — в файлах `test_semantic_queue_*.py`**, поэтому команда
   `-k semantic_queue` выбирает их все и ни одного старого (ДО — 0).
8. **Точка сверки импорта — `run_import_job`**, после `route_positions` и до
   `finalize_done`; контексты вытесненных смет собираются в `import_estimate` /
   `import_round` до удаления и едут полем `replaced_context_ids`
   исходов импорта (Task 8).
9. **Дедупликация удержанных пачек** — атомарная, частичным уникальным индексом
   по `fingerprints_hash` среди `held` и `INSERT … ON CONFLICT DO NOTHING RETURNING`
   с чтением существующей (Task 1, 6); спека дополнена той же веткой.
10. **Архивирование остановки захвата в журнал не пишется**: история остановок — на
   попытках (`reserve_exceeded`), автор последнего снятия — в строке состояния
   (спека §2.4).

## Задачи

### Task 1: схема очереди — миграция `0018`, модели, справочник

**Files**
- Create: `backend/alembic/versions/2026_09_28_0018-semantic_queue.py`
- Edit: `backend/models.py`, `backend/alembic/env.py`, `backend/tests/conftest.py`, `docs/reference/schema.md`
- Test: `backend/tests/integration/test_semantic_queue_schema.py`

**Interfaces**
- Потребляет: `Base`, `_sql_str_list`, `RAW_SQL_INDEXES`, `_DOMAIN_TABLES`, `CatalogContext`, `WorkFamily`, `ImportJob`, `User` (существуют).
- Производит:

```python
class SemanticJobStatus(str, enum.Enum): pending, running, done, error, cancelled, privacy_hold
class SemanticCancelReason(str, enum.Enum): input_changed, not_applicable, privacy_declined, stale_hold
class SemanticAttemptOutcome(str, enum.Enum): ok, transient_error, permanent_error, schema_error, lost_claim
class SuggestionUnpublishedReason(str, enum.Enum): stale_fingerprint, lost_claim, context_not_applicable, rejected
class SuggestionDecision(str, enum.Enum): accepted, rejected, other_family, family_created
class ReconcileBatchSource(str, enum.Enum): import_, operation, mass, unit_reask, config_reask   # значение 'import'
class ReconcileBatchStatus(str, enum.Enum): held, approved, discarded

class SemanticJob(Base): ...            # __tablename__ = "semantic_jobs"
class SemanticJobAttempt(Base): ...     # "semantic_job_attempts"
class FamilySuggestion(Base): ...       # "family_suggestions"
class SemanticReconcileBatch(Base): ... # "semantic_reconcile_batches"
class SemanticWorkerState(Base): ...    # "semantic_worker_state"
```

**Утверждения**
- `alembic upgrade head` и `downgrade -1` проходят на пустой базе и на базе с
  данными фичи 1; после `upgrade` в `semantic_worker_state` ровно одна строка
  `id = 1`, `claim_paused = false`;
- каждый `CHECK` спеки §2.4 отвергает свой запрещённый вход отдельной вставкой и
  пропускает соседний допустимый: `cancelled` без `cancel_reason` и `pending` с
  ним; `running` без `claim_token` и `pending` с ним; `privacy_hold` без
  `privacy_matches`; решение без автора и автор без решения; опубликованное с
  причиной неопубликования; пачка `held` с автором и `approved` без него;
  остановленный исполнитель без причины, попытки, времени — по одной вставке на
  поле; `last_resumed_by` без `last_resumed_at`; вторая строка состояния (`id = 2`);
- `UNIQUE (context_id, request_hash)` отвергает дубль задания; частичный индекс
  «не больше одного опубликованного на контекст» отвергает второе опубликованное и
  пропускает второе неопубликованное; частичный `UNIQUE (fingerprints_hash) WHERE
  status = 'held'` отвергает вторую `held`-пачку с тем же хэшем и пропускает
  `approved`/`discarded` с ним же;
- удаление контекста, на который ссылается задание, отвергается (`RESTRICT`);
- паритет: литералы `IN (...)` миграции равны значениям перечислений `models.py`
  для всех семи перечислений — тест сравнивает с независимым литералом в тесте;
- пять таблиц есть в `_DOMAIN_TABLES`; блок «Очередь семантических предложений»
  есть в `docs/reference/schema.md`, `just check-agents-index` — 18 из 18.

**Имена**
- Заводятся этой задачей: семь перечислений и пять моделей выше, миграция `0018`.
- Существуют, проверено `grep`-ом: `_sql_str_list`, `RAW_SQL_INDEXES`, `_DOMAIN_TABLES`, `CatalogContext`, `WorkFamily`.

**Проверка**
- `just test-int-local-k semantic_queue` — ДО 0, ПОСЛЕ ≥ 25.
- `just test-int-local-k semantic_schema` — ДО 96, ПОСЛЕ 96 (не сломано).
- `just check-agents-index` — 18 из 18.

### Task 2: запрос — материал, путь, применимость, каноническое тело

**Files**
- Create: `backend/services/semantic_request.py`
- Edit: `backend/config.py`
- Test: `backend/tests/unit/test_semantic_queue_request.py`, `backend/tests/integration/test_semantic_queue_material.py`

**Interfaces**
- Потребляет: `chapter_paths` (существует, `services/context_routing.py`), `PLACE_DICTIONARY_VERSION` (существует, `services/semantic_rules.py`), `CatalogContext`, `ContextMember`, `ContextBucket`, `CatalogPosition`, `WorkFamily`, `WorkCategory`, `UnitOfMeasure`, `FamilyStatus`, `SemanticKind`, `SemanticState` (существуют).
- Производит:

```python
PROMPT_VERSION: int
SERIALIZATION_VERSION: int
SEMANTIC_PROMPT: str

@dataclass(frozen=True)
class CandidateFamily:
    id: int
    title: str
    unit_code: str | None
    definition: str

@dataclass(frozen=True)
class ContextRequestMaterial:
    context_id: int
    unit_id: int | None
    unit_code: str | None
    title: str
    article: str | None            # "код название"
    path_counts: tuple[tuple[str, int], ...]   # (путь сверху вниз через " / ", число позиций)
    member_count: int
    archived: bool
    semantic_state: str
    semantic_kind: str
    work_family_id: int | None
    candidates: tuple[CandidateFamily, ...]    # активные семьи единицы, по id

@dataclass(frozen=True)
class RenderedRequest:
    body: dict                     # ровно то, что уйдёт провайдеру
    request_hash: str
    prefix_hash: str
    candidates_hash: str
    input_hash: str
    prefix_bytes: int
    user_bytes: int
    place_dictionary_version: int  # аудит задания; в тело и хэши не входит (спека §2.10)

def load_request_material(db: Session, context_ids: Collection[int]) -> dict[int, ContextRequestMaterial]
def top_path(path_counts: Sequence[tuple[str, int]]) -> str
def is_applicable(material: ContextRequestMaterial) -> bool
def render_context_request(material: ContextRequestMaterial, *, settings: Settings) -> RenderedRequest
```

**Утверждения**
- `top_path`: наибольшее число позиций; при равенстве — лексикографически
  меньший путь; результат не зависит от порядка входа (перестановки одного набора
  дают один путь);
- `load_request_material` строит `path_counts` по **всем** членствам контекста,
  включая `STALE`; число SQL-запросов не растёт с числом контекстов (10 и 60
  контекстов — одинаково);
- `is_applicable` — `false` ровно при одном из: архивный; ноль членств;
  `NOT_APPLICABLE`; `SYSTEM`; есть семья; нет активных семей единицы — каждое
  предъявлено отдельным входом, отличающимся от применимого ровно им;
- `render_context_request` детерминирован; `request_hash` меняется при смене
  каждой оси тела: пути, статьи, наименования, единицы, снимка кандидатов,
  `PROMPT_VERSION`, модели, `max_tokens`, флага рассуждения, места метки кэша; и
  **не** меняется при смене порядка кандидатов на входе (снимок сортируется по
  `id`);
- **смена `PLACE_DICTIONARY_VERSION` не меняет ни `request_hash`, ни `prefix_hash`**
  и не попадает в тело (словаря мест и `name_role` в запросе нет — спека §2.2,
  §2.10), но попадает в `RenderedRequest.place_dictionary_version`; смена `name_role`
  контекста тоже не меняет `request_hash` — отдельный вход;
- `prefix_hash` не зависит от строки контекста (два контекста одной единицы —
  один `prefix_hash`) и меняется со снимком кандидатов, промптом, моделью;
- тело содержит `temperature = 0`, `max_tokens = 600`, `reasoning.enabled = false`,
  `cache_control` на блоке списка семей, и **не содержит** ни одного из ключей и
  значений: цена, объём, количество, имя подрядчика, номер договора, имя объекта —
  тест строит материал из позиций с такими данными и ищет их в сериализованном теле;
- настройки заведены: `OPENROUTER_API_KEY`, `RUN_SEMANTIC_WORKER` (по умолчанию
  `false`), `SEMANTIC_MODEL`, `SEMANTIC_MAX_TOKENS`, `SEMANTIC_CONCURRENCY`,
  `SEMANTIC_CALL_TIMEOUT_S`, `SEMANTIC_MAX_ATTEMPTS`, четыре тарифа
  (`Decimal`), `SEMANTIC_DAILY_BUDGET_USD`, `SEMANTIC_EVENT_MAX_CONTEXTS`,
  `SEMANTIC_EVENT_MAX_RESERVE_USD`.

**Имена**
- Заводятся этой задачей: все имена блока «Производит», перечисленные настройки.
- Существуют, проверено `grep`-ом: `chapter_paths`, `PLACE_DICTIONARY_VERSION`, `FamilyStatus`, `SemanticKind`, `SemanticState`, `Settings`.

**Проверка**
- `just test-unit-k semantic_queue` — ДО 0, ПОСЛЕ ≥ 12.
- `just test-int-local-k semantic_queue` — ДО ≥ 25 (Task 1), ПОСЛЕ ≥ 29.

### Task 3: ответ — строгий разбор и схема

**Files**
- Create: `backend/services/semantic_answer.py`
- Test: `backend/tests/unit/test_semantic_queue_answer.py`

**Interfaces**
- Потребляет: `CandidateFamily` (Task 2).
- Производит:

```python
RESPONSE_SCHEMA_VERSION: int

@dataclass(frozen=True)
class ModelAnswer:
    family_id: int | None          # None = «новая»
    new_family_name: str | None
    is_system: bool                # «СИСТЕМА»
    confidence: Decimal
    reason: str

class AnswerSchemaError(Exception):
    code: str                      # not_json | extra_text | missing_field | bad_type | unknown_family | bad_confidence | empty_name | empty_reason
    detail: str                    # человекочитаемое — ложится в semantic_job_attempts.validation_error

def parse_model_answer(raw: str, candidates: Sequence[CandidateFamily]) -> ModelAnswer
```

**Утверждения**
- чистый JSON и JSON в одном блоке `` ```json `` — разбираются одинаково;
- JSON с текстом до или после, два JSON-объекта, JSON внутри прозы — `extra_text`
  или `not_json`, **не** извлекаются (вход, который разобрал бы регулярный поиск
  объекта, — отдельный тест);
- `family_id` не из переданного снимка — `unknown_family`; `0` — «новая»;
  `confidence` вне `[0, 1]` и нечисловой — `bad_confidence`; `0` без имени —
  `empty_name`; пустой `reason` — `empty_reason`; каждое — отдельным входом;
- `0` с именем `СИСТЕМА` (в любом регистре) — `is_system = true`;
- `confidence` — `Decimal`, `float` в результате нет.

**Имена**
- Заводятся этой задачей: `RESPONSE_SCHEMA_VERSION`, `ModelAnswer`, `AnswerSchemaError`, `parse_model_answer`.
- Существуют: `CandidateFamily` (Task 2).

**Проверка**
- `just test-unit-k semantic_queue` — ДО ≥ 12, ПОСЛЕ ≥ 24.

### Task 4: проверка перед отправкой — словарь и совпадения

**Files**
- Create: `backend/services/semantic_privacy.py`
- Test: `backend/tests/unit/test_semantic_queue_privacy.py`, `backend/tests/integration/test_semantic_queue_privacy_dict.py`

**Interfaces**
- Потребляет: модели объектов, подрядчиков, договоров, тендеров (существуют); `RenderedRequest` (Task 2).
- Производит:

```python
@dataclass(frozen=True)
class PrivacyEntry:
    kind: str          # object | contractor | contract | tender
    text: str          # нормализованная форма

@dataclass(frozen=True)
class PrivacyDictionary:
    entries: tuple[PrivacyEntry, ...]
    digest: str

@dataclass(frozen=True)
class PrivacyMatch:
    text: str
    kind: str
    where: str         # "context" | "family:<id>" | "prompt"

def build_privacy_dictionary(db: Session) -> PrivacyDictionary
def normalize_org_name(title: str) -> str
def find_privacy_matches(dictionary: PrivacyDictionary, rendered: RenderedRequest) -> tuple[PrivacyMatch, ...]
```

**Утверждения**
- `normalize_org_name` снимает ООО, АО, ПАО, ЗАО, ОАО, ИП, ТОО, LLP, ГК, СЗ и кавычки
  всех видов («», "", ''); `ООО «Каркас Монолит»` → `каркас монолит`;
- совпадение — по границе слова без учёта регистра: `Каркас Монолит` в строке —
  совпадение; `каркасмонолит` и `Каркас` отдельно — нет (производных слов нет);
- совпадение в строке контекста даёт `where = "context"`, в определении семьи 7 —
  `"family:7"`; оба сразу — два совпадения;
- набор совпадений упорядочен и детерминирован (один вход — один кортеж);
- `digest` словаря меняется при добавлении подрядчика и не меняется от порядка
  строк в базе;
- на тестовой базе со словарём из подрядчика «ВЫБОР» строка «выбор по образцу»
  совпадает (ложное срабатывание — известное и задерживающее, спека §1.7).

**Имена**
- Заводятся этой задачей: имена блока «Производит».
- Существуют: `RenderedRequest` (Task 2).

**Проверка**
- `just test-unit-k semantic_queue` — ДО ≥ 24, ПОСЛЕ ≥ 32.
- `just test-int-local-k semantic_queue` — ДО ≥ 29, ПОСЛЕ ≥ 32.

### Task 5: расход — резерв, бюджет, потолок

**Files**
- Create: `backend/services/semantic_cost.py`
- Test: `backend/tests/integration/test_semantic_queue_cost.py`

**Interfaces**
- Потребляет: `SemanticJobAttempt` (Task 1), `RenderedRequest` (Task 2), `Settings`.
- Производит:

```python
RESERVE_FORMULA_VERSION: int

@dataclass(frozen=True)
class Tariffs:
    input_per_m: Decimal
    cache_write_per_m: Decimal
    cache_read_per_m: Decimal
    output_per_m: Decimal

@dataclass(frozen=True)
class EventCap:
    max_contexts: int
    max_reserve_usd: Decimal

def tariffs_from(settings: Settings) -> Tariffs
def known_prefix_tokens(db: Session, prefix_hash: str) -> int | None
def reserve_for(db: Session, rendered: RenderedRequest, tariffs: Tariffs, max_tokens: int) -> Decimal
def expected_cached_cost(db: Session, rendered: RenderedRequest, tariffs: Tariffs) -> Decimal
def spent_last_24h(db: Session, *, now: datetime) -> Decimal
def exceeds_cap(count: int, reserve_total: Decimal, cap: EventCap) -> bool
```

**Утверждения**
- `known_prefix_tokens` — **максимум** `cache_write_tokens`/`cached_tokens` по всем
  попыткам с этим `prefix_hash` при разных `actual_model`/`provider`; без попыток —
  `None`; попытки другого `prefix_hash` не влияют;
- `reserve_for` при известном префиксе = префикс × тариф записи + байты строки ×
  тариф входа + `max_tokens` × тариф выхода — сверка с независимо посчитанным
  `Decimal`; без наблюдений префикс считается байтами `prefix_bytes`;
- результат — `Decimal`, без `float` на пути (тест подменяет тариф значением,
  которое `float` не представляет точно, и сравнивает точно);
- `spent_last_24h` = сумма `cost_usd` завершённых + `reserve_usd` незавершённых
  попыток в окне; попытка 24 ч + 1 с назад не входит, ровно на границе — входит;
- `exceeds_cap`: ровно `max_contexts` — не превышение, на единицу больше —
  превышение; то же для денег на границе и на копейку выше.

**Имена**
- Заводятся этой задачей: имена блока «Производит».
- Существуют: `SemanticJobAttempt` (Task 1), `RenderedRequest` (Task 2).

**Проверка**
- `just test-int-local-k semantic_queue` — ДО ≥ 32, ПОСЛЕ ≥ 40.

### Task 6: сверка — `reconcile_semantic_jobs` и удержанные пачки

**Files**
- Create: `backend/services/semantic_reconcile.py`
- Test: `backend/tests/integration/test_semantic_queue_reconcile.py`

**Interfaces**
- Потребляет: `load_request_material`, `is_applicable`, `render_context_request` (Task 2), `reserve_for`, `expected_cached_cost`, `EventCap`, `exceeds_cap` (Task 5), модели Task 1.
- Производит:

```python
NO_CAP: object                     # маркер «без потолка» для подтверждённого admin

@dataclass(frozen=True)
class ReconcileReport:
    created: int
    revived: int
    cancelled: int
    republished: int
    unpublished: int
    held_batch_id: int | None

def reconcile_semantic_jobs(
    db: Session, context_ids: Collection[int], *, cap: EventCap | object,
    source: str, import_job_id: int | None = None,
) -> ReconcileReport
def held_fingerprints(db: Session, context_ids: Collection[int]) -> list[tuple[int, str]]
    # канонически отсортировано по (context_id, request_hash)
def fingerprints_hash(fingerprints: Sequence[tuple[int, str]]) -> str
def get_or_create_held_batch(db: Session, *, fingerprints: Sequence[tuple[int, str]], source: str,
                             import_job_id: int | None, unit_id: int | None,
                             reserve_estimate_usd: Decimal, cached_estimate_usd: Decimal) -> int
    # INSERT … ON CONFLICT (fingerprints_hash) WHERE status='held' DO NOTHING RETURNING id;
    # при конфликте — SELECT существующей
```

**Утверждения**
- каждая строка таблицы исходов спеки §2.7 — отдельный вход: нет задания →
  `pending`; `pending`/`running`/`privacy_hold` с текущим отпечатком — не
  меняются; `done` без решения и неопубликованное — публикуется, вызова нет;
  `done` отклонённое — не меняется; `cancelled`/`input_changed` и
  `cancelled`/`not_applicable` — то же задание в `pending`, `retry_generation`
  +1, `privacy_released_matches` сброшен; `cancelled`/`privacy_declined` — не
  меняется; `cancelled`/`stale_hold` — `pending` без разрешения; `error` — не
  меняется;
- у применимого контекста задание со старым отпечатком: `pending` →
  `cancelled`/`input_changed`, `privacy_hold` → `cancelled`/`stale_hold`;
- неприменимый контекст: `pending` и `privacy_hold` → `cancelled`/`not_applicable`,
  опубликованное → `context_not_applicable`;
- при `cap = EventCap(...)` и наборе сверх потолка (контексты или резерв) заданий
  не создаётся, создаётся пачка `held` с `held_fingerprints` = пары текущих
  отпечатков, `contexts_count`, оценками резерва и цены с кэшем; ровно на потолке —
  задания создаются;
- при `cap = NO_CAP` тот же набор ставит задания без пачки;
- **повтор сверх потолка не плодит пачек**: если среди пачек `held` уже есть
  пачка с тем же `fingerprints_hash`, новая не создаётся, отчёт возвращает её
  `held_batch_id` — отдельный вход (два вызова подряд сверх потолка на неизменных
  данных — одна пачка); другой набор — новая пачка; пачка `approved`/`discarded` с
  тем же хэшем дедупликации не мешает;
- `fingerprints_hash` не зависит от порядка входа (перестановки одного набора —
  один хэш);
- **гонка дедупликации**: две параллельные сверки одного набора сверх потолка в
  разных сессиях — одна пачка, обе получают один `batch_id`; тест проверен
  заменой `ON CONFLICT DO NOTHING` + чтения на «прочитать, затем вставить»
  (вторая сессия получает нарушение уникальности либо вторую пачку — красное);
- материал грузится пакетно: число запросов на 10 и 60 контекстов одинаково;
  вставка — один `INSERT … ON CONFLICT DO NOTHING`;
- вызов дважды подряд на неизменных данных — второй не меняет ни одной строки
  (и под потолком, и сверх него — по правилу дедупликации выше).

**Имена**
- Заводятся этой задачей: `NO_CAP`, `ReconcileReport`, `reconcile_semantic_jobs`, `held_fingerprints`, `fingerprints_hash`, `get_or_create_held_batch`.
- Существуют: имена Task 2, Task 5.

**Проверка**
- `just test-int-local-k semantic_queue` — ДО ≥ 40, ПОСЛЕ ≥ 56.

### Task 7: `assign_family(source, suggestion_id)` и `payload` события

**Files**
- Edit: `backend/services/work_families.py`, `backend/services/semantic_events.py`
- Test: `backend/tests/integration/test_semantic_queue_assign.py`

**Interfaces**
- Потребляет: `assign_family`, `record_event`, `EVENT_ENUM_VALUES`, `FamilySource`, `CK_CONTEXT_FAMILY_PROVENANCE` (существуют); `reconcile_semantic_jobs` (Task 6).
- Производит:

```python
def assign_family(
    db: Session, *, context_id: int, family_id: int | None, actor_id: int,
    source: FamilySource = FamilySource.manual, suggestion_id: int | None = None,
) -> CatalogContext
```

**Утверждения**
- `source = suggestion`: `family_source = suggestion`, `family_by IS NULL`,
  `family_at` заполнен; событие — `source = suggestion`, `suggestion_id`,
  `actor_id` = переданный; ограничение `CK_CONTEXT_FAMILY_PROVENANCE` выдержано;
- `source = manual` — поля и событие как до задачи (существующие тесты
  `-k work_families` зелёные без правки);
- `suggestion` без `suggestion_id` и `manual` с ним — отказ до записи;
  `payload` события с `source = suggestion` без `suggestion_id` отвергает
  `record_event`;
- назначение и снятие семьи вызывают `reconcile_semantic_jobs` для контекста:
  после снятия у применимого контекста есть `pending`; после назначения его
  `pending` — `cancelled`/`not_applicable`. Тест проверен снятием вызова.

**Имена**
- Заводятся этой задачей: параметры `source`, `suggestion_id`; ключ `payload` `suggestion_id`.
- Существуют, проверено `grep`-ом: `assign_family`, `record_event`, `EVENT_ENUM_VALUES`, `FamilySource`, `CK_CONTEXT_FAMILY_PROVENANCE`.

**Проверка**
- `just test-int-local-k semantic_queue` — ДО ≥ 56, ПОСЛЕ ≥ 62.
- `just test-int-local-k work_families` — ДО 135, ПОСЛЕ 135.
- `just test-int-local-k semantic_events` — ДО 117, ПОСЛЕ ≥ 117.

### Task 8: точки инварианта — импорт, замена, удаления

**Files**
- Edit: `backend/services/import_pipeline.py`, `backend/services/estimate_import.py`, `backend/services/round_import.py`, `backend/crud/contracts.py`, `backend/crud/tenders.py`
- Test: `backend/tests/integration/test_semantic_queue_hooks_import.py`

**Interfaces**
- Потребляет: `run_import_job`, `route_positions`, `finalize_done`, `import_estimate`, `_replace_existing`, `ImportOutcome`, `import_round`, `replace_round_estimates`, `RoundImportOutcome`, `delete_contract`, `delete_tender`, `delete_round`, `delete_participant` (существуют); `reconcile_semantic_jobs`, `EventCap` (Task 5, 6).
- Производит:

```python
def contexts_of_positions(db: Session, position_item_ids: Collection[int]) -> set[int]   # в semantic_reconcile
def contexts_of_estimates(db: Session, estimate_ids: Collection[int]) -> set[int]        # в semantic_reconcile
def event_cap_from(settings: Settings) -> EventCap                                      # в semantic_cost

# ImportOutcome и RoundImportOutcome получают поле:
#   replaced_context_ids: frozenset[int]   # контексты позиций вытесненных смет, собраны ДО удаления
```

Точка импорта — **`run_import_job`** (`services/import_pipeline.py`), а не
`import_estimate`/`import_round`: членства новых позиций строит `route_positions`
после матчинга, и сверка раньше неё видела бы контексты без новых членств. Порядок
в сессии B: импорт (с заменой — `replaced_context_ids` собираются в
`import_estimate` / `import_round` до удаления) → матчинг →
`route_positions` → `reconcile_semantic_jobs(contexts_of_estimates(estimate_ids) ∪
replaced_context_ids, cap=event_cap_from(settings), source="import",
import_job_id=job_id)` → `finalize_done`.

**Утверждения**
- сверка в пайплайне идёт **после** `route_positions` и **до** `finalize_done`:
  тест входом, где контекст появляется только маршрутизацией этого импорта, — у
  него есть `pending` (вход провалился бы при сверке внутри `import_estimate`);
- `replaced_context_ids` собираются до удаления: у контекста, чьи позиции были
  только в вытесненной смете, после замены — `cancelled`/`not_applicable`;
- импорт сметы договора: после `done` у применимых контекстов её позиций есть
  `pending` с текущим отпечатком; импорт сверх потолка события — пачка `held`
  с `import_job_id`, импорт при этом `done`, счётчики `import_jobs` не меняются;
- замена сметы и замена раунда: контексты старых **и** новых позиций сверены —
  у контекста, чей самый частый путь сменился, старое `pending` →
  `input_changed`, новое `pending` создано;
- удаление договора, тендера, раунда, участника: контекст, опустевший от
  удаления, — `cancelled`/`not_applicable`; контекст, у которого сменился самый
  частый путь, — новое `pending`; идентификаторы собраны **до** удаления (тест
  входом, где после каскада членств их уже не найти);
- исключение внутри сверки откатывает операцию целиком (смета не создана, договор
  не удалён);
- **каждый тест этой задачи проверен снятием вызова сверки в своей точке**
  (`docs/insights/verifying-guards.md`).

**Имена**
- Заводятся этой задачей: `contexts_of_positions`, `contexts_of_estimates`.
- Заводятся этой задачей также: `event_cap_from`, поле `replaced_context_ids`.
- Существуют, проверено `grep`-ом: `run_import_job`, `route_positions`, `finalize_done`, `import_estimate`, `_replace_existing`, `ImportOutcome`, `import_round`, `replace_round_estimates`, `RoundImportOutcome`, `delete_contract`, `delete_tender`, `delete_round`, `delete_participant`.

**Проверка**
- `just test-int-local-k semantic_queue` — ДО ≥ 62, ПОСЛЕ ≥ 72.
- `just test-int-local-k estimate_import` — ДО 90, ПОСЛЕ 90; `-k round_import` — ДО 25, ПОСЛЕ 25; `-k import_pipeline` — ДО 35, ПОСЛЕ 35; `-k contracts` — ДО 76, ПОСЛЕ 76; `-k tenders` — ДО 116, ПОСЛЕ 116.

### Task 9: точки инварианта — операции контекстов, Review, пересчёт роли; архитектурный и структурный тесты

**Files**
- Edit: `backend/services/context_operations.py`, `backend/services/work_families.py`, `backend/services/review.py`, `backend/services/catalog_backfill.py` (стадия `_route_batch`, сигнатура `run_backfill`)
- Не меняется: `backend/cli.py` (`backfill-contexts` зовёт `run_backfill(db)` — значение по умолчанию сохраняет вызов)
- Test: `backend/tests/integration/test_semantic_queue_hooks_ops.py`, `backend/tests/unit/test_semantic_queue_architecture.py`

**Interfaces**
- Потребляет: `split_context`, `merge_contexts`, `move_members`, `archive_context`, `accept_target_decision`, `accept_transfer`, `transfer_stale_group`, `confirm_kind`, `unconfirm_kind`, `merge_into_position`, `set_kind`, `run_backfill`, `_route_batch` (существуют); `set_name_role`, `_recompute_stale_name_roles`, `refresh_membership_states` — только в отрицательных утверждениях (существуют); `reconcile_semantic_jobs`, `event_cap_from` (Task 6, 8).
- Производит:

```python
RECONCILE_ALLOWLIST: frozenset[str]   # модули, которым разрешена запись в защищённые поля (semantic_reconcile)

def run_backfill(
    db: Session, *, batch_size: int = 500, progress: Callable[[int], None] | None = None,
    event_cap: EventCap | None = None,
) -> BackfillReport
    # event_cap=None → event_cap_from(config.settings), читается в момент вызова;
    # CLI и 14 существующих вызовов в тестах не меняются
```

**Утверждения**
- для каждой операции перечня спеки §2.7 этой задачи: после неё задания
  затронутых контекстов соответствуют предикату и текущему отпечатку; каждый
  тест проверен снятием вызова сверки;
- `refresh_membership_states` вход не меняет (решение спеки 5): после перевода
  членства в `STALE` отпечаток контекста тот же, заданий не прибавилось;
- **`run_backfill`, стадия `_route_batch`**: после каждой партии и до её
  `commit` — `reconcile_semantic_jobs` затронутых контекстов с потолком события
  (переданным `event_cap`, по умолчанию — из настроек: отдельный вход с малым
  переданным потолком даёт пачку `held`, вызов без параметра — потолок настроек);
  применимый контекст, созданный маршрутизацией партии, имеет `pending` (или пачку
  `held` сверх потолка); тест проверен снятием вызова;
- `set_name_role` и стадия `_recompute_stale_name_roles` вход и применимость не
  меняют (спека §2.7, круги 1–2 гейта 3): отпечаток контекста тот же, заданий не
  прибавилось — отдельный вход, где меняется только роль;
- правки семей (`create_family`, `update_family`, `activate_family`,
  `archive_family`, `merge_families`) заданий **не ставят** (решение спеки 2);
- архитектурный тест: запись в `context_members`, в поля `catalog_contexts`
  `semantic_kind`, `semantic_state`, `work_family_id`, `archived_at`
  и удаление `position_items` встречаются только в модулях `RECONCILE_ALLOWLIST`
  (AST-поиск присваиваний атрибутов и `delete`/`update` по этим моделям);
  тест проверен вставкой записи в модуль вне списка;
- структурный тест: у каждого модуля `RECONCILE_ALLOWLIST` есть тест в
  `test_semantic_queue_hooks_*.py` (по имени модуля в строке теста); тест проверен
  добавлением модуля в список без теста.

**Имена**
- Заводятся этой задачей: `RECONCILE_ALLOWLIST`.
- Существуют, проверено `grep`-ом: все функции блока «Потребляет» (`_route_batch`, `_recompute_stale_name_roles` — `services/catalog_backfill.py`).

**Проверка**
- `just test-int-local-k semantic_queue` — ДО ≥ 72, ПОСЛЕ ≥ 86.
- `just test-unit-k semantic_queue` — ДО ≥ 32, ПОСЛЕ ≥ 34.
- `just test-int-local-k semantic_api` — ДО 186, ПОСЛЕ 186; `-k work_families` — ДО 135, ПОСЛЕ 135.
- `just test-int-local-k catalog_backfill` — ДО 17, ПОСЛЕ ≥ 17.

### Task 10: исполнитель — захват, запись, повторы, предохранитель

**Files**
- Create: `backend/services/semantic_worker.py`, `backend/services/semantic_client.py`
- Test: `backend/tests/integration/test_semantic_queue_worker.py`

**Interfaces**
- Потребляет: Task 1–6.
- Производит:

```python
class ModelClient(Protocol):
    def complete(self, body: dict, *, timeout_s: float) -> ModelResponse: ...

@dataclass(frozen=True)
class ModelResponse:
    content: str
    actual_model: str
    provider: str | None
    prompt_tokens: int
    completion_tokens: int
    cache_write_tokens: int
    cached_tokens: int
    cost_usd: Decimal | None

class TransientModelError(Exception):   # (message, *, error_class: str | None = None)
    ...
class PermanentModelError(Exception):   # (message, *, error_class: str | None = None)
    ...

class OpenRouterClient:                 # ModelClient поверх httpx.Client
    def __init__(
        self, *, api_key: str, base_url: str = ..., http_client: httpx.Client | None = None
    ) -> None: ...
    def complete(self, body: dict, *, timeout_s: float) -> ModelResponse: ...
    def close(self) -> None: ...

@dataclass(frozen=True)
class Claim:
    job_id: int
    attempt_id: int
    claim_token: UUID
    rendered: RenderedRequest
    candidates: tuple[CandidateFamily, ...]

def claim_next(db: Session, *, settings: Settings, now: datetime) -> Claim | None
def record_result(
    db: Session, claim: Claim, response: ModelResponse, *, now: datetime, settings: Settings
) -> None
    # разбирает ответ сам (parse_model_answer); AnswerSchemaError пишет попытку schema_error
    # с raw_response, validation_error, моделью, провайдером, токенами и стоимостью
def record_failure(
    db: Session, claim: Claim, error: TransientModelError | PermanentModelError,
    *, now: datetime, settings: Settings, rng: Callable[[], float] = random.random,
) -> None
    # только транспортные ошибки: ответа нет, стоимость — резерв
def process_one(session_factory, client: ModelClient, *, settings: Settings, clock) -> bool
```

**Утверждения**
- `claim_next`, ветви по порядку спеки §2.5 — каждая отдельным входом: остановка
  → `None`, задание не тронуто; неприменимый → `cancelled`/`not_applicable` и
  взято следующее; отпечаток не совпал → `cancelled`/`input_changed`, нового
  задания нет; совпадение проверки → `privacy_hold` с набором; совпадение, равное
  `privacy_released_matches`, → захват; бюджет превышен → задание `pending`,
  попытки нет, `attempts_in_generation` не изменился, `None`; иначе — `running`,
  попытка с `reserve_usd`, `prefix_hash`, `privacy_dictionary_hash`;
- порядок захвата — `unit_id`, `next_attempt_at`, `id`;
- **гонка бюджета**: четыре параллельных `claim_next` в разных сессиях при остатке
  бюджета на одну попытку — ровно одна попытка; тест проверен снятием
  `FOR UPDATE` строки `semantic_worker_state`;
- `record_result`: валидный ответ на текущий отпечаток — предложение
  опубликовано, прежнее снято, `done`, `result_suggestion_id`, факт заменил
  резерв; ответ на устаревший отпечаток — `stale_fingerprint`, ничего не
  вытеснено; неприменимый — `context_not_applicable`; перехваченный
  (`claim_token` сменён) — `lost_claim`, `done` не ставится;
- факт больше резерва — `reserve_exceeded`, `claim_paused = true` с причиной,
  попыткой, временем; следующий `claim_next` — `None`;
- `record_result` с ответом, не прошедшим строгий разбор или схему: попытка
  `schema_error` несёт `raw_response`, `validation_error` = `AnswerSchemaError.detail`,
  `actual_model`, `provider`, токены и `cost_usd` из ответа; задание `error` без
  повтора в поколении; предложение не создаётся;
- `record_failure`: временная — `pending`, `next_attempt_at` растёт от попытки к
  попытке, после `SEMANTIC_MAX_ATTEMPTS` — `error`; постоянная — `error` сразу;
  попытка без стоимости остаётся с резервом;
- исключение в одном задании не останавливает `process_one` для следующего;
- `OpenRouterClient` превращает `429`/`5xx`/таймаут в `TransientModelError`,
  прочие `4xx` — в `PermanentModelError` (тест на `httpx.MockTransport`, без сети);
  пустое `content` — `TransientModelError`.

**Имена**
- Заводятся этой задачей: имена блока «Производит».
- Существуют: имена Task 1–6.

**Проверка**
- `just test-int-local-k semantic_queue` — ДО ≥ 86, ПОСЛЕ ≥ 108.

### Task 11: поток-опросчик, восстановление, `lifespan`

**Files**
- Create: `backend/services/semantic_runner.py`
- Edit: `backend/services/maintenance.py`, `backend/main.py`
- Test: `backend/tests/integration/test_semantic_queue_runner.py`

**Interfaces**
- Потребляет: `process_one`, `ModelClient`, `OpenRouterClient` (Task 10); `run_startup_maintenance`, `recover_interrupted_jobs`, `lifespan` (существуют).
- Производит:

```python
class SemanticRunner:
    def __init__(self, session_factory, client: ModelClient, *, settings: Settings, clock=...) -> None: ...
    def start(self) -> None: ...
    def stop(self, *, timeout_s: float) -> None: ...

def recover_semantic_jobs(db: Session, *, now: datetime) -> int
```

**Утверждения**
- `recover_semantic_jobs`: все `running` → `pending`, `claim_token` снят, их
  открытые попытки закрыты `transient_error` классом `interrupted`; возвращает их
  число; вызывается внутри `run_startup_maintenance` той же транзакцией, что
  `recover_interrupted_jobs` (ошибка роняет старт — тест);
- `SemanticRunner` обрабатывает поставленные задания до пустой очереди,
  параллельно не больше `SEMANTIC_CONCURRENCY` вызовов (фейковый клиент считает
  одновременные);
- `stop` перестаёт брать новые задания и ждёт текущие не дольше таймаута;
- при `RUN_SEMANTIC_WORKER = false` `lifespan` поток не запускает; при `true` —
  запускает и останавливает на выходе (тест с внедрённым клиентом);
- существующие тесты `-k maintenance` зелёные.

**Имена**
- Заводятся этой задачей: `SemanticRunner`, `recover_semantic_jobs`.
- Существуют, проверено `grep`-ом: `run_startup_maintenance`, `recover_interrupted_jobs`, `lifespan`.

**Проверка**
- `just test-int-local-k semantic_queue` — ДО ≥ 108, ПОСЛЕ ≥ 115.
- `just test-int-local-k maintenance` — ДО 18, ПОСЛЕ 18.

### Task 12: решения `admin` — предложения, задержанные, preview, пачки, остановка

**Files**
- Create: `backend/services/semantic_decisions.py`
- Edit: `backend/cli.py`
- Test: `backend/tests/integration/test_semantic_queue_decisions.py`

**Interfaces**
- Потребляет: `assign_family` (Task 7), `create_family`, `activate_family` (существуют), `reconcile_semantic_jobs`, `NO_CAP` (Task 6), `find_privacy_matches`, `build_privacy_dictionary` (Task 4), `reserve_for`, `expected_cached_cost`, `tariffs_from`, `RESERVE_FORMULA_VERSION` (Task 5).
- Производит:

```python
class DecisionConflict(Exception):
    code: str       # suggestion_changed | job_changed | preview_changed | batch_decided | family_exists

@dataclass(frozen=True)
class Preview:
    context_count: int
    reserve_usd: Decimal
    expected_cached_usd: Decimal
    preview_hash: str

@dataclass(frozen=True)
class ConfirmReport:
    confirmed: list[int]
    skipped: list[int]

def confirm_suggestions(db: Session, *, suggestion_ids: list[int], actor_id: int) -> ConfirmReport
def reject_suggestion(db: Session, *, suggestion_id: int, actor_id: int) -> None
def assign_other_family(db: Session, *, suggestion_id: int, family_id: int, actor_id: int) -> None
def create_family_from_suggestion(db: Session, *, suggestion_id: int, title: str, definition: str, actor_id: int) -> int
def release_privacy_hold(db: Session, *, job_id: int, shown_matches: list[dict], actor_id: int) -> None
def decline_privacy_hold(db: Session, *, job_id: int, shown_matches: list[dict], actor_id: int) -> None
def release_unit_privacy_holds(db: Session, *, unit_id: int | None, shown_matches: list[dict], actor_id: int) -> ConfirmReport
def retry_job(db: Session, *, job_id: int, actor_id: int) -> None
def preview_unit_reask(db: Session, *, unit_id: int | None) -> Preview
def confirm_unit_reask(db: Session, *, unit_id: int | None, preview_hash: str, actor_id: int) -> ReconcileReport
def preview_config_reask(db: Session) -> Preview
def confirm_config_reask(db: Session, *, preview_hash: str, actor_id: int) -> ReconcileReport
def preview_batch(db: Session, *, batch_id: int) -> Preview
def approve_batch(db: Session, *, batch_id: int, preview_hash: str, actor_id: int) -> ReconcileReport
def discard_batch(db: Session, *, batch_id: int, actor_id: int) -> None
def resume_worker(db: Session, *, actor_id: int) -> None
def enqueue_all(db: Session) -> int | None   # id удержанной пачки; None — ставить нечего
```

**Утверждения**
- `confirm_suggestions`: назначение с `source = suggestion`, `decision =
  accepted`; предложение, не прошедшее перепроверку (неопубликовано, решено, не
  текущий отпечаток), — в `skipped`, остальные назначены; группа — одна транзакция;
- `assign_other_family` — `source = manual`, `other_family`;
  `reject_suggestion` — `rejected`, `is_published = false`, `rejected`, после
  сверки не воскресает (вход: повторная сверка того же контекста);
- `create_family_from_suggestion`: одна транзакция — семья `active` с
  определением, назначена с `source = suggestion`, предложение `family_created`;
  пустое определение — отказ до записи; дубль «имя + единица» среди активных —
  `family_exists`, ничего не записано;
- решения по задержанному: каждое из четырёх нарушений перепроверки (не
  `privacy_hold`; неприменим; отпечаток не текущий; набор совпадений не равен
  показанному) — `job_changed` отдельным входом; «Отправить» — `pending` с
  `privacy_released_matches`; «Не отправлять» — `cancelled`/`privacy_declined`;
- `preview_*`: `preview_hash` меняется при смене набора **и** при смене суммы
  резерва при том же наборе (новое наблюдение токенов префикса между preview и
  подтверждением) — `preview_changed` отдельными входами;
- `confirm_unit_reask`, `confirm_config_reask`, `approve_batch` ставят задания
  **без потолка события** (набор сверх потолка — задания созданы, пачки нет);
  суточный бюджет при этом не обходится (захват после — упирается в бюджет);
- `approve_batch`/`discard_batch` условно по `held`: второе решение —
  `batch_decided`, двойной постановки нет; `held_fingerprints` не меняется;
- `retry_job`: только из `error` (иначе `job_changed`); **то же** задание (тот же
  `id`) → `pending`, `retry_generation += 1`, `attempts_in_generation = 0`,
  `next_attempt_at = now`, `last_error_class` снят; все попытки прежних поколений
  остаются в `semantic_job_attempts` без изменений; `done` с тем же отпечатком —
  отказ `job_changed` (продуктовая политика спеки §2.6);
- `enqueue_all` всегда создаёт пачку `mass`, даже под потолком; CLI
  `semantic-enqueue-all` печатает её номер, число и резерв;
- `resume_worker` очищает причину, попытку и время остановки, пишет
  `last_resumed_by/at`;
- `unit_id = None` адресует контексты без единицы и только их.

**Имена**
- Заводятся этой задачей: имена блока «Производит», команда `semantic-enqueue-all`.
- Существуют, проверено `grep`-ом: `create_family`, `activate_family`, `_guard` (`cli.py`).

**Проверка**
- `just test-int-local-k semantic_queue` — ДО ≥ 115, ПОСЛЕ ≥ 145.

### Task 13: API и чтение для экрана

**Files**
- Create: `backend/crud/semantic_queue.py`
- Edit: `backend/routers/semantic.py`
- Test: `backend/tests/integration/test_semantic_queue_api.py`

**Interfaces**
- Потребляет: Task 12, `require_admin` (существует).
- Производит: маршруты спеки §2.13 и формы ответов:

```python
class SuggestionRow(TypedDict):
    suggestion_id: int
    context_id: int
    title: str
    unit_code: str | None
    article: str | None
    path: list[str]
    confidence: str            # Decimal строкой
    reason: str
    multi_owner: bool
    previously_rejected: RejectedMark | None

class RejectedMark(TypedDict):
    family_id: int
    family_title: str
    decided_at: str

class SuggestionGroup(TypedDict):
    family_id: int
    family_title: str
    unit_code: str | None
    band: Literal["high", "mid", "low"]
    rows: list[SuggestionRow]
    total: int

class PausedInfo(TypedDict):
    reason: str
    attempt_id: int
    paused_at: str

class HeldBatchInfo(TypedDict):
    batch_id: int
    source: str
    import_job_id: int | None
    unit_id: int | None
    contexts_count: int
    reserve_usd: str
    expected_cached_usd: str
    created_at: str

class StaleUnitInfo(TypedDict):
    unit_id: int | None
    unit_code: str | None
    stale_count: int            # применимые контексты без задания с текущим candidates_hash

class ConfigStaleInfo(TypedDict):
    stale_count: int
    prompt_version_current: int

class QueueStatus(TypedDict):
    spent_24h_usd: str
    daily_budget_usd: str
    claim_paused: PausedInfo | None
    held_batches: list[HeldBatchInfo]
    stale_units: list[StaleUnitInfo]
    config_stale: ConfigStaleInfo | None
```

**Утверждения**
- все маршруты — `403` для `member`, `401` без входа;
- `GET /suggestions?queue=list` группирует по предложенной семье, фильтры
  единицы, полосы (≥ 0,9 / 0,7–0,9 / < 0,7 — границы 0,9 и 0,7 входят в верхнюю
  полосу), «только многовладельческие» (≥ 2 договоров или тендеров среди членств)
  — каждый отдельным входом; неопубликованные и решённые не показываются;
- `previously_rejected` заполнен ровно тогда, когда у контекста есть
  отклонённое предложение той же семьи;
- `queue=new` показывает «новая» и «СИСТЕМА»; контексты единицы без активных
  семей — строками без предложения;
- `GET /jobs?status=error|privacy_hold`, `GET /status` — формы выше; деньги —
  строками;
- `unit_id` в теле: `null` — без единицы; отсутствие поля — `422`;
- `DecisionConflict` → `409` с кодом; число запросов `GET /suggestions` не растёт с
  числом строк (20 и 120 строк — одинаково).

**Имена**
- Заводятся этой задачей: `crud/semantic_queue.py`, `SuggestionRow`, `RejectedMark`, `SuggestionGroup`, `PausedInfo`, `HeldBatchInfo`, `StaleUnitInfo`, `ConfigStaleInfo`, `QueueStatus`, маршруты §2.13.
- Существуют, проверено `grep`-ом: `require_admin`, `router` (`routers/semantic.py`).

**Проверка**
- `just test-int-local-k semantic_queue` — ДО ≥ 145, ПОСЛЕ ≥ 170.
- `just test-int-local-k semantic_api` — ДО 186, ПОСЛЕ 186.

### Task 14: фронтенд — вкладка, шапка, очередь «Семья из списка»

**Files**
- Create: `frontend/src/pages/families/SuggestionsTab.tsx`, `SuggestionsHeader.tsx`, `SuggestionGroups.tsx`, `PreviewDialog.tsx`, их `*.test.tsx`
- Edit: `frontend/src/pages/families/FamiliesPage.tsx`, `labels.ts`, `frontend/src/services/api/domain.ts`, `services/queries.ts`, `types/domain.ts`, `test/handlers.ts`

**Interfaces**
- Потребляет: маршруты Task 13; `Tabs`, `Dialog`, `Checkbox`, `Badge`, `Button` из `@/components/ui` (существуют или `npx shadcn add`).
- Производит: компоненты `SuggestionsTab`, `SuggestionsHeader`, `SuggestionGroups`, `PreviewDialog`; хуки `useSuggestions`, `useQueueStatus`, мутации подтверждения, отклонения, другой семьи, preview/подтверждения перезапроса, пачки, снятия остановки.

**Утверждения**
- третья вкладка «Предложения» на `/families`; две прежние не изменились
  (существующие тесты `src/pages/families` зелёные);
- шапка: расход и бюджет; плашка остановки с кнопкой снятия — только при
  `claim_paused`; удержанные пачки с резервом и ожидаемой ценой; «Конфигурация
  изменена»; «Список семей единицы изменён» — каждая при своём условии и только при
  нём;
- группа: все строки отмечены, снятие отметки уменьшает число в «Подтвердить
  отмеченные N»; подтверждение шлёт ровно отмеченные; пропущенные сервером
  показаны сообщением;
- у строки — «Пояснение ИИ: …», «Другая семья…», «Отклонить»; пометка «ранее
  отклонено» — при `previously_rejected`;
- `PreviewDialog`: показывает число, резерв, ожидаемую цену; подтверждение шлёт
  `preview_hash`; ответ `409 preview_changed` — сообщение «оценка изменилась» и
  повторный запрос preview, а не молчаливое повторение;
- деньги выводятся из строк без `parseFloat` (тест на значении, которое `float`
  округляет).

**Имена**
- Заводятся этой задачей: компоненты и хуки выше.
- Существуют, проверено `grep`-ом: `FamiliesPage`, `labels.ts`, `domain.ts`, `queries.ts`, `handlers.ts`.

**Проверка**
- `just test-frontend-file src/pages/families` — ДО 254, ПОСЛЕ ≥ 280.

### Task 15: фронтенд — очереди «Новая» и «Ошибки», «Завести семью…», задержанные

**Files**
- Create: `frontend/src/pages/families/NewQueue.tsx`, `ErrorsQueue.tsx`, `CreateFamilyDialog.tsx`, их `*.test.tsx`
- Edit: `SuggestionsTab.tsx`, `labels.ts`, `services/api/domain.ts`, `services/queries.ts`, `types/domain.ts`, `test/handlers.ts`

**Interfaces**
- Потребляет: маршруты Task 13; компоненты Task 14.
- Производит: `NewQueue`, `ErrorsQueue`, `CreateFamilyDialog`; мутации создания семьи, повтора, решений по задержанным.

**Утверждения**
- «Новая»: имя от ИИ; «ИИ считает строку системой» для `СИСТЕМА`; строки без
  активных семей — «в единице нет активных семей — ИИ не спрашивали»;
- `CreateFamilyDialog`: имя подставлено и редактируемо, единица не редактируется,
  без определения кнопка неактивна; `409 family_exists` — сообщение со ссылкой на
  семью; успех — строка уходит из очереди, появляется пометка «список семей
  изменён»;
- «Ошибки»: «Повторить»; блок «Задержано проверкой»: совпавшие слова подсвечены,
  место совпадения подписано, «Отправить» / «Не отправлять» шлют показанный набор;
  `409 job_changed` — «задание изменилось, обновите экран»; совпадение в списке
  семей — одна строка на единицу с «Отправить все K».

**Имена**
- Заводятся этой задачей: компоненты и мутации выше.
- Существуют: компоненты Task 14.

**Проверка**
- `just test-frontend-file src/pages/families` — ДО ≥ 280, ПОСЛЕ ≥ 300.
- `just ci-frontend` — зелёная (eslint, tsc, vitest).

### Task 16: ревизия `AGENTS.md` §3 и §5, справочник, дорожная карта

**Files**
- Edit: `AGENTS.md`, `docs/AGENTS-revisions.md`, `docs/reference/screens.md`, `docs/product-roadmap.md`

**Interfaces**
- Потребляет: `check_agents_index.py` (существует).
- Производит: действующая ревизия в преамбуле; прежняя — в архиве.

**Утверждения**
- §3: «Без брокеров» называет поток-опросчик семантических заданий и
  распространяет на него условие одного worker; роль `admin` дополнена; новый пункт
  «Что уходит наружу» — дословно по спеке §2.16;
- §5: сверка в сессии B после членств, пачка вместо ошибки сверх потолка; recovery
  возвращает `running` семантических заданий; пять счётчиков не расширены;
- `screens.md` `## 9.` описывает вкладку «Предложения»; заголовок и якорь прежние;
- дорожная карта: пункт «семантические предложения» — сделан, со ссылкой на спеку;
  порог и цена — заполняются в Task 17;
- `just check-agents-index` — 18 из 18.

**Имена**
- Существуют, проверено `grep`-ом: `check_agents_index.py`, `EXPECTED_SCREEN_ANCHORS`.

**Проверка**
- `just check-agents-index` — 18 из 18; `-k check_agents_index` — ДО 21, ПОСЛЕ 21.

### Task 17: стенд, замеры плана, порог, devlog, PR

**Files**
- Create: `docs/devlog/2026-09-28-semantic-suggestions.md`
- Edit: `docs/product-roadmap.md`

**Interfaces**
- Потребляет: всё выше; стенд `gca_dev`; книга открытия, утверждённая пользователем
  и применённая разовым скриптом в `tasks/` (предусловие, спека §2.15).

**Утверждения**
- миграция `0018` на стенде применена;
- **замер 12 спеки**: сверка на крупнейшей смете стенда функцией
  `reconcile_semantic_jobs` — время против импорта в devlog; больше 10 % —
  задача возвращается к outbox до PR;
- **замер 13 спеки**: доля отказов строгого разбора на ≥ 200 вызовах финальным
  запросом — в devlog;
- массовая постановка `semantic-enqueue-all` — пачка `mass`, поставлена с экрана;
  прогон всех применимых завершён; в devlog — стоимость, доля чтения кэша, число
  срабатываний предохранителя, распределение по полосам;
- книга слепой разметки ≈ 300 контекстов из не вошедших в выборку открытия
  размечена пользователем; метрики спеки §2.15 посчитаны; порог выбран
  пользователем и записан в devlog и дорожную карту;
- devlog называет тронутые области и прочитанные файлы граблей, отступления от
  плана, стоимость ревью;
- `just ci` зелёный перед пушем; PR — после решения пользователя.

**Имена**
- Заводятся этой задачей: `docs/devlog/2026-09-28-semantic-suggestions.md`.
- Существуют: `reconcile_semantic_jobs` (Task 6), `semantic-enqueue-all` (Task 12), `docs/product-roadmap.md`.

**Проверка**
- `just ci` — зелёная; `just check-agents-index` — 18 из 18.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: `just ci` (§9.3); на этой машине бэкенд — `just
  test-backend-parallel 4` (долг 1, `-n 8` плавает), фронтенд — `just
  ci-frontend`.
- Экран: прогон на стенде в браузере (Task 17) — раскладка jsdom не наблюдаема.
