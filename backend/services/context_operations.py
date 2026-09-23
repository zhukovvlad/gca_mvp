"""Операции оператора над контекстами и протокол блокировки корзины (спека
`2026-09-22-catalog-families-design.md` §2.4, §2.8, §2.14).

Задача 6 фичи «Семьи и контексты»
(`docs/superpowers/plans/2026-09-22-catalog-families.md`).

Четыре операции — **разделить**, **слить**, **перенести**, **архивировать**
(спека §2.4) — и общий для всех протокол сериализации (спека §2.8): единица
блокировки — корзина, каждая операция берёт `lock_buckets(db, [bucket_id],
exclusive=True)` (`FOR UPDATE`) ДО чтения предусловий и **перечитывает** их
ПОСЛЕ получения лока, отказывая по перечитанному, а не по показанному
оператору снимку. `FOR UPDATE` несовместим с `FOR SHARE` маршрутизации на
импорте (`services/context_routing.lock_buckets(exclusive=False)`) — значит
операция ждёт идущий импорт, а импорт ждёт идущую операцию.

**Перечитывание — не «второй `db.get`».** Identity map SQLAlchemy по
умолчанию не обновляет уже загруженный Python-объект данными нового
`SELECT`: значения, прочитанные ДО блокировки, останутся в памяти сессии,
даже если после `lock_buckets` выполнить `db.get` тем же ключом ещё раз.
Поэтому сразу после каждого `lock_buckets(..., exclusive=True)` вызывается
`db.expire_all()` — он не читает базу сам, а помечает уже загруженные
атрибуты «устаревшими», и следующее обращение к ним (в том числе через
`db.get`) обязано выполнить SVEЖИЙ `SELECT`. Без этого «снятие
перечитывания», которое бриф просит показать RED-прогоном, было бы снятием
только по названию: код выглядел бы перечитывающим, но фактически читал бы
из памяти тот же снимок, что и до лока.

Бакет для лока определяется одним чтением ДО блокировки (`context.bucket_id`
у каждой строки `catalog_contexts` — неизменяемое поле, ни одна операция в
проекте его не переписывает, гоняться за его гонкой незачем). Мутируемые же
факты предусловий — `archived_at`, `is_default`, число членств, число
входящих правил, `context_members.bucket_id` (может смениться при
пересчёте эффективной статьи, спека §2.8) — читаются ИСКЛЮЧИТЕЛЬНО ПОСЛЕ
`lock_buckets` + `expire_all`.

Флаг `is_default` переезжает АТОМАРНО в два шага с `flush` между ними
(«снять у старого, затем поставить новому») — частичный уникальный индекс
`uq_catalog_contexts_default_per_bucket` (`bucket_id`) `WHERE is_default AND
archived_at IS NULL` иначе поймал бы транзиентное «два действующих
контекста по умолчанию разом» той же корзины.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

import services.context_routing as context_routing_module
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    ContextRoutingRule,
    DecisionSource,
    RoutedBy,
    SemanticState,
)
from services.context_routing import (
    ChapterContext,
    RulePredicate,
    chapter_context,
    evaluate_predicate,
    lock_buckets,
)
from services.semantic_events import EVENT_ENUM_VALUES, record_event
from services.semantic_rules import PLACE_DICTIONARY_VERSION, classify_kind, classify_name_role


class ContextOperationError(Exception):
    """Отказ операции оператора: `code` — машиночитаемая строка (задача 12
    превратит её в доменный отказ через `raise_domain_error`), сообщение —
    человекочитаемое и называет число/корзину/контекст, а контекст отказа
    (число членств, id корзины, id контекста…) доступен именованными
    атрибутами (**context), а не только текстом сообщения."""

    def __init__(self, code: str, message: str, **context: object) -> None:
        super().__init__(message)
        self.code = code
        for key, value in context.items():
            setattr(self, key, value)


#: Три предусловия архивирования — ИМЕНА ИЗ ПЛАНА (Task 6, Interfaces).
REFUSE_CONTEXT_NOT_EMPTY = "context_not_empty"
REFUSE_INCOMING_RULES = "incoming_rules"
REFUSE_DEFAULT_WITHOUT_SUCCESSOR = "default_without_successor"

#: Коды СВЕРХ ПЛАНА (решение исполнителя, задача 6 — см. отчёт
#: `tasks/catalog-families-work/task6-executor.md`): отказы на входные
#: данные операций, которых три предусловия архивирования не покрывают —
#: контекст/корзина не найдены, операция над уже архивным контекстом,
#: контексты разных корзин (слияние, перенос), членство не принадлежит
#: контексту-источнику (разделение), негодный `new_default_context_id`
#: (архивирование), источник и цель совпадают (слияние), причина переноса
#: вне закрытого множества (перенос).
REFUSE_CONTEXT_NOT_FOUND = "context_not_found"
REFUSE_CONTEXT_ARCHIVED = "context_archived"
REFUSE_DIFFERENT_BUCKET = "different_bucket"
REFUSE_INVALID_MEMBERSHIP = "invalid_membership"
REFUSE_INVALID_NEW_DEFAULT = "invalid_new_default"
REFUSE_SAME_CONTEXT = "same_context"
REFUSE_INVALID_REASON = "invalid_reason"
#: Разделение с правилом (решение оркестратора MINOR-3, ревью задачи 6,
#: раунд 3): правило, поставленное СЛЕДОМ (`max+1`), обязано реально
#: маршрутизировать КАЖДОЕ выбранное членство — иначе следующая же
#: переоценка немаршрутного (не-`manual`) членства (повторный импорт,
#: backfill задачи 11) молча откатит разделение, либо более раннее правило
#: корзины перехватит позицию первым (спека §2.4: первое сработавшее правило
#: по возрастанию `ordinal` побеждает).
REFUSE_RULE_DOES_NOT_COVER = "rule_does_not_cover"


@dataclass(frozen=True)
class SplitResult:
    new_context_id: int
    moved_members: int
    rule_id: int | None
    default_replaced: bool


def _now() -> datetime:
    return datetime.now(UTC)


def _classify_new_context_semantics(catalog_position: CatalogPosition) -> dict[str, object]:
    """Вид, роль и `semantic_state` нового контекста — ТЕМИ ЖЕ правилами, что
    при создании контекста на импорте (`context_routing._create_default_context`,
    решение оркестратора брифа задачи 6). `_create_default_context` не
    переиспользуется целиком: она безусловно ставит `is_default=True`,
    что для контекста-приёмника разделённых членств неверно (и вставка
    временного `is_default=True` до снятия флага у старого нарушила бы
    частичный уникальный индекс посреди flush'а в ветке «разделение без
    правила»). Цепочка разделов — намеренно ПУСТАЯ (`chain=()`): у нового
    контекста разделения нет ОДНОЙ позиции, чью цепочку можно было бы
    считать образцовой для всех перенесённых членств разом (они могли прийти
    из разных строк цепочки), а вид/роль здесь — SUGGESTION, а не финальное
    решение (спека §2.5); оператор, разделивший корзину, эту роль при
    необходимости поправит вручную. Решение сверх брифа — см. отчёт.
    """
    unit_norm = context_routing_module._unit_norm_of(catalog_position)
    semantic_kind = classify_kind(unit_norm)
    role_outcome = classify_name_role(catalog_position.standard_job_title, chapter_chain=())
    semantic_state = (
        SemanticState.NOT_APPLICABLE.value
        if catalog_position.kind in context_routing_module._NOT_APPLICABLE_CATALOG_KINDS
        else SemanticState.SUGGESTED.value
    )
    return {
        "semantic_kind": semantic_kind,
        "name_role": role_outcome.role,
        "comparability_reason": role_outcome.comparability_reason,
        "semantic_state": semantic_state,
    }


def _create_context(
    db: Session,
    *,
    bucket: ContextBucket,
    catalog_position: CatalogPosition,
    is_default: bool,
    actor_id: int,
) -> CatalogContext:
    """Создаёт контекст разделения (спека §2.4, §2.14): `origin='split'`
    ВСЕГДА — этот модуль создаёт контексты только операцией «разделить».
    Пишет `context_created` (обязательное условие для КАЖДОГО создания
    контекста, спека §2.14)."""
    now = _now()
    semantics = _classify_new_context_semantics(catalog_position)
    context = CatalogContext(
        bucket_id=bucket.id,
        is_default=is_default,
        semantic_kind=semantics["semantic_kind"],
        semantic_kind_source=DecisionSource.rule.value,
        semantic_kind_by=None,
        semantic_kind_at=now,
        name_role=semantics["name_role"],
        name_role_source=DecisionSource.rule.value,
        name_role_by=None,
        name_role_at=now,
        place_dictionary_version=PLACE_DICTIONARY_VERSION,
        semantic_state=semantics["semantic_state"],
        comparability_reason=semantics["comparability_reason"],
    )
    db.add(context)
    db.flush()
    record_event(
        db,
        event_type="context_created",
        context_id=context.id,
        actor_id=actor_id,
        payload={"bucket_id": bucket.id, "origin": "split"},
    )
    return context


# ---------------------------------------------------------------------------
#  Разделить
# ---------------------------------------------------------------------------

def split_context(
    db: Session,
    *,
    context_id: int,
    position_item_ids: list[int],
    rule: RulePredicate | None,
    actor_id: int,
) -> SplitResult:
    """Разделяет контекст: выбранные членства переезжают в НОВЫЙ контекст той
    же корзины (спека §2.4).

    Дальше одна из двух ветвей, которую выбирает вызывающий передачей
    `rule`:

    * **с исполнимым правилом** — правило встаёт в корзину
      (`ordinal = max+1`), перенесённые членства получают
      `routed_by='rule'` на НОВОЕ правило; действующий контекст по
      умолчанию корзины не трогается вовсе (будущие импорты, которые
      правилу не соответствуют, по-прежнему попадают в него — решение
      оркестратора брифа, обоснование в докстроке `SplitResult`);
    * **без правила** — перенесённые членства получают `routed_by='manual'`
      (позиции выбрал человек). Если разделяемый контекст был ДЕЙСТВУЮЩИМ
      контекстом по умолчанию корзины — заводится ещё один, СВЕЖИЙ пустой
      контекст, который становится новым умолчанием ВМЕСТО него (прежний
      теряет только флаг, не архивируется); если разделяемый контекст не
      был умолчанием — замена не нужна (`default_replaced=False`, второй
      контекст не создаётся), потому что будущие импорты и так минуют
      разделяемый контекст.

    Raises:
        RoutingError: `rule` синтаксически невалиден (неизвестный вид
            предиката либо без обязательного ключа) — та же проверка,
            которую иначе провёл бы первый же импорт, применяющий это
            правило; решение оркестратора — не откладывать эту проверку до
            маршрутизации.
        ContextOperationError: `position_item_ids` пуст
            (`REFUSE_INVALID_MEMBERSHIP`); контекст не найден
            (`REFUSE_CONTEXT_NOT_FOUND`); контекст архивирован
            (`REFUSE_CONTEXT_ARCHIVED`, перечитанное); хотя бы одна позиция
            не является членством ИМЕННО этого контекста, перечитанным
            ПОСЛЕ блокировки (`REFUSE_INVALID_MEMBERSHIP`); с правилом —
            хотя бы одно выбранное членство не оказалось бы в новом
            контексте реальной маршрутизацией: предикат его не покрывает
            либо более раннее правило корзины перехватывает его первым
            (`REFUSE_RULE_DOES_NOT_COVER`, ничего не записано).
    """
    if not position_item_ids:
        raise ContextOperationError(
            REFUSE_INVALID_MEMBERSHIP,
            "разделение контекста требует хотя бы одно членство",
            context_id=context_id,
            position_item_ids=[],
        )

    if rule is not None:
        # Проверяет ТОЛЬКО форму предиката (неизвестный вид/отсутствующий
        # обязательный ключ) — истинность на пустой цепочке роли не играет,
        # это побочный эффект переиспользования готовой проверки формы.
        evaluate_predicate(
            rule, ChapterContext(chain=(), nearest=None, category_id=None, category_source=None)
        )

    source = db.get(CatalogContext, context_id)
    if source is None:
        raise ContextOperationError(
            REFUSE_CONTEXT_NOT_FOUND, f"контекст {context_id} не найден", context_id=context_id
        )
    bucket_id = source.bucket_id  # неизменяемое поле — безопасно читать до лока

    lock_buckets(db, [bucket_id], exclusive=True)
    db.expire_all()  # см. докстроку модуля — иначе следующий db.get вернёт кэш

    source = db.get(CatalogContext, context_id)
    if source.archived_at is not None:
        raise ContextOperationError(
            REFUSE_CONTEXT_ARCHIVED,
            f"контекст {context_id} архивирован — разделять нечего",
            context_id=context_id,
        )

    unique_ids = list(dict.fromkeys(position_item_ids))
    members = (
        db.execute(sa.select(ContextMember).where(ContextMember.position_item_id.in_(unique_ids)))
        .scalars()
        .all()
    )
    found_by_id = {member.position_item_id: member for member in members}
    invalid = sorted(
        pid
        for pid in unique_ids
        if pid not in found_by_id or found_by_id[pid].context_id != context_id
    )
    if invalid:
        raise ContextOperationError(
            REFUSE_INVALID_MEMBERSHIP,
            f"позиции не являются членствами контекста {context_id}: {invalid}",
            context_id=context_id,
            position_item_ids=invalid,
        )

    existing_rules: list[ContextRoutingRule] = []
    if rule is not None:
        # Правило встаёт ПОСЛЕДНИМ (`ordinal = max+1`) — по построению все
        # СУЩЕСТВУЮЩИЕ правила корзины проверяются раньше него в реальной
        # маршрутизации (`_apply_routing`: первое сработавшее по
        # возрастанию `ordinal` побеждает). Значит выбранное членство
        # окажется в НОВОМ контексте тогда и только тогда, когда НИ ОДНО
        # существующее правило не срабатывает на его цепочке разделов И
        # само новое правило срабатывает. Проверяется ДО любой записи —
        # отказ не должен оставлять частичного состояния (решение
        # оркестратора MINOR-3, ревью задачи 6, раунд 3).
        existing_rules = (
            db.execute(
                sa.select(ContextRoutingRule)
                .where(ContextRoutingRule.bucket_id == bucket_id)
                .order_by(ContextRoutingRule.ordinal)
            )
            .scalars()
            .all()
        )
        uncovered: list[int] = []
        for pid in unique_ids:
            chapters = chapter_context(db, pid)
            shadowed_by_earlier = any(
                evaluate_predicate(existing_rule.predicate, chapters)
                for existing_rule in existing_rules
            )
            if shadowed_by_earlier or not evaluate_predicate(rule, chapters):
                uncovered.append(pid)
        if uncovered:
            raise ContextOperationError(
                REFUSE_RULE_DOES_NOT_COVER,
                f"новое правило не покрывает {len(uncovered)} из {len(unique_ids)} "
                "выбранных членств (предикат не совпадает либо позицию перехватывает "
                f"более раннее правило корзины): {sorted(uncovered)}",
                context_id=context_id,
                position_item_ids=sorted(uncovered),
                count=len(uncovered),
            )

    bucket = db.get(ContextBucket, bucket_id)
    catalog_position = db.get(CatalogPosition, bucket.catalog_position_id)

    target = _create_context(
        db, bucket=bucket, catalog_position=catalog_position, is_default=False, actor_id=actor_id
    )

    new_rule_id: int | None = None
    if rule is not None:
        max_ordinal = existing_rules[-1].ordinal if existing_rules else 0
        new_rule = ContextRoutingRule(
            bucket_id=bucket_id,
            ordinal=max_ordinal + 1,
            predicate=rule,
            context_id=target.id,
            created_by=actor_id,
        )
        db.add(new_rule)
        db.flush()
        new_rule_id = new_rule.id

    for pid in unique_ids:
        member = found_by_id[pid]
        member.context_id = target.id
        if rule is not None:
            member.routed_by = RoutedBy.rule.value
            member.routing_rule_id = new_rule_id
        else:
            member.routed_by = RoutedBy.manual.value
            member.routing_rule_id = None
    db.flush()

    default_replaced = rule is None and bool(source.is_default)
    if default_replaced:
        source.is_default = False
        db.flush()
        _create_context(
            db, bucket=bucket, catalog_position=catalog_position, is_default=True, actor_id=actor_id
        )
        db.flush()

    record_event(
        db,
        event_type="context_split",
        context_id=target.id,
        actor_id=actor_id,
        payload={
            "from_context_id": context_id,
            "moved_members": len(unique_ids),
            "rule_id": new_rule_id,
        },
    )

    return SplitResult(
        new_context_id=target.id,
        moved_members=len(unique_ids),
        rule_id=new_rule_id,
        default_replaced=default_replaced,
    )


# ---------------------------------------------------------------------------
#  Слить
# ---------------------------------------------------------------------------

def merge_contexts(
    db: Session, *, source_context_id: int, target_context_id: int, actor_id: int
) -> int:
    """Сливает `source_context_id` в `target_context_id` (спека §2.4).

    Членства источника переезжают в цель БЕЗ изменения `routed_by`: ручные
    остаются ручными, а правила, указывавшие на источник, переводятся на
    цель (`context_routing_rules.context_id`) — сама строка членства при
    этом `routed_by='rule'`/`routing_rule_id` не меняет, потому что ссылка
    та же самая, просто теперь указывает на цель. Источник архивируется; если
    он был действующим контекстом по умолчанию корзины, флаг переходит к
    цели (иначе корзина осталась бы без умолчания, а маршрутизация
    fail-closed, спека §2.4).

    Raises:
        ContextOperationError: `source_context_id == target_context_id`
            (`REFUSE_SAME_CONTEXT`); контекст не найден
            (`REFUSE_CONTEXT_NOT_FOUND`); контексты разных корзин
            (`REFUSE_DIFFERENT_BUCKET`); источник или цель уже архивны,
            перечитанное (`REFUSE_CONTEXT_ARCHIVED`).
    """
    if source_context_id == target_context_id:
        raise ContextOperationError(
            REFUSE_SAME_CONTEXT,
            f"источник и цель слияния совпадают: {source_context_id}",
            context_id=source_context_id,
        )

    source = db.get(CatalogContext, source_context_id)
    if source is None:
        raise ContextOperationError(
            REFUSE_CONTEXT_NOT_FOUND,
            f"контекст {source_context_id} не найден",
            context_id=source_context_id,
        )
    target = db.get(CatalogContext, target_context_id)
    if target is None:
        raise ContextOperationError(
            REFUSE_CONTEXT_NOT_FOUND,
            f"контекст {target_context_id} не найден",
            context_id=target_context_id,
        )
    if source.bucket_id != target.bucket_id:
        raise ContextOperationError(
            REFUSE_DIFFERENT_BUCKET,
            f"контексты {source_context_id} и {target_context_id} принадлежат разным корзинам",
            source_context_id=source_context_id,
            target_context_id=target_context_id,
        )
    bucket_id = source.bucket_id

    lock_buckets(db, [bucket_id], exclusive=True)
    db.expire_all()

    source = db.get(CatalogContext, source_context_id)
    target = db.get(CatalogContext, target_context_id)
    if source.archived_at is not None:
        raise ContextOperationError(
            REFUSE_CONTEXT_ARCHIVED,
            f"контекст {source_context_id} уже архивирован",
            context_id=source_context_id,
        )
    if target.archived_at is not None:
        raise ContextOperationError(
            REFUSE_CONTEXT_ARCHIVED,
            f"контекст {target_context_id} уже архивирован",
            context_id=target_context_id,
        )

    members = (
        db.execute(sa.select(ContextMember).where(ContextMember.context_id == source_context_id))
        .scalars()
        .all()
    )
    for member in members:
        member.context_id = target_context_id
    db.flush()
    moved_count = len(members)

    rules = (
        db.execute(
            sa.select(ContextRoutingRule).where(
                ContextRoutingRule.bucket_id == bucket_id,
                ContextRoutingRule.context_id == source_context_id,
            )
        )
        .scalars()
        .all()
    )
    for rule_row in rules:
        rule_row.context_id = target_context_id
    db.flush()

    if source.is_default:
        source.is_default = False
        db.flush()
        target.is_default = True
        db.flush()

    source.archived_at = _now()
    db.flush()

    record_event(
        db,
        event_type="context_merged",
        context_id=source_context_id,
        actor_id=actor_id,
        payload={"into_context_id": target_context_id, "moved_members": moved_count},
    )
    record_event(
        db,
        event_type="context_archived",
        context_id=source_context_id,
        actor_id=actor_id,
        payload={"reason": "context_merge"},
    )

    return moved_count


# ---------------------------------------------------------------------------
#  Перенести
# ---------------------------------------------------------------------------

def move_members(
    db: Session,
    *,
    position_item_ids: list[int],
    target_context_id: int,
    actor_id: int,
    reason: str,
) -> int:
    """Переносит членства в `target_context_id` вручную (спека §2.4):
    `routed_by='manual'`, `routing_rule_id=NULL` — последующая маршрутизация
    такое членство не переписывает (задача 4).

    Если переносимые позиции пришли из НЕСКОЛЬКИХ разных контекстов — по
    одному `members_moved` на каждый исходный контекст (предмет события —
    ЦЕЛЬ, `from_context_id` — источник, спека §2.14).

    Raises:
        ContextOperationError: `reason` вне `EVENT_ENUM_VALUES[("members_moved",
            "reason")]` (`REFUSE_INVALID_REASON`, проверяется первым — до
            любого чтения базы); `position_item_ids` пуст или содержит id без
            членства (`REFUSE_INVALID_MEMBERSHIP`); цель не найдена
            (`REFUSE_CONTEXT_NOT_FOUND`); членства и цель — в разных корзинах
            (`REFUSE_DIFFERENT_BUCKET`); цель архивна, перечитанное
            (`REFUSE_CONTEXT_ARCHIVED`).
    """
    allowed_reasons = EVENT_ENUM_VALUES[("members_moved", "reason")]
    if reason not in allowed_reasons:
        raise ContextOperationError(
            REFUSE_INVALID_REASON,
            f"недопустимая причина переноса: {reason!r}; допустимые: {sorted(allowed_reasons)}",
            reason=reason,
        )
    if not position_item_ids:
        raise ContextOperationError(
            REFUSE_INVALID_MEMBERSHIP,
            "перенос требует хотя бы одно членство",
            position_item_ids=[],
        )

    target = db.get(CatalogContext, target_context_id)
    if target is None:
        raise ContextOperationError(
            REFUSE_CONTEXT_NOT_FOUND,
            f"контекст {target_context_id} не найден",
            context_id=target_context_id,
        )

    unique_ids = list(dict.fromkeys(position_item_ids))
    members = (
        db.execute(sa.select(ContextMember).where(ContextMember.position_item_id.in_(unique_ids)))
        .scalars()
        .all()
    )
    found_by_id = {member.position_item_id: member for member in members}
    missing = sorted(pid for pid in unique_ids if pid not in found_by_id)
    if missing:
        raise ContextOperationError(
            REFUSE_INVALID_MEMBERSHIP,
            f"позиции без членства: {missing}",
            position_item_ids=missing,
        )

    bucket_ids = {member.bucket_id for member in found_by_id.values()}
    bucket_ids.add(target.bucket_id)
    if len(bucket_ids) != 1:
        raise ContextOperationError(
            REFUSE_DIFFERENT_BUCKET,
            "переносимые членства и целевой контекст принадлежат разным корзинам",
            bucket_ids=sorted(bucket_ids),
        )
    bucket_id = next(iter(bucket_ids))

    lock_buckets(db, [bucket_id], exclusive=True)
    db.expire_all()

    target = db.get(CatalogContext, target_context_id)
    if target.archived_at is not None:
        raise ContextOperationError(
            REFUSE_CONTEXT_ARCHIVED,
            f"контекст {target_context_id} архивирован",
            context_id=target_context_id,
        )

    members = (
        db.execute(sa.select(ContextMember).where(ContextMember.position_item_id.in_(unique_ids)))
        .scalars()
        .all()
    )
    found_by_id = {member.position_item_id: member for member in members}
    # Перечитывание существования — не только состояния: членство могло
    # уйти каскадом (удаление сметы/раунда/договора, спека §2.8) МЕЖДУ
    # первым чтением и блокировкой. Без повторной проверки цикл ниже упал
    # бы `KeyError` вместо доменного отказа (NIT-2, ревью задачи 6,
    # раунд 3).
    missing = sorted(pid for pid in unique_ids if pid not in found_by_id)
    if missing:
        raise ContextOperationError(
            REFUSE_INVALID_MEMBERSHIP,
            f"позиции без членства (после блокировки): {missing}",
            position_item_ids=missing,
        )
    bucket_ids = {member.bucket_id for member in found_by_id.values()}
    bucket_ids.add(target.bucket_id)
    if len(bucket_ids) != 1:
        raise ContextOperationError(
            REFUSE_DIFFERENT_BUCKET,
            "переносимые членства и целевой контекст принадлежат разным корзинам "
            "(после блокировки)",
            bucket_ids=sorted(bucket_ids),
        )

    from_counts: dict[int, int] = {}
    for pid in unique_ids:
        member = found_by_id[pid]
        from_context_id = member.context_id
        from_counts[from_context_id] = from_counts.get(from_context_id, 0) + 1
        member.context_id = target_context_id
        member.bucket_id = bucket_id
        member.routed_by = RoutedBy.manual.value
        member.routing_rule_id = None
    db.flush()

    for from_context_id, count in from_counts.items():
        record_event(
            db,
            event_type="members_moved",
            context_id=target_context_id,
            actor_id=actor_id,
            payload={"from_context_id": from_context_id, "moved_members": count, "reason": reason},
        )

    return len(unique_ids)


# ---------------------------------------------------------------------------
#  Архивировать
# ---------------------------------------------------------------------------

def archive_context(
    db: Session, *, context_id: int, new_default_context_id: int | None, actor_id: int
) -> None:
    """Архивирует контекст — только явная операция, три предусловия (спека
    §2.4, §2.8):

    1. контекст пуст — ни одного членства (`REFUSE_CONTEXT_NOT_EMPTY`);
    2. в контекст не ведёт ни одно правило маршрутизации
       (`REFUSE_INCOMING_RULES`);
    3. если контекст — действующий контекст по умолчанию корзины, новый
       обязан быть назначен одновременно (`REFUSE_DEFAULT_WITHOUT_SUCCESSOR`,
       если `new_default_context_id is None`; `REFUSE_INVALID_NEW_DEFAULT`,
       если он не годится — не найден, чужой корзины, уже архивен, либо
       совпадает с архивируемым).

    Если контекст НЕ является умолчанием корзины, `new_default_context_id`
    игнорируется, даже если передан, — предусловие 3 плана относится ТОЛЬКО
    к действующему умолчанию (решение сверх брифа, см. отчёт).

    Raises:
        ContextOperationError: контекст не найден (`REFUSE_CONTEXT_NOT_FOUND`);
            уже архивирован, перечитанное (`REFUSE_CONTEXT_ARCHIVED`); одно из
            трёх предусловий выше, каждое перечитанное ПОСЛЕ блокировки.
    """
    context = db.get(CatalogContext, context_id)
    if context is None:
        raise ContextOperationError(
            REFUSE_CONTEXT_NOT_FOUND, f"контекст {context_id} не найден", context_id=context_id
        )
    bucket_id = context.bucket_id

    lock_buckets(db, [bucket_id], exclusive=True)
    db.expire_all()

    context = db.get(CatalogContext, context_id)
    if context.archived_at is not None:
        raise ContextOperationError(
            REFUSE_CONTEXT_ARCHIVED,
            f"контекст {context_id} уже архивирован",
            context_id=context_id,
        )

    member_count = db.execute(
        sa.select(sa.func.count())
        .select_from(ContextMember)
        .where(ContextMember.context_id == context_id)
    ).scalar_one()
    if member_count > 0:
        raise ContextOperationError(
            REFUSE_CONTEXT_NOT_EMPTY,
            f"контекст {context_id} не пуст: членств {member_count}",
            context_id=context_id,
            count=member_count,
        )

    rule_count = db.execute(
        sa.select(sa.func.count())
        .select_from(ContextRoutingRule)
        .where(ContextRoutingRule.context_id == context_id)
    ).scalar_one()
    if rule_count > 0:
        raise ContextOperationError(
            REFUSE_INCOMING_RULES,
            f"в контекст {context_id} ведут правила маршрутизации: {rule_count}",
            context_id=context_id,
            count=rule_count,
        )

    new_default: CatalogContext | None = None
    if context.is_default:
        if new_default_context_id is None:
            raise ContextOperationError(
                REFUSE_DEFAULT_WITHOUT_SUCCESSOR,
                f"корзина {bucket_id}: контекст {context_id} — действующий по умолчанию, "
                "нужен new_default_context_id",
                bucket_id=bucket_id,
            )
        new_default = db.get(CatalogContext, new_default_context_id)
        if (
            new_default is None
            or new_default.bucket_id != bucket_id
            or new_default.archived_at is not None
            or new_default.id == context_id
        ):
            raise ContextOperationError(
                REFUSE_INVALID_NEW_DEFAULT,
                f"контекст {new_default_context_id} не годится в новое умолчание "
                f"корзины {bucket_id}",
                bucket_id=bucket_id,
                new_default_context_id=new_default_context_id,
            )

    if new_default is not None:
        context.is_default = False
        db.flush()
        new_default.is_default = True
        db.flush()

    context.archived_at = _now()
    db.flush()

    record_event(
        db,
        event_type="context_archived",
        context_id=context_id,
        actor_id=actor_id,
        payload={"reason": "operator"},
    )
