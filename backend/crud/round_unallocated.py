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

from crud.common import DomainError, iso
from crud.project_passport import _category_options, _section_metrics  # ОДИН расчёт структуры на паспорт и раунд
from models import (
    Contractor,
    Estimate,
    EstimateAdditionalWork,
    EstimateCategoryOverride,
    EstimateRawData,
    Lot,
    Offer,
    OfferPackage,
    PositionItem,
    Proposal,
    Tender,
    TenderRound,
    User,
    WorkCategory,
)
from parser.constants import JSON_KEY_LOTS
from services import round_unallocated as ru
from services.additional_works import REASON_CANDIDATE_WITHOUT_ARTICLE, categories_by_chapter_number, resolve_ref
from services.category_override import _overrides_of, _rows_of
from services.category_resolution import CategoryResolver, RowKind
from services.category_rollup import CategoryRef
from services.estimate_import import extract_positions, extract_single_proposal

CODE_TENDER_NOT_FOUND = "tender_not_found"
CODE_ROUND_NOT_FOUND = "round_not_found"
CODE_NO_OFFER_ESTIMATES = "round_has_no_offer_estimates"
NO_OFFER_ESTIMATES_MESSAGE = "Раунд или его сметы больше недоступны."

#: Три границы §5.5 спеки разноса (`docs/superpowers/specs/2026-08-11-
#: unallocated-category-override-design.md`), поимённо (спека этапного
#: разноса §2.5): позиции вне структуры файла, предложения с погашенной
#: привязкой по структуре, допработы без статьи, чья ссылка не разрешается.
DIAG_OUTSIDE_STRUCTURE = "outside_structure"
DIAG_STRUCTURE_DISABLED = "structure_disabled"
DIAG_UNRESOLVED_REF = "unresolved_chapter_ref"


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


def diagnostics(db: Session, scope: RoundScope) -> list[dict]:
    """Границы §5.5 спеки разноса поимённо (спека этапного разноса §2.5).

    План резолва — ТОТ ЖЕ, что у пересчёта (`apply_overrides`,
    `services/category_override.py`), и идёт теми же шагами, в том же
    порядке: позиции лота из `estimate_raw_data.raw_data` тем же
    `extract_single_proposal`/`extract_positions`, что и там; строки и
    действующие решения предложения тем же `_rows_of`/`_overrides_of`; тот же
    `CategoryResolver.resolve_proposal(positions, overrides)`. Расхождение с
    этим планом (например, доверие материализованному `work_category_id` без
    пересчёта причины отказа) и есть тот приближённый селектор, ради снятия
    которого написана эта функция (§1.4 спеки этапного разноса): допработа с
    исходом «кандидат без статьи» разносом раздела закрывается и сюда не
    попадает, а «нет кандидатов», «статьи различаются» и строка без ссылки
    вовсе — границы.

    `unresolved_chapter_ref` НИКОГДА не полагается на уже материализованный
    `work_category_id`: причина отказа всегда пересчитывается свежим
    `resolve_ref` (ревью после первого прохода задачи — доверие столбцу без
    пересчёта и есть тот приближённый селектор, которого эта функция обязана
    избегать; на согласованных данных оба пути совпадают, поэтому расхождение
    видно только на устаревших — ровно та подмена, которую нельзя ловить
    молчанием).

    Погашенная структура (`structure_disabled`) — особый случай для допработ,
    не только для самой структурной границы: при погашенной структуре
    `categories_by_chapter_number` не отдаёт НИ ОДНОЙ настоящей статьи (все
    кандидаты — `None`, см. `_disabled()` в `category_resolution.py`), поэтому
    ЛЮБАЯ непустая ссылка сравнением через `resolve_ref` ушла бы в «кандидат
    без статьи» и была бы ошибочно исключена — а разнос раздела здесь в
    принципе невозможен, потому что разделов, годных для решения, нет вовсе
    (§2.5, замер §1.2 спеки: строки АНТТЕК/ЕНИГЮН на 316,1 млн — именно такие
    допработы). Поэтому под погашенной структурой сравнение причины не
    вызывается: каждая непривязанная допработа — граница по определению.
    `outside_structure`, наоборот, для погашенной структуры не отдаётся вовсе:
    её `rows` там же, что и `structure_disabled.rows` (все НЕ-разделы), и
    вторая запись задвоила бы счёт одних и тех же строк.

    Диагностика НЕ проверяет биекцию `raw_data` ↔ `lots` (в отличие от
    `_require_lot_bijection`/`_require_bijection` в `category_override.py`):
    лот, который есть в БД, но пропал из `raw_data`, дал бы здесь пустые
    `positions` и тихую пустую диагностику вместо `mapping_broken`/500,
    которого требует спека; смета без `estimate_raw_data` уронила бы
    `scalar_one()` непойманным `NoResultFound` вместо типизированной ошибки,
    которую отдаёт `apply_overrides`. Сегодня оба случая недостижимы ТОЙ ЖЕ
    гарантией, которую документирует `_require_lot_bijection` (импорт создаёт
    ровно один `Lot` на ключ лота файла и никогда не удаляет обратно) — здесь
    оставлен только этот комментарий, без охраны: третья копия одной и той же
    проверки (`load_states`, `apply_overrides`, здесь) не входит в объём этой
    задачи.

    Гранулярность (решение плана, гейт 3): `outside_structure` и
    `structure_disabled` — ОДНА запись на предложение (`rows` — число НЕ-
    разделов файла позади этой границы: и позиции, и строки вне структуры —
    при погашенной структуре это ЛЮБАЯ не-раздельная строка, потому что
    границей становится предложение целиком), `unresolved_chapter_ref` — по
    строке допработ (`rows` всегда 1). Порядок: сметы радиуса по
    `estimate_id ASC` (несёт `scope.estimates`), внутри сметы — лоты по
    `proposal_id`, внутри лота — сперва структурная граница (если есть),
    затем допработы по `ordinal`.
    """
    resolver = CategoryResolver.from_db(db)
    out: list[dict] = []
    for estimate in scope.estimates:
        title = scope.contractor_title[estimate.id]
        raw = db.execute(
            sa.select(EstimateRawData.raw_data).where(EstimateRawData.estimate_id == estimate.id)
        ).scalar_one()
        lots = db.execute(
            sa.select(Lot.lot_key, Lot.lot_title, Proposal.id)
            .join(Proposal, Proposal.lot_id == Lot.id)
            .where(Lot.estimate_id == estimate.id)
            .order_by(Proposal.id)
        ).all()
        for lot_key, lot_title, proposal_id in lots:
            positions = extract_positions(extract_single_proposal((raw.get(JSON_KEY_LOTS) or {}).get(lot_key)))
            _rows, ids_by_key = _rows_of(db, proposal_id)
            resolution = resolver.resolve_proposal(positions, _overrides_of(db, ids_by_key))
            extras = db.execute(
                sa.select(EstimateAdditionalWork).where(EstimateAdditionalWork.proposal_id == proposal_id)
                .order_by(EstimateAdditionalWork.ordinal)
            ).scalars().all()
            if resolution.structure_disabled:
                out.append({
                    "code": DIAG_STRUCTURE_DISABLED, "contractor_title": title, "title": lot_title,
                    "rows": sum(1 for r in resolution.rows.values() if r.kind is not RowKind.CHAPTER),
                })
                # Структура погашена: разнос раздела здесь невозможен в принципе
                # (нет ни одного раздела, годного для решения), поэтому причина
                # `resolve_ref` не сравнивается вовсе — сравнение «кандидат без
                # статьи» имело бы смысл только там, где разнос МОГ БЫ закрыть
                # ссылку впоследствии. Каждая непривязанная допработа — граница.
                for extra in extras:
                    if extra.work_category_id is None:
                        out.append({
                            "code": DIAG_UNRESOLVED_REF, "contractor_title": title,
                            "title": extra.title, "rows": 1,
                        })
                continue
            if resolution.counters.rows_outside_structure:
                out.append({
                    "code": DIAG_OUTSIDE_STRUCTURE, "contractor_title": title, "title": lot_title,
                    "rows": resolution.counters.rows_outside_structure,
                })
            by_number = categories_by_chapter_number(positions, resolution)
            for extra in extras:
                category_id, reason = resolve_ref(extra.chapter_ref_raw, by_number)
                if category_id is None and reason != REASON_CANDIDATE_WITHOUT_ARTICLE:
                    out.append({
                        "code": DIAG_UNRESOLVED_REF, "contractor_title": title,
                        "title": extra.title, "rows": 1,
                    })
    return out


def _category_refs(db: Session) -> list[CategoryRef]:
    """Тот же список, что собирает `get_project_passport` перед `_category_options`."""
    return [
        CategoryRef(id=c.id, code=c.code, title=c.title, parent_id=c.parent_id,
                    is_bucket=c.is_bucket, sort_order=c.sort_order)
        for c in db.execute(sa.select(WorkCategory)).scalars().all()
    ]


def _section_json(a: ru.SectionAggregate, refs_by_id: dict[int, CategoryRef]) -> dict:
    """Тело одного раздела §2.3: `partial`/`conflict` — дискриминированный
    union, ключ есть ТОЛЬКО у своего состояния (`resolved` сюда не попадает —
    вызывающий код отфильтровывает его для `manual`). Денег нет нигде."""
    c = a.classification
    body = {
        "lot_key": a.node.key[0], "position_key_in_proposal": a.node.key[1],
        "parent_key": None if a.parent_key is None else list(a.parent_key),
        "depth": a.depth, "number": a.node.number, "title": a.node.title,
        "smr_article_raw": a.node.smr_article_raw, "rows": a.node.rows, "state": c.state,
    }
    if c.state == ru.STATE_PARTIAL:
        body["partial"] = {"assigned": c.assigned, "total": c.total, "notes": list(c.notes)}
    elif c.state == ru.STATE_CONFLICT:
        body["conflict"] = {
            "categories": [{"id": i, "code": refs_by_id[i].code, "title": refs_by_id[i].title} for i in c.categories],
            "notes": list(c.notes), "audit_differs": c.audit_differs,
        }
    return body


def _manual_json(a: ru.SectionAggregate, refs_by_id: dict[int, CategoryRef], email_of: dict[int, str]) -> dict:
    """Тело одной строки `manual` §2.3 — состояние `resolved`. Все векторы
    раздела равны (`classify`: `resolved` требует `assigned == total` и
    единственное различное значение среди присутствующих), поэтому
    `a.vectors[0]` — законный представитель: индекс не привилегирован, просто
    любой слот годится. `None` в этом слоте недостижимо для `resolved` (в этом
    состоянии `assigned == total`, то есть все слоты заполнены) — но если бы
    порча агрегатора всё же протащила его сюда, `v.work_category_id` упал бы
    голым `AttributeError` на `None`, не тихой подменой."""
    v = a.vectors[0]          # resolved: один вектор во всех
    return {
        "lot_key": a.node.key[0], "position_key_in_proposal": a.node.key[1],
        "number": a.node.number, "title": a.node.title, "rows": a.node.rows,
        "work_category_id": v.work_category_id, "category_code": refs_by_id[v.work_category_id].code,
        "category_title": refs_by_id[v.work_category_id].title,
        "assigned_by_email": email_of[v.assigned_by], "assigned_at": iso(v.assigned_at), "note": v.note,
    }


def pending_sections_count(db: Session, round_id: int) -> int | None:
    """Счётчик карточки (спека этапного разноса §2.6) — ТЕМ ЖЕ агрегатором,
    что GET §2.3 (`load_states`, вызванный по имени модуля - не вторая
    формула): `None` только когда у раунда нет offer-смет (триггер экрана не
    рисуется вовсе); иначе число логических разделов радиуса в состояниях
    `PENDING_STATES` (`unassigned`/`partial`/`conflict`). `contractor_title={}`
    у `RoundScope` — безопасное сокращение: `load_states` этого поля не
    читает вовсе (оно нужно только `diagnostics`/`_manual_json` тела GET)."""
    estimates = offer_estimates(db, round_id)
    if not estimates:
        return None
    rnd = db.get(TenderRound, round_id)
    scope = RoundScope(tender=db.get(Tender, rnd.tender_id), round=rnd, estimates=list(estimates), contractor_title={})
    return sum(1 for a in load_states(db, scope) if a.classification.state in ru.PENDING_STATES)


def build_round_unallocated(db: Session, tender_id: int, round_id: int) -> dict:
    """Тело GET §2.3: разделы, требующие решения, разнесённые вручную,
    диагностика границ §5.5 и справочник статей целиком."""
    scope = load_scope(db, tender_id, round_id)
    states = load_states(db, scope)
    refs = _category_refs(db)
    refs_by_id = {r.id: r for r in refs}
    authors = {a.vectors[0].assigned_by for a in states if a.classification.state == ru.STATE_RESOLVED}
    email_of = dict(db.execute(sa.select(User.id, User.email).where(User.id.in_(authors or [-1]))).all())
    rnd = scope.round
    return {
        "round": {"id": rnd.id, "stage_no": rnd.stage_no, "label": rnd.label, "held_on": iso(rnd.held_on)},
        "offers_count": len(scope.estimates),
        "sections": [_section_json(a, refs_by_id) for a in states if a.classification.state != ru.STATE_RESOLVED],
        "manual": [_manual_json(a, refs_by_id, email_of) for a in states if a.classification.state == ru.STATE_RESOLVED],
        "diagnostics": diagnostics(db, scope),
        "category_options": _category_options(refs),
    }
