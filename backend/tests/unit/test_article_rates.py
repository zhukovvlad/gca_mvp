"""Правило ставки статьи: классификация единиц и свёртка строк-носителей
(спека 2026-08-24-passport-volumes-design.md §2.2, §2.5, §2.6; план, задача 1)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from crud.units import UNITS_SEED
from services.article_rates import (
    NON_SCALABLE_UNIT_CODES,
    SCALABLE_UNIT_CODES,
    CarrierRow,
    fold_carrier_rows,
    is_scalable_unit,
)


def _row(**kwargs) -> CarrierRow:
    """Строка-носитель с годными значениями по умолчанию — тест меняет одно поле."""
    base = dict(
        amount=Decimal("1000.00"),
        vat_rate_base=Decimal("20"),
        unit_code="M2",
        unit_symbol="м²",
        volume=Decimal("10.00"),
    )
    base.update(kwargs)
    return CarrierRow(**base)


def test_three_rows_of_one_article_are_added_up():
    """§2.2: строк с одним кодом может быть несколько (корпуса) — складываются."""
    fold = fold_carrier_rows(
        [
            _row(amount=Decimal("100000.00"), volume=Decimal("1000.00")),
            _row(amount=Decimal("200000.00"), volume=Decimal("2000.00")),
            _row(amount=Decimal("300000.00"), volume=Decimal("3000.00")),
        ],
        effective_rate=Decimal("20"),
    )
    assert fold.rows == 3
    assert fold.amount == Decimal("600000.00")
    assert fold.volume == Decimal("6000.00")
    assert fold.amount_ok is True


def test_one_row_without_an_amount_kills_the_whole_article():
    """DoD 12. Обычный `SUM` пропустил бы `None` и выдал ставку по части корпусов."""
    fold = fold_carrier_rows(
        [_row(amount=Decimal("100000.00")), _row(amount=None)],
        effective_rate=Decimal("20"),
    )
    assert fold.amount_ok is False
    assert fold.amount is None


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_amount_kills_the_whole_article(bad):
    """§2.5 состояние 3: «сумма не прочитана» покрывает и не-конечное число."""
    fold = fold_carrier_rows(
        [_row(amount=Decimal("100000.00")), _row(amount=bad)],
        effective_rate=Decimal("20"),
    )
    assert fold.amount_ok is False


def test_one_row_without_a_volume_kills_the_whole_volume():
    """DoD 13. Та же причина, что у суммы: частичный объём даёт ставку по части корпусов."""
    fold = fold_carrier_rows(
        [_row(volume=Decimal("1000.00")), _row(volume=None)],
        effective_rate=Decimal("20"),
    )
    assert fold.volume_missing is True
    assert fold.volume is None


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-1")])
def test_nonpositive_volume_of_a_second_row_kills_the_volume(bad):
    """DoD 14: обе величины отдельными случаями, и вторая строка при этом годная."""
    fold = fold_carrier_rows(
        [_row(volume=Decimal("1000.00")), _row(volume=bad)],
        effective_rate=Decimal("20"),
    )
    assert fold.volume_nonpositive is True
    assert fold.volume is None


def test_missing_unit_is_not_the_same_as_conflicting_units():
    """DoD 8, DoD 9: за ними разные действия, поэтому это два разных признака."""
    missing = fold_carrier_rows([_row(unit_code=None, unit_symbol=None)], effective_rate=None)
    assert (missing.unit_missing, missing.unit_conflict) == (True, False)
    assert missing.unit_code is None

    conflict = fold_carrier_rows(
        [_row(unit_code="M2", unit_symbol="м²"), _row(unit_code="M3", unit_symbol="м³")],
        effective_rate=None,
    )
    assert (conflict.unit_missing, conflict.unit_conflict) == (False, True)
    assert conflict.unit_code is None


def test_amount_is_restated_row_by_row_before_any_division():
    """§2.2: приведение к ставке показа — ДО деления, по базе КАЖДОЙ строки.

    Две строки с разными базами (20 % и 10 %) и одна цель 22 %. Приведение уже
    накопленной суммы дало бы другое число — сумма смешивает базы.
    """
    fold = fold_carrier_rows(
        [
            _row(amount=Decimal("120.00"), vat_rate_base=Decimal("20")),
            _row(amount=Decimal("110.00"), vat_rate_base=Decimal("10")),
        ],
        effective_rate=Decimal("22"),
    )
    # 120/1.20*1.22 + 110/1.10*1.22 = 122 + 122 = 244
    assert fold.amount == Decimal("244")


def test_the_fold_does_not_depend_on_the_order_of_the_rows():
    """Global Constraint 16: свёртка идёт в ЯВНОМ контексте `prec = 100`, и
    независимость от порядка строк — СЛЕДСТВИЕ этого контекста, а не свойство
    `Decimal` само по себе.

    Числа нарочно предельные: `1E+27` и два раза `0.6`. При `prec = 28` порядок
    виден в результате — прямой ход округляет каждую добавку по отдельности и
    даёт `...002`, обратный сначала копит `1.2` и даёт `...001`. При `prec = 100`
    оба порядка дают `...001.2`. Порядок строк-носителей задаёт `ORDER BY`
    запроса задачи 3, то есть величина, зависящая от порядка, зависела бы от
    плана запроса.

    Ожидание записано ЛИТЕРАЛОМ намеренно. `Decimal("1E+27") + Decimal("1.2")`
    вычислялось бы здесь, в ambient-контексте теста (`prec = 28`), потеряло бы
    `0.2` и дало `...001` — то есть тест падал бы на ВЕРНОЙ реализации. Ровно та
    ловушка, про которую тест и написан: выражение в ожидании считается не в том
    контексте, что проверяемый код.
    """
    volumes = [Decimal("1E+27"), Decimal("0.6"), Decimal("0.6")]
    forward = fold_carrier_rows([_row(volume=v) for v in volumes], effective_rate=None)
    backward = fold_carrier_rows([_row(volume=v) for v in reversed(volumes)], effective_rate=None)
    assert forward.volume == backward.volume
    assert forward.volume == Decimal("1000000000000000000000000001.2")


def test_set_and_month_are_the_only_non_scalable_units():
    """§2.6: «Штука» масштабируема — замер 7 (у лифтов настоящее количество)."""
    assert is_scalable_unit("PCS") is True
    assert is_scalable_unit("SET") is False
    assert is_scalable_unit("MON") is False
    assert is_scalable_unit(None) is False


def test_the_two_unit_groups_are_disjoint_and_cover_the_seed():
    """DoD 11, часть без БД: неклассифицированных нет уже в исходнике справочника."""
    seeded = {unit["code"] for unit in UNITS_SEED}
    assert set() == SCALABLE_UNIT_CODES & NON_SCALABLE_UNIT_CODES
    assert seeded == SCALABLE_UNIT_CODES | NON_SCALABLE_UNIT_CODES
