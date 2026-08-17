# Сравнение договоров — план реализации

> **Для исполнителя:** задачи идут по порядку, каждая замкнута и заканчивается
> коммитом. Шаги с чекбоксами (`- [ ]`) отмечаются по мере выполнения.

**Цель:** страница `/compare` ставит рядом несколько договоров: строки — дерево
статей классификатора, колонки — договоры, в ячейке суммы и ₽/м²; тот же агрегат
выгружается в Excel.

**Спека:** [docs/superpowers/specs/2026-08-17-contract-comparison-design.md](../specs/2026-08-17-contract-comparison-design.md)
— план аргументирует от неё, читать обе.

**Ветка:** `feat/contract-compare-scaffold` (спека и макет закоммичены).

> **СТАТУС: гейт 3 НЕ пройден.** Задачи 1–3 доведены до архитектуры и
> интерфейсов; задачи 4–9 — структурные заголовки, код тестов в них ещё не
> написан. Детализировать 4–9 до одобрения основания преждевременно: два круга
> внешнего ревью показали, что ошибка в основании обесценивает расписанные под
> неё шаги.

---

## Архитектура

Три слоя, и границы между ними — не оформление:

1. **Чтение и роллап.** `v_category_totals` читается **одним запросом на всю
   выборку**, строки **накапливаются** по ключу «смета × статья × источник» и
   переводятся в нетто по базе своей группы. Результат — `EstimateRollup` на
   смету: дерево `CategoryNode`, прямые суммы по источникам, свёрнутые причины
   неполноты, «Нераспределённое» отдельно.
2. **Союз и ячейки.** Поверх роллапов — союз узлов выборки, состояния ячеек
   (`ABSENT` / `ZERO` / `VALUE`), синтетические строки.
3. **Корзины, режимы, медианы.** Один агрегат считает **сразу все три корзины**;
   у каждой свои нетто, показ, ₽/м², медиана, отклонение и причины. Экран
   выбирает представление, Excel читает те же три результата.

**Нетто — основание, а не один из режимов.** Медиана всегда нетто (спека §2.5
правило 2), поэтому нетто считается один раз, а режим показа применяется
множителем поверх: «единая ставка» — общим множителем к готовой нетто-корзине,
«своя ставка» — множителем **на каждую смету до сложения** в корзину.

### Три ошибки основания, найденные внешним ревью до написания кода

Записаны здесь, потому что каждая выглядела правдоподобно и была бы дорогой:

1. **Затирание строк VIEW.** `bucket[r.source] = DirectTotals(...)` теряет строки:
   VIEW группируется ещё по `proposal_id` и `vat_rate_base` (миграция 0012,
   `GROUP BY l.estimate_id, p.id, …`), поэтому на одну статью и один источник
   строк может быть несколько. Накапливать, а не присваивать.
   **Замер: сегодня дубликатов нет** — 290 строк на 290 ключей, у каждой сметы
   один лот и одно предложение. Дефект латентный: §4 разрешает много лотов, и он
   проявится на первой такой смете. «На стенде не воспроизводится» здесь не
   аргумент.
2. **Ложная причина «без цены».** `rows_with_amount=0 if net is None` смешивает
   два разных факта: цена есть, а база НДС неизвестна — это `vat_base_unknown`,
   и `rows_with_amount` при этом равен `rows`. Обнуление добавляло бы вторую,
   ложную причину `unpriced_rows`, а после `build_tree` различить их стало бы
   невозможно: `CategoryNode` не несёт причин.
3. **Собственные деньги вычитанием.** `node.total - sum(children)` — ровно то,
   что запрещают и спека §2.1, и докстрока `category_rollup`: результат вычитания
   выдаётся за прямой факт. Собственные деньги — это
   `direct[positions].amount + direct[additional_works].amount`, и обе ветви
   обязаны сохраниться **до** `build_tree`.

Отсюда вывод, определяющий слой 1: **`CategoryNode` недостаточен как
единственная структура результата.** `build_tree` переиспользуется для арифметики
поддеревьев и порядка, но нести факты, которых в нём нет (причины неполноты,
разделённые источники), его заставлять нельзя.

---

## Global Constraints

- **Миграций у фичи нет.** Схема не меняется.
- **`CategoryRef` объявлен в ДВУХ модулях, и они разные.**
  `services.category_resolution.CategoryRef` — два поля (`id`, `title`);
  `services.category_rollup.CategoryRef` — шесть (`id`, `code`, `title`,
  `parent_id`, `is_bucket`, `sort_order`). Нужен **второй**. Импорт первого даст
  невнятную ошибку конструктора.
- **Деньги** — `Decimal` в Python, строки в JSON; эндпоинты с деньгами идут через
  `responses.decimal_json:93` (§10 `AGENTS.md`).
- **Ось сравнения — всегда нетто**, в любом режиме показа.
- **`SECRET_KEY` не короче 32 символов**, иначе `Settings` падает на сборке
  conftest, а не на тестах.
- **`pytestmark = pytest.mark.integration`** обязателен каждому новому файлу в
  `backend/tests/integration/`.
- **Фронтенд из `frontend/`**; типы — `npx tsc -b --noEmit`.
- **Только shadcn/ui**; `checkbox`, `table`, `select`, `label`, `button`, `switch`
  уже есть в `frontend/src/components/ui/`.
- **prettier не настроен** — не запускать.
- **Перед пушем `just ci`**, шаги по отдельности. Правки только в `docs/`
  освобождены (§9.3).

**Команда бэкенд-тестов** (PowerShell, из `backend/`):

```
$env:PYTHONIOENCODING='utf-8'; $env:DATABASE_URL='postgresql+psycopg://postgres@localhost:5459/postgres'; $env:TEST_DATABASE_URL='postgresql+psycopg://postgres@localhost:5459/gca_test'; $env:SECRET_KEY='test-secret-key-for-ci-0123456789abcdef'; uv run pytest <путь> -v
```

**Состояние стенда изменилось после написания спеки.** На 2026-08-17 в `gca_dev`
осталось **три** договора (`СДП-1-МР`, `12-СИТ-МР`, `ТЕСТ`) и 290 строк VIEW;
спека §6 описывает пятидоговорное состояние (474 строки). Числа спеки —
исторические. **DoD 12** («Благоустройство, дороги» без подсветки) привязан к
удалённым данным и на приёмке проверяется пересчётом, а не сверкой с текстом.

---

## Файловая карта

| Файл | Что с ним |
|---|---|
| `AGENTS.md` | ревизия v6.8 (§10, §7, §7.6) — **задача 1** |
| `backend/crud/comparison.py` | **новый**: роллап выборки, союз, корзины, режимы, медианы |
| `backend/routers/analytics.py` | эндпоинт `GET /comparison` |
| `backend/services/excel_comparison.py` | **новый**: лист сравнения |
| `backend/routers/reports.py` | эндпоинт выгрузки |
| `backend/tests/integration/test_comparison_rollup.py` | **новый**: слой 1 |
| `backend/tests/integration/test_comparison_rows.py` | **новый**: слой 2 |
| `backend/tests/integration/test_comparison_buckets.py` | **новый**: слой 3 |
| `backend/tests/integration/test_comparison_api.py` | **новый**: эндпоинт |
| `backend/tests/integration/test_comparison_excel.py` | **новый**: лист |
| `frontend/src/pages/contracts/ContractsPage.tsx` | чекбоксы, две кнопки, сборка URL |
| `frontend/src/pages/compare/ComparePage.tsx` | **новая** страница |
| `frontend/src/App.tsx`, `services/queryKeys.ts`, `services/queries.ts`, `services/api/domain.ts`, `types/domain.ts`, `test/handlers.ts` | **задача 8**, вместе со страницей |
| `docs/devlog/2026-08-17-contract-comparison.md` | **новый** devlog |

**Переиспользуется без правок:** `services.category_rollup` (`build_tree:91`,
`CategoryNode:72`, `CategoryRef:46`, `DirectTotals:56`, `SOURCE_POSITIONS`,
`SOURCE_ADDITIONAL_WORKS`), `money.vat` (`gross_to_net:74`, `restate_gross:92`,
`effective_display_rate:126`, `quantize_money:149`), `responses.decimal_json:93`,
`crud.common.DomainError:26`, `auth.get_current_user:23`, `services.excel`
(`write_cell:124`, `write_banner:149`, `write_column_headers:160`,
`apply_column_widths:171`, `workbook_bytes:176`), `routers.reports._xlsx:43`,
`_safe_filename_part:51`.

**Осознанно НЕ переиспользуется:** `crud.project_passport._direct_totals:587` —
приватная функция чужого модуля, и она restate-ит суммы под одну ставку сметы.
Сравнению нужно нетто, из которого выводятся все три режима.

---

## Task 1: Ревизия `AGENTS.md` до v6.8

**Первой задачей, а не шестой:** пока правка не внесена, вся реализация формально
противоречит действующему инварианту (спека §2.10). Правки только в документе,
`just ci` их не касается (§9.3).

**Files:** Modify: `AGENTS.md`

- [ ] **Step 1: Врезка версии** — первой в блоке цитат, перед v6.7, со ссылками
  на спеку и (позже) devlog.
- [ ] **Step 2: §10, налоговый состав денег** (раздел «Definition of Done»,
  строка 472 — **не §3**, там только правило `Decimal`). Дописать три
  утверждения: у много-договорных **сравнительных** поверхностей ось сравнения
  всегда нетто; **показ** может иметь объявленные режимы, состав объявляется на
  поверхности; в режиме «своя ставка» итог по договору вправе складывать суммы
  разного состава при обязательной подписи — тем же основанием, каким это
  разрешено обзорной главной.
- [ ] **Step 3: §7** — страница сравнения: маршрут `/compare`, вход из списка,
  пункта меню нет.
- [ ] **Step 4: §7.6** — «два отчёта — два файла» становится тремя.
- [ ] **Step 5: Коммит**

```bash
git add AGENTS.md
git commit -m "docs: AGENTS.md v6.8 — режимы показа у сравнительных поверхностей"
```

---

## Task 2: Роллап выборки — чтение VIEW и нетто-деревья

**Files:**
- Create: `backend/crud/comparison.py`
- Test: `backend/tests/integration/test_comparison_rollup.py`

**Interfaces (заводятся этой задачей):**

```python
@dataclass(frozen=True)
class DirectBranch:
    """Прямые суммы одной ветви источника, НАКОПЛЕННЫЕ по группам VIEW."""
    net: Decimal | None          # None = ни одной известной суммы
    rows: int
    rows_priced: int             # исходный rows_with_amount, НЕ обнулённый
    rows_not_finite: int
    rows_vat_base_unknown: int   # отдельный факт, не смешан с rows_priced

@dataclass(frozen=True)
class EstimateRollup:
    estimate_id: int
    contract_id: int
    amendment_no: int | None
    display_rate: Decimal | None            # effective_display_rate, может быть None
    tree: tuple[CategoryNode, ...]          # арифметика и порядок — из build_tree
    direct: dict[int, dict[str, DirectBranch]]   # статья -> источник -> ветвь
    unallocated: dict[str, DirectBranch]         # «Нераспределённое», пусто = {}
    reasons: dict[int, frozenset[str]]      # статья -> причины неполноты ПОДДЕРЕВА

def load_rollups(db: Session, contract_ids: Sequence[int]) -> dict[int, list[EstimateRollup]]:
    """Роллапы всех смет выборки, ОДНИМ запросом к VIEW. Ключ — contract_id."""

def own_net(rollup: EstimateRollup, category_id: int) -> Decimal | None:
    """Собственные деньги статьи: positions + additional_works, БЕЗ вычитания."""
```

- [ ] **Step 1: Тест накопления строк VIEW**

Первый тест — про дефект, который ревью нашло раньше кода. Фикстура: одна смета,
**два лота с двумя предложениями**, обе строки по одной статье и одному источнику.

```python
def test_view_rows_are_accumulated_not_overwritten(db_session, factories):
    """Две группы VIEW по одной статье складываются, а не затирают друг друга.

    VIEW группируется по `proposal_id` и `vat_rate_base` (миграция 0012), поэтому
    на статью и источник строк бывает несколько. Замер 2026-08-17: на стенде
    дубликатов нет (290 строк на 290 ключей, один лот на смету), то есть дефект
    латентный и живыми данными не ловится — фикстура обязана быть искусственной.
    """
    estimate = factories.EstimateFactory.create()
    _seed_two_lots_same_category(db_session, factories, estimate, "1", "100.00", "200.00")
    db_session.flush()

    rollups = cmp.load_rollups(db_session, [estimate.contract_id])
    rollup = rollups[estimate.contract_id][0]
    branch = rollup.direct[_category_id(db_session, "1")][cmp.SOURCE_POSITIONS]

    assert branch.rows == 2, "обе группы обязаны войти в счётчики"
    assert branch.net == _net("100.00", 20) + _net("200.00", 20)
```

- [ ] **Step 2: Тест разделения причин**

```python
def test_unknown_vat_base_does_not_fake_unpriced(db_session, factories):
    """Цена есть, база НДС неизвестна → ТОЛЬКО vat_base_unknown (спека §2.1.3).

    Обнуление `rows_priced` при неизвестной базе добавляло бы вторую, ложную
    причину `unpriced_rows` — и после `build_tree` различить их было бы уже
    нельзя.
    """
    estimate = factories.EstimateFactory.create()
    _seed_priced_without_vat_base(db_session, factories, estimate, "1", "100.00")
    db_session.flush()

    rollup = cmp.load_rollups(db_session, [estimate.contract_id])[estimate.contract_id][0]
    cid = _category_id(db_session, "1")
    branch = rollup.direct[cid][cmp.SOURCE_POSITIONS]

    assert branch.rows_priced == branch.rows, "цена есть — счётчик не обнуляется"
    assert branch.rows_vat_base_unknown == branch.rows
    assert rollup.reasons[cid] == frozenset({"vat_base_unknown"})
```

- [ ] **Step 3: Тест собственных денег без вычитания**

```python
def test_own_net_sums_two_sources_exactly(db_session, factories):
    """own = positions + additional_works, ТОЧНОЕ равенство (спека §2.1).

    Фикстура ставит допработу на РОДИТЕЛЬСКУЮ статью — именно там вычитание
    «родитель минус дети» дало бы другой ответ.
    """
    estimate = factories.EstimateFactory.create()
    _seed_priced_positions(db_session, factories, estimate, [("3", "500.00"), ("3.1", "100.00")])
    _seed_additional_work(db_session, factories, estimate, "3", "200.00")
    db_session.flush()

    rollup = cmp.load_rollups(db_session, [estimate.contract_id])[estimate.contract_id][0]
    cid = _category_id(db_session, "3")

    assert cmp.own_net(rollup, cid) == _net("500.00", 20) + _net("200.00", 20)
```

- [ ] **Step 4: Тест «один запрос на выборку»**

```python
def test_view_is_read_once_for_the_whole_selection(db_session, factories):
    """N+1 запрещён: «сравнить всё» не должно давать запрос на смету.

    Считается число обращений к VIEW обработчиком `after_cursor_execute` на
    Connection — приём проверен на фиче каскадного удаления.
    """
    ...
```

- [ ] **Steps 5–7:** прогон до красного → реализация → прогон до зелёного.
- [ ] **Step 8: Коммит**

```bash
git add backend/crud/comparison.py backend/tests/integration/test_comparison_rollup.py
git commit -m "feat(comparison): роллап выборки одним запросом, нетто-деревья"
```

---

## Task 3: Союз узлов, состояния ячеек, синтетические строки

**Files:**
- Modify: `backend/crud/comparison.py`
- Test: `backend/tests/integration/test_comparison_rows.py`

**Interfaces (заводятся этой задачей):**

```python
ABSENT, ZERO, VALUE = "absent", "zero", "value"

@dataclass(frozen=True)
class RowRef:
    kind: str            # "category" | "own" | "unallocated"
    category_id: int | None
    code: str
    title: str
    level: int
    parent_code: str | None

def build_rows(rollups: dict[int, list[EstimateRollup]]) -> list[RowRef]:
    """Союз узлов ВЫБОРКИ в порядке sort_order, с синтетическими строками."""
```

Правила, которые обязаны быть в коде (все из спеки, не выдумывать):

1. узел в союзе, если `rows > 0` хотя бы у одного договора выборки; корни — как
   даёт `build_tree` (он включает их всегда), но корень, пустой у всех
   сравниваемых, из союза выпадает (спека §2.1.1);
2. `ABSENT` — узла нет в дереве договора; `ZERO` — есть и нетто равно нулю;
3. строка `kind="own"` — у узла, у которого есть и дети, и собственные деньги;
   союз применяется к ней так же;
4. строка `kind="unallocated"` — одна на таблицу, если остаток непуст хотя бы у
   одного договора.

- [ ] **Step 1: Тест союза** (строка есть у одного → присутствует у всех; порядок
  `sort_order`).
- [ ] **Step 2: Тест `ABSENT` против `ZERO`.**
- [ ] **Step 3: Тест синтетической строки «Без подстатьи»** — одна строка, значение
  равно `own_net`.
- [ ] **Step 4: Тест «Нераспределённого» — проверяются ОБЕ половины названия:**
  строка присутствует **и** входит в «Итого по договору», **и** отсутствует в
  наборе, по которому считается медиана. Проверка только наличия строки —
  недостаточна (замечание ревью).
- [ ] **Steps 5–7:** красное → реализация → зелёное.
- [ ] **Step 8: Коммит**

```bash
git commit -m "feat(comparison): союз узлов выборки, состояния ячеек, синтетические строки"
```

---

## Task 4: Корзины, режимы НДС, медианы — ОДИН агрегат

**Files:** Modify: `backend/crud/comparison.py`;
Test: `backend/tests/integration/test_comparison_buckets.py`

**Интерфейс, исправленный по ревью.** Агрегат считает **сразу три корзины**, а не
выбранную: Excel обязан получить все три с отдельными медианами, а спека требует
один серверный агрегат на экран и лист. Выбор корзины — дело представления.

```python
@dataclass(frozen=True)
class BucketCell:
    net: Decimal | None
    shown: Decimal | None          # net с множителем режима
    per_sqm: Decimal | None        # по нетто, для медианы
    state: str                     # ABSENT | ZERO | VALUE
    deviation_pct: Decimal | None
    incomplete_reasons: frozenset[str]

@dataclass(frozen=True)
class Cell:
    base: BucketCell
    amendments: BucketCell
    total: BucketCell

def build_comparison(db, contract_ids, *, vat_mode, single_rate=None) -> dict:
    """Полный агрегат: колонки, строки, ячейки по ТРЁМ корзинам, медианы, подписи."""

def rate_options(db, contract_ids) -> tuple[list[Decimal], Decimal | None]:
    """Список ставок и предвыбор (спека §2.3)."""
```

Режимы: «единая» — общий множитель к готовой нетто-корзине; «своя ставка» —
множитель **на каждую смету до сложения** в корзину.

**Шаги (код тестов — при детализации):** корзины складываются в итог; медиана
считается **по корзине**; отклонения одинаковы во всех трёх режимах;
`display_rate_undefined` гасит **ячейку корзины, а не столбец** (плюс контрольный
случай «статья только в ДГП» → ноль, не причина); список ставок и предвыбор,
включая край «ставок показа нет ни у одной сметы»; **объект без `area_total_sp`
→ ₽/м² прочерк и вне медианы** (DoD 13 спеки — пробел, найденный само-ревью).

---

## Task 5: Эндпоинт `GET /api/v1/analytics/comparison`

Две формы выборки (`ids` либо `all=1` плюс `q`, `object_id`, `contractor_id`,
`rate_class_id`), порядок колонок `signed_date DESC`, затем `id DESC`; чтение
доступно `admin` и `member`; ответ через `decimal_json`.

---

## Task 6: Excel

`services/excel_comparison.py` плюс эндпоинт в `routers/reports.py`. Три корзины
колонками, у каждой своя сумма, ₽/м² и отклонение; подпись режима, а в «своей
ставке» — состав по договору; числа совпадают с экраном при том же режиме.

---

## Task 7: Список договоров — выбор и сборка URL

**Только `ContractsPage`** (исправлено по ревью): колонка чекбоксов, кнопки
«Сравнить выбранные (N)» и «Сравнить всё по фильтру», сборка адреса. Маршрут,
`queryKeys`, `queries`, `api/domain`, `types` и MSW-обработчик **не здесь** —
страницы ещё нет, и коммит с мёртвым маршрутом либо не собрался бы, либо тащил
недостижимый код.

---

## Task 8: Страница сравнения

`pages/compare/ComparePage.tsx` вместе со всей обвязкой: маршрут в `App.tsx`,
корень `qk.comparison`, `useComparison`, вызов в `api/domain`, типы ответа,
обработчик MSW.

**Предпосылка, требующая замера:** `position: sticky` в jsdom не вычисляется —
закрепление первой колонки проверять классом, а живое поведение на стенде
(задача 9).

---

## Task 9: Приёмка

`just ci` шагами по отдельности; прогон на стенде через Playwright и системный
Chrome (`channel: "chrome"`), перезагрузка ловится счётчиком полных загрузок
документа; devlog по §9.2 с обязательным упоминанием границы инфляции (§4.1
спеки), пустой корзины ДС на живых данных и списка правил, проверенных только
искусственными фикстурами; PR со ссылками на спеку и план.

---

## Что НЕ входит

Миграций нет. «ДС на рассмотрении», ₽/м² полезной площади, печать, drill-down в
позиции, поправка на инфляцию — за скоупом (спека §4). Маршрут `/matrix` без
ссылки в интерфейсе не чинится.
