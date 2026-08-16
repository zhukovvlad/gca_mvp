"""Каскадное удаление договора: файлы, порядок, локи, гонка (спека §2.2-§2.4)."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from crud import contracts as crud_contracts
from models import ImportJob, ImportJobStatus

pytestmark = pytest.mark.integration


def test_delete_holds_the_contract_row_lock(
    committing_db, committing_factories, committing_session_factory
):
    """Пока `delete_contract` работает, вставить задание импорта нельзя (§2.2).

    Проба вставки запускается ИЗНУТРИ удаления — обработчиком, который срабатывает
    сразу после того, как production-код выполнил свой `SELECT … FOR UPDATE`.
    Поэтому тест привязан к самому коду двумя независимыми способами: снятие
    `.with_for_update()` не даст обработчику найти свой запрос (проба не
    выполнится, и `probe` останется пустым), а если она всё же выполнится — то
    без замка вставка пройдёт. Обе поломки красят тест.
    """
    contract = committing_factories.ContractFactory.create()
    # Фабрики настроены на `sqlalchemy_session_persistence = "flush"` — без
    # явного коммита другие сессии договора не увидят.
    committing_db.commit()
    contract_id = contract.id

    session = committing_session_factory()
    connection = session.connection()
    probe: dict[str, bool] = {}

    @sa.event.listens_for(connection, "after_cursor_execute")
    def run_probe(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" not in statement.upper() or probe:
            return
        other = committing_session_factory()
        try:
            other.execute(sa.text("SET lock_timeout = '400ms'"))
            other.add(
                ImportJob(
                    contract_id=contract_id,
                    amendment_no=None,
                    filename="e.xlsx",
                    file_key="0" * 32,
                    file_sha256="0" * 64,
                    status=ImportJobStatus.pending.value,
                )
            )
            other.commit()
            probe["blocked"] = False
        except Exception as exc:  # noqa: BLE001 — интересует сам факт отказа
            probe["blocked"] = "lock" in str(exc).lower()
            other.rollback()
        finally:
            other.close()

    try:
        crud_contracts.delete_contract(session, contract_id)
    finally:
        sa.event.remove(connection, "after_cursor_execute", run_probe)
        session.close()

    assert probe.get("blocked") is True, (
        "вставка задания прошла, пока шло удаление, — строка договора не заблокирована"
    )
