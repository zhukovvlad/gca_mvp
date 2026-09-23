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
только на базе с непонятным `IntegrityError` (решение оркестратора брифа
задачи 7, см. отчёт `tasks/catalog-families-work/task7-executor.md`).

Единица семьи (`unit_id`) резолвится из текста ЧЕРЕЗ `UnitResolver`
(`services/unit_resolution.py`) — как при импорте, тем же самым способом:
идентичность единицы не идентификатор строки `units_of_measurement`
(разный на разных базах), а её каноническое имя (`code`).

Коды отказов сверх плана (задача 7 заводит только `WorkFamilyError` и
`REFUSE_ACTIVATE_WITHOUT_DEFINITION` по имени из плана; остальные —
решение исполнителя, см. отчёт):
`REFUSE_FAMILY_NOT_FOUND`, `REFUSE_UPDATE_ARCHIVED` (правка архивной семьи),
`REFUSE_ACTIVATE_NOT_DRAFT` (активация не из `draft`), `REFUSE_UNKNOWN_UNIT`
(ручное создание с единицей, которую `UnitResolver` не резолвит — семья без
единицы вообще допустима, `unit_name=None`, а вот НАЗВАННАЯ, но неизвестная
единица — молчаливая потеря идентичности, которую этот модуль не допускает).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import FamilyStatus, WorkFamily
from services.semantic_events import record_event
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

#: Коды сверх плана (решение исполнителя, см. докстроку модуля и отчёт).
REFUSE_FAMILY_NOT_FOUND = "family_not_found"
REFUSE_UPDATE_ARCHIVED = "update_archived"
REFUSE_ACTIVATE_NOT_DRAFT = "activate_not_draft"
REFUSE_UNKNOWN_UNIT = "unknown_unit"


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
    приписывали (решение оркестратора брифа задачи 7)."""
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
    `status='draft'`, `family_created` с `origin='operator'` (план, задача 7,
    «Решения оркестратора»).

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
    (план, задача 7, «Решения оркестратора»: `changed` только по реально
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
    ТОЛЬКО по `seed_key` (план, задача 7, «Решения оркестратора») — найдена
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
            и в `create_family` (докстрока модуля, решение оркестратора
            Round 2 ревью задачи 7).
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
            # create_family/update_family (MINOR-2, ревью задачи 7 Round 2).
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
