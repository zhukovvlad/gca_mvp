"""Загрузка сметы: `POST /api/v1/estimates/upload` (AGENTS.md §5, шаг 1).

Эндпоинт делает только лёгкую часть: валидацию, сохранение файла, sha256,
создание `import_job` и ответ с `job_id`. Тяжёлая часть уходит в
`BackgroundTasks` (§3 — никаких брокеров).

**Сохранённый файл удаляется во всех путях, где новый job не создан** (§5):
идемпотентный возврат существующего done-job, ответ 409, любая ошибка создания
job. Файл остаётся только у реально созданного задания — иначе хранилище копило
бы сирот, которых не вычистит даже ретенция §8 (она ходит по jobs).

Путь `/api/v1/...` задан §5 дословно, поэтому он отличается от остальных
роутеров проекта (`/api/auth`, `/api/admin`, `/api/units`, без версии).
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import get_current_user
from config import settings
from database import get_db, get_session_factory
from models import (
    TERMINAL_IMPORT_JOB_STATUSES,
    Contract,
    ImportJob,
    ImportJobStatus,
    User,
    UserRole,
)
from services.estimate_import import find_estimate
from services.import_pipeline import run_import_job
from storage import Storage, get_storage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/estimates", tags=["estimates"])

#: Первые байты любого XLSX: это ZIP-контейнер.
ZIP_MAGIC = b"PK\x03\x04"

#: Расширения, которые умеет открыть openpyxl.
ALLOWED_SUFFIXES = (".xlsx", ".xlsm")

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Частичный уникальный индекс, держащий лок пары (contract_id, amendment_no).
#: Создан raw SQL в миграции 0002; имя нужно, чтобы отличить его нарушение от
#: любого другого и ответить 409, а не 500.
ACTIVE_PAIR_INDEX = "uq_import_jobs_active_pair"

#: Внешний ключ `import_jobs.contract_id`. Имя присвоено PostgreSQL по умолчанию
#: (миграция 0002 объявила ключ без имени) — оно закреплено тестом предпосылки.
CONTRACT_FK_CONSTRAINT = "import_jobs_contract_id_fkey"


def job_response(db: Session, job: ImportJob) -> dict:
    """Единый формат задания импорта — общий с роутером `import_jobs`."""
    estimate = find_estimate(db, job.contract_id, job.amendment_no)
    return {
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
        # Смета текущей пары (contract_id, amendment_no), а не «смета этого job»:
        # у неудачного задания сметы нет, а у пары она может быть от предыдущего.
        "estimate_id": estimate.id if estimate else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _read_within_limit(upload: UploadFile, max_bytes: int) -> bytes:
    """Читает файл целиком, отказывая на превышении лимита (§5).

    Читается на один байт больше лимита: так превышение видно, не загружая в
    память весь присланный поток.
    """
    payload = upload.file.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Файл больше допустимых {settings.UPLOAD_MAX_SIZE_MB} МБ.",
        )
    return payload


def _validate_xlsx(upload: UploadFile, payload: bytes) -> None:
    name = (upload.filename or "").lower()
    if not name.endswith(ALLOWED_SUFFIXES):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Ожидается файл {' или '.join(ALLOWED_SUFFIXES)}; получен «{upload.filename}». "
            "Старый формат .xls нужно пересохранить как .xlsx.",
        )
    if not payload.startswith(ZIP_MAGIC):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Файл не похож на XLSX: внутри не ZIP-контейнер. Возможно, это .xls, "
            "CSV или повреждённый файл.",
        )


def _active_job(db: Session, contract_id: int, amendment_no: int | None) -> ImportJob | None:
    """Незавершённое задание этой пары — его защищает `uq_import_jobs_active_pair`.

    Проверяется явно, чтобы отдать понятный 409 вместо `IntegrityError` из
    частичного уникального индекса.
    """
    condition = (
        ImportJob.amendment_no.is_(None)
        if amendment_no is None
        else ImportJob.amendment_no == amendment_no
    )
    return (
        db.query(ImportJob)
        .filter(
            ImportJob.contract_id == contract_id,
            condition,
            ImportJob.status.notin_([s.value for s in TERMINAL_IMPORT_JOB_STATUSES]),
        )
        .first()
    )


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
def upload_estimate(
    background: BackgroundTasks,
    response: Response,
    file: UploadFile = File(...),
    contract_id: int = Form(...),
    amendment_no: int | None = Form(default=None),
    replace: bool = Form(default=False),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    session_factory=Depends(get_session_factory),
    current_user: User = Depends(get_current_user),
):
    """Принимает XLSX-смету и запускает импорт в фоне.

    Права: загрузка — любой аутентифицированный (`member`), `replace=true` —
    только `admin` (§5, §3).

    Ответы: `202` — задание создано; `200` — идемпотентный возврат уже
    выполненного задания с тем же файлом; `409` — смета уже загружена (нужен
    `replace=true`) либо импорт этой пары уже идёт; `404` — договора нет либо
    он удалён, пока загружался файл.
    """
    if replace and current_user.role != UserRole.admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Замена загруженной сметы доступна только администратору."
        )
    if amendment_no is not None and amendment_no <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Номер допсоглашения нумеруется с 1; исходная смета загружается без него.",
        )

    contract = db.get(Contract, contract_id)
    if contract is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Договор {contract_id} не найден.")

    payload = _read_within_limit(file, settings.UPLOAD_MAX_SIZE_MB * 1024 * 1024)
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой.")
    _validate_xlsx(file, payload)
    file_sha256 = hashlib.sha256(payload).hexdigest()

    # Проверки, не требующие сохранённого файла, — до сохранения: так меньше
    # путей, на которых файл придётся удалять.
    running = _active_job(db, contract_id, amendment_no)
    if running is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Импорт этой сметы уже выполняется (задание {running.id}, статус "
            f"«{running.status}»). Дождитесь его завершения.",
        )

    existing = find_estimate(db, contract_id, amendment_no)
    if existing is not None:
        source_job = (
            db.get(ImportJob, existing.import_job_id) if existing.import_job_id else None
        )
        # Правило 1 (§5) — идемпотентность. Проверяется не «есть ли где-то
        # done-job с таким sha256», а «текущая смета этой пары загружена ИМЕННО
        # этим файлом»: иначе повторная загрузка файла, который был вытеснен
        # replace-ом, отвечала бы «уже загружено», хотя в БД лежит другая смета.
        if (
            source_job is not None
            and source_job.file_sha256 == file_sha256
            and source_job.status == ImportJobStatus.done.value
        ):
            log.info(
                "Идемпотентный ответ: смета договора %s (доп. %s) уже загружена заданием %d",
                contract_id,
                amendment_no,
                source_job.id,
            )
            # 200, а не 202: ничего не создано и ничего не запущено.
            response.status_code = status.HTTP_200_OK
            return job_response(db, source_job)

        # Правило 2 (§5).
        if not replace:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Смета уже загружена (estimate_id={existing.id}); для замены повторите "
                "запрос с replace=true.",
            )

    file_key = storage.save(payload)
    try:
        job = ImportJob(
            contract_id=contract_id,
            amendment_no=amendment_no,
            filename=file.filename or "estimate.xlsx",
            file_key=file_key,
            file_sha256=file_sha256,
            status=ImportJobStatus.pending.value,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
    except IntegrityError as exc:
        # Файл остаётся только у реально созданного задания (§5).
        db.rollback()
        storage.delete(file_key)
        # Договор удалён между проверкой его существования и вставкой задания
        # (спека §2.3). Распознаём структурированной диагностикой, а не поиском
        # подстроки: имя в тексте ошибки соседствует с пользовательскими данными.
        diag = getattr(exc.orig, "diag", None)
        if (
            getattr(exc.orig, "sqlstate", None) == "23503"
            and getattr(diag, "constraint_name", None) == CONTRACT_FK_CONSTRAINT
        ):
            log.info("Договор %s удалён, пока загружался файл", contract_id)
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Договор {contract_id} удалён, пока загружался файл. Смета не "
                "импортирована.",
            ) from exc
        if ACTIVE_PAIR_INDEX in str(exc.orig):
            # Гонка: между проверкой активного задания и INSERT такое же задание
            # успел создать параллельный запрос. Данные защищены индексом, но
            # клиенту это та же ситуация, что и синхронная проверка выше, —
            # значит и ответ обязан быть тем же 409, а не 500.
            log.info(
                "Гонка загрузок пары (contract_id=%s, amendment_no=%s): проиграли лок",
                contract_id,
                amendment_no,
            )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Импорт этой сметы уже запущен параллельным запросом. Дождитесь его "
                "завершения и проверьте результат.",
            ) from exc
        raise
    except Exception:
        db.rollback()
        storage.delete(file_key)
        raise

    background.add_task(
        run_import_job,
        job.id,
        session_factory=session_factory,
        storage=storage,
        replace=replace,
    )
    return job_response(db, job)
