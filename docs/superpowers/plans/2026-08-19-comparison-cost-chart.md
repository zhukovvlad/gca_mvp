# Диаграмма стоимости договоров и фильтр по классу объекта — план реализации

**Ветка:** `feat/comparison-cost-chart` (уже открыта; в ней лежат макет и спека).
**Спека:** [`2026-08-19-comparison-cost-chart-design.md`](../specs/2026-08-19-comparison-cost-chart-design.md)
— гейт 2 закрыт 20.08.2026.
**Макет:** [`2026-08-19-comparison-cost-chart-mockup.html`](../specs/2026-08-19-comparison-cost-chart-mockup.html).

План не пересказывает спеку. Он говорит, в каком порядке и в каких файлах, и
называет то, чего спека не фиксирует.

---

## Архитектура

Фича трогает три слоя, и на каждом ровно одну точку решений.

### Выборка: класс сужает, остальные фильтры — вторая форма

Единственное место — `resolve_selection`. Сегодня оно делает два дела: отвергает
двусмысленность и выбирает форму. Становится три: отвергает двусмысленность
(теперь и для `ids` с `q`/`object_id`/`contractor_id` — коррекция §2.6), выбирает
форму, **и затем сужает результат по классу** — одинаково для обеих форм.

Сужение стоит ПОСЛЕ выбора формы, а не внутри каждой ветви: иначе появятся две
копии одного правила, а DoD 2 требует, чтобы обе формы давали одинаковый ответ
на одинаковом множестве.

Фильтр по классу выражается через `apply_contract_filters`, а не отдельным
`WHERE`: список договоров и сравнение обязаны фильтровать одним кодом (это
причина, по которой функция и вынесена — см. её докстроку).

### Медиана в ставке показа: одна функция, одно место

`_compute_median` не меняется вовсе — она считает по `net_per_sqm`, и это
правильно. Меняется только `_median_dict`: он получает ставку показа и режим и
добавляет `shown_per_sqm` по правилу присутствия из §2.8.

Умножение — существующий `net_to_gross`, а не новая арифметика: медиана в ставке
показа обязана считаться той же функцией, которой считаются сами ячейки
(`comparison.py:1403`), иначе последний разряд разойдётся незаметно.

Правило присутствия живёт **в одном месте** — в `_median_dict`. Ни клиент, ни
`_row_cells` о нём не знают: клиент рисует линию, если поле пришло и не `null`
(плюс проверяет единицу диаграммы, которую поле не описывает).

### Номинал: одно чтение, умножение вынесено из чтения

При сосчитанном приведении нужны обе величины. Роллапы читаются **один раз** без
приведения, и приведение накладывается на копию.

Два чтения — не паранойя, а названный риск: комментарий в самом
`load_rollups` (`comparison.py:834`) объясняет, что `resolve_inflation` и этот
запрос читают сметы порознь и в READ COMMITTED каждый оператор берёт свой
снимок **даже внутри одной транзакции**. Значит два вызова `load_rollups` в
одном ответе могут разойтись по составу смет.

Поэтому умножение **извлекается** из чтения в `apply_adjustment` (новое имя,
заводится задачей 6): `load_rollups` продолжает принимать `adjustment` и просто
делегирует, чтобы место умножения осталось одно, а `build_comparison` получает
возможность применить коэффициенты к уже прочитанному. Громкий `RuntimeError` на
смету без коэффициента переезжает вместе с умножением — он и есть защита от
молчаливого смешивания приведённых с неприведёнными.

Дешевле, чем кажется: номинал нужен только «Итого по договору», то есть один
дополнительный вызов `_row_cells(..., row=None)`, а не проход по 253 узлам.
Строки дерева номинала не получают (§2.8).

### Диаграмма: recharts под shadcn, как кольцо паспорта

Никакой своей SVG-геометрии. `components/ui/chart.tsx` уже есть, recharts в
зависимостях, прецедент — `StructureRing` (кольцо структуры стоимости): секторы
там считает `<Pie>`, а не мы. Здесь столбцы считает `<BarChart>`.

Что рисуется поверх штатных примитивов recharts и почему именно так — в задаче 11.

---

## Global Constraints

1. **Ни одной денежной операции в JS.** Медиана в ставке показа, номинал,
   отклонения — всё приходит готовым. `Number()` над деньгами запрещён
   (AGENTS.md §3); исключение только для геометрии recharts, как у
   `StructureRing`.
2. **`_compute_median` не трогать.** Множество сопоставимых и правило трёх
   остаются как есть.
3. **Порядок операций не менять:** нетто → инфляция → ставка показа.
4. **Инфляционные ключи и `nominal` — только при сосчитанном приведении.** Без
   приведения ответ не несёт ни одного нового инфляционного ключа (DoD 13).
5. **`available_rate_classes` — наоборот, всегда.** Чипы нужны на первом
   открытии.
6. **Таблица сравнения не меняется:** ни колонок, ни строк, ни колонки
   «Медиана» (её нет и не будет, DoD 22в).
7. **Экран списка договоров не трогаем.** Он продолжает передавать одиночный
   `rate_class_id`.
8. **`just ci` перед каждым пушем** (§9.3). Правки только в `docs/` от него
   освобождены — все задачи ниже, кроме 1 и 13, освобождены НЕ будут.
9. **Один коммит на задачу**, сообщение по-русски, ссылка на пункт DoD.

---

## Файловая карта

| Файл | Что | Задача |
|---|---|---|
| `backend/crud/contracts.py` | `apply_contract_filters` — класс списком | 2 |
| `backend/crud/comparison.py` | `resolve_selection`, `_median_dict`, `build_comparison`, facet | 3–6 |
| `backend/routers/analytics.py` | `Selection` вместо списка (4), затем разбор адреса (7) | 4, 7 |
| `backend/routers/reports.py` | то же, один контракт выборки на оба маршрута | 4, 7 |
| `backend/tests/comparison_fixtures.py` | фикстура на общий класс | 4 |
| `backend/tests/integration/test_comparison_selection.py` | сужение и коррекция 400 | 3 |
| `backend/tests/integration/test_comparison_facet.py` | **новый** — facet | 4 |
| `backend/tests/integration/test_comparison_medians.py` | **новый** — `shown_per_sqm` | 5 |
| `backend/tests/integration/test_comparison_inflation.py` | номинал в `totals` | 6 |
| `backend/tests/integration/test_comparison_baseline.py` | эталон и его дельта | 1 |
| `frontend/src/types/domain.ts` | три новых поля | 8 |
| `frontend/src/services/api/analytics.ts` | `ComparisonParams` | 8 |
| `frontend/src/pages/compare/ComparePage.tsx` | ставка в URL, чипы, врезка диаграммы | 9, 10, 11 |
| `frontend/src/pages/compare/ContractCostChart.tsx` | **новый** — диаграмма | 11 |
| `frontend/src/pages/compare/costChartData.ts` | **новый** — чистые функции данных и оси | 11 |
| `frontend/src/pages/compare/costChartData.test.ts` | **новый** — геометрия числами | 11 |
| `frontend/src/pages/compare/ContractCostChart.test.tsx` | **новый** — линии, корзина, прокрутка | 11 |
| `frontend/src/pages/compare/ComparePage.test.tsx` | поведение экрана | 12 |
| `frontend/src/test/fixtures.ts`, `handlers.ts` | новые поля в фикстурах | 8 |
| `AGENTS.md` | §7 п. 7, §11 | 13 |
| `docs/devlog/2026-08-19-comparison-cost-chart.md` | **новый** | 14 |

---

## Инвентарь символов (гейт 3: проверено `grep`-ом 2026-08-20)

**Существуют, использовать как есть.** `resolve_selection`, `build_comparison`,
`_median_dict`, `_MedianResult`, `_compute_median`, `_row_cells`, `_cell_entry`,
`_bucket_cell_dict`, `BucketCell`, `load_rollups`, `_load_columns`,
`_split_buckets`, `_rate_options_from_rollups`, `resolve_inflation`,
`_column_inflation`, `InflationPlan`, `_mode_caption` — все в
`backend/crud/comparison.py`. `apply_contract_filters`, `filtered_contract_ids`,
`_contracts_select`, `CONTRACT_LIST_ORDER` — `backend/crud/contracts.py`.
`net_to_gross` — `backend/money/vat.py`. `parse_ids_param` —
`backend/crud/comparison.py`. `get_comparison` —
`backend/routers/analytics.py:147`, вызов `resolve_selection` — `:195`.
`comparison_report` — `backend/routers/reports.py:135`, вызов — `:173`.
`ComparisonParams.rate_class_id` — `src/types/domain.ts:1283`, УЖЕ есть как
`string`.

**Фикстуры и тесты, существуют.** `baseline_selection`, `contract_with`,
`contract_with_area`, `contract_with_disagreeing_rates`, `series_with_years`,
`contract_with_dates`, `contract_with_amendment_dates`, `make_proposal`,
`seed_chapter_with_positions` — `backend/tests/comparison_fixtures.py`.
`RateClassFactory` — `backend/tests/factories.py:82`. Файлы
`test_comparison_selection.py`, `test_comparison_baseline.py`,
`test_comparison_buckets.py`, `test_comparison_inflation.py`,
`test_comparison_excel.py` — есть.

**Фронтенд, существует.** `Comparison`, `ComparisonParams`, `ComparisonColumn`,
`ComparisonCell`, `ComparisonBucketCell`, `ComparisonMedian`, `ComparisonRow`,
`ComparisonBucket`, `ComparisonVatMode`, `ComparisonIncompleteReason` —
`src/types/domain.ts`. `comparisonApi.get` — `src/services/api/analytics.ts:43`.
`useComparison`, `useComparisonReport` — `src/services/queries.ts:818,905`.
`VAT_MODE_LABELS`, `BUCKET_LABELS`, `REASON_LABELS`, `updateSingleRate`,
`updateVatMode` — `src/pages/compare/ComparePage.tsx`. `MoneyCell` —
`src/components/ui-domain/MoneyCell.tsx:30`. `formatSharePercent`,
`formatDecimalMoney`, `roundDecimalPercent` — `src/lib/format.ts`.
`compareDecimalStrings`, `addDecimalStrings` — `src/lib/decimal.ts`.
`components/ui/chart.tsx`, `StructureRing` — есть.

**НЕ существует, заводится этой фичей** (в тестах ниже такие имена не
изображаются готовым кодом, а называются утверждением):

| Имя | Где | Задача |
|---|---|---|
| `available_rate_classes` (ключ ответа) | `build_comparison` | 4 |
| `rate_class_id` у колонки | `_load_columns` | 4 |
| `shown_per_sqm` у медианы | `_median_dict` | 5 |
| `nominal` у ячейки корзины и у медианы | `_bucket_cell_dict`, `_median_dict` | 6 |
| `apply_adjustment` | `crud/comparison.py` | 6 |
| `Selection` (dataclass) | `crud/comparison.py` | 4 |
| `contracts_sharing_a_rate_class` (фикстура) | `comparison_fixtures.py` | 4 |
| `test_comparison_facet.py`, `test_comparison_medians.py` | тесты | 4, 5 |
| `ComparisonRateClassFacet` (тип) | `domain.ts` | 8 |
| `_totals_median_dict` | `crud/comparison.py` | 5 |
| `ContractCostChart` | `pages/compare/` | 11 |
| `buildCostChartBars`, `costChartAxisTop` | `pages/compare/costChartData.ts` | 11 |
| эффект записи `single_rate` в адрес | `ComparePage.tsx` | 9 |

---

## Решения плана, которых спека не фиксирует

1. **`rate_class_id` разбирается в роутере, а не в CRUD.** Строка `2,3` — это
   формат URL; `resolve_selection` принимает `Sequence[int]`. Разбор строки в
   CRUD означал бы, что слой домена знает про запятые.
2. **Пустой `rate_class_id=` (параметр есть, значение пустое) — 400, а не «все
   классы».** Пустое значение в адресе появляется от кода, а не от человека, и
   молча трактовать его как «фильтра нет» значит прятать чужую ошибку.
3. **Facet считается ОДНИМ запросом вместе с колонками.** `_load_columns` уже
   выбирает `RateClass.title` join-ом; facet берётся из того же результата до
   сужения — второй запрос к `rate_classes` не нужен.
4. **Номинальные роллапы получаются из приведённых на одном чтении**: читаем без
   приведения, приведение накладываем копией. Порядок именно такой, потому что
   `load_rollups(adjustment=...)` умножает при чтении.
5. **Диаграмма — отдельный компонент, а не блок внутри `ComparePage`.**
   `ComparePage` уже больше тысячи строк; врезка ещё двухсот сделала бы его
   нечитаемым. Компонент получает готовый `Comparison` и корзину, своего
   запроса не делает.
6. **Прокрутка полотна — тот же контейнер, что у таблицы**
   (`overflow-x-auto`), а не своя реализация; жёлоб оси — вне контейнера.
7. **Порог «сколько столбцов много» не вводится** (§2.5): нет числа — нет и
   ветки, которая однажды соврёт.

---

## Task 1: эталон дофичевого ответа и правило приёмки его диффа

**Файл:** `backend/tests/integration/test_comparison_baseline.py` (существует).

Эталон уже есть — снят фичей инфляции ДО её правок, и его докстрока прямо
требует, чтобы файл-эталон не ехал вместе с проверяемым кодом.

Эта фича добавляет в ответ поля **всегда** (`available_rate_classes`,
`rate_class_id` колонки), значит эталон изменится законно. Задача — сделать это
изменение проверяемым, а не молчаливым.

- [x] Прогнать `test_comparison_baseline.py` ДО единой правки кода: должен быть
      зелёным. Красный здесь означает, что ветка уже что-то сломала.
      **Зелёный: 3 passed** на `HEAD` = `fb30ec8`, дерево чистое.
- [x] Записать в devlog хеш эталона до правок.
      [`docs/devlog/2026-08-19-comparison-cost-chart.md`](../../devlog/2026-08-19-comparison-cost-chart.md) §1.
- [ ] Обновлять эталон **только** в задачах 4 и 6, и в каждом коммите приводить
      дифф эталона целиком: он обязан состоять ровно из объявленной дельты.
      Любая лишняя строка — дефект, а не «эталон устарел».

---

## Task 2: `apply_contract_filters` — класс списком

**Файл:** `backend/crud/contracts.py`.

```python
def apply_contract_filters(
    stmt, *, q=None, object_id=None, contractor_id=None,
    rate_class_id: int | Sequence[int] | None = None,
):
    ...
    if rate_class_id is not None:
        classes = [rate_class_id] if isinstance(rate_class_id, int) else list(rate_class_id)
        if not classes:
            raise DomainError(400, "Фильтр по классу объекта задан пустым списком.")
        stmt = stmt.where(Contract.rate_class_id.in_(classes))
```

Одиночное значение продолжает работать — иначе сломается список договоров,
который эта фича не трогает (Global Constraint 7).

- [x] Импорт `Sequence` в `crud/contracts.py` — его там сейчас нет
      (`DomainError` уже есть, 13 употреблений).
- [x] Правка функции.
- [x] Тест: одиночный `rate_class_id` даёт то же множество, что до правки.
      Плюс существующий `test_list_contracts_filters_by_rate_class` не тронут —
      он и есть сторож Global Constraint 7.
- [x] Тест: список из двух классов даёт объединение.
- [x] Тест: пустой список — 400 (решение плана 2).
- [x] `just ci`.

Тесты — в новом `tests/integration/test_contract_filters.py` (план файла не
называл; довод — devlog §3.1).

**DoD:** 5 (частично), 1.

---

## Task 3: `resolve_selection` — сужение обеих форм и коррекция 400

**Файлы:** `backend/crud/comparison.py`,
`backend/tests/integration/test_comparison_selection.py`.

Порядок внутри функции: сначала отказы, потом форма, потом сужение.

```python
def resolve_selection(db, *, ids=None, use_filter=False, q=None,
                      object_id=None, contractor_id=None, rate_class_id=None):
    other_filters = {"q": q, "object_id": object_id, "contractor_id": contractor_id}
    named = [name for name, value in other_filters.items() if value not in (None, "")]
    if ids and (use_filter or named):
        raise DomainError(400, ...)          # коррекция §2.6 + прежний случай
    ...
```

**Внимание на существующий тест.** `test_both_forms_at_once_is_a_request_error`
вызывает `resolve_selection(db, ids=[a.id], use_filter=True)` — то есть
проверяет ТОЛЬКО `all=1` с `ids`. Случай `ids` с `q` без `all=1` не покрыт
никогда, отсюда и молчаливое игнорирование.

- [ ] Отказ 400 для `ids` вместе с `q`; отдельным тестом.
- [ ] То же для `object_id`; отдельным тестом.
- [ ] То же для `contractor_id`; отдельным тестом.
- [ ] Сужение по классу в форме `ids`.
- [ ] Сужение по классу в форме `all=1`.
- [ ] Тест равенства форм на одинаковом множестве при многозначном классе
      (DoD 2) — поверх существующего
      `test_filter_form_selects_the_same_ids_as_the_contracts_list`.
- [ ] Тест: класс, которого нет в выборке, даёт ПУСТУЮ выборку, не ошибку
      (DoD 4).
- [ ] Тест: `ids` с `all=1` по-прежнему 400 — не потерять существующее.
- [ ] `just ci`.

**DoD:** 1, 2, 3, 4.

---

## Task 4: facet `available_rate_classes` и `rate_class_id` колонки

**Файлы:** `backend/crud/comparison.py`, `backend/routers/analytics.py`,
`backend/routers/reports.py`, `backend/tests/comparison_fixtures.py`,
`backend/tests/integration/test_comparison_facet.py` (новый),
`backend/tests/integration/test_comparison_selection.py`.

Facet считается по выборке ДО сужения классами и ПОСЛЕ остальных фильтров —
значит `resolve_selection` обязан вернуть ДВА множества: полное и суженное. Это
меняет её сигнатуру, и лучше так, чем считать выборку дважды.

```python
@dataclass(frozen=True)
class Selection:
    contract_ids: list[int]        # после сужения классами
    facet_ids: list[int]           # до сужения классами, после остальных фильтров
```

**Смена типа возврата ломает ОБА роутера в этой же задаче, и они правятся
здесь.** Сегодня и `analytics.py:195`, и `reports.py:173` делают
`contract_ids = resolve_selection(...)` и передают результат прямо в
`build_comparison`. Оставить их на следующую задачу нельзя: промежуточный
`just ci` упадёт, а Global Constraint 8 требует зелёного `ci` на каждом коммите.

**Сигнатура `build_comparison` при этом НЕ меняется.** Замер: прямых вызовов
`build_comparison(` — **61 в 9 файлах** (`grep`, 20.08.2026): оба роутера, пять
файлов интеграционных тестов, эталон и **два генератора макетов** — включая
генератор макета закрытой фичи инфляции. Последнее решает вопрос само: генераторы
лежат в `docs/`, `just ci` их не касается, и переход на новый тип сломал бы их
МОЛЧА — узнали бы при следующей пересборке макета, месяцы спустя.

Поэтому:

```python
def build_comparison(db, contract_ids, *, facet_ids=None, vat_mode, …):
    ...
```

`Selection` остаётся типом возврата `resolve_selection` и **распаковывается в
роутерах**. Это не поддержка двух типов и не совместимая обёртка: у
`build_comparison` одна форма вызова, а новый параметр — обычный именованный.

`facet_ids=None` означает «сужения не было», и facet считается по самим
`contract_ids`. Умолчание не «нет facet»: facet обязан присутствовать всегда
(Global Constraint 5), а когда никто не сужал, множество до сужения и есть
выборка. Все 61 существующий вызов после этого продолжают работать и получают
верный facet.

`available_rate_classes` строится по `facet_ids` из того же join-а, что и
колонки (решение плана 3), порядок — по `title` (§2.7).

**Фикстура `contracts_sharing_a_rate_class`** — заводится этой задачей.
Существующий `baseline_selection` даёт каждому договору СВОЙ класс
(`comparison_fixtures.py:291`), поэтому `count` на нём всегда 1 и правило не
проверяется.

- [ ] `Selection` и правка `resolve_selection`.
- [ ] `build_comparison(…, facet_ids=None)` — сигнатура списка сохранена.
- [ ] `analytics.py:195` и `reports.py:173` распаковывают `Selection`.
- [ ] Существующие тесты `test_comparison_selection.py`, читающие возврат
      `resolve_selection` как список, переведены на `Selection` в этом же
      коммите. Вызовы `build_comparison` в них НЕ трогаются — сигнатура та же.
- [ ] Тест: `build_comparison` без `facet_ids` даёт facet по самой выборке.
- [ ] Фикстура на два договора одного класса и один другого.
- [ ] `available_rate_classes` в ответе; порядок по `title`.
- [ ] `rate_class_id` в колонке; значение — снимок из договора.
- [ ] Тест: facet присутствует БЕЗ сужения (DoD 7).
- [ ] Тест: при `rate_class_id=<один>` facet несёт ВСЕ классы выборки —
      негативный, снятие обязано ронять (DoD 8).
- [ ] Тест: facet после остальных фильтров — при `all=1&contractor_id=N` в нём
      только классы этого подрядчика (DoD 9).
- [ ] Тест: `count` равен числу колонок при сужении ровно до этого класса
      (DoD 10).
- [ ] Тест: переклассификация объекта не меняет `rate_class_id` колонки
      (DoD 12).
- [ ] Обновить эталон задачи 1, привести дифф целиком.
- [ ] `just ci`.

**DoD:** 7, 8, 9, 10, 11, 12.

---

## Task 5: медиана в ставке показа

**Файлы:** `backend/crud/comparison.py`,
`backend/tests/integration/test_comparison_medians.py` (новый).

**`_median_dict` НЕ трогаем.** Она вызывается из двух мест —
`comparison.py:2071` для `rows[].medians` и `:2105` для `totals_medians`, — и
правка внутри неё добавила бы `shown_per_sqm` в медианы всех 253 строк, чего
контракт спеки не вводит (§2.10 называет только `totals_medians`). Это тот же
довод, по которому номинал живёт только в «Итого».

Поэтому заводится ОТДЕЛЬНЫЙ сериализатор итоговой медианы. Отдельная функция, а
не флаг: флаг пришлось бы передавать через `_row_cells`, и однажды он приедет в
строки «за компанию».

```python
def _totals_median_dict(median, *, vat_mode, single_rate) -> dict:   # заводится здесь
    out = _median_dict(median)                      # общая часть — одна
    if vat_mode == VAT_MODE_NET:
        out["shown_per_sqm"] = median.value
    elif vat_mode == VAT_MODE_SINGLE:
        out["shown_per_sqm"] = (
            None if median.value is None else net_to_gross(median.value, single_rate))
    # VAT_MODE_OWN — ключа НЕТ вовсе
    return out
```

- [ ] `_totals_median_dict`; вызов только на `:2105`.
- [ ] Тест: `rows[].medians` ключа `shown_per_sqm` НЕ несут — негативный,
      снятие обязано ронять.
- [ ] Тест: при `net` `shown_per_sqm == value`.
- [ ] Тест: при `single` `shown_per_sqm == net_to_gross(value, rate)` —
      утверждением о числе: медиана нетто стенда 129 800 при ставке 20 % даёт
      155 760 (DoD 22а).
- [ ] Тест: при `own` ключа нет — негативный, `assert "shown_per_sqm" not in …`
      (DoD 22б).
- [ ] Тест: при менее чем трёх сопоставимых ключ ПРИСУТСТВУЕТ и равен `None` в
      `net`/`single` (DoD 24).
- [ ] `just ci`.

**DoD:** 22а, 22б, 24.

---

## Task 6: номинал в «Итого» и в медиане

**Файлы:** `backend/crud/comparison.py`,
`backend/tests/integration/test_comparison_inflation.py`.

```python
rollups = load_rollups(db, contract_ids)                    # НОМИНАЛ, одно чтение
adjusted = (apply_adjustment(rollups, plan.factor_by_estimate)   # заводится здесь
            if plan else rollups)
```

Номинальная ветвь считается тем же `_row_cells(..., row=None)` по номинальным
роллапам и укладывается в `nominal` внутри каждой корзины `totals`; медиана —
тем же порядком.

- [ ] `apply_adjustment` извлечён из `load_rollups`; `load_rollups(adjustment=…)`
      делегирует, место умножения одно.
- [ ] `RuntimeError` на смету без коэффициента сохранён и покрыт тестом —
      **снятие проверки обязано ронять тест** (защита от смешивания
      приведённых с неприведёнными).
- [ ] Разделение роллапов на номинальные и приведённые из одного чтения.
- [ ] `nominal` в ячейках `totals` по трём корзинам.
- [ ] `nominal` у `totals_medians` — с тем же правилом присутствия
      `shown_per_sqm`, что в задаче 5.
- [ ] Тест: без приведения ни одного ключа `nominal` — негативный (DoD 13).
- [ ] Тест: `nominal.shown_per_sqm × коэффициент == shown_per_sqm` там, где
      коэффициент колонки определён (DoD 14).
- [ ] Тест: при РАЗНЫХ коэффициентах ДГП и ДС номинал «Итого» верен —
      на `contract_with_amendment_dates` (DoD 15).
- [ ] Тест: `totals_medians[bucket].nominal` равна медиане ТОГО ЖЕ запроса без
      ряда — сравнением двух ответов, а не пересчётом (DoD 16).
- [ ] Тест: `rows` номинала не несут — утверждением об отсутствии ключа
      (DoD 17).
- [ ] Обновить эталон задачи 1: диффа быть НЕ должно (без приведения ответ не
      меняется).
- [ ] `just ci`.

**DoD:** 13, 14, 15, 16, 17.

---

## Task 7: параметры у обоих маршрутов

**Файлы:** `backend/routers/analytics.py`, `backend/routers/reports.py`,
`backend/tests/integration/test_comparison_api.py`,
`test_comparison_excel.py`.

Задача 4 уже перевела оба роутера на `Selection`; здесь остаётся **только
разбор адреса**.

`rate_class_id` становится строкой «через запятую» — как `ids`, и разбирается в
роутере (решение плана 1). Разбор — рядом с существующим
`crud.comparison.parse_ids_param`, тем же приёмом и с той же формой отказа:
второй способ разбирать список id в одном модуле разъедется.

- [ ] Хелпер разбора рядом с `parse_ids_param`, один на оба маршрута.
- [ ] Подключён в `get_comparison`.
- [ ] Подключён в `comparison_report`.
- [ ] Тест: `?ids=…&rate_class_id=2,3` — 200 и меньше колонок (DoD 1).
- [ ] Тест: одиночный `rate_class_id=2` — прежняя семантика (DoD 5).
- [ ] Тест: `?ids=…&q=…` — 400 (DoD 3), на уровне HTTP.
- [ ] Тест: лист XLSX на тех же параметрах — по той же выборке (DoD 6).
- [ ] `just ci`.

**DoD:** 1, 3, 5, 6.

---

## Task 8: фронт — типы, API, фикстуры

**Файлы:** `frontend/src/types/domain.ts`,
`src/services/api/analytics.ts`, `src/test/fixtures.ts`, `src/test/handlers.ts`.

```ts
export interface ComparisonRateClassFacet {
  id: number;
  title: string;
  count: number;
}
```

Плюс три поля: `ComparisonColumn.rate_class_id`,
`ComparisonMedian.shown_per_sqm?`, `nominal` у `ComparisonBucketCell` и
`ComparisonMedian`. Опциональность в типе — не украшение: она и есть правило
присутствия из §2.8, и `tsc` заставит клиента проверить поле перед отрисовкой.

- [ ] Типы.
- [ ] `ComparisonParams.rate_class_id` **уже существует** как `string`
      (`domain.ts:1283`) — поле не заводится, расширяется его СЕМАНТИКА до
      строки со списком; правка идёт в докстроку, а не в объявление.
- [ ] Фикстуры и MSW-обработчики с новыми полями.
- [ ] `just ci` (здесь важен `tsc`).

**DoD:** предпосылка для 18–34.

---

## Task 9: фронт — действующая ставка показа в адресе

**Файл:** `frontend/src/pages/compare/ComparePage.tsx`.

Эффект того же вида, что уже пишет `target_month` (`ComparePage.tsx:749`):
сервер вернул ставку, которой числа показаны ФАКТИЧЕСКИ, — клиент кладёт её в
адрес. Сегодня такого эффекта нет, есть только запись при ручном выборе
(`updateSingleRate`).

- [ ] Эффект записи `single_rate`.
- [ ] Тест: открытие `?vat_mode=single` без ставки дописывает её в адрес
      (DoD 33).
- [ ] Тест: сужение по классу не меняет действующую ставку — негативный,
      снятие эффекта обязано ронять (DoD 34).
- [ ] `just ci`.

**DoD:** 33, 34.

---

## Task 10: фронт — чипы классов

**Файл:** `ComparePage.tsx`.

Чипы строятся из `available_rate_classes`; выбранные — из `rate_class_id` в
адресе; при полном наборе параметр из адреса убирается.

- [ ] Группа чипов, порядок из ответа.
- [ ] Запись в адрес: id по возрастанию, при полном наборе параметра нет.
- [ ] Снятие последнего класса не срабатывает (DoD 30).
- [ ] Тест: чипы видны и после перезагрузки с сужением (DoD 8 на клиенте).
- [ ] `just ci`.

**DoD:** 30, 31.

---

## Task 11: фронт — диаграмма

**Файлы:** `frontend/src/pages/compare/ContractCostChart.tsx` (новый),
`frontend/src/pages/compare/costChartData.ts` (новый),
`frontend/src/pages/compare/costChartData.test.ts` (новый),
`frontend/src/pages/compare/ContractCostChart.test.tsx` (новый).

**Данные и верх оси считают ЧИСТЫЕ функции, вынесенные из компонента** —
`buildCostChartBars` и `costChartAxisTop` (оба заводятся здесь). Причина
прямая: DoD 19–21 и 28–28а требуют проверки утверждением о числе, а не глазами
по SVG. Через отрисованный `<BarChart>` эти пункты пришлось бы проверять
разбором путей, то есть глазами инструмента.

Оговорка к Global Constraint 1: чистые функции получают уже готовые decimal-
строки и **не считают деньги** — они выбирают, что показать, и переводят в
`number` только высоты и верх оси. Тот же единственный случай, что
`geometryValue` у `StructureRing`.

`<BarChart>` из recharts под `ChartContainer`. Поверх штатных примитивов:

- сплошной `<Bar>` — приведённое (или номинал, когда приведения нет);
- промежуток к номиналу — **range-bar**, то есть `<Bar>` со значением
  `[min(номинал, приведённое), max(номинал, приведённое)]`, поверх сплошного и
  со штрихованным `<pattern>`. **Не `stackId`:** стек начинается от верха
  предыдущего столбца, поэтому при РОСТЕ штриховка легла бы НАД приведённым
  значением, тогда как она обязана лежать между номиналом и приведённым.
  Range-bar даёт ровно нужный интервал в обе стороны одной формулой. Проверено:
  `recharts@3.8.1`, `types/cartesian/Bar.d.ts:22` — `value: number | [number,
  number]`;
- **совмещение полос по горизонтали задаётся явно.** Два `<Bar>` без общего
  `stackId` recharts кладёт РЯДОМ, разными группами, с зазором `barGap` — по
  умолчанию `4` (проверено: `types/chart/CartesianChart.d.ts:9`,
  `barCategoryGap` там же `"10%"`). Без правки штриховка встала бы сбоку от
  сплошного столбца. Механизм штатный: **одинаковый фиксированный `barSize={N}`
  у обеих полос и `barGap={-N}` у графика** — вторая полоса смещается на
  `N + (−N) = 0`, то есть ровно поверх первой; типы это допускают
  (`barGap?: number | string`, `util/types.d.ts:1291`). Своей геометрии и
  кастомного `shape` по-прежнему нет;
- риска номинала — `<ReferenceDot>`/`<ReferenceLine>` на столбец;
- медиана — `<ReferenceLine y={shown_per_sqm}>`, только при единице ₽/м² и
  ненулевом поле; вторая, точечная — номинальная медиана при приведении;
- подписи под столбцом (класс, коэффициент, отклонение) — вёрсткой под
  `<XAxis>`, не через `<Label>`: там три строки разного тона.

Геометрия столбцов — работа recharts (Global Constraint 1).

- [ ] `buildCostChartBars` и `costChartAxisTop` — чистые, без запросов.
- [ ] `costChartData.test.ts`: порядок столбцов (DoD 18); геометрия обоих знаков
      (DoD 19–21) — числами, включая случай, где промежуток меньше пикселя;
      верх оси вмещает столбцы, риски, призраки и обе линии (DoD 28); ось по
      применённому состоянию — 150 000 в номинале и 200 000 после приведения на
      выборке без «люкса» (DoD 28а).
- [ ] Компонент, порядок столбцов от старых к новым (DoD 18).
- [ ] Симметричное изображение поправки (DoD 19, 20, 21).
- [ ] Медиана по правилу §2.4 (DoD 22, 22а, 22в) и её отсутствие с объяснением
      (DoD 22г, 24).
- [ ] Единица диаграммы, переключатель у самой диаграммы (DoD 26).
- [ ] Следование корзине (DoD 27).
- [ ] Ось от нуля и по применённому состоянию (DoD 28, 28а).
- [ ] Прокрутка и минимальная ширина столбца (DoD 29).
- [ ] Договор без суммы занимает место с причиной (DoD 25).
- [ ] `ContractCostChart.test.tsx`: линия медианы есть в `net` и `single`, нет в
      `own` (DoD 22); нет на оси сумм при пришедшем поле (DoD 22г); две линии при
      приведении (DoD 23); объяснение вместо линии при менее чем трёх
      сопоставимых (DoD 24); следование корзине (DoD 27); прокрутка и
      минимальная ширина столбца (DoD 29).
- [ ] `ContractCostChart.test.tsx`: **`x` и `width` обеих полос совпадают** —
      иначе штриховка уезжает сбоку, и это единственная проверка, которая ловит
      потерянный `barGap`.
- [ ] `ContractCostChart.test.tsx`: договор без суммы остаётся в ряду, с причиной
      из `incomplete_reasons`, а не выпадает (DoD 25).
- [ ] `ContractCostChart.test.tsx`: на оси сумм плашка отклонения подписана
      «к медиане ₽/м²» — **негативный, снятие подписи обязано ронять тест**
      (DoD 26).
- [ ] `just ci`.

**DoD:** 18–29.

---

## Task 12: фронт — поведение экрана

**Файл:** `ComparePage.test.tsx`.

- [ ] Приведение по умолчанию выключено: без инфляционных параметров в адресе
      ответ не несёт ключей `nominal`, и экран показывает номинальные числа
      (DoD 32). Клиент `nominal` не «запрашивает» — поле появляется в ответе при
      применённом приведении.
- [ ] Таблица сравнения НЕ получает колонку «Медиана» — утверждением об
      отсутствии (DoD 22в, Global Constraint 6).
- [ ] Диаграмма не ломается на выборке без сумм.
- [ ] Диаграмма не ломается при менее чем трёх сопоставимых.
- [ ] `just ci`.

**DoD:** 32.

---

## Task 13: `AGENTS.md` → v6.11

- [ ] §7 п. 7: диаграмма итога и фильтр по классу объекта, оба выражены в URL.
- [ ] §11: грабли `Decimal.//` — усечение к нулю, «потолок» через `-(-a // b)`
      занижается.
- [ ] Врезка версии с обоснованием: ревизия §2.6 спеки сравнения и коррекция
      200 → 400.

---

## Task 14: прогон на стенде, devlog, PR

- [ ] Прогон всех состояний макета на живом стенде, включая цель раньше сметы.
- [ ] `docs/devlog/2026-08-19-comparison-cost-chart.md`: что сделано, замеры,
      отступления от плана, найденные грабли.
- [ ] Если фича выстрадала правило работы — инсайт и строка в §12.
- [ ] PR со ссылками на спеку и план.

---

## Соответствие DoD спеки → задача

| DoD | Задача | DoD | Задача |
|---|---|---|---|
| 1 | 2, 3, 7 | 18–21 | 11 |
| 2 | 3 | 22, 22а–22б, 22г | 5, 11 |
| 3 | 3, 7 | 23 | 11 |
| 4 | 3 | 24 | 5, 11 |
| 5 | 2, 7 | 25 | 11 |
| 6 | 7 | 26 | 11 |
| 7–12 | 4 | 27 | 11 |
| 13–17 | 6 | 28, 28а | 11 |
| | | 29 | 11 |
| | | 30, 31 | 10 |
| | | 22в, 32 | 12 |
| | | 33, 34 | 9 |

---

## Границы (в эту ветку не тащить)

- выбор статьи классификатора на диаграмме;
- диаграмма в XLSX;
- мультивыбор `object_id` и `contractor_id`;
- колонка «Медиана» в таблице;
- правки экрана списка договоров;
- порог числа столбцов.
