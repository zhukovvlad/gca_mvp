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
from sqlalchemy import CheckConstraint
from sqlalchemy.exc import IntegrityError, ProgrammingError

from models import (
    PASSPORT_TOP_N_DEFAULT,
    PASSPORT_TOP_N_MAX,
    PASSPORT_TOP_N_MIN,
    AppSettings,
    CatalogKind,
    CatalogPosition,
    EstimateRawData,
    ImportJobStatus,
    Lot,
    MatchingCache,
    MatchSource,
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

    def test_source_other_than_file_is_rejected(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        item = self._row(db_session, factories, proposal, is_chapter=True)
        with rejected(db_session, contains="ck_position_items_category_source"):
            db_session.execute(
                sa.update(PositionItem)
                .where(PositionItem.id == item.id)
                .values(
                    work_category_id=self._any_category_id(db_session),
                    category_source="manual",
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
