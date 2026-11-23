# План: «Не аналог»

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Спека:** [`docs/superpowers/specs/2026-11-23-not-analog-design.md`](../specs/2026-11-23-not-analog-design.md) (редакция 2)
**Дизайн и макет:** [`docs/superpowers/design/2026-11-23-not-analog.md`](../design/2026-11-23-not-analog.md)
**Ветка:** `feat/not-analog`, черновой PR #101
**Редакция 2** — по ревью Codex к `aa31560` (четыре P2): пул без отметок
строится для следа и у строки со ступенью (решение плана 4, задача 3);
прогон «до/после» — новый `compare` по всем состояниям с `selftest` на
контрольных входах (решение плана 6, подготовка); детали карточек
исключённых строк грузятся в панели вместе с аналогами (задача 4);
проверки фронта — конкретные файлы, команды и числа (задачи 6, 7).

**Goal:** администратор исключает строку договора из аналогов — в одном
тендере для строки формы КП или во всех тендерах («ошибка в цене»); подбор
идёт дальше, исключённое видно в панели, цели и файл подрядчику следуют.

**Architecture:** две таблицы отметок (миграция 0030) и маршруты — новые
`crud/not_analog.py`, `routers/not_analog.py`. «Ошибка в цене» — признак
строки истории, отсекается в `_build_pool`; тендерная отметка — множество
`KpWork.excluded`, которое заполняет сцена по ключу формы строки КП и
которое уважают читатели пула из закрытого списка спеки §3 п. 2. След и
причина `excluded` — в ядре лестницы над вторым, ленивым пулом без отметок.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest; React, TanStack Query
v5, shadcn/ui (`dropdown-menu`, `alert-dialog`, `collapsible`, `textarea`,
`badge` — уже стоят в `components/ui/`), vitest.

## Global Constraints

- Таблицы, ограничения, ключ формы, коды отказов и тексты — спека дословно:
  `price_error_marks`, `not_analog_marks` (`UNIQUE NULLS NOT DISTINCT`),
  `form_key = (normalize_title(job_title_in_proposal), position_items.unit_id)`,
  причина 1–300 символов после обрезки, `position_item_not_found` (404),
  `not_analog_not_applicable` (422), `reference_is_excluded` (422).
- «Ошибка в цене» отсекается в одном месте — `_build_pool` (спека §3 п. 1);
  тендерная отметка — только читателями из закрытого списка §3 п. 2 и
  предикатом блока А §5 п. 2; новые читатели пула вне списка не заводятся.
- Причина `excluded` — первая в цепочке; `base_reason` — внутреннее поле, в
  ответ не выходит (§3 п. 6).
- `resolve_target(..., target_excluded: bool)` и
  `line_comment_inputs(..., target_excluded: bool)` — обязательные
  именованные параметры без значения по умолчанию (§6 п. 2).
- Состояния строки КП не меняются; системы (`SYSTEM`) не трогаются.
- Права: отметки — только `admin` (`require_admin`); читают все.
- Фронт — только shadcn/ui; тексты — `ourPriceCopy.ts`, по спеке §8 и макету.
- Режим проверок — по риску (решение пользователя 28.10.2026): критические
  свойства спеки §9 п. 1–10 снимает оркестратор в копии дерева (стенд отдаёт
  живое дерево); узкое ревью (Opus) — у задач 3, 4 и 5 (подбор, панель,
  цели и файл — денежные цифры); остальные — приёмка оркестратором.
  Исполнитель гоняет только затронутые наборы, полный бэкенд — GitHub
  Actions. Итоговое ревью — Codex.

## Review Focus

1. Семья с флажком «зона обязательна»: единственный кандидат своей зоны с
   тендерной отметкой, в другой зоне — неотмеченная строка: причина
   `excluded`, след из одной строки своей зоны, блок А открыт в панели и в
   поиске — задачи 3 и 4.
2. Все кандидаты семьи с «ошибкой в цене»: `excluded`, а не `no_family` —
   задача 3.
3. Справочная цель из поиска, потом «ошибка в цене» на её строке: панель —
   `excluded`, предпросмотр и XLSX — та же наша цена, что в панели
   (самая низкая) — задача 5.
4. Две строки КП одного тендера с одним текстом в разных разделах и строка
   с тем же текстом в другом тендере: отметка действует у первых двух, не у
   третьей; строка того же текста с другой единицей — не действует —
   задачи 1 и 4.
5. Строка договора с обеими отметками: снятие «ошибки в цене» оставляет её
   в «Исключены» в этом тендере, а в другом тендере она снова аналог —
   задачи 4 и 6.

## Структура файлов

- Create: `backend/alembic/versions/2026_11_24_0030-not_analog_marks.py`;
  Edit: `backend/models.py` (`PriceErrorMark`, `NotAnalogMark`).
- Create: `backend/crud/not_analog.py` (загрузка, запись, снятие);
  `backend/routers/not_analog.py`; Edit: `backend/main.py`.
- Edit: `backend/services/estimate_import.py` (`_replace_existing`).
- Edit: `backend/services/analog_ladder.py`, `backend/services/our_price.py`,
  `backend/services/reference_blocks.py`, `backend/services/kp_file.py`,
  `backend/crud/our_price_load.py`, `backend/crud/our_price.py`,
  `backend/crud/reference_lines.py`, `backend/crud/reference_search.py`,
  `backend/crud/kp_decisions.py`, `backend/crud/lump_sum.py`.
- Create tests: `tests/integration/test_not_analog_schema.py`,
  `tests/integration/test_not_analog_api.py`,
  `tests/unit/test_not_analog_ladder.py`,
  `tests/integration/test_not_analog_panel.py`,
  `tests/integration/test_not_analog_targets.py`.
- Frontend: `types/domain.ts`, `services/api/domain.ts`, `services/queries.ts`,
  `pages/tenders/our-price/{ourPriceCopy.ts,AnalogsTable.tsx,LineSheet.tsx,OfferLinesTable.tsx,ReferenceSearch.tsx,ReferenceRows.tsx,TargetCards.tsx}`;
  Create `ExcludedAnalogs.tsx`, `MarkDialog.tsx`; тесты рядом;
  `test/fixtures.ts`.
- Docs: `docs/reference/{schema.md,money-axes.md,screens.md}`, `AGENTS.md`,
  `docs/devlog/2026-11-23-not-analog.md`.

## Решения плана, которых нет в спеке

1. Семь задач: данные и загрузчики (1), маршруты и замена сметы (2), ядро
   подбора (3), сцена и панель (4), цели, файл, поиск (5), экран — панель
   (6), экран — поиск и цели (7). Прогон «до/после», снятия, скорость,
   снимки и devlog — финал оркестратора.
2. Отметки читают загрузчики, ядро получает готовые поля:
   `WorkHistoryLine.price_error` ставит `load_work_history` (соединение с
   `price_error_marks`); `KpLine.form_key` — `load_kp_lines`; отметки тендера
   сцены — `load_not_analog_marks(db, tender_id)` в `_build_scene`
   (`_Scene.not_analog`); подписи «ошибки в цене» (причина, автор) —
   `load_price_error_marks(db, ids)` по идентификаторам там, где
   показываются.
3. `kp_work_of(line, net, *, excluded: frozenset[int])` — новый обязательный
   аргумент; сцена считает `excluded` строки как `frozenset` ключей
   `_Scene.not_analog.get(line.form_key, {})`. `work_ladder_of` и
   `evaluate_offer` получают отображение `form_key -> frozenset[int]`
   параметром `not_analog: Mapping[FormKey, frozenset[int]]` (пустое — без
   отметок), без запросов в ядре.
4. Пул без отметок — `WorkHistory.pool_unmarked(family_id)`: тот же
   `_build_pool` с параметром `skip_price_errors: bool`; кэш отдельный.
   `WorkHistory.has_marks(family_id) -> bool` — есть ли в семье строка с
   `price_error`. Строится в двух разных случаях (ред. 2, по ревью Codex):
   - **причина** (`ladder_for`, все поверхности) — только у строки без
     ступени и только при `has_marks` или непустом `kp.excluded` (спека §3
     п. 6);
   - **след** (`excluded_trace`, только панель одной строки) — при
     `has_marks` или непустом `kp.excluded`, **в том числе у строки со
     ступенью**: после исключения подбор мог найти следующую пару, и `S0`
     восстанавливается только по пулу без отметок.

   Разбор КП и этап след не строят, поэтому у строки со ступенью пул без
   отметок там не строится никогда.
5. Маршрут тендерной отметки — в `routers/not_analog.py` с полным путём
   строки КП спеки §2 п. 2; проверку строки КП берёт существующая функция
   сцены строки (та же, что у `reference-search`), без нового SQL на это.
6. Прогон «до/после» спеки §9 п. 16 (ред. 2, по ревью Codex: прежний
   `compare` строк-сумм сравнивает проекцию только у сравнимых «до» и
   пропускает прочие состояния) — новый скрипт
   `docs/superpowers/design/2026-11-23-not-analog/snapshot_lines.py`
   на основе скрипта строк-сумм (снимок и выгрузка — те же; READ ONLY,
   импорт приложения):
   - снимок: у **каждой** строки-работы КП проекция `state`,
     `no_history_reason`, `work_rung`, `range`, `lowest`, `freshest`,
     `contractor`, `analogs_count`, `to_lowest`, `family_id`, `lump` (итог
     «комплект или ставка») и `candidate_ids` (аналоги панели; у строк без
     ступени — пусто);
   - `compare BEFORE AFTER MARKED [OUT.json]`, `MARKED` — JSON со строками
     договоров с отметками и их семьями. Сравнивается проекция **всех**
     строк, любого состояния. Изменение объяснено, только если (а)
     `candidate_ids` «до» пересекаются с `MARKED` — «отмеченный кандидат»,
     или (б) `family_id` строки — семья отмеченной строки — «обычная ставка
     семьи» (смена `lump` строки КП или кандидатов из-за пересчёта ставки).
     Остальное — `unexplained` со списком; код выхода не 0, если он не
     пуст или строка пропала;
   - `selftest` — контрольные входы в памяти, каждый обязан дать
     `unexplained = 1`: `no_history` со сменой `no_history_reason`;
     `unit_mismatch` со сменой `analogs_count`; смена `lump` в семье вне
     `MARKED`; пропавшая строка. И один обязан дать `unexplained = 0`:
     сравнимая строка с отмеченным кандидатом. `selftest` гоняется до
     снимка «после».

   Снимок «до» снимает оркестратор до задачи 3, вывод — в
   `tasks/not-analog-run/` (не под гитом). Скрипт — проверочный
   инструментарий (AGENTS.md §9.1): в runtime не участвует, данных не
   меняет.
7. Подписи пар следа и строки «почему ушёл дальше» строятся на фронте из
   `excluded[].step` и `work_rung` — отдельного поля ответа нет (спека §7
   п. 2 даёт пару у каждой строки).

## Подготовка (оркестратор, до задачи 3)

- [ ] `snapshot_lines.py` по решению плана 6; `selftest` — зелёный
  (четыре контрольных входа дают `unexplained = 1`, один — `0`).
- [ ] Снимок «до» на `gca_dev` кодом базового коммита ветки →
  `tasks/not-analog-run/before.json`.

## Задачи

### Task 1: данные, загрузчики, обычная ставка

**Files:** миграция `0030`; `models.py` (`PriceErrorMark`, `NotAnalogMark`);
`crud/not_analog.py` (новый: `Mark`, `FormKey`, `load_not_analog_marks`,
`load_price_error_marks`); `crud/our_price_load.py` (`load_kp_lines` →
`KpLine.form_key`; `load_work_history` → `WorkHistoryLine.price_error`);
`services/our_price.py` (`KpLine.form_key`); `services/analog_ladder.py`
(`WorkHistoryLine.price_error`); `crud/lump_sum.py` (`load_typical_rates`
без строк с «ошибкой в цене»); тесты
`tests/integration/test_not_analog_schema.py` (новый),
`test_our_price_load.py`, `test_lump_sum_load.py`;
`docs/reference/schema.md`.

**Interfaces**
- Потребляет (существуют): `normalize_title`, `PositionItem`, `Tender`,
  `UnitOfMeasure`, `User`, `load_kp_lines`, `load_work_history`,
  `load_typical_rates`.
- Производит:

```python
# models.py
class PriceErrorMark(Base):   # price_error_marks: position_item_id BigInteger PK FK CASCADE,
                              # reason Text NOT NULL, set_by Integer FK users RESTRICT, set_at timestamptz
class NotAnalogMark(Base):    # not_analog_marks: id BigInteger PK, tender_id BigInteger FK CASCADE,
                              # form_title Text NOT NULL, form_unit_id Integer NULL FK RESTRICT,
                              # position_item_id BigInteger FK CASCADE, reason, set_by, set_at
CK_MARK_REASON = "char_length(reason) BETWEEN 1 AND 300"
# crud/not_analog.py
FormKey = tuple[str, int | None]
@dataclass(frozen=True)
class Mark:
    kind: Literal["price_error", "tender"]
    reason: str
    set_by_id: int
    set_by_email: str
    set_at: datetime
def load_not_analog_marks(db: Session, tender_id: int) -> Mapping[FormKey, Mapping[int, Mark]]
def load_price_error_marks(db: Session, position_item_ids: Collection[int]) -> dict[int, Mark]
# services/our_price.py — KpLine: form_key: FormKey | None = None
# services/analog_ladder.py — WorkHistoryLine: price_error: bool = False
```

**Утверждения**
- Миграция: обе таблицы; `CHECK` причины (пустая и 301 символ —
  `IntegrityError` с именем ограничения), `form_title <> ''`; уникальность
  тройки с `form_unit_id NULL` — второй такой же ряд отклоняется; каскад при
  удалении строки сметы и тендера; `RESTRICT` по автору и единице;
  `downgrade`.
- `KpLine.form_key`: «Монтаж  Ёмкости» с неразрывным пробелом и единицей м³
  — `("монтаж емкости", id м³)`; строка без единицы — `(…, None)`.
- `load_not_analog_marks`: по тендеру, ключ формы → строка договора →
  `Mark` с email автора; отметки другого тендера не приходят.
- `load_work_history`: строка с отметкой — `price_error = True`, остальные
  — `False`; пустой набор `load_price_error_marks` — без запроса.
- Обычная ставка: строка с «ошибкой в цене» не входит в пул; пул из трёх,
  одна из которых с отметкой, — ставки нет; тендерная отметка пул не
  меняет.

**Проверка**
- `pytest tests/integration/test_not_analog_schema.py
  tests/integration/test_our_price_load.py tests/integration/test_lump_sum_load.py`
  — ДО: 139 + 53 = 192, ПОСЛЕ: больше на новый файл и утверждения;
  `ruff check .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

### Task 2: маршруты отметок и замена сметы

**Files:** `crud/not_analog.py` (`set_price_error`, `clear_price_error`,
`set_not_analog`, `clear_not_analog`); `routers/not_analog.py` (новый);
`main.py`; `services/estimate_import.py` (`_replace_existing`); тесты
`tests/integration/test_not_analog_api.py` (новый),
`test_estimate_import.py`; `AGENTS.md` (роли `admin`, ревизия).

**Interfaces**
- Потребляет: задачу 1; существуют `require_admin`, `DomainError`,
  `raise_domain_error`, `CODE_POSITION_ITEM_NOT_FOUND`, `_replace_existing`,
  проверка строки КП маршрутов `_LINE`.
- Производит:

```python
# crud/not_analog.py
CODE_NOT_ANALOG_NOT_APPLICABLE = "not_analog_not_applicable"
def set_price_error(db: Session, *, position_item_id: int, reason: str, actor_id: int) -> Mark
def clear_price_error(db: Session, *, position_item_id: int) -> None
def set_not_analog(db: Session, *, tender_id: int, round_id: int, offer_id: int, kp_item_id: int,
                   position_item_id: int, reason: str, actor_id: int) -> Mark
def clear_not_analog(db: Session, *, tender_id: int, round_id: int, offer_id: int, kp_item_id: int,
                     position_item_id: int) -> None
def mark_view(mark: Mark) -> dict   # MarkView спеки §7 п. 1
# routers/not_analog.py
class MarkRequest(BaseModel):
    reason: str   # обрезка краёв, 1..300
# PUT/DELETE /api/v1/position-items/{position_item_id}/price-error
# PUT/DELETE /api/v1/tenders/{tender_id}/rounds/{round_id}/our-price/offers/{offer_id}/lines/{kp_item_id}/not-analog/{position_item_id}
```

**Утверждения**
- `PUT` у `admin` пишет причину, автора, время; та же причина — `200`,
  автор и время прежние (запрос другим администратором автора не меняет);
  другая — новые автор и время; ответ — `MarkView`.
- Тендерная отметка пишется на ключ формы строки КП из пути; та же строка
  договора от второй строки КП с тем же ключом — одна запись.
- `DELETE` с записью — удаляет, `204`; без записи — `204`.
- `member` — `403` на все четыре маршрута.
- `404 position_item_not_found`; строка КП не в оффере — существующий 404
  строки.
- `422 not_analog_not_applicable` — каждое по отдельности: смета КП (не
  договора), глава, контекст `SYSTEM`. Строка без варианта, с ценой 0, с
  поправкой «комплект» — отметку принимают.
- Тело: пустая причина, из пробелов, 301 символ — `422`; 300 — принимается.
- Строка договора блокируется `FOR UPDATE`.
- Замена сметы договора с одной «ошибкой в цене» и одной тендерной
  отметкой: `warnings` — «Отметки «не аналог» по строкам прежней сметы (2)
  не перенесены — поставьте заново»; без отметок — предупреждения нет;
  отметки удалены каскадом.

**Проверка**
- `pytest tests/integration/test_not_analog_api.py
  tests/integration/test_estimate_import.py` — ДО: 92, ПОСЛЕ: больше;
  `ruff check .`; `uv run python scripts/check_agents_index.py`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

### Task 3: ядро подбора — исключение, след, причина, блок А

**Files:** `services/analog_ladder.py` (`NoHistoryReason`, `KpWork.excluded`,
`_build_pool`, `WorkHistory.pool_unmarked`, `WorkHistory.has_marks`,
`ladder_for`, `parts_reference`, `_no_history_reason`, `WorkLadder.base_reason`,
`ExcludedStep`, `ExcludedTrace`, `excluded_trace`);
`services/reference_blocks.py` (`opens_zone_block`, `zone_groups`, блок
связей); тесты `tests/unit/test_not_analog_ladder.py` (новый),
`test_analog_ladder.py`, `test_ref_analogs_blocks.py`;
`docs/reference/money-axes.md` («Аналоги работы»: две отметки, след,
причина).

**Interfaces**
- Потребляет: задачу 1 (`WorkHistoryLine.price_error`); существуют
  `_FamilyPool`, `ladder_for`, `rung_slice`, `known`, `dropped_on`,
  `_scopes`, `_RUNGS`, `_known_maps`, `parts_reference`, `zone_groups`.
- Производит:

```python
NoHistoryReason = Literal["excluded", "no_family", "path_conflict", "composite", "no_params", "no_zone_match"]
# KpWork: excluded: frozenset[int] = frozenset()
# WorkLadder: base_reason: NoHistoryReason | None = None
ExcludedStep = tuple[ZoneScope, RungNo]
ExcludedTrace = tuple[tuple[_Candidate, ExcludedStep], ...]
class WorkHistory:
    def pool_unmarked(self, family_id: int) -> _FamilyPool
    def has_marks(self, family_id: int) -> bool
def excluded_trace(kp: KpWork, history: WorkHistory, *, tender_class_id: int) -> ExcludedTrace
# services/reference_blocks.py
def opens_zone_block(ladder: WorkLadder) -> bool
```

**Утверждения**
- «Ошибка в цене»: кандидат с `price_error` нет в ступени, остатке,
  выбросах и медиане их отбора, самой низкой, свежей, «с этим
  подрядчиком», `vat_dropped`, блоках А, Г, связей, «по частям» — по входу
  на каждого читателя; соседняя строка той же ступени — в вилке.
- Тендерная отметка: строка из `kp.excluded` не кандидат для этой `KpWork`
  в ступени, `dropped_on`, «по частям», блоке зон, блоке связей; для
  `KpWork` без неё та же строка — кандидат.
- Лестница идёт дальше: единственный кандидат пары «своя зона, ступень 4»
  исключён, в другой зоне той же ступени два кандидата — ступень 4,
  `zone_scope = "other"`, вилка из двух; выбросы пересчитаны по оставшимся
  (кандидат, бывший выбросом при отмеченном, перестаёт им быть).
- Причина: ступени нет, без отметок нашлась бы — `excluded`; все кандидаты
  семьи с «ошибкой в цене» — `excluded`, не `no_family` (Review Focus 2);
  без кандидатов и без отметок — прежняя причина; `base_reason` у
  `excluded` — причина прежней цепочки по обычному пулу, у прочих строк без
  ступени — равна причине, у строки со ступенью — `None`.
- След: пары от `S0` до `S1` включительно, у каждого кандидата — первая
  пара, где он стоял бы; сценарий двутавра (одна строка своей зоны
  исключена, ступень найдена в другой зоне) — след из одной строки с парой
  `("own", 4)`; все исключены — след по всем парам до конца; отметка,
  не задевшая подбор (кандидат пары после `S1`), — не в следе; `S0` нет —
  пусто.
- Пул без отметок (счётчик построений, решение плана 4): в `ladder_for` —
  только у строки без ступени и при `has_marks` или непустом
  `kp.excluded`; у строки со ступенью и у строки без ступени в семье без
  отметок — 0 построений. В `excluded_trace` — строится и у строки со
  ступенью при отметках (сценарий двутавра: ступень найдена в другой зоне,
  след из строки своей зоны); без отметок — 0 построений, след пуст.
- `opens_zone_block`: `no_zone_match` — да; `excluded` при `base_reason ==
  "no_zone_match"` — да; `excluded` при другом `base_reason` — нет;
  `zone_groups` у семьи с флажком при `excluded` над `no_zone_match` отдаёт
  группу другой зоны (Review Focus 1).
- Существующие тесты лестницы и блоков зелёные без правки ожиданий.

**Проверка**
- `pytest tests/unit/test_not_analog_ladder.py tests/unit/test_analog_ladder.py
  tests/unit/test_ref_analogs_blocks.py` — ДО: 153 + 52 = 205, ПОСЛЕ:
  больше; один раз в конце — `tests/unit`; `ruff check .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

### Task 4: сцена и панель строки

**Files:** `services/our_price.py` (`kp_work_of`, `work_ladder_of`,
`_evaluate_work`, `evaluate_offer` — параметр `not_analog`);
`crud/our_price.py` (`_Scene.not_analog`, `_build_scene`, `_zone_groups_of`
через `opens_zone_block`, `build_line_view` — `excluded`, `_excluded_view`);
`crud/reference_search.py` (`search_reference` через `opens_zone_block`);
тесты `tests/integration/test_not_analog_panel.py` (новый),
`test_our_price_offer_api.py`, `test_ref_analogs_panel.py`,
`test_our_price_ladder.py`.

**Interfaces**
- Потребляет: задачи 1 и 3; существуют `_build_scene`, `_Scene`,
  `_analogs_view`, `build_line_view`, `_zone_groups_of`, `search_reference`,
  `money_pair`.
- Производит:

```python
# services/our_price.py
def kp_work_of(line: KpLine, net: Decimal, *, excluded: frozenset[int]) -> KpWork
def work_ladder_of(line: KpLine, *, ..., not_analog: Mapping[FormKey, frozenset[int]]) -> tuple[Decimal | None, WorkLadder | None]
# evaluate_offer(..., not_analog: Mapping[FormKey, frozenset[int]])
# crud/our_price.py
# _Scene: not_analog: Mapping[FormKey, Mapping[int, Mark]]
def _excluded_view(
    scene: _Scene,
    participant: _Participant,
    row: KpLineRow,
    trace: ExcludedTrace,
    details: Mapping[int, tuple[str, str | None, list[str], int | None, int | None]],  # как у _analogs_view
    tree: Mapping[int, CategoryMeta],
    steps: Mapping[int, tuple[ChapterStep, ...]],
    price_errors: Mapping[int, Mark],
) -> list[dict]
# build_line_view (ред. 2, по ревью Codex): след строки считается до загрузки деталей;
#   load_analog_details получает [*_analog_ids(result), *reference.ids(), *trace_ids] — один запрос;
#   chapter_ids и дерево статей — по тому же объединённому набору (пути и статьи исключённых строк);
#   price_errors = load_price_error_marks(db, trace_ids) — один запрос; тендерные отметки — из scene.not_analog.
# ответ панели: excluded: ExcludedAnalogView[] (спека §7 п. 2); line.no_history_reason: "excluded"
```

**Утверждения**
- Тендерная отметка действует у строк КП всех офферов и этапов тендера с
  тем же ключом, у строки того же текста в другом разделе; не действует в
  другом тендере и при другой единице (Review Focus 4) — через ответ
  разбора и панели.
- Панель: `excluded` — поля аналога (договор, объект, подрядчик,
  наименование, цена `{net, shown}`, период, зона, `article` и
  `chapter_path` строки договора — статья и путь её раздела, не строки КП),
  `step`, `marks` («ошибка в цене» первой, с автором и причиной), порядок
  следа; у строки без отметок — `[]`; у строки без ступени по причине без
  отметок — `[]`. Сценарий двутавра (ступень найдена в другой зоне) —
  `excluded` из одной карточки с путём её сметы, `analogs` — две.
- Панель грузит детали один раз: число запросов `load_analog_details` и
  `load_price_error_marks` на запрос панели — по одному.
- Строка с обеими отметками: после снятия «ошибки в цене» — в `excluded`
  этого тендера с одной тендерной отметкой; в другом тендере — снова в
  `analogs` (Review Focus 5).
- Причина `excluded` в ответе разбора, этапа и панели.
- Блок А при `excluded` над `no_zone_match` — в панели (`_zone_groups_of`)
  и в области поиска (`search_reference`) (Review Focus 1).
- Цифры строк без отметок — те же, что до задачи (существующие тесты
  зелёные без правки ожиданий).

**Проверка**
- `pytest tests/integration/test_not_analog_panel.py
  tests/integration/test_our_price_offer_api.py
  tests/integration/test_ref_analogs_panel.py tests/unit/test_our_price_ladder.py`
  — ДО: 149 + 18 + 104 = 271, ПОСЛЕ: больше; один раз в конце —
  `test_our_price_round_api.py`, `test_our_price_signal.py`,
  `test_ref_analogs_search.py`; `ruff check .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → коммит.

### Task 5: цели, файл подрядчику, комментарий, поиск

**Files:** `services/our_price.py` (`TargetResetReason`, `resolve_target`);
`services/kp_file.py` (`FileContext.excluded_targets`,
`line_comment_inputs`, место вызова у строки файла); `crud/our_price.py`
(`_target_excluded`, оба вызова `resolve_target`, сборка `FileContext`);
`crud/kp_decisions.py` (`put_reference_target`, `CODE_REFERENCE_IS_EXCLUDED`);
`crud/reference_lines.py` (отметки строки для строки КП поиска, свёртка);
`crud/reference_search.py`, `crud/our_price.py` (`reference_row_view` —
`marks`); тесты `tests/integration/test_not_analog_targets.py` (новый),
`test_analog_targets.py`, `test_ref_analogs_target.py`,
`test_ref_analogs_search.py`, `tests/unit/test_kp_file_rows.py`,
`tests/unit/test_kp_file_comment.py`, `test_kp_file_export.py`.

**Interfaces**
- Потребляет: задачи 1, 3, 4; существуют `resolve_target`,
  `_target_lump`, `_decision_fields`, `_comment_inputs`, `FileContext`,
  `lump_targets`, `line_comment_inputs`, `put_reference_target`,
  `reference_lines_query`, `reference_lines_from_rows`, `ReferenceLine`,
  `reference_row_view`.
- Производит:

```python
TargetResetReason = Literal["kind_changed", "lump_sum", "excluded", "not_comparable", "analog_unavailable",
                            "no_freshest", "no_contractor", "reference_unavailable"]
def resolve_target(saved, result, *, is_system: bool, reference_available: bool = False,
                   target_lump: bool = False, target_excluded: bool) -> TargetChoice | None
def line_comment_inputs(..., target_lump: bool = False, target_excluded: bool) -> CommentInputs
# FileContext: excluded_targets: frozenset[int] = frozenset()
def _target_excluded(scene: _Scene, references: _ReferenceTargets | None, saved: SavedTarget | None,
                     line: KpLine) -> bool
CODE_REFERENCE_IS_EXCLUDED = "reference_is_excluded"
# ReferenceLine: marks: tuple[Mark, ...] = ()
# строка поиска: marks: MarkView[]
```

**Утверждения**
- `resolve_target`: цель `analog`/`reference` с `target_excluded` —
  `excluded` раньше `reference_unavailable`, `not_comparable`,
  `analog_unavailable`; `lump_sum` и `kind_changed` — раньше `excluded`;
  без признака — как до задачи.
- Вызов `resolve_target` и `line_comment_inputs` без `target_excluded` —
  `TypeError` (обязательный параметр).
- Одна цена на трёх поверхностях (Review Focus 3): справочная цель из
  поиска, затем строка отмечена — отдельно «ошибкой в цене» и тендерной
  отметкой; панель — `choice.active = false`, `reset_reason = "excluded"`;
  предпросмотр файла, выгруженный XLSX (настоящая сборка) и текст
  комментария — наша цена равна самой низкой, как в панели. Снятие
  отметки возвращает цель в действие на всех трёх.
- Сравнимая строка с целью `analog` на строку, которую отметили, а другие
  аналоги есть: экран — `excluded`; файл — самая низкая из оставшихся.
- `put_reference_target` на строку с отметкой, действующей для строки КП, —
  `422 reference_is_excluded` раньше проверок пересчёта; тендерная
  отметка другого ключа формы — не мешает.
- Поиск: отмеченная строка в выдаче с `marks` (только действующие для
  строки КП поиска), `SEARCH_LIMIT` и порядок прежние; строка с отметкой не
  сворачивается с соседкой того же ключа свёртки.

**Проверка**
- `pytest tests/integration/test_not_analog_targets.py
  tests/integration/test_analog_targets.py
  tests/integration/test_ref_analogs_target.py
  tests/integration/test_ref_analogs_search.py tests/unit/test_kp_file_rows.py
  tests/unit/test_kp_file_comment.py tests/integration/test_kp_file_export.py`
  — ДО: 67 + 134 + 49 + 190 + 165 + 104 = 709, ПОСЛЕ: больше; один раз в
  конце — `tests/unit`, `test_ref_analogs_file.py`,
  `test_ref_analogs_lines.py`; `ruff check .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → коммит.

### Task 6: экран — панель строки

**Files:** `types/domain.ts` (`NoHistoryReason` + `"excluded"`, `MarkView`,
`ExcludedAnalog`, `OurPriceLineView.excluded`); `services/api/domain.ts`
(`positionItemsApi.putPriceError`, `deletePriceError`,
`tendersApi.putNotAnalog`, `deleteNotAnalog`); `services/queries.ts`
(`useSetMark`, `useClearMark`, сброс как у поправки строк-сумм);
`ourPriceCopy.ts` (тексты меню, окон, меток, пар, причины);
`MarkDialog.tsx` (новый), `ExcludedAnalogs.tsx` (новый), `AnalogsTable.tsx`
(меню из трёх пунктов), `LineSheet.tsx` (список, строка «почему ушёл
дальше», пояснение причины), `OfferLinesTable.tsx` (подпись причины);
тесты: `ExcludedAnalogs.test.tsx`, `MarkDialog.test.tsx` (новые),
`AnalogsTable.test.tsx`, `LineSheet.test.tsx`, `LineSheet.work.test.tsx`,
`OfferLinesTable.test.tsx`, `ourPriceCopy.test.ts`; `test/fixtures.ts`
(`excluded: []` по умолчанию); `docs/reference/screens.md`.

**Interfaces**
- Потребляет: ответ задач 4–5, маршруты задачи 2.
- Производит:

```ts
export type MarkKind = "price_error" | "tender";
export interface MarkView { kind: MarkKind; reason: string; set_by: UserRef; set_at: string }
export interface ExcludedAnalog extends Omit<OurPriceAnalog, "outlier_ratio" | "is_lowest" | "is_freshest" | "selectable"> {
  step: { rung: 1 | 2 | 3 | 4; zone_scope: ZoneScope };
  marks: MarkView[];
}
// новое: идентификаторы строки КП для тендерной отметки
export interface LineIds { tenderId: number; roundId: number; offerId: number; kpItemId: number }
export type SetMarkVars = LineIds & { kind: MarkKind; positionItemId: number; reason: string };
export type ClearMarkVars = LineIds & { kind: MarkKind; positionItemId: number };
export function useSetMark(): UseMutationResult<void, unknown, SetMarkVars>
export function useClearMark(): UseMutationResult<void, unknown, ClearMarkVars>
export function MarkDialog(props: { kind: MarkKind; title: string; scopeText: string; open: boolean;
  onOpenChange(open: boolean): void; onSubmit(reason: string): void; busy: boolean }): JSX.Element
export function ExcludedAnalogs(props: { items: ExcludedAnalog[]; workRung: WorkRung | null; vat: VatView;
  canEdit: boolean; ids: LineIds }): JSX.Element | null
export function movedOnText(items: ExcludedAnalog[], workRung: WorkRung | null): string | null
```

**Утверждения**
- Меню аналога у `admin` — три пункта; у `member` — нет; пункт отметки
  открывает окно с текстом области действия (макет, разделы 1–2) и полем
  причины; «Исключить» недоступно при пустой причине и больше 300
  символов, занято до ответа; после ответа сброшены
  `qk.tenders.ourPriceAll` и `kpFilePreviewsAll`.
- «Исключены (N)» — свёрнут, есть только при непустом `excluded`; карточка
  с `LineWhere`; подписи пар при нескольких парах; метка «был бы на
  ступени N · …» у строки не с пары `work_rung`.
- Две отметки — отдельными блоками со своими «Снять»; «Снять во всех
  тендерах…» — через окно подтверждения (текст макета, раздел 4), «Снять в
  этом тендере» — сразу; у `member` кнопок нет.
- `movedOnText`: пара следа раньше пары `work_rung` — «Ступень 4, другие
  зоны: на ступени 4 своей зоны аналоги исключены вручную (1)»; несколько
  пар — через «;»; у строки без ступени и без пар раньше — `null`.
- Причина `excluded`: в таблице — «Аналоги исключены вручную», строка в
  «не разобраны»; в панели — пояснение спеки §8 п. 4 и раскрытый список.
- Тексты не переносятся и не наезжают на узкой панели (снимок стенда —
  финал).

**Проверка**
- из `C:\Users\zhukov_v\Projects\GCA_MVP\frontend` (PowerShell, `Set-Location` с заглавной `C:` — иначе
  vitest не находит раннер):
  `npx vitest run src/pages/tenders/our-price/ExcludedAnalogs.test.tsx src/pages/tenders/our-price/MarkDialog.test.tsx
  src/pages/tenders/our-price/AnalogsTable.test.tsx src/pages/tenders/our-price/LineSheet.test.tsx src/pages/tenders/our-price/LineSheet.work.test.tsx
  src/pages/tenders/our-price/OfferLinesTable.test.tsx src/pages/tenders/our-price/ourPriceCopy.test.ts` — ДО (замер
  `npx vitest list` на `aa31560`): 28 + 76 + 70 + 69 + 82 = 325, двух новых
  файлов нет; ПОСЛЕ: больше 325, и в выводе есть оба новых файла (без них
  команда выбирает только старые наборы); `npx tsc -b`; `npx eslint .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

### Task 7: экран — поиск и цели

**Files:** `ReferenceSearch.tsx` (`SearchMarks`), `ReferenceRows.tsx`
(метки, закрытый выбор цели, меню отметок у `admin`); `TargetCards.tsx`
(причина); `ourPriceCopy.ts` (`TARGET_RESET_REASON.excluded`,
`EXCLUDED_NOT_TARGET`); `types/domain.ts` (`TargetResetReason`,
`RefRow.marks`); `services/queries.ts` (`REFERENCE_DRIFT_CODES` +
`reference_is_excluded`); тесты `ReferenceSearch.test.tsx`,
`ReferenceBlocks.test.tsx`, `TargetCards.test.tsx`;
`docs/reference/screens.md` (метки в поиске).

**Interfaces**
- Потребляет: задачу 6 (`MarkView`, `MarkDialog`, `useSetMark`,
  `useClearMark`), ответ задачи 5.
- Производит:

```ts
// TARGET_RESET_REASON.excluded = "строка исключена из аналогов"
export const EXCLUDED_NOT_TARGET = "Исключена из аналогов";
// RefRow: marks: MarkView[]
```

**Утверждения**
- Поиск: у строки с отметками — метки вида с областью, причина в
  подсказке; «сделать целью» недоступно с подсказкой «Исключена из
  аналогов»; у строки без отметок — как сегодня.
- Поиск у `admin`: те же пункты меню и «Снять», что в панели.
- Карточка недействующей цели с `excluded` — «строка исключена из
  аналогов».
- Отказ `reference_is_excluded` при выборе цели — панель перечитывается.
- Справочные блоки (А, Г, связи) — без изменений: отмеченных строк в них
  нет (тест блока с фикстурой без отметок не меняется).

**Проверка**
- оттуда же: `npx vitest run src/pages/tenders/our-price/ReferenceSearch.test.tsx
  src/pages/tenders/our-price/ReferenceBlocks.test.tsx src/pages/tenders/our-price/TargetCards.test.tsx` — ДО (замер на
  `aa31560`): 27 + 27 + 36 = 90; ПОСЛЕ: больше 90 — новые утверждения
  задачи ложатся в `ReferenceSearch.test.tsx` (метки, закрытая цель, меню,
  отказ `reference_is_excluded`) и `TargetCards.test.tsx` (причина);
  `ReferenceBlocks.test.tsx` — без новых тестов, 27 зелёных без правки
  ожиданий; `npx tsc -b`; `npx eslint .`.

- [ ] Тесты по утверждениям (красные) → реализация → зелёные → документация → коммит.

## Финал (оркестратор)

- [ ] Снятия критических свойств спеки §9 п. 1–10 в копии дерева, каждое
  восстановлено побайтно; результат — в devlog.
- [ ] `gca_dev` до 0030 (стенд остановлен на время миграции); «ошибка в
  цене» на строках VEER (плинтус) и Тушино (м/к технического балкона) —
  записью в учётки стенда только по слову пользователя (временный админ —
  `cli.py create-user`, предупредив); снимок «после» и `compare BEFORE
  AFTER MARKED` (решение плана 6) с двумя строками и их семьями:
  `unexplained = 0` по проекции всех строк любого состояния, объяснённые —
  списками «отмеченный кандидат» и «обычная ставка семьи» с причиной;
  плинтус тендера 3 — один аналог МИРА (спека §9 п. 16).
- [ ] Скорость (спека §9 п. 15): разбор КП, этап, панель — до/после, без
  отметок и с двумя; числа в devlog.
- [ ] Снимки стенда по спеке §9 п. 17 в обеих темах, сверка с макетом.
- [ ] `docs/devlog/2026-11-23-not-analog.md`; пуш; черновик → готов к
  ревью по слову пользователя.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: GitHub Actions на пуше (локальный `just ci` не гоняется —
  решение пользователя 11.10.2026); стенд — финал.
