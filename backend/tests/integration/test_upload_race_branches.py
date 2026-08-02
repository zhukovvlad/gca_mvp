"""Ветки IntegrityError-гонки upload: winner найден / winner is None (Q6, спека §2 шаг 4)."""
import io

import pytest
from sqlalchemy.exc import IntegrityError


def _files(sample_pdf_bytes, name="race.pdf"):
    """multipart-пейлоад для upload."""
    return {"file": (name, io.BytesIO(sample_pdf_bytes), "application/pdf")}


def test_race_winner_found_returns_duplicate(client, factories, db_session, in_memory_s3,
                                             sample_pdf_bytes, monkeypatch):
    """IntegrityError + существующий победитель → rollback, S3-сирота удалена, 200 duplicate:true."""
    import routers.invoices as inv_router

    project = factories.ProjectFactory.create()
    winner = factories.DocumentFactory.create(project=project, status="parsed", file_hash=None)
    db_session.commit()

    def boom_create(db, project_id, filename, s3_key, file_hash=None):
        """Эмулирует проигрыш гонки: победитель успел закоммититься, наш INSERT падает."""
        winner.file_hash = file_hash
        db.commit()
        raise IntegrityError("INSERT INTO documents ...", {}, Exception("uq_documents_project_file_hash"))
    monkeypatch.setattr(inv_router, "create_document", boom_create)

    s3_before = set(in_memory_s3)
    r = client.post("/api/invoices/upload", data={"project_id": project.id}, files=_files(sample_pdf_bytes))
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert r.json()["id"] == winner.id
    assert set(in_memory_s3) == s3_before  # наш объект удалён (сирот нет)


def test_race_winner_none_reraises(client, factories, in_memory_s3, sample_pdf_bytes, monkeypatch):
    """IntegrityError БЕЗ победителя (например, FK) → сирота удалена, исходная ошибка переброшена.

    Фикстура client создаёт TestClient с raise_server_exceptions=True (дефолт) —
    перевыброшенное эндпоинтом исключение долетает до теста КАК ЕСТЬ, что проверяет
    «не замаскирован под дубликат» даже строже, чем ассерт кода 5xx. Общую фикстуру
    НЕ менять (глобальный raise_server_exceptions=False изменил бы весь набор).
    """
    import routers.invoices as inv_router

    project = factories.ProjectFactory.create()

    def boom_create(db, project_id, filename, s3_key, file_hash=None):
        """Эмулирует чужой IntegrityError — победителя по (project, hash) не существует."""
        raise IntegrityError("INSERT INTO documents ...", {}, Exception("fk violation"))
    monkeypatch.setattr(inv_router, "create_document", boom_create)

    s3_before = set(in_memory_s3)
    with pytest.raises(IntegrityError):
        client.post("/api/invoices/upload", data={"project_id": project.id}, files=_files(sample_pdf_bytes))
    assert set(in_memory_s3) == s3_before  # сирота убрана ДО проброса
