"""Материал запросов заданий `family_schema` и `context_values` на реальных
данных и `render_request_for` (спека `2026-10-02-catalog-variants-design.md`
§2.3, §2.6).

Чистые функции рендера, `top_paths`, оси хэша, приватность тел — в
`tests/unit/test_work_variants_request.py`. Здесь — то, что нельзя проверить без
базы: состав имён семьи, схема контекста, пути по всем членствам, постоянное
число запросов и выбор предмета в `render_request_for`.

Помощники цепочки «семья → версия схемы → параметр → значение» и строки очереди
импортируются из `test_work_variants_schema.py` и
`test_semantic_queue_schema.py`; помощники маршрутизации — локальные копии
(наборы помощников тестов проекта друг у друга их не импортируют).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from config import Settings
from models import (
    CatalogContext,
    ContextBucket,
    ContextMember,
    MembershipState,
    PositionItem,
    SemanticJob,
    SemanticJobKind,
    SuggestionDecision,
)
from services.context_routing import route_position
from services.semantic_request import load_request_material, render_context_request
from services.unit_resolution import UnitResolver
from services.variant_request import (
    MAX_PATHS,
    SubjectNotRenderable,
    load_schema_material,
    load_values_material,
    render_request_for,
    render_schema_request,
    render_values_request,
)
from tests.integration.test_work_variants_schema import (
    _family,
    _param,
    _schema,
    _value,
)

pytestmark = pytest.mark.integration


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, SECRET_KEY="x" * 32, **overrides)


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


# ---------------------------------------------------------------------------
#  Помощники: контекст с заданным наименованием строки каталога
# ---------------------------------------------------------------------------

def _unit_id(db, code):
    return UnitResolver(db).resolve(code).unit_id


def _bucket(db, factories, title, *, unit_id=None) -> ContextBucket:
    cp = factories.CatalogPositionFactory.create(standard_job_title=title, unit_id=unit_id)
    bucket = ContextBucket(catalog_position_id=cp.id, work_category_id=None)
    db.add(bucket)
    db.flush()
    return bucket


def _context(
    db, factories, title, *, work=None, pending=None, archived=False, unit_id=None, bucket=None
) -> CatalogContext:
    """Контекст с наименованием `title`; семья и ожидание ставятся так, чтобы
    пройти CHECK провенанса (источник `manual`, автор — пользователь)."""
    from tests.integration.test_semantic_queue_schema import _context as plain_context

    values: dict = {}
    if work is not None:
        values.update(
            work_family_id=work.id, family_source="manual",
            family_by=factories.UserFactory.create().id, family_at=_now(),
        )
    if pending is not None:
        values.update(
            pending_family_id=pending.id, pending_family_source="manual",
            pending_by=factories.UserFactory.create().id, pending_at=_now(),
        )
    if archived:
        values.update(archived_at=_now())
    bucket = bucket or _bucket(db, factories, title, unit_id=unit_id)
    return plain_context(db, factories, bucket=bucket, **values)


def _publish(db, factories, context, family, decision=None):
    """Опубликованное предложение семьи `family` на контекст с заданным
    решением (`None` — решения нет)."""
    from tests.integration.test_semantic_queue_schema import _suggestion

    values: dict = dict(family_id=family.id, new_family_name=None, is_published=True)
    if decision is not None:
        values.update(decision=decision, decided_at=_now())
        if not decision.startswith("auto_"):
            values["decided_by"] = factories.UserFactory.create().id
    return _suggestion(db, factories, context=context, **values)


# ---------------------------------------------------------------------------
#  Состав имён family_schema (спека §2.3)
# ---------------------------------------------------------------------------

#: Случаи, при которых имя входит в схему семьи F: (имя случая, что у контекста,
#: решение опубликованного предложения F или `None`, нет предложения вовсе —
#: пустая строка).
_INCLUDED = [
    ("bound_no_suggestion", dict(work="F"), ""),
    ("bound_with_open_suggestion", dict(work="F"), None),
    ("pending_no_suggestion", dict(pending="F"), ""),
    ("unassigned_open_suggestion", dict(), None),
    ("accepted_and_bound", dict(work="F"), SuggestionDecision.accepted.value),
    ("auto_accepted_and_bound", dict(work="F"), SuggestionDecision.auto_accepted.value),
    ("auto_pending_and_pending", dict(pending="F"), SuggestionDecision.auto_pending.value),
    ("accepted_pending_and_pending", dict(pending="F"), SuggestionDecision.accepted_pending.value),
]
_EXCLUDED = [
    ("unassigned_other_family", dict(), SuggestionDecision.other_family.value),
    ("unassigned_family_created", dict(), SuggestionDecision.family_created.value),
    ("unassigned_rejected", dict(), SuggestionDecision.rejected.value),
    ("unassigned_auto_superseded", dict(), SuggestionDecision.auto_superseded.value),
    ("unassigned_accepted", dict(), SuggestionDecision.accepted.value),
    ("unassigned_auto_accepted", dict(), SuggestionDecision.auto_accepted.value),
    ("unassigned_auto_pending", dict(), SuggestionDecision.auto_pending.value),
    ("unassigned_accepted_pending", dict(), SuggestionDecision.accepted_pending.value),
    ("accepted_but_reassigned_to_other", dict(work="G"), SuggestionDecision.accepted.value),
    ("open_suggestion_but_bound_to_other", dict(work="G"), None),
    ("open_suggestion_but_pending_to_other", dict(pending="G"), None),
    ("archived_bound", dict(work="F", archived=True), ""),
    ("archived_open_suggestion", dict(archived=True), None),
    ("archived_pending", dict(pending="F", archived=True), ""),
    ("no_family_no_suggestion", dict(), ""),
]


class TestSchemaNames:
    def _build(self, db_session, factories, spec, decision):
        family, other = _family(db_session), _family(db_session)
        families = {"F": family, "G": other}
        title = f"Наименование {_uid()}"
        kwargs = {
            key: (families[value] if key in ("work", "pending") else value)
            for key, value in spec.items()
        }
        context = _context(db_session, factories, title, **kwargs)
        if decision != "":
            _publish(db_session, factories, context, family, decision)
        return family, title

    @pytest.mark.parametrize(("case", "spec", "decision"), _INCLUDED, ids=[c[0] for c in _INCLUDED])
    def test_name_is_included(self, db_session, factories, case, spec, decision):
        family, title = self._build(db_session, factories, spec, decision)
        assert load_schema_material(db_session, family.id, 1).names == (title,)

    @pytest.mark.parametrize(("case", "spec", "decision"), _EXCLUDED, ids=[c[0] for c in _EXCLUDED])
    def test_name_is_excluded(self, db_session, factories, case, spec, decision):
        family, _title = self._build(db_session, factories, spec, decision)
        assert load_schema_material(db_session, family.id, 1).names == ()

    def test_unpublished_open_suggestion_is_excluded(self, db_session, factories):
        family = _family(db_session)
        context = _context(db_session, factories, f"Строка {_uid()}")
        from tests.integration.test_semantic_queue_schema import _suggestion

        _suggestion(db_session, factories, context=context, family_id=family.id,
                    new_family_name=None, is_published=False)
        assert load_schema_material(db_session, family.id, 1).names == ()

    def test_open_suggestion_of_another_family_is_excluded(self, db_session, factories):
        family, other = _family(db_session), _family(db_session)
        context = _context(db_session, factories, f"Строка {_uid()}")
        _publish(db_session, factories, context, other, None)
        assert load_schema_material(db_session, family.id, 1).names == ()
        assert len(load_schema_material(db_session, other.id, 1).names) == 1

    def test_open_suggestion_of_one_context_does_not_pull_in_another(
        self, db_session, factories
    ):
        # Ветка «открытое предложение» обязана быть связана с САМИМ контекстом:
        # без связи любое открытое предложение семьи втянуло бы в её схему все
        # контексты без семьи и без ожидания.
        family = _family(db_session)
        title = f"С предложением {_uid()}"
        with_suggestion = _context(db_session, factories, title)
        _context(db_session, factories, f"Без предложения {_uid()}")
        _publish(db_session, factories, with_suggestion, family, None)
        assert load_schema_material(db_session, family.id, 1).names == (title,)

    def test_names_are_sorted_without_repeats(self, db_session, factories):
        family = _family(db_session)
        m2, m3 = _unit_id(db_session, "M2"), _unit_id(db_session, "M3")
        for title, unit_id in (("Яблоня", m2), ("Береза", m2), ("Береза", m3), ("Ель", m2)):
            _context(db_session, factories, title, work=family, unit_id=unit_id)
        assert load_schema_material(db_session, family.id, 1).names == ("Береза", "Ель", "Яблоня")

    def test_same_row_name_in_two_contexts_of_one_bucket_family_is_listed_once(
        self, db_session, factories
    ):
        family = _family(db_session)
        bucket = _bucket(db_session, factories, "Одна строка")
        _context(db_session, factories, "Одна строка", work=family, bucket=bucket)
        _context(db_session, factories, "Одна строка", pending=family, bucket=bucket)
        assert load_schema_material(db_session, family.id, 1).names == ("Одна строка",)

    def test_material_carries_family_fields_and_schema_id(self, db_session, factories):
        unit_id = _unit_id(db_session, "M2")
        family = _family(db_session, title="Устройство полов", definition="Пол по грунту",
                          unit_id=unit_id)
        material = load_schema_material(db_session, family.id, 4242)
        assert (material.family_id, material.schema_id) == (family.id, 4242)
        assert material.title == "Устройство полов"
        assert material.definition == "Пол по грунту"
        assert material.unit_code == "M2"
        assert material.names == ()

    def test_family_without_unit_has_no_unit_code(self, db_session, factories):
        family = _family(db_session, unit_id=None)
        assert load_schema_material(db_session, family.id, 1).unit_code is None

    def test_missing_family_is_not_renderable(self, db_session):
        with pytest.raises(SubjectNotRenderable):
            load_schema_material(db_session, 987654321, 1)


# ---------------------------------------------------------------------------
#  Материал context_values
# ---------------------------------------------------------------------------

def _proposal(factories):
    estimate = factories.EstimateFactory.create()
    lot = factories.LotFactory.create(estimate=estimate)
    return factories.ProposalFactory.create(lot=lot)


def _chain_context(db, factories, *, title, path_specs, unit_id=None):
    """Один контекст с несколькими членствами. `path_specs` — список `(titles,
    count)`, `titles` — заголовки от корня к листу; `titles=()` — членство без
    раздела. Возвращает `(context_id, position_ids по порядку path_specs)`."""
    cp = factories.CatalogPositionFactory.create(standard_job_title=title, unit_id=unit_id)
    proposal = _proposal(factories)
    context_id = None
    position_ids: list[list[int]] = []
    for idx, (titles, count) in enumerate(path_specs):
        parent_id = None
        for chapter_title in titles:
            chapter = factories.PositionItemFactory.create(
                proposal=proposal, is_chapter=True, job_title_in_proposal=chapter_title,
                chapter_item_id=parent_id,
            )
            parent_id = chapter.id
        ids = []
        for j in range(count):
            position = factories.PositionItemFactory.create(
                proposal=proposal, is_chapter=False, job_title_in_proposal=f"Позиция {idx}-{j}",
                chapter_item_id=parent_id, catalog_position_id=cp.id,
            )
            context_id = route_position(db, position_item_id=position.id).context_id
            ids.append(position.id)
        position_ids.append(ids)
    assert context_id is not None
    return context_id, position_ids


def _bind(db, factories, context_id, family=None, pending=None):
    # Автор создаётся ДО правки контекста: фабрика сбрасывает сессию, и
    # полузаполненный провенанс упал бы на промежуточном `flush`.
    author_id = factories.UserFactory.create().id
    context = db.get(CatalogContext, context_id)
    if family is not None:
        context.work_family_id = family.id
        context.family_source = "manual"
        context.family_by = author_id
        context.family_at = _now()
    if pending is not None:
        context.pending_family_id = pending.id
        context.pending_family_source = "manual"
        context.pending_by = author_id
        context.pending_at = _now()
    db.flush()
    return context


def _frozen_schema(db, factories, family, params):
    """Текущая версия с параметрами `params = [(ordinal, name, [значения])]`;
    значения заводятся в порядке списка (по возрастанию `id`)."""
    schema = _schema(db, factories, family)
    for ordinal, name, values in params:
        parameter = _param(db, schema, ordinal, name)
        for value in values:
            _value(db, parameter, value)
    return schema


class TestValuesMaterialPaths:
    def test_top_ten_paths_by_frequency_then_lexicographic_over_all_memberships(
        self, db_session, factories
    ):
        family = _family(db_session)
        counts = {i: 1 for i in range(1, 13)}
        counts.update({12: 3, 5: 2, 6: 2})
        specs = [(("Секция", f"Р{i:02d}"), counts[i]) for i in range(1, 13)]
        context_id, position_ids = _chain_context(
            db_session, factories, title="Стяжка", path_specs=specs
        )
        # Путь с наибольшей частотой целиком в устаревших членствах: пути
        # считаются по ВСЕМ членствам независимо от состояния.
        db_session.execute(
            sa.update(ContextMember)
            .where(ContextMember.position_item_id.in_(position_ids[11]))
            .values(membership_state=MembershipState.STALE.value)
        )
        _bind(db_session, factories, context_id, family=family)
        _frozen_schema(db_session, factories, family, [(1, "Толщина", ["50 мм"])])

        paths = load_values_material(db_session, [context_id])[context_id].paths

        assert len(paths) == MAX_PATHS == 10
        expected_ids = [12, 5, 6, 1, 2, 3, 4, 7, 8, 9]
        assert paths == tuple(f"Секция / Р{i:02d}" for i in expected_ids)

    def test_membership_without_chapter_gives_no_path(self, db_session, factories):
        family = _family(db_session)
        context_id, _ = _chain_context(
            db_session, factories, title="Без раздела", path_specs=[((), 3)]
        )
        _bind(db_session, factories, context_id, family=family)
        _frozen_schema(db_session, factories, family, [])
        assert load_values_material(db_session, [context_id])[context_id].paths == ()

    def test_title_unit_article_and_paths_agree_with_request_material(
        self, db_session, factories
    ):
        family = _family(db_session)
        unit_id = _unit_id(db_session, "M2")
        context_id, _ = _chain_context(
            db_session, factories, title="Работа А", unit_id=unit_id,
            path_specs=[(("Корень", "Лист"), 2)],
        )
        _bind(db_session, factories, context_id, family=family)
        _frozen_schema(db_session, factories, family, [(1, "Тип", ["А"])])
        request = load_request_material(db_session, [context_id])[context_id]
        material = load_values_material(db_session, [context_id])[context_id]
        assert (material.title, material.unit_code, material.article) == (
            request.title, request.unit_code, request.article,
        )
        assert material.paths == ("Корень / Лист",)
        assert material.context_id == context_id


class TestValuesMaterialCyclicChapters:
    def _cyclic(self, db_session, factories):
        family = _family(db_session)
        _frozen_schema(db_session, factories, family, [(1, "Тип", ["а"])])
        context_id, positions = _chain_context(
            db_session, factories, title=f"Цикл {_uid()}",
            path_specs=[(("Внешний",), 1), (("Внутренний",), 1), (("Обычный",), 2)],
        )
        _bind(db_session, factories, context_id, family=family)
        outer = db_session.get(PositionItem, positions[0][0]).chapter_item_id
        inner = db_session.get(PositionItem, positions[1][0]).chapter_item_id
        db_session.get(PositionItem, outer).chapter_item_id = inner
        db_session.get(PositionItem, inner).chapter_item_id = outer
        db_session.flush()
        db_session.expire_all()
        return context_id

    def test_context_with_one_cyclic_membership_is_absent(self, db_session, factories):
        context_id = self._cyclic(db_session, factories)
        assert load_request_material(db_session, [context_id])[context_id].path_broken is True
        assert context_id not in load_values_material(db_session, [context_id])

    def test_render_request_for_such_context_is_not_renderable(self, db_session, factories):
        context_id = self._cyclic(db_session, factories)
        job = SemanticJob(
            kind=SemanticJobKind.context_values.value, context_id=context_id, schema_id=1,
            paths_hash="x",
        )
        with pytest.raises(SubjectNotRenderable):
            render_request_for(db_session, job, settings=_settings())

    def test_a_good_context_next_to_the_cyclic_one_keeps_its_material(self, db_session, factories):
        cyclic_id = self._cyclic(db_session, factories)
        family = _family(db_session)
        _frozen_schema(db_session, factories, family, [(1, "Тип", ["а"])])
        good_id, _ = _chain_context(
            db_session, factories, title=f"Норма {_uid()}", path_specs=[(("Пол",), 1)]
        )
        _bind(db_session, factories, good_id, family=family)
        result = load_values_material(db_session, [cyclic_id, good_id])
        assert set(result) == {good_id}
        assert result[good_id].paths == ("Пол",)


class TestValuesMaterialSchema:
    def _context_in(self, db_session, factories, family, title="Строка"):
        context_id, _ = _chain_context(
            db_session, factories, title=f"{title} {_uid()}", path_specs=[((), 1)]
        )
        _bind(db_session, factories, context_id, family=family)
        return context_id

    def test_parameters_by_ordinal_values_by_id_merged_excluded(self, db_session, factories):
        family = _family(db_session)
        schema = _schema(db_session, factories, family)
        second = _param(db_session, schema, 2, "Материал")
        first = _param(db_session, schema, 1, "Толщина")
        for text in ("в", "а", "б"):
            _value(db_session, second, text)
        target = _value(db_session, first, "100 мм")
        _value(db_session, first, "50 мм")
        merged = _value(db_session, first, "10 см")
        merged.merged_into_id = target.id
        db_session.flush()
        context_id = self._context_in(db_session, factories, family)

        material = load_values_material(db_session, [context_id])[context_id]

        assert material.schema_id == schema.id
        assert [(p.ordinal, p.name, p.values) for p in material.parameters] == [
            (1, "Толщина", ("100 мм", "50 мм")),
            (2, "Материал", ("в", "а", "б")),
        ]

    def test_merged_value_is_absent_from_the_request_body(self, db_session, factories):
        family = _family(db_session)
        schema = _schema(db_session, factories, family)
        parameter = _param(db_session, schema, 1, "Толщина")
        target = _value(db_session, parameter, "100 мм")
        merged = _value(db_session, parameter, "слитое-значение-xyz")
        merged.merged_into_id = target.id
        db_session.flush()
        context_id = self._context_in(db_session, factories, family)
        material = load_values_material(db_session, [context_id])[context_id]
        rendered = render_values_request(material, settings=_settings())
        dump = repr(rendered.body)
        assert "100 мм" in dump
        assert "слитое-значение-xyz" not in dump

    def test_zero_parameter_current_schema_is_renderable(self, db_session, factories):
        family = _family(db_session)
        schema = _schema(db_session, factories, family)
        context_id = self._context_in(db_session, factories, family)
        material = load_values_material(db_session, [context_id])[context_id]
        assert (material.schema_id, material.parameters) == (schema.id, ())

    def test_pending_family_takes_precedence_over_current(self, db_session, factories):
        current, target = _family(db_session), _family(db_session)
        _frozen_schema(db_session, factories, current, [(1, "Старый параметр", ["а"])])
        target_schema = _frozen_schema(db_session, factories, target, [(1, "Новый параметр", ["б"])])
        context_id = self._context_in(db_session, factories, current)
        _bind(db_session, factories, context_id, pending=target)
        material = load_values_material(db_session, [context_id])[context_id]
        assert material.schema_id == target_schema.id
        assert [p.name for p in material.parameters] == ["Новый параметр"]

    def test_pending_family_without_current_one_is_enough(self, db_session, factories):
        target = _family(db_session)
        schema = _frozen_schema(db_session, factories, target, [(1, "Параметр", ["а"])])
        context_id, _ = _chain_context(
            db_session, factories, title=f"Строка {_uid()}", path_specs=[((), 1)]
        )
        _bind(db_session, factories, context_id, pending=target)
        assert load_values_material(db_session, [context_id])[context_id].schema_id == schema.id

    def test_only_the_frozen_version_is_used(self, db_session, factories):
        family = _family(db_session)
        old = _schema(db_session, factories, family, version=1, status="superseded")
        _param(db_session, old, 1, "Прежний параметр")
        _schema(db_session, factories, family, version=3, status="building", frozen_at=None)
        current = _frozen_schema_version(db_session, factories, family, 2, "Текущий параметр")
        context_id = self._context_in(db_session, factories, family)
        material = load_values_material(db_session, [context_id])[context_id]
        assert material.schema_id == current.id
        assert [p.name for p in material.parameters] == ["Текущий параметр"]

    def test_family_without_a_frozen_version_is_absent(self, db_session, factories):
        family = _family(db_session)
        _schema(db_session, factories, family, version=1, status="building", frozen_at=None)
        context_id = self._context_in(db_session, factories, family)
        assert load_values_material(db_session, [context_id]) == {}

    def test_context_without_family_is_absent_not_none(self, db_session, factories):
        context_id, _ = _chain_context(
            db_session, factories, title=f"Строка {_uid()}", path_specs=[((), 1)]
        )
        result = load_values_material(db_session, [context_id])
        assert context_id not in result
        assert result == {}

    def test_unknown_and_empty_inputs(self, db_session):
        assert load_values_material(db_session, []) == {}
        assert load_values_material(db_session, [987654321]) == {}

    def test_query_count_does_not_grow_with_context_count(self, db_session, factories):
        family = _family(db_session)
        _frozen_schema(db_session, factories, family, [(1, "Параметр", ["а", "б"])])
        ids = [self._context_in(db_session, factories, family) for _ in range(9)]
        with _capturing_sql(db_session) as small:
            small_result = load_values_material(db_session, ids[:2])
        with _capturing_sql(db_session) as large:
            large_result = load_values_material(db_session, ids)
        assert (len(small_result), len(large_result)) == (2, 9)
        assert len(small) == len(large)

    def test_nothing_to_render_stops_before_the_next_query(self, db_session, factories):
        """Короткие пути загрузчика: пустой вход — ни одного запроса; контекст без
        семьи — только запрос семей; семья без текущей версии — ещё запрос схем,
        но не параметров и не материала строки. Результат у всех трёх один —
        пустой словарь, поэтому наблюдаемо их отличает только число запросов."""
        no_family_id, _ = _chain_context(
            db_session, factories, title=f"Строка {_uid()}", path_specs=[((), 1)]
        )
        building = _family(db_session)
        _schema(db_session, factories, building, version=1, status="building", frozen_at=None)
        no_schema_id = self._context_in(db_session, factories, building)

        counts = {}
        for name, ids in (("empty", []), ("no_family", [no_family_id]),
                          ("no_frozen", [no_schema_id])):
            with _capturing_sql(db_session) as statements:
                assert load_values_material(db_session, ids) == {}
            counts[name] = len(statements)
        assert counts == {"empty": 0, "no_family": 1, "no_frozen": 2}

    def test_zero_parameter_schema_skips_the_values_query(self, db_session, factories):
        zero, one = _family(db_session), _family(db_session)
        _frozen_schema(db_session, factories, zero, [])
        _frozen_schema(db_session, factories, one, [(1, "Параметр", ["а"])])
        zero_id = self._context_in(db_session, factories, zero)
        one_id = self._context_in(db_session, factories, one)
        with _capturing_sql(db_session) as zero_statements:
            assert load_values_material(db_session, [zero_id])[zero_id].parameters == ()
        with _capturing_sql(db_session) as one_statements:
            assert len(load_values_material(db_session, [one_id])[one_id].parameters) == 1
        assert len(one_statements) - len(zero_statements) == 1

    def test_contexts_of_one_family_share_the_schema_and_prefix(self, db_session, factories):
        family = _family(db_session)
        schema = _frozen_schema(db_session, factories, family, [(1, "Параметр", ["а"])])
        first = self._context_in(db_session, factories, family, "Первая")
        second = self._context_in(db_session, factories, family, "Вторая")
        result = load_values_material(db_session, [first, second])
        assert {m.schema_id for m in result.values()} == {schema.id}
        settings = _settings()
        a, b = (render_values_request(result[c], settings=settings) for c in (first, second))
        assert a.prefix_hash == b.prefix_hash
        assert a.request_hash != b.request_hash


def _frozen_schema_version(db, factories, family, version, parameter_name):
    schema = _schema(db, factories, family, version=version)
    _value(db, _param(db, schema, 1, parameter_name), "значение")
    return schema


class TestIdentifiersDoNotChangeTheHash:
    def test_two_families_with_equal_content_give_equal_request_hashes(
        self, db_session, factories
    ):
        bucket = _bucket(db_session, factories, "Одинаковая строка")
        hashes = []
        schema_ids = []
        for _ in range(2):
            family = _family(db_session)
            schema = _frozen_schema(
                db_session, factories, family, [(1, "Толщина", ["50 мм", "80 мм"])]
            )
            context = _context(
                db_session, factories, "Одинаковая строка", work=family, bucket=bucket
            )
            material = load_values_material(db_session, [context.id])[context.id]
            schema_ids.append(schema.id)
            hashes.append(render_values_request(material, settings=_settings()).request_hash)
        assert schema_ids[0] != schema_ids[1]
        assert hashes[0] == hashes[1]


# ---------------------------------------------------------------------------
#  render_request_for
# ---------------------------------------------------------------------------

class TestRenderRequestFor:
    def test_family_schema_job_renders_current_schema_material(self, db_session, factories):
        family = _family(db_session, title="Стяжка", definition="Стяжка пола")
        context = _context(db_session, factories, "Стяжка 50 мм", work=family)
        job = SemanticJob(
            kind=SemanticJobKind.family_schema.value, family_id=family.id, schema_id=11,
        )
        rendered = render_request_for(db_session, job, settings=_settings())
        expected = render_schema_request(
            load_schema_material(db_session, family.id, 11), settings=_settings()
        )
        assert rendered == expected
        assert "Стяжка 50 мм" in rendered.body["messages"][1]["content"]
        assert context.id is not None

    def test_family_schema_job_of_a_missing_family_is_not_renderable(self, db_session):
        job = SemanticJob(
            kind=SemanticJobKind.family_schema.value, family_id=987654321, schema_id=1
        )
        with pytest.raises(SubjectNotRenderable):
            render_request_for(db_session, job, settings=_settings())

    def test_context_values_job_renders_current_material(self, db_session, factories):
        family = _family(db_session)
        schema = _frozen_schema(db_session, factories, family, [(1, "Толщина", ["50 мм"])])
        context_id, _ = _chain_context(
            db_session, factories, title="Стяжка 50 мм", path_specs=[(("Полы",), 2)]
        )
        _bind(db_session, factories, context_id, family=family)
        job = SemanticJob(
            kind=SemanticJobKind.context_values.value, context_id=context_id,
            schema_id=schema.id, paths_hash="x",
        )
        rendered = render_request_for(db_session, job, settings=_settings())
        material = load_values_material(db_session, [context_id])[context_id]
        assert rendered == render_values_request(material, settings=_settings())

    def test_stale_schema_id_on_the_job_renders_the_current_schema(self, db_session, factories):
        family = _family(db_session)
        _schema(db_session, factories, family, version=1, status="superseded")
        current = _frozen_schema_version(db_session, factories, family, 2, "Текущий")
        context_id, _ = _chain_context(
            db_session, factories, title=f"Строка {_uid()}", path_specs=[((), 1)]
        )
        _bind(db_session, factories, context_id, family=family)
        job = SemanticJob(
            kind=SemanticJobKind.context_values.value, context_id=context_id,
            schema_id=current.id - 1, paths_hash="x",
        )
        rendered = render_request_for(db_session, job, settings=_settings())
        assert "Текущий" in rendered.body["messages"][0]["content"][1]["text"]

    def test_context_values_job_without_a_schema_is_not_renderable(self, db_session, factories):
        context_id, _ = _chain_context(
            db_session, factories, title=f"Строка {_uid()}", path_specs=[((), 1)]
        )
        job = SemanticJob(
            kind=SemanticJobKind.context_values.value, context_id=context_id, schema_id=1,
            paths_hash="x",
        )
        with pytest.raises(SubjectNotRenderable):
            render_request_for(db_session, job, settings=_settings())

    def test_family_suggestion_job_uses_the_unchanged_feature_two_path(
        self, db_session, factories
    ):
        family = _family(
            db_session, title="Стяжка", status="active", definition="Стяжка пола",
            unit_id=_unit_id(db_session, "M2"),
        )
        context_id, _ = _chain_context(
            db_session, factories, title="Стяжка пола", unit_id=_unit_id(db_session, "M2"),
            path_specs=[(("Полы",), 1)],
        )
        job = SemanticJob(kind=SemanticJobKind.family_suggestion.value, context_id=context_id)
        rendered = render_request_for(db_session, job, settings=_settings())
        expected = render_context_request(
            load_request_material(db_session, [context_id])[context_id], settings=_settings()
        )
        assert rendered == expected
        assert "response_format" not in rendered.body
        assert family.id is not None

    def test_family_suggestion_job_of_a_missing_context_is_not_renderable(self, db_session):
        job = SemanticJob(kind=SemanticJobKind.family_suggestion.value, context_id=987654321)
        with pytest.raises(SubjectNotRenderable):
            render_request_for(db_session, job, settings=_settings())

    def test_unknown_kind_is_rejected(self, db_session):
        job = SimpleNamespace(kind="surprise", family_id=None, context_id=None, schema_id=None)
        with pytest.raises(ValueError):
            render_request_for(db_session, job, settings=_settings())
