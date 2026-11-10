# План: упрощение управления каталогом, часть 1

**Спека:** `docs/superpowers/specs/2026-11-10-catalog-simplify-design.md` (гейт 2 одобрен 10.11.2026, четыре круга Codex, редакция 4, `b713e82`)
**Дизайн:** `docs/superpowers/design/2026-11-10-catalog-simplify.md` (редакция 2), макеты — `.../2026-11-10-catalog-simplify/mockup.html` (ред. 10), `journal.html`
**Стоит на:** спеки вариантов, 4а, 4б и лестницы аналогов — все в `main` (`97d5f4d`)
**Ветка:** `feat/catalog-simplify`, черновой PR #75
**Статус:** редакция 1, 10.11.2026, ждёт ревью гейта 3.

> Исполнителю: задачи идут по порядку; каждая — цикл TDD
> (`superpowers:test-driven-development`: тесты первыми, красный прогон
> показать). Адреса `§N` без уточнения — разделы спеки; «решение N» —
> пронумерованные решения этого плана. **Защит, дублирующих предикат, не
> класть.** Числа проверок — нижние границы. ДО сняты 10.11.2026 на `b713e82`:
> `test_simplify` — 0 в `tests/unit` и `tests/integration`. **Все новые
> бэкенд-тесты — в файлах `test_simplify_*.py`, шаблон `-k test_simplify`.**
> Тесты вариантов, семантики, лестницы, 4а и 4б остаются зелёными; их
> ожидания правятся **только** там, где задача называет изменение.

## Режим реализации (как у лестницы, с 28.10.2026)

- **Исполнители** — Sonnet; оркестратор (Opus) принимает задачу, коммитит,
  ведёт `progress.md`.
- **Проверки по риску.** Отдельное ревью (Opus, узкий бриф: свойства задачи
  и «что сделало бы этот тест ложно-зелёным») — только у задач с пометкой
  **«ревью»**: 1, 2, 3, 5, 6, 7. Остальные принимает оркестратор: тесты
  задачи, `ruff`, `tsc`, `eslint`, у фронта — снимок стенда рядом с макетом.
- **Снятия защит — только на критических свойствах** (список ниже), делает
  оркестратор лично, в копии дерева (стенд отдаёт живое дерево).
- **Прогоны.** Исполнитель гоняет наборы затронутых модулей и их вызывающих;
  полный бэкенд и `just ci` локально не гоняются — проверяет GitHub Actions
  на PR (`gh pr checks 75`), красное чинится следующим коммитом.
- **Без остановок** между задачами; стоп — пуш, внешнее ревью, платный прогон
  на стенде (Task 13) и вопросы, где допущение небезопасно.
- **Финальное ревью ветки** — Codex (комментарий в PR #75).

## Global Constraints

- **Деньги** — `Decimal` до границы ответа, строки через `decimal_json`, ни
  одного `float`; в JS деньги не складываются, не перемножаются и не делятся
  (`AGENTS.md` §3). Итоги уровней дерева — только с сервера (§2.10).
- **Суммы узлов** — правило сумм статей (`docs/reference/money-axes.md` §7):
  все конечные `total_cost_total`, отрицательные тоже; предикат цены
  (`money.price.is_price`, SQL `_price_ok`) — только пригодность расценки.
- **Наружу (модели)** — только наименования, цепочки разделов, единицы,
  имена семей и параметров, значения параметров (`AGENTS.md` §3); новый вид
  ответа `several` состав не расширяет; приватность — как у `context_values`.
- **Модель не видит составных значений** нигде (§2.3, место 1).
- **Решение человека сильнее автоматики** (§2.6): вето состава, «не дубль»,
  `auto_split_blocked_at`, `routed_by='manual'` — ни модель, ни прогоны, ни
  команды их не снимают; снимает только `restore-auto`.
- **Одно значение на параметр у контекста**; ключ варианта и
  `context_parameter_values` не меняются.
- **Чтение не пишет**: `GET` страницы участника, панели, журнала.
- **Права:** все новые маршруты — `require_admin`, кроме чтения «Против
  нашей цены» (`get_current_user`, как сейчас). Сторож
  `tests/test_auth_coverage.py` зелёный после каждой задачи с маршрутами.
- **Фронтенд** — только shadcn/ui и `ui-domain`; новые примитивы — `npx
  shadcn add`; цвета меток — парой «светлая / тёмная» в `index.css`.
- **Репозиторий публичный:** настоящие объекты, подрядчики, номера договоров,
  цены стенда — ни в тесты, ни в devlog; условные имена.
- **Стенд `gca_dev`** — запись только командой фичи и через приложение;
  учётки стенда не трогать; кириллицу в терминал не печатать; платные вызовы
  — только по согласию пользователя, с оценкой стоимости.
- **Номер новой версии `AGENTS.md`** в плане и спеке не называется (страж).

## Критические свойства (снятия оркестратора)

| № | Свойство | Задача | Снятие → ожидаемо красный тест |
|---|---|---|---|
| К1 | модель не видит составных — все три запроса | 2 | убрать фильтр `composite_kind IS NULL` в одном запросе (по очереди в каждом из трёх) → тест рендера этого запроса |
| К2 | вето сильнее всех путей составного и переживает слияние **и затем** новую версию схемы | 2, 3 | убрать проверку вето в `find_or_create_composite` → тесты `several`, фильтра 1, заморозки; убрать переписывание норм в слиянии → тест «отмена → слить A в A′ → новая версия → `several/[A′, B]`» |
| К3 | «вместе» ≠ «или» | 2 | убрать вид из `composite_key` → тест «Knauf + Gyproc и Knauf или Gyproc — два значения» |
| К4 | фильтр 1 не режет цельные значения | 3 | снять условие «каждая часть в списке» → тест «ГО и ЧС» остаётся целым |
| К5 | правило деления покрывает свою группу и только её | 5 | заменить выбор правила на `chapter_level_equals` с уровнем от корня → тест цепочек разной глубины; снять проверку чужих групп → тест перемаршрутизации |
| К6 | отмена деления недоступна после ручной правки и берёт нынешних членов | 5 | снять проверку событий → тест «перенос строки → отмена → 409»; брать членов из payload → тест «строка пришла после деления» |
| К7 | форма значения на ступенях 2/4; «по частям» по `value_id` части | 6 | убрать форму из ключа → тест «плиты не аналог плиты + балки»; искать части ключом ступени 2 → тест «отпускаемый тип, плиты 36, балки 45 — вилки разные» |
| К8 | «с этим подрядчиком» — только из остатка | 6 | искать по всем аналогам → тест «договор подрядчика — выброс → карточки нет, `PUT` 422» |
| К9 | итоги узлов по правилу сумм статей и согласованы с корнем | 7 | суммировать предикатом цены → тест «+100 и −20 → 80, корень = `articles`» |
| К10 | чтение не пишет | 4, 7 | — (тест счётчиков транзакции, как в 4а; снятие не нужно) |

## Review Focus

1. **Слитое значение в ответе `several`** (часть пришла текстом слитого
   синонима): канонизация до снятия повторов; схлопывание до одной части →
   простое значение (Task 3).
2. **Контекст «к делению», у которого после деления пришла новая смета КП**
   с теми же разделами: строки идут по правилам в свои части, отмена берёт и
   их (Task 5).
3. **Закрытый параметр и `several` с частью не из списка** → `off_list` с
   собранным текстом, составного нет (Task 3).
4. **Строка КП без раздела** (`chapter_item_id` есть, `work_category_id`
   пуст) и без цепочки: «Нераспределённое», пустой `chapter_path`, дерево не
   падает (Task 7, Task 8).
5. **Сборная строка, у которой одна часть без аналогов**: блок «по частям»
   показывает часть с «нет аналогов», цель не появляется (Task 6, Task 9).

## Структура файлов

```
backend/
  alembic/versions/2026_11_10_0025-catalog_simplify.py   создаётся (Task 1)
  models.py                       правка: FamilyParameterValue.composite_kind/composite_key,
                                  FamilyValuePart, CompositeVeto, CompositeVetoPart, ValueNotDuplicate,
                                  CatalogContext.auto_split_blocked_at/_by, CatalogAutoAction,
                                  ContextRoutingRule.created_by NULL, KP_TARGET_KINDS + contractor,
                                  SemanticJobKind + auto_split (Task 1)
  services/composite_values.py    создаётся: составное, пересборка после слияния (Task 2)
  services/human_decisions.py     создаётся: вето, «не дубль», переписывание норм, перенос семьи (Task 2)
  services/ai_journal.py          создаётся: запись автодействий (Task 2)
  services/variant_request.py, services/releasable.py   правка: фильтр составных (Task 2); промпт v2 (Task 3)
  services/work_variants.py       правка: слияние (Task 2); перенос и заморозка (Task 2); apply_values (Task 3)
  services/schema_edit.py         правка: вывод части, отказ schema_composite_text (Task 2)
  services/work_families.py       правка: перенос решений при слиянии семей (Task 2)
  services/value_filters.py       создаётся: разрез склейки, фильтры 1 и 2 (Task 3)
  services/variant_answer.py      правка: вид several (Task 3)
  crud/ai_journal.py              создаётся: чтение, undo, restore-auto (Task 4); split (Task 5)
  services/auto_split.py          создаётся: группы, правила, деление, отмена, resplit (Task 5)
  services/context_operations.py  правка: actor_id: int | None (Task 5)
  services/semantic_worker.py, services/semantic_cost.py   правка: ветка auto_split (Task 5)
  services/analog_ladder.py       правка: форма в ключе, composite, parts_reference (Task 6)
  services/our_price.py           правка: цель contractor (Task 6)
  crud/our_price_load.py          правка: составные значения строк, цепочки, категории (Task 6, 7)
  crud/our_price.py               правка: ответы строки и панели (Task 6); дерево и node_totals (Task 7)
  crud/kp_decisions.py, routers/tenders.py   правка: contractor (Task 6)
  routers/semantic.py             правка: схема (Task 2), журнал (Task 4, 5)
  cli.py                          правка: catalog-simplify-backfill (Task 11)
  tests/unit/test_simplify_*.py, tests/integration/test_simplify_*.py   создаются
frontend/src/
  types/domain.ts, services/api/domain.ts, services/queryKeys.ts, services/queries.ts,
  test/handlers.ts, test/fixtures.ts                      правка (Task 8, 9, 10)
  index.css                                               правка: цвета меток (Task 9)
  pages/tenders/our-price/
    OurPriceOfferPage.tsx, OfferArticlesTable.tsx, OfferLinesTable.tsx, offerFilter.ts   правка (Task 8)
    OfferTree.tsx, offerTree.ts                           создаются (Task 8)
    LineSheet.tsx, AnalogsTable.tsx, ExportBlock.tsx, ourPriceCopy.ts   правка (Task 9)
    TargetCards.tsx, PartsReference.tsx, LineWhere.tsx    создаются (Task 9); TargetRow.tsx удаляется (Task 9)
  pages/families/
    FamiliesPage.tsx, entryParams.ts, SchemaBlock.tsx, schema-edit/ValueChip.tsx   правка (Task 10)
    JournalTab.tsx, JournalCard.tsx                       создаются (Task 10)
AGENTS.md, docs/AGENTS-revisions.md, docs/reference/{schema,screens}.md   правка (Task 12)
docs/devlog/2026-11-10-catalog-simplify.md создаётся, docs/product-roadmap.md правка (Task 13)
```

## Решения плана, которых нет в спеке

1. **Порядок задач:** данные (1) → составное и решения человека (2) → ответ
   модели и фильтры (3) → журнал без деления (4) → автоделение (5) →
   лестница и цель (6) → дерево на сервере (7) → фронт дерева (8) → фронт
   панели (9) → фронт «Семей» (10) → команда первого прогона (11) →
   `AGENTS.md` и справочник (12) → стенд и devlog (13). Каталог раньше
   лестницы: лестнице нужны составные значения в фикстурах.
2. **Автоделение — новое задание очереди `auto_split`** (`SemanticJobKind`,
   субъект — `context_id`). `apply_values`, поставив `path_conflict`, ставит
   задание; воркер вызывает модель по группам **вне** транзакции (тот же
   рендер `render_values_request` на путях группы, `SEMANTIC_MODEL`, тарифы
   `SEMANTIC_PRICE_*`), затем `auto_split_context` под замком корзины
   сверяет отпечаток членов (`member_ids_hash`) — изменился → задание
   `cancelled` по `input_changed`. Вызов модели внедряется параметром `ask`
   (тесты подставляют ответы).
3. **Модули.** Чистые и без `db`: `value_filters.split_candidate`,
   `auto_split.plan_groups`. С `db`: `composite_values`, `human_decisions`,
   `ai_journal` (запись), `crud/ai_journal` (чтение и действия).
   `composite_values` зависит от `human_decisions` (вето) и `ai_journal`;
   обратных импортов нет.
4. **Журнал пишет тот, кто действует:** `record_action` вызывают
   `find_or_create_composite` (`composite`), фильтр 2 (`equated`),
   `_extend_with_value` (`value_added`), `auto_split_context` (`split`).
   Запись в журнал — в той же транзакции, что действие.
5. **Признак «сборная» строки КП** грузится вместе со значениями варианта:
   `WorkHistoryLine` и `KpWork` получают `forms: Mapping[parameter_id,
   Form]`, где `Form = Literal["simple"] | int` (id составного) —
   `_values_key` берёт форму отпускаемых отсюда.
6. **Дерево на фронте** строится чистой функцией `buildOfferTree(offer)`
   (`offerTree.ts`) из `categories`, `lines[].category_id`,
   `lines[].chapter_path`; итоги — только из `node_totals`. Прежний
   `OfferArticlesTable` остаётся корнем дерева (колонки корня не меняются).
7. **`TargetRow` удаляется**: выбор цели — карточки `TargetCards` в секции
   сравнения; `ExportBlock` показывает итог и источник.
8. **Счётчик новых записей журнала** — `localStorage` ключ
   `gca.journal.seenAt`, в `try/catch`; без него — счётчика нет.

## Задачи

### Task 1: миграция `0025` и модели — **ревью**

**Files**
- Create: `backend/alembic/versions/2026_11_10_0025-catalog_simplify.py`
- Edit: `backend/models.py`, `backend/tests/conftest.py` (`_DOMAIN_TABLES`)
- Test: `backend/tests/integration/test_simplify_schema.py`

**Interfaces**
- Потребляет: `FamilyParameterValue`, `CatalogContext`, `ContextRoutingRule`, `KpLineTarget`, `SemanticJob`, `SEMANTIC_EVENT_TYPES` (существуют).
- Производит:

```python
COMPOSITE_KINDS = ("all", "any")
AUTO_ACTION_KINDS = ("split", "value_added", "composite", "equated")
KP_TARGET_KINDS = ("freshest", "analog", "contractor")
# SemanticJobKind: + auto_split  (субъект — context_id)
# FamilyParameterValue: composite_kind: str | None, composite_key: str | None
class FamilyValuePart(Base):      # "family_value_parts": value_id, part_value_id, parameter_id
class CompositeVeto(Base):        # "composite_vetoes": id, family_id, parameter_name_norm, kind, value_norm, set_by, set_at
class CompositeVetoPart(Base):    # "composite_veto_parts": veto_id, part_norm
class ValueNotDuplicate(Base):    # "value_not_duplicates": id, family_id, parameter_name_norm, text_norm, value_norm, set_by, set_at
class CatalogAutoAction(Base):    # "catalog_auto_actions": id, kind, family_id, context_id, parameter_id, value_id,
                                  #   payload, created_at, undone_at, undone_by, restored_at
# CatalogContext: auto_split_blocked_at, auto_split_blocked_by
# ContextRoutingRule.created_by: int | None
```

**Утверждения**
- `alembic upgrade head` создаёт всё из §2.13 с именами
  `pk_/fk_/uq_/ck_<таблица>_…`; `just db-test-check` без дрейфа;
- CHECK по одному входу на каждый: `composite_kind = 'some'`;
  `composite_kind` без `composite_key` и наоборот; часть другого параметра
  (составной FK); `value_id = part_value_id`; `kind` автодействия вне
  четырёх; `kp_line_targets.kind = 'contractor'` с `analog_position_item_id`
  отказывает, без — проходит; задание `auto_split` без `context_id`;
- частичный уникальный индекс: два действующих составных с одним
  `composite_key` у параметра — отказ; то же при `merged_into_id` у одного —
  проходит;
- правило маршрутизации с `created_by = NULL` вставляется;
- паритет CHECK модели и миграции — `TestParityWithMigration`;
- downgrade отказывает при строке в `family_value_parts`, цели `contractor`,
  строке `catalog_auto_actions` — с сообщением, ничего не удалив; на пустых
  — снимает всё новое, `created_by` снова `NOT NULL`.

**Имена**
- Заводятся: пять классов и таблиц, колонки, `COMPOSITE_KINDS`, `AUTO_ACTION_KINDS`, вид `auto_split`, ревизия `0025`.
- Существуют: `KP_TARGET_KINDS`, `SemanticJobKind`, `_DOMAIN_TABLES`, `TestParityWithMigration`.

**Проверка**
- `just test-int-local-k test_simplify` — ДО 0, ПОСЛЕ ≥ 18.
- `just db-test-check` — зелёная; `just test-int-local-k test_analog` — зелёная.

### Task 2: составное значение, решения человека, пять мест — **ревью**

**Files**
- Create: `backend/services/composite_values.py`, `backend/services/human_decisions.py`, `backend/services/ai_journal.py`
- Edit: `backend/services/variant_request.py`, `backend/services/releasable.py`, `backend/services/work_variants.py` (`merge_parameter_values`, `_carry_human_parameters`), `backend/services/schema_edit.py` (`_write_rebuild`, `apply_schema_edit`), `backend/services/work_families.py` (`merge_families`), `backend/routers/semantic.py` (схема)
- Test: `backend/tests/integration/test_simplify_composite.py`, `test_simplify_decisions.py`

**Interfaces**
- Потребляет: модели Task 1; `canonical_value_id`, `normalize_value`, `bump_revision`, `record_event` (существуют).
- Производит:

```python
# services/ai_journal.py
def record_action(db: Session, *, kind: str, family_id: int, payload: dict,
                  context_id: int | None = None, parameter_id: int | None = None,
                  value_id: int | None = None) -> int

# services/human_decisions.py
def resolve_veto(db: Session, *, parameter_id: int, kind: str, part_ids: frozenset[int]) -> int | None
def is_not_duplicate(db: Session, *, parameter_id: int, text_norm: str, value_id: int) -> bool
def add_veto(db: Session, *, parameter_id: int, kind: str, part_ids: Sequence[int], value_id: int, user_id: int) -> int
def add_not_duplicate(db: Session, *, parameter_id: int, text_norm: str, value_id: int, user_id: int) -> int
def rewrite_after_merge(db: Session, *, parameter_id: int, source_value_id: int, canonical_value_id: int) -> None
def transfer_family(db: Session, *, source_family_id: int, target_family_id: int) -> None

# services/composite_values.py
COMPOSITE_JOIN = {"all": " + ", "any": " или "}
def composite_key(kind: str, part_ids: Iterable[int]) -> str
def find_or_create_composite(db: Session, *, parameter_id: int, kind: str, part_ids: Sequence[int],
                             context_id: int | None, source_text: str | None) -> int
def composite_parts(db: Session, value_ids: Iterable[int]) -> dict[int, tuple[str, tuple[int, ...]]]
def rebuild_after_part_merge(db: Session, *, parameter_id: int, source_id: int, canonical_id: int, actor_id: int | None) -> None
REFUSE_MERGE_COMPOSITE_MISMATCH = "merge_values_composite_mismatch"
REFUSE_SCHEMA_COMPOSITE_TEXT = "schema_composite_text"
```

**Утверждения**
- инварианты §2.3 (1–5) — по тесту на каждый; текст «плиты + балки» /
  «Knauf П113 или Gyproc П-1М-1ГСП», части в порядке id;
- **К3:** «Knauf + Gyproc» и «Knauf или Gyproc» — два разных `value_id`;
  повторный `find_or_create_composite` с теми же частями в другом порядке —
  тот же id; создание пишет одну запись журнала `composite`;
- **К1:** рендер `load_schema_parameters`, `load_human_parameters` и запроса
  `releasable` не содержит текста составного, содержит его части;
- **место 2:** слияние «плиты покрытий» → «перекрытия» превращает
  «плиты покрытий + балки» в «перекрытия + балки»; если такое уже есть —
  составные слиты, контексты и варианты сведены; слияние части в другую
  часть того же составного — составное слито в оставшееся простое; слияние
  составного с простым и составных разного вида через API — 422
  `merge_values_composite_mismatch`;
- **место 3:** перестройка параметра выводит часть — составное с ней
  выведено тем же `retired_at`;
- **место 4:** `GET …/schema` отдаёт `composite: {kind, part_ids}`; `PATCH`
  с изменённым текстом составного — 422 `schema_composite_text`;
- **место 5 (перенос):** новая версия схемы переносит составное параметра
  человека с частями и ключом; составное, потерявшее части, не переносится,
  контексты — `none` (заморозка схемы — Task 3: ей нужен фильтр 1);
- **К2 (вето):** вето «A, B → V»: `find_or_create_composite([A, B])`
  возвращает V; после слияния A в A′ — `[A′, B]` даёт V (нормы вето
  переписаны на A′); после этого новая версия схемы — `[A′, B]` даёт V в
  новой версии; два вето в один состав — побеждает позднее (`set_at`, `id`);
  части вето схлопнулись — вето молчит; «не дубль» «T → W» переживает
  слияние W в W′ и новую версию;
- слияние семей переносит решения на семью-цель.

**Имена**
- Заводятся: три модуля, функции и константы выше.
- Существуют: `merge_parameter_values`, `_carry_human_parameters`, `freeze_schema`, `_write_rebuild`, `apply_schema_edit`, `merge_families`, `load_schema_parameters`, `load_human_parameters`.

**Проверка**
- `just test-int-local-k test_simplify` — ДО ≥ 18, ПОСЛЕ ≥ 50.
- `just test-int-local-k test_work_variants`, `-k test_catalog_parameters`, `-k test_semantic` — зелёные.

### Task 3: ответ `several` и два фильтра — **ревью**

**Files**
- Create: `backend/services/value_filters.py`
- Edit: `backend/services/variant_answer.py`, `backend/services/variant_request.py` (промпт, `VALUES_PROMPT_VERSION = 2`), `backend/services/work_variants.py` (`apply_values`, `_extend_with_value`, `freeze_schema`)
- Test: `backend/tests/unit/test_simplify_filters.py`, `backend/tests/integration/test_simplify_apply.py`

**Interfaces**
- Потребляет: `find_or_create_composite`, `resolve_veto`, `is_not_duplicate`, `record_action` (Task 2); `normalize_job_title_with_lemmatization` (`parser.sanitize_text`, существует).
- Производит:

```python
# services/value_filters.py
@dataclass(frozen=True)
class SplitCandidate:
    kind: Literal["all", "any"]
    parts: tuple[str, ...]
def split_candidate(text: str) -> SplitCandidate | None          # чистая
def lemma_norm(text: str) -> str
@dataclass(frozen=True)
class ResolvedValue:
    value_id: int | None
    action: Literal["existing", "composite", "equated", "added", "off_list", "none"]
def resolve_new_value(db: Session, *, parameter_id: int, text: str, context_id: int) -> ResolvedValue
def resolve_several(db: Session, *, parameter_id: int, combine: str, texts: Sequence[str], context_id: int) -> ResolvedValue

# services/variant_answer.py — элемент ответа:
# {ordinal, kind: "value"|"new"|"conflict"|"none"|"several", value, values, combine, source}
```

**Утверждения**
- `split_candidate`: «балки, капители, трансферные конструкции» → `all`, три
  части; «Knauf П113 или Gyproc П-1М-1ГСП» → `any`; «3,6 м», «В12,5», `1/2"`,
  «ГО» → `None`; «плиты и балки» → `all`;
- **К4:** `new` «ГО и ЧС» (частей нет в списке) и «трубопровод с изоляцией и
  арматурой» — заводятся целыми, `action = 'added'`; `new` «колонны, пилоны,
  трансферные конструкции» при всех частях в списке — составное;
- порядок для `new`: точная `value_norm` существующего — `existing`, фильтры
  не применяются (в том числе для текста V после отмены разбора);
- фильтр 2: «плиты перекрытия» при «плита перекрытий» — `equated`, запись
  журнала с исходным текстом; два совпадения по лемме — `added`; пара «не
  дубль» — `added`; «плиты покрытий» при «перекрытия» — `added`;
- `several`: элементы канонизируются сквозь слияния; повторы снимаются;
  одна часть → простое; элемент, разрешившийся в составное того же вида, —
  раскрыт; другого вида — `none`; закрытый параметр и часть не из списка —
  `off_list` с собранным текстом; вето — V;
- разбор: `several` без `combine`, с одним значением, с повтором текста —
  `answer_schema_error`;
- `value_added` пишется на каждое новое простое значение (в том числе
  часть `several`);
- **место 5 (заморозка):** заморозка схемы со значениями «балки»,
  «капители», «балки, капители» заводит третье составным из первых двух;
  вето состава уважается;
- промпт v2 содержит правило `several`/`conflict` §2.4; отпечаток запроса
  меняется, контексты не перезапрашиваются (тест: `variant_paths_hash` не
  тронут).

**Имена**
- Заводятся: модуль и имена выше, вид `several`.
- Существуют: `apply_values`, `_extend_with_value`, `get_or_create_value`, `_parse_value_item`, `parse_values_answer`, `VALUES_RESPONSE_FORMAT`, `VALUES_PROMPT_VERSION`.

**Проверка**
- `just test-unit-k test_simplify` — ДО 0, ПОСЛЕ ≥ 15.
- `just test-int-local-k test_simplify` — ДО ≥ 50, ПОСЛЕ ≥ 75.
- `just test-int-local-k test_work_variants`, `just test-unit-k test_variant` — зелёные.

### Task 4: журнал — чтение, отмена склейки и приравнивания, «вернуть автоматику»

**Files**
- Create: `backend/crud/ai_journal.py`
- Edit: `backend/routers/semantic.py`
- Test: `backend/tests/integration/test_simplify_journal.py`

**Interfaces**
- Потребляет: `CatalogAutoAction`, `add_veto`, `add_not_duplicate`, `composite_parts` (Task 1–2).
- Производит:

```python
class JournalItem(TypedDict):
    id: int; kind: str; created_at: str
    family: dict; context: dict | None; parameter: dict | None
    text: str | None; summary_values: list[str]
    state: Literal["active", "undone", "locked"]
    undo: dict          # {available: bool, locked_by: {event_type, actor_email, at} | None}
    undone: dict | None # {at, by_email}
def list_journal(db, *, kind: str | None, family_id: int | None, state: str | None, q: str | None,
                 limit: int, offset: int) -> dict        # {items, total, counts}
def get_journal_item(db, item_id: int) -> dict
def undo_action(db, item_id: int, *, user_id: int) -> dict
def restore_auto(db, item_id: int, *, user_id: int) -> dict
# GET /semantic/journal, GET /semantic/journal/{id},
# POST /semantic/journal/{id}/undo, POST /semantic/journal/{id}/restore-auto
REFUSE_JOURNAL_NOT_FOUND = "journal_not_found"; REFUSE_JOURNAL_UNDONE = "journal_already_undone"
```

**Утверждения**
- список: порядок `created_at DESC, id DESC`; отборы по виду, семье,
  состоянию, тексту; `counts` по четырём видам при любом отборе вида;
- `undo` `composite`: значение простое с исходным текстом, частей нет,
  контексты на нём же, вето записано; повторный `several` по тем же частям
  даёт это значение;
- `undo` `equated`: заведено простое значение из текста, контексты записи
  на нём, «не дубль» записано; повторный `new` с тем же текстом — не
  приравнен;
- `undo` `value_added` — 409 (действие — слияние через схему);
- повторный `undo` — 409 `journal_already_undone`; несуществующая — 404;
- `restore-auto` снимает вето/«не дубль» записи, `restored_at` пишется;
- **К10:** `GET` не пишет;
- права: `member` — 403 на все четыре маршрута.

**Имена**
- Заводятся: модуль, функции, маршруты, коды.
- Существуют: `require_admin`, `_mutating`.

**Проверка**
- `just test-int-local-k test_simplify` — ДО ≥ 75, ПОСЛЕ ≥ 90; `tests/test_auth_coverage.py` — зелёная.

### Task 5: автоделение, отмена и «поправить» — **ревью**

**Files**
- Create: `backend/services/auto_split.py`
- Edit: `backend/services/context_operations.py` (`split_context`, `merge_contexts`: `actor_id: int | None`), `backend/services/work_variants.py` (`apply_values`: постановка `auto_split`), `backend/services/semantic_worker.py`, `backend/services/semantic_cost.py`, `backend/crud/ai_journal.py`, `backend/routers/semantic.py`
- Test: `backend/tests/unit/test_simplify_split_plan.py`, `backend/tests/integration/test_simplify_auto_split.py`

**Interfaces**
- Потребляет: `chapter_paths`, `evaluate_predicate`, `_apply_routing`, `split_context`, `merge_contexts`, `render_values_request`, `apply_values` (существуют); `record_action` (Task 2).
- Производит:

```python
@dataclass(frozen=True)
class SplitGroup:
    title: str                      # заголовок на уровне расхождения
    member_ids: tuple[int, ...]
@dataclass(frozen=True)
class SplitPlan:
    level: int                      # от корня
    groups: tuple[SplitGroup, ...]
def plan_groups(paths: Mapping[int, Sequence[str]], *, level: int | None = None) -> SplitPlan | None   # чистая
def choose_rule(group: SplitGroup, others: Sequence[SplitGroup], chains: Mapping[int, ChapterContext],
                earlier_rules: Sequence[RulePredicate]) -> RulePredicate | None
AskGroupValues = Callable[[int, SplitGroup], Mapping[int, "ValueAnswer"]]
@dataclass(frozen=True)
class AutoSplitOutcome:
    status: Literal["split", "resolved", "not_split", "input_changed"]
    action_id: int | None
    reason: str | None
def auto_split_context(db: Session, *, context_id: int, ask: AskGroupValues,
                       expected_members_hash: str, level: int | None = None) -> AutoSplitOutcome
def undo_split(db: Session, action_id: int, *, user_id: int) -> None
def split_lock(db: Session, action_id: int) -> dict | None   # {event_type, actor_email, at} | None
# POST /semantic/journal/{id}/resplit {level}
REFUSE_JOURNAL_UNDO_LOCKED = "journal_undo_locked"
```

**Утверждения**
- `plan_groups`: «Смета → Корпус 2 → Стены» и «Смета → Корпус 2 →
  Колонны» → уровень 2, две группы; «Стены:» и «стены» — одна группа;
  одинаковые пути — `None`;
- **К5:** цепочки разной глубины («Смета → Корпус → Надземная часть →
  Колонны» и «Смета → Корпус → Колонны»): правило выбрано
  (`chapter_chain_contains` «Колонны» или `nearest_chapter_equals`), реальный
  `evaluate_predicate` покрывает всех членов группы; заголовок группы есть в
  цепочке члена другой группы → правило отвергнуто, контекст не делится,
  журнал пуст;
- группы с одинаковыми ответами модели сливаются; одна группа → значения
  поставлены (`source = 'path'`), пометка снята, журнал пуст
  (`resolved`);
- деление: части получают семью и значения своих групп без нового
  запроса, `variant_split_hint` снят у всех, правила `created_by = NULL`,
  события с `actor_id = NULL`, одна запись журнала `split` с группами;
- отпечаток членов изменился между ответом модели и записью →
  `input_changed`, ничего не записано;
- **К6:** `undo` берёт нынешних членов (строка, пришедшая по правилу после
  деления, — в общем контексте), снимает только свои правила, ставит
  `path_conflict` и `auto_split_blocked_at`; после ручного `members/move`
  в часть — 409 `journal_undo_locked` с `locked_by`; часть архивирована —
  409; `GET` записи отдаёт `parts: [{context_id, title, members_now,
  members_after}]`;
- `resplit {level}` — отмена и деление на уровне в одной транзакции;
- `apply_values`, поставив `path_conflict`, ставит задание `auto_split`;
  у контекста с `auto_split_blocked_at` — не ставит, а уже стоящее —
  `cancelled` `not_applicable`; стоимость вызовов считается тарифами
  `SEMANTIC_PRICE_*`.

**Имена**
- Заводятся: модуль и имена выше, ветка воркера `auto_split`.
- Существуют: `split_context`, `merge_contexts`, `chapter_paths`, `chapter_context`, `evaluate_predicate`, `RulePredicate`, `ChapterContext`, `render_values_request`, `SemanticCancelReason`.

**Проверка**
- `just test-unit-k test_simplify` — ДО ≥ 15, ПОСЛЕ ≥ 25.
- `just test-int-local-k test_simplify` — ДО ≥ 90, ПОСЛЕ ≥ 115.
- `just test-int-local-k test_semantic_context`, `-k test_context_routing` — зелёные.

### Task 6: лестница — форма значения, «по частям», цель «с этим подрядчиком» — **ревью**

**Files**
- Edit: `backend/services/analog_ladder.py`, `backend/services/our_price.py` (`resolve_target`, `target_analog`), `backend/crud/our_price_load.py` (формы значений), `backend/crud/our_price.py` (`_line_view`, `build_line_view`), `backend/crud/kp_decisions.py` (`put_target`), `backend/routers/tenders.py` (`TargetPut`)
- Test: `backend/tests/unit/test_simplify_ladder.py`, `backend/tests/integration/test_simplify_target.py`

**Interfaces**
- Потребляет: `composite_parts` (Task 2); `ladder_for`, `_values_key`, `WorkHistoryLine`, `KpWork`, `resolve_target` (существуют).
- Производит:

```python
Form = Literal["simple"] | int
# WorkHistoryLine.forms, KpWork.forms: Mapping[int, Form]
# NoHistoryReason: + "composite"  (порядок: no_family, path_conflict, composite, no_params, no_zone_match)
@dataclass(frozen=True)
class PartReference:
    value_id: int; value: str; analogs: int; contracts: int
    min: Decimal | None; max: Decimal | None
    lowest_analog_id: int | None; other_class: bool
def parts_reference(kp: KpWork, history: WorkHistory, *, tender_class_id: int | None) -> list[dict]
TargetKind = Literal["freshest", "analog", "contractor"]
CODE_NO_CONTRACTOR_ANALOG = "no_contractor_analog"
# reset reason: + "no_contractor" (после no_freshest)
# _line_view: + "contractor": {value, analog_id, contract_id, period} | None; + "parts_reference" (только панель)
```

**Утверждения**
- **К7:** отпускаемый «Тип конструкции»: строка «плиты + балки» на ступени 2
  не берёт «плиты», берёт «плиты + балки»; строка «плиты» не берёт
  «плиты + балки», берёт «балки» (как сейчас — простое с простым);
  неотпускаемый — без изменений; ступени 1/3 — без изменений (тесты
  лестницы зелёные);
- сборная строка без кандидатов → `no_history_reason = 'composite'`, подпись
  с именем параметра;
- **К7:** «по частям» при отпускаемом типе: плиты 36–42, балки 45–51 —
  разные вилки и разные `lowest`; часть без аналогов в классе — другой
  класс с `other_class = true`; ни там ни там — `analogs = 0`;
  `PUT …/target` с аналогом «по частям» — 422 `analog_unavailable`;
- **К8:** «с этим подрядчиком» — самая свежая среди аналогов остатка с
  подрядчиком КП; договор подрядчика — выброс → `contractor = null`,
  `PUT kind=contractor` — 422 `no_contractor_analog`; сохранённая цель
  после ухода аналога — сброс `no_contractor`; выбор не меняет суммы и
  плитки (как К7 лестницы);
- файл подрядчику и комментарий у цели `contractor` берут её аналог.

**Имена**
- Заводятся: `Form`, `forms`, `PartReference`, `parts_reference`, вид `contractor`, коды.
- Существуют: `ladder_for`, `_values_key`, `_required_ids`, `WorkHistory`, `resolve_target`, `target_analog`, `put_target`, `TargetPut`, `_analogs_view`.

**Проверка**
- `just test-unit-k test_simplify` — ДО ≥ 25, ПОСЛЕ ≥ 37; `just test-int-local-k test_simplify` — ДО ≥ 115, ПОСЛЕ ≥ 125.
- `just test-unit-k test_analog`, `just test-int-local-k test_analog`, `-k test_our_price`, `-k test_kp_file` — зелёные.

### Task 7: страница участника на сервере — категории, цепочки, итоги узлов — **ревью**

**Files**
- Edit: `backend/crud/our_price_load.py` (`load_kp_lines`, `category_roots` → полное дерево), `backend/crud/our_price.py` (`build_offer_view`), `backend/services/our_price_money.py`
- Test: `backend/tests/integration/test_simplify_offer_tree.py`, `backend/tests/unit/test_simplify_node_totals.py`

**Interfaces**
- Потребляет: `chapter_paths`, `category_rows`, `net_by_root`, `money_pair` (существуют).
- Производит:

```python
@dataclass(frozen=True)
class CategoryMeta:
    id: int; parent_id: int | None; code: str | None; title: str; sort_order: int | None
def category_tree(db: Session) -> tuple[dict[int, int], dict[int, CategoryMeta]]   # root_of, все статьи
def node_totals(lines: Sequence[KpLineRow], *, base: Decimal | None) -> dict   # {categories: {id: NodeTotals}, chapters: {id: NodeTotals}}
# NodeTotals = {lines: int, no_price: int, kp_total: {net, shown} | None}
# ответ оффера: + categories, lines[].category_id, lines[].chapter_path [{chapter_item_id, title, number}], node_totals
```

**Утверждения**
- `categories` — все статьи на путях строк от корня до листа, без лишних;
- строка без статьи раздела — `category_id = null`; без раздела —
  `chapter_path = []`; дерево собирается;
- **К9:** строки +100 и −20 одной статьи → `kp_total.net = 80`, у корня
  `node_totals` равен сумме из `articles`; строка без цены входит в сумму
  своим `total_cost_total`, если он конечен, и в `no_price`; база НДС
  неизвестна → `kp_total = null`; нулевая сумма — `"0.00"`, не `null`;
- `chapter_path` — от корня сметы, `number` — номер раздела;
- **К10:** `GET` оффера не пишет; число запросов к базе не растёт с числом
  строк (цепочки — одним пакетным `chapter_paths`).

**Имена**
- Заводятся: `CategoryMeta`, `category_tree`, `node_totals`, поля ответа.
- Существуют: `load_kp_lines`, `category_roots`, `KpLineRow`, `build_offer_view`, `chapter_paths`.

**Проверка**
- `just test-int-local-k test_simplify` — ДО ≥ 125, ПОСЛЕ ≥ 135; `just test-unit-k test_simplify` — ДО ≥ 37, ПОСЛЕ ≥ 42.
- `just test-int-local-k test_our_price` — зелёная.

### Task 8: фронт — дерево статей и разделов на странице участника

**Files**
- Create: `frontend/src/pages/tenders/our-price/offerTree.ts`, `OfferTree.tsx`
- Edit: `OurPriceOfferPage.tsx` (`container-wide`), `OfferArticlesTable.tsx`, `OfferLinesTable.tsx`, `offerFilter.ts`, `frontend/src/types/domain.ts`, `frontend/src/test/fixtures.ts`, `frontend/src/test/handlers.ts`
- Test: `offerTree.test.ts`, `OfferTree.test.tsx`

**Interfaces**
- Потребляет: поля ответа Task 7.
- Производит:

```ts
type OfferTreeNode =
  | { kind: "category"; id: number; depth: number; title: string; code: string | null; totals: NodeTotals; children: OfferTreeNode[] }
  | { kind: "chapter"; id: number; depth: number; title: string; totals: NodeTotals; children: OfferTreeNode[] }
  | { kind: "line"; line: OurPriceLine; depth: number };
function buildOfferTree(offer: OurPriceOffer): OfferTreeNode[];
type NoPriceMode = "show" | "hide" | "only";
```

**Утверждения**
- дерево: корень → промежуточные → лист → разделы сметы → строки; раздел
  без корня сметы и без заголовков, совпадающих с названием статьи;
  «Нераспределённое» — последним;
- корень показывает прежние колонки (₽/м², вилка, «статья целиком»,
  полосы); нижние уровни — строки, сумма КП (`node_totals`, текстом из
  ответа, без арифметики), «N без цены»;
- строки `no_price` — красные; «скрыть» убирает их и пустые уровни;
  «только они» раскрывает всё; итоги уровней при любом режиме — те же;
- сортировка по столбцу выпрямляет дерево в список с путём под названием;
- страница — `container-wide`; `?article=` раскрывает свою ветку.

**Имена**
- Заводятся: `buildOfferTree`, `OfferTreeNode`, `NoPriceMode`, `OfferTree`.
- Существуют: `OfferArticlesTable`, `OfferLinesTable`, `matchesFilter`, `OurPriceOffer`.

**Проверка**
- `cd frontend && npx vitest run src/pages/tenders/our-price` — зелёная; новых тестов ≥ 14 (ДО — 0 в `offerTree`/`OfferTree`).
- `npx tsc --noEmit`, `npx eslint src/pages/tenders/our-price` — чисто; снимок стенда рядом с макетом (вкладка «Таблица строк»), обе темы.

### Task 9: фронт — панель строки

**Files**
- Create: `TargetCards.tsx`, `PartsReference.tsx`, `LineWhere.tsx` (в `pages/tenders/our-price/`)
- Edit: `LineSheet.tsx` (`sm:max-w-[680px]`, порядок секций §2.11), `AnalogsTable.tsx` (пять и «ещё N»), `ExportBlock.tsx`, `ourPriceCopy.ts`, `frontend/src/index.css`, типы, `queries.ts` (`usePutTarget` + `contractor`), фикстуры
- Delete: `TargetRow.tsx` и его тест
- Test: `TargetCards.test.tsx`, `PartsReference.test.tsx`, `LineSheet.test.tsx` (правка)

**Interfaces**
- Потребляет: `contractor`, `parts_reference`, `no_history_reason = 'composite'` (Task 6); `chapter_path` (Task 7).
- Производит: `TargetCards({line, analogs, canEdit, onChoose})`, `PartsReference({groups, vat})`, `LineWhere({line, categories})`; копии `TARGET_CARD`, `EXPORT_SOURCE`, `COMPOSITE_NO_TARGET`.

**Утверждения**
- карточки по стратегиям («самая низкая», «самая свежая», «с этим
  подрядчиком»), не объединяются; вторая на том же договоре — подпись «тот
  же договор, что …»; нажатие — `PUT kind=freshest|contractor` или `DELETE`
  для самой низкой; нет договора подрядчика — карточки нет;
- «сделать целью» у любого аналога остатка — `PUT kind=analog`; кнопка
  скрыта только у закреплённого; скрытый аналог после выбора — в пятёрке;
- аналогов не больше пяти: сначала с метками цели, затем по цене; «ещё N
  аналога из M»;
- сборная строка без сборных аналогов: пояснение «цели нет», блок «по
  частям» без кнопок цели, «В файл подрядчику» — «—» и «в файл ничего не
  идёт», ввод «Договорились» даёт число и «договорились · вписано
  вручную»; шаблона комментария нет;
- источник в «В файл подрядчику»: «наша расценка · цель: …» /
  «договорились · перекрывает цель»;
- метки цветные в обеих темах (`index.css`, пара светлая/тёмная).

**Имена**
- Заводятся: три компонента и копии выше.
- Существуют: `LineSheet`, `AnalogsTable`, `ExportBlock`, `usePutTarget`, `useDeleteTarget`, `TARGET_TEXT`.

**Проверка**
- `cd frontend && npx vitest run src/pages/tenders/our-price` — зелёная; новых тестов ≥ 16.
- `tsc`, `eslint` — чисто; снимки стенда рядом с макетом (вкладки «Панель строки», «Сборная строка»), обе темы.

### Task 10: фронт — «Журнал ИИ» и составные в схеме

**Files**
- Create: `frontend/src/pages/families/JournalTab.tsx`, `JournalCard.tsx`
- Edit: `FamiliesPage.tsx`, `entryParams.ts`, `SchemaBlock.tsx`, `schema-edit/ValueChip.tsx`, `services/api/domain.ts`, `queries.ts`, `queryKeys.ts`, типы, `test/handlers.ts`
- Test: `JournalTab.test.tsx`, `JournalTab.pending.test.tsx`, `FamiliesPage.journalLink.test.tsx`, `SchemaBlock.test.tsx` (правка)

**Interfaces**
- Потребляет: маршруты журнала (Task 4, 5); `composite` в схеме (Task 2).
- Производит: `useJournal(params)`, `useJournalItem(id)`, `useJournalUndo()`, `useJournalResplit()`, `useJournalRestore()`; `qk.journal`; `Entry` + `{tab: "journal", entryId}`.

**Утверждения**
- четвёртая вкладка «Журнал ИИ»; `?tab=journal&entry=<id>` открывает запись;
- список по дням, метки вида, отборы (вид с числами, семья, состояние,
  поиск с задержкой), `Pager`;
- карточка по виду: деление — группы и строки, «Отменить — вернуть как
  было» (подтверждение с нынешними числами), «Поправить» (уровень); при
  `locked` — кнопка недоступна и «отмена недоступна: …»; склейка — «было →
  стало» и контексты с параметрами; новое значение — «Слить с…»
  (`MergeValuesDialog`); приравнивание — «Отвязать»;
- отменённые — зачёркнуты, «Вернуть автоматику»;
- счётчик новых — по `gca.journal.seenAt`; без `localStorage` — без
  счётчика, страница работает;
- схема: составное — пометка «составное» и части, поле текста недоступно.

**Имена**
- Заводятся: два компонента, хуки, ключ, вариант `Entry`.
- Существуют: `FamiliesPage`, `parseEntry`, `ENTRY_PARAMS`, `Pager`, `EntityCombobox`, `useDebounce`, `MergeValuesDialog`, `ValueChip`.

**Проверка**
- `cd frontend && npx vitest run src/pages/families` — зелёная; новых тестов ≥ 14.
- `tsc`, `eslint` — чисто; снимок стенда рядом с `journal.html`, обе темы.

### Task 11: команда первого прогона

**Files**
- Edit: `backend/cli.py`
- Test: `backend/tests/integration/test_simplify_backfill.py`

**Interfaces**
- Потребляет: `find_or_create_composite`, `resolve_veto`, `split_candidate` (Task 2–3); постановка заданий `context_values` и `auto_split` (Task 3, 5).
- Производит: `catalog-simplify-backfill [--dry-run] [--family ID] [--limit N]`; печать — счётчики латиницей и id (кириллицу в терминал не печатать).

**Утверждения**
- склейка, все части которой в списке, — составное на месте (тот же `id`,
  контексты и варианты не тронуты); две склейки одного состава — слиты;
  под вето — не тронута; схлопнувшаяся — слита в часть; «ГО и ЧС» — в
  отчёте, без изменений; запись журнала на каждое;
- контексты «к делению» — задания `context_values` (перезапрос промптом
  v2) и после ответа с конфликтом — `auto_split`; `--limit` ограничивает
  число контекстов;
- `--dry-run` ничего не пишет (счётчик транзакций) и печатает то же, что
  сделал бы;
- повторный запуск идемпотентен.

**Имена**
- Заводится: команда `catalog-simplify-backfill`.
- Существуют: `cli`, `_guard`, `zones-backfill` (образец).

**Проверка**
- `just test-int-local-k test_simplify` — ДО ≥ 135, ПОСЛЕ ≥ 142.

### Task 12: `AGENTS.md` и справочник

**Files**
- Edit: `AGENTS.md` (§3: две строки — модель видит только простые значения; решение человека сильнее автоматики — со ссылкой на спеку), `docs/AGENTS-revisions.md`, `docs/reference/schema.md` (таблицы и колонки §2.13), `docs/reference/screens.md` (дерево, панель, «Журнал ИИ»)

**Утверждения**
- страж `backend/scripts/check_agents_index.py` — 18 из 18;
- справочник описывает сегодняшнее состояние после фичи; ссылки на спеку,
  не пересказ.

**Проверка**
- `cd backend && PYTHONIOENCODING=utf-8 uv run python scripts/check_agents_index.py` — 18 из 18.

### Task 13: стенд, приёмка, devlog

**Files**
- Create: `docs/devlog/2026-11-10-catalog-simplify.md`
- Edit: `docs/product-roadmap.md`

**Утверждения**
- стенд `gca_dev` на `0025`; `catalog-simplify-backfill --dry-run` — отчёт
  пользователю (сколько склеек станет составными, сколько контекстов к
  перезапросу); **стоп:** замер стоимости на 5 контекстах и оценка на 225 —
  пользователю; боевой прогон — только после его согласия;
- приёмка по §8 дизайна, пять пунктов, снимки рядом с макетами, обе темы;
- devlog: решения по ходу, номер техдолга «журнал всех автодействий ИИ»,
  отступление «полный прогон — GitHub Actions»; дорожная карта — пункт 1
  сделан.

**Проверка**
- `gh pr checks 75` — зелёные после пуша.

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: GitHub Actions на PR #75 (`gh pr checks 75`) и приёмка на
  стенде (Task 13).
