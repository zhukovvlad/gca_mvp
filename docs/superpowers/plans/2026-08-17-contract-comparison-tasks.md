# Сравнение договоров — задачи 2–9 (детализация)

> Продолжение [основного плана](2026-08-17-contract-comparison.md): архитектура,
> Global Constraints, файловая карта и задача 1 — там. Здесь расписаны шаги
> задач 2–9. Читать вместе; при расхождении побеждает спека.

**Спека:** [2026-08-17-contract-comparison-design.md](../specs/2026-08-17-contract-comparison-design.md)

---

## Ловушки фикстур, вскрытые сверкой (читать до первой строки кода)

Три факта о `v_category_totals` (миграция 0012), без которых половина тестов
окажется вакуозной. Все три проверены чтением определения VIEW, а не догадкой.

### 1. У позиций статья берётся с РАЗДЕЛА, а не с позиции

```sql
FROM position_items pi
LEFT JOIN position_items ch ON ch.id = pi.chapter_item_id AND ch.proposal_id = pi.proposal_id
...
ch.work_category_id
WHERE pi.is_chapter = false
```

Позиция с собственным `work_category_id`, но без `chapter_item_id`, даёт в VIEW
`work_category_id = NULL` — то есть **«Нераспределённое»**, а не свою статью.
Фикстура обязана строить пару: строка-раздел (`is_chapter=True`,
`work_category_id=<статья>`) и позиции (`is_chapter=False`,
`chapter_item_id=<id раздела>`, тот же `proposal_id`).

### 2. У допработ статья берётся С САМОЙ СТРОКИ

`aw.work_category_id` напрямую. Асимметрия с позициями — не описка VIEW, и
хелперы обязаны быть разными.

Разрешённая допработа со статьёй проходит только при заполненных
`chapter_ref_raw` **и** `raw_line`: CHECK `ck_estimate_additional_works_unresolved_ref`
требует первое, `ck_..._raw_line_pairs` — второе. Плюс `ordinal > 0`,
`total_amount >= 0`, непустой `title`.

### 3. База НДС — `COALESCE(e.vat_rate_base_override, p.vat_rate)`

`ProposalFactory` **не задаёт** `vat_rate` (проверено: в фабрике только `lot` и
`contractor`). Значит по умолчанию база `NULL`, ячейка станет неполной по
`vat_base_unknown`, и тест про суммы упадёт не по той причине. **Каждый хелпер
задаёт `vat_rate` явно.**

---

## Task 2: Роллап выборки — фикстуры, чтение VIEW, нетто-деревья

**Files:**
- Create: `backend/tests/comparison_fixtures.py` (без префикса `test_` — pytest
  не должен его собирать)
- Create: `backend/crud/comparison.py`
- Test: `backend/tests/integration/test_comparison_rollup.py`

**Interfaces:** см. основной план, задача 2 (`DirectBranch`, `EstimateRollup`,
`load_rollups`, `own_net`).

- [ ] **Step 1: Модуль сценарных сборщиков**

`backend/tests/comparison_fixtures.py`:

```python
"""Сценарные сборщики данных для тестов сравнения договоров.

БЕЗ префикса `test_`: pytest не собирает этот модуль как набор тестов.
Универсальные фабрики сущностей живут в `tests/factories.py` и здесь только
вызываются — дублировать их нельзя, разъедутся.

Каждый сборщик задаёт ЯВНО: `vat_rate` предложения, статью, `is_chapter`, суммы
и связи лот → предложение → раздел → позиция. Причина в шапке плана задач:
`ProposalFactory` не задаёт `vat_rate`, а статья позиции берётся VIEW с её
раздела.
"""
from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa

from models import EstimateAdditionalWork, WorkCategory

VAT_20 = Decimal("20")
VAT_22 = Decimal("22")


def category_id(db, code: str) -> int:
    """id статьи классификатора по коду. Классификатор засеян миграцией 0005."""
    return db.execute(
        sa.select(WorkCategory.id).where(WorkCategory.code == code)
    ).scalar_one()


def make_proposal(factories, *, estimate, vat_rate=VAT_20, lot_key=None):
    """Лот и предложение с ЯВНОЙ ставкой НДС."""
    lot = factories.LotFactory.create(estimate=estimate, **({"lot_key": lot_key} if lot_key else {}))
    return factories.ProposalFactory.create(lot=lot, vat_rate=vat_rate)


def seed_chapter_with_positions(db, factories, *, proposal, code, amounts):
    """Раздел со статьёй `code` и позиции под ним.

    Статья ставится РАЗДЕЛУ: VIEW читает `ch.work_category_id` через
    `chapter_item_id`. Позиция со своей `work_category_id` и без раздела попала бы
    в «Нераспределённое».
    """
    chapter = factories.PositionItemFactory.create(
        proposal=proposal, is_chapter=True,
        work_category_id=category_id(db, code),
        total_cost_total=None, unit_cost_total=None,
    )
    db.flush()
    items = [
        factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, chapter_item_id=chapter.id,
            total_cost_total=Decimal(a) if a is not None else None,
        )
        for a in amounts
    ]
    db.flush()
    return chapter, items


def seed_unallocated_positions(db, factories, *, proposal, amounts):
    """Позиции БЕЗ раздела → в VIEW `work_category_id IS NULL` (§2.1.4)."""
    items = [
        factories.PositionItemFactory.create(
            proposal=proposal, is_chapter=False, chapter_item_id=None,
            total_cost_total=Decimal(a),
        )
        for a in amounts
    ]
    db.flush()
    return items


def seed_additional_work(db, factories, *, proposal, code, amount, ordinal=1):
    """Разрешённая допработа со статьёй.

    `chapter_ref_raw` и `raw_line` обязательны: без первого не пустит CHECK
    `ck_estimate_additional_works_unresolved_ref`, без второго —
    `ck_estimate_additional_works_raw_line_pairs`.
    """
    work = EstimateAdditionalWork(
        proposal_id=proposal.id, ordinal=ordinal,
        title="Дополнительные работы",
        total_amount=Decimal(amount),
        work_category_id=category_id(db, code),
        chapter_ref_raw=code,
        raw_line=f"{code} Дополнительные работы",
    )
    db.add(work)
    db.flush()
    return work
```

- [ ] **Step 2: Тест предпосылки фикстур — до всех остальных**

Первым идёт тест, который проверяет **сами сборщики**: иначе неверная фикстура
даст зелёный тест на неверных данных.

```python
"""Роллап выборки: чтение VIEW, накопление групп, нетто (спека §2.1)."""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud import comparison as cmp
from tests import comparison_fixtures as fx

pytestmark = pytest.mark.integration


def _view_rows(db, estimate_id):
    return db.execute(
        sa.text(
            "select work_category_id, source, amount, row_count, rows_with_amount,"
            " rows_not_finite, vat_rate_base from v_category_totals"
            " where estimate_id = :e order by source, work_category_id"
        ),
        {"e": estimate_id},
    ).all()


def test_fixture_puts_money_under_the_intended_category(db_session, factories):
    """ПРЕДПОСЫЛКА: сборщик кладёт деньги в статью, а не в «Нераспределённое».

    VIEW берёт статью с РАЗДЕЛА позиции. Если бы сборщик ставил её позиции,
    `work_category_id` в VIEW был бы NULL, и все тесты ниже проверяли бы
    «Нераспределённое», думая, что проверяют статью 1.
    """
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="1", amounts=["100.00"])

    rows = _view_rows(db_session, estimate.id)

    assert len(rows) == 1, f"ожидалась одна группа, получено {len(rows)}"
    assert rows[0].work_category_id == fx.category_id(db_session, "1")
    assert rows[0].vat_rate_base == fx.VAT_20, "ставка НДС обязана быть задана явно"
    assert rows[0].amount == Decimal("100.00")
```

- [ ] **Step 3: Прогнать — красное**

```
… uv run pytest tests/integration/test_comparison_rollup.py -v
```
Ожидание: `ModuleNotFoundError: No module named 'crud.comparison'` (импорт в
шапке файла). Убрать импорт нельзя — он нужен следующим шагам; красное на импорте
и есть ожидаемое первое состояние.

- [ ] **Step 4: Тест накопления групп VIEW**

```python
def test_view_rows_are_accumulated_not_overwritten(db_session, factories):
    """Две группы VIEW по одной статье складываются, а не затирают друг друга.

    VIEW группируется по `proposal_id`, поэтому смета с ДВУМЯ лотами даёт две
    строки на ту же статью и тот же источник. Присваивание вместо накопления
    потеряло бы первую.

    Замер 2026-08-17: на стенде дубликатов НЕТ (290 строк на 290 ключей, один лот
    на смету) — дефект живыми данными не ловится, фикстура обязана быть
    искусственной.
    """
    estimate = factories.EstimateFactory.create()
    p1 = fx.make_proposal(factories, estimate=estimate, lot_key="lot_a")
    p2 = fx.make_proposal(factories, estimate=estimate, lot_key="lot_b")
    fx.seed_chapter_with_positions(db_session, factories, proposal=p1, code="1", amounts=["100.00"])
    fx.seed_chapter_with_positions(db_session, factories, proposal=p2, code="1", amounts=["200.00"])

    # Предпосылка проверяется В ТЕСТЕ: без двух групп он бы прошёл вакуозно.
    rows = [r for r in _view_rows(db_session, estimate.id) if r.source == "positions"]
    assert len(rows) == 2, "нужны ДВЕ proposal-группы, иначе тест ничего не стережёт"

    rollup = cmp.load_rollups(db_session, [estimate.contract_id])[estimate.contract_id][0]
    branch = rollup.direct[fx.category_id(db_session, "1")][cmp.SOURCE_POSITIONS]

    assert branch.rows == 2
    assert branch.net == cmp.net_of(Decimal("100.00"), fx.VAT_20) + \
                         cmp.net_of(Decimal("200.00"), fx.VAT_20)
```

- [ ] **Step 5: Тест разделения причин неполноты**

```python
def test_unknown_vat_base_does_not_fake_unpriced(db_session, factories):
    """Цена есть, база НДС неизвестна → ТОЛЬКО vat_base_unknown (спека §2.1.3).

    Обнуление `rows_priced` при неизвестной базе добавляло бы вторую, ложную
    причину `unpriced_rows`; после `build_tree` различить их стало бы нельзя —
    `CategoryNode` причин не несёт.
    """
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate, vat_rate=None)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="1", amounts=["100.00"])

    rows = [r for r in _view_rows(db_session, estimate.id) if r.source == "positions"]
    assert rows[0].vat_rate_base is None, "предпосылка: база НДС неизвестна"
    assert rows[0].rows_with_amount == rows[0].row_count, "предпосылка: цена ЕСТЬ"

    cid = fx.category_id(db_session, "1")
    rollup = cmp.load_rollups(db_session, [estimate.contract_id])[estimate.contract_id][0]
    branch = rollup.direct[cid][cmp.SOURCE_POSITIONS]

    assert branch.rows_priced == branch.rows, "цена есть — счётчик не обнуляется"
    assert branch.rows_vat_base_unknown == branch.rows
    assert rollup.reasons[cid] == frozenset({"vat_base_unknown"})


def test_unpriced_row_is_its_own_reason(db_session, factories):
    """Позиция без цены даёт `unpriced_rows` и НЕ даёт `vat_base_unknown`."""
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="1", amounts=["100.00", None])

    cid = fx.category_id(db_session, "1")
    rollup = cmp.load_rollups(db_session, [estimate.contract_id])[estimate.contract_id][0]

    assert rollup.reasons[cid] == frozenset({"unpriced_rows"})
```

- [ ] **Step 6: Тест собственных денег — точное равенство**

```python
def test_own_net_sums_two_sources_exactly(db_session, factories):
    """own = positions + additional_works, ТОЧНОЕ равенство (спека §2.1).

    Допработа ставится на РОДИТЕЛЬСКУЮ статью «3», у которой есть подстатья «3.1»
    с деньгами: именно здесь вычитание «родитель минус дети» дало бы другой ответ,
    и именно это запрещают спека и докстрока `category_rollup`.
    """
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="3", amounts=["500.00"])
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="3.1", amounts=["100.00"])
    fx.seed_additional_work(db_session, factories, proposal=proposal,
                            code="3", amount="200.00")

    cid = fx.category_id(db_session, "3")
    rollup = cmp.load_rollups(db_session, [estimate.contract_id])[estimate.contract_id][0]

    expected = cmp.net_of(Decimal("500.00"), fx.VAT_20) + \
               cmp.net_of(Decimal("200.00"), fx.VAT_20)
    assert cmp.own_net(rollup, cid) == expected
```

**Исполнителю:** коды «3» и «3.1» взяты из классификатора миграции 0005 — «3» это
«Устройство гидроизоляции подземной части». Проверить, что у выбранного кода есть
подстатья, запросом, а не по памяти: `select code from work_categories where
parent_id = (select id from work_categories where code = '3')`.

- [ ] **Step 7: Тест постоянного числа запросов**

```python
class _QueryCounter:
    def __init__(self):
        self.total = 0


def _count_queries(session):
    """Считает запросы соединения сессии. Listener СНИМАЕТСЯ в finally.

    Непогашенный listener течёт в следующие тесты и даёт ложные счётчики — тот же
    класс ложно-зелёного, что и непроверенное снятие защиты.
    """
    import contextlib

    @contextlib.contextmanager
    def _cm():
        counter = _QueryCounter()
        connection = session.connection()

        def on_execute(conn, cursor, statement, parameters, context, executemany):
            counter.total += 1

        sa.event.listen(connection, "after_cursor_execute", on_execute)
        try:
            yield counter
        finally:
            sa.event.remove(connection, "after_cursor_execute", on_execute)

    return _cm()


def test_query_count_does_not_grow_with_selection(db_session, factories):
    """N+1 запрещён по ВСЕМ входам: категории, сметы, ставки предложений, VIEW.

    Ключ проверки — не абсолютное число, а его НЕИЗМЕННОСТЬ: агрегат на одном
    договоре и на трёх обязан стоить одинаково.
    """
    ids = []
    for _ in range(3):
        estimate = factories.EstimateFactory.create()
        proposal = fx.make_proposal(factories, estimate=estimate)
        fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                       code="1", amounts=["100.00"])
        ids.append(estimate.contract_id)

    with _count_queries(db_session) as c1:
        cmp.load_rollups(db_session, ids[:1])
    with _count_queries(db_session) as c3:
        cmp.load_rollups(db_session, ids)

    assert c1.total == c3.total, (
        f"запросов было {c1.total} на один договор и {c3.total} на три — где-то запрос на смету"
    )
    assert c3.total <= 4, "категории, сметы, ставки предложений, VIEW — по одному"
```

- [ ] **Step 8: Прогнать — красное на всех тестах**

- [ ] **Step 9: Реализовать `backend/crud/comparison.py`**

Требования, все из спеки и из ошибок основания:

1. один запрос к VIEW на всю выборку (`estimate_id IN (…)`), один — к
   `work_categories`, один — к `estimates` выборки, один — к ставкам предложений;
2. строки VIEW **накапливаются** по ключу «смета × статья × источник»;
3. нетто — `gross_to_net(amount, vat_rate_base)`; при `vat_rate_base IS NULL`
   сумма не входит, а `rows_vat_base_unknown` увеличивается — `rows_priced`
   **не обнуляется**;
4. `own_net` = `positions.net + additional_works.net`, без вычитания;
5. `reasons[category_id]` — свёрнутое по поддереву множество причин;
6. `unallocated` — ключ `None`, вынимается до `build_tree`.

- [ ] **Step 10: Прогнать — зелёное**

- [ ] **Step 11: Снять защиту пакетности**

Заменить пакетную загрузку ставок предложений на вызов в цикле по сметам.
Ожидание: краснеет **первое** утверждение теста (`c1.total == c3.total`), а не
только предел `<= 4`. Если краснеет только предел — тест стережёт число, а не
пакетность, и его надо править. Вернуть код. В пробнике правки обязателен
`assert old in source` перед заменой.

- [ ] **Step 12: Коммит**

```bash
git add backend/tests/comparison_fixtures.py backend/crud/comparison.py backend/tests/integration/test_comparison_rollup.py
git commit -m "feat(comparison): роллап выборки одним запросом, нетто-деревья"
```

---

## Task 3: Союз узлов, состояния ячеек, синтетические строки

**Files:**
- Modify: `backend/crud/comparison.py`, `backend/tests/comparison_fixtures.py`
- Test: `backend/tests/integration/test_comparison_rows.py`

**Interfaces:** `ABSENT`/`ZERO`/`VALUE`, `RowRef`, `build_rows` — см. основной план.

- [ ] **Step 1: Тест союза выборки**

```python
def test_union_row_is_present_when_any_contract_has_it(db_session, factories):
    """Статья есть у одного договора → строка есть у обоих (спека §2.1.1)."""
    a = _contract_with(db_session, factories, {"1": ["100.00"], "2": ["200.00"]})
    b = _contract_with(db_session, factories, {"1": ["300.00"]})

    rows = cmp.build_rows(cmp.load_rollups(db_session, [a, b]))
    codes = [r.code for r in rows if r.kind == "category"]

    assert "2" in codes, "статья есть у одного — строка обязана быть у обоих"
```

- [ ] **Step 2: Тест отбрасывания пустых у всех**

```python
def test_row_empty_for_everyone_is_dropped(db_session, factories):
    """Пустая у всех сравниваемых строка не показывается, включая КОРЕНЬ.

    `build_tree` возвращает все 21 корень безусловно — это общий скелет паспорта.
    Союз выборки затем отбрасывает пустые: спека §2.1.1, и та же спека прежде
    противоречила себе арифметикой «215 + четыре пустых корня».
    """
    a = _contract_with(db_session, factories, {"1": ["100.00"]})

    rows = cmp.build_rows(cmp.load_rollups(db_session, [a]))
    codes = {r.code for r in rows if r.kind == "category"}

    assert "1" in codes
    assert "2" not in codes, "корень без данных у всей выборки обязан выпасть"
    assert len(codes) < 21, "все 21 корень в союз попадать не должны"
```

- [ ] **Step 3: Тест `ABSENT` против `ZERO`**

```python
def test_absent_and_zero_are_different_states(db_session, factories):
    """Статьи нет → ABSENT; есть и расценена в ноль → ZERO (спека §2.1.2)."""
    a = _contract_with(db_session, factories, {"1": ["0.00"], "2": ["200.00"]})
    b = _contract_with(db_session, factories, {"2": ["300.00"]})

    rollups = cmp.load_rollups(db_session, [a, b])
    cells = cmp.cells_for(rollups, code="1")

    assert cells[a].total.state == cmp.ZERO
    assert cells[b].total.state == cmp.ABSENT
```

- [ ] **Step 4: Тест синтетической строки «Без подстатьи»**

```python
def test_own_row_appears_when_parent_has_direct_rows(db_session, factories):
    """Строка «Без подстатьи» — по СЧЁТЧИКУ прямых строк, а не по сумме.

    Нулевая прямая сумма при непустом счётчике — тоже факт: без строки раскрытая
    ветка не сойдётся с родителем.
    """
    a = _contract_with(db_session, factories, {"3": ["0.00"], "3.1": ["100.00"]})

    rows = cmp.build_rows(cmp.load_rollups(db_session, [a]))
    own_rows = [r for r in rows if r.kind == "own" and r.parent_code == "3"]

    assert len(own_rows) == 1, "прямые строки есть, сумма ноль — строка обязана быть"
```

- [ ] **Step 5: Тест «Нераспределённого» — обе половины утверждения**

```python
def test_unallocated_is_in_total_but_not_in_median(db_session, factories):
    """Строка есть, входит в «Итого», НЕ входит в медиану (спека §2.1.4, DoD 5в).

    Проверять только наличие строки недостаточно — обе половины названия обязаны
    быть проверены отдельно.
    """
    estimate = factories.EstimateFactory.create()
    proposal = fx.make_proposal(factories, estimate=estimate)
    fx.seed_chapter_with_positions(db_session, factories, proposal=proposal,
                                   code="1", amounts=["100.00"])
    fx.seed_unallocated_positions(db_session, factories, proposal=proposal, amounts=["50.00"])

    rollups = cmp.load_rollups(db_session, [estimate.contract_id])
    rows = cmp.build_rows(rollups)
    agg = cmp.build_comparison(db_session, [estimate.contract_id], vat_mode="net")

    assert any(r.kind == "unallocated" for r in rows), "строка обязана быть"

    total = _row_cells(agg, kind="unallocated")[estimate.contract_id].total.net
    grand = agg["totals"][estimate.contract_id]["total"]["net"]
    assert total is not None and grand == cmp.net_of(Decimal("150.00"), fx.VAT_20), \
        "остаток обязан войти в «Итого по договору»"

    assert estimate.contract_id not in agg["median_inputs"]["unallocated"], \
        "остаток — не статья, в медиану не входит"
```

- [ ] **Steps 6–8:** прогон до красного → реализация → прогон до зелёного.

- [ ] **Step 9: Коммит**

```bash
git commit -m "feat(comparison): союз узлов выборки, состояния ячеек, синтетические строки"
```

---

## Task 4: Корзины, режимы НДС, медианы

**Files:** Modify: `backend/crud/comparison.py`, `backend/tests/comparison_fixtures.py`;
Test: `backend/tests/integration/test_comparison_buckets.py`

**Новый сборщик:** `contract_with_amendment(db, factories, *, base_rate, amd_rate)`
— договор с базовой сметой и одним допсоглашением, у каждого своя `vat_rate`.
Допсоглашений в системе НЕТ ни одного (спека §6), поэтому все тесты корзин идут
на искусственных данных; прогоном на стенде этот путь не проверяется.

- [ ] **Step 1: Тест сложения корзин**

```python
def test_buckets_sum_to_total(db_session, factories):
    """ДГП + ДС = Итого (DoD 5)."""
    c = fx.contract_with_amendment(db_session, factories, base_rate=fx.VAT_20,
                                   amd_rate=fx.VAT_20, base="1000.00", amd="200.00")

    agg = cmp.build_comparison(db_session, [c.id], vat_mode="net")
    cell = _cells(agg, code="1")[c.id]

    assert cell.base.net + cell.amendments.net == cell.total.net
```

- [ ] **Step 2: Тест «медиана — по корзине»**

```python
def test_median_is_computed_per_bucket(db_session, factories):
    """Отклонения в режиме «ДС» — от медианы ДС, а не Итого (спека §2.2)."""
```

- [ ] **Step 3: Тест «отклонения не зависят от режима»**

```python
def test_deviations_are_identical_in_all_three_modes(db_session, factories):
    """Спека §2.5 правило 2, DoD 10. Ось сравнения — всегда нетто."""
    ids = _three_contracts_with_different_rates(db_session, factories)

    devs = {}
    for mode, rate in (("own", None), ("single", Decimal("20")), ("net", None)):
        agg = cmp.build_comparison(db_session, ids, vat_mode=mode, single_rate=rate)
        devs[mode] = [c.total.deviation_pct for c in _cells(agg, code="1").values()]

    assert devs["own"] == devs["single"] == devs["net"]
```

- [ ] **Step 4: Тест двух удельных величин**

```python
def test_shown_per_sqm_follows_mode_but_median_does_not(db_session, factories):
    """`shown_per_sqm` в режиме показа, `net_per_sqm` — вход медианы."""
```

- [ ] **Step 5: Тест `display_rate_undefined` — гасит ячейку, а не столбец**

Фикстура DoD 8е: статья присутствует **и в ДГП** (ставка определена), **и в ДС**
(ставка не определена — предложения с расходящимися `vat_rate` и без
`vat_rate_target`/`vat_rate_base_override`). Контрольный случай в том же тесте:
статья только в ДГП → в режиме «ДС» **ноль**, а не причина.

- [ ] **Step 6: Тест списка ставок и предвыбора**

Включая край «ставок показа нет ни у одной сметы» → список из базовых, предвыбор
есть (DoD 8ж). Проверенное число на пятидоговорном снимке: ничья 20/22 →
предвыбор 22.

- [ ] **Step 7: Тест пустого ₽/м² без ТЭП (DoD 13)**

```python
def test_object_without_area_has_no_per_sqm_and_no_median_input(db_session, factories):
    """Объект без `area_total_sp`: ₽/м² прочерк, в медиану не входит (DoD 13)."""
    obj = factories.ObjectFactory.create(area_total_sp=None)
    ...
```

- [ ] **Step 8: Тест DoD 12 на искусственной фикстуре**

```python
def test_row_with_two_values_gets_no_median(db_session, factories):
    """Две положительные, две нулевые, одна отсутствующая → медианы нет (DoD 12).

    Пункт переписан со строки стенда на фикстуру: два договора из той пятёрки
    удалены 2026-08-17, и та же строка утверждает теперь другое свойство.
    """
```

- [ ] **Steps 9–11:** красное → реализация → зелёное.
- [ ] **Step 12: Коммит**

```bash
git commit -m "feat(comparison): три корзины, режимы НДС, медианы по нетто"
```

---

## Task 5: Эндпоинт

**Files:** Modify: `backend/routers/analytics.py`;
Test: `backend/tests/integration/test_comparison_api.py`

- [ ] **Step 1: Тесты**

```python
def test_ids_and_filter_selection_agree(client, factories):
    """Обе формы выборки на одном множестве дают одинаковый ответ (DoD 1)."""

def test_column_order_is_signed_date_desc_then_id_desc(client, factories):
    """Порядок колонок (DoD 3). Фикстура: две даты, из них две совпадающие."""

def test_all_four_filters_are_accepted(client, factories):
    """q, object_id, contractor_id, rate_class_id (DoD 22); page/page_size НЕ переносятся."""

def test_member_can_read_comparison(client, factories):
    """Чтение доступно и member (спека §2.9, DoD 14)."""

def test_money_is_returned_as_strings(client, factories):
    """decimal_json обязателен: утверждение на само тело JSON, а не на разбор."""
```

- [ ] **Steps 2–5:** красное → реализация → зелёное → коммит.

```bash
git commit -m "feat(comparison): эндпоинт пакетной выборки"
```

---

## Task 6: Excel

**Files:** Create: `backend/services/excel_comparison.py`;
Modify: `backend/routers/reports.py`;
Test: `backend/tests/integration/test_comparison_excel.py`

- [ ] **Step 1: Тесты** — числа листа совпадают с экраном при том же режиме
  (DoD 19); три корзины колонками, у каждой сумма, ₽/м² и отклонение (DoD 21);
  подпись режима, а в «своей ставке» — состав по договору (DoD 20); строки
  «Без подстатьи» и «Нераспределённое» на листе по тем же правилам.
- [ ] **Steps 2–5:** красное → реализация → зелёное → коммит.

```bash
git commit -m "feat(comparison): выгрузка сравнения в Excel"
```

---

## Task 7: Список договоров — выбор и сборка URL

**Files:** Modify: `frontend/src/pages/contracts/ContractsPage.tsx`;
Test: `frontend/src/pages/contracts/ContractsPage.test.tsx`

**Только `ContractsPage`.** Маршрут, `queryKeys`, `queries`, `api/domain`, `types`
и MSW-обработчик — задача 8: страницы ещё нет, и коммит с мёртвым маршрутом либо
не собрался бы, либо тащил недостижимый код.

- [ ] **Step 1: Тесты** — колонка чекбоксов; «Сравнить выбранные (N)» неактивна
  при нуле выбранных и называет число; «Сравнить всё по фильтру» собирает адрес
  с текущими фильтрами и `all=1`; обе кнопки видны и `member`.
- [ ] **Steps 2–5:** красное → реализация → зелёное → коммит.

```bash
git commit -m "feat(contracts): выбор договоров для сравнения"
```

---

## Task 8: Страница сравнения

**Files:** Create: `frontend/src/pages/compare/ComparePage.tsx`;
Modify: `App.tsx`, `services/queryKeys.ts`, `services/queries.ts`,
`services/api/domain.ts`, `types/domain.ts`, `test/handlers.ts`;
Test: `frontend/src/pages/compare/ComparePage.test.tsx`

- [ ] **Step 1: Тесты** — по умолчанию видны только корни; раскрытие даёт второй
  уровень; переключатель корзины меняет все ячейки; режим НДС и ставка
  восстанавливаются из URL; подсветка появляется только при трёх и более
  сопоставимых; прочерк и ноль различимы на экране.

**Предпосылка, требующая замера:** `position: sticky` в jsdom не вычисляется —
закрепление первой колонки проверять классом, живое поведение на стенде
(задача 9).

- [ ] **Steps 2–5:** красное → реализация → зелёное → коммит.

```bash
git commit -m "feat(compare): страница сравнения договоров"
```

---

## Task 9: Приёмка

- [ ] **Step 1: `just ci`** — шаги по отдельности; конвейер с `| tail` вернёт код
  `tail` и покажет падение как успех.
- [ ] **Step 2: Прогон на стенде** (три договора: `СДП-1-МР`, `12-СИТ-МР`,
  `ТЕСТ`). Живьём и только живьём: читаемость при раскрытой ветке, горизонтальная
  прокрутка с закреплённой колонкой, совпадение чисел листа с экраном,
  переключение режимов НДС без перезагрузки. Браузер — Playwright через системный
  Chrome (`channel: "chrome"`); перезагрузка ловится счётчиком полных загрузок
  документа, а не «миганием».
- [ ] **Step 3: Devlog** `docs/devlog/2026-08-17-contract-comparison.md` по §9.2.
  Обязательно назвать: границу инфляции (спека §4.1); пустую корзину ДС на живых
  данных; **список правил, проверенных только искусственными фикстурами** —
  накопление групп VIEW, неполнота, «Нераспределённое», корзины, разные ставки;
  и то, что §6 спеки — датированный снимок пятидоговорного стенда.
- [ ] **Step 4: Коммит, пуш, PR** со ссылками на спеку и оба файла плана (§9.3).
