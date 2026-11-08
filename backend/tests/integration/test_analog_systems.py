"""Системы на экране «Против нашей цены» после лестницы аналогов (спека §2.12, §2.13, §2.17).

Механика ступеней 4а у систем прежняя; новое — порог 1 ₽ по строке договора, «самая свежая» и
итоги с покрытием. Площадь объекта тендера и договоров 1000, база НДС 0: ставка договора на м²
равна сумме его строк ключа, делённой на 1000. Названия и числа выдуманы.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import (
    _history,
    _line_of,
    _offer_article,
    _ok,
    _pair,
    _panel,
    _put,
)
from tests.integration.test_our_price_round_api import Stage

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


def _sys_history(stage, total, *, prepared=dt.date(2025, 4, 1), amendment_no=None, **kwargs):
    """Договор истории с одной системной строкой: цена за единицу 1 ₽, сумма `total`."""
    contract, estimate, proposal = _history(stage, D("1"), total=D(total), kind="SYSTEM", **kwargs)
    estimate.data_prepared_on_date = prepared
    estimate.amendment_no = amendment_no
    stage.db.flush()
    return contract, estimate, proposal


def _kp(stage, totals=(30000, 90000), *, held_on=dt.date(2026, 8, 10)):
    rnd = stage.round(held_on=held_on)
    part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
    items = [_put(stage, part.proposal, "R", D("1"), total=D(total), kind="SYSTEM") for total in totals]
    return rnd, part, items


def _rates(panel) -> list[Decimal]:
    return [_pair(c["rate"])[0] for c in panel["contracts"]]


# ---------------------------------------------------------------------------
#  Порог 1 ₽ по строке договора (К4)
# ---------------------------------------------------------------------------


class TestThreshold:
    def test_a_contract_line_of_fifty_kopecks_per_unit_is_not_in_the_key_sum(self, member_client, stage):
        """К4: строка договора 0,50 ₽ за единицу не входит в сумму ключа до деления на площадь."""
        _, _, proposal = _sys_history(stage, "100000")
        _put(stage, proposal, "R", D("0.5"), total=D("500"), kind="SYSTEM")
        _sys_history(stage, "140000")
        rnd, part, (small, _) = _kp(stage)

        panel = _panel(member_client, stage.tender, rnd, part, small)

        assert _rates(panel) == [D("100"), D("140")]  # а не 100.5

    def test_a_per_area_rate_of_fifty_kopecks_enters(self, member_client, stage):
        """К4: ставка 0,50 ₽/м² — не строка договора, порогу не подчиняется."""
        _sys_history(stage, "500")  # 1 ₽ за единицу, 500 ₽ на 1000 м²
        _sys_history(stage, "140000")
        rnd, part, (small, _) = _kp(stage)

        panel = _panel(member_client, stage.tender, rnd, part, small)

        assert _rates(panel) == [D("0.5"), D("140")]

    def test_exactly_one_ruble_per_unit_enters(self, member_client, stage):
        _, _, proposal = _sys_history(stage, "100000")
        _put(stage, proposal, "R", D("1"), total=D("1000"), kind="SYSTEM")
        _sys_history(stage, "140000")
        rnd, part, (small, _) = _kp(stage)

        panel = _panel(member_client, stage.tender, rnd, part, small)

        assert _rates(panel) == [D("101"), D("140")]

    def test_a_contract_with_only_symbolic_lines_has_no_rate(self, member_client, stage):
        _history(stage, D("0.5"), total=D("500"), kind="SYSTEM")
        _sys_history(stage, "140000")
        rnd, part, (small, _) = _kp(stage)

        panel = _panel(member_client, stage.tender, rnd, part, small)

        assert _rates(panel) == [D("140")]


# ---------------------------------------------------------------------------
#  Форма ответа у системы
# ---------------------------------------------------------------------------


class TestSystemForm:
    def test_the_system_line_keeps_the_rungs_and_has_no_work_fields(self, member_client, stage):
        _sys_history(stage, "100000")
        _sys_history(stage, "140000")
        rnd, part, (small, _) = _kp(stage)

        panel = _panel(member_client, stage.tender, rnd, part, small)
        line = panel["line"]

        assert (line["rung"]["level"], line["rung"]["scope"], line["rung"]["auto"]) == ("v", "cls", True)
        assert line["rungs"]
        assert (line["work_rung"], line["zone"], line["section_chain_id"]) == (None, None, None)
        assert (line["analogs_count"], line["no_history_reason"], line["choice"]) == (0, None, None)
        assert panel["analogs"] == []
        assert line["target_kind"] == "lowest"
        assert _pair(line["lowest"]["value"]) == (D("100"), D("100"))
        # у системы самая низкая — ставка класса: договора-источника ядро не называет
        assert (line["lowest"]["analog_id"], line["lowest"]["contract_id"]) == (None, None)
        assert line["lowest"]["from_all_classes"] is False
        assert [c["is_lower_bound"] for c in panel["contracts"]] == [True, False]
        assert panel["line"]["unit_mismatch"] is None

    def test_a_system_mismatch_names_contracts_not_analogs(self, member_client, stage):
        _sys_history(stage, "100000")
        _sys_history(stage, "110000")
        _sys_history(stage, "700000")
        rnd, part, (small, _) = _kp(stage)

        panel = _panel(member_client, stage.tender, rnd, part, small)

        mismatch = panel["line"]["unit_mismatch"]
        assert mismatch["analogs"] == []
        assert mismatch["all_outliers"] is False
        assert len(mismatch["contracts"]) == 1


# ---------------------------------------------------------------------------
#  Самая свежая у систем
# ---------------------------------------------------------------------------


class TestSystemFreshest:
    def test_freshest_is_the_nearest_class_contract_and_the_lowest_stays_the_cheapest(self, member_client, stage):
        _, _, _ = _sys_history(stage, "100000", prepared=dt.date(2025, 1, 10))
        late, _, _ = _sys_history(stage, "140000", prepared=dt.date(2026, 6, 15))
        rnd, part, (small, large) = _kp(stage)

        body = _ok(member_client, stage.tender, rnd, part)
        line = _line_of(body, small)

        freshest = line["freshest"]
        assert (freshest["analog_id"], freshest["contract_id"], freshest["period"]) == (None, late.id, "2026-06")
        assert _pair(freshest["value"]) == (D("140"), D("140"))
        assert _pair(line["lowest"]["value"]) == (D("100"), D("100"))
        # группа: ₽/м² КП 120; к самой низкой +20 000, к самой свежей -20 000; доля первой строки 0,25
        assert line["to_lowest"] == {"net": "5000.00", "shown": "5000.00"}
        assert line["to_freshest"] == {"net": "-5000.00", "shown": "-5000.00"}
        assert line["over_freshest"] == {"net": "0.00", "shown": "0.00"}
        assert _line_of(body, large)["to_freshest"] == {"net": "-15000.00", "shown": "-15000.00"}

    def test_the_freshest_comes_from_the_scope_of_the_lowest(self, member_client, stage, factories):
        other_class = factories.RateClassFactory.create(title="Класс Ч")
        cheap, _, _ = _sys_history(stage, "100000", prepared=dt.date(2025, 1, 10), rate_class=other_class)
        late, _, _ = _sys_history(stage, "140000", prepared=dt.date(2026, 6, 15), rate_class=other_class)
        rnd, part, (small, _) = _kp(stage)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), small)

        assert line["lowest"]["from_all_classes"] is True
        assert _pair(line["lowest"]["value"]) == (D("100"), D("100"))
        assert line["freshest"]["contract_id"] == late.id
        assert cheap.id != late.id

    def test_without_a_period_there_is_no_freshest_and_the_line_stays_comparable(self, member_client, stage):
        _sys_history(stage, "100000", prepared=None, amendment_no=1)
        _sys_history(stage, "140000", prepared=None, amendment_no=1)
        rnd, part, (small, _) = _kp(stage)

        line = _line_of(_ok(member_client, stage.tender, rnd, part), small)

        assert line["state"] == "comparable"
        assert line["freshest"] is None and line["to_freshest"] is None and line["over_freshest"] is None
        assert line["to_lowest"] == {"net": "5000.00", "shown": "5000.00"}


# ---------------------------------------------------------------------------
#  Итоги систем: деньги группы один раз, покрытие
# ---------------------------------------------------------------------------


class TestSystemTotals:
    def _scene(self, stage):
        _sys_history(stage, "100000", prepared=dt.date(2025, 1, 10))
        _sys_history(stage, "90000", prepared=dt.date(2026, 6, 15))
        rnd = stage.round(held_on=dt.date(2026, 8, 10))
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        first = _put(stage, part.proposal, "R", D("1"), total=D("30000"), kind="SYSTEM")
        second = _put(stage, part.proposal, "R2", D("1"), total=D("90000"), kind="SYSTEM")
        return rnd, part, first, second

    def test_group_money_of_the_freshest_is_counted_once_and_split_between_articles(self, member_client, stage):
        """Группа: ₽/м² КП 120, самая свежая 90, разница 30 000 — один раз в итоге участника."""
        rnd, part, first, second = self._scene(stage)

        body = _ok(member_client, stage.tender, rnd, part)

        totals = body["participants"][0]["totals"]
        assert totals["over_freshest"] == {"net": "30000.00", "shown": "30000.00"}
        assert (totals["comparable_lines"], totals["freshest_lines"]) == (2, 2)
        shares = [
            _offer_article(body, stage, cat)["lines_totals"]["over_freshest"]["net"] for cat in ("R", "R2")
        ]
        assert shares == ["7500.00", "22500.00"]
        assert sum(D(share) for share in shares) == D(totals["over_freshest"]["net"])

    def test_the_over_lowest_of_the_same_scene_is_the_group_money_to_the_lowest(self, member_client, stage):
        rnd, part, *_ = self._scene(stage)

        totals = _ok(member_client, stage.tender, rnd, part)["participants"][0]["totals"]

        # самая низкая 90 (договор 2025 года стоит 100, 2026 года — 90): (120 - 90) * 1000
        assert totals["over_lowest"] == {"net": "30000.00", "shown": "30000.00"}

    def test_no_freshest_anywhere_gives_a_null_over_freshest_and_a_filled_over_lowest(self, member_client, stage):
        _sys_history(stage, "100000", prepared=None, amendment_no=1)
        _sys_history(stage, "140000", prepared=None, amendment_no=1)
        rnd, part, _ = _kp(stage)

        totals = _ok(member_client, stage.tender, rnd, part)["participants"][0]["totals"]

        assert totals["over_freshest"] is None
        assert (totals["comparable_lines"], totals["freshest_lines"]) == (2, 0)
        assert totals["over_lowest"] == {"net": "20000.00", "shown": "20000.00"}
