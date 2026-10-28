"""Решения по строкам КП: чтение для сцены «Против нашей цены» (спека 4б §2.3–§2.6, §2.13).

Четыре таблицы решений — «Договорились», свой комментарий, ручная ступень работы
и ручная ступень семьи системы — читаются одним вызовом `load_decisions` на все
сметы этапа: четыре `SELECT`, сколько бы смет, строк и решений ни было. Автор
решения (идентификатор и почта) приходит соединением с `users`, отдельного запроса
на него нет. Модуль только читает: ни одного `INSERT`/`UPDATE`/`DELETE`.

Снимок ключа ступени работы хранится в `jsonb` без внешних ключей; перевод в
`Hashable`, с которым сравнивает ядро (`LineKey.at`), — пара `snapshot_to_json` /
`snapshot_from_json`. Тексты единиц строк — `load_unit_texts`; строка файла для
комментария собирается `kp_source_line`.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.our_price_load import KpLineRow
from models import (
    EstimateRawData,
    KpFamilyRung,
    KpLineAgreement,
    KpLineComment,
    KpLineRung,
    Lot,
    PositionItem,
    Proposal,
    UnitOfMeasure,
    User,
)
from parser.constants import JSON_KEY_LOTS, JSON_KEY_UNIT
from services.estimate_import import extract_positions, extract_single_proposal
from services.kp_file import KpSourceLine
from services.our_price import Level, Rung, SavedFamilyRung, SavedRungs, SavedWorkRung


@dataclass(frozen=True)
class EstimateDecisions:
    """Решения по одной смете КП."""

    #: position_item_id -> (нетто за единицу, когда, кто, почта)
    agreements: dict[int, tuple[Decimal, datetime, int, str]]
    #: position_item_id -> (текст, когда, кто, почта); пустой текст — тоже решение
    comments: dict[int, tuple[str, datetime, int, str]]
    saved: SavedRungs
    #: ("line", position_item_id) | ("family", family_id) -> (когда, кто, почта)
    rung_meta: dict[tuple[str, int], tuple[datetime, int, str]]


def _by_estimate(model, estimate_ids: Sequence[int], *columns: sa.ColumnElement) -> sa.Select:
    """Строки таблицы решений по строкам КП: смета строки, её столбцы, автор."""
    return (
        sa.select(Lot.estimate_id, model.position_item_id, *columns, model.set_at, model.set_by, User.email)
        .select_from(model)
        .join(PositionItem, PositionItem.id == model.position_item_id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(User, User.id == model.set_by)
        .where(Lot.estimate_id.in_(list(estimate_ids)))
    )


def load_decisions(db: Session, estimate_ids: Sequence[int]) -> dict[int, EstimateDecisions]:
    """Решения всех заданных смет; у сметы без решений — пустой `EstimateDecisions`.

    Ступень семьи собирается по `(смета, семья)`: у второй сметы той же семьи её нет.
    """
    ids = list(estimate_ids)
    agreements: dict[int, dict[int, tuple[Decimal, datetime, int, str]]] = {i: {} for i in ids}
    comments: dict[int, dict[int, tuple[str, datetime, int, str]]] = {i: {} for i in ids}
    work: dict[int, dict[int, SavedWorkRung]] = {i: {} for i in ids}
    family: dict[int, dict[int, SavedFamilyRung]] = {i: {} for i in ids}
    meta: dict[int, dict[tuple[str, int], tuple[datetime, int, str]]] = {i: {} for i in ids}

    for estimate_id, pid, net, at, user_id, email in db.execute(
        _by_estimate(KpLineAgreement, ids, KpLineAgreement.net_price)
    ):
        agreements[estimate_id][pid] = (net, at, user_id, email)
    for estimate_id, pid, text, at, user_id, email in db.execute(_by_estimate(KpLineComment, ids, KpLineComment.text)):
        comments[estimate_id][pid] = (text, at, user_id, email)
    for estimate_id, pid, level, scope, key, at, user_id, email in db.execute(
        _by_estimate(KpLineRung, ids, KpLineRung.level, KpLineRung.scope, KpLineRung.key)
    ):
        work[estimate_id][pid] = SavedWorkRung(Rung(level, scope), snapshot_from_json(key))
        meta[estimate_id][("line", pid)] = (at, user_id, email)
    for estimate_id, family_id, level, scope, parameter_id, at, user_id, email in db.execute(
        sa.select(
            KpFamilyRung.estimate_id,
            KpFamilyRung.work_family_id,
            KpFamilyRung.level,
            KpFamilyRung.scope,
            KpFamilyRung.first_main_parameter_id,
            KpFamilyRung.set_at,
            KpFamilyRung.set_by,
            User.email,
        )
        .join(User, User.id == KpFamilyRung.set_by)
        .where(KpFamilyRung.estimate_id.in_(ids))
    ):
        family[estimate_id][family_id] = SavedFamilyRung(Rung(level, scope), parameter_id)
        meta[estimate_id][("family", family_id)] = (at, user_id, email)

    return {
        i: EstimateDecisions(
            agreements=agreements[i],
            comments=comments[i],
            saved=SavedRungs(work=work[i], family=family[i]),
            rung_meta=meta[i],
        )
        for i in ids
    }


def snapshot_to_json(level: Level, key_value: Hashable) -> dict:
    """Снимок ключа строки на уровне ступени: то, что отдаёт `LineKey.at(level)`."""
    if level == "v":
        return {"level": "v", "variant_id": key_value}
    if level == "p":
        family_id, (parameter_id, value_id) = key_value
        return {"level": "p", "family_id": family_id, "parameter_id": parameter_id, "value_id": value_id}
    return {"level": "f", "family_id": key_value}


def snapshot_from_json(data: Mapping) -> Hashable:
    """Обратный перевод: `Hashable`, который ядро сравнивает с `LineKey.at(level)`."""
    if data["level"] == "v":
        return data["variant_id"]
    if data["level"] == "p":
        return (data["family_id"], (data["parameter_id"], data["value_id"]))
    return data["family_id"]


def load_unit_texts(db: Session, estimate_id: int) -> dict[int, str]:
    """Текст единицы каждой строки (не главы) сметы КП, `position_item_id` -> текст.

    Текст из разбора файла (`estimate_raw_data`) по `(lot_key, position_key_in_proposal)`;
    нет текста — имя единицы из `units_of_measure`; нет и его — пустая строка.
    """
    rows = db.execute(
        sa.select(PositionItem.id, Lot.lot_key, PositionItem.position_key_in_proposal, UnitOfMeasure.name)
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == PositionItem.unit_id)
        .where(Lot.estimate_id == estimate_id, PositionItem.is_chapter.is_(False))
    ).all()
    raw = db.execute(sa.select(EstimateRawData.raw_data).where(EstimateRawData.estimate_id == estimate_id)).scalar_one_or_none()
    positions_of = {
        lot_key: extract_positions(extract_single_proposal(lot)) for lot_key, lot in ((raw or {}).get(JSON_KEY_LOTS) or {}).items()
    }
    texts: dict[int, str] = {}
    for pid, lot_key, position_key, unit_name in rows:
        unit = positions_of.get(lot_key, {}).get(position_key, {}).get(JSON_KEY_UNIT)
        file_text = unit.strip() if isinstance(unit, str) else ""
        texts[pid] = file_text or unit_name or ""
    return texts


def kp_source_line(row: KpLineRow, unit_text: str) -> KpSourceLine:
    """Строка КП в форме, которую берёт комментарий и файл. Номер — как в файле
    (`item_number_in_proposal`), без подмены ключом позиции."""
    return KpSourceLine(
        position_item_id=row.line.position_item_id,
        is_chapter=False,
        is_system=row.line.is_system,
        number=row.item_number,
        title=row.title,
        unit_text=unit_text,
        quantity=row.quantity,
        unit_price=row.line.unit_price,
        total=row.line.total,
    )
