# Объём и ставка в таблице статей паспорта — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Таблица статей паспорта объекта показывает объём статьи, её единицу и
ставку ₽ за единицу — со строки сметы, несущей код статьи, в ставке показа
договора, с явным состоянием пустой клетки и объявленным охватом.

**Architecture:** Чистый модуль правила (`services/article_rates.py`) — автомат
состояний, свёртка строк-носителей и проверка сходимости, без `Session` и SQL,
по образцу `services/category_rollup.py`. `crud/project_passport.py` добавляет
ОДИН запрос строк-разделов сметы, зовёт правило и приклеивает пять полей к
каждому узлу свода плюс `rate_coverage` на верхний уровень — тем же приёмом,
которым уже приклеены `extras` и `own_sections` (мимо `v_category_totals`,
намеренно). Фронт добавляет три колонки и строку охвата под таблицей.

**Tech Stack:** Python 3.12 / SQLAlchemy 2 / FastAPI / pytest; React 19 /
TypeScript / TanStack Query / shadcn-ui / vitest + msw. Postgres 16 на порту
5459 (`just pg-test-start`).

**Spec:** [`docs/superpowers/specs/2026-08-24-passport-volumes-design.md`](../specs/2026-08-24-passport-volumes-design.md)
(редакция 2, гейт 2 закрыт 24.08.2026). Источник формы —
[`docs/superpowers/specs/2026-08-23-passport-volumes-mockup.html`](../specs/2026-08-23-passport-volumes-mockup.html).
План на спеку **ссылается** и её не пересказывает: при расхождении побеждает
спека (`AGENTS.md` §9.2).

**Ветка:** `feat/passport-volumes` — **уже существует**, три коммита поверх
`main` (`9963c13`), дерево чистое. Новой ветки не заводить; PR один, со ссылками
на спеку и на этот план (`AGENTS.md` §9.3).

---

## Global Constraints

Требования спеки, действующие в КАЖДОЙ задаче. Значения скопированы дословно.

1. **Маршрут действующий:** `GET /api/v1/analytics/project-passport/{contract_id}`.
   Новых маршрутов фича не заводит (§2.10, DoD 27).
2. **Миграции у фичи нет.** `v_category_totals` не меняется, схема не меняется,
   `alembic check` обязан быть чист без новой ревизии (§2.10, DoD 28).
3. **Носитель объёма** — строка сметы `position_items.is_chapter = true AND
   position_items.smr_article_raw IS NOT NULL`. Сумма нижних позиций объёмом не
   является никогда (§2.2, §3.1, §3.4).
4. **Объём строки** — `COALESCE(position_items.suggested_quantity,
   position_items.quantity)` (§6).
5. **Сумма строки** — `position_items.total_cost_total` (§2.2). Это НЕ
   `node.total` и НЕ `node.own`: те приходят из `v_category_totals`, то есть с
   позиций (`is_chapter = false`), а не со строки-носителя. Величина нужна ровно
   для ЧИСЛИТЕЛЯ СТАВКИ и больше нигде: числитель `money_share` — наоборот
   `node.total` (задача 5), потому что §2.8 говорит «сумма денег СТАТЕЙ», а
   деньги статьи в этой таблице — её колонка «Итого, ₽». Смешивать две величины
   в одной дроби нельзя: получилось бы отношение двух разных путей к деньгам
   одной сметы.
6. **Ось показа** — `money.vat.effective_display_rate(target, base_override,
   [declared])`; приведение построчным `money.vat.restate_gross` делается **ДО**
   деления на объём. Объём от ставки НДС не зависит (§2.2, §2.7).
7. **Немасштабируемы ровно две единицы** — `SET` («Комплект») и `MON` («Месяц»).
   Остальные семь (`TON`, `KG`, `M3`, `L`, `M2`, `M`, `PCS`) масштабируемы,
   включая «Штуку». Знака «%» в справочнике нет — в правиле не упоминать (§2.5,
   §2.6).
8. **Допуск сходимости** — `ε = 0,01 × n`, где `n` — число слагаемых суммы
   объёмов детей (§2.4).
9. **Состояний десять** (девять + `rate`), проверки строго сверху вниз, **первое
   совпадение выигрывает**, у каждого узла свода РОВНО ОДНО состояние (§2.5).
   `rate_note` ∈ {`overshoot`, `mixed_units`, `unverifiable`, `null`} — третье
   значение добавляется решением У3 варианта «б» и требует правки §2.10 спеки.
10. **Только исходная смета** — `amendment_no IS NULL`, через существующий
    `_source_estimate` (§4).
11. **Не меняются:** страница сравнения, сквозная матрица, три отчёта Excel
    (§2.1). Нормативов фича не заводит (§2.7, §3.3).
12. **Конфиденциальность** (рамка фазы 7, уточнена 24.08.2026). Нельзя:
    абсолютные суммы, названия и идентификаторы объектов и корпусов, реквизиты
    контрагентов — ни в коде, ни в тестах, ни в доках, ни в сообщениях коммитов.
    Можно: удельные величины (₽/ед., ₽/м²), объёмы, доли, проценты, коды и
    названия статей классификатора. Объекты в фикстурах — «Объект А…Ж».
13. **Фронтенд — только примитивы shadcn/ui** (`@/components/ui/*`) и токены
    темы; кастомных компонентов и литеральных цветов не заводить.
14. **`just ci` зелёный перед пушем** (`AGENTS.md` §9.3); существующие тесты не
    ослаблять (DoD 31).
15. **Деньги — десятичные строки** (`AGENTS.md` §3). На клиенте `Number()` над
    деньгами и объёмами запрещён.
16. **Вся арифметика фичи — в ЯВНОМ десятичном контексте**, а не в наследуемом.
    Контекст строится той же формой, что `_VAT_CONTEXT` в `money/vat.py`:
    `Context(prec=ARITHMETIC_PRECISION, traps=[Overflow, DivisionByZero,
    InvalidOperation])`, где `ARITHMETIC_PRECISION = 100`
    (`backend/parser/summary_block.py:154`). Опора на ambient-контекст (`prec =
    28` по умолчанию) запрещена по двум причинам, обе уже зафиксированы
    докстрокой `money/vat.py`: `localcontext()` без аргумента копирует контекст
    ВМЕСТЕ С ЕГО ТРАПАМИ (включённый где-то `Inexact` превратил бы штатное
    сложение в исключение), а `prec = 28` делает сложение неассоциативным при
    достаточно длинных слагаемых — то есть **зависимым от порядка строк**.
17. **Ставка ответа квантуется ВСЕГДА** — `quantize_money` до копеек, без
    оглядки на `restated_any`. Это новое поле, обязательства «посимвольно как до
    фичи» (`AGENTS.md` §10) на нём нет, а частное почти никогда не представимо
    конечной дробью: без квантования при `prec = 100` в ответ уехала бы
    100-значная строка. `volume` НЕ квантуется — это число из файла, а не
    вычисленная величина.

---

## Решения по контракту — приняты на гейте 3 и внесены в спеку

Три места, где первая редакция спеки расходилась с макетом либо умалчивала.
Все три решены 24.08.2026 и **внесены в спеку** — план им только следует; если
что-то из перечисленного придётся менять, правится спека, а не этот план.

| | Решение | Где в спеке | Что было |
|---|---|---|---|
| **У1** | Колонок **три** — `Ед.`, `Объём`, `Ставка, ₽/ед.`; таблица становится восьмиколоночной | §2.1 (ревизия), §2.9 | «две колонки», единица с объёмом считались за одну величину |
| **У2** | `unit` отдаёт **символ** (`units_of_measure.symbol`): «м²», «м³», «шт» | §2.10 (ревизия) | «каноническое имя единицы» — то есть `name` («Кв. метр») |
| **У3** | `rate_note` получает **третье** значение `unverifiable`, подпись «сходимость не проверить» | §2.10 (ревизия) | два значения; ребёнок с непрочитанной единицей получал бы `mixed_units` |

Обоснования не дублирую — они в спеке, у каждой ревизии свой блок. Здесь только
то, что из решений следует для кода:

- **У1** — восемь `th` и восемь `td` в каждой строке таблицы; задачи 6 и 8
  считают колонки числом `8`, и печатный замер задачи 8 идёт по восьми.
- **У2** — свёртка несёт `unit_symbol` рядом с `unit_code` (задача 1), запрос
  задачи 3 тянет `units_of_measure.symbol`, шестого поля в ответе НЕ появляется.
- **У3** — `RateNote` из трёх членов, `convergence_note` из трёх проверок
  (задача 2), третья пилюля на фронте (задача 6), снятие защиты на оговорку
  `is not None` (задача 2, шаг 7а).

Ещё два решения того же гейта касаются охвата и внесены в спеку §2.8 вместе с
DoD 32 и 33: `money_share = NULL` при выходе доли из диапазона 0..100
(антицепь не держит границу при отрицательных суммах — `CHECK`-а на знак в схеме
нет) и «частичный итог статьи K не гасит». Подробности — в задаче 5.


## Проверенные символы

Проверено `grep`-ом по дереву 24.08.2026 (`AGENTS.md` §9.1). **Существующее —
брать как есть; всё, что помечено «заводится», в дереве отсутствует и создаётся
названной задачей.**

| Символ | Где | Статус |
|---|---|---|
| `_finite_amount`, `_source_estimate`, `_extras_by_category`, `_own_sections_by_category`, `_own_sections_select`, `_direct_totals`, `_sum_known`, `_share_pct`, `_per_sqm`, `_flatten_categories`, `_quantize_if_restated`, `_category_dict`, `_vat_rate`, `_section_metrics`, `get_project_passport`, `CATEGORY_TOTALS` | `backend/crud/project_passport.py` | существует |
| `effective_display_rate`, `restate_gross`, `RestatedAmount`, `AmountStatus`, `quantize_money` | `backend/money/vat.py` | существует |
| `CategoryRef`, `CategoryNode`, `DirectTotals`, `build_tree`, `SOURCE_POSITIONS`, `SOURCE_ADDITIONAL_WORKS` | `backend/services/category_rollup.py` | существует |
| `UNITS_SEED` (девять единиц, коды `TON KG M3 L M2 M PCS SET MON`) | `backend/crud/units.py` | существует |
| `PositionItem.{is_chapter, smr_article_raw, work_category_id, category_source, chapter_item_id, unit_id, quantity, suggested_quantity, total_cost_total, proposal_id, position_key_in_proposal}`, `UnitOfMeasure.{code, name, symbol}`, `Proposal.vat_rate`, `Estimate.{vat_rate_base_override, vat_rate_target, amendment_no}`, `Lot.estimate_id` | `backend/models.py` | существует |
| `_proposal`, `_chapter`, `_position`, `_additional_work`, `_category` | `backend/tests/integration/test_project_passport_api.py` (**локальные для файла**) | существует; **новый набор заводит свои копии** — файлы тестов помощников друг у друга не импортируют (докстрока того файла) |
| `_unit_id(db_session, code)` | `backend/tests/integration/test_review_api.py:67` | существует как ОБРАЗЕЦ; в новом наборе заводится своя копия |
| `PositionItemFactory` (дефолты: `is_chapter=False`, `quantity=1`, `suggested_quantity=10`, `total_cost_total=1000.00`, `unit_id` НЕ задан) | `backend/tests/factories.py` | существует; **не править** |
| `ProjectPassportCategory`, `ProjectPassportTotals`, `ProjectPassport`, `ProjectPassportSection`, `ProjectPassportExtra` | `frontend/src/types/domain.ts` | существует |
| `MoneyCell` (`value`, `currency`, `maxFractionDigits`), `formatDecimalMoney`, `formatSharePercent`, `roundDecimal` | `frontend/src/components/ui-domain/MoneyCell.tsx`, `frontend/src/lib/format.ts` | существует |
| `Badge`, `Table*`, `Switch`, `Label` | `frontend/src/components/ui/*` | существует |
| `sampleProjectPassport` (12 узлов в `categories`), `withPassport`, `renderPassport`, `PROJECT_PASSPORT_URL` | `frontend/src/test/fixtures.ts`, `frontend/src/pages/passport/ProjectPassportPage.test.tsx` | существует |
| `[data-print="sheet"] th:nth-child(1..5)` — печатные ширины ПЯТИ колонок | `frontend/src/index.css:487-491` | существует; правится задачей 8 |
| `services/article_rates.py` и всё в нём | — | **заводится задачами 1–2** |
| `_carrier_rows_select`, `_carrier_rows_by_category`, `_article_rates` | `backend/crud/project_passport.py` | **заводятся задачами 3–5** |
| `backend/tests/unit/test_article_rates.py`, `backend/tests/integration/test_passport_article_rates.py`, `frontend/src/pages/passport/PassportRates.test.tsx` | — | **заводятся задачами 1, 1, 6** |
| `data-print="drop"` | `frontend/src/index.css` | **заводится задачей 8, если замер покажет нехватку ширины** |

**Замер стенда, сделанный при планировании** (`gca_dev`, порт 5459, 24.08.2026,
`amendment_no IS NULL`): строк-носителей 883; у **883 из 883** есть
`total_cost_total`, у 883 из 883 — `work_category_id`, у 661 — `unit_id`, у 354 —
`COALESCE(suggested_quantity, quantity)`. Следствия для плана: состояния
`amount_missing` и «носитель без разрешённой статьи» на стенде **не наблюдаются**
— они строятся только фикстурами; `unit_missing` и `volume_missing` наблюдаются
массово.

---

## Структура файлов

**Создаётся**

| Файл | Ответственность |
|---|---|
| `backend/services/article_rates.py` | Чистое правило ставки: классификация единиц (§2.6), свёртка строк-носителей одной статьи (§2.2, §2.5 состояния 3–5), проверка сходимости (§2.4), автомат состояний с приоритетом (§2.5). Ни `Session`, ни ORM, ни SQL, ни импортов из `crud` — как `services/category_rollup.py`. |
| `backend/tests/unit/test_article_rates.py` | Всё, что проверяется без БД: свёртка, автомат, приоритет, сходимость, допуск, ось показа на уровне свёртки. |
| `backend/tests/integration/test_passport_article_rates.py` | Всё, что требует БД: выбор носителей запросом, вложенность, полнота классификации по таблице `units_of_measure`, поля ответа, охват, ось показа сквозь весь путь. |
| `frontend/src/pages/passport/PassportRates.test.tsx` | Три колонки, пилюли состояний, строка охвата. Отдельный файл: `ProjectPassportPage.test.tsx` — 1847 строк, и его describe-блоки принадлежат прежним задачам. |
| `docs/devlog/2026-08-24-passport-volumes.md` | Замер печати (задача 8) и прогон на стенде (задача 9). |

**Правится**

| Файл | Что именно |
|---|---|
| `backend/crud/project_passport.py` | `_carrier_rows_select` / `_carrier_rows_by_category` / `_article_rates` (новые); `_category_dict` — пять полей; `get_project_passport` — вызов и `rate_coverage` на ОБОИХ путях функции. |
| `frontend/src/types/domain.ts` | `ProjectPassportCategory` — пять полей; `RateState`, `RateNote`, `ProjectPassportRateCoverage` (новые); `ProjectPassport.rate_coverage`. |
| `frontend/src/test/fixtures.ts` | Пять полей на каждый из 12 узлов `sampleProjectPassport.categories` + `rate_coverage`. |
| `frontend/src/pages/passport/ProjectPassportPage.test.tsx` | Единственный полный литерал категории (около строки 1589) — дополнить полями, иначе `tsc` красный. Логику существующих тестов не менять. |
| `frontend/src/test/handlers.ts` | Проверить, что варианты `projectPassportForOutcome` строятся через `base.categories.map(...)` (так и есть на 202/251/268/284) — тогда правки не нужно; если найдётся полный литерал, дополнить. |
| `frontend/src/pages/passport/CategoryTable.tsx` | Три `TableHead`; три `TableCell` в строке статьи, в строке допработ, в служебной строке, в строке «Нераспределённое» и в строке итога; `colSpan={5}` → `colSpan={8}` у строки панели разноса; строка охвата под таблицей. |
| `frontend/src/index.css` | Печатные ширины: пять `nth-child` → восемь (задача 8). |
| `docs/phase7-frame.md` | «Вне скоупа v1» пункт 2 — текст правки дан в спеке §2.11 дословно. |
| `AGENTS.md` | §7 пункт 4 (он же «§7.4») — состав паспорта, строка охвата, оговорка про ось показа и про деление на базу НДС при переносе в норматив (§2.12). |

**Не трогать:** `backend/alembic/**`, `backend/services/category_rollup.py`,
`backend/crud/comparison.py`, `backend/crud/reports.py`,
`backend/services/excel*.py`, `backend/tests/factories.py`,
`frontend/src/pages/compare/**`, `frontend/src/pages/matrix/**`.

---

## Task 1: Классификация единиц и свёртка строк-носителей

**Files:**
- Create: `backend/services/article_rates.py`
- Create: `backend/tests/unit/test_article_rates.py`
- Create: `backend/tests/integration/test_passport_article_rates.py`
- Test: те же два файла тестов

**Interfaces:**
- Consumes: `money.vat.restate_gross`, `money.vat.AmountStatus` (существуют);
  `crud.units.UNITS_SEED` — только в тесте, не в модуле правила.
- Produces (всё **заводится этой задачей**):
  - `NON_SCALABLE_UNIT_CODES: frozenset[str] = frozenset({"SET", "MON"})`
  - `SCALABLE_UNIT_CODES: frozenset[str] = frozenset({"TON","KG","M3","L","M2","M","PCS"})`
  - `def is_scalable_unit(code: str | None) -> bool`
  - `@dataclass(frozen=True) class CarrierRow: amount: Decimal | None;
    vat_rate_base: Decimal | None; unit_code: str | None; unit_symbol: str | None;
    volume: Decimal | None`
  - `@dataclass(frozen=True) class ArticleFold: rows: int; amount: Decimal | None;
    amount_ok: bool; unit_code: str | None; unit_symbol: str | None;
    unit_missing: bool; unit_conflict: bool; volume: Decimal | None;
    volume_missing: bool; volume_nonpositive: bool`
  - `def fold_carrier_rows(rows: Sequence[CarrierRow], effective_rate: Decimal | None) -> ArticleFold`

### Как снимать защиту (правило для всех снятий плана)

Снятие делается ПОСРЕДИ задачи: реализация уже в рабочем дереве, но ещё не
закоммичена. Значит возврат обязан быть ТОЧНОЙ обратной правкой того же места — и
ничем больше.

- **Нельзя** `git checkout -- <файл>` и нельзя `git diff <файл> > p; git apply -R
  p`. Оба работают со всем набором несохранённых правок файла, а не с мутацией:
  в снятиях, которые правят `article_rates.py` — тот файл, который задача и
  пишет, — возврат снёс бы вместе с мутацией всю реализацию.
- **Нельзя** возвращать копией файла, снятой «на всякий случай». Копия фиксирует
  МОМЕНТ, а не правку: она вернёт и то, что было отредактировано между снимком и
  возвратом, а при фиксированном имени файла два снятия в одной задаче перетрут
  друг другу точку возврата.
- **Надо** внести мутацию как ОДНУ именованную правку и вернуть её обратной
  правкой того же места. Каждое снятие ниже описано так, что обратная правка
  читается из его же текста: «заменить A на B» → вернуть B на A. Чем правка
  вносится — `apply_patch`, `Edit`, `sed` — плану безразлично: требование
  предъявляется к результату, а не к инструменту. Результат: файл побайтово тот
  же, что до снятия, и ни один другой файл не тронут.
- **Проверять возврат прогоном**, а не глазами: после возврата тот же тест обязан
  стать зелёным. Непрошедший возврат иначе уедет в коммит вместе с мутацией — то
  же правило, что и для самого снятия, механизм проверяется поведением.

Слово «вернуть» в шагах ниже означает именно это.

**Правила свёртки** (§2.2, §2.5 состояния 3–5; негодное слагаемое гасит статью
целиком):

- **Вся арифметика — внутри `localcontext(_RATE_CONTEXT)`** (Global Constraint
  16). Один контекст на модуль, объявленный явно; накопление суммы и объёма
  идёт под ним. При `prec = 100` слагаемые файлового масштаба не округляются
  вовсе, поэтому свёртка точна, а значит от порядка строк не зависит — и это
  СЛЕДСТВИЕ контекста, а не свойство `Decimal` само по себе.
- `rows` — сколько строк-носителей вошло; `0` означает «носителя нет».
- `amount`: для каждой строки `restate_gross(row.amount, row.vat_rate_base,
  effective_rate)`; сумма только приведённых значений. `amount_ok = False`, если
  хотя бы у одной строки `amount is None` **либо** статус результата
  `AmountStatus.NOT_FINITE`; тогда `amount = None`.
- `unit_missing = True`, если хотя бы у одной строки `unit_code is None`.
- `unit_conflict = True`, если среди строк встретилось больше одного различного
  непустого `unit_code`.
- `unit_code`/`unit_symbol` заполняются ТОЛЬКО когда все строки согласны на
  одной непустой единице; иначе `None`.
- `volume`: сумма `row.volume`. `volume_missing = True`, если хотя бы у одной
  строки `volume is None`; `volume_nonpositive = True`, если хотя бы у одной
  `volume <= 0` либо `not volume.is_finite()`. При любом из двух `volume = None`.
- `is_scalable_unit` реализуется **исключением**: `code is not None and code not
  in NON_SCALABLE_UNIT_CODES`. Полноту классификации сторожит тест, а не
  перечисление в коде (§2.5).

- [ ] **Step 1: Написать падающие юнит-тесты свёртки**

`backend/tests/unit/test_article_rates.py` — новый файл. Код готовый: все имена
слева от точки заводятся этой же задачей, справа — существуют.

```python
"""Правило ставки статьи: классификация единиц и свёртка строк-носителей
(спека 2026-08-24-passport-volumes-design.md §2.2, §2.5, §2.6; план, задача 1)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from crud.units import UNITS_SEED
from services.article_rates import (
    NON_SCALABLE_UNIT_CODES,
    SCALABLE_UNIT_CODES,
    CarrierRow,
    fold_carrier_rows,
    is_scalable_unit,
)

pytestmark = pytest.mark.unit


def _row(**kwargs) -> CarrierRow:
    """Строка-носитель с годными значениями по умолчанию — тест меняет одно поле."""
    base = dict(
        amount=Decimal("1000.00"),
        vat_rate_base=Decimal("20"),
        unit_code="M2",
        unit_symbol="м²",
        volume=Decimal("10.00"),
    )
    base.update(kwargs)
    return CarrierRow(**base)


def test_three_rows_of_one_article_are_added_up():
    """§2.2: строк с одним кодом может быть несколько (корпуса) — складываются."""
    fold = fold_carrier_rows(
        [
            _row(amount=Decimal("100000.00"), volume=Decimal("1000.00")),
            _row(amount=Decimal("200000.00"), volume=Decimal("2000.00")),
            _row(amount=Decimal("300000.00"), volume=Decimal("3000.00")),
        ],
        effective_rate=Decimal("20"),
    )
    assert fold.rows == 3
    assert fold.amount == Decimal("600000.00")
    assert fold.volume == Decimal("6000.00")
    assert fold.amount_ok is True


def test_one_row_without_an_amount_kills_the_whole_article():
    """DoD 12. Обычный `SUM` пропустил бы `None` и выдал ставку по части корпусов."""
    fold = fold_carrier_rows(
        [_row(amount=Decimal("100000.00")), _row(amount=None)],
        effective_rate=Decimal("20"),
    )
    assert fold.amount_ok is False
    assert fold.amount is None


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_amount_kills_the_whole_article(bad):
    """§2.5 состояние 3: «сумма не прочитана» покрывает и не-конечное число."""
    fold = fold_carrier_rows(
        [_row(amount=Decimal("100000.00")), _row(amount=bad)],
        effective_rate=Decimal("20"),
    )
    assert fold.amount_ok is False


def test_one_row_without_a_volume_kills_the_whole_volume():
    """DoD 13. Та же причина, что у суммы: частичный объём даёт ставку по части корпусов."""
    fold = fold_carrier_rows(
        [_row(volume=Decimal("1000.00")), _row(volume=None)],
        effective_rate=Decimal("20"),
    )
    assert fold.volume_missing is True
    assert fold.volume is None


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-1")])
def test_nonpositive_volume_of_a_second_row_kills_the_volume(bad):
    """DoD 14: обе величины отдельными случаями, и вторая строка при этом годная."""
    fold = fold_carrier_rows(
        [_row(volume=Decimal("1000.00")), _row(volume=bad)],
        effective_rate=Decimal("20"),
    )
    assert fold.volume_nonpositive is True
    assert fold.volume is None


def test_missing_unit_is_not_the_same_as_conflicting_units():
    """DoD 8, DoD 9: за ними разные действия, поэтому это два разных признака."""
    missing = fold_carrier_rows([_row(unit_code=None, unit_symbol=None)], effective_rate=None)
    assert (missing.unit_missing, missing.unit_conflict) == (True, False)
    assert missing.unit_code is None

    conflict = fold_carrier_rows(
        [_row(unit_code="M2", unit_symbol="м²"), _row(unit_code="M3", unit_symbol="м³")],
        effective_rate=None,
    )
    assert (conflict.unit_missing, conflict.unit_conflict) == (False, True)
    assert conflict.unit_code is None


def test_amount_is_restated_row_by_row_before_any_division():
    """§2.2: приведение к ставке показа — ДО деления, по базе КАЖДОЙ строки.

    Две строки с разными базами (20 % и 10 %) и одна цель 22 %. Приведение уже
    накопленной суммы дало бы другое число — сумма смешивает базы.
    """
    fold = fold_carrier_rows(
        [
            _row(amount=Decimal("120.00"), vat_rate_base=Decimal("20")),
            _row(amount=Decimal("110.00"), vat_rate_base=Decimal("10")),
        ],
        effective_rate=Decimal("22"),
    )
    # 120/1.20*1.22 + 110/1.10*1.22 = 122 + 122 = 244
    assert fold.amount == Decimal("244")


def test_the_fold_does_not_depend_on_the_order_of_the_rows():
    """Global Constraint 16: свёртка идёт в ЯВНОМ контексте `prec = 100`, и
    независимость от порядка строк — СЛЕДСТВИЕ этого контекста, а не свойство
    `Decimal` само по себе.

    Числа нарочно предельные: `1E+27` и два раза `0.6`. При `prec = 28` порядок
    виден в результате — прямой ход округляет каждую добавку по отдельности и
    даёт `...002`, обратный сначала копит `1.2` и даёт `...001`. При `prec = 100`
    оба порядка дают `...001.2`. Порядок строк-носителей задаёт `ORDER BY`
    запроса задачи 3, то есть величина, зависящая от порядка, зависела бы от
    плана запроса.

    Ожидание записано ЛИТЕРАЛОМ намеренно. `Decimal("1E+27") + Decimal("1.2")`
    вычислялось бы здесь, в ambient-контексте теста (`prec = 28`), потеряло бы
    `0.2` и дало `...001` — то есть тест падал бы на ВЕРНОЙ реализации. Ровно та
    ловушка, про которую тест и написан: выражение в ожидании считается не в том
    контексте, что проверяемый код.
    """
    volumes = [Decimal("1E+27"), Decimal("0.6"), Decimal("0.6")]
    forward = fold_carrier_rows(
        [_row(volume=v) for v in volumes], effective_rate=None
    )
    backward = fold_carrier_rows(
        [_row(volume=v) for v in reversed(volumes)], effective_rate=None
    )
    assert forward.volume == backward.volume
    assert forward.volume == Decimal("1000000000000000000000000001.2")


def test_set_and_month_are_the_only_non_scalable_units():
    """§2.6: «Штука» масштабируема — замер 7 (у лифтов настоящее количество)."""
    assert is_scalable_unit("PCS") is True
    assert is_scalable_unit("SET") is False
    assert is_scalable_unit("MON") is False
    assert is_scalable_unit(None) is False


def test_the_two_unit_groups_are_disjoint_and_cover_the_seed():
    """DoD 11, часть без БД: неклассифицированных нет уже в исходнике справочника."""
    seeded = {unit["code"] for unit in UNITS_SEED}
    assert SCALABLE_UNIT_CODES & NON_SCALABLE_UNIT_CODES == set()
    assert SCALABLE_UNIT_CODES | NON_SCALABLE_UNIT_CODES == seeded
```

- [ ] **Step 2: Прогнать — должно падать на импорте**

Run: `cd backend && uv run pytest tests/unit/test_article_rates.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'services.article_rates'`.

- [ ] **Step 3: Написать модуль**

`backend/services/article_rates.py`. Докстрока модуля обязана назвать: (а) что
модуль чистый и почему (образец — `services/category_rollup.py`); (б) что
масштабируемость определяется исключением, а полноту классификации сторожит
тест, а не код (§2.5); (в) что негодное слагаемое гасит статью целиком, потому
что частичная ставка хуже пустой клетки (§2.5).

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from money.vat import AmountStatus, restate_gross
from parser.summary_block import ARITHMETIC_PRECISION

#: Контекст строится ЯВНО и не наследует глобальный — тот же довод и та же
#: форма, что у `_VAT_CONTEXT` в `money/vat.py`: `localcontext()` без аргумента
#: копирует контекст вместе с его трапами, а `prec = 28` по умолчанию делает
#: сложение неассоциативным на длинных слагаемых, то есть зависимым от порядка
#: строк запроса. При `prec = 100` слагаемые файлового масштаба не округляются,
#: и свёртка точна.
_RATE_CONTEXT = Context(
    prec=ARITHMETIC_PRECISION,
    traps=[Overflow, DivisionByZero, InvalidOperation],
)

NON_SCALABLE_UNIT_CODES: frozenset[str] = frozenset({"SET", "MON"})
SCALABLE_UNIT_CODES: frozenset[str] = frozenset(
    {"TON", "KG", "M3", "L", "M2", "M", "PCS"}
)


def is_scalable_unit(code: str | None) -> bool:
    return code is not None and code not in NON_SCALABLE_UNIT_CODES


@dataclass(frozen=True)
class CarrierRow:
    amount: Decimal | None
    vat_rate_base: Decimal | None
    unit_code: str | None
    unit_symbol: str | None
    volume: Decimal | None


@dataclass(frozen=True)
class ArticleFold:
    rows: int
    amount: Decimal | None
    amount_ok: bool
    unit_code: str | None
    unit_symbol: str | None
    unit_missing: bool
    unit_conflict: bool
    volume: Decimal | None
    volume_missing: bool
    volume_nonpositive: bool


def fold_carrier_rows(
    rows: Sequence[CarrierRow], effective_rate: Decimal | None
) -> ArticleFold:
    if not rows:
        return ArticleFold(
            rows=0, amount=None, amount_ok=False, unit_code=None, unit_symbol=None,
            unit_missing=False, unit_conflict=False, volume=None,
            volume_missing=False, volume_nonpositive=False,
        )

    # `restate_gross` держит свой контекст сам; здесь под явным контекстом идёт
    # НАКОПЛЕНИЕ — именно оно зависит от `prec` и от порядка строк.
    amount_ok = True
    with localcontext(_RATE_CONTEXT):
        amount = Decimal(0)
        for row in rows:
            restated = restate_gross(row.amount, row.vat_rate_base, effective_rate)
            if restated.amount is None or restated.status is AmountStatus.NOT_FINITE:
                amount_ok = False
                continue
            amount += restated.amount

    codes = {row.unit_code for row in rows}
    unit_missing = None in codes
    known = codes - {None}
    unit_conflict = len(known) > 1

    volume_missing = any(row.volume is None for row in rows)
    volume_nonpositive = any(
        row.volume is not None and (not row.volume.is_finite() or row.volume <= 0)
        for row in rows
    )
    volume = None
    if not volume_missing and not volume_nonpositive:
        with localcontext(_RATE_CONTEXT):
            volume = Decimal(0)
            for row in rows:
                volume += row.volume

    single = next(iter(known)) if len(known) == 1 and not unit_missing else None
    symbol = next((r.unit_symbol for r in rows if r.unit_code == single), None) if single else None

    return ArticleFold(
        rows=len(rows),
        amount=amount if amount_ok else None,
        amount_ok=amount_ok,
        unit_code=single,
        unit_symbol=symbol,
        unit_missing=unit_missing,
        unit_conflict=unit_conflict,
        volume=volume,
        volume_missing=volume_missing,
        volume_nonpositive=volume_nonpositive,
    )
```

- [ ] **Step 4: Прогнать — должно быть зелено**

Run: `cd backend && uv run pytest tests/unit/test_article_rates.py -v`
Expected: PASS, 13 тестов — 10 функций, из них две параметризованы (3 и 2
случая). Сверять число в итоговой строке `N passed`, а не «зелено вообще»
(`docs/insights/silent-test-runs.md`).

- [ ] **Step 5: Завести интеграционный набор с проверкой полноты по таблице**

`backend/tests/integration/test_passport_article_rates.py` — новый файл. Здесь
только ОДИН тест; остальные добавят задачи 3–5. Помощники (`_category`,
`_proposal`, `_chapter`, `_position`, `_additional_work`, `_unit_id`) заводятся
СВОИ, копией с `test_project_passport_api.py` и `test_review_api.py:67`: наборы
помощников друг у друга не импортируют (докстрока `test_project_passport_api.py`).

```python
def test_every_unit_of_the_reference_is_classified_for_scalability(db_session):
    """DoD 11: список неклассифицированных пуст — по ТАБЛИЦЕ, не по сиду.

    Новая единица в справочнике роняет этот тест, а не получает поведение по
    умолчанию: `is_scalable_unit` реализована исключением (§2.5), поэтому без
    этого теста «Литр-2» молча стал бы масштабируемым.
    """
    codes = set(db_session.execute(sa.select(UnitOfMeasure.code)).scalars().all())
    assert codes == SCALABLE_UNIT_CODES | NON_SCALABLE_UNIT_CODES
    assert len(codes) == 9
```

- [ ] **Step 6: Прогнать интеграционный тест**

Run: `cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_passport_article_rates.py -v`
Expected: PASS, 1 тест. Если кластер не поднят — сначала `just pg-test-start`
(команда может «висеть», кластер при этом поднимается; проверять
`netstat -an | grep 5459`).

- [ ] **Step 7: Снятие защиты — DoD 11**

Добавить в `UNITS_SEED` (`backend/crud/units.py`) десятую строку
`{"code": "L2", "name": "Литр-2", "symbol": "л2", "dimension": "volume",
"base_code": "M3", "multiplier": "0.001"}`, **не** трогая множества в
`article_rates.py`. Прогнать `tests/unit/test_article_rates.py::test_the_two_unit_groups_are_disjoint_and_cover_the_seed`.
Expected: FAIL. Записать текст падения в тело PR-описания (или в devlog задачи 9)
и вернуть файл по правилу «Как снимать защиту» — см. ниже.

Проверка теста таблицы (`test_every_unit_of_the_reference_is_classified_for_scalability`)
снятием через сид **не делается**: сид не создаёт строку в БД без миграции. Его
снятие — убрать из `SCALABLE_UNIT_CODES` код `"L"`; прогнать тест, ждать FAIL,
откатить.

- [ ] **Step 8: Снятие защиты — DoD 12**

В `fold_carrier_rows` заменить накопление на обычный `SUM`, пропускающий
негодные слагаемые: убрать сброс `amount_ok = False` и оставить `continue`, то
есть выдать сумму по годным строкам как сумму статьи. Прогнать
`tests/unit/test_article_rates.py -k "without_an_amount or non_finite_amount"`.
Expected: FAIL — `amount_ok is True` и `amount == Decimal("100000.00")` вместо
`None`, то есть **частичная ставка по части корпусов, выданная за ставку по
объекту**. Вернуть файл.

- [ ] **Step 8а: Снятие защиты — явный контекст (сверх снятий спеки)**

В `article_rates.py` заменить в `_RATE_CONTEXT` `prec=ARITHMETIC_PRECISION` на
`prec=28` — то есть вернуть точность ambient-контекста, оставив трапы. Прогнать
`tests/unit/test_article_rates.py::test_the_fold_does_not_depend_on_the_order_of_the_rows`.
Expected: FAIL на ОБОИХ утверждениях: прямой ход даёт
`Decimal('1000000000000000000000000002')`, обратный —
`Decimal('1000000000000000000000000001')`, и ни один не равен литералу
`1000000000000000000000000001.2`. То есть свёртка зависит от порядка строк
запроса. Вернуть файл (см. «Как снимать защиту» выше).

Без этого снятия тест зелен и при отсутствии контекста вовсе: на коротких
слагаемых хватает и `prec = 28`, и тест фиксировал бы совпадение, а не механизм.

> **Почему снятия именно такие.** Все четыре ломают МЕХАНИЗМ — полноту
> классификации, NULL-семантику слагаемого и явный десятичный контекст, — а не
> совпадение чисел, и проверяются прогоном теста, а не повторным чтением того же
> множества или того же условия, которое только что правилось.

- [ ] **Step 9: Коммит**

```bash
git add backend/services/article_rates.py backend/tests/unit/test_article_rates.py backend/tests/integration/test_passport_article_rates.py
git commit -m "feat(passport-volumes): правило ставки — классификация единиц и свёртка строк-носителей"
```

---

## Task 2: Автомат состояний и проверка сходимости

**Files:**
- Modify: `backend/services/article_rates.py`
- Test: `backend/tests/unit/test_article_rates.py`

**Interfaces:**
- Consumes: `ArticleFold`, `is_scalable_unit`, `NON_SCALABLE_UNIT_CODES` (задача 1).
- Produces (**заводится этой задачей**):
  - `class RateState(StrEnum)` — ровно десять членов со значениями
    `no_carrier`, `additional_works`, `amount_missing`, `unit_missing`,
    `unit_conflict`, `unit_not_scalable`, `volume_missing`,
    `volume_nonpositive`, `volume_inconsistent`, `rate`
  - `class RateNote(StrEnum)` — `OVERSHOOT = "overshoot"`,
    `MIXED_UNITS = "mixed_units"`, `UNVERIFIABLE = "unverifiable"` (третье
    значение — решение У3 варианта «б»; требует правки перечня в §2.10 спеки)
  - `TOLERANCE_PER_SUMMAND: Decimal = Decimal("0.01")`
  - `@dataclass(frozen=True) class ArticleRate: state: RateState;
    note: RateNote | None; unit: str | None; volume: Decimal | None;
    unit_rate: Decimal | None`
  - `def convergence_note(fold: ArticleFold, children: Sequence[ArticleFold]) -> RateNote | None`
  - `def resolve_rate(fold: ArticleFold, *, has_extras: bool,
    children: Sequence[ArticleFold]) -> ArticleRate`

**Правила** (§2.4, §2.5; уточнение У3 выше):

- `children` — свёртки статей-детей **дерева классификатора**, у которых есть
  свой объём (`fold.volume is not None`). Детей без объёма вызывающий не передаёт.
- `convergence_note` — три проверки строго в этом порядке, арифметика последней
  (§2.4: «смешение выясняется до арифметики»); пустой `children` — сразу `None`:
  1. **Смешение.** Если у какого-нибудь ребёнка единица ИЗВЕСТНА и отличается —
     `child.unit_code is not None and child.unit_code != fold.unit_code` —
     вернуть `MIXED_UNITS`. Оговорка `is not None` обязательна: без неё ребёнок с
     непрочитанной единицей (`unit_code is None`) попадал бы в «смешение», и код
     утверждал бы про него ложное — единица не «отличается», она неизвестна.
  2. **Непроверяемость** (уточнение У3, вариант «б»). Если у какого-нибудь
     ребёнка `unit_code is None` — вернуть `UNVERIFIABLE`. Такой ребёнок приходит
     с объёмом (вызывающий фильтрует по `volume is not None`), но единицы узла не
     несёт: `unit_missing` и `unit_conflict` в свёртке гасят `unit_code`, а
     `volume` оставляют. Складывать его объём с объёмами братьев нельзя — значит
     сходимость проверить нечем.
  3. **Арифметика.** `S = Σ child.volume`, `n = len(children)`,
     `ε = TOLERANCE_PER_SUMMAND * n`; если `S > fold.volume + ε` — `OVERSHOOT`.
     Иначе `None` (недобор и равенство законны).

  Порядок 1 перед 2 намеренный: определённая находка («единицы разделов
  расходятся») полезнее отсутствия сведений, а узел гасится в обоих случаях
  одинаково — различается только подпись. Сумма `S` считается внутри
  `localcontext(_RATE_CONTEXT)`, как и вся арифметика фичи (Global Constraint 16).
- `resolve_rate` — проверки строго в порядке §2.5, первое совпадение выигрывает:
  1. `fold.rows == 0 and has_extras` → `additional_works`
  2. `fold.rows == 0` → `no_carrier`
  3. `not fold.amount_ok` → `amount_missing`
  4. `fold.unit_missing` → `unit_missing`
  5. `fold.unit_conflict` → `unit_conflict`
  6. `not is_scalable_unit(fold.unit_code)` → `unit_not_scalable`
  7. `fold.volume_missing` → `volume_missing`
  8. `fold.volume_nonpositive` → `volume_nonpositive`
  9. `convergence_note(...) is not None` → `volume_inconsistent` + `note`
  10. иначе → `rate`
- `unit` в результате: `fold.unit_symbol` всегда, когда он известен (уточнение
  У2). При `no_carrier`/`additional_works` он `None` по построению свёртки; при
  `unit_missing`/`unit_conflict` — тоже `None`. При `volume_missing` единица
  ЕСТЬ — так показывает макет (строка 240: `Ед.` = `м²`, `Объём` = «—»).
- `volume` в результате: `fold.volume` **только** при `state is RateState.RATE`,
  иначе `None` (контракт §2.10: «NULL во всех состояниях, кроме rate»).
- `unit_rate`: `fold.amount / fold.volume` только при `RATE`, **внутри
  `localcontext(_RATE_CONTEXT)`** (Global Constraint 16), а НЕ в контексте по
  умолчанию. Проверки 3, 7, 8 уже гарантируют конечный числитель и строго
  положительный конечный знаменатель, поэтому объявленные в контексте трапы
  `DivisionByZero` и `InvalidOperation` здесь недостижимы — они стоят
  утверждением, а не обработкой. Опираться на ambient-контекст нельзя по той же
  причине, что и в свёртке: он приходит с чужими трапами, и включённый где-то
  `Inexact` превратил бы штатное деление в исключение — а частное почти никогда
  не представимо конечной дробью. Округление НЕ здесь: его делает граница ответа
  (задача 4).

- [ ] **Step 1: Написать падающие тесты автомата**

Дописать в `backend/tests/unit/test_article_rates.py`. Помощник:

```python
def _fold(**kwargs):
    """Свёртка через публичный `fold_carrier_rows`, а не конструктором
    `ArticleFold`: тест обязан ходить тем же путём, что продакшен."""
    return fold_carrier_rows([_row(**kwargs)], effective_rate=None)


def _empty_fold():
    return fold_carrier_rows([], effective_rate=None)
```

```python
def test_no_carrier_when_the_estimate_never_names_the_article():
    """DoD 6: корень классификатора без строки с кодом — клетка пуста, пометки нет."""
    rate = resolve_rate(_empty_fold(), has_extras=False, children=[])
    assert rate.state is RateState.NO_CARRIER
    assert (rate.note, rate.unit, rate.volume, rate.unit_rate) == (None, None, None, None)


def test_additional_works_wins_over_no_carrier_when_extras_exist():
    """DoD 7, коллизия «допработы есть, носителя нет». Оговорка «нет допработ»
    внутри `no_carrier` — единственное, что отличает два состояния."""
    rate = resolve_rate(_empty_fold(), has_extras=True, children=[])
    assert rate.state is RateState.ADDITIONAL_WORKS


def test_volume_missing_is_a_different_state_from_additional_works():
    """DoD 7, вторая половина: ставки пустые одинаково, смысл разный."""
    rate = resolve_rate(_fold(volume=None), has_extras=False, children=[])
    assert rate.state is RateState.VOLUME_MISSING
    assert rate.unit == "м²"  # единица ЕСТЬ — макет, строка 240


def test_priority_gives_exactly_one_state_on_a_collision():
    """DoD 15: узел под `unit_not_scalable` И под `volume_missing` получает
    состояние с МЕНЬШИМ номером — шестое, не седьмое."""
    rate = resolve_rate(
        _fold(unit_code="SET", unit_symbol="компл", volume=None),
        has_extras=False, children=[],
    )
    assert rate.state is RateState.UNIT_NOT_SCALABLE


def test_piece_with_a_real_quantity_is_not_declared_non_scalable():
    """DoD 10, вторая половина (замер 7: у лифтов настоящие 10 штук)."""
    rate = resolve_rate(
        _fold(unit_code="PCS", unit_symbol="шт", amount=Decimal("500.00"),
              volume=Decimal("10.00")),
        has_extras=False, children=[],
    )
    assert rate.state is RateState.RATE
    assert rate.unit_rate == Decimal("50")


@pytest.mark.parametrize("children_volume", [Decimal("8.00"), Decimal("2.00")])
def test_mixed_units_do_not_depend_on_the_numbers(children_volume):
    """DoD 17. Два прогона: при 8,00 сумма разноразмерных СОВПАДАЕТ с объёмом
    узла (2 + 8 = 10), при 2,00 — нет. Состояние обязано быть одинаковым.

    Снятие «детектировать смешение сравнением S ≈ P» краснеет на первом прогоне.
    """
    parent = _fold(volume=Decimal("10.00"))                      # м²
    in_m2 = fold_carrier_rows([_row(volume=Decimal("2.00"))], effective_rate=None)
    in_sets = fold_carrier_rows(
        [_row(unit_code="SET", unit_symbol="компл", volume=children_volume)],
        effective_rate=None,
    )
    rate = resolve_rate(parent, has_extras=False, children=[in_m2, in_sets])
    assert rate.state is RateState.VOLUME_INCONSISTENT
    assert rate.note is RateNote.MIXED_UNITS


def test_overshoot_kills_the_node_and_leaves_the_child_alone():
    """DoD 18: перебор гасит ставку УЗЛА; у ребёнка объём свой и остаётся в силе."""
    parent = _fold(volume=Decimal("10.00"))
    child = fold_carrier_rows(
        [_row(amount=Decimal("600.00"), volume=Decimal("12.00"))], effective_rate=None
    )
    parent_rate = resolve_rate(parent, has_extras=False, children=[child])
    child_rate = resolve_rate(child, has_extras=False, children=[])
    assert (parent_rate.state, parent_rate.note) == (
        RateState.VOLUME_INCONSISTENT, RateNote.OVERSHOOT,
    )
    assert parent_rate.unit_rate is None
    assert child_rate.state is RateState.RATE
    assert child_rate.unit_rate == Decimal("50")


def test_undershoot_does_not_kill_the_rate():
    """DoD 19: недобор — НОРМА (не все дети несут код статьи), а не расхождение."""
    parent = _fold(amount=Decimal("1000.00"), volume=Decimal("10.00"))
    child = fold_carrier_rows([_row(volume=Decimal("3.00"))], effective_rate=None)
    rate = resolve_rate(parent, has_extras=False, children=[child])
    assert rate.state is RateState.RATE
    assert rate.unit_rate == Decimal("100")


def test_the_tolerance_boundary_is_inclusive_on_the_undershoot_side():
    """DoD 20. Два ребёнка → n = 2 → ε = 0,02. P = 10,00.
    S = 10,02 — недобор (ставка есть); S = 10,03 — перебор (ставки нет)."""
    parent = _fold(amount=Decimal("1000.00"), volume=Decimal("10.00"))

    def two_children(total: str):
        first = fold_carrier_rows([_row(volume=Decimal("5.00"))], effective_rate=None)
        second = fold_carrier_rows(
            [_row(volume=Decimal(total) - Decimal("5.00"))], effective_rate=None
        )
        return [first, second]

    assert resolve_rate(parent, has_extras=False,
                        children=two_children("10.02")).state is RateState.RATE
    assert resolve_rate(parent, has_extras=False,
                        children=two_children("10.03")).state is RateState.VOLUME_INCONSISTENT


@pytest.mark.parametrize(
    "child_rows",
    [
        pytest.param([dict(unit_code=None, unit_symbol=None)], id="child_unit_missing"),
        pytest.param(
            [dict(unit_code="M2", unit_symbol="м²"), dict(unit_code="M3", unit_symbol="м³")],
            id="child_unit_conflict",
        ),
    ],
)
def test_a_child_with_an_unreadable_unit_is_unverifiable_not_mixed(child_rows):
    """Уточнение У3, вариант «б». У ребёнка единица НЕ ПРОЧИТАНА — её нет
    (`unit_missing`) либо строки не согласны (`unit_conflict`). Объём у него при
    этом ЕСТЬ, поэтому в проверку он попадает.

    Родителя гасим — иначе он получил бы ставку при недоборе, посчитанном по
    неполному множеству детей, то есть молча. Но подпись обязана быть своя:
    «смешение» утверждало бы, что единица отличается, а она неизвестна. За двумя
    подписями стоят разные действия — разобраться с единицами разделов против
    дозаполнить единицу в смете.
    """
    parent = _fold(volume=Decimal("10.00"))
    child = fold_carrier_rows(
        [_row(volume=Decimal("4.00"), **row) for row in child_rows], effective_rate=None
    )
    assert child.volume is not None, "ребёнок обязан дойти до проверки с объёмом"
    rate = resolve_rate(parent, has_extras=False, children=[child])
    assert rate.state is RateState.VOLUME_INCONSISTENT
    assert rate.note is RateNote.UNVERIFIABLE


def test_mixed_units_outrank_unverifiable_when_both_children_are_present():
    """Уточнение У3, порядок проверок: определённая находка важнее отсутствия
    сведений. Один ребёнок в другой ИЗВЕСТНОЙ единице, второй — с непрочитанной.
    Узел гасится в любом случае, различается подпись, и она обязана быть ОДНА.
    """
    parent = _fold(volume=Decimal("10.00"))
    in_sets = fold_carrier_rows(
        [_row(unit_code="SET", unit_symbol="компл", volume=Decimal("2.00"))],
        effective_rate=None,
    )
    unreadable = fold_carrier_rows(
        [_row(unit_code=None, unit_symbol=None, volume=Decimal("2.00"))],
        effective_rate=None,
    )
    rate = resolve_rate(parent, has_extras=False, children=[in_sets, unreadable])
    assert rate.state is RateState.VOLUME_INCONSISTENT
    assert rate.note is RateNote.MIXED_UNITS


def test_amount_missing_outranks_every_unit_and_volume_state():
    """§2.5, порядок: третья проверка стоит выше четвёртой–восьмой."""
    fold = fold_carrier_rows(
        [_row(amount=None, unit_code=None, unit_symbol=None, volume=None)],
        effective_rate=None,
    )
    assert resolve_rate(fold, has_extras=False, children=[]).state is RateState.AMOUNT_MISSING


def test_unit_conflict_is_its_own_state():
    """DoD 9 на уровне автомата: складывать нельзя, и причина названа отдельно."""
    fold = fold_carrier_rows(
        [_row(unit_code="M2", unit_symbol="м²"), _row(unit_code="M3", unit_symbol="м³")],
        effective_rate=None,
    )
    assert resolve_rate(fold, has_extras=False, children=[]).state is RateState.UNIT_CONFLICT


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-1")])
def test_nonpositive_volume_is_its_own_state(bad):
    """DoD 14 на уровне автомата: делить нельзя — и это не «объём не указан»."""
    fold = fold_carrier_rows([_row(volume=bad)], effective_rate=None)
    assert resolve_rate(fold, has_extras=False, children=[]).state is RateState.VOLUME_NONPOSITIVE


def test_the_state_enum_carries_exactly_the_ten_contract_values():
    """§2.10: перечень состояний в контракте и в §2.5 — ОДИН список.
    Расхождение здесь означает, что половину состояний нельзя вернуть."""
    assert {state.value for state in RateState} == {
        "no_carrier", "additional_works", "amount_missing", "unit_missing",
        "unit_conflict", "unit_not_scalable", "volume_missing",
        "volume_nonpositive", "volume_inconsistent", "rate",
    }
    assert {note.value for note in RateNote} == {
        "overshoot", "mixed_units", "unverifiable",
    }
```

- [ ] **Step 2: Прогнать — должно падать на импорте `RateState`**

Run: `cd backend && uv run pytest tests/unit/test_article_rates.py -v`
Expected: FAIL, `ImportError: cannot import name 'RateState'`.

- [ ] **Step 3: Реализовать автомат**

Дописать в `backend/services/article_rates.py` `RateState`, `RateNote`,
`TOLERANCE_PER_SUMMAND`, `ArticleRate`, `convergence_note`, `resolve_rate` по
правилам выше. Порядок проверок в `resolve_rate` — линейная цепочка `if/return`
в порядке §2.5, без словарей и без сортировок: порядок здесь и есть правило, и
он обязан читаться глазами.

Докстрока `resolve_rate` обязана назвать: (а) что порядок — часть правила и
первое совпадение выигрывает; (б) что оговорка «нет допработ» внутри
`no_carrier` выражена ПОРЯДКОМ (проверка 1 стоит раньше), и что условия
взаимоисключающи, поэтому перестановка первых двух ничего не меняет — DoD 7
снимает именно оговорку, а не порядок; (в) почему смешение единиц ловится
предикатом, а не арифметикой (§2.4, цена решения названа там же).

- [ ] **Step 4: Прогнать — должно быть зелено**

Run: `cd backend && uv run pytest tests/unit/test_article_rates.py -v`
Expected: PASS, 31 тест — 13 задачи 1 и 18 задачи 2. Это СЛУЧАИ, а не
функции: функций 25, пять параметризаций дают ещё шесть случаев.

- [ ] **Step 5: Снятие защиты — DoD 7**

В `resolve_rate` убрать из условия первой проверки требование `has_extras`,
превратив её в `if fold.rows == 0: return no_carrier` (то есть удалить оговорку,
а не переставить проверки). Прогнать
`tests/unit/test_article_rates.py::test_additional_works_wins_over_no_carrier_when_extras_exist`.
Expected: FAIL — `RateState.NO_CARRIER != RateState.ADDITIONAL_WORKS`. Вернуть файл.

- [ ] **Step 6: Снятие защиты — DoD 17**

В `convergence_note` заменить предикат смешения на арифметическое сравнение:
убрать проверку `child.unit_code != fold.unit_code` и складывать объёмы детей
независимо от единиц. Прогнать `-k mixed_units_do_not_depend_on_the_numbers`.
Expected: FAIL **на прогоне с `children_volume = 8.00`** (там 2 + 8 = 10 = P, и
арифметика смешения не увидит), PASS на прогоне с 2,00. Именно это разделение
прогонов и есть проверка: краснеет ровно тот случай, который арифметика
пропускает. Вернуть файл.

- [ ] **Step 7: Снятие защиты — DoD 20**

В `convergence_note` заменить `ε = TOLERANCE_PER_SUMMAND * n` на
`ε = TOLERANCE_PER_SUMMAND` (допуск без накопления по слагаемым). Прогнать
`-k tolerance_boundary`. Expected: FAIL на случае `S = 10,02` (при n = 2 допуск
0,01 объявит его перебором). Вернуть файл.

- [ ] **Step 7а: Снятие защиты — оговорка `is not None` в предикате смешения
      (сверх снятий спеки)**

В `convergence_note` убрать из первой проверки оговорку `child.unit_code is not
None`, оставив `child.unit_code != fold.unit_code`. Прогнать
`-k "unverifiable_not_mixed or mixed_units_outrank"`. Expected: FAIL на ОБОИХ
случаях `unverifiable_not_mixed` (`RateNote.MIXED_UNITS != RateNote.UNVERIFIABLE`),
PASS на `mixed_units_outrank`. Вернуть файл.

Это разделение и есть проверка: без оговорки ребёнок с непрочитанной единицей
молча получает подпись «объём смешан» — утверждение, которого никто не проверял.

> **Проверять снятия прогоном теста, а не чтением правленого кода.** Все четыре
> снятия ломают механизм: оговорку состояния, предикат смешения, формулу допуска,
> оговорку известной единицы. Ни одно из них не проверяется тем же выражением,
> которое правилось.

- [ ] **Step 8: Коммит**

```bash
git add backend/services/article_rates.py backend/tests/unit/test_article_rates.py
git commit -m "feat(passport-volumes): автомат состояний ставки и проверка сходимости"
```

---

## Task 3: Строки-носители запросом и защита от одноимённой вложенности

**Files:**
- Modify: `backend/crud/project_passport.py`
- Test: `backend/tests/integration/test_passport_article_rates.py`

**Interfaces:**
- Consumes: `CarrierRow` (задача 1); `PositionItem`, `UnitOfMeasure`, `Proposal`,
  `Lot`, `Estimate` (существуют, `backend/models.py`).
- Produces (**заводятся этой задачей**):
  - `def _carrier_rows_select(estimate_id: int) -> sa.Select`
  - `def _carrier_rows_by_category(db: Session, estimate_id: int,
    effective_rate: Decimal | None) -> dict[int, ArticleFold]`

**Что делает запрос.** Один `SELECT` по ВСЕМ строкам-разделам сметы
(`is_chapter = true`), а не только по носителям: цепочка предков носителя может
проходить через раздел без кода, и без всех разделов её не построить.

```
SELECT pi.id, pi.chapter_item_id, pi.work_category_id, pi.smr_article_raw,
       pi.total_cost_total                                  AS amount,
       COALESCE(e.vat_rate_base_override, p.vat_rate)       AS vat_rate_base,
       u.code                                               AS unit_code,
       u.symbol                                             AS unit_symbol,
       COALESCE(pi.suggested_quantity, pi.quantity)          AS volume
FROM position_items pi
JOIN proposals p ON p.id = pi.proposal_id
JOIN lots      l ON l.id = p.lot_id
JOIN estimates e ON e.id = l.estimate_id
LEFT JOIN units_of_measure u ON u.id = pi.unit_id
WHERE l.estimate_id = :estimate_id AND pi.is_chapter = true
```

- `COALESCE(e.vat_rate_base_override, p.vat_rate)` — **парная граница** с
  `v_category_totals` (миграция 0012, строка 111): то же выражение базы НДС,
  общего кода нет и быть не может (миграция застыла навсегда). Назвать это в
  докстроке так же, как `_finite_amount` называет свою парность с 0010.
- `ORDER BY` не нужен и **не добавляется**: результат сворачивается в суммы
  `Decimal`, а сложение `Decimal` от порядка не зависит ни значением, ни
  экспонентой. Это отличие от `_extras_select`/`_own_sections_select`, где
  порядок наблюдаем клиентом; причину написать в докстроке, чтобы ревью не
  требовало третьего явного порядка.

**Что делает свёртка по статьям** (в этом порядке):

1. Построить `parent = {row.id: row.chapter_item_id}` и
   `carrier_category = {row.id: row.work_category_id}` по строкам, где
   `smr_article_raw is not None and work_category_id is not None`.
2. Носитель отбрасывается, если у него есть **предок-носитель той же статьи**:
   подъём по `parent`, сравнение `carrier_category` (§2.3, DoD 4). Предикат —
   по `work_category_id`, а не по тексту кода: узел свода ключуется статьёй, и
   задваивались бы именно её деньги. У носителей это совпадает с равенством
   кодов, потому что разрешение «код → статья» детерминировано, а строка ручного
   разноса носителем не является вовсе (у неё `smr_article_raw IS NULL`).
   Подъём обязан быть защищён от цикла — счётчиком шагов по числу разделов;
   схема цикл запрещает составным self-FK, но чтение паспорта не имеет права
   зависнуть.
3. Оставшиеся сгруппировать по `work_category_id` и свернуть каждую группу
   `fold_carrier_rows(rows, effective_rate)`.
4. Вернуть `{category_id: ArticleFold}`. Статьи без носителя в словаре
   отсутствуют — вызывающий читает через `.get(id)` и подставляет пустую
   свёртку.

- [ ] **Step 1: Написать падающие интеграционные тесты**

Дописать в `backend/tests/integration/test_passport_article_rates.py`. Ниже —
**эскиз**: `_proposal`, `_chapter`, `_position`, `_category`, `_unit_id`
заводятся этой задачей как локальные копии; `_carrier_rows_by_category`
заводится этой же задачей; `fold_carrier_rows` существует с задачи 1.

```python
def test_the_rate_comes_from_the_carrier_row_not_from_the_sum_of_the_layers(
    db_session, factories
):
    """DoD 1. Под статьёй лежат слои ОДНОЙ поверхности: подсистема, утеплитель,
    панели. Их объёмы в сумме дают 50 089,00 при объёме строки 19 545,81 —
    пропорция замера 3 спеки (2,5626×).

    Сумма строки выбрана так, чтобы ставка по носителю была ровно 100,00 ₽/ед.
    (19 545,81 × 100). Абсолютных сумм стенда здесь нет: число синтетическое.

    Снятие: взять объём суммой листьев — ставка станет 39,02 и тест краснеет.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    facade = _category(db_session, "6.4")
    carrier = _chapter(
        factories, proposal, category_id=facade.id, smr_article_raw="6.4",
        unit_id=m2, suggested_quantity=Decimal("19545.81"),
        total_cost_total=Decimal("1954581.00"),
    )
    for layer in (Decimal("19545.81"), Decimal("19545.81"), Decimal("10997.38")):
        _position(factories, proposal, chapter=carrier, unit_id=m2,
                  suggested_quantity=layer, total_cost_total=Decimal("100.00"))

    folds = _carrier_rows_by_category(db_session, proposal.lot.estimate_id, None)
    fold = folds[facade.id]
    assert fold.volume == Decimal("19545.81")
    assert fold.amount / fold.volume == Decimal("100")


def test_three_corpus_rows_of_one_article_are_added_up(db_session, factories):
    """DoD 2: три строки с одним кодом (корпуса) — объём и деньги суммой трёх."""
    proposal = _proposal(factories)
    m3 = _unit_id(db_session, "M3")
    article = _category(db_session, "4.1.1")
    for volume, amount in (("1000.00", "100000.00"), ("2000.00", "200000.00"),
                           ("3000.00", "300000.00")):
        _chapter(factories, proposal, category_id=article.id, smr_article_raw="4.1.1",
                 unit_id=m3, suggested_quantity=Decimal(volume),
                 total_cost_total=Decimal(amount))

    fold = _carrier_rows_by_category(db_session, proposal.lot.estimate_id, None)[article.id]
    assert (fold.rows, fold.volume, fold.amount) == (
        3, Decimal("6000.00"), Decimal("600000.00"),
    )


def test_an_article_nested_inside_another_article_keeps_its_own_rate(db_session, factories):
    """DoD 3: код 6.1 лежит внутри строки с кодом 6 — ставку получают ОБЕ.

    Защита от правила первой редакции спеки, которое гасило вложенные ставки.
    Ставки сделаны РАЗНЫМИ (100 и 150), иначе перепутанные местами свёртки
    прошли бы тест.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    outer_cat, inner_cat = _category(db_session, "6"), _category(db_session, "6.1")
    outer = _chapter(factories, proposal, category_id=outer_cat.id, smr_article_raw="6",
                     unit_id=m2, suggested_quantity=Decimal("10.00"),
                     total_cost_total=Decimal("1000.00"))
    _chapter(factories, proposal, category_id=inner_cat.id, smr_article_raw="6.1",
             chapter_item_id=outer.id, unit_id=m2,
             suggested_quantity=Decimal("4.00"), total_cost_total=Decimal("600.00"))

    folds = _carrier_rows_by_category(db_session, proposal.lot.estimate_id, None)
    assert folds[outer_cat.id].amount / folds[outer_cat.id].volume == Decimal("100")
    assert folds[inner_cat.id].amount / folds[inner_cat.id].volume == Decimal("150")


def test_a_row_nested_inside_the_same_article_is_excluded(db_session, factories):
    """DoD 4: строка с кодом A внутри строки с ТЕМ ЖЕ кодом A в объём не входит —
    её деньги уже в объемлющей.

    Объём и сумма вложенной подобраны НЕ пропорционально внешней (2,00 и 500,00
    против 10,00 и 1000,00), иначе задвоение не изменило бы ставку и тест
    остался бы зелёным при снятой защите.

    Снятие: убрать подъём по предкам — объём станет 12,00, ставка 125,00.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    article = _category(db_session, "6")
    outer = _chapter(factories, proposal, category_id=article.id, smr_article_raw="6",
                     unit_id=m2, suggested_quantity=Decimal("10.00"),
                     total_cost_total=Decimal("1000.00"))
    _chapter(factories, proposal, category_id=article.id, smr_article_raw="6",
             chapter_item_id=outer.id, unit_id=m2,
             suggested_quantity=Decimal("2.00"), total_cost_total=Decimal("500.00"))

    fold = _carrier_rows_by_category(db_session, proposal.lot.estimate_id, None)[article.id]
    assert fold.rows == 1
    assert fold.volume == Decimal("10.00")
    assert fold.amount / fold.volume == Decimal("100")


def test_a_manually_assigned_section_is_not_a_carrier(db_session, factories):
    """§2.5 состояние 2: у статьи ручного разноса строки с кодом нет вовсе
    (`smr_article_raw IS NULL`), и носителем она не становится."""
    proposal = _proposal(factories)
    article = _category(db_session, "6")
    _chapter(factories, proposal, category_id=article.id,
             unit_id=_unit_id(db_session, "M2"), suggested_quantity=Decimal("10.00"))
    # `_chapter` ставит category_source='file'; smr_article_raw остаётся None.
    assert _carrier_rows_by_category(db_session, proposal.lot.estimate_id, None) == {}
```

> `proposal.lot.estimate_id` и `proposal.lot.estimate.contract_id` — проверены
> `grep`-ом: `Proposal.lot` (`models.py:580`), `Lot.estimate_id`
> (`models.py:537`), `Lot.estimate` (`models.py:546`). Отдельный помощник для
> получения сметы не нужен.

- [ ] **Step 2: Прогнать — должно падать**

Run: `cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_passport_article_rates.py -v`
Expected: FAIL, `NameError: name '_carrier_rows_by_category' is not defined`.

- [ ] **Step 3: Реализовать запрос и свёртку**

Дописать в `backend/crud/project_passport.py` рядом с `_own_sections_select` /
`_own_sections_by_category` — фича берёт величины ТЕМ ЖЕ путём, что допработы и
собственные разделы, то есть мимо `v_category_totals` намеренно (§2.10).

- [ ] **Step 4: Прогнать — зелено**

Run: та же команда.
Expected: PASS, 6 тестов (включая тест единиц из задачи 1).

- [ ] **Step 5: Снятие защиты — DoD 1**

В `_carrier_rows_by_category` подменить объём носителя суммой объёмов его прямых
позиций (`is_chapter = false` с `chapter_item_id = carrier.id`) — то есть
реализовать отвергнутый §3.1/§3.4 фолбэк. Прогнать
`-k rate_comes_from_the_carrier_row`. Expected: FAIL, `39.0221... != 100`.
Вернуть файл.

- [ ] **Step 6: Снятие защиты — DoD 4**

Убрать подъём по предкам (шаг 2 свёртки). Прогнать
`-k nested_inside_the_same_article`. Expected: FAIL — `rows == 2`,
`volume == 12.00`, ставка 125. Вернуть файл.

- [ ] **Step 7: Коммит**

```bash
git add backend/crud/project_passport.py backend/tests/integration/test_passport_article_rates.py
git commit -m "feat(passport-volumes): строки-носители статьи запросом, защита от одноимённой вложенности"
```

---

## Task 4: Пять полей на каждом узле свода

**Files:**
- Modify: `backend/crud/project_passport.py`
- Test: `backend/tests/integration/test_passport_article_rates.py`

**Interfaces:**
- Consumes: `_carrier_rows_by_category` (задача 3), `resolve_rate`,
  `ArticleFold`, `ArticleRate`, `RateState` (задача 2), `CategoryRef`,
  `_flatten_categories`, `_quantize_if_restated`, `_extras_by_category`
  (существуют).
- Produces (**заводится этой задачей**):
  - `def _article_rates(refs: Sequence[CategoryRef],
    folds: Mapping[int, ArticleFold],
    extras_by_category: Mapping[int, list[dict]]) -> dict[int, ArticleRate]`
  - `_category_dict(...)` — новый параметр `rates: Mapping[int, ArticleRate]` и
    пять новых ключей ответа: `unit`, `volume`, `unit_rate`, `rate_state`,
    `rate_note`.

**Правила:**

- `_article_rates` строит `children_by_parent` из `refs` (дерево
  КЛАССИФИКАТОРА, `CategoryRef.parent_id`), и для каждой статьи справочника
  вызывает `resolve_rate(fold, has_extras=..., children=[...])`, где
  `children` — свёртки ПРЯМЫХ детей-статей, у которых `fold.volume is not None`
  (§2.4: «дети с объёмом»). Прямые, а не все потомки: спека говорит «дети», и
  недобор законен, поэтому пропущенный внук ставку не портит.
- `fold` отсутствующей статьи — пустая свёртка `fold_carrier_rows([], rate)`;
  `has_extras = bool(extras_by_category.get(id))`.
- Состояние обязано быть у КАЖДОГО узла, попадающего в `categories`, включая
  корни классификатора (§2.5). Клиент состояние не вычисляет.
- `unit` в ответе — `rate.unit` (символ, уточнение У2).
- `volume` в ответе — `rate.volume` **без квантования**: это не деньги, а число
  из файла, и округлять его нельзя.
- `unit_rate` в ответе — `quantize_money(rate.unit_rate)` **безусловно**, а НЕ
  `_quantize_if_restated(..., restated_any)` (Global Constraint 17). Ставка —
  вычисленная величина, и при `prec = 100` частное почти никогда не представимо
  конечной дробью: без квантования в ответ уехала бы 100-значная строка.
  Оглядываться на `restated_any` здесь не на что — обязательство «посимвольно
  как до фичи» (`AGENTS.md` §10) защищает СУЩЕСТВУЮЩИЕ поля ответа, а `unit_rate`
  заводится этой фичей, и до неё у него формы не было. Приём `per_sqm`
  (`_quantize_if_restated`) сюда не переносить: он условен именно затем, чтобы не
  тронуть старое поле на непересчитанной смете, и на новом поле эта условность
  давала бы ровно одно — два разных формата у одного поля в зависимости от
  чужого признака.
- `rate_state` — `rate.state.value`; `rate_note` — `rate.note.value if
  rate.note else None`.
- **Путь без сметы** (`estimate is None`) обязан отдать ту же ФОРМУ: пять полей
  на каждом узле со `rate_state = "no_carrier"` и остальными `None`.
  `_category_dict(..., rates={})` этого добьётся, если по умолчанию
  подставляется `no_carrier`.

- [ ] **Step 1: Написать падающие тесты ответа**

Дописать в тот же интеграционный файл. Утверждения — по ответу
`get_project_passport`, а не по внутренностям.

```python
def test_every_category_node_carries_exactly_one_rate_state(db_session, factories):
    """§2.5: состояние обязательно у КАЖДОГО узла свода, включая корни."""
    proposal = _proposal(factories)
    _position(factories, proposal, chapter=_chapter(
        factories, proposal, category_id=_category(db_session, "6").id, smr_article_raw="6",
    ))
    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    states = {node["rate_state"] for node in passport["categories"]}
    assert states  # узлы есть
    assert states <= {state.value for state in RateState}
    assert all(node["rate_state"] is not None for node in passport["categories"])


def test_a_root_without_a_carrier_is_no_carrier_without_a_note(db_session, factories):
    """DoD 6 на уровне ответа: клетка пуста, пометки нет."""
    proposal = _proposal(factories)
    _position(factories, proposal)
    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    root = next(n for n in passport["categories"] if n["code"] == "6")
    assert root["rate_state"] == "no_carrier"
    assert (root["rate_note"], root["unit"], root["volume"], root["unit_rate"]) == (
        None, None, None, None,
    )


def test_every_contract_state_is_reachable_by_a_fixture_of_this_file(db_session, factories):
    """DoD 16. Утверждение о ПОКРЫТИИ, а не о конкретном ответе: набор состояний,
    достигнутых фикстурами этого файла, обязан совпасть со списком контракта.

    Реализация: фикстура строит по одному узлу на каждое состояние (девять плюс
    `rate`) в ОДНОЙ смете и сверяет множество `rate_state` в ответе со списком
    §2.10. Иначе контракт снова разойдётся с автоматом — ровно так рассыпалась
    первая редакция спеки.
    """
    ...  # состав фикстуры — задача исполнителя; утверждение ниже обязательно
    # assert observed == {state.value for state in RateState}


def test_the_rate_follows_the_display_axis(db_session, factories):
    """DoD 21. База 20 %, цель 22 %: ставка отличается от «сырой» на тот же
    множитель, что и суммы рядом.

    Снятие: считать от исходной суммы (не звать `restate_gross`) — тест краснеет.
    """
    # 1 200,00 при базе 20 % и объёме 10 → сырая ставка 120,00
    # при цели 22 %: 1200/1.20*1.22 = 1220 → ставка 122,00
    ...
    # assert Decimal(node["unit_rate"]) == Decimal("122")
    # assert Decimal(node["unit_rate"]) / Decimal(raw_rate) == Decimal("1220") / Decimal("1200")


def test_the_route_and_the_response_keys_of_the_feature(client, db_session, factories):
    """DoD 27: маршрут действующий, новых нет; пять полей и `rate_coverage` на месте."""
    ...
    # response = client.get(f"/api/v1/analytics/project-passport/{contract_id}")
    # assert response.status_code == 200
    # node = response.json()["categories"][0]
    # assert {"unit", "volume", "unit_rate", "rate_state", "rate_note"} <= node.keys()


def test_a_contract_without_an_estimate_keeps_the_same_response_shape(db_session, factories):
    """Правило 8 `get_project_passport`: форма ответа одна на оба пути функции."""
    contract = factories.ContractFactory.create()
    passport = get_project_passport(db_session, contract.id)
    assert passport["rate_coverage"] == {
        "articles_with_rate": 0, "articles_total": 0,
        "money_share": None, "money_share_state": "total_unavailable",
    }
    assert all(n["rate_state"] == "no_carrier" for n in passport["categories"])
```

> **Три теста выше оставлены УТВЕРЖДЕНИЯМИ, а не готовым кодом** (`AGENTS.md`
> §9.1): состав фикстуры на десять состояний, точная сборка `client`-теста и
> способ получить «сырую» ставку зависят от того, как исполнитель разложит
> фикстуры задачи 4, и синтаксически законченный код здесь читался бы как
> готовый. Числа в утверждениях (122, 1220/1200, множества ключей) — обязательны
> и не подлежат смягчению.

- [ ] **Step 2: Прогнать — падает**

Run: `cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_passport_article_rates.py -v`
Expected: FAIL — `KeyError: 'rate_state'`.

- [ ] **Step 3: Реализовать `_article_rates` и расширить `_category_dict`**

Плюс: дополнить нумерованный список правил в докстроке
`get_project_passport` пунктами 19 (`unit`/`volume`/`unit_rate`/`rate_state`/
`rate_note`, со ссылкой на §2.2–§2.5) и 20 (`rate_coverage`, задача 5 — пункт
завести сейчас, значение появится в задаче 5). Список правил там — действующий
контракт функции, и новые поля обязаны в нём стоять.

- [ ] **Step 4: Прогнать — зелено**

Run: та же команда. Expected: PASS.

- [ ] **Step 5: Проверить, что миграции не появилось**

Run: `just db-test-check`
Expected: `alembic upgrade head` и `alembic check` без diff. DoD 28.

- [ ] **Step 6: Проверить, что дофичевые тесты паспорта не ослабли**

Run: `cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_project_passport_api.py -v`
Expected: PASS, ни один тест не правился. Если что-то красное — это регресс, а
не «ожидаемое изменение»: поля ДОБАВЛЯЮТСЯ, ничего существующего не трогая
(тождество §2.4 спеки пересчёта НДС).

- [ ] **Step 7: Снятие защиты — DoD 21**

В свёртке (`fold_carrier_rows`) заменить `restate_gross(...)` на сырое
`row.amount`. Прогнать `-k follows_the_display_axis`. Expected: FAIL — ставка
120 вместо 122. Вернуть файл.

- [ ] **Step 8: Коммит**

```bash
git add backend/crud/project_passport.py backend/tests/integration/test_passport_article_rates.py
git commit -m "feat(passport-volumes): объём, единица, ставка и состояние на каждом узле свода"
```

---

## Task 5: Охват — неперекрывающийся набор и `rate_coverage`

**Files:**
- Modify: `backend/crud/project_passport.py`
- Test: `backend/tests/integration/test_passport_article_rates.py`

**Interfaces:**
- Consumes: `_article_rates`, `_carrier_rows_by_category`, `_share_pct`,
  `_flatten_categories`, `CategoryNode` (существуют/задачи 3–4).
- Produces (**заводится этой задачей**):
  - `def _rate_coverage(nodes: Sequence[CategoryNode],
    folds: Mapping[int, ArticleFold], rates: Mapping[int, ArticleRate],
    grand_total: Decimal | None) -> dict`
  - `class MoneyShareState(StrEnum)` — ровно пять членов со значениями
    `complete`, `partial`, `no_articles`, `total_unavailable`, `out_of_range`
    (§2.8, DoD 34)
  - ключ ответа `rate_coverage: {"articles_with_rate": int,
    "articles_total": int, "money_share": Decimal | None,
    "money_share_state": str}` на ВЕРХНЕМ уровне.

**Правила (§2.8):**

- **Набор** — статьи, у которых есть носитель (`id in folds`) и у которых **нет
  потомка-статьи с носителем** в дереве классификатора. Неперекрывающийся; от
  ставок не зависит. Предок/потомок выясняется по `node.ref.parent_id`.
- **Аргумент — `nodes`, а не `refs`**, и это следствие числителя: `CategoryRef`
  несёт только идентификаторы, а сумма статьи лежит на `CategoryNode.total`.
  Список — тот самый плоский, что `_flatten_categories` отдаёт под ключ
  `categories`. Поэтому «K = сумма видимых долей» верно ПО ПОСТРОЕНИЮ: набор и
  колонка считаются по одному списку.
- Если статья с носителем в этом списке не появилась, она не на экране, и
  сложить её долю читатель не может. Такое расхождение — дефект сборки дерева, а
  не повод молча пропустить статью: набор строится по `nodes`, и статья без узла
  в набор просто не попадёт. Тест `articles_total` на фикстуре из двух названных
  статей ловит обратный случай (узел есть, в набор не взяли).
- `articles_total` (M) = размер набора. Не 362 статьи справочника, не число
  видимых строк дерева, не корни.
- `articles_with_rate` (N) = сколько статей набора имеют `state is
  RateState.RATE`.
- `money_share` (K) = `_share_pct(covered, grand_total)`, где `covered` — сумма
  **`node.total`** статей набора со состоянием `rate`, то есть тех же величин,
  что стоят в колонке «Итого, ₽». НЕ сумма `fold.amount`: §2.8 говорит «сумма
  денег СТАТЕЙ», а деньги статьи в этой таблице — её итог из
  `v_category_totals`, а не сумма её строк-разделов. Знаменатель — тот же
  `grand_total`, что у всех `share_pct` таблицы (§2.8: «иначе проценты на одном
  листе окажутся от разных целых»), НЕквантованный, как у остальных долей.
  Величина — **процент** (0..100), как `share_pct`, а не доля: у клиента для неё
  уже есть `formatSharePercent`.
- **K ≤ 100 % доказуемо — но только при неотрицательных суммах.** Набор —
  антицепь в дереве классификатора: если статья A предок статьи B и обе в
  наборе, то у A есть потомок-статья с носителем, а такие в набор не берутся.
  Значит поддеревья статей набора попарно не пересекаются, `node.total` каждой —
  итог её поддерева, и сумма таких итогов не превосходит `grand_total`. Числитель
  и знаменатель измерены ОДНИМ путём и приведены к одной оси, поэтому доля
  инвариантна к смене цели показа (DoD 22).
- **Антицепь одна границу не держит, нужна вторая защита.** `CHECK`-а на
  `position_items.total_cost_total >= 0` в схеме нет: поле объявлено
  `Numeric, nullable=True`, миграция `0010` называет это открытым хвостом Ф4 (у
  `estimate_additional_works.total_amount` `CHECK` на знак есть, у позиций —
  нет). Отрицательная сумма в НЕПОКРЫТОМ поддереве уменьшает знаменатель, не
  уменьшая числителя, и K превысит 100 % при верном наборе. Поэтому:
  **`money_share` отдаётся `None`, если доля вышла из диапазона 0..100** (§2.8,
  DoD 32). Не срезать до 100 и не печатать 160: выход за диапазон означает либо
  отрицательные суммы, либо ошибку построения набора, и то и другое надо смотреть
  глазами. Проверка ставится на РЕЗУЛЬТАТ, а не на сканирование входа: так она
  ловит и причины, которых я не перечислил, включая дефект самого набора.
- **Состояние охвата — отдельное поле** (§2.8, DoD 34). Три из пяти исходов
  дают `money_share = None`, и клиент обязан различать их по `money_share_state`,
  а НЕ по `money_share === null`. Проверки строго сверху вниз, первое совпадение
  выигрывает, у ответа ровно одно состояние:
  1. `grand_total is None or grand_total == 0` → `TOTAL_UNAVAILABLE`
  2. `articles_total == 0` → `NO_ARTICLES`
  3. `grand_total < 0` → `OUT_OF_RANGE` — **до деления**
  4. `k` вне `0..100` → `OUT_OF_RANGE`
  5. `totals["positions_rows_priced"] < totals["positions_rows"]` → `PARTIAL`
  6. иначе → `COMPLETE`

  Порядок объясним построчно (§2.8): без знаменателя мерить нечем; на пустом
  наборе делить не придётся; отказ печатать число сильнее оговорки о неполноте.
  Неполнота берётся ПО ВСЕМУ ПАСПОРТУ, а не по учитываемым статьям: знаменатель —
  цена договора, и непросчитанные строки вне набора делают «цену договора»
  перебором. Поля `positions_rows` / `positions_rows_priced` в `totals` уже есть —
  заводить счётчик не надо.
- **Проверка 3 не дублирует проверку 4, и порядок между ними существен.**
  Отрицательный знаменатель вместе с отрицательным числителем даёт отношение
  ВНУТРИ диапазона: `covered = -50` при `grand_total = -100` — ровно 50 %.
  Проверка результата такой случай пропускает, и лист печатает правдоподобный
  процент по бессмысленным числам. Схема отрицательные суммы допускает, поэтому
  случай достижим. Состояние у обеих проверок одно — подпись и действие совпадают
  («суммы сметы требуют проверки»), различается только момент: знак смотрим до
  деления, диапазон после.
- **Ноль — не `NO_ARTICLES`.** Набор непуст, ставок нет, K = 0 — «покрыто 0 %»
  правда. `NO_ARTICLES` — про пустой набор, где делить нечего. Но каким
  состоянием окажется ноль, решает полнота сумм, а не сам ноль: при полном итоге
  `COMPLETE`, при частичном `PARTIAL` (проверка 5 стоит выше). Про два договора
  стенда с нулевым охватом (§1.1) план не утверждает НИ ОДНОГО из двух состояний:
  это зависит от их счётчиков, и замеряет их задача 9, шаг 1.
- **Частичный итог статьи K НЕ гасит** (DoD 33). Сумма статьи складывается только
  из известных слагаемых (`category_rollup`: неизвестное — отсутствующее
  слагаемое, а не ноль), непригодные строки VIEW `0010` отсекает в
  `rows_not_finite`. Но так устроены ОБЕ части дроби: и `covered`, и
  `grand_total` считают только известные деньги — ровно как все прочие доли этой
  таблицы. K читается «доля известной цены договора». Гасить K на любой
  непросчитанной позиции нельзя: замеры §1.1 (62 %, 61 %, 58 %, 33 %) сняты на
  живых сметах, где такие строки есть, и правило обнулило бы их все.
- Нефинитных значений (`NaN`, `±Infinity`) в `node.total` быть не может — VIEW
  `0010` отсекает их в `rows_not_finite` фильтром годности. Отдельная защита
  здесь была бы мёртвым кодом; проверка диапазона выше поймала бы и её случай.
- **Арифметика охвата — в `localcontext(_RATE_CONTEXT)`** (Global Constraint
  16), и это касается ДВУХ мест: накопления `covered` и самого вызова
  `_share_pct(covered, grand_total)`. `_share_pct` — существующая функция, она
  делит в ambient-контексте, и менять её нельзя: она считает `share_pct` каждой
  строки паспорта, а те обязаны остаться посимвольно теми же (`AGENTS.md` §10).
  Значит явный контекст ставится на СТОРОНЕ ВЫЗОВА:
  `with localcontext(_RATE_CONTEXT): covered = ...; k = _share_pct(covered, grand_total)`.
  Тест на это — прогон `-k money_share` под `decimal.localcontext()` с
  включённым трапом `Inexact` в ambient-контексте: без явного контекста деление
  упало бы исключением, с ним проходит.
- **Читатель может сложить колонку сам** — с точностью до округления показа. K
  равен сумме видимых значений «Доля» по статьям набора со ставкой; побитового
  равенства тут не будет и не требуется (доли печатаются через
  `formatSharePercent`, а K считается при `prec = 100`). Смысл строки охвата в
  этом и состоит: числитель, которого на листе не видно, проверить нечем.
- **Число ставок на экране в охват не входит** — оно зависит от того, что
  читатель раскрыл (§2.8).
- **Что остаётся наблюдением, а не границей охвата.** Расхождение «сумма
  строки-раздела ≠ сумма её позиций» на охват больше не влияет: в дроби его
  теперь нет с обеих сторон. Но оно влияет на доверие к САМОЙ СТАВКЕ — её
  числитель по-прежнему `total_cost_total` носителя (Global Constraint 5), и
  если раздел в файле не сходится со своими позициями, ставка считается по
  одному числу, а «Итого, ₽» рядом показывает другое. Задача 9 замеряет это
  расхождение на стенде и записывает в devlog. Это не техдолг охвата: правило
  охвата закрыто, замеряется свойство исходных файлов.

- [ ] **Step 1: Написать падающие тесты охвата**

```python
def test_articles_total_counts_the_non_overlapping_set_of_this_estimate(
    db_session, factories
):
    """DoD 23: корень без носителя и две названные статьи под ним → M = 2.
    Не число строк дерева и не размер справочника."""
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    for code in ("6.1", "6.2"):
        _chapter(factories, proposal, category_id=_category(db_session, code).id,
                 smr_article_raw=code, unit_id=m2,
                 suggested_quantity=Decimal("10.00"), total_cost_total=Decimal("1000.00"))
    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert passport["rate_coverage"]["articles_total"] == 2


def test_the_coverage_set_is_independent_of_how_many_rates_the_screen_shows(
    db_session, factories
):
    """DoD 5: на фикстуре вложенных кодов ставок ДВЕ, а статей в наборе ОДНА."""
    ...  # та же фикстура, что у DoD 3 (6 и 6.1, ставки 100 и 150)
    # assert len([n for n in passport["categories"] if n["rate_state"] == "rate"]) == 2
    # assert passport["rate_coverage"]["articles_total"] == 1
    # assert passport["rate_coverage"]["articles_with_rate"] == 1


def test_money_share_never_exceeds_a_hundred_on_nested_codes(db_session, factories):
    """DoD 24. Фикстура: раздел с кодом 6 (носитель 1 000,00 / 10,00 м²), внутри
    него раздел с кодом 6.1 (носитель 600,00 / 6,00 м²); позиции под 6 напрямую —
    400,00, позиции под 6.1 — 600,00. Итог паспорта (из позиций) = 1 000,00.

    Набор = {6.1}, её `node.total` = 600,00 → K = 60. Снятие «считать по всем
    статьям с носителем» берёт и 6, и 6.1: `node.total` шестой — итог всего её
    поддерева, то есть 1 000,00, плюс 600,00 у 6.1 → 160 %. Деньги вложенной
    статьи учтены дважды — на первой редакции спеки так и вышло, 110,7 %.
    """
    ...
    # assert Decimal(passport["rate_coverage"]["money_share"]) == Decimal("60")


def test_money_share_does_not_move_when_the_display_target_changes(
    db_session, factories
):
    """DoD 22: числитель и знаменатель приведены одинаково — доля инвариантна.
    Это и есть проверка того, что ось ОДНА (§2.7).
    """
    ...
    # before = ... ; estimate.vat_rate_target = Decimal("22") ; after = ...
    # assert Decimal(after["rate_coverage"]["money_share"]) == Decimal(before[...])


def test_money_share_is_null_when_the_share_leaves_the_zero_to_hundred_range(
    db_session, factories
):
    """DoD 32. Антицепь держит границу только при неотрицательных суммах, а
    `CHECK`-а на знак `position_items.total_cost_total` в схеме нет.

    Фикстура: статья 6.1 в наборе (носитель 600,00 / 6,00 м², позиции 600,00) и
    ВТОРАЯ статья 7 вне набора, у которой позиция несёт ОТРИЦАТЕЛЬНУЮ сумму
    -400,00. Итог паспорта = 600,00 - 400,00 = 200,00; покрыто 600,00. Отношение
    300 % — за диапазоном.

    Ожидание — `None`, а не 300 и не срезанные 100: выход за диапазон означает
    либо отрицательные суммы, либо ошибку построения набора, и печатать по нему
    процент нельзя. Знак суммы фикстура задаёт прямо — это единственный способ
    воспроизвести случай, схема его не запрещает.
    """
    ...
    # assert passport["rate_coverage"]["money_share"] is None
    # assert passport["rate_coverage"]["money_share_state"] == "out_of_range"
    # assert passport["rate_coverage"]["articles_with_rate"] == 1   # набор цел


def test_a_negative_denominator_is_out_of_range_before_the_division(
    db_session, factories
):
    """DoD 32, вторая фикстура — случай, который проверка ДИАПАЗОНА не ловит.

    ДВЕ позиции, обе с отрицательной суммой: -50,00 под статьёй 6.1 (у неё есть
    носитель, статья попадает в набор и получает ставку) и -50,00
    нераспределённая, вне набора. Тогда `covered = -50,00`, а итог паспорта
    считает обе — `grand_total = -100,00`, потому что знаменатель включает
    «Нераспределённое» (докстрока `_share_pct`).

    Отношение -50 / -100 * 100 = ровно 50 % — формально допустимый процент по
    бессмысленным числам. Знаки сократились при делении, поэтому проверка
    РЕЗУЛЬТАТА его пропускает: знак знаменателя обязан смотреться ДО деления
    (проверка 3, а не 4). Вторая позиция здесь не декорация — без неё
    `covered = grand_total` и отношение было бы 100 %, то есть тоже внутри
    диапазона, но по совпадению, а не по механизму.

    Ожидание — `out_of_range`. Фикстура задаёт знак прямо: схема его не
    запрещает (`CHECK`-а на `total_cost_total` нет), а иначе случай недостижим.
    """
    ...
    # assert passport["rate_coverage"]["money_share"] is None
    # assert passport["rate_coverage"]["money_share_state"] == "out_of_range"


def test_a_partial_article_total_does_not_kill_the_money_share(db_session, factories):
    """DoD 33. Внутри статьи со ставкой одна позиция БЕЗ суммы
    (`total_cost_total = None`): итог статьи частичный, `rows_priced < rows`.

    K обязан остаться числом. Только известные деньги считают ОБЕ части дроби —
    ровно как все прочие доли паспорта, — поэтому доля остаётся согласованной, а
    не становится ложной. Обратное решение (гасить K на любой непросчитанной
    позиции) обнулило бы замеры §1.1 на живых сметах, где такие строки есть.
    """
    ...
    # assert passport["rate_coverage"]["money_share"] is not None
    # assert passport["rate_coverage"]["money_share_state"] == "partial"


def test_a_non_empty_set_without_a_single_rate_is_a_real_zero(db_session, factories):
    """DoD 25, первая фикстура: статьи названы, но ни одна не дала ставки (объёма
    в смете нет), а суммы паспорта известны все.

    Ожидание — `money_share = 0` и состояние `complete`: «покрыто 0 %» правда, и
    её надо напечатать. Прежняя редакция плана отдавала здесь `None`.

    Фикстура доказывает доменное правило «ноль — не отсутствие охвата», но НЕ
    воспроизводит два договора стенда с нулём (§1.1): у них состояние зависит от
    `positions_rows_priced`/`positions_rows`, при неполных суммах ноль придёт как
    `partial`. Счётчики замеряет задача 9, шаг 1 — до замера привязывать фикстуру
    к стенду нельзя.
    """
    proposal = _proposal(factories)
    m2 = _unit_id(db_session, "M2")
    _chapter(factories, proposal, category_id=_category(db_session, "6.1").id,
             smr_article_raw="6.1", unit_id=m2,
             suggested_quantity=None, total_cost_total=Decimal("1000.00"))
    _position(factories, proposal, total_cost_total=Decimal("1000.00"))
    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert passport["rate_coverage"] == {
        "articles_with_rate": 0, "articles_total": 1,
        "money_share": Decimal("0"), "money_share_state": "complete",
    }


def test_an_empty_set_is_no_articles_not_a_zero(db_session, factories):
    """DoD 25, вторая фикстура: смета не называет ни одной статьи. Мерить нечего —
    состояние `no_articles`, доля `None`. «0 %» здесь было бы утверждением о
    покрытии, которого никто не считал."""
    proposal = _proposal(factories)
    _position(factories, proposal)
    passport = get_project_passport(db_session, proposal.lot.estimate.contract_id)
    assert passport["rate_coverage"] == {
        "articles_with_rate": 0, "articles_total": 0,
        "money_share": None, "money_share_state": "no_articles",
    }


def test_the_state_enum_carries_exactly_the_five_contract_values():
    """DoD 34: перечень §2.8 и контракт §2.10 — один список."""
    assert {state.value for state in MoneyShareState} == {
        "complete", "partial", "no_articles", "total_unavailable", "out_of_range",
    }
```

> Четыре теста оставлены утверждениями с числами: сборка фикстур повторяет
> задачу 3, а числа (2, 1, 60, инвариантность) обязательны.

- [ ] **Step 2: Прогнать — падает**

Run: `cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration/test_passport_article_rates.py -k coverage -v`
Expected: FAIL, `KeyError: 'rate_coverage'`.

- [ ] **Step 3: Реализовать `_rate_coverage` и включить в ответ**

На обоих путях `get_project_passport` (со сметой и без). Пункт 20 списка правил
докстроки дополнить значением.

Порядок внутри функции: набор → `covered` под `localcontext(_RATE_CONTEXT)` →
`_share_pct` в том же контексте → **проверка диапазона последней**: если доля не
`None` и вышла из `0..100`, вернуть `None`. Проверка стоит на результате, а не на
сканировании входа, — тогда она ловит и причины, не перечисленные в правилах,
включая дефект самого набора. Докстрока обязана назвать, ПОЧЕМУ проверка есть при
доказанной антицепи: доказательство держится на неотрицательности сумм, а
`CHECK`-а на знак в схеме нет.

- [ ] **Step 4: Прогнать — зелено**

Run: `cd backend && env -u DATABASE_URL TEST_DATABASE_URL="postgresql+psycopg://postgres@localhost:5459/gca_test" uv run pytest tests/integration -k "passport" -v`
Expected: PASS — и новый набор, и прежний `test_project_passport_api.py`.

- [ ] **Step 5: Снятие защиты — DoD 24**

Заменить набор на «все статьи с носителем» (убрать условие «нет
потомка-статьи с носителем»). Прогнать `-k money_share_never_exceeds`.
Expected: FAIL — `None != Decimal('60')`: числитель стал 1 600,00 при
знаменателе 1 000,00, доля вышла за 100 %, и проверка диапазона отдала `None`.
Именно так две защиты и складываются — набор считает верно, диапазон не даёт
напечатать ложный процент, когда набор посчитан неверно. Вернуть файл.

Чтобы увидеть само число 160 (а не отказ), в том же снятии временно снять и
проверку диапазона — тогда `Decimal('160') != Decimal('60')`. Текст обоих
падений — в devlog.

- [ ] **Step 6: Снятие защиты — DoD 32, две мутации**

Каждая снимает СВОЮ проверку, и каждую ловит своя фикстура — в этом и смысл двух.

1. Убрать проверку диапазона (проверка 4), оставив проверку знака. Прогнать
   `-k money_share_is_null_when_the_share_leaves`. Expected: FAIL —
   `Decimal('300') != None`: лист напечатал бы «покрыто 300 % цены договора».
2. Убрать проверку знака знаменателя (проверка 3), оставив проверку диапазона.
   Прогнать `-k negative_denominator_is_out_of_range`. Expected: FAIL —
   `Decimal('50') != None`: отношение двух отрицательных попало в диапазон, и
   лист напечатал бы правдоподобные «покрыто 50 %». Первая мутация этот тест
   ронять не должна: если он краснеет и от неё, значит две проверки склеены в
   одну и по отдельности ни одна не работает.

Вернуть после каждой (правило «Как снимать защиту»).

- [ ] **Step 7: Коммит**

```bash
git add backend/crud/project_passport.py backend/tests/integration/test_passport_article_rates.py
git commit -m "feat(passport-volumes): охват — неперекрывающийся набор статей и доля денег"
```

---

## Task 6: Три колонки в таблице статей

**Files:**
- Modify: `frontend/src/types/domain.ts`
- Modify: `frontend/src/test/fixtures.ts`
- Modify: `frontend/src/pages/passport/ProjectPassportPage.test.tsx` (только
  полный литерал категории около строки 1589 — логику тестов не менять)
- Modify: `frontend/src/pages/passport/CategoryTable.tsx`
- Create: `frontend/src/pages/passport/PassportRates.test.tsx`

**Interfaces:**
- Consumes: контракт задач 4–5 (`unit`, `volume`, `unit_rate`, `rate_state`,
  `rate_note`, `rate_coverage`); `MoneyCell`, `Badge`, `Table*` (существуют).
- Produces (**заводится этой задачей**):
  - `type RateState = "no_carrier" | "additional_works" | "amount_missing" |
    "unit_missing" | "unit_conflict" | "unit_not_scalable" | "volume_missing" |
    "volume_nonpositive" | "volume_inconsistent" | "rate"`
  - `type RateNote = "overshoot" | "mixed_units" | "unverifiable"`
  - `type MoneyShareState = "complete" | "partial" | "no_articles" |
    "total_unavailable" | "out_of_range"`
  - `interface ProjectPassportRateCoverage { articles_with_rate: number;
    articles_total: number; money_share: Decimal | null;
    money_share_state: MoneyShareState }`
  - `const RATE_STATE_LABEL` — карта состояния в подпись пилюли, объявленная
    `satisfies Record<RateState, string | null>`, чтобы новое состояние роняло
    `tsc`, а не появлялось на экране пустой клеткой.

**Подписи пилюль** — дословно из §2.5 и макета; текст менять нельзя:

| `rate_state` | Что в клетке «Ставка, ₽/ед.» |
|---|---|
| `rate` | число (`MoneyCell` с `maxFractionDigits={2}`, `currency="₽/ед."`) |
| `no_carrier` | пусто, пилюли НЕТ (DoD 6) |
| `additional_works` | «дополнительные работы» |
| `amount_missing` | «сумма не прочитана» |
| `unit_missing` | «единица не прочитана» |
| `unit_conflict` | «единицы строк не совпадают» |
| `unit_not_scalable` | «не нормируется» |
| `volume_missing` | «объём в смете не указан» |
| `volume_nonpositive` | «объём не годится» |
| `volume_inconsistent` + `rate_note = "mixed_units"` | «объём смешан» |
| `volume_inconsistent` + `rate_note = "overshoot"` | «объём не сходится» |
| `volume_inconsistent` + `rate_note = "unverifiable"` | «сходимость не проверить» |

Третья подпись — решение У3 варианта «б». Карта подписей `volume_inconsistent`
объявляется `satisfies Record<RateNote, string>` по той же причине, что
`RATE_STATE_LABEL`: четвёртое значение `rate_note` должно ронять `tsc`, а не
показываться пустой пилюлей. При выборе варианта «а» строка уходит вместе с
третьим значением типа.

Оформление пилюль — `Badge variant="outline"` (примитив shadcn, Global
Constraint 13). Макет различает `pill` и `pill warn`: `warn` — там, где данные
дозаполнимы либо испорчены (`amount_missing`, `unit_missing`, `unit_conflict`,
`volume_missing`, `volume_nonpositive`, `volume_inconsistent`); нейтральный —
там, где ставки не будет никогда по природе данных (`additional_works`,
`unit_not_scalable`). Различие выражать токенами темы
(`border-warning-border bg-warning-soft text-warning-text` — те же, что уже
несёт строка «Нераспределённое»), не литеральными цветами.

**Раскладка.** Три колонки встают СПРАВА, после «₽/м²» (порядок макета).
Таблица становится восьмиколоночной. Три новые ячейки нужны в ПЯТИ местах
`CategoryTable.tsx`: строка статьи, строка допработ, служебная строка
собственных денег, строка «Нераспределённое», строка «Итого по договору» — в
четырёх последних это прочерки (макет, строка итога). Плюс `colSpan={5}` →
`colSpan={8}` у строки панели разноса.

Ячейка «Ед.» — `unit ?? "—"`. Ячейка «Объём» — `<MoneyCell value={volume}
currency="" />`: `formatDecimalMoney` с пустой валютой даёт ровно форму макета
(«19 545,81»), а `maxFractionDigits` НЕ передаётся — объём приходит из файла, он
не вычислен (докстрока `MoneyCell` это прямо разграничивает).

- [ ] **Step 1: Дополнить типы и фикстуры, убедиться, что `tsc` красный по делу**

Добавить поля в `ProjectPassportCategory` и `rate_coverage` в `ProjectPassport`.
Run: `just typecheck-frontend`
Expected: FAIL — 12 узлов `sampleProjectPassport.categories`, литерал около
строки 1589 в `ProjectPassportPage.test.tsx` и (если найдётся) литерал в
`handlers.ts` не несут новых полей. Это ожидаемая красная фаза: она и есть
список мест, которые надо дополнить.

- [ ] **Step 2: Заполнить фикстуры так, чтобы состояния были разными**

В `sampleProjectPassport` состояния расставить содержательно, а не одним
значением: хотя бы один узел `rate` с объёмом и ставкой, один `volume_missing` с
непустым `unit`, один `unit_not_scalable`, один `additional_works` (у узла с
`extras`), остальные `no_carrier`. `rate_coverage` — согласованный с ними
(`articles_total`, `articles_with_rate`, `money_share` как десятичная строка).
Числа сложить руками и написать арифметику комментарием — так устроена вся
остальная фикстура (`amount: "4700000.00"` с разложением слагаемых).

Run: `just typecheck-frontend`
Expected: PASS.

- [ ] **Step 3: Написать падающие тесты трёх колонок**

`frontend/src/pages/passport/PassportRates.test.tsx` — новый файл; `renderPassport`,
`PROJECT_PASSPORT_URL`, `withPassport` объявить локально копией с
`ProjectPassportPage.test.tsx` (тот файл своих помощников не экспортирует).

Тесты (каждый — одно утверждение о разметке):

1. шапка несёт три новые подписи дословно: «Ед.», «Объём», «Ставка, ₽/ед.»;
2. узел `rate` показывает единицу, объём и ставку; ставка — два знака после
   запятой, точное значение остаётся в `title` (как уже проверено для «₽/м²»);
3. узел `volume_missing` показывает единицу, прочерк в объёме и пилюлю «объём в
   смете не указан»;
4. узел `no_carrier` не несёт ни одной пилюли и ни одного текста состояния —
   `queryByText` по всем ОДИННАДЦАТИ подписям возвращает `null` в его строке
   (девять состояний, кроме `rate` и `no_carrier`, плюс три подписи
   `volume_inconsistent` вместо одной). Перечень брать из карт подписей, а не
   переписывать в тесте списком: разошедшийся список молча ослабил бы проверку;
5. `unit_not_scalable` → «не нормируется»; `additional_works` → «дополнительные
   работы»; это ОТДЕЛЬНЫЕ утверждения, потому что за ними разные действия (§2.5);
6. `volume_inconsistent` со всеми ТРЕМЯ значениями `rate_note`:
   `"mixed_units"` → «объём смешан», `"overshoot"` → «объём не сходится»,
   `"unverifiable"` → «сходимость не проверить» (параметризованный тест, три
   случая). Три отдельных случая, а не два: одна подпись на две причины — это и
   есть дефект, от которого У3 уходит;
7. строка допработ, служебная строка, «Нераспределённое» и «Итого по договору»
   несут по три ячейки-прочерка — проверять числом `td` в строке (`8`), иначе
   съехавшая колонка не заметна;
8. строка панели разноса перекрывает всю ширину: `colSpan === 8`.

Каждый тест адресуется через существующее соглашение `data-testid`
(`row-cat-<code>`, `amount-cat-<code>`, …) плюс новые
`unit-cat-<code>`, `volume-cat-<code>`, `rate-cat-<code>` — **заводятся этой
задачей**.

Run: `just test-frontend-file src/pages/passport/PassportRates.test.tsx`
Expected: FAIL — колонок в разметке нет.

- [ ] **Step 4: Реализовать колонки**

Правки в `CategoryTable.tsx` по раскладке выше. Карту `RATE_STATE_LABEL`
объявить рядом с `formatSharePct` и снабдить комментарием: почему подписи
дословные (они печатаются и уходят в банк), почему `no_carrier` даёт `null`
(клетка пуста БЕЗ пометки — смета этой статьи как статьи не называет), почему
карта `satisfies Record<RateState, ...>` (новое состояние обязано ронять `tsc`).

Run: `just test-frontend-file src/pages/passport/PassportRates.test.tsx`
Expected: PASS.

- [ ] **Step 5: Убедиться, что прежние тесты паспорта не тронуты**

Run: `just test-frontend-file src/pages/passport`
Expected: PASS. `ProjectPassportPage.test.tsx`, `UnallocatedPanel.test.tsx`,
`VatRateDialog.test.tsx` не правились ни в одном утверждении — только литерал
категории дополнен полями.

- [ ] **Step 6: Коммит**

```bash
git add frontend/src/types/domain.ts frontend/src/test/fixtures.ts frontend/src/pages/passport/CategoryTable.tsx frontend/src/pages/passport/PassportRates.test.tsx frontend/src/pages/passport/ProjectPassportPage.test.tsx
git commit -m "feat(passport-volumes): три колонки — единица, объём, ставка — в таблице статей"
```

---

## Task 7: Строка охвата под таблицей

**Files:**
- Modify: `frontend/src/pages/passport/CategoryTable.tsx`
- Test: `frontend/src/pages/passport/PassportRates.test.tsx`

**Interfaces:**
- Consumes: `passport.rate_coverage` (задача 5), `formatSharePercent`
  (существует).
- Produces: `data-testid="rate-coverage"` — **заводится этой задачей**.

**Текст** (§2.8, макет строка 370):

Подпись выбирается по `money_share_state` — **пять ветвей**, по одной на
состояние (§2.8, DoD 34). Ветвиться по `money_share === null` НЕЛЬЗЯ: под `null`
живут три разных факта, и одна подпись на них была бы ложной в двух случаях из
трёх. `K = formatSharePercent(money_share)`, `А = articlesWord(...)`.

| `money_share_state` | Текст |
|---|---|
| `complete` | «Покрыто {K} цены договора — ставка есть у {N} {А} из {M} в неперекрывающемся наборе.» |
| `partial` | «**По известным суммам** покрыто {K} цены договора — ставка есть у {N} {А} из {M} в неперекрывающемся наборе.» |
| `total_unavailable` | «Ставка есть у {N} {А} из {M} в неперекрывающемся наборе; доля цены договора не определена — итог паспорта неизвестен.» |
| `out_of_range` | «Ставка есть у {N} {А} из {M} в неперекрывающемся наборе; доля цены договора не определена — суммы сметы требуют проверки.» |
| `no_articles` | «В смете нет названных статей классификатора — охват не определён.» |

Три различия, каждое из которых обязательно:

- `partial` против `complete` — оговорка «по известным суммам». Без неё лист
  утверждает «долю цены договора», а посчитана доля ИЗВЕСТНОЙ цены: часть строк
  сметы без сумм (DoD 33).
- `out_of_range` против `total_unavailable` — «суммы требуют проверки», а НЕ «итог
  неизвестен». Итог как раз известен; за диапазон доля вышла из-за сумм сметы
  (DoD 32). Прежняя редакция плана печатала здесь «итог паспорта неизвестен» —
  ложь на печатном листе.
- `no_articles` против всех — про N и M говорить нечего, оба нуля. «Ставка есть
  у 0 статей из 0» — не факт, а артефакт формы.

Карта подписей объявляется `satisfies Record<MoneyShareState, string>`: шестое
состояние обязано ронять `tsc`, а не печататься пустой строкой.
- Согласование: локальный помощник `articlesWord(n)` — `n % 10 === 1 && n % 100
  !== 11 ? "статьи" : "статей"`. Без него документ, уходящий в банк, напечатает
  «у 1 статей».
- **Печатается** (`data-print="hide"` НЕ ставить): без этой строки печатный лист
  читается как полный свод расценок, а он не полный (§2.8). Стоит ПОД таблицей,
  рядом с печатной сноской о ручных решениях, и раскрытию дерева не подчинена.
- Число ставок, видимых на экране, в строку **не выносится** (§2.8).

- [ ] **Step 1: Написать падающие тесты**

1. строка присутствует при непустом охвате и несёт K, N и M;
1а. **DoD 32, 33, 34:** пять состояний — пять подписей, параметризованным
   тестом по карте. Проверять три различия отдельными утверждениями: `partial`
   несёт «по известным суммам», `out_of_range` несёт «суммы сметы требуют
   проверки» и НЕ несёт «итог паспорта неизвестен», `no_articles` не несёт ни N,
   ни M. Снятия обеих подписей — шаг 3 этой задачи.
2. **DoD 25, два случая отдельными тестами** — их склейка и была дефектом
   прежней редакции:
   - `articles_total: 1`, `articles_with_rate: 0`, `money_share: 0`,
     состояние `complete` → строка ПЕЧАТАЕТ «покрыто 0,00 %» и называет M = 1.
     Набор непуст, ставок нет, ноль настоящий. К двум договорам стенда этот
     случай НЕ привязан: там состояние решают счётчики полноты (задача 9);
   - `articles_total: 0`, `money_share: null`, состояние `no_articles` → строка
     печатает подпись про отсутствие названных статей и НЕ называет ни N, ни M;
3. при трёх состояниях с `money_share: null` строка НЕ печатает процента —
   `queryByText(/%/)` внутри неё возвращает `null`. Проверять по состоянию, а не
   по `money_share === null`; и это НЕ противоречит пункту 2: там ноль печатается
   именно потому, что состояние `complete`, а доля посчитана;
4. согласование числа: при `articles_with_rate: 1` — «у 1 статьи», при `2` — «у
   2 статей»;
5. строка не несёт `data-print="hide"` (проверять атрибут узла) — печатное
   обещание §2.8;
6. число раскрытых ставок на экране в строку не попадает: раскрыть узел с
   ставкой и убедиться, что текст строки посимвольно тот же.

Run: `just test-frontend-file src/pages/passport/PassportRates.test.tsx`
Expected: FAIL — узла `rate-coverage` нет.

- [ ] **Step 2: Реализовать**

Run: та же команда. Expected: PASS.

- [ ] **Step 3: Снятие защиты — подписи состояний (DoD 32, 33)**

Две мутации карты подписей, по одному прогону на каждую. Обе — ровно то, что
делала прежняя редакция плана, поэтому снятие показывает цену пятого состояния, а
не выдуманный дефект.

1. Свести `out_of_range` к тексту `total_unavailable` («итог паспорта
   неизвестен»). Прогнать `-t "out_of_range"`. Expected: FAIL — лист утверждает,
   что итог неизвестен, тогда как итог известен, а проверки требуют суммы сметы.
2. Свести `partial` к тексту `complete` (убрать «по известным суммам»). Прогнать
   `-t "partial"`. Expected: FAIL — доля ИЗВЕСТНОЙ цены названа долей цены
   договора.

Вернуть после каждой (правило «Как снимать защиту»).

- [ ] **Step 4: Коммит**

```bash
git add frontend/src/pages/passport/CategoryTable.tsx frontend/src/pages/passport/PassportRates.test.tsx
git commit -m "feat(passport-volumes): строка охвата под таблицей статей"
```

---

## Task 8: Печатная раскладка — замер в браузере и решение о жертве

**Files:**
- Modify: `frontend/src/index.css` (блок `@media print`, строки 487–491)
- Create: `docs/devlog/2026-08-24-passport-volumes.md`

**Interfaces:** ничего не производит для других задач; потребляет разметку
задач 6–7.

**Почему замером, а не тестом.** `@media print` в jsdom не наблюдаем
(`AGENTS.md` §11, DoD 26). Vitest здесь бесполезен, и попытка «проверить печать
тестом» была бы ложным зелёным.

**Как мерить** (память проекта: MCP Playwright не работает, Chrome водится
скриптом; пакет `playwright` с `channel: "chrome"`):

1. поднять фронт и бэкенд, открыть `/contracts/<id>/passport` договора с полным
   разносом на стенде `gca_dev`;
2. раскрыть дерево (печатный слой сам ничего не разворачивает — `AGENTS.md`
   §7.4), чтобы на лист попали второй и третий уровни;
3. `page.emulateMedia({ media: "print" })`, затем `page.pdf({ format: "A4" })`;
4. измерить переполнение по правому краю: `scrollWidth - clientWidth` у
   `[data-slot="table-container"]` и у `table`; проверить, что `thead`
   повторяется на каждом листе PDF и что ни одна `tr` не разорвана.

- [ ] **Step 1: Расширить печатные ширины с пяти колонок до восьми**

Комментарий над блоком сегодня говорит «Пять колонок новой таблицы (Код ·
Статья · Итого · Доля · ₽/м²) — не семь колонок фазы 6». Его надо ПЕРЕПИСАТЬ, а
не оставить: устаревшее обоснование читается как живой контракт. Новая
раскладка — восемь колонок; «Статья классификатора» по-прежнему получает
`width: auto`, остальные проценты подобрать так, чтобы сумма не превышала
печатную область (190 мм при `margin: 10mm`).

- [ ] **Step 2: Замерить**

Прогнать замер по схеме выше. Записать в
`docs/devlog/2026-08-24-passport-volumes.md`: переполнение в px до и после
правки ширин, число листов, повторение шапки, разрывы строк.

- [ ] **Step 3: Решение о жертве колонки «₽/м² объекта»**

Если замер показал переполнение по правому краю **после** правки ширин — §2.9
даёт готовое решение: жертвуется «₽/м² объекта» (она выводима из суммы и
площади, а ставка не выводима ни из чего на листе). Реализация: новый признак
`data-print="drop"` на заголовке и ячейках этой колонки плюс правило
`[data-print="sheet"] [data-print="drop"] { display: none !important; }`.
Признак завести с комментарием в том же блоке докстроки `index.css`, где
перечислены `sheet`/`hide`/`row`/`clamp`: `drop` — «часть документа, которой
жертвуют при нехватке ширины (спека §2.9)», и это НЕ `hide` (та метка означает
«документом не является»). После правки — замерить снова.

Если переполнения нет — признак **не заводить**: правило, которого никто не
читает, тот самый мёртвый код с устаревшим обоснованием, за который блок CSS уже
один раз чистили. Записать в devlog, что жертва не понадобилась, и на каком
замере это установлено.

- [ ] **Step 4: Прогнать фронтовый набор целиком**

Run: `just test-frontend`
Expected: PASS. Правка CSS тестами не наблюдаема — прогон нужен, чтобы убедиться,
что `data-print="drop"` (если он появился) не сломал утверждения о
`data-print`-атрибутах в прежних тестах.

- [ ] **Step 5: Коммит**

```bash
git add frontend/src/index.css docs/devlog/2026-08-24-passport-volumes.md
git commit -m "feat(passport-volumes): печатная раскладка восьми колонок, замер в браузере"
```

---

## Task 9: Прогон на стенде, ревизия рамки и AGENTS.md, PR

**Files:**
- Modify: `docs/devlog/2026-08-24-passport-volumes.md`
- Modify: `docs/phase7-frame.md`
- Modify: `AGENTS.md`

**Interfaces:** потребляет всё; ничего не производит для кода.

- [ ] **Step 1: Прогон на живом стенде — два договора (DoD 29)**

Один договор с заполненными объёмами, один без (§1.1 замер 1 называет, что
таких на стенде по несколько). Запросы к БД гонять через `python + psycopg`
(`backend/.venv`, `PYTHONIOENCODING=utf-8`) — кириллица в аргументах `psql -c`
искажается. **Перед выводами о данных сверить `import_jobs.file_sha256` с
файлом на диске:** в `samples/` лежат разные версии одной оферты с одинаковыми
суммами и разными объёмами разделов.

Записать в devlog по каждому договору: `articles_total`,
`articles_with_rate`, `money_share`, `money_share_state` и счётчики
`positions_rows` / `positions_rows_priced`. Счётчики нужны затем, что от них
зависит состояние у нулевого охвата: при полных суммах ноль придёт как
`complete`, при неполных — как `partial`. Ни план, ни спека не берутся угадать,
какой из двух случаев на стенде; это и есть замер. И **сверить с §1.1 замером 2** (Объект А —
54 из 121 и 62 %; Объект В — 61 %; Объект Б — 58 %; Объект Г — 33 %; Объект Д —
0 %; Е и Ж — 0 %). Расхождение — находка, а не «допустимая погрешность»: либо
правило разошлось со замером, либо замер снят по другой версии файла. Объекты
называть «Объект А…Ж».

- [ ] **Step 2: Замерить расхождение «раздел против своих позиций»**

По каждому договору стенда сравнить `total_cost_total` строки-носителя со суммой
позиций той же статьи. Записать в devlog, где расходится и насколько.

Охвата это не касается: числитель и знаменатель `money_share` берутся одним
путём (`v_category_totals`), и K ≤ 100 % доказан набором-антицепью (задача 5).
Замер нужен про СТАВКУ: её числитель — сумма носителя, и там, где раздел не
сходится со своими позициями, ставка посчитана по одному числу, а «Итого, ₽»
в той же строке показывает другое. Если расхождение массовое — это находка для
следующей фичи, и её место в `docs/TECH_DEBT.md`; если единичное — наблюдение в
devlog. Заранее не угадывать.

Заодно проверить читаемость охвата: сумма видимых «Доля» по статьям набора со
ставкой обязана совпасть с `money_share` **с точностью до округления показа**
(доли печатаются через `formatSharePercent`, K считается при `prec = 100` —
побитового равенства тут нет и не требуется). Расхождение больше чем на
округление означает, что набор считается не по тому дереву.

- [ ] **Step 3: Ревизия рамки фазы 7 (DoD 30)**

`docs/phase7-frame.md`, «Вне скоупа v1» пункт 2. Новый текст пункта дан в спеке
§2.11 **дословно** — перенести его, не переписывая. Ревизия не молчаливая:
пункт помечен «не пересматривать молча», и спека §2.11 это основание называет.
Пункт 1 того же раздела ревизии НЕ подлежит.

- [ ] **Step 4: Правка `AGENTS.md` §7.4 (DoD 30)**

Пункт 4 раздела «7. Экраны фронтенда» (строка 434). Добавить: две новые
величины в составе таблицы статей (единица с объёмом и ставка ₽/ед.), строку
охвата под таблицей, оговорку, что ставка выражена в той же ставке показа, что и
суммы паспорта, и запрет переносить ставку с этого экрана в норматив без деления
на базу НДС (§2.7: норматив объявлен ценой без НДС, `AGENTS.md` §4). Ссылку на
спеку дать так же, как даны ссылки на спеку разноса и спеку сравнения. Больше
инвариантов не задевать: схемы, миграции и `v_category_totals` фича не касается
(§2.12).

- [ ] **Step 5: Дописать devlog**

Состав по `AGENTS.md` §9.2: что сделано, замеры (печать из задачи 8, стенд из
шага 1, расхождение разделов из шага 2), отступления от плана, найденные грабли.

Плюс результаты **тринадцати** шагов снятия защиты с текстом падений: снятие,
объявленное пройденным без текста падения, ничего не доказывает.

| | Снятие | Где |
|---|---|---|
| из спеки | DoD 1, 4, 7, 12, 17, 21, 24 | задачи 1–4, шаги названы в таблице соответствия |
| из спеки | DoD 32, сервер — проверки диапазона и знака знаменателя (две мутации) | задача 5, шаг 6 |
| из спеки | DoD 32 и 33, подписи состояний (две мутации карты) | задача 7, шаг 3 |
| сверх спеки | полнота классификации единиц (DoD 11) | задача 1, шаг 7 |
| сверх спеки | граница допуска (DoD 20) | задача 2, шаг 7 |
| сверх спеки | явный десятичный контекст | задача 1, шаг 8а |
| сверх спеки | оговорка известной единицы в предикате смешения | задача 2, шаг 7а |

Счёт: спека объявляет снятие у **девяти** пунктов DoD (1, 4, 7, 12, 17, 21, 24,
32, 33 — пересчитано по тексту §5, а не по шапке коммита), и план закрывает их
девятью шагами: у DoD 32 снимаются обе половины, серверная и подпись, а подписи
32 и 33 снимаются одним шагом задачи 7. План добавил **четыре** снятия сверх
спеки, потому что четыре механизма — полнота классификации, накопление допуска,
явный десятичный контекст, различение «единица неизвестна» и «единица
отличается» — иначе фиксировались бы совпадением чисел, а не поведением. Итого
тринадцать шагов.

Если фича выстрадала правило работы — завести инсайт в `docs/insights/` и строку
в указателе `AGENTS.md` §12 (§9.1).

- [ ] **Step 6: `just ci` (DoD 31)**

Run: `just ci`
Expected: все шаги зелёные — `uv lock --check`, ruff, `alembic check` без новой
миграции, pytest (8 воркёров), eslint, tsc, vitest. Составную команду не
собирать: шаги идут по отдельности намеренно (`AGENTS.md` §11).

- [ ] **Step 7: Коммит и PR**

```bash
git add docs/devlog/2026-08-24-passport-volumes.md docs/phase7-frame.md AGENTS.md
git commit -m "docs(passport-volumes): devlog, ревизия рамки фазы 7, состав паспорта в AGENTS.md"
git push -u origin feat/passport-volumes
```

PR один, со ссылками на спеку и на этот план в описании (`AGENTS.md` §9.3).
Абсолютных сумм, названий объектов и реквизитов контрагентов в описании PR и в
сообщениях коммитов нет.

---

## Соответствие DoD спеки задачам

Все 34 пункта §5 спеки (31 исходный плюс 32–34, добавленные ревизией гейта 3).
Пустых клеток быть не должно — если пункт не закрыт, это дыра плана, а не
«мелочь».

| DoD | Где закрыт | Снятие защиты |
|---|---|---|
| 1 | Задача 3, шаг 1 (`rate_comes_from_the_carrier_row`) | Задача 3, шаг 5 |
| 2 | Задача 1 (`three_rows_of_one_article`), задача 3 (`three_corpus_rows`) | — |
| 3 | Задача 3 (`nested_inside_another_article_keeps_its_own_rate`) | — |
| 4 | Задача 3 (`nested_inside_the_same_article_is_excluded`) | Задача 3, шаг 6 |
| 5 | Задача 5 (`coverage_set_is_independent_of_how_many_rates`) | — |
| 6 | Задача 2 (`no_carrier_when_the_estimate_never_names`), задача 4 (`root_without_a_carrier`) | — |
| 7 | Задача 2 (`additional_works_wins_over_no_carrier`, `volume_missing_is_a_different_state`) | Задача 2, шаг 5 |
| 8 | Задача 1 (`missing_unit_is_not_the_same_as_conflicting`) | — |
| 9 | Задача 1 (там же), задача 2 (`unit_conflict_is_its_own_state`) | — |
| 10 | Задача 2 (`priority_gives_exactly_one_state`, `piece_with_a_real_quantity`) | — |
| 11 | Задача 1 (`two_unit_groups_disjoint_and_cover_the_seed` + тест по таблице) | Задача 1, шаг 7 — **сверх** снятий спеки |
| 12 | Задача 1 (`one_row_without_an_amount_kills_the_whole_article`) | Задача 1, шаг 8 |
| 13 | Задача 1 (`one_row_without_a_volume_kills_the_whole_volume`) | — |
| 14 | Задача 1 и задача 2 (параметризация `0` / `-1`) | — |
| 15 | Задача 2 (`priority_gives_exactly_one_state_on_a_collision`) | — |
| 16 | Задача 2 (`state_enum_carries_exactly_the_ten_contract_values`), задача 4 (`every_contract_state_is_reachable`) | — |
| 17 | Задача 2 (`mixed_units_do_not_depend_on_the_numbers`, два прогона) | Задача 2, шаг 6 |
| 18 | Задача 2 (`overshoot_kills_the_node_and_leaves_the_child_alone`) | — |
| 19 | Задача 2 (`undershoot_does_not_kill_the_rate`) | — |
| 20 | Задача 2 (`tolerance_boundary_is_inclusive_on_the_undershoot_side`) | Задача 2, шаг 7 — **сверх** снятий спеки |
| 21 | Задача 1 (`amount_is_restated_row_by_row`), задача 4 (`rate_follows_the_display_axis`) | Задача 4, шаг 7 |
| 22 | Задача 5 (`money_share_does_not_move_when_the_display_target_changes`) | — |
| 23 | Задача 5 (`articles_total_counts_the_non_overlapping_set`) | — |
| 24 | Задача 5 (`money_share_never_exceeds_a_hundred_on_nested_codes`) | Задача 5, шаг 5 |
| 32 | Задача 5 (`money_share_is_null_when_the_share_leaves_the_zero_to_hundred_range`), задача 7 (подпись) | Задача 5, шаг 6 (сервер); задача 7, шаг 3 (подпись) |
| 33 | Задача 5 (`a_partial_article_total_does_not_kill_the_money_share`), задача 7 (подпись) | Задача 7, шаг 3 (подпись) |
| 34 | Задача 5 (`state_enum_carries_exactly_the_five_contract_values`), задача 7 (параметризация по пяти подписям) | — |
| 25 | Задача 5 (`non_empty_set_without_a_single_rate_is_a_real_zero`, `empty_set_is_no_articles_not_a_zero`), задача 7 (разметка) | — |
| 26 | Задача 8, шаги 2–3 (замер в браузере, вывод в devlog) | — |
| 27 | Задача 4 (`route_and_the_response_keys_of_the_feature`) | — |
| 28 | Задача 4, шаг 5 (`just db-test-check`) | — |
| 29 | Задача 9, шаги 1–2 | — |
| 30 | Задача 9, шаги 3–4 | — |
| 31 | Задача 9, шаг 6 (`just ci`) | — |

**Снятий защиты девять: восемь из спеки** (DoD 1, 4, 7, 11, 12, 17, 21, 24 —
ровно те, где §5 говорит «Снятие:» либо «роняет тест») **плюс одно сверх** (DoD
20, формула допуска). Каждое ломает МЕХАНИЗМ — носитель объёма, подъём по
предкам, оговорку состояния, полноту классификации, NULL-семантику слагаемого,
предикат смешения, приведение к оси показа, границу неперекрывающегося набора,
накопление допуска — и проверяется **прогоном теста**, а не повторным чтением
того же выражения, которое только что правилось. Снятие без записанного текста
падения не считается пройденным (задача 9, шаг 5).

---

## Порядок и точки ревью

Задачи идут строго по номерам: 1 → 2 (чистое правило), 3 → 4 → 5 (сервер),
6 → 7 (экран), 8 (печать), 9 (стенд и документы). Ревью после КАЖДОЙ задачи
обязательно и приёмкой оркестратора не заменяется.

Параллелить нечего: задача 2 стоит на типах задачи 1, задача 4 — на запросе
задачи 3, задача 6 — на контракте задач 4–5, задача 8 — на разметке задач 6–7.
