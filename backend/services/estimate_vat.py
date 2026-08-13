"""Правка ставок НДС сметы (спека пересчёта §2.7).

Инвариант «цель задаётся, только если база известна у КАЖДОГО предложения»
межтабличный, и `CHECK` его не выражает. Проверяется здесь, в одной транзакции,
под `SELECT … FOR UPDATE` на строке сметы — та же форма, которой закрыт ручной
разнос статей, и доказывается она тем же тестом на гонку.

Проверяется ИТОГОВОЕ состояние после применения обоих полей, а не каждое поле по
отдельности: запрос, снимающий базу и цель разом, законен, а по частям выглядел
бы как нарушение.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import Estimate, Lot, Proposal


class EstimateVatError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _Unset:
    """Часовой «поле не передано». Отличается от `None`, который значит «снять»."""

    def __repr__(self) -> str:  # pragma: no cover - диагностика
        return "UNSET"


UNSET = _Unset()


def set_vat_rates(
    db: Session,
    *,
    estimate_id: int,
    base_override: Decimal | None | _Unset = UNSET,
    target: Decimal | None | _Unset = UNSET,
    user_id: int,
) -> Estimate:
    estimate = db.execute(
        sa.select(Estimate).where(Estimate.id == estimate_id).with_for_update()
    ).scalar_one_or_none()
    if estimate is None:
        raise EstimateVatError("not_found", "Смета не найдена")

    new_base = (
        estimate.vat_rate_base_override if isinstance(base_override, _Unset) else base_override
    )
    new_target = estimate.vat_rate_target if isinstance(target, _Unset) else target

    if new_target is not None and new_base is None:
        unknown = db.execute(
            sa.select(sa.func.count())
            .select_from(Proposal)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == estimate_id, Proposal.vat_rate.is_(None))
        ).scalar_one()
        if unknown:
            raise EstimateVatError(
                "unknown_base",
                "Ставка показа недоступна: у части предложений сметы базовая ставка "
                "НДС неизвестна. Сначала объявите базовую ставку.",
            )

    estimate.vat_rate_base_override = new_base
    estimate.vat_rate_target = new_target

    if new_base is None and new_target is None:
        # Аудит — свойство ДЕЙСТВУЮЩЕЙ поправки. Истории правок в фиче нет по
        # решению, и аудит без поправки был бы половиной истории; сверх того,
        # `ON DELETE RESTRICT` держал бы пользователя неудаляемым навсегда.
        estimate.vat_rate_updated_by_id = None
        estimate.vat_rate_updated_at = None
    else:
        estimate.vat_rate_updated_by_id = user_id
        estimate.vat_rate_updated_at = datetime.now(UTC)

    db.flush()
    return estimate
