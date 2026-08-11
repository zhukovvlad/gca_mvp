# Ручной разнос разделов сметы по статьям — план реализации

> **Для агентных исполнителей:** ОБЯЗАТЕЛЬНЫЙ ПОДНАВЫК — `superpowers:subagent-driven-development`
> (рекомендуется) либо `superpowers:executing-plans`. Шаги помечены чекбоксами `- [ ]`.

**Цель:** дать аналитику разнести разделы сметы без статьи классификатора по
статьям прямо в паспорте проекта, не правя файл сметы.

**Архитектура:** решение хранится отдельной таблицей `estimate_category_overrides`
и **применяется прогоном того же `CategoryResolver`** по неизменяемому
`estimate_raw_data.raw_data` с подмешанными решениями; результат материализуется в
те же колонки, что пишет импорт (`position_items.work_category_id` /
`category_source` на строках-разделах и `estimate_additional_works.work_category_id`).
Закон наследования остаётся в одном месте. `v_category_totals` не меняется.

**Стек:** Python 3.12, FastAPI, SQLAlchemy 2.x sync ORM, Alembic, PostgreSQL 16;
React + TS, Vite, shadcn/ui, TanStack Query, vitest + MSW.

**Спека:** [2026-08-11-unallocated-category-override-design.md](../specs/2026-08-11-unallocated-category-override-design.md)
(коммиты `dbe870f`, `a9533e7`). **Рамка фазы:** [phase7-frame.md](../../phase7-frame.md).
**Ветка:** `feat/unallocated-override` (уже создана, спека в ней).

## Global Constraints

- **Деньги — `Decimal` end-to-end**, в JSON строками, `float` не участвует нигде
  (`AGENTS.md` §3). В тестах сравнивать `Decimal` с `Decimal`.
- **Ни одного второго предиката для уже выраженного понятия.** «Это раздел» —
  только `is_chapter` / `RowKind.CHAPTER`; «пусто» — только `_norm(...) == ""`.
- **Ни одного SQL-join по номеру раздела в денежной логике** (запрет Ф3 §1.5).
- **Закон наследования живёт только в `services/category_resolution.py`.** Второй
  обход дерева — в БД, в SQL или в TS — запрещён.
- **Порядок утверждений (спека §2.1):** ручное решение этого раздела > его
  собственная валидная статья из файла > эффективная статья родителя. Приоритет
  **локальный** — сравнивает источники на одной строке.
- **`category_source` протекает вниз вместе со статьёй**: наследник вручную
  разнесённого раздела помечается `'manual'`.
- **Пересчёт всегда полный** (все предложения, весь набор решений) и идемпотентен;
  снятие решения обязано записать `NULL` разделам, ставшим нераспределёнными.
- **Реальных сумм и реквизитов контрагентов — ни в коде, ни в тестах, ни в доках,
  ни в сообщениях коммитов** (рамка фазы 7 §Политика `samples/`). Коды и названия
  статей классификатора — можно.
- **Наименование работы зажимать по высоте всюду, где оно попадает в таблицу**
  (`AGENTS.md` §11: в каталоге есть наименование на 5077 символов). Класс `block`
  рядом с `line-clamp` отменяет `display: -webkit-box` — не ставить.
- **`@media print` в jsdom не наблюдаем** — печать проверяется только замером в
  браузере (`AGENTS.md` §11).
- **Негативные проверки доказываются снятием защиты** по восьми слоям инсайта
  [verifying-guards](../../insights/verifying-guards.md); вход негативного теста
  обязан нарушать **ровно одно** ограничение.
- **Перед пушем — `just ci`** (`AGENTS.md` §9.3). Правки только в `docs/` от него
  освобождены.
- Frontend — **только shadcn/ui** для UI-**примитивов**; новых примитивов не
  писать, недостающие ставить через `npx shadcn add`. Доменные компоненты
  (`UnallocatedPanel`) писать своими — это не примитивы.
- **`passport.categories` — НЕ справочник.** `build_tree` оставляет корни всегда, а
  глубже — только узлы, у которых есть строки
  ([project_passport.py](../../../backend/crud/project_passport.py#L91)). Список
  вариантов для выбора статьи брать **только** из `category_options` (задача 4);
  взять его из `categories` значит недодать аналитику большую часть 362 статей —
  ровно те, которых в смете ещё нет, а разносить надо как раз в них.

## Структура файлов

| Файл | Ответственность |
|---|---|
| `backend/services/category_resolution.py` (M) | закон наследования + приоритет решений; чистый, без `Session` |
| `backend/alembic/versions/2026_08_11_0011-category_overrides.py` (C) | таблица решений, расширение `ck_position_items_category_source`, отказ downgrade при живых решениях |
| `backend/models.py` (M) | ORM-модель `EstimateCategoryOverride`, обновление `CheckConstraint` |
| `backend/services/category_override.py` (C) | применение: блокировка, чтение `raw_data`, прогон резолвера, материализация разделов и допработ |
| `backend/crud/project_passport.py` (M) | метрики дерева разделов (один расчёт), дерево нераспределённого, `manual_assignments`, `own_sections[].source`, `category_options` |
| `backend/crud/contracts.py` (M) | `category_overrides_count` в каждой строке `estimates[]` карточки договора |
| `backend/routers/category_overrides.py` (C) | HTTP-слой: `PUT`/`DELETE`, коды отказов, транзакция |
| `backend/main.py` (M) | регистрация роутера |
| `backend/services/estimate_import.py` (M) | громкость замены: warning об утраченных решениях |
| `frontend/src/types/domain.ts` (M) | типы новых полей ответа |
| `frontend/src/services/api/analytics.ts` (M) | вызовы `PUT`/`DELETE` |
| `frontend/src/services/queries.ts` (M) | мутации с инвалидацией паспорта |
| `frontend/src/pages/passport/UnallocatedPanel.tsx` (C) | панель-верстак: дерево, выбор статьи, список разнесённого |
| `frontend/src/pages/passport/CategoryTable.tsx` (M) | шеврон и разворот, пометка `'manual'`, нулевое состояние, печатная сноска |
| `frontend/src/components/contracts/EstimateUploadPanel.tsx` (M) | предупреждение об утрате разноса **до** загрузки, по счётчику заменяемой пары |

Панель — отдельный файл, а не рост `CategoryTable.tsx`: у неё своя сетка, своё
состояние выбора и свои мутации, а `CategoryTable` уже несёт таблицу, разворот
статей и печатную разметку.

---

### Task 1: Резолвер принимает решения

**Files:**
- Modify: `backend/services/category_resolution.py`
- Test: `backend/tests/unit/test_category_resolution.py`

**Interfaces:**
- Consumes: существующие `CategoryRef(id, title)`, `RowResolution`, `ResolutionCounters`, `CategoryResolver`.
- Produces:
  - `CATEGORY_SOURCE_MANUAL = "manual"`
  - `CategoryResolver.resolve_proposal(positions, overrides: Mapping[str, int] | None = None) -> ProposalResolution`
  - `ResolutionCounters.chapters_manual: int = 0` — число строк-разделов, чья
    **эффективная** статья имеет происхождение `manual` (свои + унаследованные).
  - `CategoryResolutionContractError` — при `work_category_id`, которого нет в карте.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `backend/tests/unit/test_category_resolution.py` (хелперы `rows`,
`chapter`, `work`, фикстура `resolver` и карта `CATALOG` уже есть в файле):

```python
from services.category_resolution import CATEGORY_SOURCE_MANUAL  # к существующему импорту


class TestOverrides:
    """Приоритет решений (спека §2.1) и протекание источника вниз (§2.4)."""

    def test_empty_overrides_change_nothing(self, resolver):
        payload = rows(chapter("1", article="1. Подготовительные работы"), work(number="2"))
        assert resolver.resolve_proposal(payload, {}) == resolver.resolve_proposal(payload)

    def test_override_assigns_a_chapter_the_file_left_blank(self, resolver):
        result = resolver.resolve_proposal(rows(chapter("14"), work(number="2")), {"1": 104})
        assert result.rows["1"].work_category_id == 104
        assert result.rows["1"].category_source == CATEGORY_SOURCE_MANUAL
        assert result.counters.chapters_manual == 1
        assert result.counters.chapters_unassigned == 0
        assert result.counters.positions_unassigned == 0

    def test_override_beats_an_unreadable_code(self, resolver):
        payload = rows(chapter("14", article="см. приложение"))
        without = resolver.resolve_proposal(payload)
        assert without.rows["1"].work_category_id is None
        assert any("не читается" in w for w in without.warnings)

        result = resolver.resolve_proposal(payload, {"1": 104})
        assert result.rows["1"].work_category_id == 104
        assert result.rows["1"].category_source == CATEGORY_SOURCE_MANUAL
        # Клетку не читали — значит и о её нечитаемости не сообщаем.
        assert not any("не читается" in w for w in result.warnings)

    def test_override_beats_a_valid_file_article(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("1", article="1. Подготовительные работы")), {"1": 104}
        )
        assert result.rows["1"].work_category_id == 104
        assert result.rows["1"].category_source == CATEGORY_SOURCE_MANUAL

    def test_descendant_inherits_the_manual_source(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("14"), chapter("14.1", key_number="2"), work(number="3")),
            {"1": 104},
        )
        assert result.rows["2"].work_category_id == 104
        assert result.rows["2"].category_source == CATEGORY_SOURCE_MANUAL
        # Оба раздела держатся на решении — счётчик по ЭФФЕКТИВНОМУ источнику.
        assert result.counters.chapters_manual == 2

    def test_own_valid_article_survives_a_manual_parent(self, resolver):
        result = resolver.resolve_proposal(
            rows(chapter("4"), chapter("4.1", article="4.1 Ж/Б конструкции", key_number="2")),
            {"1": 101},
        )
        assert result.rows["1"].category_source == CATEGORY_SOURCE_MANUAL
        assert result.rows["2"].work_category_id == 141
        assert result.rows["2"].category_source == CATEGORY_SOURCE_FILE
        assert result.counters.chapters_manual == 1

    def test_removing_the_override_returns_the_chapter_to_unassigned(self, resolver):
        payload = rows(chapter("14"), work(number="2"))
        assert resolver.resolve_proposal(payload, {"1": 104}).rows["1"].work_category_id == 104
        after = resolver.resolve_proposal(payload, {})
        assert after.rows["1"].work_category_id is None
        assert after.rows["1"].category_source is None
        assert after.counters.chapters_manual == 0

    def test_unknown_category_id_is_a_contract_error(self, resolver):
        with pytest.raises(CategoryResolutionContractError):
            resolver.resolve_proposal(rows(chapter("14")), {"1": 999999})

    def test_overrides_do_not_revive_a_disabled_structure(self, resolver):
        # Номер раздела не разбирается -> структура гашена по всему предложению.
        result = resolver.resolve_proposal(rows(chapter("14а")), {"1": 104})
        assert result.structure_disabled is True
        assert result.rows["1"].work_category_id is None
        assert result.counters.chapters_manual == 0
```

- [ ] **Step 2: Прогнать и убедиться, что падают**

Run: `cd backend && uv run pytest tests/unit/test_category_resolution.py -k Overrides -v`
Expected: FAIL — `ImportError: cannot import name 'CATEGORY_SOURCE_MANUAL'`.

- [ ] **Step 3: Реализовать**

В `backend/services/category_resolution.py`:

```python
#: Второй источник привязки. Порядок утверждений — спека разноса §2.1:
#: ручное решение этого раздела > его валидная статья из файла > статья родителя.
CATEGORY_SOURCE_MANUAL = "manual"
```

`_StackEntry` начинает нести происхождение вместе со статьёй — иначе источник не
протечёт вниз, и наследник вручную разнесённого раздела был бы помечен `'file'`:

```python
@dataclass
class _StackEntry:
    position_key: str
    depth: int
    category: CategoryRef | None
    category_source: str | None
    """Происхождение ЭФФЕКТИВНОЙ статьи: `file`, `manual` либо `None`. Едет вместе
    с `category`, потому что наследование обязано наследовать и источник."""
```

`ResolutionCounters` получает поле (добавлять **в конец**, у датакласса позиционные
конструкторы в тестах Ф3):

```python
    chapters_manual: int = 0
    """Строки-разделы, чья ЭФФЕКТИВНАЯ статья пришла из ручного решения — свои и
    унаследованные. Это НЕ число решений: одно решение на вершине даёт столько
    разделов, сколько их в поддереве. Число решений знает вызывающий по таблице."""
```

`__init__` строит обратную карту (решение приходит идентификатором, а не кодом):

```python
    def __init__(self, by_code: Mapping[str, CategoryRef]) -> None:
        self._by_code = dict(by_code)
        self._by_id = {ref.id: ref for ref in self._by_code.values()}
```

`resolve_proposal` и `_resolve_stack` принимают решения:

```python
    def resolve_proposal(
        self,
        positions: Mapping[str, Any],
        overrides: Mapping[str, int] | None = None,
    ) -> ProposalResolution:
        keys = _ordered_keys(positions)
        conflicts = _structural_conflicts(positions, keys)
        bad_numbers = _unparsable_numbers(positions, keys)
        if conflicts or bad_numbers:
            # Решения сюда НЕ передаются намеренно (спека §1.6): привязки нет ни у
            # одной строки предложения, наследовать назначенную статью некому.
            return _disabled(positions, keys, conflicts, bad_numbers)
        return self._resolve_stack(positions, keys, overrides or {})
```

В `_resolve_stack`: новый счётчик, передача решения в `_article_for`, запись
источника в стек.

```python
        own = inherited = unassigned = positions_unassigned = outside = manual = 0
        ...
            raw = _trimmed(row.get(JSON_KEY_ARTICLE_SMR))
            category, source, outcome = self._article_for(
                raw, parent, warnings, key, row, overrides.get(key)
            )
            if outcome == "own":
                own += 1
            elif outcome == "inherited":
                inherited += 1
            elif outcome == "manual":
                own += 1  # своя статья у раздела есть — просто не из файла
            else:
                unassigned += 1
                warnings.unassigned_chapters.append(_place(key, row))
            if source == CATEGORY_SOURCE_MANUAL:
                manual += 1

            rows[key] = RowResolution(
                position_key=key,
                kind=kind,
                parent_position_key=parent.position_key if parent else None,
                smr_article_raw=raw,
                work_category_id=category.id if category else None,
                category_source=source,
            )
            stack.append(
                _StackEntry(position_key=key, depth=depth, category=category, category_source=source)
            )
```

и в конце — `chapters_manual=manual` в `ResolutionCounters`.

`_article_for` получает решение и ставит его **первой** ступенью:

```python
    def _article_for(
        self,
        raw: str | None,
        parent: _StackEntry | None,
        warnings: _Warnings,
        key: str,
        row: Mapping[str, Any],
        override_id: int | None,
    ) -> tuple[CategoryRef | None, str | None, str]:
        """Эффективная статья раздела, её происхождение и итог для счётчика.

        **Ручное решение сильнее файла.** Не из вежливости к человеку: у раздела
        может стоять нечитаемый или неизвестный код, и тогда файл НЕ молчит —
        правило Ф3 «утверждение файла сильнее наследования» само по себе оставило
        бы такой раздел неразносимым навсегда. Клетка при решении не читается
        вовсе, поэтому и предупреждения о ней не выдаются: они уже записаны в
        историю той загрузки, которая их нашла.

        **Утверждение файла сильнее наследования** (правило Ф3) — не тронуто.
        """
        if override_id is not None:
            ref = self._by_id.get(override_id)
            if ref is None:
                raise CategoryResolutionContractError(
                    f"Решение по разделу «{key}» ссылается на статью {override_id}, "
                    "которой нет в справочнике. FK это исключает, значит карта статей "
                    "собрана не из той же таблицы, и молчать об этом нельзя."
                )
            return ref, CATEGORY_SOURCE_MANUAL, "manual"

        if raw is None:
            inherited = parent.category if parent else None
            source = parent.category_source if inherited else None
            return inherited, source, "inherited" if inherited else "unassigned"

        collapsed = _norm(raw)
        prefix = collapsed.split(" ")[0]
        if not _CODE_RE.match(prefix):
            warnings.unreadable_prefix.append(_place(key, row, raw=collapsed))
            return None, None, "unassigned"

        code = prefix.rstrip(".")
        ref = self._by_code.get(code)
        if ref is None:
            warnings.unknown_code[code].append(_place(key, row))
            return None, None, "unassigned"

        in_file = collapsed[len(prefix):].strip()
        if in_file and in_file.casefold() != _norm(ref.title).casefold():
            warnings.title_mismatch.setdefault(code, (in_file, ref.title))
        return ref, CATEGORY_SOURCE_FILE, "own"
```

- [ ] **Step 4: Прогнать всё юнит-покрытие резолвера**

Run: `cd backend && uv run pytest tests/unit/test_category_resolution.py tests/unit/test_additional_works.py -v`
Expected: PASS, включая **все** тесты Ф3 и Ф4 — сигнатура расширена значением по
умолчанию, поведение импорта не изменилось.

- [ ] **Step 5: Доказать защиту снятием**

Для каждой из трёх защит снять её, увидеть красный, вернуть:
1. убрать проверку `override_id is not None` в начале `_article_for` →
   `test_override_beats_a_valid_file_article` и `..._unreadable_code` краснеют;
2. в `_article_for` вернуть `CATEGORY_SOURCE_FILE` вместо `parent.category_source`
   в ветке наследования → `test_descendant_inherits_the_manual_source` краснеет;
3. передать `overrides` в `_disabled` → `test_overrides_do_not_revive_a_disabled_structure`
   краснеет.

Записать в devlog, что снятие каждой применялось и давало красный.

- [ ] **Step 6: Коммит**

```bash
git add backend/services/category_resolution.py backend/tests/unit/test_category_resolution.py
git commit -m "feat(category): резолвер принимает ручные решения о статье раздела

Приоритет: решение раздела > его валидная статья из файла > статья родителя.
Источник протекает вниз вместе со статьёй, поэтому наследник разнесённого
вручную раздела помечается manual. Сигнатура расширена значением по умолчанию —
поведение импорта не изменилось."
```

---

### Task 2: Миграция 0011 и модель решения

**Files:**
- Create: `backend/alembic/versions/2026_08_11_0011-category_overrides.py`
- Modify: `backend/models.py`
- Test: `backend/tests/integration/test_category_overrides_schema.py`

**Interfaces:**
- Consumes: `position_items`, `work_categories`, `users` (её `id` — **`Integer`**, не `BigInteger`).
- Produces: таблица `estimate_category_overrides`, модель `EstimateCategoryOverride`
  с полями `position_item_id`, `work_category_id`, `assigned_by`, `assigned_at`, `note`;
  `ck_position_items_category_source` разрешает `('file','manual')`.

- [ ] **Step 1: Написать падающий тест схемы**

Создать `backend/tests/integration/test_category_overrides_schema.py`:

```python
"""Схема решений о статье (спека разноса §2.2, §4.2). Integration: нужен живой PG."""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from models import EstimateCategoryOverride

pytestmark = pytest.mark.integration


def test_manual_is_accepted_and_a_third_value_is_not(db_session, chapter_row, category_id):
    # Статья ОБЯЗАТЕЛЬНА: ck_position_items_category_source_pairs требует, чтобы
    # work_category_id и category_source были заполнены или пусты ВМЕСТЕ. Без
    # `category_id` тест падал бы о парный констрейнт, то есть проверял бы не то,
    # что заявлено, — вход негативного теста обязан нарушать РОВНО ОДНО ограничение.
    db_session.execute(
        sa.text(
            "UPDATE position_items SET work_category_id = :cat, category_source = 'manual' "
            "WHERE id = :rid"
        ),
        {"cat": category_id, "rid": chapter_row.id},
    )
    db_session.flush()

    with pytest.raises(IntegrityError):
        db_session.execute(
            sa.text("UPDATE position_items SET category_source = 'guess' WHERE id = :rid"),
            {"rid": chapter_row.id},
        )
        db_session.flush()


def test_pairs_constraint_is_not_weakened(db_session, chapter_row):
    """Статья без источника и источник без статьи по-прежнему невозможны."""
    with pytest.raises(IntegrityError):
        db_session.execute(
            sa.text(
                "UPDATE position_items SET work_category_id = NULL, "
                "category_source = 'manual' WHERE id = :rid"
            ),
            {"rid": chapter_row.id},
        )
        db_session.flush()


def test_deleting_the_estimate_takes_the_override(db_session, override_row, estimate_id):
    db_session.execute(sa.text("DELETE FROM estimates WHERE id = :eid"), {"eid": estimate_id})
    db_session.flush()
    assert db_session.get(EstimateCategoryOverride, override_row.position_item_id) is None


def test_category_in_use_cannot_be_deleted(db_session, override_row):
    with pytest.raises(IntegrityError):
        db_session.execute(
            sa.text("DELETE FROM work_categories WHERE id = :cid"),
            {"cid": override_row.work_category_id},
        )
        db_session.flush()


def test_author_of_a_live_decision_cannot_be_deleted(db_session, override_row):
    with pytest.raises(IntegrityError):
        db_session.execute(
            sa.text("DELETE FROM users WHERE id = :uid"), {"uid": override_row.assigned_by}
        )
        db_session.flush()


def test_a_batch_larger_than_five_survives(db_session, chapter_rows_ten, admin_user, category_id):
    """`prepare_threshold = 5` в psycopg3: партия из 5 проходит, из 10 падает
    (инсайт batch-larger-than-five). Объекты схемы проверяются партией."""
    db_session.add_all(
        [
            EstimateCategoryOverride(
                position_item_id=row.id, work_category_id=category_id, assigned_by=admin_user.id
            )
            for row in chapter_rows_ten
        ]
    )
    db_session.flush()
    assert db_session.execute(
        sa.select(sa.func.count()).select_from(EstimateCategoryOverride)
    ).scalar_one() == 10
```

Фикстуры `db_session`, `admin_user` уже есть в `backend/tests/integration/conftest.py`.
Новые — `estimate_id`, `chapter_row`, `chapter_rows_ten`, `category_id`, `override_row` —
добавить туда же, построив смету через существующий хелпер импорта fixture (тот, что
используют `test_project_passport_api.py` и `test_estimate_import.py`); подсмотреть
его имя в этих файлах и **переиспользовать**, а не писать второй.

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_category_overrides_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'EstimateCategoryOverride'`.

- [ ] **Step 3: Написать миграцию**

Создать `backend/alembic/versions/2026_08_11_0011-category_overrides.py`:

```python
"""estimate_category_overrides + источник 'manual' на position_items.

Решение о статье раздела хранится отдельной таблицей и применяется
пересчётом (спека §2.2, §2.3).

`position_item_id` в первичном ключе: «одно решение на раздел» держит схема, а не
код, и повторный `PUT` попадает в конфликт по этому же ключу. Колонки `estimate_id`
нет намеренно — она выводится соединением и, будучи продублированной, могла бы
разойтись с настоящей сметой строки; «решение сгорает вместе со сметой» выполняет
существующий каскад estimates → lots → proposals → position_items.

Того, что цель — именно строка-РАЗДЕЛ этой сметы, схема не выражает: для составного
FK понадобился бы уникальный ключ по (id, is_chapter), а частичный уникальный индекс
FK не обслуживает. Проверяет сервис под блокировкой (спека §2.2).

`users.id` — `integer`, не `bigint`; тип колонки `assigned_by` повторяет его.

Литералы записаны строками: миграция обязана быть неизменной во времени (то же
правило, что у 0002–0010).

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

SOURCE_CHECK_0011 = "category_source IS NULL OR category_source IN ('file','manual')"
SOURCE_CHECK_0010 = "category_source IS NULL OR category_source = 'file'"


def upgrade() -> None:
    op.create_table(
        "estimate_category_overrides",
        sa.Column("position_item_id", sa.BigInteger(), nullable=False),
        sa.Column("work_category_id", sa.BigInteger(), nullable=False),
        sa.Column("assigned_by", sa.Integer(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("position_item_id", name="pk_estimate_category_overrides"),
        sa.ForeignKeyConstraint(
            ["position_item_id"],
            ["position_items.id"],
            ondelete="CASCADE",
            name="fk_estimate_category_overrides_position_item_id",
        ),
        sa.ForeignKeyConstraint(
            ["work_category_id"],
            ["work_categories.id"],
            ondelete="RESTRICT",
            name="fk_estimate_category_overrides_work_category_id",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_estimate_category_overrides_assigned_by",
        ),
    )
    op.create_index(
        "idx_estimate_category_overrides_work_category_id",
        "estimate_category_overrides",
        ["work_category_id"],
    )
    op.drop_constraint("ck_position_items_category_source", "position_items", type_="check")
    op.create_check_constraint(
        "ck_position_items_category_source", "position_items", SOURCE_CHECK_0011
    )


def downgrade() -> None:
    # Откат отказывается САМ и с объяснением, а не роняет сырую ошибку
    # PostgreSQL: констрейнт версии 0010 запрещает 'manual', и живые решения
    # сделали бы `create_check_constraint` непроходимым (то же правило, которым
    # 0003 отказывается откатываться после многокилобайтного наименования).
    #
    # Проверяются ОБЕ таблицы, и это не перестраховка. Материализованных строк
    # 'manual' может не быть при живом решении: если структура разделов
    # предложения не определена, решение записано, а материализация не
    # состоялась. Проверка только по `position_items` в этом случае молча снесла
    # бы таблицу решений вместе с работой аналитика.
    bind = op.get_bind()
    manual = bind.execute(
        sa.text("SELECT count(*) FROM position_items WHERE category_source = 'manual'")
    ).scalar_one()
    decisions = bind.execute(
        sa.text("SELECT count(*) FROM estimate_category_overrides")
    ).scalar_one()
    if manual or decisions:
        raise RuntimeError(
            f"Откат 0011 невозможен: ручных решений о статьях — {decisions}, "
            f"материализованных строк-разделов с category_source='manual' — {manual}. "
            "Констрейнт версии 0010 такие значения запрещает, а таблица решений "
            "исчезла бы вместе с работой аналитика. Снимите ручные решения через API "
            "либо удалите сметы, к которым они относятся, — это потеря данных и "
            "решение человека, а не миграции."
        )
    op.drop_constraint("ck_position_items_category_source", "position_items", type_="check")
    op.create_check_constraint(
        "ck_position_items_category_source", "position_items", SOURCE_CHECK_0010
    )
    op.drop_index(
        "idx_estimate_category_overrides_work_category_id",
        table_name="estimate_category_overrides",
    )
    op.drop_table("estimate_category_overrides")
```

- [ ] **Step 4: Добавить модель**

В `backend/models.py` — рядом с `PositionItem`, после неё:

```python
class EstimateCategoryOverride(Base):
    """Ручное решение о статье строки-раздела (миграция 0011).

    Первичный ключ — сама строка-раздел: «одно решение на раздел» держит схема.
    `RESTRICT` на статью и на автора: у решения, попадающего в паспорт для банка,
    и статья, и автор должны оставаться живыми.
    """

    __tablename__ = "estimate_category_overrides"

    position_item_id = Column(
        BigInteger,
        ForeignKey(
            "position_items.id",
            ondelete="CASCADE",
            name="fk_estimate_category_overrides_position_item_id",
        ),
        primary_key=True,
    )
    work_category_id = Column(
        BigInteger,
        ForeignKey(
            "work_categories.id",
            ondelete="RESTRICT",
            name="fk_estimate_category_overrides_work_category_id",
        ),
        nullable=False,
    )
    assigned_by = Column(
        Integer,
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            name="fk_estimate_category_overrides_assigned_by",
        ),
        nullable=False,
    )
    assigned_at = _created_at()
    note = Column(Text, nullable=True)

    __table_args__ = (
        Index(
            "idx_estimate_category_overrides_work_category_id",
            "work_category_id",
        ),
    )
```

И в существующем `__table_args__` у `PositionItem` заменить текст констрейнта:

```python
        CheckConstraint(
            "category_source IS NULL OR category_source IN ('file','manual')",
            name="ck_position_items_category_source",
        ),
```

- [ ] **Step 5: Накатить и прогнать**

```bash
cd backend && uv run alembic upgrade head
uv run pytest tests/integration/test_category_overrides_schema.py -v
```
Expected: PASS (6 тестов).

- [ ] **Step 6: Проверить круговой рейс**

```bash
cd backend && uv run alembic downgrade base && uv run alembic upgrade head
```
Expected: обе команды успешны на **чистой** БД (живых `'manual'` нет).

Затем проверить отказ downgrade **двумя независимыми входами** — по одному на
каждое слагаемое условия, иначе одно из них не доказано:

1. только материализация: поставить строке-разделу `work_category_id` и
   `category_source='manual'`, таблицу решений оставить пустой → `alembic downgrade 0010`
   даёт `RuntimeError` с текстом про ручной разнос;
2. только решение: вставить строку в `estimate_category_overrides`, ни одной
   строки `'manual'` в `position_items` → тот же отказ. Это и есть случай, ради
   которого проверяются обе таблицы: при неопределённой структуре предложения
   решение есть, а материализации нет.

После каждого — вернуть состояние и убедиться, что `downgrade` проходит на чистой БД.

- [ ] **Step 7: Коммит**

```bash
git add backend/alembic/versions/2026_08_11_0011-category_overrides.py backend/models.py backend/tests/integration/test_category_overrides_schema.py backend/tests/integration/conftest.py
git commit -m "feat(schema): миграция 0011 — таблица решений о статье раздела

PK по position_item_id, каскад от сметы, RESTRICT на статью и автора.
ck_position_items_category_source расширен до ('file','manual'); downgrade
отказывается сам и с объяснением, если живые решения есть."
```

---

### Task 3: Сервис применения решений

**Files:**
- Create: `backend/services/category_override.py`
- Test: `backend/tests/integration/test_category_override_apply.py`

**Interfaces:**
- Consumes: `CategoryResolver`, `CATEGORY_SOURCE_MANUAL`, `EstimateCategoryOverride`,
  `categories_by_chapter_number`, `resolve_ref` (из `services/additional_works.py`),
  `_extract_positions` (из `services/estimate_import.py` — **переиспользовать, а не
  копировать**; при необходимости переименовать в `extract_positions` и оставить
  алиас на старое имя внутри модуля импорта).
- Produces:
  - `class CategoryOverrideError(Exception)` с полем
    `code: Literal["not_found","not_a_chapter","structure_disabled","mapping_broken"]`.
    `not_found` покрывает **три** случая: сметы нет (в т.ч. её удалила замена под
    блокировкой), раздела нет либо он из другой сметы, **статьи нет**. Последнее —
    отдельная проверка `_require_category`, а не надежда на FK: `IntegrityError`
    вылетел бы уже из `flush` и стал бы `500`, тогда как спека §2.7 обещает `404`.
  - `def apply_overrides(db: Session, estimate_id: int) -> ApplyResult`
  - `def set_override(db, *, estimate_id, position_item_id, work_category_id, note, user_id) -> ApplyResult`
  - `def clear_override(db, *, estimate_id, position_item_id) -> ApplyResult`
  - `@dataclass(frozen=True) class ApplyResult: chapters_updated: int; additional_works_updated: int; chapters_manual: int`

- [ ] **Step 1: Написать падающие тесты**

Создать `backend/tests/integration/test_category_override_apply.py`:

```python
"""Применение решений о статье (спека разноса §2.3–2.5, §4.3)."""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from crud.project_passport import get_project_passport
from models import EstimateAdditionalWork, EstimateCategoryOverride, PositionItem
from services.category_override import (
    CategoryOverrideError,
    apply_overrides,
    clear_override,
    set_override,
)
from services.category_resolution import CATEGORY_SOURCE_MANUAL

pytestmark = pytest.mark.integration


def _grand_total(db, contract_id) -> Decimal | None:
    return get_project_passport(db, contract_id)["totals"]["amount"]


def test_assigning_a_top_chapter_reaches_the_whole_subtree(
    db_session, imported_estimate, top_unassigned_chapter, category_id, admin_user
):
    before = _grand_total(db_session, imported_estimate.contract_id)
    positions_before = db_session.execute(
        sa.select(sa.func.count()).select_from(PositionItem).where(
            PositionItem.is_chapter.is_(False)
        )
    ).scalar_one()

    result = set_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.flush()

    assert result.chapters_updated > 1, "поддерево должно наследовать решение"
    # Ни одна ПОЗИЦИЯ не изменена: статью несут только строки-разделы (спека §1.3).
    assert db_session.execute(
        sa.select(sa.func.count()).select_from(PositionItem).where(
            PositionItem.is_chapter.is_(False),
            PositionItem.work_category_id.is_not(None),
        )
    ).scalar_one() == 0
    assert db_session.execute(
        sa.select(sa.func.count()).select_from(PositionItem).where(
            PositionItem.is_chapter.is_(False)
        )
    ).scalar_one() == positions_before
    assert db_session.get(PositionItem, top_unassigned_chapter.id).category_source == (
        CATEGORY_SOURCE_MANUAL
    )
    # Инвариант фичи: итог сметы не двинулся, деньги только переехали.
    assert _grand_total(db_session, imported_estimate.contract_id) == before


def test_a_recalculation_without_decisions_reproduces_the_import(db_session, imported_estimate):
    """Главная проверка выбранного подхода (спека §2.3): `raw_data` неизменяем,
    поэтому пересчёт без решений обязан дать в точности то, что записал импорт."""
    snapshot = _chapter_snapshot(db_session, imported_estimate.id)
    apply_overrides(db_session, imported_estimate.id)
    db_session.flush()
    assert _chapter_snapshot(db_session, imported_estimate.id) == snapshot


def test_applying_twice_changes_nothing_the_second_time(
    db_session, imported_estimate, top_unassigned_chapter, category_id, admin_user
):
    set_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.flush()
    once = _chapter_snapshot(db_session, imported_estimate.id)

    again = apply_overrides(db_session, imported_estimate.id)
    db_session.flush()
    assert _chapter_snapshot(db_session, imported_estimate.id) == once
    # Счётчики ОБЯЗАНЫ быть нулями: снимок совпал бы и при безусловной перезаписи
    # теми же значениями, то есть сам по себе он защиту «писать только
    # изменившееся» не стережёт — краснеет только это утверждение.
    assert again.chapters_updated == 0
    assert again.additional_works_updated == 0


def test_a_missing_category_is_refused_before_the_flush(
    db_session, imported_estimate, top_unassigned_chapter, admin_user
):
    """Спека §2.7 обещает `404`, а не `500`: FK дал бы `IntegrityError` из flush."""
    with pytest.raises(CategoryOverrideError) as exc:
        set_override(
            db_session,
            estimate_id=imported_estimate.id,
            position_item_id=top_unassigned_chapter.id,
            work_category_id=10**9,
            note=None,
            user_id=admin_user.id,
        )
    assert exc.value.code == "not_found"


def test_clearing_a_decision_that_is_not_there_succeeds(db_session, imported_estimate,
                                                        top_unassigned_chapter):
    """Снятие идемпотентно: два оператора могут снять одно решение одновременно, и
    второму нечего сообщить об ошибке — состояние уже такое, какого он хотел.
    Пересчёт при этом всё равно выполняется: он и есть смысл вызова."""
    result = clear_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
    )
    assert result.chapters_updated == 0


def test_an_additional_work_referencing_the_chapter_gets_the_same_article(
    db_session, estimate_with_extra_ref, referenced_chapter, category_id, admin_user
):
    """Замер спеки §1.4: до разноса `resolve_ref` даёт «кандидат без статьи»."""
    extra = db_session.execute(sa.select(EstimateAdditionalWork)).scalars().first()
    assert extra.work_category_id is None

    result = set_override(
        db_session,
        estimate_id=estimate_with_extra_ref.id,
        position_item_id=referenced_chapter.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.flush()

    assert result.additional_works_updated == 1
    db_session.refresh(extra)
    assert extra.work_category_id == category_id


def test_clearing_returns_chapters_and_extras_to_their_import_state(
    db_session, estimate_with_extra_ref, referenced_chapter, category_id, admin_user
):
    snapshot = _chapter_snapshot(db_session, estimate_with_extra_ref.id)
    extras_before = _extras_snapshot(db_session, estimate_with_extra_ref.id)

    set_override(
        db_session,
        estimate_id=estimate_with_extra_ref.id,
        position_item_id=referenced_chapter.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.flush()
    clear_override(
        db_session,
        estimate_id=estimate_with_extra_ref.id,
        position_item_id=referenced_chapter.id,
    )
    db_session.flush()

    assert _chapter_snapshot(db_session, estimate_with_extra_ref.id) == snapshot
    assert _extras_snapshot(db_session, estimate_with_extra_ref.id) == extras_before
    assert db_session.get(EstimateCategoryOverride, referenced_chapter.id) is None


def test_a_non_chapter_target_is_refused(
    db_session, imported_estimate, any_position_row, category_id, admin_user
):
    with pytest.raises(CategoryOverrideError) as exc:
        set_override(
            db_session,
            estimate_id=imported_estimate.id,
            position_item_id=any_position_row.id,
            work_category_id=category_id,
            note=None,
            user_id=admin_user.id,
        )
    assert exc.value.code == "not_a_chapter"


def test_a_chapter_of_another_estimate_is_not_found(
    db_session, imported_estimate, other_estimate_chapter, category_id, admin_user
):
    with pytest.raises(CategoryOverrideError) as exc:
        set_override(
            db_session,
            estimate_id=imported_estimate.id,
            position_item_id=other_estimate_chapter.id,
            work_category_id=category_id,
            note=None,
            user_id=admin_user.id,
        )
    assert exc.value.code == "not_found"


def test_a_disabled_structure_refuses_the_decision(
    db_session, estimate_with_broken_numbering, broken_chapter, category_id, admin_user
):
    with pytest.raises(CategoryOverrideError) as exc:
        set_override(
            db_session,
            estimate_id=estimate_with_broken_numbering.id,
            position_item_id=broken_chapter.id,
            work_category_id=category_id,
            note=None,
            user_id=admin_user.id,
        )
    assert exc.value.code == "structure_disabled"


def test_a_broken_bijection_is_refused_loudly(db_session, imported_estimate):
    """Ключ `raw_data` без строки в БД (спека §2.3). Сегодня недостижимо —
    воспроизводится удалением одной строки в обход домена."""
    victim = db_session.execute(
        sa.select(PositionItem.id).join(PositionItem.proposal).limit(1)
    ).scalar_one()
    db_session.execute(sa.text("DELETE FROM position_items WHERE id = :rid"), {"rid": victim})
    db_session.flush()
    with pytest.raises(CategoryOverrideError) as exc:
        apply_overrides(db_session, imported_estimate.id)
    assert exc.value.code == "mapping_broken"


def _chapter_snapshot(db, estimate_id) -> dict[int, tuple[int | None, str | None]]:
    rows = db.execute(
        sa.text(
            "SELECT pi.id, pi.work_category_id, pi.category_source "
            "FROM position_items pi JOIN proposals p ON p.id = pi.proposal_id "
            "JOIN lots l ON l.id = p.lot_id WHERE l.estimate_id = :eid AND pi.is_chapter"
        ),
        {"eid": estimate_id},
    ).all()
    return {r[0]: (r[1], r[2]) for r in rows}


def _extras_snapshot(db, estimate_id) -> dict[int, int | None]:
    rows = db.execute(
        sa.text(
            "SELECT aw.id, aw.work_category_id FROM estimate_additional_works aw "
            "JOIN proposals p ON p.id = aw.proposal_id JOIN lots l ON l.id = p.lot_id "
            "WHERE l.estimate_id = :eid"
        ),
        {"eid": estimate_id},
    ).all()
    return {r[0]: r[1] for r in rows}
```

Фикстуры `imported_estimate`, `top_unassigned_chapter`, `any_position_row`,
`other_estimate_chapter`, `estimate_with_extra_ref`, `referenced_chapter`,
`estimate_with_broken_numbering`, `broken_chapter`, `category_id` — в
`backend/tests/integration/conftest.py`, на **обезличенных** fixture-файлах Ф3/Ф4
(реальные суммы в тесты не попадают).

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_category_override_apply.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.category_override'`.

- [ ] **Step 3: Реализовать сервис**

Создать `backend/services/category_override.py`. Ключевые части — блокировка,
биекция, материализация и пересчёт допработ:

```python
"""Применение ручных решений о статье раздела (спека разноса §2.3–2.5).

Решение НЕ применяется точечным `UPDATE` поддерева: оно подмешивается в вход того
же `CategoryResolver`, который работал на импорте, и результат материализуется в
те же колонки. Закон наследования поэтому остаётся в одном месте — это и есть
причина, по которой выбран этот подход, а не прямой обход дерева в БД.

Вход резолвера собирается из `estimate_raw_data.raw_data`, а не из строк БД, хотя
из строк он тоже собрался бы (`item_number_in_proposal`, `chapter_number_in_proposal`,
`smr_article_raw`, `is_chapter`, `job_title_in_proposal` — всё на месте). Причина в
проверяемости: `raw_data` неизменяем, поэтому пересчёт БЕЗ решений обязан дать в
точности то, что записал импорт, и это утверждение исполняется тестом
`test_a_recalculation_without_decisions_reproduces_the_import`. Из строк БД такой
проверки не построить — `smr_article_raw` на не-разделах запрещён констрейнтом,
то есть один класс предупреждений Ф3 по построению невоспроизводим.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import (
    Estimate,
    EstimateAdditionalWork,
    EstimateCategoryOverride,
    EstimateRawData,
    Lot,
    PositionItem,
    Proposal,
)
from parser.constants import JSON_KEY_LOTS
from services.additional_works import categories_by_chapter_number, resolve_ref
from services.category_resolution import CategoryResolver

ErrorCode = Literal["not_found", "not_a_chapter", "structure_disabled", "mapping_broken"]


class CategoryOverrideError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code


@dataclass(frozen=True)
class ApplyResult:
    chapters_updated: int
    additional_works_updated: int
    chapters_manual: int


def _lock_estimate(db: Session, estimate_id: int) -> Estimate:
    """Блокировка строки сметы на весь пересчёт (спека §2.5).

    Держит два случая, оба достижимы при одном worker'е (импорт живёт в
    `BackgroundTasks` со своей сессией): два параллельных разноса и разнос против
    замены сметы. Если замена успела первой, строки уже нет — и это ТОТ ЖЕ
    `not_found`, что для сметы, которой не было изначально: различить два случая
    сервис не может и не должен делать вид, что может (спека §2.5).
    """
    estimate = db.execute(
        sa.select(Estimate).where(Estimate.id == estimate_id).with_for_update()
    ).scalar_one_or_none()
    if estimate is None:
        raise CategoryOverrideError("not_found", f"Смета {estimate_id} не найдена.")
    return estimate
```

Далее — `set_override` / `clear_override`, оба через один `apply_overrides`:

```python
def set_override(
    db: Session,
    *,
    estimate_id: int,
    position_item_id: int,
    work_category_id: int,
    note: str | None,
    user_id: int,
) -> ApplyResult:
    """UPSERT решения и полный пересчёт. Транзакцию ведёт вызывающий.

    Повторный вызов с ТЕМИ ЖЕ `work_category_id` и `note` — no-op: `assigned_by` и
    `assigned_at` сохраняют прежние значения (спека §2.2). «UPSERT идемпотентен по
    построению» верно для строки-результата, но не для того, кто и когда её
    поставил, — а в паспорте для банка автор решения и есть содержательная часть.
    """
    _lock_estimate(db, estimate_id)
    _require_chapter_of(db, estimate_id, position_item_id)
    # Существование статьи проверяется ЗДЕСЬ, а не оставляется на FK: нарушение
    # FK вылетает из `flush` как `IntegrityError` и превратилось бы в `500`,
    # тогда как спека §2.7 обещает на несуществующую статью `404`.
    _require_category(db, work_category_id)

    existing = db.get(EstimateCategoryOverride, position_item_id)
    if existing is None:
        db.add(
            EstimateCategoryOverride(
                position_item_id=position_item_id,
                work_category_id=work_category_id,
                assigned_by=user_id,
                note=note,
            )
        )
    elif existing.work_category_id != work_category_id or existing.note != note:
        existing.work_category_id = work_category_id
        existing.note = note
        existing.assigned_by = user_id
        existing.assigned_at = sa.func.now()
    # else: ничего не меняем — аудит остаётся прежним.
    db.flush()
    return apply_overrides(db, estimate_id, already_locked=True)
```

`apply_overrides` — сердце. Порядок: карта решений по предложениям → на каждое
предложение прогон резолвера → материализация разделов → пересчёт допработ.

```python
def apply_overrides(db: Session, estimate_id: int, *, already_locked: bool = False) -> ApplyResult:
    if not already_locked:
        _lock_estimate(db, estimate_id)

    raw = db.execute(
        sa.select(EstimateRawData.raw_data).where(EstimateRawData.estimate_id == estimate_id)
    ).scalar_one_or_none()
    if raw is None:
        raise CategoryOverrideError(
            "not_found",
            f"У сметы {estimate_id} нет разобранной копии файла — пересчёт невозможен.",
        )

    resolver = CategoryResolver.from_db(db)
    chapters_updated = extras_updated = chapters_manual = 0

    for lot_key, proposal_id, positions in _proposals_with_positions(db, estimate_id, raw):
        rows_by_key, ids_by_key = _rows_of(db, proposal_id)
        _require_bijection(lot_key, positions, ids_by_key)

        overrides = _overrides_of(db, proposal_id, ids_by_key)
        resolution = resolver.resolve_proposal(positions, overrides)
        if resolution.structure_disabled and overrides:
            raise CategoryOverrideError(
                "structure_disabled",
                f"Структура разделов предложения «{lot_key}» не определена, поэтому "
                "статьи не привязываются ни к одной его строке. Исправьте нумерацию "
                "разделов и загрузите файл повторно.",
            )

        chapters_manual += resolution.counters.chapters_manual
        chapters_updated += _materialize_chapters(db, rows_by_key, resolution)
        extras_updated += _materialize_extras(db, proposal_id, positions, resolution)

    db.flush()
    return ApplyResult(chapters_updated, extras_updated, chapters_manual)
```

Материализация разделов пишет **только изменившееся** — тогда
`chapters_updated` честно отвечает «сколько строк поехало», а не «сколько
просмотрено»:

```python
def _materialize_chapters(db, rows_by_key, resolution) -> int:
    changed = 0
    for key, decision in resolution.rows.items():
        row = rows_by_key.get(key)
        if row is None or not row.is_chapter:
            continue
        if (row.work_category_id, row.category_source) == (
            decision.work_category_id,
            decision.category_source,
        ):
            continue
        row.work_category_id = decision.work_category_id
        row.category_source = decision.category_source
        changed += 1
    return changed
```

Пересчёт допработ — **тем же** резолвом ссылки, что на импорте; состав и суммы
строк не перестраиваются:

```python
def _materialize_extras(db, proposal_id, positions, resolution) -> int:
    """Статья строки допработ выводится из плана резолва (спека §1.4), поэтому
    разнос раздела обязан её пересчитать: множество кандидатов по номеру раздела
    меняется с `{None}` на `{X}`, и `resolve_ref` переезжает с «кандидат без
    статьи» на статью. Состав и суммы строк НЕ перестраиваются — `parse_lines` и
    `build_rows` здесь не зовутся вовсе.
    """
    by_number = categories_by_chapter_number(positions, resolution)
    rows = db.execute(
        sa.select(EstimateAdditionalWork).where(
            EstimateAdditionalWork.proposal_id == proposal_id
        )
    ).scalars().all()
    changed = 0
    for row in rows:
        if row.chapter_ref_raw is None:
            continue  # ck_..._category_requires_ref: без ссылки статьи быть не может
        category_id, _reason = resolve_ref(row.chapter_ref_raw, by_number)
        if row.work_category_id != category_id:
            row.work_category_id = category_id
            changed += 1
    return changed
```

Биекция и остальные хелперы:

```python
def _require_bijection(lot_key: str, positions, ids_by_key) -> None:
    """Ключ `raw_data` ↔ строка БД (спека §2.3, §1.7). Сегодня расхождение
    недостижимо: импорт вставляет строку на каждый ключ и обратно ничего не
    удаляет. Если оно возникло — значит мы чего-то не знаем о смете, и молчать
    об этом нельзя: тихий пропуск дал бы паспорт, правдоподобный ровно настолько,
    насколько неверный.
    """
    only_in_file = set(positions) - set(ids_by_key)
    only_in_db = set(ids_by_key) - set(positions)
    if only_in_file or only_in_db:
        raise CategoryOverrideError(
            "mapping_broken",
            f"Предложение «{lot_key}»: разобранная копия файла и строки сметы не "
            f"соответствуют друг другу (только в файле: {sorted(only_in_file)[:5]}; "
            f"только в БД: {sorted(only_in_db)[:5]}). Пересчёт статей отменён.",
        )
```

`_proposals_with_positions` идёт по `raw[JSON_KEY_LOTS]`, сопоставляя ключ лота с
`lots.lot_key` (`UNIQUE (estimate_id, lot_key)`) и беря единственное предложение
лота (`AGENTS.md` §4); позиции извлекает **тем же** `_extract_positions`, что импорт.

- [ ] **Step 4: Прогнать**

Run: `cd backend && uv run pytest tests/integration/test_category_override_apply.py -v`
Expected: PASS (9 тестов).

- [ ] **Step 5: Доказать защиту снятием**

1. Убрать `.with_for_update()` — доказывается не тестом, а замером: два
   параллельных `set_override` в двух сессиях на одну смету; без лока второй
   пересчитывает от устаревшего набора. Замер и результат записать в devlog.
2. Убрать вызов `_materialize_extras` →
   `test_an_additional_work_referencing_the_chapter_gets_the_same_article` краснеет.
3. Убрать `_require_bijection` → `test_a_broken_bijection_is_refused_loudly` краснеет.
4. Заменить условие «писать только изменившееся» на безусловную запись →
   `test_assigning_a_top_chapter_reaches_the_whole_subtree` краснеет на
   `chapters_updated`.
5. Убрать проверку `structure_disabled` → `test_a_disabled_structure_refuses_the_decision`
   краснеет.

- [ ] **Step 6: Коммит**

```bash
git add backend/services/category_override.py backend/tests/integration/test_category_override_apply.py backend/tests/integration/conftest.py
git commit -m "feat(category): сервис применения ручных решений о статье

Полный идемпотентный пересчёт под блокировкой строки сметы: вход резолвера из
неизменяемого raw_data, материализация в строки-разделы и в строки допработ.
Соответствие «ключ файла ↔ строка БД» проверяется как биекция."
```

---

### Task 4: Дерево нераспределённого в ответе паспорта

**Files:**
- Modify: `backend/crud/project_passport.py`
- Test: `backend/tests/integration/test_project_passport_api.py`

**Interfaces:**
- Consumes: `get_project_passport`, `_unallocated_breakdown`, `_own_sections_select`,
  уже прочитанный список `WorkCategory` (`refs`).
- Produces:
  - внутреннее — `_section_metrics(db, estimate_id) -> dict[int, SectionMetrics]`,
    **единственный** расчёт метрик дерева разделов; два потребителя ниже;
  - в ответе — `unallocated.sections[]` (`position_item_id`,
    `parent_position_item_id`, `number`, `title`, `depth`, `amount`,
    `subtree_amount`, `rows`, `rows_priced`, `rows_not_finite`, `smr_article_raw`);
  - `manual_assignments[]` — те же поля плюс `work_category_id`, `category_code`,
    `category_title`, `assigned_by_email`, `assigned_at`, `note`;
  - `category_options[]` — `{id, code, title, is_bucket}` по **всему** справочнику;
  - `categories[].own_sections[].source`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `backend/tests/integration/test_project_passport_api.py`:

```python
def test_unallocated_tree_reports_exact_own_and_subtree_metrics(client, unallocated_tree):
    """Числа заданы фикстурой НЕЗАВИСИМО и проверяются точно.

    Фикстура `unallocated_tree` строит ровно такую нераспределённую часть (все
    суммы синтетические, реальных денег в тестах нет):

        вершина  «9»   — своих позиций 0
          ребёнок «9.1» — 2 позиции с ценой: 30 и 30
          ребёнок «9.2» — 1 позиция с ценой: 20
                          + 1 позиция без цены
                          + 1 позиция с ценой 'NaN'

    Тогда: own(9) = None, subtree(9) = 80, rows(9) = 5, rows_priced(9) = 3,
    rows_not_finite(9) = 1; own(9.1) = 60; own(9.2) = 20.

    Проверка «родитель = сумма детей» тут НЕ вырождена: у вершины своих денег нет
    вовсе, поэтому подсчёт разностью «родитель минус дети» дал бы ноль, а не None,
    и это видно по числам, а не по форме.
    """
    body = client.get(f"/api/v1/analytics/project-passport/{unallocated_tree.contract_id}").json()
    sections = {s["number"]: s for s in body["unallocated"]["sections"]}

    top = sections["9"]
    assert top["amount"] is None, "у вершины своих позиций нет — это не ноль"
    assert Decimal(top["subtree_amount"]) == Decimal("80")
    assert (top["rows"], top["rows_priced"], top["rows_not_finite"]) == (5, 3, 1)
    assert top["parent_position_item_id"] is None
    assert top["depth"] == 0

    left = sections["9.1"]
    assert Decimal(left["amount"]) == Decimal("60")
    assert Decimal(left["subtree_amount"]) == Decimal("60")
    assert (left["rows"], left["rows_priced"], left["rows_not_finite"]) == (2, 2, 0)
    assert left["parent_position_item_id"] == top["position_item_id"]
    assert left["depth"] == 1

    right = sections["9.2"]
    assert Decimal(right["amount"]) == Decimal("20")
    assert (right["rows"], right["rows_priced"], right["rows_not_finite"]) == (3, 1, 1)
    assert right["depth"] == 1


def test_a_section_with_no_money_and_no_rows_anywhere_is_absent(client, unallocated_tree):
    """Граница §5.2. Фикстура содержит раздел «8» без позиций во всём поддереве —
    его в списке быть не должно: разносить там нечего, и в счётчике `chapters` его
    тоже нет."""
    body = client.get(f"/api/v1/analytics/project-passport/{unallocated_tree.contract_id}").json()
    assert "8" not in {s["number"] for s in body["unallocated"]["sections"]}


def test_category_options_carry_the_whole_classifier(client, unallocated_tree):
    """`categories` — НЕ справочник: `build_tree` прячет вложенные узлы без строк.
    Разносить же надо в том числе в статьи, которых в смете ещё нет, поэтому
    выбор берёт варианты из отдельного поля."""
    body = client.get(f"/api/v1/analytics/project-passport/{unallocated_tree.contract_id}").json()
    options = body["category_options"]

    total = client.get("/api/v1/references/work-categories").json()  # либо прямой COUNT по таблице
    assert len(options) == len(total), "варианты обязаны покрывать весь справочник"

    visible = {c["code"] for c in body["categories"]}
    assert {o["code"] for o in options} - visible, (
        "в справочнике обязаны быть статьи, которых нет в видимом дереве паспорта, — "
        "иначе тест не отличает category_options от categories"
    )
    assert all({"id", "code", "title", "is_bucket"} <= set(o) for o in options)


def test_manual_assignment_of_a_category_absent_from_the_visible_tree(
    client, db_session, unallocated_tree, top_unassigned_chapter, admin_user
):
    """Ключевой случай пункта 1 ревью: аналитик выбирает статью, которой в смете
    ещё нет вовсе, — и она обязана появиться в дереве с деньгами."""
    body = client.get(f"/api/v1/analytics/project-passport/{unallocated_tree.contract_id}").json()
    visible = {c["code"] for c in body["categories"]}
    target = next(o for o in body["category_options"] if o["code"] not in visible)

    set_override(
        db_session,
        estimate_id=unallocated_tree.estimate_id,
        position_item_id=top_unassigned_chapter.id,
        work_category_id=target["id"],
        note=None,
        user_id=admin_user.id,
    )
    db_session.commit()

    after = client.get(f"/api/v1/analytics/project-passport/{unallocated_tree.contract_id}").json()
    node = next(c for c in after["categories"] if c["code"] == target["code"])
    assert Decimal(node["total"]) == Decimal("80")


def test_manual_assignments_carry_their_author(client, db_session, imported_estimate,
                                               top_unassigned_chapter, category_id, admin_user):
    set_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
        work_category_id=category_id,
        note="проверка",
        user_id=admin_user.id,
    )
    db_session.commit()

    body = client.get(f"/api/v1/analytics/project-passport/{imported_estimate.contract_id}").json()
    assignments = body["manual_assignments"]
    assert len(assignments) == 1
    assert assignments[0]["position_item_id"] == top_unassigned_chapter.id
    assert assignments[0]["assigned_by_email"] == admin_user.email
    assert assignments[0]["note"] == "проверка"
    assert assignments[0]["category_code"]
    # Разнесённый раздел ушёл из списка разносимого.
    assert top_unassigned_chapter.id not in {
        s["position_item_id"] for s in body["unallocated"]["sections"]
    }


def test_own_sections_declare_their_source(client, db_session, imported_estimate,
                                           top_unassigned_chapter, category_id, admin_user):
    set_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.commit()

    body = client.get(f"/api/v1/analytics/project-passport/{imported_estimate.contract_id}").json()
    sources = {
        section["source"]
        for category in body["categories"]
        for section in category["own_sections"]
    }
    assert "manual" in sources and "file" in sources
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_project_passport_api.py -k "unallocated_sections or manual_assignments or own_sections_declare or no_money_anywhere" -v`
Expected: FAIL — `KeyError: 'sections'`.

- [ ] **Step 3: Реализовать**

В `backend/crud/project_passport.py`:

1. `_own_sections_select` — добавить `PositionItem.category_source` в выборку и
   `"source": row.category_source` в `_own_sections_by_category`.

2. **Один расчёт метрик дерева разделов — `_section_metrics(db, estimate_id)`.**
   Возвращает `dict[int, SectionMetrics]` по **всем** строкам-разделам сметы:
   `parent_position_item_id`, `depth`, `number`, `title`, `smr_article_raw`,
   `work_category_id`, `own_amount`, `own_rows`, `own_rows_priced`,
   `own_rows_not_finite`, `subtree_amount`, `rows`, `rows_priced`,
   `rows_not_finite`.

   Родитель — `chapter_item_id` строки-раздела (у раздела он ведёт на раздел-предок),
   глубина — длина цепочки родителей. Свои деньги — сумма прямых позиций с **тем же
   фильтром годности, что в VIEW** (`NaN`/`±Infinity` исключаются из суммы и падают
   в `rows_not_finite`); `subtree_amount` — свёртка по дереву в Python, «только
   известные слагаемые», как `build_tree._build_node`, и **никогда** как «родитель
   минус дети»: у вершины без своих позиций разность дала бы `0` там, где верно
   `None`.

   Расчёт **один на два потребителя** — `_unallocated_sections` фильтрует его выход
   по `work_category_id IS NULL`, а `_manual_assignments` обогащает им свои строки.
   Два независимых расчёта тех же метрик разъехались бы, и это тот же класс дефекта,
   от которого уходит вся фича.

3. `_unallocated_sections(db, estimate_id)` = метрики, отфильтрованные по
   `work_category_id IS NULL`, минус узлы, у которых **ни у себя, ни ниже** нет
   строк (граница §5.2), с родителем, переподвешенным **внутрь выборки** (у корня
   нераспределённой части `parent_position_item_id = None`, `depth` пересчитан от
   этого корня — иначе экран получил бы отступ от структуры файла, а не от того,
   что показано).

4. `_manual_assignments(db, estimate_id)`: join `estimate_category_overrides` →
   `position_items` → `work_categories` → `users`, метрики — из того же
   `_section_metrics`; порядок — по `subtree_amount` убыв., затем по ключу позиции.

5. `_category_options(refs)` — плоский список `{id, code, title, is_bucket}` по
   **всем** прочитанным `WorkCategory`, отсортированный по `sort_order`. Справочник
   уже читается для `build_tree` (362 строки), второго запроса не нужно.

6. В `get_project_passport`: `"sections"` — в `unallocated_dict`,
   `"manual_assignments"` и `"category_options"` — в корень ответа. **Для договора
   без сметы** (ветка `estimate is None`) — `"sections": []`,
   `"manual_assignments": []`, а `"category_options"` — **полный** список: форма
   ответа обязана быть одинаковой, а справочник от наличия сметы не зависит.

Свёртку поддерева НЕ писать рекурсивным CTE в SQL: дерево крошечное, а второе
место, знающее закон свёртки, разъедется с первым.

- [ ] **Step 4: Прогнать полное покрытие паспорта**

Run: `cd backend && uv run pytest tests/integration/test_project_passport_api.py tests/unit/test_responses.py -v`
Expected: PASS, существующие тесты Ф6 не ослаблены.

- [ ] **Step 5: Доказать защиту снятием**

1. Вернуть в `_unallocated_sections` фильтр «только разделы с прямыми позициями» →
   `test_unallocated_tree_reports_exact_own_and_subtree_metrics` краснеет на разделе
   «9» (это ровно тот дефект, который нашёл макет).
2. Убрать `source` из `own_sections` → `test_own_sections_declare_their_source` краснеет.
3. Считать `subtree_amount` как «родитель минус дети» → тот же тест краснеет на
   `subtree_amount` вершины: вместо `80` выйдет `0`, а `own` вершины перестанет
   быть `None`.
4. Убрать фильтр годности из `own_amount` → краснеет `rows_not_finite` и сумма
   раздела «9.2» (в фикстуре есть строка с `NaN` именно для этого).
5. Отдать `category_options` из `passport.categories` →
   `test_category_options_carry_the_whole_classifier` краснеет на длине, а
   `test_manual_assignment_of_a_category_absent_from_the_visible_tree` — на
   `next(...)`, потому что выбрать отсутствующую статью станет невозможно.
6. Не переподвешивать родителя внутрь выборки → краснеет
   `top["parent_position_item_id"] is None`.

- [ ] **Step 6: Коммит**

```bash
git add backend/crud/project_passport.py backend/tests/integration/test_project_passport_api.py
git commit -m "feat(passport): дерево нераспределённого, manual_assignments, category_options

Дерево включает промежуточные разделы без своих позиций: без них разнос вершинами
недоступен, и аналитик получает 24 решения вместо 2. Метрики дерева считает ОДНА
функция на два потребителя; свёртка поддерева — в Python, как build_tree, а не
разностью «родитель минус дети».

category_options отдаёт справочник целиком: passport.categories прячет вложенные
узлы без строк, а разносить надо в том числе в статьи, которых в смете ещё нет."
```

---

### Task 5: Эндпоинты разноса

**Files:**
- Create: `backend/routers/category_overrides.py`
- Modify: `backend/main.py`
- Test: `backend/tests/integration/test_category_overrides_api.py`

**Interfaces:**
- Consumes: `set_override`, `clear_override`, `CategoryOverrideError`, `get_current_user`.
- Produces: `PUT`/`DELETE /api/v1/estimates/{estimate_id}/category-overrides/{position_item_id}`,
  ответ `{"chapters_updated": int, "additional_works_updated": int, "chapters_manual": int}`.

- [ ] **Step 1: Написать падающие тесты**

Создать `backend/tests/integration/test_category_overrides_api.py`:

```python
"""HTTP-слой разноса (спека разноса §2.7, §4.3)."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from models import EstimateCategoryOverride

pytestmark = pytest.mark.integration


def _url(estimate_id, position_item_id) -> str:
    return f"/api/v1/estimates/{estimate_id}/category-overrides/{position_item_id}"


def test_member_may_assign(member_client, imported_estimate, top_unassigned_chapter, category_id):
    r = member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id},
    )
    assert r.status_code == 200
    assert r.json()["chapters_updated"] > 1


def test_repeating_an_identical_put_is_a_no_op(
    member_client, db_session, imported_estimate, top_unassigned_chapter, category_id
):
    """Спека §2.2: аудит не переписывается одинаковым запросом."""
    first = member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id, "note": "раз"},
    )
    assert first.status_code == 200
    before = db_session.execute(
        sa.select(EstimateCategoryOverride.assigned_at, EstimateCategoryOverride.assigned_by).where(
            EstimateCategoryOverride.position_item_id == top_unassigned_chapter.id
        )
    ).one()

    again = member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id, "note": "раз"},
    )
    assert again.status_code == 200
    after = db_session.execute(
        sa.select(EstimateCategoryOverride.assigned_at, EstimateCategoryOverride.assigned_by).where(
            EstimateCategoryOverride.position_item_id == top_unassigned_chapter.id
        )
    ).one()
    assert after == before


def test_changing_the_article_moves_the_audit(
    member_client, db_session, imported_estimate, top_unassigned_chapter, category_id, other_category_id
):
    member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id},
    )
    # Время сдвигается НАЗАД заведомо далеко, а не читается как есть: два запроса
    # внутри одного теста укладываются в разрешение `now()`, и `>=` прошло бы даже
    # при неизменённом времени — то есть стерегло бы ровно ничего.
    db_session.execute(
        sa.text(
            "UPDATE estimate_category_overrides SET assigned_at = now() - interval '1 day' "
            "WHERE position_item_id = :rid"
        ),
        {"rid": top_unassigned_chapter.id},
    )
    db_session.commit()
    before = db_session.execute(
        sa.select(EstimateCategoryOverride.assigned_at).where(
            EstimateCategoryOverride.position_item_id == top_unassigned_chapter.id
        )
    ).scalar_one()

    member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": other_category_id},
    )
    after = db_session.execute(
        sa.select(
            EstimateCategoryOverride.assigned_at, EstimateCategoryOverride.work_category_id
        ).where(EstimateCategoryOverride.position_item_id == top_unassigned_chapter.id)
    ).one()
    assert after.work_category_id == other_category_id
    assert after.assigned_at > before


def test_a_missing_category_is_404(member_client, imported_estimate, top_unassigned_chapter):
    r = member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": 10**9},
    )
    assert r.status_code == 404


def test_deleting_a_decision_that_is_not_there_is_200(
    member_client, imported_estimate, top_unassigned_chapter
):
    """Снятие идемпотентно (см. сервисный тест): состояние уже такое, какого хотел
    вызывающий, и сообщать ему об ошибке не о чем."""
    r = member_client.delete(_url(imported_estimate.id, top_unassigned_chapter.id))
    assert r.status_code == 200


def test_delete_removes_the_decision(
    member_client, imported_estimate, top_unassigned_chapter, category_id
):
    member_client.put(
        _url(imported_estimate.id, top_unassigned_chapter.id),
        json={"work_category_id": category_id},
    )
    r = member_client.delete(_url(imported_estimate.id, top_unassigned_chapter.id))
    assert r.status_code == 200


def test_a_non_chapter_target_is_422(member_client, imported_estimate, any_position_row, category_id):
    r = member_client.put(
        _url(imported_estimate.id, any_position_row.id), json={"work_category_id": category_id}
    )
    assert r.status_code == 422


def test_a_missing_estimate_is_404(member_client, top_unassigned_chapter, category_id):
    r = member_client.put(_url(10**9, top_unassigned_chapter.id), json={"work_category_id": category_id})
    assert r.status_code == 404


def test_a_disabled_structure_is_409(
    member_client, estimate_with_broken_numbering, broken_chapter, category_id
):
    r = member_client.put(
        _url(estimate_with_broken_numbering.id, broken_chapter.id),
        json={"work_category_id": category_id},
    )
    assert r.status_code == 409
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_category_overrides_api.py -v`
Expected: FAIL — 404 на все маршруты (роутера нет).

- [ ] **Step 3: Реализовать роутер**

Создать `backend/routers/category_overrides.py`:

```python
"""Роутер ручного разноса разделов сметы по статьям (спека разноса §2.7).

Права: `member` тоже вправе — решение ограничено одной сметой и обратимо, тогда
как ручной матчинг правит ОБЩИЙ каталог и тоже открыт `member`. Разрушительное
действие (`replace`) закрыто на `admin` в своём роутере. Поэтому `require_admin`
здесь нет, только аутентификация (навешена в main.py).

Транзакцию ведёт роутер: сервис только пишет и пересчитывает.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import User
from services.category_override import CategoryOverrideError, clear_override, set_override

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/estimates", tags=["category-overrides"])

#: Код ошибки сервиса → HTTP. `not_found` покрывает и гонку с заменой сметы:
#: после ожидания блокировки строки просто нет, и отличить это от изначально
#: отсутствовавшей сметы нельзя (спека §2.5). `409` принадлежит ТОЛЬКО
#: `structure_disabled` — так сказано в спеке §2.7.
#:
#: `mapping_broken` в карте ОТСУТСТВУЕТ намеренно: расхождение разобранной копии
#: файла со строками сметы — нарушение целостности НАШИХ данных, а не конфликт
#: пользовательского действия. Пользователь ничего не может с ним сделать, и
#: `409` предложил бы ему повторить попытку, которая обречена. Такая ошибка
#: должна дойти до `500` и до логов — этим и занимается `_apply`.
_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "not_a_chapter": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "structure_disabled": status.HTTP_409_CONFLICT,
}


class OverrideRequest(BaseModel):
    work_category_id: int
    note: str | None = Field(default=None, max_length=2000)


def _apply(db: Session, action, **kwargs):
    try:
        result = action(db, **kwargs)
        db.commit()
    except CategoryOverrideError as exc:
        db.rollback()
        http_status = _STATUS.get(exc.code)
        if http_status is None:
            # `mapping_broken` — не пользовательский конфликт, а нарушение
            # целостности наших данных: логируем и отдаём 500, потому что повторять
            # такой запрос бессмысленно, а тишина скрыла бы поломку.
            log.error("Разнос статей: %s", exc, exc_info=True)
            raise
        raise HTTPException(http_status, str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return {
        "chapters_updated": result.chapters_updated,
        "additional_works_updated": result.additional_works_updated,
        "chapters_manual": result.chapters_manual,
    }


@router.put("/{estimate_id}/category-overrides/{position_item_id}")
def put_override(
    estimate_id: int,
    position_item_id: int,
    body: OverrideRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Назначить статью строке-разделу и пересчитать смету целиком.

    Повторный запрос с теми же статьёй и примечанием — no-op: аудит не двигается
    (спека §2.2).
    """
    return _apply(
        db,
        set_override,
        estimate_id=estimate_id,
        position_item_id=position_item_id,
        work_category_id=body.work_category_id,
        note=body.note,
        user_id=current_user.id,
    )


@router.delete("/{estimate_id}/category-overrides/{position_item_id}")
def delete_override(
    estimate_id: int,
    position_item_id: int,
    db: Session = Depends(get_db),
):
    """Снять решение и пересчитать смету целиком: разделы, ставшие
    нераспределёнными, получают `NULL` (спека §2.3)."""
    return _apply(
        db, clear_override, estimate_id=estimate_id, position_item_id=position_item_id
    )
```

В `backend/main.py` — рядом с остальными:

```python
app.include_router(category_overrides_router.router, dependencies=_auth_dep)
```

- [ ] **Step 4: Прогнать**

Run: `cd backend && uv run pytest tests/integration/test_category_overrides_api.py -v`
Expected: PASS (7 тестов).

- [ ] **Step 5: Доказать защиту снятием**

1. В `set_override` всегда обновлять `assigned_by`/`assigned_at` →
   `test_repeating_an_identical_put_is_a_no_op` краснеет.
2. Отправить `structure_disabled` в `_STATUS` как 404 →
   `test_a_disabled_structure_is_409` краснеет.
3. Убрать `dependencies=_auth_dep` при регистрации → тест на анонимный доступ
   (дописать: анонимный клиент получает 401) краснеет.

- [ ] **Step 6: Коммит**

```bash
git add backend/routers/category_overrides.py backend/main.py backend/tests/integration/test_category_overrides_api.py
git commit -m "feat(api): PUT/DELETE разноса раздела по статье

Право member. Гонка с заменой сметы даёт 404 — после ожидания блокировки строки
просто нет, и отличить это от изначально отсутствовавшей сметы нельзя; 409
остался только за structure_disabled."
```

---

### Task 6: Громкость замены сметы

**Files:**
- Modify: `backend/services/estimate_import.py` (`_replace_existing`, около строки 571)
- Modify: `backend/crud/contracts.py` (`_estimates_of`, строка 161)
- Modify: `frontend/src/components/contracts/EstimateUploadPanel.tsx`
- Modify: `frontend/src/types/domain.ts` (поле в строке списка смет договора)
- Test: `backend/tests/integration/test_estimate_import.py`
- Test: `backend/tests/integration/` — тест карточки договора на новое поле
- Test: `frontend/src/components/contracts/EstimateUploadPanel.test.tsx`

**Interfaces:**
- Consumes: `EstimateCategoryOverride`, существующий механизм `warnings` сессии B.
- Produces: warning нового `import_job` при непустом наборе утраченных решений.

- [ ] **Step 1: Написать падающий тест**

Дописать в `backend/tests/integration/test_estimate_import.py`:

```python
def test_replace_reports_the_manual_decisions_it_destroys(
    db_session, imported_estimate, top_unassigned_chapter, category_id, admin_user, replace_upload
):
    """Спека §2.9: решения сгорают каскадом, поэтому об утрате надо СКАЗАТЬ."""
    set_override(
        db_session,
        estimate_id=imported_estimate.id,
        position_item_id=top_unassigned_chapter.id,
        work_category_id=category_id,
        note=None,
        user_id=admin_user.id,
    )
    db_session.commit()

    job = replace_upload(imported_estimate.contract_id)
    # Проверяется ТОЧНАЯ фраза, а не вхождение «1»: в тексте есть дата, и любая
    # цифра из неё прошла бы проверку при неверном счётчике.
    assert any("Утрачено 1 ручных решений о статьях" in w for w in job.warnings)


def test_replace_says_nothing_when_there_were_no_decisions(
    db_session, imported_estimate, replace_upload
):
    """Негативная половина: без решений предупреждения быть не должно, иначе оно
    ничего не значит."""
    job = replace_upload(imported_estimate.contract_id)
    assert not any("ручных решений" in w for w in job.warnings)
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `cd backend && uv run pytest tests/integration/test_estimate_import.py -k replace_reports -v`
Expected: FAIL — предупреждения нет.

- [ ] **Step 3: Реализовать**

В `_replace_existing`, **до** `db.execute(delete(Estimate)...)`:

```python
    # Решения о статьях уходят каскадом вместе со сметой (спека разноса §1.8), и
    # поэтому об их утрате надо сказать: иначе аналитик потеряет работу молча и
    # узнает об этом по вернувшемуся «Нераспределённому». Предупреждение пишет
    # сессия B — оно описывает ДОМЕННОЕ состояние и не должно существовать, если
    # домен откатился (AGENTS.md §5). В `warnings` попадает число и дата, а не
    # перечень: истории решений проект не ведёт (спека разноса §3.3).
    lost = db.execute(
        sa.select(
            sa.func.count(),
            sa.func.max(EstimateCategoryOverride.assigned_at),
        )
        .select_from(EstimateCategoryOverride)
        .join(PositionItem, PositionItem.id == EstimateCategoryOverride.position_item_id)
        .join(Proposal, Proposal.id == PositionItem.proposal_id)
        .join(Lot, Lot.id == Proposal.lot_id)
        .where(Lot.estimate_id == old_id)
    ).one()
    if lost[0]:
        warnings.append(
            f"Утрачено {lost[0]} ручных решений о статьях, сделанных до "
            f"{lost[1]:%d.%m.%Y}: они относились к заменённой смете и удалены вместе "
            "с ней. Разнос «Нераспределённого» по новой смете нужно сделать заново."
        )
```

- [ ] **Step 4: Прогнать**

Run: `cd backend && uv run pytest tests/integration/test_estimate_import.py -v`
Expected: PASS, существующие тесты замены не ослаблены.

- [ ] **Step 5: Доказать защиту снятием**

Убрать `if lost[0]:` (писать всегда) → `test_replace_says_nothing_when_there_were_no_decisions`
краснеет. Убрать блок целиком → `test_replace_reports_...` краснеет.

- [ ] **Step 6: Счётчик решений — в строку списка смет договора**

Форма замены **не может** взять число из паспорта, и на это две независимые
причины. Первая: карточка договора паспорт не загружает вовсе — она держит
`useContract`, `useContractImportJobs`, `useObject`
([ContractCardPage.tsx](../../../frontend/src/pages/contracts/ContractCardPage.tsx#L45)).
Вторая, важнее: паспорт **всегда** описывает исходную смету (`amendment_no IS NULL`,
правило Ф6), а форма заменяет **любое** допсоглашение — то есть число из паспорта
относилось бы к другой паре `(contract_id, amendment_no)` и врало бы тем убедительнее,
чем больше у договора допсоглашений.

Источник — список смет, который карточка уже загружает
([`_estimates_of`](../../../backend/crud/contracts.py#L161)). Тест сначала:

```python
def test_each_estimate_row_carries_its_own_decision_count(
    client, db_session, contract_with_amendment, chapter_of_source, chapter_of_amendment,
    category_id, admin_user
):
    """Счётчик обязан быть ПОСМЕТНЫМ: паспорт описывает только исходную смету, а
    заменять можно любое допсоглашение."""
    set_override(
        db_session, estimate_id=contract_with_amendment.source_estimate_id,
        position_item_id=chapter_of_source.id, work_category_id=category_id,
        note=None, user_id=admin_user.id,
    )
    db_session.commit()

    rows = client.get(f"/api/v1/contracts/{contract_with_amendment.id}").json()["estimates"]
    by_amendment = {r["amendment_no"]: r for r in rows}
    assert by_amendment[None]["category_overrides_count"] == 1
    assert by_amendment[1]["category_overrides_count"] == 0
```

Реализация — подзапрос в `_estimates_of`: `count` по
`estimate_category_overrides` через `position_items → proposals → lots` с
`lots.estimate_id = estimates.id`.

Прогнать: `cd backend && uv run pytest tests/integration -k decision_count -v` — PASS.
Снятие: считать по договору, а не по смете → тест краснеет на строке допсоглашения.

- [ ] **Step 7: Предупредить в форме замены — до загрузки**

Warning в `import_jobs` приходит **после** того, как решения уже уничтожены; как
предупреждение он бесполезен, и спека §2.9 п. 2 требует сказать **до**. Тест сначала:

```typescript
it("форма замены предупреждает об утрате разноса заменяемой сметы", async () => {
  // Счётчик берётся по amendment_no ТОЙ пары, которую заменяют.
  renderUploadPanel({
    estimates: [
      { amendment_no: null, category_overrides_count: 2 },
      { amendment_no: 1, category_overrides_count: 0 },
    ],
    replacing: { amendment_no: null },
  });
  expect(screen.getByRole("alert")).toHaveTextContent(/ручной разнос/i);
  expect(screen.getByRole("alert")).toHaveTextContent("2");
});

it("замена допсоглашения без решений молчит, даже если у исходной сметы они есть", async () => {
  // Ровно тот случай, который сломал бы источник «из паспорта»: у исходной сметы
  // решения есть, но заменяют не её.
  renderUploadPanel({
    estimates: [
      { amendment_no: null, category_overrides_count: 2 },
      { amendment_no: 1, category_overrides_count: 0 },
    ],
    replacing: { amendment_no: 1 },
  });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
```

Прогнать: `cd frontend && npx vitest run src/components/contracts` — Expected: PASS.

Снятие: (1) убрать условие «только при непустом счётчике» → второй тест краснеет;
(2) брать счётчик исходной сметы вместо заменяемой → второй тест краснеет — это и
есть защита от того дефекта, который нашло ревью.

- [ ] **Step 8: Коммит**

```bash
git add backend/services/estimate_import.py backend/crud/contracts.py backend/tests/integration frontend/src/components/contracts frontend/src/types/domain.ts
git commit -m "feat(import): замена сметы сообщает об утраченных ручных решениях

Число и дата, не перечень: истории решений проект не ведёт. Пишет сессия B —
предупреждение описывает доменное состояние.

Форма замены предупреждает ДО загрузки, и счётчик для неё посметный
(category_overrides_count в строке estimates[]): паспорт описывает только исходную
смету, а заменять можно любое допсоглашение — число из паспорта относилось бы к
другой паре."
```

---

### Task 7: Типы, API-клиент и мутации фронта

**Files:**
- Modify: `frontend/src/types/domain.ts`, `frontend/src/services/api/analytics.ts`,
  `frontend/src/services/queries.ts`, `frontend/src/test/fixtures.ts`,
  `frontend/src/test/handlers.ts`
- Test: `frontend/src/services/queries.test.tsx` (файл уже существует — **дописать**)

**Interfaces:**
- Produces:
  - `ProjectPassportUnallocatedSection`, `ProjectPassportManualAssignment`,
    `ProjectPassportCategoryOption`,
    `ProjectPassportSection.source: "file" | "manual"`,
    `ProjectPassportUnallocated.sections`, `ProjectPassport.manual_assignments`,
    `ProjectPassport.category_options`
  - `analyticsApi.setCategoryOverride({estimateId, positionItemId, workCategoryId, note})`
  - `analyticsApi.clearCategoryOverride({estimateId, positionItemId})`
  - `useSetCategoryOverride()`, `useClearCategoryOverride()` — обе инвалидируют
    `qk.passport.project(contractId)`

- [ ] **Step 1: Написать падающий тест мутации**

```typescript
it("после назначения статьи паспорт перезапрашивается", async () => {
  // MSW: PUT отвечает 200, GET паспорта на втором вызове отдаёт пустое
  // «Нераспределённое». Тест доказывает ИНВАЛИДАЦИЮ, а не сам вызов PUT:
  // без неё экран остался бы с прежними числами.
  const { result } = renderHookWithClient(() => useSetCategoryOverride());
  await act(() => result.current.mutateAsync({
    estimateId: 11, positionItemId: 42, workCategoryId: 20, contractId: 5,
  }));
  expect(queryClient.getQueryState(qk.passport.project(5))?.isInvalidated).toBe(true);
});
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `cd frontend && npx vitest run src/services/queries.test.ts`
Expected: FAIL — `useSetCategoryOverride is not a function`.

- [ ] **Step 3: Реализовать типы**

В `frontend/src/types/domain.ts`:

```typescript
/** Раздел сметы без статьи — узел ДЕРЕВА разносимого (спека разноса §2.6). */
export interface ProjectPassportUnallocatedSection {
  position_item_id: number;
  /** `null` — узел верхнего уровня внутри нераспределённой части. */
  parent_position_item_id: number | null;
  number: string | null;
  title: string;
  depth: number;
  /** Свои прямые позиции; `null` — их нет вовсе (не «ноль»). */
  amount: Decimal | null;
  /** Итог поддерева — именно он показывает цену решения на этой вершине. */
  subtree_amount: Decimal | null;
  rows: number;
  rows_priced: number;
  rows_not_finite: number;
  /** Что стояло в клетке «Статья СМР»; `null` — файл молчал. Различие причин
   *  выражается ровно этим полем, отдельного `reason` нет намеренно. */
  smr_article_raw: string | null;
}

/** Действующее ручное решение о статье раздела. */
export interface ProjectPassportManualAssignment
  extends ProjectPassportUnallocatedSection {
  work_category_id: number;
  category_code: string;
  category_title: string;
  assigned_by_email: string;
  assigned_at: string;
  note: string | null;
}
```

```typescript
/** Вариант выбора статьи. Источник — `category_options`, НЕ `categories`:
 *  последний прячет вложенные узлы без строк, а разносить надо в том числе в
 *  статьи, которых в смете ещё нет. */
export interface ProjectPassportCategoryOption {
  id: number;
  code: string;
  title: string;
  is_bucket: boolean;
}
```

`ProjectPassportSection` получает `source: "file" | "manual"`,
`ProjectPassportUnallocated` — `sections: ProjectPassportUnallocatedSection[]`,
`ProjectPassport` — `manual_assignments: ProjectPassportManualAssignment[]` и
`category_options: ProjectPassportCategoryOption[]`.

Обновить `frontend/src/test/fixtures.ts` и `handlers.ts`: новые поля обязательны,
поэтому `tsc` укажет все места, где фикстуры их не несут.

- [ ] **Step 4: Реализовать клиент и мутации**

Мутации принимают `contractId` **отдельным полем**, хотя эндпоинт его не требует:
инвалидировать нужно запрос паспорта, а он ключуется договором.

```typescript
export function useSetCategoryOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SetCategoryOverrideInput) => analyticsApi.setCategoryOverride(input),
    onSuccess: (_data, input) => {
      qc.invalidateQueries({ queryKey: qk.passport.project(input.contractId) });
    },
    onError: toastApiError,
  });
}
```

- [ ] **Step 5: Прогнать**

```bash
cd frontend && npx tsc --noEmit
npx vitest run src/services/queries.test.tsx
```
Expected: PASS. Шаги **по отдельности**, не через `&&` (инсайт
[silent-test-runs](../../insights/silent-test-runs.md): `&&` прячет, какой из шагов
не взлетел).

- [ ] **Step 6: Коммит**

```bash
git add frontend/src/types/domain.ts frontend/src/services frontend/src/test
git commit -m "feat(frontend): типы дерева нераспределённого и мутации разноса

Мутации берут contractId отдельным полем: эндпоинт его не требует, а
инвалидировать нужно запрос паспорта, который ключуется договором."
```

---

### Task 8: Панель-верстак

**Files:**
- Create: `frontend/src/pages/passport/UnallocatedPanel.tsx`
- Create: `frontend/src/pages/passport/UnallocatedPanel.test.tsx`
- Modify: `frontend/src/pages/passport/CategoryTable.tsx`

**Interfaces:**
- Consumes: `ProjectPassportUnallocatedSection`, `ProjectPassportManualAssignment`,
  **`ProjectPassportCategoryOption` как единственный источник вариантов выбора**,
  `useSetCategoryOverride`, `useClearCategoryOverride`.
- Produces: `<UnallocatedPanel passport={...} contractId={...} estimateId={...} />`,
  `data-testid="unallocated-panel"`, `data-print="hide"`.

- [ ] **Step 1: Переиспользовать уже установленные примитивы**

`command.tsx` и `popover.tsx` **уже есть** в `frontend/src/components/ui/` вместе со
своими зависимостями — импортировать их. `npx shadcn add command popover` **не
запускать**: CLI перезапишет локальные компоненты, а вместе с ними и правки, если
они там есть.

Проверить перед началом: `ls frontend/src/components/ui/ | grep -E "command|popover"`
— должны быть оба файла. Если чего-то нет, тогда и только тогда добавить его
через `npx shadcn add <имя>`.

- [ ] **Step 2: Написать падающие тесты**

Создать `frontend/src/pages/passport/UnallocatedPanel.test.tsx`. Покрыть: разворот
по шеврону, дерево с отступом по `depth` и суммой поддерева, поиск статьи по коду и
по названию, вызов мутации с верными аргументами, список разнесённого с автором и
кнопкой «снять», отсутствие панели в печатном потоке (`data-print="hide"`), зажим
длинного наименования (`line-clamp-2` и **отсутствие** класса `block` рядом с ним).

```typescript
it("вершина показывает сумму поддерева, а не свои деньги", async () => {
  render(<UnallocatedPanel passport={passportWithTree} contractId={5} estimateId={11} />);
  const top = screen.getByTestId("unallocated-section-42");
  // У вершины своих позиций нет: `amount === null`, а решение стоит суммы поддерева.
  expect(within(top).getByTestId("subtree-amount-42")).toHaveTextContent("85 087 749,27 ₽");
  expect(within(top).queryByTestId("own-amount-42")).not.toBeInTheDocument();
});

it("в выборе статьи есть статьи, которых нет в видимом дереве паспорта", async () => {
  // Источник вариантов — category_options, а не categories: последний прячет
  // вложенные узлы без строк, и половина справочника до аналитика не дошла бы.
  render(<UnallocatedPanel passport={passportWithTree} contractId={5} estimateId={11} />);
  await userEvent.click(screen.getByTestId("pick-category-42"));
  const visible = new Set(passportWithTree.categories.map((c) => c.code));
  const hidden = passportWithTree.category_options.filter((o) => !visible.has(o.code));
  expect(hidden.length).toBeGreaterThan(0);
  expect(screen.getByText(hidden[0].title)).toBeInTheDocument();
});

it("поиск находит статью и по коду, и по названию", async () => {
  render(<UnallocatedPanel passport={passportWithTree} contractId={5} estimateId={11} />);
  await userEvent.click(screen.getByTestId("pick-category-42"));
  await userEvent.type(screen.getByRole("combobox"), "20");
  expect(screen.getByText(/MR - SHELL/)).toBeInTheDocument();
});

it("длинное наименование зажато и не отменяет line-clamp классом block", () => {
  render(<UnallocatedPanel passport={passportWithLongTitle} contractId={5} estimateId={11} />);
  const title = screen.getByTestId("unallocated-section-title-42");
  expect(title.className).toContain("line-clamp-2");
  expect(title.className.split(/\s+/)).not.toContain("block");
});
```

Суммы в тестах фронта — **синтетические** (фикстуры), не из реальной сметы.

- [ ] **Step 3: Прогнать и убедиться, что падает**

Run: `cd frontend && npx vitest run src/pages/passport/UnallocatedPanel.test.tsx`
Expected: FAIL — модуля нет.

- [ ] **Step 4: Реализовать панель и разворот**

`UnallocatedPanel.tsx` — своя сетка (дерево / сумма / действие), два блока:
«разделы без статьи» (по убыванию `subtree_amount`, отступ по `depth`) и
«разнесено вручную» (статья, автор, дата, примечание, «снять»). Выбор статьи —
`Command` внутри `Popover` **по `passport.category_options`**, поиск по коду и
названию: в справочнике 362 строки, и без поиска он неюзабелен.

- [ ] **Step 4a: Доказать защиту снятием**

Подменить источник вариантов на `passport.categories` → тест «в выборе статьи есть
статьи, которых нет в видимом дереве» краснеет. Это ровно тот дефект, который нашло
ревью плана.

В `CategoryTable.tsx` строка `row-unallocated` получает `ExpandToggle` (компонент
уже есть и уже помечен `data-print="hide"`), а под ней — строка с `colSpan` на всю
ширину, куда монтируется панель.

- [ ] **Step 5: Прогнать**

```bash
cd frontend && npx vitest run src/pages/passport && npx tsc --noEmit && npx eslint src
```
Expected: PASS, существующие тесты паспорта не ослаблены.

- [ ] **Step 6: Коммит**

```bash
git add frontend/src/pages/passport frontend/src/components/ui
git commit -m "feat(passport): панель-верстак разноса под строкой «Нераспределённое»

Раскладка выбрана макетом: в пяти колонках таблицы селектор влезает только на
место «Доля» и «₽/м²», а автору, дате и кнопке «снять» места нет вовсе."
```

---

### Task 9: Пометка `manual`, нулевое состояние, печатная сноска

**Files:**
- Modify: `frontend/src/pages/passport/CategoryTable.tsx`
- Test: `frontend/src/pages/passport/ProjectPassportPage.test.tsx`

**Interfaces:**
- Consumes: `own_sections[].source`, `manual_assignments`, существующий
  `unallocatedCaption`.
- Produces: бейдж «вручную» у разделов с `source === "manual"`, печатная сноска
  `data-testid="manual-footnote"` (печатается — **без** `data-print="hide"`),
  нулевое состояние строки без предупреждающего цвета.

- [ ] **Step 1: Написать падающие тесты**

```typescript
it("разнесённый вручную раздел помечен в дереве статей", async () => {
  // Пометка ПЕЧАТАЕТСЯ: паспорт идёт в банк, и он не должен выдавать наше
  // решение за содержимое файла.
  renderPassport(passportWithManualSection);
  const section = await screen.findByTestId("own-section-42");
  expect(within(section).getByText("вручную")).toBeInTheDocument();
  expect(section.getAttribute("data-print")).not.toBe("hide");
});

it("сноска называет число решений и не называет сумму", async () => {
  renderPassport(passportWithTwoManualAssignments);
  const note = await screen.findByTestId("manual-footnote");
  expect(note).toHaveTextContent("2");
  // Общей суммы в сноске быть не должно (спека §2.10): при вложенных решениях
  // subtree_amount задваивается, а подсчёт по эффективному 'manual' потерял бы
  // допработы, у которых category_source нет вовсе.
  expect(note.textContent).not.toMatch(/₽/);
});

it("сноски нет вовсе, когда ручных решений нет", async () => {
  renderPassport(passportWithoutManual);
  expect(screen.queryByTestId("manual-footnote")).not.toBeInTheDocument();
});

it("нулевое «Нераспределённое» выглядит как достигнутая цель", async () => {
  renderPassport(passportFullyAllocated);
  const row = await screen.findByTestId("row-unallocated");
  expect(row.className).not.toContain("warning");
  expect(screen.getByTestId("unallocated-caption")).toHaveTextContent(
    "все разделы сметы отнесены к статьям",
  );
});

it("неразносимый остаток сохраняет предупреждающий вид", async () => {
  // Граница §5.5: позиции вне структуры разносу недоступны, и подпись обязана
  // называть ИМЕННО эту причину — иначе ноль обещался бы там, где недостижим.
  renderPassport(passportWithRowsOutsideStructure);
  const row = await screen.findByTestId("row-unallocated");
  expect(row.className).toContain("warning");
  expect(screen.getByTestId("unallocated-caption")).toHaveTextContent("вне структуры");
});

it("нераспределённые допработы тоже держат остаток непустым", async () => {
  /*
    Третий случай границы §5.5, и он НЕ виден ни в `sections`, ни в
    `rows_outside_structure`: строка допработ с неразрешимой ссылкой («нет
    кандидатов» либо «статьи различаются») остаётся в `unallocated.extras`. При
    sections=[] и rows_outside_structure=0 экран объявил бы «всё разнесено», имея
    непустое «Нераспределённое» на экране рядом. Органов разноса рядом с extras
    быть не должно — их статья приезжает из раздела, на который они ссылаются.
  */
  renderPassport(passportWithUnresolvableExtras); // sections: [], rows_outside_structure: 0
  const row = await screen.findByTestId("row-unallocated");
  const caption = screen.getByTestId("unallocated-caption");

  expect(row.className).toContain("warning");
  // Подпись обязана назвать ДЕЙСТВУЮЩУЮ причину (спека §2.8). Проверять только
  // отсутствие «всё разнесено» недостаточно: `unallocatedCaption` строит базу из
  // `chapters`, и при разнесённых разделах она даёт «0 разделов сметы без статьи
  // классификатора» — подпись называет причину, которой нет, вместо той, которая есть.
  expect(caption).toHaveTextContent(/допработ/i);
  expect(caption).toHaveTextContent("2"); // столько строк в фикстуре
  expect(caption).not.toHaveTextContent("0 разделов");
  expect(screen.queryByTestId(/^pick-category-/)).not.toBeInTheDocument();
});

it("подпись не поминает допработы, когда их нет", async () => {
  // Негативная половина: иначе ветка о допработах ничего не значит.
  renderPassport(passportWithRowsOutsideStructure); // extras: []
  expect(screen.getByTestId("unallocated-caption")).not.toHaveTextContent(/допработ/i);
});
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `cd frontend && npx vitest run src/pages/passport/ProjectPassportPage.test.tsx`
Expected: FAIL на всех пяти.

- [ ] **Step 3: Реализовать**

Бейдж «вручную» в развороте статьи; сноска под таблицей (только при непустом
`manual_assignments`, число — `manual_assignments.length`, без суммы).

Нулевое состояние — снять безусловный `text-warning-text`. Предикат «всё
разнесено» — **три** слагаемых, и все три обязательны:

```
allocated = sections.length === 0
         && rows_outside_structure === 0
         && extras.length === 0
```

Третье слагаемое закрывает случай, невидимый в первых двух: строка допработ с
неразрешимой ссылкой живёт в `unallocated.extras` и разносу недоступна (граница
§5.5). Без него экран объявлял бы «всё разнесено», имея непустое
«Нераспределённое» в той же строке.

Причина остатка называется существующим `unallocatedCaption` — второго текста о том
же не заводить. Но **его надо расширить третьей причиной**: сегодня он строит базу
из `chapters` и дописывает `rows_outside_structure`
([CategoryTable.tsx](../../../frontend/src/pages/passport/CategoryTable.tsx#L125)),
а про допработы молчит. При разнесённых разделах `chapters` равен нулю, и подпись
выдала бы «0 разделов сметы без статьи классификатора» — то есть назвала бы
отсутствующую причину вместо действующей.

Правило расширения: базу строить из **первой непустой** причины, а не всегда из
`chapters`; каждую следующую дописывать через «отдельно — …», как уже сделано для
позиций вне структуры. Текст третьей причины — про строки допработ без разрешимой
статьи, с количеством (`unallocated.extras.length`). Формулировка обязана говорить,
что разносу они недоступны и почему: их статья приезжает из раздела, на который они
ссылаются (граница §5.5, третий случай).

Ноль ни одной из трёх причин не печатается вовсе — это и есть нулевое состояние.

- [ ] **Step 4: Прогнать**

```bash
cd frontend && npx vitest run src/pages/passport && npx tsc --noEmit
```
Expected: PASS.

- [ ] **Step 5: Доказать защиту снятием**

1. Добавить сумму в сноску → `test "сноска ... не называет сумму"` краснеет.
2. Рисовать сноску всегда → `test "сноски нет вовсе"` краснеет.
3. Вернуть безусловный `text-warning-text` → нулевое состояние краснеет.
4. Убрать из предиката `rows_outside_structure` → «неразносимый остаток» краснеет.
5. Убрать из предиката `extras.length` → «нераспределённые допработы» краснеет.
   Слагаемые снимаются **по одному**: вход каждого теста нарушает ровно одно из
   трёх условий, поэтому снятие одного слагаемого не маскируется двумя другими.
6. Убрать ветку о допработах из `unallocatedCaption` → тот же тест краснеет на
   `/допработ/i`, но **другим** утверждением: предикат остатка и текст подписи —
   две разные защиты, и снятие каждой обязано краснеть отдельно. Проверить, что
   при снятой ветке подписи `row.className` всё ещё содержит `warning` — иначе
   тест не различал бы эти две защиты.
7. Строить базу подписи всегда из `chapters` (как сегодня) → краснеет
   `not.toHaveTextContent("0 разделов")`.
8. Печатать ветку о допработах безусловно → «подпись не поминает допработы, когда
   их нет» краснеет.

- [ ] **Step 6: Коммит**

```bash
git add frontend/src/pages/passport
git commit -m "feat(passport): пометка ручного разноса, печатная сноска, нулевое состояние

Сноска называет число решений и не называет сумму: вложенные решения задваивают
subtree_amount, а подсчёт по эффективному manual потерял бы допработы."
```

---

### Task 10: Замер в браузере, правки документов, devlog

**Files:**
- Modify: `AGENTS.md` (§3, §7.4, §10), `docs/phase7-frame.md`
- Create: `docs/devlog/2026-08-11-unallocated-override.md`

- [ ] **Step 1: Прогнать `just ci`**

```bash
just ci
```
Expected: все шаги зелёные (ruff, pytest, eslint, tsc, vitest) — **по отдельности**,
не одной командой с `&&` (инсайт [silent-test-runs](../../insights/silent-test-runs.md)).

- [ ] **Step 2: Замер печати в настоящем браузере**

На стенде `gca_dev`, паспорт договора с нераспределённым: открыть предпросмотр
печати и проверить, что панель и все органы управления **не** печатаются; пометка
«вручную» и сноска — печатаются; нет обрезки по правому краю и разрывов внутри
строк; шапка таблицы повторяется на каждом листе. Замерить при **максимальном**
составе. Числа замера записать в devlog.

- [ ] **Step 3: Замер конкуренции**

Два параллельных `PUT` на одну смету в двух сессиях; затем `PUT` против
`replace=true`. Убедиться, что второй ждёт, а не пересчитывает от устаревшего
набора, и что гонка с заменой даёт `404`. Записать в devlog.

- [ ] **Step 4: Правки документов**

По §7 спеки: `phase7-frame.md` — пункт 4 «вне скоупа v1» **оставить как есть** и
только дописать к нему отсылку на спеку разноса; строку в таблицу состава фич **не
добавлять** (фича стоит вне фазы, у неё нет номера `ФN`). `AGENTS.md` §3 — ручной
разнос в правах `member`; §7.4 — признак ручного разноса и печатная сноска; §10 DoD
— формулировка **без** глобального обещания нуля, с перечислением трёх границ §5.5
и отдельным пунктом про смету стенда.

Devlog обязан нести шапку в форме двух последних фич: **Ветка**, **Спека**, **План**,
**Фаза:** ни к одной — с причиной («DoD фазы 7 закрыт, а этот функционал был у неё
вне скоупа v1»).

- [ ] **Step 5: Devlog**

Создать `docs/devlog/2026-08-11-unallocated-override.md`: что сделано, замеры
(печать, конкуренция, ci), отступления от плана, найденные грабли. Обязательно —
**соответствие «требование спеки → тест» списком** (инсайт
[replaying-new-rules](../../insights/replaying-new-rules.md), слой 3): требование
без исполняющего теста либо получает тест, либо объявляется границей здесь же.
Отдельным разделом — какие защиты доказаны снятием и что краснело.

- [ ] **Step 6: Коммит и PR**

```bash
git add AGENTS.md docs/phase7-frame.md docs/devlog/2026-08-11-unallocated-override.md
git commit -m "docs(разнос): devlog, правки рамки фазы 7 и AGENTS.md"
git push -u origin feat/unallocated-override
```

PR со ссылками на спеку и план в описании.

---

## Самопроверка плана

**Покрытие спеки.** §1.3 → задачи 3, 4 (позиции не трогаются, проверяется тестом);
§1.4 → задача 3 (`_materialize_extras` + тест допработы); §1.5 → задача 4 (дерево,
тест на промежуточный узел); §1.6 → задачи 1, 3, 5 (`structure_disabled` на трёх
уровнях); §1.7 → задача 3 (биекция); §1.8 → задачи 2, 6 (каскад и громкость);
§1.9 → задача 2; §2.1 → задача 1 (четыре теста приоритета); §2.2 → задачи 2, 5
(схема + семантика повторного `PUT`); §2.3 → задача 3 (в т.ч. «пересчёт без решений
= импорт»); §2.4 → задача 1; §2.5 → задачи 3, 5, 10 (лок, `404`, замер);
§2.6 → задача 4; §2.7 → задача 5; §2.8 → задача 8; §2.9 → задача 6 + задача 9
(предупреждение формы замены — во фронтовой части задачи 6, вынесено в её тест);
§2.10 → задача 9 + замер задачи 10; §3 → нигде не реализуется (список
неделаемого); §4 → тесты внутри задач 1–9; §5 → задачи 4 (границы дерева) и 9
(§5.5 в подписи строки); §7 → задача 10.

**Заглушек нет:** каждый шаг несёт либо код, либо точную команду с ожидаемым
результатом. Три места сознательно описаны прозой, а не кодом, — реализация
`_unallocated_sections` (задача 4, шаг 3), раскладка панели (задача 8, шаг 4) и
разметка сноски (задача 9, шаг 3): в них поведение полностью задано тестами шага 1,
а форма кода не должна навязываться сверх этого.

**Согласованность имён:** `CATEGORY_SOURCE_MANUAL`, `chapters_manual`,
`resolve_proposal(positions, overrides)`, `ApplyResult(chapters_updated,
additional_works_updated, chapters_manual)`, `CategoryOverrideError.code`,
`assigned_by_email`, `subtree_amount`, `position_item_id` — одни и те же во всех
задачах, где встречаются. `EstimateCategoryOverride` — единственное имя модели.

**Поправки второго ревью (пять существенных, все проверены по коду):**

1. **Селектор статей** брал варианты из `passport.categories`, который прячет
   вложенные узлы без строк, — аналитик не увидел бы как раз те статьи, в которые и
   надо разносить. Введено `category_options` (задачи 4, 7, 8) с тестом на выбор
   статьи, отсутствующей в видимом дереве, и со снятием защиты.
2. **Предупреждение формы замены** опиралось на паспорт, которого карточка договора
   не загружает, и которое к тому же всегда описывает исходную смету, тогда как
   заменять можно любое допсоглашение. Введён посметный
   `category_overrides_count` в `estimates[]` (задача 6, шаги 6–7), с тестами
   отдельно на исходную смету и на допсоглашение.
3. **HTTP-контракт:** добавлена явная проверка существования статьи
   (`_require_category` → `404` вместо `500` от FK); `mapping_broken` убран из карты
   статусов — это нарушение целостности наших данных, а не конфликт действия, и
   доходит до `500` с логом; определена семантика `DELETE` отсутствующего решения —
   идемпотентный успех.
4. **Нулевое состояние** различалось по двум признакам из трёх: нераспределённые
   допработы с неразрешимой ссылкой не видны ни в `sections`, ни в
   `rows_outside_structure`. Предикат стал тройным, слагаемые снимаются по одному.
   **Хвост, найденный третьим проходом ревью:** тройного предиката мало — сам
   `unallocatedCaption` про допработы молчит и при разнесённых разделах выдал бы
   «0 разделов сметы без статьи», то есть назвал бы отсутствующую причину вместо
   действующей. Подпись расширена третьей причиной, база строится от первой
   непустой, и это отдельная защита со своим снятием: предикат остатка и текст
   подписи краснеют разными утверждениями.
5. **Дерево и метрики** проверялись только по форме. Введён один расчёт
   `_section_metrics` на два потребителя и тест с независимо заданными числами
   (30/30, 20, строка без цены, строка `NaN`), проверяющий точные `amount`,
   `subtree_amount`, `rows`, `rows_priced`, `rows_not_finite`, — теперь снятие
   свёртки действительно краснеет.

Механические: статья в тесте констрейнта (иначе нарушались бы два ограничения
разом), downgrade проверяет обе таблицы, двойной `apply` проверяет нулевые
счётчики, аудит сдвигается назад на сутки и сравнивается строгим `>`, warning
проверяется точной фразой, `queries.test.tsx`, `command`/`popover` уже установлены —
переиспользовать, «не писать своих UI-**примитивов**» вместо «компонентов».

**Две поправки первой самопроверки:**

1. Предупреждение формы замены (спека §2.9 п. 2) — фронтовое, а задача 6 была
   целиком бэкендовой, то есть требование спеки осталось бы без исполняющего
   теста. Задача 6 получила шаг 6 с двумя фронтовыми тестами и снятием; она
   осталась одной задачей, потому что оба обязательства — про одно событие.
   Довод, который стоит того, чтобы его записать: warning в `import_jobs`
   приходит, когда решения **уже** уничтожены, поэтому как предупреждение он
   бесполезен, и без формы обязательство §2.9 не выполнено.
2. В задаче 1 был искажён путь к файлу теста — исправлен на
   `backend/tests/unit/test_category_resolution.py`.
