"""Чтение для экрана «Семьи и контексты» (спека
`2026-09-22-catalog-families-design.md` §2.10; план, задача 12).

HTTP-слой поверх сервисов задач 4, 6-10 (`services/context_routing.py`,
`services/context_operations.py`, `services/work_families.py`) — этот модуль
сам ничего не мутирует, только строит выдачу для экрана.

`list_contexts` обязан читать ограниченным числом запросов, НЕЗАВИСИМЫМ от
числа строк выдачи (план, задача 12, «Утверждения»): выбираются только
СКАЛЯРНЫЕ колонки через явные `join`/`outerjoin`, ни одна лениво подгружаемая
ORM-связь не читается в цикле по строкам — иначе число запросов росло бы
вместе с числом строк (N+1). Ровно два запроса — счётчик и страница.

Три фильтра-признака (`has_stale_members`, `has_conflicting_members`,
`has_no_members`) — ТРИ РАЗНЫХ `EXISTS`-подзапроса, а не один общий
(план, задача 12, «Утверждения»): оси независимы, и вход с одновременно
`STALE`-членством и `conflict_at` обязан пройти оба первых фильтра сразу.

Строка страницы `list_contexts` (спека `2026-09-25-families-screen-design.md`
§2.8 п. 1) добавочно несёт `member_count`, `has_stale_members`,
`has_conflicting_members` и `work_category_path` — все четыре ВНУТРИ того же
запроса страницы, а не отдельным чтением: число и оба признака —
коррелированные подзапросы (переиспользуют `_stale_exists`/`_conflict_exists`
и новый `_member_count_subquery`), путь классификатора — ДВА внешних
соединения `work_categories` на родителя и прародителя статьи корзины.
Отдельного чтения справочника и кэша процесса нет: третий запрос сломал бы
инвариант «ровно два» (счётчик его не считает вовсе — эти колонки навешаны
только на страницу).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    Lot,
    MembershipState,
    NameRole,
    PositionItem,
    Proposal,
    SemanticEvent,
    UnitOfMeasure,
    WorkCategory,
    WorkFamily,
)
from services.context_routing import chapter_paths
from services.semantic_rules import nearest_working_chapter


class WorkCategoryRef(TypedDict):
    """Одна статья пути классификатора строки списка контекстов (спека
    `2026-09-25-families-screen-design.md` §2.8 п. 1) — код и название,
    без уровня: уровень читается из позиции в списке `work_category_path`."""

    code: str
    title: str


class MemberPath(TypedDict):
    """Группа членств карточки по ТЕКСТУ ближайшего пути (спека §2.8 п. 2,
    редакция 3): одинаковый путь top-down у разделов РАЗНЫХ смет — одна
    группа экрана, а не строка на каждый раздел. `chapter_item_ids` — все
    разделы, чей путь-текст совпал с этой группой, по возрастанию id;
    `chapter_item_ids=[]` — группа «без раздела» (`path=[]`), допустима
    схемой (`position_items.chapter_item_id` — `NULL`-able), в SQL все такие
    членства уже лежат в одной строке (`GROUP BY` группирует `NULL` вместе),
    второй группы `[]` быть не может. `path` — СВЕРХУ ВНИЗ, тот же порядок,
    что `chapter_paths()`. `stale_count`/`conflict_count` — подмножества
    `member_count`, не отдельные факты (`membership_state=STALE` и
    `conflict_at IS NOT NULL` — независимые оси членства, одно и то же
    членство может нести обе), суммы по всем разделам группы."""

    chapter_item_ids: list[int]
    path: list[str]
    member_count: int
    stale_count: int
    conflict_count: int


class StaleGroup(TypedDict):
    """Устаревшие ПЕРЕНОСИМЫЕ членства, сгруппированные по паре «текст пути →
    целевая статья» (спека §2.8 п. 2, редакция 3): та же ЦЕЛЬ переноса —
    текущая эффективная статья раздела (тот же `target_category_id`, что
    видит `transfer_proposal`/`accept_transfer`,
    `services/context_operations.py`, через `chapter_context(...).category_id`
    БЛИЖАЙШЕГО раздела) — у разделов РАЗНЫХ смет с ОДИНАКОВЫМ путём-текстом
    даёт ОДНУ запись; тот же путь, но иная цель — разные записи.
    `chapter_item_ids` — разделы этой пары по возрастанию, `[]` — «без
    раздела» (цели у неё нет вовсе). `target_category_*` — `None`, если у
    раздела нет статьи или раздела нет вовсе (`chapter_item_ids=[]`).

    `count` — ТОЛЬКО переносимые устаревшие членства (`membership_state=STALE
    AND conflict_at IS NULL`) — ровно то множество, что берёт пакетный
    перенос `transfer_stale_group` (`services/context_operations.py`),
    суммой по всем разделам группы. Устаревшее членство, у которого ЕСТЬ
    конфликт, не входит в `count` и не порождает запись `stale_groups`, если
    оно единственное устаревшее в группе — оно решается отдельным действием
    «Принять решение цели», не пакетным переносом (спека §2.6). Это НЕ то же
    самое, что `MemberPath.stale_count` (та ось считает ВСЕ `STALE`-членства
    группы, включая конфликтные, — независимая от `conflict_count`
    величина)."""

    chapter_item_ids: list[int]
    path: list[str]
    count: int
    target_category_id: int | None
    target_category_code: str | None
    target_category_title: str | None


@dataclass(frozen=True)
class ContextFilters:
    """Фильтры очереди контекстов (план, задача 12, `crud/semantic.py`).

    Все поля — `None` значит «фильтр не применён». Три булевых
    фильтра-признака — независимые оси (см. докстринг модуля).
    """

    catalog_query: str | None = None
    work_category_id: int | None = None
    semantic_kind: str | None = None
    name_role: str | None = None
    semantic_state: str | None = None
    has_stale_members: bool | None = None
    has_conflicting_members: bool | None = None
    has_no_members: bool | None = None


# ---------------------------------------------------------------------------
#  Семьи
# ---------------------------------------------------------------------------

def _family_row_select():
    """Строитель строки семьи, общий для списка (`list_families`) и
    сериализации ответов мутаций (`routers/semantic.py::_serialize_family`)
    — одна форма на оба пути, а не два независимых набора колонок, которые
    молча разойдутся при следующей правке."""
    return (
        sa.select(
            WorkFamily.id,
            WorkFamily.title,
            WorkFamily.unit_id,
            UnitOfMeasure.code.label("unit_code"),
            # Символ единицы (спека `2026-09-25-families-screen-design.md`
            # §2.8, уточнение сверкой с макетом 27.09.2026) — экран печатает
            # ЕГО, не `unit_code`; то же внешнее соединение, третьего запроса
            # нет.
            UnitOfMeasure.symbol.label("unit_symbol"),
            WorkFamily.definition,
            WorkFamily.status,
            WorkFamily.seed_key,
            WorkFamily.created_by,
            WorkFamily.created_at,
            WorkFamily.updated_at,
            WorkFamily.activated_by,
            WorkFamily.activated_at,
            WorkFamily.archived_at,
            sa.func.count(CatalogContext.id).label("context_count"),
        )
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == WorkFamily.unit_id)
        .outerjoin(CatalogContext, CatalogContext.work_family_id == WorkFamily.id)
        .group_by(WorkFamily.id, UnitOfMeasure.code, UnitOfMeasure.symbol)
    )


def _family_row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "unit_id": row.unit_id,
        "unit_code": row.unit_code,
        "unit_symbol": row.unit_symbol,
        "definition": row.definition,
        "status": row.status,
        "seed_key": row.seed_key,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "activated_by": row.activated_by,
        "activated_at": row.activated_at,
        "archived_at": row.archived_at,
        "context_count": row.context_count,
    }


def list_families(db: Session, *, status: str | None, unit_id: int | None) -> list[dict]:
    """Список семей с фильтром по статусу/единице и числом привязанных
    контекстов у КАЖДОЙ (спека §2.10; план, задача 12, «Утверждения») — то
    самое число, на которое ссылаются отказы правки единицы и
    архивирования (`REFUSE_UNIT_CHANGE_WITH_LINKS`/`REFUSE_ARCHIVE_WITH_LINKS`,
    `services/work_families.py`)."""
    stmt = _family_row_select().order_by(WorkFamily.id)
    if status is not None:
        stmt = stmt.where(WorkFamily.status == status)
    if unit_id is not None:
        stmt = stmt.where(WorkFamily.unit_id == unit_id)

    rows = db.execute(stmt).all()
    return [_family_row_to_dict(row) for row in rows]


def get_family_row(db: Session, *, family_id: int) -> dict | None:
    """Строка ОДНОЙ семьи, той же формы, что строка списка (`list_families`)
    — переиспользуется сериализацией ответов мутаций
    (`routers/semantic.py::_serialize_family`), чтобы `unit_code` и
    `context_count` были в ответе `create`/`update`/`activate`/`archive`/
    `merge`, а не только в `GET /families`."""
    row = db.execute(_family_row_select().where(WorkFamily.id == family_id)).first()
    if row is None:
        return None
    return _family_row_to_dict(row)


# ---------------------------------------------------------------------------
#  Контексты — очередь
# ---------------------------------------------------------------------------

def _stale_exists():
    return (
        sa.select(sa.literal(1))
        .select_from(ContextMember)
        .where(
            ContextMember.context_id == CatalogContext.id,
            ContextMember.membership_state == MembershipState.STALE.value,
        )
        .exists()
    )


def _conflict_exists():
    return (
        sa.select(sa.literal(1))
        .select_from(ContextMember)
        .where(
            ContextMember.context_id == CatalogContext.id,
            ContextMember.conflict_at.isnot(None),
        )
        .exists()
    )


def _members_exist():
    return (
        sa.select(sa.literal(1))
        .select_from(ContextMember)
        .where(ContextMember.context_id == CatalogContext.id)
        .exists()
    )


def _member_count_subquery():
    """Коррелированный скаляр числа членств контекста — та же форма
    корреляции (`ContextMember.context_id == CatalogContext.id`), что у
    `_stale_exists`/`_conflict_exists`/`_members_exist` выше, только `COUNT`,
    а не `EXISTS` (спека §2.8 п. 1: `member_count` строки списка)."""
    return (
        sa.select(sa.func.count(ContextMember.position_item_id))
        .select_from(ContextMember)
        .where(ContextMember.context_id == CatalogContext.id)
        .scalar_subquery()
    )


def _work_category_path_from_row(row) -> list[WorkCategoryRef]:
    """Путь классификатора строки страницы — родители статьи корзины, от
    корня, БЕЗ самой статьи (спека §2.8 п. 1). Строка несёт код/название
    родителя и прародителя (два внешних соединения `list_contexts`); `None`
    означает «на этом уровне предка нет» и пропускается — статья первого
    уровня или отсутствующая статья дают пустой список, а не список с
    дырами."""
    path: list[WorkCategoryRef] = []
    if row.grandparent_category_code is not None:
        path.append(
            WorkCategoryRef(code=row.grandparent_category_code, title=row.grandparent_category_title)
        )
    if row.parent_category_code is not None:
        path.append(
            WorkCategoryRef(code=row.parent_category_code, title=row.parent_category_title)
        )
    return path


def _work_category_path(db: Session, category_id: int | None) -> list[WorkCategoryRef]:
    """Путь классификатора статьи КАРТОЧКИ — то же значение и та же форма,
    что `work_category_path` строки списка (спека §2.8 п. 2), но отдельным
    константным запросом: у карточки нет готовой строки страницы
    `list_contexts`, куда навешаны эти два соединения. Один запрос, не
    растущий вместе с числом членств/путей карточки — инвариант карточки
    защищает число запросов ровно от этого роста, а не от лишней константы."""
    if category_id is None:
        return []
    parent_category = aliased(WorkCategory)
    grandparent_category = aliased(WorkCategory)
    row = db.execute(
        sa.select(
            parent_category.code.label("parent_category_code"),
            parent_category.title.label("parent_category_title"),
            grandparent_category.code.label("grandparent_category_code"),
            grandparent_category.title.label("grandparent_category_title"),
        )
        .select_from(WorkCategory)
        .outerjoin(parent_category, parent_category.id == WorkCategory.parent_id)
        .outerjoin(grandparent_category, grandparent_category.id == parent_category.parent_id)
        .where(WorkCategory.id == category_id)
    ).first()
    if row is None:
        return []
    return _work_category_path_from_row(row)


def _member_group_sort_key(*, chapter_item_ids: list[int], count: int, path: list[str]):
    """Порядок групп членств/устаревших групп (спека §2.8 п. 2): по убыванию
    числа, затем путь лексикографически, группа «без раздела»
    (`chapter_item_ids=[]`) — ПОСЛЕДНЕЙ при любом её размере (первый элемент
    ключа — булев «это группа без раздела», он сильнее счёта). Пустой список
    однозначно отличает её от группы с разделами (редакция 3): группа с
    разделами всегда несёт хотя бы один id — путь чужого раздела не может
    совпасть с путём «без раздела» (`path=[]`), см. `MemberPath` докстроку.
    Один и тот же ключ используется и для `member_paths` (число —
    `member_count`), и для `stale_groups` (число — `count`, он же
    `stale_count` той же строки) — спека не называет для второго списка
    иного порядка."""
    return (not chapter_item_ids, -count, path)


def _member_paths_and_stale_groups(
    db: Session, *, context_id: int
) -> tuple[list[MemberPath], list[StaleGroup], dict[int, tuple[str, ...]]]:
    """Членства карточки, сгруппированные по ТЕКСТУ ближайшего пути (спека
    §2.8 п. 2, редакция 3 — «сверка с макетом», абзац у начала спеки), и
    устаревшие ПЕРЕНОСИМЫЕ группы из ТЕХ ЖЕ строк: `stale_groups.count` —
    только `STALE AND conflict_at IS NULL`, то же множество, что берёт
    пакетный перенос `transfer_stale_group` (`services/context_operations.py`);
    устаревшее конфликтное членство не входит ни в `count`, ни в саму запись
    `stale_groups`, если группа без него не набрала ни одного переносимого
    членства — карточка не должна предлагать перенос того, что перенести
    нельзя (см. `StaleGroup` докстрока выше).

    ОДИН агрегирующий запрос `GROUP BY position_items.chapter_item_id`
    (НЕ по тексту пути — путь строится позже, в памяти) — тот же запрос, что
    и до редакции 3, несущий заодно `work_category_id` строки-раздела (плюс
    её код/название) — ЭТУ ЖЕ статью `stale_groups` называет целью переноса,
    отдельного запроса под неё нет. Пути строятся ПОСЛЕ, одним вызовом
    `chapter_paths()` для всех уникальных `chapter_item_id` разом
    (`services/context_routing.py`) — не через `chapter_context()` на каждую
    группу (докстрока `chapter_paths`).

    **Слияние по тексту пути (редакция 3)** — строки ОДНОГО агрегирующего
    запроса, чей путь (сверху вниз) совпал буквально, сливаются в ОДНУ
    группу экрана В ПАМЯТИ, без нового запроса: `chapter_item_ids` группы —
    разделы всех слившихся строк по возрастанию, счётчики — суммы. Для
    `member_paths` ключ слияния — только путь; для `stale_groups` — пара
    «путь → `target_category_id`» (тот же раздел с иной целью — другая
    запись). `chapter_item_id IS NULL` — группа «без раздела» (`path=[]`):
    `GROUP BY` уже сливает ВСЕ такие членства в ОДНУ строку агрегирующего
    запроса (SQL группирует `NULL` вместе) — второй группы `[]` в слиянии
    появиться не может, `chapter_item_ids` этой группы — `[]` всегда.
    Сортировка (`_member_group_sort_key`) кладёт её ПОСЛЕДНЕЙ. Сумма
    `member_count` групп равна `member_count` карточки ПО ПОСТРОЕНИЮ:
    слияние в памяти не теряет строк агрегирующего запроса (каждая входит
    ровно в одну группу результата), а сам он теряет не больше, чем
    `COUNT(*)` без `GROUP BY`.

    Третий элемент кортежа — `paths_by_chapter` (путь СВЕРХУ ВНИЗ по
    `chapter_item_id`), тот же словарь, что строит `chapter_paths()`, отданный
    вызывающему коду НАПРЯМУЮ: карточке (`representative_work_title`, спека
    §2.8 п. 2) нужен путь раздела представительного членства, а он уже
    входит в этот же вызов `chapter_paths()` по построению — представитель
    сам является одним из членств, сгруппированных здесь, второго запроса
    под его путь заводить не нужно.
    """
    chapter = aliased(PositionItem)
    category = aliased(WorkCategory)
    rows = db.execute(
        sa.select(
            PositionItem.chapter_item_id,
            sa.func.count().label("member_count"),
            sa.func.count(
                sa.case((ContextMember.membership_state == MembershipState.STALE.value, 1))
            ).label("stale_count"),
            sa.func.count(
                sa.case((ContextMember.conflict_at.isnot(None), 1))
            ).label("conflict_count"),
            sa.func.count(
                sa.case(
                    (
                        sa.and_(
                            ContextMember.membership_state == MembershipState.STALE.value,
                            ContextMember.conflict_at.is_(None),
                        ),
                        1,
                    )
                )
            ).label("transferable_stale_count"),
            chapter.work_category_id.label("target_category_id"),
            category.code.label("target_category_code"),
            category.title.label("target_category_title"),
        )
        .select_from(ContextMember)
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .outerjoin(chapter, chapter.id == PositionItem.chapter_item_id)
        .outerjoin(category, category.id == chapter.work_category_id)
        .where(ContextMember.context_id == context_id)
        .group_by(
            PositionItem.chapter_item_id,
            chapter.work_category_id,
            category.code,
            category.title,
        )
    ).all()

    chapter_ids = [row.chapter_item_id for row in rows if row.chapter_item_id is not None]
    paths_by_chapter = chapter_paths(db, chapter_ids)

    def _path_for(chapter_item_id: int | None) -> list[str]:
        if chapter_item_id is None:
            return []
        return list(paths_by_chapter[chapter_item_id])

    # Слияние по ТЕКСТУ пути (редакция 3, см. докстроку функции) — строится
    # В ПАМЯТИ над строками уже выполненного агрегирующего запроса, без
    # нового обращения к БД. `member_paths_by_key`: ключ — кортеж пути;
    # `stale_groups_by_key`: ключ — пара «кортеж пути → target_category_id».
    member_paths_by_key: dict[tuple[str, ...], dict] = {}
    stale_groups_by_key: dict[tuple[tuple[str, ...], int | None], dict] = {}
    for row in rows:
        path = _path_for(row.chapter_item_id)
        path_key = tuple(path)

        mp_group = member_paths_by_key.setdefault(
            path_key,
            {
                "chapter_item_ids": [],
                "path": path,
                "member_count": 0,
                "stale_count": 0,
                "conflict_count": 0,
            },
        )
        if row.chapter_item_id is not None:
            mp_group["chapter_item_ids"].append(row.chapter_item_id)
        mp_group["member_count"] += row.member_count
        mp_group["stale_count"] += row.stale_count
        mp_group["conflict_count"] += row.conflict_count

        if row.transferable_stale_count > 0:
            sg_key = (path_key, row.target_category_id)
            sg_group = stale_groups_by_key.setdefault(
                sg_key,
                {
                    "chapter_item_ids": [],
                    "path": path,
                    "count": 0,
                    "target_category_id": row.target_category_id,
                    "target_category_code": row.target_category_code,
                    "target_category_title": row.target_category_title,
                },
            )
            if row.chapter_item_id is not None:
                sg_group["chapter_item_ids"].append(row.chapter_item_id)
            sg_group["count"] += row.transferable_stale_count

    member_paths: list[MemberPath] = [
        MemberPath(
            chapter_item_ids=sorted(group["chapter_item_ids"]),
            path=group["path"],
            member_count=group["member_count"],
            stale_count=group["stale_count"],
            conflict_count=group["conflict_count"],
        )
        for group in member_paths_by_key.values()
    ]
    member_paths.sort(
        key=lambda mp: _member_group_sort_key(
            chapter_item_ids=mp["chapter_item_ids"], count=mp["member_count"], path=mp["path"]
        )
    )

    stale_groups: list[StaleGroup] = [
        StaleGroup(
            chapter_item_ids=sorted(group["chapter_item_ids"]),
            path=group["path"],
            count=group["count"],
            target_category_id=group["target_category_id"],
            target_category_code=group["target_category_code"],
            target_category_title=group["target_category_title"],
        )
        for group in stale_groups_by_key.values()
    ]
    stale_groups.sort(
        key=lambda sg: _member_group_sort_key(
            chapter_item_ids=sg["chapter_item_ids"], count=sg["count"], path=sg["path"]
        )
    )

    return member_paths, stale_groups, paths_by_chapter


def _escape_ilike(text: str) -> str:
    """Экранирует `%`, `_` и сам экранирующий символ `\\` перед подстановкой
    в `ILIKE`: без этого `catalog_query`, содержащий
    буквальный `%` или `_` (в названии работы такое бывает — «40% раствор»,
    «шпонка_A»), трактуется как метасимвол шаблона, а не как искомый текст.
    Порядок замен важен: сам `\\` экранируется ПЕРВЫМ, иначе экранирующие
    слэши, вставленные для `%`/`_`, экранировались бы повторно."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _context_query(filters: ContextFilters):
    """Один запрос-строитель: только скаляры через `join`/`outerjoin`, ни
    одной ленивой ORM-связи — см. докстринг модуля про N+1."""
    stmt = (
        sa.select(
            CatalogContext.id,
            CatalogContext.bucket_id,
            CatalogContext.is_default,
            CatalogContext.semantic_kind,
            CatalogContext.semantic_kind_source,
            CatalogContext.name_role,
            CatalogContext.name_role_source,
            CatalogContext.semantic_state,
            CatalogContext.comparability_reason,
            CatalogContext.work_family_id,
            WorkFamily.title.label("family_title"),
            ContextBucket.work_category_id,
            WorkCategory.code.label("work_category_code"),
            WorkCategory.title.label("work_category_title"),
            CatalogPosition.id.label("catalog_position_id"),
            CatalogPosition.standard_job_title,
            UnitOfMeasure.code.label("unit_code"),
            # Символ единицы (спека §2.8, уточнение 27.09.2026) — тем же
            # внешним соединением, что уже несёт `unit_code`; третьего
            # запроса не добавляет (инварианты «ровно два» ниже не трогаются).
            UnitOfMeasure.symbol.label("unit_symbol"),
            CatalogContext.archived_at,
        )
        .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
        .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
        .outerjoin(UnitOfMeasure, UnitOfMeasure.id == CatalogPosition.unit_id)
        .outerjoin(WorkCategory, WorkCategory.id == ContextBucket.work_category_id)
        .outerjoin(WorkFamily, WorkFamily.id == CatalogContext.work_family_id)
    )
    if filters.catalog_query:
        stmt = stmt.where(
            CatalogPosition.standard_job_title.ilike(
                f"%{_escape_ilike(filters.catalog_query)}%", escape="\\"
            )
        )
    if filters.work_category_id is not None:
        stmt = stmt.where(ContextBucket.work_category_id == filters.work_category_id)
    if filters.semantic_kind is not None:
        stmt = stmt.where(CatalogContext.semantic_kind == filters.semantic_kind)
    if filters.name_role is not None:
        stmt = stmt.where(CatalogContext.name_role == filters.name_role)
    if filters.semantic_state is not None:
        stmt = stmt.where(CatalogContext.semantic_state == filters.semantic_state)

    # Три независимые оси (см. докстринг модуля) — `True`/`False` фильтруют
    # обе стороны, `None` фильтр не применяет вовсе.
    if filters.has_stale_members is True:
        stmt = stmt.where(_stale_exists())
    elif filters.has_stale_members is False:
        stmt = stmt.where(~_stale_exists())
    if filters.has_conflicting_members is True:
        stmt = stmt.where(_conflict_exists())
    elif filters.has_conflicting_members is False:
        stmt = stmt.where(~_conflict_exists())
    if filters.has_no_members is True:
        stmt = stmt.where(~_members_exist())
    elif filters.has_no_members is False:
        stmt = stmt.where(_members_exist())

    return stmt


def list_contexts(db: Session, *, filters: ContextFilters, limit: int, offset: int) -> dict:
    """Очередь контекстов — РОВНО ДВА запроса, независимо от `limit`/числа
    найденных строк (план, задача 12, «Утверждения»): счётчик и страница.

    Счётчик считает по «чистому» `stmt` (без добавок ниже) — колонки
    `member_count`/признаки/путь навешаны ТОЛЬКО на страницу
    (`page_stmt = stmt.add_columns(...)`), поэтому подсчёт итога не платит за
    коррелированные подзапросы и лишние соединения, а запросов по-прежнему
    два (спека §2.8 п. 1)."""
    stmt = _context_query(filters)
    total = db.execute(
        sa.select(sa.func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()

    parent_category = aliased(WorkCategory)
    grandparent_category = aliased(WorkCategory)
    page_stmt = (
        stmt.add_columns(
            _member_count_subquery().label("member_count"),
            _stale_exists().label("has_stale_members"),
            _conflict_exists().label("has_conflicting_members"),
            parent_category.code.label("parent_category_code"),
            parent_category.title.label("parent_category_title"),
            grandparent_category.code.label("grandparent_category_code"),
            grandparent_category.title.label("grandparent_category_title"),
        )
        .outerjoin(parent_category, parent_category.id == WorkCategory.parent_id)
        .outerjoin(grandparent_category, grandparent_category.id == parent_category.parent_id)
    )
    rows = db.execute(page_stmt.order_by(CatalogContext.id).offset(offset).limit(limit)).all()
    items = [
        {
            "id": row.id,
            "bucket_id": row.bucket_id,
            "is_default": row.is_default,
            "semantic_kind": row.semantic_kind,
            "semantic_kind_source": row.semantic_kind_source,
            "name_role": row.name_role,
            "name_role_source": row.name_role_source,
            "semantic_state": row.semantic_state,
            "comparability_reason": row.comparability_reason,
            "work_family_id": row.work_family_id,
            "family_title": row.family_title,
            "work_category_id": row.work_category_id,
            "work_category_code": row.work_category_code,
            "work_category_title": row.work_category_title,
            "catalog_position_id": row.catalog_position_id,
            "standard_job_title": row.standard_job_title,
            "unit_code": row.unit_code,
            "unit_symbol": row.unit_symbol,
            "archived_at": row.archived_at,
            "member_count": row.member_count,
            "has_stale_members": row.has_stale_members,
            "has_conflicting_members": row.has_conflicting_members,
            "work_category_path": _work_category_path_from_row(row),
        }
        for row in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
#  Контексты — карточка
# ---------------------------------------------------------------------------

def context_card(db: Session, *, context_id: int) -> dict | None:
    """Карточка контекста (спека §2.10): написание (нормализованное имя +
    единица), статья и её источник (`file`/`manual` — источник разноса
    строки-раздела, читается у ЛЮБОГО одного членства контекста: внутри
    одной корзины написание и эффективная статья одинаковы по построению,
    `services/context_routing.py`), вид с источником, роль имени с
    источником и версией словаря, `comparability_reason`, семья с
    источником назначения, число членств (`member_count`, всегда полное),
    путь классификатора статьи корзины (`work_category_path` — родители,
    от корня, без самой статьи, спека §2.8 п. 2, то же значение, что у
    строки списка), работу по разделу представительной позиции
    (`representative_work_title` — только при сохранённой роли
    `LOCATION_ONLY`, от СОХРАНЁННОЙ роли, а не повторной классификацией; см.
    комментарий у вычисления ниже, спека §2.8 п. 2), группы членств по тексту
    пути ближайшего раздела (`member_paths`, редакция 3) и устаревшие группы под цель переноса
    (`stale_groups` — спека §2.8 п. 2,
    `crud/semantic.py::_member_paths_and_stale_groups`), соседей по корзине
    (`bucket_contexts` — id, `is_default`, `archived_at`, `member_count`
    КАЖДОГО контекста той же `bucket_id`, включая архивные; цель слияния/
    переноса выбирается из живых соседей) и журнал событий (по времени).
    Членства поштучно карточка БОЛЬШЕ НЕ несёт (`members`/`members_truncated`
    удалены спекой `2026-09-25-families-screen-design.md` §2.8 п. 5):
    единственным потребителем был `ContextCard.tsx` фичи 1, а после
    переделки экран читает членства ПОСТРАНИЧНО запросом группы
    (`list_group_members`/`list_group_member_ids` ниже), а не обрезанным
    списком карточки.

    `None`, если контекст не найден — роутер переводит это в 404.

    Raises:
        RoutingError: цикл `chapter_item_id` среди разделов членств этой
            карточки (`chapter_paths()`, `services/context_routing.py`) —
            роутер переводит в доменную ошибку `422`
            (`routers/semantic.py::_read_domain_errors`), не `500`.
    """
    context = db.get(CatalogContext, context_id)
    if context is None:
        return None

    bucket = db.get(ContextBucket, context.bucket_id)
    catalog_position = db.get(CatalogPosition, bucket.catalog_position_id)
    unit_code = catalog_position.unit.code if catalog_position.unit_id is not None else None
    # Символ единицы (спека §2.8, уточнение 27.09.2026) — тот же ленивый
    # доступ `catalog_position.unit`, что уже несёт `unit_code`: объект уже
    # загружен строкой выше, второго запроса нет.
    unit_symbol = catalog_position.unit.symbol if catalog_position.unit_id is not None else None

    work_category = (
        db.get(WorkCategory, bucket.work_category_id)
        if bucket.work_category_id is not None
        else None
    )

    family = db.get(WorkFamily, context.work_family_id) if context.work_family_id is not None else None

    # `ORDER BY position_item_id` — детерминированный выбор членства:
    # без порядка `LIMIT 1` возвращает произвольную
    # строку, и при разных источниках статьи (file/manual) у членств одной
    # корзины ответ был бы недетерминирован. Младший `position_item_id` —
    # самый простой воспроизводимый порядок; спека не
    # называет иного критерия «какое членство представляет корзину» — тот же
    # критерий, что `_classify_new_context_semantics`
    # (`services/context_operations.py`) использует для цепочки представителя
    # при разделении. `representative_chapter_item_id` — раздел ЭТОГО ЖЕ
    # представительного членства (спека §2.8 п. 2,
    # `representative_work_title` ниже) — снят ОДНИМ и тем же запросом, а не
    # отдельным чтением.
    chapter = aliased(PositionItem)
    representative_row = db.execute(
        sa.select(
            chapter.category_source,
            PositionItem.chapter_item_id.label("representative_chapter_item_id"),
        )
        .select_from(ContextMember)
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .outerjoin(chapter, chapter.id == PositionItem.chapter_item_id)
        .where(ContextMember.context_id == context_id)
        .order_by(ContextMember.position_item_id)
        .limit(1)
    ).first()
    work_category_source = (
        representative_row.category_source if representative_row is not None else None
    )
    representative_chapter_item_id = (
        representative_row.representative_chapter_item_id
        if representative_row is not None
        else None
    )

    member_count = db.execute(
        sa.select(sa.func.count())
        .select_from(ContextMember)
        .where(ContextMember.context_id == context_id)
    ).scalar_one()

    work_category_path = _work_category_path(db, bucket.work_category_id)
    member_paths, stale_groups, paths_by_chapter = _member_paths_and_stale_groups(
        db, context_id=context_id
    )

    # `representative_work_title` — ОТ СОХРАНЁННОЙ роли, не повторной
    # классификацией (спека §2.8 п. 2): `set_name_role`
    # (`services/work_families.py`) пишет роль и ничего не пересчитывает,
    # поэтому `classify_name_role` после ручной смены роли вправе вернуть
    # другое значение — вызов дал бы подпись, противоречащую решению
    # оператора. При любой роли, кроме `LOCATION_ONLY`, — `None`; путь раздела
    # представителя уже посчитан вызовом `chapter_paths()` выше
    # (`paths_by_chapter`) — представитель сам входит в число членств,
    # сгруппированных `_member_paths_and_stale_groups`, второго запроса нет.
    # Представитель без раздела (`representative_chapter_item_id is None`) не
    # находит ключа в `paths_by_chapter` (ключи там — только настоящие
    # `chapter_item_id`, `None` среди них никогда нет) — `.get` тем же путём
    # отдаёт `None`, отдельной проверки не требуется. Цепочка над разделом
    # представителя, состоящая целиком из мест, даёт тот же `None` уже внутри
    # `nearest_working_chapter`.
    representative_work_title: str | None = None
    if context.name_role == NameRole.LOCATION_ONLY.value:
        representative_path = paths_by_chapter.get(representative_chapter_item_id)
        if representative_path:
            representative_work_title = nearest_working_chapter(
                tuple(reversed(representative_path))
            )

    # Соседи по корзине (та же `bucket_id`) — цель для слияния/переноса
    # (спека §2.4: разделение оставляет несколько контекстов на одной
    # корзине, слияние допустимо только внутри неё). Живые И архивные — оба
    # нужны экрану: архивные не предлагаются целью, но названы должны быть
    # видимо ПОЧЕМУ их нет в выборе, а не молчаливым отсутствием. ОДИН
    # агрегирующий запрос (`GROUP BY`, `outerjoin` на членства) — не растёт
    # с числом соседей, тот же приём, что у списка членств выше.
    bucket_context_rows = db.execute(
        sa.select(
            CatalogContext.id,
            CatalogContext.is_default,
            CatalogContext.archived_at,
            sa.func.count(ContextMember.position_item_id).label("member_count"),
        )
        .select_from(CatalogContext)
        .outerjoin(ContextMember, ContextMember.context_id == CatalogContext.id)
        .where(CatalogContext.bucket_id == context.bucket_id)
        .group_by(CatalogContext.id)
        .order_by(CatalogContext.id)
    ).all()

    # Членства поштучно карточка БОЛЬШЕ НЕ читает (спека §2.8 п. 5): экран
    # получает их постранично запросом группы (`list_group_members`/
    # `list_group_member_ids` ниже) — это и убирает один запрос из карточки
    # (константа `_CARD_QUERY_COUNT`, `tests/integration/test_semantic_api.py`,
    # уменьшена ровно на него).
    events = (
        db.execute(
            sa.select(SemanticEvent)
            .where(SemanticEvent.context_id == context_id)
            .order_by(SemanticEvent.created_at, SemanticEvent.id)
        )
        .scalars()
        .all()
    )

    return {
        "id": context.id,
        "bucket_id": context.bucket_id,
        "is_default": context.is_default,
        "archived_at": context.archived_at,
        "catalog_position_id": catalog_position.id,
        "standard_job_title": catalog_position.standard_job_title,
        "unit_id": catalog_position.unit_id,
        "unit_code": unit_code,
        "unit_symbol": unit_symbol,
        "work_category_id": bucket.work_category_id,
        "work_category_code": work_category.code if work_category is not None else None,
        "work_category_title": work_category.title if work_category is not None else None,
        "work_category_source": work_category_source,
        "semantic_kind": context.semantic_kind,
        "semantic_kind_source": context.semantic_kind_source,
        "semantic_kind_by": context.semantic_kind_by,
        "semantic_kind_at": context.semantic_kind_at,
        "name_role": context.name_role,
        "name_role_source": context.name_role_source,
        "name_role_by": context.name_role_by,
        "name_role_at": context.name_role_at,
        "place_dictionary_version": context.place_dictionary_version,
        "comparability_reason": context.comparability_reason,
        "semantic_state": context.semantic_state,
        "work_family_id": context.work_family_id,
        "family_title": family.title if family is not None else None,
        "family_source": context.family_source,
        "family_by": context.family_by,
        "family_at": context.family_at,
        "member_count": member_count,
        "work_category_path": work_category_path,
        "representative_work_title": representative_work_title,
        "member_paths": member_paths,
        "stale_groups": stale_groups,
        "bucket_contexts": [
            {
                "id": row.id,
                "is_default": row.is_default,
                "archived_at": row.archived_at,
                "member_count": row.member_count,
            }
            for row in bucket_context_rows
        ],
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "payload": event.payload,
                "actor_id": event.actor_id,
                "created_at": event.created_at,
            }
            for event in events
        ],
    }


# ---------------------------------------------------------------------------
#  Членства группы — постраничный список и полный набор id (спека
#  `2026-09-25-families-screen-design.md` §2.8 п. 3)
# ---------------------------------------------------------------------------

#: Состояние членства, по которому фильтрует список группы (спека §2.8 п. 3):
#: `stale` — `membership_state = STALE`, `conflict` — `conflict_at IS NOT
#: NULL`. Оси независимы (докстрока модуля выше про три ЖЕ независимых
#: признака очереди) — одно и то же членство может быть и STALE, и
#: конфликтным разом, а фильтр здесь выбирает РОВНО одну из осей, не обе.
GroupState = Literal["all", "stale", "conflict"]


@dataclass(frozen=True)
class GroupSelector:
    """Группа членств контекста — по ближайшему разделу ЕЁ ПОЗИЦИИ (тот же
    ключ, что группирует `member_paths`, спека §2.8 п. 2, 3, редакция 3):
    группа экрана — ТЕКСТ пути, а не один раздел, поэтому `chapter_item_ids`
    несёт ВСЕ разделы группы (кортеж, может быть длиннее одного — одинаковый
    путь у разных смет сливается в одну группу `member_paths`, и галочка
    группы обязана раскрыть позиции ЛЮБОГО из её разделов); пустой кортеж —
    «раздел не выбран». `no_chapter=True` — только позиции БЕЗ раздела
    (`chapter_item_id IS NULL`, схемой допустимо); ни то ни другое
    (`chapter_item_ids=()`, `no_chapter=False`) — ВЕСЬ контекст, тем же
    путём экран берёт id всех конфликтных членств для «Принять решение
    цели» (спека §2.8 п. 3). Непустой `chapter_item_ids` вместе с
    `no_chapter=True` — противоречие («разделы X, Y» и «без раздела»
    одновременно невозможны); роутер отвергает такой вход `422` ДО вызова
    этого модуля, здесь предполагается уже провалидированный вход."""

    chapter_item_ids: tuple[int, ...]
    no_chapter: bool


def _group_base_query(*, context_id: int, selector: GroupSelector, state: GroupState):
    """Базовый фильтр членств ОДНОЙ группы контекста — `context_id`,
    ближайший раздел позиции (`GroupSelector`) и состояние (`GroupState`) —
    единственное место, где эти три условия записаны. Колонка всего одна,
    `position_item_id`: `list_group_member_ids` использует запрос НАПРЯМУЮ
    (список id не нуждается в join'ах на `Proposal`/`Lot` — спека §2.8 п. 3,
    «id — восемь байт»), `_group_members_query` достраивает поверх него
    строку членства `list_group_members` (см. её докстроку)."""
    stmt = (
        sa.select(ContextMember.position_item_id)
        .select_from(ContextMember)
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .where(ContextMember.context_id == context_id)
    )
    if selector.chapter_item_ids:
        stmt = stmt.where(PositionItem.chapter_item_id.in_(selector.chapter_item_ids))
    elif selector.no_chapter:
        stmt = stmt.where(PositionItem.chapter_item_id.is_(None))
    if state == "stale":
        stmt = stmt.where(ContextMember.membership_state == MembershipState.STALE.value)
    elif state == "conflict":
        stmt = stmt.where(ContextMember.conflict_at.isnot(None))
    return stmt


def _group_members_query(*, context_id: int, selector: GroupSelector, state: GroupState):
    """Строка членства ОДНОЙ группы — форма колонок, что отдаёт
    `list_group_members` (спека §2.8 п. 3). До удаления членств поштучно из
    карточки (§2.8 п. 5) тот же набор колонок несла и `context_card`
    (`members[]`); единственный потребитель этого запроса теперь —
    `list_group_members`. Достраивает `_group_base_query` join'ами на
    `Proposal`/`Lot` (название работы по смете, id сметы) и колонками
    состояния членства — `join`, не `outerjoin`: у членства всегда есть
    позиция, у позиции всегда предложение и лот."""
    return (
        _group_base_query(context_id=context_id, selector=selector, state=state)
        .add_columns(
            PositionItem.job_title_in_proposal.label("job_title"),
            Lot.estimate_id,
            ContextMember.membership_state,
            ContextMember.conflict_at,
            ContextMember.conflict_from_context_id,
            ContextMember.routed_by,
        )
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
    )


def _member_row_to_dict(row) -> dict:
    """Форма строки членства, что несёт `list_group_members` (спека §2.8
    п. 3) — единственный потребитель `_group_members_query` теперь; до
    удаления членств поштучно из карточки (§2.8 п. 5) ту же форму несла и
    `members[]` карточки."""
    return {
        "position_item_id": row.position_item_id,
        "job_title": row.job_title,
        "estimate_id": row.estimate_id,
        "membership_state": row.membership_state,
        "conflict_at": row.conflict_at,
        "conflict_from_context_id": row.conflict_from_context_id,
        "routed_by": row.routed_by,
    }


def _context_exists_and_total(db: Session, *, context_id: int, query) -> tuple[bool, int]:
    """Существование контекста и итог фильтра ОДНИМ SELECT (спека §2.8 п. 3:
    «оба — ровно два запроса»): скаляр «контекст существует» и скаляр
    `COUNT(*)` подзапроса `query` — оба вложенными скалярными подзапросами
    БЕЗ отдельного `FROM` у внешнего запроса (`SELECT (subq1), (subq2)`),
    то есть один текст SQL, один round-trip. Решение о 404 входит в этот же
    запрос: контекста нет — вызывающий код возвращает `None` БЕЗ второго
    запроса, а не тратит его на пустую страницу."""
    row = db.execute(
        sa.select(
            sa.select(sa.literal(1))
            .where(CatalogContext.id == context_id)
            .exists()
            .label("context_exists"),
            sa.select(sa.func.count())
            .select_from(query.subquery())
            .scalar_subquery()
            .label("total"),
        )
    ).one()
    return row.context_exists, row.total


def list_group_members(
    db: Session, *, context_id: int, selector: GroupSelector, state: GroupState,
    limit: int, offset: int,
) -> dict | None:
    """Постраничный список членств ОДНОЙ группы — спека §2.8 п. 3: галочка
    группы на экране раскрывает её позиции постранично ИМЕННО этим запросом —
    карточка (`context_card` выше) членств поштучно больше не несёт вовсе
    (§2.8 п. 5).

    РОВНО два запроса, включая решение о 404 (спека §2.8 п. 3: «оба — ровно
    два запроса»): первый — `_context_exists_and_total` (существование контекста и `total`
    ОДНИМ SELECT, см. её докстроку); контекста нет — `None` без второго
    запроса, роутер переводит в `404`. Второй — сама страница,
    `ORDER BY position_item_id` (тот же порядок, что у представителя
    карточки), `LIMIT`/`OFFSET`.
    """
    query = _group_members_query(context_id=context_id, selector=selector, state=state)
    context_exists, total = _context_exists_and_total(db, context_id=context_id, query=query)
    if not context_exists:
        return None

    rows = db.execute(
        query.order_by(ContextMember.position_item_id).offset(offset).limit(limit)
    ).all()
    return {
        "items": [_member_row_to_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def list_group_member_ids(
    db: Session, *, context_id: int, selector: GroupSelector, state: GroupState,
) -> dict | None:
    """Полный список id членств группы, БЕЗ обрезки (спека §2.8 п. 3: тот же
    набор, что галочка группы передаёт целиком в «Разделить…»/«Перенести…»,
    включая позиции, не загруженные на экран, — их тела уже принимают
    список произвольной длины). Та же дисциплина «ровно два запроса,
    включая 404», что `list_group_members` (см. её докстроку и
    `_context_exists_and_total`); второй запрос здесь — список id, не
    страница, и он ничем не ограничен."""
    query = _group_base_query(context_id=context_id, selector=selector, state=state)
    context_exists, total = _context_exists_and_total(db, context_id=context_id, query=query)
    if not context_exists:
        return None

    ids = db.execute(query.order_by(ContextMember.position_item_id)).scalars().all()
    return {"position_item_ids": list(ids), "total": total}
