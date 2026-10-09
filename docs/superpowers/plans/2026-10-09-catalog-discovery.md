# План: фича 3б «Открытие семей и системы»

**Спека:** `docs/superpowers/specs/2026-10-09-catalog-discovery-design.md` (гейт 2 одобрен 10.10.2026, три круга Codex, редакция 4 `5826f49`)
**Ветка:** `feat/catalog-discovery`, черновой PR #68
**Редакция 2, 10.10.2026** — 17 задач сведены в 7 по решению пользователя
(решение плана 6): содержание утверждений не менялось, задачи 1–17 редакции 1
стали частями: 1–2 → Task 1, 3–5 → Task 2, 7–10 → Task 3, 11, 12, 6 → Task 4,
14 → Task 5, 15 → Task 6, 16–17 → Task 7; отдельной задачи API нет — маршруты
в задачах своих команд (решение плана 3).
**Редакция 3, 10.10.2026** — ревью гейта 3 на `9f64a2b` (структура из семи
задач принята): за Task 4 явно закреплены потребители снимка DoD 7 после
`commit` обработки и гонка «обработка ответа ↔ активация» (часть Г).
**Редакция 4, 10.10.2026** — повторное ревью гейта 3 на `398d080`: исход
гонки «активация раньше» исправлен — активация меняет вход, обработка
уходит в `stale_fingerprint`, а не вытесняет черновики.
**Макет:** [`docs/superpowers/design/2026-10-09-catalog-discovery/mockup.html`](../design/2026-10-09-catalog-discovery/mockup.html) — экраны 1–3, 3б, 4, К1–К3; фронт (Task 5, 6) и сверка на стенде (Task 7) идут по нему.

> Исполнителю: задачи идут снизу вверх и по порядку; каждая — цикл TDD
> (`superpowers:test-driven-development`) и ревью задачи
> (`docs/process/implementation.md`) до следующей. Адреса `§N` без уточнения —
> разделы спеки; «решение N» — пронумерованные решения спеки в её начале;
> «DoD N» — пункт §5 спеки. Числа проверок — накопительные нижние границы:
> точное ПОСЛЕ неизвестно до написания тестов. ДО сняты 10.10.2026 на `5826f49`
> командой `uv run pytest <каталог> --collect-only -q -k <шаблон>` (бэкенд) и
> `npx vitest list <каталог>` (фронт).

## Global Constraints

- **Права**: все новые маршруты и изменённые маршруты контура — только
  `admin` (`Depends(require_admin)`), `member` получает `403` (`AGENTS.md` §3,
  спека §2.12). Сторож `tests/test_auth_coverage.py` зелёный после каждой
  задачи с маршрутами.
- **Общий порядок блокировок фичи** (решение 20): категории → черновики и
  предложения категорий → строка каталога → семья → вариант → контекст →
  задание. Ссылающийся на категорию берёт её `FOR SHARE` раньше своих строк;
  повышение режима замка внутри транзакции запрещено; замок строки — с
  `populate_existing` (или `expire_all()` после замка, как в
  `services/work_families.py`).
- **Тело запроса предложения семьи для `semantic_kind <> 'SYSTEM'` не
  меняется ни байтом** (решение 1, DoD 5); тела `family_schema` и
  `context_values` не меняются ни байтом — категории в них не входят (§2.9).
- **Наружу уходит только** то, что перечислено в §2.2 дизайна и §2.3 спеки:
  наименования, статьи, пути, единица, активные семьи (имена, определения),
  справочник категорий (имена, определения). Цены, объёмы, подрядчики,
  договоры и объекты — никогда (`AGENTS.md` §3). Тело открытия проходит
  `find_privacy_matches` целиком.
- **Перечисления и CHECK** — литералами в миграции `0021`, паритет с
  `models.py` держит тест; уникальные индексы с выражениями — сырым SQL в
  `alembic/env.py` `RAW_SQL_INDEXES` (`AGENTS.md` §4, `docs/pitfalls/db.md`).
- **Тотальные предикаты**: каждая равносильность и каждая ветвь CHECK
  записываются так, чтобы ни один атом не давал `NULL`
  (`docs/insights/state-the-rule-as-an-equivalence.md`, круг 1 гейта 2).
- **Ключи существующих ответов API** не удаляются и не переименовываются —
  только добавляются (`threshold` в ответе автопринятия остаётся).
- **Каждый новый код отказа** — в карте статусов `routers/semantic.py`
  (`_STATUS_NOT_FOUND`/`_STATUS_CONFLICT`/`_STATUS_UNPROCESSABLE`, строки
  98–174; незнакомый код — `AssertionError`) и в подписях
  `frontend/src/pages/families/labels.ts`.
- **Фронтенд** — только shadcn/ui; все нужные примитивы уже в
  `frontend/src/components/ui/` (checkbox, collapsible, dialog, select, table,
  alert-dialog, badge, tooltip).
- **Номер ревизии `AGENTS.md`** называется только в коммите ревизии (Task 7):
  страж (проверка 5) краснеет на необъявленную версию.
- **Стенд**: записи в учётки `gca_dev` готовит скрипт, запускает пользователь
  (классификатор разрешений не пускает сессию); опросчик на стенде включает
  пользователь (Task 7).

## Review Focus

Входы, которые спека подразумевает, а задачи легко пропустить; тест на каждый
стоит в задаче-владельце:

1. **Единица «без единицы»** (`unit_id IS NULL`) — открытие, частичный UNIQUE
   живого открытия (`COALESCE(unit_id,-1)`), блок, preview и черновики
   работают с `NULL` как с обычной единицей (Task 1, 3, 4).
2. **Одно наименование в двух статьях** — одна строка тела, оба контекста —
   члены одной группы; «не работа» по строке группы получают оба (Task 3,
   4).
3. **Ответ модели «СИСТЕМА» на систему после снятия запрета** — разбирается
   как сегодня, предложение уходит в «Новую» и в охват следующего открытия
   (Task 2, 3).
4. **Член черновика ушёл из охвата до активации** — экран его не считает,
   «не работа» пропускает и называет, активация не падает (Task 4).
5. **Категория удалена, пока черновик с ней открыт** — черновик без
   категории, активация отказывает `draft_without_category`, правка на
   удалённую — `category_not_found` (Task 1, 4).
6. **Контекст `NOT_APPLICABLE` по строке `LOT_HEADER`** — «Вернуть в разбор»
   отказывает так же, как для `HEADER`/`TRASH` (Task 4).
7. **Вид контекста сменили между ответом модели и правилом публикации** —
   порог берётся по виду под блокировкой (Task 2).

## Структура файлов

```
backend/
  alembic/versions/2026_10_10_0021-catalog_discovery.py   создаётся: §2.2 целиком, три категории, downgrade с отказом
  alembic/env.py                    правка: RAW_SQL_INDEXES += uq_family_categories_title, uq_semantic_jobs_discovery_live
  models.py                         правка: FamilyCategory, FamilyDraft, FamilyDraftMember, FamilyCategoryProposal,
                                    WorkFamily.family_category_id, SemanticJobKind.family_discovery, CK_* , event type
  config.py, .env.example           правка: SEMANTIC_DISCOVERY_*, SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD
  services/family_categories.py     создаётся: справочник, блокировки категорий
  services/work_families.py         правка: категория в create/update/activate, отказы
  services/semantic_decisions.py    правка: create_family_from_suggestion(+категория); ветви вида discovery в задержанных и повторе
  services/semantic_request.py      правка: SYSTEM_SEMANTIC_PROMPT, выбор промпта по виду, is_applicable без SYSTEM, RequestHasher
  services/semantic_reconcile.py    правка: _values_applicable без SYSTEM; discovery вне _split_jobs/_open_suggestion_state
  services/work_variants.py         правка: _is_applicable без SYSTEM; reopen_context
  services/family_change.py         правка: Thresholds, threshold_for, правило и массовое автопринятие по виду
  services/semantic_cost.py         правка: tariffs_from(..., family_discovery)
  services/semantic_events.py       правка: context_reopened, family_created.origin += discovery
  services/variant_answer.py        правка: DISCOVERY_RESPONSE_FORMAT, parse_discovery_answer
  services/family_discovery.py      создаётся: охват, тело, preview и запуск открытия
  services/discovery_result.py      создаётся: apply_discovery — обработка ответа
  services/discovery_drafts.py      создаётся: действия над черновиками, activate_discovery
  services/semantic_worker.py       правка: ветви family_discovery в render_job_request, record_result, _note_closed
  crud/semantic_queue.py            правка: «голые» системы, system_count групп, строки открытия в list_jobs
  crud/semantic.py                  правка: категория в строке семьи и фильтр; reopenable и catalog_kind в карточке
  crud/discovery.py                 создаётся: блок «Открыть семьи», вид черновиков
  routers/semantic.py               правка: маршруты §2.12, коды отказов, create-family с категорией
  scripts/measure_system_threshold.py   создаётся: кривая порога систем (только чтение)
  tests/conftest.py                 правка: _DOMAIN_TABLES += 4 таблицы, пересев трёх категорий после очистки
  tests/unit/test_catalog_discovery_*.py, tests/integration/test_catalog_discovery_*.py   создаются
  tests/… (существующие, закреплявшие исключение SYSTEM, и 33 вызова activate_family)   правка
frontend/src/
  types/domain.ts, services/api/domain.ts, services/queries.ts, services/queryKeys.ts, test/handlers.ts   правка
  pages/families/labels.ts          правка: коды отказов, подписи категорий, «не работа» нейтрально
  pages/families/FamiliesTab.tsx    правка: колонка и фильтр «Категория», «Категории…», выбор на карточке
  pages/families/FamilyCategoriesDialog.tsx   создаётся: окно справочника (К2)
  pages/families/CreateFamilyDialog.tsx       правка: обязательная категория
  pages/families/ContextCard.tsx    правка: «Вернуть в разбор», подпись «размечено в Review»
  pages/families/SuggestionGroups.tsx         правка: метка «система» у группы и строки
  pages/families/ErrorsQueue.tsx, PrivacyHoldBlock.tsx   правка: строки открытия
  pages/families/NewQueue.tsx       правка: блок открытия над таблицей
  pages/families/discovery/DiscoveryBlock.tsx, DiscoveryLaunchDialog.tsx, DiscoveryDrafts.tsx,
    DraftCard.tsx, NotWorkGroup.tsx, CategoryProposals.tsx   создаются (экраны 1–3)
  *.test.tsx рядом с компонентами   создаются/правятся
docs/reference/schema.md            правка: блок «Открытие семей и категории» (Task 1)
docs/reference/screens.md           правка: `## 9.` — четыре абзаца §2.13 (Task 7)
AGENTS.md, docs/AGENTS-revisions.md правка: §3 три места, преамбула; архив действующей врезки (Task 7)
docs/product-roadmap.md             правка: А1 закрыт (Task 7)
docs/devlog/2026-10-09-catalog-discovery.md   создаётся (Task 7)
```

## Решения плана, которых нет в спеке

1. **Открытие разложено на три модуля по ответственности**:
   `services/family_discovery.py` — охват, тело, preview и запуск (то, что
   спека называет там); `services/discovery_result.py` — обработка ответа
   (`apply_discovery`, её порядок блокировок); `services/discovery_drafts.py`
   — действия над черновиками и активация. Чтение для экрана —
   `crud/discovery.py`, как `crud/semantic_queue.py` у очереди: пишущее и
   читающее разделены так же, как в контуре сегодня.
2. **Все новые тесты — в файлах `test_catalog_discovery_*.py`**: команда
   `-k catalog_discovery` выбирает их все (ДО — 0 и в `tests/unit`, и в
   `tests/integration`). Тесты, закреплявшие исключение `SYSTEM`, и вызовы
   `activate_family` правятся на месте — их выбор называет задача-владелец.
3. **Маршруты — в задаче своих команд**, а не отдельной задачей API: Task 1
   заводит маршруты справочника и правит существующие маршруты семей, Task 3 —
   маршруты блока, preview и запуска, Task 4 — черновиков, активации и
   возврата. Сторож прав и карта кодов проверяются в каждой из трёх задач;
   ревьюер видит команду и её маршрут одним диффом.
4. **`family_categories` входит в `_DOMAIN_TABLES`, три строки
   пересеваются той же транзакцией очистки** — тот же приём, что у
   `semantic_worker_state` (`tests/conftest.py:423`): тесты заводят свои
   категории, и без очистки они протекали бы между тестами; без пересева
   тесты активации теряли бы категорию «Работа». Тестовый помощник —
   фикстура `work_category_id` (id сида `work`).
5. **`apply_publication_rules` принимает `Thresholds(work, system)` вместо
   одного `threshold`** — `threshold_for` выбирает под блокировкой; ответ
   массового автопринятия сохраняет ключ `threshold` (порог работ) и получает
   `system_threshold`.
6. **Семь задач, а не по задаче на слой** (решение пользователя 10.10.2026:
   ревью после каждой задачи — главная цена реализации, граница задачи
   проходит там, где ревьюер может отклонить одно, приняв соседнее). Порядок:
   схема и категории (1) → системы, порог и кривая (2) → задание открытия (3)
   → черновики, активация и возврат (4) → фронт категорий, возврата и меток (5)
   → фронт открытия (6) → ревизия, стенд, devlog (7). Внутри задачи части
   (А, Б, В, Г) — порядок работы исполнителя, а не границы ревью. Системы и
   порог раньше открытия: охват открытия опирается на снятые исключения
   («СИСТЕМА», «голые» системы).
7. **Скрипт кривой тестируется импортом его функций** на тестовой базе
   (`backend/scripts/__init__.py` есть): сам скрипт приложение не
   импортирует (условие проверочного инструментария, `AGENTS.md` §9.1), а
   тест вправе импортировать скрипт.

## Задачи

### Task 1: схема `0021` и справочник категорий

**Files**
- Create: `backend/alembic/versions/2026_10_10_0021-catalog_discovery.py`, `backend/services/family_categories.py`
- Edit: `backend/models.py`, `backend/alembic/env.py`, `backend/tests/conftest.py`, `docs/reference/schema.md`,
  `backend/services/work_families.py`, `backend/services/semantic_decisions.py`, `backend/services/semantic_events.py`,
  `backend/crud/semantic.py`, `backend/routers/semantic.py`, тесты с `activate_family(` (14 файлов, 33 вызова)
- Test: `backend/tests/integration/test_catalog_discovery_schema.py`, `test_catalog_discovery_categories.py`

**Interfaces**

*Часть А — схема*

- Потребляет: `Base`, `WorkFamily`, `SemanticJob`, `SemanticJobKind`, `CatalogContext`, `UnitOfMeasure`, `User`, `SEMANTIC_EVENT_TYPES`, `CK_SEMANTIC_JOBS_CONTEXT_SUBJECT`, `CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND`, `_sql_str_list`, `RAW_SQL_INDEXES`, `_DOMAIN_TABLES` (существуют).
- Производит:

```python
class DraftGroup(str, enum.Enum): new = "new"; existing = "existing"; not_work = "not_work"
class DraftStatus(str, enum.Enum):
    open = "open"; activated = "activated"; merged = "merged"; discarded = "discarded"; superseded = "superseded"
class CategoryProposalStatus(str, enum.Enum): open = "open"; applied = "applied"; superseded = "superseded"
# SemanticJobKind.family_discovery = "family_discovery"

FAMILY_CATEGORY_SEED_KEYS: tuple[str, ...]   # ("work", "engineering_system", "costs_services")
CK_DRAFT_SHAPE: str                           # три полные ветви §2.2, с IS NOT NULL в ветви new
CK_SEMANTIC_JOBS_CONTEXT_SUBJECT: str         # переписан: (kind IN ('family_suggestion','context_values')) = (context_id IS NOT NULL)
CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND: str       # переписан: (kind IN ('family_schema','context_values')) = (schema_id IS NOT NULL)
CK_SEMANTIC_JOBS_DISCOVERY_SUBJECT: str       # kind <> 'family_discovery' OR (context_id IS NULL AND family_id IS NULL)

class FamilyCategory(Base): ...         # "family_categories", §2.2
class FamilyDraft(Base): ...            # "family_drafts", §2.2
class FamilyDraftMember(Base): ...      # "family_draft_members", §2.2
class FamilyCategoryProposal(Base): ... # "family_category_proposals", §2.2
# WorkFamily.family_category_id; SEMANTIC_EVENT_TYPES += "context_reopened"
```

*Часть Б — справочник и категория у семьи*

- Потребляет: `FamilyCategory`, `WorkFamily`, `WorkFamilyError`, `UNSET`, `_lock_families`, `record_event`, фикстура `work_category_id` (часть А; существуют).
- Производит:

```python
# services/family_categories.py
REFUSE_CATEGORY_NOT_FOUND = "category_not_found"
REFUSE_CATEGORY_IN_USE = "category_in_use"
REFUSE_CATEGORY_BLANK_TITLE = "category_blank_title"
REFUSE_CATEGORY_BLANK_DEFINITION = "category_blank_definition"
REFUSE_CATEGORY_DUPLICATE = "category_duplicate"

def lock_categories(db: Session, category_ids: list[int] | None, *, exclusive: bool) -> None  # None — все, по id
def create_category(db: Session, *, title: str, definition: str, actor_id: int) -> FamilyCategory
def update_category(db: Session, *, category_id: int, title: str | object = UNSET,
                    definition: str | object = UNSET, actor_id: int) -> FamilyCategory
def delete_category(db: Session, *, category_id: int, actor_id: int) -> None

# services/work_families.py
REFUSE_ACTIVATE_WITHOUT_CATEGORY = "activate_without_category"
REFUSE_CLEAR_CATEGORY_ACTIVE = "clear_category_active"
def create_family(db, *, title, unit_name, definition=None, actor_id,
                  family_category_id: int | None = None,
                  origin: Literal["operator", "discovery"] = "operator") -> WorkFamily
def update_family(db, *, family_id, title=UNSET, definition=UNSET, actor_id,
                  family_category_id: int | None | object = UNSET) -> WorkFamily
# services/semantic_decisions.py
def create_family_from_suggestion(db, *, suggestion_id, title, definition, family_category_id: int, actor_id) -> ...
# crud/semantic.py: строка семьи += family_category_id, family_category_title;
# list_families(..., family_category_id: int | Literal["none"] | None = None)
```

*Маршруты*

```text
GET    /api/v1/semantic/family-categories          (с числом семей)
POST   /api/v1/semantic/family-categories
PATCH  /api/v1/semantic/family-categories/{id}
DELETE /api/v1/semantic/family-categories/{id}
POST   /api/v1/semantic/families, PATCH /families/{id}, POST /families/{id}/activate   (+ family_category_id)
POST   /api/v1/semantic/suggestions/{id}/create-family                               (+ обязательное family_category_id)
GET    /api/v1/semantic/families?family_category_id=<id>|none
```

**Утверждения**

*Часть А — схема*

- `alembic upgrade head` и `downgrade -1` проходят на пустой базе; `upgrade`
  на базе с заданиями всех трёх видов не меняет ни одной строки
  `semantic_jobs` (сравнение до/после); после `upgrade` ровно три строки
  `family_categories` с ключами `FAMILY_CATEGORY_SEED_KEYS`, определения
  непусты, `created_by IS NULL`;
- каждое ограничение §2.2 пробито ровно одним нарушением на вход (DoD 1), имя
  ограничения — по `diag.constraint_name`: открытие с `context_id`; открытие
  с `family_id`; предложение без контекста; `context_values` без версии
  схемы; второе живое открытие единицы — отказ, в том числе для двух
  `NULL`-единиц; после `done` первого — второе проходит; каждая из трёх
  ветвей `CK_DRAFT_SHAPE` с одним лишним и одним недостающим полем; ветвь
  `new` отдельно: `title = NULL`, `definition = NULL`, пустое и пробельное
  значение каждого — отказ; член чужого открытия (составной FK); один
  контекст в двух группах открытия; слияние в черновик чужого открытия;
  решение (`activated`/`merged`/`discarded`) у группы `existing` и
  `not_work`; `merged` без цели и с двумя целями; слияние в себя; удаление
  категории с семьёй — `RESTRICT`; пустые имя и определение категории; дубль
  имени категории без учёта регистра и крайних пробелов; категория без
  `seed_key` и без автора;
- снятие `title IS NOT NULL` из `CK_DRAFT_SHAPE` делает вход `title = NULL`
  красным (`docs/insights/verifying-guards.md`);
- выражения `CK_DRAFT_SHAPE`, трёх CHECK заданий и списков перечислений в
  миграции равны модели — против независимого литерала в тесте;
- downgrade при каждом непустом носителе (`family_drafts`,
  `family_category_proposals`, задания `family_discovery`, семьи с
  `family_category_id`, события `context_reopened`, категория без `seed_key`)
  поднимает `RuntimeError` с диагностикой — по входу на носитель; на пустых
  проходит и возвращает прежние выражения CHECK заданий и список типов
  событий;
- четыре новые таблицы в `_DOMAIN_TABLES`; после очистки доменных таблиц три
  категории на месте с прежними `seed_key`; фикстура `work_category_id`
  возвращает id сида `work`;
- блок «Открытие семей и категории» в `docs/reference/schema.md` в форме
  соседних блоков; `work_families` — колонка категории.

*Часть Б — справочник и категория у семьи*

- `create_category` и `update_category`: пустое и пробельное имя —
  `category_blank_title`, определение — `category_blank_definition`; дубль
  без учёта регистра и крайних пробелов — `category_duplicate` (и синхронно,
  и через ключ `uq_family_categories_title` в гонке — тем же кодом);
  несуществующая — `category_not_found`;
- `delete_category`: категория с семьёй — `category_in_use` с числом семей,
  ничего не удалено; без семей — черновики с ней получают
  `family_category_id = NULL`, предложения с ней удалены, категория удалена
  — порядок §2.9 (категория `FOR UPDATE` → черновики и предложения `FOR
  UPDATE` по `id` → `DELETE`);
- `lock_categories(None, exclusive=False)` берёт `FOR SHARE` всех категорий по
  `id`; гонка «`create_family` с категорией ↔ `delete_category`» — без
  deadlock, исход один из двух: семья создана и удаление отказало
  `category_in_use`, либо удаление прошло и создание отказало
  `category_not_found`; проверено снятием `FOR SHARE` в `create_family`
  (появляется семья со ссылкой, которую удаление не видело, — ключ
  `RESTRICT` даёт `IntegrityError` вместо отказа с кодом);
- `activate_family` семьи без категории — `activate_without_category`, с
  категорией — проходит; `update_family(family_category_id=None)` у активной
  — `clear_category_active`, у черновой — проходит; смена категории пишет
  `family_updated` с элементом `{field: "family_category_id", from, to}`, без
  смены — события нет;
- `create_family(origin="discovery")` пишет `family_created.origin =
  "discovery"`; прочие значения `origin` отвергает `record_event`;
- `create_family_from_suggestion` без категории — `422` на маршруте, с
  категорией — семья активна с ней; `GET /families?family_category_id=none` —
  только семьи без категории, `=<id>` — только с ней;
- 33 вызова `activate_family(` в 14 файлах получают категорию (фикстура
  `work_category_id`), их тесты зелёные без иных правок.

*Маршруты*

- каждый маршрут части — `admin`, `member` получает `403`; сторож прав зелёный;
- каждый новый код отказа части отвечает статусом таблицы §2.12 (`404`/`409`/`422`)
  с `{code, message}` — по входу на код; ни один код не падает в `AssertionError`
  карты `routers/semantic.py:98-174`;
- категории — с числом семей; удаление с семьями — `409 category_in_use`.

**Имена**

*Часть А — схема*

- Заводятся: три перечисления, `FAMILY_CATEGORY_SEED_KEYS`, `CK_DRAFT_SHAPE`, `CK_SEMANTIC_JOBS_DISCOVERY_SUBJECT`, четыре модели, `uq_family_categories_title`, `uq_semantic_jobs_discovery_live`, фикстура `work_category_id`, миграция `0021`.
- Существуют, проверено `grep`-ом: `SemanticJobKind` (`models.py:1584`), `CK_SEMANTIC_JOBS_CONTEXT_SUBJECT` (`:2075`), `CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND` (`:2076`), `SEMANTIC_EVENT_TYPES` (`:1460`), `RAW_SQL_INDEXES` (`alembic/env.py:60`), `_DOMAIN_TABLES` (`tests/conftest.py:423`), фикстура `factories` (`tests/conftest.py:605`).

*Часть Б — справочник и категория у семьи*

- Заводятся: модуль `services/family_categories.py` и пять кодов, `lock_categories`, `create_category`, `update_category`, `delete_category`, `REFUSE_ACTIVATE_WITHOUT_CATEGORY`, `REFUSE_CLEAR_CATEGORY_ACTIVE`, параметры `family_category_id`/`origin`.
- Существуют, проверено `grep`-ом: `create_family` (`services/work_families.py:249`), `update_family` (`:304`), `activate_family` (`:443`), `_lock_families` (`:637`), `UNSET` (`:110`), `create_family_from_suggestion` (`services/semantic_decisions.py:326`), `record_event` (`services/semantic_events.py:348`), `EVENT_ENUM_VALUES` (`:138`), `_domain_error` (`routers/semantic.py:202`).

**Проверка**
- `just test-int-local-k catalog_discovery` — ДО 0, ПОСЛЕ ≥ 80.
- `just test-int-local-k "work_variants_schema or semantic_schema or semantic_queue_schema"` — ДО 549, ПОСЛЕ ≥ 549.
- `just test-int-local-k work_families` — ДО 136, ПОСЛЕ ≥ 136.
- `just test-int-local-k "semantic_decisions or semantic_queue_api or semantic_api"` — ДО 382, ПОСЛЕ ≥ 382.
- `uv run pytest tests -k auth_coverage` — ДО 147, ПОСЛЕ ≥ 151.
- `just test-backend-local` — зелёный (33 вызова активации в разных файлах).
- `just check-agents-index` — 18 из 18.

### Task 2: системы на всём пути — свой промпт, исключения сняты, свой порог, кривая

**Files**
- Create: `backend/scripts/measure_system_threshold.py`
- Edit: `backend/services/semantic_request.py`, `backend/services/semantic_reconcile.py`, `backend/services/work_variants.py`,
  `backend/crud/semantic_queue.py`, `backend/config.py`, `backend/.env.example`, `backend/services/family_change.py`,
  `backend/services/semantic_worker.py`, `backend/routers/semantic.py` (существующие `/auto-accept/preview`, `/auto-accept`),
  `backend/cli.py` (`semantic-auto-accept`), тесты §1.3 спеки (`tests/unit/test_semantic_queue_request.py`,
  `tests/integration/test_work_variants_core.py`, `test_work_variants_reconcile.py`, `test_semantic_queue_hooks_ops.py`,
  `test_semantic_queue_material.py`, `test_context_membership.py`)
- Test: `backend/tests/integration/test_catalog_discovery_systems.py`, `test_catalog_discovery_threshold.py`,
  `test_catalog_discovery_curve.py`; `backend/tests/unit/test_catalog_discovery_prompt.py`

**Interfaces**

*Часть А — промпт и применимость*

- Потребляет: `is_applicable`, `_values_applicable`, `_is_applicable` (work_variants), `_build_body`, `render_context_request`, `RequestHasher`, `_UnitFingerprints`, `_new_queue`, `_list_queue`, `SuggestionGroup`, `SEMANTIC_PROMPT`, `PROMPT_VERSION` (существуют).
- Производит:

```python
SYSTEM_SEMANTIC_PROMPT: str
SYSTEM_PROMPT_VERSION: int = 1
def prompt_for(semantic_kind: str) -> tuple[str, str]   # (текст промпта, prompt_version задания: "1" | "system:1")
# SuggestionGroup.system_count: int; строка группы += semantic_kind: str
```

*Часть Б — свой порог*

- Потребляет: `apply_publication_rules`, `_apply_publication_rules`, `rule_outcome`, `_load_candidates`, `_preview_hash`, `AutoAcceptPreview`, `preview_auto_accept`, `apply_auto_accept` (существуют).
- Производит:

```python
# config.py
SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD: Decimal | None   # None по умолчанию, тот же валидатор пустой строки
# services/family_change.py
@dataclass(frozen=True)
class Thresholds:
    work: Decimal | None
    system: Decimal | None
def thresholds_from(settings: Settings) -> Thresholds
def threshold_for(context, thresholds: Thresholds) -> Decimal | None   # по context.semantic_kind
def apply_publication_rules(db: Session, *, suggestion_id: int, thresholds: Thresholds) -> FamilyChangeOutcome | None
# AutoAcceptPreview += system_threshold: Decimal | None (threshold остаётся — порог работ)
```

*Часть В — скрипт кривой*

- Потребляет: схема `family_suggestions`, `catalog_contexts`, `semantic_events` (существуют).
- Производит:

```python
MIN_LABELS: int = 300
MIN_HIGH_LABELS: int = 100
HIGH_CONFIDENCE: Decimal = Decimal("0.9")
THRESHOLDS: tuple[Decimal, ...]          # 0.80 … 0.99 шагом 0.01

@dataclass(frozen=True)
class Label:
    suggestion_id: int
    confidence: Decimal
    correct: bool

@dataclass(frozen=True)
class CurvePoint:
    threshold: Decimal
    pass_share: Decimal       # доля предложений систем с семьёй и уверенностью ≥ порога
    precision: Decimal | None # доля «права» среди меток ≥ порога; None — меток нет
    labels: int

def load_labels(conn) -> tuple[list[Label], int]           # (метки, исключено rejected отменённых ожиданий)
def load_confidences(conn) -> list[Decimal]                 # все предложения систем с family_id
def curve(labels: list[Label], confidences: list[Decimal]) -> list[CurvePoint]
def sufficient(labels: list[Label]) -> bool
def main(argv: list[str] | None = None) -> int              # DATABASE_URL, печать таблицы
```

**Утверждения**

*Часть А — промпт и применимость*

- `is_applicable`, `_values_applicable` и `work_variants._is_applicable` не
  читают `SYSTEM`: система при прочих равных применима ровно там, где
  применима работа (по входу на каждую функцию);
- сквозной вход: система в единице с активной семьёй получает задание
  `family_suggestion`, её ответ публикуется; подтверждение даёт семью;
  сверка ставит `context_values`; результат значений даёт вариант и
  промоушен строки `TO_REVIEW → POSITION` (DoD 4);
- тело для системы строит `SYSTEM_SEMANTIC_PROMPT`, для работы — прежний
  `SEMANTIC_PROMPT`; `prompt_version` задания — `system:1` и прежнее;
  **`request_hash` работы на фикстуре фичи 2 равен снимку до фичи** —
  литерал хэша в тесте (DoD 5); у системы и работы с одинаковыми полями
  `prefix_hash` различны;
- `SYSTEM_SEMANTIC_PROMPT` не содержит правила «СИСТЕМА» (нет подстроки
  `"СИСТЕМА"`); ответ модели `"СИСТЕМА"` на систему разбирается как
  сегодня (`is_system=True`) и попадает в «Новую»;
- `RequestHasher` и `_UnitFingerprints` считают префикс по виду контекста:
  после фичи опубликованные предложения работ остаются текущими в «Семье из
  списка» (вход: очередь до и после — тот же состав), а системы без заданий
  делают единицу «изменённой» в `_stale_scan`;
- «голая» система (единица без активных семей) — строка «Новой»;
- `SuggestionGroup.system_count` = числу строк группы с видом `SYSTEM`; у
  строки — `semantic_kind`;
- тесты, закреплявшие исключение (§1.3 спеки), переписаны на применимость
  системы, а не удалены — их число в выборе не убывает.

*Часть Б — свой порог*

- при пустом пороге систем и заданном пороге работ ни одна система не
  принята автоматически — по входу на каждую строку таблицы публикации 3а
  для системы, **включая «та же семья»** (решение 14); работа — как до
  фичи;
- при заданном пороге систем: уверенность ровно на пороге — принято, на
  наименьший шаг ниже — нет; система идёт по порогу систем, работа — по
  порогу работ (два разных числа на входе);
- вид читается под блокировкой: смена вида контекста между ответом модели и
  правилом публикации меняет применённый порог (вход);
- массовое автопринятие: кандидаты несут вид, исход считается по
  `threshold_for`; `preview_hash` меняется при смене только порога систем;
  ответ preview несёт `threshold` и `system_threshold`;
- `semantic-auto-accept` CLI работает с обоими порогами.

*Часть В — скрипт кривой*

- метки (решение 15): `accepted`, `accepted_pending` → `correct=True`;
  `rejected`, `other_family` → `False`; `rejected`, чей `id` назван
  `suggestion_id` в событии `context_family_pending` с `outcome ∈
  {cancelled, superseded}`, исключён и посчитан во втором значении
  `load_labels` (вход); `auto_*`, `family_created`, `decision IS NULL` и
  предложения работ — не метки (по входу);
- на фикстуре с известными решениями `curve` даёт ожидаемые доли и точность
  на трёх порогах (литералы в тесте);
- `sufficient`: 300 меток, из них 100 с уверенностью ≥ 0,9 — `True`; 299 и
  100 — `False`; 300 и 99 — `False` (две границы, `docs/insights/claimed-property-needs-its-own-input.md`);
- скрипт не импортирует `backend` приложения и не пишет в базу: в его
  тексте нет `import` модулей приложения (проверка тестом по AST), его
  транзакция — только чтение (`SET TRANSACTION READ ONLY`);
- `main` печатает таблицу, флаг достаточности и число исключённых.

**Имена**

*Часть А — промпт и применимость*

- Заводятся: `SYSTEM_SEMANTIC_PROMPT`, `SYSTEM_PROMPT_VERSION`, `prompt_for`, `SuggestionGroup.system_count`.
- Существуют, проверено `grep`-ом: `is_applicable` (`services/semantic_request.py:205`), `_build_body` (`:402`), `RequestHasher` (`:474`), `render_context_request` (`:426`), `_values_applicable` (`services/semantic_reconcile.py:557`), `_is_applicable` (`services/work_variants.py:716`), `_new_queue` (`crud/semantic_queue.py:610`), `_list_queue` (`:385`), `SuggestionGroup` (`:104`), `_UnitFingerprints` (`:267`), `is_system_name` (`services/semantic_answer.py:45`).

*Часть Б — свой порог*

- Заводятся: `SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD`, `Thresholds`, `thresholds_from`, `threshold_for`, `AutoAcceptPreview.system_threshold`.
- Существуют, проверено `grep`-ом: `apply_publication_rules` (`services/family_change.py:753`), `_apply_publication_rules` (`:781`), `rule_outcome` (`:651`), `_load_candidates` (`:860`), `_preview_hash` (`:920`), `AutoAcceptPreview` (`:826`), `preview_auto_accept` (`:944`), `apply_auto_accept` (`:968`), `SEMANTIC_AUTO_ACCEPT_THRESHOLD` (`config.py:110`).

*Часть В — скрипт кривой*

- Заводятся: модуль и все имена выше.
- Существуют, проверено `grep`-ом: `backend/scripts/__init__.py`, соседние скрипты-образцы `measure_vat_aggregation.py`, `count_plan_edits.py`.

**Проверка**
- `just test-int-local-k catalog_discovery` — ДО ≥ 80, ПОСЛЕ ≥ 120.
- `just test-unit-k catalog_discovery` — ДО 0, ПОСЛЕ ≥ 5.
- `just test-int-local-k "semantic_queue_hooks_ops or semantic_queue_material or work_variants_core or work_variants_reconcile or context_membership"` — ДО 398, ПОСЛЕ ≥ 398.
- `just test-unit-k "semantic_queue_request or semantic_rules"` — ДО 331, ПОСЛЕ ≥ 331.
- `just test-int-local-k "family_change or auto_accept"` — ДО 230, ПОСЛЕ ≥ 230.
- `just test-int-local-k "semantic_worker or semantic_queue_worker"` — ДО 74, ПОСЛЕ ≥ 74.

### Task 3: задание открытия — охват, тело, разбор, исполнение, запуск

**Files**
- Create: `backend/services/family_discovery.py`, `backend/services/discovery_result.py`, `backend/crud/discovery.py`
- Edit: `backend/config.py`, `backend/.env.example`, `backend/services/semantic_cost.py`, `backend/services/variant_answer.py`,
  `backend/services/semantic_worker.py`, `backend/services/semantic_reconcile.py`, `backend/services/semantic_decisions.py`,
  `backend/crud/semantic_queue.py`, `backend/routers/semantic.py`
- Test: `backend/tests/integration/test_catalog_discovery_scope.py`, `test_catalog_discovery_worker.py`,
  `test_catalog_discovery_races.py`, `test_catalog_discovery_launch.py`;
  `backend/tests/unit/test_catalog_discovery_request.py`, `test_catalog_discovery_answer.py`

**Interfaces**

*Часть А — охват и тело*

- Потребляет: `RenderedRequest`, `family_line`, `top_path`, `FAMILY_BLOCK_HEADER`, `_canonical_bytes`/`_sha256_hex`, `SERIALIZATION_VERSION`, `tariffs_from`, `FamilyCategory`, `lock_categories` (Task 1; существуют).
- Производит:

```python
# config.py
SEMANTIC_DISCOVERY_MODEL: str = "anthropic/claude-sonnet-5.5"
SEMANTIC_DISCOVERY_REASONING_EFFORT: Literal["low", "medium", "high"] = "low"
SEMANTIC_DISCOVERY_MAX_TOKENS: int = 32000
SEMANTIC_DISCOVERY_MAX_NAMES: int = 1500
SEMANTIC_DISCOVERY_NAME_MAX_CHARS: int = 300
SEMANTIC_DISCOVERY_PRICE_{INPUT,CACHE_WRITE,CACHE_READ,OUTPUT}_PER_M: Decimal   # 2 / 2.5 / 0.2 / 10

# services/variant_answer.py
DISCOVERY_RESPONSE_FORMAT: dict
DISCOVERY_RESPONSE_SCHEMA_VERSION: str

# services/family_discovery.py
DISCOVERY_PROMPT: str
DISCOVERY_PROMPT_VERSION: int = 1
@dataclass(frozen=True)
class DiscoveryName:
    index: int                       # 1..N
    title: str                       # standard_job_title (полное)
    context_ids: tuple[int, ...]
    articles: tuple[str, ...]        # до трёх
    path: str                        # top_path, до 200 знаков
@dataclass(frozen=True)
class ScopeCounts:
    systems: int; new_family: int; bare: int; names: int; uncategorized_families: int
@dataclass(frozen=True)
class DiscoveryScope:
    unit_id: int | None
    unit_code: str | None
    names: tuple[DiscoveryName, ...]
    context_ids: frozenset[int]
    active_family_ids: tuple[int, ...]
    uncategorized_family_ids: tuple[int, ...]
    category_ids: tuple[int, ...]
    counts: ScopeCounts
def discovery_scope(db: Session, unit_id: int | None) -> DiscoveryScope
def is_in_scope(db: Session, context_ids: Iterable[int]) -> frozenset[int]   # тот же предикат для потребителей снимка
def render_discovery_request(scope: DiscoveryScope, db: Session, *, settings: Settings) -> RenderedRequest
# tariffs_from(settings, SemanticJobKind.family_discovery) -> SEMANTIC_DISCOVERY_PRICE_*
```

*Часть Б — разбор ответа*

- Потребляет: `AnswerSchemaError`, `_check_keys`, `DiscoveryScope` (часть А; существуют).
- Производит:

```python
@dataclass(frozen=True)
class DiscoverySent:              # что ушло модели — для проверки ссылок ответа
    names_count: int
    active_family_ids: frozenset[int]
    uncategorized_family_ids: frozenset[int]
    category_ids: frozenset[int]
@dataclass(frozen=True)
class DiscoveryGroup:
    family_id: int | None
    title: str | None
    definition: str | None
    category_id: int | None
    similar_family_id: int | None
    names: tuple[int, ...]
@dataclass(frozen=True)
class DiscoveryAnswer:
    groups: tuple[DiscoveryGroup, ...]
    not_work: tuple[int, ...]
    family_categories: tuple[tuple[int, int], ...]   # (family_id, category_id)
    unassigned: tuple[int, ...]                      # пропущенные номера
def parse_discovery_answer(raw: str, sent: DiscoverySent) -> DiscoveryAnswer
```

*Часть В — исполнение*

- Потребляет: `render_discovery_request`, `discovery_scope`, `parse_discovery_answer`, `DiscoverySent`, `lock_categories` (Task 1; части А и Б); `render_job_request`, `record_result`, `claim_next`, `_note_closed`, `_split_jobs`, `_open_suggestion_state`, `list_jobs`, `_schema_job_rows`, `_hold_verdict`, `retry_job`, `recover_semantic_jobs` (существуют).
- Производит:

```python
# services/discovery_result.py
@dataclass(frozen=True)
class DiscoveryOutcome:
    applied: bool
    unapplied_reason: Literal["lost_claim", "stale_fingerprint"] | None
    drafts_created: int
    superseded_drafts: int
    unassigned: int
def apply_discovery(db: Session, *, job_id: int, claim_token: UUID,
                    answer: DiscoveryAnswer, settings: Settings) -> DiscoveryOutcome
# render_job_request / record_result — ветвь SemanticJobKind.family_discovery
# JobRow += kind: str, unit_code: str | None, names_count: int | None; context_id: int | None
```

*Часть Г — запуск и блок «Открыть семьи»*

- Потребляет: `discovery_scope`, `render_discovery_request`, `reserve_for_known_prefix`, `expected_cached_cost`, `known_prefix_tokens`, `RESERVE_FORMULA_VERSION`, `_open_suggestion_state` (существуют).
- Производит:

```python
REFUSE_DISCOVERY_IN_PROGRESS = "discovery_in_progress"
REFUSE_DISCOVERY_UNIT_BUSY = "discovery_unit_busy"
REFUSE_DISCOVERY_NOTHING_TO_DO = "discovery_nothing_to_do"
REFUSE_DISCOVERY_TOO_MANY_NAMES = "discovery_too_many_names"
REFUSE_DISCOVERY_INPUT_UNCHANGED = "discovery_input_unchanged"
REFUSE_PREVIEW_CHANGED = "preview_changed"          # существующий код протокола preview
@dataclass(frozen=True)
class DiscoveryPreview:
    unit_id: int | None
    counts: ScopeCounts
    active_families: int
    reserve_usd: Decimal
    expected_cached_usd: Decimal
    preview_hash: str
def preview_discovery(db: Session, *, unit_id: int | None, settings: Settings) -> DiscoveryPreview
def launch_discovery(db: Session, *, unit_id: int | None, preview_hash: str,
                     actor_id: int, settings: Settings) -> SemanticJob
# crud/discovery.py
def discovery_units(db: Session, *, settings: Settings) -> list[dict]   # строка на единицу: counts, оценка, последнее открытие
```

*Маршруты*

```text
GET    /api/v1/semantic/discovery/units
POST   /api/v1/semantic/discovery/preview     {unit_id: int | null}
POST   /api/v1/semantic/discovery             {unit_id: int | null, preview_hash: str}
```

**Утверждения**

*Часть А — охват и тело*

- охват — каждое условие предиката §2.3 отдельным входом (DoD 3): архив; без
  членств; `NOT_APPLICABLE`; семья; ожидание; строка `HEADER`; ждущее
  предложение существующей семьи — вне; система; ждущая «новая семья»;
  «СИСТЕМА»; «голая» единица — в охвате; работа без предложения в единице с
  семьями — вне; `path_broken` охват не меняет;
- `discovery_scope`, `is_in_scope` и рендер считают одно множество
  контекстов на одной фикстуре (один предикат, решение 2);
- тело детерминировано: одно состояние — один `request_hash`; смена каждой
  оси меняет хэш — имя в охвате, статья, путь, активная семья (имя и
  определение), категория справочника (имя и определение), семья без
  категории, модель, `max_tokens`, усилие рассуждения, `response_format` (по
  входу на ось, DoD 2);
- одно наименование в двух статьях — одна строка тела, `context_ids` — оба;
  имена нумеруются `1..N` по побайтной сортировке `standard_job_title`; имя
  длиннее `SEMANTIC_DISCOVERY_NAME_MAX_CHARS` режется с «…», путь — до 200;
  статей у строки не больше трёх, порядок — по числу контекстов, затем по
  названию;
- активные семьи в `system[1]` идут строками `family_line` под
  `FAMILY_BLOCK_HEADER`: `find_privacy_matches` по телу не бросает и
  относит совпадение в имени к `context`, в семье — к `family:<id>`;
- тело не содержит цен, объёмов, подрядчиков, договоров и объектов: на
  фикстуре с заполненными ценами и реквизитами ни одно их значение не
  входит в сериализованное тело (`AGENTS.md` §3);
- единица `NULL` — законный вход: охват и тело строятся;
- `tariffs_from(..., family_discovery)` отдаёт тарифы открытия;
  `reasoning = {"effort": …}`, не `enabled: false`.

*Часть Б — разбор ответа*

- каждое правило разбора §2.3 — схемная ошибка отдельным входом (DoD 6):
  новый черновик без имени, без определения, с пробельным именем; категория
  не из справочника; похожая семья не из активных; группа активной семьи с
  непустым `title`, `definition`, `category_id` или `similar_family_id`;
  активная семья не из отправленных; одна активная семья в двух группах;
  номер 0 и `N+1`; номер в двух группах; номер в группе и в `not_work`;
  `family_categories` с семьёй не из «без категории», с повтором семьи, с
  категорией не из справочника; лишний ключ (`bad_type`, как у разборов 3а);
  пустой `names`;
- пропущенные номера — `unassigned` по возрастанию, не ошибка; пропущенная
  семья в `family_categories` — не ошибка;
- ответ в ```json-ограде и голым объектом разбирается одинаково; текст
  вокруг JSON — схемная ошибка (правило 3а).

*Часть В — исполнение*

- захват открытия: бюджет, `privacy_hold` → «Отправить» → выполнено, «Не
  отправлять» → `cancelled/privacy_declined`; `error` → «Повторить»;
  `running` при старте → `pending` (`recover_semantic_jobs`); схемная ошибка
  → `error` без автоповтора — по входу (DoD 7);
- обработка ответа идёт порядком §2.3 шаги 1–8 (решение 20): тест-сторож
  потока данных пишет порядок захвата блокировок и сверяет с ним
  (`docs/insights/data-flow-assertions-for-order.md`);
- изменение охвата между захватом и ответом (до шага 5) — `stale_fingerprint`,
  задание `cancelled/input_changed`, черновиков нет;
- результат вытесняет открытые черновики и предложения категорий **этой**
  единицы (`superseded`), чужой единицы — нет; номер → все контексты охвата
  с этим наименованием; пропущенные номера — `unassigned` в исходе;
- сверка, проход опросчика и удержанные пачки задание открытия не трогают:
  сверка единицы с живым открытием его не отменяет; `_open_suggestion_state`
  и `_note_closed` его не считают; `_mark_batch_jobs` до него не доходит (вход);
- строка открытия в очередях «Ошибки» и «Задержанные»: единица, число имён,
  текст ошибки или совпадения; `list_jobs` не падает на `context_id IS NULL`;
- **снимок после шага 5** (DoD 7, решение 21, круг 2–3):
  - новые строки при барьере после шага 5 — импорт нового контекста охвата,
    активация семьи с именем черновика, публикация предложения
    существующей семьи члену: обработка записывает черновики, `is_in_scope`
    не включает нового контекста и вышедшего члена;
  - уход члена штатным архивированием: `move_members` всех членств в другой
    контекст корзины (с `new_default_context_id` для умолчания), затем
    `archive_context` — после `commit` обработки; `is_in_scope` члена не
    включает;
  - заблокированные строки при барьере после шага 5 — назначение семьи
    члену, `update_family` с категорией и `archive_family` семьи предложения
    категории, `archive_family` семьи `similar`, переименование категории —
    **ждут** `commit` обработки (таймаут блокировки на второй стороне);
    снятие `FOR KEY SHARE` шага 3 и `FOR SHARE` шага 1 делает вход красным;
- гонки «ответ ↔ правка черновика», «ответ ↔ удаление категории» — без
  deadlock и без потерянной записи; проверено снятием `FOR UPDATE`
  черновиков и `FOR SHARE` категорий соответственно.

*Часть Г — запуск и блок «Открыть семьи»*

- каждый отказ запуска отдельным входом (DoD 8): живое открытие единицы;
  `pending`/`running` предложения в единице; удержанная пачка, касающаяся
  единицы (пачка другой единицы и пачка из одних `context_values` — не
  мешают); пустой охват без семей без категории; имён больше предела;
  тот же `request_hash` в статусах `done`, `error`, `cancelled` (по входу);
- охват пуст, а семьи без категории есть — запуск проходит («только
  категории», §2.10);
- `preview_hash` — sha256 `{request_hash, reserve, cached, тарифы вида,
  RESERVE_FORMULA_VERSION}`; изменение охвата между preview и запуском —
  `preview_changed`; запуск создаёт задание `family_discovery` в `pending`
  с `unit_id`, `prompt_version = "discovery:1"`, своим
  `response_schema_version`, вне потолка события и без пачки;
- оценка — формула резерва фичи 2 с тарифами открытия; у наблюдавшегося
  префикса — его токены;
- `discovery_units`: строка на каждую единицу с непустым охватом или с
  семьями без категории; числа строки совпадают с `discovery_scope` той же
  единицы; последнее открытие — статус, дата, число открытых черновиков;
  единица `NULL` — своя строка.

*Маршруты*

- каждый маршрут части — `admin`, `member` получает `403`; сторож прав зелёный;
- каждый новый код отказа части отвечает статусом таблицы §2.12 (`404`/`409`/`422`)
  с `{code, message}` — по входу на код; ни один код не падает в `AssertionError`
  карты `routers/semantic.py:98-174`;
- `unit_id: null` проходит в блок, preview и запуск.

**Имена**

*Часть А — охват и тело*

- Заводятся: настройки `SEMANTIC_DISCOVERY_*`, `DISCOVERY_RESPONSE_FORMAT`, `DISCOVERY_RESPONSE_SCHEMA_VERSION`, модуль `services/family_discovery.py` и имена выше.
- Существуют, проверено `grep`-ом: `RenderedRequest` (`services/semantic_request.py:147`), `family_line` (`:160`), `top_path` (`:193`), `FAMILY_BLOCK_HEADER` (`:81`), `load_request_material` (`:230`), `tariffs_from` (`services/semantic_cost.py:61`), `find_privacy_matches` (`services/semantic_privacy.py:260`), `SCHEMA_RESPONSE_FORMAT` (`services/variant_answer.py:32`).

*Часть Б — разбор ответа*

- Заводятся: `DiscoverySent`, `DiscoveryGroup`, `DiscoveryAnswer`, `parse_discovery_answer`.
- Существуют, проверено `grep`-ом: `AnswerSchemaError` (`services/semantic_answer.py:67`), `_check_keys` (`services/variant_answer.py:173`), `parse_schema_answer` (`:211`).

*Часть В — исполнение*

- Заводятся: модуль `services/discovery_result.py`, `DiscoveryOutcome`, `apply_discovery`, поля `JobRow`.
- Существуют, проверено `grep`-ом: `render_job_request` (`services/semantic_worker.py:219`), `record_result` (`:468`), `claim_next` (`:274`), `_note_closed` (`:269`), `_split_jobs` (`services/semantic_reconcile.py:918`), `_open_suggestion_state` (`:637`), `list_jobs` (`crud/semantic_queue.py:868`), `_schema_job_rows` (`:837`), `_hold_verdict` (`services/semantic_decisions.py:401`), `retry_job` (`:622`), `recover_semantic_jobs` (`services/semantic_runner.py:33`), `move_members`, `archive_context` (`services/context_operations.py:610`, `:759`), `archive_family` (`services/work_families.py:992`).

*Часть Г — запуск и блок «Открыть семьи»*

- Заводятся: пять кодов, `DiscoveryPreview`, `preview_discovery`, `launch_discovery`, модуль `crud/discovery.py`, `discovery_units`.
- Существуют, проверено `grep`-ом: `reserve_for_known_prefix` (`services/semantic_cost.py:127`), `expected_cached_cost` (`:156`), `known_prefix_tokens` (`:102`), `RESERVE_FORMULA_VERSION` (`:35`), `_confirm` (`services/semantic_decisions.py:713`), `preview_unit_reask` (`:724`), `_open_suggestion_state` (`services/semantic_reconcile.py:637`).

**Проверка**
- `just test-int-local-k catalog_discovery` — ДО ≥ 120, ПОСЛЕ ≥ 190.
- `just test-unit-k catalog_discovery` — ДО ≥ 5, ПОСЛЕ ≥ 45.
- `just test-int-local-k "semantic_worker or semantic_queue_worker"` — ДО ≥ 74, ПОСЛЕ ≥ 74.
- `just test-unit-k "semantic_queue_settings or work_variants_settings or semantic_queue_architecture"` — зелёная, число не убывает.
- `uv run pytest tests -k auth_coverage` — ДО ≥ 151, ПОСЛЕ ≥ 154.

### Task 4: черновики, активация, «Вернуть в разбор»

**Files**
- Create: `backend/services/discovery_drafts.py`
- Edit: `backend/crud/discovery.py`, `backend/services/work_variants.py`, `backend/services/semantic_events.py`,
  `backend/crud/semantic.py`, `backend/routers/semantic.py`
- Test: `backend/tests/integration/test_catalog_discovery_drafts.py`, `test_catalog_discovery_activate.py`,
  `test_catalog_discovery_reopen.py`, `test_catalog_discovery_api.py`

**Interfaces**

*Часть А — черновики*

- Потребляет: `FamilyDraft`, `FamilyDraftMember`, `FamilyCategoryProposal`, `lock_categories`, `is_in_scope`, `REFUSE_FAMILY_NOT_ACTIVE`, `REFUSE_CATEGORY_NOT_FOUND` (Task 1, 3; существуют).
- Производит:

```python
REFUSE_DRAFT_NOT_OPEN = "draft_not_open"
REFUSE_DRAFT_NOT_RESTORABLE = "draft_not_restorable"
REFUSE_DISCOVERY_RUN_SUPERSEDED = "discovery_run_superseded"
def latest_discovery_job_id(db: Session, unit_id: int | None) -> int | None
def edit_draft(db: Session, *, draft_id: int, title: str | object = UNSET,
               definition: str | object = UNSET, family_category_id: int | object = UNSET,
               actor_id: int) -> FamilyDraft
def merge_draft(db: Session, *, draft_id: int, target_draft_id: int | None = None,
                target_family_id: int | None = None, actor_id: int) -> FamilyDraft
def discard_draft(db: Session, *, draft_id: int, actor_id: int) -> FamilyDraft
def restore_draft(db: Session, *, draft_id: int, actor_id: int) -> FamilyDraft
# crud/discovery.py
def discovery_drafts(db: Session, *, unit_id: int | None) -> dict | None
#   группы с числом строк (свои + влитые транзитивно, только is_in_scope), три примера,
#   «не работа» построчно по наименованиям, «в активные семьи», остаток, предложения категорий
```

*Часть Б — активация*

- Потребляет: `create_family`, `activate_family`, `update_family`, `acquire_family_locks`, `take_context_off_work`, `reconcile_or_defer`, `lock_categories`, `is_in_scope`, `latest_discovery_job_id` (Task 1, 3; часть А; существуют).
- Производит:

```python
REFUSE_DRAFT_WITHOUT_CATEGORY = "draft_without_category"
REFUSE_CONTEXT_NOT_IN_GROUP = "context_not_in_group"
REFUSE_CATEGORY_NOT_PROPOSED = "category_not_proposed"
@dataclass(frozen=True)
class ActivationOutcome:
    created_family_ids: tuple[int, ...]
    categories_applied: tuple[int, ...]
    categories_skipped: tuple[int, ...]
    not_work_applied: tuple[int, ...]
    not_work_skipped: tuple[int, ...]
    reask_unit_id: int | None
def activate_discovery(db: Session, *, job_id: int, draft_ids: list[int],
                       not_work_context_ids: list[int],
                       family_categories: list[tuple[int, int]],
                       actor_id: int) -> ActivationOutcome
```

*Часть В — «Вернуть в разбор»*

- Потребляет: `acquire_family_locks`, `_lock_rows` (review), `_NOT_APPLICABLE_CATALOG_KINDS`, `reconcile_or_defer`, `record_event`, `context_card`, `mark_context_not_work` (существуют).
- Производит:

```python
REFUSE_CONTEXT_NOT_REOPENABLE_STATE = "context_not_reopenable_state"
REFUSE_CONTEXT_NOT_APPLICABLE_BY_POSITION = "context_not_applicable_by_position"
def is_reopenable(*, semantic_state: str, catalog_kind: str, archived: bool) -> bool
def reopen_context(db: Session, *, context_id: int, actor_id: int) -> CatalogContext
# context_card += reopenable: bool, catalog_kind: str
# EVENT_REQUIRED_KEYS["context_reopened"] = {"from_state", "to_state"}
```

*Маршруты*

```text
GET    /api/v1/semantic/discovery/drafts?unit_id=
PATCH  /api/v1/semantic/discovery/drafts/{id}
POST   /api/v1/semantic/discovery/drafts/{id}/merge    {target_draft_id} | {target_family_id}
POST   /api/v1/semantic/discovery/drafts/{id}/discard
POST   /api/v1/semantic/discovery/drafts/{id}/restore
POST   /api/v1/semantic/discovery/{job_id}/activate    {draft_ids, not_work_context_ids, family_categories: [{family_id, family_category_id}]}
POST   /api/v1/semantic/contexts/{id}/reopen
```

**Утверждения**

*Часть А — черновики*

- «Править»: имя, определение, категория; `edited_by/at`; пустое имя или
  определение — отказ формы `CK_DRAFT_SHAPE` переведён в `422`; правка на
  удалённую категорию — `category_not_found`;
- «Слить с черновиком»: источник `merged`, **члены остались у источника**,
  цель показывает свои и влитые транзитивно (вход: A → B, затем B → C —
  у C три набора); слияние в себя, в не-`open`, в черновик другого открытия
  — отказ;
- «Слить с активной семьёй»: семья не изменилась ни одним полем (сравнение
  строки до/после); неактивная семья — `family_not_active`; семья другой
  единицы — отказ;
- «Отбросить» → `discarded`; «Вернуть» отброшенный и слитый (с черновиком и
  с семьёй) → `open`, `merged_into_*` и `decided_*` очищены, числа цели
  вернулись к прежним (по входу на каждый, DoD 9); «Вернуть» `activated`,
  `superseded`, `open` — `draft_not_restorable`;
- каждое действие над не-`open` (кроме «Вернуть») и над группой
  `existing`/`not_work` — `draft_not_open`; над черновиком не последнего
  открытия — `discovery_run_superseded`;
- вид черновиков: число строк считает только членов, которые **сейчас** в
  охвате (`is_in_scope`): член, получивший семью после ответа, не
  считается; группы отсортированы по числу строк; «не работа» — строки по
  наименованию с числом контекстов;
- гонка «правка категории черновика ↔ удаление этой категории» — без
  deadlock (порядок решения 20), исход — `category_not_found` или черновик
  с `NULL`; проверено снятием `FOR SHARE` категории в `edit_draft`.

*Часть Б — активация*

- шаги §2.5 0–5 одной транзакцией, порядок блокировок решения 20 (сторож
  потока данных, как в Task 3);
- отмеченные черновики → активные семьи единицы с категориями; события
  `family_created` (`origin = "discovery"`) и `family_activated`; черновик
  → `activated` с `activated_family_id` (DoD 10);
- дубль активного имени единицы — отказ всей активации
  `duplicate_active_family` с номером черновика, ни одной семьи не создано
  (вход: два черновика, второй — дубль); черновик без категории —
  `draft_without_category`; черновик не этого открытия или не `open` —
  `draft_not_open`; открытие не последнее — `discovery_run_superseded`;
- категории: применены парами (человек мог сменить предложенную);
  семья, получившая категорию после ответа или переставшая быть `active`,
  пропущена и названа; пара не из предложений открытия —
  `category_not_proposed`; предложение → `applied`;
- «не работа»: только члены группы «Не работа» открытия
  (`context_not_in_group` иначе); член, вышедший из охвата, пропущен и
  назван; остальным — `take_context_off_work(reason="manual")`, их задания
  отменены, предложения сняты сверкой той же транзакции; одно наименование
  в двух статьях — оба контекста;
- транзакционность: сбой, вброшенный на последнем шаге, оставляет базу как
  до вызова (снятие транзакции делает тест красным);
- гонка «активация ↔ удаление категории» — без deadlock, исход один из
  допустимых; проверено снятием шага 0 (`FOR SHARE` категорий):
  deadlock воспроизводится (DoD 14, круг 1);
- `reask_unit_id` — единица открытия.

*Часть В — «Вернуть в разбор»*

- «не работа» человека → возврат: `semantic_kind_source = manual` даёт
  `CONFIRMED`, `rule` — `SUGGESTED` (два входа, решение 13); семья, вариант
  и значения пусты; событие `context_reopened` с автором и `{from_state,
  to_state}`; задание `family_suggestion` поставлено той же транзакцией
  (контекст применим — в единице есть активная семья);
- отказ `context_not_applicable_by_position` на строке `HEADER`,
  `LOT_HEADER`, `TRASH` (три входа) и на «не работе» человека, чью строку
  потом пометили `set_position_kind_global`; отказ
  `context_not_reopenable_state` на `SUGGESTED`; `context_archived` на
  архивном;
- `is_reopenable` и `reopen_context` дают одно решение на всех входах выше
  (один предикат; тест — таблица входов против обоих);
- параллельные возврат и глобальная пометка той же строки — без deadlock,
  исход один из двух допустимых; проверено снятием `FOR SHARE` строки в
  `reopen_context` (воспроизводится возврат контекста, чья строка уже
  `HEADER`);
- карточка контекста несёт `reopenable` и `catalog_kind`.

*Часть Г — DoD 7: потребители снимка и гонка с активацией* (ревью гейта 3)

- сценарии снимка Task 3 доводятся здесь до потребителей — на тех же входах,
  изменение после `commit` обработки, до чтения и активации черновиков:
  член, получивший семью, и член, ушедший штатным архивированием
  (`move_members` → `archive_context`), — `discovery_drafts` их не считает,
  активация «не работы» их пропускает и называет в `not_work_skipped`; новая
  активная семья с именем черновика — активация отказывает
  `duplicate_active_family`; архивированная семья `similar` — «Слить с
  активной семьёй» отказывает `family_not_active`; семья предложения
  категории, получившая категорию или архивированная, — пропущена в
  `categories_skipped`; новый контекст охвата из импорта в черновики не
  попадает и активацией не трогается (по входу на сценарий, DoD 7);
- гонка «обработка ответа ↔ активация» одной единицы: активация черновиков
  последнего открытия, пока обрабатывается ответ следующего, — без deadlock,
  исход один из двух допустимых: **активация прошла раньше** — её черновики
  `activated`, созданная семья меняет вход открытия, обработка на шаге 5
  видит другой `request_hash` (`stale_fingerprint`), задание
  `cancelled/input_changed`, новых черновиков нет, неотмеченные черновики
  прежнего открытия остаются `open`; либо **обработка прошла раньше** —
  прежние открытые черновики `superseded`, новые записаны, активация
  отказывает `discovery_run_superseded`, не создав ни одной семьи; вход
  активирует хотя бы один черновик (активация без изменений входа хэш не
  меняет); проверено снятием `FOR UPDATE` черновиков в `activate_discovery`
  (активация пишет в вытесненные черновики).

*Маршруты*

- каждый маршрут части — `admin`, `member` получает `403`; сторож прав зелёный;
- каждый новый код отказа части отвечает статусом таблицы §2.12 (`404`/`409`/`422`)
  с `{code, message}` — по входу на код; ни один код не падает в `AssertionError`
  карты `routers/semantic.py:98-174`;
- черновики — группы, члены, остаток, предложения категорий; активация —
  `ActivationOutcome` с `reask_unit_id`; ошибка сервиса активации не оставляет
  записей (одна транзакция маршрута); `unit_id: null` проходит в черновики.

**Имена**

*Часть А — черновики*

- Заводятся: модуль `services/discovery_drafts.py` и имена выше, `discovery_drafts`.
- Существуют, проверено `grep`-ом: `REFUSE_FAMILY_NOT_ACTIVE` (`services/work_families.py:153`), `UNSET` (`:110`).

*Часть Б — активация*

- Заводятся: три кода, `ActivationOutcome`, `activate_discovery`.
- Существуют, проверено `grep`-ом: `acquire_family_locks` (`services/family_change.py:163`), `take_context_off_work` (`services/work_variants.py:1105`), `reconcile_or_defer` (`services/semantic_reconcile.py:1547`), `REFUSE_DUPLICATE_ACTIVE_FAMILY` (`services/work_families.py:142`).

*Часть В — «Вернуть в разбор»*

- Заводятся: два кода, `is_reopenable`, `reopen_context`, событие `context_reopened` в реестрах `semantic_events.py`.
- Существуют, проверено `grep`-ом: `mark_context_not_work` (`services/work_variants.py:1057`), `take_context_off_work` (`:1105`), `acquire_family_locks` (`services/family_change.py:163`), `_lock_rows` (`services/review.py:90`), `_NOT_APPLICABLE_CATALOG_KINDS` (`services/context_routing.py:458`), `reconcile_or_defer` (`services/semantic_reconcile.py:1547`), `context_card` (`crud/semantic.py:682`), `set_position_kind_global` (`services/review.py:907`).

**Проверка**
- `just test-int-local-k catalog_discovery` — ДО ≥ 190, ПОСЛЕ ≥ 260.
- `uv run pytest tests -k auth_coverage` — ДО ≥ 154, ПОСЛЕ ≥ 161.

### Task 5: фронт — категории, «Вернуть в разбор», метка «система», строки открытия в очередях

**Files**
- Create: `frontend/src/pages/families/FamilyCategoriesDialog.tsx` (+ `.test.tsx`)
- Edit: `frontend/src/types/domain.ts`, `services/api/domain.ts`, `services/queries.ts`, `services/queryKeys.ts`, `test/handlers.ts`, `pages/families/FamiliesTab.tsx`, `CreateFamilyDialog.tsx`, `ContextCard.tsx`, `SuggestionGroups.tsx`, `ErrorsQueue.tsx`, `PrivacyHoldBlock.tsx`, `labels.ts` (+ их тесты)

**Interfaces**
- Потребляет: маршруты Task 1, 3, 4; `useWorkFamilies`, `useUpdateWorkFamily`, `useActivateWorkFamily`, `useCreateFamilyFromSuggestion`, `useMarkNotWork`, `SEMANTIC_KIND_LABEL`, типы `WorkFamily`, `ContextCardData`, `SuggestionGroup`, `JobRow` (существуют).
- Производит:

```ts
export interface FamilyCategory { id: number; title: string; definition: string; seed_key: string | null; family_count: number }
// WorkFamily += family_category_id: number | null; family_category_title: string | null
// ContextCardData += reopenable: boolean; catalog_kind: string
// SuggestionGroup += system_count: number; строка += semantic_kind: SemanticKind
// JobRow += kind: SemanticJobKind; unit_code: string | null; names_count: number | null; context_id: number | null
export function useFamilyCategories(): UseQueryResult<FamilyCategory[]>
export function useCreateFamilyCategory(), useUpdateFamilyCategory(), useDeleteFamilyCategory()
export function useReopenContext()
// useWorkFamilies(status?, unitId?, categoryId?: number | "none")
```

**Утверждения**
- «Семьи» (К1): колонка «Категория» после «Статус», фильтр «Категория»
  рядом с «Единица» (значения справочника и «без категории»), кнопка
  «Категории…»; фильтр уходит в запрос и в ключ кэша; выбор категории на
  карточке семьи; «Активировать» без категории показывает подпись отказа;
- окно справочника (К2): имя, определение, «Семей», «Править»,
  «Добавить категорию»; удаление — только у категории без семей (кнопка
  неактивна при `family_count > 0`, отказ `category_in_use` показан
  подписью);
- «Завести семью…» — поле «Категория» обязательно, без него кнопка
  неактивна;
- карточка контекста: «Вернуть в разбор» при `reopenable`, подтверждение,
  перечитывание карточки; у неприменимых по строке — подпись «Размечено в
  Review как …» без кнопки; подпись `context_not_applicable` нейтральна
  («вид неприменим: контекст не работа»);
- «Семья из списка»: метка «система» у группы при `system_count > 0` и у
  каждой строки-системы; ключ группы не меняется;
- «Ошибки» и «Задержанные»: строка открытия — единица, число имён, текст;
  «Повторить», «Отправить», «Не отправлять» работают по ней;
- все новые коды отказов имеют подписи в `labels.ts` (тест: каждый код
  таблицы §2.12 — непустая подпись);
- каждый новый хук вызван компонентом — доказано тестом компонента, а не
  юнитом хука (`docs/insights/prove-the-component-calls-it.md`).

**Имена**
- Заводятся: `FamilyCategoriesDialog`, `FamilyCategory`, пять хуков, новые поля типов, ключи `qk.familyCategories`.
- Существуют, проверено `grep`-ом: `useWorkFamilies` (`services/queries.ts:1498`), `useUpdateWorkFamily` (`:1517`), `useActivateWorkFamily` (`:1532`), `useMarkNotWork` (`:1698`), `useCreateFamilyFromSuggestion` (`:2240`), `WorkFamily` (`types/domain.ts:2163`), `ContextCardData` (`:2423`), `SuggestionGroup` (`:2702`), `JobRow` (`:2923`), `SEMANTIC_KIND_LABEL` (`pages/families/labels.ts:38`).

**Проверка**
- `npx vitest run src/pages/families` — ДО 672, ПОСЛЕ ≥ 700.
- `just lint-frontend` и `just typecheck-frontend` — зелёные.

### Task 6: фронт — блок «Открыть семьи», окно запуска, черновики

**Files**
- Create: `frontend/src/pages/families/discovery/DiscoveryBlock.tsx`, `DiscoveryLaunchDialog.tsx`, `DiscoveryDrafts.tsx`, `DraftCard.tsx`, `NotWorkGroup.tsx`, `CategoryProposals.tsx` (+ `.test.tsx` у каждого)
- Edit: `frontend/src/pages/families/NewQueue.tsx`, `SuggestionsTab.tsx`, `types/domain.ts`, `services/api/domain.ts`, `services/queries.ts`, `services/queryKeys.ts`, `test/handlers.ts`

**Interfaces**
- Потребляет: маршруты Task 1, 3, 4; `PreviewDialog`, `PreviewTarget`, `useReaskPreview`, `useFamilyCategories` (Task 5; существуют).
- Производит:

```ts
export interface DiscoveryUnitRow { unit_id: number | null; unit_code: string | null; counts: ScopeCounts; estimate_usd: string; last_run: DiscoveryRunInfo | null }
export interface DiscoveryDraftsView { job_id: number; unit_id: number | null; opened_at: string; groups: DraftView[]; not_work: NotWorkRow[]; existing: ExistingGroupView[]; unassigned_count: number; category_proposals: CategoryProposalView[] }
export function useDiscoveryUnits(), useDiscoveryPreview(), useLaunchDiscovery(), useDiscoveryDrafts(unitId),
  useEditDraft(), useMergeDraft(), useDiscardDraft(), useRestoreDraft(), useActivateDiscovery()
```

**Утверждения**
- экран 1: блок над таблицей «Новой» — строка на единицу: числа и оценка из
  `/discovery/units`, кнопка «Открыть семьи…»; таблица «Новой» не
  изменилась;
- экран 2: окно — систем/строк без семьи, активных семей единицы, «уходит
  наружу: имена, статьи, пути разделов, активные семьи и категории»,
  оценка; запуск с `preview_hash`; `409 preview_changed` — перечитывание
  preview с подписью, без молчаливого повтора (как `PreviewDialog`);
- живое открытие — строка «открывается…» с перечитыванием до конца;
- экран 3: группы с именем, определением, выбором категории, «похоже на
  …», числом строк, тремя примерами, сортировкой по числу строк; «Править»,
  «Слить с…» (черновик или активная семья), «Отбросить»; свёрнутые слитые и
  отброшенные с «Вернуть»; «в активные семьи» свёрнутыми строками; остаток
  числом; «Не работа» построчно, все отмечены, первые 20 видны, «Показать
  все»; «Категории активных семей» с выбором и галочкой;
- счётчик «Отмечено N черновиков · M строк»; «Активировать отмеченные и
  перезапросить «…»…» шлёт отмеченные черновики, отмеченные строки «Не
  работы» (все контексты наименования) и отмеченные пары категорий; по
  ответу открывается `PreviewDialog` перезапроса единицы `reask_unit_id`;
  пропущенные строки и категории названы уведомлением;
- отказ активации (`duplicate_active_family`, `draft_without_category`)
  показывает подпись у черновика, отметки не сбрасываются;
- вид сверен снимками экрана рядом с макетом (экраны 1–3) — в отчёте
  задачи, а не тестом (`docs/insights/unobservable-in-the-runner.md`).

**Имена**
- Заводятся: шесть компонентов, типы и хуки выше.
- Существуют, проверено `grep`-ом: `PreviewDialog` (`pages/families/PreviewDialog.tsx`), `PreviewTarget` (`types/domain.ts:2893`), `useReaskPreview` (`services/queries.ts:2027`), `NewQueue` (`pages/families/NewQueue.tsx`).

**Проверка**
- `npx vitest run src/pages/families` — ДО ≥ 700, ПОСЛЕ ≥ 740.
- `just lint-frontend` и `just typecheck-frontend` — зелёные.

### Task 7: ревизия `AGENTS.md`, стенд, devlog

**Files**
- Create: `docs/devlog/2026-10-09-catalog-discovery.md`
- Edit: `AGENTS.md`, `docs/AGENTS-revisions.md`, `docs/reference/screens.md`, `docs/reference/schema.md`, `docs/product-roadmap.md`

**Interfaces**

*Часть А — документация и ревизия*

- Потребляет: всё сделанное Task 1–6.
- Производит: ревизия `AGENTS.md` (номер — в коммите ревизии).

*Часть Б — стенд и финал*

- Потребляет: всё сделанное; стенд `gca_dev` на `0020`.

**Утверждения**

*Часть А — документация и ревизия*

- `AGENTS.md` §3 «Без брокеров» — «исполняет четыре вида заданий»; §3 «Что
  уходит наружу» — «…и семьи (имена, определения, категории — имена и
  определения справочника категорий — и ценовые параметры …)», запреты
  без изменений; §3 роли `admin` — «открытие семей и черновики, справочник
  категорий семей, возврат контекста в разбор»;
- преамбула — новая действующая врезка в форме v6.28; прежняя уехала в
  `docs/AGENTS-revisions.md` целиком;
- `screens.md` `## 9.` — абзацы «Открыть семьи», «Черновики семей»,
  «Категории семей», «Вернуть в разбор»; якорь не изменился;
- `schema.md` совпадает с моделями после Task 1–4 (каждая таблица и
  колонка §2.2 названа);
- ничего, кроме перечисленного, в `AGENTS.md` не изменилось (дифф).

*Часть Б — стенд и финал*

- порядок §2.13 спеки: миграция `0021` на `gca_dev`; опросчик включает
  пользователь; открытие «компл», просмотр, активация с категориями двух
  семей «компл»; перезапрос «компл»; то же для шести единиц работ; системы в
  «Семье из списка» пачками; схемы, значения, варианты сверкой (DoD 15);
- в devlog по единице: имён, черновиков, «в активные семьи», «не работы»,
  остатка, стоимость открытия, доля схемных ошибок; блок «Открыть семьи»
  совпал с замером дня приёмки (`m1_stand.py` или его преемник);
- у всех активных семей категория; банковские гарантии и коммунальные
  расходы — в семьях «Затрат и услуг»; ни одна система не принята
  автоматически; подтверждённые системы получили схему, значения, вариант и
  `POSITION`; «Вернуть в разбор» на ошибочно отмеченной строке; доля
  `TO_REVIEW` — число дня приёмки;
- кривая `measure_system_threshold.py` — когда решений достаточно, иначе число
  решений на день приёмки и оценка срока; порог выбирает пользователь;
- снимки экранов 1–3, 3б, 4, К1–К3 рядом с макетом — в devlog;
- devlog называет тронутые области и прочитанные файлы граблей
  (`docs/pitfalls/db.md`, `backend.md`, `frontend.md`, `runs.md`,
  `process.md`, DoD 17); отступления от плана;
- дорожная карта: А1 закрыт; «Решения за пользователем» — порог систем ждёт
  кривой;
- `just ci` зелёный перед пушем.

**Имена**

*Часть А — документация и ревизия*

- Существуют, проверено `grep`-ом: `«Без брокеров»`, `«Что уходит наружу»`, `Single-tenant` (`AGENTS.md` §3).

**Проверка**
- `just check-agents-index` — 18 из 18.
- `just ci` — зелёный перед пушем.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: `just ci` (§9.3), плюс прогон на стенде (Task 7).
