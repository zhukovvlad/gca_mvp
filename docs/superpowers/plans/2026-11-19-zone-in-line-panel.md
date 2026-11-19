# План: зона в панели строки

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Спека:** [`docs/superpowers/specs/2026-11-19-zone-in-line-panel-design.md`](../specs/2026-11-19-zone-in-line-panel-design.md)
**Дизайн и макет:** [`docs/superpowers/design/2026-11-19-zone-in-line-panel.md`](../design/2026-11-19-zone-in-line-panel.md)
**Ветка:** `feat/zone-in-line-panel`, черновой PR #92

**Goal:** в панели строки КП видно зону цепочки разделов, её источник, охват и
ответ модели; администратор меняет зону на месте.

**Architecture:** сервер добавляет в ответ панели строки поле `zone_chain`,
собранное новой функцией `crud/section_chains.py::zone_chain_info`; фронт
рисует его компонентом `LineZoneBlock` в секции «Где строка» и решает зону
существующим `useDecideChainZone`.

**Tech Stack:** FastAPI, SQLAlchemy, pytest; React, TanStack Query v5, shadcn/ui
(`tooltip`, `alert-dialog`, `toggle-group` — уже стоят), vitest.

## Global Constraints

- Поле `zone_chain` только читает; доступ к панели не меняется; решение —
  существующий `PUT /semantic/section-chains/{id}/zone` (только `admin`).
- Форма `zone_chain` и порядок `model_state` — спека §1 дословно.
- Тексты — спека §3 дословно, в `ourPriceCopy.ts`.
- Фронт — только компоненты shadcn/ui, уже стоящие в проекте.
- Режим проверок — по риску (решение пользователя 28.10.2026): деньги, НДС,
  атомарность не затронуты. Критические свойства — правило окна
  подтверждения (спека §2 п. 3) и порядок `model_state` (спека §1); их снятия
  защиты делает оркестратор лично. Отдельного ревью задачи нет; итоговое
  ревью — Codex.

## Review Focus

1. Цепочка со спорным предложением и одновременно живым заданием —
   `running`, а не `proposed` (порядок спеки §1): тест в задаче.
2. Решённая цепочка, у которой последняя `ok`-попытка не разбирается —
   `model_answer = null`, панель отвечает 200: тест в задаче.
3. Первая установка зоны при `rows_contracts > 0` — окно; при `0` — сразу:
   оба теста в задаче.
4. Нажатие стоящей зоны от правила — закрепление без окна: тест в задаче.
5. Работа без статьи и пути, но с цепочкой — секция «Где строка» всё равно
   есть: тест в задаче.

## Структура файлов

- Edit: `backend/crud/section_chains.py` — `_rows_of` (вынос счётчиков из
  `_chain_items`), `zone_chain_info`.
- Edit: `backend/crud/our_price.py` — поле `zone_chain` в `build_line_view`.
- Edit: `backend/tests/integration/test_analog_chains.py`,
  `backend/tests/integration/test_our_price_offer_api.py`.
- Edit: `frontend/src/types/domain.ts` — `ZoneChainInfo`,
  `OurPriceLineView.zone_chain`.
- Create: `frontend/src/pages/tenders/our-price/LineZoneBlock.tsx`,
  `LineZoneBlock.test.tsx`.
- Edit: `frontend/src/pages/tenders/our-price/LineSheet.tsx`,
  `ourPriceCopy.ts`, `LineSheet.test.tsx`; `frontend/src/pages/families/ZonesQueue.tsx`
  (тип пропа `ZoneToggle`); `frontend/src/test/handlers.ts` и фикстуры
  `OurPriceLineView` (`zone_chain: null` по умолчанию).
- Create: `docs/devlog/2026-11-19-zone-in-line-panel.md`.

## Решения плана, которых нет в спеке

1. Одна задача: сервер и фронт малы и проверяются вместе снимком стенда.
2. `zone_chain_info` живёт рядом с очередью «Зоны»; счётчики строк — одна
   функция `_rows_of` на очередь и панель, чтобы не разошлись. Пауза приёма —
   `crud.our_price_load._claim_paused` (импорт без цикла: `our_price_load`
   не импортирует `section_chains`).
3. `ZoneToggle` принимает `Pick<SectionChainRow, "zone" | "zone_source">`
   вместо всей строки — переиспользуется блоком без подделки строки очереди.
4. Окно — `AlertDialog`; решение уходит только по «Сменить зону». Правило
   окна — чистая экспортируемая функция, тестируется отдельно и через экран.

## Задачи

### Task 1: поле `zone_chain` и блок «Зона места»

**Interfaces**
- Потребляет (существуют): `_chain_items`, `parse_zone_answer`,
  `AnswerSchemaError`, `SemanticJob`, `SemanticJobKind.section_zone`,
  `SemanticJobAttempt`, `SemanticAttemptOutcome.ok`, `_claim_paused`,
  `iso`, `settings.SEMANTIC_ZONE_AUTO_ACCEPT_THRESHOLD`, `build_line_view`,
  `useDecideChainZone`, `zoneDecisionMutationKey`, `ZoneToggle`,
  `RecalcOverlay`, `SAVING_LABEL`, `zoneQueueHref`, `ZONE_CAPTION`,
  `ZONE_LABEL`, `formatConfidence`, `formatDate`, `useCurrentUser`,
  `LineWhere`, `SHEET_SECTION`, `WAITING_ADMIN_LABEL`.
- Производит:

```python
# crud/section_chains.py
def _rows_of(db: Session, chain_ids: Sequence[int]) -> dict[int, tuple[int, int]]  # id -> (kp, contracts)
def zone_chain_info(db: Session, chain_id: int, *, settings: Settings = default_settings) -> dict
```

```ts
// types/domain.ts
export type ZoneModelState = "running" | "paused" | "unknown" | "proposed" | "failed" | "none";
export interface ZoneChainInfo {
  id: number; titles: string[]; zone: SectionZone | null;
  zone_source: "rule" | "model" | "human" | null; decided_at: string | null;
  rows_kp: number; rows_contracts: number; model_state: ZoneModelState | null;
  model_answer: { zone: SectionZone | "unknown"; confidence: string; reason: string } | null;
  threshold: string;
}
// OurPriceLineView: zone_chain: ZoneChainInfo | null
// pages/tenders/our-price/LineZoneBlock.tsx
export function zoneNeedsConfirm(chain: ZoneChainInfo, zone: SectionZone): boolean
export function zoneModelText(chain: ZoneChainInfo): string | null
export function LineZoneBlock(props: { chain: ZoneChainInfo | null; canEdit: boolean }): JSX.Element
// ourPriceCopy.ts: WHERE_ARTICLE = "статья классификатора" и копия спеки §3
```

- [ ] **Step 1: тесты сервера (красные)**

`test_analog_chains.py`, класс `TestZoneChainInfo`:
- по одной фикстуре на каждое значение `model_state` спеки §1; цепочка со
  спорным предложением и заданием `pending` → `running`, то же при
  `claim_paused` → `paused`; `proposed_zone="unknown"` с уверенностью `0.97`
  → `unknown`, `model_answer.confidence == "0.97"`;
- цепочка с зоной `human`: `model_state is None`, `decided_at` равен
  `iso(decided_at)`, `model_answer` — из последней `ok`-попытки (у задания
  две `ok`-попытки — берётся бо́льшая по `id`); `raw_response`, не проходящий
  `parse_zone_answer`, → `model_answer is None`;
- цепочка `rule` без заданий: `model_answer is None`, `model_state is None`;
- `rows_kp`, `rows_contracts` равны полям её элемента в `list_chains`;
  `threshold == "0.95"`.

`test_our_price_offer_api.py`: панель работы с цепочкой — `zone_chain["id"]
== line["section_chain_id"]` и форма спеки §1 (набор ключей ровно её);
панель системы — `zone_chain is None`; строка работы без цепочки
(`section_chain_id` обнулён в фикстуре) — `zone_chain is None`, ответ 200.

- [ ] **Step 2:** `cd backend; .venv/Scripts/python -m pytest tests/integration/test_analog_chains.py tests/integration/test_our_price_offer_api.py -q` — новые тесты красные, прежние (211 ДО) зелёные.

- [ ] **Step 3: сервер.** `_rows_of` вынести из `_chain_items` без смены
  результата; `zone_chain_info` по спеке §1; в `build_line_view` —
  `"zone_chain": None if row.line.is_system or row.line.section_chain_id is None else zone_chain_info(db, row.line.section_chain_id)`.

- [ ] **Step 4:** та же команда — зелёная, больше 211 тестов.

- [ ] **Step 5: тесты фронта (красные)**

`LineZoneBlock.test.tsx`:
- `zoneNeedsConfirm`: `{zone:null, rows_contracts:0}` + `underground` → `false`;
  `{zone:null, rows_contracts:3}` → `true`; `{zone:"underground"}` +
  `underground` → `false`; `{zone:"underground"}` + `whole` → `true`;
- `zoneModelText` — по строке на каждое значение `model_state` и на решённую
  цепочку с ответом и без (тексты спеки §3 п. 5–6; у `unknown` нет слова
  «порог»);
- источник: `rule` / `model` с ответом и без / `human` с датой; без зоны —
  «ждёт решения администратора» у `proposed`, «модель определяет» у
  `running`, ничего у `none`;
- `admin`, `zone=null`, `rows_contracts=0`: нажатие «Подземная» шлёт одно
  `PUT …/zone` без окна; `rows_contracts=3`: открывается окно с обеими
  строками охвата и «Не определена → Подземная часть», «Отмена» — ни одного
  `PUT`, «Сменить зону» — ровно один;
- `admin`, `zone="aboveground"`, `zone_source="rule"`: нажатие стоящей зоны —
  один `PUT` без окна; нажатие «Весь объект» — окно;
- `member`: нет кнопок и ссылки, есть «Зону раздела меняет администратор.»;
- `chain=null`: одна строка спеки §3 п. 7, нет кнопок, ссылки, «?»;
- «?» — фокус клавиатурой открывает подсказку с текстом дизайна §2;
- общая блокировка: при задержанном `PUT` из блока кнопки заблокированы и
  виден «Сохраняем…».

`LineSheet.test.tsx`:
- работа без статьи и пути, с `zone_chain` — секция «Где строка» есть,
  блок в ней после пути;
- `no_zone`: «Сравнение» — «Не с чем сравнить: зона раздела не определена —
  выберите её выше» у `admin`, без хвоста у `member`, «…строка вне разделов
  сметы» при `zone_chain=null`;
- система — блока нет;
- подпись «статья классификатора» (и в `LineWhere.test.tsx`/`AnalogsTable.test.tsx`,
  где подпись проверяется).

- [ ] **Step 6:** из `C:\` (PowerShell `Set-Location`, буква диска заглавная):
  `npx vitest run src/pages/tenders/our-price/LineZoneBlock.test.tsx src/pages/tenders/our-price/LineSheet.test.tsx src/pages/tenders/our-price/LineSheet.work.test.tsx src/pages/tenders/our-price/LineWhere.test.tsx src/pages/tenders/our-price/AnalogsTable.test.tsx src/pages/families/ZonesQueue.test.tsx src/pages/families/ZoneEntryCard.test.tsx`
  — новые красные; ДО без нового файла — 181 тест (`vitest list`).

- [ ] **Step 7: фронт.** Типы, копия, `LineZoneBlock` по спеке §2–3 и макету,
  `ZoneToggle` с узким пропом, вставка в `LineSheet`, `zone_chain: null` в
  моках по умолчанию.

- [ ] **Step 8:** та же команда — зелёная; `npx tsc -b`, `npx eslint .` —
  чисто.

- [ ] **Step 9:** devlog `docs/devlog/2026-11-19-zone-in-line-panel.md`
  (что сделано; спека и план одним кругом — отступление по решению
  пользователя 18.11.2026); коммит.

**Приёмка оркестратора**
- Снятия защиты в копии вне живого дерева: (а) убрать условие
  `rows_contracts > 0` из `zoneNeedsConfirm` — тест первой установки со
  строками договоров краснеет; (б) поставить `proposed` выше `running` —
  серверный тест порядка краснеет.
- Стенд: панель «Дренажный слой с геотекстилем…» (КП 76, цепочка 27989) у
  `admin` — снимки в светлой и тёмной теме, сверка с макетом; окно открыть и
  закрыть «Отменой». Зону на стенде не менять — решение за пользователем.
  Снимок `member` — если учётка с этой ролью есть; новую заводить только
  штатной `cli.py create-user` по согласию пользователя.

## Команды проверки

- По задаче: шаги 2, 4, 6, 8.
- По фиче: GitHub Actions на пуше (локальный `just ci` перед пушем не
  гоняется — решение пользователя 11.10.2026), снимок стенда.
