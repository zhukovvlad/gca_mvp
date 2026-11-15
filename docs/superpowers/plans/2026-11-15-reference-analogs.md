# План: справочные аналоги — другая зона, поиск по договорам, пересчёт единиц

**Спека:** `docs/superpowers/specs/2026-11-15-reference-analogs-design.md` (редакция 3; гейт 2 — Codex на `c4ad52e` замечаний не имел, ред. 3 добавила `link_id` в пересчёт строки по ревью плана)
**Дизайн:** `docs/superpowers/design/2026-11-15-reference-analogs.md` (редакция 4), макет — `.../2026-11-15-reference-analogs/mockup.html`
**Стоит на:** спеки 4а, 4б, лестницы аналогов, упрощения каталога — все в `main`
**Ветка:** `feat/reference-analogs`, черновой PR #85
**Статус:** редакция 2, 15.11.2026, на гейт 3. По первому кругу Codex (на
`8386040`): запись и снятие цели — только `admin`, как сегодня (Task 7, К7,
Task 9–10); `link_id` в пересчёте строки — и в поиске (спека §2.16 ред. 3,
Task 5, 6, 9); К9 разделено между Task 3 и Task 7; экранирование поиска —
на запросе «a_».

> Исполнителю: задачи идут по порядку; каждая — цикл TDD
> (`superpowers:test-driven-development`: тесты первыми, красный прогон
> показать). Адреса `§N` без уточнения — разделы спеки; «решение N» —
> пронумерованные решения этого плана. **Защит, дублирующих предикат, не
> класть.** Числа проверок — нижние границы. ДО сняты 15.11.2026 на `c4ad52e`:
> `ref_analogs` — 0 совпадений в `backend/` и `frontend/src`. **Все новые
> бэкенд-тесты — в файлах `test_ref_analogs_*.py`, шаблон `-k ref_analogs`**
> (`test_reference` занят: 16 тестов других фич). Тесты 4а, 4б, лестницы и
> упрощения каталога остаются зелёными; их ожидания правятся **только** там,
> где задача называет изменение.

## Режим реализации (как у упрощения каталога, с 28.10.2026)

- **Исполнители** — Sonnet; оркестратор (Opus) принимает задачу, коммитит,
  ведёт `progress.md`.
- **Проверки по риску.** Отдельное ревью (Opus, узкий бриф: свойства задачи
  и «что сделало бы этот тест ложно-зелёным») — только у задач с пометкой
  **«ревью»**: 1, 2, 3, 6, 7, 8. Остальные принимает оркестратор: тесты
  задачи, `ruff`, `tsc`, `eslint`, у фронта — снимок стенда рядом с макетом.
- **Снятия защит — только на критических свойствах (К1–К10 ниже = все строки
  §6 спеки), и делает их оркестратор лично**, после приёмки
  задачи-владельца, в копии дерева (стенд отдаёт живое дерево) по протоколу
  `BASELINE_GREEN / MUTATION_APPLIED / EXPECTED_TEST_FAILURE / RESTORED`,
  итог — в `progress.md`. Исполнитель предъявляет только TDD.
- **Это отступление от `docs/process/implementation.md`** («Распределение
  ответственности»: снятия всех `behavioral`-элементов — ревьюером задачи) —
  по решению пользователя 28.10.2026, как в планах лестницы и упрощения
  каталога; называется в devlog фичи. Процессный документ этим планом не
  меняется.
- **Прогоны.** В итерациях — только новые тесты и точечные файлы; наборы
  соседних фич (`-k "analog or kp_file or simplify or our_price"`)
  исполнитель гоняет **один раз в конце задачи**, ревьюер их не повторяет.
  Полный бэкенд и `just ci` локально не гоняются — проверяет GitHub Actions
  (`gh pr checks 85`), красное чинится следующим коммитом.
- **Без остановок** между задачами; стоп — пуш, внешнее ревью и вопросы, где
  допущение небезопасно.
- **Финальное ревью ветки** — Codex (комментарий в PR #85).

## Global Constraints

- **Деньги** — `Decimal` в `price_basis._CONTEXT` до границы ответа, строки
  через `decimal_json`, ни одного `float`; деление на `N` — без
  промежуточного округления (§2.3); в JS деньги не складываются, не
  перемножаются и не делятся (`AGENTS.md` §3).
- **Справочное не входит в суммы:** вилка, самая низкая, самая свежая,
  `to_lowest`, `to_freshest`, состояния, корзины, плитки, итоги статей и
  этапа — те же до знака со справочной целью и без (§2.1, §2.15).
- **Одно правило выброса** на всех путях (панель, поиск, запись цели): одна
  функция пометок, фронт пометок не считает (§2.10, §3).
- **Пересчёт цели — только из снимка** (§2.14); `factor` связи никогда не
  обращается при записи (§2.6).
- **Состав истории** — как у `load_history`: договоры объекта тендера не
  входят ни в блоки, ни в поиск, ни в цель (§2.3).
- **Чтение не пишет**: `GET` панели, разбора, поиска и связей.
- **Права:** связи — запись только `require_admin` (свой `Depends` на каждом
  маршруте); поиск и блоки — любой вошедший; запись и снятие цели —
  `require_admin`, как сегодня у `PUT`/`DELETE …/target` (§2.19). Сторож `tests/test_auth_coverage.py` зелёный после каждой
  задачи с маршрутами.
- **Наружу ничего нового:** поиск, связи и пересчёт во внешнюю модель не
  уходят (§2.19).
- **Фронтенд** — только shadcn/ui и `ui-domain`; новые примитивы — `npx
  shadcn add`; лоадеры пересчёта — на сером слое (`RecalcOverlay`).
- **В тестах — условные имена** объектов, подрядчиков, номеров договоров;
  цены стенда в тесты и devlog не переносить.
- **Стенд `gca_dev`** — запись только миграцией фичи и через приложение;
  учётки стенда не трогать; кириллицу в терминал не печатать.
- **Номер новой версии `AGENTS.md`** в плане и спеке не называется (страж).

## Критические свойства (снятия оркестратора)

| № | Свойство (§6 спеки) | Задача | Снятие → ожидаемо красный тест |
|---|---|---|---|
| К1 | справочное не входит в суммы | 7 | подмешать значение справочной цели в `to_ours` → тест «итоги оффера и строки равны со справочной целью и без» |
| К2 | деньги пересчёта | 2 | заменить `N` на `1/N` для большей единицы → тест `1 107,24 ÷ 0,1 = 11 072,40` и зеркальный `м³ → м²`; округлить `N` до 4 знаков перед делением → тест «деньги по точному `N`» |
| К3 | подтверждение выброса на всех путях | 7 | убрать проверку `confirm_outlier` → `409` на строке блока А **и** на строке из поиска |
| К4 | снимок пересчёта | 7 | читать `N` из связи вместо `ref_factor` → тест «связь поправлена, цель прежняя, `link_now` есть» |
| К5 | сброс цели | 7 | убрать проверку справочности при чтении → тест «смета договора заменена допсоглашением → `reference_unavailable`» |
| К6 | состав истории | 6 | убрать `exclude_object_id` из поиска → тест «строка договора объекта тендера не находится» |
| К7 | права | 3, 7 | снять `require_admin` с `POST /family-links` → тест `403` у `member` (Task 3); снять `require_admin` с `PUT …/target` → тест «`member` пишет справочную цель → `403`» (Task 7) |
| К8 | направление множителя связи | 3 | обращать `factor` при упорядочивании пары → тест «создать с карточки `hi`, прочитать и сохранить с обеих карточек — `factor`, `factor_from` и `N` прежние» |
| К9 | единица семьи после связи | 3, 7 | Task 3: убрать сверку `unit_lo/hi` с текущими → тест «т → кг: связь `units_changed`». **Окончательное снятие — Task 7:** та же мутация → сквозной тест «цель по связи сохранена → единица семьи т → кг → блока Г в панели нет, значение цели и `ref_factor` прежние» |
| К10 | подтверждённый пересчёт | 7 | убрать сверку `expected_factor` → тест «значение 100 → 200 мм после чтения панели, запись с прежним `expected_factor` → `409 conversion_stale`, цели нет» |

## Review Focus

1. **Строка без единицы** (у строки КП или договора `unit_id` пуст): единицы
   «различны» — пересчёт только вручную, `conversion_required` без
   `conversion`; диалог открывается сразу с ручным вводом (Task 2, 7, 9).
2. **Одна строка договора в своей ступени и в поиске** — пометка выброса в
   выдаче поиска равна пометке в справке лестницы (то же отношение), и
   запись из поиска без `confirm_outlier` получает тот же `409` (Task 6, 7).
3. **«Договорились» поверх справочной цели**: `target_kind = agreed`, файл
   берёт договорённость, «Источник» — «Договорились …», шаблон справочной
   цели не применяется (Task 8).
4. **Значение параметра с запятой и единицей в имени** — «100,5» при «Толщина,
   мм» → `x = 0,1005`, основание «толщина 100,5 мм»; «100-200 мм» и «до 100
   мм» — не числа (Task 2).
5. **Семья КП слита или заархивирована после выбора цели по связи**: связь
   недействующая, блока Г нет, у цели подсказка `link_gone`, цифра — по
   снимку (Task 3, 7).

## Структура файлов

```
backend/
  alembic/versions/2026_11_15_0027-reference_analogs.py   создаётся (Task 1)
  models.py                         правка: FamilyLink, LINK_KINDS, REF_ORIGINS, REF_METHODS,
                                    KP_TARGET_KINDS + reference, колонки KpLineTarget (Task 1)
  tests/conftest.py                 правка: _DOMAIN_TABLES + family_links (Task 1)
  services/reference_math.py        создаётся: число из значения, N, печать N, свёртка (Task 2)
  services/analog_ladder.py         правка: outlier_ratios_of — общее ядро выбросов (Task 2)
  services/family_links.py          создаётся: проверка, состояние, создание/правка/удаление (Task 3)
  routers/semantic.py               правка: маршруты связей (Task 3)
  crud/reference_lines.py           создаётся: справочные строки, значения параметров связей (Task 4)
  services/reference_blocks.py      создаётся: блоки А и Г, пометки (Task 5)
  crud/our_price.py                 правка: other_zones (Task 5), панель (Task 5), line_with_scene (Task 7),
                                    choice/target_kind (Task 7), FileInputs (Task 8)
  crud/our_price_load.py            правка: load_kp_lines(only=…), load_work_history(family_ids=…) (Task 6)
  crud/reference_search.py          создаётся: поиск без сцены (Task 6)
  routers/tenders.py                правка: маршрут поиска (Task 6), TargetPut reference (Task 7)
  crud/kp_decisions.py              правка: загрузка снимка, put_reference_target (Task 7)
  services/our_price.py             правка: TargetKind, resolve_target(reference) (Task 7)
  services/kp_comment.py, services/kp_file.py   правка: справочная цель (Task 8)
  tests/unit/test_ref_analogs_*.py, tests/integration/test_ref_analogs_*.py   создаются
frontend/src/
  types/domain.ts, services/api/domain.ts, services/queryKeys.ts, services/queries.ts,
  test/handlers.ts, test/fixtures.ts                        правка (Task 9, 10)
  pages/tenders/our-price/
    OfferLinesTable.tsx, ourPriceCopy.ts, LineSheet.tsx       правка (Task 9)
    ReferenceBlocks.tsx, ReferenceRows.tsx, ReferenceSearch.tsx, ReferenceTargetDialog.tsx   создаются (Task 9)
    TargetCards.tsx, ExportBlock.tsx                          правка (Task 10)
  pages/families/
    FamiliesTab.tsx                                           правка (Task 10)
    FamilyLinksBlock.tsx, FamilyLinkDialog.tsx                создаются (Task 10)
AGENTS.md, docs/AGENTS-revisions.md, docs/reference/{schema,screens,money-axes}.md   правка (Task 11)
docs/devlog/2026-11-15-reference-analogs.md создаётся (Task 11)
```

## Решения плана, которых нет в спеке

1. **Порядок задач:** схема (1) → чистая арифметика (2) → связи (3) →
   справочные строки (4) → блоки А и Г в панели и `other_zones` (5) → поиск
   (6) → справочная цель: запись и чтение (7) → файл и комментарий (8) →
   фронт панели (9) → фронт цели и «Связей» (10) → `AGENTS.md`, справочник,
   стенд, devlog (11). Цель после поиска: её отказы проверяются на строках
   всех трёх путей.
2. **Единица строки** в §2.8, переключателе поиска и отказах цели —
   `catalog_positions.unit_id` каталожной позиции строки (у строки КП это
   нынешнее `KpLineRow.unit_id`; у строк договора замер §1.2 — без
   каталожной позиции 0). Подпись единицы — `units_of_measure.symbol`
   строки (как `load_analog_details`). Единицы вида связи — по `symbol`:
   `thickness` (м², м³), `width` (м, м²), `density` (м³, т); первая — меньшая.
3. **Ядро выбросов одно:** `analog_ladder._outlier_ratios` переписывается
   поверх новой `outlier_ratios_of(prices)`; блоки А, Г и пометки поиска
   зовут её же. Поведение лестницы не меняется (её тесты — без правок).
4. **Пометки — одной функцией** `reference_marks(ladder, zones, links)`
   (Task 5): строка → отношение | `None` (в наборе, не выброс); строки вне
   наборов в словаре нет. Её зовут панель, поиск и запись цели.
5. **Поиск без сцены (§2.20 укладывается без переделки `load_kp_lines`):**
   `load_kp_lines` получает фильтр `only_position_item_id` (одна строка, те
   же запросы на одну строку), `load_work_history` — фильтр `family_ids`
   (семья строки КП и связанные с ней). `load_history` — как есть (0,3–0,4
   с). Если замер на стенде (Task 11) превысит 3 с — сказать до пуша, а не
   чинить молча.
6. **Сцена для записи цели:** в `crud/our_price.py` заводится публичная
   `line_with_scene(...)` → `(scene, participant, row, result)`;
   `line_for_rung_check` становится её обёрткой (поведение прежнее).
7. **Снимок справочной цели** живёт рядом с выбором:
   `EstimateDecisions.references: dict[pid, ReferenceSnapshot]`; нынешний
   `SavedTarget = tuple[kind, analog_id]` не меняется — справочная цель
   идёт по нему как `("reference", analog_id)`, снимок берётся из
   `references`. Так `kp_file` и `our_price` правятся точечно.
8. **Запись справочной цели** — отдельная `put_reference_target` в
   `crud/kp_decisions.py`; маршрут `PUT …/target` выбирает её по
   `kind = "reference"`. `TargetPut` — дискриминированное объединение по
   `kind` (прежние три вида — прежняя форма до поля).
9. **Подсказки карточки цели** (`link_now`, `link_gone`, `own_analogs`)
   считаются и в разборе, и в панели: значения параметров связей грузятся
   одним запросом только для строк договора справочных целей сметы.
10. **Предзаполнение параметров** в диалоге «Запомнить для семей» — на фронте
    по подстроке имени («толщ», «шир», «плотн») из схем семей, которые панель
    отдаёт в `reference_links`/строке поиска (`link_candidates`), без нового
    маршрута.

## Задачи

### Task 1: миграция `0027` и модели — **ревью**

**Files**
- Create: `backend/alembic/versions/2026_11_15_0027-reference_analogs.py`
- Edit: `backend/models.py`, `backend/tests/conftest.py` (`_DOMAIN_TABLES`)
- Test: `backend/tests/integration/test_ref_analogs_schema.py`

**Interfaces**
- Потребляет: `KpLineTarget`, `KP_TARGET_KINDS`, `CK_KP_TARGET_KIND`, `CK_KP_TARGET_ANALOG`, `WorkFamily`, `UnitOfMeasure`, `FamilyParameter` (существуют).
- Производит:

```python
LINK_KINDS = ("thickness", "width", "density", "constant")
REF_ORIGINS = ("zone", "link", "search")
REF_METHODS = ("link", "manual")
KP_TARGET_KINDS = ("freshest", "analog", "contractor", "reference")
class FamilyLink(Base):   # "family_links": id, family_lo_id, family_hi_id, unit_lo_id, unit_hi_id, kind,
                          #   param_lo_id, param_hi_id, factor_from_id, factor, basis,
                          #   created_by, created_at, updated_by, updated_at
# KpLineTarget: ref_origin, ref_method, ref_factor (Numeric), ref_basis, ref_link_id, ref_without_analogs
```

**Утверждения**
- `alembic upgrade head` создаёт всё из §2.17 с именами `pk_/fk_/uq_/ck_…`;
  `just db-test-check` без дрейфа;
- CHECK по одному входу на каждый: `family_lo_id = family_hi_id` и `lo > hi`;
  вторая связь той же пары; `unit_lo_id = unit_hi_id`; `kind = 'length'`;
  `constant` без `factor`, с `factor = 0`, с пустым `basis`, с
  `factor_from_id` третьей семьи, с параметром; вид с параметром без обоих
  параметров, с `factor`; цель `reference` без аналога, без `ref_origin`,
  без `ref_without_analogs`; цель `analog` с `ref_origin`; `ref_method`
  без `ref_factor`; `ref_factor = 0`; `ref_link_id` при `ref_method =
  'manual'`; `ref_origin = 'catalog'`;
- удаление связи ставит `ref_link_id = NULL`, цель остаётся;
- паритет литералов CHECK модели и миграции — `TestParityWithMigration`;
- downgrade отказывает при связи и при справочной цели — с сообщением, ничего
  не удалив; на пустых — снимает всё новое, CHECK вида цели прежний.

**Имена**
- Заводятся: `FamilyLink`, `family_links`, `LINK_KINDS`, `REF_ORIGINS`, `REF_METHODS`, колонки `ref_*`, ревизия `0027`.
- Существуют: `KP_TARGET_KINDS`, `_DOMAIN_TABLES`, `TestParityWithMigration` (образец — `test_analog_schema.py`).

**Проверка**
- `just test-int-local-k ref_analogs` — ДО 0, ПОСЛЕ ≥ 22.
- `just db-test-check`; `just test-int-local-k "analog_schema or simplify_schema"` — зелёные.

### Task 2: арифметика пересчёта — **ревью**

**Files**
- Create: `backend/services/reference_math.py`
- Edit: `backend/services/analog_ladder.py` (`_outlier_ratios` → поверх `outlier_ratios_of`)
- Test: `backend/tests/unit/test_ref_analogs_math.py`

**Interfaces**
- Потребляет: `parse_single_number`, `price_basis._CONTEXT`, `price_basis.outside`, `LINK_KINDS` (существуют / Task 1).
- Производит:

```python
LinkKind = Literal["thickness", "width", "density", "constant"]
UNIT_PAIRS: Mapping[LinkKind, tuple[str, str]]   # (меньшая, большая) по symbol; constant — нет

@dataclass(frozen=True)
class LinkNumber:
    x: Decimal          # в метрах | т/м³
    basis: str          # «толщина 100 мм»

@dataclass(frozen=True)
class Conversion:
    method: Literal["link", "manual"]
    factor: Decimal     # точный N
    basis: str
    link_id: int | None

def link_number(kind: LinkKind, value_text: str | None, parameter_name: str, *,
                source: str | None, composite: bool) -> LinkNumber | None
def link_factor(kind: LinkKind, x: Decimal, *, contract_unit_is_smaller: bool) -> Decimal
def constant_factor(factor: Decimal, *, from_is_contract_family: bool) -> Decimal
def price_in_kp_unit(price: Decimal, factor: Decimal) -> Decimal
def format_factor(n: Decimal) -> tuple[str, bool]          # («0,6667», приближённо)
def parse_manual_factor(text: str) -> Decimal | None       # §2.13: > 0, ≤ 6 знаков, ≤ 10⁶
def outlier_ratios_of(prices: Sequence[tuple[int, Decimal]]) -> dict[int, Decimal | None]

@dataclass(frozen=True)
class FoldKey: ...   # договор, наименование, unit_id, цена, factor, basis, зона, пометка
def fold_rows(rows: Sequence[RowT], key: Callable[[RowT], Hashable],
              pid: Callable[[RowT], int]) -> list[tuple[RowT, tuple[int, ...]]]
```

**Утверждения**
- `link_number`: «100 мм» → `0,1`, «толщина 100 мм»; «100» при имени «Толщина,
  мм» → `0,1`; «100,5» при «Толщина, мм» → `0,1005`, основание «толщина
  100,5 мм»; «12 см» → `0,12`; «1,5 т/м³» и «1,5 т/м3» → `1,5`; «1500 кг/м³»
  → `1,5`; «2,4 г/см³» → `2,4`; не числа: «100-200 мм», «до 100 мм»,
  «переменная», «D400», «Ø530х8 мм», «0 мм», «100» без единицы ни в тексте,
  ни в имени, «150» при «Поверхностная плотность, г/м2», единица не своего
  вида («100 мм» у `density`), `source = 'path_conflict'`, `off_list`,
  составное, `None`;
- `link_factor`: м² → м³ при `x = 0,1` → `0,1`; м³ → м² → `10`; `1 /
  0,15` — в точности `_CONTEXT`, не округлён;
- `constant_factor`: `factor_from` = семья договора → `factor`; иначе `1 /
  factor`;
- `price_in_kp_unit(Decimal("1107.24"), Decimal("0.1")) == Decimal("11072.40")`
  (К2); цена по `1/0,15` не равна цене по `format_factor`-округлённому;
- `format_factor`: `0,1` → («0,1», False); `1/1,5` → («0,6667», True); `10` →
  («10», False);
- `parse_manual_factor`: «0,1» и «0.1» → `0,1`; отказ: «0», «-1»,
  «0,1234567», «1000001», «abc», «NaN», «Infinity»;
- `outlier_ratios_of` даёт те же отношения, что прежний `_outlier_ratios`, на
  входах лестницы; один элемент — не выброс;
- `fold_rows`: две строки одного договора с равным ключом → одна с `ids` по
  возрастанию, представитель — меньший `pid`; разная цена, разная зона,
  разная пометка (есть/нет и другое отношение), другой договор — не
  сворачиваются.

**Имена**
- Заводятся: всё в блоке «Производит».
- Существуют: `parse_single_number`, `_outlier_ratios`, `outside`, `_CONTEXT`.

**Проверка**
- `just test-unit-k ref_analogs` — ДО 0, ПОСЛЕ ≥ 35.
- `just test-unit-k "analog or ladder"` и `just test-int-local-k analog` — зелёные без правок ожиданий.

### Task 3: связи семей — сервис и маршруты — **ревью**

**Files**
- Create: `backend/services/family_links.py`
- Edit: `backend/routers/semantic.py`
- Test: `backend/tests/integration/test_ref_analogs_links.py`

**Interfaces**
- Потребляет: `FamilyLink` (Task 1); `LinkKind`, `UNIT_PAIRS` (Task 2); `WorkFamily`, `FamilyParameter`, `FamilyParameterSchema`, `require_admin`, `get_current_user`, `DomainError` (существуют).
- Производит:

```python
LinkInactiveReason = Literal["family_inactive", "units_changed", "no_live_side"]

@dataclass(frozen=True)
class LinkState:
    link: FamilyLink
    active: bool
    inactive_reason: LinkInactiveReason | None
    live_lo: bool          # параметр стороны активен и на действующей версии
    live_hi: bool

def links_of_families(db: Session, family_ids: Collection[int]) -> list[LinkState]
def link_view(state: LinkState, *, from_family_id: int) -> dict      # форма GET §2.16
def create_link(db: Session, *, body: LinkInput, actor_id: int) -> int
def update_link(db: Session, link_id: int, *, body: LinkInput, actor_id: int) -> None
def delete_link(db: Session, link_id: int) -> None

class LinkInput(BaseModel):   # family_a_id, family_b_id, kind, param_a_id, param_b_id,
                              # factor_from_family_id, factor: str | None, basis: str | None
# маршруты: GET /families/{family_id}/links; POST /family-links; PUT/DELETE /family-links/{id}
```

**Утверждения**
- создание с `a > b` хранит пару по возрастанию, параметры — со своими
  семьями; `factor` и `factor_from_id` — как пришли (К8);
- создать `constant 1 м² = 0,1 м³` с карточки `a`; `GET` с карточки `b`
  отдаёт хранимые `factor = 0.1`, `factor_from_family_id = a` и `shown =
  {from: b, factor: «10»}`; `PUT` хранимыми полями с карточки `a` и с
  карточки `b` (в любом порядке `a`, `b`) — `factor`, `factor_from_id`
  прежние до знака (К8);
- отказы `422 link_invalid` с `reason`: `same_family`; `family_inactive`;
  `units_mismatch` (thickness на м² и м; constant на равных единицах; семья
  без единицы); `param_invalid` (чужой семьи, архивный, не действующей
  версии, `range`); `params_missing`; `constant_invalid` (`factor` ≤ 0, пусто
  `basis`, `basis` > 300, `factor_from` не из пары, параметр у `constant`);
  вторая связь пары — `409 link_exists`; `PUT` с другой парой — `422`,
  несуществующей — `404`;
- состояние: семья `merged`/`archived` → `family_inactive`; единица семьи
  сменена `set_unit` т → кг → `units_changed` (К9, часть Task 3: только
  состояние связи; блок Г и снимок — Task 7), `PUT` без изменений
  возвращает `active` и перезапоминает единицы; схема стороны пересобрана →
  сторона не живая, у вида с параметром обе мёртвые → `no_live_side`;
- `DELETE` → `204`, у цели со ссылкой `ref_link_id = NULL`;
- `member`: `GET` — `200`, `POST`/`PUT`/`DELETE` — `403` (К7); `GET` не пишет
  (счётчик транзакции, как в 4а).

**Имена**
- Заводятся: всё в блоке «Производит», коды `link_invalid`, `link_exists`.
- Существуют: `set_unit`, `merge_families`, `archive_family`, `require_admin`.

**Проверка**
- `just test-int-local-k ref_analogs` — ПОСЛЕ ≥ +24 к Task 1; `uv run --locked pytest tests/test_auth_coverage.py` — зелёная.

### Task 4: справочные строки и значения параметров связей

**Files**
- Create: `backend/crud/reference_lines.py`
- Test: `backend/tests/integration/test_ref_analogs_lines.py`

**Interfaces**
- Потребляет: `HistoryContract`, `latest_estimates`, `_price_ok`, `_weight_ok`, `_WEIGHT`, `gross_to_net`, `adjust_amount`, `MIN_NOMINAL`, `chapter_steps` (существуют).
- Производит:

```python
@dataclass(frozen=True)
class ReferenceLine:
    position_item_id: int
    contract_id: int
    estimate_id: int
    title: str
    unit_id: int | None          # catalog_positions.unit_id (решение 2)
    unit_symbol: str | None
    family_id: int | None
    context_id: int | None
    zone: Zone | None
    chain_titles: frozenset[str]
    chapter_item_id: int | None
    nominal: Decimal             # нетто, без ИПЦ
    net: Decimal                 # с ИПЦ, если ряд выбран

def reference_lines_query(contracts: Sequence[HistoryContract]) -> sa.Select   # §2.3 пп. 1–5 в SQL
def load_reference_lines(db: Session, contracts: Sequence[HistoryContract], ids: Collection[int], *,
                         factors: Mapping[int, Decimal] | None) -> dict[int, ReferenceLine]
def load_link_values(db: Session, pairs: Collection[tuple[int, int]]
                     ) -> dict[tuple[int, int], ParamText]   # (position_item_id, parameter_id) -> значение

@dataclass(frozen=True)
class ParamText:
    text: str | None
    parameter_name: str
    source: str | None
    off_list: bool
    composite: bool
```

**Утверждения**
- строка справочная по §2.3: не глава; без контекста — годится; с контекстом
  `SYSTEM` — нет (если **любой** её контекст `SYSTEM`); цена `NaN`, `0`,
  отрицательная — нет; вес `0` — нет; база НДС сметы неизвестна — нет;
  номинал `0,99 ₽` — нет, `1,00 ₽` — да; строка без зоны и без варианта —
  да, `zone = None`, `family_id = None`;
- строка прошлой (не последней) сметы договора — нет; строка договора объекта
  тендера (не в `contracts`) — нет; строка сметы КП — нет;
- `net` с рядом — `adjust_amount(nominal, множитель)`; без ряда — `nominal`;
- `load_link_values` отдаёт текст значения и источник для главного и
  второстепенного параметра; `path_conflict`, `off_list`, составное
  помечены; строки без значения в ответе нет;
- `load_reference_lines` и `load_link_values` — по одному запросу на вызов
  (счётчик запросов).

**Имена**
- Заводятся: всё в блоке «Производит».
- Существуют: `latest_estimates`, `load_history`, `context_parameter_values`, `catalog_positions`.

**Проверка**
- `just test-int-local-k ref_analogs` — ПОСЛЕ ≥ +14 к Task 3.

### Task 5: блоки А и Г, пометки, `other_zones` в разборе и панели

**Files**
- Create: `backend/services/reference_blocks.py`
- Edit: `backend/crud/our_price.py` (`_line_view`/`_line_with_decisions` — `other_zones`; `build_line_view` — `reference_zones`, `reference_links`)
- Test: `backend/tests/unit/test_ref_analogs_blocks.py`, `backend/tests/integration/test_ref_analogs_panel.py`

**Interfaces**
- Потребляет: `ladder_for`, `WorkHistory`, `KpWork`, `WorkLadder` (существуют); Task 2 (всё); `LinkState`, `links_of_families` (Task 3); `ReferenceLine`, `ParamText`, `load_link_values`, `load_analog_details`, `chapter_steps` (Task 4, существуют).
- Производит:

```python
@dataclass(frozen=True)
class RefRow:
    position_item_id: int
    contract_id: int
    zone: Zone | None
    unit_id: int | None
    nominal: Decimal
    net: Decimal
    conversion: Conversion | None        # у method = link — с link_id (спека §2.16 ред. 3)
    no_conversion_reason: str | None
    kp_unit_net: Decimal | None
    outlier_ratio: Decimal | None
    param_value: str | None

@dataclass(frozen=True)
class ZoneGroup:  zone: Zone; rung_no: RungNo; released: tuple[str, ...]; other_class: bool; rows: tuple[RefRow, ...]
@dataclass(frozen=True)
class LinkGroup:  link_id: int; family_id: int; kind: LinkKind; other_class: bool; rows: tuple[RefRow, ...]

def zone_groups(kp: KpWork, history: WorkHistory, *, tender_class_id: int, tender_contractor_id: int,
                target_month: YearMonth | None) -> tuple[ZoneGroup, ...]
def other_zones(groups: Sequence[ZoneGroup]) -> list[dict]          # {zone, lines, contracts} до свёртки
def conversion_for(*, contract_unit_id: int | None, kp_unit_id: int | None, contract_family_id: int | None,
                   kp_family_id: int | None, links: Sequence[LinkState], units: Mapping[int, str],
                   values: Mapping[tuple[int, int], ParamText], position_item_id: int
                   ) -> tuple[Conversion | None, str | None, bool]   # (N, причина «без пересчёта», нужен ли пересчёт)
def link_groups(kp: KpWork, kp_unit_id: int | None, history: WorkHistory, links: Sequence[LinkState],
                units: Mapping[int, str], values: Mapping[tuple[int, int], ParamText], *,
                tender_class_id: int, target_month: YearMonth | None) -> tuple[LinkGroup, ...]
def reference_marks(ladder: WorkLadder | None, zones: Sequence[ZoneGroup],
                    links: Sequence[LinkGroup]) -> dict[int, Decimal | None]
```

**Утверждения**
- блок А только у `no_history`/`no_zone_match`; у `comparable`,
  `no_variant`, `no_history`/`no_params` — пусто; зоны — в порядке
  `underground, aboveground, territory, whole` без своей; зона без ступени
  группы не даёт; ступень и подпись — как у `ladder_for` на той зоне
  (ступень 2 с отпущенным параметром, ступень 3 «· другой класс»);
- в группе — все аналоги ступени, выбросы с пометкой по **этой** ступени;
  порядок — порядок справки лестницы;
- `other_zones` разбора: `lines` и `contracts` до свёртки; у строки без
  групп поля нет; разбор оффера без справочных данных не делает новых
  запросов (счётчик запросов разбора прежний);
- `conversion_for`: единицы равны → `None`, пересчёт не нужен; связь
  действующая и `x` число → `link` с `N` и основанием; `x` не число →
  причина «значение «переменная» — не число»; нет значения → «у строки нет
  значения «Толщина слоя»»; сторона мёртвая → «параметр устарел»; связи нет
  или единица строки ≠ единице семьи → нужен ручной; `constant` в обе
  стороны;
- блок Г: связь недействующая — группы нет; строки — кандидаты `F'` в зоне
  строки КП, класс тендера, иначе другой класс с `other_class`; пометки по
  ценам в единице КП; строки без пересчёта — в конце, без пометки; при живой
  стороне `F` и числе у строки КП — сначала строки с тем же `x`;
- `reference_marks`: строка своей ступени — отношение лестницы; строка
  группы А или Г — отношение группы; прочие — нет в словаре;
- панель отдаёт `reference_zones` и `reference_links` в форме §2.16 со
  свёрткой и `chapter_paths`; деньги — пары `{net, shown}`; `with_this_contractor`;
  `conversion.factor` — точная запись `N` (у `1 / x` — в точности `_CONTEXT`),
  не печатная форма: её же клиент вернёт в `expected_factor` (§2.13);
  у `conversion.method = link` есть `link_id` связи, давшей `N`;
- сценарий подбетонки (условные данные): строка КП в м³ без своих аналогов,
  связь `thickness` с 98-подобной семьёй в м², значение «100 мм» → строка
  блока Г `1 107,24 ₽/м² → 11 072,40 ₽/м³`, основание «толщина 100 мм».

**Имена**
- Заводятся: всё в блоке «Производит»; поля ответа `other_zones`, `reference_zones`, `reference_links`.
- Существуют: `_line_view`, `_line_with_decisions`, `build_line_view`, `load_analog_details`.

**Проверка**
- `just test-unit-k ref_analogs` — ПОСЛЕ ≥ +22 к Task 2; `just test-int-local-k ref_analogs` — ПОСЛЕ ≥ +10 к Task 4.
- Один раз в конце: `just test-int-local-k "our_price or analog or simplify"` — зелёная.

### Task 6: поиск по договорам — **ревью**

**Files**
- Create: `backend/crud/reference_search.py`
- Edit: `backend/crud/our_price_load.py` (`load_kp_lines(..., only_position_item_id=None)`, `load_work_history(..., family_ids=None)`), `backend/routers/tenders.py`
- Test: `backend/tests/integration/test_ref_analogs_search.py`

**Interfaces**
- Потребляет: `reference_lines_query`, `ReferenceLine` (Task 4); Task 5 (`zone_groups`, `link_groups`, `conversion_for`, `reference_marks`); `fold_rows` (Task 2); `normalize_job_title_with_lemmatization`, `load_history`, `plan_history_inflation`, `round_target_month`, `kp_vat_bases` (существуют).
- Производит:

```python
SEARCH_LIMIT = 50
def parse_query(q: str) -> tuple[str, ...]          # слова; 422 search_query_invalid
def search_reference(db: Session, tender_id: int, round_id: int, offer_id: int, pid: int, *,
                     q: str, units: Literal["all", "kp"], inflation_series_id: int | None) -> dict
# GET …/lines/{pid}/reference-search?q=&units=&inflation_series_id= -> {rows, total_lines, truncated}
```

**Утверждения**
- запрос: « а » и «x» → `422 search_query_invalid`; 101 знак → `422`; семь
  слов → `422`; «_» → `422` (после отбрасывания коротких слов искать
  нечего); «засыпка песком» находит «Засыпка песок» (лемма) и «засыпка
  песком» (текст); экранирование: при строках «a_» и «ab» запрос «a_» находит
  только первую, «100%» при «100%» и «1005» — только первую;
  лемматизатор недоступен (подмена) — ищет по тексту;
- каждое слово обязано найтись (в тексте **или** в леммах);
- состав: строки §2.3 сцены; строка договора объекта тендера не находится
  (К6); строка прошлой сметы договора — не находится; строка без зоны —
  находится с `zone = None`;
- `units = kp` — только `unit_id` строки КП; `all` — все, сначала своя
  единица, затем своя зона, место, цена, `position_item_id`;
- пересчёт строки выдачи: `none` | `link` | `manual_needed` по §2.8; у
  `link` — `link_id`, точный `factor` и `basis`, ровно те, что примет
  `PUT …/target` без `409`; цена в единице КП только при пересчёте или
  равных единицах;
- пометка выброса — та же, что в справке своей ступени и в блоках панели на
  той же строке договора (сравнение ответов поиска и панели);
- 51 строка после свёртки → 50, `truncated = true`, `total_lines = 51`
  (до свёртки — число строк);
- система → `422 reference_not_applicable`; чужой оффер или строка не этого КП
  → `404`;
- поиск не строит сцену: число запросов ограничено константой, не растёт с
  числом строк КП сметы; `load_kp_lines(only_position_item_id=…)` отдаёт ту
  же `KpLineRow`, что полный вызов.

**Имена**
- Заводятся: всё в блоке «Производит»; коды `search_query_invalid`, `reference_not_applicable`; параметры `only_position_item_id`, `family_ids`.
- Существуют: `load_kp_lines`, `load_work_history`, `_LINE` (префикс маршрутов строки в `routers/tenders.py`).

**Проверка**
- `just test-int-local-k ref_analogs` — ПОСЛЕ ≥ +18 к Task 5; `tests/test_auth_coverage.py` — зелёная.
- Один раз в конце: `just test-int-local-k "our_price or analog"` — зелёная.

### Task 7: справочная цель — запись, чтение, `choice` — **ревью**

**Files**
- Edit: `backend/crud/kp_decisions.py`, `backend/services/our_price.py`, `backend/crud/our_price.py`, `backend/routers/tenders.py`
- Test: `backend/tests/integration/test_ref_analogs_target.py`

**Interfaces**
- Потребляет: Task 1–6.
- Производит:

```python
TargetKind = Literal["freshest", "analog", "contractor", "reference"]
TargetResetReason = Literal[..., "reference_unavailable"]

@dataclass(frozen=True)
class ReferenceSnapshot:
    origin: Literal["zone", "link", "search"]
    method: Literal["link", "manual"] | None
    factor: Decimal | None
    basis: str | None
    link_id: int | None
    without_analogs: bool

# EstimateDecisions.references: dict[int, ReferenceSnapshot]
def resolve_target(saved: SavedTarget | None, result: LineResult, *, is_system: bool,
                   reference_available: bool = False) -> TargetChoice | None
def reference_value(line: ReferenceLine, snapshot: ReferenceSnapshot) -> Decimal      # net ÷ factor
def line_with_scene(db: Session, tender_id: int, round_id: int, offer_id: int, pid: int, *,
                    inflation_series_id: int | None) -> tuple[_Scene, _Participant, KpLineRow, LineResult]
def put_reference_target(db: Session, tender_id: int, round_id: int, offer_id: int, pid: int, *,
                         body: ReferenceTargetInput, inflation_series_id: int | None, actor_id: int) -> None
class ReferenceTargetInput(BaseModel):  # analog_id, origin, conversion, confirm_outlier (§2.13)
```

**Утверждения**
- отказы — по порядку таблицы §2.13, каждый одним отличием входа:
  система → `target_not_applicable`; строка не справочная (прошлая смета,
  объект тендера, `SYSTEM`) → `reference_unavailable`; равные единицы с
  `conversion` → `conversion_not_needed`; разные (и одна пустая) без
  `conversion` → `conversion_required`; `link` недействующей связи, связи
  чужих семей, без числа у строки → `link_not_applicable`; значение
  параметра 100 → 200 мм после чтения панели с прежним `expected_factor` →
  `409 conversion_stale` с текущими `factor` и `basis`, записи нет (К10);
  `manual` «0», «0,1234567», пустое основание, 301 знак →
  `manual_conversion_invalid`; помеченная строка без `confirm_outlier` →
  `409 outlier_confirmation_required` с `ratio` — на строке блока А **и** на
  строке из поиска (К3); с `confirm_outlier` — сохраняется;
- строка **поиска** с пересчётом `link`: тело `PUT` собрано из её
  `conversion` (`link_id`, `factor` → `expected_factor`, `basis` →
  `expected_basis`) без изменений → цель записана, `ref_link_id` и
  `ref_factor` равны отданным поиском;
- справочная цель принимается у `comparable`, `no_history`, `no_variant`,
  `no_zone`, `unit_mismatch`, `no_price`;
- снимок: `ref_method`, `ref_factor` (точный `N`), `ref_basis`,
  `ref_link_id`, `ref_origin`; `ref_without_analogs` — истина у строки без
  ступени и без лестницы, ложь при своей ступени; перезапись цели другим
  видом обнуляет `ref_*`;
- чтение: значение = цена строки договора с рядом чтения ÷ `ref_factor`
  (без пересчёта — ÷ 1); правка связи (`factor` 0,1 → 0,2) и удаление связи
  цифру не меняют (К4), подсказки `link_now` (с `N′`) и `link_gone`;
  появилась своя ступень → `own_analogs`;
- сброс: строка стала системой → `kind_changed`; смета договора заменена
  допсоглашением → `reference_unavailable` (К5); состояние строки сброса не
  даёт; при сбросе цель — как без выбора;
- `target_kind = "reference"` в любом состоянии строки, «Договорились»
  сильнее; `choice` панели и разбора — форма §2.16 с `reference` (строка
  договора в форме `rows`), и тогда, когда строки нет в блоках;
- итоги оффера, итоги строки, `to_lowest`, `to_freshest`, вилка, корзины и
  плитки равны со справочной целью и без (К1);
- `member`: `PUT` справочной цели и `DELETE` цели → `403` (К7); `admin` —
  обе операции проходят; анонимный — `401`; поиск и панель `member` читает;
- сквозной К9: цель по связи сохранена, затем единица семьи `F'` т → кг —
  блока Г в панели нет, связь `units_changed`, значение цели и `ref_factor`
  прежние, подсказка `link_gone`;
- прежние виды цели: тела и ответы `PUT …/target` прежние до поля
  (`test_analog_targets.py`, `test_simplify_target.py` — без правок).

**Имена**
- Заводятся: всё в блоке «Производит»; коды `reference_unavailable`, `conversion_not_needed`, `conversion_required`, `link_not_applicable`, `conversion_stale`, `manual_conversion_invalid`, `outlier_confirmation_required`.
- Существуют: `put_target`, `load_decisions`, `resolve_target`, `target_analog`, `_decision_fields`, `line_for_rung_check`, `TargetPut`.

**Проверка**
- `just test-int-local-k ref_analogs` — ПОСЛЕ ≥ +30 к Task 6.
- Один раз в конце: `just test-int-local-k "analog or simplify or our_price or kp_file"` — зелёная.

### Task 8: файл подрядчику и комментарий — **ревью**

**Files**
- Edit: `backend/services/kp_comment.py`, `backend/services/kp_file.py`, `backend/crud/our_price.py` (`build_file_inputs`, `_template_text`, `_file_ours`)
- Test: `backend/tests/unit/test_ref_analogs_comment.py`, `backend/tests/integration/test_ref_analogs_file.py`

**Interfaces**
- Потребляет: `ReferenceLine`, `ReferenceSnapshot`, `reference_value` (Task 4, 7); `format_factor` (Task 2); `CommentInputs`, `target_net`, `comment_case`, `template_text`, `_source_text`, `inflation_years_for` (существуют).
- Производит:

```python
@dataclass(frozen=True)
class ReferenceSource:
    contract: HistoryContract
    chapter_path: tuple[str, ...]
    unit_text: str            # единица строки договора
    nominal: Decimal          # нетто, без ИПЦ
    net: Decimal              # с ИПЦ
    snapshot: ReferenceSnapshot

# CommentInputs.reference: ReferenceSource | None = None
# FileInputs.references: dict[int, ReferenceSource]
```

**Утверждения**
- цель строки: «Договорились» → справочная (действующая) → прежний выбор →
  самая низкая; `target_net` справочной — `reference_value`;
- «Наша расценка за ед.» — `net_to_gross(цель, база КП)`, отклонение — к
  цели, у любой строки со справочной целью (и у `no_history`);
- «Источник» без пересчёта: «{объект}, договор № {номер}, {год} г.:
  сопоставимая работа ({путь}), {цитата} ₽ за {ед} {НДС}»; с пересчётом —
  ещё «; 1 м² = 0,1 м³ (толщина 100 мм): {цитата ÷ N} ₽ за м³ {НДС}»;
  обращённый `N` — с «≈»; строка вне разделов — без скобок; цитата — в
  базе НДС КП;
- столбец ИПЦ — при выбранном ряде и множителе сметы строки договора ≠ 1;
  годы блока ИПЦ включают год договора справочной цели;
- комментарий: свой — как есть; шаблон — только `over`, текст ровно как пример
  §2.15 на тех же числах (база 20 %); `below` и равная цена — без шаблона;
  случаи `kp_unit`, `history_unit` к справочной цели не применяются;
- «Договорились» поверх справочной цели: источник «Договорились …», шаблон
  справочной цели не строится;
- справочная цель сброшена (`reference_unavailable`) — файл как без выбора;
- суммы файла (итоги, шапка) — прежние до знака со справочной целью и без.

**Имена**
- Заводятся: `ReferenceSource`, `CommentInputs.reference`, `FileInputs.references`.
- Существуют: `line_comment_inputs`, `_file_line`, `build_file_rows`, `deviation_pct`, `inflation_years_for`, `build_file_inputs`.

**Проверка**
- `just test-unit-k ref_analogs` — ПОСЛЕ ≥ +10 к Task 5; `just test-int-local-k ref_analogs` — ПОСЛЕ ≥ +8 к Task 7.
- Один раз в конце: `just test-int-local-k kp_file` и `just test-unit-k "kp_comment or kp_file"` — зелёные.

### Task 9: фронт панели — подпись зон, блоки, поиск, диалог цели

**Files**
- Create: `frontend/src/pages/tenders/our-price/ReferenceBlocks.tsx`, `ReferenceRows.tsx`, `ReferenceSearch.tsx`, `ReferenceTargetDialog.tsx` (+ `*.test.tsx` рядом)
- Edit: `frontend/src/types/domain.ts`, `services/api/domain.ts`, `services/queryKeys.ts`, `services/queries.ts`, `test/handlers.ts`, `test/fixtures.ts`, `pages/tenders/our-price/OfferLinesTable.tsx`, `LineSheet.tsx`, `ourPriceCopy.ts`

**Interfaces**
- Потребляет: ответы Task 5–7 (§2.16); `money.ts`, `RecalcOverlay`, `noZoneMatchLabel` (существуют).
- Производит:

```ts
type RefConversion =
  | { method: "link"; link_id: number; factor: string; basis: string }
  | { method: "manual"; factor: string; basis: string }
  | null
interface RefRow { analog_id: number; ids: number[]; title: string; unit: string | null; zone: Zone | null;
  contract: RefContract; period: string | null; chapter_paths: string[][]; price: MoneyPair; nominal: MoneyPair;
  conversion: RefConversion; no_conversion_reason: string | null; kp_unit_price: MoneyPair | null;
  deviation_pct: string | null; outlier_ratio: string | null; with_this_contractor: boolean;
  param_value: string | null }
interface ReferenceZoneGroup { zone: Zone; rung_no: number; released: string[]; other_class: boolean; rows: RefRow[] }
interface ReferenceLinkGroup { link_id: number; family: { id: number; title: string; unit: string | null };
  kind: LinkKind; other_class: boolean; rows: RefRow[] }
function otherZonesLabel(zone: Zone | null, groups: OtherZone[]): string   // §2.5, склонения
function useReferenceSearch(...): UseQueryResult<ReferenceSearchResult>
function usePutReferenceTarget(...): UseMutationResult<void, ApiError, ReferenceTargetBody>
```

**Утверждения**
- подпись строки: «нет аналога в зоне «Надземная» · есть в зоне «Подземная»:
  3 строки, 2 договора, «Территория»: 1 строка, 1 договор» — склонения 1, 2,
  5, 21; без `other_zones` — прежняя подпись;
- блоки А и Г: первые пять строк, «ещё N» раскрывает остальные; свёрнутая
  строка «×k» с «в k разделах: …»; пометка «×{ratio} к остальным»;
  заголовок блока Г по виду связи;
- поиск: короче двух знаков — запроса нет; переключатель «все единицы / как у
  строки КП»; подсказка «ищите по корням слов»; `truncated` — «показаны первые
  50, уточните запрос»; `422 reference_not_applicable` — поля нет у системы;
- «сделать целью» и диалог — только у `admin`; `member` видит блоки и поиск
  без кнопок выбора;
- «сделать целью» у помеченной строки — сначала подтверждение, затем
  `confirm_outlier = true`;
- сквозной: строка **поиска** (не блока Г) с пересчётом `link` → диалог →
  `PUT` с `conversion = {method: "link", link_id, expected_factor, expected_basis}`
  — значения ровно из `conversion` строки, `factor` строкой без
  преобразования;
- диалог пересчёта открывается при разных единицах: `link` — только
  просмотр `N` и основания; иначе ручной ввод; без основания кнопка
  неактивна; `409 conversion_stale` подменяет `N`, основание и цену из ответа
  и ждёт нового нажатия; флажок «Запомнить для семей» (решение 10) шлёт
  `POST /family-links`, затем цель с `method = link`;
- деньги на фронте не считаются: цены и `N` — строки с сервера;
- лоадер записи цели — на сером слое.

**Имена**
- Заводятся: всё в блоке «Производит», четыре компонента.
- Существуют: `LineSheet`, `OfferLinesTable`, `noZoneMatchLabel`, `RecalcOverlay`, `MoneyPair`.

**Проверка**
- Из `C:\…\frontend` (PowerShell `Set-Location` с заглавной буквой диска): `npx vitest run src/pages/tenders/our-price` — зелёная, ≥ 30 новых тестов; `npx tsc -b --noEmit`, `npm run lint` — без ошибок.
- Снимок стенда: панель строки 223529 (блок А, блок Г, поиск «подготовка»,
  диалог) рядом с макетом; вид чисел сверить глазами.

### Task 10: фронт карточки цели и раздела «Связи»

**Files**
- Create: `frontend/src/pages/families/FamilyLinksBlock.tsx`, `FamilyLinkDialog.tsx` (+ `*.test.tsx`)
- Edit: `frontend/src/pages/tenders/our-price/TargetCards.tsx`, `ExportBlock.tsx`, `pages/families/FamiliesTab.tsx`, `types/domain.ts`, `services/api/domain.ts`, `services/queries.ts`, `test/handlers.ts`, `test/fixtures.ts`

**Interfaces**
- Потребляет: `choice` Task 7, маршруты связей Task 3.
- Производит:

```ts
interface FamilyLinkView { id: number; other_family: FamilyRef; kind: LinkKind; this_param: ParamRef | null;
  other_param: ParamRef | null; factor_from_family_id: number | null; factor: string | null; basis: string | null;
  shown: { from_family_id: number; factor: string } | null; state: "active" | "inactive";
  inactive_reason: LinkInactiveReason | null; stale_sides: ("this" | "other")[];
  created_by: string; created_at: string; updated_by: string | null; updated_at: string | null }
function useFamilyLinks(familyId: number): UseQueryResult<FamilyLinkView[]>
```

**Утверждения**
- карточка цели: «справочная · другая зона | из поиска | по связи»; при
  пересчёте «пересчёт из м²», основание, исходная и пересчитанная цена;
  подсказки `link_now`, `link_gone`, `own_analogs` текстами §2.14; «вернуть
  самую низкую» — прежний `DELETE`, только у `admin`; сброшенная — причина
  сброса;
- `ExportBlock` показывает источник справочной цели;
- «Связи» на карточке семьи: список (показ — `shown`), «не действует: единица
  семьи изменилась — подтвердите связь заново», «параметр устарел — укажите
  заново»; создание, правка, удаление — только `admin` (у `member` кнопок
  нет);
- форма правки заполняется **хранимыми** `factor` и
  `factor_from_family_id`, не `shown`: сохранение без изменений с карточки
  любой из двух семей шлёт те же `factor` и `factor_from_family_id`;
- отказы `link_invalid` — текст по `reason`; `link_exists` — «связь этой
  пары уже есть».

**Имена**
- Заводятся: два компонента, `useFamilyLinks`, мутации связей.
- Существуют: `TargetCards`, `ExportBlock`, `FamiliesTab`, `SchemaBlock`.

**Проверка**
- `npx vitest run src/pages/families src/pages/tenders/our-price` — зелёная, ≥ 18 новых тестов; `npx tsc -b --noEmit`, `npm run lint` — без ошибок.
- Снимок стенда: карточка цели по связи и раздел «Связи» семьи рядом с макетом.

### Task 11: `AGENTS.md`, справочник, стенд, devlog

**Files**
- Edit: `AGENTS.md` (§3 роли), `docs/AGENTS-revisions.md`, `docs/reference/schema.md`, `docs/reference/screens.md`, `docs/reference/money-axes.md`, `docs/product-roadmap.md`
- Create: `docs/devlog/2026-11-15-reference-analogs.md`

**Interfaces**
- Потребляет: всё выше.
- Производит: —

**Утверждения**
- `AGENTS.md` §3: `admin` — «связи семей»; цель строки — «самая свежая, с
  этим подрядчиком, аналог или справочная»; `member` — поиск и блоки — чтение;
  «что уходит наружу» без правок;
- справочник: `family_links` и колонки `kp_line_targets` (schema), блоки,
  поиск, диалог, «Связи» (screens), цена справочной строки и цель ÷ `N`
  (money-axes);
- стенд `gca_dev`: миграция 0027 вверх, вниз на копии с данными — отказ, на
  пустой — проходит, снова вверх; сценарии дизайна §7 на строке 223529
  (запись связи и цели — через приложение); снимки рядом с макетом;
- замеры §2.20 повторены (панель ≤ +0,5 с к 7,4 с; разбор оффера ≤ +5 %;
  поиск «подготовка» ≤ 3 с) и записаны в devlog; превышение — к
  пользователю до пуша;
- devlog называет отступление от `implementation.md` (снятия делал
  оркестратор) и итог К1–К10.

**Имена**
- Существуют: `check_agents_index.py`.

**Проверка**
- `cd backend && uv run --locked python scripts/check_agents_index.py` — 18 из 18.

## Команды проверки

- По задаче: в самой задаче.
- По фиче: GitHub Actions на PR #85 (`gh pr checks 85`) — полный бэкенд и
  фронт; локально `just ci` не гоняется (решение пользователя 11.10.2026).
- Стенд: Task 11.
