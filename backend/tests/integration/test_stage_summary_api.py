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
from models import Contractor, Estimate, Offer, OfferPackage, ProposalSummaryLine, TenderRound
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
