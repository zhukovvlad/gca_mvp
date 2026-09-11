# План: правило цены и две оси состояния ячейки матрицы

**Спека:** `docs/superpowers/specs/2026-09-09-price-predicate-design.md`
**Ветка:** `fix/price-predicate`

## Global Constraints

- **Деньги.** `numeric` ↔ `Decimal` ↔ строки в JSON, никаких `float`
  (`AGENTS.md` §3). Пустая стоимость остаётся `NULL`, ноль остаётся нулём: §3 не
  трогается ни одной задачей.
- **Ось сравнения — нетто.** Отклонение считает Python, VIEW отдаёт только входы
  (§4). Порядок «нетто → инфляция → ставка показа» не меняется.
- **Инвариант «пустое отклонение имеет разные причины, и UI обязан их
  различать»** (§4) сохраняется полностью; у агрегированной ячейки матрицы
  меняется поле-носитель, и это ревизия §4 (спека §2.7).
- **Контракт позиционных поверхностей не меняется.** Ключевые ставки паспорта,
  отчёты и `_net_deviation` продолжают возвращать `deviation_reason` с
  сегодняшним перечнем значений; `no_rate` там не появляется. **Состав** их
  выдачи меняется ровно на действие предиката: позиция без пригодной цены
  выпадает. Посимвольное совпадение с дофичевым обещано только на сметах, где
  все позиции несут пригодную цену, — на стенде это все сметы.
- **Счётчики полноты итогов паспорта не меняют смысл.** `positions_rows_priced`,
  `rows_priced`, `own_rows_priced`, `rows_with_amount`, `rate_coverage` остаются
  про конечный `total_cost_total`.
- **Роли.** Фича не заводит новых прав.
- **Миграции не правятся задним числом** (§9). `0016` добавляется; текст `0012`
  не меняется, `downgrade` восстанавливает его дословно.
- **Правило живёт в одном экземпляре.** Одна функция предиката; там, где копия
  неизбежна (замороженный текст миграции), она названа и стережётся тестом
  поведения, а не сверкой текста.
- **Промоушен каталога в эту фичу не входит.** Ни одна задача не меняет `kind`
  каталожных строк и не трогает `backend/services/matching.py`.
- **Ветка зелёная после каждой задачи.** Порядок подчинён этому требованию:
  читатели готовы раньше, чем сужается VIEW (см. решение 3).

## Структура файлов

```
backend/
  money/price.py                                   СОЗДАЁТСЯ
  alembic/versions/2026_09_09_0016-price_predicate.py  СОЗДАЁТСЯ
  crud/analytics.py                                ПРАВИТСЯ
  crud/comparison.py                               ПРАВИТСЯ
  crud/project_passport.py                         ПРАВИТСЯ
  services/article_rates.py                        ПРАВИТСЯ
  services/estimate_import.py                      ПРАВИТСЯ
  tests/unit/test_price_predicate.py               СОЗДАЁТСЯ
  tests/unit/test_article_rates.py                 ПРАВИТСЯ
  tests/integration/test_price_predicate_sql.py    СОЗДАЁТСЯ
  tests/integration/test_deviations_view.py        ПРАВИТСЯ
  tests/integration/test_analytics_api.py          ПРАВИТСЯ
  tests/integration/test_project_passport_api.py   ПРАВИТСЯ
  tests/integration/test_passport_article_rates.py ПРАВИТСЯ
  tests/integration/test_comparison_medians.py     ПРАВИТСЯ
  tests/integration/test_estimate_import.py        ПРАВИТСЯ
frontend/src/
  types/domain.ts                                  ПРАВИТСЯ
  pages/matrix/MatrixPage.tsx                      ПРАВИТСЯ
  pages/matrix/MatrixPage.test.tsx                 ПРАВИТСЯ
  components/matrix/MatrixCellDialog.tsx           ПРАВИТСЯ
  components/matrix/MatrixCellDialog.test.tsx      ПРАВИТСЯ
  pages/passport/rateLabels.ts                     ПРАВИТСЯ
  pages/passport/PassportRates.test.tsx            ПРАВИТСЯ
  test/fixtures.ts                                 ПРАВИТСЯ
AGENTS.md                                          ПРАВИТСЯ
docs/reference/screens.md                          ПРАВИТСЯ
docs/devlog/2026-09-09-price-predicate.md          СОЗДАЁТСЯ на финале
```

## Решения плана, которых нет в спеке

1. **Предикат живёт в новом модуле `backend/money/price.py`.** Пакет `money/`
   чистый — SQLAlchemy не импортирует; поэтому Python-предикат там, а
   SQL-выражение остаётся в `crud/analytics.py` рядом с `_NET_COST`.
2. **SQL-предикат — функция от колонки, а не готовое выражение.**
   `_price_ok(column)` и `_weight_ok(column)` вместо констант над
   `DEVIATION_INPUTS`: задачам нужен тот же предикат над `PositionItem`, где
   лежат в том числе строки, из VIEW исключённые. Правило одно, площадок
   применения несколько.
3. **Порядок: ВСЕ читатели готовы раньше, чем сужается VIEW.** VIEW читают три
   поверхности: агрегаты матрицы, `key_rates` паспорта и drill-down. Задача 2
   готовит первую, задача 3 — две остальные, и только задача 4 сужает VIEW,
   поведения читателей уже не меняя. Иначе после миграции suite был бы красным
   трижды: существующие тесты требуют видимую нефинитную ячейку с поднятым
   флагом, нефинитную позицию в `key_rates` и её же в `items` drill-down.
4. **Фильтры выборки на стороне присутствия — существующий
   `column_scope_filters`**, выраженный через `Contract` и `latest`, а не
   VIEW-привязанный `scope_filters`. Второго правила даты не заводится.
5. **Имя поля ячейки — `rate_reason`, не `rate_state`.** `rate_state` уже занят:
   так называется состояние ставки статьи в паспорте (`categories[].rate_state`,
   тип `RateState`, десять значений). Два разных факта под одним именем на двух
   поверхностях — прямое нарушение правила «факт живёт в одном месте». `null`
   в `rate_reason` означает ровно одно: ставка есть; второго смысла у пустоты
   нет. **Спека правится этим же изменением имени** — дизайн не меняется.
6. **Имя нового состояния автомата статьи — `AMOUNT_ZERO`**, в ряду
   `AMOUNT_MISSING`, `VOLUME_MISSING`, `VOLUME_NONPOSITIVE`.
7. **Третья причина пустой матрицы — `positions_without_price`**, в ряду
   `positions_pending_review` и `positions_non_work`.
8. **Downgrade доказывается прогоном, а не `alembic check`.** `just db-test-check`
   делает только `upgrade` и сверку; поведение `downgrade` проверяет именованный
   интеграционный тест.
9. **Фронт правится одной задачей**: три поверхности делят один изменившийся тип.
10. **Devlog и правка дорожной карты — на финале**, вне задач плана.

## Задачи

### Task 1: Предикат цены и предикат веса в одном экземпляре

**Files**
- Create: `backend/money/price.py`
- Create: `backend/tests/unit/test_price_predicate.py`
- Create: `backend/tests/integration/test_price_predicate_sql.py`
- Edit: `backend/crud/analytics.py`

**Interfaces**
- Потребляет: `Decimal` (stdlib), `sa` (SQLAlchemy), `PositionItem`
  (существует, `backend/models.py`), `DEVIATION_INPUTS` (существует).
- Производит:

```python
# backend/money/price.py
def is_price(value: Decimal | None) -> bool
def is_weight(value: Decimal | None) -> bool

# backend/crud/analytics.py
def _price_ok(column) -> sa.ColumnElement[bool]
def _weight_ok(column) -> sa.ColumnElement[bool]
```

**Утверждения**
- `is_price` истинна ровно на конечном значении больше нуля; ложна на `None`,
  нуле, отрицательном, `NaN`, `+Infinity`, `-Infinity` — семь входов, все
  предъявлены;
- `is_price` **не бросает** на `NaN`: конечность проверяется до сравнения, иначе
  `Decimal("NaN") > 0` даёт `InvalidOperation`;
- `is_weight` истинна ровно на конечном значении больше нуля; те же семь входов;
- `_price_ok` применим **и** к колонке `DEVIATION_INPUTS`, **и** к колонке
  `PositionItem` — оба применения предъявлены;
- SQL и Python дают одинаковый ответ на всех семи входах: сверка гонит те же
  значения через выражение в БД и через помощника;
- вход `NaN` отличает предикат от наивного «больше нуля»: без проверки
  конечности SQL на нём возвращает истину, а Python бросает.

**Имена**
- Заводятся этой задачей: `money.price`, `is_price`, `is_weight`, `_price_ok`,
  `_weight_ok`.
- Существуют, проверено `grep`-ом: `DEVIATION_INPUTS`, `_NET_COST`,
  `_NOT_FINITE_COST` (`backend/crud/analytics.py`); `PositionItem`
  (`backend/models.py`); прецедент «`is_finite()` ДО сравнения диапазона» —
  разбор ставки НДС в `backend/services/estimate_import.py`; прецедент парной
  сверки SQL и Python — `test_sql_net_weight_agrees_with_python`.

**Проверка**
- `just test-unit-k price_predicate` — зелёная.
- `just test-int-local-k price_predicate` — зелёная.

---

### Task 2: Совокупность по присутствию, две оси состояния, неполнота

Одна задача, потому что три части неразделимы: строки без цены появляются в
выдаче только вместе с полем, объясняющим их состояние, и вместе с правилом,
считающим вес такой строки.

**Files**
- Edit: `backend/crud/analytics.py`
- Edit: `backend/tests/integration/test_analytics_api.py`

**Interfaces**
- Потребляет: `_price_ok`, `_weight_ok` (задача 1); `latest_estimates`,
  `column_scope_filters`, `_cell_groups_cte`, `_cell_weights_cte`, `_fold_cell`,
  `get_matrix`, `row_totals`, `row_amount_incomplete` (существуют);
  `PositionItem`, `CatalogPosition`, `Contract` (существуют).
- Производит:

```python
def _presence_cte(*, rate_class_id, date_from, date_to, latest)  # каталожная строка × договор

# поле ячейки в ответе get_matrix
rate_reason: Literal[
    "unknown_vat_base", "not_finite", "no_weight", "negative_only", "no_price",
] | None            # None == ставка есть

# перечень deviation_reason У ЯЧЕЙКИ МАТРИЦЫ
Literal["no_standard", "not_finite", "no_rate"] | None
```

**Утверждения**
- Работа, представленная во всей выборке **исключительно** позициями без цены,
  становится строкой матрицы: она есть на странице, входит в `total` и находится
  текстовым поиском;
- отсутствие объекта ячейки по-прежнему означает «работы нет в смете этого
  договора»; присутствие работы без пригодной цены даёт объект ячейки с пустой
  ставкой;
- каждая из восьми строк таблицы спеки §2.5 предъявлена своим входом и даёт
  объявленную пару `rate_reason` и `deviation_reason`;
- каждый из шести смешанных наборов спеки §2.5 предъявлен своим входом; набор
  «положительная плюс нулевая» даёт ставку, то есть нулевая позиция её **не
  гасит**;
- `rate_reason` пуст **тогда и только тогда**, когда ставка есть; при непустом
  `rate_reason` поле `deviation_reason` равно `no_rate` и никогда не пусто;
- неполнота: флаг поднимается на **четырёх** входах — исключённая позиция с
  конечной ненулевой ценой и пригодным весом; с нефинитной ценой при весе больше
  нуля; с нефинитным весом; **с пригодной ценой и конечным отрицательным
  весом** (вклад ненулевой, вес непригоден). Флаг **не** поднимается на двух
  входах — исключённая позиция с нулевой либо пустой ценой; с нулевым либо
  пустым весом;
- отрицательный вес даёт `rate_reason` равный `no_weight`, если других входящих
  позиций в ячейке нет: вес непригоден по тому же правилу, что нуль и пустота;
- флаг поднимается при неизвестной базе НДС у входящей позиции — как сегодня;
- комбинация «ставка есть и флаг поднят» достижима и предъявлена;
- нефинитная позиция поднимает флаг, **находясь на стороне присутствия**, а не
  через `bool_or` по строкам VIEW: после задачи 3 её в VIEW не будет, и правило
  обязано работать без неё уже здесь;
- правило неполноты предъявлено красным: снятие условия «исключённая позиция с
  ненулевым вкладом» роняет именно тот тест, который его стережёт;
- сортировка не меняется: строка без вычислимой суммы стоит после строк с
  суммой;
- фильтры выборки на стороне присутствия дают тот же состав договоров, что и
  колонки матрицы: второго правила даты не заводится.

**Имена**
- Заводятся этой задачей: `_presence_cte`, `rate_reason`, значения
  `negative_only`, `no_price`, `no_rate`.
- Существуют, проверено `grep`-ом: `latest_estimates`, `column_scope_filters`,
  `scope_filters`, `_cell_groups_cte`, `_cell_weights_cte`, `_fold_cell`,
  `get_matrix`, `matrix_columns`, `row_totals`, `cell_unknown`,
  `cell_not_finite`, `_NET_COST`, `_NOT_FINITE_COST`
  (`backend/crud/analytics.py`); `row_amount_incomplete`, `unknown_vat_base`,
  `no_weight`, `not_finite`, `no_standard` (там же и
  `frontend/src/types/domain.ts`).

**Проверка**
- `just test-int-local-k matrix` — зелёная.
- `just test-backend-parallel` — зелёная (полный backend-suite после смены
  контракта ячейки).

---

### Task 3: Позиционные читатели — предикат и расщепление запроса

Идёт **до** миграции: `key_rates` паспорта и drill-down читают VIEW напрямую, и
сужение VIEW раньше их адаптации оставило бы suite красным.

**Files**
- Edit: `backend/crud/analytics.py`
- Edit: `backend/tests/integration/test_analytics_api.py`
- Edit: `backend/tests/integration/test_project_passport_api.py`

**Interfaces**
- Потребляет: `_priced_positions_select`, `get_matrix_cell`, `_cell_item`,
  `_net_deviation`, `quantize_money` (существуют); `_price_ok`, `_weight_ok`
  (задача 1).
- Производит:

```python
def _all_positions_select(estimate_id: int) -> sa.Select   # без фильтра цены
# _priced_positions_select получает _price_ok и перестаёт зависеть от фильтра VIEW

# поля элемента drill-down
included: bool
excluded_reason: Literal["no_price", "negative", "not_finite", "no_weight"] | None
```

**Утверждения**
- `_priced_positions_select` отбирает позиции **своим** предикатом, а не
  фильтром VIEW: после задачи её результат не изменится от сужения VIEW в
  задаче 4 — это отдельное утверждение, а не следствие;
- `key_rates` паспорта **не содержит** позицию с нефинитной ценой, и эндпоинт
  при этом отвечает 200, а не падает: исходная защита от `InvalidOperation`
  сохраняется, меняется только состав списка;
- `key_rates` не содержит позицию с нулевой и с отрицательной ценой;
- на смете, где **все** позиции несут пригодную цену, `key_rates` возвращает тот
  же список, что до задачи, — посимвольно. Оговорка обязательна: на смете с
  непригодными ценами состав меняется, и в этом смысл фичи;
- drill-down ячейки, у которой `rate_reason` равен `no_price`, возвращает
  **непустой** список строк, и у каждой `included` ложно с названной причиной;
- сумма нетто-вкладов вошедших строк равна полю `amount` ячейки;
- это же значение, делённое на сумму пригодных весов вошедших строк, равно полю
  `rate` ячейки. Два утверждения, а не одно: складывать вклады со ставкой
  размерностно нельзя;
- причина невхождения различает четыре случая и не сливает их в один;
- `deviation_reason` позиционных поверхностей сохраняет сегодняшний перечень
  значений: `no_rate` там не появляется.

**Имена**
- Заводятся этой задачей: `_all_positions_select`, `included`,
  `excluded_reason`.
- Существуют, проверено `grep`-ом: `_priced_positions_select`,
  `get_matrix_cell`, `_cell_item`, `_net_deviation`, `quantize_money`
  (`backend/crud/analytics.py`); `MatrixCellItem`, `MatrixCellDetail`
  (`frontend/src/types/domain.ts`).

**Проверка**
- `just test-int-local-k matrix_cell` — зелёная.
- `just test-int-local-k passport` — зелёная.

---

### Task 4: Миграция 0016 — предикат цены в VIEW отклонений

Поведение читателей этой задачей **не меняется**: задачи 2 и 3 уже применяют
предикат сами. Миграция приводит VIEW в соответствие §4 и снимает зависимость
от того, что фильтр остался только в одном месте.

**Files**
- Create: `backend/alembic/versions/2026_09_09_0016-price_predicate.py`
- Edit: `backend/tests/integration/test_deviations_view.py`

**Interfaces**
- Потребляет: `v_position_deviation_inputs` и текст
  `V_POSITION_DEVIATION_INPUTS` (существуют, миграция `0012`); ревизия `"0015"`
  как `down_revision` (существует, `2026_08_26_0015-tenders_contour.py`).
- Производит: ту же VIEW с изменённым условием; состав колонок не меняется.

**Утверждения**
- VIEW **не отдаёт** позицию с нулевой, пустой, отрицательной, `NaN`,
  `+Infinity`, `-Infinity` ценой — шесть входов;
- VIEW **отдаёт** позицию с конечной положительной ценой;
- набор колонок VIEW после миграции совпадает с набором до неё — сверка по
  `information_schema`;
- **`downgrade` возвращает поведение `0012`**: после отката позиция с нулевой
  ценой снова отдаётся VIEW. Проверяется именованным тестом, а не
  `alembic check`: тот делает только `upgrade` и сверку метаданных;
- ответы матрицы, `key_rates` и drill-down на одной и той же фикстуре с
  непригодными ценами совпадают до и после миграции — задача поведения
  читателей не меняет.

**Имена**
- Заводятся этой задачей: ревизия `0016`.
- Существуют, проверено `grep`-ом: `v_position_deviation_inputs`,
  `V_POSITION_DEVIATION_INPUTS`, `V_POSITION_DEVIATIONS` (миграция `0012`);
  `revision = "0015"` (миграция `0015`).

**Проверка**
- `just db-test-check` — зелёная.
- `just test-int-local-k deviations_view` — зелёная.
- `just test-backend-parallel` — зелёная (полный backend-suite после сужения VIEW).

---

### Task 5: Счётчики причин пустой матрицы и третья причина

**Files**
- Edit: `backend/crud/analytics.py`
- Edit: `backend/tests/integration/test_analytics_api.py`

**Interfaces**
- Потребляет: `_pending_review_condition`, `_non_work_condition`,
  `_unmatched_counts_select`, `_price_ok` (существуют или заведены задачей 1);
  `PositionItem`, `CatalogPosition` (существуют).
- Производит: поле ответа `positions_without_price` рядом с
  `positions_pending_review` и `positions_non_work`.

Условия выражены SQL-предикатом над колонкой, а не Python-помощником: это части
одного запроса.

**Утверждения**
- Три причины **не пересекаются по построению**, потому что предикат цены делит
  их первым: `positions_without_price` — позиция без пригодной цены, независимо
  от состояния каталожной строки; `positions_pending_review` — позиция **с
  ценой**, чья каталожная строка отсутствует либо в очереди;
  `positions_non_work` — позиция **с ценой**, чья каталожная строка размечена
  как не-работа;
- позиция, которая одновременно без цены и ждёт матчинга, считается **ровно один
  раз** — в `positions_without_price`. Это вход, на котором старое и новое
  разбиение расходятся, и он предъявлен;
- сумма трёх причин и четвёртой корзины («с ценой и работа») равна числу позиций
  сметы с `is_chapter = false` — разбиение проверяется равенством, а не согласием
  счётчиков между собой;
- `positions_without_price` ненулев на смете, где все позиции без цены, а
  каталожные строки размечены как работы;
- сегодняшнее «причина „нет цены“ не срабатывает никогда» больше не выполняется:
  предъявлен вход, где она срабатывает;
- слово «расценённых» в тексте экрана становится правдой: позиция с нулевой
  ценой в `positions_pending_review` больше не попадает.

**Имена**
- Заводятся этой задачей: `positions_without_price`.
- Существуют, проверено `grep`-ом: `positions_pending_review`,
  `positions_non_work`, `_pending_review_condition`, `_non_work_condition`,
  `_unmatched_counts_select` (`backend/crud/analytics.py`); те же два поля в
  `frontend/src/types/domain.ts` и `frontend/src/pages/matrix/MatrixPage.tsx`.

**Проверка**
- `just test-int-local-k unmatched` — зелёная.

---

### Task 6: Состояние `AMOUNT_ZERO` в автомате ставки статьи

**Files**
- Edit: `backend/services/article_rates.py`
- Edit: `backend/crud/project_passport.py`
- Edit: `backend/tests/unit/test_article_rates.py`
- Edit: `backend/tests/integration/test_passport_article_rates.py`

**Interfaces**
- Потребляет: `ArticleFold`, `RateState`, `ArticleRate`, `resolve_rate`,
  `convergence_note`, `is_scalable_unit`, `fold_carrier_rows` (существуют);
  `_carrier_rows_by_category`, `_article_rates` (существуют).
- Производит:

```python
class RateState(StrEnum):
    ...
    AMOUNT_ZERO = "amount_zero"
```

**Утверждения**
- `AMOUNT_ZERO` возвращается ровно на входе «строки-носители есть, сумма конечна
  и равна нулю, единица масштабируема, объём конечен и положителен»;
- проверка стоит **девятой**: после `volume_nonpositive`, до
  `volume_inconsistent`. Свёртка с нулевой суммой и несходящимися детьми даёт
  `AMOUNT_ZERO`, а не `VOLUME_INCONSISTENT`;
- при **той же нулевой сумме** сохраняются шесть соседних состояний, каждое
  своим входом: неизвестная единица; конфликт единиц; немасштабируемая единица;
  отсутствующий объём; неположительный объём; отсутствие строк-носителей;
- состояние `RATE` и нулевая ставка несовместимы: ставка `0 ₽/ед.` не
  возвращается ни на одном входе;
- порядок цепочки стережёт перестановочный тест: перенос проверки на четвёртое
  место роняет утверждения о соседних состояниях.

**Замер по ходу реализации (задача 6).** Утверждение выше сперва обещало ШЕСТЬ
покрасневших тестов. Перенос предъявлен оркестратором лично: краснеют ПЯТЬ.
Шестой сосед — отсутствие строк-носителей — порядок не различает и различать не
может: `no_carrier` стоит второй проверкой и срабатывает на `fold.rows == 0`,
то есть до того, как автомат вообще смотрит на сумму, — где бы ни стояла
проверка нулевой суммы, четвёртой или девятой.

**Правка этой врезки по финальному ревью ветки.** Первая её редакция говорила,
что вход шестого соседа «при той же нулевой сумме предъявлен и назван в тесте
так». Это неверно: у свёртки без строк-носителей сумма не ноль, а `None`
(`amount_ok` ложно), и «та же нулевая сумма» для этого состояния недостижима в
принципе. Тест, заводившийся под него в первой редакции задачи 6, оказался
дословным дублем существующего и был снят кругом правок — то есть отдельного
входа у шестого соседа нет и быть не может. DoD 9 спеки перечисляет шесть
соседей, но на порядок из них работают пять; шестой стережётся существующим
тестом состояния.

**Имена**
- Заводятся этой задачей: `AMOUNT_ZERO`, `amount_zero`.
- Существуют, проверено `grep`-ом: `RateState`, `ArticleFold`, `ArticleRate`,
  `resolve_rate`, `convergence_note`, `is_scalable_unit`, `fold_carrier_rows`,
  `AMOUNT_MISSING`, `VOLUME_MISSING`, `VOLUME_NONPOSITIVE`, `UNIT_MISSING`,
  `UNIT_CONFLICT`, `UNIT_NOT_SCALABLE`, `VOLUME_INCONSISTENT`, `NO_CARRIER`,
  `ADDITIONAL_WORKS`, `RATE` (`backend/services/article_rates.py`);
  `_carrier_rows_by_category`, `_article_rates`
  (`backend/crud/project_passport.py`).

**Проверка**
- `just test-unit-k article_rates` — зелёная.
- `just test-int-local-k passport_article_rates` — зелёная.

---

### Task 7: Два предупреждения импорта

**Files**
- Edit: `backend/services/estimate_import.py`
- Edit: `backend/tests/integration/test_estimate_import.py`

**Interfaces**
- Потребляет: `_money`, `_quantity`, список `problems`, `is_price` (существуют
  или заведены задачей 1).
- Производит: два текста предупреждения; новых полей ответа нет.

**Утверждения**
- Отрицательная цена за единицу даёт предупреждение и **не** роняет импорт;
  значение сохраняется дословно, а не превращается в `NULL`;
- строка, где цена за единицу ноль, а итог по строке не ноль, даёт
  предупреждение и не роняет импорт;
- предупреждения пишет **сессия B**: они описывают состояние домена, поэтому
  откат домена уносит их с собой (§5);
- смета без таких строк этих предупреждений не получает — негативный вход.

**Имена**
- Заводятся этой задачей: тексты двух предупреждений.
- Существуют, проверено `grep`-ом: `_money`, `_quantity`, `problems`
  (`backend/services/estimate_import.py`); правило «предупреждение о состоянии
  домена пишет сессия B» — `AGENTS.md` §5.

**Проверка**
- `just test-int-local-k estimate_import` — зелёная.

---

### Task 8: Медиана сравнения договоров — через общий предикат

**Files**
- Edit: `backend/crud/comparison.py`
- Edit: `backend/tests/integration/test_comparison_medians.py`

**Interfaces**
- Потребляет: `_compute_median`, `_MedianResult`, `comparable_count`,
  `BucketCell` (существуют); `is_price` (задача 1).
- Производит: изменений контракта нет — поведение сохраняется, меняется
  реализация условия и докстрока.

**Утверждения**
- На выборке, где **все** ячейки несут пригодную величину, медиана,
  `comparable_count` и список идентификаторов совпадают с сегодняшними —
  посимвольно. Это граница сохранения поведения, и она названа явно;
- **состав сравнимых меняется** там, где сегодняшнее условие `!= 0` пропускает
  негодное: отрицательная ячейка и нефинитная ячейка теперь исключаются. Оба
  входа предъявлены отдельно, и на каждом падает `comparable_count`;
- исключение сдвигает и саму медиану, и признак «сопоставимых меньше трёх»:
  предъявлен вход, где выборка из трёх ячеек с одной отрицательной перестаёт
  давать медиану;
- второй копии правила исключения нуля в коде не остаётся: сравнение идёт через
  общий предикат;
- докстрока больше не утверждает причину («работ нет либо учтены в другой
  статье») и называет только наблюдаемый факт.

**Имена**
- Заводятся этой задачей: имён не заводится.
- Существуют, проверено `grep`-ом: `_compute_median`, `_MedianResult`,
  `comparable_count`, `BucketCell`, `net_per_sqm` (`backend/crud/comparison.py`).

**Проверка**
- `just test-int-local-k comparison_medians` — зелёная.

---

### Task 9: Фронтенд — состояния ячейки, метки drill-down, третья причина

**Files**
- Edit: `frontend/src/types/domain.ts`
- Edit: `frontend/src/pages/matrix/MatrixPage.tsx`
- Edit: `frontend/src/pages/matrix/MatrixPage.test.tsx`
- Edit: `frontend/src/components/matrix/MatrixCellDialog.tsx`
- Edit: `frontend/src/components/matrix/MatrixCellDialog.test.tsx`
- Edit: `frontend/src/pages/passport/rateLabels.ts`
- Edit: `frontend/src/pages/passport/PassportRates.test.tsx`
- Edit: `frontend/src/test/fixtures.ts`

**Interfaces**
- Потребляет: `MatrixCell`, `MatrixCellItem`, `MatrixCellDetail`, `RateState`,
  `RATE_STATE_LABEL`, `positions_pending_review`, `positions_non_work`
  (существуют).
- Производит:

```ts
// types/domain.ts
export type CellRateReason =
  | "unknown_vat_base" | "not_finite" | "no_weight" | "negative_only" | "no_price";

interface MatrixCell {
  rate_reason: CellRateReason | null;          // null == ставка есть
  deviation_reason: "no_standard" | "not_finite" | "no_rate" | null;
}
interface MatrixCellItem {
  included: boolean;
  excluded_reason: "no_price" | "negative" | "not_finite" | "no_weight" | null;
}

// pages/passport/rateLabels.ts
// RATE_STATE_LABEL пополняется ключом amount_zero
```

**Утверждения**
- `tsc` проходит; карта `RATE_STATE_LABEL` объявлена через `satisfies
  Record<RateState, string | null>`, поэтому забытый ключ `amount_zero` **роняет
  сборку**, а не даёт пустую клетку — это существующая защита, и она обязана
  сработать;
- пять значений `rate_reason` дают пять различных подписей ячейки, ни одна не
  повторяется;
- ячейка «нет цены» отличима от «работы нет в смете»: у первой есть объект
  ячейки, у второй его нет;
- drill-down показывает невошедшие строки с причиной невхождения;
- пустая матрица с третьей причиной показывает её текст, а не текст одной из
  двух прежних;
- подпись состояния статьи в паспорте — «сумма статьи равна нулю».

**Имена**
- Заводятся этой задачей: `CellRateReason`, `rate_reason`, `included`,
  `excluded_reason`, подписи пяти состояний ячейки, подпись третьей причины,
  ключ `amount_zero` в карте подписей.
- Существуют, проверено `grep`-ом: `MatrixCell`, `MatrixCellItem`,
  `MatrixCellDetail`, `deviation_reason`, `RateState`
  (`frontend/src/types/domain.ts`); `RATE_STATE_LABEL`, `RATE_NOTE_LABEL`
  (`frontend/src/pages/passport/rateLabels.ts`); `positions_pending_review`,
  `positions_non_work` (`frontend/src/pages/matrix/MatrixPage.tsx`).

**Проверка**
- `just typecheck-frontend` — зелёная.
- `just lint-frontend` — зелёная.
- `just test-frontend` — зелёная.

---

### Task 10: Ревизия `AGENTS.md` и справочника

**Files**
- Edit: `AGENTS.md`
- Edit: `docs/reference/screens.md`

**Interfaces**
- Потребляет: `backend/scripts/check_agents_index.py` (существует),
  `docs/AGENTS-revisions.md` (существует).
- Производит: очередную ревизию; номер присваивается в этом коммите.

**Утверждения**
- §4: список исключений VIEW назван предикатом цены; заведены две оси состояния
  агрегированной ячейки; сказано прямо, что смена носителя `unknown_vat_base`
  относится **только** к ней, а позиционные поверхности не меняются;
- §6: формула ячейки исключает непригодные цены; совокупность строк задана
  присутствием; перечислены значения `rate_reason` и два смысла пустой ячейки;
- §3 не тронут ни одним символом;
- `screens.md`, экран матрицы: описаны состояния ячейки, метки drill-down и три
  причины пустой матрицы;
- действующая ревизия ровно одна, предыдущая уехала в архив; страж зелёный
  после коммита — до него упоминание несуществующей версии красно законно.

**Имена**
- Заводятся этой задачей: номер очередной ревизии.
- Существуют, проверено `grep`-ом: `check_agents_index.py`
  (`backend/scripts/`); `docs/AGENTS-revisions.md`; `docs/reference/screens.md`.

**Проверка**
- `just check-agents-index` — зелёная.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: `just ci` (§9.3). Полный прогон — около 8,5 минут.
- На стенде: паспорт и сравнение договоров проверяются на живых данных;
  **матрица на стенде остаётся пустой до фичи 2**, и это записано в DoD 8 спеки,
  а не обходится.
