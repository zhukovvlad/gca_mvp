# Каноническое переименование поставщика при правке СФ — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Правка имени поставщика в форме СФ при неизменном ИНН должна обновлять каноническое `Supplier.name` и каскадить новое имя во все счета этого поставщика, а не молча откатываться к старому имени.

**Architecture:** Точечная правка хендлера `update_invoice` в `backend/routers/invoices.py`: при совпадении ИНН и изменённом имени инлайним семантику `update_supplier` (без отдельного commit — используется единый commit хендлера) и возвращаем warning через уже существующий канал `warnings`. Фронт и путь парсинга новых счетов не меняются.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (sync), pytest. Команды — через `just`.

Спека: [docs/superpowers/specs/2026-06-15-supplier-rename-on-invoice-edit-design.md](../specs/2026-06-15-supplier-rename-on-invoice-edit-design.md)

---

## File Structure

- **Modify:** `backend/routers/invoices.py` — функция `update_invoice` (строки 263–289): поднять объявление `warnings` и заменить блок поставщика.
- **Test:** `backend/tests/integration/test_invoices.py` — добавить один интеграционный тест рядом с существующими `test_update_invoice_*`.
- **Docs:** `docs/agent/suppliers.md` — обновить строку 18 под новое поведение.

Команда запуска тестов (Windows shell):
`& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && <cmd> 2>&1"`
где `<cmd>` — `just test-backend-integration` (весь набор) либо точечный запуск через
`cd backend && python -m pytest tests/integration/test_invoices.py::<test_name> -v` внутри того же bash-вызова.

---

### Task 0: Ветка

- [ ] **Step 1: Создать ветку от main**

```bash
git checkout -b fix/supplier-rename-on-invoice-edit
```

---

### Task 1: Падающий тест на каскадное переименование

**Files:**
- Test: `backend/tests/integration/test_invoices.py` (добавить после `test_update_invoice_clears_supplier_when_name_empty`, ~строка 383)

- [ ] **Step 1: Написать падающий тест**

Добавить в `backend/tests/integration/test_invoices.py`:

```python
def test_update_invoice_renames_supplier_and_cascades(client, factories, db_session):
    """PUT /invoices/{id}: тот же ИНН, изменённое имя → каноническое переименование
    поставщика + каскад во все его счета + warning supplier_renamed."""
    from models import Invoice, Supplier

    supplier = factories.SupplierFactory.create(
        name="общество с ограниченной ответственностью Ромашка",
        inn="7707083893",
    )
    inv1 = factories.InvoiceFactory.create(
        supplier_id=supplier.id,
        supplier_name="общество с ограниченной ответственностью Ромашка",
        supplier_inn="7707083893",
    )
    inv2 = factories.InvoiceFactory.create(
        supplier_id=supplier.id,
        supplier_name="общество с ограниченной ответственностью Ромашка",
        supplier_inn="7707083893",
    )

    resp = client.put(
        f"/api/invoices/{inv1.id}",
        json={
            "number": inv1.number,
            "date": str(inv1.date),
            "supplier_name": "ООО Ромашка",
            "supplier_inn": "7707083893",
            "vat_rate": 20.0,
            "items": [],
        },
    )
    assert resp.status_code == 200

    body = resp.json()
    assert any(w["code"] == "supplier_renamed" for w in body["warnings"])

    db_session.expire_all()
    # Каноническое имя обновлено, новый поставщик НЕ создан
    suppliers = db_session.query(Supplier).filter(Supplier.inn == "7707083893").all()
    assert len(suppliers) == 1
    assert suppliers[0].name == "ООО Ромашка"
    # Каскад: оба счёта получили новое имя
    for inv_id in (inv1.id, inv2.id):
        inv = db_session.query(Invoice).filter(Invoice.id == inv_id).first()
        assert inv.supplier_id == supplier.id
        assert inv.supplier_name == "ООО Ромашка"
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_invoices.py::test_update_invoice_renames_supplier_and_cascades -v 2>&1"`

Expected: FAIL — `inv2.supplier_name` остаётся «общество с ограниченной ответственностью Ромашка» (текущий код откатывает имя к каноническому старому; каскада нет; warning отсутствует).

---

### Task 2: Реализация каскадного переименования

**Files:**
- Modify: `backend/routers/invoices.py:263-289`

- [ ] **Step 1: Поднять объявление `warnings` выше блока поставщика**

Сейчас `warnings: list[dict] = []` объявлено на строке 289 (после блока поставщика). Удалить его оттуда и объявить сразу после `invoice.vat_rate = data.vat_rate` (строка 269), перед `if _name:`.

Результирующий фрагмент (строки 263–278 заменяются на):

```python
    invoice.number = data.number
    invoice.date = data.date
    _name = (data.supplier_name.strip() or None) if data.supplier_name else None
    _inn = (data.supplier_inn.strip() or None) if data.supplier_inn else None
    if _inn and not _name:
        raise HTTPException(status_code=422, detail="supplier_name обязателен при указании supplier_inn")
    invoice.vat_rate = data.vat_rate

    warnings: list[dict] = []
    if _name:
        supplier = get_or_create_supplier(db, name=_name, inn=_inn)
        if _name != supplier.name:
            # ИНН совпал, имя изменилось → каноническое переименование.
            # Каскадим в денормализованную витрину всех счетов поставщика
            # (та же семантика, что crud.suppliers.update_supplier, но без отдельного commit).
            supplier.name = _name
            affected = db.query(Invoice).filter(Invoice.supplier_id == supplier.id).update(
                {Invoice.supplier_name: _name}, synchronize_session=False
            )
            warnings.append({
                "field": "supplier_name",
                "code": "supplier_renamed",
                "message": f"Имя поставщика обновлено во всех счетах ({affected})",
            })
        invoice.supplier_id = supplier.id
        invoice.supplier_name = supplier.name
        invoice.supplier_inn = supplier.inn
    else:
        invoice.supplier_id = None
        invoice.supplier_name = None
        invoice.supplier_inn = None
```

- [ ] **Step 2: Удалить дублирующее объявление `warnings`**

На строке 289 (была `aliases = load_alias_map(db)` / `warnings: list[dict] = []`) удалить строку `warnings: list[dict] = []` — теперь объявление выше. Оставить `aliases = load_alias_map(db)` на месте.

Проверить, что осталось ровно одно объявление `warnings`:

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && grep -n 'warnings: list' backend/routers/invoices.py 2>&1"`
Expected: одна строка.

- [ ] **Step 3: Запустить новый тест — убедиться, что проходит**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_invoices.py::test_update_invoice_renames_supplier_and_cascades -v 2>&1"`
Expected: PASS

- [ ] **Step 4: Запустить все тесты invoices — убедиться, что ничего не сломано**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP/backend && python -m pytest tests/integration/test_invoices.py -v 2>&1"`
Expected: PASS, включая `test_update_invoice_links_supplier`, `test_update_invoice_clears_supplier_when_name_empty`, `test_update_invoice_inn_without_name_returns_422`.

---

### Task 3: Документация

**Files:**
- Modify: `docs/agent/suppliers.md:18`

- [ ] **Step 1: Обновить строку 18**

Заменить:

```
- Редактирование инвойса ставит `supplier_name`/`supplier_inn` из **канонической записи БД** (не из сырого ввода), если ИНН совпал с существующим.
```

на:

```
- Редактирование инвойса: при совпадении ИНН ставит `supplier_inn` из канонической записи. Если ИНН совпал, но имя изменилось — это трактуется как каноническое переименование: `Supplier.name` обновляется и каскадится в `supplier_name` всех счетов поставщика (та же семантика, что `update_supplier`; warning `supplier_renamed` в ответе). Имя без ИНН по-прежнему создаёт/линкует нового поставщика по точному совпадению.
```

---

### Task 4: Линт и финальная проверка

- [ ] **Step 1: Линт**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint 2>&1"`
Expected: без ошибок.

- [ ] **Step 2: Полный прогон бэкенд-тестов**

Run: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-backend-integration 2>&1"`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/routers/invoices.py backend/tests/integration/test_invoices.py docs/agent/suppliers.md
git commit -m "$(cat <<'EOF'
fix(invoices): правка имени поставщика в СФ переименовывает поставщика с каскадом

При совпадении ИНН и изменённом имени update_invoice обновлял supplier_name
из канонической записи, молча откатывая правку. Теперь изменение имени при
совпавшем ИНН обновляет Supplier.name и каскадит во все счета поставщика,
возвращая warning supplier_renamed.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- Первопричина и фикс блока поставщика → Task 2 ✓
- Поднятие `warnings` выше блока → Task 2 Step 1–2 ✓
- Каскад + warning `supplier_renamed` → Task 2 + проверка в Task 1 ✓
- Путь парсинга без изменений → подтверждено в спеке, кода не требует ✓
- TDD-тест (поставщик + 2 счёта, переименование, каскад, warning) → Task 1 ✓
- Сохранность существующих тестов → Task 2 Step 4 ✓
- Правка `suppliers.md:18` → Task 3 ✓

**Placeholder scan:** плейсхолдеров нет — весь код и команды приведены полностью.

**Type consistency:** `warnings` — `list[dict]`, элемент с ключами `field/code/message` совпадает с форматом существующих warning'ов в `_normalize`. `supplier_renamed` используется одинаково в тесте (Task 1) и реализации (Task 2). `affected` — rowcount от `.update()`.
