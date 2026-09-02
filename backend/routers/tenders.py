"""Тендерный контур: тендеры, раунды, участники, загрузка сводной таблицы
раунда (спека 2026-08-26-tenders-contour-design.md §2.13).

Права: чтение и upload — member; создание/правка/удаление тендера и раунда,
удаление участника, replace — admin. Раунд в URL всегда ищется парой
(round_id, tender_id) — чужой тендер даёт 404.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import get_current_user, require_admin
from config import settings
from crud import position_drilldown as crud_position_drilldown
from crud import round_unallocated as crud_ru
from crud import stage_summary as crud_stage_summary
from crud import tenders as crud_tenders
from crud.common import DomainError
from database import get_db, get_session_factory
from models import ImportJob, ImportJobStatus, User, UserRole
from responses import decimal_json
from routers.domain_errors import raise_domain_error
from routers.estimates import _read_within_limit, _validate_xlsx, job_response
from services.import_pipeline import run_import_job
from services.maintenance import purge_files_best_effort
from storage import Storage, get_storage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/tenders", tags=["tenders"])

ACTIVE_ROUND_INDEX = "uq_import_jobs_active_round"


class TenderCreate(BaseModel):
    object_id: int
    title: str
    tender_number: str
    rate_class_id: int | None = None
    notes: str | None = None


class TenderUpdate(BaseModel):
    title: str | None = None
    notes: str | None = None


class RoundCreate(BaseModel):
    stage_no: int
    label: str | None = None
    held_on: dt.date | None = None


class RoundUpdate(BaseModel):
    label: str | None = None
    held_on: dt.date | None = None


@router.get("")
def list_tenders(q: str | None = Query(default=None), page: int = Query(default=1, ge=1),
                 page_size: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    return crud_tenders.list_tenders(db, q=q, page=page, page_size=page_size)


@router.get("/{tender_id}")
def get_tender(tender_id: int, db: Session = Depends(get_db)):
    try:
        return decimal_json(crud_tenders.get_tender_card(db, tender_id))
    except DomainError as e:
        raise_domain_error(e)


@router.get("/{tender_id}/stage-summary")
def stage_summary(tender_id: int, offers: list[int] = Query(default=[]), db: Session = Depends(get_db)):
    """Свод по этапам одного участника (спека 2026-08-27-stage-summary-design.md §2.3, §2.16).

    `offers` — повторяющийся параметр. Пустой список НЕ отдаётся ошибкой валидации
    FastAPI: он доходит до домена и получает `too_few_offers` — тот же код, что у
    одного предложения, чтобы клиент различал причины по `code`, а не по форме 422.

    Ответ обёртывается decimal_json — это стандартный контракт маршрутизатора для
    денежных полей (всё уже квантовано и распечатано слоем ниже).
    """
    try:
        return decimal_json(crud_stage_summary.build_stage_summary(db, tender_id, offers))
    except DomainError as e:
        raise_domain_error(e)


@router.get("/{tender_id}/stage-summary/{work_category_id}")
def stage_position_drilldown(tender_id: int, work_category_id: int,
                             offers: list[int] = Query(default=[]), db: Session = Depends(get_db)):
    """Разложение статьи свода по работам (спека 2026-08-30-position-drilldown-design.md §2.11)."""
    try:
        return decimal_json(crud_position_drilldown.build_position_drilldown(
            db, tender_id, work_category_id, offers))
    except DomainError as e:
        raise_domain_error(e)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_tender(body: TenderCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    try:
        card = crud_tenders.create_tender(db, object_id=body.object_id, title=body.title,
                                          tender_number=body.tender_number, rate_class_id=body.rate_class_id,
                                          notes=body.notes)
    except DomainError as e:
        raise_domain_error(e)
    return decimal_json(card, status.HTTP_201_CREATED)


@router.patch("/{tender_id}")
def update_tender(tender_id: int, body: TenderUpdate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    fields = body.model_dump(exclude_unset=True)
    try:
        return decimal_json(crud_tenders.update_tender(db, tender_id, **fields))
    except DomainError as e:
        raise_domain_error(e)


@router.delete("/{tender_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tender(tender_id: int, db: Session = Depends(get_db), storage: Storage = Depends(get_storage),
                  _: User = Depends(require_admin)):
    try:
        file_keys = crud_tenders.delete_tender(db, tender_id)
    except DomainError as e:
        raise_domain_error(e)
    purge_files_best_effort(storage, file_keys, context=f"Удаление тендера {tender_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{tender_id}/rounds", status_code=status.HTTP_201_CREATED)
def create_round(tender_id: int, body: RoundCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    try:
        card = crud_tenders.create_round(db, tender_id, stage_no=body.stage_no, label=body.label, held_on=body.held_on)
    except DomainError as e:
        raise_domain_error(e)
    return decimal_json(card, status.HTTP_201_CREATED)


@router.patch("/{tender_id}/rounds/{round_id}")
def update_round(tender_id: int, round_id: int, body: RoundUpdate, db: Session = Depends(get_db),
                 _: User = Depends(require_admin)):
    fields = body.model_dump(exclude_unset=True)
    try:
        return decimal_json(crud_tenders.update_round(db, tender_id, round_id, **fields))
    except DomainError as e:
        raise_domain_error(e)


@router.delete("/{tender_id}/rounds/{round_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_round(tender_id: int, round_id: int, db: Session = Depends(get_db),
                 storage: Storage = Depends(get_storage), _: User = Depends(require_admin)):
    try:
        file_keys = crud_tenders.delete_round(db, tender_id, round_id)
    except DomainError as e:
        raise_domain_error(e)
    purge_files_best_effort(storage, file_keys, context=f"Удаление раунда {round_id} тендера {tender_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{tender_id}/rounds/{round_id}/import-jobs")
def list_round_import_jobs(tender_id: int, round_id: int, db: Session = Depends(get_db)):
    try:
        return crud_tenders.list_round_import_jobs(db, tender_id, round_id)
    except DomainError as e:
        raise_domain_error(e)


@router.get("/{tender_id}/rounds/{round_id}/unallocated")
def round_unallocated(tender_id: int, round_id: int, db: Session = Depends(get_db)):
    """Этапный разнос: разделы раунда без единого решения (спека этапного
    разноса §2.3). Права — аутентификация роутера, `member` вправе."""
    try:
        return decimal_json(crud_ru.build_round_unallocated(db, tender_id, round_id))
    except DomainError as e:
        raise_domain_error(e)
    except crud_ru.RoundMappingBroken:
        log.error("Этапный разнос: проекции раунда %s расходятся", round_id, exc_info=True)
        raise


@router.delete("/{tender_id}/participants/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_participant(tender_id: int, package_id: int, confirmation_token: str | None = Query(default=None),
                       db: Session = Depends(get_db), _: User = Depends(require_admin)):
    try:
        crud_tenders.delete_participant(db, tender_id, package_id, confirmation_token=confirmation_token)
    except DomainError as e:
        raise_domain_error(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{tender_id}/rounds/{round_id}/upload", status_code=status.HTTP_202_ACCEPTED)
def upload_round(
    tender_id: int, round_id: int, background: BackgroundTasks, response: Response,
    file: UploadFile = File(...), replace: bool = Form(default=False),
    db: Session = Depends(get_db), storage: Storage = Depends(get_storage),
    session_factory=Depends(get_session_factory), current_user: User = Depends(get_current_user),
):
    """Сводная таблица раунда → один job → N смет + baseline (спека §2.5).

    Правила повторной загрузки — те же три, что у договора (§5), с раундом
    вместо пары: 200 — текущий job (§2.12) с тем же sha256; 409 — сметы есть,
    файл другой, replace не передан; replace — admin.
    """
    if replace and current_user.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Замена загруженного раунда доступна только администратору.")
    try:
        rnd = crud_tenders.get_round(db, tender_id, round_id)
    except DomainError as e:
        raise_domain_error(e)

    payload = _read_within_limit(file, settings.UPLOAD_MAX_SIZE_MB * 1024 * 1024)
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой.")
    _validate_xlsx(file, payload)
    file_sha256 = hashlib.sha256(payload).hexdigest()

    running = crud_tenders.active_round_job(db, rnd.id)
    if running is not None:
        raise_domain_error(DomainError(
            status.HTTP_409_CONFLICT,
            f"Импорт этого раунда уже выполняется (задание {running.id}, статус «{running.status}»).",
            code="active_import", context={"job_id": running.id},
        ))

    current = crud_tenders.current_round_job(db, rnd.id)
    if current is not None and current.file_sha256 == file_sha256:
        response.status_code = status.HTTP_200_OK
        return job_response(db, current)
    if crud_tenders.round_estimate_ids(db, rnd.id) and not replace:
        raise_domain_error(DomainError(
            status.HTTP_409_CONFLICT,
            "Раунд уже загружен; для замены всех его смет повторите запрос с replace=true.",
            code="replace_required",
        ))

    file_key = storage.save(payload)
    try:
        job = ImportJob(round_id=rnd.id, filename=file.filename or "round.xlsx", file_key=file_key,
                        file_sha256=file_sha256, status=ImportJobStatus.pending.value)
        db.add(job)
        db.commit()
        db.refresh(job)
    except IntegrityError as exc:
        db.rollback()
        storage.delete(file_key)
        if ACTIVE_ROUND_INDEX in str(exc.orig):
            raise_domain_error(DomainError(
                status.HTTP_409_CONFLICT,
                "Импорт этого раунда уже запущен параллельным запросом.",
                code="active_import",
            ))
        raise
    except Exception:
        db.rollback()
        storage.delete(file_key)
        raise

    background.add_task(run_import_job, job.id, session_factory=session_factory, storage=storage, replace=replace)
    return job_response(db, job)
