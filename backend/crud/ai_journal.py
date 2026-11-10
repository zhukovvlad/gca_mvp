"""«Журнал ИИ»: чтение списка и записи, отмена склейки и приравнивания, «вернуть
автоматику» (спека упрощения каталога §2.6, §2.7).

Запись — строка `catalog_auto_actions`; её пишут сами автодействия (`services/ai_journal.py`).
Здесь запись читается человеком и исправляется:

- `undo` склейки (`composite`): значение теряет части и становится простым с исходным
  текстом, контексты остаются на нём, пишется вето состава;
- `undo` приравнивания (`equated`): заводится простое значение из текста, контексты записи
  переходят на него, пара «текст -> значение» пишется как «не дубль»;
- `restore-auto`: снимает решение, которое записала отмена (`payload.undo_decision`) —
  вето вместе с частями либо «не дубль», — и ставит `restored_at`. Исправление значения
  он не откатывает: пометка снимается, а не исправление.

`undo` записи `value_added` (слияние идёт через схему значений) и `split` (отдельная
операция деления) здесь отказывает кодом `journal_undo_not_applicable`.

Записи значений идут через `services/schema_edit.py` (правка схемы на месте), а не прямыми
`UPDATE`: список значений, варианты и очередь заданий сверяются там. Отказ отмены ничего не
оставляет: операция идёт в savepoint. Функции ничего не коммитят: транзакцией владеет
вызывающий. Чтение не пишет.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError, iso
from models import (
    AUTO_ACTION_KINDS,
    CatalogAutoAction,
    CatalogContext,
    CatalogPosition,
    CompositeVeto,
    ContextBucket,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterValue,
    User,
    ValueNotDuplicate,
    WorkFamily,
)
from services import schema_edit
from services.human_decisions import add_not_duplicate, add_veto
from services.work_families import WorkFamilyError
from services.work_variants import REFUSE_PARAMETER_NOT_FOUND, REFUSE_VALUE_NOT_FOUND, normalize_value

REFUSE_JOURNAL_NOT_FOUND = "journal_not_found"
REFUSE_JOURNAL_UNDONE = "journal_already_undone"
REFUSE_JOURNAL_UNDO_NOT_APPLICABLE = "journal_undo_not_applicable"
REFUSE_JOURNAL_UNDO_CONFLICT = "journal_undo_conflict"
REFUSE_JOURNAL_NOT_UNDONE = "journal_not_undone"
REFUSE_JOURNAL_ALREADY_RESTORED = "journal_already_restored"

JournalState = Literal["active", "undone", "locked"]

#: Виды, чью отмену умеет этот модуль.
_UNDOABLE = ("composite", "equated")


class JournalItem(TypedDict, total=False):
    id: int
    kind: str
    created_at: str
    family: dict
    context: dict | None
    parameter: dict | None
    text: str | None
    summary_values: list[str]
    state: JournalState
    undo: dict
    undone: dict | None
    restored_at: str | None
    details: dict


def _refuse(status_code: int, code: str, detail: str) -> DomainError:
    return DomainError(status_code, detail, code=code)


def _not_found(item_id: int) -> DomainError:
    return _refuse(404, REFUSE_JOURNAL_NOT_FOUND, f"запись журнала {item_id} не найдена")


# ---------------------------------------------------------------------------
#  Чтение
# ---------------------------------------------------------------------------

def _rows_statement():
    """Запись журнала со всем, что нужно показать, одним запросом: только скаляры,
    ни одной ленивой связи."""
    return (
        sa.select(
            CatalogAutoAction.id,
            CatalogAutoAction.kind,
            CatalogAutoAction.family_id,
            CatalogAutoAction.context_id,
            CatalogAutoAction.parameter_id,
            CatalogAutoAction.value_id,
            CatalogAutoAction.payload,
            CatalogAutoAction.created_at,
            CatalogAutoAction.undone_at,
            CatalogAutoAction.restored_at,
            WorkFamily.title.label("family_title"),
            CatalogPosition.standard_job_title.label("context_title"),
            FamilyParameter.name.label("parameter_name"),
            FamilyParameterValue.value.label("value_text"),
            User.email.label("undone_by_email"),
        )
        .select_from(CatalogAutoAction)
        .join(WorkFamily, WorkFamily.id == CatalogAutoAction.family_id)
        .outerjoin(CatalogContext, CatalogContext.id == CatalogAutoAction.context_id)
        .outerjoin(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .outerjoin(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .outerjoin(FamilyParameter, FamilyParameter.id == CatalogAutoAction.parameter_id)
        .outerjoin(FamilyParameterValue, FamilyParameterValue.id == CatalogAutoAction.value_id)
        .outerjoin(User, User.id == CatalogAutoAction.undone_by)
    )


def _escape_ilike(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _conditions(*, family_id: int | None, state: str | None, q: str | None) -> list:
    """Отборы, общие для страницы и счётчиков (все, кроме вида)."""
    conditions: list = []
    if family_id is not None:
        conditions.append(CatalogAutoAction.family_id == family_id)
    if state == "active":
        conditions.append(CatalogAutoAction.undone_at.is_(None))
    elif state == "undone":
        conditions.append(CatalogAutoAction.undone_at.is_not(None))
    elif state == "locked":
        # `locked` бывает только у `split`; записей этого вида с замком пока нет.
        conditions.append(sa.false())
    if q:
        pattern = f"%{_escape_ilike(q)}%"
        conditions.append(
            sa.or_(
                CatalogAutoAction.payload["text"].astext.ilike(pattern, escape="\\"),
                WorkFamily.title.ilike(pattern, escape="\\"),
                FamilyParameterValue.value.ilike(pattern, escape="\\"),
            )
        )
    return conditions


def _summary_values(row: Any, part_texts: dict[int, str]) -> list[str]:
    payload = row.payload
    if row.kind == "composite":
        return [part_texts[part_id] for part_id in payload.get("part_ids", []) if part_id in part_texts]
    if row.kind == "equated":
        return [payload["text"], row.value_text] if row.value_text is not None else [payload["text"]]
    if row.kind == "value_added":
        return [payload["text"]]
    return [group["title"] for group in payload.get("groups", [])]


def _item(row: Any, part_texts: dict[int, str]) -> JournalItem:
    undone = row.undone_at is not None
    undoable = (
        row.kind in _UNDOABLE
        and not undone
        and row.parameter_id is not None
        and row.value_id is not None
    )
    return {
        "id": row.id,
        "kind": row.kind,
        "created_at": iso(row.created_at),
        "family": {"id": row.family_id, "title": row.family_title},
        "context": (
            None if row.context_id is None
            else {"id": row.context_id, "title": row.context_title}
        ),
        "parameter": (
            None if row.parameter_id is None
            else {"id": row.parameter_id, "name": row.parameter_name}
        ),
        "text": row.payload.get("text"),
        "summary_values": _summary_values(row, part_texts),
        "state": "undone" if undone else "active",
        "undo": {"available": undoable, "locked_by": None},
        "undone": (
            {"at": iso(row.undone_at), "by_email": row.undone_by_email} if undone else None
        ),
        "restored_at": iso(row.restored_at),
    }


def _part_texts(db: Session, rows: list[Any]) -> dict[int, str]:
    ids = {
        part_id
        for row in rows
        if row.kind == "composite"
        for part_id in row.payload.get("part_ids", [])
    }
    if not ids:
        return {}
    return dict(
        db.execute(
            sa.select(FamilyParameterValue.id, FamilyParameterValue.value).where(
                FamilyParameterValue.id.in_(ids)
            )
        ).all()
    )


def list_journal(
    db: Session, *, kind: str | None, family_id: int | None, state: str | None,
    q: str | None, limit: int, offset: int,
) -> dict:
    """Страница журнала: `{items, total, counts}`. Порядок — `created_at DESC, id DESC`;
    `counts` — по четырём видам при всех отборах, кроме вида."""
    conditions = _conditions(family_id=family_id, state=state, q=q)
    selected = list(conditions)
    if kind is not None:
        selected.append(CatalogAutoAction.kind == kind)
    rows = db.execute(
        _rows_statement()
        .where(*selected)
        .order_by(CatalogAutoAction.created_at.desc(), CatalogAutoAction.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    total = db.execute(
        sa.select(sa.func.count()).select_from(
            _rows_statement().where(*selected).order_by(None).subquery()
        )
    ).scalar_one()
    counted = dict(
        db.execute(
            sa.select(CatalogAutoAction.kind, sa.func.count())
            .select_from(CatalogAutoAction)
            .join(WorkFamily, WorkFamily.id == CatalogAutoAction.family_id)
            .outerjoin(
                FamilyParameterValue, FamilyParameterValue.id == CatalogAutoAction.value_id
            )
            .where(*conditions)
            .group_by(CatalogAutoAction.kind)
        ).all()
    )
    texts = _part_texts(db, rows)
    return {
        "items": [_item(row, texts) for row in rows],
        "total": total,
        "counts": {name: counted.get(name, 0) for name in AUTO_ACTION_KINDS},
    }


def _details(db: Session, row: Any) -> dict:
    """Контексты записи с текстом значения параметра на них сейчас и текст, который
    значение получит после отмены."""
    ids = row.payload.get("context_ids", [])
    contexts = db.execute(
        sa.select(
            CatalogContext.id, CatalogPosition.standard_job_title, FamilyParameterValue.value
        )
        .select_from(CatalogContext)
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .outerjoin(
            ContextParameterValue,
            sa.and_(
                ContextParameterValue.context_id == CatalogContext.id,
                ContextParameterValue.parameter_id == row.parameter_id,
            ),
        )
        .outerjoin(
            FamilyParameterValue, FamilyParameterValue.id == ContextParameterValue.value_id
        )
        .where(CatalogContext.id.in_(ids))
        .order_by(CatalogContext.id)
    ).all()
    return {
        "contexts": [
            {"id": context_id, "title": title, "parameter_value": value}
            for context_id, title, value in contexts
        ],
        "undo_text": row.payload["text"],
    }


def get_journal_item(db: Session, item_id: int) -> JournalItem:
    """Запись с подробностями (для склейки и приравнивания — контексты и значения на
    них); нет записи — `404 journal_not_found`."""
    rows = db.execute(_rows_statement().where(CatalogAutoAction.id == item_id)).all()
    if not rows:
        raise _not_found(item_id)
    [row] = rows
    item = _item(row, _part_texts(db, rows))
    if row.kind in _UNDOABLE:
        item["details"] = _details(db, row)
    return item


# ---------------------------------------------------------------------------
#  Отмена и «вернуть автоматику»
# ---------------------------------------------------------------------------

def _locked_row(db: Session, item_id: int) -> CatalogAutoAction:
    row = db.execute(
        sa.select(CatalogAutoAction)
        .where(CatalogAutoAction.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise _not_found(item_id)
    return row


def _undo_composite(db: Session, row: CatalogAutoAction, user_id: int) -> dict:
    parts = schema_edit.split_composite_value(
        db, family_id=row.family_id, parameter_id=row.parameter_id, value_id=row.value_id,
        text=row.payload["text"], actor_id=user_id,
    )
    veto_id = add_veto(
        db, parameter_id=row.parameter_id, kind=row.payload["kind"], part_ids=parts,
        value_id=row.value_id, user_id=user_id,
    )
    return {"veto_id": veto_id}


def _undo_equated(db: Session, row: CatalogAutoAction, user_id: int) -> dict:
    payload = row.payload
    schema_edit.detach_equated_text(
        db, family_id=row.family_id, parameter_id=row.parameter_id, text=payload["text"],
        target_value_id=payload["target_value_id"], context_ids=payload["context_ids"],
        actor_id=user_id,
    )
    decision_id = add_not_duplicate(
        db, parameter_id=row.parameter_id, text_norm=normalize_value(payload["text"]),
        value_id=payload["target_value_id"], user_id=user_id,
    )
    return {"not_duplicate_id": decision_id}


def _translated(exc: WorkFamilyError) -> Exception:
    """Отказы правки схемы, которые для журнала значат «запись устарела» или «текст
    занят»; остальные (семья не активна, идёт пересборка) уходят как есть."""
    if exc.code in (REFUSE_PARAMETER_NOT_FOUND, REFUSE_VALUE_NOT_FOUND):
        return _refuse(
            409, REFUSE_JOURNAL_UNDO_NOT_APPLICABLE,
            "запись устарела: схема семьи перестроена или значение уже изменено",
        )
    if exc.code == schema_edit.REFUSE_VALUE_TEXT_TAKEN:
        return _refuse(
            409, REFUSE_JOURNAL_UNDO_CONFLICT,
            "исходный текст уже занят другим значением параметра: переименуйте или "
            "слейте то значение и повторите отмену",
        )
    return exc


def undo_action(db: Session, item_id: int, *, user_id: int) -> JournalItem:
    """Отменяет склейку или приравнивание и возвращает обновлённую запись.

    Raises:
        DomainError: записи нет (`404 journal_not_found`); уже отменена
            (`409 journal_already_undone`); вид без отмены здесь либо запись устарела —
            параметр или значение ушли, схема перестроена (`409
            journal_undo_not_applicable`); исходный текст занят другим значением (`409
            journal_undo_conflict`) — тогда ничего не изменено.
        WorkFamilyError: семья не активна, идёт пересборка схемы.
    """
    row = _locked_row(db, item_id)
    if row.undone_at is not None:
        raise _refuse(409, REFUSE_JOURNAL_UNDONE, f"запись {item_id} уже отменена")
    if row.kind not in _UNDOABLE or row.parameter_id is None or row.value_id is None:
        raise _refuse(
            409, REFUSE_JOURNAL_UNDO_NOT_APPLICABLE,
            f"запись {item_id} вида «{row.kind}» здесь не отменяется",
        )
    try:
        with db.begin_nested():
            decision = (
                _undo_composite(db, row, user_id)
                if row.kind == "composite"
                else _undo_equated(db, row, user_id)
            )
    except WorkFamilyError as exc:
        translated = _translated(exc)
        if translated is exc:
            raise
        raise translated from exc
    row.payload = {**row.payload, "undo_decision": decision}
    row.undone_at = datetime.now(UTC)
    row.undone_by = user_id
    db.flush()
    return get_journal_item(db, item_id)


def restore_auto(db: Session, item_id: int, *, user_id: int) -> JournalItem:
    """«Вернуть автоматику»: снимает решение, записанное отменой, — вето вместе с частями
    либо «не дубль» — и ставит `restored_at`. Значение и контексты не возвращает.

    Raises:
        DomainError: записи нет (`404 journal_not_found`); запись не отменялась (`409
            journal_not_undone`); автоматика уже возвращена (`409
            journal_already_restored`).
    """
    row = _locked_row(db, item_id)
    if row.undone_at is None:
        raise _refuse(409, REFUSE_JOURNAL_NOT_UNDONE, f"запись {item_id} не отменялась")
    if row.restored_at is not None:
        raise _refuse(409, REFUSE_JOURNAL_ALREADY_RESTORED, f"запись {item_id} уже возвращена")
    decision = row.payload.get("undo_decision", {})
    if "veto_id" in decision:
        db.execute(sa.delete(CompositeVeto).where(CompositeVeto.id == decision["veto_id"]))
    if "not_duplicate_id" in decision:
        db.execute(
            sa.delete(ValueNotDuplicate).where(
                ValueNotDuplicate.id == decision["not_duplicate_id"]
            )
        )
    row.restored_at = datetime.now(UTC)
    db.flush()
    return get_journal_item(db, item_id)
