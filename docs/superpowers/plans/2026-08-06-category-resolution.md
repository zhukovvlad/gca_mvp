# Ф3: импорт — резолв статьи СМР стеком по файлу, миграция 0006 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать деньгам сметы ось «статья классификатора»: определить для каждой строки её физический раздел и статью одним проходом со стеком и материализовать это на `position_items`, не полагаясь ни на один join по номеру раздела.

**Architecture:** Новый чистый сервис `services/category_resolution.py` строит по разобранному JSON одного предложения полный план привязки (`ProposalResolution`) — до единой записи в БД, что и делает деградацию D атомарной. `_import_positions` применяет план: поля статьи проставляются при создании строк, затем один `add_all + flush` даёт id, и второй проход переводит `parent_position_key` в `chapter_item_id`. Миграция 0006 добавляет четыре колонки, три CHECK и **составной** self-FK `(proposal_id, chapter_item_id) → (proposal_id, id)`, который делает межсметную привязку непредставимой.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x sync ORM, Alembic, PostgreSQL 16, pytest, uv, just. Фронтенд не затрагивается.

**Спека:** [2026-08-06-category-resolution-design.md](../specs/2026-08-06-category-resolution-design.md) — источник правды. Замеры в §1, решения в §2, отвергнутые альтернативы в §3, тесты в §4.

## Global Constraints

- Ветка: `feat/category-resolution` (спека уже закоммичена в неё тремя коммитами).
- ruff `line-length = 120`, target py312. Комментарии и docstring — по-русски, как в окружающем коде.
- Все команды бэкенда — из `backend/` через `uv run`; системный python не вызывать.
- Точечный прогон integration: `TEST_DATABASE_URL` обязателен, `DATABASE_URL` снимать (`env -u DATABASE_URL`). Успех читать по числу `passed`, а не по коду возврата ([silent-test-runs.md](../../insights/silent-test-runs.md)).
- Новому integration-файлу — `pytestmark = pytest.mark.integration`.
- **Нормализация ровно одна:** `_norm(v) = "" if v is None else " ".join(str(v).split())`; сравнимая форма — `_norm(v).casefold()`. Другой нормализации в фиче не появляется.
- **`smr_article_raw` хранится обрезанным по краям** (`str(v).strip() or None`, как `_text` в импорте), а разбор кода и названия идёт по `_norm`. Это два разных преобразования, и путать их нельзя (спека §2.5).
- **`is_chapter` — единственный источник истины о том, раздел ли строка.** Резолвер обязан гейтить поля статьи по тому же признаку, который импорт пишет в БД, иначе `ck_position_items_article_only_on_chapters` уронит импорт (спека §2.3).
- **Ни одного join по номеру раздела** (`chapter_number_in_proposal`) в новом коде. `chapter_ref_in_proposal` не читается и не меняется.
- **Новые имена индексов НЕ добавлять в `RAW_SQL_INDEXES`** (`backend/alembic/env.py:56`): оба частичных индекса, `UNIQUE` и оба FK — декларативные и обязаны быть видны `alembic check`.
- **Деньги в фиче не двигаются вовсе.** Ни одно денежное поле не читается и не пишется; счётчики позиций и суммы по fixture обязаны остаться прежними. Поехавший baseline — признак дефекта, а не повод обновить число.
- Перед пушем — `just ci` целиком (в него входит `db-test-check`), и **до** пуша, не после.
- Реальные суммы и реквизиты контрагентов — никуда: ни в код, ни в тесты, ни в доки, ни в сообщения коммитов. Коды статей классификатора и счётчики строк — можно.

## Состояние на момент написания плана (замерено, не пересказано)

- `head` миграций — `0005` (`backend/alembic/versions/2026_08_05_0005-work_categories.py`), `down_revision = "0004"`. Новая — `0006`, `down_revision = "0005"`.
- `models.py` **не импортирует** `ForeignKeyConstraint` — его надо добавить в общий импорт из `sqlalchemy` (строки 4–20). `CheckConstraint`, `UniqueConstraint`, `Index`, `BigInteger`, `Text`, `sa_text` уже импортированы.
- `PositionItem.__table_args__` сейчас (`models.py:678-685`): `UniqueConstraint("proposal_id", "position_key_in_proposal")`, `Index("idx_position_items_proposal_id", "proposal_id")`, `Index("idx_position_items_catalog_id", ...)`, `Index("idx_position_items_unit_id", ...)`. **`idx_position_items_proposal_id` в Task 1 удаляется** — его заменяет `UNIQUE (proposal_id, id)`.
- `_import_positions` (`services/estimate_import.py:619-749`) копит строки в `rows: list[PositionItem]`, делает **один** `db.add_all(rows)` + **один** `db.flush()` (строки 734-735), затем собирает `to_match` по `item.id`.
- `import_estimate` принимает `unit_resolver: UnitResolver` последним keyword-параметром; вызывается из `services/import_pipeline.py:244`, где резолвер создаётся строкой `resolver = UnitResolver(db)` (строка 243), внутри `with session_factory() as db, db.begin():`.
- В тестах `import_estimate(` вызывается **по одному разу в трёх файлах**: `tests/integration/test_estimate_import.py`, `test_matching.py`, `test_review_concurrency.py` (каждый — внутри своего хелпера). `unit_resolver=` в тестах встречается 3 раза — ровно эти места.
- `tests/payloads.py` — конструктор синтетического `ParseResult.data`. `position(...)` **уже принимает** `article_smr`, `chapter_number`, `is_chapter`, `number`. `proposal(...)` нумерует позиции `str(i)` от 1 — то есть **все существующие payload'ы уже канонические «1..N»**, и новое правило про ключи ни одного из них не отвергает (проверено grep'ом: `JSON_KEY_CONTRACTOR_POSITIONS` в тестах строится только в `payloads.py`).
- Ни один тест не подаёт в `positions` не-словарь; защитный `if not isinstance(raw_position, dict): continue` в `_import_positions` не покрыт ничем.
- `tests/integration/test_import_fixture_e2e.py` уже несёт `FIXTURE_POSITIONS = 2576` и `FIXTURE_CHAPTERS = 746` — **совпадает с замером спеки §1.1**, то есть числа новых констант проверяются ещё и арифметикой: `222 + 485 + 39 = 746`.
- Хелпер `rejected(session, contains=...)` (`tests/integration/test_schema_constraints.py:38`) ждёт `IntegrityError` внутри savepoint. Для `ProgrammingError` он не годится — но в Ф3 все отказы схемы это `IntegrityError` (CHECK и FK).
- База отсчёта тестов: **965 passed / 6 skipped** backend на `c980274`, 175 vitest.

## File Structure

- `backend/alembic/versions/2026_08_06_0006-category_resolution.py` — **создаётся** (Task 1): колонки, CHECK-и, `UNIQUE (proposal_id, id)`, составной self-FK, два частичных индекса, замена `idx_position_items_proposal_id`, явный `downgrade`.
- `backend/models.py` — **правится** (Task 1): четыре колонки и все новые объекты на `PositionItem`; удаление `Index("idx_position_items_proposal_id", …)`; импорт `ForeignKeyConstraint`.
- `backend/services/category_resolution.py` — **создаётся** (Tasks 2–3): типы, `CategoryResolver`, чистый алгоритм. Ни ORM-объектов, ни записи в БД.
- `backend/services/estimate_import.py` — **правится** (Task 4): новый параметр, применение плана, второй проход self-FK, warnings резолва.
- `backend/services/import_pipeline.py` — **правится** (Task 4): создание резолвера рядом с `UnitResolver(db)`.
- `backend/tests/unit/test_category_resolution.py` — **создаётся** (Tasks 2–3): алгоритм без БД и без xlsx.
- `backend/tests/integration/test_schema_constraints.py` — **правится** (Task 1): CHECK-и, составной FK, RESTRICT.
- `backend/tests/integration/test_estimate_import.py` — **правится** (Task 4): материализация на синтетике, два лота, строка вне структуры, D.
- `backend/tests/integration/test_import_fixture_e2e.py` — **правится** (Task 5): замеренные числа из БД, независимая проверка вложенности.
- `backend/tests/integration/test_matching.py`, `test_review_concurrency.py` — **правятся** (Task 4): по одному вызову `import_estimate(`.
- `docs/devlog/2026-08-06-category-resolution.md` — **создаётся** (Task 6).

---

### Task 1: миграция 0006, ORM и тесты схемы

**Files:**
- Create: `backend/alembic/versions/2026_08_06_0006-category_resolution.py`
- Modify: `backend/models.py:4-20` (импорт), `backend/models.py:669-685` (`PositionItem`)
- Test: `backend/tests/integration/test_schema_constraints.py`

**Interfaces:**
- Consumes: `work_categories` из 0005 (`WorkCategory.id`, `.code`, `.title`).
- Produces: колонки `position_items.smr_article_raw | work_category_id | category_source | chapter_item_id`; констрейнты `ck_position_items_article_only_on_chapters`, `ck_position_items_category_source_pairs`, `ck_position_items_category_source`, `uq_position_items_proposal_id_id`, `fk_position_items_chapter`, `fk_position_items_work_category_id`; индексы `idx_position_items_work_category_id`, `idx_position_items_chapter_item_id`. Значение `category_source` в v1 — только `'file'`.

- [ ] **Step 1: Написать падающие тесты схемы**

Дописать в `backend/tests/integration/test_schema_constraints.py` новый класс. Конвенции файла (замерено): сессия — фикстура `db_session`, фабрики приходят фикстурой `factories` и зовутся `factories.XxxFactory.create(...)`; `ProposalFactory.create()` сам поднимает цепочку lot → estimate → contract, `PositionItemFactory` сам нумерует `position_key_in_proposal`. `PositionItem` и `WorkCategory` в файле уже импортированы.

```python
class TestPositionItemCategoryColumns:
    """Миграция 0006: поля статьи и составной self-FK (спека Ф3 §2.1)."""

    @staticmethod
    def _row(db_session, factories, proposal, *, is_chapter: bool):
        item = factories.PositionItemFactory.create(proposal=proposal, is_chapter=is_chapter)
        db_session.flush()
        return item

    @staticmethod
    def _any_category_id(db_session) -> int:
        return db_session.execute(
            sa.select(WorkCategory.id).order_by(WorkCategory.sort_order).limit(1)
        ).scalar_one()

    def test_article_fields_are_rejected_on_a_non_chapter_row(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        item = self._row(db_session, factories, proposal, is_chapter=False)
        with rejected(db_session, contains="ck_position_items_article_only_on_chapters"):
            db_session.execute(
                sa.update(PositionItem)
                .where(PositionItem.id == item.id)
                .values(smr_article_raw="4.1. Ж/Б конструкции")
            )

    def test_category_without_source_is_rejected(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        item = self._row(db_session, factories, proposal, is_chapter=True)
        with rejected(db_session, contains="ck_position_items_category_source_pairs"):
            db_session.execute(
                sa.update(PositionItem)
                .where(PositionItem.id == item.id)
                .values(work_category_id=self._any_category_id(db_session))
            )

    def test_source_other_than_file_is_rejected(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        item = self._row(db_session, factories, proposal, is_chapter=True)
        with rejected(db_session, contains="ck_position_items_category_source"):
            db_session.execute(
                sa.update(PositionItem)
                .where(PositionItem.id == item.id)
                .values(
                    work_category_id=self._any_category_id(db_session),
                    category_source="manual",
                )
            )

    def test_cross_proposal_reference_is_rejected_by_the_composite_fk(self, db_session, factories):
        """Прямая попытка записи, а не результат импорта.

        Импортёр такую ссылку не построит и при СНЯТОМ констрейнте (карта
        key -> PositionItem живёт один вызов на один proposal), поэтому проверка
        через импорт стерегла бы построение, а не FK (спека §4.2).
        """
        chapter = self._row(
            db_session, factories, factories.ProposalFactory.create(), is_chapter=True
        )
        alien = self._row(
            db_session, factories, factories.ProposalFactory.create(), is_chapter=False
        )
        with rejected(db_session, contains="fk_position_items_chapter"):
            db_session.execute(
                sa.update(PositionItem)
                .where(PositionItem.id == alien.id)
                .values(chapter_item_id=chapter.id)
            )

    def test_reference_inside_the_same_proposal_is_accepted(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        chapter = self._row(db_session, factories, proposal, is_chapter=True)
        child = self._row(db_session, factories, proposal, is_chapter=False)
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == child.id)
            .values(chapter_item_id=chapter.id)
        )
        db_session.flush()
        assert db_session.get(PositionItem, child.id).chapter_item_id == chapter.id

    def test_null_parent_is_allowed(self, db_session, factories):
        """MATCH SIMPLE: NULL во второй колонке пропускает проверку FK (спека §1.3 факт 8)."""
        item = self._row(
            db_session, factories, factories.ProposalFactory.create(), is_chapter=True
        )
        assert db_session.get(PositionItem, item.id).chapter_item_id is None

    def test_deleting_a_referenced_chapter_row_hits_restrict(self, db_session, factories):
        proposal = factories.ProposalFactory.create()
        chapter = self._row(db_session, factories, proposal, is_chapter=True)
        child = self._row(db_session, factories, proposal, is_chapter=False)
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == child.id)
            .values(chapter_item_id=chapter.id)
        )
        db_session.flush()
        with rejected(db_session, contains="fk_position_items_chapter"):
            db_session.execute(sa.delete(PositionItem).where(PositionItem.id == chapter.id))

    def test_deleting_a_used_work_category_hits_restrict(self, db_session, factories):
        item = self._row(
            db_session, factories, factories.ProposalFactory.create(), is_chapter=True
        )
        category_id = self._any_category_id(db_session)
        db_session.execute(
            sa.update(PositionItem)
            .where(PositionItem.id == item.id)
            .values(work_category_id=category_id, category_source="file")
        )
        db_session.flush()
        with rejected(db_session, contains="fk_position_items_work_category_id"):
            db_session.execute(sa.delete(WorkCategory).where(WorkCategory.id == category_id))
```

- [ ] **Step 2: Прогнать — убедиться, что падает по отсутствию колонок**

```
cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  uv run pytest tests/integration/test_schema_constraints.py -k CategoryColumns -v
```

Ожидание: **8 failed** (не errors в collection) с сообщениями про неизвестный атрибут `smr_article_raw` / отсутствующую колонку. Красный на этом шаге обязателен: если тесты зелёные до миграции — пробник неисправен ([verifying-guards.md](../../insights/verifying-guards.md), слой 3).

- [ ] **Step 3: Написать миграцию 0006**

Создать `backend/alembic/versions/2026_08_06_0006-category_resolution.py`:

```python
"""Привязка строк сметы к статьям классификатора: четыре колонки на position_items.

Паспорт проекта агрегирует деньги по статьям, а привязки в БД нет. Статья стоит в
файле только на строках-разделах и не на всех; принадлежность строки разделу задаёт
ТОЛЬКО порядок строк файла — нумерация колонки B дублируется, и у дублей бывают
разные статьи, поэтому join по номеру раздела в денежной логике запрещён (спека Ф3
§1.1). Отсюда `chapter_item_id` — ссылка на конкретную физическую строку.

**Self-FK составной.** `(proposal_id, chapter_item_id) -> (proposal_id, id)` делает
привязку к строке ЧУЖОЙ сметы непредставимой, а не «маловероятной». Цель FK требует
`UNIQUE (proposal_id, id)`; этот индекс ЗАМЕНЯЕТ `idx_position_items_proposal_id`,
потому что `proposal_id` — его левый префикс (замер на 60 000 строк: поиск по
proposal_id идёт Bitmap Index Scan по составному индексу при включённом
enable_seqscan; на таблице в 15 строк планировщик берёт Seq Scan и этот факт не
показывает вовсе).

**`ON DELETE RESTRICT` на обоих FK.** Замерено на PG 16.14 для СОСТАВНОГО FK
отдельно от одноколоночного эксперимента фазы 7: каскад `proposals -> position_items`
сносит все строки предложения одним оператором и RESTRICT ему не мешает (то есть
replace-флоу работает), а точечное удаление строки-раздела со ссылками даёт громкую
ошибку FK вместо тихого уноса подразделов.

Инвариант «цель ссылки — строка-РАЗДЕЛ» в PostgreSQL декларативно не выражается
(подзапросы в CHECK запрещены); он держится резолвером и закреплён тестами.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-06
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Выражения дублируют models.PositionItem намеренно: миграция обязана быть
# неизменной во времени (то же правило, что у литералов в 0004 и 0005).
CK_ARTICLE_ONLY_ON_CHAPTERS = (
    "is_chapter OR (smr_article_raw IS NULL AND work_category_id IS NULL "
    "AND category_source IS NULL)"
)
CK_SOURCE_PAIRS = "(work_category_id IS NULL) = (category_source IS NULL)"
CK_SOURCE_VALUES = "category_source IS NULL OR category_source = 'file'"


def upgrade() -> None:
    op.add_column("position_items", sa.Column("smr_article_raw", sa.Text(), nullable=True))
    op.add_column("position_items", sa.Column("work_category_id", sa.BigInteger(), nullable=True))
    op.add_column("position_items", sa.Column("category_source", sa.Text(), nullable=True))
    op.add_column("position_items", sa.Column("chapter_item_id", sa.BigInteger(), nullable=True))

    op.create_foreign_key(
        "fk_position_items_work_category_id",
        "position_items",
        "work_categories",
        ["work_category_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Порядок важен: сначала создаётся составной UNIQUE, только потом снимается
    # прежний индекс — таблица ни в один момент не остаётся без индекса по proposal_id.
    op.create_unique_constraint(
        "uq_position_items_proposal_id_id", "position_items", ["proposal_id", "id"]
    )
    op.drop_index("idx_position_items_proposal_id", table_name="position_items")
    op.create_foreign_key(
        "fk_position_items_chapter",
        "position_items",
        "position_items",
        ["proposal_id", "chapter_item_id"],
        ["proposal_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_position_items_article_only_on_chapters", "position_items", CK_ARTICLE_ONLY_ON_CHAPTERS
    )
    op.create_check_constraint(
        "ck_position_items_category_source_pairs", "position_items", CK_SOURCE_PAIRS
    )
    op.create_check_constraint(
        "ck_position_items_category_source", "position_items", CK_SOURCE_VALUES
    )

    op.create_index(
        "idx_position_items_work_category_id",
        "position_items",
        ["work_category_id"],
        postgresql_where=sa.text("work_category_id IS NOT NULL"),
    )
    op.create_index(
        "idx_position_items_chapter_item_id",
        "position_items",
        ["chapter_item_id"],
        postgresql_where=sa.text("chapter_item_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_position_items_chapter_item_id", table_name="position_items")
    op.drop_index("idx_position_items_work_category_id", table_name="position_items")
    op.drop_constraint("ck_position_items_category_source", "position_items", type_="check")
    op.drop_constraint("ck_position_items_category_source_pairs", "position_items", type_="check")
    op.drop_constraint(
        "ck_position_items_article_only_on_chapters", "position_items", type_="check"
    )
    # Составной FK снимается ДО UNIQUE: он на него ссылается.
    op.drop_constraint("fk_position_items_chapter", "position_items", type_="foreignkey")
    op.create_index("idx_position_items_proposal_id", "position_items", ["proposal_id"])
    op.drop_constraint("uq_position_items_proposal_id_id", "position_items", type_="unique")
    op.drop_constraint("fk_position_items_work_category_id", "position_items", type_="foreignkey")
    op.drop_column("position_items", "chapter_item_id")
    op.drop_column("position_items", "category_source")
    op.drop_column("position_items", "work_category_id")
    op.drop_column("position_items", "smr_article_raw")
```

- [ ] **Step 4: Зеркалить в ORM**

В `backend/models.py` добавить `ForeignKeyConstraint` в импорт из `sqlalchemy` (алфавитный порядок — после `ForeignKey`). Затем в `PositionItem`, сразу после `chapter_ref_in_proposal` (строка 670):

```python
    # Фаза 7, миграция 0006: привязка к статье классификатора.
    # Поля статьи живут ТОЛЬКО на строках-разделах (ck_..._article_only_on_chapters);
    # у позиции статья выводится через chapter_item_id -> её строка-раздел.
    smr_article_raw = Column(Text, nullable=True)
    work_category_id = Column(
        BigInteger,
        ForeignKey("work_categories.id", ondelete="RESTRICT",
                   name="fk_position_items_work_category_id"),
        nullable=True,
    )
    category_source = Column(Text, nullable=True)
    # Одиночного FK на position_items.id НЕТ: цель задаёт составной FK в
    # __table_args__ — он проверяет и существование строки, и совпадение proposal.
    chapter_item_id = Column(BigInteger, nullable=True)
```

и заменить `__table_args__` целиком:

```python
    __table_args__ = (
        UniqueConstraint(
            "proposal_id", "position_key_in_proposal", name="uq_position_items_proposal_id_key"
        ),
        # Цель составного self-FK. ЗАМЕНЯЕТ idx_position_items_proposal_id:
        # proposal_id — левый префикс, поиск по нему по-прежнему идёт индексом.
        UniqueConstraint("proposal_id", "id", name="uq_position_items_proposal_id_id"),
        ForeignKeyConstraint(
            ["proposal_id", "chapter_item_id"],
            ["position_items.proposal_id", "position_items.id"],
            ondelete="RESTRICT",
            name="fk_position_items_chapter",
        ),
        CheckConstraint(
            "is_chapter OR (smr_article_raw IS NULL AND work_category_id IS NULL "
            "AND category_source IS NULL)",
            name="ck_position_items_article_only_on_chapters",
        ),
        CheckConstraint(
            "(work_category_id IS NULL) = (category_source IS NULL)",
            name="ck_position_items_category_source_pairs",
        ),
        CheckConstraint(
            "category_source IS NULL OR category_source = 'file'",
            name="ck_position_items_category_source",
        ),
        Index("idx_position_items_catalog_id", "catalog_position_id"),
        Index("idx_position_items_unit_id", "unit_id"),
        Index(
            "idx_position_items_work_category_id",
            "work_category_id",
            postgresql_where=sa_text("work_category_id IS NOT NULL"),
        ),
        Index(
            "idx_position_items_chapter_item_id",
            "chapter_item_id",
            postgresql_where=sa_text("chapter_item_id IS NOT NULL"),
        ),
    )
```

- [ ] **Step 5: Накатить и прогнать тесты схемы**

```
just db-test-upgrade        # если такого рецепта нет — alembic upgrade head по TEST_DATABASE_URL
cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  uv run pytest tests/integration/test_schema_constraints.py -k CategoryColumns -v
```

Ожидание: **8 passed**, `0 skipped`. `skipped` здесь — сигнал, что `TEST_DATABASE_URL` не подхватился.

- [ ] **Step 6: Проверить дрейф и круговой рейс**

```
just db-test-check                      # alembic check: «No new upgrade operations detected»
cd backend && uv run alembic downgrade base && uv run alembic upgrade head
just db-test-check
```

Ожидание: дрейфа нет **до и после** кругового рейса. Если `alembic check` требует новую ревизию — ORM и миграция расходятся; чинить ORM, а не добавлять ревизию.

- [ ] **Step 7: Прогнать весь backend**

```
cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest -q
```

Ожидание: весь набор зелёный при `6 skipped`; прирост — восемь новых тестов Task 1 (число сверяется в Task 5 Step 3 замером, а не арифметикой здесь). Любое падение существующего теста — дефект Task 1, а не повод править ожидание: особое внимание тестам, которые опираются на `idx_position_items_proposal_id` по имени (их быть не должно — проверено grep'ом, но прогон авторитетнее).

- [ ] **Step 8: Коммит**

```bash
git add backend/alembic/versions/2026_08_06_0006-category_resolution.py backend/models.py \
        backend/tests/integration/test_schema_constraints.py
git commit -m "feat(db): миграция 0006 — поля статьи и составной self-FK на position_items"
```

---

### Task 2: резолвер — стек, наследование, код и название

**Files:**
- Create: `backend/services/category_resolution.py`
- Test: `backend/tests/unit/test_category_resolution.py`

**Interfaces:**
- Consumes: `models.WorkCategory` (только в `from_db`), ключи парсера `JSON_KEY_NUMBER`, `JSON_KEY_CHAPTER_NUMBER`, `JSON_KEY_ARTICLE_SMR`, `JSON_KEY_IS_CHAPTER`, `JSON_KEY_JOB_TITLE` из `parser.constants`.
- Produces:
  - `CATEGORY_SOURCE_FILE = "file"`;
  - `RowKind` (`CHAPTER`, `POSITION`, `OUTSIDE_STRUCTURE`);
  - `CategoryRef(id: int, title: str)`;
  - `RowResolution(position_key, kind, parent_position_key, smr_article_raw, work_category_id, category_source)`;
  - `ResolutionCounters(chapters_own, chapters_inherited, chapters_unassigned, positions_unassigned, rows_outside_structure)`;
  - `ProposalResolution(rows: dict[str, RowResolution], warnings: list[str], structure_disabled: bool, counters: ResolutionCounters)`;
  - `CategoryResolver(by_code: Mapping[str, CategoryRef])`, `CategoryResolver.from_db(db) -> CategoryResolver`, `resolver.resolve_proposal(positions) -> ProposalResolution`.

- [ ] **Step 1: Написать падающие тесты стека**

Создать `backend/tests/unit/test_category_resolution.py`. Юнит-файл: маркер `integration` не ставить, БД не трогать.

```python
"""Алгоритм резолва статьи — без БД и без xlsx (спека Ф3 §4.1)."""
from __future__ import annotations

import pytest

from services.category_resolution import (
    CATEGORY_SOURCE_FILE,
    CategoryRef,
    CategoryResolver,
    RowKind,
)
from tests.payloads import position

#: Карта справочника: ровно те коды, что нужны тестам. Названия — как в шаблоне.
CATALOG = {
    "1": CategoryRef(id=101, title="Подготовительные работы"),
    "4": CategoryRef(id=104, title="Возведение конструкций"),
    "4.1": CategoryRef(id=141, title="Ж/Б конструкции"),
    "4.1.2": CategoryRef(id=1412, title="Корпус 1"),
    "4.1.3": CategoryRef(id=1413, title="Корпус 2"),
    "5.1": CategoryRef(id=151, title="Кровля"),
    "5.2": CategoryRef(id=152, title="Фасад"),
    "7.2": CategoryRef(id=172, title="Внутренняя отделка"),
    "7.3": CategoryRef(id=173, title="Инженерные системы"),
    "14.99": CategoryRef(id=1499, title="Прочее"),
}


@pytest.fixture
def resolver() -> CategoryResolver:
    return CategoryResolver(CATALOG)


def rows(*positions):
    """Позиции в форме парсера с каноническими ключами «1..N»."""
    return {str(i): pos for i, pos in enumerate(positions, start=1)}


def chapter(number, *, article=None, title="Раздел", key_number="1"):
    return position(
        job_title=title, number=key_number, chapter_number=number,
        article_smr=article, is_chapter=True,
    )


def work(title="Работа", *, number="1", article=None):
    return position(job_title=title, number=number, article_smr=article, is_chapter=False)


class TestStack:
    def test_position_takes_the_article_of_its_chapter(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("1", article="1. Подготовительные работы"), work()))
        assert result.structure_disabled is False
        assert result.rows["2"].parent_position_key == "1"
        assert result.rows["1"].work_category_id == 101
        assert result.rows["1"].category_source == CATEGORY_SOURCE_FILE
        # У позиции полей статьи нет — она их получает через родителя.
        assert result.rows["2"].work_category_id is None
        assert result.rows["2"].category_source is None
        assert result.rows["2"].smr_article_raw is None

    def test_chapter_without_its_own_code_inherits_from_the_stack(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("4", article="4. Возведение конструкций"), chapter("4.3", title="Металлоконструкции"), work())
        )
        assert result.rows["2"].work_category_id == 104
        assert result.rows["2"].smr_article_raw is None
        assert result.rows["2"].category_source == CATEGORY_SOURCE_FILE
        assert result.rows["3"].parent_position_key == "2"
        assert result.counters.chapters_own == 1
        assert result.counters.chapters_inherited == 1

    def test_chapter_of_depth_n_evicts_everything_at_depth_n_or_deeper(self, resolver):
        result = resolver.resolve_proposal(
            rows(
                chapter("4", article="4. Возведение конструкций"),
                chapter("4.1", article="4.1. Ж/Б конструкции"),
                chapter("4.1.2", article="4.1.2. Корпус 1"),
                chapter("5.1", article="5.1. Кровля"),
                work(),
            )
        )
        # «5.1» глубины 2 вытесняет 4.1.2 (3) и 4.1 (2), но не 4 (1).
        assert result.rows["4"].parent_position_key == "1"
        assert result.rows["5"].parent_position_key == "4"

    def test_top_level_chapter_has_no_parent(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("1", article="1. Подготовительные работы")))
        assert result.rows["1"].parent_position_key is None

    def test_lot_row_is_a_chapter_without_an_article_and_is_evicted(self, resolver):
        """Дубль «1» во всех четырёх файлах — строка лота (спека §1.1)."""
        result = resolver.resolve_proposal(
            rows(
                chapter("1", title="Лот №1 - Тестовый"),
                chapter("1", article="1. Подготовительные работы"),
                work(),
            )
        )
        assert result.rows["1"].work_category_id is None
        assert result.rows["1"].parent_position_key is None
        assert result.rows["2"].parent_position_key is None      # лот вытеснен
        assert result.rows["2"].work_category_id == 101
        assert result.rows["3"].parent_position_key == "2"
        assert result.counters.chapters_unassigned == 1

    def test_duplicate_numbers_with_different_articles_do_not_collide(self, resolver):
        """Три реальных случая 159-ТУ (спека §1.1): номер один, статьи разные."""
        result = resolver.resolve_proposal(
            rows(
                chapter("4.1", article="4.1. Ж/Б конструкции"),
                chapter("4.1.2.2", article="4.1.2. Корпус 1"),
                chapter("4.1.2.2", article="4.1.3. Корпус 2"),
                chapter("5.1", article="5.1. Кровля"),
                chapter("5.1.2", article="5.1. Кровля"),
                chapter("5.1.2", article="5.2. Фасад"),
            )
        )
        assert result.rows["2"].work_category_id == 1412
        assert result.rows["3"].work_category_id == 1413
        assert result.rows["5"].work_category_id == 151
        assert result.rows["6"].work_category_id == 152

    def test_same_numbers_under_different_parents_inherit_differently(self, resolver):
        """«7.3.1»–«7.3.3» без своих кодов стоят под разными родителями."""
        result = resolver.resolve_proposal(
            rows(
                chapter("7.3", article="7.2. Внутренняя отделка"),
                chapter("7.3.1", title="Корпус 1"),
                chapter("7.3", article="7.3. Инженерные системы"),
                chapter("7.3.1", title="Корпус 1"),
            )
        )
        assert result.rows["2"].work_category_id == 172
        assert result.rows["4"].work_category_id == 173

    def test_row_outside_structure_is_not_attached(self, resolver):
        """Агрегатная строка допработ: A и B пусты (спека §2.6)."""
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                work(),
                position(job_title="Дополнительные работы", number=None,
                         chapter_number=None, is_chapter=False),
            )
        )
        assert result.rows["3"].kind is RowKind.OUTSIDE_STRUCTURE
        assert result.rows["3"].parent_position_key is None
        assert result.rows["3"].work_category_id is None
        assert result.counters.rows_outside_structure == 1
        assert any("вне структуры" in w for w in result.warnings)
        # Строку вне структуры стек не трогает: следующая позиция всё ещё под «1».
        assert result.rows["2"].parent_position_key == "1"


class TestCodeAndTitle:
    def test_trailing_dot_in_the_code_is_stripped(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("14", article="14.99. Прочее")))
        assert result.rows["1"].work_category_id == 1499
        assert result.rows["1"].smr_article_raw == "14.99. Прочее"

    def test_code_without_a_trailing_dot_resolves_too(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("14", article="14.99 Прочее")))
        assert result.rows["1"].work_category_id == 1499

    def test_code_only_value_resolves_and_does_not_warn_about_the_title(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("14", article="14.99.")))
        assert result.rows["1"].work_category_id == 1499
        assert result.warnings == []

    def test_title_mismatch_warns_but_keeps_the_link(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("7", article="7.2. Внутреняя отделка")))
        assert result.rows["1"].work_category_id == 172
        assert len(result.warnings) == 1
        # Оба текста в предупреждении: и файла, и справочника.
        assert "Внутреняя отделка" in result.warnings[0]
        assert "Внутренняя отделка" in result.warnings[0]

    def test_title_comparison_ignores_case_and_whitespace(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("7", article="7.2.   внутренняя\nотделка")))
        assert result.rows["1"].work_category_id == 172
        assert result.warnings == []

    def test_raw_is_stored_trimmed_but_not_collapsed(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("7", article="  7.2.  Внутренняя  отделка  ")))
        assert result.rows["1"].smr_article_raw == "7.2.  Внутренняя  отделка"


class TestFileAssertionBeatsInheritance:
    def test_unknown_code_gives_null_and_does_not_inherit(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("4", article="4. Возведение конструкций"), chapter("4.9", article="99.5. Неизвестная"))
        )
        assert result.rows["2"].work_category_id is None
        assert result.rows["2"].category_source is None
        assert result.rows["2"].smr_article_raw == "99.5. Неизвестная"
        assert any("99.5" in w for w in result.warnings)

    def test_subtree_of_an_unknown_code_stays_unassigned(self, resolver):
        """Ключевое следствие правила: дети НЕ получают статью предка."""
        result = resolver.resolve_proposal(
            rows(
                chapter("4", article="4. Возведение конструкций"),
                chapter("4.9", article="99.5. Неизвестная"),
                chapter("4.9.1", title="Корпус 1"),
                work(),
            )
        )
        assert result.rows["3"].work_category_id is None
        assert result.rows["4"].parent_position_key == "3"
        assert result.counters.positions_unassigned == 1

    def test_unreadable_prefix_gives_null_and_warns(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("4", article="4. Возведение конструкций"), chapter("4.9", article="Прочее по смете"))
        )
        assert result.rows["2"].work_category_id is None
        assert any("Прочее по смете" in w for w in result.warnings)

    def test_a_valid_child_code_restarts_a_resolved_subtree(self, resolver):
        result = resolver.resolve_proposal(
            rows(
                chapter("4", article="99.5. Неизвестная"),
                chapter("4.1", article="4.1. Ж/Б конструкции"),
                work(),
            )
        )
        assert result.rows["1"].work_category_id is None
        assert result.rows["2"].work_category_id == 141


class TestArticleOnNonChapter:
    def test_article_on_a_position_is_not_stored_and_warns(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("1", article="1. Подготовительные работы"), work(article="4.1. Ж/Б конструкции"))
        )
        assert result.rows["2"].smr_article_raw is None
        assert result.rows["2"].work_category_id is None
        assert any("не раздел" in w for w in result.warnings)


class TestKeyOrder:
    def test_shuffled_mapping_gives_the_same_result(self, resolver):
        ordered = rows(chapter("1", article="1. Подготовительные работы"), work(), work(number="2"))
        shuffled = {k: ordered[k] for k in ("3", "1", "2")}
        assert resolver.resolve_proposal(shuffled).rows == resolver.resolve_proposal(ordered).rows
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

```
cd backend && uv run pytest tests/unit/test_category_resolution.py -q
```

Ожидание: **collection error** — `ModuleNotFoundError: services.category_resolution`. Это законный красный на первом шаге TDD.

- [ ] **Step 3: Написать модуль (только путь без деградаций)**

Создать `backend/services/category_resolution.py`:

```python
"""Резолв статьи СМР по структуре файла сметы (спека Ф3).

Ось паспорта — статья классификатора, но в файле она стоит только на строках-разделах
и не на всех. Принадлежность строки разделу задаёт ТОЛЬКО порядок строк файла:
нумерация «№ раздела» дублируется, и у дублей бывают РАЗНЫЕ статьи, поэтому join по
номеру раздела в денежной логике запрещён (спека §1.1).

Модуль чистый: ни ORM-объектов, ни `Session`, ни записи в БД — единственное место,
которое читает справочник, это `from_db`. Предупреждения и счётчики возвращаются
результатом, а не копятся в состоянии, поэтому один экземпляр безопасно обслуживает
все предложения сметы.
"""
from __future__ import annotations

import enum
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import WorkCategory
from parser.constants import (
    JSON_KEY_ARTICLE_SMR,
    JSON_KEY_CHAPTER_NUMBER,
    JSON_KEY_IS_CHAPTER,
    JSON_KEY_JOB_TITLE,
    JSON_KEY_NUMBER,
)

#: Единственный источник привязки в v1; ck_position_items_category_source это
#: закрепляет. Расширение делает та фича, которая вводит новый источник.
CATEGORY_SOURCE_FILE = "file"

#: Номер раздела и код статьи: цифры через точки, завершающая точка допустима.
_CODE_RE = re.compile(r"^\d+(\.\d+)*\.?$")

#: Сколько примеров показывать в агрегированном предупреждении (как в
#: `_long_title_warning`): смета на 2,5 тыс. строк иначе утопит остальные.
MAX_WARNING_EXAMPLES = 5


class CategoryResolutionContractError(Exception):
    """Вход не является результатом парсера.

    Тихого фолбэка здесь нет намеренно: восстановить порядок строк файла из
    неканоничных ключей нельзя, а догадка дала бы правдоподобную, но чужую
    структуру — тот же класс ошибки, что «вложить раздел в текущую вершину».
    """


class RowKind(str, enum.Enum):
    CHAPTER = "chapter"
    POSITION = "position"
    OUTSIDE_STRUCTURE = "outside_structure"


@dataclass(frozen=True)
class CategoryRef:
    id: int
    title: str


@dataclass(frozen=True)
class RowResolution:
    position_key: str
    kind: RowKind
    parent_position_key: str | None = None
    smr_article_raw: str | None = None
    work_category_id: int | None = None
    category_source: str | None = None


@dataclass(frozen=True)
class ResolutionCounters:
    chapters_own: int = 0
    chapters_inherited: int = 0
    chapters_unassigned: int = 0
    positions_unassigned: int = 0
    rows_outside_structure: int = 0


@dataclass(frozen=True)
class ProposalResolution:
    rows: dict[str, RowResolution]
    warnings: list[str]
    structure_disabled: bool
    counters: ResolutionCounters


@dataclass
class _StackEntry:
    position_key: str
    depth: int
    category: CategoryRef | None
    """Эффективная статья: своя либо унаследованная. Считается в момент помещения в
    стек, поэтому наследование стоит O(1) и не требует проходов вверх."""


def _norm(value: Any) -> str:
    """Предикат пустоты и сравнимая форма: None → '', пробельные последовательности схлопнуты."""
    return "" if value is None else " ".join(str(value).split())


def _trimmed(value: Any) -> str | None:
    """Значение ячейки для хранения: обрезано по краям, пустое → None (как `_text` импорта)."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _row_kind(row: Mapping[str, Any]) -> RowKind:
    """Вид строки. Порядок правил задан спекой §2.3 и ничего не перекрывает молча.

    `CHAPTER` возвращается тогда и только тогда, когда истинен `is_chapter` парсера, —
    то есть гейт полей статьи по `kind` тождествен гейту по колонке БД `is_chapter`,
    на которую ссылается CHECK. Иначе импорт падал бы о констрейнт.
    """
    flag = bool(row.get(JSON_KEY_IS_CHAPTER))
    number_blank = _norm(row.get(JSON_KEY_NUMBER)) == ""
    chapter_blank = _norm(row.get(JSON_KEY_CHAPTER_NUMBER)) == ""
    if number_blank and chapter_blank and not flag:
        return RowKind.OUTSIDE_STRUCTURE
    if flag:
        return RowKind.CHAPTER
    return RowKind.POSITION


def _depth(number: str) -> int:
    """Глубина раздела = число сегментов его номера без завершающей точки."""
    return len(number.rstrip(".").split("."))


@dataclass
class _Warnings:
    """Копилка на один вызов. Тексты собираются в конце, в стабильном порядке."""

    unknown_code: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    title_mismatch: dict[str, tuple[str, str]] = field(default_factory=dict)
    unreadable_prefix: list[str] = field(default_factory=list)
    outside_structure: list[str] = field(default_factory=list)
    article_on_non_chapter: list[str] = field(default_factory=list)

    def messages(self, counters: ResolutionCounters) -> list[str]:
        out: list[str] = []
        for code, places in sorted(self.unknown_code.items()):
            out.append(
                f"Код статьи «{code}» не найден в классификаторе (строк: {len(places)}): "
                f"{_examples(places)}. Такие разделы и их позиции без своих кодов остались "
                "без статьи — исправьте код в файле либо добавьте статью в справочник. "
                "Статьи не создаются автоматически."
            )
        for code, (in_file, in_catalog) in sorted(self.title_mismatch.items()):
            out.append(
                f"Название статьи «{code}» в файле («{in_file}») не совпадает со "
                f"справочником («{in_catalog}»). Привязка сделана по коду — он "
                "авторитетен; расхождение стоит проверить глазами."
            )
        if self.unreadable_prefix:
            out.append(
                f"«Статья СМР» заполнена, но код из неё не читается (строк: "
                f"{len(self.unreadable_prefix)}): {_examples(self.unreadable_prefix)}. "
                "Такие разделы остались без статьи: файл называет статью, а какую именно "
                "— определить нельзя, и подставлять статью родителя здесь было бы подменой."
            )
        if self.outside_structure:
            out.append(
                f"Строк без номера позиции и без номера раздела: "
                f"{len(self.outside_structure)}: {_examples(self.outside_structure)}. "
                "Они не стоят в структуре файла, поэтому не привязаны ни к одному разделу "
                "и попадут в «Нераспределённое»."
            )
        if self.article_on_non_chapter:
            out.append(
                f"«Статья СМР» заполнена на строке, которая не раздел (строк: "
                f"{len(self.article_on_non_chapter)}): "
                f"{_examples(self.article_on_non_chapter)}. Значение не сохранено в смете: "
                "статья привязывается только через раздел. Полный текст остался в исходных "
                "данных загрузки."
            )
        if counters.chapters_unassigned:
            out.append(
                f"Разделов без статьи: {counters.chapters_unassigned}; позиций под ними: "
                f"{counters.positions_unassigned}. Их деньги попадут в «Нераспределённое» — "
                "это не ошибка импорта, если в файле статья действительно не проставлена."
            )
        return out


def _examples(places: list[str]) -> str:
    shown = "; ".join(places[:MAX_WARNING_EXAMPLES])
    hidden = len(places) - MAX_WARNING_EXAMPLES
    return f"{shown}{f'; …и ещё {hidden}' if hidden > 0 else ''}"


def _place(key: str, row: Mapping[str, Any], *, raw: str | None = None) -> str:
    """Где искать строку в файле. Номера строки листа импорт не знает (спека §2.9)."""
    number = _norm(row.get(JSON_KEY_CHAPTER_NUMBER)) or _norm(row.get(JSON_KEY_NUMBER)) or "—"
    title = _norm(row.get(JSON_KEY_JOB_TITLE))[:60] or "без названия"
    tail = f", значение «{raw}»" if raw is not None else ""
    return f"позиция {key} (№ раздела «{number}», «{title}»{tail})"


def _ordered_keys(positions: Mapping[str, Any]) -> list[str]:
    """Ключи в порядке файла. Контракт парсера — строго «1..N» без пропусков.

    Проверки «приводится к int» недостаточно: «1» и «01» дают одно число, а порядок
    строк восстанавливается именно из ключей (в `raw_data` это JSONB, где порядок не
    сохраняется).
    """
    expected = [str(i) for i in range(1, len(positions) + 1)]
    if set(positions) != set(expected):
        raise CategoryResolutionContractError(
            "Ключи позиций не образуют последовательность «1..N»: получено "
            f"{sorted(positions)[:10]} (всего {len(positions)}). Порядок строк файла "
            "восстановить нельзя, а он определяет принадлежность строки разделу."
        )
    for key in expected:
        if not isinstance(positions[key], Mapping):
            raise CategoryResolutionContractError(
                f"Позиция «{key}» не является словарём "
                f"({type(positions[key]).__name__}) — это не вывод парсера."
            )
    return expected


class CategoryResolver:
    """Разрешает «Статью СМР» в статью классификатора по структуре файла.

    Карта читается один раз на импорт: справочник маленький (362 строки), а
    обращений — по числу разделов. Форма повторяет `UnitResolver`, но конструктор
    берёт готовую карту, а не `Session`: тогда алгоритм — единственная нетривиальная
    часть фичи — проверяется юнит-тестами без БД.
    """

    def __init__(self, by_code: Mapping[str, CategoryRef]) -> None:
        self._by_code = dict(by_code)

    @classmethod
    def from_db(cls, db: Session) -> CategoryResolver:
        rows = db.execute(
            select(WorkCategory.code, WorkCategory.id, WorkCategory.title)
        ).all()
        return cls({code: CategoryRef(id=cid, title=title) for code, cid, title in rows})

    def resolve_proposal(self, positions: Mapping[str, Any]) -> ProposalResolution:
        keys = _ordered_keys(positions)
        return self._resolve_stack(positions, keys)

    def _resolve_stack(
        self, positions: Mapping[str, Any], keys: list[str]
    ) -> ProposalResolution:
        rows: dict[str, RowResolution] = {}
        warnings = _Warnings()
        stack: list[_StackEntry] = []
        own = inherited = unassigned = positions_unassigned = outside = 0

        for key in keys:
            row = positions[key]
            kind = _row_kind(row)

            if kind is RowKind.OUTSIDE_STRUCTURE:
                rows[key] = RowResolution(position_key=key, kind=kind)
                warnings.outside_structure.append(_place(key, row))
                outside += 1
                continue

            if kind is RowKind.POSITION:
                parent = stack[-1] if stack else None
                rows[key] = RowResolution(
                    position_key=key,
                    kind=kind,
                    parent_position_key=parent.position_key if parent else None,
                )
                if parent is None or parent.category is None:
                    positions_unassigned += 1
                raw = _norm(row.get(JSON_KEY_ARTICLE_SMR))
                if raw:
                    warnings.article_on_non_chapter.append(_place(key, row, raw=raw))
                continue

            number = _norm(row.get(JSON_KEY_CHAPTER_NUMBER))
            depth = _depth(number)
            while stack and stack[-1].depth >= depth:
                stack.pop()
            parent = stack[-1] if stack else None

            raw = _trimmed(row.get(JSON_KEY_ARTICLE_SMR))
            category, outcome = self._article_for(raw, parent, warnings, key, row)
            if outcome == "own":
                own += 1
            elif outcome == "inherited":
                inherited += 1
            else:
                unassigned += 1

            rows[key] = RowResolution(
                position_key=key,
                kind=kind,
                parent_position_key=parent.position_key if parent else None,
                smr_article_raw=raw,
                work_category_id=category.id if category else None,
                category_source=CATEGORY_SOURCE_FILE if category else None,
            )
            stack.append(_StackEntry(position_key=key, depth=depth, category=category))

        counters = ResolutionCounters(
            chapters_own=own,
            chapters_inherited=inherited,
            chapters_unassigned=unassigned,
            positions_unassigned=positions_unassigned,
            rows_outside_structure=outside,
        )
        return ProposalResolution(
            rows=rows,
            warnings=warnings.messages(counters),
            structure_disabled=False,
            counters=counters,
        )

    def _article_for(
        self,
        raw: str | None,
        parent: _StackEntry | None,
        warnings: _Warnings,
        key: str,
        row: Mapping[str, Any],
    ) -> tuple[CategoryRef | None, str]:
        """Эффективная статья раздела и итог для счётчика.

        **Утверждение файла сильнее наследования.** Наследование срабатывает только
        когда файл про статью молчит. Если «Статья СМР» заполнена, но прочитать её
        нельзя, статья предка НЕ подставляется: файл называет здесь другую статью, и
        подстановка отнесла бы деньги туда, куда файл их не относил.
        """
        if raw is None:
            inherited = parent.category if parent else None
            return inherited, "inherited" if inherited else "unassigned"

        collapsed = _norm(raw)
        prefix = collapsed.split(" ")[0]
        if not _CODE_RE.match(prefix):
            warnings.unreadable_prefix.append(_place(key, row, raw=collapsed))
            return None, "unassigned"

        code = prefix.rstrip(".")
        ref = self._by_code.get(code)
        if ref is None:
            warnings.unknown_code[code].append(_place(key, row))
            return None, "unassigned"

        in_file = collapsed[len(prefix):].strip()
        if in_file and in_file.casefold() != _norm(ref.title).casefold():
            warnings.title_mismatch.setdefault(code, (in_file, ref.title))
        return ref, "own"
```

- [ ] **Step 4: Прогнать тесты стека**

```
cd backend && uv run pytest tests/unit/test_category_resolution.py -q
```

Ожидание: **все зелёные** (24 теста Task 2). Если падает `test_shuffled_mapping_gives_the_same_result` — значит обход пошёл по порядку словаря, а не по `_ordered_keys`.

- [ ] **Step 5: Линт и коммит**

```bash
cd backend && uv run ruff check services/category_resolution.py tests/unit/test_category_resolution.py
cd .. && git add backend/services/category_resolution.py backend/tests/unit/test_category_resolution.py
git commit -m "feat(import): резолвер статьи СМР — стек по файлу, наследование, код и название"
```

---

### Task 3: резолвер — деградации и подавление предупреждений

**Files:**
- Modify: `backend/services/category_resolution.py`
- Test: `backend/tests/unit/test_category_resolution.py`

**Interfaces:**
- Consumes: всё из Task 2.
- Produces: `ProposalResolution.structure_disabled = True` с `parent_position_key = None`, `work_category_id = None`, `category_source = None` у **всех** строк и `smr_article_raw` только на строках `is_chapter`; ровно одно предупреждение о причине D плюс независимые предупреждения.

- [ ] **Step 1: Написать падающие тесты деградаций**

Дописать в `backend/tests/unit/test_category_resolution.py`:

```python
class TestStructureDisabled:
    """D: неразбираемая структура гасит привязку по ВСЕМУ предложению (спека §2.4)."""

    def test_unparsable_chapter_number_disables_the_whole_proposal(self, resolver):
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                work(),
                chapter("прим.", article="4.1. Ж/Б конструкции", title="Примечание"),
                work(number="2"),
            )
        )
        assert result.structure_disabled is True
        for row in result.rows.values():
            assert row.parent_position_key is None
            assert row.work_category_id is None
            assert row.category_source is None
        # Аудит сохранён: сырое значение на разделах остаётся.
        assert result.rows["1"].smr_article_raw == "1. Подготовительные работы"
        assert result.rows["3"].smr_article_raw == "4.1. Ж/Б конструкции"
        assert result.rows["2"].smr_article_raw is None

    def test_disabled_structure_reports_the_reason_with_raw_values(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("1", article="1. Подготовительные работы"),
                 chapter("прим.", title="Примечание"))
        )
        assert len(result.warnings) == 1
        assert "прим." in result.warnings[0]
        assert "Примечание" in result.warnings[0]

    def test_both_causes_are_named_separately(self, resolver):
        """Спека §2.9: причины D перечисляются по отдельности, со своими счётчиками.

        Без этого деградация на числовом `0` была бы верной, а объяснение — неверным:
        предупреждение говорило бы только про неразбираемый номер.
        """
        result = resolver.resolve_proposal(
            rows(
                chapter("прим.", title="Примечание"),                       # причина 1
                position(job_title="Работа", number="2", chapter_number=0,
                         is_chapter=False),                                  # причина 2
            )
        )
        assert len(result.warnings) == 1
        message = result.warnings[0]
        assert "номер раздела не разбирается (1)" in message
        assert "структурный конфликт (1)" in message
        assert "прим." in message
        assert "is_chapter=False" in message

    def test_resolution_warnings_are_suppressed_when_structure_is_disabled(self, resolver):
        """Они описывали бы резолв, которого не было (спека §2.9)."""
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="99.5. Неизвестная"),          # неизвестный код
                chapter("2", article="Прочее по смете"),            # нечитаемый префикс
                chapter("3", article="7.2. Внутреняя отделка"),     # расхождение названия
                chapter("прим.", title="Примечание"),               # причина D
            )
        )
        assert result.structure_disabled is True
        assert len(result.warnings) == 1
        assert "99.5" not in result.warnings[0]
        assert "Внутреняя" not in result.warnings[0]
        assert result.counters.chapters_unassigned == 0

    def test_independent_warnings_survive_disabled_structure(self, resolver):
        """Строка вне структуры и статья на не-разделе — не следствия резолва."""
        result = resolver.resolve_proposal(
            rows(
                chapter("прим.", title="Примечание"),
                work(article="4.1. Ж/Б конструкции"),
                position(job_title="Дополнительные работы", number=None,
                         chapter_number=None, is_chapter=False),
            )
        )
        assert result.structure_disabled is True
        assert len(result.warnings) == 3
        assert any("прим." in w for w in result.warnings)
        assert any("вне структуры" in w or "без номера" in w for w in result.warnings)
        assert any("не раздел" in w for w in result.warnings)


class TestStructuralConflicts:
    """Расхождение is_chapter с номером — тоже D (спека §2.3)."""

    def test_blank_a_and_b_with_chapter_flag_disables_structure(self, resolver):
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                position(job_title="Дополнительные работы", number=None,
                         chapter_number=None, is_chapter=True),
            )
        )
        assert result.structure_disabled is True
        assert "is_chapter" in result.warnings[0]

    def test_numeric_zero_in_the_chapter_number_disables_structure(self, resolver):
        """bool(0) ложно, а _norm(0) = «0» непусто — расхождение предикатов."""
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                position(job_title="Работа", number="2", chapter_number=0, is_chapter=False),
            )
        )
        assert result.structure_disabled is True

    def test_chapter_flag_without_a_number_disables_structure(self, resolver):
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                position(job_title="Раздел без номера", number="2",
                         chapter_number="   ", is_chapter=True),
            )
        )
        assert result.structure_disabled is True

    def test_real_aggregate_row_is_consistent_and_does_not_disable(self, resolver):
        """A и B пусты, is_chapter=false — так и приходит настоящая агрегатная строка."""
        result = resolver.resolve_proposal(
            rows(
                chapter("1", article="1. Подготовительные работы"),
                position(job_title="Дополнительные работы", number=None,
                         chapter_number=None, is_chapter=False),
            )
        )
        assert result.structure_disabled is False


class TestKeyContract:
    def test_non_canonical_keys_are_rejected(self, resolver):
        with pytest.raises(CategoryResolutionContractError, match="1..N"):
            resolver.resolve_proposal({"01": work(), "2": work()})

    def test_a_gap_in_the_keys_is_rejected(self, resolver):
        with pytest.raises(CategoryResolutionContractError):
            resolver.resolve_proposal({"1": work(), "3": work()})

    def test_a_non_dict_row_is_rejected(self, resolver):
        with pytest.raises(CategoryResolutionContractError, match="не является словарём"):
            resolver.resolve_proposal({"1": "не словарь"})

    def test_empty_proposal_is_valid(self, resolver):
        result = resolver.resolve_proposal({})
        assert result.rows == {}
        assert result.warnings == []
        assert result.structure_disabled is False
```

Добавить `CategoryResolutionContractError` в импорт файла тестов.

- [ ] **Step 2: Прогнать — убедиться, что падает**

```
cd backend && uv run pytest tests/unit/test_category_resolution.py -q -k "Disabled or Conflicts or KeyContract"
```

Ожидание: тесты `TestStructureDisabled` и `TestStructuralConflicts` **красные** (`structure_disabled` всегда False, у строк проставлены родители), `TestKeyContract` — зелёные (проверка ключей уже есть в Task 2).

- [ ] **Step 3: Добавить фазу валидации**

В `backend/services/category_resolution.py` добавить константы причин, сборщик конфликтов и путь D; заменить `resolve_proposal`.

```python
#: Причины структурного конфликта. Текст попадает в предупреждение, поэтому он
#: описывает расхождение, а не «ошибку формата».
_CONFLICT_BLANK_BUT_CHAPTER = "нет ни номера позиции, ни номера раздела, но is_chapter=true"
_CONFLICT_NUMBER_WITHOUT_FLAG = "номер раздела заполнен, но is_chapter=false"
_CONFLICT_FLAG_WITHOUT_NUMBER = "is_chapter=true, но номер раздела пуст"


def _structural_conflicts(
    positions: Mapping[str, Any], keys: list[str]
) -> list[tuple[str, str]]:
    """Расхождения между `is_chapter` парсера и номером раздела.

    Парсер считает `is_chapter = bool(сырое значение)`, а пустоту мы определяем
    нормализацией: предикаты расходятся на числовом `0` (bool ложен, норма непуста) и
    на пробельной строке (bool истинен, норма пуста). Если резолвер сочтёт разделом
    строку, у которой в БД `is_chapter = false`, запись полей статьи уронит импорт о
    ck_position_items_article_only_on_chapters — поэтому расхождение гасит структуру,
    а не разрешается порядком условий.
    """
    found: list[tuple[str, str]] = []
    for key in keys:
        row = positions[key]
        flag = bool(row.get(JSON_KEY_IS_CHAPTER))
        number_blank = _norm(row.get(JSON_KEY_NUMBER)) == ""
        chapter_blank = _norm(row.get(JSON_KEY_CHAPTER_NUMBER)) == ""
        if flag and chapter_blank:
            found.append(
                (key, _CONFLICT_BLANK_BUT_CHAPTER if number_blank else _CONFLICT_FLAG_WITHOUT_NUMBER)
            )
        elif not flag and not chapter_blank:
            found.append((key, _CONFLICT_NUMBER_WITHOUT_FLAG))
    return found


def _unparsable_numbers(positions: Mapping[str, Any], keys: list[str]) -> list[str]:
    """Разделы, у которых номер не разбирается в глубину."""
    bad: list[str] = []
    for key in keys:
        row = positions[key]
        if not bool(row.get(JSON_KEY_IS_CHAPTER)):
            continue
        number = _norm(row.get(JSON_KEY_CHAPTER_NUMBER))
        if number and not _CODE_RE.match(number):
            bad.append(key)
    return bad
```

Заменить `resolve_proposal`:

```python
    def resolve_proposal(self, positions: Mapping[str, Any]) -> ProposalResolution:
        keys = _ordered_keys(positions)
        conflicts = _structural_conflicts(positions, keys)
        bad_numbers = _unparsable_numbers(positions, keys)
        if conflicts or bad_numbers:
            return _disabled(positions, keys, conflicts, bad_numbers)
        return self._resolve_stack(positions, keys)
```

и добавить путь D модульной функцией:

```python
def _disabled(
    positions: Mapping[str, Any],
    keys: list[str],
    conflicts: list[tuple[str, str]],
    bad_numbers: list[str],
) -> ProposalResolution:
    """Структура не определена — привязки нет ни у одной строки предложения.

    Частичной структуры не бывает: половина сметы с привязкой и половина без дала бы
    паспорт, правдоподобный ровно настолько, насколько неверный. Строки при этом
    сохраняются все, и `smr_article_raw` на разделах остаётся как аудит.
    """
    rows: dict[str, RowResolution] = {}
    warnings = _Warnings()
    outside = 0

    for key in keys:
        row = positions[key]
        kind = _row_kind(row)
        rows[key] = RowResolution(
            position_key=key,
            kind=kind,
            parent_position_key=None,
            # Гейт по kind тождествен гейту по is_chapter (см. _row_kind), поэтому
            # CHECK не нарушается даже на конфликтующем входе.
            smr_article_raw=_trimmed(row.get(JSON_KEY_ARTICLE_SMR))
            if kind is RowKind.CHAPTER
            else None,
        )
        if kind is RowKind.OUTSIDE_STRUCTURE:
            warnings.outside_structure.append(_place(key, row))
            outside += 1
        elif kind is RowKind.POSITION:
            raw = _norm(row.get(JSON_KEY_ARTICLE_SMR))
            if raw:
                warnings.article_on_non_chapter.append(_place(key, row, raw=raw))

    # Предупреждения категорийного резолва не выдаются: резолва не было. Счётчики
    # нераспределённых по той же причине нулевые.
    counters = ResolutionCounters(rows_outside_structure=outside)
    messages = [_disabled_message(positions, conflicts, bad_numbers)]
    messages.extend(warnings.messages(counters))
    return ProposalResolution(
        rows=rows, warnings=messages, structure_disabled=True, counters=counters
    )


def _disabled_message(
    positions: Mapping[str, Any],
    conflicts: list[tuple[str, str]],
    bad_numbers: list[str],
) -> str:
    """Одно предупреждение, называющее ОБЕ причины по отдельности (спека §2.9)."""
    parts: list[str] = []
    if bad_numbers:
        places = [
            _place(key, positions[key], raw=_norm(positions[key].get(JSON_KEY_CHAPTER_NUMBER)))
            for key in bad_numbers
        ]
        parts.append(f"номер раздела не разбирается ({len(bad_numbers)}): {_examples(places)}")
    if conflicts:
        places = [
            f"{_place(key, positions[key])}: {reason}, "
            f"is_chapter={bool(positions[key].get(JSON_KEY_IS_CHAPTER))}"
            for key, reason in conflicts
        ]
        parts.append(f"структурный конфликт ({len(conflicts)}): {_examples(places)}")
    return (
        "Структура разделов файла не определена, поэтому статьи не привязаны ни к одной "
        f"строке этого предложения. Причины — {'; '.join(parts)}. Смета загружена целиком, "
        "деньги на месте; чтобы получить разбивку по статьям, исправьте нумерацию разделов "
        "и загрузите файл повторно."
    )
```

- [ ] **Step 4: Прогнать весь юнит-файл**

```
cd backend && uv run pytest tests/unit/test_category_resolution.py -q
```

Ожидание: **все зелёные** (24 из Task 2 + 12 новых = 36). Проверить отдельно, что `test_real_aggregate_row_is_consistent_and_does_not_disable` зелёный — иначе новое правило конфликтов начало судить настоящую агрегатную строку, и Task 5 на fixture упадёт.

- [ ] **Step 5: Линт и коммит**

```bash
cd backend && uv run ruff check services/category_resolution.py tests/unit/test_category_resolution.py
cd .. && git add backend/services/category_resolution.py backend/tests/unit/test_category_resolution.py
git commit -m "feat(import): деградация D — неразбираемая структура гасит привязку по предложению"
```

---

### Task 4: материализация в импорте

**Files:**
- Modify: `backend/services/estimate_import.py:338-348` (подпись), `:434-443` (вызов), `:619-749` (`_import_positions`)
- Modify: `backend/services/import_pipeline.py:243-253`
- Modify: `backend/tests/integration/test_estimate_import.py`, `test_matching.py`, `test_review_concurrency.py` (по одному вызову `import_estimate(`)
- Test: `backend/tests/integration/test_estimate_import.py`

**Interfaces:**
- Consumes: `CategoryResolver`, `ProposalResolution`, `CategoryResolutionContractError` из Task 2–3.
- Produces: `import_estimate(..., category_resolver: CategoryResolver)` — **обязательный** keyword-параметр; заполненные `position_items.smr_article_raw | work_category_id | category_source | chapter_item_id`; предупреждения резолва в `ImportOutcome.warnings`.

- [ ] **Step 1: Написать падающие интеграционные тесты**

Дописать в `backend/tests/integration/test_estimate_import.py`. Фактические имена файла: сессия — `db_session`, фикстура `resolver` отдаёт `UnitResolver`, а импорт зовётся хелпером `run_import(db_session, resolver, contract, data, *, amendment_no=None, replace=False, job=None)` — **44 вызова**. Поэтому `category_resolver` этот хелпер строит **внутри себя** (Step 6), и ни один из 44 вызовов не правится.

```python
class TestCategoryMaterialization:
    """Ф3: план резолвера доезжает до строк сметы (спека Ф3 §2.8)."""

    def test_positions_point_at_their_chapter_row(self, db_session, resolver, contract):
        payload = payload_for(
            contract,
            [
                position(job_title="1 Подготовительные работы", number="1",
                         chapter_number="1", article_smr="1. Подготовительные работы",
                         is_chapter=True),
                position(job_title="Расчистка", number="2", unit="м2",
                         suggested_quantity=10, unit_cost_total="100.00"),
            ],
        )
        outcome = run_import(db_session, resolver, contract, payload)
        items = _items_by_key(db_session, outcome.estimate_id)
        chapter, work = items["1"], items["2"]
        assert chapter.is_chapter is True
        assert chapter.work_category_id is not None
        assert chapter.category_source == "file"
        assert chapter.smr_article_raw == "1. Подготовительные работы"
        assert work.chapter_item_id == chapter.id
        assert work.work_category_id is None

    def test_row_outside_structure_is_not_attached(self, db_session, resolver, contract):
        """Агрегатная строка допработ: chapter_item_id остаётся NULL (спека §2.6)."""
        payload = payload_for(
            contract,
            [
                position(job_title="1 Подготовительные работы", number="1",
                         chapter_number="1", article_smr="1. Подготовительные работы",
                         is_chapter=True),
                position(job_title="Расчистка", number="2", total_cost_total="500.00"),
                position(job_title="Дополнительные работы", number=None,
                         chapter_number=None, total_cost_total="700.00"),
            ],
        )
        outcome = run_import(db_session, resolver, contract, payload)
        items = _items_by_key(db_session, outcome.estimate_id)
        assert items["3"].chapter_item_id is None
        assert items["2"].chapter_item_id == items["1"].id
        assert any("вне структуры" in w or "без номера" in w for w in outcome.warnings)

    def test_chapter_item_id_never_crosses_a_proposal(self, db_session, resolver, contract):
        """Два лота по одному предложению — поперечных ссылок нет (спека §4.2)."""
        first = proposal([
            position(job_title="1 Подготовительные работы", number="1", chapter_number="1",
                     article_smr="1. Подготовительные работы", is_chapter=True),
            position(job_title="Расчистка", number="2"),
        ])
        second = proposal([
            position(job_title="4 Возведение конструкций", number="1", chapter_number="4",
                     article_smr="4. Возведение конструкций", is_chapter=True),
            position(job_title="Монолит", number="2"),
        ])
        payload = payload_for(contract)
        payload[JSON_KEY_LOTS] = {
            "lot_1": {JSON_KEY_LOT_TITLE: "Лот №1", JSON_KEY_PROPOSALS: {"contractor_1": first},
                      JSON_KEY_BASELINE_PROPOSAL: {JSON_KEY_CONTRACTOR_TITLE: BASELINE_MISSING_TITLE}},
            "lot_2": {JSON_KEY_LOT_TITLE: "Лот №2", JSON_KEY_PROPOSALS: {"contractor_1": second},
                      JSON_KEY_BASELINE_PROPOSAL: {JSON_KEY_CONTRACTOR_TITLE: BASELINE_MISSING_TITLE}},
        }
        run_import(db_session, resolver, contract, payload)
        crossing = db_session.execute(
            sa.text(
                "select count(*) from position_items child "
                "join position_items parent on parent.id = child.chapter_item_id "
                "where child.proposal_id <> parent.proposal_id"
            )
        ).scalar_one()
        assert crossing == 0
        # И привязка при этом есть — иначе ноль был бы вакуозным.
        attached = db_session.execute(
            sa.text("select count(*) from position_items where chapter_item_id is not null")
        ).scalar_one()
        assert attached == 2

    def test_disabled_structure_keeps_rows_and_raw_but_no_links(
        self, db_session, resolver, contract
    ):
        payload = payload_for(
            contract,
            [
                position(job_title="1 Подготовительные работы", number="1", chapter_number="1",
                         article_smr="1. Подготовительные работы", is_chapter=True),
                position(job_title="Расчистка", number="2", total_cost_total="500.00"),
                position(job_title="Примечание", number="3", chapter_number="прим.",
                         is_chapter=True),
            ],
        )
        outcome = run_import(db_session, resolver, contract, payload)
        items = _items_by_key(db_session, outcome.estimate_id)
        assert len(items) == 3
        assert items["1"].smr_article_raw == "1. Подготовительные работы"
        assert all(i.work_category_id is None for i in items.values())
        assert all(i.category_source is None for i in items.values())
        assert all(i.chapter_item_id is None for i in items.values())
        assert any("не определена" in w for w in outcome.warnings)

    def test_non_canonical_position_keys_fail_the_import_with_an_explanation(
        self, db_session, resolver, contract
    ):
        payload = payload_for(contract, [position(job_title="Расчистка", number="1")])
        positions = payload[JSON_KEY_LOTS]["lot_1"][JSON_KEY_PROPOSALS]["contractor_1"][
            JSON_KEY_CONTRACTOR_ITEMS
        ][JSON_KEY_CONTRACTOR_POSITIONS]
        positions["07"] = positions.pop("1")
        with pytest.raises(EstimateImportError, match="1..N"):
            run_import(db_session, resolver, contract, payload)

    def test_replace_removes_an_estimate_with_filled_chapter_links(
        self, db_session, resolver, contract
    ):
        """RESTRICT на составном self-FK каскаду не мешает (спека §1.3 факт 8).

        Замер на scratch-таблицах это показал; здесь то же свойство проверяется на
        настоящей схеме и настоящем replace-флоу.
        """
        rows_with_links = [
            position(job_title="1 Подготовительные работы", number="1", chapter_number="1",
                     article_smr="1. Подготовительные работы", is_chapter=True),
            position(job_title="1.1 Расчистка", number="2", chapter_number="1.1",
                     is_chapter=True),
            position(job_title="Вывоз грунта", number="3", total_cost_total="500.00"),
        ]
        first = run_import(db_session, resolver, contract, payload_for(contract, rows_with_links))
        filled = db_session.execute(
            sa.text("select count(*) from position_items where chapter_item_id is not null")
        ).scalar_one()
        assert filled == 2            # иначе замена ничего не доказывает

        second = run_import(
            db_session, resolver, contract, payload_for(contract, rows_with_links), replace=True
        )
        assert second.replaced_estimate_id == first.estimate_id
        assert db_session.get(Estimate, first.estimate_id) is None
        assert len(_items_by_key(db_session, second.estimate_id)) == 3
```

Локальная фикстура договора (в начало нового класса, сразу после docstring) и хелпер (в конец файла):

```python
    @pytest.fixture
    def contract(self, factories):
        return factories.ContractFactory.create()
```

```python
def _items_by_key(db_session, estimate_id: int) -> dict[str, PositionItem]:
    rows = db_session.execute(
        sa.select(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id)
    ).scalars().all()
    return {item.position_key_in_proposal: item for item in rows}
```

Импорты файла дополнить: `JSON_KEY_LOTS`, `JSON_KEY_PROPOSALS`, `JSON_KEY_LOT_TITLE`, `JSON_KEY_BASELINE_PROPOSAL`, `JSON_KEY_CONTRACTOR_TITLE`, `JSON_KEY_CONTRACTOR_ITEMS`, `JSON_KEY_CONTRACTOR_POSITIONS` из `parser.constants` и `BASELINE_MISSING_TITLE` из `parser.postprocess` (в `tests/payloads.py` они уже используются — брать оттуда нельзя, импортировать из источника).

- [ ] **Step 2: Прогнать — убедиться, что падает**

```
cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  uv run pytest tests/integration/test_estimate_import.py -k CategoryMaterialization -v
```

Ожидание: **6 failed** — на этом шаге `run_import` ещё не передаёт `category_resolver`, поэтому падение будет по отсутствию новых значений (`chapter_item_id is None` там, где ожидается ссылка), а не по неизвестному аргументу. Красный обязателен: зелёный до реализации означает, что тесты ничего не проверяют.

- [ ] **Step 3: Провести резолвер в импорт**

В `backend/services/estimate_import.py`:

1. импорт:

```python
from services.category_resolution import (
    CategoryResolutionContractError,
    CategoryResolver,
    ProposalResolution,
)
```

2. в подпись `import_estimate` добавить обязательный keyword-параметр после `unit_resolver` и описать его в docstring («резолв статьи по структуре файла (§Ф3)»):

```python
    unit_resolver: UnitResolver,
    category_resolver: CategoryResolver,
```

3. передать в вызов `_import_positions` (строка ~434):

```python
        lot_positions, lot_to_match, lot_priced = _import_positions(
            db,
            proposal_id=proposal.id,
            proposal_data=proposal_data,
            unit_resolver=unit_resolver,
            category_resolver=category_resolver,
            value_problems=value_problems,
            warnings=warnings,
            long_titles=long_titles,
            lot_key=str(lot_key),
        )
```

- [ ] **Step 4: Применить план в `_import_positions`**

Добавить параметр `category_resolver: CategoryResolver` в подпись. Сразу после получения `positions` (строка ~638), до цикла:

```python
    # План строится ДО единой записи в БД: тогда решение «структура не определена»
    # атомарно по всему предложению, а не оставляет половину сметы привязанной.
    try:
        resolution = category_resolver.resolve_proposal(positions)
    except CategoryResolutionContractError as exc:
        raise EstimateImportError(
            f"Смету нельзя импортировать: {exc}"
        ) from exc
    warnings.extend(resolution.warnings)
```

В теле цикла, после `job_title = _text(...)`:

```python
        decision = resolution.rows[str(position_key)]
```

и в конструктор `PositionItem(...)` добавить три поля (рядом с `is_chapter`):

```python
            smr_article_raw=decision.smr_article_raw,
            work_category_id=decision.work_category_id,
            category_source=decision.category_source,
```

После `db.add_all(rows)` и `db.flush()` (строки 734-735) вставить второй проход:

```python
    # Второй проход: self-FK можно проставить только когда id уже есть. Поля статьи
    # здесь НЕ переписываются — они проставлены при создании строк. UPDATE затрагивает
    # chapter_item_id и updated_at, но updated_at берёт now() = время начала
    # транзакции, поэтому наблюдаемого временного следа после commit не остаётся.
    by_key = {item.position_key_in_proposal: item for item in rows}
    for item in rows:
        parent_key = resolution.rows[item.position_key_in_proposal].parent_position_key
        if parent_key is not None:
            item.chapter_item_id = by_key[parent_key].id
    db.flush()
```

- [ ] **Step 5: Создать резолвер в конвейере**

В `backend/services/import_pipeline.py`, строка 243:

```python
            resolver = UnitResolver(db)
            category_resolver = CategoryResolver.from_db(db)
            outcome = import_estimate(
                db,
                contract=contract,
                amendment_no=context.amendment_no,
                data=parse_result.data,
                parser_version=parse_result.parser_version,
                import_job_id=job_id,
                replace=replace,
                unit_resolver=resolver,
                category_resolver=category_resolver,
            )
```

плюс импорт `from services.category_resolution import CategoryResolver`.

- [ ] **Step 6: Починить три вызова `import_estimate` в тестах**

Вызовов ровно три, по одному в файле, и все — внутри хелперов, поэтому правок ровно три:

- `tests/integration/test_estimate_import.py` — внутри `run_import(...)` добавить
  `category_resolver=CategoryResolver.from_db(db_session)`. Сессия у хелпера уже есть,
  поэтому **ни один из 44 его вызовов не меняется**;
- `tests/integration/test_matching.py` и `tests/integration/test_review_concurrency.py` —
  то же в их локальных хелперах, с именем сессии из окружающего кода.

Плюс импорт `from services.category_resolution import CategoryResolver` в каждый из трёх файлов.

**Ожиданий существующих тестов не менять.** Новые колонки на старых payload'ах либо заполняются корректно, либо остаются NULL; ни одно прежнее утверждение о деньгах, счётчиках и числе позиций поехать не должно. Поехавшее — признак дефекта Task 4, а не устаревшего ожидания.

- [ ] **Step 7: Прогнать новые и все интеграционные**

```
cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  uv run pytest tests/integration/test_estimate_import.py -k CategoryMaterialization -v
cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  uv run pytest -q
```

Ожидание: сначала **6 passed** (пять тестов материализации плюс replace), затем весь набор зелёный при `6 skipped`. Итоговое число тестов **не выводить арифметикой** — оно замеряется в Task 5 Step 3 по `--collect-only` до и после фичи.

- [ ] **Step 8: Коммит**

```bash
git add backend/services/estimate_import.py backend/services/import_pipeline.py \
        backend/tests/integration/test_estimate_import.py \
        backend/tests/integration/test_matching.py backend/tests/integration/test_review_concurrency.py
git commit -m "feat(import): материализация привязки к статье на position_items"
```

---

### Task 5: интеграция на fixture — замеренные числа

**Files:**
- Modify: `backend/tests/integration/test_import_fixture_e2e.py`
- Test: тот же файл

**Interfaces:**
- Consumes: полный конвейер `import_pipeline` (резолвер уже подключён в Task 4).
- Produces: константы `FIXTURE_CHAPTERS_OWN = 222`, `FIXTURE_CHAPTERS_INHERITED = 485`, `FIXTURE_CHAPTERS_UNASSIGNED = 39`, `FIXTURE_POSITIONS_UNASSIGNED = 38`.

**Конвенции файла (замерено):** фикстуры `committing_client`, `committing_db`, `committing_factories`, `stub_with_fixture` (подменяет `parse_estimate` уже разобранным fixture — самая дорогая операция кэшируется на модуль); загрузка идёт хелпером `upload(client, contract_id, content)` и `xlsx_stub(marker)`; `estimate_id` берётся из `job["estimate_id"]`. Свою механику загрузки не изобретать. Replace-флоу здесь **не проверяется** — эндпоинтный `upload` этого файла не умеет `replace`, а на сервисном уровне для этого уже есть `run_import(..., replace=True)`, поэтому тест замены живёт в Task 4.

- [ ] **Step 1: Написать падающие тесты на fixture**

Дописать в `backend/tests/integration/test_import_fixture_e2e.py` рядом с существующими константами:

```python
# Замер спеки Ф3 §1.1 на этом файле. Сумма сходится с FIXTURE_CHAPTERS: 222+485+39=746.
FIXTURE_CHAPTERS_OWN = 222
FIXTURE_CHAPTERS_INHERITED = 485
FIXTURE_CHAPTERS_UNASSIGNED = 39
FIXTURE_POSITIONS_UNASSIGNED = 38
#: Разделов глубины 1 — единственные строки без родителя (строк вне структуры в этом
#: файле нет). Из гистограммы глубин пробника гейта 1: {1: 16, 2: 56, 3: 130, 4: 196,
#: 5: 337, 6: 11}; сумма 746 = FIXTURE_CHAPTERS.
FIXTURE_CHAPTERS_TOP_LEVEL = 16


def test_measured_counts_add_up_to_the_chapter_total():
    """Арифметика замера — до всякой БД: три класса разделов покрывают все разделы."""
    assert (
        FIXTURE_CHAPTERS_OWN + FIXTURE_CHAPTERS_INHERITED + FIXTURE_CHAPTERS_UNASSIGNED
        == FIXTURE_CHAPTERS
    )
```

фикстуру загрузки (рядом с `stub_with_fixture`, механика повторяет `test_upload_to_done`, ни одно существующее ожидание не меняется):

```python
@pytest.fixture
def imported_fixture_estimate(
    committing_client, committing_db, committing_factories, stub_with_fixture
) -> int:
    """Загруженная fixture-смета; отдаёт `estimate_id`."""
    contract = committing_factories.ContractFactory.create()
    committing_db.commit()
    job = upload(committing_client, contract.id, xlsx_stub("categories"))
    assert job["status"] == ImportJobStatus.done.value, job["error_text"]
    return job["estimate_id"]
```

и новый класс:

```python
class TestCategoryResolutionOnFixture:
    """Ф3 на закоммиченном файле: числа берутся из БД (спека §4.2)."""

    #: Общий хвост: только строки этой сметы.
    _SCOPE = (
        "from position_items p "
        "join proposals pr on pr.id = p.proposal_id "
        "join lots l on l.id = pr.lot_id "
        "where l.estimate_id = :eid"
    )

    def test_chapters_are_split_as_measured(self, committing_db, imported_fixture_estimate):
        counts = committing_db.execute(
            sa.text(
                "select "
                " count(*) filter (where p.is_chapter and p.smr_article_raw is not null "
                "                  and p.work_category_id is not null) as own, "
                " count(*) filter (where p.is_chapter and p.smr_article_raw is null "
                "                  and p.work_category_id is not null) as inherited, "
                " count(*) filter (where p.is_chapter "
                "                  and p.work_category_id is null) as unassigned, "
                " count(*) filter (where p.is_chapter and p.smr_article_raw is not null "
                "                  and p.work_category_id is null) as unreadable "
                + self._SCOPE
            ),
            {"eid": imported_fixture_estimate},
        ).one()
        assert counts.own == FIXTURE_CHAPTERS_OWN
        assert counts.inherited == FIXTURE_CHAPTERS_INHERITED
        assert counts.unassigned == FIXTURE_CHAPTERS_UNASSIGNED
        # Неизвестных кодов в файле нет — все 222 кода нашлись в справочнике.
        assert counts.unreadable == 0

    def test_every_work_row_is_attached_to_a_chapter(
        self, committing_db, imported_fixture_estimate
    ):
        """Строк вне структуры в этом файле нет, значит родитель есть у каждой позиции."""
        orphans = committing_db.execute(
            sa.text(
                "select count(*) " + self._SCOPE
                + " and not p.is_chapter and p.chapter_item_id is null"
            ),
            {"eid": imported_fixture_estimate},
        ).scalar_one()
        assert orphans == 0

    def test_unassigned_positions_match_the_measurement(
        self, committing_db, imported_fixture_estimate
    ):
        """Настоящий пробел: «14 SHELL & CORE» и «15 Рабочая документация» (спека §1.2)."""
        unassigned = committing_db.execute(
            sa.text(
                "select count(*) from position_items p "
                "join position_items c on c.id = p.chapter_item_id "
                "join proposals pr on pr.id = p.proposal_id "
                "join lots l on l.id = pr.lot_id "
                "where l.estimate_id = :eid and not p.is_chapter "
                "and c.work_category_id is null"
            ),
            {"eid": imported_fixture_estimate},
        ).scalar_one()
        assert unassigned == FIXTURE_POSITIONS_UNASSIGNED

    def test_parent_is_a_chapter_of_the_same_proposal_standing_earlier_in_the_file(
        self, committing_db, imported_fixture_estimate
    ):
        """Независимая проверка: эталон — порядок ключей файла, а не вывод резолвера.

        Слой 5 инсайта verifying-guards: если ожидание выводится из того же места,
        которое ломает контрпример, тест сравнивает величину с самой собой. Здесь
        ожидание берётся из `position_key_in_proposal` — его пишет импорт из ключей
        парсера, а не резолвер.
        """
        broken = committing_db.execute(
            sa.text(
                "select count(*) from position_items p "
                "join position_items c on c.id = p.chapter_item_id "
                "join proposals pr on pr.id = p.proposal_id "
                "join lots l on l.id = pr.lot_id "
                "where l.estimate_id = :eid and ("
                "  not c.is_chapter "
                "  or c.proposal_id <> p.proposal_id "
                "  or (c.position_key_in_proposal)::int >= (p.position_key_in_proposal)::int)"
            ),
            {"eid": imported_fixture_estimate},
        ).scalar_one()
        assert broken == 0
        # Ноль не должен быть вакуозным: ссылки в смете действительно есть.
        attached = committing_db.execute(
            sa.text("select count(*) " + self._SCOPE + " and p.chapter_item_id is not null"),
            {"eid": imported_fixture_estimate},
        ).scalar_one()
        assert attached == FIXTURE_POSITIONS - FIXTURE_CHAPTERS_TOP_LEVEL
```

`FIXTURE_CHAPTERS_TOP_LEVEL = 16` взято из гистограммы глубин пробника гейта 1 и **подтверждается прогоном** на шаге 2. Если прогон даст другое число — разбираться, а не подгонять константу: единственный законный источник строк без родителя в этом файле — верхний уровень дерева, потому что строк вне структуры в нём нет (`orphans == 0` проверяется отдельным тестом выше).

- [ ] **Step 2: Прогнать — убедиться, что падает по числам**

```
cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" \
  uv run pytest tests/integration/test_import_fixture_e2e.py -v
```

Ожидание: `test_measured_counts_add_up_to_the_chapter_total` зелёный сразу — он про константы и БД не трогает; четыре теста класса — **зелёные** после Task 4 (резолвер уже подключён к конвейеру). Красный на этом шаге означает расхождение с замером: 222/485/39/38 или 16 — это **дефект резолва либо замера**, а не повод обновить константу. Сначала выяснить, откуда разница, и только потом решать, что менять.

Отдельно проверить, что прогон не оказался пустым: `0 skipped` и в выводе видны имена всех пяти тестов. `stub_with_fixture` пропускает модуль, если `fixtures/gp_estimate_fixture.xlsx` не найден, а `skipped` читается как «команда отработала» ([silent-test-runs.md](../../insights/silent-test-runs.md)).

- [ ] **Step 3: Прогнать весь backend и замерить прирост**

```
cd backend && uv run pytest --collect-only -q | tail -3
cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest -q
```

Прирост числа собранных тестов **замерить, а не вывести арифметикой**: `pytest --collect-only -q` на `c980274` и на HEAD, разница обязана совпасть с числом фактически добавленных тестов (посчитать их по диффу). `6 skipped` — те же публичные endpoint'ы `test_auth_coverage.py`, к фиче отношения не имеют; любой седьмой `skipped` разбирать.

- [ ] **Step 4: Коммит**

```bash
git add backend/tests/integration/test_import_fixture_e2e.py
git commit -m "test(import): резолв статьи на fixture — 222/485/39/38 и независимая проверка вложенности"
```

---

### Task 6: негативные проверки, соответствие требований и финал

**Files:**
- Create: `docs/devlog/2026-08-06-category-resolution.md`
- Modify: `docs/phase7-brief.md` (правка факта §2 и теста §8 про «4.3»)

`docs/phase7-frame.md` **не правится**: врезка «принята, дата» добавляется в рамку при приёмке **фазы**, а не фичи (`AGENTS.md` §9.2).

**Interfaces:**
- Consumes: всё реализованное.
- Produces: devlog, PR.

- [ ] **Step 1: Негативная проверка снятием защиты — пять защит**

Делает **оркестратор лично, не исполнитель**: здесь легко получить ложно-зелёный результат. По протоколу [verifying-guards.md](../../insights/verifying-guards.md) целиком.

Для каждой из четырёх защит: (1) контрольный прогон целевого теста на целом коде — **зелёный**; (2) снятие с печатью `grep -c` по внесённому маркеру и sha256 файла; (3) прогон — **красный**, записать сколько именно упало; (4) восстановление из побайтовой копии и сверка sha256.

| Защита | Как снять | Целевой тест |
|---|---|---|
| валидатор D по неразбираемому номеру | `_unparsable_numbers` возвращает `[]` | `test_unparsable_chapter_number_disables_the_whole_proposal` |
| валидатор D по конфликту классификации | `_structural_conflicts` возвращает `[]` | `test_numeric_zero_in_the_chapter_number_disables_structure` |
| «утверждение файла сильнее наследования» | в `_article_for` при неизвестном коде вернуть `(parent.category if parent else None, "inherited")` | `test_subtree_of_an_unknown_code_stays_unassigned` |
| вытеснение по глубине | условие цикла → `while stack and stack[-1].depth > depth` | `test_chapter_of_depth_n_evicts_everything_at_depth_n_or_deeper` |
| привязка строки вне структуры к NULL | в `_resolve_stack` ветке OUTSIDE вернуть родителя из стека | `test_row_outside_structure_is_not_attached` (юнит и интеграционный) |

Две первые причины D — **разные пути** одного отказа; снятие одной ничего не говорит о другой (слой 6 инсайта), поэтому проверяются обе.

- [ ] **Step 2: Соответствие «требование спеки → тест»**

Пройти по тексту спеки требование за требованием (§2.1–§2.10) и построить таблицу «требование → тест, который упадёт при его нарушении». Требование без исполнителя либо получает тест, либо **объявляется границей** в devlog. Молчаливого третьего варианта нет ([replaying-new-rules.md](../../insights/replaying-new-rules.md), слой 3).

- [ ] **Step 3: Правка факта в брифе**

В `docs/phase7-brief.md` §2 заменить утверждение про «4.3 Металлоконструкции» как настоящий пробел: раздел наследует статью предка «4», а настоящий пробел с деньгами — `14 SHELL & CORE` и `15 Рабочая документация` в 449-ТУ и fixture. В §8 брифа заменить тест «пробел 4.3 → нераспределённое» соответственно. Правка вносится, а не планируется (спека §7).

- [ ] **Step 4: `just ci` целиком — ДО пуша**

```
just ci
```

Ожидание: `uv lock --check`, ruff, `db-test-check` (дрейфа нет), backend pytest, eslint, `tsc -b --noEmit`, vitest 175, `OK: все проверки прошли`, exit 0. Абсолютные времена между прогонами не сравнивать — они меряют и загрузку машины.

- [ ] **Step 5: Круговой рейс миграции — прогнать, а не объявить**

```
cd backend && uv run alembic downgrade base && uv run alembic upgrade head
just db-test-check
```

- [ ] **Step 6: Devlog**

Создать `docs/devlog/2026-08-06-category-resolution.md`: что сделано по задачам; замеры (собранные тесты до/после по `--collect-only`, числа fixture, времена `just ci`); что именно покраснело при каждом снятии защиты; таблица «требование → тест» и названные границы; отступления от плана с причинами; хвосты (в т.ч. обязательство Ф4 и то, что backfill старых смет не делается).

- [ ] **Step 7: Свежая заливка на стенде — после подтверждения команды**

Стенд чистится целиком, а не перезаливается через `replace=true`. **Список удаляемых БД и точную команду показать пользователю и получить подтверждение непосредственно перед запуском.** Разрешение касается только данных development-стендов и не распространяется на репозиторий и файлы проекта. После заливки — сверить распределение по статьям с §1.1 и записать в devlog.

- [ ] **Step 8: Коммит и PR**

```bash
git add docs/
git commit -m "docs(devlog): Ф3 — резолв статьи СМР, замеры и негативные проверки"
git push -u origin feat/category-resolution
gh pr create --title "Ф3: импорт — резолв статьи СМР стеком по файлу (миграция 0006)" --body-file docs/devlog/2026-08-06-category-resolution.md
```

Тело PR берётся из devlog'а (`--body-file`), чтобы не заводить третье описание одной работы. В шапку devlog'а — ссылки на [рамку фазы](../../phase7-frame.md), [спеку](../specs/2026-08-06-category-resolution-design.md) и этот план, как это сделано в devlog Ф2.
