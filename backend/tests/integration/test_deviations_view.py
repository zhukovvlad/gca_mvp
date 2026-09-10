"""Семантика VIEW v_position_deviation_inputs (AGENTS.md §4).

Проверяем ровно то, что зафиксировано брифом: дата сравнения и её фолбэк,
выбор норматива по классу ДОГОВОРА, состав строк (разделы, строки без цены и
не-POSITION в выборку не попадают). VIEW переименован и лишён `deviation_pct`
миграцией 0012 (задача 3 пересчёта НДС): норматив объявлен ценой без НДС, а
VIEW отдавал бы отклонение на валовой цене — молча неверное. Отклонение теперь
считает Python от нетто (`crud.analytics._deviation`), и это проверяют
`test_analytics_api.py`/`test_rate_standards_api.py`; здесь — только то, что
`standard_unit_rate`/`rate_standard_id` выбраны верно (тот же факт, который
раньше проверялся косвенно, через готовое отклонение).

**Миграция 0016 (задача 4 плана правила цены).** Условие `WHERE pi.unit_cost_
total IS NOT NULL` заменено предикатом цены — конечное значение больше нуля,
той же формулой, что `crud.analytics._price_ok` (спека §2.3). Классы
`TestPricePredicateInView`, `TestViewColumnsSurviveTheMigration` и
`TestDowngradeRestores0012Behavior` ниже проверяют это отдельно от семантики
выше: их предмет — сам факт фильтрации и обратимость миграции, а не выбор
норматива или даты. Поведение читателей VIEW (агрегаты матрицы, `key_rates`
паспорта, drill-down) этой миграцией не меняется — задачи 2 и 3 плана уже
применили тот же предикат на своей стороне (`crud/analytics.py`,
`_cell_groups_cte`/`_cell_weights_cte`, `_priced_positions_select`), и это
пришпилено отдельными тестами в `test_analytics_api.py`
(`TestPassportKeyRatesPricePredicate`, `TestMatrixMixedPriceSets` и соседние) —
дублировать их здесь не нужно.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from crud.analytics import DECLARED_VIEW_COLUMNS
from models import CatalogKind

pytestmark = pytest.mark.integration


def _deviation_rows(session, item_id: int) -> list[sa.RowMapping]:
    return list(
        session.execute(
            sa.text(
                "SELECT * FROM v_position_deviation_inputs WHERE position_item_id = :id"
            ),
            {"id": item_id},
        ).mappings()
    )


def _priced_item(factories, *, rate_class=None, estimate_date=dt.date(2025, 4, 1), **item_kwargs):
    """Полная цепочка договор → смета → лот → предложение → позиция."""
    contract_kwargs = {"rate_class": rate_class} if rate_class is not None else {}
    contract = factories.ContractFactory.create(**contract_kwargs)
    estimate = factories.EstimateFactory.create(
        contract=contract, data_prepared_on_date=estimate_date
    )
    lot = factories.LotFactory.create(estimate=estimate)
    proposal = factories.ProposalFactory.create(lot=lot, contractor=contract.contractor)
    item_kwargs.setdefault("catalog_position", factories.CatalogPositionFactory.create())
    return factories.PositionItemFactory.create(proposal=proposal, **item_kwargs)


def _migration_0016():
    """Загружает файл миграции 0016 модулем — тот же приём, что `_migration_
    0015()` в `test_tenders_schema.py`: файл миграции не пакет, импортировать
    его по имени нельзя, а копировать текст `upgrade`/`downgrade` в тест —
    значит проверять третью, самостоятельно написанную копию условия вместо
    настоящей миграции."""
    path = next(
        Path(__file__).resolve().parents[2].glob("alembic/versions/*0016-price_predicate.py")
    )
    spec = importlib.util.spec_from_file_location("_migration_0016", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_0012():
    """Тот же приём, что `_migration_0016()`, для оригинала — нужен
    `TestFrozenCopyMatchesMigration0012`, чтобы сверять живую константу файла
    0012, а не полагаться на разовую сверку `sha256` руками (находка ревью:
    константа заморожена в 0016 «на глаз» и НИЧЕМ не застрахована от дрейфа
    при будущих правках 0012 не подать признака — 0012 трогать нельзя, но
    подмена самой копии внутри 0016 тоже осталась бы незамеченной)."""
    path = next(
        Path(__file__).resolve().parents[2].glob("alembic/versions/*0012-vat_rate_recalculation.py")
    )
    spec = importlib.util.spec_from_file_location("_migration_0012", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _op_bound_to(connection):
    """Даёт `op.execute(...)` внутри миграции доступ к ЭТОМУ соединению —
    настоящему соединению тестовой транзакции (`db_session`, `conftest.py`),
    а не сессии `alembic upgrade/downgrade`. Позволяет вызвать НАСТОЯЩИЕ
    `upgrade()`/`downgrade()` из файла миграции внутри теста: DDL (`DROP VIEW`/
    `CREATE VIEW`) в PostgreSQL транзакционен, и он целиком откатится вместе с
    транзакцией `db_session` после теста — второй копии VIEW в схеме не
    останется, и другие тесты этого не увидят."""
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        yield


class TestDeviationValue:
    def test_deviation_against_matching_standard(self, db_session, factories):
        item = _priced_item(factories, unit_cost_total=Decimal("120.00"))
        contract = item.proposal.lot.estimate.contract
        factories.RateStandardFactory.create(
            catalog_position=item.catalog_position,
            rate_class=contract.rate_class,
            standard_unit_rate=Decimal("100.00"),
            valid_from=dt.date(2025, 1, 1),
        )
        db_session.flush()

        (row,) = _deviation_rows(db_session, item.id)
        assert row["standard_unit_rate"] == Decimal("100.00")
        assert row["comparison_date"] == dt.date(2025, 4, 1)

    def test_missing_standard_gives_null_not_zero(self, db_session, factories):
        """«Нет норматива» — `rate_standard_id`/`standard_unit_rate` пусты, и Python
        выше (`crud.analytics._deviation`) обязан отличить это от «0%» (§10)."""
        item = _priced_item(factories, unit_cost_total=Decimal("120.00"))
        db_session.flush()

        (row,) = _deviation_rows(db_session, item.id)
        assert row["rate_standard_id"] is None
        assert row["standard_unit_rate"] is None

    def test_standard_of_another_class_is_not_applied(self, db_session, factories):
        item = _priced_item(factories, unit_cost_total=Decimal("120.00"))
        factories.RateStandardFactory.create(
            catalog_position=item.catalog_position,
            rate_class=factories.RateClassFactory.create(),
            standard_unit_rate=Decimal("100.00"),
        )
        db_session.flush()

        (row,) = _deviation_rows(db_session, item.id)
        assert row["rate_standard_id"] is None

    def test_weight_prefers_suggested_quantity(self, db_session, factories):
        """w = COALESCE(suggested_quantity, quantity) — §6, подтверждено фазой 0."""
        item = _priced_item(
            factories, quantity=Decimal("1"), suggested_quantity=Decimal("12.5")
        )
        db_session.flush()
        (row,) = _deviation_rows(db_session, item.id)
        assert row["weight"] == Decimal("12.5")

        fallback = _priced_item(factories, quantity=Decimal("7"), suggested_quantity=None)
        db_session.flush()
        (row,) = _deviation_rows(db_session, fallback.id)
        assert row["weight"] == Decimal("7")


class TestComparisonDate:
    def test_falls_back_to_contract_signed_date(self, db_session, factories):
        item = _priced_item(factories, estimate_date=None, unit_cost_total=Decimal("110.00"))
        contract = item.proposal.lot.estimate.contract
        factories.RateStandardFactory.create(
            catalog_position=item.catalog_position,
            rate_class=contract.rate_class,
            standard_unit_rate=Decimal("100.00"),
            valid_from=dt.date(2025, 1, 1),
        )
        db_session.flush()

        (row,) = _deviation_rows(db_session, item.id)
        assert row["comparison_date"] == contract.signed_date
        assert row["standard_unit_rate"] == Decimal("100.00")

    def test_standard_outside_the_period_is_not_applied(self, db_session, factories):
        item = _priced_item(
            factories, estimate_date=dt.date(2025, 4, 1), unit_cost_total=Decimal("120.00")
        )
        contract = item.proposal.lot.estimate.contract
        factories.RateStandardFactory.create(
            catalog_position=item.catalog_position,
            rate_class=contract.rate_class,
            valid_from=dt.date(2025, 5, 1),   # начинается позже даты сметы
            valid_to=None,
        )
        db_session.flush()

        (row,) = _deviation_rows(db_session, item.id)
        assert row["rate_standard_id"] is None

    def test_reapproval_does_not_change_old_estimates(self, db_session, factories):
        """Переутверждение норматива не меняет отклонения прошлых смет (§10)."""
        rate_class = factories.RateClassFactory.create()
        position = factories.CatalogPositionFactory.create()
        old_item = _priced_item(
            factories,
            rate_class=rate_class,
            estimate_date=dt.date(2025, 4, 1),
            catalog_position=position,
            unit_cost_total=Decimal("120.00"),
        )
        new_item = _priced_item(
            factories,
            rate_class=rate_class,
            estimate_date=dt.date(2026, 4, 1),
            catalog_position=position,
            unit_cost_total=Decimal("120.00"),
        )
        factories.RateStandardFactory.create(
            catalog_position=position,
            rate_class=rate_class,
            standard_unit_rate=Decimal("100.00"),
            valid_from=dt.date(2025, 1, 1),
            valid_to=dt.date(2026, 1, 1),
        )
        factories.RateStandardFactory.create(
            catalog_position=position,
            rate_class=rate_class,
            standard_unit_rate=Decimal("150.00"),   # старая × индекс инфляции
            valid_from=dt.date(2026, 1, 1),
            valid_to=None,
            inflation_index=Decimal("1.5"),
        )
        db_session.flush()

        (old_row,) = _deviation_rows(db_session, old_item.id)
        (new_row,) = _deviation_rows(db_session, new_item.id)
        assert old_row["standard_unit_rate"] == Decimal("100.00")
        assert new_row["standard_unit_rate"] == Decimal("150.00")


class TestExcludedRows:
    def test_chapter_rows_excluded(self, db_session, factories):
        item = _priced_item(factories, is_chapter=True)
        db_session.flush()
        assert _deviation_rows(db_session, item.id) == []

    def test_rows_without_price_excluded(self, db_session, factories):
        """Пустая стоимость — NULL, не 0 (§3); сравнивать нечего."""
        item = _priced_item(factories, unit_cost_total=None)
        db_session.flush()
        assert _deviation_rows(db_session, item.id) == []

    def test_unmatched_rows_excluded(self, db_session, factories):
        item = _priced_item(factories, catalog_position=None)
        db_session.flush()
        assert _deviation_rows(db_session, item.id) == []

    @pytest.mark.parametrize(
        "kind", [CatalogKind.HEADER, CatalogKind.LOT_HEADER, CatalogKind.TRASH, CatalogKind.TO_REVIEW]
    )
    def test_non_position_catalog_rows_excluded(self, db_session, factories, kind):
        item = _priced_item(
            factories,
            catalog_position=factories.CatalogPositionFactory.create(kind=kind.value),
        )
        db_session.flush()
        assert _deviation_rows(db_session, item.id) == []


class TestPricePredicateInView:
    """Миграция 0016 (спека §2.3, план — задача 4): `v_position_deviation_
    inputs` не отдаёт позицию, чья цена не конечна и не больше нуля — шесть
    входов, каждый своим тестом (DoD 2), и один вход, на котором VIEW обязан
    отдавать строку.

    Пустая цена (`unit_cost_total IS NULL`) исключена из выборки VIEW и ДО
    этой миграции — `TestExcludedRows.test_rows_without_price_excluded` выше
    стережёт это независимо и не про предикат цены, а про текст 0012, который
    эта миграция не отменяет (`IS NULL` не входит в новое условие явно, но
    `column > 0` на `NULL` в PostgreSQL даёт `NULL`, а не `TRUE`, и строка не
    проходит `WHERE` — тот же трёхзначный эффект, что у `_price_ok`,
    докстрока `crud.analytics._price_ok`). Здесь пустой вход предъявлен ещё
    раз — уже как участник семейства «шесть непригодных цен», а не
    изолированно.
    """

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("пустая", None),
            ("ноль", Decimal("0")),
            ("отрицательная", Decimal("-100")),
            ("NaN", Decimal("NaN")),
            ("+Infinity", Decimal("Infinity")),
            ("-Infinity", Decimal("-Infinity")),
        ],
    )
    def test_unsuitable_price_is_not_returned(self, db_session, factories, name, value):
        item = _priced_item(factories, unit_cost_total=value)
        db_session.flush()
        assert _deviation_rows(db_session, item.id) == [], (
            f"вход «{name}»: VIEW не обязан отдавать строку с такой ценой"
        )

    def test_finite_positive_price_is_returned(self, db_session, factories):
        item = _priced_item(factories, unit_cost_total=Decimal("120.00"))
        db_session.flush()
        rows = _deviation_rows(db_session, item.id)
        assert len(rows) == 1
        assert rows[0]["unit_cost_total"] == Decimal("120.00")


class TestViewColumnsSurviveTheMigration:
    """Состав колонок VIEW после миграции 0016 совпадает с составом до неё —
    сверка по `information_schema`, а не чтением текста (план, задача 4, DoD
    2). `alembic check` тут не оракул: VIEW для `Base.metadata` невидим
    (`alembic/env.py`, комментарий у `RAW_SQL_INDEXES` объясняет тот же повод
    для индексов) — `alembic check` о VIEW ничего не знает и не мог бы
    заметить пропавшую или добавленную колонку.

    Оракул здесь — не литерал, а настоящий откат и накат ЭТОЙ миграции внутри
    тестовой транзакции (`_migration_0016`, `_op_bound_to`): `downgrade()`
    физически пересоздаёт VIEW текстом 0012, `upgrade()` — текстом 0016, и оба
    раза колонки сняты одним и тем же запросом к `information_schema.columns`,
    упорядоченным по `ordinal_position` (порядок колонок — тоже часть состава,
    не только имена).
    """

    @staticmethod
    def _columns(session) -> list[str]:
        return list(
            session.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'v_position_deviation_inputs' "
                    "ORDER BY ordinal_position"
                )
            ).scalars()
        )

    @staticmethod
    def _viewdef(session) -> str:
        """Текст VIEW, как его видит сам PostgreSQL (`pg_get_viewdef`) — не
        Python-константа, а факт СХЕМЫ. Нужен как наблюдаемый признак того,
        что `downgrade()`/`upgrade()` реально пересоздали объект, а не были
        подменены на `pass` (находка ревью, см. `test_columns_before_and_
        after_the_migration_match` ниже)."""
        return session.execute(
            sa.text("SELECT pg_get_viewdef('v_position_deviation_inputs', true)")
        ).scalar_one()

    def test_columns_before_and_after_the_migration_match(self, db_session):
        after = self._columns(db_session)
        after_def = self._viewdef(db_session)
        assert after == list(DECLARED_VIEW_COLUMNS), (
            "sanity: состояние ДО этого теста уже обязано совпадать с "
            "объявлением crud.analytics.DEVIATION_INPUTS"
        )

        module = _migration_0016()
        with _op_bound_to(db_session.connection()):
            module.downgrade()

        # Наблюдаемый признак между `downgrade()` и снимком «до» (находка
        # ревью): без него неработающий откат (`downgrade()` → `pass`) оставил
        # бы прежний, уже мигрировавший VIEW — снимок «до» читался бы с ТОГО ЖЕ
        # объекта, что и «после», и сравнение ниже совпало бы с собой
        # тавтологически, не заметив ничего.
        before_def = self._viewdef(db_session)
        assert "IS NOT NULL" in before_def, (
            "после downgrade() текст VIEW обязан быть текстом 0012 "
            "(`unit_cost_total IS NOT NULL»), а не остатком 0016"
        )
        assert before_def != after_def, (
            "VIEW не изменился между «до» и «после» — downgrade() не выполнился"
        )

        before = self._columns(db_session)
        with _op_bound_to(db_session.connection()):
            module.upgrade()

        assert before == after, (
            f"миграция 0016 не вправе менять состав/порядок колонок VIEW: "
            f"до отката {before!r}, после наката {after!r}"
        )


class TestDowngradeRestores0012Behavior:
    """`downgrade` возвращает поведение 0012 — проверено запуском, а не сверкой
    строк (план, задача 4, DoD 2). `just db-test-check` (`alembic check`)
    видит только `upgrade` до head и сверку метаданных и о `downgrade` не
    говорит ничего (план, «Решения плана», п. 8) — этот тест единственный,
    кто реально исполняет `downgrade()` из файла миграции.

    DDL (`DROP VIEW`/`CREATE VIEW`) выполняется через `op`, привязанный к
    СОЕДИНЕНИЮ тестовой транзакции (`_op_bound_to`), и PostgreSQL откатывает
    его вместе с транзакцией `db_session` после теста (`conftest.py`) — второй,
    «дофичевый» VIEW не просачивается в другие тесты этого файла и других
    модулей, гоняемых на той же схеме.
    """

    def test_downgrade_makes_zero_price_visible_again(self, db_session, factories):
        item = _priced_item(factories, unit_cost_total=Decimal("0"))
        db_session.flush()
        assert _deviation_rows(db_session, item.id) == [], (
            "до отката ноль уже отфильтрован миграцией 0016"
        )

        module = _migration_0016()
        with _op_bound_to(db_session.connection()):
            module.downgrade()

        rows = _deviation_rows(db_session, item.id)
        assert len(rows) == 1, "после отката текст 0012 снова отдаёт нулевую цену"
        assert rows[0]["unit_cost_total"] == Decimal("0")

        with _op_bound_to(db_session.connection()):
            module.upgrade()

    def test_downgrade_still_excludes_the_empty_price(self, db_session, factories):
        """Член `IS NOT NULL` текста 0012 — не украшение: без него откат отдал
        бы пустую цену вместе с нулевой, а 0012 её никогда не отдавал. Снятие
        этого члена из `V_POSITION_DEVIATION_INPUTS_0012` красит именно этот
        тест и не красит `test_downgrade_makes_zero_price_visible_again` выше
        (проверено вручную снятием защиты при разработке задачи 4) — ноль и
        пустота исключаются РАЗНЫМИ членами условия что до миграции 0016, что
        после, и оба обязаны быть предъявлены по отдельности.

        **Правка ревью (ложно-зелёный тест, находка ревьюера).** Утверждение
        «пустая цена не видна» само по себе не отличает работающий откат от
        `downgrade()`, превращённого в `pass`: НИ ДО отката (VIEW уже текста
        0016 не отдаёт пустую цену), НИ ПОСЛЕ несработавшего отката (VIEW
        остался бы тем же самым, 0016) пустая цена не появится — тест был бы
        зелёным в обоих случаях и не стерёг бы вообще ничего. Добавлено
        ПОЛОЖИТЕЛЬНОЕ наблюдение из соседнего теста: нулевая цена, невидимая
        ДО отката, обязана СТАТЬ видимой ПОСЛЕ — это единственный факт,
        отличающий «откат правда произошёл» от «откат — no-op». Подтверждено
        мутацией `downgrade()` → `pass`: с ней падает именно проверка нулевой
        цены (`assert len(zero_rows) == 1`), не пустой — см. отчёт задачи."""
        empty_item = _priced_item(factories, unit_cost_total=None)
        zero_item = _priced_item(factories, unit_cost_total=Decimal("0"))
        db_session.flush()
        assert _deviation_rows(db_session, zero_item.id) == [], (
            "до отката ноль уже отфильтрован миграцией 0016 — контроль входа"
        )

        module = _migration_0016()
        with _op_bound_to(db_session.connection()):
            module.downgrade()

        assert _deviation_rows(db_session, empty_item.id) == [], (
            "текст 0012 никогда не отдавал пустую цену — откат не вправе это изменить"
        )
        zero_rows = _deviation_rows(db_session, zero_item.id)
        assert len(zero_rows) == 1, (
            "откат обязан реально произойти: нулевая цена, скрытая миграцией "
            "0016, после НАСТОЯЩЕГО отката снова видна — иначе неработающий "
            "downgrade() (например, замена тела на `pass`) остался бы незамечен"
        )
        assert zero_rows[0]["unit_cost_total"] == Decimal("0")

        with _op_bound_to(db_session.connection()):
            module.upgrade()


class TestFrozenCopyMatchesMigration0012:
    """Дословность копии `V_POSITION_DEVIATION_INPUTS_0012` (в 0016) с оригиналом
    `V_POSITION_DEVIATION_INPUTS` (в 0012) — предмет ЭТОГО теста, в отличие от
    правила цены: DoD 2 требует именно ПОБАЙТОВОГО (с поправкой на построчную
    нормализацию переноса строк — см. ниже) совпадения текста, а не поведения,
    и до этого теста совпадение проверялось один раз руками (`sha256`, отчёт
    задачи) — разовая проверка не стережёт БУДУЩУЮ правку.

    Найдено ревью: мутации ВНУТРИ замороженной копии — подмена выражения веса
    (`COALESCE(pi.suggested_quantity, pi.quantity)` на голое `pi.quantity`) и
    снятие условия по классу договора в JOIN нормативов
    (`rs.rate_class_id = c.rate_class_id`) — не роняли НИ ОДНОГО теста
    поведения: тестовые фикстуры этого файла не создают достаточно контраста
    (`quantity == suggested_quantity` в общем случае, один класс договора на
    сценарий), а тесты других модулей идут через `crud.analytics.DEVIATION_
    INPUTS`, объявление которого никак не участвует в тексте МИГРАЦИИ. Сверка
    текста — единственный способ поймать дрейф замороженной копии.

    Сравниваются ЖИВЫЕ константы модулей (импортированных `importlib`), а не
    байты файлов: Python нормализует переносы строк ДО компиляции исходника
    (universal newlines) одинаково для CRLF и LF, поэтому то, что файл 0012
    хранится в CRLF, а 0016 — в LF (см. докстроку миграции 0016 после правки
    9), сравнению строковых констант не мешает — они совпадают как объекты
    Python независимо от исходных окончаний строк на диске.
    """

    def test_frozen_copy_is_identical_to_the_live_0012_constant(self):
        m0012 = _migration_0012()
        m0016 = _migration_0016()
        assert m0016.V_POSITION_DEVIATION_INPUTS_0012 == m0012.V_POSITION_DEVIATION_INPUTS, (
            "копия текста 0012 внутри 0016 разошлась с оригиналом миграции 0012 — "
            "downgrade() отдал бы уже не дофичевое поведение"
        )
