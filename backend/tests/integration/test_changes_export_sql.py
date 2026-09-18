"""Чтение входов книги «Изменения КП» — `crud/changes_export.py` (спека
2026-09-16-tender-changes-export-design.md §2.1-§2.4, §2.9; план, Task 3).

Фикстуры строятся ЧЕРЕЗ ФАБРИКИ, не через парсер: `import_round` матчинг не
запускает вовсе (докстрока `services.round_import.import_round` — «Матчинг
здесь НЕ вызывается»), а этому файлу нужен полный контроль над
`catalog_positions.kind` (включая `HEADER`/`LOT_HEADER`/`TRASH`) и над
`catalog_position_id IS NULL` — то же решение, что `test_analytics_api.py`
уже применяет для тех же полей.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import changes_export as crud_cx
from crud.common import DomainError
from crud.project_passport import CATEGORY_TOTALS
from finance import money_round
from models import CatalogKind, EstimateAdditionalWork, WorkCategory
from money.price import is_price, is_weight
from services import changes_export as svc_cx
from services.stage_summary import TAX_GROSS, TAX_NET, TAX_NONE, to_shown

pytestmark = pytest.mark.integration

D = Decimal


@contextmanager
def count_queries(db_session):
    """Копия `_count_queries`/`count_queries` из `test_stage_summary_api.py` —
    общий хелпер не заводится (Р10, план фичи)."""
    counter = {"n": 0}
    bind = db_session.get_bind()

    def _tick(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    sa.event.listen(bind, "before_cursor_execute", _tick)
    try:
        yield counter
    finally:
        sa.event.remove(bind, "before_cursor_execute", _tick)


# ---------------------------------------------------------------------------
#  Помощники сборки фикстур — ЧЕРЕЗ ФАБРИКИ, полный контроль над каталогом.
# ---------------------------------------------------------------------------

def _category_id(db_session, code: str) -> int:
    return db_session.execute(sa.select(WorkCategory.id).where(WorkCategory.code == code)).scalar_one()


def _category_sort_order(db_session, code: str) -> int:
    return db_session.execute(sa.select(WorkCategory.sort_order).where(WorkCategory.code == code)).scalar_one()


def _round(factories, tender, stage_no: int):
    return factories.TenderRoundFactory.create(tender=tender, stage_no=stage_no)


def _estimate(factories, *, round_, package, vat_rate=D("20"), lot_key="lot_1"):
    """Смета одного участника на одном этапе: `offer_id` — единственный владелец
    (спека контура), `lot_key` — доменный ключ допработы (§2.2)."""
    offer = factories.OfferFactory.create(round=round_, package=package, tender_id=round_.tender_id)
    estimate = factories.EstimateFactory.create(contract=None, offer_id=offer.id)
    lot = factories.LotFactory.create(estimate=estimate, lot_key=lot_key)
    proposal = factories.ProposalFactory.create(lot=lot, contractor=package.contractor, vat_rate=vat_rate)
    return estimate, lot, proposal


def _participant(factories, tender, *, title):
    contractor = factories.ContractorFactory.create(title=title)
    package = factories.OfferPackageFactory.create(tender=tender, contractor=contractor)
    return package, contractor


def _chapter(factories, proposal, *, key="1", category_id=None, source="manual"):
    kwargs = {}
    if category_id is not None:
        kwargs["work_category_id"] = category_id
        kwargs["category_source"] = source
        kwargs["smr_article_raw"] = "x"
    return factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=True, position_key_in_proposal=key,
        job_title_in_proposal=f"Раздел {key}", **kwargs,
    )


def _row(factories, proposal, chapter, *, key, number=None, catalog_position=None,
         total=D("100.00"), works=None, materials=None, indirect=None,
         unit_cost=D("10.00"), qty=D("10"),
         unit_works=None, unit_materials=None, unit_indirect=None):
    """`unit_works`/`unit_materials`/`unit_indirect` — `unit_cost_*` (цена
    составляющей ЗА ЕДИНИЦУ), НЕЗАВИСИМЫЕ от `works`/`materials`/`indirect`
    (`total_cost_*`, абсолютная составляющая строки): по умолчанию равны им
    же, деля на `qty`, только для удобства фикстур, которым разница
    unit/total не важна; тесты, которым разница важна, задают оба явно и
    РАЗНЫМИ."""
    return factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=False, position_key_in_proposal=key,
        job_title_in_proposal=f"Работа {key}", chapter_item_id=chapter.id,
        catalog_position=catalog_position, item_number_in_proposal=number or key,
        unit_cost_total=unit_cost, suggested_quantity=qty, quantity=qty, total_cost_total=total,
        total_cost_works=works, total_cost_materials=materials, total_cost_indirect_costs=indirect,
        unit_cost_works=unit_works, unit_cost_materials=unit_materials, unit_cost_indirect_costs=unit_indirect,
    )


def _additional_work(session, proposal, *, ordinal=1, category_id=None, chapter_ref_raw=None,
                      amount=D("24.00"), title="Допработа"):
    """Без фабрики (её нет намеренно — тот же довод, что у `test_category_totals_view.py`)."""
    kwargs: dict = dict(proposal_id=proposal.id, ordinal=ordinal, title=title, total_amount=amount)
    if category_id is not None or chapter_ref_raw is not None:
        kwargs["chapter_ref_raw"] = chapter_ref_raw or "3.2.2"
        kwargs["raw_line"] = "исходная строка сведений"
        if category_id is not None:
            kwargs["work_category_id"] = category_id
    work = EstimateAdditionalWork(**kwargs)
    session.add(work)
    session.flush()
    return work


# ---------------------------------------------------------------------------
#  A3.1 — отказы
# ---------------------------------------------------------------------------

class TestRefusals:
    def test_unknown_tender_is_404(self, db_session):
        with pytest.raises(DomainError) as exc:
            crud_cx.load_book(db_session, 999999)
        assert exc.value.status_code == 404
        assert exc.value.code == crud_cx.CODE_TENDER_NOT_FOUND

    def test_tender_without_two_estimate_participant_is_422_not_empty_book(self, db_session, factories):
        """Тендер с одним раундом — сравнивать нечего: 422, а не пустая книга."""
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        db_session.flush()
        package, _contractor = _participant(factories, tender, title="ООО Одна смета")
        _estimate(factories, round_=r1, package=package)
        db_session.flush()

        with pytest.raises(DomainError) as exc:
            crud_cx.load_book(db_session, tender.id)
        assert exc.value.status_code == 422
        assert exc.value.code == crud_cx.CODE_NO_COMPARABLE


# ---------------------------------------------------------------------------
#  A3.2 — участник считается по СМЕТАМ, не по предложениям
# ---------------------------------------------------------------------------

class TestParticipantByEstimatesNotOffers:
    def test_offer_without_estimate_does_not_count(self, db_session, factories):
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        r2 = _round(factories, tender, 2)
        db_session.flush()

        # А: две сметы — попадает в книгу.
        pkg_a, _ = _participant(factories, tender, title="ООО А (две сметы)")
        _estimate(factories, round_=r1, package=pkg_a)
        _estimate(factories, round_=r2, package=pkg_a)

        # Б: два ПРЕДЛОЖЕНИЯ, но смета есть только у одного раунда — НЕ попадает.
        pkg_b, _ = _participant(factories, tender, title="ООО Б (одна смета)")
        _estimate(factories, round_=r1, package=pkg_b)
        offer_b2 = factories.OfferFactory.create(round=r2, package=pkg_b, tender_id=tender.id)
        db_session.flush()
        assert offer_b2.id is not None  # предложение существует, сметы у него нет

        sheets = crud_cx.load_book(db_session, tender.id)
        titles = {s.participant_title for s in sheets}
        assert "ООО А (две сметы)" in titles
        assert "ООО Б (одна смета)" not in titles


# ---------------------------------------------------------------------------
#  A3.3 — все этапы участника, без подмножества
# ---------------------------------------------------------------------------

class TestAllStagesInOrder:
    def test_all_stages_present_in_stage_no_order(self, db_session, factories):
        tender = factories.TenderFactory.create()
        rounds = [_round(factories, tender, n) for n in (1, 2, 3)]
        db_session.flush()
        package, _ = _participant(factories, tender, title="ООО Три этапа")
        for r in rounds:
            _estimate(factories, round_=r, package=package)
        db_session.flush()

        sheets = crud_cx.load_book(db_session, tender.id)
        assert len(sheets) == 1
        assert [s.stage_no for s in sheets[0].stages] == [1, 2, 3]


# ---------------------------------------------------------------------------
#  A3.4 — предикаты цены/веса совпадают с money.price на семи граничных входах
# ---------------------------------------------------------------------------

class TestPricePredicateOracle:
    BOUNDARY = [None, D("0"), D("-5"), D("NaN"), D("Infinity"), D("-Infinity"), D("5")]
    IDS = ["null", "zero", "negative", "nan", "infinity", "neg_infinity", "positive"]

    @pytest.mark.parametrize("value", BOUNDARY, ids=IDS)
    def test_price_ok_matches_is_price(self, db_session, value):
        col = sa.bindparam("v", value, type_=sa.Numeric)
        result = db_session.execute(sa.select(crud_cx._price_ok(col))).scalar()
        assert bool(result) is is_price(value), f"value={value!r}"

    @pytest.mark.parametrize("value", BOUNDARY, ids=IDS)
    def test_weight_ok_matches_is_weight(self, db_session, value):
        col = sa.bindparam("v", value, type_=sa.Numeric)
        result = db_session.execute(sa.select(crud_cx._weight_ok(col))).scalar()
        assert bool(result) is is_weight(value), f"value={value!r}"


# ---------------------------------------------------------------------------
#  A3.5, A3.6, A3.7 — три множества строк
# ---------------------------------------------------------------------------

class TestThreeSets:
    def _one_group(self, db_session, factories, *, rows_kwargs, category_code="6"):
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        r2 = _round(factories, tender, 2)  # второй этап — только чтобы участник попал в книгу
        db_session.flush()
        package, _ = _participant(factories, tender, title="ООО Группа")
        est1, _lot1, prop1 = _estimate(factories, round_=r1, package=package)
        est2, _lot2, prop2 = _estimate(factories, round_=r2, package=package)
        category_id = _category_id(db_session, category_code)
        chapter = _chapter(factories, prop1, category_id=category_id)
        for kw in rows_kwargs:
            _row(factories, prop1, chapter, **kw)
        _chapter(factories, prop2, category_id=category_id)
        db_session.flush()
        raw = crud_cx._groups(db_session, [est1.id, est2.id])
        assert est1.id in raw
        return raw[est1.id]

    def test_presence_counts_regardless_of_money(self, db_session, factories):
        """`rows_all` — по ВСЕМ строкам, независимо от пригодности денег: строка
        с нефинитной суммой присутствует, но не входит в сумму (спека §2.4)."""
        groups = self._one_group(db_session, factories, rows_kwargs=[
            dict(key="a", number="1", total=D("100.00")),
            dict(key="b", number="2", total=D("NaN")),
        ])
        assert len(groups) == 1
        group = groups[0]
        assert group.rows_all == 2
        assert group.rows_with_amount == 1
        assert group.amount == D("100.00")   # NaN-строка не входит в сумму

    def test_price_is_only_from_rows_where_both_price_and_weight_are_fit(self, db_session, factories):
        """Цена/вес матрицы — по строкам, где пригодны И цена, И вес (спека §2.4);
        строка с нулевой ценой — «цены нет», а не ноль."""
        groups = self._one_group(db_session, factories, rows_kwargs=[
            dict(key="a", number="1", unit_cost=D("10.00"), qty=D("10"), total=D("100.00")),
            dict(key="b", number="2", unit_cost=D("0"), qty=D("5"), total=D("0.00")),
        ])
        group = groups[0]
        assert group.rows_all == 2
        assert group.rows_priced == 1
        assert group.price_num == D("100.00")
        assert group.price_den == D("10")

    def test_unpriced_row_does_not_dilute_group_price(self, db_session, factories):
        """Нерасценённая строка не входит в матрицу вовсе — знаменатель равен весу
        ОДНОЙ пригодной строки, а не сумме весов обеих (спека §1.7, §2.4).

        Контрпример спеки: свёртка «сумма / свёрнутый объём» дала бы
        `Σamount / Σqty` = `(100+50)/(10+5) = 10`, вдвое ниже верной цены 20."""
        groups = self._one_group(db_session, factories, rows_kwargs=[
            dict(key="a", number="1", unit_cost=D("20.00"), qty=D("5"), total=D("100.00")),
            dict(key="b", number="2", unit_cost=D("0"), qty=D("10"), total=D("50.00")),
        ])
        group = groups[0]
        assert group.price_den == D("5")             # только вес пригодной строки
        assert group.price_num == D("100.00")
        naive_price = (group.amount) / D("15")        # свёрнутая сумма / свёрнутый объём — ЛОВУШКА
        correct_price = group.price_num / group.price_den
        assert correct_price == D("20")
        assert naive_price != correct_price

    def test_rows_with_mix_requires_all_three_components_finite(self, db_session, factories):
        """`rows_with_mix` — конечен итог И все три составляющие; строка с `NULL`
        в одной составляющей остаётся в `rows_with_amount`, но не в `rows_with_mix`
        (спека §2.2, DoD 15 — классификацию считает именно SQL)."""
        groups = self._one_group(db_session, factories, rows_kwargs=[
            dict(key="a", number="1", total=D("100.00"), works=D("60.00"),
                 materials=D("30.00"), indirect=D("10.00")),
            dict(key="b", number="2", total=D("50.00"), works=D("50.00"),
                 materials=None, indirect=D("0.00")),
        ])
        group = groups[0]
        assert group.rows_with_amount == 2
        assert group.rows_with_mix == 1
        assert group.components.works == D("110.00")
        assert group.components.materials == D("30.00")   # NULL строки — не ноль, а отсутствие вклада


# ---------------------------------------------------------------------------
#  Ревью после гейта 3: числители состава НА ЕДИНИЦУ обязаны идти от
#  `unit_cost_*`, а не от `total_cost_*` (спека §2.4, `GROUPS_SQL` гейта 1).
#  Абсолютный состав (`c_works`/`c_materials`/`c_indirect`, `TestThreeSets`
#  выше) от `total_cost_*` — не трогается и остаётся верным.
# ---------------------------------------------------------------------------

class TestUnitComponentsNumUsesUnitCost:
    def test_unit_components_num_is_unit_cost_times_quantity_not_total_cost(self, db_session, factories):
        """Вход, где `unit_cost_works` и `total_cost_works` РАЗЛИЧНЫ и `qty != 1`
        — иначе оба варианта формулы совпали бы и тест был бы вакуозным
        (`unit_cost_works=6.00`, `total_cost_works=55.00`, `qty=10`: верно
        `unit_works_num = 60.00`; дефект отдавал `550.00`)."""
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        r2 = _round(factories, tender, 2)
        db_session.flush()
        package, _ = _participant(factories, tender, title="ООО Состав на единицу")
        est1, _lot1, prop1 = _estimate(factories, round_=r1, package=package)
        est2, _lot2, prop2 = _estimate(factories, round_=r2, package=package)
        cat6 = _category_id(db_session, "6")
        chapter1 = _chapter(factories, prop1, category_id=cat6)
        _row(factories, prop1, chapter1, key="a", number="1",
             unit_cost=D("10.00"), qty=D("10"), total=D("100.00"),
             unit_works=D("6.00"), unit_materials=D("3.00"), unit_indirect=D("1.00"),
             works=D("55.00"), materials=D("35.00"), indirect=D("10.00"))
        _chapter(factories, prop2, category_id=cat6)  # второй этап — только чтобы участник попал в книгу
        db_session.flush()

        raw = crud_cx._groups(db_session, [est1.id, est2.id])[est1.id]
        assert len(raw) == 1
        group = raw[0]
        assert group.article.code == "6"

        # Абсолютный состав — от total_cost_* — НЕ ТРОГАЕТСЯ и остаётся верным.
        assert group.components.works == D("55.00")
        assert group.components.materials == D("35.00")
        assert group.components.indirect == D("10.00")

        # Числитель НА ЕДИНИЦУ — от unit_cost_* × qty, а не от total_cost_* × qty.
        assert group.unit_components_num.works == D("60.00")      # 6.00 * 10
        assert group.unit_components_num.materials == D("30.00")  # 3.00 * 10
        assert group.unit_components_num.indirect == D("10.00")   # 1.00 * 10
        assert group.unit_components_num.works != D("550.00")     # 55.00 * 10 — дефект


class TestRouteIgnoresVolumeOnlyGrowthAsComposition:
    def test_volume_growth_alone_is_not_reported_as_composition_change(self, db_session, factories):
        """Спека §2.7: основание «состав» считается НА ЕДИНИЦУ объёма именно
        потому, что по абсолютам один лишь рост объёма выглядел бы сменой
        состава. Вход: `unit_cost_*` и цена (`unit_cost_total`) НЕ меняются
        между этапами, `suggested_quantity` УДВАИВАЕТСЯ — маршрут обязан
        назвать «объём» и НЕ назвать «состав»."""
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        r2 = _round(factories, tender, 2)
        db_session.flush()
        package, _ = _participant(factories, tender, title="ООО Только объём")
        est1, _lot1, prop1 = _estimate(factories, round_=r1, package=package)
        est2, _lot2, prop2 = _estimate(factories, round_=r2, package=package)
        cat6 = _category_id(db_session, "6")
        catalog_position = factories.CatalogPositionFactory.create(kind=CatalogKind.TO_REVIEW.value)

        chapter1 = _chapter(factories, prop1, category_id=cat6)
        _row(factories, prop1, chapter1, key="a", number="1", catalog_position=catalog_position,
             unit_cost=D("10.00"), qty=D("10"), total=D("100.00"),
             unit_works=D("6.00"), unit_materials=D("3.00"), unit_indirect=D("1.00"),
             works=D("60.00"), materials=D("30.00"), indirect=D("10.00"))

        chapter2 = _chapter(factories, prop2, category_id=cat6)
        _row(factories, prop2, chapter2, key="a", number="1", catalog_position=catalog_position,
             unit_cost=D("10.00"), qty=D("20"), total=D("200.00"),        # тот же unit_cost_total, объём удвоен
             unit_works=D("6.00"), unit_materials=D("3.00"), unit_indirect=D("1.00"),  # unit_cost_* НЕ МЕНЯЮТСЯ
             works=D("120.00"), materials=D("60.00"), indirect=D("20.00"))
        db_session.flush()

        sheets = crud_cx.load_book(db_session, tender.id)
        built = svc_cx.build_sheet(sheets[0])
        row = next(r for r in built.rows if r.kind == svc_cx.KIND_WORK)

        # Сумма и объём легитимно меняются вместе с объёмом (реалистичный вход:
        # `total_cost_* = unit_cost_* × qty`) — это не предмет проверки.
        # Предмет — «состав» НЕ обязан появиться, раз состав НА ЕДИНИЦУ не
        # менялся: до правки он появляется, потому что числитель считался от
        # `total_cost_*`, который масштабируется вместе с объёмом.
        assert row.route == ["Э1→Э2: сумма, объём"]
        assert "состав" not in row.route[0]


# ---------------------------------------------------------------------------
#  A3.8 — статья через chapter_item_id -> work_category_id, ручной разнос виден
# ---------------------------------------------------------------------------

class TestArticleViaChapter:
    def test_article_follows_chapter_work_category_including_manual_reassignment(self, db_session, factories):
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        r2 = _round(factories, tender, 2)
        db_session.flush()
        package, _ = _participant(factories, tender, title="ООО Разнос")
        est1, _l1, prop1 = _estimate(factories, round_=r1, package=package)
        est2, _l2, prop2 = _estimate(factories, round_=r2, package=package)
        cat6 = _category_id(db_session, "6")
        cat2 = _category_id(db_session, "2")

        chapter = _chapter(factories, prop1, category_id=cat6, source="file")
        _row(factories, prop1, chapter, key="a", total=D("100.00"))
        _chapter(factories, prop2, category_id=cat2, source="manual")  # разнос вручную на ДРУГУЮ статью
        db_session.flush()

        raw = crud_cx._groups(db_session, [est1.id, est2.id])
        assert raw[est1.id][0].article.code == "6"

        # Второй этап без работ под разделом — статья не появится в _groups (нет
        # строк position_items), но сам факт смены статьи проверяем на первом
        # этапе прямым чтением работы под РАЗНЕСЁННЫМ разделом.
        chapter2 = _chapter(factories, prop2, category_id=cat2, source="manual", key="2")
        _row(factories, prop2, chapter2, key="a", total=D("70.00"))
        db_session.flush()
        raw2 = crud_cx._groups(db_session, [est1.id, est2.id])
        codes_stage2 = {g.article.code for g in raw2[est2.id]}
        assert "2" in codes_stage2   # разнос виден: читается ТЕКУЩИЙ work_category_id раздела


# ---------------------------------------------------------------------------
#  A3.9 — ключ допработы: lots.lot_key, не lots.id
# ---------------------------------------------------------------------------

class TestAdditionalWorkKey:
    def test_same_lot_key_different_lot_id_collapses_to_one_sheet_row(self, db_session, factories):
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        r2 = _round(factories, tender, 2)
        db_session.flush()
        package, _ = _participant(factories, tender, title="ООО Допработы")
        est1, _l1, prop1 = _estimate(factories, round_=r1, package=package, lot_key="lot_1")
        est2, _l2, prop2 = _estimate(factories, round_=r2, package=package, lot_key="lot_1")
        assert _l1.id != _l2.id and _l1.lot_key == _l2.lot_key == "lot_1"

        cat6 = _category_id(db_session, "6")
        _additional_work(db_session, prop1, category_id=cat6, chapter_ref_raw="3.2.2", amount=D("24.00"))
        _additional_work(db_session, prop2, category_id=cat6, chapter_ref_raw="3.2.2", amount=D("30.00"))
        db_session.flush()

        sheets = crud_cx.load_book(db_session, tender.id)
        sheet = sheets[0]
        built = svc_cx.build_sheet(sheet)
        additional_rows = [r for r in built.rows if r.kind == svc_cx.KIND_ADDITIONAL]
        assert len(additional_rows) == 1   # НЕ два — ключ устоял на разных lots.id


# ---------------------------------------------------------------------------
#  A3.13 — база НДС: override побеждает, разногласие даёт None
# ---------------------------------------------------------------------------

class TestVatRateBase:
    def test_override_wins_over_declared_rate(self, db_session, factories):
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        db_session.flush()
        package, _ = _participant(factories, tender, title="ООО Ставка")
        est, _lot, _prop = _estimate(factories, round_=r1, package=package, vat_rate=D("20"))
        est.vat_rate_base_override = D("0")
        db_session.flush()

        rates = crud_cx._rates(db_session, [est.id])
        assert rates[est.id] == D("0")

    def test_disagreement_between_lots_gives_none(self, db_session, factories):
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        db_session.flush()
        package, _ = _participant(factories, tender, title="ООО Разногласие")
        offer = factories.OfferFactory.create(round=r1, package=package, tender_id=tender.id)
        estimate = factories.EstimateFactory.create(contract=None, offer_id=offer.id)
        lot1 = factories.LotFactory.create(estimate=estimate, lot_key="lot_1")
        lot2 = factories.LotFactory.create(estimate=estimate, lot_key="lot_2")
        factories.ProposalFactory.create(lot=lot1, contractor=package.contractor, vat_rate=D("10"))
        factories.ProposalFactory.create(lot=lot2, contractor=package.contractor, vat_rate=D("20"))
        db_session.flush()

        rates = crud_cx._rates(db_session, [estimate.id])
        assert rates[estimate.id] is None


# ---------------------------------------------------------------------------
#  A3.14 — ArticleRef.sort_order из WorkCategory.sort_order, None у Нераспределённого
# ---------------------------------------------------------------------------

class TestArticleSortOrder:
    def test_sort_order_from_work_category_and_none_for_unallocated(self, db_session, factories):
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        r2 = _round(factories, tender, 2)
        db_session.flush()
        package, _ = _participant(factories, tender, title="ООО Сортировка")
        est1, _l1, prop1 = _estimate(factories, round_=r1, package=package)
        _est2, _l2, prop2 = _estimate(factories, round_=r2, package=package)
        cat6 = _category_id(db_session, "6")
        expected_sort_order = _category_sort_order(db_session, "6")

        chapter_known = _chapter(factories, prop1, key="1", category_id=cat6)
        _row(factories, prop1, chapter_known, key="a")
        chapter_unallocated = _chapter(factories, prop1, key="2", category_id=None)
        _row(factories, prop1, chapter_unallocated, key="b")
        _chapter(factories, prop2, category_id=cat6)
        db_session.flush()

        raw = crud_cx._groups(db_session, [est1.id])[est1.id]
        by_code = {g.article.code: g for g in raw}
        assert by_code["6"].article.sort_order == expected_sort_order
        unallocated = next(g for g in raw if g.article.code is None)
        assert unallocated.article.sort_order is None
        assert unallocated.article.title == "Нераспределённое"


# ---------------------------------------------------------------------------
#  A3.10, A3.11 — сходимость с v_category_totals, ЧЕТЫРЕ входа
# ---------------------------------------------------------------------------

def _view_stage_total(db_session, estimate_id: int, tax_basis) -> Decimal | None:
    """Оракул DoD 4: сумма ДВУХ ветвей `v_category_totals` (без фильтра по виду
    каталожной записи — правило VIEW сохранено дословно), приведённая к оси
    листа `to_shown` (план, A3.10 — сравнение с сырой суммой VIEW верно только
    на валовой оси).

    Строки VIEW одной сметы группируются по СВОЕЙ ставочной группе
    (`estimate_id, proposal_id, vat_rate_base`), а `_rates()` признаёт базу
    известной только при единогласии предложений сметы — значит у ОДНОЙ сметы
    все её строки VIEW несут ОДНУ И ТУ ЖЕ ставку, и валовые суммы этих групп
    складываются здесь ДО перевода в ось, тем же порядком действий, что и
    `services.changes_export._cell_for` (сначала Python-свёртка валовых
    строк, потом ОДИН вызов `to_shown` на агрегат). Перевод КАЖДОЙ группы по
    отдельности и сложение результатов даёт то же число математически, но не
    побитово: `money.vat.gross_to_net` делит под контекстом ограниченной
    точности, и раздельные деления накапливают разный остаток, чем одно
    деление после сложения — этот тест сверяет с продакшен-порядком действий,
    а не с математически эквивалентным, но операционно другим."""
    rows = db_session.execute(
        sa.select(CATEGORY_TOTALS.c.amount, CATEGORY_TOTALS.c.vat_rate_base)
        .where(CATEGORY_TOTALS.c.estimate_id == estimate_id)
    ).all()
    rates = {rate for _amount, rate in rows if rate is not None}
    assert len(rates) <= 1, "у сметы с известной базой все строки VIEW обязаны нести одну ставку"
    rate = next(iter(rates), None)
    gross_total = sum((amount for amount, _rate in rows if amount is not None), Decimal("0"))
    shown = to_shown(gross_total, rate, tax_basis)
    assert shown is not None, "известная база сметы обязана давать известную ось"
    return shown


def _assert_stage_converges(db_session, built, estimate_id: int, idx: int) -> None:
    """Сравнение — на ПЕЧАТАЕМОЙ точности (`finance.money_round`, две цифры),
    тем же рубежом, на котором в этом проекте деньги вообще становятся
    сравнимыми (`AGENTS.md` §3, §10). На нетто-оси `to_shown`/`gross_to_net`
    делит, а частное большинства ставок не представимо конечной десятичной
    дробью; `build_sheet` переводит в ось КАЖДУЮ ячейку (по каталожной позиции)
    и лишь потом складывает их в подытог и в общий итог, тогда как
    `v_category_totals` агрегирует ГРУБЕЕ (по статье целиком). При делении под
    контекстом ограниченной точности (без ловушки `Inexact`, тот же приём, что
    `stage_summary._DIV_CONTEXT`) эти два порядка действий расходятся в
    последнем разряде точности контекста — глубже, чем печатает лист, а не в
    разряде, который читает человек. `money_round` — не отдельный допуск,
    изобретённый для этого теста, а тот же рубеж, которым весь проект уже
    сравнивает деньги."""
    expected = _view_stage_total(db_session, estimate_id, built.tax_basis)
    actual = built.grand_by_stage[idx].value
    assert actual is not None
    assert money_round(actual) == money_round(expected)


def _mixed_kind_fixture(db_session, factories, *, vat_rates, title="ООО Сходимость"):
    """Участник с `len(vat_rates)` этапами; на КАЖДОМ этапе — НАСТОЯЩАЯ работа
    (`catalog_position` с `kind='TO_REVIEW'` — ветвь `KIND_WORK`, не
    непривязанная строка), допработа, и (только на первом этапе) все четыре
    диагностические ветви (§2.2): HEADER, LOT_HEADER, TRASH и строка без
    каталожной привязки — с ненулевыми деньгами, чтобы сходимость была
    ЧУВСТВИТЕЛЬНА к их потере (план, A3.12).

    Ревью после гейта 3: прежняя редакция не задавала `catalog_position`
    строке «работа», и та классифицировалась как `KIND_UNMATCHED` — сходимость
    проверялась составом БЕЗ единой настоящей `KIND_WORK`-строки. `work_cat` —
    ОДИН каталожный объект на все этапы (та же позиция участвует в торге
    целиком, а не только на первом этапе)."""
    tender = factories.TenderFactory.create()
    rounds = [_round(factories, tender, n + 1) for n in range(len(vat_rates))]
    db_session.flush()
    package, _ = _participant(factories, tender, title=title)
    cat6 = _category_id(db_session, "6")
    work_cat = factories.CatalogPositionFactory.create(kind=CatalogKind.TO_REVIEW.value)

    estimates = []
    for idx, (round_, rate) in enumerate(zip(rounds, vat_rates, strict=False)):
        estimate, _lot, proposal = _estimate(factories, round_=round_, package=package, vat_rate=rate)
        chapter = _chapter(factories, proposal, category_id=cat6)
        _row(factories, proposal, chapter, key="work", catalog_position=work_cat, total=D("100.00"))
        _additional_work(db_session, proposal, category_id=cat6, amount=D("10.00"))
        if idx == 0:
            header_cat = factories.CatalogPositionFactory.create(kind=CatalogKind.HEADER.value)
            lot_header_cat = factories.CatalogPositionFactory.create(kind=CatalogKind.LOT_HEADER.value)
            trash_cat = factories.CatalogPositionFactory.create(kind=CatalogKind.TRASH.value)
            _row(factories, proposal, chapter, key="header", catalog_position=header_cat, total=D("50.00"))
            _row(factories, proposal, chapter, key="lot_header", catalog_position=lot_header_cat, total=D("30.00"))
            _row(factories, proposal, chapter, key="trash", catalog_position=trash_cat, total=D("20.00"))
            _row(factories, proposal, chapter, key="unmatched", catalog_position=None, total=D("15.00"))
        estimates.append(estimate)
    db_session.flush()
    return tender, estimates


class TestConvergenceWithView:
    def test_single_known_rate_is_gross_and_converges(self, db_session, factories):
        """Вход 1 из 4: одна известная ставка среди этапов -> валовая ось."""
        tender, estimates = _mixed_kind_fixture(db_session, factories, vat_rates=[D("20"), D("20")])
        sheets = crud_cx.load_book(db_session, tender.id)
        built = svc_cx.build_sheet(sheets[0])
        assert built.tax_basis.basis == TAX_GROSS
        for idx, estimate in enumerate(estimates):
            _assert_stage_converges(db_session, built, estimate.id, idx)

    def test_multiple_known_rates_is_net_and_converges(self, db_session, factories):
        """Вход 2 из 4: несколько разных известных ставок -> нетто целиком."""
        tender, estimates = _mixed_kind_fixture(db_session, factories, vat_rates=[D("20"), D("10")])
        sheets = crud_cx.load_book(db_session, tender.id)
        built = svc_cx.build_sheet(sheets[0])
        assert built.tax_basis.basis == TAX_NET
        for idx, estimate in enumerate(estimates):
            _assert_stage_converges(db_session, built, estimate.id, idx)

    def test_unknown_stage_is_unavailable_and_excluded_from_comparison(self, db_session, factories):
        """Вход 3 из 4: средний этап без известной базы — недоступен целиком,
        а не нулём; соседние известные этапы сходятся как обычно."""
        tender, estimates = _mixed_kind_fixture(db_session, factories, vat_rates=[D("20"), None, D("20")])
        sheets = crud_cx.load_book(db_session, tender.id)
        built = svc_cx.build_sheet(sheets[0])
        assert built.tax_basis.basis == TAX_GROSS   # среди известных — одна ставка (20)
        assert built.grand_by_stage[1].value is None
        assert built.grand_by_stage[1].reason == svc_cx.REASON_UNKNOWN_VAT_BASE
        for idx in (0, 2):
            _assert_stage_converges(db_session, built, estimates[idx].id, idx)

    def test_no_known_rate_disables_every_stage(self, db_session, factories):
        """Вход 4 из 4: ни одной известной ставки -> TAX_NONE, сравнивать нечего
        нигде — все суммы и подытоги недоступны на каждом этапе."""
        tender, _estimates = _mixed_kind_fixture(db_session, factories, vat_rates=[None, None])
        sheets = crud_cx.load_book(db_session, tender.id)
        built = svc_cx.build_sheet(sheets[0])
        assert built.tax_basis.basis == TAX_NONE
        assert all(cell.value is None for cell in built.grand_by_stage)


# ---------------------------------------------------------------------------
#  A3.12 — ловушка: сходимость обязана краснеть без диагностических ветвей
# ---------------------------------------------------------------------------

class TestConvergenceDiscriminatesDiagnosticKinds:
    """Вход несёт РЕАЛЬНЫЕ деньги за `HEADER`, `LOT_HEADER`, `TRASH` и без
    каталожной привязки на первом этапе (см. `_mixed_kind_fixture`). Сходимость
    здесь ПРОВЕРЯЕТ, что все четыре ветви остались в подытоге листа — если бы
    `_groups` фильтровал строки по `CatalogPosition.kind`, сумма листа
    разошлась бы с `v_category_totals` (которая такого фильтра не имеет) ровно
    на 50+30+20+15 = 115 на первом этапе. Снятие защиты (добавление фильтра
    `CatalogPosition.kind.in_(['POSITION', 'TO_REVIEW'])` в `_groups`) и красный
    прогон этого теста — в отчёте задачи, а не в самом тесте."""

    def test_all_four_diagnostic_branches_stay_in_the_grand_total(self, db_session, factories):
        tender, estimates = _mixed_kind_fixture(db_session, factories, vat_rates=[D("20"), D("20")])
        sheets = crud_cx.load_book(db_session, tender.id)
        built = svc_cx.build_sheet(sheets[0])
        _assert_stage_converges(db_session, built, estimates[0].id, 0)

        # Премиса: рядом с диагностическими ветвями есть НАСТОЯЩАЯ работа
        # (`KIND_WORK`) — иначе сходимость проверяется составом, которым не
        # обещает (ревью после гейта 3), и диагностические ветви несут деньги,
        # иначе проверка выше недискриминирующая (план, A3.12 — та же ловушка,
        # что держалась на стенде из-за каталога целиком в TO_REVIEW).
        kinds_present = {row.kind for row in built.rows}
        assert svc_cx.KIND_WORK in kinds_present
        assert svc_cx.KIND_NONWORK in kinds_present
        assert svc_cx.KIND_UNMATCHED in kinds_present
        nonwork_titles = {row.work_title for row in built.rows if row.kind == svc_cx.KIND_NONWORK}
        assert set(svc_cx.NON_WORK_TITLES.values()) <= nonwork_titles


# ---------------------------------------------------------------------------
#  A3.15, A3.16 — число запросов не зависит от числа участников/этапов
# ---------------------------------------------------------------------------

class TestQueryBudget:
    def _tender_with_participants(self, db_session, factories, n: int):
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        r2 = _round(factories, tender, 2)
        db_session.flush()
        cat6 = _category_id(db_session, "6")
        for i in range(n):
            package, _ = _participant(factories, tender, title=f"ООО Участник {i}")
            for r in (r1, r2):
                _estimate_, _lot_, proposal = _estimate(factories, round_=r, package=package)
                chapter = _chapter(factories, proposal, category_id=cat6)
                _row(factories, proposal, chapter, key="a")
        db_session.flush()
        return tender

    def test_query_count_independent_of_participant_count(self, db_session, factories):
        small = self._tender_with_participants(db_session, factories, 2)
        with count_queries(db_session) as two:
            crud_cx.load_book(db_session, small.id)
        large = self._tender_with_participants(db_session, factories, 6)
        with count_queries(db_session) as six:
            crud_cx.load_book(db_session, large.id)
        assert two["n"] == six["n"]

    def test_query_count_unchanged_when_a_participant_is_added(self, db_session, factories):
        tender = factories.TenderFactory.create()
        r1 = _round(factories, tender, 1)
        r2 = _round(factories, tender, 2)
        db_session.flush()
        cat6 = _category_id(db_session, "6")

        def add_participant(title):
            package, _ = _participant(factories, tender, title=title)
            for r in (r1, r2):
                _est, _lot, proposal = _estimate(factories, round_=r, package=package)
                chapter = _chapter(factories, proposal, category_id=cat6)
                _row(factories, proposal, chapter, key="a")
            db_session.flush()

        add_participant("ООО Первый")
        with count_queries(db_session) as before:
            crud_cx.load_book(db_session, tender.id)

        add_participant("ООО Второй")
        with count_queries(db_session) as after:
            crud_cx.load_book(db_session, tender.id)

        assert before["n"] == after["n"]
