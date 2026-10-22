"""Помощники тестов правки схемы на месте: тело правки и вызов «предпросмотр, затем
сохранение», как у экрана. Вынесены в отдельный модуль, чтобы наборы других точек
(`test_semantic_queue_hooks_work_variants.py`, `test_work_variants_reconcile_races.py`,
`test_catalog_parameters_identity.py`) брали их, не создавая круговых импортов."""
from __future__ import annotations

from decimal import Decimal

from models import ParameterRole, ParameterValueKind
from services.schema_edit import (
    ParameterDraft,
    RangeSpec,
    apply_schema_edit,
    preview_schema_edit,
)

PARAMS = (
    (1, "Толщина", ["50 мм", "100 мм"]),
    (2, "Материал", ["бетон", "кирпич"]),
)


def draft(
    ordinal, name, *values, role="main", kind="exact", closed=False, rng=None
) -> ParameterDraft:
    """Параметр тела правки; `rng` — `(от, до, шаг)` для диапазона."""
    return ParameterDraft(
        ordinal=ordinal, name=name, role=ParameterRole(role),
        value_kind=ParameterValueKind(kind), closed=closed, values=tuple(values),
        range=None if rng is None else RangeSpec(*(Decimal(str(n)) for n in rng)),
    )


def same_drafts(params=PARAMS) -> list[ParameterDraft]:
    """Тело, повторяющее схему мира: правка без изменений."""
    return [draft(ordinal, name, *values) for ordinal, name, values in params]


def edit(db, family_id, drafts, actor_id):
    """Предпросмотр и сохранение двумя вызовами, как у экрана."""
    summary = preview_schema_edit(db, family_id=family_id, drafts=drafts)
    return apply_schema_edit(
        db, family_id=family_id, drafts=drafts, preview_hash=summary.preview_hash,
        actor_id=actor_id,
    )
