"""Ядро варианта работы: значения, варианты, заморозка схемы, обработка
результата `context_values` и промоушен (спека
`2026-10-02-catalog-variants-design.md` §2.4, §2.6, §2.10).

Гонки двух сессий — в `test_work_variants_concurrency.py`. Здесь —
однопоточные входы: каждый шаг (0)-(8) обработчика и каждое заявление
вердикта проверяются своим входом, а доменные таблицы при «не применено»
сравниваются снимком до и после.

Помощники цепочки «семья -> схема -> параметр -> значение», контекста с путями
и ожидания импортируются из `test_work_variants_schema.py` и
`test_work_variants_material.py` (как это делает файл материала запросов).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import re
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    SemanticEvent,
    SemanticJob,
    ValueOrigin,
    WorkVariant,
    WorkVariantValue,
)
from services.semantic_answer import AnswerSchemaError
from services.variant_answer import SchemaAnswer, ValueItem, ValuesAnswer
from services.variant_request import (
    SchemaParameterIn,
    SubjectNotRenderable,
    load_values_material,
    paths_hash_of,
    render_request_for,
)
from services.work_variants import (
    ApplyValuesOutcome,
    JobGuard,
    apply_values,
    archive_variant_if_empty,
    canonical_value_id,
    freeze_schema,
    get_or_create_value,
    get_or_create_variant,
    normalize_value,
    values_key_of,
)
from tests.integration.test_work_variants_material import (
    _bind,
    _capturing_sql,
    _chain_context,
    _frozen_schema,
    _settings,
    _uid,
)
from tests.integration.test_work_variants_schema import (
    _family,
    _param,
    _schema,
    _value,
    _variant,
)

pytestmark = pytest.mark.integration


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# ---------------------------------------------------------------------------
#  normalize_value и values_key_of
# ---------------------------------------------------------------------------

class TestNormalizeValue:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("100 ММ", "100 мм"),
            ("  100   мм\t", "100 мм"),
            ("Ёлка", "елка"),
            ("елка", "елка"),
            ("«ПВХ»", "пвх"),
            ('"ПВХ"', "пвх"),
            ("ПВХ-«Эко»", "пвх- эко"),
            ("а б", "а б"),
            ("", ""),
            ("   ", ""),
            ("«»", ""),
            ('" "', ""),
        ],
    )
    def test_normalized_form(self, text, expected):
        assert normalize_value(text) == expected

    def test_same_value_in_different_spellings_has_one_norm(self):
        assert (
            normalize_value("Бетон  «М200»")
            == normalize_value("бетон м200")
            == normalize_value('БЕТОН "м200"')
        )

    def test_different_values_keep_different_norms(self):
        assert normalize_value("100 мм") != normalize_value("10 мм")


class TestValuesKeyOf:
    def test_pipe_joined_ordinals_ascending_with_question_mark_for_empty(self):
        assert values_key_of({1: 17, 2: None, 3: 42}) == "1=17|2=?|3=42"

    def test_ordinal_order_does_not_depend_on_input_order(self):
        assert values_key_of({3: 42, 1: 17, 2: None}) == "1=17|2=?|3=42"

    def test_all_empty(self):
        assert values_key_of({1: None, 2: None}) == "1=?|2=?"

    def test_single_parameter(self):
        assert values_key_of({1: 5}) == "1=5"

    def test_zero_parameters_give_the_empty_key(self):
        assert values_key_of({}) == ""


# ---------------------------------------------------------------------------
#  Цепочка и помощники
# ---------------------------------------------------------------------------

def _row_ids(db, schema):
    """`{ordinal: id параметра}` и `{(ordinal, текст): id значения}` версии."""
    parameters = {
        row.ordinal: row.id
        for row in db.execute(
            sa.select(FamilyParameter.id, FamilyParameter.ordinal).where(
                FamilyParameter.schema_id == schema.id
            )
        ).all()
    }
    values = {
        (row.ordinal, row.value): row.id
        for row in db.execute(
            sa.select(FamilyParameter.ordinal, FamilyParameterValue.value, FamilyParameterValue.id)
            .join(FamilyParameterValue, FamilyParameterValue.parameter_id == FamilyParameter.id)
            .where(FamilyParameter.schema_id == schema.id)
        ).all()
    }
    return parameters, values


_DEFAULT_PARAMS = (
    (1, "Толщина", ["50 мм", "100 мм"]),
    (2, "Материал", ["бетон", "кирпич"]),
)


def _world(
    db, factories, *, params=_DEFAULT_PARAMS, catalog_kind="TO_REVIEW", title=None,
    path_specs=None,
):
    """Семья с текущей схемой и привязанный к ней контекст на строке каталога
    нужного вида."""
    family = _family(db, definition="Стяжка пола")
    schema = _frozen_schema(db, factories, family, [list(p) for p in params])
    context_id, _ = _chain_context(
        db, factories, title=title or f"Стяжка {_uid()}",
        path_specs=path_specs or [(("Секция", "Полы"), 2)],
    )
    context = _bind(db, factories, context_id, family=family)
    catalog_id = db.execute(
        sa.select(ContextBucket.catalog_position_id).where(ContextBucket.id == context.bucket_id)
    ).scalar_one()
    db.get(CatalogPosition, catalog_id).kind = catalog_kind
    db.flush()
    parameters, values = _row_ids(db, schema)
    return SimpleNamespace(
        family=family, schema=schema, context_id=context_id, catalog_id=catalog_id,
        parameters=parameters, values=values,
    )


def _answer(*specs):
    """`ValuesAnswer` из кортежей `(ordinal, kind, value, source)`."""
    return ValuesAnswer(items=tuple(ValueItem(*spec) for spec in specs))


def _named(ordinal, text, source="name"):
    return (ordinal, "value", text, source)


def _default_answer():
    return _answer(_named(1, "50 мм"), _named(2, "бетон", "path"))


def _attach_variant(db, context_id, variant, *, paths_hash="old-paths"):
    context = db.get(CatalogContext, context_id)
    context.work_variant_id = variant.id
    context.variant_at = _now()
    context.variant_paths_hash = paths_hash
    db.flush()


def _set_pending(db, factories, context_id, family, source="manual", threshold="0.95"):
    """Ожидающее назначение, пройдя CHECK провенанса и ожидания."""
    from tests.integration.test_semantic_queue_schema import _suggestion

    context = db.get(CatalogContext, context_id)
    values = dict(pending_family_id=family.id, pending_family_source=source, pending_at=_now())
    suggestion = None
    if source == "manual":
        values["pending_by"] = factories.UserFactory.create().id
    else:
        suggestion = _suggestion(
            db, factories, context=context, family_id=family.id, new_family_name=None,
            is_published=True, confidence=Decimal("0.9"),
        )
        values["pending_suggestion_id"] = suggestion.id
    if source == "auto_suggestion":
        values["pending_threshold"] = Decimal(threshold)
    for key, value in values.items():
        setattr(context, key, value)
    db.flush()
    return suggestion


def _guarded(db, *, context_id, schema_id, token=None, status="running", expected_hash=None):
    """Задание `context_values` в очереди и охрана с ТЕКУЩИМ отпечатком (или
    заданным)."""
    probe = SemanticJob(
        kind="context_values", context_id=context_id, schema_id=schema_id, paths_hash="x"
    )
    rendered_hash = (
        expected_hash
        if expected_hash is not None
        else render_request_for(db, probe, settings=_settings()).request_hash
    )
    token = token or uuid.uuid4()
    job = SemanticJob(
        kind="context_values", context_id=context_id, schema_id=schema_id, paths_hash="x",
        request_hash=rendered_hash, status=status,
        claim_token=token if status == "running" else None,
        prompt_version="v1", model_requested="m", place_dictionary_version=1,
        candidates_hash="c", prefix_hash="p", input_hash="i", response_schema_version="v1",
        serialization_version="v1",
    )
    db.add(job)
    db.flush()
    return job, JobGuard(job_id=job.id, claim_token=token, expected_request_hash=rendered_hash)


def _apply(db, world, answer=None, *, guard=None, paths_hash="paths-1", context_id=None):
    return apply_values(
        db, context_id=context_id or world.context_id, schema_id=world.schema.id,
        answer=answer or _default_answer(), paths_hash=paths_hash, guard=guard,
        settings=_settings(),
    )


def _events(db, event_type, **where):
    db.expire_all()
    query = sa.select(SemanticEvent).where(SemanticEvent.event_type == event_type)
    for key, value in where.items():
        query = query.where(getattr(SemanticEvent, key) == value)
    return db.execute(query.order_by(SemanticEvent.id)).scalars().all()


def _table_rows(db, model, *where):
    db.expire_all()
    query = sa.select(model.__table__)
    for clause in where:
        query = query.where(clause)
    return sorted(
        (tuple(sorted(dict(row._mapping).items(), key=lambda kv: kv[0])) for row in db.execute(query)),
        key=repr,
    )


def _snapshot(db, world):
    """Всё, что необратимо меняет `apply_values`, одним сравнимым значением."""
    db.expire_all()
    return {
        "context": _table_rows(db, CatalogContext, CatalogContext.id == world.context_id),
        "catalog": _table_rows(db, CatalogPosition, CatalogPosition.id == world.catalog_id),
        "context_values": _table_rows(
            db, ContextParameterValue, ContextParameterValue.context_id == world.context_id
        ),
        "variants": _table_rows(db, WorkVariant),
        "variant_values": _table_rows(db, WorkVariantValue),
        "parameter_values": _table_rows(db, FamilyParameterValue),
        "events": db.execute(sa.select(sa.func.count()).select_from(SemanticEvent)).scalar_one(),
    }


def _job_snapshot(db, job):
    db.expire_all()
    return _table_rows(db, SemanticJob, SemanticJob.id == job.id)


class _StatementBudgetExceeded(AssertionError):
    pass


@contextlib.contextmanager
def _statement_budget(db, limit):
    """Обход цепочки, потерявший защиту от цикла, не кончается: после `limit`
    операторов на соединении сессии — `AssertionError`, а не зависание."""
    from sqlalchemy import event

    sent = [0]

    def _listener(conn, cursor, statement, parameters, context, executemany):
        sent[0] += 1
        if sent[0] > limit:
            raise _StatementBudgetExceeded(f"больше {limit} операторов: обход не кончается")

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", _listener)
    try:
        yield
    finally:
        event.remove(connection, "before_cursor_execute", _listener)


# ---------------------------------------------------------------------------
#  get_or_create_value и canonical_value_id
# ---------------------------------------------------------------------------

class TestGetOrCreateValue:
    def _parameter(self, db, factories):
        schema = _schema(db, factories)
        return _param(db, schema, 1, "Толщина")

    def test_creates_a_row_with_norm_and_origin(self, db_session, factories):
        parameter = self._parameter(db_session, factories)
        value_id, created = get_or_create_value(
            db_session, parameter_id=parameter.id, text="  Ёлка  100 ММ ",
            origin=ValueOrigin.extension,
        )
        assert created is True
        row = db_session.execute(
            sa.select(FamilyParameterValue).where(FamilyParameterValue.id == value_id)
        ).scalar_one()
        assert (row.value, row.value_norm, row.origin, row.merged_into_id) == (
            "Ёлка  100 ММ", "елка 100 мм", "extension", None,
        )

    @pytest.mark.parametrize("again", ["елка", "ЁЛКА", "  ёлка ", "«Ёлка»"])
    def test_same_norm_returns_the_same_row_and_is_not_created(
        self, db_session, factories, again
    ):
        parameter = self._parameter(db_session, factories)
        first, _ = get_or_create_value(
            db_session, parameter_id=parameter.id, text="Ёлка", origin=ValueOrigin.extension
        )
        second, created = get_or_create_value(
            db_session, parameter_id=parameter.id, text=again, origin=ValueOrigin.extension
        )
        assert (second, created) == (first, False)
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameterValue).where(
                FamilyParameterValue.parameter_id == parameter.id
            )
        ).scalar_one() == 1

    def test_other_parameter_gets_its_own_row(self, db_session, factories):
        schema = _schema(db_session, factories)
        first, second = _param(db_session, schema, 1, "А"), _param(db_session, schema, 2, "Б")
        a, _ = get_or_create_value(
            db_session, parameter_id=first.id, text="бетон", origin=ValueOrigin.extension
        )
        b, created = get_or_create_value(
            db_session, parameter_id=second.id, text="бетон", origin=ValueOrigin.extension
        )
        assert created is True and a != b

    def test_merged_synonym_in_other_case_with_yo_resolves_to_the_canonical_value(
        self, db_session, factories
    ):
        parameter = self._parameter(db_session, factories)
        canonical = _value(db_session, parameter, "Тёплый пол", value_norm="теплый пол")
        synonym = _value(
            db_session, parameter, "Подогрев Ёж", value_norm="подогрев еж"
        )
        synonym.merged_into_id = canonical.id
        db_session.flush()

        value_id, created = get_or_create_value(
            db_session, parameter_id=parameter.id, text="ПОДОГРЕВ ЁЖ", origin=ValueOrigin.extension
        )

        assert (value_id, created) == (canonical.id, False)

    def test_chain_of_merges_is_followed_to_the_end(self, db_session, factories):
        parameter = self._parameter(db_session, factories)
        end = _value(db_session, parameter, "конец")
        middle = _value(db_session, parameter, "середина")
        start = _value(db_session, parameter, "начало")
        middle.merged_into_id = end.id
        db_session.flush()
        start.merged_into_id = middle.id
        db_session.flush()
        value_id, created = get_or_create_value(
            db_session, parameter_id=parameter.id, text="Начало", origin=ValueOrigin.extension
        )
        assert (value_id, created) == (end.id, False)

    @pytest.mark.parametrize("text", ["", "   ", "«»", '" "'])
    def test_empty_after_normalization_is_a_schema_error_and_writes_nothing(
        self, db_session, factories, text
    ):
        parameter = self._parameter(db_session, factories)
        with pytest.raises(AnswerSchemaError) as raised:
            get_or_create_value(
                db_session, parameter_id=parameter.id, text=text, origin=ValueOrigin.extension
            )
        assert raised.value.code == "empty_value"
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameterValue).where(
                FamilyParameterValue.parameter_id == parameter.id
            )
        ).scalar_one() == 0


class TestCanonicalValueId:
    def test_unmerged_value_is_its_own_canonical(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories), 1, "А")
        value = _value(db_session, parameter, "а")
        assert canonical_value_id(db_session, value.id) == value.id

    def test_chain_of_two_is_followed(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories), 1, "А")
        end, middle, start = (_value(db_session, parameter, name) for name in ("к", "с", "н"))
        middle.merged_into_id = end.id
        db_session.flush()
        start.merged_into_id = middle.id
        db_session.flush()
        assert canonical_value_id(db_session, start.id) == end.id

    def test_a_closed_chain_is_refused(self, db_session, factories):
        parameter = _param(db_session, _schema(db_session, factories), 1, "А")
        first, second = _value(db_session, parameter, "п"), _value(db_session, parameter, "в")
        first.merged_into_id = second.id
        db_session.flush()
        second.merged_into_id = first.id
        db_session.flush()
        # Без защиты от цикла обход не кончается: бюджет операторов делает его
        # красным, а не зависшим (ревью задачи 5).
        with _statement_budget(db_session, 50), pytest.raises(ValueError, match="замкнута"):
            canonical_value_id(db_session, first.id)


# ---------------------------------------------------------------------------
#  get_or_create_variant и archive_variant_if_empty
# ---------------------------------------------------------------------------

def _two_parameter_schema(db, factories):
    family = _family(db)
    schema = _frozen_schema(db, factories, family, [list(p) for p in _DEFAULT_PARAMS])
    parameters, values = _row_ids(db, schema)
    return family, schema, parameters, values


class TestGetOrCreateVariant:
    def test_creates_variant_with_key_and_rows_per_parameter(self, db_session, factories):
        family, schema, parameters, values = _two_parameter_schema(db_session, factories)
        thickness, material = values[(1, "50 мм")], values[(2, "бетон")]

        variant, created = get_or_create_variant(
            db_session, family_id=family.id, schema_id=schema.id,
            value_ids_by_ordinal={1: thickness, 2: material},
        )

        assert created is True
        assert (variant.family_id, variant.schema_id, variant.status) == (
            family.id, schema.id, "active",
        )
        assert variant.values_key == f"1={thickness}|2={material}"
        rows = db_session.execute(
            sa.select(WorkVariantValue.parameter_id, WorkVariantValue.value_id).where(
                WorkVariantValue.variant_id == variant.id
            )
        ).all()
        assert sorted(rows) == sorted([(parameters[1], thickness), (parameters[2], material)])

    def test_same_set_is_the_same_variant(self, db_session, factories):
        family, schema, _parameters, values = _two_parameter_schema(db_session, factories)
        mapping = {1: values[(1, "50 мм")], 2: values[(2, "бетон")]}
        first, created_first = get_or_create_variant(
            db_session, family_id=family.id, schema_id=schema.id, value_ids_by_ordinal=mapping
        )
        second, created_second = get_or_create_variant(
            db_session, family_id=family.id, schema_id=schema.id, value_ids_by_ordinal=mapping
        )
        assert (second.id, created_first, created_second) == (first.id, True, False)

    def test_different_set_is_a_different_variant(self, db_session, factories):
        family, schema, _parameters, values = _two_parameter_schema(db_session, factories)
        a, _ = get_or_create_variant(
            db_session, family_id=family.id, schema_id=schema.id,
            value_ids_by_ordinal={1: values[(1, "50 мм")], 2: values[(2, "бетон")]},
        )
        b, _ = get_or_create_variant(
            db_session, family_id=family.id, schema_id=schema.id,
            value_ids_by_ordinal={1: values[(1, "50 мм")], 2: values[(2, "кирпич")]},
        )
        assert a.id != b.id

    def test_all_empty_is_a_variant_with_null_values(self, db_session, factories):
        family, schema, parameters, _values = _two_parameter_schema(db_session, factories)
        variant, created = get_or_create_variant(
            db_session, family_id=family.id, schema_id=schema.id,
            value_ids_by_ordinal={1: None, 2: None},
        )
        assert created is True and variant.values_key == "1=?|2=?"
        rows = db_session.execute(
            sa.select(WorkVariantValue.parameter_id, WorkVariantValue.value_id).where(
                WorkVariantValue.variant_id == variant.id
            )
        ).all()
        assert sorted(rows) == sorted([(parameters[1], None), (parameters[2], None)])

    def test_zero_parameter_schema_has_one_variant_with_empty_key(self, db_session, factories):
        family = _family(db_session)
        schema = _schema(db_session, factories, family)
        first, created_first = get_or_create_variant(
            db_session, family_id=family.id, schema_id=schema.id, value_ids_by_ordinal={}
        )
        second, created_second = get_or_create_variant(
            db_session, family_id=family.id, schema_id=schema.id, value_ids_by_ordinal={}
        )
        assert first.values_key == ""
        assert (second.id, created_first, created_second) == (first.id, True, False)
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(WorkVariantValue).where(
                WorkVariantValue.variant_id == first.id
            )
        ).scalar_one() == 0

    def test_archived_unmerged_variant_is_reactivated(self, db_session, factories):
        family, schema, _parameters, values = _two_parameter_schema(db_session, factories)
        mapping = {1: values[(1, "50 мм")], 2: None}
        archived = _variant(
            db_session, family, schema, values_key=values_key_of(mapping), status="archived"
        )
        variant, created = get_or_create_variant(
            db_session, family_id=family.id, schema_id=schema.id, value_ids_by_ordinal=mapping
        )
        assert (variant.id, created) == (archived.id, False)
        assert (variant.status, variant.archived_at) == ("active", None)

    def test_merged_variant_is_replaced_by_its_target(self, db_session, factories):
        family, schema, _parameters, values = _two_parameter_schema(db_session, factories)
        mapping = {1: values[(1, "50 мм")], 2: None}
        target = _variant(db_session, family, schema, values_key="target-key")
        _variant(
            db_session, family, schema, values_key=values_key_of(mapping), status="archived",
            merged_into_id=target.id,
        )
        variant, created = get_or_create_variant(
            db_session, family_id=family.id, schema_id=schema.id, value_ids_by_ordinal=mapping
        )
        assert (variant.id, created, variant.status) == (target.id, False, "active")

    def test_archived_target_of_a_merge_is_reactivated_too(self, db_session, factories):
        family, schema, _parameters, values = _two_parameter_schema(db_session, factories)
        mapping = {1: values[(1, "50 мм")], 2: None}
        target = _variant(db_session, family, schema, values_key="target-key", status="archived")
        _variant(
            db_session, family, schema, values_key=values_key_of(mapping), status="archived",
            merged_into_id=target.id,
        )
        variant, _created = get_or_create_variant(
            db_session, family_id=family.id, schema_id=schema.id, value_ids_by_ordinal=mapping
        )
        assert (variant.id, variant.status, variant.archived_at) == (target.id, "active", None)

    def test_a_closed_merge_chain_of_variants_is_refused(self, db_session, factories):
        family, schema, _parameters, values = _two_parameter_schema(db_session, factories)
        mapping = {1: values[(1, "50 мм")], 2: None}
        first = _variant(
            db_session, family, schema, values_key=values_key_of(mapping), status="archived"
        )
        second = _variant(
            db_session, family, schema, values_key="loop", status="archived",
            merged_into_id=first.id,
        )
        first.merged_into_id = second.id
        db_session.flush()
        with _statement_budget(db_session, 50), pytest.raises(ValueError, match="замкнута"):
            get_or_create_variant(
                db_session, family_id=family.id, schema_id=schema.id, value_ids_by_ordinal=mapping
            )

    @pytest.mark.parametrize("mapping", [{1: None}, {1: None, 2: None, 3: None}, {2: None, 3: None}])
    def test_ordinals_other_than_the_schema_parameters_are_refused(
        self, db_session, factories, mapping
    ):
        family, schema, _parameters, _values = _two_parameter_schema(db_session, factories)
        with pytest.raises(ValueError, match="ordinal"):
            get_or_create_variant(
                db_session, family_id=family.id, schema_id=schema.id,
                value_ids_by_ordinal=mapping,
            )

    def test_values_key_agrees_with_the_variant_value_rows(self, db_session, factories):
        family, schema, parameters, values = _two_parameter_schema(db_session, factories)
        for mapping in (
            {1: values[(1, "50 мм")], 2: values[(2, "бетон")]},
            {1: None, 2: values[(2, "кирпич")]},
            {1: None, 2: None},
        ):
            variant, _ = get_or_create_variant(
                db_session, family_id=family.id, schema_id=schema.id, value_ids_by_ordinal=mapping
            )
            by_parameter = {
                row.parameter_id: row.value_id
                for row in db_session.execute(
                    sa.select(WorkVariantValue.parameter_id, WorkVariantValue.value_id).where(
                        WorkVariantValue.variant_id == variant.id
                    )
                ).all()
            }
            rebuilt = values_key_of({o: by_parameter[pid] for o, pid in parameters.items()})
            assert variant.values_key == rebuilt


class TestArchiveVariantIfEmpty:
    def _chain(self, db, factories):
        family, schema, _parameters, _values = _two_parameter_schema(db, factories)
        variant = _variant(db, family, schema)
        return family, schema, variant

    def test_variant_without_contexts_is_archived(self, db_session, factories):
        _family_row, _schema_row, variant = self._chain(db_session, factories)
        assert archive_variant_if_empty(db_session, variant.id) is True
        db_session.refresh(variant)
        assert variant.status == "archived" and variant.archived_at is not None

    def test_variant_with_a_context_stays_active(self, db_session, factories):
        family, _schema_row, variant = self._chain(db_session, factories)
        world_context, _ = _chain_context(
            db_session, factories, title=f"Строка {_uid()}", path_specs=[((), 1)]
        )
        _bind(db_session, factories, world_context, family=family)
        _attach_variant(db_session, world_context, variant)
        assert archive_variant_if_empty(db_session, variant.id) is False
        db_session.refresh(variant)
        assert (variant.status, variant.archived_at) == ("active", None)

    def test_already_archived_variant_reports_false(self, db_session, factories):
        family, schema, _variant_row = self._chain(db_session, factories)
        archived = _variant(db_session, family, schema, status="archived")
        before = archived.archived_at
        assert archive_variant_if_empty(db_session, archived.id) is False
        db_session.refresh(archived)
        assert archived.archived_at == before


# ---------------------------------------------------------------------------
#  Операторы блокировки: режим каждого
# ---------------------------------------------------------------------------

def _sql(statement) -> str:
    from sqlalchemy.dialects import postgresql

    return str(statement.compile(dialect=postgresql.dialect()))


class TestLockStatements:
    def test_catalog_row_variant_context_and_job_are_for_update(self):
        from services import work_variants as wv

        for statement in (
            wv._lock_catalog_row_statement(1),
            wv._lock_variant_statement(1),
            wv._lock_context_statement(1),
            wv._lock_job_statement(1),
            wv._lock_schema_statement(1, share=False),
            wv._lock_current_schema_statement(1),
        ):
            assert re.search(r"FOR UPDATE\s*$", _sql(statement)), _sql(statement)

    def test_schema_version_in_the_values_handler_is_for_share(self):
        from services import work_variants as wv

        text = _sql(wv._lock_schema_statement(1, share=True))
        assert re.search(r"FOR SHARE\s*$", text) and "FOR UPDATE" not in text

    def test_families_are_locked_for_update_in_ascending_id_order(self):
        from services.work_families import _lock_families_statement

        text = _sql(_lock_families_statement([7, 3], exclusive=True))
        assert "ORDER BY work_families.id" in text and re.search(r"FOR UPDATE\s*$", text)


# ---------------------------------------------------------------------------
#  apply_values: порядок блокировок (шаг 0)
# ---------------------------------------------------------------------------

def _pending_world(db, factories, *, source="manual", params=((1, "Тип", ["а", "б"]),)):
    """Контекст семьи A с ожиданием семьи B (у B своя текущая схема). Поля
    `family`, `schema`, `parameters`, `values` описывают ОЖИДАЕМУЮ семью B —
    ту, по чьей схеме ставятся значения."""
    world = _world(db, factories)
    # Ожидаемая семья — живая привязка: обработчик применяет результат, только
    # если она `active` (спека вариантов §2.5 п. 6).
    family_b = _family(db, status="active", definition="Другая семья")
    schema_b = _frozen_schema(db, factories, family_b, [list(p) for p in params])
    suggestion = _set_pending(db, factories, world.context_id, family_b, source)
    parameters, values = _row_ids(db, schema_b)
    return SimpleNamespace(
        **{
            **vars(world), "family_a": world.family, "schema_a": world.schema,
            "family": family_b, "schema": schema_b, "parameters": parameters,
            "values": values, "suggestion": suggestion,
        }
    )


def _lock_sequence(statements):
    """`[(таблица, режим)]` операторов с `FOR UPDATE`/`FOR SHARE` в порядке
    отправки."""
    found = []
    for text in statements:
        mode = re.search(r"FOR (UPDATE|SHARE)\s*$", text.strip())
        table = re.search(r"\bFROM (\w+)", text)
        if mode and table:
            found.append((table.group(1), mode.group(1)))
    return found


class TestDomainLockOrder:
    def test_locks_follow_the_spec_order_with_modes(self, db_session, factories):
        world = _pending_world(db_session, factories)
        variant = _variant(db_session, world.family_a, world.schema_a)
        _attach_variant(db_session, world.context_id, variant)
        _job, guard = _guarded(
            db_session, context_id=world.context_id, schema_id=world.schema.id
        )

        with _capturing_sql(db_session) as statements:
            outcome = _apply(db_session, world, _answer(_named(1, "а")), guard=guard)

        assert outcome.applied is True
        assert _lock_sequence(statements)[:6] == [
            ("catalog_positions", "UPDATE"),
            ("work_families", "UPDATE"),
            ("family_parameter_schemas", "SHARE"),
            ("work_variants", "UPDATE"),
            ("catalog_contexts", "UPDATE"),
            ("semantic_jobs", "UPDATE"),
        ]

    def test_without_a_previous_variant_and_without_a_job_those_locks_are_skipped(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        with _capturing_sql(db_session) as statements:
            assert _apply(db_session, world).applied is True
        assert _lock_sequence(statements)[:4] == [
            ("catalog_positions", "UPDATE"),
            ("work_families", "UPDATE"),
            ("family_parameter_schemas", "SHARE"),
            ("catalog_contexts", "UPDATE"),
        ]

    def test_both_families_are_locked_by_one_statement_in_ascending_id_order(
        self, db_session, factories
    ):
        world = _pending_world(db_session, factories)
        captured: list[tuple[str, object]] = []
        from sqlalchemy import event

        def _listener(conn, cursor, statement, parameters, context, executemany):
            if "FROM work_families" in statement and "FOR UPDATE" in statement:
                captured.append((statement, parameters))

        connection = db_session.connection()
        event.listen(connection, "before_cursor_execute", _listener)
        try:
            _apply(db_session, world, _answer(_named(1, "а")))
        finally:
            event.remove(connection, "before_cursor_execute", _listener)

        assert len(captured) == 1
        statement, parameters = captured[0]
        assert "ORDER BY work_families.id" in statement
        assert sorted(v for v in parameters.values() if isinstance(v, int)) == sorted(
            [world.family_a.id, world.family.id]
        )


# ---------------------------------------------------------------------------
#  apply_values: вердикт под блокировками (шаг 1)
# ---------------------------------------------------------------------------

def _archive_context(db, world):
    db.get(CatalogContext, world.context_id).archived_at = _now()


def _drop_members(db, world):
    db.execute(sa.delete(ContextMember).where(ContextMember.context_id == world.context_id))


def _state_not_applicable(db, world):
    db.get(CatalogContext, world.context_id).semantic_state = "NOT_APPLICABLE"


def _kind_system(db, world):
    db.get(CatalogContext, world.context_id).semantic_kind = "SYSTEM"


def _catalog_kind(kind):
    def _set(db, world):
        db.get(CatalogPosition, world.catalog_id).kind = kind

    return _set


def _drop_family(db, world):
    context = db.get(CatalogContext, world.context_id)
    context.work_family_id = None
    context.family_source = None
    context.family_by = None
    context.family_at = None


def _supersede_schema(db, world):
    schema = db.get(FamilyParameterSchema, world.schema.id)
    schema.status = "superseded"
    schema.superseded_at = _now()


_NOT_APPLICABLE_CASES = [
    ("archived_context", _archive_context),
    ("no_members", _drop_members),
    ("state_not_applicable", _state_not_applicable),
    ("catalog_header", _catalog_kind("HEADER")),
    ("catalog_trash", _catalog_kind("TRASH")),
    ("catalog_lot_header", _catalog_kind("LOT_HEADER")),
    ("no_family_no_pending", _drop_family),
    ("schema_not_current", _supersede_schema),
]


class TestVerdictNotApplicable:
    @pytest.mark.parametrize(("case", "break_it"), _NOT_APPLICABLE_CASES,
                             ids=[c[0] for c in _NOT_APPLICABLE_CASES])
    def test_guarded_owned_job_is_cancelled_and_domain_is_untouched(
        self, db_session, factories, case, break_it
    ):
        world = _world(db_session, factories)
        job, guard = _guarded(db_session, context_id=world.context_id, schema_id=world.schema.id)
        break_it(db_session, world)
        db_session.flush()
        before = _snapshot(db_session, world)

        outcome = _apply(db_session, world, guard=guard)

        assert outcome == ApplyValuesOutcome(
            applied=False, unapplied_reason="not_applicable", variant_id=None,
            previous_variant_id=None, promoted=False, family_switched=False,
            values_added=(), previous_archived=False,
        )
        assert _snapshot(db_session, world) == before
        db_session.refresh(job)
        assert (job.status, job.cancel_reason, job.claim_token) == (
            "cancelled", "not_applicable", None,
        )

    @pytest.mark.parametrize(("case", "break_it"), _NOT_APPLICABLE_CASES,
                             ids=[c[0] for c in _NOT_APPLICABLE_CASES])
    def test_without_a_job_only_applicability_decides(
        self, db_session, factories, case, break_it
    ):
        world = _world(db_session, factories)
        break_it(db_session, world)
        db_session.flush()
        before = _snapshot(db_session, world)
        outcome = _apply(db_session, world, guard=None)
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        assert _snapshot(db_session, world) == before

    def test_system_kind_does_not_make_a_guarded_job_inapplicable(self, db_session, factories):
        world = _world(db_session, factories)
        job, guard = _guarded(db_session, context_id=world.context_id, schema_id=world.schema.id)
        _kind_system(db_session, world)
        db_session.flush()

        outcome = _apply(db_session, world, guard=guard)

        assert outcome.applied is True
        assert outcome.unapplied_reason is None

    def test_system_kind_without_a_job_is_applied_like_work(self, db_session, factories):
        world = _world(db_session, factories)
        _kind_system(db_session, world)
        db_session.flush()

        outcome = _apply(db_session, world, guard=None)

        assert (outcome.applied, outcome.unapplied_reason) == (True, None)

    def test_schema_of_another_family_is_not_applicable(self, db_session, factories):
        world = _world(db_session, factories)
        other_family = _family(db_session)
        other_schema = _frozen_schema(db_session, factories, other_family, [(1, "Х", ["а"])])
        before = _snapshot(db_session, world)
        outcome = apply_values(
            db_session, context_id=world.context_id, schema_id=other_schema.id,
            answer=_answer(_named(1, "а")), paths_hash="p", guard=None, settings=_settings(),
        )
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        assert _snapshot(db_session, world) == before

    def test_missing_schema_version_is_not_applicable(self, db_session, factories):
        # Ревью задачи 5: ветка `schema is None` — версии с таким `id` нет.
        world = _world(db_session, factories)
        before = _snapshot(db_session, world)
        outcome = apply_values(
            db_session, context_id=world.context_id, schema_id=987654321,
            answer=_default_answer(), paths_hash="p", guard=None, settings=_settings(),
        )
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        assert _snapshot(db_session, world) == before

    def test_context_with_a_cyclic_chapter_chain_is_not_renderable(self, db_session, factories):
        world = _world(
            db_session, factories,
            path_specs=[(("Внешний",), 1), (("Внутренний",), 1)],
        )
        job, guard = _guarded(db_session, context_id=world.context_id, schema_id=world.schema.id)
        from models import PositionItem

        members = db_session.execute(
            sa.select(ContextMember.position_item_id).where(
                ContextMember.context_id == world.context_id
            )
        ).scalars().all()
        chapters = {db_session.get(PositionItem, pid).chapter_item_id for pid in members}
        outer, inner = sorted(chapters)
        db_session.get(PositionItem, outer).chapter_item_id = inner
        db_session.get(PositionItem, inner).chapter_item_id = outer
        db_session.flush()
        db_session.expire_all()
        with pytest.raises(SubjectNotRenderable):
            render_request_for(db_session, job, settings=_settings())
        before = _snapshot(db_session, world)

        outcome = _apply(db_session, world, guard=guard)

        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        assert _snapshot(db_session, world) == before


class TestVerdictStaleFingerprint:
    def test_wrong_expected_hash_cancels_the_job_with_input_changed(self, db_session, factories):
        world = _world(db_session, factories)
        job, guard = _guarded(
            db_session, context_id=world.context_id, schema_id=world.schema.id,
            expected_hash="0" * 64,
        )
        before = _snapshot(db_session, world)

        outcome = _apply(db_session, world, guard=guard)

        assert outcome.applied is False and outcome.unapplied_reason == "stale_fingerprint"
        assert _snapshot(db_session, world) == before
        db_session.refresh(job)
        assert (job.status, job.cancel_reason, job.claim_token) == (
            "cancelled", "input_changed", None,
        )

    def test_a_changed_catalog_title_after_the_guard_was_built_is_stale(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        _job, guard = _guarded(db_session, context_id=world.context_id, schema_id=world.schema.id)
        db_session.get(CatalogPosition, world.catalog_id).standard_job_title = "Другое название"
        db_session.flush()
        before = _snapshot(db_session, world)
        outcome = _apply(db_session, world, guard=guard)
        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        assert _snapshot(db_session, world) == before

    def test_an_extended_value_list_after_the_guard_was_built_is_stale(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        _job, guard = _guarded(db_session, context_id=world.context_id, schema_id=world.schema.id)
        parameter = db_session.get(FamilyParameter, world.parameters[1])
        _value(db_session, parameter, "200 мм", origin="extension")
        before = _snapshot(db_session, world)
        outcome = _apply(db_session, world, guard=guard)
        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        assert _snapshot(db_session, world) == before

    def test_current_hash_is_applied(self, db_session, factories):
        world = _world(db_session, factories)
        _job, guard = _guarded(db_session, context_id=world.context_id, schema_id=world.schema.id)
        assert _apply(db_session, world, guard=guard).applied is True


class TestVerdictSubjectMovedBetweenReadAndLock:
    def test_a_context_that_changed_variant_after_the_unlocked_read_is_stale(
        self, db_session, factories
    ):
        from unittest import mock

        from services import work_variants as wv

        world = _world(db_session, factories)
        variant = _variant(db_session, world.family, world.schema, values_key="held")
        _attach_variant(db_session, world.context_id, variant)
        job, guard = _guarded(db_session, context_id=world.context_id, schema_id=world.schema.id)
        before = _snapshot(db_session, world)
        real = wv._read_subject
        calls: list[int] = []

        def read_before_the_variant_was_attached(db, context_id):
            calls.append(context_id)
            subject = real(db, context_id)
            if len(calls) == 1:
                return subject.__class__(
                    catalog_position_id=subject.catalog_position_id,
                    work_family_id=subject.work_family_id,
                    pending_family_id=subject.pending_family_id,
                    work_variant_id=None,
                )
            return subject

        with mock.patch.object(wv, "_read_subject", side_effect=read_before_the_variant_was_attached):
            outcome = _apply(db_session, world, guard=guard)

        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        assert len(calls) == 2
        assert _snapshot(db_session, world) == before
        db_session.refresh(job)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")

    def test_a_moved_subject_is_stale_even_when_it_is_also_not_applicable(
        self, db_session, factories
    ):
        """Ревью задачи 5: при разошедшемся предмете блокировки взяты не на те
        строки, и судить применимость под ними нельзя — устаревание
        проверяется первым."""
        from unittest import mock

        from services import work_variants as wv

        world = _world(db_session, factories)
        variant = _variant(db_session, world.family, world.schema, values_key="held")
        _attach_variant(db_session, world.context_id, variant)
        db_session.get(CatalogPosition, world.catalog_id).kind = "HEADER"
        db_session.flush()
        job, guard = _guarded(db_session, context_id=world.context_id, schema_id=world.schema.id)
        real = wv._read_subject
        calls: list[int] = []

        def first_read_without_the_variant(db, context_id):
            calls.append(context_id)
            subject = real(db, context_id)
            if len(calls) == 1:
                return subject.__class__(
                    catalog_position_id=subject.catalog_position_id,
                    work_family_id=subject.work_family_id,
                    pending_family_id=subject.pending_family_id,
                    work_variant_id=None,
                )
            return subject

        with mock.patch.object(wv, "_read_subject", side_effect=first_read_without_the_variant):
            outcome = _apply(db_session, world, guard=guard)

        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        db_session.refresh(job)
        assert job.cancel_reason == "input_changed"


class TestVerdictLostClaim:
    def _lost(self, db_session, factories, **job_kwargs):
        world = _world(db_session, factories)
        job, guard = _guarded(
            db_session, context_id=world.context_id, schema_id=world.schema.id, **job_kwargs
        )
        return world, job, JobGuard(
            job_id=job.id, claim_token=uuid.uuid4(), expected_request_hash=guard.expected_request_hash
        )

    def test_other_owner_job_and_domain_are_untouched(self, db_session, factories):
        world, job, stale_guard = self._lost(db_session, factories)
        owner_token = job.claim_token
        before = _snapshot(db_session, world)
        job_before = _job_snapshot(db_session, job)

        outcome = _apply(db_session, world, guard=stale_guard)

        assert outcome.applied is False and outcome.unapplied_reason == "lost_claim"
        assert _snapshot(db_session, world) == before
        assert _job_snapshot(db_session, job) == job_before
        db_session.refresh(job)
        assert (job.status, job.claim_token) == ("running", owner_token)

    @pytest.mark.parametrize("status", ["pending", "done", "error"])
    def test_job_that_is_not_running_is_not_ours(self, db_session, factories, status):
        world = _world(db_session, factories)
        job, guard = _guarded(
            db_session, context_id=world.context_id, schema_id=world.schema.id, status=status
        )
        before = _snapshot(db_session, world)
        job_before = _job_snapshot(db_session, job)
        outcome = _apply(db_session, world, guard=JobGuard(
            job_id=job.id, claim_token=uuid.uuid4(),
            expected_request_hash=guard.expected_request_hash,
        ))
        assert (outcome.applied, outcome.unapplied_reason) == (False, "lost_claim")
        assert _snapshot(db_session, world) == before
        assert _job_snapshot(db_session, job) == job_before

    def test_lost_claim_wins_over_a_non_applicable_subject(self, db_session, factories):
        world, job, stale_guard = self._lost(db_session, factories)
        _archive_context(db_session, world)
        db_session.flush()
        job_before = _job_snapshot(db_session, job)
        outcome = _apply(db_session, world, guard=stale_guard)
        assert outcome.unapplied_reason == "lost_claim"
        assert _job_snapshot(db_session, job) == job_before

    def test_a_job_of_another_context_is_a_call_error(self, db_session, factories):
        world = _world(db_session, factories)
        other = _world(db_session, factories)
        _job, guard = _guarded(db_session, context_id=other.context_id, schema_id=other.schema.id)
        with pytest.raises(ValueError, match="не относится"):
            _apply(db_session, world, guard=guard)


# ---------------------------------------------------------------------------
#  apply_values: шаги (1а)-(8)
# ---------------------------------------------------------------------------

class TestStep1aOrdinalSet:
    @pytest.mark.parametrize(
        "specs",
        [
            [(1, "value", "50 мм", "name")],
            [(1, "value", "50 мм", "name"), (2, "value", "бетон", "name"), (3, "none", None, None)],
            [(1, "value", "50 мм", "name"), (1, "value", "100 мм", "name")],
            [(1, "value", "50 мм", "name"), (3, "none", None, None)],
            # Дубль при полном составе: множество ordinal совпадает с
            # параметрами, ловит только сравнение длины (ревью задачи 5).
            [(1, "value", "50 мм", "name"), (2, "value", "бетон", "name"), (2, "none", None, None)],
        ],
        ids=["missing", "extra", "duplicate", "foreign", "duplicate_covering_all"],
    )
    def test_ordinals_that_differ_from_the_parameters_are_refused_before_any_write(
        self, db_session, factories, specs
    ):
        world = _world(db_session, factories)
        before = _snapshot(db_session, world)
        with pytest.raises(AnswerSchemaError) as raised:
            _apply(db_session, world, _answer(*specs))
        assert raised.value.code == "bad_ordinal"
        assert _snapshot(db_session, world) == before


def _context_value_ids(db, world):
    return {
        row.parameter_id: row.value_id
        for row in db.execute(
            sa.select(ContextParameterValue).where(
                ContextParameterValue.context_id == world.context_id
            )
        ).scalars()
    }


class TestStep2Values:
    def test_value_tag_resolves_to_the_list_value_by_exact_text(self, db_session, factories):
        world = _world(db_session, factories)
        outcome = _apply(db_session, world, _answer(_named(1, "100 мм"), _named(2, "кирпич")))
        assert _context_value_ids(db_session, world) == {
            world.parameters[1]: world.values[(1, "100 мм")],
            world.parameters[2]: world.values[(2, "кирпич")],
        }
        assert outcome.values_added == ()

    def test_value_outside_the_list_is_a_schema_error_and_writes_nothing(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        before = _snapshot(db_session, world)
        with pytest.raises(AnswerSchemaError) as raised:
            _apply(db_session, world, _answer(_named(1, "75 мм"), _named(2, "бетон")))
        assert raised.value.code == "value_not_in_list"
        assert _snapshot(db_session, world) == before

    def test_value_that_exists_only_as_a_merged_synonym_is_outside_the_list(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        parameter = db_session.get(FamilyParameter, world.parameters[1])
        synonym = _value(db_session, parameter, "5 см")
        synonym.merged_into_id = world.values[(1, "50 мм")]
        db_session.flush()
        before = _snapshot(db_session, world)
        with pytest.raises(AnswerSchemaError) as raised:
            _apply(db_session, world, _answer(_named(1, "5 см"), _named(2, "бетон")))
        assert raised.value.code == "value_not_in_list"
        assert _snapshot(db_session, world) == before

    def test_new_value_is_inserted_with_extension_origin_and_an_event(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        outcome = _apply(
            db_session, world,
            _answer((1, "new", "  75 мм ", "path"), _named(2, "бетон")),
        )
        row = db_session.execute(
            sa.select(FamilyParameterValue).where(
                FamilyParameterValue.parameter_id == world.parameters[1],
                FamilyParameterValue.value_norm == "75 мм",
            )
        ).scalar_one()
        assert (row.value, row.origin, row.merged_into_id) == ("75 мм", "extension", None)
        assert outcome.values_added == (row.id,)
        events = _events(db_session, "family_schema_value_added", family_id=world.family.id)
        assert [e.payload for e in events] == [
            {
                "parameter_id": world.parameters[1], "value_id": row.id, "value": "75 мм",
                "origin": "extension", "context_id": world.context_id,
            }
        ]
        assert events[0].context_id is None and events[0].actor_id is None

    def test_new_value_that_already_exists_in_another_case_adds_nothing(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        outcome = _apply(db_session, world, _answer((1, "new", "50 ММ", "name"), _named(2, "бетон")))
        assert outcome.values_added == ()
        assert _events(db_session, "family_schema_value_added") == []
        assert _context_value_ids(db_session, world)[world.parameters[1]] == (
            world.values[(1, "50 мм")]
        )

    def test_new_value_named_as_a_merged_synonym_resolves_to_the_canonical_one(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        parameter = db_session.get(FamilyParameter, world.parameters[1])
        canonical = _value(db_session, parameter, "Тёплый пол", value_norm="теплый пол")
        synonym = _value(
            db_session, parameter, "Подогрев  пола Ёж", value_norm="подогрев пола еж"
        )
        synonym.merged_into_id = canonical.id
        db_session.flush()

        outcome = _apply(
            db_session, world, _answer((1, "new", "ПОДОГРЕВ ПОЛА ЁЖ", "name"), _named(2, "бетон"))
        )

        assert outcome.values_added == ()
        assert _context_value_ids(db_session, world)[world.parameters[1]] == canonical.id
        assert _events(db_session, "family_schema_value_added") == []

    @pytest.mark.parametrize("text", ["«»", '" "', "   "])
    def test_new_value_empty_after_normalization_is_a_schema_error_before_any_write(
        self, db_session, factories, text
    ):
        world = _world(db_session, factories)
        before = _snapshot(db_session, world)
        with pytest.raises(AnswerSchemaError) as raised:
            _apply(db_session, world, _answer((1, "new", "75 мм", "name"), (2, "new", text, "name")))
        assert raised.value.code == "empty_value"
        # Первое значение (допустимое) тоже не записано: ошибка раньше любой записи.
        assert _snapshot(db_session, world) == before

    def test_new_before_an_out_of_list_value_leaves_no_orphan_extension(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        before = _snapshot(db_session, world)
        with pytest.raises(AnswerSchemaError) as raised:
            _apply(
                db_session, world,
                _answer((1, "new", "75 мм", "name"), _named(2, "нет такого")),
            )
        assert raised.value.code == "value_not_in_list"
        # Ни строки значения, ни события о расширении: ошибка раньше любой записи.
        assert _snapshot(db_session, world) == before
        assert _events(db_session, "family_schema_value_added") == []


class TestStep4ZeroParameterPathsHash:
    def test_hash_is_the_empty_list_constant_whatever_the_argument(self, db_session, factories):
        world = _world(db_session, factories, params=())
        _apply(db_session, world, ValuesAnswer(items=()), paths_hash="some-nonempty-paths-hash")
        db_session.expire_all()
        context = db_session.get(CatalogContext, world.context_id)
        assert context.variant_paths_hash == (
            "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
        )
        assert context.variant_paths_hash != "some-nonempty-paths-hash"

    def test_schema_with_parameters_keeps_the_argument(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, world, paths_hash="real-paths")
        db_session.expire_all()
        assert db_session.get(CatalogContext, world.context_id).variant_paths_hash == "real-paths"


class TestVerdictJobSchemaMismatch:
    def test_job_built_for_another_schema_version_is_stale(self, db_session, factories):
        world = _world(db_session, factories)
        other_family = _family(db_session)
        other_schema = _frozen_schema(db_session, factories, other_family, [(1, "Х", ["а"])])
        job, guard = _guarded(db_session, context_id=world.context_id, schema_id=other_schema.id)
        before = _snapshot(db_session, world)

        outcome = _apply(db_session, world, guard=guard)

        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        assert _snapshot(db_session, world) == before
        db_session.refresh(job)
        assert (job.status, job.cancel_reason) == ("cancelled", "input_changed")


class TestStep3ContextValues:
    @pytest.mark.parametrize(
        ("kind", "value", "source", "stored_source", "has_value"),
        [
            ("value", "50 мм", "name", "name", True),
            ("value", "50 мм", "path", "path", True),
            ("new", "75 мм", "name", "name", True),
            ("new", "75 мм", "path", "path", True),
            ("conflict", None, None, "path_conflict", False),
            ("none", None, None, "none", False),
        ],
    )
    def test_source_by_tag(
        self, db_session, factories, kind, value, source, stored_source, has_value
    ):
        world = _world(db_session, factories)
        _apply(db_session, world, _answer((1, kind, value, source), (2, "none", None, None)))
        rows = {
            row.parameter_id: row
            for row in db_session.execute(
                sa.select(ContextParameterValue).where(
                    ContextParameterValue.context_id == world.context_id
                )
            ).scalars()
        }
        first, second = rows[world.parameters[1]], rows[world.parameters[2]]
        assert first.source == stored_source
        assert (first.value_id is not None) is has_value
        assert (second.source, second.value_id) == ("none", None)

    def test_set_is_replaced_whole_when_the_schema_version_changes(self, db_session, factories):
        world = _world(db_session, factories)
        old = _schema(db_session, factories, world.family, version=9, status="superseded")
        old_parameter = _param(db_session, old, 1, "Старый параметр")
        old_value = _value(db_session, old_parameter, "старое")
        db_session.add(
            ContextParameterValue(
                context_id=world.context_id, schema_id=old.id,
                parameter_id=old_parameter.id, value_id=old_value.id, source="name",
            )
        )
        db_session.flush()

        _apply(db_session, world)

        rows = db_session.execute(
            sa.select(ContextParameterValue.schema_id, ContextParameterValue.parameter_id).where(
                ContextParameterValue.context_id == world.context_id
            )
        ).all()
        assert sorted(rows) == sorted(
            (world.schema.id, world.parameters[ordinal]) for ordinal in (1, 2)
        )
        assert len(rows) == len(world.parameters)

    def test_second_application_replaces_the_first_answer(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, world, _answer(_named(1, "50 мм"), _named(2, "бетон")))
        _apply(db_session, world, _answer((1, "none", None, None), _named(2, "кирпич")))
        rows = {
            row.parameter_id: (row.value_id, row.source)
            for row in db_session.execute(
                sa.select(ContextParameterValue).where(
                    ContextParameterValue.context_id == world.context_id
                )
            ).scalars()
        }
        assert rows == {
            world.parameters[1]: (None, "none"),
            world.parameters[2]: (world.values[(2, "кирпич")], "name"),
        }

    def test_job_id_comes_from_the_guard_and_is_null_without_one(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, world)
        assert db_session.execute(
            sa.select(ContextParameterValue.job_id).where(
                ContextParameterValue.context_id == world.context_id
            )
        ).scalars().all() == [None, None]
        # Сверка после обработчика поставила задание значений по разошедшимся
        # путям; тесту нужен свободный ключ для собственного задания.
        db_session.execute(sa.delete(SemanticJob))
        job, guard = _guarded(db_session, context_id=world.context_id, schema_id=world.schema.id)
        _apply(db_session, world, guard=guard)
        assert db_session.execute(
            sa.select(ContextParameterValue.job_id).where(
                ContextParameterValue.context_id == world.context_id
            )
        ).scalars().all() == [job.id, job.id]

    def test_paths_hash_of_the_task_is_written_to_the_context(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, world, paths_hash="hash-of-these-paths")
        db_session.expire_all()
        assert db_session.get(CatalogContext, world.context_id).variant_paths_hash == (
            "hash-of-these-paths"
        )


class TestStep4Variant:
    def _second_context(self, db, factories, world):
        context_id, _ = _chain_context(
            db, factories, title=f"Вторая строка {_uid()}", path_specs=[(("Полы",), 1)]
        )
        _bind(db, factories, context_id, family=world.family)
        return context_id

    def test_same_set_in_two_contexts_gives_one_variant(self, db_session, factories):
        world = _world(db_session, factories)
        second = self._second_context(db_session, factories, world)
        first_outcome = _apply(db_session, world)
        second_outcome = _apply(db_session, world, context_id=second)
        assert first_outcome.variant_id == second_outcome.variant_id
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(WorkVariant)
        ).scalar_one() == 1

    def test_different_sets_give_different_variants(self, db_session, factories):
        world = _world(db_session, factories)
        second = self._second_context(db_session, factories, world)
        a = _apply(db_session, world, _answer(_named(1, "50 мм"), _named(2, "бетон")))
        b = _apply(
            db_session, world, _answer(_named(1, "50 мм"), _named(2, "кирпич")),
            context_id=second,
        )
        assert a.variant_id != b.variant_id

    def test_all_empty_values_make_the_not_specified_variant(self, db_session, factories):
        world = _world(db_session, factories)
        outcome = _apply(
            db_session, world, _answer((1, "none", None, None), (2, "conflict", None, None))
        )
        variant = db_session.get(WorkVariant, outcome.variant_id)
        assert variant.values_key == "1=?|2=?"
        rows = db_session.execute(
            sa.select(WorkVariantValue.value_id).where(
                WorkVariantValue.variant_id == variant.id
            )
        ).scalars().all()
        assert rows == [None, None]

    def test_zero_parameter_schema_has_one_variant_per_family(self, db_session, factories):
        world = _world(db_session, factories, params=())
        second = self._second_context(db_session, factories, world)
        empty_hash = paths_hash_of(())
        first = _apply(db_session, world, ValuesAnswer(items=()), paths_hash=empty_hash)
        again = _apply(
            db_session, world, ValuesAnswer(items=()), paths_hash=empty_hash, context_id=second
        )
        assert first.variant_id == again.variant_id
        variant = db_session.get(WorkVariant, first.variant_id)
        assert variant.values_key == ""
        db_session.expire_all()
        context = db_session.get(CatalogContext, world.context_id)
        assert context.variant_paths_hash == paths_hash_of(())

    def test_archived_unmerged_variant_with_the_same_set_is_reactivated(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        key = f"1={world.values[(1, '50 мм')]}|2={world.values[(2, 'бетон')]}"
        archived = _variant(db_session, world.family, world.schema, values_key=key, status="archived")
        outcome = _apply(db_session, world)
        db_session.refresh(archived)
        assert outcome.variant_id == archived.id
        assert (archived.status, archived.archived_at) == ("active", None)
        event = _events(db_session, "context_variant_assigned")[-1]
        assert event.payload["reactivated_variant"] is True

    def test_merged_variant_is_replaced_by_its_target(self, db_session, factories):
        world = _world(db_session, factories)
        key = f"1={world.values[(1, '50 мм')]}|2={world.values[(2, 'бетон')]}"
        target = _variant(db_session, world.family, world.schema, values_key="other-key")
        _variant(
            db_session, world.family, world.schema, values_key=key, status="archived",
            merged_into_id=target.id,
        )
        outcome = _apply(db_session, world)
        assert outcome.variant_id == target.id
        assert db_session.get(CatalogContext, world.context_id).work_variant_id == target.id


class TestStep5SwitchAndVariantColumns:
    def test_manual_pending_family_becomes_current_and_pending_is_cleared(
        self, db_session, factories
    ):
        world = _pending_world(db_session, factories)
        pending = db_session.get(CatalogContext, world.context_id)
        pending.pending_at = _now() - dt.timedelta(days=1)
        db_session.flush()
        pending_by, pending_at = pending.pending_by, pending.pending_at
        before_switch = _now()

        outcome = _apply(db_session, world, _answer(_named(1, "а")))

        db_session.expire_all()
        context = db_session.get(CatalogContext, world.context_id)
        assert outcome.family_switched is True
        assert (context.work_family_id, context.family_source, context.family_by) == (
            world.family.id, "manual", pending_by,
        )
        # family_at - момент переключения, а не момент решения (pending_at).
        assert context.family_at >= before_switch
        assert context.family_at != pending_at
        assert (
            context.pending_family_id, context.pending_family_source,
            context.pending_suggestion_id, context.pending_by, context.pending_threshold,
            context.pending_at,
        ) == (None, None, None, None, None, None)
        assert context.work_variant_id == outcome.variant_id
        assert db_session.get(WorkVariant, outcome.variant_id).family_id == world.family.id
        db_session.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        event = _events(db_session, "context_family_assigned", context_id=world.context_id)[-1]
        assert event.payload == {
            "from_family_id": world.family_a.id, "to_family_id": world.family.id,
            "source": "manual",
        }
        assert event.actor_id == pending_by

    def test_suggestion_pending_family_is_applied_with_the_suggestion_in_the_event(
        self, db_session, factories
    ):
        world = _pending_world(db_session, factories, source="suggestion")
        _apply(db_session, world, _answer(_named(1, "б")))
        db_session.expire_all()
        context = db_session.get(CatalogContext, world.context_id)
        assert (context.work_family_id, context.family_source, context.family_by) == (
            world.family.id, "suggestion", None,
        )
        event = _events(db_session, "context_family_assigned", context_id=world.context_id)[-1]
        assert event.payload == {
            "from_family_id": world.family_a.id, "to_family_id": world.family.id,
            "source": "suggestion", "suggestion_id": world.suggestion.id,
        }
        assert event.actor_id is None

    def test_auto_suggestion_pending_family_carries_threshold_and_confidence(
        self, db_session, factories
    ):
        world = _pending_world(db_session, factories, source="auto_suggestion")
        _apply(db_session, world, _answer(_named(1, "а")))
        db_session.expire_all()
        context = db_session.get(CatalogContext, world.context_id)
        assert (context.work_family_id, context.family_source, context.family_by) == (
            world.family.id, "auto_suggestion", None,
        )
        payload = _events(db_session, "context_family_assigned", context_id=world.context_id)[
            -1
        ].payload
        assert set(payload) == {
            "from_family_id", "to_family_id", "source", "suggestion_id", "threshold", "confidence",
        }
        assert payload["source"] == "auto_suggestion"
        assert payload["suggestion_id"] == world.suggestion.id
        assert isinstance(payload["threshold"], str)
        assert Decimal(payload["threshold"]) == Decimal("0.95")
        assert isinstance(payload["confidence"], str)
        assert Decimal(payload["confidence"]) == Decimal("0.9")

    def test_without_pending_the_family_stays_and_no_assignment_event_is_written(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        outcome = _apply(db_session, world)
        db_session.expire_all()
        context = db_session.get(CatalogContext, world.context_id)
        assert outcome.family_switched is False
        assert context.work_family_id == world.family.id
        assert _events(db_session, "context_family_assigned") == []

    def test_variant_columns_are_written(self, db_session, factories):
        world = _world(db_session, factories)
        before = _now()
        outcome = _apply(db_session, world, paths_hash="paths-xyz")
        db_session.expire_all()
        context = db_session.get(CatalogContext, world.context_id)
        assert context.work_variant_id == outcome.variant_id
        assert context.variant_at >= before
        assert context.variant_paths_hash == "paths-xyz"
        assert context.variant_split_hint is None

    def test_path_conflict_hint_is_set_by_any_conflict_value(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, world, _answer(_named(1, "50 мм"), (2, "conflict", None, None)))
        db_session.expire_all()
        assert db_session.get(CatalogContext, world.context_id).variant_split_hint == (
            "path_conflict"
        )

    def test_hint_is_cleared_when_no_value_conflicts_any_more(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, world, _answer((1, "conflict", None, None), _named(2, "бетон")))
        _apply(db_session, world, _answer(_named(1, "50 мм"), _named(2, "бетон")))
        db_session.expire_all()
        assert db_session.get(CatalogContext, world.context_id).variant_split_hint is None


class TestStep6Promotion:
    def test_to_review_row_becomes_position(self, db_session, factories):
        world = _world(db_session, factories, catalog_kind="TO_REVIEW")
        outcome = _apply(db_session, world)
        db_session.expire_all()
        assert outcome.promoted is True
        assert db_session.get(CatalogPosition, world.catalog_id).kind == "POSITION"
        event = _events(db_session, "context_variant_assigned")[-1]
        assert event.payload["promoted"] is True

    def test_position_row_stays_position_and_is_not_promoted(self, db_session, factories):
        world = _world(db_session, factories, catalog_kind="POSITION")
        outcome = _apply(db_session, world)
        db_session.expire_all()
        assert outcome.promoted is False
        assert db_session.get(CatalogPosition, world.catalog_id).kind == "POSITION"
        event = _events(db_session, "context_variant_assigned")[-1]
        assert event.payload["promoted"] is False

    @pytest.mark.parametrize("kind", ["HEADER", "TRASH"])
    def test_header_and_trash_rows_keep_their_kind(self, db_session, factories, kind):
        world = _world(db_session, factories, catalog_kind=kind)
        assert _apply(db_session, world).applied is False
        db_session.expire_all()
        assert db_session.get(CatalogPosition, world.catalog_id).kind == kind


class TestStep7EventAndJob:
    def test_variant_assigned_event_payload(self, db_session, factories):
        world = _world(db_session, factories)
        # Номер версии отличен от 1: иначе `schema_version` совпал бы с
        # константой и с `id` первой версии свежей базы (ревью задачи 5).
        db_session.get(FamilyParameterSchema, world.schema.id).version = 7
        db_session.flush()
        previous = _variant(db_session, world.family, world.schema, values_key="previous")
        _attach_variant(db_session, world.context_id, previous)
        outcome = _apply(
            db_session, world, _answer(_named(1, "50 мм", "name"), (2, "none", None, None))
        )
        events = _events(db_session, "context_variant_assigned", context_id=world.context_id)
        assert len(events) == 1
        assert events[0].actor_id is None and events[0].family_id is None
        assert events[0].payload == {
            "from_variant_id": previous.id,
            "to_variant_id": outcome.variant_id,
            "schema_version": 7,
            "values": {
                "1": {"value_id": world.values[(1, "50 мм")], "source": "name"},
                "2": {"value_id": None, "source": "none"},
            },
            "promoted": True,
            "reactivated_variant": False,
        }

    def test_first_assignment_has_no_previous_variant(self, db_session, factories):
        world = _world(db_session, factories)
        _apply(db_session, world)
        event = _events(db_session, "context_variant_assigned")[-1]
        assert event.payload["from_variant_id"] is None

    def test_guarded_job_is_done_with_no_token_and_no_result_suggestion(
        self, db_session, factories
    ):
        world = _world(db_session, factories)
        job, guard = _guarded(db_session, context_id=world.context_id, schema_id=world.schema.id)
        # Пути варианта равны текущим: иначе сверка в обработчике вернула бы
        # выполненное задание в очередь.
        real_paths = paths_hash_of(
            load_values_material(db_session, [world.context_id])[world.context_id].paths
        )
        assert _apply(db_session, world, guard=guard, paths_hash=real_paths).applied is True
        db_session.refresh(job)
        assert (job.status, job.claim_token, job.result_suggestion_id, job.cancel_reason) == (
            "done", None, None, None,
        )


class TestStep8PreviousVariant:
    def _with_previous(self, db, factories, *, extra_context=False):
        world = _world(db, factories)
        previous = _variant(db, world.family, world.schema, values_key="previous")
        _attach_variant(db, world.context_id, previous)
        if extra_context:
            other, _ = _chain_context(
                db, factories, title=f"Соседняя строка {_uid()}", path_specs=[((), 1)]
            )
            _bind(db, factories, other, family=world.family)
            _attach_variant(db, other, previous)
        return world, previous

    def test_emptied_previous_variant_is_archived(self, db_session, factories):
        world, previous = self._with_previous(db_session, factories)
        outcome = _apply(db_session, world)
        db_session.refresh(previous)
        assert (outcome.previous_variant_id, outcome.previous_archived) == (previous.id, True)
        assert previous.status == "archived" and previous.archived_at is not None

    def test_previous_variant_with_another_context_stays_active(self, db_session, factories):
        world, previous = self._with_previous(db_session, factories, extra_context=True)
        outcome = _apply(db_session, world)
        db_session.refresh(previous)
        assert (outcome.previous_variant_id, outcome.previous_archived) == (previous.id, False)
        assert previous.status == "active"

    def test_same_variant_again_is_not_archived(self, db_session, factories):
        world = _world(db_session, factories)
        first = _apply(db_session, world)
        again = _apply(db_session, world)
        assert again.variant_id == first.variant_id
        assert again.previous_variant_id == first.variant_id
        assert again.previous_archived is False
        assert db_session.get(WorkVariant, first.variant_id).status == "active"

    def test_previous_variant_of_the_old_family_is_archived_on_a_family_switch(
        self, db_session, factories
    ):
        world = _pending_world(db_session, factories)
        previous = _variant(db_session, world.family_a, world.schema_a, values_key="previous")
        _attach_variant(db_session, world.context_id, previous)
        outcome = _apply(db_session, world, _answer(_named(1, "а")))
        db_session.refresh(previous)
        assert outcome.family_switched is True and outcome.previous_archived is True
        assert previous.status == "archived"
        db_session.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))


# ---------------------------------------------------------------------------
#  freeze_schema
# ---------------------------------------------------------------------------

def _parameter_in(ordinal, name, *values):
    return SchemaParameterIn(ordinal=ordinal, name=name, values=tuple(values))


def _schema_answer(*parameters):
    return SchemaAnswer(parameters=tuple(parameters))


_DEFAULT_SCHEMA_ANSWER = (
    _parameter_in(1, "Толщина", "50 мм", "100 мм"),
    _parameter_in(2, "Материал", "бетон"),
)


def _freeze_world(db, factories, *, with_current=True, family_status="active"):
    """Активная семья с контекстом (чтобы в запросе были наименования), текущей
    версией 1 (с параметром и значением) и пересборкой — версией 2 `building`."""
    family = _family(db, status=family_status, definition="Стяжка пола")
    current = None
    if with_current:
        current = _schema(db, factories, family, version=1, status="frozen")
        _value(db, _param(db, current, 1, "Старый параметр"), "старое")
    building = _schema(db, factories, family, version=2 if with_current else 1, status="building")
    context_id, _ = _chain_context(
        db, factories, title=f"Стяжка {_uid()}", path_specs=[((), 1)]
    )
    _bind(db, factories, context_id, family=family)
    return SimpleNamespace(family=family, current=current, building=building, context_id=context_id)


def _schema_job(db, world, *, token=None, status="running", expected_hash=None, schema_id=None):
    schema_id = schema_id or world.building.id
    probe = SemanticJob(kind="family_schema", family_id=world.family.id, schema_id=schema_id)
    expected = (
        expected_hash
        if expected_hash is not None
        else render_request_for(db, probe, settings=_settings()).request_hash
    )
    token = token or uuid.uuid4()
    job = SemanticJob(
        kind="family_schema", family_id=world.family.id, schema_id=schema_id,
        request_hash=expected, status=status, claim_token=token if status == "running" else None,
        prompt_version="v1", model_requested="m", place_dictionary_version=1,
        candidates_hash="c", prefix_hash="p", input_hash="i", response_schema_version="v1",
        serialization_version="v1",
    )
    db.add(job)
    db.flush()
    return job, JobGuard(job_id=job.id, claim_token=token, expected_request_hash=expected)


def _freeze(db, world, parameters=_DEFAULT_SCHEMA_ANSWER, *, guard=None, schema_id=None):
    return freeze_schema(
        db, schema_id=schema_id or world.building.id, answer=_schema_answer(*parameters),
        guard=guard, settings=_settings(),
    )


def _schema_snapshot(db):
    db.expire_all()
    return {
        "schemas": _table_rows(db, FamilyParameterSchema),
        "parameters": _table_rows(db, FamilyParameter),
        "values": _table_rows(db, FamilyParameterValue),
        "events": db.execute(sa.select(sa.func.count()).select_from(SemanticEvent)).scalar_one(),
    }


class TestFreezeSchemaWrites:
    def test_building_version_is_frozen_with_parameters_and_values(self, db_session, factories):
        world = _freeze_world(db_session, factories)
        job, guard = _schema_job(db_session, world)

        outcome = _freeze(db_session, world, guard=guard)

        assert (outcome.applied, outcome.unapplied_reason, outcome.schema_id) == (
            True, None, world.building.id,
        )
        db_session.expire_all()
        schema = db_session.get(FamilyParameterSchema, world.building.id)
        assert schema.status == "frozen" and schema.frozen_at is not None
        assert (schema.origin, schema.frozen_by, schema.superseded_at) == ("model", None, None)
        parameters = db_session.execute(
            sa.select(FamilyParameter).where(FamilyParameter.schema_id == schema.id)
            .order_by(FamilyParameter.ordinal)
        ).scalars().all()
        assert [(p.ordinal, p.name, p.name_norm) for p in parameters] == [
            (1, "Толщина", "толщина"), (2, "Материал", "материал"),
        ]
        values = db_session.execute(
            sa.select(FamilyParameter.ordinal, FamilyParameterValue.value,
                      FamilyParameterValue.value_norm, FamilyParameterValue.origin,
                      FamilyParameterValue.merged_into_id)
            .join(FamilyParameterValue, FamilyParameterValue.parameter_id == FamilyParameter.id)
            .where(FamilyParameter.schema_id == schema.id)
            .order_by(FamilyParameter.ordinal, FamilyParameterValue.id)
        ).all()
        assert [tuple(v) for v in values] == [
            (1, "50 мм", "50 мм", "schema", None),
            (1, "100 мм", "100 мм", "schema", None),
            (2, "бетон", "бетон", "schema", None),
        ]
        db_session.refresh(job)
        assert (job.status, job.claim_token) == ("done", None)

    def test_previous_current_version_is_superseded_and_keeps_its_freeze_time(
        self, db_session, factories
    ):
        world = _freeze_world(db_session, factories)
        frozen_before = world.current.frozen_at
        _freeze(db_session, world)
        db_session.expire_all()
        previous = db_session.get(FamilyParameterSchema, world.current.id)
        assert previous.status == "superseded"
        assert previous.superseded_at is not None
        assert previous.frozen_at == frozen_before
        frozen = db_session.execute(
            sa.select(FamilyParameterSchema.id).where(
                FamilyParameterSchema.family_id == world.family.id,
                FamilyParameterSchema.status == "frozen",
            )
        ).scalars().all()
        assert frozen == [world.building.id]

    def test_event_describes_the_frozen_version(self, db_session, factories):
        world = _freeze_world(db_session, factories)
        job, guard = _schema_job(db_session, world)
        _freeze(db_session, world, guard=guard)
        events = _events(db_session, "family_schema_frozen", family_id=world.family.id)
        assert [e.payload for e in events] == [
            {
                "schema_id": world.building.id, "version": 2, "origin": "model",
                "parameters": [
                    {"name": "Толщина", "values": 2}, {"name": "Материал", "values": 1},
                ],
                "job_id": job.id,
            }
        ]
        assert events[0].context_id is None and events[0].actor_id is None

    def test_first_version_without_a_current_one_and_without_a_job(self, db_session, factories):
        world = _freeze_world(db_session, factories, with_current=False)
        outcome = _freeze(db_session, world, guard=None)
        assert outcome.applied is True
        event = _events(db_session, "family_schema_frozen")[-1]
        assert event.payload["job_id"] is None and event.payload["version"] == 1

    def test_zero_parameters_is_a_lawful_freeze(self, db_session, factories):
        world = _freeze_world(db_session, factories)
        job, guard = _schema_job(db_session, world)
        outcome = _freeze(db_session, world, parameters=(), guard=guard)
        assert outcome.applied is True
        db_session.expire_all()
        assert db_session.get(FamilyParameterSchema, world.building.id).status == "frozen"
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameter).where(
                FamilyParameter.schema_id == world.building.id
            )
        ).scalar_one() == 0
        assert _events(db_session, "family_schema_frozen")[-1].payload["parameters"] == []
        db_session.refresh(job)
        assert job.status == "done"

    def test_duplicate_values_by_norm_collapse_to_the_first_in_answer_order(
        self, db_session, factories
    ):
        world = _freeze_world(db_session, factories)
        _freeze(
            db_session, world,
            parameters=(_parameter_in(1, "Толщина", "100 мм", "100  ММ", "«200» мм", "200 мм"),),
        )
        values = db_session.execute(
            sa.select(FamilyParameterValue.value, FamilyParameterValue.value_norm)
            .join(FamilyParameter, FamilyParameter.id == FamilyParameterValue.parameter_id)
            .where(FamilyParameter.schema_id == world.building.id)
            .order_by(FamilyParameterValue.id)
        ).all()
        assert [tuple(v) for v in values] == [("100 мм", "100 мм"), ("«200» мм", "200 мм")]
        assert _events(db_session, "family_schema_frozen")[-1].payload["parameters"] == [
            {"name": "Толщина", "values": 2}
        ]

    def test_name_and_values_are_stored_without_edge_spaces(self, db_session, factories):
        world = _freeze_world(db_session, factories)
        # Имя с кавычками, `ё` и двойным пробелом: `name_norm` — та же
        # нормализация, что у значений, а не `strip().lower()`.
        _freeze(db_session, world, parameters=(_parameter_in(1, "  Слой  «Ёлка» ", " 50 мм  "),))
        stored = db_session.execute(
            sa.select(FamilyParameter.name, FamilyParameter.name_norm, FamilyParameterValue.value)
            .join(FamilyParameterValue, FamilyParameterValue.parameter_id == FamilyParameter.id)
            .where(FamilyParameter.schema_id == world.building.id)
        ).all()
        assert [tuple(row) for row in stored] == [("Слой  «Ёлка»", "слой елка", "50 мм")]

    @pytest.mark.parametrize(
        "parameters",
        [
            (_parameter_in(1, "Толщина", "50 мм", "«»"),),
            (_parameter_in(1, "Толщина", '" "'),),
            (_parameter_in(1, "«»", "50 мм"),),
        ],
        ids=["quotes_value_after_good_one", "quote_and_space_value", "quotes_name"],
    )
    def test_empty_after_normalization_is_a_schema_error_before_any_write(
        self, db_session, factories, parameters
    ):
        world = _freeze_world(db_session, factories)
        _job, guard = _schema_job(db_session, world)
        before = _schema_snapshot(db_session)
        with pytest.raises(AnswerSchemaError) as raised:
            _freeze(db_session, world, parameters=parameters, guard=guard)
        assert raised.value.code == "empty_value"
        assert _schema_snapshot(db_session) == before


class TestFreezeSchemaVerdict:
    def test_other_owner_job_and_schema_are_untouched(self, db_session, factories):
        world = _freeze_world(db_session, factories)
        job, guard = _schema_job(db_session, world)
        other = JobGuard(
            job_id=job.id, claim_token=uuid.uuid4(), expected_request_hash=guard.expected_request_hash
        )
        before = _schema_snapshot(db_session)
        job_before = _job_snapshot(db_session, job)

        outcome = _freeze(db_session, world, guard=other)

        assert (outcome.applied, outcome.unapplied_reason) == (False, "lost_claim")
        assert _schema_snapshot(db_session) == before
        assert _job_snapshot(db_session, job) == job_before

    def test_changed_input_cancels_the_job_with_input_changed(self, db_session, factories):
        world = _freeze_world(db_session, factories)
        job, guard = _schema_job(db_session, world)
        world.family.title = "Другое имя семьи"
        db_session.flush()
        before = _schema_snapshot(db_session)

        outcome = _freeze(db_session, world, guard=guard)

        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        assert _schema_snapshot(db_session) == before
        db_session.refresh(job)
        assert (job.status, job.cancel_reason, job.claim_token) == (
            "cancelled", "input_changed", None,
        )

    def test_wrong_expected_hash_is_stale(self, db_session, factories):
        world = _freeze_world(db_session, factories)
        _job, guard = _schema_job(db_session, world, expected_hash="0" * 64)
        before = _schema_snapshot(db_session)
        outcome = _freeze(db_session, world, guard=guard)
        assert (outcome.applied, outcome.unapplied_reason) == (False, "stale_fingerprint")
        assert _schema_snapshot(db_session) == before

    def test_repeated_freeze_of_the_same_version_is_not_applicable(self, db_session, factories):
        world = _freeze_world(db_session, factories)
        _first_job, first_guard = _schema_job(db_session, world)
        assert _freeze(db_session, world, guard=first_guard).applied is True
        # Дубль результата того же задания после потерянного захвата: новое
        # задание на ту же версию (иной токен, тот же предмет).
        second_job, second_guard = _schema_job(db_session, world, expected_hash="second-hash")
        before = _schema_snapshot(db_session)

        outcome = _freeze(db_session, world, guard=second_guard)

        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        assert _schema_snapshot(db_session) == before
        db_session.refresh(second_job)
        assert (second_job.status, second_job.cancel_reason, second_job.claim_token) == (
            "cancelled", "not_applicable", None,
        )
        frozen = db_session.execute(
            sa.select(sa.func.count()).select_from(FamilyParameterSchema).where(
                FamilyParameterSchema.family_id == world.family.id,
                FamilyParameterSchema.status == "frozen",
            )
        ).scalar_one()
        assert frozen == 1

    @pytest.mark.parametrize("status", ["superseded", "cancelled"])
    def test_version_that_is_not_building_is_not_applicable(
        self, db_session, factories, status
    ):
        world = _freeze_world(db_session, factories)
        _job, guard = _schema_job(db_session, world)
        building = db_session.get(FamilyParameterSchema, world.building.id)
        building.status = status
        building.frozen_at = _now() if status == "superseded" else None
        building.superseded_at = _now() if status == "superseded" else None
        building.cancelled_at = _now() if status == "cancelled" else None
        db_session.flush()
        before = _schema_snapshot(db_session)
        outcome = _freeze(db_session, world, guard=guard)
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        assert _schema_snapshot(db_session) == before

    def test_family_that_is_not_active_is_not_applicable(self, db_session, factories):
        world = _freeze_world(db_session, factories, family_status="draft")
        job, guard = _schema_job(db_session, world)
        before = _schema_snapshot(db_session)
        outcome = _freeze(db_session, world, guard=guard)
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")
        assert _schema_snapshot(db_session) == before
        db_session.refresh(job)
        assert (job.status, job.cancel_reason) == ("cancelled", "not_applicable")

    def test_without_a_job_the_state_of_family_and_version_decides(self, db_session, factories):
        world = _freeze_world(db_session, factories, family_status="draft")
        outcome = _freeze(db_session, world, guard=None)
        assert (outcome.applied, outcome.unapplied_reason) == (False, "not_applicable")

    def test_a_job_of_another_version_is_a_call_error(self, db_session, factories):
        world = _freeze_world(db_session, factories)
        _job, guard = _schema_job(db_session, world)
        with pytest.raises(ValueError, match="не относится"):
            _freeze(db_session, world, guard=guard, schema_id=world.current.id)

    def test_missing_version_is_a_call_error(self, db_session, factories):
        world = _freeze_world(db_session, factories)
        with pytest.raises(ValueError, match="не найдена"):
            _freeze(db_session, world, schema_id=987654321)


class TestFreezeSchemaLockOrder:
    def test_family_then_building_then_current_then_job_all_for_update(
        self, db_session, factories
    ):
        world = _freeze_world(db_session, factories)
        _job, guard = _schema_job(db_session, world)
        with _capturing_sql(db_session) as statements:
            assert _freeze(db_session, world, guard=guard).applied is True
        assert _lock_sequence(statements)[:4] == [
            ("work_families", "UPDATE"),
            ("family_parameter_schemas", "UPDATE"),
            ("family_parameter_schemas", "UPDATE"),
            ("semantic_jobs", "UPDATE"),
        ]


# ---------------------------------------------------------------------------
#  Инвариант кэша: промоушен до повторного импорта
# ---------------------------------------------------------------------------

class TestPromotionKeepsTheCacheInvariant:
    def test_reimport_after_promotion_writes_the_cache_on_the_position_row(
        self, committing_db, committing_factories, committing_session_factory, tmp_storage,
        caplog,
    ):
        import logging

        from models import ImportJob, ImportJobStatus, MatchingCache
        from services import import_pipeline
        from tests.integration.test_import_pipeline import fake_parse
        from tests.payloads import payload_for, position

        db = committing_db
        factories = committing_factories

        def _import(contract):
            payload = payload_for(
                contract,
                [position(job_title="Устройство стяжки", unit="м2", unit_cost_total="100", number="2")],
            )
            job = factories.ImportJobFactory.create(
                contract=contract, amendment_no=None,
                file_key=tmp_storage.save(b"PK\x03\x04not-a-real-xlsx"),
                status=ImportJobStatus.pending.value,
            )
            db.commit()
            import_pipeline.run_import_job(
                job.id, session_factory=committing_session_factory, storage=tmp_storage,
                parse=fake_parse(payload),
            )
            db.expire_all()
            return db.get(ImportJob, job.id)

        first = _import(factories.ContractFactory.create())
        assert (first.status, first.to_review, first.matched_exact) == ("done", 1, 0)
        row = db.execute(sa.select(CatalogPosition)).scalar_one()
        assert row.kind == "TO_REVIEW"
        assert db.execute(sa.select(sa.func.count()).select_from(MatchingCache)).scalar_one() == 0
        context_id = db.execute(
            sa.select(CatalogContext.id)
            .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
            .where(ContextBucket.catalog_position_id == row.id)
        ).scalar_one()

        family = _family(db, status="active", definition="Стяжка пола")
        schema = _schema(db, factories, family, version=1, status="frozen")
        _bind(db, factories, context_id, family=family)
        db.commit()
        outcome = apply_values(
            db, context_id=context_id, schema_id=schema.id, answer=ValuesAnswer(items=()),
            paths_hash=paths_hash_of(()), guard=None, settings=_settings(),
        )
        db.commit()
        assert (outcome.applied, outcome.promoted) == (True, True)
        db.expire_all()
        assert db.get(CatalogPosition, row.id).kind == "POSITION"

        with caplog.at_level(logging.ERROR):
            second = _import(factories.ContractFactory.create())
            third = _import(factories.ContractFactory.create())

        assert (second.status, second.to_review, second.matched_exact) == ("done", 0, 1)
        assert (third.status, third.to_review, third.matched_cache) == ("done", 0, 1)
        cache = db.execute(sa.select(MatchingCache.catalog_position_id)).scalars().all()
        assert cache == [row.id]
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []
