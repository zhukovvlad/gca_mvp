# План: подозрительные строки договоров

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Спека:** [`docs/superpowers/specs/2026-11-24-suspicious-lines-design.md`](../specs/2026-11-24-suspicious-lines-design.md) (редакция 3)
**Дизайн и макет:** [`docs/superpowers/design/2026-11-24-suspicious-lines.md`](../design/2026-11-24-suspicious-lines.md)
**Ветка:** `feat/suspicious-lines`, черновой PR #104

**Goal:** система находит подозрительные строки смет договоров (выброс к обычной цене по
договорам и цена-двойник) и показывает их администратору списком на `/contracts` и
подсказкой на карточке аналога; администратор решает «ошибка в цене» или «не ошибка».

**Architecture:** чистое ядро `services/suspicious_lines.py` (статистика групп, факты,
состояние) над строками `load_work_history` по всем договорам; индекс — в своём
`StatusCache` со сбросом закрытым списком. Загрузчики, представления и маршруты —
`crud/suspicious_lines.py`, `routers/suspicious_lines.py`; решения «не ошибка» — таблица
`not_error_marks` (миграция 0031). Панель берёт факты аналога из того же индекса.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest; React, TanStack Query v5, shadcn/ui
(`tabs`, `select`, `collapsible`, `alert-dialog`, `textarea`, `badge`, `chart` — уже в
`components/ui/`), recharts, vitest.

## Global Constraints

- Таблица, поля, `CHECK`, коды отказов, пороги и тексты — спека дословно:
  `not_error_marks` (§1 п. 1), `MIN_CONTRACTS = 3`, `MAX_SPREAD = 2`, `OUTLIER_RATIO = 10`,
  `MIN_NOMINAL` (1 ₽, существует), `ln 2` двойника (§2 п. 4); `position_item_not_found` (404),
  `not_analog_not_applicable` (422), `not_suspicious` (422), `suspicion_changed` (409).
- Кандидаты — только строки `load_work_history` (§2 п. 1): своего запроса отбора нет.
- Деньги — `Decimal`, медианы и квартили — точные десятичные; нетто — `gross_to_net`;
  ответ — номинальное нетто, строки-десятичные; правило «цены нет» — только пусто и 0
  (§4 п. 2, ред. 3).
- Индекс сбрасывают только места закрытого списка §1 п. 3; прослойка не-GET в `main.py`
  его не трогает.
- Права: запись и список — `require_admin`; факты в панели видят все.
- Фронт — только shadcn/ui; тексты — `pages/contracts/suspiciousCopy.ts`.
- Режим проверок — по риску (решение пользователя 28.10.2026): критические свойства
  спеки §7 п. 1–7 (с 5а, 6а) снимает оркестратор лично в копии дерева (стенд отдаёт
  живое дерево); узкое ревью (Opus) — у задач 2, 4 и 5 (правило, гонка решения,
  цифры панели); остальные — приёмка оркестратором. Исполнитель гоняет только
  затронутые наборы, полный бэкенд — GitHub Actions. Итоговое ревью — Codex.

## Review Focus

1. Строка в двух группах за жизнь: решение «не ошибка» по семье A, потом каталог переводит
   строку в семью B, где она тоже выброс, — строка снова «не разобрана» с `previous` из
   решения по A, а `PUT` из окна, открытого по A, — `409` — задачи 2 и 4.
2. Группа ровно из трёх договоров, один из которых даёт выброс и получает «ошибку в
   цене»: обычной цены больше нет, двойник этой группы выбирает «обе», строка остаётся в
   `resolved` — задачи 2 и 3.
3. Отметка на строке, которой нет в индексе (комплект, прежняя смета, `NaN`, база
   неизвестна): «Разобраны» показывают её с причиной цены, а не теряют и не падают на
   сериализации — задача 3.
4. Аналог в панели тендера, объект которого — договор этой же строки: факт и цифры те же,
   что в списке (статистика по всем договорам), хотя аналогов этого объекта в панели нет —
   задача 5.
5. Окно «Не ошибка» открыто, кэш индекса истёк и пересчитан с тем же составом — `PUT`
   проходит (сверка по основаниям, а не по версии кэша) — задача 4.

## Структура файлов

- Create: `backend/alembic/versions/2026_11_24_0031-not_error_marks.py`; Edit:
  `backend/models.py` (`NotErrorMark`), `backend/config.py` (`SUSPICION_CACHE_TTL_S`),
  `backend/services/status_cache.py` (`suspicion_cache`).
- Create: `backend/services/suspicious_lines.py` (чистое ядро).
- Create: `backend/crud/suspicious_lines.py` (загрузчики, индекс через кэш, представления,
  запись решений); `backend/routers/suspicious_lines.py`; Edit: `backend/main.py`.
- Edit: `backend/crud/our_price_load.py` (`load_history(exclude_object_id: int | None)`),
  `backend/crud/our_price.py` (`_analogs_view`), `backend/services/estimate_import.py`
  (`_replace_existing`), `backend/services/import_pipeline.py`, `backend/routers/contracts.py`,
  `backend/routers/not_analog.py`, `backend/routers/lump_sum.py` (сброс индекса).
- Create tests: `tests/unit/test_suspicious_rules.py`,
  `tests/integration/test_suspicious_schema.py`, `tests/integration/test_suspicious_api.py`,
  `tests/integration/test_suspicious_panel.py`.
- Frontend: `types/domain.ts`, `services/api/domain.ts`, `services/queryKeys.ts`,
  `services/queries.ts`; Create `pages/contracts/{suspiciousCopy.ts,SuspiciousTab.tsx,
  SuspectItem.tsx,PriceStrip.tsx,TwinPair.tsx}`; Edit `pages/contracts/{ContractsPage.tsx,
  ContractCardPage.tsx}`, `pages/tenders/our-price/{AnalogsTable.tsx,MarkDialog.tsx}`; тесты
  рядом; `test/fixtures.ts`.
- Docs: `docs/reference/{schema.md,screens.md,money-axes.md}`, `AGENTS.md`,
  `docs/TECH_DEBT.md`, `docs/devlog/2026-11-24-suspicious-lines.md`.

## Решения плана, которых нет в спеке

1. Семь задач: данные (1), ядро правил (2), индекс, список и счётчики (3), решения «не
   ошибка» (4), панель (5), экран списка и карточки (6), подсказка в панели (7). Снятия,
   прогон на стенде против замеров гейта, скорость, снимки и devlog — финал оркестратора.
2. Ядро — чистые функции над датаклассами без базы (как `services/lump_sum.py`); `crud`
   готовит входы. Индекс строится одной функцией `suspicion_index(db)` через
   `suspicion_cache.get`, панель и список зовут её же — одни цифры по построению.
3. Сверка `PUT` (§3 п. 1) — в `crud/suspicious_lines.set_not_error`: `suspicion_cache.invalidate()`
   до чтения индекса; строка блокируется `FOR UPDATE` раньше чтения. Сверка — по
   множествам `BasisKey`, без версии кэша (Review Focus п. 5).
4. `MarkDialog` обобщается, а не копируется: `kind` получает `"not_error"`, новый
   необязательный `submitLabel` (по умолчанию нынешний `MARK_SUBMIT`). «Ошибка в цене» из
   списка зовёт `positionItemsApi.putPriceError` новым хуком `usePriceErrorFromList` —
   `useSetMark` требует `LineIds` строки КП, которых у списка нет.
5. Полоса цен — `PriceStrip` на `ChartContainer` + `ScatterChart` recharts с
   логарифмической осью X (`scale="log"`, `domain` по степеням 10 вокруг точек); один
   компонент для списка и панели (`compact` для панели), обёрнут в свой блок прокрутки
   (§5 п. 5).
6. Порядок места в смете для соседей — `row_number() OVER (PARTITION BY estimate ORDER BY
   position_items.id)` по строкам без глав (как в замере `twin_pick_measure.py`).

## Задачи

### Task 1: данные и кэш

**Files:** миграция `0031`; `models.py` (`NotErrorMark`); `config.py`; `services/status_cache.py`;
`crud/our_price_load.py` (`load_history`); тесты `tests/integration/test_suspicious_schema.py`
(новый), `tests/integration/test_our_price_load.py`; `docs/reference/schema.md`.

**Interfaces**
- Потребляет (существуют): `PriceErrorMark` (образец), `StatusCache`, `load_history`.
- Производит:

```python
# models.py
class NotErrorMark(Base):  # not_error_marks — поля, CHECK и UNIQUE NULLS NOT DISTINCT спеки §1 п. 1
CK_NOT_ERROR_SHAPE: str    # имя CHECK формы основания
# config.py
SUSPICION_CACHE_TTL_S: int = Field(300, ge=0)
# services/status_cache.py
suspicion_cache: StatusCache
# crud/our_price_load.py
def load_history(db: Session, *, exclude_object_id: int | None) -> tuple[list[HistoryContract], list[HistoryLine]]
```

**Утверждения**
- Миграция вверх и вниз; `CHECK` формы: `outlier` без `direction` и `twin` с `family_id` —
  `IntegrityError` с именем ограничения; причина пустая и 301 символ — отказ;
  уникальность — второй такой же ряд основания `twin` (поля выброса `NULL`) отклоняется;
  каскад при удалении строки сметы, соседа и семьи; `RESTRICT` по автору и единице.
- `load_history(db, exclude_object_id=None)` — договоры всех объектов; с объектом — как
  сегодня (существующие вызовы не меняются).
- `suspicion_cache` — отдельный от `typical_rates_cache` экземпляр со сроком из
  `SUSPICION_CACHE_TTL_S`.

**Проверка**
- `pytest tests/integration/test_suspicious_schema.py tests/integration/test_our_price_load.py`
  — ДО: 0 + 144, ПОСЛЕ: 144 + утверждения задачи; `ruff check .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → `schema.md` → коммит.

### Task 2: ядро правил

**Files:** `services/suspicious_lines.py` (новый); `tests/unit/test_suspicious_rules.py` (новый).

**Interfaces**
- Потребляет (существуют): `WorkHistoryLine` (`lump`, `price_error`, `unit_id`, `vat_base`),
  `MIN_NOMINAL`, `price_basis.median`, `gross_to_net`, `is_price`.
- Производит:

```python
MIN_CONTRACTS = 3
MAX_SPREAD = Decimal(2)
OUTLIER_RATIO = Decimal(10)

@dataclass(frozen=True)
class OutlierBasis: family_id: int; unit_id: int; direction: Literal["up", "down"]
@dataclass(frozen=True)
class TwinBasis: neighbor_item_id: int
Basis = OutlierBasis | TwinBasis

@dataclass(frozen=True)
class OutlierFact: basis: OutlierBasis; ratio: Decimal; usual: Decimal; net: Decimal
@dataclass(frozen=True)
class TwinFact: basis: TwinBasis
Fact = OutlierFact | TwinFact

@dataclass(frozen=True)
class NeighborRow:            # строка сметы без глав с местом
    position_item_id: int; estimate_id: int; place: int
    unit_id: int | None; unit_cost_total: Decimal | None

@dataclass(frozen=True)
class ContractPrice: contract_id: int; lines: int; price: Decimal; low: Decimal; high: Decimal
@dataclass(frozen=True)
class GroupStats:
    family_id: int; unit_id: int; usual: Decimal | None; homogeneous: bool
    contracts: tuple[ContractPrice, ...]
@dataclass(frozen=True)
class SuspicionIndex:
    facts: Mapping[int, tuple[Fact, ...]]
    groups: Mapping[tuple[int, int], GroupStats]
    group_of: Mapping[int, tuple[int, int]]
    net: Mapping[int, Decimal]

def quartiles(values: Sequence[Decimal]) -> tuple[Decimal, Decimal]   # inclusive, точно
def build_suspicion_index(lines: Sequence[WorkHistoryLine],
                          neighbors: Sequence[NeighborRow]) -> SuspicionIndex
def state_of(facts: Sequence[Fact], decided: Collection[Basis],
             price_error: bool) -> Literal["unresolved", "resolved"] | None
```

**Утверждения** (спека §2; критические свойства §7 п. 1–4)
- Кандидаты: `lump`, неизвестная база, нет единицы, нетто 0,99 — не кандидаты; нетто 1 —
  кандидат; строка с `price_error` — кандидат, но не в статистике.
- «Лифты»: 35 строк договора A по 100 и по одной строке договоров B…F по 300, 500, 700,
  900, 1100 — фактов нет (обычная цена 600, Q1 350, Q3 850 — разброс 2,43 > 2); та же выборка
  с медианой по строкам дала бы обычную 100 при нулевом разбросе и факт у строки 1100 (×11).
- `quartiles([1, 2, 3])` = `(1.5, 2.5)`; `quartiles([10, 20, 30, 40])` = `(17.5, 32.5)`.
- Границы: три договора 100, 150, 200 (Q3/Q1 = 175/125 = 1,4) — однородна; цены договоров с
  Q3/Q1 ровно 2 — однородна, 2,0001 — нет; два договора — `usual is None`; `ratio` ровно
  10 и ровно 0,1 — факт (`up`/`down`), 9,99 и 0,1001 — нет.
- Двойник, пять случаев замера: сосед не кандидат, у строки `d < ln 2` — фактов нет; в
  паре строка с `price_error` — факт только у неё; оба кандидата, один в пределах ×2 —
  факт у другого; оба без обычной цены — у обоих; оба с ценой за ×2 — у дальнего. Сосед
  на 4 места — не пара; та же единица — не пара; глава между ними места не занимает.
- `state_of`: `price_error` — `resolved` при любых фактах; факт с основанием вне
  `decided` — `unresolved`; все основания в `decided` — `resolved`; без фактов и решений —
  `None`.

**Проверка**
- `pytest tests/unit/test_suspicious_rules.py` — ДО: 0, ПОСЛЕ: утверждения задачи; `ruff`.

- [ ] Тесты (красные) → реализация → зелёные → узкое ревью (Opus) → коммит.

### Task 3: индекс, список и счётчики

**Files:** `crud/suspicious_lines.py` (новый); `routers/suspicious_lines.py` (новый); `main.py`;
сбросы индекса — `services/import_pipeline.py` (`run_import_job`), `routers/contracts.py`
(`delete_contract`), `routers/not_analog.py` (price-error `PUT`/`DELETE`),
`routers/lump_sum.py`; тесты `tests/integration/test_suspicious_api.py` (новый).

**Interfaces**
- Потребляет: задачи 1–2; существуют `load_work_history`, `load_analog_details`,
  `chapter_steps`, `_path_view`, `_article_ref`, `category_tree`, `load_price_error_marks`,
  `mark_view`, `require_admin`.
- Производит:

```python
def load_estimate_neighbors(db: Session, estimate_ids: Collection[int]) -> list[NeighborRow]
def suspicion_index(db: Session) -> SuspicionIndex          # через suspicion_cache
@dataclass(frozen=True)
class NotErrorRecord: basis: Basis; reason: str; set_by_id: int; set_by_email: str; set_at: datetime
def load_not_error(db: Session, position_item_ids: Collection[int] | None) -> dict[int, list[NotErrorRecord]]
@dataclass(frozen=True)
class DecidedLine:            # строка решения, независимо от индекса
    position_item_id: int; contract_id: int; latest: bool
    unit_cost_total: Decimal | None; vat_base: Decimal | None
def load_decided_lines(db: Session) -> list[DecidedLine]
def price_head(line: DecidedLine, net: Decimal | None) -> dict   # net/gross/raw/price_note, §4 п. 2
def fact_view(index: SuspicionIndex, fact: Fact, titles: Mapping[str, Mapping[int, str]]) -> dict
def decision_view(record: NotErrorRecord, titles: Mapping[str, Mapping[int, str]]) -> dict
def suspicious_list(db: Session, *, contract_id: int | None) -> dict   # ответ §4 п. 2
def suspicious_counts(db: Session) -> dict                              # §3 п. 4
# GET /api/v1/suspicious-lines?contract_id=; GET /api/v1/suspicious-lines/counts
```

**Утверждения**
- Список: `unresolved` по договору, затем по месту; `resolved` — по времени решения,
  новые сверху; строка ровно в одном блоке; `contracts` для фильтра — договоры с
  не разобранными, с числом. Фильтр `contract_id` — только его строки; неизвестный — 404.
- Пункт выброса: `facts` с `basis`, `ratio`, `usual`, `family_title`, `contracts`;
  `prices.contracts` — цены договоров группы с `is_own`; `neighbors` — до 3 строк без глав
  до и после, у края сметы — сколько есть, `is_self` у строки; `where` — как у аналога.
  Пункт двойника — `facts[].neighbor` с флагом `is_twin` и в `neighbors`.
- `resolved` (критическое §7 п. 6, 6а): «три договора 10, 100, 100», «ошибка в цене» у
  дешёвой — `facts` пуст, строка в `resolved`; отметки у комплекта, строки прежней сметы
  (`latest = false`), строки с неизвестной базой (`vat_unknown`, `gross`); цена пусто, 0,
  −100, `NaN`, `Infinity` — `no_price`, `no_price`, `negative`/`"-100"`,
  `not_finite`/`"NaN"`, `not_finite`/`"Infinity"`.
- Соседи: денежные поля — нетто конечных значений (в том числе 0 и отрицательных), при
  неизвестной базе — как в смете с `gross: true`, `NaN` — как хранится, пусто — `null`.
- Счётчики: `total` и `by_contract` — только не разобранные; совпадают с длиной
  `unresolved` и с фильтром по договору.
- Кэш (§7 п. 10): второй вызов `suspicion_index` без сброса не читает базу; сбрасывают —
  каждое место списка задачи; `PUT` решения торга (например «Договорились») — не
  сбрасывает.
- `member` — 403 на оба `GET`.

**Проверка**
- `pytest tests/integration/test_suspicious_api.py tests/integration/test_contracts_api.py
  tests/integration/test_lump_sum_api.py tests/integration/test_not_analog_api.py` — ДО:
  0 + 54 + 20 + 35, ПОСЛЕ: 109 + утверждения задачи; `ruff`.

- [ ] Тесты (красные) → реализация → зелёные → коммит.

### Task 4: решения «не ошибка» и замена сметы

**Files:** `crud/suspicious_lines.py` (`set_not_error`, `clear_not_error`);
`routers/suspicious_lines.py`; `services/estimate_import.py` (`_replace_existing`); тесты
`tests/integration/test_suspicious_api.py`, `tests/integration/test_estimate_import.py`;
`AGENTS.md` (роли `admin`, ревизия).

**Interfaces**
- Потребляет: задачи 1–3; существуют `_lock_contract_item`, `CODE_POSITION_ITEM_NOT_FOUND`,
  `CODE_NOT_ANALOG_NOT_APPLICABLE`, `DomainError`.
- Производит:

```python
CODE_NOT_SUSPICIOUS = "not_suspicious"
CODE_SUSPICION_CHANGED = "suspicion_changed"
def set_not_error(db: Session, *, position_item_id: int, reason: str,
                  bases: Collection[Basis], actor_id: int) -> list[NotErrorRecord]
def clear_not_error(db: Session, *, position_item_id: int) -> None
class NotErrorRequest(BaseModel):
    reason: str                    # обрезка, 1..300
    bases: list[BasisKey]          # непустой; BasisKey — дискриминированный union спеки §3 п. 1
# PUT/DELETE /api/v1/position-items/{position_item_id}/not-error
```

**Утверждения** (критические §7 п. 5, 5а)
- `PUT` с набором оснований, равным текущим фактам без решения, пишет их и сбрасывает
  индекс; ответ — все записи строки, новые сверху; повтор — записи прежние (автор и время
  не меняются).
- Гонка: окно по выбросу семьи A, до `PUT` строка в семье B — `409 suspicion_changed`,
  записей нет; добавился двойник — `409`; переданное основание, которого нет среди фактов, —
  `409`; индекс пересчитан с тем же составом — `200`.
- Основание: решение по A, строка снова выброс в A — решена; в B, в другую сторону, новый
  двойник — `unresolved` с `previous`. Два основания от двух администраторов с разными
  причинами — две записи со своими подписями в `previous`, `resolved` и ответе `PUT`.
- Отказы `PUT` по порядку: 404; смета КП, глава, `SYSTEM` — `422 not_analog_not_applicable`;
  причина пустая, из пробелов, 301 символ, пустые `bases` — 422 тела; без фактов — `422
  not_suspicious`. `member` — 403.
- `DELETE`: после исчезновения фактов, после перевода строки в `SYSTEM`, повторный — `204` и
  решений нет; единственный отказ — 404; `member` — 403.
- Строка блокируется `FOR UPDATE` до чтения индекса.
- Замена сметы с одной тендерной отметкой и строкой с двумя решениями «не ошибка» — одно
  предупреждение «Отметки «не аналог» и «не ошибка» по строкам прежней сметы (2) не
  перенесены — поставьте заново»; без отметок и решений — нет; решения удалены каскадом.

**Проверка**
- `pytest tests/integration/test_suspicious_api.py tests/integration/test_estimate_import.py`
  — ДО: задача 3 + 94, ПОСЛЕ: больше на утверждения; `ruff`.

- [ ] Тесты (красные) → реализация → зелёные → узкое ревью (Opus) → `AGENTS.md` → коммит.

### Task 5: панель строки

**Files:** `crud/our_price.py` (`_analogs_view`, `build_line_view`); тесты
`tests/integration/test_suspicious_panel.py` (новый), `tests/integration/test_not_analog_panel.py`.

**Interfaces**
- Потребляет: задачи 2–4 (`suspicion_index`, `load_not_error`, `fact_view`, `decision_view`).
- Производит: поля аналога `suspicion` и `not_error` спеки §4 п. 3.

**Утверждения** (критическое §7 п. 7)
- Аналог с фактом без решения — `suspicion.facts`, `suspicion.net`, `suspicion.prices`;
  цифры равны пункту списка той же строки.
- Панель в режиме «с НДС» и с рядом ИПЦ: `price` аналога меняется, `suspicion` — нет.
- Тендер на объекте договора строки: аналоги этого объекта не приходят, а у аналога из
  другого договора той же группы `usual` считает и договор объекта.
- Все факты решены — `suspicion = null`, `not_error` — записи; без фактов и решений — оба
  пустые. У `member` — те же поля.
- Число запросов панели с тёплым индексом растёт на один (решения по идентификаторам
  аналогов).

**Проверка**
- `pytest tests/integration/test_suspicious_panel.py tests/integration/test_not_analog_panel.py`
  — ДО: 0 + 28, ПОСЛЕ: 28 + утверждения; `ruff`.

- [ ] Тесты (красные) → реализация → зелёные → узкое ревью (Opus) → коммит.

### Task 6: экран — список и карточка договора

**Files:** `types/domain.ts`, `services/api/domain.ts`, `services/queryKeys.ts` (`qk.suspicious`),
`services/queries.ts` (`useSuspiciousLines`, `useSuspiciousCounts`, `useSetNotError`,
`useClearNotError`, `usePriceErrorFromList`); `pages/contracts/{suspiciousCopy.ts,
SuspiciousTab.tsx,SuspectItem.tsx,PriceStrip.tsx,TwinPair.tsx}` (новые);
`pages/contracts/{ContractsPage.tsx,ContractCardPage.tsx}`;
`pages/tenders/our-price/MarkDialog.tsx`; тесты рядом; `test/fixtures.ts`;
`docs/reference/screens.md`.

**Interfaces**
- Потребляет: ответы задач 3–4; существуют `MarkDialog`, `LineWhere`, `positionItemsApi.putPriceError`,
  `invalidateAfterMark`, `toastApiError`, `ChartContainer`.
- Производит: `SuspectView`, `ResolvedView`, `FactView`, `BasisKey`, `NotErrorDecision`,
  `GroupPricesView`, `NeighborRow` в `types/domain.ts` (поля — спека §4);
  `PriceStrip({ prices, net, compact }: PriceStripProps)`; `MarkDialogProps.submitLabel?: string`.

**Утверждения**
- `admin`: вкладки «Договоры | Подозрительные строки (N)», N из `counts.total`; `member`
  вкладки не видит, `?tab=suspicious` у него — «Договоры».
- Адрес: `tab`, `contract`, `line` — фильтр выбран, пункт `line` прокручен и раскрыт.
- Пункт выброса — метка «↕ цена выбивается», «×10,6» и `PriceStrip`; двойника — «= та же
  цена, другая единица» и `TwinPair` с выделенными единицами; два `Collapsible` свёрнуты
  (кроме `line`).
- «Разобраны (M)» свёрнут; у решения «не ошибка» — основание, причина, автор, дата и
  «Вернуть»; у «ошибки в цене» — «Снять во всех тендерах…»; `facts` пуст — «правило сейчас
  её не ловит»; `price_note` — четыре разные подписи; `latest = false` — «строка прежней
  сметы».
- Окно «Не ошибка» отправляет `basis` показанных фактов; `409 suspicion_changed` — окно
  закрыто, список перечитан, сообщение «Подозрение по строке изменилось — проверьте
  заново»; «Сохранить» занят до ответа.
- Мутации «не ошибки» и «ошибки в цене» перечитывают `qk.suspicious` и
  `qk.tenders.ourPriceAll`.
- Карточка договора у `admin`: «Подозрительные строки: n →» со ссылкой
  `/contracts?tab=suspicious&contract={id}`; при нуле и у `member` — нет.
- 390 px: таблицы блоков ≥ 760 px с прокруткой внутри, деньги без переноса.

**Проверка**
- `npx vitest run src/pages/contracts src/pages/tenders/our-price/MarkDialog.test.tsx`
  (из `C:\`, PowerShell) — ДО: 17 + 48 + 7 = 72, ПОСЛЕ: больше на новые файлы и
  утверждения; `npx tsc -b`; `npx eslint src`.
- Снимок стенда против макета (раздел 1–2, 4), обе темы и 390 px — оркестратор.

- [ ] Тесты (красные) → реализация → зелёные → снимок → коммит.

### Task 7: подсказка в панели

**Files:** `pages/tenders/our-price/AnalogsTable.tsx`; `pages/contracts/suspiciousCopy.ts`
(тексты подсказки); `AnalogsTable.test.tsx`; `test/fixtures.ts`.

**Interfaces**
- Потребляет: поля аналога задачи 5; `PriceStrip`, `MarkDialog`, `useSetNotError` задачи 6;
  существуют `useSetMark`, окна «Ошибка в цене…» и «Не аналог в этом тендере…».
- Производит: —

**Утверждения**
- Аналог с `suspicion`: множитель, текст с ценой строки и обычной ценой, `PriceStrip
  compact`, подпись «Цены подсказки — номинальные, без НДС, без индексации»; у `admin` —
  ссылка «Разобрать в списке ↗» (`/contracts?tab=suspicious&line={id}`, новая вкладка) и три
  кнопки; у `member` — без ссылки и кнопок.
- Подсказка не меняется при переключении НДС и ряда ИПЦ панели.
- `suspicion = null` и `not_error` не пуст — серая строчка на каждое решение.
- В «Исключены», «Найти в договорах» и справочных блоках подсказки нет.
- 390 px: прокручивается только полоса; текст, ссылка и кнопки — в ширине карточки.

**Проверка**
- `npx vitest run src/pages/tenders/our-price/AnalogsTable.test.tsx` — ДО: 32, ПОСЛЕ:
  больше на утверждения; `npx tsc -b`; `npx eslint src`.
- Снимок стенда против макета (раздел 3) — оркестратор.

- [ ] Тесты (красные) → реализация → зелёные → снимок → коммит.

## Финал (оркестратор)

- [ ] Снятия критических свойств спеки §7 п. 1–7, 5а, 6а — в копии дерева, каждое
  красное; результат — в devlog.
- [ ] Стенд (§7 п. 14): миграция 0031 на `gca_dev`; список против `contract_level_measure.py`
  (с учётом отметок 24.11.2026) и `twin_pick_measure.py` — расхождения объяснены поштучно.
- [ ] Скорость (§6): холодный индекс ≤ 2,5 с; панель с тёплым — не медленнее +10 %
  чередованием «база/ветка» без нагрузки; список и счётчики ≤ 2 с.
- [ ] Снимки стенда по макету в обеих темах и на 390 px.
- [ ] Docs: `money-axes.md`, `TECH_DEBT.md` (загрузка истории растёт с договорами), devlog.
- [ ] Пуш; Codex.

## Команды проверки

- По задаче — в самой задаче.
- По фиче — GitHub Actions (локальный `just ci` не гоняется, решение пользователя
  11.10.2026); страж документации `uv run python scripts/check_agents_index.py`.
