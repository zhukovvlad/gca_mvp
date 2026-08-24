"""Классификация единиц измерения правилом ставки статьи — проверка по таблице
(спека 2026-08-24-passport-volumes-design.md §2.5, §2.6; план, задача 1) и
строки-носители статьи запросом, со свёрткой (§2.2, §2.3).

Помощники (`_proposal`, `_chapter`, `_position`, `_category`, `_unit_id`) —
локальные копии из `test_project_passport_api.py`/`test_review_api.py`: наборы
помощников этого проекта друг у друга не импортируют намеренно (докстрока
`test_project_passport_api.py`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud.project_passport import _carrier_rows_by_category
from models import UnitOfMeasure, WorkCategory
from services.article_rates import NON_SCALABLE_UNIT_CODES, SCALABLE_UNIT_CODES

pytestmark = pytest.mark.integration


def test_every_unit_of_the_reference_is_classified_for_scalability(db_session):
    """DoD 11: список неклассифицированных пуст — по ТАБЛИЦЕ, не по сиду.

    Новая единица в справочнике роняет этот тест, а не получает поведение по
    умолчанию: `is_scalable_unit` реализована исключением (§2.5), поэтому без
    этого теста «Литр-2» молча стал бы масштабируемым.
    """
    codes = set(db_session.execute(sa.select(UnitOfMeasure.code)).scalars().all())
    assert codes == SCALABLE_UNIT_CODES | NON_SCALABLE_UNIT_CODES
    assert len(codes) == 9


# ---------------------------------------------------------------------------
#  Локальные помощники (см. докстроку файла — наборы друг у друга не импортируют)
# ---------------------------------------------------------------------------

def _category(session, code: str) -> WorkCategory:
    return session.query(WorkCategory).filter_by(code=code).one()


def _proposal(factories, *, contract=None, amendment_no=None):
    """Полная цепочка договор → смета → лот → предложение."""
    contract = contract or factories.ContractFactory.create()
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
    """Обычная строка. chapter=None оставляет chapter_item_id NULL — строка вне
    структуры разделов."""
    if chapter is not None:
        kwargs.setdefault("chapter_item_id", chapter.id)
    return factories.PositionItemFactory.create(proposal=proposal, **kwargs)


def _unit_id(db_session, code: str) -> int:
    return db_session.query(UnitOfMeasure).filter_by(code=code).one().id


# ---------------------------------------------------------------------------
#  Строки-носители статьи запросом (§2.2, §2.3, DoD 1-4)
# ---------------------------------------------------------------------------

def test_the_rate_comes_from_the_carrier_row_not_from_the_sum_of_the_layers(
    db_session, factories
):
    """DoD 1. Под статьёй лежат слои ОДНОЙ поверхности: подсистема, утеплитель,
    панели. Их объёмы в сумме дают 50 089,00 при объёме строки 19 545,81 —
    пропорция замера 3 спеки (2,5626×).

    Сумма строки выбрана так, чтобы ставка по носителю была ровно 100,00 ₽/ед.
    (19 545,81 × 100). Абсолютных сумм стенда здесь нет: число синтетическое.

    Снятие: взять объём суммой листьев — ставка станет 39,02 и тест краснеет.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    facade = _category(db_session, "6.4")
    carrier = _chapter(
        factories, proposal, category_id=facade.id, smr_article_raw="6.4",
        unit_id=m2, suggested_quantity=Decimal("19545.81"),
        total_cost_total=Decimal("1954581.00"),
    )
    for layer in (Decimal("19545.81"), Decimal("19545.81"), Decimal("10997.38")):
        _position(factories, proposal, chapter=carrier, unit_id=m2,
                  suggested_quantity=layer, total_cost_total=Decimal("100.00"))

    folds = _carrier_rows_by_category(db_session, proposal.lot.estimate_id, None)
    fold = folds[facade.id]
    assert fold.volume == Decimal("19545.81")
    assert fold.amount / fold.volume == Decimal("100")


def test_three_corpus_rows_of_one_article_are_added_up(db_session, factories):
    """DoD 2: три строки с одним кодом (корпуса) — объём и деньги суммой трёх."""
    proposal = _proposal(factories)
    m3 = _unit_id(db_session, "M3")
    article = _category(db_session, "4.1.1")
    for volume, amount in (("1000.00", "100000.00"), ("2000.00", "200000.00"),
                           ("3000.00", "300000.00")):
        _chapter(factories, proposal, category_id=article.id, smr_article_raw="4.1.1",
                 unit_id=m3, suggested_quantity=Decimal(volume),
                 total_cost_total=Decimal(amount))

    fold = _carrier_rows_by_category(db_session, proposal.lot.estimate_id, None)[article.id]
    assert (fold.rows, fold.volume, fold.amount) == (
        3, Decimal("6000.00"), Decimal("600000.00"),
    )


def test_an_article_nested_inside_another_article_keeps_its_own_rate(db_session, factories):
    """DoD 3: код 6.1 лежит внутри строки с кодом 6 — ставку получают ОБЕ.

    Защита от правила первой редакции спеки, которое гасило вложенные ставки.
    Ставки сделаны РАЗНЫМИ (100 и 150), иначе перепутанные местами свёртки
    прошли бы тест.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    outer_cat, inner_cat = _category(db_session, "6"), _category(db_session, "6.1")
    outer = _chapter(factories, proposal, category_id=outer_cat.id, smr_article_raw="6",
                     unit_id=m2, suggested_quantity=Decimal("10.00"),
                     total_cost_total=Decimal("1000.00"))
    _chapter(factories, proposal, category_id=inner_cat.id, smr_article_raw="6.1",
             chapter_item_id=outer.id, unit_id=m2,
             suggested_quantity=Decimal("4.00"), total_cost_total=Decimal("600.00"))

    folds = _carrier_rows_by_category(db_session, proposal.lot.estimate_id, None)
    assert folds[outer_cat.id].amount / folds[outer_cat.id].volume == Decimal("100")
    assert folds[inner_cat.id].amount / folds[inner_cat.id].volume == Decimal("150")


def test_a_row_nested_inside_the_same_article_is_excluded(db_session, factories):
    """DoD 4: строка с кодом A внутри строки с ТЕМ ЖЕ кодом A в объём не входит —
    её деньги уже в объемлющей.

    Объём и сумма вложенной подобраны НЕ пропорционально внешней (2,00 и 500,00
    против 10,00 и 1000,00), иначе задвоение не изменило бы ставку и тест
    остался бы зелёным при снятой защите.

    Снятие: убрать подъём по предкам — объём станет 12,00, ставка 125,00.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    article = _category(db_session, "6")
    outer = _chapter(factories, proposal, category_id=article.id, smr_article_raw="6",
                     unit_id=m2, suggested_quantity=Decimal("10.00"),
                     total_cost_total=Decimal("1000.00"))
    _chapter(factories, proposal, category_id=article.id, smr_article_raw="6",
             chapter_item_id=outer.id, unit_id=m2,
             suggested_quantity=Decimal("2.00"), total_cost_total=Decimal("500.00"))

    fold = _carrier_rows_by_category(db_session, proposal.lot.estimate_id, None)[article.id]
    assert fold.rows == 1
    assert fold.volume == Decimal("10.00")
    assert fold.amount / fold.volume == Decimal("100")


def test_a_row_nested_through_a_codeless_section_of_the_same_article_is_excluded(
    db_session, factories
):
    """DoD 4, вариант через раздел без кода (§2.3): между двумя носителями
    статьи A стоит промежуточный раздел, у которого нет СВОЕЙ статьи вовсе
    (`work_category_id IS NULL`, `category_source IS NULL` — оба поля пусты
    одновременно, как того требует `ck_position_items_category_source_pairs`;
    `_chapter` без `category_id` даёт ровно это). Цепочка предков внутреннего
    носителя идёт inner → промежуточный → outer, и промежуточный НЕ несёт кода
    статьи — если бы подъём строился только по строкам-носителям, а не по всем
    разделам, эта цепочка обрывалась бы на промежуточном узле и внутренний
    носитель остался бы не исключённым.

    Объём и сумма внутреннего носителя подобраны НЕ пропорционально внешнему
    (2,00 и 500,00 против 10,00 и 1000,00) — той же причиной, что у соседнего
    теста прямой вложенности: при пропорциональных числах задвоение не
    изменило бы ставку, и тест остался бы зелёным при снятой защите.

    Снятие: убрать подъём по предкам — объём станет 12,00, ставка 125,00
    (тот же результат, что у прямой вложенности).
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    article = _category(db_session, "6")
    outer = _chapter(factories, proposal, category_id=article.id, smr_article_raw="6",
                     unit_id=m2, suggested_quantity=Decimal("10.00"),
                     total_cost_total=Decimal("1000.00"))
    codeless_section = _chapter(factories, proposal, chapter_item_id=outer.id, unit_id=m2,
                                suggested_quantity=Decimal("5.00"),
                                total_cost_total=Decimal("500.00"))
    _chapter(factories, proposal, category_id=article.id, smr_article_raw="6",
             chapter_item_id=codeless_section.id, unit_id=m2,
             suggested_quantity=Decimal("2.00"), total_cost_total=Decimal("500.00"))

    fold = _carrier_rows_by_category(db_session, proposal.lot.estimate_id, None)[article.id]
    assert fold.rows == 1
    assert fold.volume == Decimal("10.00")
    assert fold.amount / fold.volume == Decimal("100")


def test_a_manually_assigned_section_is_not_a_carrier(db_session, factories):
    """§2.5 состояние 2: у статьи ручного разноса строки с кодом нет вовсе
    (`smr_article_raw IS NULL`), и носителем она не становится."""
    proposal = _proposal(factories)
    article = _category(db_session, "6")
    _chapter(factories, proposal, category_id=article.id,
             unit_id=_unit_id(db_session, "M2"), suggested_quantity=Decimal("10.00"))
    # `_chapter` ставит category_source='file'; smr_article_raw остаётся None.
    assert _carrier_rows_by_category(db_session, proposal.lot.estimate_id, None) == {}
