"""Стартовый дашборд: договорный слой (задача 1 плана).

Что здесь под контролем — ровно то, чего нет ни в одном другом наборе:

* **договорная лестница охвата** (спека §2.5): пять ступеней со строгим
  приоритетом, каждый договор ровно в одной причине, счётчики образуют
  разбиение;
* **действующая ставка — `effective_display_rate`, а не буква решения 3
  макета** (§2.3): договор, у которого ставка заявлена только в
  `estimates.vat_rate_base_override`, обязан быть УЧТЁН;
* **признак допсоглашения — наличие сметы с `amendment_no NOT NULL`, а не
  `COUNT(estimates) > 1`** (§2.4): договор, у которого есть ТОЛЬКО ДС, счётом
  смет не ловится;
* **порядок пересчёта НДС — построчно, ДО накопления** (§2.6): вход с двумя
  предложениями и РАЗНЫМИ базами; на одном предложении ошибка порядка
  невидима, поэтому вход обязан быть именно таким;
* **три условия неполноты** (§2.6), включая `SUM(row_count) = 0`, которое из
  `rows_with_amount < row_count` не выводится: `SUM` по пустому множеству даёт
  `NULL`, и сравнение молча ложно.

Семантика самого VIEW (`v_category_totals`) здесь НЕ проверяется — она под
`test_category_totals_view.py`, и второго её набора быть не должно.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from crud import dashboard as crud_dashboard
from crud.dashboard import (
    CONTRACT_REASON_AMENDMENT,
    CONTRACT_REASON_INCOMPLETE,
    CONTRACT_REASON_NO_ESTIMATE,
    CONTRACT_REASON_NO_RATE,
)
from models import UserRole
from money.vat import gross_to_net, net_to_gross
from tests.integration.test_analytics_api import _priced_estimate_with_two_proposals

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники: договор → смета → лот → предложение → позиции
# ---------------------------------------------------------------------------

def _chain(
    factories,
    *,
    contract=None,
    obj=None,
    rate_class=None,
    amendment_no=None,
    vat_rate=Decimal("20"),
):
    """Цепочка до предложения. Возвращает `(contract, estimate, proposal)`.

    `vat_rate` кладётся на предложение — это заявленная файлом база, вход
    `effective_display_rate`. `obj` позволяет посадить несколько договоров на
    один объект (нужно объектной лестнице, задача 2), `rate_class` — свести
    несколько объектов в одну дорожку диаграммы.
    """
    if contract is None:
        kwargs = {}
        if obj is not None:
            kwargs["object"] = obj
        if rate_class is not None:
            kwargs["rate_class"] = rate_class
        contract = factories.ContractFactory.create(**kwargs)
    estimate = factories.EstimateFactory.create(contract=contract, amendment_no=amendment_no)
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(
        lot=lot, contractor=contract.contractor, vat_rate=vat_rate
    )
    return contract, estimate, proposal


def _row(factories, proposal, *, total, is_chapter=False):
    """Строка сметы с ЗАДАННОЙ файловой стоимостью.

    В `v_category_totals` попадает только `total_cost_total`; `unit_cost_total`
    к суммам дашборда отношения не имеет и оставлен фабричным.
    """
    return factories.PositionItemFactory.create(
        proposal=proposal,
        catalog_position=factories.CatalogPositionFactory.create(),
        is_chapter=is_chapter,
        quantity=Decimal("1"),
        suggested_quantity=Decimal("1"),
        total_cost_total=None if total is None else Decimal(total),
    )


def _priced_contract(
    factories, *, total="1000", vat_rate=Decimal("20"), obj=None, rate_class=None
):
    """Договор, который лестница обязана УЧЕСТЬ: смета, ставка, полная стоимость."""
    contract, estimate, proposal = _chain(
        factories, obj=obj, rate_class=rate_class, vat_rate=vat_rate
    )
    _row(factories, proposal, total=total)
    return contract, estimate


def _object(factories, *, above=None, under=None, useful=None):
    """Объект с ТЭП. `area_total_sp` — генерируемая колонка (надземная +
    подземная), поэтому её считает БД, а тест её только читает."""
    return factories.ObjectFactory.create(
        area_aboveground_sp=None if above is None else Decimal(above),
        area_underground_sp=None if under is None else Decimal(under),
        area_useful_sp=None if useful is None else Decimal(useful),
    )


def _layer(db_session):
    return {row.contract_id: row for row in crud_dashboard.contract_layer(db_session)}


def _objects(db_session):
    contracts = crud_dashboard.contract_layer(db_session)
    return {
        row.object_id: row for row in crud_dashboard.object_layer(db_session, contracts)
    }


# ---------------------------------------------------------------------------
#  Лестница: пять ступеней, каждая на своём входе
# ---------------------------------------------------------------------------

class TestContractLadder:
    def test_contract_without_estimate_has_no_estimate_reason(self, db_session, factories):
        contract = factories.ContractFactory.create()
        db_session.flush()

        assert _layer(db_session)[contract.id].reason == CONTRACT_REASON_NO_ESTIMATE

    def test_contract_with_amendment_is_excluded_as_amendment(self, db_session, factories):
        """У договора есть и исходная смета, и ДС — правило сложения не принято
        (§2.4), поэтому договор уходит целиком, а не «по исходной»."""
        contract, _ = _priced_contract(factories)
        _, _, amendment_proposal = _chain(factories, contract=contract, amendment_no=1)
        _row(factories, amendment_proposal, total="500")
        db_session.flush()

        row = _layer(db_session)[contract.id]
        assert row.reason == CONTRACT_REASON_AMENDMENT
        assert row.amount is None

    def test_contract_with_only_amendment_is_amendment_not_no_estimate(
        self, db_session, factories
    ):
        """Загрузка не требует исходной сметы (§2.4), поэтому договор с ОДНОЙ
        сметой-допсоглашением законен. `COUNT(estimates) > 1` его пропустит —
        одна смета, значит «обычный», — и дашборд посчитает ДС за весь договор.
        """
        contract, _, proposal = _chain(factories, amendment_no=1)
        _row(factories, proposal, total="1000")
        db_session.flush()

        assert _layer(db_session)[contract.id].reason == CONTRACT_REASON_AMENDMENT

    def test_contract_without_any_rate_has_no_rate_reason(self, db_session, factories):
        contract, _, proposal = _chain(factories, vat_rate=None)
        _row(factories, proposal, total="1000")
        db_session.flush()

        row = _layer(db_session)[contract.id]
        assert row.reason == CONTRACT_REASON_NO_RATE
        assert row.display_rate is None

    def test_contract_with_conflicting_proposal_rates_has_no_rate_reason(
        self, db_session, factories
    ):
        """Разногласие заявленных ставок — правило `effective_display_rate`,
        которого в решении 3 макета не было вовсе (§2.3): показать «в какой-то
        из» ставок нельзя."""
        contract, estimate, first = _chain(factories, vat_rate=Decimal("20"))
        _row(factories, first, total="1000")
        lot = factories.LotFactory.create(estimate=estimate)
        second = factories.ProposalFactory.create(
            lot=lot, contractor=contract.contractor, vat_rate=Decimal("22")
        )
        _row(factories, second, total="1000")
        db_session.flush()

        assert _layer(db_session)[contract.id].reason == CONTRACT_REASON_NO_RATE

    def test_contract_with_only_base_override_is_counted(self, db_session, factories):
        """ГЛАВНЫЙ вход §2.3: ставка заявлена ЧЕЛОВЕКОМ в
        `vat_rate_base_override`, файл её не назвал. По букве решения 3 макета
        (`target` иначе `proposals.vat_rate`) договор выглядит как «без ставки»
        и уходит из итогов — на стенде это 2 договора из 6 и 24,6 % денег.
        """
        contract, estimate, proposal = _chain(factories, vat_rate=None)
        _row(factories, proposal, total="1200")
        estimate.vat_rate_base_override = Decimal("20")
        db_session.flush()

        row = _layer(db_session)[contract.id]
        assert row.reason is None
        assert row.display_rate == Decimal("20")
        assert row.amount == Decimal("1200")

    def test_estimate_without_any_priced_row_is_incomplete(self, db_session, factories):
        """Смета из ОДНИХ разделов не даёт VIEW ни одной строки (позиционная
        ветвь строится `WHERE pi.is_chapter = false`), поэтому `SUM(row_count)`
        равен `NULL`, а `rows_with_amount < row_count` — `NULL`, то есть ложь.
        Условие `SUM(row_count) = 0` обязательно и из второго не выводится.
        """
        contract, _, proposal = _chain(factories)
        _row(factories, proposal, total="1000", is_chapter=True)
        db_session.flush()

        row = _layer(db_session)[contract.id]
        assert row.reason == CONTRACT_REASON_INCOMPLETE
        assert row.amount is None

    def test_contract_with_unpriced_row_is_incomplete(self, db_session, factories):
        contract, _, proposal = _chain(factories)
        _row(factories, proposal, total="1000")
        _row(factories, proposal, total=None)
        db_session.flush()

        assert _layer(db_session)[contract.id].reason == CONTRACT_REASON_INCOMPLETE

    def test_contract_with_all_zero_costs_is_counted_with_zero(self, db_session, factories):
        """Заявленный ноль — факт, а не отсутствие факта (§2.6). Контроль
        неизменности к ужесточению правила неполноты."""
        contract, _, proposal = _chain(factories)
        _row(factories, proposal, total="0")
        _row(factories, proposal, total="0")
        db_session.flush()

        row = _layer(db_session)[contract.id]
        assert row.reason is None
        assert row.amount == Decimal("0")


# ---------------------------------------------------------------------------
#  Приоритет и разбиение
# ---------------------------------------------------------------------------

class TestContractCoverage:
    def test_amendment_wins_over_missing_rate(self, db_session, factories):
        """Пересечение причин: договор одновременно с ДС и без ставки. Без
        строгого приоритета он попал бы в оба счётчика, и они перестали бы быть
        разбиением."""
        contract, _, proposal = _chain(factories, vat_rate=None)
        _row(factories, proposal, total="1000")
        _, _, amendment_proposal = _chain(factories, contract=contract, amendment_no=1)
        _row(factories, amendment_proposal, total="500")
        db_session.flush()

        rows = crud_dashboard.contract_layer(db_session)
        coverage = crud_dashboard.contract_coverage(rows)

        assert _layer(db_session)[contract.id].reason == CONTRACT_REASON_AMENDMENT
        assert coverage["reasons"][CONTRACT_REASON_AMENDMENT] == 1
        assert coverage["reasons"][CONTRACT_REASON_NO_RATE] == 0

    def test_reasons_and_counted_partition_all_contracts(self, db_session, factories):
        """Сумма счётчиков причин плюс учтённые равна общему числу договоров.

        Пятый договор нарочно ПОДХОДИТ ПОД ДВЕ причины сразу (и ДС, и без
        ставки): на входе, где у каждого договора ровно одна беда, разбиение
        выполняется и при независимом счёте причин — то есть утверждение
        доказывало бы не то.
        """
        factories.ContractFactory.create()  # без сметы
        _priced_contract(factories)  # учтён
        _, _, no_rate_proposal = _chain(factories, vat_rate=None)
        _row(factories, no_rate_proposal, total="1000")
        _, _, incomplete_proposal = _chain(factories)
        _row(factories, incomplete_proposal, total=None)
        both, _, both_proposal = _chain(factories, vat_rate=None)
        _row(factories, both_proposal, total="1000")
        _, _, both_amendment = _chain(factories, contract=both, amendment_no=1)
        _row(factories, both_amendment, total="500")
        db_session.flush()

        coverage = crud_dashboard.contract_coverage(crud_dashboard.contract_layer(db_session))

        assert coverage["total"] == 5
        assert coverage["counted"] == 1
        assert sum(coverage["reasons"].values()) + coverage["counted"] == coverage["total"]
        assert coverage["reasons"] == {
            CONTRACT_REASON_NO_ESTIMATE: 1,
            CONTRACT_REASON_AMENDMENT: 1,
            CONTRACT_REASON_NO_RATE: 1,
            CONTRACT_REASON_INCOMPLETE: 1,
        }


# ---------------------------------------------------------------------------
#  Порядок пересчёта НДС: построчно, ДО накопления
# ---------------------------------------------------------------------------

def test_amount_restates_each_row_before_accumulating(db_session, factories):
    """Смета с ДВУМЯ предложениями и РАЗНЫМИ базами (20 % и 10 %), цель 0 %.

    Числа подобраны так, что верный порядок даёт целое:
      132 / 1.2 = 110, 132 / 1.1 = 120  →  230.
    Сложить сырые строки и пересчитать итог ОДИН раз нельзя ни по какой базе:
      (132 + 132) / 1.2 = 220,  (132 + 132) / 1.1 = 240.
    На одном предложении все три числа совпали бы — поэтому вход именно такой.
    """
    contract, estimate, _ = _priced_estimate_with_two_proposals(
        factories,
        unit_cost_total=Decimal("132"),
        vat_rates=[Decimal("20"), Decimal("10")],
    )
    estimate.vat_rate_target = Decimal("0")
    db_session.flush()

    row = _layer(db_session)[contract.id]

    expected = net_to_gross(gross_to_net(Decimal("132"), Decimal("20")), Decimal("0")) + \
        net_to_gross(gross_to_net(Decimal("132"), Decimal("10")), Decimal("0"))
    assert row.reason is None
    assert row.display_rate == Decimal("0")
    assert row.amount == expected
    assert row.amount == Decimal("230")
    assert row.amount != Decimal("220")
    assert row.amount != Decimal("240")


# ---------------------------------------------------------------------------
#  Показатели шапки (решения 5 и 7 макета)
# ---------------------------------------------------------------------------
#
# Считает их БЭКЕНД, и это отдельный блок тестов. Без него фронтенд проверял бы
# отображение своей же MSW-фикстуры: сервер мог бы не вернуть половину полей, а
# прогон остался бы зелёным.

class TestHeadline:
    def test_each_area_carries_its_own_coverage(self, db_session, factories):
        """У НАДЗЕМНОЙ с ПОДЗЕМНОЙ охват общий (`CHECK` миграции 0009 держит их
        парой), у ПОЛЕЗНОЙ — свой (`CHECK` миграции 0013 её с парой не связывает).
        Объект, у которого заведена только полезная, входит в охват полезной и НЕ
        входит в охват пары — общий «не заведена у N» это различие скрывал бы.
        """
        _object(factories, above="100", under="20")
        _object(factories, above="300", under="80")
        _object(factories, useful="50")
        db_session.flush()

        areas = crud_dashboard.area_summary(db_session)

        assert areas["total"]["value"] == Decimal("500")
        assert areas["total"]["coverage"] == {"total": 3, "counted": 2}
        assert areas["aboveground"]["value"] == Decimal("400")
        assert areas["aboveground"]["coverage"] == {"total": 3, "counted": 2}
        assert areas["underground"]["value"] == Decimal("100")
        assert areas["underground"]["coverage"] == {"total": 3, "counted": 2}
        # Полезная: сумма ТОЛЬКО третьего объекта, охват — один из трёх.
        assert areas["useful"]["value"] == Decimal("50")
        assert areas["useful"]["coverage"] == {"total": 3, "counted": 1}

    def test_largest_and_smallest_objects_by_area(self, db_session, factories):
        biggest = _object(factories, above="900", under="100")
        _object(factories, above="400", under="100")
        smallest = _object(factories, above="50", under="10")
        _object(factories, useful="777")  # без пары — в экстремумы не входит
        db_session.flush()

        areas = crud_dashboard.area_summary(db_session)

        assert areas["largest"]["object_id"] == biggest.id
        assert areas["largest"]["area_total_sp"] == Decimal("1000")
        assert areas["smallest"]["object_id"] == smallest.id
        assert areas["smallest"]["area_total_sp"] == Decimal("60")
        assert areas["total"]["coverage"] == {"total": 4, "counted": 3}

    def test_counters_do_not_merge_objects_and_contracts(self, db_session, factories):
        """Решение 9 макета: «5 договоров» и «1 объект» — разные сущности, они не
        складываются. Счётчики обязаны приезжать порознь."""
        rate_class = factories.RateClassFactory.create()
        first = _object(factories, above="100", under="0")
        second = _object(factories, above="200", under="0")
        _priced_contract(factories, obj=first, rate_class=rate_class)
        _priced_contract(factories, obj=second, rate_class=rate_class)
        _priced_contract(factories, obj=second, rate_class=rate_class)
        factories.ContractFactory.create(object=first)  # без сметы
        db_session.flush()

        counters = crud_dashboard.base_counters(
            db_session, crud_dashboard.contract_layer(db_session)
        )

        assert counters["objects"] == 2
        assert counters["contracts"] == 4
        assert counters["contracts_with_estimate"] == 3
        assert counters["objects"] != counters["contracts"]

    def test_per_sqm_extremes_carry_their_own_coverage(self, db_session, factories):
        cheap = _object(factories, above="100", under="0")
        pricey = _object(factories, above="100", under="0")
        _object(factories, above="100", under="0")  # без договора — вне охвата
        _priced_contract(factories, obj=cheap, total="1000")
        _priced_contract(factories, obj=pricey, total="5000")
        db_session.flush()

        contracts = crud_dashboard.contract_layer(db_session)
        objects = crud_dashboard.object_layer(db_session, contracts)
        extremes = crud_dashboard.per_sqm_extremes(objects)

        assert extremes["max"]["object_id"] == pricey.id
        assert extremes["max"]["per_sqm"] == Decimal("50")
        assert extremes["min"]["object_id"] == cheap.id
        assert extremes["min"]["per_sqm"] == Decimal("10")
        assert extremes["coverage"] == {"total": 3, "counted": 2}


# ---------------------------------------------------------------------------
#  Объектная лестница и разведение двух охватов
# ---------------------------------------------------------------------------

class TestObjectLadder:
    def test_object_with_several_contracts_is_out_of_ranking(self, db_session, factories):
        obj = _object(factories, above="100", under="0")
        _priced_contract(factories, obj=obj)
        _priced_contract(factories, obj=obj)
        db_session.flush()

        assert _objects(db_session)[obj.id].reason == (
            crud_dashboard.OBJECT_REASON_MANY_CONTRACTS
        )

    def test_object_without_counted_contract_is_out_of_ranking(self, db_session, factories):
        """Единственный договор объекта исключён договорной лестницей (без
        сметы) — ставить в рейтинг нечего."""
        obj = _object(factories, above="100", under="0")
        factories.ContractFactory.create(object=obj)
        db_session.flush()

        assert _objects(db_session)[obj.id].reason == (
            crud_dashboard.OBJECT_REASON_NO_COUNTED_CONTRACT
        )

    def test_object_without_area_is_in_ranking_but_not_in_chart(self, db_session, factories):
        """Отсутствие площади — охват ДИАГРАММЫ, а не рейтинга: сумма договора
        известна, и в рейтинге объекту место есть."""
        obj = _object(factories)
        _priced_contract(factories, obj=obj, total="1000")
        db_session.flush()

        contracts = crud_dashboard.contract_layer(db_session)
        objects = crud_dashboard.object_layer(db_session, contracts)
        row = {r.object_id: r for r in objects}[obj.id]

        assert row.reason is None
        assert row.amount == Decimal("1000")
        assert row.per_sqm is None
        assert [r.object_id for r in crud_dashboard.object_ranking(objects)] == [obj.id]
        chart = crud_dashboard.per_sqm_chart(objects)
        assert chart["coverage"] == {"total": 1, "counted": 0}
        assert chart["classes"] == []

    def test_two_ladders_are_different_ladders(self, db_session, factories):
        """ОБЯЗАТЕЛЬНЫЙ ВХОД, разводящий охваты (спека §2.5, DoD).

        Объект с ДВУМЯ УЧТЁННЫМИ договорами выпадает ТОЛЬКО из рейтинга; деньги
        обоих его договоров остаются в ИТОГО — они настоящие. Оба утверждения
        обязаны стоять в ОДНОМ тесте: порознь каждое пройдёт и на реализации с
        одним общим фильтром, которая выкидывает объект отовсюду сразу.
        """
        obj = _object(factories, above="100", under="0")
        _priced_contract(factories, obj=obj, total="700")
        _priced_contract(factories, obj=obj, total="300")
        db_session.flush()

        contracts = crud_dashboard.contract_layer(db_session)
        objects = crud_dashboard.object_layer(db_session, contracts)

        # Рейтинг: объекта нет.
        assert {r.object_id for r in crud_dashboard.object_ranking(objects)} == set()
        assert {r.object_id: r for r in objects}[obj.id].reason == (
            crud_dashboard.OBJECT_REASON_MANY_CONTRACTS
        )
        # Деньги: оба договора учтены и оба в ИТОГО.
        assert crud_dashboard.contract_coverage(contracts)["counted"] == 2
        assert crud_dashboard.money_total(contracts) == Decimal("1000")


# ---------------------------------------------------------------------------
#  Рейтинг: топ-10 и тай-брейк
# ---------------------------------------------------------------------------

class TestRanking:
    def test_eleventh_object_is_hidden_but_counted_in_coverage(self, db_session, factories):
        for index in range(11):
            obj = _object(factories, above="100", under="0")
            _priced_contract(factories, obj=obj, total=str(1000 + index))
        db_session.flush()

        contracts = crud_dashboard.contract_layer(db_session)
        objects = crud_dashboard.object_layer(db_session, contracts)
        top = crud_dashboard.object_ranking(objects)

        assert len(top) == 10
        assert crud_dashboard.object_coverage(objects)["counted"] == 11
        # Убран самый дешёвый, а не произвольный.
        assert min(row.amount for row in top) == Decimal("1001")

    def test_equal_sums_get_a_defined_order(self, db_session, factories):
        """Поведенческая половина тай-брейка. Одной её МАЛО: PostgreSQL и
        `sorted` вправе стабильно возвращать тот же порядок, и сто повторных
        прогонов ничего не докажут. Вторая половина — тест ФОРМЫ ключа
        сортировки (`tests/unit/test_dashboard_ranking.py`).
        """
        first = _object(factories, above="100", under="0")
        second = _object(factories, above="100", under="0")
        _priced_contract(factories, obj=first, total="1000")
        _priced_contract(factories, obj=second, total="1000")
        db_session.flush()

        contracts = crud_dashboard.contract_layer(db_session)
        objects = crud_dashboard.object_layer(db_session, contracts)
        order = [row.object_id for row in crud_dashboard.object_ranking(objects)]

        assert order == sorted([first.id, second.id])


# ---------------------------------------------------------------------------
#  Диаграмма ₽/м²: обе стороны и формула точки
# ---------------------------------------------------------------------------

class TestPerSqmChart:
    def test_class_with_two_objects_has_spread_between_its_min_and_max(
        self, db_session, factories
    ):
        """ПОЛОЖИТЕЛЬНАЯ сторона. Без неё реализация, не рисующая полос вовсе,
        проходит отрицательный тест целиком."""
        rate_class = factories.RateClassFactory.create()
        cheap = _object(factories, above="100", under="0")
        pricey = _object(factories, above="100", under="0")
        _priced_contract(factories, obj=cheap, rate_class=rate_class, total="1000")
        _priced_contract(factories, obj=pricey, rate_class=rate_class, total="3000")
        db_session.flush()

        contracts = crud_dashboard.contract_layer(db_session)
        objects = crud_dashboard.object_layer(db_session, contracts)
        lane = crud_dashboard.per_sqm_chart(objects)["classes"][0]

        assert lane["rate_class_id"] == rate_class.id
        assert lane["spread"] == {"min": Decimal("10"), "max": Decimal("30")}
        assert sorted(point["per_sqm"] for point in lane["points"]) == [
            Decimal("10"),
            Decimal("30"),
        ]

    def test_class_with_one_object_has_no_spread(self, db_session, factories):
        """Решение 11: размаха не существует, рисовать его было бы выдумкой."""
        rate_class = factories.RateClassFactory.create()
        obj = _object(factories, above="100", under="0")
        _priced_contract(factories, obj=obj, rate_class=rate_class, total="1000")
        db_session.flush()

        contracts = crud_dashboard.contract_layer(db_session)
        objects = crud_dashboard.object_layer(db_session, contracts)
        lane = crud_dashboard.per_sqm_chart(objects)["classes"][0]

        assert len(lane["points"]) == 1
        assert lane["spread"] is None

    def test_per_sqm_denominator_is_area_total_without_useful(self, db_session, factories):
        """Знаменатель ₽/м² — `area_total_sp` (надземная + подземная). Полезная в
        неё НЕ входит (миграция 0013), и фича полезной площади этот знаменатель
        нигде не меняла. Вход подобран так, что ошибка знаменателя видна:
          1000 / 100 = 10, а 1000 / (100 + 30) = 7.69…
        """
        obj = _object(factories, above="60", under="40", useful="30")
        _priced_contract(factories, obj=obj, total="1000")
        db_session.flush()

        contracts = crud_dashboard.contract_layer(db_session)
        row = {r.object_id: r for r in crud_dashboard.object_layer(db_session, contracts)}[
            obj.id
        ]

        assert row.area_total_sp == Decimal("100")
        assert row.per_sqm == Decimal("10")


# ---------------------------------------------------------------------------
#  Эндпоинт основного таба (задача 3, спека §2.8)
# ---------------------------------------------------------------------------

DASHBOARD_URL = "/api/v1/analytics/dashboard"


class TestDashboardEndpoint:
    def test_answers_both_roles(self, client, factories, db_session):
        """Основной таб — чтение, а его `AGENTS.md` §3 отдаёт и `member`.
        Разведение эндпоинтов (§2.8) ради того и сделано: закрыть весь дашборд
        вместе с диагностиками было бы отказом читателю в том, что ему положено.
        """
        obj = _object(factories, above="100", under="0")
        _priced_contract(factories, obj=obj, total="1000")
        db_session.commit()

        assert client.get(DASHBOARD_URL).status_code == 200
        client.auth_state["role"] = UserRole.member
        assert client.get(DASHBOARD_URL).status_code == 200

    def test_money_and_areas_reach_json_as_strings(self, client, factories, db_session):
        """Смотрим на СЫРОЕ тело: после `json.loads` строка и `float`
        неразличимы, а забытый `decimal_json` даёт ровно `float`.

        Негативная половина обязательна — без неё утверждение прошло бы и на
        числе, если бы строка совпала подстрокой где-то ещё в теле.
        """
        obj = _object(factories, above="62399.70", under="13341.30")
        _priced_contract(factories, obj=obj, total="1234567890.12")
        db_session.commit()

        compact = client.get(DASHBOARD_URL).text.replace(" ", "")

        assert '"amount":"1234567890.12"' in compact
        assert '"amount":1234567890.12' not in compact
        assert '"area_total_sp":"75741.00"' in compact
        assert '"area_total_sp":75741' not in compact
