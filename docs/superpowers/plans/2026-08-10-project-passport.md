# Ф6: паспорт проекта по статьям классификатора — план

**Ветка:** `feat/project-passport` (уже существует, на `c2017ec`, не запушена).
**Спека:** [2026-08-10-project-passport-design.md](../specs/2026-08-10-project-passport-design.md) — **что** делается.
**Референс экрана:** [2026-08-09-project-passport-mockup.html](../specs/2026-08-09-project-passport-mockup.html) — утверждённая форма.
**Рамка фазы:** [phase7-frame.md](../../phase7-frame.md), строка Ф6 — последняя фича фазы 7.

План говорит **чем именно и в каком порядке**. Решения дизайна он исполняет, а не
пересматривает: семнадцать границ и пятнадцать отвергнутых альтернатив спеки —
принятые решения, на них здесь только ссылки.

---

## 0. База отсчёта — замерена в этой сессии, не пересказана

| Что | Значение | Чем замерено |
|---|---|---|
| ветка / HEAD | `feat/project-passport` / `c2017ec`, дерево чистое | `git status`, `git log` |
| backend собрано | **1337** | `uv run pytest --collect-only -q` |
| backend прогон | 1331 passed / 6 skipped (известные из `test_auth_coverage.py`) | база фазы |
| vitest | **206** в **20** файлах | `npx vitest run` из PowerShell |
| кластер | PG **16.14** на 5459, поднят | `psql select version()` |
| `gca_dev` / `gca_test` | обе на **0009** | `select version_num from alembic_version` |
| head миграций | `0009`, у Ф6 — **0010** | `ls alembic/versions/` |
| `PARSER_VERSION` | `3.1.0` | рамка фазы, devlog Ф4б |

**Пофайлово (замерено точечными прогонами, а не выведено):**

| Файл | Тестов |
|---|---|
| `PassportPage.test.tsx` | **16** |
| `SettingsPage.test.tsx` | **7** |
| `queries.test.tsx` | **1** |
| `MatrixPage.test.tsx` | **16** |
| `ReportsPage.test.tsx` | **9** |

**Браузерное окружение Ф5 живо — проверено в этой сессии, а не принято на веру.**
`playwright-core` в `%TEMP%\gca-pw` (вне репозитория), системный Chrome
(`channel: "chrome"`). Прогон `shoot.mjs` по утверждённому макету: `CONSOLE_ISSUES=0`,
`bodyOverflowX=false` во всех шести состояниях (светлая, тёмная, раскрытая,
состояния, печать, 720 px), `sheetW=1060`, `tableW=1058`. Макет открыт в браузере
и просмотрен.

---

## 1. Прогон новых правил по существующим входам — сделан ДО кода

Слой 2 [replaying-new-rules](../../insights/replaying-new-rules.md). Ф6 удаляет
экран и правит навигацию, то есть **судит уже написанное**. Четыре пункта
постановки проверены механически; результат ниже — замер, и **два пункта из
четырёх уточнены против ожидания**.

### 1.1. `handlers.ts` и `SettingsPage.test.tsx` — ожидание НЕПОЛНО

Ожидание: «`SettingsPage.test.tsx` опирается на `handlerState.passportTopN`, значит
хендлер и фикстуру трогать нельзя». Замер (`grep` по `src/`, чтение файла целиком):

| Факт | Замер |
|---|---|
| `handlerState.passportTopN` читают | только `handlers.ts` (настройки) и `SettingsPage.test.tsx` (3 утверждения) |
| `samplePassport` читают | `handlers.ts` и **только** `PassportPage.test.tsx` |
| `handlerState.passportWithoutEstimate` читают | только `PassportPage.test.tsx` |
| **`SettingsPage.test.tsx` импортирует `PassportPage`** | **да, строка 9** |

**Находка.** Второй блок `describe("Смена топ-N и открытый паспорт")` в
`SettingsPage.test.tsx` **рендерит старый `PassportPage`** на маршруте
`/contracts/:contractId/passport` и утверждает «Показаны 3 из 1100» / «Показаны 1
из 1100». Удаление `PassportPage.tsx` делает **весь файл** несобираемым — красный
`tsc` и красный vitest, а не только `PassportPage.test.tsx`.

**Следствия, обязательные к исполнению:**

1. Падение vitest от удаления — **17 тестов, а не 16**: 16 из `PassportPage.test.tsx`
   плюс 1 из второго `describe` в `SettingsPage.test.tsx`. Объявлено здесь заранее,
   чтобы на замере не читалось регрессией.
2. Шесть тестов первого `describe` в `SettingsPage.test.tsx` **обязаны остаться
   зелёными**: экран «Настройки» живёт (§2.13), и `handlerState.passportTopN`
   вместе с хендлерами `/api/v1/settings` **не трогается** — здесь ожидание
   постановки верно.
3. Второй `describe` **удаляется целиком** вместе со старым паспортом: он
   проверяет перерисовку экрана, которого больше нет. Это не ослабление —
   требования у него не остаётся.
4. Хендлер `GET /api/v1/analytics/passport/:contractId`, фикстура `samplePassport`
   и флаг `passportWithoutEstimate` после задачи 11 не читает никто. Спека §2.1
   предписывает удалить **типы старого ответа**, а фикстура типизирована `Passport` —
   значит цепочка «тип → фикстура → хендлер» снимается целиком. Мёртвая фикстура
   читалась бы как живое покрытие (verifying-guards, слой 7).

### 1.2. `queries.test.tsx` и корень `qk.passport` — ожидание ВЕРНО, но защита ОТСУТСТВУЕТ

Замер: `queries.test.tsx` (1 тест) утверждает `keys.toContain(qk.passport.all)`
среди пяти корней инвалидации `useUpdateObject`. Корень переиспользуется (§2.12),
тест **обязан остаться зелёным** — здесь ожидание верно.

**Находка.** Этот тест **не стережёт переиспользование**. Он утверждает, что
`qk.passport.all` лежит в списке инвалидации, а не что новый паспорт живёт под этим
корнем. Заведи Ф6 собственный корень `qk.projectPassport` — тест остался бы
зелёным, а обязательство 1, унаследованное от Ф5, было бы нарушено молча. Спека
§2.12 обещает «проверяется тестом на состав корней»; существующий тест этого не
делает.

**Следствие:** задача 6 заводит тест, который берёт ключ **из самого хука** и
утверждает, что `qk.passport.all` — его префикс. Снятие (смена корня в хуке) обязано
его валить.

### 1.3. `TopNav` теряет три пункта — защиты НЕТ ни одной

Замер: файла `TopNav.test.tsx` в репозитории **нет**; ни один тест не рендерит
`AppShell` или `TopNav` (`grep` по `--include="*.test.tsx"` — ноль вхождений);
единственное вхождение слова «Настройки» в тестах — это `describe("Настройки")`,
имя блока самого экрана, а не ссылка навигации.

**Следствие:** удаление трёх пунктов не уронит ни одного теста, то есть требование
§2.13 останется без исполнителя. Задача 11 заводит `TopNav.test.tsx`, который
утверждает **и** отсутствие трёх ссылок, **и** присутствие остальных (иначе тест
прошёл бы на пустой навигации).

### 1.4. Маршруты `/matrix` и `/reports` — ожидание ВЕРНО

Замер `App.tsx`: маршруты `/matrix`, `/reports`, `/settings` объявлены отдельно от
`NAV` в `TopNav.tsx`; правка массива `NAV` на них не влияет.
`MatrixPage.test.tsx` (16) и `ReportsPage.test.tsx` (9) рендерят страницы напрямую,
минуя `AppShell`. Бэкенд и компоненты не трогаются. Обе цифры обязаны остаться.

### 1.5. Что новое правило начнёт судить на бэкенде

- **`test_auth_coverage.py` параметризован списком роутов.** Новый `GET
  /api/v1/analytics/project-passport/{contract_id}` добавляет туда **+1 собранный
  тест**, и он **проходит**, а не пропускается: пропусков остаётся **6**. Заложено
  в ожидание задачи 5.
- **Классификатор в тестовой БД уже есть**: миграция 0005 сидирует 362 статьи,
  `conftest` накатывает миграции на старте сессии. Фабрики `WorkCategoryFactory`
  не нужно — статьи выбираются из сида. Фабрик `EstimateAdditionalWorkFactory` и
  `ProposalSummaryLineFactory` в `tests/factories.py` **нет**; задачи 1 и 4 заводят
  локальные хелперы в своих файлах, а не общие фабрики.
- **Pydantic-схем ввода у Ф6 нет** — эндпоинт читающий, параметр один и он в пути.
  Ловушка имён валидаторов (`AGENTS.md` §11) неприменима; названо, чтобы её не
  искали.
- **Правило «партия больше пяти» неприменимо** — спека §5 п. 7: в Ф6 нет ни
  `ON CONFLICT`, ни COALESCE-уникальных и частичных индексов.

### 1.6. Печатные правила — глобальные и написаны под таблицу фазы 6

Найдено этим же прогоном, и первая редакция плана этого не увидела.
`frontend/src/index.css` несёт **глобальный** блок `@media print`, введённый фазой 6
и построенный под старый паспорт:

| Что в блоке | Замер |
|---|---|
| контракт разметки | `data-print="sheet" / "hide" / "only" / "row" / "clamp"` |
| единственный потребитель | `PassportPage.tsx` и его тест — **больше никто** |
| ширины колонок | `th:nth-child(1…7)` — **семь** колонок таблицы фазы 6 |
| у новой таблицы колонок | **пять** (`Код · Статья · Итого · Доля · ₽/м²`) |
| обоснование верхней границы N | комментарием повторяет отменяемый инвариант «одна страница А4» |

Два следствия, оба обязательны:

1. **Правила не мертвы, а вредны.** Блок глобальный: после подмены экрана он
   применится к новой таблице и растянет пять колонок по семи процентным ширинам.
   Удалить его тоже нельзя — §2.11 требует от Ф6 печатной раскладки. Значит Ф6
   **переписывает** блок под свою таблицу, а не игнорирует его.
2. **Ловушка §11 `AGENTS.md` стоит в репозитории живой.** У старого экрана
   `data-print="clamp"` соседствует с `className="block"`, а `block` отменяет
   `display: -webkit-box`. Селекторы там равной специфичности, и исход решает
   порядок правил. Новый экран **не имеет права** это повторить, и проверяется
   это прямым утверждением о разметке, а не чтением.

Отсюда отдельная задача 10 — раньше её в плане не было.

### 1.7. Премиса «снятие `ORDER BY` валит тест» — замерена и оказалась хрупкой

Спека §4.2 утверждает: «Снятие `ORDER BY` этот тест валит, потому что heap-порядок
равен порядку вставки». Это утверждение **о поведении PostgreSQL**, то есть
предпосылка, которую [false-test-premises](../../insights/false-test-premises.md)
требует замерить до того, как на ней построен тест. Замер на `gca_test`, временная
таблица с `UNIQUE (proposal_id, ordinal)`, вставка в обратном порядке (2, затем 1):

| План | Порядок без `ORDER BY` | Снятие защиты |
|---|---|---|
| Bitmap Heap Scan — **выбран планировщиком сам**, и до, и после `ANALYZE` | `2, 1` | валит тест |
| Index Only Scan — `enable_bitmapscan=off; enable_seqscan=off` | **`1, 2`** | **тест остаётся зелёным** |

Вывод: поведенческий тест **защиту не несёт** — он держится на том, какой план
выберет планировщик, а это не контракт. Достаточно другой статистики, другого
объёма или иной версии, чтобы «доказанная защита» стала мёртвой.

**Как теперь:** порядок стережётся **двумя** тестами, как уже сделано в этом
репозитории для арбитра `ON CONFLICT`
([batch-larger-than-five](../../insights/batch-larger-than-five.md): «форма
отрендеренного SQL (unit) и партия через порог»): структурный тест утверждает
`ORDER BY` в **скомпилированном SQL**, поведенческий остаётся положительной
проверкой и в реестр снятий не входит. То же — для порядка `own_sections`.

### 1.8. Экран «Настройки» продолжит утверждать отменённый инвариант

Задача правки документов снимает из `AGENTS.md` §10 обещание «паспорт печатается
на одну страницу А4». Прогон по существующим входам показывает, что **это же
обещание живёт в десяти местах кода** и после снятия инварианта каждое из них
станет ложным утверждением, а маршрут `/settings` останется рабочим — скрытого
пункта навигации недостаточно.

Первая редакция этого раздела насчитала семь мест и **недосчитала три**. Список
ниже — результат сплошного поиска (`grep -rn "А4"` по `backend/**.py` и
`frontend/src/**.{ts,tsx,css}`), а не перечисление по памяти.

| Место | Что говорит |
|---|---|
| `backend/crud/settings.py:96-98` | текст `422`, со ссылкой на **`AGENTS.md §10`** |
| `backend/crud/settings.py:3, 56, 81` | «настройка … паспорта», «точка входа для эндпоинта паспорта» |
| `backend/models.py:976` | «Верхняя граница — требование DoD §10» |
| **`backend/routers/settings.py:3-5, 41`** | «паспорт читает `passport_top_n`»; «граница держит требование DoD §10 о печати на одну страницу А4» |
| **`backend/main.py:157-158`** | комментарий монтирования: «`passport_top_n` нужен паспорту» |
| **`backend/tests/integration/test_settings_api.py:6`** | модульная докстрока: «паспорт читает `passport_top_n`» |
| `backend/tests/integration/test_settings_api.py:92, 99` | докстрока теста и утверждение `"А4" in detail` |
| `frontend/src/pages/settings/SettingsPage.tsx:22, 95-96` | видимый человеку текст под полем |
| `frontend/src/pages/settings/SettingsPage.test.tsx:41, 84, 92, 108` | утверждает `/на одну страницу А4/`, повторяет текст в подмене MSW и в комментариях |
| `frontend/src/test/handlers.ts:478, 490` | зеркало сообщения сервера |
| `frontend/src/services/queries.ts:569` | «Паспорт зависит от N» — после Ф6 неверно (граница 11 спеки) |
| `frontend/src/index.css:341-347` | комментарий, обосновывающий N той же раскладкой — **владелец задача 10**, она этот блок переписывает |

**Верхняя граница `CHECK` 1..20 остаётся** (§2.11 спеки: колонка не удаляется,
это была бы миграция, которой у Ф6 по рамке нет). Меняется **обоснование**:
`passport_top_n` объявляется настройкой **экрана фазы 6**, который из навигации
убран и на новый паспорт не влияет; граница остаётся историей подобранной тогда
раскладки, а не действующим инвариантом. Комментарий миграции 0004 **не
трогается** — она обязана быть неизменной во времени, и её текст описывает момент
своего написания.

**Что НЕ переформулируется, и это решение, а не пропуск:**

- **Метка поля «Ключевых расценок в паспорте»** остаётся дословно. На неё
  опираются **шесть** тестов через `findByLabelText`, а сама она верна: настройка
  и правда про ключевые расценки паспорта фазы 6. Правится **подсказка** под
  полем, где живёт ложное обоснование, а не метка.
- **`MatrixPage.tsx:364` и `MatrixPage.test.tsx:169`** — там «одна строка выше
  листа А4» сказано про **зажим многокилобайтового наименования** (§11
  `AGENTS.md`). Это другое утверждение, оно верно и остаётся. Названо, потому что
  сплошной поиск по «А4» их находит, и без этой строки следующий читатель
  «дочинил» бы верный текст.

**Критерий приёмки — проверяемый командой, а не списком мест.** После задачи 11
сплошной поиск `А4` по `backend/**.py` и `frontend/src/**.{ts,tsx,css}` обязан
оставить вхождения **ровно** в трёх местах:

1. `alembic/versions/2026_08_04_0004-app_settings.py` — неизменяемая миграция;
2. `frontend/src/pages/matrix/MatrixPage.tsx` и его тест — утверждение про зажим,
   не про обещание;
3. и нигде больше: вхождения `PassportPage.tsx` / `PassportPage.test.tsx` исчезают
   **вместе с файлами**, все прочие переформулированы.

Отдельно проверяется, что **`DoD §10` и `AGENTS.md §10` не остались обоснованием
ни в одном живом файле** — обещания, на которое они ссылаются, после этой задачи
не существует.

Счёт тестов от этого не меняется: `test_out_of_range_message_explains_the_a4_reason`
переформулируется (проверять надо **наличие объяснения**, а не слово «А4» — этого
и требует его собственная докстрока), и утверждение в `SettingsPage.test.tsx` —
тоже. Оба остаются одним тестом каждый.

---

## 2. Global Constraints

Нарушение любого пункта — отступление, которое записывается в devlog, а не
делается молча.

### 2.1. Деньги и контракт данных

1. `Decimal` end-to-end; `float` не появляется нигде. Сложение roll-up — только
   `Decimal`, в Python; в браузере десятичные строки **не складываются** (§2.7).
2. Ответ эндпоинта уходит через `responses.decimal_json` — иначе `jsonable_encoder`
   превратит `Decimal` во `float` до рендера (замер фазы 5). Проверяется по
   **сырому телу**, а не по разобранному JSON.
3. Даты приводятся `crud.common.iso` в доменном слое: энкодер `decimal_json`
   намеренно бросает `TypeError` на `datetime`.
4. Прочерк ≠ ноль. `total = null` тогда и только тогда, когда `rows_priced = 0`
   (§2.3 правило 1); ноль показывается числом `"0.00"`.

### 2.2. База данных и миграции

5. **Голая команда Alembic из `backend/` уходит по СТЕНДУ.** `alembic/env.py:16`
   делает `load_dotenv(ROOT/".env")`, `backend/.env` несёт `DATABASE_URL` с
   `gca_dev`, `alembic.ini` объявляет `sqlalchemy.url` пустым, а `$env:DATABASE_URL`
   в процессе **пуст** (замерено в Ф5). `db_guard` не спасает — его ось роль
   окружения, а не имя базы. Поэтому: `DATABASE_URL` задаётся **явно** и проверяется
   на суффикс `_test` **до** запуска.
6. `upgrade`/`check` тестовой базы — рецептом **`just db-test-check`**. Стенд —
   рецептом **`just db-dev-init`**, и это операция с живой базой: **спросить
   разрешение до действия**.
7. Готового рецепта `downgrade` **нет**. Круговой рейс `downgrade base → upgrade
   head` гоняется **только на `gca_test`**, полным контуром с `try/finally`,
   возвращающим `DATABASE_URL`. На стенде откат ниже `0003` упрётся в
   `ProgramLimitExceeded` (`AGENTS.md` §11) — это не дефект Ф6.
8. **Схемную защиту нельзя снять правкой базы.** `conftest.py:104-105` делает
   `DROP SCHEMA public CASCADE` и накатывает миграции заново на старте **каждой**
   сессии pytest: временный `ALTER` затирается до того, как тесты исполнятся.
   Снятие делается **правкой файла миграции**.
9. Новому integration-файлу — `pytestmark = pytest.mark.integration`.

### 2.3. PowerShell 5.1 (не bash) — каждая ловушка замерена

10. После **каждой** нативной команды внутри `try` —
    `if ($LASTEXITCODE -ne 0) { throw "<что> failed: $LASTEXITCODE" }`: после
    `try/finally` `$?` равен `True` при ненулевом `$LASTEXITCODE`.
11. `$ErrorActionPreference = 'Stop'` вокруг нативного вызова обрывает блок на
    stderr (vitest пишет туда предупреждения jsdom). Восстановление держать в
    `finally`.
12. Вердикт читается по **напечатанным числам**, а не по коду возврата: `rg` без
    совпадений возвращает `1`.
13. **vitest не поднимается из питоновского подпроцесса**, а провал печатает
    `Test Files 1 failed` — счётчик принимает это за один упавший тест.
    Фронтендовые прогоны гонять **из PowerShell** и **отказываться печатать
    вердикт** без строки `Tests ...`.
14. Сообщение коммита писать
    `[System.IO.File]::WriteAllText($p, $s, (New-Object System.Text.UTF8Encoding($false)))`:
    `Set-Content -Encoding utf8` в PS 5.1 добавляет BOM, и `\ufeff` уезжает в
    заголовок. Файлы читать инструментом `Read`, а не `Get-Content`.
15. `.ps1` без BOM читается как ANSI и искажает кириллицу: **якоря снятия держать
    ASCII**, шаблоны приводить к **фактическому** разделителю строк файла (в дереве
    встречается CRLF).
16. Не работают: `env -u`, `VAR=value` префиксом, `&&`, `||`, `grep`, `wc`,
    heredoc, перенос строки обратным слэшем. `$(...)` спотыкается о скобки внутри
    regex — считать в переменную заранее.
17. Имя побайтовой копии выводить из **полного пути**, не из basename (в Ф5
    столкнулись `routers/contracts.py` и `crud/contracts.py`).
18. Разовые скрипты — в `$env:TEMP`, каталога `scratchpad/` в репозитории нет.
    Дочернему python — `$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'`.
    Команды бэкенда — из `backend/` через `uv run`; git — из корня.

### 2.4. Замеры и проверки

19. **Число собранных тестов замеряется после КАЖДОЙ задачи и пофайлово.**
    Расхождение с ожиданием — сигнал дефекта, число не «поправляется»:
    объясняется или чинится.
20. Шаги CI гонять **по отдельности** и в форме CI. Перед пушем — `just ci`
    целиком.
21. Негативные проверки снятием защиты — по **всем восьми слоям**
    [verifying-guards](../../insights/verifying-guards.md), **делает оркестратор
    лично**. Контрольный прогон **до** снятия; `assert old in text` с проверкой
    **единственности** вхождения; sha256 до и после; восстановление из **побайтовой
    копии** со сверкой sha256 (`git checkout --` даёт CRLF и ложную тревогу).
22. **Вход негативного теста обязан нарушать ровно одно ограничение** (следствие
    слоя 8). Точная модель снятия сохраняет вычисление и убирает **отказ**.
23. Прежде чем записать «дефект воспроизведён» или «защиты нет» — спросить, **что
    ещё** стоит на пути (слой 8).
24. Печатную раскладку А4 проверяет **только замер в браузере**; `@media print` в
    jsdom не наблюдаем. У Ф6 обязательство читается как «нет обрезки по правому
    краю и разрывов внутри строк», а не «один лист» (§2.11).

### 2.5. Фронтенд

25. Только примитивы **shadcn/ui** и токены темы; новый примитив ставится
    `npx shadcn add`, а не пишется руками. Пользователь против кастомных
    компонентов.
26. Наименование зажимается по высоте всюду, где попадает в таблицу; **класс
    `block` рядом с `line-clamp` отменяет зажим вовсе** (`AGENTS.md` §11).
27. Четыре состояния у каждого блока данных: загрузка, отказ, пусто, заполнено.
    Пустое состояние утверждает **факт о данных**, которого при неудавшемся запросе
    мы не знаем (урок Ф5 §4a).
28. Тема: токены определяются в голом `:root`, переопределяются под
    `prefers-color-scheme` и под `[data-theme="dark"]` — все три состояния.

### 2.6. Роли и субагенты

29. **Opus — оркестратор:** держит план, решает отступления, **делает все коммиты**,
    **лично** выполняет все негативные проверки и все операции с базами.
    Sonnet — содержательные задачи и замеры. Haiku — механика. Fable — финальное
    ревью ветки перед внешним кругом.
30. Субагентам **запрещено**: печатать кириллицу в терминал (вердикты ASCII,
    русский текст — в файл отчёта, `rg -c` вместо `rg`, `Read` вместо
    `Get-Content`); команды `alembic`; `just`-рецепты, трогающие базу.
31. Мержит **пользователь**.

---

## 3. Задачи

Каждая задача называет **свой красный прогон до реализации**. Ожидаемые дельты —
ожидания, подлежащие замеру, а не факты.

### Задача 1 — миграция 0010, VIEW `v_category_totals`, отражение, семантика

Образец берётся из репозитория и **не изобретается**: `v_position_deviations`
создаётся `op.execute()` в 0002 из модульной константы, снимается
`DROP VIEW IF EXISTS` в `downgrade`, отражается `sa.table(...)` рядом с
`DECLARED_VIEW_COLUMNS`, стережётся сверкой с `information_schema` и отдельным
файлом семантики. Ф6 повторяет ровно это.

**Файлы:**
- `backend/alembic/versions/2026_08_10_0010-category_totals_view.py` — новый;
  константа `V_CATEGORY_TOTALS` в теле миграции (миграция неизменна во времени,
  литералы строками), `op.execute(V_CATEGORY_TOTALS)` в `upgrade`,
  `op.execute("DROP VIEW IF EXISTS v_category_totals")` в `downgrade`.
- `backend/crud/project_passport.py` — **новый модуль**. Отражение живёт в модуле,
  который VIEW потребляет: `crud/analytics.py` — код фазы 6, а спека §2.1 требует
  его не трогать.
- `backend/tests/integration/test_category_totals_view.py` — новый,
  `pytestmark = pytest.mark.integration`.

**Форма VIEW** (гранулярность — прямые суммы, без roll-up; §2.2):

```
CREATE VIEW v_category_totals AS
-- ветка позиций: путь до статьи ТОЛЬКО через chapter_item_id (§1.5, запрет Ф3)
SELECT l.estimate_id, ch.work_category_id, 'positions'::text AS source,
       SUM(pi.total_cost_total) FILTER (WHERE <годно>)              AS amount,
       COUNT(*)::int                                                 AS row_count,
       COUNT(*) FILTER (WHERE pi.total_cost_total IS NOT NULL
                          AND <годно>)::int                          AS rows_with_amount,
       COUNT(*) FILTER (WHERE pi.total_cost_total IS NOT NULL
                          AND NOT <годно>)::int                      AS rows_not_finite
FROM position_items pi
JOIN proposals p ON p.id = pi.proposal_id
JOIN lots      l ON l.id = p.lot_id
LEFT JOIN position_items ch
       ON ch.id = pi.chapter_item_id AND ch.proposal_id = pi.proposal_id
WHERE pi.is_chapter = false
GROUP BY l.estimate_id, ch.work_category_id
UNION ALL
-- ветка допработ: proposal_id → lot_id → estimate_id
SELECT l.estimate_id, aw.work_category_id, 'additional_works'::text, … аналогично …
FROM estimate_additional_works aw
JOIN proposals p ON p.id = aw.proposal_id
JOIN lots      l ON l.id = p.lot_id
GROUP BY l.estimate_id, aw.work_category_id
```

где `<годно>` — три арма фильтра годности:
`x <> 'NaN'::numeric AND x <> 'Infinity'::numeric AND x <> '-Infinity'::numeric`.
Сравнение `x = 'NaN'::numeric` в PostgreSQL истинно — замер §1.11 спеки.

**Фильтра по `amendment_no` в VIEW НЕТ** (§2.2, подтверждено на гейте 1): имя
объекта обязано описывать его содержимое. Выбор исходной сметы делает CRUD.

**Отражение в `crud/project_passport.py`:**

```python
CATEGORY_TOTALS = sa.table(
    "v_category_totals",
    sa.column("estimate_id", sa.BigInteger),
    sa.column("work_category_id", sa.BigInteger),
    sa.column("source", sa.Text),
    sa.column("amount", sa.Numeric),
    sa.column("row_count", sa.Integer),
    sa.column("rows_with_amount", sa.Integer),
    sa.column("rows_not_finite", sa.Integer),
)
DECLARED_CATEGORY_TOTALS_COLUMNS = tuple(c.name for c in CATEGORY_TOTALS.columns)

SOURCE_POSITIONS = "positions"
SOURCE_ADDITIONAL_WORKS = "additional_works"
```

**Красный прогон до реализации:** новый файл тестов при отсутствующем VIEW —
`psycopg.errors.UndefinedTable: relation "v_category_totals" does not exist`, и
тест состава колонок сравнивает пустой кортеж с объявленным.

**Тесты (§4.1 спеки) — раскладка «функция × параметры = собрано»:**

| Тестовая функция | Параметров | Собрано |
|---|---|---|
| `test_declared_view_columns_match_the_database` — сверка с `information_schema` | — | 1 |
| `test_chapter_rows_do_not_enter_the_sums` — `is_chapter = true` не в суммах | — | 1 |
| `test_position_under_a_chapter_without_a_category_falls_into_null` | — | 1 |
| `test_position_without_a_chapter_reference_falls_into_null` (синтетика — граница 8) | — | 1 |
| `test_additional_works_come_with_their_own_source` | — | 1 |
| `test_additional_work_without_a_category_falls_into_null` | — | 1 |
| `test_duplicate_chapter_numbers_do_not_double_the_money` — два раздела **одного номера** с **разными** статьями, под каждым свои позиции | — | 1 |
| `test_amount_is_null_exactly_when_no_row_entered_it` | — | 1 |
| `test_row_without_a_price_counts_but_does_not_enter_the_amount` — `row_count = 1`, `rows_with_amount = 0`, `rows_not_finite = 0` | — | 1 |
| `test_non_finite_value_does_not_poison_the_aggregate` — негодное рядом с двумя обычными: `amount` конечен по двум, `rows_not_finite = 1` | `'NaN'`, `'Infinity'`, `'-Infinity'` | **3** |
| `test_opposite_infinities_do_not_poison_the_aggregate` — пара, которая **без фильтра дала бы `NaN` при двух годных на вид слагаемых** | — | 1 |
| `test_view_does_not_filter_amendment_no` — смета-ДС в VIEW присутствует | — | 1 |
| **Итого** | | **14** |

**Круговой рейс** `downgrade base → upgrade head` — на `gca_test`, полным контуром
(Global Constraints 5–7), плюс `just db-test-check` (`alembic check` — «No new
upgrade operations detected»). Тестом не считается: это команда, а не собранный тест.

**Ожидаемая дельта:** backend **+14** (1337 → **1351**), vitest **0** (206).

---

### Задача 2 — чистый roll-up дерева статей

Дерево, `own`/`total`, три счётчика и отсечение считаются **чистыми функциями без
БД** — тот же приём, что у `services/category_resolution.py` (Ф3): алгоритм
проверяется юнит-тестами, вход строится литералами.

**Файлы:**
- `backend/services/category_rollup.py` — новый.
- `backend/tests/unit/test_category_rollup.py` — новый.

**Интерфейс:**

```python
@dataclass(frozen=True)
class CategoryRef:              # строка справочника
    id: int; code: str; title: str; parent_id: int | None
    is_bucket: bool; sort_order: int

@dataclass(frozen=True)
class DirectTotals:             # строка VIEW, уже разложенная по источникам
    amount: Decimal | None
    row_count: int
    rows_with_amount: int
    rows_not_finite: int

@dataclass(frozen=True)
class CategoryNode:
    ref: CategoryRef
    total: Decimal | None; rows: int; rows_priced: int; rows_not_finite: int
    own: Decimal | None; own_rows: int; own_rows_priced: int; own_rows_not_finite: int
    children: tuple["CategoryNode", ...]

def build_tree(
    categories: Sequence[CategoryRef],
    direct: Mapping[int | None, Mapping[str, DirectTotals]],
) -> tuple[CategoryNode, ...]:
    """Корни классификатора со свёрнутым поддеревом.

    `direct` — прямые суммы из VIEW: ключ `work_category_id` (None = нераспределённое),
    внутри — по `source`. Roll-up идёт снизу вверх, сложение — `Decimal`.
    """
```

**Правила, которые задача исполняет (§2.3, §2.7, §2.8):**
1. `total = own + сумма extras-ветки + сумма total детей`; складываются только
   **известные** слагаемые. `total = None` ⟺ `rows_priced == 0`.
2. `own` подчиняется тому же правилу по своим счётчикам.
3. `rows`, `rows_priced`, `rows_not_finite` **сворачиваются по поддереву**, а не
   считаются только по собственным строкам узла.
4. **Все 21 корня присутствуют всегда**, даже отсутствующие в смете
   (`total = None, rows = 0`).
5. Глубже первого уровня узел попадает в результат, **только если в его поддереве
   есть хотя бы одна строка** (`rows > 0`). Нулевые попадают, отсутствующие — нет.
6. Порядок — `sort_order` справочника (топологический, родитель раньше ребёнка —
   факт Ф1).

**Красный прогон:** `ImportError: cannot import name 'build_tree'`.

**Тесты — раскладка «функция × параметры = собрано»:**

| Тестовая функция | Параметров | Собрано |
|---|---|---|
| `test_leaf_total_equals_its_own_money` | — | 1 |
| `test_parent_total_is_own_plus_children_plus_extras` — все три слагаемых непусты | — | 1 |
| `test_parent_with_own_money_beyond_children_keeps_both_numbers` — случай §1.4 | — | 1 |
| `test_unknown_total_is_none_not_zero` | — | 1 |
| `test_zero_total_is_zero_not_none` | — | 1 |
| `test_counters_roll_up_over_the_whole_subtree` — дед / отец / внук | `rows`, `rows_priced`, `rows_not_finite` | **3** |
| `test_mixed_node_keeps_the_partial_sum_and_the_incompleteness` — девять расценённых и одна без цены | — | 1 |
| `test_mixed_node_reports_both_causes_separately` — пустые цены и негодные вместе | — | 1 |
| `test_all_twenty_one_roots_are_present_even_when_absent_from_the_estimate` | — | 1 |
| `test_deeper_nodes_appear_only_when_their_subtree_has_rows` | — | 1 |
| `test_zero_subtree_appears_because_its_rows_are_positive` | — | 1 |
| `test_children_follow_the_reference_sort_order` | — | 1 |
| `test_own_follows_the_same_three_rules_as_total` | неизвестно / ноль / частично | **3** |
| `test_decimal_arithmetic_only` — предпосылка проверяется **внутри теста**: вход из значений, на которых `float` промахнулся бы ([false-test-premises](../../insights/false-test-premises.md)) | — | 1 |
| **Итого** | | **18** |

**Ожидаемая дельта:** backend **+18** (1351 → **1369**), vitest 0.

---

### Задача 3 — сборка ответа: итоги, доли, руб/м², нераспределённое

**Файлы:** `backend/crud/project_passport.py` (наполняется),
`backend/tests/integration/test_project_passport_api.py` — новый.

**Интерфейс:**

```python
def get_project_passport(db: Session, contract_id: int) -> dict:
    """Паспорт договора по статьям классификатора (§2.6).

    Договор без сметы — НЕ 404: карточка заведена, файл не загружен.
    404 остаётся за несуществующим договором.
    """

def _source_estimate(db: Session, contract_id: int) -> Estimate | None:
    """Исходная смета договора: amendment_no IS NULL.

    Отдельная функция и НЕ переиспользование `latest_estimates()`/
    `get_latest_estimate()` из `crud/analytics.py`: там правило «последняя смета»
    (§6), здесь — «исходная» (§2.4). Это два разных правила, и вторая копия
    первого была бы дефектом, а не экономией.
    """

def _unallocated_breakdown(db, estimate_id) -> tuple[int, int]:
    """(`chapters`, `rows_outside_structure`) — две разные причины одного следствия."""
```

**Правила задачи:**
1. **Фильтр `amendment_no IS NULL` стоит в CRUD**, а не в VIEW (§2.4).
2. `totals.amount` подчиняется правилу узла: `null` ⟺ в сумму не вошла ни одна
   строка (`positions_rows_priced = 0` **и** `additional_works_rows = 0`).
3. `share_pct` **считает сервер**: знаменатель — `totals.amount`, один и тот же для
   всех строк включая «Нераспределённое»; `null`, если `totals.amount` пуст или
   равен нулю. Точный `Decimal`, округление — только на слое представления.
4. `per_sqm` = сумма узла / `object.area_total_sp`; `null` при `NULL`-площади или
   при `null`-сумме. Деления на ноль не бывает: `CHECK` Ф5 держит общую строго
   положительной.
5. `object_contracts_count` — число договоров у объекта.
6. `unallocated.chapters` — число **различных** строк-разделов без статьи, под
   которыми есть позиции; `rows_outside_structure` — позиции без `chapter_item_id`.

**Красный прогон:** `AttributeError: module 'crud.project_passport' has no attribute
'get_project_passport'`.

**Тесты — раскладка «функция × параметры = собрано»:**

| Тестовая функция | Параметров | Собрано |
|---|---|---|
| `test_source_estimate_is_taken_when_an_amendment_exists` — у договора исходная смета и ДС, паспорт берёт **только** исходную (закрывает фильтр в CRUD, раз его нет в VIEW) | — | 1 |
| `test_sum_invariant_categories_plus_unallocated_equals_positions_plus_extras` | — | 1 |
| `test_rollup_invariant_holds_on_the_whole_tree` | — | 1 |
| `test_all_twenty_one_roots_reach_the_response` | — | 1 |
| `test_share_is_computed_from_the_grand_total_for_every_row` | — | 1 |
| `test_share_is_null_when_the_grand_total_is_unusable` | итог пуст / итог ноль | **2** |
| `test_per_sqm_is_null_on_a_null_area_not_zero` | итог / строка статьи | **2** |
| `test_zero_sum_reaches_json_as_a_number_not_null` | — | 1 |
| `test_unallocated_counts_chapters_and_rows_outside_structure_separately` | — | 1 |
| `test_contract_without_an_estimate_answers_200_with_null_estimate` | — | 1 |
| `test_unknown_contract_answers_404` | — | 1 |
| `test_object_contracts_count_reflects_the_object` | — | 1 |
| **Итого** | | **14** |

**Ожидаемая дельта:** backend **+14** (1369 → **1383**), vitest 0.

---

### Задача 4 — валовое ИТОГО, ставка НДС, сверка

**Файлы:** `backend/crud/project_passport.py`,
`backend/tests/integration/test_project_passport_api.py`.

**Правила (§2.5):**
1. Валовое ИТОГО сметы = сумма `total_cost_including_vat` по **всем** предложениям
   всех лотов исходной сметы.
2. Оно **неизвестно** (`null`), если: нет ни одного предложения; хотя бы у одного
   нет строки с этим ключом; хотя бы одно значение пусто или не `is_finite()`.
3. **Сверка требует ДВУХ известных операндов.** `delta_to_file_total = null`, если
   неизвестен **хотя бы один** — `totals.amount` или `file_total_including_vat`.
4. `delta = totals.amount − валовое`, точный `Decimal`; строка расхождения
   показывается только при `delta ≠ 0`.
5. Ставка НДС — правило единогласия: одна и та же у всех предложений исходной
   сметы → она; различаются или хотя бы одна `NULL` → `null`. Заявленный `0 %`
   отличим от отсутствия.

**Красный прогон:** тест валового на смете с двумя лотами — `KeyError:
'file_total_including_vat'`.

**Тесты — раскладка «функция × параметры = собрано»:**

| Тестовая функция | Параметров | Собрано |
|---|---|---|
| `test_file_total_sums_over_every_proposal_of_the_source_estimate` | — | 1 |
| `test_missing_key_in_one_proposal_makes_the_file_total_unknown` | — | 1 |
| `test_estimate_without_proposals_makes_the_file_total_unknown` | — | 1 |
| `test_non_finite_value_makes_the_file_total_unknown_without_raising` | `'NaN'`, `'Infinity'`, `'-Infinity'` | **3** |
| `test_delta_is_null_when_the_table_sum_is_unknown_and_the_file_total_is_known` — **второй операнд**; вход: смета, у всех позиций которой цена пуста, при разобранном блоке итогов. Без него правило проверялось бы с одной стороны | — | 1 |
| `test_delta_is_null_when_both_operands_are_unknown` — договор без сметы | — | 1 |
| `test_delta_is_zero_when_the_paths_agree` | — | 1 |
| `test_delta_is_reported_when_the_paths_disagree` | — | 1 |
| `test_vat_rate_is_taken_when_every_proposal_declares_the_same` | — | 1 |
| `test_vat_rate_is_null_when_proposals_disagree` | — | 1 |
| `test_vat_rate_is_null_when_one_proposal_has_none` | — | 1 |
| `test_declared_zero_vat_is_distinguishable_from_absence` | — | 1 |
| **Итого** | | **14** |

**Ожидаемая дельта:** backend **+14** (1383 → **1397**), vitest 0.

---

### Задача 5 — строковые данные узла отдельными запросами, эндпоинт, права

Две величины живут **не в VIEW**, потому что у них другая гранулярность: VIEW
сворачивает в сумму на статью, а экрану нужны строки. Вытаскивать строки из
агрегата нельзя (§2.6), поэтому у каждой свой запрос.

**Файлы:** `backend/crud/project_passport.py`, `backend/routers/analytics.py`
(добавляется эндпоинт, ручки фазы 6 не трогаются),
`backend/tests/integration/test_project_passport_api.py`.

#### 5а. `extras` — строки допработ (§2.6)

1. Второй запрос — прямо в `estimate_additional_works` по предложениям исходной
   сметы; `id` и `ordinal` отдаются наружу.
2. Порядок **задан явно**: `ORDER BY proposal_id, ordinal`. Ключ полный
   (`UNIQUE (proposal_id, ordinal)` Ф4), порядок тотальный. `lots.lot_key`
   отвергнут как строковый (§2.6); граница 16 спеки в силе.
3. Допработы без статьи — в `unallocated.extras`, тем же порядком.
4. **Инвариант, связывающий два запроса:** сумма `extras` узла равна `amount`
   ветки `'additional_works'` этой же статьи в VIEW.

#### 5б. `own_sections` — какие разделы дали собственные деньги статьи

**Этого поля в спеке нет, и это её внутреннее расхождение, а не расширение
скоупа.** §2.9 п. 6 требует у служебной строки подпись «какие разделы сметы туда
попали», §6 отдельно отвергает «имя раздела как заголовок» **в пользу** этой
подписи, а утверждённый на гейте 1 макет показывает её живой («раздел сметы 6.5
«Прочее»»). Форма ответа §2.6 при этом поля не несёт — то есть требование
экрана не имеет источника данных, и фронтенд восстановить его не может ничем,
кроме догадки. План закрывает расхождение полем и **фиксирует его в devlog как
уточнение спеки**, а не как молчаливую добавку.

```
categories[].own_sections: [ { id, number, title } ]
```

Состав: строки-разделы (`is_chapter = true`) исходной сметы, у которых
`work_category_id` равен статье узла **и** под которыми есть хотя бы одна прямая
позиция. `number` — `chapter_number_in_proposal`, `title` —
`job_title_in_proposal`. У узла без собственных денег список пуст.

**Порядок — не по `position_items.id`.** Это прямой запрет хвоста Ф3: решение
§2.8 порядок сохраняет, но контрактом его не объявляет. Сортировка идёт по
**числовому** порядку ключа позиции, выраженному **без приведения типа**:

```
ORDER BY p.id, length(ch.position_key_in_proposal), ch.position_key_in_proposal
```

Приведение `::numeric` отвергнуто осознанно: колонка объявлена `String(255)` без
`CHECK`, непрерывность `1..N` держится **построением парсера**, а не схемой, и на
ключе, который схема не запрещает, приведение уронило бы **чтение паспорта**.
Длина-плюс-лексикографика даёт ровно числовой порядок на строках цифр без ведущих
нулей и не падает ни на каком входе. Граница названа: на нечисловом ключе порядок
становится детерминированным, но произвольным.

#### 5в. Защита порядка — двумя тестами, а не одним

Замер §1.7 показал, что поведенческий тест порядка держится на выборе
планировщика. Поэтому у **каждого** из двух порядков (`extras`, `own_sections`)
защиту несёт **структурный** тест: скомпилированный SQL
(`str(select.compile(dialect=postgresql.dialect()))`) обязан содержать `ORDER BY`
с ожидаемыми ключами. Поведенческий тест остаётся положительной проверкой и в
реестр снятий не входит — это прямое следствие
[false-test-premises](../../insights/false-test-premises.md).

**Эндпоинт:**

```python
@router.get("/project-passport/{contract_id}")
def get_project_passport(contract_id: int, db: Session = Depends(get_db)):
    """Паспорт проекта по статьям классификатора (Ф6 фазы 7).

    Чтение — доступно `member` (§3 AGENTS.md: аналитика есть чтение).
    """
    try:
        return decimal_json(crud_project_passport.get_project_passport(db, contract_id))
    except DomainError as e:
        _raise(e)
```

**Красный прогон:** `client.get("/api/v1/analytics/project-passport/1")` → `404`
маршрута (не доменный 404), тест ждёт `200`.

**Тесты — раскладка «функция × параметры = собрано»:**

| Тестовая функция | Параметров | Собрано |
|---|---|---|
| `test_extras_of_a_node_sum_to_the_view_branch` — инвариант двух запросов | — | 1 |
| `test_extras_select_declares_an_explicit_order` — **структурный**, форма SQL | — | 1 |
| `test_extras_come_back_in_ordinal_order` — поведенческий: две записи вставляются в **обратном** порядке `ordinal` (сначала 2, потом 1), ответ отдаёт 1, затем 2 | — | 1 |
| `test_extras_carry_id_and_ordinal` | — | 1 |
| `test_additional_work_without_a_category_lands_in_unallocated_extras` | — | 1 |
| `test_own_sections_name_the_chapter_rows_of_the_category` | — | 1 |
| `test_own_sections_list_both_sections_when_the_money_comes_from_two` — случай §1.4, четвёртый из четырёх | — | 1 |
| `test_own_sections_are_empty_when_the_node_has_no_own_money` | — | 1 |
| `test_own_sections_select_declares_an_explicit_order` — **структурный**, форма SQL | — | 1 |
| `test_own_sections_do_not_order_by_position_item_id` — хвост Ф3: ключи и `id` расходятся, порядок следует ключу | — | 1 |
| `test_money_reaches_json_as_strings_in_every_branch` — по **сырому телу** | `totals`, `categories`, `unallocated`, `extras` | **4** |
| `test_member_can_read_the_endpoint` — утверждает **отсутствие** ограничения, в снятиях не участвует | — | 1 |
| `test_auth_coverage.py` — новый роут в параметризации, **проходит**, а не пропускается | — | **1** |
| **Итого** | | **16** |

**Ожидаемая дельта:** backend **+16** (1397 → **1413**), vitest 0. Пропусков
по-прежнему **6**.
Контрольный полный прогон: **1413 собранных / 1407 passed / 6 skipped**.

---

### Задача 6 — фронтенд: типы, транспорт, хук, кэш, MSW

**Файлы:**
- `frontend/src/types/domain.ts` — новые типы (старые `Passport*` пока живы);
- `frontend/src/services/api/analytics.ts` — `analyticsApi.projectPassport`;
- `frontend/src/services/queryKeys.ts` — `qk.passport.project`;
- `frontend/src/services/queries.ts` — `useProjectPassport`;
- `frontend/src/test/fixtures.ts` — `sampleProjectPassport`;
- `frontend/src/test/handlers.ts` — хендлер нового ответа + состояния;
- `frontend/src/services/queries.test.tsx` — **+1 тест** (см. §1.2).

**Интерфейс:**

```ts
// queryKeys.ts — корень ПЕРЕИСПОЛЬЗУЕТСЯ (§2.12, обязательство 1 Ф5)
passport: {
  all: ["passport"] as const,
  one: (contractId: number) => ["passport", contractId] as const,       // фаза 6, уходит в задаче 11
  project: (contractId: number) => ["passport", "project", contractId] as const,
},
```

Корень `["passport"]` — префикс обоих ключей, поэтому инвалидация
`qk.passport.all` в `useUpdateObject` накрывает новый паспорт **по построению**;
расширять список инвалидации не требуется.

Типы — по форме ответа §2.6: `ProjectPassport`, `ProjectPassportContract`,
`ProjectPassportObject`, `ProjectPassportEstimate`, `ProjectPassportTotals`,
`ProjectPassportCategory`, `ProjectPassportUnallocated`, `ProjectPassportExtra`.
Все денежные поля — `Decimal` (= `string`), приводить к `number` нельзя.

**Состояния MSW** (для четырёх состояний блоков и пустых случаев):
`handlerState.projectPassportOutcome: "full" | "no-estimate" | "no-tep" |
"empty-total" | "zero-total" | "corrupted" | "error"`.
`handlerState.passportTopN` и хендлеры `/api/v1/settings` **не трогаются** (§1.1 п. 2).

**Красный прогон:** новый тест корня кэша при хуке, которого ещё нет —
`ImportError: useProjectPassport`.

**Тесты — раскладка «функция × параметры = собрано»** (в `queries.test.tsx`):

| Тест | Параметров | Собрано |
|---|---|---|
| `корень паспорта переиспользован новым хуком` — рендерит `useProjectPassport`, берёт ключ **из кэша**, утверждает, что `qk.passport.all` — его префикс. Закрывает дыру §1.2 | — | 1 |
| `паспорт не запрашивается, пока договор не известен` — хук получает `enabled: id !== undefined`; без него уходит `GET /project-passport/0` и штатно получает `404` (дефект P3 Ф5 §4a, тот же класс) | — | 1 |
| **Итого** | | **2** |

**Ожидаемая дельта:** backend 0, vitest **+2** (206 → **208**).

---

### Задача 7 — экран: шапка, показатели, условия, сигналы документа

**Файлы:**
- `frontend/src/pages/passport/ProjectPassportPage.tsx` — новый (страница, состояния);
- `frontend/src/pages/passport/PassportHeader.tsx` — новый (титульная полоса,
  линейка показателей, оговорки);
- `frontend/src/pages/passport/ProjectPassportPage.test.tsx` — новый.

Страница **не подключается к маршруту** в этой задаче: маршрут занят старым
экраном, и одна задача не может держать два элемента на одном пути. Тесты рендерят
компонент напрямую через `renderWithProviders`. Маршрут переключает задача 11.

**Что реализуется (§2.9 пп. 1–4, 10, 11, 14):**
- четыре состояния блока данных: загрузка / отказ / пусто / заполнено;
- шапка: объект, номер договора, подрядчик, дата подписания, класс, подписант;
- линейка показателей: общая площадь с раскладкой на подземную и надземную;
  стоимость по смете с НДС и ставка НДС подписью; стоимость за м²; аванс /
  гарантия / удержание;
- **нет ТЭП** — бейдж вместо чисел, вся колонка ₽/м² в прочерках;
- оговорки коммерческих условий зажимаются по высоте (обязательство 2); класс
  `block` рядом с `line-clamp` **не ставится**;
- бейдж «у объекта N договоров» при `object_contracts_count > 1`;
- **строка сверки** — только при расхождении; молчание есть нормальный вид;
- **баннер порчи данных** — только при `totals.positions_rows_not_finite > 0`,
  называет число позиций и говорит, что итог неполон.

**Красный прогон:** `Cannot find module './ProjectPassportPage'`.

**Тесты — раскладка «функция × параметры = собрано»:**

| Тест | Параметров | Собрано |
|---|---|---|
| `четыре состояния блока данных` | загрузка, отказ, пусто, заполнено | **4** |
| `«ТЭП не заведены» вместо чисел, вся колонка ₽/м² в прочерках` | — | 1 |
| `ставка НДС «не заявлена в файле», а не ноль` | — | 1 |
| `заявленный 0 % показан как ноль, а не как отсутствие` | — | 1 |
| `бейдж «у объекта N договоров» при значении больше единицы` | — | 1 |
| `бейджа нет при единственном договоре` | — | 1 |
| `строка сверки появляется при расхождении` | — | 1 |
| `строки сверки нет при нулевой дельте` | — | 1 |
| `строки сверки нет при неизвестной дельте` | — | 1 |
| `баннер порчи появляется при ненулевом счётчике` | — | 1 |
| `баннера порчи нет при нулевом счётчике` | — | 1 |
| `оговорки зажаты по высоте и не несут класса block` | — | 1 |
| **Итого** | | **15** |

**Ожидаемая дельта:** backend 0, vitest **+15** (208 → **223**).

---

### Задача 8 — экран: таблица по статьям

**Файлы:** `frontend/src/pages/passport/CategoryTable.tsx` — новый;
`ProjectPassportPage.test.tsx` — дополняется.

**Что реализуется (§2.9 пп. 5–9, 12, 15):**
- колонки `Код | Статья классификатора | Итого, ₽ | Доля | ₽ / м²`;
- иерархия сворачиваемая, **по умолчанию свёрнута полностью**: видны 21 статья
  верхнего уровня, «Нераспределённое» и итог;
- наименование зажимается по высоте (в справочнике есть названия до 181 символа);
- **служебная строка собственных денег** появляется при раскрытии тогда и только
  тогда, когда у узла есть дети или допработы **и** `own_rows > 0`; называется
  «Без подстатьи» при детях и «Позиции сметы», если детей нет и раскрытие вызвано
  только допработами; подпись — какие разделы сметы туда попали;
- **допработы** — отдельной строкой с бейджем внутри своей статьи;
- **«Нераспределённое»** — видимая строка внизу, с подписью «N разделов сметы без
  статьи классификатора»; строки вне структуры названы **отдельно**;
- **корзины** (`is_bucket`) — обычные статьи с бейджем;
- **переключатель «показывать нулевые подстатьи»**, по умолчанию выключен;
- **подпись неполноты на узле** по правилу 2 §2.3: называет обе причины по
  отдельности — не вошедших по пустой цене и негодных.

**Красный прогон:** `Cannot find module './CategoryTable'`.

Подпись служебной строки строится из **`own_sections`** (задача 5б): «раздел сметы
6.5 «Прочее»» при одном разделе и перечисление при нескольких. Догадок по номеру
статьи не делается — нумерация файла и коды классификатора разные оси (§1.5).

**Тесты — раскладка «функция × параметры = собрано»:**

| Тест | Параметров | Собрано |
|---|---|---|
| `по умолчанию видны только корни, «Нераспределённое» и итог` | — | 1 |
| `раскрытие показывает подстатьи` | — | 1 |
| `прочерк, «цена не заполнена» и ноль — три разных вида` | прочерк, без цены, ноль | **3** |
| `«Без подстатьи» есть, когда у статьи есть дети и свои деньги` | — | 1 |
| `«Без подстатьи» отсутствует при own_rows = 0` | — | 1 |
| `у листа с допработами служебная строка называется «Позиции сметы»` | — | 1 |
| `подпись служебной строки называет раздел сметы` | — | 1 |
| `подпись называет оба раздела, когда их два` | — | 1 |
| `строка допработ с бейджем внутри своей статьи` | — | 1 |
| `«Нераспределённое» видимо и названо` | — | 1 |
| `строки вне структуры названы отдельной причиной` | — | 1 |
| `корзина показана с бейджем` | — | 1 |
| `переключатель нулевых по умолчанию выключен` | — | 1 |
| `включение переключателя показывает нулевые подстатьи` | — | 1 |
| `подпись неполноты на смешанном узле` — при `rows = 10, rows_priced = 9` рядом с числом стоит «без цены: 1» | — | 1 |
| `подпись называет обе причины, когда обе есть` | — | 1 |
| `наименование статьи зажато по высоте и не несёт класса block` | — | 1 |
| **Итого** | | **19** |

**Ожидаемая дельта:** backend 0, vitest **+19** (223 → **242**).

---

### Задача 9 — кольцо структуры

**Файлы:** `frontend/src/pages/passport/StructureRing.tsx` — новый;
`ProjectPassportPage.test.tsx` — дополняется.

Строится на **shadcn `chart.tsx`** (уже в `components/ui/`) поверх `recharts`
(уже в зависимостях) — Global Constraint 25, кастомный SVG не пишем.

**Что реализуется (§2.10):** топ-8 статей верхнего уровня по сумме, плюс
«Остальные статьи (N)», плюс «Нераспределённое»; палитра — категориальный набор,
прошедший валидатор, в обеих темах; «Нераспределённое» **вдобавок заштриховано** —
цвет не единственный носитель смысла; легенда даёт идентичность текстом.

**Пустое состояние кольца.** При `totals.amount` пустом или равном нулю кольцо **не
строится вовсе**: ни одного деления не выполняется, вместо диаграммы и легенды
стоит «структура не строится: сумма по смете не определена» либо «…равна нулю» —
два разных случая, две разные формулировки. Статья с неизвестной суммой в кольцо
не попадает и в «Остальные» не входит.

**Замечание о наблюдаемости:** в jsdom `recharts` не даёт размеров, поэтому тесты
утверждают **легенду, тексты и пустые состояния**, а не геометрию секторов.
Геометрию и палитру проверяет замер в браузере (задача 12) — это названо, а не
замолчано.

**Красный прогон:** `Cannot find module './StructureRing'`.

**Тесты — раскладка «функция × параметры = собрано»:**

| Тест | Параметров | Собрано |
|---|---|---|
| `легенда несёт восемь крупнейших статей` | — | 1 |
| `девятая и дальше сведены в «Остальные статьи (N)»` | — | 1 |
| `«Нераспределённое» названо в легенде отдельно` | — | 1 |
| `при неопределённой сумме кольца нет и сказано, что сумма не определена` | — | 1 |
| `при нулевой сумме кольца нет и сказано, что сумма равна нулю` | — | 1 |
| `статья с неизвестной суммой не входит в «Остальные»` | — | 1 |
| **Итого** | | **6** |

**Ожидаемая дельта:** backend 0, vitest **+6** (242 → **248**).

---

### Задача 10 — печатная раскладка нового паспорта

Задача заведена находкой §1.6: печатный блок `index.css` глобален, написан под
**семиколоночную** таблицу фазы 6 и после подмены экрана применился бы к новой
таблице из **пяти** колонок. Ни удалить, ни оставить его нельзя — §2.11 требует от
Ф6 печатной раскладки.

**Файлы:** `frontend/src/index.css` — печатный блок переписывается под новую
таблицу, и вместе с ним **комментарий строк 341–347**: это десятое место из
списка §1.8, и владелец у него здесь, а не в задаче 11, потому что задача 11
переписывала бы текст блока, который эта задача заменяет целиком. Также
`frontend/src/pages/passport/*.tsx` (разметка получает `data-print`),
`ProjectPassportPage.test.tsx` — дополняется.

**Что реализуется (§2.11):**
- контракт `data-print` **переиспользуется**, а не заводится заново: `sheet` —
  лист документа, `hide` — служебные элементы (навигация, кнопка печати,
  переключатели), `clamp` — зажим по высоте, `row` — неразрывная строка;
- **шапка таблицы повторяется на каждом листе** — `thead { display:
  table-header-group }`;
- **строка не рвётся посередине** — `tr { break-inside: avoid }`;
- **кольцо печатается** (уменьшенным), служебные элементы уходят;
- ширины колонок переписываются с семи на **пять**; `table-layout: fixed` и
  проценты — экранные ширины в `rem` превышают печатное поле 190 мм, и без этого
  правые колонки срезались бы (замер фазы 6 — выход больше чем на 1100 px);
- **зажим не отменяется классом `block`** — прямая грабля §11 `AGENTS.md`, живая в
  репозитории у старого экрана.

**Красный прогон:** утверждение о наличии `data-print="sheet"` на листе нового
паспорта — `expect(received).not.toBeNull()` при `null`.

**Тесты — раскладка «функция × параметры = собрано»:**

| Тест | Параметров | Собрано |
|---|---|---|
| `лист документа помечен data-print="sheet"` | — | 1 |
| `служебные элементы помечены data-print="hide"` | переключатель нулевых, кнопка печати | **2** |
| `зажатые по высоте узлы не несут класса block` | — | 1 |
| **Итого** | | **4** |

**Что тестом не стережётся и объявляется границей:** сама печатная раскладка.
`@media print` в jsdom не наблюдаем — тесты стерегут только **разметку**, а
раскладку проверяет замер в браузере (задача 12). Названо, чтобы четыре зелёных
теста не читались как доказанная печать.

**Ожидаемая дельта:** backend 0, vitest **+4** (248 → **252**).

---

### Задача 11 — замена старого экрана, навигация, правки документов

**Порядок внутри задачи важен:** сначала переключить маршрут, потом удалять — иначе
дерево не собирается посередине.

**Файлы:**
- `frontend/src/App.tsx` — маршрут `/contracts/:contractId/passport` получает
  `ProjectPassportPage`; импорт `PassportPage` уходит;
- **удаляются:** `frontend/src/pages/passport/PassportPage.tsx`,
  `frontend/src/pages/passport/PassportPage.test.tsx`;
- `frontend/src/services/queries.ts` — `usePassport` удаляется;
- `frontend/src/services/queryKeys.ts` — `qk.passport.one` удаляется (корень
  остаётся);
- `frontend/src/services/api/analytics.ts` — `analyticsApi.passport` удаляется;
- `frontend/src/types/domain.ts` — `Passport`, `PassportContract`,
  `PassportEstimate`, `PassportKeyRate`, `PassportTotals` удаляются;
- `frontend/src/test/fixtures.ts` — `samplePassport` удаляется (типизирован
  удаляемым типом);
- `frontend/src/test/handlers.ts` — хендлер `/analytics/passport/:contractId` и
  флаг `passportWithoutEstimate` удаляются; **`handlerState.passportTopN` и
  хендлеры `/api/v1/settings` остаются** (§1.1 п. 2);
- `frontend/src/pages/settings/SettingsPage.test.tsx` — **второй `describe`
  удаляется целиком** вместе с импортом `PassportPage`; первые шесть тестов не
  трогаются;
- `frontend/src/components/layout/TopNav.tsx` — из `NAV` уходят «Матрица»,
  «Отчёты», «Настройки»; комментарий над массивом приводится в соответствие;
- `frontend/src/components/layout/TopNav.test.tsx` — **новый** (см. §1.3);
- `AGENTS.md` §7.4 и §10 — правки по §2.11 и §7 п. 1 спеки;
- `docs/phase7-frame.md` — в строке Ф6 колонка «Миграция» получает `0010`
  (§7 п. 2 спеки).

**Плюс перевод `passport_top_n` в исторический статус** — находка §1.8. Инвариант
«одна страница А4» снимается из `AGENTS.md` **этой же задачей**, поэтому все
**десять** мест, которые на него ссылаются, правятся здесь же, а не оставляются
противоречить документу. Девять из десяти — здесь; десятое (`index.css`)
принадлежит задаче 10, которая этот блок переписывает целиком.

| Файл | Что правится |
|---|---|
| `backend/crud/settings.py` | текст `422` больше не ссылается на `AGENTS.md §10` и не обещает одну страницу — он объясняет, что граница подобрана под раскладку **экрана фазы 6**; модульная докстрока и докстрока `get_passport_top_n` называют потребителя явно — паспорт **фазы 6** |
| `backend/models.py` | докстрока `PASSPORT_TOP_N_*` приводится к тому же |
| `backend/routers/settings.py` | модульная докстрока («паспорт читает `passport_top_n`») и докстрока схемы, обосновывающая отсутствие `Field(ge=…, le=…)` ссылкой на DoD §10 |
| `backend/main.py` | комментарий монтирования роутера: настройки относятся к **legacy-аналитике фазы 6**; право `member` при этом остаётся и его обоснование меняется на «чтение настроек — не admin-операция» |
| `backend/tests/integration/test_settings_api.py` | модульная докстрока; `test_out_of_range_message_explains_the_a4_reason` → `test_out_of_range_message_explains_the_reason`, утверждает **наличие объяснения**, а не слово «А4» — этого и требует его собственная докстрока, а утверждение проверяло формулировку |
| `frontend/src/pages/settings/SettingsPage.tsx` | подсказка под полем и комментарий; **метка поля не трогается** |
| `frontend/src/pages/settings/SettingsPage.test.tsx` | утверждение строки 41, текст подмены MSW и два комментария |
| `frontend/src/test/handlers.ts` | зеркало сообщения сервера |
| `frontend/src/services/queries.ts` | комментарий инвалидации: она больше ничего осмысленного не обновляет и названа **хвостом** (граница 11 спеки), а не рабочей связью |

**Что при этом НЕ меняется:** `CHECK` 1..20 миграции 0004 и сама колонка (§2.11
спеки — миграции у Ф6 на это нет, и удаление рабочего админского экрана шире
рамки); комментарий миграции 0004 — она обязана быть неизменной во времени и
описывает момент своего написания; число тестов — оба затронутых теста остаются
одним тестом каждый.

**Что НЕ удаляется** (§2.13, подтверждено на гейте 1): маршруты `/matrix`,
`/reports`, `/settings`, их компоненты, бэкенд-эндпоинты и все их тесты; экран
«Настройки», его эндпоинт и колонка `passport_top_n` с её `CHECK`. Инвалидация
`qk.passport.all` в `useUpdateAppSettings` **не удаляется** и названа хвостом
(граница 11).

**Красный прогон:** после удаления `PassportPage.tsx` и до правки
`SettingsPage.test.tsx` — `tsc` и vitest красные на `SettingsPage.test.tsx`; это и
есть предсказанный §1.1 факт, он **фиксируется замером**, а не обходится.

**Тесты — раскладка «функция × параметры = собрано»:**

| Тест (`TopNav.test.tsx`) | Параметров | Собрано |
|---|---|---|
| `в навигации нет «Матрица», «Отчёты», «Настройки»` | — | 1 |
| `в навигации есть «Главная», «Договоры», «Ручной матчинг», «Нормативы», «Пользователи»` — обязателен: иначе первый прошёл бы на пустой навигации (эталон не выводится из проверяемого — verifying-guards, слой 5). Админские пункты требуют роли `admin` — фикстура MSW | — | 1 |
| **Добавлено** | | **+2** |
| `PassportPage.test.tsx` удаляется целиком | | **−16** |
| второй `describe` в `SettingsPage.test.tsx` удаляется | | **−1** |
| **Итого** | | **−15** |

**Ожидаемая дельта:** backend 0 (**1413**), vitest **−15** (252 → **237**).
Падение на 17 объявлено заранее (§1.1 п. 1) и **не является регрессией**.

---

### Задача 12 — негативные проверки, соответствие, стенд, замер в браузере, devlog

Кода продукта не пишет, кроме правок по находкам.

1. **Негативные проверки снятием защиты — реестр §4, делает оркестратор лично.**
2. **Соответствие «требование спеки → тест»** — построить по тексту §2 спеки,
   требование за требованием ([replaying-new-rules](../../insights/replaying-new-rules.md),
   слой 3). Требование без исполнителя либо получает тест, либо **объявляется
   границей** в devlog; молчаливого третьего варианта нет.
3. **Стенд** — порядок §5.
4. **Замер печатной раскладки в браузере** — §6. Обязателен и незаменим.
5. **`just ci` целиком, шагами и в форме CI**, до пуша.
5а. **Контрольный сплошной поиск** по критерию §1.8: `А4` остаётся только в
   миграции 0004 и в паре `MatrixPage`, `DoD §10` / `AGENTS.md §10` — ни в одном
   живом файле как обоснование. Вердикт читается по **напечатанным путям**, а не
   по коду возврата `rg` (Global Constraint 12).
6. **Devlog** `docs/devlog/2026-08-10-project-passport.md`; врезка «Ф6 реализована»
   в `docs/phase7-frame.md`; PR со ссылками на спеку и план.
7. Инсайт заводить **только** если отсутствие правила уже стоило дефекта в этой
   фиче (`AGENTS.md` §12).

---

## 4. Реестр негативных проверок

Модель снятия названа для каждой. Вход каждого негативного теста обязан нарушать
**ровно одно** ограничение (Global Constraint 22). Схемные защиты снимаются
**правкой миграции** (Global Constraint 8). Числа красных — ожидания, подлежащие
замеру.

### 4.1. VIEW и схема (задача 1)

| № | Защита | Модель снятия | Ожидаемый исход |
|---|---|---|---|
| 1 | арм `<> 'NaN'` в `amount` | убрать **только** этот арм из `FILTER` у `SUM` | вход с одним `NaN` даёт `amount = NaN` |
| 2 | арм `<> 'Infinity'` | то же, только этот арм | вход с `Infinity` отравляет сумму |
| 3 | арм `<> '-Infinity'` | то же | вход с `-Infinity` отравляет сумму |
| 4 | счётчик `rows_not_finite` | `COUNT(*) FILTER (...)` → литерал `0` | счётчик молчит при живом фильтре: баннер не поднимется |
| 5 | `rows_with_amount` считает вошедших | `COUNT(*) FILTER (...)` → `COUNT(*)` | признак неполноты исчезает, `total` выглядит полным |
| 6 | `is_chapter = false` | убрать условие `WHERE` | итоги разделов входят в суммы — двойной счёт |
| 7 | путь через `chapter_item_id` | заменить join на равенство **номеров** раздела | вход «два раздела одного номера, разные статьи» задваивает деньги |
| 8 | `DROP VIEW` в `downgrade` | убрать строку | круговой рейс падает на `upgrade`: «relation already exists» |

Пункты 1–3 — **три отдельных снятия, а не одно**: у фильтра три независимых арма, и
снятие одного при живых двух говорит только о нём (слой 8). Комбинированное снятие
всех трёх заводится **отдельно**, потому что пара противоположных бесконечностей
даёт `NaN` при двух «годных на вид» слагаемых — исход, который из одиночных не
выводится.

| № | Защита | Модель снятия | Ожидаемый исход |
|---|---|---|---|
| 9 | фильтр годности целиком (пара слоя 8) | снять все три арма сразу | пара `+Infinity` / `−Infinity` даёт `NaN` |

### 4.2. Агрегация и ответ (задачи 2–5)

| № | Защита | Модель снятия | Ожидаемый исход |
|---|---|---|---|
| 10 | `amendment_no IS NULL` в CRUD | убрать условие | паспорт считает и ДС |
| 11 | `total = None` при `rows_priced = 0` | вернуть `Decimal(0)` вместо `None` | ноль вместо прочерка |
| 12 | свёртка счётчиков по поддереву | считать только собственные строки узла | неполнота не доезжает наверх |
| 13 | все 21 корня всегда | отдавать только присутствующие в смете | «ТХ» исчезает вместо прочерка |
| 14 | отсечение пустых глубже 1-го уровня | отдавать все 362 | ответ распухает, экран теряет форму |
| 15 | валовое: `null` при отсутствии ключа | суммировать по имеющимся | заниженное валовое читается как верное |
| 16 | валовое: фильтр `is_finite()` | убрать | `NaN` в валовом |
| 17 | сверка гаснет по **обоим** операндам | проверять только валовое | `None − Decimal` → `TypeError`, `500`. Наблюдаемое — отказ, а не неверное число, и это ровно то, что §2.5 предсказывает; записать наблюдаемое, а не «тест покраснел» |
| 18 | единогласие ставки НДС | брать первую попавшуюся | расхождение ставок молча даёт одну из них |
| 19 | `share_pct` = `null` при нулевом итоге | делить всегда | деление на ноль |
| 20 | `share_pct` = `null` при пустом итоге | делить всегда | деление на `None` |
| 21 | `per_sqm` = `null` при `NULL`-площади | считать всегда | падение либо ноль вместо прочерка |
| 22 | `ORDER BY proposal_id, ordinal` у `extras` | убрать `ORDER BY` | **структурный** тест формы SQL краснеет. Поведенческий — как повезёт: замер §1.7 показал, что при Index Only Scan он остаётся зелёным |
| 23 | явный порядок `own_sections` | убрать `ORDER BY` | то же, **структурный** тест |
| 24 | `decimal_json` на новом эндпоинте | вернуть голый `dict` | деньги в сыром теле становятся `float` |
| 25 | `own_sections` пусты при `own_rows = 0` | отдавать разделы всегда | подпись появляется у узла без собственных денег |

Пункты 19 и 20 разделены намеренно: у условия два арма (пусто и ноль), и вход
каждого теста нарушает ровно один.

Пункты 22 и 23 стерегутся **структурными** тестами, а не поведенческими, — это
прямое следствие замера §1.7. Поведенческие тесты порядка остаются
положительными проверками и в реестр не входят: их зелёность после снятия
означала бы не «защита есть», а «планировщик выбрал другой план».

### 4.3. Экран (задачи 6–11)

| № | Защита | Модель снятия | Ожидаемый исход |
|---|---|---|---|
| 26 | корень `qk.passport` переиспользован | сменить корень ключа в `useProjectPassport` | тест §1.2 краснеет; **старый тест `queries.test.tsx` остаётся зелёным** — это и доказывает, что дыра была реальной |
| 27 | `enabled` у хука паспорта | убрать | уходит запрос по несуществующему договору (дефект P3 Ф5) |
| 28 | баннер порчи только при `> 0` | показывать всегда | технический баннер в документе «для банка» |
| 29 | строка сверки только при `delta ≠ 0` | показывать всегда | то же |
| 30 | подпись неполноты при `rows_priced < rows` | убрать подпись | частичная сумма без признака |
| 31 | подпись называет **обе** причины | называть только негодные | текст уже собственного условия — ошибка, за которую заплатила Ф4a |
| 32 | служебная строка при `own_rows > 0` и наличии детей/допработ | показывать всегда | строка «Без подстатьи» у листа без своих денег |
| 33 | имя служебной строки по наличию детей | одно имя всегда | «Без подстатьи» у листа с допработами |
| 34 | подпись служебной строки берёт `own_sections` | подставить номер статьи вместо разделов | номер файла выдаётся за код классификатора — запрет §1.5 |
| 35 | кольцо не строится при пустом итоге | строить | деление на `None` |
| 36 | кольцо не строится при нулевом итоге | строить | деление на ноль |
| 37 | прочерки ₽/м² при отсутствии ТЭП | считать | ноль читается как «бесплатно» |
| 38 | переключатель нулевых выключен по умолчанию | включить | свёрнутый вид теряет форму |
| 39 | зажим наименования без класса `block` | добавить `block` рядом с зажимом | грабля §11 `AGENTS.md` воспроизводится: зажим отменяется |
| 40 | `TopNav` без трёх пунктов | вернуть пункт в `NAV` | новый тест §1.3 краснеет |

**Ожидание по числу: 40 снятий.** Если факт разойдётся — расхождение объясняется
целиком, как в Ф5 (§3.1 её devlog), а не подгоняется.

Снятие 39 — единственное, где «снятие» есть **добавление**: защита выражена
отсутствием класса, и сломать её можно только дописав его. Тот же приём, что у
проверки 11 Ф4б.

**Не участвуют в снятиях и названы заранее** (утверждают **отсутствие**, зелены и
до фичи — иначе их зелёность на контрольном прогоне читалась бы как
доказательство): `test_member_can_read_the_endpoint` (защиты нет, тест стережёт
будущую регрессию прав) и утверждение о присутствии остальных пунктов навигации.

**Не проверяются снятием и объявляются границами:** **действие** зажима по высоте
и печатная раскладка целиком — снятие 39 стережёт **разметку**, а не результат в
пикселях; `@media print` и `line-clamp` в jsdom не наблюдаемы, их проверяет только
замер в браузере (§6). Туда же палитра и геометрия кольца. Отсутствие
Excel-выгрузки и отсутствие починки `_money` — отсутствие кода тестом не
проверяется (границы 12 и 14 спеки).

---

## 5. Порядок работы со стендом

Стенд `gca_dev` — 3 объекта, 3 договора, 3 подрядчика, 3 сметы, каталог 1932,
`import_jobs` 9, `position_items` 5465, `proposal_summary_lines` 8, `vat_rate`
20/20/NULL, `parser_version` 3.1.0 у всех трёх. У объекта 1 площади заведены, у
договора 1 три условия с оговорками; объекты и договоры 2 и 3 пусты — законное
состояние «ТЭП не заведены», удобное для проверки бейджа.

**Отличие от предыдущих фич: перезаливать сметы НЕ НАДО.** Привязки Ф3 и записи Ф4
на стенде уже есть, парсер `3.1.0`. Повторная загрузка неизменённого файла
идемпотентна, и `replace=true` её не перебивает (правило 1 `AGENTS.md` §5
проверяется раньше правила 2) — но Ф6 это и не нужно.

**Порядок:**

1. Снять счётчики **до** всяких действий: объекты, договоры, подрядчики, сметы,
   каталог, `import_jobs`, `position_items`, `proposal_summary_lines`,
   `estimate_additional_works`, `alembic head`. Числа — вход сверки «до и после».
2. **Спросить разрешение пользователя** на доведение стенда до `0010`.
3. Довести схему рецептом **`just db-dev-init`** — единственное место, где целью
   законно является стенд. `downgrade` на стенде **не выполняется**.
4. Поднять dev-серверы (8259 и 5173), войти админом (учётные данные — в
   `docs/phase2-start.md`, пароль в чат не переносить).
5. Открыть паспорт каждого из трёх договоров. Сверить с независимым замером из БД:
   инвариант «статьи + нераспределённое = позиции + допработы» и **дельту с валовым
   ИТОГО** (ожидание — ноль на всех трёх, замер §1.2 спеки). У 449-ТУ ожидается
   непустое «Нераспределённое» (38 позиций, около 2,3 % сметы), у двух других —
   пустое.
6. Проверить бейдж «ТЭП не заведены» на договорах 2 и 3 и заполненные показатели
   на договоре 1.
7. Сверить счётчики **после**: паспорт — чтение, ни одно число измениться не
   должно.

**`POST` требует `X-CSRF-Token`, совпадающий с кукой `csrf_token`**, иначе `403`
читается как отказ по правам; роутер аутентификации смонтирован на `/api/auth`, а
не `/api/v1/auth`. Ф6 ничего не постит, но вход в приложение это затрагивает.

---

## 6. Замер в браузере — обязателен и незаменим

Окружение переиспользуется, **не пересобирается**: `playwright-core` в
`%TEMP%\gca-pw` (вне репозитория), системный Chrome
`chromium.launch({ channel: "chrome" })`, при корпоративном TLS —
`$env:NODE_OPTIONS='--use-system-ca'`. Рабочие `shoot.mjs` (состояния и измерения)
и `pdf.mjs` (рендер в A4 и счёт страниц) там же. **Моста Playwright MCP в среде
нет** — проверено, отвечает «Extension connection timeout»; шагов на него не
тратить.

Замер идёт **против живого стенда**, а не против макета: макет уже замерен
(0 сообщений в консоли в шести состояниях, нет обрезки на 1280 и 720 px и в печати,
A4 PDF — 2 листа свёрнутым и 4 полностью раскрытым), и повторять его нечего.

**Что обязано быть замерено (§4.4 спеки):**

| Что | Критерий |
|---|---|
| печатная раскладка на реальной смете стенда | **нет обрезки по правому краю**; строки **не рвутся** посередине; шапка таблицы **повторяется** на каждом листе |
| узкий экран (720 px) | нет горизонтальной прокрутки страницы; таблица прокручивается **внутри своего контейнера** |
| тёмная тема | фон и цвета взяты из токенов, кольцо различимо |
| консоль | **0** сообщений об ошибках и предупреждениях |
| длинные оговорки и длинные наименования | зажаты по высоте, лист не растёт |

Обязательство `AGENTS.md` §11 у Ф6 читается как «нет обрезки по правому краю и
разрывов внутри строк», **а не «один лист»** (§2.11) — печать многостраничная по
решению гейта 1.

---

## 7. Ожидаемые числа по задачам

Ожидания, подлежащие замеру после **каждой** задачи и **пофайлово**. Расхождение —
сигнал дефекта.

| Задача | backend собрано | vitest |
|---|---|---|
| база | **1337** | **206** |
| 1 — VIEW и миграция 0010 | 1351 (+14) | 206 |
| 2 — чистый roll-up | 1369 (+18) | 206 |
| 3 — итоги, доли, руб/м² | 1383 (+14) | 206 |
| 4 — валовое, НДС, сверка | 1397 (+14) | 206 |
| 5 — `extras`, `own_sections`, эндпоинт | 1413 (+16, из них 1 auth-coverage) | 206 |
| 6 — типы, хук, MSW | 1413 | 208 (+2) |
| 7 — шапка экрана | 1413 | 223 (+15) |
| 8 — таблица | 1413 | 242 (+19) |
| 9 — кольцо | 1413 | 248 (+6) |
| 10 — печатная раскладка | 1413 | 252 (+4) |
| 11 — замена экрана, навигация, `passport_top_n` | 1413 | **237 (+2 −17)** |
| 12 — финал | 1413 | 237 |

Каждая дельта выведена из таблицы «функция × параметры = собрано» в своей задаче;
арифметика проверяема, а не заявлена. Параметризации, дающие больше одного
собранного теста: задача 1 — негодные значения (3); задача 2 — счётчики (3) и
состояния `own` (3); задача 3 — негодный итог (2) и `per_sqm` (2); задача 4 —
негодное валовое (3); задача 5 — ветви ответа (4); задача 7 — четыре состояния (4);
задача 8 — три вида числа (3); задача 10 — служебные элементы (2).

**Падение vitest на 17 в задаче 11 объявлено заранее и регрессией не является:**
16 тестов `PassportPage.test.tsx` уходят вместе с экраном фазы 6, 1 тест —
второй `describe` `SettingsPage.test.tsx`, который рендерит этот экран (§1.1).

Пропусков backend по-прежнему **6** — известные из `test_auth_coverage.py`.
Итоговый контрольный прогон: **1413 собранных / 1407 passed / 6 skipped**,
vitest **237**.

---

## 8. Что план сознательно не делает

Ссылки, а не пересказ: спека §3 («Что сознательно НЕ делается»), §5 (семнадцать
названных границ), §6 (пятнадцать отвергнутых альтернатив) — это **принятые
решения**, и план их исполняет.

Отдельно названо, потому что касается порядка работ:

1. **`_money` Ф6 не чинит** — открытый хвост Ф4, его правка касается всех денежных
   входов сразу. Ф6 защищена на своей границе (фильтр годности в VIEW), факт порчи
   становится громким (баннер), прочие потребители не защищены. Расширять последнюю
   фичу фазы до чужого контура — рисковать приёмкой.
2. **Backfill и принудительный репарсинг не делаются** — решение пользователя, хвост
   Ф4a, в силе.
3. **Экраны фазы 6 не удаляются** — уходят только три пункта навигации. Фаза 6
   вернётся drill-down'ом из статьи.
4. **Выгрузки в Excel у Ф6 нет** — рамка называет составом Ф6 VIEW, экран и скрытие
   экранов фазы 6.

---

## 9. Чек-лист

- [ ] **Задача 1** — миграция 0010, `v_category_totals`, отражение в
      `crud/project_passport.py`, `test_category_totals_view.py`, круговой рейс на
      `gca_test`. Замер: 1351 / 206.
- [ ] **Задача 2** — `services/category_rollup.py`, юнит-тесты дерева и счётчиков.
      Замер: 1369 / 206.
- [ ] **Задача 3** — исходная смета, итоги, доли, руб/м², нераспределённое.
      Замер: 1383 / 206.
- [ ] **Задача 4** — валовое ИТОГО, ставка НДС, сверка по двум операндам.
      Замер: 1397 / 206.
- [ ] **Задача 5** — `extras` и `own_sections` отдельными запросами со
      структурной защитой порядка, эндпоинт, `decimal_json`, права.
      Замер: 1413 / 206.
- [ ] **Задача 6** — типы, транспорт, `useProjectPassport`, MSW, тест корня кэша
      и `enabled`. Замер: 1413 / 208.
- [ ] **Задача 7** — шапка, показатели, оговорки, бейджи, сверка, баннер.
      Замер: 1413 / 223.
- [ ] **Задача 8** — таблица по статьям целиком. Замер: 1413 / 242.
- [ ] **Задача 9** — кольцо структуры и его пустые состояния. Замер: 1413 / 248.
- [ ] **Задача 10** — печатная раскладка: контракт `data-print`, переписанный
      блок `@media print` под пять колонок. Замер: 1413 / 252.
- [ ] **Задача 11** — маршрут, удаление старого экрана, правка
      `SettingsPage.test.tsx`, `TopNav` −3 пункта и его тест, `passport_top_n` в
      исторический статус (семь мест), правки `AGENTS.md` §7.4/§10 и рамки фазы.
      Замер: 1413 / **237**.
- [ ] **Задача 12** — 40 негативных проверок, соответствие «требование → тест»,
      стенд, замер в браузере, `just ci`, devlog, PR.
