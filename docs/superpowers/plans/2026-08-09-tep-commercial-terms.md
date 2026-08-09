# Ф5: ТЭП и коммерческие условия — Implementation Plan

> **Для исполнителей:** задачи идут по порядку, каждая заканчивается коммитом.
> Шаги помечены чекбоксами (`- [ ]`). **Все коммиты делает оркестратор.**

**Goal:** завести ТЭП объекта (две вводимые площади и вычисляемую общую) и
коммерческие условия договора (три пары «процент + комментарий»), с формами
ввода и двойной валидацией, чтобы Ф6 могла считать руб/м² и печатать шапку
паспорта.

**Architecture:** миграция `0009` добавляет три колонки на `objects` и шесть на
`contracts`; общая площадь — `GENERATED ALWAYS ... STORED`, не хранимое поле.
Каждое ограничение схемы дублируется проверкой прикладного слоя ради `422`:
самодостаточные — в Pydantic, зависящие от записи в БД — в CRUD после `db.get`.
Фронтенд получает диалог правки объекта, вызываемый с карточки договора, и
свёрнутую секцию условий в форме договора.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x sync ORM, Alembic, psycopg3,
PostgreSQL 16; React + TS, Vite, shadcn/ui, TanStack Query, vitest + MSW.

**Спека:** [2026-08-09-tep-commercial-terms-design.md](../specs/2026-08-09-tep-commercial-terms-design.md)
(коммит `940bac1`, гейт 2 закрыт). **Ветка:** `feat/tep-commercial-terms`
(создана от `main` = `2cc82f9`).

---

## Global Constraints

Требования этого раздела входят в **каждую** задачу неявно.

**Процесс**

- В `main` не коммитить. Ветка одна — `feat/tep-commercial-terms`.
- **Перед пушем — `just ci`**, шагами и в форме CI. Правки только в `docs/` от
  него освобождены (`AGENTS.md` §9.3).
- Коммиты делает **оркестратор**, не исполнитель задачи.
- Субагентам **запрещено печатать кириллицу в терминал**: вердикты ASCII,
  русский текст — в файл отчёта, `rg -c` вместо `rg`, `Read` вместо
  `Get-Content`.

**Оболочка — PowerShell 5.1, не bash**

- После **каждой** нативной команды внутри `try` обязательно
  `if ($LASTEXITCODE -ne 0) { throw "<что> failed: $LASTEXITCODE" }`. После
  блока `try/finally` `$?` равен `True` при ненулевом `$LASTEXITCODE` — падение
  маскируется ([silent-test-runs](../../insights/silent-test-runs.md)).
- **Вердикт читается по напечатанным числам, а не по коду возврата:** `rg` без
  совпадений возвращает `1` и портит код блока, где всё верно.
- Не работают: `env -u`, `VAR=value` префикс, `&&`, `||`, `grep`, `wc`,
  heredoc, перенос строки обратным слэшем. Многострочный текст в `git commit` —
  here-string `@'…'@` с закрывающей меткой в **нулевой** колонке.
- `$(...)` спотыкается о скобки внутри regex — считать в переменную заранее.
- Разовые скрипты — в `$env:TEMP\gca-tep`; каталога `scratchpad/` в репозитории
  нет.
- Один раз на сессию: `$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'`.
- Команды бэкенда — из `backend/` через `uv run`; `git` — из корня.
- Шаблоны патчей в пробниках приводить к **фактическому** разделителю строк
  файла: CRLF против LF уже один раз не дал патчу примениться.
- Восстановление файла после снятия защиты — **из побайтовой копии**, не через
  `git checkout --`: он даёт CRLF и ложную тревогу при сверке.

**Домен**

- **Деньги, площади и проценты:** `Numeric` в БД ↔ `Decimal` в Python ↔ **строки
  в JSON**. На входе — строка или точное целое; `float` отвергается явно. В
  тестах сравнивать `Decimal` с `Decimal`.
- `decimal_json` обязателен для **любого** ответа, несущего `Decimal`. Без него
  `jsonable_encoder` отдаёт `float` (спека §1.5 п. 1).
- **Ставка НДС в `contracts` не переезжает никогда** — другой источник
  (`AGENTS.md` §3, спека Ф4б §2.1).
- **Политика `samples/`:** реальные площади, суммы, названия ЖК и реквизиты
  контрагентов — ни в код, ни в тесты, ни в доки, ни в сообщения коммитов.
- **Фронтенд — только shadcn/ui**, ставится через `npx shadcn add`; кастомных
  компонентов не писать.

**Проверки**

- Число собранных тестов замеряется **после каждой задачи** и пофайлово.
  Отправная точка — **1276 собранных / 1270 passed / 6 skipped**, vitest
  **175**. Расхождение — сигнал дефекта, а не повод обновить ожидание.
- Новому integration-файлу — `pytestmark = pytest.mark.integration`; точечный
  прогон требует `TEST_DATABASE_URL`, а `DATABASE_URL` на ту же базу надо
  снимать (`Remove-Item Env:DATABASE_URL` с возвратом в `finally`).
- **`alembic check` для `Computed` и `CHECK` бесполезен** — autogenerate их не
  сравнивает (спека §1.5 п. 4). Согласованность модели и миграции держат два
  parity-теста задачи 1.
- **Негативные проверки снятием защиты делает оркестратор лично**, по протоколу
  [verifying-guards](../../insights/verifying-guards.md) целиком: контрольный
  прогон **до** снятия; `assert old in text` с проверкой единственности
  вхождения; sha256 до и после; число упавших по каждому снятию; восстановление
  из побайтовой копии со сверкой sha256.
- **Реестр фактических защит побеждает число 23.** Спека §4.4 называет 19
  одиночных снятий и 4 комбинированных; если реализация разделит общую защиту
  (например, заведёт несколько независимых вызовов `decimal_json`) или сольёт
  две — список пересобирается по факту, а расхождение объясняется в devlog.
- **Круговой рейс `alembic downgrade base && upgrade head` — только на тестовой
  базе.** На стенде `gca_dev` откат ниже `0003` упрётся в
  `ProgramLimitExceeded` (`AGENTS.md` §11).
- **Стенд не чистить и не пересоздавать.**

---

## Состояние на момент написания плана (замерено, не пересказано)

| Что | Значение |
|---|---|
| `main` | `2cc82f9`; ветка `feat/tep-commercial-terms` создана от него |
| Коммиты ветки | `7d4d5f6` (спека), `940bac1` (правка спеки по гейту 2) |
| Head миграций | `0008`, файл `2026_08_08_0008-vat_rate.py`, `revision = "0008"` |
| Собранных тестов | **1276** |
| vitest | **175** |
| `objects` | `id, title, address, rate_class_id, created_at, updated_at` ([models.py:253-272](../../../backend/models.py#L253-L272)) |
| `contracts` | + `total_amount`, `notes`, один `CHECK` на неотрицательность суммы ([models.py:294-330](../../../backend/models.py#L294-L330)) |
| Экран объекта | **отсутствует**; `useUpdateObject` определён и не вызывается ниоткуда |
| `collapsible` в `components/ui` | **отсутствует** — 26 компонентов, свёртки среди них нет |

Существующие формы дают приём, который переиспользуется: сумма договора —
**текстовое** поле и уходит строкой, потому что `<input type="number">` отдал бы
`float` ([ContractFormDialog.tsx:92-93](../../../frontend/src/components/contracts/ContractFormDialog.tsx#L92-L93)).
Площади и проценты вводятся так же.

---

## Структура файлов

**Бэкенд**

| Файл | Ответственность |
|---|---|
| `backend/alembic/versions/2026_08_09_0009-tep_commercial_terms.py` | **создать**: девять колонок, семь `CHECK`, вычисляемое выражение |
| `backend/models.py` | **править**: `ObjectModel` (+3 колонки, +4 `CHECK`), `Contract` (+6 колонок, +3 `CHECK`) |
| `backend/crud/references.py` | **править**: `_object_dict`, `create_object`, `update_object`; **новая** `validate_area_pair` — единственный судья итогового состояния |
| `backend/routers/references.py` | **править**: `ObjectCreate`/`ObjectUpdate` + `_AreaMixin`, `decimal_json` на четырёх эндпоинтах, явный `201` |
| `backend/crud/contracts.py` | **править**: `create_contract`, `update_contract`, `get_contract_dict` |
| `backend/routers/contracts.py` | **править**: `ContractCreate`/`ContractUpdate` + `_PercentMixin` |

**Фронтенд**

| Файл | Ответственность |
|---|---|
| `frontend/src/lib/decimal.ts` | **править**: добавить `addDecimalStrings` |
| `frontend/src/types/domain.ts` | **править**: `ObjectItem`, `ObjectInput`, `ContractCard`, `ContractInput` |
| `frontend/src/services/api/domain.ts` | **править**: `getObject` |
| `frontend/src/services/queries.ts` | **править**: `useObject`, инвалидация в `useUpdateObject` |
| `frontend/src/components/objects/ObjectFormDialog.tsx` | **создать**: диалог правки объекта с живой общей площадью |
| `frontend/src/components/contracts/ContractFormDialog.tsx` | **править**: свёрнутая секция «Коммерческие условия» |
| `frontend/src/pages/contracts/ContractCardPage.tsx` | **править**: два блока на чтение + кнопка диалога объекта |
| `frontend/src/components/ui/collapsible.tsx` | **создать** через `npx shadcn add collapsible` |
| `frontend/src/test/handlers.ts` | **править**: `GET/PATCH /objects/:id`, площади в `sampleObjects` |

**Тесты**

`backend/tests/integration/test_schema_constraints.py`,
`test_references_api.py`, `test_contracts_api.py`;
`frontend/src/lib/decimal.test.ts`,
`frontend/src/components/objects/ObjectFormDialog.test.tsx`,
`frontend/src/components/contracts/ContractFormDialog.test.tsx`,
`frontend/src/pages/contracts/ContractCardPage.test.tsx`.

---

## Task 1: Миграция 0009, модели, parity-тесты

**Files:**
- Create: `backend/alembic/versions/2026_08_09_0009-tep_commercial_terms.py`
- Modify: `backend/models.py:253-272` (`ObjectModel`), `backend/models.py:294-330` (`Contract`)
- Test: `backend/tests/integration/test_schema_constraints.py`

**Interfaces:**
- Produces: колонки `objects.area_aboveground_sp`, `objects.area_underground_sp`,
  `objects.area_total_sp`; `contracts.advance_pct`, `advance_note`,
  `bank_guarantee_pct`, `bank_guarantee_note`, `retention_pct`,
  `retention_note`. Константы модуля миграции: `AREA_TOTAL_EXPRESSION`,
  `CK_AREA_ABOVE`, `CK_AREA_UNDER`, `CK_AREA_TOTAL`, `CK_AREA_PAIR`, `_ck_pct`.
  Имена констрейнтов — как в шаге 3.

- [ ] **Шаг 1: прогнать новое правило по уже существующим входам**

До всякого кода. Новые `CHECK` начнут судить то, что уже лежит в репозитории —
[replaying-new-rules](../../insights/replaying-new-rules.md), слой 2.

Проверить и **записать числа**:

```
rg -c "ObjectFactory" backend/tests
rg -n "class ObjectFactory" -A 8 backend/tests/factories.py
rg -c "createObject|/objects" frontend/src
```

Ожидание, которое надо подтвердить, а не предположить: `ObjectFactory`
([factories.py:89](../../../backend/tests/factories.py#L89)) площадей не задаёт,
то есть попадает в законное `NULL`/`NULL`, и `CHECK` его не трогает. Если это не
так — остановиться и сказать оркестратору: правило переклассифицирует
существующие фикстуры, и задача 1 меняется до написания кода.

- [ ] **Шаг 2: написать падающие тесты схемы**

В `test_schema_constraints.py` — новый класс. Партия здесь **не нужна**
(`batch-larger-than-five` про `ON CONFLICT`, спека §5 п. 7), достаточно строк.

```python
class TestObjectAreas:
    """ТЭП объекта: две вводимые площади, третья вычисляемая (спека §2.2, §2.3)."""

    def test_negative_aboveground_is_rejected(self, db_session):
        with rejected(db_session, contains="ck_objects_area_aboveground_sp_non_negative"):
            _make_object(db_session, above="-1", under="0")

    def test_negative_underground_is_rejected(self, db_session):
        with rejected(db_session, contains="ck_objects_area_underground_sp_non_negative"):
            _make_object(db_session, above="0", under="-1")

    def test_both_zero_is_rejected_because_total_would_be_zero(self, db_session):
        """Ноль в частях законен, ноль в общей — нет: она знаменатель."""
        with rejected(db_session, contains="ck_objects_area_total_sp_positive"):
            _make_object(db_session, above="0", under="0")

    def test_only_aboveground_is_rejected(self, db_session):
        with rejected(db_session, contains="ck_objects_areas_both_or_neither"):
            _make_object(db_session, above="100", under=None)

    def test_only_underground_is_rejected(self, db_session):
        with rejected(db_session, contains="ck_objects_areas_both_or_neither"):
            _make_object(db_session, above=None, under="100")

    def test_zero_part_is_a_representable_state(self, db_session):
        """Объект без подземной части: ноль, а не NULL."""
        row = _make_object_and_read(db_session, above="100.50", under="0")
        assert row.area_underground_sp == Decimal("0")
        assert row.area_underground_sp is not None

    def test_no_areas_at_all_is_a_representable_state(self, db_session):
        row = _make_object_and_read(db_session, above=None, under=None)
        assert row.area_total_sp is None

    def test_total_is_computed_from_the_parts(self, db_session):
        row = _make_object_and_read(db_session, above="62399.70", under="13341.30")
        assert row.area_total_sp == Decimal("75741.00")

    def test_total_cannot_be_written_directly(self, db_session):
        """Вычисляемая колонка не принимает значение — иначе она хранимая."""
        with pytest.raises(Exception):
            db_session.execute(
                sa.text("update objects set area_total_sp = 1 where id = :id"),
                {"id": _make_object(db_session, above="1", under="1")},
            )
```

Проценты — отдельным классом, **каждый арм отдельно** (нижнюю границу забывают —
находка финального ревью Ф4б):

```python
@pytest.mark.parametrize("field", ["advance_pct", "bank_guarantee_pct", "retention_pct"])
@pytest.mark.parametrize("value", ["-1", "101"])
def test_percent_outside_the_range_is_rejected(self, db_session, field, value):
    with rejected(db_session, contains=f"ck_contracts_{field}_range"):
        _make_contract(db_session, **{field: value})

@pytest.mark.parametrize("field", ["advance_pct", "bank_guarantee_pct", "retention_pct"])
def test_declared_zero_percent_is_representable(self, db_session, field):
    row = _make_contract_and_read(db_session, **{field: "0"})
    assert getattr(row, field) == Decimal("0")

def test_note_without_percent_is_allowed(self, db_session):
    """Условие есть, но одним процентом не выражается (спека §2.5 п. 1)."""
    row = _make_contract_and_read(db_session, advance_note="траншами по графику")
    assert row.advance_pct is None
    assert row.advance_note is not None
```

Parity — **два теста, обе стороны**, по образцу существующего
`test_orm_declares_the_same_expressions` ([test_schema_constraints.py:754](../../../backend/tests/integration/test_schema_constraints.py#L754)):
один сверяет выражения `CHECK` и `Computed` в **БД** (через `pg_constraint` и
`pg_attrdef`/`information_schema`), другой — словарь `CheckConstraint` и
`.computed` **в модели**, целиком, а не по выбранным ключам.

- [ ] **Шаг 3: прогнать и убедиться, что падают по отсутствию колонок**

```
Set-Location backend; uv run pytest tests/integration/test_schema_constraints.py -q
```
Ожидание: красные с `UndefinedColumn` / `ProgrammingError`, **не** с
`ConstraintViolation`. Красный по другой причине означает, что тест написан не
про то.

- [ ] **Шаг 4: написать миграцию**

```python
"""ТЭП объекта и коммерческие условия договора.

Общая площадь ВЫЧИСЛЯЕТСЯ, а не хранится (спека §2.2): решение пользователя —
два числа на входе, третье на выходе. Цена решения названа границей §5 п. 1:
опечатка в слагаемом ничем не ловится, сверять сумму не с чем.

Части `>= 0`, целое `> 0` — армы разные по причине, а не по недосмотру.
Объект без подземной части законен и его подземная площадь равна НУЛЮ, а не
«неизвестна» (различение, за которое Ф4б заплатила четырьмя снятиями).
А ноль в общей — это знаменатель руб/м², и деление на ноль в Ф6 закрывается
здесь, схемой, а не проверкой в коде паспорта.

Выражения дублируют models.py намеренно: миграция обязана быть неизменной во
времени (то же правило, что у 0004-0008). Расхождение ловят parity-тесты
test_schema_constraints.py — `alembic check` для Computed и CHECK бесполезен.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-09
"""
import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

AREA_TOTAL_EXPRESSION = "area_aboveground_sp + area_underground_sp"

CK_AREA_ABOVE = "area_aboveground_sp IS NULL OR area_aboveground_sp >= 0"
CK_AREA_UNDER = "area_underground_sp IS NULL OR area_underground_sp >= 0"
CK_AREA_TOTAL = "area_total_sp IS NULL OR area_total_sp > 0"
# Для пары тождественно num_nonnulls(...) IN (0, 2), но не зависит от системной
# функции и повторяет идиому 0006. Выражение никогда не даёт NULL.
CK_AREA_PAIR = "(area_aboveground_sp IS NULL) = (area_underground_sp IS NULL)"

_PCT_FIELDS = ("advance_pct", "bank_guarantee_pct", "retention_pct")
_NOTE_FIELDS = ("advance_note", "bank_guarantee_note", "retention_note")


def _ck_pct(name: str) -> str:
    return f"{name} IS NULL OR ({name} >= 0 AND {name} <= 100)"


def upgrade() -> None:
    op.add_column("objects", sa.Column("area_aboveground_sp", sa.Numeric(), nullable=True))
    op.add_column("objects", sa.Column("area_underground_sp", sa.Numeric(), nullable=True))
    op.add_column(
        "objects",
        sa.Column(
            "area_total_sp",
            sa.Numeric(),
            sa.Computed(AREA_TOTAL_EXPRESSION, persisted=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_objects_area_aboveground_sp_non_negative", "objects", CK_AREA_ABOVE
    )
    op.create_check_constraint(
        "ck_objects_area_underground_sp_non_negative", "objects", CK_AREA_UNDER
    )
    op.create_check_constraint("ck_objects_area_total_sp_positive", "objects", CK_AREA_TOTAL)
    op.create_check_constraint("ck_objects_areas_both_or_neither", "objects", CK_AREA_PAIR)

    for name in _PCT_FIELDS:
        op.add_column("contracts", sa.Column(name, sa.Numeric(), nullable=True))
        op.create_check_constraint(f"ck_contracts_{name}_range", "contracts", _ck_pct(name))
    for name in _NOTE_FIELDS:
        op.add_column("contracts", sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    for name in _NOTE_FIELDS:
        op.drop_column("contracts", name)
    for name in _PCT_FIELDS:
        op.drop_constraint(f"ck_contracts_{name}_range", "contracts", type_="check")
        op.drop_column("contracts", name)

    op.drop_constraint("ck_objects_areas_both_or_neither", "objects", type_="check")
    op.drop_constraint("ck_objects_area_total_sp_positive", "objects", type_="check")
    op.drop_constraint("ck_objects_area_underground_sp_non_negative", "objects", type_="check")
    op.drop_constraint("ck_objects_area_aboveground_sp_non_negative", "objects", type_="check")

    # Вычисляемая колонка снимается ПЕРВОЙ: она зависит от обоих слагаемых.
    # Порядок замерен шагом 6 плана, а не выведен из документации.
    op.drop_column("objects", "area_total_sp")
    op.drop_column("objects", "area_underground_sp")
    op.drop_column("objects", "area_aboveground_sp")
```

- [ ] **Шаг 5: объявить то же в моделях**

В `models.py`, рядом с `ObjectModel`:

```python
#: Дублирует миграцию 0009 намеренно; расхождение ловят parity-тесты.
OBJECT_AREA_TOTAL_EXPRESSION = "area_aboveground_sp + area_underground_sp"
```

В самом классе — три колонки и четыре `CheckConstraint` с теми же именами и
теми же выражениями; в `Contract` — шесть колонок и три `CheckConstraint`.
`Computed` импортируется из `sqlalchemy` (уже импортирован, [models.py:9](../../../backend/models.py#L9)).

- [ ] **Шаг 6: замерить порядок снятия колонок — до кругового рейса**

Ожидание «PostgreSQL откажется снимать слагаемое при живой вычисляемой» —
именно ожидание. Проверить пробником на **тестовой** базе:

```sql
-- $env:TEMP\gca-tep\probe_drop_order.sql
create table t_probe (a numeric, b numeric,
                      c numeric generated always as (a + b) stored);
alter table t_probe drop column a;   -- ожидаем ошибку зависимости
```

Записать фактический текст ошибки (или её отсутствие) в отчёт задачи. Если
отказа **нет** — комментарий в `downgrade` исправить на фактический, а не
оставить неверное объяснение верного порядка.

- [ ] **Шаг 7: применить и прогнать**

```
Set-Location backend; uv run alembic upgrade head
uv run pytest tests/integration/test_schema_constraints.py -q
```
Ожидание: зелёные. Прочитать **число прошедших**, а не код возврата.

- [ ] **Шаг 8: круговой рейс**

```
uv run alembic downgrade base
uv run alembic upgrade head
uv run alembic check
```
Ожидание: обе команды без ошибок, `alembic check` — «No new upgrade operations
detected». **Помнить: для `Computed` и `CHECK` этот вердикт ничего не
доказывает** — согласованность держат parity-тесты шага 2.

- [ ] **Шаг 9: замерить и закоммитить**

```
uv run pytest --collect-only -q
```
Записать новое число собранных и дельту от 1276. Коммит: `feat(tep): миграция
0009 — площади объекта и коммерческие условия договора`.

---

## Task 2: Прикладной слой объектов — `422` вместо `500`

**Files:**
- Modify: `backend/crud/references.py:166-176` (`_object_dict`), `:222-238`
  (`create_object`), `:241-259` (`update_object`)
- Modify: `backend/routers/references.py:53-63` (схемы), `:160-212` (эндпоинты)
- Test: `backend/tests/integration/test_references_api.py`

**Interfaces:**
- Consumes: колонки задачи 1.
- Produces: `crud.references.validate_area_pair(above, under) -> None`;
  `create_object(db, *, title, address=None, rate_class_id=None,
  area_aboveground_sp=None, area_underground_sp=None) -> dict`;
  `update_object(db, object_id, *, title=UNSET, address=UNSET,
  rate_class_id=UNSET, area_aboveground_sp=UNSET, area_underground_sp=UNSET)
  -> dict`. В ответе `_object_dict` появляются три ключа: `area_aboveground_sp`,
  `area_underground_sp`, `area_total_sp` — значения `Decimal | None`.

- [ ] **Шаг 1: написать падающие тесты API**

Таблица «входы, обязанные получить `422`» из спеки §2.4 — по строке на вход.
Утверждать **именно `422`**: `500` мимо «не 2xx» прошёл бы.

```python
def test_post_with_only_one_area_is_422(admin_client):
    r = admin_client.post("/api/v1/objects",
                          json={"title": "О1", "area_aboveground_sp": "100"})
    assert r.status_code == 422

def test_post_with_negative_area_is_422_not_500(admin_client):
    r = admin_client.post("/api/v1/objects",
                          json={"title": "О2",
                                "area_aboveground_sp": "-1",
                                "area_underground_sp": "0"})
    assert r.status_code == 422

def test_post_with_both_zero_is_422_and_names_the_total(admin_client):
    """Части нулю равны законно — отказ обязан говорить про ОБЩУЮ площадь."""
    r = admin_client.post("/api/v1/objects",
                          json={"title": "О3",
                                "area_aboveground_sp": "0",
                                "area_underground_sp": "0"})
    assert r.status_code == 422
    assert "общая" in r.text.lower()

def test_patch_one_filled_area_alone_is_allowed(admin_client, object_with_areas):
    """Обе заполнены — правка одной разрешена (спека §2.4)."""
    r = admin_client.patch(f"/api/v1/objects/{object_with_areas}",
                           json={"area_aboveground_sp": "70000.00"})
    assert r.status_code == 200

def test_patch_zero_part_alone_is_allowed(admin_client, object_with_zero_underground):
    """«Уже заполненная» — это NOT NULL, а не «не равная нулю»."""
    r = admin_client.patch(f"/api/v1/objects/{object_with_zero_underground}",
                           json={"area_underground_sp": "500.00"})
    assert r.status_code == 200

def test_patch_from_empty_to_one_area_is_422(admin_client, object_without_areas):
    r = admin_client.patch(f"/api/v1/objects/{object_without_areas}",
                           json={"area_aboveground_sp": "100"})
    assert r.status_code == 422

def test_patch_null_for_both_clears_them(admin_client, object_with_areas):
    r = admin_client.patch(f"/api/v1/objects/{object_with_areas}",
                           json={"area_aboveground_sp": None,
                                 "area_underground_sp": None})
    assert r.status_code == 200
    assert r.json()["area_total_sp"] is None

def test_patch_null_for_one_only_is_422(admin_client, object_with_areas):
    r = admin_client.patch(f"/api/v1/objects/{object_with_areas}",
                           json={"area_underground_sp": None})
    assert r.status_code == 422

def test_float_area_is_rejected(admin_client):
    r = admin_client.post("/api/v1/objects",
                          json={"title": "О4",
                                "area_aboveground_sp": 62399.7,
                                "area_underground_sp": 13341.3})
    assert r.status_code == 422
```

Деньги в сыром теле — смотреть на **текст ответа**, не на разобранный JSON:

```python
def test_areas_reach_json_as_strings(admin_client, object_with_areas):
    r = admin_client.get(f"/api/v1/objects/{object_with_areas}")
    assert '"area_total_sp":"75741.00"' in r.text.replace(" ", "")

def test_post_objects_still_answers_201(admin_client):
    """Регресс: Response несёт свой статус мимо status_code декоратора."""
    r = admin_client.post("/api/v1/objects", json={"title": "О5"})
    assert r.status_code == 201
```

Плюс `member` получает `403` на `POST` и `PATCH` с площадями.

- [ ] **Шаг 2: прогнать, увидеть красное**

```
uv run pytest tests/integration/test_references_api.py -q
```
Ожидание: красные. Часть — по `500` (значение доходит до `CHECK`), часть — по
отсутствию ключей в ответе. Записать, сколько и по какой причине.

- [ ] **Шаг 3: судья итогового состояния — в CRUD**

```python
def validate_area_pair(above: Decimal | None, under: Decimal | None) -> None:
    """Судит ИТОГОВОЕ состояние пары площадей, а не переданную дельту (§2.4).

    Вызывается и из `create_object`, и из `update_object`: у создания «итоговое
    состояние» — это просто вход. Одна функция на оба пути, иначе правило жило
    бы в двух местах и разъехалось бы при первой правке.

    `CHECK` в схеме говорит то же самое и остаётся последним рубежом; здесь
    отказ человекочитаемый, потому что `translating_integrity` переводит только
    нарушения уникальности и только в 409.
    """
    if (above is None) != (under is None):
        raise DomainError(
            422,
            "Площади задаются парой: укажите наземную и подземную вместе либо "
            "не указывайте ни одной.",
        )
    if above is None:
        return
    if above + under <= 0:
        raise DomainError(
            422,
            "Общая площадь получилась нулевой, а по ней считается руб/м². "
            "Хотя бы одна из частей должна быть больше нуля.",
        )
```

В `update_object` вызывается **после** слияния: значения берутся из `obj`, если
поле не передано.

- [ ] **Шаг 4: самодостаточные проверки — в Pydantic**

В `routers/references.py`, по образцу `_MoneyMixin`
([contracts.py:34-55](../../../backend/routers/contracts.py#L34-L55)):

```python
class _AreaMixin(BaseModel):
    """Площади: не `float` и не отрицательные (спека §2.4, §2.6)."""

    @field_validator("area_aboveground_sp", "area_underground_sp",
                     mode="before", check_fields=False)
    @classmethod
    def _reject_float(cls, value):
        if isinstance(value, float):
            raise ValueError(
                "Площадь передавайте строкой (например \"62399.70\"), а не числом "
                "с плавающей точкой: float внесёт двоичный хвост в знаменатель "
                "руб/м²."
            )
        return value

    @field_validator("area_aboveground_sp", "area_underground_sp", check_fields=False)
    @classmethod
    def _non_negative(cls, value: Decimal | None):
        if value is not None and value < 0:
            raise ValueError("Площадь не может быть отрицательной.")
        return value
```

`ObjectCreate` и `ObjectUpdate` наследуют миксин и получают по два поля
`Decimal | None`.

- [ ] **Шаг 5: ответы через `decimal_json`, `POST` — с явным `201`**

Четыре эндпоинта `objects` оборачиваются; `POST` — `decimal_json(body,
status.HTTP_201_CREATED)`. Три ключа добавляются в `_object_dict`.

- [ ] **Шаг 6: прогнать до зелёного и закоммитить**

```
uv run pytest tests/integration/test_references_api.py -q
uv run pytest --collect-only -q
```
Коммит: `feat(tep): площади объекта в API — двойная валидация и Decimal строкой`.

---

## Task 3: Коммерческие условия — Pydantic, CRUD, карточка

**Files:**
- Modify: `backend/routers/contracts.py:34-90` (миксин и схемы)
- Modify: `backend/crud/contracts.py:195-203` (`get_contract_dict`), `:268-316`
  (`create_contract`), `:319-371` (`update_contract`)
- Test: `backend/tests/integration/test_contracts_api.py`

**Interfaces:**
- Produces: шесть ключей в ответе `get_contract_dict` (и, следовательно, в
  ответах create/update); в `_contract_row_dict` их **нет**.

- [ ] **Шаг 1: написать падающие тесты**

```python
def test_commercial_terms_round_trip(admin_client, contract_payload):
    payload = {**contract_payload,
               "advance_pct": "30", "advance_note": "двумя траншами",
               "bank_guarantee_pct": "10", "retention_pct": "5"}
    created = admin_client.post("/api/v1/contracts", json=payload)
    assert created.status_code == 201
    card = admin_client.get(f"/api/v1/contracts/{created.json()['id']}").json()
    assert card["advance_pct"] == "30"
    assert card["advance_note"] == "двумя траншами"
    assert card["retention_pct"] == "5"

def test_patch_touches_one_field_only(admin_client, contract_with_terms):
    admin_client.patch(f"/api/v1/contracts/{contract_with_terms}",
                       json={"retention_pct": "7"})
    card = admin_client.get(f"/api/v1/contracts/{contract_with_terms}").json()
    assert card["retention_pct"] == "7"
    assert card["advance_pct"] == "30"          # не поехало
    assert card["advance_note"] == "двумя траншами"

def test_null_clears_a_single_condition(admin_client, contract_with_terms):
    admin_client.patch(f"/api/v1/contracts/{contract_with_terms}",
                       json={"advance_pct": None})
    card = admin_client.get(f"/api/v1/contracts/{contract_with_terms}").json()
    assert card["advance_pct"] is None
    assert card["advance_note"] == "двумя траншами"   # парности нет

def test_note_without_percent_is_accepted(admin_client, contract_payload):
    r = admin_client.post("/api/v1/contracts",
                          json={**contract_payload,
                                "advance_note": "аванс не предусмотрен"})
    assert r.status_code == 201
    assert r.json()["advance_pct"] is None

def test_empty_note_becomes_null_not_empty_string(admin_client, contract_payload):
    """Пустая строка означала бы «условие заведено», хотя заведено ничего не было."""
    r = admin_client.post("/api/v1/contracts",
                          json={**contract_payload, "advance_note": "   "})
    assert r.json()["advance_note"] is None

@pytest.mark.parametrize("field", ["advance_pct", "bank_guarantee_pct", "retention_pct"])
@pytest.mark.parametrize("value", ["-1", "101"])
def test_percent_outside_range_is_422_not_500(admin_client, contract_payload, field, value):
    r = admin_client.post("/api/v1/contracts", json={**contract_payload, field: value})
    assert r.status_code == 422

def test_terms_are_absent_from_the_list_response(admin_client, contract_with_terms):
    """Список — это выбор, а не карточка (спека §2.5)."""
    item = admin_client.get("/api/v1/contracts").json()["items"][0]
    assert "advance_pct" not in item

def test_percent_reaches_json_as_string(admin_client, contract_with_terms):
    r = admin_client.get(f"/api/v1/contracts/{contract_with_terms}")
    assert '"advance_pct":"30"' in r.text.replace(" ", "")

def test_float_percent_is_rejected(admin_client, contract_payload):
    r = admin_client.post("/api/v1/contracts",
                          json={**contract_payload, "advance_pct": 30.5})
    assert r.status_code == 422

def test_member_cannot_edit_commercial_terms(member_client, contract_with_terms):
    r = member_client.patch(f"/api/v1/contracts/{contract_with_terms}",
                            json={"advance_pct": "50"})
    assert r.status_code == 403
```

- [ ] **Шаг 2: прогнать, увидеть красное**

```
uv run pytest tests/integration/test_contracts_api.py -q
```

- [ ] **Шаг 3: миксин процентов**

```python
class _PercentMixin(BaseModel):
    """Проценты условий: не `float` и в пределах [0, 100] (спека §2.4, §2.5)."""

    @field_validator("advance_pct", "bank_guarantee_pct", "retention_pct",
                     mode="before", check_fields=False)
    @classmethod
    def _reject_float(cls, value):
        if isinstance(value, float):
            raise ValueError(
                "Процент передавайте строкой (например \"30\" или \"12.5\"), а не "
                "числом с плавающей точкой."
            )
        return value

    @field_validator("advance_pct", "bank_guarantee_pct", "retention_pct",
                     check_fields=False)
    @classmethod
    def _in_range(cls, value: Decimal | None):
        if value is not None and not (0 <= value <= 100):
            raise ValueError("Процент должен быть в пределах от 0 до 100.")
        return value
```

`ContractCreate` и `ContractUpdate` наследуют `_MoneyMixin` **и**
`_PercentMixin`, получают шесть полей.

- [ ] **Шаг 4: CRUD**

`create_contract` и `update_contract` принимают шесть аргументов; комментарии
приводятся уже стоящим рядом приёмом `(value or "").strip() or None`
([contracts.py:299-303](../../../backend/crud/contracts.py#L299-L303)). Шесть
ключей добавляются в `get_contract_dict` рядом с `notes`
([contracts.py:201](../../../backend/crud/contracts.py#L201)), **не** в
`_contract_row_dict`. Шесть имён добавляются в разбор `exclude_unset` роутера;
в `_NON_NULLABLE` они **не** попадают — `null` для них осмысленный сброс.

- [ ] **Шаг 5: прогнать до зелёного, замерить, закоммитить**

Коммит: `feat(tep): коммерческие условия договора — три пары «процент + комментарий»`.

---

## Task 4: `addDecimalStrings`

**Files:**
- Modify: `frontend/src/lib/decimal.ts`
- Test: `frontend/src/lib/decimal.test.ts`

**Interfaces:**
- Produces: `addDecimalStrings(left: string, right: string): string | null` —
  сумма строкой либо `null`, если аргумент не десятичное число. Та же контрактная
  форма, что у `multiplyDecimalStrings`.

- [ ] **Шаг 1: замерить предпосылку до написания теста**

Промах `Number` **выборочный** — `1000.33 × 1.075` совпадает точно
([decimal.ts:36-37](../../../frontend/src/lib/decimal.ts#L36-L37)). Прежде чем
писать `expect(Number(a) + Number(b)).not.toBe(...)`, подобрать пару, которая
**действительно** промахивается, и записать замер в комментарий теста.
Кандидаты для проверки в node: `0.1 + 0.2`, `62399.7 + 13341.3`,
`1000.1 + 2000.2`. Пару, которая совпала точно, в тест не брать — иначе тест
покраснеет на верной реализации.

- [ ] **Шаг 2: написать падающий тест**

```ts
describe("addDecimalStrings", () => {
  it("складывает точно там, где Number промахивается", () => {
    // Предпосылка проверяется ВНУТРИ теста: промах float выборочный, и её слом
    // должен быть виден как падение, а не как молчание (false-test-premises).
    expect(Number("0.1") + Number("0.2")).not.toBe(0.3);
    expect(addDecimalStrings("0.1", "0.2")).toBe("0.3");
  });

  it("выравнивает разные масштабы", () => {
    expect(addDecimalStrings("62399.7", "13341.30")).toBe("75741");
  });

  it("складывает целые", () => {
    expect(addDecimalStrings("100", "23")).toBe("123");
  });

  it("ноль слагаемым не мешает", () => {
    expect(addDecimalStrings("100.50", "0")).toBe("100.5");
  });

  it("возвращает null на не-числе", () => {
    expect(addDecimalStrings("сто", "1")).toBeNull();
    expect(addDecimalStrings("", "1")).toBeNull();
    expect(addDecimalStrings("1,5", "1")).toBeNull();
  });
});
```

- [ ] **Шаг 3: прогнать, увидеть красное**

```
Set-Location frontend; npx vitest run src/lib/decimal.test.ts
```

- [ ] **Шаг 4: реализация**

```ts
/**
 * Складывает две десятичные строки **точно**, без числа с плавающей точкой.
 *
 * Нужно форме ТЭП: под полями наземной и подземной площадей показывается
 * вычисленная общая, и она же на бумаге станет знаменателем руб/м². `Number`
 * внёс бы в неё двоичный хвост (замер — docstring `multiplyDecimalStrings`).
 *
 * Масштабы выравниваются по большему, дальше складываются целые в `BigInt`.
 *
 * @returns сумму строкой либо `null`, если аргумент не десятичное число.
 */
export function addDecimalStrings(left: string, right: string): string | null {
  const a = DECIMAL_RE.exec(left.trim());
  const b = DECIMAL_RE.exec(right.trim());
  if (!a || !b) return null;

  const scaleA = a[3]?.length ?? 0;
  const scaleB = b[3]?.length ?? 0;
  const scale = Math.max(scaleA, scaleB);

  const scaled = (m: RegExpExecArray, own: number) => {
    const digits = BigInt(`${m[2]}${m[3] ?? ""}`) * 10n ** BigInt(scale - own);
    return m[1] === "-" ? -digits : digits;
  };

  const sum = scaled(a, scaleA) + scaled(b, scaleB);
  const sign = sum < 0n ? "-" : "";
  const abs = (sum < 0n ? -sum : sum).toString();

  if (scale === 0) return `${sign}${abs}`;

  const padded = abs.padStart(scale + 1, "0");
  const whole = padded.slice(0, padded.length - scale);
  const fraction = padded.slice(padded.length - scale).replace(/0+$/, "");
  return fraction ? `${sign}${whole}.${fraction}` : `${sign}${whole}`;
}
```

- [ ] **Шаг 5: прогнать до зелёного и закоммитить**

Коммит: `feat(tep): точное сложение десятичных строк для живой общей площади`.

---

## Task 5: Типы, клиент, инвалидация

**Files:**
- Modify: `frontend/src/types/domain.ts:42-57` (`ObjectItem`, `ObjectInput`),
  `:111-126` (`ContractCard`, `ContractInput`)
- Modify: `frontend/src/services/api/domain.ts:54-61`
- Modify: `frontend/src/services/queries.ts:170-201`
- Modify: `frontend/src/test/handlers.ts:212+`

**Interfaces:**
- Produces: `referencesApi.getObject(id: number): Promise<ObjectItem>`;
  `useObject(id: number)`; `ObjectItem` и `ObjectInput` с тремя/двумя полями
  `Decimal | null`; `ContractCard` и `ContractInput` с шестью полями.
  `Decimal` — существующий алиас строки в `types/domain.ts`.

- [ ] **Шаг 1: написать падающий тест инвалидации**

Проверять **каждый корень отдельно**: утверждение «вызвано пять раз» прошло бы
и при пяти одинаковых ключах.

```ts
it("правка объекта инвалидирует все кэши, где лежит его название", async () => {
  const spy = vi.spyOn(queryClient, "invalidateQueries");
  await act(() => result.current.mutateAsync({ id: 1, input: { title: "Новое" } }));

  const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
  expect(keys).toContain(JSON.stringify(qk.objects.all));
  expect(keys).toContain(JSON.stringify(qk.rateClasses.all));
  expect(keys).toContain(JSON.stringify(qk.contracts.all));
  expect(keys).toContain(JSON.stringify(qk.passport.all));
  expect(keys).toContain(JSON.stringify(qk.matrix.all));
});
```

- [ ] **Шаг 2: прогнать, увидеть красное** (трёх ключей нет).

- [ ] **Шаг 3: типы и клиент**

`ObjectItem` += `area_aboveground_sp`, `area_underground_sp`, `area_total_sp`
(`Decimal | null`); `ObjectInput` += две первых (`Decimal | null` опционально).
`ContractCard` и `ContractInput` += шесть полей. В `domain.ts`:

```ts
getObject: (id: number): Promise<ObjectItem> =>
  api.get<ObjectItem>(`/v1/objects/${id}`).then((r) => r.data),
```

- [ ] **Шаг 4: хук и инвалидация**

```ts
export function useObject(id: number) {
  return useQuery({
    queryKey: qk.objects.one(id),
    queryFn: () => referencesApi.getObject(id),
  });
}
```

В `queryKeys.ts` добавить `objects.one: (id: number) => ["objects", "one", id]`.
В `useUpdateObject.onSuccess` — пять инвалидаций с комментарием:

```ts
// Название объекта денормализовано в договоры, паспорт и колонки матрицы
// (спека §2.11). Корень, а не карточка: у объекта может быть несколько
// договоров, и название лежит в каждом.
```

- [ ] **Шаг 5: MSW-хендлеры**

`sampleObjects` получают три поля; добавить `http.get("/api/v1/objects/:id")` и
`http.patch("/api/v1/objects/:id")`.

- [ ] **Шаг 6: прогнать, замерить, закоммитить**

```
npx vitest run
npx tsc -b --noEmit
```
Коммит: `feat(tep): типы площадей, getObject и полная инвалидация правки объекта`.

---

## Task 6: Диалог правки объекта

**Files:**
- Create: `frontend/src/components/objects/ObjectFormDialog.tsx`
- Create: `frontend/src/components/objects/ObjectFormDialog.test.tsx`

**Interfaces:**
- Consumes: `useObject`, `useUpdateObject`, `addDecimalStrings`,
  `normalizeDecimalInput`.
- Produces: `<ObjectFormDialog open onOpenChange objectId />`.

- [ ] **Шаг 1: написать падающие тесты**

```tsx
it("показывает вычисленную общую площадь под полями", async () => {
  render(<ObjectFormDialog open objectId={1} onOpenChange={() => {}} />);
  await userEvent.clear(await screen.findByLabelText(/наземная/i));
  await userEvent.type(screen.getByLabelText(/наземная/i), "62399.70");
  await userEvent.clear(screen.getByLabelText(/подземная/i));
  await userEvent.type(screen.getByLabelText(/подземная/i), "13341.30");
  expect(await screen.findByText("75741")).toBeInTheDocument();
});

it("общая не появляется, пока заполнено только одно поле", async () => {
  render(<ObjectFormDialog open objectId={2} onOpenChange={() => {}} />);
  await userEvent.type(await screen.findByLabelText(/наземная/i), "100");
  expect(screen.queryByTestId("area-total-preview")).not.toBeInTheDocument();
});

it("отправляет площади строками, а не числами", async () => {
  let body: unknown;
  server.use(
    http.patch("/api/v1/objects/:id", async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ id: 1 });
    })
  );
  render(<ObjectFormDialog open objectId={1} onOpenChange={() => {}} />);
  await userEvent.clear(await screen.findByLabelText(/наземная/i));
  await userEvent.type(screen.getByLabelText(/наземная/i), "62399,70");
  await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
  await waitFor(() => expect(body).toBeDefined());
  // Запятая приведена к точке, значение — строка: <input type="number"> отдал бы float.
  expect((body as Record<string, unknown>).area_aboveground_sp).toBe("62399.70");
});

it("правит одну площадь, не трогая вторую", async () => {
  let body: Record<string, unknown> | undefined;
  server.use(
    http.patch("/api/v1/objects/:id", async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ id: 1 });
    })
  );
  render(<ObjectFormDialog open objectId={1} onOpenChange={() => {}} />);
  await userEvent.clear(await screen.findByLabelText(/подземная/i));
  await userEvent.type(screen.getByLabelText(/подземная/i), "500");
  await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
  await waitFor(() => expect(body).toBeDefined());
  expect(body!.area_underground_sp).toBe("500");
});

it("сбрасывает обе площади, когда оба поля очищены", async () => {
  let body: Record<string, unknown> | undefined;
  server.use(
    http.patch("/api/v1/objects/:id", async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ id: 1 });
    })
  );
  render(<ObjectFormDialog open objectId={1} onOpenChange={() => {}} />);
  await userEvent.clear(await screen.findByLabelText(/наземная/i));
  await userEvent.clear(screen.getByLabelText(/подземная/i));
  await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
  await waitFor(() => expect(body).toBeDefined());
  expect(body!.area_aboveground_sp).toBeNull();
  expect(body!.area_underground_sp).toBeNull();
});

it("показывает отказ сервера человеку", async () => {
  server.use(
    http.patch("/api/v1/objects/:id", () =>
      HttpResponse.json({ detail: "Площади задаются парой" }, { status: 422 })
    )
  );
  render(<ObjectFormDialog open objectId={1} onOpenChange={() => {}} />);
  await userEvent.clear(await screen.findByLabelText(/подземная/i));
  await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
  expect(await screen.findByText(/площади задаются парой/i)).toBeInTheDocument();
});
```

`area-total-preview` — `data-testid` живой общей: искать её по тексту значения
нельзя, потому что то же число может появиться в поле ввода.

- [ ] **Шаг 2: прогнать, увидеть красное** (компонента нет).

- [ ] **Шаг 3: реализация**

Диалог на существующем `Dialog`; тело — отдельный компонент, монтируемый только
при `open`, начальные значения через `useState` без эффекта — тот же приём, что
в `ContractFormDialog` ([ContractFormDialog.tsx:104-109](../../../frontend/src/components/contracts/ContractFormDialog.tsx#L104-L109)).
Поля площадей — **текстовые**, не `type="number"`; ввод проходит через
`normalizeDecimalInput`, живая сумма — через `addDecimalStrings`, и показывается
только когда **оба** поля дают валидное число.

- [ ] **Шаг 4: прогнать до зелёного и закоммитить**

Коммит: `feat(tep): диалог правки объекта с живой общей площадью`.

---

## Task 7: Секция коммерческих условий в форме договора

**Files:**
- Create: `frontend/src/components/ui/collapsible.tsx` (через `npx shadcn add collapsible`)
- Modify: `frontend/src/components/contracts/ContractFormDialog.tsx:39-75`
  (`FormState`, `EMPTY`, `fromContract`), тело формы, `handleSubmit`
- Test: `frontend/src/components/contracts/ContractFormDialog.test.tsx`

- [ ] **Шаг 1: поставить компонент свёртки**

```
Set-Location frontend; npx shadcn add collapsible
```
Кастомную свёртку **не писать**. Записать, какие файлы добавились.

- [ ] **Шаг 2: написать падающие тесты**

```tsx
it("секция условий свёрнута по умолчанию", async () => {
  render(<ContractFormDialog open onOpenChange={() => {}} />);
  expect(screen.queryByLabelText(/аванс, %/i)).not.toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /коммерческие условия/i }))
    .toBeInTheDocument();
});

it("создание договора без условий не блокируется", async () => {
  render(<ContractFormDialog open onOpenChange={() => {}} />);
  await fillRequiredContractFields();          // существующий хелпер файла
  expect(screen.getByRole("button", { name: /создать/i })).toBeEnabled();
});

it("отправляет проценты строками, а пустой комментарий — как null", async () => {
  let body: Record<string, unknown> | undefined;
  server.use(
    http.post("/api/v1/contracts", async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ id: 7 }, { status: 201 });
    })
  );
  render(<ContractFormDialog open onOpenChange={() => {}} />);
  await fillRequiredContractFields();
  await userEvent.click(screen.getByRole("button", { name: /коммерческие условия/i }));
  await userEvent.type(screen.getByLabelText(/аванс, %/i), "30");
  await userEvent.type(screen.getByLabelText(/оговорка к авансу/i), "   ");
  await userEvent.click(screen.getByRole("button", { name: /создать/i }));
  await waitFor(() => expect(body).toBeDefined());
  expect(body!.advance_pct).toBe("30");
  expect(body!.advance_note).toBeNull();
});

it("в режиме правки показывает уже заведённые условия", async () => {
  render(
    <ContractFormDialog
      open
      onOpenChange={() => {}}
      contract={{ ...sampleContractCard, advance_pct: "30", advance_note: "траншами" }}
    />
  );
  await userEvent.click(await screen.findByRole("button", { name: /коммерческие условия/i }));
  expect(screen.getByLabelText(/аванс, %/i)).toHaveValue("30");
  expect(screen.getByLabelText(/оговорка к авансу/i)).toHaveValue("траншами");
});
```

- [ ] **Шаг 3: прогнать, увидеть красное.**

- [ ] **Шаг 4: реализация**

`FormState` += шесть строковых полей (проценты — текстом, как `total_amount`);
`EMPTY` и `fromContract` — соответственно. В `handleSubmit` проценты проходят
`normalizeDecimalInput(...) || null`, комментарии — `.trim() || null`. Секция
внутри `Collapsible`, закрыта по умолчанию; в `canSubmit` условия **не** входят.

- [ ] **Шаг 5: прогнать до зелёного и закоммитить**

Коммит: `feat(tep): секция коммерческих условий в форме договора`.

---

## Task 8: Карточка договора — два блока на чтение

**Files:**
- Modify: `frontend/src/pages/contracts/ContractCardPage.tsx:100-120`
- Test: `frontend/src/pages/contracts/ContractCardPage.test.tsx`

- [ ] **Шаг 1: написать падающие тесты**

```tsx
it("показывает ТЭП объекта тремя величинами", async () => {
  renderCard({ objectAreas: { above: "62399.70", under: "13341.30", total: "75741.00" } });
  expect(await screen.findByText("62 399,70")).toBeInTheDocument();
  expect(screen.getByText("13 341,30")).toBeInTheDocument();
  expect(screen.getByText("75 741,00")).toBeInTheDocument();
});

it("показывает «ТЭП не заведены», когда площадей нет", async () => {
  renderCard({ objectAreas: { above: null, under: null, total: null } });
  expect(await screen.findByText(/тэп не заведены/i)).toBeInTheDocument();
});

it("показывает процент условия вместе с его оговоркой", async () => {
  renderCard({ terms: { advance_pct: "30", advance_note: "двумя траншами" } });
  const advance = await screen.findByTestId("term-advance");
  expect(advance).toHaveTextContent("30");
  expect(advance).toHaveTextContent("двумя траншами");
});

it("показывает оговорку и тогда, когда процента нет", async () => {
  renderCard({ terms: { advance_pct: null, advance_note: "аванс не предусмотрен" } });
  expect(await screen.findByTestId("term-advance"))
    .toHaveTextContent("аванс не предусмотрен");
});

it("кнопка правки ТЭП открывает диалог объекта", async () => {
  renderCard({ role: "admin" });
  await userEvent.click(await screen.findByRole("button", { name: /тэп объекта/i }));
  expect(await screen.findByLabelText(/наземная/i)).toBeInTheDocument();
});

it("кнопки правки ТЭП нет у member", async () => {
  renderCard({ role: "member" });
  await screen.findByText(/договор/i);
  expect(screen.queryByRole("button", { name: /тэп объекта/i })).not.toBeInTheDocument();
});
```

`term-advance` — `data-testid` блока условия: процент и оговорка обязаны стоять
**в одном** узле, иначе тест прошёл бы и при оговорке, съехавшей к соседнему
условию. `renderCard` — хелпер файла, настраивающий MSW-ответ карточки, объекта
и текущего пользователя.

- [ ] **Шаг 2: прогнать, увидеть красное.**

- [ ] **Шаг 3: реализация**

Два блока рядом с существующими `Field` ([ContractCardPage.tsx:109-118](../../../frontend/src/pages/contracts/ContractCardPage.tsx#L109-L118)).
ТЭП берутся `useObject(contract.object_id)` — **отдельным запросом**, не из
карточки: `rate_class_id` в карточке это снимок договора, и класс объекта рядом
с ним дал бы два поля с одним именем и разным смыслом (спека §2.9). Кнопка
правки — только `admin`.

- [ ] **Шаг 4: прогнать до зелёного и закоммитить**

Коммит: `feat(tep): карточка договора показывает ТЭП объекта и коммерческие условия`.

---

## Task 9: Негативные проверки, соответствие, стенд, devlog, PR

**Files:**
- Create: `docs/devlog/2026-08-09-tep-commercial-terms.md`
- Modify: `docs/phase7-frame.md` (врезка «Ф5 реализована»)

- [ ] **Шаг 1: контрольный прогон до всякого снятия**

Записать числа по файлам: `test_schema_constraints.py`,
`test_references_api.py`, `test_contracts_api.py`, `decimal.test.ts`,
`ObjectFormDialog.test.tsx`, `ContractFormDialog.test.tsx`,
`ContractCardPage.test.tsx`. **Красный до поломки — это сломанный пробник, а не
доказанная защита.**

- [ ] **Шаг 2: пересобрать реестр защит по факту**

Спека §4.4 называет 19 одиночных и 4 комбинированных. Сверить со **фактической**
реализацией: если общая защита разделилась (несколько независимых вызовов
`decimal_json`) или слилась — список меняется, и расхождение объясняется в
devlog. **Реестр побеждает число 23.**

- [ ] **Шаг 3: снятия — оркестратор лично**

По каждому: `assert old in text` с проверкой единственности вхождения, sha256 до
и после (`changed=True`), прогон с записью числа упавших, восстановление из
побайтовой копии со сверкой sha256. Шаблон патча привести к фактическому
разделителю строк файла.

Комбинированные (слой 8): `4 + 11`, `3 + 12`, `1 + 8`, `6 + 9`. Каждое
доказывает третий исход — значение **ложится в таблицу**.

Снятие, не уронившее ничего, разобрать: либо защиты нет, либо модель дефекта
неверна (в Ф4б верным оказался второй ответ). Записать обе пробы.

- [ ] **Шаг 4: соответствие «требование спеки → тест»**

По тексту §2 спеки, требование за требованием. Не «покрыто ли», а «каким именно
тестом и упадёт ли он, если требование нарушить». Требование без исполнителя —
**граница в devlog**, молчаливого третьего варианта нет.

- [ ] **Шаг 5: `just ci` целиком**

Шагами и в форме CI. Вердикт — по напечатанным числам. Ожидание: `EXIT=0`,
6 skipped и ни одним больше.

- [ ] **Шаг 6: стенд**

Спросить у пользователя разрешение на операции с `gca_dev` **до** действий
(в Ф4б это потребовало отдельного разрешения). Замерить состояние **до**:
объектов, договоров, смет, каталожных строк, `import_jobs`. Затем
`alembic upgrade head` (**только upgrade**, рейса на стенде нет), завести через
**интерфейс** площади и условия на одном договоре, прочитать из БД, сверить
счётчики «до и после». Стенд не чистить.

- [ ] **Шаг 7: devlog и рамка**

Devlog по образцу [Ф4б](../../devlog/2026-08-08-vat-rate.md): что сделано по
задачам, замеры (числа после каждой задачи), негативные проверки таблицей,
соответствие «требование → тест», отступления от плана, названные границы,
найденные грабли. В `phase7-frame.md` — врезка «Ф5 реализована» со ссылкой.

- [ ] **Шаг 8: PR**

Описание со ссылками на спеку и план. **Числа в описании — из последнего
прогона**, а не из предыдущего (замечание внешнего ревью Ф4б). Ветка после
мержа удаляется; мержит **пользователь**.

---

## Чего этот план сознательно не делает

- **Не строит раздел «Объекты»** — список, карточку, маршрут и пункт меню.
  Ввод ТЭП идёт через диалог с карточки договора (спека §2.9); объект без
  договоров остаётся недостижимым для правки (граница §5 п. 8).
- **Не делает backfill** существующих объектов — они остаются `NULL`/`NULL`,
  то есть «ТЭП не заведены» (граница §5 п. 5).
- **Не заводит признак происхождения значения** (`'manual'`/`'llm'`) под
  будущий парсер договоров: рамка фазы, «вне скоупа v1» п. 6 — под него ничего
  не закладывать.
- **Не хранит стадию и дату ТЭП** — решение пользователя (граница §5 п. 2).
- **Не трогает `proposals.vat_rate`** и вообще ставку НДС: другой источник.
- **Не проверяет печатную раскладку А4** — печатать в Ф5 нечего; обязательство
  переходит в Ф6 вместе с зажимом комментариев условий по высоте.
- **Не чинит `qk.passport.all` под новый паспорт Ф6** — корень, который Ф6
  заведёт для своего экрана, в этот список автоматически не попадёт, и это
  записано в §7 спеки как её обязательство.
