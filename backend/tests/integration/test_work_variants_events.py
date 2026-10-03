"""Шесть типов журнала миграции 0019 в реестре `services/semantic_events.py`
(спека `2026-10-02-catalog-variants-design.md` §2.13): состав обязательных
ключей — независимым литералом, предмет по типу, условный порог ожидания.

Перебор «полный payload принят / без каждого ключа отказ / чужое значение
перечислимого ключа отказ» для этих же типов делают общие параметризованные
тесты `test_semantic_events.py` (их литералы расширены тем же набором).
"""
from __future__ import annotations

import pytest

from services.semantic_events import (
    CONTEXT_EVENT_TYPES,
    EVENT_ENUM_VALUES,
    EVENT_REQUIRED_KEYS,
    FAMILY_EVENT_TYPES,
    SemanticEventError,
    record_event,
)
from tests.integration.test_work_variants_schema import _context, _family

pytestmark = pytest.mark.integration

_CONTEXT_TYPES = {
    "context_variant_assigned": {
        "from_variant_id", "to_variant_id", "schema_version", "values", "promoted",
        "reactivated_variant",
    },
    "context_family_pending": {"pending_family_id", "source", "suggestion_id", "outcome"},
    "context_not_work": {"reason", "cleared_family_id", "cleared_variant_id"},
}
_FAMILY_TYPES = {
    "family_schema_frozen": {"schema_id", "version", "origin", "parameters", "job_id"},
    "family_schema_value_added": {"parameter_id", "value_id", "value", "origin", "context_id"},
    "family_variants_merged": {
        "parameter_id", "source_value_id", "target_value_id", "merged_variants",
    },
}


class TestRegistryOfSixNewTypes:
    @pytest.mark.parametrize(("event_type", "keys"), sorted(_CONTEXT_TYPES.items()))
    def test_context_type_is_registered_with_exact_keys(self, event_type, keys):
        assert event_type in CONTEXT_EVENT_TYPES
        assert event_type not in FAMILY_EVENT_TYPES
        assert EVENT_REQUIRED_KEYS[event_type] == frozenset(keys)

    @pytest.mark.parametrize(("event_type", "keys"), sorted(_FAMILY_TYPES.items()))
    def test_family_type_is_registered_with_exact_keys(self, event_type, keys):
        assert event_type in FAMILY_EVENT_TYPES
        assert event_type not in CONTEXT_EVENT_TYPES
        assert EVENT_REQUIRED_KEYS[event_type] == frozenset(keys)

    def test_closed_enum_values_of_the_spec(self):
        assert EVENT_ENUM_VALUES[("context_family_pending", "outcome")] == {
            "set", "superseded", "cancelled", "applied", "redirected",
        }
        assert EVENT_ENUM_VALUES[("context_family_pending", "source")] == {
            "manual", "suggestion", "auto_suggestion",
        }
        assert EVENT_ENUM_VALUES[("context_not_work", "reason")] == {"manual", "position_kind"}
        assert EVENT_ENUM_VALUES[("family_schema_frozen", "origin")] == {"model", "manual"}
        assert EVENT_ENUM_VALUES[("family_schema_value_added", "origin")] == {
            "schema", "extension", "manual",
        }

    def test_subject_by_type_is_enforced_before_the_database(self, db_session, factories):
        context = _context(db_session, factories)
        family = _family(db_session)
        payload = {"reason": "manual", "cleared_family_id": None, "cleared_variant_id": None}
        # Ревью задачи 1: отказ сверяется по тексту — иначе его дал бы любой
        # другой дефект payload, а не предмет.
        with pytest.raises(SemanticEventError, match="требуется context_id"):
            record_event(db_session, event_type="context_not_work", payload=payload,
                         family_id=family.id)
        with pytest.raises(SemanticEventError, match="требуется family_id"):
            record_event(
                db_session, event_type="family_schema_frozen",
                payload={"schema_id": 1, "version": 1, "origin": "model", "parameters": [],
                         "job_id": None},
                context_id=context.id,
            )


class TestPendingThreshold:
    """`threshold` обязателен при источнике `auto_suggestion` и запрещён при
    прочих — условно, как `suggestion_id` у `context_family_assigned`."""

    def _payload(self, source, **extra):
        payload = {
            "pending_family_id": 1, "source": source,
            "suggestion_id": None if source == "manual" else 5, "outcome": "set",
        }
        payload.update(extra)
        return payload

    def _record(self, db_session, factories, payload):
        context = _context(db_session, factories)
        return record_event(
            db_session, event_type="context_family_pending", payload=payload,
            context_id=context.id,
        )

    def test_auto_suggestion_with_threshold_accepted(self, db_session, factories):
        event = self._record(
            db_session, factories, self._payload("auto_suggestion", threshold="0.95")
        )
        assert event.id is not None

    def test_auto_suggestion_without_threshold_rejected(self, db_session, factories):
        with pytest.raises(SemanticEventError, match="threshold"):
            self._record(db_session, factories, self._payload("auto_suggestion"))

    @pytest.mark.parametrize("source", ["manual", "suggestion"])
    def test_other_sources_without_threshold_accepted(self, db_session, factories, source):
        assert self._record(db_session, factories, self._payload(source)).id is not None

    @pytest.mark.parametrize("source", ["manual", "suggestion"])
    def test_other_sources_with_threshold_rejected(self, db_session, factories, source):
        with pytest.raises(SemanticEventError, match="threshold"):
            self._record(db_session, factories, self._payload(source, threshold="0.95"))

