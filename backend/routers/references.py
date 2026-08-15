"""Роутер справочников: классы объектов, объекты, подрядчики (AGENTS.md §7).

Роутер тонкий: валидация формы — Pydantic, домен — `crud/references.py`,
трансляция `DomainError` → HTTP — `_raise`.

**Права (решение фазы 5 §6.2):** чтение — любому аутентифицированному (проверка
навешена на include_router в main.py), изменение — `require_admin`. Классы
объектов §3 закрепляет за admin напрямую; договоры, объекты и подрядчики §3 не
распределял — решение принято с пользователем в начале фазы.

CSRF на POST/PATCH/DELETE обеспечивает middleware из main.py — вручную ничего
навешивать не нужно.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from auth import require_admin
from crud import references as crud_refs
from crud.common import DomainError
from database import get_db
from models import User
from responses import decimal_json

router = APIRouter(prefix="/api/v1", tags=["references"])


def _raise(err: DomainError):
    raise HTTPException(err.status_code, err.detail)


# ---------------------------------------------------------------------------
#  Схемы
# ---------------------------------------------------------------------------

class RateClassCreate(BaseModel):
    title: str
    description: str | None = None


class RateClassUpdate(BaseModel):
    """PATCH: непереданное поле не меняется (`exclude_unset`), `null` — сбрасывает.

    Поэтому `title` объявлен как `str | None`, но `null` для него отклоняется в
    хендлере: название обязательно, а разница «не передано» / «передано null»
    иначе была бы неотличима.
    """
    title: str | None = None
    description: str | None = None


class _AreaMixin(BaseModel):
    """Площади: не `float` и не отрицательные (спека §2.4, §2.6).

    У каждого валидатора СВОЙ ручной список имён, и защита новым полем **не
    наследуется**: колонка, не вписанная в оба списка, молча примет `float` —
    и двоичный хвост приедет в знаменатель руб/м², ради защиты от которого
    миксин и написан (спека 2026-08-15 §2.6). Пропуск ловится параметризацией
    по именам полей в `test_references_api.py`, а не чтением кода.
    """

    @field_validator(
        "area_aboveground_sp",
        "area_underground_sp",
        "area_useful_sp",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def _reject_float(cls, value):
        if isinstance(value, float):
            raise ValueError(
                "Площадь передавайте строкой (например \"62399.70\"), а не числом "
                "с плавающей точкой: float внесёт двоичный хвост в знаменатель "
                "руб/м²."
            )
        return value

    @field_validator(
        "area_aboveground_sp",
        "area_underground_sp",
        "area_useful_sp",
        check_fields=False,
    )
    @classmethod
    def _non_negative(cls, value: Decimal | None):
        if value is not None and value < 0:
            raise ValueError("Площадь не может быть отрицательной.")
        return value


class ObjectCreate(_AreaMixin):
    title: str
    address: str | None = None
    rate_class_id: int | None = None
    area_aboveground_sp: Decimal | None = None
    area_underground_sp: Decimal | None = None
    area_useful_sp: Decimal | None = None


class ObjectUpdate(_AreaMixin):
    title: str | None = None
    address: str | None = None
    rate_class_id: int | None = None
    area_aboveground_sp: Decimal | None = None
    area_underground_sp: Decimal | None = None
    area_useful_sp: Decimal | None = None


class ContractorCreate(BaseModel):
    title: str
    inn: str
    address: str | None = None
    accreditation: str | None = None


class ContractorUpdate(BaseModel):
    title: str | None = None
    inn: str | None = None
    address: str | None = None
    accreditation: str | None = None


def _patch_fields(body: BaseModel, *, non_nullable: tuple[str, ...]) -> dict:
    """Переданные поля PATCH; `null` в обязательном поле — 422.

    `exclude_unset=True` отличает «поле не передано» от «передано null»: первое
    оставляет значение как есть, второе — осмысленный сброс для необязательных
    полей и ошибка для обязательных. Тот же приём, что в `routers/admin.py`.
    """
    fields = body.model_dump(exclude_unset=True)
    for name in non_nullable:
        if name in fields and fields[name] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Поле {name} не может быть null.",
            )
    return fields


def _unset(fields: dict, name: str):
    """Значение поля либо sentinel UNSET, если поле не передано."""
    return fields.get(name, crud_refs.UNSET)


# ---------------------------------------------------------------------------
#  Классы объектов (право admin — §3 напрямую)
# ---------------------------------------------------------------------------

@router.get("/rate-classes")
def list_rate_classes(db: Session = Depends(get_db)):
    """Все классы объектов со счётчиками использования."""
    return crud_refs.list_rate_classes(db)


@router.post("/rate-classes", status_code=status.HTTP_201_CREATED)
def create_rate_class(
    body: RateClassCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        return crud_refs.create_rate_class(db, title=body.title, description=body.description)
    except DomainError as e:
        _raise(e)


@router.patch("/rate-classes/{rate_class_id}")
def update_rate_class(
    rate_class_id: int,
    body: RateClassUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    fields = _patch_fields(body, non_nullable=("title",))
    try:
        return crud_refs.update_rate_class(
            db,
            rate_class_id,
            title=_unset(fields, "title"),
            description=_unset(fields, "description"),
        )
    except DomainError as e:
        _raise(e)


@router.delete("/rate-classes/{rate_class_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rate_class(
    rate_class_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Удалить класс. 409, если на него ссылаются договоры или нормативы."""
    try:
        crud_refs.delete_rate_class(db, rate_class_id)
    except DomainError as e:
        _raise(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
#  Объекты
# ---------------------------------------------------------------------------

@router.get("/objects")
def list_objects(
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Объекты с пагинацией; `q` — подстрока названия или адреса.

    `decimal_json` обязателен: в ответе есть площади (Decimal), § 2.7.
    """
    return decimal_json(crud_refs.list_objects(db, q=q, page=page, page_size=page_size))


@router.get("/objects/{object_id}")
def get_object(object_id: int, db: Session = Depends(get_db)):
    try:
        return decimal_json(crud_refs.get_object_dict(db, object_id))
    except DomainError as e:
        _raise(e)


@router.post("/objects", status_code=status.HTTP_201_CREATED)
def create_object(
    body: ObjectCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """`decimal_json(..., 201)` передаёт статус явно: `Response` несёт свой
    статус мимо `status_code` декоратора, без этого эндпоинт молча
    деградировал бы до 200 (спека §1.5 п. 2, §2.7)."""
    try:
        body_out = crud_refs.create_object(
            db,
            title=body.title,
            address=body.address,
            rate_class_id=body.rate_class_id,
            area_aboveground_sp=body.area_aboveground_sp,
            area_underground_sp=body.area_underground_sp,
            area_useful_sp=body.area_useful_sp,
        )
    except DomainError as e:
        _raise(e)
    return decimal_json(body_out, status.HTTP_201_CREATED)


@router.patch("/objects/{object_id}")
def update_object(
    object_id: int,
    body: ObjectUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    fields = _patch_fields(body, non_nullable=("title",))
    try:
        body_out = crud_refs.update_object(
            db,
            object_id,
            title=_unset(fields, "title"),
            address=_unset(fields, "address"),
            rate_class_id=_unset(fields, "rate_class_id"),
            area_aboveground_sp=_unset(fields, "area_aboveground_sp"),
            area_underground_sp=_unset(fields, "area_underground_sp"),
            area_useful_sp=_unset(fields, "area_useful_sp"),
        )
    except DomainError as e:
        _raise(e)
    return decimal_json(body_out)


@router.delete("/objects/{object_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_object(
    object_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        crud_refs.delete_object(db, object_id)
    except DomainError as e:
        _raise(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
#  Подрядчики
# ---------------------------------------------------------------------------

@router.get("/contractors")
def list_contractors(
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Подрядчики с пагинацией; `q` — подстрока названия или БИН/ИНН."""
    return crud_refs.list_contractors(db, q=q, page=page, page_size=page_size)


@router.get("/contractors/{contractor_id}")
def get_contractor(contractor_id: int, db: Session = Depends(get_db)):
    try:
        return crud_refs.get_contractor_dict(db, contractor_id)
    except DomainError as e:
        _raise(e)


@router.post("/contractors", status_code=status.HTTP_201_CREATED)
def create_contractor(
    body: ContractorCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        return crud_refs.create_contractor(
            db,
            title=body.title,
            inn=body.inn,
            address=body.address,
            accreditation=body.accreditation,
        )
    except DomainError as e:
        _raise(e)


@router.patch("/contractors/{contractor_id}")
def update_contractor(
    contractor_id: int,
    body: ContractorUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    fields = _patch_fields(body, non_nullable=("title", "inn"))
    try:
        return crud_refs.update_contractor(
            db,
            contractor_id,
            title=_unset(fields, "title"),
            inn=_unset(fields, "inn"),
            address=_unset(fields, "address"),
            accreditation=_unset(fields, "accreditation"),
        )
    except DomainError as e:
        _raise(e)


@router.delete("/contractors/{contractor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contractor(
    contractor_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        crud_refs.delete_contractor(db, contractor_id)
    except DomainError as e:
        _raise(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
