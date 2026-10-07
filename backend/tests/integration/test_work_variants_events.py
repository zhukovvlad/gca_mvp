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


class TestFamilyAssignedAutoSuggestionContract:
    """`context_family_assigned` при `auto_suggestion`: предложение, порог и
    уверенность обязательны; при `manual` и `suggestion` порог и уверенность
    запрещены. Порог и уверенность — строки десятичного числа в `(0, 1]`."""

    def _payload(self, source, **extra):
        payload = {"from_family_id": None, "to_family_id": 1, "source": source}
        payload.update(extra)
        return payload

    def _auto(self, **overrides):
        payload = self._payload(
            "auto_suggestion", suggestion_id=5, threshold="0.95", confidence="0.97"
        )
        payload.update(overrides)
        return payload

    def _record(self, db_session, factories, payload):
        context = _context(db_session, factories)
        return record_event(
            db_session, event_type="context_family_assigned", payload=payload,
            context_id=context.id,
        )

    def test_full_auto_suggestion_payload_accepted(self, db_session, factories):
        assert self._record(db_session, factories, self._auto()).id is not None

    @pytest.mark.parametrize("missing", ["suggestion_id", "threshold", "confidence"])
    def test_auto_suggestion_without_each_required_key_rejected(
        self, db_session, factories, missing
    ):
        payload = self._auto()
        del payload[missing]
        with pytest.raises(SemanticEventError, match=missing):
            self._record(db_session, factories, payload)

    @pytest.mark.parametrize("key", ["threshold", "confidence"])
    @pytest.mark.parametrize("bad", ["abc", "0", "-0.1", "1.5", "NaN", 0.95, None, True])
    def test_auto_suggestion_decimal_outside_unit_interval_rejected(
        self, db_session, factories, key, bad
    ):
        with pytest.raises(SemanticEventError, match=key):
            self._record(db_session, factories, self._auto(**{key: bad}))

    @pytest.mark.parametrize("key", ["threshold", "confidence"])
    def test_auto_suggestion_upper_bound_one_accepted(self, db_session, factories, key):
        assert self._record(db_session, factories, self._auto(**{key: "1"})).id is not None

    @pytest.mark.parametrize("bad", [True, "5", None])
    def test_auto_suggestion_suggestion_id_must_be_an_integer(
        self, db_session, factories, bad
    ):
        with pytest.raises(SemanticEventError, match="suggestion_id"):
            self._record(db_session, factories, self._auto(suggestion_id=bad))

    @pytest.mark.parametrize("source", ["manual", "suggestion"])
    @pytest.mark.parametrize("key", ["threshold", "confidence"])
    def test_other_sources_forbid_threshold_and_confidence(
        self, db_session, factories, source, key
    ):
        extra = {"suggestion_id": 5} if source == "suggestion" else {}
        extra[key] = "0.95"
        with pytest.raises(SemanticEventError, match=key):
            self._record(db_session, factories, self._payload(source, **extra))

    def test_suggestion_and_manual_without_them_still_accepted(self, db_session, factories):
        assert self._record(
            db_session, factories, self._payload("suggestion", suggestion_id=5)
        ).id is not None
        assert self._record(db_session, factories, self._payload("manual")).id is not None
