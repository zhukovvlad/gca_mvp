"""Синхронность множества организационных форм между проверкой ПДн и подсветкой.

`backend/services/semantic_privacy.py::_ORG_FORMS_CASEFOLD` — источник истины:
`normalize_org_name` снимает эти формы из имени при сборе словаря, а поиск
пропускает их между словами записи. `frontend/src/pages/families/matchRanges.ts`
держит копию `ORG_FORMS` для подсветки задержанной строки тем же правилом.
Форма, которой нет на фронте, даёт задержанную строку без подсвеченного
совпадения (сервер имя нашёл, экран — нет); лишняя форма на фронте подсвечивала
бы то, чего сервер не держит.

Тест не импортирует TS — читает `matchRanges.ts` текстом и извлекает элементы
массива регэкспом (тот же приём, что `test_event_labels_sync.py`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.semantic_privacy import _ORG_FORMS_CASEFOLD

REPO_ROOT = Path(__file__).resolve().parents[3]
MATCH_RANGES_TS_PATH = REPO_ROOT / "frontend" / "src" / "pages" / "families" / "matchRanges.ts"

_ARRAY_RE = re.compile(r"const\s+ORG_FORMS\s*=\s*\[(?P<body>[^\]]*)\]")
_ITEM_RE = re.compile(r"""(["'`])(?P<form>[^"'`]*)\1""")


def _read_items(source: str) -> list[str]:
    array = _ARRAY_RE.search(source)
    if array is None:
        raise AssertionError(f"`const ORG_FORMS = [...]` не найден в {MATCH_RANGES_TS_PATH}")
    body = array.group("body")
    items = [m.group("form") for m in _ITEM_RE.finditer(body)]
    # Всё, что осталось после вычёркивания строковых литералов, — запятые и
    # пробелы. Иначе элемент записан так, что регэксп его не видит (например,
    # константой), и сверка молча потеряла бы его.
    leftover = _ITEM_RE.sub("", body)
    if leftover.strip(" \t\r\n,"):
        raise AssertionError(f"в ORG_FORMS есть элемент не строковым литералом: {leftover!r}")
    if not items:
        raise AssertionError("ORG_FORMS найден, но ни одного элемента не извлечено")
    return items


def test_frontend_org_forms_equal_backend_forms():
    items = _read_items(MATCH_RANGES_TS_PATH.read_text(encoding="utf-8"))

    assert len(items) == len(set(items)), f"повтор формы в ORG_FORMS: {items}"
    # Фронт сравнивает в нижнем регистре (`toLowerCase`), сервер — после
    # `casefold`: для сверки обе стороны приводятся к одному виду.
    assert {item.casefold() for item in items} == set(_ORG_FORMS_CASEFOLD)
    assert all(item == item.lower() for item in items), (
        f"ORG_FORMS обязан быть в нижнем регистре — подсветка сравнивает с toLowerCase: {items}"
    )


@pytest.mark.parametrize(
    "source",
    [
        'const ORG_FORMS = ["ооо", KNOWN, "ао"];',
        "const OTHER = ['ооо'];",
        "const ORG_FORMS = [];",
    ],
)
def test_reader_fails_loudly_instead_of_losing_items(source):
    """Независимые входы на каждый отказ чтения: элемент не литералом,
    отсутствующий массив и пустой массив обязаны падать, а не давать
    сверке неполный список."""
    with pytest.raises(AssertionError):
        _read_items(source)


def test_reader_reads_every_ts_quoting_style():
    assert _read_items("const ORG_FORMS = [\"ооо\", 'ао', `llp`,\n  \"гк\"];") == [
        "ооо",
        "ао",
        "llp",
        "гк",
    ]
