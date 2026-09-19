"""Сериализация денег в JSON: `_decimal_encoder` (Ф6a фазы 7, спека §2.1, §4).

Своего юнита у общего сериализатора денег не было ни одного — замер плана Ф6a
(задача 1): `decimal_json` стерёгся только косвенно, двумя интеграционными
наборами (`test_contracts_api.py`, `test_references_api.py`). Здесь — юнит, на
конкретных формах `Decimal`.

**Признак нарушения берётся ПО ТИПУ, а не по тексту** (спека §4): латинская `E`
законна в тексте, и наименования статей и смет её несут, поэтому обход отбирает
листья `isinstance(value, Decimal)`. Перечень полей от этого не устаревает при
добавлении нового.

**Утверждение делается о результате `_decimal_encoder(value)`, а не о самом
`Decimal`.** Правка §2.1 меняет только сериализацию: в словаре CRUD значение
остаётся `Decimal('0E+2')` и после неё, поэтому критерий `str(value)` был бы
красен навсегда, а критерий `format(value, 'f')` — зелен даже при снятой защите.
Переключается ровно правкой только третий (замер спеки §4, таблица).
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from starlette.responses import Response, StreamingResponse

from responses import XLSX_MEDIA_TYPE, _decimal_encoder, safe_filename_part, xlsx_response

# Контракт данных, который обещает бэкенд и разбирает фронт: `-?цифры[.цифры]`.
# Тот же литерал, что `DECIMAL_RE` в `frontend/src/lib/format.ts` — именно его
# E-нотация и нарушает, а `roundDecimal`/`formatDecimalMoney` на неразобранном
# входе печатают его дословно (спека §1.2).
_CONTRACT_RE = re.compile(r"-?\d+(\.\d+)?")

# Формы построены ДЕЛЕНИЕМ, а не литералом: предпосылка «частное `Decimal` несёт
# неотрицательную экспоненту, когда у делимого знаков меньше, чем у делителя»
# способна тихо измениться, и тогда фикстура стала бы вакуозной. Отдельный тест
# ниже проверяет её внутри набора (false-test-premises).
_ZERO_SHARE = Decimal("0") / Decimal("3500.00") * 100
_ZERO_PER_SQM = Decimal("0") / Decimal("47000.00")
_LONG_PER_SQM = Decimal("600000.00") / Decimal("47000.00")

# Экспоненциальная форма приезжает и ИЗ БАЗЫ, на малых величинах: замер плана
# §1.3 — psycopg отдаёт литерал `1e-20` как `Decimal('1E-20')`. Здесь литерал,
# потому что именно так значение и появляется — не вычислением.
_SMALL_FROM_DB = Decimal("1E-20")


def _decimal_leaves(node: Any, path: str = "$") -> Iterator[tuple[str, Decimal]]:
    """Все `Decimal`-листья структуры вместе с путём до каждого.

    Обход, а не перечисление полей: перечень устаревал бы при добавлении нового
    денежного поля, и проверка молча перестала бы его касаться.
    """
    if isinstance(node, Decimal):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _decimal_leaves(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _decimal_leaves(value, f"{path}[{index}]")


def _passport_shaped() -> dict:
    """Словарь той же ФОРМЫ, что отдаёт `get_project_passport`: вложенные
    словари, список словарей, список внутри элемента списка, `None`-листья.

    Форма существенна — критерий обходит структуру, а не читает известные поля.
    Числа синтетические: реальные величины стенда в репозиторий не переносятся
    (политика `samples/`).
    """
    return {
        "contract": {
            "id": 12,
            "contract_number": "ГП-0212",
            "advance_pct": Decimal("30"),
            "retention_pct": None,
        },
        "object": {"id": 3, "area_total_sp": Decimal("47000.00")},
        "estimate": {"id": 600, "parser_version": "3.1.0", "vat_rate": Decimal("20")},
        "totals": {
            "amount": Decimal("4700000.00"),
            "per_sqm": _LONG_PER_SQM,
            "delta_to_file_total": Decimal("0.00"),
            "positions_rows": 461,
        },
        "categories": [
            {
                "code": "01",
                "title": "Земляные работы",
                "total": Decimal("500000.00"),
                "share_pct": Decimal("500000.00") / Decimal("4700000.00") * 100,
                "per_sqm": _SMALL_FROM_DB,
                "extras": [{"id": 401, "ordinal": 1, "amount": Decimal("25000.00")}],
                "own_sections": [{"id": 1, "number": "4.1", "title": "Раздел «Сети»"}],
            },
            {
                "code": "01.02",
                # Наименование с латинской `E` — на нём и видно, что признак
                # берётся по типу, а не по тексту: проверка «нет строки с E»
                # красила бы эту честную строку (спека §4).
                "title": "Водопонижение: насос GRUNDFOS SE1.80",
                "total": Decimal("0"),
                "share_pct": _ZERO_SHARE,
                "per_sqm": _ZERO_PER_SQM,
                "extras": [],
                "own_sections": [],
            },
        ],
        "unallocated": {
            "amount": None,
            "share_pct": None,
            "per_sqm": None,
            "chapters": 2,
            "extras": [{"id": 501, "ordinal": 1, "amount": Decimal("10000.00")}],
        },
    }


def test_no_decimal_leaf_is_serialized_in_exponential_notation():
    """Ни одно `Decimal`-поле не уезжает в экспоненциальной записи (DoD §4).

    Утверждение — о `_decimal_encoder(value)`, и оно сильнее, чем «нет `E`»:
    результат обязан быть строкой контракта `-?цифры[.цифры]`, которую фронт
    разбирает. Форма `0E+2` контракт нарушает, и `formatDecimalMoney` печатает
    её дословно — «0E+2 ₽» на экране (спека §1.2).
    """
    leaves = list(_decimal_leaves(_passport_shaped()))
    assert leaves, "обход не нашёл ни одного Decimal — фикстура вакуозна"

    for path, value in leaves:
        encoded = _decimal_encoder(value)
        assert isinstance(encoded, str), f"{path}: энкодер отдал {type(encoded).__name__}"
        assert _CONTRACT_RE.fullmatch(encoded), f"{path}: {encoded!r} — не строка контракта"


def test_the_synthetic_passport_carries_the_forms_the_defect_needs():
    """Анти-вакуозность: в фикстуре ЕСТЬ значения, чей `str()` даёт E-нотацию.

    **Зелен и до правки, и после — это проверка предпосылки, а не защиты.**
    Стоит потому, что без него замена `Decimal('0')` на `Decimal('0.00')` в
    фикстуре сделала бы тест выше зелёным навсегда, ничего не стерегущим
    (false-test-premises: предпосылка тихо меняется, видно только замером).
    """
    exponential = [
        (path, value)
        for path, value in _decimal_leaves(_passport_shaped())
        if "E" in str(value)
    ]
    assert len(exponential) >= 2, f"E-форм в фикстуре: {exponential}"


@pytest.mark.parametrize(
    ("case", "value", "expected"),
    [
        # Ноль от деления — повод фичи (спека §1.2). Числитель точный ноль,
        # знаменатель со шкалой: экспонента частного положительна.
        ("ноль от деления доли", _ZERO_SHARE, "0"),
        ("ноль от деления ₽/м²", _ZERO_PER_SQM, "0"),
        # Малая величина из базы (план §1.3) — разворачивается, а не сокращается.
        ("малая величина из базы", _SMALL_FROM_DB, "0.00000000000000000001"),
        # ГРАНИЦА правки, названная в спеке §2.1: обычные суммы со шкалой не
        # меняются вовсе. Эти два случая зелены и ДО правки — они стерегут не
        # правку, а её границу.
        ("сумма со шкалой не переписывается", Decimal("10.00"), "10.00"),
        ("сумма договора ГП не теряет разрядов", Decimal("1234567890.12"), "1234567890.12"),
    ],
)
def test_each_form_is_serialized_exactly(case, value, expected):
    """Точное изображение каждой формы, а не только отсутствие `E`: критерий
    «нет E-нотации» зелен и у энкодера, который отдаёт пустую строку."""
    assert _decimal_encoder(value) == expected, case


def test_the_encoder_still_refuses_a_type_it_does_not_know():
    """Строгость энкодера — защита, а не недоделка (докстрока модуля): даты
    обязаны приводиться к ISO в доменном слое, и забывчивость видна сразу.

    **Зелен и до правки** — регрессионный щит: правка §2.1 меняет ветку
    `Decimal`, и «заодно» ослабить `raise` на прочих типах она не должна.
    """
    with pytest.raises(TypeError, match="date"):
        _decimal_encoder(dt.date(2026, 8, 10))


# --- Задача 1 плана «Выгрузка Изменения КП»: перенос хелпера ответа xlsx ---
#
# `xlsx_response` и `safe_filename_part` переезжают из `routers/reports.py` в
# `backend/responses.py` нетронутыми по поведению (план, Task 1) — второй
# роутер (`tenders.py`, задача 5) не должен получить второе написание одного и
# того же правила.


def test_xlsx_response_is_a_plain_bytes_response_not_a_streaming_one():
    """Утверждение 1 задачи 1: `xlsx_response` отдаёт `bytes`, а не
    `StreamingResponse` с файловым объектом — Starlette итерирует такой объект
    построчно и не закрывает хендл (грабля фазы 4, спека §2.11).
    """
    content = b"PK\x03\x04-fake-xlsx-bytes"
    response = xlsx_response(content, "test.xlsx")

    assert isinstance(response, Response)
    assert not isinstance(response, StreamingResponse)
    assert response.body == content
    assert response.media_type == XLSX_MEDIA_TYPE


def test_xlsx_response_content_disposition_is_percent_encoded_utf8_only():
    """Утверждение 2: заголовок ответа — `Content-Disposition: attachment;
    filename*=UTF-8''<percent>`, где `<percent>` — `quote(filename)`; форма
    ASCII `filename=` не заводится вовсе — русские буквы в ней искажаются
    (спека §2.11).
    """
    filename = "Свод расценок ГП-0212.xlsx"
    response = xlsx_response(b"stub", filename)

    assert response.headers["content-disposition"] == (
        f"attachment; filename*=UTF-8''{quote(filename)}"
    )


def test_safe_filename_part_replaces_every_forbidden_character_with_a_dash():
    """Утверждение 3 (часть первая): `safe_filename_part` заменяет каждый
    символ из `/\\:*?"<>|` на `-` (спека §2.11).
    """
    value = 'a/b\\c:d*e?f"g<h>i|j'
    assert safe_filename_part(value, fallback="x") == "a-b-c-d-e-f-g-h-i-j"


def test_safe_filename_part_falls_back_to_the_required_keyword_when_empty():
    """Утверждение 3 (часть вторая): строку, ставшую пустой после чистки и
    `strip`, `safe_filename_part` заменяет на значение ОБЯЗАТЕЛЬНОГО именованного
    параметра `fallback`. Зашитого слова в общем хелпере нет: книга тендера,
    подписавшаяся словом «договор», была бы скрытой договорённостью, а не
    контрактом (план, решение 2) — поэтому `fallback` обязан приниматься только
    по имени, а не позиционно и не молчаливым умолчанием.

    Пустая строка после `strip` — только у пустого/пробельного значения:
    символ из запрещённого набора заменяется на `-`, а `-` не пробел и `strip`
    его не съедает (следующий тест разбирает именно этот вход отдельно, чтобы
    не спутать два разных случая).
    """
    assert safe_filename_part("", fallback="иное значение") == "иное значение"
    assert safe_filename_part("   ", fallback="иное значение") == "иное значение"

    with pytest.raises(TypeError):
        safe_filename_part("x", "позиционный fallback запрещён")
    with pytest.raises(TypeError):
        safe_filename_part("x")


def test_existing_reports_pass_the_literal_fallback_that_reproduces_pre_move_behavior():
    """Утверждение 4: три существующих отчёта передают `fallback="договор"` и
    отдают то же имя файла, что до переноса, на входе из одних запрещённых
    символов.

    На входе из одних символов `/\\:*?"<>|` фолбэк НЕ включается ни до переноса,
    ни после: замена даёт строку из одних `-`, а `strip` дефис не убирает —
    поэтому это утверждение о РЕГРЕССИИ (то же самое преобразование, что было),
    а не о срабатывании фолбэка на этом конкретном входе. Именно фолбэк —
    настоящее слово «договор», а не произвольная строка-заглушка вроде `"x"» —
    предъявлен явно: и как значение параметра в вызове (та же строка, что была
    зашита в прежнем приватном хелпере), и грепом по местам исходника, где
    `routers/reports.py` реально строит динамическую часть имени файла. Мест
    ДВА (`contract_summary` и `_comparison_filename` внутри `comparison_report`):
    у `bank_comparison` имя файла фиксировано и `safe_filename_part` не зовёт
    вовсе, поэтому «три отчёта» — это три ЭНДПОИНТА, а не три вызова хелпера.
    """
    only_forbidden = '/\\:*?"<>|'
    assert safe_filename_part(only_forbidden, fallback="договор") == "---------"

    source = Path(__file__).resolve().parents[2] / "routers" / "reports.py"
    text = source.read_text(encoding="utf-8")
    assert text.count('fallback="договор"') == 2, (
        "оба места, где отчёты строят часть имени файла, обязаны называть "
        "fallback явно словом «договор» — тем же, что было зашито в общем "
        "хелпере до переноса"
    )


def test_reports_router_keeps_no_local_rewrite_of_the_moved_helpers():
    """Утверждение 6: в `routers/reports.py` не остаётся ни одного собственного
    написания ни хелпера ответа, ни чистки имени — второе написание
    разъехалось бы с первым (спека §2.11). Проверяется импортированным модулем,
    а не текстом: совпадение по identity доказывает, что вызывается тот же
    объект, а не одноимённая копия.
    """
    import routers.reports as reports_module

    assert not hasattr(reports_module, "_xlsx")
    assert not hasattr(reports_module, "_safe_filename_part")
    assert reports_module.xlsx_response is xlsx_response
    assert reports_module.safe_filename_part is safe_filename_part
