"""Отметка победителя тендера: команды, фрагмент карточки тендера, охранные
проверки (спека Б2 `2026-10-08-tender-award-design.md` §2.4, §2.6, §2.7).

Иерархия замков фичи — тендер → раунды (по `id`) → участник → отметка →
договор; команды стороны договора тендер не блокируют. Каждый запрет держится
дважды: проверка команды под замком даёт текст с именами и номерами, а
ограничение схемы, переведённое `translating_integrity` парой `(код, текст)`
без подстановок, закрывает гонку и любой путь мимо команды — отказ через ключ
несёт тот же код, что синхронный (§2.7). Проверки вынесены в отдельные
функции, чтобы тест мог выключить синхронную и упереться в ключ.

Модуль зависит от `crud.tenders` (замки, карточка), а `crud.tenders` зовёт
`award_card_fragment` и `refuse_if_active_award` локальным импортом: цикл
разорван в одну сторону.
"""
from __future__ import annotations

import datetime as dt
import logging

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud import tenders as crud_tenders
from crud.common import DomainError, iso, translating_integrity
from crud.estimate_totals import estimate_total_including_vat, estimate_totals_including_vat
from models import (
    Contract,
    Contractor,
    Estimate,
    EstimateRawData,
    ObjectModel,
    Offer,
    OfferPackage,
    Tender,
    TenderAward,
    TenderRound,
    User,
)
from services.round_import import kp_inn_of

log = logging.getLogger(__name__)


def _dec(value) -> str | None:
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
#  Тексты отказов (спека Б2 §2.7). Пара — (код, текст); текст ключа — без подстановок.
# ---------------------------------------------------------------------------

def _not_active_refusal() -> tuple[str, str]:
    return "award_not_active", "Отметка уже не действующая — обновите карточку тендера."


def _award_exists_text(winner_title: str | None = None) -> tuple[str, str]:
    named = f" — {winner_title}" if winner_title is not None else ""
    return "award_exists", (
        f"В тендере уже отмечен победитель{named}. Чтобы отметить другого, снимите отметку или "
        "отметьте, что договор не заключён."
    )


def _no_kp_refusal(title: str | None = None) -> tuple[str, str]:
    who = f" «{title}»" if title is not None else ""
    return "award_no_kp", f"У участника{who} нет КП в этом этапе — отметить его нельзя."


def _kp_unidentified_refusal(title: str | None = None, stage_no: int | None = None) -> tuple[str, str]:
    who = f" «{title}»" if title is not None else ""
    stage = f" {stage_no}" if stage_no is not None else ""
    return "award_kp_unidentified", (
        f"КП участника{who} не опознаётся в разборе этапа{stage}: в нём нет ИНН участника либо их "
        "несколько. Отметить нельзя — загрузите файл этапа заново."
    )


def _has_contract_refusal(number: str | None = None) -> tuple[str, str]:
    where = f" № {number}" if number is not None else ""
    return "award_has_contract", (
        f"Нельзя: по этой отметке заключён договор{where}. Сначала удалите договор или отвяжите его "
        "от тендера."
    )


def _object_mismatch_refusal(number: str | None = None) -> tuple[str, str]:
    where = f" № {number}" if number is not None else ""
    return "contract_object_mismatch", (
        f"Договор{where} заключён на другом объекте — привязать его к этому тендеру нельзя."
    )


def _contractor_mismatch_refusal(number: str | None = None) -> tuple[str, str]:
    where = f" № {number}" if number is not None else ""
    return "contract_contractor_mismatch", (
        f"Договор{where} заключён с другим подрядчиком — привязать его к этому тендеру нельзя."
    )


def _plain_text(code: str) -> str:
    """Текст отказа по ключу для кодов расхождения сторон: без номера договора."""
    return {
        "contract_object_mismatch": _object_mismatch_refusal()[1],
        "contract_contractor_mismatch": _contractor_mismatch_refusal()[1],
    }[code]


def _refusal(pair: tuple[str, str]) -> DomainError:
    code, text = pair
    return DomainError(409, text, code=code)


# ---------------------------------------------------------------------------
#  Замки и поиск
# ---------------------------------------------------------------------------

def _lock_award(db: Session, tender_id: int, award_id: int) -> TenderAward:
    """Отметка ищется парой (id, тендер): чужой тендер в пути — 404 (спека §2.6)."""
    award = db.execute(
        sa.select(TenderAward).where(TenderAward.id == award_id, TenderAward.tender_id == tender_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if award is None:
        raise DomainError(404, f"Отметка {award_id} тендера {tender_id} не найдена.")
    return award


def _find_award(db: Session, tender_id: int, award_id: int) -> TenderAward:
    award = db.execute(
        sa.select(TenderAward).where(TenderAward.id == award_id, TenderAward.tender_id == tender_id)
    ).scalar_one_or_none()
    if award is None:
        raise DomainError(404, f"Отметка {award_id} тендера {tender_id} не найдена.")
    return award


def _contract_of(db: Session, award_id: int) -> Contract | None:
    return db.execute(
        sa.select(Contract).where(Contract.tender_award_id == award_id)
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
#  Охранные проверки — отдельными функциями (см. докстроку модуля)
# ---------------------------------------------------------------------------

def refuse_if_active_award(db: Session, tender_id: int) -> None:
    """Новый этап при действующей отметке запрещён (`round_blocked_by_award`)."""
    active = db.execute(
        sa.select(TenderAward.id).where(TenderAward.tender_id == tender_id, TenderAward.not_concluded_on.is_(None))
        .limit(1)
    ).first()
    if active is not None:
        raise DomainError(
            409,
            "Нельзя добавить этап: в тендере отмечен победитель. Снимите отметку или отметьте, что "
            "договор не заключён.",
            code="round_blocked_by_award",
        )


def _refuse_if_award_exists(db: Session, tender_id: int) -> None:
    row = db.execute(
        sa.select(Contractor.title).select_from(TenderAward)
        .join(Contractor, Contractor.id == TenderAward.contractor_id)
        .where(TenderAward.tender_id == tender_id, TenderAward.not_concluded_on.is_(None))
        .limit(1)
    ).first()
    if row is not None:
        raise _refusal(_award_exists_text(row[0]))


def _refuse_if_not_active(award: TenderAward) -> None:
    if award.not_concluded_on is not None:
        raise _refusal(_not_active_refusal())


def _refuse_if_has_contract(db: Session, award: TenderAward) -> None:
    contract = _contract_of(db, award.id)
    if contract is not None:
        raise _refusal(_has_contract_refusal(contract.contract_number))


def _parties_refusal(contract: Contract, award: TenderAward) -> tuple[str, str] | None:
    """Какая из двух сторон договора расходится с отметкой; объект — первым."""
    if contract.object_id != award.object_id:
        return _object_mismatch_refusal(contract.contract_number)
    if contract.contractor_id != award.contractor_id:
        return _contractor_mismatch_refusal(contract.contract_number)
    return None


def _refuse_if_parties_differ(contract: Contract, award: TenderAward) -> None:
    pair = _parties_refusal(contract, award)
    if pair is not None:
        raise _refusal(pair)


def _refuse_if_contract_linked(db: Session, contract: Contract) -> None:
    if contract.tender_award_id is None:
        return
    tender_number = db.execute(
        sa.select(Tender.tender_number).select_from(TenderAward)
        .join(Tender, Tender.id == TenderAward.tender_id)
        .where(TenderAward.id == contract.tender_award_id)
    ).scalar_one_or_none()
    raise DomainError(
        409, f"Договор № {contract.contract_number} уже привязан к тендеру № {tender_number}.",
        code="contract_already_linked",
    )


# ---------------------------------------------------------------------------
#  Команды
# ---------------------------------------------------------------------------

def award_winner(db: Session, tender_id: int, *, offer_id: int, user_id: int) -> dict:
    """Отметить победителем участника по КП финального этапа (спека §2.4)."""
    crud_tenders._lock_tender(db, tender_id, exclusive=True)
    crud_tenders._lock_rounds(db, tender_id)
    offer = db.execute(
        sa.select(Offer).where(Offer.id == offer_id, Offer.tender_id == tender_id)
    ).scalar_one_or_none()
    if offer is None:
        raise DomainError(404, f"Оферта {offer_id} тендера {tender_id} не найдена.")
    package = db.get(OfferPackage, offer.package_id)
    contractor = db.get(Contractor, package.contractor_id)
    stage_no = db.execute(sa.select(TenderRound.stage_no).where(TenderRound.id == offer.round_id)).scalar_one()
    last_stage = db.execute(
        sa.select(sa.func.max(TenderRound.stage_no)).where(TenderRound.tender_id == tender_id)
    ).scalar_one()
    if stage_no != last_stage:
        raise DomainError(
            409, f"Отметить победителя можно только на КП финального этапа — этап {stage_no} не последний.",
            code="award_not_final_stage",
        )
    estimate_id = db.execute(
        sa.select(Estimate.id).where(Estimate.offer_id == offer.id).order_by(Estimate.id).limit(1)
    ).scalar_one_or_none()
    if estimate_id is None:
        raise _refusal(_no_kp_refusal(contractor.title))
    crud_tenders._refuse_if_active(db, [offer.round_id])
    _refuse_if_award_exists(db, tender_id)
    raw_data = db.execute(
        sa.select(EstimateRawData.raw_data).where(EstimateRawData.estimate_id == estimate_id)
    ).scalar_one_or_none()
    kp_inn = kp_inn_of(raw_data or {})
    if kp_inn is None:
        raise _refusal(_kp_unidentified_refusal(contractor.title, stage_no))
    tender = db.get(Tender, tender_id)
    db.add(TenderAward(
        tender_id=tender_id, object_id=tender.object_id, offer_id=offer.id, package_id=package.id,
        contractor_id=package.contractor_id, estimate_id=estimate_id, kp_inn=kp_inn, awarded_by=user_id,
    ))
    with translating_integrity(db, {
        "uq_tender_awards_active": _award_exists_text(),
        "fk_tender_awards_kp_estimate": _no_kp_refusal(),
        "ck_tender_awards_kp_inn_canonical": _kp_unidentified_refusal(),
    }):
        db.commit()
    log.info("tender_award_created tender=%s offer=%s", tender_id, offer_id)
    return crud_tenders.get_tender_card(db, tender_id)


def _lock_open_award(db: Session, tender_id: int, award_id: int) -> TenderAward:
    """Тендер FOR UPDATE → отметка FOR UPDATE → действующая, без договора."""
    crud_tenders._lock_tender(db, tender_id, exclusive=True)
    award = _lock_award(db, tender_id, award_id)
    _refuse_if_not_active(award)
    _refuse_if_has_contract(db, award)
    return award


def remove_award(db: Session, tender_id: int, award_id: int) -> dict:
    """Снять действующую отметку: она удаляется без следа в истории (спека §2.4)."""
    award = _lock_open_award(db, tender_id, award_id)
    with translating_integrity(db, {"fk_contracts_tender_award": _has_contract_refusal()}):
        db.execute(sa.delete(TenderAward).where(TenderAward.id == award.id))
        db.commit()
    log.info("tender_award_removed tender=%s award=%s", tender_id, award_id)
    return crud_tenders.get_tender_card(db, tender_id)


def mark_not_concluded(
    db: Session, tender_id: int, award_id: int, *,
    not_concluded_on: dt.date, note: str | None, user_id: int,
) -> dict:
    """«Договор не заключён»: отметка закрывается, остаётся в истории (спека §2.4)."""
    award = _lock_open_award(db, tender_id, award_id)
    award.not_concluded_on = not_concluded_on
    award.not_concluded_note = note if note is not None and note.strip() else None
    award.not_concluded_by = user_id
    award.not_concluded_at = sa.func.now()
    db.commit()
    log.info("tender_award_not_concluded tender=%s award=%s", tender_id, award_id)
    return crud_tenders.get_tender_card(db, tender_id)


def contract_candidates(db: Session, tender_id: int, award_id: int) -> list[dict]:
    """Договоры того же объекта и подрядчика без основания; чтение без замков."""
    award = _find_award(db, tender_id, award_id)
    rows = db.execute(
        sa.select(Contract, ObjectModel.title, Contractor.title)
        .join(ObjectModel, ObjectModel.id == Contract.object_id)
        .join(Contractor, Contractor.id == Contract.contractor_id)
        .where(
            Contract.object_id == award.object_id, Contract.contractor_id == award.contractor_id,
            Contract.tender_award_id.is_(None),
        )
        .order_by(Contract.signed_date.desc(), Contract.id.desc())
    ).all()
    base_by_contract = dict(db.execute(
        sa.select(Estimate.contract_id, Estimate.id).where(
            Estimate.contract_id.in_([c.id for c, _, _ in rows] or [-1]), Estimate.amendment_no.is_(None),
        )
    ).all())
    totals = estimate_totals_including_vat(db, list(base_by_contract.values()))
    return [
        {
            "id": c.id, "contract_number": c.contract_number, "signed_date": iso(c.signed_date),
            "object_title": object_title, "contractor_title": contractor_title,
            "base_total_including_vat": (
                _dec(totals.get(base_by_contract[c.id])) if c.id in base_by_contract else None
            ),
        }
        for c, object_title, contractor_title in rows
    ]


def link_contract(db: Session, tender_id: int, award_id: int, *, contract_id: int) -> dict:
    """Привязать существующий договор к действующей отметке; смета договора
    не меняется (спека §2.4)."""
    award = _lock_open_award(db, tender_id, award_id)
    contract = db.execute(
        sa.select(Contract).where(Contract.id == contract_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if contract is None:
        raise DomainError(404, f"Договор {contract_id} не найден.")
    _refuse_if_contract_linked(db, contract)
    _refuse_if_parties_differ(contract, award)
    # Ключ `fk_contracts_tender_award` по одному имени не различает объект и
    # подрядчика: текст страховки берётся по тому же сравнению, что у проверки.
    key_pair = _parties_refusal(contract, award)
    contract.tender_award_id = award.id
    with translating_integrity(db, {
        # Текст страховки — без подстановок: номер договора в него не идёт.
        **({} if key_pair is None else {"fk_contracts_tender_award": (key_pair[0], _plain_text(key_pair[0]))}),
        "uq_contracts_tender_award": _has_contract_refusal(),
    }):
        db.commit()
    log.info("tender_award_linked tender=%s award=%s contract=%s", tender_id, award_id, contract_id)
    return crud_tenders.get_tender_card(db, tender_id)


# ---------------------------------------------------------------------------
#  Фрагмент карточки тендера
# ---------------------------------------------------------------------------

def award_card_fragment(db: Session, tender_id: int) -> dict:
    """`{"award": …, "award_history": […]}` — формы спеки §2.6."""
    rows = db.execute(
        sa.select(TenderAward, Contractor.title, Contractor.inn, TenderRound.id, TenderRound.stage_no)
        .join(Contractor, Contractor.id == TenderAward.contractor_id)
        .join(Offer, Offer.id == TenderAward.offer_id)
        .join(TenderRound, TenderRound.id == Offer.round_id)
        .where(TenderAward.tender_id == tender_id)
        .order_by(TenderAward.id)
    ).all()
    user_ids = {a.awarded_by for a, *_ in rows} | {a.not_concluded_by for a, *_ in rows if a.not_concluded_by}
    emails = dict(db.execute(sa.select(User.id, User.email).where(User.id.in_(user_ids or [-1]))).all())

    award = None
    history: list[dict] = []
    for a, title, inn, round_id, stage_no in rows:
        is_active = a.not_concluded_on is None
        history.append({
            "award_id": a.id, "kind": "awarded", "package_id": a.package_id, "contractor_title": title,
            "awarded_at": iso(a.awarded_at), "not_concluded_on": None, "note": None,
            "by_email": emails.get(a.awarded_by), "is_active": is_active,
        })
        if not is_active:
            history.append({
                "award_id": a.id, "kind": "not_concluded", "package_id": a.package_id,
                "contractor_title": title, "awarded_at": None, "not_concluded_on": iso(a.not_concluded_on),
                "note": a.not_concluded_note, "by_email": emails.get(a.not_concluded_by), "is_active": False,
            })
            continue
        contract = _contract_of(db, a.id)
        total = estimate_total_including_vat(db, a.estimate_id)
        award = {
            "id": a.id, "offer_id": a.offer_id, "package_id": a.package_id, "contractor_id": a.contractor_id,
            "contractor_title": title, "contractor_inn": inn, "round_id": round_id, "stage_no": stage_no,
            "estimate_id": a.estimate_id, "total_including_vat": _dec(total),
            "awarded_at": iso(a.awarded_at), "awarded_by_email": emails.get(a.awarded_by),
            "contract": None if contract is None else {
                "id": contract.id, "contract_number": contract.contract_number,
                "signed_date": iso(contract.signed_date),
            },
        }
    return {"award": award, "award_history": history}
