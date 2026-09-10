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
from sqlalchemy.dialects import postgresql

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

    def test_work_priced_only_at_zero_weight_gives_empty_cell_not_division_by_zero(
        self, client, factories
    ):
        """§6: «если входящих строк нет — ставка пустая». Именно это и защищает
        `_weight_ok` (было `w > 0`) внутри `_cell_groups_cte`.

        **До задачи 2 плана правила цены** отсутствие условия давало бы деление
        на ноль (`SUM(w) = 0`); теперь работа с одной позицией нулевого веса
        присутствует в матрице (спека §2.4 — присутствие не зависит от цены), но
        ставка у её единственной ячейки пуста: цена пригодна (900 > 0), вес —
        нет, `rate_reason == "no_weight"` (спека §2.5, Правило 2). Первая
        редакция этого теста проверяла ставку `100` на смеси весов 10 и 0, что
        не доказывало ничего — строка с нулевым весом не влияет ни на
        числитель, ни на знаменатель (`(100·10 + 900·0) / (10 + 0) = 100` и с
        условием, и без него); здесь работа заведомо ОДНА такая строка, без
        соседней с пригодным весом.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(factories, proposal, position, unit_cost="900", weight="0", total="0")

        row = _matrix(client)["rows"][0]
        assert row["row_amount"] is None
        assert row["row_amount_incomplete"] is False
        cell = _cell_of(row, contract.id)
        assert cell["rate"] is None
        assert cell["rate_reason"] == "no_weight"
        assert cell["deviation_reason"] == "no_rate"

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

    def test_position_without_any_quantity_gives_empty_cell_not_a_missing_row(
        self, client, factories
    ):
        """`w = COALESCE(suggested_quantity, quantity)`; оба NULL → строка не входит.

        Проверяется отдельно от нулевого веса (`is_weight(None)` и `is_weight(0)` —
        два разных входа одного и того же предиката, `money/price.py` задачи 1):
        если условие когда-нибудь напишут как `w <> 0`, эта позиция вернулась бы в
        расчёт и уронила деление. С задачи 2 плана правила цены работа при этом
        ПРИСУТСТВУЕТ в матрице (спека §2.4) — цена пригодна (100 > 0), веса нет,
        `rate_reason == "no_weight"`, а не отсутствие строки вовсе.
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

        row = _matrix(client)["rows"][0]
        cell = _cell_of(row, contract.id)
        assert cell["rate"] is None
        assert cell["rate_reason"] == "no_weight"

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

    def test_chapter_row_is_not_a_matrix_row(self, client, factories):
        """Правка G ревью (мутант M14): `PositionItem.is_chapter.is_(False)`
        внутри `_presence_conditions` (сторона присутствия, задача 2 плана
        правила цены) — раздел присутствует в смете, но строкой матрицы или
        ячейкой быть не имеет права. `kind='POSITION'` в этом входе не
        нарушен (строка сматчена с обычной каталожной работой), поэтому
        падение теста доказывает именно отсутствующий фильтр по `is_chapter`,
        а не соседний, живой фильтр по `kind`.
        """
        _contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=position,
            is_chapter=True,
            unit_cost_total=Decimal("100"),
            suggested_quantity=Decimal("10"),
            total_cost_total=Decimal("1000"),
        )

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
#  Третья причина пустоты — нет пригодной цены (задача 5 плана правила цены,
#  спека §1.8, §2.9)
# ---------------------------------------------------------------------------

class TestUnmatchedReasonsIncludeWithoutPrice:
    """`positions_without_price` — третья причина, рядом с `positions_pending_
    review` и `positions_non_work` (спека §2.9).

    До этой правки обе старые причины фильтровали только `unit_cost_total IS NOT
    NULL`: ноль, отрицательная и нефинитная цена читались как «цена есть», и
    причина «нет цены» не срабатывала НИ РАЗУ (§1.8 спеки), хотя на стенде цены
    нет у 16,5 % позиций. Теперь предикат цены (`_price_ok`) делит все три причины
    ПЕРВЫМ: у позиции либо есть пригодная цена (тогда её судьбу решает состояние
    каталожной строки), либо нет (тогда причина — эта, независимо от каталожной
    строки).
    """

    def _priced_work_count(self, factories, estimate_id: int) -> int:
        """Четвёртая корзина («с ценой и работа») — независимый оракул из БД.

        Не переиспользует `_price_ok`/`is_price` производственного кода: расчёт
        сделан своей, отдельной проверкой (`price is not None and price.is_
        finite() and price > 0`) над сырыми колонками — иначе тест доказывал бы
        только согласие кода с самим собой, а не с правилом
        (`docs/insights/claimed-property-needs-its-own-input.md`). Равенство
        «три причины ответа + эта корзина = позиции сметы без разделов» проверяет
        разбиение целиком, а не согласие счётчиков между собой (спека §2.9).
        """
        from models import CatalogPosition, Lot, PositionItem, Proposal

        session = factories._session_holder["session"]
        rows = session.execute(
            sa.select(PositionItem.unit_cost_total, CatalogPosition.kind)
            .select_from(PositionItem)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .outerjoin(CatalogPosition, CatalogPosition.id == PositionItem.catalog_position_id)
            .where(Lot.estimate_id == estimate_id, PositionItem.is_chapter.is_(False))
        ).all()
        count = 0
        for price, kind in rows:
            if kind != CatalogKind.POSITION.value:
                continue
            if price is None or not price.is_finite() or price <= 0:
                continue
            count += 1
        return count

    def test_all_three_unmatched_reasons_partition_non_chapter_positions(self, client, factories):
        """Разбиение проверяется РАВЕНСТВОМ (спека §2.9), не согласием счётчиков
        — и не ТОЛЬКО равенством: у равенства есть слепое пятно (переход позиции
        между двумя счётчиками ВНУТРИ суммы его не портит), поэтому ниже отдельно
        сверены и сами значения, а не только их сумма — с обеих поверхностей,
        паспорта и матрицы (иначе для матрицы разбиение проверялось бы одним
        нечувствительным равенством, а не значениями).

        Восемь позиций без раздела, каждая — свой вход: расценённая работа
        (корзина 4, вне ответа), расценённая-но-ждущая-матчинга
        (`positions_pending_review`), расценённая-но-размеченная-не-работой
        (`positions_non_work`), и ПЯТЬ разных состояний «без пригодной цены» —
        по одному на каждую конкурирующую каталожную строку из обоих старых
        условий (`cp.id IS NULL`, `TO_REVIEW`, `HEADER`/`TRASH`/`LOT_HEADER` —
        здесь `TRASH`) плюс `POSITION`, то есть строку-РАБОТУ без изъятия. Все
        пять обязаны попасть в ОДНУ причину — `positions_without_price` —
        независимо от состояния каталожной строки.

        Раздел заведён ОТДЕЛЬНО, БЕЗ пригодной цены — а не с ценой 500, как в
        прежней редакции теста: раздел с валидной ценой никогда не доходит до
        проверки `is_chapter` внутри `_without_price_condition` (у него и так
        цена пригодна, условие `NOT _price_ok(...)` на нём ложно само по себе),
        и подмена `is_chapter.is_(False)` на `sa.true()` там раньше НЕ роняла
        ничего — ни под этой командой, ни на полном наборе (находка ревью).
        Раздел без цены — единственный вход, который эту подмену ловит: без
        фильтра `is_chapter` он попал бы в `positions_without_price` наравне с
        обычной позицией, хотя разделы не должны входить ни в числитель, ни в
        знаменатель вовсе.

        Мутация, убирающая `_price_ok` из любого из трёх условий, роняет именно
        это равенство: снятая позиция задваивается (считается и в старой
        причине, и в `positions_without_price`), и сумма перестаёт совпадать со
        знаменателем.
        """
        contract, estimate, proposal = _estimate_with(factories)

        # Раздел БЕЗ пригодной цены — не считается вовсе, ни числителем, ни
        # знаменателем, и это единственный вход, ловящий подмену `is_chapter`
        # внутри `_without_price_condition` (см. докстроку выше).
        factories.PositionItemFactory.create(
            proposal=proposal,
            is_chapter=True,
            catalog_position=None,
            unit_cost_total=None,
            total_cost_total=None,
        )

        priced_work = factories.CatalogPositionFactory.create(kind=CatalogKind.POSITION.value)
        _position(factories, proposal, priced_work, unit_cost="100", weight="10")

        pending = factories.CatalogPositionFactory.create(kind=CatalogKind.TO_REVIEW.value)
        _position(factories, proposal, pending, unit_cost="100", weight="10")

        marked = factories.CatalogPositionFactory.create(kind=CatalogKind.HEADER.value)
        _position(factories, proposal, marked, unit_cost="100", weight="10")

        # Без цены и вовсе не сматчена (`cp.id IS NULL` — конкурент №1 pending_review).
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=None,
            unit_cost_total=None,
            suggested_quantity=Decimal("10"),
            total_cost_total=None,
        )
        # Без цены (нулевая), каталожная строка ждёт матчинга (`TO_REVIEW` —
        # конкурент №2 pending_review; недостающая до правки ревью пара).
        pending_no_price = factories.CatalogPositionFactory.create(kind=CatalogKind.TO_REVIEW.value)
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=pending_no_price,
            unit_cost_total=Decimal("0"),
            suggested_quantity=Decimal("10"),
            total_cost_total=Decimal("0"),
        )
        # Без цены (тоже нулевая) и снова вовсе не сматчена — второй вход на
        # `cp.id IS NULL`, но с ценой НОЛЬ, не пустой: именно на нём старое и
        # новое разбиение расходятся по-настоящему (см. докстроку
        # `test_unmatched_position_without_price_counts_once_as_without_price`).
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=None,
            unit_cost_total=Decimal("0"),
            suggested_quantity=Decimal("10"),
            total_cost_total=Decimal("0"),
        )
        # Без цены (отрицательная), но каталожная строка размечена как РАБОТА —
        # предикат цены обязан сработать раньше состояния каталожной строки.
        negative_price_work = factories.CatalogPositionFactory.create(kind=CatalogKind.POSITION.value)
        _position(factories, proposal, negative_price_work, unit_cost="-5", weight="10")
        # Без цены (нефинитная), каталожная строка уже разобрана как не-работа
        # (`TRASH` — конкурент из `non_work`).
        nonfinite_price_marked = factories.CatalogPositionFactory.create(kind=CatalogKind.TRASH.value)
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=nonfinite_price_marked,
            unit_cost_total=Decimal("NaN"),
            suggested_quantity=Decimal("10"),
            total_cost_total=Decimal("NaN"),
        )

        totals = _passport(client, contract.id)["totals"]
        matrix = _matrix(client)
        priced_work_count = self._priced_work_count(factories, estimate.id)

        assert priced_work_count == 1
        total_non_chapter = 8
        for source in (totals, matrix):
            assert (
                source["positions_pending_review"]
                + source["positions_non_work"]
                + source["positions_without_price"]
                + priced_work_count
            ) == total_non_chapter

        # Значения по отдельности, а не только их сумма — равенство выше не
        # чувствительно к переносу позиции МЕЖДУ pending_review и non_work
        # (сумма трёх осталась бы той же), и без этих проверок такой перенос
        # прошёл бы незамеченным на ОБЕИХ поверхностях.
        assert totals["positions_pending_review"] == 1
        assert totals["positions_non_work"] == 1
        assert totals["positions_without_price"] == 5
        assert matrix["positions_pending_review"] == 1
        assert matrix["positions_non_work"] == 1
        assert matrix["positions_without_price"] == 5

    @pytest.mark.parametrize("price", [Decimal("0"), Decimal("-5"), Decimal("NaN")])
    def test_position_without_price_is_never_counted_as_pending_review(self, client, factories, price):
        """Вход, который старое условие (`unit_cost_total IS NOT NULL`) читало
        неверно: ноль, отрицательная и нефинитная цена — все они «не `NULL`» и
        потому проходили старую проверку как «цена есть», хотя расценённой такая
        позиция не является.

        `NULL` сюда НАМЕРЕННО не включён: он не проходил `IS NOT NULL` и до этой
        правки, то есть на нём старое и новое поведение совпадают — возврат
        `_price_ok` к `isnot(None)` этот вход не поймал бы. Именно этот случай
        (позиция с пустой ценой в очереди Review) уже предъявлен
        `test_unpriced_position_is_not_counted_as_pending_review` выше, и
        дублировать его параметром означало бы завести мутационно немую копию.

        Это же и предъявление слова «расценённых»: позиция с нулевой ценой
        (некогда проходившая как priced, §1.8) больше не в очереди Review.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create(kind=CatalogKind.TO_REVIEW.value)
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=position,
            unit_cost_total=price,
            suggested_quantity=Decimal("10"),
            total_cost_total=price,
        )

        totals = _passport(client, contract.id)["totals"]
        assert totals["positions_pending_review"] == 0
        assert totals["positions_without_price"] == 1

    def test_unmatched_position_without_price_counts_once_as_without_price(self, client, factories):
        """Вход, на котором старое и новое разбиение ДЕЙСТВИТЕЛЬНО расходятся
        (спека §2.9) — цена НОЛЬ, не пустая: под старым условием (`unit_cost_
        total IS NOT NULL`) `0` проходит проверку («не `NULL`»), и позиция,
        одновременно вовсе не сматченная (`catalog_position_id IS NULL`),
        безусловно уходила в `positions_pending_review` (см. `test_
        unmatched_position_is_pending_review` выше — та же форма входа, но с
        пригодной ценой, и счётчик — тот же, старый). Под новым условием она
        целиком уходит в `positions_without_price` и считается там РОВНО ОДИН
        РАЗ, а не в обоих счётчиках.

        Цена `None` здесь не годится: `NULL` не проходил старую проверку и
        раньше — на нём старое и новое поведение совпадает (позиция была не
        посчитана НИГДЕ), и мутация «вернуть `_price_ok` к `isnot(None)`» такой
        вход не поймает (проверено прогоном мутации при ревью задачи).
        """
        contract, _estimate, proposal = _estimate_with(factories)
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=None,
            unit_cost_total=Decimal("0"),
            suggested_quantity=Decimal("10"),
            total_cost_total=Decimal("0"),
        )

        totals = _passport(client, contract.id)["totals"]
        assert totals["positions_without_price"] == 1
        assert totals["positions_pending_review"] == 0
        assert totals["positions_non_work"] == 0

    @pytest.mark.parametrize(
        "kind",
        [CatalogKind.HEADER.value, CatalogKind.TRASH.value, CatalogKind.LOT_HEADER.value],
    )
    def test_marked_row_without_price_counts_as_without_price_not_non_work(self, client, factories, kind):
        """Та же правка, что у `_pending_review_condition`, на другой причине:
        разобранная не-работа (`HEADER`/`TRASH`/`LOT_HEADER`) с нулевой ценой —
        до этой правки `unit_cost_total IS NOT NULL` пропускал ноль, и такая
        позиция ошибочно попадала в `positions_non_work`, хотя настоящая причина
        пустоты — отсутствие цены, а не разметка каталожной строки.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create(kind=kind)
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=position,
            unit_cost_total=Decimal("0"),
            suggested_quantity=Decimal("10"),
            total_cost_total=Decimal("0"),
        )

        totals = _passport(client, contract.id)["totals"]
        assert totals["positions_non_work"] == 0
        assert totals["positions_without_price"] == 1

    def test_all_unpriced_positions_report_positions_without_price(self, client, factories):
        """Сегодняшнее «причина `no_price` не срабатывает никогда» (§1.8 спеки)
        больше не выполняется: смета, где ВСЕ позиции без цены, а каталожные
        строки размечены как работы (`kind='POSITION'`) — то есть VIEW и раньше,
        и сейчас их не видел бы, но причина этого не была названа никогда, — даёт
        непустой `positions_without_price` и на паспорте, и на матрице.
        """
        contract, _estimate, proposal = _estimate_with(factories)
        for _ in range(3):
            position = factories.CatalogPositionFactory.create(kind=CatalogKind.POSITION.value)
            factories.PositionItemFactory.create(
                proposal=proposal,
                catalog_position=position,
                unit_cost_total=None,
                suggested_quantity=Decimal("10"),
                total_cost_total=None,
            )

        totals = _passport(client, contract.id)["totals"]
        assert totals["positions_without_price"] == 3
        assert totals["positions_pending_review"] == 0
        assert totals["positions_non_work"] == 0
        assert _matrix(client)["positions_without_price"] == 3


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
        """Носитель неизвестной базы НДС — `rate_reason` ячейки, а не
        `deviation_reason` (спека правила цены §2.5, §2.7 — ревизия §4:
        смена носителя относится ТОЛЬКО к агрегированной ячейке матрицы).
        `deviation_reason` при непустом `rate_reason` всегда `no_rate`."""
        _priced_estimate(factories, unit_cost_total=Decimal("120"), vat_rate=None)
        db_session.commit()
        cell = client.get("/api/v1/analytics/matrix").json()["rows"][0]["cells"][0]
        assert cell["rate"] is None
        assert cell["rate_reason"] == "unknown_vat_base"
        assert cell["deviation_reason"] == "no_rate"

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
        assert cell["rate_reason"] == "unknown_vat_base"
        assert cell["deviation_reason"] == "no_rate"
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
        assert hidden["rate_reason"] == "unknown_vat_base"
        assert hidden["deviation_reason"] == "no_rate"
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

        Правка F ревью: докстрока ниже раньше называла механизм `круга 3`
        (`cell_not_finite`/`_NOT_FINITE_COST` внутри `_cell_weights_cte`,
        VIEW-сторона) — задача 2 плана правила цены этот механизм УДАЛИЛА
        (он стал мёртвым: `_price_ok` не пускает NaN в `_cell_weights_cte`
        вовсе). Тест остаётся зелёным с НЕИЗМЕННЫМИ ассертами, но теперь по
        ДРУГОЙ причине: NaN-предложение договора C-2 не входит в ставку
        (`_price_ok`), становится ИСКЛЮЧЁННОЙ позицией стороны присутствия, и
        именно `excluded.incomplete` (`_excluded_positions_cte`) поднимает
        `row_amount_incomplete` — не `bool_or` по строкам VIEW. Подмена
        механизма при неизменном тексте теста была бы невидима, если бы эта
        докстрока не называла её прямо.
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

    def test_finite_proposal_survives_a_non_finite_sibling_in_the_same_cell(
        self, client, factories, db_session
    ):
        """Смена контракта задачей 2 плана правила цены (спека §2.5, набор
        «положительная + нефинитная»): раньше `_cell_groups_cte` не
        фильтровала цену, и NaN одного предложения заражала `SUM` ВСЕЙ
        группы вместе с финитным соседом — ячейка гасла ЦЕЛИКОМ (так был
        сформулирован этот тест до задачи 2, зеркально к `test_row_amount_
        excludes_partially_unknown_cell`). Теперь `_price_ok` отсекает NaN
        ДО группировки: финитная позиция считает ставку САМА, нефинитная
        лишь поднимает флаг неполноты со стороны присутствия — ячейка
        больше НЕ гаснет, и это тот же класс правки, что уже применён к
        `test_row_amount_excludes_a_non_finite_cell_and_flags_incompleteness`
        выше на разных договорах.
        """
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

        assert all(c["rate"] is not None for c in row["cells"])
        partial = next(c for c in row["cells"] if Decimal(c["rate"]) == Decimal("100"))
        assert partial["rate_reason"] is None
        assert Decimal(row["row_amount"]) == Decimal("300.00")
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
    способу репродукции.

    **Правка задачи 3 плана правила цены.** До этой задачи нефинитная позиция
    ПОПАДАЛА в `key_rates` с честной причиной `not_finite` — этот тест так и
    проверял. Теперь `_priced_positions_select` фильтрует СВОИМ предикатом
    (`_price_ok`), и нефинитная цена не проходит его вовсе: позиция пропадает
    из СОСТАВА `key_rates`, а не только гасит своё отклонение.

    **Правка задачи 4 плана правила цены (миграция 0016), решение
    оркестратора после ревью находки задачи 4.** `_passport_totals` считает
    «расценённые позиции сметы» ровно тем, что отдаёт VIEW `v_position_
    deviation_inputs` (см. её докстроку в `crud/analytics.py` — состав этого
    читателя НАМЕРЕННО задан VIEW-ом, второй предикат поверх него не заводится).
    До миграции 0016 VIEW отдавал позицию с нефинитной ценой (условие было
    только `IS NOT NULL`), и `_passport_totals` считала её «расценённой» —
    `positions_priced`/`with_standard` были равны 1, `priced_amount` был
    строкой `'NaN'`/`'Infinity'`/`'-Infinity'` (нефинитное значение, просочившееся
    в JSON-ответ). Это ровно тот же дефект счётчика, ради починки которого
    затеяна вся фича — «расценена» не должно означать «есть хоть какое-то
    значение», а «есть пригодная цена». После миграции такая позиция больше не
    расценена: `positions_priced`/`with_standard` — 0, `priced_amount` — `None`
    (позиций для суммирования нет вовсе, а не `None` от несработавшей ветки).
    `without_standard` (`positions_priced - with_standard`) не меняется — он
    и до, и после равен 0 (1-1 и 0-0 соответственно), поэтому им нельзя было
    бы отличить старое поведение от нового, и он не по этой причине оставлен
    в тесте.

    Смысл проверки эндпоинта НЕ меняется ни на йоту: он по-прежнему обязан
    ответить 200, а не 500 (защита от `InvalidOperation` в `_passport_totals`/
    `_net_deviation` не отменяется, эта задача её не трогает; после миграции
    она просто не имеет случая себя проявить на ЭТОЙ конкретной позиции, потому
    что VIEW уже не отдаёт нефинитную строку, но сама защита в коде осталась и
    продолжает стеречь остальные конечные входы этого файла)."""

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_cost_is_not_counted_as_priced(
        self, client, factories, db_session, bad
    ):
        """Эндпоинт по-прежнему не падает 500-й на нефинитной цене (дефект 1) —
        это утверждение НЕ снимается ни в каком виде. Но нефинитная позиция
        больше не «расценена»: `positions_priced`/`with_standard` — 0 (были 1
        до миграции 0016), `priced_amount` — `None` (был нефинитной строкой),
        `over_standard` остаётся 0 (не менялся ни до, ни после — превышения
        считать не с чего в обоих случаях)."""
        contract = _contract_with_standard(
            factories, unit_cost_total=Decimal(bad), vat_rate=Decimal("20"),
            standard=Decimal("100"),
        )
        db_session.commit()

        body = _passport(client, contract.id)

        # Единственная позиция сметы нефинитна — состав топа пуст, как и раньше.
        assert body["key_rates"] == []
        assert body["totals"]["positions_priced"] == 0
        assert body["totals"]["with_standard"] == 0
        assert body["totals"]["priced_amount"] is None
        assert body["totals"]["over_standard"] == 0


class TestPassportKeyRatesPricePredicate:
    """`_priced_positions_select` фильтрует СВОИМ предикатом (`_price_ok`,
    задача 3 плана правила цены), а не полагается на фильтр VIEW.

    **История утверждения (важно для честности «до/после», задача 4).** Когда
    эти тесты писались (задача 3), `v_position_deviation_inputs` ещё отсеивал
    только `unit_cost_total IS NOT NULL` (миграция 0012): ноль, отрицательное
    и `NaN` доезжали до него как цена (спека §1.1/§1.2). Тесты этого класса
    были зелёными уже ТОГДА — значит исключение делала `_priced_positions_
    select`, а не VIEW. **Миграция 0016 (задача 4) с тех пор сузила саму
    VIEW тем же предикатом**, и тесты этого класса перепрогнаны ПОСЛЕ неё
    без единой правки тела — остались зелёными с теми же числами. Это и есть
    эмпирическая половина доказательства «поведение читателя не изменилось от
    сужения VIEW» (структурная половина — независимость `_priced_positions_
    select` от текста VIEW, доказанная выше при её отсутствии). Применения
    `_price_ok` здесь сегодня ИНЕРТНЫ по отношению к уже суженной VIEW (см.
    докстроку `_price_ok`, `crud/analytics.py`) — держатся не ради наблюдаемой
    сейчас защиты, а ради независимости от текста VIEW, который эта задача
    больше не единственная вправе менять.

    **Посимвольный паритет на смете со всеми пригодными ценами** (правка
    ревью, круг 1, правка 5) доказывает НЕ новый тест здесь, а нетронутый
    `TestPassportKeyRates` (выше в этом файле): все его тесты используют
    ТОЛЬКО пригодные цены, ни один не правлен этой задачей и ни один не
    покраснел — значит `_price_ok` не отсекает на таких сметах ничего лишнего.
    Заводить здесь третью копию той же мысли (после `test_excludes_a_zero_
    price_position`/`test_excludes_a_negative_price_position`, которые уже
    доказывают обратное — что предикат ОТСЕКАЕТ негодное) дороже, чем
    сослаться на уже существующее зелёное.
    """

    def test_excludes_a_zero_price_position(self, client, factories):
        contract, _estimate, proposal = _estimate_with(factories)
        zero_priced = factories.CatalogPositionFactory.create(standard_job_title="Нулевая цена")
        valid = factories.CatalogPositionFactory.create(standard_job_title="Годная цена")
        _bare_position(factories, proposal, zero_priced, price=Decimal("0"), weight=Decimal("10"))
        _bare_position(factories, proposal, valid, price=Decimal("100"), weight=Decimal("10"))

        titles = [r["job_title"] for r in _passport(client, contract.id)["key_rates"]]
        assert titles == [_job_title_of(factories, valid)]

    def test_excludes_a_negative_price_position(self, client, factories):
        contract, _estimate, proposal = _estimate_with(factories)
        negative = factories.CatalogPositionFactory.create(standard_job_title="Отрицательная цена")
        valid = factories.CatalogPositionFactory.create(standard_job_title="Годная цена")
        _bare_position(factories, proposal, negative, price=Decimal("-50"), weight=Decimal("10"))
        _bare_position(factories, proposal, valid, price=Decimal("100"), weight=Decimal("10"))

        titles = [r["job_title"] for r in _passport(client, contract.id)["key_rates"]]
        assert titles == [_job_title_of(factories, valid)]

    def test_all_valid_prices_are_all_shown_with_their_raw_fields(self, client, factories):
        """НЕ доказательство паритета (см. докстроку класса, правка 5 ревью):
        только то, что предикат НЕ отсекает пригодные значения и не путает
        порядок/поля двух позиций с разными цифрами. Три поля из десяти —
        `job_title`, `unit_cost_total`, `total_cost_total` — намеренно, а не
        случайно неполно."""
        contract, _estimate, proposal = _estimate_with(factories)
        first = factories.CatalogPositionFactory.create(standard_job_title="Первая работа")
        second = factories.CatalogPositionFactory.create(standard_job_title="Вторая работа")
        _position(factories, proposal, first, unit_cost="150", weight="4", total="5000")
        _position(factories, proposal, second, unit_cost="80", weight="3", total="240")

        key_rates = _passport(client, contract.id)["key_rates"]
        assert [r["job_title"] for r in key_rates] == [
            _job_title_of(factories, first),
            _job_title_of(factories, second),
        ]
        assert Decimal(key_rates[0]["unit_cost_total"]) == Decimal("150")
        assert Decimal(key_rates[0]["total_cost_total"]) == Decimal("5000")
        assert Decimal(key_rates[1]["unit_cost_total"]) == Decimal("80")
        assert Decimal(key_rates[1]["total_cost_total"]) == Decimal("240")


class TestMatrixCellDrillDownSurvivesNonFiniteCost:
    """`get_matrix_cell`/`_cell_item` — ВТОРОЙ живой потребитель `_net_deviation`
    (drill-down матрицы, реально вызывается фронтендом, `MatrixCellDialog.tsx`,
    в отличие от паспорта фазы 6). Чинится той же правкой `_net_deviation`.

    **Правка задачи 3 плана правила цены.** Нефинитная позиция больше не
    ВХОДИТ в ставку ячейки (`is_price(NaN)` ложно), поэтому `_net_deviation`
    для неё больше не вызывается вовсе — причина её отсутствия в расчёте
    теперь `excluded_reason`, а не `deviation_reason` (решение «чего не
    считать для невошедшей строки», отчёт задачи 3)."""

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
        assert item["included"] is False
        assert item["excluded_reason"] == "not_finite"
        # Отклонение для невошедшей строки не вычисляется вовсе (решение «чего
        # не считать для невошедшей строки») — раньше `_net_deviation`
        # вызывался безусловно и `deviation_reason` был "not_finite"; теперь
        # это поле пусто, а причина невхождения названа отдельным полем.
        assert item["deviation_pct"] is None
        assert item["deviation_reason"] is None


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
        под видом числа, но не число. После смены осей (спека правила цены
        §2.5, §2.7) причина «ставка нефинитна» живёт в `rate_reason`, а
        `deviation_reason` при непустом `rate_reason` — всегда `no_rate`
        (сравнивать вовсе нечего, а не «сравнили и не нашли норматив»)."""
        result = _fold_cell([self._group(weighted_cost=Decimal(bad))])
        assert result["rate"] is None
        assert result["amount"] is None
        assert result["deviation_pct"] is None
        assert result["rate_reason"] == "not_finite"
        assert result["deviation_reason"] == "no_rate"
        # Норматив не гасится — та же логика, что у unknown_vat_base/no_weight:
        # он от НДС не зависит и остаётся нетто по определению.
        assert result["standard_unit_rate"] == Decimal("100")

    def test_finite_case_is_unaffected(self):
        """Контроль: обычный конечный случай не должен пострадать от починки."""
        result = _fold_cell([self._group()])
        assert result["rate"] == Decimal("83.33")  # gross_to_net(200,20)/2
        assert result["amount"] == Decimal("166.67")
        assert result["rate_reason"] is None
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
        assert cell["rate_reason"] == "not_finite"
        assert cell["deviation_reason"] == "no_rate"
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


class TestMatrixCellDrilldownIncludedFlag:
    """Признак вхождения строки в ставку ячейки на самой поверхности drill-down
    (задача 3 плана правила цены, спека §2.8). Носитель — `_all_positions_
    select`, а не `_priced_positions_select`: невошедшие ценой строки видны
    ВСЕГДА (решение о носителе, отчёт задачи 2 плана правила цены), а не
    только пока VIEW их не отсеял.

    Помощники `_cell_for_positions`/`_row_and_cell` определены НИЖЕ по файлу
    (задача 2) — тот же приём построчного входа, второй копии не заводится.
    """

    def _drilldown(self, client, contract, position):
        return client.get(
            "/api/v1/analytics/matrix/cell",
            params={"contract_id": contract.id, "catalog_position_id": position.id},
        ).json()

    def test_matrix_cell_drilldown_shows_all_rows_including_priceless(self, client, factories):
        """Ячейка `no_price` открывает НЕПУСТОЙ список строк — работа есть,
        цены нет, и утверждение экрана есть чем проверить (спека §2.8).

        Правка ревью, круг 1, правка 7: докстрока говорила про ячейку
        `no_price`, а сам `rate_reason` ячейки не читался ни разу — предпосылка
        была верна, но не утверждена. Здесь она проверяется явно, через ту же
        `/matrix`, что и `_row_and_cell`."""
        contract, position = _cell_for_positions(factories, [(None, Decimal("10"))])
        cell = _row_and_cell(client, contract, position)[1]
        assert cell["rate_reason"] == "no_price"

        body = self._drilldown(client, contract, position)
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["included"] is False
        assert item["excluded_reason"] == "no_price"

    def test_matrix_cell_drilldown_excluded_reason_negative(self, client, factories):
        contract, position = _cell_for_positions(factories, [(Decimal("-50"), Decimal("10"))])
        item = self._drilldown(client, contract, position)["items"][0]
        assert item["included"] is False
        assert item["excluded_reason"] == "negative"

    def test_matrix_cell_drilldown_excluded_reason_no_weight(self, client, factories):
        contract, position = _cell_for_positions(factories, [(Decimal("100"), Decimal("0"))])
        item = self._drilldown(client, contract, position)["items"][0]
        assert item["included"] is False
        assert item["excluded_reason"] == "no_weight"

    def test_matrix_cell_drilldown_excluded_reason_not_finite_weight(self, client, factories):
        """Второй операнд `or` в `_row_exclusion_reason` (вес нефинитен, а не
        цена) — отличает эту ветку от соседнего теста на нефинитную ЦЕНУ
        (`TestMatrixCellDrillDownSurvivesNonFiniteCost`): своим входом, как
        того требует `docs/insights/claimed-property-needs-its-own-input.md`
        — полнота предъявлений по ветвям не видит слабый предикат внутри уже
        предъявленной ветви, если для второго операнда `or` нет своего входа."""
        contract, position = _cell_for_positions(factories, [(Decimal("100"), Decimal("NaN"))])
        item = self._drilldown(client, contract, position)["items"][0]
        assert item["included"] is False
        assert item["excluded_reason"] == "not_finite"

    def test_matrix_cell_drilldown_excluded_reason_not_finite_wins_over_negative_price(
        self, client, factories
    ):
        """Приоритет `not_finite` над `negative` — РЕАЛЬНЫЙ и наблюдаемый
        (правка ревью, круг 1, правка 2), в отличие от порядка `no_weight`/
        `negative` (см. `_row_exclusion_reason`). `Decimal("-Infinity") < 0`
        не бросает и не отличается от обычного отрицательного числа — без
        проверки `not_finite` ПЕРВОЙ эта строка ушла бы в `negative`."""
        contract, position = _cell_for_positions(factories, [(Decimal("-Infinity"), Decimal("10"))])
        item = self._drilldown(client, contract, position)["items"][0]
        assert item["included"] is False
        assert item["excluded_reason"] == "not_finite"

    def test_matrix_cell_drilldown_excluded_reason_not_finite_wins_over_negative_price_with_nonfinite_weight(
        self, client, factories
    ):
        """Парный вход к тесту выше (правка ревью, круг 1, правка 2): цена
        отрицательна И конечна (`is_price`/`negative`-ветка формально
        применима), но ВЕС нефинитен — без проверки `not_finite` ПЕРВОЙ
        (до всякой проверки знака цены) строка ушла бы в `negative`, даже не
        заметив, что сам вклад невычислим."""
        contract, position = _cell_for_positions(factories, [(Decimal("-5"), Decimal("NaN"))])
        item = self._drilldown(client, contract, position)["items"][0]
        assert item["included"] is False
        assert item["excluded_reason"] == "not_finite"

    def test_matrix_cell_drilldown_included_row_has_empty_excluded_reason(self, client, factories):
        contract, position = _cell_for_positions(factories, [(Decimal("100"), Decimal("10"))])
        item = self._drilldown(client, contract, position)["items"][0]
        assert item["included"] is True
        assert item["excluded_reason"] is None

    def test_matrix_cell_drilldown_excluded_row_deviation_is_empty_not_a_second_meaning(
        self, client, factories
    ):
        """Пара «`included=false` и пустое `deviation_reason`» предъявлена
        ЯВНО (`docs/insights/one-value-two-states.md`): различитель —
        `included`, читаемое первым, а не значение `deviation_reason` само по
        себе — оно пусто здесь по ДРУГОЙ причине, чем у вошедшей строки без
        норматива (`test_matrix_cell_drilldown_without_base_reports_the_
        reason` выше). Контраст с ячейкой добавляет содержательное
        утверждение (правка ревью, круг 1, правка 8, взамен тавтологии
        `!= "no_rate"` сразу после `is None`): у ЯЧЕЙКИ той же пары (работа,
        договор) `deviation_reason` — как раз `"no_rate"` (`_cell_without_
        ingesting`), то есть один и тот же факт «сравнения не было» две
        поверхности называют РАЗНЫМИ значениями по замыслу, а не случайно."""
        contract, position = _cell_for_positions(factories, [(None, Decimal("10"))])
        cell = _row_and_cell(client, contract, position)[1]
        assert cell["deviation_reason"] == "no_rate"

        item = self._drilldown(client, contract, position)["items"][0]
        assert item["included"] is False
        assert item["deviation_pct"] is None
        assert item["deviation_reason"] is None

    def test_matrix_cell_drilldown_excluded_row_has_no_standard_rate(self, client, factories):
        """Блокер ревью, круг 1, правка 1: `_all_positions_select` подтягивает
        норматив/базу НДС LEFT JOIN-ом к `DEVIATION_INPUTS` — без `_price_ok`
        в ON этого JOIN-а невошедшая строка несла бы норматив/базу, которых
        не производила (замерено ревью: `standard_unit_rate="100"`,
        `vat_rate_base="0"` при `included=false`). Один вход, ОБЕ стороны:
        невошедшая (цена ноль) — пустые норматив и база, вошедшая (цена 100)
        — непустые."""
        contract, position = _cell_for_positions(
            factories,
            [(Decimal("0"), Decimal("10")), (Decimal("100"), Decimal("10"))],
            standard=Decimal("100"),
        )
        items = self._drilldown(client, contract, position)["items"]
        excluded = next(i for i in items if not i["included"])
        included = next(i for i in items if i["included"])
        assert excluded["standard_unit_rate"] is None
        assert excluded["vat_rate_base"] is None
        assert included["standard_unit_rate"] is not None
        assert included["vat_rate_base"] is not None

    def test_matrix_cell_drilldown_amount_and_rate_match_the_cell(self, client, factories):
        """Два ОТДЕЛЬНЫХ утверждения (спека §2.8): сумма НЕТТО-вкладов
        вошедших строк равна `amount` ячейки, и она же, делённая на сумму
        пригодных весов вошедших строк, равна `rate` — складывать вклады со
        ставкой размерностно нельзя, одно из другого не следует. Вход несёт
        ТРИ позиции с разными весами (не единица весом для всех — иначе
        деление на сумму весов совпало бы со средним арифметическим и не
        отличило бы верную формулу от ошибочной), из них одна ИСКЛЮЧЕНА
        отрицательной ценой — доказывает, что исключённая строка не
        просачивается во вклад.

        Ставка НДС — 20 %, НЕ ноль (правка ревью, круг 1, правка 6): при
        `vat_rate=0` (дефолт `_estimate_with`) `gross_to_net(x, 0) == x`, и
        валовое неотличимо от нетто на входе вовсе — суммировался бы
        `unit_cost_total`, а заявление говорит про `unit_cost_net`. Цены
        подобраны так, чтобы нетто (`gross_to_net(120,20)=100`,
        `gross_to_net(360,20)=300`) были точными без округления — иначе
        поштучное квантование `unit_cost_net` могло бы разойтись с суммой,
        квантованной ОДИН раз (тот же класс риска, что `test_sql_net_weight_
        agrees_with_python`)."""
        contract, _estimate, proposal = _estimate_with(factories, vat_rate=Decimal("20"))
        position = factories.CatalogPositionFactory.create()
        for price, weight in [
            (Decimal("120"), Decimal("2")),
            (Decimal("360"), Decimal("1")),
            (Decimal("-5"), Decimal("10")),
        ]:
            _bare_position(factories, proposal, position, price=price, weight=weight)

        cell = _row_and_cell(client, contract, position)[1]
        body = self._drilldown(client, contract, position)
        included_items = [i for i in body["items"] if i["included"]]
        assert len(included_items) == 2

        contributions = sum(
            Decimal(i["unit_cost_net"]) * Decimal(i["weight"]) for i in included_items
        )
        weights = sum(Decimal(i["weight"]) for i in included_items)
        assert quantize_money(contributions) == Decimal(cell["amount"])
        assert quantize_money(contributions / weights) == Decimal(cell["rate"])

    def test_matrix_cell_drilldown_is_scoped_to_the_requested_work(self, client, factories):
        """Условие «строка принадлежит ЗАПРОШЕННОЙ работе»
        (`PositionItem.catalog_position_id == catalog_position_id`) не
        предъявлялось ни одним существующим тестом drill-down: все они
        заводили ровно одну каталожную строку на смету, и её отсутствие
        осталось бы незамеченным (снятием подтверждено, см. отчёт задачи).
        Здесь — ДВЕ разные работы в одной смете, и запрос по одной не должен
        показать строку соседней."""
        contract, _estimate, proposal = _estimate_with(factories)
        requested = factories.CatalogPositionFactory.create(standard_job_title="Запрошенная работа")
        other = factories.CatalogPositionFactory.create(standard_job_title="Другая работа")
        _bare_position(factories, proposal, requested, price=Decimal("100"), weight=Decimal("10"))
        _bare_position(factories, proposal, other, price=Decimal("200"), weight=Decimal("5"))

        body = self._drilldown(client, contract, requested)
        assert len(body["items"]) == 1
        assert Decimal(body["items"][0]["unit_cost_total"]) == Decimal("100")

    def test_matrix_cell_drilldown_excludes_chapter_rows(self, client, factories):
        """Копия условия VIEW `is_chapter=false` внутри `_all_positions_
        select` (правка ревью, круг 1, правка 4) — названа в докстроке
        `_all_positions_select` и застрахована здесь: строка-раздел не
        обязана появляться в drill-down, даже если ей (вопреки обычному
        импорту) сопоставлена та же каталожная строка, что обычной позиции.
        Снятие ОБОИХ условий разом (`is_chapter`, `kind`) до этой правки не
        роняло набор — вход заведён отдельно от соседнего теста ниже."""
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        factories.PositionItemFactory.create(
            proposal=proposal, catalog_position=position, is_chapter=True,
            unit_cost_total=Decimal("500"), suggested_quantity=Decimal("10"),
            quantity=None, total_cost_total=None,
        )
        _bare_position(factories, proposal, position, price=Decimal("100"), weight=Decimal("10"))

        items = self._drilldown(client, contract, position)["items"]
        assert len(items) == 1
        assert Decimal(items[0]["unit_cost_total"]) == Decimal("100")

    def test_matrix_cell_drilldown_excludes_non_position_catalog_rows(self, client, factories):
        """Копия условия VIEW `kind == POSITION` внутри `_all_positions_
        select` (правка ревью, круг 1, правка 4): позиция каталожной строки,
        ждущей разбора (`TO_REVIEW`), не обязана появляться в drill-down —
        `catalog_position_id` у нужного запроса совпадает, но строка каталога
        ещё не работа."""
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create(kind=CatalogKind.TO_REVIEW.value)
        _bare_position(factories, proposal, position, price=Decimal("100"), weight=Decimal("10"))

        assert self._drilldown(client, contract, position)["items"] == []


# ---------------------------------------------------------------------------
#  Задача 2 плана правила цены (спека правила цены §2.4, §2.5, §2.6):
#  совокупность по присутствию, две оси состояния ячейки, неполнота.
# ---------------------------------------------------------------------------

NAN = Decimal("NaN")
INF = Decimal("Infinity")
NINF = Decimal("-Infinity")


def _bare_position(factories, proposal, position, *, price, weight):
    """Позиция с ЛЮБЫМ значением цены/веса, включая `None` и нефинитные —
    `_position` этого не умеет (гонит оба через `Decimal(str(x))`, а `None`
    в `str()` даёт `"None"`, не проходящий в `Decimal(...)`)."""
    return factories.PositionItemFactory.create(
        proposal=proposal,
        catalog_position=position,
        unit_cost_total=price,
        suggested_quantity=weight,
        quantity=None,
        total_cost_total=None,
    )


def _cell_for_positions(factories, pairs, *, standard=None, standard_job_title=None):
    """Одна работа, один договор, позиции заданы явными парами (цена, вес) —
    построчный вход таблиц §2.5/§2.6 спеки правила цены."""
    contract, _estimate, proposal = _estimate_with(factories)
    position = factories.CatalogPositionFactory.create(
        standard_job_title=standard_job_title or factory_default_title()
    )
    for price, weight in pairs:
        _bare_position(factories, proposal, position, price=price, weight=weight)
    if standard is not None:
        factories.RateStandardFactory.create(
            catalog_position=position,
            rate_class=contract.rate_class,
            standard_unit_rate=standard,
            valid_from=dt.date(2025, 1, 1),
        )
    return contract, position


_TITLE_COUNTER = [0]


def factory_default_title() -> str:
    _TITLE_COUNTER[0] += 1
    return f"Работа правила цены {_TITLE_COUNTER[0]}"


def _row_and_cell(client, contract, position):
    row = next(
        r for r in _matrix(client)["rows"] if r["catalog_position_id"] == position.id
    )
    return row, _cell_of(row, contract.id)


class TestMatrixPresenceMakesPricelessWorkAVisibleRow:
    """Работа, представленная во всей выборке ИСКЛЮЧИТЕЛЬНО позициями без
    цены, становится строкой матрицы (спека §2.4). Три отдельных утверждения:
    она есть на странице, входит в `total`, находится текстовым поиском.

    Оба входа — `price=None` И `price=Decimal("0")` (правка K ревью): на
    стенде NULL-цен НОЛЬ (спека §1.1 — все 10 674 бесценовые позиции несут
    именно ноль), и утверждение, доказанное только на входе, которого в
    данных не бывает, ничего не говорит о реальном стенде.
    """

    def _priceless_work(self, factories, *, price=None):
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create(
            standard_job_title="Работа целиком без цены"
        )
        _bare_position(factories, proposal, position, price=price, weight=Decimal("10"))
        return contract, position

    @pytest.mark.parametrize("price", [None, Decimal("0")], ids=["empty", "zero"])
    def test_it_is_present_on_the_page(self, client, factories, price):
        _contract, position = self._priceless_work(factories, price=price)
        ids = [r["catalog_position_id"] for r in _matrix(client)["rows"]]
        assert position.id in ids

    @pytest.mark.parametrize("price", [None, Decimal("0")], ids=["empty", "zero"])
    def test_it_is_counted_in_total(self, client, factories, price):
        self._priceless_work(factories, price=price)
        assert _matrix(client)["total"] == 1

    @pytest.mark.parametrize("price", [None, Decimal("0")], ids=["empty", "zero"])
    def test_it_is_found_by_text_search(self, client, factories, price):
        _contract, position = self._priceless_work(factories, price=price)
        body = _matrix(client, q="целиком без цены")
        assert [r["catalog_position_id"] for r in body["rows"]] == [position.id]


class TestMatrixCellAbsenceVsEmptyRate:
    """Отсутствие ячейки и ячейка с пустой ставкой — ДВА разных наблюдения
    («работы нет в смете этого договора» против «работа есть, цены нет»),
    доказанных РАЗНЫМИ входами, а не одной фикстурой."""

    def test_absent_cell_still_means_no_work_in_this_contract(self, client, factories):
        """Правка I ревью: без утверждения о членстве в `columns` «ячейки нет,
        потому что работы нет в смете» неотличимо от «ячейки нет, потому что
        договор выпал из выборки» — оба дают `_cell_of(...) is None`."""
        with_work, _e1, p1 = _estimate_with(factories)
        without_work, _e2, _p2 = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _position(factories, p1, position, unit_cost="100", weight="10")

        body = _matrix(client)
        assert without_work.id in {c["contract_id"] for c in body["columns"]}
        row = next(r for r in body["rows"] if r["catalog_position_id"] == position.id)
        cell = _cell_of(row, with_work.id)
        assert cell is not None
        assert _cell_of(row, without_work.id) is None

    def test_priceless_position_gives_a_cell_object_with_empty_rate(self, client, factories):
        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        _bare_position(factories, proposal, position, price=None, weight=Decimal("10"))

        _row, cell = _row_and_cell(client, contract, position)
        assert cell is not None
        assert cell["rate"] is None
        assert cell["rate_reason"] == "no_price"


class TestMatrixRateReasonTableRows:
    """Восемь строк таблицы §2.5 спеки правила цены — каждая своим входом,
    даёт объявленную ТРОЙКУ `(rate_reason, deviation_reason,
    row_amount_incomplete)` — все три колонки, которые таблица объявляет для
    строки, а не только первые две (ревью, Правка M второй пункт: класс
    заявлял «восемь строк... каждая своим входом», а сверял только две
    колонки из четырёх — `row_amount_incomplete` не проверялся НИ РАЗУ, и
    расхождение реализации со спекой по строке `c` было от этого невидимо).

    По строкам `a`, `b`, `f` таблица не называет `row_amount_incomplete`
    ОДНИМ значением («по §2.6» / «см. §2.6» — он зависит от того, есть ли в
    ячейке исключённые позиции, а не от самой строки таблицы). Для ВХОДА,
    выбранного здесь (единственная позиция, исключённых соседей нет),
    неполноте взяться неоткуда — она `False`; это свойство конкретного
    входа, а не гарантия строки таблицы на любом входе (`f` с ДРУГИМ входом
    — отрицательным весом вместо нулевого — даёт `True`, см.
    `TestMatrixMixedPriceSets`/`TestMatrixIncompletenessFlagFourAndTwo`).
    """

    def test_a_ingesting_with_standard_gives_rate_and_deviation(self, client, factories):
        contract, position = _cell_for_positions(
            factories, [(Decimal("120"), Decimal("1"))], standard=Decimal("100")
        )
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] is None
        assert cell["deviation_reason"] is None
        assert Decimal(cell["rate"]) == Decimal("120")
        assert row["row_amount_incomplete"] is False

    def test_b_ingesting_without_standard_gives_no_standard(self, client, factories):
        contract, position = _cell_for_positions(factories, [(Decimal("120"), Decimal("1"))])
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] is None
        assert cell["deviation_reason"] == "no_standard"
        assert row["row_amount_incomplete"] is False

    def test_c_finite_rate_with_non_finite_standard_keeps_rate_hides_deviation(
        self, client, factories, db_session
    ):
        """Нефинитный норматив ДОСТИЖИМ: CHECK `standard_unit_rate > 0` не
        отсекает `NaN` (спека §1.2 — PostgreSQL считает `'NaN' > 0` истиной).

        `row_amount_incomplete` ЗДЕСЬ — `False` (правка пользователя от
        10.09.2026 в спеке §2.5, врезка «Правка по ходу реализации»:
        отклонение стало нефинитным только от нефинитного НОРМАТИВА — ставка
        и сумма ячейки настоящие числа, ячейка показана целиком и входит в
        `row_amount` полностью, исключённой позиции здесь нет вовсе, и
        поднимать признак неполноты ВЕСА (§2.6, §6 `AGENTS.md`) значило бы
        сказать про вес неправду. Код это свойство уже соблюдал (задача 2
        никогда не заводила исключённую позицию на этом входе) — не хватало
        ИМЕННО ЭТОГО утверждения, и ревью нашло дыру в тесте, а не в коде.
        """
        _contract_with_standard(
            factories, unit_cost_total=Decimal("100"), vat_rate=Decimal("0"), standard=NAN
        )
        db_session.commit()
        row = client.get("/api/v1/analytics/matrix").json()["rows"][0]
        cell = row["cells"][0]
        assert cell["rate"] is not None
        assert cell["rate_reason"] is None
        assert cell["deviation_pct"] is None
        assert cell["deviation_reason"] == "not_finite"
        assert row["row_amount_incomplete"] is False

    def test_d_unknown_vat_base_gives_rate_reason_and_no_rate(
        self, client, factories, db_session
    ):
        _priced_estimate(factories, unit_cost_total=Decimal("120"), vat_rate=None)
        db_session.commit()
        row = client.get("/api/v1/analytics/matrix").json()["rows"][0]
        cell = row["cells"][0]
        assert cell["rate_reason"] == "unknown_vat_base"
        assert cell["deviation_reason"] == "no_rate"
        assert row["row_amount_incomplete"] is True

    @pytest.mark.parametrize("bad", [NAN, INF, NINF])
    def test_e_lone_non_finite_position_gives_not_finite_and_no_rate(
        self, client, factories, bad
    ):
        contract, position = _cell_for_positions(factories, [(bad, Decimal("1"))])
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] == "not_finite"
        assert cell["deviation_reason"] == "no_rate"
        assert row["row_amount_incomplete"] is True

    def test_f_price_ok_weight_not_ok_gives_no_weight_and_no_rate(self, client, factories):
        contract, position = _cell_for_positions(factories, [(Decimal("100"), Decimal("0"))])
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] == "no_weight"
        assert cell["deviation_reason"] == "no_rate"
        # Таблица говорит «см. §2.6» — на ЭТОМ входе (вес ровно ноль)
        # вклад нулевой, флаг не поднимается.
        assert row["row_amount_incomplete"] is False

    def test_g_finite_negative_price_gives_negative_only_and_no_rate(self, client, factories):
        contract, position = _cell_for_positions(factories, [(Decimal("-100"), Decimal("1"))])
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] == "negative_only"
        assert cell["deviation_reason"] == "no_rate"
        assert row["row_amount_incomplete"] is True

    @pytest.mark.parametrize("price", [Decimal("0"), None])
    def test_h_zero_or_empty_price_gives_no_price_and_no_rate(self, client, factories, price):
        contract, position = _cell_for_positions(factories, [(price, Decimal("1"))])
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] == "no_price"
        assert cell["deviation_reason"] == "no_rate"
        assert row["row_amount_incomplete"] is False

    @pytest.mark.parametrize("bad_weight", [NAN, INF, NINF])
    def test_i_price_ok_non_finite_weight_gives_not_finite_over_no_weight(
        self, client, factories, bad_weight
    ):
        """Правка B ревью (мутант M2): единственный вход, где `not_finite` и
        `no_weight` из `_cell_without_ingesting` претендуют ОДНОВРЕМЕННО —
        цена пригодна (100 > 0, конечна), вес нефинитен. `any_no_weight`
        здесь тоже истинно (цена пригодна, вес НЕ пригоден), поэтому
        перестановка первых двух проверок приоритета красит ИМЕННО этот
        тест, а не совпадает с ним случайно."""
        contract, position = _cell_for_positions(factories, [(Decimal("100"), bad_weight)])
        _row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] == "not_finite"

    def test_zero_price_zero_weight_gives_no_price_not_no_weight(self, client, factories):
        """Правка D ревью (мутант M11): `no_weight` в `_presence_row_flags`
        обязан требовать `_price_ok(price)`, а не только `NOT _weight_ok
        (weight)` — без этого операнда цена-ноль-и-вес-ноль тоже читалась бы
        как `no_weight`, хотя правильный приоритет — `no_price` (цены нет
        вовсе, вопрос веса вторичен)."""
        contract, position = _cell_for_positions(factories, [(Decimal("0"), Decimal("0"))])
        _row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] == "no_price"

    def test_negative_price_zero_weight_gives_negative_only_not_no_weight(
        self, client, factories
    ):
        """Правка D ревью (мутант M11), вторая половина: цена отрицательна
        (не пригодна) и вес нулевой (тоже не пригоден) — без `_price_ok`
        внутри `no_weight` этот вход тоже читался бы как `no_weight`, хотя
        приоритет спеки требует `negative_only`."""
        contract, position = _cell_for_positions(factories, [(Decimal("-50"), Decimal("0"))])
        _row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] == "negative_only"


class TestMatrixRateReasonEquivalence:
    """`rate_reason` пуст ТОГДА И ТОЛЬКО ТОГДА, когда ставка есть — записано
    эквивалентностью (`docs/insights/state-the-rule-as-an-equivalence.md`), а
    не половиной импликации. При непустом `rate_reason` `deviation_reason`
    всегда `no_rate`; при пустом — всегда `None` (стандарт задан для входа
    `rate_present`, иначе он был бы `no_standard`, а не `None`).

    **Этот класс — НЕ независимый оракул** (ревью, Правка J): предикат
    построен из ДВУХ полей ОДНОГО и того же ответа `_fold_cell`/`_cell_
    without_ingesting`, и согласованно неверная пара полей прошла бы его
    же собственную проверку. Внешний оракул — `TestMatrixRateReasonTableRows`
    рядом: там ожидание — буквальный литерал строки таблицы §2.5, а не
    производное от другого поля того же ответа.
    """

    @pytest.mark.parametrize(
        "pairs,standard",
        [
            ([(Decimal("100"), Decimal("1"))], Decimal("100")),
            ([(Decimal("0"), Decimal("1"))], None),
            ([(Decimal("-10"), Decimal("1"))], None),
            ([(Decimal("100"), Decimal("0"))], None),
            ([(NAN, Decimal("1"))], None),
        ],
        ids=["rate_present", "no_price", "negative_only", "no_weight", "not_finite"],
    )
    def test_rate_reason_null_iff_rate_present(self, client, factories, pairs, standard):
        contract, position = _cell_for_positions(factories, pairs, standard=standard)
        _row, cell = _row_and_cell(client, contract, position)
        assert (cell["rate"] is not None) == (cell["rate_reason"] is None)
        if cell["rate_reason"] is not None:
            assert cell["deviation_reason"] == "no_rate"
        else:
            assert cell["deviation_reason"] is None


class TestMatrixMixedPriceSets:
    """Смешанные наборы §2.5: несколько позиций одной ячейки, каждый набор
    предъявлен своим входом. «Положительная плюс нулевая» даёт ставку —
    нулевая позиция её НЕ гасит."""

    def test_positive_plus_zero_gives_rate_without_flag(self, client, factories):
        contract, position = _cell_for_positions(
            factories, [(Decimal("100"), Decimal("1")), (Decimal("0"), Decimal("1"))]
        )
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] is None
        assert Decimal(cell["rate"]) == Decimal("100")
        assert row["row_amount_incomplete"] is False

    def test_positive_plus_negative_gives_rate_with_flag(self, client, factories):
        """Заодно предъявляет «ставка есть И флаг поднят» — законную и
        обязательную комбинацию (спека §2.6), а не противоречие."""
        contract, position = _cell_for_positions(
            factories, [(Decimal("100"), Decimal("1")), (Decimal("-50"), Decimal("2"))]
        )
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] is None
        assert Decimal(cell["rate"]) == Decimal("100")
        assert row["row_amount_incomplete"] is True

    @pytest.mark.parametrize("bad", [NAN, INF, NINF])
    def test_positive_plus_non_finite_gives_rate_with_flag(self, client, factories, bad):
        contract, position = _cell_for_positions(
            factories, [(Decimal("100"), Decimal("1")), (bad, Decimal("1"))]
        )
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] is None
        assert Decimal(cell["rate"]) == Decimal("100")
        assert row["row_amount_incomplete"] is True

    @pytest.mark.parametrize("bad", [NAN, INF, NINF])
    def test_negative_plus_non_finite_gives_not_finite_with_flag(self, client, factories, bad):
        contract, position = _cell_for_positions(
            factories, [(Decimal("-50"), Decimal("1")), (bad, Decimal("1"))]
        )
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] == "not_finite"
        assert row["row_amount_incomplete"] is True

    def test_suitable_without_weight_plus_negative_with_weight_gives_no_weight_with_flag(
        self, client, factories
    ):
        contract, position = _cell_for_positions(
            factories, [(Decimal("100"), Decimal("0")), (Decimal("-50"), Decimal("2"))]
        )
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] == "no_weight"
        assert row["row_amount_incomplete"] is True

    def test_negative_weight_alone_gives_no_weight_rate_reason(self, client, factories):
        """Отрицательный вес даёт `rate_reason = no_weight`, если других
        входящих позиций в ячейке нет (спека §2.5) — вес непригоден по тому
        же правилу, что нуль и пустота."""
        contract, position = _cell_for_positions(factories, [(Decimal("100"), Decimal("-5"))])
        row, cell = _row_and_cell(client, contract, position)
        assert cell["rate_reason"] == "no_weight"
        assert row["row_amount_incomplete"] is True

    def test_several_ingesting_one_unknown_vat_base_gives_unknown_vat_base_with_flag(
        self, client, factories, db_session
    ):
        _priced_estimate(
            factories, positions=[(Decimal("120"), Decimal("20")), (Decimal("100"), None)]
        )
        db_session.commit()
        row = client.get("/api/v1/analytics/matrix").json()["rows"][0]
        cell = row["cells"][0]
        assert cell["rate_reason"] == "unknown_vat_base"
        assert row["row_amount_incomplete"] is True


class TestMatrixIncompletenessFlagFourAndTwo:
    """Неполнота (спека §2.6): флаг ПОДНИМАЕТСЯ на четырёх входах, НЕ
    поднимается на двух. Исключённая позиция строится РЯДОМ с одной
    ингестирующей — иначе сама ячейка гаснет целиком, и флаг наблюдать
    негде."""

    def _row_for(self, client, factories, excluded_pair):
        contract, position = _cell_for_positions(
            factories, [(Decimal("100"), Decimal("1")), excluded_pair]
        )
        row, _cell = _row_and_cell(client, contract, position)
        return row

    def test_finite_nonzero_price_with_suitable_weight_raises_flag(self, client, factories):
        row = self._row_for(client, factories, (Decimal("-50"), Decimal("2")))
        assert row["row_amount_incomplete"] is True

    @pytest.mark.parametrize("bad", [NAN, INF, NINF])
    def test_non_finite_price_with_positive_weight_raises_flag(self, client, factories, bad):
        row = self._row_for(client, factories, (bad, Decimal("2")))
        assert row["row_amount_incomplete"] is True

    @pytest.mark.parametrize("bad", [NAN, INF, NINF])
    def test_non_finite_weight_raises_flag(self, client, factories, bad):
        row = self._row_for(client, factories, (Decimal("50"), bad))
        assert row["row_amount_incomplete"] is True

    def test_suitable_price_with_finite_negative_weight_raises_flag(self, client, factories):
        row = self._row_for(client, factories, (Decimal("50"), Decimal("-3")))
        assert row["row_amount_incomplete"] is True

    @pytest.mark.parametrize("price", [Decimal("0"), None])
    def test_zero_or_empty_price_does_not_raise_flag(self, client, factories, price):
        row = self._row_for(client, factories, (price, Decimal("5")))
        assert row["row_amount_incomplete"] is False

    @pytest.mark.parametrize("weight", [Decimal("0"), None])
    def test_zero_or_empty_weight_does_not_raise_flag(self, client, factories, weight):
        row = self._row_for(client, factories, (Decimal("50"), weight))
        assert row["row_amount_incomplete"] is False


class TestMatrixPresenceReadsPositionItemNotTheView:
    """Решение о носителе (отчёт задачи 2): нефинитная позиция поднимает флаг
    неполноты, находясь на стороне присутствия, а не через `bool_or` по
    строкам VIEW — отдельное утверждение, а не следствие существующего:
    после задачи 4 такой строки в VIEW не будет, и правило обязано работать
    без неё уже сейчас."""

    def test_excluded_positions_query_does_not_reference_the_view(self):
        """Проверка структурная — по скомпилированному SQL, тем же приёмом,
        что `test_row_order_has_a_unique_tiebreaker` выше: сегодня VIEW ещё
        отдаёт нефинитные строки (миграция 0016 их уберёт только в задаче 4),
        и поведенческий тест ДО этой миграции не отличил бы верную
        реализацию (чтение `PositionItem`) от чтения VIEW — оба дали бы один
        и тот же наблюдаемый результат.
        """
        from crud import analytics

        latest = analytics.latest_estimates()
        compiled = str(
            analytics._excluded_positions_cte(
                rate_class_id=None, date_from=None, date_to=None, latest=latest
            ).compile(dialect=postgresql.dialect())
        )
        assert "v_position_deviation_inputs" not in compiled
        assert "position_items" in compiled

    def test_presence_query_does_not_reference_the_view(self):
        from crud import analytics

        latest = analytics.latest_estimates()
        compiled = str(
            analytics._presence_cte(
                rate_class_id=None, date_from=None, date_to=None, latest=latest
            ).compile(dialect=postgresql.dialect())
        )
        assert "v_position_deviation_inputs" not in compiled
        assert "position_items" in compiled


class TestMatrixPresenceWeightAgreesWithView:
    """Решение о весе (отчёт задачи 2): `_PRESENCE_WEIGHT` — именованная
    копия правила, которое несёт миграция 0012 текстом VIEW. Сверяется
    ЗНАЧЕНИЕ на одной и той же строке, а не текст двух формул — сверка
    текста зелена и когда оба текста одинаково неверны."""

    def test_presence_weight_agrees_with_view_weight_on_the_same_row(
        self, db_session, factories
    ):
        from models import PositionItem

        _priced_estimate(
            factories, unit_cost_total=Decimal("100"), vat_rate=Decimal("20"),
            weight=Decimal("7"),
        )
        db_session.commit()

        from crud.analytics import _PRESENCE_WEIGHT

        row = db_session.execute(
            sa.select(
                _PRESENCE_WEIGHT.label("presence_weight"), DEVIATION_INPUTS.c.weight
            ).select_from(
                sa.join(
                    PositionItem,
                    DEVIATION_INPUTS,
                    DEVIATION_INPUTS.c.position_item_id == PositionItem.id,
                )
            )
        ).one()
        assert row.presence_weight == row.weight

    def test_presence_weight_falls_back_to_quantity_when_suggested_is_empty(
        self, db_session, factories
    ):
        """Правка E ревью (мутант M20): тест выше держит ОБЕ колонки
        заполненными (`suggested_quantity` и `quantity`), поэтому снятие
        фолбэка целиком (`_PRESENCE_WEIGHT = PositionItem.suggested_
        quantity`, без `COALESCE`) его не роняет — обе ветки `COALESCE` дают
        один результат на таком входе. Здесь `suggested_quantity` пуст,
        `quantity` заполнен: вес обязан взяться из `quantity`."""
        from models import PositionItem

        contract, _estimate, proposal = _estimate_with(factories)
        position = factories.CatalogPositionFactory.create()
        factories.PositionItemFactory.create(
            proposal=proposal,
            catalog_position=position,
            unit_cost_total=Decimal("100"),
            suggested_quantity=None,
            quantity=Decimal("7"),
            total_cost_total=Decimal("700"),
        )
        db_session.commit()

        from crud.analytics import _PRESENCE_WEIGHT

        weight = db_session.execute(
            sa.select(_PRESENCE_WEIGHT)
            .select_from(PositionItem)
            .where(PositionItem.catalog_position_id == position.id)
        ).scalar_one()
        assert weight == Decimal("7")


class TestMatrixPresenceScopeMatchesColumns:
    """Фильтры выборки на стороне присутствия (`column_scope_filters`) дают
    тот же состав договоров, что и колонки матрицы: второго правила даты в
    системе нет (Global Constraints плана)."""

    def test_out_of_period_work_has_no_row_and_no_column(self, client, factories):
        contract = factories.ContractFactory.create(signed_date=dt.date(2025, 3, 1))
        _c, _estimate, proposal = _estimate_with(
            factories, contract=contract, estimate_date=dt.date(2026, 5, 1)
        )
        position = factories.CatalogPositionFactory.create()
        _bare_position(factories, proposal, position, price=None, weight=Decimal("10"))

        outside = _matrix(client, date_from="2025-01-01", date_to="2025-12-31")
        assert outside["columns"] == []
        assert outside["rows"] == []

        inside = _matrix(client, date_from="2026-01-01", date_to="2026-12-31")
        assert [c["contract_id"] for c in inside["columns"]] == [contract.id]
        assert len(inside["rows"]) == 1


class TestMatrixPresenceStandardBoundary:
    """`_presence_standards_cte` — норматив ячейки без входящих позиций
    (правка C ревью, мутанты M5 и M13). Полуоткрытый интервал `[valid_from,
    valid_to)` и класс договора — то же правило, что несёт VIEW (§4), но
    считанное напрямую по `PositionItem`/`Contract`, а не через VIEW/
    `cell_groups`. Каждый вход сверяет само ЗНАЧЕНИЕ норматива, а не только
    его непустоту — иначе инверсия границы интервала могла бы остаться
    незамеченной, если бы случайно попала в ДРУГОЙ, тоже подходящий норматив.
    """

    def _priceless_cell_with_standard(
        self, factories, *, estimate_date, standard, valid_from, valid_to, rate_class=None
    ):
        contract = (
            factories.ContractFactory.create(rate_class=rate_class)
            if rate_class is not None
            else factories.ContractFactory.create()
        )
        _c, _estimate, proposal = _estimate_with(
            factories, contract=contract, estimate_date=estimate_date
        )
        position = factories.CatalogPositionFactory.create()
        _bare_position(factories, proposal, position, price=None, weight=Decimal("10"))
        factories.RateStandardFactory.create(
            catalog_position=position,
            rate_class=contract.rate_class,
            standard_unit_rate=standard,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        return contract, position

    def test_standard_found_when_comparison_date_equals_valid_from(self, client, factories):
        contract, position = self._priceless_cell_with_standard(
            factories, estimate_date=dt.date(2025, 6, 1), standard=Decimal("77"),
            valid_from=dt.date(2025, 6, 1), valid_to=dt.date(2025, 12, 31),
        )
        _row, cell = _row_and_cell(client, contract, position)
        assert cell["standard_unit_rate"] is not None
        assert Decimal(cell["standard_unit_rate"]) == Decimal("77")

    def test_standard_not_found_when_comparison_date_equals_valid_to(self, client, factories):
        """Интервал ПОЛУОТКРЫТ: `valid_to` — уже не в интервале."""
        contract, position = self._priceless_cell_with_standard(
            factories, estimate_date=dt.date(2025, 12, 31), standard=Decimal("77"),
            valid_from=dt.date(2025, 1, 1), valid_to=dt.date(2025, 12, 31),
        )
        _row, cell = _row_and_cell(client, contract, position)
        assert cell["standard_unit_rate"] is None

    def test_standard_not_found_for_a_different_rate_class(self, client, factories):
        other_class = factories.RateClassFactory.create(title="Другой класс присутствия")
        wrong_standard_position = factories.CatalogPositionFactory.create()
        factories.RateStandardFactory.create(
            catalog_position=wrong_standard_position,
            rate_class=other_class,
            standard_unit_rate=Decimal("77"),
            valid_from=dt.date(2025, 1, 1),
        )
        contract, _estimate, proposal = _estimate_with(
            factories, estimate_date=dt.date(2025, 6, 1)
        )
        _bare_position(
            factories, proposal, wrong_standard_position, price=None, weight=Decimal("10")
        )

        _row, cell = _row_and_cell(client, contract, wrong_standard_position)
        assert cell["standard_unit_rate"] is None


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
