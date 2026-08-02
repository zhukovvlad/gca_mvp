# Тестирование

Живой документ о текущем состоянии тестовой инфраструктуры. Обновлять при каждом
существенном изменении (добавил тесты, перенастроил CI, поднял coverage threshold).

**Spec:** `docs/superpowers/specs/2026-05-11-testing-infrastructure-design.md` (целевая архитектура).
**Plan:** `docs/superpowers/plans/2026-05-11-testing-infrastructure.md` (пошаговый план на 5 этапов).

---

## TL;DR — текущее состояние

| Слой | Файлов | Тестов | Статус |
|---|---|---|---|
| Backend unit | 26 | 273 | ✅ |
| Backend integration | 28 | 332 | ✅ |
| Backend top-level | 1 | 74 | ✅ |
| **Backend total** | **55** | **679** | ✅ |
| Frontend (Vitest + RTL + MSW) | 29 | 222 | ✅ |
| E2E (Playwright) | — | — | ⏳ отложено |
| GitHub Actions CI | — | backend ✅ / frontend ✅ | — |
| **Grand total (локально)** | **84** | **901** | ✅ |

Последний прогон `just test` локально: backend **673 passed / 6 skipped** (`uv run pytest`, 679 собрано; локальный Postgres), frontend **222 passed** (29 файлов). CI настроен для backend (`.github/workflows/backend-tests.yml`) и frontend (`.github/workflows/frontend-tests.yml`) — оба гоняются в GitHub Actions на каждый push в `main` и PR.

---

## Быстрый старт

```bash
# Установить всё (backend + frontend)
just install

# Backend — TEST_DATABASE_URL указывает на локальный udp_test; кластер поднимается автоматически
just test-backend            # все backend-тесты, на локальном Postgres (~30 сек)
just test-backend-unit       # быстро (без БД): 155 PASSED за ~1 сек
just test-backend-integration # с реальной Postgres: 220 PASSED
just coverage-backend        # HTML отчёт в backend/htmlcov/

# Backend против ЛОКАЛЬНОГО Postgres (~6.5x быстрее Neon; установка — см. ниже)
just test-int-local          # integration на localhost:5459
just test-int-local-k "patt" # точечный локальный прогон
just test-backend-local      # весь backend на localhost:5459

# Frontend — без БД, всё через MSW-моки
just test-frontend           # 219 PASSED за ~33 сек
just test-frontend-watch     # watch режим
just test-frontend-ui        # @vitest/ui дашборд в браузере
just coverage-frontend       # HTML в frontend/coverage/

# Lint
just lint                    # backend (ruff) + frontend (eslint)
just typecheck-frontend      # tsc --noEmit
```

`just install` создаёт изолированный `backend/.venv` из `uv.lock` (uv sync) — отдельный venv активировать не нужно, все рецепты идут через `uv run`.

### Конфигурация

- **`.env.test`** в корне репо (в `.gitignore`) с `TEST_DATABASE_URL` — локальный `udp_test`
  на том же кластере, что `udp_dev`, **не прод**: `TEST_DATABASE_URL` не должен указывать
  на прод-БД ни при каких обстоятельствах, независимо от вендора.
  Шаблон: `.env.test.example`. Префикс должен быть `postgresql+psycopg://`.
- В CI (backend-tests.yml) переменные приходят из GitHub Actions env/secrets, `.env.test` не нужен.
- **`TEST_DATABASE_URL` не появляется в raw shell env автоматически.** Внутри
  `pytest` её подхватывает плагин `pytest-dotenv` через `env_files = [".env.test"]`
  (`backend/pyproject.toml`, `[tool.pytest.ini_options]`) — переменная видна
  ТОЛЬКО внутри процесса `pytest`. Любой ad-hoc `psql`/`alembic` вне pytest её не
  увидит, если `.env.test` не подгружен в сам шелл вручную (`export $(cat .env.test)`
  / аналог в PowerShell) — это ожидаемое поведение `pytest-dotenv`, не баг.
  `just db-test-migrate` этого ограничения не касается: DSN подставлен инлайн
  через `{{test_db_local}}`, `.env.test` рецепт не читает вовсе.

---

## Локальный тестовый Postgres (быстрые integration)

**Зачем.** До ревизии §12 спеки `TEST_DATABASE_URL` по умолчанию указывал на Neon
(eu-central-1) — каждый SQL-запрос платил ~43 мс сетевого RTT, и 324
integration-теста шли 6–8 минут (замер 2026-07-20: `test_invoices.py`, 59 тестов —
63 с). Против localhost тот же файл проходит за 9.7 с (~6.5x) — эта разница и
стала одним из аргументов в пользу исключения Neon test-ветки. CI уже работает на
локальном стеке (`pgvector/pgvector:pg16` service-container, полный прогон ~1
мин) — текущая установка воспроизводит тот же стек без Docker и без админ-прав.

**Что установлено.** PostgreSQL 16 + pgvector 0.8.3 из conda-forge через
портативный micromamba, целиком в профиле пользователя:

- Окружение: `%LOCALAPPDATA%\Programs\udp-pgtest` (бинарники в `Library\bin`),
  кластер — в `...\udp-pgtest\data`, лог — `data\log.txt`.
- Порт **5459**, auth `trust` — только localhost. Не 5433: машина общая, а
  инсталляторы Postgres занимают 5432 и далее инкрементом (5433, 5434…), так что
  5433 — первый кандидат на захват соседом. 5459 из этой последовательности
  выпадает и лежит ниже эфемерного диапазона Windows (49152+). Порт задан
  переменной `pg_port` в justfile и строкой `port` в `data/postgresql.conf` —
  менять надо в обоих местах. `just pg-test-start` делает preflight-проверку:
  если порт занят чужим процессом, падает с внятным сообщением, а не с
  загадочной ошибкой коннекта.
- Один кластер держит несколько баз:
  - `udp_test` — тесты; conftest сам делает `DROP SCHEMA public CASCADE` +
    Alembic на каждую pytest-сессию;
  - `udp_dev` — локальная dev-БД (см. «Локальная dev-БД» ниже).
- Сервер НЕ служба Windows: после перезагрузки его поднимает `just pg-test-start`
  (вызывается автоматически из `test-int-local` / `test-backend-local`).

**Установка с нуля (новая машина), без админ-прав:**

```bash
cd "$LOCALAPPDATA/Programs"
curl -L -o micromamba.exe "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64.exe"
./micromamba.exe create -y -p "$LOCALAPPDATA/Programs/udp-pgtest" -c conda-forge "postgresql=16" pgvector
BIN="$LOCALAPPDATA/Programs/udp-pgtest/Library/bin"
DATA="$LOCALAPPDATA/Programs/udp-pgtest/data"
"$BIN/initdb.exe" -D "$DATA" -U postgres -E UTF8 --locale="en-US"
echo "port = 5459" >> "$DATA/postgresql.conf"
"$BIN/pg_ctl.exe" -D "$DATA" -l "$DATA/log.txt" start
"$BIN/psql.exe" -h localhost -p 5459 -U postgres -c "CREATE DATABASE udp_test;"
```

Проверка: `just test-int-local-k "test_upload_creates_supplier_record"`.

**Важно про локаль:** `--locale=C` НЕ подходит — в C-локали кириллица не
считается alnum, `pg_trgm` строит пустые триграммы и
`test_duplicates_finds_similar_names` падает (similarity=0). Нужна UTF-8-совместимая
локаль (`en-US`): на Windows PostgreSQL использует wide-char CRT-функции, и
кириллица корректно классифицируется/фолдится. ICU в conda-forge win-64 сборке
не собран (`initdb --locale-provider=icu` → «ICU is not supported in this build»).

`.env` не меняется: рецепты `*-local` передают `TEST_DATABASE_URL` инлайн —
шелл-переменная имеет приоритет над значением из dotenv внутри pytest.

`just test-backend` (и, следовательно, `just test`) требует локального кластера —
фолбэк на Neon свёрнут ревизией §12 спеки, Neon test-ветка исключена. Если
каталог `%LOCALAPPDATA%\Programs\udp-pgtest\data` отсутствует, зависимость
`pg-test-start` падает с внятным сообщением («Локальный Postgres не установлен —
см. docs/testing.md»), а не тихим фолбэком. Контрибьютора без локальной установки
обслуживает CI: там `uv run pytest` идёт напрямую, минуя `just`, против
service-container Postgres.

---

## Локальная dev-БД (`udp_dev`)

**Зачем.** Разработка шла на живой Neon-базе, и 2026-07-25 это сломалось: на
машину раскатали корпоративную TLS-инспекцию (CA `Generic Root CA 3`), а
`channel_binding=require` в `DATABASE_URL` её честно детектирует — клиент считает
SCRAM channel binding по подменённому сертификату, прокси Neon по своему,
результат `insecure connection: secure channel data mismatch` и падение lifespan
на startup-sweep. Локальный Postgres от корпоративной сети не зависит вовсе.

Версии совпадают точно: Neon 16.14 и локальный кластер 16.14, `vector` +
`pg_trgm` есть в обоих — схема ложится один-в-один.

Neon с этой машины недостижим из-за корпоративной TLS-инспекции; для дев-цикла
и тестов это неважно — обе цели локальные.

`udp_dev` живёт в том же кластере, что `udp_test` (порт 5459). Это безопасно:
conftest дропает схему только в своей базе, а guard в `conftest.py` дополнительно
отказывается работать, если `TEST_DATABASE_URL` совпал с `DATABASE_URL`.

**Инициализация с нуля:**

```bash
just db-dev-init                        # createdb udp_dev + alembic upgrade head
just create-superuser admin@local.dev   # пароль спросит интерактивно
just create-org "Локальная разработка"
just create-user dev@local.dev 1 admin  # пользователь в организации id=1
```

**Запуск:** `just dev-backend`. `.env` при этом не меняется — `DATABASE_URL`
передаётся инлайн, а переменная окружения имеет приоритет над dotenv
в pydantic-settings.

Данных в `udp_dev` нет и не будет — PDF заливаются заново. Объекты в MinIO от
прежней работы остаются, но новая БД про них не знает.

### Переключатель `db_target`

Цель БД для рецептов, работающих с данными (`dev-backend`, `db-migrate`,
`create-*`), задаётся переменной `db_target` в justfile:

| Значение | Поведение |
|---|---|
| `local` (**дефолт**) | локальный DSN подставляется инлайн, `.env` не участвует |
| `env` | `DATABASE_URL` берётся из `backend/.env`, каким бы он ни был |

```bash
just dev-backend                    # локальная udp_dev
just db_target=env dev-backend      # что стоит в backend/.env
UDP_DB_TARGET=env just db-migrate   # то же через переменную окружения
```

Значение называется `env`, а не `neon`, потому что описывает **источник** строки,
а не её содержимое: у контрибьютора в `.env` может стоять свой Postgres, и цель
`local` жёстко прошита под конкретный кластер (`pg-test-start` без него падает).
Разрешение мутировать из выбора `env` **не** следует — оно отдельное (см. «Guard от
мутации незапланированной БД» ниже), иначе защита тихо снималась бы тому, чья
цель в `.env` guard'ом вообще не разрешена.

Имя переменной окружения с префиксом (`UDP_DB_TARGET`, не `DB_TARGET`): машина
общая, а мусорное значение ломает разбор **всех** рецептов, включая `lint`.

Любое другое значение — ошибка на этапе разбора justfile, а не молчаливый
фолбэк. Рецепты уважают цель через зависимость `pg-ensure`: при `local` она
поднимает кластер, при `env` — no-op.

`db-dev-init` переключателю не подчиняется: `createdb` — операция над локальным
кластером по определению.

### Guard от мутации незапланированной БД

[`backend/db_guard.py`](../backend/db_guard.py) отсекает мутирующие операции **до**
попытки коннекта. Ось — **роль окружения** (`APP_ENV`), а не вендор БД: прод не
обязательно Neon — развёртывание возможно и на сервере компании (локальный Postgres
или докер), и на стороннем managed-хостинге. Вендорная ось не защищала бы первый
случай и ломала бы второй.

| `APP_ENV` | Поведение guard'а |
|---|---|
| `dev` (**дефолт**) | мутировать разрешено loopback-целям (`localhost`/`127.0.0.1`/`::1`, любая база на них) и целям, перечисленным в `DB_EXTRA_TARGETS` |
| `prod` | цели не проверяются вовсе — декларация роли и есть разрешение: цель прода и есть её `DATABASE_URL` |

**Единица сравнения — `host:port/dbname`.** `normalize_target()` приводит DSN к
этому виду через `_resolve_connect_target()` — цель читается из `create_connect_args()`
того же SQLAlchemy-диалекта, которым реально подключается psycopg (а не из
хендролленного парсинга netloc), то есть то же самое, что увидел бы `psycopg.connect(...)`.
Имя БД входит в единицу сравнения, потому что часто это единственное, чем различаются
цели на одном хосте — иначе `localhost:5432/postgres` и `localhost:5432/udp_test` были
бы неразличимы. Query-параметры (`sslmode`, `channel_binding` и пр.) отбрасываются —
цели, различающиеся только ими, это одна цель.

**Правило, а не список патчей.** Guard отказывает мутировать (даёт нераспознанную цель),
если ЛЮБОЙ источник, невидимый на уровне SQLAlchemy, может ПЕРЕНАПРАВИТЬ конечную точку
подключения относительно того, что показывает DSN — не «поле отсутствует», а «источник
способен изменить цель даже когда DSN называет её явно». Отсутствие поля в DSN — другая,
безопасная категория: см. `PGHOST`/`PGDATABASE` ниже.

**Опасные источники — цель-определяющие, дают безусловный отказ (`UNKNOWN_HOST`-форма, не
совпадает ни с loopback, ни с записью `DB_EXTRA_TARGETS`):**

- ключи `host`, `port`, `dbname` в query-строке — SQLAlchemy-диалект подставляет их поверх
  netloc при сборке connect-args, замещая, а не дополняя его;
- ключ `hostaddr` в query-строке и переменная окружения **`PGHOSTADDR`** — если задан И
  `host`, И `hostaddr`(-подобный источник), реальный сетевой адрес — `hostaddr`, `host`
  используется только для TLS-верификации имени. Симметрия query/env специально проверена:
  SQLAlchemy не читает `PGHOSTADDR` вовсе (это подставляет только реальный libpq в момент
  коннекта), поэтому loopback-DSN с `PGHOSTADDR` в окружении процесса раньше проходил guard
  как loopback, хотя реальный коннект уходил на адрес из переменной — дыра ниже уровня,
  который видит `create_connect_args`;
- ключ `service` в query-строке и переменная окружения **`PGSERVICE`** — тянут host/port/
  dbname из `pg_service.conf`, файла, которого guard не видит: цель принципиально неизвестна;
- переменная окружения **`PGPORT`**, но только для DSN БЕЗ явного порта: явный порт в DSN
  сохраняет приоритет (не заменяется). Отсутствующий порт резолвится через `PGPORT` тем же
  образом, что и реальный libpq (DSN → service-файл → env → built-in `5432`); `PGPORT`,
  заданный значением вне ASCII-цифр 0-65535, тоже даёт нераспознанную цель, а не тихий откат
  на `5432`. Это ограничение самого guard'а, а не пересказ поведения libpq: libpq порт как
  число не валидирует (нечисловое значение — имя сервиса для `getaddrinfo` на TCP или часть
  имени файла сокета для Unix-домена), но единица сравнения `DB_EXTRA_TARGETS` — числовая
  тройка `host:port/dbname` по построению, и порт, который guard не может выразить числом,
  сравнить он тоже не может — отсюда отказ, а не признак того, что сам DSN «сломан».

(см. спеку §14/§16/§17 — §14 заменяет более ранний, точечный механизм §13; §16 — `PGPORT`;
§17 — `PGHOSTADDR`/`PGSERVICE`). `#` и второй `/` в пути (`.../dev#archive`, `...//dev`) —
часть имени БД **буквально**, а не разделитель fragment или пути: SQLAlchemy (и реальный
psycopg-коннект) не знает понятия URL-fragment вовсе, поэтому запись `DB_EXTRA_TARGETS` для
`dev` не пропускает DSN, целящийся на `dev#archive`/`/dev`. Пустой или неразбираемый хост
даёт маркер `UNKNOWN_HOST` (`"<нераспознанный хост>"`) — такая цель не loopback и ни с чем
не совпадает: fail-closed, а не молчаливое разрешение.

**`PGHOST`/`PGDATABASE` и прочие `PG*`-переменные — уже fail-closed, проверено, а не
предположено.** В отличие от `PGHOSTADDR`/`PGSERVICE`, эти две переменные — дефолты для
ПОЛЯ, ОТСУТСТВУЮЩЕГО в DSN, а не отдельные параметры, замещающие явно заданное значение.
SQLAlchemy не читает ни ту, ни другую при сборке connect-args/`make_url` вообще, ни когда
поле в DSN отсутствует, ни когда оно задано явно (проверено эмпирически на этом чекауте
для обоих случаев) — поэтому hostless DSN как давал `UNKNOWN_HOST`, так и даёт, а
dbname-less DSN как давал форму `host:port/` (которую `parse_extra_targets` в принципе не
может выдать валидной записью), так и даёт. Остальные стандартные libpq-переменные
(`PGSSLMODE`, `PGCONNECT_TIMEOUT`, `PGAPPNAME`, `PGOPTIONS`, `PGPASSFILE`, `PGSERVICEFILE`
без `PGSERVICE`) влияют на КАК подключаться (опции сессии, аутентификация, поиск файлов),
а не КУДА — та же категория, что и query-параметры вроде `sslmode`/`channel_binding`.

**Известные ложные отказы** (denies, не false-positive allow — безопасное направление,
но стоит знать заранее, чтобы не удивляться при встрече):

- **Unix-сокет через `?host=/var/run/postgresql`** — идиома psycopg для сокет-файла
  вместо TCP; самая вероятная из этого списка на практике. Попадает в
  `host`-в-query-ключ выше и получает безусловный отказ, хотя сокет-соединение по духу
  не менее «локально», чем TCP на `127.0.0.1`;
- **`127.0.0.2`** (и весь `127.0.0.0/8`, кроме литерала `127.0.0.1`) — не входит в
  `LOOPBACK_HOSTS`, хотя вся подсеть маршрутизируется на loopback-интерфейс;
- **`0.0.0.0`** — не входит в `LOOPBACK_HOSTS`;
- **Развёрнутая форма IPv6-loopback** (`0:0:0:0:0:0:0:1` вместо `::1`) — сравнение с
  `LOOPBACK_HOSTS` строковое, не по факту адреса, поэтому не совпадает с `::1`.

`LOOPBACK_HOSTS` сознательно не расширяется под эти случаи (см. докстринг модуля) —
единственный обходной путь сегодня — явная запись в `DB_EXTRA_TARGETS` там, где это
возможно (не работает для Unix-сокета — см. абзац про query-ключи выше: `host` в
query безусловно нераспознан, независимо от значения).

**Три точки, где guard подключён:**

| Точка | Что закрывает |
|---|---|
| `alembic/env.py` | любой DDL — вызов `ensure_mutation_allowed` стоит на уровне модуля, до ветвления online/offline и до `engine_from_config` |
| `cli.py` (`_guard`) | `create-superuser`, `create-org`, `create-user` — до открытия сессии |
| `main.py` (`_sweep_stuck_documents`) | startup-sweep: на каждом старте переводит `pending`/`processing` в `error`, так что `uv run uvicorn main:app` вместо `just dev-backend` испортил бы живые документы незапланированной цели |

В `main.py` guard стоит только на ветке `session_factory is None` — «приложение
поднимается по-настоящему». Инжектированная фабрика (тесты) его не задевает,
поэтому integration-тестам не нужно разрешение из-за строки в `.env`.

**`DB_EXTRA_TARGETS`** — список полных троек `host:port/dbname` через запятую, для
долгоживущих не-loopback dev-целей (пример — докерный `db` на отдельном хосте).
Каждая запись обязана быть **полной тройкой**: порт — ASCII-цифры в диапазоне
0–65535 (`parse_extra_targets` проверяет `isascii()`, а не только `isdigit()`:
юникодные цифры вроде «²» проходят `isdigit()`, но падают в `int()` чужой трассой),
хост не равен маркеру `UNKNOWN_HOST` (иначе запись разрешила бы мутацию для любого
хостless/неразбираемого DSN с тем же портом и БД). Запись без порта или без имени
БД — тоже ошибка конфигурации, а не «вся база на этом хосте». Сейчас
`DB_EXTRA_TARGETS` **пуст и не имеет ни одного потребителя по design** — ревизия
спеки (`docs/superpowers/specs/2026-07-27-deploy-env-contract-design.md` §12)
исключила из плана Neon test-ветку как кандидата на эту роль; не описывать её
здесь как пример использования переменной.

**Что поглощено allowlist'ом, а что нет.** Членство в `DB_EXTRA_TARGETS` (или
статус loopback) означает «можно мутировать» — это НЕ то же самое, что «можно
разрушить схему». `conftest.py` перед `DROP SCHEMA public CASCADE` держит два
независимых рубежа плюс сам вызов guard'а:

1. `TEST_DATABASE_URL` не совпадает с `DATABASE_URL` после нормализации — защита
   от опечатки конфигурации, ведущей на прод;
2. имя целевой БД оканчивается на `_test` — защита от `udp_dev`, которая для
   guard'а легитимная loopback-цель (мутировать её можно), но недопустима как
   цель `DROP SCHEMA`; `udp_dev` и `udp_test` различаются четырьмя символами;
3. `ensure_mutation_allowed(test_url, "conftest DROP SCHEMA")` — закрывает случай,
   не пойманный барьерами выше: удалённая `_test`-база, которая не loopback и не
   в `DB_EXTRA_TARGETS`.

Барьер `_test` не поглощается allowlist'ом намеренно: allowlist выражает право
мутировать, а не право уничтожить схему — это разные права, и `udp_dev` обязана
остаться в первом множестве, но никогда не попасть во второе.

**Барьер (a): обе стороны обязаны резолвиться, и сравниваются тройки, а не строки.**
`db_engine` (`conftest.py`) резолвит и `TEST_DATABASE_URL`, и `DATABASE_URL` через
`_resolve_connect_target` — если любая из сторон нераспознана (окружение процесса несёт
`PGHOSTADDR`/`PGSERVICE`, `PGPORT` вне 0-65535, либо сам DSN не разбирается/несёт
цель-определяющий query-ключ), фикстура падает `RuntimeError` ДО сравнения, а не
продолжает. Барьеры (a) и (b) при этом не ослаблены — это предварительная проверка их
собственной предпосылки (доверенного резолва с обеих сторон).

Сравниваются именно резолвленные кортежи `(host, port, dbname)`, а не строки
`normalize_target()` — это оказалось критично, а не деталью реализации. Первая версия
проверяла только `TEST_DATABASE_URL` и сравнивала строки: если поражены ОБЕ стороны
одинаково (например, `PGHOSTADDR`/`PGSERVICE` — проверяются безусловно, до разбора
конкретного DSN), обе давали один и тот же `UNKNOWN_HOST`-сентинел, строки совпадали, и
барьер решал «цели одинаковы» — случайно safe, но по факту ничего не доказывал. Если же
нераспознанной была ТОЛЬКО прод-сторона (`DATABASE_URL` сам несёт `?dbname=`/`service=` и
т.п.), `test_url` давал реальную строку, `prod_url` — сентинел, строки никогда не
совпадали — и `DROP SCHEMA` уходил вперёд, даже если реальная (нерезолвленная) прод-цель
была той же базой, что и `TEST_DATABASE_URL`. Сравнение кортежей вместо строк убирает
сентинел из сравнения целиком: обе стороны обязаны резолвиться порознь (иначе — громкий
отказ, не skip), и только после этого их РЕАЛЬНЫЕ тройки сравниваются напрямую.

**Осознанное ужесточение.** Разрешение для прод-цели — не рантайм-грант (guard не
детектирует «это прод» эвристикой по хосту/DSN), а одноразовая декларативная
запись роли: `APP_ENV=prod` в конфиге развёрнутого окружения. Дороже в настройке,
зато явно и не зависит от того, насколько DSN похож на прод. Осознанная миграция
прод-цели с дев-машины — тот же принцип: `APP_ENV=prod just db_target=env db-migrate`.

**Loopback — не без нюансов.** «Любая база на loopback мутируема без allowlist»
держится, только пока loopback — действительно локальный процесс. SSH-туннель или
`kubectl port-forward`, пробрасывающие удалённую БД на локальный порт, делают её
неотличимой от настоящего localhost — guard её пропустит без записи в
`DB_EXTRA_TARGETS`. Это осознанный компромисс дизайна (loopback как источник
доверия), не дыра в проверке — но стоит держать в уме при таком паттерне работы.

Guard не покрывает сырой SQL за пределами трёх точек выше — `DROP SCHEMA` в
conftest защищён отдельными барьерами (см. выше), а не только вызовом guard'а.

**`db-test-migrate` и `override=False`.** Рецепт минует `db_target` и подаёт
целевой DSN процессу `alembic upgrade head` напрямую через переменную окружения
`DATABASE_URL` — но проходит через тот же guard в `alembic/env.py`, что и
`db-migrate`. Это безопасно только потому, что `alembic/env.py` вызывает
`load_dotenv(ROOT / ".env")` с дефолтным для python-dotenv `override=False`:
`load_dotenv` не перезаписывает переменную, уже присутствующую в process env, —
поэтому `DATABASE_URL`, поданный рецептом, побеждает DSN из `backend/.env`
(который указывает на текущую рабочую цель разработчика), а не наоборот. Это
намеренный и **постоянный** инвариант, а не случайность: смени кто-нибудь
`override` на `True` из благих намерений «подтянуть `.env` понадёжнее» — рецепт
начал бы мигрировать цель из `backend/.env` вместо тестовой. Закреплено
регрессионным тестом `test_alembic_env_loads_dotenv_without_override`
(`backend/tests/unit/test_db_guard_wiring.py`), который проверяет исходник
`alembic/env.py` на отсутствие `override` в вызове `load_dotenv`.

Отдельно от этого — `set shell := ["bash", "-cu"]` в justfile (`-u`, bash
nounset): обращение к необъявленной переменной шелла падает с ошибкой вместо
молчаливой подстановки пустой строки. Это общая защита **для всех** рецептов
justfile, не специфика `db-test-migrate` — не привязывать её к конкретному
способу, которым этот рецепт сейчас передаёт целевой DSN.

В текст ошибки попадает только нормализованная цель (`host:port/dbname`), без
креденшелов — эти строки уходят в логи и CI-вывод.

## Веб-просмотр таблиц

`just db-web` → http://localhost:8081. Это [pgweb](https://github.com/sosedoff/pgweb)
v0.17.0 — один Go-бинарник без рантаймов: список таблиц, просмотр и фильтрация
строк, SQL-редактор, экспорт в CSV.

- Бинарник: `C:\dev-cache\pgweb\pgweb.exe` — **вне профиля**, потому что профиль
  смонтирован на 20-ГБ User Disk и переполняется (та же причина, что у MinIO).
- Режим `--sessions`: база выбирается в браузере, поэтому один процесс
  обслуживает весь кластер — и `udp_dev`, и `udp_test`, и базы других проектов.
  Строка подключения для `udp_dev`:
  `postgresql://postgres@localhost:5459/udp_dev?sslmode=disable`
- Neon-базам pgweb не нужен: в консоли Neon есть свои Tables и SQL Editor.

**Про общую машину.** Кластер работает с auth `trust`, а pgweb — без
аутентификации (`--bind localhost`). Оба слушают только loopback, снаружи не
достать, но любой другой интерактивный пользователь этой машины получает полный
доступ к `udp_dev` и `udp_test`. Для выбрасываемых dev-данных это приемлемо;
ничего чувствительного в эти базы класть не стоит.

Установка на новой машине:

```bash
mkdir -p /c/dev-cache/pgweb && cd /c/dev-cache/pgweb
curl -sL -o pgweb.zip "https://github.com/sosedoff/pgweb/releases/latest/download/pgweb_windows_amd64.zip"
unzip -o pgweb.zip && mv pgweb_windows_amd64 pgweb.exe && rm pgweb.zip
```

---

## Архитектура

### Backend

- **pytest 9** + `pytest-asyncio` (auto-mode), `pytest-cov`, `pytest-dotenv`.
- **`db_engine`** (session-scoped): открывает соединение к локальному `udp_test`, накатывает Alembic
  миграции один раз на всю сессию pytest.
- **`db_session`** (function-scoped): каждый тест запускается в своей транзакции с
  `join_transaction_mode="create_savepoint"`. Любые `db.commit()` внутри теста
  делают только savepoint — внешняя транзакция откатывается → полная изоляция.
- **`client`** (function-scoped): `TestClient(app)` с переопределённым `get_db`.
- **`factories`** (factory_boy): фабрики для всех моделей. `LazyAttribute` гарантирует
  согласованность производных полей (`amount = quantity * unit_price`).
- **`mock_openrouter`** (respx): перехват `httpx.AsyncClient` к `openrouter.ai`,
  возвращает JSON из `tests/fixtures/openrouter/`. Сценарий переключается через
  `mock_openrouter.use_scenario("unparseable")`.
- **`block_real_openrouter`** (autouse): защитный assert — если тест случайно
  попытается обратиться к реальному OpenRouter, упадёт с понятной ошибкой.
- **`in_memory_s3`**: in-memory подмена S3 (для upload-тестов).

### Frontend

- **Vitest** + `jsdom` + `@testing-library/react` + `@testing-library/user-event`.
- **MSW v2** (`setupServer` в `src/test/server.ts`): перехватывает `axios`-запросы,
  возвращает фикстуры из `src/test/fixtures.ts`. `onUnhandledRequest: "error"` —
  любой неучтённый запрос падает явно.
- **`renderWithProviders`** (`src/test/utils.tsx`): оборачивает компонент в
  `QueryClient` (retries=0), `MemoryRouter`, `ThemeProvider` — те же провайдеры,
  что в реальном `App.tsx`.
- **`window.matchMedia` mock** в setup — `next-themes` его требует, jsdom не
  реализует.
- **`css: false`** в `vitest.config.ts` (дефолт Vitest — НЕ включать обратно).
  jsdom не считает layout/каскад, поэтому обработка CSS не даёт реальной проверки
  стилей, а с Tailwind v4 раздувает transform/import/environment: полный прогон
  ~87с → ~33с при выключении (219/219 зелёные). Тесты через RTL проверяют
  DOM/атрибуты (`className` остаётся в разметке — `toHaveClass` работает).
  Визуальные проверки, если появятся, — отдельным слоем (Playwright/Storybook),
  не в jsdom.

---

## Что покрыто, а что нет

### ✅ Backend — покрыто

**Routers:** `projects` (100%), `material_classes` (100%), `reference_prices` (92%),
`export` (90%), `settings` (80%), `invoices` (66%), `dashboard` (49%),
`/api/units` и `/api/material-types` (auth-protected, покрыты интеграционными тестами).

**Бизнес-логика:** `crud.recalculate_prices` (95%), `pdf_parser._calculate_completeness`,
`_final_confidence`, `routers/invoices._doc_has_issues`, `_avg_confidence` —
ключевые функции расчёта средних цен и валидации документов.

**Units-рефакторинг (добавлено):**
- `test_unit_normalization.py` — `normalize_unit_key` (NFKC, unicode-fold, whitespace, dots) + reconcile-invariant.
- `test_dimension_guard.py` — размерностный guard в `compute_calculations` (class vs ref-price dimension; intra-class mix).
- `test_delivery_distribution.py` — моно- и смешанная размерность, распределение по `normalized_quantity` vs `amount`, edge-cases нулевых/ненормализованных строк.
- `test_units_api.py` — `GET /api/units`, `GET /api/units/{id}/aliases`, `GET /api/material-types`.
- `test_normalization_integration.py` — end-to-end нормализация единиц при создании инвойса, PUT-ренормализация, `warnings` по неизвестным единицам.
- `test_calculations_with_units.py` — расчёт avg_price на `normalized_quantity`, dimension_mismatch → null deviation.
- `test_reference_prices_unit.py` — валидация `unit_id` (base-unit only, dimension match vs material_type); immutability после создания.

### ⚠️ Backend — пробелы (в backlog)

- `routers/invoices.reparse` endpoint не покрыт.
- Pydantic-валидация payload'ов (POST с пустыми полями → 422) не тестируется.
- Cascade-delete для `MaterialClass` с привязанными InvoiceItem.
- `recalculate_prices` edge cases: multi-item, multi-period (частично закрыто `test_calculations_with_units.py`).
- Supplier deviation dimension guard отсутствует (см. `TECH_DEBT.md`).

### ✅ Frontend — покрыто

**Компоненты:** `Dropzone`, `EntitySelect` (4+4 теста, behavioural).
**Страницы (smoke):** `Upload`, `Review`, `Dashboard`, `Reports`, `ReferencePrices`.
**Units-рефакторинг (добавлено):**
- Выбор единицы измерения в справочных ценах: default-by-type (class onChange) + ручной выбор.
- `ReviewItemsTable` inline-edit поля `raw_unit`.
- Отображение и сброс `warnings[]` после сохранения СФ: первый save показывает предупреждение, последующий чистый save его убирает.

### ⚠️ Frontend — пробелы (в backlog)

- Coverage не измерен (Task 3.11 отложен).
- KPI-цифры на Dashboard (требуют `userEvent.click` для выбора проекта).
- Полный flow Upload → парсинг → Review (это уровень E2E).

### ⏳ E2E (Playwright) — отложено

- 5 spec'ов запланировано: golden path upload, edge cases, reference prices,
  projects CRUD, navigation/themes.
- Mock-OpenRouter сервис на FastAPI (`e2e/mock_openrouter/`), `/api/test/reset`
  роутер на бэкенде под `TEST_MODE=1`.

### ✅ CI — настроен (backend + frontend)

- **Backend** (`.github/workflows/backend-tests.yml`): ruff lint → полный `uv run pytest`
  (unit + integration + top-level) на каждый push в `main` / PR. Postgres + pgvector
  запускается как service-container; `DATABASE_URL`/`TEST_DATABASE_URL` — из env job'а.
- **Frontend** (`.github/workflows/frontend-tests.yml`): eslint → `tsc -b --noEmit` →
  `vitest --run` на каждый push в `main` / PR. Версия Node — из `frontend/.nvmrc`
  (baseline 24; на Node 20 MSW-перехват blob-ответа зависает — несовместимость
  тест-инфры, не баг приложения).

### ⏳ E2E CI + branch protection — отложено

- GitHub Actions job для e2e (Playwright).
- `branch protection` на `main` через UI (пока не включён).

---

## Как добавить новый тест

### Backend integration

```python
# backend/tests/integration/test_<router>.py
def test_<feature>(client, factories):
    obj = factories.ProjectFactory.create(name="...")
    response = client.get(f"/api/projects/{obj.id}")
    assert response.status_code == 200
    assert response.json()["name"] == "..."
```

### Backend unit

```python
# backend/tests/unit/test_<module>.py
from <module> import some_function

def test_some_function():
    assert some_function(input) == expected
```

### Frontend

```tsx
// src/components/<Component>.test.tsx
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import { MyComponent } from "./MyComponent";

describe("MyComponent", () => {
  it("renders correctly", async () => {
    renderWithProviders(<MyComponent />);
    await waitFor(() => {
      expect(screen.getByText(/text/)).toBeInTheDocument();
    });
  });
});
```

Нестандартный API endpoint? Добавить handler в `frontend/src/test/handlers.ts`.

### Snapshot AI-ответа от реального PDF

```bash
# Реальный PDF лежит локально, в .gitignore
cp /path/to/real.pdf backend/tests/fixtures/pdf/real/
# Пути — относительно backend/ (рецепт сам делает cd backend)
just snapshot-ai tests/fixtures/pdf/real/real.pdf my_scenario
# → backend/tests/fixtures/openrouter/my_scenario.json (sanitized, ИНН/имена → фейки)
git add backend/tests/fixtures/openrouter/my_scenario.json
```

---

## Tech debt / backlog

См. `docs/superpowers/plans/2026-05-11-testing-infrastructure.md` секция backlog.
Кратко:

- **Prod-code:** `datetime.utcnow()` → `datetime.now(UTC)` в `routers/invoices.py:210`,
  `crud.py:285`, `models.py` (3 места). Python 3.16 сделает их ошибкой.
- **Settings router refactor:** `update_settings` пишет в `os.environ` — выкинуть
  это в пользу только-в-файл (избавит от пайтест-pollution).
- **Test placement:** `tests/unit/test_crud_recalculate.py` использует БД —
  технически это integration, переместить в `tests/integration/`.
- **Дополнительные тесты:** см. «пробелы» выше.

---

## Roadmap

- **Этап 4 (Playwright E2E):** 10 задач (`Task 4.1-4.10` в плане). Mock-OpenRouter
  сервер, `/api/test/reset`, 5 spec'ов.
- **Этап 5 (CI + docs):** GitHub Actions workflow'ы для backend и frontend — ✅ готовы.
  Осталось: `branch protection` на `main`, e2e-job (после Этапа 4).
