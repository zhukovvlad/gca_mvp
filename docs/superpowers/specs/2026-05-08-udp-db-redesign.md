# УПД Трекер — Редизайн БД

**Дата:** 2026-05-08
**Статус:** Утверждён

---

## Контекст

Система отслеживает удорожание строительных материалов (бетон, арматура) по объектам. Пользователь загружает PDF с счетами-фактурами от генподрядчика, система извлекает данные через ИИ, рассчитывает среднюю цену за период и сравнивает с эталонной ценой из договора. Разница — основание для доплаты/возврата.

---

## Workflow пользователя

1. Загрузка PDF → система определяет: счёт-фактура или мусор
2. Пользователь указывает объект (проект)
3. ИИ извлекает: номер, дата, поставщик, позиции (материал с классом, доставка), НДС
4. Система нормализует класс материала (из "Бетон В40 П4 F200 W12 ПМД -5 гравий" → "В40")
5. Считается средняя цена за м3 = (сумма_материала_с_НДС + сумма_доставки_с_НДС) / кол-во_м3
6. Средняя за период сравнивается с эталоном по (объект + класс + период)
7. Отчёт: объект, период, материал, фактическая цена, эталон, отклонение, список СФ

---

## Особенности данных

- Один PDF может содержать несколько счетов-фактур
- В одной СФ может быть несколько строк бетона (разные рейсы)
- Доставка может быть отдельной строкой или зашита в цену (тогда delivery = 0)
- Ставка НДС берётся из документа (обычно 20%)
- Все суммы хранятся с НДС, НДС отдельно для справки
- Классы материалов — динамический справочник (не enum)
- Эталонные цены определяются договором/допсоглашением, могут различаться по объектам
- Период расчёта определяется договором (не фиксированный месяц/квартал)

---

## Схема БД

### projects (объекты строительства)

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK | |
| name | TEXT NOT NULL | "ЖК Ромашка", "Школа №5" |
| contract_number | TEXT | Номер договора |
| created_at | DATETIME | |

### material_classes (справочник классов материалов)

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK | |
| material_type | TEXT NOT NULL | "concrete" / "rebar" / "other" |
| name | TEXT NOT NULL | "В15", "В40" — нормализованное |
| created_at | DATETIME | |

### reference_prices (эталонные цены из договоров)

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK | |
| project_id | FK → projects | |
| material_class_id | FK → material_classes | |
| price | REAL NOT NULL | Эталонная цена за ед. с НДС |
| period_start | DATE | Начало периода действия |
| period_end | DATE | Конец периода действия |
| source | TEXT | "договор" / "допсоглашение №2" |

### documents (загруженные PDF)

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK | |
| project_id | FK → projects | Пользователь указывает при загрузке |
| filename | TEXT | Исходное имя файла |
| s3_key | TEXT | Путь в MinIO |
| doc_type | TEXT | "invoice" / "unknown" |
| status | TEXT | "parsed" / "review" / "error" / "rejected" |
| uploaded_at | DATETIME | |

### invoices (счета-фактуры из документа)

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK | |
| document_id | FK → documents | |
| number | TEXT NOT NULL | Номер СФ |
| date | DATE NOT NULL | Дата СФ |
| supplier_name | TEXT | Как написано в документе |
| supplier_inn | TEXT | ИНН из документа |
| vat_rate | REAL | Ставка НДС (20, 10, 0) |
| ai_confidence | REAL | 0.0–1.0 |
| created_at | DATETIME | |

### invoice_items (позиции счёт-фактуры)

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK | |
| invoice_id | FK → invoices | |
| raw_name | TEXT | Сырое наименование из документа |
| item_type | TEXT | "material" / "delivery" / "other" |
| material_class_id | FK → material_classes | NULL для доставки |
| quantity | REAL | Кол-во |
| unit | TEXT | м3, рейс, час... |
| unit_price | REAL | Цена за ед. с НДС |
| amount | REAL | Сумма с НДС |
| vat_amount | REAL | НДС из суммы (для справки) |

### price_calculations (расчётный кэш средних цен)

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK | |
| project_id | FK → projects | |
| material_class_id | FK → material_classes | |
| period_start | DATE | |
| period_end | DATE | |
| material_total | REAL | Сумма материала с НДС |
| material_vat | REAL | НДС (справка) |
| delivery_total | REAL | Сумма доставки с НДС |
| delivery_vat | REAL | НДС (справка) |
| total_qty | REAL | м3 бетона |
| avg_price | REAL | (material_total + delivery_total) / total_qty |
| invoice_count | INTEGER | Кол-во СФ в расчёте |
| reference_price | REAL | Эталон (копия для отчёта) |
| deviation_pct | REAL | Отклонение % |
| deviation_amount | REAL | Отклонение ₽ (на весь объём) |
| calculated_at | DATETIME | |

---

## Формула расчёта средней цены

```
avg_price = (material_total + delivery_total) / total_qty

где:
  material_total = Σ(amount) по позициям с item_type="material" за период
  delivery_total = Σ(amount) по позициям с item_type="delivery" за период
  total_qty = Σ(quantity) по позициям с item_type="material" за период

deviation_pct = (avg_price - reference_price) / reference_price × 100
deviation_amount = (avg_price - reference_price) × total_qty
```

---

## Отчёт

Содержит:
- Объект
- Период
- Материал (класс)
- Средняя фактическая цена за период
- Эталонная цена
- Отклонение (% и ₽)
- Список СФ, вошедших в расчёт
