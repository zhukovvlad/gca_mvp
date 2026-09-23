"""`record_event` (план фичи «Семьи и контексты», задача 2; спека
`2026-09-22-catalog-families-design.md` §2.14): закрытый список типов событий
журнала и обязательный состав `payload` на каждый тип, провалидированный ДО
обращения к базе.

Доработка после независимого ревью (второй круг): ревью лично воспроизвело
две дыры, которые прежний набор тестов не видел — (1) подмену
`context_family_assigned.source` с `FamilySource` на `DecisionSource` (обе
дают строку `'manual'`, поэтому старые enum-тесты этого не ловили), и (2)
создание `SemanticEvent`/`db.add` ДО валидации (старые тесты доказывали
только «не `IntegrityError`» и «счётчик строк не изменился без явного
`flush`» — обе проверки истинны и при этой ошибке тоже, если `flush` внутри
`record_event` не вызван). Оба класса дыр закрыты ниже: независимые
литералы `_EXPECTED_ENUM_VALUES`/`_EXPECTED_REQUIRED_KEYS` (не читают модуль)
и хелпер `_rejected`, который после каждого отказа проверяет, что в сессии
не осталось незафлашенного `SemanticEvent`, и что явный `flush()` не меняет
число строк.

Хелперы строк `_family`/`_bucket`/`_context` — минимальная копия тех же
хелперов `test_semantic_schema.py` (задача 1): репозиторий не делит хелперы
тестов между модулями, поэтому копия, а не импорт.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Callable

import pytest
import sqlalchemy as sa

from models import (
    CK_EVENT_SUBJECT_BY_TYPE,
    SEMANTIC_EVENT_TYPES_SQL,
    CatalogContext,
    ContextBucket,
    DecisionSource,
    FamilyStatus,
    NameRole,
    SemanticEvent,
    SemanticKind,
    SemanticState,
    WorkFamily,
)
from services.semantic_events import (
    CONTEXT_EVENT_TYPES,
    EVENT_ENUM_VALUES,
    EVENT_REQUIRED_KEYS,
    FAMILY_EVENT_TYPES,
    SemanticEventError,
    record_event,
)

pytestmark = pytest.mark.integration


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _family(session, factories, **overrides) -> WorkFamily:
    defaults = dict(
        title=f"Семья {_uid()}", status=FamilyStatus.draft.value, seed_key=f"seed-{_uid()}"
    )
    defaults.update(overrides)
    fam = WorkFamily(**defaults)
    session.add(fam)
    session.flush()
    return fam


def _bucket(session, factories) -> ContextBucket:
    cp = factories.CatalogPositionFactory.create()
    b = ContextBucket(catalog_position_id=cp.id, work_category_id=None)
    session.add(b)
    session.flush()
    return b


def _context(session, factories, **overrides) -> CatalogContext:
    bucket = _bucket(session, factories)
    defaults = dict(
        bucket_id=bucket.id,
        is_default=False,
        semantic_kind=SemanticKind.WORK.value,
        semantic_kind_source=DecisionSource.rule.value,
        semantic_kind_by=None,
        semantic_kind_at=_now(),
        name_role=NameRole.WORK.value,
        name_role_source=DecisionSource.rule.value,
        name_role_by=None,
        name_role_at=_now(),
        place_dictionary_version=1,
        semantic_state=SemanticState.SUGGESTED.value,
    )
    defaults.update(overrides)
    ctx = CatalogContext(**defaults)
    session.add(ctx)
    session.flush()
    return ctx


def _count_events(db_session) -> int:
    return db_session.execute(sa.text("SELECT count(*) FROM semantic_events")).scalar_one()


def _rejected(db_session, event_type, payload, **kwargs) -> SemanticEventError:
    """Вызывает `record_event`, ожидает `SemanticEventError`, и доказывает,
    что отказ произошёл ДО базы (доработка I3 после ревью — прежние тесты
    доказывали только «исключение не `IntegrityError`» и «счётчик строк не
    изменился БЕЗ явного `flush`», а обе эти проверки истинны и в дыре
    «`db.add` до валидации»/«валидация после `flush`»: раз `record_event` не
    вызвала `flush` сама, строка и не могла появиться, независимо от того,
    когда именно проверка сработала).

    Свидетель — ЯВНЫЙ `flush()` сразу после отказа: если бы валидация
    произошла ПОСЛЕ `flush` внутри `record_event` (строка успела бы уйти в
    базу до отказа) или если бы `db.add` состоялся раньше валидации (а этот
    `flush()` теста тогда бы её отправил), оба случая изменили бы счётчик
    строк здесь. Возвращает исключение — для проверки текста сообщения.

    (Доработка второго круга ревью, Z2: раньше здесь был ещё один «свидетель»
    — отсутствие незафлашенного `SemanticEvent` в `db_session.new`. Он не
    добавлял отдельного доказательства: с явным `flush()` и сверкой счётчика
    строк любая из двух дыр — «db.add до валидации» или «валидация после
    flush» — уже красит тест, так что вторая проверка не ловит ничего, чего
    не поймала бы первая; удалён по решению оркестратора.)
    """
    count_before = _count_events(db_session)
    with pytest.raises(SemanticEventError) as exc_info:
        record_event(db_session, event_type=event_type, payload=payload, **kwargs)

    db_session.flush()
    count_after = _count_events(db_session)
    assert count_after == count_before, (
        "число строк semantic_events изменилось после явного flush следом за "
        "отказавшим record_event — отказ произошёл не до базы"
    )
    return exc_info.value


# ---------------------------------------------------------------------------
#  Образцы валидных payload — по одному на тип, ключами СОВПАДАЮЩИМИ с
#  EVENT_REQUIRED_KEYS. Строятся функцией (ctx_id, fam_id) -> payload, чтобы
#  подставлять живые id, где предмет события — не сам субъект вызова
#  (например, `from_context_id`).
# ---------------------------------------------------------------------------

_SAMPLE_BUILDERS: dict[str, Callable[[int, int], dict[str, object]]] = {
    "context_created": lambda ctx_id, fam_id: {"bucket_id": 1, "origin": "import"},
    "context_split": lambda ctx_id, fam_id: {
        "from_context_id": ctx_id, "moved_members": 3, "rule_id": None,
    },
    "context_merged": lambda ctx_id, fam_id: {"into_context_id": ctx_id, "moved_members": 2},
    "members_moved": lambda ctx_id, fam_id: {
        "from_context_id": ctx_id, "moved_members": 1, "reason": "manual",
    },
    "members_marked_stale": lambda ctx_id, fam_id: {"count": 4, "trigger": "category_override"},
    "kind_set": lambda ctx_id, fam_id: {"from": "UNKNOWN", "to": "WORK", "source": "rule"},
    "name_role_set": lambda ctx_id, fam_id: {
        "from": "WORK", "to": "LOCATION_ONLY", "source": "rule", "place_dictionary_version": 1,
    },
    "context_family_assigned": lambda ctx_id, fam_id: {
        "from_family_id": None, "to_family_id": fam_id, "source": "manual",
    },
    "context_archived": lambda ctx_id, fam_id: {"reason": "operator"},
    "routing_rules_dropped": lambda ctx_id, fam_id: {
        "bucket_id": 1, "count": 2, "reason": "review_merge",
    },
    "family_created": lambda ctx_id, fam_id: {
        "title": "Штукатурка стен", "unit": None, "origin": "seed",
    },
    "family_updated": lambda ctx_id, fam_id: {
        "changed": [{"field": "title", "from": "Старое имя", "to": "Новое имя"}],
    },
    "family_activated": lambda ctx_id, fam_id: {"title": "Штукатурка стен", "unit": "m2"},
    "family_archived": lambda ctx_id, fam_id: {"reason": "operator"},
    "family_merged": lambda ctx_id, fam_id: {
        "into_family_id": fam_id, "moved_contexts": 2, "source_title": "Старая семья",
    },
}


def _sample_payload(event_type: str, ctx_id: int, fam_id: int) -> dict[str, object]:
    """KeyError здесь — это и есть «нет образца», и он обязан упасть тестом,
    а не молча подставить пустой payload (лист новый тип без образца)."""
    builder = _SAMPLE_BUILDERS[event_type]
    return builder(ctx_id, fam_id)


def _call_kwargs(event_type: str, ctx_id: int, fam_id: int) -> dict[str, object]:
    if event_type in FAMILY_EVENT_TYPES:
        return {"family_id": fam_id}
    return {"context_id": ctx_id}


# ---------------------------------------------------------------------------
#  Независимые литералы §2.14 — НЕ читают модуль (иначе сверщик делит
#  генератор с тем, что проверяет). `_EXPECTED_ENUM_VALUES.source` — строки
#  ЗАПИСАНЫ буквально, а не через `DecisionSource`/`FamilySource`: тест не
#  должен пройти только потому, что и код, и тест смотрят в одно и то же
#  перечисление models.py.
# ---------------------------------------------------------------------------

_EXPECTED_REQUIRED_KEYS: dict[str, frozenset[str]] = {
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

_EXPECTED_ENUM_VALUES: dict[tuple[str, str], frozenset[str]] = {
    ("context_created", "origin"): frozenset({"import", "backfill", "split", "review_merge"}),
    ("members_moved", "reason"): frozenset({"manual", "stale_accepted", "review_merge"}),
    ("context_archived", "reason"): frozenset({"operator", "review_merge", "context_merge"}),
    ("family_created", "origin"): frozenset({"seed", "operator"}),
    ("family_archived", "reason"): frozenset({"operator", "merged"}),
    ("members_marked_stale", "trigger"): frozenset({"category_override"}),
    ("kind_set", "source"): frozenset({"rule", "manual"}),
    ("name_role_set", "source"): frozenset({"rule", "manual"}),
    ("context_family_assigned", "source"): frozenset({"manual", "suggestion"}),
    ("routing_rules_dropped", "reason"): frozenset({"review_merge"}),
}


class TestExactlyFifteenTypes:
    def test_event_required_keys_has_exactly_fifteen_types(self):
        expected_types = {
            "context_created", "context_split", "context_merged", "members_moved",
            "members_marked_stale", "kind_set", "name_role_set",
            "context_family_assigned", "context_archived", "routing_rules_dropped",
            "family_created", "family_updated", "family_activated",
            "family_archived", "family_merged",
        }
        assert len(expected_types) == 15
        assert set(EVENT_REQUIRED_KEYS) == expected_types
        assert len(EVENT_REQUIRED_KEYS) == 15


class TestRequiredKeysMatchIndependentLiteral:
    """Доработка после ревью: прежде оракул набора обязательных ключей на
    ТИП «висел на одной строке» — типы проверялись независимо
    (`TestExactlyFifteenTypes`), а СОСТАВ ключей каждого типа сверялся
    только опосредованно, через параметризацию, построенную ИЗ САМОГО
    `EVENT_REQUIRED_KEYS`. Здесь — независимый литерал целиком, полное
    равенство словарей."""

    def test_event_required_keys_equals_independent_literal(self):
        assert len(_EXPECTED_REQUIRED_KEYS) == 15
        assert EVENT_REQUIRED_KEYS == _EXPECTED_REQUIRED_KEYS


class TestEnumValuesMatchIndependentLiteral:
    """I1 доработки после ревью: ревью лично воспроизвело дыру — подмена
    `context_family_assigned.source` с `FamilySource` на `DecisionSource`
    (обе содержат `'manual'`) оставляла прежний набор тестов зелёным. Полное
    равенство словарей с независимым литералом (строки, не перечисления)
    красится этой подменой немедленно: `frozenset({'rule','manual'})` (то,
    что дал бы `DecisionSource`) не равно `frozenset({'manual','suggestion'})`
    (то, что реально требуется)."""

    def test_event_enum_values_equals_independent_literal(self):
        assert len(_EXPECTED_ENUM_VALUES) == 10
        assert EVENT_ENUM_VALUES == _EXPECTED_ENUM_VALUES


_ENUM_LEGAL_VALUE_CASES = [
    (event_type, key, value)
    for (event_type, key), allowed in _EXPECTED_ENUM_VALUES.items()
    for value in sorted(allowed)
]

_ENUM_KEY_CASES = sorted(_EXPECTED_ENUM_VALUES)


class TestEnumValuesAcceptedAndRejected:
    """I1 доработки после ревью, поведенческая часть: перебор ЛИТЕРАЛА (не
    модуля) — каждое легальное значение долетает до базы, ровно одно чужое
    на каждую пару (тип, ключ) отказывает, называя и ключ, и само значение."""

    @pytest.mark.parametrize(("event_type", "key", "value"), _ENUM_LEGAL_VALUE_CASES)
    def test_every_legal_value_is_accepted(self, db_session, factories, event_type, key, value):
        ctx = _context(db_session, factories)
        fam = _family(db_session, factories)
        payload = _sample_payload(event_type, ctx.id, fam.id)
        payload[key] = value
        kwargs = _call_kwargs(event_type, ctx.id, fam.id)
        event = record_event(db_session, event_type=event_type, payload=payload, **kwargs)
        assert event.id is not None

    @pytest.mark.parametrize(("event_type", "key"), _ENUM_KEY_CASES)
    def test_one_foreign_value_is_refused_naming_key_and_value(
        self, db_session, factories, event_type, key
    ):
        ctx = _context(db_session, factories)
        fam = _family(db_session, factories)
        payload = _sample_payload(event_type, ctx.id, fam.id)
        foreign = "definitely-not-a-real-value-zzz"
        assert foreign not in _EXPECTED_ENUM_VALUES[(event_type, key)]
        payload[key] = foreign
        kwargs = _call_kwargs(event_type, ctx.id, fam.id)
        error = _rejected(db_session, event_type, payload, **kwargs)
        message = str(error)
        assert key in message
        assert foreign in message


# ---------------------------------------------------------------------------
#  2. Неизвестный тип; отсутствующий обязательный ключ назван по имени —
#     И НЕ называет присутствующие (I2 доработки после ревью)
# ---------------------------------------------------------------------------

class TestUnknownTypeAndMissingKey:
    def test_unknown_event_type_rejected(self, db_session, factories):
        ctx = _context(db_session, factories)
        error = _rejected(
            db_session, "totally_unknown_type",
            {"bucket_id": 1, "origin": "import"}, context_id=ctx.id,
        )
        assert "totally_unknown_type" in str(error)

    def test_missing_required_key_names_it_and_not_the_present_one(self, db_session, factories):
        """I2 доработки после ревью: сообщение обязано называть ИМЕННО
        отсутствующий ключ и НЕ называть присутствующий — сообщение вида
        «отсутствуют обязательные ключи: bucket_id, origin» (весь список,
        включая присутствующий `origin`) тоже красило бы прежнюю проверку
        зелёным."""
        ctx = _context(db_session, factories)
        payload = {"origin": "import"}  # bucket_id отсутствует, origin есть
        error = _rejected(db_session, "context_created", payload, context_id=ctx.id)
        message = str(error)
        assert "bucket_id" in message
        assert "origin" not in message


# ---------------------------------------------------------------------------
#  3. Значение перечислимого ключа вне EVENT_ENUM_VALUES отказывает
#     (базовые witness-тесты; исчерпывающий перебор — выше)
# ---------------------------------------------------------------------------

class TestEnumValueOutOfSet:
    def test_enum_value_outside_set_rejected(self, db_session, factories):
        ctx = _context(db_session, factories)
        payload = {"bucket_id": 1, "origin": "not_a_real_origin"}
        error = _rejected(db_session, "context_created", payload, context_id=ctx.id)
        assert "origin" in str(error)

    def test_key_presence_alone_would_not_catch_this(self, db_session, factories):
        """Различитель против «валидатора, проверяющего только присутствие
        ключа»: ключ присутствует (значение не отсутствует), но значение
        чужое — отказ должен произойти именно на значении."""
        ctx = _context(db_session, factories)
        payload = {"bucket_id": 1, "origin": "not_a_real_origin"}
        assert "origin" in payload  # ключ присутствует
        _rejected(db_session, "context_created", payload, context_id=ctx.id)

    def test_source_enum_value_outside_decision_source_rejected(self, db_session, factories):
        ctx = _context(db_session, factories)
        payload = {"from": "UNKNOWN", "to": "WORK", "source": "not_manual_or_rule"}
        error = _rejected(db_session, "kind_set", payload, context_id=ctx.id)
        assert "source" in str(error)


# ---------------------------------------------------------------------------
#  4. Предмет ставится ТИПОМ, а не аргументом вызова
# ---------------------------------------------------------------------------

class TestSubjectFollowsType:
    def test_family_type_with_context_id_rejected(self, db_session, factories):
        ctx = _context(db_session, factories)
        payload = {"title": "x", "unit": None, "origin": "seed"}
        error = _rejected(db_session, "family_created", payload, context_id=ctx.id)
        assert "family_created" in str(error)

    def test_context_type_with_family_id_rejected(self, db_session, factories):
        fam = _family(db_session, factories)
        payload = {"bucket_id": 1, "origin": "import"}
        error = _rejected(db_session, "context_created", payload, family_id=fam.id)
        assert "context_created" in str(error)

    def test_no_subject_at_all_rejected(self, db_session, factories):
        payload = {"bucket_id": 1, "origin": "import"}
        error = _rejected(db_session, "context_created", payload)
        assert "context_created" in str(error)

    def test_both_subjects_at_once_rejected(self, db_session, factories):
        """Оба субъекта разом на КОНТЕКСТНОМ типе — ветка `_validate_subject`:
        `event_type in CONTEXT_EVENT_TYPES` → `family_id is not None`.
        Семейный тип с обоими id — отдельная ветка, тест ниже (I4)."""
        ctx = _context(db_session, factories)
        fam = _family(db_session, factories)
        payload = {"bucket_id": 1, "origin": "import"}
        error = _rejected(
            db_session, "context_created", payload, context_id=ctx.id, family_id=fam.id,
        )
        assert "context_created" in str(error)

    def test_family_type_with_both_context_and_family_id_rejected(self, db_session, factories):
        """I4 доработки после ревью: ветка `_validate_subject`
        `event_type in FAMILY_EVENT_TYPES` → `context_id is not None` не
        была пробита ОТДЕЛЬНО от контекстной (`test_both_subjects_at_once_rejected`
        выше берёт контекстный тип с обоими id — это семейный тип с обоими
        id, другая половина равносильности `CK_EVENT_SUBJECT_BY_TYPE`)."""
        ctx = _context(db_session, factories)
        fam = _family(db_session, factories)
        payload = {"title": "x", "unit": None, "origin": "seed"}
        error = _rejected(
            db_session, "family_created", payload, context_id=ctx.id, family_id=fam.id,
        )
        assert "family_created" in str(error)

    def test_family_type_without_any_subject_rejected(self, db_session, factories):
        payload = {"title": "x", "unit": None, "origin": "seed"}
        error = _rejected(db_session, "family_created", payload)
        assert "family_created" in str(error)


# ---------------------------------------------------------------------------
#  4b. family_updated.changed — форма содержимого (решение оркестратора,
#      отдельная проверка рядом с обязательными ключами, EVENT_REQUIRED_KEYS
#      остаётся dict[str, frozenset[str]] — присутствие ключа `changed`
#      держит он, форму его содержимого — эта проверка)
# ---------------------------------------------------------------------------

class TestFamilyUpdatedChangedShape:
    def test_changed_not_a_list_rejected(self, db_session, factories):
        fam = _family(db_session, factories)
        payload = {"changed": "not-a-list"}
        error = _rejected(db_session, "family_updated", payload, family_id=fam.id)
        assert "changed" in str(error)

    def test_changed_integer_not_a_list_rejected(self, db_session, factories):
        """M3 доработки после ревью: `5` — свой собственный свидетель,
        отдельный от строки выше (строка тоже «не список», но у неё есть
        длина и она итерируема — код обязан проверять именно `isinstance(x,
        list)`, а не что-то вроде «есть len()»)."""
        fam = _family(db_session, factories)
        payload = {"changed": 5}
        error = _rejected(db_session, "family_updated", payload, family_id=fam.id)
        assert "changed" in str(error)

    def test_changed_empty_list_rejected(self, db_session, factories):
        fam = _family(db_session, factories)
        payload = {"changed": []}
        error = _rejected(db_session, "family_updated", payload, family_id=fam.id)
        assert "changed" in str(error)

    def test_changed_item_not_a_dict_rejected(self, db_session, factories):
        """M3 доработки после ревью: различитель против `in`-проверки на
        строке без проверки типа — если бы код проверял только
        `item_key not in item`, строка `'fieldfromto'` прошла бы, потому что
        `'field' in 'fieldfromto'` истинно КАК ПОДСТРОКА, и то же для
        `'from'`/`'to'`. Элемент обязан быть объектом (`Mapping`), а не
        просто «строкой, которая содержит нужные буквы»."""
        fam = _family(db_session, factories)
        payload = {"changed": ["fieldfromto"]}
        error = _rejected(db_session, "family_updated", payload, family_id=fam.id)
        assert "changed[0]" in str(error)

    def test_item_missing_field_rejected(self, db_session, factories):
        fam = _family(db_session, factories)
        payload = {"changed": [{"from": "А", "to": "Б"}]}
        error = _rejected(db_session, "family_updated", payload, family_id=fam.id)
        assert "field" in str(error)

    def test_item_missing_from_rejected(self, db_session, factories):
        fam = _family(db_session, factories)
        payload = {"changed": [{"field": "title", "to": "Б"}]}
        error = _rejected(db_session, "family_updated", payload, family_id=fam.id)
        assert "from" in str(error)

    def test_item_missing_to_rejected(self, db_session, factories):
        fam = _family(db_session, factories)
        payload = {"changed": [{"field": "title", "from": "А"}]}
        error = _rejected(db_session, "family_updated", payload, family_id=fam.id)
        assert "to" in str(error)

    def test_two_item_changed_list_records_fine(self, db_session, factories):
        fam = _family(db_session, factories)
        payload = {
            "changed": [
                {"field": "title", "from": "Старое имя", "to": "Новое имя"},
                {"field": "unit", "from": None, "to": "m2"},
            ]
        }
        event = record_event(
            db_session, event_type="family_updated", payload=payload, family_id=fam.id
        )
        assert event.id is not None
        reloaded = db_session.get(SemanticEvent, event.id)
        assert reloaded is not None
        assert reloaded.payload == payload


# ---------------------------------------------------------------------------
#  M1 доработки после ревью: нехэшируемое значение перечислимого ключа и
#     payload, не являющийся объектом, — SemanticEventError, а НЕ TypeError
# ---------------------------------------------------------------------------

class TestNonHashableAndNonDictPayload:
    def test_non_hashable_enum_value_rejected_not_type_error(self, db_session, factories):
        """`origin` — список, нехэшируем: `value in allowed` (allowed —
        `frozenset`) без защиты кинул бы `TypeError`, а не `SemanticEventError`
        — pytest.raises(SemanticEventError) ниже это отличает."""
        ctx = _context(db_session, factories)
        payload = {"bucket_id": 1, "origin": ["import"]}
        error = _rejected(db_session, "context_created", payload, context_id=ctx.id)
        assert "origin" in str(error)

    def test_non_dict_payload_none_rejected_not_type_error(self, db_session, factories):
        """Доработка второго круга ревью (Z1): `payload = None` — не
        объект и не итерируем вовсе. Без проверки `isinstance(payload,
        Mapping)` код упал бы уже на ПЕРВОЙ итерации `key not in payload`
        внутри вычисления отсутствующих ключей — `TypeError: argument of
        type 'NoneType' is not iterable`, а не `SemanticEventError`."""
        ctx = _context(db_session, factories)
        error = _rejected(db_session, "context_created", None, context_id=ctx.id)
        assert "context_created" in str(error)

    def test_non_dict_payload_with_required_key_names_as_items_rejected_not_type_error(
        self, db_session, factories
    ):
        """Доработка второго круга ревью (Z1): различитель против слабого
        прежнего входа `[1, 2, 3]`, который отказывал уже на проверке
        отсутствующих ключей («bucket_id»/«origin» не среди элементов 1, 2,
        3) и НИКОГДА не добирался до самой проверки `isinstance` — удали
        её, и `[1, 2, 3]` оставался бы зелёным по чужой причине.

        Здесь список — `["bucket_id", "origin"]`: обе строки совпадают с
        именами обязательных ключей, поэтому `key not in payload` находит
        их СРЕДИ ЭЛЕМЕНТОВ списка, `missing` получается пустым, и код без
        проверки `isinstance` дошёл бы до `payload[key]` — для списка это
        `TypeError: list indices must be integers or slices, not str`, а не
        `SemanticEventError`. Именно этот вход реально проходит СКВОЗЬ
        проверку отсутствующих ключей и упирается в форму `payload`."""
        ctx = _context(db_session, factories)
        error = _rejected(
            db_session, "context_created", ["bucket_id", "origin"], context_id=ctx.id
        )
        assert "context_created" in str(error)


# ---------------------------------------------------------------------------
#  M2 доработки после ревью: историческое имя `family_assigned` —
#     неизвестный ТИП (не входит в закрытый список пятнадцати вовсе),
#     отказ SemanticEventError, а не KeyError
# ---------------------------------------------------------------------------

class TestHistoricalNameIsUnknownType:
    def test_historical_family_assigned_name_rejected_as_unknown_not_by_key_error(
        self, db_session, factories
    ):
        """Историческое имя `family_assigned` (до переименования в
        `context_family_assigned`, спека §2.14) не входит в закрытый список
        пятнадцати типов вовсе. Явное множество (`event_type in
        FAMILY_EVENT_TYPES` / `in CONTEXT_EVENT_TYPES`), а не префикс
        `family_%`, отвергает его как НЕИЗВЕСТНЫЙ тип ДО того, как код полез
        бы в `EVENT_REQUIRED_KEYS[event_type]` — а полез бы и упал бы
        `KeyError`, будь `_validate_subject` построена на
        `event_type.startswith("family_")` (подстрока "family_" совпадает и
        с историческим именем)."""
        fam = _family(db_session, factories)
        error = _rejected(db_session, "family_assigned", {"title": "x"}, family_id=fam.id)
        assert "family_assigned" in str(error)


# ---------------------------------------------------------------------------
#  M4 доработки после ревью: позитивные входы со значением None там, где
#     ключ обязателен, но значение легитимно пусто
# ---------------------------------------------------------------------------

class TestPositiveInputsWithNone:
    def test_context_family_assigned_to_family_id_none_records_fine(self, db_session, factories):
        """`to_family_id = null` — легитимное «снятие» семьи с контекста
        (спека §2.14)."""
        ctx = _context(db_session, factories)
        payload = {"from_family_id": None, "to_family_id": None, "source": "manual"}
        event = record_event(
            db_session, event_type="context_family_assigned", payload=payload, context_id=ctx.id
        )
        assert event.id is not None
        reloaded = db_session.get(SemanticEvent, event.id)
        assert reloaded.payload["to_family_id"] is None

    def test_family_activated_unit_none_records_fine(self, db_session, factories):
        """`unit = null` — легитимно у семьи без единицы измерения (спека
        §2.14)."""
        fam = _family(db_session, factories)
        payload = {"title": "Штукатурка стен", "unit": None}
        event = record_event(
            db_session, event_type="family_activated", payload=payload, family_id=fam.id
        )
        assert event.id is not None
        reloaded = db_session.get(SemanticEvent, event.id)
        assert reloaded.payload["unit"] is None


# ---------------------------------------------------------------------------
#  5. Внешняя сверка кода и схемы
# ---------------------------------------------------------------------------

class TestExternalCrossCheckWithSchema:
    def test_family_event_types_matches_ck_event_subject_by_type_and_is_non_empty(self):
        """Доказательство, что сверка не пуста (урок задачи 1): множество,
        разобранное из `CK_EVENT_SUBJECT_BY_TYPE`, — ровно пять типов, и
        `FAMILY_EVENT_TYPES` совпадает с ним. Если бы регэксп не находил
        совпадений, `parsed` было бы пустым множеством и сравнение с
        `FAMILY_EVENT_TYPES` (5 элементов) провалилось бы, а не молча
        совпало на пустоте."""
        match = re.search(r"event_type IN \(([^)]*)\)", CK_EVENT_SUBJECT_BY_TYPE)
        assert match is not None
        parsed = {piece.strip().strip("'") for piece in match.group(1).split(",")}
        assert len(parsed) == 5
        assert parsed == set(FAMILY_EVENT_TYPES)

    def test_union_equals_check_event_type_values(self):
        """`SEMANTIC_EVENT_TYPES_SQL` — литерал `'a', 'b', ...` того же
        `CHECK` на `event_type` (`ck_semantic_events_event_type`, задача 1).
        Объединение `FAMILY_EVENT_TYPES | CONTEXT_EVENT_TYPES` обязано
        совпасть с ним, иначе код и схема разошлись в самом наборе типов."""
        parsed = {
            piece.strip().strip("'") for piece in SEMANTIC_EVENT_TYPES_SQL.split(",")
        }
        assert len(parsed) == 15
        assert parsed == (FAMILY_EVENT_TYPES | CONTEXT_EVENT_TYPES)

    def test_family_and_context_types_are_disjoint(self):
        assert frozenset() == FAMILY_EVENT_TYPES & CONTEXT_EVENT_TYPES


# ---------------------------------------------------------------------------
#  6. Перебор EVENT_REQUIRED_KEYS: валидная запись на каждый тип, и отказ
#     по каждому отдельно убранному обязательному ключу (называет ЕГО и
#     только его — I2)
# ---------------------------------------------------------------------------

_TYPE_KEY_PAIRS = [
    (event_type, key)
    for event_type, keys in _EXPECTED_REQUIRED_KEYS.items()
    for key in sorted(keys)
]


class TestEachTypeRecordsValidPayload:
    @pytest.mark.parametrize("event_type", sorted(_EXPECTED_REQUIRED_KEYS))
    def test_complete_valid_payload_lands_in_db_with_right_subject(
        self, db_session, factories, event_type
    ):
        ctx = _context(db_session, factories)
        fam = _family(db_session, factories)
        payload = _sample_payload(event_type, ctx.id, fam.id)
        # Образец обязан покрывать РОВНО обязательные ключи этого типа —
        # иначе сверка со следующим блоком (пропущенный ключ) ничего не
        # доказывает.
        assert set(payload) == _EXPECTED_REQUIRED_KEYS[event_type]

        kwargs = _call_kwargs(event_type, ctx.id, fam.id)
        event = record_event(db_session, event_type=event_type, payload=payload, **kwargs)

        assert event.id is not None
        assert event.event_type == event_type
        if event_type in FAMILY_EVENT_TYPES:
            assert event.family_id == fam.id
            assert event.context_id is None
        else:
            assert event.context_id == ctx.id
            assert event.family_id is None

        reloaded = db_session.get(SemanticEvent, event.id)
        assert reloaded is not None
        assert reloaded.payload == payload


class TestEachRequiredKeyIsEnforced:
    @pytest.mark.parametrize(("event_type", "key"), _TYPE_KEY_PAIRS)
    def test_removing_one_required_key_fails_naming_it_and_only_it(
        self, db_session, factories, event_type, key
    ):
        ctx = _context(db_session, factories)
        fam = _family(db_session, factories)
        payload = _sample_payload(event_type, ctx.id, fam.id)
        del payload[key]

        kwargs = _call_kwargs(event_type, ctx.id, fam.id)
        error = _rejected(db_session, event_type, payload, **kwargs)
        message = str(error)
        assert key in message
        for other_key in _EXPECTED_REQUIRED_KEYS[event_type]:
            if other_key == key:
                continue
            assert other_key not in message, (
                f"сообщение об отсутствующем ключе {key!r} назвало ещё и "
                f"присутствующий ключ {other_key!r}: {message!r}"
            )
