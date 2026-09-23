"""Жизненный цикл семей работ: seed, создание, правка, активация (план
`docs/superpowers/plans/2026-09-22-catalog-families.md`, задача 7; спека
`docs/superpowers/specs/2026-09-22-catalog-families-design.md` §1.14, §2.3,
§2.7, §2.14).

Seed-файл `backend/seeds/work_families_initial.json` собран заново из
`tasks/catalog-pilot-2026-09-18/razmetka-101.xlsx` скриптом
`tasks/catalog-families-work/build_work_families_seed.py` (файл вне гита,
скрипт — тоже; команда запуска и контроль 42/6/36 — в отчёте
`tasks/catalog-families-work/task7-executor.md`).

42/6/36 — НЕЗАВИСИМЫЕ литералы теста, не `len()` того же файла в обе
стороны (план, задача 7, «Проверка»).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from click.testing import CliRunner
from sqlalchemy import event

import cli
from models import FamilyStatus, SemanticEvent, WorkFamily
from services.unit_resolution import UnitResolver
from services.work_families import (
    REFUSE_ACTIVATE_NOT_DRAFT,
    REFUSE_ACTIVATE_WITHOUT_DEFINITION,
    REFUSE_FAMILY_NOT_FOUND,
    REFUSE_UNKNOWN_UNIT,
    REFUSE_UPDATE_ARCHIVED,
    SEED_PATH,
    SeedReport,
    WorkFamilyError,
    activate_family,
    create_family,
    load_seed,
    update_family,
)
from tests.integration.test_schema_constraints import rejected

pytestmark = pytest.mark.integration

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
    """Главный вход брифа: переименование семьи, повторный seed, второго
    черновика нет.

    Что держит каждая часть (MINOR-3, ревью задачи 7 Round 2— уточнение
    докстроки, чтобы она называла ровно то, что предъявляет тест):

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
    """IMPORTANT-1 (ревью задачи 7 Round 2): `load_seed` не молчит на
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
    """MINOR-2 (ревью задачи 7 Round 2): `load_seed` нормализует
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


# ---------------------------------------------------------------------------
#  update_family: правка, `changed`, запрет на archived.
# ---------------------------------------------------------------------------


def test_update_family_rename_records_single_changed_field(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Старое имя", unit_name=None, definition=None, actor_id=user.id,
    )
    updated = update_family(
        db_session, family_id=fam.id, title="Новое имя", definition=None, actor_id=user.id,
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


def test_update_family_allowed_when_active(db_session, factories):
    user = factories.UserFactory.create()
    fam = create_family(
        db_session, title="Актив", unit_name=None, definition="Определение",
        actor_id=user.id,
    )
    activate_family(db_session, family_id=fam.id, actor_id=user.id)
    updated = update_family(
        db_session, family_id=fam.id, title="Актив (правка)", definition=None,
        actor_id=user.id,
    )
    assert updated.title == "Актив (правка)"


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
            db_session, family_id=fam.id, title="Новое имя", definition=None,
            actor_id=user.id,
        )
    assert exc.value.code == REFUSE_UPDATE_ARCHIVED


def test_update_family_not_found_refuses(db_session, factories):
    user = factories.UserFactory.create()
    with pytest.raises(WorkFamilyError) as exc:
        update_family(
            db_session, family_id=999_999_999, title="x", definition=None,
            actor_id=user.id,
        )
    assert exc.value.code == REFUSE_FAMILY_NOT_FOUND


# ---------------------------------------------------------------------------
#  activate_family: без определения — отказ (до базы), с определением — успех.
# ---------------------------------------------------------------------------


def test_activate_without_definition_refuses_before_any_flush(db_session, factories):
    """`REFUSE_ACTIVATE_WITHOUT_DEFINITION` проверяется в коде ДО базы: отказ
    обязан произойти БЕЗ единого `flush` (план, задача 7, «Решения
    оркестратора»; независимый счётчик, никаких sleep)."""
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
    ниже (IMPORTANT-3, ревью задачи 7 Round 2: этот тест был ложно-зелёным
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
    """IMPORTANT-3 (ревью задачи 7 Round 2): вход, который реально проверяет
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
#  Активные дубли имени × единицы — IntegrityError; в draft/archived — проходят.
# ---------------------------------------------------------------------------


def test_activate_duplicate_active_name_and_unit_rejected(db_session, factories):
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
    with rejected(db_session, contains='"uq_work_families_active_name_unit"'):
        activate_family(db_session, family_id=f2.id, actor_id=user.id)


def test_same_name_and_unit_in_draft_pass(db_session, factories):
    user = factories.UserFactory.create()
    title = f"Дублирующее имя (draft) {_uid()}"
    f1 = create_family(db_session, title=title, unit_name="M2", definition=None, actor_id=user.id)
    f2 = create_family(db_session, title=title, unit_name="M2", definition=None, actor_id=user.id)
    assert f1.id != f2.id
    assert f1.status == f2.status == FamilyStatus.draft.value


def test_same_name_and_unit_in_archived_pass(db_session, factories):
    """Архивирование ставится прямой правкой — операции архивирования в
    задаче 7 нет (план, задача 7, «Решения оркестратора»)."""
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
    """H:aa1a09b8 / IMPORTANT-2 (ревью задачи 7 Round 2): счастливый путь
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
