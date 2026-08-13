# Пересчёт сумм сметы по изменённой ставке НДС — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** дать администратору задать смете базовую и целевую ставку НДС так, чтобы
все денежные величины приложения считались от нетто и показывались в ставке,
объявленной для каждой поверхности.

**Architecture:** в БД по-прежнему лежат валовые деньги файла; две новые колонки на
`estimates` несут решение человека, а не факт файла. Правило пересчёта записано
один раз, на Python, в `backend/money/vat.py`; SQL агрегирует до уровня, на котором
множитель постоянен (предложение), и формулы не содержит. Ось сравнения — нетто,
поэтому отклонения не зависят от ставки показа.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x sync ORM, Alembic, psycopg3,
PostgreSQL 16; React + TS, Vite, shadcn/ui, TanStack Query, Vitest.

**Spec:** [docs/superpowers/specs/2026-08-13-vat-rate-recalculation-design.md](../specs/2026-08-13-vat-rate-recalculation-design.md)

**Ветка:** `feat/vat-rate-recalculation` (уже создана, спека в ней закоммичена)

## Global Constraints

- **Деньги: `numeric` в БД ↔ `Decimal` в Python ↔ строки в JSON. Никаких `float`** —
  `AGENTS.md` §3. В тестах сравнивать `Decimal` с `Decimal`.
- **Ставки НДС хранятся в процентных пунктах** (`20`, не `0.20`) — спека Ф4б §2.9.
- **`ARITHMETIC_PRECISION = 100` — значащих цифр**, не знаков после запятой
  ([summary_block.py:154](../../../backend/parser/summary_block.py#L154)).
- **Глобальный контекст `Decimal` приложения не меняется** — отдельный тест, как у
  Ф4a и Ф4б.
- **COALESCE-уникальные, частичные, EXCLUDE-индексы и VIEW — только raw SQL** через
  `op.execute()`; `downgrade` снимает их явно (`AGENTS.md` §11).
- **Миграция обязана быть неизменной во времени:** литералы в ней записываются
  строками, а не импортируются из моделей.
- **`users.id` — `integer`, не `bigint`;** FK на него повторяет тип
  ([0011:16](../../../backend/alembic/versions/2026_08_11_0011-category_overrides.py#L16)).
- **PostgreSQL той же мажорной версии, что в проде (16)** — в тестах тоже.
- **Перед пушем — `just ci`**, шагами по отдельности. `vitest` гонять **только из
  `frontend/`**; `tsc -b --noEmit` (`AGENTS.md` §11).
- **Число собранных тестов замеряется `pytest --collect-only -q`** до и после каждой
  задачи, а не выводится арифметикой.
- **Политика `samples/`:** реальные суммы и реквизиты контрагентов не попадают ни в
  код, ни в тесты, ни в доки, ни в сообщения коммитов.

## Этапы

Фича одна, PR один, приёмка единая. Реализация делится надвое (спека §6):

- **Этап 1 — нетто-ось:** задачи 0–7. Ставку человек ещё не правит; меняется смысл
  сравнения и появляется диагностика.
- **Этап 2 — ручные ставки:** задачи 8–15. Появляются API, экран, аудит и показ в
  целевой ставке.

---

## Задача 0: четыре замера (гейт, кода нет)

Нулевая задача обязательна и идёт **до всего остального** (спека §1.6). Каждый
замер способен отменить решение, под которым стоит. **При расхождении — остановиться,
исправить спеку и пройти гейт 2 повторно**, а не править дизайн внутри реализации.

**Files:**
- Create: `docs/devlog/2026-08-13-vat-rate-recalculation.md` (раздел «Замеры»)

- [ ] **Шаг 1: замер А — лоты, предложения и ставки корпуса**

Против стенда `gca_dev`:

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

Записать таблицу в devlog. **Отменяет границу §5.1**, если найдётся смета, где
`distinct_rates > 1`: тогда «одна ручная база на смету» — дефект, а не ограничение.

- [ ] **Шаг 2: замер Б — объёмы и план запроса матрицы**

```sql
SELECT count(*) FROM position_items WHERE is_chapter = false;
SELECT count(*) FROM v_position_deviations;
```

Затем `EXPLAIN (ANALYZE, BUFFERS)` на текущем запросе ячеек матрицы (без фильтров,
самый широкий охват). Записать план и время. **Определяет механизм** доставки
множителя в задаче 4: группировка по базе против join на `VALUES`.

- [ ] **Шаг 3: замер В — стабильность округления до копеек**

```python
from decimal import Decimal
from backend.money.vat import gross_to_net, net_to_gross   # ещё не существует — считать вручную теми же формулами
# предельные суммы договоров ГП: взять максимум total_cost_total со стенда,
# домножить на 10 и на 100, прогнать пары (база, цель) из {0, 12, 16, 20, 22}
```

Для каждой пары проверить: `money_round(показ, 2)` при повторном прогоне тех же
входов даёт тот же результат, и `Σ money_round(показ_i)` отличается от
`money_round(Σ показ_i)` не более чем на копейку на строку. Записать максимальное
расхождение числом. **Отменяет §2.3**, если итог «плавает»: понадобится
фиксированная схема округления, а не «округляем на слое представления».

- [ ] **Шаг 4: замер Г — допуск `NET_RECONCILIATION_TOLERANCE`**

По каждому предложению стенда с известной ставкой и полным блоком итогов:

```sql
SELECT p.id,
       (SELECT total_cost FROM proposal_summary_lines
         WHERE proposal_id = p.id AND summary_key = 'total_cost_including_vat') AS gross,
       (SELECT total_cost FROM proposal_summary_lines
         WHERE proposal_id = p.id AND summary_key = 'total_cost_excluding_vat') AS file_net,
       p.vat_rate
FROM proposals p;
```

Посчитать `gross × 100 / (100 + vat_rate) − file_net` в `Decimal` и записать
максимум по модулю. Константа берётся **на два порядка выше** замеренного шума и
записывается в devlog вместе с обоими числами.

- [ ] **Шаг 5: зафиксировать замеры и решение**

Если все четыре согласуются со спекой — записать в devlog «расхождений нет, гейт 2
в силе» и продолжать. Если хоть один разошёлся — **остановиться**, вынести
расхождение пользователю, править спеку.

- [ ] **Шаг 6: замерить базу тестов**

```bash
cd backend && uv run pytest --collect-only -q | tail -3
cd frontend && npx vitest run --reporter=dot 2>&1 | tail -5
```

Записать оба числа в devlog как базу для всех последующих задач.

- [ ] **Шаг 7: коммит**

```bash
git add docs/devlog/2026-08-13-vat-rate-recalculation.md
git commit -m "docs(devlog): четыре замера нулевой задачи, база тестов"
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
  - `serialize_amount(restated: RestatedAmount) -> Decimal | None`
  - `AmountStatus` (`original`, `restated`, `unknown_base`, `not_finite`)
  - `RestatedAmount(amount: Decimal | None, status: AmountStatus)`

- [ ] **Шаг 1: написать падающий тест на три функции и тождество**

```python
# backend/tests/unit/test_money_vat.py
from decimal import Decimal, getcontext

import pytest

from money.vat import (
    AmountStatus,
    gross_to_net,
    net_to_gross,
    restate_gross,
    serialize_amount,
    vat_from_net,
)


def test_gross_to_net_removes_declared_vat():
    assert gross_to_net(Decimal("120"), Decimal("20")) == Decimal("100")


def test_net_to_gross_adds_target_vat():
    assert net_to_gross(Decimal("100"), Decimal("16")) == Decimal("116")


def test_vat_from_net_is_zero_at_zero_target():
    """Тест против универсального множителя: (100+0)/(100+20) дало бы не ноль."""
    assert vat_from_net(Decimal("100"), Decimal("0")) == Decimal("0")


def test_restate_keeps_object_untouched_when_target_equals_base():
    gross = Decimal("100.50")
    result = restate_gross(gross, Decimal("20"), Decimal("20"))
    assert result.status is AmountStatus.ORIGINAL
    assert result.amount == gross
    assert result.amount.as_tuple().exponent == gross.as_tuple().exponent


def test_restate_keeps_object_untouched_when_target_not_set():
    gross = Decimal("100.50")
    result = restate_gross(gross, Decimal("20"), None)
    assert result.status is AmountStatus.ORIGINAL
    assert result.amount.as_tuple().exponent == gross.as_tuple().exponent


def test_restate_without_base_reports_unknown_and_returns_source():
    gross = Decimal("100.50")
    result = restate_gross(gross, None, Decimal("16"))
    assert result.status is AmountStatus.UNKNOWN_BASE
    assert result.amount is gross


def test_restate_recalculates_when_target_differs():
    result = restate_gross(Decimal("120"), Decimal("20"), Decimal("16"))
    assert result.status is AmountStatus.RESTATED
    assert result.amount == Decimal("116")


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_restate_does_not_fail_on_non_finite(raw):
    gross = Decimal(raw)
    result = restate_gross(gross, Decimal("20"), Decimal("16"))
    assert result.status is AmountStatus.NOT_FINITE
    assert result.amount is gross


def test_serialize_rounds_only_restated_amounts():
    """Округление исходного значения сдвинуло бы exponent и сломало тождество."""
    original = restate_gross(Decimal("100.5"), Decimal("20"), Decimal("20"))
    assert serialize_amount(original) == Decimal("100.5")
    assert serialize_amount(original).as_tuple().exponent == -1

    restated = restate_gross(Decimal("120"), Decimal("20"), Decimal("16"))
    assert serialize_amount(restated) == Decimal("116.00")


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
"""Денежные правила приложения. Парсер сюда не импортируется в обратную сторону."""
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

#: Ставки в процентных пунктах, поэтому делить и умножать приходится на сто.
#: Контекст строится ЯВНО и не наследует глобальный: `localcontext()` без
#: аргумента копирует текущий контекст ВМЕСТЕ С ЕГО ТРАПАМИ, и включённый
#: где-то `Inexact` превратил бы штатное деление в исключение. Округление здесь
#: РАЗРЕШЕНО — частное почти никогда не представимо конечной десятичной дробью
#: (тот же довод и та же форма, что у `parser/vat_rate.py`).
_VAT_CONTEXT = Context(
    prec=ARITHMETIC_PRECISION,
    traps=[Overflow, DivisionByZero, InvalidOperation],
)

#: Допуск сверки выведенного нетто с файловым, в рублях. Снят замером Г задачи 0
#: и записан в devlog вместе с замеренным шумом.
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

    Ветка тождества — ТРЕБОВАНИЕ, а не оптимизация: пока цель равна базе,
    значение обязано вернуться нетронутым, вместе со своим `exponent`.
    Умножение на единицу его сдвинуло бы, и посимвольное совпадение денежных
    полей (§2.4) перестало бы выполняться.
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


def serialize_amount(restated: RestatedAmount) -> Decimal | None:
    """Значение для выдачи наружу: округляется ТОЛЬКО пересчитанное.

    Округлять исходное нельзя: `Decimal('100.5')` стал бы `Decimal('100.50')`,
    и посимвольное сравнение §2.4 упало бы там, где ничего не менялось.
    """
    if restated.status is not AmountStatus.RESTATED or restated.amount is None:
        return restated.amount
    return money_round(restated.amount, 2)
```

- [ ] **Шаг 4: прогнать тесты**

Run: `cd backend && uv run pytest tests/unit/test_money_vat.py -v`
Expected: PASS, 11 тестов

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
- Consumes: `gross_to_net`, `NET_RECONCILIATION_TOLERANCE` из задачи 1.
- Produces:
  - `NetStatus` (`ok`, `mismatch`, `unknown_base`, `not_applicable`)
  - `ProposalNetCheck(proposal_id: int, status: NetStatus, delta: Decimal | None)`
  - `NetReconciliation(status: NetStatus, delta: Decimal | None, mismatched_proposal_ids: list[int])`
  - `check_proposal_net(proposal_id: int, gross_total: Decimal | None, file_net: Decimal | None, base: Decimal | None) -> ProposalNetCheck`
  - `fold_net_reconciliation(checks: Sequence[ProposalNetCheck]) -> NetReconciliation`

- [ ] **Шаг 1: написать падающий тест на свёртку**

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
        [
            _check(1, NetStatus.UNKNOWN_BASE),
            _check(2, NetStatus.MISMATCH, Decimal("10")),
        ]
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
    folded = fold_net_reconciliation([_check(1, NetStatus.UNKNOWN_BASE)])
    assert folded.delta is None
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

    operands = (gross_total, file_net)
    if any(value is None or not value.is_finite() for value in operands):
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
Expected: PASS, 20 тестов

- [ ] **Шаг 5: коммит**

```bash
git add backend/money/vat.py backend/tests/unit/test_money_vat.py
git commit -m "feat(money): свёртка сверки нетто по смете"
```

---

## Задача 3: миграция 0012 — колонки, переименование VIEW, группировка

**Files:**
- Create: `backend/alembic/versions/2026_08_13_0012-vat_rate_recalculation.py`
- Modify: `backend/models.py` (класс `Estimate`, строки 438–473)
- Test: `backend/tests/integration/test_schema_constraints.py`

**Interfaces:**
- Produces: колонки `estimates.vat_rate_base_override`, `estimates.vat_rate_target`,
  `estimates.vat_rate_updated_by_id`, `estimates.vat_rate_updated_at`; VIEW
  `v_position_deviation_inputs` (колонка `deviation_pct` отсутствует, добавлены
  `vat_rate_base` и `vat_rate_target`); VIEW `v_category_totals` с `proposal_id` и
  `vat_rate_base` в выдаче и группировке.

- [ ] **Шаг 1: написать падающий тест схемы**

```python
# дописать в backend/tests/integration/test_schema_constraints.py
import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration


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
    exists = db_session.execute(
        sa.text("SELECT to_regclass('v_position_deviations')")
    ).scalar()
    assert exists is None


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

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_schema_constraints.py -k "vat or deviation_inputs or category_totals_view_groups" -v`
Expected: FAIL — `UndefinedColumn` / `assert None is None` не выполняется

- [ ] **Шаг 3: написать миграцию**

```python
# backend/alembic/versions/2026_08_13_0012-vat_rate_recalculation.py
"""Ручные ставки НДС на смете, нетто-ось сравнения.

Фаза 7, фича пересчёта. Три изменения схемы, все — следствия спеки:

1. Четыре колонки на `estimates`: база, назначенная человеком; ставка показа;
   автор и время последней правки. `proposals.vat_rate` не трогается — он
   остаётся неприкосновенным фактом файла, правка живёт рядом, а не поверх.
   Тип автора — `integer`, как `users.id` (то же уточнение, что в 0011).

2. `v_position_deviations` → `v_position_deviation_inputs`. Колонка
   `deviation_pct` УБРАНА: она считалась на валовой цене, а норматив объявлен
   ценой без НДС, и оставленная колонка стала бы молча неверной. Имя меняется
   вместе со смыслом — VIEW без отклонения, названный `…_deviations`, это тот
   же класс ошибки, что `total_cost_with_vat` со значением «без НДС».

3. `v_category_totals` группируется ДОПОЛНИТЕЛЬНО по предложению и его базовой
   ставке — то есть до уровня, на котором множитель пересчёта постоянен.
   Умножение остаётся в Python: правило обязано быть записано один раз и на
   одном языке.

Литералы записаны строками: миграция обязана быть неизменной во времени (то же
правило, что у 0002–0011).

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-13
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
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

_FINITE_POSITIONS = """
        WHERE pi.total_cost_total <> 'NaN'::numeric
          AND pi.total_cost_total <> 'Infinity'::numeric
          AND pi.total_cost_total <> '-Infinity'::numeric
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
CREATE VIEW v_category_totals AS
SELECT
    l.estimate_id,
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
LEFT JOIN position_items ch
       ON ch.id = pi.chapter_item_id
      AND ch.proposal_id = pi.proposal_id
WHERE pi.is_chapter = false
GROUP BY l.estimate_id, ch.work_category_id
UNION ALL
SELECT
    l.estimate_id,
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
GROUP BY l.estimate_id, aw.work_category_id
"""


def upgrade() -> None:
    op.add_column("estimates", sa.Column("vat_rate_base_override", sa.Numeric(), nullable=True))
    op.add_column("estimates", sa.Column("vat_rate_target", sa.Numeric(), nullable=True))
    op.add_column("estimates", sa.Column("vat_rate_updated_by_id", sa.Integer(), nullable=True))
    op.add_column(
        "estimates",
        sa.Column("vat_rate_updated_at", sa.DateTime(timezone=True), nullable=True),
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
    op.execute("DROP VIEW IF EXISTS v_category_totals")
    op.execute("DROP VIEW IF EXISTS v_position_deviation_inputs")
    # VIEW снимаются ДО колонок: оба ссылаются на vat_rate_base_override.
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

- [ ] **Шаг 4: дописать модель**

```python
# backend/models.py, класс Estimate — после import_job_id (строка 450)
    # Ручные ставки НДС (спека пересчёта §2.1). Решение человека о СМЕТЕ, а не
    # факт файла: `proposals.vat_rate` остаётся неприкосновенным, правка живёт
    # рядом. База перекрываема всегда, в том числе поверх заявленной файлом, —
    # файл умеет ошибиться, и это обязано лечиться приложением.
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

- [ ] **Шаг 5: прогнать миграцию и тесты**

```bash
cd backend && uv run alembic upgrade head && uv run alembic check
uv run pytest tests/integration/test_schema_constraints.py -v
```
Expected: `alembic check` чист, тесты схемы PASS

- [ ] **Шаг 6: круговой рейс**

```bash
cd backend && uv run alembic downgrade base && uv run alembic upgrade head
```
Expected: обе команды без ошибок. Падение на `downgrade` — дефект порядка снятия
VIEW и колонок, а не повод пропустить шаг.

- [ ] **Шаг 7: проверить партией больше пяти**

```bash
cd backend && uv run pytest tests/integration/test_category_totals_view.py -v
```
Expected: PASS. `prepare_threshold = 5` в psycopg3 — партия из 5 проходит, из 10
падает; VIEW обязан проверяться партией, а не одной строкой.

- [ ] **Шаг 8: коммит**

```bash
git add backend/alembic/versions/2026_08_13_0012-vat_rate_recalculation.py backend/models.py backend/tests/integration/test_schema_constraints.py
git commit -m "feat(db): миграция 0012 — ручные ставки НДС, нетто-ось VIEW"
```

---

## Задача 4: `crud/analytics.py` — отражение VIEW и нетто-матрица

**Files:**
- Modify: `backend/crud/analytics.py:61-83` (отражение), `:436-471` (`_cells_cte`), `:631-651` (сборка строк)
- Test: `backend/tests/integration/test_analytics_api.py`

**Interfaces:**
- Consumes: `gross_to_net` из `money.vat`; VIEW `v_position_deviation_inputs`.
- Produces: `DEVIATION_INPUTS` (переименованное отражение), ячейка матрицы —
  средневзвешенная **нетто**-ставка.

- [ ] **Шаг 1: написать падающий тест**

```python
# дописать в backend/tests/integration/test_analytics_api.py
def test_matrix_cell_rate_is_net_of_declared_vat(client, factories, db_session):
    """Ячейка матрицы — нетто-ставка: 120 при заявленных 20 % дают 100."""
    estimate = factories.priced_estimate(unit_cost_total=Decimal("120"), vat_rate=Decimal("20"))
    db_session.commit()
    body = client.get("/api/v1/analytics/matrix").json()
    cell = body["rows"][0]["cells"][0]
    assert Decimal(cell["rate"]) == Decimal("100.00")


def test_matrix_cell_rate_is_null_without_vat_base(client, factories, db_session):
    factories.priced_estimate(unit_cost_total=Decimal("120"), vat_rate=None)
    db_session.commit()
    body = client.get("/api/v1/analytics/matrix").json()
    cell = body["rows"][0]["cells"][0]
    assert cell["rate"] is None
    assert cell["deviation_reason"] == "unknown_vat_base"
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_analytics_api.py -k "net_of_declared or without_vat_base" -v`
Expected: FAIL — `rate` равен `120`, ключа `deviation_reason` нет

- [ ] **Шаг 3: переименовать отражение**

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

#: Прежнее имя оставлено алиасом на время правки потребителей и удаляется
#: последней задачей этапа 1: одновременное переименование в шести местах
#: прячет опечатку до попадания на стенд.
DEVIATIONS = DEVIATION_INPUTS
```

- [ ] **Шаг 4: перевести ячейку матрицы на нетто**

SQL агрегирует до уровня постоянного множителя — до базы; умножает Python.

```python
# backend/crud/analytics.py, _cells_cte — добавить базу в выдачу и группировку
def _cells_cte(scope_filters: list):
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
    )
```

```python
# backend/crud/analytics.py, сборка строк — процент и ставка считаются в Python
def _net_rate(weighted_cost, weight_total, base):
    """Средневзвешенная НЕТТО-ставка ячейки; `None`, если базы нет."""
    if weighted_cost is None or weight_total is None or weight_total == 0:
        return None
    if base is None:
        return None
    return gross_to_net(weighted_cost / weight_total, base)


def _deviation(net_rate, standard):
    if net_rate is None or standard is None or standard == 0:
        return None
    return (net_rate / standard - 1) * 100
```

Строки одной ячейки, пришедшие с разными базами, складываются по формуле §6
дословно: `SUM(cost × w)` и `SUM(w)` берутся по каждой базе, нетто считается по
своей базе, затем взвешенно сводится:

```python
def _fold_cell(groups):
    """`groups` — строки одной ячейки в разрезе базы НДС."""
    net_sum = Decimal(0)
    weight_sum = Decimal(0)
    for group in groups:
        if group.vat_rate_base is None or group.weight_total in (None, 0):
            return None  # хотя бы одна база неизвестна — ячейка честно пуста
        net_sum += gross_to_net(group.weighted_cost, group.vat_rate_base)
        weight_sum += group.weight_total
    if weight_sum == 0:
        return None
    return net_sum / weight_sum
```

- [ ] **Шаг 5: добавить причину пустого отклонения**

```python
# backend/crud/analytics.py, форма ячейки
def _deviation_reason(net_rate, standard) -> str | None:
    """Код причины пустого отклонения. Два разных факта — два разных кода:
    §4 требует отличать «нет норматива» от нуля, и то же требование
    распространяется на «неизвестна база НДС»."""
    if net_rate is None:
        return "unknown_vat_base"
    if standard is None:
        return "no_standard"
    return None
```

- [ ] **Шаг 6: прогнать тесты аналитики**

Run: `cd backend && uv run pytest tests/integration/test_analytics_api.py -v`
Expected: PASS, включая `test_declared_view_columns_match_the_database` — он
сверяет объявление отражения с `information_schema` и обязан подтвердить
переименование.

- [ ] **Шаг 7: коммит**

```bash
git add backend/crud/analytics.py backend/tests/integration/test_analytics_api.py
git commit -m "feat(analytics): ячейка матрицы — средневзвешенная нетто-ставка"
```

---

## Задача 5: `crud/analytics.py` — паспорт фазы 6 на нетто

Endpoint `GET /api/v1/analytics/passport/{id}` жив и адаптируется, а не удаляется:
фаза 7 записала, что экран вернётся drill-down'ом из статьи.

**Files:**
- Modify: `backend/crud/analytics.py:160-183` (`_priced_positions_select`), `:245-260`, `:363-380` (`over_standard`)
- Test: `backend/tests/integration/test_analytics_api.py`

**Interfaces:**
- Consumes: `gross_to_net`, `_deviation`, `_deviation_reason` из задачи 4.
- Produces: строки паспорта фазы 6 с нетто-отклонением и кодом причины.

- [ ] **Шаг 1: написать падающий тест**

```python
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

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_analytics_api.py -k phase6_passport -v`
Expected: FAIL — `deviation_pct` равен `20`, `over_standard` равен `1`

- [ ] **Шаг 3: убрать чтение снятой колонки**

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
        "deviation_reason": _deviation_reason(net_unit_cost, r.standard_unit_rate),
    }
```

- [ ] **Шаг 4: перевести `over_standard`**

`COUNT` по снятой колонке в SQL больше невозможен — счётчик считается в Python по
тем же строкам, что и таблица:

```python
    over_standard = sum(
        1
        for row in rows
        if row["deviation_pct"] is not None and row["deviation_pct"] > 0
    )
```

- [ ] **Шаг 5: прогнать тесты**

Run: `cd backend && uv run pytest tests/integration/test_analytics_api.py -v`
Expected: PASS

- [ ] **Шаг 6: коммит**

```bash
git add backend/crud/analytics.py backend/tests/integration/test_analytics_api.py
git commit -m "feat(analytics): паспорт фазы 6 сравнивает по нетто"
```

---

## Задача 6: `crud/reports.py` — нетто, четыре класса, подпись ставки

**Files:**
- Modify: `backend/crud/reports.py:77` (`_NO_VOLUME`), `:87-91` (`_deviation_pct`), `:135-220`, `:360-375`
- Test: `backend/tests/integration/test_reports_api.py`

**Interfaces:**
- Consumes: `gross_to_net` из `money.vat`, `DEVIATION_INPUTS` из `crud.analytics`.
- Produces: разбиение позиций на четыре класса; счётчик `unknown_vat_base`.

- [ ] **Шаг 1: написать падающий тест на разбиение**

```python
def test_bank_report_partition_covers_every_priced_position(client, factories, db_session):
    """Четыре класса образуют разбиение: сумма сходится с независимым счётчиком."""
    factories.bank_report_fixture()  # по одной позиции каждого класса
    db_session.commit()
    body = client.get("/api/v1/reports/bank", params={"rate_class_id": 1}).json()
    totals = body["totals"]
    assert (
        totals["comparable"]
        + totals["no_volume"]
        + totals["unknown_vat_base"]
        + totals["no_standard"]
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
```

```python
# backend/crud/reports.py — факт и норматив приводятся к одной ставке
def _net_fact(row) -> Decimal | None:
    if row.vat_rate_base is None:
        return None
    return gross_to_net(row.unit_cost_total, row.vat_rate_base)
```

- [ ] **Шаг 4: подписать ставку в шапке листа**

```python
# backend/services/excel_reports.py — строка под заголовком отчёта «для банка»
    sheet.cell(row=header_row, column=1).value = (
        "Все суммы и нормативы — без НДС"
    )
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

## Задача 7: `crud/project_passport.py` — `net_reconciliation` в ответе

**Files:**
- Modify: `backend/crud/project_passport.py:341-400`, `:1156-1180`
- Test: `backend/tests/integration/test_project_passport_api.py`

**Interfaces:**
- Consumes: `check_proposal_net`, `fold_net_reconciliation`, `NetStatus` из `money.vat`.
- Produces: ключ `net_reconciliation` в блоке `totals` паспорта:
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

    База берётся эффективная (`COALESCE(override, vat_rate)`), а вот
    СОГЛАСОВАННОСТЬ САМОГО ФАЙЛА проверяется по `proposals.vat_rate` и живёт в
    парсере — это разные диагностики, и смешивать их нельзя (спека §2.10).
    """
    rows = db.execute(
        sa.select(
            Proposal.id,
            Proposal.vat_rate,
            _summary(JSON_KEY_TOTAL_COST_INCLUDING_VAT),
            _summary(JSON_KEY_TOTAL_COST_EXCLUDING_VAT),
        )
        .select_from(Proposal)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate.id)
    ).all()

    checks = [
        check_proposal_net(
            row.id,
            row.gross_total,
            row.file_net,
            estimate.vat_rate_base_override
            if estimate.vat_rate_base_override is not None
            else row.vat_rate,
        )
        for row in rows
    ]
    return fold_net_reconciliation(checks)
```

- [ ] **Шаг 4: положить в ответ, не тронув `delta_to_file_total`**

```python
# backend/crud/project_passport.py, сборка totals — ДОБАВИТЬ ключ, ничего не меняя выше
    reconciliation = _net_reconciliation(db, estimate)
    totals["net_reconciliation"] = {
        "status": reconciliation.status.value,
        "delta": reconciliation.delta,
        "mismatched_proposal_ids": reconciliation.mismatched_proposal_ids,
    }
```

`delta_to_file_total` остаётся ровно тем, чем был: валовое против валового, из
исходных файловых денег. Ручные ставки на него не влияют **никогда**.

- [ ] **Шаг 5: прогнать тесты**

Run: `cd backend && uv run pytest tests/integration/test_project_passport_api.py -v`
Expected: PASS

- [ ] **Шаг 6: снять алиас `DEVIATIONS`**

```python
# backend/crud/analytics.py — удалить строку
DEVIATIONS = DEVIATION_INPUTS
```

Прогнать весь backend: `cd backend && uv run pytest -q`. Любое оставшееся
употребление старого имени падает здесь, а не на стенде.

- [ ] **Шаг 7: коммит — конец этапа 1**

```bash
git add backend/crud backend/tests
git commit -m "feat(passport): сверка нетто по предложениям; конец этапа 1"
```

---

## Задача 8: `services/estimate_vat.py` — сервис правки ставок

**Files:**
- Create: `backend/services/estimate_vat.py`
- Test: `backend/tests/integration/test_estimate_vat_api.py`

**Interfaces:**
- Consumes: модель `Estimate` из задачи 3.
- Produces:
  - `class EstimateVatError(Exception)` с полем `code`
  - `UNSET` — часовой «поле не передано»
  - `set_vat_rates(db, *, estimate_id: int, base_override, target, user_id: int) -> Estimate`

- [ ] **Шаг 1: написать падающий тест инварианта**

```python
# backend/tests/integration/test_estimate_vat_api.py
import pytest
from decimal import Decimal

pytestmark = pytest.mark.integration


def test_target_rejected_when_any_proposal_has_unknown_base(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20"), None])
    db_session.commit()
    response = client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"}
    )
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
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12"})
    db_session.refresh(estimate)
    assert estimate.vat_rate_target == Decimal("16")


def test_null_clears_the_field(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": None})
    db_session.refresh(estimate)
    assert estimate.vat_rate_target is None


def test_clearing_base_with_target_kept_is_rejected(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[None])
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12"})
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    response = client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": None}
    )
    assert response.status_code == 422


def test_clearing_both_rates_clears_the_audit(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    db_session.refresh(estimate)
    assert estimate.vat_rate_updated_by_id is not None

    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": None})
    db_session.refresh(estimate)
    assert estimate.vat_rate_updated_by_id is None
    assert estimate.vat_rate_updated_at is None


def test_clearing_only_one_rate_keeps_the_audit(client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[None])
    client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12", "target": "16"}
    )
    client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": None})
    db_session.refresh(estimate)
    assert estimate.vat_rate_updated_by_id is not None
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

    new_base = estimate.vat_rate_base_override if isinstance(base_override, _Unset) else base_override
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

- [ ] **Шаг 4: прогнать тесты (упадут на отсутствии роутера — это задача 9)**

Run: `cd backend && uv run pytest tests/integration/test_estimate_vat_api.py -v`
Expected: FAIL, 404 — сервис есть, маршрута нет

- [ ] **Шаг 5: коммит**

```bash
git add backend/services/estimate_vat.py backend/tests/integration/test_estimate_vat_api.py
git commit -m "feat(estimates): сервис правки ставок НДС сметы"
```

---

## Задача 9: `routers/estimate_vat.py` — `PATCH`, права, коды ошибок

**Files:**
- Create: `backend/routers/estimate_vat.py`
- Modify: `backend/main.py` (регистрация роутера, рядом со строкой 145)
- Test: `backend/tests/integration/test_estimate_vat_api.py`

**Interfaces:**
- Consumes: `set_vat_rates`, `EstimateVatError`, `UNSET` из задачи 8; `require_admin` из `auth`.
- Produces: `PATCH /api/v1/estimates/{estimate_id}/vat`.

- [ ] **Шаг 1: дописать падающий тест прав и гонки**

```python
def test_member_is_forbidden(member_client, factories, db_session):
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    response = member_client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"}
    )
    assert response.status_code == 403


def test_concurrent_patches_do_not_break_the_invariant(factories, db_engine):
    """Две одновременные правки: одна объявляет базу, другая её снимает при
    сохранённой цели. Итоговое состояние обязано остаться законным."""
    # форма теста — как у tests/integration/test_category_override_concurrency.py
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_estimate_vat_api.py -v`
Expected: FAIL, 404

- [ ] **Шаг 3: написать роутер**

```python
# backend/routers/estimate_vat.py
"""Роутер правки ставок НДС сметы (спека пересчёта §2.7).

Право — `admin`: правка меняет все деньги договора сразу, включая выгрузку для
банка. Это тот же вес, что у замены смет и нормативов (`AGENTS.md` §3), и
именно поэтому здесь `require_admin`, в отличие от разноса статей.

Транзакцию ведёт роутер, сервис только пишет — та же раскладка, что у разноса.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
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


class VatRatesRequest(BaseModel):
    """Отсутствующее поле — «не менять», `null` — «снять».

    Различить их можно только через `model_fields_set`: Pydantic кладёт `None` и
    в то, и в другое. Значения приходят СТРОКАМИ и конвертируются в `Decimal` —
    `float` в деньгах и ставках запрещён (`AGENTS.md` §3).
    """

    base_override: Decimal | None = Field(default=None, ge=0, le=100)
    target: Decimal | None = Field(default=None, ge=0, le=100)


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

- [ ] **Шаг 4: прогнать тесты**

Run: `cd backend && uv run pytest tests/integration/test_estimate_vat_api.py -v`
Expected: PASS, 9 тестов

- [ ] **Шаг 5: коммит**

```bash
git add backend/routers/estimate_vat.py backend/main.py backend/tests/integration/test_estimate_vat_api.py
git commit -m "feat(api): PATCH ставок НДС сметы под admin"
```

---

## Задача 10: целевая ставка на одно-договорных поверхностях

**Files:**
- Modify: `backend/crud/project_passport.py` (суммы дерева, кольцо, `per_sqm`)
- Modify: `backend/crud/analytics.py` (паспорт фазы 6: факт и норматив в цели)
- Modify: `backend/crud/reports.py` (свод по договору «а»: факт и норматив в цели)
- Test: `backend/tests/integration/test_project_passport_api.py`, `test_reports_api.py`

**Interfaces:**
- Consumes: `restate_gross`, `serialize_amount`, `net_to_gross`, `AmountStatus` из `money.vat`.
- Produces: суммы паспорта и свода в целевой ставке; норматив — тоже.

- [ ] **Шаг 1: написать падающий тест на обе стороны**

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


def test_contract_summary_shows_both_sides_in_target(client, factories, db_session):
    """Норматив приводится к той же ставке, что и факт: строка согласована."""
    contract = factories.contract_with_standard(
        unit_cost_total=Decimal("120"), vat_rate=Decimal("20"), standard=Decimal("100")
    )
    factories.set_vat_target(contract, Decimal("16"))
    db_session.commit()
    row = client.get(f"/api/v1/reports/contract-summary/{contract.id}").json()["rows"][0]
    assert Decimal(row["unit_cost_total"]) == Decimal("116.00")
    assert Decimal(row["standard_unit_rate"]) == Decimal("116.00")
    assert Decimal(row["deviation_pct"]) == Decimal("0")
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_project_passport_api.py -k target -v`
Expected: FAIL — `amount` равен `120`

- [ ] **Шаг 3: провести цель в суммы паспорта**

```python
# backend/crud/project_passport.py — суммы приходят из VIEW в разрезе предложения
def _restated_branch_amount(rows, target) -> Decimal | None:
    """Сумма ветки в ставке показа. Группы приходят по предложению, у каждой
    своя база, поэтому пересчитывается КАЖДАЯ и лишь потом складывается."""
    total = None
    for row in rows:
        restated = restate_gross(row.amount, row.vat_rate_base, target)
        value = serialize_amount(restated)
        if value is None:
            continue
        total = value if total is None else total + value
    return total
```

- [ ] **Шаг 4: привести норматив к той же ставке**

```python
# backend/crud/reports.py и backend/crud/analytics.py — одно-договорные поверхности
def _standard_in_target(standard: Decimal | None, target: Decimal | None) -> Decimal | None:
    """Норматив — цена без НДС; на одно-договорной поверхности он показывается в
    той же ставке, что и факт. Отклонение от этого не меняется: приведение обеих
    сторон к одной ставке отношения не меняет."""
    if standard is None:
        return None
    if target is None:
        return standard
    return money_round(net_to_gross(standard, target), 2)
```

- [ ] **Шаг 5: подписать ставку на экране и на листе**

Паспорт: строка шапки «суммы показаны с НДС 16 %» либо «без НДС», если цель `0`.
Свод по договору: та же подпись под заголовком листа.

- [ ] **Шаг 6: прогнать тесты**

Run: `cd backend && uv run pytest tests/integration -q`
Expected: PASS

- [ ] **Шаг 7: коммит**

```bash
git add backend/crud backend/tests
git commit -m "feat(passport): суммы и норматив в целевой ставке на одном договоре"
```

---

## Задача 11: фронт — типы, клиент, хук

**Files:**
- Modify: `frontend/src/types/domain.ts:496-507`
- Modify: `frontend/src/services/api/domain.ts`
- Modify: `frontend/src/services/queries.ts`
- Test: `frontend/src/services/queries.test.tsx`

**Interfaces:**
- Produces: `useSetEstimateVat()` — мутация, инвалидирующая `qk.passport.project(contractId)`.

- [ ] **Шаг 1: написать падающий тест инвалидации**

```tsx
// frontend/src/services/queries.test.tsx
it("useSetEstimateVat инвалидирует паспорт этого договора и не трогает чужой", async () => {
  const passportKey = qk.passport.project(5);
  const otherKey = qk.passport.project(99);
  // ... форма теста — как у существующего теста инвалидации паспорта
});
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd frontend && npx vitest run src/services/queries.test.tsx`
Expected: FAIL, `useSetEstimateVat is not a function`

- [ ] **Шаг 3: дописать типы**

```ts
// frontend/src/types/domain.ts, ProjectPassportEstimate — добавить три поля
  /** База, назначенная человеком; `null` — база берётся из файла. */
  vat_rate_base_override: Decimal | null;
  /** Ставка показа; `null` — показываем в базовой. */
  vat_rate_target: Decimal | null;
  /** Когда правили ставки; `null` — поправок нет. */
  vat_rate_updated_at: string | null;
```

```ts
// frontend/src/types/domain.ts — вердикт сверки нетто
export type NetReconciliationStatus = "ok" | "mismatch" | "unknown_base" | "not_applicable";

export interface NetReconciliation {
  status: NetReconciliationStatus;
  delta: Decimal | null;
  mismatched_proposal_ids: number[];
}
```

- [ ] **Шаг 4: дописать клиент и хук**

```ts
// frontend/src/services/api/domain.ts
  setVat: (
    estimateId: ID,
    input: { base_override?: Decimal | null; target?: Decimal | null },
  ): Promise<EstimateVatState> =>
    api.patch<EstimateVatState>(`/v1/estimates/${estimateId}/vat`, input).then((r) => r.data),
```

```ts
// frontend/src/services/queries.ts
export function useSetEstimateVat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ estimateId, contractId: _contractId, input }: SetEstimateVatInput) =>
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
Expected: PASS, `tsc` чист. Гонять **только из `frontend/`**: в корне лежит другой
vitest, он не разрешает алиас `@/` и падает на сборке.

- [ ] **Шаг 6: коммит**

```bash
git add frontend/src/types frontend/src/services
git commit -m "feat(frontend): клиент и хук правки ставки НДС"
```

---

## Задача 12: фронт — форма, три бейджа, печатная сноска

**Files:**
- Create: `frontend/src/pages/passport/VatRateDialog.tsx`
- Modify: `frontend/src/pages/passport/PassportHeader.tsx:94-195`
- Test: `frontend/src/pages/passport/ProjectPassportPage.test.tsx`

**Interfaces:**
- Consumes: `useSetEstimateVat` из задачи 11.

- [ ] **Шаг 1: написать падающий тест трёх состояний**

```tsx
it("без базы предлагает объявить ставку файла и не даёт задать показ", () => {
  render(<PassportHeader passport={passportWithoutVatRate} />);
  expect(screen.getByText(/ставка НДС не заявлена в файле/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /пересчитать/i })).not.toBeInTheDocument();
});

it("показывает назначенную вручную базу рядом с заявленной файлом", () => {
  render(<PassportHeader passport={passportWithBaseOverride} />);
  expect(screen.getByText(/база НДС 12 назначена вручную/)).toBeInTheDocument();
  expect(screen.getByText(/файл заявил 20/)).toBeInTheDocument();
});

it("печатная сноска о поправке присутствует всегда, когда поправка есть", () => {
  render(<PassportHeader passport={passportWithTarget} />);
  const note = screen.getByTestId("vat-print-note");
  expect(note).toHaveTextContent(/показано в ставке 16, пересчитано с 20/);
  expect(note).not.toHaveAttribute("data-print", "hide");
});
```

- [ ] **Шаг 2: прогнать и убедиться, что падает**

Run: `cd frontend && npx vitest run src/pages/passport/ProjectPassportPage.test.tsx`
Expected: FAIL — таких узлов нет

- [ ] **Шаг 3: реализовать бейджи и сноску**

Три состояния из спеки §2.8, и они не сливаются в один бейдж:

```tsx
// frontend/src/pages/passport/PassportHeader.tsx
function vatSummary(estimate: ProjectPassportEstimate): string {
  const declared = estimate.vat_rate;
  const base = estimate.vat_rate_base_override ?? declared;
  const target = estimate.vat_rate_target;

  if (base === null) return "ставка НДС не заявлена в файле";
  if (estimate.vat_rate_base_override !== null) {
    const suffix =
      declared === null ? "" : ` (файл заявил ${formatPercentDecimal(declared)})`;
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

- [ ] **Шаг 4: прогнать тесты и типы**

```bash
cd frontend && npx vitest run && npx tsc -b --noEmit
```
Expected: PASS

- [ ] **Шаг 5: коммит**

```bash
git add frontend/src/pages/passport
git commit -m "feat(frontend): форма ставки НДС, три бейджа и печатная сноска"
```

---

## Задача 13: замер печатной раскладки А4 в браузере

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

## Задача 14: негативные проверки снятием защиты

Тринадцать снятий спеки §4.5. **Делает оркестратор лично**, по протоколу
[verifying-guards.md](../../insights/verifying-guards.md) целиком.

**Files:**
- Modify: `docs/devlog/2026-08-13-vat-rate-recalculation.md`

- [ ] **Шаг 1: контрольный прогон до снятия**

```bash
cd backend && uv run pytest -q | tail -3
```
Записать число прошедших. Без этого числа красный прогон не с чем сравнить.

- [ ] **Шаг 2: снять защиту, сверить, что снятие применилось**

Для каждой из тринадцати: сделать побайтовую копию файла, применить снятие,
`assert old in text` до правки, напечатать sha256 до и после. Восстанавливать —
**из копии**, не `git checkout --`: он даёт CRLF и ложную тревогу.

Перечень снятий — спека §4.5, пункты 1–13.

- [ ] **Шаг 3: по каждому снятию записать, что покраснело**

Снятие, не уронившее ничего, означает **«защиты нет»**, а не «тест плох».

- [ ] **Шаг 4: назвать соседний слой защиты**

По каждой проверке спросить, что ещё стоит на пути дефекта. Кандидаты в пару
названы спекой заранее: 2 и 3 стерегут один инвариант с разных сторон, 1 и 7 обе
защищают числа Ф6. Если дефект не воспроизводится одним снятием — это
записывается замером, а не объявляется «дефект воспроизведён».

- [ ] **Шаг 5: коммит**

```bash
git add docs/devlog/2026-08-13-vat-rate-recalculation.md
git commit -m "docs(devlog): тринадцать снятий защиты, ни одной мёртвой"
```

---

## Задача 15: ревизия `AGENTS.md`, рамка фазы, devlog, стенд

**Files:**
- Modify: `AGENTS.md` (§4 семантика отклонений, §7.6 макет отчёта, §10 DoD)
- Modify: `docs/phase7-frame.md` (строка о фиче)
- Modify: `docs/devlog/2026-08-13-vat-rate-recalculation.md`

- [ ] **Шаг 1: ревизия §4**

Заменить формулу отклонения на нетто-версию, объявить `standard_unit_rate` ценой
**без НДС**, добавить вторую причину пустого отклонения. Врезкой — причина ревизии
и ссылка на спеку.

- [ ] **Шаг 2: ревизия §7.6**

Разбиение позиций отчёта «для банка» — четыре класса с приоритетом; норматив и
отклонение в деньгах — нетто; подпись ставки в шапке листа.

- [ ] **Шаг 3: соответствие «требование → тест»**

Построить список: поведенческое требование §2 спеки → тест, который его исполняет.
Требование без исполнителя либо получает тест, либо **объявляется границей** в
devlog. Молчаливого третьего варианта нет.

- [ ] **Шаг 4: перевести стенд**

```bash
just db-dev-init
```
Затем удалить заведённые нормативы и завести заново в семантике нетто. Сметы
**не** перезаливать: контракт парсера не менялся. Сверить счётчики договоров,
объектов, подрядчиков, смет и каталога (1932) до и после.

- [ ] **Шаг 5: прогнать `just ci` целиком**

```bash
just ci
echo "EXIT=$?"
```
Expected: `EXIT=0`. Читать код возврата, а не хвост вывода: конвейер возвращает код
последней команды, и падение приходит как `exit 0`.

- [ ] **Шаг 6: коммит и PR**

```bash
git add AGENTS.md docs/
git commit -m "docs: ревизия AGENTS.md §4 и §7.6, devlog фичи"
git push -u origin feat/vat-rate-recalculation
```

PR со ссылками на рамку фазы, спеку и этот план.

---

## Самопроверка плана

**Покрытие спеки.** §2.1 → задача 3; §2.2 → задачи 1, 2; §2.3 → задачи 0 (замер В),
1 (`serialize_amount`); §2.4 → задачи 1, 10; §2.5 → задачи 4, 5, 6, 10, 15;
§2.6 → задачи 3, 4, 5; §2.7 → задачи 8, 9; §2.8 → задача 12; §2.9 → задача 6;
§2.10 → задачи 2, 7; §2.11 → задача 8 (каскад проверяется существующим тестом
`replace`); §2.12 → задача 15. §4.5 → задача 14. §6 DoD → задачи 13, 14, 15.

**Известное упрощение, названное вслух:** фабрики тестов (`factories.priced_estimate`,
`factories.contract_with_standard`, `factories.estimate_with_proposals`,
`factories.set_vat_target`, `factories.bank_report_fixture`) в
`backend/tests/factories.py` частью ещё не существуют. Каждая заводится в той
задаче, где впервые вызвана, тем же коммитом — отдельной задачи под них нет
намеренно: фабрика без потребителя не проверяется ничем.

**Согласованность имён.** `DEVIATION_INPUTS` заводится в задаче 4 и там же на
переходный период получает алиас `DEVIATIONS`; алиас снимается шагом 6 задачи 7,
после чего весь backend прогоняется целиком. `gross_to_net`/`net_to_gross`/
`vat_from_net`/`restate_gross`/`serialize_amount` объявлены в задаче 1 и дальше
употребляются под теми же именами; `check_proposal_net`/`fold_net_reconciliation`/
`NetStatus` — в задаче 2.
