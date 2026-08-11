"""Применение решений о статье (спека разноса §2.3–2.5, §4.3)."""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud.project_passport import get_project_passport
from models import (
    EstimateAdditionalWork,
    EstimateCategoryOverride,
    Lot,
    PositionItem,
    Proposal,
    UserRole,
)
from services.category_override import (
    CategoryOverrideError,
    apply_overrides,
    clear_override,
    set_override,
)
from services.category_resolution import CATEGORY_SOURCE_MANUAL

pytestmark = pytest.mark.integration


def _grand_total(db, contract_id) -> Decimal | None:
    return get_project_passport(db, contract_id)["totals"]["amount"]


def test_assigning_a_top_chapter_reaches_the_whole_subtree(
    db_session, imported_estimate, top_unassigned_chapter, category_id, admin_user
):
    before = _grand_total(db_session, imported_estimate.contract_id)
    positions_before = db_session.execute(
        sa.select(sa.func.count())
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == imported_estimate.id, PositionItem.is_chapter.is_(False))
    ).scalar_one()

    result = set_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.flush()

    assert result.chapters_updated > 1, "поддерево должно наследовать решение"
    # Пин смысла `chapters_manual` (спека §2.1): это раздел И его подраздел,
    # т.е. эффективно-ручные строки, свои и унаследованные вместе, — а не
    # число решений (решение здесь одно). У этой фикстуры поддерево — раздел
    # + один подраздел, отсюда именно 2, не 1.
    assert result.chapters_manual == 2
    # Ни одна ПОЗИЦИЯ не изменена: статью несут только строки-разделы (спека §1.3).
    # Ассерт держится ПО ПОСТРОЕНИЮ резолвера (`_row_kind`/`_article_for` в
    # `category_resolution.py` никогда не предлагают статью не-разделу), а не
    # только гардом `_materialize_chapters`, — эти два утверждения о разных
    # частях кода звучат одинаково, но проверяют не то же самое.
    assert db_session.execute(
        sa.select(sa.func.count())
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(
            Lot.estimate_id == imported_estimate.id,
            PositionItem.is_chapter.is_(False),
            PositionItem.work_category_id.is_not(None),
        )
    ).scalar_one() == 0
    # Число строк-позиций не двигается ПО ПОСТРОЕНИЮ сервиса: он ничего не
    # `INSERT`/`DELETE`-ит, только `UPDATE` существующих разделов, — держится
    # тем, что здесь нет ни одного `db.add`/`db.delete` над `PositionItem`.
    assert db_session.execute(
        sa.select(sa.func.count())
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == imported_estimate.id, PositionItem.is_chapter.is_(False))
    ).scalar_one() == positions_before
    assert db_session.get(PositionItem, top_unassigned_chapter.id).category_source == (
        CATEGORY_SOURCE_MANUAL
    )
    # Инвариант фичи: итог сметы не двинулся, деньги только переехали.
    assert _grand_total(db_session, imported_estimate.contract_id) == before


def test_a_recalculation_without_decisions_reproduces_the_import(db_session, imported_estimate):
    """Главная проверка выбранного подхода (спека §2.3): `raw_data` неизменяем,
    поэтому пересчёт без решений обязан дать в точности то, что записал импорт."""
    snapshot = _chapter_snapshot(db_session, imported_estimate.id)
    apply_overrides(db_session, imported_estimate.id)
    db_session.flush()
    assert _chapter_snapshot(db_session, imported_estimate.id) == snapshot


def test_applying_twice_changes_nothing_the_second_time(
    db_session, imported_estimate, top_unassigned_chapter, category_id, admin_user
):
    set_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.flush()
    once = _chapter_snapshot(db_session, imported_estimate.id)

    again = apply_overrides(db_session, imported_estimate.id)
    db_session.flush()
    assert _chapter_snapshot(db_session, imported_estimate.id) == once
    # Счётчики ОБЯЗАНЫ быть нулями: снимок совпал бы и при безусловной перезаписи
    # теми же значениями, то есть сам по себе он защиту «писать только
    # изменившееся» не стережёт — краснеет только это утверждение.
    assert again.chapters_updated == 0
    assert again.additional_works_updated == 0


def test_a_repeat_identical_decision_does_not_move_the_audit(
    db_session, imported_estimate, top_unassigned_chapter, category_id, admin_user, factories
):
    """Спека §2.2: «UPSERT идемпотентен по построению» верно для строки-результата,
    но не для автора и времени — у `assigned_at` нет `onupdate`, поэтому повторный
    вызов с ТЕМИ ЖЕ `work_category_id` и `note` обязан оставить их прежними, даже
    если позвал их другой пользователь. Смена статьи, наоборот, ОБЯЗАНА подвинуть
    оба поля — иначе паспорт для банка показывал бы автора первого решения даже
    после того, как решение фактически поменялось."""
    set_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.flush()
    first = db_session.get(EstimateCategoryOverride, top_unassigned_chapter.id)
    first_assigned_at, first_assigned_by = first.assigned_at, first.assigned_by

    other_user = factories.UserFactory.create(role=UserRole.admin)
    db_session.flush()
    set_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
        work_category_id=category_id,  # та же статья, тот же note=None
        note=None,
        user_id=other_user.id,
    )
    db_session.flush()
    db_session.refresh(first)
    assert (first.assigned_at, first.assigned_by) == (first_assigned_at, first_assigned_by)

    set_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
        work_category_id=category_id,
        note="статья пересмотрена",  # note изменился — решение фактически другое
        user_id=other_user.id,
    )
    db_session.flush()
    db_session.refresh(first)
    assert first.assigned_by == other_user.id
    assert first.assigned_at >= first_assigned_at


def test_a_missing_category_is_refused_before_the_flush(
    db_session, imported_estimate, top_unassigned_chapter, admin_user
):
    """Спека §2.7 обещает `404`, а не `500`: FK дал бы `IntegrityError` из flush."""
    with pytest.raises(CategoryOverrideError) as exc:
        set_override(
            db_session,
            estimate_id=imported_estimate.id,
            position_item_id=top_unassigned_chapter.id,
            work_category_id=10**9,
            note=None,
            user_id=admin_user.id,
        )
    assert exc.value.code == "not_found"


def test_clearing_a_decision_that_is_not_there_succeeds(db_session, imported_estimate,
                                                        top_unassigned_chapter):
    """Снятие идемпотентно: два оператора могут снять одно решение одновременно, и
    второму нечего сообщить об ошибке — состояние уже такое, какого он хотел.
    Пересчёт при этом всё равно выполняется: он и есть смысл вызова."""
    result = clear_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
    )
    assert result.chapters_updated == 0


def test_an_additional_work_referencing_the_chapter_gets_the_same_article(
    db_session, estimate_with_extra_ref, referenced_chapter, category_id, admin_user
):
    """Замер спеки §1.4: до разноса `resolve_ref` даёт «кандидат без статьи»."""
    extra = db_session.execute(
        sa.select(EstimateAdditionalWork)
        .join(Proposal, Proposal.id == EstimateAdditionalWork.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_with_extra_ref.id)
    ).scalars().first()
    assert extra.work_category_id is None

    result = set_override(
        db_session,
        estimate_id=estimate_with_extra_ref.id,
        position_item_id=referenced_chapter.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.flush()

    assert result.additional_works_updated == 1
    db_session.refresh(extra)
    assert extra.work_category_id == category_id


def test_clearing_returns_chapters_and_extras_to_their_import_state(
    db_session, estimate_with_extra_ref, referenced_chapter, category_id, admin_user
):
    snapshot = _chapter_snapshot(db_session, estimate_with_extra_ref.id)
    extras_before = _extras_snapshot(db_session, estimate_with_extra_ref.id)

    set_override(
        db_session,
        estimate_id=estimate_with_extra_ref.id,
        position_item_id=referenced_chapter.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.flush()
    clear_override(
        db_session,
        estimate_id=estimate_with_extra_ref.id,
        position_item_id=referenced_chapter.id,
    )
    db_session.flush()

    assert _chapter_snapshot(db_session, estimate_with_extra_ref.id) == snapshot
    assert _extras_snapshot(db_session, estimate_with_extra_ref.id) == extras_before
    assert db_session.get(EstimateCategoryOverride, referenced_chapter.id) is None


def test_a_non_chapter_target_is_refused(
    db_session, imported_estimate, any_position_row, category_id, admin_user
):
    with pytest.raises(CategoryOverrideError) as exc:
        set_override(
            db_session,
            estimate_id=imported_estimate.id,
            position_item_id=any_position_row.id,
            work_category_id=category_id,
            note=None,
            user_id=admin_user.id,
        )
    assert exc.value.code == "not_a_chapter"


def test_a_chapter_of_another_estimate_is_not_found(
    db_session, imported_estimate, other_estimate_chapter, category_id, admin_user
):
    with pytest.raises(CategoryOverrideError) as exc:
        set_override(
            db_session,
            estimate_id=imported_estimate.id,
            position_item_id=other_estimate_chapter.id,
            work_category_id=category_id,
            note=None,
            user_id=admin_user.id,
        )
    assert exc.value.code == "not_found"


def test_a_disabled_structure_refuses_the_decision(
    db_session, estimate_with_broken_numbering, broken_chapter, category_id, admin_user
):
    with pytest.raises(CategoryOverrideError) as exc:
        set_override(
            db_session,
            estimate_id=estimate_with_broken_numbering.id,
            position_item_id=broken_chapter.id,
            work_category_id=category_id,
            note=None,
            user_id=admin_user.id,
        )
    assert exc.value.code == "structure_disabled"


def test_a_broken_bijection_is_refused_loudly(db_session, imported_estimate):
    """Ключ `raw_data` без строки в БД (спека §2.3). Сегодня недостижимо —
    воспроизводится удалением одной строки в обход домена.

    Выбор строки-жертвы СКОПЛЕН на `imported_estimate` (join до `lots.estimate_id`):
    без этого условия запрос мог бы выбрать строку любой другой сметы, созданной
    другой фикстурой того же теста, и тест перестал бы проверять то, что заявляет.

    Жертва — ПОЗИЦИЯ (`is_chapter = false`), не раздел: раздел с чем-либо под
    собой упёрся бы в `fk_position_items_chapter` (RESTRICT) раньше, чем тест
    успел бы дойти до проверяемого поведения — а это была бы уже проверка ДРУГОЙ
    защиты, не биекции.
    """
    victim = db_session.execute(
        sa.select(PositionItem.id)
        .join(PositionItem.proposal)
        .join(Proposal.lot)
        .where(Lot.estimate_id == imported_estimate.id, PositionItem.is_chapter.is_(False))
        .limit(1)
    ).scalar_one()
    db_session.execute(sa.text("DELETE FROM position_items WHERE id = :rid"), {"rid": victim})
    db_session.flush()
    with pytest.raises(CategoryOverrideError) as exc:
        apply_overrides(db_session, imported_estimate.id)
    assert exc.value.code == "mapping_broken"


def test_a_lot_missing_from_the_file_is_refused_loudly(db_session, imported_estimate):
    """Биекция на уровне ЛОТА (спека §2.3, §1.7) — та же биекция, что
    `_require_bijection` держит на уровне строки предложения, только на
    уровень выше и по той же причине. Сегодня недостижимо — импорт вставляет
    ровно один `Lot` на ключ лота файла и обратно ничего не удаляет —
    воспроизводится вычёркиванием ключа лота из `raw_data` в обход домена.

    Без этой проверки лот, оставшийся в БД, но пропавший из `raw_data`,
    молча выпал бы из цикла `_proposals_with_positions`: решение на его
    разделе флешнулось бы, ничего не материализовало и вернуло бы
    `ApplyResult(0, 0, 0)` КАК УСПЕХ — паспорт, правдоподобный ровно
    настолько, насколько неверный.
    """
    db_session.execute(
        sa.text(
            "UPDATE estimate_raw_data SET raw_data = raw_data #- '{lots,lot_1}' "
            "WHERE estimate_id = :eid"
        ),
        {"eid": imported_estimate.id},
    )
    db_session.flush()
    with pytest.raises(CategoryOverrideError) as exc:
        apply_overrides(db_session, imported_estimate.id)
    assert exc.value.code == "mapping_broken"


def _chapter_snapshot(db, estimate_id) -> dict[int, tuple[int | None, str | None]]:
    rows = db.execute(
        sa.text(
            "SELECT pi.id, pi.work_category_id, pi.category_source "
            "FROM position_items pi JOIN proposals p ON p.id = pi.proposal_id "
            "JOIN lots l ON l.id = p.lot_id WHERE l.estimate_id = :eid AND pi.is_chapter"
        ),
        {"eid": estimate_id},
    ).all()
    return {r[0]: (r[1], r[2]) for r in rows}


def _extras_snapshot(db, estimate_id) -> dict[int, int | None]:
    rows = db.execute(
        sa.text(
            "SELECT aw.id, aw.work_category_id FROM estimate_additional_works aw "
            "JOIN proposals p ON p.id = aw.proposal_id JOIN lots l ON l.id = p.lot_id "
            "WHERE l.estimate_id = :eid"
        ),
        {"eid": estimate_id},
    ).all()
    return {r[0]: r[1] for r in rows}
