"""Q6: конкурентная вставка одного (project_id, file_hash) двумя реальными сессиями.

Проверяет саму СУБД-гарантию, на которой стоит гонка upload: уникальный
констрейнт uq_documents_project_file_hash допускает ровно один INSERT;
второй получает IntegrityError ПОСЛЕ ожидания блокировки (проигравший
ждёт исхода транзакции победителя). Итог: ровно один документ.
"""
import threading

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


def test_concurrent_insert_same_hash_yields_single_document(db_engine):
    """Две параллельные вставки одного (project, hash): один победитель, один IntegrityError."""
    Factory = sessionmaker(bind=db_engine)
    setup = Factory()
    try:
        project_id = setup.execute(text(
            "INSERT INTO projects (name) VALUES ('q6-race') RETURNING id")).scalar_one()
        setup.commit()
    finally:
        setup.close()

    file_hash = "a" * 64
    results: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def inserter(tag: str) -> None:
        """Вставляет документ с общим (project, hash); фиксирует исход в results."""
        s = Factory()
        try:
            barrier.wait(timeout=5)
            s.execute(text(
                "INSERT INTO documents (project_id, filename, s3_key, status, doc_type, "
                "parse_count, parse_cost_usd, file_hash) "
                "VALUES (:p, :f, :k, 'pending', 'unknown', 0, 0, :h)"),
                {"p": project_id, "f": f"{tag}.pdf", "k": f"q6/{tag}.pdf", "h": file_hash})
            s.commit()
            results[tag] = "ok"
        except IntegrityError:
            s.rollback()
            results[tag] = "integrity_error"
        finally:
            s.close()

    threads = [threading.Thread(target=inserter, args=(t,)) for t in ("t1", "t2")]
    try:
        for t in threads:
            t.start()
    finally:
        for t in threads:
            t.join(timeout=10)

    check = Factory()
    try:
        count = check.execute(text(
            "SELECT count(*) FROM documents WHERE project_id=:p AND file_hash=:h"),
            {"p": project_id, "h": file_hash}).scalar_one()
        assert count == 1
        assert sorted(results.values()) == ["integrity_error", "ok"]
    finally:
        check.execute(text("DELETE FROM documents WHERE project_id=:p"), {"p": project_id})
        check.execute(text("DELETE FROM projects WHERE id=:p"), {"p": project_id})
        check.commit()
        check.close()
