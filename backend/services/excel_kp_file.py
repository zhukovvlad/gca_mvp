"""Книга xlsx файла подрядчику (спека 4б §2.8).

Модуль печатает готовые `FileHeader` и строки файла (`services.kp_file`) и не
считает ничего: предпросмотр и книга читают одни и те же объекты, поэтому они
равны по построению. Тексты строк уже прошли `safe_str` при сборке; повторный проход идемпотентен и
стоит в общих помощниках листа. Названия тендера, объекта и участника в шапку
идут сырыми после постоянного префикса, формулой они стать не могут.

Один лист: шапка (тендер и объект, этап, участник, дата, база НДС, источник,
ценовой уровень), при выбранном ряде ИПЦ — блок приведения по годам, затем
таблица. Деньги и проценты пишутся `Decimal`-числами без округления значения
(округляет формат ячейки). Наши столбцы залиты одним цветом; разделы — жирная
строка через всю ширину таблицы.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from openpyxl import Workbook

from services.excel import (
    C_HEADER_BG,
    C_OURS_BG,
    C_TOTAL_BG,
    FMT_MONEY,
    FMT_PCT,
    FMT_QTY,
    apply_column_widths,
    fill,
    font,
    workbook_bytes,
    write_banner,
    write_caption,
    write_cell,
    write_column_headers,
    write_inflation_block,
)
from services.kp_file import FileHeader, FileLine, FileSection

__all__ = ["KP_FILE_COLUMNS", "build_kp_file", "file_columns"]

_INFLATION_LABEL = "Приведение по ИПЦ"

#: Столбцы листа: подпись, ширина, формат числа, выравнивание. Столбец
#: «Приведение по ИПЦ» печатается только при выбранном ряде.
KP_FILE_COLUMNS: list[tuple[str, int, str | None, str]] = [
    ("№", 8, "@", "left"),
    ("Наименование", 48, None, "left"),
    ("Ед.", 8, None, "left"),
    ("Кол-во", 12, FMT_QTY, "right"),
    ("Цена за ед.", 14, FMT_MONEY, "right"),
    ("Стоимость", 16, FMT_MONEY, "right"),
    ("Наша расценка за ед.", 16, FMT_MONEY, "right"),
    ("Отклонение, %", 12, FMT_PCT, "right"),
    ("Источник", 48, None, "left"),
    (_INFLATION_LABEL, 44, None, "left"),
    ("Комментарий", 48, None, "left"),
]

#: Ключ поля строки предпросмотра у каждого столбца листа.
_COLUMN_KEYS = {
    "№": "number",
    "Наименование": "title",
    "Ед.": "unit",
    "Кол-во": "quantity",
    "Цена за ед.": "kp_unit_price",
    "Стоимость": "kp_total",
    "Наша расценка за ед.": "ours",
    "Отклонение, %": "deviation_pct",
    "Источник": "source",
    _INFLATION_LABEL: "inflation",
    "Комментарий": "comment",
}

#: Первый из наших столбцов («Наша расценка за ед.»), счёт с единицы.
_FIRST_OURS_COL = 7
_TEXT_LABELS = {"Наименование", "Источник", _INFLATION_LABEL, "Комментарий"}


def _columns(with_inflation: bool) -> list[tuple[str, int, str | None, str]]:
    return [spec for spec in KP_FILE_COLUMNS if with_inflation or spec[0] != _INFLATION_LABEL]


def file_columns(with_inflation: bool) -> list[dict[str, str]]:
    """Столбцы листа для предпросмотра: ключ поля строки и подпись, в порядке книги."""
    return [{"key": _COLUMN_KEYS[spec[0]], "title": spec[0]} for spec in _columns(with_inflation)]


def _write_section(ws, row: int, section: FileSection, width: int) -> None:
    for col in range(1, width + 1):
        value = section.number if col == 1 else section.title if col == 2 else None
        write_cell(ws, row, col, value, cell_font=font(bold=True), cell_fill=fill(C_TOTAL_BG))


def _write_line(ws, row: int, line: FileLine, columns: list[tuple[str, int, str | None, str]]) -> None:
    by_label: dict[str, str | Decimal | None] = {
        "№": line.number,
        "Наименование": line.title,
        "Ед.": line.unit,
        "Кол-во": line.quantity,
        "Цена за ед.": line.kp_unit_price,
        "Стоимость": line.kp_total,
        "Наша расценка за ед.": line.ours,
        "Отклонение, %": line.deviation_pct,
        "Источник": line.source,
        _INFLATION_LABEL: line.inflation,
        "Комментарий": line.comment,
    }
    for col, (label, _width, number_format, horizontal) in enumerate(columns, start=1):
        write_cell(
            ws,
            row,
            col,
            by_label[label],
            cell_fill=fill(C_OURS_BG) if col >= _FIRST_OURS_COL else None,
            number_format=number_format,
            horizontal=horizontal,
            wrap=label in _TEXT_LABELS,
        )


def build_kp_file(header: FileHeader, rows: Sequence[FileSection | FileLine]) -> bytes:
    """Книга файла подрядчику из шапки и строк в порядке сметы КП."""
    with_inflation = header.inflation_years is not None
    columns = _columns(with_inflation)
    width = len(columns)

    wb = Workbook()
    ws = wb.active
    ws.title = "Наши расценки"
    apply_column_widths(ws, columns)

    row = write_banner(ws, 1, f"Тендер: {header.tender} · Объект: {header.object}", width, bg=C_HEADER_BG)
    for text in (
        header.round,
        f"Участник: {header.contractor}",
        f"Дата выгрузки: {header.exported_on}",
        header.vat_label,
        header.source_label,
        f"Ценовой уровень: {header.level_label}",
    ):
        row = write_caption(ws, row, text)

    if with_inflation:
        row = write_inflation_block(
            ws,
            row,
            {
                "target_month": header.inflation_target_month,
                "series_name": header.inflation_series,
                "used_years": header.inflation_years,
            },
            lead="Цены договоров приведены к:",
        )
    row += 1  # пустая строка-разделитель перед таблицей

    row = write_column_headers(ws, row, columns)
    for item in rows:
        if isinstance(item, FileSection):
            _write_section(ws, row, item, width)
        else:
            _write_line(ws, row, item, columns)
        row += 1
    return workbook_bytes(wb)
