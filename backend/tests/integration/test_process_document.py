"""Тесты ядра обработки: process_document, error-пути, CancelledError (S0-3, S0-4, §2.3)."""
import asyncio
from decimal import Decimal

import pytest

from models import Document, Invoice
from processing import get_processing_session_factory, process_document, write_processing_error


def _proc_doc(factories, db_session, s3, s3_key="k/p.pdf"):
    """Документ в статусе processing с байтами в in-memory S3 — типовая заготовка."""
    doc = factories.DocumentFactory.create(s3_key=s3_key, status="processing")
    s3[s3_key] = b"%PDF"
    db_session.commit()
    return doc


@pytest.mark.asyncio
async def test_process_document_success_sets_parsed(
    factories, db_session, in_memory_s3, mock_openrouter, session_factory_test,
):
    """Успешный reparse ставит parsed и создаёт СФ."""
    doc = _proc_doc(factories, db_session, in_memory_s3)
    await process_document(doc.id, mode="parse", session_factory=session_factory_test)
    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "parsed"


def test_get_processing_session_factory_returns_patched_session_local(monkeypatch):
    """Поздний импорт database.SessionLocal внутри функции подхватывает monkeypatch (F1)."""
    import database

    sentinel = object()
    monkeypatch.setattr(database, "SessionLocal", sentinel)
    assert get_processing_session_factory() is sentinel


@pytest.mark.asyncio
async def test_process_document_none_session_factory_resolves_via_database(
    factories, db_session, in_memory_s3, mock_openrouter, monkeypatch, session_factory_test,
):
    """session_factory=None → поздний резолв через get_processing_session_factory доходит до parsed (F1 e2e)."""
    import database

    monkeypatch.setattr(database, "SessionLocal", session_factory_test)
    doc = _proc_doc(factories, db_session, in_memory_s3)

    await process_document(doc.id, mode="parse")

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "parsed"


@pytest.mark.asyncio
async def test_process_document_permanent_error_keeps_old_invoices(
    factories, db_session, in_memory_s3, mock_openrouter, session_factory_test,
):
    """Провал сверки итогов → error, старые СФ невредимы, last_error заполнен (AC-S0-1)."""
    mock_openrouter.use_scenario("incomplete_totals")
    doc = _proc_doc(factories, db_session, in_memory_s3)
    factories.InvoiceFactory.create(document=doc, number="СФ-OLD")
    db_session.commit()

    await process_document(doc.id, mode="parse", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.last_error
    assert saved.parse_count == 1  # платный вызов учтён
    assert [i.number for i in db_session.query(Invoice).filter(Invoice.document_id == doc.id)] == ["СФ-OLD"]


@pytest.mark.asyncio
async def test_process_document_second_invoice_bad_date_keeps_old_invoices(
    factories, db_session, in_memory_s3, mock_openrouter, session_factory_test,
):
    """FIX 1 e2e: модель вернула ДВЕ СФ, вторая с некорректной датой → PermanentError
    роняет ВЕСЬ разбор (all-or-nothing), документ уходит в error, а СТАРЫЙ набор СФ
    остаётся нетронутым (никакого partial swap первой валидной СФ поверх старых)."""
    mock_openrouter.use_scenario("two_invoices_second_bad_date")
    doc = _proc_doc(factories, db_session, in_memory_s3)
    factories.InvoiceFactory.create(document=doc, number="СФ-OLD")
    db_session.commit()

    await process_document(doc.id, mode="parse", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.last_error
    assert saved.parse_count == 1  # платный вызов учтён
    numbers = [i.number for i in db_session.query(Invoice).filter(Invoice.document_id == doc.id)]
    assert numbers == ["СФ-OLD"]  # старый набор цел, СФ-101 (валидная) НЕ протекла в БД


@pytest.mark.asyncio
async def test_process_document_phase_b_failure_writes_error_with_cost(
    factories, db_session, in_memory_s3, mock_openrouter, monkeypatch, session_factory_test,
):
    """Сбой фазы B ПОСЛЕ оплаченной фазы A (через process_document) → status=error,
    last_error заполнен, стоимость фазы A начислена ровно один раз, старые СФ целы (AC-S0-11 e2e, F5)."""
    import processing

    doc = _proc_doc(factories, db_session, in_memory_s3)
    factories.InvoiceFactory.create(document=doc, number="СФ-OLD")
    db_session.commit()

    def boom(*a, **k):
        """Ломает вставку позиции внутри фазы B (детерминированный сбой БД)."""
        raise RuntimeError("db exploded mid-phase-B")
    monkeypatch.setattr(processing, "normalize_item", boom)

    await process_document(doc.id, mode="parse", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.last_error
    # Санитизация (FIX A): raw exc (SQL/params) не должен попасть в last_error через API.
    assert "db exploded" not in saved.last_error
    assert "SQL" not in saved.last_error
    assert saved.parse_count == 1                       # detect не было; фаза A оплачена ровно раз
    assert saved.parse_cost_usd > Decimal(0)            # стоимость фазы A начислена
    numbers = [i.number for i in db_session.query(Invoice).filter(Invoice.document_id == doc.id)]
    assert numbers == ["СФ-OLD"]                         # swap откатан, старые СФ целы


@pytest.mark.asyncio
async def test_phase_b_aborts_when_verified_appeared(
    factories, db_session, in_memory_s3, mock_openrouter, session_factory_test,
):
    """Появившаяся verified-СФ к моменту фазы B → error, старый набор не тронут (AC-S0-9, S0-8).

    Имитирует гонку: verified-СФ появляется, пока идёт длительный LLM-вызов фазы A —
    эндпоинт-проверка на входе (reparse/deskew) её не видела. Повторная проверка под
    FOR UPDATE строки документа в persist_parse_result обязана поймать её ДО удаления
    старых СФ.
    """
    doc = _proc_doc(factories, db_session, in_memory_s3)
    factories.InvoiceFactory.create(document=doc, number="СФ-VER", verified=True)
    db_session.commit()

    await process_document(doc.id, mode="parse", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    numbers = [i.number for i in db_session.query(Invoice).filter(Invoice.document_id == doc.id)]
    assert "СФ-VER" in numbers  # verified-СФ не удалена


@pytest.mark.asyncio
async def test_process_document_cancelled_sets_error(
    factories, db_session, in_memory_s3, monkeypatch, session_factory_test,
):
    """CancelledError посреди парсинга → error + last_error='Обработка прервана', re-raise (AC-S0-2)."""
    import pdf_parser  # run_processing_attempt берёт parse_pdf локально из pdf_parser (F6)

    doc = _proc_doc(factories, db_session, in_memory_s3)

    async def boom(*a, **k):
        """Эмулирует отмену таски внутри фазы A."""
        raise asyncio.CancelledError()
    monkeypatch.setattr(pdf_parser, "parse_pdf", boom)

    with pytest.raises(asyncio.CancelledError):
        await process_document(doc.id, mode="parse", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.last_error == "Обработка прервана"


@pytest.mark.asyncio
async def test_deskew_cancelled_after_paid_detect_keeps_detect_cost(
    factories, db_session, in_memory_s3, monkeypatch, session_factory_test,
):
    """mode="deskew": detect уже оплачен (`_run_deskew` вернул detect_cost/detect_calls),
    затем CancelledError прилетает из фазы A/персистенции — этот путь идёт НЕ через
    ProcessingError, а через `except asyncio.CancelledError` в process_document, который
    раньше писал cost_usd=0/paid_calls=0 и тем самым терял оплаченный detect (P1: обрыв
    страницы посреди composite-попытки). Ожидаем, что оплаченный detect дойдёт до
    error-записи через accounting-аккумулятор (FIX 3)."""
    import pdf_orientation
    import pdf_parser

    doc = _proc_doc(factories, db_session, in_memory_s3)

    async def fake_deskew(pdf_bytes):
        """detect оплачен (cost=0.002), поворотов нет — ротация не требуется."""
        return pdf_bytes, [0], Decimal("0.002")
    monkeypatch.setattr(pdf_orientation, "deskew_pdf", fake_deskew)

    async def boom_parse(*a, **k):
        """Эмулирует отмену таски ПОСЛЕ того, как detect уже оплачен."""
        raise asyncio.CancelledError()
    monkeypatch.setattr(pdf_parser, "parse_pdf", boom_parse)

    with pytest.raises(asyncio.CancelledError):
        await process_document(doc.id, mode="deskew", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.parse_count == 1
    assert saved.parse_cost_usd == Decimal("0.002")


def test_write_error_conditional_skips_when_already_parsed(
    factories, db_session, session_factory_test,
):
    """Условная error-запись при уже parsed → rowcount 0, статус не затёрт (AC-S0-12)."""
    doc = factories.DocumentFactory.create(status="parsed")
    db_session.commit()

    write_processing_error(session_factory_test, doc.id, "боль", cost_usd=Decimal("0.001"), paid_calls=1)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "parsed"           # не затёрт
    assert saved.parse_count == 0             # стоимость не начислена повторно


def test_write_error_reraises_non_connection_operational_error():
    """Не-connection OperationalError (deadlock) → одна попытка, проброс, без глотания (F8)."""
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    from sqlalchemy.exc import OperationalError

    attempts = {"n": 0}

    @contextmanager
    def factory():
        """Фейковая фабрика: execute всегда бросает детерминированный OperationalError."""
        db = MagicMock()

        def _execute(*a, **k):
            """Эмулирует deadlock — OperationalError без connection_invalidated/SQLSTATE 08."""
            attempts["n"] += 1
            raise OperationalError("UPDATE documents ...", {}, Exception("deadlock detected"))

        db.execute.side_effect = _execute
        yield db

    slept: list[float] = []
    with pytest.raises(OperationalError):
        write_processing_error(factory, 1, "x", cost_usd=Decimal("0.001"), paid_calls=1,
                               sleep=slept.append)
    assert attempts["n"] == 1  # детерминированная ошибка — без ретраев
    assert slept == []          # и без паузы — не спим на непробрасываемом сбое


@pytest.mark.asyncio
async def test_deskew_sums_detect_and_parse_cost(
    factories, db_session, in_memory_s3, mock_openrouter, monkeypatch, session_factory_test,
):
    """deskew: parse_cost = detect + parse, parse_count += 2 (AC-S0-10, S0-9)."""
    import pdf_orientation

    doc = _proc_doc(factories, db_session, in_memory_s3)

    async def fake_deskew(pdf_bytes):
        """Возвращает (bytes, rotations, detect_cost) без реального vision-вызова."""
        return pdf_bytes, [0], Decimal("0.001")
    monkeypatch.setattr(pdf_orientation, "deskew_pdf", fake_deskew)

    await process_document(doc.id, mode="deskew", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "parsed"
    assert saved.parse_count == 2                       # detect + parse
    assert saved.parse_cost_usd > Decimal("0.001")      # detect + parse cost


@pytest.mark.asyncio
async def test_deskew_cancelled_during_post_detect_s3_upload_keeps_detect_cost(
    factories, db_session, in_memory_s3, monkeypatch, session_factory_test,
):
    """FIX 4: detect оплачен (rotations=[270] → есть повороты → идут пост-detect
    S3-awaits внутри `_run_deskew`), и CancelledError прилетает ИМЕННО из
    `upload_file_async` — ДО того, как `_run_deskew` успевает вернуться в
    `run_processing_attempt`. CancelledError не Exception — раньше accounting
    обновлялся только в вызывающем ПОСЛЕ возврата из `_run_deskew`, и оплаченный
    detect-cost терялся бы. Теперь `_run_deskew` пишет в accounting СРАЗУ после
    `deskew_pdf`, ДО пост-detect S3-вызовов — detect-cost обязан дойти до
    error-записи через accounting."""
    import pdf_orientation
    import s3

    doc = _proc_doc(factories, db_session, in_memory_s3)

    async def fake_deskew(pdf_bytes):
        """Платный detect с ненулевым поворотом — требует пост-detect S3 upload."""
        return b"%PDF-corrected", [270], Decimal("0.002")
    monkeypatch.setattr(pdf_orientation, "deskew_pdf", fake_deskew)

    async def boom_upload(file_bytes, object_name):
        """Эмулирует отмену таски ВНУТРИ пост-detect S3-загрузки (обрыв клиента)."""
        raise asyncio.CancelledError()
    monkeypatch.setattr(s3, "upload_file_async", boom_upload)

    with pytest.raises(asyncio.CancelledError):
        await process_document(doc.id, mode="deskew", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.parse_count == 1                       # оплаченный detect учтён
    assert saved.parse_cost_usd == Decimal("0.002")     # detect cost не потерян при cancel


@pytest.mark.asyncio
async def test_deskew_carries_detect_cost_when_s3_write_fails(
    factories, db_session, in_memory_s3, mock_openrouter, monkeypatch, session_factory_test,
):
    """detect оплачен, но перезапись S3 после detect падает → error, detect cost учтён,
    parse_count += 1 (только detect, фаза A не достигнута) (F3)."""
    import pdf_orientation
    import s3

    doc = _proc_doc(factories, db_session, in_memory_s3)

    async def fake_deskew(pdf_bytes):
        """detect «нашёл» поворот → потребуется перезапись S3."""
        return b"%PDF-corrected", [270], Decimal("0.002")
    monkeypatch.setattr(pdf_orientation, "deskew_pdf", fake_deskew)

    async def boom_upload(file_bytes, object_name):
        """Эмулирует сбой S3-записи ПОСЛЕ оплаченного detect."""
        raise RuntimeError("S3 write failed")
    monkeypatch.setattr(s3, "upload_file_async", boom_upload)

    await process_document(doc.id, mode="deskew", session_factory=session_factory_test)

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.parse_count == 1                       # оплаченный detect учтён
    assert saved.parse_cost_usd == Decimal("0.002")     # detect cost не потерян


@pytest.mark.asyncio
async def test_deskew_post_detect_s3_failure_raises_502(
    factories, db_session, in_memory_s3, mock_openrouter, monkeypatch, session_factory_test,
):
    """FIX 5: сбой S3 ПОСЛЕ оплаченного detect (перезапись s3_key исправленными байтами)
    обязан нести http_status=502 — тот же HTTP-контракт, что и пред-detect S3-сбои
    (иначе process_document(reraise=True) не пробросит её, и deskew-эндпоинт молча
    вернёт 200 при доке в error вместо 502)."""
    import pdf_orientation
    import s3
    from processing import TransientError

    doc = _proc_doc(factories, db_session, in_memory_s3)

    async def fake_deskew(pdf_bytes):
        """detect «нашёл» поворот → потребуется перезапись S3, которая ниже упадёт."""
        return b"%PDF-corrected", [270], Decimal("0.002")
    monkeypatch.setattr(pdf_orientation, "deskew_pdf", fake_deskew)

    async def boom_upload(file_bytes, object_name):
        """Эмулирует сбой S3-записи ПОСЛЕ оплаченного detect."""
        raise RuntimeError("S3 write failed")
    monkeypatch.setattr(s3, "upload_file_async", boom_upload)

    with pytest.raises(TransientError) as exc_info:
        await process_document(doc.id, mode="deskew", reraise=True, session_factory=session_factory_test)
    assert exc_info.value.http_status == 502

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.parse_count == 1                       # оплаченный detect учтён
    assert saved.parse_cost_usd == Decimal("0.002")


@pytest.mark.asyncio
async def test_deskew_fallback_download_transient_failure_raises_502(
    factories, db_session, in_memory_s3, monkeypatch, session_factory_test,
):
    """`.orig` не найден → fallback на s3_key; сам fallback-download падает транзиентно
    (не FileNotFoundError) → классифицированный TransientError(502), а не голое исключение
    (симметрия с сестринской .orig-веткой; detect не оплачен → cost=0) (FIX 4)."""
    import s3
    from processing import TransientError

    doc = _proc_doc(factories, db_session, in_memory_s3)

    def boom_download(object_name):
        """`.orig` — «не найдено» (ожидаемо, has_backup=False); сам s3_key (fallback) —
        транзиентный сбой S3, а не отсутствие объекта."""
        if object_name.endswith(".orig"):
            raise FileNotFoundError(object_name)
        raise RuntimeError("S3 read failed")
    monkeypatch.setattr(s3, "download_file", boom_download)

    with pytest.raises(TransientError) as exc_info:
        await process_document(doc.id, mode="deskew", reraise=True, session_factory=session_factory_test)
    assert exc_info.value.http_status == 502

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.parse_count == 0
    assert saved.parse_cost_usd == Decimal(0)


@pytest.mark.asyncio
async def test_process_document_reraises_deskew_error_with_http_status(
    factories, db_session, in_memory_s3, monkeypatch, session_factory_test,
):
    """T6 deferred coverage: reraise=True + ошибка с http_status (deskew 502) → error записан
    И исключение пробрасывается наружу process_document (AC-S0-8). До этой задачи ни одна
    доменная ошибка не несла http_status, ветка `if reraise and exc.http_status is not None`
    была недостижима в тестах."""
    import pdf_orientation
    from processing import TransientError

    doc = _proc_doc(factories, db_session, in_memory_s3)

    async def boom_deskew(pdf_bytes):
        """Эмулирует транспортный сбой detect — TransientError с http_status=502, без cost."""
        raise TransientError("Сервис распознавания ориентации недоступен", http_status=502)
    monkeypatch.setattr(pdf_orientation, "deskew_pdf", boom_deskew)

    with pytest.raises(TransientError) as exc_info:
        await process_document(doc.id, mode="deskew", reraise=True, session_factory=session_factory_test)
    assert exc_info.value.http_status == 502

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "error"
    assert saved.parse_count == 0            # detect не был оплачен (сбой до чтения cost)
    assert saved.parse_cost_usd == Decimal(0)


@pytest.mark.asyncio
async def test_deskew_has_backup_zero_rotations_parses_current_key(
    factories, db_session, in_memory_s3, mock_openrouter, monkeypatch, session_factory_test,
):
    """has_backup=True + нулевые повороты: детект флейкнул в нули, но .orig уже существует
    (прошлый deskew уже исправил s3_key) → должны парсить ТЕКУЩИЙ s3_key (исправленную
    версию), а не повёрнутый .orig. .orig-бэкап при этом не трогаем (review Task 7)."""
    import pdf_orientation
    import pdf_parser

    doc = _proc_doc(factories, db_session, in_memory_s3)
    orig_bytes = b"%PDF-ORIGINAL"
    current_bytes = b"%PDF-CORRECTED"
    in_memory_s3[f"{doc.s3_key}.orig"] = orig_bytes
    in_memory_s3[doc.s3_key] = current_bytes

    async def fake_deskew(pdf_bytes):
        """Детект флейкнул в нули поверх текущего (уже исправленного) s3_key."""
        return pdf_bytes, [0], Decimal("0.001")
    monkeypatch.setattr(pdf_orientation, "deskew_pdf", fake_deskew)

    parsed_bytes: list[bytes] = []
    real_parse_pdf = pdf_parser.parse_pdf

    async def recording_parse_pdf(file_data, *, document_id):
        """Фиксирует байты, реально переданные в фазу A, и делегирует настоящей parse_pdf."""
        parsed_bytes.append(file_data)
        return await real_parse_pdf(file_data, document_id=document_id)
    monkeypatch.setattr(pdf_parser, "parse_pdf", recording_parse_pdf)

    await process_document(doc.id, mode="deskew", session_factory=session_factory_test)

    assert in_memory_s3[f"{doc.s3_key}.orig"] == orig_bytes    # бэкап не тронут
    assert parsed_bytes == [current_bytes]                     # парсили текущий, не .orig

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "parsed"


@pytest.mark.asyncio
async def test_deskew_has_backup_nonzero_rotations_keeps_original_backup(
    factories, db_session, in_memory_s3, mock_openrouter, monkeypatch, session_factory_test,
):
    """has_backup=True + ненулевые повороты: бэкап .orig одноразовый — при повторном
    deskew с уже существующим .orig НЕ перезаписываем его, но текущий s3_key
    перезаписываем свежескорректированными байтами (review Task 7)."""
    import pdf_orientation

    doc = _proc_doc(factories, db_session, in_memory_s3)
    orig_bytes = b"%PDF-ORIGINAL"
    in_memory_s3[f"{doc.s3_key}.orig"] = orig_bytes
    in_memory_s3[doc.s3_key] = b"%PDF-STALE-CURRENT"

    async def fake_deskew(pdf_bytes):
        """Детект нашёл ненулевой поворот на повторном прогоне."""
        return b"%PDF-NEWCORRECTED", [270], Decimal("0.001")
    monkeypatch.setattr(pdf_orientation, "deskew_pdf", fake_deskew)

    await process_document(doc.id, mode="deskew", session_factory=session_factory_test)

    assert in_memory_s3[f"{doc.s3_key}.orig"] == orig_bytes            # бэкап не перезаписан
    assert in_memory_s3[doc.s3_key] == b"%PDF-NEWCORRECTED"            # текущий перезаписан

    db_session.expire_all()
    saved = db_session.query(Document).filter(Document.id == doc.id).first()
    assert saved.status == "parsed"


def test_write_error_retries_on_connection_loss():
    """connection_invalidated=True → ретраит до retries, затем critical-лог без проброса (F8)."""
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    from sqlalchemy.exc import OperationalError

    attempts = {"n": 0}

    @contextmanager
    def factory():
        """Фейковая фабрика: execute бросает connection-invalidated ошибку."""
        db = MagicMock()

        def _execute(*a, **k):
            """Эмулирует обрыв соединения из commit — ретраебельно."""
            attempts["n"] += 1
            err = OperationalError("UPDATE documents ...", {}, Exception("server closed connection"))
            err.connection_invalidated = True
            raise err

        db.execute.side_effect = _execute
        yield db

    # Все попытки — потеря соединения → исчерпание ретраев → critical-лог, исключение НЕ пробрасывается.
    slept: list[float] = []
    write_processing_error(factory, 1, "x", cost_usd=Decimal("0.001"), paid_calls=1, retries=3,
                           sleep=slept.append)
    assert attempts["n"] == 3
    # Пауза между попытками (спека line 156): после 1-й и 2-й connection error — спим,
    # после ПОСЛЕДНЕЙ (3-й) не спим. Backoff 0.1 → 0.25.
    assert slept == [0.1, 0.25]
