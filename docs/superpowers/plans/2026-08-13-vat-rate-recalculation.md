# Пересчёт сумм сметы по изменённой ставке НДС — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** дать администратору задать смете базовую и целевую ставку НДС так, чтобы
все денежные величины приложения считались от нетто и показывались в ставке,
объявленной для каждой поверхности.

**Architecture:** в БД по-прежнему лежат валовые деньги файла; две новые колонки на
`estimates` несут решение человека, а не факт файла. Правило пересчёта записано
один раз, на Python, в `backend/money/vat.py`; SQL агрегирует до уровня, на котором
множитель постоянен (предложение), и формулы не содержит — с одним названным
исключением (см. «Отступление от §2.6»). Ось сравнения — нетто, поэтому отклонения
не зависят от ставки показа.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x sync ORM, Alembic, psycopg3,
PostgreSQL 16; React + TS, Vite, shadcn/ui, TanStack Query, Vitest.

**Spec:** [docs/superpowers/specs/2026-08-13-vat-rate-recalculation-design.md](../specs/2026-08-13-vat-rate-recalculation-design.md)

**Ветка:** `feat/vat-rate-recalculation` (создана, спека в ней закоммичена)

## Global Constraints

- **Деньги: `numeric` в БД ↔ `Decimal` в Python ↔ строки в JSON. Никаких `float`** —
  `AGENTS.md` §3. В тестах сравнивать `Decimal` с `Decimal`.
- **Ставки НДС хранятся в процентных пунктах** (`20`, не `0.20`) — спека Ф4б §2.9.
- **`ARITHMETIC_PRECISION = 100` — значащих цифр**, не знаков после запятой
  ([summary_block.py:154](../../../backend/parser/summary_block.py#L154)).
- **Глобальный контекст `Decimal` приложения не меняется** — отдельный тест.
- **Округление до копеек — только на границе ответа, и только для пересчитанного.**
  Внутрь агрегации оно не попадает никогда: `Σ round(x) ≠ round(Σ x)`.
- **Имена методов-валидаторов Pydantic уникальны по всему дереву наследования** —
  `AGENTS.md` §11: два одноимённых валидатора схлопываются в один слот, и второй
  исчезает молча.
- **COALESCE-уникальные, частичные, EXCLUDE-индексы и VIEW — только raw SQL** через
  `op.execute()`; `downgrade` снимает их явно (`AGENTS.md` §11).
- **Миграция обязана быть неизменной во времени:** литералы в ней записываются
  строками, а не импортируются из моделей.
- **`users.id` — `integer`, не `bigint`;** FK на него повторяет тип
  ([0011:16](../../../backend/alembic/versions/2026_08_11_0011-category_overrides.py#L16)).
- **Ни один коммит не оставляет ветку красной.** Задача, ломающая потребителя,
  чинит его тем же коммитом; тест, требующий ещё не написанного маршрута, едет
  вместе с маршрутом.
- **PostgreSQL той же мажорной версии, что в проде (16)** — в тестах тоже.
- **Перед пушем — `just ci`**, шагами по отдельности. `vitest` гонять **только из
  `frontend/`**; `tsc -b --noEmit` (`AGENTS.md` §11).
- **Число собранных тестов замеряется `pytest --collect-only -q`** до и после каждой
  задачи, а не выводится арифметикой.
- **Политика `samples/`:** реальные суммы и реквизиты контрагентов не попадают ни в
  код, ни в тесты, ни в доки, ни в сообщения коммитов.

## Вес строки матрицы: исключение, внесённое в спеку

Спека §2.6 несёт **названное исключение**: нетто-вес строки (`row_amount`) считает
SQL, потому что он служит ключом `ORDER BY` и пагинации и одновременно
показывается. Ячейки по-прежнему сводит Python. Исключение оплачено тестом,
сравнивающим SQL-выражение с `gross_to_net` на тех же входах (задача 3, шаг 8), и
записывается в devlog вместе с причиной.

Оттуда же — правило неполноты: `SUM` игнорирует `NULL`, поэтому вес считается по
ячейкам с известной базой, а неполнота объявляется **булевым признаком строки**;
когда база неизвестна везде, вес пуст и строка уходит в конец (`NULLS LAST`).

## Этапы

Фича одна, PR один, приёмка единая. Реализация делится надвое (спека §6):

- **Этап 1 — нетто-ось:** задачи 0–5.
- **Этап 2 — ручные ставки:** задачи 6–13.

---

## Задача 0: четыре замера (гейт, кода нет)

Нулевая задача обязательна и идёт **до всего остального** (спека §1.6). Каждый
замер способен отменить решение, под которым стоит. **При расхождении —
остановиться, исправить спеку и пройти гейт 2 повторно**, а не править дизайн
внутри реализации.

**Files:**
- Create: `docs/devlog/2026-08-13-vat-rate-recalculation.md` (раздел «Замеры»)

- [ ] **Шаг 1: замер А — лоты, предложения и ставки корпуса**

```sql
SELECT e.id AS estimate_id,
       count(DISTINCT l.id)  AS lots,
       count(p.id)           AS proposals,
       count(DISTINCT p.vat_rate) FILTER (WHERE p.vat_rate IS NOT NULL) AS distinct_rates,
       count(*) FILTER (WHERE p.vat_rate IS NULL) AS rates_null
FROM estimates e
JOIN lots l      ON l.estimate_id = e.id
JOIN proposals p ON p.lot_id = l.id
GROUP BY e.id
ORDER BY e.id;
```

**Отменяет границу §5.1**, если найдётся смета с `distinct_rates > 1`: тогда «одна
ручная база на смету» — дефект, а не ограничение.

- [ ] **Шаг 2: замер Б — два сопоставимых кандидата на полном корпусе**

Замер обязан **выбрать механизм**, поэтому кандидаты выравниваются по трём осям:
один и тот же полный набор предложений, одна и та же гранулярность выдачи, одна и
та же арифметическая точность.

**Три условия сопоставимости, каждое — исправление наивного варианта:**

1. Замер идёт **до** миграции, поэтому `estimates.vat_rate_base_override` ещё не
   существует. Эффективная база моделируется как `p.vat_rate` — на момент замера
   поправок нет ни у одной сметы, и это не упрощение, а тождество.
2. `VALUES` строится **для всех** предложений корпуса, а не для двух примеров:
   иначе кандидат 2 обрабатывает меньше данных и выигрывает по построению.
3. Множитель считается **с проектной точностью**, не обрезанным литералом:
   `0.8333333333` заранее уступает делению в кандидате 1 и по точности, и по
   скорости.

Список `VALUES` генерируется скриптом, а не пишется руками:

```python
# scripts/measure_b_values.py — печатает готовый VALUES для кандидата 2
from decimal import Context, Decimal, localcontext

HUNDRED = Decimal(100)
rows = db.execute(sa.text("SELECT id, vat_rate FROM proposals ORDER BY id")).all()
with localcontext(Context(prec=100)):
    items = ", ".join(
        f"({r.id}::bigint, {HUNDRED / (HUNDRED + r.vat_rate)}::numeric)"
        for r in rows
        if r.vat_rate is not None
    )
print(f"(VALUES {items}) AS f(proposal_id, factor)")
```

Кандидат 1 — группировка по базе, свод в Python:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT d.catalog_position_id, d.contract_id, p.vat_rate AS vat_rate_base,
       SUM(d.unit_cost_total * d.weight) AS weighted_cost,
       SUM(d.weight)                     AS weight_total
FROM v_position_deviations d
JOIN proposals p ON p.id = d.proposal_id
WHERE d.weight > 0 AND p.vat_rate IS NOT NULL
GROUP BY d.catalog_position_id, d.contract_id, p.vat_rate;
```

Кандидат 2 — join на `VALUES` с множителями из Python:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT d.catalog_position_id, d.contract_id, f.factor AS vat_factor,
       SUM(d.unit_cost_total * d.weight) AS weighted_cost,
       SUM(d.weight)                     AS weight_total
FROM v_position_deviations d
JOIN <вставить сгенерированный VALUES> ON f.proposal_id = d.proposal_id
WHERE d.weight > 0
GROUP BY d.catalog_position_id, d.contract_id, f.factor;
```

**Гранулярность у обоих одинакова**: строка на (работа, договор, множитель), и в
обоих случаях свод делает Python. Кандидаты различаются только тем, **откуда
берётся множитель** — из колонки или из переданного списка.

Проверить это до сравнения планов: оба запроса обязаны вернуть **одинаковое число
строк** и совпадающие `weighted_cost` по каждой строке. Разошлось — сравнивать
планы бессмысленно, кандидаты делают разную работу.

Записать для обоих: время, `Buffers: shared hit/read`, форму соединения. Плюс
объёмы:

```sql
SELECT count(*) FROM position_items WHERE is_chapter = false;
SELECT count(*) FROM v_position_deviations;
SELECT count(*) FROM proposals WHERE vat_rate IS NOT NULL;
```

**Выбор фиксируется в devlog числами и определяет шаги 7–8 задачи 3.** У кандидата 2
есть свойство вне плана запроса: список предложений строится в Python и растёт
линейно с охватом матрицы — при выборке в сотни договоров это сотни literal-строк в
каждом запросе. Записывается вместе с временем.

- [ ] **Шаг 3: замер В — устойчивость точности, а не детерминизм**

Повторить те же входы недостаточно: это проверяет детерминизм, которого у `Decimal`
и так нет причин терять. Проверяется **устойчивость к самой точности**: результат
при проектной `ARITHMETIC_PRECISION` обязан после округления до копеек совпасть с
результатом при заведомо избыточной контрольной точности.

```python
from decimal import Context, Decimal, localcontext
from finance import money_round

HUNDRED = Decimal(100)


def restate(gross: Decimal, base: Decimal, target: Decimal, prec: int) -> Decimal:
    with localcontext(Context(prec=prec)):
        return gross * HUNDRED / (HUNDRED + base) * (HUNDRED + target) / HUNDRED


MAX = Decimal("<максимум total_cost_total со стенда>")
for scale in (1, 10, 100):
    gross = MAX * scale
    for base in (Decimal(0), Decimal(12), Decimal(16), Decimal(20), Decimal(22)):
        for target in (Decimal(0), Decimal(12), Decimal(16), Decimal(20), Decimal(22)):
            project = money_round(restate(gross, base, target, 100), 2)
            control = money_round(restate(gross, base, target, 1000), 2)
            assert project == control, (gross, base, target, project, control)
```

Отдельно замерить расхождение суммы округлённых слагаемых с округлённой суммой на
реальном составе позиций сметы и записать **числом**. **Отменяет §2.3**, если
проектная точность разойдётся с контрольной: понадобится фиксированная схема
округления.

- [ ] **Шаг 4: замер Г — допуск `NET_RECONCILIATION_TOLERANCE`**

```sql
SELECT p.id,
       (SELECT total_cost FROM proposal_summary_lines
         WHERE proposal_id = p.id AND summary_key = 'total_cost_including_vat') AS gross,
       (SELECT total_cost FROM proposal_summary_lines
         WHERE proposal_id = p.id AND summary_key = 'total_cost_excluding_vat') AS file_net,
       p.vat_rate
FROM proposals p;
```

Посчитать `gross × 100 / (100 + vat_rate) − file_net` в `Decimal`, записать максимум
по модулю. Константа берётся **на два порядка выше** замеренного шума; в devlog
пишутся оба числа.

- [ ] **Шаг 5: зафиксировать вердикт**

Все четыре согласуются со спекой — записать «расхождений нет, гейт 2 в силе».
Хоть один разошёлся — **остановиться** и вынести расхождение пользователю.

- [ ] **Шаг 6: замерить базу тестов**

```bash
cd backend && uv run pytest --collect-only -q | tail -3
cd frontend && npx vitest run --reporter=dot 2>&1 | tail -5
```

- [ ] **Шаг 7: коммит**

```bash
git add docs/devlog/2026-08-13-vat-rate-recalculation.md
git commit -m "docs(devlog): четыре замера нулевой задачи, выбор механизма агрегации"
```

---

## Задача 1: `money/vat.py` — три функции по смыслу величины

**Files:**
- Create: `backend/money/__init__.py`
- Create: `backend/money/vat.py`
- Test: `backend/tests/unit/test_money_vat.py`

**Interfaces:**
- Consumes: `ARITHMETIC_PRECISION` из `parser.summary_block`, `money_round` из `finance`.
- Produces:
  - `gross_to_net(gross: Decimal, base: Decimal) -> Decimal`
  - `net_to_gross(net: Decimal, target: Decimal) -> Decimal`
  - `vat_from_net(net: Decimal, target: Decimal) -> Decimal`
  - `restate_gross(gross: Decimal | None, base: Decimal | None, target: Decimal | None) -> RestatedAmount`
  - `quantize_money(value: Decimal | None) -> Decimal | None`
  - `AmountStatus` (`original`, `restated`, `unknown_base`, `not_finite`)
  - `RestatedAmount(amount: Decimal | None, status: AmountStatus)`

- [ ] **Шаг 1: написать падающий тест**

```python
# backend/tests/unit/test_money_vat.py
from decimal import Decimal, getcontext

import pytest

from money.vat import (
    AmountStatus,
    gross_to_net,
    net_to_gross,
    quantize_money,
    restate_gross,
    vat_from_net,
)


def test_gross_to_net_removes_declared_vat():
    assert gross_to_net(Decimal("120"), Decimal("20")) == Decimal("100")


def test_net_to_gross_adds_target_vat():
    assert net_to_gross(Decimal("100"), Decimal("16")) == Decimal("116")


def test_vat_from_net_is_zero_at_zero_target():
    """Тест против универсального множителя: (100+0)/(100+20) дало бы не ноль."""
    assert vat_from_net(Decimal("100"), Decimal("0")) == Decimal("0")


def test_restate_keeps_value_and_exponent_when_target_equals_base():
    gross = Decimal("100.50")
    result = restate_gross(gross, Decimal("20"), Decimal("20"))
    assert result.status is AmountStatus.ORIGINAL
    assert result.amount == gross
    assert result.amount.as_tuple().exponent == gross.as_tuple().exponent


def test_restate_keeps_value_when_target_not_set():
    gross = Decimal("100.50")
    result = restate_gross(gross, Decimal("20"), None)
    assert result.status is AmountStatus.ORIGINAL
    assert result.amount.as_tuple().exponent == gross.as_tuple().exponent


def test_restate_is_identity_when_override_equals_declared_rate():
    """Ветка тождества не зависит от того, перекрыта ли база."""
    gross = Decimal("100.50")
    result = restate_gross(gross, Decimal("20"), Decimal("20"))
    assert result.status is AmountStatus.ORIGINAL


def test_restate_without_base_reports_unknown_and_returns_source():
    gross = Decimal("100.50")
    result = restate_gross(gross, None, Decimal("16"))
    assert result.status is AmountStatus.UNKNOWN_BASE
    assert result.amount == gross
    assert result.amount.as_tuple().exponent == gross.as_tuple().exponent


def test_restate_recalculates_when_target_differs():
    result = restate_gross(Decimal("120"), Decimal("20"), Decimal("16"))
    assert result.status is AmountStatus.RESTATED
    assert result.amount == Decimal("116")


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_restate_does_not_fail_on_non_finite(raw):
    gross = Decimal(raw)
    result = restate_gross(gross, Decimal("20"), Decimal("16"))
    assert result.status is AmountStatus.NOT_FINITE


def test_quantize_money_rounds_half_up_to_kopecks():
    assert quantize_money(Decimal("116.005")) == Decimal("116.01")
    assert quantize_money(None) is None


def test_global_decimal_context_is_not_touched():
    before = getcontext().prec
    gross_to_net(Decimal("120"), Decimal("20"))
    assert getcontext().prec == before
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/unit/test_money_vat.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'money'`

- [ ] **Шаг 3: написать модуль**

```python
# backend/money/__init__.py
"""Денежные правила приложения."""
```

```python
# backend/money/vat.py
"""Пересчёт денежных величин между ставками НДС (спека §2.2).

Функции СМЫСЛОВЫЕ, а не универсальные: `gross_to_net` нельзя применять к сумме
налога и к нетто-строке блока итогов — они уже не валовые. Универсального
множителя `(100+цель)/(100+база)` здесь нет намеренно: применённый к сумме
налога, при цели 0 % он оставил бы ненулевой налог.

Канон — валовое. И валовое, и нетто суть факты файла, но единый путь
преобразования и сохранение аддитивности позиций дают именно валовому; файловое
нетто служит независимой перекрёстной проверкой (§2.2, §2.10).

ОКРУГЛЕНИЕ ЗДЕСЬ НЕ ДЕЛАЕТСЯ. `quantize_money` вызывается один раз, на границе
ответа, над ГОТОВЫМ полем; внутрь агрегации она попасть не имеет права, иначе
`Σ round(x) ≠ round(Σ x)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from enum import StrEnum

from finance import money_round
from parser.summary_block import ARITHMETIC_PRECISION

_HUNDRED = Decimal(100)

#: Контекст строится ЯВНО и не наследует глобальный: `localcontext()` без
#: аргумента копирует текущий контекст ВМЕСТЕ С ЕГО ТРАПАМИ, и включённый
#: где-то `Inexact` превратил бы штатное деление в исключение. Округление здесь
#: РАЗРЕШЕНО — частное почти никогда не представимо конечной десятичной дробью
#: (тот же довод и та же форма, что у `parser/vat_rate.py`).
_VAT_CONTEXT = Context(
    prec=ARITHMETIC_PRECISION,
    traps=[Overflow, DivisionByZero, InvalidOperation],
)

#: Допуск сверки выведенного нетто с файловым, в рублях. Снят замером Г задачи 0.
NET_RECONCILIATION_TOLERANCE = Decimal("0.01")  # ← подставить замеренное значение


class AmountStatus(StrEnum):
    """Что произошло с суммой при приведении к ставке показа."""

    ORIGINAL = "original"
    """Цель равна базе — значение НЕ тронуто, арифметики не было."""

    RESTATED = "restated"
    """Пересчитано: нетто выведено из базы, затем поднято до цели."""

    UNKNOWN_BASE = "unknown_base"
    """База неизвестна — нетто не существует, показано исходное значение."""

    NOT_FINITE = "not_finite"
    """`NaN`/`±Infinity` в источнике (открытый хвост Ф4): пересчёт невозможен."""


@dataclass(frozen=True)
class RestatedAmount:
    amount: Decimal | None
    status: AmountStatus


def gross_to_net(gross: Decimal, base: Decimal) -> Decimal:
    """Убрать из валовой суммы НДС по ставке, которой она соответствует."""
    with localcontext(_VAT_CONTEXT):
        return gross * _HUNDRED / (_HUNDRED + base)


def net_to_gross(net: Decimal, target: Decimal) -> Decimal:
    """Поднять нетто до валовой суммы по целевой ставке."""
    with localcontext(_VAT_CONTEXT):
        return net * (_HUNDRED + target) / _HUNDRED


def vat_from_net(net: Decimal, target: Decimal) -> Decimal:
    """Сумма налога от нетто по целевой ставке. При цели 0 даёт ровно 0."""
    with localcontext(_VAT_CONTEXT):
        return net * target / _HUNDRED


def restate_gross(
    gross: Decimal | None, base: Decimal | None, target: Decimal | None
) -> RestatedAmount:
    """Показать валовую сумму в ставке показа. Три ветки спеки §2.4.

    Ветка тождества — ТРЕБОВАНИЕ, а не оптимизация, и срабатывает всегда при
    равенстве цели и базы, независимо от того, перекрыта база или нет. Умножение
    на единицу сдвинуло бы `exponent`, и посимвольное совпадение денежных полей
    (§2.4) перестало бы выполняться.
    """
    if base is None:
        return RestatedAmount(amount=gross, status=AmountStatus.UNKNOWN_BASE)

    effective = base if target is None else target
    if effective == base:
        return RestatedAmount(amount=gross, status=AmountStatus.ORIGINAL)

    if gross is None:
        return RestatedAmount(amount=None, status=AmountStatus.ORIGINAL)
    if not gross.is_finite():
        return RestatedAmount(amount=gross, status=AmountStatus.NOT_FINITE)

    net = gross_to_net(gross, base)
    return RestatedAmount(amount=net_to_gross(net, effective), status=AmountStatus.RESTATED)


def quantize_money(value: Decimal | None) -> Decimal | None:
    """Округлить до копеек. Вызывается ОДИН раз, над готовым полем ответа."""
    if value is None:
        return None
    return money_round(value, 2)
```

- [ ] **Шаг 4: прогнать тесты**

Run: `cd backend && uv run pytest tests/unit/test_money_vat.py -v`
Expected: PASS, 13 тестов

- [ ] **Шаг 5: замерить сбор и закоммитить**

```bash
cd backend && uv run pytest --collect-only -q | tail -3
git add backend/money backend/tests/unit/test_money_vat.py
git commit -m "feat(money): правила пересчёта между ставками НДС"
```

---

## Задача 2: `money/vat.py` — свёртка `net_reconciliation`

**Files:**
- Modify: `backend/money/vat.py`
- Test: `backend/tests/unit/test_money_vat.py`

**Interfaces:**
- Produces:
  - `NetStatus` (`ok`, `mismatch`, `unknown_base`, `not_applicable`)
  - `ProposalNetCheck(proposal_id: int, status: NetStatus, delta: Decimal | None)`
  - `NetReconciliation(status: NetStatus, delta: Decimal | None, mismatched_proposal_ids: list[int])`
  - `check_proposal_net(proposal_id, gross_total, file_net, base) -> ProposalNetCheck`
  - `fold_net_reconciliation(checks: Sequence[ProposalNetCheck]) -> NetReconciliation`

- [ ] **Шаг 1: написать падающий тест**

```python
# дописать в backend/tests/unit/test_money_vat.py
from money.vat import (
    NetStatus,
    ProposalNetCheck,
    check_proposal_net,
    fold_net_reconciliation,
)


def _check(pid, status, delta=None):
    return ProposalNetCheck(proposal_id=pid, status=status, delta=delta)


def test_check_agrees_within_tolerance():
    result = check_proposal_net(1, Decimal("120.00"), Decimal("100.00"), Decimal("20"))
    assert result.status is NetStatus.OK
    assert result.delta == Decimal("0")


def test_check_reports_mismatch_beyond_tolerance():
    result = check_proposal_net(1, Decimal("120.00"), Decimal("90.00"), Decimal("20"))
    assert result.status is NetStatus.MISMATCH
    assert result.delta == Decimal("10")


def test_check_without_base_is_unknown_base():
    result = check_proposal_net(1, Decimal("120.00"), Decimal("100.00"), None)
    assert result.status is NetStatus.UNKNOWN_BASE
    assert result.delta is None


@pytest.mark.parametrize(
    ("gross", "file_net"),
    [(None, Decimal("100")), (Decimal("120"), None), (Decimal("NaN"), Decimal("100"))],
)
def test_check_without_both_operands_is_not_applicable(gross, file_net):
    result = check_proposal_net(1, gross, file_net, Decimal("20"))
    assert result.status is NetStatus.NOT_APPLICABLE
    assert result.delta is None


def test_fold_prefers_mismatch_over_unknown_base():
    """Приоритет идёт от противоречия к незнанию: расхождение — факт,
    незнание — его отсутствие."""
    folded = fold_net_reconciliation(
        [_check(1, NetStatus.UNKNOWN_BASE), _check(2, NetStatus.MISMATCH, Decimal("10"))]
    )
    assert folded.status is NetStatus.MISMATCH
    assert folded.mismatched_proposal_ids == [2]


def test_fold_reports_unknown_base_when_no_mismatch():
    folded = fold_net_reconciliation(
        [_check(1, NetStatus.UNKNOWN_BASE), _check(2, NetStatus.OK, Decimal("0"))]
    )
    assert folded.status is NetStatus.UNKNOWN_BASE
    assert folded.mismatched_proposal_ids == []


def test_fold_is_not_applicable_without_any_comparable():
    folded = fold_net_reconciliation([_check(1, NetStatus.NOT_APPLICABLE)])
    assert folded.status is NetStatus.NOT_APPLICABLE
    assert folded.delta is None


def test_fold_delta_ignores_non_comparable_proposals():
    """Добавление непроверяемого предложения не имеет права двигать сумму."""
    base = [_check(1, NetStatus.OK, Decimal("0.30"))]
    with_extra = base + [_check(2, NetStatus.UNKNOWN_BASE), _check(3, NetStatus.NOT_APPLICABLE)]
    assert fold_net_reconciliation(base).delta == fold_net_reconciliation(with_extra).delta


def test_fold_delta_is_null_without_comparable_proposals():
    assert fold_net_reconciliation([_check(1, NetStatus.UNKNOWN_BASE)]).delta is None
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/unit/test_money_vat.py -k "fold or check_" -v`
Expected: FAIL, `ImportError: cannot import name 'NetStatus'`

- [ ] **Шаг 3: дописать модуль**

```python
# дописать в backend/money/vat.py
from collections.abc import Sequence


class NetStatus(StrEnum):
    """Вердикт сверки выведенного нетто с файловым (спека §2.10)."""

    OK = "ok"
    MISMATCH = "mismatch"
    UNKNOWN_BASE = "unknown_base"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ProposalNetCheck:
    proposal_id: int
    status: NetStatus
    delta: Decimal | None


@dataclass(frozen=True)
class NetReconciliation:
    status: NetStatus
    delta: Decimal | None
    mismatched_proposal_ids: list[int]


_COMPARABLE = (NetStatus.OK, NetStatus.MISMATCH)


def check_proposal_net(
    proposal_id: int,
    gross_total: Decimal | None,
    file_net: Decimal | None,
    base: Decimal | None,
) -> ProposalNetCheck:
    """Сверить выведенное из валового нетто с тем, что заявил файл.

    Перекрёстная проверка, не источник значения: эталон приезжает из блока
    итогов, разобранного другим кодом и по другим правилам, поэтому одна ошибка
    не сдвигает обе стороны сравнения сразу.
    """
    if base is None:
        return ProposalNetCheck(proposal_id, NetStatus.UNKNOWN_BASE, None)

    if any(value is None or not value.is_finite() for value in (gross_total, file_net)):
        return ProposalNetCheck(proposal_id, NetStatus.NOT_APPLICABLE, None)

    delta = gross_to_net(gross_total, base) - file_net
    status = NetStatus.MISMATCH if abs(delta) > NET_RECONCILIATION_TOLERANCE else NetStatus.OK
    return ProposalNetCheck(proposal_id, status, delta)


def fold_net_reconciliation(checks: Sequence[ProposalNetCheck]) -> NetReconciliation:
    """Свернуть проверки предложений в один вердикт по смете (спека §2.10).

    Дельты непроверяемых предложений в сумму НЕ входят: иначе величина с именем
    «расхождение нетто» несла бы в себе нули, означающие «не сверяли», и
    уменьшалась бы от добавления предложений, которых сверка не касалась.
    """
    comparable = [check for check in checks if check.status in _COMPARABLE]
    mismatched = [check.proposal_id for check in checks if check.status is NetStatus.MISMATCH]

    if mismatched:
        status = NetStatus.MISMATCH
    elif any(check.status is NetStatus.UNKNOWN_BASE for check in checks):
        status = NetStatus.UNKNOWN_BASE
    elif comparable:
        status = NetStatus.OK
    else:
        status = NetStatus.NOT_APPLICABLE

    delta = sum((check.delta for check in comparable), start=Decimal(0)) if comparable else None
    return NetReconciliation(status=status, delta=delta, mismatched_proposal_ids=mismatched)
```

- [ ] **Шаг 4: прогнать тесты**

Run: `cd backend && uv run pytest tests/unit/test_money_vat.py -v`
Expected: PASS, 22 теста

- [ ] **Шаг 5: коммит**

```bash
git add backend/money/vat.py backend/tests/unit/test_money_vat.py
git commit -m "feat(money): свёртка сверки нетто по смете"
```

---

## Задача 3: миграция 0012 и нетто-аналитика (одним коммитом)

Миграция снимает `v_position_deviations` и колонку `deviation_pct`, а её читает
`crud/analytics.py`. Разделить задачи нельзя: промежуточный коммит был бы **красным**.
Поэтому схема и оба потребителя аналитики едут вместе.

**Files:**
- Create: `backend/alembic/versions/2026_08_13_0012-vat_rate_recalculation.py`
- Modify: `backend/models.py` (класс `Estimate`, строки 438–473)
- Modify: `backend/crud/analytics.py:61-83`, `:160-183`, `:245-260`, `:363-380`, `:436-470`, `:600-651`
- Test: `backend/tests/integration/test_schema_constraints.py`, `test_analytics_api.py`

**Interfaces:**
- Consumes: `gross_to_net` из `money.vat`.
- Produces: колонки `estimates.vat_rate_base_override`, `vat_rate_target`,
  `vat_rate_updated_by_id`, `vat_rate_updated_at`; VIEW `v_position_deviation_inputs`;
  `v_category_totals` с `proposal_id` и `vat_rate_base`; отражение `DEVIATION_INPUTS`;
  ячейка матрицы — средневзвешенная **нетто**-ставка; `row_amount` — **нетто**.

- [ ] **Шаг 1: написать падающие тесты схемы**

```python
# дописать в backend/tests/integration/test_schema_constraints.py
def test_estimates_vat_columns_reject_out_of_range(db_session, factories):
    estimate = factories.estimate()
    db_session.commit()
    with pytest.raises(sa.exc.IntegrityError):
        db_session.execute(
            sa.text("UPDATE estimates SET vat_rate_target = 101 WHERE id = :id"),
            {"id": estimate.id},
        )
        db_session.commit()
    db_session.rollback()


def test_deviation_inputs_view_has_no_deviation_pct(db_session):
    columns = {
        row[0]
        for row in db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'v_position_deviation_inputs'"
            )
        )
    }
    assert "deviation_pct" not in columns
    assert {"vat_rate_base", "vat_rate_target"} <= columns


def test_old_deviations_view_is_gone(db_session):
    assert db_session.execute(sa.text("SELECT to_regclass('v_position_deviations')")).scalar() is None


def test_category_totals_view_groups_by_proposal(db_session):
    columns = {
        row[0]
        for row in db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'v_category_totals'"
            )
        )
    }
    assert {"proposal_id", "vat_rate_base"} <= columns
```

- [ ] **Шаг 2: написать падающие тесты матрицы**

Проверяется не только `rate`: `amount`, `row_amount`, порядок строк и количество
ячеек на пару — всё это меняется группировкой по базе.

```python
# дописать в backend/tests/integration/test_analytics_api.py
def test_matrix_cell_rate_is_net_of_declared_vat(client, factories, db_session):
    factories.priced_estimate(unit_cost_total=Decimal("120"), vat_rate=Decimal("20"))
    db_session.commit()
    cell = client.get("/api/v1/analytics/matrix").json()["rows"][0]["cells"][0]
    assert Decimal(cell["rate"]) == Decimal("100.00")


def test_matrix_cell_amount_is_net(client, factories, db_session):
    factories.priced_estimate(
        unit_cost_total=Decimal("120"), weight=Decimal("2"), vat_rate=Decimal("20")
    )
    db_session.commit()
    cell = client.get("/api/v1/analytics/matrix").json()["rows"][0]["cells"][0]
    assert Decimal(cell["amount"]) == Decimal("200.00")


def test_matrix_row_amount_is_net_and_orders_rows(client, factories, db_session):
    """СТРОГАЯ инверсия: валовым выше A, по нетто выше B.

    Вход подобран так, что нетто НЕ совпадают, — иначе порядок решал бы
    тай-брейк, и тест был бы зелёным и при валовой сортировке:
      A: 122 при НДС 22 % → нетто 100
      B: 111 при НДС 10 % → нетто 100.909…
    """
    factories.priced_estimate(
        catalog_title="A", unit_cost_total=Decimal("122"), weight=Decimal("1"),
        vat_rate=Decimal("22"),
    )
    factories.priced_estimate(
        catalog_title="B", unit_cost_total=Decimal("111"), weight=Decimal("1"),
        vat_rate=Decimal("10"),
    )
    db_session.commit()
    rows = client.get("/api/v1/analytics/matrix").json()["rows"]
    assert [row["standard_job_title"] for row in rows] == ["B", "A"]
    assert Decimal(rows[0]["row_amount"]) == Decimal("100.91")
    assert Decimal(rows[1]["row_amount"]) == Decimal("100.00")


def test_row_amount_is_partial_and_flagged_when_a_base_is_unknown(client, factories, db_session):
    """`SUM` игнорирует NULL: без признака неполная сумма выглядела бы полной."""
    factories.priced_estimate(
        catalog_title="A", contract_number="C-1", unit_cost_total=Decimal("120"),
        weight=Decimal("1"), vat_rate=Decimal("20"),
    )
    factories.priced_estimate(
        catalog_title="A", contract_number="C-2", unit_cost_total=Decimal("500"),
        weight=Decimal("1"), vat_rate=None,
    )
    db_session.commit()
    row = client.get("/api/v1/analytics/matrix").json()["rows"][0]
    assert Decimal(row["row_amount"]) == Decimal("100.00")
    assert row["row_amount_incomplete"] is True


def test_row_amount_is_empty_when_every_base_is_unknown(client, factories, db_session):
    """Пусто, а не ноль: ноль читался бы как «работы на ноль рублей»."""
    factories.priced_estimate(
        catalog_title="A", unit_cost_total=Decimal("500"), weight=Decimal("1"), vat_rate=None
    )
    factories.priced_estimate(
        catalog_title="B", unit_cost_total=Decimal("120"), weight=Decimal("1"),
        vat_rate=Decimal("20"),
    )
    db_session.commit()
    rows = client.get("/api/v1/analytics/matrix").json()["rows"]
    assert rows[-1]["standard_job_title"] == "A"     # NULLS LAST
    assert rows[-1]["row_amount"] is None
    assert rows[-1]["row_amount_incomplete"] is True


def test_matrix_yields_one_cell_per_position_and_contract(client, factories, db_session):
    """Группировка по базе не имеет права раздваивать ячейку."""
    factories.priced_estimate_with_two_proposals(
        unit_cost_total=Decimal("120"), vat_rates=[Decimal("20"), Decimal("20")]
    )
    db_session.commit()
    row = client.get("/api/v1/analytics/matrix").json()["rows"][0]
    contract_ids = [cell["contract_id"] for cell in row["cells"]]
    assert len(contract_ids) == len(set(contract_ids))


def test_matrix_cell_is_empty_without_vat_base(client, factories, db_session):
    factories.priced_estimate(unit_cost_total=Decimal("120"), vat_rate=None)
    db_session.commit()
    cell = client.get("/api/v1/analytics/matrix").json()["rows"][0]["cells"][0]
    assert cell["rate"] is None
    assert cell["deviation_reason"] == "unknown_vat_base"


def test_phase6_passport_deviation_is_net_based(client, factories, db_session):
    """Норматив — цена без НДС: 120 с НДС 20 % против норматива 100 дают 0 %."""
    contract = factories.contract_with_standard(
        unit_cost_total=Decimal("120"), vat_rate=Decimal("20"), standard=Decimal("100")
    )
    db_session.commit()
    body = client.get(f"/api/v1/analytics/passport/{contract.id}").json()
    assert Decimal(body["top_positions"][0]["deviation_pct"]) == Decimal("0")
    assert body["totals"]["over_standard"] == 0
```

- [ ] **Шаг 3: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_schema_constraints.py tests/integration/test_analytics_api.py -v`
Expected: FAIL — VIEW не существует, `rate` равен `120`

- [ ] **Шаг 4: написать миграцию**

```python
# backend/alembic/versions/2026_08_13_0012-vat_rate_recalculation.py
"""Ручные ставки НДС на смете, нетто-ось сравнения.

Три изменения схемы, все — следствия спеки:

1. Четыре колонки на `estimates`: база, назначенная человеком; ставка показа;
   автор и время последней правки. `proposals.vat_rate` не трогается — он
   остаётся неприкосновенным фактом файла. Тип автора — `integer`, как
   `users.id` (то же уточнение, что в 0011).

2. `v_position_deviations` → `v_position_deviation_inputs`. Колонка
   `deviation_pct` УБРАНА: она считалась на валовой цене, а норматив объявлен
   ценой без НДС, и оставленная колонка стала бы молча неверной. Имя меняется
   вместе со смыслом — VIEW без отклонения, названный `…_deviations`, это тот
   же класс ошибки, что `total_cost_with_vat` со значением «без НДС».

3. `v_category_totals` группируется ДОПОЛНИТЕЛЬНО по предложению и его базовой
   ставке — до уровня, на котором множитель пересчёта постоянен.

Литералы записаны строками: миграция обязана быть неизменной во времени.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-13
"""
import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

V_POSITION_DEVIATION_INPUTS = """
CREATE VIEW v_position_deviation_inputs AS
SELECT
    pi.id                                            AS position_item_id,
    pi.proposal_id,
    p.lot_id,
    l.estimate_id,
    e.contract_id,
    c.object_id,
    c.rate_class_id,
    pi.catalog_position_id,
    pi.job_title_in_proposal,
    pi.unit_id,
    pi.unit_cost_total,
    COALESCE(pi.suggested_quantity, pi.quantity)     AS weight,
    COALESCE(e.data_prepared_on_date, c.signed_date) AS comparison_date,
    rs.id                                            AS rate_standard_id,
    rs.standard_unit_rate,
    COALESCE(e.vat_rate_base_override, p.vat_rate)   AS vat_rate_base,
    e.vat_rate_target
FROM position_items pi
JOIN proposals        p  ON p.id  = pi.proposal_id
JOIN lots             l  ON l.id  = p.lot_id
JOIN estimates        e  ON e.id  = l.estimate_id
JOIN contracts        c  ON c.id  = e.contract_id
JOIN catalog_positions cp ON cp.id = pi.catalog_position_id
LEFT JOIN rate_standards rs
       ON rs.catalog_position_id = pi.catalog_position_id
      AND rs.rate_class_id       = c.rate_class_id
      AND daterange(rs.valid_from, rs.valid_to, '[)')
          @> COALESCE(e.data_prepared_on_date, c.signed_date)
WHERE pi.is_chapter = false
  AND pi.unit_cost_total IS NOT NULL
  AND cp.kind = 'POSITION'
"""

# Дословная копия объявления из миграции 0002 — нужна для downgrade.
V_POSITION_DEVIATIONS = """
CREATE VIEW v_position_deviations AS
SELECT
    pi.id                                            AS position_item_id,
    pi.proposal_id,
    p.lot_id,
    l.estimate_id,
    e.contract_id,
    c.object_id,
    c.rate_class_id,
    pi.catalog_position_id,
    pi.job_title_in_proposal,
    pi.unit_id,
    pi.unit_cost_total,
    COALESCE(pi.suggested_quantity, pi.quantity)     AS weight,
    COALESCE(e.data_prepared_on_date, c.signed_date) AS comparison_date,
    rs.id                                            AS rate_standard_id,
    rs.standard_unit_rate,
    (pi.unit_cost_total / rs.standard_unit_rate - 1) * 100 AS deviation_pct
FROM position_items pi
JOIN proposals        p  ON p.id  = pi.proposal_id
JOIN lots             l  ON l.id  = p.lot_id
JOIN estimates        e  ON e.id  = l.estimate_id
JOIN contracts        c  ON c.id  = e.contract_id
JOIN catalog_positions cp ON cp.id = pi.catalog_position_id
LEFT JOIN rate_standards rs
       ON rs.catalog_position_id = pi.catalog_position_id
      AND rs.rate_class_id       = c.rate_class_id
      AND daterange(rs.valid_from, rs.valid_to, '[)')
          @> COALESCE(e.data_prepared_on_date, c.signed_date)
WHERE pi.is_chapter = false
  AND pi.unit_cost_total IS NOT NULL
  AND cp.kind = 'POSITION'
"""

V_CATEGORY_TOTALS_WITH_PROPOSAL = """
CREATE VIEW v_category_totals AS
SELECT
    l.estimate_id,
    p.id AS proposal_id,
    COALESCE(e.vat_rate_base_override, p.vat_rate) AS vat_rate_base,
    ch.work_category_id,
    'positions'::text AS source,
    SUM(pi.total_cost_total) FILTER (
        WHERE pi.total_cost_total <> 'NaN'::numeric
          AND pi.total_cost_total <> 'Infinity'::numeric
          AND pi.total_cost_total <> '-Infinity'::numeric
    ) AS amount,
    COUNT(*)::int AS row_count,
    COUNT(*) FILTER (
        WHERE pi.total_cost_total IS NOT NULL
          AND pi.total_cost_total <> 'NaN'::numeric
          AND pi.total_cost_total <> 'Infinity'::numeric
          AND pi.total_cost_total <> '-Infinity'::numeric
    )::int AS rows_with_amount,
    COUNT(*) FILTER (
        WHERE pi.total_cost_total IS NOT NULL
          AND NOT (
                  pi.total_cost_total <> 'NaN'::numeric
              AND pi.total_cost_total <> 'Infinity'::numeric
              AND pi.total_cost_total <> '-Infinity'::numeric
          )
    )::int AS rows_not_finite
FROM position_items pi
JOIN proposals p ON p.id = pi.proposal_id
JOIN lots      l ON l.id = p.lot_id
JOIN estimates e ON e.id = l.estimate_id
LEFT JOIN position_items ch
       ON ch.id = pi.chapter_item_id
      AND ch.proposal_id = pi.proposal_id
WHERE pi.is_chapter = false
GROUP BY l.estimate_id, p.id, COALESCE(e.vat_rate_base_override, p.vat_rate),
         ch.work_category_id
UNION ALL
SELECT
    l.estimate_id,
    p.id AS proposal_id,
    COALESCE(e.vat_rate_base_override, p.vat_rate) AS vat_rate_base,
    aw.work_category_id,
    'additional_works'::text AS source,
    SUM(aw.total_amount) FILTER (
        WHERE aw.total_amount <> 'NaN'::numeric
          AND aw.total_amount <> 'Infinity'::numeric
          AND aw.total_amount <> '-Infinity'::numeric
    ) AS amount,
    COUNT(*)::int AS row_count,
    COUNT(*) FILTER (
        WHERE aw.total_amount IS NOT NULL
          AND aw.total_amount <> 'NaN'::numeric
          AND aw.total_amount <> 'Infinity'::numeric
          AND aw.total_amount <> '-Infinity'::numeric
    )::int AS rows_with_amount,
    COUNT(*) FILTER (
        WHERE aw.total_amount IS NOT NULL
          AND NOT (
                  aw.total_amount <> 'NaN'::numeric
              AND aw.total_amount <> 'Infinity'::numeric
              AND aw.total_amount <> '-Infinity'::numeric
          )
    )::int AS rows_not_finite
FROM estimate_additional_works aw
JOIN proposals p ON p.id = aw.proposal_id
JOIN lots      l ON l.id = p.lot_id
JOIN estimates e ON e.id = l.estimate_id
GROUP BY l.estimate_id, p.id, COALESCE(e.vat_rate_base_override, p.vat_rate),
         aw.work_category_id
"""

# Дословная копия объявления из миграции 0010 — нужна для downgrade.
V_CATEGORY_TOTALS_0010 = """
<скопировать целиком из backend/alembic/versions/2026_08_10_0010-category_totals_view.py,
 константа V_CATEGORY_TOTALS, без единой правки>
"""


def upgrade() -> None:
    op.add_column("estimates", sa.Column("vat_rate_base_override", sa.Numeric(), nullable=True))
    op.add_column("estimates", sa.Column("vat_rate_target", sa.Numeric(), nullable=True))
    op.add_column("estimates", sa.Column("vat_rate_updated_by_id", sa.Integer(), nullable=True))
    op.add_column(
        "estimates", sa.Column("vat_rate_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_estimates_vat_rate_base_override",
        "estimates",
        "vat_rate_base_override IS NULL "
        "OR (vat_rate_base_override >= 0 AND vat_rate_base_override <= 100)",
    )
    op.create_check_constraint(
        "ck_estimates_vat_rate_target",
        "estimates",
        "vat_rate_target IS NULL OR (vat_rate_target >= 0 AND vat_rate_target <= 100)",
    )
    op.create_foreign_key(
        "fk_estimates_vat_rate_updated_by_id",
        "estimates",
        "users",
        ["vat_rate_updated_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.execute("DROP VIEW IF EXISTS v_position_deviations")
    op.execute(V_POSITION_DEVIATION_INPUTS)
    op.execute("DROP VIEW IF EXISTS v_category_totals")
    op.execute(V_CATEGORY_TOTALS_WITH_PROPOSAL)


def downgrade() -> None:
    # VIEW снимаются ДО колонок: оба ссылаются на vat_rate_base_override.
    op.execute("DROP VIEW IF EXISTS v_category_totals")
    op.execute("DROP VIEW IF EXISTS v_position_deviation_inputs")
    op.drop_constraint("fk_estimates_vat_rate_updated_by_id", "estimates", type_="foreignkey")
    op.drop_constraint("ck_estimates_vat_rate_target", "estimates", type_="check")
    op.drop_constraint("ck_estimates_vat_rate_base_override", "estimates", type_="check")
    op.drop_column("estimates", "vat_rate_updated_at")
    op.drop_column("estimates", "vat_rate_updated_by_id")
    op.drop_column("estimates", "vat_rate_target")
    op.drop_column("estimates", "vat_rate_base_override")
    op.execute(V_CATEGORY_TOTALS_0010)
    op.execute(V_POSITION_DEVIATIONS)
```

- [ ] **Шаг 5: дописать модель**

```python
# backend/models.py, класс Estimate — после import_job_id (строка 450)
    # Ручные ставки НДС (спека пересчёта §2.1). Решение человека о СМЕТЕ, а не
    # факт файла: `proposals.vat_rate` остаётся неприкосновенным. База
    # перекрываема всегда, в том числе поверх заявленной файлом, — файл умеет
    # ошибиться, и это обязано лечиться приложением.
    vat_rate_base_override = Column(Numeric, nullable=True)
    vat_rate_target = Column(Numeric, nullable=True)
    # `users.id` — integer, не bigint; тип повторяет его (то же, что в 0011).
    vat_rate_updated_by_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    vat_rate_updated_at = Column(DateTime(timezone=True), nullable=True)
```

```python
# backend/models.py, __table_args__ класса Estimate — добавить два CHECK
        CheckConstraint(
            "vat_rate_base_override IS NULL "
            "OR (vat_rate_base_override >= 0 AND vat_rate_base_override <= 100)",
            name="ck_estimates_vat_rate_base_override",
        ),
        CheckConstraint(
            "vat_rate_target IS NULL OR (vat_rate_target >= 0 AND vat_rate_target <= 100)",
            name="ck_estimates_vat_rate_target",
        ),
```

- [ ] **Шаг 6: переименовать отражение VIEW**

```python
# backend/crud/analytics.py, заменить блок DEVIATIONS (строки 61-79)
DEVIATION_INPUTS = sa.table(
    "v_position_deviation_inputs",
    sa.column("position_item_id", sa.BigInteger),
    sa.column("proposal_id", sa.BigInteger),
    sa.column("lot_id", sa.BigInteger),
    sa.column("estimate_id", sa.BigInteger),
    sa.column("contract_id", sa.BigInteger),
    sa.column("object_id", sa.BigInteger),
    sa.column("rate_class_id", sa.BigInteger),
    sa.column("catalog_position_id", sa.BigInteger),
    sa.column("job_title_in_proposal", sa.Text),
    sa.column("unit_id", sa.Integer),
    sa.column("unit_cost_total", sa.Numeric),
    sa.column("weight", sa.Numeric),
    sa.column("comparison_date", sa.Date),
    sa.column("rate_standard_id", sa.BigInteger),
    sa.column("standard_unit_rate", sa.Numeric),
    sa.column("vat_rate_base", sa.Numeric),
    sa.column("vat_rate_target", sa.Numeric),
)

#: Прежнее имя — алиас на время правки потребителей вне этого модуля
#: (`crud/reports.py` читает те же колонки и `deviation_pct` не трогает).
#: Снимается задачей 5 с прогоном всего backend: одновременное переименование в
#: шести местах прячет опечатку до попадания на стенд.
DEVIATIONS = DEVIATION_INPUTS
```

- [ ] **Шаг 7: переписать ячейки матрицы на нетто — по результату замера Б**

**Этот шаг условен.** Механизм выбирает замер Б задачи 0, а не план. Ниже записан
вариант «группировка»; если замер выбрал `VALUES`, меняется **только** этот шаг,
и меняется точечно:

| Что | Группировка (ниже) | `VALUES` |
|---|---|---|
| источник множителя | колонка `vat_rate_base` из VIEW | список `(proposal_id, factor)`, построенный Python из тех же баз |
| `GROUP BY` | + `vat_rate_base` | + `f.factor` |
| свод в Python | `_fold_cell` по группам базы | `_fold_cell` по группам множителя |

Миграция, модель, отражение VIEW, `_NET_WEIGHT` и паспорт фазы 6 от выбора **не
зависят**: `vat_rate_base` нужен VIEW в обоих случаях — его читают `crud/reports.py`
и паспорт проекта. Поэтому шаги 4–6 и 8–9 исполняются одинаково.

SQL агрегирует до уровня постоянного множителя; ставку и отклонение считает Python.

```python
# backend/crud/analytics.py
def _cell_groups_cte(scope_filters: list):
    """Слагаемые ячеек в разрезе базы НДС — уровня, где множитель постоянен.

    Формула §6 сохраняется дословно: `SUM(unit_cost_total * w)` и `SUM(w)`.
    Делит и приводит к нетто Python, потому что правило обязано быть записано
    один раз. `MIN(standard_unit_rate)` — по прежнему доводу: внутри группы
    норматив один, агрегат нужен лишь чтобы вынести его из `GROUP BY`.
    """
    latest = latest_estimates()
    weighted = sa.func.sum(DEVIATION_INPUTS.c.unit_cost_total * DEVIATION_INPUTS.c.weight)
    return (
        sa.select(
            DEVIATION_INPUTS.c.catalog_position_id.label("catalog_position_id"),
            DEVIATION_INPUTS.c.contract_id.label("contract_id"),
            DEVIATION_INPUTS.c.vat_rate_base.label("vat_rate_base"),
            weighted.label("weighted_cost"),
            sa.func.sum(DEVIATION_INPUTS.c.weight).label("weight_total"),
            sa.func.min(DEVIATION_INPUTS.c.standard_unit_rate).label("standard_unit_rate"),
        )
        .select_from(
            DEVIATION_INPUTS.join(latest, latest.c.estimate_id == DEVIATION_INPUTS.c.estimate_id)
        )
        .where(DEVIATION_INPUTS.c.weight > 0, *scope_filters)
        .group_by(
            DEVIATION_INPUTS.c.catalog_position_id,
            DEVIATION_INPUTS.c.contract_id,
            DEVIATION_INPUTS.c.vat_rate_base,
        )
        .cte("cell_groups")
    )
```

```python
# backend/crud/analytics.py — свёртка групп одной ячейки
def _fold_cell(groups) -> dict:
    """Одна ячейка из групп по базе. Ровно одна ячейка на пару (работа, договор).

    Хотя бы одна неизвестная база делает ячейку пустой целиком: показать
    средневзвешенное по части строк значило бы выдать неполную величину за полную.
    """
    net_cost = Decimal(0)
    weight_total = Decimal(0)
    standard = None
    for group in groups:
        if group.vat_rate_base is None or not group.weight_total:
            return {"rate": None, "amount": None, "deviation_pct": None,
                    "deviation_reason": "unknown_vat_base", "standard_unit_rate": None}
        net_cost += gross_to_net(group.weighted_cost, group.vat_rate_base)
        weight_total += group.weight_total
        standard = group.standard_unit_rate if standard is None else standard

    rate = net_cost / weight_total if weight_total else None
    return {
        "rate": quantize_money(rate),
        "amount": quantize_money(net_cost),
        "standard_unit_rate": standard,
        "deviation_pct": _deviation(rate, standard),
        "deviation_reason": None if standard is not None else "no_standard",
    }


def _deviation(net_rate, standard):
    if net_rate is None or standard is None or standard == 0:
        return None
    return (net_rate / standard - 1) * 100
```

- [ ] **Шаг 8: вес строки — единственное нетто-выражение в SQL, с признаком неполноты**

Сортировка и пагинация идут в SQL, поэтому нетто-вес строки считается там же
(исключение §2.6 спеки). Молчаливой частичной суммы при этом быть не должно:
`SUM` игнорирует `NULL`, и строка, часть ячеек которой без базы, выглядела бы
полной. Неполнота объявляется отдельным булевым признаком, вес — `NULLS LAST`.

```python
# backend/crud/analytics.py — вес строки матрицы
#: Единственное нетто-выражение в SQL (исключение §2.6): `row_amount` служит
#: ключом ORDER BY и пагинации и одновременно показывается. Пришпилено к
#: `money.vat.gross_to_net` тестом — две площадки одного правила обязаны
#: совпадать, и расхождение падает в CI, а не проявляется на стенде.
_NET_WEIGHT = sa.func.sum(
    DEVIATION_INPUTS.c.unit_cost_total
    * DEVIATION_INPUTS.c.weight
    * 100
    / (100 + DEVIATION_INPUTS.c.vat_rate_base)
)

#: Признак неполноты: хотя бы одна строка без базы вошла бы в `SUM` как
#: пропуск, а не как ноль, и сумма выглядела бы полной.
_WEIGHT_INCOMPLETE = sa.func.bool_or(DEVIATION_INPUTS.c.vat_rate_base.is_(None))
```

```python
# backend/crud/analytics.py — сортировка строк
    .order_by(page_rows.c.row_amount.desc().nullslast(), page_rows.c.catalog_position_id.asc())
```

Пустой вес при полностью неизвестной базе оставляется пустым: `COALESCE(…, 0)`
увёл бы строку в середину сортировки и читался бы как «работы на ноль рублей».

```python
# backend/tests/integration/test_analytics_api.py
def test_sql_net_weight_agrees_with_python(db_session, factories):
    """Единственное нетто-выражение в SQL обязано совпадать с money.vat.

    Отступление от §2.6 допущено ради сортировки и пагинации; расхождение двух
    площадок ловится здесь, а не на стенде.
    """
    factories.priced_estimate(unit_cost_total=Decimal("120"), weight=Decimal("3"),
                              vat_rate=Decimal("20"))
    db_session.commit()
    from_sql = db_session.execute(sa.text(
        "SELECT SUM(unit_cost_total * weight * 100 / (100 + vat_rate_base)) "
        "FROM v_position_deviation_inputs"
    )).scalar()
    from_python = gross_to_net(Decimal("120") * Decimal("3"), Decimal("20"))
    assert quantize_money(from_sql) == quantize_money(from_python)
```

- [ ] **Шаг 9: перевести паспорт фазы 6**

```python
# backend/crud/analytics.py, _priced_positions_select — вместо DEVIATIONS.c.deviation_pct
            DEVIATION_INPUTS.c.vat_rate_base,
            DEVIATION_INPUTS.c.vat_rate_target,
```

```python
# backend/crud/analytics.py, сборка строки паспорта фазы 6
    net_unit_cost = (
        None if r.vat_rate_base is None else gross_to_net(r.unit_cost_total, r.vat_rate_base)
    )
    row = {
        ...
        "deviation_pct": _deviation(net_unit_cost, r.standard_unit_rate),
        "deviation_reason": (
            "unknown_vat_base" if net_unit_cost is None
            else ("no_standard" if r.standard_unit_rate is None else None)
        ),
    }
```

`over_standard` больше не считается `COUNT`-ом по снятой колонке — считается в
Python по тем же строкам, что и таблица:

```python
    over_standard = sum(
        1 for row in rows if row["deviation_pct"] is not None and row["deviation_pct"] > 0
    )
```

- [ ] **Шаг 10: прогнать всё, включая круговой рейс**

```bash
cd backend && uv run alembic upgrade head && uv run alembic check
uv run pytest tests/integration/test_schema_constraints.py tests/integration/test_analytics_api.py tests/integration/test_category_totals_view.py -v
uv run alembic downgrade base && uv run alembic upgrade head
uv run pytest -q
```
Expected: всё зелёное. `test_declared_view_columns_match_the_database` обязан
подтвердить переименование; падение на `downgrade` — дефект порядка снятия
объектов, а не повод пропустить шаг. Партия >5 строк проверяется тестом VIEW
(`prepare_threshold = 5` в psycopg3).

- [ ] **Шаг 11: коммит**

```bash
git add backend/alembic backend/models.py backend/crud/analytics.py backend/tests
git commit -m "feat(db,analytics): миграция 0012 и нетто-ось матрицы и паспорта Ф6"
```

---

## Задача 4: `crud/reports.py` — нетто, четыре класса, подпись ставки

**Files:**
- Modify: `backend/crud/reports.py:77`, `:87-91`, `:135-220`, `:360-375`
- Modify: `backend/services/excel_reports.py` (шапка листа «для банка»)
- Test: `backend/tests/integration/test_reports_api.py`

**Interfaces:**
- Consumes: `gross_to_net` из `money.vat`, `DEVIATION_INPUTS` из `crud.analytics`.
- Produces: разбиение позиций на четыре класса; счётчик `unknown_vat_base`.

- [ ] **Шаг 1: написать падающий тест разбиения**

```python
def test_bank_report_partition_covers_every_priced_position(client, factories, db_session):
    """Четыре класса образуют разбиение: сумма сходится с независимым счётчиком."""
    factories.bank_report_fixture()  # по одной позиции каждого класса
    db_session.commit()
    totals = client.get("/api/v1/reports/bank", params={"rate_class_id": 1}).json()["totals"]
    assert (
        totals["comparable"] + totals["no_volume"]
        + totals["unknown_vat_base"] + totals["no_standard"]
        == totals["priced_positions_total"]
    )


def test_position_without_volume_and_without_base_counts_once(client, factories, db_session):
    """Приоритет блокирующей причины: объём перевешивает базу."""
    factories.priced_position(weight=Decimal("0"), vat_rate=None)
    db_session.commit()
    totals = client.get("/api/v1/reports/bank", params={"rate_class_id": 1}).json()["totals"]
    assert totals["no_volume"] == 1
    assert totals["unknown_vat_base"] == 0
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_reports_api.py -k "partition or counts_once" -v`
Expected: FAIL — ключа `unknown_vat_base` в `totals` нет

- [ ] **Шаг 3: реализовать разбиение с приоритетом**

```python
# backend/crud/reports.py
def _classify(row) -> str:
    """Класс позиции для отчёта «для банка» (спека §2.9).

    Приоритет сверху вниз, и он не декоративный: позиция без объёма и без базы
    считается ОДИН раз, в первом классе. Объём остаётся блокирующей причиной по
    прежнему доводу §7.6 — норматив без объёма не помог бы.
    """
    if row.weight is None or row.weight <= 0:
        return "no_volume"
    if row.vat_rate_base is None:
        return "unknown_vat_base"
    if row.standard_unit_rate is None:
        return "no_standard"
    return "comparable"


def _net_fact(row) -> Decimal | None:
    if row.vat_rate_base is None:
        return None
    return gross_to_net(row.unit_cost_total, row.vat_rate_base)
```

- [ ] **Шаг 4: подписать ставку в шапке листа**

```python
# backend/services/excel_reports.py — строка под заголовком отчёта «для банка»
    sheet.cell(row=header_row, column=1).value = "Все суммы и нормативы — без НДС"
```

Отчёт «для банка» — выборка многих договоров с разными целями, поэтому общая ось
там только нетто. Подпись обязательна: читатель должен видеть, в чём измерены
числа, которые он складывает.

- [ ] **Шаг 5: прогнать тесты**

Run: `cd backend && uv run pytest tests/integration/test_reports_api.py -v`
Expected: PASS

- [ ] **Шаг 6: коммит**

```bash
git add backend/crud/reports.py backend/services/excel_reports.py backend/tests/integration/test_reports_api.py
git commit -m "feat(reports): нетто-сравнение и четвёртый класс исключённого"
```

---

## Задача 5: `crud/project_passport.py` — `net_reconciliation`, снятие алиаса

**Files:**
- Modify: `backend/crud/project_passport.py:341-400`, `:1156-1180`
- Modify: `backend/crud/analytics.py` (снять алиас `DEVIATIONS`)
- Test: `backend/tests/integration/test_project_passport_api.py`

**Interfaces:**
- Consumes: `check_proposal_net`, `fold_net_reconciliation` из `money.vat`.
- Produces: ключ `net_reconciliation` в `totals`:
  `{"status": str, "delta": Decimal | None, "mismatched_proposal_ids": list[int]}`.

- [ ] **Шаг 1: написать падающий тест**

```python
def test_passport_reports_net_reconciliation_ok(client, factories, db_session):
    contract = factories.contract_with_consistent_summary()
    db_session.commit()
    totals = client.get(f"/api/v1/analytics/project-passport/{contract.id}").json()["totals"]
    assert totals["net_reconciliation"]["status"] == "ok"
    assert totals["net_reconciliation"]["mismatched_proposal_ids"] == []


def test_delta_to_file_total_ignores_manual_rates(client, factories, db_session):
    """Диагностика ИМПОРТА: она про потерянные строки, а не про показ."""
    contract = factories.contract_with_consistent_summary()
    before = client.get(f"/api/v1/analytics/project-passport/{contract.id}").json()
    factories.set_vat_target(contract, Decimal("16"))
    db_session.commit()
    after = client.get(f"/api/v1/analytics/project-passport/{contract.id}").json()
    assert after["totals"]["delta_to_file_total"] == before["totals"]["delta_to_file_total"]
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_project_passport_api.py -k net_reconciliation -v`
Expected: FAIL, `KeyError: 'net_reconciliation'`

- [ ] **Шаг 3: собрать сверку по предложениям**

```python
# backend/crud/project_passport.py
def _net_reconciliation(db: Session, estimate: Estimate) -> NetReconciliation:
    """Сверка выведенного нетто с файловым, по предложениям, свёрнутая в вердикт.

    База берётся ЭФФЕКТИВНАЯ (`COALESCE(override, vat_rate)`), а согласованность
    самого файла проверяется по `proposals.vat_rate` и живёт в парсере — это
    разные диагностики, и смешивать их нельзя (спека §2.10).
    """
    rows = db.execute(
        sa.select(
            Proposal.id.label("proposal_id"),
            Proposal.vat_rate,
            _summary_total(JSON_KEY_TOTAL_COST_INCLUDING_VAT).label("gross_total"),
            _summary_total(JSON_KEY_TOTAL_COST_EXCLUDING_VAT).label("file_net"),
        )
        .select_from(Proposal)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate.id)
    ).all()

    override = estimate.vat_rate_base_override
    checks = [
        check_proposal_net(
            row.proposal_id,
            row.gross_total,
            row.file_net,
            override if override is not None else row.vat_rate,
        )
        for row in rows
    ]
    return fold_net_reconciliation(checks)
```

- [ ] **Шаг 4: положить в ответ, не тронув `delta_to_file_total`**

```python
# backend/crud/project_passport.py — ДОБАВИТЬ ключ, ничего не меняя выше
    reconciliation = _net_reconciliation(db, estimate)
    totals["net_reconciliation"] = {
        "status": reconciliation.status.value,
        "delta": reconciliation.delta,
        "mismatched_proposal_ids": reconciliation.mismatched_proposal_ids,
    }
```

`delta_to_file_total` остаётся ровно тем, чем был: валовое против валового, из
исходных файловых денег. Ручные ставки на него не влияют **никогда**.

- [ ] **Шаг 5: снять алиас `DEVIATIONS` и прогнать backend целиком**

```python
# backend/crud/analytics.py — удалить строку
DEVIATIONS = DEVIATION_INPUTS
```

```bash
cd backend && uv run pytest -q
```
Expected: PASS. Любое оставшееся употребление старого имени падает здесь, а не на
стенде. Импорт в `crud/reports.py` переводится на `DEVIATION_INPUTS` тем же шагом.

- [ ] **Шаг 6: коммит — конец этапа 1**

```bash
git add backend/crud backend/tests
git commit -m "feat(passport): сверка нетто по предложениям; конец этапа 1"
```

---

## Задача 6: сервис и `PATCH` ставок (одним коммитом)

Тесты API без маршрута заведомо красные, поэтому сервис и роутер едут вместе.

**Files:**
- Create: `backend/services/estimate_vat.py`
- Create: `backend/routers/estimate_vat.py`
- Modify: `backend/main.py` (регистрация роутера, рядом со строкой 145)
- Test: `backend/tests/integration/test_estimate_vat_api.py`

**Interfaces:**
- Consumes: модель `Estimate`, `require_admin` из `auth`.
- Produces: `set_vat_rates(db, *, estimate_id, base_override, target, user_id) -> Estimate`;
  `EstimateVatError(code)`; `UNSET`; `PATCH /api/v1/estimates/{estimate_id}/vat`.

- [ ] **Шаг 1: написать падающие тесты**

```python
# backend/tests/integration/test_estimate_vat_api.py
from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration


def test_target_rejected_when_any_proposal_has_unknown_base(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20"), None])
    db_session.commit()
    response = client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    assert response.status_code == 422
    db_session.refresh(estimate)
    assert estimate.vat_rate_target is None


def test_declaring_base_then_target_succeeds(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[None])
    db_session.commit()
    assert client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12"}
    ).status_code == 200
    assert client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"}
    ).status_code == 200


def test_absent_field_is_not_touched(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12"})
    db_session.refresh(estimate)
    assert estimate.vat_rate_target == Decimal("16")


def test_null_clears_the_field(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": None})
    db_session.refresh(estimate)
    assert estimate.vat_rate_target is None


def test_clearing_base_with_target_kept_is_rejected(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[None])
    db_session.commit()
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12"})
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    response = client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": None})
    assert response.status_code == 422


def test_clearing_both_rates_clears_the_audit(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    db_session.refresh(estimate)
    assert estimate.vat_rate_updated_by_id is not None

    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": None})
    db_session.refresh(estimate)
    assert estimate.vat_rate_updated_by_id is None
    assert estimate.vat_rate_updated_at is None


def test_clearing_only_one_rate_keeps_the_audit(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[None])
    db_session.commit()
    client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12", "target": "16"}
    )
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": None})
    db_session.refresh(estimate)
    assert estimate.vat_rate_updated_by_id is not None


@pytest.mark.parametrize("field", ["base_override", "target"])
def test_json_float_is_rejected(client, factories, db_session, field):
    """`float` в ставке запрещён §3, а `Decimal | None` в Pydantic его принял бы."""
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    response = client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={field: 16.5})
    assert response.status_code == 422


@pytest.mark.parametrize("field", ["base_override", "target"])
def test_decimal_string_is_accepted(client, factories, db_session, field):
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    response = client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={field: "16.5"})
    assert response.status_code == 200


def test_member_is_forbidden(member_client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    response = member_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    assert response.status_code == 403


def test_concurrent_patches_keep_the_invariant(factories, db_engine):
    """Две одновременные правки: одна снимает базу, другая задаёт цель.
    Итоговое состояние обязано остаться законным.

    Форма теста — как у tests/integration/test_category_override_concurrency.py:
    два соединения, барьер между чтением и записью, проверка итога.
    """
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_estimate_vat_api.py -v`
Expected: FAIL, 404 на неизвестном маршруте

- [ ] **Шаг 3: написать сервис**

```python
# backend/services/estimate_vat.py
"""Правка ставок НДС сметы (спека пересчёта §2.7).

Инвариант «цель задаётся, только если база известна у КАЖДОГО предложения»
межтабличный, и `CHECK` его не выражает. Проверяется здесь, в одной транзакции,
под `SELECT … FOR UPDATE` на строке сметы — та же форма, которой закрыт ручной
разнос статей, и доказывается она тем же тестом на гонку.

Проверяется ИТОГОВОЕ состояние после применения обоих полей, а не каждое поле по
отдельности: запрос, снимающий базу и цель разом, законен, а по частям выглядел
бы как нарушение.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import Estimate, Lot, Proposal


class EstimateVatError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _Unset:
    """Часовой «поле не передано». Отличается от `None`, который значит «снять»."""

    def __repr__(self) -> str:  # pragma: no cover - диагностика
        return "UNSET"


UNSET = _Unset()


def set_vat_rates(
    db: Session,
    *,
    estimate_id: int,
    base_override: Decimal | None | _Unset = UNSET,
    target: Decimal | None | _Unset = UNSET,
    user_id: int,
) -> Estimate:
    estimate = db.execute(
        sa.select(Estimate).where(Estimate.id == estimate_id).with_for_update()
    ).scalar_one_or_none()
    if estimate is None:
        raise EstimateVatError("not_found", "Смета не найдена")

    new_base = (
        estimate.vat_rate_base_override if isinstance(base_override, _Unset) else base_override
    )
    new_target = estimate.vat_rate_target if isinstance(target, _Unset) else target

    if new_target is not None and new_base is None:
        unknown = db.execute(
            sa.select(sa.func.count())
            .select_from(Proposal)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == estimate_id, Proposal.vat_rate.is_(None))
        ).scalar_one()
        if unknown:
            raise EstimateVatError(
                "unknown_base",
                "Ставка показа недоступна: у части предложений сметы базовая ставка "
                "НДС неизвестна. Сначала объявите базовую ставку.",
            )

    estimate.vat_rate_base_override = new_base
    estimate.vat_rate_target = new_target

    if new_base is None and new_target is None:
        # Аудит — свойство ДЕЙСТВУЮЩЕЙ поправки. Истории правок в фиче нет по
        # решению, и аудит без поправки был бы половиной истории; сверх того,
        # `ON DELETE RESTRICT` держал бы пользователя неудаляемым навсегда.
        estimate.vat_rate_updated_by_id = None
        estimate.vat_rate_updated_at = None
    else:
        estimate.vat_rate_updated_by_id = user_id
        estimate.vat_rate_updated_at = datetime.now(UTC)

    db.flush()
    return estimate
```

- [ ] **Шаг 4: написать роутер с отказом от `float`**

```python
# backend/routers/estimate_vat.py
"""Роутер правки ставок НДС сметы (спека пересчёта §2.7).

Право — `admin`: правка меняет все деньги договора сразу, включая выгрузку для
банка. Это тот же вес, что у замены смет и нормативов (`AGENTS.md` §3), и именно
поэтому здесь `require_admin`, в отличие от разноса статей.

Транзакцию ведёт роутер, сервис только пишет — та же раскладка, что у разноса.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from auth import require_admin
from database import get_db
from models import User
from services.estimate_vat import UNSET, EstimateVatError, set_vat_rates

router = APIRouter(prefix="/api/v1/estimates", tags=["estimate-vat"])

_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "unknown_base": status.HTTP_422_UNPROCESSABLE_CONTENT,
}

_EXACT_VAT_FIELDS = ("base_override", "target")


class VatRatesRequest(BaseModel):
    """Отсутствующее поле — «не менять», `null` — «снять».

    Различить их можно только через `model_fields_set`: Pydantic кладёт `None` и
    в то, и в другое.
    """

    base_override: Decimal | None = Field(default=None, ge=0, le=100)
    target: Decimal | None = Field(default=None, ge=0, le=100)

    # Имя метода УНИКАЛЬНО по всему дереву наследования (`AGENTS.md` §11):
    # одноимённые валидаторы схлопываются в один слот, и второй исчезает молча.
    # `Decimal | None` без этой проверки принял бы JSON-число с плавающей точкой,
    # то есть ровно тот `float`, который §3 запрещает.
    @field_validator(*_EXACT_VAT_FIELDS, mode="before", check_fields=False)
    @classmethod
    def _reject_float_in_vat_rates(cls, value):
        if isinstance(value, float):
            raise ValueError(
                'Передавайте ставку строкой (например "16.5"), а не числом с '
                "плавающей точкой."
            )
        return value


@router.patch("/{estimate_id}/vat")
def patch_vat_rates(
    estimate_id: int,
    body: VatRatesRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_admin)],
):
    fields = body.model_fields_set
    try:
        estimate = set_vat_rates(
            db,
            estimate_id=estimate_id,
            base_override=body.base_override if "base_override" in fields else UNSET,
            target=body.target if "target" in fields else UNSET,
            user_id=current_user.id,
        )
        db.commit()
    except EstimateVatError as exc:
        db.rollback()
        raise HTTPException(_STATUS[exc.code], str(exc)) from exc
    except Exception:
        db.rollback()
        raise

    return {
        "estimate_id": estimate.id,
        "vat_rate_base_override": estimate.vat_rate_base_override,
        "vat_rate_target": estimate.vat_rate_target,
        "vat_rate_updated_at": estimate.vat_rate_updated_at,
    }
```

```python
# backend/main.py — рядом со строкой 145
from routers import estimate_vat as estimate_vat_router
app.include_router(estimate_vat_router.router, dependencies=_auth_dep)
```

- [ ] **Шаг 5: прогнать тесты**

Run: `cd backend && uv run pytest tests/integration/test_estimate_vat_api.py -v`
Expected: PASS, 13 тестов

- [ ] **Шаг 6: коммит**

```bash
git add backend/services/estimate_vat.py backend/routers/estimate_vat.py backend/main.py backend/tests/integration/test_estimate_vat_api.py
git commit -m "feat(api): PATCH ставок НДС сметы под admin"
```

---

## Задача 7: предупреждение о снятой ручной ставке при `replace`

Спека §2.11: обе поправки уходят с каскадом, и пропажа ручной работы **не должна
быть молчаливой**. Предупреждение описывает доменное состояние → пишет **сессия B**
(`AGENTS.md` §5).

**Files:**
- Modify: `backend/services/estimate_import.py` (ветка `replace`)
- Test: `backend/tests/integration/test_estimate_import.py`

- [ ] **Шаг 1: написать падающий тест**

```python
def test_replace_warns_about_dropped_manual_vat_rates(client, factories, db_session, tmp_path):
    estimate = factories.imported_estimate()
    factories.set_vat_rates(estimate, base_override=Decimal("12"), target=Decimal("16"))
    db_session.commit()

    job = factories.upload(estimate.contract_id, file=tmp_path / "other.xlsx", replace=True)
    warnings = " ".join(job.warnings)
    assert "ручная ставка" in warnings
    assert "12" in warnings and "16" in warnings


def test_replace_without_manual_rates_adds_no_such_warning(client, factories, db_session, tmp_path):
    estimate = factories.imported_estimate()
    db_session.commit()
    job = factories.upload(estimate.contract_id, file=tmp_path / "other.xlsx", replace=True)
    assert not any("ручная ставка" in w for w in job.warnings)
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_estimate_import.py -k manual_vat -v`
Expected: FAIL — предупреждения нет

- [ ] **Шаг 3: реализовать**

```python
# backend/services/estimate_import.py, ветка replace — ДО удаления сметы
    if existing.vat_rate_base_override is not None or existing.vat_rate_target is not None:
        parts = []
        if existing.vat_rate_base_override is not None:
            parts.append(f"база {existing.vat_rate_base_override}%")
        if existing.vat_rate_target is not None:
            parts.append(f"показ {existing.vat_rate_target}%")
        domain_warnings.append(
            "Заменена смета с ручными ставками НДС (" + ", ".join(parts) + "); "
            "ставки сняты вместе со сметой и не перенесены на новую."
        )
```

Дописывание — SQL-конкатенацией (`warnings || :items`), как во всех фичах фазы:
«прочитать-изменить-записать» потеряло бы то, что дописала другая сессия.

- [ ] **Шаг 4: прогнать тесты**

Run: `cd backend && uv run pytest tests/integration/test_estimate_import.py -v`
Expected: PASS

- [ ] **Шаг 5: коммит**

```bash
git add backend/services/estimate_import.py backend/tests/integration/test_estimate_import.py
git commit -m "feat(import): предупреждение о снятых ручных ставках при замене сметы"
```

---

## Задача 8: целевая ставка на одно-договорных поверхностях

**Files:**
- Modify: `backend/crud/project_passport.py` (суммы дерева, кольцо, `per_sqm`)
- Modify: `backend/crud/analytics.py` (паспорт фазы 6)
- Modify: `backend/crud/reports.py` (свод по договору «а»)
- Test: `backend/tests/integration/test_project_passport_api.py`, `test_reports_api.py`

**Interfaces:**
- Consumes: `restate_gross`, `quantize_money`, `net_to_gross`, `AmountStatus` из `money.vat`.
- Produces: `effective_display_rate(estimate, bases) -> Decimal | None`;
  суммы и норматив одно-договорных поверхностей — в ставке показа.

- [ ] **Шаг 1: написать падающие тесты обеих сторон**

```python
def test_passport_totals_follow_the_target_rate(client, factories, db_session):
    contract = factories.contract_with_positions(total=Decimal("120"), vat_rate=Decimal("20"))
    factories.set_vat_target(contract, Decimal("16"))
    db_session.commit()
    totals = client.get(f"/api/v1/analytics/project-passport/{contract.id}").json()["totals"]
    assert Decimal(totals["amount"]) == Decimal("116.00")


def test_passport_totals_untouched_without_target(client, factories, db_session):
    """Ветка тождества: без поправки ответ обязан совпасть посимвольно."""
    contract = factories.contract_with_positions(total=Decimal("120.5"), vat_rate=Decimal("20"))
    db_session.commit()
    totals = client.get(f"/api/v1/analytics/project-passport/{contract.id}").json()["totals"]
    assert totals["amount"] == "120.5"


def test_passport_total_is_quantized_once_not_per_group(client, factories, db_session):
    """Округление внутри агрегации дало бы Σ round(x) ≠ round(Σ x).

    Три группы по 0.005 при цели, отличной от базы: поштучное округление даст
    0.03, однократное — 0.02.
    """
    contract = factories.contract_with_three_proposals_of(Decimal("0.005"))
    factories.set_vat_target(contract, Decimal("16"))
    db_session.commit()
    totals = client.get(f"/api/v1/analytics/project-passport/{contract.id}").json()["totals"]
    assert Decimal(totals["amount"]) == Decimal("0.02")


def test_standard_follows_the_effective_rate_without_explicit_target(client, factories, db_session):
    """Цель не задана → эффективная ставка равна базе, и норматив идёт в неё же.
    Иначе факт остался бы валовым, а норматив — чистым нетто."""
    contract = factories.contract_with_standard(
        unit_cost_total=Decimal("120"), vat_rate=Decimal("20"), standard=Decimal("100")
    )
    db_session.commit()
    row = client.get(f"/api/v1/reports/contract-summary/{contract.id}").json()["rows"][0]
    assert Decimal(row["unit_cost_total"]) == Decimal("120")
    assert Decimal(row["standard_unit_rate"]) == Decimal("120.00")
    assert Decimal(row["deviation_pct"]) == Decimal("0")


def test_contract_summary_shows_both_sides_in_target(client, factories, db_session):
    contract = factories.contract_with_standard(
        unit_cost_total=Decimal("120"), vat_rate=Decimal("20"), standard=Decimal("100")
    )
    factories.set_vat_target(contract, Decimal("16"))
    db_session.commit()
    row = client.get(f"/api/v1/reports/contract-summary/{contract.id}").json()["rows"][0]
    assert Decimal(row["unit_cost_total"]) == Decimal("116.00")
    assert Decimal(row["standard_unit_rate"]) == Decimal("116.00")
    assert Decimal(row["deviation_pct"]) == Decimal("0")


def test_standard_is_null_when_bases_disagree(client, factories, db_session):
    """Разногласие заявленных ставок — оговорённая граница: единой ставки показа
    нет, и выдавать «какую-то из» нельзя."""
    contract = factories.contract_with_two_proposals(vat_rates=[Decimal("20"), Decimal("12")])
    db_session.commit()
    row = client.get(f"/api/v1/reports/contract-summary/{contract.id}").json()["rows"][0]
    assert row["standard_unit_rate"] is None
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_project_passport_api.py tests/integration/test_reports_api.py -k "target or effective or quantized or disagree" -v`
Expected: FAIL — `amount` равен `120`, норматив равен `100`

- [ ] **Шаг 3: завести эффективную ставку поверхности**

```python
# backend/money/vat.py
def effective_display_rate(
    target: Decimal | None, base_override: Decimal | None, declared: Sequence[Decimal | None]
) -> Decimal | None:
    """Ставка, в которой показывается ОДНО-ДОГОВОРНАЯ поверхность.

    Цель, если задана; иначе перекрытая база; иначе — ЕДИНОГЛАСНАЯ заявленная
    ставка предложений. Разногласие и любое неизвестное дают `None`: показать
    «в какой-то из» ставок нельзя, и это оговорённая граница §5.1.

    Без этой функции норматив уезжал бы в чистое нетто там, где факт остаётся
    валовым, — строка стала бы измерена в двух разных единицах сразу.
    """
    if target is not None:
        return target
    if base_override is not None:
        return base_override
    rates = list(declared)
    if not rates or any(rate is None for rate in rates):
        return None
    first = rates[0]
    return first if all(rate == first for rate in rates) else None
```

- [ ] **Шаг 4: сложить суммы без промежуточного округления**

```python
# backend/crud/project_passport.py
def _restated_branch_amount(rows, target) -> tuple[Decimal | None, bool]:
    """Сумма ветки в ставке показа.

    Слагаемые складываются с ПОЛНОЙ внутренней точностью; квантование делает
    вызывающий код ОДИН раз, над готовым полем ответа. Округление внутри цикла
    дало бы `Σ round(x) ≠ round(Σ x)` — ровно то расхождение, ради отсутствия
    которого канон и выбран валовым.
    """
    total = None
    restated_any = False
    for row in rows:
        restated = restate_gross(row.amount, row.vat_rate_base, target)
        if restated.status is AmountStatus.RESTATED:
            restated_any = True
        if restated.amount is None or restated.status is AmountStatus.NOT_FINITE:
            continue
        total = restated.amount if total is None else total + restated.amount
    return total, restated_any
```

```python
# backend/crud/project_passport.py — граница ответа, единственное квантование
    amount, restated_any = _restated_branch_amount(rows, target)
    node["total"] = quantize_money(amount) if restated_any else amount
```

- [ ] **Шаг 5: привести норматив к эффективной ставке**

```python
# backend/crud/reports.py и backend/crud/analytics.py — одно-договорные поверхности
def _standard_in_display_rate(standard: Decimal | None, rate: Decimal | None) -> Decimal | None:
    """Норматив — цена без НДС; на одно-договорной поверхности он показывается в
    ЭФФЕКТИВНОЙ ставке поверхности, той же, в которой показан факт.

    Отклонение от этого не меняется: приведение обеих сторон к одной ставке
    отношения не меняет.
    """
    if standard is None or rate is None:
        return None
    return quantize_money(net_to_gross(standard, rate))
```

- [ ] **Шаг 6: подписать ставку на экране и на листе**

Паспорт: строка шапки «суммы показаны с НДС 16 %» либо «без НДС» при ставке `0`.
Свод по договору: та же подпись под заголовком листа.

- [ ] **Шаг 7: прогнать всё**

Run: `cd backend && uv run pytest -q`
Expected: PASS

- [ ] **Шаг 8: коммит**

```bash
git add backend/money/vat.py backend/crud backend/tests
git commit -m "feat(passport): суммы и норматив в эффективной ставке поверхности"
```

---

## Задача 9: фронт — типы, клиент, хук

**Files:**
- Modify: `frontend/src/types/domain.ts:496-507`
- Modify: `frontend/src/services/api/domain.ts`
- Modify: `frontend/src/services/queries.ts`
- Test: `frontend/src/services/queries.test.tsx`

**Interfaces:**
- Produces: `useSetEstimateVat()` — мутация, инвалидирующая `qk.passport.project(contractId)`.

- [ ] **Шаг 1: написать падающий тест инвалидации**

```tsx
it("useSetEstimateVat инвалидирует паспорт этого договора и не трогает чужой", async () => {
  const passportKey = qk.passport.project(5);
  const otherKey = qk.passport.project(99);
  // форма — как у существующего теста инвалидации паспорта: обе половины
  // обязательны, иначе тест пройдёт и при инвалидации всего подряд
});
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd frontend && npx vitest run src/services/queries.test.tsx`
Expected: FAIL, `useSetEstimateVat is not a function`

- [ ] **Шаг 3: дописать типы**

```ts
// frontend/src/types/domain.ts, ProjectPassportEstimate — добавить поля
  /** База, назначенная человеком; `null` — база берётся из файла. */
  vat_rate_base_override: Decimal | null;
  /** Ставка показа; `null` — показываем в базовой. */
  vat_rate_target: Decimal | null;
  /** Когда правили ставки; `null` — поправок нет. */
  vat_rate_updated_at: string | null;
```

```ts
// frontend/src/types/domain.ts
export type NetReconciliationStatus = "ok" | "mismatch" | "unknown_base" | "not_applicable";

export interface NetReconciliation {
  status: NetReconciliationStatus;
  /** Decimal-строка; `null` — сравнимых предложений нет. */
  delta: Decimal | null;
  mismatched_proposal_ids: number[];
}

export interface EstimateVatState {
  estimate_id: number;
  vat_rate_base_override: Decimal | null;
  vat_rate_target: Decimal | null;
  vat_rate_updated_at: string | null;
}
```

- [ ] **Шаг 4: дописать клиент и хук**

```ts
// frontend/src/services/api/domain.ts, estimatesApi
  setVat: (
    estimateId: ID,
    input: { base_override?: Decimal | null; target?: Decimal | null },
  ): Promise<EstimateVatState> =>
    api.patch<EstimateVatState>(`/v1/estimates/${estimateId}/vat`, input).then((r) => r.data),
```

```ts
// frontend/src/services/queries.ts
export interface SetEstimateVatInput {
  estimateId: ID;
  contractId: ID;
  input: { base_override?: Decimal | null; target?: Decimal | null };
}

export function useSetEstimateVat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ estimateId, input }: SetEstimateVatInput) =>
      estimatesApi.setVat(estimateId, input),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: qk.passport.project(variables.contractId) });
      toast.success("Ставка НДС обновлена");
    },
    onError: toastApiError,
  });
}
```

- [ ] **Шаг 5: прогнать тесты и типы**

```bash
cd frontend && npx vitest run src/services/queries.test.tsx && npx tsc -b --noEmit
```
Expected: PASS. Гонять **только из `frontend/`**: в корне лежит другой vitest, он не
разрешает алиас `@/` и падает на сборке.

- [ ] **Шаг 6: коммит**

```bash
git add frontend/src/types frontend/src/services
git commit -m "feat(frontend): клиент и хук правки ставки НДС"
```

---

## Задача 10: фронт — диалог правки, бейджи, сноска, сверка

**Files:**
- Create: `frontend/src/pages/passport/VatRateDialog.tsx`
- Modify: `frontend/src/pages/passport/PassportHeader.tsx:94-195`
- Test: `frontend/src/pages/passport/ProjectPassportPage.test.tsx`
- Test: `frontend/src/pages/passport/VatRateDialog.test.tsx`

**Interfaces:**
- Consumes: `useSetEstimateVat` из задачи 9.

- [ ] **Шаг 1: написать падающие тесты действий пользователя**

```tsx
// frontend/src/pages/passport/VatRateDialog.test.tsx
it("без базы поле показа заблокировано и объясняет причину", () => {
  render(<VatRateDialog estimate={estimateWithoutRate} contractId={1} />);
  expect(screen.getByLabelText(/ставка показа/i)).toBeDisabled();
  expect(screen.getByText(/сначала объявите базовую ставку/i)).toBeInTheDocument();
});

it("отправляет только изменённое поле, а не оба", async () => {
  const patch = vi.fn().mockResolvedValue({});
  render(<VatRateDialog estimate={estimateWithRate} contractId={1} />);
  await userEvent.clear(screen.getByLabelText(/ставка показа/i));
  await userEvent.type(screen.getByLabelText(/ставка показа/i), "16");
  await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
  expect(patch).toHaveBeenCalledWith(expect.anything(), { target: "16" });
});

it("снятие ставки отправляет null, а не пустую строку", async () => {
  const patch = vi.fn().mockResolvedValue({});
  render(<VatRateDialog estimate={estimateWithTarget} contractId={1} />);
  await userEvent.click(screen.getByRole("button", { name: /снять ставку показа/i }));
  expect(patch).toHaveBeenCalledWith(expect.anything(), { target: null });
});

it("отправляет строку, а не число: float в ставке запрещён", async () => {
  const patch = vi.fn().mockResolvedValue({});
  render(<VatRateDialog estimate={estimateWithRate} contractId={1} />);
  await userEvent.type(screen.getByLabelText(/ставка показа/i), "16.5");
  await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
  expect(patch.mock.calls[0][1].target).toBe("16.5");
});

it("показывает текст 422 сервера, а не общее «ошибка»", async () => {
  server.use(http.patch("/api/v1/estimates/:id/vat", () =>
    HttpResponse.json({ detail: "Сначала объявите базовую ставку." }, { status: 422 })));
  render(<VatRateDialog estimate={estimateWithRate} contractId={1} />);
  await userEvent.click(screen.getByRole("button", { name: /сохранить/i }));
  expect(await screen.findByText(/сначала объявите базовую ставку/i)).toBeInTheDocument();
});

it("member не видит управления ставкой", () => {
  render(<VatRateDialog estimate={estimateWithRate} contractId={1} />, { role: "member" });
  expect(screen.queryByRole("button", { name: /изменить ставку/i })).not.toBeInTheDocument();
});
```

```tsx
// frontend/src/pages/passport/ProjectPassportPage.test.tsx
it("без базы предлагает объявить ставку файла", () => {
  render(<PassportHeader passport={passportWithoutVatRate} />);
  expect(screen.getByText(/ставка НДС не заявлена в файле/)).toBeInTheDocument();
});

it("показывает назначенную вручную базу рядом с заявленной файлом", () => {
  render(<PassportHeader passport={passportWithBaseOverride} />);
  expect(screen.getByText(/база НДС 12 назначена вручную/)).toBeInTheDocument();
  expect(screen.getByText(/файл заявил 20/)).toBeInTheDocument();
});

it("печатная сноска о поправке присутствует и не скрыта от печати", () => {
  render(<PassportHeader passport={passportWithTarget} />);
  const note = screen.getByTestId("vat-print-note");
  expect(note).toHaveTextContent(/показано в ставке 16, пересчитано с 20/);
  expect(note).not.toHaveAttribute("data-print", "hide");
});

it("расхождение нетто видно при mismatch и несёт число нарушителей", () => {
  render(<PassportHeader passport={passportWithNetMismatch} />);
  expect(screen.getByTestId("net-reconciliation")).toHaveTextContent(/предложений: 2/);
});

it("при статусе ok сверка нетто не показывается вовсе", () => {
  render(<PassportHeader passport={passportWithNetOk} />);
  expect(screen.queryByTestId("net-reconciliation")).not.toBeInTheDocument();
});
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd frontend && npx vitest run src/pages/passport`
Expected: FAIL — компонента нет, узлов нет

- [ ] **Шаг 3: написать диалог**

```tsx
// frontend/src/pages/passport/VatRateDialog.tsx
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSetEstimateVat } from "@/services/queries";
import type { ID, ProjectPassportEstimate } from "@/types/domain";

/**
 * Правка ставок НДС сметы.
 *
 * Поля СТРОКОВЫЕ и уезжают строками: `Number()` над ставкой — это `float`,
 * запрещённый §3 на всех слоях. Отсутствующее поле и `null` различаются
 * намеренно: первое значит «не менять», второе — «снять», и слать оба сразу
 * нельзя, иначе снятие цели затирало бы базу.
 */
export function VatRateDialog({
  estimate,
  contractId,
  canEdit,
}: {
  estimate: ProjectPassportEstimate;
  contractId: ID;
  canEdit: boolean;
}) {
  const base = estimate.vat_rate_base_override ?? estimate.vat_rate;
  const [baseDraft, setBaseDraft] = useState(estimate.vat_rate_base_override ?? "");
  const [targetDraft, setTargetDraft] = useState(estimate.vat_rate_target ?? "");
  const mutation = useSetEstimateVat();

  if (!canEdit) return null;

  const submit = (input: { base_override?: string | null; target?: string | null }) =>
    mutation.mutate({ estimateId: estimate.id, contractId, input });

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">Изменить ставку</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Ставка НДС сметы</DialogTitle>
        </DialogHeader>

        <Label htmlFor="vat-base">Базовая ставка (какой соответствуют суммы файла)</Label>
        <Input id="vat-base" inputMode="decimal" value={baseDraft}
               onChange={(e) => setBaseDraft(e.target.value)} />

        <Label htmlFor="vat-target">Ставка показа</Label>
        <Input id="vat-target" inputMode="decimal" value={targetDraft}
               disabled={base === null}
               onChange={(e) => setTargetDraft(e.target.value)} />
        {base === null && (
          <p role="note">Сначала объявите базовую ставку — пересчитывать не от чего.</p>
        )}

        {mutation.isError && <p role="alert">{errorText(mutation.error)}</p>}

        <DialogFooter>
          <Button variant="ghost"
                  onClick={() => submit({ target: null })}>Снять ставку показа</Button>
          <Button onClick={() => submit(changedOnly(estimate, baseDraft, targetDraft))}>
            Сохранить
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Только изменённые поля: неизменённое не отправляется вовсе (§2.7). */
function changedOnly(
  estimate: ProjectPassportEstimate, baseDraft: string, targetDraft: string,
) {
  const input: { base_override?: string | null; target?: string | null } = {};
  const asValue = (draft: string) => (draft.trim() === "" ? null : draft.trim());
  if (asValue(baseDraft) !== (estimate.vat_rate_base_override ?? null)) {
    input.base_override = asValue(baseDraft);
  }
  if (asValue(targetDraft) !== (estimate.vat_rate_target ?? null)) {
    input.target = asValue(targetDraft);
  }
  return input;
}
```

- [ ] **Шаг 4: три бейджа, сноска и сверка в шапке**

```tsx
// frontend/src/pages/passport/PassportHeader.tsx
function vatSummary(estimate: ProjectPassportEstimate): string {
  const declared = estimate.vat_rate;
  const base = estimate.vat_rate_base_override ?? declared;
  const target = estimate.vat_rate_target;

  if (base === null) return "ставка НДС не заявлена в файле";
  if (estimate.vat_rate_base_override !== null) {
    const suffix = declared === null ? "" : ` (файл заявил ${formatPercentDecimal(declared)})`;
    const head = `база НДС ${formatPercentDecimal(base)} назначена вручную${suffix}`;
    return target === null || target === base
      ? head
      : `${head}; показано в ставке ${formatPercentDecimal(target)}`;
  }
  if (target !== null && target !== base) {
    return `показано в ставке ${formatPercentDecimal(target)}, пересчитано с ${formatPercentDecimal(base)}`;
  }
  return `ставка НДС ${formatPercentDecimal(base)}`;
}
```

Сноска печатается **всегда**, когда поправка действует: она лежит под таблицей и
раскрытию дерева не подчинена — тот же приём, что у сноски о ручном разносе.
`data-print="hide"` на ней стоять не имеет права.

Сверка нетто показывается **только при `mismatch`**, как существующая сверка
`delta_to_file_total`, и несёт число предложений-нарушителей — без него аналитик
увидел бы расхождение и не нашёл источник:

```tsx
{totals.net_reconciliation.status === "mismatch" && (
  <p data-testid="net-reconciliation">
    Выведенное нетто расходится с заявленным в файле; предложений:{" "}
    {totals.net_reconciliation.mismatched_proposal_ids.length}
  </p>
)}
```

- [ ] **Шаг 5: прогнать тесты и типы**

```bash
cd frontend && npx vitest run && npx tsc -b --noEmit
```
Expected: PASS

- [ ] **Шаг 6: коммит**

```bash
git add frontend/src/pages/passport
git commit -m "feat(frontend): диалог ставки НДС, бейджи, сноска и сверка нетто"
```

---

## Задача 11: замер печатной раскладки А4 в браузере

`@media print` в jsdom не наблюдаем, и расчёт «на бумаге» ошибается — это уже
стоило проекту двух дефектов (`AGENTS.md` §11). Проверяется только замером.

**Files:**
- Modify: `docs/devlog/2026-08-13-vat-rate-recalculation.md`

- [ ] **Шаг 1: поднять стенд и открыть паспорт**

Окружение — `playwright-core` + системный Chrome (`channel: "chrome"`), как в Ф5 и
Ф6; браузеры не скачиваются, зависимости проекта не правятся.

- [ ] **Шаг 2: замерить четыре обязательства**

Для сметы **с действующей поправкой**: нет обрезки по правому краю; нет разрывов
внутри строк; шапка таблицы повторяется на каждом листе; сноска о поправке
присутствует на бумаге.

- [ ] **Шаг 3: записать замер числами**

Ширина контента против ширины листа, высота, число листов, консольные сообщения
(ожидается 0). Вердикт «пройдено» без чисел не засчитывается.

- [ ] **Шаг 4: коммит**

```bash
git add docs/devlog/2026-08-13-vat-rate-recalculation.md
git commit -m "docs(devlog): замер печатной раскладки А4 с поправкой ставки"
```

---

## Задача 12: негативные проверки снятием защиты

Тринадцать снятий спеки §4.5. **Делает оркестратор лично**, по протоколу
[verifying-guards.md](../../insights/verifying-guards.md) целиком.

**Files:**
- Modify: `docs/devlog/2026-08-13-vat-rate-recalculation.md`

- [ ] **Шаг 1: контрольный прогон до снятия**

```bash
cd backend && uv run pytest -q | tail -3
```
Записать число прошедших. Без него красный прогон не с чем сравнить.

- [ ] **Шаг 2: снять защиту, сверить, что снятие применилось**

Для каждой из тринадцати: побайтовая копия файла, `assert old in text` до правки,
sha256 до и после. Восстанавливать — **из копии**, не `git checkout --`: он даёт
CRLF и ложную тревогу. Перечень — спека §4.5, пункты 1–13.

- [ ] **Шаг 3: по каждому снятию записать, что покраснело**

Снятие, не уронившее ничего, означает **«защиты нет»**, а не «тест плох».

- [ ] **Шаг 4: назвать соседний слой защиты**

Кандидаты в пару названы спекой заранее: 2 и 3 стерегут один инвариант с разных
сторон, 1 и 7 обе защищают числа Ф6. Если дефект не воспроизводится одним снятием
— это записывается замером, а не объявляется «дефект воспроизведён».

- [ ] **Шаг 5: коммит**

```bash
git add docs/devlog/2026-08-13-vat-rate-recalculation.md
git commit -m "docs(devlog): тринадцать снятий защиты"
```

---

## Задача 13: ревизия `AGENTS.md`, рамка фазы, стенд, PR

**Files:**
- Modify: `AGENTS.md` (§4, §7.6, §10)
- Modify: `docs/phase7-frame.md`
- Modify: `docs/devlog/2026-08-13-vat-rate-recalculation.md`

- [ ] **Шаг 1: ревизия §4**

Формула отклонения — нетто-версия; `standard_unit_rate` объявлен ценой **без НДС**;
вторая причина пустого отклонения. Врезкой — причина ревизии и ссылка на спеку.

- [ ] **Шаг 2: ревизия §7.6**

Четыре класса с приоритетом; норматив и отклонение в деньгах — нетто; подпись
ставки в шапке листа.

- [ ] **Шаг 3: записать отступление от §2.6**

В devlog — единственное нетто-выражение в SQL, его причина (сортировка и
пагинация) и тест, который пришпиливает его к `money.vat`.

- [ ] **Шаг 4: соответствие «требование → тест»**

Список: поведенческое требование §2 спеки → тест, который его исполняет.
Требование без исполнителя либо получает тест, либо **объявляется границей** в
devlog. Молчаливого третьего варианта нет.

- [ ] **Шаг 5: перевести стенд**

```bash
just db-dev-init
```
Затем удалить заведённые нормативы и завести заново в семантике нетто. Сметы **не**
перезаливать: контракт парсера не менялся. Сверить счётчики договоров, объектов,
подрядчиков, смет и каталога (1932) до и после.

- [ ] **Шаг 6: прогнать `just ci` целиком**

```bash
just ci
echo "EXIT=$?"
```
Expected: `EXIT=0`. Читать **код возврата**, а не хвост вывода: конвейер возвращает
код последней команды, и падение приходит как `exit 0`.

- [ ] **Шаг 7: коммит и PR**

```bash
git add AGENTS.md docs/
git commit -m "docs: ревизия AGENTS.md §4 и §7.6, devlog фичи"
git push -u origin feat/vat-rate-recalculation
```

PR со ссылками на рамку фазы, спеку и этот план.

---

## Самопроверка плана

**Покрытие спеки.** §2.1 → задача 3; §2.2 → задачи 1, 2; §2.3 → задачи 0 (замер В),
1, 8 (однократное квантование); §2.4 → задачи 1, 8; §2.5 → задачи 3, 4, 8, 13;
§2.6 → задачи 3, 5 (+ названное отступление); §2.7 → задача 6; §2.8 → задача 10;
§2.9 → задача 4; §2.10 → задачи 2, 5, 10; §2.11 → задача 7; §2.12 → задача 13;
§4.5 → задача 12; §6 DoD → задачи 11, 12, 13.

**Известное упрощение, названное вслух:** фабрики тестов (`priced_estimate`,
`priced_estimate_with_two_proposals`, `contract_with_standard`,
`estimate_with_proposals`, `set_vat_target`, `set_vat_rates`,
`contract_with_consistent_summary`, `contract_with_three_proposals_of`,
`contract_with_two_proposals`, `bank_report_fixture`, `imported_estimate`, `upload`)
в `backend/tests/factories.py` частью ещё не существуют. Каждая заводится в той
задаче, где впервые вызвана, тем же коммитом: фабрика без потребителя не
проверяется ничем.

**Согласованность имён.** `DEVIATION_INPUTS` заводится в задаче 3, там же получает
переходный алиас `DEVIATIONS`; алиас снимается шагом 5 задачи 5 с прогоном всего
backend. `gross_to_net`/`net_to_gross`/`vat_from_net`/`restate_gross`/
`quantize_money`/`effective_display_rate` — модуль `money.vat`;
`check_proposal_net`/`fold_net_reconciliation`/`NetStatus` — там же, задача 2.
`quantize_money` заменила `serialize_amount` черновика: округление перестало быть
свойством одного значения и стало операцией границы ответа.

**Ни один коммит не оставляет ветку красной.** Задачи 3 (миграция + аналитика) и 6
(сервис + роутер) слиты именно поэтому; в обеих промежуточное состояние было бы
заведомо красным.
