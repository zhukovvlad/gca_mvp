"""Чистый расчёт разложения статьи (спека 2026-08-30-position-drilldown-design.md
§2.2–§2.6, §2.9, §2.13). Без БД: литералы на входе и выходе."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from services import position_drilldown as pd
from services import stage_summary as ss

D = Decimal


def col(idx: int, rate: str | None = "20") -> pd.DrillColumn:
    return pd.DrillColumn(offer_id=idx, estimate_id=100 + idx, round_id=10 + idx, stage_no=idx + 1,
                          label=None, held_on=dt.date(2026, 3, 1 + idx),
                          vat_rate_base=None if rate is None else D(rate))


COLS4 = [col(0), col(1), col(2), col(3)]
BASIS = ss.pick_tax_basis([c.vat_rate_base for c in COLS4])


def grp(kind=pd.KIND_POSITION, cpid=1, ref=None, title="Работа", key=None, **stages) -> pd.GroupInput:
    """stages: s0=..., s1=... — GroupStage по индексу колонки."""
    return pd.GroupInput(kind=kind, catalog_position_id=cpid if kind == pd.KIND_POSITION else None,
                         chapter_ref_raw=ref, title=title,
                         stages={int(k[1:]): v for k, v in stages.items()},
                         key=key or ((cpid,) if kind == pd.KIND_POSITION else (ref,)))


class TestGroupCells:
    def test_absent_iff_zero_estimate_rows(self):
        g = grp(s0=pd.GroupStage(D("100"), 1), s3=pd.GroupStage(D("90"), 1))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert [c.state for c in cells] == ["amount", "absent", "absent", "amount"]
        assert [(c.estimate_rows == 0) == (c.state == "absent") for c in cells] == [True] * 4

    def test_hole_in_the_middle_is_disappeared_then_appeared_not_removed(self):
        """Дыра §2.5: средние этапы absent, переходы disappeared/appeared,
        «снято» не появляется ни разу."""
        g = grp(s0=pd.GroupStage(D("100"), 1), s3=pd.GroupStage(D("90"), 1))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert cells[1].change.kind == "disappeared"
        assert cells[3].change.kind == "appeared"
        assert all(c.change.kind != "removed" and c.state != "removed" for c in cells)

    def test_zero_after_amount_is_removed_and_zero_before_is_not_evaluated(self):
        g = grp(s0=pd.GroupStage(D("0"), 1), s1=pd.GroupStage(D("50"), 1),
                s2=pd.GroupStage(D("0"), 1), s3=pd.GroupStage(D("0"), 1))
        states = [c.state for c in pd.group_cells(g, COLS4, BASIS)]
        assert states == ["not_evaluated", "amount", "removed", "removed"]

    def test_appeared_is_impossible_on_first_column(self):
        g = grp(s0=pd.GroupStage(D("100"), 1))
        first = pd.group_cells(g, COLS4, BASIS)[0]
        assert first.change.kind == "none" and first.change.reason == "first_column"

    def test_quantity_changed_compares_with_previous_present_stage(self):
        """Как mark_volume_steps генератора: absent-этап пропускается, «предыдущий»
        — предыдущий этап ПРИСУТСТВИЯ."""
        g = grp(s0=pd.GroupStage(D("10"), 1, (D("6"),), "шт"),
                s2=pd.GroupStage(D("10"), 1, (D("6"),), "шт"),
                s3=pd.GroupStage(D("10"), 2, (D("6"), D("11")), "шт"))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert [c.quantity_changed for c in cells] == [False, False, False, True]

    def test_unknown_middle_column_carries_reason_and_no_amount(self):
        cols = [col(0), col(1, rate=None), col(2), col(3)]
        basis = ss.pick_tax_basis([c.vat_rate_base for c in cols])
        g = grp(s0=pd.GroupStage(D("100"), 1), s1=pd.GroupStage(D("90"), 1),
                s2=pd.GroupStage(D("80"), 1), s3=pd.GroupStage(D("70"), 1))
        cells = pd.group_cells(g, cols, basis)
        assert cells[1].shown is None and cells[1].unavailable_reason == "unknown_vat_base"
        assert cells[0].shown == D("100") and cells[2].unavailable_reason is None

    def test_net_axis_recomputes_shown_per_column_rate(self):
        cols = [col(0, "20"), col(1, "22")]
        basis = ss.pick_tax_basis([c.vat_rate_base for c in cols])
        g = grp(s0=pd.GroupStage(D("120"), 1), s1=pd.GroupStage(D("122"), 1))
        cells = pd.group_cells(g, cols, basis)
        assert cells[0].shown == D("100") and cells[1].shown == D("100")

    def test_removed_then_amount_is_reappeared(self):
        g = grp(s0=pd.GroupStage(D("100"), 1), s1=pd.GroupStage(D("0"), 1), s2=pd.GroupStage(D("100"), 1))
        cells = pd.group_cells(g, COLS4[:3], BASIS)
        assert cells[1].state == "removed"
        assert cells[2].change.kind == "reappeared"

    def test_money_at_is_zero_for_absent_and_zero_states(self):
        g = grp(s0=pd.GroupStage(D("0"), 1), s3=pd.GroupStage(D("90"), 1))
        cells = pd.group_cells(g, COLS4, BASIS)
        assert pd.money_at(cells[0]) == D("0")   # not_evaluated
        assert pd.money_at(cells[1]) == D("0")   # absent
        assert pd.money_at(cells[3]) == D("90")
