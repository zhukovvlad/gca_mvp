"""Экран этапа «Против нашей цены» — `GET /api/v1/tenders/{id}/rounds/{id}/our-price`
(спека 4а §2.8-§2.12, §2.17, §2.18).

Сцены строятся фабриками и помощниками каталога; названия и числа выдуманы.
Эталоны — числа, посчитанные руками. Ставки истории — 0 %, поэтому нетто равно
валовой цене; ставки КП — 0 % или 20 %. Каждое правило имеет обе стороны: вход,
где оно срабатывает, и соседний вход, нарушающий ровно одно условие.
"""

from __future__ import annotations

import datetime as dt
import json
from contextlib import contextmanager
from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud.stage_summary import build_stage_summary
from models import EstimateAdditionalWork, SemanticJob, WorkCategory
from tests import comparison_fixtures as fx
from tests.integration.test_our_price_load import World, _categories
from tests.integration.test_our_price_signal import _job, _pause

pytestmark = pytest.mark.integration

D = Decimal

SERIES_NAME = "Росстат, ИПЦ, декабрь к декабрю"
FULL_YEARS = {2024: "1.075", 2025: "1.083", 2026: "1.060"}
#: Коэффициент периода 2025-02 → 2026-08 при ряде `FULL_YEARS` (замер спеки приведения).
FACTOR_FEB_2025 = D("1.111034702376096271381243623987202")
TARGET_MISSING_TEXT = "У этапа нет даты проведения, и у КП нет даты подготовки — приведение по ИПЦ не к чему привязать."


def _no_float(text: str):
    raise AssertionError(f"в ответе дробный литерал JSON: {text}")


def _json(response) -> dict:
    """Разбор без `float`: любой дробный литерал роняет тест."""
    return json.loads(response.text, parse_float=_no_float)


def _url(tender, rnd) -> str:
    return f"/api/v1/tenders/{tender.id}/rounds/{rnd.id}/our-price"


def _get(client, tender, rnd, **params):
    return client.get(_url(tender, rnd), params=params)


def _ok(client, tender, rnd, **params) -> dict:
    response = _get(client, tender, rnd, **params)
    assert response.status_code == 200, response.text
    return _json(response)


def _dec(value: str | None) -> Decimal | None:
    return None if value is None else D(value)


@contextmanager
def count_statements(db):
    seen: list[str] = []

    def _tick(_conn, _cursor, statement, *_args, **_kwargs):
        if not statement.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            seen.append(statement)

    engine = db.get_bind()
    sa.event.listen(engine, "before_cursor_execute", _tick)
    try:
        yield seen
    finally:
        sa.event.remove(engine, "before_cursor_execute", _tick)


class Stage:
    """Тендер на объекте с площадью `area`, классом, одним семейством с вариантом
    и своими статьями: корни `R` (первый по порядку) и `R2`."""

    def __init__(self, world: World, db, factories, *, area=D("1000")):
        self.world = world
        self.db = db
        self.f = factories
        obj = factories.ObjectFactory.create(
            area_aboveground_sp=area, area_underground_sp=D("0") if area is not None else None
        )
        self.rate_class = factories.RateClassFactory.create(title="Класс Т")
        self.tender = factories.TenderFactory.create(object=obj, rate_class=self.rate_class)
        self.cats = _categories(db)
        self.family, self.schema = world.family("Семья Т")
        param = world.param(self.schema, 1, "Толщина")
        self.variant = world.variant(self.family, self.schema, {1: world.value(param, "50 мм")})
        self._chapters: dict[tuple[int, str | None], object] = {}
        self._stage_no = 0

    # -- этап и участники ---------------------------------------------------

    def round(self, *, held_on=dt.date(2026, 8, 10)):
        self._stage_no += 1
        return self.f.TenderRoundFactory.create(tender=self.tender, stage_no=self._stage_no, held_on=held_on)

    def participant(self, rnd, title, *, vat=D("20"), estimate=True, prepared=dt.date(2026, 8, 1), package=None):
        """Участник этапа; `package` — тот же участник на другом этапе."""
        if package is None:
            contractor = self.f.ContractorFactory.create(title=title)
            package = self.f.OfferPackageFactory.create(tender=self.tender, contractor=contractor)
        contractor = package.contractor
        offer = self.f.OfferFactory.create(round=rnd, package=package, tender_id=self.tender.id)
        if not estimate:
            return Part(offer, None, None, contractor)
        est = self.f.EstimateFactory.create(contract=None, offer=offer, data_prepared_on_date=prepared)
        lot = self.f.LotFactory.create(estimate=est)
        proposal = self.f.ProposalFactory.create(lot=lot, contractor=contractor, vat_rate=vat)
        self.db.flush()
        return Part(offer, est, proposal, contractor)

    # -- строки -------------------------------------------------------------

    def chapter(self, proposal, cat: str | None):
        key = (proposal.id, cat)
        if key not in self._chapters:
            self._chapters[key] = self.world.chapter(proposal, None if cat is None else self.cats[cat].id)
        return self._chapters[key]

    def line(self, proposal, cat: str | None, unit_cost, total=None, *, qty=D("10"), variant=True):
        """Строка в главе статьи `cat`; `total` по умолчанию `unit_cost × qty`."""
        item = self.world.line(
            proposal,
            chapter=self.chapter(proposal, cat),
            unit_cost=unit_cost,
            total=unit_cost * qty if total is None else total,
            suggested=qty,
            quantity=qty,
            family=self.family if variant else None,
            variant=self.variant if variant else None,
        )
        # маршрутизация и назначение семьи ставят задания очереди; сигнал каталога
        # строки здесь задают только явные `_job` теста
        self.db.execute(sa.delete(SemanticJob).where(SemanticJob.context_id == item.context_id))
        return item

    def kp(self, part, unit_cost, cat="R", **kwargs):
        return self.line(part.proposal, cat, unit_cost, **kwargs)

    def history(self, unit_cost, *, cat="R", vat=D("0"), area=D("1000"), rate_class="own", contractor=None, **kwargs):
        """Договор истории с одной строкой варианта; `rate_class="own"` — класс тендера."""
        contract = self.world.contract(
            area=area,
            rate_class=self.rate_class if rate_class == "own" else rate_class,
            contractor=contractor,
        )
        estimate, proposal = self.world.estimate(contract, vat_rate=vat)
        self.line(proposal, cat, unit_cost, **kwargs)
        self.db.flush()
        return contract, estimate, proposal


class Part:
    def __init__(self, offer, estimate, proposal, contractor):
        self.offer = offer
        self.estimate = estimate
        self.proposal = proposal
        self.contractor = contractor


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


def _participant(body: dict, part: Part) -> dict:
    (found,) = (p for p in body["participants"] if p["offer_id"] == part.offer.id)
    return found


def _article(body: dict, stage_: Stage, cat: str) -> dict:
    (found,) = (a for a in body["articles"] if a["work_category_id"] == stage_.cats[cat].id)
    return found


def _cell(article: dict, part: Part) -> dict:
    (found,) = (c for c in article["cells"] if c["offer_id"] == part.offer.id)
    return found


def _two_history_and_kp(stage_):
    """История класса: ₽/м² 1.0 и 1.4; КП участника (НДС 20 %): нетто 120 за единицу,
    1200 по статье, 1.2 ₽/м²."""
    stage_.history(D("100"))
    stage_.history(D("140"))
    rnd = stage_.round()
    part = stage_.participant(rnd, "Подрядчик А")
    stage_.kp(part, D("144"))
    return rnd, part


# ---------------------------------------------------------------------------
#  Маршрут: отказы, доступ, чтение без записи, без float
# ---------------------------------------------------------------------------


class TestRoute:
    def test_unknown_tender_is_404(self, member_client, stage):
        rnd = stage.round()

        response = member_client.get(f"/api/v1/tenders/987654321/rounds/{rnd.id}/our-price")

        assert response.status_code == 404
        # отказ называет тендер, а не этап: этап тут существует, но чужой
        assert response.json()["detail"] == "Тендер 987654321 не найден."

    def test_round_of_another_tender_is_404(self, member_client, stage, factories):
        foreign = factories.TenderRoundFactory.create()

        assert _get(member_client, stage.tender, foreign).status_code == 404

    def test_foreign_round_with_a_series_is_404_not_422(self, member_client, stage, factories, db_session):
        """У чужого этапа нет ни даты, ни КП: без проверки принадлежности до плана
        ИПЦ ответ был бы 422 `inflation_target_missing`."""
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        foreign = factories.TenderRoundFactory.create(held_on=None)

        response = _get(member_client, stage.tender, foreign, inflation_series_id=series_id)

        assert response.status_code == 404
        assert isinstance(response.json()["detail"], str)

    def test_unknown_tender_with_a_series_is_404(self, member_client, stage, factories, db_session):
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        rnd = factories.TenderRoundFactory.create(held_on=None)
        other_tender = factories.TenderFactory.create()

        response = _get(member_client, other_tender, rnd, inflation_series_id=series_id)

        assert response.status_code == 404

    def test_member_reads(self, member_client, stage):
        """Только `member_client`: фикстуры ролей делят переопределение
        `get_current_user` одного приложения, и с `admin_client` в том же тесте
        запрос «от member» уходит от admin — проверка роли была бы ложно-зелёной."""
        rnd, _ = _two_history_and_kp(stage)

        assert _get(member_client, stage.tender, rnd).status_code == 200

    def test_the_endpoint_writes_nothing(self, member_client, stage, db_session):
        rnd, _ = _two_history_and_kp(stage)
        with count_statements(db_session) as seen:
            assert _get(member_client, stage.tender, rnd).status_code == 200
            assert _get(member_client, stage.tender, rnd, inflation_series_id=987654321).status_code == 404

        writes = [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        assert seen and writes == []

    def test_response_has_no_float_literal(self, member_client, stage):
        rnd, _ = _two_history_and_kp(stage)

        body = _ok(member_client, stage.tender, rnd)  # `_json` падает на любом дробном литерале

        assert body["participants"] and body["articles"]

    def test_header_blocks(self, member_client, stage):
        rnd = stage.round(held_on=dt.date(2026, 8, 10))

        body = _ok(member_client, stage.tender, rnd)

        assert body["tender"] == {
            "id": stage.tender.id,
            "title": stage.tender.title,
            "object": {"id": stage.tender.object_id, "title": stage.tender.object.title, "area_total_sp": "1000"},
            "rate_class": {"id": stage.rate_class.id, "title": "Класс Т"},
        }
        assert body["round"] == {"id": rnd.id, "stage_no": 1, "label": None, "held_on": "2026-08-10"}

    def test_round_without_held_on_has_null_date(self, member_client, stage):
        rnd = stage.round(held_on=None)

        assert _ok(member_client, stage.tender, rnd)["round"]["held_on"] is None


# ---------------------------------------------------------------------------
#  Участники и их состояния
# ---------------------------------------------------------------------------


class TestParticipants:
    def test_round_without_any_estimate_is_200_with_zero_progress(self, member_client, stage):
        rnd = stage.round()
        stage.participant(rnd, "Подрядчик Б", estimate=False)
        stage.participant(rnd, "Подрядчик А", estimate=False)

        body = _ok(member_client, stage.tender, rnd)

        assert body["progress"] == {"lines_total": 0, "in_progress": 0, "waiting_admin": 0}
        assert [p["state"] for p in body["participants"]] == ["no_estimate", "no_estimate"]
        assert body["articles"] == []

    def test_round_without_participants_is_200(self, member_client, stage):
        body = _ok(member_client, stage.tender, stage.round())

        assert body["participants"] == []
        assert body["progress"] == {"lines_total": 0, "in_progress": 0, "waiting_admin": 0}

    def test_four_participants_in_contractor_title_order_one_without_estimate(self, member_client, stage):
        stage.history(D("100"))
        rnd = stage.round()
        parts = {
            title: stage.participant(rnd, title, estimate=title != "Подрядчик В")
            for title in ("Подрядчик Г", "Подрядчик Б", "Подрядчик В", "Подрядчик А")
        }
        for title in ("Подрядчик Г", "Подрядчик Б", "Подрядчик А"):
            stage.kp(parts[title], D("120"), total=D("1200"))

        body = _ok(member_client, stage.tender, rnd)

        assert [p["contractor"]["title"] for p in body["participants"]] == [
            "Подрядчик А",
            "Подрядчик Б",
            "Подрядчик В",
            "Подрядчик Г",
        ]
        assert [p["state"] for p in body["participants"]] == ["ok", "ok", "no_estimate", "ok"]
        empty = _participant(body, parts["Подрядчик В"])
        assert empty["estimate_id"] is None
        assert empty["contractor"]["id"] == parts["Подрядчик В"].contractor.id
        for key in ("vat_rate", "per_sqm", "per_sqm_reason", "class_median_pct", "totals"):
            assert empty[key] is None, key
        assert _participant(body, parts["Подрядчик А"])["estimate_id"] == parts["Подрядчик А"].estimate.id

    def test_no_estimate_participant_has_null_cells_and_counts_its_contracts(self, member_client, stage):
        empty_part_contractor = stage.f.ContractorFactory.create(title="Подрядчик Д")
        stage.history(D("100"), contractor=empty_part_contractor)
        rnd = stage.round()
        package = stage.f.OfferPackageFactory.create(tender=stage.tender, contractor=empty_part_contractor)
        offer = stage.f.OfferFactory.create(round=rnd, package=package, tender_id=stage.tender.id)
        full = stage.participant(rnd, "Подрядчик А")
        stage.kp(full, D("120"))
        empty = Part(offer, None, None, empty_part_contractor)

        body = _ok(member_client, stage.tender, rnd)

        assert _participant(body, empty)["ours_with_contractor"] == 1
        assert _participant(body, full)["ours_with_contractor"] == 0
        cell = _cell(_article(body, stage, "R"), empty)
        assert cell == {
            "offer_id": offer.id,
            "per_sqm": None,
            "whole": None,
            "unit_mismatch_lines": 0,
            "ours_class_per_sqm": None,
        }

    def test_ours_with_contractor_counts_every_contract_of_the_contractor(self, member_client, stage):
        part_contractor = stage.f.ContractorFactory.create(title="Подрядчик А")
        stage.history(D("100"), contractor=part_contractor)
        stage.history(D("110"), contractor=part_contractor, rate_class=stage.f.RateClassFactory.create())
        stage.history(D("120"))
        rnd = stage.round()
        package = stage.f.OfferPackageFactory.create(tender=stage.tender, contractor=part_contractor)
        offer = stage.f.OfferFactory.create(round=rnd, package=package, tender_id=stage.tender.id)

        body = _ok(member_client, stage.tender, rnd)

        (only,) = body["participants"]
        assert only["offer_id"] == offer.id
        assert only["ours_with_contractor"] == 2

    def test_unknown_vat_base_blanks_the_whole_participant(self, member_client, stage):
        stage.history(D("100"))
        rnd = stage.round()
        unknown = stage.participant(rnd, "Подрядчик Б", vat=None)
        known = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(unknown, D("120"))
        stage.kp(known, D("120"))

        body = _ok(member_client, stage.tender, rnd)

        blank = _participant(body, unknown)
        assert blank["state"] == "unknown_vat_base"
        assert blank["per_sqm_reason"] == "unknown_vat_base"
        for key in ("vat_rate", "per_sqm", "totals", "class_median_pct"):
            assert blank[key] is None, key
        for article in (_article(body, stage, "R"), body["total"]):
            cell = _cell(article, unknown)
            assert (cell["per_sqm"], cell["whole"], cell["ours_class_per_sqm"]) == (None, None, None)
        ok = _participant(body, known)
        assert ok["state"] == "ok"
        assert ok["per_sqm_reason"] is None
        assert ok["totals"] is not None

    def test_unknown_vat_base_participant_with_a_line_in_progress_has_no_zero_totals(
        self, member_client, stage, db_session
    ):
        """В ядре корзина `in_progress` такого участника сумму даёт `0`; гасит итоги эндпоинт."""
        stage.history(D("100"))
        rnd = stage.round()
        unknown = stage.participant(rnd, "Подрядчик Б", vat=None)
        item = stage.kp(unknown, D("120"), variant=False)
        _job(db_session, "family_suggestion", "pending", context_id=item.context_id)

        body = _ok(member_client, stage.tender, rnd)

        blank = _participant(body, unknown)
        assert blank["state"] == "unknown_vat_base"
        assert blank["totals"] is None
        assert body["progress"]["in_progress"] == 1

    def test_object_without_area_has_null_per_sqm_with_reason(self, member_client, world, db_session, factories):
        stage_ = Stage(world, db_session, factories, area=None)
        stage_.history(D("100"))
        rnd = stage_.round()
        part = stage_.participant(rnd, "Подрядчик А")
        stage_.kp(part, D("144"))

        body = _ok(member_client, stage_.tender, rnd)

        row = _participant(body, part)
        assert (row["per_sqm"], row["per_sqm_reason"]) == (None, "no_area")
        assert row["state"] == "ok"
        cell = _cell(_article(body, stage_, "R"), part)
        assert (cell["per_sqm"], cell["whole"]) == (None, None)
        assert body["tender"]["object"]["area_total_sp"] is None

    def test_unknown_base_wins_over_no_area(self, member_client, world, db_session, factories):
        stage_ = Stage(world, db_session, factories, area=None)
        rnd = stage_.round()
        part = stage_.participant(rnd, "Подрядчик А", vat=None)
        stage_.kp(part, D("144"))

        row = _participant(_ok(member_client, stage_.tender, rnd), part)

        assert row["per_sqm_reason"] == "unknown_vat_base"

    def test_area_known_gives_no_reason(self, member_client, stage):
        rnd, part = _two_history_and_kp(stage)

        assert _participant(_ok(member_client, stage.tender, rnd), part)["per_sqm_reason"] is None

    def test_state_ok_totals_follow_the_core(self, member_client, stage):
        rnd, part = _two_history_and_kp(stage)

        totals = _participant(_ok(member_client, stage.tender, rnd), part)["totals"]

        # нетто КП 120, наша расценка 100, вес 10: (120 - 100) * 10 = 200; в ставке КП 20 % — 240
        assert totals["bargain"] == {"net": "200.00", "shown": "240.00"}
        assert totals["below"] == {"net": "0.00", "shown": "0.00"}
        assert (totals["below_lines"], totals["unit_mismatch_lines"]) == (0, 0)
        assert totals["unit_mismatch_amount"] == {"net": "0.00", "shown": "0.00"}
        assert totals["not_comparable"] == {"total": {"net": "0.00", "shown": "0.00"}, "by_state": {}}

    def test_not_comparable_amounts_are_split_by_state(self, member_client, stage):
        rnd, part = _two_history_and_kp(stage)
        stage.kp(part, D("100"), total=D("600"), variant=False)  # без варианта, нетто 500

        totals = _participant(_ok(member_client, stage.tender, rnd), part)["totals"]

        assert totals["not_comparable"]["total"] == {"net": "500.00", "shown": "600.00"}
        assert totals["not_comparable"]["by_state"] == {
            "no_variant": {"lines": 1, "amount": {"net": "500.00", "shown": "600.00"}}
        }


# ---------------------------------------------------------------------------
#  ₽/м², статьи, строка «Итого»
# ---------------------------------------------------------------------------


class TestPerSqmAndArticles:
    def test_per_sqm_and_cells_come_in_the_participant_rate(self, member_client, stage):
        rnd, part = _two_history_and_kp(stage)

        body = _ok(member_client, stage.tender, rnd)

        row = _participant(body, part)
        assert row["vat_rate"] == "20"
        assert {k: _dec(v) for k, v in row["per_sqm"].items()} == {"net": D("1.2"), "shown": D("1.44")}
        cell = _cell(_article(body, stage, "R"), part)
        assert {k: _dec(v) for k, v in cell["per_sqm"].items()} == {"net": D("1.2"), "shown": D("1.44")}
        # статья целиком: (1.2 - 1.0) * 1000 = 200, в ставке КП 240
        assert cell["whole"] == {"net": "200.00", "shown": "240.00"}

    def test_additional_work_of_the_article_is_included(self, member_client, stage, db_session):
        rnd, part = _two_history_and_kp(stage)
        db_session.add(
            EstimateAdditionalWork(
                proposal_id=part.proposal.id,
                ordinal=1,
                title="Допработа",
                total_amount=D("24.00"),
                chapter_ref_raw="1",
                raw_line="исходная строка сведений",
                work_category_id=stage.cats["R"].id,
            )
        )
        db_session.flush()

        body = _ok(member_client, stage.tender, rnd)

        # 1200 нетто позиций + 24 / 1.2 = 20 нетто допработы = 1220 -> 1.22 ₽/м²
        assert _dec(_participant(body, part)["per_sqm"]["net"]) == D("1.22")
        assert _dec(_cell(_article(body, stage, "R"), part)["per_sqm"]["net"]) == D("1.22")

    def test_article_sum_matches_the_stage_summary_in_net(self, member_client, stage, db_session):
        rnd, part = _two_history_and_kp(stage)
        stage.kp(part, D("96"), cat="R2")  # нетто 800, статья R2
        # свод по этапам требует два предложения одного участника
        later = stage.participant(stage.round(), "Подрядчик А", package=part.offer.package)
        stage.kp(later, D("96"), cat="R2")

        body = _ok(member_client, stage.tender, rnd)
        summary = build_stage_summary(db_session, stage.tender.id, [part.offer.id, later.offer.id])

        assert summary["display"]["tax_basis"] == "gross"
        area = D("1000")
        by_category = {row["work_category_id"]: row for row in summary["rows"]}
        for cat in ("R", "R2"):
            gross = D(by_category[stage.cats[cat].id]["cells"][0]["amount"])
            ours = _dec(_cell(_article(body, stage, cat), part)["per_sqm"]["net"]) * area
            assert ours == gross * 100 / 120, cat

    def test_articles_are_ordered_by_root_with_unallocated_last(self, member_client, stage):
        rnd, part = _two_history_and_kp(stage)
        stage.kp(part, D("100"), cat="R2")
        stage.kp(part, D("100"), cat=None)

        body = _ok(member_client, stage.tender, rnd)

        assert [a["work_category_id"] for a in body["articles"]] == [
            stage.cats["R"].id,
            stage.cats["R2"].id,
            None,
        ]
        first, _second, last = body["articles"]
        assert (first["code"], first["title"]) == ("8801", "Статья R")
        assert first["sort_order"] == stage.cats["R"].sort_order
        assert (last["code"], last["title"], last["sort_order"]) == (None, "Нераспределённое", None)

    def test_a_root_missing_in_a_participant_and_a_zero_root_are_both_null(self, member_client, stage):
        rnd, part = _two_history_and_kp(stage)
        other = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        stage.kp(other, D("120"))
        stage.kp(other, D("50"), cat="R2", total=D("0"))
        stage.history(D("100"), cat="R2")  # у класса статья есть: «whole» не None только из-за КП

        body = _ok(member_client, stage.tender, rnd)

        article = _article(body, stage, "R2")
        for participant in (part, other):  # строк нет и сумма статьи 0 — одно и то же: статьи нет
            assert _cell(article, participant)["per_sqm"] is None
            assert _cell(article, participant)["whole"] is None

    def test_a_negative_article_sum_of_a_participant_stays_a_value(self, member_client, stage):
        rnd, _part = _two_history_and_kp(stage)
        other = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        stage.kp(other, D("120"))
        stage.kp(other, D("50"), cat="R2", total=D("-100"))
        stage.history(D("100"), cat="R2")  # 1.0

        body = _ok(member_client, stage.tender, rnd)

        cell = _cell(_article(body, stage, "R2"), other)
        assert cell["per_sqm"] is not None  # зачёт — значение, а не «статьи нет»
        assert _dec(cell["per_sqm"]["net"]) == D("-0.1")
        # (-0.1 - 1.0) * 1000
        assert cell["whole"]["net"] == "-1100.00"

    def test_a_zero_article_sum_of_a_contract_is_not_in_the_class_fork(self, member_client, stage):
        stage.history(D("100"), total=D("0"))  # статья R: 0 — статьи нет
        stage.history(D("140"))  # 1.4
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("120"))

        body = _ok(member_client, stage.tender, rnd)

        article = _article(body, stage, "R")
        for fork in (article["ours_class_per_sqm"], _cell(article, part)["ours_class_per_sqm"]):
            assert (_dec(fork["min"]["net"]), _dec(fork["max"]["net"])) == (D("1.4"), D("1.4"))
        # (1.2 - 1.4) * 1000
        assert _cell(article, part)["whole"]["net"] == "-200.00"
        # «Итого» — сумма сметы, правило нуля к нему не относится
        total_fork = body["total"]["ours_class_per_sqm"]
        assert (_dec(total_fork["min"]["net"]), _dec(total_fork["max"]["net"])) == (D("0"), D("1.4"))

    @staticmethod
    def _extra_lot(stage, estimate, contractor, vat, total):
        """Ещё один лот сметы с одной строкой статьи R на `total` с НДС."""
        lot = stage.f.LotFactory.create(estimate=estimate)
        proposal = stage.f.ProposalFactory.create(lot=lot, contractor=contractor, vat_rate=vat)
        stage.line(proposal, "R", total / D("10"), total=total)
        stage.db.flush()

    def test_an_article_sum_that_cancels_to_a_cent_remainder_is_a_zero_for_a_participant(self, member_client, stage):
        stage.history(D("100"), cat="R2")
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("12"))
        stage.kp(part, D("120"), cat="R2")
        # 60, 40 и −100 с НДС 12 % гасятся в брутто, а в нетто по строкам дают остаток ~1E-98
        for total in (D("60"), D("40"), D("-100")):
            self._extra_lot(stage, part.estimate, part.contractor, D("12"), total)
        stage.history(D("100"))  # у класса статья R есть: «whole» не None только из-за КП

        body = _ok(member_client, stage.tender, rnd)

        cell = _cell(_article(body, stage, "R"), part)
        assert (cell["per_sqm"], cell["whole"]) == (None, None)

    def test_an_article_sum_that_cancels_to_a_cent_remainder_is_not_in_the_class_fork(self, member_client, stage):
        _contract, estimate, proposal = stage.history(D("6"), total=D("60"), vat=D("12"))
        for total in (D("40"), D("-100")):
            self._extra_lot(stage, estimate, proposal.contractor, D("12"), total)
        stage.history(D("140"))  # 1.4
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("120"))

        body = _ok(member_client, stage.tender, rnd)

        fork = _article(body, stage, "R")["ours_class_per_sqm"]
        assert (_dec(fork["min"]["net"]), _dec(fork["max"]["net"])) == (D("1.4"), D("1.4"))

    def test_only_a_zero_article_sum_in_the_class_gives_null_fork(self, member_client, stage):
        stage.history(D("100"), total=D("0"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("120"))

        body = _ok(member_client, stage.tender, rnd)

        article = _article(body, stage, "R")
        assert article["ours_class_per_sqm"] is None
        assert _cell(article, part)["whole"] is None

    def test_unit_mismatch_lines_are_counted_per_article_and_in_total(self, member_client, stage):
        rnd, part = _two_history_and_kp(stage)
        stage.kp(part, D("840"), cat="R2")  # нетто 700 против медианы 120: отношение выше 5

        body = _ok(member_client, stage.tender, rnd)

        assert _cell(_article(body, stage, "R2"), part)["unit_mismatch_lines"] == 1
        assert _cell(_article(body, stage, "R"), part)["unit_mismatch_lines"] == 0
        assert _cell(body["total"], part)["unit_mismatch_lines"] == 1
        assert _participant(body, part)["totals"]["unit_mismatch_lines"] == 1

    def test_class_fork_takes_only_class_contracts_with_a_known_base(self, member_client, stage):
        stage.history(D("100"))  # 1.0
        stage.history(D("140"))  # 1.4
        stage.history(D("10"), rate_class=stage.f.RateClassFactory.create())  # чужой класс: 0.1
        stage.history(D("5000"), vat=None)  # база неизвестна, корень None: 5.0
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("120"))

        body = _ok(member_client, stage.tender, rnd)

        fork = _article(body, stage, "R")["ours_class_per_sqm"]
        assert (_dec(fork["min"]["net"]), _dec(fork["max"]["net"])) == (D("1.0"), D("1.4"))
        cell_fork = _cell(_article(body, stage, "R"), part)["ours_class_per_sqm"]
        assert (_dec(cell_fork["min"]["net"]), _dec(cell_fork["max"]["net"])) == (D("1.0"), D("1.4"))

    def test_negative_contract_sums_are_legal_in_the_fork(self, member_client, stage):
        stage.history(D("100"), total=D("0"))  # статья 0 — статьи нет, в вилку не входит
        stage.history(D("100"), total=D("-100"))  # -0.1
        stage.history(D("140"))  # 1.4
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("120"))

        body = _ok(member_client, stage.tender, rnd)

        fork = _article(body, stage, "R")["ours_class_per_sqm"]
        assert (_dec(fork["min"]["net"]), _dec(fork["max"]["net"])) == (D("-0.1"), D("1.4"))
        # (1.2 - (-0.1)) * 1000 = 1300
        assert _cell(_article(body, stage, "R"), part)["whole"]["net"] == "1300.00"

    def test_no_class_contract_with_the_article_gives_null_fork_and_whole(self, member_client, stage):
        rnd, part = _two_history_and_kp(stage)
        stage.kp(part, D("100"), cat="R2")

        body = _ok(member_client, stage.tender, rnd)

        article = _article(body, stage, "R2")
        assert article["ours_class_per_sqm"] is None
        cell = _cell(article, part)
        assert cell["ours_class_per_sqm"] is None
        assert cell["whole"] is None
        assert cell["per_sqm"] is not None

    def test_whole_is_signed_both_ways(self, member_client, stage):
        stage.history(D("100"))
        rnd = stage.round()
        above = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        below = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        stage.kp(above, D("120"))  # 1.2
        stage.kp(below, D("80"))  # 0.8

        body = _ok(member_client, stage.tender, rnd)

        article = _article(body, stage, "R")
        assert _cell(article, above)["whole"]["net"] == "200.00"
        assert _cell(article, below)["whole"]["net"] == "-200.00"

    def test_total_row_has_its_own_fork_and_drops_contracts_with_an_unknown_root(
        self, member_client, stage, db_session
    ):
        stage.history(D("100"))  # итог 1.0
        stage.history(D("140"))  # итог 1.4
        partial, estimate, _proposal = stage.history(D("30"))  # корень R известен: 0.3
        lot = stage.f.LotFactory.create(estimate=estimate)
        unknown_proposal = stage.f.ProposalFactory.create(lot=lot, contractor=partial.contractor, vat_rate=None)
        stage.line(unknown_proposal, "R2", D("10"))  # корень R2 с неизвестной базой
        db_session.flush()
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("120"))

        body = _ok(member_client, stage.tender, rnd)

        total_fork = body["total"]["ours_class_per_sqm"]
        assert (_dec(total_fork["min"]["net"]), _dec(total_fork["max"]["net"])) == (D("1.0"), D("1.4"))
        article_fork = _article(body, stage, "R")["ours_class_per_sqm"]
        assert _dec(article_fork["min"]["net"]) == D("0.3")
        assert body["total"]["work_category_id"] is None
        assert {_dec(v) for v in _cell(body["total"], part)["per_sqm"].values()} == {D("1.2")}
        assert _cell(body["total"], part)["whole"]["net"] == "200.00"

    def test_class_median_pct_is_the_percent_to_the_class_median(self, member_client, stage):
        stage.history(D("100"))
        stage.history(D("140"))  # медиана ₽/м² класса: 1.2
        rnd = stage.round()
        low = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        high = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        stage.kp(low, D("120"))  # 1.2
        stage.kp(high, D("150"))  # 1.5

        body = _ok(member_client, stage.tender, rnd)

        assert _participant(body, low)["class_median_pct"] == "0.0"
        assert _participant(body, high)["class_median_pct"] == "25.0"

    def test_class_median_pct_ignores_contracts_of_other_classes(self, member_client, stage):
        stage.history(D("100"))
        stage.history(D("140"))
        stage.history(D("500"), rate_class=stage.f.RateClassFactory.create())  # чужой класс: 5.0
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("150"))  # 1.5 против медианы класса 1.2

        assert _participant(_ok(member_client, stage.tender, rnd), part)["class_median_pct"] == "25.0"

    def test_class_median_pct_is_null_when_the_median_is_zero(self, member_client, stage):
        stage.history(D("100"), total=D("0"))
        stage.history(D("100"), total=D("0"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("120"))

        response = _get(member_client, stage.tender, rnd)

        assert response.status_code == 200
        assert _participant(_json(response), part)["class_median_pct"] is None

    def test_class_median_pct_is_null_without_class_contracts(self, member_client, stage):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("120"))

        assert _participant(_ok(member_client, stage.tender, rnd), part)["class_median_pct"] is None

    def test_history_block_lists_every_contract_and_prices_those_with_area(self, member_client, stage):
        own, _, _ = stage.history(D("100"))
        foreign_class = stage.f.RateClassFactory.create(title="Класс Ч")
        foreign, _, _ = stage.history(D("200"), rate_class=foreign_class)
        no_area, _, _ = stage.history(D("300"), area=None)
        rnd = stage.round()
        stage.participant(rnd, "Подрядчик А", vat=D("0"))

        history = _ok(member_client, stage.tender, rnd)["history"]

        by_id = {c["id"]: c for c in history["contracts"]}
        assert set(by_id) == {own.id, foreign.id, no_area.id}
        assert by_id[foreign.id]["rate_class"] == {"id": foreign_class.id, "title": "Класс Ч"}
        assert by_id[no_area.id]["area"] is None
        assert by_id[own.id]["area"] == "1000"
        assert by_id[own.id]["contractor_id"] == own.contractor_id
        assert by_id[own.id]["year"] == 2025
        assert by_id[own.id]["number"] == own.contract_number
        per_sqm = {row["contract_id"]: _dec(row["net"]) for row in history["per_sqm"]}
        assert per_sqm == {own.id: D("1.0"), foreign.id: D("2.0")}

    def test_a_history_contract_without_view_rows_stays_out_of_the_per_sqm(self, member_client, stage):
        """Смета договора без единой строки VIEW — не ноль: договор остаётся в списке
        истории, но не входит ни в `history.per_sqm`, ни в вилку «Итого», ни в медиану
        класса (иначе пустая смета тянула бы минимум и медиану к нулю). У КП пустая
        смета, наоборот, — ноль (см. тест пустого корня)."""
        stage.history(D("100"))  # 1.0
        stage.history(D("140"))  # 1.4
        empty = stage.world.contract(rate_class=stage.rate_class)
        stage.world.estimate(empty, vat_rate=D("0"))
        stage.db.flush()
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("150"))  # 1.5

        body = _ok(member_client, stage.tender, rnd)

        assert empty.id in {c["id"] for c in body["history"]["contracts"]}
        assert empty.id not in {row["contract_id"] for row in body["history"]["per_sqm"]}
        total_fork = body["total"]["ours_class_per_sqm"]
        assert (_dec(total_fork["min"]["net"]), _dec(total_fork["max"]["net"])) == (D("1.0"), D("1.4"))
        # медиана класса 1.2, а не 1.0 с пустым договором
        assert _participant(body, part)["class_median_pct"] == "25.0"

    def test_a_history_contract_whose_view_amounts_are_all_empty_stays_out_too(self, member_client, stage):
        """Строки VIEW есть, но все суммы пусты: сумм «нет» так же, как при отсутствии
        строк, и договор не входит в `history.per_sqm`, вилку «Итого» и медиану."""
        stage.history(D("100"))  # 1.0
        stage.history(D("140"))  # 1.4
        hollow = stage.world.contract(rate_class=stage.rate_class)
        _estimate, proposal = stage.world.estimate(hollow, vat_rate=D("0"))
        stage.world.line(
            proposal,
            chapter=stage.chapter(proposal, "R"),
            unit_cost=D("100"),
            total=None,
            suggested=D("10"),
            quantity=D("10"),
        )
        stage.db.flush()
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("150"))  # 1.5

        body = _ok(member_client, stage.tender, rnd)

        assert hollow.id in {c["id"] for c in body["history"]["contracts"]}
        assert hollow.id not in {row["contract_id"] for row in body["history"]["per_sqm"]}
        total_fork = body["total"]["ours_class_per_sqm"]
        assert (_dec(total_fork["min"]["net"]), _dec(total_fork["max"]["net"])) == (D("1.0"), D("1.4"))
        assert _participant(body, part)["class_median_pct"] == "25.0"

    def test_per_sqm_is_not_rounded_but_whole_is(self, member_client, stage):
        """₽/м² — ставка за единицу, без округления до копеек; «статья целиком» — сумма."""
        stage.history(D("100"))  # 1.0
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("123.45"))  # 1234.5 нетто -> 1.2345 ₽/м²

        body = _ok(member_client, stage.tender, rnd)

        assert _dec(_participant(body, part)["per_sqm"]["net"]) == D("1.2345")
        assert _dec(_cell(_article(body, stage, "R"), part)["per_sqm"]["net"]) == D("1.2345")
        assert _dec(_cell(body["total"], part)["per_sqm"]["net"]) == D("1.2345")
        # (1.2345 - 1.0) * 1000 = 234.5 -> до копеек
        assert _cell(_article(body, stage, "R"), part)["whole"] == {"net": "234.50", "shown": "234.50"}

    def test_articles_follow_the_classifier_order_not_the_id(self, member_client, stage, db_session):
        lowest = db_session.execute(sa.select(sa.func.min(WorkCategory.sort_order))).scalar_one()
        first = WorkCategory(code="8800", title="Статья R0", parent_id=None, sort_order=lowest - 1)
        db_session.add(first)
        db_session.flush()
        stage.cats["R0"] = first  # заведена позже R, но раньше его по классификатору
        rnd, part = _two_history_and_kp(stage)
        stage.kp(part, D("100"), cat="R0")

        body = _ok(member_client, stage.tender, rnd)

        assert first.id > stage.cats["R"].id
        assert [a["work_category_id"] for a in body["articles"]] == [first.id, stage.cats["R"].id]

    def test_a_root_only_in_the_history_is_not_an_article(self, member_client, stage):
        stage.history(D("100"), cat="R2")
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        stage.kp(part, D("120"))

        body = _ok(member_client, stage.tender, rnd)

        assert [a["work_category_id"] for a in body["articles"]] == [stage.cats["R"].id]

    def test_total_row_has_the_article_form(self, member_client, stage):
        rnd, _part = _two_history_and_kp(stage)

        body = _ok(member_client, stage.tender, rnd)

        total = body["total"]
        assert set(total) == set(_article(body, stage, "R"))
        assert (total["work_category_id"], total["code"], total["title"], total["sort_order"]) == (
            None,
            None,
            "Итого на м² объекта",
            None,
        )


# ---------------------------------------------------------------------------
#  Ставка показа
# ---------------------------------------------------------------------------


class TestShownRate:
    def test_different_participant_rates_leave_the_common_column_in_net(self, member_client, stage):
        stage.history(D("100"))
        stage.history(D("140"))
        rnd = stage.round()
        with20 = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        with0 = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        stage.kp(with20, D("144"))
        stage.kp(with0, D("150"))

        body = _ok(member_client, stage.tender, rnd)

        assert body["history"]["shown_rate"] is None
        assert all(row["shown"] is None for row in body["history"]["per_sqm"])
        article = _article(body, stage, "R")
        assert article["ours_class_per_sqm"]["min"]["shown"] is None
        assert article["ours_class_per_sqm"]["max"]["shown"] is None
        assert body["total"]["ours_class_per_sqm"]["min"]["shown"] is None
        # у каждого участника — в своей ставке
        assert _dec(_participant(body, with20)["per_sqm"]["shown"]) == D("1.44")
        assert _dec(_participant(body, with0)["per_sqm"]["shown"]) == D("1.5")
        own_cell = _cell(article, with20)["ours_class_per_sqm"]
        assert _dec(own_cell["min"]["shown"]) == D("1.2")
        assert _dec(own_cell["max"]["shown"]) == D("1.68")
        zero_cell = _cell(article, with0)["ours_class_per_sqm"]
        assert _dec(zero_cell["min"]["shown"]) == D("1.0")
        # «статья целиком» и итоги — тоже в ставке участника, а не общей колонки
        assert _cell(article, with20)["whole"] == {"net": "200.00", "shown": "240.00"}
        assert _participant(body, with20)["totals"]["bargain"]["shown"] is not None

    def test_one_participant_rate_is_shared_by_the_common_column(self, member_client, stage):
        stage.history(D("100"))
        stage.history(D("140"))
        rnd = stage.round()
        stage.participant(rnd, "Подрядчик А", vat=D("22"))
        stage.participant(rnd, "Подрядчик Б", estimate=False)
        stage.participant(rnd, "Подрядчик В", vat=None)

        body = _ok(member_client, stage.tender, rnd)

        assert body["history"]["shown_rate"] == "22"
        shown = sorted(_dec(row["shown"]) for row in body["history"]["per_sqm"])
        assert shown == [D("1.22"), D("1.708")]

    def test_same_rate_everywhere_is_that_rate(self, member_client, stage):
        stage.history(D("100"))
        stage.history(D("140"))
        rnd = stage.round()
        first = stage.participant(rnd, "Подрядчик А", vat=D("20"))
        second = stage.participant(rnd, "Подрядчик Б", vat=D("20"))
        stage.kp(first, D("144"))
        stage.kp(second, D("144"))

        body = _ok(member_client, stage.tender, rnd)

        assert body["history"]["shown_rate"] == "20"
        fork = _article(body, stage, "R")["ours_class_per_sqm"]
        assert (_dec(fork["min"]["shown"]), _dec(fork["max"]["shown"])) == (D("1.2"), D("1.68"))

    def test_no_known_rate_leaves_shown_null(self, member_client, stage):
        stage.history(D("100"))
        rnd = stage.round()
        stage.participant(rnd, "Подрядчик А", estimate=False)

        body = _ok(member_client, stage.tender, rnd)

        assert body["history"]["shown_rate"] is None
        assert [row["shown"] for row in body["history"]["per_sqm"]] == [None]


# ---------------------------------------------------------------------------
#  ИПЦ
# ---------------------------------------------------------------------------


def _dated_history(stage_, unit_cost=D("100")):
    """Договор истории с периодом 2025-02 (сметы без даты подготовки — период от даты подписания)."""
    contract, estimate, proposal = stage_.history(unit_cost)
    contract.signed_date = dt.date(2025, 2, 20)
    estimate.data_prepared_on_date = None
    stage_.db.flush()
    return contract, estimate, proposal


def _keys(node, found=None):
    found = set() if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            _keys(value, found)
    elif isinstance(node, list):
        for item in node:
            _keys(item, found)
    return found


class TestInflation:
    def _scene(self, stage_):
        contract, _, _ = _dated_history(stage_)
        rnd = stage_.round(held_on=dt.date(2026, 8, 10))
        part = stage_.participant(rnd, "Подрядчик А", vat=D("0"))
        stage_.kp(part, D("120"))
        return contract, rnd, part

    def test_without_a_series_there_is_no_inflation_key_anywhere(self, member_client, stage):
        _, rnd, _ = self._scene(stage)

        body = _ok(member_client, stage.tender, rnd)

        assert "inflation" not in _keys(body)
        assert not {k for k in _keys(body) if "inflat" in k or k in {"nominal", "factor"}}

    def test_a_series_appearing_in_the_database_does_not_change_the_nominal_answer(
        self, member_client, stage, db_session
    ):
        _, rnd, _ = self._scene(stage)
        before = _get(member_client, stage.tender, rnd).text

        fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        after = _get(member_client, stage.tender, rnd).text

        assert before == after

    def test_with_a_series_the_block_and_the_history_are_adjusted_but_the_kp_is_not(
        self, member_client, stage, db_session
    ):
        contract, rnd, part = self._scene(stage)
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS, forecast=(2026,), note="пояснение")
        nominal = _ok(member_client, stage.tender, rnd)

        body = _ok(member_client, stage.tender, rnd, inflation_series_id=series_id)

        block = body["inflation"]
        assert block["series_id"] == series_id
        assert block["series_name"] == SERIES_NAME
        assert block["series_note"] == "пояснение"
        assert block["target_month"] == "2026-08"
        assert block["has_forecast"] is True
        assert [item["year"] for item in block["used_years"]] == [2025, 2026]
        assert block["sentence"] == f"Цены приведены к августу 2026 по ряду «{SERIES_NAME}»; 2026 — прогноз."
        (row,) = body["history"]["per_sqm"]
        assert row["contract_id"] == contract.id
        assert abs(_dec(row["net"]) - FACTOR_FEB_2025) < D("1e-20")
        # КП не приводится
        assert _participant(body, part)["per_sqm"] == _participant(nominal, part)["per_sqm"]
        # ставка ступени приведена: (120 - 100 * k) * 10 = 88.97 вместо 200.00
        assert _participant(nominal, part)["totals"]["bargain"]["net"] == "200.00"
        assert _participant(body, part)["totals"]["bargain"]["net"] == "88.97"

    def test_a_kp_prepared_long_before_the_target_is_not_adjusted(self, member_client, stage, db_session):
        """КП на своей дате не приводится, даже если она далека от месяца этапа: у
        сцены выше дата КП совпадает с целью, и приведение КП там дало бы коэффициент 1."""
        _dated_history(stage)
        rnd = stage.round(held_on=dt.date(2026, 8, 10))
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"), prepared=dt.date(2025, 2, 1))
        stage.kp(part, D("120"))
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        nominal = _ok(member_client, stage.tender, rnd)

        body = _ok(member_client, stage.tender, rnd, inflation_series_id=series_id)

        assert _dec(_participant(body, part)["per_sqm"]["net"]) == D("1.2")
        assert _participant(body, part)["per_sqm"] == _participant(nominal, part)["per_sqm"]
        assert (
            _cell(_article(body, stage, "R"), part)["per_sqm"] == _cell(_article(nominal, stage, "R"), part)["per_sqm"]
        )

    def test_the_articles_fork_is_adjusted_too(self, member_client, stage, db_session):
        _, rnd, part = self._scene(stage)
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)

        body = _ok(member_client, stage.tender, rnd, inflation_series_id=series_id)

        fork = _article(body, stage, "R")["ours_class_per_sqm"]
        assert abs(_dec(fork["min"]["net"]) - FACTOR_FEB_2025) < D("1e-20")
        assert body["total"]["ours_class_per_sqm"]["min"]["net"] == fork["min"]["net"]

    def test_unknown_series_is_404(self, member_client, stage):
        _, rnd, _ = self._scene(stage)

        response = _get(member_client, stage.tender, rnd, inflation_series_id=987654321)

        assert response.status_code == 404

    def test_series_404_comes_before_the_target_check(self, member_client, stage):
        rnd = stage.round(held_on=None)

        response = _get(member_client, stage.tender, rnd, inflation_series_id=987654321)

        assert response.status_code == 404
        assert isinstance(response.json()["detail"], str)

    def test_without_both_dates_a_series_is_422_target_missing(self, member_client, stage, db_session):
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        rnd = stage.round(held_on=None)
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"), prepared=None)
        stage.kp(part, D("120"))

        response = _get(member_client, stage.tender, rnd, inflation_series_id=series_id)

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "inflation_target_missing"
        assert detail["message"] == TARGET_MISSING_TEXT

    def test_without_both_dates_and_without_a_series_the_answer_is_nominal(self, member_client, stage):
        rnd = stage.round(held_on=None)
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"), prepared=None)
        stage.kp(part, D("120"))

        body = _ok(member_client, stage.tender, rnd)

        assert "inflation" not in body
        assert _participant(body, part)["state"] == "ok"

    def test_prepared_date_of_the_kp_is_the_target_without_held_on(self, member_client, stage, db_session):
        _dated_history(stage)
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        rnd = stage.round(held_on=None)
        stage.participant(rnd, "Подрядчик А", vat=D("0"), prepared=dt.date(2026, 8, 3))

        body = _ok(member_client, stage.tender, rnd, inflation_series_id=series_id)

        assert body["inflation"]["target_month"] == "2026-08"

    def test_target_missing_comes_before_amendment_date_missing(self, member_client, stage, db_session):
        contract, _, _ = stage.history(D("100"))
        amendment, _ = stage.world.estimate(contract, amendment_no=1)
        amendment.data_prepared_on_date = None
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        rnd = stage.round(held_on=None)
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"), prepared=None)
        stage.kp(part, D("120"))
        db_session.flush()

        response = _get(member_client, stage.tender, rnd, inflation_series_id=series_id)

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "inflation_target_missing"

    def test_amendment_date_missing_comes_before_missing_years(self, member_client, stage, db_session):
        contract, _, _ = stage.history(D("100"))
        amendment, _ = stage.world.estimate(contract, amendment_no=1)
        amendment.data_prepared_on_date = None
        series_id = fx.series_with_years(db_session, SERIES_NAME, {2020: "1.05"})
        rnd = stage.round(held_on=dt.date(2026, 8, 10))
        db_session.flush()

        response = _get(member_client, stage.tender, rnd, inflation_series_id=series_id)

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "amendment_date_missing"
        assert detail["estimate_ids"] == [amendment.id]

    def test_missing_years_alone_is_422_with_the_years(self, member_client, stage, db_session):
        _dated_history(stage)
        series_id = fx.series_with_years(db_session, SERIES_NAME, {2026: "1.060"})
        rnd = stage.round(held_on=dt.date(2026, 8, 10))

        response = _get(member_client, stage.tender, rnd, inflation_series_id=series_id)

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "missing_inflation_years"
        assert detail["missing_years"] == [2025]


# ---------------------------------------------------------------------------
#  Прогресс плашки
# ---------------------------------------------------------------------------


class TestProgress:
    def test_progress_counts_every_kp_line_of_the_round(self, member_client, stage, db_session):
        rnd = stage.round()
        first = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        second = stage.participant(rnd, "Подрядчик Б", vat=D("0"))
        stage.world.chapter(first.proposal, None)  # глава — не строка
        running = stage.kp(first, D("100"), variant=False)
        stuck = stage.kp(first, D("100"), variant=False)
        stage.kp(first, D("100"), variant=False)  # ни сигнала, ни варианта
        with_variant = stage.kp(first, D("100"))
        running_too = stage.kp(second, D("100"), variant=False)
        _job(db_session, "family_suggestion", "pending", context_id=running.context_id)
        _job(db_session, "family_suggestion", "running", context_id=running_too.context_id)
        _job(db_session, "family_suggestion", "error", context_id=stuck.context_id)
        _job(db_session, "family_suggestion", "error", context_id=with_variant.context_id)

        body = _ok(member_client, stage.tender, rnd)

        # строк 5; в работе две; ждёт администратора одна: у строки с вариантом флаг
        # сигнала поднят, но вариант есть, и она в счёт не идёт
        assert body["progress"] == {"lines_total": 5, "in_progress": 2, "waiting_admin": 1}

    def test_paused_claim_turns_a_live_job_into_waiting(self, member_client, stage, db_session):
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("100"), variant=False)
        _job(db_session, "family_suggestion", "pending", context_id=item.context_id)
        _pause(db_session)

        body = _ok(member_client, stage.tender, rnd)

        assert body["progress"] == {"lines_total": 1, "in_progress": 0, "waiting_admin": 1}

    def test_a_line_both_in_progress_and_stuck_counts_only_as_in_progress(self, member_client, stage, db_session):
        """Живое задание и задание в `error` на одном контексте: сигнал поднимает оба
        флага, но строка в работе не «ждёт администратора» (§2.11)."""
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        item = stage.kp(part, D("100"), variant=False)
        _job(db_session, "family_suggestion", "pending", context_id=item.context_id)
        _job(db_session, "family_suggestion", "error", context_id=item.context_id)

        body = _ok(member_client, stage.tender, rnd)

        assert body["progress"] == {"lines_total": 1, "in_progress": 1, "waiting_admin": 0}

    def test_lines_of_other_rounds_are_not_counted(self, member_client, stage):
        rnd = stage.round()
        other = stage.round()
        mine = stage.participant(rnd, "Подрядчик А", vat=D("0"))
        theirs = stage.participant(other, "Подрядчик А2", vat=D("0"))
        stage.kp(mine, D("100"))
        stage.kp(theirs, D("100"))
        stage.kp(theirs, D("100"))

        assert _ok(member_client, stage.tender, rnd)["progress"]["lines_total"] == 1


# ---------------------------------------------------------------------------
#  Число запросов
# ---------------------------------------------------------------------------


class TestQueryCount:
    def test_statement_count_does_not_grow_with_lines_and_participants(self, member_client, stage, db_session):
        stage.history(D("100"))
        stage.history(D("140"))
        rnd = stage.round()
        first = stage.participant(rnd, "Подрядчик А")
        stage.kp(first, D("144"))
        stage.kp(first, D("96"), cat="R2")
        with count_statements(db_session) as small:
            assert _get(member_client, stage.tender, rnd).status_code == 200

        for index in range(2):
            extra = stage.participant(rnd, f"Подрядчик {index}Я")
            for _ in range(9):
                stage.kp(extra, D("120"))
                stage.kp(extra, D("120"), cat="R2")
        with count_statements(db_session) as large:
            body = _ok(member_client, stage.tender, rnd)

        assert body["progress"]["lines_total"] == 38
        assert len(small) == len(large)

    def test_statement_count_is_the_same_with_a_series(self, member_client, stage, db_session):
        stage.history(D("100"))
        rnd = stage.round()
        part = stage.participant(rnd, "Подрядчик А")
        stage.kp(part, D("144"))
        series_id = fx.series_with_years(db_session, SERIES_NAME, FULL_YEARS)
        with count_statements(db_session) as small:
            assert _get(member_client, stage.tender, rnd, inflation_series_id=series_id).status_code == 200
        for _ in range(10):
            stage.kp(part, D("144"))
        with count_statements(db_session) as large:
            assert _get(member_client, stage.tender, rnd, inflation_series_id=series_id).status_code == 200

        assert len(small) == len(large)


# ---------------------------------------------------------------------------
#  Спека называет то, что делает код
# ---------------------------------------------------------------------------


class TestSpecNamesTheContract:
    SPEC = "docs/superpowers/specs/2026-10-23-our-price-design.md"

    def _section(self, number: str) -> str:
        import pathlib

        text = (pathlib.Path(__file__).resolve().parents[3] / self.SPEC).read_text(encoding="utf-8")
        start = text.index(f"### {number}.")
        end = text.index("\n### ", start + 1)
        return text[start:end]

    def test_inflation_section_names_the_latest_prepared_date_rule(self):
        assert "самой поздней `data_prepared_on_date` среди смет КП этапа" in self._section("2.10")

    def test_api_section_names_the_round_fields(self):
        section = self._section("2.12")
        for name in ("per_sqm_reason", "class_median_pct", "total:", "sentence"):
            assert name in section, name
        # имена тройки `progress` в разделе были и раньше; проверяется её правило
        flat = " ".join(section.split())
        assert "`waiting_admin` — строки, у которых нет варианта, нет `in_progress`" in flat
        assert "Этап без смет — три нуля" in flat
