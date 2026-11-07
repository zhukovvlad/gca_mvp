# План: лестница аналогов

**Спека:** `docs/superpowers/specs/2026-11-07-analog-ladder-design.md` (гейт 2 одобрен 07.11.2026, три круга Codex, редакция 3, `5c8b358`)
**Дизайн:** `docs/superpowers/design/2026-11-01-analog-ladder.md` (редакция 3), макет — `.../2026-11-01-analog-ladder/mockup.html`
**Стоит на:** спека 4а `2026-10-23-our-price-design.md`, спека 4б `2026-10-27-contractor-file-design.md`, обе в `main` (`04b5d3e`)
**Ветка:** `feat/analog-ladder`, черновой PR #74
**Статус:** редакция 1, 07.11.2026 — на ревью гейта 3.

> Исполнителю: задачи идут по порядку; каждая — цикл TDD
> (`superpowers:test-driven-development`: тесты первыми, красный прогон
> показать). Адреса `§N` без уточнения — разделы спеки; «решение N» —
> пронумерованные решения этого плана. **Защит, дублирующих предикат, не
> класть.** Числа проверок — нижние границы. ДО сняты 07.11.2026 на `5c8b358`
> (`uv run pytest <каталог> --collect-only -q -k <шаблон>`): `test_analog` — 0
> в `tests/unit` и `tests/integration`; `test_our_price` — unit 225,
> integration 429; `test_kp_file` — unit 438, integration 457. **Все новые
> бэкенд-тесты — в файлах `test_analog_*.py`, шаблон `-k test_analog`.**
> Тесты 4а и 4б остаются зелёными; их ожидания правятся **только** там, где
> задача называет изменение (лестница работ, ручная ступень работы, ключи
> итогов).

## Режим реализации (ускоренный, с 28.10.2026)

- **Исполнители** — Sonnet; оркестратор (Opus) принимает задачу, коммитит,
  ведёт `progress.md`.
- **Проверки по риску.** Отдельное ревью (Opus, узкий бриф: свойства задачи
  из списка ниже и «что сделало бы этот тест ложно-зелёным») — только у задач
  с пометкой **«ревью»**: 1, 3, 4, 5, 6. Остальные (2, 7, 8, 9, 10) принимает
  оркестратор: тесты задачи, `ruff`, `tsc`, `eslint`, у фронта — снимок
  стенда рядом с макетом.
- **Снятия защит — только на критических свойствах** (деньги и `Decimal`,
  права и раскрытие наружу, удаление и атомарность, конкурентность), и делает
  их оркестратор лично, в копии дерева (стенд отдаёт живое дерево): по
  каждому — риск, тест, результат в `progress.md`. Список — «Критические
  свойства» ниже; таблиц по всем фрагментам диффа нет.
- **Прогоны.** Исполнитель гоняет только наборы затронутых модулей и их
  вызывающих; полный бэкенд и `just ci` локально не гоняются — проверяет
  GitHub Actions на PR (`gh pr checks 74`), красное чинится следующим
  коммитом. Это отступление от `AGENTS.md` §9.3 называется в devlog.
- **Без остановок** между задачами; стоп — только на пуш, внешнее ревью и
  вопросы, где допущение небезопасно (`AGENTS.md`).
- **Финальное ревью ветки** — Codex (пользователь запускает с удалённой
  машины, комментарий в PR #74).

## Global Constraints

- **Деньги** — `Decimal` до границы ответа, строки в JSON через
  `decimal_json`, ни одного `float` (`AGENTS.md` §3). В JS деньги не
  перемножаются и не делятся.
- **Ось сравнения — нетто**; порядок нетто → приведение → ставка показа
  (`AGENTS.md` §10). Порог 1 ₽ — по **номиналу** нетто (§2.8 п. 4, §2.9).
- **Предикат цены** — `money.price.is_price` / `is_weight` и SQL-копии
  `_price_ok` / `_weight_ok` (`crud/our_price_load.py:258-270`); голое `> 0`
  запрещено.
- **История — без объекта тендера** (§2.3, правило 4а не меняется); строки КП
  аналогами не бывают.
- **Наружу (модели) уходят** только названия цепочек, наименования строк,
  единицы, имена/определения семей и параметров, значения параметров
  (`AGENTS.md` §3). Ни цен, ни объёмов, ни подрядчиков, ни договоров, ни
  объектов; приватность — `find_privacy_matches` у обоих новых видов.
- **Чтение не пишет.** `GET` 4а, панель, предпросмотр файла, очереди каталога
  — ни одной записи.
- **Права:** мутации цели, решения зоны и признака — `require_admin`;
  чтение «Против нашей цены» — `get_current_user`. Сторож
  `tests/test_auth_coverage.py` зелёный после каждой задачи с маршрутами.
- **Решение человека побеждает:** зону с источником `human` и признак,
  поставленный человеком, не трогают ни правило, ни модель, ни команды.
- **Системы считаются как в 4а и 4б** — кроме порога 1 ₽ до сложения, самой
  свежей и подписей (§2.13).
- **Не трогаются:** `v_position_deviation_inputs`, `v_category_totals`, свод
  по этапам, сравнение договоров, матрица, паспорт, нормативы.
- **Фронтенд** — только shadcn/ui; новые примитивы — `npx shadcn add`.
- **Репозиторий публичный:** настоящие объекты, подрядчики, номера договоров,
  цены стенда — ни в тесты, ни в devlog; условные имена («Объект Т»,
  «Подрядчик А»).
- **Стенд `gca_dev`** — запись только командами фичи (DoD 9) и через
  приложение; учётки стенда не трогать; кириллицу в терминал не печатать.
- **Номер новой версии `AGENTS.md`** в плане и спеке не называется (страж).

## Критические свойства (снятия оркестратора)

| № | Свойство | Задача | Снятие → ожидаемо красный тест |
|---|---|---|---|
| К1 | upgrade `0024` отказывает при строках `kp_line_rungs`, ничего не удалив; downgrade отказывает при решениях людей | 1 | убрать проверку → тест «строка в `kp_line_rungs` → `RuntimeError`, таблица цела» |
| К2 | решение человека о зоне не перекрашивается правилом, `--reapply-rule` и ответом модели | 2 | снять условие `zone_source <> 'human'` в записи ответа → тест «ответ модели после решения человека» |
| К3 | ответ `releasable_params` и принятие предложения — только при совпадении повторного рендера (вкл. правку семьи вне `schema_edit`) | 3 | снять повторный рендер в `record_result` / в `PUT …/releasable` → два теста `input_changed` / `proposal_stale` |
| К4 | порог 1 ₽ — до выбора ступени, по номиналу (работы) и до сложения (системы) | 4, 5 | снять фильтр → тест «символический на ступени 1, нормальный на 2 — выбрана 2»; тест системы «0,50 ₽ за ед. не входит» |
| К5 | выбросы — один проход, пустой остаток — `unit_mismatch` без спуска | 4 | заменить на итеративное исключение → тест `1, 1, 100, 100` |
| К6 | `over_freshest = null` при пустом покрытии, а не 0 | 5 | вернуть `Σ` пустого набора → тест «ни у одной сравнимой нет самой свежей» |
| К7 | выбор цели не меняет суммы и плитки | 5, 6 | подставить цель в `to_lowest` → тест «суммы до и после `PUT …/target` равны» |
| К8 | `PUT …/target` проверяется с рядом ИПЦ запроса | 6 | снять ряд из проверки → тест на номиналах `10, 100, 110` |
| К9 | чтение не пишет | 5 | — (тест счётчиков транзакции, как в 4а; снятие не нужно — тест сам ловит запись) |

## Review Focus

Входы, которые спека подразумевает, а задачи легко пропустить; тест на
каждый — в задаче-владельце:

1. **Вариант строки КП на старой версии схемы** (схему пересобрали после
   разноса): ступеней 2/4 нет, ступени 1/3 работают по `work_variant_id`;
   `no_history_reason` считается без отпускаемых (Task 4).
2. **Аналог из договора, чья смета — ДС без `data_prepared_on_date`**: дата
   цены `None`, в самую свежую не входит, в самую низкую — входит
   (Task 4, Task 5).
3. **Цепочка, у которой зону поставил человек, затем импорт новой сметы с той
   же цепочкой** — `ON CONFLICT DO NOTHING`, правило не применяется,
   задание не ставится (Task 2).
4. **Выбор аналога, чья смета договора заменена ДС** (строка ушла): выбор не
   действует с `analog_unavailable`, экран не падает (Task 6).
5. **Семья слита с другой (`merge_families`) при открытом предложении
   «можно отпустить»**: версия источника `superseded` — предложение
   недействующее и убирается; ответ задания — `not_applicable` (Task 3).
6. **Строка КП-работа, ставшая системой**, с выбором цели: `kind_changed`,
   система считается своей ступенью семьи (Task 6).

## Структура файлов

```
backend/
  alembic/versions/2026_11_07_0024-analog_ladder.py   создаётся (Task 1)
  models.py                       правка: SectionChain, ReleasableProposal, KpLineTarget,
                                  PositionItem.section_chain_id, FamilyParameter.releasable,
                                  SemanticJob.section_chain_id, SemanticJobKind + 2; KpLineRung удаляется (Task 1)
  config.py                       правка: SEMANTIC_ZONE_AUTO_ACCEPT_THRESHOLD, SEMANTIC_ZONE_MAX_TOKENS,
                                  SEMANTIC_RELEASABLE_AUTO_ACCEPT_THRESHOLD (Task 2, 3)
  services/zone_rules.py          создаётся: ключ цепочки, правило зоны (Task 2)
  crud/section_chains.py          создаётся: assign_section_chains, заполнение, очередь, решение (Task 2)
  services/zone_request.py        создаётся: рендер и запись ответа section_zone (Task 2)
  services/releasable.py          создаётся: рендер, актуальность, запись ответа, предложения, постановка (Task 3)
  services/semantic_worker.py, services/semantic_cost.py   правка: ветки двух видов (Task 2, 3)
  services/import_pipeline.py     правка: шаг assign_section_chains (Task 2)
  services/schema_edit.py         правка: поле releasable, уборка и постановка (Task 3)
  services/work_variants.py       правка: перенос releasable, уборка при supersede, постановка при заморозке (Task 3)
  services/work_families.py       правка: уборка и постановка после update_family/set_unit/merge (Task 3)
  services/analog_ladder.py       создаётся: чистое ядро лестницы работ (Task 4)
  services/our_price.py           правка: работы через analog_ladder, системы — порог 1 ₽ и самая свежая,
                                  итоги с покрытием; ручная ступень работы уходит (Task 5)
  crud/our_price_load.py          правка: история с id строки, цепочкой, зоной, периодом, значениями (Task 5)
  crud/our_price.py               правка: сцена, ответы строки/панели/итогов (Task 5); цель (Task 6)
  crud/kp_decisions.py            правка: ступень работы уходит (Task 1); цель, счёт targets (Task 6)
  services/kp_comment.py, services/kp_file.py   правка: источник — аналог цели (Task 6)
  routers/tenders.py              правка: …/target, rung_not_applicable (Task 1, 6)
  routers/semantic.py             правка: очереди, решения, поля схемы, /status (Task 2, 3)
  cli.py                          правка: zones-backfill, releasable-enqueue-all (Task 2, 3)
  tests/conftest.py               правка: _DOMAIN_TABLES (Task 1)
  tests/unit/test_analog_*.py, tests/integration/test_analog_*.py   создаются
frontend/src/
  types/domain.ts, services/api/domain.ts, services/api/admin.ts,
  services/queryKeys.ts, services/queries.ts, test/handlers.ts, test/fixtures.ts   правка (Task 7, 8)
  pages/tenders/our-price/
    ourPriceCopy.ts, LineSheet.tsx, OfferLinesTable.tsx, OfferTiles.tsx,
    ParticipantCard.tsx, offerFilter.ts, ExportBlock.tsx, RungSaveBar.tsx   правка (Task 7)
    AnalogsTable.tsx, FigureCards.tsx       создаются (Task 7)
  pages/families/
    SuggestionsTab.tsx, SchemaBlock.tsx, schema-edit/ParameterEditor.tsx   правка (Task 8)
    ZonesQueue.tsx, ReleasableQueue.tsx     создаются (Task 8)
AGENTS.md, docs/AGENTS-revisions.md, docs/reference/{schema,screens,money-axes}.md   правка (Task 9)
docs/devlog/2026-11-07-analog-ladder.md создаётся (Task 10); docs/product-roadmap.md правка (Task 10)
```

## Решения плана, которых нет в спеке

1. **Порядок задач:** схема данных и уход ручной ступени работы (1) → зона
   цепочки целиком (2) → признак «можно отпустить» целиком (3) → чистое ядро
   лестницы (4) → загрузка, сцена, ответы чтения, системы, итоги (5) → цель,
   комментарий, файл, мутации (6) → фронт разбора (7) → фронт каталога (8) →
   ревизия и справочник (9) → стенд, замеры, devlog (10). Каталог (2, 3) идёт
   раньше ядра: ядру нужны зона цепочки и признак в данных фикстур.
2. **`kp_line_rungs` уходит в Task 1 вместе с кодом ручной ступени работы**,
   иначе модель и миграция расходятся на четыре задачи: `KpLineRung`
   удаляется, `load_decisions` отдаёт `saved.work = {}`, `PUT …/rung` у
   строки-работы — 422 `rung_not_applicable`, у системы — как 4б. Тесты 4б
   про ступень строки удаляются или переписываются на отказ (называются в
   задаче). `SavedWorkRung`, `_work_manual` и ветка `saved.work` ядра уходят в
   Task 5 вместе с прежней лестницей работ.
3. **Модули.** Чистые и без `db`: `services/zone_rules.py` (ключ и правило),
   `services/analog_ladder.py` (лестница работ). С `db`: `crud/section_chains.py`
   (цепочки, заполнение, очередь, решение), `services/zone_request.py` и
   `services/releasable.py` (рендер и запись ответа, по образцу
   `services/variant_request.py` и `services/discovery_result.py`). Цель — в
   `crud/kp_decisions.py` рядом с прочими решениями 4б.
4. **Имена ядра 4а остаются внутри:** поля `LineResult.ours`, `to_ours`,
   `bargain`, `GroupResult.bargain`, `OfferTotals.bargain` значат теперь
   «самая низкая» и «КП выше самых низких»; переименование — **только на
   границе ответа** (`_line_view`, `_totals_view`, панель) в `lowest`,
   `to_lowest`, `over_lowest`. Так не переписываются 225 + 429 тестов 4а ради
   имён; докстроки полей называют новый смысл.
5. **Результат работы** — новое поле `LineResult.ladder: WorkLadder | None`
   (у работ после состояний «до лестницы»; у систем `None`) и общее
   `LineResult.freshest: Figure | None`, `LineResult.to_freshest`. У работы
   `rung = None`, `rungs = ()`, `contracts = ()`, `ours = ladder.lowest.value`
   — поэтому суммы `_totals` и комментарий 4б читают `ours` без ветки по виду.
6. **Аналог в ядре** — `HistoryLine` получает `position_item_id`, `chain_id`,
   `zone`, `chain_titles` (множество нормализованных названий), `variant_id`,
   `schema_id`, `main_values` (`Mapping[parameter_id, value_id | None]`, только
   если вариант на действующей версии); `HistoryContract` — `period:
   YearMonth | None` (`_estimate_period`, без ряда ИПЦ). Цена аналога
   считается в ядре: `gross_to_net(unit_price, vat_base)`, с множителем — как
   ставка 4а. Ядро работ получает отдельный список строк истории
   (`work_lines`), индекс систем — прежний `HistoryIndex`.
7. **Сведения семьи для ступеней 2/4** — `FamilyLadderInfo(schema_id,
   main_parameter_ids, releasable_ids, names)` по семье, грузится одним
   запросом по действующим версиям; подпись «без {имена}» — из `names` по
   порядку `ordinal`.
8. **Целевой месяц** (`round_target_month`) передаётся в `evaluate_offer`
   параметром `target_month` всегда, а не только с рядом ИПЦ: самая свежая
   нужна и без ряда.
9. **Итоги:** `OfferTotals` получает `over_freshest: Decimal | None`,
   `comparable_lines`, `freshest_lines`; у групп систем — `GroupResult.freshest`,
   `to_freshest`, `over_freshest` по образцу `bargain` (деньги систем
   считаются по группе один раз).
10. **Цепочки при заполнении** — `chapter_paths` порциями по 500 глав
    (рекурсивный запрос по всему стенду упирался во время, §6); ключ — по
    `zone_rules.chain_key`. Импорт зовёт тот же `assign_section_chains` для
    своих смет.
11. **Вызов модели у `section_zone`** — `SEMANTIC_MODEL` без рассуждения,
    `max_tokens = SEMANTIC_ZONE_MAX_TOKENS`, тарифы `SEMANTIC_PRICE_*`; у
    `releasable_params` — `SEMANTIC_SCHEMA_MODEL`, рассуждение `low`, тарифы
    `SEMANTIC_SCHEMA_PRICE_*`. Ветки `tariffs_from` — оба вида явно (ложный
    тариф по умолчанию недопустим, `ValueError` остаётся).
12. **Транзакции мутаций** (цель, зона, признак) — на роутере, как
    `_write_decision` 4б: `commit` на успехе, `rollback` на отказе.
    Конкурентные правки цели одной строки — «последний пишет» (`ON CONFLICT
    DO UPDATE`), как ступени 4б.
13. **Фронт:** справка — новый `AnalogsTable` (`ui/table`, `ui/collapsible`),
    две карточки цифр — `FigureCards`; очереди каталога — `ZonesQueue`,
    `ReleasableQueue`. Решения зоны и признака инвалидируют префикс
    `["tenders","our-price"]` и ключи очередей и схемы семьи.

## Задачи

### Task 1: миграция `0024`, модели, уход ручной ступени работы — **ревью**

**Files**
- Create: `backend/alembic/versions/2026_11_07_0024-analog_ladder.py`
- Edit: `backend/models.py`, `backend/tests/conftest.py`, `backend/crud/kp_decisions.py`, `backend/routers/tenders.py`
- Test: `backend/tests/integration/test_analog_schema.py`; правка тестов 4б про ступень строки (`test_kp_file_*`)

**Interfaces**
- Потребляет: `PositionItem`, `FamilyParameter`, `SemanticJob`, `User`, `_downgrade_blockers` (0022 как образец), `load_decisions`, `put_rung` (существуют).
- Производит:

```python
class SectionChain(Base):      # "section_chains"
    id: int; chain_key: str (UNIQUE); titles: list[str]
    zone: str | None; zone_source: str | None
    proposed_zone: str | None; proposed_confidence: Decimal | None; proposed_reason: str | None
    decided_by: int | None; decided_at: datetime | None; created_at: datetime

class ReleasableProposal(Base): # "releasable_proposals": parameter_id PK, releasable, confidence, reason, request_hash, created_at
class KpLineTarget(Base):      # "kp_line_targets": position_item_id PK, kind, analog_position_item_id (без FK), set_by, set_at

ZONES = ("underground", "aboveground", "territory", "whole")
ZONE_SOURCES = ("rule", "model", "human")
KP_TARGET_KINDS = ("freshest", "analog")
# SemanticJobKind: + section_zone, releasable_params
# PositionItem.section_chain_id, FamilyParameter.releasable, SemanticJob.section_chain_id
CODE_RUNG_NOT_APPLICABLE = "rung_not_applicable"   # crud/kp_decisions.py
```

**Утверждения**
- `alembic upgrade head` создаёт всё из таблицы §2.18 с именами
  `pk_/fk_/uq_/ck_<таблица>_…`; `just db-test-check` без дрейфа;
- CHECK по одному входу на каждый: зона вне четырёх кодов; `zone` без
  `zone_source` и наоборот; `zone_source = 'human'` без `decided_by`;
  предложение при заданной зоне; `section_chain_id` у главы;
  `releasable = true` у параметра `role = 'secondary'`; `confidence` 1,01;
  `kind = 'analog'` без `analog_position_item_id` и `freshest` с ним; вид
  задания `section_zone` с `family_id` и без `section_chain_id`;
  `releasable_params` без `schema_id`; `family_suggestion` с
  `section_chain_id`;
- паритет CHECK модели и миграции — `TestParityWithMigration` (литералы —
  константами внутри миграции);
- **К1:** upgrade при строке в `kp_line_rungs` — `RuntimeError` с текстом
  «есть ручные ступени работ: 1 — снимите их на экране 4б до обновления»,
  таблица и строка целы; downgrade отказывает по входу на каждый вид
  решения (строка `kp_line_targets`, цепочка `human`, параметр
  `releasable` + `origin = 'human'`); на пустых — снимает новое и
  восстанавливает пустую `kp_line_rungs` с прежними ограничениями;
- каскады: удаление строки КП уносит её цель; удаление параметра — его
  предложение; удаление цепочки, на которую ссылается строка, отказывает;
- `load_decisions` отдаёт `saved.work == {}`; `PUT …/rung` у строки-работы —
  422 `rung_not_applicable`, у системы — как 4б; `DELETE …/rung` у работы —
  204 без действия;
- тесты 4б, сохранявшие ступень строки, переписаны на отказ
  `rung_not_applicable` или удалены (список — в отчёте исполнителя);
  остальные `-k test_kp_file` зелёные.

**Имена**
- Заводятся: три класса и таблицы, столбцы, `ZONES`, `ZONE_SOURCES`, `KP_TARGET_KINDS`, два вида заданий, `CODE_RUNG_NOT_APPLICABLE`, ревизия `0024`.
- Существуют: `KpLineRung` (удаляется), `_DOMAIN_TABLES`, `_downgrade_blockers`, `put_rung`, `delete_rung`.

**Проверка**
- `just test-int-local-k test_analog` — ДО 0, ПОСЛЕ ≥ 25.
- `just test-int-local-k test_kp_file` и `just test-unit-k test_kp_file` — зелёные.
- `just db-test-check` — зелёная.

### Task 2: цепочки разделов и зона — правило, задание, очередь, команда

**Files**
- Create: `backend/services/zone_rules.py`, `backend/crud/section_chains.py`, `backend/services/zone_request.py`
- Edit: `backend/services/import_pipeline.py`, `backend/services/semantic_worker.py`, `backend/services/semantic_cost.py`, `backend/config.py`, `backend/routers/semantic.py`, `backend/cli.py`
- Test: `tests/unit/test_analog_zone_rules.py`, `tests/integration/test_analog_chains.py`, `tests/integration/test_analog_zone_job.py`

**Interfaces**
- Потребляет: `chapter_paths`, `find_privacy_matches`, `render_job_request`, `record_result`, `tariffs_from`, `reconcile_semantic_jobs` (образец постановки), `backfill-contexts` (образец команды).
- Производит:

```python
# services/zone_rules.py
def normalize_title(title: str) -> str
def chain_key(titles: Sequence[str]) -> str
def rule_zone(titles: Sequence[str]) -> Zone | None      # от ближней главы к корню; два шаблона — None

# crud/section_chains.py
def assign_section_chains(db: Session, estimate_ids: Sequence[int]) -> ChainsOutcome   # created, assigned, enqueued
def backfill_chains(db: Session, *, enqueue: bool, reapply_rule: bool, batch: int = 500) -> BackfillReport
def list_chains(db: Session, *, filter: ChainFilter, q: str | None, page: int) -> dict
def decide_zone(db: Session, chain_id: int, zone: Zone, *, actor_id: int) -> None

# services/zone_request.py
def render_zone_request(db: Session, chain_id: int, *, settings: Settings) -> RenderedRequest
def record_zone_result(db: Session, job: SemanticJob, answer: ZoneAnswer, *, settings: Settings) -> None
```

**Утверждения**
- `chain_key`: регистр, `ё`→`е`, NBSP, схлопывание пробелов, края,
  разделитель `" / "` — табличный тест;
- `rule_zone`: по входу на каждую альтернативу каждого шаблона §2.4;
  «ближняя глава первой» (корень «Подземная часть», глава «Кровля» →
  `aboveground`); название с двумя шаблонами останавливает просмотр без
  ответа, даже если выше есть однозначное; `whole` правило не даёт;
- совпадение с `measure.py` на корпусе цепочек стенда — разовый скрипт в
  отчёте (910 без ответа ± расхождения с объяснением), в тесты не входит;
- `assign_section_chains`: строки (не главы) получают `section_chain_id`;
  повторный импорт той же цепочки находит существующую (`ON CONFLICT
  (chain_key) DO NOTHING`); правило и задание — только для созданных этим
  вызовом цепочек; цепочка с зоной `human` не меняется (Review Focus 3);
  счётчики `import_jobs` не меняются;
- импорт сессии B зовёт шаг между `match_positions` и `route_positions`;
  отказ шага откатывает импорт целиком (как `RoutingError`);
- задание `section_zone`: тело — названия цепочки и до 10 самых частых
  наименований строк с единицей (ничьи — меньший id); тест рендера: в теле
  нет цены, объёма, подрядчика, номера договора; отпечаток — от ключа
  цепочки (новые строки под цепочкой задание не меняют);
- принятие по обе стороны порога 0,95 и ответ `unknown` — предложение;
  совпадение со словарём приватности — `privacy_hold`;
- **К2:** ответ модели после решения человека — цепочка не меняется (ответ
  только в попытке); `zones-backfill --reapply-rule` пересчитывает только
  `rule`;
- `tariffs_from(section_zone)` — `SEMANTIC_PRICE_*`; вызов —
  `SEMANTIC_MODEL`, без рассуждения, `max_tokens` из настройки;
- `GET /section-chains` — фильтры `disputed|undetermined|rule|model|human|all`,
  поиск по ключу, страницы; `PUT /section-chains/{id}/zone` — `human`,
  снимает предложение, 422 `invalid_zone`; оба `require_admin`;
  `/status` — `zones_disputed`;
- `zones-backfill [--enqueue] [--reapply-rule]` идемпотентна: второй запуск
  — ноль созданных и поставленных; печатает счёт и оценку до постановки.

**Имена**
- Заводятся: функции выше, `Zone`, `ChainFilter`, `ChainsOutcome`, `BackfillReport`, `ZoneAnswer`, `SEMANTIC_ZONE_AUTO_ACCEPT_THRESHOLD`, `SEMANTIC_ZONE_MAX_TOKENS`, команда `zones-backfill`.
- Существуют: `chapter_paths`, `find_privacy_matches`, `render_job_request`, `record_result`, `tariffs_from`, `match_positions`, `route_positions`.

**Проверка**
- `just test-unit-k test_analog` — ДО 0, ПОСЛЕ ≥ 30; `just test-int-local-k test_analog` — ПОСЛЕ ≥ 55.
- Наборы очереди и импорта: `just test-int-local-k "semantic_worker or import_pipeline"` — зелёные.

### Task 3: признак «можно отпустить» — хранение, правка, задание, предложения — **ревью**

**Files**
- Create: `backend/services/releasable.py`
- Edit: `backend/services/schema_edit.py`, `backend/services/work_variants.py`, `backend/services/work_families.py`, `backend/services/semantic_worker.py`, `backend/services/semantic_cost.py`, `backend/config.py`, `backend/routers/semantic.py`, `backend/cli.py`
- Test: `tests/unit/test_analog_releasable_render.py`, `tests/integration/test_analog_releasable.py`

**Interfaces**
- Потребляет: `ParameterDraft`, `_body_canon`, `_plan_parameter`, `_carry_human_parameters`, `freeze_schema`, `update_family`, `set_unit`, `merge_families`, `bump_revision`, `render_job_request`, `record_result`.
- Производит:

```python
# services/releasable.py
def render_releasable_request(db: Session, family_id: int, schema_id: int, *, settings: Settings) -> RenderedRequest
def lock_inputs(db: Session, family_id: int, schema_id: int) -> None      # семья FOR SHARE → версия → параметры
def record_releasable_result(db: Session, job: SemanticJob, answer: ReleasableAnswer, *, settings: Settings) -> None
def current_proposals(db: Session, *, family_ids: Sequence[int] | None, page: int | None) -> list[ProposalView]
def decide_releasable(db: Session, family_id: int, parameter_id: int, releasable: bool,
                      *, request_hash: str | None, actor_id: int) -> None   # 409 proposal_stale
def drop_stale_proposals(db: Session, schema_ids: Sequence[int], *, settings: Settings) -> int
def enqueue_releasable(db: Session, schema_id: int, *, settings: Settings) -> bool
```

**Утверждения**
- правка схемы: `ParameterDraft.releasable`, поле в `_body_canon` (хэш
  предпросмотра меняется от флажка), `_plan_parameter` называет изменение,
  пересчёта вариантов нет; смена роли `main → secondary` обнуляет признак;
  решение человека — `origin = human`, `revision + 1`;
- `_carry_human_parameters` переносит признак; параметр модели при
  пересборке не размечен, задание ставится на новую версию;
- ответ модели: каждый активный главный ровно один раз (пропуск и повтор —
  ошибка попытки); ≥ 0,95 — пишется `true`/`false`, `origin` и `revision`
  не меняются; ниже — предложение с `request_hash`; размеченный параметр
  ответом не трогается;
- **К3, ответ:** задание поставлено → параметр переименован (отдельно —
  сменены значения; отдельно — определение семьи через `update_family`) →
  уверенный ответ: `cancelled` / `input_changed`, признак `NULL`, предложений
  нет; версия ушла из `frozen` (пересборка, `merge_families` — Review Focus
  5) — `not_applicable`;
- **К3, предложение:** отложенное предложение → `update_family` меняет
  определение → `PUT …/releasable` со старым `request_hash` — 409
  `proposal_stale`, признак `NULL`; то же с именем семьи и с `set_unit`;
  такое предложение не видно в очереди, в схеме и в `releasable_open`;
- уборка: после `schema_edit`, `update_family`, `set_unit`, перевода версии
  из `frozen` недействующие предложения удалены; **снятие уборки в
  `update_family` оставляет тест К3 зелёным** (защиту держит рендер) — это
  проверка оркестратора, а не исполнителя;
- замки: тест SQL `lock_inputs` — семья `FOR SHARE` раньше версии, версия
  раньше параметров (компиляция запроса, образец `_lock_families_statement`);
- постановка: при заморозке версии с активными главными; после правки входов
  при неразмеченном главном и без задания с новым отпечатком (старое
  незавершённое — `input_changed`); `releasable-enqueue-all` идемпотентна,
  печатает счёт и оценку;
- `GET /families/{id}/schema` — `releasable`, `releasable_proposal`;
  `GET /releasable-proposals`, `PUT /families/{fid}/parameters/{pid}/releasable`
  (404, 422 `parameter_not_main`, 409) — `require_admin`; `/status` —
  `releasable_open`;
- `tariffs_from(releasable_params)` — `SEMANTIC_SCHEMA_PRICE_*`.

**Имена**
- Заводятся: функции выше, `ReleasableAnswer`, `ProposalView`, `SEMANTIC_RELEASABLE_AUTO_ACCEPT_THRESHOLD`, `CODE_PROPOSAL_STALE`, команда `releasable-enqueue-all`.
- Существуют: см. «Потребляет» (проверено `grep`: `schema_edit.py:786`, `:902`; `work_variants.py:820`, `:999`; `work_families.py:337`, `:971`).

**Проверка**
- `just test-unit-k test_analog`, `just test-int-local-k test_analog` — рост ≥ 35.
- Наборы схемы и семей: `just test-int-local-k "schema_edit or work_families or work_variants"` — зелёные.

### Task 4: чистое ядро лестницы работ — **ревью**

**Files**
- Create: `backend/services/analog_ladder.py`
- Test: `tests/unit/test_analog_ladder.py`

**Interfaces**
- Потребляет: `_outside`, `_median`, `gross_to_net`, `adjust_amount`, `is_price` (существуют).
- Производит:

```python
@dataclass(frozen=True)
class Analog:
    position_item_id: int; contract_id: int
    net: Decimal          # с приведением, если есть множитель
    nominal: Decimal      # без приведения
    period: YearMonth | None
    place_match: int
    outlier_ratio: Decimal | None   # None — в остатке

@dataclass(frozen=True)
class Figure:
    value: Decimal; analog_id: int | None; contract_id: int | None

@dataclass(frozen=True)
class WorkLadder:
    rung_no: Literal[1, 2, 3, 4] | None
    released: tuple[str, ...]; other_class: bool
    zone: Zone | None
    analogs: tuple[Analog, ...]          # справка §2.11, порядок задан
    remainder_ids: frozenset[int]
    lowest: Figure | None; freshest: Figure | None
    all_outliers: bool; kp_ratio: Decimal | None
    no_history_reason: Literal["no_family", "no_params", "no_zone_match"] | None
    vat_dropped: tuple[int, ...]

def evaluate_work_ladder(kp: KpWork, lines: Sequence[WorkHistoryLine], families: Mapping[int, FamilyLadderInfo],
                         contracts: Mapping[int, HistoryContract], *, tender_class_id: int,
                         tender_contractor_id: int, target_month: YearMonth | None,
                         factors: Mapping[int, Decimal] | None) -> WorkLadder
```

**Утверждения**
- кандидат — табличный тест по пяти условиям §2.8 (каждое по отдельности
  выключает строку); **К4:** «только символический аналог на ступени 1,
  нормальный на 2» — выбрана 2; цена ровно 1 ₽ — аналог; 0,99 — нет;
- ступени 1–4: первая непустая; тест из §2.24 — строка КП W6, в своём
  классе аналогов нет, в другом классе W6 за 100 и W8 за 70 (W не
  отпускается): ступень 3, самая низкая 100, W8 в справке нет; ступени 2/4 только при `releasable` на
  действующей версии; вариант на старой версии — ступеней 2/4 нет (Review
  Focus 1); «не уточнено» совпадает только с «не уточнено»;
- **К5:** `1, 100, 10 000` и `1, 1, 100, 100` — все выбросы,
  `unit_mismatch` с `all_outliers`, ступень не меняется; два расходящихся;
  граница ×5 и ×0,2 — «внутри»;
- КП в «разных единицах» — по медиане остатка; выброс среди аналогов строку
  не переводит;
- самая низкая: ничьи — договор с этим подрядчиком → расстояние дат → id;
  самая свежая: расстояние месяцев к `target_month`, ничьи — близость места
  → цена → id; без `target_month` — самая поздняя; аналог без периода в
  самую свежую не входит, весь остаток без периода — `freshest = None`
  (Review Focus 2);
- вилка и положение — по остатку;
- `no_history_reason`: по входу на каждую из трёх причин, «любого класса»;
- близость места — число общих нормализованных названий; справка
  упорядочена: близость ↓, расстояние дат ↑, цена ↑, id;
- `vat_dropped` — по договору, как 4а: договор с одной строкой неизвестной
  базы и одной годной не попадает;
- с множителем ИПЦ: `net = nominal × k`; порог 1 ₽ — по `nominal` (вход
  «номинал 0,9, с рядом 1,8» — не аналог);
- ни одного `float` в модуле (`grep` в отчёте) и в результатах (тест типов).

**Имена**
- Заводятся: `Analog`, `Figure`, `WorkLadder`, `KpWork`, `WorkHistoryLine`, `FamilyLadderInfo`, `evaluate_work_ladder`.
- Существуют: `_outside`, `_median` (`services/our_price.py:400`, `:417`), `YearMonth`.

**Проверка**
- `uv run pytest tests/unit -q -k test_analog_ladder` — ДО 0, ПОСЛЕ ≥ 45.

### Task 5: загрузка, сцена, ответы чтения, системы, итоги — **ревью**

**Files**
- Edit: `backend/crud/our_price_load.py`, `backend/services/our_price.py`, `backend/crud/our_price.py`
- Test: `tests/unit/test_analog_totals.py`, `tests/integration/test_analog_scene.py`, `tests/integration/test_analog_systems.py`; правка ожиданий 4а про лестницу работ (`test_our_price_*`)

**Interfaces**
- Потребляет: `evaluate_work_ladder` (Task 4), `load_history`, `load_kp_lines`, `catalog_signal`, `_estimate_period`, `round_target_month`, `_line_view`, `build_line_view`, `_totals_view`.
- Производит:

```python
def load_work_history(db: Session, *, exclude_object_id: int) -> tuple[list[WorkHistoryLine], dict[int, FamilyLadderInfo]]
# HistoryContract.period: YearMonth | None
# KpLineRow / KpLine: + section_chain_id, zone, chain_titles, schema_id, main_values
# LineState.no_zone
# LineResult: + ladder: WorkLadder | None, freshest: Figure | None, to_freshest: Decimal | None
# GroupResult: + freshest, to_freshest, over_freshest
# OfferTotals: + over_freshest: Decimal | None, comparable_lines: int, freshest_lines: int
def evaluate_offer(lines, history, *, work_history, families, kp_vat_base, tender_area, tender_class_id,
                   tender_contractor_id, target_month, factors, choice=None, saved=None) -> OfferEvaluation
```

**Утверждения**
- работы идут через `evaluate_work_ladder`; состояния — таблица §2.10 по
  порядку, `no_zone` между `no_variant` и `no_history`; `in_progress` — ещё
  при живом `section_zone` цепочки и живом `releasable_params` действующей
  версии семьи; `waiting_admin` у `no_zone` — по §2.10;
- `SavedWorkRung`, `_work_manual`, ветка `saved.work` и выбор ступени
  работы удалены; `choice` (предпросмотр ступени) у работы — 422
  `unknown_rung` на панели;
- системы: **К4** — строка договора 0,50 ₽ за ед. не входит в сумму ключа
  до деления на площадь, ставка 0,50 ₽/м² входит; самая свежая — по §2.13,
  ничьи — ставка, `contract_id`; без периода у всех — `None`;
  существующие тесты систем 4а зелёные без правки ожиданий (кроме новых
  полей);
- итоги: `over_lowest`, `below` — как `bargain`/`below` 4а; **К6** — четыре
  строки таблицы покрытия §2.12: ни одной сравнимой; ни у одной нет самой
  свежей (`null`, не 0); у части (`freshest_lines < comparable_lines`); у
  всех; **К7** — суммы не зависят от решений 4б («Договорились») —
  выбор цели проверяет Task 6;
- ответы: поля строки и панели §2.17 (`work_rung`, `zone`,
  `section_chain_id`, `lowest`, `freshest`, `to_lowest`, `to_freshest`,
  `analogs_count`, `no_history_reason`, `unit_mismatch{…}`,
  `target_kind`, `choice` — последние два пока `lowest`/`null`); панель
  работы — `analogs[]` в порядке §2.11 с `selectable`, `contracts` и
  `group_lines` — `[]`; итоги — без `bargain`, с покрытием;
- **К9:** три `GET` 4а — ни одной записи (тест счётчиков транзакции 4а
  расширен на новые пути);
- тест типов JSON — ни одного `float`;
- история: договор из победителя тендера и строка соседнего КП того же
  тендера в справке отсутствуют (тест §2.24, первая строка);
- ожидания 4а, завязанные на лестницу работ (`rung`, `rungs`, `contracts`
  у работ, `to_ours`), переписаны на новую форму — список файлов в отчёте;
  тесты систем не правятся.

**Имена**
- Заводятся: `load_work_history`, `LineState.no_zone`, поля выше.
- Существуют: `load_history` (`crud/our_price_load.py:398`), `load_kp_lines` (`:494`), `_estimate_period` (`crud/comparison.py:581`), `_line_view` (`crud/our_price.py:658`), `build_line_view` (`:955`).

**Проверка**
- `just test-unit-k test_analog`, `just test-int-local-k test_analog` — рост ≥ 40.
- `just test-unit-k test_our_price` (225) и `just test-int-local-k test_our_price` (429) — зелёные, число может меняться только на переписанных тестах лестницы работ (называются).

### Task 6: цель строки, комментарий и файл, мутации — **ревью**

**Files**
- Edit: `backend/crud/kp_decisions.py`, `backend/crud/our_price.py`, `backend/services/kp_comment.py`, `backend/services/kp_file.py`, `backend/routers/tenders.py`, `backend/services/round_import.py`
- Test: `tests/unit/test_analog_comment.py`, `tests/integration/test_analog_targets.py`

**Interfaces**
- Потребляет: `EstimateDecisions`, `_write_decision`, `count_round_decisions`, `pick_source`, `line_comment_inputs`, `_source_text`, `_what`.
- Производит:

```python
# crud/kp_decisions.py
EstimateDecisions.targets: dict[int, tuple[str, int | None, datetime, int, str]]
DecisionCounts.targets: int
def put_target(db, tender_id, round_id, offer_id, pid, *, kind: Literal["freshest", "analog"],
               analog_id: int | None, inflation_series_id: int | None, actor_id: int) -> None
def delete_target(db, tender_id, round_id, offer_id, pid) -> None
# services/our_price.py (или kp_decisions): статус выбора
@dataclass(frozen=True)
class TargetChoice:
    kind: Literal["freshest", "analog"]; analog_id: int | None
    active: bool; reset_reason: Literal["kind_changed", "not_comparable", "analog_unavailable", "no_freshest"] | None
# маршруты: PUT|DELETE …/our-price/offers/{offer_id}/lines/{position_item_id}/target?inflation_series_id=
```

**Утверждения**
- цель: договорились → действующий выбор → самая низкая — табличный тест;
  причины сброса по входу на каждую, в порядке таблицы §2.14; выброс выбрать
  нельзя (`PUT` — 422 `analog_unavailable`);
- **К8:** номиналы `10, 100, 110`, множитель 3 у сметы первого: `PUT` с
  рядом — 204, без ряда — 422 `analog_unavailable`; чтение с рядом — выбор
  действует, без ряда — `analog_unavailable`; обратный вход зеркально;
  `selectable` панели совпадает с исходом `PUT` при том же ряде;
- **К7:** суммы и плитки до и после `PUT …/target` равны; меняются
  `target_kind`, `file_ours`, предпросмотр файла, комментарий;
- аналог, чья строка ушла с заменой сметы договора, — `analog_unavailable`,
  ответ 200 (Review Focus 4); строка-работа, ставшая системой, —
  `kind_changed` (Review Focus 6);
- комментарий и файл: источник — аналог цели; ступени 1/3 — «эта работа
  стоила», 2/4 — «сопоставимая работа стоила»; «в среднем» у работ нет;
  `kp_unit` / `history_unit` по §2.15; цель «самая свежая» при КП между
  низкой и свежей — без комментария; хвост ИПЦ по множителю сметы
  аналога-источника; годы блока ИПЦ — по договорам аналогов-источников;
  системы — как 4б (их тесты зелёные без правки);
- счёт решений: `targets` в отказе замены раунда и в токене удаления
  участника; тексты называют выборы;
- права: `PUT`/`DELETE …/target` — `require_admin`, `member` — 403; сторож
  прав зелёный;
- `PUT …/target` у системы и у не-`comparable` — 422
  `target_not_applicable`; отказы ряда ИПЦ — кодами чтения.

**Имена**
- Заводятся: `put_target`, `delete_target`, `TargetChoice`, `EstimateDecisions.targets`, `DecisionCounts.targets`, `CODE_TARGET_NOT_APPLICABLE`, `CODE_ANALOG_UNAVAILABLE`, `CODE_NO_FRESHEST`.
- Существуют: `pick_source` (`services/kp_comment.py:76`), `line_comment_inputs` (`services/kp_file.py:154`), `count_round_decisions` (`crud/kp_decisions.py:178`).

**Проверка**
- `just test-unit-k test_analog`, `just test-int-local-k test_analog` — рост ≥ 35.
- `just test-unit-k test_kp_file`, `just test-int-local-k test_kp_file` — зелёные; правки ожиданий только у работ (называются).
- `uv run pytest tests/test_auth_coverage.py -q` — зелёный.

### Task 7: фронт разбора — панель, таблица, плитки, карточка, цель

**Files**
- Create: `frontend/src/pages/tenders/our-price/AnalogsTable.tsx`, `FigureCards.tsx` (+ тесты рядом)
- Edit: `types/domain.ts`, `services/api/domain.ts`, `services/queries.ts`, `test/handlers.ts`, `test/fixtures.ts`, `pages/tenders/our-price/{ourPriceCopy.ts, LineSheet.tsx, OfferLinesTable.tsx, OfferTiles.tsx, ParticipantCard.tsx, offerFilter.ts, ExportBlock.tsx, RungSaveBar.tsx}`

**Утверждения**
- типы повторяют форму §2.17; деньги — строки, без арифметики в JS;
- панель работы: подпись ступени и зона; две карточки (цена, объект, дата
  цены, разница КП); справка — первые пять, «ещё {N}»; метки «самая
  низкая», «самая свежая», «договор с этим подрядчиком», «×N к остальным —
  проверьте единицу учёта»; «сделать целью» у `selectable` только у
  `admin`; причины `no_history` и `no_zone` текстом, ссылка в очередь
  «Зоны» с `chain`;
- «Для выгрузки»: строка цели, «Сделать целью самую свежую», «Вернуть к
  самой низкой», плашка «Выбор не действует: {причина}»; мутация передаёт
  ряд ИПЦ экрана; `analog_unavailable` — тост и перезапрос панели;
  `RungSaveBar` и лестница — только у систем;
- таблица: «Самая низкая», «К самой низкой», подпись ступени и «N
  аналогов», метка цели; плитки и карточка участника — пять плиток §2.20,
  подпись покрытия у самых свежих по таблице §2.12 (тест на четыре
  случая);
- лоадер — серый слой над старыми цифрами при сохранении цели;
  инвалидация — как решения 4б;
- снимок стенда (панель, таблица, плитки) рядом с макетом; вид чисел и
  ширины колонок сверены глазами.

**Проверка**
- `npx vitest run src/pages/tenders/our-price src/services` — зелёные, рост ≥ 25;
- `just typecheck-frontend`, `just lint-frontend` — зелёные.

### Task 8: фронт каталога — очереди «Зоны» и «Можно отпустить», признак в схеме

**Files**
- Create: `frontend/src/pages/families/ZonesQueue.tsx`, `ReleasableQueue.tsx` (+ тесты)
- Edit: `services/api/admin.ts`, `services/queryKeys.ts`, `services/queries.ts`, `pages/families/{SuggestionsTab.tsx, SchemaBlock.tsx, schema-edit/ParameterEditor.tsx}`, `test/handlers.ts`

**Утверждения**
- «Зоны»: фильтр (по умолчанию спорные), поиск, страницы, строка — названия
  цепочки, примеры наименований, предложение и уверенность, `toggle-group`
  четырёх зон; подсветка по `?chain=`;
- «Можно отпустить»: по семьям, кнопки «Можно отпустить» / «Обязателен»,
  `request_hash` в запросе; 409 `proposal_stale` — тост и перезапрос;
- `SchemaBlock`: метка «можно отпустить» / «обязателен» / «не размечен» и
  «предложение модели»; `ParameterEditor`: `ui/checkbox` только при `main`,
  смена роли снимает флажок;
- инвалидация после решения зоны и признака: ключи очередей, схемы семьи и
  префикс `["tenders","our-price"]` — тест «решение зоны → перезапрос
  разбора»;
- счётчики вкладок — из `/status`.

**Проверка**
- `npx vitest run src/pages/families src/services` — зелёные, рост ≥ 15;
- `just typecheck-frontend`, `just lint-frontend` — зелёные; снимок очередей рядом с макетом.

### Task 9: ревизия `AGENTS.md` и справочник

**Files**
- Edit: `AGENTS.md`, `docs/AGENTS-revisions.md`, `docs/reference/schema.md`, `docs/reference/screens.md`, `docs/reference/money-axes.md`

**Утверждения**
- `AGENTS.md` §3 (роль `admin`, разбор зон и признака), §5 (шаг цепочек в
  сессии B) — по §2.21; номер версии — следующий, запись в
  `AGENTS-revisions.md`;
- `schema.md` — `section_chains`, новые столбцы, `releasable_proposals`,
  `kp_line_targets`, удаление `kp_line_rungs`; `screens.md` §8 — панель,
  плитки, очереди, правило заведения объекта (§2.3); `money-axes.md` —
  аналог, две цифры, суммы, цель;
- страж документации 18/18.

**Проверка**
- `just check-agents-index` — зелёный.

### Task 10: стенд, замеры, devlog

**Files**
- Create: `docs/devlog/2026-11-07-analog-ladder.md`
- Edit: `docs/product-roadmap.md`

**Утверждения**
- на `gca_dev`: `alembic upgrade head`; `zones-backfill --enqueue`,
  `releasable-enqueue-all` — оркестратор **после согласия пользователя**
  (вызовы модели платные, оценка ≈ 3–4 $ печатается командами до
  постановки); стоимость прогона — из журнала попыток;
- распределение строк КП последних этапов по ступеням и состояниям против
  таблицы дизайна §4 — в devlog (с поправкой на зоны модели и разметку);
- скорость: этап и панель до и после на тех же этапах; цель — не хуже 4а
  более чем на 25 % (§2.23);
- DoD 1–8 спеки пройдены на стенде снимками (условные имена в devlog);
- отступления от `AGENTS.md` §9.3 (без локального `just ci`) названы;
- дорожная карта: пункт «лестница аналогов» — сделан, «откуда цифра» —
  следующий.

**Проверка**
- `gh pr checks 74` — зелёные после пуша; `just check-agents-index` — зелёный.

## Команды проверки

- По задаче — в самой задаче; только затронутые наборы.
- По фиче — GitHub Actions на PR #74 (`gh pr checks 74`); локальный `just ci`
  не гоняется (режим реализации выше).
- Стенд — снимки рядом с макетом после Task 7 и Task 8; замеры — Task 10.
