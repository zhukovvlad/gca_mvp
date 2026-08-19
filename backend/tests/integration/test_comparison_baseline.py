"""Golden-снимок ДОФИЧЕВОГО ответа сравнения — основание DoD 1 фичи поправки на
инфляцию (план `2026-08-18-inflation-adjustment.md`, задача 1).

**Снят ДО единой правки `crud/comparison.py`.** Снимок, снятый после правки,
доказывал бы совпадение ответа с самим собой, а не с тем, что отвечало
приложение до фичи: файл-эталон обязан не ехать вместе с проверяемым кодом
(`docs/insights/verifying-guards.md`, слой 5).

Проверяются ДВЕ поверхности, обе перечислены в DoD 1 спеки:

* JSON-ответ `/compare` — целиком, не выборочными ключами. Сериализуется тем же
  `responses._decimal_encoder`, которым отвечает эндпоинт (`analytics.py:193`
  возвращает `decimal_json(build_comparison(...))` и ничего к нему не
  добавляет), поэтому снимок есть буквально тело ответа;
* лист Excel — **не байтами**: XLSX это ZIP, и его байты расходятся из-за
  метаданных архива при идентичном содержимом. В снимок идёт разбор
  перечитанного файла: координата, значение, формат числа, тип и жирность.

Идентификаторы договоров нормализуются позиционно: `contract_number` фабрики —
глобальная последовательность, и снимок со сквозными id краснел бы от порядка
тестов, то есть по причине, не связанной ни с одной правкой кода.
`category_id` при этом оставлен как есть — классификатор засеян миграцией 0005
детерминированно, от порядка тестов не зависит, и его сдвиг означал бы настоящее
изменение схемы, о котором и надо узнать.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook

from crud import comparison as cmp
from responses import _decimal_encoder
from services.excel_comparison import build_comparison_sheet
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"

#: Переменная окружения, которой снимок ЗАПИСЫВАЕТСЯ. Прогон с ней объявлен
#: красным намеренно (см. `_assert_matches_golden`): зелёный прогон обязан быть
#: сравнением, а не записью — иначе набор перестал бы что-либо сторожить,
#: оставаясь зелёным.
GOLDEN_WRITE_ENV = "GCA_GOLDEN_WRITE"

#: Дата в баннере листа — аргумент, а не «сегодня»: `generated_at` печатается в
#: шапку, и `dt.date.today()` сделал бы снимок красным на следующий день.
GENERATED_AT = dt.date(2026, 8, 18)


def normalized(data: dict) -> dict:
    """Ответ с позиционными идентификаторами вместо сквозных id.

    Правило одно на весь документ, а не список мест: `contract_id` встречается на
    каждой ячейке (`_cell_entry`), а `contract_ids` — в каждой медиане
    (`_median_dict`), и перечисление путей разъехалось бы с формой ответа при
    первой же новой ячейке.
    """
    order = {column["contract_id"]: index for index, column in enumerate(data["columns"])}

    def walk(node):
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                if key == "contract_id":
                    out[key] = order[value]
                elif key == "contract_ids":
                    out[key] = [order[item] for item in value]
                else:
                    out[key] = walk(value)
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(data)


def sheet_dump(content: bytes) -> list[dict]:
    """Разбор перечитанного листа: по непустой ячейке — пять фактов о ней."""
    ws = load_workbook(BytesIO(content)).active
    return [
        {
            "coordinate": cell.coordinate,
            "value": cell.value,
            "number_format": cell.number_format,
            "data_type": cell.data_type,
            "bold": bool(cell.font.bold),
        }
        for row in ws.iter_rows()
        for cell in row
        if cell.value is not None
    ]


def _canonical(payload) -> str:
    """Текст снимка. `default=_decimal_encoder` — тот же, что у эндпоинта: значение
    неизвестного типа обязано ронять прогон, а не молча уезжать в снимок
    репрезентацией по умолчанию."""
    return json.dumps(
        payload, ensure_ascii=False, indent=2, default=_decimal_encoder
    ) + "\n"


def _assert_matches_golden(name: str, payload) -> None:
    path = GOLDEN_DIR / name
    text = _canonical(payload)

    if os.environ.get(GOLDEN_WRITE_ENV) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        pytest.fail(
            f"снимок {name} ЗАПИСАН по {GOLDEN_WRITE_ENV}=1. Снимите переменную и "
            "прогоните снова: зелёным обязан быть только прогон, который сравнивал."
        )

    if not path.exists():
        pytest.fail(
            f"снимок не заведён: {path} отсутствует. Снять его можно ТОЛЬКО до правок "
            f"crud/comparison.py — прогоном с {GOLDEN_WRITE_ENV}=1 (план, задача 1)."
        )

    # Сравниваются разобранные структуры, а не строки: диффом по тексту снимок из
    # тысячи строк нечитаем, а расхождение надо уметь прочитать глазами.
    assert json.loads(text) == json.loads(path.read_text(encoding="utf-8"))


def _keys_containing(node, needle: str) -> list[str]:
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if needle in key:
                found.append(key)
            found.extend(_keys_containing(value, needle))
    elif isinstance(node, list):
        for item in node:
            found.extend(_keys_containing(item, needle))
    return found


# ---------------------------------------------------------------------------
#  Снимки
# ---------------------------------------------------------------------------

def test_comparison_response_matches_golden(db_session, factories):
    """Ответ сравнения совпадает с дофичевым снимком ЦЕЛИКОМ (DoD 1)."""
    ids = fx.baseline_selection(db_session, factories)
    data = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_OWN)

    # Утверждения плана: выборка из трёх колонок, первая — самая поздняя по
    # `signed_date` (порядок `_load_columns`: signed_date DESC, id DESC).
    assert len(data["columns"]) == 3
    assert [column["contract_number"] for column in data["columns"]] == [
        "ГП-Б3", "ГП-Б2", "ГП-Б1",
    ]

    _assert_matches_golden("comparison_baseline.json", normalized(data))


def test_comparison_sheet_matches_golden(db_session, factories):
    """Лист выгрузки совпадает с дофичевым снимком по значениям, форматам и типам.

    Байты не сравниваются (DoD 1): XLSX — ZIP-контейнер.
    """
    ids = fx.baseline_selection(db_session, factories)
    data = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_OWN)

    content = build_comparison_sheet(data, generated_at=GENERATED_AT)
    _assert_matches_golden("comparison_baseline_sheet.json", sheet_dump(content))


def test_baseline_response_carries_no_inflation_keys(db_session, factories):
    """Дофичевый ответ не несёт ни одного инфляционного ключа.

    Утверждение отдельным тестом, а не строкой в снимке: `"inflation": null` в
    номинальном ответе — уже нарушение DoD 1, и различить «ключа нет» от «ключ
    есть и пуст» снимок бы позволил, а вот сказать это вслух — нет.
    """
    ids = fx.baseline_selection(db_session, factories)
    data = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_OWN)

    assert _keys_containing(data, "inflation") == []
