"""Маршруты «подозрительных строк договоров» (спека `2026-11-24-suspicious-lines-design.md` §3).

Только `admin` (`require_admin`; у `member` — 403). Список и счётчики читают индекс из кэша;
решения «не ошибка» (`PUT`/`DELETE …/not-error`) добавляет следующая задача.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import crud.suspicious_lines as crud_suspicious
from auth import require_admin
from crud.common import DomainError
from database import get_db
from models import User
from routers.domain_errors import raise_domain_error

router = APIRouter(prefix="/api/v1/suspicious-lines", tags=["suspicious-lines"])


@router.get("")
def list_suspicious_lines_route(
    contract_id: int | None = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Список подозрительных строк: не разобранные и разобранные; `contract_id` — только договор (404, если его нет)."""
    try:
        return crud_suspicious.suspicious_list(db, contract_id=contract_id)
    except DomainError as exc:
        raise_domain_error(exc)


@router.get("/counts")
def suspicious_counts_route(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Счётчики не разобранных: всего и по договорам (договоры с нулём не перечисляются)."""
    return crud_suspicious.suspicious_counts(db)
