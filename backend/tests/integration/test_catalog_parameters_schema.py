"""Схема параметров семьи без предела (миграция 0022; спека
`2026-10-19-catalog-parameters-design.md` §2.2): каждое ограничение доказано
пробоем — `INSERT` с ожиданием `IntegrityError`, имя ограничения берётся из
`diag.constraint_name`, на каждое заявление проверки свой вход, который
нарушает ровно его, и соседний допустимый вход.

Порядок проверки CHECK в PostgreSQL — по имени ограничения (алфавит): вход,
нарушающий два ограничения разом, отказывает от первого по алфавиту; поэтому
каждый отрицательный вход строится так, чтобы остальные ограничения его
пропускали.

`upgrade` на базе с данными всех шести таблиц и заданиями четырёх видов и
`downgrade` по каждому носителю — на отдельной scratch-базе
(`@pytest.mark.migration_roundtrip`).
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from models import (
    CK_CONTEXT_VALUE_OFF_LIST_TEXT,
    CK_CONTEXT_VALUE_SOURCE_PAIR,
    CK_EVENT_SUBJECT_BY_TYPE,
    CK_PARAMETER_ORDINAL_RANGE,
    CK_PARAMETER_ORIGIN,
    CK_PARAMETER_RANGE_SHAPE,
    CK_PARAMETER_ROLE,
    CK_PARAMETER_VALUE_KIND,
    CK_PARAMETER_VALUE_RANGE_ORDER,
    CK_PARAMETER_VALUE_RETIRED_NOT_MERGED,
    CK_SEMANTIC_JOBS_REQUESTED_ORDINALS,
    PARAMETER_ORIGINS,
    PARAMETER_ROLES,
    PARAMETER_VALUE_KINDS,
    SEMANTIC_EVENT_TYPES,
    SEMANTIC_EVENT_TYPES_SQL,
    VALUE_SOURCES,
    ContextParameterValue,
    FamilyParameter,
    ParameterOrigin,
    ParameterRole,
    ParameterValueKind,
    SemanticEvent,
    ValueSource,
)
from services.semantic_events import (
    EVENT_REQUIRED_KEYS,
    FAMILY_EVENT_TYPES,
    SemanticEventError,
    record_event,
)
from tests.integration.test_catalog_discovery_schema import _seed_three_job_kinds, rejected_by
from tests.integration.test_semantic_queue_schema import _context, _job
from tests.integration.test_work_variants_schema import (
    _assigned_context,
    _chain,
    _event,
    _family,
    _live_constraint_names,
    _migration_0019,
    _schema,
    _scratch_alembic,
    _uid,
    _value,
)

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _load_migration(glob: str, module_name: str):
    path = next(BACKEND_ROOT.glob(f"alembic/versions/{glob}"))
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_0021():
    return _load_migration("*0021-catalog_discovery.py", "_migration_0021_params_test")


def _migration_0022():
    return _load_migration("*0022-catalog_parameters.py", "_migration_0022_params_test")


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _param(session, schema, ordinal=1, **overrides) -> FamilyParameter:
    name = overrides.pop("name", None) or f"Параметр {ordinal} {_uid()}"
    parameter = FamilyParameter(
        schema_id=schema.id, ordinal=ordinal, name=name, name_norm=name.strip().lower(),
        **overrides,
    )
    session.add(parameter)
    session.flush()
    return parameter


def _range_kwargs(**overrides) -> dict:
    """Допустимый параметр-диапазон «100–140 с шагом 20»; тест нарушения
    переопределяет ровно одно поле."""
    defaults = dict(
        value_kind="range", closed=True, range_from=Decimal("100"), range_to=Decimal("140"),
        range_step=Decimal("20"),
    )
    defaults.update(overrides)
    return defaults


def _context_value(session, context, schema, parameter, **overrides) -> ContextParameterValue:
    defaults = dict(
        context_id=context.id, schema_id=schema.id, parameter_id=parameter.id, value_id=None,
        source="none",
    )
    defaults.update(overrides)
    row = ContextParameterValue(**defaults)
    session.add(row)
    session.flush()
    return row


def _one_schema_world(session, factories):
    family, schema, parameter, value, _variant = _chain(session, factories)
    return family, schema, parameter, value


# ---------------------------------------------------------------------------
#  1. Новые колонки: тип, допустимость NULL, умолчание — против независимого
#     перечня
# ---------------------------------------------------------------------------

_NEW_COLUMNS = [
    ("family_parameters", "role", "text", "NO", "'main'::text"),
    ("family_parameters", "value_kind", "text", "NO", "'exact'::text"),
    ("family_parameters", "closed", "boolean", "NO", "false"),
    ("family_parameters", "range_from", "numeric", "YES", None),
    ("family_parameters", "range_to", "numeric", "YES", None),
    ("family_parameters", "range_step", "numeric", "YES", None),
    ("family_parameters", "origin", "text", "NO", "'model'::text"),
    ("family_parameters", "archived_at", "timestamp with time zone", "YES", None),
    ("family_parameter_schemas", "revision", "integer", "NO", "0"),
    ("family_parameter_values", "range_lo", "numeric", "YES", None),
    ("family_parameter_values", "range_hi", "numeric", "YES", None),
    ("family_parameter_values", "retired_at", "timestamp with time zone", "YES", None),
    ("context_parameter_values", "off_list_text", "text", "YES", None),
    ("context_parameter_values", "refresh_requested_at", "timestamp with time zone", "YES", None),
    ("semantic_jobs", "requested_ordinals", "ARRAY", "YES", None),
]


class TestNewColumns:
    @pytest.mark.parametrize(
        ("table", "column", "data_type", "nullable", "default"), _NEW_COLUMNS,
        ids=[f"{row[0]}.{row[1]}" for row in _NEW_COLUMNS],
    )
    def test_column_shape(self, db_session, table, column, data_type, nullable, default):
        row = db_session.execute(
            sa.text(
                "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).one()
        assert tuple(row) == (data_type, nullable, default)

    @pytest.mark.parametrize(
        ("table", "column", "data_type", "nullable", "default"), _NEW_COLUMNS,
        ids=[f"{row[0]}.{row[1]}" for row in _NEW_COLUMNS],
    )
    def test_the_model_declares_the_same_column_shape(
        self, table, column, data_type, nullable, default
    ):
        """Тестовая база строится миграциями, поэтому тип, `NULL` и умолчание
        колонки в `models.py` иначе никем не сверяются: модель держит тот же
        перечень, что и база (ревью задачи 1)."""
        from sqlalchemy.dialects import postgresql

        from models import Base

        model_column = Base.metadata.tables[table].c[column]
        compiled = model_column.type.compile(dialect=postgresql.dialect()).lower()
        assert compiled == ("smallint[]" if data_type == "ARRAY" else data_type)
        assert model_column.nullable is (nullable == "YES")
        if default is None:
            assert model_column.server_default is None
        else:
            assert str(model_column.server_default.arg) == default.removesuffix("::text")

    def test_requested_ordinals_is_an_array_of_smallint(self, db_session):
        udt = db_session.execute(
            sa.text(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_name = 'semantic_jobs' AND column_name = 'requested_ordinals'"
            )
        ).scalar_one()
        assert udt == "_int2"

    def test_a_parameter_created_without_the_new_fields_gets_the_defaults(
        self, db_session, factories
    ):
        _family_row, schema, *_ = _one_schema_world(db_session, factories)
        parameter = _param(db_session, schema, 2)
        db_session.refresh(parameter)
        assert (
            parameter.role, parameter.value_kind, parameter.closed, parameter.origin,
            parameter.range_from, parameter.range_to, parameter.range_step,
            parameter.archived_at,
        ) == ("main", "exact", False, "model", None, None, None, None)

    def test_a_schema_version_starts_at_revision_zero(self, db_session, factories):
        schema = _schema(db_session, factories)
        db_session.refresh(schema)
        assert schema.revision == 0

    def test_a_value_and_a_context_row_start_without_the_new_fields(self, db_session, factories):
        family, schema, parameter, value = _one_schema_world(db_session, factories)
        db_session.refresh(value)
        assert (value.range_lo, value.range_hi, value.retired_at) == (None, None, None)
        row = _context_value(
            db_session, _assigned_context(db_session, factories, family), schema, parameter,
            value_id=value.id, source="name",
        )
        db_session.refresh(row)
        assert (row.off_list_text, row.refresh_requested_at) == (None, None)

    def test_a_job_starts_without_requested_ordinals(self, db_session, factories):
        job = _job(db_session, factories)
        db_session.refresh(job)
        assert job.requested_ordinals is None

    def test_the_unique_pair_schema_ordinal_stays(self, db_session):
        assert "uq_family_parameters_schema_ordinal" in _live_constraint_names(
            db_session, "family_parameters", "u"
        )


# ---------------------------------------------------------------------------
#  2. Параметр: роль, вид, происхождение, номер
# ---------------------------------------------------------------------------

class TestParameterEnums:
    @pytest.mark.parametrize("role", ["main", "secondary"])
    def test_each_role_passes(self, db_session, factories, role):
        schema = _schema(db_session, factories)
        assert _param(db_session, schema, 1, role=role).id is not None

    def test_unknown_role_is_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        with rejected_by(db_session, "ck_family_parameters_role"):
            _param(db_session, schema, 1, role="optional")

    @pytest.mark.parametrize("origin", ["model", "human"])
    def test_each_origin_passes(self, db_session, factories, origin):
        schema = _schema(db_session, factories)
        assert _param(db_session, schema, 1, origin=origin).id is not None

    def test_unknown_origin_is_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        with rejected_by(db_session, "ck_family_parameters_origin"):
            _param(db_session, schema, 1, origin="robot")

    def test_unknown_value_kind_is_rejected_by_its_own_check(self, db_session, factories):
        """Форма диапазона сама отказывает неизвестному виду — раньше по алфавиту
        имён; чтобы проверка вида была доказана отдельно, форму снимают в этой же
        транзакции (откат вернёт её)."""
        schema = _schema(db_session, factories)
        db_session.execute(
            sa.text("ALTER TABLE family_parameters DROP CONSTRAINT ck_family_parameters_range_shape")
        )
        with rejected_by(db_session, "ck_family_parameters_value_kind"):
            _param(db_session, schema, 1, value_kind="interval")

    def test_unknown_value_kind_is_rejected_with_the_shape_in_place(self, db_session, factories):
        schema = _schema(db_session, factories)
        with rejected_by(
            db_session, {"ck_family_parameters_range_shape", "ck_family_parameters_value_kind"}
        ):
            _param(db_session, schema, 1, value_kind="interval")

    def test_closed_exact_parameter_passes(self, db_session, factories):
        """«Только из списка» не требует диапазона: точный параметр бывает закрытым."""
        schema = _schema(db_session, factories)
        assert _param(db_session, schema, 1, closed=True).id is not None

    def test_archived_parameter_passes(self, db_session, factories):
        schema = _schema(db_session, factories)
        assert _param(db_session, schema, 1, archived_at=_now()).id is not None


class TestParameterOrdinal:
    @pytest.mark.parametrize("ordinal", [1, 3, 4, 100])
    def test_ordinals_from_one_to_one_hundred_pass(self, db_session, factories, ordinal):
        schema = _schema(db_session, factories)
        assert _param(db_session, schema, ordinal).id is not None

    @pytest.mark.parametrize("ordinal", [0, 101, -1])
    def test_ordinal_outside_the_range_is_rejected(self, db_session, factories, ordinal):
        schema = _schema(db_session, factories)
        with rejected_by(db_session, "ck_family_parameters_ordinal_range"):
            _param(db_session, schema, ordinal)

    def test_two_parameters_of_one_version_cannot_share_a_number(self, db_session, factories):
        schema = _schema(db_session, factories)
        _param(db_session, schema, 5)
        with rejected_by(db_session, "uq_family_parameters_schema_ordinal"):
            _param(db_session, schema, 5)

    def test_an_archived_parameter_still_holds_its_number(self, db_session, factories):
        schema = _schema(db_session, factories)
        _param(db_session, schema, 5, archived_at=_now())
        with rejected_by(db_session, "uq_family_parameters_schema_ordinal"):
            _param(db_session, schema, 5)

    def test_the_same_number_in_another_version_passes(self, db_session, factories):
        family = _family(db_session)
        first = _schema(db_session, factories, family, version=1, status="superseded")
        second = _schema(db_session, factories, family, version=2)
        _param(db_session, first, 5)
        assert _param(db_session, second, 5).id is not None


# ---------------------------------------------------------------------------
#  3. Форма диапазона параметра: точный ⇔ три NULL; диапазон ⇔ три NOT NULL,
#     шаг > 0, от < до, кратность, не больше 28 шагов, закрытый список
# ---------------------------------------------------------------------------

_SHAPE = "ck_family_parameters_range_shape"


class TestRangeShape:
    def test_an_exact_parameter_with_no_bounds_passes(self, db_session, factories):
        schema = _schema(db_session, factories)
        assert _param(db_session, schema, 1, value_kind="exact").id is not None

    def test_a_valid_range_passes(self, db_session, factories):
        schema = _schema(db_session, factories)
        assert _param(db_session, schema, 1, **_range_kwargs()).id is not None

    def test_a_range_with_decimal_bounds_and_step_passes(self, db_session, factories):
        """Кратность считается в `numeric`, а не в плавающей точке: 0,3 / 0,1 — целое."""
        schema = _schema(db_session, factories)
        kwargs = _range_kwargs(
            range_from=Decimal("0.5"), range_to=Decimal("0.8"), range_step=Decimal("0.1")
        )
        assert _param(db_session, schema, 1, **kwargs).id is not None

    def test_a_range_with_negative_bounds_passes(self, db_session, factories):
        schema = _schema(db_session, factories)
        kwargs = _range_kwargs(
            range_from=Decimal("-20"), range_to=Decimal("20"), range_step=Decimal("10")
        )
        assert _param(db_session, schema, 1, **kwargs).id is not None

    def test_twenty_eight_steps_pass(self, db_session, factories):
        schema = _schema(db_session, factories)
        kwargs = _range_kwargs(
            range_from=Decimal("0"), range_to=Decimal("28"), range_step=Decimal("1")
        )
        assert _param(db_session, schema, 1, **kwargs).id is not None

    def test_twenty_nine_steps_are_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        kwargs = _range_kwargs(
            range_from=Decimal("0"), range_to=Decimal("29"), range_step=Decimal("1")
        )
        with rejected_by(db_session, _SHAPE):
            _param(db_session, schema, 1, **kwargs)

    @pytest.mark.parametrize("missing", ["range_from", "range_to", "range_step"])
    def test_a_range_without_one_bound_is_rejected(self, db_session, factories, missing):
        """Каждая ветвь равносильности даёт `false`, а не `NULL`, на любой
        недостающей границе: `NULL` CHECK пропустил бы как истину."""
        schema = _schema(db_session, factories)
        with rejected_by(db_session, _SHAPE):
            _param(db_session, schema, 1, **_range_kwargs(**{missing: None}))

    def test_a_range_with_no_bounds_at_all_is_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        kwargs = _range_kwargs(range_from=None, range_to=None, range_step=None)
        with rejected_by(db_session, _SHAPE):
            _param(db_session, schema, 1, **kwargs)

    def test_a_zero_step_is_rejected_without_a_division_error(self, db_session, factories):
        """Шаг 0: условие `шаг > 0` обязано погасить деление на ноль в
        соседних атомах — отказ приходит от CHECK, а не ошибкой вычисления."""
        schema = _schema(db_session, factories)
        with rejected_by(db_session, _SHAPE):
            _param(db_session, schema, 1, **_range_kwargs(range_step=Decimal("0")))

    def test_a_negative_step_is_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        with rejected_by(db_session, _SHAPE):
            _param(db_session, schema, 1, **_range_kwargs(range_step=Decimal("-20")))

    def test_equal_bounds_are_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        kwargs = _range_kwargs(range_from=Decimal("100"), range_to=Decimal("100"))
        with rejected_by(db_session, _SHAPE):
            _param(db_session, schema, 1, **kwargs)

    def test_reversed_bounds_are_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        kwargs = _range_kwargs(range_from=Decimal("140"), range_to=Decimal("100"))
        with rejected_by(db_session, _SHAPE):
            _param(db_session, schema, 1, **kwargs)

    def test_a_fractional_number_of_steps_is_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        kwargs = _range_kwargs(range_from=Decimal("100"), range_to=Decimal("150"))
        with rejected_by(db_session, _SHAPE):
            _param(db_session, schema, 1, **kwargs)

    def test_a_range_that_is_not_closed_is_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        with rejected_by(db_session, _SHAPE):
            _param(db_session, schema, 1, **_range_kwargs(closed=False))

    @pytest.mark.parametrize(
        "bound",
        [
            {"range_from": Decimal("100")},
            {"range_to": Decimal("140")},
            {"range_step": Decimal("20")},
        ],
        ids=["range_from", "range_to", "range_step"],
    )
    def test_an_exact_parameter_with_one_bound_is_rejected(self, db_session, factories, bound):
        schema = _schema(db_session, factories)
        with rejected_by(db_session, _SHAPE):
            _param(db_session, schema, 1, value_kind="exact", **bound)

    def test_an_exact_parameter_with_all_bounds_is_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        with rejected_by(db_session, _SHAPE):
            _param(db_session, schema, 1, **_range_kwargs(value_kind="exact"))

    def test_an_update_that_breaks_the_shape_is_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        parameter = _param(db_session, schema, 1, **_range_kwargs())
        with rejected_by(db_session, _SHAPE):
            parameter.closed = False


# ---------------------------------------------------------------------------
#  4. Значение списка: границы диапазона, выведенное из списка
# ---------------------------------------------------------------------------

class TestParameterValueBounds:
    @pytest.mark.parametrize(
        "bounds",
        [
            {},
            {"range_lo": Decimal("100")},
            {"range_hi": Decimal("140")},
            {"range_lo": Decimal("100"), "range_hi": Decimal("140")},
            {"range_lo": Decimal("-5"), "range_hi": Decimal("-1")},
        ],
        ids=["none", "open_left_no", "open_right_no", "both", "negative"],
    )
    def test_valid_bounds_pass(self, db_session, factories, bounds):
        _family_row, _schema_row, parameter, _value_row = _one_schema_world(db_session, factories)
        assert _value(db_session, parameter, **bounds).id is not None

    def test_equal_bounds_are_rejected(self, db_session, factories):
        *_, parameter, _value_row = _one_schema_world(db_session, factories)
        with rejected_by(db_session, "ck_family_parameter_values_range_order"):
            _value(db_session, parameter, range_lo=Decimal("100"), range_hi=Decimal("100"))

    def test_reversed_bounds_are_rejected(self, db_session, factories):
        *_, parameter, _value_row = _one_schema_world(db_session, factories)
        with rejected_by(db_session, "ck_family_parameter_values_range_order"):
            _value(db_session, parameter, range_lo=Decimal("140"), range_hi=Decimal("100"))


class TestParameterValueRetired:
    def test_a_retired_value_passes(self, db_session, factories):
        *_, parameter, _value_row = _one_schema_world(db_session, factories)
        assert _value(db_session, parameter, retired_at=_now()).id is not None

    def test_a_merged_value_passes(self, db_session, factories):
        *_, parameter, target = _one_schema_world(db_session, factories)
        assert _value(db_session, parameter, merged_into_id=target.id).id is not None

    def test_retired_and_merged_together_are_rejected(self, db_session, factories):
        *_, parameter, target = _one_schema_world(db_session, factories)
        with rejected_by(db_session, "ck_family_parameter_values_retired_not_merged"):
            _value(db_session, parameter, merged_into_id=target.id, retired_at=_now())

    def test_retiring_a_merged_value_by_update_is_rejected(self, db_session, factories):
        *_, parameter, target = _one_schema_world(db_session, factories)
        merged = _value(db_session, parameter, merged_into_id=target.id)
        with rejected_by(db_session, "ck_family_parameter_values_retired_not_merged"):
            merged.retired_at = _now()

    def test_a_retired_value_can_return_to_the_list(self, db_session, factories):
        *_, parameter, _value_row = _one_schema_world(db_session, factories)
        value = _value(db_session, parameter, retired_at=_now())
        value.retired_at = None
        db_session.flush()
        assert value.retired_at is None


# ---------------------------------------------------------------------------
#  5. Строка контекста: значение не из списка и отметка «уточняется»
# ---------------------------------------------------------------------------

class TestContextRowOffList:
    def _world(self, db_session, factories):
        family, schema, parameter, value = _one_schema_world(db_session, factories)
        return (
            _assigned_context(db_session, factories, family), schema, parameter, value,
        )

    def test_an_off_list_row_with_text_and_no_value_passes(self, db_session, factories):
        context, schema, parameter, _value_row = self._world(db_session, factories)
        row = _context_value(
            db_session, context, schema, parameter, source="off_list", off_list_text="105 мм"
        )
        assert row.source == "off_list"

    def test_an_off_list_row_without_text_is_rejected(self, db_session, factories):
        context, schema, parameter, _value_row = self._world(db_session, factories)
        with rejected_by(db_session, "ck_context_parameter_values_off_list_text"):
            _context_value(db_session, context, schema, parameter, source="off_list")

    def test_text_on_a_row_that_is_not_off_list_is_rejected(self, db_session, factories):
        context, schema, parameter, _value_row = self._world(db_session, factories)
        with rejected_by(db_session, "ck_context_parameter_values_off_list_text"):
            _context_value(
                db_session, context, schema, parameter, source="none", off_list_text="105 мм"
            )

    def test_an_off_list_row_with_a_list_value_is_rejected(self, db_session, factories):
        context, schema, parameter, value = self._world(db_session, factories)
        with rejected_by(db_session, "ck_context_parameter_values_source_value_pair"):
            _context_value(
                db_session, context, schema, parameter, source="off_list",
                off_list_text="105 мм", value_id=value.id,
            )

    def test_a_list_row_without_a_value_is_still_rejected(self, db_session, factories):
        context, schema, parameter, _value_row = self._world(db_session, factories)
        with rejected_by(db_session, "ck_context_parameter_values_source_value_pair"):
            _context_value(db_session, context, schema, parameter, source="name", value_id=None)

    def test_a_row_with_a_list_value_and_a_name_source_still_passes(self, db_session, factories):
        context, schema, parameter, value = self._world(db_session, factories)
        row = _context_value(
            db_session, context, schema, parameter, source="name", value_id=value.id
        )
        assert row.value_id == value.id

    def test_an_unknown_source_is_rejected(self, db_session, factories):
        context, schema, parameter, _value_row = self._world(db_session, factories)
        with rejected_by(db_session, "ck_context_parameter_values_source"):
            _context_value(db_session, context, schema, parameter, source="guess")

    def test_the_refresh_mark_passes_on_a_list_row_and_on_an_empty_row(
        self, db_session, factories
    ):
        context, schema, parameter, value = self._world(db_session, factories)
        row = _context_value(
            db_session, context, schema, parameter, source="name", value_id=value.id,
            refresh_requested_at=_now(),
        )
        other = _param(db_session, schema, 2)
        empty = _context_value(
            db_session, context, schema, other, source="none", refresh_requested_at=_now()
        )
        assert row.refresh_requested_at is not None
        assert empty.refresh_requested_at is not None


# ---------------------------------------------------------------------------
#  6. Задание: номера параметров частичного запроса — только у значений контекста
# ---------------------------------------------------------------------------

class TestRequestedOrdinals:
    def test_a_values_job_may_carry_ordinals(self, db_session, factories):
        family, schema, *_ = _one_schema_world(db_session, factories)
        context = _assigned_context(db_session, factories, family)
        job = _job(
            db_session, factories, context, kind="context_values", family_id=family.id,
            schema_id=schema.id, requested_ordinals=[1, 2],
        )
        db_session.refresh(job)
        assert job.requested_ordinals == [1, 2]

    def test_a_values_job_without_ordinals_passes(self, db_session, factories):
        family, schema, *_ = _one_schema_world(db_session, factories)
        context = _assigned_context(db_session, factories, family)
        job = _job(
            db_session, factories, context, kind="context_values", family_id=family.id,
            schema_id=schema.id,
        )
        assert job.requested_ordinals is None

    def test_a_suggestion_job_with_ordinals_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_semantic_jobs_requested_ordinals"):
            _job(db_session, factories, requested_ordinals=[1])

    def test_a_schema_job_with_ordinals_is_rejected(self, db_session, factories):
        family, schema, *_ = _one_schema_world(db_session, factories)
        with rejected_by(db_session, "ck_semantic_jobs_requested_ordinals"):
            _job(
                db_session, factories, kind="family_schema", context_id=None,
                family_id=family.id, schema_id=schema.id, requested_ordinals=[1],
            )

    def test_a_discovery_job_with_ordinals_is_rejected(self, db_session, factories):
        with rejected_by(db_session, "ck_semantic_jobs_requested_ordinals"):
            _job(
                db_session, factories, kind="family_discovery", context_id=None,
                requested_ordinals=[1],
            )

    def test_ordinals_do_not_enter_the_request_hash_index(self, db_session):
        """В уникальный индекс заданий колонка не входит: подмножество — часть
        тела запроса и потому его хэша."""
        definition = db_session.execute(
            sa.text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_semantic_jobs_subject_request_hash'"
            )
        ).scalar_one()
        assert "requested_ordinals" not in definition


# ---------------------------------------------------------------------------
#  7. Журнал: тип `family_schema_edited`, предмет — семья
# ---------------------------------------------------------------------------

_EDIT_PAYLOAD = {
    "schema_id": 3, "revision": 1, "changes": [{"op": "rename", "ordinal": 1}],
    "variants_before": 4, "variants_after": 4, "jobs_enqueued": 0,
}


class TestSchemaEditedEvent:
    def test_a_family_subject_passes(self, db_session):
        family = _family(db_session)
        assert _event(db_session, "family_schema_edited", family_id=family.id).id is not None

    def test_a_context_subject_is_rejected(self, db_session, factories):
        context = _context(db_session, factories)
        with rejected_by(db_session, "ck_semantic_events_subject_by_type"):
            _event(db_session, "family_schema_edited", context_id=context.id)

    def test_the_type_is_in_the_closed_list(self):
        assert "family_schema_edited" in SEMANTIC_EVENT_TYPES
        assert "family_schema_edited" in FAMILY_EVENT_TYPES

    def test_an_unknown_type_is_still_rejected(self, db_session):
        family = _family(db_session)
        with rejected_by(db_session, "ck_semantic_events_event_type"):
            _event(db_session, "family_schema_changed", family_id=family.id)

    def test_the_required_keys_are_exactly_the_six_of_the_edit(self):
        assert EVENT_REQUIRED_KEYS["family_schema_edited"] == frozenset(
            {
                "schema_id", "revision", "changes", "variants_before", "variants_after",
                "jobs_enqueued",
            }
        )

    def test_a_full_payload_is_written(self, db_session):
        family = _family(db_session)
        event = record_event(
            db_session, event_type="family_schema_edited", payload=dict(_EDIT_PAYLOAD),
            family_id=family.id,
        )
        assert (event.event_type, event.family_id, event.context_id) == (
            "family_schema_edited", family.id, None,
        )
        assert event.payload == _EDIT_PAYLOAD

    @pytest.mark.parametrize("key", sorted(_EDIT_PAYLOAD))
    def test_a_payload_without_a_key_is_refused_by_name(self, db_session, key):
        family = _family(db_session)
        payload = {k: v for k, v in _EDIT_PAYLOAD.items() if k != key}
        with pytest.raises(SemanticEventError, match=key):
            record_event(
                db_session, event_type="family_schema_edited", payload=payload,
                family_id=family.id,
            )
        assert db_session.query(SemanticEvent).filter_by(
            event_type="family_schema_edited"
        ).count() == 0

    def test_a_context_subject_is_refused_before_the_database(self, db_session, factories):
        context = _context(db_session, factories)
        with pytest.raises(SemanticEventError):
            record_event(
                db_session, event_type="family_schema_edited", payload=dict(_EDIT_PAYLOAD),
                context_id=context.id,
            )


# ---------------------------------------------------------------------------
#  8. Parity: литералы миграции = models.py = независимый литерал
# ---------------------------------------------------------------------------

_RANGE_SHAPE_LITERAL = (
    "(value_kind = 'exact' AND range_from IS NULL AND range_to IS NULL "
    "AND range_step IS NULL) "
    "OR (value_kind = 'range' AND range_from IS NOT NULL AND range_to IS NOT NULL "
    "AND range_step IS NOT NULL AND range_step > 0 AND range_from < range_to "
    "AND mod(range_to - range_from, range_step) = 0 "
    "AND (range_to - range_from) / range_step <= 28 AND closed)"
)

_EVENT_TYPES_LITERAL = {
    "context_created", "context_split", "context_merged", "members_moved",
    "members_marked_stale", "kind_set", "name_role_set", "context_family_assigned",
    "context_archived", "routing_rules_dropped", "family_created", "family_updated",
    "family_activated", "family_archived", "family_merged",
    "context_variant_assigned", "context_family_pending", "context_not_work",
    "family_schema_frozen", "family_schema_value_added", "family_variants_merged",
    "context_reopened", "family_schema_edited",
}


class TestParityWithMigration:
    _IN_CASES = [
        (PARAMETER_ROLES, "PARAMETER_ROLES", {"main", "secondary"}),
        (PARAMETER_VALUE_KINDS, "PARAMETER_VALUE_KINDS", {"exact", "range"}),
        (PARAMETER_ORIGINS, "PARAMETER_ORIGINS", {"model", "human"}),
        (
            VALUE_SOURCES, "VALUE_SOURCES",
            {"name", "path", "manual", "path_conflict", "none", "off_list"},
        ),
        (SEMANTIC_EVENT_TYPES_SQL, "SEMANTIC_EVENT_TYPES_SQL", _EVENT_TYPES_LITERAL),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "name", "independent"), _IN_CASES, ids=[case[1] for case in _IN_CASES]
    )
    def test_in_list_matches_migration_and_independent_literal(
        self, model_expr, name, independent
    ):
        assert model_expr == getattr(_migration_0022(), name)
        parsed = {piece.strip().strip("'") for piece in model_expr.split(",")}
        assert parsed == independent
        assert len(parsed) == len(model_expr.split(","))

    _CK_CASES = [
        (CK_PARAMETER_ORDINAL_RANGE, "CK_PARAMETER_ORDINAL_RANGE", "ordinal BETWEEN 1 AND 100"),
        (CK_PARAMETER_ROLE, "CK_PARAMETER_ROLE", "role IN ('main', 'secondary')"),
        (CK_PARAMETER_VALUE_KIND, "CK_PARAMETER_VALUE_KIND", "value_kind IN ('exact', 'range')"),
        (CK_PARAMETER_ORIGIN, "CK_PARAMETER_ORIGIN", "origin IN ('model', 'human')"),
        (CK_PARAMETER_RANGE_SHAPE, "CK_PARAMETER_RANGE_SHAPE", _RANGE_SHAPE_LITERAL),
        (
            CK_PARAMETER_VALUE_RANGE_ORDER, "CK_PARAMETER_VALUE_RANGE_ORDER",
            "range_lo IS NULL OR range_hi IS NULL OR range_lo < range_hi",
        ),
        (
            CK_PARAMETER_VALUE_RETIRED_NOT_MERGED, "CK_PARAMETER_VALUE_RETIRED_NOT_MERGED",
            "retired_at IS NULL OR merged_into_id IS NULL",
        ),
        (
            CK_CONTEXT_VALUE_SOURCE_PAIR, "CK_CONTEXT_VALUE_SOURCE_PAIR",
            "(value_id IS NULL) = (source IN ('path_conflict', 'none', 'off_list'))",
        ),
        (
            CK_CONTEXT_VALUE_OFF_LIST_TEXT, "CK_CONTEXT_VALUE_OFF_LIST_TEXT",
            "(source = 'off_list') = (off_list_text IS NOT NULL)",
        ),
        (
            CK_SEMANTIC_JOBS_REQUESTED_ORDINALS, "CK_SEMANTIC_JOBS_REQUESTED_ORDINALS",
            "requested_ordinals IS NULL OR kind = 'context_values'",
        ),
        (
            CK_EVENT_SUBJECT_BY_TYPE, "CK_EVENT_SUBJECT_BY_TYPE",
            "(event_type IN ('family_created', 'family_updated', 'family_activated', "
            "'family_archived', 'family_merged', 'family_schema_frozen', "
            "'family_schema_value_added', 'family_variants_merged', "
            "'family_schema_edited')) = (family_id IS NOT NULL)",
        ),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "name", "independent"), _CK_CASES, ids=[case[1] for case in _CK_CASES]
    )
    def test_check_expression_matches_migration_and_independent_literal(
        self, model_expr, name, independent
    ):
        assert model_expr == getattr(_migration_0022(), name)
        assert model_expr == independent

    def test_the_enums_equal_the_lists(self):
        assert {m.value for m in ParameterRole} == {"main", "secondary"}
        assert {m.value for m in ParameterValueKind} == {"exact", "range"}
        assert {m.value for m in ParameterOrigin} == {"model", "human"}
        assert ValueSource.off_list.value == "off_list"

    def test_the_family_subject_set_matches_the_independent_literal(self):
        parsed = {
            piece.strip().strip("'")
            for piece in CK_EVENT_SUBJECT_BY_TYPE.split("event_type IN (")[1]
            .split(")")[0]
            .split(",")
        }
        assert parsed == {
            "family_created", "family_updated", "family_activated", "family_archived",
            "family_merged", "family_schema_frozen", "family_schema_value_added",
            "family_variants_merged", "family_schema_edited",
        }
        assert parsed == set(FAMILY_EVENT_TYPES)

    def test_the_closed_event_list_has_twenty_three_types(self):
        assert len(SEMANTIC_EVENT_TYPES) == 23
        assert set(SEMANTIC_EVENT_TYPES) == _EVENT_TYPES_LITERAL

    def test_the_previous_literals_kept_for_downgrade_equal_the_earlier_migrations(self):
        m22, m21, m19 = _migration_0022(), _migration_0021(), _migration_0019()
        assert m22.PREVIOUS_CK_PARAMETER_ORDINAL_RANGE == m19.CK_PARAMETER_ORDINAL_RANGE
        assert m22.PREVIOUS_CK_CONTEXT_VALUE_SOURCE_PAIR == m19.CK_CONTEXT_VALUE_SOURCE_PAIR
        assert m22.PREVIOUS_VALUE_SOURCES == m19.VALUE_SOURCES
        assert m22.PREVIOUS_SEMANTIC_EVENT_TYPES_SQL == m21.SEMANTIC_EVENT_TYPES_SQL
        assert m22.PREVIOUS_CK_EVENT_SUBJECT_BY_TYPE == m19.CK_EVENT_SUBJECT_BY_TYPE

    _TABLE_CHECKS = {
        "family_parameters": {
            "ck_family_parameters_ordinal_range", "ck_family_parameters_name_not_blank",
            "ck_family_parameters_role", "ck_family_parameters_value_kind",
            "ck_family_parameters_origin", "ck_family_parameters_range_shape",
        },
        "family_parameter_values": {
            "ck_family_parameter_values_origin", "ck_family_parameter_values_value_not_blank",
            "ck_family_parameter_values_not_self_merged",
            "ck_family_parameter_values_range_order",
            "ck_family_parameter_values_retired_not_merged",
        },
        "context_parameter_values": {
            "ck_context_parameter_values_source",
            "ck_context_parameter_values_source_value_pair",
            "ck_context_parameter_values_off_list_text",
        },
    }

    @pytest.mark.parametrize("table", sorted(_TABLE_CHECKS))
    def test_the_live_checks_of_the_table_are_named_as_expected(self, db_session, table):
        assert _live_constraint_names(db_session, table, "c") == self._TABLE_CHECKS[table]

    @pytest.mark.parametrize("table", sorted(_TABLE_CHECKS))
    def test_the_model_declares_the_same_checks_as_the_database(self, table):
        from models import Base

        declared = {
            constraint.name
            for constraint in Base.metadata.tables[table].constraints
            if isinstance(constraint, sa.CheckConstraint)
        }
        assert declared == self._TABLE_CHECKS[table]

    def test_the_jobs_check_exists_in_the_database_and_in_the_model(self, db_session):
        from models import Base

        name = "ck_semantic_jobs_requested_ordinals"
        assert name in _live_constraint_names(db_session, "semantic_jobs", "c")
        assert name in {
            constraint.name for constraint in Base.metadata.tables["semantic_jobs"].constraints
        }


# ---------------------------------------------------------------------------
#  9. Живые прогоны alembic на scratch-базе
# ---------------------------------------------------------------------------

_TABLES_WITH_DATA = {
    "family_parameter_schemas": "id",
    "family_parameters": "id",
    "family_parameter_values": "id",
    "context_parameter_values": "context_id, parameter_id",
    "semantic_jobs": "id",
    "semantic_events": "id",
    "work_variants": "id",
    "work_variant_values": "variant_id, parameter_id",
}


def _seed_world(conn) -> dict:
    """Данные всех шести таблиц §2.2, вариантов и заданий четырёх видов — в
    колонках, существующих до `0022` (поэтому годится и на схеме `0021`, и на
    `head`)."""
    seeded = _seed_three_job_kinds(conn)
    family_id = seeded["family"]
    schema_id = conn.execute(
        sa.text(
            "SELECT id FROM family_parameter_schemas WHERE family_id = :f AND status = 'frozen'"
        ),
        {"f": family_id},
    ).scalar_one()
    context_id = conn.execute(
        sa.text(
            "SELECT context_id FROM semantic_jobs WHERE request_hash = 'job-values'"
        )
    ).scalar_one()
    parameter_ids = [
        conn.execute(
            sa.text(
                "INSERT INTO family_parameters (schema_id, ordinal, name, name_norm) "
                "VALUES (:s, :o, :n, :n) RETURNING id"
            ),
            {"s": schema_id, "o": ordinal, "n": name},
        ).scalar_one()
        for ordinal, name in ((1, "толщина"), (2, "марка"))
    ]
    value_ids = [
        conn.execute(
            sa.text(
                "INSERT INTO family_parameter_values (parameter_id, value, value_norm, origin) "
                "VALUES (:p, :v, :v, 'schema') RETURNING id"
            ),
            {"p": parameter_ids[0] if index < 2 else parameter_ids[1], "v": value},
        ).scalar_one()
        for index, value in enumerate(("100 мм", "150 мм", "м300"))
    ]
    variant_id = conn.execute(
        sa.text(
            "INSERT INTO work_variants (family_id, schema_id, values_key, status) "
            "VALUES (:f, :s, :k, 'active') RETURNING id"
        ),
        {"f": family_id, "s": schema_id, "k": f"1={value_ids[0]};2={value_ids[2]}"},
    ).scalar_one()
    for parameter_id, value_id in ((parameter_ids[0], value_ids[0]), (parameter_ids[1], value_ids[2])):
        conn.execute(
            sa.text(
                "INSERT INTO work_variant_values (variant_id, schema_id, parameter_id, value_id) "
                "VALUES (:v, :s, :p, :val)"
            ),
            {"v": variant_id, "s": schema_id, "p": parameter_id, "val": value_id},
        )
    for parameter_id, value_id, source in (
        (parameter_ids[0], value_ids[0], "name"), (parameter_ids[1], None, "none"),
    ):
        conn.execute(
            sa.text(
                "INSERT INTO context_parameter_values "
                "(context_id, schema_id, parameter_id, value_id, source) "
                "VALUES (:c, :s, :p, :v, :src)"
            ),
            {"c": context_id, "s": schema_id, "p": parameter_id, "v": value_id, "src": source},
        )
    columns = (
        "kind, context_id, family_id, schema_id, request_hash, status, prompt_version, "
        "model_requested, place_dictionary_version, candidates_hash, prefix_hash, input_hash, "
        "response_schema_version, serialization_version"
    )
    conn.execute(
        sa.text(
            f"INSERT INTO semantic_jobs ({columns}) VALUES "
            "('family_discovery', NULL, NULL, NULL, 'job-discovery', 'pending', '1', 'm', 1, "
            "'c', 'p', 'i', '1', '1')"
        )
    )
    for event_type, subject_column, subject_id in (
        ("family_schema_frozen", "family_id", family_id),
        ("context_created", "context_id", context_id),
    ):
        conn.execute(
            sa.text(
                f"INSERT INTO semantic_events ({subject_column}, event_type, payload) "
                "VALUES (:s, :t, CAST('{\"k\": 1}' AS jsonb))"
            ),
            {"s": subject_id, "t": event_type},
        )
    return {
        "family": family_id, "schema": schema_id, "context": context_id,
        "parameters": parameter_ids, "values": value_ids, "variant": variant_id,
    }


def _column_names(conn, table: str) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :t ORDER BY ordinal_position"
            ),
            {"t": table},
        )
    ]


def _snapshot(conn, columns_by_table: dict[str, list[str]]) -> dict:
    return {
        table: [
            tuple(row)
            for row in conn.execute(
                sa.text(
                    f"SELECT {', '.join(columns)} FROM {table} "
                    f"ORDER BY {_TABLES_WITH_DATA[table]}"
                )
            )
        ]
        for table, columns in columns_by_table.items()
    }


@pytest.mark.migration_roundtrip
class TestUpgradeOnData:
    def test_upgrade_changes_no_existing_row_and_fills_the_defaults(self):
        with _scratch_alembic("0022 upgrade on data") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0021")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    seeded = _seed_world(conn)
                with engine.connect() as conn:
                    columns = {table: _column_names(conn, table) for table in _TABLES_WITH_DATA}
                    before = _snapshot(conn, columns)
                for table, rows in before.items():
                    assert rows, f"предусловие: в {table} должны быть строки до upgrade"
                kinds = {row[columns["semantic_jobs"].index("kind")] for row in before["semantic_jobs"]}
                assert kinds == {
                    "family_suggestion", "family_schema", "context_values", "family_discovery",
                }
                hashes_before = {
                    row[columns["semantic_jobs"].index("request_hash")]
                    for row in before["semantic_jobs"]
                }
                assert hashes_before == {
                    "job-suggestion", "job-schema", "job-values", "job-discovery",
                }

                command.upgrade(cfg, "head")

                with engine.connect() as conn:
                    after = _snapshot(conn, columns)
                    parameters = conn.execute(
                        sa.text(
                            "SELECT role, value_kind, closed, range_from, range_to, range_step, "
                            "origin, archived_at FROM family_parameters"
                        )
                    ).all()
                    revisions = conn.execute(
                        sa.text("SELECT revision FROM family_parameter_schemas")
                    ).scalars().all()
                    value_extras = conn.execute(
                        sa.text(
                            "SELECT range_lo, range_hi, retired_at FROM family_parameter_values"
                        )
                    ).all()
                    context_extras = conn.execute(
                        sa.text(
                            "SELECT off_list_text, refresh_requested_at "
                            "FROM context_parameter_values"
                        )
                    ).all()
                    job_extras = conn.execute(
                        sa.text("SELECT requested_ordinals FROM semantic_jobs")
                    ).scalars().all()
                    keys = conn.execute(
                        sa.text("SELECT values_key FROM work_variants")
                    ).scalars().all()
                assert after == before
                assert keys == [f"1={seeded['values'][0]};2={seeded['values'][2]}"]
                assert len(parameters) == 2
                assert all(
                    tuple(row) == ("main", "exact", False, None, None, None, "model", None)
                    for row in parameters
                )
                assert revisions == [0, 0]
                assert all(tuple(row) == (None, None, None) for row in value_extras)
                assert all(tuple(row) == (None, None) for row in context_extras)
                assert job_extras == [None] * len(before["semantic_jobs"])
            finally:
                engine.dispose()

    def test_upgrade_on_an_empty_database_reaches_the_new_checks(self):
        with _scratch_alembic("0022 upgrade on empty") as (command, cfg, scratch_url):
            command.upgrade(cfg, "head")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    revision = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                    ordinal = conn.execute(
                        sa.text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conname = 'ck_family_parameters_ordinal_range'"
                        )
                    ).scalar_one()
                assert revision == "0022"
                assert "100" in ordinal
            finally:
                engine.dispose()


def _constraint_state(conn) -> dict:
    """Ограничения, колонки и индексы всех затронутых таблиц — для оракула «до»."""
    tables = (
        "family_parameter_schemas", "family_parameters", "family_parameter_values",
        "context_parameter_values", "semantic_jobs", "semantic_events",
    )
    quoted = ", ".join(f"'{table}'::regclass" for table in tables)
    constraints = conn.execute(
        sa.text(
            "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
            f"FROM pg_constraint WHERE conrelid IN ({quoted})"
        )
    ).all()
    indexes = conn.execute(
        sa.text(
            "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE tablename IN "
            f"({', '.join(repr(table) for table in tables)})"
        )
    ).all()
    columns = conn.execute(
        sa.text(
            "SELECT table_name, column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name IN "
            f"({', '.join(repr(table) for table in tables)})"
        )
    ).all()
    return {
        "constraints": {tuple(row) for row in constraints},
        "indexes": {tuple(row) for row in indexes},
        "columns": {tuple(row) for row in columns},
    }


@pytest.mark.migration_roundtrip
class TestDowngradeOnEmptyDatabase:
    def test_downgrade_restores_every_constraint_column_and_index_of_0021(self):
        """Оракул — сама схема 0021, снятая до `upgrade`, а не литералы миграции."""
        with _scratch_alembic("0022 restores 0021") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0021")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    before = _constraint_state(conn)
                command.upgrade(cfg, "head")
                with engine.connect() as conn:
                    upgraded = _constraint_state(conn)
                assert upgraded != before  # предусловие: upgrade действительно менял эти таблицы
                command.downgrade(cfg, "0021")
                with engine.connect() as conn:
                    after = _constraint_state(conn)
                    revision = conn.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
            finally:
                engine.dispose()
        assert after == before
        assert revision == "0021"

    def test_downgrade_returns_the_three_literals_and_upgrade_applies_them_again(self):
        with _scratch_alembic("0022 literals round trip") as (command, cfg, scratch_url):
            command.upgrade(cfg, "head")
            command.downgrade(cfg, "0021")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    definitions = dict(
                        conn.execute(
                            sa.text(
                                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                                "WHERE conname IN ('ck_family_parameters_ordinal_range', "
                                "'ck_context_parameter_values_source_value_pair', "
                                "'ck_semantic_events_event_type')"
                            )
                        ).all()
                    )
                assert "ordinal <= 3" in definitions["ck_family_parameters_ordinal_range"]
                assert "off_list" not in definitions["ck_context_parameter_values_source_value_pair"]
                assert "family_schema_edited" not in definitions["ck_semantic_events_event_type"]
                assert "context_reopened" in definitions["ck_semantic_events_event_type"]

                command.upgrade(cfg, "head")
                with engine.connect() as conn:
                    columns = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM information_schema.columns WHERE "
                            "(table_name, column_name) IN (('family_parameters', 'role'), "
                            "('family_parameter_schemas', 'revision'), "
                            "('semantic_jobs', 'requested_ordinals'))"
                        )
                    ).scalar_one()
                assert columns == 3
            finally:
                engine.dispose()


# ---------------------------------------------------------------------------
#  10. downgrade: носители — чистая логика, счётчики на живой схеме, отказ
# ---------------------------------------------------------------------------

_COUNTER_PHRASES = {
    "family_parameters": "параметров с новыми свойствами — 5",
    "family_parameter_schemas": "версий схем с правками на месте — 5",
    "family_parameter_values": "значений с границами или выведенных из списка — 5",
    "context_parameter_values": "строк контекстов не из списка или с отметкой — 5",
    "semantic_jobs_requested_ordinals": "заданий с частичным запросом — 5",
    "semantic_events_schema_edited": "событий family_schema_edited — 5",
}


class TestDowngradeRefusalLogic:
    """`_downgrade_refusal` — чистая логика на вручную построенных словарях:
    каждый счётчик — единственный положительный, значение (5) отличительное."""

    _ZERO = {key: 0 for key in _COUNTER_PHRASES}

    def test_there_are_six_counters(self):
        assert len(_COUNTER_PHRASES) == 6

    @pytest.mark.parametrize("key", sorted(_COUNTER_PHRASES))
    def test_each_counter_alone_blocks_and_is_named(self, key):
        blockers = dict(self._ZERO)
        blockers[key] = 5
        refusal = _migration_0022()._downgrade_refusal(blockers)
        assert refusal is not None
        assert _COUNTER_PHRASES[key] in refusal
        for other, phrase in _COUNTER_PHRASES.items():
            if other != key:
                assert phrase not in refusal

    def test_all_zero_blockers_pass(self):
        assert _migration_0022()._downgrade_refusal(dict(self._ZERO)) is None


#: Носитель → (операция над засеянным миром, счётчик, который её видит). Каждый
#: непустой носитель §2.2, названный в спеке; операции — над колонками `0022`.
_CARRIERS = {
    "parameter_secondary": (
        "UPDATE family_parameters SET role = 'secondary' WHERE id = :p1", "family_parameters",
    ),
    "parameter_range": (
        "UPDATE family_parameters SET value_kind = 'range', closed = true, range_from = 100, "
        "range_to = 140, range_step = 20 WHERE id = :p1",
        "family_parameters",
    ),
    "parameter_closed": (
        "UPDATE family_parameters SET closed = true WHERE id = :p1", "family_parameters",
    ),
    "parameter_human": (
        "UPDATE family_parameters SET origin = 'human' WHERE id = :p1", "family_parameters",
    ),
    "parameter_archived": (
        "UPDATE family_parameters SET archived_at = now() WHERE id = :p1", "family_parameters",
    ),
    "parameter_ordinal_over_three": (
        "UPDATE family_parameters SET ordinal = 4 WHERE id = :p1", "family_parameters",
    ),
    "schema_revision": (
        "UPDATE family_parameter_schemas SET revision = 1 WHERE id = :schema",
        "family_parameter_schemas",
    ),
    "value_with_bounds": (
        "UPDATE family_parameter_values SET range_lo = 10, range_hi = 20 WHERE id = :v1",
        "family_parameter_values",
    ),
    # «до …» и «… и более» — значения с ОДНОЙ границей: каждый атом счётчика
    # значений предъявлен своим входом (ревью задачи 1).
    "value_open_left": (
        "UPDATE family_parameter_values SET range_hi = 120 WHERE id = :v1",
        "family_parameter_values",
    ),
    "value_open_right": (
        "UPDATE family_parameter_values SET range_lo = 200 WHERE id = :v1",
        "family_parameter_values",
    ),
    "value_retired": (
        "UPDATE family_parameter_values SET retired_at = now() WHERE id = :v2",
        "family_parameter_values",
    ),
    "context_off_list": (
        "UPDATE context_parameter_values SET source = 'off_list', value_id = NULL, "
        "off_list_text = '105 мм' WHERE parameter_id = :p1",
        "context_parameter_values",
    ),
    "context_refresh_mark": (
        "UPDATE context_parameter_values SET refresh_requested_at = now() "
        "WHERE parameter_id = :p1",
        "context_parameter_values",
    ),
    "job_requested_ordinals": (
        "UPDATE semantic_jobs SET requested_ordinals = ARRAY[1]::smallint[] "
        "WHERE kind = 'context_values'",
        "semantic_jobs_requested_ordinals",
    ),
    "event_schema_edited": (
        "INSERT INTO semantic_events (family_id, event_type, payload) "
        "VALUES (:family, 'family_schema_edited', CAST('{\"k\": 1}' AS jsonb))",
        "semantic_events_schema_edited",
    ),
}


def _apply_carrier(conn, seeded: dict, carrier: str) -> str:
    statement, counter = _CARRIERS[carrier]
    conn.execute(
        sa.text(statement),
        {
            "p1": seeded["parameters"][0], "schema": seeded["schema"],
            "v1": seeded["values"][0], "v2": seeded["values"][1], "family": seeded["family"],
        },
    )
    return counter


class TestDowngradeBlockersLive:
    """Каждый носитель — свой вход на живой схеме: считается ровно его счётчик,
    остальные нули, отказ называет носитель."""

    def _blockers(self, db_session):
        migration = _migration_0022()
        blockers = migration._downgrade_blockers(db_session.connection())
        return blockers, migration._downgrade_refusal(blockers)

    def test_the_seeded_world_does_not_block(self, db_session):
        _seed_world(db_session.connection())
        blockers, refusal = self._blockers(db_session)
        assert blockers == {key: 0 for key in _COUNTER_PHRASES}
        assert refusal is None

    @pytest.mark.parametrize("carrier", sorted(_CARRIERS))
    def test_each_carrier_alone_raises_exactly_its_counter(self, db_session, carrier):
        conn = db_session.connection()
        seeded = _seed_world(conn)
        counter = _apply_carrier(conn, seeded, carrier)
        blockers, refusal = self._blockers(db_session)
        assert blockers == {key: (1 if key == counter else 0) for key in _COUNTER_PHRASES}
        assert _COUNTER_PHRASES[counter].replace("5", "1") in refusal

    def test_counters_count_rows(self, db_session):
        conn = db_session.connection()
        seeded = _seed_world(conn)
        conn.execute(
            sa.text("UPDATE family_parameters SET role = 'secondary' WHERE schema_id = :s"),
            {"s": seeded["schema"]},
        )
        blockers, _ = self._blockers(db_session)
        assert blockers["family_parameters"] == 2


class TestDowngradeBlockerAtomsBeyondTheChecks:
    """Два атома счётчиков равносильны соседним при действующих CHECK: диапазон
    всегда `closed` (`ck_family_parameters_range_shape`), а `off_list` ⇔ текст
    (`ck_context_parameter_values_off_list_text`). Носитель «диапазон» и пара
    «источник / текст» названы спекой §2.2 отдельно, поэтому каждый атом
    предъявлен своим входом: ограничение, которое его маскирует, снято в этой же
    транзакции (откат вернёт), и вход видит ТОЛЬКО проверяемый атом (ревью
    задачи 1)."""

    def _only(self, db_session, counter: str) -> None:
        blockers = _migration_0022()._downgrade_blockers(db_session.connection())
        assert blockers == {key: (1 if key == counter else 0) for key in _COUNTER_PHRASES}

    def test_a_range_parameter_that_is_not_closed_is_counted_by_its_kind(self, db_session):
        conn = db_session.connection()
        seeded = _seed_world(conn)
        conn.execute(
            sa.text(
                "ALTER TABLE family_parameters DROP CONSTRAINT ck_family_parameters_range_shape"
            )
        )
        conn.execute(
            sa.text(
                "UPDATE family_parameters SET value_kind = 'range', closed = false, "
                "range_from = 100, range_to = 140, range_step = 20 WHERE id = :p"
            ),
            {"p": seeded["parameters"][0]},
        )
        self._only(db_session, "family_parameters")

    def test_an_off_list_source_without_text_is_counted(self, db_session):
        conn = db_session.connection()
        seeded = _seed_world(conn)
        conn.execute(
            sa.text(
                "ALTER TABLE context_parameter_values "
                "DROP CONSTRAINT ck_context_parameter_values_off_list_text"
            )
        )
        conn.execute(
            sa.text(
                "UPDATE context_parameter_values SET source = 'off_list', value_id = NULL "
                "WHERE parameter_id = :p"
            ),
            {"p": seeded["parameters"][0]},
        )
        self._only(db_session, "context_parameter_values")

    def test_off_list_text_on_another_source_is_counted(self, db_session):
        conn = db_session.connection()
        seeded = _seed_world(conn)
        conn.execute(
            sa.text(
                "ALTER TABLE context_parameter_values "
                "DROP CONSTRAINT ck_context_parameter_values_off_list_text"
            )
        )
        conn.execute(
            sa.text(
                "UPDATE context_parameter_values SET off_list_text = '105 мм' "
                "WHERE parameter_id = :p AND source = 'none'"
            ),
            {"p": seeded["parameters"][1]},
        )
        self._only(db_session, "context_parameter_values")


@pytest.mark.migration_roundtrip
class TestDowngradeOnDataWithoutCarriers:
    def test_downgrade_passes_on_data_with_every_carrier_empty_and_keeps_the_rows(self):
        """Данные есть во всех шести таблицах, носителей нет: откат проходит,
        прежние CHECK встают поверх существующих строк, строки не теряются
        (ревью задачи 1)."""
        with _scratch_alembic("0022 downgrade on data") as (command, cfg, scratch_url):
            command.upgrade(cfg, "head")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    _seed_world(conn)
                new = {(row[0], row[1]) for row in _NEW_COLUMNS}
                with engine.connect() as conn:
                    kept = {
                        table: [c for c in _column_names(conn, table) if (table, c) not in new]
                        for table in _TABLES_WITH_DATA
                    }
                    before = _snapshot(conn, kept)
                command.downgrade(cfg, "0021")
                with engine.connect() as conn:
                    old_columns = {
                        table: _column_names(conn, table) for table in _TABLES_WITH_DATA
                    }
                    after = _snapshot(conn, kept)
                    revision = conn.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
            finally:
                engine.dispose()
        assert revision == "0021"
        assert old_columns == kept  # откат снял ровно колонки 0022
        for table, rows in before.items():
            assert rows, f"предусловие: в {table} есть строки до отката"
        assert after == before


@pytest.mark.migration_roundtrip
class TestDowngradeRefusedByEachCarrier:
    @pytest.mark.parametrize("carrier", sorted(_CARRIERS))
    def test_downgrade_is_refused_and_leaves_the_schema_untouched(self, carrier):
        with _scratch_alembic(f"0022 downgrade refusal {carrier}") as (
            command, cfg, scratch_url,
        ):
            command.upgrade(cfg, "head")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    seeded = _seed_world(conn)
                    counter = _apply_carrier(conn, seeded, carrier)

                with pytest.raises(RuntimeError, match="Откат 0022 невозможен") as exc:
                    command.downgrade(cfg, "0021")

                assert _COUNTER_PHRASES[counter].replace("5", "1") in str(exc.value)
                with engine.connect() as conn:
                    revision = conn.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    ordinal = conn.execute(
                        sa.text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conname = 'ck_family_parameters_ordinal_range'"
                        )
                    ).scalar_one()
                    column = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM information_schema.columns "
                            "WHERE table_name = 'family_parameters' AND column_name = 'role'"
                        )
                    ).scalar_one()
                assert (revision, column) == ("0022", 1)
                assert "100" in ordinal
            finally:
                engine.dispose()
