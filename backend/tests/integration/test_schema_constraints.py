"""Ограничения схемы фазы 2, на которые завязана логика фаз 4–6.

Тесты закрепляют поведение объектов, которые создаются raw SQL в миграции 0002
и потому исключены из сравнения `alembic check` (alembic/env.py, RAW_SQL_INDEXES):
их пропажа не будет замечена детектором дрейфа — только этими тестами.
"""
from __future__ import annotations

import datetime as dt
import itertools
from contextlib import contextmanager
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import CheckConstraint
from sqlalchemy.exc import IntegrityError, ProgrammingError

from models import (
    PASSPORT_TOP_N_DEFAULT,
    PASSPORT_TOP_N_MAX,
    PASSPORT_TOP_N_MIN,
    AppSettings,
    CatalogKind,
    CatalogPosition,
    Contract,
    Estimate,
    EstimateAdditionalWork,
    EstimateRawData,
    ImportJobStatus,
    Lot,
    MatchingCache,
    MatchSource,
    ObjectModel,
    PositionItem,
    Proposal,
    WorkCategory,
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


def _make_category(session, code: str, sort_order: int, parent_id: int | None = None) -> int:
    """Создаёт статью и возвращает её id. Коды 9xx заведомо вне шаблона."""
    return session.execute(
        sa.text(
            "insert into work_categories (code, title, sort_order, parent_id) "
            "values (:code, 'Тестовая статья', :sort_order, :parent_id) returning id"
        ),
        {"code": code, "sort_order": sort_order, "parent_id": parent_id},
    ).scalar_one()


def _load_work_categories_seed() -> tuple[tuple[str, str], ...]:
    """Читает `WORK_CATEGORIES_SEED` из файла миграции 0005, а не копирует его сюда:

    вторая копия шаблона на 362 строки в тестах неизбежно разошлась бы с первой,
    а проверять нужно именно порядок настоящего литерала. Имя файла миграции не
    является питоновским идентификатором, поэтому обычный `import` не работает —
    модуль грузится по пути через `importlib.util`.
    """
    import importlib.util
    from pathlib import Path

    path = next(
        Path(__file__).resolve().parents[2].glob("alembic/versions/*0005-work_categories.py")
    )
    spec = importlib.util.spec_from_file_location("_migration_0005_work_categories", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.WORK_CATEGORIES_SEED


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
        """История загрузок и замен — аудит: ЗАМЕНА старые jobs не удаляет (§5, §7.1).

        Удаление самого договора их уносит (v6.7) — это другой путь, здесь не он.
        """
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
#  proposals.vat_rate: ставка НДС из шапки ценового блока (фаза 7, спека Ф4б §2.9)
# ---------------------------------------------------------------------------

class TestProposalVatRate:
    """`CHECK` — запрет непредставимого состояния, а не основной фильтр (§2.9):

    импорт отсеивает негодные значения своей конверсией ДО вставки строки
    (`services.estimate_import._vat_rate`), а `CHECK` стережёт то, что прошло бы
    мимо импорта — прямую правку в psql.
    """

    def test_value_over_the_range_is_rejected_past_the_import(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        with rejected(db_session, contains="ck_proposals_vat_rate"):
            db_session.execute(
                sa.update(Proposal).where(Proposal.id == proposal.id).values(vat_rate=Decimal("101"))
            )

    def test_value_below_the_range_is_rejected_past_the_import(self, db_session, factories):
        """Пара к тесту выше: у выражения `CHECK` два арма, и проверка одного
        оставила бы второй без исполнителя — `>= 0` можно было бы выкинуть, не
        уронив ни одного теста. Найдено финальным ревью ветки."""
        proposal = factories.ProposalFactory.create()
        with rejected(db_session, contains="ck_proposals_vat_rate"):
            db_session.execute(
                sa.update(Proposal).where(Proposal.id == proposal.id).values(vat_rate=Decimal("-1"))
            )

    def test_declared_zero_is_a_representable_state(self, db_session, factories):
        """Ноль — законное значение колонки, а не «непредставимое состояние».

        `CHECK`, записанный как `> 0` вместо `>= 0`, отверг бы единственный
        способ сохранить заявленную нулевую ставку, и оба теста-запрета выше
        этого бы не заметили: они бьют по значениям ВНЕ диапазона.
        """
        proposal = factories.ProposalFactory.create()
        db_session.execute(
            sa.update(Proposal).where(Proposal.id == proposal.id).values(vat_rate=Decimal("0"))
        )
        db_session.flush()
        stored = db_session.execute(
            sa.select(Proposal.vat_rate).where(Proposal.id == proposal.id)
        ).scalar_one()
        assert stored == Decimal("0")


# ---------------------------------------------------------------------------
#  catalog_positions: идентичность = нормализованное название + единица
# ---------------------------------------------------------------------------

class TestCatalogIdentity:
    def test_same_normalized_pair_rejected(self, db_session, factories):
        factories.CatalogPositionFactory.create(
            standard_job_title="Кладка кирпича", normalized_job_title="кладка кирпич", unit_id=None
        )
        with rejected(db_session, contains="uq_catalog_positions_norm_hash_unit"):
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

        Арбитр — выражение индекса 0003: `sha256` нормализованного названия (с
        удвоением обратных слэшей перед приведением к `bytea`) и COALESCE(unit_id,-1).
        Если индекса нет или он объявлен иначе, PG ответит «no unique or exclusion
        constraint matching the ON CONFLICT specification».
        """
        factories.CatalogPositionFactory.create(
            standard_job_title="Монтаж",
            normalized_job_title="монтаж",
            unit_id=None,
            kind=CatalogKind.TO_REVIEW.value,
        )
        db_session.flush()

        insert_sql = sa.text(
            r"""
            INSERT INTO catalog_positions (standard_job_title, normalized_job_title, unit_id, kind)
            VALUES (:title, :norm, NULL, 'TO_REVIEW')
            ON CONFLICT (sha256(replace(normalized_job_title, '\', '\\')::bytea),
                         COALESCE(unit_id, -1)) DO NOTHING
            """
        )
        db_session.execute(insert_sql, {"title": "монтаж", "norm": "монтаж"})

        count = db_session.execute(
            sa.select(sa.func.count())
            .select_from(CatalogPosition)
            .where(CatalogPosition.normalized_job_title == "монтаж")
        ).scalar_one()
        assert count == 1

    def test_dead_title_index_is_gone(self, db_session):
        """`ix_catalog_positions_standard_job_title` удалён осознанно (0003).

        Поиск по каталогу — ILIKE '%…%', обычный btree его не обслуживает, зато
        ронял импорт длинных наименований. Проверка нужна потому, что пропажу
        индекса `alembic check` не заметит: этот раньше был в metadata, а теперь
        его нет ни там, ни в БД — тест фиксирует, что это решение, а не дрейф.
        """
        exists = db_session.execute(
            sa.text(
                "SELECT 1 FROM pg_indexes WHERE tablename = 'catalog_positions' "
                "AND indexname = 'ix_catalog_positions_standard_job_title'"
            )
        ).first()
        assert exists is None

    def test_long_normalized_title_is_accepted(self, db_session, factories):
        """Идентичность работы выдерживает название длиннее предела btree (0003)."""
        long_title = "щ" * 5000
        factories.CatalogPositionFactory.create(
            standard_job_title=long_title, normalized_job_title=long_title, unit_id=None
        )
        db_session.flush()

        with rejected(db_session, contains="uq_catalog_positions_norm_hash_unit"):
            factories.CatalogPositionFactory.create(
                standard_job_title=long_title + " копия",
                normalized_job_title=long_title,
                unit_id=None,
            )

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
#  app_settings: singleton и диапазон топ-N (миграция 0004, решение §6.2 фазы 6)
# ---------------------------------------------------------------------------

class TestAppSettings:
    """Ограничения таблицы настроек.

    Смысл решения §6.2 в том, что негодное значение **непредставимо в БД**, а не
    «проверяется в Python». Значит проверять надо именно отказ БД: если эти
    констрейнты исчезнут, вариант «ключ→значение», от которого §6.2 отказался,
    вернётся молча.
    """

    def test_migration_seeded_the_singleton_row(self, db_session):
        rows = db_session.execute(sa.select(AppSettings.id, AppSettings.passport_top_n)).all()
        assert rows == [(1, PASSPORT_TOP_N_DEFAULT)]

    def test_second_settings_row_rejected(self, db_session):
        """CHECK (id = 1): вторая строка настроек непредставима."""
        with rejected(db_session, contains="ck_app_settings_singleton"):
            db_session.execute(
                sa.insert(AppSettings).values(id=2, passport_top_n=PASSPORT_TOP_N_DEFAULT)
            )

    @pytest.mark.parametrize(
        "value", [PASSPORT_TOP_N_MIN - 1, PASSPORT_TOP_N_MAX + 1, 0, -5, 1000]
    )
    def test_out_of_range_top_n_rejected(self, db_session, value):
        """Диапазон держит БД — включая правку мимо приложения, прямо в psql."""
        with rejected(db_session, contains="ck_app_settings_passport_top_n"):
            db_session.execute(
                sa.update(AppSettings).where(AppSettings.id == 1).values(passport_top_n=value)
            )

    @pytest.mark.parametrize("value", [PASSPORT_TOP_N_MIN, PASSPORT_TOP_N_MAX])
    def test_range_boundaries_accepted(self, db_session, value):
        """Границы включительно: BETWEEN, а не строгое сравнение.

        Без этой пары предыдущий тест прошёл бы и на констрейнте, который
        запрещает вообще всё.
        """
        db_session.execute(
            sa.update(AppSettings).where(AppSettings.id == 1).values(passport_top_n=value)
        )
        db_session.flush()
        assert db_session.execute(sa.select(AppSettings.passport_top_n)).scalar_one() == value

    def test_migration_literals_match_model_constants(self):
        """Миграция 0004 обязана быть неизменной во времени, поэтому числа в ней —
        литералы, а не импорт из `models`. Цена — возможность разъехаться; этот тест
        её и закрывает."""
        import importlib.util
        from pathlib import Path

        path = next(
            Path(__file__).resolve().parents[2].glob("alembic/versions/*0004-app_settings.py")
        )
        spec = importlib.util.spec_from_file_location("_migration_0004", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module.PASSPORT_TOP_N_DEFAULT == PASSPORT_TOP_N_DEFAULT
        assert module.PASSPORT_TOP_N_MIN == PASSPORT_TOP_N_MIN
        assert module.PASSPORT_TOP_N_MAX == PASSPORT_TOP_N_MAX


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


# ---------------------------------------------------------------------------
#  work_categories: справочник статей классификатора работ (фаза 7, спека Ф1)
# ---------------------------------------------------------------------------

class TestWorkCategoriesSchema:
    """Инварианты справочника статей (спека Ф1 §2.1).

    Смысл — в непредставимости негодного состояния: справочник курируется людьми
    и будет правиться через админку, поэтому запрет живёт в БД, а не в Python.
    """

    def test_code_must_look_like_a_dotted_number(self, db_session):
        for bad in ("abc", "1..2", "1.", "", "6.6 ", ".1", "1x2", "1,2"):
            with rejected(db_session, contains="ck_work_categories_code"):
                db_session.execute(
                    sa.text(
                        "insert into work_categories (code, title, sort_order) "
                        "values (:code, 'x', 999000)"
                    ),
                    {"code": bad},
                )

    # Замеренные определения из PostgreSQL. Собирать их по памяти нельзя: функция
    # переформатирует выражение — добавляет `::text`, свои скобки и печатает LIKE
    # как оператор `~~`.
    DB_CHECKS = {
        "ck_work_categories_code": "CHECK ((code ~ '^[0-9]+([.][0-9]+)*$'::text))",
        "ck_work_categories_not_self_parent": "CHECK (((parent_id IS NULL) OR (parent_id <> id)))",
        "ck_work_categories_title_not_blank": (
            "CHECK ((btrim(title, ((((' '::text || chr(9)) || chr(10)) || chr(13)) || chr(160)))"
            " <> ''::text))"
        ),
    }
    DB_IS_BUCKET = "((code = '99'::text) OR (code ~~ '%.99'::text))"

    # Симметрично DB_CHECKS выше: та же тройка выражений, но со стороны ORM.
    ORM_CHECKS = {
        "ck_work_categories_code": "code ~ '^[0-9]+([.][0-9]+)*$'",
        "ck_work_categories_not_self_parent": "parent_id IS NULL OR parent_id <> id",
        "ck_work_categories_title_not_blank": (
            "btrim(title, ' ' || chr(9) || chr(10) || chr(13) || chr(160)) <> ''"
        ),
    }

    def test_database_holds_the_declared_expressions(self, db_session):
        """Что реально легло в БД: все три CHECK и generated-выражение.

        Сравнение словарём целиком, а не по одному ключу: так видно и подмену
        выражения, и появление лишнего CHECK, и исчезновение нужного. Канарейка
        против возврата экранирования (§2.1.1) — первая строка этого словаря.
        """
        rows = dict(
            db_session.execute(
                sa.text(
                    "select conname, pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid = 'work_categories'::regclass and contype = 'c'"
                )
            ).all()
        )
        assert rows == self.DB_CHECKS
        generated = db_session.execute(
            sa.text(
                "select generation_expression from information_schema.columns "
                "where table_name = 'work_categories' and column_name = 'is_bucket'"
            )
        ).scalar_one()
        assert generated == self.DB_IS_BUCKET

    def test_blank_title_rejected_the_same_way_on_any_locale(self, db_session):
        """Набор символов задан кодовыми точками, поэтому не зависит от LC_CTYPE."""
        for blank in ("", " ", "\t", "\n", "\r", "\xa0", " \t\xa0 "):
            with rejected(db_session, contains="ck_work_categories_title_not_blank"):
                db_session.execute(
                    sa.text(
                        "insert into work_categories (code, title, sort_order) "
                        "values ('900', :title, 999001)"
                    ),
                    {"title": blank},
                )

    def test_zero_width_space_title_is_an_accepted_boundary(self, db_session):
        """U+200B в набор не входит — граница явная и детерминированная (§2.1.2).

        Тест сторожит саму границу: если её решат закрыть, он покажет, что
        поведение изменилось осознанно.
        """
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "insert into work_categories (code, title, sort_order) "
                    "values ('901', :title, 999002)"
                ),
                {"title": "\u200b"},  # именно escape, а не невидимый символ в исходнике
            )

    def test_is_bucket_cannot_be_written(self, db_session):
        """generated column: ложь не отвергается, а невозможна.

        Класс ошибки — ProgrammingError (sqlstate 428C9), не IntegrityError,
        поэтому хелпер rejected() здесь не годится (спека §7 факт 7).
        """
        with pytest.raises(ProgrammingError, match="non-DEFAULT value"), db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "insert into work_categories (code, title, sort_order, is_bucket) "
                    "values ('902', 'x', 999003, true)"
                )
            )

    def test_is_bucket_cannot_be_updated(self, db_session):
        row_id = _make_category(db_session, "910", 999010)
        with pytest.raises(ProgrammingError, match="can only be updated to DEFAULT"), db_session.begin_nested():
            db_session.execute(
                sa.text("update work_categories set is_bucket = true where id = :id"),
                {"id": row_id},
            )

    def test_duplicate_code_rejected(self, db_session):
        _make_category(db_session, "911", 999011)
        with rejected(db_session, contains="uq_work_categories_code"):
            db_session.execute(
                sa.text(
                    "insert into work_categories (code, title, sort_order) "
                    "values ('911', 'дубль', 999012)"
                )
            )

    def test_duplicate_sort_order_rejected(self, db_session):
        _make_category(db_session, "912", 999013)
        with rejected(db_session, contains="uq_work_categories_sort_order"):
            db_session.execute(
                sa.text(
                    "insert into work_categories (code, title, sort_order) "
                    "values ('913', 'x', 999013)"
                )
            )

    def test_row_cannot_be_its_own_parent(self, db_session):
        row_id = _make_category(db_session, "914", 999014)
        with rejected(db_session, contains="ck_work_categories_not_self_parent"):
            db_session.execute(
                sa.text("update work_categories set parent_id = :id where id = :id"),
                {"id": row_id},
            )

    def test_parent_with_children_cannot_be_deleted(self, db_session):
        parent_id = _make_category(db_session, "915", 999015)
        _make_category(db_session, "915.1", 999016, parent_id=parent_id)
        with rejected(db_session, contains="work_categories_parent_id_fkey"):
            db_session.execute(
                sa.text("delete from work_categories where id = :id"), {"id": parent_id}
            )

    def test_orm_declares_the_same_expressions(self):
        """Вторая сторона парности: что объявлено в models.py.

        Замерено: `alembic check` расхождение CHECK- и Computed-выражений НЕ ловит —
        autogenerate их не сравнивает (на Computed выдаёт лишь UserWarning
        «cannot be modified», а предупреждение прогон не роняет). Поэтому ORM
        сверяется здесь, а БД — тестом выше; вместе они закрывают оба направления:
        правка в миграции ломает первый, правка в модели — второй.

        Сравнение словарём целиком (как DB_CHECKS/rows выше), а не по трём
        выбранным ключам: иначе лишний CHECK, объявленный только в модели, остался
        бы незамеченным.
        """
        checks = {
            c.name: str(c.sqltext)
            for c in WorkCategory.__table__.constraints
            if isinstance(c, CheckConstraint)
        }
        assert checks == self.ORM_CHECKS
        computed = WorkCategory.__table__.c.is_bucket.computed
        assert str(computed.sqltext) == "code = '99' OR code LIKE '%.99'"
        assert computed.persisted is True


class TestWorkCategoriesSeed:
    """Сид классификатора: 362 статьи шаблона и дерево, выведенное из кодов."""

    def test_whole_template_is_seeded(self, db_session):
        count = db_session.execute(sa.select(sa.func.count()).select_from(WorkCategory)).scalar_one()
        assert count == 362

    def test_roots_are_exactly_the_codes_without_a_dot(self, db_session):
        roots = db_session.execute(
            sa.text("select code from work_categories where parent_id is null")
        ).scalars().all()
        assert len(roots) == 21
        assert [c for c in roots if "." in c] == []

    def test_every_dotted_code_has_its_prefix_as_parent(self, db_session):
        """Страховка substring-выражения: искажение ломает свойство на 341 строке."""
        rows = db_session.execute(
            sa.text(
                "select c.code, p.code from work_categories c "
                "left join work_categories p on p.id = c.parent_id "
                "where c.code like '%.%'"
            )
        ).all()
        assert len(rows) == 341
        assert [(child, parent) for child, parent in rows if child.rsplit(".", 1)[0] != parent] == []

    def test_buckets_are_derived_from_the_code(self, db_session):
        buckets = db_session.execute(
            sa.text("select code from work_categories where is_bucket")
        ).scalars().all()
        assert len(buckets) == 22
        assert "99" in buckets
        assert [c for c in buckets if not (c == "99" or c.endswith(".99"))] == []

    def test_child_of_a_bucket_is_not_a_bucket(self, db_session):
        """11.99.2 — реальная работа под корзиной 11.99, а не корзина."""
        rows = dict(
            db_session.execute(
                sa.text(
                    "select code, is_bucket from work_categories "
                    "where code in ('11.99', '11.99.2')"
                )
            ).all()
        )
        assert rows == {"11.99": True, "11.99.2": False}

    def test_sort_order_follows_the_template(self, db_session):
        """Полный порядок кодов, а не только мультимножество значений и края.

        Сверка одних лишь значений sort_order (или только кодов '1'/'99' на
        краях) не ловит перестановку внутренних строк литерала: у переставленной
        пары получаются другие sort_order, но набор {10, 20, ..., 3620} и края
        шаблона остаются прежними. Здесь список кодов, упорядоченный по
        sort_order в БД, сравнивается с порядком самого литерала.
        """
        seed = _load_work_categories_seed()

        codes_by_sort_order = db_session.execute(
            sa.text("select code from work_categories order by sort_order")
        ).scalars().all()
        assert codes_by_sort_order == [code for code, _ in seed]

        orders = db_session.execute(
            sa.text("select sort_order from work_categories order by sort_order")
        ).scalars().all()
        assert orders == [(i + 1) * 10 for i in range(362)]

    def test_parent_always_precedes_child_and_roots_ascend_by_number(self, db_session):
        """Два свойства порядка, проверяемых без литерала и без шаблона.

        `test_sort_order_follows_the_template` выше сверяет порядок из БД с
        порядком самого литерала `WORK_CATEGORIES_SEED`: литерал там одновременно
        и эталон, и предмет проверки, поэтому перестановка строк внутри него
        двигает обе стороны сравнения и остаётся незамеченной. Здесь те же два
        свойства утверждаются независимо от литерала и от шаблона `Шаблон.xlsx`
        (который не коммитится) — читаем только содержимое `work_categories`.

        1. Топологичность (спека §1: «родитель всегда встречается раньше
           ребёнка»): для каждого кода с точкой позиция родителя (префикс до
           последней точки) в списке, упорядоченном по sort_order, должна быть
           меньше позиции самого кода. Ожидание — пустой список нарушителей.
        2. Корни (коды без точки, включая '99') идут по возрастанию номера.

        Остаточный предел: эти два инварианта не ловят перестановку двух
        сиблингов внутри одного уровня — в самом шаблоне такие инверсии есть
        (замерено: 10.2.6 идёт раньше 10.2.5, а 10.7.5 раньше 10.7.4), поэтому
        требовать от порядка шаблона полной числовой сортировки нельзя.
        """
        codes = db_session.execute(
            sa.text("select code from work_categories order by sort_order")
        ).scalars().all()
        position = {code: i for i, code in enumerate(codes)}

        violators = [
            code
            for code in codes
            if "." in code and position[code.rsplit(".", 1)[0]] >= position[code]
        ]
        assert violators == []

        roots = [code for code in codes if "." not in code]
        assert roots == sorted(roots, key=int)

    def test_titles_come_from_the_template_as_is(self, db_session):
        title = db_session.execute(
            sa.text("select title from work_categories where code = '1'")
        ).scalar_one()
        assert title == "Подготовительные работы, содержание площадки"


# ---------------------------------------------------------------------------
#  position_items: поля статьи и составной self-FK (фаза 7, спека Ф3, миграция 0006)
# ---------------------------------------------------------------------------

class TestPositionItemCategoryColumns:
    """Миграция 0006: поля статьи и составной self-FK (спека Ф3 §2.1)."""

    @staticmethod
    def _row(db_session, factories, proposal, *, is_chapter: bool):
        item = factories.PositionItemFactory.create(proposal=proposal, is_chapter=is_chapter)
        db_session.flush()
        return item

    @staticmethod
    def _any_category_id(db_session) -> int:
        """Любая статья справочника, но обязательно ЛИСТ дерева.

        Первая по `sort_order` — корень «1», и у него есть дети: его удаление
        упирается в `work_categories_parent_id_fkey` из Ф1 РАНЬШЕ, чем дойдёт до
        `fk_position_items_work_category_id`. Тест удаления получил бы отказ не от
        того констрейнта, который проверяет, — поймало это только сравнение по
        имени констрейнта, «просто IntegrityError» прошёл бы зелёным.
        """
        used_as_parent = sa.select(WorkCategory.parent_id).where(
            WorkCategory.parent_id.is_not(None)
        )
        return db_session.execute(
            sa.select(WorkCategory.id)
            .where(WorkCategory.id.not_in(used_as_parent))
            .order_by(WorkCategory.sort_order)
            .limit(1)
        ).scalar_one()

    @pytest.mark.parametrize("field_set", ["raw_only", "category_and_source"])
    def test_article_fields_are_rejected_on_a_non_chapter_row(
        self, db_session, factories, field_set
    ):
        """Оба способа заполнить статью у не-раздела, а не только сырое значение.

        `alembic check` CHECK-выражения не сравнивает вовсе (замерено на Ф1: при
        подмене autogenerate отдаёт пустой diff — комментарий к `db-test-check` в
        justfile), поэтому смысл констрейнта держат только эти parity-тесты.
        """
        proposal = factories.ProposalFactory.create()
        item = self._row(db_session, factories, proposal, is_chapter=False)
        values = (
            {"smr_article_raw": "4.1. Ж/Б конструкции"}
            if field_set == "raw_only"
            else {
                "work_category_id": self._any_category_id(db_session),
                "category_source": "file",
            }
        )
        with rejected(db_session, contains="ck_position_items_article_only_on_chapters"):
            db_session.execute(
                sa.update(PositionItem).where(PositionItem.id == item.id).values(**values)
            )

    @pytest.mark.parametrize("missing", ["source", "category"])
    def test_category_and_source_come_only_together(self, db_session, factories, missing):
        """Парность в ОБЕ стороны: и категория без источника, и источник без категории."""
        proposal = factories.ProposalFactory.create()
        item = self._row(db_session, factories, proposal, is_chapter=True)
        values = (
            {"work_category_id": self._any_category_id(db_session)}
            if missing == "source"
            else {"category_source": "file"}
        )
        with rejected(db_session, contains="ck_position_items_category_source_pairs"):
            db_session.execute(
                sa.update(PositionItem).where(PositionItem.id == item.id).values(**values)
            )

    def test_source_other_than_file_or_manual_is_rejected(self, db_session, factories):
        """Миграция 0011 расширила допустимые значения до ('file','manual') —
        третье значение (не 'file' и не 'manual') остаётся непредставимым."""
        proposal = factories.ProposalFactory.create()
        item = self._row(db_session, factories, proposal, is_chapter=True)
        with rejected(db_session, contains="ck_position_items_category_source"):
            db_session.execute(
                sa.update(PositionItem)
                .where(PositionItem.id == item.id)
                .values(
                    work_category_id=self._any_category_id(db_session),
                    category_source="guess",
                )
            )

    def test_cross_proposal_reference_is_rejected_by_the_composite_fk(self, db_session, factories):
        """Прямая попытка записи, а не результат импорта.

        Импортёр такую ссылку не построит и при СНЯТОМ констрейнте (карта
        key -> PositionItem живёт один вызов на один proposal), поэтому проверка
        через импорт стерегла бы построение, а не FK (спека §4.2).
        """
        chapter = self._row(
            db_session, factories, factories.ProposalFactory.create(), is_chapter=True
        )
        alien = self._row(
            db_session, factories, factories.ProposalFactory.create(), is_chapter=False
        )
        with rejected(db_session, contains="fk_position_items_chapter"):
            db_session.execute(
                sa.update(PositionItem)
                .where(PositionItem.id == alien.id)
                .values(chapter_item_id=chapter.id)
            )

    def test_reference_inside_the_same_proposal_is_accepted(self, db_session, factories):
        """Значение читается ИЗ БД, а не через identity map.

        `db_session.get()` здесь вернул бы `None` при верно записанной строке:
        синхронизация bulk-UPDATE в сессию патчит только те атрибуты, которые уже
        лежат в `__dict__` объекта, а `chapter_item_id` фабрика не заполняет — и
        объект не истёк, поэтому в БД повторного запроса не будет. Тест сравнивал
        бы питоновский `None` с самим собой (замерено пробником: в БД лежит id
        раздела, `get()` отдаёт `None`).
        """
        proposal = factories.ProposalFactory.create()
        chapter = self._row(db_session, factories, proposal, is_chapter=True)
        child = self._row(db_session, factories, proposal, is_chapter=False)
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == child.id)
            .values(chapter_item_id=chapter.id)
        )
        db_session.flush()
        stored = db_session.execute(
            sa.select(PositionItem.chapter_item_id).where(PositionItem.id == child.id)
        ).scalar_one()
        assert stored == chapter.id

    def test_null_parent_is_allowed(self, db_session, factories):
        """MATCH SIMPLE: NULL во второй колонке пропускает проверку FK (спека §1.3 факт 8)."""
        item = self._row(
            db_session, factories, factories.ProposalFactory.create(), is_chapter=True
        )
        assert db_session.get(PositionItem, item.id).chapter_item_id is None

    def test_deleting_a_referenced_chapter_row_hits_restrict(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        chapter = self._row(db_session, factories, proposal, is_chapter=True)
        child = self._row(db_session, factories, proposal, is_chapter=False)
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == child.id)
            .values(chapter_item_id=chapter.id)
        )
        db_session.flush()
        with rejected(db_session, contains="fk_position_items_chapter"):
            db_session.execute(sa.delete(PositionItem).where(PositionItem.id == chapter.id))

    def test_deleting_a_used_work_category_hits_restrict(self, db_session, factories):
        item = self._row(
            db_session, factories, factories.ProposalFactory.create(), is_chapter=True
        )
        category_id = self._any_category_id(db_session)
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == item.id)
            .values(work_category_id=category_id, category_source="file")
        )
        db_session.flush()
        with rejected(db_session, contains="fk_position_items_work_category_id"):
            db_session.execute(sa.delete(WorkCategory).where(WorkCategory.id == category_id))


# ---------------------------------------------------------------------------
#  estimate_additional_works: расшивка «Сведений по дополнительным работам» по
#  статьям (фаза 7, спека Ф4, миграция 0007)
# ---------------------------------------------------------------------------

class TestAdditionalWorksSchema:
    """Пять CHECK, два именованных FK, UNIQUE и частичный индекс (спека Ф4 §2.3).

    Таблица висит на `proposal_id`, а не на `estimate_id` (отступление от брифа,
    спека §2.3): агрегатная строка принадлежит предложению, и резолв ссылки в
    статью определён в его же пределах (§2.5). Отдельного индекса по
    `proposal_id` нет намеренно — его обслуживает левый префикс
    `uq_estimate_additional_works_proposal_ordinal`.
    """

    @staticmethod
    def _any_category_id(db_session) -> int:
        """Любая статья справочника, но обязательно ЛИСТ дерева.

        Первая по `sort_order` — корень «1», и у него есть дети: его удаление
        упирается в `work_categories_parent_id_fkey` РАНЬШЕ, чем дойдёт до
        `fk_estimate_additional_works_work_category_id` (тот же урок Ф3, что и у
        `TestPositionItemCategoryColumns._any_category_id`).
        """
        used_as_parent = sa.select(WorkCategory.parent_id).where(
            WorkCategory.parent_id.is_not(None)
        )
        return db_session.execute(
            sa.select(WorkCategory.id)
            .where(WorkCategory.id.not_in(used_as_parent))
            .order_by(WorkCategory.sort_order)
            .limit(1)
        ).scalar_one()

    @staticmethod
    def _insert_row(
        db_session,
        proposal_id: int,
        *,
        ordinal: int = 1,
        chapter_ref_raw: str | None = None,
        title: str = "Допработа",
        total_amount: Decimal = Decimal("100"),
        work_category_id: int | None = None,
        raw_line: str | None = None,
    ) -> int:
        """INSERT сырым SQL, именованные параметры — модель ORM в шаге 1 ещё не
        существует, и красный прогон обязан упасть на отсутствующем отношении,
        а не на ImportError."""
        return db_session.execute(
            sa.text(
                "insert into estimate_additional_works "
                "(proposal_id, ordinal, chapter_ref_raw, title, total_amount, "
                " work_category_id, raw_line) "
                "values (:proposal_id, :ordinal, :chapter_ref_raw, :title, :total_amount, "
                "        :work_category_id, :raw_line) "
                "returning id"
            ),
            {
                "proposal_id": proposal_id,
                "ordinal": ordinal,
                "chapter_ref_raw": chapter_ref_raw,
                "title": title,
                "total_amount": total_amount,
                "work_category_id": work_category_id,
                "raw_line": raw_line,
            },
        ).scalar_one()

    def test_negative_amount_is_rejected(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        db_session.flush()
        with rejected(db_session, contains="ck_estimate_additional_works_total_amount"):
            self._insert_row(db_session, proposal.id, total_amount=Decimal("-1"))

    def test_zero_amount_is_accepted(self, db_session, factories):
        """Реальный файл несёт такую строку (спека §1.2) — `>= 0` обязан её пропускать."""
        proposal = factories.ProposalFactory.create()
        db_session.flush()
        self._insert_row(db_session, proposal.id, total_amount=Decimal("0"))
        db_session.flush()

    @pytest.mark.parametrize("ordinal", [0, -1])
    def test_zero_and_negative_ordinal_are_rejected(self, db_session, factories, ordinal):
        proposal = factories.ProposalFactory.create()
        db_session.flush()
        with rejected(db_session, contains="ck_estimate_additional_works_ordinal"):
            self._insert_row(db_session, proposal.id, ordinal=ordinal)

    @pytest.mark.parametrize("title", ["", "   ", "\u00a0"])
    def test_blank_title_is_rejected(self, db_session, factories, title):
        """Третий случай — U+00A0 как escape (не невидимый литерал в исходнике):
        именно его закрывает `chr(160)` в наборе символов CHECK'а."""
        proposal = factories.ProposalFactory.create()
        db_session.flush()
        with rejected(db_session, contains="ck_estimate_additional_works_title_not_blank"):
            self._insert_row(db_session, proposal.id, title=title)

    def test_category_without_ref_is_rejected(self, db_session, factories):
        """Статья без ссылки, из которой она получена, — ложь о происхождении:
        единственный источник статьи в v1 — ссылка (спека §2.3)."""
        proposal = factories.ProposalFactory.create()
        db_session.flush()
        category_id = self._any_category_id(db_session)
        with rejected(db_session, contains="ck_estimate_additional_works_unresolved_ref"):
            self._insert_row(
                db_session,
                proposal.id,
                chapter_ref_raw=None,
                work_category_id=category_id,
                raw_line="3.2.2 Работа — 100 руб.",
            )

    @pytest.mark.parametrize("with_category", [True, False])
    def test_ref_without_raw_line_is_rejected(self, db_session, factories, with_category):
        """Ссылка (со статьёй или без) без исходной строки текста — привязка,
        происхождение которой нечем проверить (спека §2.3). Оба случая упираются
        в один и тот же CHECK."""
        proposal = factories.ProposalFactory.create()
        db_session.flush()
        work_category_id = self._any_category_id(db_session) if with_category else None
        with rejected(db_session, contains="ck_estimate_additional_works_raw_line_pairs"):
            self._insert_row(
                db_session,
                proposal.id,
                chapter_ref_raw="3.2.2",
                work_category_id=work_category_id,
                raw_line=None,
            )

    def test_duplicate_ordinal_in_one_proposal_is_rejected(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        db_session.flush()
        self._insert_row(db_session, proposal.id, ordinal=1)
        db_session.flush()
        with rejected(db_session, contains="uq_estimate_additional_works_proposal_ordinal"):
            self._insert_row(db_session, proposal.id, ordinal=1)

    def test_same_ordinal_in_another_proposal_is_allowed(self, db_session, factories):
        proposal_a = factories.ProposalFactory.create()
        proposal_b = factories.ProposalFactory.create()
        db_session.flush()
        self._insert_row(db_session, proposal_a.id, ordinal=1)
        self._insert_row(db_session, proposal_b.id, ordinal=1)
        db_session.flush()

    def test_deleting_a_used_category_hits_restrict(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        db_session.flush()
        category_id = self._any_category_id(db_session)
        self._insert_row(
            db_session,
            proposal.id,
            chapter_ref_raw="3.2.2",
            work_category_id=category_id,
            raw_line="3.2.2 Работа — 100 руб.",
        )
        db_session.flush()
        with rejected(db_session, contains="fk_estimate_additional_works_work_category_id"):
            db_session.execute(sa.delete(WorkCategory).where(WorkCategory.id == category_id))

    def test_deleting_the_proposal_cascades_records(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        db_session.flush()
        self._insert_row(db_session, proposal.id, ordinal=1)
        db_session.flush()

        db_session.execute(sa.delete(Proposal).where(Proposal.id == proposal.id))
        remaining = db_session.execute(
            sa.text("select count(*) from estimate_additional_works where proposal_id = :id"),
            {"id": proposal.id},
        ).scalar_one()
        assert remaining == 0

    # Замеренные определения из PostgreSQL (см. TestWorkCategoriesSchema выше —
    # тот же довод: собирать их по памяти нельзя, функция переформатирует
    # выражение и добавляет свои `::text`/скобки).
    DB_CHECKS = {
        "ck_estimate_additional_works_ordinal": "CHECK ((ordinal > 0))",
        "ck_estimate_additional_works_raw_line_pairs": (
            "CHECK (((raw_line IS NOT NULL) OR ((chapter_ref_raw IS NULL)"
            " AND (work_category_id IS NULL))))"
        ),
        "ck_estimate_additional_works_title_not_blank": (
            "CHECK ((btrim(title, ((((' '::text || chr(9)) || chr(10)) || chr(13)) || chr(160)))"
            " <> ''::text))"
        ),
        "ck_estimate_additional_works_total_amount": "CHECK ((total_amount >= (0)::numeric))",
        "ck_estimate_additional_works_unresolved_ref": (
            "CHECK (((chapter_ref_raw IS NOT NULL) OR (work_category_id IS NULL)))"
        ),
    }

    # Симметрично DB_CHECKS выше: та же пятёрка выражений, но со стороны ORM.
    ORM_CHECKS = {
        "ck_estimate_additional_works_total_amount": "total_amount >= 0",
        "ck_estimate_additional_works_ordinal": "ordinal > 0",
        "ck_estimate_additional_works_title_not_blank": (
            "btrim(title, ' ' || chr(9) || chr(10) || chr(13) || chr(160)) <> ''"
        ),
        "ck_estimate_additional_works_unresolved_ref": (
            "chapter_ref_raw IS NOT NULL OR work_category_id IS NULL"
        ),
        "ck_estimate_additional_works_raw_line_pairs": (
            "raw_line IS NOT NULL OR (chapter_ref_raw IS NULL AND work_category_id IS NULL)"
        ),
    }

    def test_database_holds_the_declared_expressions(self, db_session):
        """Что реально легло в БД: все пять CHECK, сравнение словарём целиком —
        так видно и подмену выражения, и появление лишнего CHECK, и исчезновение
        нужного (см. TestWorkCategoriesSchema.test_database_holds_the_declared_expressions;
        `alembic check` CHECK-выражения не сравнивает вовсе — замерено на Ф1)."""
        rows = dict(
            db_session.execute(
                sa.text(
                    "select conname, pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid = 'estimate_additional_works'::regclass and contype = 'c'"
                )
            ).all()
        )
        assert rows == self.DB_CHECKS

    def test_orm_declares_the_same_expressions(self):
        """Вторая сторона парности: что объявлено в models.py.

        Гейт против дрейфа между миграцией 0007 и ORM: `alembic check` этого не
        ловит (замер Ф1), поэтому расхождение обязана поймать эта пара тестов —
        один по БД (выше), один по declarative-модели (здесь).
        """
        checks = {
            c.name: str(c.sqltext)
            for c in EstimateAdditionalWork.__table__.constraints
            if isinstance(c, CheckConstraint)
        }
        assert checks == self.ORM_CHECKS


# ---------------------------------------------------------------------------
#  objects.area_*_sp, contracts.*_pct/*_note: ТЭП объекта и коммерческие
#  условия договора (фаза 7, спека Ф5, миграция 0009)
# ---------------------------------------------------------------------------

#: Общий счётчик для уникальных title/contract_number/inn ниже — тот же приём,
#: что у `factory.Sequence` в tests/factories.py, но без регистрации сессии в
#: фабриках: тесты этого раздела получают только `db_session`, как в
#: буквальном коде плана, а не `factories`.
_tep_seq = itertools.count()


def _make_object(
    session,
    *,
    title: str | None = None,
    address: str = "Test St, 1",
    above: str | None = None,
    under: str | None = None,
) -> int:
    """INSERT сырым SQL, как `_make_category`: на шаге 2 плана колонки площадей
    проверяются раньше, чем модель их объявит (шаг 5).

    Title/address — ASCII нарочно (не как в остальном файле): CHECK-нарушение
    печатает в DETAIL всю строку целиком, и без этого кириллица утекала бы в
    терминал через `IntegrityError` (запрет на печать кириллицы в терминал).
    """
    return session.execute(
        sa.text(
            "insert into objects (title, address, area_aboveground_sp, area_underground_sp) "
            "values (:title, :address, :above, :under) returning id"
        ),
        {
            "title": title or f"TEP object {next(_tep_seq)}",
            "address": address,
            "above": Decimal(above) if above is not None else None,
            "under": Decimal(under) if under is not None else None,
        },
    ).scalar_one()


def _make_object_and_read(session, **kwargs):
    """Как `_make_object`, но сразу читает обратно три поля площадей."""
    object_id = _make_object(session, **kwargs)
    return session.execute(
        sa.text(
            "select area_aboveground_sp, area_underground_sp, area_total_sp "
            "from objects where id = :id"
        ),
        {"id": object_id},
    ).one()


#: Прогрев формы запроса перед нарушающим исполнением: строго больше пяти.
_USEFUL_WARMUP_EXECUTIONS = 8


def _make_object_with_useful(
    session,
    *,
    above: str | None,
    under: str | None,
    useful: str | None,
    title: str | None = None,
    address: str = "Test St, 1",
) -> int:
    """INSERT со ВСЕМИ ТРЕМЯ площадями — отдельная форма запроса.

    Отдельный хелпер, а не расширение `_make_object`: форма запроса у прогрева
    и у нарушающего исполнения обязана совпадать (`prepare_threshold = 5`
    считает ПОВТОРНЫЕ ИСПОЛНЕНИЯ одной формы, а не строки в одном INSERT —
    [batch-larger-than-five](../../../docs/insights/batch-larger-than-five.md)),
    а существующие тесты площадей остаются на своей форме и не краснеют заодно.

    ASCII в title/address — тот же довод, что в докстринге `_make_object`.
    """
    return session.execute(
        sa.text(
            "insert into objects "
            "(title, address, area_aboveground_sp, area_underground_sp, area_useful_sp) "
            "values (:title, :address, :above, :under, :useful) returning id"
        ),
        {
            "title": title or f"TEP useful object {next(_tep_seq)}",
            "address": address,
            "above": Decimal(above) if above is not None else None,
            "under": Decimal(under) if under is not None else None,
            "useful": Decimal(useful) if useful is not None else None,
        },
    ).scalar_one()


def _make_object_with_useful_and_read(session, **kwargs):
    """Как `_make_object_with_useful`, но сразу читает обратно четыре площади."""
    object_id = _make_object_with_useful(session, **kwargs)
    return session.execute(
        sa.text(
            "select area_aboveground_sp, area_underground_sp, area_total_sp, area_useful_sp "
            "from objects where id = :id"
        ),
        {"id": object_id},
    ).one()


def _warm_up_useful_insert_form(session) -> None:
    """Восемь ВАЛИДНЫХ исполнений той же формы, что и нарушающее следом.

    Порог подготовки запросов psycopg3 — пять; до него у PostgreSQL другой план,
    и класс дефектов, живущий за порогом, на одном исполнении не виден. Считаются
    именно повторные исполнения: один `INSERT` с десятью строками порога НЕ
    достигает.
    """
    for i in range(_USEFUL_WARMUP_EXECUTIONS):
        _make_object_with_useful(session, above="100", under=str(i), useful="50")


def _make_rate_class_id(session) -> int:
    """ASCII нарочно — см. довод в докстринге `_make_object`."""
    return session.execute(
        sa.text("insert into rate_classes (title) values (:title) returning id"),
        {"title": f"TEP class {next(_tep_seq)}"},
    ).scalar_one()


def _make_contractor_id(session) -> int:
    """Реквизиты — тот же приём, что у `ContractorFactory` (tests/factories.py);
    ASCII нарочно — см. довод в докстринге `_make_object`."""
    return session.execute(
        sa.text(
            "insert into contractors (title, inn, address, accreditation) "
            "values (:title, :inn, 'Test city, Contractor St, 1', 'yes') returning id"
        ),
        {
            "title": f"TEP contractor {next(_tep_seq)}",
            "inn": f"77{next(_tep_seq):010d}",
        },
    ).scalar_one()


def _make_contract(
    session,
    *,
    advance_pct: str | None = None,
    advance_note: str | None = None,
    bank_guarantee_pct: str | None = None,
    bank_guarantee_note: str | None = None,
    retention_pct: str | None = None,
    retention_note: str | None = None,
) -> int:
    """INSERT сырым SQL, как `_make_object`: колонки условий проверяются раньше,
    чем модель их объявит. Объект/подрядчик/класс — минимальные строки без ТЭП,
    реквизиты — тот же приём, что у `ContractFactory`. `contract_number` — ASCII
    нарочно, тот же довод, что у `_make_object`; `*_note` остаются кириллицей
    буквально по плану (домен — комментарий человеком, не служебный идентификатор,
    и участвует только в путях без ожидаемого отказа БД)."""
    object_id = _make_object(session)
    contractor_id = _make_contractor_id(session)
    rate_class_id = _make_rate_class_id(session)
    return session.execute(
        sa.text(
            "insert into contracts "
            "(object_id, contractor_id, rate_class_id, contract_number, signed_date, "
            " advance_pct, advance_note, bank_guarantee_pct, bank_guarantee_note, "
            " retention_pct, retention_note) "
            "values (:object_id, :contractor_id, :rate_class_id, :contract_number, :signed_date, "
            "        :advance_pct, :advance_note, :bank_guarantee_pct, :bank_guarantee_note, "
            "        :retention_pct, :retention_note) "
            "returning id"
        ),
        {
            "object_id": object_id,
            "contractor_id": contractor_id,
            "rate_class_id": rate_class_id,
            "contract_number": f"TEP-GP-{next(_tep_seq):06d}",
            "signed_date": dt.date(2025, 3, 1),
            "advance_pct": Decimal(advance_pct) if advance_pct is not None else None,
            "advance_note": advance_note,
            "bank_guarantee_pct": (
                Decimal(bank_guarantee_pct) if bank_guarantee_pct is not None else None
            ),
            "bank_guarantee_note": bank_guarantee_note,
            "retention_pct": Decimal(retention_pct) if retention_pct is not None else None,
            "retention_note": retention_note,
        },
    ).scalar_one()


def _make_contract_and_read(session, **kwargs):
    """Как `_make_contract`, но сразу читает обратно шесть полей условий."""
    contract_id = _make_contract(session, **kwargs)
    return session.execute(
        sa.text(
            "select advance_pct, advance_note, bank_guarantee_pct, bank_guarantee_note, "
            "       retention_pct, retention_note "
            "from contracts where id = :id"
        ),
        {"id": contract_id},
    ).one()


class TestObjectAreas:
    """ТЭП объекта: две вводимые площади, третья вычисляемая (спека §2.2, §2.3)."""

    def test_negative_aboveground_is_rejected(self, db_session):
        """Партнёр — `10`, а НЕ `0`: вход обязан нарушать ровно один CHECK.

        Замерено (пробник задачи 1): пара `(-1, 0)` даёт общую `-1` и нарушает
        сразу и этот арм, и `ck_objects_area_total_sp_positive`; PostgreSQL
        называет один констрейнт из нескольких нарушенных, выбирая по имени.
        Тогда `total_sp_positive` **маскировал** бы этот арм — снятие арма не
        пустило бы значение в таблицу, и негативная проверка №1 реестра §4.4
        доказывала бы не то ([verifying-guards](../../../docs/insights/verifying-guards.md),
        слой 8). При общей `9` нарушение остаётся одно, и снятие даёт честный
        третий исход — значение ложится в таблицу.
        """
        with rejected(db_session, contains="ck_objects_area_aboveground_sp_non_negative"):
            _make_object(db_session, above="-1", under="10")

    def test_negative_underground_is_rejected(self, db_session):
        """Партнёр — `10`, а НЕ `0`, по тому же доводу, что у наземной выше.

        Здесь маскировка не гипотетическая: на паре `(0, -1)` PostgreSQL
        называет `ck_objects_area_total_sp_positive`, и тест с ожиданием
        подземного арма падал бы всегда.
        """
        with rejected(db_session, contains="ck_objects_area_underground_sp_non_negative"):
            _make_object(db_session, above="10", under="-1")

    def test_both_zero_is_rejected_because_total_would_be_zero(self, db_session):
        """Ноль в частях законен, ноль в общей — нет: она знаменатель."""
        with rejected(db_session, contains="ck_objects_area_total_sp_positive"):
            _make_object(db_session, above="0", under="0")

    def test_only_aboveground_is_rejected(self, db_session):
        with rejected(db_session, contains="ck_objects_areas_both_or_neither"):
            _make_object(db_session, above="100", under=None)

    def test_only_underground_is_rejected(self, db_session):
        with rejected(db_session, contains="ck_objects_areas_both_or_neither"):
            _make_object(db_session, above=None, under="100")

    def test_zero_part_is_a_representable_state(self, db_session):
        """Объект без подземной части: ноль, а не NULL."""
        row = _make_object_and_read(db_session, above="100.50", under="0")
        assert row.area_underground_sp == Decimal("0")
        assert row.area_underground_sp is not None

    def test_no_areas_at_all_is_a_representable_state(self, db_session):
        row = _make_object_and_read(db_session, above=None, under=None)
        assert row.area_total_sp is None

    def test_total_is_computed_from_the_parts(self, db_session):
        row = _make_object_and_read(db_session, above="62399.70", under="13341.30")
        assert row.area_total_sp == Decimal("75741.00")

    def test_total_cannot_be_written_directly(self, db_session):
        """Вычисляемая колонка не принимает значение — иначе она хранимая.

        Тип и SQLSTATE названы точно: `pytest.raises(Exception)` прошёл бы и от
        опечатки в SQL, и от уже отравленной сессии — то есть доказывал бы не то.

        Savepoint обязателен и своим хелпером: существующий `rejected` ловит
        только `IntegrityError`, а здесь PostgreSQL отвечает `ProgrammingError`,
        и без `begin_nested` сессия осталась бы непригодной для остальных
        тестов файла.
        """
        object_id = _make_object(db_session, above="1", under="1")
        with pytest.raises(ProgrammingError) as exc, db_session.begin_nested():
            db_session.execute(
                sa.text("update objects set area_total_sp = 1 where id = :id"),
                {"id": object_id},
            )
        # 428C9 = ERRCODE_GENERATED_ALWAYS. Значение ПОДТВЕРЖДЕНО пробником шага 6
        # плана (orchestrator, на gca_test): попытка записи в GENERATED ALWAYS ...
        # STORED отвергается именно этим SQLSTATE. Если фактический SQLSTATE
        # окажется иным — исправить константу здесь, а не расширять проверку
        # обратно до «любой ошибки».
        assert exc.value.orig.sqlstate == "428C9"


class TestObjectUsefulArea:
    """Полезная площадь объекта (спека 2026-08-15 §2.1–§2.4, миграция 0013).

    Полезная — ЧАСТЬ общей, а не третье слагаемое: `area_total_sp` не трогается
    (§2.2). Ограничений два, и они разные по причине: неотрицательность —
    самодостаточная, «не больше суммы слагаемых» — про отношение к паре.

    `CHECK` записан через СЛАГАЕМЫЕ, а не через `area_total_sp` (§2.3): тот же
    результат без зависимости от того, разрешает ли PostgreSQL ссылку на
    генерируемую колонку в `CHECK` другой колонки.

    Вход каждого негативного теста нарушает РОВНО ОДНО ограничение — иначе
    маскировку создавал бы сам выбор входа
    ([verifying-guards](../../../docs/insights/verifying-guards.md), слой 8).
    """

    def test_useful_greater_than_the_sum_of_parts_is_rejected(self, db_session):
        """Нарушение ровно одно: `100.01 >= 0` истинно, общая `100 > 0` тоже."""
        _warm_up_useful_insert_form(db_session)
        with rejected(db_session, contains="ck_objects_area_useful_sp_within_total"):
            _make_object_with_useful(db_session, above="60", under="40", useful="100.01")

    def test_negative_useful_is_rejected(self, db_session):
        """Партнёр — пара `(100, 0)`: общая `100 > 0`, и `-1 <= 100` истинно,
        поэтому `within_total` этот арм не маскирует."""
        _warm_up_useful_insert_form(db_session)
        with rejected(db_session, contains="ck_objects_area_useful_sp_non_negative"):
            _make_object_with_useful(db_session, above="100", under="0", useful="-1")

    def test_useful_equal_to_the_sum_is_accepted(self, db_session):
        """Граница ВКЛЮЧЕНА: полезная, равная общей, — законное состояние."""
        row = _make_object_with_useful_and_read(
            db_session, above="60", under="40", useful="100"
        )
        assert row.area_useful_sp == Decimal("100")
        assert row.area_total_sp == Decimal("100")

    def test_useful_without_the_pair_is_accepted(self, db_session):
        """Граница §2.4 спеки, закрепляется НАМЕРЕННО, чтобы её не ужесточили
        мимоходом: полезная не связана парой с надземной и подземной, а при
        `NULL`-паре сумма слагаемых `NULL`, `CHECK` даёт `NULL` и пропускает.
        Данные приходят кусками — это принятая цена, а не дефект.
        """
        row = _make_object_with_useful_and_read(
            db_session, above=None, under=None, useful="80"
        )
        assert row.area_useful_sp == Decimal("80")
        assert row.area_total_sp is None

    def test_useful_null_is_accepted(self, db_session):
        row = _make_object_with_useful_and_read(
            db_session, above="60", under="40", useful=None
        )
        assert row.area_useful_sp is None


class TestContractCommercialTerms:
    """Коммерческие условия договора: три пары «процент + комментарий» (спека §2.5)."""

    @pytest.mark.parametrize(
        "field", ["advance_pct", "bank_guarantee_pct", "retention_pct"]
    )
    @pytest.mark.parametrize("value", ["-1", "101"])
    def test_percent_outside_the_range_is_rejected(self, db_session, field, value):
        with rejected(db_session, contains=f"ck_contracts_{field}_range"):
            _make_contract(db_session, **{field: value})

    @pytest.mark.parametrize(
        "field", ["advance_pct", "bank_guarantee_pct", "retention_pct"]
    )
    def test_declared_zero_percent_is_representable(self, db_session, field):
        row = _make_contract_and_read(db_session, **{field: "0"})
        assert getattr(row, field) == Decimal("0")

    def test_note_without_percent_is_allowed(self, db_session):
        """Условие есть, но одним процентом не выражается (спека §2.5 п. 1)."""
        row = _make_contract_and_read(db_session, advance_note="траншами по графику")
        assert row.advance_pct is None
        assert row.advance_note is not None


class TestAreasAndTermsParity:
    """Парность объявлений задачи 1 миграции 0009 (спека §2.2, §2.3, §2.5, §2.8):
    миграция дублирует models.py намеренно, и `alembic check` расхождение CHECK-
    и Computed-выражений не ловит (спека §1.5 п. 4, §5 п. 6). Держат его эти два
    теста — тот же паттерн, что у `TestWorkCategoriesSchema` выше: один сверяет
    БД, другой — declarative-модель.

    Замеренные определения из PostgreSQL (DB_OBJECT_CHECKS,
    DB_OBJECT_AREA_TOTAL_GENERATED, DB_CONTRACT_CHECKS) — собраны запросом ниже
    на gca_test СРАЗУ после наката миграции 0009 (`db_engine` в conftest.py
    гоняет `command.upgrade(cfg, "head")` перед первым тестом сессии), а не по
    памяти: PostgreSQL переформатирует выражение — добавляет `::numeric`, свои
    скобки (см. тот же довод у `TestWorkCategoriesSchema.DB_CHECKS` выше).

        select conname, pg_get_constraintdef(oid) from pg_constraint
        where conrelid = 'objects'::regclass and contype = 'c';

        select generation_expression from information_schema.columns
        where table_name = 'objects' and column_name = 'area_total_sp';

        select conname, pg_get_constraintdef(oid) from pg_constraint
        where conrelid = 'contracts'::regclass and contype = 'c';

    Значения ORM-стороны (`ORM_OBJECT_CHECKS`, `ORM_CONTRACT_CHECKS`) — не
    измеренные, а буквально то, что записано в models.py: PostgreSQL их не
    трогает.
    """

    # --- БД: замерено на gca_test после наката 0009 (см. докстринг класса) ---
    DB_OBJECT_CHECKS = {
        "ck_objects_area_aboveground_sp_non_negative": (
            "CHECK (((area_aboveground_sp IS NULL) OR (area_aboveground_sp >= (0)::numeric)))"
        ),
        "ck_objects_area_underground_sp_non_negative": (
            "CHECK (((area_underground_sp IS NULL) OR (area_underground_sp >= (0)::numeric)))"
        ),
        "ck_objects_area_total_sp_positive": (
            "CHECK (((area_total_sp IS NULL) OR (area_total_sp > (0)::numeric)))"
        ),
        "ck_objects_areas_both_or_neither": (
            "CHECK (((area_aboveground_sp IS NULL) = (area_underground_sp IS NULL)))"
        ),
        # --- миграция 0013, полезная площадь (спека 2026-08-15 §2.3) ---
        "ck_objects_area_useful_sp_non_negative": (
            "CHECK (((area_useful_sp IS NULL) OR (area_useful_sp >= (0)::numeric)))"
        ),
        "ck_objects_area_useful_sp_within_total": (
            "CHECK (((area_useful_sp IS NULL)"
            " OR (area_useful_sp <= (area_aboveground_sp + area_underground_sp))))"
        ),
    }
    DB_OBJECT_AREA_TOTAL_GENERATED = "(area_aboveground_sp + area_underground_sp)"

    # Внимание: словарь целиком, поэтому существующий ck_contracts_total_amount_
    # non_negative (миграция 0002) обязан присутствовать — иначе сравнение
    # словарём целиком не пройдёт даже на верной миграции 0009.
    DB_CONTRACT_CHECKS = {
        "ck_contracts_total_amount_non_negative": (
            "CHECK (((total_amount IS NULL) OR (total_amount >= (0)::numeric)))"
        ),
        "ck_contracts_advance_pct_range": (
            "CHECK (((advance_pct IS NULL) OR ((advance_pct >= (0)::numeric)"
            " AND (advance_pct <= (100)::numeric))))"
        ),
        "ck_contracts_bank_guarantee_pct_range": (
            "CHECK (((bank_guarantee_pct IS NULL) OR ((bank_guarantee_pct >= (0)::numeric)"
            " AND (bank_guarantee_pct <= (100)::numeric))))"
        ),
        "ck_contracts_retention_pct_range": (
            "CHECK (((retention_pct IS NULL) OR ((retention_pct >= (0)::numeric)"
            " AND (retention_pct <= (100)::numeric))))"
        ),
    }

    # --- ORM: буквально то, что в models.py, сравнение по памяти корректно ---
    ORM_OBJECT_CHECKS = {
        "ck_objects_area_aboveground_sp_non_negative": (
            "area_aboveground_sp IS NULL OR area_aboveground_sp >= 0"
        ),
        "ck_objects_area_underground_sp_non_negative": (
            "area_underground_sp IS NULL OR area_underground_sp >= 0"
        ),
        "ck_objects_area_total_sp_positive": "area_total_sp IS NULL OR area_total_sp > 0",
        "ck_objects_areas_both_or_neither": (
            "(area_aboveground_sp IS NULL) = (area_underground_sp IS NULL)"
        ),
        # --- миграция 0013, полезная площадь (спека 2026-08-15 §2.3) ---
        "ck_objects_area_useful_sp_non_negative": (
            "area_useful_sp IS NULL OR area_useful_sp >= 0"
        ),
        "ck_objects_area_useful_sp_within_total": (
            "area_useful_sp IS NULL "
            "OR area_useful_sp <= area_aboveground_sp + area_underground_sp"
        ),
    }
    ORM_OBJECT_AREA_TOTAL_EXPRESSION = "area_aboveground_sp + area_underground_sp"

    ORM_CONTRACT_CHECKS = {
        "ck_contracts_total_amount_non_negative": "total_amount IS NULL OR total_amount >= 0",
        "ck_contracts_advance_pct_range": (
            "advance_pct IS NULL OR (advance_pct >= 0 AND advance_pct <= 100)"
        ),
        "ck_contracts_bank_guarantee_pct_range": (
            "bank_guarantee_pct IS NULL OR (bank_guarantee_pct >= 0 AND bank_guarantee_pct <= 100)"
        ),
        "ck_contracts_retention_pct_range": (
            "retention_pct IS NULL OR (retention_pct >= 0 AND retention_pct <= 100)"
        ),
    }

    def test_database_holds_the_declared_expressions(self, db_session):
        """Что реально легло в БД: все CHECK на обеих таблицах и generated-
        выражение `area_total_sp`. Сравнение словарём целиком — как у
        `TestWorkCategoriesSchema` выше — ловит и подмену выражения, и лишний
        CHECK, и пропажу существующего (`ck_contracts_total_amount_non_negative`).
        """
        object_rows = dict(
            db_session.execute(
                sa.text(
                    "select conname, pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid = 'objects'::regclass and contype = 'c'"
                )
            ).all()
        )
        assert object_rows == self.DB_OBJECT_CHECKS

        generated = db_session.execute(
            sa.text(
                "select generation_expression from information_schema.columns "
                "where table_name = 'objects' and column_name = 'area_total_sp'"
            )
        ).scalar_one()
        assert generated == self.DB_OBJECT_AREA_TOTAL_GENERATED

        contract_rows = dict(
            db_session.execute(
                sa.text(
                    "select conname, pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid = 'contracts'::regclass and contype = 'c'"
                )
            ).all()
        )
        assert contract_rows == self.DB_CONTRACT_CHECKS

    def test_orm_declares_the_same_expressions(self):
        """Вторая сторона парности: что объявлено в models.py — ObjectModel и
        Contract. Сравнение словарём целиком, а не по выбранным ключам: иначе
        лишний CHECK, объявленный только в модели, остался бы незамеченным.
        """
        object_checks = {
            c.name: str(c.sqltext)
            for c in ObjectModel.__table__.constraints
            if isinstance(c, CheckConstraint)
        }
        assert object_checks == self.ORM_OBJECT_CHECKS
        computed = ObjectModel.__table__.c.area_total_sp.computed
        assert str(computed.sqltext) == self.ORM_OBJECT_AREA_TOTAL_EXPRESSION
        assert computed.persisted is True

        contract_checks = {
            c.name: str(c.sqltext)
            for c in Contract.__table__.constraints
            if isinstance(c, CheckConstraint)
        }
        assert contract_checks == self.ORM_CONTRACT_CHECKS


# ---------------------------------------------------------------------------
#  estimates.vat_rate_*: ручные ставки НДС сметы (задача 3 пересчёта, §2.1)
# ---------------------------------------------------------------------------

class TestEstimateVatRateColumns:
    """`CHECK` на обеих новых процентных колонках — тот же класс защиты, что у
    `TestProposalVatRate` (§2.9): диапазон, а не основной фильтр."""

    def test_base_override_out_of_range_is_rejected(self, db_session, factories):
        estimate = factories.EstimateFactory.create()
        with rejected(db_session, contains="ck_estimates_vat_rate_base_override"):
            db_session.execute(
                sa.update(Estimate)
                .where(Estimate.id == estimate.id)
                .values(vat_rate_base_override=Decimal("101"))
            )

    def test_target_out_of_range_is_rejected(self, db_session, factories):
        estimate = factories.EstimateFactory.create()
        with rejected(db_session, contains="ck_estimates_vat_rate_target"):
            db_session.execute(
                sa.update(Estimate)
                .where(Estimate.id == estimate.id)
                .values(vat_rate_target=Decimal("101"))
            )


# ---------------------------------------------------------------------------
#  Миграция 0012: переименование VIEW отклонений, нетто-ось v_category_totals
# ---------------------------------------------------------------------------

def test_deviation_inputs_view_has_no_deviation_pct(db_session):
    columns = {
        row[0]
        for row in db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'v_position_deviation_inputs'"
            )
        )
    }
    assert "deviation_pct" not in columns
    assert {"vat_rate_base", "vat_rate_target"} <= columns


def test_old_deviations_view_is_gone(db_session):
    assert db_session.execute(
        sa.text("SELECT to_regclass('v_position_deviations')")
    ).scalar() is None


def test_category_totals_view_groups_by_proposal(db_session):
    columns = {
        row[0]
        for row in db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'v_category_totals'"
            )
        )
    }
    assert {"proposal_id", "vat_rate_base"} <= columns
