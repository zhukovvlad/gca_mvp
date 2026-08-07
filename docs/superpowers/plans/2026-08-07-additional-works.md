# Ф4: допработы — расшивка «Сведений», `estimate_additional_works`, миграция 0007 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Снять двойной счёт допработ и дать их деньгам ось «статья»: агрегатная строка перестаёт быть позицией, а её сумма живёт расшитой по строкам «Сведений по дополнительным работам» — так, что сумма записей всегда равна контрольной сумме агрегатной строки, а результат сходится с независимым ИТОГО файла.

**Architecture:** Парсер `2.0.0` больше не кладёт агрегатную строку в `positions` — дубль не снимается удалением, а не возникает. Новый чистый сервис `services/additional_works.py` строит по разобранному JSON одного предложения полный план записей (разбор строк текста, правило единогласия по ссылке, матрица состояний) — без `Session`, без ORM и без записи в БД. `import_estimate` определяет владельца «Сведений» предпассом по всем лотам, вычисляет план резолва Ф3 **один раз**, проверяет гейт формы 1.1.0 до вставки позиций и после позиций материализует записи. Миграция 0007 добавляет таблицу с пятью CHECK, двумя явно именованными FK, `UNIQUE (proposal_id, ordinal)` и одним частичным индексом.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x sync ORM, Alembic, PostgreSQL 16, openpyxl, pytest, uv, just. Фронтенд не затрагивается.

**Спека:** [2026-08-07-additional-works-design.md](../specs/2026-08-07-additional-works-design.md) — источник правды. Замеры §1, решения §2, отвергнутые альтернативы §3, тесты §4, границы §5, DoD §6.

## Global Constraints

- Ветка: `feat/additional-works` (спека и правка рамки фазы уже закоммичены в неё двумя коммитами: `3da471a`, `ff68a1c`).
- ruff `line-length = 120`, target py312. Комментарии и docstring — по-русски, как в окружающем коде.
- Все команды бэкенда — из `backend/` через `uv run`; системный python не вызывать. Дочерний python пишет в cp1251 — префикс `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` для любого разового скрипта, и скрипт **файлом**, не через `python -c`.
- Точечный прогон integration: `TEST_DATABASE_URL` обязателен, `DATABASE_URL` снимать (`env -u DATABASE_URL`). Успех читать по числу `passed`, а не по коду возврата ([silent-test-runs.md](../../insights/silent-test-runs.md)).
- **Деньги — `Decimal` end-to-end, в JSON строки** (`AGENTS.md` §3). В новом коде ни одного `float`; `Decimal(str(value))`, сравнение Decimal с Decimal.
- **Ни одного второго предиката для уже выраженного понятия.** «Строка без номера и без раздела» уже выражена `RowKind.OUTSIDE_STRUCTURE` в резолвере Ф3 — гейт §2.7 обязан опираться на неё, а не заводить свою проверку пустоты A и B.
- **Ни одного SQL-join по номеру раздела.** Резолв ссылки идёт в памяти по плану Ф3 (спека §2.5).
- **Один вызов `resolve_proposal` на предложение.** Второй задвоил бы все предупреждения Ф3 (спека §1.5 факт 3).
- **Новые имена индексов НЕ добавлять в `RAW_SQL_INDEXES`** (`backend/alembic/env.py`): частичный индекс, `UNIQUE` и оба FK декларативные и обязаны быть видны `alembic check`.
- Перед пушем — `just ci` целиком, и **до** пуша. Круговой рейс миграции — рецепт `db-test-migrate` (`db-test-upgrade` не существует).
- Реальные суммы и реквизиты контрагентов — никуда: ни в код, ни в тесты, ни в доки, ни в сообщения коммитов. Суммы, которые уедут в fixture, **синтетические и круглые**.
- **Замеренные числа не подгоняются.** Поехавший baseline — сигнал дефекта, разбираться до правки.

## Состояние на момент написания плана (замерено, не пересказано)

- `head` миграций — `0006` (`backend/alembic/versions/2026_08_06_0006-category_resolution.py`). Новая — `0007`, `down_revision = "0006"`.
- База отсчёта тестов: **1029 собранных, 1023 passed / 6 skipped** backend на `main` (`2c83ded`), 175 vitest.
- `PARSER_VERSION = "1.1.0"` — [estimate.py:51](../../../backend/parser/estimate.py#L51); комментарий 40–50 объясняет минорность и предсказывает мажор для Ф4. Ни одна строка кода версию не читает (только запись в `estimate_raw_data` и тест `test_parser_version_is_bumped_for_the_new_key`, `tests/unit/parser/test_estimate.py:455`).
- Место исключения строки — [get_lot_positions.py:157-160](../../../backend/parser/get_lot_positions.py#L157): `item_index` инкрементируется только вместе с записью в словарь, поэтому `continue` сохраняет непрерывность `1..N` **по построению**.
- `LotRows.additional_works` docstring (строки 57–62) описывает переходное решение Ф2 — правится вместе с кодом.
- `_import_positions` ([estimate_import.py:627](../../../backend/services/estimate_import.py#L627)) сам зовёт `resolve_proposal` (строка 654) и сразу делает `warnings.extend(resolution.warnings)` (659). В Task 5 план резолва поднимается в `import_estimate`; **публичная сигнатура `import_estimate` не меняется**, поэтому три тестовых вызова (`test_estimate_import.py`, `test_matching.py`, `test_review_concurrency.py`) не правятся.
- Порядок внутри цикла по лотам: `_import_additional_info` (438) → `_import_summary` (439) → `_import_positions` (441).
- `RowKind` = `CHAPTER | POSITION | OUTSIDE_STRUCTURE` ([category_resolution.py:55-58](../../../backend/services/category_resolution.py#L55)); `RowResolution(position_key, kind, parent_position_key, smr_article_raw, work_category_id, category_source)`; `ProposalResolution(rows, warnings, structure_disabled, counters)`.
- `MAX_WARNING_EXAMPLES = 5` и хелпер `_examples()` — [category_resolution.py:43](../../../backend/services/category_resolution.py#L43), 199–202. Новый модуль **импортирует** их, а не заводит свои.
- `tests/payloads.py`: `proposal(...)` **не создаёт ключ `additional_works` вовсе** (строки 144–154), `additional_info` — параметр с дефолтом из двух ключей. Значит у всех существующих payload'ов состояние «строки нет, „Сведений“ нет», и ни одно ожидание не меняется.
- `rejected(session, contains=...)` (`tests/integration/test_schema_constraints.py:39`) ждёт `IntegrityError` внутри savepoint — годится для всех пяти CHECK и обоих FK.
- `models.py`: `Numeric`, `Integer`, `Text`, `BigInteger`, `CheckConstraint`, `UniqueConstraint`, `Index`, `ForeignKey` уже импортированы. `WORK_CATEGORY_TITLE_BLANK_CHARS` — [models.py:786](../../../backend/models.py#L786), используется один раз (строка 819); в тестах не встречается.
- `tests/integration/test_import_fixture_e2e.py` несёт только счётчики: `FIXTURE_POSITIONS = 2576`, `FIXTURE_CHAPTERS = 746`, `222 / 485 / 39 / 38`, `FIXTURE_CHAPTERS_TOP_LEVEL = 16`. **Денежных ожиданий в файле нет** (`total_cost`, `Decimal`, `sum(`, `amount` не встречаются) — правка ИТОГО в fixture ничего не ломает.
- `tests/unit/parser/test_estimate.py`: `EXPECTED_POSITIONS = 2576`, `EXPECTED_PRICED_ROWS = 1828`, `test_additional_info_parsed` требует `len(additional) == 6` (ключ «Сведения…» уже есть и входит в шесть — правка **значения** его не добавляет).

### Структура fixture (замер, нужен для Task 6)

`fixtures/gp_estimate_fixture.xlsx`, один лист, `max_row = 2600`, `max_column = 24`.

| Что | Где |
|---|---|
| строка колоночных заголовков | 9 |
| маркер лота «Лот №» | 11 |
| зона позиций | 11 … **2586** |
| **первая merged-в-колонке-A строка после лота** (конец зоны) | **2587** — строка ИТОГО, merged 1–5 |
| вторая строка итогов («В том числе НДС», значение пустое) | 2588, merged 1–5 |
| маркер «Дополнительная информация» | 2590, merged 1–6 |
| ключ «Сведения по дополнительным работам» | 2591, **колонка 2** (merged 2–8); **значение — колонка 10** (merged 10–20) |
| полностью пустых строк внутри зоны позиций | **0** — занять готовую строку нельзя, нужна вставка |
| последняя строка-позиция (2586) | заполнены 1, 4, 7, 8, 10–20; числовые 8, 10–19 |
| строка ИТОГО (2587) | заполнены 1, 15, 16, 17, 18 |
| merged-диапазонов на листе | **2613** (по диапазону на строку, напр. 4–5 на каждой позиции) |
| merged-диапазонов с `min_row >= 2587` | ~23 (итоги + блок допинформации, по два на строку) |
| **формул** | **0** — round-trip через openpyxl не убьёт кэшированные значения |
| data validation / условное форматирование / диаграммы / картинки / defined names | нет ни одного |

Ноль формул — ключевой факт: `load_workbook(data_only=True)` + `save()` затёр бы формулы кэшем, а здесь затирать нечего.

## File Structure

- `backend/alembic/versions/2026_08_07_0007-additional_works.py` — **создаётся** (Task 1).
- `backend/models.py` — **правится** (Task 1): класс `EstimateAdditionalWork`, переиспользование константы пробельных символов.
- `backend/parser/get_lot_positions.py` — **правится** (Task 2): `continue`, docstring `LotRows`.
- `backend/parser/estimate.py` — **правится** (Task 2): `PARSER_VERSION = "2.0.0"` и комментарий-история.
- `backend/services/additional_works.py` — **создаётся** (Tasks 3–4).
- `backend/services/estimate_import.py` — **правится** (Task 5): предпасс владельца, подъём плана резолва, гейт, материализация записей.
- `backend/tests/payloads.py` — **правится** (Task 5): ключ `additional_works` и хелпер строки.
- `backend/tests/conftest.py` — **правится** (Task 1): новая таблица в `_DOMAIN_TABLES`.
- `backend/tests/integration/test_schema_constraints.py` — **правится** (Task 1).
- `backend/tests/unit/parser/test_get_lot_positions.py`, `test_estimate.py` — **правятся** (Task 2).
- `backend/tests/unit/test_additional_works.py` — **создаётся** (Tasks 3–4).
- `backend/tests/integration/test_estimate_import.py` — **правится** (Task 5).
- `backend/tests/integration/test_import_fixture_e2e.py` — **правится** (Task 6).
- `fixtures/gp_estimate_fixture.xlsx` — **правится** (Task 6) скриптом, который в репозиторий не коммитится.
- `docs/devlog/2026-08-07-additional-works.md` — **создаётся** (Task 7).

---

### Task 1: миграция 0007, ORM и тесты схемы

**Files:**
- Create: `backend/alembic/versions/2026_08_07_0007-additional_works.py`
- Modify: `backend/models.py` (новый класс; константа пробельных символов), `backend/tests/conftest.py` (`_DOMAIN_TABLES`)
- Test: `backend/tests/integration/test_schema_constraints.py`

**Interfaces:**
- Consumes: `proposals.id`, `work_categories.id`.
- Produces: таблица `estimate_additional_works`; `ck_estimate_additional_works_total_amount`, `_ordinal`, `_title_not_blank`, `_unresolved_ref`, `_raw_line_pairs`; `uq_estimate_additional_works_proposal_ordinal`; `fk_estimate_additional_works_proposal_id` (CASCADE), `fk_estimate_additional_works_work_category_id` (RESTRICT); `idx_estimate_additional_works_work_category_id`.

- [ ] **Step 1: Написать падающие тесты схемы**

Новый класс в `test_schema_constraints.py`. Конвенции файла: сессия — фикстура `db_session`, фабрики — `factories.XxxFactory.create(...)`, `ProposalFactory.create()` сам поднимает цепочку lot → estimate → contract. Хелпер `_any_category_id` **берёт лист дерева, а не корень** — урок Ф3: у корня есть дети, и его удаление упрётся в `work_categories_parent_id_fkey` раньше, чем в проверяемый FK.

Тесты (каждый — со своим `contains=` по **имени констрейнта**, иначе тест зеленеет от чужого отказа):

| Тест | Что подаёт | Ждёт |
|---|---|---|
| `test_negative_amount_is_rejected` | `total_amount = -1` | `ck_..._total_amount` |
| `test_zero_and_negative_ordinal_are_rejected` (параметризован `0`, `-1`) | `ordinal` | `ck_..._ordinal` |
| `test_blank_title_is_rejected` (параметризован `""`, `"   "`, `" "`) | `title` | `ck_..._title_not_blank` |
| `test_category_without_ref_is_rejected` | `work_category_id` при `chapter_ref_raw IS NULL` | `ck_..._unresolved_ref` |
| `test_ref_without_raw_line_is_rejected` (параметризован: со статьёй и без) | `chapter_ref_raw` при `raw_line IS NULL` | `ck_..._raw_line_pairs` |
| `test_duplicate_ordinal_in_one_proposal_is_rejected` | две записи с `ordinal = 1` | `uq_..._proposal_ordinal` |
| `test_same_ordinal_in_another_proposal_is_allowed` | те же `ordinal` у разных предложений | проходит |
| `test_deleting_a_used_category_hits_restrict` | `DELETE` статьи-листа | `fk_..._work_category_id` |
| `test_deleting_the_proposal_cascades_records` | `DELETE` предложения | записей нет, ошибки нет |

Нулевая сумма (`total_amount = 0`) — **валидна**, отдельным положительным тестом: замер даёт такую строку в реальном файле, и `>= 0` должно её пропускать.

- [ ] **Step 2: Прогнать тесты — они падают на отсутствующей таблице**

```
cd backend && env -u DATABASE_URL TEST_DATABASE_URL=$TEST_DATABASE_URL uv run pytest tests/integration/test_schema_constraints.py -k AdditionalWorks -q
```

Красный прогон **обязателен до реализации**: зелёный тест до реализации — сигнал, а не удача (урок Ф3, отступление 5).

- [ ] **Step 3: Миграция 0007**

Литералы строками, из кода не импортируются (миграция неизменна во времени — капкан `UNITS_SEED` в 0001). `TITLE_BLANK_CHARS = "' ' || chr(9) || chr(10) || chr(13) || chr(160)"` — свой литерал в файле миграции, как в 0005.

```python
op.create_table(
    "estimate_additional_works",
    sa.Column("id", sa.BigInteger(), primary_key=True),
    sa.Column("proposal_id", sa.BigInteger(), nullable=False),
    sa.Column("ordinal", sa.Integer(), nullable=False),
    sa.Column("chapter_ref_raw", sa.Text(), nullable=True),
    sa.Column("title", sa.Text(), nullable=False),
    sa.Column("total_amount", sa.Numeric(), nullable=False),
    sa.Column("work_category_id", sa.BigInteger(), nullable=True),
    sa.Column("raw_line", sa.Text(), nullable=True),
    sa.Column("created_at", ...), sa.Column("updated_at", ...),   # как в соседних таблицах
    sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], ondelete="CASCADE",
                            name="fk_estimate_additional_works_proposal_id"),
    sa.ForeignKeyConstraint(["work_category_id"], ["work_categories.id"], ondelete="RESTRICT",
                            name="fk_estimate_additional_works_work_category_id"),
    sa.CheckConstraint("total_amount >= 0", name="ck_estimate_additional_works_total_amount"),
    sa.CheckConstraint("ordinal > 0", name="ck_estimate_additional_works_ordinal"),
    sa.CheckConstraint(f"btrim(title, {TITLE_BLANK_CHARS}) <> ''",
                       name="ck_estimate_additional_works_title_not_blank"),
    sa.CheckConstraint("chapter_ref_raw IS NOT NULL OR work_category_id IS NULL",
                       name="ck_estimate_additional_works_unresolved_ref"),
    sa.CheckConstraint("raw_line IS NOT NULL OR (chapter_ref_raw IS NULL AND work_category_id IS NULL)",
                       name="ck_estimate_additional_works_raw_line_pairs"),
    sa.UniqueConstraint("proposal_id", "ordinal",
                        name="uq_estimate_additional_works_proposal_ordinal"),
)
op.create_index("idx_estimate_additional_works_work_category_id", "estimate_additional_works",
                ["work_category_id"], postgresql_where=sa.text("work_category_id IS NOT NULL"))
```

`downgrade` — `drop_index` + `drop_table`. Защитного отказа не требуется: данные производные (спека §2.3).

**Отдельного индекса по `proposal_id` не создавать** — его обслуживает `uq_..._proposal_ordinal` левым префиксом (спека §1.5 факт 4). Это не забывчивость, а решение; в миграции — комментарием.

- [ ] **Step 4: ORM-модель**

`EstimateAdditionalWork` в `models.py` рядом с `ProposalSummaryLine`, зеркалит **всё**: колонки с nullable-семантикой, пять CHECK, оба FK с теми же именами, UNIQUE, частичный индекс с тем же `postgresql_where`. Иначе `alembic check` покажет дрейф.

Константа пробельных символов **переиспользуется, а не дублируется**: `WORK_CATEGORY_TITLE_BLANK_CHARS` переименовывается в `TITLE_BLANK_CHARS_SQL` (определение [models.py:786](../../../backend/models.py#L786) и единственное использование 819; в тестах имя не встречается — проверено grep'ом). Это осознанная правка отревьюированного кода: два места, выражающие один набор символов, разъехались бы молча, а имя с `WORK_CATEGORY_` при общем использовании читалось бы неверно.

- [ ] **Step 5: Таблица — в `_DOMAIN_TABLES`, явно**

Добавить `"estimate_additional_works"` в `_DOMAIN_TABLES` ([conftest.py:198](../../../backend/tests/conftest.py#L198)) **перед `proposals`**: список идёт от детей к родителям, и место рядом с `proposal_summary_lines` и `proposal_additional_info` — там же, где остальные дети предложения.

Без этой правки таблица чистилась бы **неявно**, через `CASCADE` от `proposals` в единственном `TRUNCATE ... RESTART IDENTITY CASCADE` ([conftest.py:217-221](../../../backend/tests/conftest.py#L217-L221)). Два довода против такой опоры: скрытая зависимость от каскада — та самая грабля, которую рамка фазы называет отдельно (`committing_client` мимо `_DOMAIN_TABLES`); и `RESTART IDENTITY` для таблицы, попавшей в очистку косвенно, гарантировать нельзя — значит id поехали бы между тестами, а тесты, сверяющие `ordinal` и порядок записей, читались бы как флакающие.

`work_categories` в список **не добавлять**: справочник засеян миграцией 0005 и живёт одну сессию тестов (комментарий у списка). Наш FK на него — `RESTRICT`, но очистку он не блокирует, потому что сама таблица не усекается.

- [ ] **Step 6: Зелёный прогон схемных тестов + `alembic check`**

```
just db-test-migrate
just db-test-check
```

Ожидание: `alembic check` — «No new upgrade operations detected».

- [ ] **Step 7: Круговой рейс — прогнать, а не объявить**

```
cd backend && uv run alembic downgrade base && uv run alembic upgrade head
just db-test-check
```

---

### Task 2: парсер `2.0.0` — агрегатная строка исключается из `positions`

**Files:**
- Modify: `backend/parser/get_lot_positions.py`, `backend/parser/estimate.py`
- Test: `backend/tests/unit/parser/test_get_lot_positions.py`, `backend/tests/unit/parser/test_estimate.py`

**Interfaces:**
- Produces: JSON-контракт `2.0.0` — `contractor_items.positions` без агрегатной строки, `contractor_items.additional_works` без изменений формы (спека Ф2 §2.3).

- [ ] **Step 1: Прогон нового правила по УЖЕ СУЩЕСТВУЮЩИМ входам — до кода**

Механически, а не по памяти ([replaying-new-rules.md](../../insights/replaying-new-rules.md), слой 2). Найти все тестовые листы, где есть строка с пустыми A и B и названием «Дополнительные работы», и все места, где утверждается число позиций:

```
grep -rn "Дополнительные работы\|TABLE_PARSE_ADDITIONAL_WORKS_TITLE" backend/tests
grep -rn "len(positions)\|EXPECTED_POSITIONS\|FIXTURE_POSITIONS" backend/tests
```

Искать **и** литерал, **и** константу: тесты Ф2 строят агрегатную строку через
`TABLE_PARSE_ADDITIONAL_WORKS_TITLE`, и поиск по одному литералу их пропустил бы —
ровно тот способ промахнуться, из-за которого заведён инсайт.

Каждое найденное место разобрать **до** правки кода и записать в план исполнения: какое ожидание законно переворачивается (строка больше не позиция), а какое обязано остаться прежним. Ожидание, которое поехало без объяснения, — сигнал дефекта.

- [ ] **Step 2: Падающие тесты**

- `test_aggregate_row_is_not_a_position` — строки нет ни под одним ключом `positions`, `additional_works` заполнено;
- `test_aggregate_row_in_the_middle_keeps_keys_contiguous` — синтетический лист, где **после** агрегатной строки идут ещё две позиции: множество ключей равно `{"1", …, "N"}`, `N == len(positions)`, и позиция, стоявшая после агрегатной, получила ключ на единицу меньше, чем получила бы раньше. Это главный тест задачи: реальный файл держит агрегатную строку последней, поэтому о непрерывности он не говорит ничего, а резолвер Ф3 неканоничный набор ключей отвергает;
- переворот теста Ф2: `test_row_also_stays_in_positions` → `test_aggregate_row_lives_only_in_additional_works`, с сохранением сверки **каждого** поля копии (усиление Ф2 не терять) — теперь как утверждение о единственном месте;
- `test_parser_version_is_major_because_the_row_left_positions` (переименование `test_parser_version_is_bumped_for_the_new_key`), `PARSER_VERSION == "2.0.0"`;
- отказы Ф2 (чужое название кандидата, второй кандидат) — существующие тесты **не трогать**, они обязаны остаться зелёными.

- [ ] **Step 3: Реализация**

В `get_lot_positions` — `continue` после сборки `additional_works` (вместо комментария «Строка НЕ пропускается»). Docstring функции и `LotRows.additional_works` привести к новому контракту: строка в `positions` не попадает, переходное решение Ф2 снято.

`PARSER_VERSION = "2.0.0"`; комментарий-историю дополнить строкой про Ф4 — что изменилось и почему мажор (из `positions` исчезла строка; потребителя у версии в коде нет, но по ней отличают старый разбор от нового).

- [ ] **Step 4: Прогон подмножества парсера**

```
cd backend && uv run pytest tests/unit/parser -q
```

Ожидание: все зелёные; число тестов парсера выросло ровно на добавленные. Если поехал `EXPECTED_POSITIONS` для fixture — **остановиться**: у fixture агрегатной строки пока нет (её добавляет Task 6), значит число не должно измениться вовсе.

---

### Task 3: модуль `additional_works.py` — разбор строк и правило единогласия

**Files:**
- Create: `backend/services/additional_works.py`
- Test: `backend/tests/unit/test_additional_works.py`

**Interfaces:**

```python
#: Ключ блока «Дополнительная информация», в котором лежит расшивка. Замерен
#: во всех четырёх входах и совпадает побайтово — поэтому сравнение ТОЧНОЕ,
#: а не по подстроке: похожий незнакомый ключ не имеет права молча стать
#: денежным источником.
SVEDENIYA_KEY = "Сведения по дополнительным работам"
#: Нестрогий шаблон — ТОЛЬКО чтобы предупредить о похожем ключе, который не
#: совпал точно. В выборе источника денег не участвует.
SVEDENIYA_KEY_LOOSE_RE = re.compile(r"(?i)сведени.*дополнительн")
LINE_RE = re.compile(
    r"^\s*(?:(?P<ref>\d+(?:\.\d+)*\.?)\s+)?(?P<title>.+?)\s*[-–—]\s*"
    r"(?P<amount>[\d\s ]+(?:[.,]\d+)?)\s*руб\.?\s*$"
)

@dataclass(frozen=True)
class ParsedLine:
    ordinal: int
    ref: str | None          # нормализованный номер, завершающая точка снята
    title: str               # обрезан, непустой
    amount: Decimal          # >= 0
    raw_line: str            # исходная строка как в файле

@dataclass(frozen=True)
class AdditionalWorkRow:     # то, что ляжет в БД
    ordinal: int
    chapter_ref_raw: str | None
    title: str
    total_amount: Decimal
    work_category_id: int | None
    raw_line: str | None

@dataclass(frozen=True)
class ProposalAdditionalWorks:
    rows: tuple[AdditionalWorkRow, ...]
    warnings: tuple[str, ...]

def svedeniya_text(additional_info: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Текст расшивки и, отдельно, предупреждение о похожем-но-не-том ключе.

    Ключ выбирается ТОЧНЫМ сравнением нормализованных форм с `SVEDENIYA_KEY`
    (обрезка, схлопывание пробелов, `casefold`). Если точного нет, а нестрогому
    шаблону что-то соответствует, текст НЕ берётся, но выдаётся предупреждение с
    фактическим ключом. Поведение задано спекой — §2.4 (правило) и §2.9
    (предупреждение); довод там же: форма выгрузки уже менялась однажды (дефект
    итоговых строк, §1.4), и молча потерять расшивку из-за переименования поля
    нельзя — как нельзя и принять чужое поле за источник денег.
    """

def parse_lines(text: str) -> tuple[list[ParsedLine], list[str]]: ...   # разобранные, нечитаемые
def categories_by_chapter_number(positions, resolution) -> dict[str, set[int | None]]: ...
def resolve_ref(ref: str | None, by_number) -> tuple[int | None, str | None]: ...  # (id, причина отказа)
```

- [ ] **Step 1: Падающие тесты разбора**

Каждый вход **назван и пройден руками** до утверждения (слой 1 инсайта). Суммы — синтетические.

- ` ` в сумме и десятичная запятая → одна `Decimal`;
- сумма ноль → строка разобрана, `amount == Decimal("0")`;
- строка без ведущего номера → `ref is None`, строка разобрана;
- завершающая точка в номере снимается (`«3.2.2.»` → `«3.2.2»`);
- строка без «руб» и строка без суммы → **нечитаемая** (в `parse_lines` второй список);
- пустое название после обрезки → нечитаемая;
- вход, на котором `Decimal` бросает `InvalidOperation` → нечитаемая, исключение наружу не уходит;
- пустые строки текста пропускаются и нечитаемыми **не** считаются;
- `svedeniya_text` — четыре случая, все по именам: точный ключ найден; ключ с лишними пробелами и другим регистром **тоже** найден (нормализация работает); пустое значение и отсутствие ключа дают `None` **одинаково** (это одно состояние, спека §1.1); **похожий, но не тот ключ** (например «Сведения о дополнительных работах») текст не даёт, зато даёт предупреждение с фактическим ключом.

- [ ] **Step 2: Падающие тесты правила единогласия**

Вход — синтетические `positions` + `ProposalResolution`, собранные напрямую (без БД и без xlsx).

| Тест | Кандидаты | Ждёт |
|---|---|---|
| `test_single_candidate_with_a_category_is_attached` | один, статья есть | id статьи |
| `test_several_candidates_with_one_category_are_attached` | два, статья одна | id статьи |
| `test_candidates_with_different_categories_stay_unassigned` | два, статьи разные | `None` + причина |
| `test_candidate_without_a_category_blocks_attachment` | два, у одного `None` | `None` + причина |
| `test_no_candidates_stay_unassigned` | ноль | `None` + причина |
| `test_ref_absent_means_no_resolution_attempt` | ссылки нет | `None`, причины нет |
| `test_positions_of_another_kind_are_not_candidates` | номер совпал у строки-**позиции** | не кандидат |
| `test_disabled_structure_leaves_every_ref_unassigned` | `structure_disabled = True` | все `None` + причины |

Нормализация номера — одна: `" ".join(str(v).split())` плюс снятие завершающей точки, с **обеих** сторон сравнения.

- [ ] **Step 3: Реализация модуля**

Чистые функции, ни `Session`, ни ORM. `MAX_WARNING_EXAMPLES` и `_examples` **импортировать** из `category_resolution`, не переопределять.

- [ ] **Step 4: Зелёный прогон юнит-файла**

---

### Task 4: матрица состояний и владелец «Сведений»

**Files:**
- Modify: `backend/services/additional_works.py`
- Test: `backend/tests/unit/test_additional_works.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class OwnerDecision:
    owner_lot_key: str | None            # None, если владельца нет
    lot_keys_with_row: tuple[str, ...]   # предложения с агрегатной строкой
    warnings: tuple[str, ...]            # ноль или одно, на смету

def decide_owner(data: Mapping[str, Any]) -> OwnerDecision: ...
def build_rows(*, additional_works, svedeniya, resolution, positions, is_owner) -> ProposalAdditionalWorks: ...
```

- [ ] **Step 1: Падающие тесты матрицы (спека §2.6) — семь входов**

В каждом, помимо состава записей, проверяется **инвариант**: `sum(row.total_amount) == T` либо записей нет вовсе.

| Вход | Ждёт |
|---|---|
| строки нет, «Сведения» пусты / ключа нет | 0 записей, 0 предупреждений |
| строки нет, «Сведения» непусты | 0 записей, **одно** предупреждение |
| строка есть, «Сведения» пусты | 1 запись на `T`, `chapter_ref_raw is None`, `raw_line is None`, заголовок = `job_title` агрегатной строки |
| строка есть, `P = T` | по записи на строку, нераспределённой нет |
| строка есть, `P < T` | разобранные + нераспределённая на `T − P`, у неё максимальный `ordinal` |
| строка есть, `P > T` | ровно 1 нераспределённая на `T`, разобранные **не** сохранены, предупреждение с обоими числами |
| строка есть, `T` пусто | 0 записей, предупреждение |

- [ ] **Step 2: Падающие тесты владельца (спека §2.2) — четыре входа**

- ровно одно предложение с агрегатной строкой → оно владелец;
- ни одного **при непустых «Сведениях»** → одно предупреждение на смету;
- ни одного **при пустых «Сведениях»** → **ноль** предупреждений (редакционная точность гейта 2);
- два и более → `owner_lot_key is None`, у каждого своя нераспределённая запись на свой `T`, предупреждение одно и содержит число предложений, их ключи и `T` по каждому; предложение с пустым `T` записи не получает и названо в том же предупреждении.

- [ ] **Step 3: Тест на отсутствие состояния в модуле**

`test_one_instance_serves_two_proposals_without_leaking` по образцу Ф3: предупреждения и записи возвращаются результатом, а не копятся.

- [ ] **Step 4: Реализация и зелёный прогон**

---

### Task 5: интеграция в импорт — гейт, порядок, материализация

**Files:**
- Modify: `backend/services/estimate_import.py`, `backend/tests/payloads.py`
- Test: `backend/tests/integration/test_estimate_import.py`

**Interfaces:**
- Consumes: `ProposalResolution`, `ProposalAdditionalWorks`, `OwnerDecision`.
- Produces: строки `estimate_additional_works`; отказ `EstimateImportError` на форме 1.1.0; доменные предупреждения в `import_jobs.warnings`.
- **Публичная сигнатура `import_estimate` не меняется** — три тестовых вызова не правятся.

- [ ] **Step 1: Прогон гейта по существующим входам — до кода**

```
grep -rn "additional_works" backend/tests
grep -rn "JSON_KEY_CONTRACTOR_ITEMS" backend/tests
```

Замерено при написании плана: `payloads.proposal()` ключа `additional_works` не создаёт, то есть гейт ни на одном существующем payload'е сработать не может. Подтвердить это механически и записать; если найдётся место, строящее `contractor_items` руками, — разобрать до правки кода.

- [ ] **Step 2: Хелперы в `payloads.py`**

`proposal(..., additional_works: dict | None = None)` — ключ создаётся **всегда** (значение `None` по умолчанию), как это делает парсер: `contractor_items` у него всегда несёт `additional_works` (`get_proposals.py:92`). Плюс хелпер `additional_works_row(total="…", job_title="Дополнительные работы", source_row=999)` и хелпер текста «Сведений».

После правки — **полный** прогон backend: правка общего конструктора payload'ов задевает всех потребителей (урок Ф2: план не заметил файл, который мокал изменённую функцию).

- [ ] **Step 3: Падающие интеграционные тесты**

- `test_additional_works_rows_are_created` — синтетический payload с агрегатной строкой и тремя строками «Сведений»: три записи, `ordinal` 1..3 в порядке текста, суммы Decimal;
- `test_aggregate_row_does_not_become_a_position` — `position_items` не содержит строки с этим названием;
- `test_stale_1_1_0_shape_is_rejected` — payload, где агрегатная строка есть **и** в `positions`, и в `additional_works` с теми же деньгами → `EstimateImportError`; ни сметы, ни записей в БД не осталось;
- `test_same_title_with_other_money_is_not_the_stale_shape` — та же строка с **другими** деньгами → импорт проходит, строка идёт штатным путём Ф3 «вне структуры», предупреждение Ф3 на месте;
- `test_owner_is_ambiguous_across_two_lots` и `test_only_the_owner_gets_the_breakdown` — двухлотовые payload'ы;
- `test_replace_over_an_estimate_with_additional_works` — `replace=true` проходит, старые записи ушли каскадом;
- предупреждения: остаток, нечитаемая строка, неразрешённая ссылка — по одному тесту, каждый ищет **свою** подстроку.

- [ ] **Step 4: Реализация**

Порядок в `import_estimate` (спека §2.8):

1. `owner = decide_owner(data)` — предпасс по всем лотам **до** цикла; его предупреждения уходят в `warnings` один раз;
2. в цикле по лотам: `resolution = resolve_proposal(positions)` **один раз** на предложение (вынести из `_import_positions`, оставив там применение готового плана), `warnings.extend(resolution.warnings)` — здесь же;
3. **гейт формы 1.1.0** — сразу после плана, **до** `add_all` позиций: ищем строку с `kind == RowKind.OUTSIDE_STRUCTURE`, нормализованным названием «Дополнительные работы» и денежным блоком, равным `additional_works`. Нашли — `EstimateImportError`. Опора на `RowKind.OUTSIDE_STRUCTURE`, а не на свой предикат пустоты;
4. `_import_positions(..., resolution=resolution, ...)` — без вызова резолвера внутри;
5. `_import_additional_works(...)` — после позиций, в той же транзакции.

- [ ] **Step 5: Зелёный прогон интеграции и полного набора**

---

### Task 6: fixture — агрегатная строка, «Сведения», и E2E

**Files:**
- Modify: `fixtures/gp_estimate_fixture.xlsx` (скриптом; скрипт живёт в scratchpad и **не коммитится**)
- Test: `backend/tests/integration/test_import_fixture_e2e.py`

- [ ] **Step 1: Скрипт правки с предусловиями-ассертами**

Скрипт обязан **отказаться работать**, если структура не та, что замерена. Ассерты до правки: конец зоны позиций = 2587 и он merged в колонке A; пустых строк в зоне нет; ключ «Сведения…» в (2591, 2), значение — колонка 10; **формул на листе ноль**; `max_row = 2600`.

Порядок правки:

1. `wb = load_workbook(path, data_only=False)`; проверить, что формул нет (иначе остановиться: сохранение затрёт их);
2. `ws.insert_rows(2587)`;
3. **сдвинуть merged-диапазоны вручную** — `insert_rows` их не переносит: для каждого диапазона с `min_row >= 2587` (в исходных координатах) снять и создать заново со сдвигом `+1`. Обрабатывать по снимку списка, а не по живой коллекции;
4. добавить диапазон 4–5 на новой строке — как у соседних строк-позиций;
5. записать в строку 2587: A, B, C — пусто; D — «Дополнительные работы»; деньги — в те же колонки, что у обычной строки-позиции. Смещения money-колонок **взять из `parse_contractor_row` для `colspan = 11`**, а не зашить числом; замеренное ожидание — материалы/СМР/косвенные в 15–17 (нули, как в реальном файле), итого в 18, итого по объёмам заказчика в 19;
6. в строке ИТОГО (теперь 2588) найти колонку, значение которой **равно сумме `total_cost.total` всех строк-позиций до правки** (замер: у fixture ИТОГО равно этой сумме), и увеличить её на сумму агрегатной строки. Колонку не угадывать — найти сверкой;
7. в значение ключа «Сведения…» (теперь строка 2592, колонка 10) записать три строки, разделённые `\n`.

Суммы синтетические и круглые; сумма трёх строк равна итого агрегатной строки (`P = T`).

Три строки подбираются **программно, с проверкой поведения**:

- строка 1 — номер раздела, который встречается среди строк-разделов **ровно один раз** и резолвится в непустую статью (скрипт проверяет это через `CategoryResolver`, а не выбирает на глаз);
- строка 2 — номер `1`: в fixture он есть и у строки лота (без статьи), и у первого настоящего раздела, то есть кандидат без статьи → NULL + предупреждение. Сумма этой строки — **ноль**;
- строка 3 — номер, которого в файле нет вовсе (скрипт убеждается, что кандидатов ноль) → NULL + предупреждение.

- [ ] **Step 2: Самопроверка правки — числа Ф3 обязаны воспроизвестись**

После правки прогнать разбор и сверить: `positions == 2576`, расценённых `1828`, разделов `746`, `222 / 485 / 39 / 38`, верхнего уровня `16`, ключей блока допинформации `6`, `additional_works` заполнено, сумма трёх строк равна итого агрегатной строки, ИТОГО равно сумме позиций **включая** агрегатную строку.

**Поехало любое из этих чисел — правка неверна, ожидание не трогать.** Числа сохраняются по построению: агрегатная строка в `positions` не попадает (Task 2).

- [ ] **Step 3: Тестовый helper независимого ИТОГО**

Читает ячейку ИТОГО **прямо из workbook** по точной метке в колонке A, минуя `get_summary` и `proposal_summary_lines` (там известный дефект, спека §1.4). Неизвестная метка — громкий `pytest.fail`, а не фолбэк. Возвращает `Decimal`.

- [ ] **Step 4: E2E — восемь независимых утверждений (спека §4.3)**

Восемь тестов, а не один с восемью assert'ами: (1) агрегатная строка есть во входном листе; (2) она есть в raw `additional_works`; (3) её нет в raw `positions`; (4) соответствующего `position_item` в БД нет; (5) созданы три записи; (6) суммы и статьи ожидаемы, включая нулевую строку и две без привязки; (7) сумма записей равна контрольной сумме агрегатной строки; (8) сумма `position_items` плюс сумма записей равна независимому ИТОГО.

Чтение из БД — через `sa.select(Model.column)`, **не** через `db_session.get()` после Core-UPDATE (урок Ф3, отступление 1: identity map отдаёт `None` при верно записанной строке).

- [ ] **Step 5: Полный прогон backend**

---

### Task 7: негативные проверки, соответствие требований, финал

**Files:**
- Create: `docs/devlog/2026-08-07-additional-works.md`

`docs/phase7-frame.md` уже поправлена коммитом спеки; врезка «принята, дата» добавляется при приёмке **фазы**, а не фичи (`AGENTS.md` §9.2).

- [ ] **Step 1: Негативные проверки снятием защиты — шесть, плюс четыре CHECK**

Делает **оркестратор лично, не исполнитель**. По протоколу [verifying-guards.md](../../insights/verifying-guards.md) целиком: контрольный прогон на целом коде → снятие с печатью `grep -c` по маркеру и sha256 → красный прогон с записью, сколько именно упало → восстановление из **побайтовой копии** и сверка sha256 (`git checkout --` даёт CRLF и ложную тревогу — грабли Ф2).

| Защита | Как снять |
|---|---|
| `continue` в парсере | вернуть строку в `positions` |
| гейт формы 1.1.0 | вернуть `None` из поиска строки-копии |
| единогласие, ветка «статьи различаются» | привязать первую найденную |
| единогласие, ветка «кандидат без статьи» | игнорировать кандидатов со `None` |
| запись нераспределённого остатка | не создавать её при `P < T` |
| владелец при двух и более агрегатных строках | расшить каждому |

Две ветки единогласия — **разные пути**, снятие одной ничего не говорит о другой (слой 6 инсайта). Плюс по одной пробе на `ck_..._ordinal`, `ck_..._title_not_blank`, `ck_..._unresolved_ref`, `ck_..._raw_line_pairs`: снятие роняет **свой** тест и только его.

- [ ] **Step 2: Соответствие «требование спеки → тест»**

По тексту спеки §2.1–§2.11, требование за требованием (слой 3 инсайта). Требование без исполнителя либо получает тест, либо **объявляется границей** в devlog. Заранее известные кандидаты в границы: отсутствие отдельного индекса по `proposal_id` (свойство планировщика, тестом не закрепляется) и недостижимость ветки «вне структуры» из файла при парсере 2.0.0.

- [ ] **Step 3: `just ci` целиком — ДО пуша**

Ожидание: `uv lock --check`, ruff, `db-test-check` (дрейфа нет), backend pytest, eslint, `tsc -b --noEmit`, vitest 175, `OK: все проверки прошли`, exit 0.

- [ ] **Step 4: Круговой рейс миграции**

```
cd backend && uv run alembic downgrade base && uv run alembic upgrade head
just db-test-check
```

- [ ] **Step 5: Сверка с независимым ИТОГО на реальной 159-ТУ**

Разовым скриптом (в репозиторий не коммитится): загрузить оферту через штатный путь на чистый стенд, затем сверить `сумма position_items + сумма estimate_additional_works` с валовым ИТОГО файла, взятым **с листа** как «ИТОГО без НДС + НДС» (трёхстрочная форма, спека §1.4). Записать в devlog результат сверки и то, что до Ф4 та же сумма сходилась с ним **включая** агрегатную строку. Реальные суммы в devlog не переносить — только вердикт сверки и дельту-ноль.

- [ ] **Step 6: Свежая заливка на очищенном стенде**

Стенд чистится целиком. **Список удаляемых БД и точную команду показать пользователю и получить подтверждение непосредственно перед запуском.** Разрешение касается только данных development-стендов. После заливки: восстановить пользователя (`create-user --email admin@example.com --role admin`), залить все три оферты, сверить распределение по статьям с §1.1 спеки Ф3, замерить счётчики матчинга (ожидание: у 159-ТУ на единицу меньше строк в каскаде) и число предупреждений.

- [ ] **Step 7: Devlog**

`docs/devlog/2026-08-07-additional-works.md`: что сделано по задачам; замеры (собранные тесты до/после по `--collect-only`, числа fixture, времена `just ci`, счётчики стенда); что именно покраснело при каждом снятии защиты; таблица «требование → тест» и названные границы; отступления от плана с причинами; хвосты — Ф4a (`fix/summary-vat-lines`) как блокирующий предшественник Ф6, операционное ограничение «новая модель допработ только после `replace`», запрет читать `total_cost_with_vat` как «с НДС».

- [ ] **Step 8: Коммит и PR**

```bash
git add docs/
git commit -m "docs(devlog): Ф4 — допработы, замеры и негативные проверки"
git push -u origin feat/additional-works
gh pr create --title "Ф4: допработы — расшивка «Сведений», estimate_additional_works (миграция 0007)" --body-file docs/devlog/2026-08-07-additional-works.md
```

---

## Ожидаемый прирост тестов

Число выведено **пересчётом перечисленных в плане случаев**, а не оценкой. Первая редакция плана называла «65» и в Task 2 считала переименования новыми тестами — переименование `collect-count` не увеличивает. Ошибка того самого рода, про который заведён [replaying-new-rules.md](../../insights/replaying-new-rules.md): свойство было утверждено, не будучи прогнанным по конкретному входу. Поэтому ниже — таблица с выводом, которую можно проверить, а не итог.

| Задача | Откуда число | Собранных |
|---|---|---|
| Task 1 | десять функций Step 1, из них три параметризованы: `ordinal` (2), `title` (3), `raw_line_pairs` (2); плюс положительный тест нулевой суммы | 14 |
| Task 2 | **два новых** (`not_a_position`, `middle_keeps_keys_contiguous`); переворот теста Ф2 и тест версии — **переименования**, прирост нулевой | 2 |
| Task 3 | Step 1: девять случаев разбора + пять на `svedeniya_text` (точный ключ, нормализованный вариант, параметризованный «пусто/нет ключа», похожий ключ) = 14; Step 2: восемь строк таблицы единогласия | 22 |
| Task 4 | матрица 7 + владелец 4 + отсутствие состояния 1 | 12 |
| Task 5 | семь именованных тестов Step 3 + три на предупреждения (остаток, нечитаемая строка, неразрешённая ссылка) | 10 |
| Task 6 | восемь независимых утверждений E2E | 8 |
| | | **68** |

База 1029 → ожидание **1097**. Авторитетен замер `pytest --collect-only -q` **по задачам**, а не один раз в конце: исполнитель докладывает фактическое число после каждой задачи. Выбор параметризации может сдвинуть собранное число, не меняя покрытия, — поэтому расхождение не «подгоняется» и не игнорируется, а **объясняется** одной строкой в devlog: какой случай сложился в параметризацию или разложился на два. Расхождение, которое объяснить нечем, означает, что план и реализация разошлись, и разбираться надо **до** PR.

## Исполнение

- **Opus (оркестратор):** держит план, решает отступления, делает **все** коммиты, лично выполняет негативные проверки (Task 7 Step 1) и сверку с независимым ИТОГО (Step 5), лично показывает пользователю команду чистки стенда.
- **Sonnet:** содержательные задачи — Tasks 1, 3, 4, 5, 6 и замеры.
- **Haiku:** механика — переименования, правка docstring'ов, прогон команд.
- **Fable:** финальное ревью ветки до внешнего круга.
- Субагентам в промпте прямо запрещать печатать кириллицу в терминал: вердикты ASCII, русский текст в файл, `grep -c` вместо `grep`, `Read` вместо `cat`.
