"""Жизненный цикл семей работ: seed, создание, правка, активация, назначение
контексту, слияние и архивирование (план
`docs/superpowers/plans/2026-09-22-catalog-families.md`, задачи 7 и 8; спека
`docs/superpowers/specs/2026-09-22-catalog-families-design.md` §1.14, §2.3,
§2.7, §2.8, §2.14).

Seed-файл `backend/seeds/work_families_initial.json` собран заново из
`tasks/catalog-pilot-2026-09-18/razmetka-101.xlsx` скриптом
`tasks/catalog-families-work/build_work_families_seed.py` (файл вне гита,
скрипт — тоже).

42/6/36 — НЕЗАВИСИМЫЕ литералы теста, не `len()` того же файла в обе
стороны (план, задача 7, «Проверка»).

Классы `TestAssignFamily`, `TestSetUnit`, `TestArchiveFamily`,
`TestMergeFamilies`, `TestFamilyLockCompilation`, `TestFamilyRereadAfterLock`
и три класса гонок в конце файла — задача 8. Помощники маршрутизации
(`_proposal`, `_position`, `_routed_context`) — ЛОКАЛЬНАЯ копия набора
`test_context_operations.py` (докстрока того файла: наборы помощников тестов
друг у друга не импортируют)."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import threading
import time
import uuid
from pathlib import Path
from unittest import mock

import pytest
import sqlalchemy as sa
from click.testing import CliRunner
from sqlalchemy import event

import cli
import services.work_families as work_families_module
from models import (
    CatalogContext,
    CatalogKind,
    FamilySource,
    FamilyStatus,
    SemanticEvent,
    SemanticKind,
    SemanticState,
    WorkFamily,
)
from services.context_routing import route_position
from services.unit_resolution import UnitResolver
from services.work_families import (
    REFUSE_ACTIVATE_NOT_DRAFT,
    REFUSE_ACTIVATE_WITHOUT_DEFINITION,
    REFUSE_ARCHIVE_WITH_LINKS,
    REFUSE_BLANK_TITLE,
    REFUSE_CLEAR_DEFINITION_ACTIVE,
    REFUSE_CONTEXT_ARCHIVED,
    REFUSE_CONTEXT_NOT_FOUND,
    REFUSE_DUPLICATE_ACTIVE_FAMILY,
    REFUSE_FAMILY_NOT_ACTIVE,
    REFUSE_FAMILY_NOT_FOUND,
    REFUSE_MERGE_INACTIVE,
    REFUSE_MERGE_SAME_FAMILY,
    REFUSE_MERGE_UNIT_MISMATCH,
    REFUSE_UNIT_CHANGE_WITH_LINKS,
    REFUSE_UNIT_MISMATCH,
    REFUSE_UNKNOWN_UNIT,
    REFUSE_UPDATE_ARCHIVED,
    SEED_PATH,
    SeedReport,
    WorkFamilyError,
    activate_family,
    archive_family,
    assign_family,
    create_family,
    load_seed,
    merge_families,
    set_unit,
    update_family,
)
from tests.integration.test_schema_constraints import rejected

pytestmark = pytest.mark.integration

_JOIN_TIMEOUT = 30.0

# Независимые литералы (не len() файла): 42 семьи эталона, у 6 заполнено
# определение, у 36 — пусто (спека §1.14).
EXPECTED_TOTAL = 42
EXPECTED_WITH_DEFINITION = 6
EXPECTED_WITHOUT_DEFINITION = 36


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _seeded_families(db):
    return (
        db.execute(sa.select(WorkFamily).where(WorkFamily.seed_key.isnot(None)))
        .scalars()
        .all()
    )


def _events(db, family_id, event_type=None):
    stmt = sa.select(SemanticEvent).where(SemanticEvent.family_id == family_id)
    if event_type is not None:
        stmt = stmt.where(SemanticEvent.event_type == event_type)
    return db.execute(stmt).scalars().all()


def test_module_surface_names_from_plan_task_7():
    """Имена, заводимые задачей 7 (план, раздел «Имена»), существуют и несут
    ожидаемые роли."""
    assert isinstance(SEED_PATH, Path)
    assert SEED_PATH.name == "work_families_initial.json"
    assert isinstance(REFUSE_ACTIVATE_WITHOUT_DEFINITION, str)
    report = SeedReport(created=1, skipped_existing=2, with_definition=3)
    assert (report.created, report.skipped_existing, report.with_definition) == (1, 2, 3)


# ---------------------------------------------------------------------------
#  Контракт seed-файла: белый список, обе стороны (тот же приём, что у
#  фикстуры задачи 3, test_semantic_rules.py) + негативный вход на саму
#  проверку.
# ---------------------------------------------------------------------------

REQUIRED_KEYS_SEED = frozenset({"seed_key", "title", "unit", "definition"})
ALLOWED_KEYS_SEED = REQUIRED_KEYS_SEED  # все четыре обязательны, опциональных нет


def _seed_record_is_valid(record: dict) -> bool:
    keys = set(record.keys())
    return REQUIRED_KEYS_SEED.issubset(keys) and keys.issubset(ALLOWED_KEYS_SEED)


SEED_RECORDS = json.loads(SEED_PATH.read_bytes().decode("utf-8"))


def test_seed_file_has_42_records():
    assert len(SEED_RECORDS) == EXPECTED_TOTAL


@pytest.mark.parametrize(
    "record", SEED_RECORDS, ids=[r["seed_key"] for r in SEED_RECORDS]
)
def test_seed_record_matches_whitelist_both_directions(record):
    assert _seed_record_is_valid(record), (
        f"запись {record!r} не проходит контракт белого списка "
        f"(допустимые {sorted(ALLOWED_KEYS_SEED)})"
    )


def test_seed_whitelist_rejects_unknown_key():
    """Негативный вход на саму проверку: посторонний ключ обязан её покрасить."""
    mutated = {**SEED_RECORDS[0], "project_code": "X-1"}
    assert not _seed_record_is_valid(mutated)


def test_seed_whitelist_rejects_missing_required_key():
    mutated = dict(SEED_RECORDS[0])
    del mutated["unit"]
    assert not _seed_record_is_valid(mutated)


def test_seed_units_each_resolve_via_unit_resolver(db_session):
    """Единица КАЖДОЙ из 42 записей — каноническое имя, разрешаемое
    `UnitResolver` на справочнике, который в тестовой базе заводят миграции
    (план, задача 7, «Имена»; спека §2.7)."""
    resolver = UnitResolver(db_session)
    assert len(SEED_RECORDS) == EXPECTED_TOTAL
    for record in SEED_RECORDS:
        resolved = resolver.resolve(record["unit"])
        assert resolved.status == "known", (
            f"{record['seed_key']}: единица {record['unit']!r} не резолвится"
        )
        assert resolved.unit_id is not None


# ---------------------------------------------------------------------------
#  load_seed: 42 черновика, 6/36 по определению, идемпотентность, переезд.
# ---------------------------------------------------------------------------


def test_load_seed_creates_42_drafts(db_session):
    report = load_seed(db_session)
    assert report.created == EXPECTED_TOTAL
    assert report.skipped_existing == 0
    assert report.with_definition == EXPECTED_WITH_DEFINITION

    families = _seeded_families(db_session)
    assert len(families) == EXPECTED_TOTAL
    with_def = 0
    without_def = 0
    for fam in families:
        assert fam.status == FamilyStatus.draft.value
        assert fam.created_by is None
        assert fam.seed_key is not None
        assert fam.unit_id is not None
        if fam.definition is not None:
            with_def += 1
        else:
            without_def += 1
    assert with_def == EXPECTED_WITH_DEFINITION
    assert without_def == EXPECTED_WITHOUT_DEFINITION


def test_load_seed_writes_family_created_with_seed_origin(db_session):
    load_seed(db_session)
    fam = db_session.execute(
        sa.select(WorkFamily).where(WorkFamily.seed_key == "fam-01")
    ).scalar_one()
    events = _events(db_session, fam.id, "family_created")
    assert len(events) == 1
    assert events[0].payload["origin"] == "seed"
    assert events[0].actor_id is None
    assert events[0].payload["unit"] == fam.unit.code


def test_load_seed_repeat_run_is_idempotent_and_preserves_user_edit(db_session):
    first = load_seed(db_session)
    assert first.created == EXPECTED_TOTAL
    assert first.skipped_existing == 0

    fam = db_session.execute(
        sa.select(WorkFamily).where(WorkFamily.seed_key == "fam-07")
    ).scalar_one()
    assert fam.definition is None  # fam-07 в файле без определения
    fam.definition = "Определение, дописанное пользователем между запусками"
    db_session.flush()

    second = load_seed(db_session)
    assert second.created == 0
    assert second.skipped_existing == EXPECTED_TOTAL
    # Утверждение о ФАЙЛЕ, не о базе — одинаково на первом и повторном запуске.
    assert second.with_definition == EXPECTED_WITH_DEFINITION

    total = db_session.execute(
        sa.select(sa.func.count()).select_from(WorkFamily).where(WorkFamily.seed_key.isnot(None))
    ).scalar_one()
    assert total == EXPECTED_TOTAL  # дублей не появилось

    db_session.refresh(fam)
    assert fam.definition == "Определение, дописанное пользователем между запусками"


def test_load_seed_rename_does_not_duplicate_and_name_unit_search_would_have(db_session):
    """Переименование семьи, повторный seed, второго
    черновика нет.

    Что держит каждая часть:

    * `report.created == 0` и `total == EXPECTED_TOTAL` — сам факт
      «дубля нет» — это и есть утверждение «поиск по паре „имя × единица“
      на этом входе обязан покраснеть» (план, задача 7): мутация кода
      `load_seed`, ищущая совпадение по имени вместо `seed_key`, красит
      именно ИХ — `UniqueViolation uq_work_families_seed_key` (переименованная
      запись невидима такому поиску, `load_seed` пытается вставить второй
      `fam-01`) либо, для мутации «повтор перезаписывает `title` из файла»,
      `assert 1 == 0` на `report.created`.
    * `found_by_name_unit == 0` — УЖЕ ПОСЛЕ доказанного «дубля нет» — стережёт
      более узкий и отдельный факт: «seed не откатывает переименование
      обратно на старое имя» (запись по-прежнему называется НОВЫМ именем).
      Он НЕ доказывает сам по себе, что поиск по имени×единице завёл бы
      дубль, — это доказывают мутации выше, через настоящую подмену кода
      поиска, а не этот запрос."""
    load_seed(db_session)
    old_title = "Геотекстиль"
    fam = db_session.execute(
        sa.select(WorkFamily).where(WorkFamily.seed_key == "fam-01")
    ).scalar_one()
    assert fam.title == old_title
    unit_id = fam.unit_id
    fam.title = "Геотекстиль (переименовано оператором)"
    db_session.flush()

    report = load_seed(db_session)
    assert report.created == 0
    assert report.skipped_existing == EXPECTED_TOTAL

    total = db_session.execute(
        sa.select(sa.func.count()).select_from(WorkFamily).where(WorkFamily.seed_key.isnot(None))
    ).scalar_one()
    assert total == EXPECTED_TOTAL

    # Отдельный, более узкий факт (см. докстроку выше): запись после
    # load_seed по-прежнему называется НОВЫМ именем, а не откачена на старое.
    found_by_name_unit = db_session.execute(
        sa.select(sa.func.count())
        .select_from(WorkFamily)
        .where(WorkFamily.title == old_title, WorkFamily.unit_id == unit_id)
    ).scalar_one()
    assert found_by_name_unit == 0


def _write_seed_file(tmp_path, records) -> Path:
    path = tmp_path / "custom_seed.json"
    path.write_bytes(json.dumps(records, ensure_ascii=False).encode("utf-8"))
    return path


def test_load_seed_refuses_unresolvable_unit_and_writes_nothing(db_session, tmp_path):
    """`load_seed` не молчит на
    неизвестной единице — как и `create_family`, отказывает
    `REFUSE_UNKNOWN_UNIT`, называя `seed_key` и единицу. Проверка ВСЕХ
    записей идёт ДО первой вставки: ни «плохая», ни предшествующая ей
    «хорошая» запись не должны попасть в базу."""
    records = [
        {"seed_key": "fam-round2-good", "title": "Хорошая единица", "unit": "M2", "definition": None},
        {
            "seed_key": "fam-round2-bad", "title": "Плохая единица",
            "unit": "ne-nastoyashaya-edinica-xyz", "definition": None,
        },
    ]
    path = _write_seed_file(tmp_path, records)

    with pytest.raises(WorkFamilyError) as exc:
        load_seed(db_session, path=path)
    assert exc.value.code == REFUSE_UNKNOWN_UNIT
    assert exc.value.seed_key == "fam-round2-bad"
    assert exc.value.unit_name == "ne-nastoyashaya-edinica-xyz"

    total = db_session.execute(
        sa.select(sa.func.count())
        .select_from(WorkFamily)
        .where(WorkFamily.seed_key.in_(["fam-round2-good", "fam-round2-bad"]))
    ).scalar_one()
    assert total == 0, "load_seed записала что-то, хотя обязана была отказать до первой вставки"


def test_load_seed_normalizes_blank_definition_to_null(db_session, tmp_path):
    """`load_seed` нормализует
    пустое/пробельное `definition` файла в `NULL` — тем же правилом, что
    `create_family`/`update_family` (`_normalize_definition`), а не хранит
    его строкой из пробелов."""
    records = [
        {
            "seed_key": "fam-round2-blank-def", "title": "Пробельное определение",
            "unit": "PCS", "definition": "   ",
        },
    ]
    path = _write_seed_file(tmp_path, records)

    report = load_seed(db_session, path=path)
    assert report.created == 1

    fam = db_session.execute(
        sa.select(WorkFamily).where(WorkFamily.seed_key == "fam-round2-blank-def")
    ).scalar_one()
    assert fam.definition is None


# ---------------------------------------------------------------------------
#  create_family: ручное создание.
# ---------------------------------------------------------------------------


def test_create_family_manual_sets_operator_origin_and_draft(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title=f"Ручная семья {_uid()}", unit_name="PCS", definition=None,
        actor_id=user.id,
    )
    assert fam.seed_key is None
    assert fam.created_by == user.id
    assert fam.status == FamilyStatus.draft.value
    events = _events(db_session, fam.id, "family_created")
    assert len(events) == 1
    assert events[0].payload["origin"] == "operator"
    assert events[0].payload["unit"] == "PCS"


def test_create_family_without_unit_stores_null_unit_id(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title=f"Без единицы {_uid()}", unit_name=None, definition=None,
        actor_id=user.id,
    )
    assert fam.unit_id is None


def test_create_family_unknown_unit_refuses(db_session, factories):
    user = factories.UserFactory.create()
    with pytest.raises(WorkFamilyError) as exc:
        create_family(
            db_session, title=f"Плохая единица {_uid()}",
            unit_name="совсем-не-единица-xyz", definition=None, actor_id=user.id,
        )
    assert exc.value.code == REFUSE_UNKNOWN_UNIT


def test_create_family_blank_definition_normalizes_to_null(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title=f"Пробелы {_uid()}", unit_name=None, definition="   ",
        actor_id=user.id,
    )
    assert fam.definition is None


@pytest.mark.parametrize("title", ["", "   ", "\t\n"])
def test_create_family_blank_title_refuses_as_a_domain_error(db_session, factories, title):
    """Пустое/пробельное имя — доменный отказ (`REFUSE_BLANK_TITLE`), а не
    `IntegrityError` от `ck_work_families_title_not_blank`, дошедший до
    маршрута непойманным 500."""
    user = factories.UserFactory.create()
    with pytest.raises(WorkFamilyError) as exc:
        create_family(
            db_session, title=title, unit_name=None, definition=None, actor_id=user.id,
        )
    assert exc.value.code == REFUSE_BLANK_TITLE


# ---------------------------------------------------------------------------
#  update_family: правка, `changed`, запрет на archived.
# ---------------------------------------------------------------------------


def test_update_family_rename_records_single_changed_field(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Старое имя", unit_name=None, definition=None, actor_id=user.id,
    )
    updated = update_family(
        db_session, family_id=fam.id, title="Новое имя", actor_id=user.id,
    )
    assert updated.title == "Новое имя"
    events = _events(db_session, fam.id, "family_updated")
    assert len(events) == 1
    assert events[0].payload["changed"] == [
        {"field": "title", "from": "Старое имя", "to": "Новое имя"}
    ]


def test_update_family_two_fields_at_once_records_both(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="A", unit_name=None, definition="Def A", actor_id=user.id,
    )
    update_family(
        db_session, family_id=fam.id, title="B", definition="Def B", actor_id=user.id,
    )
    events = _events(db_session, fam.id, "family_updated")
    assert len(events) == 1
    changed = events[0].payload["changed"]
    assert len(changed) == 2  # число различимо от «1» — оба поля учтены разом
    fields = {item["field"] for item in changed}
    assert fields == {"title", "definition"}


def test_update_family_no_real_change_writes_no_event(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Неизменная", unit_name=None, definition="Тоже неизменное",
        actor_id=user.id,
    )
    before = len(_events(db_session, fam.id, "family_updated"))
    update_family(
        db_session, family_id=fam.id, title="Неизменная", definition="Тоже неизменное",
        actor_id=user.id,
    )
    after = len(_events(db_session, fam.id, "family_updated"))
    assert after == before == 0  # пустой аудит запрещён спекой §2.14 — событие не пишется


def test_update_family_clearing_definition_to_blank_is_recorded_as_null(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="С определением", unit_name=None, definition="Было",
        actor_id=user.id,
    )
    update_family(
        db_session, family_id=fam.id, title=None, definition="   ", actor_id=user.id,
    )
    db_session.refresh(fam)
    assert fam.definition is None
    events = _events(db_session, fam.id, "family_updated")
    assert events[-1].payload["changed"] == [
        {"field": "definition", "from": "Было", "to": None}
    ]


@pytest.mark.parametrize("title", ["", "   ", "\t\n"])
def test_update_family_blank_title_refuses_and_leaves_title_untouched(
    db_session, factories, title
):
    """Пустое/пробельное имя на правке — доменный отказ
    (`REFUSE_BLANK_TITLE`), не `IntegrityError`; `None` остаётся законным «не
    трогать» (проверено смежными тестами выше) — отказывает именно РЕАЛЬНО
    переданная пустая строка."""
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Имя останется", unit_name=None, definition=None, actor_id=user.id,
    )
    with pytest.raises(WorkFamilyError) as exc:
        update_family(
            db_session, family_id=fam.id, title=title, actor_id=user.id,
        )
    assert exc.value.code == REFUSE_BLANK_TITLE
    db_session.refresh(fam)
    assert fam.title == "Имя останется"


def test_update_family_allowed_when_active(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Актив", unit_name=None, definition="Определение",
        actor_id=user.id,
    )
    activate_family(db_session, family_id=fam.id, actor_id=user.id)
    updated = update_family(
        db_session, family_id=fam.id, title="Актив (правка)",
        actor_id=user.id,
    )
    assert updated.title == "Актив (правка)"


def test_update_family_clearing_definition_of_active_family_is_a_domain_refusal(
    db_session, factories
):
    """`definition=None` ПЕРЕДАННЫЙ ЯВНО — не «не трогать» (то у этой
    функции значение по умолчанию, см. `test_update_family_allowed_when_active`
    выше, где `definition` не передан вовсе), а «снять определение»; у
    `active` семьи это нарушило бы `CK_FAMILY_ACTIVE_NEEDS_DEFINITION`."""
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Актив с определением", unit_name=None, definition="Определение",
        actor_id=user.id,
    )
    activate_family(db_session, family_id=fam.id, actor_id=user.id)
    with pytest.raises(WorkFamilyError) as exc:
        update_family(
            db_session, family_id=fam.id, title=None, definition=None, actor_id=user.id,
        )
    assert exc.value.code == REFUSE_CLEAR_DEFINITION_ACTIVE
    db_session.refresh(fam)
    assert fam.definition == "Определение"


def test_update_family_archived_refuses(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Архивная", unit_name=None, definition="Определение",
        actor_id=user.id,
    )
    fam.status = FamilyStatus.archived.value
    db_session.flush()
    with pytest.raises(WorkFamilyError) as exc:
        update_family(
            db_session, family_id=fam.id, title="Новое имя",
            actor_id=user.id,
        )
    assert exc.value.code == REFUSE_UPDATE_ARCHIVED


def test_update_family_not_found_refuses(db_session, factories):
    user = factories.UserFactory.create()
    with pytest.raises(WorkFamilyError) as exc:
        update_family(
            db_session, family_id=999_999_999, title="x",
            actor_id=user.id,
        )
    assert exc.value.code == REFUSE_FAMILY_NOT_FOUND


# ---------------------------------------------------------------------------
#  activate_family: без определения — отказ (до базы), с определением — успех.
# ---------------------------------------------------------------------------


def test_activate_without_definition_refuses_before_any_flush(db_session, factories):
    """`REFUSE_ACTIVATE_WITHOUT_DEFINITION` проверяется в коде ДО базы: отказ
    обязан произойти БЕЗ единого `flush` (план, задача 7;
    независимый счётчик, никаких sleep)."""
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Без определения", unit_name=None, definition=None,
        actor_id=user.id,
    )

    flush_count = {"n": 0}

    def _count(*_args, **_kwargs):
        flush_count["n"] += 1

    event.listen(db_session, "after_flush", _count)
    try:
        with pytest.raises(WorkFamilyError) as exc:
            activate_family(db_session, family_id=fam.id, actor_id=user.id)
    finally:
        event.remove(db_session, "after_flush", _count)

    assert exc.value.code == REFUSE_ACTIVATE_WITHOUT_DEFINITION
    assert flush_count["n"] == 0, "activate_family дошла до flush до проверки определения"

    db_session.refresh(fam)
    assert fam.status == FamilyStatus.draft.value
    assert fam.activated_at is None
    assert fam.activated_by is None


def test_activate_with_whitespace_only_definition_refuses(db_session, factories):
    """Пустое/пробельное определение, поданное в `create_family`, — то же
    самое, что `NULL`, потому что `create_family` его уже свернуло
    (`_normalize_definition`) ДО того, как оно легло в базу. Этот тест
    поэтому проверяет `activate_family` на `definition IS NULL`, а не на
    ветку `.strip()` предиката `_has_definition` — её отдельно и напрямую
    проверяет `test_activate_blank_definition_set_by_direct_update_refuses`
    ниже (этот тест был ложно-зелёным
    относительно своего имени)."""
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Пробелы", unit_name=None, definition="   ",
        actor_id=user.id,
    )
    assert fam.definition is None  # уже свёрнуто create_family
    with pytest.raises(WorkFamilyError) as exc:
        activate_family(db_session, family_id=fam.id, actor_id=user.id)
    assert exc.value.code == REFUSE_ACTIVATE_WITHOUT_DEFINITION


def test_activate_blank_definition_set_by_direct_update_refuses(db_session, factories):
    """Вход, который реально проверяет
    ветку `.strip()` предиката `_has_definition` внутри `activate_family` —
    `definition` кладётся пробельной строкой ПРЯМОЙ правкой в обход
    `create_family` (черновик — `CHECK` активации на `draft` не смотрит), а
    не через сервис, который её уже нормализует."""
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Пробелы прямой правкой", unit_name=None, definition=None,
        actor_id=user.id,
    )
    db_session.execute(
        sa.update(WorkFamily).where(WorkFamily.id == fam.id).values(definition="   ")
    )
    db_session.expire_all()
    assert fam.definition == "   "  # именно НЕ None — иначе вход не тот

    with pytest.raises(WorkFamilyError) as exc:
        activate_family(db_session, family_id=fam.id, actor_id=user.id)
    assert exc.value.code == REFUSE_ACTIVATE_WITHOUT_DEFINITION


def test_activate_after_adding_definition_succeeds_and_fills_pair(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Будет активирована", unit_name="M2", definition=None,
        actor_id=user.id,
    )
    with pytest.raises(WorkFamilyError):
        activate_family(db_session, family_id=fam.id, actor_id=user.id)

    update_family(
        db_session, family_id=fam.id, title=None,
        definition="Теперь есть определение", actor_id=user.id,
    )
    activated = activate_family(db_session, family_id=fam.id, actor_id=user.id)

    assert activated.status == FamilyStatus.active.value
    assert activated.activated_by == user.id
    assert activated.activated_at is not None
    events = _events(db_session, fam.id, "family_activated")
    assert len(events) == 1
    assert events[0].payload == {"title": "Будет активирована", "unit": "M2"}


def test_activate_not_draft_refuses_from_active_and_archived(db_session, factories):
    user = factories.UserFactory.create()
    fam_active = create_family(
        db_session, title=f"Уже активна {_uid()}", unit_name=None,
        definition="Определение", actor_id=user.id,
    )
    activate_family(db_session, family_id=fam_active.id, actor_id=user.id)
    with pytest.raises(WorkFamilyError) as exc_active:
        activate_family(db_session, family_id=fam_active.id, actor_id=user.id)
    assert exc_active.value.code == REFUSE_ACTIVATE_NOT_DRAFT

    fam_archived = create_family(
        db_session, title=f"Уже архивна {_uid()}", unit_name=None,
        definition="Определение", actor_id=user.id,
    )
    fam_archived.status = FamilyStatus.archived.value
    db_session.flush()
    with pytest.raises(WorkFamilyError) as exc_archived:
        activate_family(db_session, family_id=fam_archived.id, actor_id=user.id)
    assert exc_archived.value.code == REFUSE_ACTIVATE_NOT_DRAFT


def test_activate_not_found_refuses(db_session, factories):
    user = factories.UserFactory.create()
    with pytest.raises(WorkFamilyError) as exc:
        activate_family(db_session, family_id=999_999_999, actor_id=user.id)
    assert exc.value.code == REFUSE_FAMILY_NOT_FOUND


# ---------------------------------------------------------------------------
#  CHECK — вторая линия защиты, независимая от кода операции.
# ---------------------------------------------------------------------------


def test_check_still_rejects_direct_update_bypassing_operation(db_session, factories):
    """Прямой `UPDATE` в обход `activate_family` — тот же отказ, что дала бы
    операция, но от БАЗЫ (`IntegrityError`), а не от Python
    (`WorkFamilyError`): `CHECK` — вторая, независимая линия."""
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Обход операции", unit_name=None, definition=None,
        actor_id=user.id,
    )
    with rejected(db_session, contains='"ck_work_families_active_needs_definition"'):
        db_session.execute(
            sa.update(WorkFamily)
            .where(WorkFamily.id == fam.id)
            .values(status=FamilyStatus.active.value)
        )


def test_check_still_rejects_direct_update_with_blank_definition(db_session, factories):
    """Та же линия защиты, вторая ветвь предиката (`btrim(definition) <>
    ''`, а не `IS NOT NULL`) — пустая строка, а не `NULL`."""
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Обход с пробелами", unit_name=None, definition=None,
        actor_id=user.id,
    )
    with rejected(db_session, contains='"ck_work_families_active_needs_definition"'):
        db_session.execute(
            sa.update(WorkFamily)
            .where(WorkFamily.id == fam.id)
            .values(status=FamilyStatus.active.value, definition="   ")
        )


# ---------------------------------------------------------------------------
#  Активные дубли имени × единицы — доменный отказ (REFUSE_DUPLICATE_ACTIVE_
#  FAMILY), не сырой IntegrityError; в draft/archived — проходят.
# ---------------------------------------------------------------------------


def test_activate_duplicate_active_name_and_unit_is_a_domain_refusal(db_session, factories):
    """Черновики МОГУТ делить нормализованные имя и единицу (частичный
    уникальный индекс держит их только среди `active`, см.
    `test_same_name_and_unit_in_draft_pass` ниже) — активация ВТОРОГО такого
    черновика поэтому обычный вход, доменный отказ, а не необработанный
    `IntegrityError` от `uq_work_families_active_name_unit`."""
    user = factories.UserFactory.create()
    title = f"Штукатурка стен {_uid()}"
    f1 = create_family(
        db_session, title=title, unit_name="M2", definition="Определение А",
        actor_id=user.id,
    )
    f2 = create_family(
        db_session, title=title, unit_name="M2", definition="Определение Б",
        actor_id=user.id,
    )
    activate_family(db_session, family_id=f1.id, actor_id=user.id)
    with pytest.raises(WorkFamilyError) as exc:
        activate_family(db_session, family_id=f2.id, actor_id=user.id)
    assert exc.value.code == REFUSE_DUPLICATE_ACTIVE_FAMILY
    assert exc.value.duplicate_family_id == f1.id


def test_same_name_and_unit_in_draft_pass(db_session, factories):
    user = factories.UserFactory.create()
    title = f"Дублирующее имя (draft) {_uid()}"
    f1 = create_family(db_session, title=title, unit_name="M2", definition=None, actor_id=user.id)
    f2 = create_family(db_session, title=title, unit_name="M2", definition=None, actor_id=user.id)
    assert f1.id != f2.id
    assert f1.status == f2.status == FamilyStatus.draft.value


def test_same_name_and_unit_in_archived_pass(db_session, factories):
    """Архивирование ставится прямой правкой — операции архивирования в
    задаче 7 нет (план, задача 7)."""
    user = factories.UserFactory.create()
    title = f"Дублирующее имя (archived) {_uid()}"
    f1 = create_family(db_session, title=title, unit_name="M2", definition=None, actor_id=user.id)
    f2 = create_family(db_session, title=title, unit_name="M2", definition=None, actor_id=user.id)
    f1.status = FamilyStatus.archived.value
    f2.status = FamilyStatus.archived.value
    db_session.flush()  # не обязан упасть
    assert f1.id != f2.id


# ---------------------------------------------------------------------------
#  Команда `seed-work-families`: отказ на неразрешённой цели через `_guard`.
# ---------------------------------------------------------------------------

REMOTE_URL = (
    "postgresql+psycopg://test_owner:secret-pw@"
    "ep-example-0000.c-3.eu-central-1.aws.neon.tech/neondb"
)


@pytest.fixture
def _unlisted_target_in_dev(monkeypatch):
    """Тот же приём, что `test_db_guard_wiring.py`: APP_ENV=dev, пустой
    allowlist, удалённый хост — цель не разрешена."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")
    monkeypatch.delenv("PGPORT", raising=False)
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    monkeypatch.delenv("PGHOST", raising=False)
    monkeypatch.delenv("PGDATABASE", raising=False)
    monkeypatch.setattr(cli.settings, "DATABASE_URL", REMOTE_URL, raising=False)


def test_seed_command_refuses_unlisted_target(_unlisted_target_in_dev, monkeypatch):
    """Guard срабатывает ДО открытия сессии — иначе seed уезжает в чужую
    базу без единого признака (план, задача 7, «Утверждения»)."""

    def _explode():
        raise AssertionError("SessionLocal() вызван — guard сработал слишком поздно")

    monkeypatch.setattr(cli, "SessionLocal", _explode)

    result = CliRunner().invoke(cli.cli, ["seed-work-families"])
    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert "APP_ENV=dev" in str(result.exception)


def test_seed_command_passes_guard_when_prod(monkeypatch):
    """При APP_ENV=prod guard пропускает — дальше идёт обычная работа
    команды (проверяем именно, что барьер снят)."""
    monkeypatch.setenv("APP_ENV", "prod")
    reached = []

    def _record():
        reached.append(True)
        raise RuntimeError("stop-here")

    monkeypatch.setattr(cli, "SessionLocal", _record)
    CliRunner().invoke(cli.cli, ["seed-work-families"])
    assert reached, "guard не пустил дальше, хотя запись разрешена"


def test_seed_command_success_path_persists_and_prints_report(
    committing_session_factory, monkeypatch
):
    """Счастливый путь
    команды не был проверен НИ ОДНИМ тестом — снятие `db.commit()` в теле
    `seed_work_families` оставалось зелёным на всём наборе. Гоняем команду
    на настоящей тестовой базе через `committing_session_factory`
    (настоящие commit-ы, домeнные таблицы чистятся до/после — `conftest.py`)
    и проверяем, что строки видны из НЕЗАВИСИМОЙ сессии уже ПОСЛЕ возврата
    команды, а не только внутри той же транзакции, и что отчёт напечатан."""
    monkeypatch.setenv("APP_ENV", "prod")  # тот же приём, что и выше — guard пропускает по-настоящему
    monkeypatch.setattr(cli, "SessionLocal", committing_session_factory)

    result = CliRunner().invoke(cli.cli, ["seed-work-families"])

    assert result.exit_code == 0, (
        f"команда упала: {result.output}\n{result.exception!r}"
    )
    assert "создано=42" in result.output
    assert "пропущено (уже существуют)=0" in result.output
    assert "с определением в файле=6" in result.output

    verify_db = committing_session_factory()
    try:
        total = verify_db.execute(
            sa.select(sa.func.count())
            .select_from(WorkFamily)
            .where(WorkFamily.seed_key.isnot(None))
        ).scalar_one()
        assert total == EXPECTED_TOTAL, "строки не видны из независимой сессии после команды"
    finally:
        verify_db.close()


# ---------------------------------------------------------------------------
#  Задача 8 — помощники (ЛОКАЛЬНАЯ копия, не импорт: см. докстроку модуля).
# ---------------------------------------------------------------------------


def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _position(factories, proposal, *, catalog_position=None, title="Работа"):
    kwargs = dict(proposal=proposal, is_chapter=False, job_title_in_proposal=title)
    if catalog_position is not None:
        kwargs["catalog_position_id"] = catalog_position.id
    return factories.PositionItemFactory.create(**kwargs)


def _unit_id(db, code):
    return UnitResolver(db).resolve(code).unit_id


def _routed_context(db, factories, *, unit_id=None, catalog_kind=CatalogKind.POSITION.value):
    """Реальная маршрутизация (задача 4): корзина + контекст по умолчанию
    строятся `route_position`, единица и вид каталожной строки — по
    аргументам. Возвращает `(context, catalog_position)`."""
    proposal = _proposal(factories)
    cp = factories.CatalogPositionFactory.create(unit_id=unit_id, kind=catalog_kind)
    position = _position(factories, proposal, catalog_position=cp)
    member = route_position(db, position_item_id=position.id)
    context = db.get(CatalogContext, member.context_id)
    return context, cp


def _context_events(db, context_id, event_type=None):
    stmt = sa.select(SemanticEvent).where(SemanticEvent.context_id == context_id)
    if event_type is not None:
        stmt = stmt.where(SemanticEvent.event_type == event_type)
    return db.execute(stmt).scalars().all()


def _active_family(db, *, title, unit_name, actor_id, definition="Определение"):
    fam = create_family(db, title=title, unit_name=unit_name, definition=definition, actor_id=actor_id)
    return activate_family(db, family_id=fam.id, actor_id=actor_id)


# ---------------------------------------------------------------------------
#  assign_family (спека §2.7 «Назначение семьи контексту»)
# ---------------------------------------------------------------------------


class TestAssignFamily:
    def test_success_sets_manual_triple_and_event(self, db_session, factories):
        user = factories.UserFactory.create()
        unit = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=unit)
        family = _active_family(
            db_session, title=f"Штукатурка {_uid()}", unit_name="M2", actor_id=user.id
        )

        result = assign_family(
            db_session, context_id=context.id, family_id=family.id, actor_id=user.id
        )
        assert result.work_family_id == family.id
        assert result.family_source == FamilySource.manual.value
        assert result.family_by == user.id
        assert result.family_at is not None

        events = _context_events(db_session, context.id, "context_family_assigned")
        assert len(events) == 1
        assert events[0].payload == {
            "from_family_id": None, "to_family_id": family.id, "source": "manual",
        }

    def test_unassign_clears_triple_fully(self, db_session, factories):
        user = factories.UserFactory.create()
        unit = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=unit)
        family = _active_family(
            db_session, title=f"Плитка {_uid()}", unit_name="M2", actor_id=user.id
        )
        assign_family(db_session, context_id=context.id, family_id=family.id, actor_id=user.id)

        result = assign_family(
            db_session, context_id=context.id, family_id=None, actor_id=user.id
        )
        assert result.work_family_id is None
        assert result.family_source is None
        assert result.family_by is None
        assert result.family_at is None

        events = _context_events(db_session, context.id, "context_family_assigned")
        assert len(events) == 2
        assert events[-1].payload == {
            "from_family_id": family.id, "to_family_id": None, "source": "manual",
        }

    def test_unit_mismatch_family_has_unit_context_has_none(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        user = factories.UserFactory.create()
        m2 = _unit_id(db_session, "M2")
        family = _active_family(
            db_session, title=f"С единицей {_uid()}", unit_name="M2", actor_id=user.id
        )

        with pytest.raises(WorkFamilyError) as exc:
            assign_family(
                db_session, context_id=context.id, family_id=family.id, actor_id=user.id
            )
        assert exc.value.code == REFUSE_UNIT_MISMATCH
        assert exc.value.family_unit_id == m2
        assert exc.value.context_unit_id is None

    def test_unit_mismatch_context_has_unit_family_has_none(self, db_session, factories):
        m2 = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=m2)
        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"Без единицы {_uid()}", unit_name=None, actor_id=user.id
        )

        with pytest.raises(WorkFamilyError) as exc:
            assign_family(
                db_session, context_id=context.id, family_id=family.id, actor_id=user.id
            )
        assert exc.value.code == REFUSE_UNIT_MISMATCH
        assert exc.value.family_unit_id is None
        assert exc.value.context_unit_id == m2

    def test_archived_context_refuses(self, db_session, factories):
        """Архивный контекст «выведен из обращения» (спека §2.8) — семью на
        него не назначить, тем же кодом, что операции `context_operations`."""
        user = factories.UserFactory.create()
        unit = _unit_id(db_session, "M2")
        context, _cp = _routed_context(db_session, factories, unit_id=unit)
        family = _active_family(
            db_session, title=f"Архивный контекст {_uid()}", unit_name="M2", actor_id=user.id
        )
        context.archived_at = dt.datetime.now(dt.UTC)
        db_session.flush()
        db_session.expire(context)

        with pytest.raises(WorkFamilyError) as exc:
            assign_family(
                db_session, context_id=context.id, family_id=family.id, actor_id=user.id
            )
        assert exc.value.code == REFUSE_CONTEXT_ARCHIVED

    def test_requires_active_draft_refuses(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        user = factories.UserFactory.create()
        family = create_family(
            db_session, title=f"Черновик {_uid()}", unit_name=None, definition=None,
            actor_id=user.id,
        )
        with pytest.raises(WorkFamilyError) as exc:
            assign_family(
                db_session, context_id=context.id, family_id=family.id, actor_id=user.id
            )
        assert exc.value.code == REFUSE_FAMILY_NOT_ACTIVE
        assert exc.value.status == FamilyStatus.draft.value

    def test_requires_active_archived_refuses(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"Архивная {_uid()}", unit_name=None, actor_id=user.id
        )
        family.status = FamilyStatus.archived.value
        db_session.flush()

        with pytest.raises(WorkFamilyError) as exc:
            assign_family(
                db_session, context_id=context.id, family_id=family.id, actor_id=user.id
            )
        assert exc.value.code == REFUSE_FAMILY_NOT_ACTIVE
        assert exc.value.status == FamilyStatus.archived.value

    def test_system_kind_allowed(self, db_session, factories):
        """Семья у `SYSTEM`-контекста — не запрещена (спека §2.7): в эталоне
        такого сочетания нет ни разу, но это отсутствие запрета, а не
        обязательность (план, Task 8, «Семья у SYSTEM разрешена»)."""
        set_unit_id = _unit_id(db_session, "компл")
        context, _cp = _routed_context(db_session, factories, unit_id=set_unit_id)
        assert context.semantic_kind == SemanticKind.SYSTEM.value  # предпосылка входа

        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"Комплект {_uid()}", unit_name="компл", actor_id=user.id
        )
        result = assign_family(
            db_session, context_id=context.id, family_id=family.id, actor_id=user.id
        )
        assert result.work_family_id == family.id

    def test_assignment_does_not_change_semantic_state(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        before = context.semantic_state
        assert before == SemanticState.SUGGESTED.value  # предпосылка

        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"Не трогает ось {_uid()}", unit_name=None, actor_id=user.id
        )
        assign_family(db_session, context_id=context.id, family_id=family.id, actor_id=user.id)
        db_session.refresh(context)
        assert context.semantic_state == before

    def test_context_not_found_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            assign_family(
                db_session, context_id=999_999_999, family_id=None, actor_id=user.id
            )
        assert exc.value.code == REFUSE_CONTEXT_NOT_FOUND

    def test_family_not_found_refuses(self, db_session, factories):
        context, _cp = _routed_context(db_session, factories, unit_id=None)
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            assign_family(
                db_session, context_id=context.id, family_id=999_999_999, actor_id=user.id
            )
        assert exc.value.code == REFUSE_FAMILY_NOT_FOUND


# ---------------------------------------------------------------------------
#  set_unit (спека §2.7 «Единица семьи после привязки»)
# ---------------------------------------------------------------------------


class TestSetUnit:
    def test_changes_unit_and_records_event(self, db_session, factories):
        user = factories.UserFactory.create()
        family = create_family(
            db_session, title=f"Смена единицы {_uid()}", unit_name=None, definition=None,
            actor_id=user.id,
        )
        m2 = _unit_id(db_session, "M2")

        updated = set_unit(db_session, family_id=family.id, unit_name="M2", actor_id=user.id)
        assert updated.unit_id == m2

        events = [
            e for e in _events(db_session, family.id, "family_updated")
        ]
        assert len(events) == 1
        assert events[0].payload["changed"] == [
            {"field": "unit_id", "from": None, "to": m2}
        ]

    def test_no_real_change_writes_no_event(self, db_session, factories):
        user = factories.UserFactory.create()
        family = create_family(
            db_session, title=f"Та же единица {_uid()}", unit_name="PCS", definition=None,
            actor_id=user.id,
        )
        before = len(_events(db_session, family.id, "family_updated"))
        set_unit(db_session, family_id=family.id, unit_name="PCS", actor_id=user.id)
        after = len(_events(db_session, family.id, "family_updated"))
        assert after == before == 0

    def test_refuses_with_links_and_names_count(self, db_session, factories):
        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"С привязками {_uid()}", unit_name="PCS", actor_id=user.id
        )
        pcs = _unit_id(db_session, "PCS")
        context1, _ = _routed_context(db_session, factories, unit_id=pcs)
        context2, _ = _routed_context(db_session, factories, unit_id=pcs)
        assign_family(db_session, context_id=context1.id, family_id=family.id, actor_id=user.id)
        assign_family(db_session, context_id=context2.id, family_id=family.id, actor_id=user.id)

        with pytest.raises(WorkFamilyError) as exc:
            set_unit(db_session, family_id=family.id, unit_name="M2", actor_id=user.id)
        assert exc.value.code == REFUSE_UNIT_CHANGE_WITH_LINKS
        assert exc.value.count == 2  # ЧИСЛО ≥ 2 — отличимо от константы

    def test_passes_with_zero_links(self, db_session, factories):
        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"Без привязок {_uid()}", unit_name="PCS", actor_id=user.id
        )
        m2 = _unit_id(db_session, "M2")
        updated = set_unit(db_session, family_id=family.id, unit_name="M2", actor_id=user.id)
        assert updated.unit_id == m2

    def test_unknown_unit_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        family = create_family(
            db_session, title=f"Неизвестная единица {_uid()}", unit_name=None, definition=None,
            actor_id=user.id,
        )
        with pytest.raises(WorkFamilyError) as exc:
            set_unit(
                db_session, family_id=family.id, unit_name="совсем-не-единица-xyz",
                actor_id=user.id,
            )
        assert exc.value.code == REFUSE_UNKNOWN_UNIT

    def test_family_not_found_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            set_unit(db_session, family_id=999_999_999, unit_name="M2", actor_id=user.id)
        assert exc.value.code == REFUSE_FAMILY_NOT_FOUND


# ---------------------------------------------------------------------------
#  archive_family (спека §2.7 «Архивирование семьи»)
# ---------------------------------------------------------------------------


class TestArchiveFamily:
    def test_refuses_with_links_and_names_count(self, db_session, factories):
        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"Архив с привязками {_uid()}", unit_name="PCS", actor_id=user.id
        )
        pcs = _unit_id(db_session, "PCS")
        context1, _ = _routed_context(db_session, factories, unit_id=pcs)
        context2, _ = _routed_context(db_session, factories, unit_id=pcs)
        assign_family(db_session, context_id=context1.id, family_id=family.id, actor_id=user.id)
        assign_family(db_session, context_id=context2.id, family_id=family.id, actor_id=user.id)

        with pytest.raises(WorkFamilyError) as exc:
            archive_family(db_session, family_id=family.id, actor_id=user.id)
        assert exc.value.code == REFUSE_ARCHIVE_WITH_LINKS
        assert exc.value.count == 2

    def test_passes_after_links_removed(self, db_session, factories):
        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"Освобождённая {_uid()}", unit_name="PCS", actor_id=user.id
        )
        pcs = _unit_id(db_session, "PCS")
        context, _ = _routed_context(db_session, factories, unit_id=pcs)
        assign_family(db_session, context_id=context.id, family_id=family.id, actor_id=user.id)
        assign_family(db_session, context_id=context.id, family_id=None, actor_id=user.id)

        archived = archive_family(db_session, family_id=family.id, actor_id=user.id)
        assert archived.status == FamilyStatus.archived.value
        assert archived.archived_at is not None
        events = _events(db_session, family.id, "family_archived")
        assert len(events) == 1
        assert events[0].payload == {"reason": "operator"}

    def test_passes_with_zero_links_never_assigned(self, db_session, factories):
        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"Никогда не назначалась {_uid()}", unit_name=None,
            actor_id=user.id,
        )
        archived = archive_family(db_session, family_id=family.id, actor_id=user.id)
        assert archived.status == FamilyStatus.archived.value

    def test_family_not_found_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        with pytest.raises(WorkFamilyError) as exc:
            archive_family(db_session, family_id=999_999_999, actor_id=user.id)
        assert exc.value.code == REFUSE_FAMILY_NOT_FOUND

    def test_already_archived_refuses_without_second_event(self, db_session, factories):
        """Повторное архивирование уже
        архивной семьи отказывает, называя статус `archived`, а не молча
        переустанавливает `archived_at` и не пишет второе `family_archived`."""
        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"Дважды архивная {_uid()}", unit_name=None, actor_id=user.id
        )
        archive_family(db_session, family_id=family.id, actor_id=user.id)
        events_before = len(_events(db_session, family.id, "family_archived"))
        archived_at_before = family.archived_at

        with pytest.raises(WorkFamilyError) as exc:
            archive_family(db_session, family_id=family.id, actor_id=user.id)
        assert exc.value.code == REFUSE_FAMILY_NOT_ACTIVE
        assert exc.value.status == FamilyStatus.archived.value

        db_session.refresh(family)
        assert family.archived_at == archived_at_before
        assert len(_events(db_session, family.id, "family_archived")) == events_before


# ---------------------------------------------------------------------------
#  merge_families (спека §2.7 «Слияние семей»)
# ---------------------------------------------------------------------------


class TestMergeFamilies:
    def test_success_moves_links_preserves_provenance_archives_source(
        self, db_session, factories
    ):
        """Назначение и слияние делают
        РАЗНЫЕ пользователи — `assigner`/`merger`. С ОДНИМ и тем же актёром
        баг «слияние переписывает family_by актёром СЛИЯНИЯ» был бы
        невидим (совпадение id ничего не доказывает); с разными — виден
        сравнением ЗНАЧЕНИЙ, а не только «до==после»."""
        assigner = factories.UserFactory.create()
        merger = factories.UserFactory.create()
        assert assigner.id != merger.id  # предпосылка — иначе тест ничего не доказывает
        pcs = _unit_id(db_session, "PCS")
        source = _active_family(
            db_session, title=f"Источник {_uid()}", unit_name="PCS", actor_id=assigner.id
        )
        target = _active_family(
            db_session, title=f"Цель {_uid()}", unit_name="PCS", actor_id=assigner.id,
            definition="Определение цели",
        )
        target_title_before = target.title
        target_definition_before = target.definition

        context, _ = _routed_context(db_session, factories, unit_id=pcs)
        assign_family(
            db_session, context_id=context.id, family_id=source.id, actor_id=assigner.id
        )
        family_by_before = context.family_by
        family_source_before = context.family_source
        family_at_before = context.family_at
        assert family_by_before == assigner.id  # предпосылка
        source_title_before = source.title

        moved = merge_families(
            db_session, source_family_id=source.id, target_family_id=target.id,
            actor_id=merger.id,
        )
        assert moved == 1

        db_session.refresh(context)
        assert context.work_family_id == target.id
        # тройка происхождения КОНТЕКСТА цела пополям — слияние её не трогает,
        # НЕЗАВИСИМО от того, кто мержил (`merger.id`, не `assigner.id`).
        assert context.family_by == family_by_before == assigner.id
        assert context.family_source == family_source_before
        assert context.family_at == family_at_before

        db_session.refresh(source)
        assert source.status == FamilyStatus.archived.value
        assert source.archived_at is not None

        db_session.refresh(target)
        assert target.title == target_title_before
        assert target.definition == target_definition_before

        merged_events = _events(db_session, source.id, "family_merged")
        assert len(merged_events) == 1
        assert merged_events[0].payload == {
            "into_family_id": target.id, "moved_contexts": 1, "source_title": source_title_before,
        }
        archived_events = _events(db_session, source.id, "family_archived")
        assert len(archived_events) == 1
        assert archived_events[0].payload == {"reason": "merged"}

    def test_same_family_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"Сама на себя {_uid()}", unit_name=None, actor_id=user.id
        )
        with pytest.raises(WorkFamilyError) as exc:
            merge_families(
                db_session, source_family_id=family.id, target_family_id=family.id,
                actor_id=user.id,
            )
        assert exc.value.code == REFUSE_MERGE_SAME_FAMILY

    def test_source_not_found_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        target = _active_family(
            db_session, title=f"Цель одна {_uid()}", unit_name=None, actor_id=user.id
        )
        with pytest.raises(WorkFamilyError) as exc:
            merge_families(
                db_session, source_family_id=999_999_999, target_family_id=target.id,
                actor_id=user.id,
            )
        assert exc.value.code == REFUSE_FAMILY_NOT_FOUND

    def test_target_not_found_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        source = _active_family(
            db_session, title=f"Источник один {_uid()}", unit_name=None, actor_id=user.id
        )
        with pytest.raises(WorkFamilyError) as exc:
            merge_families(
                db_session, source_family_id=source.id, target_family_id=999_999_999,
                actor_id=user.id,
            )
        assert exc.value.code == REFUSE_FAMILY_NOT_FOUND

    def test_unit_mismatch_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        source = _active_family(
            db_session, title=f"М2 {_uid()}", unit_name="M2", actor_id=user.id
        )
        target = _active_family(
            db_session, title=f"ШТ {_uid()}", unit_name="PCS", actor_id=user.id
        )
        with pytest.raises(WorkFamilyError) as exc:
            merge_families(
                db_session, source_family_id=source.id, target_family_id=target.id,
                actor_id=user.id,
            )
        assert exc.value.code == REFUSE_MERGE_UNIT_MISMATCH
        assert exc.value.source_unit_id == source.unit_id
        assert exc.value.target_unit_id == target.unit_id

    def test_target_draft_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        source = _active_family(
            db_session, title=f"Источник актив {_uid()}", unit_name=None, actor_id=user.id
        )
        target = create_family(
            db_session, title=f"Цель черновик {_uid()}", unit_name=None, definition=None,
            actor_id=user.id,
        )
        with pytest.raises(WorkFamilyError) as exc:
            merge_families(
                db_session, source_family_id=source.id, target_family_id=target.id,
                actor_id=user.id,
            )
        assert exc.value.code == REFUSE_MERGE_INACTIVE
        assert exc.value.role == "target"
        assert exc.value.status == FamilyStatus.draft.value

    def test_target_archived_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        source = _active_family(
            db_session, title=f"Источник актив 2 {_uid()}", unit_name=None, actor_id=user.id
        )
        target = _active_family(
            db_session, title=f"Цель архив {_uid()}", unit_name=None, actor_id=user.id
        )
        target.status = FamilyStatus.archived.value
        db_session.flush()
        with pytest.raises(WorkFamilyError) as exc:
            merge_families(
                db_session, source_family_id=source.id, target_family_id=target.id,
                actor_id=user.id,
            )
        assert exc.value.code == REFUSE_MERGE_INACTIVE
        assert exc.value.role == "target"
        assert exc.value.status == FamilyStatus.archived.value

    def test_source_not_active_refuses(self, db_session, factories):
        user = factories.UserFactory.create()
        source = create_family(
            db_session, title=f"Источник черновик {_uid()}", unit_name=None, definition=None,
            actor_id=user.id,
        )
        target = _active_family(
            db_session, title=f"Цель актив {_uid()}", unit_name=None, actor_id=user.id
        )
        with pytest.raises(WorkFamilyError) as exc:
            merge_families(
                db_session, source_family_id=source.id, target_family_id=target.id,
                actor_id=user.id,
            )
        assert exc.value.code == REFUSE_MERGE_INACTIVE
        assert exc.value.role == "source"
        assert exc.value.status == FamilyStatus.draft.value

    def test_context_reassigned_away_between_listing_and_lock_is_excluded(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Защита сверх явного плана
        нуждается в НАСТОЯЩЕМ входе — другая, ПОЛНОСТЬЮ ЗАКОММИЧЕННАЯ сессия
        переводит контекст на ТРЕТЬЮ семью РОВНО МЕЖДУ листингом кандидатов
        (уже выполненным запросом внутри `merge_families`) и локом контекстов
        (ещё не взятым) — точка инъекции через монки-патч `_lock_contexts`,
        вызывается ОДИН раз, до вызова оригинала. Без перечитывания условия
        `work_family_id == source_family_id` УЖЕ ПОД локом контекст был бы
        перенесён слиянием повторно, поверх решения третьей семьи."""
        user = committing_factories.UserFactory.create()
        pcs = _unit_id(committing_db, "PCS")
        source = _active_family(
            committing_db, title=f"Источник перехвата {_uid()}", unit_name="PCS",
            actor_id=user.id,
        )
        target = _active_family(
            committing_db, title=f"Цель перехвата {_uid()}", unit_name="PCS", actor_id=user.id
        )
        other = _active_family(
            committing_db, title=f"Третья семья {_uid()}", unit_name="PCS", actor_id=user.id
        )
        context, _ = _routed_context(committing_db, committing_factories, unit_id=pcs)
        committing_db.commit()
        assign_family(committing_db, context_id=context.id, family_id=source.id, actor_id=user.id)
        committing_db.commit()
        context_id = context.id
        other_id = other.id

        original_lock_contexts = work_families_module._lock_contexts
        injected = {"done": False}

        def patched_lock_contexts(db, ids):
            if not injected["done"]:
                injected["done"] = True
                with committing_session_factory() as injector:
                    injector_ctx = injector.get(CatalogContext, context_id)
                    injector_ctx.work_family_id = other_id
                    injector.commit()
            original_lock_contexts(db, ids)

        with mock.patch.object(
            work_families_module, "_lock_contexts", side_effect=patched_lock_contexts
        ):
            moved = merge_families(
                committing_db, source_family_id=source.id, target_family_id=target.id,
                actor_id=user.id,
            )
        assert moved == 0

        check_ctx = committing_db.get(CatalogContext, context_id)
        committing_db.refresh(check_ctx)
        assert check_ctx.work_family_id == other_id  # не тронут слиянием


# ---------------------------------------------------------------------------
#  Режим и позиция локов — компиляцией РЕАЛЬНОГО запроса (задача 8; тот же
#  приём, что `TestAllFourOperationsCompileToForUpdateBeforePreconditionReads`
#  в `test_context_operations.py`, задача 6). Помощники — ЛОКАЛЬНАЯ копия.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _capturing_sql(session):
    statements: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", _listener)


def _find_lock_statement(statements, table, *, mode):
    other_mode = "FOR UPDATE" if mode == "FOR SHARE" else "FOR SHARE"
    for index, statement in enumerate(statements):
        if table in statement and f" {mode}" in statement and other_mode not in statement:
            return index, statement
    raise AssertionError(
        f"нет запроса к {table!r} с {mode} среди {len(statements)}:\n"
        + "\n---\n".join(statements)
    )


def _count_query_indices(statements, table):
    """Индексы запросов `SELECT count(...)` по `table` — отдельно от
    `_plain_read_indices`, потому что `COUNT` не читает атрибуты ORM-объекта
    (identity map его не кэширует) и его текст не начинается с "SELECT ...
    FROM <table>" в том же виде, что `db.get()`."""
    indices = []
    for index, statement in enumerate(statements):
        lowered = statement.lower()
        if table in statement and "count(" in lowered and "select" in lowered:
            indices.append(index)
    return indices


def _plain_read_indices(statements, table):
    """Индексы `SELECT`-ов по `table` БЕЗ `FOR UPDATE`/`FOR SHARE` — обычные
    `db.get()`, не лок (по ВСЕМУ списку запросов, не
    только после лока)."""
    indices = []
    for index, statement in enumerate(statements):
        stripped = statement.lstrip().upper()
        if (
            stripped.startswith("SELECT")
            and table in statement
            and "FOR UPDATE" not in statement
            and "FOR SHARE" not in statement
        ):
            indices.append(index)
    return indices


class TestFamilyLockCompilation:
    def test_assign_family_share_before_context_update(self, db_session, factories):
        user = factories.UserFactory.create()
        m2 = _unit_id(db_session, "M2")
        context, _ = _routed_context(db_session, factories, unit_id=m2)
        family = _active_family(
            db_session, title=f"Лок назначения {_uid()}", unit_name="M2", actor_id=user.id
        )

        with _capturing_sql(db_session) as statements:
            assign_family(
                db_session, context_id=context.id, family_id=family.id, actor_id=user.id
            )

        family_lock_i, _ = _find_lock_statement(statements, "work_families", mode="FOR SHARE")
        context_lock_i, _ = _find_lock_statement(statements, "catalog_contexts", mode="FOR UPDATE")
        assert family_lock_i < context_lock_i, "семья обязана блокироваться РАНЬШЕ контекста"

        after_locks = [i for i in _plain_read_indices(statements, "work_families") if i > context_lock_i]
        assert after_locks, "нет перечитывания статуса семьи ПОСЛЕ обоих локов"

    def test_set_unit_for_update_with_reread(self, db_session, factories):
        user = factories.UserFactory.create()
        family = create_family(
            db_session, title=f"Лок единицы {_uid()}", unit_name=None, definition=None,
            actor_id=user.id,
        )
        # `create_family` уже загрузила объект в identity map этой же сессии
        # — без `expire` существующее чтение `db.get()` внутри `set_unit`
        # вернуло бы кэш БЕЗ SQL, и «одно чтение до лока» стало бы неверной
        # предпосылкой теста, а не утверждением о коде.
        db_session.expire(family)
        with _capturing_sql(db_session) as statements:
            set_unit(db_session, family_id=family.id, unit_name="PCS", actor_id=user.id)

        lock_i, lock_statement = _find_lock_statement(statements, "work_families", mode="FOR UPDATE")
        assert "FOR SHARE" not in lock_statement
        plain_reads = _plain_read_indices(statements, "work_families")
        assert len([i for i in plain_reads if i < lock_i]) == 1, "ровно одно чтение до лока (существование)"
        assert [i for i in plain_reads if i > lock_i], "нет перечитывания после лока"

        count_indices = _count_query_indices(statements, "catalog_contexts")
        assert count_indices, "не найден запрос COUNT привязок по catalog_contexts"
        assert min(count_indices) > lock_i, "число привязок обязано читаться ПОСЛЕ лока семьи"

    def test_activate_family_for_update_with_reread(self, db_session, factories):
        user = factories.UserFactory.create()
        family = create_family(
            db_session, title=f"Лок активации {_uid()}", unit_name=None,
            definition="Определение", actor_id=user.id,
        )
        db_session.expire(family)  # см. комментарий в test_set_unit_for_update_with_reread
        with _capturing_sql(db_session) as statements:
            activate_family(db_session, family_id=family.id, actor_id=user.id)

        lock_i, lock_statement = _find_lock_statement(statements, "work_families", mode="FOR UPDATE")
        assert "FOR SHARE" not in lock_statement
        plain_reads = _plain_read_indices(statements, "work_families")
        assert len([i for i in plain_reads if i < lock_i]) == 1, "ровно одно чтение до лока (существование)"
        assert [i for i in plain_reads if i > lock_i], "нет перечитывания статуса после лока"

    def test_archive_family_for_update_with_reread(self, db_session, factories):
        user = factories.UserFactory.create()
        family = _active_family(
            db_session, title=f"Лок архивирования {_uid()}", unit_name=None, actor_id=user.id
        )
        db_session.expire(family)  # см. комментарий в test_set_unit_for_update_with_reread
        with _capturing_sql(db_session) as statements:
            archive_family(db_session, family_id=family.id, actor_id=user.id)

        lock_i, lock_statement = _find_lock_statement(statements, "work_families", mode="FOR UPDATE")
        assert "FOR SHARE" not in lock_statement
        plain_reads = _plain_read_indices(statements, "work_families")
        assert len([i for i in plain_reads if i < lock_i]) == 1
        assert [i for i in plain_reads if i > lock_i], "нет перечитывания числа привязок после лока"

        count_indices = _count_query_indices(statements, "catalog_contexts")
        assert count_indices, "не найден запрос COUNT привязок по catalog_contexts"
        assert min(count_indices) > lock_i, "число привязок обязано читаться ПОСЛЕ лока семьи"

    def test_merge_families_family_lock_before_context_lock(self, db_session, factories):
        user = factories.UserFactory.create()
        pcs = _unit_id(db_session, "PCS")
        source = _active_family(
            db_session, title=f"Лок слияния источник {_uid()}", unit_name="PCS",
            actor_id=user.id,
        )
        target = _active_family(
            db_session, title=f"Лок слияния цель {_uid()}", unit_name="PCS", actor_id=user.id
        )
        context, _ = _routed_context(db_session, factories, unit_id=pcs)
        assign_family(db_session, context_id=context.id, family_id=source.id, actor_id=user.id)

        with _capturing_sql(db_session) as statements:
            merge_families(
                db_session, source_family_id=source.id, target_family_id=target.id,
                actor_id=user.id,
            )

        family_lock_i, family_lock_statement = _find_lock_statement(
            statements, "work_families", mode="FOR UPDATE"
        )
        assert "FOR SHARE" not in family_lock_statement
        context_lock_i, context_lock_statement = _find_lock_statement(
            statements, "catalog_contexts", mode="FOR UPDATE"
        )
        assert "FOR SHARE" not in context_lock_statement
        assert family_lock_i < context_lock_i, "семьи обязаны блокироваться РАНЬШЕ контекстов"

        after_family_lock = [
            i for i in _plain_read_indices(statements, "work_families") if i > family_lock_i
        ]
        assert after_family_lock, "нет перечитывания статусов семей после лока"


# ---------------------------------------------------------------------------
#  Перечитывание после лока. `test_merge_families_rereads_target_status` —
#  ГЕНУИННЫЙ приём БЕЗ потоков (тот же, что
#  `TestArchiveRereadsAfterLock`/`TestSplitContextRereadsAfterLock`,
#  `test_context_concurrency.py`, задача 6): другая закоммиченная сессия
#  меняет СТАТУС (атрибут ORM-объекта, кэшируемый identity map) МЕЖДУ первым
#  чтением этой сессии и локом — снятие `db.expire_all()` у `merge_families`
#  красит именно этот тест.
#
#  `test_set_unit_rereads_link_count`/`test_archive_family_rereads_link_count`
#  ниже — НЕ проверка перечитывания как механизма (число привязок — результат
#  `COUNT(*)`, а не атрибут ORM-объекта: identity map его не кэширует, и
#  `db.expire_all()` на него не влияет вовсе — проверено прогоном при попытке
#  RED), а обычные позитивные интеграционные проверки: привязка,
#  созданная ДРУГОЙ, полностью закоммиченной сессией, видна операции. Реальная
#  проверка «счёт читается ПОСЛЕ лока, не до» — `_count_query_indices` в
#  `TestFamilyLockCompilation` (компиляцией SQL, RED — перемещением строки с
#  `COUNT` перед локом).
# ---------------------------------------------------------------------------


class TestFamilyRereadAfterLock:
    def test_set_unit_rereads_link_count(
        self, committing_db, committing_factories, committing_session_factory
    ):
        user = committing_factories.UserFactory.create()
        family = _active_family(
            committing_db, title=f"Перечит. единицы {_uid()}", unit_name=None,
            actor_id=user.id,
        )
        committing_db.commit()

        primed = committing_db.get(WorkFamily, family.id)
        assert primed.status == FamilyStatus.active.value

        context, _ = _routed_context(committing_db, committing_factories, unit_id=None)
        committing_db.commit()
        with committing_session_factory() as other:
            other_ctx = other.get(CatalogContext, context.id)
            other_ctx.work_family_id = family.id
            other_ctx.family_source = FamilySource.manual.value
            other_ctx.family_by = user.id
            other_ctx.family_at = dt.datetime.now(dt.UTC)
            other.commit()

        with pytest.raises(WorkFamilyError) as exc:
            set_unit(committing_db, family_id=family.id, unit_name="M2", actor_id=user.id)
        assert exc.value.code == REFUSE_UNIT_CHANGE_WITH_LINKS
        assert exc.value.count == 1

    def test_archive_family_rereads_link_count(
        self, committing_db, committing_factories, committing_session_factory
    ):
        user = committing_factories.UserFactory.create()
        family = _active_family(
            committing_db, title=f"Перечит. архива {_uid()}", unit_name=None, actor_id=user.id
        )
        committing_db.commit()

        primed = committing_db.get(WorkFamily, family.id)
        assert primed.status == FamilyStatus.active.value

        context, _ = _routed_context(committing_db, committing_factories, unit_id=None)
        committing_db.commit()
        with committing_session_factory() as other:
            other_ctx = other.get(CatalogContext, context.id)
            other_ctx.work_family_id = family.id
            other_ctx.family_source = FamilySource.manual.value
            other_ctx.family_by = user.id
            other_ctx.family_at = dt.datetime.now(dt.UTC)
            other.commit()

        with pytest.raises(WorkFamilyError) as exc:
            archive_family(committing_db, family_id=family.id, actor_id=user.id)
        assert exc.value.code == REFUSE_ARCHIVE_WITH_LINKS
        assert exc.value.count == 1

    def test_merge_families_rereads_target_status(
        self, committing_db, committing_factories, committing_session_factory
    ):
        user = committing_factories.UserFactory.create()
        source = _active_family(
            committing_db, title=f"Перечит. слияния источник {_uid()}", unit_name=None,
            actor_id=user.id,
        )
        target = _active_family(
            committing_db, title=f"Перечит. слияния цель {_uid()}", unit_name=None,
            actor_id=user.id,
        )
        committing_db.commit()

        primed_target = committing_db.get(WorkFamily, target.id)
        assert primed_target.status == FamilyStatus.active.value

        with committing_session_factory() as other:
            other_target = other.get(WorkFamily, target.id)
            other_target.status = FamilyStatus.archived.value
            other_target.archived_at = dt.datetime.now(dt.UTC)
            other.commit()

        with pytest.raises(WorkFamilyError) as exc:
            merge_families(
                committing_db, source_family_id=source.id, target_family_id=target.id,
                actor_id=user.id,
            )
        assert exc.value.code == REFUSE_MERGE_INACTIVE
        assert exc.value.role == "target"
        assert exc.value.status == FamilyStatus.archived.value

    def test_activate_family_rereads_status_after_concurrent_archive(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Тот же приём, что `test_merge_families_rereads_target_status`
        выше: другая, полностью закоммиченная сессия архивирует семью МЕЖДУ
        первым чтением этой сессии и локом активации. Без `FOR UPDATE` +
        перечитывания активация не заметила бы архивирование и переписала бы
        статус обратно на `active`, оставив `archived_at` заполненным —
        нарушение `draft -> active -> archived`."""
        user = committing_factories.UserFactory.create()
        family = create_family(
            committing_db, title=f"Перечит. активации {_uid()}", unit_name=None,
            definition="Определение", actor_id=user.id,
        )
        committing_db.commit()

        primed = committing_db.get(WorkFamily, family.id)
        assert primed.status == FamilyStatus.draft.value

        with committing_session_factory() as other:
            archive_family(other, family_id=family.id, actor_id=user.id)
            other.commit()

        with pytest.raises(WorkFamilyError) as exc:
            activate_family(committing_db, family_id=family.id, actor_id=user.id)
        assert exc.value.code == REFUSE_ACTIVATE_NOT_DRAFT
        assert exc.value.status == FamilyStatus.archived.value


# ---------------------------------------------------------------------------
#  Гонки: три двухсессионные защиты плана + гонка «назначение против
#  архивирования» (план, Task 8, «Утверждения»; спека §2.8). Потоки
#  освобождаются и дожидаются в `finally` (задача 6, урок).
#
#  Свидетель — ТОЛЬКО `pg_blocking_pids()`
#  КОНКРЕТНОГО backend'а плюс текст его запроса (не общий счётчик по базе:
#  ассерт зелен и когда ждёт посторонний backend, `docs/pitfalls/db.md`).
#  Второй поток освобождается СРАЗУ после того, как свидетель разрешился
#  (заблокирован либо встал на СВОЮ паузу) — НЕ после `join()` первого
#  потока: если сперва ждать до 30 с окончания A и только потом освобождать
#  B, а у B в обёртке СВОЙ независимый таймаут 30 с, отсчитывающийся с
#  МОМЕНТА, когда B встала на паузу, — B успевает истечь ПРЕЖДЕ, чем до неё
#  доходит очередь (тест красил `release не пришёл вовремя`,
#  а не дедлок). `_WITNESS_TIMEOUT < _RELEASE_TIMEOUT` — инвариант, который
#  это и не даёт повториться (тот же приём, что
#  `test_context_concurrency.py`: `_RACE_WITNESS_TIMEOUT < _A_RELEASE_TIMEOUT`).
# ---------------------------------------------------------------------------

_WITNESS_TIMEOUT = 10.0
_RELEASE_TIMEOUT = 30.0
assert _WITNESS_TIMEOUT < _RELEASE_TIMEOUT


def _make_paused_after_first_call(original, pending: dict, release: dict):
    """Обёртка для `_lock_families`/`_lock_contexts`: выполняет ОРИГИНАЛЬНЫЙ
    (настоящий, блокирующий) вызов, затем — ТОЛЬКО на ПЕРВОМ вызове
    ИМЕНОВАННОГО потока (проверка `pending[name].is_set()`, ОБЩАЯ для обеих
    функций, если обе обёрнуты этим же фабричным вызовом с ОДНИМИ и теми же
    `pending`/`release` — так пауза срабатывает ровно один раз на поток,
    независимо от того, какая из двух функций вызвана первой) — сигналит
    `pending[name]` и ждёт `release[name]`. Второй и последующие вызовы того
    же потока идут БЕЗ паузы — настоящим, потенциально блокирующим запросом к
    базе (это и создаёт встречную блокировку в RED-сценах со снятым
    порядком)."""

    def _wrapped(db, ids, **kwargs):
        original(db, ids, **kwargs)
        name = threading.current_thread().name
        if name not in pending:
            return
        if not pending[name].is_set():
            pending[name].set()
            assert release[name].wait(timeout=_RELEASE_TIMEOUT), f"{name}: release не пришёл вовремя"

    return _wrapped


def _backend_blocked_once(session_factory, *, pid: int, contains: str) -> bool:
    """Один снимок: КОНКРЕТНЫЙ backend `pid` ждёт лока, и текст его запроса
    содержит `contains` — тот же приём, что
    `test_context_concurrency.py::_wait_until_backend_blocks`, но без
    собственного цикла (цикл — у вызывающих, см. ниже), чтобы совмещать это
    условие с другими (`pending[...].is_set()`) в одном опросе."""
    with session_factory() as probe:
        row = probe.execute(
            sa.text(
                "SELECT cardinality(pg_blocking_pids(:pid)) > 0 AS blocked, "
                "(SELECT query FROM pg_stat_activity WHERE pid = :pid) AS query"
            ),
            {"pid": pid},
        ).one()
        probe.rollback()
        return bool(row.blocked and contains in (row.query or ""))


def _wait_until_backend_blocks(
    session_factory, *, pid: int, contains: str, timeout: float = _WITNESS_TIMEOUT
) -> bool:
    """Цикл опроса `_backend_blocked_once` до `timeout` — используется, когда
    единственное интересующее условие — «этот backend ждёт лок»."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _backend_blocked_once(session_factory, pid=pid, contains=contains):
            return True
        time.sleep(0.05)
    return False


def _wait_paused_or_blocked(
    session_factory, pending_event: threading.Event, *, pid: int, contains: str,
    timeout: float = _WITNESS_TIMEOUT,
) -> bool:
    """Опрашивает ДВА взаимоисключающих условия — «поток встал на СВОЮ паузу»
    (`pending_event`, мутированный код с раздельными локами — реальной
    блокировки базы ещё нет) и «поток ждёт РЕАЛЬНЫЙ лок на `pid`»
    (корректный код — один совмещённый запрос блокирует целиком) — и
    выходит, как только верно любое из них, НЕ дожидаясь полного `timeout`
    (иначе тест ждал бы `_WITNESS_TIMEOUT` даже там, где ответ известен сразу
    же). Возвращает `True`, если сработало ИМЕННО блокирование лока (для
    диагностики; не фатально ни в одном случае — исход проверяется по
    данным)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pending_event.is_set():
            return False
        if _backend_blocked_once(session_factory, pid=pid, contains=contains):
            return True
        time.sleep(0.05)
    return False


def _wait_for_pid(pid_holder: dict[str, int], key: str, *, timeout: float = _WITNESS_TIMEOUT) -> bool:
    """Ждёт, пока фоновый поток не запишет свой `pg_backend_pid()` в общий
    словарь (поток пишет его ДО входа в защищаемую операцию) — та же копия,
    что `test_context_concurrency.py::_wait_for_pid`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if key in pid_holder:
            return True
        time.sleep(0.02)
    return False


def _terminate_backend(session_factory, pid: int | None) -> None:
    """Обрубает зависший backend отдельной пробной сессией — иначе упавший
    тест оставляет держателя лока живым до `TRUNCATE` следующего теста
    (используется, когда `join()` истёк, а поток всё ещё жив)."""
    if pid is None:
        return
    try:
        with session_factory() as probe:
            probe.execute(sa.text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            probe.commit()
    except Exception:  # pragma: no cover — best-effort уборка
        pass


class TestMergeVsMergeNoDeadlock:
    def test_opposite_merges_finish_without_deadlock_loser_refuses_on_reread(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Утверждения 1 и 3 плана в одной сцене: A сливает `smaller→larger`,
        B — встречно, `larger→smaller`. Порядок «по возрастанию id»
        (`_lock_families_statement`, `ORDER BY id`) держит ОБА захвата в
        одном и том же порядке независимо от того, кто из семей у кого
        источник — деталь снимается RED-мутацией ниже. Пауза — ПОСЛЕ
        ПЕРВОГО вызова `_lock_families` каждого потока (`_make_paused_after_first_call`):
        для корректного кода это единственный вызов (обе семьи разом), и B
        застревает на РЕАЛЬНОМ локе базы, ожидая коммита A; после коммита A
        —её цель `smaller` уже архивна, перечитывание REFUSE_MERGE_INACTIVE."""
        user = committing_factories.UserFactory.create()
        fam1 = _active_family(
            committing_db, title=f"MvM-1 {_uid()}", unit_name="M2", actor_id=user.id
        )
        fam2 = _active_family(
            committing_db, title=f"MvM-2 {_uid()}", unit_name="M2", actor_id=user.id
        )
        committing_db.commit()
        smaller_id, larger_id = sorted([fam1.id, fam2.id])

        pending = {"A": threading.Event(), "B": threading.Event()}
        release = {"A": threading.Event(), "B": threading.Event()}
        errors: list[str] = []
        results: dict[str, object] = {}
        pid_holder: dict[str, int] = {}

        original_lock_families = work_families_module._lock_families
        patched = _make_paused_after_first_call(original_lock_families, pending, release)

        def thread_a():
            try:
                with committing_session_factory() as dbA:
                    pid_holder["a"] = dbA.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    merge_families(
                        dbA, source_family_id=smaller_id, target_family_id=larger_id,
                        actor_id=user.id,
                    )
                    dbA.commit()
                    results["a"] = "merged"
            except WorkFamilyError as exc:
                results["a_code"] = exc.code
            except Exception as exc:  # noqa: BLE001 — ловим DeadlockDetected и прочее
                errors.append(f"A: {type(exc).__name__}: {exc}")

        def thread_b():
            try:
                with committing_session_factory() as dbB:
                    pid_holder["b"] = dbB.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    merge_families(
                        dbB, source_family_id=larger_id, target_family_id=smaller_id,
                        actor_id=user.id,
                    )
                    dbB.commit()
                    results["b"] = "merged"
            except WorkFamilyError as exc:
                results["b_code"] = exc.code
            except Exception as exc:  # noqa: BLE001
                errors.append(f"B: {type(exc).__name__}: {exc}")

        ta = threading.Thread(target=thread_a, name="A", daemon=True)
        tb = threading.Thread(target=thread_b, name="B", daemon=True)
        b_blocked_on_db = False
        try:
            with mock.patch.object(work_families_module, "_lock_families", side_effect=patched):
                ta.start()
                assert pending["A"].wait(timeout=_WITNESS_TIMEOUT), (
                    "A не встала на паузу после первого лока"
                )
                tb.start()
                assert _wait_for_pid(pid_holder, "b", timeout=_WITNESS_TIMEOUT)

                # Свидетель — ОДИН опрос двух исключающих условий, выходит
                # СРАЗУ по любому из них (не ждёт полный _WITNESS_TIMEOUT):
                # корректный код — B ждёт РЕАЛЬНЫЙ лок (True); мутированный
                # (два раздельных лока) — B встала на СВОЮ паузу (False),
                # реальной блокировки ещё нет. Не фатален — обе ветки законны
                # на этом шаге, RED красит исход ниже, не свидетеля.
                b_blocked_on_db = _wait_paused_or_blocked(
                    committing_session_factory, pending["B"],
                    pid=pid_holder["b"], contains="work_families",
                )

                # Освобождаем ОБА потока СРАЗУ после разрешения свидетеля —
                # НЕ после join() первого (см. докстроку раздела).
                # `Event.set()` на уже установленном `Event` — не-оп; если B
                # ещё не дошла до своей паузы (застряла в реальном локе),
                # `release["B"].set()` просто ждёт своего часа безвредно.
                release["A"].set()
                release["B"].set()
                ta.join(timeout=_RELEASE_TIMEOUT)
                tb.join(timeout=_RELEASE_TIMEOUT)
        finally:
            release["A"].set()
            release["B"].set()
            ta.join(timeout=_RELEASE_TIMEOUT)
            tb.join(timeout=_RELEASE_TIMEOUT)
            if ta.is_alive():
                _terminate_backend(committing_session_factory, pid_holder.get("a"))
            if tb.is_alive():
                _terminate_backend(committing_session_factory, pid_holder.get("b"))

        # Исход — ПЕРВЫМ: ни одна сессия не получила DeadlockDetected, обе
        # закончили (RED снятия сортировки красит именно это: `errors`
        # несёт `OperationalError`/`DeadlockDetected`, либо поток остаётся
        # `is_alive()` — зависание, пойманное таймаутом join).
        assert not errors, errors
        assert not ta.is_alive()
        assert not tb.is_alive()

        # Механизм — ВТОРЫМ: ОДНА сессия успехом, ВТОРАЯ — доменным отказом
        # по перечитанному (не обратным слиянием, RED снятия expire_all
        # красит именно это: `results == {'a': 'merged', 'b': 'merged'}`).
        assert results.get("a") == "merged"
        assert results.get("b_code") == REFUSE_MERGE_INACTIVE

        # Свидетель — ТРЕТЬИМ, диагностика: в корректном коде B реально ждёт
        # лок A (не только совпадение по расписанию).
        assert b_blocked_on_db, "B не встала в очередь на лок, который держит A"


class TestMergeVsAssignNoDeadlock:
    def test_merge_and_assign_on_the_same_family_finish_without_deadlock(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Утверждение 2 плана: слияние (`other → shared`) и назначение
        (`assign_family(context=C, family_id=shared)`) на ОДНУ семью
        (`shared`) не дают дедлока — общий порядок «семья раньше контекста»
        у ОБЕИХ операций. `C` дополнительно — членство `other` (кандидат
        слияния): единственный сценарий, где у операций есть ВТОРОЙ общий
        ресурс (контекст), без которого дедлок в принципе невозможен."""
        user = committing_factories.UserFactory.create()
        pcs = _unit_id(committing_db, "PCS")
        shared = _active_family(
            committing_db, title=f"MvA-shared {_uid()}", unit_name="PCS", actor_id=user.id
        )
        other = _active_family(
            committing_db, title=f"MvA-other {_uid()}", unit_name="PCS", actor_id=user.id
        )
        context, _ = _routed_context(committing_db, committing_factories, unit_id=pcs)
        committing_db.commit()
        assign_family(committing_db, context_id=context.id, family_id=other.id, actor_id=user.id)
        committing_db.commit()

        pending = {"assign": threading.Event(), "merge": threading.Event()}
        release = {"assign": threading.Event(), "merge": threading.Event()}
        errors: list[str] = []
        results: dict[str, object] = {}
        pid_holder: dict[str, int] = {}

        original_lock_families = work_families_module._lock_families
        original_lock_contexts = work_families_module._lock_contexts
        patched_families = _make_paused_after_first_call(original_lock_families, pending, release)
        patched_contexts = _make_paused_after_first_call(original_lock_contexts, pending, release)

        def thread_assign():
            try:
                with committing_session_factory() as db:
                    pid_holder["assign"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    assign_family(
                        db, context_id=context.id, family_id=shared.id, actor_id=user.id
                    )
                    db.commit()
                    results["assign"] = "assigned"
            except WorkFamilyError as exc:
                results["assign_code"] = exc.code
            except Exception as exc:  # noqa: BLE001 — ловим DeadlockDetected и прочее
                errors.append(f"assign: {type(exc).__name__}: {exc}")

        def thread_merge():
            try:
                with committing_session_factory() as db:
                    pid_holder["merge"] = db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    moved = merge_families(
                        db, source_family_id=other.id, target_family_id=shared.id,
                        actor_id=user.id,
                    )
                    db.commit()
                    results["merge"] = moved
            except WorkFamilyError as exc:
                results["merge_code"] = exc.code
            except Exception as exc:  # noqa: BLE001
                errors.append(f"merge: {type(exc).__name__}: {exc}")

        t_assign = threading.Thread(target=thread_assign, name="assign", daemon=True)
        t_merge = threading.Thread(target=thread_merge, name="merge", daemon=True)
        merge_blocked_on_db = False
        try:
            with mock.patch.object(
                work_families_module, "_lock_families", side_effect=patched_families
            ), mock.patch.object(
                work_families_module, "_lock_contexts", side_effect=patched_contexts
            ):
                t_assign.start()
                assert pending["assign"].wait(timeout=_WITNESS_TIMEOUT), "assign не встала на паузу"

                t_merge.start()
                assert _wait_for_pid(pid_holder, "merge", timeout=_WITNESS_TIMEOUT)

                # Тот же совмещённый свидетель, что в TestMergeVsMergeNoDeadlock
                # (см. докстроку раздела) — не фатален, освобождение НЕ ждёт
                # его полного таймаута и не сериализовано через join().
                merge_blocked_on_db = _wait_paused_or_blocked(
                    committing_session_factory, pending["merge"],
                    pid=pid_holder["merge"], contains="work_families",
                )

                release["assign"].set()
                release["merge"].set()
                t_assign.join(timeout=_RELEASE_TIMEOUT)
                t_merge.join(timeout=_RELEASE_TIMEOUT)
        finally:
            release["assign"].set()
            release["merge"].set()
            t_assign.join(timeout=_RELEASE_TIMEOUT)
            t_merge.join(timeout=_RELEASE_TIMEOUT)
            if t_assign.is_alive():
                _terminate_backend(committing_session_factory, pid_holder.get("assign"))
            if t_merge.is_alive():
                _terminate_backend(committing_session_factory, pid_holder.get("merge"))

        assert not errors, errors
        assert not t_assign.is_alive()
        assert not t_merge.is_alive()
        assert "assign" in results or "assign_code" in results
        assert "merge" in results or "merge_code" in results
        assert merge_blocked_on_db, "merge не встала в очередь на лок, который держит assign"


class TestAssignVsArchiveRace:
    def test_assign_wins_the_race_archive_refuses_on_reread(
        self, committing_db, committing_factories, committing_session_factory
    ):
        """Гонка сверх трёх защит слияния (план, Task 8, «Утверждения»,
        последний абзац): B (`assign_family`) держит `FOR SHARE` на семью,
        пока A (`archive_family`) ждёт `FOR UPDATE`. Освобождённая B успевает
        записать привязку и закоммитить; A, получив лок, ПЕРЕЧИТЫВАЕТ число
        привязок и отказывает — архивной семьи с живой привязкой не
        возникает.

        Пауза B — на событии `before_flush` СЕССИИ B (ПОСЛЕ
        собственных проверок статуса/единицы, КОГДА `context.work_family_id`
        уже присвоен в Python, но ДО того, как `UPDATE` реально уйдёт в
        Postgres), а не на `record_event`. Важность момента: сам `UPDATE`
        `catalog_contexts.work_family_id`, ссылающийся на семью по внешнему
        ключу, заставляет PostgreSQL взять НЕЯВНЫЙ `FOR KEY SHARE` на
        родительскую строку `work_families` — это системное ограничение
        целостности, а не код этого модуля, и оно КОНФЛИКТУЕТ с `FOR UPDATE`
        архивирования НЕЗАВИСИМО от явного `_lock_families`. Первая версия
        паузы (`record_event`, ПОСЛЕ `db.flush()`) поэтому не давала снять
        RED: `UPDATE` уже ушёл, неявный FK-лок уже взят, и A всё равно
        блокировалась и отказывала — RED со снятым `FOR SHARE` не
        воспроизводился.
        Пауза ДО `flush()` — единственная точка, где B ещё НЕ держит НИ
        явного, НИ неявного (FK) лока: A (без `FOR SHARE`) успевает
        целиком — лок, `COUNT=0`, архивирование, коммит — прежде чем `UPDATE`
        B вообще отправлен; когда B наконец коммитит, FK лишь проверяет, что
        строка `work_families` СУЩЕСТВУЕТ (она существует — архивная, но не
        удалена), и пропускает запись."""
        user = committing_factories.UserFactory.create()
        family = _active_family(
            committing_db, title=f"AvA {_uid()}", unit_name=None, actor_id=user.id
        )
        context, _ = _routed_context(committing_db, committing_factories, unit_id=None)
        committing_db.commit()

        b_paused = threading.Event()
        b_release = threading.Event()
        errors: list[str] = []
        pid_holder: dict[str, int] = {}
        a_result: dict[str, object] = {}
        b_result: dict[str, object] = {}

        def thread_b():
            try:
                with committing_session_factory() as dbB:
                    pid_holder["b"] = dbB.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()

                    def _pause_before_flush(session, flush_context, instances):
                        b_paused.set()
                        assert b_release.wait(timeout=_RELEASE_TIMEOUT), (
                            "release B не пришёл вовремя"
                        )

                    event.listen(dbB, "before_flush", _pause_before_flush)
                    try:
                        assign_family(
                            dbB, context_id=context.id, family_id=family.id, actor_id=user.id
                        )
                    finally:
                        event.remove(dbB, "before_flush", _pause_before_flush)
                    dbB.commit()
                    b_result["assigned"] = True
            except Exception as exc:  # noqa: BLE001
                errors.append(f"B: {type(exc).__name__}: {exc}")

        def thread_a():
            try:
                with committing_session_factory() as dbA:
                    pid_holder["a"] = dbA.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
                    archive_family(dbA, family_id=family.id, actor_id=user.id)
                    dbA.commit()
                    a_result["archived"] = True
            except WorkFamilyError as exc:
                a_result["error_code"] = exc.code
            except Exception as exc:  # noqa: BLE001
                errors.append(f"A: {type(exc).__name__}: {exc}")

        tb = threading.Thread(target=thread_b, name="B", daemon=True)
        ta = threading.Thread(target=thread_a, name="A", daemon=True)
        a_blocked_on_b = False
        try:
            tb.start()
            assert b_paused.wait(timeout=_WITNESS_TIMEOUT), "B не встала на паузу"

            ta.start()
            assert _wait_for_pid(pid_holder, "a", timeout=_WITNESS_TIMEOUT)
            # Свидетель — pg_blocking_pids() КОНКРЕТНОГО backend'а A плюс
            # текст его запроса, не общий счётчик. НЕ фатален
            # (записывается, не форсирует исход) — со снятым FOR SHARE
            # A НЕ блокируется вовсе, и это законный, ожидаемый RED-путь.
            a_blocked_on_b = _wait_until_backend_blocks(
                committing_session_factory, pid=pid_holder["a"], contains="work_families",
                timeout=_WITNESS_TIMEOUT,
            )

            b_release.set()
            tb.join(timeout=_RELEASE_TIMEOUT)
            ta.join(timeout=_RELEASE_TIMEOUT)
        finally:
            b_release.set()
            tb.join(timeout=_RELEASE_TIMEOUT)
            ta.join(timeout=_RELEASE_TIMEOUT)
            if tb.is_alive():
                _terminate_backend(committing_session_factory, pid_holder.get("b"))
            if ta.is_alive():
                _terminate_backend(committing_session_factory, pid_holder.get("a"))

        assert not errors, errors
        assert not tb.is_alive()
        assert not ta.is_alive()

        # Исход — ПЕРВЫМ: прямая проверка инварианта по данным. RED
        # снятия `FOR SHARE` красит ИМЕННО это — архивная семья с живой
        # привязкой становится физически достижимой.
        committing_db.refresh(family)
        link_count = committing_db.execute(
            sa.select(sa.func.count())
            .select_from(CatalogContext)
            .where(CatalogContext.work_family_id == family.id)
        ).scalar_one()
        assert not (family.status == FamilyStatus.archived.value and link_count > 0), (
            f"invariant violated: archived family with a live link "
            f"(status={family.status!r}, link_count={link_count})"
        )

        # Механизм — ВТОРЫМ, для корректного (незамутированного) кода.
        assert b_result.get("assigned") is True
        assert a_result.get("error_code") == REFUSE_ARCHIVE_WITH_LINKS

        # Свидетель — ТРЕТЬИМ, диагностика.
        assert a_blocked_on_b, "A (архивирование) не встала в очередь на лок, который держит B"
