# План: направления материалов — backend

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** параметр `direction` во всех эндпоинтах страницы объекта, новый shape summary с разбивкой по направлениям, разноска additive-классов внутри своего `material_type`.

**Architecture:** изменений схемы БД НЕТ (спека R5, ADR #12). Справочник направлений — существующий `material_types`; `direction` в API = `material_types.code`. Фильтр направления — строго на выходе расчёта (ADR #2): знаменатели разноски всегда по полному счёту. Доставка разносится на весь счёт, additive — только по base-классам своего типа (ADR #8).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (sync), pytest + factory_boy (`tests/factories.py`), Postgres.

**Спека:** `docs/superpowers/specs/2026-06-11-material-directions-design.md` (R5). Перед началом прочитай §5 (семантика) и §6 (API) целиком.

**Команды** (только через `just`, из корня репо; Windows-обёртка из CLAUDE.md):
- тесты: `just test-backend-unit` · `just test-backend-integration`
- линт: `just lint`

**Ветка:** `feat/material-directions-backend` от свежего `main`.

---

## Контекст для исполнителя (прочитать ДО кода)

| Что | Где |
|---|---|
| Расчёты: `compute_shared_shares`, `_aggregate_by_class`, `compute_calculations`, `compute_export_rows` | `backend/crud/calculations.py` |
| Summary/invoices/calculations/monthly роутер | `backend/routers/dashboard.py` |
| Поставщики проекта (`/{project_id}/suppliers`) | `backend/routers/projects.py:72-94` |
| Дубль механики разноски | `backend/crud/suppliers.py:280-438` (`_compute_supplier_project_deviation`) |
| Базовые цены, `_validate_ref_unit` | `backend/routers/reference_prices.py` |
| Экспорт, имя файла | `backend/routers/export.py:388-522` |
| Модели: `MaterialType(code,name,default_unit_id)`, `MaterialClass(material_type_id, calc_role)` | `backend/models.py` |
| Фабрики тестов (`material_type_code` Param у MaterialClassFactory!) | `backend/tests/factories.py` |

Терминология: `calc_role` — роль класса в расчёте avg_price (`base`/`additive`/`exclude`), НЕ «добавки». В коде и тестах использовать слово «additive».

ВАЖНО про фабрики: `InvoiceItemFactory` по умолчанию нормализован под м³. Для арматуры в тоннах задавай ЯВНО: `normalized_unit_id=_unit_id("TON")` — в тестах ниже это уже учтено хелпером `_rebar_item(...)`.

---

### Task 1: разноска additive по типу — `_aggregate_by_class`

**Files:**
- Modify: `backend/crud/calculations.py` (`_aggregate_by_class`, base_rows query в `compute_calculations`, additive query)
- Test: `backend/tests/unit/test_additive_allocation.py` (создать)

- [ ] **Step 1: Write the failing unit tests**

```python
"""Unit-тесты разноски shared-затрат (спека §5.4):
доставка — на весь счёт, additive — внутри своего material_type."""
from collections import namedtuple
from decimal import Decimal

from crud.calculations import _aggregate_by_class

# Строка base-материала: как из SQL-запроса (см. compute_calculations)
Row = namedtuple("Row", "invoice_id material_class_id mat_total mat_vat qty dimension symbol type_id")

CONCRETE, REBAR = 1, 2  # material_type_id


def test_mono_type_invoice_unchanged():
    """Моно-направленный счёт: additive+delivery достаются классам типа — побитово
    как старый общий котёл (регрессия текущего поведения)."""
    rows = [
        Row(10, 100, Decimal("80000"), Decimal("16000"), Decimal("10"), "volume", "м³", CONCRETE),
        Row(10, 101, Decimal("40000"), Decimal("8000"), Decimal("5"), "volume", "м³", CONCRETE),
    ]
    delivery = {10: Decimal("3000")}
    additive = {(10, CONCRETE): Decimal("1500")}
    contrib = _aggregate_by_class(rows, delivery, additive)
    # моно-размерность → доли по qty: 10/15 и 5/15; (3000+1500) в тех же долях
    assert contrib[100]["shared_with_vat"] == Decimal("4500") * Decimal("10") / Decimal("15")
    assert contrib[101]["shared_with_vat"] == Decimal("4500") * Decimal("5") / Decimal("15")


def test_mixed_invoice_additive_scoped_to_own_type():
    """Смешанный счёт: additive типа concrete входит только бетонным классам;
    delivery — всем (микс размерностей → по amount)."""
    rows = [
        Row(10, 100, Decimal("80000"), Decimal("16000"), Decimal("10"), "volume", "м³", CONCRETE),
        Row(10, 200, Decimal("20000"), Decimal("4000"), Decimal("2"), "mass", "т", REBAR),
    ]
    delivery = {10: Decimal("5000")}
    additive = {(10, CONCRETE): Decimal("1000")}
    contrib = _aggregate_by_class(rows, delivery, additive)
    # delivery по amount (80000 vs 20000): 4000 бетону, 1000 арматуре
    # additive только бетону (единственный класс типа → доля 1)
    assert contrib[100]["shared_with_vat"] == Decimal("4000") + Decimal("1000")
    assert contrib[200]["shared_with_vat"] == Decimal("1000")


def test_additive_without_own_type_base_rows_not_allocated():
    """Edge case §5.4: additive типа concrete в счёте только с base-классами rebar
    → не входит ни в чей avg_price."""
    rows = [
        Row(10, 200, Decimal("20000"), Decimal("4000"), Decimal("2"), "mass", "т", REBAR),
    ]
    delivery = {}
    additive = {(10, CONCRETE): Decimal("1000")}
    contrib = _aggregate_by_class(rows, delivery, additive)
    assert contrib[200]["shared_with_vat"] == Decimal("0")


def test_additive_two_types_independent():
    """Два типа с собственными additive: каждый котёл — только своим классам."""
    rows = [
        Row(10, 100, Decimal("80000"), Decimal("16000"), Decimal("10"), "volume", "м³", CONCRETE),
        Row(10, 200, Decimal("20000"), Decimal("4000"), Decimal("2"), "mass", "т", REBAR),
    ]
    additive = {(10, CONCRETE): Decimal("300"), (10, REBAR): Decimal("700")}
    contrib = _aggregate_by_class(rows, {}, additive)
    assert contrib[100]["shared_with_vat"] == Decimal("300")
    assert contrib[200]["shared_with_vat"] == Decimal("700")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test-backend-unit`
Expected: FAIL — `_aggregate_by_class() takes 2 positional arguments but 3 were given` (или ImportError при другой сигнатуре).

- [ ] **Step 3: Implement `_aggregate_by_class` (новая сигнатура)**

Заменить функцию в `backend/crud/calculations.py` целиком:

```python
def _aggregate_by_class(
    base_rows,
    delivery_per_invoice: dict[int, Decimal],
    additive_per_invoice_type: dict[tuple[int, int], Decimal],
) -> dict[int, dict]:
    """Distribute shared costs across base classes per invoice (spec §5.4).

    Delivery is invoice-wide (no direction on a delivery line). Additive-class
    costs are scoped to base classes of the SAME material_type within the
    invoice; an additive whose type has no base rows in the invoice is not
    allocated to anyone (honest refusal, spec §5.4 edge case).

    base_rows: rows with (invoice_id, material_class_id, mat_total, mat_vat,
      qty, dimension, symbol, type_id). qty is SUM(normalized_quantity);
      mat_total is SUM(amount) excl VAT.
    Returns dict[class_id -> {mat_with_vat, shared_with_vat, qty, dimensions,
      symbol, invoice_ids}].
    """
    from collections import defaultdict

    rows_by_invoice: dict[int, list] = defaultdict(list)
    for row in base_rows:
        rows_by_invoice[row.invoice_id].append(row)

    class_contrib: dict[int, dict] = {}
    for inv_id, rows in rows_by_invoice.items():
        # Per-ROW accumulation: material, qty, dimensions (a class may have >1
        # row when it spans dimensions — these MUST sum across rows).
        for row in rows:
            cid = row.material_class_id
            if cid not in class_contrib:
                class_contrib[cid] = {
                    "mat_with_vat": Decimal("0"),
                    "shared_with_vat": Decimal("0"),
                    "qty": Decimal("0"),
                    "dimensions": set(),   # >1 ⇒ intra-class dimension mix (guarded downstream)
                    "symbol": row.symbol,
                    "invoice_ids": set(),
                }
            class_contrib[cid]["mat_with_vat"] += row.mat_total + row.mat_vat
            class_contrib[cid]["qty"] += row.qty
            class_contrib[cid]["dimensions"].add(row.dimension)
            class_contrib[cid]["invoice_ids"].add(inv_id)

        # Delivery: invoice-wide shares (exactly ONCE per (invoice, class)).
        delivery_total = delivery_per_invoice.get(inv_id, Decimal("0"))
        if delivery_total:
            for cid, share in compute_shared_shares(rows).items():
                class_contrib[cid]["shared_with_vat"] += delivery_total * share

        # Additive: shares within base rows of the SAME material_type.
        rows_by_type: dict[int, list] = defaultdict(list)
        for row in rows:
            rows_by_type[row.type_id].append(row)
        for type_id, type_rows in rows_by_type.items():
            additive_total = additive_per_invoice_type.get((inv_id, type_id), Decimal("0"))
            if additive_total:
                for cid, share in compute_shared_shares(type_rows).items():
                    class_contrib[cid]["shared_with_vat"] += additive_total * share
    return class_contrib
```

- [ ] **Step 4: Run unit tests to verify they pass**

Run: `just test-backend-unit`
Expected: 4 новых теста PASS. Существующие unit-тесты, дёргающие `_aggregate_by_class` со старой сигнатурой (если есть, см. `tests/unit/`), обнови на новую сигнатуру: старый вызов `_aggregate_by_class(rows, shared)` эквивалентен `_aggregate_by_class(rows, shared, {})` для delivery-only сценариев.

- [ ] **Step 5: Обновить caller №1 — `compute_calculations`**

В `backend/crud/calculations.py`:

5a. В query `base_rows` (внутри цикла по месяцам) добавить колонку типа и group_by:

```python
            db.query(
                InvoiceItem.invoice_id,
                InvoiceItem.material_class_id,
                func.sum(InvoiceItem.amount).label("mat_total"),
                func.sum(func.coalesce(...)).label("mat_vat"),       # как было
                func.sum(InvoiceItem.normalized_quantity).label("qty"),
                UnitOfMeasure.dimension.label("dimension"),
                UnitOfMeasure.symbol.label("symbol"),
                MaterialClass.material_type_id.label("type_id"),     # NEW
            )
            ...
            .group_by(
                InvoiceItem.invoice_id, InvoiceItem.material_class_id,
                UnitOfMeasure.dimension, UnitOfMeasure.symbol,
                MaterialClass.material_type_id,                      # NEW (класс в одном типе — строки не дробит)
            )
```

5b. Additive-query — группировка по (счёт × тип), результат в `additive_per_invoice_type`:

```python
        additive_per_invoice_type: dict[tuple[int, int], Decimal] = {}
        for row in (
            db.query(
                InvoiceItem.invoice_id,
                MaterialClass.material_type_id.label("type_id"),
                func.sum(
                    InvoiceItem.amount + func.coalesce(
                        InvoiceItem.vat_amount,
                        InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100
                    )
                ).label("total_with_vat"),
            )
            .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .filter(
                InvoiceItem.invoice_id.in_(invoice_ids_month),
                InvoiceItem.item_type == "material",
                MaterialClass.calc_role == "additive",
            )
            .group_by(InvoiceItem.invoice_id, MaterialClass.material_type_id)
            .all()
        ):
            additive_per_invoice_type[(row.invoice_id, row.type_id)] = row.total_with_vat
```

5c. Убрать слияние `shared_per_invoice = {...}`; вызов:

```python
        class_contrib = _aggregate_by_class(base_rows, delivery_per_invoice, additive_per_invoice_type)
```

- [ ] **Step 6: Обновить caller №2 — `compute_export_rows`**

6a. В query `base_rows` — те же две строки (`type_id` в select и group_by), что в 5a.

6b. Заменить расчёт долей: delivery-доля — по всем строкам счёта (как сейчас), additive-доля — по строкам своего типа:

```python
    rows_by_invoice = defaultdict(list)
    for r in base_rows:
        rows_by_invoice[r.invoice_id].append(r)
    delivery_share_by_inv_class: dict[tuple[int, int], Decimal] = {}
    additive_share_by_inv_class: dict[tuple[int, int], Decimal] = {}
    for inv_id, rows in rows_by_invoice.items():
        for cid, share in compute_shared_shares(rows).items():
            delivery_share_by_inv_class[(inv_id, cid)] = share
        rows_by_type = defaultdict(list)
        for r in rows:
            rows_by_type[r.type_id].append(r)
        for type_rows in rows_by_type.values():
            for cid, share in compute_shared_shares(type_rows).items():
                additive_share_by_inv_class[(inv_id, cid)] = share  # класс в одном типе — ключ не конфликтует
```

6c. Additive-агрегаты — по (счёт × тип): `additive_per_inv_type` / `additive_excl_per_inv_type` (тот же query, что в 5b, плюс `func.sum(InvoiceItem.amount).label("excl_vat")`, group_by по счёту и типу).

6d. В цикле по `base_rows` аллокации:

```python
        delivery_share = delivery_share_by_inv_class.get((inv_id, cid), Decimal("0"))
        additive_share = additive_share_by_inv_class.get((inv_id, cid), Decimal("0"))
        delivery_alloc = delivery_per_inv.get(inv_id, Decimal("0")) * delivery_share
        additive_alloc = additive_per_inv_type.get((inv_id, br.type_id), Decimal("0")) * additive_share
        delivery_excl_alloc = delivery_excl_per_inv.get(inv_id, Decimal("0")) * delivery_share
        additive_excl_alloc = additive_excl_per_inv_type.get((inv_id, br.type_id), Decimal("0")) * additive_share
```

- [ ] **Step 7: Обновить caller №3 — `_compute_supplier_project_deviation`** (`backend/crud/suppliers.py:280`)

- в `base_rows` query — `MaterialClass.material_type_id.label("type_id")` в select и group_by (как 5a);
- первый цикл (delivery) пишет в `delivery_per_invoice: dict[int, Decimal]` (переименовать `shared_per_invoice`);
- второй цикл (additive) — group_by по (счёт × тип), пишет в `additive_per_invoice_type: dict[tuple[int, int], Decimal]` (как 5b);
- вызов: `_aggregate_by_class(base_rows, delivery_per_invoice, additive_per_invoice_type)`.

- [ ] **Step 8: Интеграционный тест разноски** — Create: `backend/tests/integration/test_dashboard_directions.py` (первые тесты файла):

```python
from datetime import date
from decimal import Decimal


def _rebar_class(factories, name="А500С Ø12"):
    return factories.MaterialClassFactory.create(material_type_code="rebar", name=name)


def _rebar_item(factories, invoice, mc, qty, unit_price):
    """Позиция арматуры в тоннах — normalized_* задаём явно (см. факторку)."""
    from tests.factories import _unit_id
    return factories.InvoiceItemFactory.create(
        invoice=invoice, material_class=mc, item_type="material",
        quantity=qty, raw_unit="т", unit_price=unit_price,
        normalized_unit_id=_unit_id("TON"), normalized_quantity=Decimal(str(qty)),
    )


def test_additive_scoped_to_own_type_in_mixed_invoice(client, factories):
    """§5.4: additive типа concrete в смешанном счёте входит только бетону;
    у арматуры — только доля доставки."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    plasticizer = factories.MaterialClassFactory.create(name="Пластификатор", calc_role="additive")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)               # бетон 80000 (+20% НДС)
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)   # арматура 20000 (+НДС)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=plasticizer, item_type="material",
        quantity=1, unit_price=1000, amount=1000)                 # additive concrete 1000 (+НДС 200)
    factories.InvoiceItemFactory.create(
        invoice=inv, item_type="delivery", material_class=None,
        quantity=1, unit_price=5000, amount=5000)                 # доставка 5000 (+НДС 1000)

    rows = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()
    by_name = {r["material_class_name"]: r for r in rows}
    # mixed dimensions → delivery по amount (80000 vs 20000 без НДС): 0.8 / 0.2 от 6000 с НДС
    # additive (1200 с НДС) — только бетону
    assert by_name["В25"]["delivery_total"] == 4800.0 + 1200.0
    assert by_name["А500С Ø12"]["delivery_total"] == 1200.0


def test_additive_without_own_type_base_not_allocated(client, factories):
    """Edge §5.4: additive concrete в счёте только с base rebar — никому."""
    project = factories.ProjectFactory.create()
    rebar = _rebar_class(factories)
    plasticizer = factories.MaterialClassFactory.create(name="Пластификатор", calc_role="additive")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=plasticizer, item_type="material",
        quantity=1, unit_price=1000, amount=1000)

    rows = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()
    assert len(rows) == 1
    assert rows[0]["delivery_total"] == 0.0  # additive не разнесён


def test_supplier_project_deviation_additive_scoped(client, factories):
    """§5.4: та же разноска в _compute_supplier_project_deviation (карточка поставщика).
    Additive concrete не должен удорожать арматуру в отклонении поставщика."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    plasticizer = factories.MaterialClassFactory.create(name="Пластификатор", calc_role="additive")
    supplier = factories.SupplierFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, supplier_id=supplier.id, date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=plasticizer, item_type="material",
        quantity=1, unit_price=1000, amount=1000)
    # Базовая цена ровно по факту арматуры: 12000 ₽/т с НДС → отклонение арматуры = 0,
    # если additive (1200 с НДС) НЕ протёк в её avg_price.
    from tests.factories import _unit_id
    factories.ReferencePriceFactory.create(
        project=project, material_class=rebar, price=12000.0, unit_id=_unit_id("TON"))

    rows = client.get(f"/api/suppliers/{supplier.id}/projects").json()
    stats = next(r for r in rows if r["project_id"] == project.id)
    assert stats["deviation_amount"] == 0.0
```

- [ ] **Step 9: Run all backend tests (регрессия моно-счетов)**

Run: `just test-backend-unit` затем `just test-backend-integration`
Expected: ВСЕ существующие тесты зелёные без правок ожиданий (моно-направленные счета — побитово прежнее поведение). Новые тесты PASS. Если существующий тест расчётов упал — это сигнал ошибки в разноске, НЕ повод править ожидания (спека §10, риск №2).

- [ ] **Step 10: Commit**

```bash
git add backend/crud/calculations.py backend/crud/suppliers.py backend/tests/unit/test_additive_allocation.py backend/tests/integration/test_dashboard_directions.py
git commit -m "feat(directions): scope additive-class allocation to its material_type (spec §5.4, ADR #8)"
```

---

### Task 2: фильтр направления и поле `direction` в расчётах

**Files:**
- Modify: `backend/crud/calculations.py` (`compute_calculations`, `compute_export_rows`)
- Modify: `backend/routers/dashboard.py` (`/calculations`)
- Test: `backend/tests/integration/test_dashboard_directions.py`

- [ ] **Step 1: Write the failing tests** (добавить в `test_dashboard_directions.py`)

```python
def test_calculations_direction_field_and_filter(client, factories):
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)

    all_rows = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()
    assert {r["direction"] for r in all_rows} == {"concrete", "rebar"}

    resp = client.get(f"/api/dashboard/calculations?project_id={project.id}&direction=rebar")
    rows = resp.json()
    assert [r["material_class_name"] for r in rows] == ["А500С Ø12"]


def test_calculations_direction_filter_does_not_change_class_rows(client, factories):
    """Тест-страж ADR #2: фильтр на выходе — поклассовые цифры идентичны."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)
    factories.InvoiceItemFactory.create(
        invoice=inv, item_type="delivery", material_class=None,
        quantity=1, unit_price=5000, amount=5000)
    factories.ReferencePriceFactory.create(project=project, material_class=concrete, price=8000.0)

    full = client.get(f"/api/dashboard/calculations?project_id={project.id}").json()
    scoped = client.get(f"/api/dashboard/calculations?project_id={project.id}&direction=concrete").json()
    full_concrete = [r for r in full if r["direction"] == "concrete"]
    assert scoped == full_concrete  # включая avg_price/deviation/compensation


def test_calculations_unknown_direction_422(client, factories):
    project = factories.ProjectFactory.create()
    resp = client.get(f"/api/dashboard/calculations?project_id={project.id}&direction=bricks")
    assert resp.status_code == 422
```

- [ ] **Step 2: Run** `just test-backend-integration` → FAIL (нет поля `direction`, нет параметра).

- [ ] **Step 3: Implement — `compute_calculations`**

3a. Сигнатура: добавить `direction_type_id: int | None = None` (после `excluded_supplier_ids`).

3b. Карта кодов типов один раз перед циклом по месяцам:

```python
    from models import MaterialType  # уже импортирован models — добавить имя в импорт файла
    type_code_map: dict[int, str] = {t.id: t.code for t in db.query(MaterialType).all()}
```

3c. В цикле выдачи (рядом с фильтром `material_class_id`, строка ~287):

```python
            if direction_type_id is not None and class_type_id_map.get(cid) != direction_type_id:
                continue
```

3d. В результирующий dict добавить `"direction": type_code_map.get(class_type_id_map.get(cid), "other"),`.

- [ ] **Step 4: Implement — `compute_export_rows`**: параметр `direction_type_id: int | None = None`; фильтр на выходе рядом с `material_class_id` (строка ~531): `if direction_type_id is not None and br.type_id != direction_type_id: continue`.

- [ ] **Step 5: Implement — роутер** `backend/routers/dashboard.py`:

5a. Хелпер (вверху файла, после `router = APIRouter()`):

```python
from fastapi import HTTPException
from models import MaterialType


def _resolve_direction_type(db: Session, direction: str | None) -> MaterialType | None:
    """code направления → MaterialType. Неизвестный code → 422 (спека §6)."""
    if direction is None:
        return None
    mt = db.query(MaterialType).filter(MaterialType.code == direction).first()
    if mt is None:
        raise HTTPException(status_code=422, detail=f"Неизвестное направление: {direction}")
    return mt
```

5b. `/calculations`: параметр `direction: str | None = None`; `mt = _resolve_direction_type(db, direction)`; прокинуть `direction_type_id=mt.id if mt else None` в оба вызова `compute_calculations` (ветки с/без project_id); в сериализацию строк добавить `"direction": r["direction"],`.

- [ ] **Step 6: Run** `just test-backend-integration` → PASS (новые и старые).

- [ ] **Step 7: Commit**

```bash
git add backend/crud/calculations.py backend/routers/dashboard.py backend/tests/integration/test_dashboard_directions.py
git commit -m "feat(directions): direction filter (output-stage) and direction field in calculations"
```

---

### Task 3: summary — новый shape с разбивкой по направлениям

**Files:**
- Modify: `backend/routers/dashboard.py` (`get_project_summary` + новый хелпер `_direction_summaries`)
- Test: `backend/tests/integration/test_dashboard_directions.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest


def _mixed_project(factories):
    """Объект: смешанный счёт (бетон+арматура+доставка+прочее) + чисто бетонный счёт
    + счёт без direction-позиций (только доставка)."""
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    doc = factories.DocumentFactory.create(project=project)

    inv_mixed = factories.InvoiceFactory.create(document=doc, date=date(2026, 3, 10))
    factories.InvoiceItemFactory.create(
        invoice=inv_mixed, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)               # 96000 с НДС
    _rebar_item(factories, inv_mixed, rebar, qty=2, unit_price=10000)  # 24000 с НДС
    factories.InvoiceItemFactory.create(
        invoice=inv_mixed, item_type="delivery", material_class=None,
        quantity=1, unit_price=5000, amount=5000)                 # 6000 с НДС
    factories.InvoiceItemFactory.create(
        invoice=inv_mixed, item_type="other", material_class=None,
        quantity=1, unit_price=2000, amount=2000)                 # 2400 с НДС

    inv_concrete = factories.InvoiceFactory.create(document=doc, date=date(2026, 4, 5))
    factories.InvoiceItemFactory.create(
        invoice=inv_concrete, material_class=concrete, item_type="material",
        quantity=5, unit_price=8200, amount=41000)                # 49200 с НДС

    inv_delivery_only = factories.InvoiceFactory.create(document=doc, date=date(2026, 4, 7))
    factories.InvoiceItemFactory.create(
        invoice=inv_delivery_only, item_type="delivery", material_class=None,
        quantity=1, unit_price=1000, amount=1000)                 # 1200 с НДС

    return project, concrete, rebar


def test_summary_directions_and_invariant(client, factories):
    project, *_ = _mixed_project(factories)
    body = client.get(f"/api/dashboard/summary?project_id={project.id}").json()

    codes = [d["code"] for d in body["directions"]]
    assert codes == ["concrete", "rebar"]          # порядок — по id типа; other отсутствует
    by_code = {d["code"]: d for d in body["directions"]}
    assert by_code["concrete"]["turnover"] == 96000.0 + 49200.0
    assert by_code["rebar"]["turnover"] == 24000.0
    assert by_code["concrete"]["volume"] == 15.0
    assert by_code["concrete"]["volume_unit"] == "м³"
    assert by_code["rebar"]["volume"] == 2.0
    assert by_code["rebar"]["volume_unit"] == "т"
    assert by_code["concrete"]["invoice_count"] == 2
    assert by_code["rebar"]["invoice_count"] == 1
    assert by_code["concrete"]["mixed_invoice_count"] == 1
    assert body["mixed_invoice_count"] == 1
    assert body["other_invoice_count"] == 1        # счёт из одной доставки
    assert body["delivery_total"] == 6000.0 + 1200.0
    assert body["other_total"] == 2400.0
    # ИНВАРИАНТ §5.1 — на сериализованных значениях. Каждое слагаемое округлено
    # до копеек НЕЗАВИСИМО, поэтому сумма округлённых может разойтись с округлённой
    # суммой на ±копейки — approx, не точное равенство. Точная (Decimal, до round)
    # партиция проверяется отдельным тестом test_summary_material_partition_exact.
    assert body["total_amount"] == pytest.approx(
        sum(d["turnover"] for d in body["directions"])
        + body["delivery_total"] + body["other_total"],
        abs=0.05,
    )


def test_summary_material_partition_exact(client, factories, db_session):
    """Точный Decimal-инвариант §5.1 ДО сериализации: группировка material-позиций
    по типу (направления / other / NULL-класс) разбивает их без пересечений и
    пропусков — суммы сходятся точно. Значит, расхождение сериализованного
    инварианта может дать только независимое округление, не потеря позиций."""
    project, *_ = _mixed_project(factories)
    from sqlalchemy import func, literal
    from models import Document, Invoice, InvoiceItem, MaterialClass, MaterialType

    vat = func.coalesce(
        InvoiceItem.vat_amount,
        InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100,
    )
    grouped = (
        db_session.query(MaterialType.code, func.sum(InvoiceItem.amount + vat))
        .select_from(InvoiceItem)
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .outerjoin(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .outerjoin(MaterialType, MaterialClass.material_type_id == MaterialType.id)
        .filter(Document.project_id == project.id, InvoiceItem.item_type == "material")
        .group_by(MaterialType.code)
        .all()
    )
    total_material = (
        db_session.query(func.sum(InvoiceItem.amount + vat))
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .filter(Document.project_id == project.id, InvoiceItem.item_type == "material")
        .scalar()
    )
    assert sum((row[1] for row in grouped), Decimal("0")) == total_material  # точно, без round


def test_summary_other_type_class_goes_to_other_total(client, factories):
    """ADR #9: классы типа other — в other_total, направления не образуют."""
    project = factories.ProjectFactory.create()
    misc = factories.MaterialClassFactory.create(material_type_code="other", name="Крепёж")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=misc, item_type="material",
        quantity=1, unit_price=3000, amount=3000)

    body = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    assert body["directions"] == []
    assert body["other_total"] == 3600.0
    assert body["other_invoice_count"] == 1
    assert body["mixed_invoice_count"] == 0


def test_summary_concrete_only_matches_legacy_fields(client, factories):
    """Критерий приёмки #1: моно-бетонный объект — directions согласован со старыми полями."""
    project = factories.ProjectFactory.create()
    mc = factories.MaterialClassFactory.create()
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc)
    factories.InvoiceItemFactory.create(invoice=inv, material_class=mc, item_type="material",
                                        quantity=5, amount=40000)
    body = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    assert [d["code"] for d in body["directions"]] == ["concrete"]
    d = body["directions"][0]
    assert d["turnover"] == body["material_amount"]
    assert d["volume"] == 5.0
    assert d["invoice_count"] == body["invoice_count"]
    assert body["mixed_invoice_count"] == 0 and body["other_invoice_count"] == 0


def test_summary_overpayment_per_direction_sums_to_full(client, factories):
    project, concrete, rebar = _mixed_project(factories)
    factories.ReferencePriceFactory.create(project=project, material_class=concrete, price=8000.0)
    from tests.factories import _unit_id
    factories.ReferencePriceFactory.create(
        project=project, material_class=rebar, price=10000.0, unit_id=_unit_id("TON"))

    body = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    by_code = {d["code"]: d for d in body["directions"]}
    overpayments = [d["overpayment"] for d in body["directions"] if d["overpayment"] is not None]
    # Слагаемые округлены независимо при сериализации → approx (та же причина,
    # что у инварианта оборота; источник один — calc_rows, потерь быть не может).
    assert sum(overpayments) == pytest.approx(body["full_deviation_amount"], abs=0.05)
    assert by_code["concrete"]["overpayment"] is not None


def test_summary_volume_excluded_count(client, factories):
    """§5.2: арматура в пог.м (length) не входит в объём «т» и попадает в счётчик."""
    project = factories.ProjectFactory.create()
    rebar = _rebar_class(factories)
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc)
    _rebar_item(factories, inv, rebar, qty=2, unit_price=10000)   # 2 т — входит
    from tests.factories import _unit_id
    factories.InvoiceItemFactory.create(                          # 100 пог.м — НЕ входит
        invoice=inv, material_class=rebar, item_type="material",
        quantity=100, raw_unit="м", unit_price=50,
        normalized_unit_id=_unit_id("M"), normalized_quantity=Decimal("100"))

    body = client.get(f"/api/dashboard/summary?project_id={project.id}").json()
    d = body["directions"][0]
    assert d["volume"] == 2.0
    assert d["volume_excluded_count"] == 1
```

- [ ] **Step 2: Run** `just test-backend-integration` → FAIL (нет полей).

- [ ] **Step 3: Implement — хелпер `_direction_summaries`** в `backend/routers/dashboard.py`:

```python
from sqlalchemy.orm import Query

from models import MaterialClass, MaterialType, UnitOfMeasure


def _direction_summaries(db: Session, project_id: int, excl_filter, calc_rows: list[dict]) -> dict:
    """Разбивка summary по направлениям (спека §5.1–§5.5, §6.1).

    excl_filter — функция, добавляющая фильтр исключённых поставщиков (как в summary).
    calc_rows — строки compute_calculations за полный период (для overpayment).
    Возвращает {directions, mixed_invoice_count, directed_invoice_ids,
    other_material_total}: directed_invoice_ids нужен вызывающему для
    other_invoice_count (= invoice_count - len(...)), other_material_total
    доливается в other_total."""
    types = db.query(MaterialType).order_by(MaterialType.id).all()
    direction_types = [t for t in types if t.code != "other"]   # ADR #9
    vat_expr = func.coalesce(
        InvoiceItem.vat_amount,
        InvoiceItem.amount * func.coalesce(Invoice.vat_rate, literal(Decimal("20.0"))) / 100,
    )

    # 1) Оборот по типам (позиционно, §5.1). outerjoin: NULL-класс → type_id IS NULL.
    turnover_rows = excl_filter(
        db.query(
            MaterialClass.material_type_id.label("type_id"),
            func.sum(InvoiceItem.amount + vat_expr).label("turnover"),
        )
        .select_from(InvoiceItem)
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .outerjoin(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .filter(Document.project_id == project_id, InvoiceItem.item_type == "material")
    ).group_by(MaterialClass.material_type_id).all()
    turnover_by_type = {r.type_id: r.turnover or Decimal("0") for r in turnover_rows}

    # 2) Объём по типам: только base, размерность = размерности default_unit (§5.2).
    #    outerjoin к units: ненормализованные строки → dimension IS NULL → в excluded.
    vol_rows = excl_filter(
        db.query(
            MaterialClass.material_type_id.label("type_id"),
            UnitOfMeasure.dimension.label("dimension"),
            func.sum(InvoiceItem.normalized_quantity).label("qty"),
            func.count(InvoiceItem.id).label("position_count"),
        )
        .select_from(InvoiceItem)
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .join(Document, Invoice.document_id == Document.id)
        .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
        .outerjoin(UnitOfMeasure, InvoiceItem.normalized_unit_id == UnitOfMeasure.id)
        .filter(
            Document.project_id == project_id,
            InvoiceItem.item_type == "material",
            MaterialClass.calc_role == "base",
        )
    ).group_by(MaterialClass.material_type_id, UnitOfMeasure.dimension).all()

    # 3) Счета по типам + смешанность (§5.5) — только direction-типы.
    direction_type_ids = [t.id for t in direction_types]
    inv_type_rows = []
    if direction_type_ids:
        inv_type_rows = excl_filter(
            db.query(Invoice.id.label("inv_id"), MaterialClass.material_type_id.label("type_id"))
            .join(Document, Invoice.document_id == Document.id)
            .join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .filter(
                Document.project_id == project_id,
                InvoiceItem.item_type == "material",
                MaterialClass.material_type_id.in_(direction_type_ids),
            )
            .distinct()
        ).all()
    types_by_invoice: dict[int, set[int]] = {}
    for r in inv_type_rows:
        types_by_invoice.setdefault(r.inv_id, set()).add(r.type_id)
    mixed_invoice_ids = {inv for inv, s in types_by_invoice.items() if len(s) >= 2}

    # 4) Переплата по направлениям — из УЖЕ посчитанных calc_rows (ноль лишних прогонов).
    overpayment_by_code: dict[str, Decimal] = {}
    has_ref_by_code: set[str] = set()
    for r in calc_rows:
        if r["deviation_amount"] is not None:
            has_ref_by_code.add(r["direction"])
            overpayment_by_code[r["direction"]] = (
                overpayment_by_code.get(r["direction"], Decimal("0")) + Decimal(str(r["deviation_amount"]))
            )

    directions = []
    for t in direction_types:
        invoice_ids = {inv for inv, s in types_by_invoice.items() if t.id in s}
        if not invoice_ids and not turnover_by_type.get(t.id):
            continue  # направление без данных не показывается (§3.1)
        default_dim = t.default_unit.dimension if t.default_unit else None
        volume = Decimal("0")
        excluded = 0
        for vr in vol_rows:
            if vr.type_id != t.id:
                continue
            if default_dim is not None and vr.dimension == default_dim:
                volume += vr.qty or Decimal("0")
            else:
                excluded += vr.position_count
        directions.append({
            "code": t.code,
            "name": t.name,
            "turnover": round(float(turnover_by_type.get(t.id, 0) or 0), 2),
            "overpayment": (
                round(float(overpayment_by_code[t.code]), 2) if t.code in has_ref_by_code else None
            ),
            "volume": round(float(volume), 2) if t.default_unit else None,
            "volume_unit": t.default_unit.symbol if t.default_unit else None,
            "volume_excluded_count": excluded,
            "invoice_count": len(invoice_ids),
            "mixed_invoice_count": len(invoice_ids & mixed_invoice_ids),
        })

    # other_total долив (§5.1): material-позиции типа other + позиции без класса
    other_type_ids = [t.id for t in types if t.code == "other"]
    other_material_total = sum(
        (v for k, v in turnover_by_type.items() if k is None or k in other_type_ids),
        Decimal("0"),
    )
    return {
        "directions": directions,
        "mixed_invoice_count": len(mixed_invoice_ids),
        "directed_invoice_ids": set(types_by_invoice.keys()),
        "other_material_total": other_material_total,
    }
```

- [ ] **Step 4: Implement — рефактор `get_project_summary`**:

4a. Заменить блок `full_deviation` (строки ~65–76): вместо `compute_full_deviation` — прямой вызов `compute_calculations` (тот же период-нормализованный диапазон), `calc_rows` сохранить.

> **ПРЕДОХРАНИТЕЛЬ (обязателен):** перед заменой прочитай тело `compute_full_deviation`
> (`crud/calculations.py:346-358`) и воспроизведи его агрегацию БУКВАЛЬНО — это
> главная цифра продукта. Ожидаемая семантика: делегирование в `compute_calculations`
> с теми же аргументами + `round(sum(непустых deviation_amount), 2)`, `None` если
> непустых нет. Если в актуальном коде найдёшь ЛЮБОЕ другое отличие (иная нормализация
> дат, клампинг, доп. фильтр) — ОСТАНОВИСЬ и доложи, не подгоняй молча.

```python
    calc_rows: list[dict] = []
    full_deviation = None
    if first_invoice_date and last_invoice_date:
        period_start = first_invoice_date.replace(day=1)
        last_day = monthrange(last_invoice_date.year, last_invoice_date.month)[1]
        period_end = last_invoice_date.replace(day=last_day)
        calc_rows = compute_calculations(
            db, project_id, period_start, period_end,
            excluded_supplier_ids=excluded or None,
        )
        amounts = [r["deviation_amount"] for r in calc_rows if r["deviation_amount"] is not None]
        full_deviation = round(sum(amounts), 2) if amounts else None
```

(Импорт `compute_full_deviation` в этом файле больше не нужен — убрать из импорта, сама функция остаётся для других потребителей.)

4b. После него: `dir_data = _direction_summaries(db, project_id, _excl_filter, calc_rows)`.

4c. В return добавить (старые поля НЕ трогать):

```python
        "directions": dir_data["directions"],
        "mixed_invoice_count": dir_data["mixed_invoice_count"],
        "other_invoice_count": (invoice_count or 0) - len(dir_data["directed_invoice_ids"]),
        "delivery_total": round(by_type.get("delivery", 0), 2),
        "other_total": round(by_type.get("other", 0) + float(dir_data["other_material_total"]), 2),
```

ВНИМАНИЕ: `material_amount`/`other_amount` (legacy) не меняются. Инвариант держится потому, что `turnover` направлений + `other_material_total` = весь `material`-оборот, а `delivery_total`/`item_type='other'` — отдельные группы.

- [ ] **Step 5: Run** `just test-backend-integration` → все PASS (включая старые summary-тесты: `test_summary_empty` получит `directions == []`, `other_invoice_count == 0` — проверить, что старые ассерты не ломаются; они проверяют только старые поля).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/dashboard.py backend/tests/integration/test_dashboard_directions.py
git commit -m "feat(directions): summary directions breakdown, invariant-safe totals (spec §6.1)"
```

---

### Task 4: `direction` в `/dashboard/invoices` и `/dashboard/monthly-summary`

**Files:**
- Modify: `backend/routers/dashboard.py`
- Test: `backend/tests/integration/test_dashboard_directions.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_invoices_direction_filter_mixed_visible_in_both(client, factories):
    """Критерий #2: смешанный счёт виден в обоих направлениях, целиком."""
    project, *_ = _mixed_project(factories)
    base = f"/api/dashboard/invoices?project_id={project.id}"
    all_ids = {i["id"] for i in client.get(base).json()}
    concrete_ids = {i["id"] for i in client.get(base + "&direction=concrete").json()}
    rebar_ids = {i["id"] for i in client.get(base + "&direction=rebar").json()}
    assert len(all_ids) == 3
    assert len(concrete_ids) == 2          # смешанный + чисто бетонный
    assert len(rebar_ids) == 1             # только смешанный
    assert rebar_ids < concrete_ids | rebar_ids
    mixed_id = next(iter(rebar_ids))
    mixed_inv = next(i for i in client.get(base + "&direction=rebar").json() if i["id"] == mixed_id)
    assert len(mixed_inv["items"]) == 4    # документ целиком, со всеми позициями


def test_monthly_summary_direction_scoped(client, factories):
    project, *_ = _mixed_project(factories)
    base = f"/api/dashboard/monthly-summary?project_id={project.id}"
    rows = client.get(base + "&direction=concrete").json()
    by_month = {(r["year"], r["month"]): r for r in rows}
    assert by_month[(2026, 3)]["total_amount"] == 96000.0   # только бетонные позиции
    assert by_month[(2026, 3)]["total_qty"] == 10.0
    assert by_month[(2026, 3)]["invoice_count"] == 1
    assert by_month[(2026, 3)]["volume_unit"] == "м³"
    assert by_month[(2026, 4)]["total_amount"] == 49200.0
    assert (2026, 4) in by_month and by_month[(2026, 4)]["invoice_count"] == 1
    # счёт «только доставка» (апрель) в направлении не существует
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement `/invoices`** — параметр `direction: str | None = None`; после построения query:

```python
    mt = _resolve_direction_type(db, direction)
    if mt is not None:
        direction_exists = (
            db.query(InvoiceItem.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .filter(
                InvoiceItem.invoice_id == Invoice.id,
                InvoiceItem.item_type == "material",
                MaterialClass.material_type_id == mt.id,
            )
            .exists()
        )
        invoices = ( ... .filter(direction_exists) ... )   # добавить .filter(direction_exists) в существующий query
```

(Импортировать `MaterialClass` в `routers/dashboard.py`.)

- [ ] **Step 4: Implement `/monthly-summary`** — параметр `direction: str | None = None`. Без параметра — текущий код без изменений (плюс `"volume_unit": None` в строках). С параметром (`mt = _resolve_direction_type(...)`) — два скоупнутых агрегата, слитых по (year, month); `distinct`/`or_` уже импортированы в модуле:

```python
    if mt is not None:
        default_dim = mt.default_unit.dimension if mt.default_unit else None
        # Оборот направления (позиционно) + счета
        amount_q = (
            db.query(
                year_expr.label("year"), month_expr.label("month"),
                func.sum(InvoiceItem.amount + vat_expr).label("total_amount"),
                func.count(distinct(Invoice.id)).label("invoice_count"),
            )
            .join(Document, Invoice.document_id == Document.id)
            .join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .filter(
                Document.project_id == project_id,
                InvoiceItem.item_type == "material",
                MaterialClass.material_type_id == mt.id,
            )
        )
        # Объём: base + совпадение размерности (§5.2)
        qty_q = (
            db.query(
                year_expr.label("year"), month_expr.label("month"),
                func.sum(InvoiceItem.normalized_quantity).label("total_qty"),
            )
            .join(Document, Invoice.document_id == Document.id)
            .join(InvoiceItem, InvoiceItem.invoice_id == Invoice.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .join(UnitOfMeasure, InvoiceItem.normalized_unit_id == UnitOfMeasure.id)
            .filter(
                Document.project_id == project_id,
                InvoiceItem.item_type == "material",
                MaterialClass.material_type_id == mt.id,
                MaterialClass.calc_role == "base",
                UnitOfMeasure.dimension == default_dim,
            )
        )
        if excluded:
            excl = or_(Invoice.supplier_id.is_(None), Invoice.supplier_id.notin_(excluded))
            amount_q = amount_q.filter(excl)
            qty_q = qty_q.filter(excl)
        amount_rows = amount_q.group_by(year_expr, month_expr).order_by(year_expr, month_expr).all()
        qty_by_month = {
            (int(r.year), int(r.month)): r.total_qty
            for r in qty_q.group_by(year_expr, month_expr).all()
        }
        unit_symbol = mt.default_unit.symbol if mt.default_unit else None
        return [
            {
                "year": int(r.year),
                "month": int(r.month),
                "total_amount": round(float(r.total_amount or 0), 2),
                "total_qty": round(float(qty_by_month.get((int(r.year), int(r.month)), 0) or 0), 2),
                "invoice_count": int(r.invoice_count),
                "volume_unit": unit_symbol,
            }
            for r in amount_rows
        ]
```

`vat_expr` — тот же `coalesce`, что в текущем коде. В ответ каждой строки добавить `"volume_unit": mt.default_unit.symbol if (mt and mt.default_unit) else None` (в безпараметрной ветке — `"volume_unit": None`).

- [ ] **Step 5: Run** → PASS. **Step 6: Commit**

```bash
git add backend/routers/dashboard.py backend/tests/integration/test_dashboard_directions.py
git commit -m "feat(directions): direction param in dashboard invoices and monthly-summary"
```

---

### Task 5: `direction` в поставщиках проекта и базовых ценах + запрет цен для типа `other`

**Files:**
- Modify: `backend/routers/projects.py` (`list_project_suppliers`)
- Modify: `backend/routers/reference_prices.py` (GET-фильтр, `_validate_ref_unit`)
- Modify: `backend/crud/projects.py` (`get_reference_prices` — параметр `material_type_code`)
- Test: `backend/tests/integration/test_dashboard_directions.py`, `backend/tests/integration/test_reference_prices.py` (если файла нет — положить тесты цен в test_dashboard_directions.py)

- [ ] **Step 1: Write the failing tests**

```python
def test_project_suppliers_direction_scoped(client, factories):
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    sup_a = factories.SupplierFactory.create(name="БетонТорг")
    sup_b = factories.SupplierFactory.create(name="МеталлБаза")
    doc = factories.DocumentFactory.create(project=project)
    inv_a = factories.InvoiceFactory.create(document=doc, supplier_id=sup_a.id)
    factories.InvoiceItemFactory.create(invoice=inv_a, material_class=concrete,
                                        item_type="material", quantity=5, amount=40000)
    inv_b = factories.InvoiceFactory.create(document=doc, supplier_id=sup_b.id)
    _rebar_item(factories, inv_b, rebar, qty=2, unit_price=10000)

    url = f"/api/projects/{project.id}/suppliers"
    assert {s["name"] for s in client.get(url).json()} == {"БетонТорг", "МеталлБаза"}
    concrete_rows = client.get(url + "?direction=concrete").json()
    assert [s["name"] for s in concrete_rows] == ["БетонТорг"]
    assert concrete_rows[0]["invoice_count"] == 1


def test_reference_prices_direction_filter(client, factories):
    project = factories.ProjectFactory.create()
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = _rebar_class(factories)
    factories.ReferencePriceFactory.create(project=project, material_class=concrete)
    from tests.factories import _unit_id
    factories.ReferencePriceFactory.create(
        project=project, material_class=rebar, unit_id=_unit_id("TON"), price=10000.0)

    rows = client.get(f"/api/reference-prices?project_id={project.id}&direction=rebar").json()
    assert [r["material_type"] for r in rows] == ["rebar"]


def test_reference_price_for_other_type_class_rejected(client, factories):
    """§5.3: классам типа other базовая цена не назначается → 422."""
    project = factories.ProjectFactory.create()
    misc = factories.MaterialClassFactory.create(material_type_code="other", name="Крепёж")
    from tests.factories import _unit_id
    resp = client.post("/api/reference-prices", json={
        "project_id": project.id, "material_class_id": misc.id,
        "unit_id": _unit_id("PCS"), "price": 100,
        "period_start": "2026-01-01", "period_end": "2026-12-31",
    })
    assert resp.status_code == 422
    assert "Прочее" in resp.json()["detail"]
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement `list_project_suppliers`** (`backend/routers/projects.py`): параметр `direction: str | None = None`; резолв через `_resolve_direction_type` (импортировать из `routers.dashboard`); при `mt`:

```python
        direction_exists = (
            db.query(InvoiceItem.id)
            .join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
            .filter(
                InvoiceItem.invoice_id == Invoice.id,
                InvoiceItem.item_type == "material",
                MaterialClass.material_type_id == mt.id,
            )
            .exists()
        )
        rows_q = rows_q.filter(direction_exists)   # применить к существующему query до .all()
```

(`func.count(Invoice.id)` при этом считает только отфильтрованные счета — то, что нужно.) Импорты `InvoiceItem`, `MaterialClass` добавить.

- [ ] **Step 4: Implement reference-prices**:

4a. `crud/projects.py::get_reference_prices` — параметр `material_type_code: str | None = None`; при нём `.join(ReferencePrice.material_class).join(MaterialClass.material_type).filter(MaterialType.code == material_type_code)` (сверь имена relationship по модели).

4b. Роутер GET: параметр `direction: str | None = None`, прокинуть. Неизвестный code здесь даст пустой список — для единообразия добавить резолв с 422: `_resolve_direction_type(db, direction)` перед вызовом.

4c. `_validate_ref_unit` — после получения `mc`:

```python
    if mc.material_type.code == "other":
        raise HTTPException(
            status_code=422,
            detail="Классам типа «Прочее» базовая цена не назначается (направления не образует)",
        )
```

(PATCH не меняет класс/единицу и валидатор не дёргает — этого достаточно.)

- [ ] **Step 5: Run** → PASS. **Step 6: Commit**

```bash
git add backend/routers/projects.py backend/routers/reference_prices.py backend/crud/projects.py backend/tests/
git commit -m "feat(directions): direction in project suppliers and reference prices; 422 for other-type classes"
```

---

### Task 6: экспорт — `direction`, каноническое имя файла, строка «Направление»

**Files:**
- Modify: `backend/routers/export.py`
- Test: `backend/tests/integration/test_export.py` (дополнить существующий)

- [ ] **Step 1: Write the failing tests** (в стиле существующего test_export.py; openpyxl уже в зависимостях):

```python
from io import BytesIO
from urllib.parse import unquote

from openpyxl import load_workbook


def test_export_direction_scoped_and_filename(client, factories):
    from decimal import Decimal

    from tests.factories import _unit_id

    project = factories.ProjectFactory.create(name="ЖК Радуга")
    concrete = factories.MaterialClassFactory.create(name="В25")
    rebar = factories.MaterialClassFactory.create(material_type_code="rebar", name="А500С Ø12")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=concrete, item_type="material",
        quantity=10, unit_price=8000, amount=80000)
    factories.InvoiceItemFactory.create(
        invoice=inv, material_class=rebar, item_type="material",
        quantity=2, raw_unit="т", unit_price=10000,
        normalized_unit_id=_unit_id("TON"), normalized_quantity=Decimal("2"))

    resp = client.get(f"/api/export/excel?project_id={project.id}&direction=rebar")
    assert resp.status_code == 200
    disposition = unquote(resp.headers["content-disposition"])
    assert "-Арматура" in disposition            # канонический суффикс §6.7
    ws = load_workbook(BytesIO(resp.content)).active
    cells = [str(c.value) for row in ws.iter_rows() for c in row if c.value]
    assert any("А500С" in c for c in cells)
    assert not any("В25" in c for c in cells)    # бетонных секций нет
    assert any("Направление: Арматура" in c for c in cells)


def test_export_unknown_direction_422(client, factories):
    project = factories.ProjectFactory.create()
    assert client.get(f"/api/export/excel?project_id={project.id}&direction=bricks").status_code == 422


def test_export_filename_canonical_without_direction(client, factories):
    project = factories.ProjectFactory.create(name="ЖК Радуга")
    doc = factories.DocumentFactory.create(project=project)
    inv = factories.InvoiceFactory.create(document=doc)
    factories.InvoiceItemFactory.create(invoice=inv, material_class=factories.MaterialClassFactory.create(),
                                        item_type="material", quantity=5, amount=40000)
    resp = client.get(f"/api/export/excel?project_id={project.id}")
    disposition = unquote(resp.headers["content-disposition"])
    assert "отчёт-ЖК Радуга_" in disposition     # дефис после «отчёт», подчёркивание перед периодом
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `export_excel`:

3a. Параметр `direction: str | None = None`; `mt = _resolve_direction_type(db, direction)` (импорт из `routers.dashboard`); `direction_type_id=mt.id if mt else None` прокинуть в `compute_export_rows` и `compute_calculations`.

3b. Info-блок: при `mt` вставить строку после contract_number:

```python
    info_lines = [
        _safe_str(project.name),
        _safe_str(project.contract_number or ""),
        *( [f"Направление: {mt.name}"] if mt else [] ),
        f"Период: ...",     # как было
        f"Сформировано: ...",
    ]
```

(синхронно расширить `info_fonts`/`info_heights` на одну позицию при `mt` — тем же шрифтом, что contract_number).

3c. Имя файла — канон §6.7 (дефисы вокруг имени/направления, подчёркивание перед периодом, en-dash между датами):

```python
    dir_suffix = f"-{mt.name}" if mt else ""
    filename = f"отчёт-{safe_name or project.id}{dir_suffix}_{display_start}–{display_end}.xlsx"
```

- [ ] **Step 4: Run** `just test-backend-integration` → PASS (существующий тест имени файла в test_export.py, если он проверяет старый формат `отчёт_…` — обновить ожидание на канон: это намеренная смена формата, спека §0c п.3).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/export.py backend/tests/integration/test_export.py
git commit -m "feat(directions): direction-scoped excel export, canonical filename (spec §6.7)"
```

---

### Task 7: финальная проверка backend

- [ ] **Step 1:** `just lint` → чисто (ruff).
- [ ] **Step 2:** `just test-backend-unit` и `just test-backend-integration` → все зелёные.
- [ ] **Step 3:** Самопроверка по спеке: §5.1 инвариант (тест есть), §5.4 страж (есть), ADR #8 регрессия моно (есть), §6.1–§6.7 параметры (есть), 422 на unknown direction во всех эндпоинтах с параметром (calculations, invoices, monthly, suppliers, reference-prices, export — проверить, что резолв вызывается в каждом).
- [ ] **Step 4: Commit + push, PR** `feat/material-directions-backend` → main. В описании PR — ссылка на спеку R5.
