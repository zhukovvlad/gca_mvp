"""AC-S0-9 / FIX 7: РЕАЛЬНАЯ двух-соединительная гонка verified-СФ ↔ фаза B.

`test_phase_b_aborts_when_verified_appeared` (test_process_document.py) сеет
verified-СФ ДО запуска process_document — тест проходит даже БЕЗ `FOR UPDATE`
в persist_parse_result (verified уже видна на момент первого чтения). Здесь —
подлинная гонка на двух соединениях: T1 держит `SELECT ... FOR UPDATE` строки
документа и вставляет verified-СФ, НЕ коммитя; T2 запускает `persist_parse_result`
(фаза B), которая делает СВОЙ `FOR UPDATE`-запрос и НАСТОЯЩЕ блокируется на
row lock T1 (наблюдаем через `pg_stat_activity.wait_event_type='Lock'`, тот же
детерминированный приём, что и в test_conditional_error_write_concurrency.py).
После commit T1 (verified-СФ становится видна) T2 разблокируется, её повторная
проверка под локом видит verified-СФ → PermanentError, старый набор СФ не удалён.
"""
import threading
import time
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from pdf_parser import ParsedInvoice, ParsedItem, ParseOutcome
from processing import PermanentError, persist_parse_result


def _new_invoice_outcome() -> ParseOutcome:
    """ParseOutcome с одной новой СФ — то, что фаза B попыталась бы записать, если бы
    verified-проверка под локом её не остановила (в этом тесте не должно долететь до БД)."""
    return ParseOutcome(
        doc_type="invoice",
        cost_usd=Decimal("0.007"),
        paid_calls=1,
        invoices=[ParsedInvoice(
            number="СФ-NEW", date=date(2026, 7, 1),
            supplier_name="ООО Новый", supplier_inn="2222222222",
            vat_rate=20, confidence=0.9,
            items=[ParsedItem(
                raw_name="Бетон В30", item_type="material", material_class="В30",
                material_type="concrete", calc_role="base", quantity=3.0, unit="м3",
                unit_price=8500.0, amount=25500.0, vat_amount=4250.0,
            )],
        )],
    )


def test_phase_b_real_two_connection_verified_race(db_engine):
    """T1 держит row lock документа и вставляет verified-СФ без коммита; T2 (фаза B,
    persist_parse_result) РЕАЛЬНО блокируется на этом локе (наблюдаем Lock wait);
    после commit T1 T2 разблокируется, видит verified под своим FOR UPDATE и
    бросает PermanentError, старый набор СФ (включая новую verified) остаётся цел,
    новая СФ из outcome в БД не попадает."""
    Factory = sessionmaker(bind=db_engine)

    # --- setup: документ в processing с одной уже существующей (не verified) СФ ---
    setup = Factory()
    try:
        # Уникальный project_id вместо хардкода (FIX D) — убирает state-dependence и hazard
        # при параллельном запуске (например, под pytest-xdist на общей тестовой БД).
        project_id = setup.execute(text(
            "INSERT INTO projects (name) VALUES ('ac-s0-9-fix7') RETURNING id"
        )).scalar_one()
        doc_id = setup.execute(text(
            "INSERT INTO documents (project_id, filename, s3_key, status, doc_type, "
            "parse_count, parse_cost_usd) "
            "VALUES (:project_id, 'fix7.pdf', 'k/fix7.pdf', 'processing', 'invoice', 1, 0.001) "
            "RETURNING id"
        ), {"project_id": project_id}).scalar_one()
        setup.execute(text(
            "INSERT INTO invoices (document_id, number, date, vat_rate, verified) "
            "VALUES (:doc_id, 'СФ-OLD', '2026-01-01', 20, false)"
        ), {"doc_id": doc_id})
        setup.commit()
    finally:
        setup.close()

    t2_ready = threading.Event()      # PID backend'а T2 захвачен и опубликован
    t2_done = threading.Event()
    t2_pid: dict = {}
    t2_result: dict = {}

    def t2_worker():
        """T2 (фаза B): фиксирует backend PID на СВОЁМ соединении, затем зовёт
        persist_parse_result — её собственный FOR UPDATE упрётся в лок T1."""
        s = Factory()
        try:
            t2_pid["pid"] = s.execute(text("SELECT pg_backend_pid()")).scalar_one()
            t2_ready.set()

            persist_parse_result(s, doc_id, _new_invoice_outcome())
            t2_result["ok"] = True  # не должно случиться — ожидаем PermanentError
        except PermanentError as exc:
            t2_result["permanent_error"] = exc
        except Exception as exc:  # noqa: BLE001 — любая иная ошибка = провал теста
            t2_result["error"] = exc
        finally:
            s.close()
            t2_done.set()

    worker = threading.Thread(target=t2_worker)
    worker_started = False
    try:
        # --- T1: берём row lock документа, вставляем verified-СФ, НЕ коммитим ---
        t1 = Factory()
        try:
            t1.execute(text("SELECT id FROM documents WHERE id=:id FOR UPDATE"), {"id": doc_id}).one()
            t1.execute(text(
                "INSERT INTO invoices (document_id, number, date, vat_rate, verified) "
                "VALUES (:doc_id, 'СФ-VER', '2026-02-01', 20, true)"
            ), {"doc_id": doc_id})

            worker.start()
            worker_started = True
            assert t2_ready.wait(timeout=5), "T2 не захватил backend PID"

            # Детерминированно ждём, пока backend T2 РЕАЛЬНО встанет в ожидание
            # блокировки PostgreSQL (wait_event_type='Lock') — доказывает, что
            # persist_parse_result дошла до своего FOR UPDATE и упёрлась в лок T1,
            # а не «поток просто стартовал».
            observer = Factory()
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    row = observer.execute(text(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"
                    ), {"pid": t2_pid["pid"]}).first()
                    observer.rollback()  # не держим снапшот между опросами
                    if row and row.wait_event_type == "Lock":
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("T2-бэкенд не вышел в ожидание блокировки (Lock) за 10с")
            finally:
                observer.close()

            t1.commit()  # verified-СФ теперь видна → T2 разблокируется
        finally:
            t1.close()

        assert t2_done.wait(timeout=5)
        worker.join(timeout=5)
        assert not worker.is_alive(), "T2-поток не завершился"

        assert "permanent_error" in t2_result, (
            f"T2 не бросила PermanentError: ok={t2_result.get('ok')!r}, "
            f"error={t2_result.get('error')!r}"
        )
        assert "подтверждённые" in t2_result["permanent_error"].message

        check = Factory()
        try:
            numbers = {r.number: r.verified for r in check.execute(text(
                "SELECT number, verified FROM invoices WHERE document_id=:id"
            ), {"id": doc_id}).all()}
            # Старый набор (СФ-OLD + только что закоммиченная СФ-VER) цел, СФ-NEW
            # из outcome НЕ записана — swap не состоялся (guard сработал ДО удаления).
            assert numbers == {"СФ-OLD": False, "СФ-VER": True}
        finally:
            check.close()
    finally:
        if worker_started:
            worker.join(timeout=5)
        cleanup = Factory()
        try:
            cleanup.execute(text("DELETE FROM invoices WHERE document_id=:id"), {"id": doc_id})
            cleanup.execute(text("DELETE FROM documents WHERE id=:id"), {"id": doc_id})
            cleanup.execute(text("DELETE FROM projects WHERE id=:id"), {"id": project_id})
            cleanup.commit()
        finally:
            cleanup.close()
