# Парсер: смысл колонок — из заголовков — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Смысл колонки блока подрядчика выводится из пары «тип группы +
эффективная подпись» двухъярусной шапки, разрешается один раз на блок и доходит
объектом до `parse_contractor_row`; ширина блока остаётся только физической
границей сканирования.

**Architecture:** Новый модуль `parser/resolve_contractor.py` — закрытый словарь
пар, чтение двухъярусной шапки, семантический контракт отказа, `BlockLayout` +
`ResolvedContractor`. `estimate.parse_worksheet` разрешает раскладку после
`_validate_column_headers` и передаёт список `ResolvedContractor` вниз через
`read_lots_and_boundaries` → `get_proposals` → `get_lot_positions`/`get_summary`
→ `parse_contractor_row`/`get_items_dict`. `get_proposals` перестаёт звать
`read_contractors`. `check_estimate_layout` сравнивает разрешённый набор ключей
с `GP_EXPECTED_KEYS`. Четыре сущности §2.7 спеки исчезают последней кодовой
задачей, когда потребителей уже нет.

**Tech Stack:** Python 3.12 / openpyxl 3.1.5 / pytest. Только `backend/parser/`
и его тесты; ни БД, ни FastAPI, ни фронтенда фича не трогает.

**Spec:** [`docs/superpowers/specs/2026-08-25-parser-columns-by-header-design.md`](../specs/2026-08-25-parser-columns-by-header-design.md)
(гейт 2 закрыт 25.08.2026). План на спеку **ссылается** и её не пересказывает:
при расхождении побеждает спека (`AGENTS.md` §9.2). Рамка —
[`docs/proposals/2026-08-25-tenders-model.md`](../../proposals/2026-08-25-tenders-model.md) §4:
это фича 1 из четырёх; импорт многоподрядного файла и запись
`deviation_from_baseline_cost` в базу — фича 2, сюда не входят.

**Ветка:** `feat/parser-columns-by-header` — **уже существует**, два коммита
docs поверх `main` (`310c6bf`), дерево чистое. Новой ветки не заводить; PR один,
со ссылками на спеку и на этот план (`AGENTS.md` §9.3).

---

## Global Constraints

Требования спеки, действующие в КАЖДОЙ задаче. Точные значения — из спеки и
замеров ниже.

1. **Семантический контракт — только в `resolve_contractor`** (§2.6):
   неизвестная пара, повтор ключа, пропуск обязательного ключа → `EstimateParseError`.
   `_validate_contractor_geometry` проверяет геометрию и ничего больше;
   `check_estimate_layout` только предупреждает. Дублирования проверки нет.
2. **`colspan` остаётся физической границей сканирования** и перестаёт нести
   смысл. Ни одна ветка кода не выводит ключи из числа колонок.
3. **Словарь пар закрытый** (§2.2): без подстрок, синонимов и расстояний.
   Распознавание типа группы — `casefold`-префикс после `normalized_cell_text`;
   второго нормализатора не заводить (§2.3 п.3).
4. **Совместимость по классам** (§7): 18 файлов — побайтно тот же JSON и те же
   предупреждения; 3 файла ширины 10 — дельта ровно по трём пунктам; сводная
   таблица — разбор состоялся, сверка с независимым замером по листу; 5 файлов
   без тендерной шапки — тот же отказ тем же сообщением. Проверяется
   **сравнением снимков**, а не осмотром.
5. **Форма JSON: ключа НЕТ, а не `null`** (§2.8) — шаблон позиции строится по
   разрешённому набору ключей.
6. **`backend/parser/postprocess.py` не меняется ни одной строкой** (DoD 8).
7. **Конфиденциальность:** реальные имена подрядчиков и объектов, номера
   тендеров и абсолютные суммы не попадают ни в код, ни в тесты, ни в доки, ни
   в сообщения коммитов, ни в вывод скриптов. Файлы образцов в выводе и devlog
   называются первыми 12 знаками sha256. Снимки JSON лежат ТОЛЬКО под
   `samples/_snapshots/` — каталог `/samples` в `.gitignore` целиком.
8. **Деньги и ставки в JSON — десятичные строки** (`AGENTS.md` §3). Новая
   колонка `deviation_from_baseline_cost` идёт через `money_to_json`
   (решение Р2 ниже).
9. **`just ci` перед пушем**; тесты парсера гонять
   `cd backend && uv run pytest tests/unit/parser -v`; скрипты — через
   `backend/.venv/Scripts/python.exe` либо `uv run python -m scripts.<имя>`
   с `PYTHONIOENCODING=utf-8` (кириллица в консоли Windows).
10. **Существующие тесты не ослаблять**: каждое переписанное утверждение обязано
    стеречь то же или более сильное свойство под новым контрактом.
11. **`PARSER_VERSION` → `"4.0.0"`** (решение Р1 ниже) — единственная правка
    версии; `ParseResult` не меняется.

---

## Решения плана (спека молчит — зафиксировано на гейте 3)

| | Решение | Основание |
|---|---|---|
| **Р1** | `PARSER_VERSION` = `4.0.0` (мажор) | у файлов ширины 10 из позиций исчезает ключ `total_cost_for_organizer_quantity` и появляется `comment_contractor` — исчезновение ключа уже дважды признано мажором (2.0.0, 3.0.0, см. комментарий версии в `estimate.py`) |
| **Р2** | значения колонки «% от р/с» идут через `money_to_json`: `JSON_KEY_DEVIATION_FROM_CALCULATED_COST` добавляется в `MONEY_KEYS` | ставки и доли в JSON — строки, как `vat_rate`; текст `#DIV/0!` штатно гасит `replace_excel_errors_with_null`, а при невалидной базе ключ вычищает `_clean_deviation_fields` — обе дороги уже существуют |
| **Р3** | `check_estimate_layout` теряет параметр `ws` | после перехода на набор ключей функция не читает ни одной ячейки; диаграмма §2.5 иллюстрирует порядок вызовов, а не сигнатуру |
| **Р4** | новый модуль `parser/resolve_contractor.py`; `GP_EXPECTED_KEYS` живёт в `layout.py` | докстрока `layout.py` объявляет его домом предупреждений; отказы там жить не могут |
| **Р5** | подписи одиночных колонок и два префикса групп — константы в `constants.py`; подписи внутри групп (`Материалы`…) — словарём в `resolve_contractor.py` | `constants.py` — объявленный дом текстовых маркеров; `TABLE_PARSE_SUGGESTED_QUANTITY` переиспользуется, а не дублируется |
| **Р6** | «Комментарий участника» в замеренных файлах НЕ объединён по вертикали: подпись лежит в верхнем ярусе, нижний пуст | замер ниже; прежняя таблица §2.3 спеки содержала ложный замер и **исправлена ревизией 25.08.2026 (гейт 3)** — план следует исправленной спеке; правило 2 покрывает оба начертания, код на объединение комментария не полагается, но поддерживает его |
| **Р7** | снимки и сравнение классов — скриптами `backend/scripts/` (см. задачи 1 и 7), вывод — счётчики и дайджесты | реальные файлы и их JSON не коммитятся; pytest-прогон по samples/ уже существует и остаётся |

---

## Замеры при планировании (25.08.2026, 27 xlsx в `samples/` рекурсивно)

Прогон `read_contractors` + чтение двух ярусов шапки текущим кодом:

- **Классы §7 подтверждены поштучно:** 18 файлов — блок 11, последняя колонка
  «Комментарий участника»; 3 файла — блок 10, последняя «Комментарий участника»;
  1 сводная таблица — блоки 8 + 11 («% от р/с») + 12×3, лист на 2 644 строки,
  шапка A–D совпадает с `TABLE_PARSE_POSITION_COLUMN_HEADERS` в строке 11;
  5 файлов — `read_contractors` не находит строку контрагентов (отказ до всего).
- **Ровно восемь подписей** — как §1.3 спеки; двенадцать написаний заголовков
  групп, все начинаются `цена за ед. изм.` либо `стоимость всего, rub`
  (включая `…, с учетом ндс` без ставки и `…ндс 20` без знака процента).
- **Геометрия шапки** (строка шапки = `header_row`, у ГП это 9):
  заголовок группы — горизонтальное объединение в строке `header_row`
  (например J: `(9,11,9,14)`), подписи группы — в `header_row+1` без
  объединений; «Предлагаемое количество» и «% от р/с» — нижний ярус, верх пуст;
  «Стоимость всего за объемы заказчика» — вертикальное объединение
  `(header_row, c, header_row+1, c)`, значение в верхней ячейке;
  «Комментарий участника» — верхний ярус БЕЗ объединения (Р6).
- **Блок «Расчетная стоимость»** сводной таблицы: 8 колонок, `rowspan=4`,
  заголовок начинается с `Расчетная стоимость` → `postprocess` отделит его в
  `baseline_proposal` штатно; блок пуст, «% от р/с» держит `#DIV/0!` (§2.8).
- Обезличенный `fixtures/gp_estimate_fixture.xlsx` несёт ту же геометрию
  (J..T, группы `(9,11,9,14)`/`(9,15,9,18)`, вертикальное объединение S9:S10).

Пять измеренных раскладок → кортежи ключей (эталоны тестов; `uc.` =
`unit_cost.`, `tc.` = `total_cost.`, восьмёрка денег = `uc.materials, uc.works,
uc.indirect_costs, uc.total, tc.materials, tc.works, tc.indirect_costs,
tc.total`):

| Раскладка | column_keys | offsets (uc, tc) |
|---|---|---|
| 8 (расчётная стоимость) | восьмёрка денег | (0, 4) |
| 10 | `suggested_quantity`, восьмёрка, `comment_contractor` | (1, 5) |
| 11 ГП | `suggested_quantity`, восьмёрка, `total_cost_for_organizer_quantity`, `comment_contractor` | (1, 5) |
| 11 тендерная | `suggested_quantity`, восьмёрка, `total_cost_for_organizer_quantity`, `deviation_from_baseline_cost` | (1, 5) |
| 12 | `suggested_quantity`, восьмёрка, `total_cost_for_organizer_quantity`, `comment_contractor`, `deviation_from_baseline_cost` | (1, 5) |

---

## Проверенные символы

Проверено `grep`-ом по дереву 25.08.2026 (`AGENTS.md` §9.1). Существующее —
брать как есть; помеченное «заводится» в дереве отсутствует и создаётся
названной задачей.

| Символ | Где | Статус |
|---|---|---|
| `parse_worksheet`, `parse_estimate`, `ParseResult`, `PARSER_VERSION`, `_validate_contractor_blocks`, `_validate_column_headers`, `_find_column_header_row`, `_select_worksheet` | `backend/parser/estimate.py` | существует; `_validate_contractor_blocks` переименовывается задачей 3 |
| `EstimateParseError` | `backend/parser/errors.py` | существует |
| `read_contractors` | `backend/parser/read_contractors.py` | существует, не меняется |
| `find_lot_starts`, `read_lots_and_boundaries`, `LotsResult` | `backend/parser/read_lots_and_boundaries.py` | существует |
| `get_proposals`, `LotProposals` | `backend/parser/get_proposals.py` | существует |
| `get_lot_positions`, `LotRows` | `backend/parser/get_lot_positions.py` | существует |
| `get_summary` | `backend/parser/get_summary.py` | существует |
| `SummaryBlock`, `SummaryRow`, `build_summary_block` | `backend/parser/summary_block.py` | существует, не меняется |
| `parse_contractor_row`, `money_to_json`, `MONEY_KEYS`, `get_column_keys`, `money_group_offsets`, `SUPPORTED_CONTRACTOR_COLSPANS` | `backend/parser/parse_contractor_row.py` | существует; последние три исчезают задачей 5 |
| `get_items_dict` | `backend/parser/get_items_dict.py` | существует, меняет сигнатуру задачей 3 |
| `check_estimate_layout`, `find_suggested_quantity_header`, `GP_CONTRACTOR_COLSPAN` | `backend/parser/layout.py` | существует; последние два исчезают задачей 5 |
| `build_vat_rate`, `read_label_rate` | `backend/parser/vat_rate.py` | существует, не меняется |
| `normalized_cell_text`, `contractor_last_column`, `row_is_empty`, `cell_text_is_blank` | `backend/parser/sheet.py` | существует, не меняется |
| `build_merged_shape_map`, `merged_rows_in_first_column` | `backend/parser/build_merged_shape_map.py` | существует, не меняется |
| `normalize_lots_json_structure`, `_clean_deviation_fields`, `_is_baseline_valid`, `BASELINE_MISSING_TITLE` | `backend/parser/postprocess.py` | существует, **не меняется** (DoD 8) |
| `get_additional_info` | `backend/parser/get_additional_info.py` | существует; сигнатура прежняя, задачей 3 получает `resolved.geometry` |
| `TABLE_PARSE_SUGGESTED_QUANTITY`, `TABLE_PARSE_POSITION_COLUMN_HEADERS`, `TABLE_PARSE_BASELINE_COST`, `TABLE_PARSE_CONTRACTOR_TITLE`, `CONTRACTOR_SCAN_ROW_START`, `JSON_KEY_UNIT_COST`, `JSON_KEY_TOTAL_COST`, `JSON_KEY_MATERIALS`, `JSON_KEY_WORKS`, `JSON_KEY_INDIRECT_COSTS`, `JSON_KEY_TOTAL`, `JSON_KEY_SUGGESTED_QUANTITY`, `JSON_KEY_COMMENT_CONTRACTOR`, `JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST`, `JSON_KEY_DEVIATION_FROM_CALCULATED_COST`, `JSON_KEY_CONTRACTOR_INDEX`, `JSON_KEY_VAT_RATE` | `backend/parser/constants.py` | существует |
| `fixture_result`, `sample_results`, `SAMPLE_PATHS`, `_minimal_sheet`, `_sheet_with_summary_rows`, `_proposal`, `_positions`, `SUMMARY_TRIPLE`, `SUMMARY_TRIPLE_ZERO_VAT`, `TestVatRateFullPath`, `TestParseEstimateFailures`, `TestColumnHeaderGuard` | `backend/tests/unit/parser/test_estimate.py` | существует, правится задачей 3 |
| `CONTRACTOR` | `backend/tests/unit/parser/test_get_summary.py:9` и `test_get_lot_positions.py:32` (свои копии) | существует, правится задачей 3 |
| `sample_contractors_data`, `_patch_collaborators`, `COLUMN_HEADER_ROW`, `MODULE` | `backend/tests/unit/parser/test_get_proposals.py` | существует, правится задачей 3 |
| `_build_estimate_sheet` | `backend/tests/unit/parser/test_performance.py` | существует, правится задачей 3 |
| `FIXTURE_CONTRACTOR`, `_independent_total_with_vat` | `backend/tests/integration/test_import_fixture_e2e.py` | существует, правится задачей 3 |
| `_ALLOWED_SKIPS` | `backend/tests/conftest.py` | существует, **не правится** — новых samples-зависимых pytest-файлов план не заводит |
| `scripts/measure_vat_aggregation.py` | `backend/scripts/` | существует как образец оформления скрипта |
| `_money` (строки 179–197), `_text` (строка 249), `_import_positions` (строка 946), `import_estimate`, `ImportOutcome.warnings` | `backend/services/estimate_import.py` | существует, **не меняется** — тесты зовут как есть |
| `run_import` (строка 59), `resolver` (фикстура, строка 55) | `backend/tests/integration/test_estimate_import.py` | существует; новый класс пользуется ими как есть |
| `factories` (фикстура, `conftest.py:512`), `ContractFactory` (`factories.py:107`) | `backend/tests/` | существует |
| `PositionItem.{comment_contractor, total_cost_for_organizer_quantity, job_title_in_proposal}` | `backend/models.py` | существует (поля видны в конструкторе `_import_positions`) |
| `resolve_contractor`, `BlockLayout`, `ResolvedContractor`, `COLUMN_KEY_BY_PAIR`, `REQUIRED_COLUMN_KEYS`, `OPTIONAL_COLUMN_KEYS`, `_header_merge_map` | `backend/parser/resolve_contractor.py` | **заводится задачей 2** |
| `TABLE_PARSE_UNIT_COST_GROUP_PREFIX`, `TABLE_PARSE_TOTAL_COST_GROUP_PREFIX`, `TABLE_PARSE_ORGANIZER_QUANTITY_LABEL`, `TABLE_PARSE_COMMENT_CONTRACTOR_LABEL`, `TABLE_PARSE_DEVIATION_COLUMN_LABEL` | `backend/parser/constants.py` | **заводится задачей 2** |
| `GP_EXPECTED_KEYS` | `backend/parser/layout.py` | **заводится задачей 3** |
| `_validate_contractor_geometry` | `backend/parser/estimate.py` | **заводится задачей 3** (переименование) |
| `KEYS_8`, `KEYS_9`, `KEYS_10`, `KEYS_GP_11`, `KEYS_TENDER_11`, `KEYS_12`, `KEYS_PERMUTED_11`, `COLUMNS_BY_WIDTH`, `add_contractor_block`, `gp_sheet`, `resolved` | `backend/tests/unit/parser/sheet_builders.py` | **заводится задачей 2** |
| `GP_11_COLUMN_KEYS` (литеральный кортеж 11 ключей — эталон, независимый от sheet_builders) | `backend/tests/integration/test_import_fixture_e2e.py` | **заводится задачей 3** |
| `backend/tests/unit/parser/test_resolve_contractor.py` | — | **заводится задачей 2** |
| `scripts/snapshot_parse_samples.py`, `scripts/compare_parse_snapshots.py`, `scripts/verify_summary_sheet.py` | `backend/scripts/` | **заводятся задачами 1, 6, 6** |
| `docs/devlog/2026-08-25-parser-columns-by-header.md` | — | **заводится задачей 6, дописывается задачей 7** |

---

## Структура файлов

**Создаётся**

| Файл | Ответственность |
|---|---|
| `backend/parser/resolve_contractor.py` | Закрытый словарь пар, чтение двухъярусной шапки в реальных границах объединений, семантический контракт отказа (§2.6), `BlockLayout`, `ResolvedContractor`. Ни предупреждений, ни обхода позиций. |
| `backend/tests/unit/parser/test_resolve_contractor.py` | Пять измеренных раскладок, перестановка, три класса отказа, правила ярусов и объединений, нормализация. |
| `backend/tests/unit/parser/sheet_builders.py` | Строитель синтетических листов с настоящей двухъярусной шапкой; канонические кортежи ключей; фабрика `ResolvedContractor` для юнит-тестов. |
| `backend/scripts/snapshot_parse_samples.py` | Снимок разбора всех `samples/**/*.xlsx` в JSON-файлы по sha256 (задача 1 — «до», задача 6 — «после»). |
| `backend/scripts/compare_parse_snapshots.py` | Классификация «до/после» по четырём классам §7 с точными правилами дельты. |
| `backend/scripts/verify_summary_sheet.py` | Независимый замер сводной таблицы: сверка JSON с ячейками листа по записанным литералами координатам. |
| `docs/devlog/2026-08-25-parser-columns-by-header.md` | Числа снимков, вывод сравнения, независимый замер, отступления от плана. |

**Правится**

| Файл | Что именно |
|---|---|
| `backend/parser/constants.py` | Пять новых текстовых маркеров (Р5). |
| `backend/parser/estimate.py` | `_validate_contractor_blocks` → `_validate_contractor_geometry` (только геометрия); вызов `resolve_contractor` после `_validate_column_headers`; новая передача в `check_estimate_layout` и `read_lots_and_boundaries`; `PARSER_VERSION = "4.0.0"`; докстроки. |
| `backend/parser/layout.py` | `check_estimate_layout` сравнивает набор ключей с `GP_EXPECTED_KEYS`; `find_suggested_quantity_header` и `GP_CONTRACTOR_COLSPAN` удаляются (задача 5); докстрока модуля. |
| `backend/parser/read_lots_and_boundaries.py` | Параметр `contractors`, транзит в `get_proposals`. |
| `backend/parser/get_proposals.py` | Убирается вызов и импорт `read_contractors` и `money_group_offsets`; обход по `ResolvedContractor`; якоря НДС из `BlockLayout`. |
| `backend/parser/get_lot_positions.py` | `contractor: ResolvedContractor`; `get_items_dict(contractor.layout)`. |
| `backend/parser/get_summary.py` | `contractor: ResolvedContractor`, транзит в `parse_contractor_row`. |
| `backend/parser/parse_contractor_row.py` | `contractor: ResolvedContractor`; `MONEY_KEYS` += deviation; удаление `get_column_keys`, `money_group_offsets`, `SUPPORTED_CONTRACTOR_COLSPANS` (задача 5). |
| `backend/parser/get_items_dict.py` | `get_items_dict(layout: BlockLayout)` — шаблон из `column_keys`. |
| Тесты: `test_estimate.py`, `test_parse_contractor_row.py`, `test_get_items_dict.py`, `test_get_lot_positions.py`, `test_get_summary.py`, `test_get_proposals.py`, `test_performance.py`, `tests/integration/test_import_fixture_e2e.py` | Синтетические листы получают настоящую шапку; конструкторы `ResolvedContractor`; новые ожидания ширины 10. |

**Не трогать:** `backend/parser/postprocess.py`, `read_contractors.py`,
`read_headers.py`, `read_executer_block.py`, `summary_block.py`, `vat_rate.py`,
`sheet.py`, `build_merged_shape_map.py`, `sanitize_text.py`,
`find_row_by_first_column.py`, `services/**`, `crud/**`, `alembic/**`,
`frontend/**`, `backend/tests/conftest.py`, `backend/tests/payloads.py`.

---

## Task 1: Снимок «до» по всем образцам

Снимок обязан быть снят, пока парсер — ещё код `main`: после задачи 3 эталона
«до» не существует, а именно он доказывает побайтное равенство 18 файлов.
В ветке сейчас только docs-коммиты, значит `backend/parser/` идентичен `main`
— проверить это первым шагом.

**Files:**
- Create: `backend/scripts/snapshot_parse_samples.py`
- Вывод: `samples/_snapshots/before/*.json` (в git не попадает: `/samples` в `.gitignore`)

**Interfaces:**
- Produces: снимок `{sha256}.json` на каждый xlsx: `{"relpath", "parser_version",
  "status": "ok"|"error", "error", "warnings", "data"}`; `data` сериализован
  `json.dumps(..., ensure_ascii=False, indent=1)` — эта сериализация и есть
  предмет побайтного сравнения задачи 6. Рядом — `manifest.json`:
  `{"label", "git_head", "parser_tree", "dirty", "parser_version", "count",
  "digests"}`; его проверяет `compare_parse_snapshots` (задача 6).

Базовый коммит фичи — `310c6bf3db20528fb1e1ce85de963b1bafdd4daf`
(`BASELINE_COMMIT`); дерево парсера в нём —
`git rev-parse 310c6bf3db20528fb1e1ce85de963b1bafdd4daf:backend/parser` =
`ff8b7b3f65ce9922d75ec12977fa100be9a58b83`. Эталон «до» привязывается к этому
коммиту, а не к подвижному `origin/main`.

- [x] **Step 1: убедиться, что парсер не тронут веткой**

Run: `git diff 310c6bf3db20528fb1e1ce85de963b1bafdd4daf --stat -- backend/ && git status --porcelain -- backend/`
Expected: обе части пусты. Непусто — остановиться и разобраться до любого
снимка: эталон обязан быть снят кодом базового коммита.

- [x] **Step 2: написать скрипт снимка**

```python
"""Снимок разбора всех образцов samples/ для регрессии по классам (план фичи
«колонки по заголовкам», задача 1/6; спека §7).

Запуск из backend/:
    PYTHONIOENCODING=utf-8 uv run python -m scripts.snapshot_parse_samples before

Пишет по одному JSON на файл в samples/_snapshots/<метка>/<sha256>.json плюс
manifest.json с происхождением снимка (коммит, дерево backend/parser, версия
парсера, дайджесты). Непустая метка НЕ перезаписывается без --force: снимок
«до» — единственный эталон побайтного сравнения, и повторный запуск кодом
«после» уничтожил бы его молча, дав ложнозелёное сравнение.

Каталог /samples целиком в .gitignore — снимки не коммитятся. В консоль
печатаются только счётчики и дайджесты: имена файлов несут реквизиты
контрагентов и в вывод не попадают (AGENTS.md §9).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from parser import PARSER_VERSION, parse_estimate

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "samples"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def snapshot_one(path: Path) -> dict:
    payload: dict = {"relpath": str(path.relative_to(SAMPLES_DIR))}
    try:
        result = parse_estimate(str(path))
    except Exception as exc:  # noqa: BLE001 — снимок фиксирует и отказ, и его текст
        payload.update(status="error", error=f"{type(exc).__name__}: {exc}")
    else:
        payload.update(
            status="ok",
            parser_version=result.parser_version,
            warnings=result.warnings,
            data=result.data,
        )
    return payload


def main() -> int:
    label = sys.argv[1]
    force = "--force" in sys.argv[2:]
    out_dir = SAMPLES_DIR / "_snapshots" / label
    if out_dir.is_dir() and any(out_dir.iterdir()) and not force:
        print(f"метка «{label}» уже содержит снимок; перезапись только с --force")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    counts = {"ok": 0, "error": 0}
    digests: list[str] = []
    for path in sorted(SAMPLES_DIR.rglob("*.xlsx")):
        if path.name.startswith("~$") or "_snapshots" in path.parts:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digests.append(digest)
        payload = snapshot_one(path)
        (out_dir / f"{digest}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        counts[payload["status"]] += 1
        print(digest[:12], payload["status"])

    manifest = {
        "label": label,
        "git_head": _git("rev-parse", "HEAD"),
        "parser_tree": _git("rev-parse", "HEAD:backend/parser"),
        "dirty": bool(_git("status", "--porcelain", "--", "backend/parser")),
        "parser_version": PARSER_VERSION,
        "count": len(digests),
        "digests": sorted(digests),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"итого: ok={counts['ok']} error={counts['error']} "
          f"parser={manifest['parser_version']} dirty={manifest['dirty']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`parser_tree` в manifest — хеш дерева `backend/parser` на HEAD; вместе с
`dirty` он привязывает снимок к конкретному состоянию кода, а не к моменту
запуска. Снимок с `dirty=true` сравнение задачи 6 отвергает.

- [x] **Step 3: прогнать снимок «до»**

Run: `cd backend && PYTHONIOENCODING=utf-8 uv run python -m scripts.snapshot_parse_samples before`
Expected: 27 строк-дайджестов; **ok=21, error=6** — 18 файлов класса 1 плюс
3 файла ширины 10 разбираются, а сводная таблица сегодня падает на ширине 12
(`_validate_contractor_blocks` — до построения JSON) вместе с пятью файлами
без тендерной шапки. Любое другое соотношение — записать фактическое и сверить
руками с классами §7 до продолжения.

- [x] **Step 4: проверить происхождение эталона**

Run: `cat ../samples/_snapshots/before/manifest.json` (из backend/)
Expected: `"parser_tree": "ff8b7b3f65ce9922d75ec12977fa100be9a58b83"` (дерево
парсера базового коммита), `"dirty": false`, `"parser_version": "3.1.0"`,
`"count": 27`. Расхождение — снимок снят не тем кодом, пересдать.

Счётчики и шесть дайджестов отказов пойдут в devlog задачей 6. Проверить, что
`git status` не показывает ничего из `samples/`.

- [x] **Step 5: Commit**

```bash
git add backend/scripts/snapshot_parse_samples.py
git commit -m "feat(parser-columns): скрипт снимка разбора образцов, снят эталон «до»"
```

---

## Task 2: `resolve_contractor` — словарь, шапка, контракт отказа

Ядро фичи, ни одного существующего файла парсера не меняет (кроме пяти новых
констант в `constants.py`). Старый код продолжает работать как раньше — задача
зелёная сама по себе.

**Files:**
- Create: `backend/parser/resolve_contractor.py`
- Create: `backend/tests/unit/parser/sheet_builders.py`
- Create: `backend/tests/unit/parser/test_resolve_contractor.py`
- Modify: `backend/parser/constants.py` (пять маркеров)

**Interfaces:**
- Consumes: `normalized_cell_text` (`parser/sheet.py`), `EstimateParseError`
  (`parser/errors.py`), JSON-ключи и `TABLE_PARSE_SUGGESTED_QUANTITY`
  (`parser/constants.py`) — существуют.
- Produces (всё **заводится этой задачей**):
  - `TABLE_PARSE_UNIT_COST_GROUP_PREFIX = "цена за ед. изм."`
  - `TABLE_PARSE_TOTAL_COST_GROUP_PREFIX = "стоимость всего, rub"`
  - `TABLE_PARSE_ORGANIZER_QUANTITY_LABEL = "Стоимость всего за объемы заказчика"`
  - `TABLE_PARSE_COMMENT_CONTRACTOR_LABEL = "Комментарий участника"`
  - `TABLE_PARSE_DEVIATION_COLUMN_LABEL = "% от р/с"`
  - `@dataclass(frozen=True) class BlockLayout: column_keys: tuple[str, ...];
    unit_cost_offset: int; total_cost_offset: int`
  - `@dataclass(frozen=True) class ResolvedContractor: geometry: dict[str, Any];
    layout: BlockLayout`
  - `COLUMN_KEY_BY_PAIR: dict[tuple[str | None, str], str]` — двенадцать пар §2.2
  - `REQUIRED_COLUMN_KEYS: frozenset[str]` (восемь денежных),
    `OPTIONAL_COLUMN_KEYS: frozenset[str]` (четыре остальных)
  - `def resolve_contractor(ws: Worksheet, contractor: dict[str, Any],
    header_row: int) -> ResolvedContractor` — raises `EstimateParseError`
  - хелперы тестов: `KEYS_8/9/10/GP_11/TENDER_11/12/PERMUTED_11`,
    `COLUMNS_BY_WIDTH`, `add_contractor_block(...)`, `gp_sheet(...)`,
    `resolved(col_start, columns, **geometry_extra)`

- [x] **Step 1: константы в `constants.py`**

Рядом с `TABLE_PARSE_SUGGESTED_QUANTITY` (после строки 62):

```python
# Заголовки двухъярусной шапки блока подрядчика (фича «колонки по заголовкам»).
# Тип группы распознаётся по casefold-ПРЕФИКСУ горизонтально объединённой ячейки
# верхнего яруса: хвост (`RUB`, `ОСН`, ставка НДС) изменчив по построению и
# читается vat_rate.read_label_rate. Префикс total_cost включает запятую и RUB
# намеренно: без них под него подпала бы одиночная колонка «Стоимость всего за
# объемы заказчика» (спека §2.1).
TABLE_PARSE_UNIT_COST_GROUP_PREFIX = "цена за ед. изм."
TABLE_PARSE_TOTAL_COST_GROUP_PREFIX = "стоимость всего, rub"

# Подписи одиночных колонок блока — пишутся по реальным заголовкам файлов
# (замер плана фичи: 27 образцов, восемь подписей без единого варианта).
TABLE_PARSE_ORGANIZER_QUANTITY_LABEL = "Стоимость всего за объемы заказчика"
TABLE_PARSE_COMMENT_CONTRACTOR_LABEL = "Комментарий участника"
TABLE_PARSE_DEVIATION_COLUMN_LABEL = "% от р/с"
```

- [x] **Step 2: строитель листов `sheet_builders.py`**

Полный файл (тестовая инфраструктура, импортируется тестами как модуль — по
образцу `tests/payloads.py`):

```python
"""Синтетические листы с настоящей двухъярусной шапкой блока подрядчика.

После фичи «колонки по заголовкам» ни один синтетический лист не разбирается
без полной шапки блока: resolve_contractor отказывает на неопознанной паре.
Геометрия — та же, что замерена на реальных файлах (план фичи, «Замеры»):
заголовок группы объединён по горизонтали в строке header_row, подписи группы —
в header_row+1; «Стоимость всего за объемы заказчика» объединена по вертикали;
«Комментарий участника» лежит в верхнем ярусе без объединения; «Предлагаемое
количество» и «% от р/с» — в нижнем ярусе.
"""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from parser.constants import (
    JSON_KEY_COMMENT_CONTRACTOR,
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_MATERIALS,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_SUGGESTED_QUANTITY,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
    JSON_KEY_UNIT_COST,
    JSON_KEY_WORKS,
    TABLE_PARSE_COMMENT_CONTRACTOR_LABEL,
    TABLE_PARSE_DEVIATION_COLUMN_LABEL,
    TABLE_PARSE_ORGANIZER_QUANTITY_LABEL,
    TABLE_PARSE_POSITION_COLUMN_HEADERS,
    TABLE_PARSE_SUGGESTED_QUANTITY,
)
from parser.resolve_contractor import BlockLayout, ResolvedContractor

_UC = JSON_KEY_UNIT_COST
_TC = JSON_KEY_TOTAL_COST
_MONEY_EIGHT = tuple(
    f"{group}.{part}"
    for group in (_UC, _TC)
    for part in (JSON_KEY_MATERIALS, JSON_KEY_WORKS, JSON_KEY_INDIRECT_COSTS, JSON_KEY_TOTAL)
)

KEYS_8 = _MONEY_EIGHT
KEYS_9 = (*_MONEY_EIGHT, JSON_KEY_COMMENT_CONTRACTOR)
KEYS_10 = (JSON_KEY_SUGGESTED_QUANTITY, *_MONEY_EIGHT, JSON_KEY_COMMENT_CONTRACTOR)
KEYS_GP_11 = (
    JSON_KEY_SUGGESTED_QUANTITY,
    *_MONEY_EIGHT,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_COMMENT_CONTRACTOR,
)
KEYS_TENDER_11 = (
    JSON_KEY_SUGGESTED_QUANTITY,
    *_MONEY_EIGHT,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
)
KEYS_12 = (
    JSON_KEY_SUGGESTED_QUANTITY,
    *_MONEY_EIGHT,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_COMMENT_CONTRACTOR,
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
)
#: Перестановка при ПРЕЖНЕМ colspan 11 (спека §6): группы целиком, но не на
#: канонических местах; одиночные колонки перемешаны.
KEYS_PERMUTED_11 = (
    JSON_KEY_COMMENT_CONTRACTOR,
    f"{_TC}.{JSON_KEY_MATERIALS}",
    f"{_TC}.{JSON_KEY_WORKS}",
    f"{_TC}.{JSON_KEY_INDIRECT_COSTS}",
    f"{_TC}.{JSON_KEY_TOTAL}",
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    f"{_UC}.{JSON_KEY_MATERIALS}",
    f"{_UC}.{JSON_KEY_WORKS}",
    f"{_UC}.{JSON_KEY_INDIRECT_COSTS}",
    f"{_UC}.{JSON_KEY_TOTAL}",
    JSON_KEY_SUGGESTED_QUANTITY,
)

#: Канонические раскладки по ширине — ТОЛЬКО для удобства тестов: продакшен-код
#: ширину со смыслом не связывает.
COLUMNS_BY_WIDTH = {8: KEYS_8, 9: KEYS_9, 10: KEYS_10, 11: KEYS_GP_11, 12: KEYS_12}

_SUBLABEL_BY_PART = {
    JSON_KEY_MATERIALS: "Материалы",
    JSON_KEY_WORKS: "СМР",
    JSON_KEY_INDIRECT_COSTS: "Косвенные расходы",
    JSON_KEY_TOTAL: "Всего",
}
_GROUP_HEAD = {_UC: "Цена за ед. изм., RUB, ОСН", _TC: "Стоимость всего, RUB, ОСН"}


def add_contractor_block(
    ws: Worksheet,
    *,
    col_start: int,
    columns: tuple[str, ...],
    title: str = 'ООО "Тест"',
    contractor_row: int = 6,
    header_row: int = 9,
    vat_suffix: str | None = ", с учетом НДС 20%",
) -> Worksheet:
    """Пишет заголовок подрядчика и двухъярусную шапку его блока.

    Группы (подряд идущие ключи одного префикса) объединяются по горизонтали;
    группа из одной колонки — ошибка вызова: тип группы существует только у
    объединения шириной больше единицы (спека §2.3 п.1).
    """
    colspan = len(columns)
    ws.merge_cells(
        start_row=contractor_row, start_column=col_start,
        end_row=contractor_row, end_column=col_start + colspan - 1,
    )
    ws.cell(row=contractor_row, column=col_start, value=title)

    i = 0
    while i < colspan:
        key = columns[i]
        col = col_start + i
        group = key.split(".")[0] if "." in key else None
        if group in (_UC, _TC):
            run = 1
            while i + run < colspan and columns[i + run].startswith(f"{group}."):
                run += 1
            if run < 2:
                raise ValueError(f"группа {group} из одной колонки не имеет типа (спека §2.3 п.1)")
            ws.merge_cells(start_row=header_row, start_column=col, end_row=header_row, end_column=col + run - 1)
            ws.cell(row=header_row, column=col, value=_GROUP_HEAD[group] + (vat_suffix or ""))
            for j in range(run):
                part = columns[i + j].split(".", 1)[1]
                ws.cell(row=header_row + 1, column=col + j, value=_SUBLABEL_BY_PART[part])
            i += run
            continue
        if key == JSON_KEY_SUGGESTED_QUANTITY:
            ws.cell(row=header_row + 1, column=col, value=TABLE_PARSE_SUGGESTED_QUANTITY)
        elif key == JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST:
            ws.merge_cells(start_row=header_row, start_column=col, end_row=header_row + 1, end_column=col)
            ws.cell(row=header_row, column=col, value=TABLE_PARSE_ORGANIZER_QUANTITY_LABEL)
        elif key == JSON_KEY_COMMENT_CONTRACTOR:
            ws.cell(row=header_row, column=col, value=TABLE_PARSE_COMMENT_CONTRACTOR_LABEL)
        elif key == JSON_KEY_DEVIATION_FROM_CALCULATED_COST:
            ws.cell(row=header_row + 1, column=col, value=TABLE_PARSE_DEVIATION_COLUMN_LABEL)
        else:
            raise ValueError(f"строитель не знает ключа {key!r}")
        i += 1
    return ws


def gp_sheet(
    columns: tuple[str, ...] = KEYS_GP_11,
    *,
    col_start: int = 10,
    header_row: int = 9,
    **block_kwargs,
) -> Worksheet:
    """Минимальный лист сметы ГП: маркер контрагентов, шапка A–D, блок, лот.

    Наследник `_minimal_sheet` из test_estimate.py: та же строка маркера G6,
    маркер лота D11 и «позиция» A11/B11 — плюс настоящая шапка блока.
    """
    ws = Workbook().active
    ws["G6"] = "Наименование контрагента"
    add_contractor_block(ws, col_start=col_start, columns=columns, header_row=header_row, **block_kwargs)
    for column, title in TABLE_PARSE_POSITION_COLUMN_HEADERS.items():
        ws.cell(row=header_row, column=column, value=title)
    ws["D11"] = "Лот №1 Тестовый"
    ws["A11"] = 1
    ws["B11"] = 1
    return ws


def resolved(col_start: int, columns: tuple[str, ...], **geometry_extra) -> ResolvedContractor:
    """ResolvedContractor для юнит-тестов нижних слоёв — без листа и шапки.

    Смещения выводятся из columns; тесты, которые СТЕРЕГУТ смещения, пишут их
    литералами в test_resolve_contractor.py, а не берут отсюда.
    """
    geometry = {
        "column_start": col_start,
        "merged_shape": {"rowspan": 1, "colspan": len(columns)},
        **geometry_extra,
    }
    return ResolvedContractor(
        geometry=geometry,
        layout=BlockLayout(
            column_keys=tuple(columns),
            unit_cost_offset=columns.index(f"{_UC}.{JSON_KEY_MATERIALS}"),
            total_cost_offset=columns.index(f"{_TC}.{JSON_KEY_MATERIALS}"),
        ),
    )
```

- [x] **Step 3: написать падающие тесты `test_resolve_contractor.py`**

Состав (каждый тест — обычный pytest, листы через `gp_sheet`/`add_contractor_block`,
кроме одного рукописного):

```python
"""Разрешение раскладки блока по подписям шапки — ядро фичи, без оркестратора.

Пять измеренных раскладок (план, «Замеры») — тестовые случаи, а не ветви кода
(спека §3). Отказы §2.6 проверяются на входах, нарушающих РОВНО ОДНО
ограничение каждый (docs/insights/verifying-guards.md, слой про выбор входа).
"""
from __future__ import annotations

import pytest
from openpyxl import Workbook

from parser.errors import EstimateParseError
from parser.read_contractors import read_contractors
from parser.resolve_contractor import BlockLayout, resolve_contractor

from .sheet_builders import (
    KEYS_8, KEYS_10, KEYS_GP_11, KEYS_PERMUTED_11, KEYS_TENDER_11, KEYS_12,
    gp_sheet,
)

HEADER_ROW = 9


def _contractor(ws):
    """Геометрия первого подрядчика листа — тем же кодом, что в продакшене."""
    return read_contractors(ws)[1]


class TestMeasuredLayouts:
    @pytest.mark.parametrize(
        ("columns", "offsets"),
        [
            (KEYS_8, (0, 4)),
            (KEYS_10, (1, 5)),
            (KEYS_GP_11, (1, 5)),
            (KEYS_TENDER_11, (1, 5)),
            (KEYS_12, (1, 5)),
        ],
    )
    def test_measured_layouts_resolve_to_measured_keys(self, columns, offsets):
        """Смещения записаны ЛИТЕРАЛАМИ — эталон не выводится из проверяемого."""
        ws = gp_sheet(columns)
        result = resolve_contractor(ws, _contractor(ws), HEADER_ROW)
        assert result.layout.column_keys == columns
        assert (result.layout.unit_cost_offset, result.layout.total_cost_offset) == offsets

    def test_permuted_columns_resolve_by_labels_not_by_position(self):
        ws = gp_sheet(KEYS_PERMUTED_11)
        result = resolve_contractor(ws, _contractor(ws), HEADER_ROW)
        assert result.layout.column_keys == KEYS_PERMUTED_11
        assert result.layout.unit_cost_offset == 6
        assert result.layout.total_cost_offset == 1

    def test_real_gp_geometry_built_by_hand(self):
        """Геометрия fixture воспроизведена merge_cells-ами, не строителем —
        строитель не проверяет сам себя (замер плана: J..T, header_row 9)."""
        ws = Workbook().active
        ws["G6"] = "Наименование контрагента"
        ws.merge_cells("J6:T6")
        ws["J6"] = 'ООО "Тест"'
        ws["J10"] = "Предлагаемое количество"
        ws.merge_cells("K9:N9")
        ws["K9"] = "Цена за ед. изм., RUB, ОСН, с учетом НДС 20%"
        ws.merge_cells("O9:R9")
        ws["O9"] = "Стоимость всего, RUB, ОСН, с учетом НДС 20%"
        for col, label in ((11, "Материалы"), (12, "СМР"), (13, "Косвенные расходы"), (14, "Всего")):
            ws.cell(row=10, column=col, value=label)
            ws.cell(row=10, column=col + 4, value=label)
        ws.merge_cells("S9:S10")
        ws["S9"] = "Стоимость всего за объемы заказчика"
        ws["T9"] = "Комментарий участника"

        result = resolve_contractor(ws, _contractor(ws), HEADER_ROW)

        assert result.layout.column_keys == KEYS_GP_11
        assert (result.layout.unit_cost_offset, result.layout.total_cost_offset) == (1, 5)

    def test_group_header_without_rate_still_types_the_group(self):
        """`…, с учетом НДС` без ставки и вовсе без хвоста — тип группы тот же;
        ставкой занимается vat_rate, не словарь (спека §2.1)."""
        ws = gp_sheet(KEYS_GP_11, vat_suffix=None)
        result = resolve_contractor(ws, _contractor(ws), HEADER_ROW)
        assert result.layout.column_keys == KEYS_GP_11

    def test_geometry_is_passed_through_unchanged(self):
        ws = gp_sheet(KEYS_GP_11)
        contractor = _contractor(ws)
        assert resolve_contractor(ws, contractor, HEADER_ROW).geometry is contractor


class TestHeaderTiers:
    def test_labels_are_matched_after_normalization(self):
        """Регистр, двойные пробелы и неразрывный пробел не мешают паре."""
        ws = gp_sheet(KEYS_GP_11)
        ws.cell(row=HEADER_ROW, column=20, value="КОММЕНТАРИЙ  УЧАСТНИКА")
        result = resolve_contractor(ws, _contractor(ws), HEADER_ROW)
        assert result.layout.column_keys == KEYS_GP_11

    def test_merged_group_header_does_not_bleed_right(self):
        """«Последняя виденная» шапка вправо не тянется (спека §2.3 п.1):
        колонка сразу за группой с пустыми обоими ярусами — отказ, а не
        продолжение группы."""
        ws = gp_sheet(KEYS_GP_11)
        # Сносим подпись колонки S (организатор): вертикальное объединение
        # остаётся, значение убирается — оба яруса пусты.
        ws.cell(row=HEADER_ROW, column=19, value=None)
        with pytest.raises(EstimateParseError, match="S9"):
            resolve_contractor(ws, _contractor(ws), HEADER_ROW)

    def test_column_inside_group_without_sublabel_is_a_refusal(self):
        """Пара (тип, «») — неопознана (спека §2.3 п.4)."""
        ws = gp_sheet(KEYS_GP_11)
        ws.cell(row=HEADER_ROW + 1, column=12, value=None)  # СМР группы цен
        with pytest.raises(EstimateParseError, match="L9"):
            resolve_contractor(ws, _contractor(ws), HEADER_ROW)


class TestRefusals:
    def test_unknown_label_names_coordinate_and_both_raw_labels(self):
        ws = gp_sheet(KEYS_GP_11)
        ws.cell(row=HEADER_ROW + 1, column=12, value="Труд")  # вместо СМР
        with pytest.raises(EstimateParseError) as err:
            resolve_contractor(ws, _contractor(ws), HEADER_ROW)
        message = str(err.value)
        assert "L9" in message
        assert "Труд" in message
        assert "Цена за ед. изм." in message      # верхняя подпись до нормализации
        assert 'ООО "Тест"' in message

    def test_merged_group_with_foreign_prefix_is_unknown(self):
        """Объединённая группа с чужим заголовком не получает тип — и колонка
        с легальной подписью «Материалы» внутри неё не опознаётся."""
        ws = gp_sheet(KEYS_GP_11)
        ws.cell(row=HEADER_ROW, column=11, value="Скидка, RUB")
        with pytest.raises(EstimateParseError, match="Скидка"):
            resolve_contractor(ws, _contractor(ws), HEADER_ROW)

    def test_duplicate_key_names_both_coordinates(self):
        ws = gp_sheet(KEYS_GP_11)
        # Организатор S превращаем во второй комментарий: снять вертикальное
        # объединение нельзя, но подпись заменить можно — значение в S9.
        ws.cell(row=HEADER_ROW, column=19, value="Комментарий участника")
        with pytest.raises(EstimateParseError) as err:
            resolve_contractor(ws, _contractor(ws), HEADER_ROW)
        assert "S9" in str(err.value) and "T9" in str(err.value)

    def test_missing_required_key_names_block_and_key_without_coordinate(self):
        """Ширина 7: восьмёрка денег без total_cost.total — группа стоимости из
        ТРЁХ колонок. Нарушено ровно одно ограничение — состав; все подписи
        легальны и уникальны. Лист рукописный: строитель групп из трёх колонок
        с легальной формой не описывает."""
        ws = Workbook().active
        ws["G6"] = "Наименование контрагента"
        ws.merge_cells("J6:P6")
        ws["J6"] = 'ООО "Тест"'
        ws.merge_cells("J9:M9")
        ws["J9"] = "Цена за ед. изм., RUB, ОСН, с учетом НДС 20%"
        ws.merge_cells("N9:P9")
        ws["N9"] = "Стоимость всего, RUB, ОСН, с учетом НДС 20%"
        for col, label in ((10, "Материалы"), (11, "СМР"), (12, "Косвенные расходы"), (13, "Всего")):
            ws.cell(row=10, column=col, value=label)
        for col, label in ((14, "Материалы"), (15, "СМР"), (16, "Косвенные расходы")):
            ws.cell(row=10, column=col, value=label)

        with pytest.raises(EstimateParseError) as err:
            resolve_contractor(ws, _contractor(ws), HEADER_ROW)

        message = str(err.value)
        assert "total_cost.total" in message
        assert 'ООО "Тест"' in message
```

Все имена в эскизах существуют либо заводятся этой задачей.

- [x] **Step 4: убедиться, что тесты падают**

Run: `cd backend && uv run pytest tests/unit/parser/test_resolve_contractor.py -v`
Expected: сбор падает на `ModuleNotFoundError: parser.resolve_contractor`.

- [x] **Step 5: реализовать `resolve_contractor.py`**

Полный модуль:

```python
"""Разрешение раскладки блока подрядчика по подписям двухъярусной шапки.

Смысл колонки — пара «тип группы + эффективная подпись» (спека §2.2); ширина
блока задаёт только физическую границу сканирования. Словарь пар ЗАКРЫТЫЙ:
нечётких эвристик нет — измерено, что подписи не плавают (спека §1.3), а
нечёткое правило вернуло бы ровно тот класс ошибки, от которого фича
избавляется.

Здесь же — единственное место семантического контракта (§2.6): неизвестная
пара, повтор ключа и пропуск обязательного ключа дают EstimateParseError с
именем подрядчика. Геометрию (наличие объединения) проверяет
estimate._validate_contractor_geometry ДО вызова; предупреждениями о НАБОРЕ
ключей занимается layout.check_estimate_layout ПОСЛЕ.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .constants import (
    JSON_KEY_COMMENT_CONTRACTOR,
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_MATERIALS,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_SUGGESTED_QUANTITY,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
    JSON_KEY_UNIT_COST,
    JSON_KEY_WORKS,
    TABLE_PARSE_COMMENT_CONTRACTOR_LABEL,
    TABLE_PARSE_DEVIATION_COLUMN_LABEL,
    TABLE_PARSE_ORGANIZER_QUANTITY_LABEL,
    TABLE_PARSE_SUGGESTED_QUANTITY,
    TABLE_PARSE_TOTAL_COST_GROUP_PREFIX,
    TABLE_PARSE_UNIT_COST_GROUP_PREFIX,
)
from .errors import EstimateParseError
from .sheet import normalized_cell_text

#: Подписи колонок внутри денежной группы → хвост составного ключа.
_COST_GROUP_SUBLABELS = {
    "материалы": JSON_KEY_MATERIALS,
    "смр": JSON_KEY_WORKS,
    "косвенные расходы": JSON_KEY_INDIRECT_COSTS,
    "всего": JSON_KEY_TOTAL,
}

#: Закрытый словарь §2.2: пара «тип группы + эффективная подпись (casefold)» →
#: ключ JSON. Подписи одиночных колонок берутся из constants (одна правда).
COLUMN_KEY_BY_PAIR: dict[tuple[str | None, str], str] = {
    **{
        (JSON_KEY_UNIT_COST, label): f"{JSON_KEY_UNIT_COST}.{part}"
        for label, part in _COST_GROUP_SUBLABELS.items()
    },
    **{
        (JSON_KEY_TOTAL_COST, label): f"{JSON_KEY_TOTAL_COST}.{part}"
        for label, part in _COST_GROUP_SUBLABELS.items()
    },
    (None, TABLE_PARSE_SUGGESTED_QUANTITY.casefold()): JSON_KEY_SUGGESTED_QUANTITY,
    (None, TABLE_PARSE_ORGANIZER_QUANTITY_LABEL.casefold()): JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    (None, TABLE_PARSE_COMMENT_CONTRACTOR_LABEL.casefold()): JSON_KEY_COMMENT_CONTRACTOR,
    (None, TABLE_PARSE_DEVIATION_COLUMN_LABEL.casefold()): JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
}

#: Восемь денежных ключей обязаны присутствовать ровно один раз (спека §2.2).
REQUIRED_COLUMN_KEYS = frozenset(
    key for key in COLUMN_KEY_BY_PAIR.values() if "." in key
)
#: Четыре остальных — опциональны, но не более одного раза каждый.
OPTIONAL_COLUMN_KEYS = frozenset(COLUMN_KEY_BY_PAIR.values()) - REQUIRED_COLUMN_KEYS


@dataclass(frozen=True)
class BlockLayout:
    """Раскладка блока: физический порядок ключей и якоря денежных групп.

    column_keys — tuple, а не list: frozen=True запрещает переприсваивание
    поля, но не мутацию списка внутри него (спека §2.4). Смещения считаются
    там же, где ключи, из того же перечня — две согласованные таблицы
    разъезжаются молча (довод прежнего money_group_offsets).
    """

    column_keys: tuple[str, ...]
    unit_cost_offset: int
    total_cost_offset: int


@dataclass(frozen=True)
class ResolvedContractor:
    """Геометрия блока и его раскладка — один объект, а не два списка,
    связываемых индексом: тихий сдвиг индекса при нескольких подрядчиках
    означал бы деньги не в тех полях (спека §2.4)."""

    geometry: dict[str, Any]
    layout: BlockLayout


def _header_merge_map(
    ws: Worksheet, header_row: int
) -> dict[tuple[int, int], tuple[int, int, int, int]]:
    """Границы объединённых диапазонов, накрывающих два яруса шапки.

    Раскрытие объединения — строго в его реальном диапазоне (спека §2.3 п.1):
    карта строится по границам диапазонов, «последняя виденная» шапка вправо
    не тянется.
    """
    bounds_by_cell: dict[tuple[int, int], tuple[int, int, int, int]] = {}
    for merged_range in ws.merged_cells.ranges:
        if merged_range.max_row < header_row or merged_range.min_row > header_row + 1:
            continue
        bounds = (merged_range.min_row, merged_range.min_col, merged_range.max_row, merged_range.max_col)
        for row in (header_row, header_row + 1):
            if merged_range.min_row <= row <= merged_range.max_row:
                for col in range(merged_range.min_col, merged_range.max_col + 1):
                    bounds_by_cell[(row, col)] = bounds
    return bounds_by_cell


def resolve_contractor(ws: Worksheet, contractor: dict[str, Any], header_row: int) -> ResolvedContractor:
    """Разрешает раскладку одного блока по подписям шапки.

    Верхний ярус — строка header_row (та же, где шапка A–D), нижний —
    header_row+1. Тип группы приписывается колонке, только если она входит в
    горизонтально объединённую ячейку верхнего яруса шириной больше единицы;
    эффективная подпись — нижний ярус, если он непуст, иначе верхний (для
    колонки внутри группы — только нижний, §2.3 п.4). Значение объединённой
    ячейки читается из её якоря.

    Raises:
        EstimateParseError: неизвестная пара / повтор ключа / пропуск
            обязательного ключа — контракт §2.6, только здесь.
    """
    title = contractor.get("value")
    col_start: int = contractor["column_start"]
    colspan: int = contractor["merged_shape"]["colspan"]
    merges = _header_merge_map(ws, header_row)

    def raw_at(row: int, col: int) -> Any:
        bounds = merges.get((row, col))
        if bounds is not None:
            return ws.cell(row=bounds[0], column=bounds[1]).value
        return ws.cell(row=row, column=col).value

    keys: list[str] = []
    seen: dict[str, tuple[str, str]] = {}  # ключ -> (координата, показанная подпись)

    for offset in range(colspan):
        col = col_start + offset
        coordinate = f"{get_column_letter(col)}{header_row}"
        upper_bounds = merges.get((header_row, col))
        upper_raw = raw_at(header_row, col)
        lower_raw = raw_at(header_row + 1, col)
        upper_shown = normalized_cell_text(upper_raw)
        lower_shown = normalized_cell_text(lower_raw)

        in_group = upper_bounds is not None and upper_bounds[3] > upper_bounds[1]
        if in_group:
            head = upper_shown.casefold()
            if head.startswith(TABLE_PARSE_UNIT_COST_GROUP_PREFIX):
                group_type: str | None = JSON_KEY_UNIT_COST
            elif head.startswith(TABLE_PARSE_TOTAL_COST_GROUP_PREFIX):
                group_type = JSON_KEY_TOTAL_COST
            else:
                group_type = None
            key = COLUMN_KEY_BY_PAIR.get((group_type, lower_shown.casefold())) if group_type else None
            shown = lower_shown
        else:
            shown = lower_shown or upper_shown
            key = COLUMN_KEY_BY_PAIR.get((None, shown.casefold()))

        if key is None:
            raise EstimateParseError(
                f"Не удалось опознать колонку {coordinate} блока подрядчика «{title}»: "
                f"заголовок группы «{upper_raw or ''}», подпись «{lower_raw or ''}». "
                "Смысл колонок определяется подписями шапки по закрытому словарю; "
                "незнакомая подпись — отказ, иначе стоимости легли бы не в те поля молча."
            )
        if key in seen:
            first_coordinate, first_shown = seen[key]
            raise EstimateParseError(
                f"Колонки {first_coordinate} и {coordinate} блока подрядчика «{title}» "
                f"дают один и тот же ключ «{key}» (подписи «{first_shown}» и «{shown}»). "
                "Каждый ключ допустим не более одного раза."
            )
        seen[key] = (coordinate, shown)
        keys.append(key)

    missing = sorted(REQUIRED_COLUMN_KEYS - set(keys))
    if missing:
        raise EstimateParseError(
            f"В блоке подрядчика «{title}» нет обязательных денежных колонок: "
            f"{', '.join(missing)}. Координаты у отсутствующей колонки не существует; "
            "без полной восьмёрки денег предложение не читается."
        )

    column_keys = tuple(keys)
    return ResolvedContractor(
        geometry=contractor,
        layout=BlockLayout(
            column_keys=column_keys,
            unit_cost_offset=column_keys.index(f"{JSON_KEY_UNIT_COST}.{JSON_KEY_MATERIALS}"),
            total_cost_offset=column_keys.index(f"{JSON_KEY_TOTAL_COST}.{JSON_KEY_MATERIALS}"),
        ),
    )
```

- [x] **Step 6: прогнать тесты задачи**

Run: `cd backend && uv run pytest tests/unit/parser/test_resolve_contractor.py -v`
Expected: PASS все.

- [x] **Step 7: прогнать весь набор парсера — старый код не задет**

Run: `cd backend && uv run pytest tests/unit/parser -q`
Expected: PASS (все прежние тесты зелёные: ни один существующий модуль
логику не менял).

- [x] **Step 8: Commit**

```bash
git add backend/parser/resolve_contractor.py backend/parser/constants.py \
  backend/tests/unit/parser/sheet_builders.py backend/tests/unit/parser/test_resolve_contractor.py
git commit -m "feat(parser-columns): resolve_contractor — словарь пар, шапка, контракт отказа"
```

---

## Task 3: Сквозная передача `ResolvedContractor`

Одна связная правка через весь конвейер — делить её на «низ» и «верх» значило
бы заводить временный мост (второй вызов `read_contractors` внутри
`get_proposals`), который следующая задача сносила бы. Задача большая, но
одноцелевая; каждый шаг — один файл.

**Files:**
- Modify: `backend/parser/estimate.py`, `backend/parser/layout.py`,
  `backend/parser/read_lots_and_boundaries.py`, `backend/parser/get_proposals.py`,
  `backend/parser/get_lot_positions.py`, `backend/parser/get_summary.py`,
  `backend/parser/parse_contractor_row.py`, `backend/parser/get_items_dict.py`
- Test: `backend/tests/unit/parser/test_estimate.py`,
  `test_parse_contractor_row.py`, `test_get_items_dict.py`,
  `test_get_lot_positions.py`, `test_get_summary.py`, `test_get_proposals.py`,
  `test_performance.py`; `backend/tests/integration/test_import_fixture_e2e.py`

**Interfaces:**
- Consumes: `resolve_contractor`, `BlockLayout`, `ResolvedContractor`,
  `REQUIRED_COLUMN_KEYS` (задача 2); строитель `sheet_builders` (задача 2).
- Produces:
  - `estimate._validate_contractor_geometry(contractors: list[dict]) -> None`
  - `layout.GP_EXPECTED_KEYS: frozenset[str]`;
    `layout.check_estimate_layout(contractors: Sequence[ResolvedContractor], lot_starts: list[dict]) -> list[str]`
  - `read_lots_and_boundaries(ws, *, header_row: int, contractors: Sequence[ResolvedContractor]) -> LotsResult`
  - `get_proposals(ws, start_row, end_row, *, header_row: int, contractors: Sequence[ResolvedContractor]) -> LotProposals`
  - `get_lot_positions(ws, contractor: ResolvedContractor, lot_start_row, lot_end_row) -> LotRows`
  - `get_summary(ws, contractor: ResolvedContractor, search_start_row) -> SummaryBlock`
  - `parse_contractor_row(ws, row_index, contractor: ResolvedContractor) -> dict`
  - `get_items_dict(layout: BlockLayout) -> dict`
  - `PARSER_VERSION = "4.0.0"`

- [x] **Step 1: написать падающий тест — дефект ширины 10 исправлен**

В `test_estimate.py` (новый класс, импорты из `sheet_builders` добавить):

```python
class TestWidthTenComment:
    """Класс 2 спеки §7: комментарий ширины 10 попадает в comment_contractor,
    ключа total_cost_for_organizer_quantity в JSON нет вовсе (§2.8)."""

    def test_comment_of_a_ten_wide_block_lands_in_comment_contractor(self):
        ws = gp_sheet(KEYS_10)
        ws.cell(row=12, column=1, value=2)
        ws.cell(row=12, column=2, value="1")
        ws.cell(row=12, column=4, value="Работа")
        ws.cell(row=12, column=19, value="таймлайн уточним")  # 10-я колонка блока

        result = parse_worksheet(ws)

        position = _positions(result)["2"]
        assert position["comment_contractor"] == "таймлайн уточним"
        assert "total_cost_for_organizer_quantity" not in position
```

- [x] **Step 2: убедиться, что тест падает по НУЖНОЙ причине**

Run: `cd backend && uv run pytest tests/unit/parser/test_estimate.py::TestWidthTenComment -v`
Expected: FAIL — сегодня текст лежит в `total_cost_for_organizer_quantity`
(через `money_to_json`), ключа `comment_contractor` нет.

- [x] **Step 3: `parse_contractor_row.py`**

- В `MONEY_KEYS` добавить `JSON_KEY_DEVIATION_FROM_CALCULATED_COST` (импорт из
  constants) с комментарием Р2: «доля — та же дисциплина строк, что и деньги;
  `#DIV/0!` гасит postprocess, ключ при невалидной базе вычищает
  `_clean_deviation_fields`».
- `parse_contractor_row(ws, row_index, contractor: ResolvedContractor)`:

```python
    contractor_col_start: int = contractor.geometry["column_start"]
    list_of_keys = list(contractor.layout.column_keys)

    cells_to_parse: list[Cell] = [
        ws.cell(row=row_index, column=col_idx)
        for col_idx in range(contractor_col_start, contractor_col_start + len(list_of_keys))
    ]

    return map_to_nested_dict(cells_to_parse, list_of_keys)
```

- Докстроки: раскладка приходит разрешённой (`resolve_contractor`), ширина
  блока — только число ключей; секция Raises про `SUPPORTED_CONTRACTOR_COLSPANS`
  удаляется. `get_column_keys` и `money_group_offsets` пока НЕ трогать —
  их снесёт задача 5, когда уйдут их тесты.

- [x] **Step 4: `get_items_dict.py`**

```python
def get_items_dict(layout: BlockLayout) -> dict[str, Any]:
    """Шаблон позиции: общие поля + поля подрядчика по разрешённой раскладке.

    Шаблон строится по ТОМУ ЖЕ перечню ключей, которым читаются ячейки
    (BlockLayout.column_keys), поэтому разъехаться с parse_contractor_row не
    может по построению. У блока, где колонки нет, ключа в шаблоне нет вовсе —
    отсутствующий ключ, а не null (спека §2.8): все потребители читают позицию
    через .get(...) (estimate_import.py) и .pop(..., None) (_clean_deviation_fields).
    """
    item: dict[str, Any] = {
        JSON_KEY_NUMBER: None,
        JSON_KEY_CHAPTER_NUMBER: None,
        JSON_KEY_ARTICLE_SMR: None,
        JSON_KEY_JOB_TITLE: None,
        JSON_KEY_COMMENT_ORGANIZER: None,
        JSON_KEY_UNIT: None,
        JSON_KEY_QUANTITY: None,
    }
    for key in layout.column_keys:
        head, _, tail = key.partition(".")
        if tail:
            item.setdefault(head, {})[tail] = None
        else:
            item[key] = None
    return item
```

Ветка `"error"` и вся colspan-ветвистость исчезают вместе с прежней докстрокой.
Порядок ключей шаблона для ширины 11 ГП совпадает с прежним поколоночно —
это условие побайтного равенства класса 1.

- [x] **Step 5: `get_summary.py` и `get_lot_positions.py`**

- `get_summary(ws, contractor: ResolvedContractor, search_start_row)`:
  `contractor_last_column(contractor.geometry)`; транзит `contractor` в
  `parse_contractor_row`.
- `get_lot_positions(ws, contractor: ResolvedContractor, lot_start_row, lot_end_row)`:
  `contractor_last_column(contractor.geometry)`;
  `item = get_items_dict(contractor.layout)`;
  `parse_contractor_row(ws, current_row_num, contractor)`.
- Докстроки обеих: аргумент — разрешённый блок; про «нужны column_start и
  merged_shape.colspan» заменить на «геометрия в .geometry, раскладка в .layout».

- [x] **Step 6: `get_proposals.py`**

- Импорты: убрать `from .parse_contractor_row import money_group_offsets` и
  `from .read_contractors import read_contractors`; добавить
  `from collections.abc import Sequence` и
  `from .resolve_contractor import ResolvedContractor` (только для аннотации).
- Сигнатура: `def get_proposals(ws, start_row, end_row, *, header_row: int,
  contractors: Sequence[ResolvedContractor]) -> LotProposals:`
- Тело цикла:

```python
    proposals: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    for i, resolved in enumerate(contractors, start=1):
        geometry = resolved.geometry
        contractor_name = geometry.get("value")
        contractor_row_start = geometry.get("row_start")
        contractor_col_start: int = geometry["column_start"]
        contractor_coordinate = geometry.get("coordinate")
        merged_shape: dict[str, int] = geometry.get("merged_shape", {})
        rowspan: int = merged_shape.get("rowspan", 1)
        colspan: int = merged_shape.get("colspan", 1)

        inn_val: Any = None
        address_val: Any = None
        accreditation_val: Any = None
        if rowspan == 1 and contractor_row_start is not None:
            inn_val = ws.cell(row=contractor_row_start + 1, column=contractor_col_start).value
            address_val = ws.cell(row=contractor_row_start + 2, column=contractor_col_start).value
            accreditation_val = ws.cell(row=contractor_row_start + 3, column=contractor_col_start).value

        lot_rows = get_lot_positions(ws, resolved, lot_start_row=start_row, lot_end_row=end_row)
        summary = get_summary(ws, resolved, search_start_row=start_row)
        warnings.extend(summary.warnings)

        # Якоря групповых шапок — из разрешённой раскладки: смещения живут в
        # BlockLayout и посчитаны из того же перечня ключей, что сами колонки.
        unit_cost_label = ws.cell(row=header_row, column=contractor_col_start + resolved.layout.unit_cost_offset).value
        total_cost_label = ws.cell(row=header_row, column=contractor_col_start + resolved.layout.total_cost_offset).value
```

  Дальше тело функции НЕ меняется (`get_proposals.py:136-162`): `build_vat_rate`,
  строка `vat_rate_value`, словарь `contractor_items_data`, ключи предложения —
  всё прежнее, только `contractor_additional_info_data =
  get_additional_info(ws, geometry)` получает `geometry` вместо
  `contractor_details`.

```python
        contractor_additional_info_data = get_additional_info(ws, geometry)
```

- Докстроки модуля и функции: `contractors` приходит разрешённым из
  `parse_worksheet`, «read_contractors отсюда убран — геометрия не читается
  второй раз (спека §2.5)»; фразу про `money_group_offsets` заменить ссылкой
  на `BlockLayout`. Ветка `if not contractors_list` не нужна — пустой список
  даёт пустой обход.

- [x] **Step 7: `read_lots_and_boundaries.py`**

`def read_lots_and_boundaries(ws, *, header_row: int, contractors:
Sequence[ResolvedContractor]) -> LotsResult:` — транзит в `get_proposals`
(`contractors=contractors`); докстрока аргумента.

- [x] **Step 8: `layout.py` — набор ключей вместо ширины**

Модуль после правки (кроме `find_suggested_quantity_header` — он остаётся
мёртвым до задачи 5):

```python
from .constants import (
    JSON_KEY_COMMENT_CONTRACTOR,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_SUGGESTED_QUANTITY,
    TABLE_PARSE_SUGGESTED_QUANTITY,
)
from .resolve_contractor import REQUIRED_COLUMN_KEYS, ResolvedContractor

#: Эталон набора ключей сметы ГП (спека §2.5): восемь обязательных денежных
#: плюс три из четырёх опциональных. deviation_from_baseline_cost сюда не
#: входит: его присутствие означает расчётную стоимость, которой у сметы ГП
#: нет (AGENTS.md §4) — лишний ключ даёт предупреждение.
GP_EXPECTED_KEYS = frozenset(REQUIRED_COLUMN_KEYS) | {
    JSON_KEY_SUGGESTED_QUANTITY,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_COMMENT_CONTRACTOR,
}


def check_estimate_layout(
    contractors: Sequence[ResolvedContractor],
    lot_starts: list[dict[str, Any]],
) -> list[str]:
    """Сверяет разрешённые раскладки с эталоном сметы ГП.

    Только предупреждения: файл уже разобран по фактическим подписям, решение
    о нём принимает человек. Отказы живут в resolve_contractor (§2.6).
    Прежняя формулировка «блок занимает N колонок вместо 11» исчезла вместе с
    самой мыслью: число колонок больше ничего не значит.
    """
    warnings: list[str] = []

    if len(contractors) != 1:
        warnings.append(
            f"В смете ГП ожидается один подрядчик, найдено {len(contractors)}. "
            "Разобраны все найденные блоки — проверьте файл."
        )
    if len(lot_starts) != 1:
        warnings.append(
            f"В смете ожидается один лот, найдено {len(lot_starts)}. Проверьте файл."
        )

    for resolved in contractors:
        keys = set(resolved.layout.column_keys)
        missing = sorted(GP_EXPECTED_KEYS - keys)
        extra = sorted(keys - GP_EXPECTED_KEYS)
        if not missing and not extra:
            continue
        parts = []
        if missing:
            parts.append(f"отсутствуют: {', '.join(missing)}")
        if extra:
            parts.append(f"лишние: {', '.join(extra)}")
        title = resolved.geometry.get("value")
        warnings.append(
            f"Набор колонок блока подрядчика «{title}» не совпадает с ожидаемым "
            f"для сметы ГП — {'; '.join(parts)}. Файл разобран по фактическим "
            "подписям шапки; вес позиции в сквозной матрице берётся из "
            f"«{TABLE_PARSE_SUGGESTED_QUANTITY}», проверьте файл."
        )
    return warnings
```

Докстроку модуля переписать: смысл колонок разрешён по подписям
(`resolve_contractor`), здесь — только сверка НАБОРА с эталоном и счётчики.
Сообщение о наборе стабильно по тексту — на фрагмент «не совпадает с ожидаемым»
опирается сравнение снимков задачи 6.

- [x] **Step 9: `estimate.py`**

- Импорты: `from .resolve_contractor import resolve_contractor`; убрать
  `from .parse_contractor_row import SUPPORTED_CONTRACTOR_COLSPANS`.
- `_validate_contractor_blocks` → `_validate_contractor_geometry`; тело —
  только проверка `merged_shape`:

```python
def _validate_contractor_geometry(contractors: list[dict[str, Any]]) -> None:
    """Отвергает файлы, где у блока подрядчика нет физических границ.

    Проверяется только геометрия: заголовок объединён, значит известно, сколько
    колонок сканировать. Смысл колонок здесь НЕ проверяется — семантический
    контракт живёт в resolve_contractor и только там (спека §2.6).
    """
    for contractor in contractors[1:]:
        if not contractor.get("merged_shape"):
            raise EstimateParseError(
                f"Заголовок подрядчика «{contractor.get('value')}» "
                f"({contractor.get('coordinate')}) не объединён с колонками своего "
                "блока, поэтому неизвестна его физическая граница — сколько колонок "
                "сканировать и где кончается блок."
            )
```

- `parse_worksheet` после `header_row = _validate_column_headers(...)`:

```python
    resolved_contractors = [
        resolve_contractor(ws, contractor, header_row) for contractor in contractors[1:]
    ]

    warnings.extend(check_estimate_layout(resolved_contractors, lot_starts))

    lots = read_lots_and_boundaries(ws, header_row=header_row, contractors=resolved_contractors)
```

- `PARSER_VERSION = "4.0.0"` с комментарием в журнале версий:

```python
# 4.0.0 (фича «колонки по заголовкам»): смысл колонок блока подрядчика
# определяется парой «тип группы + подпись» из шапки, а не числом колонок.
# Мажор, потому что у файлов ширины 10 из позиций ИСЧЕЗ ключ
# total_cost_for_organizer_quantity (файл такой колонки не нёс — прежний разбор
# читал в него комментарий участника) и появился comment_contractor; на
# позициях впервые возможен deviation_from_baseline_cost. Для смет ГП
# ширины 11 JSON побайтно прежний (спека §7, регрессия по классам).
```

- Докстроки `parse_worksheet` (Raises) и модуля привести к новому потоку §2.5.

- [x] **Step 10: прогнать тест шага 1**

Run: `cd backend && uv run pytest tests/unit/parser/test_estimate.py::TestWidthTenComment -v`
Expected: PASS.

- [x] **Step 11: тест потока данных — раскладка разрешается один раз и доходит объектом**

В `test_estimate.py` (DoD 2; порядок и связь звеньев стережёт только
утверждение о потоке данных — `docs/insights/data-flow-assertions-for-order.md`):

```python
class TestResolutionFlow:
    def test_resolution_happens_once_per_block_and_the_same_object_reaches_rows(self, monkeypatch):
        """Два лота, один блок: resolve_contractor вызван РОВНО один раз, и в
        parse_contractor_row приходит ТОТ ЖЕ объект (identity), а не пересчёт.
        Регресс «get_proposals снова читает геометрию сам» этим и ловится."""
        import parser.estimate as estimate_module
        import parser.get_lot_positions as glp_module

        ws = _sheet_with_summary_rows(SUMMARY_TRIPLE, second_lot=True)

        resolved_seen = []
        real_resolve = estimate_module.resolve_contractor
        monkeypatch.setattr(
            estimate_module, "resolve_contractor",
            lambda ws_, c, hr: resolved_seen.append(real_resolve(ws_, c, hr)) or resolved_seen[-1],
        )
        row_contractors = []
        real_row = glp_module.parse_contractor_row
        monkeypatch.setattr(
            glp_module, "parse_contractor_row",
            lambda ws_, row, contractor: row_contractors.append(contractor) or real_row(ws_, row, contractor),
        )

        parse_worksheet(ws)

        assert len(resolved_seen) == 1
        assert row_contractors, "ни одна строка не прошла через parse_contractor_row"
        assert all(c is resolved_seen[0] for c in row_contractors)
```

Run: `cd backend && uv run pytest tests/unit/parser/test_estimate.py -k test_resolution_happens_once -v`
Expected: PASS.

- [x] **Step 12: переписать `test_estimate.py` под новый контракт**

Диспозиция по местам:

- `_minimal_sheet(contractor_colspan)` — делегирует строителю:

```python
def _minimal_sheet(contractor_colspan: int | None):
    """Лист с шапкой контрагентов, шапкой блока и маркером лота, без позиций.

    None — заголовок подрядчика не объединён (случай геометрии); ширины 8–12
    получают канонические измеренные раскладки sheet_builders.COLUMNS_BY_WIDTH.
    """
    if contractor_colspan is None:
        from openpyxl import Workbook
        ws = Workbook().active
        ws["G6"] = "Наименование контрагента"
        ws["J6"] = 'ООО "Тест"'
        ws["D11"] = "Лот №1 Тестовый"
        for column, title in TABLE_PARSE_POSITION_COLUMN_HEADERS.items():
            ws.cell(row=9, column=column, value=title)
        ws["A11"] = 1
        ws["B11"] = 1
        return ws
    return gp_sheet(COLUMNS_BY_WIDTH[contractor_colspan])
```

- `test_raises_on_unsupported_contractor_colspan` — УДАЛИТЬ (ширина 12 теперь
  законна) и вместо него:

```python
    def test_unknown_column_label_is_a_refusal_through_the_full_path(self):
        """Проводка контракта §2.6 через parse_worksheet — сами классы отказа
        проверены в test_resolve_contractor.py."""
        ws = _minimal_sheet(11)
        ws.cell(row=10, column=12, value="Труд")  # подпись СМР группы цен
        with pytest.raises(EstimateParseError, match="L9"):
            parse_worksheet(ws)

    def test_twelve_wide_block_parses_with_an_extra_key_warning(self):
        """Ширина 12 разбирается; лишний deviation_from_baseline_cost — отличие
        от эталона ГП и предупреждение поимённо (спека §2.5)."""
        ws = _minimal_sheet(12)
        result = parse_worksheet(ws)
        matching = [w for w in result.warnings if "не совпадает с ожидаемым" in w]
        assert len(matching) == 1
        assert "deviation_from_baseline_cost" in matching[0]
```

- `test_supported_but_non_gp_colspan_is_a_warning_not_an_error` — оставить
  сценарий (ширина 8 → предупреждение), утверждение сменить: в тексте
  предупреждения — «не совпадает с ожидаемым» и все три отсутствующих ключа
  поимённо (`suggested_quantity`, `total_cost_for_organizer_quantity`,
  `comment_contractor`); плюс негатив на исчезнувшую мысль:
  `assert not any("колонок вместо" in w for w in result.warnings)`.
- `test_raises_when_contractor_header_is_not_merged` — без изменений
  (фрагмент «не объединён» сохранился в новом сообщении).
- `TestVatRateFullPath`: строки вида `ws.cell(row=9, column=10, value=
  "Предлагаемое количество")` и ручные суффиксы в 9-й строке УДАЛИТЬ — шапку
  и суффикс `с учетом НДС 20%` даёт строитель. Тесту
  `test_vat_rate_warning_reaches_parse_result_warnings` собрать лист
  `gp_sheet(KEYS_GP_11, vat_suffix=None)`: заголовки групп без ставки — тип
  группы распознан (замеренное написание), а `read_label_rate` ставку не
  находит → то же предупреждение. `test_declared_zero_rate...` — строитель с
  `vat_suffix=", с учетом НДС 0%"`. `test_vat_rate_survives_a_header_row_shift`
  — `gp_sheet(KEYS_GP_11, header_row=8)` (строитель кладёт и A–D, и блок в
  сдвинутую строку; отдельные затирания больше не нужны).
  `test_vat_rate_found_at_measured_offsets_for_every_supported_width` —
  параметризацию заменить на `COLUMNS_BY_WIDTH` (8, 9, 10, 11, 12): суффикс
  ставит строитель в якорь группы, тест утверждает `vat_rate == "20"` на
  каждой ширине. `test_vat_rate_not_found_when_suffix_uses_wrong_offset_formula`
  — УДАЛИТЬ: его место занимает пермутационный тест задачи 4 (якоря следуют
  раскладке, а не формуле от ширины).
- `_sheet_with_summary_rows` — без изменений логики (наследует новый
  `_minimal_sheet(11)`).
- Хвостовые правки докстрок класса `TestParseEstimateFailures` (граница отказа
  теперь: геометрия — `_validate_contractor_geometry`, семантика —
  `resolve_contractor`).

Run: `cd backend && uv run pytest tests/unit/parser/test_estimate.py -q`
Expected: PASS (при наличии `samples/` — вместе с TestParseEstimateOnRealSamples:
все три верхнеуровневые оферты — ширина 11, их поведение не изменилось).

- [x] **Step 13: переписать нижние тесты**

- `test_parse_contractor_row.py`: `_sheet_with_row(values)` дополнительно
  строит `resolved(10, columns)` из `sheet_builders`; параметризации по
  `colspan` → по кортежам `COLUMNS_BY_WIDTH`; литеральные эталоны
  `test_column_lift_keeps_the_positional_layout` обновить: **для ширины 10
  ожидание меняется** — десятая колонка теперь `comment_contractor: 19`
  (число, не строка: комментарий не денежный ключ), ключа
  `total_cost_for_organizer_quantity` нет; для 9 и 11 эталоны прежние; добавить
  случай `KEYS_12` с `deviation_from_baseline_cost: "21"` (строка — Р2).
  `test_unsupported_colspan_raises_value_error` УДАЛИТЬ (понятия нет).
  Класс `TestMoneyGroupOffsets` НЕ трогать — его снесёт задача 5 вместе с
  функцией.
- `test_get_items_dict.py`: переписать вокруг `get_items_dict(layout)`:
  общие поля всегда; состав по `KEYS_8/9/10/GP_11/TENDER_11/12` (для 10 —
  `comment_contractor` есть, организатора НЕТ — это смена ожиданий, прежний
  тест `test_colspan_10_adds_suggested_quantity_and_organizer_total` заменяется);
  `deviation` присутствует только у раскладок с «% от р/с»; независимость
  копий блоков стоимости — сохранить; классы `InvalidColspan` удалить;
  `test_template_matches_parse_contractor_row_keys` сохранить, строя оба через
  один `resolved(...)`.
- `test_get_lot_positions.py` и `test_get_summary.py`: константу
  `CONTRACTOR = {...}` заменить на
  `CONTRACTOR = resolved(9, KEYS_8)` (позиции колонок 9–16 сохраняются — все
  прежние литералы эталонов остаются верными);
  `test_invalid_contractor_structure` — геометрия без `column_start`:
  `ResolvedContractor(geometry={"merged_shape": {"colspan": 8}}, layout=CONTRACTOR.layout)`
  → ожидать `KeyError`.
- `test_get_proposals.py`: фикстура `sample_contractors_data` → список из ДВУХ
  `resolved(...)` (без ячейки-маркера; `value`, `row_start`, `coordinate` —
  через `geometry_extra`); `_patch_collaborators` теряет патч
  `read_contractors`; все вызовы получают `contractors=...`;
  `test_returns_empty_dict_when_no_contractors` → передача `contractors=[]`;
  `test_skips_first_contractor_entry` УДАЛИТЬ (маркера в списке больше нет);
  `test_handles_missing_contractor_fields` переписать: геометрия без
  `row_start` (ИНН/адрес не читаются, предложение собирается);
  докстроку модуля дополнить: `header_row` и `contractors` приходят сверху.
- `test_performance.py`: в `_build_estimate_sheet` ручную шапку блока заменить
  вызовом `add_contractor_block(ws, col_start=10, columns=KEYS_GP_11)` (плюс
  прежние ИНН/адрес в J7/J8); строки с `ws["K9"]`/`ws["O9"]`/`ws["J10"]`
  убрать. Замеряемые свойства не меняются.
- `tests/integration/test_import_fixture_e2e.py`:

```python
FIXTURE_CONTRACTOR = ResolvedContractor(
    geometry={"column_start": 10, "merged_shape": {"colspan": 11}},
    layout=BlockLayout(column_keys=GP_11_COLUMN_KEYS, unit_cost_offset=1, total_cost_offset=5),
)
```

  где `GP_11_COLUMN_KEYS` — литеральный кортеж одиннадцати ключей прямо в этом
  файле (эталон независим от sheet_builders, комментарий: «замер плана фичи»);
  импорт `from parser.resolve_contractor import BlockLayout, ResolvedContractor`.

Run: `cd backend && uv run pytest tests/unit/parser tests/unit/test_additional_works.py -q`
Expected: PASS.

- [x] **Step 14: полный юнит-прогон**

Run: `cd backend && uv run pytest tests/unit -q`
Expected: PASS.

- [x] **Step 15: Commit**

```bash
git add backend/parser backend/tests
git commit -m "feat(parser-columns): раскладка разрешается один раз и доходит до строки объектом"
```

**Точка ревью** (обязательная, задача самая большая): сверить с §2.5 порядок
вызовов, отсутствие `read_contractors` в `get_proposals`, отсутствие
дублирования контракта, byte-совместимость шаблона ширины 11.

---

## Task 4: Сквозные доказательства спеки §6

Новые тесты поверх готовой проводки: перестановка через весь `parse_worksheet`,
многоподрядный лист, судьба «% от р/с» с валидной и пустой расчётной
стоимостью (DoD 7, 8).

**Files:**
- Test: `backend/tests/unit/parser/test_estimate.py` (новые классы)
- Test: `backend/tests/integration/test_estimate_import.py` (класс
  `TestWidthTenReachesImportCleanly` — путь до `ImportOutcome.warnings` и
  `PositionItem`)
- Modify: `backend/tests/unit/parser/sheet_builders.py` (если понадобится
  параметр `title` у `gp_sheet` — прокинуть в `add_contractor_block`, он уже есть)

**Interfaces:**
- Consumes: всё из задач 2–3; `BASELINE_MISSING_TITLE`
  (`parser/postprocess.py`), `_positions`/`_proposal` (test_estimate.py).

- [x] **Step 1: перестановка колонок при прежнем colspan — сквозной тест**

```python
class TestPermutedColumnsEndToEnd:
    """Спека §6: синтетическая фикстура перестановки, собранная кодом. Два
    яруса, горизонтальные и вертикальные объединения, прежний colspan 11 и
    РАЗЛИЧИМЫЙ маркер в каждой физической колонке — иначе тест не отличит
    правильный разбор от совпадения."""

    def test_every_marker_lands_under_its_own_key(self):
        ws = gp_sheet(KEYS_PERMUTED_11)
        ws.cell(row=12, column=1, value=2)
        ws.cell(row=12, column=2, value="1")
        ws.cell(row=12, column=4, value="Работа")
        # Маркеры: физическая колонка 10+i несёт значение 100+i (деньги и
        # количество) либо строку-маркер (комментарий).
        markers = ["маркер-комментарий", 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
        for offset, marker in enumerate(markers):
            ws.cell(row=12, column=10 + offset, value=marker)

        result = parse_worksheet(ws)
        position = _positions(result)["2"]

        assert position["comment_contractor"] == "маркер-комментарий"
        assert position["total_cost"] == {
            "materials": "101", "works": "102", "indirect_costs": "103", "total": "104",
        }
        assert position["total_cost_for_organizer_quantity"] == "105"
        assert position["unit_cost"] == {
            "materials": "106", "works": "107", "indirect_costs": "108", "total": "109",
        }
        assert position["suggested_quantity"] == 110

    def test_vat_anchors_follow_the_permuted_layout(self):
        """Ставка найдена в якорях ПЕРЕСТАВЛЕННЫХ групп — доказательство, что
        смещения вычисляются раскладкой, а не формулой от ширины (замена
        прежнего test_vat_rate_not_found_when_suffix_uses_wrong_offset_formula)."""
        ws = gp_sheet(KEYS_PERMUTED_11)
        result = parse_worksheet(ws)
        assert _proposal(result)["vat_rate"] == "20"
```

- [x] **Step 2: прогнать, увидеть зелёное; затем снятие защиты**

Run: `cd backend && uv run pytest tests/unit/parser/test_estimate.py::TestPermutedColumnsEndToEnd -v`
Expected: PASS.

**Снятие** (правка вносится и возвращается ТОЧНОЙ обратной правкой, результат
проверяется прогоном, не глазами): в `resolve_contractor` временно заменить
строку `keys.append(key)` на `keys.append(KEYS_CANONICAL_FALLBACK[offset])`,
где рядом временно объявить
`KEYS_CANONICAL_FALLBACK = [...канонический порядок ГП 11...]` — это и есть
старая болезнь «смысл по месту». Expected: оба теста класса красные, при этом
`TestMeasuredLayouts` в test_resolve_contractor остаётся зелёным (канон
совпадает с местом) — значит перестановку стережёт именно этот тест. Вернуть
обе строки, прогнать снова — зелёное.

- [x] **Step 3: многоподрядный лист (DoD 7) и валидная расчётная стоимость**

```python
class TestMultiContractorSheet:
    def test_two_blocks_parse_with_a_count_warning(self):
        ws = gp_sheet(KEYS_GP_11)                                  # J..T
        add_contractor_block(ws, col_start=22, columns=KEYS_12, title='ООО "Тест-2"')
        result = parse_worksheet(ws)
        proposals = result.data["lots"]["lot_1"]["proposals"]
        assert list(proposals) == ["contractor_1", "contractor_2"]
        assert any("ожидается один подрядчик, найдено 2" in w for w in result.warnings)

    @staticmethod
    def _tender_sheet_with_baseline(*, baseline_total: float | None):
        """Тендерный лист: подрядчик KEYS_TENDER_11 (J..T) + базовый блок
        KEYS_8 (V..AC), позиция с «% от р/с» и блок итогов.

        baseline_total кладётся в колонку total_cost.total БАЗОВОГО блока
        (22+7=29): ненулевое значение делает базу валидной для
        postprocess._is_baseline_valid; None — база пуста.
        """
        ws = gp_sheet(KEYS_TENDER_11)
        add_contractor_block(ws, col_start=22, columns=KEYS_8,
                             title="Расчетная стоимость", vat_suffix=None)
        ws.cell(row=12, column=1, value=2)
        ws.cell(row=12, column=2, value="1")
        ws.cell(row=12, column=4, value="Работа")
        ws.cell(row=12, column=20, value=-0.05)   # % от р/с — 11-я колонка блока
        # Объединённая ячейка в колонке A — конец блока позиций (AGENTS.md §11).
        ws.merge_cells(start_row=14, start_column=1, end_row=14, end_column=5)
        ws.cell(row=14, column=1, value="ИТОГО, руб. с учетом НДС")
        if baseline_total is not None:
            ws.cell(row=14, column=29, value=baseline_total)
        return ws

    def test_deviation_reaches_json_when_the_baseline_is_valid(self):
        """% от р/с доезжает до позиции (§2.8) — при живой расчётной стоимости
        postprocess его не трогает."""
        ws = self._tender_sheet_with_baseline(baseline_total=100.0)

        result = parse_worksheet(ws)

        lot = result.data["lots"]["lot_1"]
        assert lot["baseline_proposal"]["title"] == "Расчетная стоимость"
        position = lot["proposals"]["contractor_1"]["contractor_items"]["positions"]["2"]
        assert position["deviation_from_baseline_cost"] == "-0.05"

    def test_empty_baseline_cleans_deviations_with_the_base(self):
        """DoD 8: postprocess.py не менялся, и его правило «нет валидной базы —
        нет осмысленного отклонения» покрыто на файле с ПУСТОЙ расчётной
        стоимостью: тот же лист, но без итога базового блока."""
        ws = self._tender_sheet_with_baseline(baseline_total=None)

        result = parse_worksheet(ws)

        lot = result.data["lots"]["lot_1"]
        assert lot["baseline_proposal"]["title"] == BASELINE_MISSING_TITLE
        position = lot["proposals"]["contractor_1"]["contractor_items"]["positions"]["2"]
        assert "deviation_from_baseline_cost" not in position
```

Перед реализацией свериться с фактическим поведением
`postprocess._separate_proposals` (как он находит базовое предложение по
`TABLE_PARSE_BASELINE_COST`) — тест подстраивается под фактическое имя
итоговой метки/структуры, НЕ меняя postprocess. Если для валидности базы нужны
итоговые строки в самом блоке summary — добавить их листу, как в
`_sheet_with_summary_rows`.

- [x] **Step 4: прогнать класс, затем весь набор**

Run: `cd backend && uv run pytest tests/unit/parser -q`
Expected: PASS.

- [x] **Step 5: путь парсер → импорт для ширины 10 — настоящий `import_estimate`**

Вывод «ключа нет → предупреждению не из чего родиться» правдоподобен, но
поведение `import_jobs.warnings` он не исполняет. Исполняем настоящим импортом:
инфраструктура готова в `tests/integration/test_estimate_import.py` —
`run_import` (строка 59) зовёт `import_estimate` и возвращает `ImportOutcome`
с тем самым списком `warnings`, который роутер пишет в `import_jobs.warnings`.
Новый класс в `tests/integration/test_estimate_import.py`:

```python
class TestWidthTenReachesImportCleanly:
    """Третий пункт дельты класса 2 (спека §7): ложное «значение не число»
    исчезло. Полный путь: синтетический лист ширины 10 → parse_worksheet →
    import_estimate → ImportOutcome.warnings и сохранённый PositionItem.
    До фичи комментарий лежал под денежным ключом, _money падал на тексте и
    писал предупреждение на каждую строку с комментарием (спека §1.1)."""

    @staticmethod
    def _width_ten_payload():
        ws = gp_sheet(KEYS_10)
        ws.cell(row=12, column=1, value=2)
        ws.cell(row=12, column=2, value="1")
        ws.cell(row=12, column=4, value="Работа")
        ws.cell(row=12, column=19, value="таймлайн уточним")  # 10-я колонка блока
        return parse_worksheet(ws).data

    def test_no_false_warning_and_the_comment_lands_in_the_row(
        self, db_session, factories, resolver
    ):
        contract = factories.ContractFactory.create()
        db_session.flush()

        outcome = run_import(db_session, resolver, contract, self._width_ten_payload())

        assert not any("не число" in w for w in outcome.warnings), outcome.warnings
        item = db_session.execute(
            sa.select(PositionItem).where(PositionItem.job_title_in_proposal == "Работа")
        ).scalar_one()
        assert item.comment_contractor == "таймлайн уточним"
        assert item.total_cost_for_organizer_quantity is None
```

Утверждение про `warnings` — «нет НИ ОДНОГО „не число“», а не «список пуст»:
синтетический лист без шапки документа даёт законные предупреждения сверки
реквизитов и раскладки, они к проверяемому пути отношения не имеют. Импорты в
шапку файла: `from parser import parse_worksheet`,
`from tests.unit.parser.sheet_builders import KEYS_10, gp_sheet` — паттерн
`from tests....` в integration-тестах уже принят (`tests.payloads`,
`tests.comparison_fixtures`). Код сервиса НЕ меняется.

Run: `cd backend && uv run pytest tests/integration/test_estimate_import.py -k WidthTenReachesImport -v`
(нужен `TEST_DATABASE_URL`; в `just ci` входит)
Expected: PASS.

Контроль наблюдаемости — юнит-тестом рядом, в
`tests/unit/parser/test_estimate.py` (он вторичен и основной тест не заменяет):

```python
class TestOldWidthTenShapeWasNoisy:
    def test_the_old_shape_did_produce_the_false_warning(self):
        """Старая форма позиции (комментарий под денежным ключом) на том же
        пути значений даёт ровно то предупреждение, чьё исчезновение утверждает
        интеграционный тест. Без контроля пустой список мог бы означать
        «смотреть было нечем» (docs/insights/unobservable-in-the-runner.md)."""
        from services.estimate_import import _money

        value_problems: list[str] = []
        result = _money("таймлайн уточним", value_problems, "позиция «2»")

        assert result is None
        assert value_problems == [
            "позиция «2»: значение «таймлайн уточним» не число, записано NULL"
        ]
```

Run: `cd backend && uv run pytest tests/unit/parser/test_estimate.py -k OldWidthTenShape -v`
Expected: PASS.

- [x] **Step 6: снятие защиты предупреждения о лишнем ключе**

В `check_estimate_layout` временно удалить две строки
`if extra: parts.append(...)`. Expected:
`test_twelve_wide_block_parses_with_an_extra_key_warning` красный (задача 3),
остальное зелёное. Вернуть, прогнать — зелёное. Так доказано, что «лишний
deviation — тоже отличие» стережёт именно тест, а не совпадение.

- [x] **Step 7: Commit**

```bash
git add backend/tests/unit/parser backend/tests/integration/test_estimate_import.py
git commit -m "test(parser-columns): перестановка, многоподрядный лист, % от р/с, путь до импорта"
```

---

## Task 5: Исчезновение четырёх сущностей (DoD 1)

Потребителей больше нет — проверить grep-ом ДО удаления, удалить, проверить
grep-ом ПОСЛЕ.

**Files:**
- Modify: `backend/parser/parse_contractor_row.py` (−`SUPPORTED_CONTRACTOR_COLSPANS`,
  −`get_column_keys`, −`money_group_offsets`)
- Modify: `backend/parser/layout.py` (−`GP_CONTRACTOR_COLSPAN`,
  −`find_suggested_quantity_header`, −мёртвые импорты)
- Test: `backend/tests/unit/parser/test_parse_contractor_row.py`
  (−`TestMoneyGroupOffsets`, −импорты удалённого)

- [x] **Step 1: убедиться, что потребителей нет**

Run (из корня):
`grep -rn "SUPPORTED_CONTRACTOR_COLSPANS\|GP_CONTRACTOR_COLSPAN\|money_group_offsets\|find_suggested_quantity_header\|get_column_keys" backend --include=*.py`
Expected: только объявления в двух файлах и их тесты/докстроки — ни одного
вызова из живого кода. Любой неожиданный потребитель — вернуться в задачу 3.

- [x] **Step 2: удалить код и тесты**

Вместе с функциями удалить их упоминания в докстроках живых модулей
(`get_items_dict`, `get_proposals`, `layout`, `estimate` — задача 3 уже
переписала большинство; здесь добить остаточные).

- [x] **Step 3: grep DoD 1**

Run: `grep -rn "SUPPORTED_CONTRACTOR_COLSPANS\|GP_CONTRACTOR_COLSPAN\|money_group_offsets\|find_suggested_quantity_header" backend`
Expected: пусто. (Исторические `docs/` не считаются: DoD говорит про код.)

- [x] **Step 4: прогнать всё**

Run: `cd backend && uv run pytest tests/unit -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend
git commit -m "refactor(parser-columns): ширина больше не несёт смысла — четыре сущности удалены"
```

---

## Task 6: Регрессия по классам §7 и независимый замер сводной

Снимок «после» тем же скриптом, сравнение по четырём классам, независимая
сверка сводной таблицы по листу. Результаты — в devlog.

**Files:**
- Create: `backend/scripts/compare_parse_snapshots.py`
- Create: `backend/scripts/verify_summary_sheet.py`
- Create: `docs/devlog/2026-08-25-parser-columns-by-header.md` (раздел «Регрессия»)
- Вывод: `samples/_snapshots/after/*.json`

**Interfaces:**
- Consumes: снимки задачи 1; формат снимка из `snapshot_parse_samples.py`.

- [ ] **Step 1: снимок «после»**

Прогонять на ЗАКОММИЧЕННОМ дереве (после коммита задачи 5): manifest снимает
`git rev-parse HEAD:backend/parser` и `dirty`, грязный снимок сравнение
отвергнет. Метку `before` не трогать — скрипт и сам откажется писать в неё без
`--force`, но `--force` на `before` законен только для пересдачи эталона с
кода базового коммита.

Run: `cd backend && PYTHONIOENCODING=utf-8 uv run python -m scripts.snapshot_parse_samples after`
Expected: `ok=22 error=5` — сводная таблица перешла из отказа в разбор;
в `manifest.json` метки `after`: `"parser_version": "4.0.0"`, `"dirty": false`,
`"count": 27`.

- [ ] **Step 2: скрипт сравнения**

`scripts/compare_parse_snapshots.py <before> <after>` (метки каталогов).
Правила классификации на каждый дайджест — зеркало §7:

```python
"""Сравнение снимков «до/после» по четырём классам спеки §7.

Никаких имён файлов в выводе — только дайджесты и счётчики. parser_version
сравнению не подлежит (3.1.0 → 4.0.0 — ожидаемая смена). Побайтность — это
равенство сериализаций json.dumps(data, ensure_ascii=False, indent=1), то есть
той же формы, в которой снимки записаны.

Запуск из backend/:
    PYTHONIOENCODING=utf-8 uv run python -m scripts.compare_parse_snapshots before after
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS_DIR = REPO_ROOT / "samples" / "_snapshots"
EXPECTED_COUNTS = {"class1": 18, "class2": 3, "class3": 1, "class4": 5}

#: База фичи: эталон «до» обязан быть снят деревом парсера ЭТОГО коммита,
#: а не тем, что окажется в origin/main на момент сравнения.
BASELINE_COMMIT = "310c6bf3db20528fb1e1ce85de963b1bafdd4daf"
BASELINE_PARSER_VERSION = "3.1.0"
AFTER_PARSER_VERSION = "4.0.0"


def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


def _git_tree(rev: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", f"{rev}:backend/parser"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()


def check_manifests(before_dir: Path, after_dir: Path) -> list[str]:
    """Происхождение снимков. Любая строка в ответе — отказ от сравнения:
    сравнивать подделанный или пересданный не тем кодом эталон бессмысленно.

    «До» привязан к дереву парсера БАЗОВОГО коммита, «после» — к дереву
    парсера ТЕКУЩЕГО HEAD: иначе старый снимок «после» от другого дерева с
    той же версией 4.0.0 прошёл бы проверку, и сравнение говорило бы не о
    проверяемой реализации.
    """
    problems: list[str] = []
    before = json.loads((before_dir / "manifest.json").read_text(encoding="utf-8"))
    after = json.loads((after_dir / "manifest.json").read_text(encoding="utf-8"))
    if before["parser_tree"] != _git_tree(BASELINE_COMMIT):
        problems.append("эталон «до» снят не деревом базового коммита")
    if after["parser_tree"] != _git_tree("HEAD"):
        problems.append("снимок «после» снят не деревом текущего HEAD — пересдать")
    if before["parser_version"] != BASELINE_PARSER_VERSION:
        problems.append(f"версия «до» {before['parser_version']} ≠ {BASELINE_PARSER_VERSION}")
    if after["parser_version"] != AFTER_PARSER_VERSION:
        problems.append(f"версия «после» {after['parser_version']} ≠ {AFTER_PARSER_VERSION}")
    if before["dirty"] or after["dirty"]:
        problems.append("снимок снят при грязном backend/parser")
    if before["digests"] != after["digests"]:
        problems.append("наборы файлов образцов в снимках различаются")
    if before["count"] != len(before["digests"]) or after["count"] != len(after["digests"]):
        problems.append("count манифеста расходится со списком дайджестов")
    return problems


def rename_organizer_to_comment(node):
    """Единственная законная дельта класса 2: ключ
    total_cost_for_organizer_quantity становится comment_contractor НА ТОМ ЖЕ
    МЕСТЕ словаря. Любое расхождение сверх этого — нарушение."""
    if isinstance(node, dict):
        return {
            ("comment_contractor" if key == "total_cost_for_organizer_quantity" else key):
                rename_organizer_to_comment(value)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [rename_organizer_to_comment(item) for item in node]
    return node

def classify(before, after) -> str:
    if before["status"] == "error" and after["status"] == "error":
        return "class4" if before["error"] == after["error"] else "VIOLATION"
    if before["status"] == "error" and after["status"] == "ok":
        return "class3"
    if before["status"] == "ok" and after["status"] == "error":
        return "VIOLATION"
    if dumps(before["data"]) == dumps(after["data"]):
        return "class1" if before["warnings"] == after["warnings"] else "VIOLATION"
    if dumps(rename_organizer_to_comment(before["data"])) == dumps(after["data"]):
        removed = [w for w in before["warnings"] if w not in after["warnings"]]
        added = [w for w in after["warnings"] if w not in before["warnings"]]
        ok = all("колонок вместо" in w for w in removed) and all(
            "не совпадает с ожидаемым" in w for w in added
        )
        return "class2" if ok else "VIOLATION"
    return "VIOLATION"
```

`main()`: сначала `check_manifests` — любая проблема происхождения печатается
и даёт `exit 1` ДО сравнения данных; затем пройти ОБЪЕДИНЕНИЕ дайджестов двух
каталогов (файл, есть только с одной стороны, — VIOLATION), классифицировать
каждую пару, напечатать счётчики по классам и список дайджестов на класс.
`exit 1`, если счётчики ≠ `EXPECTED_COUNTS` или есть хоть один VIOLATION.
Ложно-зелёный прогон исключается формой запуска: успех читается по коду
возврата ОДИНОЧНОЙ команды, без конвейеров
(`docs/insights/silent-test-runs.md`).

Комментарий в скрипте — почему трёхпунктная дельта класса 2 сводится к
переименованию: значение прежнего ключа (текст комментария) в «до» прошло через
`money_to_json` (текст возвращается как есть), в «после» — читается как есть,
поэтому значения совпадают. Третий пункт дельты — исчезновение ложных
«значение не число» — этим сравнением не доказывается: его ИСПОЛНЯЕТ
`TestWidthTenReachesImportCleanly` (задача 4, шаг 5) настоящим
`import_estimate` — от листа ширины 10 до `ImportOutcome.warnings` и
сохранённого `PositionItem`. Если у какого-то файла в колонке
комментария лежит ЧИСЛО (в «до» — десятичная строка, в «после» — число), скрипт
покажет VIOLATION — тогда расхождение разобрать руками и записать в devlog как
находку, прежде чем ослаблять правило.

- [ ] **Step 3: прогнать сравнение**

Run: `cd backend && PYTHONIOENCODING=utf-8 uv run python -m scripts.compare_parse_snapshots before after`
Expected: `class1=18 class2=3 class3=1 class4=5`, код возврата 0
(проверить `echo $?` отдельной командой).

- [ ] **Step 4: независимый замер сводной таблицы**

`scripts/verify_summary_sheet.py` — находит в `samples/` файл класса 3 (тот,
чей снимок «до» был отказом на ширине 12; дайджест берёт из вывода сравнения
либо аргументом). Замер идёт **двумя этапами**, потому что итоговый JSON
пяти блоков не несёт: postprocess заменяет пустой baseline заглушкой (его
позиции из JSON исчезают) и вычищает отклонения подрядчиков, хотя физическая
колонка «% от р/с» на листе есть.

**Этап A — до postprocess, все пять блоков поколоночно.** Скрипт собирает
СЫРЫЕ лоты той же цепочкой, что `parse_worksheet`, но останавливается ДО
`normalize_lots_json_structure`: `read_contractors` → `find_lot_starts` →
`_validate_column_headers` → `resolve_contractor` на каждый блок →
`read_lots_and_boundaries(...)` — и берёт `lots.lots` как есть. Эталон при этом
НЕЗАВИСИМ от резолвера: физическая раскладка пяти блоков записана в скрипт
литералами из замера плана (`col_start` 10/19/31/44/57, `header_row` 11, первая
строка данных 13, ключи колонок каждого блока — литеральные кортежи, включая
«% от р/с» у блоков 19/31/44/57). Для первых трёх и последних двух строк
позиций КАЖДОГО из пяти блоков каждое значение JSON сверяется с сырым чтением
`ws.cell(...)` по этим литеральным координатам; плюс счётчик позиций на блок
одинаков у всех пяти.

Эталонное преобразование денежной ячейки (и «% от р/с») обязано различать три
случая — ДО postprocess Excel-ошибки ещё живы, и `Decimal("#DIV/0!")` уронил
бы сам замер, а не найденное расхождение (в блоке «% от р/с» пустой базы стоит
именно `#DIV/0!`, спека §2.8):

```python
def expected_money_cell(value):
    """Зеркало контракта money_to_json на стороне ЭТАЛОНА: число → десятичная
    строка, пусто/bool/нечисловые nan-inf → None, текст (включая Excel-ошибки
    вида '#DIV/0!') — исходная строка как есть. Независимость замера — в
    КООРДИНАТАХ (литералы листа против раскладки резолвера), а не в кодировке
    значений: её контракт один на проект (AGENTS.md §3)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(Decimal(value))
    if isinstance(value, float):
        return str(Decimal(str(value))) if math.isfinite(value) else None
    return value
```

Неденежные колонки (`suggested_quantity`, комментарий) сверяются сырым
равенством без преобразования.

**Этап B — после `parse_estimate`, факты постобработки.** Полный прогон того же
файла:

1. `proposals` = `contractor_1..4` — восьмиколоночный блок озаглавлен в
   точности «Расчетная стоимость» (замер плана) и отделён
   `postprocess._separate_proposals` (сравнение там ТОЧНОЕ, не по префиксу);
2. блок пуст, поэтому `baseline_proposal["title"]` ожидается равным
   `BASELINE_MISSING_TITLE` — предпосылка утверждается внутри прогона,
   фактическая форма записывается в devlog;
3. `vat_rate == "22"` у всех четырёх предложений (шапки 22%);
4. `deviation_from_baseline_cost` на позициях ОТСУТСТВУЕТ — колонка на листе
   есть (этап A её прочитал), вычистило её именно правило «нет валидной базы»
   (спека §2.8);
5. предупреждения содержат «найдено 5» (число блоков с базовым — DoD 7).

Оба этапа печатают счётчики сверенных ячеек; `exit 1` при любом расхождении.

Run: `cd backend && PYTHONIOENCODING=utf-8 uv run python -m scripts.verify_summary_sheet`
Expected: код возврата 0; счётчики в devlog.

- [ ] **Step 5: пункт 2 этапа B уточнить по факту**

Ожидание «baseline пуст → заголовок стал `BASELINE_MISSING_TITLE`» — предпосылка,
проверяемая внутри самого прогона (docs/insights/false-test-premises.md):
скрипт утверждает фактическую форму, а не молчит. Что бы ни оказалось — записать
в devlog.

- [ ] **Step 6: devlog — раздел «Регрессия по классам»**

Числа: 27 файлов, счётчики классов, ok/error «до» и «после», счётчики
независимого замера. Только дайджесты и счётчики — ни имён, ни сумм.

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/compare_parse_snapshots.py backend/scripts/verify_summary_sheet.py \
  docs/devlog/2026-08-25-parser-columns-by-header.md
git commit -m "test(parser-columns): регрессия по четырём классам и независимый замер сводной"
```

---

## Task 7: Финал — devlog, `just ci`, PR

- [ ] **Step 1: postprocess не изменён (DoD 8)**

Run: `git diff origin/main -- backend/parser/postprocess.py`
Expected: пусто.

- [ ] **Step 2: соответствие «требование спеки → тест» перечитать списком**

Пройти таблицу «Соответствие DoD» ниже по фактическому дереву (не по памяти —
`docs/insights/replaying-new-rules.md`, слой 3): каждый названный тест
существует и зелёный. Требование без теста — либо тест, либо граница в devlog.

- [ ] **Step 3: devlog дописать**

Отступления от плана, находки, границы. Путь до `import_jobs.warnings` для
ширины 10 исполнен настоящим `import_estimate`
(`TestWidthTenReachesImportCleanly`, integration); если по ходу реализации
какая-то проверка осталась выводом, а не прогоном, — назвать её границей явно.

- [ ] **Step 4: `just ci`**

Run: `just ci` (из корня).
Expected: `OK: все проверки прошли`. Код возврата смотреть у самой команды,
не через конвейер.

- [ ] **Step 5: push и PR**

```bash
git push -u origin feat/parser-columns-by-header
```

PR: заголовок «Парсер: смысл колонок — из заголовков, а не из их числа»,
в описании ссылки на спеку и этот план, счётчики классов из devlog (без имён
файлов), пометка «postprocess.py не изменён; импорт многоподрядного файла —
фича 2».

---

## Соответствие DoD спеки задачам

Все девять пунктов §5 спеки.

| DoD | Где закрыт | Чем доказано |
|---|---|---|
| 1. Смысл нигде не из `colspan` | Задача 5 | grep трёх имён по `backend` пуст; `get_column_keys` удалён тем же шагом |
| 2. Раскладка один раз, объектом; `get_proposals` без `read_contractors` | Задача 3 | `TestResolutionFlow` (identity через spy на обоих звеньях); импорт удалён |
| 3. Контракт только в `resolve_contractor`; геометрия отдельно; `GP_EXPECTED_KEYS` поимённо; лишний deviation — отличие | Задачи 2, 3 | `TestRefusals`, `_validate_contractor_geometry` без семантики, `test_twelve_wide_block_parses_with_an_extra_key_warning` + снятие задачи 4 шаг 5 |
| 4. Три класса отказа с координатами и подписями | Задача 2 | `TestRefusals`: координата+обе подписи; обе координаты повтора; блок+ключ без координаты |
| 5. Совместимость по классам, сравнением | Задачи 1, 4, 6 | снимок «до» кодом базового коммита, «после» — деревом текущего HEAD (manifest + `--force`-защита, `check_manifests`); `compare_parse_snapshots` с жёсткими счётчиками 18/3/1/5 и точной формой дельты; третий пункт дельты исполняется `TestWidthTenReachesImportCleanly` настоящим `import_estimate` до `ImportOutcome.warnings` и `PositionItem` |
| 6. НДС 20/22/0/без ставки | Задачи 2, 3, 6 | `test_group_header_without_rate_still_types_the_group`; обновлённый `TestVatRateFullPath` (20%, 0%, без суффикса); класс 1 побайтно (20%, «20», без ставки в образцах); сводная — `vat_rate == "22"` в `verify_summary_sheet` |
| 7. Многоподрядный файл | Задачи 4, 6 | `TestMultiContractorSheet` (синтетика); сводная таблица: этап A `verify_summary_sheet` читает все пять блоков до postprocess, этап B — 4 предложения + базовый-заглушка и предупреждение «найдено 5» |
| 8. `postprocess.py` не изменён; пустая база вычищает отклонения | Задачи 4, 7 | `test_empty_baseline_cleans_deviations_with_the_base`; `git diff` пуст |
| 9. `just ci` зелёный | Задача 7 | прогон перед пушем |

Доказательства §6 спеки: пять раскладок — `TestMeasuredLayouts`; две ширины 11 —
там же (`KEYS_GP_11` и `KEYS_TENDER_11`); ширина 12 —
`test_twelve_wide_block_parses_with_an_extra_key_warning`; ширина 10 —
`TestWidthTenComment` + `TestWidthTenReachesImportCleanly` + класс 2 сравнения;
перестановка —
`TestPermutedColumnsEndToEnd` (+ снятие); отказы — `TestRefusals`; объединения —
`TestHeaderTiers` + рукописный `test_real_gp_geometry_built_by_hand`; ставка —
DoD 6; регрессия — задача 6.

---

## Порядок и точки ревью

Строго по номерам: 1 (эталон «до» — пока код старый) → 2 (ядро, ничего не
ломает) → 3 (сквозная передача — потребители словаря) → 4 (доказательства) →
5 (удаление — потребителей уже нет) → 6 (регрессия по классам) → 7 (финал).

Ревью после КАЖДОЙ задачи обязательно и приёмкой оркестратора не заменяется
(`AGENTS.md`, инсайт per-task-review). Отдельное внимание: после задачи 3 —
сверка потока §2.5 и byte-совместимость шаблона ширины 11; после задачи 6 —
счётчики классов против таблицы §7.

Параллелить нечего: 3 стоит на 2, 4 и 5 — на 3, 6 — на 1 и 5.
