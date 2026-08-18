# Поправка на инфляцию в сравнении договоров — план реализации

> **Для исполнителя:** задачи идут по порядку, каждая замкнута и заканчивается
> коммитом. Шаги с чекбоксами (`- [ ]`) отмечаются по мере выполнения.
> Коммиты делает оркестратор (Opus), не исполнитель.

**Цель:** страница `/compare` и её Excel-выгрузка умеют привести суммы всех
договоров выборки к ценовому уровню одного выбранного месяца по именованному
годовому ряду индексов. По умолчанию поправка выключена, и тогда обе поверхности
отвечают как до фичи — посимвольно.

**Спека:** [docs/superpowers/specs/2026-08-18-inflation-adjustment-design.md](../specs/2026-08-18-inflation-adjustment-design.md)
— план аргументирует от неё, читать обе. Макет:
[2026-08-18-inflation-adjustment-mockup.html](../specs/2026-08-18-inflation-adjustment-mockup.html).

**Ветка:** `feat/inflation-adjustment` (спека и макет уже закоммичены, 13 коммитов
docs-only, HEAD `2c513bd`).

---

## Архитектура

Четыре слоя, и границы между ними — не оформление.

1. **Ядро формулы — `backend/money/inflation.py`.** Чистые функции над `Decimal`:
   отображение «год → показатель степени», требуемые годы пары дат, коэффициент
   канонической формой, умножение суммы. Ни базы, ни FastAPI, ни знания про
   сравнение. Рядом с `money/vat.py`, потому что это общий денежный механизм:
   вторая фича (матрица, нормативы) подхватит его без переделки (спека §2.5).
2. **Справочник рядов — `crud/inflation_series.py` + `routers/inflation_series.py`.**
   Своя таблица, свой роутер, `DELETE` нет нигде. Правка ряда — одна транзакция
   на всё окно.
3. **Приведение — внутри `crud/comparison.py`, до всякой агрегации.** Коэффициент
   разрешается **на смету** (спека §2.2) и применяется к нетто-суммам
   `DirectBranch.net` **до** `build_tree`. Из этого автоматически следует всё
   остальное: ₽/м², медиана, отклонения, режимы показа НДС, «Итого» как сумма
   раздельно приведённых корзин.
4. **Показ — экран и лист.** Оба читают ОДИН агрегат (`build_comparison`), как и
   до фичи; лист печатает то, что экран не показывает (`source` года), и это
   разделение, а не дубль (спека §2.12).

### Где именно умножается сумма, и почему там

```
_load_estimates ──┐
                  ├─► load_rollups(..., adjustment: Mapping[estimate_id, Decimal] | None)
_load_view_rows ──┘        │
                           ├─ frozen_branches: DirectBranch.net *= k(estimate)   ← ЕДИНСТВЕННОЕ место
                           └─ build_tree(...)  ← дерево строится уже из приведённых сумм
```

**Одна точка умножения.** Если приводить позже — в `cell_net`, `_grand_total_cell`,
`_own_mode_shown` и `_single_rollup_axis` пришлось бы протащить коэффициент в
четыре независимых сумматора, и каждый стал бы отдельным шансом забыть. Умножение
до `build_tree` даёт приведённое дерево, и весь слой 2–3 модуля работает без
единой правки.

**Порядок операций «нетто → инфляция → ставка показа» выполняется по
построению:** `net_to_gross` вызывается в `_build_bucket_cell`/`_own_mode_shown`
**после** роллапа, то есть уже над приведённой нетто-суммой.

**Счётчики строк и причины неполноты не трогаются.** Инфляция не делает ячейку
полнее или беднее: `rows`, `rows_priced`, `rows_vat_base_unknown`,
`incomplete_reasons` остаются как были, `net is None` остаётся `None`, ноль
остаётся нулём (`ZERO`). Нового кода причины не заводится (спека §2.9).

### Разрешение дат — отдельная функция, свои запросы

`resolve_inflation(db, contract_ids, *, series_id, target_month)` возвращает план
приведения либо бросает `DomainError` с кодом. Она НЕ встроена в
`_load_estimates` по двум причинам:

* покрытие проверяется по **объединению требуемых годов ВСЕЙ выборки** (спека
  §2.5) — то есть решение принимается до того, как хоть одна сумма умножена;
* `load_rollups` держит инвариант «ровно четыре запроса на любую выборку», и его
  стережёт тест `test_comparison_rollup.py::test_query_count_does_not_grow_with_selection`
  с жёстким `assert c3.total <= 4`. Запрос внутри `load_rollups` уронил бы его.

**Бюджет запросов `build_comparison` — точный, и он входит в тест абсолютным
числом, а не только «не растёт от размера выборки».** Сегодня их **пять**:
четыре в `load_rollups` плюс `_load_columns` ([comparison.py:1605](../../../backend/crud/comparison.py));
`_rate_options_from_rollups` — чистая функция над роллапами и в базу не ходит.
С приведением их **восемь**: те же пять плюс три у `resolve_inflation` — ряд по
`id` (он же даёт `404`), значения ряда, даты смет (`Estimate ⋈ Contract`).
Прежняя редакция плана называла инфляционный запрос «пятым» — это была ошибка
счёта, `_load_columns` в ней не учтён. Если реализация уложится в другое число —
правится тест **вместе с обоснованием в devlog**, а не молча.

**Два чтения смет обязаны видеть одно и то же множество, и это проверяется, а
не предполагается.** `resolve_inflation` читает сметы своим запросом, а
`load_rollups` — своим; в `READ COMMITTED` каждый оператор берёт свой снимок
даже внутри одной транзакции, поэтому между ними набор смет выборки в принципе
может разойтись. Тихое последствие — смета, которой нет в `factor_by_estimate`,
осталась бы **неприведённой и смешалась с приведёнными**. Поэтому `load_rollups`
при непустом `adjustment` обязан **громко упасть** `RuntimeError`-ом на смете без
коэффициента — той же формой, что уже стоит в `_resolve_cell`
([comparison.py:816](../../../backend/crud/comparison.py)) на нарушении
инварианта VIEW. Тест на это — с искусственно урезанным `adjustment`.

### Форма отказа

### Форма отказа

```
crud/… ── DomainError(422, message, code=…, context={…})
                   │
                   └─► routers/domain_errors.py: raise_domain_error(err) ─► HTTPException
                                    ▲                    ▲                    ▲
                          routers/analytics.py   routers/reports.py   routers/inflation_series.py
```

`DomainError` о HTTP не знает: замером установлено, что **FastAPI не импортируется
в `crud/` нигде** (спека §6). Транслятор — один на три роутера; `_raise` из
`analytics.py:35` и `reports.py:51` удаляются, вместо них импортируется общая
функция. При `code is None` поведение прежнее до символа — `detail` строкой.

---

## Global Constraints

- **Без выбранного приведения ответ не меняется НИ ОДНИМ КЛЮЧОМ.** Инфляционные
  поля появляются в ответе только когда приведение сосчитано; `"inflation": null`
  и `inflation_coefficient: null` в номинальном ответе — уже нарушение DoD 1.
  Стережёт golden-снимок задачи 1.
- **Деньги** — `Decimal` в Python, строки в JSON, `float` на входе отвергается;
  ответы через `responses.decimal_json:93`.
- **Точность приведения** — явный `localcontext(prec=34)` внутри
  `money/inflation.py`; округление только `money.vat.quantize_money` на слое
  показа. Канонической объявлена **форма показателей** (спека §2.5): годы по
  возрастанию, одна операция `**` на год, затем последовательное умножение.
  Второй формулы в коде не заводить.
- **Ось сравнения — всегда нетто**, в любом режиме показа и с любым приведением.
- **`DELETE` не заводится нигде** — ни для рядов, ни для значений (спека §2.10).
- **Миграция 0014 существующих данных не трогает**, справочник создаётся пустым.
- **`pytestmark = pytest.mark.integration`** обязателен каждому новому файлу в
  `backend/tests/integration/`.
- **Имена методов-валидаторов pydantic уникальны по всему дереву схемы** —
  одноимённые схлопываются молча (§11 `AGENTS.md`, замер Ф5 фазы 7). Валидатор
  `coefficient` назвать `_reject_float_coefficient`, а не `_reject_float`.
- **Схема запроса НЕ дублирует доменные проверки.** В pydantic живёт только
  форма (типы, обязательность, запрет `float`); «коэффициент больше нуля» и
  «источник непуст после `btrim`» проверяет домен — как это делает
  `create_rate_standard` ([crud/rate_standards.py:220](../../../backend/crud/rate_standards.py)).
  Причина не стилистическая: тест атомарности задачи 5 обязан дойти ДО мутации
  внутри CRUD, а схема, отвергнувшая тело раньше, сделала бы его вакуозным —
  снятие транзакции осталось бы зелёным. `gt=0` в схеме запрещён явно.
- **Метку времени нельзя проверять внутри одной транзакции.** Замер 2026-08-18 на
  `gca_test`: `now()` возвращает **одно и то же** значение до и после
  savepoint-commit'а, `clock_timestamp()` — разные. Фикстура `db_session` держит
  весь тест в одной внешней транзакции, поэтому «`updated_at` сдвинулся» там
  недоказуемо, а «не сдвинулся» — вакуозно зелено (§12, инсайт про ложные
  предпосылки). DoD 37 гоняется на `committing_db`/`committing_client`.
- **`SECRET_KEY` не короче 32 символов**, иначе `Settings` падает на сборке
  conftest, а не на тестах.
- **Фронтенд гоняется только из `frontend/`**; типы — `npx tsc -b --noEmit`
  (голый `tsc --noEmit` проверяет ноль файлов, §11). `prettier` не настроен.
- **Только shadcn/ui, компоненты УЖЕ стоят** — `dialog`, `input`, `label`,
  `checkbox`, `table`, `button`, `select`, `tabs`, `badge`. `npx shadcn add` не
  запускать.
- **Перед пушем `just ci`**, шаги по отдельности (конвейер с `| tail` вернёт
  чужой код возврата). Правки только в `docs/` от `just ci` освобождены.
- **Два одновременных прогона pytest сталкиваются** на `DROP SCHEMA` в фикстуре
  `db_engine`: если параллельно работает субагент — свою проверку гонять с `-n 1`.

**Команда бэкенд-тестов** (PowerShell, из `backend/`):

```
$env:PYTHONIOENCODING='utf-8'; $env:DATABASE_URL='postgresql+psycopg://postgres@localhost:5459/postgres'; $env:TEST_DATABASE_URL='postgresql+psycopg://postgres@localhost:5459/gca_test'; $env:SECRET_KEY='test-secret-key-for-ci-0123456789abcdef'; uv run pytest <путь> -v
```

---

## Файловая карта

| Файл | Что с ним |
|---|---|
| `backend/money/inflation.py` | **новый**: ядро формулы (задача 2) |
| `backend/alembic/versions/2026_08_18_0014-inflation_series.py` | **новый**: две таблицы (задача 3) |
| `backend/models.py` | **правка**: `InflationSeries`, `InflationIndexValue` |
| `backend/crud/common.py` | **правка**: `DomainError` получает `code`, `context` |
| `backend/routers/domain_errors.py` | **новый**: единая трансляция на три роутера |
| `backend/routers/analytics.py`, `backend/routers/reports.py` | **правка**: `_raise` → общий транслятор; два новых query-параметра |
| `backend/crud/inflation_series.py` | **новый**: CRUD рядов, `PATCH` одной транзакцией |
| `backend/routers/inflation_series.py` | **новый**: `/api/v1/inflation-series` |
| `backend/main.py` | **правка**: регистрация роутера |
| `backend/crud/comparison.py` | **правка**: `resolve_inflation`, `load_rollups(adjustment=…)`, метаданные и подпись |
| `backend/services/excel_comparison.py` | **правка**: печать ряда, месяца и годов |
| `backend/tests/integration/test_comparison_baseline.py` | **новый**: golden дофичевого ответа (задача 1) |
| `backend/tests/unit/test_money_inflation.py` | **новый**: ядро формулы |
| `backend/tests/integration/test_inflation_series_api.py` | **новый**: ряды, права, атомарность |
| `backend/tests/integration/test_comparison_inflation.py` | **новый**: приведение, отказы, метаданные |
| `backend/tests/integration/test_schema_constraints.py` | **правка**: CHECK/UNIQUE новых таблиц |
| `backend/tests/comparison_fixtures.py` | **правка**: фикстуры с датами и рядом |
| `frontend/src/lib/inflation.ts` | **новый**: расшифровка коэффициента в уровень |
| `frontend/src/components/inflation/InflationSeriesDialog.tsx` | **новый**: ОДНО окно правки на три входа |
| `frontend/src/components/inflation/InflationControls.tsx` | **новый**: группа «Поправка на инфляцию» |
| `frontend/src/components/inflation/InflationLevelsBar.tsx` | **новый**: полоса уровней |
| `frontend/src/components/inflation/InflationRefusalBanner.tsx` | **новый**: баннер отказа |
| `frontend/src/components/standards/InflationSeriesTab.tsx` | **новый**: вкладка на экране нормативов |
| `frontend/src/pages/standards/StandardsPage.tsx` | **правка**: третья вкладка |
| `frontend/src/pages/compare/ComparePage.tsx` | **правка**: параметры URL, группа, полоса, баннер |
| `frontend/src/services/queries.ts` | **правка**: объектная форма `detail`, хуки рядов |
| `frontend/src/services/api/domain.ts`, `queryKeys.ts`, `types/domain.ts`, `test/handlers.ts`, `test/fixtures.ts` | **правка**: контракт рядов и приведения |
| `AGENTS.md` | ревизия v6.10 (задача 15) |
| `docs/devlog/2026-08-18-inflation-adjustment.md` | **новый** devlog (задача 16) |

---

## Инвентарь символов (гейт 3: проверено `grep`-ом 2026-08-18)

**Существуют, использовать как есть** — путь и строка проверены:

| Символ | Где |
|---|---|
| `DomainError` (поля `status_code`, `detail`), `iso`, `rollback_on_domain_error`, `require_text`, `translating_integrity` | `backend/crud/common.py:26,40,75,99,123` |
| `load_rollups`, `build_comparison`, `_load_estimates`, `_load_columns`, `EstimateRollup`, `DirectBranch`, `_split_buckets`, `_build_bucket_cell`, `_compute_median`, `_apply_deviation`, `_mode_caption`, `_composition_caption`, `_row_cells`, `_cell_entry` | `backend/crud/comparison.py:533,1553,250,1523,161,144,975,1081,1137,1166,1300,1324,1229,1217` |
| `BUCKET_BASE/AMENDMENTS/TOTAL`, `VAT_MODE_OWN/SINGLE/NET`, `ABSENT/ZERO/VALUE`, `_DIV_CONTEXT` | `backend/crud/comparison.py:110-131` |
| `quantize_money`, `net_to_gross`, `gross_to_net`, `effective_display_rate` | `backend/money/vat.py:149,80,74,126` |
| `_ExactNumbersMixin`, `_EXACT_FIELDS` (образец валидатора float) | `backend/routers/rate_standards.py:35,29` |
| `decimal_json`, `require_admin`, `get_current_user` | `backend/responses.py:93`, `backend/auth.py:72,23` |
| `_created_at`, `_updated_at` (server_default `now()`, `onupdate`) | `backend/models.py:34,38` |
| `Estimate.data_prepared_on_date`, `Contract.signed_date` | `backend/models.py:468`, `ContractFactory` |
| `build_comparison_sheet`, `_columns_spec`, `_write_contract_group_headers`, `_write_data_row`, `_BUCKET_ORDER`, `_COLS_PER_CONTRACT` | `backend/services/excel_comparison.py:206,138,149,167,75,82` |
| `write_banner`, `write_cell`, `write_column_headers`, `apply_column_widths`, `workbook_bytes`, `font`, `safe_str`, `C_HEADER_BG` | `backend/services/excel.py:149,124,160,171,176,…` |
| `contract_with_area`, `contract_with_amendment`, `make_proposal`, `seed_chapter_with_positions`, `category_id`, `VAT_20` | `backend/tests/comparison_fixtures.py:103,188,39,45,32,28` |
| Фикстуры `client` (роль admin, переключается `client.auth_state["role"]`), `db_session` (savepoint, `commit()` внутри CRUD виден тесту), `factories`, `member_client`, `admin_client` | `backend/tests/conftest.py:327,306,508`, `tests/integration/conftest.py:100,122` |
| `apiErrorDetail`, `apiErrorStatus`, `toastApiError`, `reportErrorMessage` | `frontend/src/services/queries.ts:57,72,76,801` |
| `useComparison`, `useComparisonReport`, `comparisonApi.get`, `reportsApi.comparison`, `qk.comparison.get` | `frontend/src/services/queries.ts:763,839`, `services/api/analytics.ts:43,136`, `services/queryKeys.ts:116` |
| `ComparisonColumnHeader`, `deviationTone`, `buildComparisonTree` | `frontend/src/pages/compare/ComparePage.tsx:253`, `compare/deviationTone.ts` |
| `roundDecimalPercent`, `formatSharePercent`, `formatDate`, `addDecimalStrings`, `multiplyDecimalStrings`, `normalizeDecimalInput` | `frontend/src/lib/format.ts:162,115,207`, `lib/decimal.ts:61,87,21` |
| `renderWithProviders` (параметр `initialUser` переключает роль), `handlerState`, `server`, `sampleComparison` | `frontend/src/test/utils.tsx:70`, `test/handlers.ts:107`, `test/fixtures.ts:1525` |
| `Dialog/DialogContent/DialogHeader/DialogTitle/DialogFooter/DialogDescription`, `Input`, `Label`, `Checkbox`, `Table*`, `Tabs*`, `Button`, `Select*` | `frontend/src/components/ui/` — **уже стоят** |
| `RateStandardFormDialog`, `ReapproveDialog` (образец модального окна на этом экране) | `frontend/src/components/standards/` |

**Заводится этой задачей** — сегодня этих имён НЕТ (проверено: `money/inflation.py`
и `routers/domain_errors.py` отсутствуют, `inflation_series`/`inflation_index_values`
не встречаются ни в одном `.py`, `alembic/versions` кончается на `0013`):

`money.inflation.{YearMonth, year_exponents, required_years, coefficient,
adjust_amount, current_period, parse_target_month, BUSINESS_TIMEZONE,
INFLATION_PRECISION}`, `models.{InflationSeries, InflationIndexValue}`,
`crud.inflation_series.*`, `routers.domain_errors.raise_domain_error`,
`routers.inflation_series.router`, `crud.comparison.{resolve_inflation,
InflationPlan}`, `frontend/src/lib/inflation.ts::coefficientLevel`,
`InflationSeriesDialog`, `InflationControls`, `InflationLevelsBar`,
`InflationRefusalBanner`, `InflationSeriesTab`, типы `InflationSeries*`,
`ComparisonInflation`.

**Проверено отдельно, чтобы не переоткрывали:**

* `tzdata` в окружении есть **транзитивно, как жёсткая зависимость `psycopg` на
  win32** (`uv.lock:674`); `ZoneInfo("Europe/Moscow")` отработал замером
  (`2026-08-18 20:17+03:00`). Отдельную зависимость в `pyproject.toml` **не
  добавлять**.
* `crud/comparison.py` **не вызывает `quantize_money` вовсе** — округления в
  агрегате сравнения нет и сейчас; DoD 6 («равенство после `quantize_money`»)
  проверяется в тесте, а не новым вызовом в коде.
* Фабрики дают детерминированные даты: `EstimateFactory.data_prepared_on_date =
  2025-04-01`, `ContractFactory.signed_date = 2025-03-01`. Значит существующие
  тесты сравнения после фичи попадают в один год и без ряда работают как раньше.
* `_load_estimates` уже сортирует `amendment_no NULLS FIRST` — порядок смет
  внутри договора устойчив, приведение на смету на него не влияет.

---

## Решения плана, которых спека не фиксирует

Названы отдельно: их одобрение — часть гейта 3.

1. **Коэффициент колонки, когда сметы договора приведены РАЗНЫМИ множителями.**
   Спека требует коэффициент на смету (§2.2) и чип уровня на колонке (DoD 33), но
   случай «в договоре ДГП 2024 года и ДС 2026-го» не разбирает. Решение:
   `columns[].inflation_coefficient` несёт значение, **только если все сметы
   договора получили один и тот же коэффициент**; иначе `null`, чип показывает
   «разные», **а разбивка уезжает в ответе и живёт в подсказке**:

   ```jsonc
   "inflation_factors": [{"label": "ДГП",    "coefficient": "1.083"},
                         {"label": "ДС №1",  "coefficient": "1.000"}]
   ```

   Поле приходит **только** когда коэффициенты расходятся. Без разбивки чип
   «разные» отправлял бы читателя смотреть корзины, а корзины множителей не
   показывают, — арифметика переставала бы быть проверяемой, чего DoD 33 требует
   прямо. Подписи `label` берутся из словаря `_composition_caption`
   ([comparison.py:1324](../../../backend/crud/comparison.py)) — «ДГП», «ДС №1» —
   чтобы шапка колонки говорила о сметах теми же словами, что подпись состава
   НДС под ней. Врать одним числом нельзя, вторая ось чипов удвоила бы шапку. На
   стенде случай не воспроизводится (ДС нет) — проверяется фикстурой.
2. **Golden-снимок для DoD 1.** «Сравнить с дофичевым целиком» реализуется
   снимком, снятым **до** правки `comparison.py` и положенным в
   `backend/tests/golden/`. Идентификаторы в снимке нормализуются позиционно
   (`contract_id` → индекс колонки), иначе снимок ломался бы от порядка тестов:
   `ContractFactory.contract_number` — глобальная последовательность.
3. **В окне правки есть строка «добавить год».** Макет её не показывает, но без
   неё вход «Заполнить недостающие годы» (§2.9) нечем исполнить: недостающих
   годов в ряду по определению ещё нет.
4. **Три дополнительных запроса при включённом приведении** — `resolve_inflation`
   (ряд, значения, даты смет). Полный бюджет `build_comparison`: **5 без
   приведения, 8 с ним**, и это утверждается абсолютным `assert`, а не только
   «не растёт с выборкой» (см. «Архитектура»). Согласованность двух чтений смет
   держит громкий `RuntimeError`, а не предположение.
5. **Подпись оси собирает сервер** (`_mode_caption` получает план приведения), а
   не клиент: подпись печатается и на листе, а вторая её сборка на фронте была бы
   ровно тем расхождением, против которого §2.12 требует одной функции.

---

## Task 1: Golden-снимок дофичевого ответа

**Первой задачей и ДО единой правки `comparison.py`:** снимок, снятый после
правки, доказывает совпадение с самим собой.

**Files:**
- Create: `backend/tests/integration/test_comparison_baseline.py`
- Create: `backend/tests/golden/comparison_baseline.json`, `comparison_baseline_sheet.json`
- Modify: `backend/tests/comparison_fixtures.py` (детерминированная фикстура)

**Интерфейс (заводится этой задачей):**

```python
def baseline_selection(db, factories) -> list[int]:
    """Три договора с ЯВНЫМИ номерами, датами, площадями и суммами.

    Всё задано явно: `contract_number` фабрики — глобальная последовательность,
    и снимок, снятый с неё, разъехался бы от порядка тестов.
    """

def normalized(data: dict) -> dict:
    """Ответ с позиционными идентификаторами вместо сквозных id."""
```

- [ ] **Step 1:** фикстура `baseline_selection` — три договора, у каждого одна
      смета; номера `ГП-Б1/Б2/Б3`, `signed_date` 2024-05-28 / 2025-02-20 /
      2026-07-06 (разбег стенда, спека §1.1), `data_prepared_on_date` тот же,
      площади 50000+50000, суммы по статьям «1», «2», «4» разные.
- [ ] **Step 2:** тест снимает `build_comparison(db, ids, vat_mode="own")`,
      нормализует и сравнивает с golden-файлом; при отсутствии файла — падает с
      внятным текстом «снимок не заведён», а не создаёт его молча.
- [ ] **Step 3:** второй тест — дамп листа: `build_comparison_sheet(data,
      generated_at=dt.date(2026, 8, 18))` открывается `openpyxl.load_workbook`
      из `io.BytesIO`, в дамп идут по каждой непустой ячейке `coordinate`,
      `value`, `number_format`, `data_type`, `font.bold`. **Байты не
      сравниваются** (DoD 1: XLSX — ZIP, метаданные архива расходятся).
- [ ] **Step 4:** сгенерировать оба golden-файла (одноразовым прогоном с
      `--golden-write` либо ручной записью результата), закоммитить вместе с
      тестом.
- [ ] **Step 5:** прогнать `pytest tests/integration/test_comparison_baseline.py -v`
      — обязан быть зелёным ДО всех правок.

**Утверждения с числами:** в снимке три колонки; `columns[0].contract_number ==
"ГП-Б3"` (порядок `signed_date DESC`); ни одного ключа, содержащего `inflation`.

**Коммит:** `test(inflation): golden-снимок дофичевого сравнения — основание DoD 1`

---

## Task 2: Ядро формулы — `money/inflation.py`

**Files:**
- Create: `backend/money/inflation.py`
- Test: `backend/tests/unit/test_money_inflation.py`

**Интерфейс (заводится этой задачей):**

```python
INFLATION_PRECISION = 34
BUSINESS_TIMEZONE = "Europe/Moscow"   # §2.7: «текущий» в названной зоне, не по часам ОС

@dataclass(frozen=True, order=True)
class YearMonth:
    year: int
    month: int          # 1..12, день намеренно отсутствует (§2.4)

def parse_target_month(raw: str) -> YearMonth: ...
    # "YYYY-MM"; иначе ValueError — в HTTP-код его переводит роутер

def year_exponents(source: YearMonth, target: YearMonth) -> dict[int, Decimal]:
    """E(target)[t] − E(source)[t] для всех t, где разность НЕ равна нулю.

    E(y, m): k(t)→1 при t < y, k(y)→m/12, иначе 0 (§2.5).
    Годы, сокращающиеся тождественно, в результат не попадают — правило
    «сплошной диапазон min…max» математически неверно (§2.5).
    """

def required_years(source: YearMonth, target: YearMonth) -> list[int]: ...
    # sorted(year_exponents(...)) — покрытие выборки есть ОБЪЕДИНЕНИЕ этих списков

def coefficient(exponents: Mapping[int, Decimal], values: Mapping[int, Decimal]) -> Decimal:
    """Каноническая форма: годы по ВОЗРАСТАНИЮ, одна `**` на год, затем
    последовательное умножение, всё внутри localcontext(prec=34)."""

def adjust_amount(value: Decimal, factor: Decimal) -> Decimal: ...
    # единственное место умножения суммы на коэффициент, тот же prec=34

def current_period(tz_name: str = BUSINESS_TIMEZONE) -> YearMonth: ...
    # datetime.now(ZoneInfo(tz_name)) — НЕ локальные часы процесса
```

- [ ] **Step 1:** написать тесты по числам ниже, убедиться, что они красные.
- [ ] **Step 2:** реализовать модуль.
- [ ] **Step 3:** прогнать `pytest tests/unit/test_money_inflation.py -v`.

**Утверждения с числами** (пересчитаны замером 2026-08-18 при `k(2024)=1.075`,
`k(2025)=1.083`, `k(2026)=1.060`, `prec=34`):

| Проверка | Ожидание | DoD |
|---|---|---|
| `year_exponents((2024,12),(2025,12))` | `{2025: 1}` — 2024-го НЕТ | 4 |
| коэффициент того же перехода | ровно `Decimal("1.083")` | 3 |
| `year_exponents((2026,3),(2026,3))` | `{}` | 5 |
| коэффициент на пустых показателях | ровно `Decimal(1)`, **ни одной строки ряда не требуется** | 5 |
| `year_exponents((2024,5),(2026,8))` | `{2024: 7/12, 2025: 1, 2026: 2/3}` | — |
| коэффициент того же | `1.174412425779300303760548277958037` | — |
| `(2026,7)→(2026,8)` | `1.004867550565343037541198945587506` | — |
| `(2025,2)→(2026,8)` | `1.111034702376096271381243623987202` | — |
| `(2026,7)→(2024,5)` (обратно) | `0.8556342972091315960133676609758070`, показатели отрицательные | — |
| `(2025,1)→(2025,12)` | `1.075827773742335083053899972842088` | — |
| телескопирование `may2024→dec2025→aug2026` против прямого | коэффициенты совпадают точно; на сумме `4823456789.17` расходятся в 24-м знаке (`…318244` против `…318246`) и **совпадают после `quantize_money`** | 6 |
| `L(дек y)/L(дек y−1)` | равно `k(y)` — семантика показателя | 3 |
| `current_period` с подменённой зоной | тест подменяет `tz_name` на `"Pacific/Kiritimati"` и на `"Pacific/Niue"` у момента, где даты в этих зонах разные, и получает **разные** `YearMonth` — доказывает, что зона читается, а не игнорируется | 18 |

**Границы:** искусственного диапазона `0.5…2.0` НЕ вводить (спека §2.4).
Экстраполяции нет: отсутствующий год — отказ, а не подстановка.

**Коммит:** `feat(inflation): ядро формулы приведения — форма показателей, prec=34`

---

## Task 3: Миграция 0014 и модели

**Files:**
- Create: `backend/alembic/versions/2026_08_18_0014-inflation_series.py`
- Modify: `backend/models.py`
- Test: `backend/tests/integration/test_schema_constraints.py` (новый класс)

**Схема — дословно из спеки §2.11.** `inflation_series`: `name text NOT NULL
UNIQUE CHECK (btrim(name) <> '')`, `note text NULL`, `is_active bool NOT NULL
DEFAULT true`, `created_at`, `updated_at`. `inflation_index_values`: `series_id
NOT NULL → inflation_series` (`ondelete` не задаётся — `DELETE` запрещён),
`year int NOT NULL`, `coefficient numeric NOT NULL CHECK (coefficient > 0)`,
`is_forecast bool NOT NULL DEFAULT false`, `source text NOT NULL CHECK (btrim(source) <> '')`,
`UNIQUE (series_id, year)`, `created_at`, `updated_at`.

- [ ] **Step 1:** миграция (`revision = "0014"`, `down_revision = "0013"`),
      `downgrade` роняет обе таблицы. Выражения CHECK продублировать константами
      в шапке — как в 0013: миграция обязана быть неизменной во времени.
- [ ] **Step 2:** модели поверх, `__table_args__` повторяют CHECK/UNIQUE
      дословно (за расхождение отвечают parity-тесты, `alembic check` выражений
      не сравнивает).
- [ ] **Step 3:** справочник создаётся **пустым** — никаких `INSERT` в миграции.
- [ ] **Step 4:** parity-тесты: пустое `name` после `btrim` отвергнуто, пустой
      `source` отвергнут, `coefficient = 0` и `-1` отвергнуты, два одинаковых
      года в одном ряду отвергнуты, тот же год в РАЗНЫХ рядах разрешён.
- [ ] **Step 5:** `just db-test-check` (`alembic check`) и прогон
      `test_schema_constraints.py`; проверить `alembic downgrade -1` и обратно.

**Утверждения:** после `upgrade` `select count(*) from inflation_series` = 0;
`INSERT` строки со `source = '   '` даёт `IntegrityError` с именем
`ck_inflation_index_values_source_not_blank`.

**Коммит:** `feat(inflation): миграция 0014 — справочник рядов и значения по годам`

---

## Task 4: `DomainError.code` и единая трансляция

**Files:**
- Modify: `backend/crud/common.py`, `backend/routers/analytics.py`, `backend/routers/reports.py`
- Create: `backend/routers/domain_errors.py`
- Test: `backend/tests/unit/test_domain_errors.py` (новый)

**Интерфейс:**

```python
# crud/common.py — обратно совместимо
class DomainError(Exception):
    def __init__(self, status_code: int, detail: str,
                 code: str | None = None, context: dict | None = None): ...

# routers/domain_errors.py — ЕДИНСТВЕННЫЙ транслятор
def raise_domain_error(err: DomainError) -> NoReturn:
    """code is None -> detail строкой, как до фичи, до символа.
    code задан -> detail объектом: {"code", "message", **context}.
    Карты «код → статус» НЕТ: статус несёт сама ошибка (спека §2.12)."""
```

- [ ] **Step 1:** расширить `DomainError`, не трогая существующие вызовы.
- [ ] **Step 2:** завести `routers/domain_errors.py`.
- [ ] **Step 3:** удалить `_raise` из `analytics.py:35` и `reports.py:51`,
      импортировать общий; проверить, что других определений `_raise` в
      `routers/` не осталось (`grep -n "def _raise" routers/`).
- [ ] **Step 4:** тесты: строковый отказ не изменился; кодированный отказ даёт
      `{"detail": {"code", "message", "missing_years"}}`; ключи `context` лежат
      РЯДОМ с `code`, а не вложенным узлом; `analytics.raise_domain_error is
      reports.raise_domain_error` — трансляция в одном экземпляре (DoD 23).
- [ ] **Step 5:** прогнать весь набор сравнения — существующие 400/404 обязаны
      остаться прежними (DoD 22).

**Коммит:** `feat(errors): DomainError получает code/context, трансляция одна на три роутера`

---

## Task 5: CRUD рядов — правка одной транзакцией

**Files:**
- Create: `backend/crud/inflation_series.py`
- Test: `backend/tests/integration/test_inflation_series_api.py` (часть про CRUD)

**Интерфейс:**

```python
def list_series(db, *, include_archived: bool = False) -> list[dict]: ...
def get_series_dict(db, series_id: int) -> dict: ...        # 404 неизвестный
def list_values(db, series_id: int) -> list[dict]: ...      # архивный ЧИТАЕТСЯ
def create_series(db, *, name, note, values: list[dict]) -> dict: ...
def update_series(db, series_id: int, *, name=UNSET, note=UNSET,
                  is_active=UNSET, values: list[dict] | None = None) -> dict: ...
```

**Правила (спека §2.12), каждое — тест:**

* `values[]`: год задаётся **тремя** полями (`coefficient`, `source`,
  `is_forecast`); неполный год — `422`.
* повтор года внутри `values[]` — `422`, код `duplicate_year`, контекст
  `{"years": [...]}`, **проверка ДО любой записи**;
* год, чья тройка совпала с сохранённой, — **no-op**, `updated_at` года не
  двигается;
* `inflation_series.updated_at` двигается **тогда и только тогда**, когда что-то
  реально изменилось (поле ряда либо хотя бы один год);
* годы, не перечисленные в теле, **остаются** (`PATCH`, не `PUT`);
* архивный ряд: `{"is_active": true}` **в одиночку** — размораживает; вместе с
  полями или `values` — `409`; любая другая правка архивного — `409`.

**Форма записи — с этого места дословно, от неё зависит негативная проверка:**

```python
with rollback_on_domain_error(db):
    _reject_duplicate_years(payload_values)      # ДО мутаций
    series = _get_for_update(db, series_id)
    _apply_archived_rules(series, ...)
    changed = _apply_series_fields(series, ...)
    for item in payload_values:                  # порядок тела сохраняется
        changed |= _apply_year(db, series, item) # валидирует и мутирует
    if changed:
        series.updated_at = sa.func.now()
db.commit()                                      # ОДИН на всё окно
```

- [ ] **Step 1:** тесты (красные) по списку правил.
- [ ] **Step 2:** реализация в указанной форме. Доменные проверки года
      (`coefficient > 0`, `require_text(source)`) живут в `_apply_year`, а НЕ в
      схеме запроса (Global Constraints).
- [ ] **Step 3:** тест атомарности: `PATCH` с двумя годами, где ПЕРВЫЙ валиден
      (2025, `1.0830`, источник «бюллетень 01.2026»), а ВТОРОЙ невалиден
      (2026, `source = "   "` — пусто после `btrim`), плюс новое `name`.
      Ожидание: `422`, в базе `name` прежнее, года 2025 нет, года 2026 нет.

      **Почему именно пустой `source`, а не `coefficient = "-1"`:** отказ обязан
      случиться ВНУТРИ CRUD, после того как первый год уже применён. Правило
      «непусто после `btrim`» естественным образом не выражается в pydantic
      (`min_length=1` пропускает пробел), тогда как положительность коэффициента
      кто-нибудь однажды продублирует в схеме `gt=0` — и тест станет вакуозным,
      не изменив ни строчки в самом тесте. Тест дополнительно утверждает, что
      `detail` — **строка доменного отказа**, а не список pydantic: если проверка
      всё же уедет в схему, тест покраснеет вместо того, чтобы тихо перестать
      сторожить.

      Состояние после `422` читать **после `db_session.expire_all()`** — иначе
      identity map вернёт объект из памяти сессии, и тест проверит кэш, а не базу.
- [ ] **Step 4 — НЕ ДЕЛЕГИРУЕТСЯ, снятие защиты (DoD 26):** перенести
      `db.commit()` ВНУТРЬ цикла по годам (правдоподобная наивная реализация —
      «пишем год за годом»), прогнать тест Step 3, убедиться, что он **красный**
      на частично записанном ряде (2025 записан). Вернуть код, сверить файл по
      `SHA256` с состоянием до правки, прогнать снова — зелёный. В пробнике
      правки обязателен `assert old in source` перед заменой.
- [ ] **Step 5 (DoD 37):** тест метки времени — **на `committing_db`**, не на
      `db_session`: замером установлено, что `now()` внутри одной транзакции
      одинакова до и после savepoint-commit'а, поэтому в обычной фикстуре
      направление «сдвинулась» недоказуемо, а «не сдвинулась» вакуозно зелено.
      Проверяются ОБА направления: правка поля ряда либо любого года двигает
      `inflation_series.updated_at`; `PATCH`, повторяющий текущее состояние
      целиком, не двигает ни её, ни `updated_at` годов.
- [ ] **Step 6:** добавить `inflation_index_values` и `inflation_series` в
      `_DOMAIN_TABLES` ([tests/conftest.py:383](../../../backend/tests/conftest.py)) —
      иначе ряды, записанные committing-тестом, переживут его и потекут в
      соседние.
- [ ] **Step 7:** прогон файла целиком.

**Коммит:** `feat(inflation): CRUD рядов — правка ряда одной транзакцией`

---

## Task 6: Роутер `/api/v1/inflation-series`

**Files:**
- Create: `backend/routers/inflation_series.py`
- Modify: `backend/main.py`
- Test: `backend/tests/integration/test_inflation_series_api.py` (часть про HTTP)

**Маршруты (спека §2.12):** `GET /` (`include_archived=false` по умолчанию),
`GET /{id}/values`, `POST /` (`admin`), `PATCH /{id}` (`admin`). `DELETE` — нет.

**Схемы pydantic:** `coefficient: Decimal` строкой в JSON, `float` на входе
отвергается валидатором с **уникальным именем метода**
`_reject_float_coefficient` (§11: pydantic ключует валидаторы по имени, и
одноимённые схлопываются молча). **`gt=0`, `min_length` и прочие доменные
проверки в схеме запрещены** (Global Constraints): они увели бы отказ из CRUD и
обессмыслили тест атомарности задачи 5.

- [ ] **Step 1:** роутер по образцу `routers/rate_standards.py` (тот же приём:
      `require_admin` на изменяющих, чтение — всем аутентифицированным).
- [ ] **Step 2:** регистрация в `main.py` рядом с нормативами, с комментарием о
      праве.
- [ ] **Step 3:** тесты HTTP: `member` читает список и значения (200); `member`
      получает `403` на `POST` и на `PATCH` (включая архивацию); архивный ряд
      **отсутствует** в списке по умолчанию и **присутствует** при
      `include_archived=1`; `GET /{id}/values` архивного — 200; неизвестный ряд —
      404.
- [ ] **Step 4:** тест `float` на входе: `{"coefficient": 1.083}` (число, не
      строка) — `422`; `{"coefficient": "1.083"}` — принято; в ответе
      `coefficient` приходит **строкой** (прогон по конкретному входу, а не
      чтение кода).
- [ ] **Step 5:** прогон файла.

**Коммит:** `feat(inflation): роутер рядов индексов, запись под admin`

---

## Task 7: Разрешение дат, покрытие и отказы приведения

**Files:**
- Modify: `backend/crud/comparison.py`
- Test: `backend/tests/integration/test_comparison_inflation.py` (часть про отказы)
- Modify: `backend/tests/comparison_fixtures.py`

**Интерфейс:**

```python
@dataclass(frozen=True)
class InflationPlan:
    series_id: int
    series_name: str
    series_note: str | None
    series_updated_at: str | None          # iso()
    target_month: str                      # "YYYY-MM"
    has_forecast: bool
    used_years: list[dict]                 # year, coefficient, source, is_forecast, updated_at
    factor_by_estimate: dict[int, Decimal]

def resolve_inflation(db, contract_ids, *, series_id: int,
                      target_month: YearMonth) -> InflationPlan:
    """Период каждой сметы, покрытие по ОБЪЕДИНЕНИЮ требуемых годов, коэффициенты.

    Период (спека §2.2):
      amendment_no IS NULL      -> COALESCE(estimates.data_prepared_on_date,
                                            contracts.signed_date)
      amendment_no IS NOT NULL  -> ТОЛЬКО estimates.data_prepared_on_date
    """
```

**Отказы — `DomainError(422, message, code=…, context=…)`:**

| Код | Когда | Контекст |
|---|---|---|
| `missing_inflation_years` | покрытия не хватает | `{"missing_years": [int]}` (по возрастанию) |
| `amendment_date_missing` | у ДС нет своей `data_prepared_on_date` | `{"estimate_ids": [int]}` |

- [ ] **Step 1:** фикстуры: `series_with_years(db, name, {2024: "1.075", …})` и
      `contract_with_amendment_dates(...)` — базовая смета и ДС с ЯВНЫМИ
      `data_prepared_on_date` (у существующей `contract_with_amendment` дат нет).
- [ ] **Step 2:** тесты (красные): покрытие считается объединением, а не
      диапазоном (договор мая-2024 и договор августа-2026 при цели август-2026
      требуют 2024, 2025, 2026; при цели декабрь-2024 — только 2024);
      отсутствующий год → отказ с перечнем; номинальные числа НЕ выданы.
- [ ] **Step 3:** реализация — один запрос `Estimate ⋈ Contract`, затем
      `required_years` на смету, объединение, проверка, `coefficient` на смету.
- [ ] **Step 4:** тест DoD 8: ДС **без** `data_prepared_on_date` у договора,
      чей `signed_date` попадает в год, полностью покрытый рядом → `422`,
      код `amendment_date_missing`, `estimate_ids == [id ДС]`.
- [ ] **Step 5 — НЕ ДЕЛЕГИРУЕТСЯ, снятие защиты (DoD 8):** заменить правило
      периода ДС на тот же `COALESCE(..., contracts.signed_date)`, что у базовой
      сметы (снятие, которое МОЖЕТ случиться — «сделаем одинаково»), прогнать
      тест Step 4 и убедиться, что он **красный**: без защиты запрос отвечает
      `200` и выдаёт приведённые числа. Вернуть код, сверить `SHA256`, прогнать
      снова.
- [ ] **Step 6:** тест DoD 10 — цель раньше самой поздней сметы: коэффициент
      меньше единицы, **отказа нет** (обратное направление законно).

**Утверждения с числами:** ряд `{2024: 1.075, 2025: 1.083, 2026: 1.060}`, цель
`2026-08`; смета от 2024-05-28 → `1.174412425779300303760548277958037`; смета от
2026-07-06 → `1.004867550565343037541198945587506`; смета от 2026-08-15 → ровно
`1` (тот же месяц, ни одной строки ряда не требуется).

**Коммит:** `feat(inflation): период сметы, покрытие выборки и два кода отказа`

---

## Task 8: Приведение чисел, метаданные и подпись оси

**Files:**
- Modify: `backend/crud/comparison.py`
- Test: `backend/tests/integration/test_comparison_inflation.py` (часть про числа)

**Правки:**

```python
def load_rollups(db, contract_ids, *,
                 adjustment: Mapping[int, Decimal] | None = None) -> …
    # единственное место: DirectBranch.net = adjust_amount(net, k) ДО build_tree

def build_comparison(db, contract_ids, *, vat_mode, single_rate=None,
                     inflation_series_id: int | None = None,
                     target_month: YearMonth | None = None) -> dict
```

**Ответ пополняется ТОЛЬКО при сосчитанном приведении** (иначе DoD 1):

```jsonc
{
  "inflation": {
    "series_id": 1, "series_name": "…", "series_note": "…",
    "series_updated_at": "2026-01-12T…", "target_month": "2026-08",
    "has_forecast": true,
    "used_years": [{"year": 2024, "coefficient": "1.0750",
                    "source": "бюллетень 01.2025", "is_forecast": false,
                    "updated_at": "…"}]
  },
  "columns": [{"…": "…", "inflation_coefficient": "1.1744124257793003…",
               // только когда коэффициенты смет РАСХОДЯТСЯ (решение плана №1):
               "inflation_factors": [{"label": "ДГП", "coefficient": "1.083"},
                                     {"label": "ДС №1", "coefficient": "1.000"}]}]
}
```

- [ ] **Step 1:** тесты (красные) по числам ниже.
- [ ] **Step 2:** умножение в `load_rollups`, план — из задачи 7. Умножается
      **каждая ветвь `frozen_branches` до разделения на `direct` и
      `unallocated`** — иначе дерево приведётся, а остаток останется номинальным
      и молча смешается с приведённым в «Итого по договору»
      ([comparison.py:1002](../../../backend/crud/comparison.py) складывает их
      вместе).
- [ ] **Step 3:** громкий `RuntimeError` на смете без коэффициента при непустом
      `adjustment` (см. «Архитектура»), тест с урезанным `adjustment`.
- [ ] **Step 4:** `_mode_caption` получает `InflationPlan | None` и дописывает
      предложение «Цены приведены к <месяц прописью> <год> по ряду «<название>»;
      <год> — прогноз.» Название берётся ИЗ ПЛАНА — захардкоженное было бы
      дефектом того же рода, что подсветка по чужой медиане (спека §2.12).
- [ ] **Step 5:** `columns[].inflation_coefficient` и `inflation_factors` по
      правилу решения плана №1.
- [ ] **Step 6:** абсолютный бюджет запросов: **5 без приведения, 8 с ним**
      (`_count_queries` уже есть — `test_comparison_buckets.py:101`). Существующий
      тест «не растёт с выборкой» остаётся — он про другое.
- [ ] **Step 7 — НЕ ДЕЛЕГИРУЕТСЯ, снятие защиты (DoD 31):** зашить название ряда
      в `_mode_caption` константой, прогнать тест «два ряда — две подписи и
      разные числа», убедиться, что он **красный** на подписи. Вернуть, сверить
      `SHA256`.

**Утверждения с числами.** Фикстура: три договора, площадь 100 000 м², даты смет
2024-05-28 / 2025-02-20 / 2026-07-06, ряд `{2024: 1.075, 2025: 1.083,
2026: 1.060}`, цель `2026-08`. У каждого договора деньги лежат **в трёх разных
местах сразу**, иначе единственная точка умножения проверялась бы одной веткой
данных: статья «1» с подстатьёй «1.1» (поддерево), собственные деньги статьи «1»
(строка «Без подстатьи»), позиции без раздела («Нераспределённое»). Нетто в
каждом месте — 1 000 000.

| Проверка | Ожидание | DoD |
|---|---|---|
| множители колонок | `1.174412…`, `1.111034…`, `1.004867…` | 2 |
| медиана строки до приведения | равна 10 (₽/м²) у всех трёх | — |
| медиана после приведения | `1.111034702376096271381243623987202 × 10` — то есть посчитана ОТ ПРИВЕДЁННЫХ | 12 |
| отклонение самой ранней колонки | `+5.7…%` (было `0`) — сдвинулось, и это цель | 12 |
| договор с ДГП 2024-12 и ДС 2025-12 при цели 2025-12 | корзина ДГП × `1.083`, корзина ДС × `1`, «Итого» = их сумма, а НЕ (ДГП+ДС) × общий множитель | 2 |
| `has_forecast` | `true`, если хоть один использованный год прогнозный | 14 |
| `used_years` | ровно требуемые годы, по возрастанию; при цели «тот же месяц» — пустой список, а `inflation` в ответе есть | 5, 13 |
| `series_note`, `series_updated_at` | приходят В ЭТОМ ответе, в том числе для **архивного** ряда по явному id | 38 |
| **строка «Нераспределённое»** | приведена тем же множителем, что статьи; «Итого по договору» равно сумме приведённых частей | 2 |
| **строка «Без подстатьи»** | приведена тем же множителем | 2 |
| колонка со сметами разных лет | `inflation_coefficient is None`, `inflation_factors == [{"ДГП", "1.083"}, {"ДС №1", "1.000"}]` | 33 |
| номинальный ответ | ключей `inflation`, `inflation_coefficient`, `inflation_factors` нет — golden задачи 1 зелёный | 1 |

**Коммит:** `feat(inflation): приведение на смету до агрегации, метаданные и подпись оси`

---

## Task 9: Параметры у обоих маршрутов сравнения

**Files:**
- Modify: `backend/routers/analytics.py`, `backend/routers/reports.py`
- Test: `backend/tests/integration/test_comparison_inflation.py` (часть про HTTP)

**Контракт (спека §2.12), таблица целиком:**

| `inflation_series_id` | `target_month` | Поведение |
|---|---|---|
| нет | нет | дофичевый ответ |
| есть | нет | сервер разрешает текущий месяц в `BUSINESS_TIMEZONE` и **возвращает** его |
| нет | есть | `400` — умолчательного ряда не существует |
| есть | есть | приведение |

- [ ] **Step 1:** оба роутера принимают два одинаковых параметра; разбор
      `YYYY-MM` — `parse_target_month`, неверный формат → `400`.
- [ ] **Step 2:** неизвестный ряд → `404`; архивный по явному id → **200 и
      приведение считается** (DoD 20, два разных утверждения).
- [ ] **Step 3:** тест DoD 11 — выгрузка при отказе отвечает тем же
      структурированным `422`, `Content-Type` НЕ `…spreadsheetml…`, вложения нет
      (проверяется отсутствием вложения, а не содержимым листа).
- [ ] **Step 4:** тест DoD 23 — экран и лист на ОДНОМ входе дают идентичный
      объект `detail`.
- [ ] **Step 5:** тест DoD 18 — «текущий месяц» разрешается в названной зоне:
      подмена `money.inflation.BUSINESS_TIMEZONE`/аргумента даёт другой
      `target_month` на моменте, где зоны расходятся датой.

**Коммит:** `feat(inflation): параметры приведения у экрана и выгрузки`

---

## Task 10: Лист выгрузки печатает ряд

**Files:**
- Modify: `backend/services/excel_comparison.py`
- Test: `backend/tests/integration/test_comparison_excel.py` (дописать)

- [ ] **Step 1:** при `data.get("inflation")` под подписью состава печатаются:
      строка «Цены приведены к: август 2026 · ряд: «…»», затем по строке на
      каждый использованный год: `2024 · 1.0750 · источник · прогноз/факт ·
      правлен 12.01.2026`. Без блока `inflation` лист не меняется **ни на одну
      ячейку** — golden задачи 1.
- [ ] **Step 2:** тест DoD 13: все пять фактов года на листе присутствуют.
- [ ] **Step 3:** тест DoD 32 (половина про лист): `source` года **есть на
      листе**; парная половина («на экране его нет») живёт в задаче 14.

**Коммит:** `feat(inflation): лист выгрузки печатает ряд, месяц и годы с источниками`

---

## Task 11: Фронт — типы, API, разбор объектного отказа

**Files:**
- Modify: `frontend/src/types/domain.ts`, `services/api/domain.ts`,
  `services/queryKeys.ts`, `services/queries.ts`
- Test: `frontend/src/services/apiErrors.test.ts` (дописать)

- [ ] **Step 1:** типы `InflationSeries`, `InflationSeriesValue`,
      `InflationSeriesInput`, `InflationSeriesPatch`, `ComparisonInflation`;
      `Comparison.inflation?`, `ComparisonColumn.inflation_coefficient?`,
      `ComparisonParams.inflation_series_id?`/`target_month?`.
- [ ] **Step 2:** `inflationSeriesApi` в `services/api/domain.ts` (там же, где
      нормативы), ключи в `queryKeys.ts`, хуки `useInflationSeries`,
      `useInflationSeriesValues`, `useCreateInflationSeries`,
      `useUpdateInflationSeries` (последний инвалидирует и `qk.comparison.all`
      — DoD 36).
- [ ] **Step 3:** `apiErrorDetail` учит **третью** форму `detail` — объект:
      возвращает `detail.message`. Без этого тост печатал бы `[object Object]`.
      Рядом — `apiErrorCode(err)` и `apiErrorContext<T>(err)`.
- [ ] **Step 4:** `reportErrorMessage` (блоб-путь выгрузки) — то же ветвление:
      сегодня он берёт только `typeof detail === "string"`.
- [ ] **Step 5:** тесты на все три формы `detail`; прогон
      `npx vitest run src/services` из `frontend/`.

**Коммит:** `feat(inflation): контракт рядов на фронте, объектная форма detail`

---

## Task 12: Окно правки ряда — один компонент

**Files:**
- Create: `frontend/src/lib/inflation.ts`, `lib/inflation.test.ts`
- Create: `frontend/src/components/inflation/InflationSeriesDialog.tsx` + тест

**Интерфейс:**

```ts
/** «1.083» → {level: "+8,3%", text: "Рост 8,3 %", tone: "ok"},
 *  «0.083» → {text: "Снижение 91,7 %", tone: "bad"}, "" → {text: "—"} */
export function coefficientLevel(raw: string): CoefficientLevel
```

Считается точно, без `float`: `addDecimalStrings(k, "-1")` →
`multiplyDecimalStrings(…, "100")` → `roundDecimalPercent(…, 1)`. Все три уже
есть. `tone: "bad"` при уровне ниже −50 % либо выше +100 % — как в макете:
красным помечается не «снижение», а невероятный уровень.

- [ ] **Step 1:** `coefficientLevel` + юнит-тесты (`1.083` → рост 8,3 %;
      `0.083` → снижение 91,7 %; `1.15` → рост 15,0 %; пусто → «—»; `abc` → «—»).
- [ ] **Step 2:** диалог по образцу `ReapproveDialog.tsx`: `Dialog` + форма
      отдельным компонентом с `key`, поля «Название», «Примечание», таблица
      годов (год, коэффициент, **расшифровка по вводимому значению**, источник,
      прогноз), строка «добавить год», «Отмена» / «Сохранить» одним запросом.
- [ ] **Step 3: режим СОЗДАНИЯ — тот же компонент, `series === null`.** Шлёт
      `POST` вместо `PATCH`, заголовок «Новый ряд индексов», после успеха ряд
      появляется в списке. **Это первый и обязательный пользовательский путь:**
      миграция создаёт справочник ПУСТЫМ (§2.6), и без создания через интерфейс
      фича не заводится вовсе. Отдельный тест: пустой список → «Создать ряд» →
      имя, примечание, один год → ряд в списке.
- [ ] **Step 4:** проп `missingYears?: number[]` — вход из баннера открывает окно
      с уже добавленными пустыми строками этих годов.
- [ ] **Step 5:** архивный ряд: кнопка правки **выключена**, в шапке окна
      пояснение «сначала вернуть в активные».
- [ ] **Step 6:** компонентный тест DoD 25: ввести `0.083` в поле коэффициента и
      увидеть «Снижение 91,7 %» **до сохранения**.
- [ ] **Step 7 — НЕ ДЕЛЕГИРУЕТСЯ, снятие защиты (DoD 25):** удалить вызов
      `coefficientLevel` из компонента (снятие, которое МОЖЕТ случиться),
      прогнать оба теста: компонентный обязан **покраснеть**, юнит-тест функции
      — остаться зелёным. Это и есть доказательство, что защита проверена на том
      уровне, где она живёт. Вернуть код, сверить `SHA256`.

**Коммит:** `feat(inflation): окно правки ряда с живой расшифровкой коэффициента`

---

## Task 13: Вкладка «Индексы инфляции» на экране нормативов

**Files:**
- Create: `frontend/src/components/standards/InflationSeriesTab.tsx` + тест
- Modify: `frontend/src/pages/standards/StandardsPage.tsx`, `test/handlers.ts`

- [ ] **Step 1:** третья вкладка рядом со «Ставки» и «Классы объектов»: таблица
      рядов (название, примечание, охват годов, состояние, кнопки). Пустой
      справочник показывает `EmptyState` с кнопкой «Создать ряд» — это состояние
      сразу после миграции, и оно обязано быть проходимым.
- [ ] **Step 2: архивация и возврат — две разные кнопки, по одному запросу
      каждая.** У активного ряда — «В архив» (`PATCH {"is_active": false}`), у
      архивного — «Вернуть в активные» (`PATCH {"is_active": true}` **и больше
      ничего в теле**). Совмещать возврат с правкой нельзя: сервер ответит `409`
      (§2.10), и это правило проверяемо только двумя шагами. Кнопка «Изменить» у
      архивного выключена.
- [ ] **Step 3:** MSW-хендлеры рядов в `test/handlers.ts` + фикстура двух рядов
      и одного архивного в `test/fixtures.ts`.
- [ ] **Step 4:** тесты: пустой справочник → создание ряда доводится до списка;
      список показывает охват «2024–2026»; «Изменить» открывает **тот же**
      `InflationSeriesDialog`; «В архив» уводит ряд из активных, «Вернуть в
      активные» возвращает; у архивного «Изменить» выключено.

> Экран нормативов целиком под `RequireAdmin` (`App.tsx:92`), поэтому DoD 35
> («кнопок правки нет у `member`») проверяется НЕ здесь, а на `/compare` — там
> `member` бывает.

**Коммит:** `feat(inflation): вкладка рядов индексов на экране нормативов`

---

## Task 14: `/compare` — группа управления, полоса уровней, баннер

**Files:**
- Create: `frontend/src/components/inflation/{InflationControls,InflationLevelsBar,InflationRefusalBanner}.tsx`
- Modify: `frontend/src/pages/compare/ComparePage.tsx`, `ComparePage.test.tsx`, `test/handlers.ts`

**Состояния элементов — таблица спеки §2.12 целиком:**

| Состояние | Поведение |
|---|---|
| Ряд не выбран | селект доступен, «Выберите ряд»; «Привести» **недоступно**; месяц пуст и заблокирован |
| Ряд выбран, приведение выключено | «Привести» доступно; числа те же; параметров в URL нет |
| Приведение включено | сервер вернул месяц → клиент пишет его в поле и в URL вместе с рядом |
| Возврат к номиналу | инфляционные параметры **удаляются** из URL, поле месяца пусто |
| Ряд сброшен в placeholder | приведение выключается |

- [ ] **Step 1:** `InflationControls` — `fieldset` с `legend` «Поправка на
      инфляцию», внутри порядок: Режим, Ряд индексов, В ценах. **Отдельная
      группа**, а не три поля вперемешку с «НДС» (замечание пользователя по
      макету).
- [ ] **Step 2:** запись параметров в URL — тем же приёмом, что `updateVatMode`
      (`setSearchParams(next, { replace: true })`). `Date.now()` на клиенте **не
      использовать**: месяц приходит от сервера.
- [ ] **Step 3:** `InflationLevelsBar` — уровни по годам («2024 +7,5 % · 2025
      +8,3 % · 2026 +6,0 % прогноз»), **примечание ряда**, «правлен ДД.ММ.ГГГГ»,
      кнопка «Изменить ряд». Источники годов на экране **не показываются**.
      Полоса скрыта до выбора ряда; при `hidden` не должна занимать место — в
      макете `display:flex` перебивал `[hidden]{display:none}` (грабли §7 спеки).
- [ ] **Step 4:** чип коэффициента в шапке колонки — **уровень** `+17,4 %`,
      множитель `× 1.1744` в `title` (DoD 33); при `inflation_coefficient ===
      null` — «разные», а в `title` перечень множителей из `inflation_factors`
      («ДГП × 1.083 · ДС №1 × 1.000», решение плана №1). Тест утверждает
      **содержимое подсказки**, а не факт её наличия: чип без разбивки сделал бы
      арифметику колонки непроверяемой.
- [ ] **Step 5:** `InflationRefusalBanner` — общий каркас, **два разных текста
      по коду отказа, и кнопка только у одного из них**:

      | Код | Текст | Кнопка |
      |---|---|---|
      | `missing_inflation_years` | «в ряду «…» нет коэффициентов за 2024, 2025» | «Заполнить недостающие годы» — открывает то же окно на этих годах |
      | `amendment_date_missing` | «у допсоглашений <номера> нет собственной даты; дату договора подставлять нельзя» | **нет** — правкой ряда это не лечится |

      Предлагать «заполнить годы» там, где не хватает даты ДС, значит звать
      человека делать работу, которая ничего не исправит. Оба случая: номинальный
      вариант дозапрашивается и показывается, **параметры URL сохраняются**,
      переключатель визуально выключен. Тесты — на оба кода, включая
      **отсутствие** кнопки у второго.
- [ ] **Step 6:** кнопки правки **не отрисованы** для `member`
      (`renderWithProviders(..., { initialUser: { role: "member" } })`) —
      проверяется отсутствием узла, а не его заблокированностью (DoD 35).
- [ ] **Step 7:** тесты DoD 34: строка ряда, полоса и баннер рендерят **один
      именованный компонент**; два входа на `/compare` управляют **одним
      экземпляром** (открытие из полосы и из баннера дают то же состояние);
      `document.querySelectorAll("[role=dialog]").length === 1` — как проверка
      «двух окон разом не бывает», а не как доказательство переиспользования.
- [ ] **Step 8:** тест DoD 36: после «Сохранить» сравнение перезапрашивается и
      показывает числа по новым коэффициентам (MSW отдаёт второй ответ).
- [ ] **Step 9:** тест DoD 31 на экране: смена ряда меняет подпись оси И числа
      (два ряда с разными коэффициентами).
- [ ] **Step 10:** `npx vitest run` и `npx tsc -b --noEmit` из `frontend/`.

**Коммит:** `feat(inflation): группа приведения, полоса уровней и баннер отказа на /compare`

---

## Task 15: `AGENTS.md` → v6.10

Правки перечислены спекой §2.13 и являются задачей ПЛАНА, а не отдельной фичей.

- [ ] **Step 1:** врезка v6.10 первой в блоке цитат, со ссылками на спеку и
      devlog. Changelog обязан назвать именно **добавление инварианта**, а не
      редакционную правку.
- [ ] **Step 2:** §7 п. 3 — на экране нормативов появляется ряд индексов
      инфляции.
- [ ] **Step 3:** §7 п. 7 — в URL страницы сравнения, кроме режима НДС и ставки,
      живут выбранный ряд и целевой месяц.
- [ ] **Step 4:** §10 — новый совместимый инвариант: много-договорные
      СРАВНИТЕЛЬНЫЕ поверхности вправе дополнительно приводиться к ценовому
      уровню выбранного месяца, и **ряд с месяцем объявляются на самой
      поверхности** — тем же правилом, каким объявляется налоговый состав.
      Действующие инварианты не пересматриваются: ось сравнения остаётся нетто.

**Коммит:** `docs: AGENTS.md v6.10 — ценовой уровень как вторая ось денежных величин`

---

## Task 16: Прогон на стенде, devlog, PR

- [ ] **Step 1:** `just ci` целиком, шаги **по отдельности**; `$LASTEXITCODE`
      снимать сразу после каждой команды.
- [ ] **Step 2:** стенд `gca_dev`: завести ряд «Росстат, ИПЦ, декабрь к декабрю»
      с 2024–2026 и проверить на пяти договорах, что множители совпадают с
      §2.5 спеки (1.174412 … 1.004868). Числа стенда — датированный снимок:
      **пересчитывать, а не верить записанному**.
- [ ] **Step 3:** проверить браузером (playwright-core, `channel: "chrome"`):
      группа приведения одной группой, полоса уровней, чип уровня, отказ с
      сохранёнными параметрами URL, окно правки из трёх входов, отсутствие
      горизонтального переполнения. «Без перезагрузки» доказывать счётчиком в
      `sessionStorage` плюс событиями `load`.
- [ ] **Step 4:** пересобрать макет командой из §7 спеки, если стенд изменился.
- [ ] **Step 5:** соответствие «требование спеки → тест» построить **списком по
      всем 39 пунктам DoD** (таблица ниже — заготовка, на финале сверяется
      прогоном, а не по памяти). Требование без теста либо получает тест, либо
      объявляется границей в devlog.
- [ ] **Step 6:** devlog `docs/devlog/2026-08-18-inflation-adjustment.md`: что
      сделано, замеры, отступления от плана, найденные грабли.
- [ ] **Step 7:** PR со ссылками на спеку и план. Мерж делает пользователь.

---

## Соответствие DoD спеки → задача

| DoD | Задача | DoD | Задача | DoD | Задача |
|---|---|---|---|---|---|
| 1 | 1 | 14 | 8 | 27 | 5 |
| 2 | 7, 8 | 15 | 16 (утверждение в devlog) | 28 | 5, 13 |
| 3 | 2 | 16 | 3, 5, 13 | 29 | 5 |
| 4 | 2 | 17 | 3 | 30 | 6 |
| 5 | 2, 8 | 18 | 2, 9 | 31 | 8 (снятие), 14 |
| 6 | 2 | 19 | 9 | 32 | 10, 14 |
| 7 | 7, 14 | 20 | 9 | 33 | 8, 14 |
| 8 | 7 (снятие) | 21 | 4, 7 | 34 | 12, 14 |
| 9 | 14 | 22 | 4 | 35 | 14 |
| 10 | 7 | 23 | 4, 9 | 36 | 11, 14 |
| 11 | 9 | 24 | 6 | 37 | 5 |
| 12 | 8 | 25 | 12 (снятие) | 38 | 8 |
| 13 | 10 | 26 | 5 (снятие) | 39 | 16 |

**Три негативные проверки снятием защиты — задачи 5, 7, 12 — делает оркестратор
лично.** Снятие обязано быть тем, которое МОЖЕТ случиться; после возврата файл
сверяется по `SHA256`; в пробнике правки обязателен `assert old in source` перед
заменой.

---

## Границы (в эту ветку не тащить)

Названы, чтобы не выглядели недосмотром: исправление сквозной матрицы
(`TECH_DEBT.md` 15 — берёт последнюю дельту ДС вместо базы); снятие исключения
договоров с ДС на дашборде; удаление медианы корзины ДС; ослабление
`ck_estimate_additional_works_total_amount`; отсутствие расшифровки уровня в
существующем `ReapproveDialog` (та же уязвимость ×13, но чужая поверхность —
внешний круг признал это вне границ фичи); персональный гипотетический ряд для
`member`; импорт ряда из API. Если реализация упрётся в любое из них — сказать,
но не чинить заодно.
