"""CRUD договоров генподряда (AGENTS.md §4, §7.1).

Карточка договора — **источник истины при импорте** (§3): объект, подрядчик и
реквизиты не апсертятся из XLSX, а берутся отсюда. Поэтому здесь же живут два
смысловых правила, которых нет в схеме:

* **`rate_class_id` — снимок класса на момент создания договора** (§4). Если он не
  передан, подставляется класс объекта: это ровно то значение по умолчанию, для
  которого `objects.rate_class_id` и существует. Если его нет ни там, ни в
  запросе — отказ, а не `NULL`: колонка NOT NULL, а переклассификация объекта не
  должна менять отклонения прошлых смет.
* **Удаление отказывается, если у договора есть сметы или задания импорта.**
  Записи `import_jobs` не удаляются никогда — это аудит (§5, правило 3).

Права: чтение — любому аутентифицированному, изменение — `admin` (решение фазы 5
§6.2, согласовано с пользователем).
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import (
    DomainError,
    clamp_page,
    iso,
    paginated,
    require_text,
    rollback_on_domain_error,
    translating_integrity,
)
from models import (
    Contract,
    Contractor,
    Estimate,
    ImportJob,
    Lot,
    ObjectModel,
    PositionItem,
    Proposal,
    RateClass,
)

log = logging.getLogger(__name__)

_UNIQUE_MESSAGES = {
    "uq_contracts_contract_number": "Договор с таким номером уже есть.",
}

UNSET = object()


def _contract_row_dict(
    contract: Contract,
    object_title: str,
    contractor_title: str,
    rate_class_title: str,
    estimates_count: int,
) -> dict:
    """Договор для списка: без вложенных сущностей, но с их названиями."""
    return {
        "id": contract.id,
        "contract_number": contract.contract_number,
        "title": contract.title,
        "object_id": contract.object_id,
        "object_title": object_title,
        "contractor_id": contract.contractor_id,
        "contractor_title": contractor_title,
        "rate_class_id": contract.rate_class_id,
        "rate_class_title": rate_class_title,
        "signer": contract.signer,
        "signed_date": iso(contract.signed_date),
        "total_amount": contract.total_amount,
        "estimates_count": estimates_count,
        "created_at": iso(contract.created_at),
        "updated_at": iso(contract.updated_at),
    }


def _contracts_select():
    """Договор + названия связанных сущностей + число смет.

    Все три FK объявлены NOT NULL, поэтому join-ы внутренние: договора без
    объекта, подрядчика или класса не существует.
    """
    estimates_count = (
        sa.select(sa.func.count())
        .where(Estimate.contract_id == Contract.id)
        .scalar_subquery()
    )
    return (
        sa.select(Contract, ObjectModel.title, Contractor.title, RateClass.title, estimates_count)
        .join(ObjectModel, ObjectModel.id == Contract.object_id)
        .join(Contractor, Contractor.id == Contract.contractor_id)
        .join(RateClass, RateClass.id == Contract.rate_class_id)
    )


def list_contracts(
    db: Session,
    *,
    q: str | None = None,
    object_id: int | None = None,
    contractor_id: int | None = None,
    rate_class_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Договоры с фильтрами и пагинацией (§7.1, список).

    `q` — подстрока номера договора, названия договора, объекта или подрядчика:
    человек ищет договор по тому, что помнит, а помнит он обычно объект.
    """
    page, page_size = clamp_page(page, page_size)
    stmt = _contracts_select()
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            sa.or_(
                Contract.contract_number.ilike(pattern),
                Contract.title.ilike(pattern),
                ObjectModel.title.ilike(pattern),
                Contractor.title.ilike(pattern),
            )
        )
    if object_id is not None:
        stmt = stmt.where(Contract.object_id == object_id)
    if contractor_id is not None:
        stmt = stmt.where(Contract.contractor_id == contractor_id)
    if rate_class_id is not None:
        stmt = stmt.where(Contract.rate_class_id == rate_class_id)

    rows, total = paginated(
        db,
        stmt,
        # Свежие договоры сверху; id — тай-брейк, без него порядок страниц
        # не определён при равных датах, и запись могла бы попасть на две страницы.
        order_by=(Contract.signed_date.desc(), Contract.id.desc()),
        page=page,
        page_size=page_size,
    )
    return {
        "items": [_contract_row_dict(*row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_contract(db: Session, contract_id: int) -> Contract:
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise DomainError(404, f"Договор {contract_id} не найден.")
    return contract


def _estimates_of(db: Session, contract_id: int) -> list[dict]:
    """Сметы договора со числом позиций, одним запросом.

    Число позиций считается коррелированным подзапросом через лоты и
    предложение: в карточке смет единицы, а дозапрос на каждую дал бы N+1.
    """
    positions_count = (
        sa.select(sa.func.count())
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == Estimate.id)
        .scalar_subquery()
    )
    rows = db.execute(
        sa.select(Estimate, positions_count)
        .where(Estimate.contract_id == contract_id)
        # NULL (исходная смета) — первой: nulls first при возрастании.
        .order_by(Estimate.amendment_no.asc().nulls_first())
    ).all()
    return [
        {
            "id": estimate.id,
            "amendment_no": estimate.amendment_no,
            "title": estimate.title,
            "data_prepared_on_date": iso(estimate.data_prepared_on_date),
            "import_job_id": estimate.import_job_id,
            "positions_count": count,
            "created_at": iso(estimate.created_at),
        }
        for estimate, count in rows
    ]


def get_contract_dict(db: Session, contract_id: int) -> dict:
    """Карточка договора: реквизиты, класс, текущие сметы (§7.1)."""
    row = db.execute(_contracts_select().where(Contract.id == contract_id)).first()
    if row is None:
        raise DomainError(404, f"Договор {contract_id} не найден.")
    body = _contract_row_dict(*row)
    body["notes"] = row[0].notes
    #: Коммерческие условия — в карточке, не в списке (спека §2.5): список это
    #: выбор, а не карточка, поэтому эти шесть ключей нет смысла нести в
    #: `_contract_row_dict`.
    body["advance_pct"] = row[0].advance_pct
    body["advance_note"] = row[0].advance_note
    body["bank_guarantee_pct"] = row[0].bank_guarantee_pct
    body["bank_guarantee_note"] = row[0].bank_guarantee_note
    body["retention_pct"] = row[0].retention_pct
    body["retention_note"] = row[0].retention_note
    body["estimates"] = _estimates_of(db, contract_id)
    return body


def list_contract_import_jobs(db: Session, contract_id: int) -> list[dict]:
    """История загрузок и замен договора (§7.1).

    `is_current` — ведёт ли задание на смету, которая лежит в БД сейчас. Это и
    есть отличие «загружено» от «вытеснено заменой»: после `replace` (§5,
    правило 3) старое задание и его файл остаются как аудит, но смета уже другая.
    Считается по `estimates.import_job_id`, то есть по тому же признаку, по
    которому правило 1 определяет идемпотентность.
    """
    get_contract(db, contract_id)   # 404 на несуществующий договор, а не пустой список

    rows = db.execute(
        sa.select(ImportJob, Estimate.id)
        .outerjoin(Estimate, Estimate.import_job_id == ImportJob.id)
        .where(ImportJob.contract_id == contract_id)
        .order_by(ImportJob.id.desc())
    ).all()
    return [
        {
            "id": job.id,
            "contract_id": job.contract_id,
            "amendment_no": job.amendment_no,
            "filename": job.filename,
            "file_sha256": job.file_sha256,
            "status": job.status,
            "error_text": job.error_text,
            "warnings": job.warnings,
            "counters": {
                "positions_total": job.positions_total,
                "matched_cache": job.matched_cache,
                "matched_exact": job.matched_exact,
                "matched_nonposition": job.matched_nonposition,
                "to_review": job.to_review,
            },
            "estimate_id": estimate_id,
            "is_current": estimate_id is not None,
            "created_at": iso(job.created_at),
            "started_at": iso(job.started_at),
            "finished_at": iso(job.finished_at),
        }
        for job, estimate_id in rows
    ]


def _resolve_snapshot_rate_class(
    db: Session, *, obj: ObjectModel, rate_class_id: int | None
) -> int:
    """Класс договора: переданный либо дефолт объекта. NULL недопустим (§4)."""
    if rate_class_id is not None:
        if db.get(RateClass, rate_class_id) is None:
            raise DomainError(404, f"Класс объектов {rate_class_id} не найден.")
        return rate_class_id
    if obj.rate_class_id is not None:
        return obj.rate_class_id
    raise DomainError(
        422,
        f"У объекта «{obj.title}» не задан класс, а класс договора обязателен: он "
        "фиксируется снимком на момент подписания и по нему сравниваются нормативы. "
        "Укажите rate_class_id в запросе либо задайте класс объекту.",
    )


def create_contract(
    db: Session,
    *,
    object_id: int,
    contractor_id: int,
    contract_number: str,
    signed_date: dt.date,
    rate_class_id: int | None = None,
    title: str | None = None,
    signer: str | None = None,
    total_amount: Decimal | None = None,
    notes: str | None = None,
    advance_pct: Decimal | None = None,
    advance_note: str | None = None,
    bank_guarantee_pct: Decimal | None = None,
    bank_guarantee_note: str | None = None,
    retention_pct: Decimal | None = None,
    retention_note: str | None = None,
) -> dict:
    """Создать договор. `rate_class_id` по умолчанию — класс объекта (§4)."""
    contract_number = require_text(contract_number, "Номер договора")

    obj = db.get(ObjectModel, object_id)
    if obj is None:
        raise DomainError(404, f"Объект {object_id} не найден.")
    if db.get(Contractor, contractor_id) is None:
        raise DomainError(404, f"Подрядчик {contractor_id} не найден.")
    snapshot_class = _resolve_snapshot_rate_class(db, obj=obj, rate_class_id=rate_class_id)

    if db.query(Contract).filter(Contract.contract_number == contract_number).first():
        raise DomainError(409, _UNIQUE_MESSAGES["uq_contracts_contract_number"])

    contract = Contract(
        object_id=object_id,
        contractor_id=contractor_id,
        rate_class_id=snapshot_class,
        contract_number=contract_number,
        title=(title or "").strip() or None,
        signer=(signer or "").strip() or None,
        signed_date=signed_date,
        total_amount=total_amount,
        notes=(notes or "").strip() or None,
        advance_pct=advance_pct,
        advance_note=(advance_note or "").strip() or None,
        bank_guarantee_pct=bank_guarantee_pct,
        bank_guarantee_note=(bank_guarantee_note or "").strip() or None,
        retention_pct=retention_pct,
        retention_note=(retention_note or "").strip() or None,
    )
    db.add(contract)
    with translating_integrity(db, _UNIQUE_MESSAGES):
        db.commit()
    db.refresh(contract)
    log.info(
        "contract_created id=%s number=%s object=%s rate_class=%s",
        contract.id,
        contract.contract_number,
        object_id,
        snapshot_class,
    )
    return get_contract_dict(db, contract.id)


def update_contract(
    db: Session,
    contract_id: int,
    *,
    object_id=UNSET,
    contractor_id=UNSET,
    rate_class_id=UNSET,
    contract_number=UNSET,
    title=UNSET,
    signer=UNSET,
    signed_date=UNSET,
    total_amount=UNSET,
    notes=UNSET,
    advance_pct=UNSET,
    advance_note=UNSET,
    bank_guarantee_pct=UNSET,
    bank_guarantee_note=UNSET,
    retention_pct=UNSET,
    retention_note=UNSET,
) -> dict:
    """Правка карточки. Все поля опциональны; непереданные не меняются.

    Класс правится намеренно: снимок защищает историю от **переклассификации
    объекта** (§4), а не от исправления опечатки в самой карточке.
    """
    contract = get_contract(db, contract_id)

    # Поля применяются по одному, а отказать может любое из последующих —
    # например пустой номер договора после уже присвоенного объекта. Без откага
    # отвергнутая правка оставалась бы видимой в этой сессии.
    with rollback_on_domain_error(db):
        if object_id is not UNSET:
            if db.get(ObjectModel, object_id) is None:
                raise DomainError(404, f"Объект {object_id} не найден.")
            contract.object_id = object_id
        if contractor_id is not UNSET:
            if db.get(Contractor, contractor_id) is None:
                raise DomainError(404, f"Подрядчик {contractor_id} не найден.")
            contract.contractor_id = contractor_id
        if rate_class_id is not UNSET:
            if db.get(RateClass, rate_class_id) is None:
                raise DomainError(404, f"Класс объектов {rate_class_id} не найден.")
            contract.rate_class_id = rate_class_id
        if contract_number is not UNSET:
            contract.contract_number = require_text(contract_number, "Номер договора")
        if title is not UNSET:
            contract.title = (title or "").strip() or None
        if signer is not UNSET:
            contract.signer = (signer or "").strip() or None
        if signed_date is not UNSET:
            contract.signed_date = signed_date
        if total_amount is not UNSET:
            contract.total_amount = total_amount
        if notes is not UNSET:
            contract.notes = (notes or "").strip() or None
        # Коммерческие условия: каждое из шести полей правится независимо, без
        # парности между процентом и комментарием (спека §2.5) — `null` сбрасывает
        # ровно то одно условие, которое передано.
        if advance_pct is not UNSET:
            contract.advance_pct = advance_pct
        if advance_note is not UNSET:
            contract.advance_note = (advance_note or "").strip() or None
        if bank_guarantee_pct is not UNSET:
            contract.bank_guarantee_pct = bank_guarantee_pct
        if bank_guarantee_note is not UNSET:
            contract.bank_guarantee_note = (bank_guarantee_note or "").strip() or None
        if retention_pct is not UNSET:
            contract.retention_pct = retention_pct
        if retention_note is not UNSET:
            contract.retention_note = (retention_note or "").strip() or None

    with translating_integrity(db, _UNIQUE_MESSAGES):
        db.commit()
    log.info("contract_updated id=%s", contract_id)
    return get_contract_dict(db, contract_id)


def delete_contract(db: Session, contract_id: int) -> None:
    """Удалить договор. Отказ, если есть сметы или задания импорта.

    Задания импорта — аудит и не удаляются никогда (§5, правило 3), поэтому
    договор с историей загрузок неудаляем в принципе: удалить его значило бы
    оборвать FK, на котором эта история висит. Пустая карточка (опечатка при
    заведении) удаляется свободно.
    """
    contract = get_contract(db, contract_id)
    estimates = db.query(Estimate).filter(Estimate.contract_id == contract_id).count()
    jobs = db.query(ImportJob).filter(ImportJob.contract_id == contract_id).count()
    if estimates or jobs:
        raise DomainError(
            409,
            f"Договор «{contract.contract_number}» удалить нельзя: смет ({estimates}), "
            f"заданий импорта ({jobs}). Задания импорта — аудит и не удаляются; "
            "замена сметы делается загрузкой с replace=true.",
        )
    db.delete(contract)
    db.commit()
    log.info("contract_deleted id=%s", contract_id)
