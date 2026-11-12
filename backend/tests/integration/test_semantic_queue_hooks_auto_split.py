"""Точка сверки очереди в `services.auto_split`: части деления наследуют семью и получают
значения через `apply_values`, который сверяет каждую часть сам (спека семантических
предложений §2.7, «Проверка инварианта»; спека упрощения каталога §2.5).

После деления у каждой части очередь такова, какой её построила бы сверка: новая часть не
несёт заданий значений (ей нечего спрашивать), а нужда частей в значениях пуста.
"""
from __future__ import annotations

import pytest

from services.semantic_reconcile import values_need
from tests.integration.test_simplify_auto_split import (
    _all_contexts,
    _jobs,
    _scene,
    _split,
)

pytestmark = pytest.mark.integration


def test_the_parts_of_a_split_have_no_unmet_need_and_the_new_part_has_no_value_jobs(
    db_session, factories
):
    scene = _scene(db_session, factories)

    assert _split(db_session, scene).status == "split"

    for context_id in _all_contexts(db_session, scene):
        need = values_need(db_session, context_id, scene.schema.id)
        assert need.ordinals == ()
    [new_ctx] = [c for c in _all_contexts(db_session, scene) if c != scene.ctx]
    assert _jobs(db_session, "context_values", context_id=new_ctx) == []
