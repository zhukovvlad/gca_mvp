"""Раундовая запись (спека этапного разноса §2.4, §4.2) — уровень сервиса и HTTP."""
from __future__ import annotations

import logging

import pytest
import sqlalchemy as sa

from crud import round_unallocated as crud_ru
from crud.common import DomainError
from models import EstimateAdditionalWork, EstimateCategoryOverride, Lot, PositionItem, Proposal, WorkCategory
from services import round_category_override as rco
from services import round_unallocated as ru
from services.category_override import set_override

pytestmark = pytest.mark.integration


def cat(db, code):
    return db.execute(sa.select(WorkCategory.id).where(WorkCategory.code == code)).scalar_one()


def overrides_of(db, scene, number):
    return [db.get(EstimateCategoryOverride, scene.chapter(e.id, number).id) for e in scene.estimates]


def put14(db, scene, user, *, code="20", note="из плана"):
    return rco.set_round_override(db, tender_id=scene.tender.id, round_id=scene.r1.id,
                                  lot_key=scene.key14[0], position_key_in_proposal=scene.key14[1],
                                  work_category_id=cat(db, code), note=note, user_id=user.id)


def state_of(db, scene, number):
    scope = crud_ru.load_scope(db, scene.tender.id, scene.r1.id)
    return {a.node.number: a for a in crud_ru.load_states(db, scope)}[number].classification


def materialized(db, estimate_id):
    """Срез материализации одной сметы: разделы и допработы — для паритета с по-сметным сервисом."""
    chapters = db.execute(
        sa.select(PositionItem.chapter_number_in_proposal, PositionItem.work_category_id, PositionItem.category_source)
        .join(Proposal, Proposal.id == PositionItem.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id, PositionItem.is_chapter.is_(True))
        .order_by(sa.func.length(PositionItem.position_key_in_proposal), PositionItem.position_key_in_proposal)
    ).all()
    extras = db.execute(
        sa.select(EstimateAdditionalWork.chapter_ref_raw, EstimateAdditionalWork.work_category_id)
        .join(Proposal, Proposal.id == EstimateAdditionalWork.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id).order_by(EstimateAdditionalWork.ordinal)
    ).all()
    return [tuple(r) for r in chapters], [tuple(r) for r in extras]


class TestPut:
    def test_writes_one_decision_and_one_audit_into_every_offer_estimate(self, db_session, round_scene, admin_user):
        result = put14(db_session, round_scene, admin_user)
        rows = overrides_of(db_session, round_scene, "14")
        assert all(r is not None for r in rows)
        assert {(r.work_category_id, r.note, r.assigned_by, r.assigned_at) for r in rows} == {
            (cat(db_session, "20"), "из плана", admin_user.id, rows[0].assigned_at)}
        # Множество из ОДНОГО значения доказывает только «три сметы совпали
        # между собой» — трёх раздельных `now()` по одной на смету, случайно
        # совпавших благодаря транзакционному `now()` PostgreSQL, эта проверка
        # не отличила бы от одного `now()` транзакции (документированная
        # ловушка проекта). Сверка со `SELECT now()` ЭТОЙ ЖЕ транзакции пинит
        # именно ВТОРОЕ: что штамп — штамп транзакции, а не случайное согласие.
        txn_now = db_session.execute(sa.text("select now()")).scalar_one()
        assert rows[0].assigned_at == txn_now
        assert state_of(db_session, round_scene, "14").state == ru.STATE_RESOLVED
        # Подраздел «14» несёт три раздела своего поддерева («14», «14.1»,
        # «14.3») — «1.1» под «1» сюда не входит, это другое поддерево.
        assert result.chapters_updated == 3 * 3          # «14», «14.1», «14.3» × 3 сметы
        for e in round_scene.estimates:
            assert round_scene.chapter(e.id, "14.1").work_category_id == cat(db_session, "20")

    def test_baseline_is_not_touched_by_the_same_selection(self, db_session, round_scene, admin_user):
        put14(db_session, round_scene, admin_user)
        baseline_rows = db_session.execute(
            sa.select(sa.func.count()).select_from(EstimateCategoryOverride)
            .join(PositionItem, PositionItem.id == EstimateCategoryOverride.position_item_id)
            .join(Proposal, Proposal.id == PositionItem.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == round_scene.baseline_id)
        ).scalar_one()
        assert baseline_rows == 0
        assert db_session.execute(
            sa.select(PositionItem.work_category_id).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id == round_scene.baseline_id)
        ).scalars().all() == [None]

    def test_additional_work_with_a_resolvable_ref_follows_the_decision(self, db_session, round_scene, admin_user):
        """§1.4 спеки разноса, связка, не предикат: до решения строка «14 Отделка»
        — «кандидат без статьи», после — статья решения, во ВСЕХ сметах."""
        q = sa.select(EstimateAdditionalWork.work_category_id).where(EstimateAdditionalWork.chapter_ref_raw == "14")
        assert db_session.execute(q).scalars().all() == [None, None, None]
        result = put14(db_session, round_scene, admin_user)
        assert db_session.execute(q).scalars().all() == [cat(db_session, "20")] * 3
        assert result.additional_works_updated == 3

    def test_identical_repeat_is_a_no_op_that_keeps_the_audit(self, db_session, round_scene, admin_user, factories):
        put14(db_session, round_scene, admin_user)
        db_session.execute(sa.text("UPDATE estimate_category_overrides SET assigned_at = now() - interval '1 day'"))
        other = factories.UserFactory.create()
        put14(db_session, round_scene, other)                       # то же (статья, заметка), другой автор
        rows = overrides_of(db_session, round_scene, "14")
        assert {r.assigned_by for r in rows} == {admin_user.id}
        now = db_session.execute(sa.text("select now()")).scalar_one()
        assert all(r.assigned_at < now for r in rows)

    def test_same_vectors_but_a_new_article_is_a_rewrite(self, db_session, round_scene, admin_user, factories):
        """Негативный (а) к предикату no-op: без второго условия выбор новой
        статьи при согласованном старом решении сошёл бы за no-op."""
        put14(db_session, round_scene, admin_user)
        other = factories.UserFactory.create()
        put14(db_session, round_scene, other, code="16")
        rows = overrides_of(db_session, round_scene, "14")
        assert {(r.work_category_id, r.assigned_by) for r in rows} == {(cat(db_session, "16"), other.id)}

    def test_vectors_differing_only_in_audit_are_rewritten_and_aligned(self, db_session, round_scene, admin_user, factories):
        """Негативный (б): статья и заметка совпадают с телом, аудит разошёлся —
        перезапись выравнивает автора и время во всех сметах."""
        put14(db_session, round_scene, admin_user)
        victim = round_scene.chapter(round_scene.estimates[1].id, "14").id
        db_session.execute(sa.text("UPDATE estimate_category_overrides SET assigned_at = now() - interval '1 day' "
                                   "WHERE position_item_id = :rid"), {"rid": victim})
        assert state_of(db_session, round_scene, "14").state == ru.STATE_CONFLICT
        other = factories.UserFactory.create()
        put14(db_session, round_scene, other)
        rows = overrides_of(db_session, round_scene, "14")
        assert {(r.assigned_by, r.assigned_at) for r in rows} == {(other.id, rows[0].assigned_at)}
        # Та же ловушка, что в первом тесте PUT: согласие трёх смет само по
        # себе не отличает «одна метка транзакции» от «три метки случайно
        # совпали» — сверяем со временем ЭТОЙ транзакции.
        txn_now = db_session.execute(sa.text("select now()")).scalar_one()
        assert rows[0].assigned_at == txn_now
        assert state_of(db_session, round_scene, "14").state == ru.STATE_RESOLVED

    def test_note_null_clears_notes_everywhere_and_a_new_note_lands_everywhere(self, db_session, round_scene, admin_user):
        put14(db_session, round_scene, admin_user, note="старая")
        put14(db_session, round_scene, admin_user, note=None)
        assert {r.note for r in overrides_of(db_session, round_scene, "14")} == {None}
        put14(db_session, round_scene, admin_user, note="новая")
        assert {r.note for r in overrides_of(db_session, round_scene, "14")} == {"новая"}

    def test_partial_and_conflict_are_aligned_by_one_decision(self, db_session, round_scene, admin_user):
        e0 = round_scene.estimates[0]
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "14").id,
                     work_category_id=cat(db_session, "16"), note="чужая", user_id=admin_user.id)
        assert state_of(db_session, round_scene, "14").state == ru.STATE_PARTIAL
        put14(db_session, round_scene, admin_user)
        assert state_of(db_session, round_scene, "14").state == ru.STATE_RESOLVED

    def test_parity_with_the_by_estimate_service(self, db_session, round_scene, admin_user, factories):
        """Пересчёт — ТЕМ ЖЕ кодом (§2.4 п.4): смета, разнесённая по-сметным
        `set_override`, и смета, разнесённая раундовым PUT, материализованы
        построчно одинаково (разделы и допработы). Автор раундового PUT —
        другой пользователь, иначе для первой сметы сработал бы no-op."""
        e0, e1, _ = round_scene.estimates
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "14").id,
                     work_category_id=cat(db_session, "20"), note=None, user_id=admin_user.id)
        expected = materialized(db_session, e0.id)
        put14(db_session, round_scene, factories.UserFactory.create(), note=None)
        assert materialized(db_session, e1.id) == expected
        assert materialized(db_session, e0.id) == expected


class TestKeyResolution:
    def _put(self, db, scene, user, lot_key, key, *, round_id=None, code="20"):
        return rco.set_round_override(db, tender_id=scene.tender.id, round_id=round_id or scene.r1.id,
                                      lot_key=lot_key, position_key_in_proposal=key,
                                      work_category_id=cat(db, code), note=None, user_id=user.id)

    def test_key_absent_everywhere_is_section_not_found(self, db_session, round_scene, admin_user):
        with pytest.raises(DomainError) as e:
            self._put(db_session, round_scene, admin_user, round_scene.key14[0], "no-such-key-999")
        assert (e.value.status_code, e.value.code) == (404, rco.CODE_SECTION_NOT_FOUND)

    def test_key_present_in_some_estimates_is_mapping_broken(self, db_session, round_scene, admin_user):
        """`DELETE` действительно достигает резолвера без всякой дополнительной
        заботы об identity map (в отличие от соседнего теста на `is_chapter`
        ниже): строка физически исчезает из таблицы, и запрос `_resolve_key`
        просто не возвращает её в наборе `found` — тут нечему закешироваться
        под ключом, которого больше нет. Кеш становится ловушкой только когда
        строка ОСТАЁТСЯ на месте и меняется лишь её флаг сырым `UPDATE`."""
        e1 = round_scene.estimates[1]
        ch = round_scene.chapter(e1.id, "15")
        db_session.execute(sa.text("DELETE FROM position_items WHERE chapter_item_id = :cid"), {"cid": ch.id})
        db_session.execute(sa.text("DELETE FROM position_items WHERE id = :cid"), {"cid": ch.id})
        db_session.flush()
        with pytest.raises(crud_ru.RoundMappingBroken):
            self._put(db_session, round_scene, admin_user, *round_scene.key15)

    def test_key_of_a_position_everywhere_is_not_a_chapter(self, db_session, round_scene, admin_user):
        work_key = db_session.execute(
            sa.select(PositionItem.position_key_in_proposal).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == round_scene.estimates[0].id, PositionItem.is_chapter.is_(False)).limit(1)
        ).scalar_one()
        with pytest.raises(DomainError) as e:
            self._put(db_session, round_scene, admin_user, round_scene.key14[0], work_key)
        assert (e.value.status_code, e.value.code) == (422, rco.CODE_NOT_A_CHAPTER)

    def test_is_chapter_differing_between_estimates_is_mapping_broken(self, db_session, round_scene, admin_user):
        """Вход обязан нарушать РОВНО ОДНО ограничение (находка ревью): «15»
        не годится — под ним есть позиции, и их пришлось бы удалить, а
        удаление само по себе рвёт биекцию `raw_data` <-> строк ДО того, как
        дело дойдёт до ветки `is_chapter` (первая версия теста ловила именно
        это исключение, а не свою). «1.1» — единственный раздел ведомости без
        НИ ОДНОЙ своей позиции (докстрока `round_scene`), поэтому флип его
        флага не задевает биекцию вовсе: удалять нечего.

        Флип — присваиванием ОРМ-атрибуту, а не сырым `UPDATE`: сырой SQL
        меняет строку в БД мимо identity map сессии, и последующий ОРМ-запрос
        `_resolve_key` возвращает ТОТ ЖЕ закешированный Python-объект со
        старым `is_chapter=True` — ветка расхождения так и не вычисляется
        (вторая находка ревью: с сырым `UPDATE` тест был зелёным не потому,
        что проверял ветку, а потому что она никогда не запускалась). Флаг и
        три поля статьи обнуляются В ОДНОЙ ОРМ-транзакции одним `flush()`,
        чтобы не споткнуться о `ck_position_items_category_source_pairs`
        посередине.
        """
        lot_key = round_scene.key14[0]
        ch = round_scene.chapter(round_scene.estimates[2].id, "1.1")
        key = ch.position_key_in_proposal
        ch.is_chapter = False
        ch.smr_article_raw = None
        ch.work_category_id = None
        ch.category_source = None
        db_session.flush()
        with pytest.raises(crud_ru.RoundMappingBroken) as exc:
            self._put(db_session, round_scene, admin_user, lot_key, key)
        # Доказывает, что отказ пришёл ИМЕННО из разрешения ключа
        # (`_resolve_key`), а не из пересчёта: сообщение пересчёта не
        # называет `is_chapter` вовсе (оно про биекцию raw_data/лотов), только
        # ветка `_resolve_key` пишет это слово в текст исключения.
        assert "is_chapter" in str(exc.value)

    def test_unknown_category_is_category_not_found(self, db_session, round_scene, admin_user):
        with pytest.raises(DomainError) as e:
            rco.set_round_override(db_session, tender_id=round_scene.tender.id, round_id=round_scene.r1.id,
                                   lot_key=round_scene.key14[0], position_key_in_proposal=round_scene.key14[1],
                                   work_category_id=10**9, note=None, user_id=admin_user.id)
        assert (e.value.status_code, e.value.code) == (404, rco.CODE_CATEGORY_NOT_FOUND)

    def test_disabled_structure_is_409(self, db_session, round_scene, admin_user):
        e = crud_ru.offer_estimates(db_session, round_scene.r2.id)[0]
        lot_key, key = db_session.execute(
            sa.select(Lot.lot_key, PositionItem.position_key_in_proposal)
            .join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id == e.id, PositionItem.is_chapter.is_(True))
        ).one()
        with pytest.raises(DomainError) as err:
            self._put(db_session, round_scene, admin_user, lot_key, key, round_id=round_scene.r2.id)
        assert (err.value.status_code, err.value.code) == (409, rco.CODE_STRUCTURE_DISABLED)

    def test_key_and_category_both_invalid_the_key_outcome_wins(self, db_session, round_scene, admin_user):
        """G: спека §2.4 не фиксирует очерёдность между разрешением ключа и
        проверкой статьи — этот тест пинит РЕШЕНИЕ ревью (ключ раньше статьи,
        как и в реализации): при обоих неверных сразу побеждает
        `section_not_found`, а не `category_not_found`."""
        with pytest.raises(DomainError) as e:
            rco.set_round_override(db_session, tender_id=round_scene.tender.id, round_id=round_scene.r1.id,
                                   lot_key=round_scene.key14[0], position_key_in_proposal="no-such-key-999",
                                   work_category_id=10**9, note=None, user_id=admin_user.id)
        assert (e.value.status_code, e.value.code) == (404, rco.CODE_SECTION_NOT_FOUND)

    def test_round_without_offer_estimates_is_404_before_anything_else(self, db_session, round_scene, admin_user, factories):
        empty = factories.TenderRoundFactory.create(tender=round_scene.tender, stage_no=3)
        db_session.flush()
        with pytest.raises(DomainError) as e:
            self._put(db_session, round_scene, admin_user, round_scene.key14[0], "1", round_id=empty.id)
        assert e.value.code == crud_ru.CODE_NO_OFFER_ESTIMATES


class TestDelete:
    def _delete(self, db, scene, key):
        return rco.clear_round_override(db, tender_id=scene.tender.id, round_id=scene.r1.id,
                                        lot_key=key[0], position_key_in_proposal=key[1])

    def test_removes_the_decision_from_every_estimate_and_returns_extras(self, db_session, round_scene, admin_user):
        put14(db_session, round_scene, admin_user)
        result = self._delete(db_session, round_scene, round_scene.key14)
        assert overrides_of(db_session, round_scene, "14") == [None, None, None]
        assert result.chapters_updated == 9 and result.additional_works_updated == 3
        assert state_of(db_session, round_scene, "14").state == ru.STATE_UNASSIGNED

    def test_aligns_partial_and_conflict_to_no_decision(self, db_session, round_scene, admin_user):
        e0 = round_scene.estimates[0]
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "14").id,
                     work_category_id=cat(db_session, "16"), note=None, user_id=admin_user.id)
        self._delete(db_session, round_scene, round_scene.key14)
        assert overrides_of(db_session, round_scene, "14") == [None, None, None]

    def test_removes_a_real_conflict_from_every_estimate(self, db_session, round_scene, admin_user):
        """Замыкающее предложение §2.4: DELETE выравнивает и КОНФЛИКТНОЕ
        состояние в «без решения», не только частичное (сосед выше строит
        только `partial` — override в одной смете; этот тест строит настоящий
        `conflict` — override во ВСЕХ трёх, с разными статьями)."""
        codes = ["20", "20", "16"]
        for e, code in zip(round_scene.estimates, codes, strict=True):
            set_override(db_session, estimate_id=e.id, position_item_id=round_scene.chapter(e.id, "14").id,
                         work_category_id=cat(db_session, code), note=None, user_id=admin_user.id)
        assert state_of(db_session, round_scene, "14").state == ru.STATE_CONFLICT
        self._delete(db_session, round_scene, round_scene.key14)
        assert overrides_of(db_session, round_scene, "14") == [None, None, None]
        assert state_of(db_session, round_scene, "14").state == ru.STATE_UNASSIGNED

    def test_no_decision_anywhere_is_a_no_op_not_an_error(self, db_session, round_scene):
        result = self._delete(db_session, round_scene, round_scene.key14)
        assert result.chapters_updated == 0

    def test_key_absent_everywhere_is_section_not_found_not_a_no_op(self, db_session, round_scene):
        """Коллизия, найденная ревью гейта 2: таблица исходов ключа идёт ДО no-op."""
        with pytest.raises(DomainError) as e:
            self._delete(db_session, round_scene, (round_scene.key14[0], "no-such-key-999"))
        assert e.value.code == rco.CODE_SECTION_NOT_FOUND


class TestAtomicity:
    def test_a_failure_on_the_last_estimate_leaves_no_estimate_changed(self, db_session, round_scene, admin_user, monkeypatch):
        """Искусственный отказ пересчёта на ПОСЛЕДНЕЙ смете: транзакцию ведёт
        вызывающий, поэтому проверяется, что после `rollback()` ни одна смета не
        несёт решения (§2.4 п.5). `db_session.commit()` до действия фиксирует
        границу савпоинта — как в `test_a_refused_put_rolls_back`.

        Голых проверок «после отката пусто» недостаточно (находка ревью): это
        верно и для реализации, которая вообще ничего не записала бы. Поэтому
        счётчики снимаются ДВАЖДЫ — внутри хука отказа (доказывает, что запись
        и частичный пересчёт РЕАЛЬНО произошли: три решения уже флешнуты во
        ВСЕ сметы к этому моменту, а первые две сметы уже материализовались —
        по 3 строки-раздела на смету, «14»/«14.1»/«14.3») и после `rollback()`
        (доказывает, что откат унёс именно это, а не то, что писать было
        нечего)."""
        import services.round_category_override as module
        real = module.apply_overrides
        last = round_scene.estimate_ids[-1]
        observed: dict[str, int] = {}

        def failing(db, estimate_id, **kw):
            if estimate_id == last:
                observed["overrides"] = db.execute(
                    sa.select(sa.func.count()).select_from(EstimateCategoryOverride)
                ).scalar_one()
                observed["manual_rows"] = db.execute(
                    sa.select(sa.func.count()).select_from(PositionItem)
                    .where(PositionItem.category_source == "manual")
                ).scalar_one()
                raise RuntimeError("искусственный отказ на последней смете")
            return real(db, estimate_id, **kw)

        monkeypatch.setattr(module, "apply_overrides", failing)
        db_session.commit()
        with pytest.raises(RuntimeError):
            put14(db_session, round_scene, admin_user)
        assert observed == {"overrides": 3, "manual_rows": 6}  # решения — во ВСЕХ трёх; пересчёт — только в первых двух
        db_session.rollback()
        assert db_session.execute(sa.select(sa.func.count()).select_from(EstimateCategoryOverride)).scalar_one() == 0
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(PositionItem).where(PositionItem.category_source == "manual")
        ).scalar_one() == 0


OVERRIDES = "/api/v1/tenders/{t}/rounds/{r}/category-overrides"


def body14(scene, code_id, note="из http"):
    return {"lot_key": scene.key14[0], "position_key_in_proposal": scene.key14[1],
            "work_category_id": code_id, "note": note}


class TestHttp:
    def test_member_puts_and_gets_the_three_key_summary(self, member_client, db_session, round_scene, factories):
        """Пункт B ревью: без ВТОРОГО пользователя «автор — запросивший member»
        истинно только потому, что в таблице `users` он ОДИН — ревью доказало
        это, подменив роутер на «взять первого пользователя таблицы», и тест
        остался зелёным. Второй, лишний кандидат делает утверждение различающим:
        `assigned_by` обязан совпасть ИМЕННО с `member_client.user.id`, а не с
        любым существующим `User.id`."""
        other = factories.UserFactory.create()
        db_session.flush()
        assert other.id != member_client.user.id

        r = member_client.put(OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id),
                              json=body14(round_scene, cat(db_session, "20")))
        assert r.status_code == 200, r.text
        assert set(r.json()) == {"chapters_updated", "additional_works_updated", "chapters_manual"}
        assert r.json()["chapters_updated"] == 9
        assert {o.assigned_by for o in overrides_of(db_session, round_scene, "14")} == {member_client.user.id}

    def test_note_omitted_is_422_and_note_null_is_accepted(self, member_client, db_session, round_scene):
        url = OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id)
        without = {k: v for k, v in body14(round_scene, cat(db_session, "20")).items() if k != "note"}
        assert member_client.put(url, json=without).status_code == 422          # omitted != null (§2.4)
        assert member_client.put(url, json=body14(round_scene, cat(db_session, "20"), note=None)).status_code == 200
        assert {o.note for o in overrides_of(db_session, round_scene, "14")} == {None}

    @pytest.mark.parametrize("patch, status, code", [
        ({"position_key_in_proposal": "999"}, 404, "section_not_found"),
        ({"work_category_id": 10**9}, 404, "category_not_found"),
    ])
    def test_refusal_codes(self, member_client, db_session, round_scene, patch, status, code):
        body = {**body14(round_scene, cat(db_session, "20")), **patch}
        r = member_client.put(OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id), json=body)
        assert (r.status_code, r.json()["detail"]["code"]) == (status, code)

    def test_position_key_is_422_not_a_chapter(self, member_client, db_session, round_scene):
        work_key = db_session.execute(
            sa.select(PositionItem.position_key_in_proposal).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == round_scene.estimates[0].id, PositionItem.is_chapter.is_(False)).limit(1)
        ).scalar_one()
        body = {**body14(round_scene, cat(db_session, "20")), "position_key_in_proposal": work_key}
        r = member_client.put(OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id), json=body)
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "not_a_chapter")

    def test_disabled_structure_is_409_and_the_decision_is_rolled_back(self, member_client, db_session, round_scene):
        """Как `test_a_refused_put_rolls_back`: решение флешнуто ДО того, как
        пересчёт поднял `structure_disabled`; без `rollback()` в роутере строка
        осталась бы. `db_session.commit()` фиксирует границу савпоинта."""
        db_session.commit()
        e = crud_ru.offer_estimates(db_session, round_scene.r2.id)[0]
        key = db_session.execute(
            sa.select(PositionItem.position_key_in_proposal).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id == e.id, PositionItem.is_chapter.is_(True))
        ).scalar_one()
        r = member_client.put(OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r2.id),
                              json={"lot_key": "lot_1", "position_key_in_proposal": key,
                                    "work_category_id": cat(db_session, "20"), "note": None})
        assert (r.status_code, r.json()["detail"]["code"]) == (409, "structure_disabled")
        assert db_session.execute(sa.select(sa.func.count()).select_from(EstimateCategoryOverride)).scalar_one() == 0

    def test_integrity_failure_from_recalculation_rolls_back_through_the_route(
        self, member_client_no_raise, db_session, round_scene, monkeypatch
    ):
        """Пункт A ревью: `test_mapping_broken_is_500_and_logged` ниже ловит
        `RoundMappingBroken` из РАЗРЕШЕНИЯ КЛЮЧА (`_resolve_key`) — оно
        случается ДО всякой записи, так что `db.rollback()` в роутере там
        отменять нечего, и его отсутствие невидимо: ревью убрало этот
        `rollback()` из ветки `RoundMappingBroken`, и все 35 тестов остались
        зелёными. Настоящий случай — отказ ЦЕЛОСТНОСТИ ИЗ ПЕРЕСЧЁТА
        (`apply_overrides` поднимает `CategoryOverrideError` кодом, отличным
        от `structure_disabled`, например при отсутствующем `raw_data`):
        `set_round_override` к этому моменту уже записал и `flush`-нул
        решения во ВСЕ offer-сметы, и только тогда пересчёт доходит до
        последней и падает — как `TestAtomicity.test_a_failure_on_the_last_
        estimate_leaves_no_estimate_changed`, но через РОУТЕР и HTTP, чтобы
        стеречь именно роутерный `rollback()`, а не тестовый вызывающий код.
        `db_session.commit()` фиксирует границу савпоинта ПЕРЕД рискованным
        запросом — тем же приёмом, что у `test_disabled_structure_is_409_
        and_the_decision_is_rolled_back` и у `TestAtomicity`.
        """
        import services.round_category_override as module
        real = module.apply_overrides
        last = round_scene.estimate_ids[-1]
        observed: dict[str, int] = {}

        def failing(db, estimate_id, **kw):
            if estimate_id == last:
                observed["overrides"] = db.execute(
                    sa.select(sa.func.count()).select_from(EstimateCategoryOverride)
                ).scalar_one()
                raise module.CategoryOverrideError("mapping_broken", "искусственный отказ целостности на последней смете")
            return real(db, estimate_id, **kw)

        monkeypatch.setattr(module, "apply_overrides", failing)
        db_session.commit()

        captured: list[logging.LogRecord] = []

        class _Collector(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = _Collector(level=logging.ERROR)
        router_log = logging.getLogger("routers.tenders")
        router_log.addHandler(handler)
        try:
            r = member_client_no_raise.put(OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id),
                                           json=body14(round_scene, cat(db_session, "20")))
        finally:
            router_log.removeHandler(handler)

        assert r.status_code == 500
        assert any(rec.levelno == logging.ERROR for rec in captured)
        # Решения ДЕЙСТВИТЕЛЬНО были флешнуты во все три сметы к моменту отказа
        # (иначе итоговый ноль ниже доказывал бы только «писать было нечего»,
        # не откат) — тот же довод, что у `TestAtomicity`.
        assert observed.get("overrides") == 3
        # ЭТА проверка и есть та, что падает без `db.rollback()` в роутере.
        assert db_session.execute(sa.select(sa.func.count()).select_from(EstimateCategoryOverride)).scalar_one() == 0

    def test_mapping_broken_is_500_and_logged(self, member_client_no_raise, db_session, round_scene):
        e1 = round_scene.estimates[1]
        ch = round_scene.chapter(e1.id, "15")
        db_session.execute(sa.text("DELETE FROM position_items WHERE chapter_item_id = :cid"), {"cid": ch.id})
        db_session.execute(sa.text("DELETE FROM position_items WHERE id = :cid"), {"cid": ch.id})
        db_session.flush()
        captured: list[logging.LogRecord] = []

        class _Collector(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = _Collector(level=logging.ERROR)
        router_log = logging.getLogger("routers.tenders")
        router_log.addHandler(handler)
        try:
            r = member_client_no_raise.put(OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id),
                                           json={"lot_key": round_scene.key15[0], "position_key_in_proposal": round_scene.key15[1],
                                                 "work_category_id": cat(db_session, "20"), "note": None})
        finally:
            router_log.removeHandler(handler)
        assert r.status_code == 500
        assert any(rec.levelno == logging.ERROR for rec in captured)

    # --- Пункт C ревью: те же три отказа, уже покрытые на PUT выше
    # (`not_a_chapter`, `structure_disabled`, `mapping_broken`), но никогда
    # не пройденные через DELETE, хотя `clear_round_override` проходит ТУ ЖЕ
    # разрешение ключа и ТОТ ЖЕ пересчёт (§2.4: «DELETE симметричен PUT через
    # шаги 1-2»). Тела и глагол здесь — единственное отличие, поэтому не
    # параметризую (PUT-версии уже существуют отдельными тестами, не
    # переиспользуемыми напрямую из-за разных тел) — просто зеркалю их на DELETE.

    def test_delete_not_a_chapter_is_422(self, member_client, db_session, round_scene):
        work_key = db_session.execute(
            sa.select(PositionItem.position_key_in_proposal).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == round_scene.estimates[0].id, PositionItem.is_chapter.is_(False)).limit(1)
        ).scalar_one()
        r = member_client.request(
            "DELETE", OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id),
            json={"lot_key": round_scene.key14[0], "position_key_in_proposal": work_key},
        )
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "not_a_chapter")

    def test_delete_disabled_structure_is_409(self, member_client, db_session, round_scene, factories):
        """Пункт C ревью — этот случай не зеркалится с PUT напрямую.
        `clear_round_override` СНАЧАЛА удаляет решение по ЦЕЛЕВОМУ ключу и
        только ПОТОМ пересчитывает, а `apply_overrides` поднимает
        `structure_disabled` лишь когда `overrides` предложения НЕ пусты
        (`services/category_override.py`: `if resolution.structure_disabled
        and overrides`). У `round_scene.r2` в предложении ровно ОДНА позиция —
        DELETE стирает решение на неё же ДО пересчёта, `overrides` пустеет, и
        запрос молча получает 200 вместо 409 (проверено: с телом на `r2`,
        как у PUT-теста, ответ реально был `200`, не `409` — первая версия
        этого теста ловила именно эту ложную зелень).

        Настоящий 409 требует предложения с ДВУМЯ разделами: один гасит
        структуру ЦЕЛОГО предложения (`chapter_number="прим."`), второй не
        участвует в этом DELETE и несёт УЖЕ существующее решение — оно
        вставлено В ОБХОД домена (тем же приёмом, что у mapping_broken-тестов
        файла), поскольку через API оно недостижимо: PUT на погашенную
        структуру сам получил бы 409 и откатился (см. тест выше). Раунд
        строится напрямую через `import_round`, как `round_scene`, — тем же
        набором инструментов, без правки conftest.py.
        """
        from services.category_resolution import CategoryResolver
        from services.round_import import import_round
        from services.unit_resolution import UnitResolver
        from tests.payloads import position, proposal, round_payload

        broken_round = factories.TenderRoundFactory.create(tender=round_scene.tender, stage_no=42)
        db_session.flush()
        data = round_payload([proposal([
            position(job_title="Раздел рабочий", is_chapter=True, chapter_number="14", number="1"),
            position(job_title="Раздел с нечитаемым номером", is_chapter=True, chapter_number="прим.", number="2"),
        ])])
        import_round(db_session, tender_round=broken_round, data=data, parser_version="4.0.0",
                    import_job_id=None, replace=False, unit_resolver=UnitResolver(db_session),
                    category_resolver=CategoryResolver.from_db(db_session))
        db_session.flush()

        estimate_id = crud_ru.offer_estimates(db_session, broken_round.id)[0].id

        def chapter_by_number(number):
            return db_session.execute(
                sa.select(PositionItem).join(Proposal, Proposal.id == PositionItem.proposal_id)
                .join(Lot, Lot.id == Proposal.lot_id)
                .where(Lot.estimate_id == estimate_id, PositionItem.chapter_number_in_proposal == number)
            ).scalar_one()

        target = chapter_by_number("14")
        survivor = chapter_by_number("прим.")

        db_session.add(EstimateCategoryOverride(position_item_id=survivor.id, work_category_id=cat(db_session, "20"),
                                                note=None, assigned_by=member_client.user.id, assigned_at=sa.func.now()))
        db_session.flush()

        r = member_client.request(
            "DELETE", OVERRIDES.format(t=round_scene.tender.id, r=broken_round.id),
            json={"lot_key": "lot_1", "position_key_in_proposal": target.position_key_in_proposal},
        )
        assert (r.status_code, r.json()["detail"]["code"]) == (409, "structure_disabled")

    def test_delete_mapping_broken_is_500_and_logged(self, member_client_no_raise, db_session, round_scene):
        e1 = round_scene.estimates[1]
        ch = round_scene.chapter(e1.id, "15")
        db_session.execute(sa.text("DELETE FROM position_items WHERE chapter_item_id = :cid"), {"cid": ch.id})
        db_session.execute(sa.text("DELETE FROM position_items WHERE id = :cid"), {"cid": ch.id})
        db_session.flush()
        captured: list[logging.LogRecord] = []

        class _Collector(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = _Collector(level=logging.ERROR)
        router_log = logging.getLogger("routers.tenders")
        router_log.addHandler(handler)
        try:
            r = member_client_no_raise.request(
                "DELETE", OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id),
                json={"lot_key": round_scene.key15[0], "position_key_in_proposal": round_scene.key15[1]},
            )
        finally:
            router_log.removeHandler(handler)
        assert r.status_code == 500
        assert any(rec.levelno == logging.ERROR for rec in captured)

    def test_delete_with_a_body_clears_everywhere_and_missing_key_is_404(self, member_client, db_session, round_scene):
        url = OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id)
        assert member_client.put(url, json=body14(round_scene, cat(db_session, "20"))).status_code == 200
        r = member_client.request("DELETE", url, json={"lot_key": round_scene.key14[0],
                                                       "position_key_in_proposal": round_scene.key14[1]})
        assert r.status_code == 200
        # Пункт D ревью: раньше проверялся только НАБОР ключей ответа — этого
        # не хватило бы, чтобы поймать по-сметный результат ОДНОЙ сметы вместо
        # суммы по раунду. Значения — те же, что в сервисном
        # `TestDelete.test_removes_the_decision_from_every_estimate_and_returns_extras`
        # (§1.4 спеки разноса: допработа со ссылкой «14» переезжает вместе с
        # разделом), и `chapters_manual` — 0: решение снято, «ручных» строк
        # у раздела «14» не осталось ни у одной offer-сметы.
        assert r.json() == {"chapters_updated": 9, "additional_works_updated": 3, "chapters_manual": 0}
        assert overrides_of(db_session, round_scene, "14") == [None, None, None]
        r = member_client.request("DELETE", url, json={"lot_key": "lot_1", "position_key_in_proposal": "999"})
        assert (r.status_code, r.json()["detail"]["code"]) == (404, "section_not_found")

    def test_empty_note_normalises_to_null(self, member_client, db_session, round_scene):
        """Решение пользователя 02.09.2026: пустая строка в раундовом теле PUT
        становится `null`, а не буквальным `""`, ДО того, как сервис увидит
        значение (валидатор `RoundOverridePut._blank_note_is_null`)."""
        url = OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id)
        r = member_client.put(url, json=body14(round_scene, cat(db_session, "20"), note=""))
        assert r.status_code == 200, r.text
        assert {o.note for o in overrides_of(db_session, round_scene, "14")} == {None}

    def test_whitespace_only_note_normalises_to_null(self, member_client, db_session, round_scene):
        """Пробельная строка — та же пустота, что и `""` (правило по трим на
        границах): табы, переводы строк и пробелы внутри и по краям, ничего
        не остающееся после `strip()`."""
        url = OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id)
        r = member_client.put(url, json=body14(round_scene, cat(db_session, "20"), note="  \t\n  "))
        assert r.status_code == 200, r.text
        assert {o.note for o in overrides_of(db_session, round_scene, "14")} == {None}

    def test_real_note_with_surrounding_whitespace_is_stored_untrimmed(self, member_client, db_session, round_scene):
        """Негативный к обоим тестам выше: решение о пустоте смотрит на
        `value.strip()`, но СОХРАНЯЕТ исходную строку целиком — ни края, ни
        середина настоящей заметки не обрезаются."""
        note = "  реальная   заметка  "
        url = OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id)
        r = member_client.put(url, json=body14(round_scene, cat(db_session, "20"), note=note))
        assert r.status_code == 200, r.text
        assert {o.note for o in overrides_of(db_session, round_scene, "14")} == {note}

    def test_empty_string_then_null_is_not_a_conflict_and_keeps_the_first_audit(
        self, member_client, db_session, round_scene, factories
    ):
        """Это и есть вред, который лечит нормализация (не голая проверка
        итогового значения, а поведение НА ГРАНИЦЕ предиката no-op §2.4 п.3):
        первый запрос кладёт `""`, второй, от ДРУГОГО пользователя, кладёт
        `null` при той же статье. Без нормализации `("", cat) != (None, cat)`
        не совпало бы с предикатом no-op — второй запрос ушёл бы в перезапись
        и переставил аудит на второго пользователя, хотя содержательно ничего
        не изменилось. С нормализацией оба запроса несут ОДНО и то же значение
        `(None, cat)`, второй — no-op, аудит остаётся за первым автором, а
        состояние раздела — `resolved`, не `conflict` (структурно раздел раунда
        и не мог бы разойтись по сметам от одних только раундовых PUT: запись
        атомарна и переписывает ВСЕ offer-сметы одним значением за один вызов,
        так что расхождение между сметами эта пара вызовов доказать не может —
        зато то, что предикат no-op их СЧИТАЕТ одним и тем же решением, а не
        двумя разными, доказывает именно аудит, не тронутый вторым вызовом).
        """
        url = OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id)
        code_id = cat(db_session, "20")
        first_author = member_client.user.id

        r1 = member_client.put(url, json=body14(round_scene, code_id, note=""))
        assert r1.status_code == 200, r1.text

        second_user = factories.UserFactory.create()
        db_session.flush()
        member_client.set_user(second_user)

        r2 = member_client.put(url, json=body14(round_scene, code_id, note=None))
        assert r2.status_code == 200, r2.text

        rows = overrides_of(db_session, round_scene, "14")
        assert {o.note for o in rows} == {None}
        assert {o.assigned_by for o in rows} == {first_author}          # аудит НЕ переехал на second_user
        assert state_of(db_session, round_scene, "14").state == ru.STATE_RESOLVED


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
class TestWriteRefusalsAcrossVerbs:
    """Пункт C ревью: `tender_not_found`, `round_not_found` и
    `round_has_no_offer_estimates` не доходили ни до одного теста ЧЕРЕЗ
    РОУТЕР ни на одном глаголе — первые два не были покрыты вообще нигде,
    даже на уровне сервиса; третий был покрыт только на уровне сервиса
    (`TestKeyResolution.test_round_without_offer_estimates_is_404_before_
    anything_else`), а маршрут не проверял никто. PUT и DELETE проходят ОДНУ
    и ТУ ЖЕ `_lock_scope` (§2.4 п.1: тендер → раунд → сметы) ДО разрешения
    ключа, поэтому один параметризованный класс достаточен на оба глагола —
    тело запроса и метод HTTP единственное, что отличается, и оба явно
    проверяются в каждом тесте (не просто «код есть где-то в наборе»).
    """

    @staticmethod
    def _body(method, scene, db):
        if method == "PUT":
            return body14(scene, cat(db, "20"))
        return {"lot_key": scene.key14[0], "position_key_in_proposal": scene.key14[1]}

    def test_unknown_tender_is_404(self, member_client, db_session, round_scene, method):
        url = OVERRIDES.format(t=10**9, r=round_scene.r1.id)
        r = member_client.request(method, url, json=self._body(method, round_scene, db_session))
        assert (r.status_code, r.json()["detail"]["code"]) == (404, "tender_not_found")

    def test_round_of_another_tender_is_404(self, member_client, db_session, round_scene, factories, method):
        other = factories.TenderFactory.create()
        db_session.flush()
        url = OVERRIDES.format(t=other.id, r=round_scene.r1.id)
        r = member_client.request(method, url, json=self._body(method, round_scene, db_session))
        assert (r.status_code, r.json()["detail"]["code"]) == (404, "round_not_found")

    def test_round_without_offer_estimates_is_404(self, member_client, db_session, round_scene, factories, method):
        empty = factories.TenderRoundFactory.create(tender=round_scene.tender, stage_no=3)
        db_session.flush()
        url = OVERRIDES.format(t=round_scene.tender.id, r=empty.id)
        r = member_client.request(method, url, json=self._body(method, round_scene, db_session))
        assert (r.status_code, r.json()["detail"]["code"]) == (404, "round_has_no_offer_estimates")
