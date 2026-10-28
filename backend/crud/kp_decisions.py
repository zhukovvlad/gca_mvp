"""Решения по строкам КП: чтение для сцены «Против нашей цены» (спека 4б §2.3–§2.6, §2.13).

Четыре таблицы решений — «Договорились», свой комментарий, ручная ступень работы
и ручная ступень семьи системы — читаются одним вызовом `load_decisions` на все
сметы этапа: четыре `SELECT`, сколько бы смет, строк и решений ни было. Автор
решения (идентификатор и почта) приходит соединением с `users`, отдельного запроса
на него нет. Запись решений — `put_*`/`delete_*` ниже: они флешат, транзакцию ведёт
роутер.

Снимок ключа ступени работы хранится в `jsonb` без внешних ключей; перевод в
`Hashable`, с которым сравнивает ядро (`LineKey.at`), — пара `snapshot_to_json` /
`snapshot_from_json`. Тексты единиц строк — `load_unit_texts`; строка файла для
комментария собирается `kp_source_line`.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from crud.common import DomainError
from crud.our_price_load import KpLineRow, kp_vat_bases, load_kp_lines
from models import (
    KP_COMMENT_MAX,
    Estimate,
    EstimateRawData,
    KpFamilyRung,
    KpLineAgreement,
    KpLineComment,
    KpLineRung,
    Lot,
    Offer,
    PositionItem,
    Proposal,
    TenderRound,
    UnitOfMeasure,
    User,
)
from money.price import is_price
from money.vat import gross_to_net
from parser.constants import JSON_KEY_LOTS, JSON_KEY_UNIT
from services.estimate_import import extract_positions, extract_single_proposal
from services.kp_file import KpSourceLine
from services.our_price import RUNG_ORDER, Level, Rung, SavedFamilyRung, SavedRungs, SavedWorkRung

CODE_INVALID_AGREED = "invalid_agreed"
CODE_COMMENT_TOO_LONG = "comment_too_long"
CODE_RUNG_EMPTY = "rung_empty"
CODE_NO_LADDER = "no_ladder"
CODE_UNKNOWN_VAT_BASE = "unknown_vat_base"
#: Код отказа панели строки и `PUT …/rung`: ступень не названа целиком, неизвестна
#: или недоступна строке.
CODE_UNKNOWN_RUNG = "unknown_rung"


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
    raw = db.execute(
        sa.select(EstimateRawData.raw_data).where(EstimateRawData.estimate_id == estimate_id)
    ).scalar_one_or_none()
    positions_of = {
        lot_key: extract_positions(extract_single_proposal(lot))
        for lot_key, lot in ((raw or {}).get(JSON_KEY_LOTS) or {}).items()
    }
    texts: dict[int, str] = {}
    for pid, lot_key, position_key, unit_name in rows:
        unit = positions_of.get(lot_key, {}).get(position_key, {}).get(JSON_KEY_UNIT)
        file_text = unit.strip() if isinstance(unit, str) else ""
        texts[pid] = file_text or unit_name or ""
    return texts


def load_file_skeleton(db: Session, estimate_id: int) -> tuple[dict[int, int], list[KpSourceLine]]:
    """Порядок и главы сметы КП для файла: один запрос по всем её строкам.

    Возвращает лот каждой строки, `position_item_id -> lots.id` (главы и позиции), и главы как
    строки-разделы: номер и название. Позиции сцена уже несёт; здесь они нужны только местом в
    порядке `(лот, id)`.
    """
    rows = db.execute(
        sa.select(
            PositionItem.id,
            Lot.id,
            PositionItem.is_chapter,
            PositionItem.chapter_number_in_proposal,
            PositionItem.job_title_in_proposal,
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id)
    ).all()
    lot_of = {pid: lot_id for pid, lot_id, *_rest in rows}
    chapters = [
        KpSourceLine(
            position_item_id=pid,
            is_chapter=True,
            is_system=False,
            number=number,
            title=title,
            unit_text="",
            quantity=None,
            unit_price=None,
            total=None,
        )
        for pid, _lot_id, is_chapter, number, title in rows
        if is_chapter
    ]
    return lot_of, chapters


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


# --- запись решений (спека 4б §2.3–§2.6, §2.11) -------------------------------------


def parse_agreed(text: str) -> Decimal:
    """Цена «Договорились» из строки запроса: конечное число больше нуля, без `float`."""
    try:
        value = Decimal(text)
    except InvalidOperation:
        value = None
    if not is_price(value):
        raise DomainError(422, "Цена «Договорились» — число больше нуля.", code=CODE_INVALID_AGREED)
    return value


def _line_estimate(db: Session, tender_id: int, round_id: int, offer_id: int, pid: int) -> int:
    """Смета КП, которой принадлежит строка (не глава) оффера этапа этого тендера.

    Raises:
        DomainError 404: цепочка тендер, этап, оффер, смета, строка не сходится.
    """
    estimate_id = db.execute(
        sa.select(Lot.estimate_id)
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Estimate, Estimate.id == Lot.estimate_id)
        .join(Offer, Offer.id == Estimate.offer_id)
        .join(TenderRound, TenderRound.id == Offer.round_id)
        .where(
            PositionItem.id == pid,
            PositionItem.is_chapter.is_(False),
            Offer.id == offer_id,
            TenderRound.id == round_id,
            TenderRound.tender_id == tender_id,
        )
    ).scalar_one_or_none()
    if estimate_id is None:
        raise DomainError(404, f"Строка {pid} не найдена в КП оффера {offer_id}.")
    return estimate_id


def put_agreed(
    db: Session, tender_id: int, round_id: int, offer_id: int, pid: int, *, value: Decimal, basis: str, user_id: int
) -> None:
    """Сохранить «Договорились» нетто. `basis="shown"` — значение в базе НДС КП: нетто
    считается без округления; `"net"` принимается при любой базе."""
    estimate_id = _line_estimate(db, tender_id, round_id, offer_id, pid)
    net = value
    if basis == "shown":
        base = kp_vat_bases(db, [estimate_id])[estimate_id]
        if base is None:
            raise DomainError(422, "У КП неизвестна база НДС: укажите цену без НДС.", code=CODE_UNKNOWN_VAT_BASE)
        net = gross_to_net(value, base)
    stmt = pg_insert(KpLineAgreement).values(position_item_id=pid, net_price=net, set_by=user_id, set_at=sa.func.now())
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[KpLineAgreement.position_item_id],
            set_={"net_price": stmt.excluded.net_price, "set_by": stmt.excluded.set_by, "set_at": stmt.excluded.set_at},
        )
    )
    db.flush()


def delete_agreed(db: Session, tender_id: int, round_id: int, offer_id: int, pid: int) -> None:
    _line_estimate(db, tender_id, round_id, offer_id, pid)
    db.execute(sa.delete(KpLineAgreement).where(KpLineAgreement.position_item_id == pid))
    db.flush()


def put_comment(
    db: Session, tender_id: int, round_id: int, offer_id: int, pid: int, *, text: str, user_id: int
) -> None:
    """Сохранить свой комментарий; пустая строка — тоже решение."""
    if len(text) > KP_COMMENT_MAX:
        raise DomainError(422, f"Комментарий длиннее {KP_COMMENT_MAX} символов.", code=CODE_COMMENT_TOO_LONG)
    _line_estimate(db, tender_id, round_id, offer_id, pid)
    stmt = pg_insert(KpLineComment).values(position_item_id=pid, text=text, set_by=user_id, set_at=sa.func.now())
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[KpLineComment.position_item_id],
            set_={"text": stmt.excluded.text, "set_by": stmt.excluded.set_by, "set_at": stmt.excluded.set_at},
        )
    )
    db.flush()


def delete_comment(db: Session, tender_id: int, round_id: int, offer_id: int, pid: int) -> None:
    _line_estimate(db, tender_id, round_id, offer_id, pid)
    db.execute(sa.delete(KpLineComment).where(KpLineComment.position_item_id == pid))
    db.flush()


def _unknown_rung() -> DomainError:
    return DomainError(
        422,
        "Такой ступени у строки нет: нужны обе величины — уровень (v, p, f) и охват (cls, all) — "
        "и ступень из доступных этой строке.",
        code=CODE_UNKNOWN_RUNG,
    )


def put_rung(
    db: Session, tender_id: int, round_id: int, offer_id: int, pid: int, *, level: str, scope: str, user_id: int
) -> None:
    """Сохранить ручную ступень строки. Работа пишет свою запись; системная строка — ступень
    семьи `(смета, семья)` и удаляет собственную запись строки: та могла остаться от времени,
    когда строка была работой, и по ней строка показывала бы сброс поверх ступени семьи.

    Отказы по порядку: пара не образует ступень (`unknown_rung`); строка не дошла до
    лестницы (`no_ladder`); ступени нет у строки (`unknown_rung`); у ступени нет
    договоров (`rung_empty`). Строка считается той же сценой, что разбор, без ряда ИПЦ.
    """
    from crud.our_price import line_for_rung_check  # crud.our_price читает решения из этого модуля

    estimate_id, row, result = line_for_rung_check(db, tender_id, round_id, offer_id, pid)
    rung = next((r for r in RUNG_ORDER if r.level == level and r.scope == scope), None)
    if rung is None:
        raise _unknown_rung()
    if not result.rungs:
        raise DomainError(422, "Строка не дошла до ступеней: сравнивать её не с чем.", code=CODE_NO_LADDER)
    if rung not in {count.rung for count in result.rungs}:
        raise _unknown_rung()
    if rung not in result.selectable:
        raise DomainError(422, "На этой ступени нет договоров.", code=CODE_RUNG_EMPTY)

    key = row.line.key
    if row.line.is_system:
        stmt = pg_insert(KpFamilyRung).values(
            estimate_id=estimate_id,
            work_family_id=key.family_id,
            level=level,
            scope=scope,
            first_main_parameter_id=key.first_main[0] if level == "p" else None,
            set_by=user_id,
            set_at=sa.func.now(),
        )
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=[KpFamilyRung.estimate_id, KpFamilyRung.work_family_id],
                set_={
                    "level": stmt.excluded.level,
                    "scope": stmt.excluded.scope,
                    "first_main_parameter_id": stmt.excluded.first_main_parameter_id,
                    "set_by": stmt.excluded.set_by,
                    "set_at": stmt.excluded.set_at,
                },
            )
        )
        db.execute(sa.delete(KpLineRung).where(KpLineRung.position_item_id == pid))
    else:
        stmt = pg_insert(KpLineRung).values(
            position_item_id=pid,
            level=level,
            scope=scope,
            key=snapshot_to_json(rung.level, key.at(rung.level)),
            set_by=user_id,
            set_at=sa.func.now(),
        )
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=[KpLineRung.position_item_id],
                set_={
                    "level": stmt.excluded.level,
                    "scope": stmt.excluded.scope,
                    "key": stmt.excluded.key,
                    "set_by": stmt.excluded.set_by,
                    "set_at": stmt.excluded.set_at,
                },
            )
        )
    db.flush()


def delete_rung(db: Session, tender_id: int, round_id: int, offer_id: int, pid: int) -> None:
    """Снять ручную ступень: у работы — запись строки, у системной строки — ступень семьи
    сметы и собственная запись строки."""
    estimate_id = _line_estimate(db, tender_id, round_id, offer_id, pid)
    (row,) = (r for r in load_kp_lines(db, [estimate_id])[estimate_id] if r.line.position_item_id == pid)
    db.execute(sa.delete(KpLineRung).where(KpLineRung.position_item_id == pid))
    if row.line.is_system and row.line.key is not None:
        db.execute(
            sa.delete(KpFamilyRung).where(
                KpFamilyRung.estimate_id == estimate_id, KpFamilyRung.work_family_id == row.line.key.family_id
            )
        )
    db.flush()
