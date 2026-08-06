# Ф2: парсер — guard шапки и агрегатная строка допработ — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Отвергать файлы с чужой раскладкой колонок A–D и научиться узнавать агрегатную строку «Дополнительные работы», не меняя при этом ни одного числа в текущем импорте.

**Architecture:** Два независимых изменения в парсере. Первое — структурный отказ (`EstimateParseError`) в `parse_worksheet`, рядом с существующими отказами; строка шапки ищется по маркеру между строкой контрагентов и первым лотом, сверяются все четыре фиксированные колонки. Второе — распознавание агрегатной строки внутри существующего прохода `get_lot_positions`: строка **копируется** в новое поле `additional_works` и **остаётся** в `positions` (переходное решение, снимается в Ф4).

**Tech Stack:** Python 3.12, openpyxl, pytest, uv, just. Миграций нет, фронтенд не затрагивается.

**Спека:** [2026-08-06-parser-smr-article-design.md](../specs/2026-08-06-parser-smr-article-design.md) — источник правды. Замеры в §1, обязательство для Ф4 в §7.

## Global Constraints

- Ветка: `feat/parser-smr-article` (спека уже закоммичена в неё).
- ruff `line-length = 120`, target py312; комментарии и docstring — по-русски, как в окружающем коде.
- **Деньги — только через `parse_contractor_row`**, который отдаёт их десятичными строками (`money_to_json`). Читать денежные ячейки напрямую нельзя: вернётся `float` (`AGENTS.md` §3).
- **Не ломать досрочный выход `get_lot_positions` по merged-ячейке в колонке A** — это признак конца блока позиций (`AGENTS.md` §11).
- **Агрегатная строка в Ф2 остаётся в `positions`.** Исключение — Ф4, атомарно с записью в `estimate_additional_works` (спека §2.2). Тест на это есть в Task 2.
- **Счётчики позиций и суммы по реальным офертам меняться не должны.** Поехавший baseline — признак дефекта, а не повод обновить число (спека §4).
- Нормализация текста для сверки — ровно `" ".join(str(value).split()).casefold()`; «пусто» — `"" if value is None else " ".join(str(value).split())`.
- Все команды бэкенда — из `backend/` через `uv run`; системный python не вызывать.
- Перед пушем — `just ci` (в него теперь входит `db-test-check`).

## Состояние на момент написания плана

- `PARSER_VERSION = "1.0.0"` (`backend/parser/estimate.py:43`).
- `parse_worksheet` уже делает три структурных отказа подряд: нет строки контрагентов → нет подрядчика → нет маркера лота, затем `_validate_contractor_blocks` (`estimate.py:145-167`).
- `read_contractors` отдаёт список словарей с ключами `value`, `coordinate`, `column_start`, `row_start`, `merged_shape`; `find_lot_starts` — `[{"start_row": int, "title": str}, ...]`.
- `layout.check_estimate_layout` — **только предупреждения**; структурные отказы живут в `estimate.py`. Границу не смешивать.
- `get_lot_positions(ws, contractor, lot_start_row, lot_end_row) -> dict` читает A, B, C, D, F, G, H по фиксированным позициям и вызывает `parse_contractor_row`. **14 вызовов** в `tests/unit/parser/test_get_lot_positions.py`.
- Синтетические листы `parse_worksheet` строит один хелпер `_minimal_sheet` (`tests/unit/parser/test_estimate.py:487`); плюс `test_performance.py` строит свой лист, где уже есть `ws["A9"] = "№ п/п"` и **вертикально объединённая** `A9:A10`.
- Три теста в `TestParseEstimateFailures` собирают листы вручную, но падают **до** новой проверки (нет контрагентов / нет подрядчика / нет лота) — их трогать не нужно.

## File Structure

- `backend/parser/constants.py` — **правится**: заголовки колонок A–D, название агрегатной строки, JSON-ключи.
- `backend/parser/sheet.py` — **правится**: `normalized_cell_text` и `cell_text_is_blank` (утилиты уровня ячейки, рядом с `row_is_empty`).
- `backend/parser/errors.py` — **создаётся** в Task 2 Step 0: `EstimateParseError` переезжает сюда, чтобы `get_lot_positions` мог его поднимать без цикла импортов.
- `backend/parser/estimate.py` — **правится**: `_find_column_header_row`, `_validate_column_headers`, вызов в `parse_worksheet`, `PARSER_VERSION`, импорт исключения из `errors`.
- `backend/parser/get_lot_positions.py` — **правится**: dataclass `LotRows`, распознавание агрегатной строки в существующем цикле.
- `backend/parser/get_proposals.py` — **правится**: разложить `LotRows` в `contractor_items`.
- `backend/parser/__init__.py` — **не трогается**: `LotRows` наружу не выводим. Модуль прямо объявляет, что внутренние шаги разбора не реэкспортируются (иначе имя затирает атрибут-модуль); в `__all__` только точка входа, `ParseResult`, `EstimateParseError`, `PARSER_VERSION` и контракт нормализации. Публичное имя `EstimateParseError` после переезда в `errors.py` сохраняется — за этим следит тест в Task 2 Step 0.
- `backend/tests/unit/parser/test_estimate.py` — **правится**: шапка в `_minimal_sheet`, новый класс тестов guard'а.
- `backend/tests/unit/parser/test_get_lot_positions.py` — **правится**: 14 вызовов получают `.positions`, новые тесты агрегатной строки.
- `backend/tests/unit/parser/test_performance.py` — **правится**: в синтетический лист добавляются B9, C9, D9.
- `docs/devlog/2026-08-06-parser-smr-article.md` — **создаётся** в Task 3.

---

### Task 1: guard шапки — отказ при чужой раскладке A–D

**Files:**
- Modify: `backend/parser/constants.py`
- Modify: `backend/parser/sheet.py`
- Modify: `backend/parser/estimate.py`
- Test: `backend/tests/unit/parser/test_estimate.py`
- Test: `backend/tests/unit/parser/test_performance.py`

**Interfaces:**
- Produces: `sheet.normalized_cell_text(value: Any) -> str`, `sheet.cell_text_is_blank(value: Any) -> bool`, `constants.TABLE_PARSE_POSITION_COLUMN_HEADERS: dict[int, str]`. На них опирается Task 2.

- [ ] **Step 1: Добавить константы заголовков**

В `backend/parser/constants.py`, рядом с прочими `TABLE_PARSE_*` (после `TABLE_PARSE_SUGGESTED_QUANTITY`, строка 62):

```python
#: Заголовки общих колонок таблицы позиций. Замерены на трёх реальных офертах
#: (спека Ф2 §1): строка 9, A «№ п/п», B «№ раздела», C «Статья СМР»,
#: D «Наименование работ». Сверяются ВСЕ ЧЕТЫРЕ: колонки читаются по фиксированным
#: позициям, поэтому чужая шапка делает недостоверным весь позиционный разбор,
#: а не одну колонку статьи.
TABLE_PARSE_POSITION_COLUMN_HEADERS: dict[int, str] = {
    1: "№ п/п",
    2: "№ раздела",
    3: "Статья СМР",
    4: "Наименование работ",
}
```

- [ ] **Step 2: Добавить утилиты нормализации ячейки**

В `backend/parser/sheet.py`, рядом с `row_is_empty`:

```python
def normalized_cell_text(value: Any) -> str:
    """Текст ячейки в форме, пригодной для сверки с ожидаемым.

    Схлопывает любые пробельные последовательности (включая переносы строк и
    неразрывный пробел) в один пробел и обрезает края. `None` даёт пустую строку.
    Регистр НЕ трогает — за это отвечает вызывающий, чтобы не мешать сверку и
    показ значения человеку.
    """
    if value is None:
        return ""
    return " ".join(str(value).split())


def cell_text_is_blank(value: Any) -> bool:
    """Пуста ли ячейка по тексту.

    Пустотой считаются `None`, пустая строка, пробелы, табуляции, переносы и
    неразрывный пробел (все они схлопываются `normalized_cell_text`). Ноль
    пустотой НЕ считается: `0` — это значение.
    """
    return normalized_cell_text(value) == ""
```

- [ ] **Step 3: Написать падающие тесты guard'а**

В конец `backend/tests/unit/parser/test_estimate.py`. Хелпер `_minimal_sheet` пока шапку не ставит — на Step 5 он её получит, и тест корректной шапки станет зелёным.

```python
class TestColumnHeaderGuard:
    """Чужая раскладка колонок A–D — структурный отказ (спека Ф2 §2.1).

    Не предупреждение: колонки A, B, C, D читаются по фиксированным позициям,
    поэтому при чужой шапке недостоверен весь позиционный разбор, а не только
    колонка статьи. Та же граница, что у `_validate_contractor_blocks`.
    """

    def test_correct_headers_parse(self):
        ws = _minimal_sheet(11)
        result = parse_worksheet(ws)
        assert result.data is not None

    @pytest.mark.parametrize(
        ("column", "letter", "wrong_value"),
        [
            (2, "B", "Глава"),
            (3, "C", "Артикул СМР"),
            (4, "D", "Наименование видов работ"),
        ],
    )
    def test_wrong_header_in_any_column_is_rejected(self, column, letter, wrong_value):
        """Колонка A сюда НЕ входит намеренно.

        Строка шапки ищется именно по маркеру в A, поэтому испорченный A даёт не
        «чужой заголовок», а «строка не найдена» — и то сообщение фактическое
        значение не называет (спека §2.1, пункт 4). Этот случай покрывает
        `test_missing_header_row_is_rejected_without_naming_a_row`.
        """
        ws = _minimal_sheet(11)
        ws.cell(row=9, column=column, value=wrong_value)

        with pytest.raises(EstimateParseError) as exc:
            parse_worksheet(ws)

        message = str(exc.value)
        assert letter in message
        assert wrong_value in message

    def test_missing_header_row_is_rejected_without_naming_a_row(self):
        """Маркер «№ п/п» испорчен — «ту самую» строку определить нельзя.

        Поэтому сообщение называет ожидаемый маркер и просмотренный диапазон,
        но НЕ фактическое значение: назвать его было бы выдумкой.
        """
        ws = _minimal_sheet(11)
        ws.cell(row=9, column=1, value="Порядковый номер")

        with pytest.raises(EstimateParseError, match="№ п/п"):
            parse_worksheet(ws)

    def test_header_row_is_found_not_hardcoded(self):
        """Шапка сдвинута на строку — файл валиден и должен разбираться.

        Ради этого строка ищется по маркеру, а не берётся константой 9.
        """
        ws = _minimal_sheet(11)
        for column in TABLE_PARSE_POSITION_COLUMN_HEADERS:
            ws.cell(row=9, column=column, value=None)
        for column, title in TABLE_PARSE_POSITION_COLUMN_HEADERS.items():
            ws.cell(row=8, column=column, value=title)

        result = parse_worksheet(ws)
        assert result.data is not None

    def test_headers_are_compared_ignoring_case_and_extra_spaces(self):
        ws = _minimal_sheet(11)
        ws.cell(row=9, column=3, value="  статья  смр  ")

        result = parse_worksheet(ws)
        assert result.data is not None
```

Импорты в начало файла добавить к существующим:

```python
from parser.constants import TABLE_PARSE_POSITION_COLUMN_HEADERS
```

- [ ] **Step 4: Прогнать — убедиться, что падают по причине «нет проверки»**

Run: `cd backend && uv run pytest tests/unit/parser/test_estimate.py -k ColumnHeaderGuard -q`
Expected: FAIL. Тесты с чужим заголовком падают с `DID NOT RAISE EstimateParseError` — проверки ещё нет. Это правильная причина; если увидите другую, разберитесь до перехода дальше.

- [ ] **Step 5: Добавить шапку в синтетические листы**

В `backend/tests/unit/parser/test_estimate.py`, в `_minimal_sheet` — после строки `ws["D11"] = "Лот №1 Тестовый"`:

```python
    for column, title in TABLE_PARSE_POSITION_COLUMN_HEADERS.items():
        ws.cell(row=9, column=column, value=title)
```

**И там же — номер с разделом в саму строку лота:**

```python
    ws["A11"] = 1
    ws["B11"] = 1
```

Это не косметика, а обязательное условие Task 2, и лучше внести его сразу. Замерено: `read_lots_and_boundaries:85` передаёт `start_row` в `get_lot_positions` без сдвига, а цикл идёт `range(lot_start_row, lot_end_row + 1)` — **строка лота входит в обход позиций**. Сейчас в хелпере заполнена только `D11`, поэтому после Task 2 строка лота стала бы «кандидатом с пустыми A и B» и файл отвергался бы с названием «Лот №1 Тестовый». Реальные файлы и `test_performance.py` несут в этой строке `A=1, B=1` — хелпер приводится к фактической форме.

В `backend/tests/unit/parser/test_performance.py`, где уже стоит `ws["A9"] = "№ п/п"` — дополнить тремя соседями:

```python
    ws["B9"] = "№ раздела"
    ws["C9"] = "Статья СМР"
    ws["D9"] = "Наименование работ"
```

Это не подгонка под новый код, а требование нового контракта: лист без шапки A–D больше не считается сметой ГП.

- [ ] **Step 6: Реализовать guard**

В `backend/parser/estimate.py`. Импорты дополнить:

```python
from .constants import JSON_KEY_EXECUTOR, JSON_KEY_LOTS, TABLE_PARSE_POSITION_COLUMN_HEADERS
from .sheet import normalized_cell_text
```

Две функции — после `_validate_contractor_blocks`:

```python
def _find_column_header_row(ws: Worksheet, search_start_row: int, search_end_row: int) -> int | None:
    """Ищет строку шапки таблицы позиций по маркеру в колонке A.

    Область поиска — строго между строкой заголовка контрагентов и первой
    строкой данных (маркером лота). Весь лист не сканируется: обе границы к
    моменту вызова уже известны, а за ними шапки заведомо нет.

    Строка 9 не зашита константой намеренно: файл со сдвинутой на строку шапкой
    имеет верную раскладку и обязан разбираться.

    Args:
        ws: лист Excel.
        search_start_row: строка заголовка контрагентов (не включается).
        search_end_row: первая строка данных (не включается).

    Returns:
        Номер строки шапки либо None, если маркер не найден.
    """
    expected = normalized_cell_text(TABLE_PARSE_POSITION_COLUMN_HEADERS[1]).casefold()
    for row in range(search_start_row + 1, search_end_row):
        if normalized_cell_text(ws.cell(row=row, column=1).value).casefold() == expected:
            return row
    return None


def _validate_column_headers(
    ws: Worksheet,
    contractors: list[dict[str, Any]],
    lot_starts: list[dict[str, Any]],
) -> None:
    """Отвергает файлы, у которых шапка общих колонок не та.

    Колонки A, B, C, D читаются по ФИКСИРОВАННЫМ позициям
    (`get_lot_positions`), поэтому чужая шапка означает, что недостоверен весь
    позиционный разбор: номер, раздел, статья и наименование могли бы прийти не
    из тех ячеек. Это отказ, а не предупреждение, — та же граница, что у
    `_validate_contractor_blocks`: предупреждение обещает импорт, а импортировать
    здесь нечего.

    Args:
        ws: лист Excel.
        contractors: результат `read_contractors` целиком.
        lot_starts: результат `find_lot_starts`.

    Raises:
        EstimateParseError: строка шапки не найдена либо хотя бы один заголовок
            не совпал с ожидаемым.
    """
    header_marker_row = contractors[0].get("row_start")
    first_lot_row = lot_starts[0]["start_row"]
    if header_marker_row is None:
        header_marker_row = CONTRACTOR_SCAN_ROW_START - 1

    header_row = _find_column_header_row(ws, header_marker_row, first_lot_row)
    if header_row is None:
        raise EstimateParseError(
            f"Не найдена шапка таблицы позиций: в колонке A строк "
            f"{header_marker_row + 1}–{first_lot_row - 1} нет ячейки «"
            f"{TABLE_PARSE_POSITION_COLUMN_HEADERS[1]}». Колонки A–D читаются по "
            "фиксированным позициям, и без шапки нечем подтвердить, что раскладка "
            "та самая."
        )

    for column, expected_title in TABLE_PARSE_POSITION_COLUMN_HEADERS.items():
        actual = normalized_cell_text(ws.cell(row=header_row, column=column).value)
        if actual.casefold() != normalized_cell_text(expected_title).casefold():
            letter = get_column_letter(column)
            raise EstimateParseError(
                f"Колонка {letter} шапки (строка {header_row}) озаглавлена "
                f"«{actual}», ожидалось «{expected_title}». Колонки A–D читаются "
                "по фиксированным позициям, поэтому при другой раскладке номер, "
                "раздел, статья и наименование пришли бы не из тех ячеек."
            )
```

Импорты для них:

```python
from openpyxl.utils import get_column_letter

from .constants import CONTRACTOR_SCAN_ROW_START
```

Вызов — в `parse_worksheet`, сразу после `_validate_contractor_blocks(contractors)`:

```python
    _validate_column_headers(ws, contractors, lot_starts)
```

И дополнить docstring `parse_worksheet` в разделе `Raises`: «…либо шапка общих колонок A–D не совпала с ожидаемой (`_validate_column_headers`)».

- [ ] **Step 7: Прогнать тесты**

Run: `cd backend && uv run pytest tests/unit/parser/ -q`
Expected: PASS всё, включая `TestColumnHeaderGuard` и `test_performance.py`. Если падают старые тесты — значит какой-то синтетический лист остался без шапки; добавить шапку, а не ослаблять проверку.

Run: `cd backend && uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 8: Отдать оркестратору**

Коммит делает оркестратор. Ничего не коммитить.

---

### Task 2: агрегатная строка допработ

**Files:**
- Modify: `backend/parser/constants.py`
- Modify: `backend/parser/get_lot_positions.py`
- Modify: `backend/parser/get_proposals.py`
- Modify: `backend/parser/estimate.py` (только `PARSER_VERSION`)
- Test: `backend/tests/unit/parser/test_get_lot_positions.py`

**Interfaces:**
- Consumes: `sheet.normalized_cell_text`, `sheet.cell_text_is_blank` из Task 1.
- Produces: `get_lot_positions(...) -> LotRows`, где `LotRows.positions: dict[str, Any]` и `LotRows.additional_works: dict[str, Any] | None`; ключ `contractor_items["additional_works"]`.

- [ ] **Step 0: Вынести `EstimateParseError` в отдельный модуль**

Это не развилка, а установленный факт: цепочка импортов `estimate.py:33` →
`read_lots_and_boundaries.py:24` → `get_proposals.py:35` → `get_lot_positions.py`
замкнётся, если `get_lot_positions` импортирует из `.estimate`; вдобавок
`EstimateParseError` объявлен в `estimate.py:46`, то есть **после** его импортов.

Создать `backend/parser/errors.py`:

```python
"""Исключения парсера.

Вынесено из `estimate.py` отдельным модулем, потому что `get_lot_positions`
тоже отвергает структурно непригодные файлы, а импорт из `estimate` замкнул бы
цикл: `estimate` → `read_lots_and_boundaries` → `get_proposals` →
`get_lot_positions`. Модуль намеренно ничего не импортирует из пакета — он
нижний слой.
"""

from __future__ import annotations


class EstimateParseError(Exception):
    """Файл не разбирается как смета ГП.

    Поднимается только на структурно непригодных файлах. Всё, что можно
    прочитать с оговорками, читается и попадает в `ParseResult.warnings`.
    """
```

В `backend/parser/estimate.py` удалить объявление класса (строки 46–51) и
импортировать его:

```python
from .errors import EstimateParseError
```

**Публичное имя менять нельзя:** на `from parser import EstimateParseError`
завязаны `services/import_pipeline.py:39` и тесты. Проверить, что
`backend/parser/__init__.py` по-прежнему экспортирует его (при необходимости
поправить путь импорта внутри `__init__.py`, оставив имя в `__all__`).

Тест — в `backend/tests/unit/parser/test_estimate.py`:

```python
def test_parse_error_is_importable_from_the_package_root():
    """Публичный контракт: `from parser import EstimateParseError`.

    Класс переехал в `parser.errors` ради разрыва цикла импортов, но снаружи
    имя прежнее — на него завязан `services/import_pipeline`. Сверяется
    идентичность объекта, а не только импортируемость: два разных класса с одним
    именем ловились бы `except` мимо.
    """
    import parser as parser_package
    from parser.errors import EstimateParseError as FromErrors

    assert parser_package.EstimateParseError is FromErrors
```

Run: `cd backend && uv run pytest tests/unit/parser/ -q`
Expected: PASS — переезд исключения ничего не меняет по поведению.

- [ ] **Step 1: Добавить константы**

В `backend/parser/constants.py`:

```python
#: Название агрегатной строки допработ в колонке D. Замер: 159-ТУ строка 1523;
#: в 42-ТУ и 449-ТУ такой строки нет вовсе — это валидное состояние.
TABLE_PARSE_ADDITIONAL_WORKS_TITLE = "Дополнительные работы"

JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS = "additional_works"  # Агрегатная строка допработ
JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW = "source_row"  # Номер строки листа, откуда она взята
```

- [ ] **Step 2: Написать падающие тесты**

В конец `backend/tests/unit/parser/test_get_lot_positions.py`. Фикстура `sample_worksheet` из этого файла даёт лист с позициями в строках 13–15; агрегатную строку кладём следом.

```python
class TestAdditionalWorksRow:
    """Агрегатная строка «Дополнительные работы» (спека Ф2 §2.2).

    Признак — пустые A и B плюс точное название в D. Замер: строк с пустыми
    A и B во всех трёх реальных офертах ровно одна, ложных нет; наивное
    «D содержит „дополнительн“» дало бы 3–5 попаданий на файл, почти все —
    настоящие позиции.
    """

    def test_recognized_row_goes_to_additional_works(self, sample_worksheet):
        ws = sample_worksheet
        ws.cell(row=16, column=4, value="Дополнительные работы")
        ws.cell(row=16, column=10, value=12675964.53)

        result = get_lot_positions(ws, CONTRACTOR, lot_start_row=13, lot_end_row=16)

        assert result.additional_works is not None
        assert result.additional_works["job_title"] == "Дополнительные работы"
        assert result.additional_works["source_row"] == 16

    def test_money_is_a_decimal_string_not_float(self, sample_worksheet):
        """Деньги идут через parse_contractor_row: строка, не float (AGENTS.md §3).

        Проверка адресная, а не «нет float среди values()»: деньги лежат ВЛОЖЕННО
        в `unit_cost` и `total_cost`, поэтому обход верхнего уровня их не видит и
        прошёл бы даже при float внутри. Раскладка замерена: у `CONTRACTOR`
        `column_start = 9` и `colspan = 8`, а ключи colspan-8 начинаются с
        `unit_cost.materials`, значит колонка 10 — это `unit_cost.works`.
        `money_to_json(12675964.53)` даёт ровно `"12675964.53"` (замерено).
        """
        ws = sample_worksheet
        ws.cell(row=16, column=4, value="Дополнительные работы")
        ws.cell(row=16, column=10, value=12675964.53)

        result = get_lot_positions(ws, CONTRACTOR, lot_start_row=13, lot_end_row=16)

        value = result.additional_works["unit_cost"]["works"]
        assert value == "12675964.53"
        assert isinstance(value, str)
        assert _floats_anywhere(result.additional_works) == []

    def test_row_also_stays_in_positions(self, sample_worksheet):
        """ПЕРЕХОДНОЕ решение Ф2 (спека §2.2): строка остаётся позицией.

        Импортёр читает только `positions`; если убрать её здесь, деньги
        исчезнут из аналитики до выхода Ф4. Исключение делает Ф4 — атомарно с
        записью в `estimate_additional_works`. Тест обязан упасть, если кто-то
        «доделает» исключение раньше.
        """
        ws = sample_worksheet
        ws.cell(row=16, column=4, value="Дополнительные работы")

        result = get_lot_positions(ws, CONTRACTOR, lot_start_row=13, lot_end_row=16)

        titles = [item["job_title"] for item in result.positions.values()]
        assert "Дополнительные работы" in titles

    def test_absent_row_is_valid(self, sample_worksheet):
        """42-ТУ и 449-ТУ: строки нет вовсе, это не ошибка и не warning."""
        result = get_lot_positions(sample_worksheet, CONTRACTOR, lot_start_row=13, lot_end_row=15)

        assert result.additional_works is None
        assert result.positions != {}

    def test_candidate_with_other_title_is_rejected(self, sample_worksheet):
        ws = sample_worksheet
        ws.cell(row=16, column=4, value="Прочие затраты")

        with pytest.raises(EstimateParseError, match="Прочие затраты"):
            get_lot_positions(ws, CONTRACTOR, lot_start_row=13, lot_end_row=16)

    def test_second_candidate_is_rejected(self, sample_worksheet):
        ws = sample_worksheet
        ws.cell(row=16, column=4, value="Дополнительные работы")
        ws.cell(row=17, column=4, value="Дополнительные работы")

        with pytest.raises(EstimateParseError, match="16"):
            get_lot_positions(ws, CONTRACTOR, lot_start_row=13, lot_end_row=17)

    def test_title_is_compared_normalized(self, sample_worksheet):
        """Регистр и лишние пробелы в названии не мешают распознаванию.

        Без этого теста реализация с простым `==` тоже была бы зелёной, а спека
        §2.2 требует сверки нормализованного названия.
        """
        ws = sample_worksheet
        ws.cell(row=16, column=4, value="  ДОПОЛНИТЕЛЬНЫЕ   РАБОТЫ ")

        result = get_lot_positions(ws, CONTRACTOR, lot_start_row=13, lot_end_row=16)

        assert result.additional_works is not None
        assert result.additional_works["job_title"] == "  ДОПОЛНИТЕЛЬНЫЕ   РАБОТЫ "

    def test_blank_is_by_text_not_by_none(self, sample_worksheet):
        """Пробел и неразрывный пробел в A/B — тоже пустота (спека §2.2)."""
        ws = sample_worksheet
        ws.cell(row=16, column=1, value=" ")
        ws.cell(row=16, column=2, value=" ")
        ws.cell(row=16, column=4, value="Дополнительные работы")

        result = get_lot_positions(ws, CONTRACTOR, lot_start_row=13, lot_end_row=16)

        assert result.additional_works is not None
```

Рекурсивный хелпер — рядом с тестами этого файла:

```python
def _floats_anywhere(value, path=""):
    """Пути до всех float внутри вложенной структуры. Пусто — значит их нет."""
    if isinstance(value, float):
        return [path or "<root>"]
    if isinstance(value, dict):
        found = []
        for key, nested in value.items():
            found.extend(_floats_anywhere(nested, f"{path}.{key}" if path else str(key)))
        return found
    return []
```

`CONTRACTOR` — тот же словарь подрядчика, что уже используют существующие тесты этого файла (`{"column_start": 9, "merged_shape": {"colspan": 8}}`); взять его оттуда, не выдумывать новый. Импорт `EstimateParseError` — из `parser.errors` (Step 0).

**Тесты итоговой JSON-формы** — в `backend/tests/unit/parser/test_estimate.py`. Тесты `LotRows` выше доказывают только работу `get_lot_positions`; что `get_proposals` действительно положил поле рядом с `positions` и `summary`, они не проверяют, и разовый скрипт это не защитит:

```python
class TestAdditionalWorksInJson:
    """Поле доезжает до итоговой структуры рядом с positions и summary."""

    def test_additional_works_lands_next_to_positions(self):
        ws = _minimal_sheet(11)
        ws.cell(row=12, column=1, value=1)
        ws.cell(row=12, column=2, value="1")
        ws.cell(row=12, column=4, value="Обычная работа")
        ws.cell(row=13, column=4, value="Дополнительные работы")

        items = _proposal(parse_worksheet(ws))["contractor_items"]

        assert set(items) >= {"positions", "summary", "additional_works"}
        assert items["additional_works"]["job_title"] == "Дополнительные работы"
        assert items["additional_works"]["source_row"] == 13

    def test_additional_works_is_none_when_row_absent(self):
        """42-ТУ и 449-ТУ: ключ есть, значение None — это валидное состояние."""
        ws = _minimal_sheet(11)
        ws.cell(row=12, column=1, value=1)
        ws.cell(row=12, column=2, value="1")
        ws.cell(row=12, column=4, value="Обычная работа")

        items = _proposal(parse_worksheet(ws))["contractor_items"]

        assert items["additional_works"] is None
```

`_proposal` — существующий хелпер этого файла (`test_estimate.py:507`).

Плюс тест версии — в `backend/tests/unit/parser/test_estimate.py`, к остальным:

```python
def test_parser_version_is_bumped_for_the_new_key():
    """1.1.0: в contractor_items появился `additional_works` (спека Ф2 §2.4).

    Версия — часть контракта: она ложится в `estimate_raw_data.parser_version`,
    и по ней потом отличают, каким кодом разобран сохранённый JSON.
    """
    assert PARSER_VERSION == "1.1.0"
```

Импорт `PARSER_VERSION` добавить к существующим импортам из `parser.estimate`.

- [ ] **Step 3: Прогнать — убедиться, что падают**

Run: `cd backend && uv run pytest tests/unit/parser/test_get_lot_positions.py -k AdditionalWorksRow -q`
Expected: FAIL с `AttributeError: 'dict' object has no attribute 'additional_works'` — функция ещё возвращает словарь.

- [ ] **Step 4: Реализовать распознавание**

В `backend/parser/get_lot_positions.py`. Импорты дополнить:

```python
from dataclasses import dataclass

from .constants import (
    JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW,
    TABLE_PARSE_ADDITIONAL_WORKS_TITLE,
)
from .sheet import cell_text_is_blank, contractor_last_column, normalized_cell_text
```

`EstimateParseError` берётся из `parser.errors` (создан в Step 0), а не из `.estimate`.

Результат функции:

```python
@dataclass(frozen=True)
class LotRows:
    """Строки лота: позиции и, если она есть, агрегатная строка допработ."""

    positions: dict[str, Any]
    """Позиции подрядчика; ключи — порядковые номера в виде строк."""

    additional_works: dict[str, Any] | None = None
    """Агрегатная строка «Дополнительные работы» либо None, если её в файле нет.

    В Ф2 та же строка ПРИСУТСТВУЕТ и в `positions` — переходное решение спеки
    §2.2; исключение делает Ф4.
    """
```

Внутри цикла, **после** `item.update(contractor_specific_data)` и **перед** `positions[str(item_index)] = item`:

```python
        if cell_text_is_blank(item[JSON_KEY_NUMBER]) and cell_text_is_blank(item[JSON_KEY_CHAPTER_NUMBER]):
            # Строка без номера и без раздела — не позиция и не раздел. В смете
            # ГП такая ровно одна: агрегатная строка допработ перед ИТОГО.
            title = normalized_cell_text(item[JSON_KEY_JOB_TITLE])
            if title.casefold() != TABLE_PARSE_ADDITIONAL_WORKS_TITLE.casefold():
                raise EstimateParseError(
                    f"Строка {current_row_num} не имеет ни номера, ни раздела, "
                    f"а называется «{title}». Единственная известная строка такого "
                    f"вида — «{TABLE_PARSE_ADDITIONAL_WORKS_TITLE}»; её деньги мы "
                    "умеем отнести, а эти — нет, и они молча легли бы в статью "
                    "последнего раздела."
                )
            if additional_works is not None:
                raise EstimateParseError(
                    f"Агрегатных строк «{TABLE_PARSE_ADDITIONAL_WORKS_TITLE}» "
                    f"больше одной: строки "
                    f"{additional_works[JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW]} и "
                    f"{current_row_num}. Ожидается не больше одной на лот."
                )
            additional_works = {
                JSON_KEY_JOB_TITLE: item[JSON_KEY_JOB_TITLE],
                JSON_KEY_ADDITIONAL_WORKS_SOURCE_ROW: current_row_num,
                **contractor_specific_data,
            }
            # Строка НЕ пропускается: в Ф2 она остаётся позицией (спека §2.2).
```

Перед циклом объявить `additional_works: dict[str, Any] | None = None`, в конце вернуть `LotRows(positions=positions, additional_works=additional_works)`. Обновить docstring и аннотацию возврата.

- [ ] **Step 5: Развести результат в `get_proposals`**

В `backend/parser/get_proposals.py`, заменить строки 85 и 88–91:

```python
        lot_rows = get_lot_positions(ws, contractor_details, lot_start_row=start_row, lot_end_row=end_row)
        summary_data = get_summary(ws, contractor_details, search_start_row=start_row)

        contractor_items_data = {
            JSON_KEY_CONTRACTOR_POSITIONS: lot_rows.positions,
            JSON_KEY_CONTRACTOR_SUMMARY: summary_data,
            JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS: lot_rows.additional_works,
        }
```

Импорт `JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS` добавить к существующим.

- [ ] **Step 6: Обновить 14 существующих вызовов**

В `backend/tests/unit/parser/test_get_lot_positions.py` каждый `get_lot_positions(...)` теперь отдаёт `LotRows`. Там, где результат использовался как словарь позиций, добавить `.positions`. **Ожидания не менять** — только доступ к полю. Если после правки какой-то тест всё равно красный, это дефект реализации, а не повод править ожидание: остановиться и эскалировать.

- [ ] **Step 7: Поднять `PARSER_VERSION`**

В `backend/parser/estimate.py`:

```python
PARSER_VERSION = "1.1.0"
```

И дополнить комментарий над константой:

```python
# 1.1.0 (Ф2): в contractor_items появился ключ `additional_works`. Версия
# минорная — структура только дополнена, из `positions` ничего не убрано, поэтому
# существующий импортёр не ломается. Исключение агрегатной строки из `positions`
# (Ф4) будет ломающим и потребует следующего подъёма.
```

- [ ] **Step 8: Прогнать тесты**

Run: `cd backend && uv run pytest tests/unit/parser/ -q`
Expected: PASS всё.

Run: `cd backend && uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 9: Отдать оркестратору**

Коммит делает оркестратор.

---

### Task 3: реальные файлы, негативная проверка, devlog и PR

**Files:**
- Create: `docs/devlog/2026-08-06-parser-smr-article.md`

**Interfaces:**
- Consumes: результаты Task 1–2.

- [ ] **Step 1: Проверить, что на реальных офертах ничего не поехало**

Ключевое требование DoD: счётчики и суммы **не изменились**. Прогнать интеграционные тесты, работающие на реальных файлах и fixture:

```bash
cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/ -q
```
Expected: PASS. Любой поехавший baseline — **сигнал дефекта**, разбираться до правки, а не обновлять число.

- [ ] **Step 2: Замер по трём офертам**

Разовым скриптом (в `scratchpad`, не коммитить; вывод — ASCII, без кириллицы в терминал) разобрать три файла из `samples/` и напечатать: число позиций, наличие `additional_works`, `source_row`, тип значения суммы. Ожидание: 159-ТУ — поле заполнено, `source_row = 1523`, тип `str`; 42-ТУ и 449-ТУ — `None`; число позиций у всех трёх такое же, как до фичи.

- [ ] **Step 3: Негативная проверка снятием защиты — ОРКЕСТРАТОР, НЕ ДЕЛЕГИРОВАТЬ**

По протоколу [verifying-guards.md](../../insights/verifying-guards.md), полностью:

1. контрольный прогон на целом коде: `pytest tests/unit/parser/ -q` — зелёный;
2. снять guard — закомментировать вызов `_validate_column_headers(ws, contractors, lot_starts)` в `parse_worksheet`, **с `assert`, что шаблон замены найден** (иначе молча не применившийся патч выглядит как доказанная защита);
3. прогнать `pytest tests/unit/parser/test_estimate.py -k ColumnHeaderGuard -q` — должно стать **красным**;
4. вернуть guard, сверить файл побайтово (`sha256sum` до и после), прогнать снова — зелёный.

То же для агрегатной строки: снять `raise` на втором кандидате → тест `test_second_candidate_is_rejected` обязан покраснеть.

- [ ] **Step 4: `just ci`**

Run: `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 just ci` (в фоне — идёт ~2,5 минуты)
Expected: PASS. Записать число тестов до и после.

- [ ] **Step 5: Написать devlog**

`docs/devlog/2026-08-06-parser-smr-article.md`. Обязательно: что сделано; замеры (число тестов до/после, результаты по трём офертам, вывод негативных проверок дословно); **подтверждение, что счётчики позиций не изменились** — главный признак безопасности перехода; отступления от плана; хвосты — в первую очередь **обязательство для Ф4 из спеки §7** (дубль в уже сохранённом raw_data), чтобы оно не потерялось между фичами.

- [ ] **Step 6: Коммит devlog, push, PR**

PR со ссылками на рамку фазы, спеку и devlog. В описании явно сказать: **Ф2 не чинит бизнес-дефект** с 12,7 млн в чужой статье — она ставит guard и учится узнавать строку, чинит Ф4.

---

## Self-Review плана

**Покрытие спеки:** §2.1 guard → Task 1 (константы Step 1, утилиты Step 2, реализация Step 6, тесты Step 3); поиск строки, а не константа → Task 1 Step 6 `_find_column_header_row` + тест `test_header_row_is_found_not_hardcoded`; сообщение без «что нашли» при ненайденной шапке → Task 1 Step 6 первый `raise` + тест; §2.2 признак и кардинальность → Task 2 Step 4 + пять тестов Step 2; переходное решение «остаётся в positions» → Task 2 Step 2 `test_row_also_stays_in_positions`; §2.3 форма и деньги через `parse_contractor_row` → Task 2 Step 4 + тест на отсутствие `float`; §2.4 версия → Task 2 Step 7; §4 «синтетические листы дополняются шапкой» → Task 1 Step 5; §4 «baselines не меняются» → Task 3 Step 1–2; §6 DoD снятие защиты → Task 3 Step 3; §7 обязательство Ф4 → Task 3 Step 5 (в devlog).

**Пробел, найденный при сверке и закрытый в плане:** спека требует тест «`PARSER_VERSION` равен 1.1.0», а первая редакция плана меняла только константу. Тест `test_parser_version_is_bumped_for_the_new_key` добавлен в Task 2 Step 2.

**Четыре дефекта, найденные внешним ревью и закрытые в плане** (каждый проверен фактом до правки):

1. Тест чужого заголовка в колонке A был **неисполним**: строка шапки ищется именно по маркеру в A, поэтому испорченный A даёт «строка не найдена», а это сообщение фактическое значение не называет. Параметризация оставлена на B–D, случай A покрыт отдельным тестом.
2. Тест денежного типа был **ложно-зелёным**: обход `values()` не видит деньги, вложенные в `unit_cost` / `total_cost`. Заменён адресной сверкой `unit_cost.works == "12675964.53"` (раскладка и строка замерены) плюс рекурсивный запрет `float`.
3. Циклический импорт — **не развилка, а установленный факт** (`estimate.py:33` → `read_lots_and_boundaries.py:24` → `get_proposals.py:35` → `get_lot_positions`, при этом класс объявлен в `estimate.py:46`). План прямо предписывает `parser/errors.py` и тест на публичное имя.
4. **Не было теста итоговой JSON-формы** — `LotRows` не доказывает, что `get_proposals` положил поле в `contractor_items`. Добавлен класс `TestAdditionalWorksInJson` на полный путь через `parse_worksheet`, с проверкой и наличия, и `None`.

**Ещё два, найденные вторым кругом ревью** (проявились именно после появления полнопроходного теста):

5. **`_minimal_sheet` стал бы невалидным.** Строка лота входит в обход позиций (замерено: `read_lots_and_boundaries:85` не сдвигает `start_row`), а в хелпере у неё заполнена только `D11` — после Task 2 она была бы принята за кандидата с пустыми A/B и файл отвергался бы. В Task 1 Step 5 хелпер приводится к фактической форме реальных файлов: `A11 = 1`, `B11 = 1`.
6. **Не было теста нормализации названия** агрегатной строки: все проверки использовали точную строку, поэтому реализация с простым `==` тоже была бы зелёной. Добавлен `test_title_is_compared_normalized` с `"  ДОПОЛНИТЕЛЬНЫЕ   РАБОТЫ "`.

**Согласованность имён:** `normalized_cell_text` / `cell_text_is_blank` (Task 1) используются в Task 2 под теми же именами; `LotRows.positions` / `LotRows.additional_works` — в Task 2 Step 4, 5, 6; `JSON_KEY_CONTRACTOR_ADDITIONAL_WORKS` — в константах и `get_proposals`.

---

## Исполнение в мультимодельной сессии

Роли те же, что на Ф1: **Opus — оркестратор**, **Sonnet — исполнитель**, **Fable — финальное ревью**. Коммиты делает оркестратор.

Оркестратор **не делегирует**: негативную проверку Task 3 Step 3 и решение при любом расхождении с планом.

### Запреты для исполнителей

1. **Не менять ожидания тестов и baselines.** Все числа — из замеров на трёх реальных офертах (спека §1). Расхождение — стоп и эскалация.
2. **Не исключать агрегатную строку из `positions`.** Это Ф4. В Ф2 она копируется и остаётся; на это есть тест.
3. **Не читать деньги из ячеек напрямую** — только через `parse_contractor_row`, иначе в контур вернётся `float`.

### Где исполнитель споткнётся (замерено)

- `layout.py` — **только предупреждения**; структурные отказы живут в `estimate.py`. Не смешивать.
- Три теста в `TestParseEstimateFailures` собирают листы вручную и падают **до** нового guard'а — их править не нужно.
- **Строка лота входит в обход позиций** (`read_lots_and_boundaries:85` отдаёт `start_row` без сдвига). Поэтому у неё обязаны быть заполнены A и B, иначе правило Task 2 примет её за кандидата. В реальных файлах и в `test_performance.py` там `A=1, B=1`; в `_minimal_sheet` это добавляется в Task 1 Step 5.
- В `test_performance.py` шапка частично есть (`A9`), а `A9:A10` объединена вертикально — добавлять B9/C9/D9, объединение не трогать.
- `get_lot_positions` имеет **14** вызовов в своих тестах; смена возврата на `LotRows` требует `.positions` в каждом. Это механика, ожидания при этом не меняются.
- **Цикл импортов реален** (`estimate` → `read_lots_and_boundaries` → `get_proposals` → `get_lot_positions`), поэтому Task 2 начинается с переезда `EstimateParseError` в `parser/errors.py`. Публичное имя `parser.EstimateParseError` менять нельзя: на него завязан `services/import_pipeline.py:39`.
- Деньги в результате лежат **вложенно** (`unit_cost.works`, а не плоским ключом) — проверять адресно, обход верхнего уровня их не увидит.
- Прогон `pytest tests/` требует `TEST_DATABASE_URL` и снятого `DATABASE_URL`, иначе integration молча пропускаются.
