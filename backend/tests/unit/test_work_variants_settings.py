"""Настройки вариантов работ: профили моделей по виду задания, тарифы по виду,
порог автопринятия (спека 2026-10-02-catalog-variants-design.md §2.3, §2.5).
Неверное значение — ошибка загрузки `Settings`, а не молчаливо неверный расчёт."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from config import Settings
from models import SemanticJobKind
from services.semantic_cost import Tariffs, tariffs_from

_KEY = "k" * 32


def _load(**kw):
    return Settings(_env_file=None, SECRET_KEY=_KEY, **kw)


def _assert_rejected_only(name, excinfo):
    assert [err["loc"] for err in excinfo.value.errors()] == [(name,)]


_STR_DEFAULTS = {
    "SEMANTIC_SCHEMA_MODEL": "anthropic/claude-sonnet-5.5",
    "SEMANTIC_VALUES_MODEL": "anthropic/claude-sonnet-5.5",
    "SEMANTIC_VARIANTS_REASONING_EFFORT": "low",
}
_INT_DEFAULTS = {
    "SEMANTIC_SCHEMA_MAX_TOKENS": 20000,
    "SEMANTIC_VALUES_MAX_TOKENS": 600,
}
_PRICE_SUFFIXES = ["INPUT", "CACHE_WRITE", "CACHE_READ", "OUTPUT"]
_PRICE_DEFAULTS = ["2", "2.5", "0.2", "10"]
_PRICE_NAMES = [
    f"SEMANTIC_{kind}_PRICE_{suffix}_PER_M"
    for kind in ("SCHEMA", "VALUES")
    for suffix in _PRICE_SUFFIXES
]


def test_work_variants_settings_defaults():
    s = _load()
    for name, expected in {**_STR_DEFAULTS, **_INT_DEFAULTS}.items():
        assert getattr(s, name) == expected, name
    for kind in ("SCHEMA", "VALUES"):
        got = [
            getattr(s, f"SEMANTIC_{kind}_PRICE_{suffix}_PER_M")
            for suffix in _PRICE_SUFFIXES
        ]
        assert got == [Decimal(x) for x in _PRICE_DEFAULTS], kind
        assert all(isinstance(v, Decimal) for v in got)
    assert s.SEMANTIC_AUTO_ACCEPT_THRESHOLD is None


@pytest.mark.parametrize("name", ["SEMANTIC_SCHEMA_MAX_TOKENS", "SEMANTIC_VALUES_MAX_TOKENS"])
@pytest.mark.parametrize("bad", [0, -1])
def test_work_variants_settings_max_tokens_below_one_rejected(name, bad):
    with pytest.raises(ValidationError) as excinfo:
        _load(**{name: bad})
    _assert_rejected_only(name, excinfo)


@pytest.mark.parametrize("name", ["SEMANTIC_SCHEMA_MAX_TOKENS", "SEMANTIC_VALUES_MAX_TOKENS"])
def test_work_variants_settings_max_tokens_one_accepted(name):
    assert getattr(_load(**{name: 1}), name) == 1


@pytest.mark.parametrize("name", _PRICE_NAMES)
def test_work_variants_settings_price_negative_rejected(name):
    with pytest.raises(ValidationError) as excinfo:
        _load(**{name: Decimal("-0.01")})
    _assert_rejected_only(name, excinfo)


@pytest.mark.parametrize("name", _PRICE_NAMES)
def test_work_variants_settings_price_zero_accepted(name):
    assert getattr(_load(**{name: Decimal("0")}), name) == Decimal("0")


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_work_variants_settings_reasoning_effort_accepted(effort):
    got = _load(SEMANTIC_VARIANTS_REASONING_EFFORT=effort).SEMANTIC_VARIANTS_REASONING_EFFORT
    assert got == effort


@pytest.mark.parametrize("bad", ["", "none", "minimal", "LOW", "xhigh"])
def test_work_variants_settings_reasoning_effort_outside_list_rejected(bad):
    with pytest.raises(ValidationError) as excinfo:
        _load(SEMANTIC_VARIANTS_REASONING_EFFORT=bad)
    _assert_rejected_only("SEMANTIC_VARIANTS_REASONING_EFFORT", excinfo)


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-0.1"), Decimal("1.01"), Decimal("2")])
def test_work_variants_settings_threshold_out_of_range_rejected(bad):
    with pytest.raises(ValidationError) as excinfo:
        _load(SEMANTIC_AUTO_ACCEPT_THRESHOLD=bad)
    _assert_rejected_only("SEMANTIC_AUTO_ACCEPT_THRESHOLD", excinfo)


@pytest.mark.parametrize("ok", ["1", "0.5", "0.0001"])
def test_work_variants_settings_threshold_in_range_accepted(ok):
    got = _load(SEMANTIC_AUTO_ACCEPT_THRESHOLD=Decimal(ok)).SEMANTIC_AUTO_ACCEPT_THRESHOLD
    assert got == Decimal(ok)
    assert isinstance(got, Decimal)


def test_work_variants_settings_threshold_from_environment_is_exact_decimal(monkeypatch):
    monkeypatch.setenv("SEMANTIC_AUTO_ACCEPT_THRESHOLD", "0.85")
    got = _load().SEMANTIC_AUTO_ACCEPT_THRESHOLD
    assert isinstance(got, Decimal)
    assert got == Decimal("0.85")
    assert got.as_tuple() == Decimal("0.85").as_tuple()


@pytest.mark.parametrize("bad", ["0", "-0.5", "1.5"])
def test_work_variants_settings_threshold_out_of_range_from_environment_rejected(monkeypatch, bad):
    monkeypatch.setenv("SEMANTIC_AUTO_ACCEPT_THRESHOLD", bad)
    with pytest.raises(ValidationError) as excinfo:
        _load()
    _assert_rejected_only("SEMANTIC_AUTO_ACCEPT_THRESHOLD", excinfo)


@pytest.mark.parametrize("blank", ["", "   ", "\t"], ids=["empty", "spaces", "tab"])
def test_work_variants_settings_threshold_empty_string_in_environment_is_none(monkeypatch, blank):
    # Пробельная строка — тоже «не задан»: числа pydantic читает с обрезкой
    # пробелов, поэтому строка из одних пробелов — та же пустота, а не ошибка.
    monkeypatch.setenv("SEMANTIC_AUTO_ACCEPT_THRESHOLD", blank)
    assert _load().SEMANTIC_AUTO_ACCEPT_THRESHOLD is None


_ENV_CASES = [
    ("SEMANTIC_SCHEMA_MODEL", "vendor/schema-model", "vendor/schema-model"),
    ("SEMANTIC_VALUES_MODEL", "vendor/values-model", "vendor/values-model"),
    ("SEMANTIC_VARIANTS_REASONING_EFFORT", "high", "high"),
    ("SEMANTIC_SCHEMA_MAX_TOKENS", "123", 123),
    ("SEMANTIC_VALUES_MAX_TOKENS", "77", 77),
    ("SEMANTIC_AUTO_ACCEPT_THRESHOLD", "0.9", Decimal("0.9")),
] + [(name, "3.25", Decimal("3.25")) for name in _PRICE_NAMES]


@pytest.mark.parametrize(("name", "raw", "expected"), _ENV_CASES)
def test_work_variants_settings_read_from_environment(monkeypatch, name, raw, expected):
    monkeypatch.setenv(name, raw)
    got = getattr(_load(), name)
    assert got == expected
    # `float` 3.25 равен `Decimal("3.25")` по `==` — тип сверяется отдельно.
    assert type(got) is type(expected)


def _distinct_prices():
    return _load(
        SEMANTIC_PRICE_INPUT_PER_M=Decimal("1.1"),
        SEMANTIC_PRICE_CACHE_WRITE_PER_M=Decimal("1.2"),
        SEMANTIC_PRICE_CACHE_READ_PER_M=Decimal("1.3"),
        SEMANTIC_PRICE_OUTPUT_PER_M=Decimal("1.4"),
        SEMANTIC_SCHEMA_PRICE_INPUT_PER_M=Decimal("2.1"),
        SEMANTIC_SCHEMA_PRICE_CACHE_WRITE_PER_M=Decimal("2.2"),
        SEMANTIC_SCHEMA_PRICE_CACHE_READ_PER_M=Decimal("2.3"),
        SEMANTIC_SCHEMA_PRICE_OUTPUT_PER_M=Decimal("2.4"),
        SEMANTIC_VALUES_PRICE_INPUT_PER_M=Decimal("3.1"),
        SEMANTIC_VALUES_PRICE_CACHE_WRITE_PER_M=Decimal("3.2"),
        SEMANTIC_VALUES_PRICE_CACHE_READ_PER_M=Decimal("3.3"),
        SEMANTIC_VALUES_PRICE_OUTPUT_PER_M=Decimal("3.4"),
    )


def test_work_variants_tariffs_default_kind_is_family_suggestion_with_old_prices():
    s = _distinct_prices()
    expected = Tariffs(Decimal("1.1"), Decimal("1.2"), Decimal("1.3"), Decimal("1.4"))
    assert tariffs_from(s) == expected
    assert tariffs_from(s, SemanticJobKind.family_suggestion) == expected
    assert tariffs_from(s) == tariffs_from(s, SemanticJobKind.family_suggestion)


def test_work_variants_tariffs_each_kind_takes_its_own_four_fields():
    s = _distinct_prices()
    assert tariffs_from(s, SemanticJobKind.family_schema) == Tariffs(
        Decimal("2.1"), Decimal("2.2"), Decimal("2.3"), Decimal("2.4")
    )
    assert tariffs_from(s, SemanticJobKind.context_values) == Tariffs(
        Decimal("3.1"), Decimal("3.2"), Decimal("3.3"), Decimal("3.4")
    )


def test_work_variants_tariffs_three_kinds_pairwise_distinct():
    s = _distinct_prices()
    got = [tariffs_from(s, k) for k in SemanticJobKind]
    assert len(got) == 3
    assert len(set(got)) == 3


def test_work_variants_tariffs_unknown_kind_raises_value_error():
    with pytest.raises(ValueError):
        tariffs_from(_load(), "something_else")  # type: ignore[arg-type]
