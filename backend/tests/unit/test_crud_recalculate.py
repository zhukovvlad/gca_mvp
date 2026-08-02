"""Тесты бизнес-логики crud.compute_calculations и сопутствующих функций.

Обновлены после удаления recalculate_prices():
compute_calculations() является единственным источником расчётов.
"""
from datetime import date
from unittest.mock import patch

import crud.materials as crud_materials
from crud.calculations import compute_calculations
from crud.materials import get_or_create_material_class
from crud.suppliers import _compute_supplier_project_deviation


def test_compute_calculations_no_items_returns_empty(factories, db_session):
    project = factories.ProjectFactory.create()
    result = compute_calculations(db_session, project.id)
    assert result == []


def test_compute_calculations_no_invoices_returns_empty(factories, db_session):
    project = factories.ProjectFactory.create()
    # Нет инвойсов → авто-диапазон не определяется → []
    result = compute_calculations(db_session, project.id)
    assert result == []


def test_compute_calculations_simple_avg(factories, db_session):
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create(name="В25", material_type_code="concrete")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc, quantity=10.0, unit_price=8000.0, amount=80000.0,
    )

    result = compute_calculations(db_session, project.id)

    assert len(result) == 1
    row = result[0]
    assert row["total_qty"] == 10.0
    assert row["avg_price"] == 9600.0
    assert row["invoice_count"] == 1
    assert row["reference_price"] is None
    assert row["deviation_amount"] is None
    # Auto-detected range is normalized to full month boundaries
    assert row["period_start"] == date(2026, 3, 1)
    assert row["period_end"] == date(2026, 3, 31)


def test_compute_calculations_with_deviation(factories, db_session):
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()
    factories.ReferencePriceFactory.create(
        project=project, material_class=mc, price=10000.0,
        period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
    )
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc, quantity=10.0, unit_price=11000.0, amount=110000.0,
    )

    result = compute_calculations(db_session, project.id)

    assert len(result) == 1
    row = result[0]
    assert row["reference_price"] == 10000.0
    assert row["deviation_pct"] == 32.0
    assert row["deviation_amount"] == 32000.0


def test_compute_calculations_delivery_allocation(factories, db_session):
    """Доставка распределяется пропорционально объёму класса в суммарном объёме."""
    project = factories.ProjectFactory.create()
    mc1 = factories.MaterialClassFactory.create(name="В25", material_type_code="concrete")
    mc2 = factories.MaterialClassFactory.create(name="d12", material_type_code="rebar")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))

    # mc1: 10 м³ × 8000 = 80 000, НДС 16 000
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc1,
        quantity=10.0, unit_price=8000.0, amount=80000.0, vat_amount=16000.0,
    )
    # mc2: 30 м³ × 6000 = 180 000, НДС 36 000
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc2,
        quantity=30.0, unit_price=6000.0, amount=180000.0, vat_amount=36000.0,
    )
    # Доставка: 40 000, НДС 8 000 → итого с НДС 48 000
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=None,
        item_type="delivery", quantity=1.0, unit_price=40000.0, amount=40000.0, vat_amount=8000.0,
    )

    result = compute_calculations(db_session, project.id)
    assert len(result) == 2

    by_class = {r["material_class_id"]: r for r in result}
    r1 = by_class[mc1.id]
    r2 = by_class[mc2.id]

    # mc1 доля = 10/40 = 0.25 → доставка_с_ндс 48_000 × 0.25 = 12_000
    # avg_price = (80_000 + 16_000 + 12_000) / 10 = 10_800
    assert r1["delivery_total"] == 12000.0
    assert r1["avg_price"] == 10800.0

    # mc2 доля = 30/40 = 0.75 → доставка_с_ндс 48_000 × 0.75 = 36_000
    # avg_price = (180_000 + 36_000 + 36_000) / 30 = 8_400
    assert r2["delivery_total"] == 36000.0
    assert r2["avg_price"] == 8400.0


def test_compute_calculations_period_filter(factories, db_session):
    """Явный period_start/period_end отсекает счета вне диапазона."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()
    doc = factories.DocumentFactory.create(project=project)

    inv_jan = factories.InvoiceFactory.create(document=doc, date=date(2026, 1, 15))
    factories.InvoiceItemFactory.create(
        invoice=inv_jan, material_class=mc, quantity=5.0, unit_price=9000.0, amount=45000.0,
    )
    inv_mar = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))
    factories.InvoiceItemFactory.create(
        invoice=inv_mar, material_class=mc, quantity=10.0, unit_price=8000.0, amount=80000.0,
    )

    # Фильтр только март
    result = compute_calculations(
        db_session, project.id,
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
    )
    assert len(result) == 1
    assert result[0]["total_qty"] == 10.0


def test_compute_calculations_multi_month(factories, db_session):
    """Счета в разных месяцах → отдельные строки."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()
    doc = factories.DocumentFactory.create(project=project)

    inv_jan = factories.InvoiceFactory.create(document=doc, date=date(2026, 1, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv_jan, material_class=mc, quantity=5.0, unit_price=9000.0, amount=45000.0,
    )
    inv_feb = factories.InvoiceFactory.create(document=doc, date=date(2026, 2, 20))
    factories.InvoiceItemFactory.create(
        invoice=inv_feb, material_class=mc, quantity=8.0, unit_price=8500.0, amount=68000.0,
    )

    result = compute_calculations(db_session, project.id)
    assert len(result) == 2

    periods = {r["period_start"].month for r in result}
    assert periods == {1, 2}


# =============================================================================
# Тесты новой методологии: агрегация на уровне счёта + calc_role
# =============================================================================


def test_delivery_not_shared_across_invoices(factories, db_session):
    """Доставка не перетекает между счетами. Доставка из счёта А не входит в avg_price В30 из счёта Б."""
    project = factories.ProjectFactory.create()
    mc_v40 = factories.MaterialClassFactory.create(name="В40", material_type_code="concrete")
    mc_v30 = factories.MaterialClassFactory.create(name="В30", material_type_code="concrete")
    doc = factories.DocumentFactory.create(project=project)

    # Счёт А: В40 + доставка
    inv_a = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv_a, material_class=mc_v40,
        quantity=10.0, unit_price=8000.0, amount=80000.0, vat_amount=16000.0,
    )
    factories.InvoiceItemFactory.create(
        invoice=inv_a, material_class=None,
        item_type="delivery", quantity=1.0, unit_price=40000.0, amount=40000.0, vat_amount=8000.0,
    )

    # Счёт Б: В30, доставки нет
    inv_b = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))
    factories.InvoiceItemFactory.create(
        invoice=inv_b, material_class=mc_v30,
        quantity=10.0, unit_price=7000.0, amount=70000.0, vat_amount=14000.0,
    )

    result = compute_calculations(db_session, project.id)
    assert len(result) == 2

    by_class = {r["material_class_id"]: r for r in result}

    # В40: mat_with_vat=96_000, shared = 48_000 × 1.0 = 48_000 → avg = 14_400
    r_v40 = by_class[mc_v40.id]
    assert r_v40["delivery_total"] == 48000.0
    assert r_v40["avg_price"] == 14400.0

    # В30: нет доставки — shared=0 → avg = 84_000 / 10 = 8_400
    r_v30 = by_class[mc_v30.id]
    assert r_v30["delivery_total"] == 0.0
    assert r_v30["avg_price"] == 8400.0


def test_additives_included_in_avg_price(factories, db_session):
    """Присадки (calc_role='additive') входят в avg_price пропорционально объёму."""
    project = factories.ProjectFactory.create()
    mc_v40 = factories.MaterialClassFactory.create(name="В40", material_type_code="concrete")
    mc_add = factories.MaterialClassFactory.create(
        name="Пластификатор", material_type_code="concrete", calc_role="additive",
    )
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))

    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc_v40,
        quantity=10.0, unit_price=8000.0, amount=80000.0, vat_amount=16000.0,
    )
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc_add,
        item_type="material", quantity=1.0, unit_price=5000.0, amount=5000.0, vat_amount=1000.0,
    )

    result = compute_calculations(db_session, project.id)

    # Только В40 — присадка не даёт отдельной строки в результате
    assert len(result) == 1
    row = result[0]
    assert row["material_class_id"] == mc_v40.id

    # additive_total = 6_000; share=1.0 → shared=6_000
    # avg = (96_000 + 6_000) / 10 = 10_200
    assert row["delivery_total"] == 6000.0
    assert row["avg_price"] == 10200.0


def test_exclude_items_not_in_avg_price(factories, db_session):
    """Позиции calc_role='exclude' (цементное молоко и пр.) не входят в avg_price."""
    project = factories.ProjectFactory.create()
    mc_v40 = factories.MaterialClassFactory.create(name="В40", material_type_code="concrete")
    mc_exc = factories.MaterialClassFactory.create(
        name="Цементное молоко", material_type_code="concrete", calc_role="exclude",
    )
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))

    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc_v40,
        quantity=10.0, unit_price=8000.0, amount=80000.0, vat_amount=16000.0,
    )
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc_exc,
        item_type="material", quantity=5.0, unit_price=2000.0, amount=10000.0, vat_amount=2000.0,
    )

    result = compute_calculations(db_session, project.id)

    # Только В40 — exclude не даёт строки
    assert len(result) == 1
    row = result[0]
    assert row["material_class_id"] == mc_v40.id

    # Ни доставки, ни присадок → shared=0
    # avg = 96_000 / 10 = 9_600
    assert row["delivery_total"] == 0.0
    assert row["avg_price"] == 9600.0


def test_exclude_items_not_in_denominator(factories, db_session):
    """Объём exclude-позиций не учитывается в знаменателе при распределении доставки."""
    project = factories.ProjectFactory.create()
    mc_v40 = factories.MaterialClassFactory.create(name="В40", material_type_code="concrete")
    mc_exc = factories.MaterialClassFactory.create(
        name="Цементное молоко", material_type_code="concrete", calc_role="exclude",
    )
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))

    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc_v40,
        quantity=100.0, unit_price=8000.0, amount=800000.0, vat_amount=160000.0,
    )
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc_exc,
        item_type="material", quantity=1.0, unit_price=8000.0, amount=8000.0, vat_amount=1600.0,
    )
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=None,
        item_type="delivery", quantity=1.0, unit_price=50000.0, amount=50000.0, vat_amount=10000.0,
    )

    result = compute_calculations(db_session, project.id)
    assert len(result) == 1
    row = result[0]

    # Знаменатель = 100 (не 101). share В40 = 100/100 = 1.0
    # delivery = 60_000. shared = 60_000 × 1.0 = 60_000
    # avg = (960_000 + 60_000) / 100 = 10_200
    assert row["delivery_total"] == 60000.0
    assert row["avg_price"] == 10200.0


def test_multiple_classes_in_one_invoice(factories, db_session):
    """При нескольких классах в одном счёте доставка распределяется пропорционально объёмам."""
    project = factories.ProjectFactory.create()
    mc_v40 = factories.MaterialClassFactory.create(name="В40", material_type_code="concrete")
    mc_v30 = factories.MaterialClassFactory.create(name="В30", material_type_code="concrete")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))

    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc_v40,
        quantity=60.0, unit_price=8000.0, amount=480000.0, vat_amount=96000.0,
    )
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc_v30,
        quantity=40.0, unit_price=7000.0, amount=280000.0, vat_amount=56000.0,
    )
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=None,
        item_type="delivery", quantity=1.0, unit_price=120000.0, amount=120000.0, vat_amount=24000.0,
    )

    result = compute_calculations(db_session, project.id)
    assert len(result) == 2

    by_class = {r["material_class_id"]: r for r in result}

    # base_qty = 100; delivery_total = 144_000
    # В40: share=0.6 → shared=86_400; avg=(576_000+86_400)/60=11_040
    r_v40 = by_class[mc_v40.id]
    assert r_v40["delivery_total"] == 86400.0
    assert r_v40["avg_price"] == 11040.0

    # В30: share=0.4 → shared=57_600; avg=(336_000+57_600)/40=9_840
    r_v30 = by_class[mc_v30.id]
    assert r_v30["delivery_total"] == 57600.0
    assert r_v30["avg_price"] == 9840.0


def test_invoice_without_base_material_does_not_crash(factories, db_session):
    """Счёт без base-материала (только доставка) не вызывает исключения, вклад = []."""
    project = factories.ProjectFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 15))

    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=None,
        item_type="delivery", quantity=1.0, unit_price=40000.0, amount=40000.0, vat_amount=8000.0,
    )

    result = compute_calculations(db_session, project.id)
    assert result == []


# =============================================================================
# Тесты _compute_supplier_project_deviation
# =============================================================================


def test_supplier_deviation_uses_latest_ref_price_no_period_filter(factories, db_session):
    """_compute_supplier_project_deviation использует самую свежую базовую цену по классу
    без привязки к периоду — в отличие от compute_calculations, которая фильтрует по периоду.

    Сценарий: счёт за 2025, есть две базовые цены — старая (2025) и новая (2026).
    Функция должна взять 2026 (более поздний period_start), а не 2025 (совпадает с датой счёта).
    """
    project = factories.ProjectFactory.create()
    supplier = factories.SupplierFactory.create()
    mc = factories.MaterialClassFactory.create(name="В40", material_type_code="concrete")
    doc = factories.DocumentFactory.create(project=project)

    inv = factories.InvoiceFactory.create(
        document=doc, date=date(2025, 6, 15), supplier_id=supplier.id,
    )
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc,
        quantity=10.0, unit_price=8000.0, amount=80000.0, vat_amount=16000.0,
    )

    # Новая базовая цена — период не совпадает с датой счёта
    # Создаётся первой, чтобы тест не мог пройти случайно из-за порядка вставки;
    # функция обязана явно сортировать по period_start DESC, а не по insertion order.
    factories.ReferencePriceFactory.create(
        project=project, material_class=mc, price=10000.0,
        period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
    )
    # Старая базовая цена — период совпадает с датой счёта
    factories.ReferencePriceFactory.create(
        project=project, material_class=mc, price=9000.0,
        period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
    )

    deviation_pct, deviation_amount = _compute_supplier_project_deviation(
        db_session, supplier.id, project.id
    )

    # avg_price = (80_000 + 16_000) / 10 = 9_600
    # Берётся самая свежая цена: 10_000 (2026), а не 9_000 (2025)
    # deviation_pct = (9_600 − 10_000) / 10_000 × 100 = −4.0
    # deviation_amount = (9_600 − 10_000) × 10 = −4_000
    assert deviation_pct == -4.0
    assert deviation_amount == -4000.0


def test_supplier_deviation_with_delivery_allocation(factories, db_session):
    """_compute_supplier_project_deviation правильно включает доставку в avg_price
    и считает отклонение через shared-cost аллокацию."""
    project = factories.ProjectFactory.create()
    supplier = factories.SupplierFactory.create()
    mc = factories.MaterialClassFactory.create(name="В40", material_type_code="concrete")
    factories.ReferencePriceFactory.create(
        project=project, material_class=mc, price=12000.0,
        period_start=date(2026, 1, 1), period_end=date(2026, 12, 31),
    )
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(
        document=doc, date=date(2026, 3, 15), supplier_id=supplier.id,
    )

    # В40: 10 м³ × 8_000 = 80_000, НДС 16_000 → mat_with_vat = 96_000
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc,
        quantity=10.0, unit_price=8000.0, amount=80000.0, vat_amount=16000.0,
    )
    # Доставка: 40_000, НДС 8_000 → shared = 48_000, share В40 = 1.0
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=None,
        item_type="delivery", quantity=1.0, unit_price=40000.0,
        amount=40000.0, vat_amount=8000.0,
    )

    deviation_pct, deviation_amount = _compute_supplier_project_deviation(
        db_session, supplier.id, project.id
    )

    # avg_price = (96_000 + 48_000) / 10 = 14_400
    # ref_price = 12_000
    # deviation_pct = (14_400 − 12_000) / 12_000 × 100 = 20.0
    # deviation_amount = (14_400 − 12_000) × 10 = 24_000
    assert deviation_pct == 20.0
    assert deviation_amount == 24000.0


def test_supplier_deviation_returns_none_when_no_invoices(factories, db_session):
    """Нет счетов поставщика по объекту → функция возвращает (None, None)."""
    project = factories.ProjectFactory.create()
    supplier = factories.SupplierFactory.create()

    deviation_pct, deviation_amount = _compute_supplier_project_deviation(
        db_session, supplier.id, project.id
    )

    assert deviation_pct is None
    assert deviation_amount is None


def test_supplier_deviation_returns_none_when_no_ref_prices(factories, db_session):
    """Нет базовых цен → функция возвращает (None, None), не 0."""
    project = factories.ProjectFactory.create()
    supplier = factories.SupplierFactory.create()
    mc = factories.MaterialClassFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(
        document=doc, date=date(2026, 3, 15), supplier_id=supplier.id,
    )
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=mc,
        quantity=10.0, unit_price=8000.0, amount=80000.0, vat_amount=16000.0,
    )

    deviation_pct, deviation_amount = _compute_supplier_project_deviation(
        db_session, supplier.id, project.id
    )

    assert deviation_pct is None
    assert deviation_amount is None


def test_get_or_create_material_class_invalid_calc_role_raises(db_session):
    """Неизвестный calc_role вызывает ValueError ещё до обращения к БД."""
    import pytest
    with pytest.raises(ValueError, match="calc_role"):
        get_or_create_material_class(
            db_session, name="Что-то", material_type="concrete", calc_role="bad_value",
        )


def test_get_or_create_material_class_calc_role_mismatch_logs_warning(factories, db_session):
    """При несовпадении calc_role у существующей записи функция возвращает её без изменений и логирует warning."""
    # Создаём класс с calc_role="base" напрямую
    mc_original = factories.MaterialClassFactory.create(
        name="Цементное молоко", material_type_code="concrete", calc_role="base",
    )

    with patch.object(crud_materials.logger, "warning") as mock_warn:
        mc_returned = get_or_create_material_class(
            db_session, name="Цементное молоко", material_type="concrete", calc_role="exclude",
        )

    # Возвращает существующую запись без изменений
    assert mc_returned.id == mc_original.id
    assert mc_returned.calc_role == "base"

    # logger.warning должен быть вызван, сообщение содержит "calc_role"
    mock_warn.assert_called_once()
    assert "calc_role" in mock_warn.call_args[0][0]

