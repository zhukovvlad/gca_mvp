"""Действия над черновиками открытия семей (спека 3б §2.4; решения 6, 19, 20).

Черновик — группа ответа последнего выполненного открытия единицы. Человек правит
имя, определение и категорию, сливает черновик с другим черновиком или с активной
семьёй, отбрасывает его и возвращает слитый или отброшенный обратно. Действия
идут только над группой `new` последнего открытия: черновик прежнего открытия —
`discovery_run_superseded`, не `open` — `draft_not_open`.

Порядок блокировок общий для фичи (решение 20): категория, на которую правка
меняет черновик, `FOR SHARE`; затем черновик и цель слияния `FOR UPDATE` одним
запросом по возрастанию `id`; затем активная семья цели `FOR SHARE`. Замок
категории берётся ДО замка черновика: удаление категории держит её `FOR UPDATE` и
затем ждёт черновики, а правка, записавшая ссылку на категорию под замком
черновика, ждала бы категорию — цикл.

Слияние ничего не переносит: члены остаются при источнике, цель показывает их как
свои (`crud/discovery.py`); семья-цель не меняется ни полем (решение 6).

Функции не коммитят: транзакцией владеет вызывающий. Отказы — `WorkFamilyError`
с кодом из этого модуля или из `services/work_families.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import (
    CatalogContext,
    CategoryProposalStatus,
    DraftGroup,
    DraftStatus,
    FamilyCategoryProposal,
    FamilyDraft,
    FamilyDraftMember,
    FamilyStatus,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    UnitOfMeasure,
    WorkFamily,
)
from services.family_categories import lock_categories, require_category
from services.family_change import acquire_family_locks
from services.family_discovery import is_in_scope
from services.semantic_reconcile import reconcile_or_defer
from services.work_families import (
    REFUSE_DUPLICATE_ACTIVE_FAMILY,
    REFUSE_FAMILY_NOT_ACTIVE,
    REFUSE_FAMILY_NOT_FOUND,
    REFUSE_UNIT_MISMATCH,
    UNSET,
    WorkFamilyError,
    _lock_families,
    activate_family,
    create_family,
    update_family,
)
from services.work_variants import take_context_off_work

REFUSE_DRAFT_NOT_OPEN = "draft_not_open"
REFUSE_DRAFT_NOT_RESTORABLE = "draft_not_restorable"
REFUSE_DISCOVERY_RUN_SUPERSEDED = "discovery_run_superseded"
REFUSE_DRAFT_BLANK_TITLE = "draft_blank_title"
REFUSE_DRAFT_BLANK_DEFINITION = "draft_blank_definition"
REFUSE_DRAFT_WITHOUT_CATEGORY = "draft_without_category"
REFUSE_CONTEXT_NOT_IN_GROUP = "context_not_in_group"
REFUSE_CATEGORY_NOT_PROPOSED = "category_not_proposed"


def _now() -> datetime:
    return datetime.now(UTC)


def _blank(value: object) -> bool:
    return not isinstance(value, str) or value.strip() == ""


def latest_discovery_job_id(db: Session, unit_id: int | None) -> int | None:
    """Последнее выполненное открытие единицы: задание `family_discovery` в `done`
    с наибольшим `id` (`None` — единица «без единицы»); `None`, если выполненных
    открытий нет."""
    return db.execute(
        sa.select(sa.func.max(SemanticJob.id)).where(
            SemanticJob.kind == SemanticJobKind.family_discovery.value,
            SemanticJob.status == SemanticJobStatus.done.value,
            SemanticJob.unit_id.is_not_distinct_from(unit_id),
        )
    ).scalar_one()


def _label(draft: FamilyDraft) -> str:
    return draft.title if draft.title else f"№ {draft.ordinal}"


def _not_open(draft: FamilyDraft) -> WorkFamilyError:
    return WorkFamilyError(
        REFUSE_DRAFT_NOT_OPEN,
        f"Черновик «{_label(draft)}» уже активирован, слит, отброшен или устарел. "
        "Слитый и отброшенный можно вернуть.",
        draft_id=draft.id,
    )


def _superseded(draft: FamilyDraft) -> WorkFamilyError:
    return WorkFamilyError(
        REFUSE_DISCOVERY_RUN_SUPERSEDED,
        "Эти черновики устарели: единица открыта заново. Обновите экран.",
        draft_id=draft.id,
    )


def _not_restorable(draft: FamilyDraft) -> WorkFamilyError:
    return WorkFamilyError(
        REFUSE_DRAFT_NOT_RESTORABLE,
        f"Черновик «{_label(draft)}» вернуть нельзя: он уже стал семьёй (её архивируют "
        "на вкладке «Семьи») или единица открыта заново.",
        draft_id=draft.id,
    )


def _lock_drafts(db: Session, draft_ids: list[int]) -> dict[int, FamilyDraft]:
    """Черновики `FOR UPDATE` одним запросом по возрастанию `id`; свежее состояние
    после ожидания замка (`populate_existing`)."""
    rows = (
        db.execute(
            sa.select(FamilyDraft)
            .where(FamilyDraft.id.in_(sorted(set(draft_ids))))
            .order_by(FamilyDraft.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    return {draft.id: draft for draft in rows}


def _require_draft(locked: dict[int, FamilyDraft], draft_id: int) -> FamilyDraft:
    draft = locked.get(draft_id)
    if draft is None:
        raise LookupError(f"черновик {draft_id} не найден")
    return draft


def _check_actionable(db: Session, draft: FamilyDraft) -> None:
    """Общая проверка действий над `open`: группа `new` (иначе `draft_not_open`),
    последнее открытие единицы (иначе `discovery_run_superseded`), состояние
    `open` (иначе `draft_not_open`). Вызывается ПОД замком черновика."""
    if draft.grp != DraftGroup.new.value:
        raise _not_open(draft)
    if draft.job_id != latest_discovery_job_id(db, draft.unit_id):
        raise _superseded(draft)
    if draft.status != DraftStatus.open.value:
        raise _not_open(draft)


def edit_draft(
    db: Session,
    *,
    draft_id: int,
    title: str | object = UNSET,
    definition: str | object = UNSET,
    family_category_id: int | object = UNSET,
    actor_id: int,
) -> FamilyDraft:
    """Правит имя, определение и/или категорию открытого черновика; непереданное
    поле (`UNSET`) не трогается; `edited_by/at` ставятся.

    Raises:
        WorkFamilyError: `draft_blank_title`, `draft_blank_definition` — до любого
            обращения к базе; `category_not_found` — категории нет или её удалили;
            `discovery_run_superseded`, `draft_not_open`.
        LookupError: черновика нет.
    """
    if title is not UNSET and _blank(title):
        raise WorkFamilyError(
            REFUSE_DRAFT_BLANK_TITLE, "У черновика должно быть имя.",
            draft_id=draft_id,
        )
    if definition is not UNSET and _blank(definition):
        raise WorkFamilyError(
            REFUSE_DRAFT_BLANK_DEFINITION, "У черновика должно быть определение.",
            draft_id=draft_id,
        )
    if family_category_id is not UNSET:
        require_category(db, family_category_id, exclusive=False)
    draft = _require_draft(_lock_drafts(db, [draft_id]), draft_id)
    _check_actionable(db, draft)

    if title is not UNSET:
        draft.title = title.strip()
    if definition is not UNSET:
        draft.definition = definition.strip()
    if family_category_id is not UNSET:
        draft.family_category_id = family_category_id
    draft.edited_by = actor_id
    draft.edited_at = _now()
    db.flush()
    return draft


def merge_draft(
    db: Session,
    *,
    draft_id: int,
    target_draft_id: int | None = None,
    target_family_id: int | None = None,
    actor_id: int,
) -> FamilyDraft:
    """Сливает открытый черновик с другим черновиком того же открытия либо с
    активной семьёй единицы. Источник становится `merged`, его члены остаются при
    нём; цель и семья не меняются ни полем.

    Raises:
        ValueError: цель не одна из двух.
        WorkFamilyError: `draft_not_open` — цель не `open`, не группа `new`, из
            другого открытия или сам источник; `discovery_run_superseded`;
            `family_not_active`; `family_not_found`; `unit_mismatch` — семья
            другой единицы.
        LookupError: источника или целевого черновика нет.
    """
    if (target_draft_id is None) == (target_family_id is None):
        raise ValueError("слияние требует ровно одну цель: черновик или семью")
    ids = [draft_id] + ([target_draft_id] if target_draft_id is not None else [])
    locked = _lock_drafts(db, ids)
    draft = _require_draft(locked, draft_id)
    _check_actionable(db, draft)

    if target_draft_id is not None:
        target = _require_draft(locked, target_draft_id)
        if (
            target.id == draft.id
            or target.job_id != draft.job_id
            or target.grp != DraftGroup.new.value
            or target.status != DraftStatus.open.value
        ):
            raise _not_open(target)
        draft.merged_into_draft_id = target.id
    else:
        _lock_families(db, [target_family_id], exclusive=False)
        family = db.execute(
            sa.select(WorkFamily)
            .where(WorkFamily.id == target_family_id)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if family is None:
            raise WorkFamilyError(
                REFUSE_FAMILY_NOT_FOUND, f"семья {target_family_id} не найдена",
                family_id=target_family_id,
            )
        if family.status != FamilyStatus.active.value:
            raise WorkFamilyError(
                REFUSE_FAMILY_NOT_ACTIVE,
                f"Семья «{family.title}» больше не активна — слить с ней черновик нельзя.",
                family_id=family.id,
            )
        if family.unit_id != draft.unit_id:
            raise WorkFamilyError(
                REFUSE_UNIT_MISMATCH,
                f"Семья «{family.title}» относится к другой единице — слить с ней "
                "черновик нельзя.",
                family_id=family.id,
            )
        draft.merged_into_family_id = family.id

    draft.status = DraftStatus.merged.value
    draft.decided_by = actor_id
    draft.decided_at = _now()
    db.flush()
    return draft


def discard_draft(db: Session, *, draft_id: int, actor_id: int) -> FamilyDraft:
    """Отбрасывает открытый черновик: `discarded`, члены остаются, контексты без
    семьи идут в перезапрос.

    Raises:
        WorkFamilyError: `discovery_run_superseded`, `draft_not_open`.
        LookupError: черновика нет."""
    draft = _require_draft(_lock_drafts(db, [draft_id]), draft_id)
    _check_actionable(db, draft)
    draft.status = DraftStatus.discarded.value
    draft.decided_by = actor_id
    draft.decided_at = _now()
    db.flush()
    return draft


def restore_draft(db: Session, *, draft_id: int, actor_id: int) -> FamilyDraft:
    """Возвращает отброшенный или слитый черновик в `open`: `merged_into_*` и
    `decided_*` очищены; влитые в него черновики остаются влитыми.

    Raises:
        WorkFamilyError: `draft_not_open` — группа не `new`; `draft_not_restorable`
            — состояние не `discarded`/`merged` (активирован, вытеснен, открыт)
            либо открытие уже не последнее («единица открыта заново»).
        LookupError: черновика нет."""
    draft = _require_draft(_lock_drafts(db, [draft_id]), draft_id)
    if draft.grp != DraftGroup.new.value:
        raise _not_open(draft)
    if draft.status not in (
        DraftStatus.discarded.value,
        DraftStatus.merged.value,
    ) or draft.job_id != latest_discovery_job_id(db, draft.unit_id):
        raise _not_restorable(draft)
    draft.status = DraftStatus.open.value
    draft.merged_into_draft_id = None
    draft.merged_into_family_id = None
    draft.decided_by = None
    draft.decided_at = None
    db.flush()
    return draft


# ---------------------------------------------------------------------------
#  Активация отмеченных (спека 3б §2.5)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActivationOutcome:
    """Итог активации: заведённые семьи (по `ordinal` черновиков), применённые и
    пропущенные пары категорий, применённые и пропущенные строки «не работы»,
    единица открытия для перезапроса (`None` — единица «без единицы»)."""

    created_family_ids: tuple[int, ...]
    categories_applied: tuple[int, ...]
    categories_skipped: tuple[int, ...]
    not_work_applied: tuple[int, ...]
    not_work_skipped: tuple[int, ...]
    reask_unit_id: int | None


def _not_open_id(draft_id: int) -> WorkFamilyError:
    return WorkFamilyError(
        REFUSE_DRAFT_NOT_OPEN,
        f"Черновик «№ {draft_id}» уже активирован, слит, отброшен или устарел. "
        "Слитый и отброшенный можно вернуть.",
        draft_id=draft_id,
    )


def _lock_job_drafts(db: Session, job_id: int) -> dict[int, FamilyDraft]:
    """Шаг 1, черновики: все группы открытия `FOR UPDATE` по `id`."""
    rows = (
        db.execute(
            sa.select(FamilyDraft)
            .where(FamilyDraft.job_id == job_id)
            .order_by(FamilyDraft.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    return {draft.id: draft for draft in rows}


def _lock_job_proposals(db: Session, job_id: int) -> dict[int, FamilyCategoryProposal]:
    """Шаг 1, предложения категорий открытия `FOR UPDATE` по `family_id`."""
    rows = (
        db.execute(
            sa.select(FamilyCategoryProposal)
            .where(FamilyCategoryProposal.job_id == job_id)
            .order_by(FamilyCategoryProposal.family_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    return {proposal.family_id: proposal for proposal in rows}


def activate_discovery(
    db: Session,
    *,
    job_id: int,
    draft_ids: list[int],
    not_work_context_ids: list[int],
    family_categories: list[tuple[int, int]],
    actor_id: int,
) -> ActivationOutcome:
    """Активирует отмеченное одной транзакцией (решение 9; спека 3б §2.5).

    Порядок блокировок — общий порядок фичи (решение 20), как у обработки ответа:
    (0) все категории `FOR SHARE` — новые семьи и пары ссылаются на категории, и
    без этого шага удаление категории, державшее её и ждущее черновик, замыкало
    бы цикл с активацией, державшей черновик и ждущей категорию; (1) черновики и
    предложения категорий открытия `FOR UPDATE`; затем проверки: открытие —
    последнее выполненное единицы, каждый черновик — этого открытия, `new`,
    `open`, с категорией; (2) новые семьи по `ordinal` (`create_family` +
    `activate_family` в точке сохранения на черновик); (3) категории активным
    семьям — семьи `FOR UPDATE`; (4) «не работа» — `acquire_family_locks` и
    `take_context_off_work`; (5) сверка очереди по контекстам шага 4.

    Шаги 2-5 идут в одной точке сохранения: отказ или сбой любого шага оставляет
    базу как до вызова. Вход шагов 3-4 проверяется до записи.

    Raises:
        LookupError: задания открытия нет.
        WorkFamilyError: `discovery_run_superseded`; `draft_not_open`;
            `draft_without_category` (атрибут `draft_id`); `category_not_proposed`;
            `category_not_found`; `context_not_in_group`;
            `duplicate_active_family` (атрибуты `draft_id`, `ordinal`) — отказ
            всей активации, частичной нет.
    """
    job = db.execute(
        sa.select(SemanticJob.unit_id, SemanticJob.kind).where(SemanticJob.id == job_id)
    ).one_or_none()
    if job is None or job.kind != SemanticJobKind.family_discovery.value:
        raise LookupError(f"открытие {job_id} не найдено")
    unit_id = job.unit_id

    # Шаги 0-1.
    lock_categories(db, None, exclusive=False)
    drafts = _lock_job_drafts(db, job_id)
    proposals = _lock_job_proposals(db, job_id)
    if job_id != latest_discovery_job_id(db, unit_id):
        raise WorkFamilyError(
            REFUSE_DISCOVERY_RUN_SUPERSEDED,
            "Эти черновики устарели: единица открыта заново. Обновите экран.",
            job_id=job_id,
        )

    selected: list[FamilyDraft] = []
    for draft_id in sorted(set(draft_ids)):
        draft = drafts.get(draft_id)
        if draft is None:
            raise _not_open_id(draft_id)
        if draft.grp != DraftGroup.new.value or draft.status != DraftStatus.open.value:
            raise _not_open(draft)
        selected.append(draft)
    selected.sort(key=lambda d: d.ordinal)
    for draft in selected:
        if draft.family_category_id is None:
            raise WorkFamilyError(
                REFUSE_DRAFT_WITHOUT_CATEGORY,
                f"У черновика «{_label(draft)}» не выбрана категория.",
                draft_id=draft.id,
                ordinal=draft.ordinal,
            )

    pairs: dict[int, int] = {}
    for family_id, category_id in family_categories:
        proposal = proposals.get(family_id)
        if proposal is None or proposal.status != CategoryProposalStatus.open.value:
            title = db.execute(
                sa.select(WorkFamily.title).where(WorkFamily.id == family_id)
            ).scalar_one_or_none()
            raise WorkFamilyError(
                REFUSE_CATEGORY_NOT_PROPOSED,
                f"Семье «{title or '№ ' + str(family_id)}» категорию в этом открытии не "
                "предлагали — смените её на карточке семьи.",
                family_id=family_id,
            )
        require_category(db, category_id, exclusive=False)
        pairs[family_id] = category_id

    not_work_ids = sorted(set(not_work_context_ids))
    if not_work_ids:
        group_ids = [d.id for d in drafts.values() if d.grp == DraftGroup.not_work.value]
        members = (
            set(
                db.execute(
                    sa.select(FamilyDraftMember.context_id).where(
                        FamilyDraftMember.draft_id.in_(group_ids)
                    )
                ).scalars()
            )
            if group_ids
            else set()
        )
        if not set(not_work_ids) <= members:
            raise WorkFamilyError(
                REFUSE_CONTEXT_NOT_IN_GROUP,
                "Строка не входит в группу «Не работа» этого открытия.",
                context_ids=sorted(set(not_work_ids) - members),
            )

    unit_code = (
        None
        if unit_id is None
        else db.execute(
            sa.select(UnitOfMeasure.code).where(UnitOfMeasure.id == unit_id)
        ).scalar_one()
    )

    with db.begin_nested():
        # Шаг 2: новые семьи.
        created: list[int] = []
        for draft in selected:
            try:
                with db.begin_nested():
                    family = create_family(
                        db,
                        title=draft.title,
                        unit_name=unit_code,
                        definition=draft.definition,
                        actor_id=actor_id,
                        family_category_id=draft.family_category_id,
                        origin="discovery",
                    )
                    activate_family(
                        db, family_id=family.id, actor_id=actor_id,
                        rollback_on_conflict=False,
                    )
            except WorkFamilyError as exc:
                if exc.code != REFUSE_DUPLICATE_ACTIVE_FAMILY:
                    raise
                raise WorkFamilyError(
                    REFUSE_DUPLICATE_ACTIVE_FAMILY,
                    f"Активная семья «{draft.title}» в единице «{unit_code or 'без единицы'}» "
                    "уже есть — переименуйте черновик или слейте его с ней.",
                    draft_id=draft.id,
                    ordinal=draft.ordinal,
                    title=draft.title,
                ) from exc
            draft.status = DraftStatus.activated.value
            draft.activated_family_id = family.id
            draft.decided_by = actor_id
            draft.decided_at = _now()
            db.flush()
            created.append(family.id)

        # Шаг 3: категории активным семьям.
        applied_categories: list[int] = []
        skipped_categories: list[int] = []
        if pairs:
            _lock_families(db, sorted(pairs), exclusive=True)
            for family_id in sorted(pairs):
                family = db.execute(
                    sa.select(WorkFamily)
                    .where(WorkFamily.id == family_id)
                    .execution_options(populate_existing=True)
                ).scalar_one()
                if (
                    family.status != FamilyStatus.active.value
                    or family.family_category_id is not None
                ):
                    skipped_categories.append(family_id)
                    continue
                update_family(
                    db, family_id=family_id, family_category_id=pairs[family_id], actor_id=actor_id
                )
                proposal = proposals[family_id]
                proposal.status = CategoryProposalStatus.applied.value
                proposal.decided_by = actor_id
                proposal.decided_at = _now()
                db.flush()
                applied_categories.append(family_id)

        # Шаг 4: «не работа».
        applied_not_work: list[int] = []
        skipped_not_work: list[int] = []
        if not_work_ids:
            acquire_family_locks(
                db, [(c, None) for c in not_work_ids], release_on_failure=False,
                lock_variants=True,
            )
            candidates = is_in_scope(db, not_work_ids, why_in_scope=False)
            for context_id in not_work_ids:
                if context_id in candidates:
                    applied_not_work.append(context_id)
                else:
                    skipped_not_work.append(context_id)
            contexts = (
                db.execute(
                    sa.select(CatalogContext)
                    .where(CatalogContext.id.in_(applied_not_work))
                    .order_by(CatalogContext.id)
                    .execution_options(populate_existing=True)
                )
                .scalars()
                .all()
            )
            for context in contexts:
                take_context_off_work(db, context, actor_id=actor_id, reason="manual")

        # Шаг 5: сверка очереди.
        reconcile_or_defer(db, applied_not_work)

    return ActivationOutcome(
        created_family_ids=tuple(created),
        categories_applied=tuple(applied_categories),
        categories_skipped=tuple(skipped_categories),
        not_work_applied=tuple(applied_not_work),
        not_work_skipped=tuple(skipped_not_work),
        reask_unit_id=unit_id,
    )
