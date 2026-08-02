"""Юнит-тест сериализатора строки расчёта — общая форма для /summary и /calculations."""
from datetime import date
from decimal import Decimal

from routers.dashboard import _serialize_calc_row


def _raw_row() -> dict:
    """Минимальная сырая строка compute_calculations (date-объекты, Decimal)."""
    return {
        "project_id": 1, "material_class_id": 10, "material_class_name": "В25",
        "direction": "concrete", "period_start": date(2026, 1, 1), "period_end": date(2026, 1, 31),
        "material_total": Decimal("100000"), "delivery_total": Decimal("0"),
        "total_qty": Decimal("10"), "avg_price": Decimal("9600"), "unit_symbol": "м³",
        "dimension_mismatch": False, "invoice_count": 1, "reference_price": None,
        "deviation_pct": None, "deviation_amount": None, "corridor_pct": None,
        "compensation_per_unit": None, "compensation_amount": None,
    }


def test_serialize_calc_row_isoformats_dates():
    """period_start/end сериализуются в ISO-строки."""
    out = _serialize_calc_row(_raw_row())
    assert out["period_start"] == "2026-01-01"
    assert out["period_end"] == "2026-01-31"


def test_serialize_calc_row_keys_stable():
    """Набор ключей фиксирован — контракт формы для обоих эндпоинтов."""
    out = _serialize_calc_row(_raw_row())
    assert set(out.keys()) == {
        "project_id", "material_class_id", "material_class_name", "direction",
        "period_start", "period_end", "material_total", "delivery_total", "total_qty",
        "avg_price", "unit_symbol", "dimension_mismatch", "invoice_count",
        "reference_price", "deviation_pct", "deviation_amount", "corridor_pct",
        "compensation_per_unit", "compensation_amount",
    }
