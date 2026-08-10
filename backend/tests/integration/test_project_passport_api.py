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
from sqlalchemy.dialects import postgresql

from crud import project_passport as crud_project_passport
from crud.common import DomainError
from crud.project_passport import get_project_passport
from models import EstimateAdditionalWork, ProposalSummaryLine, UserRole, WorkCategory
from parser.constants import JSON_KEY_TOTAL_COST_INCLUDING_VAT

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


def _summary_line(session, proposal, *, key: str, total, job_title: str = "Итого по смете"):
    """Строка блока «Итого» предложения (`proposal_summary_lines`), без фабрики
    — та же причина, что у `_additional_work`: строка сугубо служебная для
    этого файла, заводить постоянную фабрику под неё незачем."""
    line = ProposalSummaryLine(
        proposal_id=proposal.id, summary_key=key, job_title=job_title, total_cost=total,
    )
    session.add(line)
    session.flush()
    return line


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


# ---------------------------------------------------------------------------
#  13. Валовое ИТОГО сметы — сумма по ВСЕМ предложениям ВСЕХ лотов (спека §2.5)
# ---------------------------------------------------------------------------

def test_file_total_sums_over_every_proposal_of_the_source_estimate(db_session, factories):
    """Смета с ДВУМЯ лотами несёт два предложения, каждое — свою строку
    «Итого включая НДС» (спека §2.5, правило 1): файловый итог — их сумма, а
    не итог одного лота. Второй лот+предложение заведены на ТУ ЖЕ смету."""
    proposal_1 = _proposal(factories)
    estimate = proposal_1.lot.estimate
    lot_2 = factories.LotFactory.create(estimate=estimate)
    proposal_2 = factories.ProposalFactory.create(lot=lot_2)

    _summary_line(db_session, proposal_1, key=JSON_KEY_TOTAL_COST_INCLUDING_VAT, total=Decimal("1000.00"))
    _summary_line(db_session, proposal_2, key=JSON_KEY_TOTAL_COST_INCLUDING_VAT, total=Decimal("2500.00"))
    db_session.flush()

    result = get_project_passport(db_session, estimate.contract_id)

    assert result["totals"]["file_total_including_vat"] == Decimal("3500.00")


def test_missing_key_in_one_proposal_makes_the_file_total_unknown(db_session, factories):
    """Одно из двух предложений не несёт строки «Итого включая НДС» вовсе ->
    файловый итог целиком `None` (спека §2.5, правило 2б). Частичная сумма,
    прочитанная как полная, была бы правдоподобным занижением без всякого
    признака ошибки."""
    proposal_1 = _proposal(factories)
    estimate = proposal_1.lot.estimate
    lot_2 = factories.LotFactory.create(estimate=estimate)
    factories.ProposalFactory.create(lot=lot_2)  # второе предложение сметы — без строки ниже

    _summary_line(db_session, proposal_1, key=JSON_KEY_TOTAL_COST_INCLUDING_VAT, total=Decimal("1000.00"))
    db_session.flush()

    result = get_project_passport(db_session, estimate.contract_id)

    assert result["totals"]["file_total_including_vat"] is None


def test_estimate_without_proposals_makes_the_file_total_unknown(db_session, factories):
    """Смета вовсе без предложений -> файловый итог `None`, а не ноль (спека
    §2.5, правило 2а)."""
    contract = factories.ContractFactory.create()
    factories.EstimateFactory.create(contract=contract, amendment_no=None)
    db_session.flush()

    result = get_project_passport(db_session, contract.id)

    assert result["totals"]["file_total_including_vat"] is None


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_value_makes_the_file_total_unknown_without_raising(db_session, factories, bad):
    """`NaN`/`Infinity`/`-Infinity` в `total_cost` — открытый хвост Ф4 (`_money`
    их не отсекает, `numeric` их принимает) — не должны уронить чтение
    паспорта: файловый итог просто становится `None` (спека §2.5, правило 2в).
    Сам факт того, что вызов ниже отрабатывает без исключения, — часть
    проверки наравне с итоговым значением."""
    proposal = _proposal(factories)
    _summary_line(db_session, proposal, key=JSON_KEY_TOTAL_COST_INCLUDING_VAT, total=Decimal(bad))
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)

    assert result["totals"]["file_total_including_vat"] is None


# ---------------------------------------------------------------------------
#  14. Сверка delta_to_file_total требует ДВА известных операнда (спека §2.5)
# ---------------------------------------------------------------------------

def test_delta_is_null_when_the_table_sum_is_unknown_and_the_file_total_is_known(db_session, factories):
    """Второй операнд сверки (спека §2.5, правило 3): табличная сумма
    неизвестна (единственная позиция без цены), а файловый итог известен ->
    `delta_to_file_total` обязана быть `None`, а не «`None` минус число». Без
    этого теста правило было бы доказано только с одной стороны — гейтинг
    только по файловому итогу оставил бы этот путь открытым."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal, category_id=category.id)
    _position(factories, proposal, chapter=chapter, total_cost_total=None)
    _summary_line(db_session, proposal, key=JSON_KEY_TOTAL_COST_INCLUDING_VAT, total=Decimal("1000.00"))
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)

    assert result["totals"]["amount"] is None
    assert isinstance(result["totals"]["file_total_including_vat"], Decimal)
    assert result["totals"]["delta_to_file_total"] is None


def test_delta_is_null_when_both_operands_are_unknown(db_session, factories):
    """Договор вовсе без сметы -> оба операнда сверки неизвестны, `delta_to_
    file_total` тоже `None` (спека §2.5, правила 3 и 7)."""
    contract = factories.ContractFactory.create()
    db_session.flush()

    result = get_project_passport(db_session, contract.id)

    assert result["totals"]["amount"] is None
    assert result["totals"]["file_total_including_vat"] is None
    assert result["totals"]["delta_to_file_total"] is None


def test_delta_is_zero_when_the_paths_agree(db_session, factories):
    """Табличный путь (позиции + допработы) и файловый блок «Итого» сходятся
    -> `delta_to_file_total == Decimal('0.00')` — ЧИСЛО, а не ложно-пустое
    значение (спека §2.5, правило 4)."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal, category_id=category.id)
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("700.00"))
    _additional_work(db_session, proposal, category_id=category.id, amount=Decimal("300.00"))
    _summary_line(db_session, proposal, key=JSON_KEY_TOTAL_COST_INCLUDING_VAT, total=Decimal("1000.00"))
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)

    assert result["totals"]["amount"] == Decimal("1000.00")
    assert result["totals"]["file_total_including_vat"] == Decimal("1000.00")
    delta = result["totals"]["delta_to_file_total"]
    assert delta is not None
    assert delta == Decimal("0.00")


def test_delta_is_reported_when_the_paths_disagree(db_session, factories):
    """Пути расходятся -> `delta_to_file_total` — точный ЗНАКОВЫЙ `Decimal`
    (табличная сумма минус файловый итог, спека §2.5, правило 4): знак
    закреплён явно, а не только модуль расхождения."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal, category_id=category.id)
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1200.00"))
    _summary_line(db_session, proposal, key=JSON_KEY_TOTAL_COST_INCLUDING_VAT, total=Decimal("1000.00"))
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)

    assert result["totals"]["amount"] == Decimal("1200.00")
    assert result["totals"]["file_total_including_vat"] == Decimal("1000.00")
    assert result["totals"]["delta_to_file_total"] == Decimal("200.00")


# ---------------------------------------------------------------------------
#  15. Ставка НДС сметы — правило единогласия (спека §2.5, правило 5)
# ---------------------------------------------------------------------------

def test_vat_rate_is_taken_when_every_proposal_declares_the_same(db_session, factories):
    """Единогласие предложений сметы по ставке НДС -> она и есть ответ (спека
    §2.5, правило 5)."""
    proposal_1 = _proposal(factories)
    estimate = proposal_1.lot.estimate
    lot_2 = factories.LotFactory.create(estimate=estimate)
    proposal_2 = factories.ProposalFactory.create(lot=lot_2)
    proposal_1.vat_rate = Decimal("20")
    proposal_2.vat_rate = Decimal("20")
    db_session.flush()

    result = get_project_passport(db_session, estimate.contract_id)

    assert result["estimate"]["vat_rate"] == Decimal("20")


def test_vat_rate_is_null_when_proposals_disagree(db_session, factories):
    """Ставки предложений одной сметы РАЗНЫЕ -> `estimate.vat_rate` — `None`,
    экран пишет «не заявлена в файле», а не первую попавшуюся ставку (спека
    §2.5, правило 5)."""
    proposal_1 = _proposal(factories)
    estimate = proposal_1.lot.estimate
    lot_2 = factories.LotFactory.create(estimate=estimate)
    proposal_2 = factories.ProposalFactory.create(lot=lot_2)
    proposal_1.vat_rate = Decimal("20")
    proposal_2.vat_rate = Decimal("18")
    db_session.flush()

    result = get_project_passport(db_session, estimate.contract_id)

    assert result["estimate"]["vat_rate"] is None


def test_vat_rate_is_null_when_one_proposal_has_none(db_session, factories):
    """Одно предложение сметы вовсе не заявило ставку -> `estimate.vat_rate` —
    `None` (спека §2.5, правило 5): единогласия не может быть, если у части
    предложений мнения вовсе нет."""
    proposal_1 = _proposal(factories)
    estimate = proposal_1.lot.estimate
    lot_2 = factories.LotFactory.create(estimate=estimate)
    proposal_2 = factories.ProposalFactory.create(lot=lot_2)
    proposal_1.vat_rate = Decimal("20")
    proposal_2.vat_rate = None
    db_session.flush()

    result = get_project_passport(db_session, estimate.contract_id)

    assert result["estimate"]["vat_rate"] is None


def test_declared_zero_vat_is_distinguishable_from_absence(db_session, factories):
    """ЗАЯВЛЕННЫЙ НОЛЬ — не то же самое, что отсутствие ставки (спека §2.5,
    правило 6): `Decimal('0')` ложен в Python, поэтому весь путь обязан
    сравнивать через `is None`/`is not None`, никогда истинностно. Регрессионный
    щит именно этой ловушки, стоившей Ф4б четырёх отдельных доказательств
    снятием защиты. Два договора в одном тесте — чтобы ноль и отсутствие были
    видны рядом, а не порознь."""
    zero_proposal = _proposal(factories)
    zero_proposal.vat_rate = Decimal("0")
    db_session.flush()

    absent_proposal = _proposal(factories)
    absent_proposal.vat_rate = None
    db_session.flush()

    zero_result = get_project_passport(db_session, zero_proposal.lot.estimate.contract_id)
    absent_result = get_project_passport(db_session, absent_proposal.lot.estimate.contract_id)

    assert zero_result["estimate"]["vat_rate"] == Decimal("0")
    assert zero_result["estimate"]["vat_rate"] is not None
    assert absent_result["estimate"]["vat_rate"] is None


# ---------------------------------------------------------------------------
#  16. Допработы узла — сумма сходится с веткой VIEW (задача 5)
# ---------------------------------------------------------------------------

def test_extras_of_a_node_sum_to_the_view_branch(db_session, factories):
    """Инвариант, связывающий два запроса (бриф задачи 5): сумма `extras` узла
    обязана совпасть с суммой ветки `additional_works` того же узла в
    `v_category_totals` — читанной СЫРЫМ SQL как независимый операнд. Без этой
    проверки два запроса (VIEW и построчный) могли бы молча разъехаться —
    одна и та же величина, посчитанная дважды. ДВЕ строки допработ на статью,
    чтобы сумма не была тривиальным единственным значением."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    _additional_work(db_session, proposal, ordinal=1, category_id=category.id, amount=Decimal("300.00"))
    _additional_work(db_session, proposal, ordinal=2, category_id=category.id, amount=Decimal("450.00"))
    db_session.flush()
    estimate_id = proposal.lot.estimate.id

    oracle = db_session.execute(
        sa.text(
            "SELECT amount FROM v_category_totals WHERE estimate_id = :eid "
            "AND work_category_id = :cid AND source = 'additional_works'"
        ),
        {"eid": estimate_id, "cid": category.id},
    ).scalar_one()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    node = next(c for c in result["categories"] if c["id"] == category.id)

    assert len(node["extras"]) == 2
    assert sum(e["amount"] for e in node["extras"]) == oracle


# ---------------------------------------------------------------------------
#  17. _extras_select объявляет явный порядок (СТРУКТУРНЫЙ тест)
# ---------------------------------------------------------------------------

def test_extras_select_declares_an_explicit_order():
    """СТРУКТУРНЫЙ тест: компилируем select и проверяем сам SQL-текст, а не
    поведение. Поведенческий тест ЭТУ границу не охраняет: замерено на
    `gca_test` — без `ORDER BY` Bitmap Heap Scan возвращает `2, 1` (тест
    падает), а Index Only Scan возвращает `1, 2` (тест зелёный), то есть
    поведенческая проверка опиралась бы на выбор планировщика, а это не
    контракт."""
    sql = str(crud_project_passport._extras_select(1).compile(dialect=postgresql.dialect()))

    assert "ORDER BY" in sql
    order_clause = sql.split("ORDER BY", 1)[1]
    assert "proposal_id" in order_clause
    assert "ordinal" in order_clause


# ---------------------------------------------------------------------------
#  18. Допработы возвращаются в порядке ordinal (поведенческий, позитивный)
# ---------------------------------------------------------------------------

def test_extras_come_back_in_ordinal_order(db_session, factories):
    """Две записи заведены в ОБРАТНОМ порядке ordinal (сначала 2, потом 1) в
    одном предложении — ответ обязан вернуть 1, затем 2, что доказывает: сортировка
    делает `ORDER BY`, а не порядок вставки."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    _additional_work(db_session, proposal, ordinal=2, category_id=category.id, amount=Decimal("200.00"))
    _additional_work(db_session, proposal, ordinal=1, category_id=category.id, amount=Decimal("100.00"))
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    node = next(c for c in result["categories"] if c["id"] == category.id)

    assert [e["ordinal"] for e in node["extras"]] == [1, 2]


# ---------------------------------------------------------------------------
#  19. Строка extras несёт id и ordinal
# ---------------------------------------------------------------------------

def test_extras_carry_id_and_ordinal(db_session, factories):
    """`id` и `ordinal` обязаны быть в ответе — иначе строку паспорта нельзя
    найти в БД, не гадая."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    work = _additional_work(db_session, proposal, ordinal=1, category_id=category.id, amount=Decimal("500.00"))
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    node = next(c for c in result["categories"] if c["id"] == category.id)

    assert len(node["extras"]) == 1
    assert node["extras"][0]["id"] == work.id
    assert node["extras"][0]["ordinal"] == 1


# ---------------------------------------------------------------------------
#  20. Допработа без статьи — в unallocated.extras, не в categories[].extras
# ---------------------------------------------------------------------------

def test_additional_work_without_a_category_lands_in_unallocated_extras(db_session, factories):
    """Запись без статьи показывается в `unallocated["extras"]` и НЕ в
    `categories[].extras` ни одной категории."""
    proposal = _proposal(factories)
    _additional_work(db_session, proposal, ordinal=1, amount=Decimal("400.00"))
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)

    assert len(result["unallocated"]["extras"]) == 1
    assert result["unallocated"]["extras"][0]["amount"] == Decimal("400.00")
    for c in result["categories"]:
        assert c["extras"] == []


# ---------------------------------------------------------------------------
#  21. own_sections называет разделы, давшие статье её собственные деньги
# ---------------------------------------------------------------------------

def test_own_sections_name_the_chapter_rows_of_the_category(db_session, factories):
    """Один раздел со статьёй и позицией под ним -> `own_sections` — список из
    ОДНОЙ записи с настоящими номером и заголовком раздела."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(
        factories, proposal, category_id=category.id,
        chapter_number="3", job_title_in_proposal="Раздел 3",
    )
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1000.00"))
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    node = next(c for c in result["categories"] if c["id"] == category.id)

    assert node["own_sections"] == [{"id": chapter.id, "number": "3", "title": "Раздел 3"}]


# ---------------------------------------------------------------------------
#  22. Спека §1.4: деньги приходят из ДВУХ разделов — оба обязаны попасть в ответ
# ---------------------------------------------------------------------------

def test_own_sections_list_both_sections_when_the_money_comes_from_two(db_session, factories):
    """ДВА раздела с ОДНОЙ и той же статьёй, у каждого своя позиция — случай
    спеки §1.4 (четвёртый из четырёх измеренных): в ответе обязаны быть ОБА."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter_a = _chapter(
        factories, proposal, category_id=category.id,
        chapter_number="1", job_title_in_proposal="Раздел А", position_key_in_proposal="1",
    )
    _position(factories, proposal, chapter=chapter_a, total_cost_total=Decimal("500.00"))
    chapter_b = _chapter(
        factories, proposal, category_id=category.id,
        chapter_number="2", job_title_in_proposal="Раздел Б", position_key_in_proposal="2",
    )
    _position(factories, proposal, chapter=chapter_b, total_cost_total=Decimal("700.00"))
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    node = next(c for c in result["categories"] if c["id"] == category.id)

    assert {s["id"] for s in node["own_sections"]} == {chapter_a.id, chapter_b.id}
    assert len(node["own_sections"]) == 2


# ---------------------------------------------------------------------------
#  23. own_sections пуст, когда у узла нет собственных денег
# ---------------------------------------------------------------------------

def test_own_sections_are_empty_when_the_node_has_no_own_money(db_session, factories):
    """Раздел несёт статью, но под ним НЕТ позиций -> у узла нет собственных
    денег. КОРНЕВАЯ статья (код "1"), чтобы узел вообще доехал до ответа
    (глубже первого уровня видимость требует rows > 0 у `build_tree`)."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    _chapter(factories, proposal, category_id=category.id, chapter_number="1")  # без позиций под ним
    db_session.flush()

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    node = next(c for c in result["categories"] if c["id"] == category.id)

    assert node["own_rows"] == 0
    assert node["own_sections"] == []


# ---------------------------------------------------------------------------
#  24. _own_sections_select объявляет явный порядок (СТРУКТУРНЫЙ тест)
# ---------------------------------------------------------------------------

def test_own_sections_select_declares_an_explicit_order():
    """СТРУКТУРНЫЙ тест, та же техника, что у `test_extras_select_declares_an_
    explicit_order`: проверяем сам SQL-текст на наличие `ORDER BY` с числовым
    порядком по ключу позиции, а не поведение (планировщик может случайно
    вернуть верный порядок и без него)."""
    sql = str(crud_project_passport._own_sections_select(1).compile(dialect=postgresql.dialect()))

    assert "ORDER BY" in sql
    order_clause = sql.split("ORDER BY", 1)[1]
    assert "proposals.id" in order_clause
    assert "length(" in order_clause
    assert "position_key_in_proposal" in order_clause


# ---------------------------------------------------------------------------
#  25. own_sections НЕ сортируется по id строки (хвост Ф3)
# ---------------------------------------------------------------------------

def test_own_sections_do_not_order_by_position_item_id(db_session, factories):
    """Хвост Ф3: два раздела ОДНОЙ статьи в одном предложении, чей порядок по
    ключу ПРОТИВОПОЛОЖЕН порядку по id — раздел с ключом "10" заведён ПЕРВЫМ
    (меньший id), раздел с ключом "2" заведён ВТОРЫМ (больший id), у каждого
    своя позиция. Ответ обязан перечислить "2" раньше "10" (длина-потом-лексика
    даёт числовой порядок), то есть НЕ порядок id. Проверяем, что id
    действительно в обратном порядке — иначе тест мог бы пройти случайно."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter_10 = _chapter(
        factories, proposal, category_id=category.id,
        chapter_number="10", position_key_in_proposal="10",
    )
    _position(factories, proposal, chapter=chapter_10, total_cost_total=Decimal("100.00"))
    chapter_2 = _chapter(
        factories, proposal, category_id=category.id,
        chapter_number="2", position_key_in_proposal="2",
    )
    _position(factories, proposal, chapter=chapter_2, total_cost_total=Decimal("200.00"))
    db_session.flush()

    assert chapter_10.id < chapter_2.id  # порядок id — противоположен порядку ключа

    result = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    node = next(c for c in result["categories"] if c["id"] == category.id)

    assert [s["number"] for s in node["own_sections"]] == ["2", "10"]


# ---------------------------------------------------------------------------
#  26. Деньги доезжают в JSON строками в каждой ветке (эндпоинт, RAW BODY)
# ---------------------------------------------------------------------------

_MONEY_FIELD_BY_BRANCH = {
    "totals": "amount",
    "categories": "total",
    "unallocated": "amount",
    "extras": "amount",
}


@pytest.mark.parametrize("branch", ["totals", "categories", "unallocated", "extras"])
def test_money_reaches_json_as_strings_in_every_branch(client, db_session, factories, branch):
    """Через ЭНДПОИНТ (не CRUD напрямую), проверка на СЫРОМ теле ответа
    (`response.text`), а не на распарсенном JSON — парсинг спрятал бы сам
    дефект (`json.loads("1000.0")` не отличит float от Decimal на глаз). Суммы
    подобраны различными по каждой ветке, чтобы каждая была узнаваема: итог
    договора 3500.00, собственная сумма категории (позиция+допработа) 1500.00,
    нераспределённая позиция 2000.00, допработа 500.00. Тело — компактный JSON
    (`separators=(",", ":")`), поэтому после двоеточия пробела нет."""
    category = _category(db_session, "1")
    proposal = _proposal(factories)
    chapter = _chapter(factories, proposal, category_id=category.id, chapter_number="1")
    _position(factories, proposal, chapter=chapter, total_cost_total=Decimal("1000.00"))
    _additional_work(db_session, proposal, ordinal=1, category_id=category.id, amount=Decimal("500.00"))
    _position(factories, proposal, total_cost_total=Decimal("2000.00"))  # нераспределённая
    db_session.flush()

    response = client.get(f"/api/v1/analytics/project-passport/{proposal.lot.estimate.contract_id}")
    assert response.status_code == 200
    body = response.text

    value = {"totals": "3500.00", "categories": "1500.00", "unallocated": "2000.00", "extras": "500.00"}[branch]
    field = _MONEY_FIELD_BY_BRANCH[branch]
    numeric = Decimal(value)

    assert f'"{field}":"{value}"' in body
    assert f'"{field}":{int(numeric)}.0' not in body
    assert f'"{field}":{int(numeric)}' not in body


# ---------------------------------------------------------------------------
#  27. member читает эндпоинт (§3 AGENTS.md: аналитика есть чтение)
# ---------------------------------------------------------------------------

def test_member_can_read_the_endpoint(client, factories):
    """Этот тест доказывает ОТСУТСТВИЕ ограничения: он зелёный ещё ДО фичи (до
    появления самого эндпоинта он получит 404 на несуществующем роуте, а не
    403 — то есть роль тут ни при чём) и потому не участвует в доказательствах
    снятием защиты. Его роль — регрессионный щит будущего сужения прав."""
    contract = factories.ContractFactory.create()
    client.auth_state["role"] = UserRole.member

    response = client.get(f"/api/v1/analytics/project-passport/{contract.id}")

    assert response.status_code == 200
