# Спецификация: справочники единиц измерения и типов материалов

**Дата:** 2026-06-08
**Статус:** утверждён · ревизия R4 (финальная перед реализацией)
**Scope:** backend schema + normalization + calculations + API + migration + tests

---

## 0. Изменения ревизии R2 (для ревьюера)

Правки внесены после ревью; ниже — что и зачем тронуто.

1. **§4.1 — уточнён смысл инварианта.** `norm_qty × norm_price ≈ amount` проверяет
   согласованность исходной строки СФ, а **не** корректность множителя (он
   алгебраически сокращается). Корректность `multiplier` гарантируют unit-тесты §8.1.
2. **§4.4 — агрегация только per-material_class.** `SUM(normalized_quantity)` и
   `avg_price` считаются внутри одного класса (одна размерность); инвойс-wide
   суммирование количества запрещено («попугаи»).
3. **§3.2 / §5.2 — валидация размерности ref_price.** `unit_id` проверяется не только
   на «базовость», но и на совпадение размерности с `material_type` класса (fail early).
4. **§5.2 / §10 — Excel-колонка переименована** «Расчётный объём» → «Расчётное кол-во»
   (для массы/длины/штук «объём» некорректен).
5. **§3.1 / §4.1 / §7 — `normalize_unit_key` усилен NFKC** (+ схлопывание внутренних
   пробелов, срез хвостовых точек): объединяет `м³`↔`м3`, NBSP и пр. Бэкфилл и сидинг
   обязаны использовать ту же нормализацию (см. примечание в §7).
6. **§7 / §10 — уточнена обратимость down.** Down обратим по структуре; данные,
   созданные под новой схемой (`reference_prices.unit_id` ≠ M3), на down/up-цикле
   не сохраняются.
7. **§3.2 / §7 — `ON DELETE` к `material_types` унифицирован на RESTRICT** для всех FK
   (reference-данные иммутабельны).
8. **Прочее:** индексы на `normalized_unit_id` и `material_type_id`; поведение
   `item_type='other'` в delivery distribution; опциональный триггер иммутабельности
   `to_base_multiplier`; раздел «Открытые вопросы» → закрыты.

---

## 0b. Изменения ревизии R3 (сверка с `corridor-fallback`)

> **Зависимость:** этот рефакторинг применяется **после** плана `corridor-fallback`
> (ревизия `d0e1f2a3b4c5`), который уже перестроил `compensation_corridors`:
> суррогатный `id` PK, таргет `material_type` (String) **XOR** `material_class_id`,
> `is_compensable` + nullable `corridor_pct`, два частичных уникальных индекса, CHECK
> `chk_corridor_target_exclusive` и `chk_corridor_pct_required_if_compensable`,
> резолвер class→type→no-row (whitelist). **Логика коридоров не меняется** — меняется
> только колонка таргета: `material_type` (String) → `material_type_id` (FK).

1. **§3.2 / §7 — конвертация таргета коридора String→FK расписана полностью.** Раз
   `material_classes.material_type` уезжает в FK, оставить коридор на строке нельзя:
   резолвер сматчивал бы строковый ключ коридора с id-ключом класса. Поэтому FK на
   `material_type_id` — условие согласованности резолвера, не косметика (усиливает ADR #2).
2. **§7 — пересоздаются ОБА частичных индекса.** `uq_corridor_project_class` имеет
   предикат `WHERE material_type IS NULL` — он ссылается на дропаемую строковую колонку,
   поэтому оба индекса (и CHECK) надо пересоздать на `material_type_id`, не только
   `uq_corridor_project_type`.
3. **§4.2 / §6 — правка резолвера.** `get_corridor_map` / `resolve_corridor` меняют ключ
   type-уровня со строки на `material_type_id`; интеграция в `calculations.py` подаёт
   `class.material_type_id`. Сам алгоритм (class→type→no-row, whitelist) — без изменений.
4. **§4.2 — порядок гейтов.** Dimension guard и corridor resolution ортогональны:
   сначала guard, затем (для прошедших) резолвер.
5. **§7 Step 2 — `Decimal("0.001")`** вместо float-литерала в сиде (точность Numeric).

---

## 0c. Изменения ревизии R4 (финальная вычитка)

1. **§7 Step 5 — down для `material_classes` дописан по образцу коридора (баг).** Был
   только ADD COLUMN + бэкфилл, не хватало DROP `material_type_id` (и его INDEX) +
   `SET NOT NULL` на восстановленном `material_type`. Теперь симметричен с коридором.
2. **§7 Step 1 / Step 4 — явные `CREATE INDEX` в SQL миграции.** INDEX на
   `material_type_id` и `normalized_unit_id` были заявлены в §3.2 и §10, но отсутствовали
   в SQL. Добавлены в Step 4 (после ужесточения NOT NULL).
3. **§7 Step 3 — предохранитель 2 читает старую строковую колонку `mc.material_type`**
   вместо JOIN через ещё-не-бэкфиленный `material_type_id` (timing bug: JOIN по NULL →
   пустой результат → проверка пропускает что угодно).
4. **§3.2 / §5.2 — валидация 2 ref_price при `default_unit_id IS NULL`.** Для
   `material_type='other'` (нет `default_unit`) валидация размерности пропускается —
   допустима любая базовая единица. Иначе `None.dimension` → crash или ложный 422.
5. **§3.1 — `material_types.default_unit_id` ON DELETE RESTRICT** (было не указано).
6. **§7 — унификация переменных** в псевдокоде (`TON_ID`, `M3_ID` — получены из flush).

---

## 1. Контекст и цель

Система трекинга УПД масштабируется с бетона (где единица почти всегда м³) на
арматуру, кирпич, сыпучие смеси. Парсер отдаёт единицы измерения в разнобой:
`т`, `тн`, `тонн`, `кг`.

**Цель:** стандартизировать единицы на бэкенде и корректно считать компенсации
по эталонным ценам, убрав хардкод `material_type` (String) и `unit` (String).

**Принципы:**
- YAGNI — сидим только реальные единицы из данных
- Строгая нормализация — детерминированный маппинг при приёме, без угадывания
- Write-time нормализация — результат вычислен один раз, аудируем, не зависит от будущих изменений справочника
- Никаких физических формул (плотность, удельный вес) — перевод между размерностями вне scope

---

## 2. Scope

### В работе

- Справочники `units_of_measure`, `unit_aliases`, `material_types`
- Конвертация внутри одной размерности (кг↔т, л↔м³)
- Нормализация при приёме: `normalized_*` на `InvoiceItem`
- `unit_id` NOT NULL на `ReferencePrice`
- `material_type_id` FK на `MaterialClass` и `CompensationCorridor`
- `item_type` → Enum + CHECK constraint
- Guard по dimension в калькуляторе
- Delivery distribution: qty для моно-dimension, fallback amount для mixed
- Миграция с предохранителями и down-миграцией
- API: read-only справочники, warning при unknown unit
- Excel: два блока колонок (сырые + расчётные)
- Тесты: unit + integration + migration backfill + edge cases

### Осознанно за scope (backlog)

| Что | Почему отложено |
|---|---|
| Перевод между размерностями через плотность | Требует справочника плотностей, сложная предметная область |
| Множественные единицы на тип материала | Одна базовая единица на размерность достаточна |
| UI-редактор справочников (CRUD units/aliases/types) | MVP — через миграции/CLI, фронтенд-компоненты дорогие |
| Самообучающийся справочник алиасов | Отличная идея, требует UX-дизайна workflow |
| Lazy reprocess (`POST /api/admin/reprocess-units`) | YAGNI сейчас, схема позволяет добавить без изменений |
| Темпоральное версионирование справочников | Не нужно при иммутабельном `to_base_multiplier` |
| BAG и экзотические единицы | Сидим реальные из данных, расширяем append-only |
| Перевод пог.м арматуры → т (линейный вес) | ~10% строк, операционно приемлемо; поднимем при реальной загрузке |

---

## 3. Схема БД

### 3.1. Новые таблицы

**`UnitDimension`** — enum (`native_enum=False`, VARCHAR + CHECK, name=`ck_unit_dimension`):
`mass`, `volume`, `length`, `count`.

**`ItemType`** — enum (`native_enum=False`, VARCHAR + CHECK, name=`ck_item_type`):
`material`, `delivery`, `other`. Ортогональная ось к `material_type` — определяет
роль строки в расчёте, не семейство материала.

**`units_of_measure`** — канонические единицы:

| Поле | Тип | Описание |
|---|---|---|
| `id` | int PK | |
| `code` | str, UNIQUE | `TON`, `KG`, `M3`, `L`, `M`, `PCS` |
| `name` | str | «Тонна», «Килограмм» |
| `symbol` | str | «т», «кг» (для UI) |
| `dimension` | enum `UnitDimension` | |
| `base_unit_id` | FK self, nullable, ON DELETE RESTRICT | NULL = эта единица базовая в размерности |
| `to_base_multiplier` | Numeric(30,15), default=1 | `qty_base = qty * multiplier` |

- CHECK: `base_unit_id IS NULL => to_base_multiplier = 1`
- Политика: `to_base_multiplier` **иммутабелен** (append-only, часть финансового аудита).
  Сейчас это соглашение уровня кода/ревью; при желании ужесточить — `BEFORE UPDATE`
  триггер, отклоняющий смену `to_base_multiplier` у существующей строки.
- Самоссылка через `relationship(remote_side=[id])`

**`unit_aliases`** — мост «сырая строка → канон»:

| Поле | Тип | Описание |
|---|---|---|
| `id` | int PK | |
| `raw_text` | str, UNIQUE | нормализованный ключ: `normalize_unit_key()` (см. ниже) |
| `unit_id` | FK → `units_of_measure`, ON DELETE CASCADE | |

Ключевое: `normalize_unit_key()` — **одна функция**, используемая и при сидинге,
и при поиске, и при бэкфилле. Реализация:

```python
def normalize_unit_key(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw or "")  # м³→м3, ²→2, NBSP→space
    s = re.sub(r"\s+", " ", s).strip().lower()     # схлопнуть внутренние пробелы
    return s.rstrip(".")                            # «куб.м.» → «куб.м»
```

NFKC снимает целый класс вариантов написания (`м³`↔`м3`, неразрывные пробелы),
поэтому держать `м3` и `м³` отдельными алиасами больше не нужно. Поиск в БД —
`WHERE raw_text = :normalized_key`, без `lower()` в SQL (UNIQUE даёт B-Tree индекс
бесплатно).

**`material_types`** — семейство материала:

| Поле | Тип | Описание |
|---|---|---|
| `id` | int PK | |
| `code` | str, UNIQUE | `concrete` / `rebar` / `other` |
| `name` | str | «Бетон», «Арматура», «Прочее» |
| `default_unit_id` | FK → `units_of_measure`, nullable, ON DELETE RESTRICT | подсказка для UI, **не** источник истины для цен |

### 3.2. Изменяемые таблицы

**`material_classes`:**
- DROP `material_type` (String)
- ADD `material_type_id` FK → `material_types`, NOT NULL (после бэкфилла), ON DELETE RESTRICT
- **INDEX** на `material_type_id`

**`invoice_items`:**
- RENAME `unit` → `raw_unit` (сырое из документа, аудит)
- ADD `normalized_unit_id` FK → `units_of_measure`, nullable, **INDEX** (фильтр калькулятора)
- ADD `normalized_quantity` Numeric(20,6), nullable
- ADD `normalized_unit_price` Numeric(24,6), nullable
- `quantity` / `unit_price` / `amount` остаются сырыми (как от парсера)
- `item_type`: String → Enum `ItemType` (VARCHAR + CHECK `ck_item_type`)

**`reference_prices`:**
- ADD `unit_id` FK → `units_of_measure`, NOT NULL (после бэкфилла), ON DELETE RESTRICT
- Валидация 1: `unit_id` должен быть базовой единицей (`base_unit_id IS NULL`)
- Валидация 2: размерность `unit_id` совпадает с ожидаемой для `material_type`
  класса (через `material_type.default_unit.dimension`) — fail early при вводе цены,
  а не молчаливый dimension mismatch на этапе расчёта.
  **При `default_unit_id IS NULL`** (material_type='other') валидация 2 пропускается —
  допустима любая базовая единица

**`compensation_corridors`** (поверх структуры из `corridor-fallback`; логика без изменений):
- Таблица уже имеет суррогатный `id` PK, `is_compensable`, nullable `corridor_pct`,
  CHECK `chk_corridor_pct_required_if_compensable` и два частичных уникальных индекса —
  **это не трогаем**.
- DROP `material_type` (String) → ADD `material_type_id` FK → `material_types`, nullable,
  ON DELETE RESTRICT (бэкфилл строки→id перед дропом, см. §7).
- Пересоздать CHECK `chk_corridor_target_exclusive` на `material_type_id`:
  `(material_type_id IS NOT NULL AND material_class_id IS NULL) OR (material_type_id IS NULL AND material_class_id IS NOT NULL)`.
- Пересоздать **оба** частичных индекса на `material_type_id` (у `uq_corridor_project_class`
  предикат `WHERE material_type IS NULL` ссылается на дропаемую колонку — иначе DROP COLUMN
  не пройдёт / снесёт индекс по CASCADE):
  - `uq_corridor_project_type` — `(project_id, material_type_id) WHERE material_class_id IS NULL`
  - `uq_corridor_project_class` — `(project_id, material_class_id) WHERE material_type_id IS NULL`

---

## 4. Логика нормализации (при приёме)

### 4.1. Нормализация в `create_invoice()`

Выполняется **один раз** при сохранении строки счёта:

```
normalize_unit_key(raw) → NFKC + collapse spaces + lower + rstrip('.')
                         ↓
              unit_aliases WHERE raw_text = key
                    ↓                     ↓
               найден alias           не найден
                    ↓                     ↓
        unit = alias.unit         normalized_* = NULL
        base = unit.base_unit     document.has_issues = True
              or unit
                    ↓
    normalized_quantity = quantity × unit.to_base_multiplier
    normalized_unit_price = unit_price / unit.to_base_multiplier
    normalized_unit_id = base.id
                    ↓
          ИНВАРИАНТ: norm_qty × norm_price ≈ amount
          (допуск: max(1₽, 0.1%))
```

> **Что проверяет инвариант.** `m` (multiplier) в произведении `norm_qty × norm_price`
> сокращается алгебраически → результат равен `quantity × unit_price` при любом `m`.
> Значит инвариант проверяет **согласованность исходной строки СФ** (`quantity ×
> unit_price ≈ amount`), а не корректность конвертации. Это ловит ошибки парсера и
> кривые строки документа — но **не** неверный множитель. Корректность `multiplier`
> закрыта unit-тестами §8.1 (прямая сверка `normalized_qty` с ожидаемым значением).

Функция-нормализатор: **чистая, unit-тестируемая без БД** (принимает `raw_unit: str`,
`aliases: dict[str, UnitAlias]` → возвращает `NormalizationResult | None`).
Поиск алиасов — один bulk-SELECT при обработке документа (не N запросов на строку).

### 4.2. Guard в калькуляторе

В `compute_calculations`, перед сравнением с эталонной ценой — два уровня защиты:

1. `normalized_unit_id IS NULL` → пропустить строку (нет нормализованной единицы)
2. `item.normalized_unit.dimension != reference_price.unit.dimension` → НЕ считать, пометить на ручную проверку

Оба случая — честный флаг, не тихий неверный расчёт.

> **Порядок гейтов.** Dimension guard и corridor resolution ортогональны и идут строго
> по порядку: (1) guard по размерности — при mismatch строка уходит на ручной флаг
> **независимо** от компенсируемости; (2) для прошедших guard — `resolve_corridor`
> (class→type→no-row, whitelist) из `corridor-fallback`.
>
> **Правка резолвера (R3).** Алгоритм резолвера не меняется, но ключ type-уровня в
> `get_corridor_map` / `resolve_corridor` переходит со строки `material_type` на
> `material_type_id`, а интеграция в `calculations.py` подаёт `class.material_type_id`
> (старая строка `material_classes.material_type` к этому моменту удалена).

### 4.3. Delivery distribution (обновлённая логика)

Текущая логика: доставка распределяется пропорционально `quantity` (м³) каждого класса.

**Проблема «яблок и апельсинов»:** при смешанных размерностях (м³ + т) суммирование
`normalized_quantity` физически бессмысленно: 50 м³ бетона + 2 т арматуры = 52 «условных попугаев».

**Решение:**

```python
dimensions = set(item.normalized_unit.dimension for item in materials if item.normalized_unit_id)

if len(dimensions) == 1:
    # Моно-размерность → распределение по normalized_quantity (физический смысл)
    share = class_qty / total_qty
elif len(dimensions) > 1:
    # Смешанные размерности → fallback по amount (деньги — универсальный знаменатель)
    share = class_amount / total_amount
```

**Edge cases:**
- `total_amount = 0` при fallback по amount (все материалы бесплатные) → доставка не распределяется (0 для всех строк), без DivisionByZero
- Часть строк `amount=0` → эти строки получают 0 доставки, ненулевые делят 100%
- Все `normalized_unit_id IS NULL` → доставка не распределяется (нет базы для пропорции)
- База распределения — только строки `item_type='material'`. Строки `item_type='other'`
  (скидки, округления, прочее) **не входят** ни в базу пропорции, ни в получатели доставки

### 4.4. Изменения в `compute_calculations()`

- `WHERE normalized_unit_id IS NOT NULL` — строки без нормализации исключены
- `SUM(normalized_quantity)` вместо `SUM(quantity)` для агрегации объёмов
- `avg_price` через нормализованные значения → всегда в базовой единице размерности
- Delivery distribution по обновлённой логике (4.3)

> **Агрегация — строго per-material_class.** `SUM(normalized_quantity)` и `avg_price`
> считаются только в пределах одного класса (один класс = одна размерность).
> Инвойс-wide суммирование количества **запрещено** — это те же «попугаи», что в 4.3.
> Через весь счёт допустимо агрегировать только деньги (`amount`).

### Без изменений

- `amount` (сырая сумма из СФ) — не пересчитывается
- `vat_amount` / `vat_rate` логика
- Monthly summary (`SUM(amount + vat)`) — оборот = как выставлено поставщиком, включает все `item_type` (material + delivery + other)
- Supplier aggregation endpoints — оборот включает все `item_type`

---

## 5. API

### 5.1. Новые эндпоинты (read-only для MVP)

```
GET /api/units                → list[UnitOfMeasure]  (id, code, name, symbol, dimension)
GET /api/units/{id}/aliases   → list[UnitAlias]
GET /api/material-types       → list[MaterialType]   (id, code, name, default_unit)
```

Управление справочниками — через миграции/CLI, не через UI.

### 5.2. Изменения в существующих эндпоинтах

**`POST /api/documents/{id}/parse`** (и upload flow):
- `create_invoice()` дополняется нормализацией (п. 4.1)
- `normalized_unit_id IS NULL` хотя бы у одной строки → `document.has_issues = True`

**`PUT /api/invoices/{id}`** (ручное редактирование):
- При изменении `raw_unit` или `quantity` → перенормализация
- Новые поля в `InvoiceItemEdit`: `raw_unit` (вместо `unit`)
- Warning при неизвестной единице:
  ```json
  {"warnings": [{"field": "raw_unit", "code": "unknown_unit",
    "message": "Единица измерения «бухта» не найдена в справочнике"}]}
  ```

**`POST /api/reference-prices`** и `PUT`:
- Новое обязательное поле `unit_id: int`
- Валидация 1: `unit_id` должен быть базовой единицей (`base_unit_id IS NULL`) — 422 иначе
- Валидация 2: размерность `unit_id` совпадает с размерностью `default_unit`
  типа материала класса — 422 иначе (иначе расчёт молча заблокируется guard'ом).
  При `default_unit IS NULL` (material_type='other') — пропускается, допустима любая
  базовая единица

**`GET /api/dashboard/calculations`**:
- Дополнительные поля: `unit_symbol` (символ базовой единицы), `dimension_mismatch: bool`

**`GET /api/export/excel`**:
- Два блока колонок:
  - Сырые данные из УПД: «Кол-во по документу», «Ед. изм. по документу»
  - Данные для расчёта: «Расчётное кол-во», «Базовая ед. изм.»
  - Пустые ячейки в расчётном блоке при `normalized_* IS NULL`

### 5.3. Без изменений

- `GET /api/dashboard/monthly-summary` — оборот по сырым `amount`
- Supplier endpoints — агрегация по `amount`
- Auth, admin, org endpoints

---

## 6. Парсер (`pdf_parser.py`)

Парсер **не меняется** в части извлечения данных — по-прежнему отдаёт `unit` как
сырую строку из PDF. Нормализация — ответственность `create_invoice()`.

Единственное изменение: `create_invoice()` резольвит `material_type` (строка от парсера)
в `material_type_id`:

```python
material_type_row = db.query(MaterialType).filter_by(code=item["material_type"]).first()
```

Неизвестный `material_type` от парсера → `has_issues`, класс материала не создаётся автоматически.

> **Резолвер коридора (R3).** После того как `material_classes.material_type` (String)
> заменён на `material_type_id`, существующий резолвер из `corridor-fallback`
> (`get_corridor_map` / `resolve_corridor`) сматчивает type-уровень по `material_type_id`,
> а не по строке. Логика class→type→no-row + whitelist остаётся прежней.

---

## 7. Миграция (Alembic)

Порядок критичен — NOT NULL ставится **только после** бэкфилла.

### Step 1: Schema — создание таблиц и nullable-колонок

```sql
CREATE TABLE units_of_measure (...)
CREATE TABLE unit_aliases (...)
CREATE TABLE material_types (...)

ALTER TABLE material_classes
  ADD COLUMN material_type_id INT REFERENCES material_types(id)
    ON DELETE RESTRICT  -- nullable на этом этапе

ALTER TABLE invoice_items
  RENAME COLUMN unit TO raw_unit  -- op.alter_column(..., new_column_name='raw_unit')
  ADD COLUMN normalized_unit_id INT REFERENCES units_of_measure(id)
  ADD COLUMN normalized_quantity NUMERIC(20,6)
  ADD COLUMN normalized_unit_price NUMERIC(24,6)

-- Pre-check перед CHECK constraint:
-- SELECT COUNT(*) FROM invoice_items WHERE item_type NOT IN ('material','delivery','other')
-- Если > 0 → миграция FAIL
ALTER TABLE invoice_items
  ADD CONSTRAINT ck_item_type CHECK (item_type IN ('material','delivery','other'))

ALTER TABLE reference_prices
  ADD COLUMN unit_id INT REFERENCES units_of_measure(id)
    ON DELETE RESTRICT

ALTER TABLE compensation_corridors
  ADD COLUMN material_type_id INT REFERENCES material_types(id)
    ON DELETE RESTRICT  -- унифицировано с material_classes: типы иммутабельны
```

### Step 2: Seed справочников (data migration)

```python
# Базовые единицы первыми (flush), затем производные с их id.
# multiplier — Decimal/строкой, НЕ float (0.001 во float неточен → ломает Numeric-аудит).
# Переменные *_ID — id строки после flush, используются в бэкфилле (Step 3) и алиасах.
TON_ID = insert(code="TON", name="Тонна",     symbol="т",  dimension="mass",   base_unit_id=None, multiplier=Decimal("1"))
KG_ID  = insert(code="KG",  name="Килограмм", symbol="кг", dimension="mass",   base_unit_id=TON_ID, multiplier=Decimal("0.001"))
M3_ID  = insert(code="M3",  name="Куб. метр", symbol="м³", dimension="volume", base_unit_id=None, multiplier=Decimal("1"))
L_ID   = insert(code="L",   name="Литр",      symbol="л",  dimension="volume", base_unit_id=M3_ID,  multiplier=Decimal("0.001"))
M_ID   = insert(code="M",   name="Метр",      symbol="м",  dimension="length", base_unit_id=None, multiplier=Decimal("1"))
PCS_ID = insert(code="PCS", name="Штука",     symbol="шт", dimension="count",  base_unit_id=None, multiplier=Decimal("1"))

# Алиасы — собрать из distinct-ключей: {normalize_unit_key(u) for u in raw_units}
# (NFKC уже объединил м³↔м3, поэтому отдельные строки под них не нужны).
# Минимальный набор (ключи — уже нормализованные):
# т/тн/тонн/тонна/t/ton   → TON
# кг/kg                    → KG
# м3/куб/куб.м             → M3   (м³ нормализуется в м3)
# л/l                      → L
# м/m/пог.м/п.м            → M
# шт/штук/pcs              → PCS  (шт. нормализуется в шт)

# material_types
concrete = insert(code="concrete", name="Бетон",    default_unit_id=M3)
rebar    = insert(code="rebar",    name="Арматура", default_unit_id=TON)
other    = insert(code="other",    name="Прочее",   default_unit_id=None)
```

### Step 3: Бэкфилл существующих данных

```python
# Предохранитель 1: SELECT DISTINCT material_type FROM material_classes
# Если есть значения вне {concrete, rebar, other} → миграция FAIL

# Предохранитель 2: читаем старую строковую колонку (material_type_id ещё NULL):
# SELECT DISTINCT mc.material_type
#   FROM reference_prices rp JOIN material_classes mc ON rp.material_class_id = mc.id
# assert result ⊆ {'concrete'} — иначе бэкфилл «всем M3» неверен

# material_classes.material_type_id ← маппинг строк
op.execute("""
  UPDATE material_classes SET material_type_id = (
    SELECT id FROM material_types WHERE code = material_classes.material_type
  )
""")

# reference_prices.unit_id ← M3 для всех (M3_ID получен из flush в Step 2)
op.execute(f"UPDATE reference_prices SET unit_id = {M3_ID}")

# compensation_corridors.material_type_id ← маппинг строк
op.execute("""
  UPDATE compensation_corridors SET material_type_id = (
    SELECT id FROM material_types WHERE code = compensation_corridors.material_type
  ) WHERE material_type IS NOT NULL
""")

# invoice_items.
# ВАЖНО: normalize_unit_key() (NFKC + схлопывание пробелов + rstrip '.') нельзя
# корректно воспроизвести в SQL через lower(trim(...)) — иначе бэкфилл разойдётся
# с рантаймом. Поэтому маппим через distinct-значения в Python (их единицы — не
# строки): строим карту normalize_unit_key(raw) → unit_id, затем bulk-апдейт.
# aliases_by_key — карта нормализованный ключ → {unit_id, base_id, multiplier},
# строится из засеянных алиасов (Step 2):
aliases_by_key = {
    row.raw_text: row for row in conn.execute("SELECT raw_text, unit_id, ... FROM unit_aliases JOIN units_of_measure ...")
}

distinct_raw = {r[0] for r in conn.execute("SELECT DISTINCT raw_unit FROM invoice_items")}
key_to_unit = {}                       # исходный raw → (unit_id, base_id, multiplier)
for raw in distinct_raw:
    alias = aliases_by_key.get(normalize_unit_key(raw or ""))
    if alias:
        key_to_unit[raw] = alias       # ключ — ИСХОДНЫЙ raw, чтобы матчить строки 1:1

for raw, u in key_to_unit.items():
    conn.execute(
        """UPDATE invoice_items
           SET normalized_unit_id = :base,
               normalized_quantity = quantity * :m,
               normalized_unit_price = unit_price / :m
           WHERE raw_unit = :raw""",
        {"base": u.base_id or u.unit_id, "m": u.multiplier, "raw": raw},
    )
# Несматченные raw_unit остаются с normalized_* = NULL → подхватятся has_issues.
```

### Step 4: Ужесточение

```sql
ALTER TABLE material_classes
  ALTER COLUMN material_type_id SET NOT NULL;
ALTER TABLE material_classes
  DROP COLUMN material_type;

ALTER TABLE reference_prices
  ALTER COLUMN unit_id SET NOT NULL;

-- Индексы (заявлены в §3.2, создаём после ужесточения NOT NULL):
CREATE INDEX ix_material_classes_material_type_id
  ON material_classes(material_type_id);
CREATE INDEX ix_invoice_items_normalized_unit_id
  ON invoice_items(normalized_unit_id);

-- compensation_corridors: конвертация таргета String → FK.
-- ОБА частичных индекса завязаны на material_type (один — ключом, другой — предикатом
-- WHERE material_type IS NULL), поэтому дропаем и пересоздаём оба, иначе DROP COLUMN
-- упадёт или снесёт uq_corridor_project_class по CASCADE.
DROP INDEX uq_corridor_project_type;
DROP INDEX uq_corridor_project_class;
ALTER TABLE compensation_corridors DROP CONSTRAINT chk_corridor_target_exclusive;
ALTER TABLE compensation_corridors DROP COLUMN material_type;   -- material_type_id уже забэкфилен (Step 3)
ALTER TABLE compensation_corridors ADD CONSTRAINT chk_corridor_target_exclusive CHECK (
  (material_type_id IS NOT NULL AND material_class_id IS NULL) OR
  (material_type_id IS NULL     AND material_class_id IS NOT NULL));
CREATE UNIQUE INDEX uq_corridor_project_type
  ON compensation_corridors(project_id, material_type_id)  WHERE material_class_id IS NULL;
CREATE UNIQUE INDEX uq_corridor_project_class
  ON compensation_corridors(project_id, material_class_id) WHERE material_type_id IS NULL;
-- chk_corridor_pct_required_if_compensable и суррогатный id PK — НЕ трогаем.
```

### Step 5: Down-миграция (обратимость)

> **Обратима по структуре, но лосси по данным новой схемы.** `reference_prices.unit_id`
> при down дропается, а повторный up снова проставит всем `M3`. Значит цена, заведённая
> под новой схемой в не-M3 единице (напр. арматура в TON), на цикле down→up схлопнется
> в M3. Для одноразовой миграции приемлемо; если под новой схемой успели появиться
> не-M3 ref_prices — перед down их `unit_id` нужно выгрузить отдельно.

```sql
-- Восстановить material_classes (симметрично с коридором):
ALTER TABLE material_classes ADD COLUMN material_type VARCHAR;
UPDATE material_classes SET material_type = (
  SELECT code FROM material_types WHERE id = material_type_id
);
ALTER TABLE material_classes ALTER COLUMN material_type SET NOT NULL;
DROP INDEX ix_material_classes_material_type_id;
ALTER TABLE material_classes DROP COLUMN material_type_id;

-- Откат compensation_corridors к строковой структуре corridor-fallback
-- (НЕ к до-fallback композитному PK — это зона ответственности down самого fallback):
DROP INDEX uq_corridor_project_type;
DROP INDEX uq_corridor_project_class;
ALTER TABLE compensation_corridors DROP CONSTRAINT chk_corridor_target_exclusive;
ALTER TABLE compensation_corridors ADD COLUMN material_type VARCHAR;
UPDATE compensation_corridors SET material_type = (
  SELECT code FROM material_types WHERE id = material_type_id
) WHERE material_type_id IS NOT NULL;
ALTER TABLE compensation_corridors DROP COLUMN material_type_id;
ALTER TABLE compensation_corridors ADD CONSTRAINT chk_corridor_target_exclusive CHECK (
  (material_type IS NOT NULL AND material_class_id IS NULL) OR
  (material_type IS NULL     AND material_class_id IS NOT NULL));
CREATE UNIQUE INDEX uq_corridor_project_type
  ON compensation_corridors(project_id, material_type)     WHERE material_class_id IS NULL;
CREATE UNIQUE INDEX uq_corridor_project_class
  ON compensation_corridors(project_id, material_class_id) WHERE material_type IS NULL;

-- Откат invoice_items:
ALTER TABLE invoice_items RENAME COLUMN raw_unit TO unit;
DROP INDEX ix_invoice_items_normalized_unit_id;
ALTER TABLE invoice_items DROP COLUMN normalized_unit_id, normalized_quantity, normalized_unit_price;
ALTER TABLE invoice_items DROP CONSTRAINT ck_item_type;

-- Откат reference_prices:
ALTER TABLE reference_prices DROP COLUMN unit_id;

-- DROP таблиц:
DROP TABLE unit_aliases;
DROP TABLE units_of_measure;
DROP TABLE material_types;
```

---

## 8. Тестирование

### 8.1. Unit-тесты (без БД)

**`test_unit_normalization.py`:**
- `т` → TON, qty=5 → normalized_qty=5, multiplier=1
- `кг` → KG → TON, qty=5000 → normalized_qty=5.0, multiplier=0.001
- `тонн` / `тн` / `t` → все → TON
- `м³` (U+00B3) и `м3` (digit) → оба → M3 (NFKC нормализация)
- Неизвестная единица `"бухта"` → `None`
- Пустая строка / None → `None`
- Инвариант: `normalized_qty × normalized_unit_price ≈ amount`
- Граница допуска: расхождение ровно 0.1% → pass; 0.11% → fail
- Граница допуска: расхождение ровно 1₽ → pass; 1.01₽ → fail

**`test_dimension_guard.py`:**
- Одинаковая размерность (volume/volume) → расчёт проходит
- Разная размерность (volume/mass) → расчёт заблокирован
- `normalized_unit_id IS NULL` → строка пропущена

**`test_delivery_distribution.py`:**
- Моно-размерность (все volume) → по `normalized_quantity`
- Смешанные размерности (volume + mass) → fallback по `amount`
- Все строки без нормализации → доставка не распределяется
- Edge: `amount=0` (бесплатный материал) при fallback → 0 доставки, ненулевые = 100%, без DivisionByZero
- Edge: все `amount=0` при fallback → доставка = 0 для всех, без DivisionByZero

**`test_compensation_with_units.py`:**
- `compute_compensation_per_unit` с нормализованными значениями — поведение не меняется
- Dimension mismatch → compensation = None

**`test_item_type_enum.py`:**
- `"material"` / `"delivery"` / `"other"` — ок
- `"unknown"` → ошибка валидации

### 8.2. Интеграционные тесты (с БД)

**`test_normalization_integration.py`:**
- Полный flow: upload PDF → create_invoice → проверить `normalized_*` в БД
- Документ с неизвестной единицей → `has_issues=true`, `normalized_* IS NULL`
- Ручное редактирование `raw_unit` → перенормализация
- Case-insensitive дубли алиасов: `normalize_unit_key("Т ") == "т"` → get_or_create возвращает существующий, не INSERT

**`test_calculations_with_units.py`:**
- `compute_calculations` с нормализованными данными — результат в базовых единицах
- Смешанный счёт (м³ + т) → доставка по amount, расчёт корректен
- Строки с `normalized_unit_id IS NULL` → исключены из агрегатов

**`test_reference_prices_unit.py`:**
- Создание ref_price с `unit_id` — ок
- Попытка создать ref_price с производной единицей (KG вместо TON) → 422
- Попытка создать ref_price с базовой единицей неверной размерности (TON для бетона) → 422
- Guard: ref_price dimension != item dimension → расчёт заблокирован

**`test_migration_backfill.py`** (одноразовый):
- Предохранитель: неизвестный `material_type` → миграция падает
- Бэкфилл `material_classes.material_type_id` — все строки заполнены
- Бэкфилл `reference_prices.unit_id` — все = M3
- Бэкфилл `invoice_items.normalized_*` — сматченные через алиасы

### 8.3. Frontend тесты

- MSW handlers: `GET /api/units`, `GET /api/material-types`
- `PUT /api/invoices/{id}` с unknown unit → warning в UI
- Форма создания ref_price — выпадающий список единиц, валидация

### 8.4. Что НЕ тестируем (YAGNI)

- UI-редактор справочников (его нет)
- Reprocess endpoint (его нет)
- Самообучение алиасов (backlog)
- Конвертацию между размерностями (вне scope)

---

## 9. Риски и митигация

| Риск | Митигация |
|---|---|
| Бэкфилл `material_type` не покрывает все значения | `SELECT DISTINCT` + fail перед UPDATE |
| Бэкфилл `item_type` содержит мусор | Pre-check `COUNT(*) WHERE NOT IN (...)` перед CHECK |
| Изменение `to_base_multiplier` ломает историю | Иммутабельность (append-only политика; опционально — BEFORE UPDATE триггер) |
| Float-погрешность в деньгах | Везде `Numeric`, `money_round`, `_dec()` |
| Скрытый межразмерностный расчёт | Guard по `dimension` + инвариант на приёме |
| Парсер выдаёт невиданное написание единиц | `normalized_*=NULL`, `has_issues=true`, append-only алиасы |
| Delivery distribution DivisionByZero | Edge case: `amount=0` → 0 доставки, явно в коде и тестах |
| Down-миграция теряет не-M3 ref_prices | Восстановление строковых полей до DROP; лосси для unit_id — задокументировано |
| Инвариант не ловит неверный multiplier | Корректность multiplier закрыта unit-тестами, не инвариантом |
| Нарезка миграции на коммиты разъединяет бэкфилл и DROP | Step 3 (бэкфилл) и Step 4 (DROP) — одна миграция Alembic |
| Предохранитель 2 проходит вхолостую при timing bug | Читаем старую строковую `mc.material_type`, не JOIN через `material_type_id` (R4) |

---

## 10. Критерии приёмки

- [ ] `т`/`тн`/`тонн`/`кг` маппятся в единый канон через `unit_aliases`
- [ ] `м³` (U+00B3) и `м3` (digit) нормализуются в одно через NFKC
- [ ] При приёме строки заполняются `normalized_*`; инвариант `qty × price ≈ amount`
- [ ] Неизвестная единица → `normalized_*=NULL`, document `has_issues=true`
- [ ] Компенсация и avg_price считаются в базовой единице размерности, **per-material_class**
- [ ] Dimension mismatch блокируется guard'ом, не считается молча
- [ ] Delivery: моно-dimension → по qty, mixed → по amount, zero-amount → без деления на 0
- [ ] `item_type='other'` вне delivery distribution, но входит в оборот (monthly summary, supplier)
- [ ] `item_type` валидируется CHECK constraint (`ck_item_type`)
- [ ] `CompensationCorridor.material_type_id` — FK, не строка; **оба** частичных индекса
      (`uq_corridor_project_type`, `uq_corridor_project_class`) и CHECK
      `chk_corridor_target_exclusive` пересозданы на `material_type_id`
- [ ] Логика коридоров (class→type→no-row, `is_compensable`, whitelist) не изменилась;
      резолвер сматчивает type-уровень по `material_type_id`
- [ ] `reference_prices.unit_id` — NOT NULL, только базовые единицы, dimension совпадает с material_type
- [ ] Миграция up проходит без потери данных; down обратим по структуре (лосси для не-M3 `unit_id`, см. §7 Step 5)
- [ ] Предохранители в миграции: неизвестные значения → fail
- [ ] Excel: два блока колонок (сырые + расчётные), заголовок «Расчётное кол-во»
- [ ] Warning в API при unknown unit после ручного редактирования
- [ ] Именованные CHECK constraints (`ck_unit_dimension`, `ck_item_type`)
- [ ] ON DELETE RESTRICT для всех FK в `material_types` и `units_of_measure` (кроме `unit_aliases` → CASCADE)
- [ ] Индексы на `normalized_unit_id` и `material_type_id`

---

## 11. Ключевые решения (ADR)

1. **item_type остаётся** — ортогональная ось к `material_type` (роль строки в расчёте vs семейство материала). Переезжает на Enum + CHECK.
2. **CompensationCorridor.material_type → material_type_id FK** — раз `material_classes.material_type` уезжает в FK, строковый таргет коридора рассинхронит резолвер (строка vs id); FK — условие согласованности `resolve_corridor`, не косметика. Логика коридоров (class→type→no-row, whitelist, is_compensable) из `corridor-fallback` сохраняется.
3. **Write-time нормализация** — единственный корректный подход для финансовой системы (аудируемость, детерминистичность, иммутабельность истории).
4. **Delivery distribution: qty для моно-dimension, amount для mixed** — физический смысл в строительной логистике (фрахт зависит от объёма/массы, не от стоимости груза).
5. **Неизвестная единица → has_issues, не error** — гранулярность (49 из 50 строк считаются), не блокировка всего документа.
6. **BAG вне scope** — YAGNI, расширяем append-only когда появится в данных.
7. **Арматура: default_unit=TON** — ~90% приходит в тоннах; пог.м отловит guard.
8. **`ON DELETE RESTRICT` для всех FK в `material_types`/`units_of_measure`** (R2) —
   справочники иммутабельны; CASCADE на reference-данных опасен и непоследователен.
9. **`normalize_unit_key` с NFKC** (R2) — детерминированно объединяет `м³`↔`м3`, NBSP,
   суффиксные точки до уровня ключа; одна функция в рантайме, сидинге и бэкфилле.

---

## 12. Закрытые вопросы (из ревью)

1. **Пог.м арматуры (~10%).** Приемлемо операционно; перевод пог.м→т через линейный
   вес остаётся в backlog. Поднимем при реальной загрузке арматурных документов.

2. **Агрегация per-class.** Зафиксировано в §4.4: `SUM(normalized_quantity)` и `avg_price`
   строго per-material_class. Проверим при реализации, что в существующем
   `compute_calculations` нет invoice-wide агрегации количества.

3. **`item_type='other'` в обороте.** Входят в monthly summary и supplier-агрегаты
   (текущее поведение, не меняем). Вне delivery distribution (§4.3).

4. **Историческая чистота ref_prices.** Предохранитель 2 (§7 Step 3) проверит; если
   в проде окажутся не-concrete ref_prices, миграция упадёт с понятной ошибкой.
