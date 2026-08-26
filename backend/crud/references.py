"""CRUD справочников: классы объектов, объекты, подрядчики (AGENTS.md §4, §7).

Три сущности, без которых нельзя завести договор. Роутер
(`routers/references.py`) остаётся тонким и делегирует сюда.

**Права (решение фазы 5 §6.2, согласовано с пользователем):** чтение — любому
аутентифицированному, изменение — только `admin`. Проверка живёт в роутере
зависимостью `require_admin`; здесь только домен.

**Удаление отказывается, а не каскадит.** У `contracts.object_id`,
`contracts.contractor_id`, `contracts.rate_class_id` и `proposals.contractor_id`
FK объявлены без каскада — БД и сама не пустит. Но 500 из `IntegrityError` не
объясняет человеку, что произошло, поэтому зависимости проверяются заранее и
отказ приходит как 409 с числом мешающих записей. Исключение одно:
`objects.rate_class_id` объявлен `ON DELETE SET NULL` намеренно (класс объекта —
лишь значение по умолчанию для новых договоров, §4), поэтому класс, который
используется только как дефолт объекта, удалить можно — объект потеряет дефолт,
а не историю.
"""
from __future__ import annotations

import logging
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import (
    DomainError,
    clamp_page,
    iso,
    paginated,
    require_text,
    rollback_on_domain_error,
    translating_integrity,
)
from models import Contract, Contractor, ObjectModel, OfferPackage, Proposal, RateClass, RateStandard
from utils import canonicalize_inn

log = logging.getLogger(__name__)

_UNIQUE_MESSAGES = {
    "uq_rate_classes_title": "Класс объектов с таким названием уже есть.",
    "uq_objects_title": "Объект с таким названием уже есть.",
    "uq_contractors_inn": "Подрядчик с таким БИН/ИНН уже есть.",
}

#: Sentinel «поле не передано» — отличает PATCH без поля от PATCH с null.
UNSET = object()


def _count_of(column, parent_id_column) -> sa.ScalarSelect:
    """Коррелированный подзапрос «сколько строк ссылается на эту запись».

    Считается в том же SELECT, что и сама запись: иначе список из 50 объектов
    давал бы 50 дозапросов на счётчик договоров.

    Корреляция автоматическая: во внешнем SELECT таблица-родитель уже есть, и
    SQLAlchemy убирает её из FROM подзапроса, оставляя таблицу-потомка.
    """
    return sa.select(sa.func.count()).where(column == parent_id_column).scalar_subquery()


# ---------------------------------------------------------------------------
#  Классы объектов
# ---------------------------------------------------------------------------

def _rate_class_dict(rc: RateClass, contracts: int, objects: int, standards: int) -> dict:
    return {
        "id": rc.id,
        "title": rc.title,
        "description": rc.description,
        "contracts_count": contracts,
        "objects_count": objects,
        "standards_count": standards,
        "created_at": iso(rc.created_at),
        "updated_at": iso(rc.updated_at),
    }


def list_rate_classes(db: Session) -> list[dict]:
    """Все классы объектов со счётчиками использования.

    Без пагинации намеренно: классов единицы, а UI нужен полный список для
    выпадающих списков в форме договора и нормативов.
    """
    rows = db.execute(
        sa.select(
            RateClass,
            _count_of(Contract.rate_class_id, RateClass.id),
            _count_of(ObjectModel.rate_class_id, RateClass.id),
            _count_of(RateStandard.rate_class_id, RateClass.id),
        ).order_by(RateClass.title)
    ).all()
    return [_rate_class_dict(*row) for row in rows]


def get_rate_class(db: Session, rate_class_id: int) -> RateClass:
    rc = db.get(RateClass, rate_class_id)
    if rc is None:
        raise DomainError(404, f"Класс объектов {rate_class_id} не найден.")
    return rc


def create_rate_class(db: Session, *, title: str, description: str | None = None) -> dict:
    title = require_text(title, "Название")
    if db.query(RateClass).filter(RateClass.title == title).first():
        raise DomainError(409, _UNIQUE_MESSAGES["uq_rate_classes_title"])

    rc = RateClass(title=title, description=(description or None))
    db.add(rc)
    with translating_integrity(db, _UNIQUE_MESSAGES):
        db.commit()
    db.refresh(rc)
    log.info("rate_class_created id=%s title=%s", rc.id, rc.title)
    return get_rate_class_dict(db, rc.id)


def update_rate_class(db: Session, rate_class_id: int, *, title=UNSET, description=UNSET) -> dict:
    rc = get_rate_class(db, rate_class_id)
    if title is not UNSET:
        rc.title = require_text(title, "Название")
    if description is not UNSET:
        rc.description = (description or "").strip() or None
    with translating_integrity(db, _UNIQUE_MESSAGES):
        db.commit()
    db.refresh(rc)
    log.info("rate_class_updated id=%s", rc.id)
    return get_rate_class_dict(db, rc.id)


def get_rate_class_dict(db: Session, rate_class_id: int) -> dict:
    """Один класс со счётчиками — тем же выражением, что и список."""
    row = db.execute(
        sa.select(
            RateClass,
            _count_of(Contract.rate_class_id, RateClass.id),
            _count_of(ObjectModel.rate_class_id, RateClass.id),
            _count_of(RateStandard.rate_class_id, RateClass.id),
        ).where(RateClass.id == rate_class_id)
    ).first()
    if row is None:
        raise DomainError(404, f"Класс объектов {rate_class_id} не найден.")
    return _rate_class_dict(*row)


def delete_rate_class(db: Session, rate_class_id: int) -> None:
    """Удалить класс. Отказ, если на него ссылаются договоры или нормативы."""
    rc = get_rate_class(db, rate_class_id)
    contracts = db.query(Contract).filter(Contract.rate_class_id == rc.id).count()
    standards = db.query(RateStandard).filter(RateStandard.rate_class_id == rc.id).count()
    if contracts or standards:
        raise DomainError(
            409,
            f"Класс «{rc.title}» удалить нельзя: на него ссылаются договоры "
            f"({contracts}) и нормативы ({standards}). Класс в договоре — это снимок "
            "на момент подписания, он и держит историю отклонений.",
        )
    db.delete(rc)
    db.commit()
    log.info("rate_class_deleted id=%s", rate_class_id)


# ---------------------------------------------------------------------------
#  Объекты
# ---------------------------------------------------------------------------

def _object_dict(obj: ObjectModel, contracts: int, rate_class_title: str | None) -> dict:
    return {
        "id": obj.id,
        "title": obj.title,
        "address": obj.address,
        "rate_class_id": obj.rate_class_id,
        "rate_class_title": rate_class_title,
        "area_aboveground_sp": obj.area_aboveground_sp,
        "area_underground_sp": obj.area_underground_sp,
        "area_total_sp": obj.area_total_sp,
        "area_useful_sp": obj.area_useful_sp,
        "contracts_count": contracts,
        "created_at": iso(obj.created_at),
        "updated_at": iso(obj.updated_at),
    }


def _objects_select():
    return sa.select(
        ObjectModel,
        _count_of(Contract.object_id, ObjectModel.id),
        RateClass.title,
    ).outerjoin(RateClass, RateClass.id == ObjectModel.rate_class_id)


def list_objects(db: Session, *, q: str | None = None, page: int = 1, page_size: int = 20) -> dict:
    """Объекты с пагинацией. `q` — подстрока названия или адреса."""
    page, page_size = clamp_page(page, page_size)
    stmt = _objects_select()
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            sa.or_(ObjectModel.title.ilike(pattern), ObjectModel.address.ilike(pattern))
        )

    rows, total = paginated(
        db, stmt, order_by=(ObjectModel.title,), page=page, page_size=page_size
    )
    return {
        "items": [_object_dict(*row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_object_dict(db: Session, object_id: int) -> dict:
    row = db.execute(_objects_select().where(ObjectModel.id == object_id)).first()
    if row is None:
        raise DomainError(404, f"Объект {object_id} не найден.")
    return _object_dict(*row)


def _resolve_rate_class(db: Session, rate_class_id: int | None) -> int | None:
    if rate_class_id is None:
        return None
    get_rate_class(db, rate_class_id)   # 404, если класса нет
    return rate_class_id


def validate_area_pair(above: Decimal | None, under: Decimal | None) -> None:
    """Судит ИТОГОВОЕ состояние пары площадей, а не переданную дельту (§2.4).

    Вызывается и из `create_object`, и из `update_object`: у создания «итоговое
    состояние» — это просто вход. Одна функция на оба пути, иначе правило жило
    бы в двух местах и разъехалось бы при первой правке.

    `CHECK` в схеме говорит то же самое и остаётся последним рубежом; здесь
    отказ человекочитаемый, потому что `translating_integrity` переводит только
    нарушения уникальности и только в 409.
    """
    if (above is None) != (under is None):
        raise DomainError(
            422,
            "Площади задаются парой: укажите наземную и подземную вместе либо "
            "не указывайте ни одной.",
        )
    if above is None:
        return
    if above + under <= 0:
        raise DomainError(
            422,
            "Общая площадь получилась нулевой, а по ней считается руб/м². "
            "Хотя бы одна из частей должна быть больше нуля.",
        )


def validate_useful_area(
    above: Decimal | None, under: Decimal | None, useful: Decimal | None
) -> None:
    """Судит ИТОГОВОЕ состояние трёх площадей, а не переданную дельту (§2.5).

    Отдельная функция, а не расширение `validate_area_pair`: правило пары —
    про надземную и подземную, и полезная в него не входит (§2.4). Общая у них
    только форма — обе судят состояние ПОСЛЕ применения дельты и обе зовутся с
    обоих путей, создания и правки.

    Нарушение вносит любая из трёх колонок: `PATCH`, уменьшающий СЛАГАЕМОЕ,
    роняет общую под уже заведённую полезную ровно так же, как `PATCH`,
    поднимающий саму полезную. Проверять «своё» поле здесь недостаточно.

    Сравнение — через СЛАГАЕМЫЕ, тем же выражением, что `CHECK` в схеме (§2.3),
    и с той же границей §2.4: при незаведённой паре сравнивать не с чем, и
    полезная проходит без сравнения. `CHECK` остаётся последним рубежом; здесь
    отказ человекочитаемый, потому что `translating_integrity` переводит только
    нарушения уникальности и только в 409.
    """
    if useful is None or above is None or under is None:
        return
    total = above + under
    if useful > total:
        raise DomainError(
            422,
            f"Полезная площадь ({useful}) больше общей ({total}). Полезная — "
            "часть общей, а не третье слагаемое: уменьшите её либо увеличьте "
            "наземную или подземную площадь.",
        )


def create_object(
    db: Session,
    *,
    title: str,
    address: str | None = None,
    rate_class_id: int | None = None,
    area_aboveground_sp: Decimal | None = None,
    area_underground_sp: Decimal | None = None,
    area_useful_sp: Decimal | None = None,
) -> dict:
    title = require_text(title, "Название")
    validate_area_pair(area_aboveground_sp, area_underground_sp)
    validate_useful_area(area_aboveground_sp, area_underground_sp, area_useful_sp)
    rate_class_id = _resolve_rate_class(db, rate_class_id)
    if db.query(ObjectModel).filter(ObjectModel.title == title).first():
        raise DomainError(409, _UNIQUE_MESSAGES["uq_objects_title"])

    # address NOT NULL без дефолта в схеме; пустая строка допустима — адрес
    # уточняют позже, а объект нужен уже сейчас, чтобы завести договор.
    obj = ObjectModel(
        title=title,
        address=(address or "").strip(),
        rate_class_id=rate_class_id,
        area_aboveground_sp=area_aboveground_sp,
        area_underground_sp=area_underground_sp,
        area_useful_sp=area_useful_sp,
    )
    db.add(obj)
    with translating_integrity(db, _UNIQUE_MESSAGES):
        db.commit()
    db.refresh(obj)
    log.info("object_created id=%s title=%s", obj.id, obj.title)
    return get_object_dict(db, obj.id)


def update_object(
    db: Session,
    object_id: int,
    *,
    title=UNSET,
    address=UNSET,
    rate_class_id=UNSET,
    area_aboveground_sp=UNSET,
    area_underground_sp=UNSET,
    area_useful_sp=UNSET,
) -> dict:
    obj = db.get(ObjectModel, object_id)
    if obj is None:
        raise DomainError(404, f"Объект {object_id} не найден.")
    # Откат обязателен: `_resolve_rate_class` отвергает неизвестный класс уже
    # после того, как название и адрес присвоены, и без отката следующее чтение в
    # этой же сессии увидело бы отвергнутую правку. Проверка пары площадей живёт
    # в этом же блоке и по той же причине: она судит СЛИТОЕ состояние `obj`.
    with rollback_on_domain_error(db):
        if title is not UNSET:
            obj.title = require_text(title, "Название")
        if address is not UNSET:
            obj.address = (address or "").strip()
        if rate_class_id is not UNSET:
            obj.rate_class_id = _resolve_rate_class(db, rate_class_id)
        if area_aboveground_sp is not UNSET:
            obj.area_aboveground_sp = area_aboveground_sp
        if area_underground_sp is not UNSET:
            obj.area_underground_sp = area_underground_sp
        if area_useful_sp is not UNSET:
            obj.area_useful_sp = area_useful_sp
        validate_area_pair(obj.area_aboveground_sp, obj.area_underground_sp)
        validate_useful_area(
            obj.area_aboveground_sp, obj.area_underground_sp, obj.area_useful_sp
        )
    with translating_integrity(db, _UNIQUE_MESSAGES):
        db.commit()
    log.info("object_updated id=%s", object_id)
    return get_object_dict(db, object_id)


def delete_object(db: Session, object_id: int) -> None:
    obj = db.get(ObjectModel, object_id)
    if obj is None:
        raise DomainError(404, f"Объект {object_id} не найден.")
    contracts = db.query(Contract).filter(Contract.object_id == obj.id).count()
    if contracts:
        raise DomainError(
            409,
            f"Объект «{obj.title}» удалить нельзя: на него ссылаются договоры "
            f"({contracts}). Сначала удалите или перенесите договоры.",
        )
    db.delete(obj)
    db.commit()
    log.info("object_deleted id=%s", object_id)


# ---------------------------------------------------------------------------
#  Подрядчики
# ---------------------------------------------------------------------------

def _contractor_dict(c: Contractor, contracts: int) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "inn": c.inn,
        "address": c.address,
        "accreditation": c.accreditation,
        "contracts_count": contracts,
        "created_at": iso(c.created_at),
        "updated_at": iso(c.updated_at),
    }


def _contractors_select():
    return sa.select(Contractor, _count_of(Contract.contractor_id, Contractor.id))


def list_contractors(
    db: Session, *, q: str | None = None, page: int = 1, page_size: int = 20
) -> dict:
    """Подрядчики с пагинацией. `q` — подстрока названия или БИН/ИНН."""
    page, page_size = clamp_page(page, page_size)
    stmt = _contractors_select()
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        conditions = [Contractor.title.ilike(pattern)]
        # ИНН хранится каноном (только цифры), а человек вводит его с
        # разделителями — ищем по канону запроса, иначе «77 00 12» не нашёл
        # бы сохранённое «7700123456» (спека контура §2.7).
        inn_digits = canonicalize_inn(q)
        if inn_digits:
            conditions.append(Contractor.inn.like(f"%{inn_digits}%"))
        stmt = stmt.where(sa.or_(*conditions))

    rows, total = paginated(
        db, stmt, order_by=(Contractor.title,), page=page, page_size=page_size
    )
    return {
        "items": [_contractor_dict(*row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_contractor_dict(db: Session, contractor_id: int) -> dict:
    row = db.execute(_contractors_select().where(Contractor.id == contractor_id)).first()
    if row is None:
        raise DomainError(404, f"Подрядчик {contractor_id} не найден.")
    return _contractor_dict(*row)


def create_contractor(
    db: Session,
    *,
    title: str,
    inn: str,
    address: str | None = None,
    accreditation: str | None = None,
) -> dict:
    title = require_text(title, "Название")
    inn = canonicalize_inn(require_text(inn, "БИН/ИНН"))
    if not inn:
        raise DomainError(422, "Поле «БИН/ИНН» не содержит ни одной цифры.")
    if db.query(Contractor).filter(Contractor.inn == inn).first():
        raise DomainError(409, _UNIQUE_MESSAGES["uq_contractors_inn"])

    # Формат БИН/ИНН не валидируется: разрядность отличается по юрисдикции
    # (10/12 в РФ, 12 в РК), и придумывать правило за пользователя — не задача
    # MVP. Требуется только непустота и уникальность (uq_contractors_inn).
    contractor = Contractor(
        title=title,
        inn=inn,
        address=(address or "").strip(),
        accreditation=(accreditation or "").strip(),
    )
    db.add(contractor)
    with translating_integrity(db, _UNIQUE_MESSAGES):
        db.commit()
    db.refresh(contractor)
    log.info("contractor_created id=%s inn=%s", contractor.id, contractor.inn)
    return get_contractor_dict(db, contractor.id)


def update_contractor(
    db: Session,
    contractor_id: int,
    *,
    title=UNSET,
    inn=UNSET,
    address=UNSET,
    accreditation=UNSET,
) -> dict:
    contractor = db.get(Contractor, contractor_id)
    if contractor is None:
        raise DomainError(404, f"Подрядчик {contractor_id} не найден.")
    # Пустой БИН/ИНН отвергается после того, как название уже присвоено.
    with rollback_on_domain_error(db):
        if title is not UNSET:
            contractor.title = require_text(title, "Название")
        if inn is not UNSET:
            canonical = canonicalize_inn(require_text(inn, "БИН/ИНН"))
            if not canonical:
                raise DomainError(422, "Поле «БИН/ИНН» не содержит ни одной цифры.")
            contractor.inn = canonical
        if address is not UNSET:
            contractor.address = (address or "").strip()
        if accreditation is not UNSET:
            contractor.accreditation = (accreditation or "").strip()
    with translating_integrity(db, _UNIQUE_MESSAGES):
        db.commit()
    log.info("contractor_updated id=%s", contractor_id)
    return get_contractor_dict(db, contractor_id)


def delete_contractor(db: Session, contractor_id: int) -> None:
    """Удалить подрядчика. Отказ, если он в договоре или в предложении сметы."""
    contractor = db.get(Contractor, contractor_id)
    if contractor is None:
        raise DomainError(404, f"Подрядчик {contractor_id} не найден.")
    contracts = db.query(Contract).filter(Contract.contractor_id == contractor.id).count()
    # proposals.contractor_id — тоже FK без каскада: подрядчик остаётся в уже
    # импортированных сметах даже если его договор удалён.
    proposals = db.query(Proposal).filter(Proposal.contractor_id == contractor.id).count()
    # offer_packages.contractor_id RESTRICT (спека контура §2.13): участник
    # тендера без материализованных предложений — третий потребитель, и без
    # этого счётчика удаление упало бы сырым IntegrityError.
    tenders = db.query(OfferPackage).filter(OfferPackage.contractor_id == contractor.id).count()
    if contracts or proposals or tenders:
        raise DomainError(
            409,
            f"Подрядчика «{contractor.title}» удалить нельзя: договоров ({contracts}), "
            f"предложений в сметах ({proposals}), участий в тендерах ({tenders}).",
        )
    db.delete(contractor)
    db.commit()
    log.info("contractor_deleted id=%s", contractor_id)
