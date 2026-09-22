# План: семьи и контексты

**Спека:** `docs/superpowers/specs/2026-09-22-catalog-families-design.md`
**Ветка:** `feat/catalog-families` — пять коммитов гейтов 1–2 поверх
`main` = `6637076`, ветка не пушилась.

Форма плана — `docs/superpowers/plans/TEMPLATE.md`, правила — `AGENTS.md` §9.1.
**Тел тестов и тел реализации в плане нет ни одного**; схема таблиц,
доказательства и обоснования не пересказываются, а адресуются в спеку —
«факт живёт в одном месте» (`AGENTS.md` §9.2).

## Global Constraints

- **Спека сильнее дизайна r6.** Где они расходятся — права спека; дизайн
  открывается только чтобы не воскресить снятое.
- **Опустошение контекста архивированием НЕ является** (спека §2.8, отмена
  дизайна r6 §3.1). Ни одна задача не заводит автоархивирования ни по какому
  условию; `archived_at` ставит только явная операция.
- **Каскад матчинга `AGENTS.md` §5 и его ключ не трогаются**: `matching_cache`,
  `NORM_VERSION`, `cache_key`, get-or-create `catalog_positions`, пять
  счётчиков `import_jobs`. Слияние в Review получает **один шаг перед**
  `DELETE` и ни одной правки в шагах 1, 2 и 4 (спека §2.8, §4).
- **`backend/parser/` не меняется ни одним символом** (спека §4).
- **`AGENTS.md` §4 и §6 не правятся**: новый блок схемы едет в
  `docs/reference/schema.md`, матрица и нормативы продолжают стоять на
  `POSITION` (спека §2.11, §4).
- **Внешней модели в фиче нет вовсе** — ни обращения, ни очереди, ни
  `family_suggestions`, ни маскирования (спека §2.13).
- **`work_variant` не заводится, смысл `TO_REVIEW` не переопределяется** —
  фича 3 (спека §4).
- **`semantic_state` несёт ровно три значения** `{SUGGESTED, CONFIRMED,
  NOT_APPLICABLE}`; `PENDING` и `ERROR` не объявляются ни значением, ни
  комментарием (спека §2.5).
- **Деньги — `Decimal`, никогда `float`** (`AGENTS.md` §3). Фича денег не
  считает вовсе, и ни одна её поверхность денежной величины не печатает.
- **Права:** всё, что фича добавляет, — под `admin` (спека §2.7, §2.10). Прав
  `member` на семьи и контексты нет; слияние в Review остаётся у `member` и
  роли не меняет.
- **Фикстуры в `tasks/` не коммитятся** (`AGENTS.md` §9.3, `tasks/` в
  `.gitignore`); всё, на что опирается тест, обязано лежать под гитом — см.
  решение плана 1.
- **`docs/reference/` под стражем:** любой `§N` в правках `schema.md` и
  `screens.md` квалифицируется (`AGENTS.md §N`), ссылки внутрь `docs/`
  поднимаются до `../…`, шапка «Когда читать:» остаётся ровно одна
  (`check_agents_index.py`, проверка 16).
- **Номер ревизии `AGENTS.md` в плане не называется** — по той же причине, по
  которой его не называет спека (страж не разрешает несуществующую `v6.N`).
  Номер присваивает коммит задачи 14.
- **Зелёного стража план не обещает ни на одной задаче до 14**
  (`docs/pitfalls/process.md`). Ожидание такое: ни спека, ни план новой `v6.N`
  не называют, поэтому красноты быть не должно, — но это ожидание, а не
  обещание, и проверяется оно прогоном, а не рассуждением.

## Структура файлов

```
backend/
  alembic/versions/2026_09_22_0017-semantic_contour.py  Create задача 1
  models.py                                             Edit   задача 1
  cli.py                                                Edit   задачи 7, 11
  services/semantic_events.py                           Create задача 2
  services/semantic_rules.py                            Create задача 3
  services/context_routing.py                           Create задача 4
  services/context_operations.py                        Create задача 6
  services/work_families.py                             Create задачи 7, 8
  services/import_pipeline.py                           Edit   задача 5
  services/review.py                                    Edit   задача 9
  services/category_override.py                         Edit   задача 10
  services/round_category_override.py                   Edit   задача 10
  services/catalog_backfill.py                          Create задача 11
  crud/semantic.py                                      Create задача 12
  routers/semantic.py                                   Create задача 12
  routers/review.py                                     Edit   задача 9
  main.py                                               Edit   задача 12
  seeds/work_families_initial.json                      Create задача 7
  scripts/check_agents_index.py                         Edit   задача 14
  tests/conftest.py                                     Edit   задача 1
  tests/data/semantic_reference_101.json                Create задача 3
  tests/data/manual_overrides_18.json                   Create задача 10
  tests/integration/test_semantic_schema.py             Create задача 1
  tests/integration/test_semantic_events.py             Create задача 2
  tests/unit/test_semantic_rules.py                     Create задача 3
  tests/integration/test_context_routing.py             Create задача 4
  tests/integration/test_context_membership.py          Create задача 4
  tests/integration/test_import_pipeline.py             Edit   задача 5
  tests/integration/test_context_operations.py          Create задача 6
  tests/integration/test_context_concurrency.py         Create задача 6
  tests/integration/test_work_families.py               Create задачи 7, 8
  tests/integration/test_semantic_state.py              Create задача 8
  tests/integration/test_review_contexts.py             Create задача 9
  tests/integration/test_context_cascade.py             Create задача 10
  tests/integration/test_catalog_backfill.py            Create задача 11
  tests/integration/test_semantic_api.py                Create задача 12
frontend/src/
  types/domain.ts                                       Edit   задачи 9, 13
  services/api/domain.ts                                Edit   задачи 9, 13
  services/queries.ts                                   Edit   задачи 9, 13
  services/queries.test.tsx                             Edit   задача 9
  services/queryKeys.ts                                 Edit   задача 13
  App.tsx                                               Edit   задача 13
  pages/families/FamiliesPage.tsx                       Create задача 13
  pages/families/FamiliesTab.tsx                        Create задача 13
  pages/families/ContextsTab.tsx                        Create задача 13
  pages/families/ContextCard.tsx                        Create задача 13
  pages/families/*.test.tsx                             Create задача 13
docs/
  reference/schema.md                                   Edit   задача 1
  reference/screens.md                                  Edit   задачи 9 (## 2.), 14 (## 9.)
  AGENTS-revisions.md                                   Edit   задача 14
  product-roadmap.md                                    Edit   задача 14
  devlog/2026-09-22-catalog-families.md                 Create задача 15
AGENTS.md                                               Edit   задача 14
```

Удаляется: ничего.

## Решения плана, которых нет в спеке

1. **Эталонные данные переезжают под гит двумя фикстурами — и это решение
   гейта 3, а не плана.** Спека §6 требует прогонов на эталоне 101 строки
   (правило вида даёт 98, роль имени — семь `LOCATION_ONLY`, смешанное имя,
   четыре `GENERIC_WORK`) и на снимке 18 ручных разносов. Оба корпуса лежат в
   `tasks/`, который **целиком в `.gitignore` и существует на одной машине**:
   тест, опирающийся на них, зелен только у автора и после мержа не
   воспроизводится ничем. План предлагает завести
   `backend/tests/data/semantic_reference_101.json` (наименование, единица,
   эталонный вид, эталонная роль имени — **101 запись**) и
   `backend/tests/data/manual_overrides_18.json` (**18** пар «раздел →
   целевая статья»).
   **Решение принято пользователем на гейте 3 22.09.2026** как обезличенные
   тестовые данные в смысле `AGENTS.md` §9.3.
   **Контракт фикстур — часть решения, а не пожелание, и проверяется тестом
   ПО БЕЛОМУ СПИСКУ, а не по чёрному:**
   - у каждой фикстуры объявлено **точное множество допустимых ключей** —
     обязательные и опциональные порознь, — и проверка сверяет `row.keys()` с
     ним в **обе** стороны: неизвестный ключ отвергается, недостающий
     обязательный отвергается тоже;
   - `semantic_reference_101.json`: обязательные — название работы, единица,
     эталонный вид; опциональные — эталонная роль имени, код либо название
     статьи, пометка исключения;
   - `manual_overrides_18.json`: обязательные — название раздела и адрес целевой
     статьи; опциональных нет;
   - статья адресуется **устойчивым кодом либо названием классификатора**, а не
     стендовым первичным ключом: PK стенда у другой базы другой, и фикстура,
     завязанная на него, зелена ровно на одной машине — то есть ровно та
     болезнь, от которой файл и заводится;
   - `manual_overrides_18.json` собирается **заново как минимальная выжимка**, а
     не копированием `manual_overrides_snapshot.json`: снимок несёт стендовые
     идентификаторы и поля, которых контракт не допускает.
   **Почему белый список, а не чёрный:** перечень запрещённых ключей
   (`position_item_id`, `estimate_id`, `contract_id`, `offer_id`, `assigned_by`,
   времена, цены, объёмы) пропустил бы молча любой ключ, которого в перечне
   нет, — `project_code`, `object_title`, что угодно дописанное завтра. Обещание
   фикстуры звучит «в файле **только** эти поля», и проверять его обязан
   предикат той же формы: чёрный список доказывает отсутствие названного, а
   обещано отсутствие **всего остального**
   (`docs/insights/state-the-rule-as-an-equivalence.md`). Множество допустимых
   ключей живёт в тесте **структурой** и сверяется перебором.
   **Обоснования «эти имена уже уходят во внешнюю LLM» в решении нет
   намеренно** (снято на гейте 3): передача провайдеру и публикация в git —
   разные контуры, и довод одного не переносится на другой.
2. **Задачи идут схема → журнал → правила → корзины → импорт → операции →
   семьи → привязка → Review → каскад → проход → API → экран → ревизия →
   финал.** Порядок спеки «миграция → seed → разовый проход» (§2.9) — это
   порядок ЗАПУСКА команд, и он сохраняется внутри задач 7 и 11; порядок задач
   строится по зависимостям кода. Семьи разрезаны надвое намеренно: задача 7
   не может проверить ни отказ архивирования при живых привязках, ни слияние с
   переносом ссылок, потому что привязок в ней ещё не существует, — а
   утверждение, для которого нельзя построить вход, в задаче не место.
3. **Модули названы по предмету, а не по слою.** `services/semantic_rules.py` —
   чистые правила без `Session`; `services/context_routing.py` — корзины,
   правила и маршрут; `services/context_operations.py` — операции оператора;
   `services/work_families.py` — семьи и привязка; `crud/semantic.py` — чтение
   для экрана; `routers/semantic.py` — маршруты. Это то же разделение, которым
   живут `services/stage_summary.py` ↔ `crud/stage_summary.py`.
4. **Префикс API — `/api/v1/semantic`, маршрут экрана — `/families`.** Спека
   фиксирует только маршрут экрана (§2.10). Префикс общий на семьи и контексты,
   потому что операции ходят через обе сущности разом (назначение, слияние
   семей с переносом ссылок контекстов), и разрезать их по двум роутерам
   значило бы завести два места, где живёт один контракт.
5. **Членства на импорте строятся перечитыванием позиций по `estimate_ids`, а
   не расширением контракта `match_positions`.** Иначе задача 5 правила бы
   каскад матчинга, который спека §4 объявила незатрагиваемым. Оба владельца
   уже дают список смет в сессии B: `outcome.estimate_id` у договора и
   `round_outcome.estimate_ids` у раунда (`services/import_pipeline.py`).
6. **Сверка DoD 12 считает эффективную статью НЕЗАВИСИМЫМ SQL, а не тем же
   предикатом, которым маркировка ставит `STALE`.** Сверщик, делящий предикат
   с генератором, доказывает согласованность, а не правильность: обе стороны
   ошибутся одинаково и промолчат. Поэтому в задаче 10 у сверки свой запрос
   `position_items → chapter_item_id → work_category_id` против
   `context_buckets.work_category_id`, написанный отдельно от
   `context_routing.effective_category_id` и сверяемый с ним на всём стенде.
7. **`docs/reference/schema.md` правится задачей 1, а не задачей ревизии.**
   Справочник описывает СЕГОДНЯШНЕЕ состояние (`AGENTS.md` §9.2, правило 4), и
   раздел «Семантический контур» становится правдой ровно в тот коммит, где
   появляются таблицы. DoD 18 спеки перечисляет его рядом с ревизией — это
   перечень того, что фича обязана сделать, а не требование одного коммита;
   одним коммитом спека требует только три места девятого экрана (§1.13).
8. **Обе команды — подкоманды существующего `backend/cli.py`**
   (`seed-work-families`, `backfill-contexts`), а не отдельные скрипты в
   `backend/scripts/`. В `cli.py` уже стоит `_guard` на `ensure_mutation_allowed`,
   без которого команда, пишущая в БД, может уехать не в ту базу; а
   `backend/scripts/` по `AGENTS.md` §9.1 — место читающего инструментария, не
   мутирующего.
9. **Все отказы фичи — доменные, через существующий `raise_domain_error`**
   (`backend/routers/domain_errors.py`) с кодом и контекстными ключами:
   спека требует, чтобы отказ НАЗЫВАЛ число или оба значения, а не был
   человекочитаемой строкой. Коды перечислены в задачах; текст сообщения —
   человекочитаемый, значения — в контексте.
10. **Словарь мест и `PLACE_DICTIONARY_VERSION` живут в одном модуле рядом** —
    ровно как `NORM_VERSION` лежит в `services/matching.py` рядом с
    `cache_key`. Разнести константу и словарь значило бы разрешить им
    разъехаться.
11. **Фраза в `screens.md ## 2.` («Ручной матчинг») едет задачей 9**, а не
    задачей ревизии: описанное меняет именно она. Раздел `## 9.` и
    `EXPECTED_SCREEN_ANCHORS` остаются в задаче 14 — их разрыв страж красит.
12. **Разовый проход коммитит КАЖДУЮ завершённую партию, а не весь проход одной
    транзакцией.** Формулировки «партиями с прогрессом и прерыванием»
    недостаточно: при одной транзакции прерывание откатывает сделанное целиком,
    и обещание сохраняемого прогресса оказывается ложным на 100 335 позициях.
    Граница названа явно: `run_backfill` открывает и коммитит транзакцию **на
    партию**, прерывание оставляет **согласованный префикс** (у каждой
    обработанной позиции есть корзина, контекст и членство — частично
    обработанных позиций не бывает), а повторный запуск продолжает
    идемпотентно. Цена, которую надо принять вслух: прерванный проход
    оставляет каталог **частично** построенным, и это нормальное состояние, из
    которого выходят повторным запуском, а не ручной чисткой.
13. **Число тестов «ДО» замерено на `main`-состоянии ветки** (`906a86a`,
    2 947 собираемых тестов бэкенда, 1 038 фронтенда) командой
    `pytest --collect-only -q -k …`. У каждой задачи две команды: **узкая** (её
    собственный файл, ДО = 0 — она показывает, что новые имена вообще
    собираются) и **широкая** по области (ДО — замеренное число, ПОСЛЕ обязано
    вырасти). Сигналом служит широкая: если её выбор не вырос, новые классы в
    область не попали (`docs/process/implementation.md`, «Выбор команды
    проверки»).

---

## Задачи

### Task 1: миграция `0017` и модели семантического контура

**Files**
- Create: `backend/alembic/versions/2026_09_22_0017-semantic_contour.py`
  (префикс даты — день коммита задачи; ревизия `0017`)
- Edit: `backend/models.py`
- Edit: `backend/tests/conftest.py:383` (`_DOMAIN_TABLES`)
- Edit: `docs/reference/schema.md` (новый раздел «Семантический контур»; в
  «Каталожный контур» — одна фраза о том, что нормализованная пара называется
  идентичностью написания)
- Test: `backend/tests/integration/test_semantic_schema.py`

**Interfaces**
- Потребляет: `Base`, `users`, `units_of_measurement`, `work_categories`,
  `catalog_positions`, `position_items` (существуют, `backend/models.py`).
- Производит — шесть классов моделей и их перечисления; **форма таблиц,
  все `CHECK`, составные FK и частичные индексы заданы спекой §2.3 и здесь не
  дублируются**:

```python
class FamilyStatus(str, enum.Enum): ...      # draft | active | archived
class SemanticKind(str, enum.Enum): ...      # WORK | SYSTEM | UNKNOWN
class NameRole(str, enum.Enum): ...          # WORK | LOCATION_ONLY | GENERIC_WORK
class SemanticState(str, enum.Enum): ...     # SUGGESTED | CONFIRMED | NOT_APPLICABLE
class MembershipState(str, enum.Enum): ...   # CURRENT | STALE
class DecisionSource(str, enum.Enum): ...    # rule | manual
class FamilySource(str, enum.Enum): ...      # manual | suggestion
class RoutedBy(str, enum.Enum): ...          # default | rule | manual
class ComparabilityReason(str, enum.Enum): ...  # insufficient_description

class WorkFamily(Base): ...
class ContextBucket(Base): ...
class CatalogContext(Base): ...
class ContextRoutingRule(Base): ...
class ContextMember(Base): ...
class SemanticEvent(Base): ...

# Константы выражений CHECK — в шапке миграции и в models.py, сверяет parity-тест:
CK_FAMILY_ACTIVE_NEEDS_DEFINITION: str
CK_FAMILY_ACTIVATION_PAIR: str
CK_FAMILY_AUTHOR_IFF_NOT_SEED: str
CK_CONTEXT_FAMILY_PROVENANCE: str
CK_CONTEXT_KIND_SOURCE_PAIR: str
CK_CONTEXT_NAME_ROLE_SOURCE_PAIR: str
CK_MEMBER_RULE_PAIR: str
CK_MEMBER_CONFLICT_PAIR: str
CK_EVENT_ONE_SUBJECT: str
CK_EVENT_SUBJECT_BY_TYPE: str
CK_EVENT_PAYLOAD_NOT_EMPTY: str
```

**Утверждения**
- Шесть таблиц спеки §2.3 созданы; **каждое** ограничение доказано пробоем — по
  одному нарушенному ограничению на вход, и входы перечислены спекой §6 («Схема»)
  поимённо; ни один вход не нарушает двух ограничений сразу, иначе он не
  доказывает, какое из них сработало.
- **Обе стороны каждой равносильности предъявлены порознь**: `routed_by='rule'`
  без `routing_rule_id` и `routing_rule_id` при `routed_by='default'`;
  `*_source='manual'` без автора и автор при `source='rule'`; `seed_key` при
  заполненном `created_by` и пустой `created_by` без `seed_key`; `conflict_at`
  без `conflict_from_context_id` и наоборот. Правило, записанное импликацией,
  ловит половину дефектов.
- **Происхождение семьи — четыре входа, и четвёртый обязателен:** назначенная
  семья без происхождения; происхождение без семьи; `manual` без автора либо
  автор при `suggestion`; и **одинокий `family_by` при трёх пустых полях**. Без
  четвёртого тотальность предиката заявлена, а не доказана: пара
  равносильностей на этом входе давала `NULL`, то есть «не нарушено»
  (`docs/pitfalls/db.md`).
- **Границы, которые обязаны ПРОХОДИТЬ, а не падать:** две корзины без статьи у
  одной строки различаются (`COALESCE(work_category_id,-1)`); архивный контекст
  по умолчанию сосуществует с действующим (в предикате частичного индекса есть
  половина `archived_at IS NULL`); две ручные семьи с пустым `seed_key`
  сосуществуют (`NULL`-ы различны); две семьи с одним именем и единицей в
  `draft` и `archived` сосуществуют. Каждая из четырёх краснела бы в обратную
  сторону, если бы ограничение было записано полным вместо частичного.
- **Составной FK пробивается прямо**: членство, указывающее на контекст чужой
  корзины, отвергается; то же для правила маршрутизации.
- **Журнал:** событие с двумя предметами и событие без предмета — обе границы
  `num_nonnulls = 1`; семейное событие на контексте и контекстное событие на
  семье — обе стороны равносильности «тип ↔ предмет»; пустой `payload`
  отвергается. Отдельным входом — `context_family_assigned` с `family_id`
  отвергается, а он же с `context_id` **проходит**: именно этот вход ловил бы
  прежний предикат по префиксу `family_%`, и он заведён как свидетельство, что
  ограничение опирается на множество, а не на имя.
- **`downgrade` отказывает по четырём счётчикам ПО ОТДЕЛЬНОСТИ** — четыре входа,
  каждый называет, что именно держит откат: `work_families`,
  `catalog_contexts`, `context_members`, `semantic_events`. Одного счётчика
  мало, потому что семьи появляются раньше контекстов, контексты переживают
  удаление всех позиций, а журнал переживает всё. **Пятый вход — чистая база:
  откат проходит**, иначе доказано было бы только «отказывает всегда».
- Parity: каждое выражение `CHECK` в миграции побуквенно равно константе в
  `models.py`.
- **Все шесть таблиц перечислены в `_DOMAIN_TABLES` ЯВНО**
  (`backend/tests/conftest.py:383`) — это очистка вокруг тестов с настоящими
  коммитами, а двухсессионные проверки задач 6, 8 и 11 коммитят по-настоящему.
  Молчаливая опора на каскад здесь недопустима по двум разным причинам:
  - **`work_families` каскадом НЕ очистится вовсе** — на неё ссылается
    `catalog_contexts`, а не наоборот, и `TRUNCATE catalog_positions CASCADE`
    до неё не доходит. Семьи пережили бы тест, и соседний увидел бы чужие
    строки: частичная уникальность имени среди `active` сделала бы порядок
    тестов значимым, а зависимость от порядка — это ложно-зелёный прогон,
    ждущий перестановки;
  - **остальные пять очистились бы каскадом случайно**, то есть защитой,
    которой никто не объявлял: правка любого внешнего ключа сняла бы её молча.
    Перечисление говорит то же самое явно и переживает правку схемы.
  **Утверждение предъявляется снятием:** убрать `work_families` из перечня — и
  тест, проверяющий чистоту таблиц после committing-прогона, обязан покраснеть.
  Без снятия доказано лишь, что на сегодняшнем порядке тестов совпало.
- `docs/reference/schema.md` получил раздел «Семантический контур»; все `§N` в
  нём квалифицированы, шапка «Когда читать:» осталась одна.

**Имена**
- Заводятся этой задачей: `WorkFamily`, `ContextBucket`, `CatalogContext`,
  `ContextRoutingRule`, `ContextMember`, `SemanticEvent`, `FamilyStatus`,
  `SemanticKind`, `NameRole`, `SemanticState`, `MembershipState`,
  `DecisionSource`, `FamilySource`, `RoutedBy`, `ComparabilityReason`, все
  одиннадцать констант `CK_*`.
- Существуют, проверено `grep`-ом: `CatalogKind` (`backend/models.py:175`),
  `CatalogPosition` (`models.py:895`), `PositionItem` (`models.py:997`),
  `WorkCategory` (`models.py:1204`), `op.execute` (миграции `0002`, `0003`,
  `0015`), `_DOMAIN_TABLES` (`backend/tests/conftest.py:383`),
  `_truncate_domain_tables` (там же, `:407`), `committing_session_factory`
  (там же, `:415`).

**Проверка**
- `just test-int-local-k semantic_schema` — зелёная. ДО **0**, ПОСЛЕ — все входы
  задачи; точное число называет отчёт о задаче.
- `just test-int-local-k schema` — зелёная. ДО **184**
  (`test_schema_constraints.py`, `test_category_overrides_schema.py`,
  `test_tenders_schema.py` и соседи), ПОСЛЕ — строго больше: имя нового файла
  содержит `schema`, и не вырасти выбор не может.
- `just lint-backend` — зелёная.
- Страж на этой задаче не обещается (Global Constraints).

---

### Task 2: журнал — закрытый список событий и обязательный состав `payload`

**Files**
- Create: `backend/services/semantic_events.py`
- Test: `backend/tests/integration/test_semantic_events.py`

**Interfaces**
- Потребляет: `SemanticEvent`, `CatalogContext`, `WorkFamily` (задача 1).
- Производит:

```python
class SemanticEventError(Exception): ...

FAMILY_EVENT_TYPES: frozenset[str]     # ровно те пять, что в CHECK задачи 1
CONTEXT_EVENT_TYPES: frozenset[str]
EVENT_REQUIRED_KEYS: dict[str, frozenset[str]]   # таблица спеки §2.14 целиком
EVENT_ENUM_VALUES: dict[tuple[str, str], frozenset[str]]
    # (тип события, ключ payload) → допустимые значения перечислимых ключей
    # (`origin`, `reason`, `source`) — спека §2.14

def record_event(
    db: Session,
    *,
    event_type: str,
    payload: dict[str, object],
    context_id: int | None = None,
    family_id: int | None = None,
    actor_id: int | None = None,
) -> SemanticEvent
```

**Утверждения**
- `EVENT_REQUIRED_KEYS` содержит **ровно пятнадцать** ключей — столько же, сколько
  строк в таблице спеки §2.14; лишний или недостающий тип обязан покраснеть.
- Запись события **без обязательного ключа отказывает** `SemanticEventError`,
  называя недостающий ключ; запись с неизвестным `event_type` отказывает.
- Значение перечислимого ключа вне `EVENT_ENUM_VALUES` отказывает: `origin`,
  `reason` и `source` спека перечисляет закрытыми множествами, и валидатор,
  проверяющий только присутствие ключа, пропустил бы чужое значение.
- **Предмет ставится по типу, а не аргументом вызова:** `record_event` с
  семейным типом и `context_id` отказывает **до** базы, тем же множеством, что
  и `CHECK`; обратный случай — тоже.
- **Согласованность кода и схемы проверяется внешней сверкой:** множество
  `FAMILY_EVENT_TYPES` равно множеству семейных типов, перечисленных в
  константе `CK_EVENT_SUBJECT_BY_TYPE` задачи 1, а объединение
  `FAMILY_EVENT_TYPES | CONTEXT_EVENT_TYPES` равно множеству значений
  `CHECK`-а на `event_type`. Разойтись им нечем только пока их сверяет тест.
- Тест строится **перебором `EVENT_REQUIRED_KEYS`**, а не перечислением руками:
  иначе новый тип войдёт без проверки и никто этого не заметит
  (`docs/insights/enumerate-the-rules-own-properties.md`).

**Имена**
- Заводятся этой задачей: `SemanticEventError`, `FAMILY_EVENT_TYPES`,
  `CONTEXT_EVENT_TYPES`, `EVENT_REQUIRED_KEYS`, `EVENT_ENUM_VALUES`,
  `record_event`.
- Существуют, проверено `grep`-ом: `SemanticEvent`, `CK_EVENT_SUBJECT_BY_TYPE`
  (задача 1); `Session` (`sqlalchemy.orm`).

**Проверка**
- `just test-int-local-k semantic_events` — зелёная. ДО **0**, ПОСЛЕ — не меньше
  пятнадцати (перебор типов) плюс входы валидатора.
- `just test-int-local-k semantic` — зелёная. ДО **12** на `main` плюс то, что
  завела задача 1 (её отчёт называет число); ПОСЛЕ — строго больше.
- `just lint-backend` — зелёная.

---

### Task 3: правила — вид работы, роль имени, словарь мест с версией

**Files**
- Create: `backend/services/semantic_rules.py`
- Create: `backend/tests/data/semantic_reference_101.json` (решение плана 1)
- Test: `backend/tests/unit/test_semantic_rules.py`

**Interfaces**
- Потребляет: `UnitResolver`, `ResolvedUnit`, `NO_UNIT_NORM`
  (`backend/services/unit_resolution.py`, существуют);
  `normalize_job_title_with_lemmatization` (`backend/parser/sanitize_text.py`,
  существует — **вызывается, не переписывается**: вторых представлений строки
  в проекте не заводится, спека §2.4).
- Производит:

```python
PLACE_DICTIONARY_VERSION: int          # аналог NORM_VERSION в services/matching.py
PLACE_TOKENS: frozenset[str]           # признаки места: секция, корпус, этап,
                                       # урбан-блок, паркинг, уровень, этаж, зона
GENERIC_WORK_TOKENS: frozenset[str]    # род изделия / поверхность без состава
SYSTEM_UNIT_NORM: str                  # нормализованная «компл»

@dataclass(frozen=True)
class NameRoleOutcome:
    role: str                          # NameRole
    location: str | None               # префикс места у смешанного имени
    work_title: str | None             # остаток имени либо имя рабочего раздела
    comparability_reason: str | None   # ComparabilityReason либо None

def classify_kind(unit_norm: str) -> str
def classify_name_role(
    title: str, *, chapter_chain: tuple[str, ...]
) -> NameRoleOutcome
```

**Утверждения**
- **Правило вида на эталоне 101 строки даёт 98 совпадений** (спека §1.9) — число
  здесь утверждение, а не обвязка: оно и есть основание, по которому вид ставит
  правило, а не модель. **Три исключения названы в тесте поимённо** и разобраны
  по природе (спека §1.14): «Стены толщ. 200мм» (вид неизвестен человеку),
  «Оборудование службы безопасности паркинга» (единица «шт» — опечатка входа),
  «Подключение блоков к КЛ ЭОМ…» (комплект означает состав работы).
- `classify_kind` ставит `SYSTEM` тогда и только тогда, когда нормализованная
  единица равна `SYSTEM_UNIT_NORM`; вход с другой единицей даёт `WORK` —
  **обе** стороны, потому что импликация ловит половину.
- **Семь чистых `LOCATION_ONLY` эталона** получают `work_title` из ближайшего
  **рабочего** раздела цепочки; **`LOCATION_ONLY` без рабочего раздела над ней**
  получает `comparability_reason = insufficient_description` и пустой
  `work_title`.
- **Смешанное имя разводится, и проверяются ОБА выхода** — `location` и
  остаток; проверка одного выхода прошла бы при потерянном втором.
- **Четыре решённых `GENERIC_WORK`** («Светильники», «Полы:», «Стены:»,
  «Потолок:») получают `insufficient_description`, и роль у них именно
  `GENERIC_WORK`, а не `LOCATION_ONLY`: спека §1.6 разводит эти классы, и одно
  правило на оба дало бы неверный разбор обоим.
- **Негативный вход на словарь:** форма места, которой в `PLACE_TOKENS` нет,
  обязана дать `WORK`, а не `LOCATION_ONLY`. Без него проверена только
  положительная половина предиката, и словарь, возвращающий «место» на всё,
  остался бы зелёным.
- Модуль не импортирует `Session` и не обращается к БД ни одной строкой: правило
  обязано быть исполнимым на строке из файла, иначе разовый проход (задача 11)
  не сможет прогнать его на 100 335 позициях без кластера.
- **Контракт фикстуры проверяется БЕЛЫМ СПИСКОМ** (решение плана 1):
  `row.keys()` каждой из 101 записи сверяется с объявленным множеством
  допустимых ключей **в обе стороны** — неизвестный ключ отвергается,
  недостающий обязательный отвергается тоже. Статья адресована **кодом либо
  названием классификатора**, а не числом.
- **Негативный вход на саму проверку:** запись с дописанным посторонним ключом
  (например `project_code`) обязана её покрасить. Без него проверка
  неотличима от чёрного списка, который пропустил бы ровно этот случай, — а
  обещано «в файле только эти поля», то есть отсутствие **всего остального**.

**Имена**
- Заводятся этой задачей: `PLACE_DICTIONARY_VERSION`, `PLACE_TOKENS`,
  `GENERIC_WORK_TOKENS`, `SYSTEM_UNIT_NORM`, `NameRoleOutcome`,
  `classify_kind`, `classify_name_role`.
- Существуют, проверено `grep`-ом: `NORM_VERSION` (`services/matching.py:67` —
  образец «константа рядом со своим правилом»), `UnitResolver`
  (`services/unit_resolution.py:55`), `NO_UNIT_NORM`
  (`services/unit_resolution.py:28`), `normalize_job_title_with_lemmatization`
  (`backend/parser/sanitize_text.py`), `SemanticKind`, `NameRole`,
  `ComparabilityReason` (задача 1).

**Проверка**
- `just test-unit-k semantic_rules` — зелёная. ДО **0**, ПОСЛЕ — не меньше
  одиннадцати входов, перечисленных в утверждениях, плюс перебор 101 эталонной
  строки одним параметризованным входом.
- `just test-backend-unit` — зелёная; выбор не уменьшается.
- `just lint-backend` — зелёная.

---

### Task 4: корзины, контексты и маршрутизация позиции

**Files**
- Create: `backend/services/context_routing.py`
- Test: `backend/tests/integration/test_context_routing.py`
- Test: `backend/tests/integration/test_context_membership.py`

**Interfaces**
- Потребляет: `classify_kind`, `classify_name_role`, `PLACE_DICTIONARY_VERSION`
  (задача 3); `record_event` (задача 2); `ContextBucket`, `CatalogContext`,
  `ContextRoutingRule`, `ContextMember` (задача 1); `CatalogKind`,
  `PositionItem.chapter_item_id`, `PositionItem.work_category_id`,
  `PositionItem.category_source`, `PositionItem.is_chapter` (существуют).
- Производит:

```python
class RoutingError(Exception): ...

NO_CATEGORY_SENTINEL: int              # -1, тот же приём, что COALESCE(unit_id,-1)

PREDICATE_NEAREST_CHAPTER_EQUALS: str  # nearest_chapter_equals
PREDICATE_CHAPTER_CHAIN_CONTAINS: str  # chapter_chain_contains
PREDICATE_CHAPTER_LEVEL_EQUALS: str    # chapter_level_equals
PREDICATE_KINDS: frozenset[str]

class RulePredicate(TypedDict, total=False):
    kind: str
    value: str
    level: int

@dataclass(frozen=True)
class ChapterContext:
    """Цепочка разделов позиции: от ближайшего к корню."""
    chain: tuple[str, ...]
    nearest: str | None
    category_id: int | None
    category_source: str | None

@dataclass(frozen=True)
class RoutingOutcome:
    buckets_created: int
    contexts_created: int
    members_created: int
    members_skipped_chapters: int
    members_skipped_unmatched: int

def effective_category_id(db: Session, position_item_id: int) -> int | None
def chapter_context(db: Session, position_item_id: int) -> ChapterContext
def get_or_create_bucket(
    db: Session, *, catalog_position_id: int, work_category_id: int | None
) -> tuple[ContextBucket, bool]
def lock_buckets(db: Session, bucket_ids: list[int], *, exclusive: bool) -> None
def evaluate_predicate(predicate: RulePredicate, chapters: ChapterContext) -> bool
def route_position(db: Session, *, position_item_id: int) -> ContextMember
def route_positions(db: Session, *, estimate_ids: list[int]) -> RoutingOutcome
```

**Утверждения**
- **Корзина идемпотентна:** два конкурентных импорта той же пары «написание ×
  эффективная статья» дают **одну** корзину (двухсессионный тест по образцу
  `test_category_override_concurrency.py`), а не две и не отказ.
- **Порядок правил соблюдается:** два правила, оба истинны, срабатывает первое по
  `ordinal`; **перестановка `ordinal` меняет результат** — без второй половины
  тест доказывал бы совпадение, а не порядок.
- Ни одно правило не сработало → действующий контекст по умолчанию,
  `routed_by = 'default'`, `routing_rule_id` пуст.
- **Корзина без действующего контекста по умолчанию даёт отказ, называющий
  корзину**, а не тихую подстановку. Вход строится **прямой правкой строки в
  обход операции**: сама операция архивирования такое состояние создать не даёт
  (задача 6), и это признаётся явно, а не маскируется.
- **`routed_by = 'manual'` переживает повторную маршрутизацию:** правило,
  указывающее на другой контекст, членство не переписывает. **Негативная
  сторона: снятие проверки на `manual` обязано покрасить тест** — иначе
  доказано, что на этом входе совпало, а не что ручное решение сильнее правила.
- **Эффективная статья берётся у строки-раздела, а не у позиции**: у позиции её
  нет по `ck_position_items_article_only_on_chapters`, и попытка прочесть её у
  позиции дала бы `-1` на всём каталоге, оставшись зелёной на входе без
  разделов. Позиция без раздела и раздел без статьи дают корзину
  `NO_CATEGORY_SENTINEL` — **два разных входа**, потому что путь до `None`
  у них разный.
- **Членства не имеют двух разных категорий позиций, и это различимо запросом:**
  `is_chapter = true` (носитель статьи, не работа) и позиция без
  `catalog_position_id` (неопознаваемая пара). `RoutingOutcome` считает их
  **разными счётчиками** — одно «нет членства» на два факта запрещено
  (`docs/insights/one-value-two-states.md`).
- **Маршрутизация берёт `FOR SHARE` на КАЖДУЮ существующую корзину, в которую
  маршрутизирует, и это контракт задачи 4, а не задачи 6.** Вновь созданную
  корзину блокировать не нужно — её ещё никто не видит. Когда корзин несколько,
  порядок захвата — **по возрастанию `id`**. Утверждение предъявляется тремя
  входами: блокировка **взята** (двухсессионный вход: `FOR UPDATE` на той же
  корзине задерживает маршрутизацию), **режим именно `FOR SHARE`** (проверяется
  **компиляцией запроса в SQL**, а не намерением: `with_for_update(read=True)` —
  это `FOR SHARE`, а `key_share=True` — это `FOR NO KEY UPDATE`,
  `docs/pitfalls/db.md`), и **`FOR SHARE` совместим сам с собой** — два
  параллельных импорта друг друга не задерживают, иначе хот-путь
  сериализовался бы. Без третьего входа защита неотличима от `FOR UPDATE`,
  который «тоже работает», но стоит пропускной способности импорта.
- **Корзина «Прочее» — полноценная статья, и отдельной ветки у неё нет.**
  Доказывается **отсутствием упоминания «Прочее» в правиле маршрутизации**:
  утверждение о коде модуля, а не о поведении на одном входе — ветка, которой
  нет, не может сработать на непроверенной статье. Положительный вход: позиция
  статьи «Прочее» получает корзину тем же путём, что и любая другая.
- **Вид и роль имени ставятся при создании контекста** правилами задачи 3;
  контекст строки `kind ∈ {HEADER, LOT_HEADER, TRASH}` рождается сразу
  `NOT_APPLICABLE`, строки `kind ∈ {TO_REVIEW, POSITION}` — `SUGGESTED` с
  `source = 'rule'`; `place_dictionary_version` записан.
- Каждое создание контекста пишет `context_created` с `bucket_id` и `origin`.

**Имена**
- Заводятся этой задачей: `RoutingError`, `NO_CATEGORY_SENTINEL`,
  `PREDICATE_NEAREST_CHAPTER_EQUALS`, `PREDICATE_CHAPTER_CHAIN_CONTAINS`,
  `PREDICATE_CHAPTER_LEVEL_EQUALS`, `PREDICATE_KINDS`, `RulePredicate`,
  `ChapterContext`, `RoutingOutcome`, `effective_category_id`,
  `chapter_context`, `get_or_create_bucket`, `lock_buckets`,
  `evaluate_predicate`, `route_position`, `route_positions`.
- Существуют, проверено `grep`-ом: `chapter_item_id`, `category_source`
  (`backend/models.py`, 104 вхождения каждое),
  `ck_position_items_article_only_on_chapters` (миграция `0006`, `models.py`),
  `committing_session_factory` (`backend/tests/conftest.py:415`),
  `CategoryResolver` (`services/category_resolution.py`).

**Проверка**
- `just test-int-local-k "context_routing or context_membership"` — зелёная.
  ДО **0**, ПОСЛЕ — все входы задачи.
- `just test-int-local-k context` — зелёная. ДО **9** (посторонние совпадения в
  `test_domain_errors.py`, `test_passport_article_rates.py` и парсере), ПОСЛЕ —
  строго больше: оба новых файла содержат `context` в имени.
- `just lint-backend` — зелёная.

---

### Task 5: построение членств на импорте (сессия B)

**Files**
- Edit: `backend/services/import_pipeline.py` (`run_import_job`, между
  `match_positions` и `finalize_done`)
- Test: `backend/tests/integration/test_import_pipeline.py` (новые утверждения;
  существующие не правятся)

**Interfaces**
- Потребляет: `route_positions`, `RoutingError` (задача 4); `match_positions`,
  `finalize_done`, `ImportOutcome.estimate_id`, `RoundImportOutcome.estimate_ids`
  (существуют).
- Производит: ни одного нового публичного имени. Вызов `route_positions`
  встраивается в уже открытую транзакцию сессии B; `RoutingError` добавляется в
  цепочку `except` рядом с `EstimateImportError` и роняет job штатным путём.

**Утверждения**
- **Членства пишутся в сессии B, между матчингом и `finalize_done`.**
  Доказывается **инъекцией отказа после матчинга**: job уходит в `error`, смета
  откатилась, и членств нет **ни одного**. Если бы они писались своей
  транзакцией, они пережили бы откат — и именно это, а не наличие членств на
  успешном прогоне, доказывает принадлежность транзакции
  (`docs/insights/data-flow-assertions-for-order.md`).
- **Пять счётчиков `import_jobs` те же, что до фичи**, побайтно: фича их не
  расширяет (спека §2.9). Утверждение отдельное, а не следствие: шестой
  счётчик отвечал бы на другой вопрос и поменял бы внешний контракт, который
  сегодня проверен посимвольно.
- **Отказ маршрутизации доменный, а не диагностический:** корзина без
  действующего контекста по умолчанию роняет job в `error` с текстом,
  называющим корзину, а не завершает импорт без членств.
- Существующие `test_estimate_import.py`, `test_import_pipeline.py`,
  `test_import_fixture_e2e.py`, `test_matching.py` зелёные **без правок
  утверждений** — ни одно существующее утверждение не переписывается, только
  добавляются новые.
- Оба владельца покрыты: договор (`estimate_id`) и раунд тендера
  (`estimate_ids`, N смет одним файлом). Покрытие одного владельца оставило бы
  раунд без членств и осталось бы зелёным.

**Имена**
- Заводятся этой задачей: ни одного.
- Существуют, проверено `grep`-ом: `run_import_job`
  (`services/import_pipeline.py:213`), `match_positions` (там же, 15 вхождений),
  `finalize_done` (`services/import_pipeline.py:165`), `import_estimate`
  (`services/estimate_import.py:466`), `route_positions` (задача 4).

**Проверка**
- `just test-int-local-k import_pipeline` — зелёная. ДО **25**, ПОСЛЕ — строго
  больше: задача заводит новые утверждения в этом же файле.
- `just test-int-local-k "review or matching"` — зелёная. ДО **247**, ПОСЛЕ —
  **те же 247**, и совпадение здесь **ожидаемо**: задача не заводит в этой
  области ни одного теста и обязана не сдвинуть ни одного существующего.
- `just lint-backend` — зелёная.

---

### Task 6: операции оператора над контекстами и протокол блокировки корзины

**Files**
- Create: `backend/services/context_operations.py`
- Test: `backend/tests/integration/test_context_operations.py`
- Test: `backend/tests/integration/test_context_concurrency.py`

**Interfaces**
- Потребляет: `lock_buckets`, `route_position`, `RoutingError`, `RulePredicate`
  (задача 4); `record_event` (задача 2).
- Производит:

```python
class ContextOperationError(Exception): ...

REFUSE_CONTEXT_NOT_EMPTY: str        # с числом членств
REFUSE_INCOMING_RULES: str           # с числом правил
REFUSE_DEFAULT_WITHOUT_SUCCESSOR: str  # с именем корзины

@dataclass(frozen=True)
class SplitResult:
    new_context_id: int
    moved_members: int
    rule_id: int | None
    default_replaced: bool

def split_context(
    db: Session, *, context_id: int, position_item_ids: list[int],
    rule: RulePredicate | None, actor_id: int,
) -> SplitResult
def merge_contexts(
    db: Session, *, source_context_id: int, target_context_id: int, actor_id: int
) -> int
def move_members(
    db: Session, *, position_item_ids: list[int], target_context_id: int,
    actor_id: int, reason: str,
) -> int
def archive_context(
    db: Session, *, context_id: int, new_default_context_id: int | None,
    actor_id: int,
) -> None
```

**Утверждения**
- **Разделение с исполнимым правилом:** правило встаёт в корзину, и **следующий
  импорт той же корзины уходит по правилу**. Проверяется маршрутом новой
  позиции, а не наличием строки правила: строка может лежать и не применяться.
- **Разделение без правила:** контекстом по умолчанию становится **свежий**
  контекст, и следующий импорт попадает **в него, а не в разделённый**. Это и
  есть утверждение, ради которого замена контекста по умолчанию заведена: без
  неё следующий импорт влил бы новые позиции в контекст с принятым решением и
  отменил разделение молча. Прежний контекст по умолчанию **не архивируется** —
  он теряет только флаг, и `archived_at` у него проверяется пустым.
- **Слияние контекстов:** членства переезжают, источник архивируется, правила,
  указывавшие на источник, **переведены на цель** — проверяется маршрутом, а не
  значением колонки. Контексты **разных** корзин сливать нельзя — отдельный
  отказ.
- **Перенос:** `routed_by = 'manual'`, и последующая маршрутизация членство не
  переписывает (вторая половина — в задаче 4).
- **Архивирование: три предусловия — три входа, каждый со своим отказом и своим
  числом**: контекст с одним членством (`REFUSE_CONTEXT_NOT_EMPTY`, число
  членств), контекст с одним входящим правилом (`REFUSE_INCOMING_RULES`, число
  правил), действующий контекст по умолчанию без назначения нового
  (`REFUSE_DEFAULT_WITHOUT_SUCCESSOR`, имя корзины). Один общий отказ на три
  предусловия не отличил бы, какое сработало.
- **Положительный вход:** пустой контекст, без входящих правил, не по
  умолчанию — архивируется. После него — **сверка по всей базе, что ни одно
  правило не ведёт в архивный контекст**: ограничение схемой не выражается
  (цель FK обязана быть полным уникальным ограничением), и только сверка
  отличает «операция его держит» от «на этом входе совпало».
- **Восстановления нет, но утверждать это здесь нечем, и задача его не
  утверждает.** Семантического роутера на конец задачи 6 не существует, и
  проверка «маршрута `…/restore` нет» была бы зелена тривиально — она
  доказывала бы отсутствие роутера, а не отсутствие операции. Утверждение о
  **наборе маршрутов** живёт в задаче 12, об отсутствии действия в интерфейсе —
  в задаче 13.
- **Протокол блокировки корзины проверяется тремя двухсессионными входами, и у
  каждого своя негативная сторона:**
  - **импорт против архивирования:** сессия A начинает архивирование пустого
    контекста, сессия B маршрутизирует в него позицию. Непустого архивного
    контекста не возникает **ни в одном порядке** — проверяются оба;
  - **создание правила против архивирования** — то же на предусловии 2;
  - **снятие `FOR SHARE` у маршрутизации обязано воспроизвести гонку**, то есть
    тест обязан увидеть непустой архивный контекст. Без этого прогона нельзя
    сказать, что защиту держит протокол, а не расписание планировщика; и
    именно этим проверяется, что **неявный замок внешнего ключа недостаточен
    как ЗАЯВЛЕННЫЙ механизм** — он, возможно, и сработает, но защитой объявлен
    не он (спека §2.8);
  - **перечитывание после блокировки снимается отдельно:** убрать повторный
    подсчёт — и архивирование пройдёт по устаревшему снимку.
- Режим блокировки проверяется **компиляцией запроса в SQL**, а не намерением:
  `with_for_update(read=True)` — это `FOR SHARE`, а `key_share=True` — это
  `FOR NO KEY UPDATE` (`docs/pitfalls/db.md`).
- Порядок захвата корзин — **по возрастанию `id`**, когда операция трогает
  несколько.
- Каждая операция пишет своё событие: `context_split`, `context_merged`,
  `members_moved`, `context_archived`, `routing_rules_dropped`.

**Имена**
- Заводятся этой задачей: `ContextOperationError`, `REFUSE_CONTEXT_NOT_EMPTY`,
  `REFUSE_INCOMING_RULES`, `REFUSE_DEFAULT_WITHOUT_SUCCESSOR`, `SplitResult`,
  `split_context`, `merge_contexts`, `move_members`, `archive_context`.
- Существуют, проверено `grep`-ом: `committing_session_factory`
  (`tests/conftest.py:415`), `_wait_until_a_backend_blocks`
  (`tests/integration/test_category_override_concurrency.py` — приём
  копируется, не импортируется: тестовые хелперы в этом репозитории между
  модулями не делятся), `lock_estimate` (`services/category_override.py:67` —
  образец `FOR UPDATE`).

**Проверка**
- `just test-int-local-k "context_operations or context_concurrency"` — зелёная.
  ДО **0**, ПОСЛЕ — не меньше девяти входов (три предусловия, положительный,
  сверка по базе, три гонки, снятие перечитывания).
- `just test-int-local-k context` — зелёная. ДО — **9** плюс то, что завела
  задача 4; ПОСЛЕ — строго больше.
- `just lint-backend` — зелёная.

---

### Task 7: семьи — жизненный цикл, seed-файл и команда загрузки

**Files**
- Create: `backend/services/work_families.py`
- Create: `backend/seeds/work_families_initial.json`
- Edit: `backend/cli.py` (подкоманда `seed-work-families`)
- Test: `backend/tests/integration/test_work_families.py`

**Interfaces**
- Потребляет: `WorkFamily`, `FamilyStatus` (задача 1); `record_event` (задача 2);
  `UnitResolver` (существует); `_guard`, `ensure_mutation_allowed`
  (`backend/cli.py`, `backend/db_guard.py`, существуют).
- Производит:

```python
class WorkFamilyError(Exception): ...

SEED_PATH: Path                        # backend/seeds/work_families_initial.json
REFUSE_ACTIVATE_WITHOUT_DEFINITION: str

@dataclass(frozen=True)
class SeedReport:
    created: int
    skipped_existing: int
    with_definition: int

def create_family(
    db: Session, *, title: str, unit_name: str | None,
    definition: str | None, actor_id: int,
) -> WorkFamily
def update_family(
    db: Session, *, family_id: int, title: str | None,
    definition: str | None, actor_id: int,
) -> WorkFamily
def activate_family(db: Session, *, family_id: int, actor_id: int) -> WorkFamily
def load_seed(db: Session, *, path: Path = SEED_PATH) -> SeedReport
```

**Утверждения**
- **Seed кладёт 42 черновика**: 42 записи, у **каждой** ровно одна единица, у
  **шести** заполнено `definition`, у тридцати шести — пусто; у каждой записи
  есть `seed_key`, `created_by` пуст, `status = 'draft'`. Числа 42 и 6 —
  утверждения: они и есть причина, по которой активация не входит в приёмку
  фичи (спека §1.14, §2.7).
- Единица в файле записана **каноническим именем**, разрешаемым `UnitResolver`,
  а не идентификатором: идентификаторы у разных баз разные, и seed с `unit_id`
  не переносится на стенд.
- **Повторный запуск не создаёт дублей и не трогает правок пользователя:**
  определение, дописанное между запусками, остаётся на месте.
- **Главный вход — переименование:** оператор меняет имя семьи, seed
  запускается снова, второго черновика **не появляется**. Поиск по паре «имя ×
  единица» на этом входе обязан покраснеть — иначе `seed_key` ничем не оправдан
  (частичная уникальность здесь не спасает: она действует только среди
  `active`, а черновики дубли допускают).
- **Активация без определения отказывает** (`REFUSE_ACTIVATE_WITHOUT_DEFINITION`);
  после дописывания определения проходит и заполняет пару
  `activated_by`/`activated_at` целиком.
- **Две активные семьи с одинаковыми именем и единицей невозможны; те же две в
  `draft` и в `archived` — возможны.** Обе стороны, потому что индекс частичный
  и проверка одной стороны не отличила бы его от полного.
- События: `family_created` с `origin = 'seed'` у seed-записей и
  `origin = 'operator'` у ручных; `family_updated` с `changed` (список полей с
  `from` и `to`); `family_activated`.
- Команда отказывается мутировать БД, цель которой не разрешена текущим
  `APP_ENV` (`_guard`) — иначе seed уезжает в чужую базу без единого признака.

**Имена**
- Заводятся этой задачей: `WorkFamilyError`, `SEED_PATH`,
  `REFUSE_ACTIVATE_WITHOUT_DEFINITION`, `SeedReport`, `create_family`,
  `update_family`, `activate_family`, `load_seed`, подкоманда
  `seed-work-families`.
- Существуют, проверено `grep`-ом: `UnitResolver`
  (`services/unit_resolution.py:55`), `ResolvedUnit` (там же, строка 32),
  `_guard` (`backend/cli.py:23`), `ensure_mutation_allowed`
  (`backend/db_guard.py`), `WorkFamily`, `FamilyStatus` (задача 1).

**Проверка**
- `just test-int-local-k work_families` — зелёная. ДО **0**, ПОСЛЕ — все входы
  задачи.
- `just test-int-local-k "family or families"` — зелёная. ДО **0** на `main`;
  ПОСЛЕ — то же число, что даёт узкая команда. Совпадение здесь не сигнал: до
  фичи в проекте слова «семья» нет вовсе, и широкой области у задачи пока не
  существует — она появится в задаче 8.
- `just lint-backend` — зелёная.

---

### Task 8: привязка семьи к контексту, слияние и архивирование семей

**Files**
- Edit: `backend/services/work_families.py`
- Test: `backend/tests/integration/test_work_families.py` (дополняется)
- Test: `backend/tests/integration/test_semantic_state.py`

**Interfaces**
- Потребляет: `create_family`, `activate_family`, `WorkFamilyError` (задача 7);
  `CatalogContext`, `SemanticState`, `DecisionSource`, `FamilySource`
  (задача 1); `record_event` (задача 2); `classify_kind` (задача 3).
- Производит:

```python
REFUSE_UNIT_MISMATCH: str              # называет ОБА значения единиц
REFUSE_FAMILY_NOT_ACTIVE: str          # называет статус
REFUSE_UNIT_CHANGE_WITH_LINKS: str     # называет число привязанных контекстов
REFUSE_ARCHIVE_WITH_LINKS: str         # называет число привязанных контекстов
REFUSE_MERGE_UNIT_MISMATCH: str
REFUSE_MERGE_INACTIVE: str

def assign_family(
    db: Session, *, context_id: int, family_id: int | None, actor_id: int
) -> CatalogContext
def set_unit(
    db: Session, *, family_id: int, unit_name: str | None, actor_id: int
) -> WorkFamily
def archive_family(db: Session, *, family_id: int, actor_id: int) -> WorkFamily
def merge_families(
    db: Session, *, source_family_id: int, target_family_id: int, actor_id: int
) -> int
def confirm_kind(
    db: Session, *, context_id: int, kind: str | None, actor_id: int
) -> CatalogContext
def unconfirm_kind(db: Session, *, context_id: int, actor_id: int) -> CatalogContext
def set_name_role(
    db: Session, *, context_id: int, role: str, actor_id: int
) -> CatalogContext
```

**Утверждения**
- **Назначение: единица обязана совпасть**, сравнением `COALESCE(unit_id,-1)` —
  тем же выражением, которым сравнивается каталог. Отказ называет **оба**
  значения. Пары «семья без единицы × строка с единицей» и обратная — **два
  отдельных входа**, потому что `COALESCE(unit_id,-1)` ошибается именно на них.
- **Семья обязана быть `active`:** `draft` → отказ, `archived` → отказ,
  `active` → успех. Три входа, а не один: отказ обязан называть статус.
- **Семья у `SYSTEM` разрешена** — запрета нет, и это отдельный положительный
  вход: в эталоне ни одна из 21 системы семьи не имеет, но запрет потребовал бы
  решения, которого никто не принимал (спека §1.14, §3).
- **`semantic_state` назначением не меняется** — сравнение до и после. И
  **обратная сторона**: подтверждение вида не трогает `work_family_id`. Обе
  стороны, потому что независимость осей — утверждение о двух направлениях.
- **Снятие семьи очищает тройку происхождения целиком** (`CHECK` на 0 или 3
  непустых): остаточный `family_by` при пустой семье — ровно тот вход, который
  доказан в задаче 1.
- **Переходы таблицы спеки §2.5, ДОСТИЖИМЫЕ ПРЯМОЙ ОПЕРАЦИЕЙ, пройдены
  прогоном:** рождение контекста в `SUGGESTED` с видом по правилу (задача 4),
  `SUGGESTED → CONFIRMED` подтверждением либо переопределением оператора, и
  обратный `CONFIRMED → SUGGESTED` **с пересчётом вида** (а не сохранением
  прежнего значения — проверяется сравнением поля, а не отсутствием ошибки).
- **Переходы через `set_kind` эта задача НЕ утверждает.** `services/review.py`
  правит задача 9, и на конец задачи 8 переводов `HEADER`/`TRASH` в
  `NOT_APPLICABLE` не существует — утверждение о них здесь было бы обещанием о
  чужом коде. Все входы, идущие через `set_kind` (два перехода в
  `NOT_APPLICABLE`, неизменность контекста при `POSITION`, отвержение
  `LOT_HEADER` и терминальность `NOT_APPLICABLE`), стоят в задаче 9 вместе с
  кодом, который их делает.
- **Множество значений `semantic_state` — ровно три**, и это держит `CHECK`, а
  не соглашение: пробой `semantic_state = 'PENDING'` обязан дать
  `IntegrityError`. Утверждение о границе схемы сильнее утверждения «ни один
  путь не производит четвёртого значения».
- **Правка единицы семьи при живых привязках отказывает с их числом**; при нуле
  привязок проходит. Обе стороны.
- **Архивирование семьи при живых привязках отказывает с их числом**; после
  снятия семьи у контекстов проходит. **Гонка «назначение против
  архивирования»:** сессия B назначает семью, пока сессия A архивирует;
  архивирование перечитывает число привязок под `FOR UPDATE` и отказывает.
  **Снятие `FOR SHARE` у назначения обязано дать архивную семью с живой
  привязкой** — иначе доказано лишь то, что порядок сложился удачно.
- **Слияние семей:** разные единицы → отказ; **цель `draft` → отказ, цель
  `archived` → отказ, источник не `active` → отказ** — по входу на каждое из
  трёх состояний, а не один общий «неактивна». Ссылки перенесены, **тройка
  происхождения контекста цела пополям**, источник архивирован, **имя и
  определение цели сравниваются до и после и не изменились**.
- **Блокировки — три двухсессионных входа, потому что защит три:**
  (1) два встречных слияния (`A→B` и `B→A`) доходят до конца без дедлока —
  держит порядок семей по `id`;
  (2) слияние против назначения на ту же семью не дают дедлока — держит
  **общий порядок «семья раньше контекста»**, и **снятие именно его обязано
  воспроизвести дедлок**, тогда как порядок по `id` здесь не помогает вовсе;
  (3) проигравший гонку **перечитывает статусы под блокировкой и отказывает**, а
  не выполняет обратное слияние поверх законченного — **снятие перечитывания
  обязано дать второе слияние**. Без всех трёх снятий тесты фиксируют
  совпадение, а не механизм.
- События: `context_family_assigned` (в том числе со снятием — `to_family_id`
  пуст), `family_archived`, `family_merged`, `kind_set`, `name_role_set`.

**Имена**
- Заводятся этой задачей: шесть констант `REFUSE_*`, `assign_family`, `set_unit`,
  `archive_family`, `merge_families`, `confirm_kind`, `unconfirm_kind`,
  `set_name_role`.
- Существуют, проверено `grep`-ом: `CatalogKind` (`models.py:175`),
  `with_for_update` (SQLAlchemy; режим сверяется компиляцией —
  `docs/pitfalls/db.md`). `MANUAL_KINDS` и `set_kind` (`services/review.py:39`,
  `:221`) в утверждениях этой задачи **не участвуют** — см. выше.

**Проверка**
- `just test-int-local-k "work_families or semantic_state"` — зелёная. ДО — то,
  что оставила задача 7 (её отчёт называет число); ПОСЛЕ — строго больше.
- `just test-int-local-k "family or families"` — зелёная. ДО **0** на `main`,
  ПОСЛЕ — строго больше нуля и не меньше узкой команды.
- `just lint-backend` — зелёная.

---

### Task 9: исходы Review — слияние контекстов, конфликт и `warnings`

**Files**
- Edit: `backend/services/review.py` (`merge_into_position` — **один шаг перед**
  `DELETE`; `set_kind` — перевод контекстов)
- Edit: `backend/routers/review.py` (ответ `POST /{to_review_id}/merge`)
- Edit: `backend/services/context_operations.py` (снятие конфликта — операция
  появляется здесь, потому что раньше задачи 9 конфликта не существует)
- Edit: `frontend/src/types/domain.ts:464-468` (`MergeResult`)
- Edit: `frontend/src/services/api/domain.ts:205-207` (`review.merge`)
- Edit: `frontend/src/services/queries.ts:602` (`useMergeReview` — **единственный
  владелец показа**, см. решение задачи ниже)
- Edit: `docs/reference/screens.md` (одна фраза в `## 2.`)
- Test: `backend/tests/integration/test_review_contexts.py`
- Test: `frontend/src/services/queries.test.tsx`
- **НЕ правятся:** `frontend/src/pages/review/ReviewPage.tsx` и
  `frontend/src/components/review/MergeTargetDialog.tsx` — обоснование ниже.

**Interfaces**
- Потребляет: `merge_into_position`, `set_kind`, `ReviewError`, `_require_kind`,
  `_lock_rows` (существуют); `get_or_create_bucket`, `lock_buckets`,
  `route_position` (задача 4); `record_event` (задача 2).
- Производит:

```python
# backend/services/review.py — расширение возвращаемого значения слияния:
@dataclass(frozen=True)
class MergeOutcome:
    moved_positions: int
    warnings: list[str]        # пустой в подавляющем большинстве слияний

def reconcile_contexts(
    db: Session, *, source_position_id: int, target_position_id: int
) -> list[str]

# backend/services/context_operations.py — снятие конфликта оператором:
def accept_target_decision(
    db: Session, *, position_item_ids: list[int], actor_id: int
) -> int
```

```typescript
// frontend/src/types/domain.ts — АДДИТИВНО, три прежних поля не тронуты:
export interface MergeResult {
  to_review_id: number;
  target: CatalogPositionRow & { normalized_job_title: string; kind: string };
  moved_positions: number;
  warnings: string[];
}
```

**Утверждения**
- **Порядок существен: шаг 3 встаёт ПЕРЕД `DELETE`**, шаги 1, 2 и 4 не правятся
  ни одним символом. Для каждой корзины исходной строки: найти или создать
  корзину цели с той же эффективной статьёй; перенести членства обычной
  маршрутизацией цели; **исходные контексты архивировать и перевесить**
  (`bucket_id`) в корзину цели; правила исходной корзины отбросить с записью в
  журнал; удалить опустевшие корзины.
- **Внутри шага 3 порядок тоже существен: сперва архивировать, потом
  перевешивать** — перевешенный действующий контекст столкнулся бы с
  действующим контекстом по умолчанию корзины цели по частичному индексу.
- **Слияние строки с двумя корзинами в цель с одной**: членства в корзинах цели,
  вторая корзина цели создана, исходные контексты архивны и **висят на корзинах
  цели**, их журнал читается после слияния, исходные корзины удалены, `DELETE`
  прошёл.
- **Негативная проверка снятием, и снимать надо каждое звено по отдельности:**
  без переноса членств `DELETE` падает на `RESTRICT` **членства**, без
  перевешивания контекстов — на `RESTRICT` **контекста**, без удаления корзин —
  на `RESTRICT` **корзины**. Три разных падения на трёх разных ограничениях;
  один прогон «упало» доказал бы, что держит какое-то звено, а не что держат
  все три.
- **Конфликт назначенных семей** → `conflict_at` и `conflict_from_context_id` на
  перенесённых членствах плюс **непустой `warnings` в ответе, называющий обе
  семьи**; совпадающие решения дают пустой `warnings` и пустой `conflict_at`
  (вторая сторона). **Отдельным входом — конфликт подтверждённых видов без
  семей**: правило конфликта названо через ИЛИ, и вход на одну его половину
  оставил бы вторую непроверенной.
- **`membership_state` перенесённых членств остаётся `CURRENT`** — отдельное
  утверждение, а не следствие. Конфликтное `STALE` сделало бы хранимое значение
  неравным вычисляемому (ломая сверку задачи 10) и заставило бы экран
  предлагать перенос в корзину, где позиция уже лежит.
- **Существующие `test_review*.py` зелёные без правки утверждений о трёх
  прежних полях** — отдельная строка приёмки, а не подразумеваемое: добавка
  `warnings` аддитивна, и доказать это можно только прогоном прежних
  утверждений. Ни одно прежнее поле не переименовано, не убрано и не поменяло
  тип. Остальные ответы Review (`kind`, батч, очередь) не тронуты.
- **Переходы через `set_kind` утверждаются здесь целиком — это единственная
  задача, правящая `services/review.py`** (в задаче 8 их нет намеренно):
  - `set_kind(HEADER)` и `set_kind(TRASH)` → контексты строки в
    `NOT_APPLICABLE`, **порознь двумя входами**, а не одним на «HEADER или
    TRASH»; **членства целы** — позиции никуда не делись;
  - `set_kind(POSITION)` → **ни одно поле контекста не изменилось**,
    проверяется снимком **всех** полей до и после, а не отсутствием ошибки;
  - `set_kind(LOT_HEADER)` отвергается сегодняшним `MANUAL_KINDS` раньше, чем
    дойдёт до контекста, — и это же утверждение сверяет, что список не
    разошёлся с литералом роутера (существующий тест на расхождение продолжает
    работать без правок);
  - **`NOT_APPLICABLE` терминально:** после `set_kind(HEADER)` повторный
    `set_kind` отказывает, потому что строка уже не `TO_REVIEW`. Утверждение о
    достижимости, а не об отсутствии кода обратного перехода.
- **«Принять решение цели» снимает `conflict_at` и `conflict_from_context_id`
  вместе** — обе колонки, иначе `CHECK` равносильности отвергнет строку;
  `membership_state` при этом не трогается, и `context_id` не меняется:
  позиция уже в правильной корзине, и второе действие («перенести в другой
  контекст») — это операция переноса задачи 6, а не эта.
- Корзины источника и цели берутся `FOR UPDATE` **по возрастанию `id`**.
- **Фронт: `warnings` показывает `useMergeReview` в `queries.ts` тостом
  `toast.warning`, и ни `ReviewPage`, ни `MergeTargetDialog` не правятся.**
  Выбор между двумя путями сделан фактом, а не вкусом: результат слияния
  получает `useMergeReview` внутри `MergeTargetDialog`
  (`frontend/src/components/review/MergeTargetDialog.tsx:63`), а `ReviewPage`
  его не видит вовсе — он только открывает диалог. Показывать предупреждение
  **внутри диалога нельзя**: диалог закрывается сразу после успеха
  (`onOpenChange(false)` следующей строкой за `mutateAsync`), и предупреждение
  оказалось бы в компоненте, который тут же размонтируется, — то есть было бы
  не показано никому. Тост — уже действующее соглашение проекта: `queries.ts`
  держит **21** вызов `toast.success` в `onSuccess` мутаций и `toastApiError` в
  `onError` (строка 134), причём **один из них — сам `useMergeReview`**
  (строка 609). То есть задача не заводит нового механизма показа, а дописывает
  второй вид тоста туда, где первый уже живёт.
- **Утверждения о тостах различают ВИД тоста, а не его наличие.** `onSuccess`
  у `useMergeReview` уже вызывает `toast.success` о выполненном слиянии
  (`frontend/src/services/queries.ts:609`), то есть тост есть **всегда**, и
  «тоста нет» было бы ложным утверждением при любом входе. Три утверждения,
  и все три — о `toast.warning` либо о кратности `toast.success`:
  - непустой `warnings` вызывает `toast.warning` с предупреждениями;
  - пустой `warnings` не вызывает **ни одного** `toast.warning`;
  - существующий `toast.success` остаётся ровно **один** в обоих случаях — он
    не заменяется предупреждением и не дублируется им.
  Третье утверждение отдельное, а не подразумеваемое: показ предупреждения,
  проглотивший сообщение об успехе, оставил бы оператора без подтверждения, что
  слияние вообще произошло.
- **Текст тоста называет оба разошедшихся решения**, а не сообщает о факте
  расхождения: `member`, выполняющий слияние, экрана семей не видит вовсе (он
  под `RequireAdmin`), и это единственное место, где он вообще узнаёт о
  конфликте.
- `docs/reference/screens.md ## 2.` получил фразу о том, что слияние сводит
  контексты и предупреждает о конфликте; `§N` в ней квалифицирован.

**Имена**
- Заводятся этой задачей: `MergeOutcome`, `reconcile_contexts`,
  `accept_target_decision`, поле `warnings` в ответе
  `POST /api/v1/review/{id}/merge` и в `MergeResult`.
- Существуют, проверено `grep`-ом: `merge_into_position`
  (`services/review.py:154`, 22 вхождения), `set_kind`
  (`services/review.py:221`, 28 вхождений), `MANUAL_KINDS`
  (`services/review.py:39`), `_write_manual_cache` (`services/review.py:110`),
  `MergeResult` (`frontend/src/types/domain.ts:464`), `useMergeReview`
  (`frontend/src/services/queries.ts:602`), `review.merge`
  (`frontend/src/services/api/domain.ts:205`), `MergeTargetDialog`
  (`frontend/src/components/review/MergeTargetDialog.tsx:63` — **потребитель
  ответа, но не правится**), `toast` / `toastApiError`
  (`frontend/src/services/queries.ts:2`, `:134`), `RequireAdmin`
  (`frontend/src/App.tsx:54`).

**Проверка**
- `just test-int-local-k review_contexts` — зелёная. ДО **0**, ПОСЛЕ — все входы
  задачи, включая три отдельных снятия звеньев `RESTRICT`.
- `just test-int-local-k review` — зелёная. ДО **201**, ПОСЛЕ — строго больше, и
  **ни один из прежних 201 не переписан**.
- `just test-frontend-file src/services/queries.test.tsx` — зелёная. Это
  **узкая команда задачи на фронте**: единственный правящийся фронтовый файл —
  `queries.ts`, и его тест живёт здесь, а не в `ReviewPage.test.tsx`. Выбор
  растёт на три входа: «непустой `warnings` → `toast.warning`», «пустой
  `warnings` → ни одного `toast.warning`» и «`toast.success` ровно один в обоих
  случаях».
- `just ci-frontend` — зелёная (eslint + tsc + vitest); общий выбор 1 038 не
  уменьшается, а фронтовый выбор по `review` (**19** из 1 038) остаётся **тем
  же**: `ReviewPage.test.tsx` задача не трогает, и совпадение здесь
  **ожидаемо**, а не сигнал.
- `just lint-backend` — зелёная.

---

### Task 10: каскадные события — ручной разнос, `STALE`, предложение переноса

**Files**
- Edit: `backend/services/category_override.py` (`apply_overrides`)
- Edit: `backend/services/round_category_override.py` (`_recompute`)
- Edit: `backend/services/context_operations.py` (предложение и его принятие)
- Create: `backend/tests/data/manual_overrides_18.json` (решение плана 1)
- Test: `backend/tests/integration/test_context_cascade.py`

**Interfaces**
- Потребляет: `apply_overrides` (`services/category_override.py:191`),
  `_recompute` (`services/round_category_override.py:118`),
  `effective_category_id`, `route_position` (задача 4); `record_event`
  (задача 2).
- Производит:

```python
@dataclass(frozen=True)
class TransferProposal:
    position_item_id: int
    current_context_id: int
    proposed_bucket_id: int
    proposed_context_id: int
    effective_category_id: int | None

@dataclass(frozen=True)
class RefreshReport:
    marked_stale: int       # CURRENT → STALE
    marked_current: int     # STALE  → CURRENT
    unchanged: int

def refresh_membership_states(
    db: Session, *, position_item_ids: list[int]
) -> RefreshReport
    """Приводит хранимое membership_state к вычисляемому — В ОБЕ СТОРОНЫ."""

def transfer_proposal(db: Session, *, position_item_id: int) -> TransferProposal | None
def accept_transfer(
    db: Session, *, position_item_id: int, expected_category_id: int | None,
    actor_id: int,
) -> int
def stale_mismatch_report(db: Session) -> list[int]
    """Сверка DoD 12 НЕЗАВИСИМЫМ запросом — не через effective_category_id."""
```

**Утверждения**
- **Ручной разнос помечает `STALE` только затронутые членства; незатронутые
  членства того же контекста остаются `CURRENT`** — обе стороны в одном входе,
  иначе утверждение половинчато: ось на контексте покрасила бы правых вместе с
  виноватым, и тест, смотрящий только на виноватого, этого бы не увидел.
- **Приведение идёт В ОБЕ СТОРОНЫ, и это требование инварианта, а не удобство.**
  `membership_state` объявлен полностью **вычислимым**, и хранимое обязано
  равняться вычисляемому **всегда** (спека §2.5, DoD 12) — а не только после
  ухудшения. Разнос обратим штатным путём: `clear_override`
  (`services/category_override.py:172`) возвращает статью к файловой, и если та
  совпала со статьёй корзины, членство обязано снова стать `CURRENT`.
  Односторонний `mark_stale` оставил бы `STALE` навсегда, сверка DoD 12
  покраснела бы, а экран предлагал бы перенос в корзину, где позиция уже лежит.
  **Вход — цикл `CURRENT → STALE → CURRENT` на одном членстве**, с проверкой
  поля на каждом из трёх шагов: вход, останавливающийся на `STALE`, доказывает
  половину правила (`docs/insights/state-the-rule-as-an-equivalence.md`).
- **`RefreshReport` считает три исхода порознь** — `marked_stale`,
  `marked_current`, `unchanged`. Один счётчик «затронуто» не отличил бы
  приведение в `CURRENT` от приведения в `STALE`, а именно это различение и
  проверяет вход выше.
- **Приведение идемпотентно:** повторный вызов на неизменных данных даёт
  `marked_stale = 0`, `marked_current = 0` — иначе журнал заполнялся бы
  событиями о несуществующих изменениях.
- Событие `members_marked_stale` с `count` и `trigger = 'category_override'`
  пишется, **когда `marked_stale > 0`**; нулевое приведение события не пишет.
- **Предложение переноса вычисляется, а не хранится** — седьмой сущности не
  заводится; повторный вызов после смены статьи даёт **другое** предложение без
  единой записи в базу.
- **Принятие под блокировкой отказывает, если эффективная статья успела
  измениться**, и возвращает новое предложение, **ничего не перенеся**. Вторая
  половина: при неизменной статье перенос проходит. Отказ без положительного
  входа доказал бы «отказывает всегда».
- **Хранимое `membership_state` равно вычисляемому предикату на всём стенде.**
  Сверка (`stale_mismatch_report`) считает эффективную статью **своим запросом**
  `position_items → chapter_item_id → work_category_id` против
  `context_buckets.work_category_id`, а не вызовом
  `effective_category_id` — сверщик, делящий предикат с генератором, доказывает
  согласованность, а не правильность. Отдельным входом сверяется, что оба
  написания дают один ответ на всём каталоге стенда.
- **Равенство остаётся верным после слияния в Review с конфликтом:** конфликт
  живёт на своей оси и в `membership_state` не пишет (задача 9).
- **`replace` проходит целиком — главный вход, ради которого требование дизайна
  отменено.** До и после замены сверяются `id` контекстов, их `is_default`,
  набор правил и `archived_at` — **всё то же**; членства восстановлены **в те же
  контексты попозиционно**. Отдельным утверждением: **архивных контекстов после
  замены ноль**.
- **Негативная сторона того же входа: включите архивирование по опустошению — и
  `replace` обязан упасть доменной ошибкой маршрутизации.** Без этого прогона
  §2.8 спеки остаётся рассуждением, а отмена решения гейта 1 — ничем не
  подтверждённой.
- **Разделённая корзина переживает замену:** два контекста и правило между ними;
  после замены позиции раскладываются **по тем же двум контекстам**, а не
  схлопываются в один. Это тот дефект, которым ловится отвергнутый вариант «не
  архивировать только последний контекст по умолчанию».
- Удаление договора, раунда, тендера и участника — членства ушли, **контексты
  действующие и пустые**.
- **Повторная загрузка того же файла кладёт позиции в тот же контекст с тем же
  `id`**, а не заводит новый: ровно то обещание, ради которого контекст заведён.
- **18 ручных разносов стенда учтены как эффективная статья с источником
  `manual`:** их позиции лежат в корзинах **целевых** статей, а не исходных.
  Число 18 — утверждение: столько разносов подтверждено пользователем как
  намеренные (спека §1.7).
- **Контракт `manual_overrides_18.json` проверяется тем же БЕЛЫМ СПИСКОМ, что и
  у второй фикстуры** (решение плана 1): `row.keys()` каждой из 18 записей
  сверяется с множеством допустимых ключей в обе стороны, и посторонний ключ
  обязан покрасить проверку — свой негативный вход, как у задачи 3. Файл
  собран **заново как минимальная выжимка** — название раздела и адрес целевой
  статьи, — а не копированием `manual_overrides_snapshot.json`, который несёт
  стендовые идентификаторы и поля, контрактом не допущенные.

**Имена**
- Заводятся этой задачей: `TransferProposal`, `RefreshReport`,
  `refresh_membership_states`, `transfer_proposal`, `accept_transfer`,
  `stale_mismatch_report`.
- Существуют, проверено `grep`-ом: `apply_overrides`
  (`services/category_override.py:191`), `clear_override`
  (`services/category_override.py:172`), `lock_estimate`
  (`services/category_override.py:67`), `_recompute`
  (`services/round_category_override.py:118`), `ApplyResult`
  (`services/category_override.py:51`), `_replace_existing`
  (`services/estimate_import.py:687`).

**Проверка**
- `just test-int-local-k context_cascade` — зелёная. ДО **0**, ПОСЛЕ — все входы
  задачи, включая оба снятия (автоархивирование и общий предикат сверки).
- `just test-int-local-k category_override` — зелёная. ДО **83**, ПОСЛЕ — строго
  больше нуля прироста: задача правит `apply_overrides` и обязана завести здесь
  свои утверждения; ни один из 83 не переписан.
- `just lint-backend` — зелёная.

---

### Task 11: разовый проход по существующему каталогу

**Files**
- Create: `backend/services/catalog_backfill.py`
- Edit: `backend/cli.py` (подкоманда `backfill-contexts`)
- Test: `backend/tests/integration/test_catalog_backfill.py`

**Interfaces**
- Потребляет: `route_position`, `get_or_create_bucket`, `RoutingOutcome`
  (задача 4); `classify_kind`, `classify_name_role`,
  `PLACE_DICTIONARY_VERSION` (задача 3); `_guard` (`backend/cli.py`).
- Производит:

```python
@dataclass(frozen=True)
class BackfillReport:
    buckets_created: int
    contexts_created: int
    members_created: int
    rows_without_bucket: int          # строки-сироты без позиций
    by_name_role: dict[str, int]
    by_semantic_kind: dict[str, int]
    fields_changed: int               # ноль на повторном запуске

def run_backfill(
    db: Session, *, batch_size: int = 500, progress: Callable[[int], None] | None = None
) -> BackfillReport
```

**Утверждения**
- **Идемпотентность:** второй запуск на неизменных данных даёт **нулевой отчёт по
  созданию и нулевой по правкам** — `fields_changed = 0`. Проверяется
  сравнением **пополям**, а не отсутствием исключения.
- **Ручные переопределения не переписываются:** контекст с
  `semantic_kind_source = 'manual'`, `name_role_source = 'manual'` и членство с
  `routed_by = 'manual'` сравниваются пополям до и после — все три, потому что
  правило «ручное сильнее» записано о трёх разных полях. Входы строятся
  **тестом**, не стендом: на стенде то же правило проверяется задачей 13, где
  ручные решения появляются штатным путём.
- **Проход повторяем после смены версии словаря:** инкремент
  `PLACE_DICTIONARY_VERSION` делает контексты прежней версии **находимыми
  запросом**, и повторный проход адресуется им, а не всему каталогу; контексты
  с `name_role_source = 'manual'` пересчёт **не трогает** — проверяется
  сравнением полей до и после, а не отсутствием исключения.
- **Проход — команда, а не тело миграции**, и все три причины проверяемы:
  объём (работа идёт партиями, каждая коммитится), зависимость от кода
  приложения (вызывает `classify_name_role`), повторяемость (выше).
- **Прогон на стенде даёт 6 613 контекстов на 5 305 строк, 100 335 членств и
  665 строк без корзины.** Числа утверждают: они замерены тем же правилом
  «написание × эффективная статья», и расхождение с ними есть **сигнал, а не
  допуск**. Результат прогона записывается в devlog (задача 15).
- **На стенде 22 корзины «Прочее» несут 6 781 позицию — 7 % от 100 335.** Число
  утверждает: семь процентов — доля, при которой «Прочее» не становится
  свалкой, и именно она была доводом «за» ключ корзины (спека §1.8). Отчёт
  прохода печатает её отдельной строкой.
- **Транзакционная граница названа: коммит на КАЖДУЮ завершённую партию**
  (решение плана 12). Проверяется входом «прерывание после первой партии»:
  сделанное **пережило** прерывание, префикс согласован (у каждой обработанной
  позиции есть корзина, контекст и членство — частично обработанных не бывает),
  а повторный запуск дописывает остаток **и не дублирует префикс**. Вход на
  прерывание обязателен: без него «прогресс с прерыванием» — обещание, которое
  одна транзакция молча отменяет откатом.
- **Ручные переопределения стенда эта задача НЕ создаёт и не проверяет.**
  Штатного пути оператора на конец задачи 11 не существует — ни API (задача
  12), ни экрана (задача 13), — и «переопределить три исключения» здесь
  означало бы править базу руками в обход операции, то есть проверять не тот
  механизм, который сдаётся. Три исключения, подтверждение систем стенда и
  повторный проход поверх них стоят в задаче 13 (DoD 6).
- **Порядок запуска — миграция → seed → проход** (спека §2.9). Проход от seed не
  зависит (семьи контекстам в фиче 1 автоматически не назначаются), но обратный
  порядок запутал бы отчёты.
- Партия больше пяти: проход проверяется не одной строкой, а партией —
  `prepare_threshold` psycopg просыпается на шестом исполнении
  (`docs/insights/batch-larger-than-five.md`).

**Имена**
- Заводятся этой задачей: `BackfillReport`, `run_backfill`, подкоманда
  `backfill-contexts`.
- Существуют, проверено `grep`-ом: `_guard` (`backend/cli.py:23`),
  `SessionLocal` (`backend/database.py`), `route_position` (задача 4).

**Проверка**
- `just test-int-local-k catalog_backfill` — зелёная. ДО **0**, ПОСЛЕ — все
  входы задачи.
- `just test-int-local-k catalog` — зелёная. ДО **26**, ПОСЛЕ — строго больше:
  имя нового файла содержит `catalog`.
- Прогон на стенде `gca_dev`: `python -m cli backfill-contexts` дважды;
  первый — четыре названных числа, второй — нулевой отчёт.
- `just lint-backend` — зелёная.

---

### Task 12: API семантического контура

**Files**
- Create: `backend/crud/semantic.py`
- Create: `backend/routers/semantic.py`
- Edit: `backend/main.py` (подключение роутера)
- Test: `backend/tests/integration/test_semantic_api.py`

**Interfaces**
- Потребляет: всё, что произвели задачи 6, 7, 8, 9, 10; `raise_domain_error`
  (`backend/routers/domain_errors.py:36`), `require_admin`
  (`backend/auth.py:72`), `get_current_user` (там же).
- Производит — маршруты под `/api/v1/semantic`, все под правом `admin`:

```
GET    /api/v1/semantic/families
POST   /api/v1/semantic/families
PATCH  /api/v1/semantic/families/{family_id}
POST   /api/v1/semantic/families/{family_id}/activate
POST   /api/v1/semantic/families/{family_id}/archive
POST   /api/v1/semantic/families/{family_id}/merge
GET    /api/v1/semantic/contexts
GET    /api/v1/semantic/contexts/{context_id}
POST   /api/v1/semantic/contexts/{context_id}/kind
POST   /api/v1/semantic/contexts/{context_id}/name-role
POST   /api/v1/semantic/contexts/{context_id}/family
POST   /api/v1/semantic/contexts/{context_id}/split
POST   /api/v1/semantic/contexts/{context_id}/merge
POST   /api/v1/semantic/contexts/{context_id}/archive
POST   /api/v1/semantic/members/move
GET    /api/v1/semantic/members/{position_item_id}/transfer-proposal
POST   /api/v1/semantic/members/{position_item_id}/transfer
POST   /api/v1/semantic/members/accept-target-decision
```

```python
# backend/crud/semantic.py — чтение для экрана
@dataclass(frozen=True)
class ContextFilters:
    catalog_query: str | None
    work_category_id: int | None
    semantic_kind: str | None
    name_role: str | None
    semantic_state: str | None
    has_stale_members: bool | None
    has_conflicting_members: bool | None
    has_no_members: bool | None

def list_families(db: Session, *, status: str | None, unit_id: int | None) -> list[dict]
def list_contexts(db: Session, *, filters: ContextFilters, limit: int, offset: int) -> dict
def context_card(db: Session, *, context_id: int) -> dict
```

**Утверждения**
- **Все восемнадцать маршрутов требуют `admin`:** `member` получает отказ на
  **каждом**, перебором по списку маршрутов, а не на одном примере. Прав
  `member` на семьи и контексты в фиче 1 нет вовсе.
- **Набор маршрутов приложения проверяется ЦЕЛИКОМ, и здесь — единственное
  место, где такое утверждение осмысленно** (в задаче 6 семантического роутера
  ещё нет, и проверка была бы зелена тривиально). Множество путей под
  `/api/v1/semantic`, собранное из `app.routes`, равно восемнадцати, названным
  выше; **маршрута восстановления архивного контекста (`…/restore`) в нём
  нет** — утверждение о собранном наборе, а не о ненахождении слова в
  исходнике. Отсутствие действия в интерфейсе проверяет задача 13 — это
  отдельный носитель и отдельное утверждение.
- **Три фильтра-признака — три разные оси и три разных запроса:** «есть
  устаревшие членства», «есть конфликтные членства», «нет членств». Вход,
  у которого одновременно и `STALE`, и `conflict_at`, обязан попасть в **оба**
  первых фильтра: оси независимы и складываются, и один фильтр на два факта
  потерял бы этот случай.
- **Отказы приходят кодом и контекстом**, а не только текстом: у отказа о
  несовпадении единиц в контексте **оба** значения, у отказов архивирования —
  **число**, у отказа маршрутизации — корзина. Проверяется чтением контекста
  ответа, а не подстрокой сообщения.
- Список семей несёт у каждой **число привязанных контекстов** — то самое, на
  которое ссылаются отказы правки единицы и архивирования.
- Карточка контекста отдаёт написание, статью и **её источник** (`file`/
  `manual`), вид с источником, роль имени с источником и **версией словаря**,
  `comparability_reason`, семью с источником назначения, число членств и
  журнал.
- Число запросов на список не зависит от числа строк выдачи: чтение идёт
  ограниченным набором запросов, а не запросом на строку.

**Имена**
- Заводятся этой задачей: `ContextFilters`, `list_families`, `list_contexts`,
  `context_card`, восемнадцать маршрутов выше.
- Существуют, проверено `grep`-ом: `raise_domain_error`
  (`backend/routers/domain_errors.py:36`), `require_admin`
  (`backend/auth.py:72`), `DecimalJSONResponse` (`backend/responses.py`),
  роутеры-образцы `backend/routers/review.py`, `backend/routers/tenders.py`.

**Проверка**
- `just test-int-local-k semantic_api` — зелёная. ДО **0**, ПОСЛЕ — не меньше
  восемнадцати входов на право плюс входы фильтров и отказов.
- `just test-int-local-k semantic` — зелёная; ПОСЛЕ строго больше, чем оставила
  задача 8.
- `just lint-backend` — зелёная.

---

### Task 13: экран `/families`

**Files**
- Create: `frontend/src/pages/families/FamiliesPage.tsx`,
  `FamiliesTab.tsx`, `ContextsTab.tsx`, `ContextCard.tsx`
- Create: `frontend/src/pages/families/*.test.tsx`
- Edit: `frontend/src/App.tsx` (маршрут под `RequireAdmin`),
  `frontend/src/services/api/domain.ts`, `frontend/src/services/queries.ts`,
  `frontend/src/services/queryKeys.ts`, `frontend/src/types/domain.ts`

**Interfaces**
- Потребляет: маршруты задачи 12; `RequireAdmin` (`frontend/src/App.tsx:54`);
  примитивы `shadcn/ui` (**кастомных компонентов не заводится** — ставятся
  через `npx shadcn add`).
- Производит: маршрут `/families`, три области экрана (Семьи, Контексты,
  Операции), типы ответов в `frontend/src/types/domain.ts`.

**Утверждения**
- **Право:** `member` на `/families` получает редирект (`RequireAdmin`) — тем же
  входом, которым он проверен у `/standards`.
- **Три области присутствуют**, и каждая проверяется своим состоянием: пустая
  очередь контекстов; контекст с конфликтом решений; контекст с устаревшим
  членством; контекст с `insufficient_description`.
- **Подписи причин проверяются ТЕКСТОМ, а не наличием узла:** пустая подпись и
  подпись «состав не описан — сравнение ставок не производится» — разные факты,
  и проверка на существование элемента прошла бы при пустой подписи.
- **«Нет семьи» и «семья не назначена, потому что состав не описан» — две разные
  подписи** и два разных входа.
- **Два признака — два фильтра**, потому что это две оси: «есть устаревшие
  членства» и «есть конфликтные членства»; плюс третий — «нет членств» (пустые
  контексты после замены сметы, их архивирует оператор, а не автоматика).
- **Два признака — и два РАЗНЫХ действия:** у устаревшего членства —
  «принять предложение переноса», у конфликтного — «принять решение цели»
  (снимает `conflict_at`) либо «перенести в другой контекст». Действие переноса
  по устаревшей статье конфликтному членству **не предлагается**: оно
  предложило бы перенос туда, где позиция уже лежит.
- **После seed экран показывает 42 черновика — штатное первое состояние**, и
  путь «дописать определение → активировать» проходит здесь. Число 42 — то же
  утверждение, что в задаче 7.
- **Кнопка активации недоступна без определения** — то же правило, что держит
  `CHECK`; недоступность проверяется состоянием кнопки, а не отсутствием
  запроса.
- **Правка единицы недоступна, пока привязки есть, и подпись называет их
  число.**
- **Восстановления архивного контекста на экране нет** — утверждение о наборе
  действий карточки.
- Снимки в **обеих темах** для каждого состояния взаимодействия.
- **Стендовые действия оператора закрываются здесь, потому что здесь впервые
  появляется штатный путь** (в задаче 11 его не было — ни API, ни экрана):
  - **три известных исключения правила вида переопределены через экран** и
    несут `semantic_kind_source = 'manual'` с автором и временем; **системы
    стенда подтверждены оператором** (DoD 6, спека §1.14). Правило дало 98 из
    101 — оставшиеся три и есть работа человека, и делает он её тем же
    интерфейсом, который сдаётся;
  - **повторный `backfill-contexts` ПОВЕРХ этих решений не переписывает ни
    одного** — то же правило «ручное сильнее», но проверенное на решениях,
    принятых операцией, а не расставленных тестом. Это и есть замер, которого
    задача 11 дать не могла.

**Имена**
- Заводятся этой задачей: `FamiliesPage`, `FamiliesTab`, `ContextsTab`,
  `ContextCard`, ключи запросов семейств и контекстов в `queryKeys.ts`.
- Существуют, проверено `grep`-ом: `RequireAdmin` (`frontend/src/App.tsx:54`),
  `RequireAdmin.test.tsx` (`frontend/src/pages/admin/`), `StandardsPage`
  (`frontend/src/pages/standards/StandardsPage.tsx` — образец экрана под
  `admin`).

**Проверка**
- `just test-frontend-file src/pages/families` — зелёная. ДО **0** (на `main`
  слово `families` не выбирает ни одного из 1 038 тестов), ПОСЛЕ — все входы
  задачи.
- `just ci-frontend` — зелёная (eslint + tsc + vitest); общий выбор 1 038 не
  уменьшается.
- **Прогон на стенде `gca_dev`** (второй прогон фичи; первый — задача 11):
  экраном переопределены три исключения и подтверждены системы; затем
  `python -m cli backfill-contexts` — отчёт называет **ноль** переписанных
  ручных решений, и поля трёх контекстов сравниваются пополям до и после.
  Результат записывается в devlog (задача 15).

---

### Task 14: ревизия `AGENTS.md` §3, §5, §7 — один коммит

**Files**
- Edit: `AGENTS.md` (§3, §5, §7 и преамбула)
- Edit: `docs/AGENTS-revisions.md` (действующая врезка уезжает в архив)
- Edit: `docs/reference/screens.md` (раздел `## 9.`)
- Edit: `backend/scripts/check_agents_index.py` (`EXPECTED_SCREEN_ANCHORS`)
- Edit: `docs/product-roadmap.md` (пункт А1)

**Interfaces**
- Потребляет: `EXPECTED_SCREEN_ANCHORS`
  (`backend/scripts/check_agents_index.py:405`, восемь троек сегодня).
- Производит: девятую тройку `("9", "<название экрана>", "#9-<якорь>")`,
  девятый пункт §7, раздел `## 9.` в `screens.md`, новую врезку преамбулы.

**Утверждения**
- **Три места правятся ОДНИМ коммитом** — §7 `AGENTS.md`, `## 9.` в
  `screens.md`, `EXPECTED_SCREEN_ANCHORS` в страже. Разрыв между ними страж
  показывает красным, и это проверяется **прогоном**: девятый пункт §7 **без**
  соответствующего `## 9.` обязан покрасить проверку 13. Утверждение
  предъявляется снятием, а не объявляется.
- **Число проверок стража не растёт.** Девятый экран правит не список проверок,
  а **ожидание** проверки 13: `EXPECTED_SCREEN_ANCHORS` и множество заголовков
  `{1..8}` → `{1..9}`. После коммита `just check-agents-index` зелёный целиком.
- **§3 записан с различением времён** и обязан пережить собственный мерж:
  нормализованная пара — идентичность **входного написания** и ключ каскада, и
  она же остаётся техническим носителем матрицы и нормативов до фичи вариантов;
  идентичностью **работы** она с этой ревизии **не называется**. `work_variant`
  и `work_family` упоминаются как **принятое направление, а не как факт**, и
  таблица `work_variant` этой фичей не заводится.
- **§5 дополняется ортогонально:** к существующим действиям Review добавляется
  то, что они делают с контекстами, и называются две новые оси — `semantic_state`
  контекста и `membership_state` членства. **Смысл `TO_REVIEW` не
  переопределяется** ни одним символом: «ждёт решения оператора» живёт до фичи
  промоушена.
- **§4 не правится ни одним символом**; §6, §8 и §10 — тоже.
- Врезка v6.23 уезжает в `docs/AGENTS-revisions.md` целиком, на её место встаёт
  новая; проверки «единственность действующей ревизии», «разрешение каждого
  упоминания `v6.N`» и «целостность локальных ссылок архива» выполняются тем же
  переносом.
- **Пункт А1 карты переписан и НЕ закрыт:** автопромоушен стоит на семьях и
  контекстах и разложен на четыре фичи, из которых первая — эта; открытый
  вопрос «смысл `TO_REVIEW` — ревизия §5» **переезжает к фиче 3** и с А1
  снимается; ссылка на спеку встаёт в пункт. А1 закроется фичей 3.
- Все `§N` в правках `screens.md` квалифицированы, шапка «Когда читать:»
  осталась одна, ссылок-сирот и битых ссылок нет.

**Имена**
- Заводятся этой задачей: девятая тройка `EXPECTED_SCREEN_ANCHORS`, номер новой
  ревизии (**в плане не называется** — Global Constraints).
- Существуют, проверено `grep`-ом: `EXPECTED_SCREEN_ANCHORS`
  (`backend/scripts/check_agents_index.py:405`), `check_agents_index.py`
  (`justfile`, рецепт `check-agents-index`), `docs/AGENTS-revisions.md`.

**Проверка**
- `just check-agents-index` — зелёная, **все проверки**. Это **первая** задача
  плана, которая обещает зелёного стража; до неё он не обещается
  (`docs/pitfalls/process.md`).
- `just test-unit-k check_agents_index` — зелёная. ДО **20** (замер
  22.09.2026: `20/1199 tests collected` в `tests/unit`), ПОСЛЕ — **не меньше
  21**: вход «девятый пункт §7 без соответствующего `## 9.` в `screens.md`
  обязан покрасить проверку 13» заводится здесь и обязан попасть в этот же
  выбор.
- `just lint-backend` — зелёная.

---

### Task 15: финал — devlog, PR

**Files**
- Create: `docs/devlog/2026-09-22-catalog-families.md`

**Interfaces**
- Потребляет: отчёты всех задач и результаты **двух** прогонов на стенде —
  разового прохода (задача 11, раздел «Проверка») и операторского прогона с
  повторным проходом поверх ручных решений (задача 13, раздел «Проверка»).
- Производит: devlog и описание PR со ссылками на спеку и план.

**Утверждения**
- Devlog называет: что сделано, **замеры** (четыре числа прохода на стенде,
  доля корзин «Прочее», число тестов до и после фичи, отклонения от ожидаемых
  чисел, если они были), **отступления от плана** поимённо, найденные грабли.
- Devlog называет **оба** прогона на стенде порознь — разовый проход и
  операторский с повторным проходом поверх ручных решений: второй доказывает
  то, чего первый доказать не мог (ручное решение, принятое операцией, а не
  расставленное тестом).
- Devlog **называет тронутые области и прочитанные по ним файлы
  `docs/pitfalls/`** (`AGENTS.md` §10): по составу фичи это `db.md` (миграция,
  индексы, блокировки), `backend.md` (домен и схемы), `runs.md` (прогоны),
  `frontend.md` (экран), `process.md` (страж посреди фичи-ревизии). Механизма
  здесь нет и быть не может — пункт проверяется чтением на ревью.
- **`just ci` зелёный целиком перед пушем** (около 8,5 минут). Правки не только
  в `docs/` — значит `just ci`, а не `just check-agents-index`.
- **Ограничение фичи названо вслух и в devlog:** правило «написание ×
  эффективная статья» принято по выборке **85 из 524** контекстов, размеченной
  не сметчиком; ни один тест не доказывает, что в остальных **439** ложной
  склейки нет. Фича даёт **механизм** развести её, когда она найдётся, — и это
  всё, что она обещает.
- **Активация 42 семей в приёмку не входит** — это шаг пользователя и
  предпосылка фичи 2; devlog фиксирует 42 черновика, а не 42 активные семьи.
- Если фича выстрадала правило работы — инсайт в `docs/insights/` и строка в
  указателе `AGENTS.md` §12. Не выстрадала — так и написать.

**Имена**
- Заводятся этой задачей: `docs/devlog/2026-09-22-catalog-families.md`.
- Существуют, проверено `grep`-ом: `docs/pitfalls/db.md`, `backend.md`,
  `runs.md`, `frontend.md`, `process.md` (маршрут в `AGENTS.md` §11).

**Проверка**
- `just ci` — зелёная целиком.
- `just check-agents-index` — зелёная.

---

## Команды проверки

- **По задаче:** указаны в самой задаче — узкая (собственный файл задачи, ДО 0)
  и широкая по области (ДО замерено, ПОСЛЕ обязано вырасти). Сигналом служит
  широкая: если её выбор не вырос, новые классы в область не попали
  (`docs/process/implementation.md`, «Выбор команды проверки»).
- **Замер «ДО» снят на `main`-состоянии ветки** (`906a86a`, 22.09.2026):
  бэкенд собирает **2 947** тестов, фронтенд — **1 038**. Замеренные области:
  `schema` — 184, `review` — 201, `matching` — 60, `review or matching` — 247,
  `category_override` — 83, `estimate_import` — 90, `import_pipeline` — 25,
  `catalog` — 26, `context` — 9, `semantic` — 12, `family`/`families`/`routing`/
  `membership`/`backfill` — 0; на фронтенде `review` — 19, `families` — 0.
- **По фиче целиком:** `just ci` (`AGENTS.md` §9.3), плюс два прогона на стенде
  `gca_dev` — разовый проход (задача 11) и операторский прогон с повторным
  проходом поверх ручных решений (задача 13).
- **Локальный кластер:** `just pg-test-start`, порт 5459; ждать готовности
  (`pg_isready`), а не `pg_ctl status`; восстановление до 9 минут.
  `just test-backend-parallel 4` — `n=8` даёт «out of shared memory».
- **Кириллица в выводе дочернего Python** — `PYTHONIOENCODING=utf-8`.
