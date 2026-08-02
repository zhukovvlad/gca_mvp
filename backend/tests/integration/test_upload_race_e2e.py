"""E2E-гонка upload: два параллельных запроса одного файла (Q6, ревью плана P2).

НЕ использует общую client-фикстуру: её get_db-override отдаёт одну
транзакционную сессию на все запросы — два потока сломали бы её. Здесь
каждый запрос получает СВОЮ реальную сессию (sessionmaker(bind=db_engine)),
данные реально коммитятся, cleanup явный. Барьер внутри обёртки
create_document выравнивает оба запроса ПОСЛЕ fast-path (оба увидели
«дубликата нет») и ПЕРЕД INSERT — гонка детерминированна.

Два потока используют ДВА отдельных TestClient (не один общий): TestClient
гоняет ASGI-приложение через anyio-портал — один выделенный поток с одним
event loop на клиент. Синхронный `barrier.wait()` внутри обработчика запроса
блокирует этот единственный поток целиком, поэтому конкурентные запросы через
ОДИН TestClient сериализуются и гонка не возникает (второй запрос просто не
успевает доехать до барьера, первый ловит таймаут → BrokenBarrierError). Два
клиента — два портала/потока — настоящая конкурентность.
"""
import io
import threading

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def race_client(db_engine, in_memory_s3, monkeypatch):
    """Фабрика TestClient'ов с реальными пер-запросными сессиями и спаем enqueue —
    зеркало основной client-фикстуры (tests/conftest.py:190-247: auth-мок, CSRF
    double-submit, session-factory), но с РЕАЛЬНЫМИ сессиями вместо транзакционной.

    Возвращает (make_client, enqueued): make_client() — фабрика, а НЕ один клиент,
    потому что TestClient гоняет ASGI-приложение через один anyio-портал (один
    выделенный поток с одним event loop) — конкурентные запросы через ОДИН и тот
    же TestClient сериализуются на этом потоке и НЕ дают настоящей гонки (синхронный
    barrier.wait() внутри обработчика блокирует единственный event-loop-поток
    целиком, второй запрос до него просто не доезжает → BrokenBarrierError по
    таймауту). Два отдельных TestClient — два отдельных портала/потока — реальная
    конкурентность. enqueued копит вызовы add_task под локом, общий на все клиенты.
    """
    from unittest.mock import MagicMock

    import main
    from auth import get_current_user
    from database import get_db
    from main import app
    from processing import get_processing_session_factory

    Factory = sessionmaker(bind=db_engine)

    def real_get_db():
        """Каждому запросу — собственная реальная сессия (потокобезопасность гонки)."""
        db = Factory()
        try:
            yield db
        finally:
            db.close()

    def override_get_current_user():
        """Мок суперюзера — как в основной client-фикстуре."""
        user = MagicMock()
        user.id = 1
        user.is_superuser = True
        user.org_id = None
        user.org_role = None
        user.is_active = True
        return user

    def _sweep_noop(session_factory=None):
        """Sweep в lifespan гасится: он бил бы по реальному SessionLocal (чужая БД),
        а pending-документы этого теста должны жить (sweep не предмет теста)."""
        return 0
    monkeypatch.setattr(main, "_sweep_stuck_documents", _sweep_noop)

    enqueued: list[dict] = []
    lock = threading.Lock()

    def spy_add_task(self, func, *args, **kwargs):
        """Потокобезопасно фиксирует enqueue, не исполняя таску."""
        with lock:
            enqueued.append({"func": func, "args": args})
    monkeypatch.setattr(BackgroundTasks, "add_task", spy_add_task)

    app.dependency_overrides[get_db] = real_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    # sessionmaker поддерживает контекст-менеджер (SQLAlchemy 1.4+) — валидная фабрика
    # для process_document; спай add_task всё равно не даст таске исполниться.
    app.dependency_overrides[get_processing_session_factory] = lambda: Factory

    _csrf_token = "test-csrf-token"  # CSRF double-submit — как в основной фикстуре
    _contexts: list[TestClient] = []

    def make_client() -> TestClient:
        """Создаёт НОВЫЙ TestClient (свой anyio-портал/поток) с общими overrides/CSRF."""
        cm = TestClient(app, headers={"X-CSRF-Token": _csrf_token})
        c = cm.__enter__()
        c.cookies.set("csrf_token", _csrf_token)
        _contexts.append(cm)
        return c

    try:
        yield make_client, enqueued
    finally:
        for cm in _contexts:
            cm.__exit__(None, None, None)
        app.dependency_overrides.clear()


def test_two_parallel_uploads_same_file(race_client, db_engine, in_memory_s3, sample_pdf_bytes, monkeypatch):
    """Гонка: 1 документ, 1 enqueue, 1 живой S3-объект, у проигравшего 200 duplicate:true."""
    make_client, enqueued = race_client
    # Два ОТДЕЛЬНЫХ TestClient (два anyio-портала/потока) — иначе один портал
    # сериализует оба запроса на своём единственном event-loop-потоке и гонка
    # никогда не случится (см. докстринг фикстуры race_client).
    client_a, client_b = make_client(), make_client()
    Factory = sessionmaker(bind=db_engine)

    setup = Factory()
    try:
        project_id = setup.execute(text(
            "INSERT INTO projects (name) VALUES ('q6-race-e2e') RETURNING id")).scalar_one()
        setup.commit()
    finally:
        setup.close()

    import routers.invoices as inv_router
    real_create = inv_router.create_document
    barrier = threading.Barrier(2, timeout=10)

    def synced_create(db, project_id, filename, s3_key, file_hash=None):
        """Выравнивает оба запроса после fast-path и перед INSERT — гонка гарантирована."""
        barrier.wait()
        return real_create(db, project_id, filename, s3_key, file_hash=file_hash)
    monkeypatch.setattr(inv_router, "create_document", synced_create)

    results: dict[str, dict] = {}

    def do_upload(tag: str, client: TestClient) -> None:
        """Один участник гонки: POST /upload на СВОЁМ TestClient и фиксация исхода."""
        files = {"file": (f"{tag}.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
        r = client.post("/api/invoices/upload", data={"project_id": project_id}, files=files)
        results[tag] = {"code": r.status_code, "body": r.json()}

    threads = [
        threading.Thread(target=do_upload, args=(t, c))
        for t, c in (("t1", client_a), ("t2", client_b))
    ]
    try:
        for t in threads:
            t.start()
    finally:
        for t in threads:
            t.join(timeout=30)

    check = Factory()
    try:
        codes = sorted(res["code"] for res in results.values())
        assert codes == [200, 202]                                   # победитель 202, проигравший 200
        loser = next(res for res in results.values() if res["code"] == 200)
        winner = next(res for res in results.values() if res["code"] == 202)
        assert loser["body"]["duplicate"] is True
        assert winner["body"]["duplicate"] is False
        assert loser["body"]["id"] == winner["body"]["id"]           # один и тот же документ
        count = check.execute(text(
            "SELECT count(*) FROM documents WHERE project_id=:p"), {"p": project_id}).scalar_one()
        assert count == 1                                            # ровно один документ
        assert len(enqueued) == 1                                    # ровно один enqueue
        s3_keys = [k for k in in_memory_s3 if not k.endswith(".orig")]
        assert len(s3_keys) == 1                                     # сирота проигравшего удалена
    finally:
        check.execute(text("DELETE FROM documents WHERE project_id=:p"), {"p": project_id})
        check.execute(text("DELETE FROM projects WHERE id=:p"), {"p": project_id})
        check.commit()
        check.close()
