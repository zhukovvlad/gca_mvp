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

from crud.project_passport import _carrier_rows_by_category, get_project_passport
from models import EstimateAdditionalWork, UnitOfMeasure, WorkCategory
from services.article_rates import NON_SCALABLE_UNIT_CODES, SCALABLE_UNIT_CODES, RateState

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
    """DoD 27: маршрут действующий, новых нет; пять полей на месте.

    `rate_coverage` не проверяется здесь — задача 5 (см. решения оркестратора)."""
    contract = factories.ContractFactory.create()

    response = client.get(f"/api/v1/analytics/project-passport/{contract.id}")
    assert response.status_code == 200
    node = response.json()["categories"][0]
    assert {"unit", "volume", "unit_rate", "rate_state", "rate_note"} <= node.keys()


def test_a_contract_without_an_estimate_keeps_the_same_response_shape(db_session, factories):
    """Правило 8 `get_project_passport`: форма ответа одна на оба пути функции.

    `rate_coverage` не проверяется здесь — задача 5 (см. решения оркестратора)."""
    contract = factories.ContractFactory.create()
    passport = get_project_passport(db_session, contract.id)
    assert all(n["rate_state"] == "no_carrier" for n in passport["categories"])
