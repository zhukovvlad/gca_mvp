"""Оркестратор задания импорта: две сессии, атомарность, recovery (AGENTS.md §5)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from freezegun import freeze_time

from models import (
    CatalogContext,
    CatalogKind,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    DecisionSource,
    Estimate,
    ImportJob,
    ImportJobStatus,
    Lot,
    NameRole,
    PositionItem,
    Proposal,
    SemanticEvent,
    SemanticKind,
    SemanticState,
)
from parser import EstimateParseError, ParseResult
from parser.sanitize_text import NormalizationUnavailableError, normalize_job_title_with_lemmatization
from services import import_pipeline
from services.context_routing import get_or_create_bucket
from services.context_routing import route_positions as _real_route_positions
from services.maintenance import recover_interrupted_jobs
from tests.payloads import payload_for, position

pytestmark = pytest.mark.integration


def _now() -> datetime:
    return datetime.now(UTC)


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


# ---------------------------------------------------------------------------
#  Задача 5 плана: членства на импорте (сессия B, между матчингом и финалом).
#  `docs/superpowers/plans/2026-09-22-catalog-families.md`, Task 5.
# ---------------------------------------------------------------------------

class TestMembershipsBuiltOnImport:
    """У каждой не-раздельной позиции ровно одно членство, у строк-разделов —
    ни одного. Сверка МНОЖЕСТВОМ `position_item_id`, а не только числом:
    равное количество не доказывает совпадающий состав."""

    def test_every_non_chapter_position_gets_one_membership_and_chapters_get_none(
        self, job_env
    ):
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

        job_env.db.expire_all()
        rows = job_env.db.execute(sa.select(PositionItem.id, PositionItem.is_chapter)).all()
        non_chapter_ids = {r.id for r in rows if not r.is_chapter}
        chapter_ids = {r.id for r in rows if r.is_chapter}
        assert non_chapter_ids and chapter_ids  # входные данные действительно смешаны

        member_ids = set(
            job_env.db.execute(sa.select(ContextMember.position_item_id)).scalars().all()
        )

        assert member_ids == non_chapter_ids
        assert member_ids.isdisjoint(chapter_ids)


class TestMembershipsCoverEveryRoundEstimate:
    """Раунд покрыт целиком: членства есть у позиций КАЖДОЙ сметы раунда
    (обеих offer-смет и baseline). Сверка по каждому `estimate_id`
    отдельно, а не суммой по всему раунду — иначе смета, оставшаяся без
    маршрутизации, спряталась бы за членствами других смет того же раунда."""

    def test_each_round_estimate_has_memberships_for_all_its_positions(self, job_env):
        from tests.payloads import baseline_proposal_block, proposal, round_payload

        rnd = job_env.factories.TenderRoundFactory.create()
        job_env.db.flush()
        job = job_env.factories.ImportJobFactory.create(
            contract=None, round_id=rnd.id, file_key=job_env.storage.save(b"PK\x03\x04y"),
            status=ImportJobStatus.pending.value,
        )
        job_env.db.commit()

        payload = round_payload(
            [
                proposal(
                    [position(job_title="Работа", unit="м2", unit_cost_total="10", total_cost_total="10")],
                    title="ООО А", inn="7700000001",
                ),
                proposal(
                    [position(job_title="Работа", unit="м2", unit_cost_total="11", total_cost_total="11")],
                    title="ООО Б", inn="7700000002",
                ),
            ],
            baseline=baseline_proposal_block(
                [position(job_title="Работа", unit="м2", unit_cost_total="9", total_cost_total="9")]
            ),
        )
        done = job_env.run(payload, job=job, parse=fake_parse(payload, version="4.0.0"))

        assert done.status == ImportJobStatus.done.value
        assert done.estimates_created == 3

        job_env.db.expire_all()
        estimate_ids = (
            job_env.db.execute(sa.select(Estimate.id).where(Estimate.import_job_id == done.id))
            .scalars()
            .all()
        )
        assert len(estimate_ids) == 3

        for estimate_id in estimate_ids:
            rows = job_env.db.execute(
                sa.select(PositionItem.id, PositionItem.is_chapter)
                .join(Proposal, Proposal.id == PositionItem.proposal_id)
                .join(Lot, Lot.id == Proposal.lot_id)
                .where(Lot.estimate_id == estimate_id)
            ).all()
            non_chapter_ids = {r.id for r in rows if not r.is_chapter}
            assert non_chapter_ids, f"смета {estimate_id}: нет позиций в фикстуре"

            member_ids = set(
                job_env.db.execute(
                    sa.select(ContextMember.position_item_id).where(
                        ContextMember.position_item_id.in_(non_chapter_ids)
                    )
                ).scalars().all()
            )
            assert member_ids == non_chapter_ids, f"смета {estimate_id}: членства не совпали"


class TestRoutingBelongsToTransactionB:
    """Членства пишутся В ТОЙ ЖЕ транзакции сессии B, что и домен (план, Task 5;
    спека §2.9). Инъекция отказа ПОСЛЕ маршрутизации проверяет ВНУТРИ той же
    сессии, что членства уже записаны и позиции уже сопоставлены, ПРЕЖДЕ чем
    бросить исключение — иначе тест не отличил бы «записали и откатили» от
    «ничего не записали»."""

    def test_failure_after_routing_rolls_back_everything_including_memberships(
        self, job_env, monkeypatch
    ):
        from services import import_pipeline as pipeline_module

        def boom(db, *, estimate_ids, **kwargs):
            _real_route_positions(db, estimate_ids=estimate_ids, **kwargs)
            # (б) членства уже записаны — счёт > 0 — И позиции уже сопоставлены
            # с каталогом (маршрутизация идёт после матчинга).
            member_count = db.execute(
                sa.select(sa.func.count()).select_from(ContextMember)
            ).scalar_one()
            assert member_count > 0, "обёртка вызвана раньше, чем членства появились"
            unmatched = db.execute(
                sa.select(sa.func.count())
                .select_from(PositionItem)
                .where(
                    PositionItem.is_chapter.is_(False),
                    PositionItem.catalog_position_id.is_(None),
                )
            ).scalar_one()
            assert unmatched == 0, "позиции ещё не сопоставлены — маршрутизация раньше матчинга?"
            raise RuntimeError("инъекция отказа ПОСЛЕ маршрутизации")

        monkeypatch.setattr(pipeline_module, "route_positions", boom)

        job = job_env.run(
            payload_for(
                job_env.contract,
                [
                    position(job_title="Раздел 1", is_chapter=True, chapter_number="1"),
                    position(job_title="Устройство стяжки", unit="м2", unit_cost_total="100", number="2"),
                ],
            )
        )

        assert job.status == ImportJobStatus.error.value
        assert job_env.estimates() == []

        job_env.db.expire_all()
        for model in (ContextMember, ContextBucket, CatalogContext, SemanticEvent):
            count = job_env.db.execute(
                sa.select(sa.func.count()).select_from(model)
            ).scalar_one()
            assert count == 0, f"{model.__name__}: откат не убрал строки"

    def test_failure_in_finalize_leaves_no_memberships(self, job_env, monkeypatch):
        """Второй вход той же природы: отказ в `finalize_done` доказывает,
        что членства не коммитятся раньше финала — они тоже откатываются
        вместе с доменом."""

        def boom(*_args, **_kwargs):
            raise RuntimeError("падение между маршрутизацией и финалом")

        monkeypatch.setattr("services.import_pipeline.finalize_done", boom)

        job = job_env.run(
            payload_for(job_env.contract, [position(job_title="Работа", unit="шт", unit_cost_total="10")])
        )

        assert job.status == ImportJobStatus.error.value
        job_env.db.expire_all()
        member_count = job_env.db.execute(
            sa.select(sa.func.count()).select_from(ContextMember)
        ).scalar_one()
        assert member_count == 0


class TestRoutingHappensAfterCatalogAndBeforeFinal:
    """Порядок «матчинг → маршрутизация → финал» — утверждение о ПОТОКЕ
    ДАННЫХ, а не о тексте кода. Две половины теста стерегут РАЗНОЕ:

    - половина про статус (`seen_status`) стережёт маршрутизацию ПОСЛЕ ТОГО,
      как сессия B уже закоммичена (например, маршрутизация в отдельной
      сессии, начатой уже после выхода из `with session_factory() as db,
      db.begin():`): независимая сессия читает статус job ВНУТРИ обёртки
      `route_positions` и обязана увидеть `matching`, а не `done`;
    - половина про число членств (`seen_member_counts`) стережёт порядок
      ВНУТРИ самой транзакции B — что `route_positions` вызывается РАНЬШЕ
      `finalize_done`, а не после него: обёртка `finalize_done` считает
      членства В ТОЙ ЖЕ сессии B и обязана увидеть их уже существующими.

    Обе половины нужны вместе: перестановка `route_positions` ПОСЛЕ
    `finalize_done`, но всё ещё ВНУТРИ ещё не закоммиченной транзакции B, не
    трогает статус (`done` до коммита B в любом случае не виден независимой
    сессии, порядок внутри B на это не влияет) — такую перестановку ловит
    только половина про число членств."""

    def test_status_has_not_reached_done_yet_during_routing_and_memberships_exist_before_finalize(
        self, job_env, monkeypatch
    ):
        from services import import_pipeline as pipeline_module

        seen_status: list[str] = []

        def spying_route_positions(db, *, estimate_ids, **kwargs):
            with job_env.session_factory() as independent:
                seen_status.append(
                    independent.execute(
                        sa.select(ImportJob.status).where(ImportJob.id == job_env.job.id)
                    ).scalar_one()
                )
            return _real_route_positions(db, estimate_ids=estimate_ids, **kwargs)

        real_finalize_done = pipeline_module.finalize_done
        seen_member_counts: list[int] = []

        def spying_finalize_done(db, job_id, **kwargs):
            seen_member_counts.append(
                db.execute(sa.select(sa.func.count()).select_from(ContextMember)).scalar_one()
            )
            return real_finalize_done(db, job_id, **kwargs)

        monkeypatch.setattr(pipeline_module, "route_positions", spying_route_positions)
        monkeypatch.setattr(pipeline_module, "finalize_done", spying_finalize_done)

        payload = payload_for(
            job_env.contract, [position(job_title="Работа", unit="шт", unit_cost_total="10")]
        )
        job = job_env.run(payload)

        assert job.status == ImportJobStatus.done.value
        # НЕ done: статус читается ВНУТРИ обёртки, пока job ещё в процессе.
        assert seen_status == [ImportJobStatus.matching.value]
        # Ровно одно членство — единственная не-раздельная позиция фикстуры —
        # уже существует к моменту вызова finalize_done.
        assert seen_member_counts == [1]


class TestFiveCountersStayTheSame:
    """Пять счётчиков `import_jobs` — те же, что до фичи, побайтно:
    маршрутизация их не расширяет и не сдвигает (спека §2.9)."""

    def test_matchcounters_keys_equal_the_five_known_names(self):
        """(а) Независимый литерал-оракул, а не перебор словаря модуля."""
        from services.matching import MatchCounters

        assert set(MatchCounters().as_dict().keys()) == {
            "positions_total",
            "matched_cache",
            "matched_exact",
            "matched_nonposition",
            "to_review",
        }

    def test_import_jobs_table_gained_no_new_columns(self):
        """Литерал снят ДО задачи 5 из `models.py` (`ImportJob`) —
        маршрутизация не заводит новых колонок `import_jobs`."""
        assert {c.name for c in ImportJob.__table__.columns} == {
            "id",
            "contract_id",
            "round_id",
            "amendment_no",
            "filename",
            "file_key",
            "file_sha256",
            "status",
            "error_text",
            "warnings",
            "positions_total",
            "matched_cache",
            "matched_exact",
            "matched_nonposition",
            "to_review",
            "parsed_data",
            "parser_version",
            "estimates_created",
            "created_at",
            "started_at",
            "finished_at",
        }

    def test_routing_does_not_shift_the_five_counters_delivered_to_final_status(
        self, job_env, monkeypatch
    ):
        """(б) Шпион на `match_positions` снимает КОПИЮ счётчиков сразу после
        матчинга; шпион на `finalize_done` — значения, дошедшие до финала.
        Плюс литеральные ожидаемые числа, выведенные из payload (2
        не-раздельные ИМЕНОВАННЫЕ позиции, обе новые — обе уходят в
        `to_review`), чтобы тест не был тавтологией шпиона.

        Во входе есть ЧЕТВЁРТАЯ позиция — БЕЗ названия работы. Она не входит
        в каскад матчинга вовсе (`estimate_import.py`: строка без
        `job_title` не допускается к матчингу за отсутствием идентичности),
        поэтому в `MatchCounters` не видна, но доходит до `route_positions` и
        считается там отдельным счётчиком `RoutingOutcome.members_skipped_unmatched`
        — счётчиком, которого среди пяти колонок `import_jobs` нет. Именно
        такой вход и нужен утверждению «маршрутизация не сдвигает ни одного
        из пяти счётчиков»: без строки, у которой нет `catalog_position_id`,
        утечка `members_skipped_unmatched` в `to_review` была бы неотличима
        от отсутствия утечки — оба случая дали бы `to_review == 2`."""
        from services import import_pipeline as pipeline_module

        real_match_positions = pipeline_module.match_positions
        real_finalize_done = pipeline_module.finalize_done
        snapshots: dict[str, object] = {}

        def spying_match(db, items):
            result = real_match_positions(db, items)
            snapshots["after_match"] = dict(result.counters.as_dict())
            return result

        def spying_finalize(db, job_id, *, counters, warnings, now, estimates_created):
            snapshots["at_finalize"] = dict(counters.as_dict())
            snapshots["at_finalize_keys"] = set(counters.as_dict().keys())
            return real_finalize_done(
                db,
                job_id,
                counters=counters,
                warnings=warnings,
                now=now,
                estimates_created=estimates_created,
            )

        monkeypatch.setattr(pipeline_module, "match_positions", spying_match)
        monkeypatch.setattr(pipeline_module, "finalize_done", spying_finalize)

        payload = payload_for(
            job_env.contract,
            [
                position(job_title="Раздел 1", is_chapter=True, chapter_number="1"),
                position(job_title="Устройство стяжки", unit="м2", unit_cost_total="100", number="2"),
                position(job_title="Монтаж кабеля", unit="м", unit_cost_total="50", number="3"),
                position(job_title=None, unit="шт", unit_cost_total="5", number="4"),
            ],
        )
        job = job_env.run(payload)

        assert job.status == ImportJobStatus.done.value
        expected = {
            "positions_total": 2,
            "matched_cache": 0,
            "matched_exact": 0,
            "matched_nonposition": 0,
            "to_review": 2,
        }
        assert snapshots["after_match"] == expected
        assert snapshots["at_finalize"] == snapshots["after_match"]
        assert snapshots["at_finalize_keys"] == set(expected.keys())
        assert (
            job.positions_total,
            job.matched_cache,
            job.matched_exact,
            job.matched_nonposition,
            job.to_review,
        ) == (
            expected["positions_total"],
            expected["matched_cache"],
            expected["matched_exact"],
            expected["matched_nonposition"],
            expected["to_review"],
        )

        # Строка без названия сохранена (job_title_in_proposal == "" — NOT
        # NULL, §5 estimate_import), но у неё нет catalog_position_id и,
        # следовательно, нет членства: она — members_skipped_unmatched, а не
        # часть каскада.
        job_env.db.expire_all()
        untitled_id = job_env.db.execute(
            sa.select(PositionItem.id).where(PositionItem.job_title_in_proposal == "")
        ).scalar_one()
        untitled_has_membership = job_env.db.execute(
            sa.select(sa.func.count())
            .select_from(ContextMember)
            .where(ContextMember.position_item_id == untitled_id)
        ).scalar_one()
        assert untitled_has_membership == 0


class TestRoutingFailureIsADomainRefusal:
    """Отказ маршрутизации — доменный, а не диагностический: корзина без
    действующего контекста по умолчанию роняет job в `error` с текстом,
    называющим корзину (спека §2.4). Каталожная строка строится так, чтобы
    совпасть с той, которую найдёт матчинг (та же нормализация, что и у
    `TestRealNormalization` выше)."""

    def test_bucket_without_a_live_default_context_fails_the_job_naming_the_bucket(
        self, job_env
    ):
        title = "Устройство фундамента под опору"
        normalized = normalize_job_title_with_lemmatization(title)

        catalog_position = job_env.factories.CatalogPositionFactory.create(
            standard_job_title=title,
            normalized_job_title=normalized,
            kind=CatalogKind.POSITION.value,
            unit_id=None,
        )
        job_env.db.flush()

        # Корзина той пары, в которую попадёт позиция импорта: без раздела
        # эффективная статья пуста (`work_category_id=None`) — тот же путь,
        # что у позиции без раздела в матчинге.
        bucket, created = get_or_create_bucket(
            job_env.db, catalog_position_id=catalog_position.id, work_category_id=None
        )
        assert created is True

        # ЗААРХИВИРОВАННЫЙ прямой правкой строки default-контекст — единственный,
        # и он не действующий: у корзины нет живого default (спека §2.4).
        default_context = CatalogContext(
            bucket_id=bucket.id,
            is_default=True,
            semantic_kind=SemanticKind.WORK.value,
            semantic_kind_source=DecisionSource.rule.value,
            semantic_kind_at=_now(),
            name_role=NameRole.WORK.value,
            name_role_source=DecisionSource.rule.value,
            name_role_at=_now(),
            place_dictionary_version=1,
            semantic_state=SemanticState.SUGGESTED.value,
            archived_at=_now(),
        )
        job_env.db.add(default_context)
        job_env.db.commit()

        bucket_id = bucket.id

        payload = payload_for(
            job_env.contract, [position(job_title=title, unit=None, unit_cost_total="10")]
        )
        job = job_env.run(payload)

        assert job.status == ImportJobStatus.error.value
        # Текст РОВНО тот, что строит `_live_default_context` (RoutingError) —
        # не просто «где-то содержит цифру id корзины»: `f"...{id} "` с
        # пробелом после числа якорит id как целое слово, а не подстроку.
        assert job.error_text == (
            f"у корзины {bucket_id} нет действующего контекста по умолчанию — "
            "маршрутизация отказывает, а не подставляет случайный контекст"
        )
        assert not job.error_text.startswith("Непредвиденная ошибка импорта")
        assert job_env.estimates() == []

        job_env.db.expire_all()
        member_count = job_env.db.execute(
            sa.select(sa.func.count()).select_from(ContextMember)
        ).scalar_one()
        assert member_count == 0


class TestReplaceReusesBucketsForNewEstimate:
    """Замена сметы (`replace=True`) через пайплайн: членства есть у позиций
    НОВОЙ сметы и ни одного, указывающего на удалённые позиции; корзина той
    же пары переиспользуется, а не дублируется."""

    def test_replace_gives_memberships_to_new_positions_only_and_reuses_the_bucket(
        self, job_env
    ):
        rows = [position(job_title="Кладка стен", unit="м2", unit_cost_total="10")]
        first = job_env.run(payload_for(job_env.contract, rows))
        assert first.status == ImportJobStatus.done.value

        job_env.db.expire_all()
        old_position_ids = set(job_env.db.execute(sa.select(PositionItem.id)).scalars().all())
        bucket_count_before = job_env.db.execute(
            sa.select(sa.func.count()).select_from(ContextBucket)
        ).scalar_one()

        second_job = job_env.new_job()
        job_env.db.commit()
        job = job_env.run(payload_for(job_env.contract, rows), job=second_job, replace=True)

        assert job.status == ImportJobStatus.done.value

        job_env.db.expire_all()
        new_position_ids = set(job_env.db.execute(sa.select(PositionItem.id)).scalars().all())
        assert new_position_ids.isdisjoint(old_position_ids)

        member_position_ids = set(
            job_env.db.execute(sa.select(ContextMember.position_item_id)).scalars().all()
        )
        assert member_position_ids == new_position_ids
        assert member_position_ids.isdisjoint(old_position_ids)

        bucket_count_after = job_env.db.execute(
            sa.select(sa.func.count()).select_from(ContextBucket)
        ).scalar_one()
        assert bucket_count_after == bucket_count_before
