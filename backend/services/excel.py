"""Обвязка openpyxl для выгрузок §7.6.

Перенос из `backend/routers/export.py` источника (udp-tenders @ 98fb67667c44) —
именно обвязка: `_fill`, `_font`, `_align`, `_safe_str`, `_dev_font`, палитра,
секции с итогами. Домен УПД (счета, поставщики, классы материалов) не переносится
(§3 брифинга фазы 6).

Два отличия от источника, оба осознанные:

* **Деньги пишутся `Decimal`, а не `float`** — но у §3 здесь есть граница, и её
  надо знать. openpyxl принимает `Decimal`, однако **сам формат xlsx хранит числа
  как IEEE-754 double**, и «Decimal end-to-end» в файле не продолжается: точность
  ограничена форматом, а не кодом. Замер (`test_reports_api.py::
  test_xlsx_number_precision_boundary`):

  ```
  12000.55             → 12000.55              точно
  1234567890.12        → 1234567890.12         точно
  1075.35475           → 1075.35475            точно
  12345678901234567.89 → 1.234567890123457e+16 ПОТЕРЯ
  ```

  То есть до ~15 значащих цифр значение доезжает без искажений, дальше теряет
  младшие разряды. Суммы договоров ГП на стенде укладываются в 12 значащих цифр,
  то есть запас есть, но утверждать «полная точность» было бы неправдой.

  Альтернатива — писать деньги текстом — отвергнута: она ломает главное, ради чего
  выгрузку и просят. Банк складывает колонку в Excel, а `СУММ` по текстовым ячейкам
  даёт ноль. Числа с известной границей полезнее строк без неё.
* **Отдаём `bytes`, а не `StreamingResponse` с файловым объектом.** Грабли фазы 4:
  Starlette итерирует такой объект построчно и не закрывает хендл. Для xlsx из
  памяти (`BytesIO`) правильный ответ — вернуть содержимое целиком (§7 брифинга).
"""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from services.stage_summary import TAX_GROSS, TAX_NET, TAX_NONE

# ---------------------------------------------------------------------------
#  Стили (перенос из источника)
# ---------------------------------------------------------------------------

def fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)


def font(bold: bool = False, color: str = "000000", size: int = 10) -> Font:
    return Font(bold=bold, color=color, name="Calibri", size=size)


def align(h: str = "left", v: str = "center", wrap: bool = False) -> Alignment:
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)


_THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

C_HEADER_BG = "1F4E79"      # тёмно-синий — шапка документа
C_CLASS_BG = "2E75B6"       # синий — заголовок секции класса
C_COL_BG = "4472C4"         # светлее — строка заголовков колонок
C_CLASS_TOTAL_BG = "9DC3E6" # итог по классу
C_TOTAL_BG = "BDD7EE"       # общий итог
C_ODD = "FFFFFF"
C_EVEN = "EBF3FB"
C_OURS_BG = "E2EFDA"        # наши столбцы в файле подрядчику
C_RED_TEXT = "C00000"       # превышение норматива
C_GREEN_TEXT = "375623"     # экономия

FMT_MONEY = '#,##0.00'
FMT_MONEY_CUR = '#,##0.00 "₽"'
FMT_QTY = "#,##0.###"
FMT_PCT = "+0.0;-0.0;0.0"   # проценты уже в п.п., знак обязателен
FMT_DATE = "DD.MM.YYYY"


def safe_str(value: Any) -> Any:
    """Защита от formula injection (перенос из источника).

    Excel считает формулой всё, что начинается с `=`, `+`, `-` или `@`. Наименование
    работы приходит из чужого XLSX, то есть это недоверенный ввод: строка вида
    `=1+1` или `-500` в ячейке превратилась бы в вычисление, а `=cmd|...` в старых
    сборках Excel — в запуск команды. Апостроф перед значением делает его текстом.

    Числа и даты не трогаем: у них нет такого разбора.
    """
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("=", "+", "-", "@")):
            return "'" + value
    return value


def deviation_font(value: Decimal | None, *, bold: bool = False, size: int = 10) -> Font:
    """Цвет шрифта по знаку отклонения (перенос `_dev_font` из источника).

    `None` — «нет норматива», и цвета у него нет: §10 требует отличать этот случай
    от нуля, а покрасить его как экономию значило бы стереть разницу.

    **Нефинитное значение (Правка 3, ре-ревью финала ветки пересчёта НДС)** —
    `Decimal('NaN')` доезжает сюда из открытого хвоста Ф4 (§5.6: `_money`
    пропускает NaN/Infinity при импорте) через `_deviation_pct`, который
    условие годности не проверяет. `value > 0` для NaN бросает
    `InvalidOperation` в ДЕФОЛТНОМ контексте `decimal` (он трапится там же, где
    и в `money.vat` — не отдельная настройка), и это ПЕРВАЯ строка отчёта с
    такой суммой роняла бы xlsx 500-й уже ПОСЛЕ починки сортировки (Правка 3,
    сортировка — не единственное место, где сравнивается сырой `Decimal`).
    Нефинитное отклонение не «превышение» и не «экономия» — цвета у него тоже
    нет, той же логикой, что у `None`; сама сумма при этом остаётся видимой на
    листе (число просто пишется как есть, см. `quantize_money`)."""
    if value is None or not value.is_finite() or value == 0:
        return font(bold=bold, size=size)
    return font(bold=bold, color=C_RED_TEXT if value > 0 else C_GREEN_TEXT, size=size)


# ---------------------------------------------------------------------------
#  Помощники листа
# ---------------------------------------------------------------------------

def write_cell(
    ws: Worksheet,
    row: int,
    col: int,
    value: Any,
    *,
    cell_font: Font | None = None,
    cell_fill: PatternFill | None = None,
    number_format: str | None = None,
    horizontal: str = "left",
    wrap: bool = False,
    border: bool = True,
):
    cell = ws.cell(row=row, column=col, value=safe_str(value))
    cell.font = cell_font or font()
    if cell_fill is not None:
        cell.fill = cell_fill
    if number_format is not None:
        cell.number_format = number_format
    cell.alignment = align(h=horizontal, wrap=wrap)
    if border:
        cell.border = BORDER
    return cell


def write_banner(ws: Worksheet, row: int, text: str, width: int, *, bg: str, size: int = 11) -> int:
    """Строка на всю ширину таблицы: шапка документа или заголовок секции."""
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=width)
    cell = ws.cell(row=row, column=1, value=safe_str(text))
    cell.fill = fill(bg)
    cell.font = font(bold=True, color="FFFFFF", size=size)
    cell.alignment = align(h="left")
    ws.row_dimensions[row].height = 18
    return row + 1


def write_caption(ws: Worksheet, row: int, text: str, *, wrap: bool = False) -> int:
    """Строка подписи без рамки: подписи шапки не часть таблицы, границ у них нет."""
    cell = ws.cell(row=row, column=1, value=safe_str(text))
    cell.font = font(bold=True, size=9)
    cell.alignment = align(h="left", wrap=wrap)
    return row + 1


#: Подписи налогового состава и ценового уровня — печатаются на каждом листе
#: (AGENTS.md §10). Слова «валовые, как в файлах» и «нетто» держатся дословно.
TAX_BASIS_CAPTION = {
    TAX_GROSS: "Деньги: валовые, как в файлах (одна известная ставка НДС на всех этапах)",
    TAX_NET: "Деньги: нетто (ставки НДС этапов различаются)",
    TAX_NONE: "Деньги: сумм нет — ни на одном этапе не известна база НДС",
}
PRICE_LEVEL_NOMINAL = "номинальный, без приведения"
PRICE_LEVEL_NOMINAL_CAPTION = f"Ценовой уровень: {PRICE_LEVEL_NOMINAL}"

#: Месяцы в именительном: на листе месяц стоит ПОСЛЕ двоеточия, то есть подписью
#: («Цены приведены к: август 2026»), а не продолжением фразы. В подписи оси
#: (`crud.comparison`) он в дательном, потому что там фраза сплошная.
_MONTHS_NOMINATIVE = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def month_label(target_month: str) -> str:
    year, month = target_month.split("-")
    return f"{_MONTHS_NOMINATIVE[int(month) - 1]} {year}"


def iso_date_to_ru(value: str) -> str:
    """`2026-01-12T…` → `12.01.2026`. Дата правки ряда печатается по-русски.

    Разбор строкой, а не `datetime.fromisoformat`: значение уже пришло в ответе
    строкой (`crud.common.iso`), и второе преобразование туда-обратно только
    добавило бы место, где формат может разойтись.
    """
    date_part = value.split("T")[0]
    year, month, day = date_part.split("-")
    return f"{day}.{month}.{year}"


def write_inflation_block(ws: Worksheet, row: int, inflation: dict, *, lead: str = "Цены приведены к:") -> int:
    """Ряд, целевой месяц и ПО КАЖДОМУ году — пять фактов (DoD 13, спека инфляции 2026-08-18-inflation-adjustment-design.md §2.10).

    **Печатается только на листе.** Экран показывает примечание ряда и уровни по
    годам, но НЕ источники: три источника рядом с тремя процентами превращают
    ориентирующую строку в таблицу. Источник нужен там, где задают вопрос «откуда
    8,3 %», — а задают его к артефакту защиты перед банком, то есть к файлу.
    Разделение простое: экран ориентирует, файл защищает (там же, §2.12).

    Пять фактов на год — коэффициент, источник, признак прогноза, дата правки и
    сам год — потому что версионности у ряда нет (там же, §2.10): воспроизводимость
    лежит на файле, и защищённые числа заморожены в нём. Год без источника или без
    даты правки эту роль не исполнил бы.

    `lead` — ведущая фраза первой строки: у выгрузки сравнения «Цены приведены
    к:», у файла подрядчику «Цены договоров приведены к:».
    """
    ws.cell(
        row=row, column=1,
        value=safe_str(
            f"{lead} {month_label(inflation['target_month'])}"
            f" · ряд: «{inflation['series_name']}»"
        ),
    ).font = font(bold=True, size=9)
    row += 1

    for item in inflation["used_years"]:
        kind = "прогноз" if item["is_forecast"] else "факт"
        parts = [
            str(item["year"]),
            format(item["coefficient"], "f"),
            safe_str(item["source"]),
            kind,
        ]
        if item["updated_at"]:
            parts.append(f"правлен {iso_date_to_ru(item['updated_at'])}")
        ws.cell(row=row, column=1, value=safe_str(" · ".join(parts))).font = font(size=9)
        row += 1

    if not inflation["used_years"]:
        # Пустой список — законное состояние (цель совпала с месяцем сметы, DoD 5),
        # и молчать о нём нельзя: читатель обязан понять, что приведение включено,
        # а годов не потребовалось, — иначе он решит, что ряд не доехал до листа.
        ws.cell(
            row=row, column=1,
            value="Коэффициенты за годы не потребовались: цены уже в целевом уровне.",
        ).font = font(size=9)
        row += 1
    return row


def write_column_headers(ws: Worksheet, row: int, columns: list[tuple[str, int, str | None, str]]) -> int:
    for index, (label, _width, _fmt, horizontal) in enumerate(columns, start=1):
        cell = ws.cell(row=row, column=index, value=label)
        cell.fill = fill(C_COL_BG)
        cell.font = font(bold=True, color="FFFFFF", size=9)
        cell.border = BORDER
        cell.alignment = align(h=horizontal, wrap=True)
    ws.row_dimensions[row].height = 30
    return row + 1


def apply_column_widths(ws: Worksheet, columns: list[tuple[str, int, str | None, str]]) -> None:
    for index, (_label, width, _fmt, _horizontal) in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width


def workbook_bytes(wb: Workbook) -> bytes:
    """Готовый xlsx как `bytes` (см. модульную документацию про StreamingResponse)."""
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
