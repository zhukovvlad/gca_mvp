# Поставщики

## Агрегация (на лету, без кеша)

Ключевые функции в `crud.suppliers`:

- `get_suppliers_with_stats` — реестр: оборот, project_count, invoice_count, категории
- `get_supplier_detail` — те же агрегаты по одному поставщику (шапка карточки)
- `get_supplier_project_stats` — построчно по проектам: volume_m3 и deviation_pct/amount
- `_compute_supplier_project_deviation` — отклонение в рамках инвойсов самого поставщика (та же логика, что `compute_full_deviation`, но берёт самую свежую базовую цену по классу без фильтра по периоду — намеренно, см. docstring). Не использовать, когда нужна сверка с страницей проекта по периодам.

**Оборот в supplier-агрегатах**: `SUM(amount + COALESCE(vat_amount, amount * COALESCE(vat_rate, 20.0) / 100))`.

## Дедупликация (в парсинге и ручном редактировании)

- `crud.suppliers.get_or_create_supplier(db, name, inn)` — дедуп по ИНН если есть, иначе по точному имени где `inn IS NULL`. Race-safe через `INSERT ... ON CONFLICT DO NOTHING` + re-SELECT. Всегда явно ставит `created_at` (ORM-дефолт не срабатывает через `pg_insert`).
- `supplier_inn` без `supplier_name` невалиден: `PUT /api/invoices/{id}` → 422; `crud.documents.create_invoice()` тихо чистит `_inn` (нет Supplier без имени).
- Редактирование инвойса: при совпадении ИНН ставит `supplier_inn` из канонической записи. Если ИНН совпал, но имя изменилось — это трактуется как каноническое переименование: `Supplier.name` обновляется и каскадится в `supplier_name` всех счетов поставщика (та же семантика, что `update_supplier`; warning `supplier_renamed` в ответе). Имя без ИНН по-прежнему создаёт/линкует нового поставщика по точному совпадению.
- `PUT /suppliers/{id}` → 409 с разными сообщениями для конфликта ИНН (`suppliers.inn` unique) vs имени (`uq_suppliers_name_no_inn`, partial index для `inn IS NULL`).

## Исключение поставщиков из расчётов

Пользователь может исключить поставщика из расчётов по конкретному проекту (например субподрядчик с нерепрезентативными ценами). Хранится в `project_supplier_exclusions(project_id PK, supplier_id PK, reason TEXT, created_at)`.

- **Scope**: per-project, не глобально. Исключается из avg_price, deviation, export и всех KPI-карточек (оборот, объём м³, счетов) только в рамках проекта.
- **Supplier-side stats** (`crud.suppliers`) — исключения **не применяются**: оборот и аналитика поставщика считаются по всем его инвойсам независимо от проектных исключений.
- **Invoice.supplier_id IS NULL** — инвойсы без поставщика **всегда включаются** (`or_(supplier_id IS NULL, supplier_id NOT IN (excluded))`).
- **API**: `GET /api/projects/{id}/suppliers` → `[{ id, name, inn, invoice_count }]`; `GET /api/projects/{id}/supplier-exclusions` → `list[int]` (sorted supplier_ids, не объекты); `POST/DELETE /api/projects/{id}/supplier-exclusions/{supplier_id}` — добавить/снять (204). Тело POST: `{ reason?: string }`.
- **Frontend**: таб «Поставщики» в ProjectPage — чекбоксы с инлайн-формой причины (Escape/Enter). Баннер в обзоре проекта при активных исключениях.
- **Идемпотентность**: повторный POST не дублирует; повторный DELETE не падает.
- **Загрузка** в роутерах: `excluded = get_excluded_supplier_ids(db, project_id)` из `crud.supplier_exclusions`; передавать `excluded or None` (пустой set → None, чтобы не добавлять лишний WHERE).

## Ограничения MVP (НАМЕРЕННЫЕ, не баги)

Секции `/suppliers` и `/suppliers/:id` — MVP-аналитика. Это сознательные ограничения:

- **Нет кросс-проектной средней наценки**. Плановые цены — per-project/contract; усреднять отклонения по разным плановым базам методологически неверно. Отклонение живёт только в построчных данных таба «По объектам».
- **Нет сравнения с рынком** («дешевле/дороже рынка»). В бэклоге — нужна фиксированная корзина, достаточно поставщиков на класс, корректировка логистики.
- **Нет таба «Сравнение»** — сознательно исключён из MVP.
- **Строка итогов**: оборот/объём/счета суммируются; project_count → `—`; deviation → «не суммируем» (italic grey).
- **Бейдж «Новый»**: порог 30 дней от `first_invoice_date`.
- **Merge при конфликте ИНН**: `PUT /suppliers/{id}` → 409 `{ code: "inn_conflict", existing: { id, name } }`, когда ИНН принадлежит другому поставщику. Фронт показывает диалог слияния, затем `POST /suppliers/{target_id}/merge { source_id }` редиректит на выжившую карточку.
