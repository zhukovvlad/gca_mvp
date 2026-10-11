"""Обработка ответа открытия семей: ответ модели -> черновики (спека 3б §2.3,
«Исполнение», шаги 1-8; решения 7, 20, 21).

`apply_discovery` — одна транзакция в общем порядке блокировок фичи «домен
раньше задания»: (1) все категории справочника `FOR SHARE` (их имена и
определения входят в тело, и до `commit` их не переименуют и не удалят);
(2) открытые черновики и предложения категорий единицы `FOR UPDATE`;
(3) активные семьи единицы и контексты охвата `FOR KEY SHARE` — только затем,
чтобы неявный `FOR KEY SHARE` внешних ключей вставок шага 7 не ждал доменную
строку под замком задания; (4) задание `FOR UPDATE` и проверка захвата;
(5) повторный рендер тела: другой `request_hash` — задание отменено
(`input_changed`), черновиков нет; (6) прежние открытые черновики и
предложения категорий единицы — `superseded`; (7) группы, члены, предложения
категорий; (8) задание `done`.

Ответ относится ко входу, каким он был на шаге 5. Дальше вход меняется двумя
путями: новые строки и правки без блокировки существующей строки (импорт нового
контекста, активация семьи, публикация предложения существующей семьи,
архивирование контекста неключевым `UPDATE`) и строки, взятые на шагах 1 и 3,
которые штатные операции меняют под `FOR UPDATE` и потому ждут `commit` этой
обработки. Поэтому черновики — снимок, и потребитель перепроверяет его под своими
блокировками (`is_in_scope`).

Функция не коммитит: коммит за вызывающим (`record_result`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import Settings
from models import (
    CatalogContext,
    CategoryProposalStatus,
    DraftGroup,
    DraftStatus,
    FamilyCategoryProposal,
    FamilyDraft,
    FamilyDraftMember,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    WorkFamily,
)
from services.family_categories import lock_categories
from services.family_discovery import discovery_scope, render_discovery_request
from services.variant_answer import DiscoveryAnswer


@dataclass(frozen=True)
class DiscoveryOutcome:
    """Исход обработки. `unapplied_reason`: `lost_claim` — задание не наше,
    ничего не записано; `stale_fingerprint` — охват изменился, задание
    отменено, черновиков нет. `unassigned` — число пропущенных номеров имён."""

    applied: bool
    unapplied_reason: Literal["lost_claim", "stale_fingerprint"] | None
    drafts_created: int
    superseded_drafts: int
    unassigned: int


def _unit_clause_job(unit_id: int | None):
    return SemanticJob.unit_id.is_not_distinct_from(unit_id)


def _lock_open_drafts(db: Session, unit_id: int | None) -> list[int]:
    """Шаг 2, черновики: открытые черновики единицы `FOR UPDATE` по `id`."""
    return list(
        db.execute(
            sa.select(FamilyDraft.id)
            .where(
                FamilyDraft.unit_id.is_not_distinct_from(unit_id),
                FamilyDraft.status == DraftStatus.open.value,
            )
            .order_by(FamilyDraft.id)
            .with_for_update()
        )
        .scalars()
        .all()
    )


def _lock_open_proposals(db: Session, unit_id: int | None) -> list[tuple[int, int]]:
    """Шаг 2, предложения категорий: открытые предложения заданий открытия этой
    единицы `FOR UPDATE` по ключу. Единица предложения — единица его задания."""
    unit_jobs = sa.select(SemanticJob.id).where(
        SemanticJob.kind == SemanticJobKind.family_discovery.value, _unit_clause_job(unit_id)
    )
    rows = db.execute(
        sa.select(FamilyCategoryProposal.job_id, FamilyCategoryProposal.family_id)
        .where(
            FamilyCategoryProposal.status == CategoryProposalStatus.open.value,
            FamilyCategoryProposal.job_id.in_(unit_jobs),
        )
        .order_by(FamilyCategoryProposal.job_id, FamilyCategoryProposal.family_id)
        .with_for_update()
    ).all()
    return [(job_id, family_id) for job_id, family_id in rows]


def _key_share(db: Session, model, ids: list[int]) -> None:
    """`FOR KEY SHARE` строк по `id` в порядке возрастания; id без строки
    пропускаются."""
    if not ids:
        return
    db.execute(
        sa.select(model.id)
        .where(model.id.in_(sorted(ids)))
        .order_by(model.id)
        .with_for_update(read=True, key_share=True)
    ).all()


def apply_discovery(
    db: Session,
    *,
    job_id: int,
    claim_token: UUID,
    answer: DiscoveryAnswer,
    settings: Settings,
) -> DiscoveryOutcome:
    unit_id = db.execute(
        sa.select(SemanticJob.unit_id).where(SemanticJob.id == job_id)
    ).scalar_one()

    # Шаги 1-3: домен. Охват читается без замков только затем, чтобы узнать, что
    # блокировать; его перепроверяет шаг 5.
    lock_categories(db, None, exclusive=False)
    _lock_open_drafts(db, unit_id)
    _lock_open_proposals(db, unit_id)
    preliminary = discovery_scope(db, unit_id)
    _key_share(db, WorkFamily, list(preliminary.active_family_ids))
    _key_share(db, CatalogContext, list(preliminary.context_ids))

    # Шаг 4: задание и проверка захвата.
    job = db.execute(
        sa.select(SemanticJob)
        .where(SemanticJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if job.claim_token != claim_token:
        return DiscoveryOutcome(False, "lost_claim", 0, 0, 0)

    # Шаг 5: повторный рендер тела.
    scope = discovery_scope(db, unit_id)
    rendered = render_discovery_request(scope, db, settings=settings)
    if rendered.request_hash != job.request_hash:
        job.status = SemanticJobStatus.cancelled.value
        job.cancel_reason = SemanticCancelReason.input_changed.value
        job.claim_token = None
        db.flush()
        return DiscoveryOutcome(False, "stale_fingerprint", 0, 0, 0)

    # Шаг 6: прежние открытые черновики и предложения категорий единицы.
    superseded = _supersede(db, unit_id)

    # Шаг 7: группы, члены, предложения категорий.
    created = _write_groups(db, job, scope, answer)

    # Шаг 8: задание выполнено.
    job.status = SemanticJobStatus.done.value
    job.claim_token = None
    db.flush()
    return DiscoveryOutcome(True, None, created, superseded, len(answer.unassigned))


def _supersede(db: Session, unit_id: int | None) -> int:
    drafts = db.execute(
        sa.update(FamilyDraft)
        .where(
            FamilyDraft.unit_id.is_not_distinct_from(unit_id),
            FamilyDraft.status == DraftStatus.open.value,
        )
        .values(status=DraftStatus.superseded.value)
        .returning(FamilyDraft.id)
    ).all()
    unit_jobs = sa.select(SemanticJob.id).where(
        SemanticJob.kind == SemanticJobKind.family_discovery.value, _unit_clause_job(unit_id)
    )
    db.execute(
        sa.update(FamilyCategoryProposal)
        .where(
            FamilyCategoryProposal.status == CategoryProposalStatus.open.value,
            FamilyCategoryProposal.job_id.in_(unit_jobs),
        )
        .values(status=CategoryProposalStatus.superseded.value)
    )
    return len(drafts)


def _write_groups(db: Session, job: SemanticJob, scope, answer: DiscoveryAnswer) -> int:
    """Группы ответа -> `family_drafts`, номера имён -> все контексты охвата с
    этим наименованием -> `family_draft_members`; затем предложения категорий.
    Возвращает число созданных групп (черновиков)."""
    contexts_of = {name.index: name.context_ids for name in scope.names}
    ordinal = 0

    def _add_group(**fields) -> FamilyDraft:
        nonlocal ordinal
        ordinal += 1
        draft = FamilyDraft(
            job_id=job.id,
            unit_id=job.unit_id,
            ordinal=ordinal,
            status=DraftStatus.open.value,
            **fields,
        )
        db.add(draft)
        db.flush()
        return draft

    def _add_members(draft: FamilyDraft, numbers: tuple[int, ...]) -> None:
        for number in numbers:
            for context_id in contexts_of[number]:
                db.add(
                    FamilyDraftMember(
                        draft_id=draft.id, job_id=job.id, context_id=context_id,
                        name_index=number,
                    )
                )
        db.flush()

    for group in answer.groups:
        if group.family_id is None:
            draft = _add_group(
                grp=DraftGroup.new.value,
                title=group.title,
                definition=group.definition,
                family_category_id=group.category_id,
                similar_family_id=group.similar_family_id,
            )
        else:
            draft = _add_group(grp=DraftGroup.existing.value, existing_family_id=group.family_id)
        _add_members(draft, group.names)
    if answer.not_work:
        draft = _add_group(grp=DraftGroup.not_work.value)
        _add_members(draft, answer.not_work)

    for family_id, category_id in answer.family_categories:
        db.add(
            FamilyCategoryProposal(
                job_id=job.id,
                family_id=family_id,
                family_category_id=category_id,
                status=CategoryProposalStatus.open.value,
            )
        )
    db.flush()
    return ordinal

