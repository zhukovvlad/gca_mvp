"""Тесты строгого разбора ответа модели (задача 3 фичи «Семантические
предложения»).

План: `docs/superpowers/plans/2026-09-28-semantic-suggestions.md`, задача 3.
Спека: `docs/superpowers/specs/2026-09-28-semantic-suggestions-design.md`
§2.2, §2.6.

`parse_model_answer` — чистая функция, в базу не ходит. Каждый код ошибки —
отдельный вход, отличающийся от валидного соседа РОВНО одним нарушенным
свойством (`docs/insights/claimed-property-needs-its-own-input.md`).
"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from services.semantic_answer import (
    RESPONSE_SCHEMA_VERSION,
    AnswerSchemaError,
    parse_model_answer,
)
from services.semantic_request import CandidateFamily

# ---------------------------------------------------------------------------
#  Заготовки — валидные payload'ы, меняем ровно по одному свойству за раз
# ---------------------------------------------------------------------------

def _candidates() -> tuple[CandidateFamily, ...]:
    return (
        CandidateFamily(id=3, title="Устройство пола", unit_code="M2", definition="Пол по грунту"),
        CandidateFamily(id=5, title="Кладка стен", unit_code="M3", definition="Кирпичная кладка"),
    )


def _matched_payload(**overrides) -> dict:
    """Валидный ответ, совпадающий с существующей семьёй (id=3)."""
    base = {
        "family_id": 3,
        "new_family_name": None,
        "confidence": 0.8,
        "reason": "Похоже по наименованию и единице",
    }
    base.update(overrides)
    return base


def _new_payload(**overrides) -> dict:
    """Валидный ответ, предлагающий новую семью (family_id=0)."""
    base = {
        "family_id": 0,
        "new_family_name": "Устройство лестниц",
        "confidence": 0.6,
        "reason": "Ни одна семья списка не описывает этот тип работы",
    }
    base.update(overrides)
    return base


def _raw(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _fenced(text: str, label: str = "json") -> str:
    return f"```{label}\n{text}\n```"


def _error_code(exc_info) -> str:
    return exc_info.value.code


# ---------------------------------------------------------------------------
#  Обрамление (спека §2.2)
# ---------------------------------------------------------------------------

class TestFraming:
    def test_plain_json_and_fenced_json_parse_the_same(self):
        raw = _raw(_matched_payload())
        plain = parse_model_answer(raw, _candidates())
        fenced = parse_model_answer(_fenced(raw), _candidates())
        assert plain == fenced

    def test_fence_without_label_is_accepted(self):
        raw = _raw(_matched_payload())
        result = parse_model_answer(_fenced(raw, label=""), _candidates())
        assert result.family_id == 3

    def test_fence_label_case_insensitive(self):
        raw = _raw(_matched_payload())
        result = parse_model_answer(_fenced(raw, label="JSON"), _candidates())
        assert result.family_id == 3

    def test_fence_with_other_label_is_extra_text(self):
        raw = _fenced(_raw(_matched_payload()), label="python")
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) == "extra_text"

    def test_text_after_object_is_extra_text(self):
        raw = _raw(_matched_payload()) + " спасибо за внимание"
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) == "extra_text"

    def test_two_objects_back_to_back_is_extra_text(self):
        payload = _raw(_matched_payload())
        raw = payload + payload
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) == "extra_text"

    def test_text_before_object_is_not_extracted(self):
        """Регулярный поиск объекта в прозе ЗАПРЕЩЁН — вход, который его бы
        разобрал (`{...}` где-то в середине текста), не должен дать валидный
        результат. Код здесь — `not_json` (текст перед объектом не даёт
        `raw_decode` разобрать с позиции 0); альтернативно план допускает
        `extra_text` для этой формы — важно, что объект НЕ извлечён."""
        raw = "Вот мой ответ: " + _raw(_matched_payload())
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) in {"not_json", "extra_text"}

    def test_json_embedded_in_prose_is_not_extracted(self):
        raw = "Структура такая " + _raw(_matched_payload()) + " как и просили"
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) in {"not_json", "extra_text"}

    @pytest.mark.parametrize(
        "wrap",
        [
            pytest.param(lambda fenced: "Вот ответ:\n" + fenced, id="prose_before_fence"),
            pytest.param(lambda fenced: fenced + "\nНадеюсь, помог.", id="text_after_fence"),
        ],
    )
    def test_text_around_fence_is_not_extracted(self, wrap):
        """Ограда допустима только как ВЕСЬ ответ: блок `json` посреди прозы —
        та же форма «объект внутри текста», что и без ограды, и не извлекается."""
        raw = wrap(_fenced(_raw(_matched_payload())))
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) in {"not_json", "extra_text"}

    def test_whitespace_around_answer_is_allowed(self):
        """Ответ модели обычно кончается переводом строки: пробельные символы
        вокруг объекта и вокруг ограды — не «текст вокруг»."""
        raw = _raw(_matched_payload())
        expected = parse_model_answer(raw, _candidates())
        assert parse_model_answer("\n  " + raw + " \n", _candidates()) == expected
        assert parse_model_answer("\n" + _fenced(raw) + "\n\n", _candidates()) == expected

    def test_pretty_printed_json_inside_fence(self):
        """Многострочный JSON в ограде — обычная форма ответа модели."""
        payload = _matched_payload()
        pretty = json.dumps(payload, ensure_ascii=False, indent=2)
        assert "\n" in pretty
        assert parse_model_answer(_fenced(pretty), _candidates()) == parse_model_answer(
            _raw(payload), _candidates()
        )

    def test_blank_lines_inside_fence_are_allowed(self):
        raw = _raw(_matched_payload())
        assert parse_model_answer(_fenced("\n" + raw + "\n"), _candidates()) == parse_model_answer(
            raw, _candidates()
        )


# ---------------------------------------------------------------------------
#  not_json / bad_type (верхний уровень)
# ---------------------------------------------------------------------------

class TestNotJsonAndTopLevelType:
    def test_garbage_text_is_not_json(self):
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer("это не json вообще", _candidates())
        assert _error_code(exc_info) == "not_json"

    def test_not_json_detail_does_not_leak_full_response_text(self):
        marker = "XYZ_УНИКАЛЬНЫЙ_МАРКЕР_ОТВЕТА_ЦЕЛИКОМ"
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(marker, _candidates())
        assert marker not in exc_info.value.detail

    def test_json_array_is_bad_type(self):
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer("[1, 2, 3]", _candidates())
        assert _error_code(exc_info) == "bad_type"

    def test_json_number_is_bad_type(self):
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer("42", _candidates())
        assert _error_code(exc_info) == "bad_type"

    def test_duplicate_key_is_not_json(self):
        """Повторяющийся ключ — модуль `json` по умолчанию молча берёт
        последнее значение; для строгой валидации это двусмысленный ответ,
        а не совпадение, поэтому это ошибка разбора, а не выбор `3`."""
        raw = (
            '{"family_id": 99, "family_id": 3, "new_family_name": null, '
            '"confidence": 0.5, "reason": "x"}'
        )
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) == "not_json"
        assert "family_id" in exc_info.value.detail


# ---------------------------------------------------------------------------
#  missing_field — каждое поле по отдельности, лишние поля игнорируются
# ---------------------------------------------------------------------------

class TestMissingAndExtraFields:
    def test_missing_family_id(self):
        payload = _matched_payload()
        del payload["family_id"]
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "missing_field"

    def test_missing_new_family_name(self):
        payload = _matched_payload()
        del payload["new_family_name"]
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "missing_field"

    def test_missing_confidence(self):
        payload = _matched_payload()
        del payload["confidence"]
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "missing_field"

    def test_missing_reason(self):
        payload = _matched_payload()
        del payload["reason"]
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "missing_field"

    def test_extra_key_is_ignored(self):
        payload = _matched_payload(note="модель добавила своё поле")
        result = parse_model_answer(_raw(payload), _candidates())
        assert result.family_id == 3

    def test_missing_field_wins_over_bad_family_id(self):
        """Порядок проверок: «обязательные поля»
        раньше `family_id` — отсутствующее поле должно давать `missing_field`,
        даже если ОСТАВШИЙСЯ `family_id` тоже невалиден."""
        payload = _matched_payload(family_id=-1)
        del payload["confidence"]
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "missing_field"


class TestCheckOrder:
    """Порядок проверок: `family_id` → `confidence` →
    имя → `reason`. Каждый вход нарушает РОВНО два соседних шага; побеждает
    ранний."""

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            pytest.param(
                _matched_payload(family_id=99, confidence=1.5), "unknown_family",
                id="family_id_before_confidence",
            ),
            pytest.param(
                _new_payload(new_family_name="   ", confidence=1.5), "bad_confidence",
                id="confidence_before_name",
            ),
            pytest.param(
                _new_payload(new_family_name="   ", reason="   "), "empty_name",
                id="name_before_reason",
            ),
        ],
    )
    def test_check_order_between_neighbours(self, payload, expected):
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == expected


# ---------------------------------------------------------------------------
#  family_id (спека §2.2)
# ---------------------------------------------------------------------------

class TestFamilyId:
    def test_zero_means_new_family(self):
        result = parse_model_answer(_raw(_new_payload()), _candidates())
        assert result.family_id is None
        assert result.is_system is False

    def test_positive_id_in_candidates_matches_and_ignores_name(self):
        result = parse_model_answer(_raw(_matched_payload()), _candidates())
        assert result.family_id == 3
        assert result.new_family_name is None

    def test_id_not_in_candidates_is_unknown_family(self):
        payload = _matched_payload(family_id=99)
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "unknown_family"

    def test_negative_id_is_unknown_family(self):
        payload = _matched_payload(family_id=-1)
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "unknown_family"

    def test_negative_id_is_unknown_family_even_if_in_snapshot(self):
        """`family_id` — целое `>= 0`, отрицательное —
        `unknown_family` само по себе, а не только потому, что его нет в
        снимке. Снимок здесь содержит `-1`, поэтому членство в снимке
        отрицательный номер пропустило бы — вход отделяет запрет знака от
        проверки членства."""
        candidates = _candidates() + (
            CandidateFamily(id=-1, title="Отрицательный", unit_code="M2", definition="x"),
        )
        payload = _matched_payload(family_id=-1)
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), candidates)
        assert _error_code(exc_info) == "unknown_family"

    def test_bool_family_id_is_bad_type_not_unknown_family(self):
        """`True == 1` в Python, а `1` не входит в снимок кандидатов (id=3,
        5) — без явной проверки `bool` до `isinstance(int)` это дало бы
        `unknown_family` вместо `bad_type`."""
        raw = '{"family_id": true, "new_family_name": null, "confidence": 0.5, "reason": "x"}'
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) == "bad_type"

    def test_float_family_id_is_bad_type(self):
        payload = _matched_payload(family_id=3.0)
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "bad_type"

    def test_string_family_id_is_bad_type(self):
        payload = _matched_payload(family_id="3")
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "bad_type"


# ---------------------------------------------------------------------------
#  confidence (спека §2.2)
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_int_confidence_becomes_decimal(self):
        payload = _matched_payload(confidence=1)
        result = parse_model_answer(_raw(payload), _candidates())
        assert result.confidence == Decimal("1")
        assert isinstance(result.confidence, Decimal)

    def test_decimal_confidence_matches_exact_literal(self):
        payload = _matched_payload(confidence=0.8)
        result = parse_model_answer(_raw(payload), _candidates())
        assert result.confidence == Decimal("0.8")

    def test_string_confidence_is_bad_confidence(self):
        raw = '{"family_id": 3, "new_family_name": null, "confidence": "0.8", "reason": "x"}'
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) == "bad_confidence"

    def test_confidence_above_one_is_bad_confidence(self):
        payload = _matched_payload(confidence=1.5)
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "bad_confidence"

    def test_confidence_below_zero_is_bad_confidence(self):
        payload = _matched_payload(confidence=-0.1)
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "bad_confidence"

    def test_nan_confidence_is_bad_confidence(self):
        raw = '{"family_id": 3, "new_family_name": null, "confidence": NaN, "reason": "x"}'
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) == "bad_confidence"

    def test_infinity_confidence_is_bad_confidence(self):
        raw = '{"family_id": 3, "new_family_name": null, "confidence": Infinity, "reason": "x"}'
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) == "bad_confidence"

    @pytest.mark.parametrize(
        ("literal", "expected"),
        [("0", Decimal("0")), ("0.0", Decimal("0")), ("1", Decimal("1")), ("1.0", Decimal("1"))],
    )
    def test_confidence_bounds_are_inclusive(self, literal, expected):
        raw = (
            '{"family_id": 3, "new_family_name": null, "confidence": '
            + literal
            + ', "reason": "x"}'
        )
        result = parse_model_answer(raw, _candidates())
        assert result.confidence == expected
        assert isinstance(result.confidence, Decimal)

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_confidence_detail_names_finiteness(self, literal):
        """Голые `NaN`/`Infinity` (модуль `json` принимает их по умолчанию)
        доходят до проверки как `Decimal` и отвергаются как НЕКОНЕЧНЫЕ — это
        и видит администратор в `validation_error`. Код `bad_confidence`
        получился бы и без этого решения (как `float` — через ветку типа),
        поэтому решение наблюдаемо только в `detail`."""
        raw = (
            '{"family_id": 3, "new_family_name": null, "confidence": '
            + literal
            + ', "reason": "x"}'
        )
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) == "bad_confidence"
        assert "конечн" in exc_info.value.detail

    def test_bool_confidence_is_bad_confidence(self):
        raw = '{"family_id": 3, "new_family_name": null, "confidence": true, "reason": "x"}'
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(raw, _candidates())
        assert _error_code(exc_info) == "bad_confidence"

    def test_confidence_is_never_a_python_float(self):
        payload = _matched_payload(confidence=0.42)
        result = parse_model_answer(_raw(payload), _candidates())
        assert not isinstance(result.confidence, float)


# ---------------------------------------------------------------------------
#  new_family_name / СИСТЕМА (спека §2.2)
# ---------------------------------------------------------------------------

class TestNewFamilyName:
    def test_name_is_stripped(self):
        payload = _new_payload(new_family_name="  Устройство лестниц  ")
        result = parse_model_answer(_raw(payload), _candidates())
        assert result.new_family_name == "Устройство лестниц"

    def test_empty_name_after_strip_is_empty_name(self):
        payload = _new_payload(new_family_name="   ")
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "empty_name"

    def test_null_name_at_zero_is_empty_name(self):
        """По промпту `null` — это «имени нет», то же самое, что пустая или
        пробельная строка, а не отдельный тип ошибки."""
        payload = _new_payload(new_family_name=None)
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "empty_name"

    @pytest.mark.parametrize(
        "value", [123, 0, False, [], {}], ids=["number", "zero", "false", "empty_list", "empty_object"]
    )
    def test_non_string_name_at_zero_is_bad_type(self, value):
        """Не-строка, отличная от `null` (число, список, `bool`), — уже не
        «имени нет», а нарушение типа; в том числе «ложные» `0`, `false`,
        `[]`, `{}` — `null` отделяется от них проверкой на `None`, а не на
        истинность."""
        payload = _new_payload(new_family_name=value)
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "bad_type"

    def test_name_ignored_when_family_id_is_nonzero(self):
        """При `family_id != 0` поле не проверяется
        вообще — даже заведомо неверный тип не должен ронять разбор."""
        raw = '{"family_id": 3, "new_family_name": 12345, "confidence": 0.5, "reason": "x"}'
        result = parse_model_answer(raw, _candidates())
        assert result.new_family_name is None

    def test_system_name_uppercase_sets_is_system(self):
        payload = _new_payload(new_family_name="СИСТЕМА")
        result = parse_model_answer(_raw(payload), _candidates())
        assert result.is_system is True
        assert result.family_id is None
        assert result.new_family_name == "СИСТЕМА"

    def test_system_name_mixed_case_sets_is_system(self):
        payload = _new_payload(new_family_name="Система")
        result = parse_model_answer(_raw(payload), _candidates())
        assert result.is_system is True
        # имя — как пришло (после strip), регистр не нормализуется
        assert result.new_family_name == "Система"

    def test_system_name_with_surrounding_spaces(self):
        payload = _new_payload(new_family_name="  система \t")
        result = parse_model_answer(_raw(payload), _candidates())
        assert result.is_system is True
        assert result.new_family_name == "система"

    @pytest.mark.parametrize("name", ["Кладка перегородок", "СИСТЕМА"], ids=["ordinary", "system"])
    def test_string_name_is_dropped_when_family_id_is_nonzero(self, name):
        """При совпадении с семьёй строковое имя (в том числе «СИСТЕМА»)
        отбрасывается целиком: ни имени, ни признака системы."""
        payload = _matched_payload(new_family_name=name)
        result = parse_model_answer(_raw(payload), _candidates())
        assert result.family_id == 3
        assert result.new_family_name is None
        assert result.is_system is False

    def test_similar_but_different_name_is_not_system(self):
        payload = _new_payload(new_family_name="Система вентиляции")
        result = parse_model_answer(_raw(payload), _candidates())
        assert result.is_system is False


# ---------------------------------------------------------------------------
#  reason (спека §2.2)
# ---------------------------------------------------------------------------

class TestReason:
    def test_reason_is_stripped(self):
        payload = _matched_payload(reason="  ok  ")
        result = parse_model_answer(_raw(payload), _candidates())
        assert result.reason == "ok"

    def test_empty_reason_after_strip_is_empty_reason(self):
        payload = _matched_payload(reason="   ")
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "empty_reason"

    def test_non_string_reason_is_bad_type(self):
        payload = _matched_payload(reason=None)
        with pytest.raises(AnswerSchemaError) as exc_info:
            parse_model_answer(_raw(payload), _candidates())
        assert _error_code(exc_info) == "bad_type"


# ---------------------------------------------------------------------------
#  Схема модуля
# ---------------------------------------------------------------------------

class TestModuleSchema:
    def test_response_schema_version_is_int(self):
        assert isinstance(RESPONSE_SCHEMA_VERSION, int)

    def test_model_answer_is_frozen(self):
        result = parse_model_answer(_raw(_matched_payload()), _candidates())
        with pytest.raises(FrozenInstanceError):
            result.family_id = 99  # type: ignore[misc]
