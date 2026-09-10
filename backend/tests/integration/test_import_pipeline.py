"""Оркестратор задания импорта: две сессии, атомарность, recovery (AGENTS.md §5)."""
from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa
from freezegun import freeze_time

from models import CatalogKind, CatalogPosition, Estimate, ImportJob, ImportJobStatus, PositionItem
from parser import EstimateParseError, ParseResult
from parser.sanitize_text import NormalizationUnavailableError, normalize_job_title_with_lemmatization
from services import import_pipeline
from services.maintenance import recover_interrupted_jobs
from tests.payloads import payload_for, position

pytestmark = pytest.mark.integration


def fake_parse(payload, *, warnings=(), version="1.0.0"):
    """Подменяет парсер: тесты пайплайна не должны стоить 20 с лемматизации."""

    def _parse(handle):
        handle.read()  # файл действительно читается из хранилища
        return ParseResult(data=payload, parser_version=version, warnings=list(warnings))

    return _parse


@pytest.fixture
def job_env(committing_db, committing_factories, tmp_storage, committing_session_factory):
    """Договор + задание с реальным файлом в хранилище, всё закоммичено."""

    class Env:
        db = committing_db
        factories = committing_factories
        storage = tmp_storage
        session_factory = committing_session_factory

        def __init__(self):
            self.contract = committing_factories.ContractFactory.create()
            committing_db.flush()
            self.job = self.new_job()
            committing_db.commit()

        def new_job(self, *, amendment_no=None, contract=None):
            key = tmp_storage.save(b"PK\x03\x04not-a-real-xlsx")
            job = committing_factories.ImportJobFactory.create(
                contract=contract or self.contract,
                amendment_no=amendment_no,
                file_key=key,
                status=ImportJobStatus.pending.value,
            )
            committing_db.flush()
            return job

        def reload(self, job):
            committing_db.expire_all()
            return committing_db.get(ImportJob, job.id)

        def run(self, payload, *, job=None, **kwargs):
            kwargs.setdefault("parse", fake_parse(payload))
            import_pipeline.run_import_job(
                (job or self.job).id,
                session_factory=committing_session_factory,
                storage=tmp_storage,
                **kwargs,
            )
            return self.reload(job or self.job)

        def estimates(self):
            committing_db.expire_all()
            return committing_db.execute(sa.select(Estimate)).scalars().all()

    return Env()


# ---------------------------------------------------------------------------
#  Счастливый путь
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_pipeline_reaches_done_with_counters(self, job_env):
        payload = payload_for(
            job_env.contract,
            [
                position(job_title="Раздел 1", is_chapter=True, chapter_number="1"),
                position(job_title="Устройство стяжки", unit="м2", unit_cost_total="100", number="2"),
                position(job_title="Монтаж кабеля", unit="м", unit_cost_total="50", number="3"),
            ],
        )

        job = job_env.run(payload)

        assert job.status == ImportJobStatus.done.value
        assert job.error_text is None
        assert job.finished_at is not None
        assert job.started_at is not None
        assert (job.positions_total, job.to_review) == (2, 2)
        assert job.matched_cache == job.matched_exact == job.matched_nonposition == 0

        estimate = job_env.estimates()[0]
        assert estimate.import_job_id == job.id
        assert estimate.raw_data.parser_version == "1.0.0"

    def test_parser_warnings_land_in_the_job(self, job_env):
        payload = payload_for(job_env.contract)

        job = job_env.run(payload, parse=fake_parse(payload, warnings=["раскладка поехала"]))

        assert "раскладка поехала" in job.warnings

    def test_domain_warnings_land_in_the_job(self, job_env):
        """Warnings домена пишет сессия B — вместе с данными, которые они описывают."""
        payload = payload_for(job_env.contract, tender_object="Совсем другой объект")

        job = job_env.run(payload)

        assert any("Объект в файле" in w for w in job.warnings)

    def test_parser_and_domain_warnings_coexist(self, job_env):
        payload = payload_for(job_env.contract, tender_object="Другой объект")

        job = job_env.run(payload, parse=fake_parse(payload, warnings=["из парсера"]))

        assert "из парсера" in job.warnings
        assert any("Объект в файле" in w for w in job.warnings)

    def test_status_is_parsing_while_the_parser_works(self, job_env):
        """Промежуточные статусы коммитятся сразу — фронт видит прогресс (§5)."""
        seen: list[str] = []
        payload = payload_for(job_env.contract)

        def spying_parse(handle):
            with job_env.session_factory() as independent:
                seen.append(
                    independent.execute(
                        sa.select(ImportJob.status).where(ImportJob.id == job_env.job.id)
                    ).scalar_one()
                )
            return fake_parse(payload)(handle)

        job_env.run(payload, parse=spying_parse)

        assert seen == [ImportJobStatus.parsing.value]

    def test_second_import_of_another_amendment_uses_the_cache(self, job_env):
        rows = [position(job_title="Устройство стяжки", unit="м2", unit_cost_total="100")]
        job_env.run(payload_for(job_env.contract, rows))
        second_job = job_env.new_job(amendment_no=1)
        job_env.db.commit()

        # После первой загрузки работа лежит в каталоге как TO_REVIEW, поэтому
        # вторая снова попадёт в ветку 3 и переиспользует ту же строку.
        job = job_env.run(payload_for(job_env.contract, rows), job=second_job)

        assert job.status == ImportJobStatus.done.value
        assert job.to_review == 1
        rows_in_catalog = job_env.db.execute(
            sa.select(sa.func.count()).select_from(CatalogPosition)
        ).scalar_one()
        assert rows_in_catalog == 1


# ---------------------------------------------------------------------------
#  Ошибки: домен откатывается, error переживает откат
# ---------------------------------------------------------------------------

class TestFailures:
    def test_parse_error_becomes_job_error(self, job_env):
        def broken_parse(handle):
            raise EstimateParseError("Не найден маркер лота")

        job = job_env.run(None, parse=broken_parse)

        assert job.status == ImportJobStatus.error.value
        assert job.error_text == "Не найден маркер лота"
        assert job.finished_at is not None
        assert job_env.estimates() == []

    def test_import_error_rolls_back_the_domain(self, job_env):
        from tests.payloads import proposal

        payload = payload_for(
            job_env.contract,
            proposals={
                "contractor_1": proposal([position(job_title="Работа")], title="ООО Первый"),
                "contractor_2": proposal([position(job_title="Работа")], title="ООО Второй"),
            },
        )

        job = job_env.run(payload)

        assert job.status == ImportJobStatus.error.value
        assert "несколькими подрядчиками" in job.error_text
        assert job_env.estimates() == []

    def test_missing_file_becomes_job_error(self, job_env):
        job_env.storage.delete(job_env.job.file_key)

        job = job_env.run(payload_for(job_env.contract))

        assert job.status == ImportJobStatus.error.value
        assert "недоступен" in job.error_text

    def test_normalization_failure_fails_the_job(self, job_env, monkeypatch):
        """§11: тихая деградация нормализации недопустима — джоб обязан упасть."""

        def unavailable(_text):
            raise NormalizationUnavailableError("Модель spaCy не найдена")

        monkeypatch.setattr(
            "services.matching.normalize_job_title_with_lemmatization", unavailable
        )

        job = job_env.run(payload_for(job_env.contract))

        assert job.status == ImportJobStatus.error.value
        assert "Матчинг невозможен" in job.error_text
        assert job_env.estimates() == []

    def test_unexpected_error_is_reported_not_swallowed(self, job_env, monkeypatch):
        monkeypatch.setattr(
            "services.import_pipeline.match_positions",
            lambda *a, **kw: (_ for _ in ()).throw(ZeroDivisionError("бух")),
        )

        job = job_env.run(payload_for(job_env.contract))

        assert job.status == ImportJobStatus.error.value
        assert "ZeroDivisionError" in job.error_text
        assert job_env.estimates() == []


# ---------------------------------------------------------------------------
#  `AGENTS.md` §5: смета и done коммитятся атомарно (crash window закрыт)
# ---------------------------------------------------------------------------

class TestAtomicity:
    def test_crash_between_matching_and_final_leaves_no_estimate(self, job_env, monkeypatch):
        """Не существует состояния «смета в БД, а job не done» (§5)."""

        def boom(*_args, **_kwargs):
            raise RuntimeError("падение между матчингом и финалом")

        monkeypatch.setattr("services.import_pipeline.finalize_done", boom)

        job = job_env.run(payload_for(job_env.contract))

        assert job.status == ImportJobStatus.error.value
        assert job_env.estimates() == []
        assert (
            job_env.db.execute(sa.select(sa.func.count()).select_from(PositionItem)).scalar_one()
            == 0
        )

    def test_domain_price_warning_does_not_survive_a_crash_before_finalize(
        self, job_env, monkeypatch
    ):
        """§5, задача 7 плана правила цены: предупреждение о состоянии домена
        (`_price_domain_warnings` — здесь отрицательная цена за единицу)
        описывает домен и обязано откатиться вместе с ним.

        Прямое наблюдение, которого не хватало `test_estimate_import.py`
        (тот файл вызывает `import_estimate` напрямую и никогда не видит
        `import_jobs.warnings` — только возвращаемый `ImportOutcome`; ревью
        задачи 7, правки 1 и 2). Здесь — полный пайплайн: строка с
        отрицательной ценой доходит до конца `import_estimate` НОРМАЛЬНО (тот
        же приём, что `test_crash_between_matching_and_final_leaves_no_estimate`
        выше — падение после матчинга, patch `finalize_done`), предупреждение
        уже лежит в возвращённом `domain_warnings`, и только ПОСЛЕДНЯЯ
        операция транзакции (`finalize_done`) должна была записать его в
        `import_jobs.warnings`. Она падает раньше — колонка остаётся тем же
        `'[]'::jsonb`, которым создан job (`StatusWriter.fail` её не трогает).
        """

        def boom(*_args, **_kwargs):
            raise RuntimeError("падение между матчингом и финалом")

        monkeypatch.setattr("services.import_pipeline.finalize_done", boom)

        job = job_env.run(
            payload_for(
                job_env.contract,
                [
                    position(
                        job_title="Работа",
                        unit="шт",
                        unit_cost_total="-5.00",
                        total_cost_total="-50.00",
                    )
                ],
            )
        )

        assert job.status == ImportJobStatus.error.value
        assert job_env.estimates() == []
        assert not any("отрицательная" in w for w in job.warnings)

    def test_catalog_rows_of_a_failed_import_are_rolled_back_too(self, job_env, monkeypatch):
        """Каталог наполняется в той же транзакции — TO_REVIEW-мусор не остаётся."""
        monkeypatch.setattr(
            "services.import_pipeline.finalize_done",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("бух")),
        )

        job_env.run(payload_for(job_env.contract, [position(job_title="Небывалая работа")]))

        job_env.db.expire_all()
        assert (
            job_env.db.execute(
                sa.select(sa.func.count()).select_from(CatalogPosition)
            ).scalar_one()
            == 0
        )

    def test_process_death_before_error_is_healed_by_recovery(self, job_env, monkeypatch):
        """Если сессия A тоже недоступна, задание лечит startup-recovery (§5)."""
        monkeypatch.setattr(
            "services.import_pipeline.finalize_done",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("бух")),
        )
        monkeypatch.setattr(
            import_pipeline.StatusWriter,
            "_run",
            lambda self, values: None
            if values.get("status") == ImportJobStatus.error.value
            else _original_run(self, values),
        )

        job = job_env.run(payload_for(job_env.contract))

        # Задание осталось незавершённым, сметы нет.
        assert job.status != ImportJobStatus.error.value
        assert job_env.estimates() == []

        with job_env.session_factory() as db:
            assert recover_interrupted_jobs(db) == 1
            db.commit()
        assert job_env.reload(job).status == ImportJobStatus.error.value


_original_run = import_pipeline.StatusWriter._run


# ---------------------------------------------------------------------------
#  Мягкий таймаут (§3)
# ---------------------------------------------------------------------------

class TestSoftTimeout:
    def test_slow_parsing_trips_the_soft_timeout(self, job_env):
        payload = payload_for(job_env.contract)

        with freeze_time("2026-05-01 10:00:00") as frozen:

            def slow_parse(handle):
                frozen.tick(timedelta(minutes=11))
                return fake_parse(payload)(handle)

            job = job_env.run(payload, parse=slow_parse, soft_timeout_minutes=10)

        assert job.status == ImportJobStatus.error.value
        assert "не завершился за отведённые 10 мин" in job.error_text
        assert job_env.estimates() == []

    def test_timeout_is_soft_and_does_not_cut_a_stage(self, job_env):
        """Проверка — на границе этапа: сам этап доводится до конца."""
        payload = payload_for(job_env.contract)
        finished = []

        with freeze_time("2026-05-01 10:00:00") as frozen:

            def slow_parse(handle):
                frozen.tick(timedelta(minutes=99))
                finished.append("parse")
                return fake_parse(payload)(handle)

            job_env.run(payload, parse=slow_parse, soft_timeout_minutes=1)

        assert finished == ["parse"]

    def test_zero_disables_the_timeout(self, job_env):
        payload = payload_for(job_env.contract)

        with freeze_time("2026-05-01 10:00:00") as frozen:

            def slow_parse(handle):
                frozen.tick(timedelta(days=1))
                return fake_parse(payload)(handle)

            job = job_env.run(payload, parse=slow_parse, soft_timeout_minutes=0)

        assert job.status == ImportJobStatus.done.value


# ---------------------------------------------------------------------------
#  replace через пайплайн (§5, правило 3)
# ---------------------------------------------------------------------------

class TestReplaceThroughPipeline:
    def test_replace_swaps_the_estimate_and_records_the_audit_warning(self, job_env):
        rows = [position(job_title="Работа", unit="м2", unit_cost_total="10")]
        first = job_env.run(payload_for(job_env.contract, rows))
        old_estimate_id = job_env.estimates()[0].id

        second_job = job_env.new_job()
        job_env.db.commit()
        job = job_env.run(payload_for(job_env.contract, rows), job=second_job, replace=True)

        assert job.status == ImportJobStatus.done.value
        estimates = job_env.estimates()
        assert len(estimates) == 1
        assert estimates[0].id != old_estimate_id
        assert any(f"estimate_id={old_estimate_id}" in w for w in job.warnings)
        # Прежнее задание — аудит, оно на месте и осталось done.
        assert job_env.reload(first).status == ImportJobStatus.done.value

    def test_without_replace_the_unique_index_fails_the_job(self, job_env):
        rows = [position(job_title="Работа", unit="м2", unit_cost_total="10")]
        job_env.run(payload_for(job_env.contract, rows))
        old_estimate_id = job_env.estimates()[0].id

        second_job = job_env.new_job()
        job_env.db.commit()
        job = job_env.run(payload_for(job_env.contract, rows), job=second_job)

        assert job.status == ImportJobStatus.error.value
        # Старая смета не пострадала.
        assert [e.id for e in job_env.estimates()] == [old_estimate_id]


# ---------------------------------------------------------------------------
#  Нормализация в пайплайне — настоящая
# ---------------------------------------------------------------------------

class TestRealNormalization:
    def test_catalog_row_carries_real_normalized_title(self, job_env):
        job_env.run(payload_for(job_env.contract, [position(job_title="Устройство стяжек")]))

        row = job_env.db.execute(sa.select(CatalogPosition)).scalar_one()
        assert row.normalized_job_title == normalize_job_title_with_lemmatization(
            "Устройство стяжек"
        )
        assert row.kind == CatalogKind.TO_REVIEW.value


# ---------------------------------------------------------------------------
#  §2.3: три факта об одном файле — parsed_data сессией A
# ---------------------------------------------------------------------------

class TestParseAudit:
    """Три факта об одном файле (спека контура §2.3): точный ParseResult
    остаётся у job, что бы ни случилось с импортом."""

    def test_done_job_keeps_parsed_data_and_version(self, job_env):
        payload = payload_for(job_env.contract)
        job = job_env.run(payload, parse=fake_parse(payload, version="4.0.0"))
        assert job.status == ImportJobStatus.done.value
        assert job.parsed_data == payload
        assert job.parser_version == "4.0.0"
        assert job.estimates_created == 1

    def test_failed_import_still_keeps_parsed_data(self, job_env):
        """Инъекция отказа ПОСЛЕ парсинга: домен откатился, аудит разбора — нет."""
        payload = payload_for(job_env.contract)
        # Два предложения в лоте — договорный путь отвергает такой файл в
        # _validate_payload, то есть после успешного парсинга.
        payload["lots"]["lot_1"]["proposals"]["contractor_2"] = dict(
            payload["lots"]["lot_1"]["proposals"]["contractor_1"]
        )
        job = job_env.run(payload, parse=fake_parse(payload, version="4.0.0"))
        assert job.status == ImportJobStatus.error.value
        assert job.parsed_data == payload
        assert job.parser_version == "4.0.0"
        assert job.estimates_created is None
        assert job_env.estimates() == []

    def test_parse_failure_leaves_both_null(self, job_env):
        def broken(_handle):
            raise EstimateParseError("не смета")

        job = job_env.run(None, parse=broken)
        assert job.status == ImportJobStatus.error.value
        assert job.parsed_data is None and job.parser_version is None


# ---------------------------------------------------------------------------
#  Раунд (спека контура §2.5): один файл — N offer-смет + baseline, один
#  матчинг на весь набор.
# ---------------------------------------------------------------------------

class TestRoundJob:
    def test_round_job_creates_all_estimates_and_matches_once(self, job_env, monkeypatch):
        from services import import_pipeline as pipeline_module
        from tests.payloads import baseline_proposal_block, proposal, round_payload

        rnd = job_env.factories.TenderRoundFactory.create()
        job_env.db.flush()
        job = job_env.factories.ImportJobFactory.create(
            contract=None, round_id=rnd.id, file_key=job_env.storage.save(b"PK\x03\x04x"),
            status=ImportJobStatus.pending.value,
        )
        job_env.db.commit()

        calls = {"n": 0}
        real_match = pipeline_module.match_positions
        def counting_match(db, items):
            calls["n"] += 1
            return real_match(db, items)
        monkeypatch.setattr(pipeline_module, "match_positions", counting_match)

        payload = round_payload(
            [proposal([position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")], title="ООО А", inn="7700000001"),
             proposal([position(job_title="Работа", unit="м2", unit_cost_total="11", total_cost_total="11")], title="ООО Б", inn="7700000002")],
            baseline=baseline_proposal_block([position(job_title="Работа", unit="м2", unit_cost_total="9", total_cost_total="9")]),
        )
        done = job_env.run(payload, job=job, parse=fake_parse(payload, version="4.0.0"))

        assert done.status == ImportJobStatus.done.value
        assert done.estimates_created == 3
        assert calls["n"] == 1
        assert done.positions_total == 3
        assert (done.matched_cache + done.matched_exact + done.matched_nonposition + done.to_review) == done.positions_total
        assert done.parsed_data == payload
