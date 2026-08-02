# Масштабирование: добавление новых типов материалов

Разбор того, что уже готово к расширению номенклатуры, что нужно менять, и какие БД-изменения стоит сделать.

---

## Текущая архитектура

`MaterialClass` — универсальная таблица вида `(material_type: str, name: str)`. Жёсткой привязки к бетону нет ни в схеме БД, ни в API, ни в CRUD-агрегациях.

Поддерживаемые типы сейчас:

| `material_type` | Примеры `name` | Статус |
|---|---|---|
| `concrete` | В15, В25, В40, В60 | Полная поддержка (промпт + UI) |
| `rebar` | d12, d16, d20, d32 | Полная поддержка (промпт + UI) |
| `other` | — | Корзина: класс не извлекается, агрегации нет |

---

## Что будет работать автоматически при новом типе

- **БД / ORM** — новая строка в `material_classes`, ни одной миграции.
- **API** — `GET /material-classes`, `POST /material-classes`, фильтрация по `?material_type=` работают для любой строки.
- **CRUD** — `recalculate_prices`, агрегации поставщиков, отклонения считаются по `material_class_id` без проверки типа.
- **Базовые цены** — `ReferencePrice` привязана к `material_class_id`, тип не важен.

---

## Что нужно менять при каждом новом типе

### 1. Промпт парсера — `backend/pdf_parser.py`

Самое важное. Без изменений модель не умеет извлекать класс для нового типа:

```text
# текущие правила (строки 61-62 в SYSTEM_PROMPT):
concrete → класс прочности: В15, В25, В40...
rebar    → диаметр: d12, d16, d20...
new_type → ??? → material_class вернётся null → item.material_class_id = NULL
```

Что нужно добавить для каждого нового типа:

```text
cement  → марка: М400, М500, ПЦ-500, ЦЕМ I 42.5...
gravel  → фракция: 5-20, 20-40, 40-70...
sand    → модуль крупности или происхождение: кварцевый, морской, речной...
brick   → марка и размер: М150 одинарный, М200 полуторный...
```

По аналогии нужно также:
- добавить новую ветку в раздел `material_type` промпта
- описать формат `material_class` (что именно извлекать)
- добавить примеры в поле `items[]` в JSON-примере промпта

### 2. Словарь меток и селект в трёх файлах фронтенда

`TYPE_LABELS` и список опций `<Select>` хардкожены в трёх местах:

| Файл | Строки | Что менять |
|---|---|---|
| `frontend/src/pages/Materials.tsx` | 41, 51, 56, 76-82 | `TYPE_LABELS` + `<Select>` опции |
| `frontend/src/pages/MaterialClasses.tsx` | 44-47, 57, 67, 96-98 | то же |
| `frontend/src/pages/MaterialPage.tsx` | 15 | только `TYPE_LABELS` |

До изменения новый тип отобразится как сырая строка (`gravel` вместо «Щебень»).

---

## Проблемы в БД, которые стоит устранить

### Проблема 1 — нет уникального ограничения на `(name, material_type)`

`get_or_create_material_class` делает SELECT, затем INSERT (классический TOCTOU).
При параллельном парсинге двух PDF с одинаковым материалом оба потока могут попасть в ветку «не найден» и создать дубликаты.

**Решение:** добавить `UniqueConstraint` на уровне БД.

```python
# backend/models.py
from sqlalchemy import UniqueConstraint

class MaterialClass(Base):
    __tablename__ = "material_classes"
    __table_args__ = (
        UniqueConstraint("name", "material_type", name="uq_material_class_name_type"),
    )
    # ... поля без изменений
```

После этого `get_or_create_material_class` можно защитить через `INSERT ... ON CONFLICT DO NOTHING` или `try/except IntegrityError` — дубликатов не будет в принципе.

```python
# backend/crud.py — защитный вариант
from sqlalchemy.exc import IntegrityError

def get_or_create_material_class(db: Session, name: str, material_type: str) -> MaterialClass:
    mc = db.query(MaterialClass).filter(
        MaterialClass.name == name, MaterialClass.material_type == material_type
    ).first()
    if mc:
        return mc
    try:
        mc = MaterialClass(name=name, material_type=material_type)
        db.add(mc)
        db.commit()
        db.refresh(mc)
        return mc
    except IntegrityError:
        db.rollback()
        return db.query(MaterialClass).filter(
            MaterialClass.name == name, MaterialClass.material_type == material_type
        ).one()
```

### Проблема 2 — нет индекса на `material_classes.material_type`

`GET /material-classes?material_type=concrete` делает `WHERE material_type = 'concrete'` без индекса.
Сейчас таблица маленькая — незаметно. При >500 классах (несколько типов, несколько проектов) начнёт замедляться.

```python
# backend/models.py
from sqlalchemy import Index

class MaterialClass(Base):
    __tablename__ = "material_classes"
    __table_args__ = (
        UniqueConstraint("name", "material_type", name="uq_material_class_name_type"),
        Index("ix_material_class_material_type", "material_type"),
    )
```

---

## Миграция (Alembic)

```python
# backend/alembic/versions/xxxx_material_class_constraints.py

def upgrade():
    # Сначала удаляем дубликаты, если они есть (безопасный запуск на продакшене)
    op.execute("""
        DELETE FROM material_classes
        WHERE id NOT IN (
            SELECT MIN(id) FROM material_classes
            GROUP BY name, material_type
        )
    """)

    op.create_unique_constraint(
        "uq_material_class_name_type",
        "material_classes",
        ["name", "material_type"],
    )
    op.create_index(
        "ix_material_class_material_type",
        "material_classes",
        ["material_type"],
    )


def downgrade():
    op.drop_index("ix_material_class_material_type", "material_classes")
    op.drop_constraint("uq_material_class_name_type", "material_classes", type_="unique")
```

---

## Чеклист: добавление нового типа материала

- [ ] Добавить правила в `SYSTEM_PROMPT` в `pdf_parser.py` (что такое `material_class` для этого типа, примеры)
- [ ] Добавить тип в `TYPE_LABELS` в `Materials.tsx`, `MaterialClasses.tsx`, `MaterialPage.tsx`
- [ ] Добавить `<SelectItem>` в формы создания класса в `Materials.tsx` и `MaterialClasses.tsx`
- [ ] Добавить тестовые примеры инвойсов с новым типом в `tests/fixtures/openrouter/`
- [ ] Написать unit-тест для промпта (ожидаемый `material_type` и `material_class` для нового типа)

**Изменения БД при этом не нужны** — модель данных уже универсальна.

---

## Потенциальное будущее: таблица конфигурации типов

Если типов станет много (5+) или их захочется менять без деплоя, можно ввести таблицу `material_type_configs`:

```sql
CREATE TABLE material_type_configs (
    slug        VARCHAR PRIMARY KEY,  -- "gravel"
    label_ru    VARCHAR NOT NULL,     -- "Щебень"
    class_hint  TEXT,                 -- подсказка для промпта
    is_active   BOOLEAN DEFAULT TRUE
);
```

Тогда `TYPE_LABELS` на фронте тянется с сервера (`GET /material-type-configs`), а промпт генерируется динамически из `class_hint`.
**Это преждевременно для текущего MVP.** Ввести, когда типов станет ≥5 или появится требование конфигурировать их из UI.
