# План: экран «Семьи и контексты»

**Спека:** `docs/superpowers/specs/2026-09-25-families-screen-design.md` (гейт 2 пройден 25.09.2026, три круга ревью)
**Ветка:** `feat/families-screen`

## Global Constraints

- Схема и миграции не меняются. Существующие операции фичи 1, их тела, отказы и
  блокировки не меняются (спека §2.9). Новая мутация одна — пакетный перенос
  (спека §2.8 п. 4), и она только композиция `accept_transfer`.
- Право `admin` на всех новых маршрутах — `Depends(require_admin)`, как у соседей
  в `routers/semantic.py`.
- Инварианты числа запросов (спека §2.8): `list_contexts` — ровно 2 запроса;
  карточка — не растёт ни с числом членств, ни с числом уникальных путей, ни с их
  глубиной; эндпоинты группы — ровно 2 запроса. Пороги существующих тестов
  числа запросов не правятся.
- Порядок блокировок корзин — только через `lock_buckets` (по возрастанию `id`,
  `services/context_routing.py`); пакет не вводит своего.
- Фронтенд — только shadcn/ui; недостающие компоненты — `npx shadcn add`.
- Коды полей из таблицы спеки §2.2 на экран не выводятся, кроме подсказки журнала.
- `docs/reference/screens.md` `## 9.` переписывается тем же коммитом, что экран
  (Task 8); заголовок `## 9. Семьи и контексты` и `EXPECTED_SCREEN_ANCHORS` не
  трогаются.

## Структура файлов

```
backend/
  crud/semantic.py                 правка: поля строки списка, сводки карточки, запрос группы,
                                   удаление members[]/members_truncated/CONTEXT_MEMBERS_PAGE_CAP
  services/semantic_rules.py       правка: публичная nearest_working_chapter
  services/context_routing.py      правка: chapter_paths (рекурсивный CTE)
  services/context_operations.py   правка: transfer_stale_group
  routers/semantic.py              правка: GET members, GET member-ids, POST stale-groups/transfer
  tests/unit/test_semantic_rules.py         правка
  tests/integration/test_semantic_api.py    правка
frontend/src/
  components/domain/Pager.tsx      правка: размер страницы и номера страниц — необязательные props
  components/domain/Pager.test.tsx создаётся
  pages/families/labels.ts         создаётся: словарь подписей
  pages/families/SourceChip.tsx    создаётся: плашки «статья СМР» / «в смете»
  pages/families/usePersistedPageSize.ts   создаётся
  pages/families/FamiliesPage.tsx  правка: две вкладки, панель
  pages/families/FamiliesTab.tsx   правка: пагинация, панель правки
  pages/families/ContextsTab.tsx   правка: строка списка, панель карточки
  pages/families/ContextCard.tsx   правка: шапка, строки внимания, вкладки, группы членств
  pages/families/*.test.tsx        правка, плюс labels.test.ts
  services/api/domain.ts, services/queries.ts, types/domain.ts, test/handlers.ts   правка
docs/reference/screens.md          правка: ## 9.
```

## Решения плана, которых нет в спеке

1. **Порядок задач — бэкенд (1–5), затем фронтенд (6–8).** Удаление `members[]`
   из ответа карточки — в Task 8 вместе с переписанной карточкой, а не в Task 2:
   иначе между задачами экран ветки читал бы отсутствующее поле.
2. **`chapter_paths` живёт в `services/context_routing.py`** рядом с
   `chapter_context`, а не в `crud`: та же предметная операция (подъём по разделам)
   и тот же отказ `RoutingError` при цикле.
3. **`nearest_working_chapter` принимает цепочку снизу вверх** — порядок
   `ChapterContext.chain`, тот же, что у `classify_name_role(chapter_chain=...)`.
4. **`Pager` расширяется необязательными props**, а не заменяется: им пользуются
   ещё пять экранов (`ContractsPage`, `MatrixPage`, `ReviewPage`,
   `StandardsPage`, `TendersPage`), и без новых props их вид не меняется.
   Номера страниц — только при `showPageNumbers` (докстрока `Pager` объясняет,
   почему очередь Review их не показывает).
5. **Размер страницы хранится в `localStorage` ключом
   `gca.families.<список>.pageSize`**; недопустимое значение или ошибка доступа —
   умолчание 20.
6. **Эндпоинты группы и пакет — в префиксе `/api/v1/semantic`** (существующий
   `APIRouter`).

## Задачи

### Task 1: строка списка контекстов — число позиций, признаки, путь классификатора

**Files**
- Edit: `backend/crud/semantic.py`
- Test: `backend/tests/integration/test_semantic_api.py`

**Interfaces**
- Потребляет: `_context_query`, `list_contexts`, `_stale_exists`, `_conflict_exists` (существуют).
- Производит:

```python
class WorkCategoryRef(TypedDict):
    code: str
    title: str

# items[] ответа list_contexts получают ключи:
#   member_count: int
#   has_stale_members: bool
#   has_conflicting_members: bool
#   work_category_path: list[WorkCategoryRef]   # родители статьи, от корня, без неё самой
```

**Утверждения**
- у контекста с тремя членствами, одно из которых `STALE`, — `member_count = 3`,
  `has_stale_members = true`, `has_conflicting_members = false`;
- у контекста с членством с непустым `conflict_at` — `has_conflicting_members = true`;
- у пустого контекста — `member_count = 0`, оба признака `false`;
- у статьи третьего уровня (`8.2.3`) `work_category_path` — два элемента, `8`
  затем `8.2`; у статьи первого уровня — пустой список; у корзины без статьи —
  пустой список;
- максимальная глубина `work_categories` в засеянном справочнике `≤ 3` — тест
  называет найденную глубину, если она больше;
- `test_list_contexts_query_count_independent_of_row_count` зелёный без правки
  порога (ровно 2 запроса).

**Имена**
- Заводятся этой задачей: `WorkCategoryRef`.
- Существуют, проверено `grep`-ом: `list_contexts`, `_context_query`, `_stale_exists`, `_conflict_exists`, `test_list_contexts_query_count_independent_of_row_count`.

**Проверка**
- `just test-int-local-k semantic_api` — зелёная; ДО 91, ПОСЛЕ ≥ 96.

### Task 2: пути членств карточки одним рекурсивным CTE и сводки групп

**Files**
- Edit: `backend/services/context_routing.py`, `backend/crud/semantic.py`
- Test: `backend/tests/integration/test_semantic_api.py`

**Interfaces**
- Потребляет: `chapter_context`, `ChapterContext`, `RoutingError` (существуют), `WorkCategoryRef` (Task 1).
- Производит:

```python
def chapter_paths(db: Session, chapter_item_ids: Collection[int]) -> dict[int, tuple[str, ...]]
    # путь СВЕРХУ ВНИЗ для каждого раздела; один запрос; цикл — RoutingError

class MemberPath(TypedDict):
    chapter_item_id: int | None
    path: list[str]
    member_count: int
    stale_count: int
    conflict_count: int

class StaleGroup(TypedDict):
    chapter_item_id: int | None
    path: list[str]
    count: int
    target_category_id: int | None
    target_category_code: str | None
    target_category_title: str | None

# ответ context_card получает ключи:
#   work_category_path: list[WorkCategoryRef]
#   member_paths: list[MemberPath]
#   stale_groups: list[StaleGroup]
```

**Утверждения**
- `chapter_paths` для раздела глубины 4 возвращает 4 названия сверху вниз и
  совпадает с `tuple(reversed(chapter_context(db, позиция).chain))` для позиции
  этого раздела;
- `chapter_paths` на цикле разделов (раздел A → B → A) поднимает `RoutingError`,
  не зависает; карточка такого контекста отвечает доменной ошибкой, а не `500`;
- у контекста с позициями в трёх разделах и одной позицией без раздела —
  `member_paths` из 4 групп, сумма `member_count` групп = `member_count`
  карточки, порядок по `member_count` убыв., группа `chapter_item_id = null` с
  `path = []` — последней при любом её размере;
- `stale_count` и `conflict_count` группы равны числу таких членств в ней;
- после ручного разноса раздела в статью C у его устаревших членств —
  одна запись `stale_groups` с `target_category_id = C` и её кодом и названием;
  членства другого раздела той же карточки в эту запись не попадают;
- **число запросов карточки** у контекста с 1 путём глубины 2 и 2 членствами и у
  контекста с 12 разными путями глубины 4 и 30 членствами — одинаково (тест
  `test_card_members_query_count_independent_of_member_count` переписывается на
  эти два входа).

**Имена**
- Заводятся этой задачей: `chapter_paths`, `MemberPath`, `StaleGroup`.
- Существуют, проверено `grep`-ом: `chapter_context`, `ChapterContext`, `RoutingError`, `context_card`, `test_card_members_query_count_independent_of_member_count`.

**Проверка**
- `just test-int-local-k semantic_api` — зелёная; ДО ≥ 96 (после Task 1), ПОСЛЕ ≥ 102.

### Task 3: работа имени-места от сохранённой роли

**Files**
- Edit: `backend/services/semantic_rules.py`, `backend/crud/semantic.py`
- Test: `backend/tests/unit/test_semantic_rules.py`, `backend/tests/integration/test_semantic_api.py`

**Interfaces**
- Потребляет: `_is_working_chapter`, `classify_name_role` (существуют), `chapter_paths` (Task 2).
- Производит:

```python
def nearest_working_chapter(chain: tuple[str, ...]) -> str | None
    # chain — снизу вверх, как ChapterContext.chain

# ответ context_card получает ключ:
#   representative_work_title: str | None
```

**Утверждения**
- `nearest_working_chapter` возвращает первый снизу раздел, для которого
  `_is_working_chapter` истинно; цепочка из одних мест — `None`; пустая — `None`;
- для каждой цепочки из тестов `classify_name_role`, где роль — `LOCATION_ONLY`
  с рабочим разделом, `nearest_working_chapter(chain)` равен
  `classify_name_role(...).work_title` — одна истина о рабочем разделе;
- карточка: правило поставило `LOCATION_ONLY` под рабочим разделом —
  `representative_work_title` равен названию этого раздела у членства с
  наименьшим `position_item_id`;
- карточка: оператор вручную поставил `LOCATION_ONLY` строке, которую
  классификатор считает `WORK`, — `representative_work_title` = рабочий раздел,
  а не наименование строки;
- карточка: оператор вручную сменил `LOCATION_ONLY` на `WORK` —
  `representative_work_title = None`;
- у пустого контекста — `None`; при роли `LOCATION_ONLY` без рабочего раздела в
  цепочке — `None`;
- число запросов карточки из Task 2 не меняется.

**Имена**
- Заводятся этой задачей: `nearest_working_chapter`.
- Существуют, проверено `grep`-ом: `_is_working_chapter`, `classify_name_role`, `NameRoleOutcome`, `set_name_role`.

**Проверка**
- `just test-unit-k semantic_rules` — зелёная; ДО 258, ПОСЛЕ ≥ 262.
- `just test-int-local-k semantic_api` — зелёная; ДО ≥ 102, ПОСЛЕ ≥ 106.

### Task 4: членства группы — постраничный список и полный набор id

**Files**
- Edit: `backend/crud/semantic.py`, `backend/routers/semantic.py`
- Test: `backend/tests/integration/test_semantic_api.py`

**Interfaces**
- Потребляет: `require_admin`, `MAX_PAGE_SIZE` (существуют), `MemberPath` (Task 2).
- Производит:

```python
GroupState = Literal["all", "stale", "conflict"]

@dataclass(frozen=True)
class GroupSelector:
    chapter_item_id: int | None      # None вместе с no_chapter=False — весь контекст
    no_chapter: bool

def list_group_members(db: Session, *, context_id: int, selector: GroupSelector,
                       state: GroupState, limit: int, offset: int) -> dict
    # {items: [...], total, limit, offset}; строка — та же форма, что была у members[]

def list_group_member_ids(db: Session, *, context_id: int, selector: GroupSelector,
                          state: GroupState) -> dict
    # {position_item_ids: list[int], total: int}

# маршруты:
# GET /api/v1/semantic/contexts/{context_id}/members
# GET /api/v1/semantic/contexts/{context_id}/member-ids
#   query: chapter_item_id: int | None, no_chapter: bool = False,
#          state: GroupState = "all", limit, offset (только у members)
```

**Утверждения**
- `chapter_item_id = X` отдаёт только позиции раздела X; `no_chapter=true` — только
  позиции без раздела; без обоих — все позиции контекста;
- `chapter_item_id` и `no_chapter=true` вместе — `422`;
- `state=stale` и `state=conflict` фильтруют по `membership_state = STALE` и по
  `conflict_at IS NOT NULL` соответственно;
- `member-ids` у группы из 520 позиций отдаёт 520 id и `total = 520`, без обрезки;
- `members` постранично: `limit=100, offset=500` на группе из 520 даёт 20 строк,
  `total = 520`; порядок — `position_item_id`;
- несуществующий контекст — `404` у обоих; `member` (не `admin`) — `403`;
- оба маршрута — ровно 2 запроса на группах из 5 и из 520 позиций.

**Имена**
- Заводятся этой задачей: `GroupState`, `GroupSelector`, `list_group_members`, `list_group_member_ids`.
- Существуют, проверено `grep`-ом: `require_admin`, `MAX_PAGE_SIZE`, `router`.

**Проверка**
- `just test-int-local-k semantic_api` — зелёная; ДО ≥ 106, ПОСЛЕ ≥ 114.

### Task 5: пакетный перенос устаревшей группы

**Files**
- Edit: `backend/services/context_operations.py`, `backend/routers/semantic.py`
- Test: `backend/tests/integration/test_semantic_api.py`

**Interfaces**
- Потребляет: `accept_transfer`, `ContextOperationError`, `lock_buckets` (существуют), `GroupSelector` (Task 4).
- Производит:

```python
@dataclass(frozen=True)
class StaleTransferItem:
    position_item_id: int
    outcome: Literal["moved", "refused"]
    target_context_id: int | None
    error_code: str | None
    message: str | None

@dataclass(frozen=True)
class StaleGroupTransferResult:
    results: tuple[StaleTransferItem, ...]
    moved: int
    refused: int

def transfer_stale_group(db: Session, *, context_id: int, chapter_item_id: int | None,
                         expected_category_id: int | None, actor_id: int) -> StaleGroupTransferResult

class StaleGroupTransferRequest(BaseModel):
    chapter_item_id: int | None
    expected_category_id: int | None

# POST /api/v1/semantic/contexts/{context_id}/stale-groups/transfer
```

**Утверждения**
- группа из трёх устаревших позиций, у всех статья раздела = `expected_category_id`
  — `moved = 3`, `refused = 0`, все три в одном целевом контексте, событий
  `members_moved` три;
- у одной из трёх статья раздела после показа строки внимания сменилась
  (`fresh_effective != expected_category_id`) — `moved = 2`, `refused = 1`,
  отказ несёт `error_code` и текст доменной ошибки `accept_transfer`, две другие
  перенесены и закоммичены;
- группа, где устаревших позиций нет, — `200`, `results = []`, `moved = 0`;
- `chapter_item_id = null` переносит только устаревшие позиции без раздела;
- позиции `CURRENT` и конфликтные той же группы не трогаются;
- обход — по возрастанию `position_item_id`: `results` упорядочены так же;
- `member` — `403`; несуществующий контекст — `404`.

**Имена**
- Заводятся этой задачей: `StaleTransferItem`, `StaleGroupTransferResult`, `transfer_stale_group`, `StaleGroupTransferRequest`.
- Существуют, проверено `grep`-ом: `accept_transfer`, `ContextOperationError`, `lock_buckets`, `raise_domain_error`, `_mutating`.

**Проверка**
- `just test-int-local-k semantic_api` — зелёная; ДО ≥ 114, ПОСЛЕ ≥ 121.

### Task 6: фронтенд — словарь подписей, плашки, пагинация

**Files**
- Create: `frontend/src/pages/families/labels.ts`, `frontend/src/pages/families/labels.test.ts`,
  `frontend/src/pages/families/SourceChip.tsx`, `frontend/src/pages/families/usePersistedPageSize.ts`,
  `frontend/src/components/domain/Pager.test.tsx`,
  `backend/tests/unit/test_event_labels_sync.py`
- Edit: `frontend/src/components/domain/Pager.tsx`

**Interfaces**
- Потребляет: `NameRole`, `SemanticKind`, `SemanticState`, `DecisionSource`, `FamilySource`, `ComparabilityReason` (существуют, `types/domain.ts`).
- Производит:

```ts
export const NAME_ROLE_LABEL: Record<NameRole, string>;
export const SEMANTIC_KIND_LABEL: Record<SemanticKind, string>;
export const SEMANTIC_STATE_LABEL: Record<SemanticState, string>;
export const DECISION_SOURCE_LABEL: Record<DecisionSource, string>;
export const FAMILY_SOURCE_LABEL: Record<FamilySource, string>;
export const CATEGORY_SOURCE_LABEL: Record<"file" | "manual", string>;
export function comparabilityLabel(reason: ComparabilityReason | null): string;
export type SemanticEventType =   // закрытый список фичи 1, 15 значений (models.py)
  | "context_created" | "context_split" | "context_merged" | "members_moved"
  | "members_marked_stale" | "kind_set" | "name_role_set" | "context_family_assigned"
  | "context_archived" | "routing_rules_dropped" | "family_created" | "family_updated"
  | "family_activated" | "family_archived" | "family_merged";
export const EVENT_LABEL: Record<SemanticEventType, string>;
export function eventLabel(eventType: string): string;   // неизвестный тип — сам код

export function SourceChip(props: { kind: "classifier" | "estimate" }): JSX.Element;

export function usePersistedPageSize(key: string, fallback: number): [number, (n: number) => void];

interface PagerProps {           // новые — необязательные
  onPageSizeChange?: (n: number) => void;
  pageSizeOptions?: readonly number[];   // по умолчанию [10, 20, 50, 100]
  showPageNumbers?: boolean;
}
```

**Утверждения**
- каждое значение каждого типа из таблицы спеки §2.2 имеет непустую подпись, и ни
  одна подпись не равна коду — тест перебирает значения типов, а не выборку;
- `comparabilityLabel(null)` — «да», `comparabilityLabel("insufficient_description")`
  — «нет — сравнение ставок не производится»;
- `EVENT_LABEL` покрывает все 15 событий закрытого списка фичи 1 — список
  сверяется с `SEMANTIC_EVENT_TYPES` бэкенда (`models.py`) тестом бэкенда, который
  читает `labels.ts` и сравнивает множества; `eventLabel` на неизвестном типе
  возвращает сам код, не падает;
- `SourceChip` печатает «статья СМР» и «в смете» и несёт подсказку-пояснение;
- `Pager` без новых props — только «назад / стр. N из M / вперёд»: ни выбора
  размера, ни номеров страниц (так остаются пять прочих экранов);
- `Pager` с `showPageNumbers` на 5 страницах и текущей 1 показывает 1, 2, …, 5;
  на текущей 3 — 1, 2, 3, 4, 5; на 12 страницах и текущей 6 — 1, …, 5, 6, 7, …, 12;
- выбор размера вызывает `onPageSizeChange` с числом из `pageSizeOptions`;
- `usePersistedPageSize` возвращает сохранённое значение из `pageSizeOptions`,
  при отсутствии, недопустимом значении или исключении `localStorage` — `fallback`.

**Имена**
- Заводятся этой задачей: все перечисленные в Interfaces, включая `SemanticEventType`, `eventLabel`.
- Существуют, проверено `grep`-ом: `SEMANTIC_EVENT_TYPES` (`backend/models.py`), `Pager`, `PagerProps`, `NameRole`, `SemanticKind`, `SemanticState`, `DecisionSource`, `FamilySource`, `ComparabilityReason`.

**Проверка**
- `cd frontend && npx vitest run src/pages/families src/components/domain` — зелёная; ДО 43, ПОСЛЕ ≥ 55.
- `just test-unit-k event_labels` — зелёная; ДО 0, ПОСЛЕ ≥ 1.

### Task 7: фронтенд — раскладка, список контекстов, семьи

**Files**
- Edit: `frontend/src/pages/families/FamiliesPage.tsx`, `ContextsTab.tsx`, `FamiliesTab.tsx`,
  `frontend/src/types/domain.ts` (`ContextRow`), `frontend/src/test/handlers.ts`
- Test: `FamiliesPage.test.tsx`, `ContextsTab.test.tsx`, `FamiliesTab.test.tsx`

**Interfaces**
- Потребляет: `labels.ts`, `SourceChip`, `usePersistedPageSize`, `Pager` с новыми props (Task 6); поля строки списка (Task 1).
- Производит: `ContextRow` получает `member_count`, `has_stale_members`, `has_conflicting_members`, `work_category_path`.

**Утверждения**
- на странице две вкладки, «Семьи» и «Контексты»; вкладки «Операции» нет;
- щелчок по строке контекста показывает карточку справа от списка, активная
  вкладка остаётся «Контексты»; смена страницы списка не снимает выбор, если
  выбранный контекст есть на новой странице, и снимает, если нет;
- строка контекста — наименование, «статья СМР» + код и название, подсказка с
  путём классификатора, единица, семья, число позиций; точка — при
  `has_stale_members`, `has_conflicting_members` или `member_count = 0`, и её нет
  у контекста без этих признаков;
- «Контексты»: выбор 10/20/50/100 передаёт `limit` в запрос и ставит `offset = 0`;
  смена фильтра — `offset = 0`;
- «Семьи»: пагинация на фронтенде — 43 семьи при размере 10 дают 5 страниц,
  третья — строки 21–30; смена фильтра — первая страница;
- каждый сценарий операции, который проверяли тесты фичи 1 в этих трёх файлах,
  сохраняется (меняется путь к кнопке, не сценарий);
- на экране нет кодов `LOCATION_ONLY`, `GENERIC_WORK`, `SUGGESTED`, `CONFIRMED`,
  `NOT_APPLICABLE` вне подсказок.

**Имена**
- Заводятся этой задачей: поля `ContextRow` из Interfaces.
- Существуют, проверено `grep`-ом: `FamiliesPage`, `ContextsTab`, `FamiliesTab`, `ContextRow`, `useSemanticContexts`, `useWorkFamilies`.

**Проверка**
- `cd frontend && npx vitest run src/pages/families src/components/domain` — зелёная; ДО ≥ 55, ПОСЛЕ ≥ 62.

### Task 8: фронтенд — карточка контекста; удаление `members[]`; справочник экранов

**Files**
- Edit: `frontend/src/pages/families/ContextCard.tsx`, `frontend/src/types/domain.ts`
  (`ContextCardData`, удаление `ContextMemberRow` из карточки), `frontend/src/services/api/domain.ts`,
  `frontend/src/services/queries.ts`, `frontend/src/test/handlers.ts`,
  `backend/crud/semantic.py` (удаление `members`, `members_truncated`, `CONTEXT_MEMBERS_PAGE_CAP`),
  `docs/reference/screens.md` (`## 9.`)
- Test: `ContextCard.test.tsx`, `backend/tests/integration/test_semantic_api.py`

**Interfaces**
- Потребляет: поля карточки (Tasks 2–3), маршруты группы (Task 4) и пакета (Task 5), `labels.ts`, `SourceChip` (Task 6), `useAcceptStaleTransfer`, `useAcceptTargetDecision`, `useSplitContext`, `useMoveMembers` (существуют).
- Производит:

```ts
semanticApi.groupMembers(contextId: number, selector: GroupSelector, state: GroupState,
                         limit: number, offset: number): Promise<GroupMembersPage>;
semanticApi.groupMemberIds(contextId: number, selector: GroupSelector,
                           state: GroupState): Promise<{ position_item_ids: number[]; total: number }>;
semanticApi.transferStaleGroup(contextId: number, input: { chapter_item_id: number | null;
                               expected_category_id: number | null }): Promise<StaleGroupTransferResult>;
export function useContextGroupMembers(contextId: number | null, selector: GroupSelector,
                                       state: GroupState, page: number, pageSize: number);
export function useTransferStaleGroup();
```

**Утверждения**
- шапка: наименование и единица; «статья СМР» + код и название; «внутри: …» из
  `work_category_path`; «ручной разнос» при `work_category_source = manual`;
- строки внимания: по одной на каждую запись `stale_groups` с разделом, целевой
  статьёй и числом; одна при конфликтных членствах; одна при пустом контексте; ни
  одной у контекста без этих состояний;
- «Перенести их» вызывает `transferStaleGroup` один раз с `chapter_item_id` и
  `expected_category_id` своей записи; ответ `moved = 2, refused = 1` печатает
  «перенесено 2 из 3» и текст отказа;
- вкладки карточки — «Решения», «Членства N», «Журнал»; «Решения» у
  `LOCATION_ONLY` печатает «работа по разделу представительной позиции» и
  `representative_work_title`, у прочих ролей этой строки нет;
- «Членства»: группы из `member_paths` в порядке ответа, группа `null` —
  «без раздела»; раскрытие группы грузит её страницу `groupMembers`;
- галочка группы из 520 позиций при загруженных 20 выбирает 520 id (вызов
  `groupMemberIds`), счётчик — «выбрано 520»; «Разделить…» и «Перенести…»
  отправляют эти 520 id;
- «Журнал» печатает события словами из `EVENT_LABEL`, код — в подсказке;
- ответ `GET /contexts/{id}` не содержит `members` и `members_truncated`;
  `CONTEXT_MEMBERS_PAGE_CAP` в `backend/` не встречается; три теста обрезки
  (`monkeypatch.setattr(crud_semantic, "CONTEXT_MEMBERS_PAGE_CAP", ...)`)
  удалены, и `grep -c CONTEXT_MEMBERS_PAGE_CAP backend/tests` = 0;
- каждый сценарий операции из тестов фичи 1 в `ContextCard.test.tsx` сохраняется;
- `screens.md` `## 9.` описывает две вкладки, карточку панелью, строки внимания,
  группы членств, плашки источника, пагинацию; страж
  `check_agents_index.py` зелёный.

**Имена**
- Заводятся этой задачей: `groupMembers`, `groupMemberIds`, `transferStaleGroup`, `useContextGroupMembers`, `useTransferStaleGroup`, `GroupMembersPage` (тип), `GroupSelector`/`GroupState` (фронтенд-типы тех же имён, что в Task 4).
- Существуют, проверено `grep`-ом: `ContextCard`, `ContextCardData`, `ContextMemberRow`, `useAcceptStaleTransfer`, `useAcceptTargetDecision`, `useSplitContext`, `useMoveMembers`, `CONTEXT_MEMBERS_PAGE_CAP`, `EXPECTED_SCREEN_ANCHORS`.

**Проверка**
- `cd frontend && npx vitest run src/pages/families src/components/domain` — зелёная; ДО ≥ 62, ПОСЛЕ ≥ 72.
- `just test-int-local-k semantic_api` — зелёная; ДО ≥ 121, ПОСЛЕ = ДО − 3 (удалены три теста обрезки) + ≥ 1.
- `cd backend && uv run python scripts/check_agents_index.py` — зелёная (страж 18/18).

## Команды проверки

- По задаче: указаны в самой задаче.
- По фиче целиком: `just ci` (§9.3).
- Живой экран на стенде, скриптом через системный Chrome (`playwright-core`,
  `channel: "chrome"`): выбор контекста без смены вкладки; «Перенести их» у
  «Шпатлевки в 2 слоя» × 8.2.3 после ручного разноса раздела «SHELL & CORE»;
  галочка группы больше 20 позиций; смена размера страницы в обоих списках.
