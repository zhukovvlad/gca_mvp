"""Оркестратор задания импорта: две сессии, одна доменная транзакция (§5).

Транзакционная модель AGENTS.md §5, дословно:

* **сессия A** — промежуточные статусы `pending→parsing→importing→matching` и
  предупреждения парсера: отдельные короткие транзакции с немедленным commit,
  чтобы фронт видел прогресс. Статус `error` пишется сессией A ПОСЛЕ откатa
  домена и потому его переживает;
* **сессия B** — домен (смета, лоты, предложение, позиции, raw_data), матчинг и
  **финальный переход в `done` вместе с итоговыми счётчиками** — ОДНА атомарная
  транзакция. Состояния «смета в БД, а job не done» не существует: crash window
  закрыт.

Уточнение к §5, принятое фазой 4: предупреждения, ОПИСЫВАЮЩИЕ доменное состояние
(замена сметы, расхождение шапки, неизвестные единицы, эвристики), пишет сессия B
в той же транзакции, что и сами данные. Тем же аргументом, которым §5 требует
атомарности `done` со счётчиками: предупреждение о замене сметы не должно
существовать, если замены не произошло. Предупреждения парсера, наоборот, пишет
сессия A — они относятся к файлу, а не к домену, и обязаны переживать откат.

Мягкий таймаут (§3): прошедшее время проверяется НА ГРАНИЦАХ этапов. Никакого
жёсткого прерывания — оно оставило бы недописанную транзакцию и не дало бы
внятного сообщения.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from config import settings
from models import Contract, ImportJob, ImportJobStatus
from parser import EstimateParseError, parse_estimate
from parser.sanitize_text import NormalizationUnavailableError
from services.estimate_import import EstimateImportError, import_estimate
from services.matching import MatchCounters, match_positions
from services.unit_resolution import UnitResolver
from storage import Storage, StorageFileNotFound
from utils import utcnow_aware

log = logging.getLogger(__name__)


class ImportSoftTimeout(Exception):
    """Пайплайн не успел в отведённое время (§3, мягкий таймаут)."""


@dataclass(frozen=True)
class JobContext:
    """Минимум данных о задании, нужный пайплайну (читается сессией A один раз)."""

    job_id: int
    contract_id: int
    amendment_no: int | None
    file_key: str
    filename: str


# ---------------------------------------------------------------------------
#  Сессия A: статусы, предупреждения парсера, финальный error
# ---------------------------------------------------------------------------

def _warnings_concat(items: list[str]):
    """SQL-выражение `warnings || :items`.

    Дописывание делается в SQL, а не «прочитать-изменить-записать»: сессии A и B
    пишут в один и тот же массив в разные моменты, и чтение в Python потеряло бы
    то, что дописал другой.
    """
    return ImportJob.warnings.op("||")(sa.cast(sa.literal(json.dumps(items)), JSONB))


class StatusWriter:
    """Сессия A: каждый вызов — своя короткая транзакция с немедленным commit."""

    def __init__(self, session_factory: Callable[[], Session], job_id: int) -> None:
        self._session_factory = session_factory
        self.job_id = job_id

    def _run(self, values: dict) -> None:
        with self._session_factory() as db:
            db.execute(sa.update(ImportJob).where(ImportJob.id == self.job_id).values(**values))
            db.commit()

    def set_status(self, status: ImportJobStatus, **extra) -> None:
        self._run({"status": status.value, **extra})

    def add_warnings(self, items: list[str]) -> None:
        if not items:
            return
        self._run({"warnings": _warnings_concat(items)})

    def fail(self, error_text: str) -> None:
        """Статус `error` + текст. Пишется ПОСЛЕ откатa домена, поэтому выживает.

        Если сессия A в этот момент недоступна (БД упала, процесс убит), задание
        останется в незавершённом статусе — его вылечит startup-recovery при
        следующем запуске (§5). Поэтому исключение здесь только логируется:
        поверх пайплайна его никто не ждёт, а второй барьер уже есть.
        """
        try:
            self._run(
                {
                    "status": ImportJobStatus.error.value,
                    "error_text": error_text,
                    "finished_at": utcnow_aware(),
                }
            )
        except Exception:
            log.exception(
                "Задание %d: не удалось записать статус error; задание останется "
                "незавершённым до startup-recovery",
                self.job_id,
            )


# ---------------------------------------------------------------------------
#  Мягкий таймаут
# ---------------------------------------------------------------------------

class _Deadline:
    """Проверка прошедшего времени на границах этапов (§3)."""

    def __init__(self, minutes: int, *, now: datetime | None = None) -> None:
        self.started = now or utcnow_aware()
        self.limit = timedelta(minutes=minutes) if minutes > 0 else None

    def check(self, stage: str) -> None:
        if self.limit is None:
            return
        elapsed = utcnow_aware() - self.started
        if elapsed > self.limit:
            raise ImportSoftTimeout(
                f"Импорт не завершился за отведённые {self.limit.total_seconds() / 60:.0f} мин "
                f"(остановлено на этапе «{stage}», прошло "
                f"{elapsed.total_seconds() / 60:.1f} мин). Файл слишком большой либо "
                "сервер перегружен — попробуйте позже."
            )


# ---------------------------------------------------------------------------
#  Финал сессии B
# ---------------------------------------------------------------------------

def finalize_done(
    db: Session, job_id: int, *, counters: MatchCounters, warnings: list[str], now: datetime
) -> None:
    """Переводит задание в `done` со счётчиками — ПОСЛЕДНЯЯ операция сессии B.

    Коммитится вместе с доменом (§5): смета и её `done`-статус неразделимы.
    """
    values: dict = {
        "status": ImportJobStatus.done.value,
        "error_text": None,
        "finished_at": now,
        **counters.as_dict(),
    }
    if warnings:
        values["warnings"] = _warnings_concat(warnings)
    db.execute(sa.update(ImportJob).where(ImportJob.id == job_id).values(**values))


# ---------------------------------------------------------------------------
#  Пайплайн
# ---------------------------------------------------------------------------

def load_job_context(db: Session, job_id: int) -> JobContext:
    row = db.execute(
        sa.select(
            ImportJob.id,
            ImportJob.contract_id,
            ImportJob.amendment_no,
            ImportJob.file_key,
            ImportJob.filename,
        ).where(ImportJob.id == job_id)
    ).one_or_none()
    if row is None:
        raise LookupError(f"Задание импорта {job_id} не найдено")
    return JobContext(*row)


def run_import_job(
    job_id: int,
    *,
    session_factory: Callable[[], Session],
    storage: Storage,
    replace: bool = False,
    soft_timeout_minutes: int | None = None,
    parse: Callable = parse_estimate,
) -> None:
    """Выполняет задание импорта целиком. Исключения наружу не выпускает.

    Запускается из `BackgroundTasks` (§3: никаких брокеров), поэтому обязана сама
    довести задание либо до `done`, либо до `error` — наверху её никто не ждёт.

    Args:
        job_id: задание, созданное эндпоинтом загрузки.
        session_factory: фабрика сессий; отсюда берутся обе сессии, A и B.
        storage: хранилище, из которого читается исходный файл.
        replace: замена существующей сметы (§5, правило 3).
        soft_timeout_minutes: мягкий таймаут; по умолчанию из настроек.
        parse: точка внедрения парсера (тесты подают готовый `ParseResult`).
    """
    minutes = (
        settings.IMPORT_SOFT_TIMEOUT_MINUTES
        if soft_timeout_minutes is None
        else soft_timeout_minutes
    )
    deadline = _Deadline(minutes)
    status = StatusWriter(session_factory, job_id)

    try:
        with session_factory() as db:
            context = load_job_context(db, job_id)

        status.set_status(ImportJobStatus.parsing, started_at=utcnow_aware())

        # --- Этап 2: парсинг (без БД) ---
        with storage.get(context.file_key) as handle:
            parse_result = parse(handle)
        status.add_warnings(list(parse_result.warnings))
        deadline.check("парсинг")

        status.set_status(ImportJobStatus.importing)

        # --- Этапы 3–4 и финал: ОДНА транзакция сессии B ---
        with session_factory() as db:
            with db.begin():
                contract = db.get(Contract, context.contract_id)
                if contract is None:
                    raise EstimateImportError(
                        f"Договор {context.contract_id} не найден — импортировать смету не к чему."
                    )

                resolver = UnitResolver(db)
                outcome = import_estimate(
                    db,
                    contract=contract,
                    amendment_no=context.amendment_no,
                    data=parse_result.data,
                    parser_version=parse_result.parser_version,
                    import_job_id=job_id,
                    replace=replace,
                    unit_resolver=resolver,
                )
                deadline.check("импорт")

                # Статус пишет сессия A, пока транзакция B открыта. Блокировки нет:
                # вставка сметы взяла на строке job FOR KEY SHARE (это FK-ссылка),
                # а UPDATE неключевых колонок берёт FOR NO KEY UPDATE — эти режимы
                # в PostgreSQL совместимы.
                status.set_status(ImportJobStatus.matching)

                match = match_positions(db, outcome.positions_to_match)
                deadline.check("матчинг")

                finalize_done(
                    db,
                    job_id,
                    counters=match.counters,
                    warnings=outcome.warnings + match.warnings,
                    now=utcnow_aware(),
                )

        log.info(
            "Импорт задания %d завершён: estimate_id=%d, счётчики=%s",
            job_id,
            outcome.estimate_id,
            match.counters.as_dict(),
        )

    except StorageFileNotFound:
        log.exception("Задание %d: исходный файл недоступен", job_id)
        status.fail(
            "Исходный файл задания недоступен в хранилище — загрузите смету повторно."
        )
    except EstimateParseError as exc:
        log.warning("Задание %d: файл не разобран: %s", job_id, exc)
        status.fail(str(exc))
    except EstimateImportError as exc:
        log.warning("Задание %d: смета не импортирована: %s", job_id, exc)
        status.fail(str(exc))
    except NormalizationUnavailableError as exc:
        # Тихая деградация здесь недопустима: нелемматизированная строка развела бы
        # каталог, кэш и матчер (AGENTS.md §11). Джоб обязан упасть.
        log.exception("Задание %d: нормализация недоступна", job_id)
        status.fail(f"Матчинг невозможен: {exc}")
    except ImportSoftTimeout as exc:
        log.warning("Задание %d: мягкий таймаут: %s", job_id, exc)
        status.fail(str(exc))
    except Exception as exc:
        log.exception("Задание %d: непредвиденная ошибка импорта", job_id)
        status.fail(
            "Непредвиденная ошибка импорта, смета не сохранена. "
            f"Техническая причина: {type(exc).__name__}: {str(exc)[:500]}"
        )
