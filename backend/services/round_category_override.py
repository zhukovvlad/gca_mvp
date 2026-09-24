"""Раундовая запись решений о статье раздела (спека этапного разноса §2.4).

Единица — логический раздел `(lot_key, position_key_in_proposal)`, радиус —
все offer-сметы раунда. `set_override` по-сметного сервиса НЕ переиспользуется:
его no-op сохранил бы старых авторов там, где содержимое совпало, и аудит
раунда остался бы разнородным (§1.3). Решения пишутся здесь, пересчёт — тот
же `apply_overrides`, что у по-сметного маршрута.

Порядок: блокировки tender → round → сметы по `estimate_id ASC` (§2.4 п.1) →
разрешение ключа во ВСЕХ сметах по таблице §2.4 п.2 → предикат no-op → запись
→ пересчёт каждой сметы. Транзакцию ведёт роутер.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError
from crud.round_unallocated import (
    CODE_NO_OFFER_ESTIMATES,
    CODE_ROUND_NOT_FOUND,
    CODE_TENDER_NOT_FOUND,
    NO_OFFER_ESTIMATES_MESSAGE,
    RoundMappingBroken,
    offer_estimates,
    require_round,
)
from models import EstimateCategoryOverride, Lot, PositionItem, Proposal, Tender, TenderRound, WorkCategory
from services.category_override import ApplyResult, CategoryOverrideError, apply_overrides, lock_estimate

CODE_SECTION_NOT_FOUND = "section_not_found"
CODE_CATEGORY_NOT_FOUND = "category_not_found"
CODE_NOT_A_CHAPTER = "not_a_chapter"
CODE_STRUCTURE_DISABLED = "structure_disabled"


def _lock_scope(db: Session, tender_id: int, round_id: int) -> list[int]:
    """tender FOR KEY SHARE → round FOR KEY SHARE → offer-сметы FOR UPDATE по
    `estimate_id ASC`. KEY SHARE на тендере совместим с параллельным раундовым
    писателем — этого мы и добиваемся; сериализация с соседями происходит НИЖЕ,
    и точное место у каждого своё (уточнено финальным ревью ветки, первая
    редакция валила всех троих в одну кучу «конфликтует по тендеру»):

    - `import_round` (загрузка и замена файла раунда) берёт тендер
      `FOR NO KEY UPDATE`, то есть с нашим KEY SHARE он СОВМЕСТИМ; расходимся мы
      на строке раунда `FOR UPDATE` и на строках смет;
    - `delete_round` берёт тендер тем же `FOR NO KEY UPDATE` (долг №26 — его
      докстрока называет это «FOR KEY SHARE»), и снова разводит нас раунд;
    - `delete_participant` строку раунда не трогает вовсе — с ним мы
      встречаемся только на сметах.

    После их коммита смет может не остаться — это
    `round_has_no_offer_estimates` (§1.5)."""
    rnd = require_round(db, tender_id, round_id)
    # ИМЕННО `read=True, key_share=True`: это компилируется в `FOR KEY SHARE`.
    # Одно `key_share=True` даёт `FOR NO KEY UPDATE` (проверено компиляцией под
    # диалект PostgreSQL — находка внешнего ревью плана), а он НЕсовместим сам
    # с собой — второй раундовый писатель встал бы уже на тендере, и блокировки
    # смет ниже перестали бы быть тем, что его держит.
    #
    # `require_round` читает БЕЗ лока — между этим чтением и блокирующим
    # SELECT ниже строка может исчезнуть (параллельное каскадное удаление).
    # `scalar_one_or_none()` вместо `scalar_one()` — гонка обязана дать ТОТ ЖЕ
    # 404 с тем же кодом, что дал бы `require_round` на свежем чтении, а не
    # уронить `NoResultFound` в непойманный 500 (спека §2.4: HTTP-таблица
    # обещает пользователю 404, а не поломку).
    tender_locked = db.execute(
        sa.select(Tender.id).where(Tender.id == tender_id).with_for_update(read=True, key_share=True)
    ).scalar_one_or_none()
    if tender_locked is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.", code=CODE_TENDER_NOT_FOUND)
    round_locked = db.execute(
        sa.select(TenderRound.id).where(TenderRound.id == rnd.id).with_for_update(read=True, key_share=True)
    ).scalar_one_or_none()
    if round_locked is None:
        raise DomainError(404, f"Раунд {round_id} тендера {tender_id} не найден.", code=CODE_ROUND_NOT_FOUND)
    ids = [e.id for e in offer_estimates(db, round_id)]      # ORDER BY Estimate.id — единственный источник порядка
    if not ids:
        raise DomainError(404, NO_OFFER_ESTIMATES_MESSAGE, code=CODE_NO_OFFER_ESTIMATES)
    for estimate_id in ids:
        try:
            lock_estimate(db, estimate_id)
        except CategoryOverrideError as exc:     # смета исчезла под нашим KEY SHARE — недостижимо
            raise RoundMappingBroken(str(exc)) from exc
    return ids


def _resolve_key(db: Session, estimate_ids: list[int], lot_key: str, position_key: str) -> dict[int, PositionItem]:
    """Таблица исходов §2.4 п.2 — разбиение полное."""
    rows = db.execute(
        sa.select(Lot.estimate_id, PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids), Lot.lot_key == lot_key,
               PositionItem.position_key_in_proposal == position_key)
    ).all()
    # Дубликат `estimate_id` (последняя строка молча накрыла бы предыдущую)
    # недостижим: `uq_lots_estimate_lot_key` даёт не больше одного лота на
    # `(estimate_id, lot_key)`, `uq_proposals_lot_id` — не больше одного
    # предложения на лот, `uq_position_items_proposal_id_key` — не больше
    # одной строки на `(proposal_id, position_key_in_proposal)`; втроём они
    # держат не больше одной строки на `(estimate_id, lot_key, position_key)`.
    found = {estimate_id: row for estimate_id, row in rows}
    if not found:
        raise DomainError(404, f"Раздел «{lot_key}/{position_key}» не найден ни в одной смете раунда.",
                          code=CODE_SECTION_NOT_FOUND)
    if set(found) != set(estimate_ids):
        raise RoundMappingBroken(f"Ключ «{lot_key}/{position_key}» есть в сметах {sorted(found)} и отсутствует в "
                                 f"{sorted(set(estimate_ids) - set(found))}: проекции раунда разошлись.")
    kinds = {row.is_chapter for row in found.values()}
    if len(kinds) > 1:
        raise RoundMappingBroken(f"Ключ «{lot_key}/{position_key}»: is_chapter различается между сметами раунда.")
    if kinds == {False}:
        raise DomainError(422, f"Строка «{lot_key}/{position_key}» не является разделом: статья привязывается "
                               "только к строкам-разделам.", code=CODE_NOT_A_CHAPTER)
    return found


def _recompute(db: Session, estimate_ids: list[int]) -> ApplyResult:
    chapters = extras = manual = 0
    for estimate_id in estimate_ids:
        try:
            r = apply_overrides(db, estimate_id, already_locked=True)
        except CategoryOverrideError as exc:
            if exc.code == "structure_disabled":
                raise DomainError(409, str(exc), code=CODE_STRUCTURE_DISABLED) from exc
            raise RoundMappingBroken(str(exc)) from exc     # mapping_broken, отсутствие raw_data
        chapters += r.chapters_updated
        extras += r.additional_works_updated
        manual += r.chapters_manual

    # `membership_state` (спека §2.5) приводит `apply_overrides` в цикле выше —
    # по всем позициям каждой сметы раунда; эффективная статья позиции
    # зависит только от разделов её же предложения, поэтому отдельного
    # приведения по раунду здесь нет.
    return ApplyResult(chapters, extras, manual)


def set_round_override(db: Session, *, tender_id: int, round_id: int, lot_key: str,
                       position_key_in_proposal: str, work_category_id: int, note: str | None,
                       user_id: int) -> ApplyResult:
    ids = _lock_scope(db, tender_id, round_id)
    # Порядок между разрешением ключа и проверкой статьи спекой НЕ зафиксирован
    # (§2.4 перечисляет оба исхода, не фиксируя очерёдность) — оставлен как
    # есть НАМЕРЕННО (решение по ревью): ключ раньше статьи держит PUT и
    # DELETE одинаковыми через шаги 1-2, а «DELETE симметричен через шаги 1-2»
    # у спеки как раз об этом. Тем же порядком `not_a_chapter` (внутри
    # `_resolve_key`) тоже предшествует `category_not_found`.
    chapters = _resolve_key(db, ids, lot_key, position_key_in_proposal)
    if db.execute(sa.select(WorkCategory.id).where(WorkCategory.id == work_category_id)).scalar_one_or_none() is None:
        raise DomainError(404, f"Статья {work_category_id} не найдена в классификаторе.", code=CODE_CATEGORY_NOT_FOUND)
    current = [db.get(EstimateCategoryOverride, chapters[eid].id) for eid in ids]
    vectors = [None if o is None else (o.work_category_id, o.note, o.assigned_by, o.assigned_at) for o in current]
    # Предикат no-op — ОБА условия (§2.4 п.3): векторы одинаковы между собой И
    # их (статья, заметка) совпадают с телом. Решения и аудит не трогаются;
    # пересчёт идёт всегда, как у по-сметного `clear_override`.
    if all(v is not None for v in vectors) and len(set(vectors)) == 1 and vectors[0][:2] == (work_category_id, note):
        return _recompute(db, ids)
    for eid, existing in zip(ids, current, strict=True):
        if existing is None:
            db.add(EstimateCategoryOverride(position_item_id=chapters[eid].id, work_category_id=work_category_id,
                                            note=note, assigned_by=user_id, assigned_at=sa.func.now()))
        else:
            existing.work_category_id = work_category_id
            existing.note = note
            existing.assigned_by = user_id
            existing.assigned_at = sa.func.now()    # одно now() транзакции на все сметы — единый аудит
    db.flush()
    return _recompute(db, ids)


def clear_round_override(db: Session, *, tender_id: int, round_id: int, lot_key: str,
                         position_key_in_proposal: str) -> ApplyResult:
    """DELETE симметричен PUT через шаги 1-2 (§2.4): те же блокировки, то же
    разрешение ключа ДО предиката no-op.

    Нет отдельной ветки «нет решения нигде — не трогать БД и вернуть нулевой
    `ApplyResult` без пересчёта»: она и текущий код (снять решение, если оно
    есть, затем ВСЕГДА пересчитать) наблюдаемо неразличимы — оба не пишут ни
    решений, ни аудита, когда решения не было ни в одной смете, и пересчёт по
    неизменному набору решений идемпотентен (та же логика, что у по-сметного
    `clear_override`: «пересчёт выполняется ВСЕГДА, даже когда снимать было
    нечего»). Заводить вторую копию условия ради пропуска идемпотентного
    пересчёта незачем.
    """
    ids = _lock_scope(db, tender_id, round_id)
    chapters = _resolve_key(db, ids, lot_key, position_key_in_proposal)   # ДО no-op (§2.4, коллизия ревью гейта 2)
    for eid in ids:
        existing = db.get(EstimateCategoryOverride, chapters[eid].id)
        if existing is not None:
            db.delete(existing)
    db.flush()
    return _recompute(db, ids)
