"""Классификация единиц измерения правилом ставки статьи — проверка по таблице
(спека 2026-08-24-passport-volumes-design.md §2.5, §2.6; план, задача 1) и
строки-носители статьи запросом, со свёрткой (§2.2, §2.3).

Помощники (`_proposal`, `_chapter`, `_position`, `_category`, `_unit_id`) —
локальные копии из `test_project_passport_api.py`/`test_review_api.py`: наборы
помощников этого проекта друг у друга не импортируют намеренно (докстрока
`test_project_passport_api.py`).
"""

from __future__ import annotations

import decimal
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud.project_passport import (
    MoneyShareState,
    _carrier_rows_by_category,
    _rate_coverage,
    get_project_passport,
)
from models import EstimateAdditionalWork, UnitOfMeasure, WorkCategory
from services.article_rates import (
    NON_SCALABLE_UNIT_CODES,
    SCALABLE_UNIT_CODES,
    ArticleRate,
    RateState,
    fold_carrier_rows,
)
from services.category_rollup import CategoryNode, CategoryRef

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


def _additional_work(session, proposal, *, ordinal=1, category_id, amount=Decimal("500.00"),
                      title="Дополнительная работа"):
    """`EstimateAdditionalWork` без фабрики — та же причина, что в
    `test_project_passport_api.py::_additional_work` (локальная копия, не
    импорт: наборы помощников этого проекта друг у друга не импортируют,
    докстрока файла). Пара CHECK-ов (`ck_estimate_additional_works_
    unresolved_ref`, `ck_estimate_additional_works_raw_line_pairs`) требует:
    если задана статья, то заданы и `chapter_ref_raw`, и `raw_line`."""
    work = EstimateAdditionalWork(
        proposal_id=proposal.id, ordinal=ordinal, title=title, total_amount=amount,
        work_category_id=category_id, chapter_ref_raw="6.1", raw_line="исходная строка сведений",
    )
    session.add(work)
    session.flush()
    return work


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


# ---------------------------------------------------------------------------
#  Пять полей ставки на каждом узле свода (§2.2-§2.5, §2.10, задача 4)
# ---------------------------------------------------------------------------

def test_every_category_node_carries_exactly_one_rate_state(db_session, factories):
    """§2.5: состояние обязательно у КАЖДОГО узла свода, включая корни."""
    proposal = _proposal(factories)
    _position(factories, proposal, chapter=_chapter(
        factories, proposal, category_id=_category(db_session, "6").id, smr_article_raw="6",
    ))
    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    states = {node["rate_state"] for node in passport["categories"]}
    assert states  # узлы есть
    assert states <= {state.value for state in RateState}
    assert all(node["rate_state"] is not None for node in passport["categories"])


def test_a_root_without_a_carrier_is_no_carrier_without_a_note(db_session, factories):
    """DoD 6 на уровне ответа: клетка пуста, пометки нет."""
    proposal = _proposal(factories)
    _position(factories, proposal)
    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    root = next(n for n in passport["categories"] if n["code"] == "6")
    assert root["rate_state"] == "no_carrier"
    assert (root["rate_note"], root["unit"], root["volume"], root["unit_rate"]) == (
        None, None, None, None,
    )


def test_every_contract_state_is_reachable_by_a_fixture_of_this_file(db_session, factories):
    """DoD 16. Утверждение о ПОКРЫТИИ, а не о конкретном ответе: набор состояний,
    достигнутых фикстурами этого файла, обязан совпасть со списком контракта.

    Реализация: фикстура строит по одному узлу на каждое состояние (девять плюс
    `rate`) в ОДНОЙ смете и сверяет множество `rate_state` в ответе со списком
    §2.10. Иначе контракт снова разойдётся с автоматом — ровно так рассыпалась
    первая редакция спеки.

    Все девять «тупиковых» состояний посажены на КОРНИ классификатора: корень
    виден в ответе всегда (`build_tree`), независимо от строк-позиций, а
    ставка статьи читается со строки-раздела (носителя), а не с позиций —
    значит для каждого состояния достаточно одной-двух строк-носителей под
    своим корнем, без единой обычной позиции. Только `volume_inconsistent`
    вдобавок требует РЕБЁНКА в дереве КЛАССИФИКАТОРА (не в видимом дереве
    паспорта — ребёнок в ответе может и не появиться, участие в свёртке
    родителя от видимости не зависит, см. докстроку `_article_rates`):
    родитель "11" получает перебор объёма от дочерней статьи "11.1", у которой
    объём (20,00) больше объёма родителя (10,00) сверх допуска.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    m3 = _unit_id(db_session, "M3")
    non_scalable = _unit_id(db_session, "SET")

    # 1. additional_works — доп. работа есть, носителя нет.
    _additional_work(db_session, proposal, category_id=_category(db_session, "1").id)

    # 2. no_carrier — корень "2" не тронут вовсе: ни строки, ни допработ.

    # 3. amount_missing — у одной строки статьи нет суммы, у другой есть.
    amount_missing = _category(db_session, "3")
    _chapter(factories, proposal, category_id=amount_missing.id, smr_article_raw="3",
              unit_id=m2, suggested_quantity=Decimal("10.00"), total_cost_total=None)
    _chapter(factories, proposal, category_id=amount_missing.id, smr_article_raw="3",
              unit_id=m2, suggested_quantity=Decimal("5.00"), total_cost_total=Decimal("500.00"))

    # 4. unit_missing — unit_id пуст у строки-носителя.
    unit_missing = _category(db_session, "4")
    _chapter(factories, proposal, category_id=unit_missing.id, smr_article_raw="4",
              unit_id=None, suggested_quantity=Decimal("10.00"), total_cost_total=Decimal("100.00"))

    # 5. unit_conflict — две строки статьи с разными единицами.
    unit_conflict = _category(db_session, "5")
    _chapter(factories, proposal, category_id=unit_conflict.id, smr_article_raw="5",
              unit_id=m2, suggested_quantity=Decimal("10.00"), total_cost_total=Decimal("100.00"))
    _chapter(factories, proposal, category_id=unit_conflict.id, smr_article_raw="5",
              unit_id=m3, suggested_quantity=Decimal("5.00"), total_cost_total=Decimal("50.00"))

    # 6. unit_not_scalable — единица «Комплект».
    unit_not_scalable = _category(db_session, "8")
    _chapter(factories, proposal, category_id=unit_not_scalable.id, smr_article_raw="8",
              unit_id=non_scalable, suggested_quantity=Decimal("1.00"), total_cost_total=Decimal("100.00"))

    # 7. volume_missing — ни quantity, ни suggested_quantity не заданы.
    volume_missing = _category(db_session, "9")
    _chapter(factories, proposal, category_id=volume_missing.id, smr_article_raw="9",
              unit_id=m2, quantity=None, suggested_quantity=None, total_cost_total=Decimal("100.00"))

    # 8. volume_nonpositive — объём не строго положителен.
    volume_nonpositive = _category(db_session, "10")
    _chapter(factories, proposal, category_id=volume_nonpositive.id, smr_article_raw="10",
              unit_id=m2, suggested_quantity=Decimal("0.00"), total_cost_total=Decimal("100.00"))

    # 9. volume_inconsistent — перебор: дочерняя статья несёт больше объёма,
    #    чем родитель (единица общая, разница больше допуска ε = 0,01 × n).
    inconsistent_parent = _category(db_session, "11")
    inconsistent_child = _category(db_session, "11.1")
    _chapter(factories, proposal, category_id=inconsistent_parent.id, smr_article_raw="11",
              unit_id=m2, suggested_quantity=Decimal("10.00"), total_cost_total=Decimal("1000.00"))
    _chapter(factories, proposal, category_id=inconsistent_child.id, smr_article_raw="11.1",
              unit_id=m2, suggested_quantity=Decimal("20.00"), total_cost_total=Decimal("2000.00"))

    # 10. rate — годная статья без единой преграды.
    rate_ok = _category(db_session, "12")
    _chapter(factories, proposal, category_id=rate_ok.id, smr_article_raw="12",
              unit_id=m2, suggested_quantity=Decimal("10.00"), total_cost_total=Decimal("1000.00"))

    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    observed = {node["rate_state"] for node in passport["categories"]}
    assert observed == {state.value for state in RateState}


def test_the_rate_follows_the_display_axis(db_session, factories):
    """DoD 21. База 20 %, цель 22 %: ставка отличается от «сырой» на тот же
    множитель, что и суммы рядом.

    1 200,00 при базе 20 % и объёме 10 → сырая ставка 120,00; при цели 22 %:
    1200/1,20×1,22 = 1220 → ставка 122,00.

    Снятие: считать от исходной суммы (не звать `restate_gross`) — тест краснеет.
    """
    contract = factories.ContractFactory.create()
    estimate = factories.EstimateFactory.create(contract=contract)
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(
        lot=lot, contractor=contract.contractor, vat_rate=Decimal("20"),
    )
    article = _category(db_session, "6")
    _chapter(
        factories, proposal, category_id=article.id, smr_article_raw="6",
        unit_id=_unit_id(db_session, "M2"),
        suggested_quantity=Decimal("10"), total_cost_total=Decimal("1200.00"),
    )
    estimate.vat_rate_target = Decimal("22")
    db_session.flush()

    passport = get_project_passport(db_session, contract.id)
    node = next(n for n in passport["categories"] if n["code"] == "6")

    raw_rate = Decimal("1200") / Decimal("10")
    assert Decimal(node["unit_rate"]) == Decimal("122")
    assert Decimal(node["unit_rate"]) / raw_rate == Decimal("1220") / Decimal("1200")


def test_the_route_and_the_response_keys_of_the_feature(client, db_session, factories):
    """DoD 27: маршрут действующий, новых нет; пять полей на месте, плюс
    `rate_coverage` на верхнем уровне ответа (задача 5, §2.10)."""
    contract = factories.ContractFactory.create()

    response = client.get(f"/api/v1/analytics/project-passport/{contract.id}")
    assert response.status_code == 200
    body = response.json()
    node = body["categories"][0]
    assert {"unit", "volume", "unit_rate", "rate_state", "rate_note"} <= node.keys()
    assert "rate_coverage" in body


def test_a_contract_without_an_estimate_keeps_the_same_response_shape(db_session, factories):
    """Правило 8 `get_project_passport`: форма ответа одна на оба пути функции.

    `rate_coverage` на пути без сметы (задача 5): без знаменателя первая же
    проверка `_rate_coverage` отдаёт `total_unavailable`, а не `no_articles` —
    порядок проверок §2.8 ставит отсутствие итога раньше пустого набора."""
    contract = factories.ContractFactory.create()
    passport = get_project_passport(db_session, contract.id)
    assert all(n["rate_state"] == "no_carrier" for n in passport["categories"])
    assert passport["rate_coverage"] == {
        "articles_with_rate": 0, "articles_total": 0,
        "money_share": None, "money_share_state": "total_unavailable",
    }


# ---------------------------------------------------------------------------
#  Охват: неперекрывающийся набор статей и доля денег (§2.8, §2.10, задача 5)
# ---------------------------------------------------------------------------

def test_articles_total_counts_the_non_overlapping_set_of_this_estimate(
    db_session, factories
):
    """DoD 23: корень без носителя и две названные статьи под ним → M = 2.
    Не число строк дерева и не размер справочника."""
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    for code in ("6.1", "6.2"):
        chapter = _chapter(factories, proposal, category_id=_category(db_session, code).id,
                 smr_article_raw=code, unit_id=m2,
                 suggested_quantity=Decimal("10.00"), total_cost_total=Decimal("1000.00"))
        # Собственная позиция обязательна: `v_category_totals` (миграция 0010)
        # считает только `is_chapter = false` строки, и без неё узел не набрал
        # бы `rows > 0` — `build_tree` спрятал бы его, и он не появился бы ни
        # в `categories`, ни в `nodes`, которыми считает `_rate_coverage`.
        _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1.00"))
    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert passport["rate_coverage"]["articles_total"] == 2


def test_the_coverage_set_is_independent_of_how_many_rates_the_screen_shows(
    db_session, factories
):
    """DoD 5: на фикстуре вложенных кодов ставок ДВЕ, а статей в наборе ОДНА.

    Та же фикстура, что у DoD 3
    (`test_an_article_nested_inside_another_article_keeps_its_own_rate`): код
    6.1 лежит внутри строки с кодом 6, обе получают ставку (100 и 150) — но
    6 несёт потомка-статью с носителем (6.1), и в набор охвата не входит.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    outer_cat, inner_cat = _category(db_session, "6"), _category(db_session, "6.1")
    outer = _chapter(factories, proposal, category_id=outer_cat.id, smr_article_raw="6",
                     unit_id=m2, suggested_quantity=Decimal("10.00"),
                     total_cost_total=Decimal("1000.00"))
    inner = _chapter(factories, proposal, category_id=inner_cat.id, smr_article_raw="6.1",
                     chapter_item_id=outer.id, unit_id=m2,
                     suggested_quantity=Decimal("4.00"), total_cost_total=Decimal("600.00"))
    # Обе статьи обязаны иметь СОБСТВЕННУЮ позицию (не только строку-носитель):
    # `v_category_totals` считает только `is_chapter = false` строки (миграция
    # `0010`), и без этого узел не набрал бы `rows > 0` и не попал бы в
    # видимое дерево паспорта (`build_tree`) вовсе — тогда на экране не было
    # бы ни одной из двух ставок, которые проверяет этот тест.
    _position(factories, proposal, chapter=outer, total_cost_total=Decimal("1.00"))
    _position(factories, proposal, chapter=inner, total_cost_total=Decimal("1.00"))

    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert len([n for n in passport["categories"] if n["rate_state"] == "rate"]) == 2
    assert passport["rate_coverage"]["articles_total"] == 1
    assert passport["rate_coverage"]["articles_with_rate"] == 1


def test_money_share_never_exceeds_a_hundred_on_nested_codes(db_session, factories):
    """DoD 24. Фикстура: раздел с кодом 6 (носитель 1 000,00 / 10,00 м²), внутри
    него раздел с кодом 6.1 (носитель 600,00 / 6,00 м²); позиции под 6 напрямую —
    400,00, позиции под 6.1 — 600,00. Итог паспорта (из позиций) = 1 000,00.

    Набор = {6.1}, её `node.total` = 600,00 → K = 60. Снятие «считать по всем
    статьям с носителем» берёт и 6, и 6.1: `node.total` шестой — итог всего её
    поддерева, то есть 1 000,00, плюс 600,00 у 6.1 → 160 %. Деньги вложенной
    статьи учтены дважды — на первой редакции спеки так и вышло, 110,7 %.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    outer_cat, inner_cat = _category(db_session, "6"), _category(db_session, "6.1")
    outer = _chapter(factories, proposal, category_id=outer_cat.id, smr_article_raw="6",
                     unit_id=m2, suggested_quantity=Decimal("10.00"),
                     total_cost_total=Decimal("1000.00"))
    inner = _chapter(factories, proposal, category_id=inner_cat.id, smr_article_raw="6.1",
                     chapter_item_id=outer.id, unit_id=m2,
                     suggested_quantity=Decimal("6.00"), total_cost_total=Decimal("600.00"))
    _position(factories, proposal, chapter=outer, total_cost_total=Decimal("400.00"))
    _position(factories, proposal, chapter=inner, total_cost_total=Decimal("600.00"))

    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert Decimal(passport["rate_coverage"]["money_share"]) == Decimal("60")


def test_money_share_does_not_move_when_the_display_target_changes(
    db_session, factories
):
    """DoD 22: числитель и знаменатель приведены одинаково — доля инвариантна.
    Это и есть проверка того, что ось ОДНА (§2.7).

    Статья со ставкой несёт 600,00 своих позиций, «Нераспределённое» — 300,00
    (носитель статьи 10,00 м² / 1 000,00, база НДС 20 %). Оба слагаемых —
    кратные 3, поэтому `gross_to_net` (§2.2, `money/vat.py`) делит их НАЦЕЛО
    и на цели показа 22 % не оставляет остатка округления по отдельным
    строкам VIEW — иначе двойное независимое округление числителя и
    знаменателя (задача 8, `_direct_totals`) могло бы разъехись в последнем
    знаке `prec = 100` и превратить точное равенство в `59,999...998 != 60`.
    """
    contract = factories.ContractFactory.create()
    estimate = factories.EstimateFactory.create(contract=contract)
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(
        lot=lot, contractor=contract.contractor, vat_rate=Decimal("20"),
    )
    m2 = _unit_id(db_session, "M2")
    article = _category(db_session, "6")
    chapter = _chapter(factories, proposal, category_id=article.id, smr_article_raw="6",
                        unit_id=m2, suggested_quantity=Decimal("10.00"),
                        total_cost_total=Decimal("1000.00"))
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("600.00"))
    _position(factories, proposal, total_cost_total=Decimal("300.00"))

    before = get_project_passport(db_session, contract.id)
    estimate.vat_rate_target = Decimal("22")
    db_session.flush()
    after = get_project_passport(db_session, contract.id)

    assert before["rate_coverage"]["money_share"] is not None
    assert Decimal(after["rate_coverage"]["money_share"]) == Decimal(
        before["rate_coverage"]["money_share"]
    )


def test_money_share_is_null_when_the_share_leaves_the_zero_to_hundred_range(
    db_session, factories
):
    """DoD 32. Антицепь держит границу только при неотрицательных суммах, а
    `CHECK`-а на знак `position_items.total_cost_total` в схеме нет.

    Фикстура: статья 6.1 в наборе (носитель 600,00 / 6,00 м², позиции 600,00) и
    ВТОРАЯ статья 7 вне набора, у которой позиция несёт ОТРИЦАТЕЛЬНУЮ сумму
    -400,00. Итог паспорта = 600,00 - 400,00 = 200,00; покрыто 600,00. Отношение
    300 % — за диапазоном.

    Ожидание — `None`, а не 300 и не срезанные 100: выход за диапазон означает
    либо отрицательные суммы, либо ошибку построения набора, и печатать по нему
    процент нельзя. Знак суммы фикстура задаёт прямо — это единственный способ
    воспроизвести случай, схема его не запрещает.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    article = _category(db_session, "6.1")
    carrier = _chapter(factories, proposal, category_id=article.id, smr_article_raw="6.1",
                       unit_id=m2, suggested_quantity=Decimal("6.00"),
                       total_cost_total=Decimal("600.00"))
    _position(factories, proposal, chapter=carrier, total_cost_total=Decimal("600.00"))

    outside = _category(db_session, "7")
    outside_chapter = _chapter(factories, proposal, category_id=outside.id,
                               unit_id=m2, suggested_quantity=Decimal("5.00"))
    _position(factories, proposal, chapter=outside_chapter, total_cost_total=Decimal("-400.00"))

    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert passport["rate_coverage"]["money_share"] is None
    assert passport["rate_coverage"]["money_share_state"] == "out_of_range"
    assert passport["rate_coverage"]["articles_with_rate"] == 1   # набор цел


def test_a_negative_denominator_is_out_of_range_before_the_division(
    db_session, factories
):
    """DoD 32, вторая фикстура — случай, который проверка ДИАПАЗОНА не ловит.

    ДВЕ позиции, обе с отрицательной суммой: -50,00 под статьёй 6.1 (у неё есть
    носитель, статья попадает в набор и получает ставку) и -50,00
    нераспределённая, вне набора. Тогда `covered = -50,00`, а итог паспорта
    считает обе — `grand_total = -100,00`, потому что знаменатель включает
    «Нераспределённое» (докстрока `_share_pct`).

    Отношение -50 / -100 * 100 = ровно 50 % — формально допустимый процент по
    бессмысленным числам. Знаки сократились при делении, поэтому проверка
    РЕЗУЛЬТАТА его пропускает: знак знаменателя обязан смотреться ДО деления
    (проверка 3, а не 4). Вторая позиция здесь не декорация — без неё
    `covered = grand_total` и отношение было бы 100 %, то есть тоже внутри
    диапазона, но по совпадению, а не по механизму.

    Ожидание — `out_of_range`. Фикстура задаёт знак прямо: схема его не
    запрещает (`CHECK`-а на `total_cost_total` нет), а иначе случай недостижим.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    article = _category(db_session, "6.1")
    carrier = _chapter(factories, proposal, category_id=article.id, smr_article_raw="6.1",
                       unit_id=m2, suggested_quantity=Decimal("6.00"),
                       total_cost_total=Decimal("600.00"))
    _position(factories, proposal, chapter=carrier, total_cost_total=Decimal("-50.00"))
    _position(factories, proposal, total_cost_total=Decimal("-50.00"))

    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert passport["rate_coverage"]["money_share"] is None
    assert passport["rate_coverage"]["money_share_state"] == "out_of_range"


def test_a_partial_article_total_does_not_kill_the_money_share(db_session, factories):
    """DoD 33. Внутри статьи со ставкой одна позиция БЕЗ суммы
    (`total_cost_total = None`): итог статьи частичный, `rows_priced < rows`.

    K обязан остаться числом. Только известные деньги считают ОБЕ части дроби —
    ровно как все прочие доли паспорта, — поэтому доля остаётся согласованной, а
    не становится ложной. Обратное решение (гасить K на любой непросчитанной
    позиции) обнулило бы замеры §1.1 на живых сметах, где такие строки есть.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    article = _category(db_session, "6")
    chapter = _chapter(factories, proposal, category_id=article.id, smr_article_raw="6",
                        unit_id=m2, suggested_quantity=Decimal("10.00"),
                        total_cost_total=Decimal("1000.00"))
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("600.00"))
    _position(factories, proposal, chapter=chapter, total_cost_total=None)

    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert passport["rate_coverage"]["money_share"] is not None
    assert passport["rate_coverage"]["money_share_state"] == "partial"


def test_a_non_empty_set_without_a_single_rate_is_a_real_zero(db_session, factories):
    """DoD 25, первая фикстура: статьи названы, но ни одна не дала ставки (объёма
    в смете нет), а суммы паспорта известны все.

    Ожидание — `money_share = 0` и состояние `complete`: «покрыто 0 %» правда, и
    её надо напечатать. Прежняя редакция плана отдавала здесь `None`.

    Фикстура доказывает доменное правило «ноль — не отсутствие охвата», но НЕ
    воспроизводит два договора стенда с нулём (§1.1): у них состояние зависит от
    `positions_rows_priced`/`positions_rows`, при неполных суммах ноль придёт как
    `partial`. Счётчики замеряет задача 9, шаг 1 — до замера привязывать фикстуру
    к стенду нельзя.

    Собственная позиция под носителем 6.1 обязательна: `v_category_totals`
    считает только `is_chapter = false` строки (миграция 0010), и без неё
    статья осталась бы без строк (`rows = 0`) и `build_tree` спрятал бы её —
    тогда набор охвата не увидел бы узел вовсе, вместо того чтобы честно
    посчитать его без ставки.

    `quantity=None` ЯВНО, а не только `suggested_quantity=None`: объём
    носителя читается через `COALESCE(suggested_quantity, quantity)`
    (`_carrier_rows_select`), а `PositionItemFactory` даёт `quantity`
    ненулевое значение по умолчанию (`Decimal("1")`) — без этой явной
    перезаписи `COALESCE` тихо подставил бы его, и статья получила бы
    `RATE` вместо `volume_missing`.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    chapter = _chapter(factories, proposal, category_id=_category(db_session, "6.1").id,
             smr_article_raw="6.1", unit_id=m2,
             quantity=None, suggested_quantity=None, total_cost_total=Decimal("1000.00"))
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1000.00"))
    _position(factories, proposal, total_cost_total=Decimal("1000.00"))
    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert passport["rate_coverage"] == {
        "articles_with_rate": 0, "articles_total": 1,
        "money_share": Decimal("0"), "money_share_state": "complete",
    }


def test_an_empty_set_is_no_articles_not_a_zero(db_session, factories):
    """DoD 25, вторая фикстура: смета не называет ни одной статьи. Мерить нечего —
    состояние `no_articles`, доля `None`. «0 %» здесь было бы утверждением о
    покрытии, которого никто не считал."""
    proposal = _proposal(factories)
    _position(factories, proposal)
    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert passport["rate_coverage"] == {
        "articles_with_rate": 0, "articles_total": 0,
        "money_share": None, "money_share_state": "no_articles",
    }


def test_the_state_enum_carries_exactly_the_five_contract_values():
    """DoD 34: перечень §2.8 и контракт §2.10 — один список."""
    assert {state.value for state in MoneyShareState} == {
        "complete", "partial", "no_articles", "total_unavailable", "out_of_range",
    }


def test_money_share_computation_survives_an_inexact_trap_in_the_ambient_context():
    """Тест на явный контекст (Global Constraint 16, докстрока `_rate_coverage`).

    `_share_pct` делит в AMBIENT-контексте, и `covered` копится под явным
    `localcontext(_RATE_CONTEXT)` (трапы: `Overflow`, `DivisionByZero`,
    `InvalidOperation` — БЕЗ `Inexact`). Этот тест взводит `Inexact` в
    AMBIENT-контексте (той же нити, что видит `_share_pct` СНАРУЖИ явного
    контекста) и делит 100,00 на 300,00 — частное `33,333...%` не представимо
    конечной десятичной дробью НИ при каком `prec`, и деление обязано вызвать
    `decimal.Inexact` без защиты. Вызов идёт напрямую в `_rate_coverage`, а не
    в `get_project_passport`: у КАЖДОЙ строки паспорта своя доля `share_pct`
    через ТУ ЖЕ незащищённую `_share_pct`, и на цельном паспорте пришлось бы
    доказывать, что все они тоже делятся нацело — а это не то, что проверяет
    этот тест. Без `with localcontext(_RATE_CONTEXT)` внутри `_rate_coverage`
    этот же вызов уронил бы `decimal.Inexact`; тест намеренно ставит трап
    ДО вызова, чтобы падение (при регрессии) было видно здесь, а не потерялось
    в шуме интеграционного теста.
    """
    node = CategoryNode(
        ref=CategoryRef(id=1, code="6", title="Статья", parent_id=None, is_bucket=False, sort_order=1),
        total=Decimal("100.00"), rows=1, rows_priced=1, rows_not_finite=0,
        own=Decimal("100.00"), own_rows=1, own_rows_priced=1, own_rows_not_finite=0,
        children=(),
    )
    rate = ArticleRate(
        state=RateState.RATE, note=None, unit="м²", volume=Decimal("10.00"), unit_rate=Decimal("10.00"),
    )
    folds = {1: fold_carrier_rows([], None)}
    totals = {"positions_rows": 1, "positions_rows_priced": 1}

    with decimal.localcontext() as ctx:
        ctx.traps[decimal.Inexact] = True
        result = _rate_coverage([node], folds, {1: rate}, Decimal("300.00"), totals)

    assert result["money_share"] is not None
    assert result["money_share_state"] == "complete"


def test_money_share_is_quantized_to_hundredths_of_a_percent(db_session, factories):
    """Ревизия 24.08.2026, реализация («money_share квантуется», §2.10).

    Фикстура — статья со своей позицией на 100,00 и нераспределённая позиция
    на 200,00: набор = {статья}, `covered` = 100,00, знаменатель = 300,00.
    Частное 100/300 = 33,333...% — период, не представимый конечной десятичной
    дробью НИ при каком `prec` (тот же случай, что доказывает соседний тест на
    `Inexact`-ловушку, только здесь через полный `get_project_passport`, чтобы
    проверить фактическое поле ответа, а не внутренний вызов). Без квантования
    в ответ уехало бы 100-символьное число ставки на явном `_RATE_CONTEXT`; с
    квантованием (тот же приём, что `unit_rate`, §2.10) — ровно `33.33`.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    article = _category(db_session, "6")
    chapter = _chapter(factories, proposal, category_id=article.id, smr_article_raw="6",
                        unit_id=m2, suggested_quantity=Decimal("10.00"),
                        total_cost_total=Decimal("1000.00"))
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("100.00"))
    _position(factories, proposal, total_cost_total=Decimal("200.00"))

    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert passport["rate_coverage"]["money_share"] == Decimal("33.33")
