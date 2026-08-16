"""Каскадное удаление договора: файлы, порядок, локи, гонка (спека §2.2-§2.4)."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from crud import contracts as crud_contracts
from models import Contract, ImportJob, ImportJobStatus

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


def test_files_are_deleted_after_commit(
    committing_client, committing_db, committing_factories, tmp_storage
):
    """Файлы заданий исчезают с диска вместе с договором (спека §2.4)."""
    contract = committing_factories.ContractFactory.create()
    keys = [tmp_storage.save(b"PK\x03\x04 first"), tmp_storage.save(b"PK\x03\x04 second")]
    for key in keys:
        committing_factories.ImportJobFactory.create(
            contract=contract, file_key=key, status=ImportJobStatus.done.value
        )
    committing_db.commit()

    assert committing_client.delete(f"/api/v1/contracts/{contract.id}").status_code == 204

    assert [tmp_storage.exists(key) for key in keys] == [False, False]


def test_commit_failure_deletes_no_files(
    committing_client, committing_db, committing_factories, committing_session_factory,
    tmp_storage, monkeypatch
):
    """Сбой КОММИТА → ни одного обращения к хранилищу; записи и файлы целы (§2.4).

    Удаление проходит по-настоящему: CRUD выполняет все DELETE, и падает именно
    `commit` сессии запроса. Иначе тест был бы вакуозным — «файлы целы, потому
    что удаление не начиналось» доказывает не порядок, а отсутствие работы.
    """
    contract = committing_factories.ContractFactory.create()
    key = tmp_storage.save(b"PK\x03\x04 payload")
    committing_factories.ImportJobFactory.create(
        contract=contract, file_key=key, status=ImportJobStatus.done.value
    )
    committing_db.commit()

    calls: list[str] = []
    monkeypatch.setattr(tmp_storage, "delete", lambda k: calls.append(k) or True)

    from database import get_db
    from main import app

    broken = committing_session_factory()

    @sa.event.listens_for(broken, "before_commit")
    def refuse_commit(session):
        raise RuntimeError("коммит не прошёл")

    def override_broken_db():
        # Именно генератор: `lambda: iter([broken])` FastAPI счёл бы ЗНАЧЕНИЕМ
        # зависимости, и роутер получил бы итератор вместо сессии.
        yield broken

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_broken_db
    try:
        with pytest.raises(RuntimeError):
            committing_client.delete(f"/api/v1/contracts/{contract.id}")
    finally:
        broken.rollback()
        broken.close()
        if previous is not None:
            app.dependency_overrides[get_db] = previous

    assert calls == []
    assert tmp_storage.exists(key)
    committing_db.rollback()  # снимаем снимок транзакции, иначе видно старое
    assert committing_db.get(Contract, contract.id) is not None
    assert (
        committing_db.execute(
            sa.select(sa.func.count()).select_from(ImportJob).where(
                ImportJob.contract_id == contract.id
            )
        ).scalar_one()
        == 1
    )


def test_one_broken_key_does_not_stop_the_rest(
    committing_client, committing_db, committing_factories, tmp_storage
):
    """Ошибка на одном файле не отменяет остальные, ответ всё равно 204 (спека §2.4)."""
    contract = committing_factories.ContractFactory.create()
    good = tmp_storage.save(b"PK\x03\x04 ok")
    # Ключ не проходит KEY_PATTERN → StorageKeyError; ровно этот случай изолирует
    # `purge_files_best_effort`.
    committing_factories.ImportJobFactory.create(
        contract=contract, file_key="сломанный ключ", status=ImportJobStatus.error.value
    )
    committing_factories.ImportJobFactory.create(
        contract=contract, file_key=good, status=ImportJobStatus.done.value
    )
    committing_db.commit()

    assert committing_client.delete(f"/api/v1/contracts/{contract.id}").status_code == 204
    assert not tmp_storage.exists(good)


def test_missing_file_is_normal(
    committing_client, committing_db, committing_factories, tmp_storage
):
    """Файла уже нет (ретенция §8) — это штатный исход, а не ошибка."""
    contract = committing_factories.ContractFactory.create()
    committing_factories.ImportJobFactory.create(
        contract=contract, file_key="0" * 32, status=ImportJobStatus.error.value
    )
    committing_db.commit()

    assert committing_client.delete(f"/api/v1/contracts/{contract.id}").status_code == 204
