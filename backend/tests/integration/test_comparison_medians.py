"""Медиана в ставке показа — `totals_medians[].shown_per_sqm` (план, задача 5).

`_median_dict` (используется и для `rows[].medians`, и для `totals_medians`)
не трогается — она отдаёт `value`/`comparable_count`/`contract_ids` и НЕ несёт
режима показа. Правило спеки §2.10 требует поля `shown_per_sqm` только на
`totals_medians`, и только когда режим НДС допускает единую ось показа:

* `net`/`single` — ключ ЕСТЬ ВСЕГДА (значение может быть `None`, если
  сопоставимых меньше трёх — то же правило, что у `value`);
* `own` — ключа НЕТ вовсе (у каждого договора своя ставка, единой оси нет).

`rows[].medians` этот ключ не несёт НИКОГДА, ни в одном режиме, ни в одной
корзине — им продолжает пользоваться голый `_median_dict`.

**Отбор сравнимых ячеек — общий предикат `is_price`** (план фичи предиката
цены `2026-09-09-price-predicate-design.md`, задача 8). Три теста в конце
файла добавлены этой задачей и НЕ переписывают ничего из уже стоявших выше:
доказательством того, что поведение НЕ изменилось на выборке с пригодными
величинами, служит именно НЕТРОНУТЫЙ прогон всех тестов этого файла и файла
`test_comparison_buckets.py` (`just test-int-local-k comparison` — весь
экран, а не только медианы), а не какой-то один новый тест, сравнивающий
вывод сам с собой (`docs/insights/parity-with-the-existing-surface.md`).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from crud import comparison as cmp
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration

_BUCKETS = (cmp.BUCKET_BASE, cmp.BUCKET_AMENDMENTS, cmp.BUCKET_TOTAL)


def _totals_median(agg: dict, *, bucket: str) -> dict:
    return agg["totals_medians"][bucket]


def _all_row_medians(agg: dict) -> list[dict]:
    """Медианы ВСЕХ строк, ВСЕХ корзин — плоский список словарей."""
    return [
        row["medians"][bucket]
        for row in agg["rows"]
        for bucket in _BUCKETS
    ]


# ---------------------------------------------------------------------------
#  Фикстура с ненулевыми, различными медианами в ВСЕХ ТРЁХ корзинах.
# ---------------------------------------------------------------------------
#
# Три договора, у каждого ДГП и ОДНО ДС, ставка НДС 0 % (нетто = валовое —
# арифметика ставки показа тестом не проверяется здесь, только присутствие и
# происхождение значения). Площадь каждого объекта — 100 м² (50 + 50), суммы
# подобраны так, чтобы медиана в КАЖДОЙ из трёх корзин была отдельным круглым
# числом:
#
#   ДГП:  100 / 200 / 300 -> net_per_sqm 1 / 2 / 3   -> медиана 2
#   ДС:   400 / 500 / 600 -> net_per_sqm 4 / 5 / 6   -> медиана 5
#   Итого: 500 / 700 / 900 -> net_per_sqm 5 / 7 / 9  -> медиана 7
def _three_bucket_contracts(db, factories) -> list[int]:
    x = fx.contract_with_amendment(
        db, factories, base_rate=Decimal("0"), amd_rate=Decimal("0"),
        base="100.00", amd="400.00", area_aboveground="50", area_underground="50",
    )
    y = fx.contract_with_amendment(
        db, factories, base_rate=Decimal("0"), amd_rate=Decimal("0"),
        base="200.00", amd="500.00", area_aboveground="50", area_underground="50",
    )
    z = fx.contract_with_amendment(
        db, factories, base_rate=Decimal("0"), amd_rate=Decimal("0"),
        base="300.00", amd="600.00", area_aboveground="50", area_underground="50",
    )
    db.flush()
    return [x.id, y.id, z.id]


# ---------------------------------------------------------------------------
#  1. Режим "net": shown_per_sqm == value, в ВСЕХ ТРЁХ корзинах.
# ---------------------------------------------------------------------------

def test_net_mode_shown_per_sqm_equals_value_in_all_three_buckets(db_session, factories):
    ids = _three_bucket_contracts(db_session, factories)

    agg = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_NET)

    expected = {
        cmp.BUCKET_BASE: Decimal("2"),
        cmp.BUCKET_AMENDMENTS: Decimal("5"),
        cmp.BUCKET_TOTAL: Decimal("7"),
    }
    for bucket in _BUCKETS:
        median = _totals_median(agg, bucket=bucket)
        assert median["value"] == expected[bucket], (
            f"предпосылка: медиана корзины {bucket!r} обязана быть известным круглым числом"
        )
        assert "shown_per_sqm" in median
        assert median["shown_per_sqm"] == median["value"], (
            f"режим net: shown_per_sqm обязан ДУБЛИРОВАТЬ value в корзине {bucket!r}"
        )
        assert isinstance(median["shown_per_sqm"], Decimal), (
            "сравнение обязано быть Decimal с Decimal, не строк и не float"
        )


# ---------------------------------------------------------------------------
#  2. Режим "single": shown_per_sqm == net_to_gross(value, rate), ЧИСЛОМ.
# ---------------------------------------------------------------------------

def test_single_mode_shown_per_sqm_is_net_to_gross_of_value(db_session, factories):
    """Медиана нетто, выведенная из фикстуры: 200 (см. ниже). Ставка показа —
    20 %, произведение — 240 (200 * 1.2). Оба числа — следствие фикстуры, не
    переписаны из брифа."""
    a = fx.contract_with_area(
        db_session, factories, {"1": ["100000.00"]}, vat_rate=Decimal("0"),
        area_aboveground="500", area_underground="500",
    )
    b = fx.contract_with_area(
        db_session, factories, {"1": ["200000.00"]}, vat_rate=Decimal("0"),
        area_aboveground="500", area_underground="500",
    )
    c = fx.contract_with_area(
        db_session, factories, {"1": ["300000.00"]}, vat_rate=Decimal("0"),
        area_aboveground="500", area_underground="500",
    )
    ids = [a, b, c]

    agg = cmp.build_comparison(
        db_session, ids, vat_mode=cmp.VAT_MODE_SINGLE, single_rate=Decimal("20")
    )
    median = _totals_median(agg, bucket=cmp.BUCKET_TOTAL)

    assert median["value"] == Decimal("200"), (
        "предпосылка: медиана нетто выборки равна известному круглому числу 200 "
        "(net_per_sqm 100/200/300 при площади 1000 м², средний по трём — 200)"
    )
    assert median["shown_per_sqm"] == cmp.net_to_gross(median["value"], Decimal("20")), (
        "shown_per_sqm обязан быть выведен через net_to_gross ИЗ value"
    )
    assert median["shown_per_sqm"] == Decimal("240"), (
        "конкретное число: 200 нетто при ставке 20% -> 240 валовых (ловит случай, "
        "когда обе стороны сравнения ошиблись одинаково)"
    )


# ---------------------------------------------------------------------------
#  3. Режим "own": ключа НЕТ вовсе, в ВСЕХ ТРЁХ корзинах.
# ---------------------------------------------------------------------------

def test_own_mode_has_no_shown_per_sqm_key_in_any_bucket(db_session, factories):
    ids = _three_bucket_contracts(db_session, factories)

    agg = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_OWN)

    for bucket in _BUCKETS:
        median = _totals_median(agg, bucket=bucket)
        assert median["value"] is not None, (
            f"предпосылка: медиана корзины {bucket!r} вычислима — иначе отсутствие "
            "ключа не отличить от отсутствия медианы"
        )
        assert "shown_per_sqm" not in median, (
            f"режим own: у каждого договора своя ставка, единой оси показа НЕТ — "
            f"ключ обязан отсутствовать в корзине {bucket!r}"
        )


# ---------------------------------------------------------------------------
#  4. rows[].medians НИКОГДА не несут shown_per_sqm — ни в одной строке,
#     ни в одной корзине, независимо от режима показа.
# ---------------------------------------------------------------------------

def test_row_medians_never_carry_shown_per_sqm(db_session, factories):
    ids = [
        fx.contract_with_area(
            db_session, factories,
            {"1": ["1200.00"], "2": ["600.00"]}, vat_rate=Decimal("20"),
        )
        for _ in range(3)
    ]
    db_session.flush()

    for mode, rate in (
        (cmp.VAT_MODE_NET, None),
        (cmp.VAT_MODE_SINGLE, Decimal("18")),
        (cmp.VAT_MODE_OWN, None),
    ):
        agg = cmp.build_comparison(db_session, ids, vat_mode=mode, single_rate=rate)
        assert agg["rows"], "предпосылка: строки есть — иначе проверка пуста"
        for median in _all_row_medians(agg):
            assert "shown_per_sqm" not in median, (
                f"режим {mode!r}: rows[].medians не имеет права нести shown_per_sqm — "
                "это поле только у totals_medians"
            )


# ---------------------------------------------------------------------------
#  5. Меньше трёх сопоставимых: ключ ПРИСУТСТВУЕТ и равен None (net и single).
# ---------------------------------------------------------------------------

def test_fewer_than_three_comparable_keeps_key_present_but_none_net(db_session, factories):
    a = fx.contract_with_area(db_session, factories, {"1": ["1200.00"]})
    b = fx.contract_with_area(db_session, factories, {"1": ["2400.00"]})

    agg = cmp.build_comparison(db_session, [a, b], vat_mode=cmp.VAT_MODE_NET)
    median = _totals_median(agg, bucket=cmp.BUCKET_TOTAL)

    assert median["comparable_count"] == 2, "предпосылка: меньше трёх сопоставимых"
    assert median["value"] is None
    assert "shown_per_sqm" in median, "режим net: ключ обязан присутствовать всегда"
    assert median["shown_per_sqm"] is None, "медианы нет -> и shown_per_sqm None"


def test_fewer_than_three_comparable_keeps_key_present_but_none_single(db_session, factories):
    a = fx.contract_with_area(db_session, factories, {"1": ["1200.00"]})
    b = fx.contract_with_area(db_session, factories, {"1": ["2400.00"]})

    agg = cmp.build_comparison(
        db_session, [a, b], vat_mode=cmp.VAT_MODE_SINGLE, single_rate=Decimal("20")
    )
    median = _totals_median(agg, bucket=cmp.BUCKET_TOTAL)

    assert median["comparable_count"] == 2, "предпосылка: меньше трёх сопоставимых"
    assert median["value"] is None
    assert "shown_per_sqm" in median, "режим single: ключ обязан присутствовать всегда"
    assert median["shown_per_sqm"] is None, "медианы нет -> и shown_per_sqm None"


# ---------------------------------------------------------------------------
#  6. "single" БЕЗ явной ставки — обязан взять EFFECTIVE_single_rate
#     (предвыбор), а не остаться None (главная ловушка брифа §5.2).
# ---------------------------------------------------------------------------

def test_single_mode_without_explicit_rate_uses_effective_rate_for_median(db_session, factories):
    a = fx.contract_with_area(db_session, factories, {"1": ["100.00"]}, vat_rate=Decimal("16"))
    b = fx.contract_with_area(db_session, factories, {"1": ["100.00"]}, vat_rate=Decimal("20"))
    c = fx.contract_with_area(db_session, factories, {"1": ["100.00"]}, vat_rate=Decimal("22"))
    ids = [a, b, c]

    agg = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_SINGLE, single_rate=None)
    median = _totals_median(agg, bucket=cmp.BUCKET_TOTAL)

    assert agg["single_rate"] == Decimal("22"), (
        "предпосылка: три ставки, каждая по разу -> ничья -> предвыбор — бо́льшая "
        "(то же правило, что test_rate_options_and_preselection)"
    )
    assert median["value"] is not None, "предпосылка: медиана вычислима — три сопоставимых"
    assert median["shown_per_sqm"] is not None, (
        "БЕЗ подстановки предвыбора линия медианы исчезла бы там, где столбцы уже "
        "с числами — объяснить это было бы нечем (§5.2 брифа)"
    )
    assert median["shown_per_sqm"] == cmp.net_to_gross(median["value"], agg["single_rate"]), (
        "shown_per_sqm обязан быть согласован с ОТДАННЫМ single_rate (предвыбором), "
        "а не с исходным (None) параметром запроса"
    )


# ---------------------------------------------------------------------------
#  8-10. Задача 8 плана: отбор сравнимых ячеек — общий предикат `is_price`,
#       не собственная копия правила `!= 0` (см. `crud.comparison._compute_median`).
# ---------------------------------------------------------------------------

def test_all_priced_cells_keep_todays_comparable_count_and_ids(db_session, factories):
    """Граница сохранения поведения — названа явно (план задачи 8, утверждение 1).

    ДОКАЗЫВАЕТ, что поведение не изменилось на выборке без непригодных величин,
    НЕ этот тест: доказательством служит нетронутый прогон семи тестов ВЫШЕ в
    этом же файле (эта задача не правила в них ни строки) и всего экрана
    сравнения целиком (`just test-int-local-k comparison`, включая
    `test_comparison_buckets.py`) — см. `docs/insights/parity-with-the-
    existing-surface.md`: паритет доказывает равенство с существующей
    поверхностью на прогоне, а не тест, сравнивающий вывод сам с собой. Этот
    тест — документирование факта на конкретных числах, а не независимое
    доказательство.

    Фикстура — `_three_bucket_contracts`, ТА ЖЕ, что у теста 1 выше: корзина
    ДГП даёт net_per_sqm 1/2/3 — все конечны и положительны. `is_price`
    пропускает каждую ровно так же, как раньше пропускало `!= 0`: состав и
    порядок `contract_ids` не меняются.
    """
    ids = _three_bucket_contracts(db_session, factories)

    agg = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_NET)
    median = _totals_median(agg, bucket=cmp.BUCKET_BASE)

    assert median["value"] == Decimal("2"), "предпосылка: та же фикстура, что у теста 1"
    assert median["comparable_count"] == 3, (
        "все три ячейки конечны и положительны -> ни одна не гасится новым предикатом"
    )
    # Множеством, а не списком: порядок `contract_ids` — `signed_date DESC, id
    # DESC` (`_load_columns`), решение другого правила, к предикату цены
    # отношения не имеющее; сверять здесь нужно СОСТАВ, а не случайную для
    # этого теста очерёдность дат.
    assert set(median["contract_ids"]) == set(ids), (
        "состав списка id не меняется на выборке без непригодных величин"
    )


def test_negative_net_per_sqm_is_excluded_and_the_median_disappears(db_session, factories):
    """Отрицательная ячейка перестаёт быть сравнимой (план задачи 8, утверждения 2, 3).

    Отрицательный `net_per_sqm` — не надуманный вход: знак
    `position_items.total_cost_total` схемой не ограничен, `CHECK` на знак
    нет (независимо подтверждено ревью). Здесь отрицательная ячейка ОДНА —
    меняется именно ЧИСЛО сравнимых, а не итоговое значение симметричным
    гашением пары.

    Три договора, ставка НДС 0 % (нетто = валовое, площадь каждого — 100 м²):
    1000 -> net_per_sqm 10; 3000 -> net_per_sqm 30; -500 -> net_per_sqm -5.

    ДОФИЧЕВЫМ условием (`is not None and != 0`) все три считались бы
    сравнимыми — `-5 != 0` истинно, и `sorted([-5, 10, 30])` дал бы медиану
    10, то есть медиана БЫЛА БЫ. Новым условием (`is_price`) отрицательная
    ячейка не проходит: сравнимых остаётся ДВА — меньше трёх (правило 5), и
    медианы нет вовсе. Один вход предъявляет сразу оба утверждения плана:
    падение `comparable_count` и исчезновение медианы, которая иначе была бы.

    **Собственная предпосылка теста проверяется отдельно (ревью, правка 3).**
    Три утверждения ниже про счётчик/состав/медиану НЕ отличают «c исключён,
    потому что отрицателен» от «c не доехал до ячейки вовсе»: замерено, что
    фикстура БЕЗ категории "1" у c (`state == cmp.ABSENT`, `net_per_sqm is
    None`) даёт ТЕ ЖЕ три внешних результата — `comparable_count == 2`,
    `contract_ids == {a, b}`, `value is None` — при СОВСЕМ другой причине.
    Поэтому здесь дополнительно читается ОТДЕЛЬНАЯ ячейка договора `c` и
    утверждается: величина ДОЕХАЛА (`net_per_sqm is not None`) И именно
    ОТРИЦАТЕЛЬНА — не любая другая причина исключения.
    """
    a = fx.contract_with_area(
        db_session, factories, {"1": ["1000.00"]}, vat_rate=Decimal("0"),
        area_aboveground="50", area_underground="50",
    )
    b = fx.contract_with_area(
        db_session, factories, {"1": ["3000.00"]}, vat_rate=Decimal("0"),
        area_aboveground="50", area_underground="50",
    )
    c = fx.contract_with_area(
        db_session, factories, {"1": ["-500.00"]}, vat_rate=Decimal("0"),
        area_aboveground="50", area_underground="50",
    )
    ids = [a, b, c]

    agg = cmp.build_comparison(db_session, ids, vat_mode=cmp.VAT_MODE_NET)
    median = _totals_median(agg, bucket=cmp.BUCKET_TOTAL)

    assert median["comparable_count"] == 2, (
        "предпосылка нарушена, если жива старая копия правила: она пропустила бы "
        "отрицательную ячейку как сравнимую, и счётчик остался бы 3"
    )
    # Множеством: порядок задаёт `signed_date DESC, id DESC` (`_load_columns`),
    # не порядок вставки — здесь важен СОСТАВ, отсутствие `c`.
    assert set(median["contract_ids"]) == {a, b}, (
        "сравнимыми остаются a и b, отрицательный c выпадает"
    )
    assert median["value"] is None, (
        "два сравнимых меньше трёх -> медианы нет, хотя дофичевым условием она бы была"
    )

    row_total = next(r for r in agg["rows"] if r["code"] == "1")
    cell_c_total = next(cc for cc in row_total["cells"] if cc["contract_id"] == c)[cmp.BUCKET_TOTAL]
    assert cell_c_total["net_per_sqm"] is not None, (
        "предпосылка: величина ДОЕХАЛА до ячейки c — исключение не потому, что она "
        "никогда не прибывала (ABSENT дал бы тот же None здесь и те же три утверждения выше)"
    )
    assert cell_c_total["net_per_sqm"] < 0, (
        "и она именно ОТРИЦАТЕЛЬНА — а не любая другая непригодная причина"
    )


def test_non_finite_net_per_sqm_is_excluded_without_crashing():
    """Нефинитная ячейка исключена, а не роняет сравнение (план задачи 8, утверждение 2).

    Полный конвейер `build_comparison` НЕ умеет породить нефинитный
    `net_per_sqm` — не потому что случая нет в жизни, а потому что каждый
    слой на пути к нему нарочно бросает раньше, чем нефинитное значение
    доедет: VIEW `v_category_totals` (миграция 0012) суммирует `total_cost_
    total` с `FILTER (WHERE <> 'NaN' AND <> 'Infinity' AND <> '-Infinity')`,
    а вся денежная арифметика этого модуля (`_VAT_CONTEXT`, `_DIV_CONTEXT`)
    держит `traps=[Overflow, DivisionByZero, InvalidOperation]`. Поэтому вход
    строится НАПРЯМУЮ на `_compute_median`, минуя `build_comparison`, тем же
    типом `BucketCell`, каким пользуется сама функция — прямой вызов
    приватной функции модуля в тестах этого файла уже есть прецедентом
    (`test_comparison_inflation.py` вызывает `cmp._load_categories` так же).

    Почему это не бесполезная проверка. ДОФИЧЕВОЕ условие (`!= 0`) NaN не
    ловит: сравнение Decimal на неравенство с NaN не бросает и молча
    истинно (`Decimal("NaN") != 0` -> `True`), то есть NaN прошла бы фильтр
    и попала бы в `sorted(...)` вместе с двумя конечными значениями — а `<`
    на NaN бросает `InvalidOperation` (спека §1.2). Значит старое условие на
    этом входе не тихо ошибалось бы, а РОНЯЛО БЫ весь расчёт медианы. Новое
    условие (`is_price`) проверяет конечность ДО сравнения со знаком и
    отсеивает NaN на входе, до всякой сортировки.
    """
    def cell(value: Decimal | None) -> cmp.BucketCell:
        return cmp.BucketCell(
            net=None, shown=None, net_per_sqm=value, shown_per_sqm=None,
            state=cmp.VALUE, deviation_pct=None, incomplete_reasons=frozenset(),
        )

    cells = {
        1: cell(Decimal("10")),
        2: cell(Decimal("20")),
        3: cell(Decimal("NaN")),
    }

    result = cmp._compute_median(cells, [1, 2, 3])

    assert result.comparable_count == 2, (
        "NaN не конечна -> is_price ложен -> исключена, сравнимых остаётся ДВА"
    )
    assert result.contract_ids == [1, 2], "порядок сохраняет договоры 1 и 2, NaN-ячейка выпадает"
    assert result.value is None, "два сравнимых меньше трёх -> медианы нет, а не мусор и не падение"
