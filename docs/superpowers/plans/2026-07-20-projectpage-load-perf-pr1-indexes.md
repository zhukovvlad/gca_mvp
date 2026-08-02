# ProjectPage Load Perf — PR-1: индексы и закрытие ORM/БД-дрейфа

> **Исполнение:** в superpowers-совместимом харнессе — через superpowers:subagent-driven-development (рекомендуется) или superpowers:executing-plans, задача за задачей. В иных харнессах (напр. Codex) — исполнять шаги по чекбоксам напрямую; sub-skill необязателен.

**Goal:** Закрыть весь дрейф ORM/БД по индексам (объявить в моделях уже существующие в БД индексы) и добавить два новых индекса на горячие колонки, чтобы `alembic check` был чистым и прод был застрахован на масштабе.

**Architecture:** Существующие индексы объявляются в SQLAlchemy-моделях **метаданными без миграции** (они уже в БД). Два новых индекса создаются реальной alembic-ревизией и параллельно объявляются в моделях. Приёмка — `alembic check` (через `just db-test-check`) с нулевым кодом (нет pending ops).

**Tech Stack:** Python 3.12, SQLAlchemy (sync), Alembic 1.14, PostgreSQL (Neon prod / локальный PG16 :5433 в тестах). Команды — только через `just`.

**Spec:** `docs/superpowers/specs/2026-07-20-projectpage-load-perf-design.md` §3.

## Global Constraints

- Команды проекта — **только через `just`**, никогда `cd backend && ...` вручную. Каждую проверку запускать **отдельной** командой (не через `&&`/`;`/`| tail` — иначе код возврата маскируется и упавший тест выглядит зелёным).
- Миграции: исторические файлы в `backend/alembic/versions/` НЕ редактировать. Новая ревизия — через `just db-revision "..."` (позиционный аргумент, НЕ `message=`), тело `upgrade`/`downgrade` заполнять вручную.
- Докстринг у каждой функции/метода (включая тесты).
- Имена индексов в моделях обязаны буквально совпадать с именами в БД/миграциях.
- `.env`/`.env.test` не трогать.
- Windows shell для запуска just: `& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just <cmd>"`.

---

## File Structure

- Modify: `justfile` — новый рецепт `db-test-check` (проверка дрейфа через `alembic check`).
- Modify: `backend/models.py` — объявить существующие индексы (drift) + два новых.
- Create: `backend/alembic/versions/<generated>-add_hot_path_indexes.py` — создание двух новых индексов.
- Modify: `docs/TECH_DEBT.md` — снять чекбокс пункта дрейфа + поправить устаревшую ссылку строки.

---

## Task 0: Baseline-замер (ДО любых изменений)

Снять **до** правок PR-1, иначе потом останутся только «после»-цифры и эффект индексов будет не с чем сравнить. Процедура и метрики — раздел «Замер» плана PR-2.

- [ ] **Step 1: Зафиксировать baseline на реалистичных данных**

На текущем `main` снять и записать (в описание PR / рабочую заметку): p50/p95 времени ответа `/summary` (≥20 холодных заходов — см. процедуру в PR-2 §Замер), `EXPLAIN (ANALYZE, BUFFERS)` ключевых запросов `compute_calculations`, форму набора (месяцы/счета/позиции). Это «точка 1» из трёх (baseline → после PR-1 → после PR-2).

---

## Task 1: Рецепт проверки дрейфа + объявление существующих индексов

`alembic check` сравнивает модели с БД и завершается ненулевым кодом при наличии pending ops — без создания временных ревизий. Сравнение идёт с БД из `DATABASE_URL` (`alembic.ini:66` пуст → `env.py:22` берёт `DATABASE_URL`), поэтому рецепт явно направляет его на **локальную тест-БД** (:5433, offline), предварительно доведя её до head.

Все четыре существующих индекса созданы старыми миграциями (`b3c7e9f12a45`, `c7d8e9f0a1b2`), но не объявлены в моделях → `check` предлагает их дропнуть, а `ix_suppliers_id` — создать. Объявляем метаданными (без миграции) и убираем избыточный `index=True` с PK `Supplier.id`.

**Files:**
- Modify: `justfile`
- Modify: `backend/models.py` — классы `InvoiceItem` (~379), `Invoice` (~358), `Supplier` (~288).

**Interfaces:**
- Produces: рецепт `just db-test-check`; модели с объявленными индексами `ix_invoice_items_invoice_id_item_type`, `ix_invoices_supplier_id`, `ix_suppliers_name_trgm`, `uq_suppliers_name_no_inn`; `Supplier.id` без `index=True`.

- [ ] **Step 1: Добавить рецепт `db-test-check` в justfile**

Рядом с `db-test-migrate` (~строка 146) добавить:

```makefile
# Проверка дрейфа ORM/БД: локальная тест-БД до head + alembic check.
# Нулевой код = моделей и схемы совпадают (нет pending upgrade ops).
db-test-check: pg-test-start
    cd backend && DATABASE_URL="{{test_db_local}}" alembic upgrade head
    cd backend && DATABASE_URL="{{test_db_local}}" alembic check
```

- [ ] **Step 2: Baseline — убедиться, что дрейф виден**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-test-check"
```
Expected: **ненулевой код**, в выводе `alembic check` — `New upgrade operations detected` с `drop_index('ix_invoice_items_invoice_id_item_type')`, `drop_index('ix_invoices_supplier_id')`, `drop_index('ix_suppliers_name_trgm')`, `drop_index('uq_suppliers_name_no_inn')`, `create_index('ix_suppliers_id', ...)`.

- [ ] **Step 3: Объявить индекс `InvoiceItem`**

В классе `InvoiceItem` (`backend/models.py`) добавить `__table_args__` (сейчас его нет) после последней колонки, до `relationship`:

```python
    __table_args__ = (
        # Уже в БД (миграция c7d8e9f0a1b2) — объявляем, чтобы autogenerate не предлагал drop.
        Index("ix_invoice_items_invoice_id_item_type", "invoice_id", "item_type"),
    )
```

- [ ] **Step 4: Объявить индекс `Invoice.supplier_id`**

В классе `Invoice` заменить строку `supplier_id`:

```python
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True, index=True)
```

(дефолтное имя SQLAlchemy `ix_invoices_supplier_id` совпадает с БД, миграция `b3c7e9f12a45`.)

- [ ] **Step 5: Объявить индексы `Supplier` и убрать `index=True` с PK**

В классе `Supplier`: убрать `index=True` у `id` и добавить `__table_args__`:

```python
class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    inn = Column(String, nullable=True, unique=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    invoices = relationship("Invoice", back_populates="supplier")

    __table_args__ = (
        # Оба уже в БД (миграция b3c7e9f12a45) — объявляем как метаданные.
        Index("ix_suppliers_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        Index("uq_suppliers_name_no_inn", "name", unique=True, postgresql_where=sa_text("inn IS NULL")),
    )
```

(`Index` и `sa_text` уже импортированы — `backend/models.py:12,23`.)

- [ ] **Step 6: Проверить, что дрейф закрыт**

Правки моделей — метаданные, схему БД не меняют (индексы уже в БД).

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-test-check"
```
Expected: **нулевой код**, `No new upgrade operations detected`.

Если `check` всё ещё видит дифф **только** по `ix_suppliers_name_trgm`/`uq_suppliers_name_no_inn` — сверить определения в модели дословно с миграцией `b3c7e9f12a45` (opclass `gin_trgm_ops`, `postgresql_where` = `inn IS NULL`) и добиться чистого `check`. Дрейф считается закрытым (и чекбокс TECH_DEBT снимается) **только при нулевом коде** — «benign diff» не оставляем.

- [ ] **Step 7: Прогнать backend-тесты (объявления не должны ничего сломать)**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test-int-local"
```
Expected: PASS (в т.ч. suppliers trgm-поиск — подтверждает, что объявление GIN/partial-unique корректно).

- [ ] **Step 8: Commit**

Рабочее дерево содержит несвязанные изменения `justfile` (MinIO) — стадировать **только** hunk с рецептом `db-test-check` (`git add -p justfile`), не весь файл.

```
git add -p justfile
git add backend/models.py
git commit -m "fix(db): рецепт db-test-check + объявить существующие индексы в моделях (закрыть ORM/БД-дрейф)"
```

---

## Task 2: Добавить два новых индекса на горячие колонки

Горячие фильтры `documents.project_id` и путь `invoices(document_id) + date range` не проиндексированы. Добавляем `ix_documents_project_id` и композит `ix_invoices_document_id_date` (модель + реальная миграция).

**Files:**
- Modify: `backend/models.py` — `Document` (~251), `Invoice` (~358).
- Create: `backend/alembic/versions/<generated>-add_hot_path_indexes.py`.
- Modify: `docs/TECH_DEBT.md`.

**Interfaces:**
- Consumes: рецепт `db-test-check` и модели из Task 1.
- Produces: индексы `ix_documents_project_id`, `ix_invoices_document_id_date` в БД и в моделях.

- [ ] **Step 1: Объявить индекс `Document.project_id`**

В классе `Document` (`backend/models.py`) заменить строку `project_id`:

```python
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
```

(дефолтное имя `ix_documents_project_id`.)

- [ ] **Step 2: Объявить композит на `Invoice`**

У `Invoice` **нет** `__table_args__` (в Task 1 `supplier_id` объявлен через `index=True` на колонке, не в `__table_args__`) — создаём его здесь:

```python
    __table_args__ = (
        Index("ix_invoices_document_id_date", "document_id", "date"),
    )
```

(`supplier_id`-индекс в `__table_args__` дублировать не нужно — он уже на колонке.)

- [ ] **Step 3: Создать пустую ревизию**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-revision \"add hot path indexes\""
```
Вывод печатает точный путь: `Generating .../backend/alembic/versions/<YYYY_MM_DD_HHMM>-<rev>_add_hot_path_indexes.py`. **Записать это точное имя** и использовать его далее (в Step 4 и в `git add`) — без glob. Формат имени задан `alembic.ini:12` (`..._%(rev)s_%(slug)s` → slug после underscore, поэтому glob для страховки — `*_add_hot_path_indexes.py`, НЕ `*-...`).

- [ ] **Step 4: Заполнить тело ревизии вручную**

В созданном файле:

```python
def upgrade() -> None:
    # Горячие колонки: project-фильтр и путь documents→invoices(document_id)+date range.
    # На текущих малых данных прироста может не быть (доминируют round-trip'ы) —
    # ставка на масштаб. Прод не развёрнут, таблицы малы → обычная транзакционная
    # миграция допустима; при росте таблиц заменить на CREATE INDEX CONCURRENTLY
    # (autocommit-блок Alembic).
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.create_index("ix_invoices_document_id_date", "invoices", ["document_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_invoices_document_id_date", table_name="invoices")
    op.drop_index("ix_documents_project_id", table_name="documents")
```

Проверить, что `import` для `op` (`from alembic import op`) присутствует (шаблон just его добавляет).

- [ ] **Step 5: Проверить, что дрейф по-прежнему закрыт (новые индексы в модели и БД)**

`db-test-check` сам накатит новую ревизию на локальную БД (`alembic upgrade head`), затем сверит.

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-test-check"
```
Expected: **нулевой код**, `No new upgrade operations detected` (новые индексы теперь и в модели, и в БД).

- [ ] **Step 6: Снять чекбокс в TECH_DEBT + поправить устаревшую ссылку**

В `docs/TECH_DEBT.md`: пункт «Дрейф ORM/БД: индексы созданы raw SQL, но не объявлены в моделях» — заменить `- [ ]` на `- [x]` (только если Step 6 Task 1 и Step 5 Task 2 дали чистый `check`). В теле пункта заменить устаревшую ссылку `models.py:282` на актуальный `Supplier.id` (:291 на момент правки; проверить актуальную строку).

- [ ] **Step 7: Финальный lint**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just lint"
```
Expected: PASS.

- [ ] **Step 8: Финальный полный тест (отдельной командой)**

Run:
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just test"
```
Expected: PASS (полный сьют, без маскировки кода возврата).

- [ ] **Step 9: Commit**

```
# <migration> — точный путь из вывода Step 3 (fallback-glob: backend/alembic/versions/*_add_hot_path_indexes.py)
git add backend/models.py <migration> docs/TECH_DEBT.md
git commit -m "perf(db): индексы documents.project_id и invoices(document_id,date) + закрытие дрейфа в TECH_DEBT"
```

- [ ] **Step 10: Накатить проверенную миграцию на dev-Neon**

Только **после** зелёных lint/test (Steps 7-8) — во внешнюю dev-БД уезжает уже проверенная миграция. Иначе dev останется без индексов и следующий честный `check` у разработчика снова зашумит. Требует подключения к Neon (если недоступно из окружения агента — выполнить перед мержем/деплоем):
```
& "C:\Program Files\Git\bin\bash.exe" -c "cd /c/Users/zhukov_v/Projects/UDP && just db-migrate"
```
Expected: `Running upgrade ... -> <rev>, add hot path indexes` без ошибок.

---

## Self-Review

- **Spec coverage §3:** §3.1 (drift close) → Task 1 Steps 3-5; §3.2 (2 new, no item_type/invoice_id/material_class_id) → Task 2 Steps 1-2; §3.3 (миграция вручную + CONCURRENTLY-заметка, накат dev-Neon) → Task 2 Step 4 и Step 10; §3.4 (чистый `check`, TECH_DEBT) → Task 1 Step 6 / Task 2 Step 5 (чистый `check`) + Step 6 (TECH_DEBT). ✓
- **Placeholders:** нет — имена индексов, колонки, рецепт и команды конкретны.
- **Type/name consistency:** имена индексов совпадают с миграциями `b3c7e9f12a45`/`c7d8e9f0a1b2` и с телом новой ревизии; `db-test-check` определён в Task 1 Step 1, используется в Task 1 Step 6 и Task 2 Step 5.
- **Exit-code честность:** проверки — отдельные команды без `| tail`/`&&`/`;`; финал — `just lint` и `just test` раздельно.

## Замер (точки 1-2 из трёх)

Baseline («точка 1») снимается в **Task 0** до изменений. **После PR-1** («точка 2») повторить те же метрики (p50/p95 `/summary`, `EXPLAIN (ANALYZE, BUFFERS)`) — так эффект индексов отделяется от эффекта PR-2 («точка 3», см. PR-2 §Замер). Процедура — раздел «Замер» плана PR-2.
