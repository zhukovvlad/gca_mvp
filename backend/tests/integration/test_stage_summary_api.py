"""Свод по этапам: проверка выбора, чтение входов, HTTP-контракт (спека §2.3, §2.16).

Сметы предложений строятся НАСТОЯЩИМ `import_round` (образец `_grid` в
`test_tenders_crud.py`): только так у строк появляется `work_category_id`, а
у допработ — статья через ссылку «Сведений» на раздел.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import stage_summary as crud_ss
from crud.common import DomainError
from models import (
    Contractor,
    Estimate,
    Lot,
    Offer,
    OfferPackage,
    PositionItem,
    Proposal,
    ProposalSummaryLine,
    TenderRound,
    WorkCategory,
)
from parser.constants import (
    JSON_KEY_BASELINE_PROPOSAL,
    JSON_KEY_CONTRACTOR_TITLE,
    JSON_KEY_LOT_TITLE,
    JSON_KEY_PROPOSALS,
    JSON_KEY_TOTAL_COST_EXCLUDING_VAT,
    JSON_KEY_TOTAL_COST_INCLUDING_VAT,
    JSON_KEY_VAT_AMOUNT,
)
from parser.postprocess import BASELINE_MISSING_TITLE
from services import stage_summary as ss
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.payloads import (
    additional_works_row,
    estimate_payload,
    position,
    proposal,
    round_payload,
    summary_line,
    svedeniya_info,
)

pytestmark = pytest.mark.integration

D = Decimal


def chaptered(code_by_chapter: dict[str, tuple[str, str]], *, vat_rate="20", additional=None, inn="7700000001",
              title="ООО А", total="1200.00"):
    """Предложение: разделы с article_smr и по одной работе под каждым.
    `code_by_chapter` = {"1": ("6", "120.00"), "2": ("2", "60.00")} — статья и сумма работы."""
    positions, n = [], 1
    for chapter, (code, amount) in code_by_chapter.items():
        positions.append(position(job_title=f"Раздел {chapter}", is_chapter=True, chapter_number=chapter,
                                  article_smr=code, number=str(n)))
        n += 1
        positions.append(position(job_title=f"Работа {chapter}", unit="м2", quantity=1, suggested_quantity=1,
                                  unit_cost_total=amount, total_cost_total=amount, chapter_ref=chapter, number=str(n)))
        n += 1
    summary = {JSON_KEY_TOTAL_COST_INCLUDING_VAT: summary_line("ИТОГО, руб. с учетом НДС", total),
               JSON_KEY_VAT_AMOUNT: summary_line("В том числе НДС", "0"),
               JSON_KEY_TOTAL_COST_EXCLUDING_VAT: summary_line("ИТОГО, руб. без учета НДС", total)}
    return proposal(positions, title=title, inn=inn, vat_rate=vat_rate, summary=summary,
                    additional_works=additional,
                    additional_info=svedeniya_info("1 Допработы по разделу - 24.00 руб.") if additional else None)


@contextmanager
def count_queries(db_session):
    """Копия `_count_queries` из test_dashboard_api.py — общий хелпер не заводится (Р10)."""
    counter = {"n": 0}
    bind = db_session.get_bind()

    def _tick(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    sa.event.listen(bind, "before_cursor_execute", _tick)
    try:
        yield counter
    finally:
        sa.event.remove(bind, "before_cursor_execute", _tick)


@pytest.fixture
def grid(db_session, factories):
    """Тендер, 4 раунда (1, 2, 3, 4), участники А и Б. А: во всех четырёх; Б: только во 2-м.
    Суммы А по статьям: р1 6→120, 2→60; р2 6→96(+24 допработ), 2→0; р3 6→100; р4 6→90.
    `g.path` — трасса из трёх выбранных (1, 2, 4): этап 3 ИСКЛЮЧЁН выбором (спека §2.2, §2.6)."""
    tender = factories.TenderFactory.create()
    rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2, 3, 4)}
    db_session.flush()
    payloads = {
        1: [chaptered({"1": ("6", "120.00"), "2": ("2", "60.00")}, total="180.00")],
        2: [chaptered({"1": ("6", "96.00"), "2": ("2", "0")}, total="120.00", additional=additional_works_row(total="24.00")),
            chaptered({"1": ("6", "200.00")}, inn="7700000002", title="ООО Б", total="200.00")],
        3: [chaptered({"1": ("6", "100.00")}, total="100.00")],
        4: [chaptered({"1": ("6", "90.00")}, total="90.00")],
    }
    for n, rnd in rounds.items():
        import_round(db_session, tender_round=rnd, data=round_payload(payloads[n]), parser_version="4.0.0",
                     import_job_id=None, replace=False, unit_resolver=UnitResolver(db_session),
                     category_resolver=CategoryResolver.from_db(db_session))
    db_session.flush()

    def offers_of(inn):
        return db_session.execute(
            sa.select(Offer.id)
            .join(OfferPackage, OfferPackage.id == Offer.package_id)
            .join(Contractor, Contractor.id == OfferPackage.contractor_id)
            .join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id, Contractor.inn == inn)
            .order_by(TenderRound.stage_no)
        ).scalars().all()

    class G:
        pass
    g = G()
    g.tender = tender
    g.rounds = rounds
    g.a = offers_of("7700000001")   # [o1, o2, o3, o4]
    g.b = offers_of("7700000002")   # [o2b]
    g.path = [g.a[0], g.a[1], g.a[3]]   # выбранная трасса: 1, 2, 4
    return g


class TestBatchTotalsParity:
    """`estimate_totals_including_vat` по списку обязано совпадать с одиночным правилом
    на каждом достижимом исходе — иначе у свода и решётки были бы два разных «Итого с НДС».
    Дубль строки итога на живой БД НЕДОСТИЖИМ (`UNIQUE (proposal_id, summary_key)`, models.py) —
    он покрыт unit-тестом с подменённым результатом запроса (`tests/unit/test_estimate_totals_batch.py`)."""

    def _estimate(self, db, factories, lines_per_proposal):
        """lines_per_proposal: список списков итогов по предложениям; [] — строки нет."""
        est = factories.EstimateFactory.create()
        for lines in lines_per_proposal:
            lot = factories.LotFactory.create(estimate=est)
            prop = factories.ProposalFactory.create(lot=lot)
            db.flush()
            for total in lines:
                db.add(ProposalSummaryLine(proposal_id=prop.id, summary_key="total_cost_including_vat",
                                           job_title="ИТОГО", total_cost=total))
        db.flush()
        return est

    @pytest.mark.parametrize("lines", [
        [[Decimal("1200.00")], [Decimal("1200.00")]],           # единогласие
        [[Decimal("1200.00")], []],                              # пропуск
        [[Decimal("1200.00")], [Decimal("NaN")]],               # NaN
        [[Decimal("1200.00")], [Decimal("1300.00")]],           # разные итоги
        [],                                                     # предложений нет
    ])
    def test_batch_matches_single_rule(self, db_session, factories, lines):
        from crud.estimate_totals import estimate_total_including_vat, estimate_totals_including_vat
        est = self._estimate(db_session, factories, lines)
        assert estimate_totals_including_vat(db_session, [est.id])[est.id] == estimate_total_including_vat(db_session, est.id)

    def test_batch_partitions_several_estimates_without_leaking(self, db_session, factories):
        """Три сметы в ОДНОМ вызове батча, в трёх разных состояниях единогласия
        (единогласие / разногласие / предложений нет вовсе) — если бы батч
        перепутал предложения одной сметы с другой, хотя бы одно из трёх
        ожидаемых значений изменилось бы, а полное совпадение с одиночным
        правилом по каждому id осталось бы неотличимо от утечки."""
        from crud.estimate_totals import estimate_total_including_vat, estimate_totals_including_vat
        unanimous = self._estimate(db_session, factories, [[Decimal("1200.00")], [Decimal("1200.00")]])
        disagreement = self._estimate(db_session, factories, [[Decimal("500.00")], [Decimal("700.00")]])
        empty = self._estimate(db_session, factories, [])
        ids = [unanimous.id, disagreement.id, empty.id]

        batch = estimate_totals_including_vat(db_session, ids)

        for estimate_id in ids:
            assert batch[estimate_id] == estimate_total_including_vat(db_session, estimate_id)
        assert batch[unanimous.id] == Decimal("1200.00")
        assert batch[disagreement.id] is None
        assert batch[empty.id] is None


class TestSelectionRefusals:
    def _code(self, db_session, tender_id, offers):
        with pytest.raises(DomainError) as e:
            crud_ss.build_stage_summary(db_session, tender_id, offers)
        return e.value

    def test_offer_of_other_tender_is_404(self, db_session, factories, grid):
        other = factories.TenderFactory.create()
        db_session.flush()
        err = self._code(db_session, other.id, grid.a[:2])
        assert err.status_code == 404 and err.code == crud_ss.CODE_OFFER_NOT_FOUND
        assert set(err.context["offers"]) == set(grid.a[:2])

    def test_too_few_offers_is_422(self, db_session, grid):
        err = self._code(db_session, grid.tender.id, grid.a[:1])
        assert err.status_code == 422 and err.code == crud_ss.CODE_TOO_FEW

    def test_two_offers_of_one_round_is_422(self, db_session, grid):
        err = self._code(db_session, grid.tender.id, [grid.a[1], grid.b[0]])
        # оба предложения из раунда 2 — но они ещё и разных участников; порядок проверок:
        # раунд раньше участника (спека §2.3 таблица), поэтому код — one_offer_per_round
        assert err.code == crud_ss.CODE_ONE_PER_ROUND and set(err.context["offers"]) == {grid.a[1], grid.b[0]}

    def test_mixed_participants_is_422(self, db_session, grid):
        err = self._code(db_session, grid.tender.id, [grid.a[0], grid.b[0]])
        assert err.code == crud_ss.CODE_SINGLE_PARTICIPANT and grid.b[0] in err.context["offers"]

    def test_offer_without_estimate_is_422(self, db_session, grid):
        db_session.execute(sa.delete(Estimate).where(Estimate.offer_id == grid.a[3]))
        db_session.flush()
        err = self._code(db_session, grid.tender.id, grid.path)
        assert err.code == crud_ss.CODE_NO_ESTIMATE and err.context["offers"] == [grid.a[3]]


class TestSelectionShape:
    def test_columns_follow_stage_no_regardless_of_request_order(self, db_session, grid):
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, [grid.a[3], grid.a[0], grid.a[1]])
        assert [c["stage_no"] for c in body["columns"]] == [1, 2, 4]
        assert body["participant"]["rounds_with_estimate"] == 4
        assert body["kpi"]["stages_selected"] == 3 and body["kpi"]["stages_loaded"] == 4

    def test_participant_stages_list_marks_excluded_stage(self, db_session, grid):
        """Исключённые этапы клиент берёт из ответа, не вычисляет (спека §2.2, §2.16)."""
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        stages = body["participant"]["stages"]
        assert [(s["stage_no"], s["selected"]) for s in stages] == [(1, True), (2, True), (3, False), (4, True)]
        assert [s["offer_id"] for s in stages if s["selected"]] == [c["offer_id"] for c in body["columns"]]
        assert stages[2]["offer_id"] == grid.a[2]

    def test_query_count_does_not_grow_with_columns(self, db_session, grid):
        with count_queries(db_session) as two:
            crud_ss.build_stage_summary(db_session, grid.tender.id, grid.a[:2])
        with count_queries(db_session) as three:
            crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        assert three["n"] == two["n"]


def _lot_multi(work_totals: list[str], *, code: str = "6") -> dict:
    """Один лот раунда: один участник (А), один раздел под статьёй `code`,
    и ПО ОДНОЙ работе на каждый элемент `work_totals` — строка-сумма, либо
    буквально `"NaN"` (парсер конструирует `Decimal("NaN")` без исключения,
    `_money`, `services/estimate_import.py`; строки без CHECK на
    `total_cost_total`, ровно тот класс входа, который считает
    `rows_not_finite` VIEW). Так один лот может нести РАЗНОЕ число строк,
    разное число оценённых и разное число неоценённых — и обе величины
    (сумма и три счётчика) проверяются накоплением по лоту, а не только
    деньгами (гейт 3, round 2 review)."""
    positions = [position(job_title="Раздел 1", is_chapter=True, chapter_number="1", article_smr=code, number="0")]
    for i, total in enumerate(work_totals, start=1):
        positions.append(position(job_title=f"Работа {i}", unit="м2", quantity=1, suggested_quantity=1,
                                  unit_cost_total=total, total_cost_total=total, chapter_ref="1", number=str(i)))
    finite_sum = str(sum(v for t in work_totals if (v := Decimal(t)).is_finite()))
    summary = {JSON_KEY_TOTAL_COST_INCLUDING_VAT: summary_line("ИТОГО, руб. с учетом НДС", finite_sum),
               JSON_KEY_VAT_AMOUNT: summary_line("В том числе НДС", "0"),
               JSON_KEY_TOTAL_COST_EXCLUDING_VAT: summary_line("ИТОГО, руб. без учета НДС", finite_sum)}
    proposal_block = proposal(positions, title="ООО А", inn="7700000001", vat_rate="20", summary=summary)
    return {
        JSON_KEY_LOT_TITLE: "Лот - Тестовый",
        JSON_KEY_PROPOSALS: {"contractor_1": proposal_block},
        JSON_KEY_BASELINE_PROPOSAL: {JSON_KEY_CONTRACTOR_TITLE: BASELINE_MISSING_TITLE},
    }


class TestTwoLotAccumulation:
    """Смета одного участника с ДВУМЯ лотами (спека §2.2, задача 3 пересчёта
    НДС): `v_category_totals` группируется в т.ч. по `proposal_id`, и на статью
    со сметой из нескольких предложений приходит НЕСКОЛЬКО строк VIEW — деньги
    статьи обязаны накопиться по ОБЕИМ, а не взяться из одной (плана: гейт 3
    review, item 2 — фикстура `grid` этот путь не проходит, там у каждой сметы
    ровно один лот)."""

    def test_article_sum_accumulates_across_two_lots(self, db_session, factories):
        tender = factories.TenderFactory.create()
        r1 = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
        r2 = factories.TenderRoundFactory.create(tender=tender, stage_no=2)
        db_session.flush()

        # Лот 1: одна оценённая работа (30.00) и одна с "NaN" — 2 строки, 1
        # оценённая, 1 неоценённая. Лот 2: одна оценённая (50.00) и ДВЕ с
        # "NaN" — 3 строки, 1 оценённая, 2 неоценённых. Числа лотов подобраны
        # так, что ни для суммы, ни для одного из трёх счётчиков верный ответ
        # НЕ совпадает ни с одним лотом в одиночку — независимо от того, в
        # каком порядке Postgres вернёт строки VIEW (порядок не гарантирован,
        # `_direct_by_estimate` без ORDER BY), перезапись последней строкой
        # вместо накопления даст число из {2,3} / {1} / {1,2} / {30.00,50.00} —
        # ни разу верную сумму (5 / 2 / 3 / 80.00).
        two_lot_data = estimate_payload(
            lots={
                "lot_1": _lot_multi(["30.00", "NaN"]),
                "lot_2": _lot_multi(["50.00", "NaN", "NaN"]),
            },
            tender_title="Свод по этапам — раунд с двумя лотами",
        )
        import_round(db_session, tender_round=r1, data=two_lot_data, parser_version="4.0.0",
                     import_job_id=None, replace=False, unit_resolver=UnitResolver(db_session),
                     category_resolver=CategoryResolver.from_db(db_session))
        import_round(db_session, tender_round=r2, data=round_payload([chaptered({"1": ("6", "40.00")}, total="40.00")]),
                     parser_version="4.0.0", import_job_id=None, replace=False,
                     unit_resolver=UnitResolver(db_session), category_resolver=CategoryResolver.from_db(db_session))
        db_session.flush()

        offer_ids = db_session.execute(
            sa.select(Offer.id)
            .join(OfferPackage, OfferPackage.id == Offer.package_id)
            .join(Contractor, Contractor.id == OfferPackage.contractor_id)
            .join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id, Contractor.inn == "7700000001")
            .order_by(TenderRound.stage_no)
        ).scalars().all()

        body = crud_ss.build_stage_summary(db_session, tender.id, offer_ids)

        article_6 = next(r for r in body["rows"] if r["code"] == "6")
        cell = article_6["cells"][0]

        assert cell["amount"] == "80.00"
        assert cell["amount"] not in ("30.00", "50.00")

        # Три счётчика строк — не только деньги — обязаны накопиться по ОБОИМ
        # лотам: 2+3, 1+1, 1+2. Перезапись последней строкой VIEW дала бы
        # число из {2,3} / {1} / {1,2} соответственно — ни разу правильную
        # сумму. Эти счётчики отличают "нет в файле" (row_count == 0) от "не
        # оценивалась" (row_count > 0, rows_with_amount == 0) — заниженный
        # счёт молча искажал бы именно эту границу.
        assert cell["rows"]["row_count"] == 5
        assert cell["rows"]["row_count"] not in (2, 3)
        assert cell["rows"]["rows_with_amount"] == 2
        assert cell["rows"]["rows_with_amount"] not in (0, 1)
        assert cell["rows"]["rows_not_finite"] == 3
        assert cell["rows"]["rows_not_finite"] not in (1, 2)

        # Два лота с РАЗНЫМИ итогами предложения нарушают правило единогласия
        # (спека контура §2.13): файловый итог сметы недоступен, а не тихо
        # берёт итог одного из лотов.
        assert body["columns"][0]["convergence"]["file_total"] is None
        assert body["columns"][0]["convergence"]["reason"] == ss.CONV_FILE_TOTAL_UNAVAILABLE


def _row(body, code):
    return next(r for r in body["rows"] if r["code"] == code)


def _estimate_of(db, offer_id):
    return db.execute(sa.select(Estimate).where(Estimate.offer_id == offer_id)).scalar_one()


def _assert_column_totals_reconcile(body):
    """Аддиция 1 задачи 4: инвариант арифметики ответа, которого нет нигде в
    плане. Итог колонки (`columns[].total`) обязан посимвольно совпадать
    (а) с опубликованной суммой в `total.cells` той же колонки и (б) с суммой
    опубликованных сумм всех строк ВЕРХНЕГО уровня плюс Нераспределённого —
    считая по СТРОКАМ ответа (то, что видит и складывает читатель на экране),
    а не по внутренним `Decimal` до квантования. `None`-сумма строки (клетка
    не в состоянии `amount`) участвует как ноль — так же, как её трактует
    `compute_summary` при сборке `totals_gross`/`totals_shown` (`_sum_known`
    там неприменим: это чужая сумма, уже квантованная и распечатанная).
    Проверяется на обеих осях (валовой и нетто) вызывающим тестом — на нетто
    это же ловит расхождение «квантовать итог целиком» vs «квантовать каждую
    строку и сложить квантованное», которое резолюция C (§2.9) в
    `services/stage_summary.py` устраняет структурой кода, но которое до сих
    пор не было проверено ни одним тестом на живых числах."""
    for idx, col in enumerate(body["columns"]):
        total = col["total"]
        total_cell = body["total"]["cells"][idx]["amount"]
        assert total_cell == total
        if total is None:
            continue
        parts = [Decimal(r["cells"][idx]["amount"]) for r in body["rows"] if r["cells"][idx]["amount"] is not None]
        unalloc = body["unallocated"]["cells"][idx]["amount"]
        if unalloc is not None:
            parts.append(Decimal(unalloc))
        assert sum(parts, Decimal(0)) == Decimal(total)


class TestAmounts:
    def test_both_view_branches_and_additional_works_amount(self, db_session, grid):
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        six = _row(body, "6")
        assert six["cells"][1]["amount"] == "120.00"                # 96 позиций + 24 допработ (§2.4)
        assert six["cells"][1]["additional_works_amount"] == "24.00"
        # Аддиция 2 задачи 4: поле для суммы работ и поле для допработ — РАЗНЫЕ
        # числа; транспонирование этой пары (частая ошибка сборки JSON, две
        # соседние Decimal-суммы в одном `_cell`) обязано уронить тест, а не
        # пройти незамеченным на совпавшей паре значений.
        assert six["cells"][1]["amount"] != six["cells"][1]["additional_works_amount"]
        assert six["cells"][0]["additional_works_amount"] is None   # ветви нет вовсе

    def test_states_along_selected_path(self, db_session, grid):
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        two = _row(body, "2")
        assert [c["state"] for c in two["cells"]] == ["amount", "removed", "absent"]
        assert two["cells"][1]["change"]["kind"] == "removed"
        # removed → absent: отдельной дельты нет (матрица §2.6), факт виден по состояниям
        assert two["cells"][2]["change"] == {"kind": "none", "value": None, "direction": None, "reason": "no_amounts"}
        assert two["contribution"] == {"value": None, "direction": None, "reason": "absent_endpoint"}

    def test_excluding_middle_column_changes_neighbours(self, db_session, grid):
        full = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        short = crud_ss.build_stage_summary(db_session, grid.tender.id, [grid.a[0], grid.a[3]])
        assert _row(full, "6")["cells"][1]["change"]["value"] == "0.0"      # 120 → 120 (96 + 24 допработ)
        assert _row(full, "6")["cells"][2]["change"]["value"] == "-25.0"    # 120 → 90 относительно раунда 2
        assert _row(short, "6")["cells"][1]["change"]["value"] == "-25.0"   # 120 → 90 относительно раунда 1
        # путь = выбранные: без раунда 2 статья «2» идёт amount → absent, то есть «нет в файле», а не «снято»
        assert _row(full, "2")["cells"][1]["change"]["kind"] == "removed"
        assert _row(short, "2")["cells"][1]["change"]["kind"] == "disappeared"
        assert len(short["columns"]) == 2
        assert short["participant"]["rounds_with_estimate"] == 4 and short["kpi"]["stages_selected"] == 2

    def test_manual_override_marks_only_its_own_column(self, db_session, grid, admin_user):
        from services.category_override import set_override
        est = _estimate_of(db_session, grid.a[0])
        chapter = db_session.execute(
            sa.select(PositionItem).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == est.id, PositionItem.is_chapter.is_(True),
                   PositionItem.chapter_number_in_proposal == "2")
        ).scalar_one()
        seven = db_session.execute(sa.select(WorkCategory.id).where(WorkCategory.code == "7")).scalar_one()
        set_override(db_session, estimate_id=est.id, position_item_id=chapter.id, work_category_id=seven,
                     note=None, user_id=admin_user.id)
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        assert _row(body, "7")["cells"][0]["amount"] == "60.00"
        assert _row(body, "2")["cells"][0]["state"] == "absent"
        assert body["columns"][0]["manual_overrides"]["count"] == 1
        assert body["columns"][0]["manual_overrides"]["last_at"] is not None
        # Раунд с переносом (round1) несёт метку; оба соседа по пути (round2,
        # round4) — нет. Раньше проверялась только колонка 1, здесь "и больше
        # нигде" покрыто целиком, а не на две трети.
        assert body["columns"][1]["manual_overrides"] == {"count": 0, "last_at": None}
        assert body["columns"][2]["manual_overrides"] == {"count": 0, "last_at": None}


class TestVatAxis:
    def _set_rate(self, db, offer_id, rate):
        est = _estimate_of(db, offer_id)
        db.execute(sa.update(Proposal).where(Proposal.lot_id.in_(sa.select(Lot.id).where(Lot.estimate_id == est.id)))
                   .values(vat_rate=rate))
        db.flush()

    def test_single_rate_is_gross(self, db_session, grid):
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        assert body["display"]["tax_basis"] == "gross" and body["display"]["reason"] == "single_rate"
        # "Единая ставка — все колонки валовые" — раньше проверялась только
        # колонка 0; здесь заявление проверено на КАЖДОЙ из трёх колонок пути,
        # не на одной.
        assert [c["vat_state"] for c in body["columns"]] == ["known", "known", "known"]
        assert all(c["vat_rate_base"] is not None for c in body["columns"])
        assert [c["total"] for c in body["columns"]] == ["180.00", "120.00", "90.00"]
        _assert_column_totals_reconcile(body)   # аддиция 1 — ось gross

    def test_mixed_rates_is_net_with_rates_listed(self, db_session, grid):
        self._set_rate(db_session, grid.a[1], Decimal("0"))
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        assert body["display"]["tax_basis"] == "net"
        assert [Decimal(r) for r in body["display"]["rates_by_column"]] == [D("20"), D("0"), D("20")]
        assert body["columns"][0]["total"] == "150.00" and body["columns"][1]["total"] == "120.00"
        assert body["columns"][0]["convergence"]["categories_sum"] == "180.00"   # сходимость в валовых (§2.9)
        # Тот же приём аддиции 2: «показанный» (нетто) итог колонки и валовая
        # сумма категорий из сходимости живут в одном объекте колонки, и это
        # два РАЗНЫХ числа — перестановка полей при сборке JSON обязана упасть.
        assert body["columns"][0]["total"] != body["columns"][0]["convergence"]["categories_sum"]
        _assert_column_totals_reconcile(body)   # аддиция 1 — ось net (нет дрейфа округления)

    def test_twenty_plus_unknown_keeps_known_gross(self, db_session, grid):
        self._set_rate(db_session, grid.a[1], None)
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        assert body["display"]["tax_basis"] == "gross"
        col = body["columns"][1]
        assert col["vat_state"] == "unknown_vat_base" and col["total"] is None and col["bar_height_pct"] is None
        cell = _row(body, "6")["cells"][1]
        assert cell["state"] == "amount" and cell["amount"] is None
        assert cell["amount_unavailable_reason"] == "unknown_vat_base"
        assert cell["change"] == {"kind": "none", "value": None, "direction": None, "reason": "unknown_vat_base"}
        assert _row(body, "6")["cells"][2]["change"]["reason"] == "unknown_vat_base"
        assert body["track"]["available"] is True
        assert col["convergence"]["converged"] is True      # сходимость от ставки не зависит

    def test_estimate_override_wins_over_declared_rate(self, db_session, grid):
        """COALESCE(override, ставка предложения) — спека §1.3: назначенная вручную база
        побеждает заявленную, и одна такая колонка делает ось нетто."""
        est = _estimate_of(db_session, grid.a[1])
        est.vat_rate_base_override = Decimal("0")
        db_session.flush()
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        assert body["display"]["tax_basis"] == "net"
        assert Decimal(body["columns"][1]["vat_rate_base"]) == D("0")
        assert body["columns"][1]["total"] == "120.00" and body["columns"][0]["total"] == "150.00"

    def test_all_unknown_disables_track_and_basis(self, db_session, grid):
        for o in grid.a:
            self._set_rate(db_session, o, None)
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        assert body["display"]["tax_basis"] == "none"
        assert body["track"] == {"available": False, "reason": "no_comparable_totals"}
        assert _row(body, "6")["cells"][0]["state"] == "amount"


class TestConvergenceAndKpi:
    def test_convergence_null_when_file_total_not_unanimous(self, db_session, grid):
        est = _estimate_of(db_session, grid.a[0])
        db_session.execute(sa.delete(ProposalSummaryLine).where(
            ProposalSummaryLine.proposal_id.in_(sa.select(Proposal.id).join(Lot).where(Lot.estimate_id == est.id))))
        db_session.flush()
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        assert body["columns"][0]["convergence"]["converged"] is None
        assert body["columns"][0]["convergence"]["reason"] == "file_total_unavailable"
        assert body["columns"][0]["total"] == "180.00"                # итог колонки — не файловый (§2.9)

    def test_kpi_last_stage_positions_excludes_chapters(self, db_session, grid):
        """`kpi.categories_total` не проверяется против `len(body["rows"])`:
        производственный код определяет это поле КАК длину того же списка
        (`crud/stage_summary.py`, `"categories_total": ... len(result.rows)`),
        так что сравнение значения с собой через две записи не может упасть —
        оно ничего не доказывает и прячет смысл числа. Источник истины для
        смысла — СВОИМ SQL-запросом посчитанное число корневых статей
        классификатора (`work_categories where parent_id is null`): это то,
        что `categories_total` обязано отражать по спеке (все корни, есть
        деньги или нет), а не то, сколько строк решил вернуть расчёт."""
        root_count = db_session.execute(
            sa.select(sa.func.count()).select_from(WorkCategory).where(WorkCategory.parent_id.is_(None))
        ).scalar_one()
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        assert body["kpi"]["last_stage_positions"] == 1
        assert body["kpi"]["categories_with_amount"] == 1
        assert body["kpi"]["categories_total"] == root_count
        # Смысл числа: корни присутствуют ВСЕ, деньгами в последней колонке
        # несёт только один — поэтому это строго больше, а не совпадение.
        assert body["kpi"]["categories_total"] > body["kpi"]["categories_with_amount"]

    def test_rows_not_finite_reaches_cell(self, db_session, grid):
        est = _estimate_of(db_session, grid.a[3])
        db_session.execute(sa.update(PositionItem).where(
            PositionItem.proposal_id.in_(sa.select(Proposal.id).join(Lot).where(Lot.estimate_id == est.id)),
            PositionItem.is_chapter.is_(False)).values(total_cost_total=Decimal("NaN")))
        db_session.flush()
        body = crud_ss.build_stage_summary(db_session, grid.tender.id, grid.path)
        rows = _row(body, "6")["cells"][2]["rows"]
        assert rows == {"row_count": 1, "rows_with_amount": 0, "rows_not_finite": 1}


class TestHttp:
    URL = "/api/v1/tenders/{tid}/stage-summary"

    def test_member_reads_summary(self, member_client, db_session, grid):
        db_session.flush()
        r = member_client.get(self.URL.format(tid=grid.tender.id), params={"offers": grid.path})
        assert r.status_code == 200, r.text
        body = r.json()
        assert [c["stage_no"] for c in body["columns"]] == [1, 2, 4]
        assert body["display"]["price_level"] == "nominal"
        assert body["columns"][0]["total"] == "180.00"      # фикстура: раунд 1, итог участника А

    def test_422_detail_is_object_with_code_and_offers(self, member_client, grid):
        r = member_client.get(self.URL.format(tid=grid.tender.id), params={"offers": [grid.a[0], grid.b[0]]})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "single_participant"
        assert set(r.json()["detail"]["offers"]) == {grid.a[0], grid.b[0]}

    def test_404_detail_has_same_shape(self, member_client, grid):
        """Тело ошибки имеет ровно три ключа (code, message, offers), потому что клиент читает причину из code."""
        r = member_client.get(self.URL.format(tid=grid.tender.id), params={"offers": [grid.a[0], 999999]})
        assert r.status_code == 404
        detail = r.json()["detail"]
        assert set(detail.keys()) == {"code", "message", "offers"}
        assert detail["code"] == "offer_not_found"
        assert detail["offers"] == [999999]
        assert isinstance(detail["message"], str) and len(detail["message"]) > 0

    def test_missing_offers_param_is_422_too_few(self, member_client, grid):
        r = member_client.get(self.URL.format(tid=grid.tender.id))
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "too_few_offers"

    def test_unknown_tender_is_404_with_code(self, member_client, grid):
        r = member_client.get(self.URL.format(tid=999999), params={"offers": grid.path})
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "tender_not_found"
        assert set(r.json()["detail"]["offers"]) == set(grid.path)
