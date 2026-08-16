"""Обслуживание при старте приложения (AGENTS.md §5, §8).

Две задачи, обе выполняются один раз при подъёме процесса:

1. **startup-recovery** зависших `import_jobs`: всё, что осталось в
   `pending|parsing|importing|matching`, переводится в `error`. Корректно ровно
   потому, что MVP запускается с ОДНИМ worker-процессом uvicorn (AGENTS.md §3):
   при живом втором worker'е мы бы «уронили» его текущие задания. Это не дефолт,
   который можно поменять, — это условие корректности.

2. **ретенция файлов** error-jobs старше `ERROR_JOB_FILE_RETENTION_DAYS`.
   Проверка «при старте» означает: файл удаляется при первом запуске после
   истечения срока, а не ровно в срок (§8). Сама запись job ретенцию переживает —
   это аудит; уносит её только удаление договора (v6.7). Факт удаления файла
   пишется в `warnings`, чтобы у выдачи файла был человекочитаемый ответ, почему
   его больше нет.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models import ACTIVE_IMPORT_JOB_STATUSES, ImportJob, ImportJobStatus
from storage import Storage, StorageKeyError
from utils import utcnow_aware

log = logging.getLogger(__name__)

RECOVERY_ERROR_TEXT = "Импорт прерван перезапуском приложения, повторите загрузку."

_ACTIVE_STATUS_VALUES = tuple(s.value for s in ACTIVE_IMPORT_JOB_STATUSES)


def recover_interrupted_jobs(db: Session) -> int:
    """Переводит все незавершённые задания в `error`.

    Args:
        db: сессия; коммит — на вызывающей стороне.

    Returns:
        Число переведённых заданий.
    """
    now = utcnow_aware()
    result = db.execute(
        update(ImportJob)
        .where(ImportJob.status.in_(_ACTIVE_STATUS_VALUES))
        .values(
            status=ImportJobStatus.error.value,
            error_text=RECOVERY_ERROR_TEXT,
            finished_at=now,
        )
    )
    recovered = result.rowcount or 0
    if recovered:
        log.warning("startup-recovery: %d зависших заданий импорта переведены в error", recovered)
    return recovered


def purge_expired_error_job_files(db: Session, storage: Storage, *, retention_days: int) -> int:
    """Удаляет файлы error-jobs, завершённых раньше, чем `retention_days` назад.

    Отметкой времени служит `finished_at`, а при её отсутствии — `created_at`
    (job мог оказаться в `error` до появления финальной метки).

    Args:
        db: сессия; коммит — на вызывающей стороне.
        storage: хранилище файлов.
        retention_days: срок хранения; 0 и меньше отключает ретенцию.

    Returns:
        Число фактически удалённых файлов.
    """
    if retention_days <= 0:
        return 0

    cutoff = utcnow_aware() - timedelta(days=retention_days)
    stale = db.execute(
        select(ImportJob)
        .where(
            ImportJob.status == ImportJobStatus.error.value,
            ImportJob.finished_at.is_(None) | (ImportJob.finished_at < cutoff),
            ImportJob.created_at < cutoff,
        )
        .order_by(ImportJob.id)
    ).scalars()

    purged = 0
    for job in stale:
        # Ошибки изолируются ПО ЗАДАНИЯМ: испорченный file_key (StorageKeyError)
        # или занятый файл (OSError) не должны обрывать ретенцию остальных —
        # иначе один дефектный ряд копил бы файлы до ручного вмешательства.
        try:
            # exists() до delete(): пропускаем уже вычищенные задания, иначе
            # warning дописывался бы при каждом старте приложения.
            if not storage.exists(job.file_key):
                continue
            storage.delete(job.file_key)
        except (StorageKeyError, OSError):
            log.exception(
                "Ретенция §8: файл задания %d (file_key=%r) не удалён — пропущен",
                job.id,
                job.file_key,
            )
            continue
        job.warnings = [
            *(job.warnings or []),
            f"Исходный файл удалён по ретенции ({retention_days} дн.) "
            f"{utcnow_aware().date().isoformat()}; запись задания сохранена как аудит.",
        ]
        purged += 1

    if purged:
        log.info("Ретенция §8: удалено %d файлов error-jobs старше %d дн.", purged, retention_days)
    return purged


def run_startup_maintenance(session_factory, storage: Storage, *, retention_days: int) -> tuple[int, int]:
    """Выполняет обе задачи — каждую своей транзакцией.

    Транзакции РАЗДЕЛЕНЫ, и это не косметика: задачи имеют разную обязательность.

    * **recovery обязателен.** Не выполнившийся recovery оставляет незавершённые
      задания активными, а они держат `uq_import_jobs_active_pair` — повторная
      загрузка той же пары будет получать 409 до следующего перезапуска. Это
      прямое нарушение инварианта §5, поэтому исключение уходит наружу и роняет
      старт приложения: громкий отказ честнее полурабочего сервиса, который
      вечно отвечает «импорт уже идёт»;
    * **ретенция — best-effort.** Недоступное хранилище не мешает работать, и
      файл, не удалённый сегодня, удалится при следующем запуске (§8 обещает
      «проверку при старте», а не срок). Её ошибка только логируется — и,
      благодаря отдельной транзакции, НЕ откатывает recovery.

    Returns:
        Кортеж (переведено в error, удалено файлов).

    Raises:
        Exception: любая ошибка recovery — она обязана остановить старт.
    """
    with session_factory() as db:
        recovered = recover_interrupted_jobs(db)
        db.commit()

    purged = 0
    try:
        with session_factory() as db:
            purged = purge_expired_error_job_files(db, storage, retention_days=retention_days)
            db.commit()
    except Exception:
        log.exception("Ретенция файлов error-заданий не выполнена; recovery это не отменяет")

    return recovered, purged


def purge_files_best_effort(storage: Storage, file_keys: Iterable[str], *, context: str) -> int:
    """Удаляет файлы по ключам, изолируя ошибки ПО КЛЮЧАМ.

    Вызывается ПОСЛЕ коммита доменной транзакции (спека §2.4): ошибка носителя
    не вправе откатывать уже принятое решение, поэтому она только пишется в лог.
    Цена названа границей спеки §4.1: неудалённый файл остаётся на диске
    навсегда — ретенция §8 ходит по заданиям, а записи задания уже нет.
    """
    purged = 0
    for key in file_keys:
        try:
            if storage.delete(key):
                purged += 1
        except (StorageKeyError, OSError):
            log.exception("%s: файл %r не удалён — пропущен", context, key)
    return purged
