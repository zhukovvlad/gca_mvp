"""Гонки тендерного контура (спека §2.11, §6): единая иерархия локов
tender → rounds → package у импорта и всех удалений.

Приём — из test_contract_cascade_delete.py и test_category_override_concurrency.py:
два потока, настоящий замок, ожидание блокировки по pg_stat_activity, а не
пауза «на глазок». Каждый тест называет снятие, которое его красит.
"""
from __future__ import annotations

import contextlib
import threading
import time

import pytest
import sqlalchemy as sa

from crud import tenders as crud_tenders
from crud.common import DomainError
from models import ImportJob, ImportJobStatus, Offer
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.payloads import position, proposal, round_payload

pytestmark = pytest.mark.integration


def _wait_until_a_backend_blocks(session_factory, *, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    with session_factory() as probe:
        while time.monotonic() < deadline:
            blocked = probe.execute(sa.text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar_one()
            probe.rollback()
            if blocked:
                return True
            time.sleep(0.05)
    return False


def P_A():
    return proposal([position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")], title="ООО А", inn="7700000001")


@pytest.fixture
def grid(committing_db, committing_factories, committing_session_factory):
    tender = committing_factories.TenderFactory.create()
    rnd = committing_factories.TenderRoundFactory.create(tender=tender, stage_no=1)
    committing_db.flush()
    import_round(committing_db, tender_round=rnd, data=round_payload([P_A()]), parser_version="4.0.0",
                 import_job_id=None, replace=False, unit_resolver=UnitResolver(committing_db),
                 category_resolver=CategoryResolver.from_db(committing_db))
    # Второй раунд — ПУСТОЙ: в нём у участника ещё нет Offer, поэтому импорт в
    # тесте дедлока делает настоящий INSERT (FK берёт KEY SHARE на пакете), а не
    # ON CONFLICT DO NOTHING, который на занятой паре не взял бы ничего.
    empty_round = committing_factories.TenderRoundFactory.create(tender=tender, stage_no=2)
    committing_db.commit()
    package_id = committing_db.execute(sa.select(Offer.package_id).where(Offer.round_id == rnd.id)).scalar_one()

    class G:
        tender_id = tender.id
        round_id = rnd.id
        empty_round_id = empty_round.id
        package = package_id
        session_factory = committing_session_factory

    return G()


def _hold_upload(session_factory, round_id, inserted, may_commit, committed):
    """Поток загрузки: вставил round-job (FOR KEY SHARE на раунде по FK), держит транзакцию."""
    session = session_factory()
    try:
        session.add(ImportJob(round_id=round_id, filename="race.xlsx", file_key="race-" + str(round_id),
                              file_sha256="1" * 64, status=ImportJobStatus.pending.value))
        session.flush()
        inserted.set()
        may_commit.wait(timeout=10)
        session.commit()
        committed.set()
    finally:
        session.close()


@pytest.mark.parametrize("command", ["round", "tender", "participant"])
def test_upload_wins_the_race_and_deletion_answers_active_import(grid, command):
    """Загрузка успела первой (job вставлен, не закоммичен) — удаление ждёт и
    отказывает `active_import`, а не сносит данные из-под живого импорта.
    Снятие любого лока раундов у команды красит тест: без него проверка
    активного job не увидит незакоммиченную вставку."""
    inserted, may_commit, committed = threading.Event(), threading.Event(), threading.Event()
    outcome: dict = {}
    uploader = threading.Thread(target=_hold_upload, args=(grid.session_factory, grid.round_id, inserted, may_commit, committed), daemon=True)
    uploader.start()
    assert inserted.wait(timeout=10)

    def deleter():
        session = grid.session_factory()
        try:
            if command == "round":
                crud_tenders.delete_round(session, grid.tender_id, grid.round_id)
            elif command == "tender":
                crud_tenders.delete_tender(session, grid.tender_id)
            else:
                crud_tenders.delete_participant(session, grid.tender_id, grid.package, confirmation_token="x")
            outcome["result"] = "deleted"
        except DomainError as e:
            outcome["result"] = e.code or str(e.status_code)
        finally:
            session.close()

    deleting = threading.Thread(target=deleter, daemon=True)
    deleting.start()
    assert _wait_until_a_backend_blocks(grid.session_factory), "удаление не встало на замок — сериализации нет"
    assert deleting.is_alive(), "удаление вернулось до коммита загрузки — замка нет"
    may_commit.set()
    assert committed.wait(timeout=10)
    deleting.join(timeout=15)
    assert outcome["result"] == "active_import"


def test_participant_deletion_blocks_a_concurrent_round_creation(grid):
    """FOR UPDATE тендера у удаления участника задерживает вставку раунда до конца
    команды (§2.11). Снятие лока тендера — тест красный: раунд вставится сразу."""
    entered, may_finish = threading.Event(), threading.Event()
    order: list[str] = []

    def deleter():
        session = grid.session_factory()
        try:
            @sa.event.listens_for(session.connection(), "after_cursor_execute")
            def pause_after_tender_lock(conn, cursor, statement, parameters, context, executemany):
                if "FOR UPDATE" in statement.upper() and "tenders" in statement and not entered.is_set():
                    entered.set()
                    may_finish.wait(timeout=10)
            preview = crud_tenders.participant_deletion_preview(session, grid.tender_id, grid.package)
            crud_tenders.delete_participant(session, grid.tender_id, grid.package, confirmation_token=preview["confirmation_token"])
            order.append("deleted")
        finally:
            session.close()

    t = threading.Thread(target=deleter, daemon=True)
    t.start()
    assert entered.wait(timeout=10)

    def creator():
        session = grid.session_factory()
        try:
            # stage_no=3: этапы 1 и 2 у фикстуры grid уже заняты (второй — пустой
            # раунд для теста дедлока); занятый номер дал бы 409 вместо гонки.
            crud_tenders.create_round(session, grid.tender_id, stage_no=3, label=None, held_on=None)
            order.append("created")
        finally:
            session.close()

    c = threading.Thread(target=creator, daemon=True)
    c.start()
    assert _wait_until_a_backend_blocks(grid.session_factory), "создание раунда не ждёт удаление участника"
    may_finish.set()
    t.join(timeout=15)
    c.join(timeout=15)
    assert order == ["deleted", "created"]


def test_import_and_participant_deletion_do_not_deadlock(grid):
    """Обе команды берут tender → rounds → package в одном порядке (§2.11), и
    доказательство ДЕТЕРМИНИРОВАННОЕ — событиями, не «повезло за три прогона».

    Импорт воспроизводится его ТОЧНОЙ последовательностью локов сырым SQL
    (tender FOR KEY SHARE → round FOR UPDATE → INSERT offers, который берёт
    FOR KEY SHARE на пакете по FK); удаление участника — настоящим
    `delete_participant`. Импорт идёт в ПУСТОЙ второй раунд: там у участника
    Offer ещё нет, и INSERT настоящий — на занятой паре `ON CONFLICT DO NOTHING`
    не вставил бы строку, FK не проверялась бы, и ключевой захват пакета не
    состоялся бы вовсе (находка ревью гейта 3). Сценарий:

    1. импорт держит tender KEY SHARE и empty_round FOR UPDATE;
    2. удаление стартует и — по иерархии — упирается в tender FOR UPDATE;
    3. импорт вставляет offer (KEY SHARE на пакете) и коммитит;
    4. удаление просыпается, локирует раунды и пакет, доходит до конца.

    Снятие защиты — переставить в `delete_participant` лок пакета ПЕРЕД
    `_lock_tender`/`_lock_rounds`: тогда на шаге 2 удаление возьмёт пакет сразу
    (событие `package_locked`), импорт на шаге 3 встанет на пакете, удаление
    на раундах — и PostgreSQL убьёт одного `DeadlockDetected`. Тест обязан
    покраснеть по `errors`, а не по таймауту.
    """
    errors: list[str] = []
    import_locked = threading.Event()
    package_locked = threading.Event()
    import_done = threading.Event()
    delete_done = threading.Event()

    def importer():
        session = grid.session_factory()
        try:
            with session.begin():
                session.execute(sa.text("SELECT id FROM tenders WHERE id = :t FOR KEY SHARE"), {"t": grid.tender_id})
                session.execute(sa.text("SELECT id FROM tender_rounds WHERE id = :r FOR UPDATE"), {"r": grid.empty_round_id})
                import_locked.set()
                # Ждём, пока удаление либо встало на замок тендера (правильная
                # иерархия), либо успело взять пакет (снятая защита).
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not package_locked.is_set():
                    if _wait_until_a_backend_blocks(grid.session_factory, timeout=0.2):
                        break
                # Настоящий INSERT, без ON CONFLICT: занятая пара обязана уронить
                # тест, а не тихо отменить захват пакета.
                inserted = session.execute(sa.text(
                    "INSERT INTO offers (tender_id, round_id, package_id) VALUES (:t, :r, :p) RETURNING id"
                ), {"t": grid.tender_id, "r": grid.empty_round_id, "p": grid.package}).scalar_one()
                assert inserted is not None
        except Exception as e:  # noqa: BLE001
            errors.append(f"import: {type(e).__name__}: {e}")
        finally:
            session.close()
            import_done.set()

    def deleter():
        session = grid.session_factory()
        try:
            @sa.event.listens_for(session.connection(), "after_cursor_execute")
            def note_package_lock(conn, cursor, statement, parameters, context, executemany):
                if "FOR UPDATE" in statement.upper() and "offer_packages" in statement:
                    package_locked.set()

            assert import_locked.wait(timeout=10)
            preview = crud_tenders.participant_deletion_preview(session, grid.tender_id, grid.package)
            # устаревший token / active_import — законные исходы; ошибка БД — нет
            with contextlib.suppress(DomainError):
                crud_tenders.delete_participant(session, grid.tender_id, grid.package,
                                                confirmation_token=preview["confirmation_token"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"delete: {type(e).__name__}: {e}")
        finally:
            session.close()
            delete_done.set()

    threads = [threading.Thread(target=importer, daemon=True), threading.Thread(target=deleter, daemon=True)]
    for th in threads:
        th.start()
    assert import_done.wait(timeout=30), "импорт не завершился — потоки зависли"
    assert delete_done.wait(timeout=30), "удаление не завершилось — потоки зависли"
    assert errors == []
    # Контроль корпуса: захват пакета состоялся — offer во втором раунде есть.
    with grid.session_factory() as check:
        assert check.execute(
            sa.select(sa.func.count()).select_from(Offer).where(Offer.round_id == grid.empty_round_id)
        ).scalar_one() == 1
