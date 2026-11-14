# План: пауза до сброса лимита провайдера вместо ошибок

**Спека:** `docs/superpowers/specs/2026-11-15-provider-budget-pause-design.md` (гейт 2 одобрен 15.11.2026, редакция 2, `a4f4a13`)
**Дизайн:** `docs/superpowers/design/2026-11-15-provider-budget-pause.md` (редакция 3)
**Ветка:** `fix/provider-budget-pause`, черновой PR #83
**Статус:** редакция 1, 15.11.2026, на гейте 3.

> Исполнителю: задачи по порядку; каждая — цикл TDD
> (`superpowers:test-driven-development`: тесты первыми, красный прогон
> показать). Адреса `§N` без уточнения — разделы спеки, «проверка N» — её §3;
> «решение N» — решения этого плана. Защит, дублирующих правило, не класть.
> ДО сняты 15.11.2026 на `a4f4a13`: шаблон
> `-k "provider_budget or ProviderBudget or build_wait or BuildWait"` выбирает
> **0** тестов из 12 821 в `tests/unit` и `tests/integration`; затронутые
> наборы бэкенда (команда в Task 3) — **916** тестов.

## Режим реализации (ускоренный, как в прошлых фичах)

- **Исполнители** — Sonnet; оркестратор (Opus) принимает, коммитит, ведёт
  `progress.md`. Task 5 — оркестратор сам.
- **Ревью — только у Task 3** (деньги, судьба задания, остановка очереди):
  Opus с узким брифом — правила §2.2–§2.3 и «что сделало бы этот тест
  ложно-зелёным». Остальные задачи — приёмка оркестратором: тесты, ruff,
  tsc, eslint, снимок стенда.
- **Снятия защит делает оркестратор лично** — помеченные «снятие» в §3 спеки,
  в копии дерева, по протоколу `BASELINE_GREEN / MUTATION_APPLIED /
  EXPECTED_TEST_FAILURE / RESTORED`, `-n 1`, жёсткий таймаут на прогон; итог
  — в `progress.md` и devlog. Отступление от `docs/process/implementation.md`
  (снятия — ревьюером) по решению пользователя 28.10.2026, как в прошлых
  фичах.
- **Прогоны.** Исполнитель в итерациях гоняет только тесты задачи; затронутые
  наборы — один раз в конце задачи; ревьюер их не повторяет. Полный бэкенд и
  `just ci` локально не гоняются — GitHub Actions на PR.
- **Без остановок** между задачами; стоп — пуш и внешнее ревью.

## Global Constraints

- Тело запросов к модели, `RECONCILE_ALLOWLIST`, резервы и их формулы — без
  изменений (§2.8). Исполнитель очереди домен не пишет.
- Деньги — `Decimal` от разбора JSON (`parse_float=Decimal`) до записи; float
  нигде (`AGENTS.md` §3).
- Сигнатуры существующих функций меняются только добавлением именованного
  параметра со значением по умолчанию, сохраняющим нынешнее поведение.
  `_open_suggestion_state` сохраняет и подпись, и ответ — у неё два
  потребителя кроме схемы (`services/family_discovery.py`, сверка в
  `semantic_reconcile.py`).
- Существующие тесты затронутых наборов зелёные **без правок**. Исключение —
  тексты плашки остановки в `SuggestionsHeader.test.tsx`, если тест
  сравнивает весь текст: причина `reserve_exceeded` печатает прежний текст,
  поломка допустима только от смены разметки, исполнитель называет её.
- Субагентам: кириллицу в терминал не печатать, вывод pytest и vitest — в
  файл, `PYTHONIOENCODING=utf-8`; vitest запускать из `C:\…` (заглавная буква
  диска).

## Структура файлов

- Edit: `backend/services/semantic_client.py` — класс отказа по деньгам,
  разбор 402/403 (§2.1).
- Create: `backend/alembic/versions/2026_11_15_0026-provider_budget_pause.py`.
- Edit: `backend/models.py` — `SemanticAttemptOutcome.budget_refused`, пять
  полей и CHECK у `SemanticWorkerState`.
- Edit: `backend/services/semantic_worker.py` — ветка отказа в
  `record_failure`, пауза и остановка, проверка паузы в `claim_next`.
- Edit: `backend/crud/semantic_queue.py` — `budget_pause` в `QueueStatus`.
- Edit: `backend/services/semantic_reconcile.py` — разбор удержаний с
  номерами пачек, `schema_build_wait`.
- Edit: `backend/crud/work_variants.py` — `build_wait` в `SchemaOut` (схема семьи).
- Edit: `frontend/src/types/domain.ts`, `SuggestionsHeader.tsx`,
  `ContextCard.tsx`, `FamiliesTab.tsx`, `SchemaBlock.tsx` (все — в
  `frontend/src/pages/families/`, кроме типов) и их тесты.
- Create: `backend/tests/integration/test_provider_budget_pause.py`,
  `backend/tests/integration/test_provider_budget_schema.py`.
- Edit: `backend/tests/unit/test_semantic_queue_client.py`.
- Edit: `docs/reference/schema.md`, `docs/reference/screens.md`,
  `docs/TECH_DEBT.md`, `backend/.env.example`.
- Create: `docs/devlog/2026-11-15-provider-budget-pause.md`.

## Решения плана, которых нет в спеке

1. **Пять задач:** клиент; миграция и модель; опросчик и статус; причина
   ожидания схемы и весь фронт; документы и стенд. Клиент и миграция — до
   опросчика, потому что опросчик потребляет оба.
2. **Имя класса** — `ProviderBudgetError(TransientModelError)`; действие —
   `BudgetAction` (`pause` / `stop`). Причины остановки — константы рядом с
   `PAUSE_REASON_RESERVE_EXCEEDED` в `semantic_worker.py`.
3. **Остановку ставит одна функция.** Нынешний код остановки из
   `_apply_fuse` выносится в `_stop_claim(state, reason, attempt_id, now)`;
   её зовут и предохранитель, и отказ по деньгам. «Первая остановка
   остаётся» и «остановка снимает паузу по времени» живут в ней одной.
4. **Причина ожидания схемы** — новая `open_suggestion_holds(db)`, которая
   возвращает и единицы, и удержавшие их пачки; `_open_suggestion_state` и
   новая `schema_build_wait` строятся из неё, а `schema_ready_to_build`
   становится `schema_build_wait(...) is None`. Так у трёх потребителей одно
   правило отбора (§2.7 п. 3).
5. **Тест миграции** — отдельный файл `*_schema.py` с маркером
   `migration_roundtrip`: его гоняет `backend-slow` по пути
   `backend/alembic/**`, а основной набор нет.
6. **Плашки отсчитывают время от `until` статуса**, без своего таймера:
   статус перечитывается тем же опросом, что сегодня.

## Задачи

### Task 1: клиент распознаёт отказ по деньгам

**Files**
- Edit: `backend/services/semantic_client.py`
- Test: `backend/tests/unit/test_semantic_queue_client.py`

**Interfaces**
- Потребляет: `TransientModelError`, `PermanentModelError`,
  `OpenRouterClient.complete` (существуют).
- Производит:

```python
class BudgetAction(str, enum.Enum):
    pause = "pause"
    stop = "stop"

class ProviderBudgetError(TransientModelError):
    action: BudgetAction
    resets_at: datetime | None          # 403 с временем сброса
    retry_after_s: int | None           # 402 «бюджет на лету»
    stop_reason: str | None             # provider_credits | provider_budget_no_reset | provider_refusal_unknown
    limit_source: str | None
    limit_usd: Decimal | None
    spend_usd: Decimal | None
```

**Утверждения**
- Каждая строка таблицы §2.1 даёт своё действие, причину и `error_class`
  (проверка 1); 402 без тела, без `error`, без `metadata`, без
  `limit_source` и с неизвестным источником — `stop` /
  `provider_refusal_unknown`, не `PermanentModelError`.
- `Retry-After`: `"30"` → 30; `"0"`, `"4000"`, дата, `"abc"`, нет заголовка
  → 60 (проверка 2).
- `resets_at` `"2026-11-16T00:00:00.000Z"` → время с поясом UTC;
  `null` → `stop` / `provider_budget_no_reset`; `"2026-11-16 00:00"` (без
  пояса) и отсутствие ключа → `stop` / `provider_refusal_unknown`.
- `limit_usd` и `spend_usd` — `Decimal` из JSON-чисел (`5` → `Decimal("5")`,
  `5.0001` без потери); строка или `null` → `None`.
- 403 с `metadata` модерации (без бюджетного `limit_source`) — по-прежнему
  `PermanentModelError` с `http_403` (проверка 3).
- 429, 5xx, 400, 401 — как сегодня (существующие тесты `TestErrors` зелёные
  без правок).

**Имена**
- Заводятся: `BudgetAction`, `ProviderBudgetError`.
- Существуют, проверено `grep`-ом: `TransientModelError`,
  `PermanentModelError`, `OpenRouterClient`, `_ERROR_BODY_LIMIT`.

**Проверка**
- `uv run --locked pytest tests/unit/test_semantic_queue_client.py` — зелёная;
  новые тесты называются с `provider_budget`, шаблон ДО → 0, ПОСЛЕ → число
  новых тестов задачи.

### Task 2: миграция 0026 и модель

**Files**
- Create: `backend/alembic/versions/2026_11_15_0026-provider_budget_pause.py`
- Edit: `backend/models.py`
- Test: `backend/tests/integration/test_provider_budget_schema.py`

**Interfaces**
- Потребляет: `SemanticAttemptOutcome`, `SemanticWorkerState`, CHECK
  `ck_semantic_job_attempts_outcome` (существуют).
- Производит: `SemanticAttemptOutcome.budget_refused`; у
  `SemanticWorkerState` — `budget_paused_until`, `budget_pause_attempt_id`,
  `budget_pause_source`, `budget_limit_usd`, `budget_spend_usd`; CHECK
  `ck_semantic_worker_state_budget_pause_triple` (срок, попытка, источник —
  все `NULL` или все заданы) и `ck_semantic_worker_state_budget_sums` (суммы
  без срока не бывают).

**Утверждения**
- upgrade на пустой базе даёт пять полей и оба CHECK; `alembic check` после
  upgrade без расхождений с моделью.
- Попытка с исходом `budget_refused` вставляется после upgrade и отвергается
  CHECK-ом до него.
- Строка состояния со сроком без попытки — отказ CHECK; с суммой без срока —
  отказ.
- downgrade на пустой базе возвращает прежний CHECK исхода и снимает поля.
- downgrade при попытке `budget_refused` и при стоящей паузе по времени
  отказывает с понятным текстом и ничего не меняет (схема та же, что до
  него).

**Имена**
- Заводятся: имена полей и CHECK выше, ревизия `0026`.
- Существуют: `SemanticAttemptOutcome`, `SemanticWorkerState`, ревизия
  `0025` (`2026_11_10_0025-catalog_simplify.py`).

**Проверка**
- `uv run --locked pytest tests/integration/test_provider_budget_schema.py -m migration_roundtrip`
  — зелёная; `uv run --locked alembic check` на мигрированной базе — без
  расхождений.

### Task 3: опросчик — отказ, пауза, остановка, статус

**Files**
- Edit: `backend/services/semantic_worker.py`, `backend/crud/semantic_queue.py`
- Test: `backend/tests/integration/test_provider_budget_pause.py`

**Interfaces**
- Потребляет: `ProviderBudgetError`, `BudgetAction` (Task 1); поля и исход
  (Task 2); `record_failure`, `_apply_fuse`, `_fill_from_responses`,
  `_lock_worker_state`, `claim_next`, `resume_worker`, `QueueStatus`,
  `PausedInfo` (существуют).
- Производит:

```python
PAUSE_REASON_PROVIDER_CREDITS = "provider_credits"
PAUSE_REASON_PROVIDER_BUDGET_NO_RESET = "provider_budget_no_reset"
PAUSE_REASON_PROVIDER_REFUSAL_UNKNOWN = "provider_refusal_unknown"
BUDGET_PAUSE_FLOOR = timedelta(seconds=60)

def _stop_claim(state: SemanticWorkerState, *, reason: str, attempt_id: int, now: datetime) -> None
def _set_budget_pause(state: SemanticWorkerState, *, until: datetime, attempt_id: int,
                      source: str | None, limit_usd: Decimal | None, spend_usd: Decimal | None) -> None

class BudgetPauseInfo(TypedDict):
    until: str
    source: str | None
    limit_usd: str | None
    spend_usd: str | None
    attempt_id: int
# QueueStatus.budget_pause: BudgetPauseInfo | None
```

**Утверждения**
- Обычное задание, отказ → задание `pending`, `claim_token` пуст,
  `attempts_in_generation` как до захвата, `next_attempt_at = now`; попытка
  `budget_refused`, стоимость 0 (проверка 4).
- Открытие семей: две порции по $0,0010 и $0,0020, третья — 403 → стоимость
  попытки $0,0030, `spent_last_24h` вырос на неё (проверка 5); у одной из
  двух стоимость неизвестна → `NULL` (проверка 6).
- Автоделение: отказ на второй группе → задание `pending`, стоимость первой
  группы в попытке (проверка 7).
- Захват перехвачен → `lost_claim`, пауза стоит (проверка 8).
- Стоимость выполненных порций больше резерва → `reserve_exceeded` у
  попытки, остановка `reserve_exceeded`, паузы по времени нет, после
  `resets_at` захват не идёт; то же при `lost_claim` (проверка 8а).
- Пауза до T: захват в `T − 1 с` — `None`, задание нетронуто; в `T` — берёт
  задание, пять полей пусты (проверка 9). `resets_at` на 10 минут в прошлом
  → срок `now + 60 с` (проверка 10). Более ранний второй срок не сокращает
  первый, более поздний продлевает (проверка 11).
- Остановка при стоящей паузе снимает паузу; пауза при стоящей остановке не
  пишется (проверка 12). «Снять остановку» после `provider_credits` — захват
  идёт (проверка 13).
- Три задания `pending`, первое получает 403 с `resets_at` → два других не
  тронуты (`attempts_in_generation` 0, попыток нет); после срока
  выполняются все три (проверка 14).
- Статус: `budget_pause` со сроком и суммами строками при паузе, `null` после
  срока (проверка 15); `claim_paused.reason` — новая причина.

**Имена**
- Заводятся: константы, `_stop_claim`, `_set_budget_pause`,
  `BUDGET_PAUSE_FLOOR`, `BudgetPauseInfo`, поле `QueueStatus.budget_pause`.
- Существуют, проверено `grep`-ом: `record_failure`, `_apply_fuse`,
  `_fill_from_responses`, `_owns_job`, `_lock_worker_state`, `claim_next`,
  `PAUSE_REASON_RESERVE_EXCEEDED`, `spent_last_24h`, `queue_status`,
  `PausedInfo`, `resume_worker`.

**Проверка**
- `uv run --locked pytest tests/integration/test_provider_budget_pause.py`
  — зелёная.
- Затронутые наборы, один раз в конце: `uv run --locked pytest -n 4
  tests/unit/test_semantic_queue_client.py tests/integration/test_semantic_queue_worker.py
  tests/integration/test_catalog_discovery_worker.py tests/integration/test_work_variants_reconcile.py
  tests/integration/test_semantic_queue_api.py tests/integration/test_work_variants_api.py
  tests/integration/test_semantic_queue_schema.py` — ДО 916, ПОСЛЕ 916 плюс
  новые тесты Task 1, все зелёные.
- Снятия (оркестратор): задание снова в `error` при отказе (проверка 4, 14);
  стоимость выполненных порций → 0 (проверка 5); проверка паузы в
  `claim_next` убрана (проверка 9); предохранитель на пути отказа убран
  (проверка 8а); 402 снова постоянная ошибка (проверка 1, Task 1).

### Task 4: причина ожидания схемы и экран

**Files**
- Edit: `backend/services/semantic_reconcile.py`, `backend/crud/work_variants.py`
- Edit: `frontend/src/types/domain.ts`,
  `frontend/src/pages/families/SuggestionsHeader.tsx`, `ContextCard.tsx`,
  `FamiliesTab.tsx`, `SchemaBlock.tsx`
- Test: `backend/tests/integration/test_provider_budget_pause.py` (класс
  `TestBuildWait`), `SuggestionsHeader.test.tsx`, `ContextCard.test.tsx`,
  `FamiliesTab.test.tsx`, `SchemaBlock.test.tsx`

**Interfaces**
- Потребляет: `QueueStatus.budget_pause` (Task 3), `_open_suggestion_state`,
  `schema_ready_to_build`, `useUnits`, `EntityCombobox`, `EntitySelect`
  (существуют).
- Производит:

```python
@dataclass(frozen=True)
class SuggestionHold:
    batch_id: int
    source: str
    import_job_id: int | None

@dataclass(frozen=True)
class SuggestionHolds:
    mass: tuple[SuggestionHold, ...]
    units: frozenset[int | None]
    by_unit: Mapping[int | None, tuple[SuggestionHold, ...]]
    live_jobs_by_unit: Mapping[int | None, int]

def open_suggestion_holds(db: Session) -> SuggestionHolds
def schema_build_wait(db: Session, family_id: int) -> BuildWait | None
# BuildWait: {"held_batches": [SuggestionHold как dict], "live_jobs": int}
```

```ts
interface BudgetPause { until: string; source: string | null; limit_usd: string | null; spend_usd: string | null; attempt_id: number }
// QueueStatus.budget_pause: BudgetPause | null
// FamilySchema.build_wait: { held_batches: { batch_id: number; source: string; import_job_id: number | null }[]; live_jobs: number } | null
```

**Утверждения**
- `_open_suggestion_state` на тех же данных отвечает как до правки (её
  существующие тесты в `test_work_variants_reconcile.py` и
  `test_catalog_discovery_worker.py` зелёные без правок).
- `build_wait`: пачка с отпечатками предложений этой единицы → номер, источник
  и `job`; `mass` с отпечатками предложений → в каждой единице; только
  задания → `live_jobs` (проверка 19). Пачки только из `family_schema` или
  `context_values` (в том числе `mass`), предложения другой единицы,
  смешанная пачка с предложениями другой единицы и значениями этой →
  `null` (проверка 19а). `ready_to_build` равно `build_wait is None` на
  каждом из этих входов.
- Плашки §2.5: 403-бюджет с суммами («Лимит OpenRouter исчерпан ($5,00 из
  $5,00). Продолжим в 03:00.») и без них; «бюджет на лету» с секундами;
  завтрашний срок — с датой; каждая причина остановки — свой текст, кнопка
  «Снять остановку» у всех; неизвестная причина — общий текст (проверка 16).
- Карточка контекста в единице м³: в списке «Назначить семью» нет семей в
  м² и без единицы, две одноимённые семьи в м³ и м² не стоят рядом, подпись
  «… · м³»; контекст без единицы — только семьи без единицы, подпись
  «… · без единицы»; пустой список — «Нет активных семей в единице «м³»»
  (проверка 17).
- «Новая семья работ»: выбор «м³» отправляет `unit_name: "M3"`, «Без
  единицы» — `null` (проверка 18).
- Метка схемы: «схема ждёт удержанную пачку №8 (импорт, job 46) —
  поставьте или отбросьте её на вкладке «Предложения»»; две пачки — номера
  через запятую; только задания — «схема ждёт 3 заданий предложений в этой
  единице» (проверка 19).

**Имена**
- Заводятся: `SuggestionHold`, `SuggestionHolds`, `open_suggestion_holds`,
  `schema_build_wait`, `BuildWait`, поле `SchemaOut.build_wait` и `FamilySchema.build_wait`, тип `BudgetPause`.
- Существуют, проверено `grep`-ом: `_open_suggestion_state`,
  `schema_ready_to_build`, `useUnits`, `useWorkFamilies`, `EntityCombobox`,
  `EntitySelect`, `SuggestionsHeader`, `SchemaBlock`, `CreateFamilyDialog`
  (в `FamiliesTab.tsx`).

**Проверка**
- `uv run --locked pytest tests/integration/test_provider_budget_pause.py -k BuildWait`
  и затронутые наборы Task 3 — зелёные.
- Из `C:\…\frontend`: `npx vitest run src/pages/families/SuggestionsHeader.test.tsx
  src/pages/families/ContextCard.test.tsx src/pages/families/FamiliesTab.test.tsx
  src/pages/families/SchemaBlock.test.tsx` — ДО 231 тест (16 + 122 + 42 + 51),
  ПОСЛЕ — плюс новые; `npx tsc -b` и `npx eslint .` — чисто.
- Снятия (оркестратор): отбор по виду отпечатков убран (проверка 19а);
  фильтр единицы в списке семей убран (проверка 17).

### Task 5: документы и стенд (оркестратор)

**Files**
- Edit: `docs/reference/schema.md` (`semantic_job_attempts.outcome`,
  `semantic_worker_state`), `docs/reference/screens.md` (шапка
  «Предложения», карточка контекста, «Новая семья работ», «Схема и
  варианты»), `docs/TECH_DEBT.md` (резерв автоделения учитывает префикс
  один раз — §2.2), `backend/.env.example` (строка к
  `SEMANTIC_DAILY_BUDGET_USD`).
- Create: `docs/devlog/2026-11-15-provider-budget-pause.md`.

**Утверждения**
- Справочник описывает новые поля, исход и плашки словами экрана;
  `check_agents_index.py` — 18 из 18.
- Долг резерва автоделения записан с контрпримером Codex и замером 39
  попыток.
- Стенд: миграция 0026 на `gca_dev`, бэкенд перезапущен; снимки — две
  «подбетонки» в списке карточки контекста различимы, «Новая семья работ» с
  выбором единицы, метка схемы с номером пачки, если пачка №8 ещё удержана.

**Проверка**
- `uv run --locked python scripts/check_agents_index.py` — 18 из 18; снимки
  стенда приложены к devlog.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: GitHub Actions на PR (`gh pr checks 83`) — решение
  пользователя 11.10.2026 не гонять `just ci` локально; стенд — Task 5.
