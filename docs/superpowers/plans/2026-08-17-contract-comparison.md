# Сравнение договоров — план реализации

> **Для исполнителя:** задачи идут по порядку, каждая замкнута и заканчивается
> коммитом. Шаги с чекбоксами (`- [ ]`) отмечаются по мере выполнения. Код тестов
> ниже — **проверенный эскиз**: каждое имя сверено `grep`-ом по репозиторию
> 2026-08-17; где символ заводится этой фичей, это сказано прямо.

**Цель:** страница `/compare` ставит рядом несколько договоров: строки — дерево
статей классификатора, колонки — договоры, в ячейке суммы и ₽/м²; тот же агрегат
выгружается в Excel.

**Архитектура:** на каждый договор строится **нетто-дерево** (`v_category_totals`
→ нетто по базе группы → `services.category_rollup.build_tree`); поверх
посконтрактных деревьев считается **союз узлов выборки** и матрица ячеек; режим
показа применяется множителем поверх нетто, поэтому медиана (всегда нетто) не
зависит от режима. Экран и Excel читают один агрегат.

**Стек:** FastAPI, SQLAlchemy 2.x sync, psycopg3, PostgreSQL 16, openpyxl;
React + TS, TanStack Query, shadcn/ui, vitest, MSW.

**Спека:** [docs/superpowers/specs/2026-08-17-contract-comparison-design.md](../specs/2026-08-17-contract-comparison-design.md)
— план аргументирует от неё, читать обе. Спека прошла пять кругов внешнего ревью;
все её §-ссылки ниже точные.

**Ветка:** `feat/contract-compare-scaffold` (создана, спека и макет закоммичены).

> **СТАТУС: ЧЕРНОВИК, гейт 3 НЕ пройден.** Задачи 1–2 доведены до требуемой
> плотности (код тестов, ожидаемое красное, реализация). Задачи 3–9 — **скелет**:
> в них стоят `...` вместо кода и свёрнутые шаги «красное → реализация →
> зелёное». По §9.1 этого недостаточно: несверенный или отсутствующий код теста
> исполнитель читает как готовый, и на фиче пересчёта НДС так ушло шесть кругов.
> Черновик закоммичен, чтобы не потерять структуру и результаты сверки имён;
> отдавать его исполнителю нельзя.
>
> **Известный пробел покрытия:** DoD 13 спеки (объект без `area_total_sp` → ₽/м²
> прочерк и вне медианы) не имеет своего шага ни в одной задаче. Закрывается
> шагом в задачу 3 при доведении.

---

## Global Constraints

- **Миграций у фичи нет.** Схема не меняется. Если задача потребовала миграцию —
  это ошибка в задаче, остановиться и спросить.
- **`CategoryRef` объявлен в ДВУХ модулях, и они разные.**
  `services.category_resolution.CategoryRef` — два поля (`id`, `title`);
  `services.category_rollup.CategoryRef` — шесть (`id`, `code`, `title`,
  `parent_id`, `is_bucket`, `sort_order`). Нужен **второй**:
  `from services.category_rollup import CategoryRef`. Импорт первого даст
  невнятную ошибку конструктора.
- **Деньги** — `Decimal` в Python, строки в JSON. Любой эндпоинт с деньгами идёт
  через `responses.decimal_json` (§10 `AGENTS.md`), иначе `jsonable_encoder`
  превратит `Decimal` во `float`.
- **Ось сравнения — всегда нетто**, в любом режиме показа (спека §2.5 правило 2).
- **`SECRET_KEY` в командах — не короче 32 символов**, иначе `Settings` падает
  `ValidationError` на сборке conftest, а не на тестах.
- **Каждому новому файлу в `backend/tests/integration/`** обязателен
  `pytestmark = pytest.mark.integration` — без него `pytest -m integration`
  молча пропустит файл.
- **Фронтенд гоняется из `frontend/`**; типы — `npx tsc -b --noEmit` (голый
  `npx tsc --noEmit` проверяет ноль файлов).
- **Только shadcn/ui.** `checkbox`, `table`, `select`, `label`, `button`,
  `switch`, `tabs` уже лежат в `frontend/src/components/ui/`.
- **prettier в проекте не настроен** — не запускать.
- **Перед пушем `just ci`**, шаги по отдельности (конвейер с `| tail` вернёт код
  `tail`). Правки только в `docs/` от `just ci` освобождены (§9.3).
- Кириллица в выводе дочернего python — `PYTHONIOENCODING=utf-8`.

**Команда бэкенд-тестов** (PowerShell, из `backend/`):

```
$env:PYTHONIOENCODING='utf-8'; $env:DATABASE_URL='postgresql+psycopg://postgres@localhost:5459/postgres'; $env:TEST_DATABASE_URL='postgresql+psycopg://postgres@localhost:5459/gca_test'; $env:SECRET_KEY='test-secret-key-for-ci-0123456789abcdef'; uv run pytest <путь> -v
```

---

## Файловая карта

| Файл | Что с ним |
|---|---|
| `backend/crud/comparison.py` | **новый**: чтение VIEW в нетто, нетто-дерево на договор, союз выборки, корзины, режимы, медианы |
| `backend/routers/analytics.py` | эндпоинт `GET /comparison` |
| `backend/services/excel_comparison.py` | **новый**: лист сравнения |
| `backend/routers/reports.py` | эндпоинт `GET /comparison-xlsx` |
| `backend/tests/integration/test_comparison_aggregate.py` | **новый**: дерево, союз, состояния ячеек |
| `backend/tests/integration/test_comparison_vat.py` | **новый**: корзины, режимы, медианы |
| `backend/tests/integration/test_comparison_api.py` | **новый**: эндпоинт, выборка, права |
| `backend/tests/integration/test_comparison_excel.py` | **новый**: лист |
| `AGENTS.md` | ревизия v6.8 (§10, §7, §7.6) |
| `frontend/src/services/queryKeys.ts` | корень `comparison` |
| `frontend/src/services/queries.ts` | `useComparison` |
| `frontend/src/services/api/domain.ts` | вызов эндпоинта |
| `frontend/src/types/domain.ts` | типы ответа |
| `frontend/src/pages/contracts/ContractsPage.tsx` | чекбоксы, две кнопки |
| `frontend/src/pages/compare/ComparePage.tsx` | **новая** страница |
| `frontend/src/App.tsx` | маршрут `/compare` |
| `frontend/src/test/handlers.ts` | обработчик MSW |
| `docs/devlog/2026-08-17-contract-comparison.md` | **новый** devlog |

**Существующее, что переиспользуется без правок:**
`services.category_rollup` (`build_tree`, `CategoryNode`, `CategoryRef`,
`DirectTotals`, `SOURCE_POSITIONS`, `SOURCE_ADDITIONAL_WORKS`),
`money.vat` (`gross_to_net:74`, `restate_gross:92`, `effective_display_rate:126`,
`quantize_money:149`), `responses.decimal_json:93`, `crud.common.DomainError:26`,
`auth.get_current_user:23`, `services.excel` (`write_cell:124`,
`write_banner:149`, `write_column_headers:160`, `apply_column_widths:171`,
`workbook_bytes:176`), `routers.reports._xlsx:43`, `_safe_filename_part:51`.

**Осознанно НЕ переиспользуется:** `crud.project_passport._direct_totals:587` —
приватная функция чужого модуля, и она restate-ит суммы под одну ставку сметы.
Сравнению нужны нетто-суммы, из которых потом выводятся все три режима, поэтому
чтение VIEW пишется своё. Чистый `build_tree` переиспользуется как есть.

---

## Task 1: Нетто-дерево на договор

**Files:**
- Create: `backend/crud/comparison.py`
- Test: `backend/tests/integration/test_comparison_aggregate.py` (новый)

**Interfaces:**
- Produces: `crud.comparison.net_tree(db, estimate_id) -> tuple[tuple[CategoryNode, ...], dict[str, DirectTotals] | None]`
  — дерево статей в нетто и прямые суммы «Нераспределённого» (`None`, если его
  нет). Заводится этой задачей.
- Produces: `crud.comparison.CellState` — enum-подобные строковые константы
  `ABSENT`, `ZERO`, `VALUE`. Заводится этой задачей.
- Consumes: `services.category_rollup.build_tree`, `CategoryRef`, `DirectTotals`,
  `SOURCE_POSITIONS`, `SOURCE_ADDITIONAL_WORKS`; `money.vat.gross_to_net`;
  `models.WorkCategory` — все существуют.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/integration/test_comparison_aggregate.py`:

```python
"""Агрегат сравнения: нетто-дерево, союз выборки, состояния ячеек (спека §2.1)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from crud import comparison as cmp
from models import ImportJobStatus

pytestmark = pytest.mark.integration


def test_net_tree_root_equals_subtree_sum(db_session, factories):
    """Корень равен сумме поддерева — в НЕТТО (спека §2.1, DoD 4)."""
    estimate = factories.EstimateFactory.create()
    # `_seed_priced_positions` заводится этим тестовым файлом ниже.
    _seed_priced_positions(db_session, factories, estimate, [("1", "1000.00"), ("1.1", "500.00")])
    db_session.flush()

    roots, unallocated = cmp.net_tree(db_session, estimate.id)

    root = next(n for n in roots if n.ref.code == "1")
    assert root.total == sum(c.total for c in root.children) + (root.own or Decimal(0))
    assert unallocated is None
```

**Исполнителю: `_seed_priced_positions` придётся написать самому** — фабрики
позиций с привязкой к статье в проекте нет. Опора: `PositionItemFactory`
существует (`backend/tests/factories.py`), колонка привязки —
`position_items.work_category_id` (миграция 0006). Хелпер обязан ставить
`vat_rate` предложению, иначе `vat_rate_base` окажется `NULL` и ячейка станет
неполной по §2.1.3 — тест упадёт не по той причине.

- [ ] **Step 2: Прогнать и увидеть красное**

Ожидание: `ModuleNotFoundError: No module named 'crud.comparison'`.

- [ ] **Step 3: Реализовать `net_tree`**

```python
"""Агрегат сравнения договоров (спека 2026-08-17).

Нетто — единственная ось, на которой считается медиана (спека §2.5 правило 2),
поэтому дерево строится СРАЗУ в нетто: суммы VIEW делятся на базу своей группы
до свёртки. Режим показа применяется множителем поверх готового нетто-дерева
(задача 3) — так все три режима выводятся из одного расчёта, а медиана не
зависит ни от одного из них.
"""
import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import WorkCategory
from money.vat import gross_to_net
from services.category_rollup import (
    SOURCE_ADDITIONAL_WORKS,
    SOURCE_POSITIONS,
    CategoryNode,
    CategoryRef,
    DirectTotals,
    build_tree,
)

ABSENT = "absent"
ZERO = "zero"
VALUE = "value"

V_CATEGORY_TOTALS = sa.table(
    "v_category_totals",
    sa.column("estimate_id", sa.BigInteger),
    sa.column("vat_rate_base", sa.Numeric),
    sa.column("work_category_id", sa.BigInteger),
    sa.column("source", sa.Text),
    sa.column("amount", sa.Numeric),
    sa.column("row_count", sa.Integer),
    sa.column("rows_with_amount", sa.Integer),
    sa.column("rows_not_finite", sa.Integer),
)


def _category_refs(db: Session) -> list[CategoryRef]:
    return [
        CategoryRef(
            id=c.id, code=c.code, title=c.title, parent_id=c.parent_id,
            is_bucket=c.is_bucket, sort_order=c.sort_order,
        )
        for c in db.execute(sa.select(WorkCategory)).scalars().all()
    ]


def net_tree(db: Session, estimate_id: int):
    """Дерево статей одной сметы в НЕТТО плюс прямые суммы «Нераспределённого».

    `amount is None` — отсутствующее слагаемое, не ноль (инвариант VIEW).
    Неизвестная `vat_rate_base` НЕ подменяется догадкой: сумма выпадает, а
    счётчики остаются — неполноту распознаёт задача 2 по `rows_priced < rows`.
    """
    rows = db.execute(
        sa.select(V_CATEGORY_TOTALS).where(V_CATEGORY_TOTALS.c.estimate_id == estimate_id)
    ).all()

    direct: dict[int | None, dict[str, DirectTotals]] = {}
    for r in rows:
        net = (
            None
            if r.amount is None or r.vat_rate_base is None
            else gross_to_net(r.amount, r.vat_rate_base)
        )
        bucket = direct.setdefault(r.work_category_id, {})
        bucket[r.source] = DirectTotals(
            amount=net,
            row_count=r.row_count,
            rows_with_amount=0 if net is None else r.rows_with_amount,
            rows_not_finite=r.rows_not_finite,
        )

    unallocated = direct.pop(None, None)
    return build_tree(_category_refs(db), direct), unallocated


def own_money(node: CategoryNode):
    """Собственные деньги узла: `own` (позиции) ПЛЮС прямые допработы.

    `category_rollup` кладёт в `own` только ветку `positions`, а прямые
    допработы входят в `total`, минуя `own` (спека §2.1). Сводить родителя с
    детьми по одному `own` нельзя — отсюда эта функция.
    """
    return node.total - sum(c.total for c in node.children if c.total is not None) \
        if node.total is not None else None
```

- [ ] **Step 4: Прогнать — зелёное**

- [ ] **Step 5: Тест собственных денег узла**

```python
def test_own_money_includes_direct_additional_works(db_session, factories):
    """`own` — только позиции; прямые допработы входят в total (спека §2.1)."""
    estimate = factories.EstimateFactory.create()
    _seed_priced_positions(db_session, factories, estimate, [("3.2", "1000.00")])
    _seed_additional_work(db_session, factories, estimate, "3.2", "200.00")
    db_session.flush()

    roots, _ = cmp.net_tree(db_session, estimate.id)
    node = _find(roots, "3.2")

    assert node.own is not None
    assert cmp.own_money(node) > node.own, "прямые допработы обязаны войти"
```

**Замер, объясняющий, зачем этот тест:** на стенде допработ 4 группы (статьи 3.2
и 12.99), обе на листьях. То есть путь исполняется на живых данных.

- [ ] **Step 6: Прогнать — зелёное. Коммит**

```bash
git add backend/crud/comparison.py backend/tests/integration/test_comparison_aggregate.py
git commit -m "feat(comparison): нетто-дерево статей на договор"
```

---

## Task 2: Союз узлов выборки, состояния ячеек, синтетические строки

**Files:**
- Modify: `backend/crud/comparison.py`
- Test: `backend/tests/integration/test_comparison_aggregate.py`

**Interfaces:**
- Produces: `crud.comparison.build_rows(trees: dict[int, tuple[CategoryNode, ...]]) -> list[Row]`
  — строки союза выборки в порядке `sort_order`, с уровнем вложенности.
  `Row` — dataclass с полями `code`, `title`, `level`, `kind`
  (`"category" | "own" | "unallocated"`), `category_id`. Заводится этой задачей.
- Consumes: Task 1.

- [ ] **Step 1: Тест союза**

```python
def test_union_row_is_present_when_any_contract_has_it(db_session, factories):
    """Строка есть, если статья непуста хотя бы у одного договора (спека §2.1.1)."""
    a, b = factories.EstimateFactory.create(), factories.EstimateFactory.create()
    _seed_priced_positions(db_session, factories, a, [("1", "100.00"), ("2", "200.00")])
    _seed_priced_positions(db_session, factories, b, [("1", "300.00")])
    db_session.flush()

    trees = {a.id: cmp.net_tree(db_session, a.id)[0], b.id: cmp.net_tree(db_session, b.id)[0]}
    rows = cmp.build_rows(trees)
    codes = [r.code for r in rows if r.kind == "category"]

    assert "2" in codes, "статья есть у одного — строка обязана быть у обоих"
    assert codes == sorted(codes, key=_sort_key), "порядок — sort_order классификатора"
```

- [ ] **Step 2: Тест состояний ячейки**

```python
def test_absent_and_zero_are_different_states(db_session, factories):
    """Статьи нет → ABSENT; есть и расценена в ноль → ZERO (спека §2.1.2)."""
    a, b = factories.EstimateFactory.create(), factories.EstimateFactory.create()
    _seed_priced_positions(db_session, factories, a, [("1", "0.00"), ("2", "200.00")])
    _seed_priced_positions(db_session, factories, b, [("2", "300.00")])
    db_session.flush()

    trees = {a.id: cmp.net_tree(db_session, a.id)[0], b.id: cmp.net_tree(db_session, b.id)[0]}
    cells = cmp.cells_for(trees, code="1")

    assert cells[a.id].state == cmp.ZERO
    assert cells[b.id].state == cmp.ABSENT
```

**Замер, задающий ожидание:** на сетке стенда 5×215 состояний 497 `ABSENT`,
89 `ZERO`, 489 `VALUE`. Оба нулевых состояния исключаются из медианы, но
показываются по-разному.

- [ ] **Step 3: Тест неполноты — четыре причины списком**

```python
@pytest.mark.parametrize(
    "seed, expected",
    [
        ("unpriced", "unpriced_rows"),
        ("not_finite", "not_finite_rows"),
        ("no_vat_base", "vat_base_unknown"),
    ],
)
def test_incomplete_cell_is_empty_with_reason(db_session, factories, seed, expected):
    """Неполная ячейка пуста ЦЕЛИКОМ и несёт причину (спека §2.1.3, DoD 8, 8а)."""
    estimate = factories.EstimateFactory.create()
    _seed_incomplete(db_session, factories, estimate, seed)  # хелпер этого файла
    db_session.flush()

    trees = {estimate.id: cmp.net_tree(db_session, estimate.id)[0]}
    cell = cmp.cells_for(trees, code="1")[estimate.id]

    assert cell.amount is None, "частичная сумма не показывается как полная"
    assert expected in cell.incomplete_reasons


def test_two_reasons_are_both_reported(db_session, factories):
    """Причины совмещаются списком, а не прячутся друг за другом (DoD 8д)."""
    estimate = factories.EstimateFactory.create()
    _seed_incomplete(db_session, factories, estimate, "unpriced_and_not_finite")
    db_session.flush()

    trees = {estimate.id: cmp.net_tree(db_session, estimate.id)[0]}
    cell = cmp.cells_for(trees, code="1")[estimate.id]

    assert set(cell.incomplete_reasons) == {"unpriced_rows", "not_finite_rows"}
```

**Замер:** на стенде ни одного такого случая (474 группы, частично расценённых 0,
нефинитных 0). Правило доменное, поэтому фикстуры искусственные — иначе тест
проверял бы отсутствие данных, а не правило.

- [ ] **Step 4: Тест синтетических строк**

```python
def test_own_row_appears_for_parents_with_own_money(db_session, factories):
    """«Без подстатьи» — одна строка, own + прямые допработы (спека §2.1)."""
    estimate = factories.EstimateFactory.create()
    _seed_priced_positions(db_session, factories, estimate, [("3", "500.00"), ("3.1", "100.00")])
    db_session.flush()

    trees = {estimate.id: cmp.net_tree(db_session, estimate.id)[0]}
    rows = cmp.build_rows(trees)

    own_rows = [r for r in rows if r.kind == "own" and r.code == "3"]
    assert len(own_rows) == 1


def test_unallocated_row_present_and_in_total_but_not_in_median(db_session, factories):
    """«Нераспределённое» входит в итог, но не в медиану (спека §2.1.4, DoD 5в)."""
    estimate = factories.EstimateFactory.create()
    _seed_priced_positions(db_session, factories, estimate, [("1", "100.00")])
    _seed_unallocated(db_session, factories, estimate, "50.00")  # work_category_id = NULL
    db_session.flush()

    trees = {estimate.id: cmp.net_tree(db_session, estimate.id)[0]}
    rows = cmp.build_rows(trees)

    assert any(r.kind == "unallocated" for r in rows)
```

**Замер:** «Нераспределённого» на стенде нет ни одной группы, но `AGENTS.md` §10
не гарантирует глобального нуля — фикстура искусственная.

- [ ] **Step 5: Реализовать `build_rows` и `cells_for`**

Правила, которые обязаны быть в коде (все из спеки, не выдумывать):

1. союз — узел попадает в строки, если `rows > 0` хотя бы у одного договора
   выборки; порядок — `sort_order`, вложенность — по `parent_id`;
2. `ABSENT`, если узла нет в дереве договора; `ZERO`, если есть и `total == 0`;
3. неполнота — четыре причины **списком** (`unpriced_rows`, `not_finite_rows`,
   `vat_base_unknown`, `display_rate_undefined`); при непустом списке
   `amount is None`;
4. строка `kind="own"` у узла, у которого есть и дети, и собственные деньги;
   союз применяется к ней так же;
5. строка `kind="unallocated"` одна на таблицу, если остаток непуст хотя бы у
   одного договора.

- [ ] **Step 6: Прогнать — зелёное. Коммит**

```bash
git add backend/crud/comparison.py backend/tests/integration/test_comparison_aggregate.py
git commit -m "feat(comparison): союз узлов выборки, состояния ячеек, синтетические строки"
```

---

## Task 3: Корзины, режимы НДС, медианы

**Files:**
- Modify: `backend/crud/comparison.py`
- Test: `backend/tests/integration/test_comparison_vat.py` (новый)

**Interfaces:**
- Produces: `crud.comparison.build_comparison(db, contract_ids, *, bucket, vat_mode, single_rate=None) -> dict`
  — готовый агрегат: колонки, строки, ячейки, медианы, подписи. Заводится этой
  задачей.
- Produces: `crud.comparison.rate_options(db, contract_ids) -> tuple[list[Decimal], Decimal | None]`
  — список ставок и предвыбор. Заводится этой задачей.

- [ ] **Step 1: Тест корзин**

```python
def test_buckets_sum_to_total(committing_db, committing_factories):
    """ДГП + ДС = Итого на фикстуре с допсоглашением (DoD 5)."""
    contract = committing_factories.ContractFactory.create()
    base = committing_factories.EstimateFactory.create(contract=contract, amendment_no=None)
    amd = committing_factories.EstimateFactory.create(contract=contract, amendment_no=1)
    ...
```

**Исполнителю:** допсоглашений в системе НЕТ ни одного (замер спеки §6), поэтому
все тесты корзин идут на искусственных фикстурах с `amendment_no > 0`. На стенде
этот путь не исполняется — не пытаться проверить прогоном.

- [ ] **Step 2: Тест режимов и правила «медиана всегда нетто»**

```python
def test_deviations_are_identical_in_all_three_modes(committing_db, committing_factories):
    """Отклонения не зависят от режима показа (спека §2.5 правило 2, DoD 10)."""
    ...
    devs = {}
    for mode in ("own", "single", "net"):
        agg = cmp.build_comparison(committing_db, ids, bucket="total", vat_mode=mode,
                                   single_rate=Decimal(20) if mode == "single" else None)
        devs[mode] = [c["deviation_pct"] for c in _row(agg, "1")["cells"]]
    assert devs["own"] == devs["single"] == devs["net"]
```

- [ ] **Step 3: Тест `display_rate_undefined` — гасит ячейку, а не столбец**

```python
def test_undefined_display_rate_kills_cell_not_column(committing_db, committing_factories):
    """Фикстура спеки DoD 8е: статья и в ДГП, и в ДС без определённой ставки.

    Контрольный случай в том же тесте: статья, которая есть ТОЛЬКО в ДГП, в
    режиме «ДС» даёт НОЛЬ, а не причину — проблемная смета по ней пуста.
    Прежняя редакция спеки утверждала здесь причину, и это была ошибка.
    """
    ...
```

- [ ] **Step 4: Тест списка ставок и предвыбора**

```python
def test_rate_options_fall_back_to_base_rates(committing_db, committing_factories):
    """Ставок показа нет ни у одной сметы → список из базовых, предвыбор есть (DoD 8ж)."""
    ...
    options, preselect = cmp.rate_options(committing_db, ids)
    assert options, "список не может быть пустым, если есть показываемые числа"
    assert preselect is not None
```

**Проверенное число:** на стенде ставки показа дают ничью (22 у двух договоров,
20 у двух, 16 у одного), и по правилу «при равенстве — бо́льшая» предвыбор равен
**22**. Не 20 — прежняя редакция спеки называла 20 ошибочно.

- [ ] **Step 5: Реализовать. Прогнать — зелёное. Коммит**

```bash
git add backend/crud/comparison.py backend/tests/integration/test_comparison_vat.py
git commit -m "feat(comparison): три корзины, три режима НДС, медианы по нетто"
```

---

## Task 4: Эндпоинт

**Files:**
- Modify: `backend/routers/analytics.py`
- Test: `backend/tests/integration/test_comparison_api.py` (новый)

**Interfaces:**
- Produces: `GET /api/v1/analytics/comparison` с параметрами
  `ids` (список) **либо** `all=1` плюс `q`, `object_id`, `contractor_id`,
  `rate_class_id`; плюс `bucket`, `vat_mode`, `single_rate`.
- Consumes: Task 3, `responses.decimal_json`, `auth.get_current_user`.

- [ ] **Step 1: Тесты**

```python
def test_ids_and_filter_selection_agree(client, factories):
    """Обе формы выборки на одном множестве дают одинаковый ответ (DoD 1)."""
    ...

def test_column_order_is_signed_date_desc(client, factories):
    """Порядок колонок — signed_date DESC, при равенстве id DESC (DoD 3)."""
    ...

def test_all_four_filters_are_accepted(client, factories):
    """q, object_id, contractor_id, rate_class_id — все четыре (DoD 22)."""
    ...

def test_member_can_read_comparison(client, factories):
    """Чтение доступно и member (спека §2.9, DoD 14)."""
    client.auth_state["role"] = UserRole.member
    assert client.get("/api/v1/analytics/comparison?ids=1").status_code == 200
```

- [ ] **Step 2: Прогнать — красное. Step 3: Реализовать. Step 4: Зелёное. Step 5: Коммит**

```bash
git commit -m "feat(comparison): эндпоинт пакетной выборки"
```

---

## Task 5: Excel

**Files:**
- Create: `backend/services/excel_comparison.py`
- Modify: `backend/routers/reports.py`
- Test: `backend/tests/integration/test_comparison_excel.py` (новый)

- [ ] **Step 1: Тесты**

```python
def test_sheet_numbers_match_the_screen(committing_client, ...):
    """Один агрегат — два представления; числа совпадают (DoD 19)."""

def test_sheet_prints_three_buckets_each_with_own_deviation(...):
    """У каждой корзины своя сумма, ₽/м² и отклонение (DoD 21)."""

def test_sheet_labels_vat_mode_and_composition(...):
    """Подпись режима, а в «своей ставке» — состав «ДГП 20 % · ДС 22 %» (DoD 20)."""
```

- [ ] **Steps 2–5: красное → реализация → зелёное → коммит**

```bash
git commit -m "feat(comparison): выгрузка сравнения в Excel"
```

---

## Task 6: Ревизия `AGENTS.md` до v6.8

**Не последняя задача намеренно:** пока правка не внесена, реализация формально
противоречит документу (спека §2.10).

**Files:** Modify: `AGENTS.md`

- [ ] **Step 1: Врезка версии** — первой в блоке цитат, перед v6.7.
- [ ] **Step 2: §10, налоговый состав денег.** Дописать: у много-договорных
  **сравнительных** поверхностей ось сравнения всегда нетто; **показ** может
  иметь объявленные режимы; в режиме «своя ставка» итог по договору вправе
  складывать суммы разного состава при обязательной подписи — тем же основанием,
  каким это разрешено обзорной главной. **Раздел именно §10** («Definition of
  Done», строка 472), не §3 — там только правило `Decimal`.
- [ ] **Step 3: §7** — пункт про страницу сравнения: маршрут `/compare`, вход из
  списка, пункта меню нет.
- [ ] **Step 4: §7.6** — «два отчёта — два файла» → три.
- [ ] **Step 5: Коммит**

```bash
git add AGENTS.md
git commit -m "docs: AGENTS.md v6.8 — режимы показа у сравнительных поверхностей"
```

---

## Task 7: Список договоров — выбор и вход

**Files:**
- Modify: `frontend/src/pages/contracts/ContractsPage.tsx`,
  `frontend/src/services/queryKeys.ts`, `frontend/src/App.tsx`,
  `frontend/src/test/handlers.ts`
- Test: `frontend/src/pages/contracts/ContractsPage.test.tsx`

- [ ] **Step 1: Тесты** — колонка чекбоксов; «Сравнить выбранные (N)» неактивна
  при нуле выбранных; «Сравнить всё по фильтру» уходит с текущими параметрами;
  обе кнопки видны и `member`.
- [ ] **Steps 2–5: красное → реализация → зелёное → коммит**

```bash
git commit -m "feat(contracts): выбор договоров для сравнения"
```

---

## Task 8: Страница сравнения

**Files:**
- Create: `frontend/src/pages/compare/ComparePage.tsx`
- Modify: `frontend/src/services/queries.ts`,
  `frontend/src/services/api/domain.ts`, `frontend/src/types/domain.ts`
- Test: `frontend/src/pages/compare/ComparePage.test.tsx` (новый)

- [ ] **Step 1: Тесты** — по умолчанию видны только корни; раскрытие даёт второй
  уровень; переключатель корзины меняет все ячейки; режим НДС и ставка
  восстанавливаются из URL; первая колонка закреплена; подсветка появляется
  только при трёх и более сопоставимых.

**Предпосылка, требующая замера, а не веры:** закрепление первой колонки в jsdom
не наблюдаемо (`position: sticky` не вычисляется). Проверять классом на элементе,
а живое поведение — на стенде задачей 9. `@media print` в jsdom так же
ненаблюдаем (§11 `AGENTS.md`).

- [ ] **Steps 2–5: красное → реализация → зелёное → коммит**

```bash
git commit -m "feat(compare): страница сравнения договоров"
```

---

## Task 9: Приёмка

- [ ] **Step 1: `just ci`** — шаги по отдельности.
- [ ] **Step 2: Прогон на стенде `gca_dev`.** Что проверяется живьём и только
  живьём: читаемость таблицы при пяти колонках и раскрытой ветке; горизонтальная
  прокрутка с закреплённой колонкой; совпадение чисел листа Excel с экраном;
  переключение режимов НДС без перезагрузки. **Браузером** — Playwright через
  системный Chrome (`channel: "chrome"`), перезагрузка ловится счётчиком полных
  загрузок документа, а не «миганием».
- [ ] **Step 3: Devlog** `docs/devlog/2026-08-17-contract-comparison.md` по §9.2:
  что сделано, замеры, отступления от плана, найденные грабли. Обязательно
  назвать: границу инфляции (§4.1 спеки), пустую корзину ДС на живых данных и то,
  какие правила проверены только искусственными фикстурами.
- [ ] **Step 4: Коммит, пуш, PR** со ссылками на спеку и план (§9.3).

---

## Что НЕ входит

Миграций нет. «ДС на рассмотрении», ₽/м² полезной площади, печать, drill-down в
позиции, поправка на инфляцию — за скоупом (спека §4). Маршрут `/matrix` без
ссылки в интерфейсе не чинится.
