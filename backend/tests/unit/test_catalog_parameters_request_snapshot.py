"""Снимок отпечатков запросов `context_values` и `family_schema` на материале с
точными главными параметрами (спека 3в §2.4: когда запрошены все активные
параметры и среди них нет диапазонов, тело запроса совпадает с прежним байт в
байт, и существующие задания, отпечатки и кэш префикса не сдвигаются).

Литералы получены прогоном на коде до правки рендера и записаны вручную: эталон
не вычисляется тем же кодом, что проверяется. Профиль модели задан явно, чтобы
хэш не зависел от окружения.
"""
from __future__ import annotations

from config import Settings
from services.variant_request import (
    SchemaParameterIn,
    SchemaRequestMaterial,
    ValuesRequestMaterial,
    render_schema_request,
    render_values_request,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        SECRET_KEY="x" * 32,
        SEMANTIC_VALUES_MODEL="vendor/values-model",
        SEMANTIC_VALUES_MAX_TOKENS=900,
        SEMANTIC_SCHEMA_MODEL="vendor/schema-model",
        SEMANTIC_SCHEMA_MAX_TOKENS=2500,
        SEMANTIC_VARIANTS_REASONING_EFFORT="low",
    )


def _values_material() -> ValuesRequestMaterial:
    return ValuesRequestMaterial(
        context_id=5,
        schema_id=70,
        title="Стяжка пола 50 мм",
        unit_code="M2",
        article="12.03 Полы",
        paths=("Секция 1 / Полы", "Секция 2 / Полы"),
        parameters=(
            SchemaParameterIn(ordinal=1, name="Толщина", values=("50 мм", "80 мм")),
            SchemaParameterIn(ordinal=3, name="Материал", values=("бетон", "цементный раствор")),
        ),
    )


def _schema_material() -> SchemaRequestMaterial:
    return SchemaRequestMaterial(
        family_id=7,
        schema_id=70,
        title="Устройство пола",
        unit_code="M2",
        definition="Пол по грунту",
        names=("Стяжка пола 50 мм", "Стяжка пола 80 мм", "Устройство бетонного пола"),
    )


VALUES_REQUEST_HASH = "2b2de6c7d26b53314e5582540a0a4bd0a6f7403917ab4195e73e69e5c4f527b8"
SCHEMA_REQUEST_HASH = "399aed346028e2c173c6107dc0114fc0ecbf649fc675f3cacc798c491cbce685"


def test_values_request_hash_of_full_exact_subset_is_pinned():
    rendered = render_values_request(_values_material(), settings=_settings())
    assert rendered.request_hash == VALUES_REQUEST_HASH


def test_schema_request_hash_is_pinned():
    rendered = render_schema_request(_schema_material(), settings=_settings())
    assert rendered.request_hash == SCHEMA_REQUEST_HASH
