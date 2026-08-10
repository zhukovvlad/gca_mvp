"""Семантика VIEW v_category_totals (фаза 7, спека Ф6 §1, §2.2).

Проверяем ровно то, что зафиксировано спекой: гранулярность (прямые суммы, без
roll-up), путь позиции до статьи СТРОГО через chapter_item_id (не через номер
раздела), раздельные источники positions/additional_works, различимость «нет
цены» и «есть, но не число» (NaN/Infinity), и что VIEW не фильтрует
amendment_no — это дело CRUD, а не VIEW.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud.project_passport import (
    DECLARED_CATEGORY_TOTALS_COLUMNS,
    SOURCE_ADDITIONAL_WORKS,
    SOURCE_POSITIONS,
)
from models import EstimateAdditionalWork, WorkCategory

pytestmark = pytest.mark.integration


def _category(session, code: str) -> WorkCategory:
    return session.query(WorkCategory).filter_by(code=code).one()


def _proposal(factories, *, amendment_no=None):
    """Полная цепочка договор → смета → лот → предложение."""
    contract = factories.ContractFactory.create()
    estimate = factories.EstimateFactory.create(contract=contract, amendment_no=amendment_no)
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _chapter(factories, proposal, *, category_id=None, chapter_number="1", **kwargs):
    """Строка-раздел. Статья (если задана) обязана нести category_source='file'
    — этого требует ck_position_items_category_source_pairs."""
    kwargs.setdefault("is_chapter", True)
    kwargs.setdefault("chapter_number_in_proposal", chapter_number)
    if category_id is not None:
        kwargs.setdefault("work_category_id", category_id)
        kwargs.setdefault("category_source", "file")
    return factories.PositionItemFactory.create(proposal=proposal, **kwargs)


def _position(factories, proposal, *, chapter=None, **kwargs):
    """Обычная строка. chapter=None оставляет chapter_item_id NULL (граница 8 спеки)."""
    if chapter is not None:
        kwargs.setdefault("chapter_item_id", chapter.id)
    return factories.PositionItemFactory.create(proposal=proposal, **kwargs)


def _additional_work(session, proposal, *, ordinal=1, category_id=None, amount=Decimal("500.00"),
                      title="Дополнительная работа"):
    """EstimateAdditionalWork без фабрики (её нет намеренно, см. бриф задачи).

    Пара CHECK-ов (`ck_estimate_additional_works_unresolved_ref`,
    `ck_estimate_additional_works_raw_line_pairs`) требует: если задана статья,
    то заданы и chapter_ref_raw, и raw_line — все три поля появляются/отсутствуют
    вместе.
    """
    kwargs: dict = dict(proposal_id=proposal.id, ordinal=ordinal, title=title, total_amount=amount)
    if category_id is not None:
        kwargs["work_category_id"] = category_id
        kwargs["chapter_ref_raw"] = "6.1"
        kwargs["raw_line"] = "исходная строка сведений"
    work = EstimateAdditionalWork(**kwargs)
    session.add(work)
    session.flush()
    return work


def _rows_for_estimate(session, estimate_id: int) -> list[sa.RowMapping]:
    return list(
        session.execute(
            sa.text(
                "SELECT * FROM v_category_totals WHERE estimate_id = :id "
                "ORDER BY source, work_category_id NULLS FIRST"
            ),
            {"id": estimate_id},
        ).mappings()
    )


def _row(rows: list[sa.RowMapping], *, work_category_id, source: str) -> sa.RowMapping:
    matches = [r for r in rows if r["work_category_id"] == work_category_id and r["source"] == source]
    assert len(matches) == 1, (work_category_id, source, rows)
    return matches[0]


# ---------------------------------------------------------------------------
#  Отражение VIEW в Python не должно разъезжаться с самим VIEW
# ---------------------------------------------------------------------------

def test_declared_view_columns_match_the_database(db_session):
    """`crud.project_passport.CATEGORY_TOTALS` — объявление руками, VIEW создан
    raw SQL в 0010. `alembic check` их не сверяет (VIEW не в `Base.metadata`)."""
    actual = tuple(
        db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'v_category_totals' ORDER BY ordinal_position"
            )
        ).scalars()
    )
    assert actual == DECLARED_CATEGORY_TOTALS_COLUMNS


# ---------------------------------------------------------------------------
#  Гранулярность: прямые суммы, не roll-up
# ---------------------------------------------------------------------------

def test_chapter_rows_do_not_enter_the_sums(db_session, factories):
    """Деньги, записанные в самой строке-разделе, в сумму статьи не входят —
    сумма статьи это сумма её ПОЗИЦИЙ (спека §2.2), и раздел не считается строкой."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal, category_id=category.id, total_cost_total=Decimal("999.00"))
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1000.00"))
    db_session.flush()

    rows = _rows_for_estimate(db_session, proposal.lot.estimate.id)
    row = _row(rows, work_category_id=category.id, source=SOURCE_POSITIONS)
    assert row["amount"] == Decimal("1000.00")
    assert row["row_count"] == 1


def test_position_under_a_chapter_without_a_category_falls_into_null(db_session, factories):
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal)  # без статьи
    position = _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1000.00"))
    db_session.flush()

    rows = _rows_for_estimate(db_session, proposal.lot.estimate.id)
    row = _row(rows, work_category_id=None, source=SOURCE_POSITIONS)
    assert row["amount"] == position.total_cost_total


def test_position_without_a_chapter_reference_falls_into_null(db_session, factories):
    """chapter_item_id=None — синтетическая граница 8 спеки, недостижима из
    реальных файлов на парсере 2.0.0, но VIEW обязан вести себя предсказуемо."""
    proposal = _proposal(factories)
    position = _position(factories, proposal, total_cost_total=Decimal("1000.00"))
    db_session.flush()

    rows = _rows_for_estimate(db_session, proposal.lot.estimate.id)
    row = _row(rows, work_category_id=None, source=SOURCE_POSITIONS)
    assert row["amount"] == position.total_cost_total


# ---------------------------------------------------------------------------
#  Источники не смешиваются
# ---------------------------------------------------------------------------

def test_additional_works_come_with_their_own_source(db_session, factories):
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal, category_id=category.id)
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1000.00"))
    _additional_work(db_session, proposal, category_id=category.id, amount=Decimal("500.00"))
    db_session.flush()

    rows = _rows_for_estimate(db_session, proposal.lot.estimate.id)
    positions_row = _row(rows, work_category_id=category.id, source=SOURCE_POSITIONS)
    additional_row = _row(rows, work_category_id=category.id, source=SOURCE_ADDITIONAL_WORKS)
    assert positions_row["amount"] == Decimal("1000.00")
    assert additional_row["amount"] == Decimal("500.00")


def test_additional_work_without_a_category_falls_into_null(db_session, factories):
    proposal = _proposal(factories)
    _additional_work(db_session, proposal, amount=Decimal("500.00"))
    db_session.flush()

    rows = _rows_for_estimate(db_session, proposal.lot.estimate.id)
    row = _row(rows, work_category_id=None, source=SOURCE_ADDITIONAL_WORKS)
    assert row["amount"] == Decimal("500.00")


# ---------------------------------------------------------------------------
#  Ключевой страж: путь только через chapter_item_id
# ---------------------------------------------------------------------------

def test_duplicate_chapter_numbers_do_not_double_the_money(db_session, factories):
    """Join по номеру раздела запрещён (спека §1.5): два раздела с одним и тем
    же chapter_number_in_proposal, но разными статьями, не должны слить деньги
    в одну сумму — единственный путь к статье это chapter_item_id."""
    category_6 = _category(db_session, "6")
    category_7 = _category(db_session, "7")
    proposal = _proposal(factories)
    chapter_a = _chapter(factories, proposal, category_id=category_6.id, chapter_number="6")
    chapter_b = _chapter(factories, proposal, category_id=category_7.id, chapter_number="6")
    _position(factories, proposal, chapter=chapter_a, total_cost_total=Decimal("1000.00"))
    _position(factories, proposal, chapter=chapter_b, total_cost_total=Decimal("2000.00"))
    db_session.flush()

    rows = _rows_for_estimate(db_session, proposal.lot.estimate.id)
    row_6 = _row(rows, work_category_id=category_6.id, source=SOURCE_POSITIONS)
    row_7 = _row(rows, work_category_id=category_7.id, source=SOURCE_POSITIONS)
    assert row_6["amount"] == Decimal("1000.00")
    assert row_6["row_count"] == 1
    assert row_7["amount"] == Decimal("2000.00")
    assert row_7["row_count"] == 1
    assert not any(r["amount"] == Decimal("3000.00") for r in rows)


# ---------------------------------------------------------------------------
#  «Нет цены» ≠ «есть, но не число» — два разных счётчика
# ---------------------------------------------------------------------------

def test_amount_is_null_exactly_when_no_row_entered_it(db_session, factories):
    category_a = _category(db_session, "1")
    category_b = _category(db_session, "2")
    proposal = _proposal(factories)
    chapter_a = _chapter(factories, proposal, category_id=category_a.id)
    chapter_b = _chapter(factories, proposal, category_id=category_b.id)
    _position(factories, proposal, chapter=chapter_a, total_cost_total=Decimal("1000.00"))
    _position(factories, proposal, chapter=chapter_b, total_cost_total=None)
    db_session.flush()

    rows = _rows_for_estimate(db_session, proposal.lot.estimate.id)
    row_a = _row(rows, work_category_id=category_a.id, source=SOURCE_POSITIONS)
    row_b = _row(rows, work_category_id=category_b.id, source=SOURCE_POSITIONS)

    assert row_a["amount"] is not None
    assert row_a["rows_with_amount"] == 1

    assert row_b["amount"] is None
    assert row_b["rows_with_amount"] == 0
    assert row_b["row_count"] == 1


def test_row_without_a_price_counts_but_does_not_enter_the_amount(db_session, factories):
    """Пустая цена — законное состояние файла, не порча (в отличие от NaN/Infinity)."""
    proposal = _proposal(factories)
    _position(factories, proposal, total_cost_total=None)
    db_session.flush()

    rows = _rows_for_estimate(db_session, proposal.lot.estimate.id)
    row = _row(rows, work_category_id=None, source=SOURCE_POSITIONS)
    assert row["row_count"] == 1
    assert row["rows_with_amount"] == 0
    assert row["rows_not_finite"] == 0
    assert row["amount"] is None


# ---------------------------------------------------------------------------
#  Годность значения: NaN/Infinity не портят сумму
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_value_does_not_poison_the_aggregate(db_session, factories, bad):
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal, category_id=category.id)
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1000.00"))
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("2000.00"))
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal(bad))
    db_session.flush()

    rows = _rows_for_estimate(db_session, proposal.lot.estimate.id)
    row = _row(rows, work_category_id=category.id, source=SOURCE_POSITIONS)
    assert row["amount"] == Decimal("3000.00")
    assert row["amount"].is_finite()
    assert row["row_count"] == 3
    assert row["rows_with_amount"] == 2
    assert row["rows_not_finite"] == 1


def test_opposite_infinities_do_not_poison_the_aggregate(db_session, factories):
    """+Infinity и -Infinity вместе дают NaN даже вдвоём — без фильтра эта пара
    «правдоподобных» слагаемых отравила бы сумму сама по себе."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal, category_id=category.id)
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("Infinity"))
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("-Infinity"))
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1000.00"))
    db_session.flush()

    rows = _rows_for_estimate(db_session, proposal.lot.estimate.id)
    row = _row(rows, work_category_id=category.id, source=SOURCE_POSITIONS)
    assert row["amount"] == Decimal("1000.00")
    assert row["amount"].is_finite()
    assert row["row_count"] == 3
    assert row["rows_with_amount"] == 1
    assert row["rows_not_finite"] == 2


# ---------------------------------------------------------------------------
#  amendment_no — забота CRUD, не VIEW (спека §2.2)
# ---------------------------------------------------------------------------

def test_view_does_not_filter_amendment_no(db_session, factories):
    proposal = _proposal(factories, amendment_no=1)
    chapter = _chapter(factories, proposal)
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1000.00"))
    db_session.flush()

    estimate_id = proposal.lot.estimate.id
    rows = _rows_for_estimate(db_session, estimate_id)
    assert rows
    assert all(r["estimate_id"] == estimate_id for r in rows)
