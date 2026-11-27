# План: кэш подготовленного этапа

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Спека:** [`docs/superpowers/specs/2026-11-27-stage-cache-design.md`](../specs/2026-11-27-stage-cache-design.md)
**Дизайн:** [`docs/superpowers/design/2026-11-27-stage-cache.md`](../design/2026-11-27-stage-cache.md)
**Ветка:** `perf/stage-cache`, черновой PR #111

**Goal:** повторные открытия экрана этапа, разбора участника, панели строки и предпросмотра
файла, в том числе перезапросы после решения по строке, берут собранный этап из памяти
процесса и не показывают устаревших цифр.

**Architecture:** `services/stage_cache.py` держит кэш сцен по ключу с поколением и сроком
жизни, а также отслеживает записи событиями класса `Session`. После коммита записи вне
закрытого списка он сбрасывает сначала `typical_rates_cache`, затем кэш сцен. Сцену в
`crud/our_price.py` выдаёт одна точка входа: поколение берётся до первого чтения, на
попадании решения и файлы читаются заново. В грязной транзакции и точка входа, и
`typical_rates` идут мимо общих кэшей.

**Tech Stack:** FastAPI, SQLAlchemy 2.0.35, pytest. Фронта, миграций и новых маршрутов нет.

## Global Constraints

- Ответы API с кэшем и без поле в поле совпадают. «Без кэша» означает, что выключены оба
  кэша, сцен и обычных ставок (срок 0), — спека §7.
- Закрытый список таблиц дословно: `kp_line_agreements`, `kp_line_comments`,
  `kp_family_rungs`, `kp_line_targets`, `kp_sent_files` (спека §4.3).
- Порядок сброса на коммите: сначала `typical_rates_cache.invalidate()`, затем кэш сцен
  (спека §4.3; дизайн, «Правило сброса» п. 4).
- Поколение фиксируется до первого чтения любого входа сцены, включая `_load_head` и
  участников (дизайн, «Правило сброса» п. 3).
- Настройки дословно: `STAGE_SCENE_CACHE_TTL_S: int = Field(600, ge=0)`,
  `STAGE_SCENE_CACHE_SIZE: int = Field(8, ge=1)` (спека §6).
- Прежние сбросы в прослойке `main.py` и в `run_import_job` не трогаются.
- Один процесс uvicorn (AGENTS.md §3). Кэш живёт в памяти и между процессами не делится.
- Режим проверок — по риску (решение пользователя 28.10.2026). Критическое свойство —
  «устаревших цифр нет», задачи 2–4. Защиты в копии дерева снимает оркестратор лично,
  задачи 2–4 проходят узкое ревью (Opus), задача 1 — приёмку оркестратором. Итоговое
  ревью — Codex.
- Исполнитель гоняет только наборы из раздела «Проверка» своей задачи. Полный бэкенд и
  `just ci` локально не запускаются, их гоняет GitHub Actions.

## Review Focus

1. Участник или тендер удалён, пока его сцена лежит в кэше. Следующий запрос панели и
   разбора — 404, а не ответ из кэша — задача 3.
2. Смена ряда ИПЦ на экране: с рядом и без ряда — разные записи. Ответ с рядом после ответа
   без ряда совпадает с собранным без кэша — задача 3.
3. Фоновый разбор каталога коммитит из своего потока и своей сессии, пока панель открыта.
   Следующий запрос панели собирает этап заново — задача 2 (источник сброса) и задача 3
   (ответ).
4. Решение по строке упало отказом (`DomainError`) после записи в таблицу списка, транзакция
   откатилась. Кэш не сброшен, ответ совпадает с собранным без кэша — задача 2.
5. Кэш выключен (срок 0). Все существующие наборы задач 3 и 4 зелёные без правок ожиданий,
   отслеживание записей ничего не ломает — задачи 1 и 3.

## Структура файлов

- Create: `backend/services/stage_cache.py` — кэш сцен (задача 1), отслеживание записей
  (задача 2).
- Edit: `backend/config.py`, `backend/.env.example`, `backend/tests/conftest.py` (задача 1).
- Edit: `backend/services/context_routing.py` — пометка чтения в `_chapter_chain_rows`
  (задача 2).
- Edit: `backend/crud/our_price.py` — точка входа; `backend/routers/tenders.py` —
  `_write_decision`; `backend/crud/kp_sent_files.py` — `export_file`;
  `backend/services/analog_ladder.py` — докстрока `WorkHistory` (задача 3).
- Edit: `backend/crud/lump_sum.py` — `typical_rates` (задача 4).
- Create tests: `backend/tests/unit/test_stage_cache.py`,
  `backend/tests/integration/test_stage_cache_tracking.py`,
  `backend/tests/integration/test_stage_cache_api.py`,
  `backend/tests/integration/test_stage_cache_races.py`.
- Edit: скрипт замера в `docs/superpowers/design/2026-11-27-stage-cache/`, devlog
  `docs/devlog/2026-11-27-stage-cache.md` (финал оркестратора).

## Решения плана, которых нет в спеке

1. Четыре задачи и финал:
   - кэш как структура (1);
   - отслеживание записей (2);
   - точка входа и пометки (3);
   - гонки и грязная транзакция (4).

   Гонки выделены отдельно: ревьюер может принять точку входа и отклонить защиту от гонок.
   Замер на стенде, devlog и правка карты — финал оркестратора.
2. Кэш сцен — новый класс `SceneCache`, а не обобщение `StatusCache`. У `StatusCache` одно
   значение и объединение пересчётов, у кэша сцен ключ, вытеснение и нет объединения
   (спека §2). Общий у них только приём поколения.
3. API кэша — «взять поколение / прочитать / положить с поколением», без `get(compute)`:
   точке входа нужно поколение до первого чтения, а не в момент промаха.
4. Попадание в тестах считается шпионом над `crud.our_price._build_scene` (подмена
   `monkeypatch` с подсчётом вызовов). Счётчика в продуктовом коде нет.
5. Пометка CTE-чтения — опция выполнения `stage_cache_read=True` у `TextClause`. Её
   читает `do_orm_execute` через `orm_execute_state.execution_options` (опции оператора
   входят туда).
6. Признак «грязная» и пометка решений живут в `session.info` под закрытыми ключами
   модуля. Очищаются на коммите и откате внешней транзакции.
7. `typical_rates` берёт `is_dirty` из `services/stage_cache.py`. Модуль импортирует
   только `services/status_cache.py`, `config` и SQLAlchemy, циклов нет.

## Задачи

### Task 1: кэш сцен

**Files**
- Create: `backend/services/stage_cache.py`
- Edit: `backend/config.py`, `backend/.env.example`, `backend/tests/conftest.py`
- Test: `backend/tests/unit/test_stage_cache.py` (новый)

**Interfaces**
- Потребляет (существуют): `settings` (`config.py`), `Field`.
- Производит:

```python
# services/stage_cache.py
SceneKey = tuple[int, int, int | None, int | None]  # тендер, этап, оффер | весь этап, ряд | без ряда

class SceneCache(Generic[T]):
    clock: Callable[[], float]  # открытый атрибут: тесты подменяют часы

    def __init__(
        self,
        ttl_seconds: Callable[[], float],
        size: Callable[[], int],
        clock: Callable[[], float] = time.monotonic,
    ) -> None: ...
    def enabled(self) -> bool: ...                 # срок > 0
    def generation(self) -> int: ...
    def get(self, key: SceneKey) -> T | None: ...  # просроченная — None и удаляется; попадание обновляет место
    def put(self, key: SceneKey, value: T, generation: int) -> bool: ...  # False — поколение сменилось, не сохранено
    def invalidate(self) -> None: ...              # поколение + 1, все записи удалены

stage_scene_cache: SceneCache = SceneCache(
    lambda: settings.STAGE_SCENE_CACHE_TTL_S, lambda: settings.STAGE_SCENE_CACHE_SIZE
)

# config.py
STAGE_SCENE_CACHE_TTL_S: int = Field(600, ge=0)
STAGE_SCENE_CACHE_SIZE: int = Field(8, ge=1)
```

**Утверждения** (спека §2, §6)
- `put` с текущим поколением, затем `get` того же ключа — то же значение. Другой ключ,
  отличный одним элементом (оффер `None` против числа, ряд `None` против числа), — `None`.
- `put` с поколением, взятым до `invalidate()`, возвращает `False`, `get` — `None`.
- После `invalidate()` все ключи — `None`, `generation()` вырос на 1.
- Срок 600: при сдвиге часов на 599 с запись есть, на 600 с — нет. Срок отсчитывается от
  `put`, а не от `get`.
- Объём 8: девятый `put` вытесняет самую давно использованную. Если до девятого `put`
  прочитать первый ключ, вытесняется второй.
- Срок 0: `enabled()` — `False`, `put` ничего не сохраняет.
- Исключение не проходит через кэш: структура о нём не знает. Утверждение — в задаче 3.
- `.env.example` содержит обе настройки с комментарием «кэш в памяти процесса, верен только
  при одном процессе uvicorn (AGENTS.md §3)».
- Автофикстура `tests/conftest.py` ставит `STAGE_SCENE_CACHE_TTL_S` в 0 и вызывает
  `stage_scene_cache.invalidate()` до и после каждого теста, рядом с прежней
  `_status_cache_off`.

**Имена**
- Заводятся: `services/stage_cache.py`, `SceneKey`, `SceneCache`, `stage_scene_cache`,
  `STAGE_SCENE_CACHE_TTL_S`, `STAGE_SCENE_CACHE_SIZE`.
- Существуют, проверено `grep`-ом: `_status_cache_off` (`tests/conftest.py:769`),
  `TYPICAL_RATES_CACHE_TTL_S` (`config.py:92`).

**Проверка**
- `cd backend && uv run pytest tests/unit/test_stage_cache.py tests/unit/test_status_cache.py`.
  ДО — 0 + 16, ПОСЛЕ — 16 плюс новые тесты задачи. `uv run ruff check .`

- [ ] Тесты (красные) → реализация → зелёные → коммит.

### Task 2: отслеживание записей

**Files**
- Edit: `backend/services/stage_cache.py`, `backend/services/context_routing.py`
  (`_chapter_chain_rows`)
- Test: `backend/tests/integration/test_stage_cache_tracking.py` (новый)

**Interfaces**
- Потребляет: `stage_scene_cache` (задача 1). Существуют: `typical_rates_cache`
  (`services/status_cache.py`), `sqlalchemy.orm.Session`, `chapter_steps`.
- Производит:

```python
# services/stage_cache.py
DECISION_TABLES: frozenset[str]   # пять таблиц закрытого списка
READ_OPTION = "stage_cache_read"  # sa.text(...).execution_options(stage_cache_read=True)

def mark_decision_write(session: Session) -> None: ...  # пометка текущей транзакции
def is_dirty(session: Session) -> bool: ...             # в текущей транзакции записано вне DECISION_TABLES, включая неизвестное
# Подписки на события класса Session — при импорте модуля (спека §4.1).
```

**Утверждения** (спека §4, дизайн «Правило сброса» п. 1, 2, 4)

Каждое утверждение — на сессиях тестового движка `tests/conftest.py`, не `database.engine`.

- Коммит ORM-записи (`add`, правка атрибута, `delete`) в таблицу вне списка увеличивает
  `stage_scene_cache.generation()` и сбрасывает `typical_rates_cache`.
- Коммит `pg_insert` и `sa.delete` в таблицу списка без пометки сбрасывает. С пометкой не
  сбрасывает.
- Помеченная транзакция, записавшая таблицу списка и таблицу вне его, сбрасывает.
- `sa.text` с `SELECT`, в том числе после пробелов и комментария, не сбрасывает.
  `sa.text` с `UPDATE` сбрасывает. Непомеченный `sa.text` с `WITH … SELECT` сбрасывает,
  помеченный `stage_cache_read` — нет.
- `Select` с `FOR UPDATE` и `FOR KEY SHARE` не запись.
- Порядок: в момент сброса кэша сцен `typical_rates_cache` уже сброшен. Проверяется
  подменой `stage_scene_cache.invalidate`, которая читает состояние кэша ставок.
- Сброс происходит после коммита: в момент вызова `invalidate()` запись видна другой
  сессии.
- Откат после записи вне списка не сбрасывает. После отката `is_dirty` — `False`, пометка
  снята, следующая транзакция чистая.
- `begin_nested` с записью вне списка и откатом точки сохранения, затем коммит внешней
  транзакции — сброс есть. Выход из `begin_nested` (`RELEASE SAVEPOINT`) без коммита
  внешней — сброса нет.
- Решение по строке, упавшее `DomainError` после записи в таблицу списка, откатывается и
  не сбрасывает (Review Focus 4).
- `is_dirty` — `True` после flush записи вне списка и после `sa.text` с записью;
  `False` после записи только в таблицу списка и после одних чтений.
- Коммит из другого потока со своей сессией сбрасывает тот же кэш: источник «поток
  обработчика» (Review Focus 3).
- `chapter_steps` с непустым путём разделов в помеченной транзакции, коммитящей
  `kp_sent_files`, не сбрасывает кэш. Без пометки чтения в `_chapter_chain_rows` —
  сбрасывает.

**Имена**
- Заводятся: `DECISION_TABLES`, `READ_OPTION`, `mark_decision_write`, `is_dirty`.
- Существуют, проверено `grep`-ом: `chapter_steps` (`services/context_routing.py:285`),
  `_chapter_chain_rows` (`services/context_routing.py:296`), `typical_rates_cache`
  (`services/status_cache.py:109`), модели пяти таблиц (`models.py`: `KpLineAgreement`,
  `KpLineComment`, `KpFamilyRung`, `KpSentFile`, `KpLineTarget`).

**Проверка**
- `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test"
  uv run pytest tests/integration/test_stage_cache_tracking.py tests/integration/test_context_routing.py`.
  ДО — 0 + 41, ПОСЛЕ — 41 плюс новые тесты задачи. `uv run ruff check .`

- [ ] Тесты (красные) → реализация → зелёные → коммит.

### Task 3: точка входа и пометки решений

**Files**
- Edit: `backend/crud/our_price.py` (`build_round_view`, `_offer_context`, новая
  `_scene_for`), `backend/routers/tenders.py` (`_write_decision`),
  `backend/crud/kp_sent_files.py` (`export_file`), `backend/services/analog_ladder.py`
  (докстрока `WorkHistory`)
- Test: `backend/tests/integration/test_stage_cache_api.py` (новый)

**Interfaces**
- Потребляет: `stage_scene_cache`, `SceneKey` (задача 1); `mark_decision_write`, `is_dirty`
  (задача 2). Существуют: `_load_head`, `_load_participants`, `_offer_raw`,
  `_build_scene`, `load_decisions`, `_sent_files_counts`, `_Scene`.
- Производит:

```python
# crud/our_price.py
def _scene_for(
    db: Session,
    tender_id: int,
    round_id: int,
    offer_id: int | None,
    *,
    inflation_series_id: int | None,
    require_line: int | None = None,
) -> _Scene: ...  # спека §3; при is_dirty(db) или выключенном кэше — холодный путь
```

**Утверждения** (спека §2, §3, §4.3; дизайн, критерии 2 и 3)

Все сравнения — с ответом, собранным при выключенных обоих кэшах.

- `build_round_view` и `_offer_context` берут сцену только через `_scene_for`. `grep`
  вызовов `_build_scene` в `crud/` находит один — в `_scene_for`.
- Второй подряд запрос разбора, этапа, панели и предпросмотра — попадание: шпион над
  `_build_scene` насчитал по одной сборке на ключ. Ответ совпадает с собранным без кэша.
- Разбор, затем этап: разные ключи, две сборки. Ответ этапа совпадает с собранным без
  кэша.
- Ряд ИПЦ и без ряда — разные ключи. Каждый ответ совпадает с собранным без кэша (Review
  Focus 2).
- Для каждой записи закрытого списка: PUT и DELETE согласовано, комментария, ступени, цели
  (обычной и справочной), а также `POST …/export`. После неё перезапросы разбора, этапа,
  панели и предпросмотра — попадания. Ответы совпадают с собранными без кэша, в том числе
  поля решения и число отправленных файлов.
- Выгрузка КП с сохранённой справочной целью и непустым путём разделов сохраняет
  прогретые сцены этапа и участника: следующий вызов — попадание (спека, критерий 8а).
- Запрос с экрана вне списка (назначение базы НДС сметы КП, отметка «не аналог») сбрасывает:
  следующий запрос — сборка, ответ совпадает с собранным без кэша.
- Фоновая задача импорта (`run_import_job`) после коммита сбрасывает: следующий запрос —
  сборка.
- Удаление участника этапа: следующий запрос его разбора и панели — 404, текст как без
  кэша (Review Focus 1).
- Попадание с `require_line`, которой нет в КП, — 404 с тем же текстом, что у холодного
  пути (спека, критерий 9).
- Отказ 422 приведения по ИПЦ уходит вызвавшему и не кэшируется: второй такой же запрос —
  снова сборка и снова 422.
- При сроке 0 каждый запрос — сборка, ответы прежние (Review Focus 5).

**Имена**
- Заводятся: `_scene_for`.
- Существуют, проверено `grep`-ом: `build_round_view` (`crud/our_price.py:642`),
  `_offer_context` (`crud/our_price.py:716`), `_offer_raw` (`crud/our_price.py:738`),
  `_load_head` (`crud/our_price.py:226`), `_build_scene` (`crud/our_price.py:400`),
  `_write_decision` (`routers/tenders.py:219`), `export_file`
  (`crud/kp_sent_files.py:122`), `load_decisions` (`crud/kp_decisions.py:137`),
  `run_import_job` (`services/import_pipeline.py`).

**Проверка**
- `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test"
  uv run pytest tests/integration/test_stage_cache_api.py`. ДО — 0, ПОСЛЕ — новые тесты
  задачи.
- Тот же префикс, наборы без правок ожиданий: `tests/integration/test_our_price_offer_api.py
  tests/integration/test_our_price_round_api.py tests/integration/test_kp_file_export.py
  tests/integration/test_kp_file_mutations.py tests/integration/test_kp_file_scene.py
  tests/integration/test_analog_targets.py tests/integration/test_ref_analogs_target.py`.
  ДО и ПОСЛЕ — 149 + 73 + 104 + 152 + 31 + 67 + 134 = 710, гоняется один раз в конце
  задачи. `uv run ruff check .`

- [ ] Тесты (красные) → реализация → зелёные → коммит.

### Task 4: гонки и грязная транзакция

**Files**
- Edit: `backend/crud/lump_sum.py` (`typical_rates`); при необходимости
  `backend/crud/our_price.py` (`_scene_for`) — если тест гонки покажет, что поколение
  берётся поздно
- Test: `backend/tests/integration/test_stage_cache_races.py` (новый)

**Interfaces**
- Потребляет: `_scene_for` (задача 3); `is_dirty` (задача 2); `stage_scene_cache`
  (задача 1). Существуют: `typical_rates`, `load_typical_rates`, `typical_rates_cache`.
- Производит: в `typical_rates(db)` при `is_dirty(db)` ставки считаются через
  `load_typical_rates(db)`, а `typical_rates_cache` не читается и не заполняется.
  Подпись прежняя.

**Утверждения** (дизайн, критерий 4; спека §5, критерии 6 и 6а)

Чередования задаются событиями (`threading.Event`) в подменах `_load_head`, `_build_scene`
и `stage_scene_cache.invalidate`. Пауза ставится **после** чтения, которое должно
устареть: событие перед запросом выравнивает момент отправки, а не снимок данных. Таймаут
ожидания — провал теста с понятным текстом, а не подавленное исключение. Иначе сломанное
предусловие выглядит как продуктовое падение.

- Сборка, во время которой другой поток закоммитил запись вне списка, не сохраняется:
  следующий запрос — сборка.
- Пауза после `_load_head`, до `_build_scene`. Другой поток меняет площадь объекта тендера
  и коммитит. Следующий ответ совпадает с собранным без кэша, по новой площади.
- Коммит записи, меняющей обычные ставки (цена строки сметы договора), с сессией мимо
  прослойки `main.py`. Сразу после него собирается сцена, её признак «комплект» совпадает со
  сборкой без кэша. Перед коммитом кэш ставок прогрет.
- Пауза между сбросом ставок и сбросом сцены, в паузе начинается сборка. Её результат не
  сохраняется.
- Грязная транзакция: запись вне списка, затем `_scene_for` в той же сессии. Сцена не
  сохраняется и не берётся из кэша, хотя по ключу лежит прогретая. После отката следующий
  ответ совпадает с собранным без кэша.
- Кэш ставок прогрет до записи. Своя запись меняет ставки, сборка в той же транзакции даёт
  классификацию по своим данным, как без кэша.
- Кэш ставок пуст, сборка в грязной транзакции, откат. Кэш ставок остаётся пустым,
  следующая сборка совпадает с собранной без кэша.
- Транзакция, записавшая только таблицу списка (цель строки), не грязная: `_scene_for` в ней
  — попадание, решения прочитаны заново, с её незакоммиченной целью.

**Имена**
- Заводятся: тестовый файл; продуктовых имён нет.
- Существуют, проверено `grep`-ом: `typical_rates` (`crud/lump_sum.py:110`),
  `load_typical_rates` (`crud/lump_sum.py:46`), `typical_rates_cache`
  (`services/status_cache.py:109`).

**Проверка**
- `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test"
  uv run pytest tests/integration/test_stage_cache_races.py tests/integration/test_lump_sum_api.py
  tests/integration/test_lump_sum_load.py tests/unit/test_lump_sum_rule.py`. ДО — 0 + 20 + 57 + 34,
  ПОСЛЕ — 111 плюс новые тесты задачи. `uv run ruff check .`

- [ ] Тесты (красные) → реализация → зелёные → коммит.

## Снятия (оркестратор, копия дерева)

Каждое снятие обязано уронить названный тест (дизайн, критерий 5; спека, критерии 6, 6а, 8а):

| Снятие | Падает |
|---|---|
| пометка `mark_decision_write` в `_write_decision` | задача 3, перезапросы после решения — попадания |
| сброс на коммите (подписка `after_commit`) | задача 2, коммит ORM-записи; задача 3, запрос вне списка |
| проверка поколения в `put` | задача 1, `put` со старым поколением; задача 4, сборка с чужим коммитом |
| поколение после `_load_head` | задача 4, пауза после шапки |
| сброс ставок на коммите | задача 2, порядок; задача 4, ставки сразу после коммита |
| сброс ставок после сброса сцены | задача 2, порядок; задача 4, пауза между сбросами |
| `is_dirty` в `_scene_for` | задача 4, грязная транзакция |
| `is_dirty` в `typical_rates` | задача 4, оба теста кэша ставок |
| пометка чтения в `_chapter_chain_rows` | задача 2, `chapter_steps`; задача 3, выгрузка со справочной целью |

## Финал (оркестратор)

- Замер скриптом из `docs/superpowers/design/2026-11-27-stage-cache/`, расширенным на
  тёплый вызов и последовательность «записать цель → разбор, этап, панель, предпросмотр». На
  `gca_dev` пороги такие: тёплая панель ≤ 1,5 с, тёплый разбор ≤ 2,5 с, каждый перезапрос
  после цели ≤ 2,5 с. Ответы с кэшем и без совпадают целиком. Запись цели делается в копии
  `gca_dev`, не на самом стенде (данные стенда не меняются).
- Devlog `docs/devlog/2026-11-27-stage-cache.md`. Правка карты (`docs/product-roadmap.md`,
  п. «Скорость 4а и 4б»). Справочник и AGENTS.md не меняются: инвариантов фича не трогает,
  §3 об одном процессе уже есть.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: `just ci` в GitHub Actions; замер на стенде — в финале.
