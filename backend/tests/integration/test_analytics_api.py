"""Аналитика фазы 6: паспорт (§7.4) и сквозная матрица (§6, §7.5).

Проверяется то, что фаза 6 добавила **поверх** VIEW отклонений, а не сама семантика
отклонений — она уже покрыта `test_deviations_view.py`, и её второй проверки здесь
быть не должно (иначе два набора начнут расходиться, как расходились бы две
реализации).

Что здесь под контролем:

* фильтр «только последняя смета договора» (§6), включая `NULL` как **минимальный**
  `amendment_no` — самая тихая из возможных ошибок: цифры на месте, но не те;
* средневзвешенная ставка `SUM(unit_cost_total × w) / SUM(w)` на работе,
  встречающейся в смете дважды, и точность результата (`Decimal`, не float);
* «нет норматива» отличимо от «0 %» и в паспорте, и в матрице (§10);
* деньги уезжают **строками** (§3) — и в паспорте, и в каждой ячейке матрицы;
* топ-N берёт N из БД и реагирует на его изменение (§7.4);
* набор колонок матрицы не зависит от страницы;
* drill-down показывает те же строки, из которых сложилась ставка.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from crud.analytics import (
    _NET_COST,
    DECLARED_VIEW_COLUMNS,
    DEVIATION_INPUTS,
    _fold_cell,
    _net_deviation,
)
from models import PASSPORT_TOP_N_DEFAULT, CatalogKind
from money.vat import gross_to_net, quantize_money

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Помощники: цепочка договор → смета → лот → предложение → позиции
# ---------------------------------------------------------------------------

def _estimate_with(
    factories,
    *,
    contract=None,
    amendment_no=None,
    estimate_date=dt.date(2025, 4, 1),
    vat_rate=Decimal("0"),
):
    """Цепочка договор → смета → лот → предложение.

    `vat_rate` по умолчанию `0` (задача 3 пересчёта НДС): при базе 0 % нетто
    численно равно валовому (`gross_to_net(x, 0) == x`), поэтому все тесты этого
    файла, писавшиеся ДО перевода матрицы и паспорта на нетто-ось и не
    указывавшие ставку явно, продолжают проверять те же значения — без ставки
    НДС проверять здесь нечего, это дело `test_matrix_cell_rate_is_net_of_
    declared_vat` и соседних тестов пересчёта.
    """
    contract = contract or factories.ContractFactory.create()
    estimate = factories.EstimateFactory.create(
        contract=contract, amendment_no=amendment_no, data_prepared_on_date=estimate_date
    )
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(
        lot=lot, contractor=contract.contractor, vat_rate=vat_rate
    )
    return contract, estimate, proposal


def _position(factories, proposal, position, *, unit_cost, weight, total=None):
    """Расценённая позиция: вес кладётся в `suggested_quantity` (§6: приоритетный)."""
    return factories.PositionItemFactory.create(
        proposal=proposal,
        catalog_position=position,
        unit_cost_total=Decimal(unit_cost),
        suggested_quantity=Decimal(weight),
        quantity=Decimal("1"),
        total_cost_total=Decimal(total) if total is not None else Decimal(unit_cost) * Decimal(weight),
    )


# ---------------------------------------------------------------------------
#  Помощники нетто-оси (задача 3 пересчёта НДС): построены на `_estimate_with`/
#  `_position` — фабрик `factories.priced_estimate(...)` в проекте НЕТ, составные
#  строители живут локальными хелперами тестового файла (см. приложение
#  оркестратора к брифу задачи).
# ---------------------------------------------------------------------------

def _find_catalog_position(factories, title: str | None):
    if title is None:
        return factories.CatalogPositionFactory.create()
    from models import CatalogPosition

    session = factories._session_holder["session"]
    existing = session.query(CatalogPosition).filter_by(standard_job_title=title).first()
    return existing or factories.CatalogPositionFactory.create(standard_job_title=title)


def _find_contract(factories, contract_number: str | None):
    if contract_number is None:
        return factories.ContractFactory.create()
    from models import Contract

    session = factories._session_holder["session"]
    existing = session.query(Contract).filter_by(contract_number=contract_number).first()
    return existing or factories.ContractFactory.create(contract_number=contract_number)


def _priced_estimate(
    factories,
    *,
    unit_cost_total=None,
    vat_rate=None,
    weight=Decimal("1"),
    catalog_title=None,
    contract_number=None,
    positions=None,
):
    """Смета с расценённой(ыми) позицией(ями), одна ставка НДС на предложение.

    `catalog_title`/`contract_number`, повторённые между вызовами, переиспользуют
    ту же каталожную строку/договор — так собираются несколько предложений на
    одну пару (работа, договор), нужные тестам ячейки и веса строки.

    `positions=[(unit_cost, vat_rate), ...]` заводит НЕСКОЛЬКО предложений (лотов)
    под одним договором/сметой — по одному на пару, поскольку ставка НДС живёт на
    предложении (`proposals.vat_rate`), а не на позиции.
    """
    contract = _find_contract(factories, contract_number)
    estimate = factories.EstimateFactory.create(contract=contract)
    position = _find_catalog_position(factories, catalog_title)

    pairs = positions if positions is not None else [(unit_cost_total, vat_rate)]
    for cost, rate in pairs:
        lot = factories.LotFactory.create(estimate=estimate)
        proposal = factories.ProposalFactory.create(
            lot=lot, contractor=contract.contractor, vat_rate=rate
        )
        _position(factories, proposal, position, unit_cost=cost, weight=weight)
    return contract, estimate, position


def _priced_estimate_with_two_proposals(factories, *, unit_cost_total, vat_rates, weight=Decimal("1")):
    return _priced_estimate(
        factories,
        positions=[(unit_cost_total, rate) for rate in vat_rates],
        weight=weight,
    )


def _contract_with_standard(factories, *, unit_cost_total, vat_rate, standard, weight=Decimal("1")):
    """Смета с одной расценённой позицией и нормативом на её класс.

    `catalog_position_id` кладётся на возвращённый `contract` — удобство ТОЛЬКО
    для тестов этого файла (у ORM-модели `Contract` такого атрибута нет), чтобы
    вызывающему не пришлось тащить третий возврат ради одного id.
    """
    contract, estimate, position = _priced_estimate(
        factories, unit_cost_total=unit_cost_total, vat_rate=vat_rate, weight=weight
    )
    factories.RateStandardFactory.create(
        catalog_position=position,
        rate_class=contract.rate_class,
        standard_unit_rate=standard,
        valid_from=dt.date(2025, 1, 1),
    )
    contract.catalog_position_id = position.id
    return contract


def _passport(client, contract_id: int) -> dict:
    response = client.get(f"/api/v1/analytics/passport/{contract_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _matrix(client, **params) -> dict:
    response = client.get("/api/v1/analytics/matrix", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _cell_of(row: dict, contract_id: int) -> dict | None:
    return next((c for c in row["cells"] if c["contract_id"] == contract_id), None)


# ---------------------------------------------------------------------------
#  Отражение VIEW в Python не должно разъезжаться с самим VIEW
# ---------------------------------------------------------------------------

def test_declared_view_columns_match_the_database(db_session):
    """`crud.analytics.DEVIATION_INPUTS` — объявление руками, VIEW создан raw SQL
    в 0002 и переименован (лишён `deviation_pct`, обзавёлся базой/целью НДС)
    миграцией 0012.

    `alembic check` их не сверяет (VIEW не в `Base.metadata`), поэтому расхождение
    поймает только этот тест: переименованная в миграции колонка иначе проявилась бы
    ошибкой выполнения на живом стенде.
    """
    actual = tuple(
        db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'v_position_deviation_inputs' ORDER BY ordinal_position"
            )
        ).scalars()
    )
    assert actual == DECLARED_VIEW_COLUMNS


# ---------------------------------------------------------------------------
#  Паспорт (§7.4)
# ---------------------------------------------------------------------------

class TestPassportRequisites:
    def test_unknown_contract_gives_404(self, client):
        assert client.get("/api/v1/analytics/passport/999999").status_code == 404

    def test_contract_without_estimate_still_prints(self, client, factories):
        """Карточка заведена, файл не загружен — паспорт печатается по реквизитам."""
        contract = factories.ContractFactory.create()
        body = _passport(client, contract.id)

        assert body["estimate"] is None
        assert body["key_rates"] == []
        assert body["contract"]["contract_number"] == contract.contract_number
        assert body["totals"]["positions_priced"] == 0

    def test_requisites_cover_the_fields_dod_requires(self, client, factories):
        """§1 пункт 2: номер, подписант, дата, сумма, класс — и объект с подрядчиком."""
        contract = factories.ContractFactory.create(
            signer="Петров П.П.",
            signed_date=dt.date(2025, 2, 3),
            total_amount=Decimal("1234567890.12"),
        )
        requisites = _passport(client, contract.id)["contract"]

        assert requisites["contract_number"] == contract.contract_number
        assert requisites["signer"] == "Петров П.П."
        assert requisites["signed_date"] == "2025-02-03"
        assert requisites["rate_class_title"] is not None
        assert requisites["object_title"] is not None
        assert requisites["contractor_title"] is not None

    def test_contract_amount_is_a_string_not_a_float(self, client, factories):
        """§3: деньги в JSON строками. Сумма с 12 значащими цифрами — как раз тот
        размер, на котором double начинает врать в последнем разряде."""
        contract = factories.ContractFactory.create(total_amount=Decimal("1234567890.12"))
        raw = client.get(f"/api/v1/analytics/passport/{contract.id}").text

        assert '"total_amount":"1234567890.12"' in raw.replace(", ", ",")
        assert json.loads(raw)["contract"]["total_amount"] == "1234567890.12"


class TestPassportKeyRates:
    def test_top_n_is_ordered_by_position_amount(self, client, factories):
        """§7.4: топ-N по `total_cost_total`, а не по ставке и не по порядку в смете."""
        contract, _estimate, proposal = _estimate_with(factories)
        cheap = factories.CatalogPositionFactory.create(standard_job_title="Дешёвая работа")
        pricey = factories.CatalogPositionFactory.create(standard_job_title="Дорогая работа")
        # У дешёвой ставка ВЫШЕ — иначе тест прошёл бы и при сортировке по ставке.
        _position(factories, proposal, cheap, unit_cost="900", weight="1", total="900")
        _position(factories, proposal, pricey, unit_cost="100", weight="500", total="50000")

        titles = [r["job_title"] for r in _passport(client, contract.id)["key_rates"]]
        assert len(titles) == 2
        assert titles[0] == _job_title_of(factories, pricey)

    def test_top_n_respects_the_setting_from_the_database(self, client, factories):
        """N хранится в БД (§7.4) — значит его изменение обязано менять длину топа."""
        contract, _estimate, proposal = _estimate_with(factories)
        for i in range(5):
            position = factories.CatalogPositionFactory.create()
            _position(factories, proposal, position, unit_cost=str(100 + i), weight="10")

        assert len(_passport(client, contract.id)["key_rates"]) == 5

        assert client.patch("/api/v1/settings", json={"passport_top_n": 2}).status_code == 200
        body = _passport(client, contract.id)
        assert body["top_n"] == 2
        assert len(body["key_rates"]) == 2
        # Итоги — по ВСЕЙ совокупности, а не по показанному топу.
        assert body["totals"]["positions_priced"] == 5
        assert body["totals"]["positions_shown"] == 2

    def test_only_the_latest_estimate_participates(self, client, factories):
        """§6: последняя смета договора. NULL — МИНИМАЛЬНЫЙ amendment_no."""
        contract, _initial, initial_proposal = _estimate_with(factories, amendment_no=None)
        _contract, _amend, amend_proposal = _estimate_with(
            factories, contract=contract, amendment_no=1
        )
        old_work = factories.CatalogPositionFactory.create(standard_job_title="Работа из исходной")
        new_work = factories.CatalogPositionFactory.create(standard_job_title="Работа из ДС")
        _position(factories, initial_proposal, old_work, unit_cost="100", weight="10")
        _position(factories, amend_proposal, new_work, unit_cost="200", weight="10")

        body = _passport(client, contract.id)
        assert body["estimate"]["amendment_no"] == 1
        titles = [r["job_title"] for r in body["key_rates"]]
        assert titles == [_job_title_of(factories, new_work)]

    def test_no_standard_is_null_not_zero(self, client, factories):
        """§10: «нет норматива» обязано быть отличимо от «0 %»."""
        contract, _estimate, proposal = _estimate_with(factories)
        without = factories.CatalogPositionFactory.create()
        exact = factories.CatalogPositionFactory.create()
        _position(factories, proposal, without, unit_cost="100", weight="10", total="2000")
        _position(factories, proposal, exact, unit_cost="100", weight="10", total="1000")
        factories.RateStandardFactory.create(
            catalog_position=exact,
            rate_class=contract.rate_class,
            standard_unit_rate=Decimal("100"),
            valid_from=dt.date(2025, 1, 1),
        )

        rates = {r["catalog_position_id"]: r for r in _passport(client, contract.id)["key_rates"]}
        assert rates[without.id]["deviation_pct"] is None
        assert rates[without.id]["standard_unit_rate"] is None
        # Ровно по нормативу — это НОЛЬ, а не «нет данных».
        assert Decimal(rates[exact.id]["deviation_pct"]) == Decimal("0")

    def test_totals_separate_missing_standard_from_zero_deviation(self, client, factories):
        contract, _estimate, proposal = _estimate_with(factories)
        over = factories.CatalogPositionFactory.create()
        exact = factories.CatalogPositionFactory.create()
        without = factories.CatalogPositionFactory.create()
        _position(factories, proposal, over, unit_cost="150", weight="10")
        _position(factories, proposal, exact, unit_cost="100", weight="10")
        _position(factories, proposal, without, unit_cost="100", weight="10")
        for position in (over, exact):
            factories.RateStandardFactory.create(
                catalog_position=position,
                rate_class=contract.rate_class,
                standard_unit_rate=Decimal("100"),
                valid_from=dt.date(2025, 1, 1),
            )

        totals = _passport(client, contract.id)["totals"]
        assert totals["positions_priced"] == 3
        assert totals["with_standard"] == 2
        assert totals["without_standard"] == 1
        # Только превышение, не «есть отклонение»: ровно по нормативу — не превышение.
        assert totals["over_standard"] == 1

    def test_money_in_key_rates_is_string(self, client, factories):
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(
            factories, proposal, position, unit_cost="1075.35475", weight="3", total="3226.06425"
        )

        rate = _passport(client, contract.id)["key_rates"][0]
        assert isinstance(rate["unit_cost_total"], str)
        assert isinstance(rate["total_cost_total"], str)
        # Дробная часть не потеряна: ставка утверждена именно такой.
        assert Decimal(rate["unit_cost_total"]) == Decimal("1075.35475")


def _job_title_of(factories, position) -> str:
    """Название работы, как его вернёт паспорт — из СМЕТЫ, а не из каталога.

    `PositionItemFactory` генерирует `job_title_in_proposal` своей
    последовательностью, независимо от каталожного названия, поэтому сверять надо с
    тем, что реально лежит в позиции.
    """
    from models import PositionItem

    session = factories._session_holder["session"]
    return session.execute(
        sa.select(PositionItem.job_title_in_proposal).where(
            PositionItem.catalog_position_id == position.id
        )
    ).scalars().first()


# ---------------------------------------------------------------------------
#  Матрица (§6, §7.5)
# ---------------------------------------------------------------------------

class TestMatrixSemantics:
    def test_empty_scope_returns_empty_matrix(self, client):
        body = _matrix(client)
        assert body["rows"] == []
        assert body["columns"] == []
        assert body["total"] == 0

    def test_weighted_average_over_repeated_work(self, client, factories):
        """§6: работа встречается дважды → ячейка = SUM(ставка × вес) / SUM(вес).

        Числа выбраны так, что средневзвешенное (140) **отличается** от среднего
        арифметического (150): иначе тест прошёл бы и на неверной формуле.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(factories, proposal, position, unit_cost="100", weight="30")
        _position(factories, proposal, position, unit_cost="200", weight="20")

        row = _matrix(client)["rows"][0]
        cell = _cell_of(row, contract.id)
        assert Decimal(cell["rate"]) == Decimal("140")

    def test_work_priced_only_at_zero_weight_gives_no_cell(self, client, factories):
        """§6: «если таких строк нет — ячейка пустая». Именно это и защищает `w > 0`.

        **Первая редакция этого теста ничего не доказывала.** Она брала одну строку с
        весом 10 и одну с весом 0 и проверяла, что ставка равна 100 — но строка с
        нулевым весом не влияет ни на числитель, ни на знаменатель:
        `(100·10 + 900·0) / (10 + 0) = 100` и с условием `w > 0`, и без него. Снятие
        защиты тест не валило.

        Настоящее следствие отсутствия условия — деление на ноль: у работы, все строки
        которой имеют нулевой вес, `SUM(w) = 0`. §6 требует, чтобы такая ячейка была
        **пустой**, а не ошибкой.
        """
        _contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(factories, proposal, position, unit_cost="900", weight="0", total="0")

        # Без `w > 0` здесь был бы DivisionByZero из PostgreSQL, то есть 500.
        assert _matrix(client)["rows"] == []

    def test_zero_weight_row_does_not_dilute_a_priced_work(self, client, factories):
        """Соседство нулевого веса с осмысленным не меняет ставку.

        Тест намеренно оставлен, хотя снятием `w > 0` он НЕ валится (арифметика та же,
        см. тест выше): он закрывает не условие, а формулу — если знаменателем станет
        `COUNT(*)` вместо `SUM(w)`, ставка съедет с 100 на 500.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(factories, proposal, position, unit_cost="100", weight="10")
        _position(factories, proposal, position, unit_cost="900", weight="0", total="0")

        cell = _cell_of(_matrix(client)["rows"][0], contract.id)
        assert Decimal(cell["rate"]) == Decimal("100")

    def test_position_without_any_quantity_is_excluded(self, client, factories):
        """`w = COALESCE(suggested_quantity, quantity)`; оба NULL → строки нет.

        Проверяется отдельно от нулевого веса: `NULL > 0` даёт `NULL`, и если условие
        когда-нибудь напишут как `w <> 0`, эта позиция вернулась бы в расчёт и уронила
        деление.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=position,
            unit_cost_total=Decimal("100"),
            suggested_quantity=None,
            quantity=None,
            total_cost_total=Decimal("100"),
        )

        assert _matrix(client)["rows"] == []

    def test_only_latest_estimate_in_matrix(self, client, factories):
        """§6 в матрице: исходная смета (amendment_no NULL) — не последняя."""
        contract, _initial, initial_proposal = _estimate_with(factories, amendment_no=None)
        _c, _amend, amend_proposal = _estimate_with(factories, contract=contract, amendment_no=2)
        position = factories.CatalogPositionFactory.create()
        _position(factories, initial_proposal, position, unit_cost="100", weight="10")
        _position(factories, amend_proposal, position, unit_cost="500", weight="10")

        cell = _cell_of(_matrix(client)["rows"][0], contract.id)
        assert Decimal(cell["rate"]) == Decimal("500")

    def test_rows_are_catalog_positions_only(self, client, factories):
        """§6: строки — `catalog_positions (kind='POSITION')`."""
        contract, _estimate, proposal = _estimate_with(factories)
        header = factories.CatalogPositionFactory.create(kind=CatalogKind.HEADER.value)
        _position(factories, proposal, header, unit_cost="100", weight="10")

        assert _matrix(client)["rows"] == []

    def test_deviation_is_computed_from_the_weighted_rate(self, client, factories):
        """Отклонение считается от ячейки, а не усредняется из построчных.

        Ставки 100 и 200 при весах 30/20 дают ячейку 140; норматив 100 → +40 %.
        Усреднение построчных отклонений (0 % и +100 %) дало бы +50 %.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(factories, proposal, position, unit_cost="100", weight="30")
        _position(factories, proposal, position, unit_cost="200", weight="20")
        factories.RateStandardFactory.create(
            catalog_position=position,
            rate_class=contract.rate_class,
            standard_unit_rate=Decimal("100"),
            valid_from=dt.date(2025, 1, 1),
        )

        cell = _cell_of(_matrix(client)["rows"][0], contract.id)
        assert Decimal(cell["deviation_pct"]) == Decimal("40")

    def test_no_standard_gives_null_deviation_in_matrix(self, client, factories):
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(factories, proposal, position, unit_cost="100", weight="10")

        cell = _cell_of(_matrix(client)["rows"][0], contract.id)
        assert cell["deviation_pct"] is None
        assert cell["standard_unit_rate"] is None

    def test_cell_money_is_string(self, client, factories):
        """§3 в каждой ячейке — в матрице денег больше всего.

        Значение округлено до копеек (`quantize_money` на границе ответа, задача 3
        пересчёта НДС: ячейка теперь нетто-величина, а не сырое деление) — тест
        проверяет ТИП (`Decimal`, не float) и точность округлённого результата, а
        не сохранение исходных шести знаков после запятой, которого больше нет ни
        у одного пересчитанного денежного поля.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(factories, proposal, position, unit_cost="1234567.891234", weight="1")

        cell = _cell_of(_matrix(client)["rows"][0], contract.id)
        assert isinstance(cell["rate"], str)
        assert Decimal(cell["rate"]) == Decimal("1234567.89")


class TestMatrixColumnsAndPaging:
    def _two_contracts_with_positions(
        self, factories, n_positions: int, *, equal_weights: bool = False
    ):
        """Две колонки и `n_positions` строк.

        Args:
            equal_weights: одинаковый вес у всех работ, то есть **равные суммы строк**
                матрицы. Нужно тесту пагинации: порядок строк задан суммой, и пока
                суммы различны, тай-брейк не задействован ни разу — тест без этого
                флага проверял бы сортировку, которой не требуется тай-брейк.
        """
        first, _e1, p1 = _estimate_with(factories)
        second, _e2, p2 = _estimate_with(factories)
        positions = []
        for i in range(n_positions):
            position = factories.CatalogPositionFactory.create()
            positions.append(position)
            # Вес — он же множитель суммы ячейки, по которой упорядочены строки.
            weight = "10" if equal_weights else str((n_positions - i) * 10)
            _position(factories, p1, position, unit_cost="100", weight=weight)
            _position(factories, p2, position, unit_cost="120", weight=weight)
        return first, second, positions

    def test_columns_are_independent_of_the_page(self, client, factories):
        """Набор колонок обязан быть одинаков на всех страницах.

        Иначе таблица «прыгала» бы при листании, а сравнение по строке потеряло бы
        смысл: одна и та же работа сравнивалась бы с разным набором договоров.
        """
        first, second, _positions = self._two_contracts_with_positions(factories, 5)

        page_one = _matrix(client, page=1, page_size=2)
        page_three = _matrix(client, page=3, page_size=2)

        assert [c["contract_id"] for c in page_one["columns"]] == [
            c["contract_id"] for c in page_three["columns"]
        ]
        assert {first.id, second.id} == {c["contract_id"] for c in page_one["columns"]}
        assert len(page_one["rows"]) == 2
        assert page_one["total"] == 5

    def test_pages_do_not_overlap_or_lose_rows_when_amounts_tie(self, client, factories):
        """Листание при РАВНЫХ суммах строк: ни дублей, ни потерь.

        Равные суммы обязательны. Первая редакция теста давала работам разные суммы,
        то есть строгий порядок существовал сам по себе и тай-брейк не участвовал —
        снятие тай-брейка тест не валило (замер пробником).

        Оговорка, которую надо знать читателю: и в этом виде тест доказывает
        **следствие** (страницы бьются без дублей), а не сам тай-брейк.
        Отсутствие тай-брейка даёт *неопределённый* порядок, а не гарантированно
        неверный: на маленькой таблице PostgreSQL может вернуть один и тот же порядок
        для всех страниц и без него. Именно поэтому рядом стоит
        `test_row_order_has_a_unique_tiebreaker`, который проверяет сам запрос.
        """
        self._two_contracts_with_positions(factories, 12, equal_weights=True)

        seen: list[int] = []
        for page in (1, 2, 3, 4):
            seen += [
                r["catalog_position_id"] for r in _matrix(client, page=page, page_size=3)["rows"]
            ]

        assert len(seen) == 12, f"страницы потеряли строки: {len(seen)} из 12"
        assert len(set(seen)) == 12, "строка попала на две страницы"

    def test_row_order_has_a_unique_tiebreaker(self, db_session):
        """Порядок строк матрицы обязан быть ПОЛНЫМ, а не только по сумме.

        Проверка структурная — по скомпилированному SQL, — и это осознанный выбор.
        Свойство здесь «порядок определён однозначно», а его нарушение —
        неопределённость, которую поведенческий тест на маленькой таблице
        воспроизвести не может (см. тест выше). Структурная проверка при этом
        **опровергаема**: убери тай-брейк — она краснеет.
        """
        from crud import analytics

        # Ловим текст запроса страницы: собираем его тем же кодом, что эндпоинт.
        statements: list[str] = []
        original_execute = db_session.execute

        def spy(statement, *args, **kwargs):
            # Компилируем диалектом соединения, а не дефолтным: `str(statement)`
            # берёт дефолтный диалект и предупреждает про `DISTINCT ON`, которого тот
            # не умеет. Предупреждение безвредно, но шумит в каждом полном прогоне.
            statements.append(str(statement.compile(dialect=db_session.bind.dialect)))
            return original_execute(statement, *args, **kwargs)

        db_session.execute = spy  # type: ignore[method-assign]
        try:
            analytics.get_matrix(db_session, page=1, page_size=3)
        finally:
            db_session.execute = original_execute  # type: ignore[method-assign]

        paged = [s for s in statements if "LIMIT" in s and "row_amount" in s]
        assert paged, "запрос страницы матрицы не найден — проверять нечего"

        # Существен ровно тот ORDER BY, что стоит ПЕРЕД LIMIT: он решает, какие
        # строки попадут на страницу. Внешняя сортировка результата тай-брейк тоже
        # содержит, и первая редакция этой проверки читала именно её — поэтому
        # проходила и со снятым тай-бреком (замер пробником).
        window = re.search(r"ORDER BY (?P<cols>[^()]*?)\s*LIMIT", paged[-1], re.DOTALL)
        assert window, f"не найден ORDER BY перед LIMIT в запросе страницы: {paged[-1][-400:]}"
        ordering = window.group("cols")
        assert "catalog_position_id" in ordering, (
            "в ORDER BY страницы нет уникального тай-брейка — при равных суммах "
            f"состав страниц не определён. ORDER BY перед LIMIT: {ordering.strip()}"
        )

    def test_rows_ordered_by_amount_descending(self, client, factories):
        _first, _second, positions = self._two_contracts_with_positions(factories, 4)
        order = [r["catalog_position_id"] for r in _matrix(client)["rows"]]
        assert order == [p.id for p in positions]

    def test_contract_in_scope_is_a_column_even_without_matching_work(self, client, factories):
        """Договор выборки остаётся колонкой, даже если на странице нет его ячеек.

        Иначе «нет данных по этой работе» и «договора нет в выборке» слились бы в
        одно, и пустая колонка читалась бы как отсутствие договора.
        """
        with_work, _e1, p1 = _estimate_with(factories)
        without_work, _e2, _p2 = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(factories, p1, position, unit_cost="100", weight="10")

        body = _matrix(client)
        assert {c["contract_id"] for c in body["columns"]} == {with_work.id, without_work.id}
        assert _cell_of(body["rows"][0], without_work.id) is None

    def test_class_filter_narrows_both_rows_and_columns(self, client, factories):
        kept = factories.RateClassFactory.create(title="Оставляемый класс")
        dropped = factories.RateClassFactory.create(title="Отбрасываемый класс")
        kept_contract = factories.ContractFactory.create(rate_class=kept)
        dropped_contract = factories.ContractFactory.create(rate_class=dropped)
        _c1, _e1, p1 = _estimate_with(factories, contract=kept_contract)
        _c2, _e2, p2 = _estimate_with(factories, contract=dropped_contract)
        shared = factories.CatalogPositionFactory.create()
        _position(factories, p1, shared, unit_cost="100", weight="10")
        _position(factories, p2, shared, unit_cost="500", weight="10")

        body = _matrix(client, rate_class_id=kept.id)
        assert [c["contract_id"] for c in body["columns"]] == [kept_contract.id]
        assert len(body["rows"]) == 1
        assert _cell_of(body["rows"][0], dropped_contract.id) is None

    def test_period_filter_uses_the_comparison_date(self, client, factories):
        """Период — по дате сравнения (§4), не по дате подписания.

        У договора дата подписания 2025-03-01, у сметы дата подготовки 2026-05-01.
        Фильтр по 2026 году обязан договор оставить: норматив подбирается по дате
        сметы, и фильтр должен отбирать по тому же признаку.
        """
        contract = factories.ContractFactory.create(signed_date=dt.date(2025, 3, 1))
        _c, _e, proposal = _estimate_with(
            factories, contract=contract, estimate_date=dt.date(2026, 5, 1)
        )
        position = factories.CatalogPositionFactory.create()
        _position(factories, proposal, position, unit_cost="100", weight="10")

        inside = _matrix(client, date_from="2026-01-01", date_to="2026-12-31")
        assert [c["contract_id"] for c in inside["columns"]] == [contract.id]
        assert len(inside["rows"]) == 1

        outside = _matrix(client, date_from="2025-01-01", date_to="2025-12-31")
        assert outside["columns"] == []
        assert outside["rows"] == []

    def test_text_filter_searches_the_catalog_title(self, client, factories):
        _c, _e, proposal = _estimate_with(factories)
        wanted = factories.CatalogPositionFactory.create(standard_job_title="Кладка кирпичная")
        other = factories.CatalogPositionFactory.create(standard_job_title="Стяжка пола")
        _position(factories, proposal, wanted, unit_cost="100", weight="10")
        _position(factories, proposal, other, unit_cost="100", weight="10")

        body = _matrix(client, q="кирпич")
        assert [r["catalog_position_id"] for r in body["rows"]] == [wanted.id]
        assert body["total"] == 1


class TestMatrixDrillDown:
    def test_drill_down_shows_the_rows_behind_the_cell(self, client, factories):
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(factories, proposal, position, unit_cost="100", weight="30", total="3000")
        _position(factories, proposal, position, unit_cost="200", weight="20", total="4000")

        response = client.get(
            "/api/v1/analytics/matrix/cell",
            params={"contract_id": contract.id, "catalog_position_id": position.id},
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert len(body["items"]) == 2
        # Проверяемость: показанные строки обязаны складываться в показанную ставку.
        weighted = sum(
            Decimal(i["unit_cost_total"]) * Decimal(i["weight"]) for i in body["items"]
        )
        weight = sum(Decimal(i["weight"]) for i in body["items"])
        assert weighted / weight == Decimal("140")

    def test_drill_down_ignores_older_estimates(self, client, factories):
        contract, _initial, initial_proposal = _estimate_with(factories, amendment_no=None)
        _c, _amend, amend_proposal = _estimate_with(factories, contract=contract, amendment_no=1)
        position = factories.CatalogPositionFactory.create()
        _position(factories, initial_proposal, position, unit_cost="100", weight="10")
        _position(factories, amend_proposal, position, unit_cost="500", weight="10")

        body = client.get(
            "/api/v1/analytics/matrix/cell",
            params={"contract_id": contract.id, "catalog_position_id": position.id},
        ).json()

        assert body["amendment_no"] == 1
        assert [Decimal(i["unit_cost_total"]) for i in body["items"]] == [Decimal("500")]

    def test_drill_down_without_estimate_gives_404(self, client, factories):
        contract = factories.ContractFactory.create()
        position = factories.CatalogPositionFactory.create()
        response = client.get(
            "/api/v1/analytics/matrix/cell",
            params={"contract_id": contract.id, "catalog_position_id": position.id},
        )
        assert response.status_code == 404


def test_default_top_n_matches_the_agreed_value(client, factories):
    """§7.4 называет 15 прямо — значение по умолчанию не должно уехать незамеченным."""
    contract = factories.ContractFactory.create()
    assert _passport(client, contract.id)["top_n"] == PASSPORT_TOP_N_DEFAULT


# ---------------------------------------------------------------------------
#  Пустой результат при непустой смете (найдено прогоном стенда фазы 6)
# ---------------------------------------------------------------------------

class TestPendingReviewIsExplained:
    """Почему паспорт и матрица пусты, хотя смета загружена и расценена.

    **Нашёл прогон стенда, а не тест.** На живой базе все 1830 позиций реальной
    сметы имели цену, но каталог целиком состоял из `TO_REVIEW` — и VIEW отклонений
    их не берёт (§4: только `kind='POSITION'`). Паспорт при этом сообщал «у позиций
    не заполнена цена за единицу», то есть называл неверную причину и отправлял
    искать проблему не там.

    Тесты этого не поймали потому, что фикстуры создают каталожные строки сразу
    `POSITION`. Здесь состояние воспроизводится намеренно.
    """

    def _priced_but_unreviewed(self, factories, *, kind=CatalogKind.TO_REVIEW.value):
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create(kind=kind)
        _position(factories, proposal, position, unit_cost="100", weight="10")
        return contract

    def test_passport_reports_positions_waiting_for_review(self, client, factories):
        contract = self._priced_but_unreviewed(factories)
        body = _passport(client, contract.id)

        assert body["key_rates"] == []
        # Совокупность VIEW пуста — и это правильно...
        assert body["totals"]["positions_priced"] == 0
        # ...но причина названа, а не оставлена на догадки.
        assert body["totals"]["positions_pending_review"] == 1

    def test_matrix_reports_positions_waiting_for_review(self, client, factories):
        self._priced_but_unreviewed(factories)
        body = _matrix(client)

        assert body["rows"] == []
        # Договор в выборке есть (смета загружена), а работ нет — счётчик объясняет.
        assert len(body["columns"]) == 1
        assert body["positions_pending_review"] == 1

    def test_unpriced_position_is_not_counted_as_pending_review(self, client, factories):
        """Без цены — другая причина, и смешивать их нельзя.

        У позиции без цены сравнивать нечего независимо от каталога, поэтому она не
        должна попадать в счётчик «ждут матчинга»: иначе подсказка отправила бы
        человека в очередь Review, где он ничего не исправит.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create(kind=CatalogKind.TO_REVIEW.value)
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=position,
            unit_cost_total=None,
            suggested_quantity=Decimal("10"),
            total_cost_total=None,
        )

        assert _passport(client, contract.id)["totals"]["positions_pending_review"] == 0

    def test_reviewed_position_leaves_the_pending_counter(self, client, factories):
        """Разобранная работа уходит из счётчика и появляется в топе.

        Проверяется переход, а не два состояния по отдельности: именно он показывает,
        что счётчик считает то, что нужно.
        """
        contract = self._priced_but_unreviewed(factories, kind=CatalogKind.POSITION.value)
        body = _passport(client, contract.id)

        assert body["totals"]["positions_pending_review"] == 0
        assert len(body["key_rates"]) == 1

    @pytest.mark.parametrize(
        "kind",
        [CatalogKind.HEADER.value, CatalogKind.TRASH.value, CatalogKind.LOT_HEADER.value],
    )
    def test_already_marked_rows_are_not_pending_review(self, client, factories, kind):
        """`HEADER`/`TRASH`/`LOT_HEADER` — РАЗОБРАННЫЕ строки, их нет в очереди Review.

        Очередь ручного матчинга — это позиции, привязанные к `kind='TO_REVIEW'`
        (§5); `HEADER` и `TRASH` §5.4.3 описывает как строку, которая «уже вручную
        размечена как не-работа», и в очередь она не попадает. Значит считать их
        «ожидающими матчинга» — значит советовать разобрать очередь, в которой их
        нет: человек откроет Review и не найдёт там ничего.

        Замечание внешнего ревью; подтверждено этим тестом до правки.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create(kind=kind)
        _position(factories, proposal, position, unit_cost="100", weight="10")

        # В VIEW такой строки нет (§4), поэтому топ пуст...
        body = _passport(client, contract.id)
        assert body["key_rates"] == []
        # ...но это НЕ «ждёт матчинга»: разбирать нечего.
        assert body["totals"]["positions_pending_review"] == 0
        assert _matrix(client)["positions_pending_review"] == 0

    @pytest.mark.parametrize(
        "kind",
        [CatalogKind.HEADER.value, CatalogKind.TRASH.value, CatalogKind.LOT_HEADER.value],
    )
    def test_marked_rows_are_counted_as_non_work(self, client, factories, kind):
        """Разобранные не-работы учтены СВОИМ счётчиком, а не смешаны с очередью.

        Появился как следствие правки по замечанию ревью: как только `HEADER` перестал
        считаться «ожидающим матчинга», паспорт начал утверждать «не заполнена цена» —
        неправду. Отдельный счётчик даёт экрану назвать третью причину как она есть.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create(kind=kind)
        _position(factories, proposal, position, unit_cost="100", weight="10")

        totals = _passport(client, contract.id)["totals"]
        assert totals["positions_pending_review"] == 0
        assert totals["positions_non_work"] == 1
        assert _matrix(client)["positions_non_work"] == 1

    def test_counters_do_not_overlap(self, client, factories):
        """Один и тот же набор позиций не должен попадать в оба счётчика.

        Иначе экран мог бы одновременно звать в очередь и сообщать, что правки не
        требуется, — а человек не поймёт, что делать.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        waiting = factories.CatalogPositionFactory.create(kind=CatalogKind.TO_REVIEW.value)
        marked = factories.CatalogPositionFactory.create(kind=CatalogKind.TRASH.value)
        _position(factories, proposal, waiting, unit_cost="100", weight="10")
        _position(factories, proposal, marked, unit_cost="200", weight="10")

        totals = _passport(client, contract.id)["totals"]
        assert totals["positions_pending_review"] == 1
        assert totals["positions_non_work"] == 1

    def test_unmatched_position_is_pending_review(self, client, factories):
        """Позиция вовсе без каталожной строки — тоже ждёт разбора.

        Такое состояние возможно, если импорт прервался между импортом и матчингом
        (§5): для человека это тот же случай, что `TO_REVIEW`.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=None,
            unit_cost_total=Decimal("100"),
            suggested_quantity=Decimal("10"),
            total_cost_total=Decimal("1000"),
        )

        assert _passport(client, contract.id)["totals"]["positions_pending_review"] == 1

    def test_pending_counter_is_scoped_to_the_selection(self, client, factories):
        """Счётчик матрицы считает по договорам ВЫБОРКИ, а не по всей базе.

        Иначе подсказка говорила бы о работах, которых человек на этом экране всё
        равно не видит, — и «разберите очередь» не изменило бы для него ничего.
        """
        kept_class = factories.RateClassFactory.create(title="Класс выборки")
        kept = factories.ContractFactory.create(rate_class=kept_class)
        _c1, _e1, kept_proposal = _estimate_with(factories, contract=kept)
        reviewed = factories.CatalogPositionFactory.create(kind=CatalogKind.POSITION.value)
        _position(factories, kept_proposal, reviewed, unit_cost="100", weight="10")

        other = factories.ContractFactory.create()
        _c2, _e2, other_proposal = _estimate_with(factories, contract=other)
        unreviewed = factories.CatalogPositionFactory.create(kind=CatalogKind.TO_REVIEW.value)
        _position(factories, other_proposal, unreviewed, unit_cost="100", weight="10")

        # Вне выборки неразобранная позиция есть, внутри — нет.
        assert _matrix(client)["positions_pending_review"] == 1
        assert _matrix(client, rate_class_id=kept_class.id)["positions_pending_review"] == 0


# ---------------------------------------------------------------------------
#  Нетто-ось матрицы и паспорта (задача 3 пересчёта НДС, спека §2.4, §6)
# ---------------------------------------------------------------------------

class TestMatrixNetAxis:
    def test_matrix_cell_rate_is_net_of_declared_vat(self, client, factories, db_session):
        _priced_estimate(factories, unit_cost_total=Decimal("120"), vat_rate=Decimal("20"))
        db_session.commit()
        cell = client.get("/api/v1/analytics/matrix").json()["rows"][0]["cells"][0]
        assert Decimal(cell["rate"]) == Decimal("100.00")

    def test_matrix_cell_amount_is_net(self, client, factories, db_session):
        _priced_estimate(
            factories, unit_cost_total=Decimal("120"), weight=Decimal("2"), vat_rate=Decimal("20")
        )
        db_session.commit()
        cell = client.get("/api/v1/analytics/matrix").json()["rows"][0]["cells"][0]
        assert Decimal(cell["amount"]) == Decimal("200.00")

    def test_matrix_row_amount_is_net_and_orders_rows(self, client, factories, db_session):
        """СТРОГАЯ инверсия: валовым выше A, по нетто выше B.

        Вход подобран так, что нетто НЕ совпадают, — иначе порядок решал бы
        тай-брейк, и тест был бы зелёным и при валовой сортировке:
          A: 122 при НДС 22 % -> нетто 100
          B: 111 при НДС 10 % -> нетто 100.909...
        """
        _priced_estimate(
            factories, catalog_title="A", unit_cost_total=Decimal("122"),
            weight=Decimal("1"), vat_rate=Decimal("22"),
        )
        _priced_estimate(
            factories, catalog_title="B", unit_cost_total=Decimal("111"),
            weight=Decimal("1"), vat_rate=Decimal("10"),
        )
        db_session.commit()
        rows = client.get("/api/v1/analytics/matrix").json()["rows"]
        assert [row["job_title"] for row in rows] == ["B", "A"]
        assert Decimal(rows[0]["row_amount"]) == Decimal("100.91")
        assert Decimal(rows[1]["row_amount"]) == Decimal("100.00")

    def test_row_amount_is_partial_and_flagged_when_a_base_is_unknown(
        self, client, factories, db_session
    ):
        """`SUM` игнорирует NULL: без признака неполная сумма выглядела бы полной."""
        _priced_estimate(
            factories, catalog_title="A", contract_number="C-1",
            unit_cost_total=Decimal("120"), weight=Decimal("1"), vat_rate=Decimal("20"),
        )
        _priced_estimate(
            factories, catalog_title="A", contract_number="C-2",
            unit_cost_total=Decimal("500"), weight=Decimal("1"), vat_rate=None,
        )
        db_session.commit()
        row = client.get("/api/v1/analytics/matrix").json()["rows"][0]
        assert Decimal(row["row_amount"]) == Decimal("100.00")
        assert row["row_amount_incomplete"] is True

    def test_row_amount_is_empty_when_every_base_is_unknown(self, client, factories, db_session):
        """Пусто, а не ноль: ноль читался бы как «работы на ноль рублей»."""
        _priced_estimate(
            factories, catalog_title="A", unit_cost_total=Decimal("500"),
            weight=Decimal("1"), vat_rate=None,
        )
        _priced_estimate(
            factories, catalog_title="B", unit_cost_total=Decimal("120"),
            weight=Decimal("1"), vat_rate=Decimal("20"),
        )
        db_session.commit()
        rows = client.get("/api/v1/analytics/matrix").json()["rows"]
        assert rows[-1]["job_title"] == "A"  # NULLS LAST
        assert rows[-1]["row_amount"] is None
        assert rows[-1]["row_amount_incomplete"] is True

    def test_matrix_yields_one_cell_per_position_and_contract(self, client, factories, db_session):
        """Группировка по базе не имеет права раздваивать ячейку."""
        _priced_estimate_with_two_proposals(
            factories, unit_cost_total=Decimal("120"), vat_rates=[Decimal("20"), Decimal("20")]
        )
        db_session.commit()
        row = client.get("/api/v1/analytics/matrix").json()["rows"][0]
        contract_ids = [cell["contract_id"] for cell in row["cells"]]
        assert len(contract_ids) == len(set(contract_ids))

    def test_matrix_cell_is_empty_without_vat_base(self, client, factories, db_session):
        _priced_estimate(factories, unit_cost_total=Decimal("120"), vat_rate=None)
        db_session.commit()
        cell = client.get("/api/v1/analytics/matrix").json()["rows"][0]["cells"][0]
        assert cell["rate"] is None
        assert cell["deviation_reason"] == "unknown_vat_base"

    def test_matrix_cell_keeps_standard_unit_rate_without_vat_base(
        self, client, factories, db_session
    ):
        """Норматив не гаснет вместе с ячейкой — отступление от текста плана в
        пользу спеки (найдено ревью задачи 3): спека §2.5 говорит дословно
        «норматив при неизвестной базе показывается как нетто; не вычисляется
        только отклонение». Норматив от НДС не зависит и есть нетто по
        определению, поэтому гасить его вместе с `rate`/`amount` значило бы
        стирать разницу между «норматив есть, сравнить не с чем» и «норматива
        нет вовсе» — а ради этой разницы и заведена пара кодов причины.
        """
        _contract_with_standard(
            factories, unit_cost_total=Decimal("120"), vat_rate=None, standard=Decimal("100")
        )
        db_session.commit()
        cell = client.get("/api/v1/analytics/matrix").json()["rows"][0]["cells"][0]
        assert cell["rate"] is None
        assert cell["amount"] is None
        assert cell["deviation_pct"] is None
        assert cell["deviation_reason"] == "unknown_vat_base"
        assert Decimal(cell["standard_unit_rate"]) == Decimal("100")

    def test_row_amount_excludes_partially_unknown_cell(self, client, factories, db_session):
        """Ячейка с одной известной и одной неизвестной базой скрыта целиком —
        значит её известная часть НЕ имеет права попасть в вес строки.

        Без правила «единица неполноты — ячейка» сюда попало бы 100.00 от первой
        позиции, и `row_amount` разошёлся бы с суммой показанных ячеек.
        """
        _priced_estimate(
            factories, catalog_title="A", contract_number="C-1",
            positions=[(Decimal("120"), Decimal("20")), (Decimal("500"), None)],
        )
        _priced_estimate(
            factories, catalog_title="A", contract_number="C-2",
            positions=[(Decimal("240"), Decimal("20"))],
        )
        db_session.commit()
        row = client.get("/api/v1/analytics/matrix").json()["rows"][0]

        hidden = next(c for c in row["cells"] if c["rate"] is None)
        shown = next(c for c in row["cells"] if c["rate"] is not None)
        assert hidden["deviation_reason"] == "unknown_vat_base"
        assert Decimal(row["row_amount"]) == Decimal(shown["amount"])
        assert row["row_amount_incomplete"] is True

    # -----------------------------------------------------------------------
    #  Круг 3 (ре-ревью Codex, PR #21, найдено оркестратором лично): та же
    #  неполнота, но по ДРУГОЙ причине — не неизвестная база НДС, а NaN/
    #  Infinity стоимость при ИЗВЕСТНОЙ базе (открытый хвост Ф4, §5.6).
    #  `row_amount_incomplete` на `main` не существует вовсе — признак завела
    #  эта ветка, и наполовину честным его сделала тоже эта ветка: он
    #  проверял только `cell_unknown`, а `cell_not_finite` не проверял совсем.
    # -----------------------------------------------------------------------

    def test_row_amount_excludes_a_non_finite_cell_and_flags_incompleteness(
        self, client, factories, db_session
    ):
        """Прямая зеркальная пара к `test_row_amount_is_partial_and_flagged_
        when_a_base_is_unknown` выше — та же форма теста, другая причина
        неполноты.

        Краснеет от возврата прежнего поведения (проверено снятием правки —
        `git stash` вернул `_cell_weights_cte`/`row_totals` без
        `cell_not_finite`): `row_amount` содержал бы `NaN` (весь `SUM`
        становится NaN от одной нефинитной ячейки — `NaN` не `NULL`, `SUM` его
        не игнорирует), а `row_amount_incomplete` оставался бы `False`, то
        есть строка отчиталась бы «вес полон», неся при этом мусор.
        """
        _priced_estimate(
            factories, catalog_title="A", contract_number="C-1",
            unit_cost_total=Decimal("120"), weight=Decimal("1"), vat_rate=Decimal("20"),
        )
        _priced_estimate(
            factories, catalog_title="A", contract_number="C-2",
            unit_cost_total=Decimal("NaN"), weight=Decimal("1"), vat_rate=Decimal("20"),
        )
        db_session.commit()
        row = client.get("/api/v1/analytics/matrix").json()["rows"][0]
        assert Decimal(row["row_amount"]) == Decimal("100.00")
        assert row["row_amount_incomplete"] is True

    def test_row_amount_excludes_partially_non_finite_cell(self, client, factories, db_session):
        """Зеркало `test_row_amount_excludes_partially_unknown_cell`: ячейка с
        одним финитным и одним NaN-предложением скрыта ЦЕЛИКОМ (единица
        неполноты — ячейка, а не строка VIEW), значит её финитная часть НЕ
        имеет права попасть в вес строки."""
        _priced_estimate(
            factories, catalog_title="A", contract_number="C-1",
            positions=[(Decimal("120"), Decimal("20")), (Decimal("NaN"), Decimal("20"))],
        )
        _priced_estimate(
            factories, catalog_title="A", contract_number="C-2",
            positions=[(Decimal("240"), Decimal("20"))],
        )
        db_session.commit()
        row = client.get("/api/v1/analytics/matrix").json()["rows"][0]

        hidden = next(c for c in row["cells"] if c["rate"] is None)
        shown = next(c for c in row["cells"] if c["rate"] is not None)
        assert hidden["deviation_reason"] == "not_finite"
        assert Decimal(row["row_amount"]) == Decimal(shown["amount"])
        assert row["row_amount_incomplete"] is True

    def test_sql_net_weight_agrees_with_python(self, db_session, factories):
        """Единственное нетто-выражение в SQL обязано совпадать с money.vat.

        Отступление от §2.6 допущено ради сортировки и пагинации; расхождение двух
        площадок ловится здесь, а не на стенде.

        SQL-сторона собирается ИЗ САМОГО `crud.analytics._NET_COST` — не
        переписывается вручную строкой `sa.text(...)`. Ручная копия проверяла бы
        третье, независимое выражение: расхождение между ПРОДАКШЕН-выражением и
        `money.vat` осталось бы незамеченным, если бы разошлись именно они, а не
        текст теста и `money.vat`.

        ДВЕ одинаковые строки, а не одна: `100 / 120` не представимо конечной
        десятичной дробью (83.333...), и сумма двух таких строк (166.666...)
        округляется до 166.67 (ROUND_HALF_UP). Если бы округление до копеек
        случайно попало ВНУТРЬ `_NET_COST` (на строку, а не на сумму), тот же вход
        дал бы 83.33 + 83.33 = 166.66 — другое число. На одной строке эта разница
        не проявилась бы: `round(x) == round(round(x))` тривиально, и первая
        редакция теста (единственная строка, `120 × 3 / 120` = ровно `300`) её не
        обнаруживала — снятием защиты подтверждено, см. отчёт задачи 3.
        """
        _priced_estimate(
            factories,
            positions=[(Decimal("100"), Decimal("20")), (Decimal("100"), Decimal("20"))],
            weight=Decimal("1"),
        )
        db_session.commit()
        from_sql = db_session.execute(
            sa.select(sa.func.sum(_NET_COST)).select_from(DEVIATION_INPUTS)
        ).scalar()
        from_python = gross_to_net(Decimal("100"), Decimal("20")) + gross_to_net(
            Decimal("100"), Decimal("20")
        )
        assert quantize_money(from_sql) == quantize_money(from_python)
        assert quantize_money(from_sql) == Decimal("166.67")


class TestPassportNetAxis:
    def test_phase6_passport_deviation_is_net_based(self, client, factories, db_session):
        """Норматив — цена без НДС: 120 с НДС 20 % против норматива 100 дают 0 %."""
        contract = _contract_with_standard(
            factories, unit_cost_total=Decimal("120"), vat_rate=Decimal("20"),
            standard=Decimal("100"),
        )
        db_session.commit()
        body = client.get(f"/api/v1/analytics/passport/{contract.id}").json()
        assert Decimal(body["key_rates"][0]["deviation_pct"]) == Decimal("0")
        assert body["totals"]["over_standard"] == 0


# ---------------------------------------------------------------------------
#  Дефект 1 (ре-ревью Codex, PR #21): `NaN`/`Infinity` в валовой цене доезжают
#  сюда открытым хвостом Ф4 (§5.6: импорт не проверяет годность цены).
#  Арифметика (`gross_to_net`, `_deviation`) тихо распространяет нефинитное
#  значение дальше — но `_passport_totals` ЗАТЕМ его СРАВНИВАЕТ
#  (`deviation_pct > 0`), а сравнение нефинитного `Decimal` бросает
#  `InvalidOperation` (тот же класс, что уже чинили в `crud.reports.
#  _amount_sort_key` и `services.excel.deviation_font` — здесь третья
#  поверхность). `_net_deviation` обязана возвращать финитный `deviation_pct`
#  либо `None` вместе с ЧЕСТНОЙ причиной: не «нет норматива» (норматив есть)
#  и не «неизвестна база НДС» (база известна) — четвёртый код `not_finite`.
# ---------------------------------------------------------------------------

class TestNetDeviationNotFinite:
    """Прямые вызовы `_net_deviation` — без БД, чистая функция."""

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_gross_yields_no_deviation_with_honest_reason(self, bad):
        """Краснеет от текущего кода: `net`/`deviation_pct` возвращались
        нефинитными, а `reason` — `None` (== «нормы отношение известно»), хотя
        норматив в этом вызове ЕСТЬ (100) — то есть причина ЛГАЛА."""
        net, deviation_pct, reason = _net_deviation(
            Decimal(bad), Decimal("20"), Decimal("100")
        )
        assert deviation_pct is None
        assert reason == "not_finite"
        # `net` может остаться нефинитным (это отдельное поле паспорта —
        # `unit_cost_net`, у него свой открытый хвост через `quantize_money`,
        # см. AGENTS.md §11), но ОТКЛОНЕНИЕ обязано быть либо числом, либо
        # отсутствовать целиком.

    def test_not_finite_wins_over_missing_standard(self):
        """Норматива тоже нет — но `not_finite` важнее «нет норматива»: сама
        величина не число, и это не то же самое, что «сравнивать не с чем»."""
        _net, deviation_pct, reason = _net_deviation(Decimal("NaN"), Decimal("20"), None)
        assert deviation_pct is None
        assert reason == "not_finite"

    def test_finite_case_is_unaffected(self):
        """Контроль: обычный конечный случай не должен пострадать от починки."""
        net, deviation_pct, reason = _net_deviation(
            Decimal("120"), Decimal("20"), Decimal("100")
        )
        assert net == Decimal("100")
        assert deviation_pct == Decimal("0")
        assert reason is None


class TestPassportSurvivesNonFiniteCost:
    """Воспроизведение дефекта 1 ЧЕРЕЗ САМ ЭНДПОИНТ паспорта (не только прямым
    вызовом `_net_deviation`) — оркестратор явно потребовал не верить одному
    способу репродукции."""

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_cost_with_standard_does_not_crash_passport(
        self, client, factories, db_session, bad
    ):
        """До починки: `_passport_totals` сравнивает `deviation_pct > 0` на
        `Decimal('NaN')` и бросает `decimal.InvalidOperation` — запрос
        завершается 500-й (`TestClient` с `raise_server_exceptions=True`
        поднимает это исключение прямо в тесте, см. `tests/conftest.py`)."""
        contract = _contract_with_standard(
            factories, unit_cost_total=Decimal(bad), vat_rate=Decimal("20"),
            standard=Decimal("100"),
        )
        db_session.commit()

        body = _passport(client, contract.id)

        rate = body["key_rates"][0]
        assert rate["deviation_pct"] is None
        assert rate["deviation_reason"] == "not_finite"
        # Причина обязана остаться ЧЕСТНОЙ, а не превратиться молча в
        # «нет норматива» (§10) — норматив у этой позиции есть (100).
        assert rate["standard_unit_rate"] is not None
        # `over_standard` не должен посчитать нефинитную строку превышением
        # (и не должен упасть, воспроизводя дефект 1).
        assert body["totals"]["over_standard"] == 0
        assert body["totals"]["with_standard"] == 1


class TestMatrixCellDrillDownSurvivesNonFiniteCost:
    """`get_matrix_cell`/`_cell_item` — ВТОРОЙ живой потребитель `_net_deviation`
    (drill-down матрицы, реально вызывается фронтендом, `MatrixCellDialog.tsx`,
    в отличие от паспорта фазы 6). Чинится той же правкой `_net_deviation`."""

    def test_nan_cost_reports_the_reason_instead_of_crashing(self, client, factories, db_session):
        contract = _contract_with_standard(
            factories, unit_cost_total=Decimal("NaN"), vat_rate=Decimal("20"),
            standard=Decimal("100"),
        )
        db_session.commit()
        body = client.get(
            "/api/v1/analytics/matrix/cell",
            params={
                "contract_id": contract.id,
                "catalog_position_id": contract.catalog_position_id,
            },
        ).json()
        item = body["items"][0]
        assert item["deviation_pct"] is None
        assert item["deviation_reason"] == "not_finite"


# ---------------------------------------------------------------------------
#  Дефект 1, ТРЕТИЙ экземпляр (ре-ревью Codex, PR #21, круг 3): найден и
#  воспроизведён оркестратором на `_fold_cell` (ячейка матрицы) ПОСЛЕ починки
#  `_net_deviation`. Это не «то же сомнение», а третья поверхность того же
#  класса: `SUM`/деление в `_fold_cell` тихо распространяют `NaN`/`Infinity`
#  из `weighted_cost` на средневзвешенную ставку ЯЧЕЙКИ, и утечка серьёзнее,
#  чем в паспорте/drill-down — под угрозой ДВА денежных поля ответа сразу
#  (`rate` И `amount`), а не только `deviation_pct`, и матрица — поверхность,
#  которую UI реально рисует.
# ---------------------------------------------------------------------------

class TestFoldCellNotFinite:
    """Прямой вызов `_fold_cell` — воспроизведение оркестратора буквально:
    `_fold_cell([group(vat_rate_base=20, weighted_cost=NaN, weight_total=2,
    standard_unit_rate=100)])`."""

    def _group(self, **kwargs):
        base = {
            "vat_rate_base": Decimal("20"),
            "weighted_cost": Decimal("200"),
            "weight_total": Decimal("2"),
            "standard_unit_rate": Decimal("100"),
        }
        base.update(kwargs)
        return SimpleNamespace(**base)

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_weighted_cost_hides_rate_and_amount_with_honest_reason(self, bad):
        """Краснеет от текущего (до правки круга 3) кода: `rate`/`amount`
        возвращались буквальным `Decimal('NaN')`/`Decimal('Infinity')` — число
        под видом числа, но не число, — а `deviation_reason` оставался
        `None` (== «норматив есть, отклонение известно»), хотя отклонение
        как раз НЕ известно."""
        result = _fold_cell([self._group(weighted_cost=Decimal(bad))])
        assert result["rate"] is None
        assert result["amount"] is None
        assert result["deviation_pct"] is None
        assert result["deviation_reason"] == "not_finite"
        # Норматив не гасится — та же логика, что у unknown_vat_base/no_weight:
        # он от НДС не зависит и остаётся нетто по определению.
        assert result["standard_unit_rate"] == Decimal("100")

    def test_finite_case_is_unaffected(self):
        """Контроль: обычный конечный случай не должен пострадать от починки."""
        result = _fold_cell([self._group()])
        assert result["rate"] == Decimal("83.33")  # gross_to_net(200,20)/2
        assert result["amount"] == Decimal("166.67")
        assert result["deviation_reason"] is None


class TestMatrixCellFoldSurvivesNonFiniteCost:
    """Воспроизведение ЧЕРЕЗ САМ ЭНДПОИНТ матрицы (не только прямым вызовом
    `_fold_cell`) — тот же принцип, что и у паспорта дефекта 1: не верить
    одному способу репродукции."""

    def test_nan_cost_hides_rate_and_amount_instead_of_leaking_nan(
        self, client, factories, db_session
    ):
        """До починки: ячейка матрицы отдавала бы буквальный `"NaN"` в
        `rate`/`amount`, подписанный `deviation_reason=None` (== «нет
        причины, отклонение как есть») — числовой мусор под видом факта,
        да ещё и с честной на вид, но лживой причиной."""
        contract = _contract_with_standard(
            factories, unit_cost_total=Decimal("NaN"), vat_rate=Decimal("20"),
            standard=Decimal("100"),
        )
        db_session.commit()

        row = next(
            r for r in _matrix(client)["rows"] if r["catalog_position_id"] == contract.catalog_position_id
        )
        cell = _cell_of(row, contract.id)

        assert cell["rate"] is None
        assert cell["amount"] is None
        assert cell["deviation_pct"] is None
        assert cell["deviation_reason"] == "not_finite"
        # Норматив виден — та же граница, что у "unknown_vat_base"/"no_weight".
        assert cell["standard_unit_rate"] is not None


class TestMatrixCellDrillDownNetAxis:
    def test_matrix_cell_drilldown_survives_the_migration(self, client, factories, db_session):
        """Ячейка и её drill-down обязаны сходиться: иначе человек, проверяя цифру,
        складывает не то, что сложила система."""
        contract = _contract_with_standard(
            factories, unit_cost_total=Decimal("120"), weight=Decimal("2"),
            vat_rate=Decimal("20"), standard=Decimal("100"),
        )
        db_session.commit()
        body = client.get(
            "/api/v1/analytics/matrix/cell",
            params={
                "contract_id": contract.id,
                "catalog_position_id": contract.catalog_position_id,
            },
        ).json()

        item = body["items"][0]
        assert Decimal(item["unit_cost_total"]) == Decimal("120")  # валовое не тронуто
        assert Decimal(item["unit_cost_net"]) == Decimal("100.00")
        assert Decimal(item["vat_rate_base"]) == Decimal("20")
        assert Decimal(item["deviation_pct"]) == Decimal("0")
        assert item["deviation_reason"] is None

    def test_matrix_cell_drilldown_without_base_reports_the_reason(
        self, client, factories, db_session
    ):
        contract = _contract_with_standard(
            factories, unit_cost_total=Decimal("120"), vat_rate=None, standard=Decimal("100")
        )
        db_session.commit()
        item = client.get(
            "/api/v1/analytics/matrix/cell",
            params={
                "contract_id": contract.id,
                "catalog_position_id": contract.catalog_position_id,
            },
        ).json()["items"][0]
        assert item["unit_cost_net"] is None
        assert item["deviation_pct"] is None
        assert item["deviation_reason"] == "unknown_vat_base"


# ---------------------------------------------------------------------------
#  Задача 8 (ревью, Правка 2): паспорт объекта в ставке показа (спека §5.1)
# ---------------------------------------------------------------------------
#
# Матрица И её drill-down (`_cell_item`, `get_matrix_cell`) остаются на нетто-
# оси — они много-договорные (задача 3), и это НЕ трогается здесь (см. тесты
# класса выше: `unit_cost_total` там по-прежнему "валовое не тронуто"). Только
# паспорт ОБЪЕКТА (`get_passport`, однодоговорная поверхность) переходит на
# ставку показа — ровно то, что было пропущено в первом круге реализации
# задачи 8 без движущего теста, и это найдено внешним ревью.

def _set_vat_target(db_session, contract, rate):
    from models import Estimate

    estimate = db_session.query(Estimate).filter_by(contract_id=contract.id).one()
    estimate.vat_rate_target = rate
    db_session.flush()
    return estimate


class TestPassportDisplayRate:
    def test_standard_and_fact_follow_the_target_rate(self, client, factories, db_session):
        """Цель показа (16 %) приведена к базе предложения (20 %): факт 120
        (база 20 %) даёт нетто 100, а 100 нетто по цели 16 % даёт 116.00 —
        И факт, И норматив показаны в ОДНОЙ ставке.

        Краснеет от: `get_passport`, приводящей к ставке показа факт БЕЗ
        норматива или наоборот (тогда числа разошлись бы) — подтверждено
        мутацией: см. отчёт задачи."""
        contract = _contract_with_standard(
            factories, unit_cost_total=Decimal("120"), vat_rate=Decimal("20"),
            standard=Decimal("100"),
        )
        _set_vat_target(db_session, contract, Decimal("16"))
        db_session.commit()

        rate = _passport(client, contract.id)["key_rates"][0]
        assert Decimal(rate["unit_cost_total"]) == Decimal("116.00")
        assert Decimal(rate["total_cost_total"]) == Decimal("116.00")
        assert Decimal(rate["standard_unit_rate"]) == Decimal("116.00")

    def test_standard_and_fact_are_untouched_without_target(self, client, factories, db_session):
        """Парный тест тождества — ре-ревью задачи 8, круг 3: ПЕРЕПИСАН так,
        чтобы позиция ИМЕЛА норматив (круг 2 заводил тест БЕЗ норматива и тем
        самым обходил дефект — гейт был поднят ОДНИМ флагом на строку целиком
        от одного лишь показа норматива, а тест этот путь ни разу не проходил;
        находка внешнего ре-ревью).

        Без цели эффективная ставка равна базе предложения (20 %,
        единогласной): факт остаётся посимвольно тем же валовым значением,
        что и до задачи 8 (сравнение через СЫРУЮ строку JSON, а не
        `Decimal(...)==Decimal(...)` — то пропустило бы сдвиг `exponent`).
        Норматив (нетто 100) ПРИ ЭТОМ ВСЁ РАВНО приводится к ставке показа
        (100 нетто по базе/цели 20 % даёт 120.00) — у нормы нет ветки
        тождества, у факта — есть; это и есть гейт «по полю».

        Краснеет от (круг 2): подъёма ОДНОГО флага `key_rates_restated_any`
        сразу от показа норматива И квантования им же факта — тогда
        `unit_cost_total`/`total_cost_total` стали бы "120.50", а не "120.5"
        — подтверждено мутацией: см. отчёт задачи."""
        contract, _estimate, proposal = _estimate_with(factories, vat_rate=Decimal("20"))
        position = factories.CatalogPositionFactory.create(standard_job_title="Тождество Ф6, с нормативом")
        _position(factories, proposal, position, unit_cost="120.5", weight="1")
        factories.RateStandardFactory.create(
            catalog_position=position,
            rate_class=contract.rate_class,
            standard_unit_rate=Decimal("100"),
            valid_from=dt.date(2025, 1, 1),
        )
        db_session.commit()

        raw = client.get(f"/api/v1/analytics/passport/{contract.id}").text
        assert '"unit_cost_total":"120.5"' in raw.replace(", ", ",")
        assert '"total_cost_total":"120.5"' in raw.replace(", ", ",")

        rate = _passport(client, contract.id)["key_rates"][0]
        assert Decimal(rate["standard_unit_rate"]) == Decimal("120.00")

    def test_priced_amount_follows_the_target_rate_too(self, client, factories, db_session):
        """`totals.priced_amount` — та же ставка показа, что и `key_rates`
        (ревью задачи 8, Правка 2): раньше это была сырая `SUM(total_cost_
        total)` без отношения к базе НДС.

        Краснеет от: `_passport_totals`, суммирующей `PositionItem.total_
        cost_total` СЫРЫМ SQL-`SUM` вместо построчного `restate_gross`
        (тогда `priced_amount` остался бы "120", а не "116.00") —
        подтверждено мутацией: см. отчёт задачи."""
        contract = _contract_with_standard(
            factories, unit_cost_total=Decimal("120"), vat_rate=Decimal("20"),
            standard=Decimal("100"),
        )
        _set_vat_target(db_session, contract, Decimal("16"))
        db_session.commit()

        totals = _passport(client, contract.id)["totals"]
        assert Decimal(totals["priced_amount"]) == Decimal("116.00")

    def test_standard_is_shown_as_net_when_this_row_has_no_vat_base(
        self, client, factories, db_session
    ):
        """Ре-ревью задачи 8, круг 3, Правка 2 — парный к матричному тесту
        `test_matrix_cell_keeps_standard_unit_rate_without_vat_base` (спека
        §2.5, строка 293, дословно: «норматив при неизвестной базе
        показывается как нетто; не вычисляется только отклонение»).

        НЕИЗВЕСТНАЯ база (`vat_rate=None`) — не то же самое, что РАЗНОГЛАСИЕ
        заявленных ставок предложений (§5.1): при неизвестности гасить
        норматив нельзя, у него всё ещё есть значение, просто не с чем
        сравнить деньгами (`deviation_reason`); гасить его молча значило бы
        стереть эту разницу.

        Круг 2 схлопнул оба случая в одну ветку `effective_display_rate is
        None -> норматив None`, поскольку функция сама не различает
        «неизвестность» и «разногласие» (обе дают `None`) — round 3 развёл их
        на стороне вызывающего кода по признаку «база ЭТОЙ строки известна
        (`r.vat_rate_base is not None`) или нет», не трогая саму
        `effective_display_rate`.

        Краснеет от: `standard_value = _standard_in_display_rate(r.standard_
        unit_rate, effective_rate)` БЕЗ ветки `if r.vat_rate_base is None`
        (тогда `effective_rate is None` из-за неизвестной ставки погасил бы
        норматив так же, как разногласие) — подтверждено мутацией: см. отчёт
        задачи."""
        contract = _contract_with_standard(
            factories, unit_cost_total=Decimal("120"), vat_rate=None, standard=Decimal("100")
        )
        db_session.commit()

        rate = _passport(client, contract.id)["key_rates"][0]
        assert Decimal(rate["standard_unit_rate"]) == Decimal("100")
        assert rate["deviation_pct"] is None
        assert rate["deviation_reason"] == "unknown_vat_base"

    def test_standard_is_null_when_proposals_disagree_on_the_rate(
        self, client, factories, db_session
    ):
        """Ре-ревью задачи 8, круг 4, Правка 1 — парный к соседнему тесту
        выше (`test_standard_is_shown_as_net_when_this_row_has_no_vat_base`):
        та сторона стережёт «не гасить при неизвестной базе», эта — «гасить
        при настоящем разногласии» (§5.1). Круг 3 развёл обе ветки в коде, но
        застраховал тестом только ОДНУ сторону — ре-ревьюер сломал ветку
        разногласия (`crud/analytics.py:275-277`, вернул норматив вместо
        гашения) и прогнал ВЕСЬ файл + `test_project_passport_api.py` — 129
        зелёных, ни одного красного: стража не было вовсе.

        ДВА предложения с РАЗНЫМИ заявленными ставками (20 % и 12 %) на ОДНУ
        и ту же работу — базы ОБЕИХ строк ИЗВЕСТНЫ (в отличие от соседнего
        теста), но единой ставки показа нет: `effective_display_rate`
        отдаёт `None` по разногласию, а не по незнанию. Норматив обязан
        погаснуть на КАЖДОЙ из двух строк.

        Краснеет от: возврата норматива вместо `None` в ветке `else`
        (`r.vat_rate_base is not None`) при разногласии — подтверждено
        мутацией: см. отчёт задачи."""
        contract, _estimate, position = _priced_estimate_with_two_proposals(
            factories, unit_cost_total=Decimal("120"), vat_rates=[Decimal("20"), Decimal("12")]
        )
        factories.RateStandardFactory.create(
            catalog_position=position,
            rate_class=contract.rate_class,
            standard_unit_rate=Decimal("100"),
            valid_from=dt.date(2025, 1, 1),
        )
        db_session.commit()

        key_rates = _passport(client, contract.id)["key_rates"]
        assert len(key_rates) == 2
        assert all(rate["standard_unit_rate"] is None for rate in key_rates)
