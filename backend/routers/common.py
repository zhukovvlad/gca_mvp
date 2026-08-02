"""Общие хелперы роутеров."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import MaterialType


def resolve_direction_type(db: Session, direction: str | None) -> MaterialType | None:
    """code направления → MaterialType. Неизвестный code → 422 (спека §6).

    Тип «other» направлением не является (ADR #9): фильтровать по нему нельзя —
    тоже 422, иначе API-контракт противоречил бы UI-модели."""
    if direction is None:
        return None
    mt = db.query(MaterialType).filter(MaterialType.code == direction).first()
    if mt is None:
        raise HTTPException(status_code=422, detail=f"Неизвестное направление: {direction}")
    if mt.code == "other":
        raise HTTPException(status_code=422, detail="Тип «Прочее» направлением не является")
    return mt
