"""Тесты шаблона позиции.

Перенос `app/tests/excel_parser/test_get_items_dict.py` из
`parser_tender_xlsx@0e178c0`, переписанный под фичу «колонки по заголовкам»
(спека §2.4, §2.8): шаблон строится по РАЗРЕШЁННОЙ раскладке
(`BlockLayout.column_keys`), а не по ширине блока (`colspan`). Понятия
«неподдерживаемый colspan» и ветки "error" у `get_items_dict` больше нет —
они уходят вместе с самой мыслью, что смысл колонок определяет их число.
"""
from __future__ import annotations

from openpyxl import Workbook

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
from parser.parse_contractor_row import parse_contractor_row

from .sheet_builders import KEYS_8, KEYS_9, KEYS_10, KEYS_12, KEYS_GP_11, KEYS_TENDER_11, resolved

COMMON_FIELDS = [
    JSON_KEY_NUMBER,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_ARTICLE_SMR,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_COMMENT_ORGANIZER,
    JSON_KEY_UNIT,
    JSON_KEY_QUANTITY,
]

#: Пять измеренных раскладок задачи 2 (план фичи, «Замеры»): KEYS_8, KEYS_10,
#: KEYS_GP_11, KEYS_TENDER_11, KEYS_12. KEYS_9 в замер не входит — это ширина 9
#: старого (width-based) парсера, здесь она только тестовый случай, а не
#: ветвь кода (спека §3).
MEASURED_LAYOUTS = [KEYS_8, KEYS_9, KEYS_10, KEYS_GP_11, KEYS_TENDER_11, KEYS_12]


def _layout(columns):
    """`BlockLayout` для набора ключей — геометрия здесь не важна."""
    return resolved(10, columns).layout


class TestGetItemsDictBehavior:
    """Общее поведение."""

    def test_returns_dict_for_any_measured_layout(self):
        for columns in MEASURED_LAYOUTS:
            assert isinstance(get_items_dict(_layout(columns)), dict), columns

    def test_always_includes_common_fields(self):
        for columns in MEASURED_LAYOUTS:
            result = get_items_dict(_layout(columns))
            for field in COMMON_FIELDS:
                assert field in result, f"{field} должно быть при columns={columns}"

    def test_common_fields_default_to_none(self):
        result = get_items_dict(_layout(KEYS_8))
        for field in COMMON_FIELDS:
            assert result[field] is None, field


class TestGetItemsDictMeasuredLayouts:
    """Состав полей подрядчика по разрешённой раскладке (спека §2.4, §2.8)."""

    def test_keys_8_is_only_cost_blocks(self):
        """KEYS_8 — только блоки стоимости, ничего опционального."""
        result = get_items_dict(_layout(KEYS_8))

        assert isinstance(result[JSON_KEY_UNIT_COST], dict)
        assert isinstance(result[JSON_KEY_TOTAL_COST], dict)

        for field in (
            JSON_KEY_SUGGESTED_QUANTITY,
            JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
            JSON_KEY_COMMENT_CONTRACTOR,
            JSON_KEY_DEVIATION_FROM_CALCULATED_COST,
        ):
            assert field not in result, f"{field} не должно быть у KEYS_8"

    def test_keys_9_adds_contractor_comment(self):
        result = get_items_dict(_layout(KEYS_9))

        assert JSON_KEY_UNIT_COST in result
        assert JSON_KEY_TOTAL_COST in result
        assert result[JSON_KEY_COMMENT_CONTRACTOR] is None

        for field in (JSON_KEY_SUGGESTED_QUANTITY, JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST):
            assert field not in result, f"{field} не должно быть у KEYS_9"

    def test_keys_10_has_comment_but_not_organizer_total(self):
        """Смена ожиданий фичи (спека §2.8): у ширины 10 в файле нет колонки
        «Стоимость всего за объемы заказчика» — там стоит комментарий
        участника. Ключа `total_cost_for_organizer_quantity` в шаблоне нет
        вовсе, а `comment_contractor` есть — сменяет прежний
        `test_colspan_10_adds_suggested_quantity_and_organizer_total`, который
        описывал обратное (дефект, который фича устраняет, спека §7 класс 2).
        """
        result = get_items_dict(_layout(KEYS_10))

        assert result[JSON_KEY_SUGGESTED_QUANTITY] is None
        assert result[JSON_KEY_COMMENT_CONTRACTOR] is None
        assert JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST not in result

    def test_keys_gp_11_is_full_gp_estimate_layout(self):
        """KEYS_GP_11 — раскладка сметы ГП: J..T."""
        result = get_items_dict(_layout(KEYS_GP_11))

        for field in (
            JSON_KEY_SUGGESTED_QUANTITY,
            JSON_KEY_UNIT_COST,
            JSON_KEY_TOTAL_COST,
            JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST,
            JSON_KEY_COMMENT_CONTRACTOR,
        ):
            assert field in result, field
        assert JSON_KEY_DEVIATION_FROM_CALCULATED_COST not in result

        assert result[JSON_KEY_SUGGESTED_QUANTITY] is None
        assert result[JSON_KEY_ORGANIZER_QUANTITY_TOTAL_COST] is None
        assert result[JSON_KEY_COMMENT_CONTRACTOR] is None

    def test_keys_tender_11_has_deviation_instead_of_comment(self):
        """Другая ширина-11: «% от р/с» вместо комментария (спека §1.2)."""
        result = get_items_dict(_layout(KEYS_TENDER_11))

        assert result[JSON_KEY_DEVIATION_FROM_CALCULATED_COST] is None
        assert JSON_KEY_COMMENT_CONTRACTOR not in result

    def test_keys_12_carries_both_comment_and_deviation(self):
        result = get_items_dict(_layout(KEYS_12))

        assert result[JSON_KEY_COMMENT_CONTRACTOR] is None
        assert result[JSON_KEY_DEVIATION_FROM_CALCULATED_COST] is None

    def test_deviation_present_only_on_layouts_with_the_percent_column(self):
        """Поле отклонения — только у раскладок, где физически есть «% от
        р/с» (спека §2.2); в KEYS_GP_11 её нет, значит и ключа нет."""
        for columns in MEASURED_LAYOUTS:
            has_deviation_column = JSON_KEY_DEVIATION_FROM_CALCULATED_COST in columns
            has_deviation_key = JSON_KEY_DEVIATION_FROM_CALCULATED_COST in get_items_dict(_layout(columns))
            assert has_deviation_key == has_deviation_column, columns


class TestGetItemsDictCostBlocks:
    """Вложенные блоки стоимости."""

    def test_cost_blocks_are_independent_copies(self):
        result = get_items_dict(_layout(KEYS_8))
        unit_cost = result[JSON_KEY_UNIT_COST]
        total_cost = result[JSON_KEY_TOTAL_COST]

        assert unit_cost is not total_cost
        assert unit_cost == total_cost

        unit_cost[JSON_KEY_MATERIALS] = "test_value"
        assert total_cost[JSON_KEY_MATERIALS] is None

    def test_cost_block_structure(self):
        expected = [JSON_KEY_MATERIALS, JSON_KEY_WORKS, JSON_KEY_INDIRECT_COSTS, JSON_KEY_TOTAL]
        result = get_items_dict(_layout(KEYS_8))

        for block_key in (JSON_KEY_UNIT_COST, JSON_KEY_TOTAL_COST):
            block = result[block_key]
            assert list(block) == expected, block_key
            assert all(block[f] is None for f in expected), block_key

    def test_cost_blocks_present_in_every_measured_layout(self):
        for columns in MEASURED_LAYOUTS:
            result = get_items_dict(_layout(columns))
            assert isinstance(result[JSON_KEY_UNIT_COST], dict), columns
            assert isinstance(result[JSON_KEY_TOTAL_COST], dict), columns


class TestGetItemsDictDataIntegrity:
    """Независимость возвращаемых структур."""

    def test_no_shared_mutable_objects(self):
        layout = _layout(KEYS_GP_11)
        result1 = get_items_dict(layout)
        result2 = get_items_dict(layout)

        assert result1 is not result2
        assert result1 == result2

        result1[JSON_KEY_NUMBER] = "test"
        assert result2[JSON_KEY_NUMBER] is None

        result1[JSON_KEY_UNIT_COST][JSON_KEY_MATERIALS] = "test"
        assert result2[JSON_KEY_UNIT_COST][JSON_KEY_MATERIALS] is None

    def test_consistent_field_count_for_the_same_layout(self):
        for columns in MEASURED_LAYOUTS:
            layout = _layout(columns)
            counts = {len(get_items_dict(layout)) for _ in range(5)}
            assert len(counts) == 1, columns

    def test_field_count_does_not_shrink_along_the_measured_growth_chain(self):
        """Ширины 8→9→10→ГП-11 наращивают число полей монотонно — та самая
        измеренная цепочка расширений блока (docs/phase0-input-data.md).
        KEYS_12 сюда не входит: у него другой набор опциональных ключей
        (комментарий И отклонение сразу), а не надмножество предыдущих.
        """
        counts = [len(get_items_dict(_layout(c))) for c in (KEYS_8, KEYS_9, KEYS_10, KEYS_GP_11)]
        assert counts == sorted(counts)

    def test_template_matches_parse_contractor_row_keys(self):
        """Шаблон и заполнение обязаны знать один и тот же набор полей.

        Если они разойдутся, позиция получит поля-призраки (из шаблона, всегда
        None) либо поля мимо шаблона — и то и другое утечёт в raw_data. Оба
        строятся через ОДИН `resolved(...)`, чтобы раскладка не разъехалась
        сама с собой между шаблоном и разбором.
        """
        for columns in MEASURED_LAYOUTS:
            contractor = resolved(1, columns)
            template = get_items_dict(contractor.layout)

            ws = Workbook().active
            parsed = parse_contractor_row(ws, 1, contractor)

            assert set(parsed) <= set(template), f"{columns}: лишние поля {set(parsed) - set(template)}"
            template_contractor_fields = set(template) - set(COMMON_FIELDS)
            assert template_contractor_fields == set(parsed), (
                f"{columns}: шаблон и разбор строки описывают разные поля"
            )
