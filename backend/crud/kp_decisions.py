"""Решения по строкам КП: чтение для сцены «Против нашей цены» (спека 4б §2.3–§2.6, §2.13).

Три таблицы решений — «Договорились», свой комментарий и ручная ступень семьи
системы — читаются одним вызовом `load_decisions` на все сметы этапа: три `SELECT`,
сколько бы смет, строк и решений ни было. Ручной ступени работы больше нет (миграция
0024). Автор решения (идентификатор и почта) приходит
соединением с `users`, отдельного запроса на него нет. Запись решений — `put_*`/`delete_*` ниже: они флешат, транзакцию ведёт
роутер.

Тексты единиц строк — `load_unit_texts`; строка файла для комментария собирается `kp_source_line`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from crud.common import DomainError
from crud.our_price_load import KpLineRow, kp_vat_bases, load_kp_lines
from crud.reference_lines import load_link_values, load_reference_lines
from models import (
    KP_COMMENT_MAX,
    Estimate,
    EstimateRawData,
    KpFamilyRung,
    KpLineAgreement,
    KpLineComment,
    KpLineTarget,
    KpSentFile,
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
from services.family_links import links_of_families
from services.kp_file import KpSourceLine
from services.our_price import (
    RUNG_ORDER,
    LineState,
    ReferenceSnapshot,
    Rung,
    SavedFamilyRung,
    SavedRungs,
)
from services.reference_blocks import conversion_for, link_param_id, link_units
from services.reference_math import parse_manual_factor

CODE_INVALID_AGREED = "invalid_agreed"
CODE_COMMENT_TOO_LONG = "comment_too_long"
CODE_RUNG_EMPTY = "rung_empty"
CODE_NO_LADDER = "no_ladder"
CODE_UNKNOWN_VAT_BASE = "unknown_vat_base"
#: Код отказа панели строки и `PUT …/rung`: ступень не названа целиком, неизвестна
#: или недоступна строке.
CODE_UNKNOWN_RUNG = "unknown_rung"
#: Код отказа `PUT …/rung` у строки-работы: ручной ступени работы больше нет, цель
#: работы задаётся выбором аналога.
CODE_RUNG_NOT_APPLICABLE = "rung_not_applicable"
#: Код отказа `PUT …/target`: у строки нет цели-выбора (система или не `comparable`).
CODE_TARGET_NOT_APPLICABLE = "target_not_applicable"
#: Код отказа `PUT …/target`: выбранного аналога нет в остатке ступени (выброс, чужая ступень, ушёл).
CODE_ANALOG_UNAVAILABLE = "analog_unavailable"
#: Код отказа `PUT …/target` «самая свежая»: самой свежей у строки нет.
CODE_NO_FRESHEST = "no_freshest"
CODE_NO_CONTRACTOR_ANALOG = "no_contractor_analog"
#: Коды отказов справочной цели (спека справочных аналогов §2.13).
CODE_REFERENCE_UNAVAILABLE = "reference_unavailable"
CODE_CONVERSION_NOT_NEEDED = "conversion_not_needed"
CODE_CONVERSION_REQUIRED = "conversion_required"
CODE_LINK_NOT_APPLICABLE = "link_not_applicable"
CODE_CONVERSION_STALE = "conversion_stale"
CODE_MANUAL_CONVERSION_INVALID = "manual_conversion_invalid"
CODE_OUTLIER_CONFIRMATION_REQUIRED = "outlier_confirmation_required"
#: Предел основания ручного пересчёта, знаков.
MANUAL_BASIS_MAX = 300

#: Снимок справочной цели пуст: цель другого вида его не несёт.
_NO_REFERENCE_SNAPSHOT = {
    "ref_origin": None,
    "ref_method": None,
    "ref_factor": None,
    "ref_basis": None,
    "ref_link_id": None,
    "ref_without_analogs": None,
}


@dataclass(frozen=True)
class EstimateDecisions:
    """Решения по одной смете КП."""

    #: position_item_id -> (нетто за единицу, когда, кто, почта)
    agreements: dict[int, tuple[Decimal, datetime, int, str]]
    #: position_item_id -> (текст, когда, кто, почта); пустой текст — тоже решение
    comments: dict[int, tuple[str, datetime, int, str]]
    saved: SavedRungs
    #: position_item_id -> (вид выбора, аналог | None, когда, кто, почта); выбор цели строки-работы
    targets: dict[int, tuple[str, int | None, datetime, int, str]]
    #: ("line", position_item_id) | ("family", family_id) -> (когда, кто, почта)
    rung_meta: dict[tuple[str, int], tuple[datetime, int, str]]
    #: position_item_id -> снимок справочной цели (только у строк с `kind = 'reference'`)
    references: dict[int, ReferenceSnapshot] = field(default_factory=dict)


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
    targets: dict[int, dict[int, tuple[str, int | None, datetime, int, str]]] = {i: {} for i in ids}
    family: dict[int, dict[int, SavedFamilyRung]] = {i: {} for i in ids}
    meta: dict[int, dict[tuple[str, int], tuple[datetime, int, str]]] = {i: {} for i in ids}
    references: dict[int, dict[int, ReferenceSnapshot]] = {i: {} for i in ids}

    for estimate_id, pid, net, at, user_id, email in db.execute(
        _by_estimate(KpLineAgreement, ids, KpLineAgreement.net_price)
    ):
        agreements[estimate_id][pid] = (net, at, user_id, email)
    for estimate_id, pid, text, at, user_id, email in db.execute(_by_estimate(KpLineComment, ids, KpLineComment.text)):
        comments[estimate_id][pid] = (text, at, user_id, email)
    for estimate_id, pid, kind, analog_id, origin, method, factor, basis, link_id, without_analogs, at, user_id, email in (
        db.execute(
            _by_estimate(
                KpLineTarget,
                ids,
                KpLineTarget.kind,
                KpLineTarget.analog_position_item_id,
                KpLineTarget.ref_origin,
                KpLineTarget.ref_method,
                KpLineTarget.ref_factor,
                KpLineTarget.ref_basis,
                KpLineTarget.ref_link_id,
                KpLineTarget.ref_without_analogs,
            )
        )
    ):
        targets[estimate_id][pid] = (kind, analog_id, at, user_id, email)
        if kind == "reference":
            references[estimate_id][pid] = ReferenceSnapshot(
                origin=origin,
                method=method,
                factor=factor,
                basis=basis,
                link_id=link_id,
                without_analogs=without_analogs,
            )
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
            targets=targets[i],
            saved=SavedRungs(family=family[i]),
            rung_meta=meta[i],
            references=references[i],
        )
        for i in ids
    }


@dataclass(frozen=True)
class DecisionCounts:
    """Сколько решений по строкам КП стоит на сметах раунда или участника."""

    agreements: int
    comments: int
    #: ступени семей (ручной ступени работы больше нет)
    rungs: int
    #: выборы цели строк-работ
    targets: int

    @property
    def total(self) -> int:
        return self.agreements + self.comments + self.rungs + self.targets


def _count_decisions(db: Session, estimate_ids: sa.Select) -> DecisionCounts:
    """Счёт решений по сметам из подзапроса — один запрос, сколько бы смет ни было."""
    items = (
        sa.select(PositionItem.id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids))
    )

    def on_items(model) -> sa.ScalarSelect:
        return sa.select(sa.func.count()).select_from(model).where(model.position_item_id.in_(items)).scalar_subquery()

    family = (
        sa.select(sa.func.count())
        .select_from(KpFamilyRung)
        .where(KpFamilyRung.estimate_id.in_(estimate_ids))
        .scalar_subquery()
    )
    agreements, comments, targets, family_rungs = db.execute(
        sa.select(on_items(KpLineAgreement), on_items(KpLineComment), on_items(KpLineTarget), family)
    ).one()
    return DecisionCounts(agreements, comments, family_rungs, targets)


def count_round_decisions(db: Session, round_id: int) -> DecisionCounts:
    """Решения по сметам раунда: и сметы раунда, и сметы его офферов."""
    offers = sa.select(Offer.id).where(Offer.round_id == round_id)
    return _count_decisions(
        db, sa.select(Estimate.id).where(sa.or_(Estimate.round_id == round_id, Estimate.offer_id.in_(offers)))
    )


def count_package_decisions(db: Session, package_id: int) -> DecisionCounts:
    """Решения по сметам офферов участника во всех раундах."""
    offers = sa.select(Offer.id).where(Offer.package_id == package_id)
    return _count_decisions(db, sa.select(Estimate.id).where(Estimate.offer_id.in_(offers)))


def sent_file_keys(
    db: Session, *, round_id: int | None = None, tender_id: int | None = None, package_id: int | None = None
) -> list[str]:
    """Ключи хранилища отправленных файлов раунда, тендера или участника.

    Ключи собираются по офферам, а не по сметам: у оффера без сметы файлы тоже есть.
    Ровно один аргумент.
    """
    chosen = [(Offer.round_id, round_id), (Offer.tender_id, tender_id), (Offer.package_id, package_id)]
    given = [(column, value) for column, value in chosen if value is not None]
    if len(given) != 1:
        raise ValueError("sent_file_keys: нужен ровно один из round_id, tender_id, package_id")
    column, value = given[0]
    return list(
        db.execute(
            sa.select(KpSentFile.file_key)
            .join(Offer, Offer.id == KpSentFile.offer_id)
            .where(column == value)
            .order_by(KpSentFile.id)
        ).scalars()
    )


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
    """Сохранить ручную ступень системной строки: ступень семьи `(смета, семья)`. У строки-работы
    ступени нет (миграция 0024).

    Отказы по порядку: строка — работа (`rung_not_applicable`); пара не образует ступень
    (`unknown_rung`); строка не дошла до лестницы (`no_ladder`); ступени нет у строки
    (`unknown_rung`); у ступени нет договоров (`rung_empty`). Строка считается той же сценой,
    что разбор, без ряда ИПЦ.
    """
    from crud.our_price import line_for_rung_check  # crud.our_price читает решения из этого модуля

    estimate_id, row, result = line_for_rung_check(db, tender_id, round_id, offer_id, pid)
    if not row.line.is_system:
        raise DomainError(
            422,
            "У работы нет ручной ступени: цель работы выбирается среди аналогов.",
            code=CODE_RUNG_NOT_APPLICABLE,
        )
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
    db.flush()


def put_target(
    db: Session,
    tender_id: int,
    round_id: int,
    offer_id: int,
    pid: int,
    *,
    kind: Literal["freshest", "analog", "contractor"],
    analog_id: int | None,
    inflation_series_id: int | None,
    actor_id: int,
) -> None:
    """Сохранить выбор цели строки-работы: «самая свежая», «с этим подрядчиком» или конкретный аналог остатка.

    Строка проверяется той же сценой, что чтение, и с тем же рядом ИПЦ: остаток от приведения зависит
    от ряда в обе стороны (спека лестницы аналогов §2.17), поэтому `selectable` панели и исход записи
    считаются одинаково. Ряд в записи не хранится. Отказы по порядку: ряд и принадлежность (как у
    чтения), система или не `comparable` (`target_not_applicable`), аналога нет в остатке
    (`analog_unavailable`), самой свежей нет (`no_freshest`), аналога подрядчика КП в остатке нет
    (`no_contractor_analog`).
    """
    from crud.our_price import line_for_rung_check  # crud.our_price читает решения из этого модуля

    _, row, result = line_for_rung_check(
        db, tender_id, round_id, offer_id, pid, inflation_series_id=inflation_series_id
    )
    if row.line.is_system or result.state is not LineState.comparable:
        raise DomainError(
            422, "Выбор цели есть только у сравнимой строки-работы.", code=CODE_TARGET_NOT_APPLICABLE
        )
    if kind == "analog" and analog_id not in result.ladder.remainder_ids:
        raise DomainError(
            422, "Этот аналог не входит в остаток ступени строки: выбрать его нельзя.", code=CODE_ANALOG_UNAVAILABLE
        )
    if kind == "freshest" and result.freshest is None:
        raise DomainError(422, "У строки нет самой свежей расценки.", code=CODE_NO_FRESHEST)
    if kind == "contractor" and result.ladder.contractor is None:
        raise DomainError(
            422, "Среди аналогов строки нет договора подрядчика этого КП.", code=CODE_NO_CONTRACTOR_ANALOG
        )

    stmt = pg_insert(KpLineTarget).values(
        position_item_id=pid,
        kind=kind,
        analog_position_item_id=analog_id if kind == "analog" else None,
        set_by=actor_id,
        set_at=sa.func.now(),
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[KpLineTarget.position_item_id],
            set_={
                "kind": stmt.excluded.kind,
                "analog_position_item_id": stmt.excluded.analog_position_item_id,
                "set_by": stmt.excluded.set_by,
                "set_at": stmt.excluded.set_at,
                # перезапись цели другим видом снимает снимок справочной цели
                **_NO_REFERENCE_SNAPSHOT,
            },
        )
    )
    db.flush()


class LinkConversionInput(BaseModel):
    """Пересчёт по связи: тот, что человек видел в диалоге (`factor` и `basis` из ответа панели или поиска
    без изменений); число считает сервер, ожидаемые значения — только проверка актуальности."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["link"]
    link_id: int
    expected_factor: str
    expected_basis: str


class ManualConversionInput(BaseModel):
    """Ручной пересчёт: `N` и основание вводит человек (спека справочных аналогов §2.13)."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["manual"]
    factor: str
    basis: str


class ReferenceTargetInput(BaseModel):
    """Тело `PUT …/target` с `kind = "reference"` (спека справочных аналогов §2.13)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["reference"]
    analog_id: int
    origin: Literal["zone", "link", "search"]
    conversion: Annotated[LinkConversionInput | ManualConversionInput, Field(discriminator="method")] | None = None
    confirm_outlier: bool = False


def _same_factor(current: Decimal, expected: str) -> bool:
    """Ожидаемое клиентом `N` равно текущему как `Decimal`; нечисловая строка — не равно."""
    try:
        return Decimal(expected) == current
    except InvalidOperation:
        return False


def put_reference_target(
    db: Session,
    tender_id: int,
    round_id: int,
    offer_id: int,
    pid: int,
    *,
    body: ReferenceTargetInput,
    inflation_series_id: int | None,
    actor_id: int,
) -> None:
    """Сохранить справочную цель строки-работы: строку договора с пересчётом единиц, записанным снимком.

    Строка проверяется той же сценой и с тем же рядом ИПЦ, что чтение. Отказы по таблице спеки
    справочных аналогов §2.13, по порядку: система (`target_not_applicable`); строка договора не
    справочная (`reference_unavailable`); пересчёт при равных единицах (`conversion_not_needed`); различны
    единицы, а пересчёта нет (`conversion_required`); связь не годится (`link_not_applicable`); `N` или
    основание по связи сменились (`409 conversion_stale`); ручной пересчёт неверен
    (`manual_conversion_invalid`); пара помечена выбросом без подтверждения
    (`409 outlier_confirmation_required`). Пометка — та же функция, что у панели и поиска.
    """
    from crud.our_price import (  # crud.our_price читает решения из этого модуля
        line_with_scene,
        reference_mark_of,
    )

    scene, participant, row, result = line_with_scene(
        db, tender_id, round_id, offer_id, pid, inflation_series_id=inflation_series_id
    )
    if row.line.is_system:
        raise DomainError(422, "Справочная цель есть только у строки-работы.", code=CODE_TARGET_NOT_APPLICABLE)
    factors = scene.plan.factor_by_estimate if scene.plan is not None else None
    line = load_reference_lines(db, scene.contracts, [body.analog_id], factors=factors).get(body.analog_id)
    if line is None:
        raise DomainError(
            422, "Эта строка договора не годится как справочная цель.", code=CODE_REFERENCE_UNAVAILABLE
        )

    same_unit = line.unit_id is not None and line.unit_id == row.unit_id
    conversion = body.conversion
    if same_unit and conversion is not None:
        raise DomainError(422, "Единицы совпадают: пересчёт не нужен.", code=CODE_CONVERSION_NOT_NEEDED)
    if not same_unit and conversion is None:
        raise DomainError(422, "Единицы различны: нужен пересчёт.", code=CODE_CONVERSION_REQUIRED)

    method: str | None = None
    factor: Decimal | None = None
    basis: str | None = None
    link_id: int | None = None
    if isinstance(conversion, LinkConversionInput):
        kp_family = None if row.line.key is None else row.line.key.family_id
        states = links_of_families(db, [] if kp_family is None else [kp_family])
        param_id = link_param_id(states, line.family_id, kp_family)
        values = load_link_values(db, set() if param_id is None else {(line.position_item_id, param_id)})
        found, _reason, _needed = conversion_for(
            contract_unit_id=line.unit_id,
            kp_unit_id=row.unit_id,
            contract_family_id=line.family_id,
            kp_family_id=kp_family,
            links=states,
            units=link_units(states),
            values=values,
            position_item_id=line.position_item_id,
        )
        if found is None or found.link_id != conversion.link_id:
            raise DomainError(
                422, "Эта связь не даёт пересчёта для выбранной строки.", code=CODE_LINK_NOT_APPLICABLE
            )
        if not _same_factor(found.factor, conversion.expected_factor) or found.basis != conversion.expected_basis:
            raise DomainError(
                409,
                "Пересчёт по связи изменился, пока диалог был открыт: проверьте новое значение.",
                code=CODE_CONVERSION_STALE,
                context={"factor": format(found.factor, "f"), "basis": found.basis},
            )
        method, factor, basis, link_id = "link", found.factor, found.basis, found.link_id
    elif isinstance(conversion, ManualConversionInput):
        manual = parse_manual_factor(conversion.factor)
        text = conversion.basis.strip()
        if manual is None or not text or len(text) > MANUAL_BASIS_MAX:
            raise DomainError(
                422,
                "Ручной пересчёт: множитель — число больше нуля, не более 10⁶, до шести знаков после "
                f"запятой; основание — не пустое, до {MANUAL_BASIS_MAX} знаков.",
                code=CODE_MANUAL_CONVERSION_INVALID,
            )
        method, factor, basis = "manual", manual, text

    ratio = reference_mark_of(db, scene, participant, row, result, body.analog_id)
    if ratio is not None and not body.confirm_outlier:
        raise DomainError(
            409,
            "Строка помечена выбросом: подтвердите выбор.",
            code=CODE_OUTLIER_CONFIRMATION_REQUIRED,
            context={"ratio": format(ratio, "f")},
        )

    snapshot = {
        "ref_origin": body.origin,
        "ref_method": method,
        "ref_factor": factor,
        "ref_basis": basis,
        "ref_link_id": link_id,
        "ref_without_analogs": result.ladder is None or result.ladder.rung_no is None,
    }
    stmt = pg_insert(KpLineTarget).values(
        position_item_id=pid,
        kind="reference",
        analog_position_item_id=body.analog_id,
        set_by=actor_id,
        set_at=sa.func.now(),
        **snapshot,
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[KpLineTarget.position_item_id],
            set_={
                "kind": stmt.excluded.kind,
                "analog_position_item_id": stmt.excluded.analog_position_item_id,
                "set_by": stmt.excluded.set_by,
                "set_at": stmt.excluded.set_at,
                **snapshot,
            },
        )
    )
    db.flush()


def delete_target(db: Session, tender_id: int, round_id: int, offer_id: int, pid: int) -> None:
    """Снять выбор цели: строка возвращается к самой низкой. Нет записи — тот же 204."""
    _line_estimate(db, tender_id, round_id, offer_id, pid)
    db.execute(sa.delete(KpLineTarget).where(KpLineTarget.position_item_id == pid))
    db.flush()


def delete_rung(db: Session, tender_id: int, round_id: int, offer_id: int, pid: int) -> None:
    """Снять ручную ступень: у системной строки — ступень семьи сметы; у работы снимать
    нечего, ответ тот же 204."""
    estimate_id = _line_estimate(db, tender_id, round_id, offer_id, pid)
    (row,) = (r for r in load_kp_lines(db, [estimate_id])[estimate_id] if r.line.position_item_id == pid)
    if row.line.is_system and row.line.key is not None:
        db.execute(
            sa.delete(KpFamilyRung).where(
                KpFamilyRung.estimate_id == estimate_id, KpFamilyRung.work_family_id == row.line.key.family_id
            )
        )
    db.flush()
