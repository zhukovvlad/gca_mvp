# Ф4a: три независимые итоговые строки вместо коллизии ключей — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Цель:** блок итогов сметы перестаёт терять валовое ИТОГО: каждая строка блока
даёт свой ключ в JSON и свою запись в `proposal_summary_lines`.

**Архитектура:** чистое ядро + тонкая оболочка. Новый модуль
`parser/summary_block.py` не знает о `Worksheet` и содержит всё, что можно
проверить без файла: распознавание меток, инъективное назначение ключей,
конверсию денег и сверку арифметики, тексты предупреждений. `get_summary`
остаётся обходом листа и зовёт ядро. Предупреждения едут наверх новыми
возвращаемыми структурами (образец — `LotRows` из Ф2).

**Стек:** Python 3.12, openpyxl, pytest, `Decimal` end-to-end.

**Спека:** [2026-08-07-summary-vat-lines-design.md](../specs/2026-08-07-summary-vat-lines-design.md)
**Рамка фазы:** [phase7-frame.md](../../phase7-frame.md)
**Ветка:** `fix/summary-vat-lines` (уже создана; спека закоммичена в `771cd9b`)

## Global Constraints

- **Деньги — `Decimal`, никогда `float`** (`AGENTS.md` §3). В JSON деньги —
  строки; в тестах сравнивать `Decimal` с `Decimal`.
- **Миграции нет.** `alembic check` обязан остаться чистым; появление операций
  означает, что задача тронула схему вопреки спеке §2.9.
- **`parser/` не импортирует из `services/`** — направление зависимости проверено
  замером (спека §1.5). Константу предела примеров и хелпер усечения завести
  **в парсере** по образцу `services/category_resolution._examples`, не импортируя
  его.
- **Все предупреждения Ф4a — parser warnings** (`ParseResult.warnings`, сессия A).
  Доменных предупреждений фича не заводит ни одного.
- **Предупреждения агрегированные**: по одному на вид на блок, перечень внутри,
  усечение до `MAX_SUMMARY_WARNING_EXAMPLES = 5` с хвостом «…и ещё N».
- **Числа, которые нельзя «поправить»:** база тестов `1111` собранных,
  `1105 passed / 6 skipped`, vitest `175`; парсер — `264` собранных;
  fixture — `2576 / 746 / 222 / 485 / 39 / 38 / 16` и `EXPECTED_PRICED_ROWS`.
  Расхождение — повод разбираться, а не обновлять ожидание.
- **`samples/` не коммитится**; реальные суммы и реквизиты не попадают ни в код,
  ни в тесты, ни в доки, ни в сообщения коммитов. Суммы в fixture — синтетические.
- **Кириллица в терминал не печатается** исполнителями: вердикты ASCII, русский
  текст — в файл, `grep -c` вместо `grep`, `Read` вместо `cat`. Дочерний python —
  с префиксом `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`.
- Команды бэкенда — из `backend/` через `uv run`; системный python не вызывать.
  Разовый скрипт — **файлом**, не через `python -c`. Git — из корня репозитория.
- Точечный прогон integration: `TEST_DATABASE_URL` обязателен, `DATABASE_URL`
  снимать (`env -u DATABASE_URL`). Успех читать по числу `passed`, а не по коду
  возврата ([silent-test-runs.md](../../insights/silent-test-runs.md)).
- ruff `line-length = 120`, target py312. Комментарии и docstring — по-русски,
  как в окружающем коде.

## Состояние на момент написания плана (замерено, не пересказано)

- `main` = `79e2f75`, ветка `fix/summary-vat-lines` создана; в ней два коммита
  документации: спека `ef8093b` и её правки `0863dae`, `771cd9b`. Кода фича ещё
  не касалась.
- `PARSER_VERSION = "2.0.0"` — [estimate.py:60](../../../backend/parser/estimate.py#L60).
  Версию **не читает ни одна строка кода**: только запись в
  `estimate_raw_data.parser_version` и тест.
- `get_summary` возвращает голый `dict`; продакшен-вызов **один** —
  [get_proposals.py:87](../../../backend/parser/get_proposals.py#L87). Выше по
  цепочке `read_lots_and_boundaries` вызывается в
  [estimate.py:261](../../../backend/parser/estimate.py#L261) **внутри
  dict-литерала** — его придётся разобрать на две инструкции.
- В `tests/unit/parser/test_get_proposals.py`: **8** патчей
  `parser.get_proposals.get_summary` и **15** мест, где результат
  `get_proposals(...)` присваивается (ещё 6 вызовов результат не разбирают).
- Имя `total_cost_with_vat` живёт в **6** местах: `constants.py:86`,
  `get_summary.py` (импорт и присваивание), `payloads.py:195`,
  `test_estimate.py:207`, `test_estimate_import.py:158`.
- `_import_summary` ([estimate_import.py:649](../../../backend/services/estimate_import.py#L649))
  обходит ключи словаря механически — при трёх ключах даст три записи **без
  единой правки**. Схема тоже: `summary_key` — свободный `Text`,
  `UNIQUE (proposal_id, summary_key)`, CHECK на значения нет.
- `MAX_WARNING_EXAMPLES` и `_examples()` живут в
  [category_resolution.py:43](../../../backend/services/category_resolution.py#L43) —
  это **services**, а `parser` из `services` не импортирует ничего (замерено).
  Ф4 свой модуль оттуда импортировала; Ф4a так сделать **не может** и заводит
  собственную константу с собственным именем.
- Блок итогов fixture до правки: строки 2588–2589, `max_row = 2601`,
  merged-диапазонов 2614; строка НДС несёт **одну** непустую ячейку — метку.
- Парсерных тестов до фичи — **264** собранных.

## Структура файлов

| Файл | Ответственность |
|---|---|
| `backend/parser/summary_block.py` | **создаётся.** Чистое ядро: распознавание меток, инъективные ключи, конверсия денег, сверка арифметики, сборка предупреждений. Ни `Worksheet`, ни `openpyxl`. |
| `backend/parser/constants.py` | три новых ключа JSON, три константы меток; два старых ключа удаляются |
| `backend/parser/get_summary.py` | только обход листа: найти блок, собрать строки, отдать их ядру |
| `backend/parser/get_proposals.py` | пробрасывает предупреждения предложения наверх |
| `backend/parser/read_lots_and_boundaries.py` | пробрасывает предупреждения лотов наверх |
| `backend/parser/estimate.py` | собирает предупреждения с устранением дублей; `PARSER_VERSION = "3.0.0"` |
| `backend/tests/unit/parser/test_summary_block.py` | **создаётся.** Юнит-тесты ядра без листа и без файла |
| `backend/tests/unit/parser/test_get_summary.py` | **создаётся.** Обход листа на синтетических листах |
| `backend/tests/unit/parser/test_get_proposals.py` | 8 моков `get_summary` и 15 разборов результата |
| `backend/tests/unit/parser/test_estimate.py` | имена ключей, версия парсера, отсутствие предупреждений на fixture |
| `backend/tests/payloads.py` | синтетические payload'ы на новых именах |
| `backend/tests/integration/test_estimate_import.py` | три записи `proposal_summary_lines` |
| `backend/tests/integration/test_import_fixture_e2e.py` | E2E: три строки → три ключа → три записи |
| `fixtures/gp_estimate_fixture.xlsx` | блок итогов доводится до трёхстрочной формы |
| `docs/devlog/2026-08-07-summary-vat-lines.md` | **создаётся** на финале |

---

## Task 1: Fixture — блок итогов до трёхстрочной формы

Правка идёт **первой и без изменений кода**: после неё закоммиченный вход
воспроизводит дефект (три строки листа → два ключа JSON), и следующие задачи
получают честный красный прогон. Все существующие тесты обязаны остаться
зелёными: старый `get_summary` на трёхстрочном блоке по-прежнему отдаёт два
ключа с теми же именами, а ни один тест не утверждает **значение**
`total_cost_with_vat`.

**Files:**
- Modify: `fixtures/gp_estimate_fixture.xlsx`
- Test: `backend/tests/integration/test_import_fixture_e2e.py`

**Interfaces:**
- Consumes: ничего
- Produces: fixture с блоком итогов из трёх строк в строках 2588–2590; пустая
  строка-терминатор уезжает на 2591, «Дополнительная информация:» — на 2592.
  Метки: `ИТОГО, руб. с учетом НДС`, `В том числе НДС`, `ИТОГО, руб. без учета НДС`.

- [ ] **Шаг 1: замерить исходное состояние блока**

Скрипт кладётся в scratchpad, в репозиторий не попадает. Запуск из `backend/`:

```python
# scratchpad/measure_fixture_before.py
from pathlib import Path
import openpyxl

wb = openpyxl.load_workbook(Path("..") / "fixtures" / "gp_estimate_fixture.xlsx", data_only=True)
ws = wb["Лист1"]
print("max_row", ws.max_row)
print("merged", len(ws.merged_cells.ranges))
for row in range(2585, 2595):
    label = ws.cell(row=row, column=1).value
    money = [ws.cell(row=row, column=c).value for c in range(15, 19)]
    covering = [str(r) for r in ws.merged_cells.ranges if r.min_row <= row <= r.max_row]
    print(row, repr(label), money, covering)
```

Ожидание (замер спеки §1.1): `max_row = 2601`, merged `2614`; строка 2588 —
валовая с четырьмя суммами и объединением `A2588:E2588`; 2589 — метка НДС без
сумм, объединение `A2589:E2589`; 2590 — пустая, объединений нет; 2591 —
«Дополнительная информация:», объединение есть.

- [ ] **Шаг 2: написать скрипт правки**

Порядок операций **не переставлять**: объединения снимаются **до** `insert_rows`,
иначе `unmerge_cells` ищет заглушки по устаревшим координатам и падает `KeyError`
(грабли Ф4, devlog Ф4 п. 7 отступлений).

```python
# scratchpad/edit_fixture.py
from decimal import Decimal
from pathlib import Path
import openpyxl
from openpyxl.worksheet.cell_range import CellRange   # submodule НЕ подтягивается `import openpyxl`

PATH = Path("..") / "fixtures" / "gp_estimate_fixture.xlsx"
GROSS_ROW, VAT_ROW, TERMINATOR_ROW = 2588, 2589, 2590
MONEY_COLS = (15, 16, 17, 18)

wb = openpyxl.load_workbook(PATH)          # без data_only: сохраняем книгу
ws = wb["Лист1"]

# 1. Читаем валовое и считаем НДС/безналоговое так, чтобы тождество сошлось ТОЧНО.
gross = {c: Decimal(str(ws.cell(row=GROSS_ROW, column=c).value)) for c in MONEY_COLS}
net = {c: (gross[c] / Decimal("1.2")).quantize(Decimal("0.01")) for c in MONEY_COLS}
vat = {c: gross[c] - net[c] for c in MONEY_COLS}
for c in MONEY_COLS:
    assert net[c] + vat[c] == gross[c], f"колонка {c}: тождество не сошлось"

# 2. Снимаем объединения строк, которые поедут вниз (терминатор и всё ниже).
moving = [str(r) for r in ws.merged_cells.ranges if r.min_row >= TERMINATOR_ROW]
for ref in moving:
    ws.unmerge_cells(ref)

# 3. Вставляем строку ПЕРЕД терминатором: терминатор обязан уцелеть пустым,
#    иначе обход блока не остановится и прочитает «Дополнительную информацию».
ws.insert_rows(TERMINATOR_ROW)

# 4. Заполняем строку НДС и новую строку «без учета НДС».
for c in MONEY_COLS:
    ws.cell(row=VAT_ROW, column=c, value=float(vat[c]))
ws.cell(row=TERMINATOR_ROW, column=1, value="ИТОГО, руб. без учета НДС")
for c in MONEY_COLS:
    ws.cell(row=TERMINATOR_ROW, column=c, value=float(net[c]))

# 5. Возвращаем объединения, сдвинув их на одну строку, плюс объединение
#    A..E у новой строки — как у двух соседних.
ws.merge_cells(start_row=TERMINATOR_ROW, start_column=1, end_row=TERMINATOR_ROW, end_column=5)
for ref in moving:
    rng = CellRange(ref)
    ws.merge_cells(start_row=rng.min_row + 1, start_column=rng.min_col,
                   end_row=rng.max_row + 1, end_column=rng.max_col)

# 6. Самопроверка на месте: три строки блока, терминатор пуст, суммы сходятся.
assert ws.cell(row=2588, column=1).value == "ИТОГО, руб. с учетом НДС"
assert ws.cell(row=2589, column=1).value == "В том числе НДС"
assert ws.cell(row=2590, column=1).value == "ИТОГО, руб. без учета НДС"
assert all(ws.cell(row=2591, column=c).value is None for c in range(1, 21))
print("OK: rows 2588-2590 filled, 2591 empty")
wb.save(PATH)
```

Деньги пишутся `float(...)` намеренно: openpyxl хранит числовую ячейку как
double, и парсер читает её через `money_to_json`, который сам приводит к
`Decimal(str(...))`. Суммы синтетические — они уже такие в fixture.

- [ ] **Шаг 3: прогнать скрипт и замерить результат**

Из `backend/`:

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run python ../scratchpad/edit_fixture.py
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run python ../scratchpad/measure_fixture_before.py
```

Ожидание: `max_row = 2602`, блок 2588–2590 заполнен, 2591 пуста, 2592 —
«Дополнительная информация:».

- [ ] **Шаг 4: проверить, что числа Ф3 не поехали**

```bash
cd backend && uv run pytest tests/unit/parser -q
cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  uv run pytest tests/integration/test_import_fixture_e2e.py -q
```

Ожидание: `264 passed` и `16 passed`. **Поехало число — неверна правка, а не
устарело ожидание.** В частности `2576 / 746 / 222 / 485 / 39 / 38 / 16` и
`EXPECTED_PRICED_ROWS` обязаны воспроизвестись: правка лежит ниже зоны позиций.

- [ ] **Шаг 5: закрепить форму fixture тестом, читающим лист напрямую**

Добавить в `backend/tests/integration/test_import_fixture_e2e.py` рядом с
`_independent_total_with_vat`:

```python
SUMMARY_BLOCK_LABELS = (
    "ИТОГО, руб. с учетом НДС",
    "В том числе НДС",
    "ИТОГО, руб. без учета НДС",
)


def test_fixture_summary_block_has_three_filled_rows(fixture_worksheet):
    """Форма самого входа, прочитанная с листа, — не через парсер.

    Стережёт правку fixture: если блок снова станет двухстрочным, главный путь
    Ф4a останется без закоммиченного входа, и это должно быть видно сразу.
    """
    ws = fixture_worksheet
    rows = [2588, 2589, 2590]
    labels = [str(ws.cell(row=row, column=1).value or "").strip() for row in rows]
    assert labels == list(SUMMARY_BLOCK_LABELS)

    money = {
        row: [ws.cell(row=row, column=col).value for col in range(15, 19)]
        for row in rows
    }
    for row, values in money.items():
        assert all(value is not None for value in values), f"строка {row} заполнена не полностью"

    assert all(ws.cell(row=2591, column=col).value is None for col in range(1, 21)), (
        "строка 2591 обязана остаться пустой: она терминатор блока итогов"
    )

    for index in range(4):
        gross = Decimal(str(money[2588][index]))
        vat = Decimal(str(money[2589][index]))
        net = Decimal(str(money[2590][index]))
        assert gross == net + vat, f"колонка {15 + index}: тождество не сошлось"
```

- [ ] **Шаг 6: прогнать новый тест**

```bash
cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  uv run pytest tests/integration/test_import_fixture_e2e.py -q
```

Ожидание: `17 passed`.

- [ ] **Шаг 7: коммит**

```bash
git add fixtures/gp_estimate_fixture.xlsx backend/tests/integration/test_import_fixture_e2e.py
git commit -m "test(fixture): Ф4a — блок итогов доведён до трёхстрочной формы

Закоммиченный вход был двухстрочным, то есть коллизии ключей на нём не
происходило и главный путь фичи на CI не проверялся. Теперь блок — три
строки; тождество «с НДС = без НДС + НДС» сходится точно по всем четырём
колонкам, терминатор блока остался пустым.

Числа Ф3 воспроизвелись до последнего: правка ниже зоны позиций.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Ядро — распознавание метки и инъективные ключи

**Files:**
- Create: `backend/parser/summary_block.py`
- Modify: `backend/parser/constants.py`
- Test: `backend/tests/unit/parser/test_summary_block.py`

**Interfaces:**
- Consumes: `normalized_cell_text` из `parser.sheet`
- Produces:
  - `SummaryRow(row: int, label: Any, values: dict[str, Any])` — вход ядра;
  - `assign_summary_keys(rows: Sequence[SummaryRow]) -> KeyAssignment`;
  - `KeyAssignment(keys: list[str], unrecognized: list[tuple[int, str]],
    duplicated: list[tuple[int, str, int]])` — `keys` ровно по одному на вход, в
    том же порядке; `duplicated` несёт (строка, метка, строка первого владельца).

- [ ] **Шаг 1: написать падающие тесты ядра**

Создать `backend/tests/unit/parser/test_summary_block.py`:

```python
"""Ядро разбора блока итогов: распознавание меток и назначение ключей.

Ни `Worksheet`, ни файла — блок итогов это три строки и метка в колонке A,
синтетический вход моделирует его точно (спека §2.10).
"""
from __future__ import annotations

import pytest

from parser.summary_block import SummaryRow, assign_summary_keys

INCLUDING = "total_cost_including_vat"
VAT = "vat_amount"
EXCLUDING = "total_cost_excluding_vat"


def _row(row: int, label, values=None) -> SummaryRow:
    return SummaryRow(row=row, label=label, values=values or {})


class TestRecognition:
    def test_three_tax_labels_get_three_distinct_keys(self):
        """Ровно тот случай, ради которого заведена фича."""
        rows = [
            _row(10, "ИТОГО, руб. с учетом НДС"),
            _row(11, "В том числе НДС"),
            _row(12, "ИТОГО, руб. без учета НДС"),
        ]
        assignment = assign_summary_keys(rows)
        assert assignment.keys == [INCLUDING, VAT, EXCLUDING]
        assert assignment.unrecognized == []
        assert assignment.duplicated == []

    @pytest.mark.parametrize(
        "label",
        [
            "  ИТОГО, руб. с учетом НДС  ",
            "ИТОГО,  руб.  с  учетом  НДС",
            "ИТОГО,\xa0руб.\xa0с учетом НДС",
            "итого, руб. с учетом ндс",
            "ИТОГО, РУБ. С УЧЕТОМ НДС",
        ],
    )
    def test_normalization_before_exact_match(self, label):
        """Края, внутренние пробелы (включая неразрывный) и регистр не мешают."""
        assert assign_summary_keys([_row(10, label)]).keys == [INCLUDING]

    def test_substring_of_a_known_label_is_not_a_match(self):
        """Точное равенство, а не вхождение: «итого»+«ндс» и есть дефект."""
        assignment = assign_summary_keys([_row(10, "ИТОГО с учетом НДС по лоту")])
        assert assignment.keys == ["merged_10"]
        assert assignment.unrecognized == [(10, "ИТОГО с учетом НДС по лоту")]

    def test_tender_labels_are_still_matched_by_substring(self):
        """Их константы — маркеры подстроки, полного текста входа у нас нет."""
        rows = [
            _row(10, "Отклонение от расчетной стоимости, руб."),
            _row(11, "Первоначальная стоимость (справочно)"),
        ]
        assert assign_summary_keys(rows).keys == [
            "deviation_from_baseline_cost",
            "initial_cost",
        ]

    def test_unknown_label_falls_back_to_row_key(self):
        assignment = assign_summary_keys([_row(42, "Что-то незнакомое")])
        assert assignment.keys == ["merged_42"]
        assert assignment.unrecognized == [(42, "Что-то незнакомое")]

    def test_empty_label_is_unrecognized_not_a_crash(self):
        assignment = assign_summary_keys([_row(42, None)])
        assert assignment.keys == ["merged_42"]
        assert assignment.unrecognized == [(42, "")]


class TestInjectivity:
    def test_repeated_label_does_not_overwrite_the_first(self):
        """Точность метки коллизию НЕ закрывает — закрывает инъективность."""
        rows = [
            _row(10, "ИТОГО, руб. с учетом НДС"),
            _row(11, "ИТОГО, руб. с учетом НДС"),
        ]
        assignment = assign_summary_keys(rows)
        assert assignment.keys == [INCLUDING, "merged_11"]
        assert assignment.duplicated == [(11, "ИТОГО, руб. с учетом НДС", 10)]

    def test_repeated_tender_label_is_covered_too(self):
        rows = [
            _row(10, "Первоначальная стоимость"),
            _row(11, "Первоначальная стоимость, руб."),
        ]
        assignment = assign_summary_keys(rows)
        assert assignment.keys == ["initial_cost", "merged_11"]
        assert len(assignment.duplicated) == 1

    @pytest.mark.parametrize(
        "labels",
        [
            ["ИТОГО, руб. с учетом НДС", "В том числе НДС", "ИТОГО, руб. без учета НДС"],
            ["ИТОГО, руб. с учетом НДС", "В том числе НДС"],
            ["ИТОГО, руб. с учетом НДС", "ИТОГО, руб. с учетом НДС", "Незнакомая"],
            [None, None],
        ],
    )
    def test_keys_are_unique_and_one_per_row(self, labels):
        """Инвариант §2.3 на каждом входе: строк = ключей, и все ключи разные."""
        rows = [_row(100 + i, label) for i, label in enumerate(labels)]
        keys = assign_summary_keys(rows).keys
        assert len(keys) == len(rows)
        assert len(set(keys)) == len(rows)
```

- [ ] **Шаг 2: прогнать и убедиться, что падает по правильной причине**

```bash
cd backend && uv run pytest tests/unit/parser/test_summary_block.py -q
```

Ожидание: `ModuleNotFoundError: No module named 'parser.summary_block'` —
собралось ноль тестов, ошибка **сбора**, а не падение утверждения.

- [ ] **Шаг 3: добавить константы**

В `backend/parser/constants.py` заменить блок ключей итогов:

```python
# -- Общие ключи для итоговых сумм тендера/лота и специфических полей --
# Ф4a: три независимых ключа вместо одного двусмысленного. Прежние
# `total_cost_with_vat` и `vat` удалены, а не переосмыслены: `raw_data`
# неизменяем и backfill невозможен, поэтому одно имя с двумя значениями у
# старых и новых смет различалось бы только по `parser_version`
# (спека Ф4a §2.1).
JSON_KEY_TOTAL_COST_INCLUDING_VAT = "total_cost_including_vat"  # валовое ИТОГО
JSON_KEY_VAT_AMOUNT = "vat_amount"  # СУММА НДС; ставка — Ф4б, имя `vat_rate` за ней
JSON_KEY_TOTAL_COST_EXCLUDING_VAT = "total_cost_excluding_vat"  # ИТОГО без НДС
JSON_KEY_INITIAL_COST = "initial_cost"  # Первоначальная стоимость
# Отклонение предложения подрядчика от базовой (расчетной) стоимости.
JSON_KEY_DEVIATION_FROM_CALCULATED_COST = "deviation_from_baseline_cost"

# Метки блока итогов в колонке A. Сравниваются ТОЧНО, после нормализации
# (спека Ф4a §2.2): замер даёт побайтово одинаковые метки во всех четырёх
# известных файлах, поэтому строгость ничего не стоит, а нестрогость и есть
# исходный дефект.
TABLE_PARSE_SUMMARY_INCLUDING_VAT = "ИТОГО, руб. с учетом НДС"
TABLE_PARSE_SUMMARY_VAT = "В том числе НДС"
TABLE_PARSE_SUMMARY_EXCLUDING_VAT = "ИТОГО, руб. без учета НДС"
```

Строки `JSON_KEY_TOTAL_COST_VAT` и `JSON_KEY_VAT` **удалить**.
`TABLE_PARSE_DEVIATION_FROM_CALCULATED_COST` и `TABLE_PARSE_INITIAL_COST`
не трогать.

- [ ] **Шаг 4: написать ядро**

Создать `backend/parser/summary_block.py`:

```python
"""Ядро разбора блока итогов сметы.

Чистые функции: ни `Worksheet`, ни `openpyxl`, ни БД. Обход листа живёт в
`get_summary`, здесь — только правила (спека Ф4a §2.2–§2.7).

Почему модуль вообще есть: блок итогов — три строки и метка в колонке A, то
есть всё содержательное в нём проверяется без файла. Раньше правила сидели
внутри обхода, и единственным способом их проверить был разбор xlsx.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .constants import (
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
    JSON_KEY_INITIAL_COST,
    JSON_KEY_TOTAL_COST_EXCLUDING_VAT,
    JSON_KEY_TOTAL_COST_INCLUDING_VAT,
    JSON_KEY_VAT_AMOUNT,
    TABLE_PARSE_DEVIATION_FROM_CALCULATED_COST,
    TABLE_PARSE_INITIAL_COST,
    TABLE_PARSE_SUMMARY_EXCLUDING_VAT,
    TABLE_PARSE_SUMMARY_INCLUDING_VAT,
    TABLE_PARSE_SUMMARY_VAT,
)
from .sheet import normalized_cell_text

#: Предел примеров в агрегированном предупреждении. Своя константа, а не импорт
#: из `services.category_resolution`: `parser` не зависит от `services` (условие
#: фазы 3), и инвертировать направление ради одного числа нельзя.
MAX_SUMMARY_WARNING_EXAMPLES = 5

#: Метки, распознаваемые ТОЧНЫМ равенством нормализованных форм.
_EXACT_KEY_BY_LABEL: dict[str, str] = {
    normalized_cell_text(TABLE_PARSE_SUMMARY_INCLUDING_VAT).casefold(): JSON_KEY_TOTAL_COST_INCLUDING_VAT,
    normalized_cell_text(TABLE_PARSE_SUMMARY_VAT).casefold(): JSON_KEY_VAT_AMOUNT,
    normalized_cell_text(TABLE_PARSE_SUMMARY_EXCLUDING_VAT).casefold(): JSON_KEY_TOTAL_COST_EXCLUDING_VAT,
}

#: Тендерные метки: их константы — маркеры для поиска ВНУТРИ строки, а полного
#: текста реального тендерного заголовка у нас нет (спека §1.5 факт 3).
_SUBSTRING_KEY_BY_MARKER: tuple[tuple[str, str], ...] = (
    (TABLE_PARSE_DEVIATION_FROM_CALCULATED_COST, JSON_KEY_DEVIATION_FROM_CALCULATED_COST),
    (TABLE_PARSE_INITIAL_COST, JSON_KEY_INITIAL_COST),
)


@dataclass(frozen=True)
class SummaryRow:
    """Одна физическая строка блока итогов."""

    row: int
    """Номер строки листа — он же источник фолбэк-ключа `merged_{row}`."""

    label: Any
    """Сырое значение ячейки колонки A."""

    values: dict[str, Any] = field(default_factory=dict)
    """Результат `parse_contractor_row` для этой строки."""


@dataclass(frozen=True)
class KeyAssignment:
    """Ключи по одному на строку плюс факты о том, что пошло не так."""

    keys: list[str]
    unrecognized: list[tuple[int, str]]
    duplicated: list[tuple[int, str, int]]


def assign_summary_keys(rows: Sequence[SummaryRow]) -> KeyAssignment:
    """Назначает каждой строке блока ключ, и каждой — свой.

    Три шага (спека §2.2): нормализация, точное равенство для трёх налоговых
    меток (подстрока — для двух тендерных), инъективность. Третий шаг не
    украшение: без него две строки с одинаковой меткой снова затёрли бы друг
    друга, и точность метки коллизию бы не закрыла.
    """
    keys: list[str] = []
    unrecognized: list[tuple[int, str]] = []
    duplicated: list[tuple[int, str, int]] = []
    owner_row_by_key: dict[str, int] = {}

    for item in rows:
        shown = normalized_cell_text(item.label)
        key = _recognize(shown)

        if key is None:
            unrecognized.append((item.row, shown))
            key = f"merged_{item.row}"
        elif key in owner_row_by_key:
            duplicated.append((item.row, shown, owner_row_by_key[key]))
            key = f"merged_{item.row}"

        owner_row_by_key.setdefault(key, item.row)
        keys.append(key)

    return KeyAssignment(keys=keys, unrecognized=unrecognized, duplicated=duplicated)


def _recognize(shown: str) -> str | None:
    """Ключ по нормализованной метке либо None."""
    folded = shown.casefold()
    exact = _EXACT_KEY_BY_LABEL.get(folded)
    if exact is not None:
        return exact
    for marker, key in _SUBSTRING_KEY_BY_MARKER:
        if marker in folded:
            return key
    return None


def _examples(items: list[str]) -> str:
    """Перечень с усечением — агрегированные предупреждения (Global Constraints)."""
    shown = "; ".join(items[:MAX_SUMMARY_WARNING_EXAMPLES])
    hidden = len(items) - MAX_SUMMARY_WARNING_EXAMPLES
    return f"{shown}{f'; …и ещё {hidden}' if hidden > 0 else ''}"
```

- [ ] **Шаг 5: прогнать тесты ядра**

```bash
cd backend && uv run pytest tests/unit/parser/test_summary_block.py -q
```

Ожидание: все зелёные. Число собранных **замерить командой и записать**, не
выводить сложением параметризаций в уме — арифметика в голове и есть тот способ,
которым числа «уезжают».

- [ ] **Шаг 6: убедиться, что ничего не сломано и `_examples` пока не используется**

```bash
cd backend && uv run pytest tests/unit/parser -q
cd backend && uv run ruff check parser/summary_block.py
```

Ожидание: парсер зелёный (`264 passed` плюс новые), ruff чистый. `_examples`
подключается в Task 4 и до тех пор не вызывается — прочитать вывод ruff, а не
предполагать: если он всё же ругается, функцию перенести в Task 4, а не глушить
директивой.

- [ ] **Шаг 7: коммит**

```bash
git add backend/parser/summary_block.py backend/parser/constants.py \
        backend/tests/unit/parser/test_summary_block.py
git commit -m "feat(parser): Ф4a — ядро блока итогов, точные метки и инъективные ключи

Три налоговые метки распознаются точным равенством нормализованных форм,
две тендерные — по-прежнему подстрокой (их константы это маркеры, полного
текста реального входа нет). Ключ, уже занятый в блоке, второй раз не
выдаётся: строка уходит в merged_<row>. Без инъективности точность метки
коллизию не закрывает.

Ядро чистое: ни Worksheet, ни openpyxl. Ещё не подключено.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Ядро — конверсия денег и сверка арифметики

**Files:**
- Modify: `backend/parser/summary_block.py`
- Test: `backend/tests/unit/parser/test_summary_block.py`

**Interfaces:**
- Consumes: `KeyAssignment` и `SummaryRow` из Task 2
- Produces:
  - `to_decimal(value: Any) -> tuple[Decimal | None, str | None]` — `(число, None)`
    для годного, `(None, None)` для пустого, `(None, сырьё)` для негодного;
  - `check_arithmetic(lines: Mapping[str, Any]) -> ArithmeticReport`;
  - `ArithmeticReport(broken: list[str], unverified: list[str])` — перечни
    человекочитаемых описаний по колонкам.

- [ ] **Шаг 1: написать падающие тесты конверсии и сверки**

Дописать в `backend/tests/unit/parser/test_summary_block.py`:

```python
from decimal import Decimal

from parser.summary_block import check_arithmetic, to_decimal

MONEY_COLUMNS = ("materials", "works", "indirect_costs", "total")


def _line(**amounts) -> dict:
    """Строка итогов в форме, в которой её отдаёт parse_contractor_row."""
    return {"total_cost": {name: amounts.get(name) for name in MONEY_COLUMNS}}


def _triple(gross, vat, net) -> dict:
    """Блок из трёх налоговых строк; одно и то же значение во все колонки."""
    return {
        INCLUDING: _line(**{name: gross for name in MONEY_COLUMNS}),
        VAT: _line(**{name: vat for name in MONEY_COLUMNS}),
        EXCLUDING: _line(**{name: net for name in MONEY_COLUMNS}),
    }


class TestToDecimal:
    def test_decimal_string_is_converted(self):
        assert to_decimal("120.50") == (Decimal("120.50"), None)

    def test_int_and_float_are_converted_through_str(self):
        assert to_decimal(7) == (Decimal("7"), None)
        assert to_decimal(0.1) == (Decimal("0.1"), None)

    def test_none_is_blank(self):
        assert to_decimal(None) == (None, None)

    @pytest.mark.parametrize("value", ["", "   ", "\xa0", "\n"])
    def test_blank_string_is_blank_not_unusable(self, value):
        """`money_to_json` пропускает '' как есть; косметически пустая ячейка
        не должна выглядеть негодным значением (спека §2.6)."""
        assert to_decimal(value) == (None, None)

    @pytest.mark.parametrize("value", ["n/a", "1 234,56", "#REF!"])
    def test_arbitrary_text_is_unusable_and_keeps_the_raw_value(self, value):
        assert to_decimal(value) == (None, value)

    @pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
    def test_non_finite_is_unusable(self, value):
        """is_finite() — единственный фильтр, закрывающий все три случая."""
        assert to_decimal(value) == (None, value)


class TestArithmetic:
    def test_exact_identity_is_silent(self):
        report = check_arithmetic(_triple("120", "20", "100"))
        assert report.broken == []
        assert report.unverified == []

    def test_broken_identity_names_the_column_and_all_three_numbers(self):
        # Числа подобраны так, чтобы ни одно не было подстрокой другого:
        # с «120/20/99» утверждение прошло бы вакуозно — «20» лежит внутри «120».
        report = check_arithmetic(_triple("500", "77", "400"))
        assert len(report.broken) == len(MONEY_COLUMNS)
        assert "materials" in report.broken[0]
        for number in ("500", "77", "400"):
            assert number in report.broken[0]
        assert report.unverified == []

    def test_all_three_blank_in_a_column_is_silent(self):
        report = check_arithmetic(_triple(None, None, None))
        assert report.broken == []
        assert report.unverified == []

    def test_partial_triple_is_unverified(self):
        lines = _triple("120", None, "100")
        report = check_arithmetic(lines)
        assert len(report.unverified) == len(MONEY_COLUMNS)
        assert report.broken == []

    @pytest.mark.parametrize("bad", ["n/a", "#REF!", "NaN", "sNaN"])
    def test_unusable_value_is_unverified_and_named(self, bad):
        report = check_arithmetic(_triple("120", bad, "100"))
        assert report.broken == []
        assert len(report.unverified) == len(MONEY_COLUMNS)
        assert bad in report.unverified[0]

    def test_infinity_does_not_pass_as_agreement(self):
        """Главная дыра: Infinity == Infinity + 100 истинно, и без фильтра
        противоречивый файл выглядел бы сошедшимся."""
        report = check_arithmetic(_triple("Infinity", "100", "Infinity"))
        assert report.broken == []
        assert len(report.unverified) == len(MONEY_COLUMNS)

    def test_opposite_infinities_do_not_raise(self):
        """`-Infinity + Infinity` бросает InvalidOperation на СЛОЖЕНИИ —
        фильтр обязан отработать раньше."""
        report = check_arithmetic(_triple("100", "Infinity", "-Infinity"))
        assert report.broken == []
        assert len(report.unverified) == len(MONEY_COLUMNS)

    def test_missing_tax_line_means_no_check_at_all(self):
        """Сверка идёт, только если присутствуют все три налоговые строки."""
        lines = _triple("120", "20", "100")
        del lines[EXCLUDING]
        report = check_arithmetic(lines)
        assert report.broken == []
        assert report.unverified == []
```

- [ ] **Шаг 2: прогнать и увидеть красный**

```bash
cd backend && uv run pytest tests/unit/parser/test_summary_block.py -q
```

Ожидание: `ImportError: cannot import name 'check_arithmetic'` — ошибка сбора
файла целиком.

- [ ] **Шаг 3: реализовать конверсию и сверку**

Дописать в `backend/parser/summary_block.py`:

```python
from decimal import Decimal, InvalidOperation

from .constants import (
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_MATERIALS,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
    JSON_KEY_WORKS,
)

#: Денежные колонки блока `total_cost`, по которым идёт сверка.
_MONEY_COLUMNS: tuple[str, ...] = (
    JSON_KEY_MATERIALS,
    JSON_KEY_WORKS,
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_TOTAL,
)


@dataclass(frozen=True)
class ArithmeticReport:
    """Результат сверки `including = excluding + vat_amount` по колонкам."""

    broken: list[str]
    """Колонки, где равенство не выполнилось; текст несёт все три числа."""

    unverified: list[str]
    """Колонки, где сверить было нечем: неполная тройка либо негодное значение."""


def to_decimal(value: Any) -> tuple[Decimal | None, str | None]:
    """Безопасная конверсия денежного значения парсера.

    `money_to_json` отдаёт деньги десятичными СТРОКАМИ, а нечисловой текст
    ячейки пропускает как есть, поэтому сложение без явной конверсии не
    определено (спека §2.6).

    Returns:
        `(Decimal, None)` — годное значение;
        `(None, None)` — пусто (`None` либо строка, пустая после нормализации);
        `(None, сырьё)` — негодное: не конвертируется либо не `is_finite()`.
        Негодным считается и `Infinity`: `Infinity == Infinity + 100` истинно,
        то есть без этого фильтра противоречивый файл выглядел бы сошедшимся.
    """
    if value is None:
        return None, None
    shown = normalized_cell_text(value)
    if shown == "":
        return None, None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None, shown
    if not number.is_finite():
        return None, shown
    return number, None


def check_arithmetic(lines: Mapping[str, Any]) -> ArithmeticReport:
    """Сверяет `including = excluding + vat_amount` по четырём колонкам.

    Сверка выполняется, только если в блоке присутствуют все три налоговые
    строки. Конверсия и проверка годности идут ДО сложения и сравнения:
    `-Infinity + Infinity` бросает `InvalidOperation` уже на сложении, а `sNaN` —
    на сравнении, поэтому «обернуть сверку в try» фильтр не заменяет.
    """
    broken: list[str] = []
    unverified: list[str] = []

    needed = (JSON_KEY_TOTAL_COST_INCLUDING_VAT, JSON_KEY_VAT_AMOUNT, JSON_KEY_TOTAL_COST_EXCLUDING_VAT)
    if not all(key in lines for key in needed):
        return ArithmeticReport(broken=broken, unverified=unverified)

    blocks = [(lines[key].get(JSON_KEY_TOTAL_COST) or {}) for key in needed]

    for column in _MONEY_COLUMNS:
        raw = [block.get(column) for block in blocks]
        converted = [to_decimal(value) for value in raw]

        bad = [problem for _, problem in converted if problem is not None]
        if bad:
            unverified.append(f"«{column}»: негодное значение {', '.join(repr(item) for item in bad)}")
            continue

        numbers = [number for number, _ in converted]
        if all(number is None for number in numbers):
            continue
        if any(number is None for number in numbers):
            missing = [
                name
                for name, number in zip(("с НДС", "НДС", "без НДС"), numbers, strict=True)
                if number is None
            ]
            unverified.append(f"«{column}»: нет значений — {', '.join(missing)}")
            continue

        including, vat, excluding = numbers
        if including != excluding + vat:
            broken.append(f"«{column}»: с НДС {including}, НДС {vat}, без НДС {excluding}")

    return ArithmeticReport(broken=broken, unverified=unverified)
```

Не забыть добавить `from collections.abc import Mapping, Sequence` в импорты
модуля.

- [ ] **Шаг 4: прогнать тесты**

```bash
cd backend && uv run pytest tests/unit/parser/test_summary_block.py -q
```

Ожидание: все зелёные; число собранных замерить и записать.

- [ ] **Шаг 5: коммит**

```bash
git add backend/parser/summary_block.py backend/tests/unit/parser/test_summary_block.py
git commit -m "feat(parser): Ф4a — конверсия денег и сверка арифметики блока итогов

money_to_json отдаёт деньги строками, а нечисловой текст пропускает как
есть, поэтому сложение без явной конверсии не определено. Годным считается
только is_finite(): Infinity == Infinity + 100 истинно, то есть без фильтра
противоречивый файл выглядел бы сошедшимся, sNaN бросает на сравнении, а
-Inf + Inf — уже на сложении.

Негодное значение не роняет разбор: колонка уходит в «не проверена» с
фактическим значением. Пустая строка считается пустотой, а не негодностью.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Ядро — сборка блока и семь предупреждений

**Files:**
- Modify: `backend/parser/summary_block.py`
- Test: `backend/tests/unit/parser/test_summary_block.py`

**Interfaces:**
- Consumes: `assign_summary_keys`, `check_arithmetic`, `_examples` из Task 2–3
- Produces: `build_summary_block(rows: Sequence[SummaryRow], *, search_start_row: int) -> SummaryBlock`
  и `SummaryBlock(lines: dict[str, Any], warnings: list[str])`. `lines` — ровно та
  структура, которую `get_summary` возвращал раньше: ключ → `{job_title: ..., **values}`.

- [ ] **Шаг 1: написать падающие тесты сборки и предупреждений**

Дописать в `backend/tests/unit/parser/test_summary_block.py`:

```python
from parser.summary_block import SummaryBlock, build_summary_block


def _summary_row(row: int, label, **amounts) -> SummaryRow:
    return SummaryRow(row=row, label=label, values=_line(**amounts))


def _full_triple_rows(gross="120", vat="20", net="100") -> list[SummaryRow]:
    return [
        _summary_row(10, "ИТОГО, руб. с учетом НДС", **{c: gross for c in MONEY_COLUMNS}),
        _summary_row(11, "В том числе НДС", **{c: vat for c in MONEY_COLUMNS}),
        _summary_row(12, "ИТОГО, руб. без учета НДС", **{c: net for c in MONEY_COLUMNS}),
    ]


def _warned(block: SummaryBlock, fragment: str) -> list[str]:
    return [text for text in block.warnings if fragment in text]


class TestBuildSummaryBlock:
    def test_three_rows_give_three_keys_and_no_warnings(self):
        block = build_summary_block(_full_triple_rows(), search_start_row=5)
        assert sorted(block.lines) == sorted([INCLUDING, VAT, EXCLUDING])
        assert block.warnings == []

    def test_job_title_keeps_the_raw_cell_value(self):
        block = build_summary_block(_full_triple_rows(), search_start_row=5)
        assert block.lines[INCLUDING]["job_title"] == "ИТОГО, руб. с учетом НДС"

    def test_values_are_carried_through_untouched(self):
        block = build_summary_block(_full_triple_rows(), search_start_row=5)
        assert block.lines[VAT]["total_cost"]["total"] == "20"

    @pytest.mark.parametrize(
        "rows",
        [
            _full_triple_rows(),
            [_summary_row(10, "ИТОГО, руб. с учетом НДС"), _summary_row(11, "В том числе НДС")],
            [_summary_row(10, "Незнакомая"), _summary_row(11, "Незнакомая")],
        ],
    )
    def test_invariant_rows_equal_keys(self, rows):
        """Инвариант §2.3 — на каждом входе."""
        block = build_summary_block(rows, search_start_row=5)
        assert len(block.lines) == len(rows)


class TestWarnings:
    def test_block_not_found_names_the_search_start(self):
        block = build_summary_block([], search_start_row=11)
        assert block.lines == {}
        assert _warned(block, "Блок итогов не найден")
        assert "11" in _warned(block, "Блок итогов не найден")[0]

    def test_unrecognized_labels_are_one_aggregated_warning_with_the_actual_text(self):
        rows = [_summary_row(10, "Первое чужое"), _summary_row(11, "Второе чужое")]
        block = build_summary_block(rows, search_start_row=5)
        found = _warned(block, "не распознан")
        assert len(found) == 1
        assert "Первое чужое" in found[0] and "Второе чужое" in found[0]

    def test_examples_are_truncated_with_a_tail(self):
        rows = [_summary_row(10 + i, f"Чужая метка {i}") for i in range(7)]
        block = build_summary_block(rows, search_start_row=5)
        found = _warned(block, "не распознан")
        assert len(found) == 1
        assert "…и ещё 2" in found[0]

    def test_duplicate_label_is_its_own_warning(self):
        rows = [
            _summary_row(10, "ИТОГО, руб. с учетом НДС"),
            _summary_row(11, "ИТОГО, руб. с учетом НДС"),
        ]
        block = build_summary_block(rows, search_start_row=5)
        found = _warned(block, "встретилась дважды")
        assert len(found) == 1
        assert "merged_11" in found[0]

    def test_empty_vat_row_warns_without_claiming_anything_about_the_header(self):
        rows = [
            _summary_row(10, "ИТОГО, руб. с учетом НДС", **{c: "120" for c in MONEY_COLUMNS}),
            _summary_row(11, "В том числе НДС"),
        ]
        block = build_summary_block(rows, search_start_row=5)
        found = _warned(block, "суммы не указаны")
        assert len(found) == 1
        assert "не заявлен" not in found[0], "предупреждение не имеет права судить о шапке файла"

    def test_missing_gross_row_warns_and_nothing_is_reconstructed(self):
        rows = [
            _summary_row(10, "В том числе НДС", **{c: "20" for c in MONEY_COLUMNS}),
            _summary_row(11, "ИТОГО, руб. без учета НДС", **{c: "100" for c in MONEY_COLUMNS}),
        ]
        block = build_summary_block(rows, search_start_row=5)
        assert INCLUDING not in block.lines
        assert _warned(block, "Валовое ИТОГО отсутствует")

    def test_broken_arithmetic_warns(self):
        block = build_summary_block(_full_triple_rows(net="99"), search_start_row=5)
        assert _warned(block, "не сходится")

    def test_unverified_arithmetic_is_a_different_warning(self):
        block = build_summary_block(_full_triple_rows(vat="NaN"), search_start_row=5)
        assert _warned(block, "не проверена")
        assert not _warned(block, "не сходится")

    def test_full_correct_block_is_completely_silent(self):
        """Форма fixture после Task 1: ни одного предупреждения."""
        assert build_summary_block(_full_triple_rows(), search_start_row=5).warnings == []
```

- [ ] **Шаг 2: прогнать и увидеть красный**

```bash
cd backend && uv run pytest tests/unit/parser/test_summary_block.py -q
```

Ожидание: `ImportError: cannot import name 'build_summary_block'`.

- [ ] **Шаг 3: реализовать сборку**

Дописать в `backend/parser/summary_block.py`:

```python
from .constants import JSON_KEY_JOB_TITLE


@dataclass(frozen=True)
class SummaryBlock:
    """Разобранный блок итогов и всё, что о нём надо сказать человеку."""

    lines: dict[str, Any]
    """Ключ → строка блока; форма та же, что у прежнего `get_summary`."""

    warnings: list[str]
    """Parser warnings (сессия A): каждое описывает ФАЙЛ, а не домен."""


def build_summary_block(rows: Sequence[SummaryRow], *, search_start_row: int) -> SummaryBlock:
    """Собирает блок итогов из физических строк и объясняет отклонения.

    Инвариант: `len(lines) == len(rows)` при любом входе (спека §2.3). Пустой
    `rows` означает ровно одно — блок не найден: найденный блок начинается со
    строки с меткой в колонке A, то есть непустой.
    """
    if not rows:
        return SummaryBlock(
            lines={},
            warnings=[
                f"Блок итогов не найден: от строки {search_start_row} до конца листа "
                "нет ни одной строки с объединённой ячейкой в колонке A. "
                "Итоговых сумм у сметы не будет."
            ],
        )

    assignment = assign_summary_keys(rows)
    lines: dict[str, Any] = {}
    for key, item in zip(assignment.keys, rows, strict=True):
        lines[key] = {JSON_KEY_JOB_TITLE: item.label, **item.values}

    warnings: list[str] = []

    if assignment.unrecognized:
        places = [f"строка {row}: «{label}»" for row, label in assignment.unrecognized]
        warnings.append(
            f"В блоке итогов не распознано меток: {len(places)} — {_examples(places)}. "
            "Значения сохранены под техническими ключами `merged_<строка>`; "
            "проверьте форму файла."
        )

    if assignment.duplicated:
        places = [
            f"строка {row}: «{label}» уже была в строке {first} — записана как `merged_{row}`"
            for row, label, first in assignment.duplicated
        ]
        warnings.append(
            f"В блоке итогов метка встретилась дважды: {len(places)} — {_examples(places)}. "
            "Вторая строка не перезаписала первую."
        )

    vat_line = lines.get(JSON_KEY_VAT_AMOUNT)
    if vat_line is not None and _has_no_money(vat_line):
        row = next(item.row for key, item in zip(assignment.keys, rows, strict=True) if key == JSON_KEY_VAT_AMOUNT)
        warnings.append(
            f"В строке «{TABLE_PARSE_SUMMARY_VAT}» (строка {row}) суммы не указаны. "
            "Сумма НДС по этой смете неизвестна."
        )

    if JSON_KEY_TOTAL_COST_INCLUDING_VAT not in lines:
        present = ", ".join(f"«{normalized_cell_text(item.label)}»" for item in rows)
        warnings.append(
            f"Валовое ИТОГО отсутствует: строки «{TABLE_PARSE_SUMMARY_INCLUDING_VAT}» в блоке нет. "
            f"В блоке есть: {present}. Сумма не восстанавливалась сложением — "
            "парсер отдаёт только то, что есть в файле."
        )

    report = check_arithmetic(lines)
    if report.broken:
        warnings.append(
            f"Арифметика НДС не сходится в колонках: {len(report.broken)} — "
            f"{_examples(report.broken)}. Числа взяты из файла и не исправлялись."
        )
    if report.unverified:
        # «неполных ИЛИ негодных»: сюда же попадают `n/a`, `NaN`, `Infinity` —
        # текст обязан покрывать оба входа ветки, иначе он уже собственного
        # условия (та же ошибка, что «НДС не заявлен» в §2.7).
        warnings.append(
            f"Арифметика НДС не проверена из-за неполных или негодных данных: "
            f"{len(report.unverified)} — {_examples(report.unverified)}."
        )

    return SummaryBlock(lines=lines, warnings=warnings)


def _has_no_money(line: Mapping[str, Any]) -> bool:
    """Ни одной годной или хотя бы непустой суммы в строке."""
    block = line.get(JSON_KEY_TOTAL_COST) or {}
    return all(to_decimal(block.get(column)) == (None, None) for column in _MONEY_COLUMNS)
```

- [ ] **Шаг 4: прогнать тесты**

```bash
cd backend && uv run pytest tests/unit/parser/test_summary_block.py -q
cd backend && uv run ruff check parser/summary_block.py
```

Ожидание: зелёный, ruff чистый.

- [ ] **Шаг 5: коммит**

```bash
git add backend/parser/summary_block.py backend/tests/unit/parser/test_summary_block.py
git commit -m "feat(parser): Ф4a — сборка блока итогов и семь предупреждений

Инвариант «строк = ключей» держится по построению. Семь агрегированных
предупреждений: блок не найден, нераспознанные метки, повторная метка,
суммы НДС не указаны, валовое ИТОГО отсутствует, арифметика не сходится,
арифметика не проверена.

Предупреждение о НДС говорит только о строке блока и НЕ утверждает ничего
о шапке файла: шапку Ф4a не читает вовсе (это Ф4б).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Подключение — `get_summary`, проводка наверх, версия `3.0.0`

Самая ломающая задача: после неё контракт парсера сменился. Делается целиком,
одним коммитом — половина этого изменения оставила бы репозиторий несобранным.

**Files:**
- Modify: `backend/parser/get_summary.py`
- Modify: `backend/parser/get_proposals.py`
- Modify: `backend/parser/read_lots_and_boundaries.py`
- Modify: `backend/parser/estimate.py`
- Modify: `backend/tests/unit/parser/test_get_proposals.py`
- Modify: `backend/tests/unit/parser/test_estimate.py`
- Modify: `backend/tests/payloads.py`
- Modify: `backend/tests/integration/test_estimate_import.py`
- Create: `backend/tests/unit/parser/test_get_summary.py`

**Interfaces:**
- Consumes: `SummaryBlock`, `SummaryRow`, `build_summary_block` из Task 4
- Produces:
  - `get_summary(ws, contractor, search_start_row) -> SummaryBlock`;
  - `get_proposals(ws, start_row, end_row) -> LotProposals(proposals, warnings)`;
  - `read_lots_and_boundaries(ws) -> LotsResult(lots, warnings)`;
  - `PARSER_VERSION == "3.0.0"`.

- [ ] **Шаг 1: прогон нового правила по уже существующим входам**

До единой правки кода — механически выписать, что новое поведение
переклассифицирует ([replaying-new-rules.md](../../insights/replaying-new-rules.md),
слой 2). Записать результат в scratchpad и приложить к отчёту задачи:

```bash
cd backend
grep -rn "get_summary" tests --include=*.py | grep -c "patch"          # ожидание: 8
grep -rn "= get_proposals(\|result = get_proposals" tests --include=*.py | wc -l   # ожидание: 15
grep -rn "total_cost_with_vat\|JSON_KEY_VAT\b\|JSON_KEY_TOTAL_COST_VAT" \
  --include=*.py . | grep -v "\.venv" | grep -v __pycache__          # ожидание: 6 мест
```

Ожидаемый список правок: 8 патчей `get_summary`, 15 разборов результата
`get_proposals` (плюс 6 вызовов без разбора — их править не нужно),
`payloads.py:195-196`, `test_estimate.py:207`, `test_estimate_import.py:158-159`.
**Расхождение с ожиданием — не повод «поправить» число, а повод разобраться.**

- [ ] **Шаг 2: переписать `get_summary` на обход + ядро**

`backend/parser/get_summary.py`, тело функции целиком:

```python
def get_summary(ws: Worksheet, contractor: dict[str, Any], search_start_row: int) -> SummaryBlock:
    """Извлекает итоговые строки подрядчика из блока итогов внизу таблицы.

    Здесь только обход листа: где блок начинается, где кончается и что стоит в
    каждой строке. Все правила — распознавание метки, инъективность ключа,
    сверка арифметики, предупреждения — живут в `summary_block` и проверяются
    без файла (спека Ф4a §2.2–§2.7).

    Returns:
        `SummaryBlock`: строки блока и parser warnings о нём.
    """
    merged_first_column_rows = merged_rows_in_first_column(ws)
    last_column = contractor_last_column(contractor)

    summary_start_row = -1
    for row_num in range(search_start_row, ws.max_row + 1):
        if row_num in merged_first_column_rows:
            summary_start_row = row_num
            break

    rows: list[SummaryRow] = []
    if summary_start_row != -1:
        for current_row_num in range(summary_start_row, ws.max_row + 1):
            if row_is_empty(ws, current_row_num, last_column):
                break  # Пустая строка означает конец блока
            rows.append(
                SummaryRow(
                    row=current_row_num,
                    label=ws.cell(row=current_row_num, column=1).value,
                    values=parse_contractor_row(ws, current_row_num, contractor),
                )
            )

    return build_summary_block(rows, search_start_row=search_start_row)
```

Импорты модуля: убрать `JSON_KEY_*`-константы итогов и `JSON_KEY_JOB_TITLE`
(они уехали в ядро), добавить
`from .summary_block import SummaryBlock, SummaryRow, build_summary_block`.
Docstring модуля дополнить третьим отличием от исходника: правила вынесены в
`summary_block`.

- [ ] **Шаг 3: проводка через `get_proposals`**

В `backend/parser/get_proposals.py` добавить структуру и вернуть её:

```python
@dataclass(frozen=True)
class LotProposals:
    """Предложения лота и предупреждения, собранные при их разборе."""

    proposals: dict[str, dict[str, Any]]
    warnings: list[str]
```

В теле: `summary = get_summary(...)`, в `contractor_items_data` класть
`summary.lines`, а `summary.warnings` копить в локальный список; вернуть
`LotProposals(proposals=proposals, warnings=warnings)`. Ранний выход при
отсутствии подрядчиков — `LotProposals(proposals={}, warnings=[])`.

- [ ] **Шаг 4: проводка через `read_lots_and_boundaries`**

Добавить структуру и вернуть её:

```python
@dataclass(frozen=True)
class LotsResult:
    """Лоты и предупреждения, собранные при их разборе."""

    lots: dict[str, dict[str, Any]]
    warnings: list[str]
```

Ранний выход — `LotsResult(lots={}, warnings=[])`. В цикле:
`lot = get_proposals(...)`, `JSON_KEY_PROPOSALS: lot.proposals`,
`warnings.extend(lot.warnings)`.

- [ ] **Шаг 5: сборка и устранение дублей в `estimate.py`**

Заменить построение `data` в `parse_worksheet`:

```python
    lots = read_lots_and_boundaries(ws)

    # Блок итогов — факт уровня ЛИСТА, а `get_summary` зовётся на каждое
    # предложение каждого лота (спека §1.5 факт 5). В смете ГП лот и подрядчик
    # одни, но на двухлотовом входе одни и те же предупреждения пришли бы
    # дважды. `dict.fromkeys` снимает дубли и сохраняет порядок.
    warnings.extend(dict.fromkeys(lots.warnings))

    data: dict[str, Any] = {
        **read_headers(ws),
        JSON_KEY_EXECUTOR: read_executer_block(ws),
        JSON_KEY_LOTS: lots.lots,
    }
```

И поднять версию:

```python
# 3.0.0 (Ф4a): блок итогов отдаёт три независимых ключа —
# `total_cost_including_vat`, `vat_amount`, `total_cost_excluding_vat`. Прежние
# `total_cost_with_vat` и `vat` ИСЧЕЗЛИ. Это мажор не потому, что появились
# ключи, а потому, что два ключа пропали: смена ИМЕНИ вместо смены смысла
# выбрана намеренно — `raw_data` неизменяем, backfill невозможен, и одно имя с
# двумя значениями у старых и новых смет различалось бы только по этой самой
# версии (спека Ф4a §2.1).
PARSER_VERSION = "3.0.0"
```

- [ ] **Шаг 6: починить 8 моков и 15 разборов результата**

В `backend/tests/unit/parser/test_get_proposals.py`:

- импортировать `from parser.summary_block import SummaryBlock`;
- каждый `patch(f"{MODULE}.get_summary", return_value={})` →
  `return_value=SummaryBlock(lines={}, warnings=[])`;
- патч с данными (`_patch_collaborators`, строка 92) →
  `SummaryBlock(lines={} if summary is None else summary, warnings=[])`;
- `assert get_proposals(...) == {}` → `.proposals == {}`;
- каждое `result = get_proposals(...)` → `result = get_proposals(...).proposals`.

- [ ] **Шаг 7: переименовать ключи у трёх потребителей**

- `backend/tests/payloads.py:195-196`:

```python
                JSON_KEY_TOTAL_COST_INCLUDING_VAT: summary_line("ИТОГО, руб. с учетом НДС", "1200.00"),
                JSON_KEY_VAT_AMOUNT: summary_line("В том числе НДС", "200.00"),
                JSON_KEY_TOTAL_COST_EXCLUDING_VAT: summary_line("ИТОГО, руб. без учета НДС", "1000.00"),
```

Три строки вместо двух намеренно: синтетический payload теперь несёт ту же
форму, что реальный трёхстрочный файл, и суммы сходятся точно
(`1200 = 1000 + 200`). Импорты в шапке файла поправить соответственно.

- `backend/tests/unit/parser/test_estimate.py:207`:

```python
        assert sorted(summary) == ["total_cost_excluding_vat", "total_cost_including_vat", "vat_amount"]
```

Тест переименовать в `test_summary_block_gives_one_key_per_row` и добавить
утверждение, ради которого фича существует:

```python
    def test_summary_block_gives_one_key_per_row(self, fixture_result):
        """Три строки блока в файле → три ключа. Прежде их было два: метки
        «с учетом НДС» и «без учета НДС» обе содержали «итого» и «ндс»."""
        summary = _proposal(fixture_result)["contractor_items"]["summary"]
        assert sorted(summary) == ["total_cost_excluding_vat", "total_cost_including_vat", "vat_amount"]
        assert summary["total_cost_including_vat"]["job_title"] == "ИТОГО, руб. с учетом НДС"
        assert summary["total_cost_excluding_vat"]["job_title"] == "ИТОГО, руб. без учета НДС"
```

Тест версии переименовать и переписать причину:

```python
    def test_parser_version_is_major_because_two_summary_keys_disappeared(self, fixture_result):
        assert fixture_result.parser_version == "3.0.0"
```

- `backend/tests/integration/test_estimate_import.py:158-159`:

```python
        assert set(lines) == {"total_cost_including_vat", "vat_amount", "total_cost_excluding_vat"}
        assert lines["total_cost_including_vat"].total_cost == Decimal("1200.00")
        assert lines["total_cost_excluding_vat"].total_cost == Decimal("1000.00")
```

- [ ] **Шаг 8: новый тест обхода листа**

Создать `backend/tests/unit/parser/test_get_summary.py`:

```python
"""Обход блока итогов по листу. Правила проверяются в test_summary_block.py."""
from __future__ import annotations

from openpyxl import Workbook

from parser.get_summary import get_summary

CONTRACTOR = {"column_start": 10, "merged_shape": {"colspan": 11}}


# Блок подрядчика J..T (colspan 11) раскладывается так — замерено вызовом
# parse_contractor_row на листе, где в каждой ячейке лежит её номер колонки:
#   10 suggested_quantity | 11..14 unit_cost.{mat,wrk,ind,total}
#   15..18 total_cost.{mat,wrk,ind,total} | 19 организатор | 20 комментарий
# Итоговая стоимость — колонка 18, НЕ 17 (17 это indirect_costs).
TOTAL_COST_TOTAL_COLUMN = 18


def _sheet_with_summary(rows: list[tuple[str, str | None]]):
    """Лист, где с 20-й строки идёт блок итогов, а ниже — «Дополнительная информация».

    Args:
        rows: пары (метка колонки A, значение колонки `total_cost.total`);
            None означает строку без сумм.
    """
    ws = Workbook().active
    ws["A11"] = "первая строка позиций"
    for offset, (label, total) in enumerate(rows):
        row = 20 + offset
        ws.cell(row=row, column=1, value=label)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        if total is not None:
            ws.cell(row=row, column=TOTAL_COST_TOTAL_COLUMN, value=float(total))
    ws.cell(row=20 + len(rows) + 1, column=1, value="Дополнительная информация:")
    return ws


def test_total_is_read_from_the_column_the_helper_writes():
    """Предпосылка самого хелпера: колонка 18 — это `total_cost.total`.

    Проверяется внутри теста, а не «известна»: перепутанная колонка оставила бы
    все тесты блока зелёными, сверяя пустоту с пустотой
    (false-test-premises.md).
    """
    ws = _sheet_with_summary([("ИТОГО, руб. с учетом НДС", "120")])
    block = get_summary(ws, CONTRACTOR, search_start_row=11)
    line = block.lines["total_cost_including_vat"]
    assert line["total_cost"]["total"] == "120.0"


def test_walks_from_the_first_merged_row_to_the_first_empty_one():
    ws = _sheet_with_summary(
        [("ИТОГО, руб. с учетом НДС", "120"), ("В том числе НДС", "20"),
         ("ИТОГО, руб. без учета НДС", "100")]
    )
    block = get_summary(ws, CONTRACTOR, search_start_row=11)
    assert len(block.lines) == 3


def test_empty_row_stops_the_walk_before_additional_info():
    """Терминатор блока — пустая строка. Если её нет, обход прочитает
    «Дополнительную информацию» как ещё одну итоговую строку."""
    ws = _sheet_with_summary([("ИТОГО, руб. с учетом НДС", "120"), ("В том числе НДС", "20")])
    block = get_summary(ws, CONTRACTOR, search_start_row=11)
    assert "Дополнительная информация:" not in [
        line["job_title"] for line in block.lines.values()
    ]


def test_no_merged_row_means_block_not_found():
    ws = Workbook().active
    ws["A11"] = "первая строка позиций"
    block = get_summary(ws, CONTRACTOR, search_start_row=11)
    assert block.lines == {}
    assert any("не найден" in text for text in block.warnings)
```

- [ ] **Шаг 9: тесты полного пути — проводка, дедупликация, постобработка**

Все положительные тесты предупреждений из Task 4 зовут **ядро напрямую**. Значит
обрыв проводки в `get_proposals` или `read_lots_and_boundaries` не уронил бы
ничего: предупреждение молча не доехало бы до `ParseResult.warnings`, а набор
остался бы зелёным. Три теста ниже закрывают именно путь, а не правило.

Дописать в `backend/tests/unit/parser/test_estimate.py`:

```python
# Раскладка колонок замерена: 15..18 — total_cost.{materials,works,
# indirect_costs,total}. Тождество ниже сходится ТОЧНО: 120 = 100 + 20.
SUMMARY_TRIPLE = (
    ("ИТОГО, руб. с учетом НДС", {15: 120.0, 16: 120.0, 17: 120.0, 18: 120.0}),
    ("В том числе НДС", {15: 20.0, 16: 20.0, 17: 20.0, 18: 20.0}),
    ("ИТОГО, руб. без учета НДС", {15: 100.0, 16: 100.0, 17: 100.0, 18: 100.0}),
)


def _sheet_with_summary_rows(summary_rows, *, second_lot: bool = False):
    """Лист с блоком итогов под позициями; при `second_lot` — два лота, один блок.

    Блок итогов — факт уровня листа, а `get_summary` зовётся на каждое
    предложение каждого лота, поэтому при двух лотах один и тот же блок
    читается дважды. Строка сразу под блоком остаётся пустой — она терминатор,
    без неё обход прочитает то, что ниже, как ещё одну итоговую строку.

    Args:
        summary_rows: последовательность (метка колонки A, {номер колонки: значение}).
        second_lot: добавить второй маркер лота над блоком.
    """
    ws = _minimal_sheet(contractor_colspan=11)          # лот №1 в D11
    ws.cell(row=12, column=1, value=1)
    ws.cell(row=12, column=2, value="1")
    ws.cell(row=12, column=4, value="Работа первого лота")

    if second_lot:
        ws.cell(row=13, column=1, value=2)
        ws.cell(row=13, column=2, value="2")
        ws.cell(row=13, column=4, value="Лот №2 Второй")
        ws.cell(row=14, column=1, value=3)
        ws.cell(row=14, column=2, value="3")
        ws.cell(row=14, column=4, value="Работа второго лота")

    for offset, (label, money) in enumerate(summary_rows):
        row = 15 + offset
        ws.cell(row=row, column=1, value=label)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        for column, value in money.items():
            ws.cell(row=row, column=column, value=value)
    return ws


class TestSummaryWarningsReachTheTop:
    """Проводка предупреждений блока итогов наверх, а не только правило."""

    def test_warning_from_the_core_reaches_parse_result(self):
        """Один лот: предупреждение обязано доехать до ParseResult.warnings.

        Без этого теста обрыв проводки в get_proposals или
        read_lots_and_boundaries остался бы невидимым: тесты ядра зовут его
        напрямую.
        """
        ws = _sheet_with_summary_rows([("Совершенно чужая метка", {})])

        result = parse_worksheet(ws)

        matching = [w for w in result.warnings if "Совершенно чужая метка" in w]
        assert len(matching) == 1

    def test_the_same_warning_is_not_doubled_on_a_two_lot_sheet(self):
        """Два лота — один блок; наверху остаётся ОДИН экземпляр (спека §2.8)."""
        ws = _sheet_with_summary_rows([("Совершенно чужая метка", {})], second_lot=True)

        result = parse_worksheet(ws)

        matching = [w for w in result.warnings if "Совершенно чужая метка" in w]
        assert len(matching) == 1, f"ожидался один экземпляр, получено {len(matching)}"

    def test_excel_error_in_a_summary_cell_is_named_in_the_warning_and_nulled_in_json(self):
        """Пара, которую спека §2.6 обязалась назвать вслух.

        Предупреждение говорит о том, ЧТО СТОЯЛО В ЯЧЕЙКЕ (`#REF!`), а в JSON
        на её месте `None` — штатная `replace_excel_errors_with_null`
        отрабатывает ПОСЛЕ разбора блока. Оба утверждения в одном тесте:
        порознь они выглядели бы противоречием.

        Блок ОБЯЗАН быть трёхстрочным: `check_arithmetic` выходит сразу, если
        нет хотя бы одной из трёх налоговых строк, — на однострочном блоке
        ветка с негодным значением не исполнилась бы вовсе, и тест краснел бы
        по чужой причине.
        """
        gross_label, gross_money = SUMMARY_TRIPLE[0]
        rows = [
            (gross_label, {**gross_money, 18: "#REF!"}),
            SUMMARY_TRIPLE[1],
            SUMMARY_TRIPLE[2],
        ]
        ws = _sheet_with_summary_rows(rows)

        result = parse_worksheet(ws)

        named = [w for w in result.warnings if "#REF!" in w]
        assert len(named) == 1, "негодное значение обязано быть названо ровно одним предупреждением"
        assert "не проверена" in named[0]
        assert not any("не сходится" in w for w in result.warnings), (
            "в трёх годных колонках тождество сходится точно — «не сходится» здесь быть не должно"
        )

        summary = _proposal(result)["contractor_items"]["summary"]
        total_cost = summary["total_cost_including_vat"]["total_cost"]
        assert total_cost["total"] is None, "#REF! обязан стать null штатной постобработкой"
        assert total_cost["materials"] == "120.0", "соседняя годная сумма не задета"
```

Предпосылка `test_..._nulled_in_json` — что `#REF!` вообще попадает в набор
литералов постобработки — уже закреплена соседним тестом
`test_temporal_and_error_cells_become_json_safe` (он делает то же с `#N/A` в
позиции). Если набор изменится, покраснеют оба.

- [ ] **Шаг 10: прогнать всё**

```bash
cd backend && uv run pytest tests/unit -q
cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  env -u DATABASE_URL uv run pytest tests/integration -q
```

Ожидание: зелёный, `skipped` только шесть известных из `test_auth_coverage.py`.
`skipped` больше — разбираться, а не считать фоном
([silent-test-runs.md](../../insights/silent-test-runs.md)).

- [ ] **Шаг 11: коммит**

```bash
git add backend/parser backend/tests
git commit -m "feat(parser)!: Ф4a — контракт 3.0.0, три ключа блока итогов

get_summary отдаёт SummaryBlock (строки + parser warnings); предупреждения
едут наверх через LotProposals и LotsResult и склеиваются в parse_worksheet
с устранением дублей — блок итогов это факт уровня листа, а зовётся его
разбор на каждое предложение каждого лота.

Ключи total_cost_with_vat и vat исчезли, вместо них
total_cost_including_vat / vat_amount / total_cost_excluding_vat.
Потребители переведены: payloads.py, test_estimate.py,
test_estimate_import.py. Починены 8 моков get_summary и 15 разборов
результата get_proposals — объём правок замерен до неё самой, а не найден
красным прогоном.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: E2E — три строки файла доходят до трёх записей в БД

**Files:**
- Modify: `backend/tests/integration/test_import_fixture_e2e.py`

**Interfaces:**
- Consumes: fixture из Task 1, контракт `3.0.0` из Task 5
- Produces: доказательство починки на закоммиченном входе

- [ ] **Шаг 1: написать тесты**

Дописать в `backend/tests/integration/test_import_fixture_e2e.py`:

```python
def test_three_sheet_rows_give_three_summary_keys_in_raw(imported_fixture):
    """Счёт, которым дефект был доказан: было три строки и два ключа."""
    summary = imported_fixture.raw["lots"]["lot_1"]["proposals"]["contractor_1"][
        "contractor_items"
    ]["summary"]
    assert len(summary) == 3
    assert sorted(summary) == [
        "total_cost_excluding_vat",
        "total_cost_including_vat",
        "vat_amount",
    ]


def test_three_summary_records_reach_the_database(db, imported_fixture):
    lines = {
        line.summary_key: line
        for line in db.scalars(
            sa.select(ProposalSummaryLine).where(
                ProposalSummaryLine.proposal_id == imported_fixture.proposal_id
            )
        )
    }
    assert sorted(lines) == [
        "total_cost_excluding_vat",
        "total_cost_including_vat",
        "vat_amount",
    ]
    assert lines["total_cost_including_vat"].job_title == "ИТОГО, руб. с учетом НДС"


def test_gross_total_matches_the_reference_read_from_the_sheet(
    db, imported_fixture, fixture_worksheet
):
    """Главное доказательство починки: поле совпадает с эталоном, прочитанным
    с листа помимо парсера. До Ф4a в этом поле лежала сумма БЕЗ НДС."""
    expected = _independent_total_with_vat(fixture_worksheet, FIXTURE_CONTRACTOR)
    stored = db.scalars(
        sa.select(ProposalSummaryLine).where(
            ProposalSummaryLine.proposal_id == imported_fixture.proposal_id,
            ProposalSummaryLine.summary_key == "total_cost_including_vat",
        )
    ).one()
    assert stored.total_cost == expected


@pytest.mark.parametrize(
    "field",
    ["materials_cost", "works_cost", "indirect_costs_cost", "total_cost"],
)
def test_identity_holds_on_the_stored_records(db, imported_fixture, field):
    """Все ЧЕТЫРЕ денежные колонки, а не только итоговая (спека §2.6).

    Сверка одной колонки прошла бы и при разъехавшейся разбивке: три остальные
    поля доезжают до БД тем же путём и тем же `_money`, но проверялись бы
    ничем.
    """
    lines = {
        line.summary_key: line
        for line in db.scalars(
            sa.select(ProposalSummaryLine).where(
                ProposalSummaryLine.proposal_id == imported_fixture.proposal_id
            )
        )
    }
    including = getattr(lines["total_cost_including_vat"], field)
    excluding = getattr(lines["total_cost_excluding_vat"], field)
    vat = getattr(lines["vat_amount"], field)
    # Каждое слагаемое проверяется ДО сложения: пустой компонент иначе даст
    # `TypeError: unsupported operand type(s)` вместо объясняющего падения, и
    # причина «в БД нет значения» осталась бы нечитаемой.
    for name, value in (("с НДС", including), ("без НДС", excluding), ("НДС", vat)):
        assert value is not None, f"{field}: значение «{name}» пусто — сверять нечего, тест был бы вакуозен"
    assert including == excluding + vat


SUMMARY_WARNING_MARKERS = (
    "Блок итогов не найден",
    "не распознано меток",
    "встретилась дважды",
    "суммы не указаны",
    "Валовое ИТОГО отсутствует",
    "Арифметика НДС не сходится",
    "Арифметика НДС не проверена из-за неполных или негодных данных",
)


def test_fixture_parses_without_any_summary_warning(imported_fixture):
    """Форма fixture полная и непротиворечивая — блок итогов молчит.

    Маркеры перечислены поимённо, а не отфильтрованы подстрокой «итог»: тексты
    про арифметику этого слова не содержат вовсе, и фильтр был бы вакуозен.
    """
    found = [
        text
        for text in imported_fixture.warnings
        for marker in SUMMARY_WARNING_MARKERS
        if marker in text
    ]
    assert found == []
```

Имена фикстур (`imported_fixture`, `db`, `fixture_worksheet`) и способ добраться
до `raw`, `proposal_id` и `warnings` взять **из существующего файла** — он уже
делает это для восьми утверждений Ф4; повторять чужую механику по памяти нельзя.
Если подходящей фикстуры нет, добавить её рядом с существующими, не изобретая
второй способ импорта.

- [ ] **Шаг 2: прогнать**

```bash
cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  env -u DATABASE_URL uv run pytest tests/integration/test_import_fixture_e2e.py -q
```

Ожидание: `22 passed` (17 после Task 1 + 5). Число замерить, а не вывести.

- [ ] **Шаг 3: проверить, что числа Ф3 на месте**

```bash
cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  env -u DATABASE_URL uv run pytest tests/integration -q
```

Ожидание: зелёный; `2576 / 746 / 222 / 485 / 39 / 38 / 16` и
`EXPECTED_PRICED_ROWS` воспроизводятся.

- [ ] **Шаг 4: коммит**

```bash
git add backend/tests/integration/test_import_fixture_e2e.py
git commit -m "test(e2e): Ф4a — три строки файла доходят до трёх записей в БД

Пять независимых утверждений: три ключа в raw, три записи в
proposal_summary_lines, валовое совпадает с эталоном, прочитанным с листа
помимо парсера, тождество держится на сохранённых записях, блок итогов не
даёт предупреждений.

Совпадение с эталоном — то самое, чего сегодня нет: в поле лежала сумма
без НДС.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Негативные проверки, соответствие «требование → тест», devlog

**Делает оркестратор лично**, не исполнитель (роли, `AGENTS.md` §9 и спека §4.4).

**Files:**
- Create: `docs/devlog/2026-08-07-summary-vat-lines.md`

- [ ] **Шаг 1: контрольные прогоны до всякого снятия**

```bash
cd backend && uv run pytest tests/unit/parser -q
cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  env -u DATABASE_URL uv run pytest tests/integration/test_import_fixture_e2e.py -q
```

Записать числа. Красный **до** снятия означает сломанный пробник, а не
доказанную защиту ([verifying-guards.md](../../insights/verifying-guards.md),
слой 3).

- [ ] **Шаг 2: провести двенадцать снятий**

По каждому: побайтовая копия файла → `assert old in text` перед заменой → печать
sha256 до и после → прогон → запись, **сколько именно** упало → восстановление из
копии со сверкой sha256. Не `git checkout --`: он даёт CRLF и ложную тревогу.

| № | Что снять | Ожидание |
|---|---|---|
| 1 | `_recognize`: вернуть `"итого" in folded and "ндс" in folded` вместо точного равенства | воспроизводится исходный дефект: три строки → два ключа |
| 2 | ветку `elif key in owner_row_by_key` в `assign_summary_keys` | падает тест повторной метки |
| 3 | предупреждение «Блок итогов не найден» | падает `test_block_not_found_names_the_search_start` |
| 4 | предупреждение о нераспознанных метках | падают тесты нераспознанного и усечения |
| 5 | предупреждение о повторной метке | падает свой тест |
| 6 | предупреждение «суммы не указаны» | падает свой тест |
| 7 | предупреждение «Валовое ИТОГО отсутствует» | падает свой тест |
| 8 | ветку `broken` в `check_arithmetic` | падает тест несходящейся арифметики |
| 9 | ветку «неполная тройка» в `check_arithmetic` | падает `test_partial_triple_is_unverified` |
| 10 | **фильтр `is_finite()`** в `to_decimal` | `test_infinity_does_not_pass_as_agreement` краснеет: `Infinity` начинает давать ложное «сошлось» |
| 11 | `warnings.extend(lot.warnings)` в `read_lots_and_boundaries` (проводка) | `test_warning_from_the_core_reaches_parse_result` краснеет; тесты ядра остаются зелёными — это и есть доказательство, что они путь не стерегли |
| 12 | `dict.fromkeys` в `parse_worksheet` (дедупликация) | `test_the_same_warning_is_not_doubled_on_a_two_lot_sheet` краснеет: два экземпляра вместо одного |

Снятие, не валящее ничего, означает **отсутствие защиты**, а не плохой тест
(слой 7). Такой случай записывается в devlog и закрывается тестом.

- [ ] **Шаг 3: сверка на реальных офертах и стенде**

Стенд `gca_dev` **не чистится**. Три оферты перезаливаются штатным
`replace=true` через HTTP (auth-роутер на `/api/auth`, не `/api/v1/auth`).
Проверить запросом из файла (`psql -f`, `PGCLIENTENCODING=UTF8`):

- записей `proposal_summary_lines`: **3 / 3 / 2** (159-ТУ, 42-ТУ, 449-ТУ);
- `parser_version = 3.0.0` у всех трёх;
- у 159-ТУ и 42-ТУ `job_title` записи `total_cost_including_vat` — «ИТОГО, руб.
  с учетом НДС»;
- по каждой смете: сумма `position_items` (не-разделы) плюс сумма
  `estimate_additional_works` равна `total_cost_including_vat`, дельта ноль
  (`count(<колонка>)`, не `count(*)`, на `LEFT JOIN`);
- предупреждений блока итогов: ноль у 159-ТУ и 42-ТУ, ровно одно у 449-ТУ
  («суммы не указаны»).

- [ ] **Шаг 4: соответствие «требование спеки → тест»**

Построить таблицу по тексту §2.1–§2.12, требование за требованием
([replaying-new-rules.md](../../insights/replaying-new-rules.md), слой 3).
Требование без исполнителя либо получает тест, либо **объявляется границей** в
devlog. Отдельной строкой — восемь требований Ф4 §7: шесть исполняются, одно
исполняется строже (инъективность), одно отменено (§2.5).

- [ ] **Шаг 5: `just ci` целиком**

```bash
just ci
```

Шаги по отдельности, в форме CI. `alembic check` обязан быть чистым: миграции у
фичи нет, и её появление означало бы, что схему тронули вопреки §2.9.

- [ ] **Шаг 6: devlog и PR**

`docs/devlog/2026-08-07-summary-vat-lines.md`: что сделано по задачам, замеры
(собранные тесты до/после каждой задачи, числа fixture, стенд), отступления от
плана, найденные грабли, негативные проверки с тем, что именно покраснело,
соответствие «требование → тест», названные границы.

PR со ссылками на рамку, спеку и план.

---

## Самопроверка плана

**Покрытие спеки.** §2.1 — Task 2 (константы) и Task 5 (версия, потребители);
§2.2 — Task 2; §2.3 — инвариант утверждается в Task 2, 4 и 6; §2.4 — Task 4
(матрица форм) и Task 5 (обход); §2.5 — Task 4, тест «ничего не
восстанавливается»; §2.6 — Task 3 (правило) и Task 5 шаг 9 (`#REF!` через
постобработку); §2.7 — Task 4; §2.8 — Task 5 шаги 3–5 (код) и шаг 9 (проводка и
дедупликация тестами полного пути);
§2.9 — миграции нет, проверяется `alembic check` в Task 7; §2.10 — Task 1;
§2.11 — Task 7 шаг 3 (`replace`), кода не требует; §2.12 — гейта нет, кода не
требует. §4.4 — Task 7 шаг 2; §4.5 — Task 7 шаг 4; §6 DoD — Task 7 целиком.

**Типы согласованы:** `SummaryRow`, `KeyAssignment`, `ArithmeticReport`,
`SummaryBlock`, `LotProposals`, `LotsResult` объявлены по одному разу и
употребляются под теми же именами; `to_decimal` возвращает
`tuple[Decimal | None, str | None]` во всех употреблениях; `build_summary_block`
принимает `search_start_row` только ключевым аргументом и в плане вызывается так
везде.

**Числа, которые исполнитель обязан замерить, а не вывести:** число собранных
тестов после каждой задачи; `264` у парсера до фичи; `17` и `22` у fixture-E2E;
8 моков и 15 разборов результата; числа Ф3 и `EXPECTED_PRICED_ROWS`; счётчики
стенда.
