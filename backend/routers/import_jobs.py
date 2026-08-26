"""Задания импорта: поллинг статуса и выдача исходного файла (AGENTS.md §5, §8).

`GET /api/v1/import-jobs/{id}` — то, что опрашивает экран загрузки: статус,
счётчики, предупреждения, текст ошибки.

`GET /api/v1/import-jobs/{id}/file` — выдача исходного XLSX. Только через этот
авторизованный эндпоинт: статики над директорией хранилища нет, имя на диске —
непрозрачный ключ, оригинальное имя живёт в БД (§8).
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import ImportJob
from routers.estimates import XLSX_MEDIA_TYPE, job_response
from storage import Storage, StorageFileNotFound, StorageKeyError, get_storage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/import-jobs", tags=["import-jobs"])

#: Размер чтения при отдаче файла. XLSX — двоичный ZIP: отдай мы StreamingResponse
#: сам файловый объект, Starlette итерировал бы его ПОСТРОЧНО (файл — итератор по
#: строкам), нарезая ответ по случайным байтам 0x0A, и никогда не закрыл бы хендл.
_DOWNLOAD_CHUNK_SIZE = 64 * 1024


def _stream_chunks(handle, chunk_size: int = _DOWNLOAD_CHUNK_SIZE):
    """Читает файл кусками. Закрытием хендла владеет `ClosingStreamingResponse`."""
    while chunk := handle.read(chunk_size):
        yield chunk


class ClosingStreamingResponse(StreamingResponse):
    """StreamingResponse, детерминированно закрывающий хендл хранилища.

    `finally` внутри генератора закрывал хендл только при ПОЛНОМ прочтении: при
    разрыве соединения `send()` падает, Starlette перестаёт потреблять генератор
    и хранит его в `body_iterator` — `finally` не выполняется, пока жив сам
    объект ответа, то есть закрытие снова доставалось сборщику мусора
    (воспроизведено прямым ASGI-вызовом с падающим send; дефект второго
    внутреннего круга ревью). Поэтому закрытие поднято на уровень
    `Response.__call__`: его `finally` выполняется при любом исходе — полное
    чтение, разрыв, отмена. `close()` файла идемпотентен.

    Детерминизм здесь не эстетика: на Windows открытый хендл блокирует удаление
    файла, то есть незакрытая выдача мешала бы ретенции §8 в этом же процессе.
    """

    def __init__(self, handle, **kwargs) -> None:
        self._handle = handle
        super().__init__(_stream_chunks(handle), **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._handle.close()


def _get_job(db: Session, job_id: int) -> ImportJob:
    job = db.get(ImportJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Задание импорта {job_id} не найдено.")
    return job


@router.get("/{job_id}")
def get_import_job(job_id: int, db: Session = Depends(get_db)):
    """Статус задания импорта — источник для поллинга на фронте."""
    return job_response(db, _get_job(db, job_id))


@router.get("/{job_id}/file")
def download_import_job_file(
    job_id: int,
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    """Отдаёт исходный XLSX задания.

    `410 Gone` — если файл уже удалён ретенцией §8 (файлы error-jobs старше
    настроенного срока). Запись задания остаётся, пока жив её владелец —
    договор или раунд тендера, это аудит (v6.7); поэтому «нет файла» и «нет
    задания» — разные ответы.
    """
    job = _get_job(db, job_id)
    try:
        handle = storage.get(job.file_key)
    except StorageFileNotFound as exc:
        raise HTTPException(
            status.HTTP_410_GONE,
            "Исходный файл задания больше не хранится: он удалён по ретенции. "
            "Сама запись задания остаётся, пока жив владелец — договор либо раунд тендера.",
        ) from exc
    except StorageKeyError as exc:
        # Ключ в БД испорчен — это не ошибка клиента.
        log.error("Задание %d: некорректный file_key в БД", job_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Некорректная ссылка на файл задания."
        ) from exc

    # RFC 5987: имена файлов у нас кириллические, в голом filename= их нельзя.
    ascii_fallback = "estimate.xlsx"
    disposition = (
        f"attachment; filename=\"{ascii_fallback}\"; "
        f"filename*=UTF-8''{quote(job.filename, safe='')}"
    )
    return ClosingStreamingResponse(
        handle,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": disposition},
    )
