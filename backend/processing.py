"""Ядро обработки документов: парсинг (фаза A) + персистенция (фаза B).

См. docs/superpowers/specs/2026-07-16-async-processing-design.md.
На ступени 0 process_document вызывается инлайн (await в хэндлере).
"""
# from __future__ import annotations — постоянно откладывает вычисление аннотаций,
# что позволяет типизировать persist_parse_result(outcome: ParseOutcome) без
# импорта ParseOutcome в рантайме (pdf_parser импортирует ProcessingError/
# TransientError/PermanentError из этого модуля — прямой импорт по кругу упал бы).
from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from crud.materials import VALID_CALC_ROLES, UnknownMaterialType, get_or_create_material_class
from crud.suppliers import get_or_create_supplier
from crud.units import load_alias_map, normalize_item
from models import Document, Invoice, InvoiceItem

if TYPE_CHECKING:  # только для типизации — не вызывает циклический импорт в рантайме
    from pdf_parser import ParseOutcome

logger = logging.getLogger(__name__)

_LAST_ERROR_MAXLEN = 500


class ProcessingError(Exception):
    """Базовая доменная ошибка попытки обработки.

    Несёт накопленный учёт платных вызовов OpenRouter (cost_usd, paid_calls),
    чтобы error-путь мог начислить стоимость даже при провале (инвариант
    parse-cost-tracking: HTTP 200 → деньги потрачены → стоимость учтена).
    """

    def __init__(self, message: str, *, cost_usd: Decimal = Decimal(0), paid_calls: int = 0,
                 http_status: int | None = None):
        """Сохраняет сообщение, накопленный учёт стоимости и подсказку HTTP-статуса.

        http_status задают только доменные ошибки, которые на ступени 0 должны
        дойти до клиента прежним HTTP-кодом (deskew: 413 слишком много страниц,
        502 сервис распознавания недоступен) — см. AC-S0-8. Ошибки парсинга
        http_status не задают → гасятся в status='error' + 200.
        """
        super().__init__(message)
        self.message = message
        self.cost_usd = cost_usd
        self.paid_calls = paid_calls
        self.http_status = http_status


class TransientError(ProcessingError):
    """Транзиентная ошибка (S3 недоступен, httpx timeout/сетевой сбой, 5xx/429/408 OpenRouter, сбой detect).

    На ступени 2 получит retry-политику; на ступени 0/1 ведёт к терминальному error.
    """


class PermanentError(ProcessingError):
    """Перманентная ошибка контента (невалидный JSON, провал сверки итогов,

    finish_reason=length, doc_type != invoice, слишком много страниц для deskew).
    Не ретраится никогда.
    """


def _dec(value):
    """LLM/JSON float → Decimal через str() (отсекает бинарную погрешность). None-safe."""
    return None if value is None else Decimal(str(value))


def _resolve_material_class_id(db: Session, item, *, document_id: int) -> int | None:
    """Резолвит material_class позиции в id (flush, без commit) — перенос из старого parse.

    Только для item_type='material' с непустым material_class. Неизвестный calc_role → 'base';
    неизвестный material_type → 'other'.
    """
    if item.item_type != "material" or not item.material_class:
        return None
    raw_role = str(item.calc_role or "base").strip().lower()
    if raw_role not in VALID_CALC_ROLES:
        logger.warning("[doc=%d] неизвестный calc_role=%r → 'base'", document_id, raw_role)
        raw_role = "base"
    try:
        mc = get_or_create_material_class(
            db, name=item.material_class, material_type=item.material_type or "other",
            calc_role=raw_role, commit=False,
        )
    except UnknownMaterialType:
        mc = get_or_create_material_class(
            db, name=item.material_class, material_type="other", calc_role=raw_role, commit=False,
        )
    return mc.id


def persist_parse_result(db: Session, doc_id: int, outcome: ParseOutcome) -> None:
    """Фаза B: в одной транзакции заменить СФ документа результатом парсинга.

    Удаляет старые СФ, резолвит поставщиков/классы (flush), вставляет новые СФ,
    ставит status='parsed', инкремент стоимости, единственный commit. Никаких
    промежуточных commit — инвариант транзакционности (§2.3/§2.4).

    Все выходы-ошибки НЕСУТ учёт стоимости outcome (фаза A уже оплачена → error-путь
    обязан начислить, инвариант §2.3). Детерминированный сбой (flush/insert) → явный
    rollback (сессия в failed state, без него последующая error-запись через ту же
    сессию упадёт PendingRollbackError) + TransientError с учётом. Сбой из commit
    (ambiguous) → тоже TransientError; условная error-запись (§2.3) разрулит, лёг ли swap.
    """
    # FOR UPDATE: сериализует фазу B с мутирующими эндпоинтами роутера (S0-8) —
    # верификация/удаление СФ, удаление/переразбор документа ждут эту блокировку
    # или блокируют её сами (см. routers/invoices._load_document_locked).
    doc = db.query(Document).filter(Document.id == doc_id).with_for_update().first()
    if doc is None:
        raise PermanentError(f"Документ id={doc_id} не найден на фазе B",
                             cost_usd=outcome.cost_usd, paid_calls=outcome.paid_calls)
    # Повторная проверка под блокировкой строки: verified-СФ могла появиться после
    # guard-перехода (try_acquire_processing), пока шёл длительный LLM-вызов фазы A
    # (S0-8) — эндпоинт-проверка на входе в reparse/deskew этого уже не гарантирует.
    # Ошибка несёт cost — фаза A оплачена (инвариант §2.3); старый набор СФ не трогаем.
    if any(inv.verified for inv in doc.invoices):
        raise PermanentError("Документ содержит подтверждённые СФ — переразбор отменён",
                             cost_usd=outcome.cost_usd, paid_calls=outcome.paid_calls)

    try:
        for inv in list(doc.invoices):
            db.delete(inv)

        aliases = load_alias_map(db)
        for pinv in outcome.invoices:
            _inn = (pinv.supplier_inn.strip() or None) if pinv.supplier_inn else None
            _name = (pinv.supplier_name.strip() or None) if pinv.supplier_name else None
            if not _name:
                _inn = None
            supplier_id = None
            if _name:
                supplier = get_or_create_supplier(db, name=_name, inn=_inn)  # уже flush-only
                supplier_id, _name, _inn = supplier.id, supplier.name, supplier.inn

            invoice = Invoice(
                document_id=doc_id, supplier_id=supplier_id, number=pinv.number, date=pinv.date,
                supplier_name=_name, supplier_inn=_inn, vat_rate=_dec(pinv.vat_rate),
                ai_confidence=pinv.confidence,
            )
            db.add(invoice)
            db.flush()

            for item in pinv.items:
                mc_id = _resolve_material_class_id(db, item, document_id=doc_id)
                quantity, unit_price = _dec(item.quantity), _dec(item.unit_price)
                norm = normalize_item(item.unit, quantity, unit_price, aliases)
                db.add(InvoiceItem(
                    invoice_id=invoice.id, raw_name=item.raw_name, item_type=item.item_type,
                    material_class_id=mc_id, quantity=quantity, raw_unit=item.unit,
                    normalized_unit_id=norm.normalized_unit_id if norm else None,
                    normalized_quantity=norm.normalized_quantity if norm else None,
                    normalized_unit_price=norm.normalized_unit_price if norm else None,
                    unit_price=unit_price, amount=_dec(item.amount), vat_amount=_dec(item.vat_amount),
                ))

        doc.status = "parsed"
        doc.last_error = None
        doc.doc_type = "invoice"  # успешный разбор → документ точно СФ
        # (на error-пути doc_type НЕ трогается — см. write_processing_error: документ
        #  хранит живые старые СФ, флип invoice→unknown был бы противоречив.)
        # Атомарный SQL-инкремент (x = x + v) — защита от гонки параллельных разборов.
        doc.parse_cost_usd = Document.parse_cost_usd + outcome.cost_usd
        doc.parse_count = Document.parse_count + outcome.paid_calls
        db.commit()
    except ProcessingError:
        # verified-abort (Task 9) и doc-not-found уже несут cost — не оборачиваем.
        # Мутаций либо не было (raise до тела), либо откатятся close()/rollback вызывающего.
        raise
    except Exception as exc:  # noqa: BLE001 — детерминированный сбой ИЛИ ambiguous commit
        db.rollback()
        # Сырой exc (для SQLAlchemy-исключений — текст SQL + параметры) НЕ должен попасть
        # в last_error, отдаваемый через API — сообщение пользователю стабильное и общее,
        # подробности только в логе (санитизация по тому же приёму, что и FIX 6).
        logger.warning(f"[doc={doc_id}] фаза B: ошибка сохранения: {exc!r}")
        raise TransientError("Ошибка сохранения результата разбора",
                             cost_usd=outcome.cost_usd, paid_calls=outcome.paid_calls) from exc
    logger.info(f"[doc={doc_id}] Фаза B: сохранено СФ {len(outcome.invoices)}, статус parsed")


def get_processing_session_factory():
    """FastAPI-dependency: фабрика сессий для инлайн-обработки (F1).

    Возвращает SessionLocal (поздний импорт — не связываем на этапе модуля, чтобы
    тестовый override и патч database.SessionLocal работали). В тестах переопределяется
    через app.dependency_overrides на тест-фабрику, чтобы обработка видела тест-данные.
    """
    from database import SessionLocal
    return SessionLocal


def _is_connection_error(exc: DBAPIError) -> bool:
    """Потеря соединения по SQLAlchemy-флагу ИЛИ SQLSTATE класса 08 (connection exception).

    Только такие ошибки из commit ретраебельны (запись идемпотентна). Прочие
    OperationalError (deadlock 40P01, lock_timeout, statement cancellation 57014, …)
    и любой другой DBAPIError детерминированы → пробрасываются вызывающим немедленно (F8).
    """
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return bool(exc.connection_invalidated or (sqlstate and str(sqlstate).startswith("08")))


_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.1, 0.25)


def _retry_delay(attempt: int) -> float:
    """Пауза (сек) ПЕРЕД следующей попыткой после connection-error на попытке `attempt`.

    Небольшой backoff (0.1, затем 0.25 с) даёт соединению/пулу шанс восстановиться
    между попытками условной error-записи — спека §2.3 (line 156) требует «2–3 попытки
    С ПАУЗОЙ». Если попыток больше, чем ступеней в графике, переиспользуем последнюю.
    """
    return _RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]


def write_processing_error(session_factory, doc_id: int, message: str, *,
                           cost_usd: Decimal, paid_calls: int, retries: int = 3,
                           sleep=time.sleep) -> None:
    """Идемпотентная условная error-запись (§2.3).

    UPDATE ... WHERE status='processing' — при уже закоммитившемся swap (ambiguous
    commit) предикат ложен после ожидания блокировки (EvalPlanQual), rowcount 0.
    Ретраим ТОЛЬКО потерю соединения из самого commit (`connection_invalidated` ИЛИ
    SQLSTATE класса 08 — см. `_is_connection_error`) — запись идемпотентна. Прочие ошибки,
    ВКЛЮЧАЯ не-connection `OperationalError` (deadlock 40P01, lock_timeout, statement
    cancellation 57014) и любой другой `DBAPIError`/Exception, детерминированы → НЕ глотаем,
    пробрасываем, чтобы баг падал в тестах, а не оставлял документ processing молча (F8).
    Между connection-ретраями выдерживаем паузу `_retry_delay(attempt)` (спека line 156);
    после ПОСЛЕДНЕЙ попытки не спим. `sleep` инъектируется, чтобы unit-тест не ждал реально.
    Пауза синхронная (функция sync; на S0 вызывается инлайн из process_document) — короткий
    блок event loop на вырожденном пути потери БД (суммарно ≤0.35 с) принят осознанно.
    Исчерпание connection-ретраев → лог critical, документ остаётся processing (доберёт
    startup-sweep S1-4); стоимость этой попытки теряется (at-most-once).
    """
    # doc_type НЕ трогаем: при parse-then-swap error-документ хранит живые старые СФ,
    # флип doc_type invoice→unknown у документа с СФ противоречив (§2.3 SQL его не содержит).
    sql = text(
        "UPDATE documents SET status='error', last_error=:msg, "
        "parse_cost_usd = parse_cost_usd + :cost, parse_count = parse_count + :calls "
        "WHERE id=:id AND status='processing'"
    )
    params = {"msg": message[:_LAST_ERROR_MAXLEN], "cost": cost_usd, "calls": paid_calls, "id": doc_id}
    for attempt in range(1, retries + 1):
        try:
            with session_factory() as db:
                result = db.execute(sql, params)
                db.commit()
            if result.rowcount == 0:
                # rowcount 0: swap уже лёг (parsed), ИЛИ документ удалён/уже error —
                # различить постфактум нельзя; во всех случаях повторно писать нечего.
                logger.warning(f"[doc={doc_id}] error-запись пропущена (rowcount 0): "
                               f"документ не в статусе processing (swap лёг / удалён / уже error)")
            return
        except DBAPIError as exc:
            # Только потеря соединения ретраебельна; прочий DBAPIError (в т.ч. deadlock/
            # lock_timeout/cancel OperationalError, ProgrammingError) детерминирован — проброс.
            if not _is_connection_error(exc):
                raise
            logger.warning(f"[doc={doc_id}] error-запись, попытка {attempt}/{retries} "
                           f"не удалась (потеря соединения): {exc}")
            if attempt < retries:      # перед следующей попыткой — пауза; после последней не спим
                sleep(_retry_delay(attempt))
    logger.critical(f"[doc={doc_id}] error-запись НЕ выполнена: БД недоступна. "
                    f"Документ остаётся processing до рестарта/ручного восстановления; "
                    f"стоимость ${cost_usd} не учтена.")


def _is_not_found(exc: Exception) -> bool:
    """S3 «нет объекта» vs транзиентный сбой (порт из routers/invoices старой версии).

    Различение нужно в `_run_deskew`: «нет бэкапа» — ожидаемый случай (fallback на
    исходный s3_key), а транзиентный сбой S3 обязан стать TransientError, иначе можно
    было бы затереть настоящий оригинал под видом «бэкапа нет».
    """
    from botocore.exceptions import ClientError
    if isinstance(exc, FileNotFoundError):            # in-memory-фикстура тестов
        return True
    if isinstance(exc, ClientError):                  # MinIO/S3 в проде
        return exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NoSuchBucket")
    return False


async def _run_deskew(s3_key: str, *, accounting: dict | None = None) -> tuple[bytes, Decimal, int]:
    """Коррекция ориентации от оригинала. Возвращает (pdf_для_парсинга, detect_cost, detect_calls).

    Источник — всегда {s3_key}.orig (идемпотентность повторных deskew). Различаем «нет
    бэкапа» (fallback на s3_key) и транзиентный сбой S3 (→ TransientError 502, чтобы не
    затереть настоящий оригинал). При ненулевых поворотах: одноразовый бэкап оригинала +
    перезапись s3_key исправленными байтами. file_hash не пересчитываем (Q6).

    ПОСЛЕ оплаченного detect любой S3-сбой оборачивается в TransientError с detect-cost —
    стоимость не теряется в generic-ветке process_document (F3, §2.3).

    accounting — необязательный mutable-аккумулятор {"cost_usd", "paid_calls"} (FIX 4):
    заполняем его СРАЗУ после `deskew_pdf` (ДО пост-detect S3-вызовов ниже — backup/
    overwrite/current-download), потому что CancelledError, прилетевший ИЗ этих awaits,
    не является Exception и обходит `except Exception` ниже — раньше это теряло уже
    оплаченный detect-cost, если аккумулятор обновлялся только в вызывающем
    `run_processing_attempt` ПОСЛЕ возврата из этой функции. Возвращаемые
    detect_cost/detect_calls остаются источником истины для ProcessingError/success
    путей (см. run_processing_attempt) — accounting и возврат НЕ читаются одновременно
    одним и тем же путём, поэтому стоимость не задваивается.
    """
    import pdf_orientation
    from s3 import download_file_async, upload_file_async

    orig_key = f"{s3_key}.orig"
    try:
        source_bytes = await download_file_async(orig_key)
        has_backup = True
    except Exception as e:  # noqa: BLE001 — до detect: сбой S3 не оплачен
        if not _is_not_found(e):
            raise TransientError("Хранилище временно недоступно", http_status=502) from e
        # .orig не найден (ожидаемо — бэкапа ещё нет) → fallback на исходный s3_key.
        # Транзиентный сбой ЭТОГО скачивания — симметрично .orig-ветке выше: detect ещё
        # не оплачен, поэтому тоже без cost, но обязан стать классифицированным 502,
        # а не голым исключением.
        try:
            source_bytes = await download_file_async(s3_key)
        except Exception as e2:  # noqa: BLE001 — до detect: сбой S3 не оплачен
            raise TransientError("Хранилище временно недоступно", http_status=502) from e2
        has_backup = False

    # deskew_pdf бросает TransientError ДО чтения cost при транспортном сбое detect
    # (тогда detect не оплачен); при битом envelope платного 200 — ошибку С detect-cost
    # (§2.5 спеки LLM-провайдера: retryable=False, cost, paid_calls=1 — биллинг в exc);
    # при сбое apply_rotations — уже С detect-cost (см. pdf_orientation).
    corrected, rotations, detect_cost = await pdf_orientation.deskew_pdf(source_bytes)
    detect_calls = 1
    if accounting is not None:
        # ДО пост-detect S3-awaits ниже (FIX 4) — CancelledError из них не Exception,
        # обходит except ниже, и без этой ранней записи detect-cost терялся бы.
        accounting["cost_usd"] += detect_cost
        accounting["paid_calls"] += detect_calls

    # Всё, что после успешного detect, оплачено — S3-сбой не должен обнулять учёт (F3).
    try:
        if any(r % 360 for r in rotations):
            if not has_backup:
                await upload_file_async(source_bytes, orig_key)   # одноразовый бэкап оригинала
            await upload_file_async(corrected, s3_key)            # перезапись основным ключом
            return corrected, detect_cost, detect_calls

        # Нули: коррекция не нужна ЭТИМ прогоном. Но если .orig уже существует (has_backup),
        # значит прошлый deskew исправил s3_key — а detect сейчас флейкнул в нули. Парсить
        # повёрнутый .orig нельзя (перезатрёт хороший набор СФ парсом кривого файла) — берём
        # текущий s3_key (исправленную версию). Без бэкапа .orig == s3_key, source и есть текущий.
        if has_backup:
            current = await download_file_async(s3_key)
            return current, detect_cost, detect_calls
        return source_bytes, detect_cost, detect_calls
    except ProcessingError:
        # Доменная ошибка (Transient/Permanent) уже несёт свой http_status/cost — не переупаковываем,
        # пробрасываем как есть.
        raise
    except Exception as exc:  # noqa: BLE001 — S3-сбой ПОСЛЕ оплаченного detect
        # http_status=502 (FIX 5) — держит тот же HTTP-контракт, что и пред-detect S3-сбои
        # (иначе process_document(reraise=True) не пробросит её и deskew-эндпоинт молча
        # вернёт 200 вместо 502). Сообщение — стабильный текст без сырого exc (FIX 6).
        logger.warning(f"[s3_key={s3_key}] _run_deskew: ошибка S3 после коррекции ориентации: {exc!r}")
        raise TransientError("Ошибка хранилища при коррекции ориентации", http_status=502,
                             cost_usd=detect_cost, paid_calls=detect_calls) from exc


async def run_processing_attempt(session_factory, doc_id: int, *, mode: str,
                                 pdf_bytes: bytes | None = None,
                                 accounting: dict | None = None) -> None:
    """Одна попытка обработки: (скачать / deskew) → фаза A → фаза B.

    Доменные ошибки (Transient/Permanent) НЕ гасит — пробрасывает наверх с учётом
    стоимости. Это ядро, неизменное между ступенями (обёртки завершения — разные).
    mode="deskew": коррекция ориентации от оригинала (`_run_deskew`) ДО фазы A; её
    оплаченная стоимость (detect_cost/detect_calls) прибавляется к исходу фазы A —
    как к успеху, так и к ошибке парсинга (составная попытка, §2.5, AC-S0-10).

    accounting — необязательный mutable-аккумулятор {"cost_usd", "paid_calls"},
    который process_document передаёт, чтобы НЕ-ProcessingError пути (CancelledError,
    generic Exception) тоже видели оплаченный detect (FIX 4). Просто прокидываем его
    в `_run_deskew`, которая пишет в него оплаченный detect-cost СРАЗУ после
    `deskew_pdf`, ДО собственных пост-detect S3-awaits — если CancelledError/generic
    исключение прилетит там или позже (parse_pdf/persist_parse_result), детект уже
    учтён. Для ProcessingError-пути аккумулятор НЕ используется вообще — cost уже
    слит в exc.cost_usd ниже, чтобы не задвоить.
    """
    from pdf_parser import parse_pdf  # локальный импорт против кругового; патчится через pdf_parser (F6)
    from s3 import download_file_async

    detect_cost = Decimal(0)
    detect_calls = 0

    if pdf_bytes is None:
        with session_factory() as db:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if doc is None or not doc.s3_key:
                raise PermanentError(f"Документ id={doc_id} без s3_key")
            s3_key = doc.s3_key
        if mode == "deskew":
            # accounting обновляется ВНУТРИ _run_deskew сразу после оплаченного detect,
            # ДО пост-detect S3-awaits (FIX 4) — здесь его больше не трогаем, иначе
            # стоимость задвоилась бы.
            pdf_bytes, detect_cost, detect_calls = await _run_deskew(s3_key, accounting=accounting)
        else:
            try:
                pdf_bytes = await download_file_async(s3_key)
            except Exception as exc:  # noqa: BLE001
                # Сообщение — стабильный текст без сырого exc (FIX 6); подробности в логе.
                logger.warning(f"[doc={doc_id}] не удалось скачать PDF из S3: {exc!r}")
                raise TransientError("Не удалось получить PDF из хранилища") from exc

    try:
        outcome = await parse_pdf(pdf_bytes, document_id=doc_id)
    except ProcessingError as exc:
        # Составная попытка (§2.5): прибавляем оплаченный detect к ошибке парсинга.
        # НЕ трогаем accounting здесь — этот путь читает cost из exc.cost_usd, не из
        # аккумулятора (иначе detect задвоился бы: он уже добавлен и сюда, и в accounting).
        exc.cost_usd = exc.cost_usd + detect_cost
        exc.paid_calls = exc.paid_calls + detect_calls
        raise

    outcome.cost_usd = outcome.cost_usd + detect_cost
    outcome.paid_calls = outcome.paid_calls + detect_calls
    with session_factory() as db:
        persist_parse_result(db, doc_id, outcome)


async def process_document(doc_id: int, *, mode: str, pdf_bytes: bytes | None = None,
                           session_factory=None, reraise: bool = False) -> None:
    """Обёртка ступени 0/1: выполнить попытку, любую доменную ошибку → терминальный error.

    session_factory=None → поздний резолв SessionLocal (F1: не связываем дефолт на этапе
    def — это открывало сессию на реальном dev-DATABASE_URL в тестах и плохо патчилось).

    Всегда пишет status='error' + last_error через условную запись. Если reraise=True
    И ошибка несёт http_status (только ориентация deskew: 413/502) — после записи
    пробрасывает её, чтобы эндпоинт смапил на прежний HTTP-код (AC-S0-8, поведение API
    на S0 не меняется). Ошибки парсинга (http_status=None) не пробрасываются → 200 + error.
    На S1 reraise=False (фоновой таске отвечать некому — контракт §2.2 не ломается).

    CancelledError (обрыв клиента / отмена таски) → error + 'Обработка прервана' + re-raise
    (детерминированный исход, AC-S0-2). Успех фиксируется внутри фазы B.

    accounting — mutable-аккумулятор {"cost_usd", "paid_calls"}, передаётся в
    run_processing_attempt → `_run_deskew` (mode="deskew"), которая заполняет его
    оплаченным detect-стоимостью СРАЗУ после `deskew_pdf`, ДО собственных
    пост-detect S3-awaits и уж тем более до фазы A/B (FIX 4). Если после этого
    прилетит CancelledError или неклассифицированное исключение (P1: обрыв
    страницы/клиента посреди составной попытки deskew+parse) — эти два except
    читают cost из accounting, а не пишут 0/0, иначе оплаченный detect терялся бы.
    Ветка ProcessingError аккумулятор НЕ читает — там cost уже в exc.cost_usd
    (run_processing_attempt сливает detect туда же); чтение из accounting тоже
    задвоило бы стоимость.
    """
    if session_factory is None:
        session_factory = get_processing_session_factory()
    accounting = {"cost_usd": Decimal(0), "paid_calls": 0}
    try:
        await run_processing_attempt(session_factory, doc_id, mode=mode, pdf_bytes=pdf_bytes,
                                     accounting=accounting)
    except ProcessingError as exc:
        logger.warning(f"[doc={doc_id}] обработка завершилась ошибкой: {exc.message}")
        write_processing_error(session_factory, doc_id, exc.message,
                               cost_usd=exc.cost_usd, paid_calls=exc.paid_calls)
        if reraise and exc.http_status is not None:
            raise
    except asyncio.CancelledError:
        logger.warning(f"[doc={doc_id}] обработка прервана (CancelledError)")
        write_processing_error(session_factory, doc_id, "Обработка прервана",
                               cost_usd=accounting["cost_usd"], paid_calls=accounting["paid_calls"])
        raise
    except Exception as exc:  # noqa: BLE001 — подлинно непредвиденное (не ProcessingError)
        # logger.exception уже логирует traceback с exc — сообщение пользователю
        # стабильный текст без сырого exc (FIX 6).
        logger.exception(f"[doc={doc_id}] непредвиденная ошибка обработки: {exc!r}")
        write_processing_error(session_factory, doc_id, "Внутренняя ошибка обработки",
                               cost_usd=accounting["cost_usd"], paid_calls=accounting["paid_calls"])
