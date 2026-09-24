"""Жизненный цикл семей работ: создание, правка, активация и загрузка
идемпотентного seed-файла (спека `2026-09-22-catalog-families-design.md`
§1.14, §2.3, §2.7, §2.14; план, задача 7).

`draft -> active -> archived`. Активация требует непустого определения —
это держит `CHECK` схемы (`CK_FAMILY_ACTIVE_NEEDS_DEFINITION`), но проверка
здесь идёт ПЕРВОЙ ЛИНИЕЙ, в Python, ДО `flush`: отказ обязан быть
человекочитаемым `WorkFamilyError`, а не сырым `IntegrityError`. `CHECK`
остаётся ВТОРОЙ линией — прямая правка строки в обход этого модуля
по-прежнему не может создать активную семью без определения
(`test_work_families.py`, класс проверок CHECK).

Пустое/пробельное определение считается отсутствующим — так же, как и
`NULL` (то же самое, что делает `CHECK`, чьё выражение —
`definition IS NOT NULL AND btrim(definition) <> ''`): без этого правила
активация с определением из одних пробелов проходила бы код и падала бы
только на базе с непонятным `IntegrityError`.

Единица семьи (`unit_id`) резолвится из текста ЧЕРЕЗ `UnitResolver`
(`services/unit_resolution.py`) — как при импорте, тем же самым способом:
идентичность единицы не идентификатор строки `units_of_measurement`
(разный на разных базах), а её каноническое имя (`code`).

Коды отказов сверх плана (задача 7 заводит только `WorkFamilyError` и
`REFUSE_ACTIVATE_WITHOUT_DEFINITION` по имени из плана; остальные —
сверх плана):
`REFUSE_FAMILY_NOT_FOUND`, `REFUSE_UPDATE_ARCHIVED` (правка архивной семьи),
`REFUSE_ACTIVATE_NOT_DRAFT` (активация не из `draft`), `REFUSE_UNKNOWN_UNIT`
(ручное создание с единицей, которую `UnitResolver` не резолвит — семья без
единицы вообще допустима, `unit_name=None`, а вот НАЗВАННАЯ, но неизвестная
единица — молчаливая потеря идентичности, которую этот модуль не допускает).

Задача 8 (план, «Task 8: привязка семьи к контексту, слияние и
архивирование семей»; спека §2.3, §2.5, §2.7–§2.8, §2.14) дописывает
`assign_family`, `set_unit`, `archive_family`, `merge_families`,
`confirm_kind`, `unconfirm_kind`, `set_name_role` — шесть `REFUSE_*` из
плана (`REFUSE_UNIT_MISMATCH`, `REFUSE_FAMILY_NOT_ACTIVE`,
`REFUSE_UNIT_CHANGE_WITH_LINKS`, `REFUSE_ARCHIVE_WITH_LINKS`,
`REFUSE_MERGE_UNIT_MISMATCH`, `REFUSE_MERGE_INACTIVE`) плюс коды сверх
плана: `REFUSE_CONTEXT_NOT_FOUND`, `REFUSE_CONTEXT_NOT_APPLICABLE`
(вид/подтверждение вида на `NOT_APPLICABLE`-контексте — своя причина у
`confirm_kind`/`unconfirm_kind`, а не пересечение с шестью плановыми),
`REFUSE_MERGE_SAME_FAMILY`, `REFUSE_INVALID_KIND`, `REFUSE_INVALID_NAME_ROLE`.

Порядок блокировок «семья раньше контекста»:
`assign_family` берёт `FOR SHARE` на назначаемую семью (прежняя
семья контекста не блокируется — она не меняется и не проверяется) ДО
`FOR UPDATE` на контекст; `merge_families` берёт `FOR UPDATE` на обе семьи
по возрастанию `id`, затем `FOR UPDATE` на переводимые контексты, тоже по
`id`. `archive_family`/`set_unit` берут `FOR UPDATE` только на саму семью.
`confirm_kind`/`unconfirm_kind`/`set_name_role` берут `FOR UPDATE` на
строку контекста — гонок по ним план не утверждает, блокировка нужна ради
перечитывания состояния. Перечитывание — тот же приём задачи 6:
`db.expire_all()` сразу после лока, потому что identity map SQLAlchemy не
обновляет уже загруженный Python-объект данными нового `SELECT`
(докстрока `services/context_operations.py`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

import services.context_routing as context_routing_module
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    DecisionSource,
    FamilySource,
    FamilyStatus,
    NameRole,
    SemanticKind,
    SemanticState,
    WorkFamily,
)
from services.semantic_events import record_event
from services.semantic_rules import PLACE_DICTIONARY_VERSION, classify_kind
from services.unit_resolution import NO_UNIT_NORM, UnitResolver

#: `backend/seeds/work_families_initial.json` (план, задача 7, Interfaces).
SEED_PATH: Path = Path(__file__).resolve().parent.parent / "seeds" / "work_families_initial.json"


class WorkFamilyError(Exception):
    """Отказ операции над семьёй: `code` — машиночитаемая строка (задача 12
    превратит её в доменный отказ через `raise_domain_error`, тот же приём,
    что `ContextOperationError` задачи 6), контекст отказа доступен
    именованными атрибутами (**context), а не только текстом сообщения."""

    def __init__(self, code: str, message: str, **context: object) -> None:
        super().__init__(message)
        self.code = code
        for key, value in context.items():
            setattr(self, key, value)


#: Имя из плана (Task 7, Interfaces) — активация без определения.
REFUSE_ACTIVATE_WITHOUT_DEFINITION = "activate_without_definition"

#: Коды сверх плана (см. докстроку модуля).
REFUSE_FAMILY_NOT_FOUND = "family_not_found"
REFUSE_UPDATE_ARCHIVED = "update_archived"
REFUSE_ACTIVATE_NOT_DRAFT = "activate_not_draft"
REFUSE_UNKNOWN_UNIT = "unknown_unit"

#: Шесть имён из плана (Task 8, Interfaces).
REFUSE_UNIT_MISMATCH = "unit_mismatch"
REFUSE_FAMILY_NOT_ACTIVE = "family_not_active"
REFUSE_UNIT_CHANGE_WITH_LINKS = "unit_change_with_links"
REFUSE_ARCHIVE_WITH_LINKS = "archive_with_links"
REFUSE_MERGE_UNIT_MISMATCH = "merge_unit_mismatch"
#: Один код на ТРИ домена входа (цель `draft`, цель `archived`, источник не
#: `active`) — план запрещает «один общий отказ» на уровне ВХОДОВ теста
#: («по входу на каждое из трёх состояний»), а не на уровне кода: Interfaces
#: задачи 8 заводит РОВНО одну константу. Различают домены атрибуты отказа
#: (`role='source'|'target'`, `status`), не отдельные коды.
REFUSE_MERGE_INACTIVE = "merge_inactive"

#: Коды сверх плана — задача 8.
REFUSE_CONTEXT_NOT_FOUND = "context_not_found"
#: `confirm_kind`/`unconfirm_kind` на `NOT_APPLICABLE`-контексте: решения о
#: виде для строк-разделов/мусора бессмысленны (спека §2.5) — своя причина,
#: не пересекается ни с одним из шести кодов плана.
REFUSE_CONTEXT_NOT_APPLICABLE = "context_not_applicable"
REFUSE_MERGE_SAME_FAMILY = "merge_same_family"
#: `confirm_kind(kind=...)` с переопределением вне `SemanticKind` — первая
#: линия защиты в Python, до `CHECK` (та же дисциплина, что и остальные
#: домены модуля).
REFUSE_INVALID_KIND = "invalid_kind"
REFUSE_INVALID_NAME_ROLE = "invalid_name_role"

#: Белые списки допустимых значений — сверка ПЕРЕД записью в базу, не после
#: отказа `CHECK` (докстрока модуля, задача 7, «первая линия защиты»).
_SEMANTIC_KIND_VALUES: frozenset[str] = frozenset(member.value for member in SemanticKind)
_NAME_ROLE_VALUES: frozenset[str] = frozenset(member.value for member in NameRole)


@dataclass(frozen=True)
class SeedReport:
    created: int
    skipped_existing: int
    with_definition: int


def _now() -> datetime:
    return datetime.now(UTC)


def _has_definition(value: str | None) -> bool:
    """Непустое, непробельное определение — то же правило, что и `CHECK`
    `CK_FAMILY_ACTIVE_NEEDS_DEFINITION` (`definition IS NOT NULL AND
    btrim(definition) <> ''`), выполненное в Python до обращения к базе."""
    return value is not None and value.strip() != ""


def _normalize_definition(value: str | None) -> str | None:
    """Пустая/пробельная строка хранится как `NULL` — семья без определения
    не отличается тем, ПУСТУЮ строку ей приписали или вовсе ничего не
    приписывали."""
    return value if _has_definition(value) else None


def create_family(
    db: Session,
    *,
    title: str,
    unit_name: str | None,
    definition: str | None,
    actor_id: int,
) -> WorkFamily:
    """Заводит семью вручную: `created_by=actor_id`, `seed_key=NULL`,
    `status='draft'`, `family_created` с `origin='operator'` (план, задача 7).

    Raises:
        WorkFamilyError: `unit_name` задан, но `UnitResolver` не резолвит его
            (`REFUSE_UNKNOWN_UNIT`) — семья БЕЗ единицы (`unit_name=None`)
            допустима, а вот названная неизвестная единица молча потеряла бы
            идентичность (докстрока `services/unit_resolution.py`).
    """
    resolver = UnitResolver(db)
    resolved = resolver.resolve(unit_name)
    if resolved.is_unknown:
        raise WorkFamilyError(
            REFUSE_UNKNOWN_UNIT,
            f"единица {unit_name!r} не резолвится справочником единиц",
            unit_name=unit_name,
        )

    family = WorkFamily(
        seed_key=None,
        title=title,
        unit_id=resolved.unit_id,
        definition=_normalize_definition(definition),
        status=FamilyStatus.draft.value,
        created_by=actor_id,
    )
    db.add(family)
    db.flush()

    record_event(
        db,
        event_type="family_created",
        family_id=family.id,
        actor_id=actor_id,
        payload={"title": family.title, "unit": resolved.unit_norm, "origin": "operator"},
    )
    return family


def update_family(
    db: Session,
    *,
    family_id: int,
    title: str | None,
    definition: str | None,
    actor_id: int,
) -> WorkFamily:
    """Правит имя и/или определение семьи. `None` у параметра значит «не
    трогать это поле» — вызывающий передаёт только то, что реально меняется
    (план, задача 7: `changed` только по реально
    изменившимся полям, пустой аудит запрещён спекой §2.14).

    Допустима в любом статусе, КРОМЕ `archived`.

    Raises:
        WorkFamilyError: семья не найдена (`REFUSE_FAMILY_NOT_FOUND`); семья
            архивирована (`REFUSE_UPDATE_ARCHIVED`).
    """
    family = db.get(WorkFamily, family_id)
    if family is None:
        raise WorkFamilyError(
            REFUSE_FAMILY_NOT_FOUND, f"семья {family_id} не найдена", family_id=family_id
        )
    if family.status == FamilyStatus.archived.value:
        raise WorkFamilyError(
            REFUSE_UPDATE_ARCHIVED,
            f"семья {family_id} архивирована — правка недоступна",
            family_id=family_id,
        )

    changed: list[dict[str, object]] = []
    if title is not None and title != family.title:
        changed.append({"field": "title", "from": family.title, "to": title})
        family.title = title
    if definition is not None:
        normalized = _normalize_definition(definition)
        if normalized != family.definition:
            changed.append({"field": "definition", "from": family.definition, "to": normalized})
            family.definition = normalized

    if not changed:
        # Пустой аудит запрещён спекой §2.14 — событие не пишется вовсе, а
        # не пишется с `changed=[]` (последнее и не прошло бы валидатор
        # `record_event`: `changed` обязан быть непустым списком).
        return family

    db.flush()
    record_event(
        db,
        event_type="family_updated",
        family_id=family.id,
        actor_id=actor_id,
        payload={"changed": changed},
    )
    return family


def activate_family(db: Session, *, family_id: int, actor_id: int) -> WorkFamily:
    """Переводит семью `draft -> active`, заполняя пару
    `activated_by`/`activated_at` целиком (план, задача 7).

    Порядок проверок: семья найдена → статус `draft` → определение непусто.
    Все три — ПЕРВОЙ ЛИНИЕЙ в Python, до `db.flush()`: ни один из трёх
    отказов не мутирует сессию. `CHECK` схемы
    (`CK_FAMILY_ACTIVE_NEEDS_DEFINITION`) — ВТОРАЯ линия, независимая от
    этой функции (прямой `UPDATE` в обход неё по-прежнему получает
    `IntegrityError`, `test_work_families.py`).

    Raises:
        WorkFamilyError: семья не найдена (`REFUSE_FAMILY_NOT_FOUND`); семья
            не в статусе `draft` (`REFUSE_ACTIVATE_NOT_DRAFT`, покрывает и
            `active`, и `archived` — при трёх статусах всего второй код не
            нужен); определение пусто/пробельно/`NULL`
            (`REFUSE_ACTIVATE_WITHOUT_DEFINITION`).
    """
    family = db.get(WorkFamily, family_id)
    if family is None:
        raise WorkFamilyError(
            REFUSE_FAMILY_NOT_FOUND, f"семья {family_id} не найдена", family_id=family_id
        )
    if family.status != FamilyStatus.draft.value:
        raise WorkFamilyError(
            REFUSE_ACTIVATE_NOT_DRAFT,
            f"семья {family_id} не в статусе draft (текущий статус: {family.status})",
            family_id=family_id,
            status=family.status,
        )
    if not _has_definition(family.definition):
        raise WorkFamilyError(
            REFUSE_ACTIVATE_WITHOUT_DEFINITION,
            f"семья {family_id} не может быть активирована без определения",
            family_id=family_id,
        )

    family.status = FamilyStatus.active.value
    family.activated_by = actor_id
    family.activated_at = _now()
    db.flush()

    unit_norm = family.unit.code if family.unit_id is not None else NO_UNIT_NORM
    record_event(
        db,
        event_type="family_activated",
        family_id=family.id,
        actor_id=actor_id,
        payload={"title": family.title, "unit": unit_norm},
    )
    return family


def load_seed(db: Session, *, path: Path = SEED_PATH) -> SeedReport:
    """Загружает `path` идемпотентно: совпадение при повторном запуске ищется
    ТОЛЬКО по `seed_key` (план, задача 7) — найдена
    запись — ни одно её поле не трогается (ни имя, ни определение, ни
    статус, ни единица): «не трогает правок пользователя». Поиск по паре
    «имя × единица» здесь не участвует вовсе: после переименования он завёл
    бы второй черновик (спека §2.7, `test_work_families.py` показывает это
    отдельным входом-свидетелем).

    `with_definition` — число записей ФАЙЛА с непустым определением: это
    утверждение о содержимом файла, а не о базе, поэтому оно одинаково и на
    первом, и на повторном запуске.

    Raises:
        WorkFamilyError: единица хотя бы одной записи не резолвится
            `UnitResolver` (`REFUSE_UNKNOWN_UNIT`, контекст — `seed_key` и
            `unit_name`). Проверяются ВСЕ записи файла ДО первой вставки —
            отказ на записи №K не должен оставить в базе записи №1..K-1: та
            же дисциплина «молчаливая потеря идентичности недопустима», что
            и в `create_family` (докстрока модуля).
    """
    records: list[dict[str, object]] = json.loads(Path(path).read_bytes().decode("utf-8"))

    resolver = UnitResolver(db)

    # Резолвим и проверяем единицы ВСЕХ записей ОДНИМ проходом до первой
    # записи в базу (см. Raises выше) — resolve() не трогает базу (карта
    # единиц уже построена в UnitResolver.__init__), поэтому этот проход не
    # производит побочных эффектов, которые пришлось бы откатывать.
    resolved_by_seed_key = {}
    for record in records:
        resolved = resolver.resolve(record["unit"])
        if resolved.is_unknown:
            raise WorkFamilyError(
                REFUSE_UNKNOWN_UNIT,
                f"seed {record['seed_key']!r}: единица {record['unit']!r} не "
                "резолвится справочником единиц",
                seed_key=record["seed_key"],
                unit_name=record["unit"],
            )
        resolved_by_seed_key[record["seed_key"]] = resolved

    created = 0
    skipped_existing = 0
    with_definition = sum(1 for record in records if _has_definition(record["definition"]))

    for record in records:
        seed_key = record["seed_key"]
        existing = db.execute(
            sa.select(WorkFamily.id).where(WorkFamily.seed_key == seed_key)
        ).scalar_one_or_none()
        if existing is not None:
            skipped_existing += 1
            continue

        resolved = resolved_by_seed_key[seed_key]
        family = WorkFamily(
            seed_key=seed_key,
            title=record["title"],
            unit_id=resolved.unit_id,
            # Пустое/пробельное определение — NULL, тем же правилом, что и
            # create_family/update_family.
            definition=_normalize_definition(record["definition"]),
            status=FamilyStatus.draft.value,
            created_by=None,
        )
        db.add(family)
        db.flush()
        record_event(
            db,
            event_type="family_created",
            family_id=family.id,
            actor_id=None,
            payload={"title": family.title, "unit": resolved.unit_norm, "origin": "seed"},
        )
        created += 1

    return SeedReport(created=created, skipped_existing=skipped_existing, with_definition=with_definition)


# ---------------------------------------------------------------------------
#  Задача 8: назначение семьи контексту, слияние и архивирование
#  (спека §2.3, §2.5, §2.7–§2.8, §2.14; план, задача 8)
# ---------------------------------------------------------------------------


def _lock_families_statement(family_ids: list[int], *, exclusive: bool):
    """Отдельно от исполнения — тест компилирует запрос в SQL и проверяет
    РЕЖИМ (`FOR SHARE` vs `FOR UPDATE`), а не намерение вызова
    (`docs/pitfalls/db.md`). `ORDER BY id` — порядок захвата по возрастанию
    (спека §2.8): единый порядок делает встречную блокировку невозможной по
    построению у `merge_families` (две семьи разом)."""
    stmt = sa.select(WorkFamily.id).where(WorkFamily.id.in_(family_ids)).order_by(WorkFamily.id)
    return stmt.with_for_update() if exclusive else stmt.with_for_update(read=True)


def _lock_families(db: Session, family_ids: list[int], *, exclusive: bool) -> None:
    """Блокирует существующие семьи по возрастанию `id`. `exclusive=False` —
    `FOR SHARE` (`assign_family`, совместима сама с собой — параллельные
    назначения друг другу не мешают). `exclusive=True` — `FOR UPDATE`
    (`set_unit`, `archive_family`, `merge_families`). Пустой список — no-op."""
    if not family_ids:
        return
    db.execute(_lock_families_statement(family_ids, exclusive=exclusive)).all()


def _lock_contexts_statement(context_ids: list[int]):
    """Контексты в этом модуле блокируются ТОЛЬКО `FOR UPDATE` — ни одна
    операция задачи 8 не читает контекст на общих правах."""
    return (
        sa.select(CatalogContext.id)
        .where(CatalogContext.id.in_(context_ids))
        .order_by(CatalogContext.id)
        .with_for_update()
    )


def _lock_contexts(db: Session, context_ids: list[int]) -> None:
    if not context_ids:
        return
    db.execute(_lock_contexts_statement(context_ids)).all()


def assign_family(
    db: Session, *, context_id: int, family_id: int | None, actor_id: int
) -> CatalogContext:
    """Назначает семью контексту либо снимает её (`family_id=None`) — спека
    §2.7 «Назначение семьи контексту».

    Порядок блокировок «семья раньше контекста»:
    `family_id` задан — `FOR SHARE` на НАЗНАЧАЕМУЮ семью (прежняя семья
    контекста, если была, НЕ блокируется — она не меняется и не проверяется),
    затем `FOR UPDATE` на контекст. Это и есть протокол, которым архивирование
    (`FOR UPDATE` на семью) задерживает назначение и наоборот — гонка
    «назначение против архивирования» (спека §2.7, «Архивирование семьи»).

    Raises:
        WorkFamilyError: контекст не найден (`REFUSE_CONTEXT_NOT_FOUND`);
            семья не найдена (`REFUSE_FAMILY_NOT_FOUND`); семья не `active`,
            ПЕРЕЧИТАННОЕ после лока (`REFUSE_FAMILY_NOT_ACTIVE`, называет
            статус); единица семьи не совпадает с единицей каталожной строки
            контекста, прямым сравнением `unit_id` (`REFUSE_UNIT_MISMATCH`,
            называет ОБА значения).
    """
    context = db.get(CatalogContext, context_id)
    if context is None:
        raise WorkFamilyError(
            REFUSE_CONTEXT_NOT_FOUND, f"контекст {context_id} не найден", context_id=context_id
        )

    if family_id is not None:
        family = db.get(WorkFamily, family_id)
        if family is None:
            raise WorkFamilyError(
                REFUSE_FAMILY_NOT_FOUND, f"семья {family_id} не найдена", family_id=family_id
            )
        _lock_families(db, [family_id], exclusive=False)  # FOR SHARE — раньше контекста
    _lock_contexts(db, [context_id])  # FOR UPDATE
    db.expire_all()  # см. докстроку модуля — иначе следующий db.get вернёт кэш

    context = db.get(CatalogContext, context_id)
    old_family_id = context.work_family_id
    now = _now()

    if family_id is None:
        context.work_family_id = None
        context.family_source = None
        context.family_by = None
        context.family_at = None
    else:
        family = db.get(WorkFamily, family_id)  # ПЕРЕЧИТАННОЕ после лока
        if family.status != FamilyStatus.active.value:
            raise WorkFamilyError(
                REFUSE_FAMILY_NOT_ACTIVE,
                f"семья {family_id} не активна (статус: {family.status})",
                family_id=family_id,
                status=family.status,
            )
        bucket = db.get(ContextBucket, context.bucket_id)
        catalog_position = db.get(CatalogPosition, bucket.catalog_position_id)
        family_unit_id = family.unit_id
        context_unit_id = catalog_position.unit_id
        # Прямое сравнение, БЕЗ сентинела COALESCE(unit_id,-1): это Python,
        # где `None == None` уже `True` (в отличие от SQL, где `NULL = NULL`
        # даёт `NULL`, а не `TRUE`, — ровно то, ради чего сентинел нужен на
        # уровне БД/SQL, `docs/pitfalls/db.md`, но не внутри интерпретатора).
        if family_unit_id != context_unit_id:
            raise WorkFamilyError(
                REFUSE_UNIT_MISMATCH,
                f"единица семьи {family_unit_id!r} не совпадает с единицей "
                f"контекста {context_unit_id!r}",
                family_unit_id=family_unit_id,
                context_unit_id=context_unit_id,
            )
        context.work_family_id = family_id
        context.family_source = FamilySource.manual.value
        context.family_by = actor_id
        context.family_at = now

    db.flush()
    record_event(
        db,
        event_type="context_family_assigned",
        context_id=context_id,
        actor_id=actor_id,
        payload={
            "from_family_id": old_family_id,
            "to_family_id": family_id,
            "source": FamilySource.manual.value,
        },
    )
    return context


def set_unit(db: Session, *, family_id: int, unit_name: str | None, actor_id: int) -> WorkFamily:
    """Правит единицу семьи — запрещено при живых привязках (спека §2.7,
    «Единица семьи после привязки»): проверка держит инвариант «единица
    контекста = единица семьи», проверяемый при назначении, от порчи задним
    числом.

    `FOR UPDATE` на семью, перечитывание числа привязок ПОСЛЕ лока.

    Raises:
        WorkFamilyError: семья не найдена (`REFUSE_FAMILY_NOT_FOUND`); есть
            хотя бы один привязанный контекст, перечитанное
            (`REFUSE_UNIT_CHANGE_WITH_LINKS`, называет число); `unit_name`
            задан, но `UnitResolver` не резолвит его (`REFUSE_UNKNOWN_UNIT`).
    """
    family = db.get(WorkFamily, family_id)
    if family is None:
        raise WorkFamilyError(
            REFUSE_FAMILY_NOT_FOUND, f"семья {family_id} не найдена", family_id=family_id
        )

    _lock_families(db, [family_id], exclusive=True)  # FOR UPDATE
    db.expire_all()

    family = db.get(WorkFamily, family_id)  # ПЕРЕЧИТАННОЕ после лока
    link_count = db.execute(
        sa.select(sa.func.count())
        .select_from(CatalogContext)
        .where(CatalogContext.work_family_id == family_id)
    ).scalar_one()
    if link_count > 0:
        raise WorkFamilyError(
            REFUSE_UNIT_CHANGE_WITH_LINKS,
            f"у семьи {family_id} есть привязанные контексты: {link_count}",
            family_id=family_id,
            count=link_count,
        )

    resolver = UnitResolver(db)
    resolved = resolver.resolve(unit_name)
    if resolved.is_unknown:
        raise WorkFamilyError(
            REFUSE_UNKNOWN_UNIT,
            f"единица {unit_name!r} не резолвится справочником единиц",
            unit_name=unit_name,
        )

    old_unit_id = family.unit_id
    family.unit_id = resolved.unit_id
    db.flush()
    if old_unit_id != resolved.unit_id:
        # Пустой аудит запрещён спекой §2.14 — событие пишется, только если
        # значение реально изменилось (тот же приём, что `update_family`).
        record_event(
            db,
            event_type="family_updated",
            family_id=family_id,
            actor_id=actor_id,
            payload={"changed": [{"field": "unit_id", "from": old_unit_id, "to": resolved.unit_id}]},
        )
    return family


def archive_family(db: Session, *, family_id: int, actor_id: int) -> WorkFamily:
    """Архивирует семью — запрещено при живых привязках (спека §2.7,
    «Архивирование семьи»): без этого запрета архивирование дало бы чёрным
    ходом состояние, которое парадная дверь (назначение требует `active`) не
    пускает.

    `FOR UPDATE` на семью, перечитывание числа привязок ПОСЛЕ лока — это и
    держит гонку «назначение против архивирования» (`assign_family` берёт
    `FOR SHARE` на семью раньше контекста, спека §2.7).

    Raises:
        WorkFamilyError: семья не найдена (`REFUSE_FAMILY_NOT_FOUND`); семья
            уже архивирована, перечитанное (`REFUSE_FAMILY_NOT_ACTIVE`,
            называет статус `archived`:
            повторное архивирование иначе молча переустановило бы
            `archived_at` и записало бы второе `family_archived`); есть хотя
            бы один привязанный контекст, перечитанное
            (`REFUSE_ARCHIVE_WITH_LINKS`, называет число).
    """
    family = db.get(WorkFamily, family_id)
    if family is None:
        raise WorkFamilyError(
            REFUSE_FAMILY_NOT_FOUND, f"семья {family_id} не найдена", family_id=family_id
        )

    _lock_families(db, [family_id], exclusive=True)  # FOR UPDATE
    db.expire_all()

    family = db.get(WorkFamily, family_id)  # ПЕРЕЧИТАННОЕ после лока
    if family.status == FamilyStatus.archived.value:
        raise WorkFamilyError(
            REFUSE_FAMILY_NOT_ACTIVE,
            f"семья {family_id} уже архивирована",
            family_id=family_id,
            status=family.status,
        )
    link_count = db.execute(
        sa.select(sa.func.count())
        .select_from(CatalogContext)
        .where(CatalogContext.work_family_id == family_id)
    ).scalar_one()
    if link_count > 0:
        raise WorkFamilyError(
            REFUSE_ARCHIVE_WITH_LINKS,
            f"у семьи {family_id} есть привязанные контексты: {link_count}",
            family_id=family_id,
            count=link_count,
        )

    family.status = FamilyStatus.archived.value
    family.archived_at = _now()
    db.flush()
    record_event(
        db,
        event_type="family_archived",
        family_id=family_id,
        actor_id=actor_id,
        payload={"reason": "operator"},
    )
    return family


def merge_families(
    db: Session, *, source_family_id: int, target_family_id: int, actor_id: int
) -> int:
    """Сливает `source_family_id` в `target_family_id` (спека §2.7,
    «Слияние семей»): ссылки контекстов переезжают на цель (тройка
    происхождения контекста НЕ трогается), источник архивируется, имя и
    определение цели не меняются.

    Блокировки — единый порядок «семья, затем контекст» (спека §2.8):
    `FOR UPDATE` на ОБЕ семьи по возрастанию `id` (спасает от дедлока двух
    встречных слияний), затем `FOR UPDATE` на переводимые контексты, тоже по
    `id` (спасает от дедлока «слияние держит семью и ждёт контекст ↔
    назначение держит контекст и ждёт семью» — общий порядок «семья раньше
    контекста» с `assign_family`). После лока — перечитывание статусов и
    единиц: проигравший гонку слияния видит уже архивированный источник либо
    цель и отказывает, а не выполняет слияние поверх законченного.

    Raises:
        WorkFamilyError: источник и цель совпадают (`REFUSE_MERGE_SAME_FAMILY`);
            семья не найдена (`REFUSE_FAMILY_NOT_FOUND`); единицы не совпадают
            (прямое сравнение `unit_id`), перечитанное
            (`REFUSE_MERGE_UNIT_MISMATCH`, называет ОБЕ единицы); цель не
            `active` либо источник не `active`, перечитанное
            (`REFUSE_MERGE_INACTIVE`, называет `role` — `source`/`target` — и
            статус).
    """
    if source_family_id == target_family_id:
        raise WorkFamilyError(
            REFUSE_MERGE_SAME_FAMILY,
            f"источник и цель слияния совпадают: {source_family_id}",
            family_id=source_family_id,
        )

    source = db.get(WorkFamily, source_family_id)
    if source is None:
        raise WorkFamilyError(
            REFUSE_FAMILY_NOT_FOUND,
            f"семья {source_family_id} не найдена",
            family_id=source_family_id,
        )
    target = db.get(WorkFamily, target_family_id)
    if target is None:
        raise WorkFamilyError(
            REFUSE_FAMILY_NOT_FOUND,
            f"семья {target_family_id} не найдена",
            family_id=target_family_id,
        )

    ordered_ids = sorted([source_family_id, target_family_id])
    _lock_families(db, ordered_ids, exclusive=True)  # FOR UPDATE, по возрастанию id
    db.expire_all()

    source = db.get(WorkFamily, source_family_id)  # ПЕРЕЧИТАННОЕ после лока
    target = db.get(WorkFamily, target_family_id)

    source_unit_id = source.unit_id
    target_unit_id = target.unit_id
    # Прямое сравнение (см. комментарий в assign_family) — None == None уже
    # True в Python, сентинел COALESCE(unit_id,-1) здесь не нужен.
    if source_unit_id != target_unit_id:
        raise WorkFamilyError(
            REFUSE_MERGE_UNIT_MISMATCH,
            f"единицы семей не совпадают: источник {source_unit_id!r}, "
            f"цель {target_unit_id!r}",
            source_unit_id=source_unit_id,
            target_unit_id=target_unit_id,
        )
    if target.status != FamilyStatus.active.value:
        raise WorkFamilyError(
            REFUSE_MERGE_INACTIVE,
            f"цель слияния {target_family_id} не активна (статус: {target.status})",
            family_id=target_family_id,
            role="target",
            status=target.status,
        )
    if source.status != FamilyStatus.active.value:
        raise WorkFamilyError(
            REFUSE_MERGE_INACTIVE,
            f"источник слияния {source_family_id} не активен (статус: {source.status})",
            family_id=source_family_id,
            role="source",
            status=source.status,
        )

    # Кандидаты — списком id ДО лока контекстов (список нужен, чтобы знать,
    # что блокировать); membership по `work_family_id == source_family_id`
    # ПЕРЕЧИТЫВАЕТСЯ ЕЩЁ РАЗ уже под локом контекстов — контекст мог уйти у
    # источника МЕЖДУ листингом и локом (например, `assign_family` успела
    # его переназначить в третью семью, взяв `FOR UPDATE` на тот же контекст
    # раньше), и такой контекст не должен попасть в перенос.
    candidate_ids = [
        row.id
        for row in db.execute(
            sa.select(CatalogContext.id)
            .where(CatalogContext.work_family_id == source_family_id)
            .order_by(CatalogContext.id)
        )
    ]
    contexts: list[CatalogContext] = []
    if candidate_ids:
        _lock_contexts(db, candidate_ids)  # FOR UPDATE, по возрастанию id
        db.expire_all()
        contexts = (
            db.execute(
                sa.select(CatalogContext).where(
                    CatalogContext.id.in_(candidate_ids),
                    CatalogContext.work_family_id == source_family_id,
                )
            )
            .scalars()
            .all()
        )

    for ctx in contexts:
        ctx.work_family_id = target_family_id
    db.flush()
    moved_count = len(contexts)

    source_title = source.title
    source.status = FamilyStatus.archived.value
    source.archived_at = _now()
    db.flush()

    record_event(
        db,
        event_type="family_merged",
        family_id=source_family_id,
        actor_id=actor_id,
        payload={
            "into_family_id": target_family_id,
            "moved_contexts": moved_count,
            "source_title": source_title,
        },
    )
    record_event(
        db,
        event_type="family_archived",
        family_id=source_family_id,
        actor_id=actor_id,
        payload={"reason": "merged"},
    )
    return moved_count


def confirm_kind(
    db: Session, *, context_id: int, kind: str | None, actor_id: int
) -> CatalogContext:
    """Подтверждает текущий вид (`kind=None`) либо переопределяет его
    (`kind=X`): `SUGGESTED → CONFIRMED`, `semantic_kind_source='manual'` с
    автором и временем (спека §2.5, таблица переходов). `work_family_id` и
    `semantic_state`-независимая семья контекста этим вызовом НЕ трогаются —
    вид и семья разные оси (спека §2.5).

    Вызов на УЖЕ `CONFIRMED` контексте — НЕ-ОП, если `kind` не меняет фактическое значение (`kind=None` либо
    `kind` равен текущему `semantic_kind`): поле не трогается, событие не
    пишется. Единственный смысл повторного вызова на `CONFIRMED` —
    ПЕРЕОПРЕДЕЛИТЬ вид на ДРУГОЕ значение (тогда это обычная запись:
    `semantic_kind`/`source`/`by`/`at` обновляются, событие пишется). Из
    `SUGGESTED` вызов ВСЕГДА реален (это и есть переход таблицы §2.5), даже
    при `kind=None` — там меняются `source`/`state`, а не только `kind`.

    `FOR UPDATE` на строку контекста, перечитывание `semantic_state` ПОСЛЕ
    лока: гонок по этой операции план не утверждает,
    блокировка — ради перечитывания состояния, не ради сериализации.

    Raises:
        WorkFamilyError: контекст не найден (`REFUSE_CONTEXT_NOT_FOUND`);
            контекст `NOT_APPLICABLE`, перечитанное (`REFUSE_CONTEXT_NOT_APPLICABLE`
            — свой код сверх плана: решение о виде для строки-раздела/мусора
            бессмысленно, спека §2.5); `kind` задан, но вне `SemanticKind`
            (`REFUSE_INVALID_KIND`).
    """
    context = db.get(CatalogContext, context_id)
    if context is None:
        raise WorkFamilyError(
            REFUSE_CONTEXT_NOT_FOUND, f"контекст {context_id} не найден", context_id=context_id
        )

    _lock_contexts(db, [context_id])  # FOR UPDATE
    db.expire_all()

    context = db.get(CatalogContext, context_id)  # ПЕРЕЧИТАННОЕ после лока
    if context.semantic_state == SemanticState.NOT_APPLICABLE.value:
        raise WorkFamilyError(
            REFUSE_CONTEXT_NOT_APPLICABLE,
            f"контекст {context_id}: вид работы неприменим (NOT_APPLICABLE)",
            context_id=context_id,
        )

    old_kind = context.semantic_kind
    new_kind = old_kind if kind is None else kind
    if new_kind not in _SEMANTIC_KIND_VALUES:
        raise WorkFamilyError(
            REFUSE_INVALID_KIND, f"недопустимый вид работы: {new_kind!r}", kind=new_kind
        )

    if context.semantic_state == SemanticState.CONFIRMED.value and new_kind == old_kind:
        # НЕ-ОП: уже CONFIRMED, реального переопределения нет (см. докстроку).
        return context

    context.semantic_kind = new_kind
    context.semantic_kind_source = DecisionSource.manual.value
    context.semantic_kind_by = actor_id
    context.semantic_kind_at = _now()
    context.semantic_state = SemanticState.CONFIRMED.value
    db.flush()

    record_event(
        db,
        event_type="kind_set",
        context_id=context_id,
        actor_id=actor_id,
        payload={"from": old_kind, "to": new_kind, "source": DecisionSource.manual.value},
    )
    return context


def unconfirm_kind(db: Session, *, context_id: int, actor_id: int) -> CatalogContext:
    """Снимает подтверждение вида: `CONFIRMED → SUGGESTED`, вид
    ПЕРЕСЧИТЫВАЕТСЯ ПРАВИЛОМ (`classify_kind` от единицы каталожной строки
    контекста — НЕ сохраняется прежнее значение), `source='rule'`,
    автор/время очищены (спека §2.5, таблица переходов).

    Вызов на УЖЕ `SUGGESTED` контексте — НЕ-ОП: снимать нечего (подтверждения не было), поле не трогается,
    событие не пишется — без этой проверки повторный вызов молча
    переустановил бы `semantic_kind_at` и записал бы второе `kind_set` с
    `from == to`, хотя ничего в состоянии контекста не изменилось.

    `FOR UPDATE` на строку контекста, перечитывание `semantic_state` ПОСЛЕ
    лока — тот же протокол, что `confirm_kind`.

    Raises:
        WorkFamilyError: контекст не найден (`REFUSE_CONTEXT_NOT_FOUND`);
            контекст `NOT_APPLICABLE`, перечитанное
            (`REFUSE_CONTEXT_NOT_APPLICABLE`).
    """
    context = db.get(CatalogContext, context_id)
    if context is None:
        raise WorkFamilyError(
            REFUSE_CONTEXT_NOT_FOUND, f"контекст {context_id} не найден", context_id=context_id
        )

    _lock_contexts(db, [context_id])  # FOR UPDATE
    db.expire_all()

    context = db.get(CatalogContext, context_id)  # ПЕРЕЧИТАННОЕ после лока
    if context.semantic_state == SemanticState.NOT_APPLICABLE.value:
        raise WorkFamilyError(
            REFUSE_CONTEXT_NOT_APPLICABLE,
            f"контекст {context_id}: вид работы неприменим (NOT_APPLICABLE)",
            context_id=context_id,
        )
    if context.semantic_state == SemanticState.SUGGESTED.value:
        # НЕ-ОП: уже SUGGESTED, снимать нечего (см. докстроку).
        return context

    bucket = db.get(ContextBucket, context.bucket_id)
    catalog_position = db.get(CatalogPosition, bucket.catalog_position_id)
    unit_norm = context_routing_module._unit_norm_of(catalog_position)
    recomputed_kind = classify_kind(unit_norm)

    old_kind = context.semantic_kind
    context.semantic_kind = recomputed_kind
    context.semantic_kind_source = DecisionSource.rule.value
    context.semantic_kind_by = None
    context.semantic_kind_at = _now()
    context.semantic_state = SemanticState.SUGGESTED.value
    db.flush()

    record_event(
        db,
        event_type="kind_set",
        context_id=context_id,
        actor_id=actor_id,
        payload={"from": old_kind, "to": recomputed_kind, "source": DecisionSource.rule.value},
    )
    return context


def set_name_role(db: Session, *, context_id: int, role: str, actor_id: int) -> CatalogContext:
    """Ручная роль имени: `name_role_source='manual'` с автором и временем;
    `place_dictionary_version` на контексте и в событии — ТЕКУЩАЯ константа
    словаря (`PLACE_DICTIONARY_VERSION`), а не сохранённое прежнее значение:
    это ручное решение оператора, принятое СЕЙЧАС, а не пересчёт по словарю
    задним числом.

    `FOR UPDATE` на строку контекста, перечитывание не несёт домена (роль не
    зависит от чужого перечитываемого состояния), но лок — той же дисциплины
    ради.

    Raises:
        WorkFamilyError: `role` вне `NameRole` (`REFUSE_INVALID_NAME_ROLE`,
            проверяется ПЕРВЫМ, до чтения базы); контекст не найден
            (`REFUSE_CONTEXT_NOT_FOUND`).
    """
    if role not in _NAME_ROLE_VALUES:
        raise WorkFamilyError(
            REFUSE_INVALID_NAME_ROLE, f"недопустимая роль имени: {role!r}", role=role
        )

    context = db.get(CatalogContext, context_id)
    if context is None:
        raise WorkFamilyError(
            REFUSE_CONTEXT_NOT_FOUND, f"контекст {context_id} не найден", context_id=context_id
        )

    _lock_contexts(db, [context_id])  # FOR UPDATE
    db.expire_all()

    context = db.get(CatalogContext, context_id)  # ПЕРЕЧИТАННОЕ после лока
    old_role = context.name_role
    context.name_role = role
    context.name_role_source = DecisionSource.manual.value
    context.name_role_by = actor_id
    context.name_role_at = _now()
    context.place_dictionary_version = PLACE_DICTIONARY_VERSION
    db.flush()

    record_event(
        db,
        event_type="name_role_set",
        context_id=context_id,
        actor_id=actor_id,
        payload={
            "from": old_role,
            "to": role,
            "source": DecisionSource.manual.value,
            "place_dictionary_version": PLACE_DICTIONARY_VERSION,
        },
    )
    return context
