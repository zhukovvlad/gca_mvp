"""Ограничения схемы фазы 2, на которые завязана логика фаз 4–6.

Тесты закрепляют поведение объектов, которые создаются raw SQL в миграции 0002
и потому исключены из сравнения `alembic check` (alembic/env.py, RAW_SQL_INDEXES):
их пропажа не будет замечена детектором дрейфа — только этими тестами.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from models import (
    CatalogKind,
    CatalogPosition,
    EstimateRawData,
    ImportJobStatus,
    Lot,
    MatchingCache,
    MatchSource,
    PositionItem,
    Proposal,
)

pytestmark = pytest.mark.integration


@contextmanager
def rejected(session, contains: str | None = None):
    """Ожидаем отказ БД внутри savepoint — сессия остаётся пригодной дальше."""
    with pytest.raises(IntegrityError) as exc, session.begin_nested():
        yield
        session.flush()
    if contains is not None:
        assert contains in str(exc.value)


# ---------------------------------------------------------------------------
#  rate_standards: EXCLUDE USING gist — периоды действия не пересекаются
# ---------------------------------------------------------------------------

class TestRateStandardsExclude:
    def test_overlapping_periods_rejected(self, db_session, factories):
        base = factories.RateStandardFactory.create(
            valid_from=dt.date(2025, 1, 1), valid_to=dt.date(2026, 1, 1)
        )
        with rejected(db_session, contains="ex_rate_standards_no_overlap"):
            factories.RateStandardFactory.create(
                catalog_position=base.catalog_position,
                rate_class=base.rate_class,
                valid_from=dt.date(2025, 6, 1),
                valid_to=dt.date(2026, 6, 1),
            )

    def test_open_ended_period_blocks_later_one(self, db_session, factories):
        """valid_to IS NULL — бесконечная верхняя граница, COALESCE не нужен."""
        base = factories.RateStandardFactory.create(
            valid_from=dt.date(2025, 1, 1), valid_to=None
        )
        with rejected(db_session, contains="ex_rate_standards_no_overlap"):
            factories.RateStandardFactory.create(
                catalog_position=base.catalog_position,
                rate_class=base.rate_class,
                valid_from=dt.date(2030, 1, 1),
                valid_to=None,
            )

    def test_adjacent_periods_allowed(self, db_session, factories):
        """Период — полуинтервал [valid_from, valid_to): стык не пересечение.

        Это и есть переутверждение норматива: UPDATE valid_to старой строки +
        INSERT новой (AGENTS.md §4).
        """
        base = factories.RateStandardFactory.create(
            valid_from=dt.date(2025, 1, 1), valid_to=dt.date(2026, 1, 1)
        )
        factories.RateStandardFactory.create(
            catalog_position=base.catalog_position,
            rate_class=base.rate_class,
            valid_from=dt.date(2026, 1, 1),
            valid_to=None,
        )
        db_session.flush()

    def test_same_period_different_class_allowed(self, db_session, factories):
        base = factories.RateStandardFactory.create()
        factories.RateStandardFactory.create(
            catalog_position=base.catalog_position,
            rate_class=factories.RateClassFactory.create(),
            valid_from=base.valid_from,
        )
        db_session.flush()

    def test_non_positive_rate_rejected(self, db_session, factories):
        with rejected(db_session, contains="ck_rate_standards_rate_positive"):
            factories.RateStandardFactory.create(standard_unit_rate=Decimal("0"))

    def test_inverted_period_rejected(self, db_session, factories):
        with rejected(db_session, contains="ck_rate_standards_period"):
            factories.RateStandardFactory.create(
                valid_from=dt.date(2026, 1, 1), valid_to=dt.date(2025, 1, 1)
            )


# ---------------------------------------------------------------------------
#  import_jobs: частичный уникальный индекс — лок на пару (договор, ДС)
# ---------------------------------------------------------------------------

class TestImportJobsActiveLock:
    def test_second_active_job_for_same_pair_rejected(self, db_session, factories):
        job = factories.ImportJobFactory.create(status=ImportJobStatus.parsing.value)
        with rejected(db_session, contains="uq_import_jobs_active_pair"):
            factories.ImportJobFactory.create(
                contract=job.contract, amendment_no=None, status=ImportJobStatus.pending.value
            )

    def test_parallel_import_of_different_amendments_allowed(self, db_session, factories):
        """Блокируется ПАРА (contract_id, amendment_no), не договор целиком (§4)."""
        job = factories.ImportJobFactory.create(amendment_no=None)
        factories.ImportJobFactory.create(contract=job.contract, amendment_no=1)
        factories.ImportJobFactory.create(contract=job.contract, amendment_no=2)
        db_session.flush()

    @pytest.mark.parametrize("terminal", [ImportJobStatus.done, ImportJobStatus.error])
    def test_terminal_job_releases_the_lock(self, db_session, factories, terminal):
        job = factories.ImportJobFactory.create(status=terminal.value)
        factories.ImportJobFactory.create(
            contract=job.contract, amendment_no=None, status=ImportJobStatus.pending.value
        )
        db_session.flush()

    def test_many_terminal_jobs_for_same_pair_allowed(self, db_session, factories):
        """История загрузок и замен — аудит, старые jobs не удаляются (§5, §7.1)."""
        job = factories.ImportJobFactory.create(status=ImportJobStatus.done.value)
        for _ in range(3):
            factories.ImportJobFactory.create(
                contract=job.contract, amendment_no=None, status=ImportJobStatus.done.value
            )
        db_session.flush()

    def test_zero_amendment_no_rejected(self, db_session, factories):
        """-1 — сентинел COALESCE; нумерация ДС начинается с 1."""
        with rejected(db_session, contains="ck_import_jobs_amendment_no"):
            factories.ImportJobFactory.create(amendment_no=0)


# ---------------------------------------------------------------------------
#  estimates: UNIQUE NULLS NOT DISTINCT (PG16)
# ---------------------------------------------------------------------------

class TestEstimatesUniqueness:
    def test_two_original_estimates_rejected(self, db_session, factories):
        """amendment_no IS NULL у двух смет одного договора — конфликт.

        Обычный UNIQUE этого не ловит: в нём NULL-ы различны.
        """
        estimate = factories.EstimateFactory.create(amendment_no=None)
        with rejected(db_session, contains="uq_estimates_contract_amendment"):
            factories.EstimateFactory.create(contract=estimate.contract, amendment_no=None)

    def test_duplicate_amendment_no_rejected(self, db_session, factories):
        estimate = factories.EstimateFactory.create(amendment_no=1)
        with rejected(db_session, contains="uq_estimates_contract_amendment"):
            factories.EstimateFactory.create(contract=estimate.contract, amendment_no=1)

    def test_original_and_amendments_coexist(self, db_session, factories):
        estimate = factories.EstimateFactory.create(amendment_no=None)
        factories.EstimateFactory.create(contract=estimate.contract, amendment_no=1)
        factories.EstimateFactory.create(contract=estimate.contract, amendment_no=2)
        db_session.flush()

    def test_one_proposal_per_lot(self, db_session, factories):
        """В смете ГП единственный подрядчик → ровно одно предложение на лот (§4)."""
        proposal = factories.ProposalFactory.create()
        with rejected(db_session, contains="uq_proposals_lot_id"):
            factories.ProposalFactory.create(lot=proposal.lot)


# ---------------------------------------------------------------------------
#  catalog_positions: идентичность = нормализованное название + единица
# ---------------------------------------------------------------------------

class TestCatalogIdentity:
    def test_same_normalized_pair_rejected(self, db_session, factories):
        factories.CatalogPositionFactory.create(
            standard_job_title="Кладка кирпича", normalized_job_title="кладка кирпич", unit_id=None
        )
        with rejected(db_session, contains="uq_catalog_positions_norm_unit"):
            # Другое отображаемое название, но та же нормализованная пара —
            # это одна и та же работа.
            factories.CatalogPositionFactory.create(
                standard_job_title="кладка  КИРПИЧА",
                normalized_job_title="кладка кирпич",
                unit_id=None,
            )

    def test_same_title_different_units_allowed(self, db_session, factories):
        m2 = db_session.execute(
            sa.text("SELECT id FROM units_of_measure WHERE code = 'M2'")
        ).scalar_one()
        m3 = db_session.execute(
            sa.text("SELECT id FROM units_of_measure WHERE code = 'M3'")
        ).scalar_one()
        factories.CatalogPositionFactory.create(normalized_job_title="штукатурка", unit_id=m2)
        factories.CatalogPositionFactory.create(normalized_job_title="штукатурка", unit_id=m3)
        factories.CatalogPositionFactory.create(normalized_job_title="штукатурка", unit_id=None)
        db_session.flush()

    def test_get_or_create_on_conflict_uses_the_same_index(self, db_session, factories):
        """Ветка 3 каскада матчинга (§5): INSERT ... ON CONFLICT DO NOTHING.

        Арбитром конфликта служит выражение COALESCE(unit_id,-1) — если индекса
        нет или он объявлен иначе, PG ответит «no unique or exclusion constraint
        matching the ON CONFLICT specification».
        """
        factories.CatalogPositionFactory.create(
            standard_job_title="Монтаж",
            normalized_job_title="монтаж",
            unit_id=None,
            kind=CatalogKind.TO_REVIEW.value,
        )
        db_session.flush()

        insert_sql = sa.text(
            """
            INSERT INTO catalog_positions (standard_job_title, normalized_job_title, unit_id, kind)
            VALUES (:title, :norm, NULL, 'TO_REVIEW')
            ON CONFLICT (normalized_job_title, COALESCE(unit_id, -1)) DO NOTHING
            """
        )
        db_session.execute(insert_sql, {"title": "монтаж", "norm": "монтаж"})

        count = db_session.execute(
            sa.select(sa.func.count())
            .select_from(CatalogPosition)
            .where(CatalogPosition.normalized_job_title == "монтаж")
        ).scalar_one()
        assert count == 1

    def test_unknown_kind_rejected(self, db_session, factories):
        with rejected(db_session, contains="ck_catalog_positions_kind"):
            factories.CatalogPositionFactory.create(kind="GROUP_TITLE")

    def test_fts_vector_is_generated(self, db_session, factories):
        position = factories.CatalogPositionFactory.create(
            standard_job_title="устройство бетонной стяжки"
        )
        db_session.flush()
        found = db_session.execute(
            sa.text(
                "SELECT id FROM catalog_positions "
                "WHERE fts_vector @@ to_tsquery('simple', 'стяжки') AND id = :id"
            ),
            {"id": position.id},
        ).scalar_one_or_none()
        assert found == position.id


# ---------------------------------------------------------------------------
#  matching_cache
# ---------------------------------------------------------------------------

class TestMatchingCache:
    """Правило TTL (§4): 'auto' обязан иметь срок, 'manual' обязан не иметь."""

    AUTO_TTL = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)

    def _cache_row(self, position, **kwargs):
        payload = {
            "cache_key": "k" * 64,
            "norm_version": 1,
            "job_title_text": "кладка кирпича",
            "unit_text": "м2",
            "catalog_position_id": position.id,
            "source": MatchSource.auto.value,
            "expires_at": self.AUTO_TTL,
        }
        payload.update(kwargs)
        return MatchingCache(**payload)

    def test_both_valid_combinations_accepted(self, db_session, factories):
        position = factories.CatalogPositionFactory.create()
        db_session.flush()
        db_session.add(self._cache_row(position, cache_key="a" * 64))
        db_session.add(
            self._cache_row(
                position, cache_key="b" * 64, source=MatchSource.manual.value, expires_at=None
            )
        )
        db_session.flush()

    def test_auto_entry_must_have_ttl(self, db_session, factories):
        """Без срока автоматическая запись стала бы бессрочной и подменила бы
        собой ручное решение — вечный кэш промаха матчинга."""
        position = factories.CatalogPositionFactory.create()
        db_session.flush()
        with rejected(db_session, contains="ck_matching_cache_ttl_by_source"):
            db_session.add(
                self._cache_row(position, source=MatchSource.auto.value, expires_at=None)
            )

    def test_manual_entry_cannot_expire(self, db_session, factories):
        """Ручное решение из Review не истекает (§4, DoD «переживает 30 дней»)."""
        position = factories.CatalogPositionFactory.create()
        db_session.flush()
        with rejected(db_session, contains="ck_matching_cache_ttl_by_source"):
            db_session.add(
                self._cache_row(
                    position,
                    source=MatchSource.manual.value,
                    expires_at=dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
                )
            )

    def test_unknown_source_rejected(self, db_session, factories):
        # Неизвестный source нарушает оба CHECK-а сразу (ни одна ветка правила
        # TTL к нему не подходит), а порядок их проверки PostgreSQL не
        # гарантирует — поэтому имя конкретного констрейнта здесь не фиксируем.
        position = factories.CatalogPositionFactory.create()
        db_session.flush()
        with rejected(db_session, contains="matching_cache"):
            db_session.add(self._cache_row(position, source="guess"))

    def test_cache_rows_die_with_their_catalog_position(self, db_session, factories):
        """FK ON DELETE CASCADE: удаление каталожной строки не оставляет
        висящих ключей кэша.

        Проверяется именно каскад. Инвариант «кэш никогда не ссылается на
        строку kind='TO_REVIEW'» (§5) обеспечивается кодом матчинга, а не
        схемой, и закрепляется тестами фазы 4 — поэтому здесь взята обычная
        POSITION, а не запрещённая связка.
        """
        position = factories.CatalogPositionFactory.create(kind=CatalogKind.POSITION.value)
        db_session.flush()
        db_session.add(self._cache_row(position))
        db_session.flush()

        db_session.execute(
            sa.delete(CatalogPosition).where(CatalogPosition.id == position.id)
        )
        remaining = db_session.execute(
            sa.select(sa.func.count()).select_from(MatchingCache)
        ).scalar_one()
        assert remaining == 0


# ---------------------------------------------------------------------------
#  Каскады и защита истории
# ---------------------------------------------------------------------------

class TestCascades:
    def test_deleting_estimate_removes_its_whole_subtree(self, db_session, factories):
        """replace-флоу (§5, правило 3) удаляет смету одним DELETE."""
        proposal = factories.ProposalFactory.create()
        estimate = proposal.lot.estimate
        factories.PositionItemFactory.create(proposal=proposal)
        db_session.add(
            EstimateRawData(estimate_id=estimate.id, raw_data={"a": 1}, parser_version="test")
        )
        db_session.flush()

        db_session.execute(sa.delete(sa.table("estimates")).where(sa.column("id") == estimate.id))
        db_session.expire_all()

        for entity in (Lot, Proposal, PositionItem, EstimateRawData):
            count = db_session.execute(
                sa.select(sa.func.count()).select_from(entity)
            ).scalar_one()
            assert count == 0, f"{entity.__name__} пережил удаление сметы"

    def test_catalog_position_survives_estimate_deletion(self, db_session, factories):
        """Каталог общий (§3): удаление сметы не трогает справочник работ."""
        position = factories.CatalogPositionFactory.create()
        item = factories.PositionItemFactory.create(catalog_position=position)
        estimate = item.proposal.lot.estimate
        db_session.flush()

        db_session.execute(sa.delete(sa.table("estimates")).where(sa.column("id") == estimate.id))
        db_session.expire_all()

        assert db_session.get(CatalogPosition, position.id) is not None

    def test_catalog_position_in_use_cannot_be_deleted(self, db_session, factories):
        """Условие безопасности DELETE в Review: ссылки переносятся ДО удаления (§5)."""
        position = factories.CatalogPositionFactory.create(kind=CatalogKind.TO_REVIEW.value)
        factories.PositionItemFactory.create(catalog_position=position)
        db_session.flush()

        with rejected(db_session, contains="position_items"):
            db_session.execute(
                sa.delete(CatalogPosition).where(CatalogPosition.id == position.id)
            )


# ---------------------------------------------------------------------------
#  Деньги
# ---------------------------------------------------------------------------

def test_money_round_trips_as_decimal(db_session, factories):
    """numeric в БД ↔ Decimal в Python; никаких float (§3)."""
    item = factories.PositionItemFactory.create(
        unit_cost_total=Decimal("1234567.891234"),
        total_cost_total=Decimal("0.01"),
    )
    db_session.flush()
    db_session.expire(item)

    assert isinstance(item.unit_cost_total, Decimal)
    assert item.unit_cost_total == Decimal("1234567.891234")
    assert item.total_cost_total == Decimal("0.01")
