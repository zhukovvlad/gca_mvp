"""Обслуживание при старте: recovery зависших джобов (§5) и ретенция файлов (§8)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from models import ACTIVE_IMPORT_JOB_STATUSES, ImportJob, ImportJobStatus
from services.maintenance import (
    RECOVERY_ERROR_TEXT,
    purge_expired_error_job_files,
    recover_interrupted_jobs,
    run_startup_maintenance,
)
from utils import utcnow_aware

pytestmark = pytest.mark.integration


class TestRecovery:
    @pytest.mark.parametrize("status", [s.value for s in ACTIVE_IMPORT_JOB_STATUSES])
    def test_active_statuses_become_error(self, db_session, factories, status):
        job = factories.ImportJobFactory.create(status=status)
        db_session.flush()

        assert recover_interrupted_jobs(db_session) == 1

        db_session.refresh(job)
        assert job.status == ImportJobStatus.error.value
        assert job.error_text == RECOVERY_ERROR_TEXT
        assert job.finished_at is not None

    @pytest.mark.parametrize("status", ["done", "error"])
    def test_terminal_statuses_untouched(self, db_session, factories, status):
        job = factories.ImportJobFactory.create(status=status, error_text=None)
        db_session.flush()

        assert recover_interrupted_jobs(db_session) == 0

        db_session.refresh(job)
        assert job.status == status
        assert job.error_text is None

    def test_recovery_frees_the_active_pair_lock(self, db_session, factories):
        """Лок uq_import_jobs_active_pair держат только незавершённые статусы.

        Поэтому после recovery повторная загрузка той же пары разрешена — на этом
        держится инструкция «повторите загрузку» в тексте ошибки.
        """
        stuck = factories.ImportJobFactory.create(status=ImportJobStatus.matching.value)
        db_session.flush()
        recover_interrupted_jobs(db_session)

        retry = factories.ImportJobFactory.create(
            contract=stuck.contract,
            amendment_no=None,
            status=ImportJobStatus.pending.value,
        )
        db_session.flush()
        assert retry.id != stuck.id


class TestRetention:
    def _aged_error_job(self, db_session, factories, storage, *, days_ago: int):
        key = storage.save(b"xlsx-bytes")
        job = factories.ImportJobFactory.create(
            status=ImportJobStatus.error.value,
            error_text="боль",
            file_key=key,
        )
        db_session.flush()
        moment = utcnow_aware() - timedelta(days=days_ago)
        job.created_at = moment
        job.finished_at = moment
        db_session.flush()
        return job, key

    def test_old_error_job_file_is_deleted(self, db_session, factories, tmp_storage):
        job, key = self._aged_error_job(db_session, factories, tmp_storage, days_ago=31)

        assert purge_expired_error_job_files(db_session, tmp_storage, retention_days=30) == 1

        assert tmp_storage.exists(key) is False
        # Сама запись — аудит, она остаётся; факт удаления виден в warnings.
        assert db_session.get(ImportJob, job.id) is not None
        assert any("ретенции" in w for w in job.warnings)

    def test_fresh_error_job_file_is_kept(self, db_session, factories, tmp_storage):
        _job, key = self._aged_error_job(db_session, factories, tmp_storage, days_ago=5)

        assert purge_expired_error_job_files(db_session, tmp_storage, retention_days=30) == 0
        assert tmp_storage.exists(key) is True

    def test_done_job_file_is_kept_forever(self, db_session, factories, tmp_storage):
        key = tmp_storage.save(b"xlsx-bytes")
        job = factories.ImportJobFactory.create(status=ImportJobStatus.done.value, file_key=key)
        db_session.flush()
        job.created_at = job.finished_at = utcnow_aware() - timedelta(days=1000)
        db_session.flush()

        assert purge_expired_error_job_files(db_session, tmp_storage, retention_days=30) == 0
        assert tmp_storage.exists(key) is True

    def test_second_run_does_not_duplicate_the_warning(self, db_session, factories, tmp_storage):
        """«Проверка при старте» повторяется каждый запуск — warning не должен расти."""
        job, _key = self._aged_error_job(db_session, factories, tmp_storage, days_ago=31)

        purge_expired_error_job_files(db_session, tmp_storage, retention_days=30)
        assert purge_expired_error_job_files(db_session, tmp_storage, retention_days=30) == 0
        assert len(job.warnings) == 1

    def test_zero_days_disables_retention(self, db_session, factories, tmp_storage):
        _job, key = self._aged_error_job(db_session, factories, tmp_storage, days_ago=9999)

        assert purge_expired_error_job_files(db_session, tmp_storage, retention_days=0) == 0
        assert tmp_storage.exists(key) is True


class TestMaintenanceObligations:
    """Recovery обязателен, ретенция — best-effort, и они не мешают друг другу."""

    def test_retention_failure_does_not_roll_back_recovery(
        self, committing_session_factory, committing_db, committing_factories, tmp_storage
    ):
        key = tmp_storage.save(b"xlsx-bytes")
        stuck = committing_factories.ImportJobFactory.create(
            status=ImportJobStatus.parsing.value
        )
        old = committing_factories.ImportJobFactory.create(
            contract=stuck.contract,
            amendment_no=3,
            status=ImportJobStatus.error.value,
            file_key=key,
        )
        committing_db.flush()
        old.created_at = old.finished_at = utcnow_aware() - timedelta(days=99)
        committing_db.commit()
        stuck_id = stuck.id

        class BrokenStorage:
            def exists(self, _key):
                return True

            def delete(self, _key):
                raise OSError("хранилище недоступно")

        recovered, purged = run_startup_maintenance(
            committing_session_factory, BrokenStorage(), retention_days=30
        )

        assert (recovered, purged) == (1, 0)
        with committing_session_factory() as fresh:
            assert fresh.get(ImportJob, stuck_id).status == ImportJobStatus.error.value

    def test_recovery_failure_propagates(self, committing_session_factory, monkeypatch):
        """Не выполнившийся recovery обязан ронять старт, а не логироваться.

        Иначе незавершённые задания продолжают держать лок пары
        `uq_import_jobs_active_pair`, и повторная загрузка вечно отвечает 409.
        """
        monkeypatch.setattr(
            "services.maintenance.recover_interrupted_jobs",
            lambda _db: (_ for _ in ()).throw(OSError("БД недоступна")),
        )

        with pytest.raises(OSError, match="БД недоступна"):
            run_startup_maintenance(committing_session_factory, object(), retention_days=30)

    def test_app_startup_fails_when_maintenance_fails(self, monkeypatch):
        """Тот же инвариант на уровне lifespan приложения."""
        from fastapi.testclient import TestClient

        from config import settings
        from main import app

        monkeypatch.setattr(settings, "RUN_STARTUP_MAINTENANCE", True)
        monkeypatch.setattr(
            "main.run_startup_maintenance",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("БД недоступна")),
        )

        with pytest.raises(OSError, match="БД недоступна"), TestClient(app):
            pass  # pragma: no cover — до тела дело не доходит


class TestRunStartupMaintenance:
    def test_does_both_and_commits(
        self, committing_session_factory, committing_db, committing_factories, tmp_storage
    ):
        key = tmp_storage.save(b"xlsx-bytes")
        stuck = committing_factories.ImportJobFactory.create(
            status=ImportJobStatus.parsing.value
        )
        old = committing_factories.ImportJobFactory.create(
            contract=stuck.contract,
            amendment_no=7,
            status=ImportJobStatus.error.value,
            file_key=key,
        )
        committing_db.flush()
        moment = utcnow_aware() - timedelta(days=99)
        old.created_at = old.finished_at = moment
        committing_db.commit()
        stuck_id, old_id = stuck.id, old.id

        recovered, purged = run_startup_maintenance(
            committing_session_factory, tmp_storage, retention_days=30
        )
        assert (recovered, purged) == (1, 1)

        # Коммит виден из независимой сессии.
        with committing_session_factory() as fresh:
            assert fresh.get(ImportJob, stuck_id).status == ImportJobStatus.error.value
            assert fresh.get(ImportJob, old_id).warnings
        assert tmp_storage.exists(key) is False
