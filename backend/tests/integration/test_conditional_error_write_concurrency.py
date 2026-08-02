"""AC-S0-13: КОНКУРЕНТНАЯ условная error-запись на двух реальных соединениях.

Проверяет реальную гонку, а не пост-фактум (это и отличает тест от AC-S0-12):
T1 держит строку документа под SELECT ... FOR UPDATE и НЕ коммитит swap в parsed;
T2 (в отдельном потоке) шлёт условный UPDATE ... WHERE status='processing' и
БЛОКИРУЕТСЯ на строке. После commit T1 (swap лёг) Postgres перечитывает предикат
(EvalPlanQual) на новой версии строки → он ложен → rowcount 0. Итог: parsed не
затёрт, parse_count/parse_cost_usd не задвоены (§2.3).
"""
import threading
import time
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from processing import write_processing_error


def test_conditional_write_waits_then_skips_when_swap_lands(db_engine):
    """T2-бэкенд РЕАЛЬНО ждёт row lock T1 (наблюдаем через pg_stat_activity), после коммита
    parsed видит предикат ложным → rowcount 0 (AC-S0-13, детерминированно, без тайм-эвристики)."""
    Factory = sessionmaker(bind=db_engine)

    # --- setup: документ в processing (уникальный id вне фабрик, явный cleanup) ---
    setup = Factory()
    try:
        # Уникальный project_id вместо хардкода (FIX D) — убирает state-dependence и hazard
        # при параллельном запуске (например, под pytest-xdist на общей тестовой БД).
        project_id = setup.execute(text(
            "INSERT INTO projects (name) VALUES ('ac-s0-13') RETURNING id"
        )).scalar_one()
        doc_id = setup.execute(text(
            "INSERT INTO documents (project_id, filename, s3_key, status, doc_type, "
            "parse_count, parse_cost_usd) "
            "VALUES (:project_id, 'ac13.pdf', 'k/ac13.pdf', 'processing', 'invoice', 1, 0.005) "
            "RETURNING id"
        ), {"project_id": project_id}).scalar_one()
        setup.commit()
    finally:
        setup.close()

    t2_ready = threading.Event()      # PID backend'а T2 захвачен и опубликован
    t2_done = threading.Event()
    t2_pid: dict = {}
    t2_result: dict = {}

    def t2_worker():
        """T2: фиксирует backend PID на СВОЁМ соединении, затем шлёт условный UPDATE.

        write_processing_error гоняется через фабрику, отдающую ЭТО ЖЕ соединение, чтобы
        заблокированный на row lock backend имел известный PID (видимый в pg_stat_activity).
        """
        s = Factory()
        try:
            t2_pid["pid"] = s.execute(text("SELECT pg_backend_pid()")).scalar_one()
            t2_ready.set()

            @contextmanager
            def pinned_factory():
                """Отдаёт то же соединение s — условный UPDATE идёт под известным PID."""
                yield s

            write_processing_error(pinned_factory, doc_id, "поздняя ошибка",
                                   cost_usd=Decimal("0.003"), paid_calls=1)
            t2_result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            t2_result["error"] = exc
        finally:
            s.close()
            t2_done.set()

    worker = threading.Thread(target=t2_worker)
    worker_started = False
    try:
        # --- T1: берём row lock и делаем swap в parsed, НЕ коммитим ---
        t1 = Factory()
        try:
            t1.execute(text("SELECT id FROM documents WHERE id=:id FOR UPDATE"), {"id": doc_id}).one()
            t1.execute(text("UPDATE documents SET status='parsed' WHERE id=:id"), {"id": doc_id})

            worker.start()
            worker_started = True
            assert t2_ready.wait(timeout=5), "T2 не захватил backend PID"

            # Детерминированно ждём, пока backend T2 РЕАЛЬНО встанет в ожидание блокировки
            # PostgreSQL (wait_event_type='Lock') — это доказывает, что условный UPDATE
            # отправлен и упёрся в row lock T1, а не «поток просто стартовал».
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

            t1.commit()  # swap лёг → T2 разблокируется, EvalPlanQual перечитает предикат
        finally:
            t1.close()

        assert t2_done.wait(timeout=5)
        worker.join(timeout=5)
        assert not worker.is_alive(), "T2-поток не завершился"
        assert t2_result.get("ok"), f"T2 упал вместо rowcount 0: {t2_result.get('error')!r}"

        check = Factory()
        try:
            r = check.execute(text("SELECT status, parse_count, parse_cost_usd "
                                   "FROM documents WHERE id=:id"), {"id": doc_id}).one()
            assert r.status == "parsed"            # error-запись не затёрла swap
            assert r.parse_count == 1              # стоимость не задвоена (rowcount 0)
            assert r.parse_cost_usd == Decimal("0.005")
        finally:
            check.close()
    finally:
        # join только если поток реально стартовал — иначе join() бросит RuntimeError
        # и замаскирует исходное исключение (сбой до worker.start()).
        if worker_started:
            worker.join(timeout=5)
        cleanup = Factory()
        try:
            cleanup.execute(text("DELETE FROM documents WHERE id=:id"), {"id": doc_id})
            cleanup.execute(text("DELETE FROM projects WHERE id=:id"), {"id": project_id})
            cleanup.commit()
        finally:
            cleanup.close()


def test_conditional_write_waits_then_succeeds_when_lock_holder_rolls_back(db_engine):
    """AC-S0-13, ветка 2: T1 держит row lock, НЕ коммитит swap в 'parsed' (документ
    остаётся 'processing'), затем РОЛЛБЭЧИТСЯ — освобождает блокировку без изменения
    статуса. T2 (реально дождавшийся блокировки, `wait_event_type='Lock'`, тот же
    детерминированный наблюдатель pg_stat_activity, что и в ветке 1) продолжает:
    предикат `status='processing'` после разблокировки истинен → rowcount 1 →
    status='error', last_error записан, parse_count/parse_cost_usd увеличены РОВНО
    на инкремент этой записи (не задвоены — до T2 инкрементов не было)."""
    Factory = sessionmaker(bind=db_engine)

    # --- setup: документ в processing, без предыдущей стоимости (уникальный id вне
    # фабрик, явный cleanup) ---
    setup = Factory()
    try:
        # Уникальный project_id вместо хардкода (FIX D) — убирает state-dependence и hazard
        # при параллельном запуске (например, под pytest-xdist на общей тестовой БД).
        project_id = setup.execute(text(
            "INSERT INTO projects (name) VALUES ('ac-s0-13') RETURNING id"
        )).scalar_one()
        doc_id = setup.execute(text(
            "INSERT INTO documents (project_id, filename, s3_key, status, doc_type, "
            "parse_count, parse_cost_usd) "
            "VALUES (:project_id, 'ac13b.pdf', 'k/ac13b.pdf', 'processing', 'invoice', 0, 0) "
            "RETURNING id"
        ), {"project_id": project_id}).scalar_one()
        setup.commit()
    finally:
        setup.close()

    t2_ready = threading.Event()      # PID backend'а T2 захвачен и опубликован
    t2_done = threading.Event()
    t2_pid: dict = {}
    t2_result: dict = {}

    def t2_worker():
        """T2: фиксирует backend PID на СВОЁМ соединении, затем шлёт условный UPDATE.

        write_processing_error гоняется через фабрику, отдающую ЭТО ЖЕ соединение, чтобы
        заблокированный на row lock backend имел известный PID (видимый в pg_stat_activity).
        """
        s = Factory()
        try:
            t2_pid["pid"] = s.execute(text("SELECT pg_backend_pid()")).scalar_one()
            t2_ready.set()

            @contextmanager
            def pinned_factory():
                """Отдаёт то же соединение s — условный UPDATE идёт под известным PID."""
                yield s

            write_processing_error(pinned_factory, doc_id, "поздняя ошибка (rollback-ветка)",
                                   cost_usd=Decimal("0.004"), paid_calls=1)
            t2_result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            t2_result["error"] = exc
        finally:
            s.close()
            t2_done.set()

    worker = threading.Thread(target=t2_worker)
    worker_started = False
    try:
        # --- T1: берём row lock и НЕ коммитим никакого изменения статуса ---
        t1 = Factory()
        try:
            t1.execute(text("SELECT id FROM documents WHERE id=:id FOR UPDATE"), {"id": doc_id}).one()

            worker.start()
            worker_started = True
            assert t2_ready.wait(timeout=5), "T2 не захватил backend PID"

            # Детерминированно ждём, пока backend T2 РЕАЛЬНО встанет в ожидание блокировки
            # PostgreSQL (wait_event_type='Lock') — доказывает, что условный UPDATE
            # отправлен и упёрся в row lock T1, а не «поток просто стартовал».
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

            t1.rollback()  # освобождает lock БЕЗ изменения статуса → T2 разблокируется
        finally:
            t1.close()

        assert t2_done.wait(timeout=5)
        worker.join(timeout=5)
        assert not worker.is_alive(), "T2-поток не завершился"
        assert t2_result.get("ok"), f"T2 упал вместо rowcount 1: {t2_result.get('error')!r}"

        check = Factory()
        try:
            r = check.execute(text("SELECT status, last_error, parse_count, parse_cost_usd "
                                   "FROM documents WHERE id=:id"), {"id": doc_id}).one()
            assert r.status == "error"                        # предикат был истинен → запись прошла
            assert r.last_error == "поздняя ошибка (rollback-ветка)"
            assert r.parse_count == 1                          # инкремент ровно один раз
            assert r.parse_cost_usd == Decimal("0.004")
        finally:
            check.close()
    finally:
        # join только если поток реально стартовал — иначе join() бросит RuntimeError
        # и замаскирует исходное исключение (сбой до worker.start()).
        if worker_started:
            worker.join(timeout=5)
        cleanup = Factory()
        try:
            cleanup.execute(text("DELETE FROM documents WHERE id=:id"), {"id": doc_id})
            cleanup.execute(text("DELETE FROM projects WHERE id=:id"), {"id": project_id})
            cleanup.commit()
        finally:
            cleanup.close()
