"""Guard от мутации незапланированной БД.

Ось — **роль окружения**, а не вендор БД: прод не обязательно Neon. Развёртывание
возможно и на сервере компании (БД локальная или в докере), и на стороннем
хостинге. Вендорная ось не защищала первый случай и ломала второй.

При `APP_ENV=dev` (дефолт) мутировать разрешено loopback-цели и нормализованные
записи `DB_EXTRA_TARGETS`. При `APP_ENV=prod` цели не проверяются: декларация роли
и есть разрешение — цель прода и есть его `DATABASE_URL`.

ВАЖНО про loopback: правило «любая база на loopback мутируема без allowlist»
держится, только пока loopback — действительно локальный процесс. SSH-туннель
или `kubectl port-forward`, пробрасывающие удалённую БД на локальный порт,
делают её неотличимой от настоящего localhost — guard её пропустит без
объявления в DB_EXTRA_TARGETS. Осознанный компромисс дизайна (loopback как
источник доверия), а не дыра в проверке; при таком паттерне работы это стоит
держать в уме на ревью операций.

ВАЖНО про источник цели: цель (host/port/dbname) берётся не хендролленным
парсингом DSN (`urlsplit` + список query-ключей), а из того же слоя, который
реально подключается — `_resolve_connect_target` строит connect-args через
SQLAlchemy-диалект (`create_connect_args`), то есть то же самое, что увидел бы
`psycopg.connect(...)`.

ПРАВИЛО, а не список патчей: guard отказывает мутировать (даёт нераспознанную
цель), если ЛЮБОЙ источник, невидимый на уровне SQLAlchemy, может изменить
конечную точку подключения относительно того, что показывает DSN. Отсутствие
поля в DSN — не то же самое: недостающий hostname/dbname уже безопасен сам по
себе (`UNKNOWN_HOST` / форма `host:port/`, которую `DB_EXTRA_TARGETS` принять
не может), потому что SQLAlchemy эти дефолты вообще не резолвит — на итоговую
тройку они не влияют. Опасны источники, которые способны ПЕРЕНАПРАВИТЬ цель
даже когда DSN эту цель называет явно: query-ключи `host`/`hostaddr`/`port`/
`dbname`/`service` (SQLAlchemy подставляет их поверх netloc при сборке
connect-args — `?host=`/`?port=`/`?dbname=` замещают, а `hostaddr`/`service`
дополняют новым смыслом, которого netloc не содержит) и их libpq-аналоги в
окружении процесса — `$PGHOSTADDR`, `$PGSERVICE` (та же семантика: `hostaddr`
задаёт реальный сетевой адрес независимо от явного `host`, `service` тянет
host/port/dbname из файла, которого guard не видит) — и `$PGPORT` как
единственное исключение: он не перенаправляет явный порт (тот сохраняет
приоритет), а лишь резолвит порт, отсутствующий в DSN, — тем же образом, что
и реальный libpq (см. `_port_from_env_or_default`). `#`/`//` в пути DSN —
отдельный класс: не источник вне DSN, а расхождение парсеров (SQLAlchemy не
знает понятия URL-fragment, а старый `urlsplit`-based код думал иначе).
Подробности каждого случая и почему это закрытие всего класса дыры, а не
очередной точечный патч, — в спеке, §14, §16, §17 (§13 описывает более ранний,
точечный механизм, который §14 заменяет целиком).

Спека: docs/superpowers/specs/2026-07-27-deploy-env-contract-design.md
"""
import os
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

DEFAULT_PG_PORT = 5432
MAX_PG_PORT = 65535
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
UNKNOWN_HOST = "<нераспознанный хост>"


def _port_from_env_or_default() -> int | None:
    """Порт для DSN без явного порта — так, как это сделал бы сам libpq.

    Приоритет параметров подключения в libpq: DSN/keyword-аргументы →
    service-файл → переменные окружения → built-in default. Guard уже
    отклоняет DSN с `service=` (см. `TARGET_DEFINING_QUERY_KEYS`), поэтому
    следующий по приоритету источник после самого DSN — `$PGPORT`.

    `PGPORT` не задан → `DEFAULT_PG_PORT` (сегодняшнее поведение). Задан, но
    не ASCII-цифры в диапазоне 0-65535 → `None`. Это ограничение guard'а, а не
    поведение libpq: libpq порт как число не валидирует (для TCP строка идёт
    в `getaddrinfo` как имя сервиса, для Unix-сокета — часть имени файла), то
    есть нечисловой `PGPORT` не обязательно ошибка. Но единица сравнения
    allowlist'а — числовая тройка `host:port/dbname` по построению
    (`parse_extra_targets` не примет иное), поэтому порт, который guard не
    может выразить числом, — цель, которую он не может сравнить; тихая
    подстановка `5432` тут была бы маленькой ложью о цели — fail-closed
    предпочтительнее.
    """
    raw = os.environ.get("PGPORT")
    if raw is None:
        return DEFAULT_PG_PORT
    if raw.isdigit() and raw.isascii() and int(raw) <= MAX_PG_PORT:
        return int(raw)
    return None


# libpq/psycopg-ключи, которые ЗАМЕЩАЮТ хост/порт/имя БД из netloc или делают
# цель нечитаемой для guard'а, а не дополняют netloc опцией соединения вроде
# sslmode/channel_binding:
#   - host/port/dbname через query — психоп-диалект SQLAlchemy делает
#     `opts.update(url.query)` при сборке connect-args, то есть `?host=` /
#     `?port=` / `?dbname=` подставляются в реальный коннект вместо значения
#     из netloc, а не рядом с ним;
#   - hostaddr — отдельный libpq-параметр: если заданы И host, И hostaddr, то
#     РЕАЛЬНЫЙ сетевой адрес — hostaddr, host используется только для
#     TLS-верификации имени (libpq: "Parameter Key Words", hostaddr).
#     SQLAlchemy об этом не знает и просто прокидывает оба ключа дальше;
#   - service — имя записи в pg_service.conf, из которой берутся host/port/
#     dbname; guard не видит содержимое этого файла, поэтому цель для него
#     принципиально неизвестна.
# Единица сравнения guard'а — тройка host:port/dbname, читаемая из netloc через
# connect-args. DSN, где любой из этих ключей задан через query, не может дать
# доверенную тройку этим способом — цель считается нераспознанной, а не
# подставляется «как получится» из netloc.
TARGET_DEFINING_QUERY_KEYS = frozenset({"host", "hostaddr", "port", "dbname", "service"})


def _resolve_connect_target(url: str) -> tuple[str, int, str] | None:
    """`(host, port, dbname)` так, как их увидит драйвер при реальном коннекте.

    `None` — разобрать не удалось или цель нельзя доверенно определить (битый
    DSN, `service=` в query, `PGHOSTADDR`/`PGSERVICE` в окружении). Ось метода
    — не переизобретать парсинг DSN, а спросить тот же слой, который реально
    подключается: `create_connect_args` психоп-диалекта SQLAlchemy строит
    связку host/port/dbname точно так же, как для настоящего
    `psycopg.connect(...)`, включая замещение из query.

    Общее правило, а не список патчей: guard отказывает, если ЛЮБОЙ источник,
    которого он не видит на уровне SQLAlchemy, может изменить конечную точку
    подключения — не «поле отсутствует в DSN» (это как раз безопасно, см.
    `PGHOST`/`PGDATABASE` ниже), а «это способно перенаправить туда, куда DSN
    не указывает». `PGHOSTADDR` и `PGSERVICE` проверяются первым делом, до
    парсинга самого DSN, — чтобы покрыть обе ветки резолва одинаково и не
    оставить путь в обход через loopback-шорткат `is_target_allowed`:
    SQLAlchemy эти переменные вообще не читает (`create_connect_args` видит
    только то, что явно в DSN), их подставляет только сам libpq в момент
    реального коннекта, поэтому единственный способ их учесть — не доверять
    цели вовсе, когда они заданы.

    - `PGHOSTADDR` — отдельный libpq-параметр окружения, тот же смысл, что и
      query-ключ `hostaddr` (см. `TARGET_DEFINING_QUERY_KEYS`): задаёт
      РЕАЛЬНЫЙ сетевой адрес независимо от того, что стоит в `host` DSN —
      loopback-DSN с `PGHOSTADDR=10.1.2.3` в окружении реально подключается
      не на loopback. `host` DSN тут ни при чём: подмена происходит даже
      когда host в DSN явный;
    - `PGSERVICE` — то же самое, что query-ключ `service`: тянет host/port/
      dbname из `pg_service.conf`, файла, которого guard не видит.

    Fail-closed и дальше: `make_url` бросил — `None`; среди query-ключей
    нашёлся цель-определяющий (см. `TARGET_DEFINING_QUERY_KEYS`) — `None`,
    независимо от того, удалось бы диалекту его разрулить технически корректно
    (порт-only оверрайд на localhost технически остаётся loopback, но остаётся
    query-оверрайдом, а не netloc-значением, — единица сравнения этого guard'а
    построена на netloc, поэтому он не доверяется). Отсутствующий в DSN порт
    резолвится через `$PGPORT` (см. `_port_from_env_or_default`) — невалидный
    `PGPORT` тоже даёт `None`, а не тихий откат на `DEFAULT_PG_PORT`.
    """
    if os.environ.get("PGHOSTADDR") or os.environ.get("PGSERVICE"):
        return None

    try:
        parsed = make_url(url or "")
    except Exception:
        return None

    if TARGET_DEFINING_QUERY_KEYS & parsed.query.keys():
        return None

    try:
        dialect_cls = parsed.get_dialect()
        dialect = dialect_cls()
        connect_args = dialect.create_connect_args(parsed)
        # Форма — (позиционные_аргументы, именованные_аргументы); не
        # предполагаем это молча, проверяем через unpacking + duck-typing.
        _, kwargs = connect_args
        if not hasattr(kwargs, "get"):
            raise TypeError("create_connect_args: второй элемент не dict-like")
        host = str(kwargs.get("host") or "").lower().rstrip(".")
        port = kwargs.get("port")
        dbname = str(kwargs.get("dbname") or "")
    except Exception:
        # Диалект не импортируется — реальный, не гипотетический триггер:
        # схема `postgres://` (без `postgresql`), которую отдают дашборды
        # Neon/Heroku/Supabase/Render и которую пользователь чаще всего
        # вставляет в DATABASE_URL. SQLAlchemy не регистрирует `postgres` как
        # диалект вовсе — `get_dialect()` бросает `NoSuchModuleError` ДО
        # попытки импорта какого-либо DBAPI. (Bare `postgresql://` — НЕ
        # триггер: проверено эмпирически на SQLAlchemy 2.0.35, психоп2-диалект
        # строит connect-args чистой строковой логикой, не трогая `self.dbapi`,
        # и не бросает, даже когда пакет `psycopg2` не установлен, — в проекте
        # используется psycopg 3, `uv.lock`.) Единственный источник,
        # оставшийся доступным после отказа диалекта, — атрибуты самого
        # `make_url`; они НИКОГДА не отражают query-оверрайды (уже
        # отфильтровано выше), поэтому доверять netloc-полям здесь безопасно.
        # Путь у́же основного: он существует только на случай «диалект не
        # резолвится», когда реально подключиться всё равно было бы нечем.
        host = (parsed.host or "").lower().rstrip(".")
        port = parsed.port
        dbname = parsed.database or ""

    if port is None:
        # Явного порта в DSN нет ни в одной из веток — берём из окружения так
        # же, как это сделал бы сам libpq (DSN → service-файл → env → default).
        port = _port_from_env_or_default()
        if port is None:
            return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = DEFAULT_PG_PORT
    return (host, port, dbname)


def safe_host(url: str) -> str:
    """Хост из DSN без креденшелов — пароль не должен попасть в текст ошибки."""
    if not url:
        return ""
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def normalize_target(url: str) -> str:
    """DSN → нормализованная цель `host:port/dbname`.

    Единица сравнения включает имя БД, потому что CI различает свои цели только
    им: `localhost:5432/postgres` против `localhost:5432/udp_test`.

    Триплет строится из `_resolve_connect_target` — источник тот же слой,
    который реально подключается (см. модульный докстринг). Нераспознанная
    цель (битый DSN, `service=`/`hostaddr=`/`host=`/`port=`/`dbname=` в query,
    либо `$PGPORT` в окружении задан, но не ASCII-цифры 0-65535) даёт
    `UNKNOWN_HOST`-форму: такая цель не loopback и не совпадает ни с одной
    записью `DB_EXTRA_TARGETS` (маркер отдельно запрещён как запись —
    `parse_extra_targets` его отвергает).

    Пустой hostname (socket-DSN вида `postgresql:///db`) — тоже `UNKNOWN_HOST`
    для хост-части: `_resolve_connect_target` в этом случае успешно вернул
    порт и dbname, но host — пустая строка, и она заменяется на маркер здесь
    же, не в `_resolve_connect_target` (там пустой host — валидный ответ, а не
    признак отказа).
    """
    resolved = _resolve_connect_target(url)
    if resolved is None:
        return f"{UNKNOWN_HOST}:{DEFAULT_PG_PORT}/"
    host, port, dbname = resolved
    host = host or UNKNOWN_HOST
    return f"{host}:{port}/{dbname}"


def parse_extra_targets(raw: str) -> frozenset[str]:
    """Разобрать `DB_EXTRA_TARGETS` — список `host:port/dbname` через запятую.

    Каждая запись обязана быть полной тройкой: хост, порт в диапазоне
    0-65535 и имя БД. Порт проверяется через `isdigit() and isascii()`, а не
    только `isdigit()`: юникодные цифры вроде "²" проходят `str.isdigit()`, но
    `int()` их не принимает — без явной проверки ASCII отвергнутая запись
    падала бы с чужой трассой `int()` вместо предсказуемого `ValueError` этой
    функции. Хост `UNKNOWN_HOST` отдельно запрещён как запись: это маркер
    нераспознанного DSN, который печатает `ensure_mutation_allowed` в тексте
    ошибки, и его copy-paste в DB_EXTRA_TARGETS разрешил бы мутацию для ЛЮБОГО
    хостless или неразбираемого DSN с тем же портом и именем БД. Запись без
    порта или без имени БД незаметно расширила бы allowlist до уровня хоста,
    что противоречит выбору единицы сравнения, поэтому это тоже ошибка, а не
    «любая база на этом хосте».

    Raises:
        ValueError: запись не имеет вида `host:port/dbname`, порт вне
            диапазона 0-65535 или хост равен `UNKNOWN_HOST`.
    """
    targets = set()
    for chunk in (raw or "").split(","):
        entry = chunk.strip()
        if not entry:
            continue
        host, _, tail = entry.partition(":")
        port, _, dbname = tail.partition("/")
        host = host.lower().rstrip(".")
        valid_port = port.isdigit() and port.isascii() and int(port) <= MAX_PG_PORT
        if not host or host == UNKNOWN_HOST or not valid_port or not dbname:
            raise ValueError(
                f"DB_EXTRA_TARGETS: запись '{entry}' должна иметь вид host:port/dbname"
            )
        targets.add(f"{host}:{int(port)}/{dbname}")
    return frozenset(targets)


def is_target_allowed(url: str, extra: frozenset[str]) -> bool:
    """Разрешена ли цель в dev: loopback (любая база) или запись из allowlist.

    Loopback-шорткат сверяется по **резолвленному** хосту из
    `_resolve_connect_target`, а не по `safe_host(url)` (netloc). Иначе
    `?hostaddr=10.1.2.3` на `localhost`-netloc обманул бы шорткат: netloc
    показывает loopback, а реальный коннект (libpq отдаёт приоритет hostaddr
    над host) уходит на `10.1.2.3`. Нераспознанная цель (см.
    `_resolve_connect_target`) отклоняется безусловно — fail-closed.
    """
    resolved = _resolve_connect_target(url)
    if resolved is None:
        return False
    host, _port, _dbname = resolved
    if host in LOOPBACK_HOSTS:
        return True
    return normalize_target(url) in extra


def ensure_mutation_allowed(url: str, action: str) -> None:
    """Прервать `action`, если цель не разрешена для текущего `APP_ENV`.

    Args:
        url: DSN цели, которую собираются мутировать. Именно цель операции, а не
            `settings.DATABASE_URL`: в `db-test-migrate` она приходит из process
            env, в conftest — из `cfg.set_main_option`.
        action: что собирались сделать — попадёт в текст ошибки.

    Raises:
        RuntimeError: `APP_ENV=dev`, цель не loopback и отсутствует в
            `DB_EXTRA_TARGETS`.
        ValueError: `DB_EXTRA_TARGETS` содержит запись не вида
            `host:port/dbname` — пробрасывается из `parse_extra_targets` как
            есть, без оборачивания в `RuntimeError`: это ошибка конфигурации
            (опечатка в `backend/.env`), а не штатный отказ guard'а.
    """
    # Импорт внутри функции: db_guard остаётся импортируемым без конфига, а
    # Settings() собирается на момент вызова — тестовые monkeypatch действуют.
    from config import Settings

    s = Settings()
    if s.APP_ENV == "prod":
        return
    extra = parse_extra_targets(s.DB_EXTRA_TARGETS)
    if is_target_allowed(url, extra):
        return

    if _resolve_connect_target(url) is None:
        # Нераспознанная цель, и первым делом называем переменную окружения
        # по имени, если она и есть причина: пользователь, у которого DSN на
        # вид совершенно loopback, иначе не поймёт, почему её отклонили — сам
        # DSN ни в чём не виноват, а окружение процесса не видно в тексте DSN.
        env_culprits = [name for name in ("PGHOSTADDR", "PGSERVICE") if os.environ.get(name)]
        if env_culprits:
            names = " и ".join(env_culprits)
            raise RuntimeError(
                f"{action}: цель DSN не удалось однозначно определить при APP_ENV=dev.\n"
                f"Хост в основной части DSN (может не совпадать с реальной целью "
                f"коннекта — потому и отказ): {safe_host(url) or UNKNOWN_HOST}.\n"
                f"В окружении процесса задан {names} — libpq применяет его при "
                "реальном коннекте независимо от того, что написано в самом DSN "
                "(даже если DSN называет loopback явно), поэтому guard не доверяет "
                "видимой цели.\n"
                f"Уберите {names} из окружения и повторите операцию. Если это "
                "осознанно развёрнутое окружение — выставьте APP_ENV=prod."
            )
        # Иначе: битый DSN, `host`/`hostaddr`/`port`/`dbname`/`service` в
        # query (см. TARGET_DEFINING_QUERY_KEYS), либо `$PGPORT` в окружении
        # задан, но не ASCII-цифры 0-65535. normalize_target в этом случае
        # даёт UNKNOWN_HOST-форму, которую parse_extra_targets безусловно
        # отвергает как запись — совет «добавьте её в DB_EXTRA_TARGETS» был
        # бы гарантированно нерабочим (и в некоторых случаях — рецептом уйти
        # на непреднамеренную цель, скрытую query-параметром). Вместо этого
        # просим убрать сам параметр. `safe_host()` (netloc, без креденшелов)
        # — только для ориентира в диагностике: это НЕ обязательно реальная
        # цель коннекта, отсюда и весь отказ.
        raise RuntimeError(
            f"{action}: цель DSN не удалось однозначно определить при APP_ENV=dev.\n"
            f"Хост в основной части DSN (может не совпадать с реальной целью "
            f"коннекта — потому и отказ): {safe_host(url) or UNKNOWN_HOST}.\n"
            "Возможные причины: DSN не разбирается (проверьте синтаксис); он "
            "содержит host/hostaddr/port/dbname/service в query-строке — guard "
            "не доверяет такой цели, потому что реальный коннект может отличаться "
            "от того, что видно в основной части DSN; либо DSN без явного порта, "
            "а переменная окружения PGPORT задана значением вне 0-65535.\n"
            "Если дело в query-ключе — уберите его из query-строки (перенесите "
            "значение в основную часть DSN, если оно нужно) и повторите операцию. "
            "Если дело в PGPORT — поправьте значение переменной окружения."
        )

    target = normalize_target(url)
    listed = ", ".join(sorted(extra)) if extra else "сейчас пусто"
    raise RuntimeError(
        f"{action}: цель {target} не разрешена при APP_ENV=dev.\n"
        f"Разрешено: loopback + DB_EXTRA_TARGETS ({listed}).\n"
        "Если это развёрнутое окружение — выставьте APP_ENV=prod.\n"
        f"Если это дев-цель — добавьте {target} в DB_EXTRA_TARGETS в backend/.env."
    )
