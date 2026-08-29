"""Дубль строки итога у одного предложения структурно невозможен в БД (UNIQUE), но
правило «ровно одна конечная строка на предложение» — часть контракта функции, и
его стережёт подменённый результат запроса, а не фикстура."""
from decimal import Decimal

from crud.estimate_totals import estimate_totals_including_vat


class _Result:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _FakeDb:
    def __init__(self, proposals, lines): self._answers = [_Result(proposals), _Result(lines)]
    def execute(self, _stmt): return self._answers.pop(0)


def test_duplicate_summary_line_makes_total_unavailable():
    db = _FakeDb(proposals=[(10, 100)], lines=[(100, Decimal("1200.00")), (100, Decimal("1200.00"))])
    assert estimate_totals_including_vat(db, [10]) == {10: None}


def test_single_line_per_proposal_is_returned():
    db = _FakeDb(proposals=[(10, 100), (10, 101)], lines=[(100, Decimal("5")), (101, Decimal("5"))])
    assert estimate_totals_including_vat(db, [10]) == {10: Decimal("5")}


def test_two_estimates_do_not_leak_into_each_other():
    """Строки обоих запросов ЧЕРЕДУЮТСЯ по сметам: если бы код группировал по
    позиции в списке, а не по фактическому `estimate_id`/`proposal_id`, ответ
    сметы 10 подхватил бы предложение сметы 20 (или наоборот), и обе сметы
    получили бы неверный вердикт. Смета 10 — единогласие (5); смета 20 —
    разногласие (50 против 70) — то есть один и тот же ответ ни для одной
    сметы не получился бы, будь строки перепутаны."""
    db = _FakeDb(
        proposals=[(10, 100), (20, 200), (10, 101), (20, 201)],
        lines=[(200, Decimal("50")), (100, Decimal("5")), (201, Decimal("70")), (101, Decimal("5"))],
    )
    assert estimate_totals_including_vat(db, [10, 20]) == {10: Decimal("5"), 20: None}
