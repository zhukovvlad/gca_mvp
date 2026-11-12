"""Статический текст промпта значений не должен совпадать со словарём приватности
(спека упрощения каталога §2.4; спека семантических предложений §2.3).

Слово из обычной русской лексики в промпте ловится именем подрядчика с тем же
написанием и отправляет КАЖДЫЙ запрос значений в `privacy_hold`. Словарь здесь
строится настоящим сборщиком из базы, подрядчик выдуманный.
"""
from __future__ import annotations

import pytest

from config import Settings
from services.semantic_privacy import build_privacy_dictionary, find_privacy_matches
from services.variant_request import (
    SchemaParameterIn,
    ValuesRequestMaterial,
    render_values_request,
)

pytestmark = pytest.mark.integration


def _rendered(title: str = "Стяжка пола 50 мм"):
    material = ValuesRequestMaterial(
        context_id=5,
        schema_id=70,
        title=title,
        unit_code="M2",
        article=None,
        paths=("Секция 1 / Полы",),
        parameters=(SchemaParameterIn(ordinal=1, name="Толщина", values=("50 мм", "80 мм")),),
    )
    return render_values_request(
        material, settings=Settings(_env_file=None, SECRET_KEY="x" * 32)
    )


def test_a_contractor_named_like_a_common_word_does_not_hit_the_static_prompt(
    db_session, factories
):
    factories.ContractorFactory.create(title="ООО «Выбор»")
    dictionary = build_privacy_dictionary(db_session)
    assert ("contractor", "выбор") in {(e.kind, e.text) for e in dictionary.entries}

    matches = find_privacy_matches(dictionary, _rendered())

    assert [m for m in matches if m.where == "prompt"] == []


def test_the_same_contractor_still_matches_in_the_context_row(db_session, factories):
    factories.ContractorFactory.create(title="ООО «Выбор»")
    dictionary = build_privacy_dictionary(db_session)

    matches = find_privacy_matches(dictionary, _rendered(title="Стяжка по выбор пола"))

    assert [(m.kind, m.where) for m in matches] == [("contractor", "context")]
