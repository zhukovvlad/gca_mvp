"""Разложение статьи: чтение входов, сборка групп, HTTP-контракт (спека
2026-08-30-position-drilldown-design.md §2.1, §2.2, §2.7, §2.11).

Сметы строятся НАСТОЯЩИМ import_round — как в test_stage_summary_api.py."""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import position_drilldown as crud_pd
from crud import stage_summary as crud_ss
from crud.common import DomainError
from models import (
    Estimate,
    EstimateAdditionalWork,
    Lot,
    Offer,
    PositionItem,
    Proposal,
    TenderRound,
    WorkCategory,
)
from services import position_drilldown as pd_service
from services import stage_summary as ss
from services.category_resolution import CategoryResolver
from services.matching import match_positions
from services.round_import import import_round
from services.unit_resolution import UnitResolver

# `chaptered` и `count_queries` — обычные функции соседнего файла, их можно
# импортировать; фикстуру `grid` — НЕ импортируем: своя `drill_grid` ниже
# отличается запуском матчинга.
from tests.integration.test_stage_summary_api import chaptered, count_queries
from tests.payloads import (
    additional_works_row,
    position,
    proposal,
    round_payload,
    svedeniya_info,
)

pytestmark = pytest.mark.integration

D = Decimal


def estimates_of(db, offer_ids):
    return db.execute(
        sa.select(Estimate.id).join(Offer, Offer.id == Estimate.offer_id)
        .join(TenderRound, TenderRound.id == Offer.round_id)
        .where(Estimate.offer_id.in_(offer_ids)).order_by(TenderRound.stage_no)
    ).scalars().all()


def category_id(db, code):
    return db.execute(sa.select(WorkCategory.id).where(WorkCategory.code == code)).scalar_one()


def import_and_match(db, *, tender_round, data):
    """Импорт раунда И МАТЧИНГ.

    `import_round` матчинг НЕ вызывает — он только возвращает
    `positions_to_match`, а каскад запускает пайплайн (докстрока
    `services/round_import.import_round`, §2.5 п.5 её спеки). Без этого шага у
    всех строк `catalog_position_id` остаётся NULL, и разложение видит одну
    группу `unmatched` вместо работ — тесты этого файла молча мерили бы не тот
    вид строк (ревью плана 31.08.2026).
    """
    outcome = import_round(db, tender_round=tender_round, data=data, parser_version="4.0.0",
                           import_job_id=None, replace=False,
                           unit_resolver=UnitResolver(db),
                           category_resolver=CategoryResolver.from_db(db))
    match_positions(db, outcome.positions_to_match)
    db.flush()
    return outcome


@pytest.fixture
def drill_grid(db_session, factories):
    """Тот же тендер, что `grid` свода, но собранный С МАТЧИНГОМ.

    Своя фикстура, а не переиспользование `grid`: соседнюю фикстуру нельзя
    позвать функцией (это pytest-фикстура), а `match_positions` принимает
    `PositionToMatch` из результата импорта, а не строки из БД — то есть
    матчинг надо запускать В МОМЕНТ импорта. Тестам свода каталог не нужен, и
    их фикстура остаётся как есть.

    Суммы совпадают с `grid` (§ докстроки соседней фикстуры): р1 6→120, 2→60;
    р2 6→96 плюс допработы 24, 2→0; р3 6→100; р4 6→90. `path` — трасса 1, 2, 4.
    """
    tender = factories.TenderFactory.create()
    rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2, 3, 4)}
    db_session.flush()
    payloads = {
        1: [chaptered({"1": ("6", "120.00"), "2": ("2", "60.00")}, total="180.00")],
        2: [chaptered({"1": ("6", "96.00"), "2": ("2", "0")}, total="120.00",
                      additional=additional_works_row(total="24.00"))],
        3: [chaptered({"1": ("6", "100.00")}, total="100.00")],
        4: [chaptered({"1": ("6", "90.00")}, total="90.00")],
    }
    for n, rnd in rounds.items():
        import_and_match(db_session, tender_round=rnd, data=round_payload(payloads[n]))
    offers = db_session.execute(
        sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
        .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()

    class G:
        pass
    g = G()
    g.tender = tender
    g.rounds = rounds
    g.offers = offers
    g.path = [offers[0], offers[1], offers[3]]      # трасса 1, 2, 4
    return g


class TestLoadGroups:
    def test_same_catalog_position_in_parent_and_child_is_one_group(self, db_session, factories):
        """Негативная §6.1: ключ группы — каталожная позиция БЕЗ статьи. Одна и
        та же работа (то же наименование и единица => та же каталожная позиция,
        matching get-or-create) на этапе 1 лежит в статье-родителе, на этапе 2 —
        в статье-потомке; групп в поддереве родителя обязана быть ОДНА, без
        исчезновения. Ключ со статьёй здесь краснеет двумя группами."""
        tender = factories.TenderFactory.create()
        rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)}
        db_session.flush()
        parent_code, child_code = "6", "6.1"
        for n, art in ((1, parent_code), (2, child_code)):
            positions = [position(job_title="Раздел", is_chapter=True, chapter_number="1",
                                  article_smr=art, number="1"),
                         position(job_title="Фасад корпуса", unit="м2", quantity=1, suggested_quantity=5,
                                  unit_cost_total="100.00", total_cost_total="100.00",
                                  chapter_ref="1", number="2")]
            import_and_match(db_session, tender_round=rounds[n],
                             data=round_payload([proposal(positions, vat_rate="20")]))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
        estimates = estimates_of(db_session, offers)
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, parent_code))
        groups = crud_pd.load_groups(db_session, estimates, subtree)
        works = [g for g in groups if g.kind == pd_service.KIND_POSITION]
        assert len(works) == 1
        assert set(works[0].stages) == {0, 1}          # оба этапа, исчезновения нет

    def test_extras_are_rows_by_ref_and_title_comes_from_the_last_stage(self, db_session, factories):
        """§2.7: две ссылки с одним наименованием — ДВЕ группы (ключ по
        наименованию краснеет); подпись группы — с последнего этапа присутствия.

        Поправка к брифу: ссылка «Сведений» резолвится в статью ТОЛЬКО по
        точному совпадению с номером раздела (`resolve_ref`/
        `categories_by_chapter_number`, `services/additional_works.py`) —
        раздел с номером «1» ссылку «1.1» не резолвит. В брифе был один раздел
        «1» под ссылки «1.1»/«1.2»; здесь — два раздела с НОМЕРАМИ, буквально
        равными ссылкам, оба под статьёй «6». Также исправлена контрольная
        сумма второго этапа (12.00 + 21.00 = 33.00, в брифе стояло 30.00 —
        расшивка при перевесе разобранных строк над итогом отбрасывается
        целиком, гейт §2.6 `parsed_sum > total`).
        """
        tender = factories.TenderFactory.create()
        rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)}
        db_session.flush()
        info = {
            1: svedeniya_info("1.1 Монолитные конструкции - 10.00 руб.",
                              "1.2 Монолитные конструкции - 20.00 руб."),
            2: svedeniya_info("1.1 Монолитные конструкции, уточнено - 12.00 руб.",
                              "1.2 Монолитные конструкции - 21.00 руб."),
        }
        totals = {1: "30.00", 2: "33.00"}
        for n, rnd in rounds.items():
            positions = [position(job_title="Раздел 1.1", is_chapter=True, chapter_number="1.1",
                                  article_smr="6", number="1"),
                         position(job_title="Раздел 1.2", is_chapter=True, chapter_number="1.2",
                                  article_smr="6", number="2")]
            import_and_match(db_session, tender_round=rnd,
                             data=round_payload([proposal(positions, vat_rate="20",
                                                          additional_works=additional_works_row(total=totals[n]),
                                                          additional_info=info[n])]))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        groups = crud_pd.load_groups(db_session, estimates_of(db_session, offers), subtree)
        extras = sorted((g for g in groups if g.kind == pd_service.KIND_ADDITIONAL_WORKS),
                        key=lambda g: g.chapter_ref_raw)
        assert [g.chapter_ref_raw for g in extras] == ["1.1", "1.2"]
        assert extras[0].title == "Монолитные конструкции, уточнено"   # последний этап
        assert set(extras[0].stages) == {0, 1}

    def test_unmatched_rows_fold_into_one_group_per_subtree(self, db_session, drill_grid):
        """Фикстурная ветка §5: привязку снимаем руками (на живой базе её
        снимает только недоработанный матчинг, 0 из 64 505)."""
        db_session.execute(sa.update(PositionItem).where(PositionItem.is_chapter.is_(False))
                           .values(catalog_position_id=None))
        db_session.flush()
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        estimates = estimates_of(db_session, drill_grid.path)
        groups = crud_pd.load_groups(db_session, estimates, subtree)
        unmatched = [g for g in groups if g.kind == pd_service.KIND_UNMATCHED]
        assert len(unmatched) == 1 and unmatched[0].title == crud_pd.UNMATCHED_TITLE

    def test_query_count_does_not_grow_with_columns(self, db_session, drill_grid):
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        two = estimates_of(db_session, drill_grid.path[:2])
        three = estimates_of(db_session, drill_grid.path)
        with count_queries(db_session) as c2:
            crud_pd.load_groups(db_session, two, subtree)
        with count_queries(db_session) as c3:
            crud_pd.load_groups(db_session, three, subtree)
        assert c2["n"] == c3["n"]

    def test_non_finite_row_is_counted_but_left_out_of_the_sum(self, db_session, drill_grid):
        """§1.7: правило конечности VIEW повторяется здесь, иначе разложение не
        сойдётся со статьёй. Неконечных сумм на живой базе нет (0 из 64 505),
        поэтому ветка ставится UPDATE-ом — как и снятая привязка выше; РУЧНАЯ
        ВСТАВКА строки не годится: у `position_items` есть обязательные поля
        (`position_key_in_proposal`), и тест ломался бы на них, а не на правиле."""
        estimates = estimates_of(db_session, drill_grid.path)
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        before = crud_pd.load_groups(db_session, estimates, subtree)
        work = next(g for g in before if g.kind == pd_service.KIND_POSITION)
        stage = min(work.stages)
        # Ставим NaN ОДНОЙ строке группы на первом этапе: строка остаётся в
        # файле (счётчик её видит), но в сумму не входит.
        target = db_session.execute(
            sa.select(PositionItem.id).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == estimates[stage], PositionItem.is_chapter.is_(False),
                   PositionItem.catalog_position_id == work.catalog_position_id)
        ).scalars().first()
        db_session.execute(sa.update(PositionItem).where(PositionItem.id == target)
                           .values(total_cost_total=D("NaN")))
        db_session.flush()
        after = crud_pd.load_groups(db_session, estimates, subtree)
        same = next(g for g in after if g.catalog_position_id == work.catalog_position_id)
        assert same.stages[stage].rows == work.stages[stage].rows          # строка посчитана
        assert same.stages[stage].gross == work.stages[stage].gross - D("120.00")   # и вычтена из суммы

    def test_group_of_only_non_finite_rows_is_zero_not_absent(self, db_session, drill_grid):
        """Продолжение правила: у группы, где ВСЕ строки этапа неконечны, сумма
        ноль при ненулевом счётчике — то есть ячейка получает нулевое состояние
        («не оценивалась» / «снято»), а НЕ `absent`. Иначе поехала бы
        эквивалентность §2.11 `estimate_rows == 0 ⟺ absent`: строка в файле
        есть, и говорить «нет в файле» о ней нельзя."""
        estimates = estimates_of(db_session, drill_grid.path)
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        stage = 0
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id.in_(
                sa.select(PositionItem.id).join(Proposal, Proposal.id == PositionItem.proposal_id)
                .join(Lot, Lot.id == Proposal.lot_id)
                .where(Lot.estimate_id == estimates[stage], PositionItem.is_chapter.is_(False))))
            .values(total_cost_total=D("Infinity")))
        db_session.flush()
        groups = crud_pd.load_groups(db_session, estimates, subtree)
        work = next(g for g in groups if g.kind == pd_service.KIND_POSITION)
        assert work.stages[stage].rows > 0 and work.stages[stage].gross == D("0")
        columns = [pd_service.DrillColumn(offer_id=o, estimate_id=e, round_id=0, stage_no=i + 1,
                                          label=None, held_on=None, vat_rate_base=D("20"))
                   for i, (o, e) in enumerate(zip(drill_grid.path, estimates, strict=True))]
        cells = pd_service.group_cells(work, columns, ss.pick_tax_basis([c.vat_rate_base for c in columns]))
        assert cells[stage].state != "absent" and cells[stage].estimate_rows > 0

    @pytest.mark.parametrize("bad", [D("NaN"), D("Infinity")], ids=["nan", "inf"])
    def test_non_finite_extra_row_is_counted_but_left_out_of_the_sum(self, db_session, factories, bad):
        """§1.7, ветка допработ: то же правило конечности, что для позиций
        (`test_non_finite_row_is_counted_but_left_out_of_the_sum`), но у
        `EstimateAdditionalWork.total_amount` — единственная сумма (`crud/
        position_drilldown.py::load_groups`, ветка `extra_rows`) БЕЗ этого
        правила до фикса шла в `sum()` необёрнутой, а `total_amount >= 0`
        (единственный CHECK на колонке) NaN и `Infinity` пропускает
        («'NaN'::numeric >= 0» и «'Infinity'::numeric >= 0» истинны в
        Postgres) — значит строка была допустимой в базе, но валила сумму
        статьи. Ставится UPDATE-ом на импортированную строку, а не вставкой:
        у `estimate_additional_works` тоже есть обязательные поля
        (`raw_line`), и вставка ломалась бы на них, а не на правиле.

        `-Infinity` НЕ параметризован здесь (в отличие от позиций, где та же
        троица испытана без проблем): проверено запуском — UPDATE с
        `-Infinity` падает `CheckViolation` на `ck_estimate_additional_works_
        total_amount` (`total_amount >= 0`), потому что «'-Infinity'::numeric
        >= 0» ложно. У `position_items.total_cost_total` такого CHECK нет
        (только допустимость статьи/источника категории), поэтому там все три
        значения проходят; здесь `-Infinity` физически не может лежать в
        колонке — тест на невозможном состоянии не нужен."""
        tender = factories.TenderFactory.create()
        rnd = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
        db_session.flush()
        positions = [position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                              article_smr="6", number="1")]
        import_and_match(db_session, tender_round=rnd, data=round_payload([proposal(
            positions, vat_rate="20",
            additional_works=additional_works_row(total="30.00"),
            additional_info=svedeniya_info("1 Первая по файлу - 10.00 руб.",
                                           "1 Вторая по файлу - 20.00 руб."))]))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id)).scalars().all()
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        estimates = estimates_of(db_session, offers)
        before = crud_pd.load_groups(db_session, estimates, subtree)
        extra = next(g for g in before if g.kind == pd_service.KIND_ADDITIONAL_WORKS)
        assert extra.stages[0].rows == 2 and extra.stages[0].gross == D("30.00")   # предпосылка
        # Ordinal 1 — строка «Первая по файлу», 10.00: ставим её неконечной.
        db_session.execute(sa.update(EstimateAdditionalWork)
                           .where(EstimateAdditionalWork.ordinal == 1).values(total_amount=bad))
        db_session.flush()
        after = crud_pd.load_groups(db_session, estimates, subtree)
        same = next(g for g in after if g.kind == pd_service.KIND_ADDITIONAL_WORKS)
        assert same.stages[0].rows == 2                       # строка посчитана
        assert same.stages[0].gross == D("20.00")              # 30 - 10 (неконечная вычтена)

    def test_group_of_only_non_finite_extra_rows_is_zero_not_absent(self, db_session, factories):
        """Продолжение правила для допработ (аналог
        `test_group_of_only_non_finite_rows_is_zero_not_absent` у позиций):
        группа, у которой ВСЕ строки этапа неконечны, обязана дать нулевую
        сумму при ненулевом счётчике — ноль как СОСТОЯНИЕ, а не отсутствие
        строки; иначе эквивалентность §2.11 `estimate_rows == 0 ⟺ absent`
        поехала бы и здесь, во втором источнике групп."""
        tender = factories.TenderFactory.create()
        rnd = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
        db_session.flush()
        positions = [position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                              article_smr="6", number="1")]
        import_and_match(db_session, tender_round=rnd, data=round_payload([proposal(
            positions, vat_rate="20",
            additional_works=additional_works_row(total="30.00"),
            additional_info=svedeniya_info("1 Первая по файлу - 10.00 руб.",
                                           "1 Вторая по файлу - 20.00 руб."))]))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id)).scalars().all()
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        estimates = estimates_of(db_session, offers)
        db_session.execute(sa.update(EstimateAdditionalWork).values(total_amount=D("Infinity")))
        db_session.flush()
        groups = crud_pd.load_groups(db_session, estimates, subtree)
        extra = next(g for g in groups if g.kind == pd_service.KIND_ADDITIONAL_WORKS)
        assert extra.stages[0].rows > 0 and extra.stages[0].gross == D("0")
        columns = [pd_service.DrillColumn(offer_id=offers[0], estimate_id=estimates[0], round_id=0,
                                          stage_no=1, label=None, held_on=None, vat_rate_base=D("20"))]
        cells = pd_service.group_cells(extra, columns, ss.pick_tax_basis([c.vat_rate_base for c in columns]))
        assert cells[0].state != "absent" and cells[0].estimate_rows > 0

    def test_several_rows_under_one_ref_are_one_group_titled_by_ordinal(self, db_session, factories):
        """§2.7: ссылка ГРУППИРУЕТ работу (три такие группы на стенде: 5, 2, 2
        строки). Здесь наименования РАЗНЫЕ — случай, на котором правило подписи
        различимо: берётся первая по файлу, а группа несёт пилюлю."""
        tender = factories.TenderFactory.create()
        rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)}
        db_session.flush()
        for rnd in rounds.values():
            positions = [position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                                  article_smr="6", number="1"),
                         position(job_title="Работа", unit="м2", quantity=1, suggested_quantity=1,
                                  unit_cost_total="5.00", total_cost_total="5.00",
                                  chapter_ref="1", number="2")]
            import_and_match(db_session, tender_round=rnd,
                         data=round_payload([proposal(
                             positions, vat_rate="20",
                             additional_works=additional_works_row(total="30.00"),
                             # ДВЕ строки «Сведений» с ОДНОЙ ссылкой «1» и разными наименованиями
                             additional_info=svedeniya_info("1 Первая по файлу - 10.00 руб.",
                                                            "1 Вторая по файлу - 20.00 руб."))]))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        groups = crud_pd.load_groups(db_session, estimates_of(db_session, offers), subtree)
        extras = [g for g in groups if g.kind == pd_service.KIND_ADDITIONAL_WORKS]
        assert len(extras) == 1                                   # одна ссылка — одна группа
        assert extras[0].title == "Первая по файлу"               # первая по (proposal_id, ordinal)
        assert extras[0].stages[0].rows == 2                      # пилюля «несколько строк сметы»
        assert extras[0].stages[0].gross == D("30.00")

    def test_two_lots_keep_their_refs_apart_but_share_the_catalog_key(self, db_session, factories):
        """§2.7 (ревизия 31.08.2026): номер раздела между лотами законно
        означает РАЗНЫЕ работы, поэтому одна ссылка в двух лотах даёт ДВЕ
        группы — склейка была бы невидимой, а сходимость объединения не требует
        (две строки складываются в тот же итог). У РАБОТ правило обратное и это
        не разнобой: каталожная позиция — идентичность каталога, и строки двух
        лотов с одной позицией остаются ОДНОЙ группой. Многолотовых смет в базе
        нет (блок замеров: 0 и 0 из 43), ветка фикстурная.

        Поправка к брифу: если ОБА лота несут в JSON свою агрегатную строку
        допработ с ОДНИМ и тем же текстом «Сведений» (то, что кладёт сюда
        `round_payload([payload], lots=2)` — один участник, продублированный
        в оба лота), `services.additional_works.decide_owner` видит ДВУХ
        владельцев и объявляет владельца неоднозначным: расшивка не
        применяется НИ К ОДНОМУ лоту, а `chapter_ref_raw`/`work_category_id`
        обеих строк остаются NULL — ветка проверяет как раз `decide_owner`, не
        `load_groups`. Это уже покрыто модулем допработ отдельно; здесь строки
        `estimate_additional_works` заводятся НАПРЯМУЮ (обычная фикстурная
        конструкция, как и допускает докстрока брифа «ветка фикстурная»,
        §2.7): позиции сметы по-прежнему идут настоящим импортом и матчингом,
        а расшивку допработ по лотам строим сами, минуя `decide_owner`."""
        tender = factories.TenderFactory.create()
        rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)}
        db_session.flush()
        subtree_category = category_id(db_session, "6")
        for rnd in rounds.values():
            positions = [position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                                  article_smr="6", number="1"),
                         position(job_title="Работа", unit="м2", quantity=1, suggested_quantity=1,
                                  unit_cost_total="5.00", total_cost_total="5.00",
                                  chapter_ref="1", number="2")]
            payload = proposal(positions, vat_rate="20")
            import_and_match(db_session, tender_round=rnd,
                             data=round_payload([payload], lots=2))   # ДВА лота одной сметы
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
        estimates = estimates_of(db_session, offers)
        # Предпосылка теста: смета действительно из двух лотов с двумя предложениями.
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(Proposal).join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == estimates[0])).scalar_one() == 2

        # Допработы заводим напрямую: по одной строке с одной и той же ссылкой
        # «1» на КАЖДЫЙ лот КАЖДОЙ сметы (обоих этапов).
        for estimate_id in estimates:
            lot_proposals = db_session.execute(
                sa.select(Proposal.id).join(Lot, Lot.id == Proposal.lot_id)
                .where(Lot.estimate_id == estimate_id).order_by(Lot.lot_key)
            ).scalars().all()
            for proposal_id in lot_proposals:
                db_session.add(EstimateAdditionalWork(
                    proposal_id=proposal_id, ordinal=1, chapter_ref_raw="1",
                    title="Работа лота", total_amount=D("10.00"),
                    work_category_id=subtree_category,
                    raw_line="1 Работа лота - 10.00 руб."))
        db_session.flush()

        subtree = crud_pd.subtree_ids(db_session, subtree_category)
        groups = crud_pd.load_groups(db_session, estimates, subtree)

        extras = [g for g in groups if g.kind == pd_service.KIND_ADDITIONAL_WORKS]
        assert len(extras) == 2                                  # по группе на лот
        assert {g.chapter_ref_raw for g in extras} == {"1"}       # ссылка в контракте — голая
        assert all(g.stages[0].rows == 1 and g.stages[0].gross == D("10.00") for g in extras)
        # Обе группы живут на ОБОИХ этапах: ключ лота между этапами устойчив,
        # пока подрядчик не переставил лоты (§2.7 — названная развилка).
        assert all(set(g.stages) == {0, 1} for g in extras)

        works = [g for g in groups if g.kind == pd_service.KIND_POSITION]
        assert len(works) == 1 and works[0].stages[0].rows == 2   # лот в ключ работы не входит
        assert works[0].stages[0].gross == D("10.00")

        # Ответ уровня HTTP-контракта: те же два лота обязаны остаться двумя
        # РАЗЛИЧНЫМИ строками (row_key), делящими один и тот же голый
        # chapter_ref_raw «1», с лотом в отдельном поле lot_key (§2.11).
        #
        # Суммы обоих лотов заведены выше ОДИНАКОВЫМИ на обоих этапах — годится
        # для проверки идентичности группы, но не для видимости строки:
        # `article_delta == 0` — законный случай БЕЗ единого объяснителя (§2.3),
        # и обе строки ушли бы в свёрнутые «прочие» неразличимо. Здесь суммы
        # второго этапа разводятся, чтобы у каждого лота появился свой ненулевой
        # вклад и обе строки допработ остались показаны РАЗДЕЛЬНО — иначе
        # row_key и lot_key ответа проверять было бы не на чем.
        lot_1_proposals = sa.select(Proposal.id).join(Lot, Lot.id == Proposal.lot_id).where(
            Lot.estimate_id == estimates[1], Lot.lot_key == "lot_1")
        lot_2_proposals = sa.select(Proposal.id).join(Lot, Lot.id == Proposal.lot_id).where(
            Lot.estimate_id == estimates[1], Lot.lot_key == "lot_2")
        db_session.execute(sa.update(EstimateAdditionalWork)
                           .where(EstimateAdditionalWork.proposal_id.in_(lot_1_proposals))
                           .values(total_amount=D("22.00")))
        db_session.execute(sa.update(EstimateAdditionalWork)
                           .where(EstimateAdditionalWork.proposal_id.in_(lot_2_proposals))
                           .values(total_amount=D("4.00")))
        db_session.flush()

        data = crud_pd.build_position_drilldown(db_session, tender.id, subtree_category, offers)
        extra_rows = [r for r in data["rows"] if r["kind"] == pd_service.KIND_ADDITIONAL_WORKS]
        assert len(extra_rows) == 2
        assert len({r["row_key"] for r in extra_rows}) == 2
        assert {r["chapter_ref_raw"] for r in extra_rows} == {"1"}
        assert {r["lot_key"] for r in extra_rows} == {"lot_1", "lot_2"}
        assert all(c["converged"] is True for c in data["convergence"])

    def test_quantities_are_sorted_and_distinct_within_a_stage(self, db_session, factories):
        """§2.4: объём этапа — МНОЖЕСТВО значений `suggested_quantity`, а не
        мультимножество. Три строки одной каталожной позиции на одном этапе —
        объёмы 5, 11, 5 — обязаны дать `quantities == (5, 11)`: дубль
        схлопнут, порядок отсортирован, а `rows` при этом продолжает считать
        ВСЕ ТРИ строки. Инвариант пин нужен здесь, а не только в
        `services/position_drilldown.py`: `group_cells` сравнивает объёмы
        МНОЖЕСТВАМИ (`set(quantities)`) именно потому, что этот SQL уже отдаёт
        `array_agg(DISTINCT ...)`, отсортированный в Python, — без теста на
        источнике это допущение ничем не закреплено."""
        tender = factories.TenderFactory.create()
        rnd = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
        db_session.flush()
        positions = [
            position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                     article_smr="6", number="1"),
            position(job_title="Работа", unit="м2", quantity=1, suggested_quantity=5,
                     unit_cost_total="5.00", total_cost_total="5.00", chapter_ref="1", number="2"),
            position(job_title="Работа", unit="м2", quantity=1, suggested_quantity=11,
                     unit_cost_total="7.00", total_cost_total="7.00", chapter_ref="1", number="3"),
            position(job_title="Работа", unit="м2", quantity=1, suggested_quantity=5,
                     unit_cost_total="3.00", total_cost_total="3.00", chapter_ref="1", number="4"),
        ]
        import_and_match(db_session, tender_round=rnd, data=round_payload([proposal(positions, vat_rate="20")]))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "6"))
        groups = crud_pd.load_groups(db_session, estimates_of(db_session, offers), subtree)
        work = next(g for g in groups if g.kind == pd_service.KIND_POSITION)
        assert work.stages[0].quantities == (D("5"), D("11"))     # дубль схлопнут, отсортировано
        assert work.stages[0].rows == 3                            # счётчик строк дубль не теряет


def _subtree_only_grid(db_session, factories):
    """Два раунда; все строки лежат в статье 6.1 — у статьи 6 собственных строк нет."""
    tender = factories.TenderFactory.create()
    rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)}
    db_session.flush()
    for n, amount in ((1, "120.00"), (2, "96.00")):
        positions = [position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                              article_smr="6.1", number="1"),
                     position(job_title="Фасадная работа", unit="м2", quantity=1, suggested_quantity=1,
                              unit_cost_total=amount, total_cost_total=amount,
                              chapter_ref="1", number="2")]
        import_and_match(db_session, tender_round=rounds[n],
                         data=round_payload([proposal(positions, vat_rate="20")]))
    db_session.flush()
    offers = db_session.execute(
        sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
        .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
    return tender, offers


def _extras_only_grid(db_session, factories):
    """Два раунда. Раздел 1 (статья 6) несёт работы; раздел 2 (статья 2) — ни
    одной работы, только строку «Сведений» по ссылке «2». Поддерево статьи 2
    состоит из одних допработ.

    Проверено запуском: импорт ПРИНИМАЕТ главу без единой строки под ней —
    постобработка её не отбрасывает, а `decide_owner`/`resolve_ref` резолвят
    ссылку «2» в `work_category_id` статьи «2» тем же путём, что и обычная
    ссылка внутри непустого раздела. Обходной ORM-вставки (как в фикстуре
    Task 4 для двух лотов) здесь не понадобилось — обычный `import_and_match`
    заводит нужную картину."""
    tender = factories.TenderFactory.create()
    rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)}
    db_session.flush()
    for n, extra in ((1, "24.00"), (2, "30.00")):
        positions = [position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                              article_smr="6", number="1"),
                     position(job_title="Работа фасада", unit="м2", quantity=1, suggested_quantity=1,
                              unit_cost_total="100.00", total_cost_total="100.00",
                              chapter_ref="1", number="2"),
                     # раздел БЕЗ работ под ним — его статья живёт только допработой
                     position(job_title="Раздел 2", is_chapter=True, chapter_number="2",
                              article_smr="2", number="3")]
        import_and_match(db_session, tender_round=rounds[n],
                     data=round_payload([proposal(
                         positions, vat_rate="20",
                         additional_works=additional_works_row(total=extra),
                         additional_info=svedeniya_info(f"2 Допработы участка - {extra} руб."))]))
    db_session.flush()
    offers = db_session.execute(
        sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
        .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
    return tender, offers


class TestDrilldownGroupCountMatchesLoadGroups:
    """`drilldown_group_count` свода (`crud/stage_summary.py::_row`) — число
    ГРУПП разложения поддерева, показанное ДО первой загрузки самого
    разложения (спека 2026-08-30-position-drilldown-design.md §2.1). Хард-
    инвариант: значение обязано РАВНЯТЬСЯ `len(load_groups(db, estimate_ids,
    subtree_ids(db, category_id)))` — той же паре функций, что строит сами
    группы разложения, а не пересказу их числа отдельной арифметикой.

    Ключевая ловушка (§1.9, §2.2): свёртка count-а вверх по дереву — ОБЪЕДИНЕНИЕ
    множеств ключей, а НЕ СУММА по детям, потому что `load_groups` держит
    работу ОДНОЙ группой независимо от того, под какой статьёй поддерева она
    лежит на каждом этапе — ровно случай миграции классификации
    `test_same_catalog_position_in_parent_and_child_is_one_group` выше."""

    def _expected(self, db, estimates, code):
        subtree = crud_pd.subtree_ids(db, category_id(db, code))
        return len(crud_pd.load_groups(db, estimates, subtree))

    def test_matches_load_groups_for_several_categories(self, db_session, drill_grid):
        """`drill_grid`: статья «6» несёт РАБОТЫ и ДОПРАБОТЫ (движение статьи
        включает допработы, §1.5), статья «2» — только работы, исчезающие по
        трассе. Обе — на настоящем импорте С МАТЧИНГОМ, иначе все позиции ушли
        бы одной строкой `unmatched`, и виды групп было бы не на чем различить."""
        summary = crud_ss.build_stage_summary(db_session, drill_grid.tender.id, drill_grid.path)
        estimates = estimates_of(db_session, drill_grid.path)
        by_code = {r["code"]: r for r in summary["rows"]}
        for code in ("6", "2"):
            expected = self._expected(db_session, estimates, code)
            assert expected > 0   # предпосылка: сравнение не обесценено нулём с обеих сторон
            assert by_code[code]["drilldown_group_count"] == expected

    def test_matches_load_groups_when_rows_live_only_in_a_descendant(self, db_session, factories):
        """Узел «6» без СОБСТВЕННЫХ строк вовсе — все лежат у потомка «6.1»
        (§1.7: 18 и 22 таких узла на стенде). Проверяет согласие запроса и
        свёртки на живом узле-«пустышке», а не только на узле с прямыми
        строками."""
        tender, offers = _subtree_only_grid(db_session, factories)
        summary = crud_ss.build_stage_summary(db_session, tender.id, offers)
        estimates = estimates_of(db_session, offers)
        expected = self._expected(db_session, estimates, "6")
        row = next(r for r in summary["rows"] if r["code"] == "6")
        assert expected > 0
        assert row["drilldown_group_count"] == expected

    def test_matches_load_groups_for_subtree_with_additional_works(self, db_session, factories):
        """Статья «2», в чьём поддереве ТОЛЬКО допработы (§6.2, на стенде таких
        поддеревьев нет — 0 из 143, ветка фикстурная). Guard-removal
        «игнорировать допработы» обязан уронить именно эту проверку."""
        tender, offers = _extras_only_grid(db_session, factories)
        summary = crud_ss.build_stage_summary(db_session, tender.id, offers)
        estimates = estimates_of(db_session, offers)
        expected = self._expected(db_session, estimates, "2")
        row = next(r for r in summary["rows"] if r["code"] == "2")
        assert expected > 0
        assert row["drilldown_group_count"] == expected

    def test_parent_child_migration_counts_as_one_not_two(self, db_session, factories):
        """Негативная §1.9/§2.2: та же каталожная позиция — под статьёй-родителем
        на этапе 1, под статьёй-потомком на этапе 2 (конструкция теста
        `TestLoadGroups.test_same_catalog_position_in_parent_and_child_is_one_
        group` выше). `load_groups` строит по ней ОДНУ группу; свёртка count-а
        СУММОЙ по детям дала бы 2 — ровно guard-removal, который просит бриф
        задачи (см. отчёт)."""
        tender = factories.TenderFactory.create()
        rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n) for n in (1, 2)}
        db_session.flush()
        parent_code, child_code = "6", "6.1"
        for n, art in ((1, parent_code), (2, child_code)):
            positions = [position(job_title="Раздел", is_chapter=True, chapter_number="1",
                                  article_smr=art, number="1"),
                         position(job_title="Фасад корпуса", unit="м2", quantity=1, suggested_quantity=5,
                                  unit_cost_total="100.00", total_cost_total="100.00",
                                  chapter_ref="1", number="2")]
            import_and_match(db_session, tender_round=rounds[n],
                             data=round_payload([proposal(positions, vat_rate="20")]))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
        summary = crud_ss.build_stage_summary(db_session, tender.id, offers)
        row = next(r for r in summary["rows"] if r["code"] == parent_code)
        assert row["drilldown_group_count"] == 1


class TestEndpointContract:
    def _drill(self, db, grid, code="6", offers=None):
        return crud_pd.build_position_drilldown(
            db, grid.tender.id, category_id(db, code), offers or grid.path)

    def test_response_shape_and_cell_row_invariant(self, db_session, drill_grid):
        data = self._drill(db_session, drill_grid)
        assert data["work_category"]["code"] == "6" and data["reason"] is None
        assert [c["stage_no"] for c in data["columns"]] == [1, 2, 4]
        assert data["display"]["tax_basis"] == "gross" and data["display"]["reason"] == "single_rate"
        for row in data["rows"]:
            assert len(row["cells"]) == len(data["columns"])
            for cell in row["cells"]:
                assert (cell["estimate_rows"] == 0) == (cell["state"] == "absent")

    def test_convergence_matches_the_summary_row_number(self, db_session, drill_grid):
        """§2.13: article_amount == числу строки свода, shown_sum == article_amount."""
        summary = crud_ss.build_stage_summary(db_session, drill_grid.tender.id, drill_grid.path)
        summary_row = next(r for r in summary["rows"] if r["code"] == "6")
        data = self._drill(db_session, drill_grid)
        for idx, conv in enumerate(data["convergence"]):
            assert conv["converged"] is True
            expected = summary_row["cells"][idx]["amount"] or "0.00"
            assert conv["article_amount"] == expected == conv["shown_sum"]

    def test_extras_money_is_inside_the_rows_or_totals_do_not_converge(self, db_session, drill_grid):
        """§1.5: сумма статьи в своде = позиции ПЛЮС допработы; в grid у статьи 6
        на этапе 2 допработы 24.00 — без их строки сходимость упала бы."""
        data = self._drill(db_session, drill_grid)
        kinds = {r["kind"] for r in data["rows"]}
        assert "additional_works" in kinds
        assert all(c["converged"] is True for c in data["convergence"])

    def test_non_finite_extra_row_does_not_crash_the_endpoint(self, db_session, factories):
        """Регресс на найденный дефект: до фикса неконечная сумма допработ
        доходила необёрнутой до `article_delta` в `services/position_drilldown
        .py`. `_row_from_group` считает `contribution.value` ТОЛЬКО по ПЕРВОЙ
        и ПОСЛЕДНЕЙ показанным колонкам (`money_at(last) - money_at(first)`) —
        поэтому воспроизвести падение НЕ получилось бы на `drill_grid`, где
        допработы статьи «6» живут лишь на СРЕДНЕМ этапе трассы: NaN там не
        касается ни первой, ни последней колонки, `article_delta` остаётся
        конечным, а расхождение видно только как `converged: false` (не как
        исключение) — разные дефекты, и smoke-тест обязан бить именно по
        тому пути, что уронил прод: `_extras_only_grid` даёт статью «2» с
        ЕДИНСТВЕННОЙ группой (допработы), присутствующей на ОБОИХ этапах —
        первом и последнем разом, — так что NaN на первом этапе гарантированно
        входит в `contribution.value`, а значит и в `article_delta`, и цикл
        отбора `abs(article_delta - cumulative) > floor` получает NaN по ОБЕ
        стороны `>`: `Decimal('NaN') > Decimal('NaN')` поднимает
        `decimal.InvalidOperation` в питоновском `decimal` (проверено
        отдельно, см. отчёт задачи) — ровно тот путь, что бил прод."""
        tender, offers = _extras_only_grid(db_session, factories)
        estimates = estimates_of(db_session, offers)
        db_session.execute(
            sa.update(EstimateAdditionalWork)
            .where(EstimateAdditionalWork.proposal_id.in_(
                sa.select(Proposal.id).join(Lot, Lot.id == Proposal.lot_id)
                .where(Lot.estimate_id == estimates[0])))
            .values(total_amount=D("NaN")))
        db_session.flush()
        # Не должно бросить decimal.InvalidOperation.
        data = crud_pd.build_position_drilldown(db_session, tender.id, category_id(db_session, "2"), offers)
        assert data["reason"] is None
        assert all(c["converged"] is True for c in data["convergence"])

    def test_unknown_work_category_is_404(self, db_session, drill_grid):
        with pytest.raises(DomainError) as e:
            crud_pd.build_position_drilldown(db_session, drill_grid.tender.id, 10**9, drill_grid.path)
        assert e.value.status_code == 404 and e.value.code == crud_pd.CODE_WC_NOT_FOUND

    def test_selection_refusals_are_the_summary_codes(self, db_session, drill_grid):
        with pytest.raises(DomainError) as e:
            self._drill(db_session, drill_grid, offers=[drill_grid.path[0]])
        assert e.value.code == "too_few_offers"

    def test_empty_subtree_is_no_rows_in_subtree_not_an_error(self, db_session, drill_grid):
        """Пустота проверяется ПО ОБЕИМ ветвям и ПО ВСЕМУ поддереву выбранных
        смет, а не «по категориям, не встречающимся в position_items»: та
        формулировка выбрала бы и корень со строками у потомка (живой узел «1»),
        и статью с одними допработами — то есть тест мерил бы не то, что
        обещает (ревью плана 31.08.2026). Предпосылку тест доказывает сам."""
        estimates = estimates_of(db_session, drill_grid.path)
        # В grid заняты статьи «6» и «2»; «3» не заняты ни работой, ни допработой.
        subtree = crud_pd.subtree_ids(db_session, category_id(db_session, "3"))
        chapter = sa.orm.aliased(PositionItem)
        positions_in_subtree = db_session.execute(
            sa.select(sa.func.count()).select_from(PositionItem)
            .join(chapter, sa.and_(chapter.id == PositionItem.chapter_item_id,
                                   chapter.proposal_id == PositionItem.proposal_id))
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id.in_(estimates), PositionItem.is_chapter.is_(False),
                   chapter.work_category_id.in_(subtree))).scalar_one()
        extras_in_subtree = db_session.execute(
            sa.select(sa.func.count()).select_from(EstimateAdditionalWork)
            .join(Proposal, Proposal.id == EstimateAdditionalWork.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id.in_(estimates),
                   EstimateAdditionalWork.work_category_id.in_(subtree))).scalar_one()
        assert positions_in_subtree == 0 and extras_in_subtree == 0   # предпосылка теста
        data = self._drill(db_session, drill_grid, code="3")
        assert data["reason"] == "no_rows_in_subtree" and data["rows"] == []
        assert data["convergence"] == []

    def test_subtree_of_only_additional_works_gets_a_drilldown(self, db_session, factories):
        """Негативная §6.2 и фикстура §5: статья, в поддереве которой ТОЛЬКО
        допработы (на стенде таких поддеревьев нет — 0 из 143), разложение
        получает; проверка одних `position_items` обязана здесь краснеть."""
        tender, offers = _extras_only_grid(db_session, factories)
        data = crud_pd.build_position_drilldown(db_session, tender.id,
                                                category_id(db_session, "2"), offers)
        assert data["reason"] is None
        assert [r["kind"] for r in data["rows"] if r["group_count"] is None] == ["additional_works"]
        assert all(c["converged"] is True for c in data["convergence"])

    def test_article_with_only_descendant_rows_gets_a_drilldown(self, db_session, factories):
        """Негативная §6.2: узел без собственных строк, но со строками потомка,
        no_rows_in_subtree НЕ получает и отдаёт непустое разложение (живой
        случай стенда: 18 и 22 таких узла по трассам, §1.7)."""
        tender, offers = _subtree_only_grid(db_session, factories)
        data = crud_pd.build_position_drilldown(db_session, tender.id,
                                                category_id(db_session, "6"), offers)
        assert data["reason"] is None and len(data["rows"]) >= 1
        assert all(c["converged"] is True for c in data["convergence"])

    def test_query_count_does_not_grow_with_columns(self, db_session, drill_grid):
        wc = category_id(db_session, "6")
        with count_queries(db_session) as c2:
            crud_pd.build_position_drilldown(db_session, drill_grid.tender.id, wc, drill_grid.path[:2])
        with count_queries(db_session) as c3:
            crud_pd.build_position_drilldown(db_session, drill_grid.tender.id, wc, drill_grid.path)
        assert c2["n"] == c3["n"]

    def test_quantity_is_serialized_raw_not_formatted(self, db_session, drill_grid):
        """§2.11: `quantity` — СЫРОЕ значение `suggested_quantity`, как оно лежит
        в базе (пример спеки: `"8726.397168"`), а не разряды/один знак после
        запятой (§2.4) — то форматирование клиентское (Task 9). `chaptered()`
        задаёт `suggested_quantity=1` во всех раундах grid — раз объём не
        менялся, `str(Decimal("1"))` обязано остаться голым «1», а не «1.0»
        или «1,0»."""
        data = self._drill(db_session, drill_grid)
        work_row = next(r for r in data["rows"] if r["kind"] == pd_service.KIND_POSITION)
        assert [c["quantity"] for c in work_row["cells"]] == ["1", "1", "1"]

    def test_quantity_join_and_null_at_response_level(self, db_session, factories):
        """§2.11, вторая половина правила `quantity` — не покрыта соседним
        тестом (тот проверяет только «сырое против отформатированного» на
        ОДНОМ значении). Здесь — два случая, оба живут в одной строке
        `_cell_json`: несколько `suggested_quantity` на этапе join'ятся `+`
        БЕЗ пробелов и в отсортированном порядке (разряды и пробел вокруг
        `+` — клиентское форматирование, Task 9); у строки без объёма вовсе
        (допработы) `quantity` — JSON `null`, а не пустая строка.

        Обе группы заведены ТОЛЬКО в первом раунде (во втором — пустая глава
        без работ и без допработ), поэтому обе — появившиеся/исчезнувшие
        (класс 2 §2.3) и остаются показаны РАЗДЕЛЬНО, а не тонут в «прочих»
        (тот самый урок из блока Task 4, задача 5)."""
        tender = factories.TenderFactory.create()
        r1 = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
        r2 = factories.TenderRoundFactory.create(tender=tender, stage_no=2)
        db_session.flush()
        positions_r1 = [
            position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                     article_smr="6", number="1"),
            # ДВЕ строки одного наименования и единицы — матчинг сведёт их в
            # одну группу с двумя РАЗНЫМИ suggested_quantity на этом этапе.
            position(job_title="Работа", unit="м2", quantity=1, suggested_quantity=11,
                     unit_cost_total="7.00", total_cost_total="7.00", chapter_ref="1", number="2"),
            position(job_title="Работа", unit="м2", quantity=1, suggested_quantity=5,
                     unit_cost_total="5.00", total_cost_total="5.00", chapter_ref="1", number="3"),
        ]
        import_and_match(db_session, tender_round=r1, data=round_payload([proposal(
            positions_r1, vat_rate="20",
            additional_works=additional_works_row(total="9.00"),
            additional_info=svedeniya_info("1 Допработы - 9.00 руб."))]))
        positions_r2 = [position(job_title="Раздел 1", is_chapter=True, chapter_number="1",
                                 article_smr="6", number="1")]   # пустая глава — работа и допработы исчезли
        import_and_match(db_session, tender_round=r2, data=round_payload([proposal(positions_r2, vat_rate="20")]))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
        data = crud_pd.build_position_drilldown(db_session, tender.id, category_id(db_session, "6"), offers)

        work_row = next(r for r in data["rows"] if r["kind"] == pd_service.KIND_POSITION)
        assert work_row["cells"][0]["quantity"] == "5+11"    # отсортировано, без пробелов
        assert work_row["cells"][1]["quantity"] is None      # исчезла — absent, объёма нет

        extra_row = next(r for r in data["rows"] if r["kind"] == pd_service.KIND_ADDITIONAL_WORKS)
        assert extra_row["cells"][0]["quantity"] is None     # у допработ объёма нет вовсе — null, не ""
        assert all(c["converged"] is True for c in data["convergence"])

    def test_http_route_returns_the_same_payload(self, client, db_session, drill_grid):
        wc = category_id(db_session, "6")
        offers = "&".join(f"offers={o}" for o in drill_grid.path)
        response = client.get(f"/api/v1/tenders/{drill_grid.tender.id}/stage-summary/{wc}?{offers}")
        assert response.status_code == 200
        assert response.json()["work_category"]["code"] == "6"


class TestTaxAxis:
    """§2.8/§6.2 на настоящем импорте: то, что Task 3 доказал литералами —
    ось `net` при расходящихся ставках и отказ/точечная разметка при
    неизвестной базе — доказать на `import_and_match`, а не на `GroupInput`
    руками.

    `vat_rate=None` в `chaptered(...)` доходит до `Proposal.vat_rate` через
    `services.estimate_import._vat_rate`, который отдаёт `None` НАПРЯМУЮ на
    входном `None` (без подстановки ставки по умолчанию) — тот же путь,
    что и форма payload парсера ≤3.0.0. Значит `TestVatAxis` соседнего файла
    здесь не нужна: не пришлось стирать `Proposal.vat_rate` UPDATE-ом, ставка
    неизвестна уже на входе, ровно как просит бриф."""

    def _tender(self, db_session, factories, rates):
        tender = factories.TenderFactory.create()
        rounds = {n: factories.TenderRoundFactory.create(tender=tender, stage_no=n)
                  for n in range(1, len(rates) + 1)}
        db_session.flush()
        for n, rate in enumerate(rates, start=1):
            import_and_match(db_session, tender_round=rounds[n],
                             data=round_payload([chaptered({"1": ("6", "120.00")}, vat_rate=rate,
                                                           total="120.00")]))
        db_session.flush()
        offers = db_session.execute(
            sa.select(Offer.id).join(TenderRound, TenderRound.id == Offer.round_id)
            .where(Offer.tender_id == tender.id).order_by(TenderRound.stage_no)).scalars().all()
        return tender, offers

    def test_mixed_rates_recompute_sums_to_net_and_still_converge(self, db_session, factories):
        """Единственная группа статьи «6» несёт РАЗНЫЕ ставки (20 % и 22 %) при
        ОДИНАКОВОЙ валовой сумме (120.00) на обоих этапах.

        Поправка к брифу: несмотря на равную валовую сумму, вклад строки
        считается в ПОКАЗАННЫХ (нетто) деньгах (`money_at` берёт `cell.shown`,
        `services/position_drilldown.py`), а 120/1.20 != 120/1.22 — движение
        статьи НЕНУЛЕВОЕ, единственная группа становится объяснителем
        (`compute_drilldown`, покрытие 90% достигается первой же строкой) и
        остаётся ОБЫЧНОЙ строкой `position`, а не сворачивается в `rest`.
        Ряд из брифа («когда движение статьи ноль, всё уходит в rest») здесь
        не воспроизводится буквально — она справедлива для ЕДИНОЙ ставки на
        обоих этапах, не для разных ставок с равной валовой суммой; поэтому
        индекс `rows[0]` действительно указывает на строку работы, а не на
        свёрнутый остаток. Проверено запуском (см. отчёт задачи)."""
        tender, offers = self._tender(db_session, factories, ["20", "22"])
        data = crud_pd.build_position_drilldown(db_session, tender.id,
                                                category_id(db_session, "6"), offers)
        assert data["display"]["tax_basis"] == "net"
        assert data["rows"][0]["kind"] == pd_service.KIND_POSITION      # не свёрнутый остаток
        assert data["rows"][0]["cells"][0]["amount"] == "100.00"        # 120 / 1.20
        assert all(c["converged"] is True for c in data["convergence"])

    def test_unknown_base_at_the_endpoint_returns_empty_rows_with_reason(self, db_session, factories):
        tender, offers = self._tender(db_session, factories, [None, "20"])
        data = crud_pd.build_position_drilldown(db_session, tender.id,
                                                category_id(db_session, "6"), offers)
        assert data["reason"] == "unknown_vat_base" and data["rows"] == [] and data["convergence"] == []

    def test_unknown_base_in_the_middle_marks_only_its_column(self, db_session, factories):
        tender, offers = self._tender(db_session, factories, ["20", None, "20"])
        data = crud_pd.build_position_drilldown(db_session, tender.id,
                                                category_id(db_session, "6"), offers)
        assert data["reason"] is None
        middle = data["convergence"][1]
        assert middle["converged"] is None and middle["reason"] == "unknown_vat_base"
        assert data["rows"][0]["cells"][1]["amount_unavailable_reason"] == "unknown_vat_base"
