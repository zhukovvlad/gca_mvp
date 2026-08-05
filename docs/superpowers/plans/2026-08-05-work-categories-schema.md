# Ф1: классификатор видов работ (миграция 0005 и сид) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Завести в БД справочник статей классификатора компании: таблица `work_categories` с 362 статьями из корпоративного шаблона, деревом и инвариантами, которые нельзя обойти.

**Architecture:** Одна миграция 0005 + ORM-модель. Сид — литерал в миграции; `parent_id` не берётся из литерала, а выводится из самого кода одним UPDATE, после чего миграция **громко отказывает**, если число строк не совпало или появились сироты. `is_bucket` — generated column, поэтому соврать в него нельзя. Ни в одном SQL-выражении нет бэкслэшей (точка задана как `[.]`) и локале-зависимых классов — оба решения приняты по замерам, см. спеку §2.1.1 и §2.1.2.

**Tech Stack:** PostgreSQL 16 (pgvector-образ в CI), Alembic, SQLAlchemy 2.x Core/ORM, pytest (integration), uv, just.

**Спека:** [2026-08-05-work-categories-schema-design.md](../specs/2026-08-05-work-categories-schema-design.md) — источник правды. Двенадцать замеров в §7; в план вынесены только выводы.

## Global Constraints

- Ветка: `feat/work-categories-schema` (спека уже закоммичена в неё).
- ruff `line-length = 120`, `target py312`; комментарии и docstring — по-русски, как в окружающем коде.
- **Ни одного бэкслэша в SQL-выражениях.** Точка-литерал — `[.]`. Причина — спека §2.1.1 (замер: при `standard_conforming_strings=off` обычный литерал молча принимает код `1x2`, а тест этого не поймает).
- **Никаких локале-зависимых классов** (`[[:space:]]` и родня): инвариант обязан вести себя одинаково при любом `LC_CTYPE` (спека §2.1.2).
- Миграция неизменна во времени: значения — литералами в её файле, **без** импортов из `crud`/`models` (прецедент 0004; антипрецедент — 0001).
- Прикладной код не трогаем: только миграция, `models.py` и тесты.
- Все команды бэкенда — из `backend/` через `uv run`; системный python не вызывать.
- Integration-файлы обязаны иметь `pytestmark = pytest.mark.integration` (в `test_schema_constraints.py` он уже на уровне модуля).
- Перед пушем — `just ci`.

## Состояние на момент написания плана

- Ветка создана, спека прошла четыре круга внешнего ревью и гейт 2 (коммиты `a57af1e`…`d84cc69`).
- Последняя миграция в дереве — `0004` (`2026_08_04_0004-app_settings.py`).
- `models.py` уже импортирует всё нужное: `BigInteger, Boolean, CheckConstraint, Column, Computed, ForeignKey, Integer, Text, UniqueConstraint`; есть хелперы `_created_at()` / `_updated_at()`.
- `backend/tests/integration/test_schema_constraints.py` содержит хелпер `rejected(session, contains=...)`, который ждёт **`IntegrityError`**, и класс-образец `TestAppSettings`.
- conftest накатывает `alembic upgrade head` один раз за сессию на `TEST_DATABASE_URL`, поэтому тесты видят сид.
- `samples/Шаблон.xlsx` на месте (не коммитится).

## File Structure

- `backend/alembic/versions/2026_08_05_0005-work_categories.py` — **создаётся**: таблица, литерал сида, вывод родителя, отказы.
- `backend/models.py` — **правится**: класс `WorkCategory` в конец файла (перед служебными таблицами, порядок как в остальном файле — доменные сущности рядом).
- `backend/tests/integration/test_schema_constraints.py` — **правится**: класс `TestWorkCategoriesSchema` (инварианты) и `TestWorkCategoriesSeed` (данные).
- `docs/devlog/2026-08-05-work-categories-schema.md` — **создаётся** в Task 3.

---

### Task 1: таблица, ORM-модель и инварианты

**Files:**
- Create: `backend/alembic/versions/2026_08_05_0005-work_categories.py`
- Modify: `backend/models.py`
- Test: `backend/tests/integration/test_schema_constraints.py`

**Interfaces:**
- Produces: таблица `work_categories` (колонки `id, code, title, parent_id, is_bucket, sort_order, created_at, updated_at`); констрейнты `uq_work_categories_code`, `uq_work_categories_sort_order`, `ck_work_categories_code`, `ck_work_categories_not_self_parent`, `ck_work_categories_title_not_blank`; ORM-класс `models.WorkCategory`. На это опирается Task 2 и все фичи фазы 7.

- [ ] **Step 1: Написать падающие тесты инвариантов**

В конец `backend/tests/integration/test_schema_constraints.py`. Обратить внимание: для generated column нужен **`ProgrammingError`**, а не хелпер `rejected()` (замер — спека §7 факт 7).

```python
class TestWorkCategoriesSchema:
    """Инварианты справочника статей (спека Ф1 §2.1).

    Смысл — в непредставимости негодного состояния: справочник курируется людьми
    и будет правиться через админку, поэтому запрет живёт в БД, а не в Python.
    """

    def test_code_must_look_like_a_dotted_number(self, db_session):
        for bad in ("abc", "1..2", "1.", "", "6.6 ", ".1", "1x2", "1,2"):
            with rejected(db_session, contains="ck_work_categories_code"):
                db_session.execute(
                    sa.text(
                        "insert into work_categories (code, title, sort_order) "
                        "values (:code, 'x', 999000)"
                    ),
                    {"code": bad},
                )

    # Замеренные определения из PostgreSQL. Собирать их по памяти нельзя: функция
    # переформатирует выражение — добавляет `::text`, свои скобки и печатает LIKE
    # как оператор `~~`.
    DB_CHECKS = {
        "ck_work_categories_code": "CHECK ((code ~ '^[0-9]+([.][0-9]+)*$'::text))",
        "ck_work_categories_not_self_parent": "CHECK (((parent_id IS NULL) OR (parent_id <> id)))",
        "ck_work_categories_title_not_blank": (
            "CHECK ((btrim(title, ((((' '::text || chr(9)) || chr(10)) || chr(13)) || chr(160)))"
            " <> ''::text))"
        ),
    }
    DB_IS_BUCKET = "((code = '99'::text) OR (code ~~ '%.99'::text))"

    def test_database_holds_the_declared_expressions(self, db_session):
        """Что реально легло в БД: все три CHECK и generated-выражение.

        Сравнение словарём целиком, а не по одному ключу: так видно и подмену
        выражения, и появление лишнего CHECK, и исчезновение нужного. Канарейка
        против возврата экранирования (§2.1.1) — первая строка этого словаря.
        """
        rows = dict(
            db_session.execute(
                sa.text(
                    "select conname, pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid = 'work_categories'::regclass and contype = 'c'"
                )
            ).all()
        )
        assert rows == self.DB_CHECKS
        generated = db_session.execute(
            sa.text(
                "select generation_expression from information_schema.columns "
                "where table_name = 'work_categories' and column_name = 'is_bucket'"
            )
        ).scalar_one()
        assert generated == self.DB_IS_BUCKET

    def test_blank_title_rejected_the_same_way_on_any_locale(self, db_session):
        """Набор символов задан кодовыми точками, поэтому не зависит от LC_CTYPE."""
        for blank in ("", " ", "\t", "\n", "\r", "\xa0", " \t\xa0 "):
            with rejected(db_session, contains="ck_work_categories_title_not_blank"):
                db_session.execute(
                    sa.text(
                        "insert into work_categories (code, title, sort_order) "
                        "values ('900', :title, 999001)"
                    ),
                    {"title": blank},
                )

    def test_zero_width_space_title_is_an_accepted_boundary(self, db_session):
        """U+200B в набор не входит — граница явная и детерминированная (§2.1.2).

        Тест сторожит саму границу: если её решат закрыть, он покажет, что
        поведение изменилось осознанно.
        """
        with db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "insert into work_categories (code, title, sort_order) "
                    "values ('901', :title, 999002)"
                ),
                {"title": "\u200b"},  # именно escape, а не невидимый символ в исходнике
            )

    def test_is_bucket_cannot_be_written(self, db_session):
        """generated column: ложь не отвергается, а невозможна.

        Класс ошибки — ProgrammingError (sqlstate 428C9), не IntegrityError,
        поэтому хелпер rejected() здесь не годится (спека §7 факт 7).
        """
        with pytest.raises(ProgrammingError, match="non-DEFAULT value"), db_session.begin_nested():
            db_session.execute(
                sa.text(
                    "insert into work_categories (code, title, sort_order, is_bucket) "
                    "values ('902', 'x', 999003, true)"
                )
            )

    def test_is_bucket_cannot_be_updated(self, db_session):
        row_id = _make_category(db_session, "910", 999010)
        with pytest.raises(ProgrammingError, match="can only be updated to DEFAULT"), db_session.begin_nested():
            db_session.execute(
                sa.text("update work_categories set is_bucket = true where id = :id"),
                {"id": row_id},
            )

    def test_duplicate_code_rejected(self, db_session):
        _make_category(db_session, "911", 999011)
        with rejected(db_session, contains="uq_work_categories_code"):
            db_session.execute(
                sa.text(
                    "insert into work_categories (code, title, sort_order) "
                    "values ('911', 'дубль', 999012)"
                )
            )

    def test_duplicate_sort_order_rejected(self, db_session):
        _make_category(db_session, "912", 999013)
        with rejected(db_session, contains="uq_work_categories_sort_order"):
            db_session.execute(
                sa.text(
                    "insert into work_categories (code, title, sort_order) "
                    "values ('913', 'x', 999013)"
                )
            )

    def test_row_cannot_be_its_own_parent(self, db_session):
        row_id = _make_category(db_session, "914", 999014)
        with rejected(db_session, contains="ck_work_categories_not_self_parent"):
            db_session.execute(
                sa.text("update work_categories set parent_id = :id where id = :id"),
                {"id": row_id},
            )

    def test_parent_with_children_cannot_be_deleted(self, db_session):
        parent_id = _make_category(db_session, "915", 999015)
        _make_category(db_session, "915.1", 999016, parent_id=parent_id)
        with rejected(db_session, contains="work_categories_parent_id_fkey"):
            db_session.execute(
                sa.text("delete from work_categories where id = :id"), {"id": parent_id}
            )

    def test_orm_declares_the_same_expressions(self):
        """Вторая сторона парности: что объявлено в models.py.

        Замерено: `alembic check` расхождение CHECK- и Computed-выражений НЕ ловит —
        autogenerate их не сравнивает (на Computed выдаёт лишь UserWarning
        «cannot be modified», а предупреждение прогон не роняет). Поэтому ORM
        сверяется здесь, а БД — тестом выше; вместе они закрывают оба направления:
        правка в миграции ломает первый, правка в модели — второй.
        """
        checks = {
            c.name: str(c.sqltext)
            for c in WorkCategory.__table__.constraints
            if isinstance(c, CheckConstraint)
        }
        assert checks["ck_work_categories_code"] == "code ~ '^[0-9]+([.][0-9]+)*$'"
        assert checks["ck_work_categories_not_self_parent"] == "parent_id IS NULL OR parent_id <> id"
        assert checks["ck_work_categories_title_not_blank"] == (
            "btrim(title, ' ' || chr(9) || chr(10) || chr(13) || chr(160)) <> ''"
        )
        computed = WorkCategory.__table__.c.is_bucket.computed
        assert str(computed.sqltext) == "code = '99' OR code LIKE '%.99'"
        assert computed.persisted is True
```

Хелпер рядом с `rejected()` в начале файла — схемные тесты **не должны опираться на сид**, иначе Task 1 нельзя принять отдельно от Task 2:

```python
def _make_category(session, code: str, sort_order: int, parent_id: int | None = None) -> int:
    """Создаёт статью и возвращает её id. Коды 9xx заведомо вне шаблона."""
    return session.execute(
        sa.text(
            "insert into work_categories (code, title, sort_order, parent_id) "
            "values (:code, 'Тестовая статья', :sort_order, :parent_id) returning id"
        ),
        {"code": code, "sort_order": sort_order, "parent_id": parent_id},
    ).scalar_one()
```

В начало файла добавить импорты (рядом с существующими):

```python
from sqlalchemy import CheckConstraint
from sqlalchemy.exc import IntegrityError, ProgrammingError

from models import WorkCategory
```

- [ ] **Step 2: Прогнать — убедиться, что падают по причине «нет таблицы»**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_schema_constraints.py -k WorkCategories -q`
Expected: FAIL, в тексте — `relation "work_categories" does not exist`.

- [ ] **Step 3: Создать миграцию (только таблица, без сида)**

`backend/alembic/versions/2026_08_05_0005-work_categories.py`. Docstring обязателен и должен объяснять **почему** так, а не что (стиль 0003/0004): generated column вместо флага, `[.]` вместо `\.`, явный набор символов вместо `[[:space:]]`.

```python
"""Классификатор видов работ компании: справочник статей СМР.

Паспорт проекта (фаза 7) агрегирует деньги по крупным статьям фиксированного
классификатора, а классификатора в БД не было. Канонический источник — корпоративный
шаблон (`samples/Шаблон.xlsx`, в репозиторий не попадает); коды и названия статей
коммитить можно — это справочник, а не коммерческие данные.

**`is_bucket` — generated column, а не хранимый флаг.** Корзина полностью
определяется кодом, поэтому запись в колонку запрещена самой БД: соврать нельзя
даже правкой мимо приложения. Иначе через будущую админку появилась бы «корзина
5.1», и подсветка в паспорте начала бы лгать. Прецедент формы — `fts_vector` в 0002.

**В выражениях нет ни одного бэкслэша: точка задана как `[.]`.** Замер на PG 16.14:
при `standard_conforming_strings = off` обычный литерал `'\\.'` теряет
экранирование, регулярка превращается в `^[0-9]+(.[0-9]+)*$` — и код `1x2`
принимается МОЛЧА. Хуже того, поймать такую подмену тестом нельзя: при дефолтном
`on` определения обычного литерала и `E'…'` в БД побайтово равны (PostgreSQL не
хранит лексическую форму). `[.]` снимает проблему целиком — терять нечего.

**Непустое название проверяется явным набором символов, а не `[[:space:]]`.**
Покрытие POSIX-класса зависит от `LC_CTYPE` базы: замер на двух базах показал, что
на ctype `en-US` название из неразрывных пробелов отвергается, а на ctype `C` —
принимается. Одна схема с двумя ответами — это не инвариант. Набор задан кодовыми
точками через `chr()`, поэтому вердикт одинаков всюду. U+2009/U+3000/U+200B в набор
не входят — явно принятая граница (обоснование целиком — спека Ф1 §2.1.2).

Сид — 362 строки литералом (следующая ревизия правил не меняет: миграция неизменна
во времени, поэтому значения здесь, а не импортом из crud, как в 0001).

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-05
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Выражения дублируют models.WorkCategory намеренно: миграция обязана быть
# неизменной во времени (то же правило, что у литералов в 0004). Расхождение ловит
# test_schema_constraints.py::TestWorkCategoriesSchema.
CODE_REGEX = "^[0-9]+([.][0-9]+)*$"
IS_BUCKET_EXPRESSION = "code = '99' OR code LIKE '%.99'"
TITLE_BLANK_CHARS = "' ' || chr(9) || chr(10) || chr(13) || chr(160)"


def upgrade() -> None:
    op.create_table(
        "work_categories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "parent_id",
            sa.BigInteger(),
            sa.ForeignKey("work_categories.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "is_bucket",
            sa.Boolean(),
            sa.Computed(IS_BUCKET_EXPRESSION, persisted=True),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("code", name="uq_work_categories_code"),
        sa.UniqueConstraint("sort_order", name="uq_work_categories_sort_order"),
        sa.CheckConstraint(f"code ~ '{CODE_REGEX}'", name="ck_work_categories_code"),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="ck_work_categories_not_self_parent"
        ),
        sa.CheckConstraint(
            f"btrim(title, {TITLE_BLANK_CHARS}) <> ''", name="ck_work_categories_title_not_blank"
        ),
    )


def downgrade() -> None:
    op.drop_table("work_categories")
```

- [ ] **Step 4: Добавить ORM-модель**

В `backend/models.py`, рядом с прочими доменными сущностями. Выражения повторяют миграцию посимвольно; расхождение ловит **только** `test_orm_declares_the_same_expressions` из Step 1 — `alembic check` для CHECK и `Computed` бесполезен (замерено).

```python
WORK_CATEGORY_CODE_REGEX = "^[0-9]+([.][0-9]+)*$"
WORK_CATEGORY_IS_BUCKET_EXPRESSION = "code = '99' OR code LIKE '%.99'"
WORK_CATEGORY_TITLE_BLANK_CHARS = "' ' || chr(9) || chr(10) || chr(13) || chr(160)"


class WorkCategory(Base):
    """Статья классификатора видов работ компании (фаза 7, спека Ф1).

    Дерево держится на `parent_id`; `is_bucket` — производное от кода, писать в него
    нельзя (generated column). Уровень статьи не хранится: он выводится из дерева.
    """

    __tablename__ = "work_categories"

    id = Column(BigInteger, primary_key=True)
    code = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    parent_id = Column(
        BigInteger, ForeignKey("work_categories.id", ondelete="RESTRICT"), nullable=True
    )
    is_bucket = Column(
        Boolean, Computed(WORK_CATEGORY_IS_BUCKET_EXPRESSION, persisted=True), nullable=False
    )
    sort_order = Column(Integer, nullable=False)
    created_at = _created_at()
    updated_at = _updated_at()

    __table_args__ = (
        UniqueConstraint("code", name="uq_work_categories_code"),
        UniqueConstraint("sort_order", name="uq_work_categories_sort_order"),
        CheckConstraint(f"code ~ '{WORK_CATEGORY_CODE_REGEX}'", name="ck_work_categories_code"),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="ck_work_categories_not_self_parent"
        ),
        CheckConstraint(
            f"btrim(title, {WORK_CATEGORY_TITLE_BLANK_CHARS}) <> ''",
            name="ck_work_categories_title_not_blank",
        ),
    )
```

- [ ] **Step 5: Накатить миграцию и прогнать тесты**

Run: `just db-test-migrate`
Expected: `Running upgrade 0004 -> 0005`.

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_schema_constraints.py -k WorkCategories -q`
Expected: **все тесты класса PASS**. Схемные тесты создают свои строки через `_make_category` (коды `9xx` заведомо вне шаблона), поэтому от сида не зависят и Task 1 принимается отдельно от Task 2 — красных тестов в коммите не остаётся.

- [ ] **Step 6: Проверить отсутствие дрейфа ORM/БД**

Run: `just db-test-check`
Expected: `alembic check` без изменений — он сторожит **состав** колонок, типы и индексы. **Расхождение CHECK/Computed он не увидит** (замер: при подмене обоих выражений autogenerate вернул пустой diff, ограничившись `UserWarning` про Computed) — за это отвечает `test_orm_declares_the_same_expressions`. Допустимо увидеть здесь тот же UserWarning; это не отказ.

- [ ] **Step 7: Коммит**

```bash
git add backend/alembic/versions/2026_08_05_0005-work_categories.py backend/models.py backend/tests/integration/test_schema_constraints.py
git commit -m "feat(db): таблица work_categories с непредставимыми негодными состояниями"
```

---

### Task 2: сид, вывод родителя из кода и громкий отказ

**Files:**
- Modify: `backend/alembic/versions/2026_08_05_0005-work_categories.py`
- Test: `backend/tests/integration/test_schema_constraints.py`

**Interfaces:**
- Consumes: таблицу из Task 1.
- Produces: 362 статьи с деревом. Тесты Task 1 от них **не зависят** (создают свои строки), так что эта задача добавляет только новый класс тестов.

- [ ] **Step 1: Сгенерировать литерал из шаблона**

Одноразовый скрипт печатает готовые к вставке строки. Путь **фиксированный и вне репозитория** — `/c/tmp/gca-f1/` (в Git Bash это `C:\tmp\gca-f1`): переменные оболочки между вызовами не сохраняются, а Step 4 обязан прочитать тот же файл.

**Сначала preflight и создание каталога, только потом запись файла.** Фиксированный путь может остаться от другой сессии, а перезаписывать чужое и потом сносить рекурсивно нельзя:

```bash
test ! -e /c/tmp/gca-f1 || { echo "/c/tmp/gca-f1 уже существует — разобраться вручную, не перезаписывать"; exit 1; }
mkdir -p /c/tmp/gca-f1
```

Дальше записать скрипт в `/c/tmp/gca-f1/gen_seed.py` **инструментом записи файлов** (`Write` / `apply_patch`), а не heredoc-ом. Путь к шаблону принимает аргументом, чтобы не зависеть от текущего каталога:

```python
# gen_seed.py — запускается один раз, в репозиторий не коммитится
import re
import sys

from openpyxl import load_workbook

ws = load_workbook(sys.argv[1], data_only=True, read_only=True)["Лист1"]
rows = []
for row in ws.iter_rows(values_only=True):
    raw = str(row[0]).strip()
    code_raw, title = re.match(r"^\((.+?)\)\s*(.*)$", raw).groups()
    code = re.sub(r"[\s.]+$", "", code_raw.strip()).replace(" ", "")  # «6.6 .» -> «6.6»
    assert re.fullmatch(r"\d+(\.\d+)*", code), raw
    title = title.strip()
    assert title, raw
    rows.append((code, title))

assert len(rows) == 362, len(rows)                      # снапшот шаблона на 2026-08-05
assert len({c for c, _ in rows}) == len(rows)            # инвариант: коды уникальны
for code, title in rows:
    print(f'    ({code!r}, {title!r}),')
```

Запуск — через `uv run` из `backend/`, как требует Global Constraints:
```bash
cd backend && uv run python /c/tmp/gca-f1/gen_seed.py ../samples/Шаблон.xlsx > /c/tmp/gca-f1/seed_rows.txt
wc -l /c/tmp/gca-f1/seed_rows.txt
```
Expected: 362 строки; если `assert` про 362 упал — шаблон обновился, и число в снапшоте надо пересмотреть осознанно (спека §6).

- [ ] **Step 2: Написать падающие тесты данных**

```python
class TestWorkCategoriesSeed:
    """Сид классификатора: 362 статьи шаблона и дерево, выведенное из кодов."""

    def test_whole_template_is_seeded(self, db_session):
        count = db_session.execute(sa.select(sa.func.count()).select_from(WorkCategory)).scalar_one()
        assert count == 362

    def test_roots_are_exactly_the_codes_without_a_dot(self, db_session):
        roots = db_session.execute(
            sa.text("select code from work_categories where parent_id is null")
        ).scalars().all()
        assert len(roots) == 21
        assert [c for c in roots if "." in c] == []

    def test_every_dotted_code_has_its_prefix_as_parent(self, db_session):
        """Страховка substring-выражения: искажение ломает свойство на 341 строке."""
        rows = db_session.execute(
            sa.text(
                "select c.code, p.code from work_categories c "
                "left join work_categories p on p.id = c.parent_id "
                "where c.code like '%.%'"
            )
        ).all()
        assert len(rows) == 341
        assert [(child, parent) for child, parent in rows if child.rsplit(".", 1)[0] != parent] == []

    def test_buckets_are_derived_from_the_code(self, db_session):
        buckets = db_session.execute(
            sa.text("select code from work_categories where is_bucket")
        ).scalars().all()
        assert len(buckets) == 22
        assert "99" in buckets
        assert [c for c in buckets if not (c == "99" or c.endswith(".99"))] == []

    def test_child_of_a_bucket_is_not_a_bucket(self, db_session):
        """11.99.2 — реальная работа под корзиной 11.99, а не корзина."""
        rows = dict(
            db_session.execute(
                sa.text(
                    "select code, is_bucket from work_categories "
                    "where code in ('11.99', '11.99.2')"
                )
            ).all()
        )
        assert rows == {"11.99": True, "11.99.2": False}

    def test_sort_order_follows_the_template(self, db_session):
        orders = db_session.execute(
            sa.text("select sort_order from work_categories order by sort_order")
        ).scalars().all()
        assert orders == [(i + 1) * 10 for i in range(362)]

    def test_titles_come_from_the_template_as_is(self, db_session):
        title = db_session.execute(
            sa.text("select title from work_categories where code = '1'")
        ).scalar_one()
        assert title == "Подготовительные работы, содержание площадки"
```

Импорт `WorkCategory` добавить к существующим импортам моделей в файле.

- [ ] **Step 3: Прогнать — убедиться, что падают на пустой таблице**

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_schema_constraints.py -k WorkCategoriesSeed -q`
Expected: FAIL, `assert 0 == 362`.

- [ ] **Step 4: Вписать сид в миграцию**

Модульная константа после `TITLE_BLANK_CHARS` — вставить содержимое `/c/tmp/gca-f1/seed_rows.txt`:

```python
# 362 статьи корпоративного шаблона в его порядке. Литералом, а не импортом:
# миграция обязана быть неизменной во времени, а справочник уедет в админку.
WORK_CATEGORIES_SEED: tuple[tuple[str, str], ...] = (
    ("1", "Подготовительные работы, содержание площадки"),
    # … 361 строка из /c/tmp/gca-f1/seed_rows.txt …
)
```

В конец `upgrade()` — вставка, вывод родителя и **отказы**:

```python
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO work_categories (code, title, sort_order) "
            "VALUES (:code, :title, :sort_order)"
        ),
        [
            {"code": code, "title": title, "sort_order": (index + 1) * 10}
            for index, (code, title) in enumerate(WORK_CATEGORIES_SEED)
        ],
    )

    inserted = conn.execute(sa.text("SELECT count(*) FROM work_categories")).scalar_one()
    if inserted != len(WORK_CATEGORIES_SEED):
        raise RuntimeError(
            f"сид усечён: в таблице {inserted} строк из {len(WORK_CATEGORIES_SEED)}"
        )

    # Родитель выводится ИЗ САМОГО КОДА, а не из литерала: тогда дерево согласовано
    # по построению, и порядок строк шаблона перестаёт быть частью контракта.
    conn.execute(
        sa.text(
            "UPDATE work_categories c SET parent_id = p.id FROM work_categories p "
            "WHERE p.code = substring(c.code from '^(.*)[.][^.]+$')"
        )
    )

    # Молчаливый NULL здесь означал бы ложный корень — статья повисла бы вне дерева,
    # и её деньги в паспорте ушли бы не в ту ветку. Отказываем громко, до коммита.
    orphans = conn.execute(
        sa.text(
            "SELECT code FROM work_categories "
            "WHERE code LIKE '%.%' AND parent_id IS NULL ORDER BY code"
        )
    ).scalars().all()
    if orphans:
        raise RuntimeError(
            "у статей нет прямого предка в справочнике: " + ", ".join(orphans)
        )
```

- [ ] **Step 5: Перекатить миграцию и прогнать все тесты класса**

Run:
```bash
cd backend && DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run alembic downgrade 0004
cd backend && DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run alembic upgrade head
```
Expected: `0004 -> 0005` без ошибок.

Литерал перенесён — убрать временные файлы **поимённо**, каталог снять нерекурсивно (если в нём осталось что-то ещё, `rmdir` откажет, и это правильный сигнал):
```bash
rm -f /c/tmp/gca-f1/gen_seed.py /c/tmp/gca-f1/seed_rows.txt
rmdir /c/tmp/gca-f1
```

Run: `cd backend && TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_schema_constraints.py -k WorkCategories -q`
Expected: PASS все — и схемные (они самодостаточны с Task 1), и новые тесты сида.

- [ ] **Step 6: Проверить снятием защиты, что отказ на сиротах реально срабатывает**

Тест из Step 2 доказывает лишь, что на **правильном** литерале дерево верное. Что миграция умеет **кричать**, доказывается только порчей входа (правило трёх слоёв, `AGENTS.md` §11).

Временно удалить из `WORK_CATEGORIES_SEED` строку `("11.99", ...)`, оставив её детей `11.99.2`–`11.99.4`, и проверить, что вставка `assert`-а действительно есть в файле перед прогоном:

```bash
cd backend && grep -c "нет прямого предка" alembic/versions/2026_08_05_0005-work_categories.py   # ожидание: 1
cd backend && DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run alembic downgrade 0004
cd backend && DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run alembic upgrade head
```
Expected: миграция падает `RuntimeError: у статей нет прямого предка в справочнике: 11.99.2, 11.99.3, 11.99.4`. Таблицы после отката нет — проверить: `select to_regclass('work_categories')` → `NULL`.

**Граница второго отказа — зафиксировать, а не проверять.** Счётчик строк **не защищает почти ни от чего**, и это надо назвать прямо: `INSERT` без `ON CONFLICT` либо проходит целиком, либо бросает исключение до `SELECT count(*)`, так что «частично провалившейся вставки» в этом сценарии не существует. Он остаётся как предписанная спекой sanity-проверка: сработает, если таблица окажется непустой к моменту вставки или если кто-то позже сделает вставку условной (`ON CONFLICT DO NOTHING`). Полноту литерала и соответствие шаблону сторожит тест `test_whole_template_is_seeded` (362), а дерево — `test_every_dotted_code_has_its_prefix_as_parent`. Записать это разделение обязанностей в devlog, иначе следующий читатель припишет счётчику защиту, которой у него нет.

Восстановить удалённую строку `("11.99", …)`, перекатить миграцию (`downgrade 0004` → `upgrade head`), повторить прогон тестов — PASS.

- [ ] **Step 7: Коммит**

```bash
git add backend/alembic/versions/2026_08_05_0005-work_categories.py backend/tests/integration/test_schema_constraints.py
git commit -m "feat(db): сид классификатора — 362 статьи, дерево из кодов, громкий отказ на сиротах"
```

---

### Task 3: круговой рейс, полный прогон, devlog и PR

**Files:**
- Create: `docs/devlog/2026-08-05-work-categories-schema.md`

**Interfaces:**
- Consumes: результаты Task 1–2.

- [ ] **Step 1: Круговой рейс миграций**

Run:
```bash
cd backend && DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run alembic downgrade base
cd backend && DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run alembic upgrade head
```
Expected: оба направления без ошибок (требование DoD фазы, `docs/phase7-frame.md`).

- [ ] **Step 2: Проверить отсутствие дрейфа после сида**

Run: `just db-test-check`
Expected: без изменений.

- [ ] **Step 3: Полный прогон в форме CI**

Run: `just ci`
Expected: PASS. Записать длительность и число тестов (было 927 passed / 6 skipped до фичи).

- [ ] **Step 4: Написать devlog**

`docs/devlog/2026-08-05-work-categories-schema.md`. Обязательно: что сделано; **замеры** (число тестов до/после, длительность `just ci`, результат негативной проверки из Task 2 Step 6 — какое сообщение выдала миграция); граница счётчика строк из того же шага; отступления от плана, если были; хвосты (`alembic check` не входит в `just ci` — прогнан руками).

- [ ] **Step 5: Коммит devlog**

```bash
git add docs/devlog/2026-08-05-work-categories-schema.md
git commit -m "docs: devlog Ф1 — классификатор в БД"
```

- [ ] **Step 6: Push и PR**

```bash
git push -u origin feat/work-categories-schema
gh pr create --base main --title "Ф1: классификатор видов работ — таблица work_categories и сид" --body "$(cat <<'EOF'
## Что и зачем

Паспорт проекта (фаза 7) агрегирует деньги по крупным статьям фиксированного
классификатора компании — а классификатора в БД не было.

- Спека: `docs/superpowers/specs/2026-08-05-work-categories-schema-design.md`
- План: `docs/superpowers/plans/2026-08-05-work-categories-schema.md`
- Devlog: `docs/devlog/2026-08-05-work-categories-schema.md`
- Рамка фазы: `docs/phase7-frame.md`

## Состав

- Миграция 0005: таблица `work_categories`, 362 статьи литералом, дерево выводится
  из самих кодов одним UPDATE, миграция громко отказывает на сиротах.
- `is_bucket` — generated column: соврать в него нельзя даже правкой мимо приложения.
- Ни одного бэкслэша в SQL и никаких локале-зависимых классов — оба решения по замерам
  (спека §2.1.1, §2.1.2): при `standard_conforming_strings=off` обычный литерал молча
  принимал код `1x2`, а `[[:space:]]` давал разный вердикт на ctype `en-US` и `C`.
- ORM-модель и тесты: инварианты схемы + содержимое сида.

Прикладной код не тронут.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review плана

**Покрытие спеки:** §2.1 схема → Task 1 Step 3–4; §2.1.1 `[.]` → Task 1 Step 3 (константа `CODE_REGEX`) + тест-канарейка Step 1; §2.1.2 непустое название → Task 1 Step 3 (`TITLE_BLANK_CHARS`) + два теста (отказ и граница U+200B); §2.2 сид и вывод родителя → Task 2 Step 4; отказы → Task 2 Step 4 и проверка снятием защиты Step 6; §2.3 ORM-модель → Task 1 Step 4; §4 тесты → Task 1 Step 1 и Task 2 Step 2 (все пункты списка спеки представлены); §5 границы → по построению (ни API, ни колонок `position_items`); §6 генератор → Task 2 Step 1; §8 следствия → Task 3 Step 2 (`alembic check` руками).

**Пробел, найденный при сверке:** спека требует тест «дубль `sort_order` отвергается», а в первой редакции плана его не было — добавлен (`test_duplicate_sort_order_rejected`; как и остальные схемные тесты, создаёт свою строку с `sort_order = 999013`, от сида не зависит).

**Согласованность имён:** константы `CODE_REGEX` / `IS_BUCKET_EXPRESSION` / `TITLE_BLANK_CHARS` в миграции и их зеркала `WORK_CATEGORY_*` в `models.py` — разные имена намеренно (миграция не импортирует модели), но **выражения** должны совпадать посимвольно. Сторожит это `test_orm_declares_the_same_expressions`, а **не** `alembic check`: замерено, что при подмене CHECK- и Computed-выражений autogenerate возвращает пустой diff. Имена констрейнтов одинаковы в миграции, модели и тестах; `WorkCategory` — единственное имя ORM-класса.

**Задачи независимы.** Схемные тесты Task 1 создают свои строки (`_make_category`, коды `9xx` вне шаблона) и не опираются на сид, поэтому Task 1 коммитится полностью зелёным, а Task 2 добавляет только тесты содержимого. Первая редакция плана оставляла Task 1 красным и объявляла это ожидаемым — так делать нельзя: красный коммит нельзя принять, и он маскирует настоящие поломки.

---

## Исполнение в мультимодельной сессии

Роли на эту фичу зафиксированы пользователем: **Opus — оркестратор**,
**Sonnet/Haiku — исполнители**, **Fable — финальное ревью** перед мержем.

### Кто что делает

| Роль | Работа |
|---|---|
| Оркестратор (Opus) | читает спеку и план целиком; выдаёт задачи по одной; **сам** прогоняет проверки между задачами и коммитит; принимает решения при любом расхождении с планом |
| Исполнитель (Sonnet) | одна задача за вызов: пишет тесты/миграцию/модель по готовому коду плана, прогоняет указанные команды, отдаёт результат |
| Исполнитель (Haiku) | механика без решений: вставка 362 строк литерала, запуск генератора, точечные прогоны pytest |
| Финальное ревью (Fable) | после Task 3: диффы, devlog, вывод негативной проверки |

Оркестратор **не делегирует**: негативную проверку Task 2 Step 6 (снятие защиты —
там легко получить ложно-зелёный результат), решение при несовпадении ожидания и
любые правки спеки.

### Два запрета для исполнителей

1. **Не менять ожидания тестов.** Все ожидания в плане — из замеров на PG 16.14
   (спека §7, двенадцать фактов), включая нормализованные тексты
   `pg_get_constraintdef`. Если факт не совпал с ожиданием — **стоп и эскалация**,
   а не подгонка ожидания под вывод. Подгонка превратила бы тест в описание
   текущего поведения, каким бы оно ни было.
2. **Не пересматривать пять дизайн-решений**, каждое стоит на замере: `[.]` вместо
   `\.` (§2.1.1); явный набор символов вместо `[[:space:]]` (§2.1.2); `is_bucket`
   как generated column; сид литералом без промежуточного CSV; `parent_id`
   выводится UPDATE-ом, а не берётся из литерала. Возражение — через оркестратора,
   с фактом, а не с соображением.

### Где исполнитель споткнётся (замерено, не предположение)

- Запись в `is_bucket` даёт **`ProgrammingError`** (sqlstate `428C9`), а не
  `IntegrityError` — project-хелпер `rejected()` здесь не работает (спека §7 факт 7).
- `just db-test-check` **не увидит** расхождения CHECK/Computed между моделью и
  миграцией: autogenerate их не сравнивает, отдаёт пустой diff и лишь
  `UserWarning` про Computed. За это отвечает `test_orm_declares_the_same_expressions`.
- Точечный прогон integration-тестов требует `TEST_DATABASE_URL`, иначе они молча
  пропускаются; conftest накатывает миграции один раз за сессию.
- `just ci` идёт ~5,5 минуты (backend ~267 с, frontend ~53 с) — запускать в фоне,
  а не прятать вывод в `tail`, иначе выглядит как зависание.
- Временный каталог `/c/tmp/gca-f1`: сперва preflight «не существует», потом
  `mkdir`, в конце — поимённое удаление и `rmdir` (не `rm -rf`).

### Готовность фичи

Три коммита по задачам + devlog, `just ci` зелёный, круговой рейс
`downgrade base → upgrade head` пройден, `alembic check` без дрейфа, PR открыт.
Дальше — ревью Fable; замечания проверять фактом до правки, как во всех кругах
этой фичи.
