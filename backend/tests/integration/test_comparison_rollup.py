"""Роллап выборки: чтение VIEW, накопление групп, нетто (спека §2.1).

Слой 1 архитектуры сравнения договоров (план, раздел «Архитектура»): один
запрос на всю выборку к `v_category_totals`, накопление строк VIEW по ключу
«смета × статья × источник», перевод в нетто по базе своей группы. Тесты этого
файла проверяют РОВНО эти три вещи плюс постоянство числа запросов — арифметика
дерева (`build_tree`) и союз строк выборки проверяются задачей 3.
"""
from __future__ import annotations

import contextlib
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import comparison as cmp
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration


def _view_rows(db, estimate_id):
    return db.execute(
        sa.text(
            "select work_category_id, source, amount, row_count, rows_with_amount,"
            " rows_not_finite, vat_rate_base from v_category_totals"
            " where estimate_id = :e order by source, work_category_id"
        ),
        {"e": estimate_id},
    ).all()


# ---------------------------------------------------------------------------
#  Step 2: предпосылка фикстур — до всех остальных тестов
# ---------------------------------------------------------------------------

def test_fixture_puts_money_under_the_intended_category(db_session, factories):
    """ПРЕДПОСЫЛКА: сборщик кладёт деньги в статью, а не в «Нераспределённое».

    VIEW берёт статью с РАЗДЕЛА позиции. Если бы сборщик ставил её позиции,
    `work_category_id` в VIEW был бы NULL, и все тесты ниже проверяли бы
    «Нераспределённое», думая, что проверяют статью 1.
    """
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="1", amounts=["100.00"])

    rows = _view_rows(db_session, estimate.id)

    assert len(rows) == 1, f"ожидалась одна группа, получено {len(rows)}"
    assert rows[0].work_category_id == fx.category_id(db_session, "1")
    assert rows[0].vat_rate_base == fx.VAT_20, "ставка НДС обязана быть задана явно"
    assert rows[0].amount == Decimal("100.00")


# ---------------------------------------------------------------------------
#  Step 4: накопление групп VIEW, а не затирание
# ---------------------------------------------------------------------------

def test_view_rows_are_accumulated_not_overwritten(db_session, factories):
    """Две группы VIEW по одной статье складываются, а не затирают друг друга.

    VIEW группируется по `proposal_id`, поэтому смета с ДВУМЯ лотами даёт две
    строки на ту же статью и тот же источник. Присваивание вместо накопления
    потеряло бы первую.

    Замер 2026-08-17: на стенде дубликатов НЕТ (290 строк на 290 ключей, один лот
    на смету) — дефект живыми данными не ловится, фикстура обязана быть
    искусственной.
    """
    estimate = factories.EstimateFactory.create()
    p1 = fx.make_proposal(factories, estimate=estimate, lot_key="lot_a")
    p2 = fx.make_proposal(factories, estimate=estimate, lot_key="lot_b")
    fx.seed_chapter_with_positions(db_session, factories, proposal=p1, code="1", amounts=["100.00"])
    fx.seed_chapter_with_positions(db_session, factories, proposal=p2, code="1", amounts=["200.00"])

    # Предпосылка проверяется В ТЕСТЕ: без двух групп он бы прошёл вакуозно.
    rows = [r for r in _view_rows(db_session, estimate.id) if r.source == "positions"]
    assert len(rows) == 2, "нужны ДВЕ proposal-группы, иначе тест ничего не стережёт"

    rollup = cmp.load_rollups(db_session, [estimate.contract_id])[estimate.contract_id][0]
    branch = rollup.direct[fx.category_id(db_session, "1")][cmp.SOURCE_POSITIONS]

    assert branch.rows == 2
    assert branch.net == cmp.net_of(Decimal("100.00"), fx.VAT_20) + \
                         cmp.net_of(Decimal("200.00"), fx.VAT_20)


# ---------------------------------------------------------------------------
#  Step 5: разделение причин неполноты
# ---------------------------------------------------------------------------

def test_unknown_vat_base_does_not_fake_unpriced(db_session, factories):
    """Цена есть, база НДС неизвестна → ТОЛЬКО vat_base_unknown (спека §2.1.3).

    Обнуление `rows_priced` при неизвестной базе добавляло бы вторую, ложную
    причину `unpriced_rows`; после `build_tree` различить их стало бы нельзя —
    `CategoryNode` причин не несёт.
    """
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate, vat_rate=None)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="1", amounts=["100.00"])

    rows = [r for r in _view_rows(db_session, estimate.id) if r.source == "positions"]
    assert rows[0].vat_rate_base is None, "предпосылка: база НДС неизвестна"
    assert rows[0].rows_with_amount == rows[0].row_count, "предпосылка: цена ЕСТЬ"

    cid = fx.category_id(db_session, "1")
    rollup = cmp.load_rollups(db_session, [estimate.contract_id])[estimate.contract_id][0]
    branch = rollup.direct[cid][cmp.SOURCE_POSITIONS]

    assert branch.rows_priced == branch.rows, "цена есть — счётчик не обнуляется"
    assert branch.rows_vat_base_unknown == branch.rows
    assert rollup.reasons[cid] == frozenset({"vat_base_unknown"})


def test_unpriced_row_is_its_own_reason(db_session, factories):
    """Позиция без цены даёт `unpriced_rows` и НЕ даёт `vat_base_unknown`."""
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="1", amounts=["100.00", None])

    cid = fx.category_id(db_session, "1")
    rollup = cmp.load_rollups(db_session, [estimate.contract_id])[estimate.contract_id][0]

    assert rollup.reasons[cid] == frozenset({"unpriced_rows"})


# ---------------------------------------------------------------------------
#  Step 6: собственные деньги — точное равенство, без вычитания
# ---------------------------------------------------------------------------

def test_own_net_sums_two_sources_exactly(db_session, factories):
    """own = positions + additional_works, ТОЧНОЕ равенство (спека §2.1).

    Допработа ставится на РОДИТЕЛЬСКУЮ статью «3», у которой есть подстатья «3.1»
    с деньгами: именно здесь вычитание «родитель минус дети» дало бы другой ответ,
    и именно это запрещают спека и докстрока `category_rollup`.
    """
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="3", amounts=["500.00"])
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="3.1", amounts=["100.00"])
    fx.seed_additional_work(db_session, factories, proposal=proposal,
                            code="3", amount="200.00")

    cid = fx.category_id(db_session, "3")
    rollup = cmp.load_rollups(db_session, [estimate.contract_id])[estimate.contract_id][0]

    expected = cmp.net_of(Decimal("500.00"), fx.VAT_20) + \
               cmp.net_of(Decimal("200.00"), fx.VAT_20)
    assert cmp.own_net(rollup, cid) == expected


# ---------------------------------------------------------------------------
#  Step 7: постоянное число запросов, независимо от размера выборки
# ---------------------------------------------------------------------------

class _QueryCounter:
    def __init__(self):
        self.total = 0


def _count_queries(session):
    """Считает запросы соединения сессии. Listener СНИМАЕТСЯ в finally.

    Непогашенный listener течёт в следующие тесты и даёт ложные счётчики — тот же
    класс ложно-зелёного, что и непроверенное снятие защиты.
    """

    @contextlib.contextmanager
    def _cm():
        counter = _QueryCounter()
        connection = session.connection()

        def on_execute(conn, cursor, statement, parameters, context, executemany):
            counter.total += 1

        sa.event.listen(connection, "after_cursor_execute", on_execute)
        try:
            yield counter
        finally:
            sa.event.remove(connection, "after_cursor_execute", on_execute)

    return _cm()


def test_query_count_does_not_grow_with_selection(db_session, factories):
    """N+1 запрещён по ВСЕМ входам: категории, сметы, ставки предложений, VIEW.

    Ключ проверки — не абсолютное число, а его НЕИЗМЕННОСТЬ: агрегат на одном
    договоре и на трёх обязан стоить одинаково.
    """
    ids = []
    for _ in range(3):
        estimate = factories.EstimateFactory.create()
        proposal = fx.make_proposal(factories, estimate=estimate)
        fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                       code="1", amounts=["100.00"])
        ids.append(estimate.contract_id)

    with _count_queries(db_session) as c1:
        cmp.load_rollups(db_session, ids[:1])
    with _count_queries(db_session) as c3:
        cmp.load_rollups(db_session, ids)

    assert c1.total == c3.total, (
        f"запросов было {c1.total} на один договор и {c3.total} на три — где-то запрос на смету"
    )
    assert c3.total <= 4, "категории, сметы, ставки предложений, VIEW — по одному"
