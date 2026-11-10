"""Признак «можно отпустить» главного параметра семьи: задание `releasable_params`,
предложения модели и решение человека (спека лестницы аналогов
`2026-11-07-analog-ladder-design.md` §2.6, §2.7).

`family_parameters.releasable`: `NULL` — не размечен, `false` — обязателен,
`true` — можно отпустить; пока не стоит `true`, параметр обязателен.

Предмет задания — `(family_id, schema_id)` действующей версии. Наружу уходят имя и
определение семьи, её единица, id, имена и списки значений (до 30) активных
главных параметров; цен, объёмов, подрядчиков и договоров в теле нет (`AGENTS.md`
§3). Сам признак `releasable` в тело не входит: разметка одного параметра вопрос
по другим не меняет.

**Актуальность определяется повторным рендером, а не путями правки и не статусом
задания** (свойство К3). Входы запроса правят `update_family`, `set_unit`,
`schema_edit` и заморозка версии; ответ модели и принятие предложения берут замки
входов (семья `FOR SHARE` → версия → параметры) и сравнивают отпечаток текущего
рендера с отпечатком запроса. Уборка в путях правки (`refresh_inputs`) — гигиена
таблицы и очереди: пропущенный путь даёт лишнюю строку, а не применённый
устаревший ответ.
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from config import Settings
from config import settings as app_settings
from crud.common import DomainError
from models import (
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    FamilyStatus,
    ParameterOrigin,
    ParameterRole,
    ReleasableProposal,
    SchemaStatus,
    SemanticCancelReason,
    SemanticJob,
    SemanticJobKind,
    SemanticJobStatus,
    UnitOfMeasure,
    WorkFamily,
)
from services.semantic_answer import (
    AnswerSchemaError,
    _extract_json_text,
    _parse_confidence,
    _parse_reason,
    _parse_single_object,
)
from services.semantic_cost import known_prefix_tokens, reserve_for_known_prefix, tariffs_from
from services.semantic_reconcile import (
    Fingerprint,
    _insert_job_rows,
    _job_row,
    _lock_jobs,
    _revive_jobs,
)
from services.semantic_request import RenderedRequest
from services.variant_request import SubjectNotRenderable, _finish, _unit_display

#: Версия текста промпта — столбец аудита задания, в хэши не входит.
RELEASABLE_PROMPT_VERSION = 1

#: Версия формы ответа — столбец аудита задания.
RELEASABLE_RESPONSE_SCHEMA_VERSION = "releasable:1"

#: Сколько значений параметра уходит модели (спека §2.7).
MAX_VALUES: Final = 30

#: Предел `reason` в ответе.
MAX_REASON_CHARS: Final = 300

#: Размер страницы очереди «Можно отпустить».
PAGE_SIZE: Final = 50

#: Коды отказов решения человека.
CODE_PROPOSAL_STALE: Final = "proposal_stale"
CODE_PARAMETER_NOT_MAIN: Final = "parameter_not_main"

#: Статусы незавершённого задания: их отменяет уборка.
_UNFINISHED: Final = (
    SemanticJobStatus.pending.value,
    SemanticJobStatus.running.value,
    SemanticJobStatus.privacy_hold.value,
    SemanticJobStatus.error.value,
)

RELEASABLE_PROMPT = """Ты помогаешь вести каталог строительных работ генподрядчика. Семья — тип работы с одной единицей измерения. Внутри семьи строки различаются ГЛАВНЫМИ ПАРАМЕТРАМИ: признаками, от которых цена за единицу при той же работе меняется (материал, толщина, класс, тип изделия и т. п.).

Тебе дают семью (имя, единицу, определение) и пронумерованный список её главных параметров со значениями.

Когда для строки семьи нет аналога с тем же значением параметра, аналогом иногда берут строку той же семьи с другим значением этого параметра. Вопрос для КАЖДОГО параметра: можно ли взять аналогом строку той же семьи с другим значением этого параметра, если с тем же значением аналога нет — то есть разница по этому параметру мало меняет цену за единицу.

- "releasable": true — можно, разница по параметру мало меняет цену за единицу;
- "releasable": false — нельзя, параметр существенно меняет цену или тип работы, его значение обязательно должно совпасть;
- "confidence" — твоя уверенность от 0 до 1; не уверен — ставь низкую;
- "reason" — краткая причина, не длиннее 300 символов.

Верни ровно один объект на КАЖДЫЙ параметр, без пропусков и повторов, с тем же "parameter_id", что в списке.

Ответ — только JSON без пояснений:
{"parameters": [{"parameter_id": <число>, "releasable": true | false, "confidence": <число от 0 до 1>, "reason": "<причина>"}]}"""

RELEASABLE_RESPONSE_FORMAT: Final = {
    "type": "json_schema",
    "json_schema": {
        "name": "releasable_params",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "parameters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "parameter_id": {"type": "integer"},
                            "releasable": {"type": "boolean"},
                            "confidence": {"type": "number"},
                            "reason": {"type": "string"},
                        },
                        "required": ["parameter_id", "releasable", "confidence", "reason"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["parameters"],
            "additionalProperties": False,
        },
    },
}


# ---------------------------------------------------------------------------
#  Материал и тело запроса
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReleasableParameterIn:
    """Активный главный параметр в запросе. `releasable` — признак на момент чтения;
    в тело и отпечаток не входит."""

    parameter_id: int
    name: str
    values: tuple[str, ...]
    releasable: bool | None


@dataclass(frozen=True)
class ReleasableMaterial:
    family_id: int
    schema_id: int
    title: str
    unit_code: str | None
    definition: str
    parameters: tuple[ReleasableParameterIn, ...]

    @property
    def has_unmarked(self) -> bool:
        return any(parameter.releasable is None for parameter in self.parameters)


@dataclass(frozen=True)
class ReleasableAnswerItem:
    parameter_id: int
    releasable: bool
    confidence: Decimal
    reason: str


@dataclass(frozen=True)
class ReleasableAnswer:
    parameters: tuple[ReleasableAnswerItem, ...]


@dataclass(frozen=True)
class ProposalView:
    """Действующее предложение: семья, параметр со значениями, ответ модели и
    отпечаток запроса, на который она ответила."""

    family_id: int
    family_title: str
    parameter_id: int
    parameter_name: str
    values: tuple[str, ...]
    releasable: bool
    confidence: Decimal
    reason: str
    request_hash: str


def load_releasable_material(
    db: Session, family_id: int, schema_id: int
) -> ReleasableMaterial | None:
    """Материал задания по текущему состоянию; `None` — предмет не применим:
    семьи или версии нет, версия не принадлежит семье или не `frozen`, семья не
    `active`, у версии нет активных главных параметров."""
    unit = aliased(UnitOfMeasure)
    family = db.execute(
        sa.select(
            WorkFamily.title, WorkFamily.definition, WorkFamily.status,
            unit.code.label("unit_code"),
        )
        .outerjoin(unit, unit.id == WorkFamily.unit_id)
        .where(WorkFamily.id == family_id)
    ).one_or_none()
    schema = db.execute(
        sa.select(FamilyParameterSchema.family_id, FamilyParameterSchema.status).where(
            FamilyParameterSchema.id == schema_id
        )
    ).one_or_none()
    if (
        family is None or schema is None or schema.family_id != family_id
        or schema.status != SchemaStatus.frozen.value
        or family.status != FamilyStatus.active.value
    ):
        return None
    parameters = db.execute(
        sa.select(FamilyParameter.id, FamilyParameter.name, FamilyParameter.releasable)
        .where(
            FamilyParameter.schema_id == schema_id,
            FamilyParameter.role == ParameterRole.main.value,
            FamilyParameter.archived_at.is_(None),
        )
        .order_by(FamilyParameter.ordinal)
    ).all()
    if not parameters:
        return None
    values_of: dict[int, list[str]] = {row.id: [] for row in parameters}
    for row in db.execute(
        sa.select(FamilyParameterValue.parameter_id, FamilyParameterValue.value)
        .where(
            FamilyParameterValue.parameter_id.in_(list(values_of)),
            FamilyParameterValue.merged_into_id.is_(None),
            FamilyParameterValue.retired_at.is_(None),
            FamilyParameterValue.composite_kind.is_(None),
        )
        .order_by(FamilyParameterValue.parameter_id, FamilyParameterValue.id)
    ).all():
        values_of[row.parameter_id].append(row.value)
    return ReleasableMaterial(
        family_id=family_id,
        schema_id=schema_id,
        title=family.title,
        unit_code=family.unit_code,
        definition=family.definition or "",
        parameters=tuple(
            ReleasableParameterIn(
                row.id, row.name, tuple(values_of[row.id]), row.releasable
            )
            for row in parameters
        ),
    )


def _user_text(material: ReleasableMaterial) -> str:
    blocks = [
        f"Параметр {p.parameter_id}: {p.name}\nЗначения:\n" + "\n".join(f"- {v}" for v in p.values[:MAX_VALUES])
        for p in material.parameters
    ]
    return (
        f"СЕМЬЯ: {material.title}\nЕДИНИЦА: {_unit_display(material.unit_code)}\n"
        f"ОПРЕДЕЛЕНИЕ: {material.definition}\n\nГЛАВНЫЕ ПАРАМЕТРЫ:\n" + "\n\n".join(blocks)
    )


def render_releasable_material(
    material: ReleasableMaterial, *, settings: Settings
) -> RenderedRequest:
    """Каноническое тело запроса и отпечатки; в базу не ходит. Профиль — модель
    схемы (`SEMANTIC_SCHEMA_MODEL`, рассуждение `SEMANTIC_VARIANTS_REASONING_EFFORT`).
    `candidates_hash` — от id параметров."""
    return _finish(
        model=settings.SEMANTIC_SCHEMA_MODEL,
        max_tokens=settings.SEMANTIC_SCHEMA_MAX_TOKENS,
        reasoning_effort=settings.SEMANTIC_VARIANTS_REASONING_EFFORT,
        response_format=RELEASABLE_RESPONSE_FORMAT,
        system_blocks=[
            {"type": "text", "text": RELEASABLE_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        user_text=_user_text(material),
        candidates_snapshot=[p.parameter_id for p in material.parameters],
    )


def render_releasable_request(
    db: Session, family_id: int, schema_id: int, *, settings: Settings
) -> RenderedRequest:
    """Текущее тело запроса версии.

    Raises:
        SubjectNotRenderable: предмет не применим (см. `load_releasable_material`)."""
    material = load_releasable_material(db, family_id, schema_id)
    if material is None:
        raise SubjectNotRenderable(f"версия {schema_id} семьи {family_id} не спрашивается")
    return render_releasable_material(material, settings=settings)


def _current_hash(db: Session, family_id: int, schema_id: int, settings: Settings) -> str | None:
    material = load_releasable_material(db, family_id, schema_id)
    if material is None:
        return None
    return render_releasable_material(material, settings=settings).request_hash


# ---------------------------------------------------------------------------
#  Разбор ответа
# ---------------------------------------------------------------------------

def parse_releasable_answer(raw: str) -> ReleasableAnswer:
    """Строгий разбор `{parameters: [{parameter_id, releasable, confidence,
    reason}]}`. Состав параметров против запроса проверяет `check_coverage`.
    Ответ не по схеме — `AnswerSchemaError`."""
    obj = _parse_single_object(_extract_json_text(raw))
    if "parameters" not in obj:
        raise AnswerSchemaError("missing_field", "нет обязательного поля parameters")
    items = obj["parameters"]
    if not isinstance(items, list):
        raise AnswerSchemaError("bad_type", "parameters — не список")
    parsed: list[ReleasableAnswerItem] = []
    for item in items:
        if not isinstance(item, dict):
            raise AnswerSchemaError("bad_type", "элемент parameters — не объект")
        missing = [
            key for key in ("parameter_id", "releasable", "confidence", "reason")
            if key not in item
        ]
        if missing:
            raise AnswerSchemaError("missing_field", f"нет обязательных полей: {', '.join(missing)}")
        parameter_id, releasable = item["parameter_id"], item["releasable"]
        if isinstance(parameter_id, bool) or not isinstance(parameter_id, int):
            raise AnswerSchemaError("bad_type", "parameter_id — не целое число")
        if not isinstance(releasable, bool):
            raise AnswerSchemaError("bad_type", "releasable — не булево значение")
        confidence = _parse_confidence(item["confidence"])
        reason = _parse_reason(item["reason"])
        if len(reason) > MAX_REASON_CHARS:
            raise AnswerSchemaError("bad_type", f"reason длиннее {MAX_REASON_CHARS} символов")
        parsed.append(ReleasableAnswerItem(parameter_id, releasable, confidence, reason))
    return ReleasableAnswer(parameters=tuple(parsed))


def check_coverage(answer: ReleasableAnswer, expected_ids: Collection[int]) -> None:
    """Каждый спрошенный параметр — ровно один раз, чужих нет; иначе ошибка попытки."""
    ids = [item.parameter_id for item in answer.parameters]
    expected = sorted(expected_ids)
    if sorted(ids) == expected:
        return
    if len(set(ids)) != len(ids):
        raise AnswerSchemaError("bad_type", "параметр назван в ответе больше одного раза")
    if set(ids) - set(expected):
        raise AnswerSchemaError("bad_type", "в ответе есть параметр, о котором не спрашивали")
    raise AnswerSchemaError("missing_field", "в ответе пропущен параметр запроса")


# ---------------------------------------------------------------------------
#  Замки входов
# ---------------------------------------------------------------------------

def _lock_family_statement(family_id: int):
    return sa.select(WorkFamily.id).where(WorkFamily.id == family_id).with_for_update(read=True)


def _lock_family_exclusive_statement(family_id: int):
    """Семья `FOR UPDATE` — замок писателя входов (отдельно от исполнения: тест компилирует
    запрос и проверяет режим)."""
    return sa.select(WorkFamily.id).where(WorkFamily.id == family_id).with_for_update()


def _lock_schema_statement(schema_id: int, *, for_update: bool = False):
    stmt = sa.select(FamilyParameterSchema.id).where(FamilyParameterSchema.id == schema_id)
    return stmt.with_for_update(key_share=True) if for_update else stmt.with_for_update(read=True)


def _lock_parameters_statement(schema_id: int):
    return (
        sa.select(FamilyParameter.id)
        .where(
            FamilyParameter.schema_id == schema_id,
            FamilyParameter.role == ParameterRole.main.value,
            FamilyParameter.archived_at.is_(None),
        )
        .order_by(FamilyParameter.id)
        .with_for_update(read=True)
    )


def lock_inputs(
    db: Session, family_id: int, schema_id: int, *, bump_schema: bool = False
) -> None:
    """Замки всех входов запроса в общем порядке «семья раньше версии раньше
    параметров»: семья `FOR SHARE`, версия `FOR SHARE`, активные главные параметры
    `FOR SHARE`. Писатели входов берут семью `FOR UPDATE` первой, поэтому после
    этих замков тело запроса не меняется до коммита.

    `bump_schema` — вызывающий повысит `revision` версии (решение человека): версия
    берётся `FOR NO KEY UPDATE`, иначе два решения по одной версии, держа `FOR SHARE`,
    встретились бы на повышении `revision` и замкнулись в цикл."""
    db.execute(_lock_family_statement(family_id)).all()
    db.execute(_lock_schema_statement(schema_id, for_update=bump_schema)).all()
    db.execute(_lock_parameters_statement(schema_id)).all()


# ---------------------------------------------------------------------------
#  Запись ответа модели
# ---------------------------------------------------------------------------

def record_releasable_result(
    db: Session, job: SemanticJob, answer: ReleasableAnswer, *, settings: Settings
) -> None:
    """Применяет разобранный ответ и закрывает задание; не коммитит. Вызывающий
    владеет захватом и держит замки: `lock_inputs`, затем задание `FOR UPDATE`.

    Порядок решений:
    1. версия не действующая или семья не `active`, либо нет активных главных —
       задание `cancelled` / `not_applicable`, ничего не пишется;
    2. тело, отрендеренное заново по текущему состоянию, даёт другой
       `request_hash` — `cancelled` / `input_changed`, ничего не пишется;
    3. ответ не покрывает параметры запроса ровно по разу — `AnswerSchemaError`
       (до любой записи; задание не тронуто);
    4. иначе по размеченному параметру ответ молчит; уверенность не ниже порога
       пишет `releasable` (`origin` и `revision` не меняются), ниже — предложение
       с отпечатком задания."""
    assert job.family_id is not None and job.schema_id is not None
    material = load_releasable_material(db, job.family_id, job.schema_id)
    if material is None:
        _cancel(db, job, SemanticCancelReason.not_applicable)
        return
    current = render_releasable_material(material, settings=settings)
    if current.request_hash != job.request_hash:
        _cancel(db, job, SemanticCancelReason.input_changed)
        return
    check_coverage(answer, [p.parameter_id for p in material.parameters])

    unmarked = {p.parameter_id for p in material.parameters if p.releasable is None}
    threshold = settings.SEMANTIC_RELEASABLE_AUTO_ACCEPT_THRESHOLD
    for item in answer.parameters:
        if item.parameter_id not in unmarked:
            continue
        if item.confidence >= threshold:
            db.execute(
                sa.update(FamilyParameter)
                .where(FamilyParameter.id == item.parameter_id, FamilyParameter.releasable.is_(None))
                .values(releasable=item.releasable)
                .execution_options(synchronize_session=False)
            )
            db.execute(
                sa.delete(ReleasableProposal).where(
                    ReleasableProposal.parameter_id == item.parameter_id
                )
            )
        else:
            values = dict(
                parameter_id=item.parameter_id, releasable=item.releasable,
                confidence=item.confidence, reason=item.reason, request_hash=job.request_hash,
            )
            db.execute(
                pg_insert(ReleasableProposal)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[ReleasableProposal.parameter_id],
                    set_={key: value for key, value in values.items() if key != "parameter_id"},
                )
            )
    job.status = SemanticJobStatus.done.value
    job.claim_token = None
    db.flush()


def _cancel(db: Session, job: SemanticJob, reason: SemanticCancelReason) -> None:
    job.status = SemanticJobStatus.cancelled.value
    job.cancel_reason = reason.value
    job.claim_token = None
    db.flush()


# ---------------------------------------------------------------------------
#  Действующие предложения и решение человека
# ---------------------------------------------------------------------------

def current_proposals(
    db: Session, *, family_ids: Sequence[int] | None, page: int | None
) -> list[ProposalView]:
    """Действующие предложения: версия действующая, семья `active`, параметр
    активный главный с `releasable IS NULL`, отпечаток предложения равен отпечатку
    тела, отрендеренного ЗАНОВО по текущему состоянию (по одному рендеру на версию
    в выборке). Сравнение со строкой предложения актуальности не доказывает.
    По семьям и номерам параметров; `page` — страница по `PAGE_SIZE` (с 1), `None` —
    все."""
    stmt = (
        sa.select(
            ReleasableProposal, FamilyParameter.name, FamilyParameter.ordinal,
            FamilyParameter.schema_id, WorkFamily.id.label("family_id"),
            WorkFamily.title.label("family_title"),
        )
        .join(FamilyParameter, FamilyParameter.id == ReleasableProposal.parameter_id)
        .join(FamilyParameterSchema, FamilyParameterSchema.id == FamilyParameter.schema_id)
        .join(WorkFamily, WorkFamily.id == FamilyParameterSchema.family_id)
        .where(
            FamilyParameterSchema.status == SchemaStatus.frozen.value,
            WorkFamily.status == FamilyStatus.active.value,
            FamilyParameter.role == ParameterRole.main.value,
            FamilyParameter.archived_at.is_(None),
            FamilyParameter.releasable.is_(None),
        )
        .order_by(WorkFamily.id, FamilyParameter.ordinal)
    )
    if family_ids is not None:
        stmt = stmt.where(WorkFamily.id.in_(list(family_ids)))
    rows = db.execute(stmt).all()

    renders: dict[tuple[int, int], tuple[str, dict[int, tuple[str, ...]]] | None] = {}
    views: list[ProposalView] = []
    for row in rows:
        key = (row.family_id, row.schema_id)
        if key not in renders:
            material = load_releasable_material(db, row.family_id, row.schema_id)
            renders[key] = (
                None if material is None
                else (
                    render_releasable_material(material, settings=app_settings).request_hash,
                    {p.parameter_id: p.values for p in material.parameters},
                )
            )
        current = renders[key]
        proposal = row[0]
        if current is None or proposal.request_hash != current[0]:
            continue
        views.append(
            ProposalView(
                family_id=row.family_id, family_title=row.family_title,
                parameter_id=proposal.parameter_id, parameter_name=row.name,
                values=current[1].get(proposal.parameter_id, ()),
                releasable=proposal.releasable, confidence=proposal.confidence,
                reason=proposal.reason, request_hash=proposal.request_hash,
            )
        )
    if page is None:
        return views
    start = (max(1, page) - 1) * PAGE_SIZE
    return views[start:start + PAGE_SIZE]


def _not_found(parameter_id: int, family_id: int) -> DomainError:
    return DomainError(
        404, f"Параметр {parameter_id} не найден в действующей схеме семьи {family_id}."
    )


def _parameter_row(db: Session, family_id: int, parameter_id: int):
    """Параметр действующей версии активной семьи: `(schema_id, role)`; `None` —
    такого нет (чужая семья, не действующая версия, архивный параметр)."""
    return db.execute(
        sa.select(FamilyParameter.schema_id, FamilyParameter.role)
        .join(FamilyParameterSchema, FamilyParameterSchema.id == FamilyParameter.schema_id)
        .join(WorkFamily, WorkFamily.id == FamilyParameterSchema.family_id)
        .where(
            FamilyParameter.id == parameter_id,
            FamilyParameterSchema.family_id == family_id,
            FamilyParameterSchema.status == SchemaStatus.frozen.value,
            WorkFamily.status == FamilyStatus.active.value,
            FamilyParameter.archived_at.is_(None),
        )
    ).one_or_none()


def decide_releasable(
    db: Session, family_id: int, parameter_id: int, releasable: bool,
    *, request_hash: str | None, actor_id: int,
) -> None:
    """Решение человека по главному параметру; не коммитит.

    Замки: `lock_inputs` (семья, версия, параметры), затем строка предложения
    `FOR UPDATE`. Решение пишет `releasable`, `origin = human`, `revision + 1` и
    удаляет предложение параметра. С `request_hash` оно принимается, только если
    предложение ДЕЙСТВУЮЩЕЕ: строка есть, отпечаток строки, переданный и текущий
    (рендер заново под замками) совпадают, параметр не размечен; иначе
    `409 proposal_stale`, признак не меняется, недействующая строка удаляется (коммит
    удаления — за вызывающим). Без `request_hash` — решение по текущему виду схемы
    без сверки.

    Raises:
        DomainError: 404 — параметра нет в действующей схеме активной семьи; 422
            `parameter_not_main`; 409 `proposal_stale`."""
    found = _parameter_row(db, family_id, parameter_id)
    if found is None:
        raise _not_found(parameter_id, family_id)
    lock_inputs(db, family_id, found.schema_id, bump_schema=True)
    found = _parameter_row(db, family_id, parameter_id)  # перечитано под замками
    if found is None:
        raise _not_found(parameter_id, family_id)
    if found.role != ParameterRole.main.value:
        raise DomainError(
            422, "«Можно отпустить» задаётся только главному параметру.",
            code=CODE_PARAMETER_NOT_MAIN,
        )
    proposal = db.execute(
        sa.select(ReleasableProposal)
        .where(ReleasableProposal.parameter_id == parameter_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    flag = db.execute(
        sa.select(FamilyParameter.releasable)
        .where(FamilyParameter.id == parameter_id)
        .execution_options(populate_existing=True)
    ).scalar_one()

    if request_hash is not None:
        current = _current_hash(db, family_id, found.schema_id, app_settings)
        live = proposal is not None and flag is None and proposal.request_hash == current
        if not live or request_hash != current:
            if proposal is not None and not live:
                db.delete(proposal)
                db.flush()
            raise DomainError(
                409, "Предложение устарело: схема или семья изменились после вопроса модели.",
                code=CODE_PROPOSAL_STALE,
            )

    from services.work_variants import bump_revision  # цикл импортов: work_variants -> releasable

    db.execute(
        sa.update(FamilyParameter)
        .where(FamilyParameter.id == parameter_id)
        .values(releasable=releasable, origin=ParameterOrigin.human.value)
        .execution_options(synchronize_session=False)
    )
    db.execute(
        sa.delete(ReleasableProposal)
        .where(ReleasableProposal.parameter_id == parameter_id)
        .execution_options(synchronize_session=False)
    )
    bump_revision(db, found.schema_id)
    db.flush()
    db.expire_all()
    del actor_id  # решение человека — `origin = human`; отдельного журнала у признака нет


def accept_all_proposals(db: Session, *, actor_id: int) -> dict[str, int]:
    """Принять все действующие предложения как решения человека. Семьи по возрастанию
    id, по транзакции на семью (коммит после семьи). Каждое предложение идёт под
    SAVEPOINT: прочий отказ откатывает только это предложение (`failed`), семья
    продолжается; `409 proposal_stale` исключение гасит ВНУТРИ блока SAVEPOINT, чтобы
    удаление недействующей строки, сделанное `decide_releasable`, осталось и вошло в
    коммит семьи (`stale`). Возвращает `{accepted, stale, failed}`."""
    by_family: dict[int, list[ProposalView]] = {}
    for view in current_proposals(db, family_ids=None, page=None):
        by_family.setdefault(view.family_id, []).append(view)
    accepted = stale = failed = 0
    try:
        for family_id in sorted(by_family):
            for view in by_family[family_id]:
                try:
                    with db.begin_nested():
                        try:
                            decide_releasable(
                                db, family_id, view.parameter_id, view.releasable,
                                request_hash=view.request_hash, actor_id=actor_id,
                            )
                            accepted += 1
                        except DomainError as exc:
                            if exc.code != CODE_PROPOSAL_STALE:
                                raise
                            stale += 1
                except DomainError:
                    failed += 1
            db.commit()
    except Exception:
        db.rollback()
        raise
    return {"accepted": accepted, "stale": stale, "failed": failed}


# ---------------------------------------------------------------------------
#  Уборка и постановка
# ---------------------------------------------------------------------------

def drop_stale_proposals(
    db: Session, schema_ids: Sequence[int], *, settings: Settings
) -> int:
    """Удаляет предложения версий, ставшие недействующими (версия не действующая,
    семья не `active`, параметр не главный, архивный или размечен, отпечаток не равен
    текущему рендеру). Гигиена таблицы; защиту держит повторный рендер при чтении и
    решении. Вызывающий держит семью `FOR UPDATE`. Возвращает число удалённых."""
    ids = sorted(set(schema_ids))
    if not ids:
        return 0
    rows = db.execute(
        sa.select(
            ReleasableProposal.parameter_id, ReleasableProposal.request_hash,
            FamilyParameter.schema_id, FamilyParameter.role, FamilyParameter.archived_at,
            FamilyParameter.releasable, FamilyParameterSchema.family_id,
        )
        .join(FamilyParameter, FamilyParameter.id == ReleasableProposal.parameter_id)
        .join(FamilyParameterSchema, FamilyParameterSchema.id == FamilyParameter.schema_id)
        .where(FamilyParameter.schema_id.in_(ids))
        .order_by(ReleasableProposal.parameter_id)
    ).all()
    hashes: dict[int, str | None] = {}
    stale: list[int] = []
    for row in rows:
        if row.schema_id not in hashes:
            hashes[row.schema_id] = _current_hash(db, row.family_id, row.schema_id, settings)
        if not (
            row.role == ParameterRole.main.value and row.archived_at is None
            and row.releasable is None and hashes[row.schema_id] == row.request_hash
        ):
            stale.append(row.parameter_id)
    if not stale:
        return 0
    return db.execute(
        sa.delete(ReleasableProposal)
        .where(ReleasableProposal.parameter_id.in_(stale))
        .execution_options(synchronize_session=False)
    ).rowcount


def enqueue_releasable(db: Session, schema_id: int, *, settings: Settings) -> bool:
    """Ставит задание версии, если нужно; не коммитит. Возвращает `True`, если
    задание создано (или возвращено в очередь).

    Незавершённые задания версии с отпечатком не равным текущему отменяются
    (`input_changed`; у версии, которую уже не спрашивают, — `not_applicable`).
    Задание ставится, если у версии есть неразмеченный активный главный параметр и
    задания с текущим отпечатком нет; отменённое задание с текущим отпечатком (вход
    вернули к прежнему) возвращается в очередь, а не заводится заново. Вызывающий
    держит семью `FOR UPDATE` до коммита: пути правки — сами, `enqueue_all_releasable` —
    на каждую версию."""
    schema = db.execute(
        sa.select(FamilyParameterSchema.family_id).where(FamilyParameterSchema.id == schema_id)
    ).one_or_none()
    if schema is None:
        return False
    family_id = schema.family_id
    material = load_releasable_material(db, family_id, schema_id)
    rendered = (
        None if material is None else render_releasable_material(material, settings=settings)
    )
    current_hash = None if rendered is None else rendered.request_hash

    unfinished = db.execute(
        sa.select(SemanticJob.id, SemanticJob.request_hash)
        .where(
            SemanticJob.kind == SemanticJobKind.releasable_params.value,
            SemanticJob.schema_id == schema_id,
            SemanticJob.status.in_(_UNFINISHED),
        )
        .order_by(SemanticJob.id)
    ).all()
    to_cancel = [row.id for row in unfinished if row.request_hash != current_hash]
    if to_cancel:
        _lock_jobs(db, to_cancel)
        db.execute(
            sa.update(SemanticJob)
            .where(SemanticJob.id.in_(to_cancel), SemanticJob.status.in_(_UNFINISHED))
            .values(
                status=SemanticJobStatus.cancelled.value,
                cancel_reason=(
                    SemanticCancelReason.input_changed
                    if material is not None else SemanticCancelReason.not_applicable
                ).value,
                claim_token=None,
            )
            .execution_options(synchronize_session=False)
        )
        db.flush()
        db.expire_all()
    if material is None or rendered is None or not material.has_unmarked:
        return False

    existing = db.execute(
        sa.select(SemanticJob.id, SemanticJob.status).where(
            SemanticJob.kind == SemanticJobKind.releasable_params.value,
            SemanticJob.schema_id == schema_id,
            SemanticJob.request_hash == current_hash,
        )
    ).first()
    if existing is not None:
        if existing.status != SemanticJobStatus.cancelled.value:
            return False
        _lock_jobs(db, [existing.id])
        revived = _revive_jobs(db, [existing.id])
        db.flush()
        db.expire_all()
        return revived > 0

    fingerprint = Fingerprint(
        SemanticJobKind.releasable_params, None, family_id, schema_id, rendered.request_hash
    )
    row = _job_row(fingerprint, rendered, unit_id=None, prompt_version=RELEASABLE_PROMPT_VERSION)
    row["response_schema_version"] = RELEASABLE_RESPONSE_SCHEMA_VERSION
    return _insert_job_rows(db, [row]) > 0


def refresh_inputs(db: Session, schema_ids: Sequence[int], *, settings: Settings) -> None:
    """Уборка после правки входов запроса: недействующие предложения удалены,
    незавершённое задание со старым отпечатком отменено, при неразмеченном главном
    поставлено новое. Вызывается путями правки в их транзакции под семьёй
    `FOR UPDATE`."""
    ids = sorted(set(schema_ids))
    drop_stale_proposals(db, ids, settings=settings)
    for schema_id in ids:
        enqueue_releasable(db, schema_id, settings=settings)


def refresh_family(db: Session, family_id: int, *, settings: Settings) -> None:
    """`refresh_inputs` по действующей версии семьи (правка имени, определения,
    единицы); версии нет — ничего."""
    schema_id = db.execute(
        sa.select(FamilyParameterSchema.id).where(
            FamilyParameterSchema.family_id == family_id,
            FamilyParameterSchema.status == SchemaStatus.frozen.value,
        )
    ).scalar_one_or_none()
    if schema_id is not None:
        refresh_inputs(db, [schema_id], settings=settings)


# ---------------------------------------------------------------------------
#  Команда releasable-enqueue-all
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnqueueAllReport:
    to_enqueue: int
    estimate_usd: Decimal
    enqueued: int


def _schemas_needing_job(db: Session) -> list[int]:
    unmarked = (
        sa.select(sa.literal(1))
        .select_from(FamilyParameter)
        .where(
            FamilyParameter.schema_id == FamilyParameterSchema.id,
            FamilyParameter.role == ParameterRole.main.value,
            FamilyParameter.archived_at.is_(None),
            FamilyParameter.releasable.is_(None),
        )
        .exists()
    )
    return list(
        db.execute(
            sa.select(FamilyParameterSchema.id)
            .join(WorkFamily, WorkFamily.id == FamilyParameterSchema.family_id)
            .where(
                FamilyParameterSchema.status == SchemaStatus.frozen.value,
                WorkFamily.status == FamilyStatus.active.value,
                unmarked,
            )
            .order_by(FamilyParameterSchema.id)
        ).scalars()
    )


def enqueue_all_releasable(
    db: Session,
    *,
    settings: Settings = app_settings,
    before_enqueue: Callable[[int, Decimal], None] | None = None,
) -> EnqueueAllReport:
    """Задания для всех действующих версий, где есть неразмеченный активный главный
    параметр и нет задания (не отменённого) с текущим отпечатком. Перед постановкой
    зовёт `before_enqueue(число заданий, оценка резерва)`. Идемпотентна.

    Команда идёт параллельно с приложением, поэтому каждая версия ставится под своей
    семьёй `FOR UPDATE`, взятой до чтения материала, и коммитится сразу: замок живёт одну
    постановку, больше одной семьи команда не держит, а порядка замков нескольких семей
    (по возрастанию `id`, как у слияния) не требуется. Подсчёт до `before_enqueue` читает
    без замков и только оценивает; решение принимает постановка под замком."""
    tariffs = tariffs_from(settings, SemanticJobKind.releasable_params)
    known: dict[str, int | None] = {}
    wanted: list[int] = []
    estimate = Decimal(0)
    for schema_id in _schemas_needing_job(db):
        family_id = db.execute(
            sa.select(FamilyParameterSchema.family_id).where(FamilyParameterSchema.id == schema_id)
        ).scalar_one()
        material = load_releasable_material(db, family_id, schema_id)
        if material is None:
            continue
        rendered = render_releasable_material(material, settings=settings)
        has_job = db.execute(
            sa.select(SemanticJob.id).where(
                SemanticJob.kind == SemanticJobKind.releasable_params.value,
                SemanticJob.schema_id == schema_id,
                SemanticJob.request_hash == rendered.request_hash,
                SemanticJob.status != SemanticJobStatus.cancelled.value,
            ).limit(1)
        ).first()
        if has_job is not None:
            continue
        wanted.append(schema_id)
        if rendered.prefix_hash not in known:
            known[rendered.prefix_hash] = known_prefix_tokens(db, rendered.prefix_hash)
        prefix = known[rendered.prefix_hash]
        estimate += reserve_for_known_prefix(
            prefix if prefix is not None else rendered.prefix_bytes,
            rendered, tariffs, rendered.body["max_tokens"],
        )
    if before_enqueue is not None:
        before_enqueue(len(wanted), estimate)
    enqueued = 0
    for schema_id in wanted:
        family_id = db.execute(
            sa.select(FamilyParameterSchema.family_id).where(FamilyParameterSchema.id == schema_id)
        ).scalar_one()
        db.execute(_lock_family_exclusive_statement(family_id)).all()
        enqueued += int(enqueue_releasable(db, schema_id, settings=settings))
        db.commit()
    return EnqueueAllReport(len(wanted), estimate, enqueued)
