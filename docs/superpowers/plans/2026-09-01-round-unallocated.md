# Этапный разнос «Нераспределённого» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **УСТАРЕЛО В ЧАСТИ ОТБОРА РАЗДЕЛОВ (02.09.2026).** План закрыт целиком, но
> писался до правки спеки по замечанию пользователя, и **модель отбора у него
> прежняя.** Действующее правило — [спека](../specs/2026-09-01-round-unallocated-design.md)
> §2.2 «Достижимость» и §2.3; разбор — [devlog](../../devlog/2026-09-01-round-unallocated.md)
> §9б. Коротко, что здесь читать как неверное:
>
> - раздел попадает во множество не при ЛЮБОМ отсутствии статьи, а только когда
>   решение на нём достигает хотя бы одной строки (свёртка по наследующей части
>   поддерева, правило Ф3) — либо когда на нём уже стоит override;
> - `rows` у `sections[]` — ДОСТИЖИМЫЕ строки, а не полный размер файлового
>   поддерева; полный остаётся только у `manual[]`.
>
> Ниже это задето в шести местах, каждое помечено `⚠ УСТАРЕЛО` по месту:
> Global Constraints (два пункта), `ChapterNode.rows`, юнит-тест
> `test_rows_is_the_node_rows_regardless_of_vectors`, тип
> `RoundUnallocatedSectionBase.rows`, `PENDING_HINT`.
>
> Текст плана НЕ переписан задним числом намеренно: план — артефакт гейта 3, и
> расхождения с ним живут в devlog (§3 — девять отступлений, §9б — эта правка),
> а не правкой истории. Врезка стоит здесь потому, что пересказ правила и есть
> место, где дефект однажды уже завёлся
> ([инсайт](../../insights/parity-with-the-existing-surface.md)), — молча
> оставленный устаревший пересказ завёл бы его снова.

**Goal:** дать аналитику разнести разделы без статьи в сметах ПРЕДЛОЖЕНИЙ
тендера — одним решением на логический раздел раунда, применяемым ко всем
offer-сметам раунда разом, — и тем оживить подпись `manual_overrides` свода
(закрывает `docs/TECH_DEBT.md` №22).

**Architecture:** носитель решения — раунд; ключ раздела —
`(lot_key, position_key_in_proposal)`, одинаковый во всех проекциях одного
файла. Чистый агрегатор состояний (`services/round_unallocated.py`, без
`Session`) классифицирует раздел по ПОЛНОМУ вектору решений всех offer-смет;
чтение (`crud/round_unallocated.py`) собирает вход агрегатора постоянным числом
запросов и строит ответ `GET …/rounds/{round_id}/unallocated`, тем же
агрегатором считается счётчик карточки. Запись
(`services/round_category_override.py`) под блокировками tender → round →
сметы по `estimate_id ASC` разрешает ключ во ВСЕХ сметах, проверяет предикат
no-op и атомарно пишет единое решение с единым аудитом, а пересчёт зовёт уже
существующим `apply_overrides` по-сметного сервиса. На фронте из
`UnallocatedPanel` выделяется презентационное ядро `UnallocatedWorkbench`;
тендер получает `Sheet`-верстак, триггер в заголовке этапа решётки,
URL-контракт `?unallocated=<round_id>` и ссылку из строки «Нераспределённое»
свода. Миграций нет: решения хранятся в существующей
`estimate_category_overrides`.

**Tech Stack:** FastAPI + SQLAlchemy 2.x + pytest (маркер `integration`,
xdist); React + TanStack Query + shadcn/ui + vitest + msw.

**Spec:** `docs/superpowers/specs/2026-09-01-round-unallocated-design.md`
(гейт 2 закрыт 01.09.2026, семь кругов внешнего ревью). Тексты экрана — из
макета `docs/superpowers/specs/2026-09-01-offer-unallocated-mockup.html`.
**Ветка:** `feat/round-unallocated` от `main 8673a2b` — создана; первым
коммитом (`d605ea0`) в ней лежат спека и макет. Этот план — второй коммит.

## Global Constraints

Каждая строка — дословное требование спеки; задачи ниже её не пересказывают, а
исполняют.

- **Носитель разноса — раунд; единица — логический раздел
  `(lot_key, position_key_in_proposal)`** (§2.1). Никаких соединений разделов
  по номеру: ключ точный, не эвристика (§1.6).
- **Радиус — все существующие offer-сметы раунда; baseline исключён на уровне
  ВЫБОРКИ** — `Estimate` берётся соединением с `Offer` по `Offer.round_id`,
  не фильтром в цикле (§2.1). Доступность разноса — наличие offer-смет, НЕ
  `current_round_job` (§1.5).
- **Входное множество агрегатора** — логические разделы, у которых хотя бы в
  одной offer-смете эффективная статья отсутствует ИЛИ существует хотя бы один
  override (§2.2). Раздел со статьёй из файла и без решений в ответ не попадает.
  **⚠ УСТАРЕЛО** (см. врезку): первый дизъюнкт требует ещё и ненулевой
  достижимости.
- **Четыре состояния по ПОЛНОМУ вектору
  `(work_category_id, note, assigned_by, assigned_at)`**: `unassigned`
  (override ни в одной), `partial` (не во всех), `conflict` (во всех, векторы
  различаются — статьи, заметки ИЛИ аудит), `resolved` (один вектор во всех).
  Разбиение исчерпывающее и непересекающееся (§2.2).
- **Структура дерева — из представительной сметы** (первая по
  `estimate_id ASC`); ключ, отсутствующий хотя бы в одной offer-смете, либо
  различающийся `is_chapter` — `mapping_broken`: 500 с логом, не 409 (§2.2, §2.4).
- **`rows` — ПОЛНЫЙ размер файлового поддерева** (число позиций), стабилен и не
  зависит от вложенных решений; `rows_priced` НЕ отдаётся (§2.3).
  **⚠ УСТАРЕЛО** (см. врезку): так теперь только у `manual[]`; у `sections[]`
  `rows` — достижимые строки.
- **Денег в ответе GET и в верстаке нет** (§2.3, §3 п.3).
- **404-коды GET:** `tender_not_found`, `round_not_found` (раунд ищется
  парой), `round_has_no_offer_estimates` с текстом «Раунд или его сметы больше
  недоступны» (§2.3). Права — аутентификация; `member` вправе (§2.3, §2.4).
- **Раундовые PUT/DELETE:** ключ — в теле; `note` — ОБЯЗАТЕЛЬНОЕ nullable-поле,
  семантики «omitted» нет (§2.4). Таблица исходов разрешения ключа (§2.4 п.2)
  применяется и к PUT, и к DELETE ДО предиката no-op: во всех сметах нет →
  `section_not_found` 404; в части смет → `mapping_broken` 500; во всех, но не
  разделы → `not_a_chapter` 422; `is_chapter` различается → `mapping_broken`
  500; `work_category_id` нет в классификаторе → `category_not_found` 404;
  структура погашена → `structure_disabled` 409.
- **Предикат no-op PUT — ДВА условия:** все текущие векторы одинаковы между
  собой И их `(work_category_id, note)` совпадают с телом (§2.4 п.3). No-op
  DELETE — у разрешённого раздела override нет ни в одной смете. No-op не
  трогает ни решений, ни аудита.
- **Иначе — атомарная перезапись всех смет:** единое решение И единый аудит
  (`assigned_by` = автор запроса, `assigned_at` = `now()` ОДНОЙ транзакции)
  в каждой offer-смете; `set_override` не переиспользуется (его no-op сохранил
  бы старых авторов, §1.3); пересчёт — `apply_overrides` по-сметного сервиса
  (§2.4 п.4). Либо во всех сметах, либо ни в одной (§2.4 п.5).
- **Блокировки в объявленном порядке: tender → round → offer-сметы по
  `estimate_id ASC`** — один тотальный порядок у обоих раундовых писателей
  (§2.4 п.1).
- **Диагностика — три кода своим селектором** (`outside_structure`,
  `structure_disabled`, `unresolved_chapter_ref`), тем же планом резолва, что
  пересчёт (`categories_by_chapter_number`/`resolve_ref`); допработа с
  разрешимой ссылкой в диагностику НЕ попадает; `unallocated.extras` паспорта
  не переиспользуется (§2.5, §1.4). Кнопок у диагностики нет.
- **Счётчик карточки `unallocated_pending_sections: int | null`** — ТЕМ ЖЕ
  агрегатором, что GET; `null` ⟺ у раунда нет offer-смет; иначе число разделов
  в `unassigned + partial + conflict`. Существующие поля карточки — посимвольно
  те же (§2.6).
- **Экран:** триггер в заголовке этапа решётки отдельным элементом
  (ячейки-`Toggle` не трогаются); Sheet справа; ленивый GET при первом
  открытии; свои loading / error / 404; URL `?unallocated=<round_id>` —
  открытие пишет, закрытие удаляет ТОЛЬКО этот параметр; чужой или устаревший
  id не запускает запрос вовсе (§2.7).
- **Хуки:** свои `useSetRoundCategoryOverride` / `useClearRoundCategoryOverride`;
  успех инвалидирует `qk.tenders.card(tenderId)`,
  `qk.tenders.stageSummaryForTender(tenderId)`,
  `qk.tenders.stagePositionsForTender(tenderId)` и ключ GET §2.3; перекрёстных
  инвалидаций договорного контура НЕТ (§2.7).
- **По-сметные маршруты, сервис, панель и хуки паспорта — без изменений;
  их тесты остаются зелёными без правок** (§3 п.5).
- **Свод:** «разнести →» у ячейки строки «Нераспределённое» при
  `rows.row_count > 0` (в том числе у колонки с неизвестной базой НДС), ссылка
  на `/tenders/{tenderId}?unallocated=<columns[i].round_id>` (§2.8).
- Бэкенд: `uv run ruff check`, `uv run pytest` (интеграция —
  `TEST_DATABASE_URL`, маркер `integration`). Фронт: `npm run lint` по ВСЕМУ
  фронтенду, `npx tsc -b`, `npm test`. Перед пушем — `just ci`, НЕ в
  конвейере. Кириллица дочернего python — `PYTHONIOENCODING=utf-8`.
- **Негативные проверки снятием защиты исполняет оркестратор лично**, не
  субагент (`docs/insights/verifying-guards.md`); вход негативного теста
  нарушает ровно одно ограничение.
- Только shadcn/ui для примитивов (`Sheet`, `Popover`, `Command`, `Textarea`
  уже установлены в `frontend/src/components/ui/`). Наименования разделов —
  `line-clamp-2` без класса `block` (`AGENTS.md` §11).

### Решения плана, которых нет в спеке (на утверждение гейта 3)

1. **Ответ раундовых PUT/DELETE** — те же три ключа, что у по-сметного
   маршрута (`chapters_updated`, `additional_works_updated`,
   `chapters_manual`), СУММОЙ по offer-сметам раунда: фронт переиспользует тип
   `CategoryOverrideChangeSummary`, второй формы ответа не заводится.
2. **Гранулярность диагностики:** `outside_structure` и `structure_disabled`
   — одной записью на предложение (`title` = название лота, `rows` = число
   строк-позиций за границей); `unresolved_chapter_ref` — по строке допработ
   (`title` = наименование строки, `rows` = 1). Иначе `rows` у записи не имело
   бы смысла, а сотня сиротливых позиций дала бы сотню записей.
3. **`partial`/`conflict` в JSON присутствуют только у своего состояния**
   (буквально §2.3); на фронте это дискриминированный union по `state`.
4. **`mapping_broken` при расчёте счётчика карточки роняет карточку в 500 с
   логом**, как и GET: расхождение проекций — порча наших данных, и молчание
   на карточке скрыло бы её; сегодня недостижимо по построению
   (`split_round_payload` — `deepcopy` одного JSON).
5. **Выделение «общей apply-части» сводится к публичности двух точек входа
   по-сметного сервиса:** `apply_overrides(db, estimate_id, already_locked=True)`
   уже публичен и пригоден как есть; `_lock_estimate` становится
   `lock_estimate`. Ничего не переносится в новый модуль — переносить нечего.
6. **Переподвешивание родителей внутри входного множества** — тем же
   предикатом, что у `_unallocated_sections` паспорта (узел со своим
   `smr_article_raw` — всегда корень своего кусочка): решение на предке до него
   не дойдёт, и вложенность обещала бы неправду.
7. **Пометка конфликта «различаются заметки»** — в макете нет (там статьи и
   аудит); текст задан в задаче 11 по образцу соседних.
8. **Ссылка «разнести →» — render-prop `allocateLink` у `StageSummaryTable`**,
   а не `Link` внутри таблицы: тесты таблицы рендерят её без роутера, а
   `Link` вне `<Router>` падает; страница передаёт настоящий `Link`, таблица
   решает только УСЛОВИЕ показа.

---

## Структура файлов

| Файл | Ответственность |
|---|---|
| `backend/services/round_unallocated.py` (C) | чистый агрегатор: `Vector`, `classify`, входное множество, переподвешивание, файловый порядок — без `Session` |
| `backend/crud/round_unallocated.py` (C) | чтение раунда с кодами 404, offer-сметы, вход агрегатора из БД, `mapping_broken`, диагностика, тело GET, `pending_sections_count` |
| `backend/services/round_category_override.py` (C) | раундовые PUT/DELETE: блокировки, разрешение ключа, no-op, атомарная запись, пересчёт через `apply_overrides` |
| `backend/services/category_override.py` (M) | `_lock_estimate` → `lock_estimate`; докстроки о втором потребителе; поведение не меняется |
| `backend/services/additional_works.py` (M) | три причины `resolve_ref` — именованные константы (поведение прежнее) |
| `backend/crud/tenders.py` (M) | `unallocated_pending_sections` в `rounds[]` карточки |
| `backend/routers/tenders.py` (M) | `GET …/unallocated`, `PUT`/`DELETE …/category-overrides`, транзакция и перевод отказов |
| `frontend/src/types/domain.ts` (M) | типы ответа GET, входов мутаций, поле раунда карточки |
| `frontend/src/services/api/domain.ts` (M) | `tendersApi.roundUnallocated / setRoundCategoryOverride / clearRoundCategoryOverride` |
| `frontend/src/services/queryKeys.ts` (M) | `qk.tenders.roundUnallocated` |
| `frontend/src/services/queries.ts` (M) | `useRoundUnallocated`, `useSetRoundCategoryOverride`, `useClearRoundCategoryOverride` |
| `frontend/src/components/unallocated/UnallocatedWorkbench.tsx` (C) | презентационное ядро: дерево по родительским ключам, блоки, слоты правой колонки и пометок |
| `frontend/src/components/unallocated/CategoryPicker.tsx` (C) | `Command` в `Popover` + необязательное поле «Заметка» (выделено из панели паспорта) |
| `frontend/src/components/unallocated/roundUnallocatedCopy.ts` (C) | тексты Sheet, пометок, диагностики, склонения |
| `frontend/src/pages/passport/UnallocatedPanel.tsx` (M) | обёртка над ядром; имя, пропсы, testid, тексты, сортировка — прежние |
| `frontend/src/components/tenders/UnallocatedSheet.tsx` (C) | Sheet-верстак раунда: ленивый GET, состояния, блоки, диагностика |
| `frontend/src/components/tenders/OfferGrid.tsx` (M) | триггер в заголовке этапа |
| `frontend/src/pages/tenders/TenderCardPage.tsx` (M) | URL-контракт `?unallocated=`, монтаж Sheet |
| `frontend/src/pages/tenders/summary/SummaryCell.tsx` (M) | проп `extra` у `SummaryCell` (как у `SummaryTotalCell`) |
| `frontend/src/pages/tenders/summary/StageSummaryTable.tsx` (M) | условие «разнести →» и render-prop `allocateLink` |
| `frontend/src/pages/tenders/summary/StageSummaryPage.tsx` (M) | передаёт `Link` в `allocateLink` |
| `frontend/src/test/fixtures.ts`, `handlers.ts` (M) | `sampleRoundUnallocated`, хендлеры трёх маршрутов, поле раунда в `sampleTenderCard` |

Тесты: `backend/tests/unit/test_round_unallocated.py`,
`backend/tests/integration/test_round_unallocated_api.py`,
`backend/tests/integration/test_round_category_override.py`,
`backend/tests/integration/test_round_category_override_concurrency.py`;
`frontend/src/components/unallocated/CategoryPicker.test.tsx`,
`frontend/src/components/tenders/UnallocatedSheet.test.tsx`, правки в
`queries.tenders.test.tsx`, `TenderCardPage.test.tsx`, `StageSummaryTable.test.tsx`,
`StageSummaryPage.test.tsx`. `UnallocatedPanel.test.tsx` — без правок.

**Общая фикстура интеграционных тестов** — `round_scene` в
`backend/tests/integration/conftest.py` (задача 2): тендер с двумя раундами,
построенными НАСТОЯЩИМ `import_round` (иначе `apply_overrides` уронит
биекцию `raw_data` ↔ строки, как объяснено у `unallocated_tree`). Раунд 1 —
три участника и baseline; ведомость каждого:

```
1     «Раздел 1»               article_smr="6"   ← статья из файла
2       работа под «1»                          100.00
14    «SHELL & CORE»           без статьи        ← вершина разноса
14.1    «Подраздел 14.1»       без статьи
3       работа под «14.1»                        30.00
4       работа под «14.1»                        30.00
14.3    «Подраздел 14.3»       без статьи
5       работа под «14.3»                        20.00
15    «Рабочая документация»   без статьи
6       работа под «15»                          10.00
допработы: агрегат 30.00; «Сведения»: «14 Отделка - 24.00 руб.»
          → строка со ссылкой «14» (кандидат без статьи — закроется разносом)
          → остаток 6.00 без ссылки (chapter_ref_raw IS NULL — граница §5.5)
```

Раунд 2 — один участник, ведомость `estimate_with_broken_numbering`
(`chapter_number="прим."`) — `structure_disabled` у всего предложения.
`rows` вершины «14» = 3 (позиции 3, 4, 5), «14.1» = 2, «14.3» = 1, «15» = 1.

---

### Task 1: Чистый агрегатор состояний (`services/round_unallocated.py`)

Без `Session` и ORM — как `services/stage_summary.py`. Здесь живёт всё, что
спека §2.2 говорит о состояниях, входном множестве и порядке; чтение из БД
(задача 2) только собирает вход.

**Files:**
- Create: `backend/services/round_unallocated.py`
- Test: `backend/tests/unit/test_round_unallocated.py`

**Interfaces:**
- Consumes: ничего из проекта (литералы на входе и выходе).
- Produces:

```python
STATE_UNASSIGNED = "unassigned"
STATE_PARTIAL = "partial"
STATE_CONFLICT = "conflict"
STATE_RESOLVED = "resolved"
PENDING_STATES = (STATE_UNASSIGNED, STATE_PARTIAL, STATE_CONFLICT)

SectionKey = tuple[str, str]          # (lot_key, position_key_in_proposal)

@dataclass(frozen=True)
class Vector:
    """Решение одной сметы по разделу — ПОЛНЫЙ вектор §2.2."""
    work_category_id: int
    note: str | None
    assigned_by: int
    assigned_at: datetime

@dataclass(frozen=True)
class Classification:
    state: str
    assigned: int                        # смет с решением
    total: int                           # offer-смет в радиусе
    categories: tuple[int, ...]          # РАЗЛИЧНЫЕ статьи решений, порядок первого появления
    notes: tuple[str | None, ...]        # РАЗЛИЧНЫЕ заметки решений, тот же порядок
    audit_differs: bool                  # (assigned_by, assigned_at) различаются

@dataclass(frozen=True)
class ChapterNode:
    """Логический раздел по представительной смете."""
    key: SectionKey
    file_parent: SectionKey | None       # родитель по структуре файла
    number: str | None
    title: str
    smr_article_raw: str | None
    rows: int                            # полный размер файлового поддерева
    # ⚠ УСТАРЕЛО (см. врезку): рядом появилось own_rows — прямые строки узла,
    # вход свёртки достижимости; достижимые идут в SectionAggregate.

@dataclass(frozen=True)
class SectionAggregate:
    node: ChapterNode
    parent_key: SectionKey | None        # переподвешенный родитель внутри входного множества
    depth: int                           # глубина от переподвешенного корня
    classification: Classification
    vectors: tuple[Vector | None, ...]   # по сметам, порядок estimate_id ASC

def classify(vectors: Sequence[Vector | None]) -> Classification
def input_set(nodes: Sequence[ChapterNode], missing_article: Mapping[SectionKey, bool],
              vectors: Mapping[SectionKey, Sequence[Vector | None]]) -> list[ChapterNode]
def aggregate(nodes: Sequence[ChapterNode], missing_article: Mapping[SectionKey, bool],
              vectors: Mapping[SectionKey, Sequence[Vector | None]]) -> list[SectionAggregate]
```

`nodes` — ВСЕ разделы представительной сметы в ФАЙЛОВОМ порядке (не только
входное множество: переподвешивание идёт по полной цепочке `file_parent`, и
предок вне множества — законный промежуток цепочки); `aggregate` порядок
сохраняет, отдельной сортировки в чистом модуле нет. Переподвешивание —
внутри `aggregate`, отдельной публичной функции нет: второй копии закона не
заводится.

- [ ] **Step 1: Написать падающие тесты**

`backend/tests/unit/test_round_unallocated.py`:

```python
"""Чистый агрегатор этапного разноса (спека 2026-09-01-round-unallocated-design.md §2.2).
Без БД: литералы на входе и выходе."""
from __future__ import annotations

import datetime as dt
import itertools

import pytest

from services import round_unallocated as ru

T1 = dt.datetime(2026, 8, 29, 10, 0, tzinfo=dt.UTC)
T2 = dt.datetime(2026, 8, 30, 10, 0, tzinfo=dt.UTC)


def vec(category=20, note=None, by=1, at=T1) -> ru.Vector:
    return ru.Vector(work_category_id=category, note=note, assigned_by=by, assigned_at=at)


class TestClassify:
    def test_no_decisions_is_unassigned(self):
        c = ru.classify([None, None, None])
        assert (c.state, c.assigned, c.total) == (ru.STATE_UNASSIGNED, 0, 3)

    def test_some_decisions_is_partial_with_distinct_notes(self):
        c = ru.classify([vec(note="а"), None, vec(note="а"), vec(note=None)])
        assert (c.state, c.assigned, c.total) == (ru.STATE_PARTIAL, 3, 4)
        assert c.notes == ("а", None)

    def test_identical_vectors_everywhere_is_resolved(self):
        c = ru.classify([vec(), vec(), vec()])
        assert c.state == ru.STATE_RESOLVED
        assert c.categories == (20,) and c.audit_differs is False

    # Конфликт — по КАЖДОЙ компоненте вектора отдельно (§4.1): негативные к
    # «конфликт = разные статьи». Вход каждого теста нарушает ровно одну компоненту.
    def test_conflict_by_category_lists_both_categories_in_first_seen_order(self):
        c = ru.classify([vec(category=11), vec(category=10), vec(category=11)])
        assert c.state == ru.STATE_CONFLICT
        assert c.categories == (11, 10)
        assert c.notes == (None,) and c.audit_differs is False

    def test_conflict_by_note_only(self):
        c = ru.classify([vec(note="раз"), vec(note="два")])
        assert c.state == ru.STATE_CONFLICT
        assert c.categories == (20,) and c.notes == ("раз", "два") and c.audit_differs is False

    def test_conflict_by_author_only(self):
        c = ru.classify([vec(by=1), vec(by=2)])
        assert c.state == ru.STATE_CONFLICT and c.audit_differs is True
        assert c.categories == (20,) and c.notes == (None,)

    def test_conflict_by_time_only(self):
        c = ru.classify([vec(at=T1), vec(at=T2)])
        assert c.state == ru.STATE_CONFLICT and c.audit_differs is True

    def test_single_estimate_round_has_only_two_states(self):
        assert ru.classify([None]).state == ru.STATE_UNASSIGNED
        assert ru.classify([vec()]).state == ru.STATE_RESOLVED

    def test_empty_radius_is_a_contract_error(self):
        with pytest.raises(ValueError):
            ru.classify([])


class TestPartitionProperty:
    """Разбиение исчерпывающее и непересекающееся — свойство на ВСЕХ векторах
    из малого домена (урок «сходимость не доказывает разбиения»: состав
    проверяется отдельно, не через один пример на состояние)."""

    DOMAIN = [None, vec(), vec(category=21), vec(note="з"), vec(by=2), vec(at=T2)]

    @pytest.mark.parametrize("n", [1, 2, 3])
    def test_every_vector_tuple_lands_in_exactly_one_state(self, n):
        for vectors in itertools.product(self.DOMAIN, repeat=n):
            c = ru.classify(list(vectors))
            present = [v for v in vectors if v is not None]
            expected = (
                ru.STATE_UNASSIGNED if not present
                else ru.STATE_PARTIAL if len(present) < n
                else ru.STATE_RESOLVED if len(set(present)) == 1
                else ru.STATE_CONFLICT
            )
            assert c.state == expected, vectors
            assert c.state in (*ru.PENDING_STATES, ru.STATE_RESOLVED)
            assert c.assigned == len(present) and c.total == n


def node(key, parent=None, raw=None, rows=1, number=None, title="р") -> ru.ChapterNode:
    return ru.ChapterNode(key=("lot_1", key), file_parent=None if parent is None else ("lot_1", parent),
                          number=number or key, title=title, smr_article_raw=raw, rows=rows)


NODES = [node("1"), node("14"), node("14.1", parent="14"), node("14.3", parent="14"), node("15")]


class TestInputSet:
    def test_chapter_with_file_article_and_no_override_is_excluded(self):
        missing = {n.key: n.key[1] != "1" for n in NODES}          # у «1» статья есть
        vectors = {n.key: [None, None] for n in NODES}
        keys = [n.key[1] for n in ru.input_set(NODES, missing, vectors)]
        assert keys == ["14", "14.1", "14.3", "15"]                 # файловый порядок сохранён

    def test_chapter_with_file_article_but_a_partial_override_is_included(self):
        missing = {n.key: n.key[1] != "1" for n in NODES}
        vectors = {n.key: [None, None] for n in NODES}
        vectors[("lot_1", "1")] = [vec(), None]
        assert ("lot_1", "1") in {n.key for n in ru.input_set(NODES, missing, vectors)}

    def test_empty_statement_is_empty_answer(self):
        assert ru.input_set([], {}, {}) == []


class TestRehang:
    def test_parent_is_the_nearest_file_ancestor_inside_the_set(self):
        """«14.1» классифицирована файлом и не тронута — вне множества; «14.1.1»
        обязана повиснуть на «14», а не стать корнем и не потеряться."""
        deep = [node("14"), node("14.1", parent="14"), node("14.1.1", parent="14.1")]
        missing = {deep[0].key: True, deep[1].key: False, deep[2].key: True}
        out = {a.node.key[1]: a for a in ru.aggregate(deep, missing, {n.key: [None] for n in deep})}
        assert set(out) == {"14", "14.1.1"}
        assert out["14"].parent_key is None
        assert out["14.1.1"].parent_key == ("lot_1", "14") and out["14.1.1"].depth == 1

    def test_node_with_its_own_raw_statement_is_always_a_root(self):
        """Тот же предикат, что у `_unallocated_sections` паспорта: решение на
        предке до узла со своим утверждением не дойдёт (правило Ф3), и
        вложенность обещала бы неправду. Негативный к «родитель = ближайший
        предок из множества»: вход отличается от предыдущего теста ТОЛЬКО
        непустым `smr_article_raw` у ребёнка."""
        nodes = [node("14"), node("14.1", parent="14", raw="9999")]
        out = {a.node.key[1]: a for a in ru.aggregate(nodes, {n.key: True for n in nodes}, {n.key: [None] for n in nodes})}
        assert out["14.1"].parent_key is None and out["14.1"].depth == 0


class TestAggregate:
    def test_depth_counts_from_rehung_root_and_order_is_the_input_order(self):
        missing = {n.key: True for n in NODES}
        vectors = {n.key: [None] for n in NODES}
        out = ru.aggregate(NODES, missing, vectors)
        assert [a.node.key[1] for a in out] == ["1", "14", "14.1", "14.3", "15"]
        assert [a.depth for a in out] == [0, 0, 1, 1, 0]

    def test_rows_is_the_node_rows_regardless_of_vectors(self):
        """`rows` — полный размер файлового поддерева: конфликт при нуле
        нераспределённых строк несёт ненулевой `rows` (§4.1).

        ⚠ УСТАРЕЛО (см. врезку): `node.rows` таким и остался, но на провод у
        `sections[]` идут достижимые строки — см. `TestReachability`."""
        n14 = node("14", rows=3)
        out = ru.aggregate([n14], {n14.key: False}, {n14.key: [vec(category=10), vec(category=11)]})
        assert out[0].classification.state == ru.STATE_CONFLICT
        assert out[0].node.rows == 3
```

- [ ] **Step 2: Прогнать, убедиться в падении**

Из `backend/`: `uv run pytest tests/unit/test_round_unallocated.py -q` —
`ModuleNotFoundError: services.round_unallocated`.

- [ ] **Step 3: Реализация**

```python
"""Чистый агрегатор состояний этапного разноса (спека этапного разноса §2.2).

Состояние логического раздела — агрегат по ПОЛНОМУ вектору решения
`(work_category_id, note, assigned_by, assigned_at)` всех offer-смет раунда:
без сравнения аудита раздел с единой статьёй и разошедшимися авторами исчез бы
из обоих блоков экрана (находка ревью гейта 1). Разбиение исчерпывающее:
`unassigned` / `partial` / `conflict` / `resolved`.

Модуль без `Session`: вход собирает `crud/round_unallocated.py`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

STATE_UNASSIGNED = "unassigned"
STATE_PARTIAL = "partial"
STATE_CONFLICT = "conflict"
STATE_RESOLVED = "resolved"
PENDING_STATES = (STATE_UNASSIGNED, STATE_PARTIAL, STATE_CONFLICT)

SectionKey = tuple[str, str]


@dataclass(frozen=True)
class Vector:
    work_category_id: int
    note: str | None
    assigned_by: int
    assigned_at: datetime


@dataclass(frozen=True)
class Classification:
    state: str
    assigned: int
    total: int
    categories: tuple[int, ...]
    notes: tuple[str | None, ...]
    audit_differs: bool


@dataclass(frozen=True)
class ChapterNode:
    key: SectionKey
    file_parent: SectionKey | None
    number: str | None
    title: str
    smr_article_raw: str | None
    rows: int


@dataclass(frozen=True)
class SectionAggregate:
    node: ChapterNode
    parent_key: SectionKey | None
    depth: int
    classification: Classification
    vectors: tuple[Vector | None, ...]


def _distinct(values):
    seen: list = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return tuple(seen)


def classify(vectors: Sequence[Vector | None]) -> Classification:
    """Четыре состояния §2.2. `vectors` — по одной позиции на offer-смету
    радиуса, порядок `estimate_id ASC`; пустой радиус — ошибка контракта, а не
    состояние: у раунда без offer-смет агрегатора не бывает (§1.5, 404)."""
    if not vectors:
        raise ValueError("радиус агрегатора пуст: у раунда нет offer-смет")
    present = [v for v in vectors if v is not None]
    total, assigned = len(vectors), len(present)
    categories = _distinct(v.work_category_id for v in present)
    notes = _distinct(v.note for v in present)
    audit_differs = len({(v.assigned_by, v.assigned_at) for v in present}) > 1
    if assigned == 0:
        state = STATE_UNASSIGNED
    elif assigned < total:
        state = STATE_PARTIAL
    elif len(set(present)) == 1:
        state = STATE_RESOLVED
    else:
        state = STATE_CONFLICT
    return Classification(state, assigned, total, categories, notes, audit_differs)


def input_set(
    nodes: Sequence[ChapterNode],
    missing_article: Mapping[SectionKey, bool],
    vectors: Mapping[SectionKey, Sequence[Vector | None]],
) -> list[ChapterNode]:
    """§2.2: эффективной статьи нет хотя бы в одной смете ИЛИ есть хотя бы
    один override. Порядок `nodes` (файловый) сохраняется."""
    return [
        n for n in nodes
        if missing_article.get(n.key, False) or any(v is not None for v in vectors.get(n.key, ()))
    ]


def aggregate(
    nodes: Sequence[ChapterNode],
    missing_article: Mapping[SectionKey, bool],
    vectors: Mapping[SectionKey, Sequence[Vector | None]],
) -> list[SectionAggregate]:
    """Входное множество, переподвешивание, глубина и классификация — в файловом
    порядке `nodes`.

    Родитель внутри входного множества — ближайший файловый предок из
    множества; предок ВНЕ множества — законный промежуток цепочки, поэтому
    `nodes` обязаны нести все разделы сметы. Узел со своим `smr_article_raw`
    — всегда корень своего кусочка (тот же предикат, что у
    `_unallocated_sections` паспорта: решение на предке до узла с собственным
    утверждением не доходит — правило Ф3 «утверждение файла сильнее
    наследования», — и вложенность обещала бы неправду).
    """
    selected = input_set(nodes, missing_article, vectors)
    file_parent = {n.key: n.file_parent for n in nodes}
    in_set = {n.key for n in selected}
    parent_of: dict[SectionKey, SectionKey | None] = {}
    for n in selected:
        if n.smr_article_raw is not None:
            parent_of[n.key] = None
            continue
        p = file_parent[n.key]
        while p is not None and p not in in_set:
            p = file_parent.get(p)
        parent_of[n.key] = p
    depth: dict[SectionKey, int] = {}

    def _depth(k: SectionKey) -> int:
        if k not in depth:
            p = parent_of[k]
            depth[k] = 0 if p is None else _depth(p) + 1
        return depth[k]

    return [
        SectionAggregate(
            node=n, parent_key=parent_of[n.key], depth=_depth(n.key),
            classification=classify(vectors[n.key]), vectors=tuple(vectors[n.key]),
        )
        for n in selected
    ]
```

- [ ] **Step 4: Прогнать** — `uv run pytest tests/unit/test_round_unallocated.py -q` зелёный;
  `uv run ruff check services/round_unallocated.py tests/unit/test_round_unallocated.py`.
- [ ] **Step 5: Commit**

```bash
git add backend/services/round_unallocated.py backend/tests/unit/test_round_unallocated.py
git commit -m "feat(round-unallocated): чистый агрегатор состояний по полному вектору решений"
```

---

### Task 2: Вход агрегатора из БД и фикстура `round_scene` (`crud/round_unallocated.py`)

**Files:**
- Create: `backend/crud/round_unallocated.py`
- Modify: `backend/tests/integration/conftest.py` (фикстура `round_scene`)
- Test: `backend/tests/integration/test_round_unallocated_api.py` (класс `TestStates`)

**Interfaces:**
- Consumes: `services.round_unallocated` (задача 1); `crud.project_passport._section_metrics`
  (расчёт структуры и `rows` — ОДИН на паспорт и раунд; импорт приватного
  имени намеренный, паспорт не правится ни строкой); `crud.common.DomainError`;
  модели `Tender`, `TenderRound`, `Offer`, `OfferPackage`, `Contractor`,
  `Estimate`, `Lot`, `Proposal`, `PositionItem`, `EstimateCategoryOverride`, `User`.
- Produces:

```python
CODE_TENDER_NOT_FOUND = "tender_not_found"
CODE_ROUND_NOT_FOUND = "round_not_found"
CODE_NO_OFFER_ESTIMATES = "round_has_no_offer_estimates"
NO_OFFER_ESTIMATES_MESSAGE = "Раунд или его сметы больше недоступны."

class RoundMappingBroken(Exception):
    """Проекции одного файла разошлись — порча НАШИХ данных: 500 с логом."""

@dataclass(frozen=True)
class RoundScope:
    tender: Tender
    round: TenderRound
    estimates: list[Estimate]              # offer-сметы, estimate_id ASC; непустой
    contractor_title: dict[int, str]       # estimate_id → название участника

def require_round(db, tender_id, round_id) -> TenderRound     # DomainError 404 с кодами
def offer_estimates(db, round_id) -> list[Estimate]           # join Offer, ORDER BY Estimate.id
def load_scope(db, tender_id, round_id) -> RoundScope         # + 404 round_has_no_offer_estimates
def load_states(db, scope) -> list[ru.SectionAggregate]       # вход агрегатора из БД; RoundMappingBroken
```

- [ ] **Step 1: Фикстура `round_scene`** — в `backend/tests/integration/conftest.py`,
  рядом с `unallocated_tree`:

```python
@pytest.fixture
def round_scene(db_session, factories):
    """Тендер с двумя раундами через НАСТОЯЩИЙ `import_round` (см. докстроку
    `unallocated_tree`: пересчёт разноса держит биекцию raw_data ↔ строки).
    Раунд 1: три участника + baseline, у всех одна ведомость — «1» со статьёй
    «6», «14» → «14.1»/«14.3» без статьи, «15» без статьи, допработы со
    ссылкой на «14» (кандидат без статьи) и остаток без ссылки (граница §5.5).
    Раунд 2: один участник, `chapter_number="прим."` — структура погашена.
    Числа спеки: rows «14» = 3, «14.1» = 2, «14.3» = 1, «15» = 1."""
    from tests.payloads import (
        additional_works_row, baseline_proposal_block, position, proposal, round_payload,
        summary_line, svedeniya_info,
    )
    from parser.constants import (
        JSON_KEY_TOTAL_COST_EXCLUDING_VAT, JSON_KEY_TOTAL_COST_INCLUDING_VAT, JSON_KEY_VAT_AMOUNT,
    )
    from services.round_import import import_round

    def work(number, ref, amount):
        return position(job_title=f"Работа {number}", unit="м2", quantity=1, suggested_quantity=1,
                        unit_cost_total=amount, total_cost_total=amount, chapter_ref=ref, number=number)

    def statement(*, inn, title):
        positions = [
            position(job_title="Раздел 1", is_chapter=True, chapter_number="1", article_smr="6", number="1"),
            work("2", "1", "100.00"),
            position(job_title="SHELL & CORE", is_chapter=True, chapter_number="14", number="3"),
            position(job_title="Подраздел 14.1", is_chapter=True, chapter_number="14.1", number="4"),
            work("5", "14.1", "30.00"),
            work("6", "14.1", "30.00"),
            position(job_title="Подраздел 14.3", is_chapter=True, chapter_number="14.3", number="7"),
            work("8", "14.3", "20.00"),
            position(job_title="Рабочая документация", is_chapter=True, chapter_number="15", number="9"),
            work("10", "15", "10.00"),
        ]
        summary = {JSON_KEY_TOTAL_COST_INCLUDING_VAT: summary_line("ИТОГО, руб. с учетом НДС", "220.00"),
                   JSON_KEY_VAT_AMOUNT: summary_line("В том числе НДС", "0"),
                   JSON_KEY_TOTAL_COST_EXCLUDING_VAT: summary_line("ИТОГО, руб. без учета НДС", "220.00")}
        return proposal(positions, inn=inn, title=title, vat_rate="20", summary=summary,
                        additional_works=additional_works_row(total="30.00"),
                        additional_info=svedeniya_info("14 Отделка - 24.00 руб."))

    tender = factories.TenderFactory.create()
    r1 = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
    r2 = factories.TenderRoundFactory.create(tender=tender, stage_no=2)
    db_session.flush()
    base = baseline_proposal_block([position(job_title="База", unit="м2", unit_cost_total="9", total_cost_total="9")])
    participants = [statement(inn="7700000001", title="ООО А"), statement(inn="7700000002", title="ООО Б"),
                    statement(inn="7700000003", title="ООО В")]
    import_round(db_session, tender_round=r1, data=round_payload(participants, baseline=base),
                 parser_version="4.0.0", import_job_id=None, replace=False,
                 unit_resolver=UnitResolver(db_session), category_resolver=CategoryResolver.from_db(db_session))
    broken = proposal([position(job_title="Примечание", number="3", chapter_number="прим.", is_chapter=True)],
                      inn="7700000001", title="ООО А")
    import_round(db_session, tender_round=r2, data=round_payload([broken]), parser_version="4.0.0",
                 import_job_id=None, replace=False, unit_resolver=UnitResolver(db_session),
                 category_resolver=CategoryResolver.from_db(db_session))
    db_session.flush()

    offer_ids = sa.select(Offer.id).where(Offer.round_id == r1.id)
    estimates = db_session.execute(
        sa.select(Estimate).where(Estimate.offer_id.in_(offer_ids)).order_by(Estimate.id)
    ).scalars().all()
    baseline_id = db_session.execute(sa.select(Estimate.id).where(Estimate.round_id == r1.id)).scalar_one()

    def chapter(estimate_id, number):
        return db_session.execute(
            sa.select(PositionItem).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == estimate_id, PositionItem.is_chapter.is_(True),
                   PositionItem.chapter_number_in_proposal == number)
        ).scalar_one()

    return SimpleNamespace(tender=tender, r1=r1, r2=r2, estimates=estimates,
                           estimate_ids=[e.id for e in estimates], baseline_id=baseline_id,
                           key14=("lot_1", chapter(estimates[0].id, "14").position_key_in_proposal),
                           key15=("lot_1", chapter(estimates[0].id, "15").position_key_in_proposal),
                           key1=("lot_1", chapter(estimates[0].id, "1").position_key_in_proposal),
                           chapter=chapter)
```

`UnitResolver`, `CategoryResolver`, `Offer`, `Estimate`, `SimpleNamespace`
в conftest уже импортированы либо импортируются рядом — проверить `grep -n`
перед добавлением; чего нет — добавить в шапку файла.

- [ ] **Step 2: Написать падающие тесты** — `backend/tests/integration/test_round_unallocated_api.py`:

```python
"""Этапный разнос: вход агрегатора из БД, GET, счётчик карточки
(спека 2026-09-01-round-unallocated-design.md §2.2, §2.3, §2.5, §2.6, §4.1, §4.3)."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from crud import round_unallocated as crud_ru
from crud.common import DomainError
from models import EstimateCategoryOverride, WorkCategory
from services import round_unallocated as ru
from services.category_override import set_override

pytestmark = pytest.mark.integration


def cat(db, code):
    return db.execute(sa.select(WorkCategory.id).where(WorkCategory.code == code)).scalar_one()


def states_by_number(db, scene):
    scope = crud_ru.load_scope(db, scene.tender.id, scene.r1.id)
    return {a.node.number: a for a in crud_ru.load_states(db, scope)}


class TestStates:
    def test_input_set_excludes_file_classified_untouched_chapters(self, db_session, round_scene):
        by = states_by_number(db_session, round_scene)
        assert set(by) == {"14", "14.1", "14.3", "15"}          # «1» со статьёй «6» — вне множества
        assert all(a.classification.state == ru.STATE_UNASSIGNED for a in by.values())

    def test_rows_is_the_full_file_subtree(self, db_session, round_scene):
        by = states_by_number(db_session, round_scene)
        assert {n: a.node.rows for n, a in by.items()} == {"14": 3, "14.1": 2, "14.3": 1, "15": 1}

    def test_parents_are_rehung_and_order_is_the_file_order(self, db_session, round_scene):
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r1.id)
        out = crud_ru.load_states(db_session, scope)
        assert [a.node.number for a in out] == ["14", "14.1", "14.3", "15"]
        assert out[1].parent_key == round_scene.key14 and out[0].parent_key is None
        assert [a.depth for a in out] == [0, 1, 1, 0]

    def test_partial_after_a_by_estimate_decision_on_one_estimate(self, db_session, round_scene, admin_user):
        """Частичное состояние создаётся ШТАТНЫМ по-сметным маршрутом — он
        остаётся открытым паспорту, и именно он даёт смешанные состояния."""
        e0 = round_scene.estimates[0]
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "14").id,
                     work_category_id=cat(db_session, "20"), note="первая", user_id=admin_user.id)
        c = states_by_number(db_session, round_scene)["14"].classification
        assert (c.state, c.assigned, c.total, c.notes) == (ru.STATE_PARTIAL, 1, 3, ("первая",))

    def test_file_classified_chapter_with_partial_override_enters_the_set(self, db_session, round_scene, admin_user):
        e0 = round_scene.estimates[0]
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "1").id,
                     work_category_id=cat(db_session, "20"), note=None, user_id=admin_user.id)
        assert states_by_number(db_session, round_scene)["1"].classification.state == ru.STATE_PARTIAL

    def test_conflict_by_category_and_resolved_when_identical(self, db_session, round_scene, admin_user):
        codes = ["20", "20", "16"]
        for e, code in zip(round_scene.estimates, codes):
            set_override(db_session, estimate_id=e.id, position_item_id=round_scene.chapter(e.id, "14").id,
                         work_category_id=cat(db_session, code), note=None, user_id=admin_user.id)
        c = states_by_number(db_session, round_scene)["14"].classification
        assert c.state == ru.STATE_CONFLICT
        assert c.categories == (cat(db_session, "20"), cat(db_session, "16"))
        # Выравнивание третьей сметы → resolved (в одной транзакции now() один
        # на всех, автор один — вектор единый).
        e2 = round_scene.estimates[2]
        set_override(db_session, estimate_id=e2.id, position_item_id=round_scene.chapter(e2.id, "14").id,
                     work_category_id=cat(db_session, "20"), note=None, user_id=admin_user.id)
        assert states_by_number(db_session, round_scene)["14"].classification.state == ru.STATE_RESOLVED

    def test_conflict_by_audit_only(self, db_session, round_scene, admin_user):
        """Статья и заметка едины, различается ТОЛЬКО `assigned_at` — сдвинут
        явным UPDATE (в одной транзакции now() один, иначе различия не создать)."""
        for e in round_scene.estimates:
            set_override(db_session, estimate_id=e.id, position_item_id=round_scene.chapter(e.id, "14").id,
                         work_category_id=cat(db_session, "20"), note=None, user_id=admin_user.id)
        victim = round_scene.chapter(round_scene.estimates[1].id, "14").id
        db_session.execute(sa.text("UPDATE estimate_category_overrides SET assigned_at = now() - interval '1 day' "
                                   "WHERE position_item_id = :rid"), {"rid": victim})
        c = states_by_number(db_session, round_scene)["14"].classification
        assert c.state == ru.STATE_CONFLICT and c.audit_differs is True and len(c.categories) == 1

    def test_baseline_is_outside_the_radius(self, db_session, round_scene):
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r1.id)
        assert [e.id for e in scope.estimates] == round_scene.estimate_ids
        assert round_scene.baseline_id not in round_scene.estimate_ids

    def test_key_missing_in_one_projection_is_mapping_broken(self, db_session, round_scene):
        """Ключ представительной сметы отсутствует в другой (§4.1): раздел «15»
        удаляется у второй сметы в обход домена (сначала его позиция — RESTRICT
        `fk_position_items_chapter`)."""
        e1 = round_scene.estimates[1]
        ch = round_scene.chapter(e1.id, "15")
        db_session.execute(sa.text("DELETE FROM position_items WHERE chapter_item_id = :cid"), {"cid": ch.id})
        db_session.execute(sa.text("DELETE FROM position_items WHERE id = :cid"), {"cid": ch.id})
        db_session.flush()
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r1.id)
        with pytest.raises(crud_ru.RoundMappingBroken):
            crud_ru.load_states(db_session, scope)

    def test_round_without_offer_estimates_is_404_with_its_code(self, db_session, factories):
        tender = factories.TenderFactory.create()
        rnd = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
        db_session.flush()
        with pytest.raises(DomainError) as e:
            crud_ru.load_scope(db_session, tender.id, rnd.id)
        assert (e.value.status_code, e.value.code) == (404, crud_ru.CODE_NO_OFFER_ESTIMATES)
        assert e.value.detail == crud_ru.NO_OFFER_ESTIMATES_MESSAGE

    def test_round_of_another_tender_is_404_round_not_found(self, db_session, round_scene, factories):
        other = factories.TenderFactory.create()
        db_session.flush()
        with pytest.raises(DomainError) as e:
            crud_ru.require_round(db_session, other.id, round_scene.r1.id)
        assert e.value.code == crud_ru.CODE_ROUND_NOT_FOUND
        with pytest.raises(DomainError) as e:
            crud_ru.require_round(db_session, other.id + 10_000, round_scene.r1.id)
        assert e.value.code == crud_ru.CODE_TENDER_NOT_FOUND
```

`admin_user` — фикстура integration/conftest. Коды статей «20», «16», «6» в
справочнике стенда есть (спека разноса §1.2, тесты свода); если у справочника
тестовой БД их нет — взять любые два листа, как делает `scene` в
`test_category_override_concurrency.py`.

- [ ] **Step 3: Прогнать, убедиться в падении** —
  `TEST_DATABASE_URL=… uv run pytest tests/integration/test_round_unallocated_api.py -q` — `ImportError`.

- [ ] **Step 4: Реализация** `backend/crud/round_unallocated.py`:

```python
"""Чтение для этапного разноса (спека этапного разноса §2.2–§2.3, §2.5–§2.6):
радиус раунда, вход агрегатора, диагностика, тело GET, счётчик карточки.

Радиус — offer-сметы раунда СОЕДИНЕНИЕМ с `offers` (§2.1): baseline
(`estimates.round_id`) в выборку не попадает по построению, а не фильтром.
Структура и `rows` — из `_section_metrics` паспорта: расчёт ОДИН на оба
потребителя, второй разошёлся бы с первым (спека разноса §5.2–5.3).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from crud.common import DomainError
from crud.project_passport import _section_metrics  # ОДИН расчёт структуры на паспорт и раунд
from models import (
    Contractor, Estimate, EstimateCategoryOverride, Lot, Offer, OfferPackage, PositionItem,
    Proposal, Tender, TenderRound, User,
)
from services import round_unallocated as ru

CODE_TENDER_NOT_FOUND = "tender_not_found"
CODE_ROUND_NOT_FOUND = "round_not_found"
CODE_NO_OFFER_ESTIMATES = "round_has_no_offer_estimates"
NO_OFFER_ESTIMATES_MESSAGE = "Раунд или его сметы больше недоступны."


class RoundMappingBroken(Exception):
    """Проекции одного файла разошлись (ключ не во всех сметах либо разный
    `is_chapter`) — порча НАШИХ данных: роутер логирует и отдаёт 500,
    пользователю повторять нечего (§2.2, §2.4)."""


@dataclass(frozen=True)
class RoundScope:
    tender: Tender
    round: TenderRound
    estimates: list[Estimate]
    contractor_title: dict[int, str]


def require_round(db: Session, tender_id: int, round_id: int) -> TenderRound:
    """Как `crud.tenders.get_round`, но с КОДАМИ (§2.3): существующий
    `get_round` без кода не правится — его 404 менять нельзя."""
    if db.get(Tender, tender_id) is None:
        raise DomainError(404, f"Тендер {tender_id} не найден.", code=CODE_TENDER_NOT_FOUND)
    rnd = db.execute(
        sa.select(TenderRound).where(TenderRound.id == round_id, TenderRound.tender_id == tender_id)
    ).scalar_one_or_none()
    if rnd is None:
        raise DomainError(404, f"Раунд {round_id} тендера {tender_id} не найден.", code=CODE_ROUND_NOT_FOUND)
    return rnd


def offer_estimates(db: Session, round_id: int) -> list[Estimate]:
    """Радиус §2.1: offer-сметы раунда, `estimate_id ASC` — этот же порядок
    задаёт представительную смету и порядок блокировок писателя."""
    return db.execute(
        sa.select(Estimate).join(Offer, Offer.id == Estimate.offer_id)
        .where(Offer.round_id == round_id).order_by(Estimate.id)
    ).scalars().all()


def load_scope(db: Session, tender_id: int, round_id: int) -> RoundScope:
    tender = db.get(Tender, tender_id)
    rnd = require_round(db, tender_id, round_id)
    estimates = offer_estimates(db, round_id)
    if not estimates:
        raise DomainError(404, NO_OFFER_ESTIMATES_MESSAGE, code=CODE_NO_OFFER_ESTIMATES)
    titles = dict(db.execute(
        sa.select(Estimate.id, Contractor.title)
        .join(Offer, Offer.id == Estimate.offer_id)
        .join(OfferPackage, OfferPackage.id == Offer.package_id)
        .join(Contractor, Contractor.id == OfferPackage.contractor_id)
        .where(Estimate.id.in_([e.id for e in estimates]))
    ).all())
    return RoundScope(tender=tender, round=rnd, estimates=list(estimates), contractor_title=titles)


def _chapter_rows(db: Session, estimate_ids: list[int]):
    """Все строки-разделы радиуса с их решениями и авторами — ОДНИМ запросом."""
    author = aliased(User)
    return db.execute(
        sa.select(
            Lot.estimate_id, Lot.lot_key, PositionItem.id, PositionItem.position_key_in_proposal,
            PositionItem.work_category_id,
            EstimateCategoryOverride.work_category_id.label("decided_category_id"),
            EstimateCategoryOverride.note, EstimateCategoryOverride.assigned_by,
            EstimateCategoryOverride.assigned_at, author.email.label("assigned_by_email"),
        )
        .select_from(PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .outerjoin(EstimateCategoryOverride, EstimateCategoryOverride.position_item_id == PositionItem.id)
        .outerjoin(author, author.id == EstimateCategoryOverride.assigned_by)
        .where(Lot.estimate_id.in_(estimate_ids), PositionItem.is_chapter.is_(True))
    ).all()


def representative_nodes(db: Session, estimate: Estimate) -> list[ru.ChapterNode]:
    """Структура и `rows` — из `_section_metrics` представительной сметы, в
    файловом порядке (тот же ключ порядка, что у `_unallocated_sections`:
    лот по `proposal_id`, затем числовой порядок ключа позиции)."""
    metrics = _section_metrics(db, estimate.id)
    lot_key_of = dict(db.execute(
        sa.select(Proposal.id, Lot.lot_key).join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id == estimate.id)
    ).all())
    key_of = {pid: (lot_key_of[m.proposal_id], m.position_key_in_proposal) for pid, m in metrics.items()}
    ordered = sorted(metrics.items(), key=lambda kv: (kv[1].proposal_id, len(kv[1].position_key_in_proposal),
                                                      kv[1].position_key_in_proposal))
    return [
        ru.ChapterNode(
            key=key_of[pid],
            file_parent=None if m.parent_position_item_id is None else key_of[m.parent_position_item_id],
            number=m.number, title=m.title, smr_article_raw=m.smr_article_raw, rows=m.rows,
        )
        for pid, m in ordered
    ]


def load_states(db: Session, scope: RoundScope) -> list[ru.SectionAggregate]:
    ids = [e.id for e in scope.estimates]
    rows = _chapter_rows(db, ids)
    keys_by_estimate: dict[int, set[ru.SectionKey]] = {eid: set() for eid in ids}
    vectors: dict[ru.SectionKey, dict[int, ru.Vector | None]] = defaultdict(dict)
    missing: dict[ru.SectionKey, bool] = defaultdict(bool)
    for r in rows:
        key = (r.lot_key, r.position_key_in_proposal)
        keys_by_estimate[r.estimate_id].add(key)
        missing[key] = missing[key] or r.work_category_id is None
        vectors[key][r.estimate_id] = (
            None if r.decided_category_id is None
            else ru.Vector(r.decided_category_id, r.note, r.assigned_by, r.assigned_at)
        )
    reference = keys_by_estimate[ids[0]]
    for eid, keys in keys_by_estimate.items():
        if keys != reference:
            raise RoundMappingBroken(
                f"Раунд {scope.round.id}: набор разделов сметы {eid} не совпадает с представительной "
                f"сметой {ids[0]} (только там: {sorted(keys - reference)[:5]}; только здесь: "
                f"{sorted(reference - keys)[:5]}). Проекции одного файла разошлись."
            )
    nodes = representative_nodes(db, scope.estimates[0])
    ordered_vectors = {k: [vectors[k].get(eid) for eid in ids] for k in vectors}
    return ru.aggregate(nodes, missing, ordered_vectors)
```

`aliased` и `User`: `assigned_by_email` понадобится сериализации в задаче 4 —
столбец есть уже здесь, чтобы запрос был один. `assigned_at` в `Vector` —
`datetime` с tz из колонки `timestamptz`; сравнение векторов идёт по
значениям БД, поэтому единый `now()` одной транзакции даёт равенство.

- [ ] **Step 5: Прогнать** — `uv run pytest tests/integration/test_round_unallocated_api.py -q -k TestStates` зелёный.
  Затем `uv run pytest tests/integration/test_project_passport_api.py -q` — паспорт не тронут, но
  `_section_metrics` получил второго потребителя: убедиться, что зелёный остался зелёным.
- [ ] **Step 6: Commit**

```bash
git add backend/crud/round_unallocated.py backend/tests/integration/conftest.py backend/tests/integration/test_round_unallocated_api.py
git commit -m "feat(round-unallocated): вход агрегатора из БД — радиус offer-смет, структура представительной сметы, mapping_broken"
```

---

### Task 3: Диагностика — три кода своим селектором

**Files:**
- Modify: `backend/services/additional_works.py` (константы причин `resolve_ref`)
- Modify: `backend/crud/round_unallocated.py` (`diagnostics`)
- Test: `backend/tests/integration/test_round_unallocated_api.py` (класс `TestDiagnostics`),
  `backend/tests/unit/test_additional_works.py` (константы)

**Interfaces:**
- Consumes: `services.additional_works.categories_by_chapter_number`, `resolve_ref`;
  `services.category_resolution.CategoryResolver`; `services.estimate_import.extract_positions`,
  `extract_single_proposal`; `services.category_override._overrides_of`, `_rows_of`
  (карта решений предложения по ключу файла — тот же вход резолвера, что у пересчёта).
- Produces:

```python
# services/additional_works.py — поведение resolve_ref не меняется, литералы получают имена:
REASON_NO_CANDIDATES = "нет кандидатов"
REASON_CANDIDATE_WITHOUT_ARTICLE = "кандидат без статьи"
REASON_AMBIGUOUS = "статьи различаются"

# crud/round_unallocated.py
DIAG_OUTSIDE_STRUCTURE = "outside_structure"
DIAG_STRUCTURE_DISABLED = "structure_disabled"
DIAG_UNRESOLVED_REF = "unresolved_chapter_ref"

def diagnostics(db, scope: RoundScope) -> list[dict]
# элемент: {"code", "contractor_title", "title", "rows"}; порядок — сметы по id, внутри сметы — лоты
# по proposal_id, внутри лота: structure_disabled | outside_structure, затем допработы по ordinal
```

- [ ] **Step 1: Тесты**

В `backend/tests/unit/test_additional_works.py` (рядом с тестами `resolve_ref`):

```python
def test_resolve_ref_reasons_are_the_named_constants():
    from services.additional_works import (
        REASON_AMBIGUOUS, REASON_CANDIDATE_WITHOUT_ARTICLE, REASON_NO_CANDIDATES, resolve_ref,
    )
    assert resolve_ref("9", {}) == (None, REASON_NO_CANDIDATES)
    assert resolve_ref("9", {"9": {None, 7}}) == (None, REASON_CANDIDATE_WITHOUT_ARTICLE)
    assert resolve_ref("9", {"9": {7, 8}}) == (None, REASON_AMBIGUOUS)
```

В `test_round_unallocated_api.py`:

```python
class TestDiagnostics:
    def test_remainder_without_ref_is_unresolved_and_resolvable_ref_is_not(self, db_session, round_scene):
        """Остаток 6.00 без ссылки — граница §5.5; строка «14 Отделка» ссылается на
        раздел без статьи (кандидат без статьи) — разнос её закроет, в диагностике
        её НЕТ (негативный, §2.5)."""
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r1.id)
        diags = crud_ru.diagnostics(db_session, scope)
        assert [d["code"] for d in diags] == [crud_ru.DIAG_UNRESOLVED_REF] * 3      # по одной на участника
        assert {d["title"] for d in diags} == {"Дополнительные работы"}
        assert {d["contractor_title"] for d in diags} == {"ООО А", "ООО Б", "ООО В"}
        assert all(d["rows"] == 1 for d in diags)

    def test_ref_to_unknown_chapter_is_unresolved(self, db_session, factories):
        scene = _scene_with_lines(db_session, factories, ["77 Подвал - 24.00 руб."])
        codes = [d["code"] for d in crud_ru.diagnostics(db_session, crud_ru.load_scope(db_session, scene.tender.id, scene.r1.id))]
        assert codes.count(crud_ru.DIAG_UNRESOLVED_REF) == 2     # «77» нет кандидатов + остаток без ссылки

    def test_structure_disabled_is_one_row_per_proposal(self, db_session, round_scene):
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r2.id)
        diags = crud_ru.diagnostics(db_session, scope)
        assert [d["code"] for d in diags] == [crud_ru.DIAG_STRUCTURE_DISABLED]
        assert diags[0]["rows"] == 0 and diags[0]["contractor_title"] == "ООО А"

    def test_position_outside_structure_is_one_row_per_proposal_with_a_count(self, db_session, factories):
        """Строка без номера и без раздела — `RowKind.OUTSIDE_STRUCTURE`
        (`category_resolution._row_kind`): number пустой И chapter_number пустой."""
        scene = _scene_with_positions(db_session, factories, [
            position(job_title="Раздел 1", is_chapter=True, chapter_number="1", article_smr="6", number="1"),
            position(job_title="Сирота", unit="м2", quantity=1, suggested_quantity=1,
                     unit_cost_total="5", total_cost_total="5", number=""),
            position(job_title="Сирота 2", unit="м2", quantity=1, suggested_quantity=1,
                     unit_cost_total="5", total_cost_total="5", number=""),
        ])
        diags = crud_ru.diagnostics(db_session, crud_ru.load_scope(db_session, scene.tender.id, scene.r1.id))
        outside = [d for d in diags if d["code"] == crud_ru.DIAG_OUTSIDE_STRUCTURE]
        assert len(outside) == 1 and outside[0]["rows"] == 2
```

`_scene_with_lines` / `_scene_with_positions` — локальные хелперы файла: один
участник, один раунд, `import_round` как в `round_scene`; `position` из
`tests.payloads`. Каждый из трёх кодов имеет позитивный тест, у
`unresolved_chapter_ref` — негативный (разрешимая ссылка не попадает).

- [ ] **Step 2: Прогнать** — `-k "TestDiagnostics or reasons_are"` красный (`AttributeError`).

- [ ] **Step 3: Реализация.** В `services/additional_works.py` — три константы
  над `resolve_ref`, и в теле `return None, REASON_NO_CANDIDATES` и т. д.
  В `crud/round_unallocated.py`:

```python
from services.additional_works import (
    REASON_CANDIDATE_WITHOUT_ARTICLE, categories_by_chapter_number, resolve_ref,
)
from services.category_override import _overrides_of, _rows_of
from services.category_resolution import CategoryResolver, RowKind
from services.estimate_import import extract_positions, extract_single_proposal
from models import EstimateAdditionalWork, EstimateRawData
from parser.constants import JSON_KEY_LOTS

DIAG_OUTSIDE_STRUCTURE = "outside_structure"
DIAG_STRUCTURE_DISABLED = "structure_disabled"
DIAG_UNRESOLVED_REF = "unresolved_chapter_ref"


def diagnostics(db: Session, scope: RoundScope) -> list[dict]:
    """Границы §5.5 спеки разноса поимённо (§2.5). План резолва — ТОТ ЖЕ, что у
    пересчёта (`apply_overrides`): позиции из `raw_data`, решения предложения,
    `CategoryResolver.resolve_proposal`, `categories_by_chapter_number`/`resolve_ref`.
    Допработа с исходом «кандидат без статьи» разносом закрывается и сюда не
    попадает; «нет кандидатов», «статьи различаются» и строка без ссылки —
    границы."""
    resolver = CategoryResolver.from_db(db)
    out: list[dict] = []
    for estimate in scope.estimates:
        title = scope.contractor_title[estimate.id]
        raw = db.execute(sa.select(EstimateRawData.raw_data).where(EstimateRawData.estimate_id == estimate.id)).scalar_one()
        lots = db.execute(
            sa.select(Lot.lot_key, Lot.lot_title, Proposal.id).join(Proposal, Proposal.lot_id == Lot.id)
            .where(Lot.estimate_id == estimate.id).order_by(Proposal.id)
        ).all()
        for lot_key, lot_title, proposal_id in lots:
            positions = extract_positions(extract_single_proposal((raw.get(JSON_KEY_LOTS) or {}).get(lot_key)))
            _rows, ids_by_key = _rows_of(db, proposal_id)
            resolution = resolver.resolve_proposal(positions, _overrides_of(db, ids_by_key))
            if resolution.structure_disabled:
                out.append({"code": DIAG_STRUCTURE_DISABLED, "contractor_title": title, "title": lot_title,
                            "rows": sum(1 for r in resolution.rows.values() if r.kind is not RowKind.CHAPTER)})
                continue
            if resolution.counters.rows_outside_structure:
                out.append({"code": DIAG_OUTSIDE_STRUCTURE, "contractor_title": title, "title": lot_title,
                            "rows": resolution.counters.rows_outside_structure})
            by_number = categories_by_chapter_number(positions, resolution)
            extras = db.execute(
                sa.select(EstimateAdditionalWork).where(EstimateAdditionalWork.proposal_id == proposal_id)
                .order_by(EstimateAdditionalWork.ordinal)
            ).scalars().all()
            for extra in extras:
                if extra.work_category_id is not None:
                    continue
                category_id, reason = resolve_ref(extra.chapter_ref_raw, by_number)
                if category_id is None and reason != REASON_CANDIDATE_WITHOUT_ARTICLE:
                    out.append({"code": DIAG_UNRESOLVED_REF, "contractor_title": title,
                                "title": extra.title, "rows": 1})
    return out
```

У `structure_disabled` в
`round_scene` раунд 2 несёт ОДНУ строку-раздел и ни одной позиции — `rows == 0`,
как утверждает тест.

- [ ] **Step 4: Прогнать** — `-k "TestDiagnostics or reasons_are"` зелёный;
  `uv run pytest tests/unit/test_additional_works.py tests/integration/test_category_override_apply.py -q`
  — прежние потребители `resolve_ref` не задеты.
- [ ] **Step 5: Commit**

```bash
git add backend/services/additional_works.py backend/crud/round_unallocated.py backend/tests/unit/test_additional_works.py backend/tests/integration/test_round_unallocated_api.py
git commit -m "feat(round-unallocated): диагностика границ §5.5 тремя кодами тем же планом резолва, что пересчёт"
```

---

### Task 4: `GET /api/v1/tenders/{tender_id}/rounds/{round_id}/unallocated`

**Files:**
- Modify: `backend/crud/round_unallocated.py` (`build_round_unallocated`)
- Modify: `backend/routers/tenders.py`
- Test: `backend/tests/integration/test_round_unallocated_api.py` (класс `TestGet`)

**Interfaces:**
- Consumes: `load_scope`, `load_states`, `diagnostics` (задачи 2–3);
  `crud.project_passport._category_options` и `services.category_rollup.CategoryRef`
  (справочник целиком — тем же способом, что паспорт); `crud.common.iso`;
  `responses.decimal_json`; `routers.domain_errors.raise_domain_error`.
- Produces: `build_round_unallocated(db, tender_id, round_id) -> dict` — тело
  §2.3; маршрут GET с переводом `DomainError` → HTTP и `RoundMappingBroken`
  → `log.error(..., exc_info=True)` + 500.

Форма тела — §2.3 дословно. Ключи `partial`/`conflict` присутствуют ТОЛЬКО у
своего состояния (решение плана 3); `conflict.categories[*]` — `{id, code,
title}` из справочника; `manual[*]` — по единственному вектору `resolved`.

- [ ] **Step 1: Тесты** (в `test_round_unallocated_api.py`; `import logging` — в шапку):

```python
URL = "/api/v1/tenders/{t}/rounds/{r}/unallocated"


def category_count(db):
    return db.execute(sa.select(sa.func.count()).select_from(WorkCategory)).scalar_one()


class TestGet:
    def test_member_gets_the_full_shape(self, member_client, db_session, round_scene):
        r = member_client.get(URL.format(t=round_scene.tender.id, r=round_scene.r1.id))
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"round", "offers_count", "sections", "manual", "diagnostics", "category_options"}
        assert body["round"] == {"id": round_scene.r1.id, "stage_no": 1, "label": None, "held_on": None}
        assert body["offers_count"] == 3
        top = next(s for s in body["sections"] if s["number"] == "14")
        assert set(top) == {"lot_key", "position_key_in_proposal", "parent_key", "depth", "number", "title",
                            "smr_article_raw", "rows", "state"}          # ни partial, ни conflict, ни денег
        assert (top["state"], top["rows"], top["parent_key"]) == ("unassigned", 3, None)
        child = next(s for s in body["sections"] if s["number"] == "14.1")
        assert child["parent_key"] == [top["lot_key"], top["position_key_in_proposal"]]
        assert body["manual"] == []
        assert [d["code"] for d in body["diagnostics"]] == ["unresolved_chapter_ref"] * 3
        assert set(body["category_options"][0]) == {"id", "code", "title", "is_bucket"}
        assert len(body["category_options"]) == category_count(db_session)

    def test_partial_and_conflict_carry_their_blocks_and_resolved_goes_to_manual(
        self, member_client, db_session, round_scene, admin_user
    ):
        e0, e1, e2 = round_scene.estimates
        # «14»: конфликт статей 20/20/16 с заметками; «15»: частичное 1 из 3; «14.3»: единое решение.
        for e, code, note in zip((e0, e1, e2), ("20", "20", "16"), ("а", "б", "а")):
            set_override(db_session, estimate_id=e.id, position_item_id=round_scene.chapter(e.id, "14").id,
                         work_category_id=cat(db_session, code), note=note, user_id=admin_user.id)
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "15").id,
                     work_category_id=cat(db_session, "16"), note="только у первой", user_id=admin_user.id)
        for e in (e0, e1, e2):
            set_override(db_session, estimate_id=e.id, position_item_id=round_scene.chapter(e.id, "14.3").id,
                         work_category_id=cat(db_session, "20"), note=None, user_id=admin_user.id)
        body = member_client.get(URL.format(t=round_scene.tender.id, r=round_scene.r1.id)).json()
        by = {s["number"]: s for s in body["sections"]}
        assert by["14"]["state"] == "conflict"
        assert [c["code"] for c in by["14"]["conflict"]["categories"]] == ["20", "16"]
        assert by["14"]["conflict"]["notes"] == ["а", "б"] and by["14"]["conflict"]["audit_differs"] is False
        assert by["15"]["state"] == "partial"
        assert by["15"]["partial"] == {"assigned": 1, "total": 3, "notes": ["только у первой"]}
        assert "14.3" not in by
        [manual] = body["manual"]
        assert set(manual) == {"lot_key", "position_key_in_proposal", "number", "title", "rows", "work_category_id",
                               "category_code", "category_title", "assigned_by_email", "assigned_at", "note"}
        assert (manual["number"], manual["rows"], manual["category_code"], manual["note"]) == ("14.3", 1, "20", None)
        assert manual["assigned_by_email"] == admin_user.email

    def test_unknown_tender_is_404_tender_not_found(self, member_client):
        r = member_client.get(URL.format(t=999_999, r=1))
        assert r.status_code == 404 and r.json()["detail"]["code"] == "tender_not_found"

    def test_round_of_another_tender_is_404_round_not_found(self, member_client, db_session, round_scene, factories):
        other = factories.TenderFactory.create()
        db_session.flush()
        r = member_client.get(URL.format(t=other.id, r=round_scene.r1.id))
        assert r.status_code == 404 and r.json()["detail"]["code"] == "round_not_found"

    def test_round_without_offer_estimates_is_404_with_the_spec_message(self, member_client, factories, db_session):
        tender = factories.TenderFactory.create()
        rnd = factories.TenderRoundFactory.create(tender=tender, stage_no=1)
        db_session.flush()
        r = member_client.get(URL.format(t=tender.id, r=rnd.id))
        assert r.status_code == 404
        assert r.json()["detail"] == {"code": "round_has_no_offer_estimates",
                                      "message": "Раунд или его сметы больше недоступны."}

    def test_mapping_broken_is_500_and_logged(self, member_client_no_raise, db_session, round_scene):
        """Тот же приём и та же причина, что `test_mapping_broken_is_500_not_409`
        по-сметного роутера: хендлер вешается ПРЯМО на логгер `routers.tenders`,
        потому что `setup_logging()` сносит хендлеры root (и `caplog`)."""
        e1 = round_scene.estimates[1]
        ch = round_scene.chapter(e1.id, "15")
        db_session.execute(sa.text("DELETE FROM position_items WHERE chapter_item_id = :cid"), {"cid": ch.id})
        db_session.execute(sa.text("DELETE FROM position_items WHERE id = :cid"), {"cid": ch.id})
        db_session.flush()
        captured: list[logging.LogRecord] = []

        class _Collector(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = _Collector(level=logging.ERROR)
        router_log = logging.getLogger("routers.tenders")
        router_log.addHandler(handler)
        try:
            r = member_client_no_raise.get(URL.format(t=round_scene.tender.id, r=round_scene.r1.id))
        finally:
            router_log.removeHandler(handler)
        assert r.status_code == 500
        assert any(rec.levelno == logging.ERROR for rec in captured)
```

- [ ] **Step 2: Прогнать** — `-k TestGet` красный (маршрута нет → 404 без кода / `AttributeError`).

- [ ] **Step 3: Реализация.** `crud/round_unallocated.py`:

```python
from crud.common import iso
from crud.project_passport import _category_options
from services.category_rollup import CategoryRef
from models import WorkCategory


def _category_refs(db: Session) -> list[CategoryRef]:
    """Тот же список, что собирает `get_project_passport` перед `_category_options`."""
    return [
        CategoryRef(id=c.id, code=c.code, title=c.title, parent_id=c.parent_id,
                    is_bucket=c.is_bucket, sort_order=c.sort_order)
        for c in db.execute(sa.select(WorkCategory)).scalars().all()
    ]


def _section_json(a: ru.SectionAggregate, refs_by_id: dict[int, CategoryRef]) -> dict:
    c = a.classification
    body = {
        "lot_key": a.node.key[0], "position_key_in_proposal": a.node.key[1],
        "parent_key": None if a.parent_key is None else list(a.parent_key),
        "depth": a.depth, "number": a.node.number, "title": a.node.title,
        "smr_article_raw": a.node.smr_article_raw, "rows": a.node.rows, "state": c.state,
    }
    if c.state == ru.STATE_PARTIAL:
        body["partial"] = {"assigned": c.assigned, "total": c.total, "notes": list(c.notes)}
    elif c.state == ru.STATE_CONFLICT:
        body["conflict"] = {
            "categories": [{"id": i, "code": refs_by_id[i].code, "title": refs_by_id[i].title} for i in c.categories],
            "notes": list(c.notes), "audit_differs": c.audit_differs,
        }
    return body


def _manual_json(a: ru.SectionAggregate, refs_by_id, email_of: dict[int, str]) -> dict:
    v = a.vectors[0]          # resolved: один вектор во всех
    return {
        "lot_key": a.node.key[0], "position_key_in_proposal": a.node.key[1],
        "number": a.node.number, "title": a.node.title, "rows": a.node.rows,
        "work_category_id": v.work_category_id, "category_code": refs_by_id[v.work_category_id].code,
        "category_title": refs_by_id[v.work_category_id].title,
        "assigned_by_email": email_of[v.assigned_by], "assigned_at": iso(v.assigned_at), "note": v.note,
    }


def build_round_unallocated(db: Session, tender_id: int, round_id: int) -> dict:
    scope = load_scope(db, tender_id, round_id)
    states = load_states(db, scope)
    refs = _category_refs(db)
    refs_by_id = {r.id: r for r in refs}
    authors = {a.vectors[0].assigned_by for a in states if a.classification.state == ru.STATE_RESOLVED}
    email_of = dict(db.execute(sa.select(User.id, User.email).where(User.id.in_(authors or [-1]))).all())
    rnd = scope.round
    return {
        "round": {"id": rnd.id, "stage_no": rnd.stage_no, "label": rnd.label, "held_on": iso(rnd.held_on)},
        "offers_count": len(scope.estimates),
        "sections": [_section_json(a, refs_by_id) for a in states if a.classification.state != ru.STATE_RESOLVED],
        "manual": [_manual_json(a, refs_by_id, email_of) for a in states if a.classification.state == ru.STATE_RESOLVED],
        "diagnostics": diagnostics(db, scope),
        "category_options": _category_options(refs),
    }
```

`routers/tenders.py` (импорт `from crud import round_unallocated as crud_ru`):

```python
@router.get("/{tender_id}/rounds/{round_id}/unallocated")
def round_unallocated(tender_id: int, round_id: int, db: Session = Depends(get_db)):
    """Этапный разнос: разделы раунда без единого решения (спека этапного
    разноса §2.3). Права — аутентификация роутера, `member` вправе."""
    try:
        return decimal_json(crud_ru.build_round_unallocated(db, tender_id, round_id))
    except DomainError as e:
        raise_domain_error(e)
    except crud_ru.RoundMappingBroken:
        # Порча НАШИХ данных (§2.2): логируем и роняем в 500 — повторять
        # пользователю нечего; тот же принцип, что у mapping_broken по-сметного роутера.
        log.error("Этапный разнос: проекции раунда %s расходятся", round_id, exc_info=True)
        raise
```

- [ ] **Step 4: Прогнать** — `-k TestGet` зелёный; `uv run pytest tests/test_auth_coverage.py -q`
  (новый маршрут под `dependencies=_auth_dep` роутера — тест-сторож это подтверждает).
- [ ] **Step 5: Commit**

```bash
git add backend/crud/round_unallocated.py backend/routers/tenders.py backend/tests/integration/test_round_unallocated_api.py
git commit -m "feat(round-unallocated): GET rounds/{round_id}/unallocated — состояния, разнесённое, диагностика, справочник"
```

---

### Task 5: Счётчик карточки `unallocated_pending_sections`

**Files:**
- Modify: `backend/crud/round_unallocated.py` (`pending_sections_count`)
- Modify: `backend/crud/tenders.py` (`get_tender_card`)
- Modify: `backend/routers/tenders.py` (`get_tender`: лог `RoundMappingBroken`)
- Test: `backend/tests/integration/test_round_unallocated_api.py` (класс `TestCardCounter`),
  `backend/tests/integration/test_tenders_crud.py` (набор ключей раунда)

**Interfaces:**
- Produces: `pending_sections_count(db, round_id) -> int | None` — `None` ⟺
  у раунда нет offer-смет; иначе число `SectionAggregate` со
  `state in PENDING_STATES`. Поле `rounds[*].unallocated_pending_sections` карточки.

- [ ] **Step 1: Тесты**

```python
class TestCardCounter:
    def test_counter_equals_the_aggregator_on_the_same_data(self, db_session, round_scene, admin_user):
        from crud import tenders as crud_tenders
        e0 = round_scene.estimates[0]
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "1").id,
                     work_category_id=cat(db_session, "20"), note=None, user_id=admin_user.id)   # partial у «1»
        card = crud_tenders.get_tender_card(db_session, round_scene.tender.id)
        r1 = next(r for r in card["rounds"] if r["id"] == round_scene.r1.id)
        scope = crud_ru.load_scope(db_session, round_scene.tender.id, round_scene.r1.id)
        pending = [a for a in crud_ru.load_states(db_session, scope) if a.classification.state in ru.PENDING_STATES]
        assert r1["unallocated_pending_sections"] == len(pending) == 5     # 14, 14.1, 14.3, 15 + частичная «1»

    def test_no_offer_estimates_is_null_and_disabled_structure_still_counts(self, db_session, round_scene, factories):
        from crud import tenders as crud_tenders
        empty = factories.TenderRoundFactory.create(tender=round_scene.tender, stage_no=3)
        db_session.flush()
        rounds = {r["id"]: r for r in crud_tenders.get_tender_card(db_session, round_scene.tender.id)["rounds"]}
        assert rounds[empty.id]["unallocated_pending_sections"] is None
        assert rounds[round_scene.r2.id]["unallocated_pending_sections"] == 1     # раздел «прим.» без статьи

    def test_there_is_no_second_formula(self, db_session, round_scene, monkeypatch, member_client):
        """Подмена агрегатора меняет ОБА ответа (§4.3): счётчик карточки и
        `sections` GET читают один `load_states`."""
        from crud import tenders as crud_tenders
        real = crud_ru.load_states
        monkeypatch.setattr(crud_ru, "load_states", lambda db, scope: real(db, scope)[:1])
        card = crud_tenders.get_tender_card(db_session, round_scene.tender.id)
        body = member_client.get(URL.format(t=round_scene.tender.id, r=round_scene.r1.id)).json()
        assert next(r for r in card["rounds"] if r["id"] == round_scene.r1.id)["unallocated_pending_sections"] == 1
        assert len(body["sections"]) == 1
```

В `test_tenders_crud.py::TestTenderCard`, рядом с `test_round_carries_baseline_and_current_job`:

```python
    def test_round_keys_are_the_old_ones_plus_the_counter(self, db_session, rectangular_grid):
        card = crud_tenders.get_tender_card(db_session, rectangular_grid.tender.id)
        assert set(card["rounds"][0]) == {"id", "stage_no", "label", "held_on", "latest_job", "current_job_id",
                                          "baseline_estimate_id", "baseline_total_including_vat",
                                          "unallocated_pending_sections"}
```

Значения прежних полей стерегут существующие тесты `TestTenderCard`
(они сравнивают поля буквально); этот тест — состав ключей.

- [ ] **Step 2: Прогнать** — `-k "TestCardCounter or old_ones_plus"` красный (`KeyError`).
- [ ] **Step 3: Реализация.** `crud/round_unallocated.py`:

```python
def pending_sections_count(db: Session, round_id: int) -> int | None:
    """Счётчик карточки (§2.6) — ТЕМ ЖЕ агрегатором, что GET; `None` только
    когда у раунда нет offer-смет (триггер не рисуется)."""
    estimates = offer_estimates(db, round_id)
    if not estimates:
        return None
    rnd = db.get(TenderRound, round_id)
    scope = RoundScope(tender=db.get(Tender, rnd.tender_id), round=rnd, estimates=list(estimates), contractor_title={})
    return sum(1 for a in load_states(db, scope) if a.classification.state in ru.PENDING_STATES)
```

`crud/tenders.py` — импорт `from crud import round_unallocated as crud_round_unallocated`;
в `rounds_out.append({...})` последним ключом:

```python
            # Спека этапного разноса §2.6: тем же агрегатором, что GET
            # …/unallocated; null — у раунда нет offer-смет.
            "unallocated_pending_sections": crud_round_unallocated.pending_sections_count(db, rnd.id),
```

`routers/tenders.get_tender` получает ту же ветку
`except crud_ru.RoundMappingBroken: log.error(...); raise`, что GET задачи 4
(решение плана 4: карточка падает громко, а не молчит).

- [ ] **Step 4: Прогнать** — `uv run pytest tests/integration/test_round_unallocated_api.py tests/integration/test_tenders_crud.py tests/integration/test_tenders_api.py -q` зелёные.
- [ ] **Step 5: Commit**

```bash
git add backend/crud/round_unallocated.py backend/crud/tenders.py backend/routers/tenders.py backend/tests/integration/test_round_unallocated_api.py backend/tests/integration/test_tenders_crud.py
git commit -m "feat(round-unallocated): unallocated_pending_sections у раунда карточки — тем же агрегатором, что GET"
```

---

### Task 6: Сервис записи — раундовые PUT и DELETE (`services/round_category_override.py`)

**Files:**
- Modify: `backend/services/category_override.py` (`_lock_estimate` → `lock_estimate`, докстроки)
- Create: `backend/services/round_category_override.py`
- Test: `backend/tests/integration/test_round_category_override.py`

**Interfaces:**
- Consumes: `services.category_override.lock_estimate`, `apply_overrides`,
  `ApplyResult`, `CategoryOverrideError`; `crud.round_unallocated.require_round`,
  `offer_estimates`, `RoundMappingBroken`, `CODE_NO_OFFER_ESTIMATES`,
  `NO_OFFER_ESTIMATES_MESSAGE`; `crud.common.DomainError`.
- Produces:

```python
CODE_SECTION_NOT_FOUND = "section_not_found"
CODE_CATEGORY_NOT_FOUND = "category_not_found"
CODE_NOT_A_CHAPTER = "not_a_chapter"
CODE_STRUCTURE_DISABLED = "structure_disabled"

def set_round_override(db, *, tender_id, round_id, lot_key, position_key_in_proposal,
                       work_category_id, note, user_id) -> ApplyResult
def clear_round_override(db, *, tender_id, round_id, lot_key, position_key_in_proposal) -> ApplyResult
```

Транзакцию ведёт вызывающий (роутер, задача 8). Отказы пользователя —
`DomainError` с кодом; целостность — `RoundMappingBroken`.

- [ ] **Step 1: Публичность точек входа по-сметного сервиса.** В
  `services/category_override.py` переименовать `_lock_estimate` →
  `lock_estimate` (три вызова внутри файла: `set_override`, `clear_override`,
  `apply_overrides`; `grep -rn _lock_estimate backend/` обязан дать ноль вне
  этого файла — тесты имя не упоминают), в докстроку `apply_overrides` добавить
  абзац: «Второй потребитель — `services/round_category_override.py`:
  раундовая запись пишет решения сама и зовёт этот пересчёт по каждой
  offer-смете под уже взятыми блокировками (`already_locked=True`) — закон
  материализации один на оба маршрута (спека этапного разноса §2.4 п.4)».
  Прогнать `uv run pytest tests/integration/test_category_override_apply.py tests/integration/test_category_overrides_api.py tests/integration/test_category_override_concurrency.py -q` — зелёные без правок.

- [ ] **Step 2: Тесты** — `backend/tests/integration/test_round_category_override.py`:

```python
"""Раундовая запись (спека этапного разноса §2.4, §4.2) — уровень сервиса."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from crud import round_unallocated as crud_ru
from crud.common import DomainError
from models import EstimateAdditionalWork, EstimateCategoryOverride, Lot, PositionItem, Proposal, WorkCategory
from services import round_category_override as rco
from services import round_unallocated as ru
from services.category_override import set_override

pytestmark = pytest.mark.integration


def cat(db, code):
    return db.execute(sa.select(WorkCategory.id).where(WorkCategory.code == code)).scalar_one()


def overrides_of(db, scene, number):
    return [db.get(EstimateCategoryOverride, scene.chapter(e.id, number).id) for e in scene.estimates]


def put14(db, scene, user, *, code="20", note="из плана"):
    return rco.set_round_override(db, tender_id=scene.tender.id, round_id=scene.r1.id,
                                  lot_key=scene.key14[0], position_key_in_proposal=scene.key14[1],
                                  work_category_id=cat(db, code), note=note, user_id=user.id)


def state_of(db, scene, number):
    scope = crud_ru.load_scope(db, scene.tender.id, scene.r1.id)
    return {a.node.number: a for a in crud_ru.load_states(db, scope)}[number].classification


def materialized(db, estimate_id):
    """Срез материализации одной сметы: разделы и допработы — для паритета с по-сметным сервисом."""
    chapters = db.execute(
        sa.select(PositionItem.chapter_number_in_proposal, PositionItem.work_category_id, PositionItem.category_source)
        .join(Proposal, Proposal.id == PositionItem.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id, PositionItem.is_chapter.is_(True))
        .order_by(sa.func.length(PositionItem.position_key_in_proposal), PositionItem.position_key_in_proposal)
    ).all()
    extras = db.execute(
        sa.select(EstimateAdditionalWork.chapter_ref_raw, EstimateAdditionalWork.work_category_id)
        .join(Proposal, Proposal.id == EstimateAdditionalWork.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == estimate_id).order_by(EstimateAdditionalWork.ordinal)
    ).all()
    return [tuple(r) for r in chapters], [tuple(r) for r in extras]


class TestPut:
    def test_writes_one_decision_and_one_audit_into_every_offer_estimate(self, db_session, round_scene, admin_user):
        result = put14(db_session, round_scene, admin_user)
        rows = overrides_of(db_session, round_scene, "14")
        assert all(r is not None for r in rows)
        assert {(r.work_category_id, r.note, r.assigned_by, r.assigned_at) for r in rows} == {
            (cat(db_session, "20"), "из плана", admin_user.id, rows[0].assigned_at)}
        assert state_of(db_session, round_scene, "14").state == ru.STATE_RESOLVED
        assert result.chapters_updated == 3 * 3          # «14», «14.1», «14.3» × 3 сметы
        for e in round_scene.estimates:
            assert round_scene.chapter(e.id, "14.1").work_category_id == cat(db_session, "20")

    def test_baseline_is_not_touched_by_the_same_selection(self, db_session, round_scene, admin_user):
        put14(db_session, round_scene, admin_user)
        baseline_rows = db_session.execute(
            sa.select(sa.func.count()).select_from(EstimateCategoryOverride)
            .join(PositionItem, PositionItem.id == EstimateCategoryOverride.position_item_id)
            .join(Proposal, Proposal.id == PositionItem.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == round_scene.baseline_id)
        ).scalar_one()
        assert baseline_rows == 0
        assert db_session.execute(
            sa.select(PositionItem.work_category_id).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id == round_scene.baseline_id)
        ).scalars().all() == [None]

    def test_additional_work_with_a_resolvable_ref_follows_the_decision(self, db_session, round_scene, admin_user):
        """§1.4 спеки разноса, связка, не предикат: до решения строка «14 Отделка»
        — «кандидат без статьи», после — статья решения, во ВСЕХ сметах."""
        q = sa.select(EstimateAdditionalWork.work_category_id).where(EstimateAdditionalWork.chapter_ref_raw == "14")
        assert db_session.execute(q).scalars().all() == [None, None, None]
        result = put14(db_session, round_scene, admin_user)
        assert db_session.execute(q).scalars().all() == [cat(db_session, "20")] * 3
        assert result.additional_works_updated == 3

    def test_identical_repeat_is_a_no_op_that_keeps_the_audit(self, db_session, round_scene, admin_user, factories):
        put14(db_session, round_scene, admin_user)
        db_session.execute(sa.text("UPDATE estimate_category_overrides SET assigned_at = now() - interval '1 day'"))
        other = factories.UserFactory.create()
        put14(db_session, round_scene, other)                       # то же (статья, заметка), другой автор
        rows = overrides_of(db_session, round_scene, "14")
        assert {r.assigned_by for r in rows} == {admin_user.id}
        now = db_session.execute(sa.text("select now()")).scalar_one()
        assert all(r.assigned_at < now for r in rows)

    def test_same_vectors_but_a_new_article_is_a_rewrite(self, db_session, round_scene, admin_user, factories):
        """Негативный (а) к предикату no-op: без второго условия выбор новой
        статьи при согласованном старом решении сошёл бы за no-op."""
        put14(db_session, round_scene, admin_user)
        other = factories.UserFactory.create()
        put14(db_session, round_scene, other, code="16")
        rows = overrides_of(db_session, round_scene, "14")
        assert {(r.work_category_id, r.assigned_by) for r in rows} == {(cat(db_session, "16"), other.id)}

    def test_vectors_differing_only_in_audit_are_rewritten_and_aligned(self, db_session, round_scene, admin_user, factories):
        """Негативный (б): статья и заметка совпадают с телом, аудит разошёлся —
        перезапись выравнивает автора и время во всех сметах."""
        put14(db_session, round_scene, admin_user)
        victim = round_scene.chapter(round_scene.estimates[1].id, "14").id
        db_session.execute(sa.text("UPDATE estimate_category_overrides SET assigned_at = now() - interval '1 day' "
                                   "WHERE position_item_id = :rid"), {"rid": victim})
        assert state_of(db_session, round_scene, "14").state == ru.STATE_CONFLICT
        other = factories.UserFactory.create()
        put14(db_session, round_scene, other)
        rows = overrides_of(db_session, round_scene, "14")
        assert {(r.assigned_by, r.assigned_at) for r in rows} == {(other.id, rows[0].assigned_at)}
        assert state_of(db_session, round_scene, "14").state == ru.STATE_RESOLVED

    def test_note_null_clears_notes_everywhere_and_a_new_note_lands_everywhere(self, db_session, round_scene, admin_user):
        put14(db_session, round_scene, admin_user, note="старая")
        put14(db_session, round_scene, admin_user, note=None)
        assert {r.note for r in overrides_of(db_session, round_scene, "14")} == {None}
        put14(db_session, round_scene, admin_user, note="новая")
        assert {r.note for r in overrides_of(db_session, round_scene, "14")} == {"новая"}

    def test_partial_and_conflict_are_aligned_by_one_decision(self, db_session, round_scene, admin_user):
        e0 = round_scene.estimates[0]
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "14").id,
                     work_category_id=cat(db_session, "16"), note="чужая", user_id=admin_user.id)
        assert state_of(db_session, round_scene, "14").state == ru.STATE_PARTIAL
        put14(db_session, round_scene, admin_user)
        assert state_of(db_session, round_scene, "14").state == ru.STATE_RESOLVED

    def test_parity_with_the_by_estimate_service(self, db_session, round_scene, admin_user, factories):
        """Пересчёт — ТЕМ ЖЕ кодом (§2.4 п.4): смета, разнесённая по-сметным
        `set_override`, и смета, разнесённая раундовым PUT, материализованы
        построчно одинаково (разделы и допработы). Автор раундового PUT —
        другой пользователь, иначе для первой сметы сработал бы no-op."""
        e0, e1, _ = round_scene.estimates
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "14").id,
                     work_category_id=cat(db_session, "20"), note=None, user_id=admin_user.id)
        expected = materialized(db_session, e0.id)
        put14(db_session, round_scene, factories.UserFactory.create(), note=None)
        assert materialized(db_session, e1.id) == expected
        assert materialized(db_session, e0.id) == expected


class TestKeyResolution:
    def _put(self, db, scene, user, lot_key, key, *, round_id=None, code="20"):
        return rco.set_round_override(db, tender_id=scene.tender.id, round_id=round_id or scene.r1.id,
                                      lot_key=lot_key, position_key_in_proposal=key,
                                      work_category_id=cat(db, code), note=None, user_id=user.id)

    def test_key_absent_everywhere_is_section_not_found(self, db_session, round_scene, admin_user):
        with pytest.raises(DomainError) as e:
            self._put(db_session, round_scene, admin_user, "lot_1", "999")
        assert (e.value.status_code, e.value.code) == (404, rco.CODE_SECTION_NOT_FOUND)

    def test_key_present_in_some_estimates_is_mapping_broken(self, db_session, round_scene, admin_user):
        e1 = round_scene.estimates[1]
        ch = round_scene.chapter(e1.id, "15")
        db_session.execute(sa.text("DELETE FROM position_items WHERE chapter_item_id = :cid"), {"cid": ch.id})
        db_session.execute(sa.text("DELETE FROM position_items WHERE id = :cid"), {"cid": ch.id})
        db_session.flush()
        with pytest.raises(crud_ru.RoundMappingBroken):
            self._put(db_session, round_scene, admin_user, *round_scene.key15)

    def test_key_of_a_position_everywhere_is_not_a_chapter(self, db_session, round_scene, admin_user):
        work_key = db_session.execute(
            sa.select(PositionItem.position_key_in_proposal).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == round_scene.estimates[0].id, PositionItem.is_chapter.is_(False)).limit(1)
        ).scalar_one()
        with pytest.raises(DomainError) as e:
            self._put(db_session, round_scene, admin_user, "lot_1", work_key)
        assert (e.value.status_code, e.value.code) == (422, rco.CODE_NOT_A_CHAPTER)

    def test_is_chapter_differing_between_estimates_is_mapping_broken(self, db_session, round_scene, admin_user):
        ch = round_scene.chapter(round_scene.estimates[2].id, "15")
        db_session.execute(sa.text("DELETE FROM position_items WHERE chapter_item_id = :cid"), {"cid": ch.id})
        db_session.execute(sa.text("UPDATE position_items SET is_chapter = false, smr_article_raw = NULL, "
                                   "work_category_id = NULL, category_source = NULL WHERE id = :cid"), {"cid": ch.id})
        db_session.flush()
        with pytest.raises(crud_ru.RoundMappingBroken):
            self._put(db_session, round_scene, admin_user, *round_scene.key15)

    def test_unknown_category_is_category_not_found(self, db_session, round_scene, admin_user):
        with pytest.raises(DomainError) as e:
            rco.set_round_override(db_session, tender_id=round_scene.tender.id, round_id=round_scene.r1.id,
                                   lot_key=round_scene.key14[0], position_key_in_proposal=round_scene.key14[1],
                                   work_category_id=10**9, note=None, user_id=admin_user.id)
        assert (e.value.status_code, e.value.code) == (404, rco.CODE_CATEGORY_NOT_FOUND)

    def test_disabled_structure_is_409(self, db_session, round_scene, admin_user):
        e = crud_ru.offer_estimates(db_session, round_scene.r2.id)[0]
        key = db_session.execute(
            sa.select(PositionItem.position_key_in_proposal).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id == e.id, PositionItem.is_chapter.is_(True))
        ).scalar_one()
        with pytest.raises(DomainError) as err:
            self._put(db_session, round_scene, admin_user, "lot_1", key, round_id=round_scene.r2.id)
        assert (err.value.status_code, err.value.code) == (409, rco.CODE_STRUCTURE_DISABLED)

    def test_round_without_offer_estimates_is_404_before_anything_else(self, db_session, round_scene, admin_user, factories):
        empty = factories.TenderRoundFactory.create(tender=round_scene.tender, stage_no=3)
        db_session.flush()
        with pytest.raises(DomainError) as e:
            self._put(db_session, round_scene, admin_user, "lot_1", "1", round_id=empty.id)
        assert e.value.code == crud_ru.CODE_NO_OFFER_ESTIMATES


class TestDelete:
    def _delete(self, db, scene, key):
        return rco.clear_round_override(db, tender_id=scene.tender.id, round_id=scene.r1.id,
                                        lot_key=key[0], position_key_in_proposal=key[1])

    def test_removes_the_decision_from_every_estimate_and_returns_extras(self, db_session, round_scene, admin_user):
        put14(db_session, round_scene, admin_user)
        result = self._delete(db_session, round_scene, round_scene.key14)
        assert overrides_of(db_session, round_scene, "14") == [None, None, None]
        assert result.chapters_updated == 9 and result.additional_works_updated == 3
        assert state_of(db_session, round_scene, "14").state == ru.STATE_UNASSIGNED

    def test_aligns_partial_and_conflict_to_no_decision(self, db_session, round_scene, admin_user):
        e0 = round_scene.estimates[0]
        set_override(db_session, estimate_id=e0.id, position_item_id=round_scene.chapter(e0.id, "14").id,
                     work_category_id=cat(db_session, "16"), note=None, user_id=admin_user.id)
        self._delete(db_session, round_scene, round_scene.key14)
        assert overrides_of(db_session, round_scene, "14") == [None, None, None]

    def test_no_decision_anywhere_is_a_no_op_not_an_error(self, db_session, round_scene):
        result = self._delete(db_session, round_scene, round_scene.key14)
        assert result.chapters_updated == 0

    def test_key_absent_everywhere_is_section_not_found_not_a_no_op(self, db_session, round_scene):
        """Коллизия, найденная ревью гейта 2: таблица исходов ключа идёт ДО no-op."""
        with pytest.raises(DomainError) as e:
            self._delete(db_session, round_scene, ("lot_1", "999"))
        assert e.value.code == rco.CODE_SECTION_NOT_FOUND


class TestAtomicity:
    def test_a_failure_on_the_last_estimate_leaves_no_estimate_changed(self, db_session, round_scene, admin_user, monkeypatch):
        """Искусственный отказ пересчёта на ПОСЛЕДНЕЙ смете: транзакцию ведёт
        вызывающий, поэтому проверяется, что после `rollback()` ни одна смета не
        несёт решения (§2.4 п.5). `db_session.commit()` до действия фиксирует
        границу савпоинта — как в `test_a_refused_put_rolls_back`."""
        import services.round_category_override as module
        real = module.apply_overrides
        last = round_scene.estimate_ids[-1]

        def failing(db, estimate_id, **kw):
            if estimate_id == last:
                raise RuntimeError("искусственный отказ на последней смете")
            return real(db, estimate_id, **kw)

        monkeypatch.setattr(module, "apply_overrides", failing)
        db_session.commit()
        with pytest.raises(RuntimeError):
            put14(db_session, round_scene, admin_user)
        db_session.rollback()
        assert db_session.execute(sa.select(sa.func.count()).select_from(EstimateCategoryOverride)).scalar_one() == 0
        assert db_session.execute(
            sa.select(sa.func.count()).select_from(PositionItem).where(PositionItem.category_source == "manual")
        ).scalar_one() == 0
```

- [ ] **Step 3: Прогнать** — `uv run pytest tests/integration/test_round_category_override.py -q` — `ImportError`.

- [ ] **Step 4: Реализация** `backend/services/round_category_override.py`:

```python
"""Раундовая запись решений о статье раздела (спека этапного разноса §2.4).

Единица — логический раздел `(lot_key, position_key_in_proposal)`, радиус —
все offer-сметы раунда. `set_override` по-сметного сервиса НЕ переиспользуется:
его no-op сохранил бы старых авторов там, где содержимое совпало, и аудит
раунда остался бы разнородным (§1.3). Решения пишутся здесь, пересчёт — тот
же `apply_overrides`, что у по-сметного маршрута.

Порядок: блокировки tender → round → сметы по `estimate_id ASC` (§2.4 п.1) →
разрешение ключа во ВСЕХ сметах по таблице §2.4 п.2 → предикат no-op → запись
→ пересчёт каждой сметы. Транзакцию ведёт роутер.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from crud.common import DomainError
from crud.round_unallocated import (
    CODE_NO_OFFER_ESTIMATES, NO_OFFER_ESTIMATES_MESSAGE, RoundMappingBroken, offer_estimates, require_round,
)
from models import EstimateCategoryOverride, Lot, PositionItem, Proposal, Tender, TenderRound, WorkCategory
from services.category_override import ApplyResult, CategoryOverrideError, apply_overrides, lock_estimate

CODE_SECTION_NOT_FOUND = "section_not_found"
CODE_CATEGORY_NOT_FOUND = "category_not_found"
CODE_NOT_A_CHAPTER = "not_a_chapter"
CODE_STRUCTURE_DISABLED = "structure_disabled"


def _lock_scope(db: Session, tender_id: int, round_id: int) -> list[int]:
    """tender FOR KEY SHARE → round FOR KEY SHARE → offer-сметы FOR UPDATE по
    `estimate_id ASC`. KEY SHARE совместим с параллельным раундовым писателем и
    конфликтует с FOR UPDATE замены/удаления раунда (`import_round`,
    `delete_round`, `delete_participant`) — те ждут нас, мы их. После их
    коммита смет может не остаться — это `round_has_no_offer_estimates` (§1.5)."""
    rnd = require_round(db, tender_id, round_id)
    # ИМЕННО `read=True, key_share=True`: это компилируется в `FOR KEY SHARE`.
    # Одно `key_share=True` даёт `FOR NO KEY UPDATE` (проверено компиляцией под
    # диалект PostgreSQL 01.09.2026, находка внешнего ревью плана), а он
    # НЕсовместим сам с собой — второй раундовый писатель встал бы уже на
    # тендере, и блокировки смет ниже перестали бы быть тем, что его держит.
    db.execute(sa.select(Tender.id).where(Tender.id == tender_id)
               .with_for_update(read=True, key_share=True)).scalar_one()
    db.execute(sa.select(TenderRound.id).where(TenderRound.id == rnd.id)
               .with_for_update(read=True, key_share=True)).scalar_one()
    ids = [e.id for e in offer_estimates(db, round_id)]      # ORDER BY Estimate.id — единственный источник порядка
    if not ids:
        raise DomainError(404, NO_OFFER_ESTIMATES_MESSAGE, code=CODE_NO_OFFER_ESTIMATES)
    for estimate_id in ids:
        try:
            lock_estimate(db, estimate_id)
        except CategoryOverrideError as exc:     # смета исчезла под нашим KEY SHARE — недостижимо
            raise RoundMappingBroken(str(exc)) from exc
    return ids


def _resolve_key(db: Session, estimate_ids: list[int], lot_key: str, position_key: str) -> dict[int, PositionItem]:
    """Таблица исходов §2.4 п.2 — разбиение полное."""
    rows = db.execute(
        sa.select(Lot.estimate_id, PositionItem)
        .join(Proposal, Proposal.id == PositionItem.proposal_id).join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id.in_(estimate_ids), Lot.lot_key == lot_key,
               PositionItem.position_key_in_proposal == position_key)
    ).all()
    found = {estimate_id: row for estimate_id, row in rows}
    if not found:
        raise DomainError(404, f"Раздел «{lot_key}/{position_key}» не найден ни в одной смете раунда.",
                          code=CODE_SECTION_NOT_FOUND)
    if set(found) != set(estimate_ids):
        raise RoundMappingBroken(f"Ключ «{lot_key}/{position_key}» есть в сметах {sorted(found)} и отсутствует в "
                                 f"{sorted(set(estimate_ids) - set(found))}: проекции раунда разошлись.")
    kinds = {row.is_chapter for row in found.values()}
    if len(kinds) > 1:
        raise RoundMappingBroken(f"Ключ «{lot_key}/{position_key}»: is_chapter различается между сметами раунда.")
    if kinds == {False}:
        raise DomainError(422, f"Строка «{lot_key}/{position_key}» не является разделом: статья привязывается "
                               "только к строкам-разделам.", code=CODE_NOT_A_CHAPTER)
    return found


def _recompute(db: Session, estimate_ids: list[int]) -> ApplyResult:
    chapters = extras = manual = 0
    for estimate_id in estimate_ids:
        try:
            r = apply_overrides(db, estimate_id, already_locked=True)
        except CategoryOverrideError as exc:
            if exc.code == "structure_disabled":
                raise DomainError(409, str(exc), code=CODE_STRUCTURE_DISABLED) from exc
            raise RoundMappingBroken(str(exc)) from exc     # mapping_broken, отсутствие raw_data
        chapters += r.chapters_updated
        extras += r.additional_works_updated
        manual += r.chapters_manual
    return ApplyResult(chapters, extras, manual)


def set_round_override(db: Session, *, tender_id: int, round_id: int, lot_key: str,
                       position_key_in_proposal: str, work_category_id: int, note: str | None,
                       user_id: int) -> ApplyResult:
    ids = _lock_scope(db, tender_id, round_id)
    chapters = _resolve_key(db, ids, lot_key, position_key_in_proposal)
    if db.execute(sa.select(WorkCategory.id).where(WorkCategory.id == work_category_id)).scalar_one_or_none() is None:
        raise DomainError(404, f"Статья {work_category_id} не найдена в классификаторе.", code=CODE_CATEGORY_NOT_FOUND)
    current = [db.get(EstimateCategoryOverride, chapters[eid].id) for eid in ids]
    vectors = [None if o is None else (o.work_category_id, o.note, o.assigned_by, o.assigned_at) for o in current]
    # Предикат no-op — ОБА условия (§2.4 п.3): векторы одинаковы между собой И
    # их (статья, заметка) совпадают с телом. Решения и аудит не трогаются;
    # пересчёт идёт всегда, как у по-сметного `clear_override`.
    if all(v is not None for v in vectors) and len(set(vectors)) == 1 and vectors[0][:2] == (work_category_id, note):
        return _recompute(db, ids)
    for eid, existing in zip(ids, current):
        if existing is None:
            db.add(EstimateCategoryOverride(position_item_id=chapters[eid].id, work_category_id=work_category_id,
                                            note=note, assigned_by=user_id, assigned_at=sa.func.now()))
        else:
            existing.work_category_id = work_category_id
            existing.note = note
            existing.assigned_by = user_id
            existing.assigned_at = sa.func.now()    # одно now() транзакции на все сметы — единый аудит
    db.flush()
    return _recompute(db, ids)


def clear_round_override(db: Session, *, tender_id: int, round_id: int, lot_key: str,
                         position_key_in_proposal: str) -> ApplyResult:
    ids = _lock_scope(db, tender_id, round_id)
    chapters = _resolve_key(db, ids, lot_key, position_key_in_proposal)   # ДО no-op (§2.4, коллизия ревью гейта 2)
    for eid in ids:
        existing = db.get(EstimateCategoryOverride, chapters[eid].id)
        if existing is not None:
            db.delete(existing)
    db.flush()
    return _recompute(db, ids)
```

`assigned_at=sa.func.now()` на INSERT задан явно, а не серверным умолчанием
(`models._created_at` — тоже `now()`), чтобы обе ветви писали ОДНО выражение.

- [ ] **Step 5: Прогнать** — `uv run pytest tests/integration/test_round_category_override.py tests/integration/test_category_override_apply.py tests/integration/test_category_overrides_api.py -q` зелёные.
- [ ] **Step 6: Commit**

```bash
git add backend/services/category_override.py backend/services/round_category_override.py backend/tests/integration/test_round_category_override.py
git commit -m "feat(round-unallocated): раундовые PUT/DELETE — блокировки по порядку, таблица исходов ключа, no-op из двух условий, единый аудит"
```

---

### Task 7: Конкуренция — блокировка смет и её порядок

Мера, которую даёт `_lock_scope`: второй раундовый писатель ЖДЁТ первого, а
не пересчитывает поверх незакоммиченного набора решений; порядок захвата
смет — один у обоих писателей. Приём — `test_category_override_concurrency.py`
(настоящие транзакции на `committing_session_factory`, ожидание замка через
`pg_stat_activity`, хелпер копируется, не импортируется — так заведено там).

**Files:**
- Create: `backend/tests/integration/test_round_category_override_concurrency.py`

**Interfaces:**
- Consumes: `services.round_category_override.set_round_override`, `lock_estimate`
  (через модуль `rco` — для шпиона порядка); `services.round_import.import_round`;
  `committing_db`, `committing_factories`, `committing_session_factory`.

- [ ] **Step 1: Тесты**

```python
"""Конкуренция раундовой записи (спека этапного разноса §2.4 п.1, §4.2).

Что чем доказывается — как в `test_category_override_concurrency.py`: без
блокировок смет второй писатель НЕ встаёт на ожидание вовсе, и ассерт
`_wait_until_a_backend_blocks` красный именно и только в этом случае (строки
`position_items`, которые трогают два разных раздела, не пересекаются, и
конфликта уровня строки нет). Порядок захвата стережёт ШПИОН на
`lock_estimate` — утверждение о потоке данных (`docs/insights/
data-flow-assertions-for-order.md`), а не результат.
"""
from __future__ import annotations

import threading
import time

import pytest
import sqlalchemy as sa

from models import Estimate, EstimateCategoryOverride, Lot, Offer, PositionItem, Proposal, UserRole, WorkCategory
from services import round_category_override as rco
from services.category_resolution import CategoryResolver
from services.round_import import import_round
from services.unit_resolution import UnitResolver
from tests.payloads import position, proposal, round_payload

pytestmark = pytest.mark.integration


def _statement(inn, title):
    return proposal([
        position(job_title="SHELL & CORE", is_chapter=True, chapter_number="14", number="1"),
        position(job_title="Работа 14", unit="м2", quantity=1, suggested_quantity=1,
                 unit_cost_total="30.00", total_cost_total="30.00", chapter_ref="14", number="2"),
        position(job_title="Рабочая документация", is_chapter=True, chapter_number="15", number="3"),
        position(job_title="Работа 15", unit="м2", quantity=1, suggested_quantity=1,
                 unit_cost_total="10.00", total_cost_total="10.00", chapter_ref="15", number="4"),
    ], inn=inn, title=title)


@pytest.fixture
def scene(committing_db, committing_factories, committing_session_factory):
    tender = committing_factories.TenderFactory.create()
    rnd = committing_factories.TenderRoundFactory.create(tender=tender, stage_no=1)
    committing_db.flush()
    import_round(committing_db, tender_round=rnd,
                 data=round_payload([_statement("7700000001", "ООО А"), _statement("7700000002", "ООО Б"),
                                     _statement("7700000003", "ООО В")]),
                 parser_version="4.0.0", import_job_id=None, replace=False,
                 unit_resolver=UnitResolver(committing_db), category_resolver=CategoryResolver.from_db(committing_db))
    first = committing_factories.UserFactory.create(role=UserRole.admin)
    second = committing_factories.UserFactory.create(role=UserRole.admin)
    used_as_parent = sa.select(WorkCategory.parent_id).where(WorkCategory.parent_id.is_not(None))
    cat_a, cat_b = committing_db.execute(
        sa.select(WorkCategory.id).where(WorkCategory.id.not_in(used_as_parent)).order_by(WorkCategory.sort_order).limit(2)
    ).scalars().all()
    committing_db.commit()
    ids = committing_db.execute(
        sa.select(Estimate.id).join(Offer, Offer.id == Estimate.offer_id).where(Offer.round_id == rnd.id).order_by(Estimate.id)
    ).scalars().all()

    class Scene:
        session_factory = committing_session_factory
        tender_id, round_id = tender.id, rnd.id
        estimate_ids = list(ids)
        first_id, second_id = first.id, second.id
        category_a, category_b = cat_a, cat_b
        key14, key15 = ("lot_1", "1"), ("lot_1", "3")
    return Scene()


def _wait_until_a_backend_blocks(session_factory, *, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    with session_factory() as probe:
        while time.monotonic() < deadline:
            blocked = probe.execute(sa.text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar_one()
            probe.rollback()
            if blocked:
                return True
            time.sleep(0.05)
    return False


def _put(session, scene, key, category, user_id):
    return rco.set_round_override(session, tender_id=scene.tender_id, round_id=scene.round_id,
                                  lot_key=key[0], position_key_in_proposal=key[1],
                                  work_category_id=category, note=None, user_id=user_id)


class TestTwoRoundWriters:
    def test_the_second_writer_waits_for_the_first_and_both_land(self, scene):
        outcome: dict[str, object] = {}
        started = threading.Event()

        def second_writer():
            with scene.session_factory() as s:
                started.set()
                try:
                    _put(s, scene, scene.key15, scene.category_b, scene.second_id)
                    s.commit()
                    outcome["done"] = True
                except Exception as exc:  # pragma: no cover — диагностика
                    s.rollback()
                    outcome["error"] = f"{type(exc).__name__}: {exc}"

        with scene.session_factory() as first:
            _put(first, scene, scene.key14, scene.category_a, scene.first_id)
            t = threading.Thread(target=second_writer, daemon=True)
            t.start()
            assert started.wait(timeout=10)
            # МЕХАНИЗМ: без FOR UPDATE по сметам второй писатель не встаёт на замок вовсе.
            assert _wait_until_a_backend_blocks(scene.session_factory), "второй писатель не ждёт первого"
            first.commit()
        t.join(timeout=20)
        assert not t.is_alive() and outcome == {"done": True}, outcome

        with scene.session_factory() as check:
            decided = check.execute(
                sa.select(PositionItem.position_key_in_proposal, sa.func.count())
                .join(EstimateCategoryOverride, EstimateCategoryOverride.position_item_id == PositionItem.id)
                .group_by(PositionItem.position_key_in_proposal)
            ).all()
            assert dict(decided) == {"1": 3, "3": 3}           # оба решения — во всех трёх сметах

    def test_estimates_are_locked_in_ascending_id_order(self, scene, monkeypatch):
        """Утверждение о ПОРЯДКЕ потока: шпион на `lock_estimate` записывает
        id в порядке захвата. Один тотальный порядок у обоих писателей — то,
        что спасает от взаимоблокировки параллельных PUT (§2.4 п.1)."""
        seen: list[int] = []
        real = rco.lock_estimate

        def spy(db, estimate_id):
            seen.append(estimate_id)
            return real(db, estimate_id)

        monkeypatch.setattr(rco, "lock_estimate", spy)
        with scene.session_factory() as s:
            _put(s, scene, scene.key14, scene.category_a, scene.first_id)
            s.rollback()
        assert seen == sorted(scene.estimate_ids)
        assert len(seen) == 3
```

- [ ] **Step 2: Прогнать** — оба зелёные на реализации задачи 6:
  `uv run pytest tests/integration/test_round_category_override_concurrency.py -q`.

- [ ] **Step 3: Негативные проверки — ОРКЕСТРАТОР ЛИЧНО**, каждая по протоколу
  `verifying-guards.md` (контрольный прогон до снятия; убедиться `grep`-ом, что
  снятие легло; красный; восстановить; снова зелёный):
  1. Закомментировать цикл `for estimate_id in ids: lock_estimate(...)` в
     `_lock_scope` → `test_the_second_writer_waits_for_the_first_and_both_land`
     обязан краснеть на ассерте «второй писатель не ждёт первого».
     Предпосылка этого красного: блокировки tender и round выше цикла —
     настоящий `FOR KEY SHARE` (`with_for_update(read=True, key_share=True)`),
     совместимый сам с собой. С `FOR NO KEY UPDATE` (одно `key_share=True`)
     второй писатель ждал бы уже на тендере, снятие цикла ничего бы не
     уронило, и зелёный означал бы «держит другой замок», а не «держит наш»
     (находка внешнего ревью плана 01.09.2026; слой 8 `verifying-guards.md`).
     Перед снятием защиты сверить компиляцию: `str(select(...).with_for_update(
     read=True, key_share=True).compile(dialect=postgresql.dialect()))`
     оканчивается на `FOR KEY SHARE`.
  2. В `crud/round_unallocated.offer_estimates` заменить `.order_by(Estimate.id)`
     на `.order_by(Estimate.id.desc())` → `test_estimates_are_locked_in_ascending_id_order`
     красный. (Просто УБРАТЬ `order_by` — снятие, которое тест не ловит: без
     сортировки индексный доступ обычно отдаёт id по возрастанию; это
     граница теста, назвать её в докстроке.)
  3. Демонстрация, ЗАЧЕМ порядок один: разовый скрипт в scratchpad — две
     сессии берут `SELECT … FOR UPDATE` смет `e1, e2` против `e2, e1` с
     барьером между захватами → PostgreSQL поднимает `DeadlockDetected` в
     одной из них. Вывод — в devlog; в тесты не кладётся (это свойство СУБД,
     не нашего кода).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_round_category_override_concurrency.py
git commit -m "test(round-unallocated): второй раундовый писатель ждёт первого; порядок захвата смет — по id"
```

---

### Task 8: HTTP-слой — `PUT`/`DELETE …/rounds/{round_id}/category-overrides`

**Files:**
- Modify: `backend/routers/tenders.py`
- Test: `backend/tests/integration/test_round_category_override.py` (класс `TestHttp`)

**Interfaces:**
- Consumes: `services.round_category_override.set_round_override`, `clear_round_override`;
  `crud.round_unallocated.RoundMappingBroken`; `auth.get_current_user`.
- Produces:

```python
class RoundOverridePut(BaseModel):
    lot_key: str
    position_key_in_proposal: str
    work_category_id: int
    note: str | None = Field(max_length=2000)    # ОБЯЗАТЕЛЬНОЕ nullable: без default поле required, null принимается

class RoundOverrideDelete(BaseModel):
    lot_key: str
    position_key_in_proposal: str

PUT    /api/v1/tenders/{tender_id}/rounds/{round_id}/category-overrides   → {"chapters_updated", "additional_works_updated", "chapters_manual"}
DELETE /api/v1/tenders/{tender_id}/rounds/{round_id}/category-overrides   (тело RoundOverrideDelete) → те же три ключа
```

Транзакцию ведёт роутер: `commit` на успехе, `rollback` на любом отказе
(как `_apply` по-сметного роутера). `DomainError` → `raise_domain_error`;
`RoundMappingBroken` → `log.error(..., exc_info=True)` и голый `raise` (500).

- [ ] **Step 1: Тесты** (в `test_round_category_override.py`; `import logging` в шапку):

```python
OVERRIDES = "/api/v1/tenders/{t}/rounds/{r}/category-overrides"


def body14(scene, code_id, note="из http"):
    return {"lot_key": scene.key14[0], "position_key_in_proposal": scene.key14[1],
            "work_category_id": code_id, "note": note}


class TestHttp:
    def test_member_puts_and_gets_the_three_key_summary(self, member_client, db_session, round_scene):
        r = member_client.put(OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id),
                              json=body14(round_scene, cat(db_session, "20")))
        assert r.status_code == 200, r.text
        assert set(r.json()) == {"chapters_updated", "additional_works_updated", "chapters_manual"}
        assert r.json()["chapters_updated"] == 9
        assert {o.assigned_by for o in overrides_of(db_session, round_scene, "14")} == {member_client.user.id}

    def test_note_omitted_is_422_and_note_null_is_accepted(self, member_client, db_session, round_scene):
        url = OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id)
        without = {k: v for k, v in body14(round_scene, cat(db_session, "20")).items() if k != "note"}
        assert member_client.put(url, json=without).status_code == 422          # omitted ≠ null (§2.4)
        assert member_client.put(url, json=body14(round_scene, cat(db_session, "20"), note=None)).status_code == 200
        assert {o.note for o in overrides_of(db_session, round_scene, "14")} == {None}

    @pytest.mark.parametrize("patch, status, code", [
        ({"position_key_in_proposal": "999"}, 404, "section_not_found"),
        ({"work_category_id": 10**9}, 404, "category_not_found"),
    ])
    def test_refusal_codes(self, member_client, db_session, round_scene, patch, status, code):
        body = {**body14(round_scene, cat(db_session, "20")), **patch}
        r = member_client.put(OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id), json=body)
        assert (r.status_code, r.json()["detail"]["code"]) == (status, code)

    def test_position_key_is_422_not_a_chapter(self, member_client, db_session, round_scene):
        work_key = db_session.execute(
            sa.select(PositionItem.position_key_in_proposal).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id)
            .where(Lot.estimate_id == round_scene.estimates[0].id, PositionItem.is_chapter.is_(False)).limit(1)
        ).scalar_one()
        body = {**body14(round_scene, cat(db_session, "20")), "position_key_in_proposal": work_key}
        r = member_client.put(OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id), json=body)
        assert (r.status_code, r.json()["detail"]["code"]) == (422, "not_a_chapter")

    def test_disabled_structure_is_409_and_the_decision_is_rolled_back(self, member_client, db_session, round_scene):
        """Как `test_a_refused_put_rolls_back`: решение флешнуто ДО того, как
        пересчёт поднял `structure_disabled`; без `rollback()` в роутере строка
        осталась бы. `db_session.commit()` фиксирует границу савпоинта."""
        db_session.commit()
        e = crud_ru.offer_estimates(db_session, round_scene.r2.id)[0]
        key = db_session.execute(
            sa.select(PositionItem.position_key_in_proposal).join(Proposal, Proposal.id == PositionItem.proposal_id)
            .join(Lot, Lot.id == Proposal.lot_id).where(Lot.estimate_id == e.id, PositionItem.is_chapter.is_(True))
        ).scalar_one()
        r = member_client.put(OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r2.id),
                              json={"lot_key": "lot_1", "position_key_in_proposal": key,
                                    "work_category_id": cat(db_session, "20"), "note": None})
        assert (r.status_code, r.json()["detail"]["code"]) == (409, "structure_disabled")
        assert db_session.execute(sa.select(sa.func.count()).select_from(EstimateCategoryOverride)).scalar_one() == 0

    def test_mapping_broken_is_500_and_logged(self, member_client_no_raise, db_session, round_scene):
        e1 = round_scene.estimates[1]
        ch = round_scene.chapter(e1.id, "15")
        db_session.execute(sa.text("DELETE FROM position_items WHERE chapter_item_id = :cid"), {"cid": ch.id})
        db_session.execute(sa.text("DELETE FROM position_items WHERE id = :cid"), {"cid": ch.id})
        db_session.flush()
        captured: list[logging.LogRecord] = []

        class _Collector(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = _Collector(level=logging.ERROR)
        router_log = logging.getLogger("routers.tenders")
        router_log.addHandler(handler)
        try:
            r = member_client_no_raise.put(OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id),
                                           json={"lot_key": round_scene.key15[0], "position_key_in_proposal": round_scene.key15[1],
                                                 "work_category_id": cat(db_session, "20"), "note": None})
        finally:
            router_log.removeHandler(handler)
        assert r.status_code == 500
        assert any(rec.levelno == logging.ERROR for rec in captured)

    def test_delete_with_a_body_clears_everywhere_and_missing_key_is_404(self, member_client, db_session, round_scene):
        url = OVERRIDES.format(t=round_scene.tender.id, r=round_scene.r1.id)
        assert member_client.put(url, json=body14(round_scene, cat(db_session, "20"))).status_code == 200
        r = member_client.request("DELETE", url, json={"lot_key": round_scene.key14[0],
                                                       "position_key_in_proposal": round_scene.key14[1]})
        assert r.status_code == 200 and set(r.json()) == {"chapters_updated", "additional_works_updated", "chapters_manual"}
        assert overrides_of(db_session, round_scene, "14") == [None, None, None]
        r = member_client.request("DELETE", url, json={"lot_key": "lot_1", "position_key_in_proposal": "999"})
        assert (r.status_code, r.json()["detail"]["code"]) == (404, "section_not_found")
```

- [ ] **Step 2: Прогнать** — `-k TestHttp` красный (405/404 маршрута).

- [ ] **Step 3: Реализация** (`routers/tenders.py`; импорты `Field`, `get_current_user` уже есть,
  добавить `from services import round_category_override as rco`):

```python
class RoundOverridePut(BaseModel):
    lot_key: str
    position_key_in_proposal: str
    work_category_id: int
    # ОБЯЗАТЕЛЬНОЕ nullable (спека этапного разноса §2.4): семантики «omitted»
    # нет, тело всегда объявляет заметку целиком, явный null — явная очистка.
    note: str | None = Field(max_length=2000)


class RoundOverrideDelete(BaseModel):
    lot_key: str
    position_key_in_proposal: str


def _round_override(db: Session, round_id: int, action, **kwargs):
    """Транзакцию ведёт роутер (§2.4): commit на успехе, rollback на любом отказе.
    `RoundMappingBroken` — порча наших данных: лог и 500, как `mapping_broken`
    по-сметного роутера."""
    try:
        result = action(db, **kwargs)
        db.commit()
    except DomainError as e:
        db.rollback()
        raise_domain_error(e)
    except crud_ru.RoundMappingBroken:
        db.rollback()
        log.error("Этапный разнос: проекции раунда %s расходятся", round_id, exc_info=True)
        raise
    except Exception:
        db.rollback()
        raise
    return {"chapters_updated": result.chapters_updated,
            "additional_works_updated": result.additional_works_updated,
            "chapters_manual": result.chapters_manual}


@router.put("/{tender_id}/rounds/{round_id}/category-overrides")
def put_round_override(tender_id: int, round_id: int, body: RoundOverridePut, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """Единое решение по логическому разделу во ВСЕХ offer-сметах раунда (§2.4). `member` вправе."""
    return _round_override(db, round_id, rco.set_round_override, tender_id=tender_id, round_id=round_id,
                           lot_key=body.lot_key, position_key_in_proposal=body.position_key_in_proposal,
                           work_category_id=body.work_category_id, note=body.note, user_id=current_user.id)


@router.delete("/{tender_id}/rounds/{round_id}/category-overrides")
def delete_round_override(tender_id: int, round_id: int, body: RoundOverrideDelete, db: Session = Depends(get_db)):
    """Снять решение во всех offer-сметах раунда; частичное и конфликтное — тоже в «без решения» (§2.4)."""
    return _round_override(db, round_id, rco.clear_round_override, tender_id=tender_id, round_id=round_id,
                           lot_key=body.lot_key, position_key_in_proposal=body.position_key_in_proposal)
```

- [ ] **Step 4: Прогнать** — `uv run pytest tests/integration/test_round_category_override.py tests/test_auth_coverage.py -q`
  зелёные (сторож auth обходит и новые PUT/DELETE — они под `_auth_dep`).
- [ ] **Step 5: Commit**

```bash
git add backend/routers/tenders.py backend/tests/integration/test_round_category_override.py
git commit -m "feat(round-unallocated): PUT/DELETE rounds/{round_id}/category-overrides — note обязательное nullable, транзакция в роутере"
```

---

### Task 9: Фронт — типы, api, ключ, хуки, фикстуры msw

**Files:**
- Modify: `frontend/src/types/domain.ts`
- Modify: `frontend/src/services/api/domain.ts`
- Modify: `frontend/src/services/queryKeys.ts`
- Modify: `frontend/src/services/queries.ts`
- Modify: `frontend/src/test/fixtures.ts`, `frontend/src/test/handlers.ts`
- Test: `frontend/src/services/queries.tenders.test.tsx`

**Interfaces:**
- Produces:

```ts
// types/domain.ts — TenderRoundRow получает поле:
  /** Разделов раунда в состояниях unassigned+partial+conflict (спека этапного
   *  разноса §2.6); null — у раунда нет offer-смет, триггер разноса не рисуется. */
  unallocated_pending_sections: number | null;

export type RoundSectionState = "unassigned" | "partial" | "conflict";
export type SectionKey = [lot_key: string, position_key_in_proposal: string];
export interface RoundSectionPartial { assigned: number; total: number; notes: (string | null)[] }
export interface RoundSectionConflict {
  categories: { id: number; code: string; title: string }[];
  notes: (string | null)[];
  audit_differs: boolean;
}
interface RoundUnallocatedSectionBase {
  lot_key: string; position_key_in_proposal: string;
  parent_key: SectionKey | null;      // вершина нераспределённой части — null
  depth: number; number: string | null; title: string; smr_article_raw: string | null;
  rows: number;                       // ⚠ УСТАРЕЛО (см. врезку): у sections[] это
                                      // ДОСТИЖИМЫЕ строки — подпись «N позиций»
}
export type RoundUnallocatedSection = RoundUnallocatedSectionBase & (
  | { state: "unassigned" }
  | { state: "partial"; partial: RoundSectionPartial }
  | { state: "conflict"; conflict: RoundSectionConflict });
export interface RoundManualAssignment {
  lot_key: string; position_key_in_proposal: string; number: string | null; title: string; rows: number;
  work_category_id: number; category_code: string; category_title: string;
  assigned_by_email: string; assigned_at: string; note: string | null;
}
export type RoundDiagnosticCode = "outside_structure" | "structure_disabled" | "unresolved_chapter_ref";
export interface RoundDiagnostic { code: RoundDiagnosticCode; contractor_title: string; title: string; rows: number }
export interface RoundUnallocated {
  round: { id: number; stage_no: number; label: string | null; held_on: string | null };
  offers_count: number;
  sections: RoundUnallocatedSection[];
  manual: RoundManualAssignment[];
  diagnostics: RoundDiagnostic[];
  category_options: ProjectPassportCategoryOption[];
}
export interface SetRoundCategoryOverrideInput {
  tenderId: number; roundId: number; lotKey: string; positionKey: string; workCategoryId: number;
  /** ВСЕГДА уходит в тело: null — явная очистка (§2.4). */
  note: string | null;
}
export interface ClearRoundCategoryOverrideInput { tenderId: number; roundId: number; lotKey: string; positionKey: string }

// api/domain.ts — в tendersApi:
roundUnallocated: (tenderId: number, roundId: number): Promise<RoundUnallocated> =>
  api.get<RoundUnallocated>(`/v1/tenders/${tenderId}/rounds/${roundId}/unallocated`).then((r) => r.data),
setRoundCategoryOverride: ({ tenderId, roundId, lotKey, positionKey, workCategoryId, note }: SetRoundCategoryOverrideInput): Promise<CategoryOverrideChangeSummary> =>
  api.put<CategoryOverrideChangeSummary>(`/v1/tenders/${tenderId}/rounds/${roundId}/category-overrides`,
    { lot_key: lotKey, position_key_in_proposal: positionKey, work_category_id: workCategoryId, note }).then((r) => r.data),
clearRoundCategoryOverride: ({ tenderId, roundId, lotKey, positionKey }: ClearRoundCategoryOverrideInput): Promise<CategoryOverrideChangeSummary> =>
  api.delete<CategoryOverrideChangeSummary>(`/v1/tenders/${tenderId}/rounds/${roundId}/category-overrides`,
    { data: { lot_key: lotKey, position_key_in_proposal: positionKey } }).then((r) => r.data),

// queryKeys.ts — в qk.tenders:
/** Нераспределённое раунда (спека этапного разноса §2.3). */
roundUnallocated: (tenderId: number, roundId: number) => ["tenders", "round-unallocated", tenderId, roundId] as const,

// queries.ts:
export function useRoundUnallocated(tenderId: number | undefined, roundId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: qk.tenders.roundUnallocated(tenderId ?? 0, roundId ?? 0),
    queryFn: () => tendersApi.roundUnallocated(tenderId as number, roundId as number),
    // Ленивый GET (§2.7): запрос уходит только при открытом Sheet; 404 не повторяется.
    enabled: enabled && tenderId !== undefined && roundId !== undefined,
    retry: false,
  });
}
function invalidateAfterRoundOverride(qc: ReturnType<typeof useQueryClient>, tenderId: number, roundId: number) {
  // §2.7: карточка (счётчик), ОБА префикса свода, сам верстак. Ничего договорного.
  qc.invalidateQueries({ queryKey: qk.tenders.card(tenderId) });
  qc.invalidateQueries({ queryKey: qk.tenders.stageSummaryForTender(tenderId) });
  qc.invalidateQueries({ queryKey: qk.tenders.stagePositionsForTender(tenderId) });
  qc.invalidateQueries({ queryKey: qk.tenders.roundUnallocated(tenderId, roundId) });
}
export function useSetRoundCategoryOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: tendersApi.setRoundCategoryOverride,
    onSuccess: (_d, input) => invalidateAfterRoundOverride(qc, input.tenderId, input.roundId),
    onError: toastApiError,
  });
}
export function useClearRoundCategoryOverride() { /* то же с tendersApi.clearRoundCategoryOverride */ }
```

Фикстуры (`test/fixtures.ts`): `sampleTenderCard.rounds[0]` (3001) получает
`unallocated_pending_sections: 24`, `rounds[1]` (3002) — `null`; в
`tenderCardFor` (`test/handlers.ts`) состояния, где у раунда 3002 появляется
смета (`both-loaded`, `both-loaded-with-beta`, `second-round-no-estimate` —
у последнего сметы нет → остаётся `null`), выставляют `0`. Новая
`sampleRoundUnallocated: RoundUnallocated` — числа макета (`lot_key: "lot_1"`):

```
round {id 3001, stage_no 1, label "Первичные предложения", held_on "2026-06-01"}, offers_count 2
sections:
  «14» SHELL & CORE          key "3"   parent null   depth 0  rows 3  unassigned  smr_article_raw "—"
  «14.1» Маячковый ряд       key "4"   parent ["lot_1","3"] depth 1  rows 2  unassigned
  «12» Лифтовое оборудование key "20"  parent null   depth 0  rows 6  partial {assigned 1, total 2, notes ["код в файле нечитаем"]}
  «11» Слаботочные системы   key "30"  parent null   depth 0  rows 4  conflict {categories [{10,"10","Слаботочка"},{11,"11","ИТП"}], notes [null], audit_differs false}
  «16» Пусконаладочные работы key "40" parent null   depth 0  rows 3  conflict {categories [{18,"18","Благоустройство"}], notes [null], audit_differs true}
manual: «13» Благоустройство территории key "50" rows 5 → 18 «Благоустройство», analyst@mr-group.kz, 2026-08-29T10:00:00Z, note "код в файле нечитаем"
diagnostics: [{unresolved_chapter_ref, "ООО «АНТТЕК»", "c +6,650м до +16,500м", 1},
              {unresolved_chapter_ref, "ООО «ЕНИГЮН КОНСТРАКШН»", "Дополнительные работы", 1}]
category_options: sampleProjectPassport.category_options
```

Хендлеры (`test/handlers.ts`, блок тендерного контура):
`GET /api/v1/tenders/:id/rounds/:rid/unallocated` — 3001 → фикстура; иначе
404 `{detail: {code: "round_has_no_offer_estimates", message: "Раунд или его сметы больше недоступны."}}`;
`PUT`/`DELETE /api/v1/tenders/:id/rounds/:rid/category-overrides` — пишут
`{method, body}` в `handlerState.roundOverrideRequests` (новое поле,
сбрасывается в `resetHandlerState`) и отвечают
`{chapters_updated: 3, additional_works_updated: 1, chapters_manual: 3}`.

- [ ] **Step 1: Тесты** — в `queries.tenders.test.tsx`:

```tsx
describe("useRoundUnallocated", () => {
  it("enabled=false — запрос не уходит (ленивый GET, §2.7)", () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useRoundUnallocated(300, 3001, false), { wrapper: wrapperFor(qc) });
    expect(result.current.fetchStatus).toBe("idle");
  });

  it("грузит нераспределённое раунда 3001", async () => {
    const qc = createTestQueryClient();
    const { result } = renderHook(() => useRoundUnallocated(300, 3001, true), { wrapper: wrapperFor(qc) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.sections).toHaveLength(5);
    expect(result.current.data?.offers_count).toBe(2);
  });
});

describe("раундовые мутации разноса (§2.7)", () => {
  const EXPECTED = [
    qk.tenders.card(300), qk.tenders.stageSummaryForTender(300),
    qk.tenders.stagePositionsForTender(300), qk.tenders.roundUnallocated(300, 3001),
  ].map((k) => JSON.stringify(k));

  it("PUT несёт заметку явно (null тоже) и инвалидирует тендерную ветку — и ничего договорного", async () => {
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useSetRoundCategoryOverride(), { wrapper: wrapperFor(qc) });
    await act(() => result.current.mutateAsync({ tenderId: 300, roundId: 3001, lotKey: "lot_1", positionKey: "3", workCategoryId: 20, note: null }));
    const body = handlerState.roundOverrideRequests[0].body as Record<string, unknown>;
    expect("note" in body && body.note === null).toBe(true);
    expect(body).toMatchObject({ lot_key: "lot_1", position_key_in_proposal: "3", work_category_id: 20 });
    const keys = spy.mock.calls.map(([f]) => JSON.stringify(f?.queryKey));
    expect(new Set(keys)).toEqual(new Set(EXPECTED));
    // Негативно к перекрёстной инвалидации: ни один ключ не начинается с корней договорного контура.
    expect(keys.some((k) => k.startsWith('["passport"') || k.startsWith('["contracts"'))).toBe(false);
  });

  it("DELETE несёт ключ в теле и инвалидирует тот же набор", async () => {
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useClearRoundCategoryOverride(), { wrapper: wrapperFor(qc) });
    await act(() => result.current.mutateAsync({ tenderId: 300, roundId: 3001, lotKey: "lot_1", positionKey: "50" }));
    expect(handlerState.roundOverrideRequests[0]).toMatchObject({ method: "DELETE", body: { lot_key: "lot_1", position_key_in_proposal: "50" } });
    expect(new Set(spy.mock.calls.map(([f]) => JSON.stringify(f?.queryKey)))).toEqual(new Set(EXPECTED));
  });
});
```

Импорты `useRoundUnallocated`, `useSetRoundCategoryOverride`,
`useClearRoundCategoryOverride` — в существующий импорт из `./queries`.

- [ ] **Step 2: Прогнать** — `npm test -- queries.tenders` красный (нет экспортов);
  `npx tsc -b` красный на `TenderRoundRow` без нового поля во всех фикстурах карточки.
- [ ] **Step 3: Реализовать** блок Interfaces, фикстуры и хендлеры; добить все места,
  которые `tsc` покажет (литералы `TenderRoundRow` в тестах).
- [ ] **Step 4: Прогнать** — `npm test -- queries.tenders`, `npx tsc -b`,
  `npm test -- TenderCardPage BaselineStatus RoundUploadPanel` (потребители фикстуры карточки) зелёные.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/domain.ts frontend/src/services/api/domain.ts frontend/src/services/queryKeys.ts frontend/src/services/queries.ts frontend/src/services/queries.tenders.test.tsx frontend/src/test/fixtures.ts frontend/src/test/handlers.ts
git commit -m "feat(round-unallocated): фронт — типы ответа, api, ключ round-unallocated, раундовые хуки с тендерной инвалидацией"
```

---

### Task 10: Фронт — ядро `UnallocatedWorkbench`, `CategoryPicker` с заметкой, паспортная обёртка

Из `UnallocatedPanel` выделяется презентационное ядро (§2.7). Обёртка
паспорта сохраняет имя, пропсы, тексты, `data-testid` и сортировку —
`UnallocatedPanel.test.tsx` НЕ правится и остаётся зелёным (§3 п.5).

**Files:**
- Create: `frontend/src/components/unallocated/UnallocatedWorkbench.tsx`
- Create: `frontend/src/components/unallocated/CategoryPicker.tsx`
- Modify: `frontend/src/pages/passport/UnallocatedPanel.tsx`
- Test: `frontend/src/components/unallocated/CategoryPicker.test.tsx` (новый),
  `frontend/src/pages/passport/UnallocatedPanel.test.tsx` (без правок — гейт задачи)

**Interfaces:**
- Produces:

```ts
// UnallocatedWorkbench.tsx
export interface WorkbenchSection { key: string; parentKey: string | null; number: string | null; title: string; smr_article_raw: string | null }
export interface WorkbenchManualRow { key: string; number: string | null; title: string; category_code: string; category_title: string; assigned_by_email: string; assigned_at: string; note: string | null }
export interface WorkbenchCopy { sectionsHeading: string; sectionsHint: string; sectionsEmpty: string; manualHeading: string; manualHint: string; manualEmpty: string }
export type NoteField<S> = false | { existingNotes: (section: S) => (string | null)[] };
export interface UnallocatedWorkbenchProps<S extends WorkbenchSection, M extends WorkbenchManualRow> {
  sections: S[]; manual: M[]; categoryOptions: ProjectPassportCategoryOption[]; copy: WorkbenchCopy;
  /** Хвост data-testid строк и кнопок: паспорт — position_item_id, тендер — `${lot_key}:${position_key}`. */
  testId: (section: { key: string }) => string;
  /** Порядок сиблингов; undefined — порядок прихода (файловый, тендер). */
  compareSiblings?: (a: S, b: S) => number;
  renderAside: (section: S) => ReactNode;          // правая колонка дерева: деньги (паспорт) или «N позиций» (тендер)
  renderManualAside: (row: M) => ReactNode;
  renderMark?: (section: S) => ReactNode;          // пометка под названием: partial / conflict (тендер)
  noteField: NoteField<S>;
  onPick: (section: S, option: ProjectPassportCategoryOption, note: string | null) => void;
  onClear: (row: M) => void;
  disabled: boolean;
  children?: ReactNode;                            // блоки после «Разнесено вручную» (диагностика)
}
export function UnallocatedWorkbench<S extends WorkbenchSection, M extends WorkbenchManualRow>(props: UnallocatedWorkbenchProps<S, M>): JSX.Element
export function chapterLabel(section: { number: string | null; title: string }): string   // «14 SHELL & CORE»

// CategoryPicker.tsx
export interface CategoryPickerProps {
  testKey: string; label: string; options: ProjectPassportCategoryOption[]; disabled?: boolean;
  noteField: false | { existingNotes: (string | null)[] };
  onPick: (option: ProjectPassportCategoryOption, note: string | null) => void;
}
```

Разметка дерева, popover и строк «Разнесено вручную» переезжает из
`UnallocatedPanel.tsx` дословно (testid `unallocated-section-${id}`,
`unallocated-section-row-${id}`, `unallocated-section-title-${id}`,
`pick-category-${id}`, `manual-assignment-${id}`, `manual-remove-${id}`,
aria-label `Отнести на статью: ${label}` / `Снять решение по разделу: ${label}`,
триггер «Отнести на статью…», плейсхолдер «Код или название статьи…»,
`data-print="clamp"` и `line-clamp-2` у названия, отступ `12 + depth * 20`) —
где `${id}` = `testId(section)`. Корень `<section data-testid="unallocated-panel" data-print="hide">`
остаётся В ОБЁРТКЕ паспорта (тест «панель помечена data-testid и не попадает
в печатный поток» проверяет именно её); ядро отдаёт `<div>`.

Дерево строится по `parentKey` тем же `buildUnallocatedTree`, что был в
панели (узел без родителя в списке — верхним уровнем, finding I-4),
сиблинги сортируются `compareSiblings`, если передан, рекурсивно.

**Поле «Заметка» в пикере** (§2.7, §2.4): при `noteField === false` поля нет
и `onPick(option, null)`. Иначе — `Textarea aria-label="Заметка"` под
`CommandList`:
- различных заметок (`null` — «без заметки» — тоже значение) 0 или 1 →
  поле предзаполнено единственной (или пусто), выбор статьи доступен сразу;
- ≥ 2 → над полем текст «Заметки в сметах различаются — выберите, какую
  оставить:» и кнопки `«…»` на каждую заметку и «без заметки»; до нажатия
  одной из них пункты `CommandItem` `disabled`; нажатие кладёт заметку в поле;
- `onPick(option, field.trim() === "" ? null : field)`.

Паспортная обёртка: `noteField: false` (по-сметный маршрут заметку с экрана
не принимает — поведение прежнее), `compareSiblings: compareBySubtreeDesc`
(остаётся в `UnallocatedPanel.tsx` вместе с `compareDecimalStrings`),
`testId: (s) => s.key` где `key = String(position_item_id)`,
`renderAside` — прежние две `MoneyCell` с testid `subtree-amount-${id}` /
`own-amount-${id}`, `renderManualAside` — `manual-amount-${id}`; `copy` —
прежние заголовки, подсказки и пустые состояния панели.

- [ ] **Step 1: Контрольный прогон ДО правок** — `npm test -- UnallocatedPanel` зелёный
  (эталон, который обязан остаться зелёным без правок).

- [ ] **Step 2: Тесты пикера** — `CategoryPicker.test.tsx` (`renderWithProviders`, `userEvent`):

```tsx
const OPTIONS = sampleProjectPassport.category_options;
const pick = (props: Partial<CategoryPickerProps> = {}) => {
  const onPick = vi.fn();
  renderWithProviders(<CategoryPicker testKey="k" label="14 SHELL & CORE" options={OPTIONS} noteField={false} onPick={onPick} {...props} />);
  return onPick;
};

it("без поля заметки выбор статьи шлёт null", async () => {
  const onPick = pick();
  await user.click(screen.getByTestId("pick-category-k"));
  expect(screen.queryByLabelText("Заметка")).not.toBeInTheDocument();
  await user.click(screen.getByRole("option", { name: new RegExp(OPTIONS[0].code) }));
  expect(onPick).toHaveBeenCalledWith(OPTIONS[0], null);
});
it("единая существующая заметка предзаполняется и уходит в выбор", …existingNotes: ["код нечитаем"] → textarea value, onPick(...,"код нечитаем"));
it("пустое поле уходит как null", …existingNotes: [] → clear textarea, onPick(..., null));
it("при различающихся заметках пункты недоступны до явного выбора; «без заметки» даёт null", …existingNotes: ["а", null] → options aria-disabled; click «без заметки»; option enabled; onPick(..., null));
it("выбор одной из различающихся заметок отправляет её", …click «а» → onPick(..., "а"));
```

Тела написать полностью по образцу первого (`screen.getByRole("option")` —
пункты `cmdk` несут роль `option`; проверка недоступности —
`toHaveAttribute("aria-disabled", "true")`).

- [ ] **Step 3: Прогнать** — `npm test -- CategoryPicker` красный (модуля нет).
- [ ] **Step 4: Реализовать** ядро, пикер и обёртку по блоку Interfaces.
- [ ] **Step 5: Прогнать** — `npm test -- CategoryPicker UnallocatedPanel ProjectPassportPage`,
  `npx tsc -b`, `npm run lint` зелёные. `git diff --stat frontend/src/pages/passport/UnallocatedPanel.test.tsx`
  обязан быть пустым.
- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/unallocated/ frontend/src/pages/passport/UnallocatedPanel.tsx
git commit -m "refactor(round-unallocated): ядро UnallocatedWorkbench и CategoryPicker с полем заметки; панель паспорта — обёртка без изменений поведения"
```

---

### Task 11: Фронт — `UnallocatedSheet` раунда

**Files:**
- Create: `frontend/src/components/unallocated/roundUnallocatedCopy.ts`
- Create: `frontend/src/components/tenders/UnallocatedSheet.tsx`
- Test: `frontend/src/components/tenders/UnallocatedSheet.test.tsx`

**Interfaces:**
- Consumes: `UnallocatedWorkbench`, `useRoundUnallocated`,
  `useSetRoundCategoryOverride`, `useClearRoundCategoryOverride`, `apiErrorStatus`
  (из `@/services/queries`), `Sheet*` из `@/components/ui/sheet`, `Skeleton`, `Button`, `pluralRu`.
- Produces:

```ts
// roundUnallocatedCopy.ts — все тексты экрана из макета
export const sheetTitle = (stageNo: number) => `Разнос статей — Этап ${stageNo}`;
export const sheetSubtitle = (offers: number) =>
  `Ведомость одна на этап: решение по разделу применяется ко всем сметам раунда (${offers} участник${pluralRu(offers)}).`;
export const pendingHeading = (n: number) => `Требуют решения — ${n}`;
// ⚠ УСТАРЕЛО (см. врезку) и вдвойне: полный текст берётся из макета (§3 devlog,
// отступление 5), а смысл счётчика с 02.09.2026 — охват решения.
export const PENDING_HINT = "Файловый порядок ведомости; счётчик — полный размер файлового поддерева.";
export const MANUAL_HEADING = "Разнесено вручную";
export const MANUAL_HINT = "Только разделы с единым решением во всех сметах раунда; «снять» убирает решение во всех сметах.";
export const PENDING_EMPTY = "Разделов, требующих решения, нет.";
export const MANUAL_EMPTY = "Единых ручных решений пока нет.";
export const diagnosticsHeading = (rows: number) => `Не закрывается разносом — ${rows} ${rowsWord(rows)}`;
export const positionsLabel = (n: number) => `${n} ${positionsWord(n)}`;          // «37 позиций», «2 позиции», «1 позиция»
export const partialMark = (p: RoundSectionPartial) => `разнесено не во всех сметах: ${p.assigned} из ${p.total}`;
export function conflictMark(c: RoundSectionConflict): string {
  if (c.categories.length > 1)
    return `решения в сметах различаются: статьи ${c.categories.map((x) => `«${x.code} · ${x.title}»`).join(" против ")} — нераспределённых строк нет, но этап несогласован`;
  if (c.notes.length > 1) return "решения в сметах различаются: статья едина, различаются заметки — повторное решение выравнивает";
  return "решения в сметах различаются: статья едина, различается аудит (авторы/даты) — повторное решение выравнивает";
}
export const DIAGNOSTIC_REASON: Record<RoundDiagnosticCode, string> = {
  outside_structure: "позиции вне структуры файла — раздела, которому можно назначить статью, у них нет",
  structure_disabled: "привязка по структуре погашена — статьи не привязаны ни к одной строке предложения",
  unresolved_chapter_ref: "допработа с неразрешимой ссылкой — статью не от кого наследовать",
};
export const LOAD_ERROR = "Не удалось загрузить нераспределённое.";
export const ROUND_GONE = "Раунд или его сметы больше недоступны.";
export const sectionKeyOf = (lotKey: string, positionKey: string) => `${lotKey}:${positionKey}`;
function positionsWord(n: number): string  // позиция / позиции / позиций (ж. р., как estimateWordFor в TenderCardPage)
function rowsWord(n: number): string       // строка / строки / строк

// UnallocatedSheet.tsx
export function UnallocatedSheet({ tenderId, round, open, onOpenChange }: {
  tenderId: number; round: TenderRoundRow | undefined; open: boolean; onOpenChange: (open: boolean) => void;
}): JSX.Element
```

Поведение: `useRoundUnallocated(tenderId, round?.id, open)` — GET уходит
только при `open`; `SheetContent side="right"` с `SheetTitle`
`sheetTitle(round.stage_no)` и `SheetDescription` `sheetSubtitle(data.offers_count)`;
`isPending` → три `Skeleton`; ошибка со статусом 404 → `ROUND_GONE` и кнопка
«Обновить карточку» (инвалидирует `qk.tenders.card(tenderId)` и зовёт
`onOpenChange(false)`); другая ошибка → `LOAD_ERROR` и «Повторить» (`refetch`);
успех → `UnallocatedWorkbench` с `sections` → `{...s, key: sectionKeyOf(...), parentKey: s.parent_key && sectionKeyOf(...)}`,
`compareSiblings` не передаётся (файловый порядок), `renderAside` →
`positionsLabel(s.rows)` в `data-testid="rows-${testId}"`, `renderMark` →
`partialMark`/`conflictMark` в `text-warning-text`, `noteField: { existingNotes }`
(partial → `partial.notes`, conflict → `conflict.notes`, unassigned → `[]`),
`onPick` → `useSetRoundCategoryOverride().mutate({ tenderId, roundId, lotKey, positionKey, workCategoryId: option.id, note })`,
`onClear` → `useClearRoundCategoryOverride().mutate(...)`, `disabled` пока
любая мутация `isPending`; `children` — блок диагностики:
`<h3>{diagnosticsHeading(Σ rows)}</h3>` и строки
`<div data-testid="diagnostic-row"><b>{contractor_title}</b> · «{title}»<p>{DIAGNOSTIC_REASON[code]}</p></div>`
БЕЗ кнопок; при пустой диагностике блок не рисуется.

- [ ] **Step 1: Тесты** (`UnallocatedSheet.test.tsx`, `renderWithProviders`, хендлеры задачи 9):

```tsx
const ROUND = sampleTenderCard.rounds[0];      // 3001, 24 раздела
function renderSheet(props: Partial<Parameters<typeof UnallocatedSheet>[0]> = {}, options = {}) {
  return renderWithProviders(<UnallocatedSheet tenderId={300} round={ROUND} open onOpenChange={() => {}} {...props} />, options);
}

it("ленивый GET: закрытый Sheet не шлёт запрос, открытый — шлёт один", async () => {
  let hits = 0;
  server.use(http.get("/api/v1/tenders/:id/rounds/:rid/unallocated", () => { hits += 1; return HttpResponse.json(sampleRoundUnallocated); }));
  const { rerender } = renderSheet({ open: false });
  await new Promise((r) => setTimeout(r, 50));
  expect(hits).toBe(0);
  rerender(<UnallocatedSheet tenderId={300} round={ROUND} open onOpenChange={() => {}} />);
  await screen.findByRole("heading", { name: "Разнос статей — Этап 1" });
  expect(hits).toBe(1);
});
it("шапка: подзаголовок с числом участников; блок «Требуют решения — 5»; счётчик «3 позиции» без денег", …
   expect(screen.getByText(/ко всем сметам раунда \(2 участника\)/)); expect(screen.getByRole("heading", {name: "Требуют решения — 5"}));
   expect(screen.getByTestId("rows-lot_1:3")).toHaveTextContent("3 позиции"); expect(screen.queryByText(/₽/)).toBeNull());
it("пометки partial и conflict — текстами макета", … "разнесено не во всех сметах: 1 из 2"; /статьи «10 · Слаботочка» против «11 · ИТП»/; /статья едина, различается аудит/);
it("«Разнесено вручную»: запись со статьёй, автором, датой, заметкой; «Снять» шлёт DELETE с ключом раздела", …
   click manual-remove-lot_1:50 → handlerState.roundOverrideRequests[0] toMatchObject({method:"DELETE", body:{lot_key:"lot_1", position_key_in_proposal:"50"}}));
it("выбор статьи у частичного раздела предзаполняет единую заметку и шлёт её в PUT", … pick-category-lot_1:20 → textarea value "код в файле нечитаем" → click option → body.note === "код в файле нечитаем");
it("диагностика: заголовок «Не закрывается разносом — 2 строки», участник поимённо, причина, без кнопок", …
   within(block).queryAllByRole("button") toHaveLength(0));
it("404 — «Раунд или его сметы больше недоступны.» и «Обновить карточку» закрывает Sheet", … round={sampleTenderCard.rounds[1]} → GET 3002 → 404; onOpenChange called with false);
it("ошибка сервера — «Не удалось загрузить нераспределённое.» и «Повторить» перезапрашивает", … server.use 500 once then fixture; hits === 2);
```

Тела дописать полностью по образцу первого теста; тексты — из
`roundUnallocatedCopy.ts` литералами (сравнивать со СЛОВОМ макета, а не с
константой, — иначе тест зелёный при любой опечатке в константе).

- [ ] **Step 2: Прогнать** — `npm test -- UnallocatedSheet` красный.
- [ ] **Step 3: Реализовать** copy-модуль и Sheet.
- [ ] **Step 4: Прогнать** — `npm test -- UnallocatedSheet`, `npx tsc -b`, `npm run lint` зелёные.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/unallocated/roundUnallocatedCopy.ts frontend/src/components/tenders/UnallocatedSheet.tsx frontend/src/components/tenders/UnallocatedSheet.test.tsx
git commit -m "feat(round-unallocated): Sheet-верстак раунда — ленивый GET, состояния, пометки, диагностика, заметка в пикере"
```

---

### Task 12: Фронт — триггер в заголовке этапа и URL-контракт `?unallocated=`

**Files:**
- Modify: `frontend/src/components/tenders/OfferGrid.tsx`
- Modify: `frontend/src/components/unallocated/roundUnallocatedCopy.ts` (`triggerLabel`)
- Modify: `frontend/src/pages/tenders/TenderCardPage.tsx`
- Test: `frontend/src/pages/tenders/TenderCardPage.test.tsx`

**Interfaces:**
- Produces:

```ts
// roundUnallocatedCopy.ts
export const QUIET_TRIGGER = "разнести";
export function triggerLabel(pending: number): string {
  if (pending === 0) return QUIET_TRIGGER;
  const verb = pending % 10 === 1 && pending % 100 !== 11 ? "требует" : "требуют";
  return `⚠ ${pending} раздел${pluralRu(pending)} ${verb} решения — разнести`;   // макет: «⚠ 24 раздела требуют решения — разнести»
}
// OfferGrid: новый обязательный проп
onOpenUnallocated: (roundId: number) => void;
```

В `<TableHead>` этапа под кнопкой «Этап N» — второй `button`
`data-testid={`unallocated-trigger-${round.id}`}`, только когда
`round.unallocated_pending_sections !== null`; при `> 0` — классы
`rounded-md border border-warning-border bg-warning-soft px-2 py-0.5 text-2xs text-warning-text hover:underline`,
при `0` — `text-2xs text-fg-tertiary hover:underline`; текст `triggerLabel(n)`.
Заголовок оборачивается во `flex flex-col items-start gap-1`; `Toggle`
ячеек не трогается.

`TenderCardPage`: параметр читается и проверяется — принимается лишь раунд
ТЕКУЩЕЙ карточки с `unallocated_pending_sections !== null`:

```ts
const unallocatedParam = params.get("unallocated");
const unallocatedRound = unallocatedParam === null
  ? undefined
  : rounds.find((r) => r.id === Number(unallocatedParam) && r.unallocated_pending_sections !== null);
function openUnallocated(roundId: number) { const next = new URLSearchParams(params); next.set("unallocated", String(roundId)); setParams(next); }
function closeUnallocated() { const next = new URLSearchParams(params); next.delete("unallocated"); setParams(next); }
…
<OfferGrid … onOpenUnallocated={openUnallocated} />
<UnallocatedSheet tenderId={card.id} round={unallocatedRound} open={unallocatedRound !== undefined}
                  onOpenChange={(open) => { if (!open) closeUnallocated(); }} />
```

- [ ] **Step 1: Тесты** (в `TenderCardPage.test.tsx`, новый `describe("Триггер разноса и ?unallocated= (§2.7)")`):

```tsx
it("три состояния триггера: тёплый бейдж при 24, тихое «разнести» при 0, ничего при null", async () => {
  handlerState.tenderRoundState = "both-loaded";           // 3001 → 24, 3002 → 0
  renderCard();
  expect(await screen.findByTestId("unallocated-trigger-3001")).toHaveTextContent("⚠ 24 раздела требуют решения — разнести");
  expect(screen.getByTestId("unallocated-trigger-3002")).toHaveTextContent(/^разнести$/);
  handlerState.tenderRoundState = "loaded";                 // 3002 → null
});
it("при null триггера нет", async () => { renderCard(); await screen.findByText("ООО Альфа"); expect(screen.queryByTestId("unallocated-trigger-3002")).toBeNull(); });
it("клик пишет ?unallocated=<round_id>, сохраняя ?round=; закрытие удаляет только unallocated", async () => {
  renderCard({ initialRoute: "/tenders/300?round=3002" });
  await user.click(await screen.findByTestId("unallocated-trigger-3001"));
  expect(await screen.findByRole("heading", { name: "Разнос статей — Этап 1" })).toBeInTheDocument();
  expect(currentSearch()).toBe("?round=3002&unallocated=3001");   // currentSearch — через <Route element={<LocationProbe/>}> рядом с страницей
  await user.click(screen.getByRole("button", { name: /Close|Закрыть/ }));
  await waitFor(() => expect(currentSearch()).toBe("?round=3002"));
});
it("чужой ?unallocated=999 и раунд без offer-смет (3002 при null) не запускают запрос", async () => {
  let hits = 0;
  server.use(http.get("/api/v1/tenders/:id/rounds/:rid/unallocated", () => { hits += 1; return HttpResponse.json(sampleRoundUnallocated); }));
  renderCard({ initialRoute: "/tenders/300?unallocated=999" });
  await screen.findByText("ООО Альфа");
  expect(screen.queryByRole("heading", { name: /Разнос статей/ })).toBeNull();
  cleanup();
  renderCard({ initialRoute: "/tenders/300?unallocated=3002" });
  await screen.findByText("ООО Альфа");
  expect(hits).toBe(0);
});
```

`LocationProbe` — крошечный компонент теста, пишущий `useLocation().search`
в `data-testid="location"`, добавленный вторым `Route` в `renderCard`
(путь тот же `/tenders/:tenderId`, рендер через фрагмент). Имя кнопки
закрытия `Sheet` shadcn — `sr-only` «Close»; при русификации в проекте
взять фактическое (проверить `grep -n "sr-only" frontend/src/components/ui/sheet.tsx`).

- [ ] **Step 2: Прогнать** — красный (нет testid).
- [ ] **Step 3: Реализовать.**
- [ ] **Step 4: Прогнать** — `npm test -- TenderCardPage`, `npx tsc -b`, `npm run lint` зелёные;
  прежние тесты карточки (плитки, диалоги) не задеты.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/tenders/OfferGrid.tsx frontend/src/components/unallocated/roundUnallocatedCopy.ts frontend/src/pages/tenders/TenderCardPage.tsx frontend/src/pages/tenders/TenderCardPage.test.tsx
git commit -m "feat(round-unallocated): триггер в заголовке этапа и URL-контракт ?unallocated= с проверкой раунда"
```

---

### Task 13: Фронт — «разнести →» из строки «Нераспределённое» свода

**Files:**
- Modify: `frontend/src/pages/tenders/summary/SummaryCell.tsx` (проп `extra`)
- Modify: `frontend/src/pages/tenders/summary/StageSummaryTable.tsx` (обязательный проп `allocateLink`)
- Modify: `frontend/src/pages/tenders/summary/StageSummaryPage.tsx`
- Test: `frontend/src/pages/tenders/summary/StageSummaryTable.test.tsx`,
  `frontend/src/pages/tenders/summary/StageSummaryPage.test.tsx`

**Interfaces:**
- Produces:

```ts
// SummaryCell: как у SummaryTotalCell — третья строка ячейки
export function SummaryCell({ cell, extra }: { cell: StageSummaryCell; extra?: ReactNode })
// StageSummaryTable: новый ОБЯЗАТЕЛЬНЫЙ проп (иначе кнопка нарисована и не подключена — урок фичи 4)
allocateLink: (roundId: number) => ReactNode;
// в строке «Нераспределённое»:
{unallocated.cells.map((cell, index) => (
  <SummaryCell key={index} cell={cell}
    extra={cell.rows.row_count > 0 ? allocateLink(columns[index].round_id) : undefined} />
))}
// StageSummaryPage:
<StageSummaryTable summary={summary} tenderId={id!} offerIds={offerIds}
  allocateLink={(roundId) => (
    <Link to={`/tenders/${id}?unallocated=${roundId}`} className="text-warning-text hover:underline"
          data-testid={`allocate-${roundId}`}>разнести →</Link>
  )} />
```

- [ ] **Step 1: Тесты.** В `StageSummaryTable.test.tsx` — `TABLE_PROPS` получает
  `allocateLink: () => null`; новые:

```tsx
it("«разнести →» зовётся ровно для колонок с нераспределёнными строками, по round_id колонки", () => {
  const allocateLink = vi.fn((roundId: number) => <span data-testid={`allocate-${roundId}`}>разнести →</span>);
  render(<StageSummaryTable summary={sampleStageSummary} {...TABLE_PROPS} allocateLink={allocateLink} />);
  const expected = sampleStageSummary.columns
    .filter((_, i) => sampleStageSummary.unallocated.cells[i].rows.row_count > 0)
    .map((c) => c.round_id);
  expect(allocateLink.mock.calls.map(([id]) => id)).toEqual(expected);
  expect(expected.length).toBeGreaterThan(0);                               // предпосылка фикстуры
  expect(sampleStageSummary.unallocated.cells.some((c) => c.rows.row_count === 0)).toBe(true); // и отрицательный случай есть
});
it("у колонки с неизвестной базой НДС ссылка есть — строки разносимы, хотя сумма скрыта", () => {
  const allocateLink = vi.fn(() => <span>разнести →</span>);
  render(<StageSummaryTable summary={stageSummaryWithUnknownSecondColumn()} {...TABLE_PROPS} allocateLink={allocateLink} />);
  expect(allocateLink).toHaveBeenCalledWith(stageSummaryWithUnknownSecondColumn().columns[1].round_id);
});
```

Если в `sampleStageSummary.unallocated.cells` нет ячейки с `row_count === 0`
или с `> 0` — поправить фикстуру так, чтобы были обе (и прогнать
`fixtures.test.ts`). В `StageSummaryPage.test.tsx`:

```tsx
it("ссылка «разнести →» ведёт на карточку с ?unallocated=<round_id>", async () => {
  renderPage(...);   // существующий хелпер файла
  const link = await screen.findByRole("link", { name: "разнести →" });   // или getAllByRole, если колонок несколько
  expect(link).toHaveAttribute("href", `/tenders/300?unallocated=${sampleStageSummary.columns[<i>].round_id}`);
});
```

- [ ] **Step 2: Прогнать** — `npm test -- StageSummaryTable StageSummaryPage` красный.
- [ ] **Step 3: Реализовать.**
- [ ] **Step 4: Прогнать** — те же файлы, `npx tsc -b`, `npm run lint` зелёные.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/tenders/summary/
git commit -m "feat(round-unallocated): «разнести →» у строки Нераспределённое свода по round_id колонки"
```

---

### Task 14: Стендовая приёмка §4.5 (DoD 1, 3)

Живой стенд `gca_dev`, тендер **449-ТУ** (найти id: `GET /api/v1/tenders?q=449`).
Бэкенд `just dev-backend` (8259), фронт `just dev-frontend` (5173/5174 —
читать лог СВОЕГО vite), учётка `admin@example.com` / `gca-admin-2026`;
браузер — системный Chrome через playwright в scratchpad (`channel: "chrome"`,
проект прошлых сессий — см. память `playwright-via-system-chrome`). Делает
оркестратор; вывод — в devlog.

- [ ] **Step 1: Замер ДО.** SQL по `v_category_totals` (форма `psql -h localhost -p 5459 -U postgres -d gca_dev -A -F'|' -c "…"`):
  число разделов без статьи и сумма `work_category_id IS NULL` по сметам
  этапа 3 (ожидание спеки §1.2: 38 позиций под `14.*`/`15` у всех четырёх;
  480,3 / 495,5 / 613,4 / 535,2 млн). Снять ответ `GET /api/v1/tenders/{id}`
  — `unallocated_pending_sections` по раундам; открыть `GET …/rounds/{r3}/unallocated`
  и убедиться, что `sections` — вершины `14`, `15` и их поддерево, `rows`
  вершины `14` = число позиций под ней по SQL.
- [ ] **Step 2: Экран.** Карточка 449-ТУ: у этапов 3 и 4 тёплый бейдж с
  числом, у этапов 1–2 — свой счётчик или тихое «разнести». Клик по бейджу
  этапа 3 → URL `?unallocated=<r3>`, Sheet «Разнос статей — Этап 3», подзаголовок
  «(4 участника)». Разнести `14` → «20 MR - SHELL & CORE», `15` → «16
  Разработка рабочей документации» (коды проверить по `category_options`).
- [ ] **Step 3: Замер ПОСЛЕ.** Бейдж этапа 3 стал тихим «разнести»
  (счётчик 0); блок «Разнесено вручную» несёт две записи с автором и датой;
  свод трассы любого участника этапов 1–4: строка «Нераспределённое» по
  колонке этапа 3 — `0,00`, подпись колонки «разнос: 2 решения · <дата>»;
  SQL: `work_category_id IS NULL` по сметам этапа 3 — 0 строк-позиций.
  Снять решение и вернуть — DELETE через «снять», проверить возврат
  счётчика и строки свода; повторить PUT.
- [ ] **Step 4: Этапы 1–2.** Sheet этапа 1 и 2: блок «Не закрывается
  разносом» называет АНТТЕК «c +6,650м до +16,500м» и ЕНИГЮН «Дополнительные
  работы» (`unresolved_chapter_ref`); остаток строки «Нераспределённое» в
  своде АНТТЕК по этим колонкам равен вкладу этих строк (316,1 млн / 4,4 млн
  — сверить с `estimate_additional_works.total_amount` по SQL).
- [ ] **Step 5: Раскладка и тема.** Sheet при 1280 и 1100 px, обе темы
  (`getComputedStyle` по узлам): без горизонтальной прокрутки, названия
  разделов зажаты двумя строками на самом длинном наименовании (АGENTS §11).
- [ ] **Step 6: Commit** (если были правки стилей) —
  `fix(round-unallocated): раскладка Sheet по замеру на стенде`.

---

### Task 15: Документы — долг №22, AGENTS §3, рамка контура, devlog

**Files:**
- Modify: `docs/TECH_DEBT.md` (§22)
- Modify: `AGENTS.md` (§3 — права `member`)
- Modify: `docs/proposals/2026-08-25-tenders-model.md` (§4: «фича 4 в работе» → смержена
  PR #35/#36; граница фичи 3 про поверхность разноса закрыта этой фичей, ссылка на спеку)
- Create: `docs/devlog/2026-09-01-round-unallocated.md`

- [ ] **Step 1: `TECH_DEBT.md` §22** — статус «ЗАКРЫТО 2026-09 фичей этапного
  разноса» с ответами на три развилки записи: (1) своя поверхность —
  Sheet на карточке тендера с общим ядром `UnallocatedWorkbench`, панель
  паспорта не тронута; (2) право `member` — подтверждено решением
  пользователя 01.09.2026 (спека §2.3); (3) хуки — свои раундовые, с
  инвалидацией карточки, обоих префиксов свода и ключа верстака. Ссылки на
  спеку, план, devlog. Запись НЕ удаляется — долг описывает историю.
- [ ] **Step 2: `AGENTS.md` §3** — к правам `member` после «ручной разнос
  разделов сметы по статьям классификатора (…)» добавить: «и этапный разнос
  разделов раунда во всех сметах предложений разом ([спека этапного
  разноса](docs/superpowers/specs/2026-09-01-round-unallocated-design.md) §2.3)».
  Инвариант §10 о нуле «Нераспределённого» НЕ правится (спека §3 п.6).
- [ ] **Step 2а: `AGENTS.md` §11** — грабля SQLAlchemy: `with_for_update(key_share=True)`
  компилируется в `FOR NO KEY UPDATE`, настоящий `FOR KEY SHARE` даёт только
  `read=True, key_share=True`. Существующие `crud/tenders._lock_tender(exclusive=False)`
  и `services/round_import.import_round` называют свою блокировку тендера
  «FOR KEY SHARE», а берут `FOR NO KEY UPDATE` — сегодня это безвредно (все
  писатели там взаимно исключают друг друга и так), но докстроки лгут;
  правка их — отдельная запись в `TECH_DEBT.md`, не эта фича.
- [ ] **Step 3: Devlog** — по образцу `2026-08-31-position-drilldown.md`:
  гейты и круги ревью; что сделано по задачам; отступления от плана;
  решения плана 1–8 и их судьба на ревью; негативные проверки задачи 7
  (что снималось, что краснело, скрипт взаимоблокировки); приёмка §4.5 с
  числами; соответствие «требование спеки §4 → тест» списком
  (`replaying-new-rules.md`); границы (§3 спеки).
- [ ] **Step 4: Инсайт** — только если фича выстрадала правило, которого нет
  в `docs/insights/` (AGENTS §12: «положительные приёмы сюда не попадают»).
- [ ] **Step 5: Commit**

```bash
git add docs/TECH_DEBT.md AGENTS.md docs/proposals/2026-08-25-tenders-model.md docs/devlog/2026-09-01-round-unallocated.md
git commit -m "docs(round-unallocated): долг №22 закрыт, права member в AGENTS §3, рамка контура, devlog"
```

---

### Task 16: Финал — CI и PR

- [ ] `cd backend && uv run ruff check . && uv run pytest -q` — зелёные
  (успех читать по числу прошедших, не по коду; `-n 4`, если 8 воркёров дают
  `max_locks_per_transaction`).
- [ ] `cd frontend && npm run lint && npx tsc -b && npm test` — зелёные.
- [ ] `just ci` — НЕ в конвейере: в Bash-инструменте `just ci > ci.log 2>&1; echo EXIT=$?`
  и читать строку `EXIT=` из файла; в PowerShell — `$LASTEXITCODE`.
- [ ] Пуш `feat/round-unallocated`, PR со ссылками на спеку и план
  (REQUIRED SUB-SKILL: superpowers:finishing-a-development-branch); мерж —
  за пользователем.

---

## Самопроверка плана (выполнена при написании)

**Покрытие спеки.** §1.5 (доступность по offer-сметам, не по job) — задачи 2,
5, 12. §2.1 (носитель — раунд, baseline вне выборки) — задачи 2, 6
(`test_baseline_is_not_touched_by_the_same_selection`). §2.2 (входное множество,
четыре состояния по полному вектору, представительная смета, `mapping_broken`
500) — задачи 1, 2, 4. §2.3 (форма GET, `rows` = полное поддерево, без денег,
три 404, `member`) — задачи 2, 4. §2.4 (ключ в теле, `note` обязательное
nullable, таблица исходов ключа до no-op у PUT и DELETE, предикат из двух
условий, атомарная перезапись с единым аудитом, `apply_overrides`, порядок
блокировок) — задачи 6, 7, 8. §2.5 (три кода своим селектором, тот же план
резолва, разрешимая ссылка не в диагностике) — задача 3. §2.6 (счётчик тем же
агрегатором, `null` без offer-смет, прежние поля карточки) — задача 5. §2.7
(триггер отдельным элементом, Sheet, ленивый GET, состояния, заметка в
пикере, ядро без изменения паспорта, URL, хуки и инвалидации без
перекрёстных) — задачи 9–12. §2.8 (ссылка из свода по `round_id`, условие по
`row_count`, колонка без базы НДС) — задача 13. §3 (что не делается) —
границы названы в devlog (задача 15). §4.1–§4.4 — каждый пункт имеет
исполняющий тест в задачах 1–13, включая негативные: конфликт по каждой
компоненте (1), `rows` при конфликте (1, 2), оба условия no-op (6), omitted ≠
null (8), четыре исхода ключа (6, 8), атомарность (6), связка допработ (6),
гонки со снятием защиты (7), DELETE без no-op при отсутствующем ключе (6, 8),
по-сметные тесты без правок (6, 10), разрешимая ссылка вне диагностики (3),
второй формулы нет (5), чужой/устаревший id без запроса (12), отсутствие
перекрёстных инвалидаций (9). §4.5 — задача 14. §5 DoD — задачи 14 (подпись
свода оживает), 15 (долг №22), 4/11 (четыре состояния текстами макета), 5
(тождество карточки), 16 (`just ci`).

**Заглушки.** Все тела тестов, кроме явно помеченных «дописать по образцу
первого» (задачи 10, 11 — однотипные vitest-сценарии с названными
ожиданиями и селекторами), написаны полностью; код реализации приведён для
каждого бэкендового модуля целиком и для фронтовых — интерфейсами с
поведением по пунктам. Имена, которые план упоминает, проверены `grep`-ом по
репозиторию 01.09.2026: `apply_overrides(already_locked=)`, `_lock_estimate`,
`_overrides_of`, `_rows_of`, `_section_metrics`, `_category_options`,
`CategoryRef` (rollup), `resolve_ref`/`categories_by_chapter_number`,
`extract_positions`/`extract_single_proposal`, `RowKind`, `DomainError(code=)`,
`raise_domain_error`, `decimal_json`, `iso`, `get_round`, `member_client`,
`member_client_no_raise`, `admin_user`, `committing_*`, `factories.*Factory`,
`payloads.*`, `import_round`, `_wait_until_a_backend_blocks` (копия);
`qk.tenders.*`, `tendersApi`, `apiErrorStatus`, `toastApiError`, `pluralRu`,
`createTestQueryClient`, `renderWithProviders`, `handlerState`,
`resetHandlerState`, `sampleTenderCard`, `tenderCardFor`,
`stageSummaryWithUnknownSecondColumn`, `Sheet*`, `Textarea`, `Skeleton`,
`StatusPill`. Заводятся этим планом: `round_scene`, `sampleRoundUnallocated`,
`handlerState.roundOverrideRequests`, `lock_estimate` (переименование),
константы `REASON_*`, все модули из таблицы «Структура файлов».

**Согласованность имён между задачами.** `RoundScope`, `load_scope`,
`load_states`, `offer_estimates`, `require_round`, `RoundMappingBroken`,
`CODE_*`, `NO_OFFER_ESTIMATES_MESSAGE` — задача 2, используются в 3–6, 8.
`set_round_override`/`clear_round_override` — задача 6, роутер задачи 8.
`SectionAggregate.classification.state`, `PENDING_STATES` — задача 1, задачи
4–5. `sectionKeyOf`, `testId` вида `lot_1:3` — задачи 10–12. `allocateLink`
— задача 13 и страница. Тексты экрана — один модуль
`roundUnallocatedCopy.ts` (задачи 11–12).

**Что ревью гейта 3 стоит проверить особо:** восемь решений плана в шапке;
достаточность негативного протокола задачи 7 (граница — снятие `order_by`
без `desc()`); гранулярность диагностики; поведение карточки при
`mapping_broken`.
