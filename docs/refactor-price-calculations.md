# Рефакторинг: замена `price_calculations` кеш-таблицы на live-вычисление

> Дата: 2026-05-17
> Статус: реализована

---

## Проблема

Таблица `price_calculations` — ручной кеш с пассивной инвалидацией.

### 1. Кеш протухает молча

Ни одно из этих действий **не пересчитывает** `price_calculations`:
- Загрузка нового PDF / создание инвойса
- Редактирование инвойса (`PUT /api/invoices/{id}`)
- Изменение базовой цены (`PATCH /api/reference-prices/{id}`)
- Слияние поставщиков

Пользователь видит устаревшие цифры, пока вручную не нажмёт «Рассчитать» на странице проекта.

### 2. Три параллельные реализации расчёта отклонений

| Где | Источник | Выбор базовой цены | Область |
|---|---|---|---|
| Таблица расчётов (кеш) | `price_calculations` | По пересечению периодов | Все поставщики |
| KPI-карточка проекта | `compute_full_deviation()` — live | По пересечению периодов | Все поставщики |
| Карточка поставщика | `_compute_supplier_project_deviation()` — live | Самая свежая, без фильтра по периоду | Один поставщик |

KPI-карточка и таблица расчётов на одной странице могут показывать **разные** суммы отклонений.

### 3. `reference_price` зашит как снимок

Если базовую цену обновили — `deviation_pct` и `deviation_amount` в кеше остаются от старой цены. Индикатора «данные устарели» в UI нет.

### 4. Нет уникального ключа / индекса

На `(project_id, material_class_id, period_start, period_end)` нет ни unique constraint, ни составного индекса. Код компенсирует через DELETE + INSERT.

### 5. Мёртвый код

`POST /dashboard/auto-calculate`, `useAutoCalculate()`, `AutoCalculateResponse` — написаны, но нигде не вызываются в UI.

---

## Решение: вариант B — live-вычисление с фильтром по периоду

- `GET /dashboard/calculations` считает на лету (SQL-агрегация), принимает опциональные `period_start` / `period_end`
- Без периода — автоматически определяет диапазон по датам инвойсов, возвращает все месяцы
- Поля дат на ProjectPage становятся реактивными фильтрами (без кнопки «Рассчитать»)
- Таблица `price_calculations` удаляется
- Формат ответа `DashboardCalculation` меняется: поле `id` (бралось из кеш-таблицы) больше не возвращается — breaking change; стабильный ключ строки: `project_id` + `material_class_id` + `period_start`

### Что это даёт

| Проблема | Решение |
|---|---|
| Протухший кеш | Нет кеша — данные всегда свежие |
| 3 реализации отклонений | `compute_full_deviation()` делегирует в `compute_calculations()` — единый источник |
| Снимок reference_price | JOIN к актуальной `reference_prices` при каждом запросе |
| Нет индекса | Таблица удаляется |
| Мёртвый код | Удаляется (`auto-calculate`, `useAutoCalculate`, `AutoCalculateResponse`) |
| Кнопка «Рассчитать» | Убирается — UX упрощается |

### Что НЕ меняется

- `_compute_supplier_project_deviation()` — другая бизнес-семантика (один поставщик, другой выбор базовой цены), остаётся as-is
- `GET /dashboard/monthly-summary` — уже считает live, не зависит от `price_calculations`
- `GET /dashboard/summary` — уже считает live через `compute_full_deviation()`

---

## План реализации

### Фаза 1 — Новая функция `compute_calculations()` (backend/crud.py)

**1.1** Добавить `compute_calculations(db, project_id, period_start=None, period_end=None, material_class_id=None) -> list[dict]`

Логика:
- Если period не задан → определить по `MIN/MAX(Invoice.date)` для проекта
- Нет инвойсов → `[]`
- Разбить диапазон на календарные месяцы (1-е — последний день)
- Для каждого месяца: та же агрегация что в `recalculate_prices()` — material items по классу, delivery proration, reference price lookup, deviation
- Вернуть `list[dict]` с полями: `material_class_name`, `material_class_id`, `project_id`, `period_start`, `period_end`, `avg_price`, `reference_price`, `deviation_pct`, `deviation_amount`, `material_total`, `delivery_total`, `material_vat`, `delivery_vat`, `total_qty`, `invoice_count`
- Пропускать строки с `total_qty == 0`

**1.2** Переписать `compute_full_deviation()` — делегировать в `compute_calculations()` и суммировать `deviation_amount`. Гарантирует что KPI-карточка и таблица расчётов всегда совпадают.

> **⚠ Граничный случай `None`:** функция должна возвращать `None` если ни у одного класса нет reference price (а не `0.0`). Реализация:
> ```python
> rows = compute_calculations(db, project_id, period_start, period_end)
> amounts = [r["deviation_amount"] for r in rows if r["deviation_amount"] is not None]
> return round(sum(amounts), 2) if amounts else None
> ```
> Без этого KPI-карточка покажет «Отклонение: 0 ₽» там, где должна быть пустая ячейка.

**1.3** Удалить `recalculate_prices()` (после того как все вызовы убраны).

### Фаза 2 — Роутеры (backend/routers/)

**2.1** `GET /dashboard/calculations` (dashboard.py:112-139) — переписать:
- Добавить query params: `period_start?: date`, `period_end?: date`
- Если `project_id` задан → `crud.compute_calculations(db, project_id, ...)`
- Если нет → итерировать все проекты, собрать результаты (для Dashboard)
- Формат ответа: общая структура сохранена, но поле `id` удалено — live-ответ не опирается на строки таблицы. Стабильный ключ записи: `project_id + material_class_id + period_start`

> **Производительность:** при вызове без `project_id` выполняется N запросов `compute_calculations()` — по одному на проект. Для MVP приемлемо; добавить пункт в `TECH_DEBT.md` для будущей оптимизации (единый SQL с `GROUP BY project_id`).

**2.2** `GET /export/excel` (export.py:15-73) — заменить запрос к `PriceCalculation` на вызов `crud.compute_calculations()`. Итерировать `list[dict]` вместо ORM-объектов.

**2.3** Удалить `POST /dashboard/calculate` (dashboard.py:244-293)

**2.4** Удалить `POST /dashboard/auto-calculate` (dashboard.py:142-204)

> **⚠ 2.3 и 2.4 выполнять одновременно** — к этому моменту `recalculate_prices()` должна ещё существовать (она удаляется в Фазе 1.3). Оба эндпоинта должны исчезнуть в одном коммите: если один из них останется без второго — риска нет, но удалить вместе чище.

### Фаза 3 — Удаление модели и миграция

**3.1** `backend/models.py` — удалить класс `PriceCalculation`, убрать relationship из `Project` и `MaterialClass`

**3.2** `backend/crud.py` — убрать импорт `PriceCalculation`, удалить строку удаления в `delete_material_class()`

**3.3** `backend/routers/dashboard.py` — убрать импорт `PriceCalculation`, вычистить неиспользуемые импорты

**3.4** `backend/routers/export.py` — убрать импорт `PriceCalculation`

**3.5** Скрипты: `backend/scripts/reset_documents.py`, `backend/scripts/migrate_sqlite_to_postgres.py` — убрать `PriceCalculation` ссылки

**3.6** Alembic миграция: `DROP TABLE price_calculations` (down_revision = `b3c7e9f12a45`)

> Downgrade необратим — в `def downgrade()` поставить `raise NotImplementedError("price_calculations table dropped intentionally")`.

### Фаза 4 — Фронтенд

**4.1** `frontend/src/services/api/dashboard.ts`:
- `calculations(projectId, periodStart?, periodEnd?)` — передавать period как query params
- Удалить `calculate()` и `autoCalculate()`

**4.2** `frontend/src/services/queryKeys.ts`:
- `calculations: (projectId, periodStart?, periodEnd?) => [...]` — включить period в ключ

**4.3** `frontend/src/services/queries.ts`:
- `useDashboardCalculations(projectId, periodStart?, periodEnd?)` — реактивный запрос
- Удалить `useCalculate()`, `useAutoCalculate()`

**4.4** `frontend/src/types/dashboard.ts`:
- Удалить `CalculateInput`, `AutoCalculateResponse`

**4.5** `frontend/src/pages/ProjectPage.tsx`:
- Убрать `calculateMut`, `handleCalculate`, импорт `useCalculate`
- Передать `periodStart`, `periodEnd` в `useDashboardCalculations()`
- Убрать кнопку «Рассчитать»
- Оставить date-инпуты как фильтры + кнопка «Сбросить»
- Убрать блок «Последний расчёт» (`calculated_at` отсутствует в `DashboardCalculation` TypeScript-типе и в UI не отображается — удаление безопасно)

> **Дебаунс date-фильтров:** при `onChange` каждый символ при вводе даты отправляет запрос (`2026-0` → `2026-05` → ...). Использовать `useDebounce(periodStart, 400)` / `useDebounce(periodEnd, 400)` перед передачей в хук, или переключить инпуты на `onBlur`.

### Фаза 5 — Тесты

**5.1** `backend/tests/unit/test_crud_recalculate.py` → переименовать в `test_crud_compute_calculations.py`, переписать на `compute_calculations()`. Обязательно добавить тест **delivery-allocation**: несколько классов материалов с разными объёмами → доставка распределяется пропорционально `qty / all_qty`. Текущие 3 теста эту логику не покрывают.

**5.2** `backend/tests/integration/test_dashboard.py` — удалить тесты POST-эндпоинтов, добавить тесты live data + period filter

**5.3** `backend/tests/integration/test_export.py` — обновить под live data

**5.4** `frontend/src/test/handlers.ts` — убрать POST-хендлеры

**5.5** `frontend/src/pages/ProjectPage.test.tsx` — убрать ссылки на «Рассчитать», добавить тест на фильтр

### Фаза 6 — Документация

**6.1** `docs/TECH_DEBT.md` — убрать пункты про N+1 auto_calculate и отсутствующий индекс PriceCalculation; добавить новый пункт: «`GET /dashboard/calculations` без `project_id` выполняет N запросов к БД — оптимизировать через единый SQL с `GROUP BY project_id` при росте числа проектов».

**6.2** `CLAUDE.md` — обновить секцию "Database models" и ссылки на `PriceCalculation`

---

## Порядок выполнения (приложение работает на каждом шаге)

1. Фаза 1.1 + 1.2 — добавить новую функцию, переключить `compute_full_deviation`
2. Фаза 2.1 + 2.2 — переключить GET-эндпоинты на live data
3. Фаза 4 — фронтенд на реактивные фильтры
4. Фаза 2.3 + 2.4 — удалить POST-эндпоинты
5. Фаза 1.3 + 3 — удалить `recalculate_prices`, модель, миграция
6. Фаза 5 — тесты
7. Фаза 6 — документация

---

## Ключевые файлы

| Файл | Действие |
|---|---|
| `backend/crud.py` | Новая `compute_calculations()`, рефакторинг `compute_full_deviation()`, удаление `recalculate_prices()` |
| `backend/models.py` | Удалить `PriceCalculation` |
| `backend/routers/dashboard.py` | Переписать GET, удалить 2 POST |
| `backend/routers/export.py` | Переключить на `compute_calculations()` |
| `frontend/src/pages/ProjectPage.tsx` | Реактивные фильтры, убрать кнопку |
| `frontend/src/services/api/dashboard.ts` | Period params, удалить mutations |
| `frontend/src/services/queries.ts` | Обновить хук, удалить mutations |
| `frontend/src/types/dashboard.ts` | Удалить мёртвые типы |

## Верификация

```bash
just test-backend-unit        # crud compute_calculations тесты
just test-backend-integration # dashboard + export тесты
just test-frontend            # ProjectPage + Dashboard тесты
just lint                     # ruff + eslint
just typecheck-frontend       # tsc
```

Ручная проверка: открыть ProjectPage, убедиться что данные появляются без нажатия кнопки, фильтр по периоду работает реактивно.
