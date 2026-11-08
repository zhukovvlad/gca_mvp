"""Признак «можно отпустить»: тело запроса `releasable_params`, разбор ответа, замки,
тариф и поле правки схемы (лестница аналогов, спека §2.6, §2.7). Без базы данных.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql

from config import Settings
from models import ParameterRole, ParameterValueKind, SemanticJobKind
from services import releasable
from services.releasable import (
    MAX_VALUES,
    ReleasableMaterial,
    ReleasableParameterIn,
    check_coverage,
    parse_releasable_answer,
    render_releasable_material,
)
from services.schema_edit import ParameterDraft, _body_canon, _checked_drafts
from services.semantic_answer import AnswerSchemaError
from services.semantic_cost import tariffs_from


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, SECRET_KEY="x" * 32, **overrides)


S = _settings()


def _material(**overrides) -> ReleasableMaterial:
    fields = dict(
        family_id=1, schema_id=10, title="Стяжка пола", unit_code="м2",
        definition="Устройство цементной стяжки",
        parameters=(
            ReleasableParameterIn(11, "Толщина", ("50 мм", "80 мм"), None),
            ReleasableParameterIn(12, "Армирование", ("сетка", "фибра"), None),
        ),
    )
    fields.update(overrides)
    return ReleasableMaterial(**fields)


def _hash(material: ReleasableMaterial, settings: Settings = S) -> str:
    return render_releasable_material(material, settings=settings).request_hash


def _answer_json(*items) -> str:
    return json.dumps(
        {
            "parameters": [
                {"parameter_id": pid, "releasable": rel, "confidence": conf, "reason": "причина"}
                for pid, rel, conf in items
            ]
        },
        ensure_ascii=False,
    )


def _draft(**overrides) -> ParameterDraft:
    fields = dict(
        ordinal=1, name="Толщина", role=ParameterRole.main,
        value_kind=ParameterValueKind.exact, closed=False, values=("50 мм",), range=None,
    )
    fields.update(overrides)
    return ParameterDraft(**fields)


class TestRenderedBody:
    def test_profile_is_the_schema_model_with_low_reasoning(self):
        custom = _settings(
            SEMANTIC_SCHEMA_MODEL="vendor/model-x", SEMANTIC_SCHEMA_MAX_TOKENS=777,
            SEMANTIC_VARIANTS_REASONING_EFFORT="low",
        )

        body = render_releasable_material(_material(), settings=custom).body

        assert body["model"] == "vendor/model-x"
        assert body["max_tokens"] == 777
        assert body["reasoning"] == {"effort": "low"}
        assert body["temperature"] == 0

    def test_user_text_names_family_unit_definition_ids_names_and_values(self):
        text = render_releasable_material(_material(), settings=S).body["messages"][1]["content"]

        for needle in ("Стяжка пола", "м2", "Устройство цементной стяжки", "11", "Толщина",
                       "50 мм", "80 мм", "12", "Армирование", "сетка", "фибра"):
            assert needle in text

    def test_hash_is_deterministic(self):
        assert _hash(_material()) == _hash(_material())

    @pytest.mark.parametrize(
        "changed",
        [
            dict(title="Стяжка пола, армированная"),
            dict(definition="Другое определение"),
            dict(unit_code="м3"),
            dict(unit_code=None),
            dict(parameters=(ReleasableParameterIn(11, "Толщина слоя", ("50 мм", "80 мм"), None),
                             ReleasableParameterIn(12, "Армирование", ("сетка", "фибра"), None))),
            dict(parameters=(ReleasableParameterIn(11, "Толщина", ("50 мм", "90 мм"), None),
                             ReleasableParameterIn(12, "Армирование", ("сетка", "фибра"), None))),
            dict(parameters=(ReleasableParameterIn(11, "Толщина", ("50 мм", "80 мм"), None),)),
            dict(parameters=(ReleasableParameterIn(21, "Толщина", ("50 мм", "80 мм"), None),
                             ReleasableParameterIn(12, "Армирование", ("сетка", "фибра"), None))),
        ],
        ids=["title", "definition", "unit", "no_unit", "param_name", "param_value",
             "param_removed", "param_id"],
    )
    def test_every_request_input_changes_the_hash(self, changed):
        assert _hash(_material(**changed)) != _hash(_material())

    def test_the_releasable_flag_is_not_part_of_the_body(self):
        marked = _material(
            parameters=(
                ReleasableParameterIn(11, "Толщина", ("50 мм", "80 мм"), True),
                ReleasableParameterIn(12, "Армирование", ("сетка", "фибра"), False),
            )
        )

        assert _hash(marked) == _hash(_material())

    def test_at_most_thirty_values_per_parameter_go_out(self):
        many = tuple(f"значение {i:02d}" for i in range(40))
        material = _material(parameters=(ReleasableParameterIn(11, "Тип", many, None),))

        text = render_releasable_material(material, settings=S).body["messages"][1]["content"]

        assert MAX_VALUES == 30
        assert "значение 29" in text and "значение 30" not in text

    def test_no_money_or_volume_words_in_the_body(self):
        dumped = json.dumps(
            render_releasable_material(_material(), settings=S).body, ensure_ascii=False
        ).lower()

        assert "$" not in dumped and "руб" not in dumped

    def test_candidates_hash_follows_the_parameter_ids_only(self):
        a = render_releasable_material(_material(), settings=S)
        renamed = render_releasable_material(
            _material(title="Другая семья"), settings=S
        )

        assert a.candidates_hash == renamed.candidates_hash
        assert a.request_hash != renamed.request_hash


class TestParseAnswer:
    def test_a_valid_answer_is_parsed(self):
        answer = parse_releasable_answer(_answer_json((11, True, 0.97), (12, False, 0.5)))

        assert [(i.parameter_id, i.releasable, i.confidence) for i in answer.parameters] == [
            (11, True, Decimal("0.97")), (12, False, Decimal("0.5")),
        ]
        assert answer.parameters[0].reason == "причина"

    def test_a_json_fence_is_accepted(self):
        raw = "```json\n" + _answer_json((11, True, 1)) + "\n```"
        assert parse_releasable_answer(raw).parameters[0].parameter_id == 11

    @pytest.mark.parametrize(
        ("raw", "code"),
        [
            ("не json", "not_json"),
            ("[]", "bad_type"),
            ("{}", "missing_field"),
            ('{"parameters": {}}', "bad_type"),
            ('{"parameters": [1]}', "bad_type"),
            ('{"parameters": [{"releasable": true, "confidence": 1, "reason": "р"}]}',
             "missing_field"),
            ('{"parameters": [{"parameter_id": "11", "releasable": true, "confidence": 1,'
             ' "reason": "р"}]}', "bad_type"),
            ('{"parameters": [{"parameter_id": true, "releasable": true, "confidence": 1,'
             ' "reason": "р"}]}', "bad_type"),
            ('{"parameters": [{"parameter_id": 11, "releasable": "да", "confidence": 1,'
             ' "reason": "р"}]}', "bad_type"),
            ('{"parameters": [{"parameter_id": 11, "releasable": null, "confidence": 1,'
             ' "reason": "р"}]}', "bad_type"),
            ('{"parameters": [{"parameter_id": 11, "releasable": true, "confidence": 1.2,'
             ' "reason": "р"}]}', "bad_confidence"),
            ('{"parameters": [{"parameter_id": 11, "releasable": true, "confidence": "1",'
             ' "reason": "р"}]}', "bad_confidence"),
            ('{"parameters": [{"parameter_id": 11, "releasable": true, "confidence": 1,'
             ' "reason": "  "}]}', "empty_reason"),
        ],
    )
    def test_malformed_answers_are_attempt_errors(self, raw, code):
        with pytest.raises(AnswerSchemaError) as caught:
            parse_releasable_answer(raw)
        assert caught.value.code == code

    def test_a_reason_over_three_hundred_characters_is_refused(self):
        raw = _answer_json((11, True, 1)).replace("причина", "я" * 301)
        with pytest.raises(AnswerSchemaError):
            parse_releasable_answer(raw)


class TestCoverage:
    def _answer(self, *ids):
        return parse_releasable_answer(_answer_json(*[(i, True, 1) for i in ids]))

    def test_every_asked_parameter_exactly_once_passes(self):
        check_coverage(self._answer(12, 11), [11, 12])

    @pytest.mark.parametrize(
        "ids", [(11,), (11, 12, 12), (11, 11, 12), (11, 12, 13), (), (12, 13)],
        ids=["skipped", "repeated_second", "repeated_first", "foreign", "empty", "swapped"],
    )
    def test_a_skip_a_repeat_or_a_foreign_id_is_an_attempt_error(self, ids):
        with pytest.raises(AnswerSchemaError):
            check_coverage(self._answer(*ids), [11, 12])


class TestLockStatements:
    @staticmethod
    def _sql(statement) -> str:
        return str(statement.compile(dialect=postgresql.dialect()))

    def test_family_and_parameters_are_for_share(self):
        for statement in (
            releasable._lock_family_statement(1),
            releasable._lock_parameters_statement(1),
        ):
            text = self._sql(statement)
            assert re.search(r"FOR SHARE\s*$", text) and "FOR UPDATE" not in text, text

    def test_the_schema_lock_is_for_share(self):
        text = self._sql(releasable._lock_schema_statement(1))
        assert re.search(r"FOR SHARE\s*$", text) and "FOR UPDATE" not in text

    def test_parameters_are_locked_in_id_order_and_only_active_main_ones(self):
        text = self._sql(releasable._lock_parameters_statement(1))
        assert "ORDER BY family_parameters.id" in text
        assert "archived_at IS NULL" in text and "role" in text


class TestTariffsAndSettings:
    def test_releasable_params_uses_the_schema_tariffs(self):
        custom = _settings(
            SEMANTIC_SCHEMA_PRICE_INPUT_PER_M=Decimal("21"),
            SEMANTIC_SCHEMA_PRICE_CACHE_WRITE_PER_M=Decimal("22"),
            SEMANTIC_SCHEMA_PRICE_CACHE_READ_PER_M=Decimal("23"),
            SEMANTIC_SCHEMA_PRICE_OUTPUT_PER_M=Decimal("24"),
            SEMANTIC_PRICE_INPUT_PER_M=Decimal("1"),
        )

        tariffs = tariffs_from(custom, SemanticJobKind.releasable_params)

        assert (tariffs.input_per_m, tariffs.cache_write_per_m, tariffs.cache_read_per_m,
                tariffs.output_per_m) == (Decimal(21), Decimal(22), Decimal(23), Decimal(24))

    def test_an_unknown_kind_still_has_no_silent_tariff(self):
        with pytest.raises(ValueError):
            tariffs_from(S, "no_such_kind")  # type: ignore[arg-type]

    def test_the_auto_accept_threshold_defaults_to_095(self):
        assert Decimal("0.95") == S.SEMANTIC_RELEASABLE_AUTO_ACCEPT_THRESHOLD

    @pytest.mark.parametrize("bad", ["0", "1.5", "-1"])
    def test_the_threshold_must_be_in_the_open_closed_unit_interval(self, bad):
        with pytest.raises(ValueError):
            _settings(SEMANTIC_RELEASABLE_AUTO_ACCEPT_THRESHOLD=Decimal(bad))


class TestSchemaEditDraft:
    def test_the_draft_defaults_to_an_undecided_flag(self):
        assert _draft().releasable is None

    def test_the_flag_is_part_of_the_canonical_body(self):
        marked = _body_canon([_draft(releasable=True)])
        unmarked = _body_canon([_draft()])
        other = _body_canon([_draft(releasable=False)])

        assert marked != unmarked and marked != other and other != unmarked

    def test_a_secondary_parameter_loses_the_flag(self):
        (checked,) = _checked_drafts([_draft(role=ParameterRole.secondary, releasable=True)])

        assert checked.releasable is None

    def test_a_main_parameter_keeps_the_flag(self):
        (checked,) = _checked_drafts([_draft(releasable=False)])

        assert checked.releasable is False

    def test_replace_keeps_other_fields(self):
        assert replace(_draft(releasable=True), name="X").releasable is True
