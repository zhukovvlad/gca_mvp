# План: вход в очередь «Зоны» по ссылке «ждёт решения»

**Спека:** [`docs/superpowers/specs/2026-11-18-zone-link-entry-design.md`](../specs/2026-11-18-zone-link-entry-design.md)
**Дизайн и макет:** [`docs/superpowers/design/2026-11-18-zone-link-entry.md`](../design/2026-11-18-zone-link-entry.md)
**Ветка:** `fix/zone-link-entry`, черновой PR #91

## Global Constraints

- Новый маршрут только читает и только для `admin` (AGENTS.md §3, роли);
  наружу ничего не уходит.
- Форма элемента цепочки одна на список и на одну цепочку (спека §1).
- Решение зоны — существующий `PUT /section-chains/{id}/zone` и
  `useDecideChainZone`; их поведение не меняется.
- Фронт — только компоненты shadcn/ui, уже стоящие в проекте.
- Режим проверок — по риску (решение пользователя 28.10.2026): задача не
  трогает деньги, НДС, атомарность и конкурентность. Права нового маршрута —
  единственное критическое свойство; его снятие защиты делает оркестратор
  лично. Отдельного ревью задачи нет; итоговое ревью — Codex. Это отступление
  от `docs/process/implementation.md` со ссылкой на то же решение.

## Структура файлов

- Edit: `backend/crud/section_chains.py` — общий сборщик элементов, `get_chain`.
- Edit: `backend/routers/semantic.py` — `GET /section-chains/{chain_id}`.
- Edit: `backend/tests/integration/test_analog_chains.py`,
  `backend/tests/integration/test_semantic_api.py`.
- Edit: `frontend/src/services/api/domain.ts`, `frontend/src/services/queryKeys.ts`,
  `frontend/src/services/queries.ts`, `frontend/src/test/handlers.ts`.
- Create: `frontend/src/pages/families/ZoneEntryCard.tsx` и
  `ZoneEntryCard.test.tsx`.
- Edit: `frontend/src/pages/families/ZonesQueue.tsx`, `SuggestionsTab.tsx`,
  `labels.ts` и тесты `ZonesQueue.test.tsx`, `SuggestionsTab.ladder.test.tsx`,
  `FamiliesPage.chainLink.test.tsx`.
- Create: `docs/devlog/2026-11-18-zone-link-entry.md`.

## Решения плана, которых нет в спеке

1. Одна задача: бэкенд и фронт малы и проверяются вместе снимком стенда.
2. Ключ карточки — `["semantic-queue", "zones", "chain", id]`: лежит под
   `qk.semanticQueue.zonesAll`, поэтому `invalidateAfterLadderDecision`
   перечитывает его без правки.
3. Кнопки зон и строка модели выносятся из `ZoneRow` в экспортируемые
   `ZoneToggle` и `modelLine` (`ZonesQueue.tsx`) и переиспользуются карточкой,
   чтобы карточка и очередь не разошлись.
4. Карточку рисует `SuggestionsTab` над `ZonesQueue` (пока открыта очередь
   «Зоны» и вход пришёл с `chainId`); у `ZonesQueue` проп
   `highlightChainId` удаляется вместе с подсветкой и `zone-not-found`.
5. Поллинга у запроса карточки нет: решение в ней сбрасывает ключ, а ответ
   модели по одной цепочке за время просмотра не ждём.

## Задачи

### Task 1: одна цепочка по номеру, карточка входа, пояснение очереди

**Files** — как в «Структуре файлов».

**Interfaces**
- Потребляет (существуют): `list_chains`, `SAMPLE_TITLES`, `load_zone_material`,
  `DomainError`, `raise_domain_error`, `require_admin`, `LADDER_ROUTE_TEMPLATES`,
  `SectionChainRow`, `semanticApi`, `qk.semanticQueue.zonesAll`,
  `useDecideChainZone`, `apiErrorStatus`, `ladderQueues`, `LADDER_PAGE_SIZE`,
  `ZONE_LABEL`, `ZONE_SOURCE_LABEL`, `SECTION_ZONES`, `parseEntry`.
- Производит:

```python
def _chain_items(db: Session, chains: Sequence[SectionChain]) -> list[dict]
def get_chain(db: Session, chain_id: int) -> dict  # DomainError(404), если нет

@router.get("/section-chains/{chain_id}")
def get_section_chain_route(chain_id: int, _admin: User = Depends(require_admin),
                            db: Session = Depends(get_db)) -> dict
```

```ts
// services/api/domain.ts
getSectionChain: (chainId: number) => Promise<SectionChainRow>
// services/queryKeys.ts
qk.semanticQueue.zoneChain: (chainId: number) => readonly ["semantic-queue", "zones", "chain", number]
// services/queries.ts
export function useSectionChain(chainId: number): UseQueryResult<SectionChainRow>
// pages/families/ZonesQueue.tsx
export function modelLine(chain: SectionChainRow): string
export function ZoneToggle(props: { chain: SectionChainRow; busy: boolean;
  onDecide: (zone: SectionZone) => void }): JSX.Element
export function ZonesQueue(): JSX.Element            // проп highlightChainId удалён
// pages/families/ZoneEntryCard.tsx
export function ZoneEntryCard(props: { chainId: number }): JSX.Element
// pages/families/labels.ts
export const ZONES_EXPLAIN: string
export const ZONE_ENTRY_TITLE: string
export const ZONE_ENTRY_ASK: string
export const ZONE_ENTRY_BACK: string
export const ZONE_ENTRY_NOT_FOUND: string
```

**Утверждения**

Бэкенд:
- `get_chain` для цепочки возвращает словарь, равный её элементу в
  `list_chains(filter="all", …)` той же базы — на цепочке со спорным
  предложением, заданием и строками КП и договоров (все поля, не выборочно);
- `get_chain` на несуществующем `id` — `DomainError` со статусом 404;
- маршрут: `admin` — 200 с той же формой; несуществующий `id` — 404;
  `member` — 403; без входа — 401 или 403;
- `LADDER_ROUTE_TEMPLATES` содержит `("GET", f"{BASE}/section-chains/{{chain_id}}")`,
  его размер 7, проверка полноты маршрутов зелёная;
- `list_chains` после выноса сборщика отдаёт то же, что до (существующие
  тесты `list_chains` зелёные без правки).

Фронт:
- вход `?tab=suggestions&queue=zones&chain=<id>` на цепочку, стоящую в
  моке после `LADDER_PAGE_SIZE` других (её нет на первой странице ни
  «Спорных», ни «Всех»): карточка показывает её разделы, строку модели и
  четыре кнопки зон; на сегодняшнем коде проверка красная;
- нажатие зоны в карточке шлёт ровно одно решение `{chainId, zone}`; после
  перечитывания кнопка зоны нажата, вместо строки модели —
  «зона поставлена: человек»;
- `404` от маршрута — `ZONE_ENTRY_NOT_FOUND` вместо карточки, очередь ниже
  отрисована; ошибка 500 — не текст «не найдена»;
- «← вернуться к КП» есть, когда до страницы была навигация, и нет при
  первом входе; нажатие возвращает на предыдущий адрес;
- очередь «Зоны» без ссылки и со ссылкой открывается фильтром «Спорные»;
  `ZONES_EXPLAIN` виден в обоих случаях; `data-highlighted` и
  `zone-not-found` в разметке отсутствуют;
- без `chain` в адресе карточки нет.

Стенд (приёмка оркестратора):
- ссылка «ждёт решения администратора» у строки «Цементно-песчаная стяжка…»
  (КП 76, цепочка 27989) ведёт на карточку цепочки 27989; снимки в светлой и
  тёмной теме сверены с макетом. Зону на стенде не ставить — решение за
  пользователем.

**Имена**
- Заводятся этой задачей: `_chain_items`, `get_chain`, `get_section_chain_route`,
  `getSectionChain`, `qk.semanticQueue.zoneChain`, `useSectionChain`,
  `ZoneToggle`, `ZoneEntryCard`, `ZONES_EXPLAIN`, `ZONE_ENTRY_TITLE`,
  `ZONE_ENTRY_ASK`, `ZONE_ENTRY_BACK`, `ZONE_ENTRY_NOT_FOUND`; `modelLine`
  существует, задача его экспортирует.
- Существуют, проверено `grep`-ом: `list_chains`, `SAMPLE_TITLES`,
  `load_zone_material`, `raise_domain_error`, `require_admin`,
  `LADDER_ROUTE_TEMPLATES`, `SectionChainRow`, `qk.semanticQueue.zonesAll`,
  `useDecideChainZone`, `invalidateAfterLadderDecision`, `apiErrorStatus`,
  `ladderQueues`, `LADDER_PAGE_SIZE`, `ZONE_LABEL`, `ZONE_SOURCE_LABEL`,
  `SECTION_ZONES`, `parseEntry`.

**Проверка**
- `cd backend; .venv/Scripts/python -m pytest tests/integration/test_analog_chains.py tests/integration/test_semantic_api.py -q`
  — зелёная; выбирает 250 тестов ДО, больше 250 ПОСЛЕ (новые тесты
  `get_chain` и маршрута — в этих файлах).
- `cd frontend; npx vitest run src/pages/families/ZoneEntryCard.test.tsx src/pages/families/ZonesQueue.test.tsx src/pages/families/ZonesQueue.overlay.test.tsx src/pages/families/SuggestionsTab.ladder.test.tsx src/pages/families/FamiliesPage.chainLink.test.tsx src/pages/tenders/our-price/LineSheet.work.test.tsx src/pages/tenders/our-price/OfferLinesTable.test.tsx`
  — зелёная; без `ZoneEntryCard.test.tsx` выбирает 149 тестов ДО; ПОСЛЕ —
  с новым файлом; удалённые тесты подсветки заменены, а не просто сняты.
- `npx tsc -b`, `npx eslint .` — чисто.
- Снятие защиты (оркестратор): без `require_admin` у нового маршрута тест
  `member` → 403 краснеет.

## Команды проверки

- По задаче: выше.
- По фиче: GitHub Actions на пуше (локальный `just ci` перед пушем не
  гоняется — решение пользователя 11.10.2026), снимок стенда.
