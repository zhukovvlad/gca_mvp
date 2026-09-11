"""Правило ставки статьи: классификация единиц и свёртка строк-носителей
(спека 2026-08-24-passport-volumes-design.md §2.2, §2.5, §2.6; план, задача 1)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from crud.units import UNITS_SEED
from services.article_rates import (
    NON_SCALABLE_UNIT_CODES,
    SCALABLE_UNIT_CODES,
    ArticleFold,
    CarrierRow,
    RateNote,
    RateState,
    fold_carrier_rows,
    is_scalable_unit,
    resolve_rate,
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
    assert missing.unit_symbol is None

    conflict = fold_carrier_rows(
        [_row(unit_code="M2", unit_symbol="м²"), _row(unit_code="M3", unit_symbol="м³")],
        effective_rate=None,
    )
    assert (conflict.unit_missing, conflict.unit_conflict) == (False, True)
    assert conflict.unit_code is None
    assert conflict.unit_symbol is None


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


def _fold(**kwargs):
    """Свёртка через публичный `fold_carrier_rows`, а не конструктором
    `ArticleFold`: тест обязан ходить тем же путём, что продакшен."""
    return fold_carrier_rows([_row(**kwargs)], effective_rate=None)


def _empty_fold():
    return fold_carrier_rows([], effective_rate=None)


def test_no_carrier_when_the_estimate_never_names_the_article():
    """DoD 6: корень классификатора без строки с кодом — клетка пуста, пометки нет."""
    rate = resolve_rate(_empty_fold(), has_extras=False, children=[])
    assert rate.state is RateState.NO_CARRIER
    assert (rate.note, rate.unit, rate.volume, rate.unit_rate) == (None, None, None, None)


def test_additional_works_wins_over_no_carrier_when_extras_exist():
    """DoD 7, коллизия «допработы есть, носителя нет». Оговорка «нет допработ»
    внутри `no_carrier` — единственное, что отличает два состояния."""
    rate = resolve_rate(_empty_fold(), has_extras=True, children=[])
    assert rate.state is RateState.ADDITIONAL_WORKS


def test_volume_missing_is_a_different_state_from_additional_works():
    """DoD 7, вторая половина: ставки пустые одинаково, смысл разный."""
    rate = resolve_rate(_fold(volume=None), has_extras=False, children=[])
    assert rate.state is RateState.VOLUME_MISSING
    assert rate.unit == "м²"  # единица ЕСТЬ — макет, строка 240


def test_priority_gives_exactly_one_state_on_a_collision():
    """DoD 15: узел под `unit_not_scalable` И под `volume_missing` получает
    состояние с МЕНЬШИМ номером — шестое, не седьмое."""
    rate = resolve_rate(
        _fold(unit_code="SET", unit_symbol="компл", volume=None),
        has_extras=False, children=[],
    )
    assert rate.state is RateState.UNIT_NOT_SCALABLE


def test_piece_with_a_real_quantity_is_not_declared_non_scalable():
    """DoD 10, вторая половина (замер 7: у лифтов настоящие 10 штук)."""
    rate = resolve_rate(
        _fold(unit_code="PCS", unit_symbol="шт", amount=Decimal("500.00"),
              volume=Decimal("10.00")),
        has_extras=False, children=[],
    )
    assert rate.state is RateState.RATE
    assert rate.unit_rate == Decimal("50")


@pytest.mark.parametrize("children_volume", [Decimal("8.00"), Decimal("2.00")])
def test_mixed_units_do_not_depend_on_the_numbers(children_volume):
    """DoD 17. Два прогона: при 8,00 сумма разноразмерных СОВПАДАЕТ с объёмом
    узла (2 + 8 = 10), при 2,00 — нет. Состояние обязано быть одинаковым.

    Снятие «детектировать смешение сравнением S ≈ P» краснеет на первом прогоне.
    """
    parent = _fold(volume=Decimal("10.00"))                      # м²
    in_m2 = fold_carrier_rows([_row(volume=Decimal("2.00"))], effective_rate=None)
    in_sets = fold_carrier_rows(
        [_row(unit_code="SET", unit_symbol="компл", volume=children_volume)],
        effective_rate=None,
    )
    rate = resolve_rate(parent, has_extras=False, children=[in_m2, in_sets])
    assert rate.state is RateState.VOLUME_INCONSISTENT
    assert rate.note is RateNote.MIXED_UNITS


def test_overshoot_kills_the_node_and_leaves_the_child_alone():
    """DoD 18: перебор гасит ставку УЗЛА; у ребёнка объём свой и остаётся в силе."""
    parent = _fold(volume=Decimal("10.00"))
    child = fold_carrier_rows(
        [_row(amount=Decimal("600.00"), volume=Decimal("12.00"))], effective_rate=None
    )
    parent_rate = resolve_rate(parent, has_extras=False, children=[child])
    child_rate = resolve_rate(child, has_extras=False, children=[])
    assert (parent_rate.state, parent_rate.note) == (
        RateState.VOLUME_INCONSISTENT, RateNote.OVERSHOOT,
    )
    assert parent_rate.unit_rate is None
    assert child_rate.state is RateState.RATE
    assert child_rate.unit_rate == Decimal("50")


def test_undershoot_does_not_kill_the_rate():
    """DoD 19: недобор — НОРМА (не все дети несут код статьи), а не расхождение."""
    parent = _fold(amount=Decimal("1000.00"), volume=Decimal("10.00"))
    child = fold_carrier_rows([_row(volume=Decimal("3.00"))], effective_rate=None)
    rate = resolve_rate(parent, has_extras=False, children=[child])
    assert rate.state is RateState.RATE
    assert rate.unit_rate == Decimal("100")


def test_the_tolerance_boundary_is_inclusive_on_the_undershoot_side():
    """DoD 20. Два ребёнка → n = 2 → ε = 0,02. P = 10,00.
    S = 10,02 — недобор (ставка есть); S = 10,03 — перебор (ставки нет)."""
    parent = _fold(amount=Decimal("1000.00"), volume=Decimal("10.00"))

    def two_children(total: str):
        first = fold_carrier_rows([_row(volume=Decimal("5.00"))], effective_rate=None)
        second = fold_carrier_rows(
            [_row(volume=Decimal(total) - Decimal("5.00"))], effective_rate=None
        )
        return [first, second]

    assert resolve_rate(parent, has_extras=False,
                        children=two_children("10.02")).state is RateState.RATE
    assert resolve_rate(parent, has_extras=False,
                        children=two_children("10.03")).state is RateState.VOLUME_INCONSISTENT


@pytest.mark.parametrize(
    "child_rows",
    [
        pytest.param([dict(unit_code=None, unit_symbol=None)], id="child_unit_missing"),
        pytest.param(
            [dict(unit_code="M2", unit_symbol="м²"), dict(unit_code="M3", unit_symbol="м³")],
            id="child_unit_conflict",
        ),
    ],
)
def test_a_child_with_an_unreadable_unit_is_unverifiable_not_mixed(child_rows):
    """Уточнение У3, вариант «б». У ребёнка единица НЕ ПРОЧИТАНА — её нет
    (`unit_missing`) либо строки не согласны (`unit_conflict`). Объём у него при
    этом ЕСТЬ, поэтому в проверку он попадает.

    Родителя гасим — иначе он получил бы ставку при недоборе, посчитанном по
    неполному множеству детей, то есть молча. Но подпись обязана быть своя:
    «смешение» утверждало бы, что единица отличается, а она неизвестна. За двумя
    подписями стоят разные действия — разобраться с единицами разделов против
    дозаполнить единицу в смете.
    """
    parent = _fold(volume=Decimal("10.00"))
    child = fold_carrier_rows(
        [_row(volume=Decimal("4.00"), **row) for row in child_rows], effective_rate=None
    )
    assert child.volume is not None, "ребёнок обязан дойти до проверки с объёмом"
    rate = resolve_rate(parent, has_extras=False, children=[child])
    assert rate.state is RateState.VOLUME_INCONSISTENT
    assert rate.note is RateNote.UNVERIFIABLE


def test_mixed_units_outrank_unverifiable_when_both_children_are_present():
    """Уточнение У3, порядок проверок: определённая находка важнее отсутствия
    сведений. Один ребёнок в другой ИЗВЕСТНОЙ единице, второй — с непрочитанной.
    Узел гасится в любом случае, различается подпись, и она обязана быть ОДНА.
    """
    parent = _fold(volume=Decimal("10.00"))
    in_sets = fold_carrier_rows(
        [_row(unit_code="SET", unit_symbol="компл", volume=Decimal("2.00"))],
        effective_rate=None,
    )
    unreadable = fold_carrier_rows(
        [_row(unit_code=None, unit_symbol=None, volume=Decimal("2.00"))],
        effective_rate=None,
    )
    rate = resolve_rate(parent, has_extras=False, children=[in_sets, unreadable])
    assert rate.state is RateState.VOLUME_INCONSISTENT
    assert rate.note is RateNote.MIXED_UNITS


def test_amount_missing_outranks_every_unit_and_volume_state():
    """§2.5, порядок: третья проверка стоит выше четвёртой–восьмой."""
    fold = fold_carrier_rows(
        [_row(amount=None, unit_code=None, unit_symbol=None, volume=None)],
        effective_rate=None,
    )
    assert resolve_rate(fold, has_extras=False, children=[]).state is RateState.AMOUNT_MISSING


def test_unit_conflict_is_its_own_state():
    """DoD 9 на уровне автомата: складывать нельзя, и причина названа отдельно."""
    fold = fold_carrier_rows(
        [_row(unit_code="M2", unit_symbol="м²"), _row(unit_code="M3", unit_symbol="м³")],
        effective_rate=None,
    )
    assert resolve_rate(fold, has_extras=False, children=[]).state is RateState.UNIT_CONFLICT


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-1")])
def test_nonpositive_volume_is_its_own_state(bad):
    """DoD 14 на уровне автомата: делить нельзя — и это не «объём не указан»."""
    fold = fold_carrier_rows([_row(volume=bad)], effective_rate=None)
    assert resolve_rate(fold, has_extras=False, children=[]).state is RateState.VOLUME_NONPOSITIVE


# ---------------------------------------------------------------------------
#  RateState.AMOUNT_ZERO — девятая проверка (§1.9, §2.9, §2.10, план задача 6)
# ---------------------------------------------------------------------------

def test_amount_zero_is_returned_on_a_finite_zero_sum():
    """Вход нового состояния (§2.10): строки-носители есть, сумма КОНЕЧНА и
    равна нулю, единица масштабируема, объём конечен и положителен. Дефолты
    `_row` дают все условия, кроме суммы, готовыми — меняется одно поле."""
    rate = resolve_rate(_fold(amount=Decimal("0.00")), has_extras=False, children=[])
    assert rate.state is RateState.AMOUNT_ZERO
    assert (rate.note, rate.unit, rate.volume, rate.unit_rate) == (None, "м²", None, None)


def test_amount_zero_treats_negative_zero_as_zero():
    """Решение осознанное, не везение: `Decimal("-0.00")` — тот же факт «денег
    нет», что и `Decimal("0.00")`, и ловится тем же оператором `==`, которым
    Decimal вообще сравнивает величины (в отличие от `is_signed()`, который
    отрицательный ноль бы отличил).

    Вход строится КОНСТРУКТОРОМ `ArticleFold`, а не через `fold_carrier_rows` —
    и это единственное место файла, где так сделано намеренно. Накопление
    `fold_carrier_rows` начинается с `Decimal(0)` (положительного), а сложение
    разнознаковых операндов, дающее точный ноль, само по себе возвращает
    ПОЛОЖИТЕЛЬНЫЙ ноль — доказательство через `is_signed()`, а не через `==`
    (`==` двух нулей разного знака как раз ничего не доказал бы, оба истинны):
    `(Decimal(0) + Decimal("-0.00")).is_signed()` даёт `False`, тогда как
    `Decimal("-0.00").is_signed()` само по себе — `True`; знак теряется именно
    на сложении. Значит `fold.amount` через настоящую свёртку никогда не бывает
    отрицательным нулём буквально, и вход через `_fold(amount=Decimal("-0.00"))`
    эту проверку молча не нагружал бы вовсе (первая редакция теста так и
    делала — красного не давала ни на одной мутации сравнения по знаку,
    снятием проверено). `resolve_rate` при этом принимает `ArticleFold` как
    контракт САМ ПО СЕБЕ, независимо от того, кто его построил, и обязан быть
    верным на границе своего же входа."""
    fold = ArticleFold(
        rows=1, amount=Decimal("-0.00"), amount_ok=True,
        unit_code="M2", unit_symbol="м²", unit_missing=False, unit_conflict=False,
        volume=Decimal("10.00"), volume_missing=False, volume_nonpositive=False,
    )
    rate = resolve_rate(fold, has_extras=False, children=[])
    assert rate.state is RateState.AMOUNT_ZERO


def test_amount_zero_outranks_volume_inconsistent_at_the_same_zero_sum():
    """§2.10: место проверки — девятое, ДО сходимости детей (десятая). Вход —
    тот же перебор объёма, что у `test_overshoot_kills_the_node_and_leaves_
    the_child_alone` (ребёнок 12,00 против родителя 10,00, разница выше
    допуска), но сумма родителя обнулена: правильный порядок обязан назвать
    первым делом отсутствие денег статьи, а не разногласие детей.

    Перестановочный эксперимент (не проводится здесь — его ставит
    оркестратор): перенос этой проверки ПОСЛЕ `convergence_note` (десятое
    место вместо девятого) роняет это утверждение — ответ станет
    `volume_inconsistent`."""
    parent = _fold(amount=Decimal("0.00"), volume=Decimal("10.00"))
    child = fold_carrier_rows(
        [_row(amount=Decimal("600.00"), volume=Decimal("12.00"))], effective_rate=None
    )
    rate = resolve_rate(parent, has_extras=False, children=[child])
    assert rate.state is RateState.AMOUNT_ZERO


def test_unit_missing_outranks_amount_zero_at_the_same_zero_sum():
    """Один из пяти входов, стерегущих ПОРЯДОК цепочки (§2.10: порядок — само
    правило, а не деталь реализации): при ТОЙ ЖЕ нулевой сумме проверка 4
    (`unit_missing`) стоит раньше девятой и выигрывает — вход несёт ОБА условия
    сразу.

    Перестановочный эксперимент (переносит `AMOUNT_ZERO` на четвёртое место,
    рядом с `amount_missing`, отвергнутая альтернатива §2.10): на ЭТОМ входе он
    меняет ответ — `unit_missing` сегодня четвёртая проверка, `AMOUNT_ZERO` на
    её месте встала бы ПЕРЕД ней, и тест покраснеет состоянием `amount_zero`.
    """
    fold = fold_carrier_rows(
        [_row(amount=Decimal("0.00"), unit_code=None, unit_symbol=None)],
        effective_rate=None,
    )
    assert resolve_rate(fold, has_extras=False, children=[]).state is RateState.UNIT_MISSING


def test_unit_conflict_outranks_amount_zero_at_the_same_zero_sum():
    """Тот же довод, что у соседнего теста, для проверки 5 (`unit_conflict`) —
    обе строки нулевые, чтобы сумма свёртки тоже была нулевой, а не только у
    одной строки."""
    fold = fold_carrier_rows(
        [
            _row(amount=Decimal("0.00"), unit_code="M2", unit_symbol="м²"),
            _row(amount=Decimal("0.00"), unit_code="M3", unit_symbol="м³"),
        ],
        effective_rate=None,
    )
    assert resolve_rate(fold, has_extras=False, children=[]).state is RateState.UNIT_CONFLICT


def test_unit_not_scalable_outranks_amount_zero_at_the_same_zero_sum():
    """Тот же довод для проверки 6 (`unit_not_scalable`)."""
    fold = fold_carrier_rows(
        [_row(amount=Decimal("0.00"), unit_code="SET", unit_symbol="компл")],
        effective_rate=None,
    )
    assert resolve_rate(fold, has_extras=False, children=[]).state is RateState.UNIT_NOT_SCALABLE


def test_volume_missing_outranks_amount_zero_at_the_same_zero_sum():
    """Тот же довод для проверки 7 (`volume_missing`)."""
    fold = fold_carrier_rows([_row(amount=Decimal("0.00"), volume=None)], effective_rate=None)
    assert resolve_rate(fold, has_extras=False, children=[]).state is RateState.VOLUME_MISSING


def test_volume_nonpositive_outranks_amount_zero_at_the_same_zero_sum():
    """Тот же довод для проверки 8 (`volume_nonpositive`)."""
    fold = fold_carrier_rows(
        [_row(amount=Decimal("0.00"), volume=Decimal("0.00"))], effective_rate=None
    )
    assert resolve_rate(fold, has_extras=False, children=[]).state is RateState.VOLUME_NONPOSITIVE


def test_rate_state_and_a_zero_unit_rate_are_mutually_exclusive():
    """Эквивалентность, а не пример (`docs/insights/state-the-rule-as-an-
    equivalence.md`): при прочих проверках пройденных `RATE` возвращается
    ТОГДА И ТОЛЬКО ТОГДА, когда сумма свёртки ОТЛИЧНА от нуля — значит,
    `unit_rate`, равный `0 ₽/ед.`, не возвращается ни на одном входе.

    Три входа одной формы различаются только суммой: отрицательная и
    положительная дают `RATE` с ненулевой (в том числе отрицательной) ставкой
    — проверка 9 смотрит РАВЕНСТВО нулю, а не знак и не неположительность;
    нулевая сумма даёт `AMOUNT_ZERO`. Обратная сторона эквивалентности (`RATE`
    ⟹ ставка не ноль) — не отдельная проверка кода, а следствие той же
    арифметики: `volume` строго положителен к этому месту (проверки 7 и 8 уже
    это гарантируют), и ненулевой числитель на положительном знаменателе не
    может дать ноль — приведено как утверждение, а не оставлено предположением.
    """
    cases = (
        (Decimal("500.00"), RateState.RATE),
        (Decimal("-500.00"), RateState.RATE),
        (Decimal("0.00"), RateState.AMOUNT_ZERO),
    )
    for amount, expected_state in cases:
        rate = resolve_rate(_fold(amount=amount), has_extras=False, children=[])
        assert rate.state is expected_state
        if expected_state is RateState.RATE:
            assert rate.unit_rate != 0
        else:
            assert rate.unit_rate is None


def test_the_state_enum_carries_exactly_the_eleven_contract_values():
    """§2.10: перечень состояний в контракте и в §2.5 — ОДИН список.
    Расхождение здесь означает, что часть состояний нельзя вернуть."""
    assert {state.value for state in RateState} == {
        "no_carrier", "additional_works", "amount_missing", "unit_missing",
        "unit_conflict", "unit_not_scalable", "volume_missing",
        "volume_nonpositive", "amount_zero", "volume_inconsistent", "rate",
    }
    assert {note.value for note in RateNote} == {
        "overshoot", "mixed_units", "unverifiable",
    }
