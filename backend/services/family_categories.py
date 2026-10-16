"""Справочник категорий семей: создание, правка, удаление и блокировки
(спека `2026-10-09-catalog-discovery-design.md` §2.9, решение 20).

Три строки справочника заводит миграция 0021 (`seed_key` не пуст); остальные —
оператор. Категория — **первая** в общем порядке блокировок фичи: кто ссылается
на категорию (`create_family`, `update_family`, `create_family_from_suggestion`,
обработка ответа открытия, активация, правка черновика), берёт её `FOR SHARE`
раньше своих строк семей и черновиков (`lock_categories(..., exclusive=False)`);
`update_category` берёт её `FOR UPDATE` и больше ничего; `delete_category` —
`FOR UPDATE`, затем черновики и предложения с этой категорией по `id`, затем
`DELETE`. Повышение режима замка внутри транзакции запрещено: ни одна функция
здесь не берёт `FOR UPDATE` на категорию, уже взятую `FOR SHARE` той же
транзакцией.

Замок строки идёт с `populate_existing`: без него identity map SQLAlchemy
отдала бы уже загруженный объект, а не свежее состояние после ожидания замка
(тот же приём, что `services/work_families.py`).

Отказы — `WorkFamilyError` с кодом из этого модуля (HTTP-слой переводит их
`routers/semantic.py`); пустые имя и определение отвергаются ПЕРВОЙ линией, в
Python, до обращения к базе — `CHECK` схемы остаётся второй. Дубль имени без
учёта регистра и крайних пробелов отвергается и проверкой, и ключом
`uq_family_categories_title` (гонка двух одновременных `create_category` —
тем же кодом); удаление категории, на которую успела сослаться семья, —
`category_in_use`, тот же код, что у проверки до удаления, а не сырой
`IntegrityError`.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import FamilyCategory, FamilyCategoryProposal, FamilyDraft, WorkFamily
from services.work_families import UNSET, WorkFamilyError

REFUSE_CATEGORY_NOT_FOUND = "category_not_found"
REFUSE_CATEGORY_IN_USE = "category_in_use"
REFUSE_CATEGORY_BLANK_TITLE = "category_blank_title"
REFUSE_CATEGORY_BLANK_DEFINITION = "category_blank_definition"
REFUSE_CATEGORY_DUPLICATE = "category_duplicate"

#: Ограничения базы, превращаемые в доменный отказ (второй линией).
_UNIQUE_TITLE = "uq_family_categories_title"
_FAMILY_CATEGORY_FK = "fk_work_families_family_category_id"


def _blank(value: object) -> bool:
    return not isinstance(value, str) or value.strip() == ""


def _constraint_name(exc: IntegrityError) -> str | None:
    """Имя нарушенного ограничения — по `diag` драйвера, не по тексту сообщения."""
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _lock_categories_statement(category_ids: list[int] | None, *, exclusive: bool):
    """Отдельно от исполнения — тест компилирует запрос в SQL и проверяет РЕЖИМ
    (`FOR SHARE` vs `FOR UPDATE`) и порядок (`ORDER BY id`), а не намерение
    вызова (`docs/pitfalls/db.md`)."""
    stmt = sa.select(FamilyCategory.id).order_by(FamilyCategory.id)
    if category_ids is not None:
        stmt = stmt.where(FamilyCategory.id.in_(category_ids))
    return stmt.with_for_update() if exclusive else stmt.with_for_update(read=True)


def lock_categories(db: Session, category_ids: list[int] | None, *, exclusive: bool) -> None:
    """Блокирует существующие категории по возрастанию `id`; `None` — все.
    `exclusive=False` — `FOR SHARE` (совместима сама с собой: параллельные
    ссылающиеся друг другу не мешают), `exclusive=True` — `FOR UPDATE`
    (правка и удаление). Пустой список — no-op; несуществующий id пропускается —
    отсутствие категории устанавливает `require_category`."""
    if category_ids is not None and not category_ids:
        return
    db.execute(_lock_categories_statement(category_ids, exclusive=exclusive)).all()


def _not_found(category_id: int) -> WorkFamilyError:
    return WorkFamilyError(
        REFUSE_CATEGORY_NOT_FOUND,
        f"Категории «№ {category_id}» больше нет — её удалили. Выберите другую.",
        category_id=category_id,
    )


def require_category(db: Session, category_id: int, *, exclusive: bool = False) -> FamilyCategory:
    """Берёт категорию под замком (`FOR SHARE` либо `FOR UPDATE`) и возвращает её
    свежее состояние.

    Raises:
        WorkFamilyError: `category_not_found` — категории нет или её удалили."""
    lock_categories(db, [category_id], exclusive=exclusive)
    category = db.execute(
        sa.select(FamilyCategory)
        .where(FamilyCategory.id == category_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if category is None:
        raise _not_found(category_id)
    return category


def _duplicate_exists(db: Session, *, title: str, exclude_id: int | None) -> bool:
    """Есть ли другая категория с тем же именем без учёта регистра и крайних
    пробелов — то же выражение, что у уникального индекса."""
    stmt = sa.select(FamilyCategory.id).where(
        sa.func.lower(sa.func.btrim(FamilyCategory.title)) == sa.func.lower(sa.func.btrim(title))
    )
    if exclude_id is not None:
        stmt = stmt.where(FamilyCategory.id != exclude_id)
    return db.execute(stmt.limit(1)).first() is not None


def _duplicate(title: str) -> WorkFamilyError:
    return WorkFamilyError(
        REFUSE_CATEGORY_DUPLICATE, f"Категория «{title}» уже есть.", title=title
    )


def _check_input(title: object, definition: object) -> None:
    """Пустое и пробельное имя и определение — первая линия. `UNSET` — поле не
    передано, проверять нечего."""
    if title is not UNSET and _blank(title):
        raise WorkFamilyError(
            REFUSE_CATEGORY_BLANK_TITLE,
            "У категории должно быть имя — по определению модель выбирает категорию.",
        )
    if definition is not UNSET and _blank(definition):
        raise WorkFamilyError(
            REFUSE_CATEGORY_BLANK_DEFINITION,
            "У категории должно быть определение — по определению модель выбирает категорию.",
        )


def create_category(
    db: Session, *, title: str, definition: str, actor_id: int
) -> FamilyCategory:
    """Заводит категорию оператора: `seed_key = NULL`, автор — `actor_id`.

    Raises:
        WorkFamilyError: `category_blank_title`, `category_blank_definition`
            (проверяются ПЕРВЫМИ, до чтения базы); `category_duplicate` —
            проверкой и, при гонке двух одновременных вставок, ключом
            `uq_family_categories_title`."""
    _check_input(title, definition)
    clean_title = title.strip()
    if _duplicate_exists(db, title=clean_title, exclude_id=None):
        raise _duplicate(clean_title)

    category = FamilyCategory(
        seed_key=None, title=clean_title, definition=definition.strip(), created_by=actor_id
    )
    try:
        with db.begin_nested():
            db.add(category)
            db.flush()
    except IntegrityError as exc:
        if _constraint_name(exc) != _UNIQUE_TITLE:
            raise
        raise _duplicate(clean_title) from exc
    return category


def update_category(
    db: Session,
    *,
    category_id: int,
    title: str | object = UNSET,
    definition: str | object = UNSET,
    actor_id: int,
) -> FamilyCategory:
    """Правит имя и/или определение. Непереданное поле (`UNSET`) не трогается.
    Берёт категорию `FOR UPDATE` и больше ничего (решение 20).

    Raises:
        WorkFamilyError: `category_blank_title`, `category_blank_definition`;
            `category_not_found`; `category_duplicate` — имя занято другой
            категорией (проверкой под замком либо ключом при гонке)."""
    _check_input(title, definition)
    category = require_category(db, category_id, exclusive=True)

    clean_title = None if title is UNSET else title.strip()
    if clean_title is not None and _duplicate_exists(
        db, title=clean_title, exclude_id=category_id
    ):
        raise _duplicate(clean_title)

    # Поля присваиваются ВНУТРИ точки сохранения: `begin_nested()` перед `SAVEPOINT`
    # сам делает `flush()`, и присвоенное до него ушло бы в `UPDATE` вне точки —
    # ошибка ключа рушила бы всю транзакцию сессии, а не только точку.
    try:
        with db.begin_nested():
            if clean_title is not None:
                category.title = clean_title
            if definition is not UNSET:
                category.definition = definition.strip()
            db.flush()
    except IntegrityError as exc:
        if _constraint_name(exc) != _UNIQUE_TITLE:
            raise
        raise _duplicate(clean_title) from exc
    return category


def _in_use(title: str, category_id: int, family_count: int) -> WorkFamilyError:
    return WorkFamilyError(
        REFUSE_CATEGORY_IN_USE,
        f"Категорию «{title}» носят {family_count} семей — удалить можно только пустую.",
        category_id=category_id,
        family_count=family_count,
    )


def _family_count(db: Session, category_id: int) -> int:
    return db.execute(
        sa.select(sa.func.count(WorkFamily.id)).where(WorkFamily.family_category_id == category_id)
    ).scalar_one()


def delete_category(db: Session, *, category_id: int, actor_id: int) -> None:
    """Удаляет категорию без семей. Порядок блокировок — спека §2.9: (1)
    категория `FOR UPDATE`; (2) число семей с ней — ненулевое даёт
    `category_in_use`, ничего не удалено; (3) черновики с этой категорией
    `FOR UPDATE` по `id` получают `family_category_id = NULL`, предложения
    категории с ней `FOR UPDATE` по `id` удаляются — они временные; (4)
    `DELETE` категории. Действия внешних ключей `SET NULL`/`CASCADE` к этому
    моменту строк не находят: обе правки сделаны здесь, под замками.

    Raises:
        WorkFamilyError: `category_not_found`; `category_in_use` (с числом
            семей) — проверкой, а при гонке с семьёй, успевшей сослаться на
            категорию, ключом `RESTRICT` — тем же кодом."""
    category = require_category(db, category_id, exclusive=True)

    family_count = _family_count(db, category_id)
    if family_count:
        raise _in_use(category.title, category_id, family_count)

    draft_ids = (
        db.execute(
            sa.select(FamilyDraft.id)
            .where(FamilyDraft.family_category_id == category_id)
            .order_by(FamilyDraft.id)
            .with_for_update()
        )
        .scalars()
        .all()
    )
    if draft_ids:
        db.execute(
            sa.update(FamilyDraft)
            .where(FamilyDraft.id.in_(draft_ids))
            .values(family_category_id=None)
        )
    proposal_keys = db.execute(
        sa.select(FamilyCategoryProposal.job_id, FamilyCategoryProposal.family_id)
        .where(FamilyCategoryProposal.family_category_id == category_id)
        .order_by(FamilyCategoryProposal.job_id, FamilyCategoryProposal.family_id)
        .with_for_update()
    ).all()
    for job_id, family_id in proposal_keys:
        db.execute(
            sa.delete(FamilyCategoryProposal).where(
                FamilyCategoryProposal.job_id == job_id,
                FamilyCategoryProposal.family_id == family_id,
            )
        )

    title = category.title
    try:
        with db.begin_nested():
            db.execute(sa.delete(FamilyCategory).where(FamilyCategory.id == category_id))
    except IntegrityError as exc:
        if _constraint_name(exc) != _FAMILY_CATEGORY_FK:
            raise
        raise _in_use(title, category_id, _family_count(db, category_id)) from exc
    db.expire_all()
