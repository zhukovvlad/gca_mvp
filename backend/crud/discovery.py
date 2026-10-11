"""Чтение блока «Открыть семьи» и черновиков (спека 3б §2.4, §2.12): по единице —
состав охвата, оценка, последнее открытие и черновики последнего выполненного
открытия. Пишущая часть — `services/family_discovery.py` и
`services/discovery_drafts.py`; числа строки считает та же `discovery_scope`, что
тело запроса и запуск (один предикат на блок, preview, тело и результат).
"""
from __future__ import annotations

from collections import defaultdict

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import Settings
from crud.semantic_queue import money_str
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    DraftGroup,
    DraftStatus,
    FamilyCategory,
    FamilyCategoryProposal,
    FamilyDraft,
    FamilyDraftMember,
    FamilyStatus,
    SemanticJob,
    SemanticJobKind,
    UnitOfMeasure,
    WorkFamily,
)
from services.discovery_drafts import latest_discovery_job_id
from services.family_discovery import _preview_of, _scope_query, discovery_scope, is_in_scope

#: Сколько примеров наименований показывает группа.
DRAFT_EXAMPLES = 3


def _candidate_units(db: Session) -> set[int | None]:
    """Единицы, у которых есть что открывать: в охвате есть контекст либо есть
    активная семья без категории."""
    in_scope = set(db.execute(_scope_query(CatalogPosition.unit_id).distinct()).scalars())
    uncategorized = set(
        db.execute(
            sa.select(WorkFamily.unit_id)
            .where(
                WorkFamily.status == FamilyStatus.active.value,
                WorkFamily.family_category_id.is_(None),
            )
            .distinct()
        ).scalars()
    )
    return in_scope | uncategorized


def _actionable(db: Session, job_id: int) -> bool:
    """На экране последнего выполненного открытия ещё есть что решать (спека 3б §2.4): открытый
    черновик новой семьи, либо слитый или отброшенный (его можно вернуть), либо предложение
    категории семье, которой она ещё нужна (активна и без категории), либо член группы «Не
    работа», который СЕЙЧАС в охвате (`is_in_scope` без условия «почему в охвате», как у вида
    черновиков). Группы «в активные семьи» — справка и экран не держат. Один расчёт и для строки
    блока, и для вида черновиков."""
    restorable_or_open = db.execute(
        sa.select(FamilyDraft.id)
        .where(
            FamilyDraft.job_id == job_id,
            FamilyDraft.grp == DraftGroup.new.value,
            FamilyDraft.status.in_(
                (DraftStatus.open.value, DraftStatus.merged.value, DraftStatus.discarded.value)
            ),
        )
        .limit(1)
    ).first()
    if restorable_or_open is not None:
        return True
    needs_category = db.execute(
        sa.select(FamilyCategoryProposal.family_id)
        .join(WorkFamily, WorkFamily.id == FamilyCategoryProposal.family_id)
        .where(
            FamilyCategoryProposal.job_id == job_id,
            WorkFamily.status == FamilyStatus.active.value,
            WorkFamily.family_category_id.is_(None),
        )
        .limit(1)
    ).first()
    if needs_category is not None:
        return True
    not_work_members = set(
        db.execute(
            sa.select(FamilyDraftMember.context_id)
            .join(FamilyDraft, FamilyDraft.id == FamilyDraftMember.draft_id)
            .where(FamilyDraft.job_id == job_id, FamilyDraft.grp == DraftGroup.not_work.value)
        ).scalars()
    )
    return bool(is_in_scope(db, not_work_members, why_in_scope=False))


def _last_discovery(db: Session, unit_id: int | None) -> dict | None:
    job = db.execute(
        sa.select(SemanticJob)
        .where(
            SemanticJob.kind == SemanticJobKind.family_discovery.value,
            SemanticJob.unit_id.is_not_distinct_from(unit_id),
        )
        .order_by(SemanticJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if job is None:
        return None
    done_id = latest_discovery_job_id(db, unit_id)
    open_drafts = db.execute(
        sa.select(sa.func.count(FamilyDraft.id)).where(
            FamilyDraft.job_id == job.id,
            FamilyDraft.grp == DraftGroup.new.value,
            FamilyDraft.status == DraftStatus.open.value,
        )
    ).scalar_one()
    return {
        "job_id": job.id,
        "status": job.status,
        "at": job.updated_at.isoformat(),
        "open_drafts": open_drafts,
        # Экран черновиков принадлежит последнему ВЫПОЛНЕННОМУ открытию (спека 3б §2.4): новейшее
        # задание в ошибке, отменённое или идущее его не скрывает; его статус строка называет сама.
        "actionable": done_id is not None and _actionable(db, done_id),
    }


def discovery_units(db: Session, *, settings: Settings) -> list[dict]:
    """Строка на каждую единицу с непустым охватом или с семьями без категории
    (`None` — единица «без единицы», своя строка). Числа — из `discovery_scope`
    той же единицы; оценка — резерв и ожидаемая цена при попадании в кэш;
    последнее открытие — статус, время и число открытых черновиков новых семей.
    Порядок: по коду единицы, «без единицы» последней."""
    units = _candidate_units(db)
    codes: dict[int | None, str | None] = {None: None}
    real = {unit for unit in units if unit is not None}
    if real:
        codes.update(
            db.execute(
                sa.select(UnitOfMeasure.id, UnitOfMeasure.code).where(UnitOfMeasure.id.in_(real))
            ).all()
        )
    rows: list[dict] = []
    for unit_id in units:
        scope = discovery_scope(db, unit_id)
        preview, _rendered = _preview_of(db, scope, settings)
        counts = scope.counts
        rows.append(
            {
                "unit_id": unit_id,
                "unit_code": codes.get(unit_id),
                "systems": counts.systems,
                "new_family": counts.new_family,
                "bare": counts.bare,
                "names": counts.names,
                "uncategorized_families": counts.uncategorized_families,
                "active_families": preview.active_families,
                "reserve_usd": money_str(preview.reserve_usd),
                "expected_cached_usd": money_str(preview.expected_cached_usd),
                "last_discovery": _last_discovery(db, unit_id),
            }
        )
    rows.sort(key=lambda r: (r["unit_code"] is None, r["unit_code"] or "", r["unit_id"] or 0))
    return rows


def _titles_of(db: Session, context_ids: set[int]) -> dict[int, str]:
    if not context_ids:
        return {}
    return dict(
        db.execute(
            sa.select(CatalogContext.id, CatalogPosition.standard_job_title)
            .join(ContextBucket, ContextBucket.id == CatalogContext.bucket_id)
            .join(CatalogPosition, CatalogPosition.id == ContextBucket.catalog_position_id)
            .where(CatalogContext.id.in_(context_ids))
        ).all()
    )


def _by_count_then_title(counter: dict[str, int]) -> list[tuple[str, int]]:
    """Наименования: больше контекстов — раньше, затем по алфавиту."""
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


def discovery_drafts(db: Session, *, unit_id: int | None) -> dict | None:
    """Черновики последнего выполненного открытия единицы (`None` — единица «без
    единицы»); `None`, если выполненных открытий нет.

    «Строки» черновика — его члены и члены влитых в него (транзитивно, по
    `merged_into_draft_id`), которые СЕЙЧАС в охвате (`is_in_scope` без условия «почему
    в охвате», как у активации «не работы»): черновики — снимок, и член, получивший
    семью или ушедший в архив после ответа, не считается; семьи, заведённые
    самой активацией, членов из охвата не выводят. Состав:

    * `drafts` — открытые черновики новых семей по убыванию числа строк;
    * `folded` — слитые и отброшенные (свёрнутые строки с «Вернуть»);
    * `activated` — уже ставшие семьями;
    * `existing` — группы «в активную семью» (число строк, семья и её статус);
    * `not_work` — группа «Не работа» построчно по наименованию (число
      контекстов и их id для активации) либо `None`;
    * `rest` — число контекстов охвата (полный предикат), не попавших ни в одну
      группу открытия;
    * `category_proposals` — предложения категорий семьям, которым она ещё нужна
      (семья активна и без категории).

    Ссылки на семьи (`similar`, слияние, группа «в активную семью») несут статус
    семьи: на экране ссылка показывает его (спека 3б §2.3).
    """
    job_id = latest_discovery_job_id(db, unit_id)
    if job_id is None:
        return None

    drafts = (
        db.execute(
            sa.select(FamilyDraft).where(FamilyDraft.job_id == job_id).order_by(FamilyDraft.ordinal)
        )
        .scalars()
        .all()
    )
    members = db.execute(
        sa.select(FamilyDraftMember.draft_id, FamilyDraftMember.context_id).where(
            FamilyDraftMember.job_id == job_id
        )
    ).all()
    all_member_ids = {context_id for _draft, context_id in members}
    live = is_in_scope(db, all_member_ids, why_in_scope=False)
    titles = _titles_of(db, set(live))

    own: dict[int, list[int]] = defaultdict(list)
    for draft_id, context_id in members:
        if context_id in live:
            own[draft_id].append(context_id)
    merged_in: dict[int, list[int]] = defaultdict(list)
    for draft in drafts:
        if draft.merged_into_draft_id is not None:
            merged_in[draft.merged_into_draft_id].append(draft.id)

    def contexts_of(draft_id: int) -> list[int]:
        found: list[int] = []
        stack = [draft_id]
        seen: set[int] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            found.extend(own.get(current, ()))
            stack.extend(merged_in.get(current, ()))
        return found

    def examples_of(context_ids: list[int]) -> list[str]:
        counter: dict[str, int] = defaultdict(int)
        for context_id in context_ids:
            counter[titles[context_id]] += 1
        return [title for title, _count in _by_count_then_title(counter)[:DRAFT_EXAMPLES]]

    proposals = (
        db.execute(
            sa.select(FamilyCategoryProposal).where(
                FamilyCategoryProposal.job_id == job_id,
            )
        )
        .scalars()
        .all()
    )
    family_ids: set[int] = set()
    for draft in drafts:
        family_ids.update(
            f
            for f in (
                draft.existing_family_id,
                draft.similar_family_id,
                draft.merged_into_family_id,
                draft.activated_family_id,
            )
            if f is not None
        )
    family_ids.update(p.family_id for p in proposals)
    families = {}
    if family_ids:
        families = {
            row.id: row
            for row in db.execute(
                sa.select(
                    WorkFamily.id,
                    WorkFamily.title,
                    WorkFamily.definition,
                    WorkFamily.status,
                    WorkFamily.family_category_id,
                ).where(WorkFamily.id.in_(family_ids))
            ).all()
        }
    categories = {
        row.id: row.title for row in db.execute(sa.select(FamilyCategory.id, FamilyCategory.title))
    }

    def family_title(family_id: int | None) -> str | None:
        return families[family_id].title if family_id in families else None

    def family_status(family_id: int | None) -> str | None:
        return families[family_id].status if family_id in families else None

    def draft_row(draft: FamilyDraft) -> dict:
        context_ids = contexts_of(draft.id)
        return {
            "id": draft.id,
            "ordinal": draft.ordinal,
            "status": draft.status,
            "title": draft.title,
            "definition": draft.definition,
            "family_category_id": draft.family_category_id,
            "family_category_title": categories.get(draft.family_category_id),
            "similar_family_id": draft.similar_family_id,
            "similar_family_title": family_title(draft.similar_family_id),
            "similar_family_status": family_status(draft.similar_family_id),
            "merged_into_draft_id": draft.merged_into_draft_id,
            "merged_into_family_id": draft.merged_into_family_id,
            "merged_into_family_title": family_title(draft.merged_into_family_id),
            "merged_into_family_status": family_status(draft.merged_into_family_id),
            "activated_family_id": draft.activated_family_id,
            "rows": len(context_ids),
            "examples": examples_of(context_ids),
            "edited_at": draft.edited_at,
        }

    new_rows = [draft_row(d) for d in drafts if d.grp == DraftGroup.new.value]
    open_rows = sorted(
        (r for r in new_rows if r["status"] == DraftStatus.open.value),
        key=lambda r: (-r["rows"], r["ordinal"]),
    )
    folded = [
        r
        for r in new_rows
        if r["status"] in (DraftStatus.merged.value, DraftStatus.discarded.value)
    ]
    activated = [r for r in new_rows if r["status"] == DraftStatus.activated.value]

    existing = [
        {
            "id": d.id,
            "family_id": d.existing_family_id,
            "family_title": family_title(d.existing_family_id),
            "family_status": family_status(d.existing_family_id),
            "rows": len(own.get(d.id, ())),
        }
        for d in drafts
        if d.grp == DraftGroup.existing.value
    ]

    not_work = None
    for draft in drafts:
        if draft.grp == DraftGroup.not_work.value:
            by_title: dict[str, list[int]] = defaultdict(list)
            for context_id in own.get(draft.id, ()):
                by_title[titles[context_id]].append(context_id)
            not_work = {
                "id": draft.id,
                "names": [
                    {"title": title, "contexts": len(ids), "context_ids": sorted(ids)}
                    for title, ids in sorted(by_title.items(), key=lambda i: (-len(i[1]), i[0]))
                ],
            }

    # Остаток — не потребитель снимка: это нынешний неоткрытый остаток единицы
    # (в охвате сейчас по полному предикату и не член групп открытия).
    rest = len(discovery_scope(db, unit_id).context_ids - all_member_ids)

    category_proposals = [
        {
            "family_id": p.family_id,
            "family_title": families[p.family_id].title,
            "family_definition": families[p.family_id].definition,
            "family_category_id": p.family_category_id,
            "family_category_title": categories.get(p.family_category_id),
        }
        for p in sorted(proposals, key=lambda p: p.family_id)
        if p.family_id in families
        and families[p.family_id].status == FamilyStatus.active.value
        and families[p.family_id].family_category_id is None
    ]

    opened_at = db.execute(
        sa.select(SemanticJob.updated_at).where(SemanticJob.id == job_id)
    ).scalar_one()
    return {
        "job_id": job_id,
        "unit_id": unit_id,
        "opened_at": opened_at.isoformat(),
        "drafts": open_rows,
        "folded": folded,
        "activated": activated,
        "existing": existing,
        "not_work": not_work,
        "rest": rest,
        "category_proposals": category_proposals,
        "actionable": _actionable(db, job_id),
    }
