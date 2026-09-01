"""Чтение для этапного разноса (спека этапного разноса §2.2-§2.3, §2.5-§2.6):
радиус раунда, вход агрегатора, диагностика, тело GET, счётчик карточки.

Радиус - offer-сметы раунда СОЕДИНЕНИЕМ с `offers` (§2.1): baseline
(`estimates.round_id`) в выборку не попадает по построению, а не фильтром.
Структура и `rows` - из `_section_metrics` паспорта: расчёт ОДИН на оба
потребителя, второй разошёлся бы с первым (спека разноса §5.2-5.3).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from crud.common import DomainError
from crud.project_passport import _section_metrics  # ОДИН расчёт структуры на паспорт и раунд
from models import (
    Contractor,
    Estimate,
    EstimateCategoryOverride,
    Lot,
    Offer,
    OfferPackage,
    PositionItem,
    Proposal,
    Tender,
    TenderRound,
    User,
)
from services import round_unallocated as ru

CODE_TENDER_NOT_FOUND = "tender_not_found"
CODE_ROUND_NOT_FOUND = "round_not_found"
CODE_NO_OFFER_ESTIMATES = "round_has_no_offer_estimates"
NO_OFFER_ESTIMATES_MESSAGE = "Раунд или его сметы больше недоступны."


class RoundMappingBroken(Exception):
    """Проекции одного файла разошлись (ключ не во всех сметах либо разный
    `is_chapter`) - порча НАШИХ данных: роутер логирует и отдаёт 500,
    пользователю повторять нечего (§2.2, §2.4)."""


@dataclass(frozen=True)
class RoundScope:
    tender: Tender
    round: TenderRound
    estimates: list[Estimate]
    contractor_title: dict[int, str]


def require_round(db: Session, tender_id: int, round_id: int) -> TenderRound:
    """Как `crud.tenders.get_round`, но с КОДАМИ (§2.3): существующий
    `get_round` без кода не правится - его 404 менять нельзя."""
    if db.get(Tender, tender_id) is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.", code=CODE_TENDER_NOT_FOUND)
    rnd = db.execute(
        sa.select(TenderRound).where(TenderRound.id == round_id, TenderRound.tender_id == tender_id)
    ).scalar_one_or_none()
    if rnd is None:
        raise DomainError(404, f"Раунд {round_id} тендера {tender_id} не найден.", code=CODE_ROUND_NOT_FOUND)
    return rnd


def offer_estimates(db: Session, round_id: int) -> list[Estimate]:
    """Радиус §2.1: offer-сметы раунда, `estimate_id ASC` - этот же порядок
    задаёт представительную смету и порядок блокировок писателя."""
    return list(
        db.execute(
            sa.select(Estimate).join(Offer, Offer.id == Estimate.offer_id)
            .where(Offer.round_id == round_id).order_by(Estimate.id)
        ).scalars().all()
    )


def load_scope(db: Session, tender_id: int, round_id: int) -> RoundScope:
    rnd = require_round(db, tender_id, round_id)
    tender = db.get(Tender, tender_id)
    estimates = offer_estimates(db, round_id)
    if not estimates:
        raise DomainError(404, NO_OFFER_ESTIMATES_MESSAGE, code=CODE_NO_OFFER_ESTIMATES)
    titles = dict(
        db.execute(
            sa.select(Estimate.id, Contractor.title)
            .join(Offer, Offer.id == Estimate.offer_id)
            .join(OfferPackage, OfferPackage.id == Offer.package_id)
            .join(Contractor, Contractor.id == OfferPackage.contractor_id)
            .where(Estimate.id.in_([e.id for e in estimates]))
        ).all()
    )
    return RoundScope(tender=tender, round=rnd, estimates=list(estimates), contractor_title=titles)


def _chapter_rows(db: Session, estimate_ids: list[int]):
    """Все строки-разделы радиуса с их решениями и авторами - ОДНИМ запросом."""
    author = aliased(User)
    return db.execute(
        sa.select(
            Lot.estimate_id, Lot.lot_key, PositionItem.id, PositionItem.position_key_in_proposal,
            PositionItem.work_category_id,
            EstimateCategoryOverride.work_category_id.label("decided_category_id"),
            EstimateCategoryOverride.note, EstimateCategoryOverride.assigned_by,
            EstimateCategoryOverride.assigned_at, author.email.label("assigned_by_email"),
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(EstimateCategoryOverride, EstimateCategoryOverride.position_item_id == PositionItem.id)
        .outerjoin(author, author.id == EstimateCategoryOverride.assigned_by)
        .where(Lot.estimate_id.in_(estimate_ids), PositionItem.is_chapter.is_(True))
    ).all()


def representative_nodes(db: Session, estimate: Estimate) -> list[ru.ChapterNode]:
    """Структура и `rows` - из `_section_metrics` представительной сметы.

    Порядок - ЧИСТЫЙ файловый порядок (лот по `proposal_id`, затем числовой
    порядок ключа позиции), БЕЗ группировки по глубине. Это НЕ тот же порядок,
    что отдаёт `_unallocated_sections` паспорта: та функция сортирует сначала
    по `depth` (все переподвешенные корни раньше всех их потомков) и только
    внутри уровня - файловым порядком, что на этой же ведомости даёт
    `14, 15, 14.1, 14.3`. Здесь сортировки по глубине нет вовсе, и на той же
    ведомости получается `14, 14.1, 14.3, 15` - порядок ведомости файла, как
    того требует спека этапного разноса §2.3. Совпадение с паспортом на
    плоских деревьях - случайность формы, а не тождество: не "чинить" одно
    под другое.

    Предположение, не охраняемое кодом: `key = (lot_key, position_key_in_proposal)`
    уникален внутри сметы, потому что раундовая проекция несёт РОВНО ОДНО
    предложение на лот (`uq_proposals_lot_id`, `AGENTS.md` §4) - `position_key_
    in_proposal` сам по себе уникален только В ПРЕДЕЛАХ ОДНОГО предложения
    (`uq_position_items_proposal_id_key`), и два предложения под одним лотом
    молча схлопнули бы разные строки в один ключ.
    """
    metrics = _section_metrics(db, estimate.id)
    lot_key_of = dict(
        db.execute(
            sa.select(Proposal.id, Lot.lot_key).join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == estimate.id)
        ).all()
    )
    key_of = {pid: (lot_key_of[m.proposal_id], m.position_key_in_proposal) for pid, m in metrics.items()}
    ordered = sorted(
        metrics.items(),
        key=lambda kv: (kv[1].proposal_id, len(kv[1].position_key_in_proposal), kv[1].position_key_in_proposal),
    )
    return [
        ru.ChapterNode(
            key=key_of[pid],
            file_parent=None if m.parent_position_item_id is None else key_of[m.parent_position_item_id],
            number=m.number, title=m.title, smr_article_raw=m.smr_article_raw, rows=m.rows,
        )
        for pid, m in ordered
    ]


def load_states(db: Session, scope: RoundScope) -> list[ru.SectionAggregate]:
    """Вход агрегатора §2.2 из БД: структура представительной сметы (первая по
    `estimate_id ASC`) плюс векторы решений всех offer-смет радиуса.

    Расхождение набора логических ключей между сметами радиуса -
    `RoundMappingBroken` (§2.2, §2.4): проекции одного файла разошлись. Флип
    `is_chapter` попадает в ту же проверку без отдельной ветки - строка,
    переставшая (или ставшая) быть разделом в одной из смет, просто исчезает
    (или появляется) в её наборе `is_chapter=True`-ключей, и множества
    перестают совпадать с представительной сметой.
    """
    ids = [e.id for e in scope.estimates]
    rows = _chapter_rows(db, ids)
    keys_by_estimate: dict[int, set[ru.SectionKey]] = {eid: set() for eid in ids}
    vectors: dict[ru.SectionKey, dict[int, ru.Vector | None]] = defaultdict(dict)
    missing: dict[ru.SectionKey, bool] = defaultdict(bool)
    for r in rows:
        key = (r.lot_key, r.position_key_in_proposal)
        keys_by_estimate[r.estimate_id].add(key)
        missing[key] = missing[key] or r.work_category_id is None
        vectors[key][r.estimate_id] = (
            None if r.decided_category_id is None
            else ru.Vector(r.decided_category_id, r.note, r.assigned_by, r.assigned_at)
        )
    reference = keys_by_estimate[ids[0]]
    for eid, keys in keys_by_estimate.items():
        if keys != reference:
            raise RoundMappingBroken(
                f"Раунд {scope.round.id}: набор разделов сметы {eid} не совпадает с представительной "
                f"сметой {ids[0]} (только в смете {eid}: {sorted(keys - reference)[:5]}; только в "
                f"представительной смете {ids[0]}: {sorted(reference - keys)[:5]}). Проекции одного "
                "файла разошлись."
            )
    nodes = representative_nodes(db, scope.estimates[0])
    ordered_vectors = {k: [vectors[k].get(eid) for eid in ids] for k in vectors}
    return ru.aggregate(nodes, missing, ordered_vectors)
