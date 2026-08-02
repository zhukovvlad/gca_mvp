"""Unit-тесты helper-функций pdf_parser."""
from unittest.mock import MagicMock, patch

from crud.materials import UnknownMaterialType
from pdf_parser import _calculate_completeness, _final_confidence, _reconcile_totals


class TestCalculateCompleteness:
    def test_full_invoice_returns_one(self):
        inv_data = {
            "number": "СФ-1",
            "date": "2026-05-01",
            "supplier_name": "ООО Ромашка",
            "items": [
                {"raw_name": "Бетон В25", "quantity": 5, "unit_price": 8000, "amount": 40000},
            ],
        }
        assert _calculate_completeness(inv_data) == 1.0

    def test_empty_invoice_returns_low(self):
        inv_data = {"number": "", "date": "", "supplier_name": "", "items": []}
        # 4 поля по 1 баллу, все пустые → 0/4 = 0.0
        assert _calculate_completeness(inv_data) == 0.0

    def test_invalid_date_does_not_count(self):
        inv_data = {
            "number": "X",
            "date": "not-a-date",
            "supplier_name": "Y",
            "items": [],
        }
        # number=1, date=0 (невалидная), supplier=1, items=0 → 2/4 = 0.5
        assert _calculate_completeness(inv_data) == 0.5

    def test_item_with_zero_quantity_partial_credit(self):
        inv_data = {
            "number": "X",
            "date": "2026-05-01",
            "supplier_name": "Y",
            "items": [
                {"raw_name": "Что-то", "quantity": 0, "unit_price": 100, "amount": 100},
            ],
        }
        # 4 invoice-fields full + 4 item-fields, qty=0 → 1 балл потерян → 7/8 = 0.88
        assert _calculate_completeness(inv_data) == 0.88


class TestFinalConfidence:
    def test_takes_min_of_model_and_completeness(self):
        assert _final_confidence(0.95, 0.5) == 0.5
        assert _final_confidence(0.5, 0.95) == 0.5

    def test_handles_none_model_conf(self):
        assert _final_confidence(None, 0.7) == 0.7

    def test_normalizes_percent_scale(self):
        # Модель вернула 95 вместо 0.95 → должно нормализоваться
        assert _final_confidence(95, 1.0) == 0.95

    def test_clamps_above_one(self):
        assert _final_confidence(150, 1.0) == 1.0

    def test_clamps_below_zero(self):
        assert _final_confidence(-0.5, 1.0) == 0.0

    def test_invalid_model_conf_falls_back_to_completeness(self):
        assert _final_confidence("invalid", 0.7) == 0.7


class TestReconcileTotals:
    def test_matching_totals_ok(self):
        items = [{"amount": 56000.0}, {"amount": 7500.0}]
        ok, detail = _reconcile_totals(63500.0, items)
        assert ok is True
        assert detail == ""

    def test_rounding_noise_within_tolerance_ok(self):
        # 66 строк с покопеечным округлением: сумма расходится с печатным итогом на рубли
        items = [{"amount": 73300.0} for _ in range(33)] + [{"amount": 7500.0} for _ in range(33)]
        # фактическая сумма = 2 666 400; печатный итог на 50 ₽ больше (накопленное округление)
        ok, detail = _reconcile_totals(2_666_450.0, items)
        assert ok is True

    def test_missing_rows_flags_incomplete(self):
        # 60 из 66 строк: сумма сильно меньше печатного итога
        items = [{"amount": 73300.0} for _ in range(30)] + [{"amount": 7500.0} for _ in range(30)]
        # сумма = 2 424 000; печатный итог 2 472 124.99 → расхождение ~48k
        ok, detail = _reconcile_totals(2_472_124.99, items)
        assert ok is False
        assert "Всего к оплате" in detail

    def test_absent_doc_total_flags_incomplete(self):
        ok, detail = _reconcile_totals(None, [{"amount": 100.0}])
        assert ok is False
        assert detail

    def test_zero_doc_total_flags_incomplete(self):
        ok, detail = _reconcile_totals(0.0, [{"amount": 100.0}])
        assert ok is False

    def test_empty_items_with_positive_total_flags(self):
        ok, detail = _reconcile_totals(1000.0, [])
        assert ok is False

    def test_item_missing_amount_treated_as_zero(self):
        # позиция без amount не должна ломать суммирование
        ok, detail = _reconcile_totals(100.0, [{"amount": 100.0}, {"raw_name": "x"}])
        assert ok is True


class TestUnknownMaterialTypeFallback:
    """Регрессионный тест: фаза B (`processing._resolve_material_class_id`) должна
    откатываться к 'other' при неизвестном material_type от LLM, а не падать.

    Мигрировано из parse_invoice_pdf (удалён в S0-5, Task 8) — та же самая try/except
    UnknownMaterialType-логика теперь живёт в processing._resolve_material_class_id
    (фаза B, персистенция), а не в pdf_parser (фаза A больше не резолвит материалы в id).
    """

    def test_fallback_to_other_on_unknown_material_type(self):
        """Проверяет, что try/except UnknownMaterialType в processing._resolve_material_class_id
        перехватывает исключение и повторно вызывает get_or_create_material_class
        с material_type='other'.

        Мокируем get_or_create_material_class так, что первый вызов (с 'wood')
        бросает UnknownMaterialType, а второй (с 'other') возвращает фейковый объект.
        Без try/except в processing.py UnknownMaterialType всплыл бы наружу
        и абортировал бы всю попытку обработки.
        """
        import processing

        fake_mc = MagicMock()
        fake_mc.id = 42

        call_count = 0

        def mock_get_or_create(db, name, material_type, calc_role="base", *, commit=True):
            """Первый вызов (material_type='wood') бросает UnknownMaterialType, второй
            (material_type='other', после fallback) возвращает fake_mc."""
            nonlocal call_count
            call_count += 1
            if material_type == "wood":
                raise UnknownMaterialType("wood")
            return fake_mc

        item = MagicMock()
        item.item_type = "material"
        item.material_class = "Древесина ЛДСп"
        item.material_type = "wood"
        item.calc_role = "base"

        with patch("processing.get_or_create_material_class", side_effect=mock_get_or_create):
            mc_id = processing._resolve_material_class_id(MagicMock(), item, document_id=1)

        assert mc_id == 42, "Fallback должен возвращать id объекта, созданного с material_type='other'"
        assert call_count == 2, "Должно быть ровно 2 вызова: первый упал, второй с 'other'"

    def test_unknown_material_type_exception_is_raised_for_unknown_code(self):
        """Убеждаемся, что UnknownMaterialType — это ValueError,
        и что его можно перехватить отдельно от других исключений."""
        exc = UnknownMaterialType("wood")
        assert isinstance(exc, ValueError)
        assert "wood" in str(exc)
