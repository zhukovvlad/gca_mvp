"""Журнал семантического контура: запись событий с закрытым списком типов и
обязательным составом ``payload`` (план фичи «Семьи и контексты», задача 2;
спека `2026-09-22-catalog-families-design.md` §2.14).

Таблица §2.14 — единственный источник истины для ``EVENT_REQUIRED_KEYS``:
пятнадцать типов событий, каждому — свой набор обязательных ключей payload.
Схема (миграция 0017, задача 1) держит закрытый список ``event_type`` и
равносильность «тип ↔ предмет» (``CK_EVENT_SUBJECT_BY_TYPE``); состав ключей
payload схемой не выражается — его держит валидатор здесь, ДО того, как
что-либо достигает базы (невалидное событие не долетает даже до ``flush``).

Предмет события ставится ТИПОМ, а не аргументом вызова: семейный тип отвергает
``context_id`` и требует ``family_id``, контекстный — наоборот; отсутствие
обоих субъектов и оба субъекта разом отвергаются тем же правилом (то же
множество, что различает ``CK_EVENT_SUBJECT_BY_TYPE``).

Закрытые множества перечислимых ключей (``origin``, ``reason``, ``source``,
``trigger``) — из спеки §2.14, кроме ``source``: спека не сводит его в
множество, а закрывает через прикладной смысл ключа (Global Constraints
задачи, решение оркестратора). ``kind_set.source`` и ``name_role_set.source``
берут значения из ``DecisionSource`` (обе колонки контекста —
``semantic_kind_source``/``name_role_source`` — того же типа, §2.3);
``context_family_assigned.source`` — из ``FamilySource`` (колонка
``family_source``, там же): значения взяты из перечислений `models.py`, а не
перепечатаны строками. Это НЕ гарантирует само по себе, что множество здесь
не разойдётся с колонкой схемы — перечисление можно поменять на другое и
код по-прежнему будет читать чьи-то значения; расхождение (например, если
``context_family_assigned.source`` ошибочно возьмёт значения
``DecisionSource`` вместо ``FamilySource``) ловит только тест
(`test_semantic_events.py`), который закрепляет `EVENT_ENUM_VALUES` целиком
против независимого литерала — литерал написан В ТЕСТЕ, не в этом модуле.
``routing_rules_dropped.reason``
спека не сводит в множество вовсе: в фиче 1 правила пропадают только при
слиянии в Review, поэтому множество закрыто единственным значением
``review_merge``; следующая причина (будущая задача/фича) расширяет это
множество в своём коммите.
"""
from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.orm import Session

from models import DecisionSource, FamilySource, SemanticEvent


class SemanticEventError(Exception):
    """Невалидная запись журнала: неизвестный тип, предмет не по типу,
    отсутствующий обязательный ключ или значение перечислимого ключа вне
    закрытого множества. Кидается ДО того, как что-либо достигает базы."""


#: Ровно пять типов, предмет которых — семья (спека §2.14, равно множеству
#: `CK_EVENT_SUBJECT_BY_TYPE` задачи 1 — сверка внешняя, тестом).
FAMILY_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "family_created",
        "family_updated",
        "family_activated",
        "family_archived",
        "family_merged",
    }
)

#: Остальные десять типов — предмет контекст (спека §2.14).
CONTEXT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "context_created",
        "context_split",
        "context_merged",
        "members_moved",
        "members_marked_stale",
        "kind_set",
        "name_role_set",
        "context_family_assigned",
        "context_archived",
        "routing_rules_dropped",
    }
)

#: Таблица спеки §2.14 целиком: тип события → обязательные ключи `payload`.
#: Ключ ОБЯЗАН присутствовать в словаре; значение ключа может законно быть
#: `None` (`context_split.rule_id`, `context_family_assigned.from_family_id`/
#: `to_family_id`, `family_created.unit`, `family_activated.unit`) —
#: отсутствие ключа и ключ со значением `None` не одно и то же.
EVENT_REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "context_created": frozenset({"bucket_id", "origin"}),
    "context_split": frozenset({"from_context_id", "moved_members", "rule_id"}),
    "context_merged": frozenset({"into_context_id", "moved_members"}),
    "members_moved": frozenset({"from_context_id", "moved_members", "reason"}),
    "members_marked_stale": frozenset({"count", "trigger"}),
    "kind_set": frozenset({"from", "to", "source"}),
    "name_role_set": frozenset({"from", "to", "source", "place_dictionary_version"}),
    "context_family_assigned": frozenset({"from_family_id", "to_family_id", "source"}),
    "context_archived": frozenset({"reason"}),
    "routing_rules_dropped": frozenset({"bucket_id", "count", "reason"}),
    "family_created": frozenset({"title", "unit", "origin"}),
    "family_updated": frozenset({"changed"}),
    "family_activated": frozenset({"title", "unit"}),
    "family_archived": frozenset({"reason"}),
    "family_merged": frozenset({"into_family_id", "moved_contexts", "source_title"}),
}

#: (тип события, ключ payload) → допустимые значения перечислимого ключа
#: (спека §2.14; `source` — решение оркестратора, см. докстринг модуля).
EVENT_ENUM_VALUES: dict[tuple[str, str], frozenset[str]] = {
    ("context_created", "origin"): frozenset(
        {"import", "backfill", "split", "review_merge", "stale_accepted"}
    ),
    ("members_moved", "reason"): frozenset({"manual", "stale_accepted", "review_merge"}),
    ("context_archived", "reason"): frozenset({"operator", "review_merge", "context_merge"}),
    ("family_created", "origin"): frozenset({"seed", "operator"}),
    ("family_archived", "reason"): frozenset({"operator", "merged"}),
    ("members_marked_stale", "trigger"): frozenset({"category_override"}),
    ("kind_set", "source"): frozenset(member.value for member in DecisionSource),
    ("name_role_set", "source"): frozenset(member.value for member in DecisionSource),
    ("context_family_assigned", "source"): frozenset(member.value for member in FamilySource),
    ("routing_rules_dropped", "reason"): frozenset({"review_merge"}),
}


def _validate_subject(event_type: str, context_id: int | None, family_id: int | None) -> None:
    if event_type in FAMILY_EVENT_TYPES:
        if family_id is None:
            raise SemanticEventError(
                f"событие {event_type!r}: предмет — семья, требуется family_id"
            )
        if context_id is not None:
            raise SemanticEventError(
                f"событие {event_type!r}: предмет — семья, context_id недопустим"
            )
        return
    if event_type in CONTEXT_EVENT_TYPES:
        if context_id is None:
            raise SemanticEventError(
                f"событие {event_type!r}: предмет — контекст, требуется context_id"
            )
        if family_id is not None:
            raise SemanticEventError(
                f"событие {event_type!r}: предмет — контекст, family_id недопустим"
            )
        return
    raise SemanticEventError(f"неизвестный event_type: {event_type!r}")


#: Ключи, обязательные внутри КАЖДОГО элемента списка `family_updated.changed`
#: (спека §2.14: «список полей с `from` и `to`» — сам список назван полем
#: `changed`, а поле каждого элемента — `field`, «откуда» и «куда»).
_CHANGED_ITEM_REQUIRED_KEYS = ("field", "from", "to")


def _validate_family_updated_changed(payload: Mapping[str, object]) -> None:
    """`family_updated.changed` — НЕПУСТОЙ список, каждый элемент которого
    несёт `field`, `from`, `to` (решение оркестратора: `EVENT_REQUIRED_KEYS`
    остаётся `dict[str, frozenset[str]]` — присутствие ключа `changed` на
    верхнем уровне держит этот словарь как и раньше; форма СОДЕРЖИМОГО
    списка — отдельная проверка, потому что `frozenset` ключей не способен
    выразить «список объектов с тремя полями каждый»).
    """
    changed = payload["changed"]
    if not isinstance(changed, list):
        raise SemanticEventError(
            "событие 'family_updated': ключ 'changed' обязан быть списком, "
            f"получено {type(changed).__name__}"
        )
    if len(changed) == 0:
        raise SemanticEventError(
            "событие 'family_updated': ключ 'changed' не может быть пустым списком"
        )
    for index, item in enumerate(changed):
        for item_key in _CHANGED_ITEM_REQUIRED_KEYS:
            if not isinstance(item, Mapping) or item_key not in item:
                raise SemanticEventError(
                    f"событие 'family_updated': changed[{index}] не содержит "
                    f"обязательный ключ {item_key!r}"
                )


def _validate_payload(event_type: str, payload: object) -> None:
    """Проверяет обязательные ключи и допустимые значения `payload`.

    Сообщение об отсутствующем ключе называет ИМЕННО отсутствующие ключи —
    ключи, которые в `payload` уже присутствуют, в сообщении не упоминаются
    (`test_semantic_events.py::TestEachRequiredKeyIsEnforced`; сообщение
    вида «отсутствуют обязательные ключи: a, b, c», перечисляющее ВСЕ
    обязательные ключи типа вместо только недостающих, обязано покраснить
    этот тест).
    """
    if not isinstance(payload, Mapping):
        raise SemanticEventError(
            f"событие {event_type!r}: payload обязан быть объектом (dict), получено "
            f"{type(payload).__name__}"
        )
    required = EVENT_REQUIRED_KEYS[event_type]
    missing = sorted(key for key in required if key not in payload)
    if missing:
        raise SemanticEventError(
            f"событие {event_type!r}: отсутствуют обязательные ключи payload: "
            + ", ".join(missing)
        )
    for key in required:
        allowed = EVENT_ENUM_VALUES.get((event_type, key))
        if allowed is None:
            continue
        value = payload[key]
        # Сравнение `value in allowed` (allowed — `frozenset`) может кинуть
        # `TypeError`: либо `value` нехэшируемо (список, словарь), либо его
        # `__eq__`/`__hash__` сам отказывается сравниваться с чем-либо из
        # множества. `TypeError` наружу нарушил бы контракт «любой отказ —
        # SemanticEventError» (M1) — в обоих случаях.
        try:
            is_allowed = value in allowed
        except TypeError:
            raise SemanticEventError(
                f"событие {event_type!r}: ключ {key!r} = {value!r} нельзя сравнить "
                f"с допустимым множеством {sorted(allowed)}"
            ) from None
        if not is_allowed:
            raise SemanticEventError(
                f"событие {event_type!r}: ключ {key!r} = {value!r} вне допустимого "
                f"множества {sorted(allowed)}"
            )
    if event_type == "family_updated":
        _validate_family_updated_changed(payload)


def record_event(
    db: Session,
    *,
    event_type: str,
    payload: dict[str, object],
    context_id: int | None = None,
    family_id: int | None = None,
    actor_id: int | None = None,
) -> SemanticEvent:
    """Записывает событие журнала, провалидировав его ДО обращения к базе.

    Порядок проверок: субъект по типу (совпадает с `CK_EVENT_SUBJECT_BY_TYPE`,
    но выполняется в Python — до `flush`, а не как побочный эффект отказа
    базы), затем обязательные ключи payload, затем значения перечислимых
    ключей. Любой отказ — `SemanticEventError`, ни один — не долетает даже
    до `db.add`.
    """
    _validate_subject(event_type, context_id, family_id)
    _validate_payload(event_type, payload)

    event = SemanticEvent(
        context_id=context_id,
        family_id=family_id,
        event_type=event_type,
        payload=payload,
        actor_id=actor_id,
    )
    db.add(event)
    db.flush()
    return event
