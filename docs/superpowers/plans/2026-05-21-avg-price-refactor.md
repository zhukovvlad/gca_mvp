# План: рефакторинг расчёта средней цены бетона

> Дата: 2026-05-21
> Статус: готов к реализации
> Связанный документ: `docs/methodology-avg-price.md`

---

## Суть изменений

Три проблемы в текущем `compute_calculations()`:

1. **Доставка пула месяца** — доставка суммируется по всему проекту за месяц
   и распределяется по всем классам. Доставка поставщика А влияет на цену
   бетона поставщика Б.

2. **Неклассифицированные материалы в знаменателе** — позиции без класса
   (цементное молоко, проволока и т.п.) входят в `all_material_qty`,
   уменьшая долю доставки для основного материала.

3. **Присадки не учитываются** — их сумма нигде не попадает в `avg_price`.

**Решение:** сменить единицу агрегации с «месяц» на «счёт-фактуру».
Доставка и присадки из каждого счёта распределяются только по основному
материалу из того же счёта.

---

## Изменения в схеме БД

### Новая колонка `calc_role` в `material_classes`

```sql
ALTER TABLE material_classes
  ADD COLUMN calc_role VARCHAR NOT NULL DEFAULT 'base';
```

Существующие записи автоматически получают `calc_role = 'base'` — корректно,
так как сейчас в справочнике только классы бетона и арматуры.

**Значения `calc_role`:**

| `calc_role` | Смысл | Участие в avg_price |
|---|---|---|
| `base` | Основной материал (В40, А500С) | Объём + стоимость |
| `additive` | Присадки к основному материалу | Пропорционально входит |
| `exclude` | Сопутствующие позиции (цем.молоко, проволока) | Не входит |

**`material_type` не меняется** — сохраняет значения `concrete` / `rebar` / `other`,
используется для группировки в UI.

Вместе два поля однозначно описывают роль любого класса:

| `material_type` | `calc_role` | Примеры |
|---|---|---|
| `concrete` | `base` | В40, В30, В25 |
| `concrete` | `additive` | Пластификатор, гидрофобизатор |
| `concrete` | `exclude` | Цементное молоко |
| `rebar` | `base` | А500С, А240 |
| `rebar` | `exclude` | Проволока вязальная |

`rebar` + `additive` — не создаём, пока нет подтверждённой потребности.

### Новый индекс на `invoice_items`

```sql
CREATE INDEX ON invoice_items (invoice_id, item_type);
```

Нужен для эффективной агрегации по счёту в новой логике расчёта.

### `invoice_items` — ничего не меняется

`item_type` остаётся с тремя значениями `"material"` / `"delivery"` / `"other"`.
Доставка всегда входит в avg_price пропорционально — через `item_type`,
а не через `calc_role` (у доставки нет `material_class_id`).
Позиции `item_type="other"` (скидки, возвраты, корректировки) не участвуют в расчёте.

---

## Требования к именованию классов материалов

`material_classes.name` хранит **короткое торговое наименование**:
`"В40"`, `"В30"`, `"А500С"` — не полную спецификацию из счёта.

Полная строка из СФ (`"БСТ В40 П4 F200 W12 (С.24-23)"`) остаётся
в `invoice_items.raw_name`.

LLM при парсинге должен:
1. Извлечь класс из `raw_name` (например `"В40"` из `"БСТ В40 П4 F200 W12"`)
2. Найти или создать `MaterialClass` с таким именем

---

## Изменения в LLM-промпте (`pdf_parser.py`)

Добавить инструкции:

- **`item_type = "material"`, `calc_role = "base"`** — строки с классом бетона
  или арматуры; имя класса извлекается коротко: `"В40"`, `"А500С"`
- **`item_type = "material"`, `calc_role = "additive"`** — химические добавки
  к бетонной смеси: пластификаторы, гидрофобизаторы, противоморозные добавки,
  ускорители твердения
- **`item_type = "material"`, `calc_role = "exclude"`** — цементное молоко,
  простой миксера, мойка, проволока вязальная и прочие сопутствующие позиции
- **`item_type = "delivery"`** — строки доставки; `material_class_id = NULL`

---

## Изменения в `compute_calculations()` (`backend/crud.py`)

### Текущая логика (упрощённо)

```python
for month in months:
    class_rows = query(SUM amount, SUM qty GROUP BY material_class_id)
        WHERE item_type="material" AND material_class_id IS NOT NULL

    delivery_total = query(SUM amount WHERE item_type="delivery")

    # Включает неклассифицированные позиции — ошибка
    all_qty = query(SUM qty WHERE item_type="material")

    for class C:
        share = qty_C / all_qty
        avg_price = (бетон_C + НДС_C + delivery_total × share) / qty_C
```

### Новая логика

```python
for month in months:
    # Агрегация по каждому счёту отдельно:
    #   - объём и стоимость base-материалов по классам
    #   - сумма доставки
    #   - сумма additive-материалов
    # Знаменатель пропорции: только calc_role="base" в данном счёте

    for class C:
        contribution_C = SUM по счетам(
            base_amount_C_in_invoice
            + base_vat_C_in_invoice
            + (delivery_in_invoice + delivery_vat_in_invoice) * share_C
            + (additive_in_invoice + additive_vat_in_invoice) * share_C
        )
        where share_C = qty_C_in_invoice / qty_base_in_invoice

        avg_price_C = contribution_C / total_qty_C
```

### Конкретные изменения в SQL

**Запрос 1 (base-материалы по классам):**
```python
.join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
.filter(MaterialClass.calc_role == "base")
.group_by(InvoiceItem.invoice_id, InvoiceItem.material_class_id)
```

**Запрос 2 (знаменатель пропорции)** — объём base-материалов по каждому счёту:
```python
.join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
.filter(MaterialClass.calc_role == "base")
.group_by(InvoiceItem.invoice_id)
```

**Запрос 3 (доставка по счёту):**
```python
.filter(InvoiceItem.item_type == "delivery")
.group_by(InvoiceItem.invoice_id)
```

**Запрос 4 (присадки по счёту):**
```python
.join(MaterialClass, InvoiceItem.material_class_id == MaterialClass.id)
.filter(MaterialClass.calc_role == "additive")
.group_by(InvoiceItem.invoice_id)
```

---

## Изменения в `_compute_supplier_project_deviation()` (`backend/crud.py`)

Та же логика: заменить агрегацию за весь период на агрегацию по счёту.
Дополнительно учесть присадки (`calc_role="additive"`) как shared cost.
Знаменатель пропорции — только `calc_role="base"`.

---

## Файлы для изменения

| Файл | Что меняется |
|---|---|
| `backend/crud.py` | `compute_calculations()`, `_compute_supplier_project_deviation()` |
| `backend/models.py` | Добавить поле `calc_role` в `MaterialClass` |
| `backend/pdf_parser.py` | LLM-промпт: классификация по `calc_role`, короткие имена классов |
| `backend/alembic/` | Миграция: ADD COLUMN `calc_role`, CREATE INDEX |
| `backend/tests/unit/test_crud_compute_calculations.py` | Новые тест-кейсы |

Фронтенд и API-контракт **не меняются**.

---

## Граничные случаи (явно вне охвата)

- **СФ без base-материала** (только доставка или только присадки):
  `qty_base_in_invoice = 0` → `share = 0` → вклад в avg_price = 0.
  Доставка и присадки из такой СФ теряются — сознательное ограничение.
- **Поставщик на УСН** (НДС не выделен): LLM должен передавать `vat_rate = 0`,
  иначе `COALESCE(vat_rate, 20.0)` завысит сумму.

---

## Тест-кейсы (обязательно)

1. **Доставка не перетекает между поставщиками**
   Два счёта в одном месяце: счёт А (В40 + доставка), счёт Б (В30 без доставки).
   `avg_price` В30 не должна включать доставку из счёта А.

2. **Присадки входят в avg_price**
   Счёт с В40 и присадкой (`calc_role="additive"`).
   `avg_price` В40 должна включать стоимость присадки пропорционально.

3. **`exclude`-позиции не входят в avg_price**
   Счёт с В40 и цементным молоком (`calc_role="exclude"`).
   `avg_price` В40 не должна содержать стоимость цементного молока.

4. **`exclude`-позиции не учитываются в знаменателе пропорции**
   Счёт: 100 м³ В40 (`base`) + 1 м³ цем.молоко (`exclude`) + доставка 50 000 ₽.
   `share` В40 = 100/100 = 1.0, не 100/101.

5. **Несколько классов в одном счёте**
   Счёт: 60 м³ В40 + 40 м³ В30 + доставка 120 000 ₽.
   В40 получает 72 000 ₽, В30 — 48 000 ₽.

6. **СФ без base-материала (только доставка)**
   `compute_calculations()` не должна падать, вклад = 0.

---

## Порядок выполнения

1. Alembic-миграция: `ADD COLUMN calc_role DEFAULT 'base'` + индекс
2. `backend/models.py`: добавить поле `calc_role` в `MaterialClass`
3. Создать классы-справочники с нужным `calc_role` (цем.молоко, присадки)
4. Обновить LLM-промпт: `calc_role`-классификация + короткие имена
5. Переписать `compute_calculations()` — invoice-level агрегация
6. Обновить `_compute_supplier_project_deviation()` аналогично
7. Написать новые юнит-тесты, убедиться что все проходят
8. Прогнать `just test` полностью

---

## Что не меняется

- `invoice_items` — схема и `item_type` без изменений
- `material_type` в `material_classes` — значения не меняются, используется в UI
- Фронтенд и API-контракт `compute_calculations()`
- Логика выбора базовой цены (period overlap)
- `compute_full_deviation()` — делегирует в `compute_calculations()`,
  автоматически получит исправление
