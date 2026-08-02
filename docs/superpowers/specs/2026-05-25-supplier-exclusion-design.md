# Исключение поставщиков из расчётов

**Дата:** 2026-05-25  
**Статус:** approved

## Контекст

Тендерный менеджер хочет исключать отдельных поставщиков из расчёта средней цены и отчётов — например, если поставщик работал только на разовой аварийной закупке и его цены не репрезентативны. Исключение действует на уровне объекта: один и тот же поставщик может быть включён на одном объекте и исключён на другом.

## Решение

Новая join-таблица `project_supplier_exclusions` (opt-out: запись = исключён). Управляется через таб «Поставщики» в карточке объекта — чекбокс в строке поставщика. Фильтрация применяется во всех расчётных путях: карточка объекта (`GET /dashboard/calculations?project_id=X`), summary (`GET /dashboard/summary?project_id=X`), monthly-summary (`GET /dashboard/monthly-summary?project_id=X`), Excel-экспорт. Глобальный дашборд без `project_id` (`GET /dashboard/calculations` без параметра) — исключения применяются через bulk-prefetch: все записи `project_supplier_exclusions` загружаются одним запросом и раздаются по проектам.

## База данных

```sql
CREATE TABLE project_supplier_exclusions (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    reason      TEXT,
    created_at  TIMESTAMP DEFAULT (now() AT TIME ZONE 'utc'),
    PRIMARY KEY (project_id, supplier_id)
);
```

Миграция Alembic. `reason` — необязательное поле, поставщик может быть исключён без объяснения. В будущем можно добавить `excluded_by INTEGER FK users`.

**Ограничение:** инвойсы с `supplier_id IS NULL` (парсер не смог извлечь имя поставщика) всегда участвуют в расчётах — их нельзя исключить через эту механику. Это намеренное ограничение: такие инвойсы считаются невалидными данными, пользователь должен их исправить вручную.

## Backend

### Новый CRUD: `crud/supplier_exclusions.py`

```python
def get_excluded_supplier_ids(db: Session, project_id: int) -> set[int]
def set_supplier_excluded(db: Session, project_id: int, supplier_id: int, excluded: bool, reason: str | None = None) -> None
```

### Новые эндпоинты

Добавляются в `routers/projects.py` (или отдельный `routers/project_suppliers.py`):

```
GET  /api/projects/{project_id}/suppliers
     → [{id, name, inn, invoice_count}]
     Список поставщиков проекта с их supplier_id. Заменяет клиентскую агрегацию.

POST   /api/projects/{project_id}/supplier-exclusions/{supplier_id}
       Body: {"reason": "..."}  (необязательно)
       → 204 No Content. Добавить исключение (идемпотентно).

DELETE /api/projects/{project_id}/supplier-exclusions/{supplier_id}
       → 204 No Content. Снять исключение.

GET /api/projects/{project_id}/supplier-exclusions
    → [supplier_id, ...]  список исключённых supplier_id
```

### Изменения в расчётных функциях (`crud/calculations.py`)

```python
def compute_calculations(
    db, project_id, period_start=None, period_end=None,
    material_class_id=None,
    excluded_supplier_ids: set[int] | None = None,   # новый параметр
) -> list[dict]: ...

def compute_full_deviation(
    db, project_id, period_start, period_end,
    excluded_supplier_ids: set[int] | None = None,
) -> float | None: ...

def compute_export_rows(
    db, project_id, period_start=None, period_end=None,
    material_class_id=None,
    excluded_supplier_ids: set[int] | None = None,
) -> list[dict]: ...
```

Фильтр добавляется к запросу `invoice_ids_month` / `invoices_raw`:
```python
if excluded_supplier_ids:
    q = q.filter(
        or_(Invoice.supplier_id.is_(None),
            Invoice.supplier_id.notin_(excluded_supplier_ids))
    )
```

Инвойсы с `supplier_id IS NULL` явно пропускаются через `is_(None)` — они не должны блокироваться исключением.

### Изменения в роутерах

`routers/dashboard.py` и `routers/export.py` — перед вызовом расчётных функций:
```python
excluded = get_excluded_supplier_ids(db, project_id)
rows = compute_calculations(db, project_id, ..., excluded_supplier_ids=excluded)
```

## Frontend

### Новые TanStack Query хуки (`services/queries.ts`)

```typescript
useProjectSuppliers(projectId)          // GET /api/projects/{id}/suppliers
useSupplierExclusions(projectId)        // GET /api/projects/{id}/supplier-exclusions → Set<number>
useToggleSupplierExclusion(projectId)   // mutation: POST / DELETE
```

### Таб «Поставщики» в `ProjectPage.tsx`

- Данные берутся из `useProjectSuppliers` (не из клиентской агрегации инвойсов).
- Добавляется колонка «В расчётах» с чекбоксом:
  - ✓ (checked) = поставщик **включён**
  - ☐ (unchecked) = поставщик **исключён**
- **Включение** (☐ → ✓): DELETE без подтверждения. После успешного ответа инвалидируются связанные запросы (`supplier-exclusions`, `calculations`, `summary`) и UI обновляется из refetch.
- **Исключение** (✓ → ☐): открывается небольшой Popover под строкой с полем «Причина исключения (необязательно)» и кнопками «Исключить» / «Отмена». POST отправляется только после подтверждения. Если поле пустое — `reason: null`. После успешного ответа инвалидируются связанные запросы (`supplier-exclusions`, `calculations`, `summary`) и UI обновляется из refetch.
- Таб показывает только поставщиков, возвращённых `GET /api/projects/{id}/suppliers`; записи инвойсов без `supplier_id` в этот список не попадают (нет отдельного disabled-чекбокса для них).

### Таб «Обзор» в `ProjectPage.tsx`

Если `useSupplierExclusions` возвращает непустое множество — ненавязчивый баннер под KPI:
> «N поставщиков исключено из расчётов · [Управление]»

Клик «Управление» переключает активный таб на «Поставщики».

## Что остаётся за рамками

- Исключение конкретного счёта из расчётов — пока закрывается удалением счёта.
- Показ `reason` в таблице — поле сохраняется, но в строке поставщика пока не отображается (tooltip или отдельная колонка — backlog).
- История изменений / audit log исключений — не MVP.
