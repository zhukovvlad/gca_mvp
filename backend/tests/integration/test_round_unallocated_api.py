"""Этапный разнос: вход агрегатора из БД, GET, счётчик карточки
(спека 2026-09-01-round-unallocated-design.md §2.2, §2.3, §2.5, §2.6, §4.1, §4.3)."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from crud import round_unallocated as crud_ru
from crud.common import DomainError
from models import PositionItem, WorkCategory
from services import round_unallocated as ru
from services.additional_works import parse_lines
from services.category_override import set_override
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.payloads import additional_works_row, position, proposal, round_payload, svedeniya_info

pytestmark = pytest.mark.integration


def cat(db, code):
    return db.execute(sa.select(WorkCategory.id).where(WorkCategory.code == code)).scalar_one()


def _single_participant_scene(db_session, factories, participant):
    """Тендер с одним раундом и одним участником — машинерия, общая для
    `_scene_with_lines`/`_scene_with_positions` (Задача 3): тот же настоящий
    `import_round`, что у `round_scene`, только с одной проекцией на сцену —
    этим двум тестам структура остальных участников/раунда не нужна."""
    tender = factories.TenderFactory.create()
    r1 = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
    db_session.flush()
    import_round(
        db_session, tender_round=r1, data=round_payload([participant]),
        parser_version="4.0.0", import_job_id=None, replace=False,
        unit_resolver=UnitResolver(db_session), category_resolver=CategoryResolver.from_db(db_session),
    )
    db_session.flush()
    return SimpleNamespace(tender=tender, r1=r1)


def _scene_with_lines(db_session, factories, lines, *, positions=()):
    """Сцена с текстом «Сведений» из `lines` — для `unresolved_chapter_ref`
    (§2.5). `positions` по умолчанию пуста (структура тривиальна, у КАЖДОЙ
    ссылки «нет кандидатов»); передайте раздел со статьёй из файла, чтобы
    ссылка на него резолвилась в НАСТОЯЩУЮ статью (негатив §2.5: разнос такую
    ссылку уже закрыл, диагностика её не называет). Сумма агрегатной строки —
    сумма разобранных строк плюс остаток 6.00 БЕЗ ссылки (та же граница §5.5,
    что у `round_scene`): гарантирует, что у сцены всегда есть и разобранная
    строка, и безссылочный остаток, независимо от того, что передал вызывающий."""
    parsed, _unreadable = parse_lines("\n".join(lines))
    parsed_sum = sum((p.amount for p in parsed), Decimal("0"))
    total = parsed_sum + Decimal("6.00")
    participant = proposal(
        list(positions), additional_works=additional_works_row(total=str(total)),
        additional_info=svedeniya_info(*lines),
    )
    return _single_participant_scene(db_session, factories, participant)


def _scene_with_positions(db_session, factories, positions, *, additional_works=None, additional_info=None):
    """Сцена с заданной ведомостью позиций — для `outside_structure`/
    `structure_disabled` (§2.5). `additional_works`/`additional_info`
    опциональны: без них сцена несёт только структурную границу; с ними —
    структурную границу вместе с допработами (комбинация A/C/D задачи 3)."""
    participant = proposal(positions, additional_works=additional_works, additional_info=additional_info)
    return _single_participant_scene(db_session, factories, participant)


def states_by_number(db, scene):
    scope = crud_ru.load_scope(db, scene.tender.id, scene.r1.id)
    return {a.node.number: a for a in crud_ru.load_states(db, scope)}


class TestStates:
    def test_input_set_excludes_file_classified_untouched_chapters(self, db_session, round_scene):
        by = states_by_number(db_session, round_scene)
        assert set(by) == {"14", "14.1", "14.3", "15"}          # «1» со статьёй «6» — вне множества
        assert all(a.classification.state == ru.STATE_UNASSIGNED for a in by.values())
        # «1.1» — подраздел «1» БЕЗ своей клетки «Статья СМР» и без единой
        # позиции: она НАСЛЕДУЕТ «6» от «1» (правило Ф3), и поэтому у неё есть
        # эффективная статья, хотя своего утверждения в файле нет вовсе.
        # Предикат входного множества обязан читать РЕЗУЛЬТАТ наследования
        # (`work_category_id`), а не сырую клетку файла (`smr_article_raw`) —
        # второе включило бы «1.1» ошибочно (нет своей `smr_article_raw`, но
        # статья ЕСТЬ). Негативный к «входное множество = разделы без своего
        # утверждения файла».
        assert "1.1" not in by

    def test_rows_is_the_full_file_subtree(self, db_session, round_scene):
        """`rows` — ПОЛНЫЙ размер файлового поддерева, а не число РАСЦЕНЁННЫХ
        строк в нём (`rows_priced`, спека §2.3 прямо запрещает её здесь:
        расценённость по-участниковая, а панель без денег). «15» несёт ДВЕ
        позиции в файле, из них расценена только ОДНА («Работа 11 без цены» —
        `total_cost_total IS NULL`), и `rows` обязан отдать 2, а не 1: без
        такой строки `rows` и `rows_priced` совпали бы на всех четырёх
        разделах и не отличили бы верную реализацию от подмены."""
        by = states_by_number(db_session, round_scene)
        assert {n: a.node.rows for n, a in by.items()} == {"14": 3, "14.1": 2, "14.3": 1, "15": 2}
        chapter15_id = round_scene.chapter(round_scene.estimates[0].id, "15").id
        priced = db_session.execute(
            sa.select(sa.func.count()).select_from(PositionItem)
            .where(
                PositionItem.chapter_item_id == chapter15_id,
                PositionItem.total_cost_total.is_not(None),
            )
        ).scalar_one()
        assert priced == 1                                   # ровно одна из двух строк «15» расценена

    def test_rows_stays_the_full_file_subtree_after_a_full_decision(self, db_session, round_scene, admin_user):
        """§4.1: `rows` — свойство ФАЙЛА, не зависящее от того, что уже решено
        внутри поддерева. На этой ведомости ДО решения три метрики совпадают
        численно (3/2/1/1 для 14/14.1/14.3/15): полный размер файлового
        поддерева (правильная метрика), число строк, ОСТАЮЩИХСЯ
        нераспределёнными внутри поддерева (метрика паспорта
        `_unallocated_sections`, спека запрещает её здесь §4.1), и
        `rows_priced`. Поэтому `test_rows_is_the_full_file_subtree` в одиночку
        не отличает верную реализацию от реализации, вернувшей любую из двух
        неверных метрик — обе дали бы те же числа.

        Полное решение по «14» во всех трёх сметах гонит метрику паспорта в
        НОЛЬ (внутри поддерева не осталось ни одной нераспределённой строки),
        а размер файлового поддерева не меняется вовсе — это и есть случай,
        который убивает реализацию, подставившую любую из двух неверных
        метрик.

        Побочный эффект, который стоит закрепить тем же тестом: как только
        «14» решена во всех сметах, пересчёт материализует унаследованную
        статью на «14.1»/«14.3» (у них нет своей `smr_article_raw` — Ф3
        разрешает наследование), их собственная эффективная статья перестаёт
        отсутствовать, и, не имея СВОЕГО override, они целиком покидают
        входное множество.
        """
        for e in round_scene.estimates:
            set_override(db_session, estimate_id=e.id, position_item_id=round_scene.chapter(e.id, "14").id,
                         work_category_id=cat(db_session, "20"), note=None, user_id=admin_user.id)
        by = states_by_number(db_session, round_scene)
        assert set(by) == {"14", "15"}                     # «14.1»/«14.3» унаследовали и покинули множество
        assert by["14"].node.rows == 3                      # файловый размер поддерева не изменился
        assert by["14"].classification.state == ru.STATE_RESOLVED

    def test_parents_are_rehung_and_order_is_the_file_order(self, db_session, round_scene):
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r1.id)
        out = crud_ru.load_states(db_session, scope)
        assert [a.node.number for a in out] == ["14", "14.1", "14.3", "15"]
        assert out[1].parent_key == round_scene.key14 and out[0].parent_key is None
        assert [a.depth for a in out] == [0, 1, 1, 0]

    def test_partial_after_a_by_estimate_decision_on_one_estimate(self, db_session, round_scene, admin_user):
        """Частичное состояние создаётся ШТАТНЫМ по-сметным маршрутом — он
        остаётся открытым паспорту, и именно он даёт смешанные состояния."""
        e0 = round_scene.estimates[0]
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "14").id,
                     work_category_id=cat(db_session, "20"), note="первая", user_id=admin_user.id)
        c = states_by_number(db_session, round_scene)["14"].classification
        assert (c.state, c.assigned, c.total, c.notes) == (ru.STATE_PARTIAL, 1, 3, ("первая",))

    def test_file_classified_chapter_with_partial_override_enters_the_set(self, db_session, round_scene, admin_user):
        e0 = round_scene.estimates[0]
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "1").id,
                     work_category_id=cat(db_session, "20"), note=None, user_id=admin_user.id)
        assert states_by_number(db_session, round_scene)["1"].classification.state == ru.STATE_PARTIAL

    def test_conflict_by_category_and_resolved_when_identical(self, db_session, round_scene, admin_user):
        codes = ["20", "20", "16"]
        for e, code in zip(round_scene.estimates, codes, strict=True):
            set_override(db_session, estimate_id=e.id, position_item_id=round_scene.chapter(e.id, "14").id,
                         work_category_id=cat(db_session, code), note=None, user_id=admin_user.id)
        c = states_by_number(db_session, round_scene)["14"].classification
        assert c.state == ru.STATE_CONFLICT
        assert c.categories == (cat(db_session, "20"), cat(db_session, "16"))
        # Выравнивание третьей сметы → resolved (в одной транзакции now() один
        # на всех, автор один — вектор единый).
        e2 = round_scene.estimates[2]
        set_override(db_session, estimate_id=e2.id, position_item_id=round_scene.chapter(e2.id, "14").id,
                     work_category_id=cat(db_session, "20"), note=None, user_id=admin_user.id)
        assert states_by_number(db_session, round_scene)["14"].classification.state == ru.STATE_RESOLVED

    def test_conflict_by_audit_only(self, db_session, round_scene, admin_user):
        """Статья и заметка едины, различается ТОЛЬКО `assigned_at` — сдвинут
        явным UPDATE (в одной транзакции now() один, иначе различия не создать)."""
        for e in round_scene.estimates:
            set_override(db_session, estimate_id=e.id, position_item_id=round_scene.chapter(e.id, "14").id,
                         work_category_id=cat(db_session, "20"), note=None, user_id=admin_user.id)
        victim = round_scene.chapter(round_scene.estimates[1].id, "14").id
        db_session.execute(sa.text("UPDATE estimate_category_overrides SET assigned_at = now() - interval '1 day' "
                                   "WHERE position_item_id = :rid"), {"rid": victim})
        c = states_by_number(db_session, round_scene)["14"].classification
        assert c.state == ru.STATE_CONFLICT and c.audit_differs is True and len(c.categories) == 1

    def test_vectors_align_positionally_with_estimate_id_order(self, db_session, round_scene, admin_user):
        """`SectionAggregate.vectors` — один слот на смету радиуса, порядок
        `estimate_id ASC` (тот же порядок, что несёт `RoundScope.estimates`).
        `classify` порядок не различает, а построение вектора идёт словарём
        (`vectors[k].get(eid)`) — переход на порядок ИТЕРАЦИИ словаря вместо
        порядка `ids` не покраснил бы ни один существующий тест. Здесь порядок
        фиксируется НАПРЯМУЮ: первая и третья сметы получают РАЗНЫЕ решения,
        вторая — никакого, и слот `None` обязан стоять СРЕДНИМ.

        `RoundScope.contractor_title` индексирован id сметы, а не позицией в
        списке — любой будущий потребитель, называющий участника по вектору,
        обязан брать id из `scope.estimates[i].id`, а не полагаться на то, что
        порядок решений совпал с порядком id по счастливой случайности; этот
        тест и есть то, что держит позицию и id в шаге друг с другом."""
        e0, e1, e2 = round_scene.estimates
        cat20, cat16 = cat(db_session, "20"), cat(db_session, "16")
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "14").id,
                     work_category_id=cat20, note=None, user_id=admin_user.id)
        set_override(db_session, estimate_id=e2.id, position_item_id=round_scene.chapter(e2.id, "14").id,
                     work_category_id=cat16, note=None, user_id=admin_user.id)
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r1.id)
        agg = next(a for a in crud_ru.load_states(db_session, scope) if a.node.number == "14")
        assert agg.vectors[1] is None
        assert agg.vectors[0] is not None and agg.vectors[0].work_category_id == cat20
        assert agg.vectors[2] is not None and agg.vectors[2].work_category_id == cat16

    def test_baseline_is_outside_the_radius(self, db_session, round_scene):
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r1.id)
        assert [e.id for e in scope.estimates] == round_scene.estimate_ids
        assert round_scene.baseline_id not in round_scene.estimate_ids

    def test_offer_estimates_is_scoped_to_the_round_by_the_offer_join(self, db_session, round_scene):
        """§2.1: радиус — offer-сметы ИМЕННО ЭТОГО раунда, соединением с
        `offers` (`Offer.round_id`), а не «все сметы тендера, отфильтрованные
        в питоне по `offer_id IS NOT NULL`» — та реализация дала бы тот же
        видимый исход «baseline вне радиуса», но затянула бы в радиус раунда 1
        offer-смету ЧУЖОГО раунда. Раунд 2 несёт СВОЮ offer-смету — это и есть
        поведенческое различие, которое ловит именно эту подмену."""
        round1_ids = [e.id for e in crud_ru.offer_estimates(db_session, round_scene.r1.id)]
        assert round1_ids == round_scene.estimate_ids
        round2_ids = [e.id for e in crud_ru.offer_estimates(db_session, round_scene.r2.id)]
        assert len(round2_ids) == 1
        assert round2_ids[0] not in round1_ids
        scope2 = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r2.id)
        assert [e.id for e in scope2.estimates] == round2_ids

    def test_key_missing_in_one_projection_is_mapping_broken(self, db_session, round_scene):
        """Ключ представительной сметы отсутствует в другой (§4.1): раздел «15»
        удаляется у второй сметы в обход домена (сначала его позиция — RESTRICT
        `fk_position_items_chapter`)."""
        e1 = round_scene.estimates[1]
        ch = round_scene.chapter(e1.id, "15")
        db_session.execute(sa.text("DELETE FROM position_items WHERE chapter_item_id = :cid"), {"cid": ch.id})
        db_session.execute(sa.text("DELETE FROM position_items WHERE id = :cid"), {"cid": ch.id})
        db_session.flush()
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r1.id)
        with pytest.raises(crud_ru.RoundMappingBroken):
            crud_ru.load_states(db_session, scope)

    def test_is_chapter_flip_on_a_non_representative_estimate_is_mapping_broken(self, db_session, round_scene):
        """§2.2: `is_chapter`, различающийся между проекциями, — тоже
        `mapping_broken`, ТОЙ ЖЕ проверкой набора ключей, без отдельной ветки:
        строка, переставшая быть разделом в одной из смет, просто исчезает из
        её набора `is_chapter=True`-ключей, и множества расходятся с
        представительной сметой ровно как при отсутствующем ключе. Проверяется
        одно направление флипа — довольно, чтобы закрепить правило; остальные
        держатся тем же кодом (см. докстринг `load_states`)."""
        e1 = round_scene.estimates[1]
        ch15 = round_scene.chapter(e1.id, "15")
        db_session.execute(sa.text("UPDATE position_items SET is_chapter = false WHERE id = :cid"), {"cid": ch15.id})
        db_session.flush()
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r1.id)
        with pytest.raises(crud_ru.RoundMappingBroken):
            crud_ru.load_states(db_session, scope)

    def test_round_without_offer_estimates_is_404_with_its_code(self, db_session, factories):
        tender = factories.TenderFactory.create()
        rnd = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
        db_session.flush()
        with pytest.raises(DomainError) as e:
            crud_ru.load_scope(db_session, tender.id, rnd.id)
        assert (e.value.status_code, e.value.code) == (404, crud_ru.CODE_NO_OFFER_ESTIMATES)
        assert e.value.detail == crud_ru.NO_OFFER_ESTIMATES_MESSAGE

    def test_round_of_another_tender_is_404_round_not_found(self, db_session, round_scene, factories):
        other = factories.TenderFactory.create()
        db_session.flush()
        with pytest.raises(DomainError) as e:
            crud_ru.require_round(db_session, other.id, round_scene.r1.id)
        assert e.value.code == crud_ru.CODE_ROUND_NOT_FOUND
        with pytest.raises(DomainError) as e:
            crud_ru.require_round(db_session, other.id + 10_000, round_scene.r1.id)
        assert e.value.code == crud_ru.CODE_TENDER_NOT_FOUND


class TestDiagnostics:
    """Три границы §5.5 спеки разноса, поимённо (спека этапного разноса §2.5):
    свой селектор, тот же план резолва, что у пересчёта `apply_overrides`."""

    def test_remainder_without_ref_is_unresolved_and_resolvable_ref_is_not(self, db_session, round_scene):
        """Остаток 6.00 без ссылки — граница §5.5; строка «14 Отделка» ссылается на
        раздел без статьи (кандидат без статьи) — разнос её закроет, в диагностике
        её НЕТ (негативный, §2.5)."""
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r1.id)
        diags = crud_ru.diagnostics(db_session, scope)
        assert [d["code"] for d in diags] == [crud_ru.DIAG_UNRESOLVED_REF] * 3      # по одной на участника
        assert {d["title"] for d in diags} == {"Дополнительные работы"}
        assert {d["contractor_title"] for d in diags} == {"ООО А", "ООО Б", "ООО В"}
        assert all(d["rows"] == 1 for d in diags)

    def test_ref_resolving_to_a_real_category_is_not_a_boundary_but_unknown_ref_and_remainder_are(
        self, db_session, factories
    ):
        """F (ревью после первого прохода задачи): сцена несёт раздел «5» с
        НАСТОЯЩЕЙ статьёй из файла — ссылка на него резолвится в статью и
        разносом уже закрыта, в диагностике её быть не должно (иначе половина
        предиката `category_id is None and ...` осталась бы непроверенной).
        Ссылка на неизвестный раздел «77» и безссылочный остаток — границы,
        обе видны в ЭТОЙ ЖЕ сцене, титулами разных причин."""
        scene = _scene_with_lines(
            db_session, factories,
            ["5 Кровля - 10.00 руб.", "77 Подвал - 24.00 руб."],
            positions=[
                position(job_title="Раздел 5", is_chapter=True, chapter_number="5", article_smr="6", number="1"),
            ],
        )
        diags = crud_ru.diagnostics(db_session, crud_ru.load_scope(db_session, scene.tender.id, scene.r1.id))
        assert all(d["code"] == crud_ru.DIAG_UNRESOLVED_REF for d in diags)
        titles = [d["title"] for d in diags]
        assert titles == ["Подвал", "Дополнительные работы"]      # ordinal 2, затем остаток (ordinal 3)
        assert "Кровля" not in titles                              # резолвится в статью «6» — не граница

    def test_stale_materialized_category_on_an_unresolved_reference_is_still_reported(
        self, db_session, factories
    ):
        """Мутационный тест (ревью после второго прохода задачи, «мутант 2»):
        `work_category_id` строки «Подвал» (ссылка «77», кандидатов НЕТ —
        `resolve_ref` никогда не назначил бы ей статью) проставлен НАПРЯМУЮ
        SQL, в обход домена — согласованные данные такую комбинацию никогда
        не производят: `apply_overrides`/`build_rows` не назначают статью
        строке, чья ссылка не резолвится, поэтому это состояние ДОСТИГАЕТСЯ
        только руками, как и `test_key_missing_in_one_projection_is_mapping_
        broken` реалит недостижимую расходящуюся проекцию тем же приёмом.
        Тест существует не для описания достижимых данных, а чтобы держать
        честной РЕКОМПУТАЦИЮ: `ck_estimate_additional_works_unresolved_ref`
        разрешает `work_category_id` только при непустой `chapter_ref_raw`,
        поэтому взята именно ссылочная строка, а не безссылочный остаток
        (тот пуст по `chapter_ref_raw` — CHECK запретил бы). `expire_all()`
        обязателен: без него ORM отдала бы уже загруженный Python-объект из
        identity map со СТАРЫМ (`None`) значением колонки, и сырой UPDATE
        оказался бы невидим последующему `sa.select(EstimateAdditionalWork)`
        внутри `diagnostics` — тест стал бы зелёным независимо от того, снят
        ли быстрый выход по `work_category_id is not None` (задача B)."""
        scene = _scene_with_lines(db_session, factories, ["77 Подвал - 24.00 руб."])
        db_session.execute(
            sa.text(
                "UPDATE estimate_additional_works SET work_category_id = :cat "
                "WHERE chapter_ref_raw = '77'"
            ),
            {"cat": cat(db_session, "6")},
        )
        db_session.expire_all()
        diags = crud_ru.diagnostics(db_session, crud_ru.load_scope(db_session, scene.tender.id, scene.r1.id))
        assert any(d["title"] == "Подвал" for d in diags)

    def test_ambiguous_reference_is_unresolved(self, db_session, factories):
        """G: два раздела с одним номером «9», статьи разные — `REASON_AMBIGUOUS`,
        единственная граница §5.5, не имевшая интеграционного покрытия. Тот же
        случай, что и `test_candidates_with_different_categories_stay_unassigned`
        в `test_additional_works.py`, только через настоящий импорт и `diagnostics`."""
        scene = _scene_with_lines(
            db_session, factories, ["9 Спорное - 5.00 руб."],
            positions=[
                position(job_title="Раздел 9а", is_chapter=True, chapter_number="9", article_smr="6", number="1"),
                position(job_title="Раздел 9б", is_chapter=True, chapter_number="9", article_smr="16", number="2"),
            ],
        )
        diags = crud_ru.diagnostics(db_session, crud_ru.load_scope(db_session, scene.tender.id, scene.r1.id))
        assert [d["title"] for d in diags] == ["Спорное", "Дополнительные работы"]
        assert all(d["code"] == crud_ru.DIAG_UNRESOLVED_REF for d in diags)

    def test_structure_disabled_is_one_row_per_proposal(self, db_session, round_scene):
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r2.id)
        diags = crud_ru.diagnostics(db_session, scope)
        assert [d["code"] for d in diags] == [crud_ru.DIAG_STRUCTURE_DISABLED]
        assert diags[0]["rows"] == 0 and diags[0]["contractor_title"] == "ООО А"
        assert diags[0]["title"] == "Лот №1 - Тестовый"            # H: title раньше не проверялся

    def test_disabled_structure_reports_structural_boundary_then_all_its_extras_in_ordinal_order(
        self, db_session, factories
    ):
        """A: разнос раздела при погашенной структуре невозможен в принципе —
        `resolve_ref` для допработ этого предложения не вызывается вовсе
        (docstring `diagnostics`): КАЖДАЯ непривязанная допработа — граница,
        независимо от того, что говорит номер в ссылке. `outside_structure`
        для этого же предложения не отдаётся — её `rows` задвоил бы счёт
        `structure_disabled.rows` (та же граница, тот же приём).

        Мутационный тест (ревью после второго прохода задачи, «мутант 1»):
        строка «Смежное» ссылается на раздел «14» — раздел, который в ЭТОЙ
        погашенной ведомости РЕАЛЬНО существует (не «прим.», а второй,
        цифровой раздел того же предложения; сослаться цифрой на САМ «прим.»
        нельзя — `LINE_RE` допускает в ссылке только цифры, а нечитаемый номер
        по определению не цифра). Под погашенной структурой
        `categories_by_chapter_number` отдаёт «14» только `{None}` (раздел
        есть, статьи нет ни у одного раздела погашенного предложения), поэтому
        `resolve_ref("14", ...)` вернул бы «кандидат без статьи» — версия,
        которая СРАВНИВАЕТ причину отказа на этой ветке (а не просто проверяет
        `work_category_id is None`), исключила бы «Смежное» по этой причине,
        хотя разнос раздела «14» здесь так же невозможен, как и разнос «прим.»
        (структура погашена целиком, а не частями). Остаточная строка
        («Дополнительные работы», без ссылки вовсе) осталась бы видна и на
        сломанной версии — поэтому одних безссылочных строк для этого мутанта
        недостаточно, нужна именно ссылка с исходом «кандидат без статьи».

        C: порядок пришпилен ЦЕЛИКОМ — структурная граница ПЕРЕД допработами,
        допработы по `ordinal` (не в развороте).
        D: `rows` структурной границы — РЕАЛЬНОЕ число НЕ-разделов (2), не
        случайный ноль фикстуры без позиций (раздел «14» — раздел, в счёт не
        входит)."""
        scene = _scene_with_positions(
            db_session, factories,
            [
                position(job_title="Примечание", number="1", chapter_number="прим.", is_chapter=True),
                position(job_title="Работа 1", unit="м2", quantity=1, suggested_quantity=1,
                         unit_cost_total="10", total_cost_total="10", chapter_ref="прим.", number="2"),
                position(job_title="Работа 2", unit="м2", quantity=1, suggested_quantity=1,
                         unit_cost_total="5", total_cost_total="5", chapter_ref="прим.", number="3"),
                position(job_title="Раздел 14", is_chapter=True, chapter_number="14", number="4"),
            ],
            additional_works=additional_works_row(total="40.00"),
            additional_info=svedeniya_info(
                "5 Первая - 10.00 руб.", "6 Вторая - 15.00 руб.", "14 Смежное - 8.00 руб.",
            ),
        )
        diags = crud_ru.diagnostics(db_session, crud_ru.load_scope(db_session, scene.tender.id, scene.r1.id))
        assert [(d["code"], d["title"]) for d in diags] == [
            (crud_ru.DIAG_STRUCTURE_DISABLED, "Лот №1 - Тестовый"),
            (crud_ru.DIAG_UNRESOLVED_REF, "Первая"),
            (crud_ru.DIAG_UNRESOLVED_REF, "Вторая"),
            (crud_ru.DIAG_UNRESOLVED_REF, "Смежное"),
            (crud_ru.DIAG_UNRESOLVED_REF, "Дополнительные работы"),
        ]
        assert diags[0]["rows"] == 2                                # позиции «2»/«3» — НЕ разделы, реальный счёт
        assert crud_ru.DIAG_OUTSIDE_STRUCTURE not in [d["code"] for d in diags]

    def test_position_outside_structure_is_one_row_per_proposal_with_a_count(self, db_session, factories):
        """Строка без номера и без раздела — `RowKind.OUTSIDE_STRUCTURE`
        (`category_resolution._row_kind`): number пустой И chapter_number пустой.
        E: обычная РАСЦЕНЁННАЯ позиция ПОД разделом тоже не раздел, но НЕ вне
        структуры — счётчик обязан отличать «вне структуры» от «любая
        не-раздельная строка»: без неё «все НЕ-разделы» (3) и «вне структуры»
        (2) совпали бы случайно на этой сцене и не различили бы реализации."""
        scene = _scene_with_positions(db_session, factories, [
            position(job_title="Раздел 1", is_chapter=True, chapter_number="1", article_smr="6", number="1"),
            position(job_title="Обычная работа", unit="м2", quantity=1, suggested_quantity=1,
                     unit_cost_total="5", total_cost_total="5", chapter_ref="1", number="2"),
            position(job_title="Сирота", unit="м2", quantity=1, suggested_quantity=1,
                     unit_cost_total="5", total_cost_total="5", number=""),
            position(job_title="Сирота 2", unit="м2", quantity=1, suggested_quantity=1,
                     unit_cost_total="5", total_cost_total="5", number=""),
        ])
        diags = crud_ru.diagnostics(db_session, crud_ru.load_scope(db_session, scene.tender.id, scene.r1.id))
        outside = [d for d in diags if d["code"] == crud_ru.DIAG_OUTSIDE_STRUCTURE]
        assert len(outside) == 1 and outside[0]["rows"] == 2
        assert outside[0]["title"] == "Лот №1 - Тестовый"           # H: title раньше не проверялся
