"""Паспорт проекта по статьям классификатора — CRUD-уровень (фаза 7, Ф6, задача 3).

Эндпоинт HTTP появится в задаче 5; здесь тесты вызывают `crud.project_passport.
get_project_passport` напрямую. Помощники — локальные для этого файла (спека
задачи прямо запрещает трогать `tests/factories.py` и импортировать помощники
из `test_category_totals_view.py`): `EstimateAdditionalWork` не имеет фабрики
намеренно (см. комментарий у `_additional_work`), а сборка цепочки
договор → смета → лот → предложение здесь своя, потому что часть тестов
(например, «исходная смета vs допсоглашение») требует ДВЕ сметы на один
договор — сценарий, которого нет в `test_category_totals_view.py`.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud.common import DomainError
from crud.project_passport import get_project_passport
from models import EstimateAdditionalWork, WorkCategory

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
#  Локальные помощники
# ---------------------------------------------------------------------------

def _category(session, code: str) -> WorkCategory:
    return session.query(WorkCategory).filter_by(code=code).one()


def _proposal(factories, *, contract=None, amendment_no=None):
    """Полная цепочка договор → смета → лот → предложение.

    `contract` можно передать готовым — так тест кладёт на ОДИН договор две
    сметы (исходную и допсоглашение), что и требует тест №1.
    """
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
    структуры разделов (спека §2.6: «строки вне структуры»)."""
    if chapter is not None:
        kwargs.setdefault("chapter_item_id", chapter.id)
    return factories.PositionItemFactory.create(proposal=proposal, **kwargs)


def _additional_work(session, proposal, *, ordinal=1, category_id=None, amount=Decimal("500.00"),
                      title="Дополнительная работа"):
    """EstimateAdditionalWork без фабрики (её нет намеренно — бриф задачи 3).

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


# ---------------------------------------------------------------------------
#  1. Исходная смета, а не последняя
# ---------------------------------------------------------------------------

def test_source_estimate_is_taken_when_an_amendment_exists(db_session, factories):
    """Правило CRUD «исходная смета» (amendment_no IS NULL, спека §2.4), а НЕ
    «последняя смета» (`crud.analytics.latest_estimates`/`get_latest_estimate`,
    AGENTS.md §6) — это два разных правила. Договор несёт ОБЕ сметы, каждая со
    своими деньгами; паспорт обязан отразить ТОЛЬКО исходную."""
    contract = factories.ContractFactory.create()
    category = _category(db_session, "1")

    original_proposal = _proposal(factories, contract=contract, amendment_no=None)
    original_chapter = _chapter(factories, original_proposal, category_id=category.id)
    _position(factories, original_proposal, chapter=original_chapter, total_cost_total=Decimal("1000.00"))

    amendment_proposal = _proposal(factories, contract=contract, amendment_no=1)
    amendment_chapter = _chapter(factories, amendment_proposal, category_id=category.id)
    _position(factories, amendment_proposal, chapter=amendment_chapter, total_cost_total=Decimal("5000.00"))

    db_session.flush()

    result = get_project_passport(db_session, contract.id)

    assert result["estimate"] is not None
    assert result["estimate"]["amendment_no"] is None
    assert result["totals"]["amount"] == Decimal("1000.00")
    category_row = next(c for c in result["categories"] if c["code"] == "1")
    assert category_row["total"] == Decimal("1000.00")


# ---------------------------------------------------------------------------
#  2. Инвариант суммы: категории + нераспределённое == позиции + допработы
# ---------------------------------------------------------------------------

def test_sum_invariant_categories_plus_unallocated_equals_positions_plus_extras(db_session, factories):
    """(Σ total корней) + unallocated.amount == totals.amount (спека §2.6, правило
    3), и это равно НЕЗАВИСИМО посчитанной по сырому SQL сумме всех денег позиций
    и допработ сметы — раз каждая позиция и допработа попадают либо в дерево
    статей, либо в «Нераспределённое», разбиение исчерпывающее и оракул обязан
    сойтись без остатка."""
    category_a = _category(db_session, "1")
    category_b = _category(db_session, "2")
    proposal = _proposal(factories)

    chapter_a = _chapter(factories, proposal, category_id=category_a.id, chapter_number="1")
    _position(factories, proposal, chapter=chapter_a, total_cost_total=Decimal("1000.00"))
    _position(factories, proposal, chapter=chapter_a, total_cost_total=Decimal("2000.00"))

    chapter_b = _chapter(factories, proposal, category_id=category_b.id, chapter_number="2")
    _position(factories, proposal, chapter=chapter_b, total_cost_total=Decimal("500.00"))

    _position(factories, proposal, total_cost_total=Decimal("300.00"))  # нераспределённая позиция

    _additional_work(db_session, proposal, ordinal=1, category_id=category_a.id, amount=Decimal("700.00"))
    _additional_work(db_session, proposal, ordinal=2, amount=Decimal("400.00"))  # нераспределённая допработа

    db_session.flush()
    contract_id = proposal.lot.estimate.contract_id
    estimate_id = proposal.lot.estimate.id

    oracle = db_session.execute(
        sa.text(
            "SELECT "
            "(SELECT COALESCE(SUM(pi.total_cost_total), 0) FROM position_items pi "
            " JOIN proposals p ON p.id = pi.proposal_id "
            " JOIN lots l ON l.id = p.lot_id "
            " WHERE l.estimate_id = :eid AND pi.is_chapter = false) "
            "+ "
            "(SELECT COALESCE(SUM(aw.total_amount), 0) FROM estimate_additional_works aw "
            " JOIN proposals p ON p.id = aw.proposal_id "
            " JOIN lots l ON l.id = p.lot_id "
            " WHERE l.estimate_id = :eid) AS grand_total"
        ),
        {"eid": estimate_id},
    ).scalar_one()

    result = get_project_passport(db_session, contract_id)

    root_sum = sum(
        c["total"] for c in result["categories"] if c["parent_id"] is None and c["total"] is not None
    )
    assert root_sum + result["unallocated"]["amount"] == result["totals"]["amount"]
    assert result["totals"]["amount"] == oracle


# ---------------------------------------------------------------------------
#  3. Инвариант roll-up по всему дереву
# ---------------------------------------------------------------------------

def test_rollup_invariant_holds_on_the_whole_tree(db_session, factories):
    """Для КАЖДОГО узла: total == own + допработы своего узла (читаются СЫРЫМ SQL
    из `v_category_totals` — независимый операнд) + Σ total детей, суммируя
    только известные слагаемые (спека §2.6). Категория "1" несёт все три
    слагаемых разом: собственные позиции, собственные допработы и ребёнка "1.1"
    с деньгами."""
    category_1 = _category(db_session, "1")
    category_11 = _category(db_session, "1.1")

    proposal = _proposal(factories)
    chapter_1 = _chapter(factories, proposal, category_id=category_1.id, chapter_number="1")
    _position(factories, proposal, chapter=chapter_1, total_cost_total=Decimal("1000.00"))

    chapter_11 = _chapter(factories, proposal, category_id=category_11.id, chapter_number="1.1")
    _position(factories, proposal, chapter=chapter_11, total_cost_total=Decimal("2000.00"))

    _additional_work(db_session, proposal, category_id=category_1.id, amount=Decimal("300.00"))

    db_session.flush()
    contract_id = proposal.lot.estimate.contract_id
    estimate_id = proposal.lot.estimate.id

    result = get_project_passport(db_session, contract_id)
    by_id = {c["id"]: c for c in result["categories"]}

    checked_all_three = False
    for node in result["categories"]:
        extra_amount = db_session.execute(
            sa.text(
                "SELECT amount FROM v_category_totals "
                "WHERE estimate_id = :eid AND work_category_id = :cid AND source = 'additional_works'"
            ),
            {"eid": estimate_id, "cid": node["id"]},
        ).scalar_one_or_none()

        children_totals = [
            child["total"] for child in by_id.values() if child["parent_id"] == node["id"]
        ]
        summands = [v for v in [node["own"], extra_amount, *children_totals] if v is not None]
        expected = sum(summands) if summands else None
        assert node["total"] == expected, node

        if node["id"] == category_1.id:
            assert node["own"] == Decimal("1000.00")
            assert extra_amount == Decimal("300.00")
            assert children_totals == [Decimal("2000.00")]
            checked_all_three = True

    assert checked_all_three


# ---------------------------------------------------------------------------
#  4. Все 21 статья верхнего уровня доезжают в ответ
# ---------------------------------------------------------------------------

def test_all_twenty_one_roots_reach_the_response(db_session, factories):
    """21 статья верхнего уровня (коды 1..20 и 99) присутствуют ВСЕГДА
    (`build_tree`, спека §2.6) — включая те, для которых в смете нет ни одной
    строки: у них total is None и rows == 0."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal, category_id=category.id)
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1000.00"))
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)

    roots = [c for c in result["categories"] if c["parent_id"] is None]
    assert len(roots) == 21

    empty_roots = [r for r in roots if r["code"] != "1"]
    assert len(empty_roots) == 20
    for r in empty_roots:
        assert r["total"] is None
        assert r["rows"] == 0


# ---------------------------------------------------------------------------
#  5. share_pct считается от общего итога — один знаменатель для всех строк
# ---------------------------------------------------------------------------

def test_share_is_computed_from_the_grand_total_for_every_row(db_session, factories):
    """share_pct — доля от ОБЩЕГО итога, один и тот же знаменатель для всех
    строк, включая «Нераспределённое» (спека §2.6, правило 5). Точное сравнение
    Decimal с Decimal — округление не здесь."""
    category_a = _category(db_session, "1")
    category_b = _category(db_session, "2")
    proposal = _proposal(factories)
    chapter_a = _chapter(factories, proposal, category_id=category_a.id, chapter_number="1")
    _position(factories, proposal, chapter=chapter_a, total_cost_total=Decimal("1000.00"))
    chapter_b = _chapter(factories, proposal, category_id=category_b.id, chapter_number="2")
    _position(factories, proposal, chapter=chapter_b, total_cost_total=Decimal("3000.00"))
    _position(factories, proposal, total_cost_total=Decimal("1000.00"))  # нераспределённая
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    grand_total = result["totals"]["amount"]
    assert grand_total == Decimal("5000.00")

    checked_categories = 0
    for row in result["categories"]:
        if row["total"] is not None:
            assert row["share_pct"] == row["total"] / grand_total * 100
            checked_categories += 1
    assert checked_categories == 2  # category_a и category_b

    unallocated = result["unallocated"]
    assert unallocated["share_pct"] == unallocated["amount"] / grand_total * 100


# ---------------------------------------------------------------------------
#  6. share_pct == None, когда знаменатель непригоден — два разных случая
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", ["empty", "zero"])
def test_share_is_null_when_the_grand_total_is_unusable(db_session, factories, case):
    """Два РАЗНЫХ условия одного отказа (спека §2.6, правило 5): грандтотал
    ПУСТОЙ (ни одной расценённой строки вообще) или РАВЕН НУЛЮ (строки есть, но
    все суммы 0.00) — в обоих случаях share_pct всюду None, и каждый вход
    нарушает ровно одно из двух условий."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal, category_id=category.id)
    if case == "empty":
        _position(factories, proposal, chapter=chapter, total_cost_total=None)
    else:
        _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("0.00"))
        _position(factories, proposal, total_cost_total=Decimal("0.00"))  # нераспределённая, тоже 0
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)

    if case == "empty":
        assert result["totals"]["amount"] is None
    else:
        assert result["totals"]["amount"] == Decimal("0.00")

    for row in result["categories"]:
        assert row["share_pct"] is None
    assert result["unallocated"]["share_pct"] is None


# ---------------------------------------------------------------------------
#  7. per_sqm == None при отсутствующей площади, а не при нулевой
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("place", ["totals", "category"])
def test_per_sqm_is_null_on_a_null_area_not_zero(db_session, factories, place):
    """Объект БЕЗ площадей -> per_sqm is None, никогда 0 (спека §2.6, правило 6)
    — ноль читался бы как «бесплатно». Деление на ноль невозможно в принципе
    (CHECK Ф5 держит общую площадь строго положительной), поэтому единственный
    интересный случай — отсутствующая площадь."""
    obj = factories.ObjectFactory.create()  # без area_aboveground_sp/area_underground_sp
    contract = factories.ContractFactory.create(object=obj)
    category = _category(db_session, "1")
    proposal = _proposal(factories, contract=contract)
    chapter = _chapter(factories, proposal, category_id=category.id)
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1000.00"))
    db_session.flush()

    result = get_project_passport(db_session, contract.id)

    assert result["object"]["area_total_sp"] is None
    if place == "totals":
        assert result["totals"]["per_sqm"] is None
    else:
        row = next(c for c in result["categories"] if c["code"] == "1")
        assert row["per_sqm"] is None


# ---------------------------------------------------------------------------
#  8. Нулевая сумма — это число, а не отсутствие числа
# ---------------------------------------------------------------------------

def test_zero_sum_reaches_json_as_a_number_not_null(db_session, factories):
    """Категория, чьи позиции суммируются РОВНО в ноль: total == Decimal('0.00')
    и total is not None на уровне CRUD. Проверка сырой JSON-строки (что "0.00"
    не превращается в null при сериализации) — дело Задачи 5; здесь проверяется
    только то, что CRUD отдаёт настоящий Decimal('0.00'), а не None."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal, category_id=category.id)
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("500.00"))
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("-500.00"))
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    row = next(c for c in result["categories"] if c["code"] == "1")
    assert row["total"] == Decimal("0.00")
    assert row["total"] is not None


# ---------------------------------------------------------------------------
#  9. Разделы без статьи и строки вне структуры — две разные причины
# ---------------------------------------------------------------------------

def test_unallocated_counts_chapters_and_rows_outside_structure_separately(db_session, factories):
    """Две РАЗНЫЕ причины одного и того же следствия «Нераспределённое» (спека
    §2.6): разделы без статьи считаются штуками разделов (`chapters`), а позиции
    без ссылки на раздел вовсе — отдельным счётчиком строк
    (`rows_outside_structure`). Числа обязаны быть РАЗНЫМИ, чтобы ни один не мог
    подменить собой другой."""
    proposal = _proposal(factories)
    chapter_1 = _chapter(factories, proposal, chapter_number="1")  # без статьи
    _position(factories, proposal, chapter=chapter_1, total_cost_total=Decimal("100.00"))
    chapter_2 = _chapter(factories, proposal, chapter_number="2")  # без статьи
    _position(factories, proposal, chapter=chapter_2, total_cost_total=Decimal("200.00"))
    _position(factories, proposal, total_cost_total=Decimal("300.00"))  # без chapter_item_id вовсе
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)

    assert result["unallocated"]["chapters"] == 2
    assert result["unallocated"]["rows_outside_structure"] == 1
    assert result["unallocated"]["chapters"] != result["unallocated"]["rows_outside_structure"]


# ---------------------------------------------------------------------------
#  10-11. Договор без сметы — не 404; несуществующий договор — 404
# ---------------------------------------------------------------------------

def test_contract_without_an_estimate_answers_200_with_null_estimate(db_session, factories):
    """Договор без сметы — НЕ 404 (спека §2.6): карточка заведена, файл ещё не
    загружен. Честно: этот тест вызывает CRUD напрямую, не HTTP (эндпоинт —
    задача 5), и проверяет ровно требование «не 404» — что `DomainError` не
    поднимается и структура ответа полна (все 21 корня на месте)."""
    contract = factories.ContractFactory.create()
    db_session.flush()

    result = get_project_passport(db_session, contract.id)

    assert result["estimate"] is None
    assert result["totals"]["amount"] is None
    roots = [c for c in result["categories"] if c["parent_id"] is None]
    assert len(roots) == 21


def test_unknown_contract_answers_404(db_session):
    """Несуществующий id — `DomainError(404, ...)` (спека §2.6, вторая половина
    правила 8). Честно: тест вызывает CRUD напрямую, не HTTP — эндпоинт появится
    в задаче 5, транслировать `DomainError` в HTTP-404 будет роутер."""
    with pytest.raises(DomainError) as exc:
        get_project_passport(db_session, 999_999_999)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
#  12. object_contracts_count — число договоров ОБЪЕКТА
# ---------------------------------------------------------------------------

def test_object_contracts_count_reflects_the_object(db_session, factories):
    """object_contracts_count — число договоров объекта (спека §2.6, правило 7),
    на экране это предупреждающий бейдж, когда их больше одного."""
    obj = factories.ObjectFactory.create()
    contract_1 = factories.ContractFactory.create(object=obj)
    contract_2 = factories.ContractFactory.create(object=obj)
    lone_contract = factories.ContractFactory.create()
    db_session.flush()

    result_1 = get_project_passport(db_session, contract_1.id)
    result_2 = get_project_passport(db_session, contract_2.id)
    result_lone = get_project_passport(db_session, lone_contract.id)

    assert result_1["contract"]["object_contracts_count"] == 2
    assert result_2["contract"]["object_contracts_count"] == 2
    assert result_lone["contract"]["object_contracts_count"] == 1
