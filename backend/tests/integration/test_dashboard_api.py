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
from money.vat import gross_to_net, net_to_gross
from tests.integration.test_analytics_api import _priced_estimate_with_two_proposals

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники: договор → смета → лот → предложение → позиции
# ---------------------------------------------------------------------------

def _chain(factories, *, contract=None, obj=None, amendment_no=None, vat_rate=Decimal("20")):
    """Цепочка до предложения. Возвращает `(contract, estimate, proposal)`.

    `vat_rate` кладётся на предложение — это заявленная файлом база, вход
    `effective_display_rate`. `obj` позволяет посадить несколько договоров на
    один объект (нужно объектной лестнице, задача 2).
    """
    if contract is None:
        kwargs = {"object": obj} if obj is not None else {}
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


def _priced_contract(factories, *, total="1000", vat_rate=Decimal("20"), obj=None):
    """Договор, который лестница обязана УЧЕСТЬ: смета, ставка, полная стоимость."""
    contract, estimate, proposal = _chain(factories, obj=obj, vat_rate=vat_rate)
    _row(factories, proposal, total=total)
    return contract, estimate


def _layer(db_session):
    return {row.contract_id: row for row in crud_dashboard.contract_layer(db_session)}


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
