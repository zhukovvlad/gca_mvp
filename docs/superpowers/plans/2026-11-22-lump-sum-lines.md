# План: строки, похожие на комплект

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Спека:** [`docs/superpowers/specs/2026-11-22-lump-sum-lines-design.md`](../specs/2026-11-22-lump-sum-lines-design.md) (редакция 2)
**Дизайн, макет, замер, выборка:** [`docs/superpowers/design/2026-11-22-lump-sum-lines.md`](../design/2026-11-22-lump-sum-lines.md) (редакция 4)
**Ветка:** `feat/lump-sum-lines`, черновой PR #94
**Редакция 2** — по ревью Codex к `eaf18cc` (P1): признак «цель указывает на
комплект» считается одной функцией и доходит до всех трёх мест, где
разрешается цель, — экран (`_decision_fields`), комментарий строки и файл
подрядчику (`line_comment_inputs`); проверка — настоящей сборкой
предпросмотра и XLSX (задача 4, Review Focus 3, решение плана 7).

**Goal:** строка сметы с весом 1 и ценой, несоразмерной обычной ставке
семьи, получает признак «Похоже на комплект»: строка КП не сравнивается,
строка договора не служит аналогом, администратор поправляет признак.

**Architecture:** чистое правило и обычные ставки — новый модуль
`services/lump_sum.py`; загрузка ставок и поправок — `crud/lump_sum.py`,
ставки во втором экземпляре `StatusCache`. Признак проставляют три
загрузчика (строки КП, история договоров, справочные строки), поэтому его
видят все поверхности. Ядро сравнения получает состояние `lump_sum`,
исключение из пула и причину цели; фронт — метку, пояснение, поправку.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest; React, TanStack Query
v5, shadcn/ui (`toggle-group`, `alert-dialog`, `badge`, `dropdown-menu` —
уже стоят), vitest.

## Global Constraints

- Правило — спека §1 дословно: `MEASURE_UNIT_CODES = {"M", "M2", "M3",
  "TON", "KG"}`, пороги `5` и `50`, граница включена (`net >= threshold *
  typical`), вес ровно `1`, `TYPICAL_MIN_LINES = 3`, нижняя граница пула —
  `MIN_NOMINAL` (1 ₽ нетто); все цены — нетто по `coalesce(estimates.vat_rate_base_override,
  proposals.vat_rate)` через `gross_to_net`; медиана — `price_basis.median`,
  точная десятичная, не `double precision`.
- Пул обычной ставки — спека §1 п. 2 (сметы договоров и КП, все; не главы;
  контекст не архивный, не `SYSTEM`, с семьёй; `is_price`, `is_weight`, вес
  ≠ 1; база известна; нетто ≥ 1; без поправки `lump`). Единица — только
  `position_items.catalog_position_id → catalog_positions.unit_id`.
- Применимость — спека §1 п. 4; вне её признака нет (`None`), строка — как
  сегодня.
- Поправка бьёт правило на всех поверхностях (спека §1 п. 5–6); одна
  функция решения.
- `lump_sum` — между `no_variant` и `no_zone` (спека §4 п. 1); комплект
  договора не кандидат пула нигде (§4 п. 4); системы не трогаются.
- Цели — порядок спеки §5 п. 2; «Договорились» действует (§5 п. 4).
- Коды отказов: `position_item_not_found` (404), `lump_sum_not_applicable`
  (422), `reference_is_lump_sum` (422), существующий
  `target_not_applicable`.
- Права: поправка — только `admin` (`require_admin`); читают все.
- Тексты экрана — спека §8 дословно, в `ourPriceCopy.ts`.
- Фронт — только компоненты shadcn/ui, уже стоящие в проекте.
- Режим проверок — по риску (решение пользователя 28.10.2026): критические
  свойства спеки §9 п. 1–7 снимает оркестратор лично в копии дерева (стенд
  отдаёт живое дерево); отдельное узкое ревью (Opus) — у задач 3 и 4, они
  двигают денежные цифры и цели; остальные — приёмка оркестратором (тесты,
  `tsc`, `eslint`, снимок стенда против макета). Соседние большие наборы
  исполнитель гоняет один раз в конце задачи. Итоговое ревью — Codex.

## Review Focus

1. Строка КП-комплект в семье с флажком «зона обязательна» и без зоны —
   `lump_sum`, а не `no_zone`, и плашка «ждёт решения» её не считает —
   задача 3.
2. Пул ключа ровно из трёх строк, одна из которых — поправка «это
   комплект»: обычной ставки нет (две строки), мерная строка с весом 1 —
   комплект по правилу «ставки нет» — задача 1.
3. Справочная цель, выбранная до фичи на строку договора, которую потом
   пометили «это комплект», — недействующая с `lump_sum`, в файле подрядчику
   нашей цены нет — задача 4.
4. Поправка «это ставка» у строки договора с весом 1 и ценой ×100: строка
   снова кандидат и в вилке, но в пул обычной ставки не входит (вес 1) —
   задачи 1 и 3.
5. Две строки одной цены нетто в сметах с базой 20 % и 0 % у порога —
   одно решение и в строке, и в пуле — задача 1.

## Структура файлов

- Create: `backend/services/lump_sum.py` — константы, типы, правило.
- Create: `backend/crud/lump_sum.py` — обычные ставки, поправки, запись поправки.
- Create: `backend/routers/lump_sum.py`; Edit: `backend/main.py` (роутер, сброс кэша).
- Create: `backend/alembic/versions/2026_11_22_0029-lump_sum_overrides.py`; Edit: `backend/models.py`.
- Edit: `backend/services/status_cache.py`, `backend/config.py`, `backend/services/import_pipeline.py`.
- Edit: `backend/services/estimate_import.py` (`_replace_existing`).
- Edit: `backend/services/our_price.py`, `backend/services/analog_ladder.py`,
  `backend/crud/our_price_load.py`, `backend/crud/our_price.py`,
  `backend/crud/reference_lines.py`, `backend/crud/reference_search.py`,
  `backend/crud/kp_decisions.py`.
- Create tests: `tests/unit/test_lump_sum_rule.py`,
  `tests/integration/test_lump_sum_load.py`,
  `tests/integration/test_lump_sum_api.py`.
- Edit tests: `test_status_cache.py`, `test_estimate_import.py`,
  `test_analog_ladder.py`, `test_our_price_ladder.py`, `test_analog_totals.py`,
  `test_our_price_load.py`, `test_our_price_offer_api.py`,
  `test_ref_analogs_target.py`, `test_ref_analogs_search.py`,
  `test_kp_file_rows.py`.
- Frontend: `types/domain.ts`, `services/api/domain.ts`, `services/queries.ts`,
  `pages/tenders/our-price/{ourPriceCopy.ts,OfferLinesTable.tsx,LineSheet.tsx,AnalogsTable.tsx,ReferenceSearch.tsx,ReferenceRows.tsx}`,
  Create `LumpBlock.tsx`; тесты рядом; `test/fixtures.ts`.
- Create: `docs/superpowers/design/2026-11-22-lump-sum-lines/snapshot_lines.py`.
- Docs: `docs/reference/{schema.md,money-axes.md,screens.md}`, `AGENTS.md`,
  `docs/devlog/2026-11-22-lump-sum-lines.md`.

## Решения плана, которых нет в спеке

1. Шесть задач: правило и ставки (1), маршруты поправки (2), сравнение (3),
   цели, поиск и ответ API (4), экран — строка и панель (5), экран — аналоги,
   поиск, цели (6); прогон «до/после», выборка, скорость, снимки и devlog —
   финал оркестратора.
2. Признак проставляют загрузчики, а не ядро: `load_kp_lines` (сам читает
   базы своих смет `kp_vat_bases`), `load_work_history` и
   `reference_lines_from_rows`. Каждый берёт `typical_rates(db)` (кэш) и
   `load_lump_overrides(db, ids)` — одним запросом на набор. Ядро читает
   готовые поля `KpLine.lump`, `WorkHistoryLine.lump`, `ReferenceLine.lump`.
3. `KpLine.catalog_unit_id` — новое поле, единица по
   `PositionItem.catalog_position_id` (как пул); сегодняшний
   `KpLineRow.unit_id` (через корзину контекста) не меняется.
4. Сброс кэша ставок — в те же две точки, что кэш сводки: прослойка
   `invalidate_status_cache_on_write` сбрасывает оба кэша, `run_import_job` —
   оба. Имя прослойки не меняется.
5. Маршрут поправки — отдельный роутер `/api/v1/position-items`, потому что
   строка договора и строка КП — одна сущность, а панель знает только
   `position_item_id`.
6. Снимок «до» для спеки §9 п. 15 оркестратор снимает **до задачи 3** (задачи
   1–2 сравнение не меняют) новым скриптом `snapshot_lines.py` по образцу
   скрипта фичи «зона как предпочтение»; вывод — в `tasks/lump-sum-run/`
   (не под гитом).
7. Признак цели (ред. 2): `_target_lump(scene, references, saved) -> bool`
   в `crud/our_price.py` — единственное место, где он считается: строка
   договора сохранённой цели вида `analog`/`reference` — комплект по
   `WorkHistory.is_lump` или по `ReferenceLine.lump` справочных строк сцены.
   Его зовут `_decision_fields`, `_comment_inputs` и сборка файла; файл
   получает готовое множество `FileContext.lump_targets` (строки КП, чья цель
   указывает на комплект), `line_comment_inputs` — флаг `target_lump`. Ядро
   (`services/`) признак не вычисляет и в `crud` не ходит.

## Подготовка (оркестратор, до задачи 3)

- [ ] `snapshot_lines.py` (READ ONLY, импорт приложения): по всем КП базы —
  `build_offer_view`, строки-работы, проекция спеки §9 п. 15 (`state`,
  `work_rung`, `range`, `lowest`, `freshest`, `contractor`,
  `analogs_count`, `to_lowest`, `no_history_reason`) и
  `candidate_ids` — идентификаторы строк договоров ступени (аналоги и
  выбросы, из панели `analogs[].analog_id`, только для сравнимых и
  «Разных единиц»). Режим `compare BEFORE AFTER LUMPS`: счётчик переходов
  состояний; строки, ставшие `lump_sum`; 68 строк `no_variant` без
  изменений; у прочих сравнимых «до» — полное совпадение проекции, кроме
  строк, чьи `candidate_ids` пересекаются с комплектами договоров (список
  `LUMPS` — выгрузка кодом ветки), — они печатаются отдельно.
- [ ] Снимок «до» на `gca_dev`: `tasks/lump-sum-run/before.json`.

## Задачи

### Task 1: правило, обычные ставки, поправки в базе и кэш

**Files:** `services/lump_sum.py` (новый); `crud/lump_sum.py` (новый:
`load_typical_rates`, `typical_rates`, `load_lump_overrides`); миграция
`0029`; `models.py` (`LumpSumOverride`); `services/status_cache.py`
(`typical_rates_cache`); `config.py` (`TYPICAL_RATES_CACHE_TTL_S`);
`main.py`, `services/import_pipeline.py` (сброс второго кэша); тесты
`tests/unit/test_lump_sum_rule.py` (новый),
`tests/integration/test_lump_sum_load.py` (новый, вместе со схемой),
`tests/unit/test_status_cache.py`; `docs/reference/schema.md` (таблица
`lump_sum_overrides`).

**Interfaces**
- Потребляет (существуют): `gross_to_net`, `is_price`, `is_weight`,
  `price_basis.median`, `MIN_NOMINAL` (`services/analog_ladder.py`),
  `StatusCache`, `status_cache`, `run_import_job`,
  `invalidate_status_cache_on_write`, `PositionItem`, `CatalogPosition`,
  `CatalogContext`, `ContextMember`, `Estimate`, `Proposal`, `Lot`,
  `UnitOfMeasure`.
- Производит:

```python
# services/lump_sum.py
MEASURE_UNIT_CODES: frozenset[str] = frozenset({"M", "M2", "M3", "TON", "KG"})
MEASURE_THRESHOLD = Decimal(5)
PIECE_THRESHOLD = Decimal(50)
TYPICAL_MIN_LINES = 3
LumpVerdict = Literal["lump", "rate"]

@dataclass(frozen=True)
class TypicalRate:
    net: Decimal
    lines: int

@dataclass(frozen=True)
class TypicalRates:
    rates: Mapping[tuple[int, int], TypicalRate]   # (family_id, unit_id)
    unit_codes: Mapping[int, str]                  # unit_id -> units_of_measure.code
    unit_labels: Mapping[int, str]                 # unit_id -> подпись единицы

@dataclass(frozen=True)
class LumpOverride:
    verdict: LumpVerdict
    set_by_id: int
    set_by_email: str
    set_at: datetime

@dataclass(frozen=True)
class LumpCheck:
    rule: bool
    override: LumpOverride | None
    is_lump: bool
    measure: bool
    unit_id: int
    typical: TypicalRate | None
    net: Decimal
    threshold: Decimal

def lump_check(
    *, family_id: int, unit_id: int | None, unit_price: Decimal | None,
    weight: Decimal | None, vat_base: Decimal | None,
    override: LumpOverride | None, rates: TypicalRates,
) -> LumpCheck | None      # None — правило неприменимо по цене, весу, базе, единице

# crud/lump_sum.py
def load_typical_rates(db: Session) -> TypicalRates
def typical_rates(db: Session) -> TypicalRates     # через typical_rates_cache
def load_lump_overrides(db: Session, position_item_ids: Collection[int]) -> dict[int, LumpOverride]

# models.py — LumpSumOverride, таблица lump_sum_overrides: position_item_id (BigInteger PK,
#   FK position_items.id CASCADE), verdict (Text NOT NULL), set_by (Integer FK users.id
#   RESTRICT NOT NULL), set_at (DateTime(timezone=True) NOT NULL);
# CK_LUMP_SUM_VERDICT = "verdict IN ('lump', 'rate')", name="ck_lump_sum_overrides_verdict"
# services/status_cache.py
typical_rates_cache: StatusCache[TypicalRates]
# config.py
TYPICAL_RATES_CACHE_TTL_S: int = Field(300, ge=0)
```

**Утверждения**
- Мерная единица, ставка 1 000: цена нетто 5 000 при весе 1 — комплект;
  4 999,99 — ставка. Штучная: 50 000 — комплект, 49 999,99 — ставка
  (Global Constraints: граница включена).
- Вес 1,0001 или 2 при отношении ×1 000 — ставка; вес `Decimal("1.000")` —
  вес 1.
- Ставки нет: мерная — комплект, штучная — ставка; `typical is None`.
- Поправка: `lump` при ответе правила «ставка» — `is_lump`; `rate` при
  «комплект» — не `is_lump`; `rule` в обоих случаях — ответ правила.
- Неприменимо — `None`: `unit_id is None`, цена `0`/`NaN`/`None`, вес не
  `is_weight`, база `None`.
- НДС (мерная единица): строка 600 ₽ при базе 20 % и строка 500 ₽ при
  базе 0 % — обе 500 ₽ нетто; при обычной ставке 100 ₽ нетто обе —
  комплект, при 100,01 ₽ — обе ставка. Пул: три строки с ценами 120, 240,
  360 ₽ при базе 20 % и те же 100, 200, 300 ₽ при базе 0 % дают одну
  обычную ставку 200 ₽ (Review Focus 5).
- Пул (`load_typical_rates`, фикстуры в базе): из ключа выпадает каждое по
  отдельности — вес 1; нетто 0,99; база `NULL`; смета раунда; архивный
  контекст; `SYSTEM`; глава; поправка `lump`; строка без каталожной
  единицы. Пул из двух строк — ставки нет; из трёх — есть; из четырёх —
  среднее двух средних; строки КП и договора, в том числе прежней сметы
  договора, входят обе (Review Focus 2).
- `load_lump_overrides` — по идентификаторам, с email автора; пустой набор
  — без запроса.
- Кэш: два вызова `typical_rates` в срок — один пересчёт; не-GET запрос
  (`PUT` любого маршрута) и `run_import_job` сбрасывают его; при `0` —
  пересчёт каждый раз.
- Миграция: таблица, `CHECK` (значение вне двух — `IntegrityError` с
  именем ограничения), каскад при удалении строки сметы, `RESTRICT` по
  автору, паритет выражения модели и миграции, `downgrade`.

**Проверка**
- `pytest tests/unit/test_lump_sum_rule.py
  tests/integration/test_lump_sum_load.py tests/unit/test_status_cache.py` —
  ДО: 13 (`test_status_cache.py`), ПОСЛЕ: больше на тесты двух новых файлов;
  `ruff check .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

### Task 2: маршруты поправки и замена сметы

**Files:** `crud/lump_sum.py` (`set_lump_override`, `clear_lump_override`);
`routers/lump_sum.py` (новый); `main.py` (подключение);
`services/estimate_import.py` (`_replace_existing`); тесты
`tests/integration/test_lump_sum_api.py` (новый),
`tests/integration/test_estimate_import.py`; `AGENTS.md` (роли `admin`:
«поправка «комплект или ставка» у строки сметы» со ссылкой на спеку §3;
новая ревизия в шапке).

**Interfaces**
- Потребляет: задачу 1 (`LumpSumOverride`, `LumpVerdict`); существуют
  `require_admin`, `DomainError`, `raise_domain_error`, `_replace_existing`.
- Производит:

```python
# crud/lump_sum.py
CODE_POSITION_ITEM_NOT_FOUND = "position_item_not_found"
CODE_LUMP_SUM_NOT_APPLICABLE = "lump_sum_not_applicable"
def set_lump_override(db: Session, *, position_item_id: int, verdict: LumpVerdict, actor_id: int) -> LumpSumOverride
def clear_lump_override(db: Session, *, position_item_id: int) -> None
# routers/lump_sum.py
class LumpSumRequest(BaseModel):
    verdict: LumpVerdict
# PUT    /api/v1/position-items/{position_item_id}/lump-sum -> {"position_item_id", "verdict", "set_by": {"id", "email"}, "set_at"}
# DELETE /api/v1/position-items/{position_item_id}/lump-sum -> 204
```

**Утверждения**
- `PUT` у `admin`: запись со значением, автором, временем; повтор того же
  значения — `200`, автор и время прежние (запрос другим администратором
  не меняет автора); смена значения — новый автор и время.
- `DELETE`: с записью — удаляет, `204`; без записи — `204`.
- `member` — `403` на оба маршрута.
- Строки нет — `404 position_item_not_found`.
- `422 lump_sum_not_applicable` — каждое по отдельности: глава; контекст
  `SYSTEM`; без семьи; семья без варианта; контекст в архиве; смета
  раунда. Строка с неизвестной базой НДС и строка с ценой 0 — поправку
  принимают.
- Тело с `verdict: "other"` — `422` валидации.
- Строка сметы блокируется `FOR UPDATE` (по образцу `decide_zone`).
- Замена сметы договора с двумя поправками: в `warnings` —
  «Поправки «комплект или ставка» по строкам прежней сметы (2) не
  перенесены — правило решит заново»; без поправок — предупреждения нет;
  поправки удалены каскадом.

**Проверка**
- `pytest tests/integration/test_lump_sum_api.py
  tests/integration/test_estimate_import.py` — ДО: 90, ПОСЛЕ: больше на тесты
  нового файла и утверждения 8; `ruff check .`; страж документации
  `uv run python scripts/check_agents_index.py`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

### Task 3: сравнение — состояние, пул, загрузчики

**Files:** `services/our_price.py` (`LineState.lump_sum`, `KpLine.lump`,
`KpLine.catalog_unit_id`, `evaluate_offer`, `work_ladder_of`);
`services/analog_ladder.py` (`WorkHistoryLine.lump`, `_build_pool`,
`WorkHistory.is_lump`); `crud/our_price_load.py` (`load_kp_lines`,
`load_work_history`); тесты `test_our_price_ladder.py`,
`test_analog_ladder.py`, `test_analog_totals.py`, `test_our_price_load.py`,
`tests/unit/test_ref_analogs_blocks.py` (блоки А и Г);
`docs/reference/money-axes.md` («Аналоги работы»: обычная ставка, правило,
исключение комплектов, поправка).

**Interfaces**
- Потребляет: задачу 1 (`LumpCheck`, `lump_check`, `typical_rates`,
  `load_lump_overrides`); существуют `evaluate_offer`, `work_ladder_of`,
  `_build_pool`, `_FamilyPool`, `kp_vat_bases`, `_totals`,
  `_waiting_admin`.
- Производит:

```python
# services/our_price.py
class LineState(str, Enum): ... no_variant, lump_sum, no_zone, ...   # lump_sum = "lump_sum"
# KpLine: lump: LumpCheck | None = None; catalog_unit_id: int | None = None
# services/analog_ladder.py
# WorkHistoryLine: lump: bool = False
class WorkHistory:
    def is_lump(self, position_item_id: int) -> bool
```

**Утверждения**
- Строка КП с `lump.is_lump` — `lump_sum`; без ступени, вилки, самой
  низкой, свежей, «с этим подрядчиком», разниц; `kp_unit_net` и
  `kp_total_net` заполнены.
- Порядок: `in_progress`, `unknown_vat_base`, `no_price`, `no_variant`
  раньше `lump_sum`; строка-комплект семьи с флажком без зоны — `lump_sum`,
  `waiting_admin` ложно, плашка её не считает (Review Focus 1).
- `work_ladder_of` у строки-комплекта — `None` (лестницы нет).
- Итоги: `lump_sum` в `not_comparable` с суммой строки, ключ `by_state`.
- Пул: строка договора с `lump` не кандидат — нет в ступени, остатке,
  выбросах и медиане их отбора, самой низкой, свежей, «с этим
  подрядчиком», `vat_dropped`, блоках А и Г, справке «по частям» (по
  одному входу на читателя пула); соседняя ставка той же ступени — в
  вилке.
- Ступень, где единственный кандидат — комплект: у строки КП ступени нет
  (`no_history`), а не пустая вилка.
- Строки-ставки без комплектов среди кандидатов — ответ тот же, что до
  задачи (существующие тесты лестницы и итогов зелёные без правки
  ожиданий).
- Загрузчики: `load_kp_lines` проставляет `lump` по базе своей сметы и
  `catalog_unit_id` по `PositionItem.catalog_position_id`;
  `load_work_history` — `lump` по базе сметы договора; поправка видна в
  обоих; строка без варианта — `lump is None`.
- Поправка «это ставка» у строки договора с весом 1 и ценой ×100 — снова
  кандидат и в вилке (Review Focus 4); «это комплект» у строки-ставки —
  не кандидат.

**Проверка**
- `pytest tests/unit/test_our_price_ladder.py
  tests/unit/test_analog_ladder.py tests/unit/test_analog_totals.py
  tests/integration/test_our_price_load.py tests/unit/test_ref_analogs_blocks.py`
  — ДО: 104 + 144 + 42 + 126 + 48 = 464,
  ПОСЛЕ: больше; один раз в конце — `tests/unit`,
  `test_our_price_offer_api.py`, `test_our_price_signal.py`,
  `test_our_price_round_api.py`, `test_ref_analogs_panel.py`; `ruff check .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

### Task 4: цели, поиск, ответ API

**Files:** `services/our_price.py` (`TargetResetReason`, `resolve_target`);
`crud/kp_decisions.py` (`put_reference_target`,
`CODE_REFERENCE_IS_LUMP_SUM`); `crud/reference_lines.py` (вес и `lump` в
`reference_lines_query`, `ReferenceLine`, `reference_lines_from_rows`);
`crud/our_price.py` (`_line_view` — `lump`, `reference_row_view` — `lump`,
`_target_lump`, `_decision_fields`, `_comment_inputs`, сборка `FileContext`,
`lump_view`); `services/kp_file.py` (`FileContext.lump_targets`,
`line_comment_inputs`, `_file_line`); тесты
`test_our_price_offer_api.py`, `test_ref_analogs_target.py`,
`test_ref_analogs_search.py`, `tests/unit/test_kp_file_rows.py`,
`tests/integration/test_kp_file_export.py` (предпросмотр и XLSX).

**Interfaces**
- Потребляет: задачи 1 и 3 (`LumpCheck`, `TypicalRates.unit_labels`,
  `KpLine.lump`, `WorkHistory.is_lump`, `LineState.lump_sum`); существуют
  `resolve_target`, `put_target`, `put_reference_target`,
  `CODE_TARGET_NOT_APPLICABLE`, `money_pair`, `_author`, `iso`,
  `reference_row_view`, `_line_view`, `_decision_fields`, `_comment_inputs`,
  `_reference_targets`, `FileContext`, `line_comment_inputs`, `_file_line`,
  `target_net`.
- Производит:

```python
# services/our_price.py
TargetResetReason = Literal["kind_changed", "lump_sum", "not_comparable", "analog_unavailable",
                            "no_freshest", "no_contractor", "reference_unavailable"]
def resolve_target(saved, result, *, is_system: bool, reference_available: bool = False,
                   target_lump: bool = False) -> TargetChoice
# crud/kp_decisions.py
CODE_REFERENCE_IS_LUMP_SUM = "reference_is_lump_sum"
# crud/reference_lines.py — ReferenceLine: weight: Decimal; lump: LumpCheck | None
# crud/our_price.py
def lump_view(check: LumpCheck, *, rate: Decimal | None, family: str, unit_label: str) -> dict
def _target_lump(scene: _Scene, references: _ReferenceTargets | None, saved: SavedTarget | None) -> bool
# services/kp_file.py
# FileContext: lump_targets: frozenset[int] = frozenset()   # position_item_id строк КП
def line_comment_inputs(..., target_lump: bool = False) -> CommentInputs   # передаёт в resolve_target
# ответ: line.lump и row.lump — LumpView спеки §7 п. 1; choice.reset_reason: "lump_sum"
```

**Утверждения**
- `resolve_target`: строка `lump_sum` — `lump_sum` у всех четырёх видов
  цели, в том числе `reference`; цель `analog`/`reference` на строку
  договора с `target_lump` — `lump_sum` раньше `analog_unavailable` и
  `reference_unavailable`; система — `kind_changed` раньше всего;
  остальные случаи — как до задачи (существующие тесты зелёные).
- `put_target` у строки `lump_sum` — `422 target_not_applicable`;
  `put_reference_target` у строки `lump_sum` — `target_not_applicable`, на
  строку договора-комплект — `422 reference_is_lump_sum`; оба — до проверок
  пересчёта (тело с неверным пересчётом получает эти коды).
- Одно решение на трёх путях (ред. 2): строка КП `no_history` со
  справочной целью; её строку договора помечают «это комплект» через
  `PUT …/lump-sum`; затем — ответ панели (`choice.active = false`,
  `reset_reason = "lump_sum"`), предпросмотр файла и выгруженный XLSX
  (настоящая сборка, не прямой вызов `resolve_target`): нашей цены у строки
  нет, шаблонного комментария нет (Review Focus 3).
- Строка КП сравнимая, сохранённая цель `analog` на строку договора, которую
  помечают «это комплект», а другие аналоги остаются: экран —
  `reset_reason = "lump_sum"`; файл и комментарий — цена и источник по самой
  низкой из оставшихся аналогов, как у строки без сохранённого выбора.
- Снятие поправки (`DELETE`) в обоих сценариях возвращает цель в действие —
  на экране и в файле.
- «Договорились» у строки `lump_sum` ставится, и в файле подрядчику цена
  есть.
- Поиск: строка-комплект в выдаче с `lump.is_lump = true`, порядок и
  `SEARCH_LIMIT` прежние; строка без семьи — `lump: null`; строка КП
  `lump_sum` — `reference_not_applicable`.
- Ответ строки: `lump` у строки с применимым правилом в любом состоянии
  (сравнимая ставка — `rule = false`, `is_lump = false`), `null` у
  неприменимой; поля спеки §7 п. 1 (`unit_price`, `typical` — `{net,
  shown}` в ставке участника, `typical_lines`, `ratio` без округления,
  `threshold` `"5"`/`"50"`, `family`, `unit`, `override` с автором и
  временем); `typical: null` и `typical_lines: 0` без ставки.
- Ответ разбора и этапа: `totals.not_comparable.by_state.lump_sum`.

**Проверка**
- `pytest tests/integration/test_our_price_offer_api.py
  tests/integration/test_ref_analogs_target.py
  tests/integration/test_ref_analogs_search.py tests/unit/test_kp_file_rows.py
  tests/integration/test_kp_file_export.py`
  — ДО: 149 + 108 + 49 + 184 + 101 = 591, ПОСЛЕ: больше; один раз в конце —
  `tests/unit`, `test_ref_analogs_lines.py`,
  `test_ref_analogs_panel.py`, `test_ref_analogs_file.py`; `ruff check .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → коммит.

### Task 5: экран — строка, панель, поправка

**Files:** `types/domain.ts` (`OurPriceLineState`, `LumpView`,
`OurPriceLine.lump`); `services/api/domain.ts` (`positionItemsApi`);
`services/queries.ts` (`useSetLumpSum`, сброс как
`invalidateAfterLadderDecision`); `ourPriceCopy.ts`
(`LINE_STATE_LABEL.lump_sum`, подпись, пояснения, `FILTER_HINT`);
`OfferLinesTable.tsx`; `LumpBlock.tsx` (новый); `LineSheet.tsx`; тесты
`OfferLinesTable.test.tsx`, `LumpBlock.test.tsx` (новый),
`LineSheet.work.test.tsx`, `ourPriceCopy.test.ts`; `test/fixtures.ts`
(`lump: null` по умолчанию); `docs/reference/screens.md` (состояние и
поправка).

**Interfaces**
- Потребляет: ответ задачи 4, маршруты задачи 2.
- Производит:

```ts
export type LumpVerdict = "lump" | "rate";
export interface LumpView {
  rule: boolean; is_lump: boolean; measure: boolean;
  unit_price: MoneyPair; typical: MoneyPair | null; typical_lines: number;
  ratio: string | null; threshold: string; family: string; unit: string;
  override: { verdict: LumpVerdict; set_by: UserRef; set_at: string } | null;
}
positionItemsApi.putLumpSum(id: number, verdict: LumpVerdict): Promise<void>
positionItemsApi.deleteLumpSum(id: number): Promise<void>
export function useSetLumpSum(): UseMutationResult<void, unknown, { id: number; verdict: LumpVerdict | null }>
// ourPriceCopy.ts
export function lumpCaption(lump: LumpView): string           // «цена в N раз выше…» | «обычной ставки нет»
export function lumpWhy(lump: LumpView, vat: VatView): string // три вида пояснения спеки §8 п. 2
export function lumpOverrideNote(lump: LumpView): string | null
export function LumpBlock(props: { line: OurPriceLine; canEdit: boolean; vat: VatView }): JSX.Element | null
```

**Утверждения**
- Таблица: у `lump_sum` метка «Похоже на комплект» нейтрально-серым и под
  ней `lumpCaption`; `ratio` — целые с разделителем разрядов
  («16 989»); без ставки — «обычной ставки нет»; строка в плитке «не
  разобраны», подсказка плитки называет «похожие на комплект».
- Панель `lump_sum`: блок «Сравнение» с меткой «Похоже на комплект» ·
  «не сравнивается» и `lumpWhy` — текст спеки §8 п. 2 дословно для
  каждого из трёх видов.
- Поправка у `admin`: `ToggleGroup` из трёх положений, у «Как решило
  правило» в скобках ответ правила; выбор уходит сразу (`PUT` с
  `verdict`, «как решило правило» — `DELETE`), группа занята на время
  запроса; после ответа сброшены `qk.tenders.ourPriceAll` и
  `kpFilePreviewsAll`.
- У сравнимой строки с `lump` — блок поправки под «Сравнением»; у строки
  с `lump: null` — блока нет.
- `member`: переключателя нет; при поправке — подпись «поправлено
  вручную: {email}, {дата}».
- Числа и тексты не переносятся и не наезжают на узкой панели (снимок
  стенда — финал).

**Проверка**
- из `frontend/` (диск `C:\`): `npx vitest run` по файлам
  задачи — ДО: число тестов этих файлов на базовом коммите, ПОСЛЕ: больше;
  `npx tsc -b`; `npx eslint .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

### Task 6: экран — аналоги, поиск, цели

**Files:** `AnalogsTable.tsx` (действие «это комплект — убрать из
сравнения» с `AlertDialog`); `ReferenceSearch.tsx` (`SearchMarks`),
`ReferenceRows.tsx` (выбор цели закрыт, меню поправки); `ourPriceCopy.ts`
(`TARGET_RESET_REASON.lump_sum`, тексты); `types/domain.ts`
(`TargetResetReason`, `RefRow.lump`); `services/queries.ts`
(`REFERENCE_DRIFT_CODES` + `reference_is_lump_sum`); тесты
`AnalogsTable.test.tsx`, `ReferenceSearch.test.tsx`,
`ReferenceBlocks.test.tsx` (выбор цели не тронут), `TargetCards.test.tsx`;
`docs/reference/screens.md` (метка в поиске).

**Interfaces**
- Потребляет: задачу 5 (`LumpView`, `useSetLumpSum`, `lumpOverrideNote`),
  ответ задачи 4.
- Производит:

```ts
// ourPriceCopy.ts
export const LUMP_ANALOG_ACTION = "это комплект — убрать из сравнения";
export function lumpAnalogConfirm(title: string): string   // текст спеки §8 п. 4
export const LUMP_SEARCH_MARK = "похоже на комплект";
export const LUMP_NOT_TARGET = "Похоже на комплект — не цель для ставки";
// TARGET_RESET_REASON.lump_sum = "комплект вместо ставки"
```

**Утверждения**
- Аналоги у `admin`: действие у каждого аналога; подтверждение с текстом
  спеки §8 п. 4; после подтверждения — `PUT verdict = "lump"` по
  `analog_id` и сброс `qk.tenders.ourPriceAll`; у `member` действия нет.
- Поиск: у строки с `lump.is_lump` — метка «похоже на комплект» (с
  «поправлено вручную», если поправка) и подсказка с `ratio`; «сделать
  целью» недоступно с подсказкой `LUMP_NOT_TARGET`; у строки без признака
  — как сегодня.
- Поиск у `admin`: меню строки с тремя положениями поправки; выбор уходит
  сразу.
- Карточка недействующей цели с `lump_sum` — «комплект вместо ставки».
- Отказ `reference_is_lump_sum` при выборе цели — панель перечитывается
  (как остальные коды `REFERENCE_DRIFT_CODES`).

**Проверка**
- из `frontend/`: `npx vitest run` по файлам задачи — ДО/ПОСЛЕ
  как в задаче 5; `npx tsc -b`; `npx eslint .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

## Финал (оркестратор)

- [ ] Снятия критических свойств спеки §9 п. 1–7 в копии дерева, каждое
  восстановлено побайтно; результат — в devlog.
- [ ] `gca_dev` до 0029 (стенд остановлен на время миграции); выгрузка
  `LUMPS`, снимок «после» и `snapshot_lines.py compare`: 1 135 ± импорт
  после снимка, 68 `no_variant` без изменений, остальные сравнимые «до» —
  совпадение или в списке «был комплект среди кандидатов» (спека §9 п. 15).
- [ ] Выборка (спека §9 п. 14): ответ кода по 37 строкам
  `tasks/lump-sum-sample/` против разметки и ожидания.
- [ ] Скорость (спека §9 п. 11): разбор КП, этап, панель — тёплый кэш до/после,
  холодный пересчёт ставок — числа в devlog.
- [ ] Снимки стенда по спеке §9 п. 16 в обеих темах, сверка с макетом.
- [ ] `docs/devlog/2026-11-22-lump-sum-lines.md`; пуш; черновик → готов к
  ревью по слову пользователя.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: GitHub Actions на пуше (локальный `just ci` не гоняется —
  решение пользователя 11.10.2026), прогон на стенде — финал.
