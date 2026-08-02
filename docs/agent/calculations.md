# Расчёты цен, отклонений и компенсаций

Единый источник истины — `compute_calculations()` в `crud/calculations.py`. `compute_full_deviation()` делегирует туда же; агрегация суммы отклонений по готовым строкам — `full_deviation_from_rows(rows)` (её использует и summary, чтобы не гонять расчёт дважды). Данные считаются на лету, без кеша.

## Методология avg_price

Средняя цена считается **с НДС** — чтобы сравнение с базовыми ценами было корректным (базовые цены пользователь тоже вводит с НДС).

```
avg_price = (mat_total + mat_vat + delivery_for_class + delivery_vat_for_class) / qty
```

- `mat_total` = `SUM(InvoiceItem.amount)` — сумма без НДС из позиции
- `mat_vat` = `SUM(COALESCE(vat_amount, amount * COALESCE(vat_rate, 20.0) / 100))` — НДС; если `vat_amount` не извлечён парсером, берётся расчётный по ставке счёта
- `qty` = `SUM(InvoiceItem.normalized_quantity)` — суммируется в базовых единицах; один класс материала = одна размерность
- Расчёт помесячный; каждый месяц — отдельная строка

**Размерностный guard (`dimension_mismatch`):**

`compute_calculations` сравнивает размерность базовой единицы класса (из `material_types.default_unit`) с размерностью `reference_prices.unit_id`. При несовпадении или при смешении разных размерностей внутри одного класса (intra-class dimension mix) строка получает флаг `dimension_mismatch=True`. Такие строки **исключаются** из расчёта отклонения и компенсации (поля `deviation_pct`, `deviation_amount`, `compensation_*` возвращаются как `None`). Выходные поля строки: `unit_symbol` и `dimension_mismatch`.

**Отклонение:**
```
deviation_pct    = (avg_price − ref_price) / ref_price × 100
deviation_amount = (avg_price − ref_price) × qty
```

**Распределение доставки и additive-классов** (спека направлений §5.4, ADR #8):

Разноска — внутри каждого счёта, двумя раздельными котлами (`_aggregate_by_class(base_rows, delivery_per_invoice, additive_per_invoice_type)`):

- **Доставка (`item_type='delivery'`)** — на ВСЕ base-классы счёта: у строки доставки направления нет, это общий фрахт.
- **Additive-классы (`calc_role='additive'`)** — только на base-классы **своего `material_type`** внутри счёта (пластификатор не удорожает арматуру в смешанном счёте). Edge case: additive типа D в счёте без base-классов типа D не входит ни в чей avg_price (честный отказ). Для моно-направленных счетов поведение побитово совпадает со старым общим котлом.
- Доли — `compute_shared_shares`: моно-размерность строк → пропорционально `SUM(normalized_quantity)`, смешанная → пропорционально `amount`. Строки с неизвестной единицей (`normalized_unit_id IS NULL`) при mono-distribution получают нулевую долю.
- Та же механика зеркально в `compute_export_rows` и `_compute_supplier_project_deviation` (`crud/suppliers.py`) — при изменении править все три места.

**Направления (`direction`):**

`direction` в HTTP API = код `material_types` (`concrete`/`rebar`/...). Резолв и валидация — `routers/common.py::resolve_direction_type(db, direction)`: `None` → без фильтра, неизвестный код → 422, `other` → тоже 422 (тип `other` направлением не является — ADR #9). Параметр принимают: `/dashboard/calculations`, `/dashboard/invoices`, `/dashboard/monthly-summary`, `/projects/{id}/suppliers`, `/reference-prices`, `/export/excel`.

**КРИТИЧНО (ADR #2):** фильтр направления в `compute_calculations`/`compute_export_rows` применяется строго **на выходе** (как `material_class_id`) — знаменатели разноски всегда считаются по полному счёту, иначе avg_price класса зависел бы от выбранного режима. Тест-страж: `test_calculations_direction_filter_does_not_change_class_rows`. Каждая строка calculations несёт поле `direction` (код типа класса).

## Исключённые поставщики

Все три функции (`compute_calculations`, `compute_full_deviation`, `compute_export_rows`) принимают `excluded_supplier_ids: set[int] | None`. При непустом set инвойсы исключённых фильтруются через `or_(Invoice.supplier_id.is_(None), Invoice.supplier_id.notin_(excluded))` — **инвойсы без поставщика всегда включаются**. `get_project_summary` в `dashboard.py` применяет тот же фильтр ко всем агрегатам (оборот, объём м³, кол-во счетов). Загружать: `excluded = get_excluded_supplier_ids(db, project_id)`, передавать `excluded or None`.

## Dashboard и месячный оборот

`GET /dashboard/calculations` — на лету через `compute_calculations()`; опциональный `?direction=`.

`GET /dashboard/summary` — кроме legacy-полей возвращает разбивку по направлениям: `directions[]` (`code, name, turnover, overpayment, volume, volume_unit, volume_excluded_count, invoice_count, mixed_invoice_count`), `mixed_invoice_count`, `other_invoice_count`, `delivery_total`, `other_total`. Тип `other` направления не образует (ADR #9): его material-позиции и позиции без класса — в `other_total`. Инвариант: `total_amount = Σ directions.turnover + delivery_total + other_total` (точен на Decimal до сериализации; сериализованные слагаемые округлены независимо). Сборка — `_direction_summaries()` в `routers/dashboard.py`. **KPI «Компенсация» = Σ компенсаций (за пределами коридора), а не сырых отклонений**: `directions[].overpayment` = Σ `compensation_amount` классов направления (`None`, если ни один класс не компенсируется), `full_compensation_amount` = Σ по всем классам — обе из того же прогона `compute_calculations`. KPI-карточка, баннер и подытоги направлений в `DeviationChart` используют именно компенсацию; бары графика по-прежнему показывают `deviation_pct`. UI-подпись по знаку: `+` (доплата поставщику) → «Компенсация подрядчику», `−` (возврат заказчику) → «Компенсация подрядчиком», нет данных → «Компенсация за весь период» (поля API названия не меняли — `overpayment`/`full_compensation_amount`). Параметр `direction` summary НЕ принимает — один ответ обслуживает оба режима фронта. Поле `total_qty` — deprecated («попугаи» при миксе единиц).

`GET /dashboard/monthly-summary?project_id=` питает таб «По месяцам» (`MonthlyTab.tsx`). **Без `direction`: оборот по месяцам = полная стоимость всех позиций СФ с НДС**: `SUM(item.amount + COALESCE(item.vat_amount, item.amount * COALESCE(invoice.vat_rate, 20.0)/100))` по **всем** `item_type` (material + delivery + прочее), без фильтра по типу. Намеренно отличается от avg_price (которая работает только с `material`): тут нужен полный оборот по счёту, как выставил поставщик. **С `?direction=`**: `total_amount` — только material-позиции направления, `total_qty` — `SUM(normalized_quantity)` base-классов направления с совпадающей размерностью, `invoice_count` — счета направления; в строках поле `volume_unit` (символ default_unit направления; `None` без параметра).

`GET /dashboard/invoices?direction=` — счета с ≥1 material-позицией направления (correlated EXISTS); смешанный счёт возвращается в обоих направлениях целиком, со всеми позициями.

## VAT guard

`Invoice.vat_rate` не имеет NOT NULL (см. `TECH_DEBT.md`), поэтому во всех SQL-выражениях — `COALESCE(vat_rate, literal(Decimal("20.0")))`: type-bound Decimal literal, чтобы COALESCE оставался NUMERIC в DB-выражении и не смешивался с float.

## Decimal-слой

Весь расчётный слой (`crud/calculations.py`, `crud/suppliers.py`) работает в `Decimal` end-to-end. Округление — `money_round` из `backend/finance.py` (ROUND_HALF_UP, RU-арифметика). Нормализация на входе LLM→DB: `_dec(value)` в `crud/documents.py` через `Decimal(str(value))` отсекает бинарную погрешность float. API-payloads (`price`, `corridor_pct`) — `Decimal`-поля Pydantic. Сериализация: `DecimalJSONResponse` в `main.py` (`default_response_class`) конвертирует Decimal→float при отдаче JSON; фронтенд-контракт не меняется.

## DateTime serialization

Все `DateTime`-колонки хранятся как **naive UTC** (без timezone). При ручной сериализации в JSON добавляй `"Z"`: `dt.isoformat() + "Z"` — сигнал браузеру о UTC, исключает сдвиг даты у пользователей не в UTC. Pydantic-схемы с `datetime` делают это сами; правило актуально для dict-ответов (например в `crud/admin.py`).

## Экспорт Excel

`GET /api/export/excel?project_id=&period_start=&period_end=&material_class_id=&direction=` → openpyxl через `compute_export_rows()` → `routers/export.py`. Возвращает `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`. При `direction`: строки только направления + строка «Направление: {имя}» в info-блоке. Каноническое имя файла: `отчёт-{имя}[-{Направление}]_{start}–{end}.xlsx` (дефисы вокруг имени/направления, подчёркивание перед периодом, en-dash между датами) — фронтовый `a.download` использует тот же формат.

**21 колонка (A–U):** два блока единиц — «по документу» (сырые данные из PDF) и «расчётное» (нормализованные). Блок «по документу»: дата, номер СФ, поставщик, «Кол-во по документу», «Ед. изм. по документу». Блок «расчётное»: «Расчётное кол-во», «Базовая ед. изм.», базовая цена, ставка НДС, материал/доставка/прочее без НДС, итого без НДС (формула), те же три с НДС (формулы), итого с НДС (формула), откл. % и откл. ₽ (формулы). Строчная математика (цены на единицу) считается на `normalized_quantity`. Месячные строки — SUMPRODUCT-формулы, разделители между месяцами, grand total на класс. Кнопка «Экспорт» в `ProjectPage.tsx` использует `periodStart`/`periodEnd` напрямую (не debounced) — правильно для действия по кнопке.

## Коридор компенсации (Spec 2 — fallback иерархия)

Таблица `compensation_corridors(id PK, project_id, material_type_id? FK→material_types, material_class_id?, is_compensable, corridor_pct?)` — иерархические правила per-project. Ровно одно из `material_type_id`/`material_class_id` заполнено (CHECK constraint). `material_type` (String) заменён на `material_type_id` FK → `material_types`; резолвер `get_corridor_map` использует ключи по `material_type_id`. HTTP API по-прежнему принимает `material_type` code (напр. `PUT .../corridors/type/concrete`) — роутер маппит code→id перед передачей в CRUD.

**Whitelist-дефолт:** нет строки = не компенсируется.

**Fallback-резолв:** class-level → type-level → нет записи. Class-level override побеждает в обе стороны.

- `is_compensable=false` — явно выключено на данном уровне
- `is_compensable=true, corridor_pct=X` — допуск ±X%, компенсация за пределами коридора
- `is_compensable=true, corridor_pct=0` — любое отклонение компенсируется

**Формула** (нелинейная, на единицу объёма, P = avg_price, B = ref_price, k = corridor_pct/100):
```
P > B*(1+k):  comp = P - B*(1+k)    # + удорожание
P < B*(1-k):  comp = P - B*(1-k)    # − экономия
иначе:        comp = 0
```
На объём: `compensation_amount = comp * qty`. Знак: + доплата поставщику / − возврат заказчику.

**Важно:** формула нелинейна → считается от средней цены за месяц, не из построчных значений.

- **Batch-резолв в `crud/calculations.py`:** `get_corridor_map(db, project_id)` → `(by_class, by_type)` dicts; `resolve_corridor(by_class, by_type, class_id, material_type)` → `(compensable, corridor_pct)`. Один запрос на проект, без N+1.
- **Чистая формула:** `compute_compensation_per_unit(avg_price, ref_price, corridor_pct)` — unit-тестируема без БД.
- **Три поля в dict строки:** `corridor_pct`, `compensation_per_unit`, `compensation_amount`. `None` = не компенсируется (нет строки или `is_compensable=false`), `Decimal("0")` = внутри коридора.
- **API:** `GET /api/projects/{id}/corridors` (resolved matrix), `PUT/DELETE /api/projects/{id}/corridors/type/{material_type}`, `PUT/DELETE /api/projects/{id}/corridors/class/{material_class_id}`. PUT тело: `{is_compensable, corridor_pct?}` — `corridor_pct` обязателен при `is_compensable=true` (422 иначе). Upsert через `pg_insert ON CONFLICT` по partial unique index.
- **Excel:** колонки Q «Коридор, %», R «Компенсация, ₽». `None` → пусто.
- **Frontend:** таб «Коридоры» (`CorridorsTab.tsx`) — единая таблица, сгруппированная по типу. Заголовки типов с toggle вкл/выкл + %; строки классов с индикацией «(наследовано)» / «[своё]» + кнопкой `×` для удаления class-override.
