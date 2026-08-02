"""Startup-sweep S1-4: pending|processing → error на старте процесса (AC-S1-3)."""
import pytest

from models import Document


def test_sweep_marks_pending_and_processing_as_error(factories, db_session, session_factory_test):
    """Оба нетерминальных статуса переводятся в error с текстом про перезапуск."""
    from main import _sweep_stuck_documents

    doc_pending = factories.DocumentFactory.create(status="pending")
    doc_processing = factories.DocumentFactory.create(status="processing")
    doc_parsed = factories.DocumentFactory.create(status="parsed")
    doc_error = factories.DocumentFactory.create(status="error", last_error="старая причина")
    db_session.commit()

    swept = _sweep_stuck_documents(session_factory=session_factory_test)

    db_session.expire_all()
    assert swept == 2
    for doc_id in (doc_pending.id, doc_processing.id):
        saved = db_session.query(Document).filter(Document.id == doc_id).first()
        assert saved.status == "error"
        assert saved.last_error == "Обработка прервана перезапуском сервера"
    assert db_session.query(Document).filter(Document.id == doc_parsed.id).first().status == "parsed"
    err = db_session.query(Document).filter(Document.id == doc_error.id).first()
    assert err.last_error == "старая причина"  # терминальные не тронуты


def test_lifespan_invokes_sweep(monkeypatch):
    """lifespan вызывает sweep на старте (интеграция функции в жизненный цикл)."""
    from fastapi.testclient import TestClient

    import main

    calls = {"n": 0}

    def spy(session_factory=None):
        """Считает вызовы sweep вместо реального обращения к БД."""
        calls["n"] += 1
        return 0
    monkeypatch.setattr(main, "_sweep_stuck_documents", spy)

    with TestClient(main.app):
        pass
    assert calls["n"] == 1


def test_lifespan_aborts_startup_when_sweep_fails(monkeypatch):
    """Fail-fast: ошибка sweep (БД недоступна) прерывает startup, приложение не поднимается."""
    from fastapi.testclient import TestClient

    import main

    def boom(session_factory=None):
        """Эмулирует недоступность БД на старте."""
        raise RuntimeError("db down")
    monkeypatch.setattr(main, "_sweep_stuck_documents", boom)

    with pytest.raises(RuntimeError, match="db down"), TestClient(main.app):
        pass
