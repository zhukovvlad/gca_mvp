"""Шкала значений параметра: разбор «приставка? число единица?» и одна форма у всех значений
(спека ближайшего значения §1, §6 п. 1).

Чистая функция без базы. Эталоны — руками: число каждого значения и его текст.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from services.value_scale import Scale, scale_numbers


def _values(*texts: str) -> list[tuple[int, str]]:
    """Значения с id 1, 2, 3… по порядку."""
    return list(enumerate(texts, start=1))


@pytest.mark.parametrize(
    ("texts", "numbers"),
    [
        (("B25", "B30"), (25, 30)),
        (("50", "60", "80"), (50, 60, 80)),
        (("200 мм", "250 мм"), (200, 250)),
        (("EI60", "EI90", "EI120"), (60, 90, 120)),
        (("2,5", "3"), (Decimal("2.5"), 3)),
        (("2.5", "3"), (Decimal("2.5"), 3)),
        (("B30", "В40"), (30, 40)),  # латиница и кириллица в «B»
        (("М100", "m200"), (100, 200)),  # кириллическая «М» и латинская «m»
        (("200мм", "250 мм."), (200, 250)),  # пробелы и точки по краям единицы
        (("b25", "B30"), (25, 30)),  # регистр
    ],
)
def test_a_scale_gives_the_number_of_every_value(texts, numbers):
    scale = scale_numbers(_values(*texts))

    assert scale is not None
    assert dict(scale.numbers) == {i: Decimal(n) for i, n in enumerate(numbers, start=1)}
    assert dict(scale.texts) == dict(enumerate(texts, start=1))


def test_the_ids_of_values_are_kept_not_the_positions():
    scale = scale_numbers([(41, "B30"), (7, "B25")])

    assert scale == Scale(numbers={41: Decimal(30), 7: Decimal(25)}, texts={41: "B30", 7: "B25"})


@pytest.mark.parametrize(
    "texts",
    [
        ("600×600", "800×800"),
        ("EI60 или EI90", "EI120"),
        ("до 100", "200"),
        ("от 50", "100"),
        ("50 мм", "5 см"),
        ("B30", "М300"),
        ("0", "50", "100"),
        ("КМ0", "КМ1"),
        ("B25 W6", "B30 W6"),
        ("B30",),
        (),
    ],
)
def test_not_a_scale(texts):
    assert scale_numbers(_values(*texts)) is None


@pytest.mark.parametrize(
    "bad",
    ["100+200", "100x200", "100х200", "100*2", "100-200", "100–200", "100—200", "100/200", "100 или 200", "ДО 100"],
)
def test_one_forbidden_sign_or_word_breaks_the_scale(bad):
    assert scale_numbers(_values("50", bad)) is None


def test_the_words_до_and_от_matter_only_as_the_first_word():
    scale = scale_numbers(_values("100 до", "200 до"))

    assert scale is not None
    assert dict(scale.numbers) == {1: Decimal(100), 2: Decimal(200)}


def test_a_value_that_does_not_parse_breaks_the_scale():
    assert scale_numbers(_values("50", "много")) is None
    assert scale_numbers(_values("50", "")) is None


def test_the_decimal_with_a_comma_is_exact():
    scale = scale_numbers(_values("0,1", "0,25"))

    assert scale is not None
    assert scale.numbers[1] == Decimal("0.1")
    assert scale.numbers[2] == Decimal("0.25")
