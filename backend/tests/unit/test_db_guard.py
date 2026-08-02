"""Тесты guard'а от мутации незапланированной БД (db_guard)."""
import pytest

from db_guard import (
    UNKNOWN_HOST,
    _resolve_connect_target,
    ensure_mutation_allowed,
    is_target_allowed,
    normalize_target,
    parse_extra_targets,
    safe_host,
)

# Хост синтетический: реальный прод-эндпоинт в репозитории и в CI-логах при
# падении не нужен — ни один тест не зависит от его настоящего значения.
REMOTE_HOST = "ep-example-0000.c-3.eu-central-1.aws.neon.tech"
REMOTE_URL = (
    f"postgresql+psycopg://test_owner:secret-pw@{REMOTE_HOST}/neondb"
    "?sslmode=require&channel_binding=require"
)
LOCAL_URL = "postgresql+psycopg://postgres@localhost:5459/udp_dev"


@pytest.fixture(autouse=True)
def _hermetic_guard_env(monkeypatch):
    """Пин APP_ENV и DB_EXTRA_TARGETS через process env.

    Без пина тесты читали бы реальный backend/.env: env_file абсолютный, а
    инструкция плана предписывает вписать туда DB_EXTRA_TARGETS. Тогда «дефолт
    пустой» был бы зелёным в CI (файла нет) и красным у каждого, кто инструкцию
    выполнил. Process env бьёт env_file — тем же механизмом, что и пин.

    `PGPORT`/`PGHOSTADDR`/`PGSERVICE`/`PGHOST`/`PGDATABASE` тоже снимаются:
    `_resolve_connect_target` читает их напрямую из `os.environ`, а не через
    `Settings()` — без явного `delenv` поведение тестов зависело бы от того,
    что задано в шелле разработчика/CI-раннера. `PGHOSTADDR`/`PGSERVICE`
    особенно: они безусловно превращают ЛЮБОЙ DSN в нераспознанную цель (см.
    `_resolve_connect_target`), включая loopback-DSN, на которых держится
    большинство остальных тестов этого файла.
    """
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DB_EXTRA_TARGETS", "")
    monkeypatch.delenv("PGPORT", raising=False)
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    monkeypatch.delenv("PGHOST", raising=False)
    monkeypatch.delenv("PGDATABASE", raising=False)


def test_normalize_target_builds_triple():
    """DSN → host:port/dbname."""
    assert normalize_target(LOCAL_URL) == "localhost:5459/udp_dev"


def test_normalize_target_drops_query_params():
    """Query-параметры отбрасываются: различие только в них — та же цель."""
    a = "postgresql+psycopg://u@h.example.com:5432/db?sslmode=require"
    b = "postgresql+psycopg://u@h.example.com:5432/db"
    assert normalize_target(a) == normalize_target(b) == "h.example.com:5432/db"


def test_normalize_target_defaults_port():
    """Отсутствующий порт нормализуется в 5432."""
    assert normalize_target("postgresql://u@h.example.com/db") == "h.example.com:5432/db"


def test_normalize_target_lowercases_host():
    """Регистр хоста не создаёт вторую цель."""
    assert normalize_target("postgresql://u@H.Example.COM/db") == "h.example.com:5432/db"


def test_normalize_target_handles_broken_dsn():
    """Битый DSN не роняет нормализацию и даёт нераспознанный хост."""
    assert normalize_target("postgresql://u@[bad:ipv6/db").startswith(UNKNOWN_HOST)


def test_normalize_target_marks_empty_host():
    """Пустой hostname показывается явным маркером, а не пустой строкой."""
    assert normalize_target("postgresql:///db") == f"{UNKNOWN_HOST}:5432/db"


def test_safe_host_hides_credentials():
    """В хосте нет ни пользователя, ни пароля — строка уходит в логи."""
    host = safe_host(REMOTE_URL)
    assert host == REMOTE_HOST
    assert "secret-pw" not in host
    assert "test_owner" not in host


def test_safe_host_survives_malformed_url():
    """Битый DSN не роняет извлечение хоста (urlsplit кидает ValueError).

    is_target_allowed больше не читает safe_host() — loopback-шорткат сверяется
    по резолвленному хосту из `_resolve_connect_target` (см. соответствующий
    докстринг `is_target_allowed`). Но `ensure_mutation_allowed` зовёт
    `safe_host()` для диагностики в тексте ошибки нераспознанной цели — там
    его "никогда не бросает" остаётся load-bearing тем же образом: битый DSN
    не должен ронять формирование самого сообщения об ошибке.
    """
    assert safe_host("postgresql://u@[bad:ipv6/db") == ""


def test_safe_host_handles_empty():
    """Пустой DSN не роняет извлечение хоста и не даёт ложный loopback."""
    assert safe_host("") == ""


def test_parse_extra_targets_empty():
    """Пустая строка — пустой список."""
    assert parse_extra_targets("") == frozenset()


def test_parse_extra_targets_multiple():
    """Список через запятую, пробелы игнорируются, хост в нижний регистр."""
    raw = "H.Example.com:5432/db1, other.example.com:6000/db2"
    assert parse_extra_targets(raw) == frozenset(
        {"h.example.com:5432/db1", "other.example.com:6000/db2"}
    )


@pytest.mark.parametrize("entry", ["h.example.com/db", "h.example.com:5432", "h.example.com"])
def test_parse_extra_targets_rejects_partial_entry(entry):
    """Неполная тройка — ошибка: иначе allowlist расширился бы до уровня хоста."""
    with pytest.raises(ValueError, match="host:port/dbname"):
        parse_extra_targets(entry)


@pytest.mark.parametrize("entry", ["h.example.com:²/db", "h.example.com:99999999/db"])
def test_parse_extra_targets_rejects_invalid_port(entry):
    """Юникодная цифра в порте и порт вне 0-65535 — та же ошибка формата.

    '²'.isdigit() is True, но int('²') бросает ValueError с чужим
    текстом — без явной проверки isascii() отвергнутая запись падала бы не
    предсказуемым сообщением этой функции, а трассой int().
    """
    with pytest.raises(ValueError, match="host:port/dbname"):
        parse_extra_targets(entry)


def test_parse_extra_targets_rejects_unknown_host_marker():
    """UNKNOWN_HOST как запись — ошибка, а не заготовка allowlist на все битые DSN.

    UNKNOWN_HOST — маркер нераспознанного хоста в тексте ошибки
    ensure_mutation_allowed; будь он разрешённым host в DB_EXTRA_TARGETS,
    copy-paste из ошибки разрешил бы мутацию для любого хостless/битого DSN
    с тем же портом и именем БД.
    """
    with pytest.raises(ValueError, match="host:port/dbname"):
        parse_extra_targets(f"{UNKNOWN_HOST}:5432/db")


def test_normalize_target_handles_ipv6_literal():
    """IPv6-литерал в скобках даёт хост без скобок."""
    assert normalize_target("postgresql://u@[::1]:5459/db") == "::1:5459/db"


@pytest.mark.parametrize(
    "query",
    ["host=prod.example.com", "port=6543", "dbname=other_db"],
)
def test_normalize_target_rejects_query_key_override(query):
    """`?host=`/`port=`/`dbname=` не дают матчируемую тройку из netloc.

    psycopg-диалект SQLAlchemy делает `opts.update(url.query)` при сборке
    connect-args — эти query-ключи ЗАМЕЩАЮТ значение из netloc в реальном
    подключении, а не дополняют его (проверено эмпирически: `create_connect_args`
    на `postgresql+psycopg://u@localhost:5459/udp_dev?host=prod.example.com`
    даёт `{'host': 'prod.example.com', ...}`). Единица сравнения из netloc в
    этом случае описывает не ту цель, к которой реально подключится psycopg —
    поэтому normalize_target обязан дать UNKNOWN_HOST, а не тройку по netloc.
    """
    url = f"postgresql+psycopg://u@localhost:5459/udp_dev?{query}"
    result = normalize_target(url)
    assert result.startswith(UNKNOWN_HOST)
    # UNKNOWN_HOST не разбирается parse_extra_targets — такая цель не может
    # совпасть ни с одной записью DB_EXTRA_TARGETS.
    with pytest.raises(ValueError):
        parse_extra_targets(result)


@pytest.mark.parametrize(
    "query",
    ["host=prod.example.com", "port=6543", "dbname=other_db"],
)
def test_is_target_allowed_denies_loopback_with_query_key_override(query):
    """`?host=`/`port=`/`dbname=` на loopback-netloc не проходят loopback-шорткат.

    Без этой проверки `is_target_allowed` смотрел бы на `safe_host(url)` ==
    "localhost" и пропускал бы мутацию, хотя реальное соединение (через
    psycopg-диалект) уйдёт на цель, заданную query-ключом — ровно дыра,
    которую реализация guard'а обязана закрывать.
    """
    url = f"postgresql+psycopg://u@localhost:5459/udp_dev?{query}"
    assert is_target_allowed(url, frozenset()) is False


def test_normalize_target_rejects_query_key_override_after_fragment():
    """`#fragment?host=...` не должен обходить override-детект через urlsplit.

    `urlsplit` трактует `#` как разделитель fragment и обрезает `.query` до
    него, но URL-парсер SQLAlchemy понятия fragment не знает вовсе — весь
    хвост после первого `?` для него query, независимо от `#` где-то внутри.
    Поэтому DSN с `#frag?host=...` реально подключается по host= (проверено
    эмпирически: create_connect_args даёt {'host': 'prod.example.com', ...}),
    хотя старая реализация (urlsplit(url).query) видела пустую query и
    override пропускала.
    """
    url = "postgresql+psycopg://u@localhost:5459/udp_dev#frag?host=prod.example.com&dbname=proddb"
    result = normalize_target(url)
    assert result.startswith(UNKNOWN_HOST)
    assert is_target_allowed(url, frozenset()) is False


def test_is_target_allowed_still_ignores_ordinary_query_params():
    """Обычные query-параметры (sslmode, channel_binding, ...) не меняют цель.

    Существующее поведение (см. test_normalize_target_drops_query_params) не
    должно пострадать от точечного исключения для host/port/dbname.
    """
    url = "postgresql+psycopg://u@localhost:5459/udp_dev?sslmode=require&channel_binding=require"
    assert is_target_allowed(url, frozenset()) is True


def test_ipv6_loopback_allowed_without_list():
    """IPv6-loopback разрешён через LOOPBACK_HOSTS, а не через allowlist.

    В DB_EXTRA_TARGETS IPv6 выразить нельзя: разбор записи режет по первому
    двоеточию, и `::1:5459/db` не разбирается. Это осознанный YAGNI — единственная
    нужная IPv6-цель это loopback, а он покрыт LOOPBACK_HOSTS. Появится реальная
    не-loopback IPv6-цель — разбор придётся усложнить.
    """
    assert is_target_allowed("postgresql://u@[::1]:5459/db", frozenset()) is True


def test_loopback_allowed_without_list():
    """Loopback разрешён безусловно — любая база, без DB_EXTRA_TARGETS."""
    assert is_target_allowed(LOCAL_URL, frozenset()) is True
    assert is_target_allowed("postgresql://u@127.0.0.1:5432/anything", frozenset()) is True


def test_remote_target_needs_list():
    """Не-loopback цель без записи в allowlist запрещена."""
    assert is_target_allowed(REMOTE_URL, frozenset()) is False


def test_remote_target_allowed_when_listed():
    """Не-loopback цель разрешена, если её нормализованная тройка в списке."""
    extra = parse_extra_targets(f"{REMOTE_HOST}:5432/neondb")
    assert is_target_allowed(REMOTE_URL, extra) is True


def test_dev_blocks_unlisted_target():
    """При APP_ENV=dev неразрешённая цель прерывает операцию."""
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(REMOTE_URL, "alembic")
    assert "APP_ENV=dev" in str(exc.value)


def test_dev_allows_loopback():
    """При APP_ENV=dev loopback проходит без списка."""
    ensure_mutation_allowed(LOCAL_URL, "alembic")


def test_prod_skips_target_check(monkeypatch):
    """При APP_ENV=prod цели не проверяются — роль и есть разрешение."""
    monkeypatch.setenv("APP_ENV", "prod")
    ensure_mutation_allowed(REMOTE_URL, "alembic")


def test_dev_allows_target_from_env_list(monkeypatch):
    """DB_EXTRA_TARGETS из окружения действительно снимает запрет.

    Без этого теста ensure_mutation_allowed мог бы игнорировать
    settings.DB_EXTRA_TARGETS целиком (например, звать
    parse_extra_targets("") вместо parse_extra_targets(s.DB_EXTRA_TARGETS)) —
    остальные 23 теста модуля остались бы зелёными, а единственный
    пользовательский escape hatch был бы мёртв.
    """
    monkeypatch.setenv("DB_EXTRA_TARGETS", normalize_target(REMOTE_URL))
    ensure_mutation_allowed(REMOTE_URL, "alembic")


def test_error_lists_configured_targets(monkeypatch):
    """При непустом DB_EXTRA_TARGETS текст ошибки перечисляет его, а не «пусто».

    Покрывает ветку `", ".join(sorted(extra))` — до этого теста исполнялась
    только ветка "сейчас пусто".
    """
    other = "other.example.com:5432/otherdb"
    monkeypatch.setenv("DB_EXTRA_TARGETS", other)
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(REMOTE_URL, "alembic")
    message = str(exc.value)
    assert other in message
    assert "сейчас пусто" not in message


def test_ensure_mutation_allowed_propagates_malformed_extra_targets(monkeypatch):
    """Неполная запись в DB_EXTRA_TARGETS даёт ValueError, а не RuntimeError.

    ensure_mutation_allowed сам формат не проверяет — это ошибка конфигурации
    (опечатка в backend/.env), которая обязана падать иначе, чем штатный отказ
    guard'а, а не быть проглочена под RuntimeError на стартовом пути.
    """
    monkeypatch.setenv("DB_EXTRA_TARGETS", "h.example.com")
    with pytest.raises(ValueError, match="host:port/dbname"):
        ensure_mutation_allowed(REMOTE_URL, "alembic")


def test_error_names_both_exits():
    """Текст ошибки называет оба выхода: APP_ENV=prod и DB_EXTRA_TARGETS."""
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(REMOTE_URL, "alembic")
    message = str(exc.value)
    assert "APP_ENV=prod" in message
    assert "DB_EXTRA_TARGETS" in message


def test_error_target_is_copy_pasteable():
    """Отвергнутая цель напечатана в формате, который принимает DB_EXTRA_TARGETS.

    Требование, не совпадение: пользователь копирует строку из ошибки в
    переменную без редактирования.
    """
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(REMOTE_URL, "alembic")
    target = normalize_target(REMOTE_URL)
    assert target in str(exc.value)
    assert parse_extra_targets(target) == frozenset({target})


def test_error_leaks_no_password():
    """Пароль из DSN не попадает в сообщение."""
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(REMOTE_URL, "cli create-superuser")
    assert "secret-pw" not in str(exc.value)


# --- Регресс: `_resolve_connect_target` вместо urlsplit+блэклиста ----------
#
# Найдено внешним ревью после второго патча: guard разбирал DSN сам
# (urlsplit + список query-ключей), а реально подключается SQLAlchemy-диалект
# + libpq — два независимых парсера расходились на `hostaddr` (libpq отдаёт
# ему приоритет над `host` для сетевого адреса, но не входил в старый список
# TARGET_OVERRIDE_QUERY_KEYS) и на `#`/`//` в пути (SQLAlchemy не знает
# понятия fragment, а `#`-тесты уже были покрыты отдельно — здесь добавлены
# `dev#archive`/`//dev` без `?host=` вовсе, то есть без явного query-ключа).


def test_hostaddr_query_key_denies_loopback_shortcut():
    """`?hostaddr=` на loopback-netloc не проходит loopback-шорткат.

    libpq: если заданы И host, И hostaddr, реальный сетевой адрес — hostaddr,
    host используется только для TLS-верификации имени. `10.1.2.3` — не
    loopback, поэтому реальное соединение уходит не туда, куда указывает
    netloc (`localhost`). `hostaddr` входит в TARGET_DEFINING_QUERY_KEYS
    наравне с host/port/dbname/service — по той же причине: тройка,
    прочитанная из netloc, не описывает цель, к которой реально подключится
    psycopg.
    """
    url = "postgresql+psycopg://u:p@localhost:5459/udp_dev?hostaddr=10.1.2.3"
    assert is_target_allowed(url, frozenset()) is False


def test_normalize_target_rejects_hostaddr_query_key():
    """`?hostaddr=` даёт `UNKNOWN_HOST`, а не тройку из netloc или из hostaddr.

    Не подставляем и резолвленный `10.1.2.3` вместо netloc: единица
    сравнения guard'а — netloc-тройка, а DSN с hostaddr эту тройку сделать
    доверенной не может — ровно так же, как `?host=`/`?port=`/`?dbname=`.
    """
    url = "postgresql+psycopg://u:p@localhost:5459/udp_dev?hostaddr=10.1.2.3"
    result = normalize_target(url)
    assert result.startswith(UNKNOWN_HOST)
    with pytest.raises(ValueError):
        parse_extra_targets(result)


def test_normalize_target_rejects_service_query_key():
    """`?service=` даёт `UNKNOWN_HOST`: pg_service.conf guard не видит.

    service тянет host/port/dbname из файла конфигурации, которого guard не
    видит вовсе — цель принципиально неизвестна, а не просто «отличается от
    netloc».
    """
    url = "postgresql+psycopg://u@remote.example.com:5432/dev?service=myservice"
    result = normalize_target(url)
    assert result.startswith(UNKNOWN_HOST)


def test_is_target_allowed_denies_service_query_key():
    """`?service=` запрещён безусловно — даже с непустым allowlist."""
    url = "postgresql+psycopg://u@remote.example.com:5432/dev?service=myservice"
    extra = parse_extra_targets("remote.example.com:5432/dev")
    assert is_target_allowed(url, extra) is False


def test_ensure_mutation_allowed_gives_actionable_advice_for_unresolvable_target():
    """Нераспознанная цель (`?hostaddr=`) не советует нерабочий DB_EXTRA_TARGETS.

    До этого фикса текст ошибки для ЛЮБОЙ отклонённой цели предлагал добавить
    `normalize_target(url)` в DB_EXTRA_TARGETS — для нераспознанной цели это
    `<нераспознанный хост>:5432/`, запись, которую `parse_extra_targets`
    безусловно отвергает как формат. Пользователь, последовавший совету,
    получил бы ValueError при следующем чтении Settings(). Ветка `hostaddr`/
    `service` шире, чем раньше (см. TARGET_DEFINING_QUERY_KEYS), поэтому этот
    путь теперь достижим не только через битый DSN.
    """
    url = "postgresql+psycopg://u:p@localhost:5459/udp_dev?hostaddr=10.1.2.3"
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(url, "alembic")
    message = str(exc.value)
    assert UNKNOWN_HOST not in message
    assert "DB_EXTRA_TARGETS" not in message
    assert "host/hostaddr/port/dbname/service" in message
    with pytest.raises(ValueError):
        # Подтверждаем, что старый совет действительно был бы нерабочим —
        # чтобы не полагаться на утверждение без проверки.
        parse_extra_targets(f"{UNKNOWN_HOST}:5432/")


def test_normalize_target_does_not_collapse_fragment_dbname_to_bare_name():
    """`/dev#archive` в пути — часть имени БД, а не `#`-разделитель fragment.

    SQLAlchemy (и реальный psycopg-коннект) не знает понятия URL-fragment —
    всё после первого `/` в пути является именем БД буквально, включая `#`.
    Запись в DB_EXTRA_TARGETS для `dev` не должна пропускать эту цель:
    реальная БД называется `dev#archive`, а не `dev`.
    """
    url = "postgresql+psycopg://u:p@remote.example.com:5432/dev#archive"
    result = normalize_target(url)
    assert result == "remote.example.com:5432/dev#archive"
    extra = parse_extra_targets("remote.example.com:5432/dev")
    assert is_target_allowed(url, extra) is False


def test_normalize_target_does_not_collapse_double_slash_dbname_to_bare_name():
    """`//dev` в пути даёт имя БД `/dev`, а не `dev` — совпадения по `dev` нет."""
    url = "postgresql+psycopg://u:p@remote.example.com:5432//dev"
    result = normalize_target(url)
    assert result == "remote.example.com:5432//dev"
    extra = parse_extra_targets("remote.example.com:5432/dev")
    assert is_target_allowed(url, extra) is False


def test_resolve_connect_target_falls_back_for_postgres_scheme():
    """`postgres://` (без `ql`) реально бросает в `get_dialect()` — не мок.

    Найдено ревью: bare `postgresql://` (изначальное предположение брифа для
    этого теста) НЕ триггерит отказ диалекта — психоп2-диалект строит
    connect-args чистой строковой логикой и не трогает `self.dbapi`, поэтому
    не бросает, даже когда пакет `psycopg2` не установлен (проверено
    эмпирически на SQLAlchemy 2.0.35). Реальный триггер — схема `postgres://`
    (без `postgresql`), которую отдают дашборды Neon/Heroku/Supabase/Render и
    которую пользователь чаще всего вставляет в `DATABASE_URL`: `postgres`
    не зарегистрирована как диалект в SQLAlchemy вовсе, `get_dialect()`
    бросает `NoSuchModuleError` без единого мока — ровно та ветка, которую
    ловит `except Exception` в `_resolve_connect_target`.
    """
    result = _resolve_connect_target("postgres://u@h.example.com:5432/dev#archive")
    assert result == ("h.example.com", 5432, "dev#archive")


def test_resolve_connect_target_fallback_still_rejects_query_override():
    """Fallback тоже не доверяет query-оверрайдам — проверка стоит до диалекта.

    Проверка `TARGET_DEFINING_QUERY_KEYS` в `_resolve_connect_target`
    выполняется один раз, до ветвления primary/fallback, поэтому она
    защищает fallback-путь так же, как основной. `postgres://` — тот же
    реальный триггер отказа диалекта, что и в предыдущем тесте, без мока.
    """
    result = _resolve_connect_target("postgres://u@localhost:5432/db?hostaddr=10.1.2.3")
    assert result is None


def test_resolve_connect_target_returns_none_for_unparsable_url():
    """`make_url` сам бросает — `_resolve_connect_target` даёт `None`, не трассу."""
    assert _resolve_connect_target("postgresql://u@[bad:ipv6/db") is None


def test_resolve_connect_target_primary_path_uses_create_connect_args(monkeypatch):
    """Пин на то, что основной путь реально спрашивает диалект, а не netloc напрямую.

    Без этого теста ничто не заметило бы, если бы `_resolve_connect_target`
    тихо перестал звать `create_connect_args` и стал читать `parsed.host` для
    ЛЮБОГО DSN (не только для fallback) — поведение для "чистых" DSN осталось
    бы прежним внешне, но перестало бы быть тем самым "спросить слой, который
    реально подключается", ради которого весь модуль переписан.
    """
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

    calls = []
    original = PGDialect_psycopg.create_connect_args

    def _spy(self, url):
        calls.append(url)
        return original(self, url)

    monkeypatch.setattr(PGDialect_psycopg, "create_connect_args", _spy)
    result = _resolve_connect_target("postgresql+psycopg://u@h.example.com:5432/db")
    assert result == ("h.example.com", 5432, "db")
    assert len(calls) == 1


# --- Регресс: $PGPORT для DSN без явного порта (CodeRabbit, поверх 2a07417) --
#
# libpq-приоритет параметров: DSN/keyword-аргументы → service-файл → env →
# built-in default. `_resolve_connect_target` подставлял DEFAULT_PG_PORT
# безусловно для DSN без порта — allowlist-запись `host:5432/db` тем самым
# разрешала мутацию DSN, который реально коннектится на `host:$PGPORT/db`,
# если `PGPORT` задан в окружении процесса. Loopback-ветка не затронута:
# loopback разрешает любую базу на любом порту by design.


def test_normalize_target_uses_pgport_when_dsn_has_no_explicit_port(monkeypatch):
    """Портless DSN + `$PGPORT` → нормализованная тройка содержит `PGPORT`."""
    monkeypatch.setenv("PGPORT", "6001")
    url = "postgresql+psycopg://u@remote.example.com/db"
    assert normalize_target(url) == "remote.example.com:6001/db"


def test_is_target_allowed_denies_default_port_entry_when_pgport_set(monkeypatch):
    """Allowlist-запись с портом `5432` не пропускает DSN, реально идущий на `PGPORT`."""
    monkeypatch.setenv("PGPORT", "6001")
    url = "postgresql+psycopg://u@remote.example.com/db"
    extra = parse_extra_targets("remote.example.com:5432/db")
    assert is_target_allowed(url, extra) is False


def test_is_target_allowed_permits_pgport_entry_when_pgport_set(monkeypatch):
    """Allowlist-запись с реальным `PGPORT`-портом пропускает тот же DSN."""
    monkeypatch.setenv("PGPORT", "6001")
    url = "postgresql+psycopg://u@remote.example.com/db"
    extra = parse_extra_targets("remote.example.com:6001/db")
    assert is_target_allowed(url, extra) is True


def test_explicit_dsn_port_wins_over_differing_pgport(monkeypatch):
    """Явный порт в DSN сильнее `$PGPORT` — тот же приоритет, что и у libpq."""
    monkeypatch.setenv("PGPORT", "6001")
    url = "postgresql+psycopg://u@remote.example.com:5432/db"
    assert normalize_target(url) == "remote.example.com:5432/db"
    extra = parse_extra_targets("remote.example.com:5432/db")
    assert is_target_allowed(url, extra) is True


def test_normalize_target_defaults_to_5432_when_pgport_unset(monkeypatch):
    """`PGPORT` не задан → поведение как сегодня, порт `5432`."""
    monkeypatch.delenv("PGPORT", raising=False)
    url = "postgresql+psycopg://u@remote.example.com/db"
    assert normalize_target(url) == "remote.example.com:5432/db"


@pytest.mark.parametrize("bad_pgport", ["abc", "-1", "99999999", "5432.0", "5432 "])
def test_resolve_connect_target_denies_invalid_pgport(monkeypatch, bad_pgport):
    """Невалидный `PGPORT` (не ASCII-цифры 0-65535) — нераспознанная цель, не тихий 5432."""
    monkeypatch.setenv("PGPORT", bad_pgport)
    url = "postgresql+psycopg://u@remote.example.com/db"
    assert _resolve_connect_target(url) is None
    assert normalize_target(url).startswith(UNKNOWN_HOST)
    assert is_target_allowed(url, parse_extra_targets("remote.example.com:5432/db")) is False


def test_resolve_connect_target_pgport_applies_on_fallback_path_too(monkeypatch):
    """`$PGPORT` учитывается и в fallback-ветке (`postgres://`, диалект недоступен)."""
    monkeypatch.setenv("PGPORT", "6001")
    result = _resolve_connect_target("postgres://u@h.example.com/dev")
    assert result == ("h.example.com", 6001, "dev")


def test_resolve_connect_target_fallback_denies_invalid_pgport(monkeypatch):
    """Fallback-ветка (`postgres://`) тоже не подставляет 5432 молча при невалидном `PGPORT`."""
    monkeypatch.setenv("PGPORT", "not-a-port")
    assert _resolve_connect_target("postgres://u@h.example.com/dev") is None


# --- Регресс: $PGHOSTADDR/$PGSERVICE — дыра ниже уровня create_connect_args ---
#
# Найдено внешним ревью, воспроизведено эмпирически: SQLAlchemy вообще не читает
# PGHOSTADDR/PGSERVICE при сборке connect-args — их подставляет только сам libpq
# в момент реального psycopg.connect(...). С PGHOSTADDR=10.1.2.3 и PGSERVICE=prod
# в окружении процесса loopback-DSN `postgresql+psycopg://u@localhost:5459/udp_test`
# по-прежнему нормализовался в 'localhost:5459/udp_test' с allowed=True, хотя
# libpq реально уходит на hostaddr (или на host/port/dbname из pg_service.conf).
# Симметрично query-ключам hostaddr/service: цель считается нераспознанной
# безусловно, а не резолвится «как получится».


def test_pghostaddr_denies_loopback_dsn(monkeypatch):
    """`PGHOSTADDR` в окружении делает loopback-DSN нераспознанной целью — exploit shape.

    Тест намеренно берёт loopback DSN, а не удалённый: на удалённом DSN
    is_target_allowed и так был бы False (не loopback, allowlist пуст), и тест
    ничего не доказал бы даже при незакрытой дыре. Именно loopback-DSN — та
    форма, где старая реализация (safe_host на netloc) ошибочно давала True.
    """
    monkeypatch.setenv("PGHOSTADDR", "10.1.2.3")
    url = "postgresql+psycopg://u:p@localhost:5459/udp_test"
    assert _resolve_connect_target(url) is None
    assert is_target_allowed(url, frozenset()) is False
    assert normalize_target(url).startswith(UNKNOWN_HOST)


def test_pgservice_denies_loopback_dsn(monkeypatch):
    """`PGSERVICE` в окружении делает loopback-DSN нераспознанной целью — та же дыра."""
    monkeypatch.setenv("PGSERVICE", "prod")
    url = "postgresql+psycopg://u:p@localhost:5459/udp_test"
    assert _resolve_connect_target(url) is None
    assert is_target_allowed(url, frozenset()) is False
    assert normalize_target(url).startswith(UNKNOWN_HOST)


def test_pghostaddr_and_pgservice_unset_leaves_loopback_allowed(monkeypatch):
    """Обе переменные не заданы → поведение как сегодня, loopback разрешён."""
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    url = "postgresql+psycopg://u:p@localhost:5459/udp_test"
    assert is_target_allowed(url, frozenset()) is True
    assert normalize_target(url) == "localhost:5459/udp_test"


def test_pghostaddr_denies_on_fallback_path_too(monkeypatch):
    """`PGHOSTADDR` тоже проверяется до ветвления primary/fallback (`postgres://`)."""
    monkeypatch.setenv("PGHOSTADDR", "10.1.2.3")
    assert _resolve_connect_target("postgres://u:p@localhost:5459/udp_test") is None


def test_pgservice_denies_on_fallback_path_too(monkeypatch):
    """`PGSERVICE` тоже проверяется до ветвления primary/fallback (`postgres://`)."""
    monkeypatch.setenv("PGSERVICE", "prod")
    assert _resolve_connect_target("postgres://u:p@localhost:5459/udp_test") is None


def test_ensure_mutation_allowed_names_pghostaddr_in_error(monkeypatch):
    """Ошибка называет `PGHOSTADDR` по имени, а не только «нераспознанная цель».

    Пользователь с явно loopback-выглядящим DSN иначе не поймёт, что причина —
    в его собственном шелле, а не в DSN: DSN тут ни при чём.
    """
    monkeypatch.setenv("PGHOSTADDR", "10.1.2.3")
    url = "postgresql+psycopg://u:p@localhost:5459/udp_test"
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(url, "alembic")
    message = str(exc.value)
    assert "PGHOSTADDR" in message
    assert "PGSERVICE" not in message


def test_ensure_mutation_allowed_names_pgservice_in_error(monkeypatch):
    """Ошибка называет `PGSERVICE` по имени."""
    monkeypatch.setenv("PGSERVICE", "prod")
    url = "postgresql+psycopg://u:p@localhost:5459/udp_test"
    with pytest.raises(RuntimeError) as exc:
        ensure_mutation_allowed(url, "alembic")
    message = str(exc.value)
    assert "PGSERVICE" in message
    assert "PGHOSTADDR" not in message


# --- PGHOST/PGDATABASE — повторная проверка симметрии по запросу ревью -------
#
# Гипотеза (подтверждена эмпирически, не только рассуждением): SQLAlchemy не
# читает ни PGHOST, ни PGDATABASE при сборке connect-args/make_url — их (как и
# PGHOSTADDR/PGSERVICE) подставляет только сам libpq в реальном psycopg.connect().
# Разница с PGHOSTADDR/PGSERVICE: PGHOST/PGDATABASE — это ДЕФОЛТЫ для
# ОТСУТСТВУЮЩЕГО поля, а не отдельный параметр, замещающий явно заданное. DSN,
# где поле присутствует явно, полностью игнорирует одноимённую переменную
# окружения — резолв не читает os.environ вообще для host/dbname. Оба случая
# (поле отсутствует и поле присутствует) закрыты тестами ниже.


def test_pghost_ignored_when_dsn_has_explicit_host(monkeypatch):
    """`PGHOST` не подменяет явный host в DSN — SQLAlchemy его не читает."""
    monkeypatch.setenv("PGHOST", "evil.example.com")
    url = "postgresql+psycopg://u@localhost:5459/udp_dev"
    assert _resolve_connect_target(url) == ("localhost", 5459, "udp_dev")
    assert is_target_allowed(url, frozenset()) is True


def test_pghost_does_not_fill_hostless_dsn_either(monkeypatch):
    """`PGHOST` не используется и для hostless DSN — остаётся `UNKNOWN_HOST`, не evil-хост.

    Симметрия с PGHOSTADDR специально проверена и на этой форме: если бы
    SQLAlchemy когда-нибудь стал читать PGHOST как дефолт для отсутствующего
    host (сегодня — нет), это было бы новой дырой того же класса.
    """
    monkeypatch.setenv("PGHOST", "evil.example.com")
    url = "postgresql+psycopg:///somedb"
    resolved = _resolve_connect_target(url)
    assert resolved == ("", 5432, "somedb")
    assert normalize_target(url).startswith(UNKNOWN_HOST)
    assert is_target_allowed(url, frozenset()) is False


def test_pgdatabase_ignored_when_dsn_has_explicit_dbname(monkeypatch):
    """`PGDATABASE` не подменяет явный dbname в DSN — SQLAlchemy его не читает."""
    monkeypatch.setenv("PGDATABASE", "evildb")
    url = "postgresql+psycopg://u@localhost:5459/udp_dev"
    assert _resolve_connect_target(url) == ("localhost", 5459, "udp_dev")
    assert is_target_allowed(url, frozenset()) is True


def test_pgdatabase_does_not_fill_dbname_less_dsn_either(monkeypatch):
    """`PGDATABASE` не используется и для dbname-less DSN — тройка не совпадает ни с чем."""
    monkeypatch.setenv("PGDATABASE", "evildb")
    url = "postgresql+psycopg://u@remote.example.com:5432/"
    resolved = _resolve_connect_target(url)
    assert resolved == ("remote.example.com", 5432, "")
    result = normalize_target(url)
    assert result == "remote.example.com:5432/"
    with pytest.raises(ValueError):
        # Форму host:port/ (пустой dbname) parse_extra_targets в принципе не
        # может выдать как валидную запись — даже если её вписать в
        # DB_EXTRA_TARGETS руками, старт упадёт на валидации.
        parse_extra_targets(result)


def test_other_pg_env_vars_do_not_affect_target(monkeypatch):
    """Опционные (не цель-определяющие) `PG*`-переменные не меняют резолвленную тройку.

    По запросу ревью — обзор остальных стандартных libpq-переменных: PGSSLMODE,
    PGCONNECT_TIMEOUT, PGAPPNAME, PGOPTIONS, PGPASSFILE, PGSERVICEFILE (без
    PGSERVICE эта переменная ничего не активирует — сервис-файл читается только
    когда указано ИМЯ сервиса). Все они влияют на КАК подключаться
    (опции сессии/аутентификация/поиск файлов), а не КУДА — та же категория, что
    query-параметры вроде sslmode/channel_binding, уже не отбрасываемые как
    цель-определяющие.
    """
    for name, value in (
        ("PGSSLMODE", "require"),
        ("PGCONNECT_TIMEOUT", "5"),
        ("PGAPPNAME", "myapp"),
        ("PGOPTIONS", "-c foo=bar"),
        ("PGPASSFILE", "/tmp/.pgpass"),
        ("PGSERVICEFILE", "/tmp/pg_service.conf"),
    ):
        monkeypatch.setenv(name, value)
    url = "postgresql+psycopg://u@localhost:5459/udp_dev"
    assert _resolve_connect_target(url) == ("localhost", 5459, "udp_dev")
    assert is_target_allowed(url, frozenset()) is True
