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
перечитывания» было бы снятием
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
from sqlalchemy.orm import Session, aliased

import services.context_routing as context_routing_module
from models import (
    CatalogContext,
    CatalogPosition,
    ContextBucket,
    ContextMember,
    ContextRoutingRule,
    DecisionSource,
    MembershipState,
    PositionItem,
    RoutedBy,
    SemanticState,
)
from services.context_routing import (
    NO_CATEGORY_SENTINEL,
    ChapterContext,
    RulePredicate,
    chapter_context,
    evaluate_predicate,
    lock_buckets,
)
from services.semantic_events import record_event
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

#: Коды СВЕРХ ПЛАНА: отказы на входные
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
#: Разделение с правилом: правило, поставленное СЛЕДОМ (`max+1`), обязано реально
#: маршрутизировать КАЖДОЕ выбранное членство — иначе следующая же
#: переоценка немаршрутного (не-`manual`) членства (повторный импорт,
#: backfill задачи 11) молча откатит разделение, либо более раннее правило
#: корзины перехватит позицию первым (спека §2.4: первое сработавшее правило
#: по возрастанию `ordinal` побеждает).
REFUSE_RULE_DOES_NOT_COVER = "rule_does_not_cover"
#: Задача 9 («принять решение цели», спека §2.8): членство без конфликта
#: (сверх плана — план называет только предусловия архивирования и трёх
#: прежних операций; отказ на входные данные ЭТОЙ операции план не называет,
#: тот же приём, что семь кодов выше).
REFUSE_NOT_CONFLICTED = "not_conflicted"
#: Задача 10 («принять предложение переноса», спека §2.8): эффективная статья
#: позиции успела измениться между показом предложения и принятием под
#: блокировкой (сверх плана — тот же приём, что коды выше). Отказ несёт
#: свежее `TransferProposal` атрибутом `new_proposal` (может быть `None`,
#: если позиция успела вернуться в `CURRENT`). Тем же кодом отказывает и
#: гонка на целевой корзине: если корзина, найденная ПОСЛЕ блокировки по
#: свежей статье, не входит в заблокированный набор — значит она появилась
#: МЕЖДУ первым чтением и локом, и держать её мы не можем; отказ и здесь
#: несёт свежее предложение.
REFUSE_CATEGORY_CHANGED = "category_changed"
#: Задача 10, сверх плана: принятие предложения на `CURRENT`-членстве —
#: переносить нечего, `transfer_proposal` на нём уже вернул бы `None`. Свой
#: код, а не `REFUSE_INVALID_MEMBERSHIP`: членство существует и валидно,
#: просто не устарело.
REFUSE_NOT_STALE = "not_stale"


@dataclass(frozen=True)
class SplitResult:
    new_context_id: int
    moved_members: int
    rule_id: int | None
    default_replaced: bool


def _now() -> datetime:
    return datetime.now(UTC)


def _classify_new_context_semantics(
    catalog_position: CatalogPosition, *, chain: tuple[str, ...] = ()
) -> dict[str, object]:
    """Вид, роль и `semantic_state` нового контекста — ТЕМИ ЖЕ правилами, что
    при создании контекста на импорте (`context_routing._create_default_context`).
    `_create_default_context` не переиспользуется целиком: она безусловно ставит `is_default=True`,
    что для контекста-приёмника разделённых членств неверно (и вставка
    временного `is_default=True` до снятия флага у старого нарушила бы
    частичный уникальный индекс посреди flush'а в ветке «разделение без
    правила»).

    Цепочка разделов (`chain`) — у группы перенесённых членств действительно
    нет ОДНОЙ образцовой цепочки (они могли прийти из разных строк цепочки),
    но взять чью-то, а не никакую, существенно для `LOCATION_ONLY`-имени под
    рабочим разделом: на пустой цепочке роль пересчиталась бы в
    `insufficient_description`, хотя состав описан не хуже, чем был (тот же
    дефект, что нашла и исправила задача 11 для пересчёта словаря). Вызывающий
    передаёт цепочку ПРЕДСТАВИТЕЛЬНОГО членства (наименьший
    `position_item_id` среди переносимых); умолчание `()` — для контекста БЕЗ
    единого переносимого членства (свежий пустой контекст по умолчанию взамен
    разделённого), где представителя нет по построению. Вид/роль здесь в
    любом случае SUGGESTION, а не финальное решение (спека §2.5); оператор,
    разделивший корзину, при необходимости поправит их вручную.
    """
    unit_norm = context_routing_module._unit_norm_of(catalog_position)
    semantic_kind = classify_kind(unit_norm)
    role_outcome = classify_name_role(catalog_position.standard_job_title, chapter_chain=chain)
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
    chain: tuple[str, ...] = (),
) -> CatalogContext:
    """Создаёт контекст разделения (спека §2.4, §2.14): `origin='split'`
    ВСЕГДА — этот модуль создаёт контексты только операцией «разделить».
    Пишет `context_created` (обязательное условие для КАЖДОГО создания
    контекста, спека §2.14). `chain` — см. докстроку
    `_classify_new_context_semantics`."""
    now = _now()
    semantics = _classify_new_context_semantics(catalog_position, chain=chain)
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
      правилу не соответствуют, по-прежнему попадают в него — обоснование
      в докстроке `SplitResult`);
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
            правило; эта проверка не откладывается до
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
        # отказ не должен оставлять частичного состояния.
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

    # Цепочка представителя — членство с наименьшим `position_item_id` среди
    # переносимых (см. докстроку `_classify_new_context_semantics`), не
    # пустая: иначе `LOCATION_ONLY`-имя под рабочим разделом теряло бы
    # рабочее имя при разделении.
    representative_chain = chapter_context(db, min(unique_ids)).chain
    target = _create_context(
        db, bucket=bucket, catalog_position=catalog_position, is_default=False, actor_id=actor_id,
        chain=representative_chain,
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

    `reason` допускает только `"manual"`: эта операция — ручной перенос
    оператором, а не канал для записи в журнал причин других операций
    (`stale_accepted` пишет `accept_transfer`, `review_merge` — слияние в
    Review; каждая — напрямую своим событием, не через эту функцию). Более
    широкое множество `EVENT_ENUM_VALUES[("members_moved", "reason")]`
    описывает журнал в целом, а не то, что вправе заявить вызывающий здесь.

    Raises:
        ContextOperationError: `reason` не `"manual"` (`REFUSE_INVALID_REASON`,
            проверяется первым — до любого чтения базы); `position_item_ids`
            пуст или содержит id без членства (`REFUSE_INVALID_MEMBERSHIP`);
            цель не найдена (`REFUSE_CONTEXT_NOT_FOUND`); членства и цель — в
            разных корзинах (`REFUSE_DIFFERENT_BUCKET`); цель архивна,
            перечитанное (`REFUSE_CONTEXT_ARCHIVED`).
    """
    allowed_reasons = {"manual"}
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
    # бы `KeyError` вместо доменного отказа.
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
    к действующему умолчанию.

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


# ---------------------------------------------------------------------------
#  Принять решение цели (задача 9, спека §2.8 «Слияние в Review»)
# ---------------------------------------------------------------------------

def accept_target_decision(db: Session, *, position_item_ids: list[int], actor_id: int) -> int:
    """Оператор принимает решение ЦЕЛИ по конфликтным членствам, возникшим
    при слиянии в Review (спека §2.8): снимает `conflict_at` и
    `conflict_from_context_id` ВМЕСТЕ — `CHECK` равносильности
    (`ck_context_members_conflict_pair`) иначе отвергнет строку, будь снята
    только одна колонка. `context_id` и `membership_state` не трогает:
    позиция уже лежит в правильной корзине (маршрут актуален), а «перенести в
    другой контекст» — это отдельная операция `move_members`, не эта.

    `actor_id` принимается по сигнатуре плана (Task 9, Interfaces); в фиче 1
    для этого перехода нет своего типа события (§2.14 не называет такого) —
    поэтому он не пишется в журнал, а `actor_id` не используется в теле
    (решение сверх плана задачи 9).

    `FOR UPDATE` — на корзину(ы) членств, а не на единую цель: в отличие от
    `move_members`, у конфликтных членств нет ОДНОЙ общей цели, и они не
    обязаны лежать в одной корзине.

    Raises:
        ContextOperationError: `position_item_ids` пуст либо содержит id без
            членства, ПЕРЕЧИТАННОЕ после блокировки (`REFUSE_INVALID_MEMBERSHIP`);
            хотя бы одно членство без конфликта (`REFUSE_NOT_CONFLICTED`, код
            сверх плана) — называет ИМЕННО неконфликтные id, а не все входные.
    """
    if not position_item_ids:
        raise ContextOperationError(
            REFUSE_INVALID_MEMBERSHIP,
            "принятие решения цели требует хотя бы одно членство",
            position_item_ids=[],
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

    bucket_ids = sorted({member.bucket_id for member in found_by_id.values()})
    lock_buckets(db, bucket_ids, exclusive=True)
    db.expire_all()  # см. докстроку модуля — иначе следующий db.get вернёт кэш

    # Перечитывание существования — не только состояния конфликта: членство
    # могло уйти каскадом МЕЖДУ первым чтением и блокировкой (тот же приём,
    # что в `move_members`).
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
            f"позиции без членства (после блокировки): {missing}",
            position_item_ids=missing,
        )

    not_conflicted = sorted(pid for pid, member in found_by_id.items() if member.conflict_at is None)
    if not_conflicted:
        raise ContextOperationError(
            REFUSE_NOT_CONFLICTED,
            f"членства без конфликта не могут принять решение цели: {not_conflicted}",
            position_item_ids=not_conflicted,
            count=len(not_conflicted),
        )

    for pid in unique_ids:
        member = found_by_id[pid]
        member.conflict_at = None
        member.conflict_from_context_id = None
    db.flush()

    return len(unique_ids)


# ---------------------------------------------------------------------------
#  Каскадные события (задача 10, спека §2.5, §2.8): ручной разнос помечает
#  членства `STALE`, предложение и принятие переноса.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RefreshReport:
    """Итог приведения `membership_state` к вычисляемому предикату — три
    исхода ПОРОЗНЬ: один счётчик «затронуто» не
    отличил бы приведение в `CURRENT` от приведения в `STALE`."""

    marked_stale: int
    marked_current: int
    unchanged: int


def refresh_membership_states(db: Session, *, position_item_ids: list[int]) -> RefreshReport:
    """Приводит хранимое `membership_state` к вычисляемому предикату — В ОБЕ
    СТОРОНЫ (спека §2.5, DoD 12):

    `STALE` ⇔ `COALESCE(bucket.work_category_id, -1) <>
    COALESCE(эффективная статья позиции, -1)`; иначе `CURRENT`. В Python
    сравнение `bucket.work_category_id != effective` даёт тот же ответ БЕЗ
    сентинела: `None != None` — `False` (обе стороны «нет статьи», не стало),
    `None != N` — `True` (стало), `N != M` — как обычно; сентинел
    (`NO_CATEGORY_SENTINEL`) нужен только SQL-версии предиката
    (`stale_mismatch_report`), где `NULL` не совпадает сам с собой.

    Затрагивает ТОЛЬКО строки с явным членством (`context_members` по
    `position_item_id`) — строки-разделы и несопоставленные позиции членства
    не имеют по построению (спека §2.2) и пропускаются молча тем же `IN`,
    не отдельной веткой. Пустой `position_item_ids` — тот же путь, не особый
    случай: `IN (пусто)` не находит строк, дальнейшее тело — no-op, `flush`
    ничего не коммитит (нет грязных объектов), `RefreshReport(0, 0, 0)`
    возвращается тем же кодом, что и на непустом входе без изменений.
    Ранний выход на пустом списке, условный `flush` и дедупликация
    `position_item_ids` были ТРЕМЯ эквивалентными решениями — ни одно не
    меняет результат ни на одном входе, убраны.

    Пишет `members_marked_stale` (`count`, `trigger='category_override'`) на
    КАЖДЫЙ контекст, где хотя бы одно его членство стало `STALE` в ЭТОМ
    вызове (спека §2.14); при нуле — событие не пишется. Предмет события —
    контекст, ДО которого домаршрутизации ещё не дошло: разнос помечает
    несоответствие, не переносит (перенос — `accept_transfer`, отдельная
    операция).
    """
    members = (
        db.execute(sa.select(ContextMember).where(ContextMember.position_item_id.in_(position_item_ids)))
        .scalars()
        .all()
    )

    marked_stale = marked_current = unchanged = 0
    stale_by_context: dict[int, int] = {}

    for member in members:
        bucket = db.get(ContextBucket, member.bucket_id)
        effective = context_routing_module.effective_category_id(db, member.position_item_id)
        desired_stale = bucket.work_category_id != effective
        desired = MembershipState.STALE.value if desired_stale else MembershipState.CURRENT.value
        if member.membership_state == desired:
            unchanged += 1
            continue
        member.membership_state = desired
        if desired_stale:
            marked_stale += 1
            stale_by_context[member.context_id] = stale_by_context.get(member.context_id, 0) + 1
        else:
            marked_current += 1

    db.flush()

    for context_id, count in stale_by_context.items():
        record_event(
            db,
            event_type="members_marked_stale",
            context_id=context_id,
            payload={"count": count, "trigger": "category_override"},
        )

    return RefreshReport(marked_stale, marked_current, unchanged)


def stale_mismatch_report(db: Session) -> list[int]:
    """Сверка DoD 12 НЕЗАВИСИМЫМ запросом — не через
    `effective_category_id`: сверщик, делящий предикат с генератором,
    доказывает согласованность, а не правильность.

    Свой SQL: `position_items → chapter_item_id → её строка-раздел →
    work_category_id` (тот же ОДИН уровень, что материализован резолвером —
    статья раздела уже несёт унаследованное значение, спека §1.7, дальше
    цепочку ходить не нужно) против `context_buckets.work_category_id`
    членства, с тем же `COALESCE(-1)`, что у предиката выше.

    Возвращает `position_item_id` членств, у которых ХРАНИМОЕ
    `membership_state` НЕ РАВНО вычисленному этим запросом.
    """
    chapter = aliased(PositionItem)
    computed_stale = sa.func.coalesce(chapter.work_category_id, NO_CATEGORY_SENTINEL) != sa.func.coalesce(
        ContextBucket.work_category_id, NO_CATEGORY_SENTINEL
    )
    stored_stale = ContextMember.membership_state == MembershipState.STALE.value

    rows = db.execute(
        sa.select(ContextMember.position_item_id)
        .join(PositionItem, PositionItem.id == ContextMember.position_item_id)
        .outerjoin(chapter, chapter.id == PositionItem.chapter_item_id)
        .join(ContextBucket, ContextBucket.id == ContextMember.bucket_id)
        .where(computed_stale != stored_stale)
        .order_by(ContextMember.position_item_id)
    ).scalars().all()
    return list(rows)


def _resolve_target_context(
    db: Session, *, bucket: ContextBucket, chapters: ChapterContext
) -> tuple[CatalogContext, int | None, str]:
    """Обычная маршрутизация БЕЗ единой записи (спека §2.8): правило по
    возрастанию `ordinal`, иначе действующий контекст по умолчанию — та же
    логика, что `context_routing._apply_routing`, но БЕЗ ветки «ручное
    решение сильнее правила»: и предложение переноса, и его принятие вправе
    переопределить прежний ручной выбор члена — оператор явно решает
    перенести устаревшее членство, это не переоценка импортом (решение
    исполнителя: спека §2.8 такую переоценку правилом не запрещает, а
    молчаливое сохранение `routed_by='manual'` при явном переносе оператором
    оставило бы будущий импорт неспособным поправить маршрут).

    Возвращает `(context, routing_rule_id, routed_by)`.
    """
    rules = (
        db.execute(
            sa.select(ContextRoutingRule)
            .where(ContextRoutingRule.bucket_id == bucket.id)
            .order_by(ContextRoutingRule.ordinal)
        )
        .scalars()
        .all()
    )
    for rule in rules:
        if evaluate_predicate(rule.predicate, chapters):
            return db.get(CatalogContext, rule.context_id), rule.id, RoutedBy.rule.value
    return context_routing_module._live_default_context(db, bucket.id), None, RoutedBy.default.value


def _lookup_bucket(db: Session, *, catalog_position_id: int, work_category_id: int | None) -> ContextBucket | None:
    """Ищет существующую корзину «написание × статья» БЕЗ создания (спека
    §2.8: предложение переноса не пишет ни строки — корзина цели может ещё
    не существовать, и это законный исход, а не ошибка)."""
    return db.execute(
        sa.select(ContextBucket).where(
            ContextBucket.catalog_position_id == catalog_position_id,
            ContextBucket.work_category_id == work_category_id,
        )
    ).scalar_one_or_none()


@dataclass(frozen=True)
class TransferProposal:
    """Предложение переноса устаревшего членства — вычисляется, не хранится
    (спека §2.8). `None` у `transfer_proposal` для `CURRENT`-членства.

    **Отступление от типа плана**:
    `proposed_bucket_id`/`proposed_context_id` — `int | None`, а не `int`:
    корзины цели может ещё не быть, тогда оба поля `None`, и текст «корзина
    будет создана при принятии» несёт сам факт `None`, отдельного поля для
    него не заводится.
    """

    position_item_id: int
    current_context_id: int
    proposed_bucket_id: int | None
    proposed_context_id: int | None
    effective_category_id: int | None


def transfer_proposal(db: Session, *, position_item_id: int) -> TransferProposal | None:
    """Вычисляет предложение переноса БЕЗ единой записи (спека §2.8).

    `None` — членство `CURRENT`: переносить некуда, оно уже в правильной
    корзине. Для `STALE` — корзина по паре «то же написание × текущая
    эффективная статья», и в ней контекст обычной маршрутизацией
    (`_resolve_target_context`). Корзины цели может ещё не быть — тогда
    `proposed_bucket_id`/`proposed_context_id` — `None`.
    """
    member = db.get(ContextMember, position_item_id)
    if member is None:
        raise ContextOperationError(
            REFUSE_INVALID_MEMBERSHIP,
            f"позиция {position_item_id} без членства",
            position_item_ids=[position_item_id],
        )
    if member.membership_state != MembershipState.STALE.value:
        return None

    current_bucket = db.get(ContextBucket, member.bucket_id)
    chapters = chapter_context(db, position_item_id)
    effective = chapters.category_id

    target_bucket = _lookup_bucket(
        db, catalog_position_id=current_bucket.catalog_position_id, work_category_id=effective
    )
    if target_bucket is None:
        return TransferProposal(
            position_item_id=position_item_id,
            current_context_id=member.context_id,
            proposed_bucket_id=None,
            proposed_context_id=None,
            effective_category_id=effective,
        )

    target_context, _rule_id, _routed_by = _resolve_target_context(db, bucket=target_bucket, chapters=chapters)
    return TransferProposal(
        position_item_id=position_item_id,
        current_context_id=member.context_id,
        proposed_bucket_id=target_bucket.id,
        proposed_context_id=target_context.id,
        effective_category_id=effective,
    )


def accept_transfer(
    db: Session, *, position_item_id: int, expected_category_id: int | None, actor_id: int
) -> int:
    """Принимает предложение переноса под блокировкой и с перечитыванием
    (спека §2.8): корзины (текущая и цели, если она уже существует, по
    возрастанию `id`) — ПЕРВЫМИ, тем же порядком, что у любой другой мутации
    ветки («корзина → членство», не наоборот — см. `move_members`,
    `merge_contexts`, `split_context`, слияние в Review); `FOR UPDATE` на само
    членство берётся ПОСЛЕ них. Обе блокировки существуют до чтения
    `member.bucket_id`, использованного для выбора набора корзин, БЕЗ лока —
    поэтому после лока на членство `bucket_id` перечитывается и сверяется с
    запертым набором: если корзина членства успела смениться (гонка с
    `move_members`/слиянием/разделением МЕЖДУ этим первым чтением и локом на
    членство), запертый набор больше не описывает её положение, и операция
    отказывает НОВЫМ предложением, а не молча правит не ту корзину. Тем же
    перечитыванием заново проверяется и `membership_state` — членство могло
    стать `CURRENT` (другой `accept_transfer`, тот же перенос) между первым,
    незапертым чтением и локом.

    Раскрывшееся расхождение (корзина членства сменилась; ЛИБО
    `fresh_effective != expected_category_id`; ЛИБО целевая корзина, найденная
    свежей статьёй, не входит в набор, который мы реально держим под локом —
    гонка на СОЗДАНИИ корзины между первым чтением и локом) отказывает
    `REFUSE_CATEGORY_CHANGED`-ом с НОВЫМ предложением атрибутом
    `new_proposal`, ничего не перенося. Совпадение — членство маршрутизируется
    в корзину цели обычной маршрутизацией (корзина и контекст по умолчанию
    создаются при необходимости, `origin='stale_accepted'`),
    `membership_state` становится `CURRENT`, пишется `members_moved`
    (`reason='stale_accepted'`).

    **Принятие сбрасывает `routed_by='manual'`, если оно было**: предложение
    и его принятие маршрутизируют ОДНОЙ и той же функцией
    (`_resolve_target_context`), которая не несёт ветки «ручное решение
    сильнее правила» — показанное оператору предложение и то, что реально
    исполняется, обязаны совпадать, а принятие — явная операция оператора,
    вправе переопределить прежний ручной выбор (спека §2.8 явно этот случай
    не называет).

    Raises:
        ContextOperationError: `position_item_id` без членства
            (`REFUSE_INVALID_MEMBERSHIP`, оба чтения — до и после лока);
            членство `CURRENT`, а не `STALE`, при первом ИЛИ повторном
            (после лока) чтении (`REFUSE_NOT_STALE`, сверх плана — переносить
            нечего, `transfer_proposal` на нём вернул бы `None`);
            статья/целевая корзина/корзина самого членства разошлись с
            показанным (`REFUSE_CATEGORY_CHANGED`, см. выше).

    Возвращает число перенесённых членств (1).
    """
    member = db.execute(
        sa.select(ContextMember).where(ContextMember.position_item_id == position_item_id)
    ).scalar_one_or_none()
    if member is None:
        raise ContextOperationError(
            REFUSE_INVALID_MEMBERSHIP,
            f"позиция {position_item_id} без членства",
            position_item_ids=[position_item_id],
        )
    if member.membership_state != MembershipState.STALE.value:
        raise ContextOperationError(
            REFUSE_NOT_STALE,
            f"членство {position_item_id} не устарело (CURRENT) — переносить нечего",
            position_item_id=position_item_id,
        )

    current_bucket = db.get(ContextBucket, member.bucket_id)
    catalog_position_id = current_bucket.catalog_position_id
    chapters = chapter_context(db, position_item_id)
    candidate_effective = chapters.category_id
    candidate_target_bucket = _lookup_bucket(
        db, catalog_position_id=catalog_position_id, work_category_id=candidate_effective
    )

    bucket_ids = sorted(
        {member.bucket_id} | ({candidate_target_bucket.id} if candidate_target_bucket is not None else set())
    )
    lock_buckets(db, bucket_ids, exclusive=True)
    db.expire_all()  # см. докстроку модуля — иначе следующий db.get вернёт кэш

    # Перечитывание — не только состояния, но и существования (спека §2.8):
    # членство могло уйти каскадом МЕЖДУ первым чтением и блокировкой (тот же
    # приём, что в `move_members`).
    member = db.execute(
        sa.select(ContextMember).where(ContextMember.position_item_id == position_item_id).with_for_update()
    ).scalar_one_or_none()
    if member is None:
        raise ContextOperationError(
            REFUSE_INVALID_MEMBERSHIP,
            f"позиция {position_item_id} без членства (после блокировки)",
            position_item_ids=[position_item_id],
        )
    if member.bucket_id not in bucket_ids:
        # Корзина членства сменилась МЕЖДУ незапертым чтением выше и локом на
        # само членство (гонка с `move_members`/слиянием/разделением на ЭТОМ
        # членстве, взявшими корзину раньше нас по общему порядку) — набор,
        # который мы держим под локом, больше не её место, маршрутизировать
        # в него нельзя.
        raise ContextOperationError(
            REFUSE_CATEGORY_CHANGED,
            f"корзина членства {position_item_id} изменилась между чтением и "
            "блокировкой (гонка на переносе того же членства)",
            position_item_id=position_item_id,
            new_proposal=transfer_proposal(db, position_item_id=position_item_id),
        )
    if member.membership_state != MembershipState.STALE.value:
        raise ContextOperationError(
            REFUSE_NOT_STALE,
            f"членство {position_item_id} не устарело (CURRENT) — переносить нечего (после блокировки)",
            position_item_id=position_item_id,
        )

    chapters = chapter_context(db, position_item_id)
    fresh_effective = chapters.category_id
    if fresh_effective != expected_category_id:
        raise ContextOperationError(
            REFUSE_CATEGORY_CHANGED,
            f"эффективная статья позиции {position_item_id} изменилась: ожидалась "
            f"{expected_category_id!r}, сейчас {fresh_effective!r}",
            position_item_id=position_item_id,
            new_proposal=transfer_proposal(db, position_item_id=position_item_id),
        )

    # Статья совпала с ожидаемой, но КОРЗИНА цели могла родиться МЕЖДУ
    # первым чтением (candidate_target_bucket) и локом —
    # `bucket_ids` заперт ДО этого рождения и её не держит. Перечитываем
    # корзину цели СВЕЖЕЙ (уже под локом на всём, что мы держим) и отказываем,
    # если она не входит в заблокированный набор: держать её мы не можем, а
    # маршрутизировать в незапертую корзину — races с любой операцией,
    # берущей на неё `FOR UPDATE` (разделить/слить/архивировать, задача 6).
    fresh_target_bucket = _lookup_bucket(
        db, catalog_position_id=catalog_position_id, work_category_id=fresh_effective
    )
    if fresh_target_bucket is not None and fresh_target_bucket.id not in bucket_ids:
        raise ContextOperationError(
            REFUSE_CATEGORY_CHANGED,
            f"целевая корзина позиции {position_item_id} изменилась между чтением и "
            "блокировкой (гонка на создании корзины)",
            position_item_id=position_item_id,
            new_proposal=transfer_proposal(db, position_item_id=position_item_id),
        )

    catalog_position = db.get(CatalogPosition, catalog_position_id)
    new_bucket, _bucket_created, _ctx_created = context_routing_module._get_or_create_bucket_with_default_context(
        db,
        catalog_position=catalog_position,
        work_category_id=fresh_effective,
        chain=chapters.chain,
        origin="stale_accepted",
    )
    target_context, matched_rule_id, routed_by_value = _resolve_target_context(
        db, bucket=new_bucket, chapters=chapters
    )

    from_context_id = member.context_id
    member.context_id = target_context.id
    member.bucket_id = new_bucket.id
    member.routed_by = routed_by_value
    member.routing_rule_id = matched_rule_id
    member.membership_state = MembershipState.CURRENT.value
    db.flush()

    record_event(
        db,
        event_type="members_moved",
        context_id=target_context.id,
        actor_id=actor_id,
        payload={"from_context_id": from_context_id, "moved_members": 1, "reason": "stale_accepted"},
    )

    return 1
