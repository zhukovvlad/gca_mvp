"""CRUD нормативов расценок (AGENTS.md §4 «Нормативы», §7.3).

Норматив — ставка для пары (каталожная работа × класс объектов) на период
`[valid_from, valid_to)`. Пересечение периодов одной пары запрещено
EXCLUDE-констрейнтом `ex_rate_standards_no_overlap`; благодаря ему на дату сметы
подходит максимум одна строка, и отклонение считается однозначно (§4).

**Переутверждение — не правка ставки.** §4 требует: `UPDATE valid_to` старой
строки **плюс** `INSERT` новой, в одной транзакции; историю не мутировать. Именно
это делает `reapprove`. Если вместо него поднять ставку в существующей строке,
отклонения **прошлых** смет пересчитаются по новой ставке — то есть сломается
DoD-требование «переутверждение норматива не меняет отклонения старых смет».

**Нарушение EXCLUDE — человекочитаемый 400, а не 500** (§7.3). Ловится по имени
констрейнта, как `uq_import_jobs_active_pair` в `routers/estimates.py`.
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crud.common import DomainError, clamp_page, iso, paginated, rollback_on_domain_error
from models import CatalogKind, CatalogPosition, RateClass, RateStandard, UnitOfMeasure

log = logging.getLogger(__name__)

#: EXCLUDE USING gist по (catalog_position_id, rate_class_id, daterange). Создан
#: raw SQL в миграции 0002; имя нужно, чтобы отличить его нарушение от любого
#: другого и ответить 400 с объяснением, а не 500.
OVERLAP_CONSTRAINT = "ex_rate_standards_no_overlap"

UNSET = object()


def _overlap_error(catalog_title: str, rate_class_title: str) -> DomainError:
    return DomainError(
        400,
        f"Период пересекается с уже действующим нормативом для работы "
        f"«{catalog_title}» и класса «{rate_class_title}». На каждую дату у пары "
        "может действовать только одна ставка — иначе отклонение считалось бы "
        "по двум нормативам сразу. Закройте прежний период (переутверждение) "
        "либо выберите другие даты.",
    )


def _standard_dict(
    standard: RateStandard,
    catalog_title: str,
    unit_code: str | None,
    rate_class_title: str,
) -> dict:
    return {
        "id": standard.id,
        "catalog_position_id": standard.catalog_position_id,
        "catalog_position_title": catalog_title,
        "unit_code": unit_code,
        "rate_class_id": standard.rate_class_id,
        "rate_class_title": rate_class_title,
        "standard_unit_rate": standard.standard_unit_rate,
        "valid_from": iso(standard.valid_from),
        "valid_to": iso(standard.valid_to),
        "inflation_index": standard.inflation_index,
        "approved_by": standard.approved_by,
        "approved_at": iso(standard.approved_at),
        "note": standard.note,
        "created_at": iso(standard.created_at),
        "updated_at": iso(standard.updated_at),
    }


def _standards_select():
    return (
        sa.select(RateStandard, CatalogPosition.standard_job_title, UnitOfMeasure.code, RateClass.title)
        .join(CatalogPosition, CatalogPosition.id == RateStandard.catalog_position_id)
        .join(RateClass, RateClass.id == RateStandard.rate_class_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == CatalogPosition.unit_id)
    )


def list_rate_standards(
    db: Session,
    *,
    rate_class_id: int | None = None,
    catalog_position_id: int | None = None,
    q: str | None = None,
    on_date: dt.date | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Нормативы с фильтрами (§7.3).

    Args:
        on_date: только действующие на эту дату — `valid_from <= on_date` и
            (`valid_to` пуст либо `> on_date`). Полуинтервал `[valid_from, valid_to)`
            тот же, что в EXCLUDE и в VIEW отклонений: иначе экран показывал бы
            действующим не то, по чему считается отклонение.
    """
    page, page_size = clamp_page(page, page_size)
    stmt = _standards_select()
    if rate_class_id is not None:
        stmt = stmt.where(RateStandard.rate_class_id == rate_class_id)
    if catalog_position_id is not None:
        stmt = stmt.where(RateStandard.catalog_position_id == catalog_position_id)
    if q and q.strip():
        stmt = stmt.where(CatalogPosition.standard_job_title.ilike(f"%{q.strip()}%"))
    if on_date is not None:
        stmt = stmt.where(
            RateStandard.valid_from <= on_date,
            sa.or_(RateStandard.valid_to.is_(None), RateStandard.valid_to > on_date),
        )

    rows, total = paginated(
        db,
        stmt,
        order_by=(
            CatalogPosition.standard_job_title.asc(),
            RateClass.title.asc(),
            RateStandard.valid_from.desc(),
        ),
        page=page,
        page_size=page_size,
    )
    return {
        "items": [_standard_dict(*row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_rate_standard(db: Session, standard_id: int) -> RateStandard:
    standard = db.get(RateStandard, standard_id)
    if standard is None:
        raise DomainError(404, f"Норматив {standard_id} не найден.")
    return standard


def get_rate_standard_dict(db: Session, standard_id: int) -> dict:
    row = db.execute(_standards_select().where(RateStandard.id == standard_id)).first()
    if row is None:
        raise DomainError(404, f"Норматив {standard_id} не найден.")
    return _standard_dict(*row)


def _require_refs(
    db: Session, catalog_position_id: int, rate_class_id: int, *, require_position_kind: bool = False
) -> tuple[str, str]:
    """Проверяет работу и класс; возвращает их названия для текстов сообщений.

    Args:
        require_position_kind: требовать `kind='POSITION'`. Включается при
            **создании** норматива и выключено при правке существующего: пара
            (работа, класс) в правке не меняется, а `kind` строки правкой
            норматива не управляется.
    """
    position = db.get(CatalogPosition, catalog_position_id)
    if position is None:
        raise DomainError(404, f"Каталожная строка {catalog_position_id} не найдена.")
    if require_position_kind and position.kind != CatalogKind.POSITION.value:
        raise DomainError(
            422,
            f"Работа «{position.standard_job_title}» имеет kind={position.kind}, а норматив "
            "назначается только строке POSITION. Причин две, и обе жёсткие: VIEW отклонений "
            "берёт только POSITION, поэтому такая ставка не участвовала бы в расчёте вовсе; "
            "а норматив на строке TO_REVIEW сделал бы её неудаляемой и сорвал бы её "
            "последующее слияние в ручном матчинге. "
            "Разберите строку в очереди ручного матчинга, затем назначайте ставку.",
        )
    rate_class = db.get(RateClass, rate_class_id)
    if rate_class is None:
        raise DomainError(404, f"Класс объектов {rate_class_id} не найден.")
    return position.standard_job_title, rate_class.title


def _validate_period(valid_from: dt.date, valid_to: dt.date | None) -> None:
    """CHECK ck_rate_standards_period — понятным 422, не 500."""
    if valid_to is not None and valid_to <= valid_from:
        raise DomainError(
            422,
            f"Дата окончания ({valid_to.isoformat()}) должна быть строго позже даты "
            f"начала ({valid_from.isoformat()}): период задаётся полуинтервалом "
            "[начало, окончание).",
        )


def _validate_rate(rate: Decimal) -> None:
    """CHECK ck_rate_standards_rate_positive — понятным 422."""
    if rate <= 0:
        raise DomainError(422, "Ставка норматива должна быть больше нуля.")


def create_rate_standard(
    db: Session,
    *,
    catalog_position_id: int,
    rate_class_id: int,
    standard_unit_rate: Decimal,
    valid_from: dt.date,
    valid_to: dt.date | None = None,
    inflation_index: Decimal | None = None,
    approved_by: str | None = None,
    approved_at: dt.datetime | None = None,
    note: str | None = None,
) -> dict:
    """Создать норматив.

    422 — работа не `POSITION`, ставка неположительна или период вывернут;
    400 — пересечение периодов (EXCLUDE).
    """
    catalog_title, rate_class_title = _require_refs(
        db, catalog_position_id, rate_class_id, require_position_kind=True
    )
    _validate_rate(standard_unit_rate)
    _validate_period(valid_from, valid_to)
    if inflation_index is not None and inflation_index <= 0:
        raise DomainError(422, "Коэффициент инфляции должен быть больше нуля.")

    standard = RateStandard(
        catalog_position_id=catalog_position_id,
        rate_class_id=rate_class_id,
        standard_unit_rate=standard_unit_rate,
        valid_from=valid_from,
        valid_to=valid_to,
        inflation_index=inflation_index,
        approved_by=(approved_by or "").strip() or None,
        approved_at=approved_at,
        note=(note or "").strip() or None,
    )
    db.add(standard)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if OVERLAP_CONSTRAINT in str(exc.orig):
            raise _overlap_error(catalog_title, rate_class_title) from exc
        raise
    db.refresh(standard)
    log.info(
        "rate_standard_created id=%s position=%s class=%s from=%s",
        standard.id,
        catalog_position_id,
        rate_class_id,
        valid_from,
    )
    return get_rate_standard_dict(db, standard.id)


def update_rate_standard(
    db: Session,
    standard_id: int,
    *,
    standard_unit_rate=UNSET,
    valid_from=UNSET,
    valid_to=UNSET,
    inflation_index=UNSET,
    approved_by=UNSET,
    approved_at=UNSET,
    note=UNSET,
) -> dict:
    """Правка норматива — исправление ошибки ввода, НЕ переутверждение.

    Пара (работа, класс) не меняется: сменить её значило бы перенести историю
    ставок на другую работу. Нужна другая пара — создайте отдельный норматив.
    Для нового периода с новой ставкой есть `reapprove`.
    """
    standard = get_rate_standard(db, standard_id)
    catalog_title, rate_class_title = _require_refs(
        db, standard.catalog_position_id, standard.rate_class_id
    )

    with rollback_on_domain_error(db):
        if standard_unit_rate is not UNSET:
            _validate_rate(standard_unit_rate)
            standard.standard_unit_rate = standard_unit_rate
        if valid_from is not UNSET:
            standard.valid_from = valid_from
        if valid_to is not UNSET:
            standard.valid_to = valid_to
        # Проверка сочетания дат возможна только после присваивания — поэтому
        # отказ здесь застаёт объект уже изменённым, и его надо откатить.
        _validate_period(standard.valid_from, standard.valid_to)
        if inflation_index is not UNSET:
            if inflation_index is not None and inflation_index <= 0:
                raise DomainError(422, "Коэффициент инфляции должен быть больше нуля.")
            standard.inflation_index = inflation_index
        if approved_by is not UNSET:
            standard.approved_by = (approved_by or "").strip() or None
        if approved_at is not UNSET:
            standard.approved_at = approved_at
        if note is not UNSET:
            standard.note = (note or "").strip() or None

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if OVERLAP_CONSTRAINT in str(exc.orig):
            raise _overlap_error(catalog_title, rate_class_title) from exc
        raise
    log.info("rate_standard_updated id=%s", standard_id)
    return get_rate_standard_dict(db, standard_id)


def reapprove_rate_standard(
    db: Session,
    standard_id: int,
    *,
    valid_from: dt.date,
    standard_unit_rate: Decimal | None = None,
    inflation_index: Decimal | None = None,
    approved_by: str | None = None,
    approved_at: dt.datetime | None = None,
    note: str | None = None,
) -> dict:
    """Переутвердить норматив: закрыть прежний период и открыть новый (§4).

    Одна транзакция, две операции: `UPDATE valid_to` старой строки датой
    `valid_from` новой — и `INSERT` новой. Историю не мутируем: прежняя ставка
    остаётся на своём периоде, поэтому отклонения смет, датированных до
    переутверждения, не меняются.

    Прежний период закрывается **ровно** датой начала нового: полуинтервал
    `[valid_from, valid_to)` не оставляет ни зазора (дата без норматива), ни
    пересечения (две ставки на одну дату).

    Args:
        valid_from: дата начала новой ставки — она же дата закрытия прежней.
        standard_unit_rate: новая ставка. Не передана — считается как
            `прежняя × inflation_index`.
        inflation_index: коэффициент индексации. Сохраняется в новой строке как
            обоснование, даже если ставка задана явно.

    Raises:
        DomainError 422: не задана ни ставка, ни коэффициент; дата не позже начала
            прежнего периода; прежний период уже закрыт.
    """
    old = get_rate_standard(db, standard_id)
    catalog_title, rate_class_title = _require_refs(
        db, old.catalog_position_id, old.rate_class_id
    )

    if valid_from <= old.valid_from:
        raise DomainError(
            422,
            f"Новый период должен начинаться позже {old.valid_from.isoformat()} — "
            "иначе прежний период стал бы пустым, а его история потерялась бы.",
        )
    # Закрытый период не переутверждается ВООБЩЕ, независимо от новой даты.
    # Прежняя проверка (`valid_from >= old.valid_to`) была неполной и пропускала
    # дату ВНУТРИ закрытого периода: [2025-01-01, 2025-07-01) + valid_from
    # 2025-03-01 давало «успех», прежний период укорачивался до 2025-03-01, а
    # новый становился бессрочным. То есть у смет, датированных мартом–июнем,
    # отклонения молча пересчитывались по новой ставке — ровно то, что запрещает
    # §4 и DoD «переутверждение не меняет отклонения старых смет».
    if old.valid_to is not None:
        raise DomainError(
            422,
            f"Норматив закрыт {old.valid_to.isoformat()} и переутверждению не подлежит: "
            "переутверждают действующую ставку, а закрытый период — уже история, и "
            "менять его значило бы задним числом изменить отклонения смет того времени. "
            "Если нужна ставка на другой период — создайте отдельный норматив.",
        )

    if standard_unit_rate is None:
        if inflation_index is None:
            raise DomainError(
                422,
                "Укажите новую ставку либо коэффициент инфляции: без одного из них "
                "новую ставку взять неоткуда.",
            )
        if inflation_index <= 0:
            raise DomainError(422, "Коэффициент инфляции должен быть больше нуля.")
        # Точное произведение, без округления: округление — дело слоя
        # представления (§4 требует того же для deviation_pct). Округли мы здесь,
        # и в БД легла бы ставка, которой никто не утверждал.
        standard_unit_rate = old.standard_unit_rate * inflation_index
    _validate_rate(standard_unit_rate)
    if inflation_index is not None and inflation_index <= 0:
        raise DomainError(422, "Коэффициент инфляции должен быть больше нуля.")

    fresh = RateStandard(
        catalog_position_id=old.catalog_position_id,
        rate_class_id=old.rate_class_id,
        standard_unit_rate=standard_unit_rate,
        valid_from=valid_from,
        valid_to=None,
        inflation_index=inflation_index,
        approved_by=(approved_by or "").strip() or None,
        approved_at=approved_at,
        note=(note or "").strip() or None,
    )

    try:
        # ЗАЩИТНАЯ МЕРА: порядок операторов задан явно, двумя flush-ами.
        #
        # Порядок здесь существен. Пока прежняя строка открыта (`valid_to IS NULL`),
        # её период бесконечен, и EXCLUDE отверг бы любую новую строку той же пары;
        # констрейнт не DEFERRABLE, поэтому отложить проверку до конца транзакции
        # нельзя. Значит закрыть прежний период надо ДО вставки новой строки.
        #
        # Замер показал, что в одном flush SQLAlchemy и так делает это в нужном
        # порядке (`UPDATE rate_standards`, затем `INSERT rate_standards`), то есть
        # одного flush хватило бы и дефекта здесь нет. Но этот порядок — деталь
        # реализации unit of work, а не наш инвариант: он не обещан документацией и
        # изменился бы, окажись строки разных мапперов или добавь кто-то в эту же
        # транзакцию третью запись. Цена явной границы — один лишний round-trip на
        # редкую операцию; цена неявной — отказ EXCLUDE на совершенно корректных
        # данных, который придётся объяснять с нуля.
        old.valid_to = valid_from
        db.flush()          # закрыли прежний период
        db.add(fresh)
        db.flush()          # и только теперь открыли новый
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if OVERLAP_CONSTRAINT in str(exc.orig):
            raise _overlap_error(catalog_title, rate_class_title) from exc
        raise

    db.refresh(fresh)
    log.info(
        "rate_standard_reapproved old=%s new=%s from=%s",
        standard_id,
        fresh.id,
        valid_from,
    )
    return {
        "previous": get_rate_standard_dict(db, standard_id),
        "current": get_rate_standard_dict(db, fresh.id),
    }


def delete_rate_standard(db: Session, standard_id: int) -> None:
    """Удалить норматив.

    Отказа по ссылкам нет: на `rate_standards` не ссылается ничего — отклонения
    считаются VIEW-ом по датам (§4), а не хранимой связью. Удаление уместно для
    ошибочно введённой строки; штатное изменение ставки — `reapprove`.
    """
    standard = get_rate_standard(db, standard_id)
    db.delete(standard)
    db.commit()
    log.info("rate_standard_deleted id=%s", standard_id)
