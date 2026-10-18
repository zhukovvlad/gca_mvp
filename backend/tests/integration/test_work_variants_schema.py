"""Схема вариантов работ и схем параметров семей (миграция 0019; спека
`2026-10-02-catalog-variants-design.md` §2.4): каждое ограничение доказано
пробоем — `INSERT` с ожиданием `IntegrityError`, по одному нарушенному
ограничению на вход, и соседним допустимым входом. Отложенные FK — на
настоящем `commit`; перевод очереди и пачек на новый формат, `upgrade` на
данных фичи 2 и `downgrade` — на отдельной scratch-базе.

Помощник `rejected` сбрасывает сессию (`begin_nested` делает `flush`), поэтому
строка, которую ожидаем отвергнутой, создаётся и добавляется ВНУТРИ блока
`with rejected(...)`.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from models import (
    CK_CONTEXT_FAMILY_PROVENANCE,
    CK_CONTEXT_PENDING,
    CK_CONTEXT_SPLIT_HINT_NEEDS_VARIANT,
    CK_CONTEXT_VALUE_SOURCE_PAIR,
    CK_CONTEXT_VARIANT_AT_PAIR,
    CK_CONTEXT_VARIANT_NEEDS_FAMILY,
    CK_CONTEXT_VARIANT_PATHS_HASH_PAIR,
    CK_EVENT_SUBJECT_BY_TYPE,
    CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR,
    CK_PARAMETER_NAME_NOT_BLANK,
    CK_PARAMETER_ORDINAL_RANGE,
    CK_PARAMETER_VALUE_NOT_BLANK,
    CK_PARAMETER_VALUE_NOT_SELF_MERGED,
    CK_SCHEMA_CANCELLED_PAIR,
    CK_SCHEMA_FROZEN_AT_PAIR,
    CK_SCHEMA_ORIGIN_FROZEN_BY_PAIR,
    CK_SCHEMA_SUPERSEDED_AT_PAIR,
    CK_SEMANTIC_JOBS_CONTEXT_SUBJECT,
    CK_SEMANTIC_JOBS_RESULT_SUGGESTION_KIND,
    CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND,
    CK_SEMANTIC_JOBS_SCHEMA_SUBJECT,
    CK_VARIANT_ARCHIVED_PAIR,
    CK_VARIANT_MERGED_NEEDS_ARCHIVED,
    FAMILY_SOURCES,
    SCHEMA_ORIGINS,
    SCHEMA_STATUSES,
    SEMANTIC_EVENT_TYPES,
    SEMANTIC_EVENT_TYPES_SQL,
    SEMANTIC_JOB_KINDS,
    SUGGESTION_DECISIONS,
    VALUE_ORIGINS,
    VALUE_SOURCES,
    VARIANT_SPLIT_HINTS,
    VARIANT_STATUSES,
    CatalogContext,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    FamilyStatus,
    SemanticEvent,
    SemanticJob,
    WorkFamily,
    WorkVariant,
    WorkVariantValue,
)
from tests.integration.test_schema_constraints import rejected
from tests.integration.test_semantic_queue_schema import (
    _batch,
    _context,
    _job,
    _suggestion,
)

pytestmark = pytest.mark.integration


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _load_migration(glob: str, module_name: str):
    path = next(Path(__file__).resolve().parents[2].glob(f"alembic/versions/{glob}"))
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_0019():
    return _load_migration("*0019-work_variants.py", "_migration_0019")


def _migration_0017():
    return _load_migration("*0017-semantic_contour.py", "_migration_0017")


def _migration_0021():
    return _load_migration("*0021-catalog_discovery.py", "_migration_0021")


#: Литералы, которые миграция 0021 переписала: их текущее значение живёт в ней, а
#: не в 0019 (0019 хранит прежнее — для возврата `downgrade`).
_REWRITTEN_BY_0021 = frozenset(
    {
        "SEMANTIC_JOB_KINDS", "SEMANTIC_EVENT_TYPES_SQL",
        "CK_SEMANTIC_JOBS_CONTEXT_SUBJECT", "CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND",
    }
)


def _current_migration_for(name: str):
    return _migration_0021() if name in _REWRITTEN_BY_0021 else _migration_0019()


# ---------------------------------------------------------------------------
#  Помощники: цепочка семья → версия схемы → параметр → значение → вариант
# ---------------------------------------------------------------------------

def _family(session, **overrides) -> WorkFamily:
    defaults = dict(
        title=f"Семья {_uid()}", status=FamilyStatus.draft.value, seed_key=f"seed-{_uid()}"
    )
    defaults.update(overrides)
    family = WorkFamily(**defaults)
    session.add(family)
    session.flush()
    return family


def _schema(session, factories, family=None, *, version=1, status="frozen", origin="model",
            **overrides) -> FamilyParameterSchema:
    """Версия схемы: отметки времени ставятся по статусу, чтобы строка прошла
    свои равносильности; тест, проверяющий нарушение, переопределяет ровно одну."""
    family = family or _family(session)
    defaults = dict(
        family_id=family.id, version=version, status=status, origin=origin,
        frozen_at=_now() if status in ("frozen", "superseded") else None,
        superseded_at=_now() if status == "superseded" else None,
        cancelled_at=_now() if status == "cancelled" else None,
        frozen_by=factories.UserFactory.create().id if origin == "manual" else None,
    )
    defaults.update(overrides)
    schema = FamilyParameterSchema(**defaults)
    session.add(schema)
    session.flush()
    return schema


def _param(session, schema, ordinal=1, name=None) -> FamilyParameter:
    name = name or f"Параметр {_uid()}"
    parameter = FamilyParameter(
        schema_id=schema.id, ordinal=ordinal, name=name, name_norm=name.strip().lower()
    )
    session.add(parameter)
    session.flush()
    return parameter


def _value(session, parameter, value=None, origin="schema", **overrides) -> FamilyParameterValue:
    value = value if value is not None else f"значение {_uid()}"
    defaults = dict(
        parameter_id=parameter.id, value=value, value_norm=value.strip().lower(), origin=origin
    )
    defaults.update(overrides)
    row = FamilyParameterValue(**defaults)
    session.add(row)
    session.flush()
    return row


def _variant(session, family, schema, *, values_key=None, status="active", **overrides) -> WorkVariant:
    defaults = dict(
        family_id=family.id, schema_id=schema.id, values_key=values_key or f"k-{_uid()}",
        status=status, archived_at=_now() if status == "archived" else None,
    )
    defaults.update(overrides)
    variant = WorkVariant(**defaults)
    session.add(variant)
    session.flush()
    return variant


def _chain(session, factories):
    """Семья с текущей схемой из одного параметра с одним значением и вариантом
    на нём — минимальная допустимая цепочка."""
    family = _family(session)
    schema = _schema(session, factories, family)
    parameter = _param(session, schema, 1, "Толщина")
    value = _value(session, parameter, "100 мм")
    variant = _variant(session, family, schema, values_key=f"1={value.id}")
    return family, schema, parameter, value, variant


def _assigned_context(session, factories, family, **overrides) -> CatalogContext:
    """Контекст, которому назначена семья вручную (проходит провенанс-CHECK)."""
    user = factories.UserFactory.create()
    defaults = dict(
        work_family_id=family.id, family_source="manual", family_by=user.id, family_at=_now(),
    )
    defaults.update(overrides)
    return _context(session, factories, **defaults)


def _event(session, event_type, *, context_id=None, family_id=None) -> SemanticEvent:
    event = SemanticEvent(
        context_id=context_id, family_id=family_id, event_type=event_type, payload={"k": 1}
    )
    session.add(event)
    session.flush()
    return event


def _live_constraint_names(session, table: str, contype: str) -> set[str]:
    rows = session.execute(
        sa.text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = CAST(:t AS regclass) AND contype = :c"
        ),
        {"t": table, "c": contype},
    ).all()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
#  1. Шесть таблиц существуют — позитивный вход на каждую
# ---------------------------------------------------------------------------

class TestTablesExist:
    def test_schema_version_row(self, db_session, factories):
        assert _schema(db_session, factories).id is not None

    def test_parameter_row(self, db_session, factories):
        assert _param(db_session, _schema(db_session, factories)).id is not None

    def test_parameter_value_row(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories))
        assert _value(db_session, parameter).id is not None

    def test_variant_row(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        assert _variant(db_session, family, schema).id is not None

    def test_variant_value_row_with_value_and_without(self, db_session, factories):
        family, schema, parameter, value, variant = _chain(db_session, factories)
        db_session.add(
            WorkVariantValue(
                variant_id=variant.id, schema_id=schema.id, parameter_id=parameter.id,
                value_id=value.id,
            )
        )
        other = _variant(db_session, family, schema)
        db_session.add(
            WorkVariantValue(
                variant_id=other.id, schema_id=schema.id, parameter_id=parameter.id,
                value_id=None,
            )
        )
        db_session.flush()

    def test_context_value_row(self, db_session, factories):
        family, schema, parameter, value, _variant_row = _chain(db_session, factories)
        context = _assigned_context(db_session, factories, family)
        db_session.add(
            ContextParameterValue(
                context_id=context.id, schema_id=schema.id, parameter_id=parameter.id,
                value_id=value.id, source="name",
            )
        )
        db_session.flush()


# ---------------------------------------------------------------------------
#  2. Версия схемы: равносильности, статусы, частичные уникальные индексы
# ---------------------------------------------------------------------------

class TestSchemaVersionRules:
    def test_superseded_without_frozen_at_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_parameter_schemas_frozen_at_pair"'):
            _schema(db_session, factories, status="superseded", frozen_at=None)

    def test_building_with_frozen_at_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_parameter_schemas_frozen_at_pair"'):
            _schema(db_session, factories, status="building", frozen_at=_now())

    def test_superseded_with_frozen_at_and_superseded_at_passes(self, db_session, factories):
        assert _schema(db_session, factories, status="superseded").id is not None

    def test_superseded_without_superseded_at_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_parameter_schemas_superseded_at_pair"'):
            _schema(db_session, factories, status="superseded", superseded_at=None)

    def test_frozen_with_superseded_at_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_parameter_schemas_superseded_at_pair"'):
            _schema(db_session, factories, status="frozen", superseded_at=_now())

    def test_cancelled_without_cancelled_at_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_parameter_schemas_cancelled_pair"'):
            _schema(db_session, factories, status="cancelled", cancelled_at=None)

    def test_building_with_cancelled_at_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_parameter_schemas_cancelled_pair"'):
            _schema(db_session, factories, status="building", cancelled_at=_now())

    def test_cancelled_with_cancelled_at_passes(self, db_session, factories):
        assert _schema(db_session, factories, status="cancelled").id is not None

    def test_manual_origin_without_author_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_parameter_schemas_origin_frozen_by_pair"'):
            _schema(db_session, factories, origin="manual", frozen_by=None)

    def test_model_origin_with_author_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_family_parameter_schemas_origin_frozen_by_pair"'):
            _schema(db_session, factories, origin="model", frozen_by=user.id)

    def test_manual_origin_with_author_passes(self, db_session, factories):
        assert _schema(db_session, factories, origin="manual").id is not None

    def test_unknown_status_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_parameter_schemas_status"'):
            _schema(db_session, factories, status="bogus", frozen_at=None)

    def test_unknown_origin_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_family_parameter_schemas_origin"'):
            _schema(db_session, factories, origin="bogus")

    def test_duplicate_family_version_rejected(self, db_session, factories):
        family = _family(db_session)
        _schema(db_session, factories, family, version=1, status="superseded")
        with rejected(db_session, contains='"uq_family_parameter_schemas_family_version"'):
            _schema(db_session, factories, family, version=1, status="superseded")

    def test_other_version_and_other_family_same_version_pass(self, db_session, factories):
        family = _family(db_session)
        _schema(db_session, factories, family, version=1, status="superseded")
        _schema(db_session, factories, family, version=2, status="superseded")
        _schema(db_session, factories, version=1, status="superseded")

    def test_second_frozen_of_family_rejected(self, db_session, factories):
        family = _family(db_session)
        _schema(db_session, factories, family, version=1, status="frozen")
        with rejected(db_session, contains='"uq_family_parameter_schemas_frozen"'):
            _schema(db_session, factories, family, version=2, status="frozen")

    def test_second_building_of_family_rejected(self, db_session, factories):
        family = _family(db_session)
        _schema(db_session, factories, family, version=1, status="building")
        with rejected(db_session, contains='"uq_family_parameter_schemas_building"'):
            _schema(db_session, factories, family, version=2, status="building")

    def test_second_superseded_and_second_cancelled_pass(self, db_session, factories):
        family = _family(db_session)
        _schema(db_session, factories, family, version=1, status="superseded")
        _schema(db_session, factories, family, version=2, status="superseded")
        _schema(db_session, factories, family, version=3, status="cancelled")
        _schema(db_session, factories, family, version=4, status="cancelled")

    def test_frozen_and_building_coexist_and_other_family_frozen_passes(
        self, db_session, factories
    ):
        family = _family(db_session)
        _schema(db_session, factories, family, version=1, status="frozen")
        _schema(db_session, factories, family, version=2, status="building")
        _schema(db_session, factories, status="frozen")

    def test_delete_family_with_schema_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        with rejected(db_session, contains='"fk_family_parameter_schemas_family_id"'):
            db_session.execute(
                sa.text("DELETE FROM work_families WHERE id = :id"), {"id": schema.family_id}
            )


# ---------------------------------------------------------------------------
#  3. Параметры версии
# ---------------------------------------------------------------------------

class TestParameters:
    @pytest.mark.parametrize("ordinal", [0, 4])
    def test_ordinal_outside_range_rejected(self, db_session, factories, ordinal):
        schema = _schema(db_session, factories)
        with rejected(db_session, contains='"ck_family_parameters_ordinal_range"'):
            _param(db_session, schema, ordinal)

    @pytest.mark.parametrize("ordinal", [1, 3])
    def test_ordinal_range_edges_pass(self, db_session, factories, ordinal):
        assert _param(db_session, _schema(db_session, factories), ordinal).id is not None

    def test_duplicate_ordinal_in_schema_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        _param(db_session, schema, 1)
        with rejected(db_session, contains='"uq_family_parameters_schema_ordinal"'):
            _param(db_session, schema, 1)

    def test_same_ordinal_in_other_schema_passes(self, db_session, factories):
        _param(db_session, _schema(db_session, factories), 1)
        _param(db_session, _schema(db_session, factories), 1)

    @pytest.mark.parametrize("name", ["", "   "])
    def test_blank_name_rejected(self, db_session, factories, name):
        schema = _schema(db_session, factories)
        with rejected(db_session, contains='"ck_family_parameters_name_not_blank"'):
            db_session.add(
                FamilyParameter(schema_id=schema.id, ordinal=1, name=name, name_norm="x")
            )

    def test_delete_schema_cascades_to_parameters_and_values(self, db_session, factories):
        schema = _schema(db_session, factories, status="cancelled")
        parameter = _param(db_session, schema)
        _value(db_session, parameter)
        db_session.execute(
            sa.text("DELETE FROM family_parameter_schemas WHERE id = :id"), {"id": schema.id}
        )
        remaining = db_session.execute(
            sa.text(
                "SELECT (SELECT count(*) FROM family_parameters WHERE schema_id = :s), "
                "(SELECT count(*) FROM family_parameter_values WHERE parameter_id = :p)"
            ),
            {"s": schema.id, "p": parameter.id},
        ).one()
        assert remaining == (0, 0)


# ---------------------------------------------------------------------------
#  4. Значения закрытого списка
# ---------------------------------------------------------------------------

class TestParameterValues:
    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_value_rejected(self, db_session, factories, value):
        parameter = _param(db_session, _schema(db_session, factories))
        with rejected(db_session, contains='"ck_family_parameter_values_value_not_blank"'):
            db_session.add(
                FamilyParameterValue(
                    parameter_id=parameter.id, value=value, value_norm="x", origin="schema"
                )
            )

    def test_empty_value_norm_rejected(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories))
        with rejected(db_session, contains='"ck_family_parameter_values_value_not_blank"'):
            db_session.add(
                FamilyParameterValue(
                    parameter_id=parameter.id, value="100 мм", value_norm="", origin="schema"
                )
            )

    def test_unknown_origin_rejected(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories))
        with rejected(db_session, contains='"ck_family_parameter_values_origin"'):
            _value(db_session, parameter, origin="bogus")

    @pytest.mark.parametrize("origin", ["schema", "extension", "manual"])
    def test_each_origin_passes(self, db_session, factories, origin):
        parameter = _param(db_session, _schema(db_session, factories))
        assert _value(db_session, parameter, origin=origin).id is not None

    def test_duplicate_normalized_value_in_parameter_rejected(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories))
        _value(db_session, parameter, "100 мм")
        with rejected(db_session, contains='"uq_family_parameter_values_parameter_value_norm"'):
            _value(db_session, parameter, "other", value_norm="100 мм")

    def test_same_normalized_value_in_other_parameter_passes(self, db_session, factories):
        schema = _schema(db_session, factories)
        _value(db_session, _param(db_session, schema, 1), "100 мм")
        _value(db_session, _param(db_session, schema, 2), "100 мм")

    def test_merge_into_value_of_same_parameter_passes(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories))
        target = _value(db_session, parameter, "100 мм")
        source = _value(db_session, parameter, "10 см", merged_into_id=target.id)
        assert source.merged_into_id == target.id

    def test_merge_into_value_of_other_parameter_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        target_elsewhere = _value(db_session, _param(db_session, schema, 2), "100 мм")
        parameter = _param(db_session, schema, 1)
        with rejected(db_session, contains='"fk_family_parameter_values_merged_into"'):
            _value(db_session, parameter, "10 см", merged_into_id=target_elsewhere.id)

    def test_merge_into_self_rejected(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories))
        row = _value(db_session, parameter, "100 мм")
        with rejected(db_session, contains='"ck_family_parameter_values_not_self_merged"'):
            row.merged_into_id = row.id


# ---------------------------------------------------------------------------
#  5. Вариант
# ---------------------------------------------------------------------------

class TestVariants:
    def test_archived_without_archived_at_rejected(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        with rejected(db_session, contains='"ck_work_variants_archived_pair"'):
            _variant(db_session, family, schema, status="archived", archived_at=None)

    def test_active_with_archived_at_rejected(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        with rejected(db_session, contains='"ck_work_variants_archived_pair"'):
            _variant(db_session, family, schema, status="active", archived_at=_now())

    def test_archived_with_archived_at_passes(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        assert _variant(db_session, family, schema, status="archived").id is not None

    def test_merged_active_variant_rejected(self, db_session, factories):
        family, schema, _parameter, _value_row, target = _chain(db_session, factories)
        with rejected(db_session, contains='"ck_work_variants_merged_needs_archived"'):
            _variant(db_session, family, schema, status="active", merged_into_id=target.id)

    def test_merged_archived_variant_passes(self, db_session, factories):
        family, schema, _parameter, _value_row, target = _chain(db_session, factories)
        merged = _variant(db_session, family, schema, status="archived", merged_into_id=target.id)
        assert merged.merged_into_id == target.id

    def test_unknown_status_rejected(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        with rejected(db_session, contains='"ck_work_variants_status"'):
            _variant(db_session, family, schema, status="bogus", archived_at=None)

    def test_same_values_key_in_schema_rejected_even_for_archived(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        _variant(db_session, family, schema, values_key="1=?", status="archived")
        with rejected(db_session, contains='"uq_work_variants_schema_values_key"'):
            _variant(db_session, family, schema, values_key="1=?")

    def test_same_values_key_in_other_schema_passes(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        other_schema = _schema(db_session, factories, family, version=2, status="superseded")
        _variant(db_session, family, schema, values_key="1=?")
        _variant(db_session, family, other_schema, values_key="1=?")

    def test_delete_schema_under_a_variant_rejected(self, db_session, factories):
        """RESTRICT не откладывается вместе с отложенным FK: удаление версии под
        вариантом отказывает сразу."""
        _family_row, schema, _p, _v, _variant_row = _chain(db_session, factories)
        with rejected(db_session, contains='"fk_work_variants_schema_family"'):
            db_session.execute(
                sa.text("DELETE FROM family_parameter_schemas WHERE id = :id"), {"id": schema.id}
            )


class TestVariantValues:
    def test_parameter_of_other_schema_rejected(self, db_session, factories):
        family, schema, _parameter, _value_row, variant = _chain(db_session, factories)
        other_schema = _schema(db_session, factories, family, version=2, status="superseded")
        foreign_parameter = _param(db_session, other_schema, 1)
        with rejected(db_session, contains='"fk_work_variant_values_parameter_schema"'):
            db_session.add(
                WorkVariantValue(
                    variant_id=variant.id, schema_id=schema.id,
                    parameter_id=foreign_parameter.id, value_id=None,
                )
            )

    def test_value_of_other_parameter_rejected(self, db_session, factories):
        _family_row, schema, parameter, _value_row, variant = _chain(db_session, factories)
        second_parameter = _param(db_session, schema, 2)
        foreign_value = _value(db_session, second_parameter, "красный")
        with rejected(db_session, contains='"fk_work_variant_values_value_parameter"'):
            db_session.add(
                WorkVariantValue(
                    variant_id=variant.id, schema_id=schema.id, parameter_id=parameter.id,
                    value_id=foreign_value.id,
                )
            )

    def test_row_schema_differing_from_variant_schema_rejected(self, db_session, factories):
        family, _schema_row, _parameter, _value_row, variant = _chain(db_session, factories)
        other_schema = _schema(db_session, factories, family, version=2, status="superseded")
        other_parameter = _param(db_session, other_schema, 1)
        with rejected(db_session, contains='"fk_work_variant_values_variant_schema"'):
            db_session.add(
                WorkVariantValue(
                    variant_id=variant.id, schema_id=other_schema.id,
                    parameter_id=other_parameter.id, value_id=None,
                )
            )

    def test_duplicate_parameter_of_variant_rejected(self, db_session, factories):
        _family_row, schema, parameter, value, variant = _chain(db_session, factories)
        insert = sa.text(
            "INSERT INTO work_variant_values (variant_id, schema_id, parameter_id, value_id) "
            "VALUES (:v, :s, :p, :val)"
        )
        args = {"v": variant.id, "s": schema.id, "p": parameter.id, "val": value.id}
        db_session.execute(insert, args)
        with rejected(db_session, contains='"pk_work_variant_values"'):
            db_session.execute(insert, args)

    def test_delete_parameter_under_variant_values_rejected(self, db_session, factories):
        """Ревью задачи 1: прежний вход удалял ВЕРСИЮ под вариантом — его отвергал
        FK самого варианта раньше, чем FK строк значений, и RESTRICT параметра не
        был предъявлен. Здесь удаляется параметр при `value_id IS NULL`: держит
        только `fk_work_variant_values_parameter_schema`."""
        _family_row, schema, parameter, _value_row, variant = _chain(db_session, factories)
        db_session.add(
            WorkVariantValue(
                variant_id=variant.id, schema_id=schema.id, parameter_id=parameter.id,
                value_id=None,
            )
        )
        db_session.flush()
        with rejected(db_session, contains='"fk_work_variant_values_parameter_schema"'):
            db_session.execute(
                sa.text("DELETE FROM family_parameters WHERE id = :id"), {"id": parameter.id}
            )

    def test_delete_value_under_variant_values_rejected(self, db_session, factories):
        _family_row, schema, parameter, value, variant = _chain(db_session, factories)
        db_session.add(
            WorkVariantValue(
                variant_id=variant.id, schema_id=schema.id, parameter_id=parameter.id,
                value_id=value.id,
            )
        )
        db_session.flush()
        with rejected(db_session, contains='"fk_work_variant_values_value_parameter"'):
            db_session.execute(
                sa.text("DELETE FROM family_parameter_values WHERE id = :id"), {"id": value.id}
            )

    def test_delete_variant_cascades_to_its_values(self, db_session, factories):
        _family_row, schema, parameter, value, variant = _chain(db_session, factories)
        db_session.add(
            WorkVariantValue(
                variant_id=variant.id, schema_id=schema.id, parameter_id=parameter.id,
                value_id=value.id,
            )
        )
        db_session.flush()
        db_session.execute(sa.text("DELETE FROM work_variants WHERE id = :id"), {"id": variant.id})
        left = db_session.execute(
            sa.text("SELECT count(*) FROM work_variant_values WHERE variant_id = :v"),
            {"v": variant.id},
        ).scalar_one()
        assert left == 0


class TestContextParameterValues:
    def _row(self, db_session, factories, **overrides):
        family, schema, parameter, value, _variant_row = _chain(db_session, factories)
        context = _assigned_context(db_session, factories, family)
        defaults = dict(
            context_id=context.id, schema_id=schema.id, parameter_id=parameter.id,
            value_id=value.id, source="name",
        )
        defaults.update(overrides)
        row = ContextParameterValue(**defaults)
        db_session.add(row)
        db_session.flush()
        return row, (family, schema, parameter, value, context)

    def test_parameter_of_other_schema_rejected(self, db_session, factories):
        family, schema, _parameter, _value_row, _variant_row = _chain(db_session, factories)
        other_schema = _schema(db_session, factories, family, version=2, status="superseded")
        foreign_parameter = _param(db_session, other_schema, 1)
        context = _assigned_context(db_session, factories, family)
        with rejected(db_session, contains='"fk_context_parameter_values_parameter_schema"'):
            db_session.add(
                ContextParameterValue(
                    context_id=context.id, schema_id=schema.id,
                    parameter_id=foreign_parameter.id, value_id=None, source="none",
                )
            )

    def test_value_of_other_parameter_rejected(self, db_session, factories):
        family, schema, parameter, _value_row, _variant_row = _chain(db_session, factories)
        foreign_value = _value(db_session, _param(db_session, schema, 2), "красный")
        context = _assigned_context(db_session, factories, family)
        with rejected(db_session, contains='"fk_context_parameter_values_value_parameter"'):
            db_session.add(
                ContextParameterValue(
                    context_id=context.id, schema_id=schema.id, parameter_id=parameter.id,
                    value_id=foreign_value.id, source="name",
                )
            )

    @pytest.mark.parametrize("source", ["name", "path", "manual"])
    def test_value_with_each_value_source_passes(self, db_session, factories, source):
        row, _ = self._row(db_session, factories, source=source)
        assert row.source == source

    @pytest.mark.parametrize("source", ["path_conflict", "none"])
    def test_no_value_with_each_empty_source_passes(self, db_session, factories, source):
        row, _ = self._row(db_session, factories, value_id=None, source=source)
        assert row.value_id is None

    @pytest.mark.parametrize("source", ["name", "path", "manual"])
    def test_no_value_with_value_source_rejected(self, db_session, factories, source):
        with rejected(db_session, contains='"ck_context_parameter_values_source_value_pair"'):
            self._row(db_session, factories, value_id=None, source=source)

    @pytest.mark.parametrize("source", ["path_conflict", "none"])
    def test_value_with_empty_source_rejected(self, db_session, factories, source):
        with rejected(db_session, contains='"ck_context_parameter_values_source_value_pair"'):
            self._row(db_session, factories, source=source)

    def test_unknown_source_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_context_parameter_values_source"'):
            self._row(db_session, factories, source="bogus")

    def test_duplicate_parameter_of_context_rejected(self, db_session, factories):
        row, _ = self._row(db_session, factories)
        with rejected(db_session, contains='"pk_context_parameter_values"'):
            db_session.execute(
                sa.text(
                    "INSERT INTO context_parameter_values "
                    "(context_id, schema_id, parameter_id, value_id, source) "
                    "VALUES (:c, :s, :p, NULL, 'none')"
                ),
                {"c": row.context_id, "s": row.schema_id, "p": row.parameter_id},
            )

    def test_delete_parameter_under_context_values_rejected(self, db_session, factories):
        row, (_f, _s, parameter, _v, _c) = self._row(
            db_session, factories, value_id=None, source="none"
        )
        with rejected(db_session, contains='"fk_context_parameter_values_parameter_schema"'):
            db_session.execute(
                sa.text("DELETE FROM family_parameters WHERE id = :id"), {"id": parameter.id}
            )

    def test_delete_value_under_context_values_rejected(self, db_session, factories):
        row, (_f, _s, _p, value, _c) = self._row(db_session, factories)
        with rejected(db_session, contains='"fk_context_parameter_values_value_parameter"'):
            db_session.execute(
                sa.text("DELETE FROM family_parameter_values WHERE id = :id"), {"id": value.id}
            )

    def test_delete_context_with_values_rejected(self, db_session, factories):
        _row, (_f, _s, _p, _v, context) = self._row(db_session, factories)
        with rejected(db_session, contains='"fk_context_parameter_values_context_id"'):
            db_session.execute(
                sa.text("DELETE FROM catalog_contexts WHERE id = :id"), {"id": context.id}
            )


# ---------------------------------------------------------------------------
#  6. Контекст: вариант и семья (MATCH SIMPLE), «вариант + отметки»
# ---------------------------------------------------------------------------

class TestContextVariant:
    def _with_variant(self, db_session, factories, family, variant, **overrides):
        defaults = dict(
            work_variant_id=variant.id, variant_at=_now(), variant_paths_hash=f"h-{_uid()}"
        )
        defaults.update(overrides)
        return _assigned_context(db_session, factories, family, **defaults)

    def test_variant_with_family_passes(self, db_session, factories):
        family, _schema_row, _parameter, _value_row, variant = _chain(db_session, factories)
        context = self._with_variant(db_session, factories, family, variant)
        assert context.work_variant_id == variant.id

    def test_variant_without_family_rejected_by_check_not_by_fk(self, db_session, factories):
        """Составной FK по умолчанию `MATCH SIMPLE` и при пустой семье пару не
        проверяет вовсе (к тому же отложен до commit): отказ даёт именно CHECK."""
        _family_row, _schema_row, _parameter, _value_row, variant = _chain(db_session, factories)
        with rejected(db_session, contains='"ck_catalog_contexts_variant_needs_family"'):
            _context(
                db_session, factories, work_variant_id=variant.id, variant_at=_now(),
                variant_paths_hash="h",
            )

    def test_no_variant_no_family_passes(self, db_session, factories):
        assert _context(db_session, factories).id is not None

    def test_variant_without_variant_at_rejected(self, db_session, factories):
        family, _s, _p, _v, variant = _chain(db_session, factories)
        with rejected(db_session, contains='"ck_catalog_contexts_variant_at_pair"'):
            self._with_variant(db_session, factories, family, variant, variant_at=None)

    def test_variant_at_without_variant_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_catalog_contexts_variant_at_pair"'):
            _context(db_session, factories, variant_at=_now())

    def test_variant_without_paths_hash_rejected(self, db_session, factories):
        family, _s, _p, _v, variant = _chain(db_session, factories)
        with rejected(db_session, contains='"ck_catalog_contexts_variant_paths_hash_pair"'):
            self._with_variant(db_session, factories, family, variant, variant_paths_hash=None)

    def test_paths_hash_without_variant_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_catalog_contexts_variant_paths_hash_pair"'):
            _context(db_session, factories, variant_paths_hash="h")

    def test_split_hint_without_variant_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_catalog_contexts_split_hint_needs_variant"'):
            _context(db_session, factories, variant_split_hint="path_conflict")

    def test_split_hint_with_variant_passes(self, db_session, factories):
        family, _s, _p, _v, variant = _chain(db_session, factories)
        context = self._with_variant(
            db_session, factories, family, variant, variant_split_hint="path_conflict"
        )
        assert context.variant_split_hint == "path_conflict"

    def test_unknown_split_hint_rejected(self, db_session, factories):
        family, _s, _p, _v, variant = _chain(db_session, factories)
        with rejected(db_session, contains='"ck_catalog_contexts_variant_split_hint"'):
            self._with_variant(db_session, factories, family, variant, variant_split_hint="bogus")

    def test_delete_variant_referenced_by_context_rejected(self, db_session, factories):
        family, _s, _p, _v, variant = _chain(db_session, factories)
        self._with_variant(db_session, factories, family, variant)
        with rejected(db_session, contains='"fk_catalog_contexts_work_variant_family"'):
            db_session.execute(
                sa.text("DELETE FROM work_variants WHERE id = :id"), {"id": variant.id}
            )


class TestFamilySourceAndProvenance:
    def test_auto_suggestion_without_author_passes(self, db_session, factories):
        family = _family(db_session)
        context = _context(
            db_session, factories, work_family_id=family.id, family_source="auto_suggestion",
            family_at=_now(), family_by=None,
        )
        assert context.family_source == "auto_suggestion"

    def test_auto_suggestion_with_author_rejected(self, db_session, factories):
        family = _family(db_session)
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_catalog_contexts_family_provenance"'):
            _context(
                db_session, factories, work_family_id=family.id,
                family_source="auto_suggestion", family_at=_now(), family_by=user.id,
            )

    def test_unknown_family_source_rejected(self, db_session, factories):
        family = _family(db_session)
        with rejected(db_session, contains='"ck_catalog_contexts_family_source"'):
            _context(
                db_session, factories, work_family_id=family.id, family_source="bogus",
                family_at=_now(), family_by=None,
            )


# ---------------------------------------------------------------------------
#  7. Ожидающее назначение — целиком или никак (CK_CONTEXT_PENDING)
# ---------------------------------------------------------------------------

_PENDING_COLUMNS = (
    "pending_family_id", "pending_family_source", "pending_suggestion_id",
    "pending_by", "pending_threshold", "pending_at",
)


class TestContextPending:
    """Заготовки допустимых ожиданий и отказы по входу на каждое нарушение.
    Значения-метки `FAM`/`USER`/`SUG`/`NOW`/`THR` подставляются настоящими."""

    _TEMPLATES = {
        "manual": {
            "pending_family_id": "FAM", "pending_family_source": "manual",
            "pending_by": "USER", "pending_at": "NOW",
        },
        "suggestion": {
            "pending_family_id": "FAM", "pending_family_source": "suggestion",
            "pending_suggestion_id": "SUG", "pending_at": "NOW",
        },
        "auto_suggestion": {
            "pending_family_id": "FAM", "pending_family_source": "auto_suggestion",
            "pending_suggestion_id": "SUG", "pending_threshold": "THR", "pending_at": "NOW",
        },
    }

    #: Все шесть колонок заполнены — недопустимо ни при каком источнике.
    _FULL = {
        "pending_family_id": "FAM", "pending_family_source": "auto_suggestion",
        "pending_suggestion_id": "SUG", "pending_by": "USER", "pending_threshold": "THR",
        "pending_at": "NOW",
    }

    def _tokens(self, db_session, factories):
        return {
            "FAM": _family(db_session).id,
            "USER": factories.UserFactory.create().id,
            "SUG": _suggestion(db_session, factories).id,
            "NOW": _now(),
            "THR": Decimal("0.90"),
        }

    def _insert(self, db_session, factories, spec):
        tokens = self._tokens(db_session, factories)
        values = {column: tokens.get(value, value) for column, value in spec.items()}
        return _context(db_session, factories, **values)

    def test_all_empty_passes(self, db_session, factories):
        assert _context(db_session, factories).pending_family_id is None

    @pytest.mark.parametrize("kind", sorted(_TEMPLATES))
    def test_complete_pending_of_each_source_passes(self, db_session, factories, kind):
        context = self._insert(db_session, factories, self._TEMPLATES[kind])
        assert context.pending_family_source == kind

    @pytest.mark.parametrize(
        ("kind", "blanked"),
        [
            (kind, column)
            for kind, template in sorted(_TEMPLATES.items())
            for column in sorted(template)
        ],
        ids=lambda value: str(value),
    )
    def test_each_required_column_emptied_alone_rejected(
        self, db_session, factories, kind, blanked
    ):
        spec = dict(self._TEMPLATES[kind])
        del spec[blanked]
        with rejected(db_session, contains='"ck_catalog_contexts_pending"'):
            self._insert(db_session, factories, spec)

    @pytest.mark.parametrize(
        ("kind", "extra"),
        [
            ("manual", "pending_suggestion_id"),
            ("manual", "pending_threshold"),
            ("suggestion", "pending_by"),
            ("suggestion", "pending_threshold"),
            ("auto_suggestion", "pending_by"),
        ],
    )
    def test_column_foreign_to_the_source_rejected(self, db_session, factories, kind, extra):
        spec = dict(self._TEMPLATES[kind])
        spec[extra] = {
            "pending_suggestion_id": "SUG", "pending_threshold": "THR", "pending_by": "USER",
        }[extra]
        with rejected(db_session, contains='"ck_catalog_contexts_pending"'):
            self._insert(db_session, factories, spec)

    @pytest.mark.parametrize("source", sorted(_TEMPLATES))
    def test_all_six_columns_filled_rejected_for_each_source(
        self, db_session, factories, source
    ):
        """Все шесть колонок сразу недопустимы ни при каком источнике.

        Ревью задачи 1: прежний вход «пять из шести» (`_FULL` без одной колонки)
        нёс `pending_by` при источнике `auto_suggestion` в КАЖДОМ случае и
        отвергался несоответствием автора, какую бы колонку ни опустили, — снятие
        `pending_family_id IS NOT NULL` из второй ветви его не роняло. «Пустая
        колонка при заполненных остальных» предъявлена
        `test_each_required_column_emptied_alone_rejected` (по одному нарушению
        на вход), здесь — только заявление «шести сразу не бывает»."""
        spec = dict(self._FULL)
        spec["pending_family_source"] = source
        with rejected(db_session, contains='"ck_catalog_contexts_pending"'):
            self._insert(db_session, factories, spec)

    @pytest.mark.parametrize("filled", _PENDING_COLUMNS)
    def test_one_column_filled_with_the_other_five_empty_rejected(
        self, db_session, factories, filled
    ):
        spec = {filled: self._FULL[filled]}
        if filled == "pending_family_source":
            spec[filled] = "manual"
        with rejected(db_session, contains='"ck_catalog_contexts_pending"'):
            self._insert(db_session, factories, spec)

    def test_unknown_pending_source_rejected(self, db_session, factories):
        spec = dict(self._TEMPLATES["suggestion"])
        spec["pending_family_source"] = "bogus"
        with rejected(db_session, contains='"ck_catalog_contexts_pending_family_source"'):
            self._insert(db_session, factories, spec)


# ---------------------------------------------------------------------------
#  8. Задания: вид, предмет, новый ключ
# ---------------------------------------------------------------------------

class TestSemanticJobKinds:
    def _schema_job(self, db_session, factories, schema=None, **overrides):
        schema = schema or _schema(db_session, factories, status="building")
        defaults = dict(
            kind="family_schema", context_id=None, family_id=schema.family_id,
            schema_id=schema.id,
        )
        defaults.update(overrides)
        return _job(db_session, factories, **defaults)

    def _values_job(self, db_session, factories, **overrides):
        family, schema, *_ = _chain(db_session, factories)
        context = _assigned_context(db_session, factories, family)
        defaults = dict(
            kind="context_values", context_id=context.id, family_id=family.id,
            schema_id=schema.id,
        )
        defaults.update(overrides)
        return _job(db_session, factories, context=context, **defaults), (family, schema, context)

    def test_schema_job_without_context_passes(self, db_session, factories):
        job = self._schema_job(db_session, factories)
        assert job.context_id is None

    def test_schema_job_with_context_rejected(self, db_session, factories):
        """Нарушает оба предмета сразу (`context_subject` и `schema_subject`):
        отказ — любым из них."""
        context = _context(db_session, factories)
        with rejected(db_session):
            self._schema_job(db_session, factories, context_id=context.id)

    def test_schema_job_without_family_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_schema_subject"'):
            self._schema_job(db_session, factories, family_id=None)

    def test_values_job_with_context_and_schema_passes(self, db_session, factories):
        job, _ = self._values_job(db_session, factories)
        assert job.kind == "context_values"

    def test_values_job_without_context_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_context_subject"'):
            self._values_job(db_session, factories, context_id=None, family_id=None)

    def test_values_job_without_schema_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_schema_id_by_kind"'):
            self._values_job(db_session, factories, schema_id=None)

    def test_schema_job_without_schema_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_schema_id_by_kind"'):
            self._schema_job(db_session, factories, schema_id=None)

    def test_suggestion_job_with_schema_rejected(self, db_session, factories):
        schema = _schema(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_jobs_schema_id_by_kind"'):
            _job(db_session, factories, kind="family_suggestion", schema_id=schema.id)

    def test_suggestion_job_without_context_rejected(self, db_session, factories):
        with rejected(db_session, contains='"ck_semantic_jobs_context_subject"'):
            _job(db_session, factories, kind="family_suggestion", context_id=None)

    def test_unknown_kind_rejected(self, db_session, factories):
        """Чужой вид не входит ни в одно из множеств равносильностей предмета —
        без контекста и без версии схемы нарушено ровно одно ограничение, список видов."""
        with rejected(db_session, contains='"ck_semantic_jobs_kind"'):
            _job(db_session, factories, kind="bogus", context_id=None)

    def test_kind_defaults_to_family_suggestion_in_the_orm(self, db_session, factories):
        context = _context(db_session, factories)
        job = SemanticJob(
            context_id=context.id, request_hash=f"req-{_uid()}", status="pending",
            prompt_version="v1", model_requested="m", place_dictionary_version=1,
            candidates_hash="c", prefix_hash="p", input_hash="i",
            response_schema_version="v1", serialization_version="v1",
        )
        db_session.add(job)
        db_session.flush()
        assert job.kind == "family_suggestion"

    def test_kind_has_no_server_default(self, db_session):
        default = db_session.execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'semantic_jobs' AND column_name = 'kind'"
            )
        ).scalar_one()
        assert default is None

    def test_values_job_without_explicit_kind_but_with_schema_rejected(
        self, db_session, factories
    ):
        """Новые виды без явного вида не создаются: умолчание ORM —
        `family_suggestion`, и версия схемы при нём отвергается CHECK-ом."""
        family, schema, *_ = _chain(db_session, factories)
        context = _assigned_context(db_session, factories, family)
        with rejected(db_session, contains='"ck_semantic_jobs_schema_id_by_kind"'):
            db_session.add(
                SemanticJob(
                    context_id=context.id, family_id=family.id, schema_id=schema.id,
                    request_hash=f"req-{_uid()}", status="pending", prompt_version="v1",
                    model_requested="m", place_dictionary_version=1, candidates_hash="c",
                    prefix_hash="p", input_hash="i", response_schema_version="v1",
                    serialization_version="v1",
                )
            )

    def test_result_suggestion_on_suggestion_job_passes(self, db_session, factories):
        context = _context(db_session, factories)
        job = _job(db_session, factories, context=context)
        suggestion = _suggestion(db_session, factories, context=context, job=job)
        job.result_suggestion_id = suggestion.id
        db_session.flush()

    def test_result_suggestion_on_values_job_rejected(self, db_session, factories):
        suggestion = _suggestion(db_session, factories)
        job, _ = self._values_job(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_jobs_result_suggestion_kind"'):
            job.result_suggestion_id = suggestion.id

    def test_delete_schema_referenced_by_job_rejected(self, db_session, factories):
        schema = _schema(db_session, factories, status="building")
        self._schema_job(db_session, factories, schema=schema)
        with rejected(db_session, contains='"fk_semantic_jobs_schema_id"'):
            db_session.execute(
                sa.text("DELETE FROM family_parameter_schemas WHERE id = :id"), {"id": schema.id}
            )


class TestSemanticJobKey:
    """`UNIQUE(kind, COALESCE(context_id,-1), COALESCE(family_id,-1),
    COALESCE(schema_id,-1), request_hash)`: версия схемы — часть предмета."""

    def _schema_job(self, db_session, factories, schema, request_hash):
        return _job(
            db_session, factories, kind="family_schema", context_id=None,
            family_id=schema.family_id, schema_id=schema.id, request_hash=request_hash,
        )

    def test_same_schema_and_hash_rejected(self, db_session, factories):
        schema = _schema(db_session, factories, status="building")
        self._schema_job(db_session, factories, schema, "same")
        with rejected(db_session, contains='"uq_semantic_jobs_subject_request_hash"'):
            self._schema_job(db_session, factories, schema, "same")

    def test_other_schema_version_same_hash_passes(self, db_session, factories):
        family = _family(db_session)
        first = _schema(db_session, factories, family, version=1, status="superseded")
        second = _schema(db_session, factories, family, version=2, status="building")
        self._schema_job(db_session, factories, first, "same")
        self._schema_job(db_session, factories, second, "same")

    def test_same_schema_other_hash_passes(self, db_session, factories):
        schema = _schema(db_session, factories, status="building")
        self._schema_job(db_session, factories, schema, "one")
        self._schema_job(db_session, factories, schema, "two")

    def test_values_jobs_same_context_schema_hash_rejected(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        context = _assigned_context(db_session, factories, family)
        kwargs = dict(
            kind="context_values", family_id=family.id, schema_id=schema.id,
            request_hash="same",
        )
        _job(db_session, factories, context=context, **kwargs)
        with rejected(db_session, contains='"uq_semantic_jobs_subject_request_hash"'):
            _job(db_session, factories, context=context, **kwargs)

    def test_values_jobs_same_context_hash_other_schema_passes(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        later = _schema(db_session, factories, family, version=2, status="building")
        context = _assigned_context(db_session, factories, family)
        for target in (schema, later):
            _job(
                db_session, factories, context=context, kind="context_values",
                family_id=family.id, schema_id=target.id, request_hash="same",
            )

    def test_suggestion_and_values_jobs_of_one_context_and_hash_pass(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        context = _assigned_context(db_session, factories, family)
        _job(db_session, factories, context=context, request_hash="same")
        _job(
            db_session, factories, context=context, kind="context_values",
            family_id=family.id, schema_id=schema.id, request_hash="same",
        )

    def test_suggestion_jobs_same_context_and_hash_still_rejected(self, db_session, factories):
        context = _context(db_session, factories)
        _job(db_session, factories, context=context, request_hash="same")
        with rejected(db_session, contains='"uq_semantic_jobs_subject_request_hash"'):
            _job(db_session, factories, context=context, request_hash="same")

    def test_old_unique_constraint_is_gone_and_new_index_is_live(self, db_session):
        unique_constraints = _live_constraint_names(db_session, "semantic_jobs", "u")
        assert "uq_semantic_jobs_context_request_hash" not in unique_constraints
        definition = db_session.execute(
            sa.text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_semantic_jobs_subject_request_hash'"
            )
        ).scalar_one()
        assert definition.startswith("CREATE UNIQUE INDEX")
        for fragment in ("(kind, COALESCE(context_id,", "COALESCE(family_id,",
                         "COALESCE(schema_id,", "'-1'", "request_hash)"):
            assert fragment in definition


# ---------------------------------------------------------------------------
#  9. Предложения: новые решения и автор
# ---------------------------------------------------------------------------

class TestSuggestionDecisions:
    @pytest.mark.parametrize("decision", ["accepted_pending", "accepted", "rejected"])
    def test_human_decision_without_author_rejected(self, db_session, factories, decision):
        with rejected(db_session, contains='"ck_family_suggestions_decision_author_pair"'):
            _suggestion(
                db_session, factories, decision=decision, decided_by=None, decided_at=_now()
            )

    @pytest.mark.parametrize("decision", ["accepted_pending", "accepted", "rejected"])
    def test_human_decision_with_author_passes(self, db_session, factories, decision):
        user = factories.UserFactory.create()
        suggestion = _suggestion(
            db_session, factories, decision=decision, decided_by=user.id, decided_at=_now()
        )
        assert suggestion.decision == decision

    @pytest.mark.parametrize("decision", ["auto_accepted", "auto_pending", "auto_superseded"])
    def test_automatic_decision_with_author_rejected(self, db_session, factories, decision):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_family_suggestions_decision_author_pair"'):
            _suggestion(
                db_session, factories, decision=decision, decided_by=user.id, decided_at=_now()
            )

    @pytest.mark.parametrize("decision", ["auto_accepted", "auto_pending", "auto_superseded"])
    def test_automatic_decision_without_author_passes(self, db_session, factories, decision):
        suggestion = _suggestion(
            db_session, factories, decision=decision, decided_by=None, decided_at=_now()
        )
        assert suggestion.decision == decision

    def test_author_without_decision_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_family_suggestions_decision_author_pair"'):
            _suggestion(db_session, factories, decision=None, decided_by=user.id)

    def test_no_decision_no_author_passes(self, db_session, factories):
        assert _suggestion(db_session, factories).decision is None

    def test_unknown_decision_rejected(self, db_session, factories):
        user = factories.UserFactory.create()
        with rejected(db_session, contains='"ck_family_suggestions_decision"'):
            _suggestion(
                db_session, factories, decision="bogus", decided_by=user.id, decided_at=_now()
            )


# ---------------------------------------------------------------------------
#  10. Журнал: шесть типов §2.13 и предмет по типу
# ---------------------------------------------------------------------------

_CONTEXT_EVENT_TYPES = ("context_variant_assigned", "context_family_pending", "context_not_work")
_FAMILY_EVENT_TYPES = (
    "family_schema_frozen", "family_schema_value_added", "family_variants_merged",
)


class TestJournalNewTypes:
    @pytest.mark.parametrize("event_type", _CONTEXT_EVENT_TYPES)
    def test_context_event_with_context_subject_passes(self, db_session, factories, event_type):
        context = _context(db_session, factories)
        assert _event(db_session, event_type, context_id=context.id).id is not None

    @pytest.mark.parametrize("event_type", _CONTEXT_EVENT_TYPES)
    def test_context_event_with_family_subject_rejected(self, db_session, factories, event_type):
        family = _family(db_session)
        with rejected(db_session, contains='"ck_semantic_events_subject_by_type"'):
            _event(db_session, event_type, family_id=family.id)

    @pytest.mark.parametrize("event_type", _FAMILY_EVENT_TYPES)
    def test_family_event_with_family_subject_passes(self, db_session, factories, event_type):
        family = _family(db_session)
        assert _event(db_session, event_type, family_id=family.id).id is not None

    @pytest.mark.parametrize("event_type", _FAMILY_EVENT_TYPES)
    def test_family_event_with_context_subject_rejected(self, db_session, factories, event_type):
        context = _context(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_events_subject_by_type"'):
            _event(db_session, event_type, context_id=context.id)

    def test_unknown_event_type_rejected(self, db_session, factories):
        context = _context(db_session, factories)
        with rejected(db_session, contains='"ck_semantic_events_event_type"'):
            _event(db_session, "context_bogus", context_id=context.id)

    def test_event_types_cover_the_spec_and_nothing_more_than_22(self):
        independent = {
            "context_created", "context_split", "context_merged", "members_moved",
            "members_marked_stale", "kind_set", "name_role_set", "context_family_assigned",
            "context_archived", "routing_rules_dropped", "family_created", "family_updated",
            "family_activated", "family_archived", "family_merged",
            "context_variant_assigned", "context_family_pending", "context_not_work",
            "family_schema_frozen", "family_schema_value_added", "family_variants_merged",
            "context_reopened",
        }
        assert len(independent) == 22
        assert set(SEMANTIC_EVENT_TYPES) == independent
        assert len(SEMANTIC_EVENT_TYPES) == 22

    def test_old_event_types_keep_their_subjects(self, db_session, factories):
        family = _family(db_session)
        context = _context(db_session, factories)
        assert _event(db_session, "family_merged", family_id=family.id).id is not None
        assert _event(db_session, "context_family_assigned", context_id=context.id).id is not None


# ---------------------------------------------------------------------------
#  11. Отложенные FK — ровно два, и только до настоящего commit
# ---------------------------------------------------------------------------

_NEW_TABLES = (
    "family_parameter_schemas", "family_parameters", "family_parameter_values",
    "work_variants", "work_variant_values", "context_parameter_values",
)


class TestDeferredForeignKeys:
    def test_exactly_two_foreign_keys_are_deferrable_and_initially_deferred(self, db_session):
        rows = db_session.execute(
            sa.text(
                "SELECT conrelid::regclass::text, conname, condeferrable, condeferred "
                "FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid = ANY (CAST(:tables AS regclass[])) "
                "ORDER BY conname"
            ),
            {"tables": "{" + ",".join((*_NEW_TABLES, "catalog_contexts")) + "}"},
        ).all()
        assert rows, "foreign keys were not found"
        deferrable = {name for _table, name, can_defer, _initially in rows if can_defer}
        assert deferrable == {
            "fk_catalog_contexts_work_variant_family", "fk_work_variants_schema_family",
        }
        assert all(initially for _t, _name, can_defer, initially in rows if can_defer)

    def test_false_pair_context_variant_passes_flush_and_fails_commit(
        self, committing_db, committing_factories
    ):
        session = committing_db
        family_a = _family(session)
        family_b = _family(session)
        schema_b = _schema(session, committing_factories, family_b)
        variant_b = _variant(session, family_b, schema_b)
        _assigned_context(
            session, committing_factories, family_a, work_variant_id=variant_b.id,
            variant_at=_now(), variant_paths_hash="h",
        )
        session.flush()  # проходит: проверка отложена
        with pytest.raises(IntegrityError) as exc:
            session.commit()
        assert '"fk_catalog_contexts_work_variant_family"' in str(exc.value)
        session.rollback()

    def test_right_pair_context_variant_commits(self, committing_db, committing_factories):
        session = committing_db
        family = _family(session)
        schema = _schema(session, committing_factories, family)
        variant = _variant(session, family, schema)
        _assigned_context(
            session, committing_factories, family, work_variant_id=variant.id,
            variant_at=_now(), variant_paths_hash="h",
        )
        session.commit()

    def test_false_pair_variant_schema_passes_flush_and_fails_commit(
        self, committing_db, committing_factories
    ):
        session = committing_db
        family_a = _family(session)
        family_b = _family(session)
        schema_b = _schema(session, committing_factories, family_b)
        _variant(session, family_a, schema_b)
        session.flush()  # проходит: проверка отложена
        with pytest.raises(IntegrityError) as exc:
            session.commit()
        assert '"fk_work_variants_schema_family"' in str(exc.value)
        session.rollback()

    def test_right_pair_variant_schema_commits(self, committing_db, committing_factories):
        session = committing_db
        family = _family(session)
        schema = _schema(session, committing_factories, family)
        _variant(session, family, schema)
        session.commit()

    def test_set_constraints_immediate_surfaces_the_false_pair_at_once(
        self, committing_db, committing_factories
    ):
        session = committing_db
        family_a = _family(session)
        schema_b = _schema(session, committing_factories, _family(session))
        _variant(session, family_a, schema_b)
        session.flush()
        with pytest.raises(IntegrityError) as exc:
            session.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert '"fk_work_variants_schema_family"' in str(exc.value)
        session.rollback()


# ---------------------------------------------------------------------------
#  11а. Каталог внешних ключей: каждая ссылка §2.4 существует с заявленными
#       колонками, целью, ON DELETE и отложенностью (ревью задачи 1)
# ---------------------------------------------------------------------------

#: Независимый литерал по спеке §2.4 — определения в форме `pg_get_constraintdef`.
#: Набор FK шести новых таблиц сверяется ЦЕЛИКОМ (лишний или пропавший FK —
#: отказ), у `catalog_contexts` и `semantic_jobs` — только FK, заведённые 0019.
_EXPECTED_FOREIGN_KEYS = {
    "family_parameter_schemas": {
        "fk_family_parameter_schemas_family_id":
            "FOREIGN KEY (family_id) REFERENCES work_families(id) ON DELETE RESTRICT",
        "fk_family_parameter_schemas_job_id":
            "FOREIGN KEY (job_id) REFERENCES semantic_jobs(id) ON DELETE SET NULL",
        "fk_family_parameter_schemas_frozen_by":
            "FOREIGN KEY (frozen_by) REFERENCES users(id) ON DELETE RESTRICT",
    },
    "family_parameters": {
        "fk_family_parameters_schema_id":
            "FOREIGN KEY (schema_id) REFERENCES family_parameter_schemas(id) ON DELETE CASCADE",
    },
    "family_parameter_values": {
        "fk_family_parameter_values_parameter_id":
            "FOREIGN KEY (parameter_id) REFERENCES family_parameters(id) ON DELETE CASCADE",
        "fk_family_parameter_values_merged_into":
            "FOREIGN KEY (merged_into_id, parameter_id) "
            "REFERENCES family_parameter_values(id, parameter_id) ON DELETE RESTRICT",
    },
    "work_variants": {
        "fk_work_variants_family_id":
            "FOREIGN KEY (family_id) REFERENCES work_families(id) ON DELETE RESTRICT",
        "fk_work_variants_schema_family":
            "FOREIGN KEY (schema_id, family_id) REFERENCES family_parameter_schemas(id, family_id) "
            "ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED",
        "fk_work_variants_merged_into_id":
            "FOREIGN KEY (merged_into_id) REFERENCES work_variants(id) ON DELETE RESTRICT",
    },
    "work_variant_values": {
        "fk_work_variant_values_variant_schema":
            "FOREIGN KEY (variant_id, schema_id) REFERENCES work_variants(id, schema_id) "
            "ON DELETE CASCADE",
        "fk_work_variant_values_parameter_schema":
            "FOREIGN KEY (parameter_id, schema_id) REFERENCES family_parameters(id, schema_id) "
            "ON DELETE RESTRICT",
        "fk_work_variant_values_value_parameter":
            "FOREIGN KEY (value_id, parameter_id) "
            "REFERENCES family_parameter_values(id, parameter_id) ON DELETE RESTRICT",
    },
    "context_parameter_values": {
        "fk_context_parameter_values_context_id":
            "FOREIGN KEY (context_id) REFERENCES catalog_contexts(id) ON DELETE RESTRICT",
        "fk_context_parameter_values_parameter_schema":
            "FOREIGN KEY (parameter_id, schema_id) REFERENCES family_parameters(id, schema_id) "
            "ON DELETE RESTRICT",
        "fk_context_parameter_values_value_parameter":
            "FOREIGN KEY (value_id, parameter_id) "
            "REFERENCES family_parameter_values(id, parameter_id) ON DELETE RESTRICT",
        "fk_context_parameter_values_job_id":
            "FOREIGN KEY (job_id) REFERENCES semantic_jobs(id) ON DELETE SET NULL",
    },
}
_EXPECTED_NEW_FOREIGN_KEYS_OF_OLD_TABLES = {
    "catalog_contexts": {
        "fk_catalog_contexts_work_variant_family":
            "FOREIGN KEY (work_variant_id, work_family_id) REFERENCES work_variants(id, family_id) "
            "ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED",
        "fk_catalog_contexts_pending_family_id":
            "FOREIGN KEY (pending_family_id) REFERENCES work_families(id) ON DELETE RESTRICT",
        "fk_catalog_contexts_pending_suggestion_id":
            "FOREIGN KEY (pending_suggestion_id) REFERENCES family_suggestions(id) "
            "ON DELETE RESTRICT",
        "fk_catalog_contexts_pending_by":
            "FOREIGN KEY (pending_by) REFERENCES users(id) ON DELETE RESTRICT",
    },
    "semantic_jobs": {
        "fk_semantic_jobs_family_id":
            "FOREIGN KEY (family_id) REFERENCES work_families(id) ON DELETE RESTRICT",
        "fk_semantic_jobs_schema_id":
            "FOREIGN KEY (schema_id) REFERENCES family_parameter_schemas(id) ON DELETE RESTRICT",
    },
}


def _live_foreign_keys(session, table: str) -> dict[str, str]:
    rows = session.execute(
        sa.text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = CAST(:t AS regclass) AND contype = 'f'"
        ),
        {"t": table},
    ).all()
    return {name: definition for name, definition in rows}


class TestForeignKeyCatalog:
    @pytest.mark.parametrize("table", sorted(_EXPECTED_FOREIGN_KEYS))
    def test_new_table_has_exactly_the_foreign_keys_of_the_spec(self, db_session, table):
        assert _live_foreign_keys(db_session, table) == _EXPECTED_FOREIGN_KEYS[table]

    @pytest.mark.parametrize("table", sorted(_EXPECTED_NEW_FOREIGN_KEYS_OF_OLD_TABLES))
    def test_extended_table_has_the_new_foreign_keys_of_the_spec(self, db_session, table):
        live = _live_foreign_keys(db_session, table)
        for name, definition in _EXPECTED_NEW_FOREIGN_KEYS_OF_OLD_TABLES[table].items():
            assert live.get(name) == definition, name

    def test_deleting_a_job_nulls_job_id_of_schema_and_of_context_values(
        self, db_session, factories
    ):
        family, schema, parameter, value, _variant_row = _chain(db_session, factories)
        context = _assigned_context(db_session, factories, family)
        values_job = _job(
            db_session, factories, context=context, kind="context_values",
            family_id=family.id, schema_id=schema.id,
        )
        building = _schema(db_session, factories, family, version=2, status="building")
        schema_job = _job(
            db_session, factories, kind="family_schema", context_id=None,
            family_id=family.id, schema_id=building.id,
        )
        building.job_id = schema_job.id
        db_session.add(
            ContextParameterValue(
                context_id=context.id, schema_id=schema.id, parameter_id=parameter.id,
                value_id=value.id, source="name", job_id=values_job.id,
            )
        )
        db_session.flush()
        db_session.execute(
            sa.text("DELETE FROM semantic_jobs WHERE id IN (:a, :b)"),
            {"a": values_job.id, "b": schema_job.id},
        )
        left = db_session.execute(
            sa.text(
                "SELECT (SELECT job_id FROM family_parameter_schemas WHERE id = :s), "
                "(SELECT job_id FROM context_parameter_values WHERE context_id = :c), "
                "(SELECT count(*) FROM context_parameter_values WHERE context_id = :c)"
            ),
            {"s": building.id, "c": context.id},
        ).one()
        assert left == (None, None, 1)

    def test_deleting_the_pending_family_is_rejected(self, db_session, factories):
        family = _family(db_session)
        user = factories.UserFactory.create()
        _context(
            db_session, factories, pending_family_id=family.id, pending_family_source="manual",
            pending_by=user.id, pending_at=_now(),
        )
        with rejected(db_session, contains='"fk_catalog_contexts_pending_family_id"'):
            db_session.execute(sa.text("DELETE FROM work_families WHERE id = :id"), {"id": family.id})

    def test_deleting_the_pending_author_is_rejected(self, db_session, factories):
        family = _family(db_session)
        user = factories.UserFactory.create()
        _context(
            db_session, factories, pending_family_id=family.id, pending_family_source="manual",
            pending_by=user.id, pending_at=_now(),
        )
        with rejected(db_session, contains='"fk_catalog_contexts_pending_by"'):
            db_session.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": user.id})

    def test_deleting_the_pending_suggestion_is_rejected(self, db_session, factories):
        family = _family(db_session)
        suggestion = _suggestion(db_session, factories)
        _context(
            db_session, factories, pending_family_id=family.id,
            pending_family_source="suggestion", pending_suggestion_id=suggestion.id,
            pending_at=_now(),
        )
        with rejected(db_session, contains='"fk_catalog_contexts_pending_suggestion_id"'):
            db_session.execute(
                sa.text("DELETE FROM family_suggestions WHERE id = :id"), {"id": suggestion.id}
            )

    def test_deleting_the_author_of_a_manual_schema_is_rejected(self, db_session, factories):
        schema = _schema(db_session, factories, origin="manual")
        with rejected(db_session, contains='"fk_family_parameter_schemas_frozen_by"'):
            db_session.execute(
                sa.text("DELETE FROM users WHERE id = :id"), {"id": schema.frozen_by}
            )

    def test_deleting_the_merge_target_variant_is_rejected(self, db_session, factories):
        family, schema, _parameter, _value_row, target = _chain(db_session, factories)
        _variant(db_session, family, schema, status="archived", merged_into_id=target.id)
        with rejected(db_session, contains='"fk_work_variants_merged_into_id"'):
            db_session.execute(
                sa.text("DELETE FROM work_variants WHERE id = :id"), {"id": target.id}
            )

# ---------------------------------------------------------------------------
#  12. Сырые индексы: наличие в базе и регистрация в alembic/env.py
# ---------------------------------------------------------------------------

class TestRawIndexes:
    _EXPECTED = {
        "uq_family_parameter_schemas_frozen": (
            "family_parameter_schemas", "(family_id) WHERE (status = 'frozen'::text)",
        ),
        "uq_family_parameter_schemas_building": (
            "family_parameter_schemas", "(family_id) WHERE (status = 'building'::text)",
        ),
        "uq_semantic_jobs_subject_request_hash": ("semantic_jobs", "request_hash)"),
    }

    @pytest.mark.parametrize("name", sorted(_EXPECTED))
    def test_index_exists_and_is_unique(self, db_session, name):
        table, fragment = self._EXPECTED[name]
        definition = db_session.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :n AND tablename = :t"),
            {"n": name, "t": table},
        ).scalar_one()
        assert definition.startswith("CREATE UNIQUE INDEX")
        assert fragment in definition

    @pytest.mark.parametrize("name", sorted(_EXPECTED))
    def test_index_is_registered_in_env(self, name):
        env_text = (
            Path(__file__).resolve().parents[2] / "alembic" / "env.py"
        ).read_bytes().decode("utf-8")
        assert f'"{name}"' in env_text


# ---------------------------------------------------------------------------
#  13. Parity: литералы IN (...) и CHECK-выражения 0019 = models.py = независимый
#      литерал (тройка, как в TestParityWithMigration фич 1-2)
# ---------------------------------------------------------------------------

class TestParityWithMigration:
    _IN_CASES = [
        (SCHEMA_STATUSES, "SCHEMA_STATUSES", {"building", "frozen", "superseded", "cancelled"}),
        (SCHEMA_ORIGINS, "SCHEMA_ORIGINS", {"model", "manual"}),
        (VALUE_ORIGINS, "VALUE_ORIGINS", {"schema", "extension", "manual"}),
        (VARIANT_STATUSES, "VARIANT_STATUSES", {"active", "archived"}),
        (VALUE_SOURCES, "VALUE_SOURCES", {"name", "path", "manual", "path_conflict", "none"}),
        (VARIANT_SPLIT_HINTS, "VARIANT_SPLIT_HINTS", {"path_conflict"}),
        (
            SEMANTIC_JOB_KINDS, "SEMANTIC_JOB_KINDS",
            {"family_suggestion", "family_schema", "context_values", "family_discovery"},
        ),
        (FAMILY_SOURCES, "FAMILY_SOURCES", {"manual", "suggestion", "auto_suggestion"}),
        (
            SUGGESTION_DECISIONS, "SUGGESTION_DECISIONS",
            {
                "accepted", "rejected", "other_family", "family_created", "accepted_pending",
                "auto_accepted", "auto_pending", "auto_superseded",
            },
        ),
        (
            SEMANTIC_EVENT_TYPES_SQL, "SEMANTIC_EVENT_TYPES_SQL",
            {
                "context_created", "context_split", "context_merged", "members_moved",
                "members_marked_stale", "kind_set", "name_role_set", "context_family_assigned",
                "context_archived", "routing_rules_dropped", "family_created", "family_updated",
                "family_activated", "family_archived", "family_merged",
                "context_variant_assigned", "context_family_pending", "context_not_work",
                "family_schema_frozen", "family_schema_value_added", "family_variants_merged",
                "context_reopened",
            },
        ),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "name", "independent"), _IN_CASES, ids=[case[1] for case in _IN_CASES]
    )
    def test_in_list_matches_migration_and_independent_literal(
        self, model_expr, name, independent
    ):
        assert model_expr == getattr(_current_migration_for(name), name)
        parsed = {piece.strip().strip("'") for piece in model_expr.split(",")}
        assert parsed == independent

    _CK_CASES = [
        (CK_SCHEMA_CANCELLED_PAIR, "CK_SCHEMA_CANCELLED_PAIR",
         "(status = 'cancelled') = (cancelled_at IS NOT NULL)"),
        (CK_SCHEMA_FROZEN_AT_PAIR, "CK_SCHEMA_FROZEN_AT_PAIR",
         "(status IN ('frozen', 'superseded')) = (frozen_at IS NOT NULL)"),
        (CK_SCHEMA_SUPERSEDED_AT_PAIR, "CK_SCHEMA_SUPERSEDED_AT_PAIR",
         "(status = 'superseded') = (superseded_at IS NOT NULL)"),
        (CK_SCHEMA_ORIGIN_FROZEN_BY_PAIR, "CK_SCHEMA_ORIGIN_FROZEN_BY_PAIR",
         "(origin = 'manual') = (frozen_by IS NOT NULL)"),
        (CK_PARAMETER_ORDINAL_RANGE, "CK_PARAMETER_ORDINAL_RANGE", "ordinal BETWEEN 1 AND 3"),
        (CK_PARAMETER_NAME_NOT_BLANK, "CK_PARAMETER_NAME_NOT_BLANK", "btrim(name) <> ''"),
        (CK_PARAMETER_VALUE_NOT_BLANK, "CK_PARAMETER_VALUE_NOT_BLANK",
         "btrim(value) <> '' AND value_norm <> ''"),
        (CK_PARAMETER_VALUE_NOT_SELF_MERGED, "CK_PARAMETER_VALUE_NOT_SELF_MERGED",
         "merged_into_id IS NULL OR merged_into_id <> id"),
        (CK_VARIANT_ARCHIVED_PAIR, "CK_VARIANT_ARCHIVED_PAIR",
         "(status = 'archived') = (archived_at IS NOT NULL)"),
        (CK_VARIANT_MERGED_NEEDS_ARCHIVED, "CK_VARIANT_MERGED_NEEDS_ARCHIVED",
         "merged_into_id IS NULL OR status = 'archived'"),
        (CK_CONTEXT_VALUE_SOURCE_PAIR, "CK_CONTEXT_VALUE_SOURCE_PAIR",
         "(value_id IS NULL) = (source IN ('path_conflict', 'none'))"),
        (CK_CONTEXT_VARIANT_NEEDS_FAMILY, "CK_CONTEXT_VARIANT_NEEDS_FAMILY",
         "work_variant_id IS NULL OR work_family_id IS NOT NULL"),
        (CK_CONTEXT_VARIANT_AT_PAIR, "CK_CONTEXT_VARIANT_AT_PAIR",
         "(work_variant_id IS NULL) = (variant_at IS NULL)"),
        (CK_CONTEXT_VARIANT_PATHS_HASH_PAIR, "CK_CONTEXT_VARIANT_PATHS_HASH_PAIR",
         "(work_variant_id IS NULL) = (variant_paths_hash IS NULL)"),
        (CK_CONTEXT_SPLIT_HINT_NEEDS_VARIANT, "CK_CONTEXT_SPLIT_HINT_NEEDS_VARIANT",
         "variant_split_hint IS NULL OR work_variant_id IS NOT NULL"),
        (
            CK_CONTEXT_PENDING, "CK_CONTEXT_PENDING",
            "(pending_family_id IS NULL AND pending_family_source IS NULL "
            "AND pending_suggestion_id IS NULL AND pending_by IS NULL "
            "AND pending_threshold IS NULL AND pending_at IS NULL) "
            "OR (pending_family_id IS NOT NULL AND pending_family_source IS NOT NULL "
            "AND pending_at IS NOT NULL "
            "AND (pending_family_source = 'manual') = (pending_by IS NOT NULL) "
            "AND (pending_family_source <> 'manual') = (pending_suggestion_id IS NOT NULL) "
            "AND (pending_family_source = 'auto_suggestion') = (pending_threshold IS NOT NULL))",
        ),
        (CK_SEMANTIC_JOBS_SCHEMA_SUBJECT, "CK_SEMANTIC_JOBS_SCHEMA_SUBJECT",
         "(kind = 'family_schema') = (context_id IS NULL AND family_id IS NOT NULL)"),
        (CK_SEMANTIC_JOBS_CONTEXT_SUBJECT, "CK_SEMANTIC_JOBS_CONTEXT_SUBJECT",
         "(kind IN ('family_suggestion', 'context_values')) = (context_id IS NOT NULL)"),
        (CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND, "CK_SEMANTIC_JOBS_SCHEMA_ID_BY_KIND",
         "(kind IN ('family_schema', 'context_values')) = (schema_id IS NOT NULL)"),
        (CK_SEMANTIC_JOBS_RESULT_SUGGESTION_KIND, "CK_SEMANTIC_JOBS_RESULT_SUGGESTION_KIND",
         "result_suggestion_id IS NULL OR kind = 'family_suggestion'"),
        (
            CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR,
            "CK_FAMILY_SUGGESTIONS_DECISION_AUTHOR_PAIR",
            "(decided_by IS NULL) = "
            "(decision IS NULL OR decision IN ('auto_accepted', 'auto_pending', 'auto_superseded'))",
        ),
        (
            CK_EVENT_SUBJECT_BY_TYPE, "CK_EVENT_SUBJECT_BY_TYPE",
            "(event_type IN ('family_created', 'family_updated', 'family_activated', "
            "'family_archived', 'family_merged', 'family_schema_frozen', "
            "'family_schema_value_added', 'family_variants_merged')) = (family_id IS NOT NULL)",
        ),
    ]

    @pytest.mark.parametrize(
        ("model_expr", "name", "independent"), _CK_CASES, ids=[case[1] for case in _CK_CASES]
    )
    def test_check_expression_matches_migration_and_independent_literal(
        self, model_expr, name, independent
    ):
        assert model_expr == getattr(_current_migration_for(name), name)
        assert model_expr == independent

    def test_family_provenance_text_is_unchanged_by_the_migration(self):
        """Ветвь `(family_by IS NOT NULL) = (family_source = 'manual')` уже даёт
        `auto_suggestion => family_by IS NULL`: расширяется только список источников."""
        assert CK_CONTEXT_FAMILY_PROVENANCE == _migration_0017().CK_CONTEXT_FAMILY_PROVENANCE

    def test_subject_by_type_family_set_matches_independent_literal(self):
        match = re.search(r"event_type IN \(([^)]*)\)", CK_EVENT_SUBJECT_BY_TYPE)
        assert match is not None
        parsed = {piece.strip().strip("'") for piece in match.group(1).split(",")}
        assert parsed == {
            "family_created", "family_updated", "family_activated", "family_archived",
            "family_merged", "family_schema_frozen", "family_schema_value_added",
            "family_variants_merged",
        }


# ---------------------------------------------------------------------------
#  14. Шесть таблиц — явно в _DOMAIN_TABLES, очистка их чистит
# ---------------------------------------------------------------------------

class TestDomainTablesCleanup:
    def test_all_six_tables_listed_explicitly(self):
        from tests.conftest import _DOMAIN_TABLES

        for table in _NEW_TABLES:
            assert table in _DOMAIN_TABLES

    def test_truncate_clears_all_six_tables(
        self, db_engine, committing_db, committing_factories
    ):
        from tests.conftest import _truncate_domain_tables

        session = committing_db
        family, schema, parameter, value, variant = _chain(session, committing_factories)
        context = _assigned_context(session, committing_factories, family)
        session.add(
            WorkVariantValue(
                variant_id=variant.id, schema_id=schema.id, parameter_id=parameter.id,
                value_id=value.id,
            )
        )
        session.add(
            ContextParameterValue(
                context_id=context.id, schema_id=schema.id, parameter_id=parameter.id,
                value_id=value.id, source="name",
            )
        )
        session.commit()

        def counts():
            with db_engine.connect() as conn:
                return {
                    table: conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
                    for table in _NEW_TABLES
                }

        assert all(count >= 1 for count in counts().values())
        session.close()
        _truncate_domain_tables(db_engine)
        assert counts() == {table: 0 for table in _NEW_TABLES}


# ---------------------------------------------------------------------------
#  15. downgrade: десять носителей — чистая логика и счётчики на живой схеме
# ---------------------------------------------------------------------------

_CARRIERS = {
    "work_variants": "вариантов — 5",
    "family_parameter_schemas": "версий схем — 5",
    "context_parameter_values": "значений контекстов — 5",
    "catalog_contexts_with_variant": "контекстов с вариантом — 5",
    "catalog_contexts_with_pending": "контекстов с ожиданием — 5",
    "catalog_contexts_auto_source": "контекстов с источником семьи auto_suggestion — 5",
    "semantic_jobs_new_kinds": "заданий новых видов — 5",
    "family_suggestions_new_decisions": "предложений с новыми решениями — 5",
    "semantic_events_new_types": "событий новых типов — 5",
    "semantic_reconcile_batches_other_kinds": "пачек с отпечатками других видов — 5",
}


class TestDowngradeRefusalLogic:
    """Чистая логика `_downgrade_refusal`, без БД: каждый из десяти счётчиков —
    единственный положительный, значение (5) отличительное."""

    _ZERO = {key: 0 for key in _CARRIERS}

    def test_carriers_are_ten(self):
        assert len(_CARRIERS) == 10

    @pytest.mark.parametrize("key", sorted(_CARRIERS))
    def test_each_counter_alone_blocks_and_is_named(self, key):
        blockers = dict(self._ZERO)
        blockers[key] = 5
        refusal = _migration_0019()._downgrade_refusal(blockers)
        assert refusal is not None
        assert _CARRIERS[key] in refusal
        for other, phrase in _CARRIERS.items():
            if other != key:
                assert phrase not in refusal

    def test_all_zero_blockers_pass(self):
        assert _migration_0019()._downgrade_refusal(dict(self._ZERO)) is None


class TestDowngradeBlockersLive:
    """Каждый носитель — свой вход на живой схеме: считается ровно его счётчик,
    остальные нули, отказ называет носитель."""

    def _blockers(self, db_session):
        m = _migration_0019()
        blockers = m._downgrade_blockers(db_session.connection())
        return blockers, m._downgrade_refusal(blockers)

    def _assert_only(self, db_session, key, expected=1):
        blockers, refusal = self._blockers(db_session)
        assert blockers == {k: (expected if k == key else 0) for k in _CARRIERS}
        assert refusal is not None
        assert _CARRIERS[key].replace("5", str(expected)) in refusal

    def test_clean_schema_does_not_block(self, db_session):
        blockers, refusal = self._blockers(db_session)
        assert blockers == {key: 0 for key in _CARRIERS}
        assert refusal is None

    def test_variants_counted_by_their_own_table(self, db_session, factories):
        family, schema, *_ = _chain(db_session, factories)
        _variant(db_session, family, schema)
        blockers, _ = self._blockers(db_session)
        assert blockers["work_variants"] == 2
        assert blockers["family_parameter_schemas"] == 1

    def test_schemas_carrier(self, db_session, factories):
        _schema(db_session, factories)
        _schema(db_session, factories, status="cancelled")
        self._assert_only(db_session, "family_parameter_schemas", expected=2)

    def test_context_values_carrier(self, db_session, factories):
        """Значения контекста без варианта и без ожидания: считается только их
        таблица (версия схемы при этом есть, но её счётчик другой)."""
        family, schema, parameter, value, _variant_row = _chain(db_session, factories)
        context = _assigned_context(db_session, factories, family)
        db_session.add(
            ContextParameterValue(
                context_id=context.id, schema_id=schema.id, parameter_id=parameter.id,
                value_id=value.id, source="name",
            )
        )
        db_session.flush()
        blockers, refusal = self._blockers(db_session)
        assert blockers["context_parameter_values"] == 1
        assert blockers["catalog_contexts_with_variant"] == 0
        assert blockers["catalog_contexts_with_pending"] == 0
        assert "значений контекстов — 1" in refusal

    def test_context_with_variant_carrier(self, db_session, factories):
        family, _s, _p, _v, variant = _chain(db_session, factories)
        _assigned_context(
            db_session, factories, family, work_variant_id=variant.id, variant_at=_now(),
            variant_paths_hash="h",
        )
        blockers, refusal = self._blockers(db_session)
        assert blockers["catalog_contexts_with_variant"] == 1
        assert blockers["catalog_contexts_with_pending"] == 0
        assert blockers["context_parameter_values"] == 0
        assert "контекстов с вариантом — 1" in refusal

    def test_context_with_pending_carrier(self, db_session, factories):
        family = _family(db_session)
        user = factories.UserFactory.create()
        _context(
            db_session, factories, pending_family_id=family.id, pending_family_source="manual",
            pending_by=user.id, pending_at=_now(),
        )
        self._assert_only(db_session, "catalog_contexts_with_pending")

    def test_context_with_auto_source_carrier(self, db_session, factories):
        """Контекст с автопринятой семьёй без ожидания и без варианта: считается
        только его носитель."""
        family = _family(db_session)
        _context(
            db_session, factories, work_family_id=family.id,
            family_source="auto_suggestion", family_at=_now(), family_by=None,
        )
        self._assert_only(db_session, "catalog_contexts_auto_source")

    def test_context_with_manual_or_suggestion_source_does_not_block(
        self, db_session, factories
    ):
        family = _family(db_session)
        _assigned_context(db_session, factories, family)
        _context(
            db_session, factories, work_family_id=family.id, family_source="suggestion",
            family_at=_now(), family_by=None,
        )
        blockers, refusal = self._blockers(db_session)
        assert blockers["catalog_contexts_auto_source"] == 0
        assert refusal is None

    def test_job_of_new_kind_carrier(self, db_session, factories):
        schema = _schema(db_session, factories, status="building")
        _job(
            db_session, factories, kind="family_schema", context_id=None,
            family_id=schema.family_id, schema_id=schema.id,
        )
        blockers, refusal = self._blockers(db_session)
        assert blockers["semantic_jobs_new_kinds"] == 1
        assert blockers["family_parameter_schemas"] == 1
        assert "заданий новых видов — 1" in refusal

    def test_suggestion_jobs_alone_do_not_block(self, db_session, factories):
        _job(db_session, factories)
        blockers, refusal = self._blockers(db_session)
        assert blockers == {key: 0 for key in _CARRIERS}
        assert refusal is None

    @pytest.mark.parametrize(
        "decision", ["accepted_pending", "auto_accepted", "auto_pending", "auto_superseded"]
    )
    def test_suggestion_with_new_decision_carrier(self, db_session, factories, decision):
        author = factories.UserFactory.create().id if decision == "accepted_pending" else None
        _suggestion(
            db_session, factories, decision=decision, decided_by=author, decided_at=_now()
        )
        self._assert_only(db_session, "family_suggestions_new_decisions")

    @pytest.mark.parametrize("decision", ["accepted", "rejected", "other_family", "family_created"])
    def test_suggestion_with_old_decision_does_not_block(self, db_session, factories, decision):
        user = factories.UserFactory.create()
        _suggestion(
            db_session, factories, decision=decision, decided_by=user.id, decided_at=_now()
        )
        blockers, refusal = self._blockers(db_session)
        assert blockers["family_suggestions_new_decisions"] == 0
        assert refusal is None

    @pytest.mark.parametrize(
        ("event_type", "subject"),
        [(t, "context") for t in _CONTEXT_EVENT_TYPES]
        + [(t, "family") for t in _FAMILY_EVENT_TYPES],
    )
    def test_event_of_new_type_carrier(self, db_session, factories, event_type, subject):
        if subject == "context":
            _event(db_session, event_type, context_id=_context(db_session, factories).id)
        else:
            _event(db_session, event_type, family_id=_family(db_session).id)
        self._assert_only(db_session, "semantic_events_new_types")

    def test_event_of_old_type_does_not_block(self, db_session, factories):
        _event(db_session, "context_created", context_id=_context(db_session, factories).id)
        blockers, refusal = self._blockers(db_session)
        assert blockers["semantic_events_new_types"] == 0
        assert refusal is None

    def test_batch_with_non_suggestion_fingerprint_carrier(self, db_session, factories):
        _batch(
            db_session, factories,
            held_fingerprints=[
                {"kind": "family_suggestion", "context_id": 1, "family_id": None,
                 "schema_id": None, "request_hash": "a"},
                {"kind": "context_values", "context_id": 2, "family_id": 3, "schema_id": 4,
                 "request_hash": "b"},
            ],
        )
        self._assert_only(db_session, "semantic_reconcile_batches_other_kinds")

    def test_batch_of_suggestion_fingerprints_only_does_not_block(self, db_session, factories):
        _batch(
            db_session, factories,
            held_fingerprints=[
                {"kind": "family_suggestion", "context_id": 1, "family_id": None,
                 "schema_id": None, "request_hash": "a"},
            ],
        )
        _batch(db_session, factories, held_fingerprints=[[1, "legacy-pair"]])
        blockers, refusal = self._blockers(db_session)
        assert blockers["semantic_reconcile_batches_other_kinds"] == 0
        assert refusal is None

    def test_batches_are_counted_per_batch_not_per_fingerprint(self, db_session, factories):
        other = {"kind": "family_schema", "context_id": None, "family_id": 1, "schema_id": 2,
                 "request_hash": "x"}
        _batch(db_session, factories, held_fingerprints=[other, dict(other, request_hash="y")])
        self._assert_only(db_session, "semantic_reconcile_batches_other_kinds", expected=1)


class TestFingerprintFormatHelpers:
    """Перевод формата пачек — чистые функции миграции (без БД)."""

    def test_pairs_become_sorted_objects_with_null_subjects(self):
        m = _migration_0019()
        result = m._fingerprints_to_objects([[7, "bbb"], [3, "ccc"], [3, "aaa"]])
        assert result == [
            {"kind": "family_suggestion", "context_id": 3, "family_id": None,
             "schema_id": None, "request_hash": "aaa"},
            {"kind": "family_suggestion", "context_id": 3, "family_id": None,
             "schema_id": None, "request_hash": "ccc"},
            {"kind": "family_suggestion", "context_id": 7, "family_id": None,
             "schema_id": None, "request_hash": "bbb"},
        ]

    def test_objects_become_sorted_pairs(self):
        m = _migration_0019()
        objects = [
            {"kind": "family_suggestion", "context_id": 7, "family_id": None,
             "schema_id": None, "request_hash": "bbb"},
            {"kind": "family_suggestion", "context_id": 3, "family_id": None,
             "schema_id": None, "request_hash": "aaa"},
        ]
        assert m._fingerprints_to_pairs(objects) == [[3, "aaa"], [7, "bbb"]]

    def test_sort_key_orders_missing_subject_first_within_kind(self):
        m = _migration_0019()
        with_context = {"kind": "context_values", "context_id": 5, "family_id": 1,
                        "schema_id": 2, "request_hash": "a"}
        without_context = {"kind": "context_values", "context_id": None, "family_id": 9,
                           "schema_id": 9, "request_hash": "a"}
        assert m._fingerprints_to_objects([with_context, without_context]) == [
            without_context, with_context,
        ]

    def test_canonical_hash_is_independent_of_dict_key_order_and_keeps_non_ascii(self):
        m = _migration_0019()
        a = [{"kind": "family_suggestion", "context_id": 1, "family_id": None,
              "schema_id": None, "request_hash": "хэш"}]
        b = [{"request_hash": "хэш", "schema_id": None, "family_id": None, "context_id": 1,
              "kind": "family_suggestion"}]
        expected = hashlib.sha256(
            '[{"context_id":1,"family_id":null,"kind":"family_suggestion",'
            '"request_hash":"хэш","schema_id":null}]'.encode()
        ).hexdigest()
        assert m._canonical_hash(a) == m._canonical_hash(b) == expected


# ---------------------------------------------------------------------------
#  16. Живые прогоны alembic на scratch-базе: upgrade с данными фичи 2 и downgrade
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _scratch_alembic(label: str):
    """Отдельная база и конфиг alembic: сессионная тестовая БД не трогается —
    `downgrade` на ней сломал бы все остальные тесты."""
    test_url = os.environ.get("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL не задан")

    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    from alembic import command
    from alembic.config import Config
    from db_guard import ensure_mutation_allowed
    from tests.conftest import _create_worker_database

    parsed = make_url(test_url)
    base = (parsed.database or "gca").removesuffix("_test") or "gca"
    scratch_name = f"{base}_scratch_{_uid()}_test"
    scratch_url = parsed.set(database=scratch_name).render_as_string(hide_password=False)
    ensure_mutation_allowed(scratch_url, f"test_work_variants_schema scratch db ({label})")
    _create_worker_database(scratch_url)

    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", scratch_url)
    environ_snapshot = dict(os.environ)
    try:
        yield command, cfg, scratch_url
    finally:
        os.environ.clear()
        os.environ.update(environ_snapshot)
        with psycopg.connect(
            host=parsed.host, port=parsed.port, user=parsed.username,
            password=parsed.password, dbname="postgres", autocommit=True,
        ) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(scratch_name)
                )
            )


def _seed_feature2_data(conn) -> dict:
    """Данные фичи 2 в схеме 0018 (колонки `kind` ещё нет): три контекста, три
    задания, попытка, предложение и три удержанные пачки в формате пар."""
    position_id = conn.execute(
        sa.text(
            "INSERT INTO catalog_positions "
            "(standard_job_title, normalized_job_title, kind, status) "
            "VALUES ('Работа (scratch)', 'работа (scratch)', 'POSITION', 'na') RETURNING id"
        )
    ).scalar_one()
    bucket_id = conn.execute(
        sa.text("INSERT INTO context_buckets (catalog_position_id) VALUES (:cp) RETURNING id"),
        {"cp": position_id},
    ).scalar_one()
    context_ids = []
    for _ in range(3):
        context_ids.append(
            conn.execute(
                sa.text(
                    "INSERT INTO catalog_contexts "
                    "(bucket_id, semantic_kind, semantic_kind_source, semantic_kind_at, "
                    " name_role, name_role_source, name_role_at, place_dictionary_version, "
                    " semantic_state) "
                    "VALUES (:b, 'WORK', 'rule', now(), 'WORK', 'rule', now(), 1, 'SUGGESTED') "
                    "RETURNING id"
                ),
                {"b": bucket_id},
            ).scalar_one()
        )
    job_columns = (
        "context_id, request_hash, status, cancel_reason, prompt_version, model_requested, "
        "place_dictionary_version, candidates_hash, prefix_hash, input_hash, "
        "response_schema_version, serialization_version"
    )
    job_ids = []
    for context_id, status, reason in (
        (context_ids[0], "pending", None),
        (context_ids[1], "done", None),
        (context_ids[2], "cancelled", "input_changed"),
    ):
        job_ids.append(
            conn.execute(
                sa.text(
                    f"INSERT INTO semantic_jobs ({job_columns}) "
                    "VALUES (:c, :h, :s, :r, '1', 'm', 1, 'c', 'p', 'i', '1', '1') RETURNING id"
                ),
                {"c": context_id, "h": f"job-{context_id}", "s": status, "r": reason},
            ).scalar_one()
        )
    attempt_id = conn.execute(
        sa.text(
            "INSERT INTO semantic_job_attempts "
            "(job_id, claim_token, retry_generation, started_at, reserve_usd, prefix_hash, "
            " privacy_dictionary_hash) "
            "VALUES (:j, :t, 0, now(), 0.1, 'p', 'd') RETURNING id"
        ),
        {"j": job_ids[1], "t": str(uuid.uuid4())},
    ).scalar_one()
    suggestion_id = conn.execute(
        sa.text(
            "INSERT INTO family_suggestions "
            "(context_id, job_id, attempt_id, request_hash, candidates_hash, "
            " candidates_snapshot, confidence, reason, is_published) "
            "VALUES (:c, :j, :a, :h, 'c', CAST('{}' AS jsonb), 0.9, 'r', true) RETURNING id"
        ),
        {"c": context_ids[1], "j": job_ids[1], "a": attempt_id, "h": f"job-{context_ids[1]}"},
    ).scalar_one()
    conn.execute(
        sa.text("UPDATE semantic_jobs SET result_suggestion_id = :s WHERE id = :j"),
        {"s": suggestion_id, "j": job_ids[1]},
    )

    c1, c2, c3 = context_ids
    # Старый формат (спека 2): пары, хэш — sha256 списка пар, отсортированного
    # по (context_id, request_hash). Каноны собраны вручную, не кодом миграции.
    batches = (
        ([[c2, "bbb"], [c1, "aaa"]], f'[[{c1},"aaa"],[{c2},"bbb"]]'),
        ([[c3, "хэш-запроса"]], f'[[{c3},"хэш-запроса"]]'),
        ([], "[]"),
    )
    for pairs, canonical_pairs in batches:
        conn.execute(
            sa.text(
                "INSERT INTO semantic_reconcile_batches "
                "(source, held_fingerprints, fingerprints_hash, contexts_count, "
                " reserve_estimate_usd, cached_estimate_usd, status) "
                "VALUES ('import', CAST(:held AS jsonb), :hash, :n, 1, 0, 'held')"
            ),
            {
                "held": json.dumps(pairs, ensure_ascii=False),
                "hash": hashlib.sha256(canonical_pairs.encode()).hexdigest(),
                "n": len(pairs),
            },
        )
    return {"contexts": context_ids, "jobs": job_ids, "suggestion": suggestion_id}


@pytest.mark.migration_roundtrip
class TestUpgradeAndDowngradeOnFeature2Data:
    def test_upgrade_converts_jobs_and_batches_then_downgrade_restores_pairs(self):
        with _scratch_alembic("feature 2 data") as (command, cfg, scratch_url):
            command.upgrade(cfg, "0018")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    seeded = _seed_feature2_data(conn)
                c1, c2, c3 = seeded["contexts"]

                command.upgrade(cfg, "head")

                def read_batches(conn):
                    return {
                        count: (held, digest)
                        for count, held, digest in conn.execute(
                            sa.text(
                                "SELECT contexts_count, held_fingerprints, fingerprints_hash "
                                "FROM semantic_reconcile_batches ORDER BY id"
                            )
                        ).all()
                    }

                with engine.connect() as conn:
                    kinds = conn.execute(
                        sa.text("SELECT kind, count(*) FROM semantic_jobs GROUP BY kind")
                    ).all()
                    suggestion_kept = conn.execute(
                        sa.text("SELECT job_id, decision FROM family_suggestions")
                    ).all()
                    batches = read_batches(conn)
                assert kinds == [("family_suggestion", 3)]
                assert suggestion_kept == [(seeded["jobs"][1], None)]

                def item(context_id, request_hash):
                    return {
                        "kind": "family_suggestion", "context_id": context_id,
                        "family_id": None, "schema_id": None, "request_hash": request_hash,
                    }

                # Независимое вычисление: канонический JSON собран вручную
                # (ключи по алфавиту, без пробелов, не-ASCII как есть).
                def canonical(context_id, request_hash):
                    return (
                        f'{{"context_id":{context_id},"family_id":null,'
                        f'"kind":"family_suggestion","request_hash":"{request_hash}",'
                        f'"schema_id":null}}'
                    )

                expected_unsorted_json = f"[{canonical(c1, 'aaa')},{canonical(c2, 'bbb')}]"
                expected_non_ascii_json = f"[{canonical(c3, 'хэш-запроса')}]"
                assert batches[2] == (
                    [item(c1, "aaa"), item(c2, "bbb")],
                    hashlib.sha256(expected_unsorted_json.encode()).hexdigest(),
                )
                assert batches[1] == (
                    [item(c3, "хэш-запроса")],
                    hashlib.sha256(expected_non_ascii_json.encode()).hexdigest(),
                )
                assert batches[0] == ([], hashlib.sha256(b"[]").hexdigest())

                command.downgrade(cfg, "0018")

                with engine.connect() as conn:
                    restored = read_batches(conn)
                    legacy_unique = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_constraint WHERE contype = 'u' "
                            "AND conname = 'uq_semantic_jobs_context_request_hash'"
                        )
                    ).scalar_one()
                    new_index = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_indexes "
                            "WHERE indexname = 'uq_semantic_jobs_subject_request_hash'"
                        )
                    ).scalar_one()
                    kind_column = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM information_schema.columns "
                            "WHERE table_name = 'semantic_jobs' AND column_name = 'kind'"
                        )
                    ).scalar_one()
                    context_nullable = conn.execute(
                        sa.text(
                            "SELECT is_nullable FROM information_schema.columns "
                            "WHERE table_name = 'semantic_jobs' AND column_name = 'context_id'"
                        )
                    ).scalar_one()
                    new_tables = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM information_schema.tables WHERE table_name IN "
                            "('family_parameter_schemas', 'family_parameters', "
                            " 'family_parameter_values', 'work_variants', "
                            " 'work_variant_values', 'context_parameter_values')"
                        )
                    ).scalar_one()
                    jobs_left = conn.execute(
                        sa.text("SELECT count(*) FROM semantic_jobs")
                    ).scalar_one()
                # Формат фичи 2: пары и прежний хэш (канон старой формы — вручную).
                assert restored[2] == (
                    [[c1, "aaa"], [c2, "bbb"]],
                    hashlib.sha256(f'[[{c1},"aaa"],[{c2},"bbb"]]'.encode()).hexdigest(),
                )
                assert restored[1] == (
                    [[c3, "хэш-запроса"]],
                    hashlib.sha256(f'[[{c3},"хэш-запроса"]]'.encode()).hexdigest(),
                )
                assert restored[0] == ([], hashlib.sha256(b"[]").hexdigest())
                assert legacy_unique == 1
                assert new_index == 0
                assert kind_column == 0
                assert context_nullable == "NO"
                assert new_tables == 0
                assert jobs_left == 3
            finally:
                engine.dispose()

    def test_downgrade_refused_by_event_of_new_type_on_scratch_db(self):
        with _scratch_alembic("downgrade refusal") as (command, cfg, scratch_url):
            command.upgrade(cfg, "head")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.begin() as conn:
                    family_id = conn.execute(
                        sa.text(
                            "INSERT INTO work_families (title, status, seed_key) "
                            "VALUES ('Семья (scratch)', 'draft', :k) RETURNING id"
                        ),
                        {"k": f"scratch-{_uid()}"},
                    ).scalar_one()
                    conn.execute(
                        sa.text(
                            "INSERT INTO semantic_events (family_id, event_type, payload) "
                            "VALUES (:f, 'family_schema_frozen', CAST('{\"k\": 1}' AS jsonb))"
                        ),
                        {"f": family_id},
                    )
                with pytest.raises(Exception, match="Откат 0019 невозможен") as exc:
                    command.downgrade(cfg, "0018")
                assert "событий новых типов — 1" in str(exc.value)
                # Отказ не оставил полуоткатанной схемы: ключ на месте.
                with engine.connect() as conn:
                    still = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_indexes "
                            "WHERE indexname = 'uq_semantic_jobs_subject_request_hash'"
                        )
                    ).scalar_one()
                assert still == 1
            finally:
                engine.dispose()

    def test_downgrade_then_upgrade_round_trip_on_empty_database(self):
        with _scratch_alembic("empty round trip") as (command, cfg, scratch_url):
            command.upgrade(cfg, "head")
            command.downgrade(cfg, "0018")
            command.upgrade(cfg, "head")
            engine = sa.create_engine(scratch_url)
            try:
                with engine.connect() as conn:
                    tables = conn.execute(
                        sa.text(
                            "SELECT count(*) FROM information_schema.tables WHERE table_name IN "
                            "('family_parameter_schemas', 'family_parameters', "
                            " 'family_parameter_values', 'work_variants', "
                            " 'work_variant_values', 'context_parameter_values')"
                        )
                    ).scalar_one()
                assert tables == 6
            finally:
                engine.dispose()
