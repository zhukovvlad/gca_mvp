"""Тесты шаблона позиции.

Перенос `app/tests/excel_parser/test_get_items_dict.py` из
`parser_tender_xlsx@0e178c0`.

**Тесты исходника были протухшими**: они описывали более раннюю версию
`get_items_dict` — с валидным colspan 12 и полем `deviation_from_baseline_cost`
у colspan 9/10/11. Код исходника на зафиксированной ревизии так себя не ведёт
(colspan 8..11, поля отклонения нет вовсе), то есть эти тесты в исходном
репозитории падали. Здесь они приведены к фактическому поведению кода;
проверяемые свойства (общие поля всегда на месте, блоки стоимости —
независимые копии, неподдерживаемый colspan даёт "error") сохранены.
"""
from __future__ import annotations

from parser.constants import (
    JSON_KEY_ARTICLE_SMR,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_COMMENT_CONTRACTOR,
    JSON_KEY_COMMENT_ORGANIZER,
    JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
    JSON_KEY_INDIRECT_COSTS,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_MATERIALS,
    JSON_KEY_NUMBER,
    JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
    JSON_KEY_QUANTITY,
    JSON_KEY_SUGGESTED_QUANTITY,
    JSON_KEY_TOTAL,
    JSON_KEY_TOTAL_COST,
    JSON_KEY_UNIT,
    JSON_KEY_UNIT_COST,
    JSON_KEY_WORKS,
)
from parser.get_items_dict import get_items_dict

COMMON_FIELDS = [
    JSON_KEY_NUMBER,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_ARTICLE_SMR,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_COMMENT_ORGANIZER,
    JSON_KEY_UNIT,
    JSON_KEY_QUANTITY,
]

VALID_COLSPANS = [8, 9, 10, 11]


class TestGetItemsDictBehavior:
    """Общее поведение."""

    def test_returns_dict_for_any_input(self):
        for colspan in [*VALID_COLSPANS, 12, 0, -1, 100, 999]:
            assert isinstance(get_items_dict(colspan), dict), f"colspan={colspan}"

    def test_always_includes_common_fields(self):
        for colspan in [*VALID_COLSPANS, 12, -1, 999]:
            result = get_items_dict(colspan)
            for field in COMMON_FIELDS:
                assert field in result, f"{field} должно быть при colspan={colspan}"

    def test_common_fields_default_to_none(self):
        result = get_items_dict(8)
        for field in COMMON_FIELDS:
            assert result[field] is None, field


class TestGetItemsDictValidColspan:
    """Состав полей подрядчика по ширине блока."""

    def test_colspan_8_structure(self):
        """colspan=8 — только блоки стоимости."""
        result = get_items_dict(8)

        assert isinstance(result[JSON_KEY_UNIT_COST], dict)
        assert isinstance(result[JSON_KEY_TOTAL_COST], dict)

        for field in (
            JSON_KEY_SUGGESTED_QUANTITY,
            JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
            JSON_KEY_COMMENT_CONTRACTOR,
            JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
        ):
            assert field not in result, f"{field} не должно быть при colspan=8"

    def test_colspan_9_adds_contractor_comment(self):
        result = get_items_dict(9)

        assert JSON_KEY_UNIT_COST in result
        assert JSON_KEY_TOTAL_COST in result
        assert result[JSON_KEY_COMMENT_CONTRACTOR] is None

        for field in (JSON_KEY_SUGGESTED_QUANTITY, JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST):
            assert field not in result, f"{field} не должно быть при colspan=9"

    def test_colspan_10_adds_suggested_quantity_and_organizer_total(self):
        result = get_items_dict(10)

        assert result[JSON_KEY_SUGGESTED_QUANTITY] is None
        assert result[JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST] is None
        assert JSON_KEY_COMMENT_CONTRACTOR not in result

    def test_colspan_11_is_full_gp_estimate_layout(self):
        """colspan=11 — раскладка сметы ГП: J..T."""
        result = get_items_dict(11)

        for field in (
            JSON_KEY_SUGGESTED_QUANTITY,
            JSON_KEY_UNIT_COST,
            JSON_KEY_TOTAL_COST,
            JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
            JSON_KEY_COMMENT_CONTRACTOR,
        ):
            assert field in result, field

        assert result[JSON_KEY_SUGGESTED_QUANTITY] is None
        assert result[JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST] is None
        assert result[JSON_KEY_COMMENT_CONTRACTOR] is None

    def test_deviation_field_never_present(self):
        """Поля отклонения от baseline нет ни при какой ширине.

        В смете ГП baseline отсутствует по определению (AGENTS.md §4), а в
        тендерной таблице отклонение живёт в summary, не в шаблоне позиции.
        """
        for colspan in VALID_COLSPANS:
            assert JSON_KEY_DEVIATION_FROM_CALCULATED_COST not in get_items_dict(colspan)


class TestGetItemsDictCostBlocks:
    """Вложенные блоки стоимости."""

    def test_cost_blocks_are_independent_copies(self):
        result = get_items_dict(8)
        unit_cost = result[JSON_KEY_UNIT_COST]
        total_cost = result[JSON_KEY_TOTAL_COST]

        assert unit_cost is not total_cost
        assert unit_cost == total_cost

        unit_cost[JSON_KEY_MATERIALS] = "test_value"
        assert total_cost[JSON_KEY_MATERIALS] is None

    def test_cost_block_structure(self):
        expected = [JSON_KEY_MATERIALS, JSON_KEY_WORKS, JSON_KEY_INDIRECT_COSTS, JSON_KEY_TOTAL]
        result = get_items_dict(8)

        for block_key in (JSON_KEY_UNIT_COST, JSON_KEY_TOTAL_COST):
            block = result[block_key]
            assert list(block) == expected, block_key
            assert all(block[f] is None for f in expected), block_key

    def test_cost_blocks_present_in_all_valid_colspan(self):
        for colspan in VALID_COLSPANS:
            result = get_items_dict(colspan)
            assert isinstance(result[JSON_KEY_UNIT_COST], dict), colspan
            assert isinstance(result[JSON_KEY_TOTAL_COST], dict), colspan


class TestGetItemsDictInvalidColspan:
    """Неподдерживаемая ширина блока."""

    def test_invalid_colspan_adds_error_field(self):
        for colspan in [0, -1, 7, 12, 13, 100, 999]:
            result = get_items_dict(colspan)
            assert result["error"] == f"Unknown contractor_colspan: {colspan}"

    def test_invalid_colspan_still_includes_common_fields(self):
        result = get_items_dict(999)
        for field in COMMON_FIELDS:
            assert result[field] is None, field

    def test_boundary_values(self):
        """Границы поддерживаемого диапазона: 8..11."""
        for colspan, valid in [(7, False), (8, True), (11, True), (12, False)]:
            result = get_items_dict(colspan)
            assert ("error" not in result) is valid, colspan


class TestGetItemsDictDataIntegrity:
    """Независимость возвращаемых структур."""

    def test_no_shared_mutable_objects(self):
        result1 = get_items_dict(11)
        result2 = get_items_dict(11)

        assert result1 is not result2
        assert result1 == result2

        result1[JSON_KEY_NUMBER] = "test"
        assert result2[JSON_KEY_NUMBER] is None

        result1[JSON_KEY_UNIT_COST][JSON_KEY_MATERIALS] = "test"
        assert result2[JSON_KEY_UNIT_COST][JSON_KEY_MATERIALS] is None

    def test_consistent_field_count_for_same_colspan(self):
        for colspan in VALID_COLSPANS:
            counts = {len(get_items_dict(colspan)) for _ in range(5)}
            assert len(counts) == 1, colspan

    def test_field_count_does_not_shrink_as_colspan_grows(self):
        counts = [len(get_items_dict(c)) for c in VALID_COLSPANS]
        assert counts == sorted(counts)

    def test_template_matches_parse_contractor_row_keys(self):
        """Шаблон и заполнение обязаны знать один и тот же набор полей.

        Если они разойдутся, позиция получит поля-призраки (из шаблона, всегда
        None) либо поля мимо шаблона — и то и другое утечёт в raw_data.
        """
        from parser.parse_contractor_row import parse_contractor_row

        for colspan in VALID_COLSPANS:
            template = get_items_dict(colspan)
            # Достаём приватный список ключей тем же способом, что и продовый код:
            # через фактический разбор строки на синтетическом листе.
            from openpyxl import Workbook

            ws = Workbook().active
            contractor = {"column_start": 1, "merged_shape": {"colspan": colspan}}
            parsed = parse_contractor_row(ws, 1, contractor)

            assert set(parsed) <= set(template), f"colspan={colspan}: лишние поля {set(parsed) - set(template)}"
            template_contractor_fields = set(template) - set(COMMON_FIELDS)
            assert template_contractor_fields == set(parsed), (
                f"colspan={colspan}: шаблон и разбор строки описывают разные поля"
            )
