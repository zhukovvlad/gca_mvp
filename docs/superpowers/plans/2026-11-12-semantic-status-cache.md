# План: кэш сводки очереди на сервере

**Спека:** `docs/superpowers/specs/2026-11-12-semantic-status-cache-design.md` (гейт 2 одобрен 12.11.2026 после второго круга Codex, редакция 2, `b9a12af`)
**Дизайн:** `docs/superpowers/design/2026-11-12-semantic-status-cache.md` (редакция 3)
**Ветка:** `perf/semantic-status-cache`, черновой PR #76
**Статус:** редакция 2, 12.11.2026. **Гейт 3 одобрен пользователем
12.11.2026** после второго круга ревью Codex в PR #76 (на `f56fde1` замечаний
нет). Круг 1 Codex (на `00abe16`):
С5 снимается только при собственном механизме у ожидающего, иначе сценарий
числится за С4 (`REATTRIBUTED`); сценарий ошибки после срока сделан
различающим — третий вызов во время удержанного второго пересчёта ждёт, и
С10 краснеет на этом.

> Исполнителю: задачи по порядку; Task 1 — цикл TDD
> (`superpowers:test-driven-development`: тесты первыми, красный прогон
> показать). Адреса `§N` без уточнения — разделы спеки, «проверка N» — её §3;
> «решение N» — решения этого плана. Защит, дублирующих правило, не класть.
> ДО сняты 12.11.2026 на `b9a12af`: шаблон `-k "status_cache or StatusCache"`
> выбирает **0** тестов из 12 750 в `tests/unit` и `tests/integration`.

## Режим реализации (ускоренный, как у упрощения каталога)

- **Исполнитель** Task 1 — Sonnet; оркестратор (Opus) принимает, коммитит,
  ведёт `progress.md`. Task 2 делает оркестратор сам.
- **Ревью — только у Task 1** (свойства конкурентности и сброса): Opus с
  узким брифом — правила §2.3 и §2.4 и «что сделало бы этот тест
  ложно-зелёным».
- **Снятия защит делает оркестратор лично** — проверки 2–6, таблица ниже, в
  копии дерева, по протоколу `BASELINE_GREEN / MUTATION_APPLIED /
  EXPECTED_TEST_FAILURE / RESTORED`, итог — в `progress.md` и devlog. Это
  отступление от `docs/process/implementation.md` («Распределение
  ответственности»: снятия — ревьюером задачи) по решению пользователя
  28.10.2026, сохранённому 10.11.2026; процессный документ не меняется.
- **Прогоны.** Исполнитель в итерациях гоняет только тесты задачи; затронутые
  наборы (897 тестов, команда в Task 1) — один раз в конце задачи; ревьюер их
  не повторяет. Полный бэкенд и `just ci` локально не гоняются — GitHub
  Actions на PR (`gh pr checks 76`).
- **Без остановок** между задачами; стоп — пуш и внешнее ревью.

## Global Constraints

- AGENTS §3: один процесс uvicorn — кэш в памяти процесса опирается на это;
  зависимость названа в докстринге модуля. Ни брокеров, ни внешнего кэша.
- Ответ `GET /api/v1/semantic/status` не меняется ни ключом, ни типом
  (спека §2.1); право — по-прежнему `admin`.
- `queue_status(db)` без `heavy` отвечает так же, как до фичи: все
  действующие тесты зелёные **без правок**.
- Деньги в ответе — строками, как сейчас (`money_str`); кэш их не хранит —
  дешёвая часть считается всегда.
- Фронт, `docs/reference/`, `AGENTS.md` не меняются (спека §5).

## Структура файлов

- Create: `backend/services/status_cache.py` — `StatusCache`, `status_cache`.
- Edit: `backend/crud/semantic_queue.py` — `HeavyStatusParts`,
  `heavy_status_parts`, параметр `heavy` у `queue_status`.
- Edit: `backend/routers/semantic.py` — `queue_status_route` через кэш.
- Edit: `backend/main.py` — прослойка сброса.
- Edit: `backend/services/import_pipeline.py` — сброс в `run_import_job`.
- Edit: `backend/config.py`, `backend/.env.example` —
  `SEMANTIC_STATUS_CACHE_TTL_S`.
- Edit: `backend/tests/conftest.py` — автофикстура «кэш выключен».
- Create: `backend/tests/unit/test_status_cache.py`,
  `backend/tests/integration/test_status_cache_api.py`.
- Edit (новые классы): `backend/tests/integration/test_analog_releasable.py`,
  `backend/tests/integration/test_import_pipeline.py`.
- Edit: `docs/TECH_DEBT.md` №57 (зависимость от одного процесса).
- Create: `docs/devlog/2026-11-12-semantic-status-cache.md`.

## Решения плана, которых нет в спеке

1. **Две задачи:** весь код и тесты — Task 1 (правила кэша не проверить без
   точек сброса и наоборот); стенд и devlog — Task 2.
2. **Размещение тестов и шаблон.** Правила кэша — `tests/unit/test_status_cache.py`;
   срок и сброс запросом — `tests/integration/test_status_cache_api.py`;
   сценарий «принять всё» — класс `TestStatusCacheAfterPartialAcceptAll` в
   `test_analog_releasable.py` (помощники `_deferred` там); импорт — класс
   `TestStatusCacheResetOnImport` в `test_import_pipeline.py` (фикстура
   `job_env` там). Шаблон `-k "status_cache or StatusCache"`: `-k` не
   различает регистр, но подчёркивание — различает, поэтому два слова.
3. **Часы — открытый атрибут `clock` экземпляра**, а не только аргумент
   конструктора: интеграционные тесты подменяют его у общего `status_cache`
   (`monkeypatch.setattr`), не пересоздавая экземпляр.
4. **Проверка 3 читает кэш напрямую, а не через `GET /status`.** Тест импорта
   работает на закоммиченных сессиях (`job_env`), `admin_client` — на
   транзакционной; смешивать их — рисковать блокировками. Дефект живёт в
   `finally` `run_import_job`, и счётчик вызовов `compute` у
   `status_cache.get` наблюдает его на том же уровне. Это сужение формулировки
   спеки («следующий `GET /status`»), а не смена утверждения.
5. **Исключение в обработчике для проверки 2** — подменённый
   `services.semantic_decisions.resume_worker`, выбрасывающий `RuntimeError`, на
   `POST /worker/resume` (маршрут зовёт его через модуль
   `semantic_decisions`, подмена действует); 4xx — `POST` с ошибкой схемы
   тела (422) на маршруте с телом: у `resume_worker` своего отказа нет
   (повторное снятие — не ошибка), а 422 прослойка видит так же, как любой
   другой ответ.
6. **Маршрут зовёт расчёт через модуль** —
   `crud_semantic_queue.heavy_status_parts(db)` в момент вызова, чтобы тест мог
   подменить функцию счётчиком.
7. **Строку пересчёта в журнал пишет `StatusCache`** (уровень `INFO`,
   длительность в секундах) — она нужна проверке 7.
8. **Проверка 8 на стенде.** «Перезапросить единицу…» ставит задания
   исполнителю, и он платно зовёт модель. Поэтому проверка 8 идёт буквально,
   только если на стенде есть устаревшая единица и пользователь согласен на
   расход; иначе — тем же свойством на бесплатном действии: решение в
   «Можно отпустить» уменьшает счётчик вкладки при первом же перечитывании
   шапки. Что из двух сделано — в devlog.

## Обязательные снятия (оркестратор, после приёмки Task 1)

| # | Проверка | Что снять | Что обязано покраснеть |
|---|---|---|---|
| С1 | 2 | `finally` в прослойке (сброс только при нормальном ответе) | исключение в обработчике и «принять всё» после частичного коммита |
| С2 | 2 | прослойку целиком | 2xx и 4xx |
| С3 | 3 | сброс в `run_import_job` | импорт при `done` и при `error` |
| С4 | 4 | проверку поколения при запоминании | пересчёт, пересекающий сброс, **и** ожидающий, пришедший после сброса (проснувшись, он перечитывает состояние и находит запомненный старый результат) |
| С5 | 4 | **только если в реализации есть свой механизм** у ожидающего — проверку поколения результата, переданного ему мимо общего состояния | ожидающий после сброса получает старый результат. Нет такого механизма (ожидающий лишь перечитывает общее состояние) — снятия нет, сценарий числится за С4, в `progress.md` — `REATTRIBUTED` с указанием места |
| С6 | 5 | ожидание при пустом кэше (каждый считает сам) | один `compute` на два вызова |
| С7 | 5 | выдачу истёкшего значения во время пересчёта | второй вызов без ожидания |
| С8 | 6 | снятие признака «идёт пересчёт» при исключении | ожидающий и третий вызов — **падением по таймауту, не зависанием** |
| С9 | 6 | пробуждение ожидающих при исключении | то же |
| С10 | 6 | стирание истёкшего значения при исключении | сценарий «ошибка после срока» (Task 1): третий вызов во время удержанного второго пересчёта возвращает старое значение сразу, а не ждёт — тест краснеет на утверждении «ждёт», не по таймауту |

## Задачи

### Task 1: кэш дорогих частей сводки и точки сброса — **ревью**

**Files**
- Create: `backend/services/status_cache.py`
- Edit: `backend/crud/semantic_queue.py`, `backend/routers/semantic.py`,
  `backend/main.py`, `backend/services/import_pipeline.py`,
  `backend/config.py`, `backend/.env.example`, `backend/tests/conftest.py`,
  `docs/TECH_DEBT.md`
- Test: `backend/tests/unit/test_status_cache.py`,
  `backend/tests/integration/test_status_cache_api.py`, новые классы в
  `backend/tests/integration/test_analog_releasable.py` и
  `backend/tests/integration/test_import_pipeline.py`

**Interfaces**
- Потребляет: `queue_status`, `_stale_scan`, `QueueStatus`, `StaleUnitInfo`,
  `ConfigStaleInfo` (`crud/semantic_queue.py`); `current_proposals`,
  `accept_all_proposals`, `decide_releasable` (`services/releasable.py`);
  `queue_status_route`, `accept_all_releasable_route` (`routers/semantic.py`);
  `resume_worker` (`services/semantic_decisions.py`); `run_import_job`
  (`services/import_pipeline.py`); `csrf_middleware`, `log_requests`
  (`main.py`); `settings` (`config.py`); фикстуры `admin_client`, `job_env`,
  `fake_parse`, `committing_session_factory` — все существуют.
- Производит:

```python
# config.py
SEMANTIC_STATUS_CACHE_TTL_S: int = Field(300, ge=0)  # 0 — кэш выключен

# crud/semantic_queue.py
class HeavyStatusParts(TypedDict):
    stale_units: list[StaleUnitInfo]
    config_stale: ConfigStaleInfo | None
    releasable_open: int

def heavy_status_parts(db: Session) -> HeavyStatusParts
def queue_status(db: Session, *, heavy: HeavyStatusParts | None = None) -> QueueStatus

# services/status_cache.py
T = TypeVar("T")

class StatusCache(Generic[T]):
    clock: Callable[[], float]
    def __init__(
        self,
        ttl_seconds: Callable[[], float],
        clock: Callable[[], float] = time.monotonic,
    ) -> None
    def get(self, compute: Callable[[], T]) -> T
    def invalidate(self) -> None

status_cache: StatusCache[HeavyStatusParts]  # ttl — settings.SEMANTIC_STATUS_CACHE_TTL_S

# main.py
@app.middleware("http")
async def invalidate_status_cache_on_write(request: Request, call_next)
```

**Утверждения**
- Правила `StatusCache` — ровно §2.3, пункты 1–6; при `ttl = 0` каждый `get`
  вызывает `compute`, ничего не запоминает и не берёт замка.
- Проверка 1: второй `GET /status` в пределах срока не вызывает
  `heavy_status_parts`, а остановка захвата, поставленная через crud между
  вызовами, видна во втором ответе; после сдвига `clock` на срок — пересчёт.
- Проверка 2: после `POST` с ответом 2xx, с ответом 4xx и с исключением в
  обработчике следующий `GET /status` вызывает `heavy_status_parts`; после
  `GET` другого маршрута (например, `…/jobs?status=error`) — не вызывает.
- Проверка 2, сценарий ревью: две семьи с действующими предложениями;
  `decide_releasable` подменён так, что на второй семье выбрасывает
  `RuntimeError`; `POST …/releasable-proposals/accept-all` падает; следующий
  `GET /status` показывает `releasable_open`, уменьшенный ровно на число
  предложений первой семьи.
- Проверка 3: `compute`, вызванный через `status_cache.get` из подставленного
  `parse` во время импорта, запоминается; после `run_import_job` следующий
  `get` вызывает `compute` снова — и когда задание кончилось `done`, и когда
  `error` (решение 4).
- Проверки 4–6 — модульные, потоки и `threading.Event`, каждое ожидание с
  таймаутом; сценарии — §3 спеки; в 5 второй вызов при истёкшем значении
  возвращается, пока первый `compute` ещё держится на событии.
- Проверка 4, ожидающий после сброса: первый `compute` держится на событии;
  `invalidate()`; второй вызов начинается и ждёт; первый `compute`
  завершается — второй вызов не возвращает его результат, а вызывает
  `compute` сам (счётчик вызовов — 2, значение — второго пересчёта).
- Проверка 6, ошибка после срока — так, чтобы стирание было наблюдаемо
  (одиночный следующий `get` пересчитал бы и без стирания): запомнено
  значение A; срок истёк; первый пересчёт выбрасывает исключение; второй
  пересчёт запущен и держится на событии; третий вызов в это время **ждёт**
  (не возвращается, пока второй пересчёт не отпущен) и получает его
  результат B, а не A. Без стирания третий вызов вернул бы A сразу по правилу
  выдачи истёкшего во время пересчёта.
- Отрицательный случай срока: при `ttl = 0` два подряд `GET /status`
  вызывают `heavy_status_parts` дважды (автофикстура действующих тестов
  опирается на это).
- Ответ `GET /status` при включённом кэше совпадает по ключам с ответом при
  выключенном на одних данных.
- Автофикстура ставит срок 0 и сбрасывает `status_cache` до и после каждого
  теста; 897 тестов затронутых наборов зелёные без правок.
- `docs/TECH_DEBT.md` №57, «Чем закрыто сейчас»: кэш держится на одном
  процессе uvicorn (AGENTS §3).

**Имена**
- Заводятся этой задачей: `SEMANTIC_STATUS_CACHE_TTL_S`, `HeavyStatusParts`,
  `heavy_status_parts`, `StatusCache`, `status_cache`,
  `invalidate_status_cache_on_write`, классы `TestStatusCacheAfterPartialAcceptAll`,
  `TestStatusCacheResetOnImport`, файлы `test_status_cache.py`,
  `test_status_cache_api.py`, автофикстура в `tests/conftest.py`.
- Существуют, проверено `grep`-ом 12.11.2026: `queue_status`, `_stale_scan`,
  `QueueStatus`, `StaleUnitInfo`, `ConfigStaleInfo`, `current_proposals`,
  `accept_all_proposals`, `decide_releasable`, `ProposalView`,
  `queue_status_route`, `accept_all_releasable_route`, `resume_worker`,
  `run_import_job`, `csrf_middleware`, `log_requests`, `admin_client`,
  `job_env`, `fake_parse`, `committing_session_factory`,
  `crud_semantic_queue` (псевдоним модуля в `routers/semantic.py`).

**Проверка**
- `cd backend && TEST_DATABASE_URL=… uv run pytest tests/unit tests/integration -k "status_cache or StatusCache" -v`
  (то же — `just test-int-local-k` и `just test-unit-k` с этим шаблоном) —
  зелёная; выбирает **0** тестов ДО и не меньше **14** ПОСЛЕ (проверки 1–6
  со сценариями; число — нижняя граница).
- Затронутые наборы, один раз в конце задачи:
  `uv run pytest -n 4 tests/integration/test_semantic_queue_api.py tests/integration/test_analog_releasable.py tests/integration/test_catalog_discovery_systems.py tests/integration/test_analog_zone_job.py tests/integration/test_work_variants_api.py tests/integration/test_import_pipeline.py tests/integration/test_semantic_api.py`
  — **897** ДО, 897 + новые классы ПОСЛЕ, все зелёные.
- `uv run ruff check .` — чисто.

### Task 2: стенд и devlog

**Files**
- Create: `docs/devlog/2026-11-12-semantic-status-cache.md`

**Interfaces**
- Потребляет: стенд `gca_dev`, бэкенд :8260 (перезапуск — `Start-Process`,
  без `--reload`), фронт :5175, `backend/logs/app.log`, строка пересчёта
  (решение 7).
- Производит: devlog.

**Утверждения**
- Проверка 7: с открытой вкладкой «Предложения» и без действий за 10 минут в
  журнале не больше двух строк пересчёта; ответы `/status` между ними —
  меньше 1 с; замер до (из логов 12.11.2026: 16–30 с) и после — в devlog.
- Проверка 8 — по решению 8; что сделано и что увидено — в devlog.
- Devlog: что сделано, замер стенда, отступления (решения 4 и 8, ускоренный
  режим), итог снятий С1–С10, тронутые области и прочитанные файлы граблей
  (`docs/pitfalls/backend.md`, `docs/pitfalls/runs.md`) — AGENTS §10.

**Имена**
- Заводятся этой задачей: файл devlog.
- Существуют: всё из Task 1.

**Проверка**
- `just check-agents-index` — 18 из 18.

## Команды проверки

- По задаче: в самой задаче.
- По фиче целиком: GitHub Actions на PR #76 (`gh pr checks 76`); стенд —
  Task 2.
