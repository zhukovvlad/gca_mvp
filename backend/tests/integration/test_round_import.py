"""Импорт сводной таблицы раунда (спека контура §2.5–§2.9): разбиение по
каноническому ИНН, атомарность, замена уровнем раунда, baseline по лотам,
один матчинг.

Payload — `round_payload`: форма ParseResult.data ПОСЛЕ постобработки, та же,
что отдаёт парсер на реальной сводной таблице.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import Contractor, Estimate, Lot, Offer, OfferPackage, PositionItem, Proposal
from parser import parse_worksheet
from services.category_resolution import CategoryResolver
from services.estimate_import import EstimateImportError
from services.round_import import (
    get_or_create_contractor,
    get_or_create_offer,
    get_or_create_package,
    import_round,
    replace_round_estimates,
    split_round_payload,
)
from services.unit_resolution import UnitResolver
from tests.payloads import baseline_proposal_block, position, proposal, round_payload
from tests.unit.parser.sheet_builders import KEYS_8, KEYS_GP_11, KEYS_TENDER_11, add_contractor_block, gp_sheet

pytestmark = pytest.mark.integration

def P1():
    return proposal([position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")],
                    title="ООО Первый", inn="7700000001")


def P2():
    return proposal([position(job_title="Работа", unit="м2", unit_cost_total="12", total_cost_total="12")],
                    title="ООО Второй", inn="77 0000 0002")


class TestSplitRoundPayload:
    def test_one_projection_per_canonical_inn_across_lots(self):
        data = round_payload([P1(), P2()], lots=2)
        projections, baseline = split_round_payload(data)
        assert [p.inn for p in projections] == ["7700000001", "7700000002"]
        assert baseline is None
        for p in projections:
            assert set(p.data["lots"]) == {"lot_1", "lot_2"}
            for lot in p.data["lots"].values():
                assert list(lot["proposals"]) == ["contractor_1"]

    def test_projection_keeps_its_own_block_only(self):
        data = round_payload([P1(), P2()])
        [first, second], _ = split_round_payload(data)
        assert first.data["lots"]["lot_1"]["proposals"]["contractor_1"]["title"] == "ООО Первый"
        assert second.data["lots"]["lot_1"]["proposals"]["contractor_1"]["title"] == "ООО Второй"

    def test_empty_inn_is_a_refusal(self):
        data = round_payload([proposal([position(job_title=None)], title="Без ИНН", inn="")])
        with pytest.raises(EstimateImportError, match="ИНН"):
            split_round_payload(data)

    def test_duplicate_inn_within_a_lot_is_a_refusal(self):
        data = round_payload([P1(), proposal([position(job_title=None)], title="Дубль", inn="7700000001")])
        with pytest.raises(EstimateImportError, match="дважды"):
            split_round_payload(data)

    def test_mismatched_inn_sets_across_lots_is_a_refusal(self):
        data = round_payload([P1(), P2()], lots=2)
        del data["lots"]["lot_2"]["proposals"]["contractor_2"]
        with pytest.raises(EstimateImportError, match="наборы участников"):
            split_round_payload(data)

    def test_valid_baseline_in_all_lots(self):
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])
        _, baseline = split_round_payload(round_payload([P1()], baseline=block, lots=2))
        assert baseline is not None
        assert baseline.lots_with_baseline == ("lot_1", "lot_2")
        assert baseline.lots_without == ()
        for lot in baseline.data["lots"].values():
            assert lot["proposals"]["contractor_1"]["title"] == "Расчетная стоимость"

    def test_baseline_valid_in_one_lot_of_two(self):
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])
        data = round_payload([P1()], baseline=block, lots=2)
        data["lots"]["lot_2"]["baseline_proposal"] = {"title": "Расчетная стоимость отсутствует"}
        _, baseline = split_round_payload(data)
        assert baseline.lots_with_baseline == ("lot_1",)
        assert baseline.lots_without == ("lot_2",)
        assert set(baseline.data["lots"]) == {"lot_1"}

    def test_real_parser_output_without_inn_is_refused(self):
        """Реальный парсер на синтетическом листе без строки ИНН — отказ, не тихий
        участник с пустым ключом (Global Constraint 7)."""
        ws = gp_sheet(KEYS_TENDER_11)
        add_contractor_block(ws, col_start=22, columns=KEYS_GP_11, title='ООО "Второй"')
        data = parse_worksheet(ws).data
        with pytest.raises(EstimateImportError, match="ИНН"):
            split_round_payload(data)


def synthetic_round_sheet(*, baseline_total: float | None = 90.0):
    """Сводная таблица раунда как ЛИСТ (спека §6): два участника с ИНН в
    строке под заголовком, блок «Расчетная стоимость» KEYS_8, одна позиция и
    блок итогов. Геометрия — та же, что у `_tender_sheet_with_baseline` в
    test_estimate.py: участник 1 — J..T (10..20), участник 2 — V..AF (22..32),
    база — AH..AO (34..41); итог базы — total_cost.total, индекс 7 → колонка 41.
    Настоящий `parse_worksheet` даёт форму ParseResult.data, которую режет
    `split_round_payload`: расхождение формы поймает CI, а не стенд."""
    ws = gp_sheet(KEYS_TENDER_11, inn="7700000001", address="г. Тест, ул. Первая, 1")
    add_contractor_block(ws, col_start=22, columns=KEYS_TENDER_11, title='ООО "Второй"',
                         inn="77 0000 0002", address="г. Тест, ул. Вторая, 2")
    add_contractor_block(ws, col_start=34, columns=KEYS_8, title="Расчетная стоимость", vat_suffix=None)
    ws.cell(row=12, column=1, value=2)
    ws.cell(row=12, column=2, value="1")
    ws.cell(row=12, column=4, value="Работа")
    for col_start in (10, 22):
        ws.cell(row=12, column=col_start + 8, value=100.0)   # total_cost.total участника
        ws.cell(row=12, column=col_start + 10, value=-0.05)  # % от р/с
    ws.cell(row=12, column=34 + 7, value=90.0)               # total_cost.total базы
    ws.merge_cells(start_row=14, start_column=1, end_row=14, end_column=5)
    ws.cell(row=14, column=1, value="ИТОГО, руб. с учетом НДС")
    for col_start in (10, 22):
        ws.cell(row=14, column=col_start + 8, value=100.0)
    if baseline_total is not None:
        ws.cell(row=14, column=34 + 7, value=baseline_total)
    return ws


class TestRealParserPath:
    """Положительный путь через НАСТОЯЩИЙ парсер (спека §6): синтетический XLSX
    → parse_worksheet → split → import_round → 3 сметы."""

    def test_two_participants_and_baseline_from_a_real_sheet(self, db_session, factories):
        data = parse_worksheet(synthetic_round_sheet()).data

        projections, baseline = split_round_payload(data)
        assert [p.inn for p in projections] == ["7700000001", "7700000002"]
        assert baseline is not None and baseline.lots_with_baseline == ("lot_1",)

        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _run_round(db_session, rnd, data)
        assert outcome.estimates_created == 3
        # `gp_sheet` кладёт маркеры лота в A11/B11 (`ws["A11"] = 1`, `ws["B11"] = 1`),
        # и парсер читает их как «позицию 1» с job_title из D11 — тот же артефакт,
        # что `_tender_sheet_with_baseline` в test_estimate.py обходит, глядя
        # только на `positions["2"]`. Фильтр по номеру ИЗ ФАЙЛА (не по DICT-ключу
        # postprocess) выделяет настоящую, расценённую строку — ряд 12.
        rows = db_session.execute(
            sa.select(Proposal.is_baseline, PositionItem.deviation_from_baseline_cost)
            .join(PositionItem, PositionItem.proposal_id == Proposal.id)
            .where(PositionItem.item_number_in_proposal == "2")
        ).all()
        assert sorted(str(d) for b, d in rows if not b) == ["-0.05", "-0.05"]
        assert all(d is None for b, d in rows if b)

    def test_empty_baseline_on_a_real_sheet_gives_two_estimates(self, db_session, factories):
        data = parse_worksheet(synthetic_round_sheet(baseline_total=None)).data
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _run_round(db_session, rnd, data)
        assert outcome.estimates_created == 2
        assert any("не заполнена" in w for w in outcome.warnings)


class TestGetOrCreate:
    def test_new_inn_creates_contractor_and_warns(self, db_session, factories):
        warnings: list[str] = []
        c = get_or_create_contractor(db_session, inn="7700000009", title="ООО Новый",
                                     address=None, accreditation=None, warnings=warnings)
        assert db_session.get(Contractor, c.id).inn == "7700000009"
        assert any("заведён" in w and "7700000009" in w for w in warnings)

    def test_known_inn_is_reused_and_card_untouched(self, db_session, factories):
        existing = factories.ContractorFactory.create(inn="7700000001", title="ООО Карточка")
        db_session.flush()
        warnings: list[str] = []
        c = get_or_create_contractor(db_session, inn="7700000001", title="ООО Из файла",
                                     address=None, accreditation=None, warnings=warnings)
        assert c.id == existing.id
        assert db_session.get(Contractor, c.id).title == "ООО Карточка"
        # Расхождение имени — забота compare_header, не get-or-create: здесь тихо.
        assert warnings == []

    def test_name_mismatch_is_reported_exactly_once_per_lot(self, db_session, factories):
        """Одно расхождение — одно предупреждение (из compare_header), а не два."""
        factories.ContractorFactory.create(inn="7700000001", title="ООО Карточка")
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _run_round(db_session, rnd, round_payload([P1()]))  # в файле — «ООО Первый»
        mismatches = [w for w in outcome.warnings if "Подрядчик" in w and "ООО Первый" in w and "ООО Карточка" in w]
        assert len(mismatches) == 1

    def test_package_and_offer_are_idempotent(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        contractor = factories.ContractorFactory.create()
        db_session.flush()
        p1 = get_or_create_package(db_session, tender_id=rnd.tender_id, contractor_id=contractor.id)
        p2 = get_or_create_package(db_session, tender_id=rnd.tender_id, contractor_id=contractor.id)
        assert p1.id == p2.id
        o1 = get_or_create_offer(db_session, tender_id=rnd.tender_id, round_id=rnd.id, package_id=p1.id)
        o2 = get_or_create_offer(db_session, tender_id=rnd.tender_id, round_id=rnd.id, package_id=p1.id)
        assert o1.id == o2.id
        assert db_session.execute(sa.select(sa.func.count()).select_from(Offer)).scalar_one() == 1


class TestReplaceRoundEstimates:
    def test_no_estimates_is_a_noop(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        assert replace_round_estimates(db_session, rnd.id, replace=False, warnings=[]) == []

    def test_existing_estimates_without_replace_refuse(self, db_session, factories):
        offer = factories.OfferFactory.create()
        db_session.add(Estimate(offer_id=offer.id))
        db_session.flush()
        with pytest.raises(EstimateImportError, match="replace"):
            replace_round_estimates(db_session, offer.round_id, replace=False, warnings=[])

    def test_replace_removes_all_round_estimates_but_keeps_offers(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        offer_a = factories.OfferFactory.create(round=rnd)
        offer_b = factories.OfferFactory.create(round=rnd)
        db_session.add_all([Estimate(offer_id=offer_a.id), Estimate(offer_id=offer_b.id), Estimate(round_id=rnd.id)])
        db_session.flush()

        removed = replace_round_estimates(db_session, rnd.id, replace=True, warnings=[])

        assert len(removed) == 3
        assert db_session.execute(sa.select(sa.func.count()).select_from(Estimate)).scalar_one() == 0
        assert db_session.execute(sa.select(sa.func.count()).select_from(Offer)).scalar_one() == 2


def _run_round(db, rnd, data, *, replace=False):
    return import_round(
        db, tender_round=rnd, data=data, parser_version="4.0.0", import_job_id=None,
        replace=replace, unit_resolver=UnitResolver(db), category_resolver=CategoryResolver.from_db(db),
    )


def _estimates_of_round(db, rnd):
    offer_ids = sa.select(Offer.id).where(Offer.round_id == rnd.id)
    return db.execute(
        sa.select(Estimate).where(sa.or_(Estimate.round_id == rnd.id, Estimate.offer_id.in_(offer_ids)))
    ).scalars().all()


class TestImportRound:
    def test_two_participants_and_baseline_give_three_estimates(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])

        outcome = _run_round(db_session, rnd, round_payload([P1(), P2()], baseline=block))

        assert outcome.estimates_created == 3
        estimates = _estimates_of_round(db_session, rnd)
        assert len(estimates) == 3
        assert sum(1 for e in estimates if e.round_id == rnd.id) == 1
        assert db_session.execute(sa.select(sa.func.count()).select_from(OfferPackage)).scalar_one() == 2
        inns = set(db_session.execute(sa.select(Contractor.inn)).scalars())
        assert {"7700000001", "7700000002"} <= inns

    def test_without_baseline_two_estimates_and_a_warning(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        outcome = _run_round(db_session, rnd, round_payload([P1(), P2()]))
        assert outcome.estimates_created == 2
        assert any("не заполнена" in w for w in outcome.warnings)

    def test_partial_baseline_names_the_lot_without_it(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])
        data = round_payload([P1()], baseline=block, lots=2)
        data["lots"]["lot_2"]["baseline_proposal"] = {"title": "Расчетная стоимость отсутствует"}

        outcome = _run_round(db_session, rnd, data)

        baseline = next(e for e in _estimates_of_round(db_session, rnd) if e.round_id == rnd.id)
        lot_keys = set(db_session.execute(sa.select(Lot.lot_key).where(Lot.estimate_id == baseline.id)).scalars())
        assert lot_keys == {"lot_1"}
        assert any("lot_2" in w for w in outcome.warnings)

    def test_positions_to_match_are_pooled_across_all_estimates(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])
        outcome = _run_round(db_session, rnd, round_payload([P1(), P2()], baseline=block))
        # по одной расценённой позиции у двух участников и у базы
        assert len(outcome.positions_to_match) == 3

    def test_second_upload_without_replace_refuses_before_writing(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        _run_round(db_session, rnd, round_payload([P1()]))
        before = {e.id for e in _estimates_of_round(db_session, rnd)}
        with pytest.raises(EstimateImportError, match="replace"):
            _run_round(db_session, rnd, round_payload([P1(), P2()]))
        assert {e.id for e in _estimates_of_round(db_session, rnd)} == before

    def test_replace_drops_a_participant_missing_from_the_new_file_but_keeps_his_offer(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        _run_round(db_session, rnd, round_payload([P1(), P2()]))

        outcome = _run_round(db_session, rnd, round_payload([P1()]), replace=True)

        assert outcome.estimates_created == 1
        offers = db_session.execute(sa.select(Offer).where(Offer.round_id == rnd.id)).scalars().all()
        assert len(offers) == 2  # ячейка второго осталась, сметы у неё нет
        estimates = _estimates_of_round(db_session, rnd)
        assert len(estimates) == 1
        assert any("удалено смет предыдущей загрузки — 2" in w for w in outcome.warnings)

    def test_deviation_lands_on_participants_when_baseline_is_valid(self, db_session, factories):
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()
        p = P1()
        p["contractor_items"]["positions"]["1"]["deviation_from_baseline_cost"] = "-0.05"
        block = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])

        _run_round(db_session, rnd, round_payload([p], baseline=block))

        rows = db_session.execute(
            sa.select(Proposal.is_baseline, PositionItem.deviation_from_baseline_cost)
            .join(PositionItem, PositionItem.proposal_id == Proposal.id)
        ).all()
        by_kind = {is_baseline: dev for is_baseline, dev in rows}
        assert str(by_kind[False]) == "-0.05"
        assert by_kind[True] is None

    def test_failure_on_second_participant_rolls_back_the_first(self, db_session, factories, monkeypatch):
        """Атомарность раунда: инъекция отказа на втором вызове import_estimate."""
        import services.round_import as module

        real = module.import_estimate
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise EstimateImportError("инъекция отказа")
            return real(*args, **kwargs)

        monkeypatch.setattr(module, "import_estimate", flaky)
        rnd = factories.TenderRoundFactory.create()
        db_session.flush()

        with pytest.raises(EstimateImportError, match="инъекция"), db_session.begin_nested():
            _run_round(db_session, rnd, round_payload([P1(), P2()]))

        assert _estimates_of_round(db_session, rnd) == []
