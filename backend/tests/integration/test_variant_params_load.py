"""Параметры строки КП с названиями — `load_kp_lines` (спека параметров варианта §2.2, §2.4).

Сцены строятся помощниками `World` из тестов загрузчиков; записи значений контекста
и значения вариантов пишутся литералами, названия выдуманы. Ожидания — независимые
литералы таблицы показа §2.2, а не вызов проверяемого кода.
"""

from __future__ import annotations

import pytest

from crud.our_price_load import catalog_text, load_kp_lines
from models import ContextParameterValue, FamilyParameterValue, FamilyValuePart
from tests.integration.test_our_price_load import NOW, World, count_statements

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


def _view(param):
    """Плоское представление параметра строки для сравнения с литералом."""
    return (
        param.name,
        param.role,
        param.value,
        param.off_list,
        param.composite,
        param.source,
        param.refreshing,
        param.catalog_value,
    )


def _record(world, item, schema, parameter, *, value_id=None, source="manual", off_list=None, refreshing=False):
    """Запись значения контекста строки по параметру действующей схемы."""
    world.db.add(
        ContextParameterValue(
            context_id=item.context_id,
            schema_id=schema.id,
            parameter_id=parameter.id,
            value_id=value_id,
            source=source,
            off_list_text=off_list,
            refresh_requested_at=NOW if refreshing else None,
        )
    )
    world.db.flush()


def _scene(world):
    """Семья со схемой: главные «Толщина» (1) и «Материал» (2), второстепенные «Цвет» (3)
    и «Фактура» (4), архивные второстепенный «Старый» (5) и главный «Прежний» (6).

    Параметры заводятся не по порядку номеров: порядок по `id` (и порядок вставки) с
    порядком по `ordinal` не совпадает, иначе сортировку по номеру тест бы не отличил."""
    estimate, proposal = world.estimate(world.contract())
    family, schema = world.family()
    material = world.param(schema, 2, "Материал")
    thickness = world.param(schema, 1, "Толщина")
    texture = world.param(schema, 4, "Фактура", role="secondary")
    color = world.param(schema, 3, "Цвет", role="secondary")
    world.param(schema, 5, "Старый", role="secondary", archived=True)
    world.param(schema, 6, "Прежний", archived=True)
    return estimate, proposal, family, schema, thickness, material, color, texture


def _params(world, estimate):
    (row,) = load_kp_lines(world.db, [estimate.id])[estimate.id]
    return row.params


class TestComposition:
    def test_mains_by_ordinal_then_secondaries_and_no_archived(self, world):
        estimate, proposal, family, schema, thickness, material, color, texture = _scene(world)
        variant = world.variant(family, schema, {1: world.value(thickness, "50 мм"), 2: world.value(material, "бетон")})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, thickness, value_id=world.value(thickness, "50 мм"), source="name")
        _record(world, item, schema, material, value_id=world.value(material, "бетон"), source="path")
        _record(world, item, schema, color, value_id=world.value(color, "серый"))
        _record(world, item, schema, texture, value_id=world.value(texture, "гладкая"), source="name")

        params = _params(world, estimate)

        assert [(p.name, p.role) for p in params] == [
            ("Толщина", "main"),
            ("Материал", "main"),
            ("Цвет", "secondary"),
            ("Фактура", "secondary"),
        ]
        assert [p.parameter_id for p in params] == [thickness.id, material.id, color.id, texture.id]

    def test_line_without_a_variant_has_no_params(self, world):
        estimate, proposal, family, schema, *_ = _scene(world)
        world.line(proposal, family=family)

        assert _params(world, estimate) == ()

    def test_line_without_a_family_has_no_params(self, world):
        estimate, proposal, *_ = _scene(world)
        world.line(proposal)

        assert _params(world, estimate) == ()

    def test_secondary_without_a_record_is_listed_as_missing(self, world):
        estimate, proposal, family, schema, thickness, material, color, texture = _scene(world)
        variant = world.variant(family, schema, {1: world.value(thickness, "50 мм"), 2: world.value(material, "бетон")})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, color, value_id=world.value(color, "серый"))

        params = _params(world, estimate)

        assert [_view(p) for p in params if p.role == "secondary"] == [
            ("Цвет", "secondary", "серый", None, False, "manual", False, None),
            ("Фактура", "secondary", None, None, False, "missing", False, None),
        ]


class TestMainValues:
    def test_value_is_the_variant_text_and_the_source_comes_through(self, world):
        estimate, proposal, family, schema, thickness, material, color, texture = _scene(world)
        thin = world.value(thickness, "50 мм")
        concrete = world.value(material, "бетон")
        variant = world.variant(family, schema, {1: thin, 2: concrete})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, thickness, value_id=thin, source="name")
        _record(world, item, schema, material, value_id=concrete, source="manual")

        mains = [p for p in _params(world, estimate) if p.role == "main"]

        assert [_view(p) for p in mains] == [
            ("Толщина", "main", "50 мм", None, False, "name", False, None),
            ("Материал", "main", "бетон", None, False, "manual", False, None),
        ]

    def test_path_source_comes_through(self, world):
        estimate, proposal, family, schema, thickness, material, *_ = _scene(world)
        thin = world.value(thickness, "50 мм")
        variant = world.variant(family, schema, {1: thin, 2: None})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, thickness, value_id=thin, source="path")
        _record(world, item, schema, material, source="none")

        first, second, *_ = _params(world, estimate)

        assert (first.source, second.source) == ("path", "none")

    @pytest.mark.parametrize(
        ("source", "off_list"),
        [("path_conflict", None), ("none", None), ("off_list", "75 мм")],
    )
    def test_empty_sources_against_an_empty_variant_value_keep_their_source(self, world, source, off_list):
        """NULL записи против NULL варианта — не расхождение: источник исходный, не `stale`."""
        estimate, proposal, family, schema, thickness, material, *_ = _scene(world)
        variant = world.variant(family, schema, {1: None, 2: world.value(material, "бетон")})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, thickness, source=source, off_list=off_list)
        _record(world, item, schema, material, value_id=world.value(material, "бетон"), source="name")

        first, *_ = _params(world, estimate)

        assert _view(first) == ("Толщина", "main", None, off_list, False, source, False, None)

    def test_a_main_without_a_record_is_missing_not_none(self, world):
        estimate, proposal, family, schema, thickness, material, *_ = _scene(world)
        thin = world.value(thickness, "50 мм")
        variant = world.variant(family, schema, {1: thin, 2: None})
        world.line(proposal, family=family, variant=variant)

        first, second, *_ = _params(world, estimate)

        assert _view(first) == ("Толщина", "main", "50 мм", None, False, "missing", False, None)
        assert _view(second) == ("Материал", "main", None, None, False, "missing", False, None)

    def test_composite_form_marks_only_the_composite_main(self, world):
        estimate, proposal, family, schema, thickness, material, *_ = _scene(world)
        part_a = world.value(thickness, "100 мм")
        part_b = world.value(thickness, "150 мм")
        composite = FamilyParameterValue(
            parameter_id=thickness.id,
            value="100 мм + 150 мм",
            value_norm="100 мм + 150 мм",
            origin="extension",
            composite_kind="all",
            composite_key=f"all:{part_a},{part_b}",
        )
        world.db.add(composite)
        world.db.flush()
        for part in (part_a, part_b):
            world.db.add(FamilyValuePart(value_id=composite.id, part_value_id=part, parameter_id=thickness.id))
        world.db.flush()
        steel = world.value(material, "сталь")
        variant = world.variant(family, schema, {1: composite.id, 2: steel})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, thickness, value_id=composite.id, source="manual")
        _record(world, item, schema, material, value_id=steel, source="manual")

        first, second, *_ = _params(world, estimate)

        assert (first.value, first.composite) == ("100 мм + 150 мм", True)
        assert (second.value, second.composite) == ("сталь", False)

    def test_off_list_text_is_the_record_text(self, world):
        estimate, proposal, family, schema, thickness, material, *_ = _scene(world)
        variant = world.variant(family, schema, {1: None, 2: None})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, thickness, source="off_list", off_list="75 мм")
        _record(world, item, schema, material, source="none")

        first, second, *_ = _params(world, estimate)

        assert _view(first) == ("Толщина", "main", None, "75 мм", False, "off_list", False, None)
        assert _view(second) == ("Материал", "main", None, None, False, "none", False, None)


class TestOldSchema:
    # система идёт тем же путём, что работа: главные её варианта прежней схемы — тоже `old_schema`
    @pytest.mark.parametrize("kind", [None, "SYSTEM"])
    def test_every_main_of_a_variant_on_a_replaced_schema_is_old_schema(self, world, kind):
        estimate, proposal, family, schema, thickness, material, color, texture = _scene(world)
        thin = world.value(thickness, "50 мм")
        concrete = world.value(material, "бетон")
        variant = world.variant(family, schema, {1: thin, 2: concrete})
        item = world.line(proposal, family=family, variant=variant, kind=kind)
        new = world.supersede(family, schema)
        new_thickness = world.param(new, 1, "Толщина")
        new_color = world.param(new, 2, "Цвет", role="secondary")
        # запись того же порядкового номера на действующей схеме не делает главный «своим»
        _record(
            world,
            item,
            new,
            new_thickness,
            value_id=world.value(new_thickness, "70 мм"),
            source="manual",
            refreshing=True,
        )
        _record(world, item, new, new_color, value_id=world.value(new_color, "серый"))

        params = _params(world, estimate)

        assert [_view(p) for p in params] == [
            ("Толщина", "main", "50 мм", None, False, "old_schema", False, None),
            ("Материал", "main", "бетон", None, False, "old_schema", False, None),
            ("Цвет", "secondary", "серый", None, False, "manual", False, None),
        ]

    def test_records_of_the_variant_own_old_parameters_are_not_read(self, world):
        """Записи контекста по главным самой прежней схемы варианта не сопоставляются: ни
        источник, ни «не из списка», ни отметка перезапроса из них не берутся."""
        estimate, proposal, family, schema, thickness, material, color, texture = _scene(world)
        thin = world.value(thickness, "50 мм")
        variant = world.variant(family, schema, {1: thin, 2: None})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, thickness, value_id=world.value(thickness, "100 мм"), refreshing=True)
        _record(world, item, schema, material, source="off_list", off_list="сталь Х", refreshing=True)
        world.supersede(family, schema)

        params = _params(world, estimate)

        assert [_view(p) for p in params] == [
            ("Толщина", "main", "50 мм", None, False, "old_schema", False, None),
            ("Материал", "main", None, None, False, "old_schema", False, None),
        ]


class TestStale:
    def test_schema_edit_scenario_marks_only_the_diverged_main(self, world):
        """Правка схемы разрешила «Толщину» (`manual`, отметка снята), «Материал» ещё
        `refreshing`, версия схемы та же: у первого `stale`, у второго свой источник."""
        estimate, proposal, family, schema, thickness, material, *_ = _scene(world)
        concrete = world.value(material, "бетон")
        variant = world.variant(family, schema, {1: world.value(thickness, "50 мм"), 2: concrete})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, thickness, value_id=world.value(thickness, "100 мм"), source="manual")
        _record(world, item, schema, material, value_id=concrete, source="name", refreshing=True)

        first, second, *_ = _params(world, estimate)

        assert _view(first) == ("Толщина", "main", "50 мм", None, False, "stale", False, "100 мм")
        assert _view(second) == ("Материал", "main", "бетон", None, False, "name", True, None)

    @pytest.mark.parametrize(
        ("record", "expected_catalog"),
        [
            ({"source": "off_list", "off_list": "75 мм"}, "не из списка: «75 мм»"),
            ({"source": "path_conflict"}, "расходится по разделам"),
            ({"source": "none"}, "не уточнено"),
        ],
    )
    def test_an_empty_record_against_a_variant_value_is_stale(self, world, record, expected_catalog):
        estimate, proposal, family, schema, thickness, material, *_ = _scene(world)
        variant = world.variant(family, schema, {1: world.value(thickness, "50 мм"), 2: None})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, thickness, **record)

        first, *_ = _params(world, estimate)

        assert _view(first) == ("Толщина", "main", "50 мм", None, False, "stale", False, expected_catalog)

    def test_a_value_against_an_empty_variant_value_is_stale(self, world):
        estimate, proposal, family, schema, thickness, material, *_ = _scene(world)
        variant = world.variant(family, schema, {1: None, 2: None})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, thickness, value_id=world.value(thickness, "100 мм"), source="manual")

        first, *_ = _params(world, estimate)

        assert _view(first) == ("Толщина", "main", None, None, False, "stale", False, "100 мм")

    def test_stale_keeps_the_refreshing_flag_of_the_record(self, world):
        estimate, proposal, family, schema, thickness, material, *_ = _scene(world)
        variant = world.variant(family, schema, {1: world.value(thickness, "50 мм"), 2: None})
        item = world.line(proposal, family=family, variant=variant)
        _record(
            world, item, schema, thickness, value_id=world.value(thickness, "100 мм"), source="manual", refreshing=True
        )

        first, *_ = _params(world, estimate)

        assert (first.source, first.refreshing) == ("stale", True)


class TestRefreshing:
    def test_flag_follows_the_record_and_the_source_stays(self, world):
        estimate, proposal, family, schema, thickness, material, color, texture = _scene(world)
        variant = world.variant(family, schema, {1: None, 2: None})
        item = world.line(proposal, family=family, variant=variant)
        _record(world, item, schema, thickness, source="none", refreshing=True)
        _record(world, item, schema, material, source="off_list", off_list="сталь Х", refreshing=True)
        _record(world, item, schema, color, source="none", refreshing=True)
        _record(world, item, schema, texture, value_id=world.value(texture, "гладкая"), source="name")

        params = _params(world, estimate)

        assert [_view(p) for p in params] == [
            ("Толщина", "main", None, None, False, "none", True, None),
            ("Материал", "main", None, "сталь Х", False, "off_list", True, None),
            ("Цвет", "secondary", None, None, False, "none", True, None),
            ("Фактура", "secondary", "гладкая", None, False, "name", False, None),
        ]


class TestCatalogText:
    @pytest.mark.parametrize(
        ("value", "source", "off_list", "expected"),
        [
            ("100 мм", "manual", None, "100 мм"),
            (None, "off_list", "75 мм", "не из списка: «75 мм»"),
            (None, "path_conflict", None, "расходится по разделам"),
            (None, "none", None, "не уточнено"),
            (None, "manual", None, "не уточнено"),
            # path_conflict и off_list проверяются раньше значения
            ("100 мм", "path_conflict", None, "расходится по разделам"),
            ("100 мм", "off_list", "75 мм", "не из списка: «75 мм»"),
        ],
    )
    def test_text_by_the_display_table(self, value, source, off_list, expected):
        assert catalog_text(value, source, off_list) == expected


class TestStatementCount:
    def _rows(self, world, proposal, family, schema, thickness, color, count, start):
        for index in range(start, start + count):
            value = world.value(thickness, f"{index} мм")
            variant = world.variant(family, schema, {1: value})
            item = world.line(proposal, family=family, variant=variant)
            _record(world, item, schema, thickness, value_id=value, source="name")
            _record(world, item, schema, color, value_id=world.value(color, f"цвет {index}"))

    def test_load_kp_lines_runs_the_same_statements_for_2_and_20_lines(self, world, db_session):
        estimate, proposal = world.estimate(world.contract())
        family, schema = world.family()
        thickness = world.param(schema, 1, "Толщина")
        color = world.param(schema, 2, "Цвет", role="secondary")
        self._rows(world, proposal, family, schema, thickness, color, 2, 0)
        with count_statements(db_session) as few:
            rows_few = load_kp_lines(db_session, [estimate.id])[estimate.id]
        # вторая семья: чтение по семье (второстепенные действующей схемы) тоже не должно расти
        other, other_schema = world.family(title="Семья Б")
        other_thickness = world.param(other_schema, 1, "Ширина")
        other_color = world.param(other_schema, 2, "Оттенок", role="secondary")
        self._rows(world, proposal, family, schema, thickness, color, 9, 2)
        self._rows(world, proposal, other, other_schema, other_thickness, other_color, 9, 11)
        with count_statements(db_session) as many:
            rows_many = load_kp_lines(db_session, [estimate.id])[estimate.id]

        assert (len(rows_few), len(rows_many)) == (2, 20)
        # записи действительно прочитаны: у каждой строки главный и второстепенный из записей
        assert {tuple((p.role, p.source) for p in r.params) for r in rows_many} == {
            (("main", "name"), ("secondary", "manual"))
        }
        assert few[0] > 0
        assert many[0] == few[0]
