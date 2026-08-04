"""Catalog indexes survive job titles longer than the btree limit.

Дефект, найденный при импорте реальной сметы: в ячейке «Наименование работы»
оказалась целая спецификация на 5077 символов (9260 байт, 3424 после сжатия), и
`INSERT` в `catalog_positions` падал `ProgramLimitExceeded` — btree не индексирует
значения длиннее 2704 байт. Смета не сохранялась целиком: домен откатывался, job
уходил в `error`.

Под ударом были два индекса из 0002:

* `ix_catalog_positions_standard_job_title` — обычный btree «для поиска в UI»
  (AGENTS.md §4). Поиск по каталогу сделан через `ILIKE '%…%'` (решение фазы 5
  §6.4: `crud/catalog.py`, `crud/review.py`, `crud/rate_standards.py`), а такой
  запрос обычный btree обслуживать не может в принципе — то есть индекс не
  использовался ничем и только ронял импорт. Удаляется без замены: индекс по
  префиксу (`left(...)`) ILIKE-поиску тоже не помогает, а стоит записи. Если
  каталог вырастет настолько, что `ILIKE` станет узким местом, правильный ответ —
  GIN по `pg_trgm`, отдельным решением.
* `uq_catalog_positions_norm_unit` — UNIQUE (normalized_job_title,
  COALESCE(unit_id,-1)), идентичность работы (§4) и арбитр `ON CONFLICT` в
  get-or-create матчинга (§5, шаг 4.3). Выкинуть нельзя, поэтому от названия в
  индексе лежит `sha256`: фиксированные 32 байта, длина названия перестаёт иметь
  значение.

**Идентичность работы не изменилась** — это по-прежнему пара (полное
`normalized_job_title`, единица). Хэш здесь технический: он синхронизирует
конкурентный get-or-create, а совпадение подтверждается сравнением полного текста
в `services/matching.py`. Нормализация не тронута, поэтому `norm_version`
остаётся 1 и перевыпуск ключей кэша (§11) не нужен. Ни название, ни его
нормализованная форма не обрезаются: обрезка склеила бы две разные спецификации с
общим началом в одну расценку.

`sha256(bytea)` — встроенная функция PostgreSQL 11+, расширения не нужны.
Приведение `::bytea` (а не `convert_to(x,'UTF8')`) обязательно: `convert_to`
объявлена `stable`, и PostgreSQL не примет её в выражении индекса.

**Обратные слэши удваиваются перед приведением.** `text::bytea` разбирает вход как
escape-формат bytea, а не берёт байты строки. Замер на PG 16.14: «C:\\temp\\x» и
«перегородка \\ стена» дают `invalid input syntax for type bytea` (то есть импорт
падал бы снова, только на другом файле), а «\\x41» и «\\101» молча приводятся к
тому же байту, что «A», — три разные работы получили бы один хэш. После
`replace(x, '\\', '\\\\')` приведение побайтово равно UTF-8-представлению строки
(проверено на 15 краевых входах против `hashlib.sha256`, тест
`test_matching.py::TestNormHash`).

Слэши записаны **escape-строками** (`E'\\\\'`), а не обычными литералами. Обычный
литерал `'\\'` означает один символ только при `standard_conforming_strings=on`: при
`off` и `CREATE INDEX`, и `ON CONFLICT` этого вида — синтаксическая ошибка (замер на
PG 16.14). `E'…'` разбирает escape-последовательности при любом значении настройки, а
в дереве выражения даёт ровно то же (`'\\'::text`), поэтому индекс сопоставляется с
арбитром в обоих написаниях. Настройка при этом остаётся дефолтной — код просто
перестал от неё зависеть.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-04
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

#: Предел btree на индексный кортеж. Порог для проверки в downgrade, не точная
#: граница: PostgreSQL применяет его к кортежу ПОСЛЕ сжатия.
BTREE_MAX_INDEX_BYTES = 2704

#: Хэш-выражение идентичности. Литералы (не параметры) — то же требование, что и к
#: арбитру ON CONFLICT в `services/matching.py`: с параметром PostgreSQL перестаёт
#: сопоставлять выражение с индексом, как только psycopg3 подготовит запрос.
#: Копия этого выражения живёт в `services.matching.norm_hash`; расхождение ловит
#: `test_matching.py::TestNormHash::test_index_expression_matches_the_migration`.
_NORM_HASH_INDEX = r"""
CREATE UNIQUE INDEX uq_catalog_positions_norm_hash_unit
ON catalog_positions (
    sha256(replace(normalized_job_title, E'\\', E'\\\\')::bytea),
    COALESCE(unit_id, -1)
)
"""

_OLD_NORM_INDEX = """
CREATE UNIQUE INDEX uq_catalog_positions_norm_unit
ON catalog_positions (normalized_job_title, COALESCE(unit_id, -1))
"""


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_catalog_positions_standard_job_title")
    op.execute("DROP INDEX IF EXISTS uq_catalog_positions_norm_unit")
    op.execute(_NORM_HASH_INDEX)


def downgrade() -> None:
    """Возврат к индексам 0002 — **возможен не всегда**.

    Старое определение не проиндексирует названия длиннее предела btree, поэтому
    после первой же успешно загруженной сметы с многокилобайтовым наименованием
    откат этой миграции невозможен: `CREATE INDEX` упадёт `ProgramLimitExceeded`.
    Это не дефект downgrade, а то же ограничение PostgreSQL, из-за которого
    миграция и появилась, — схему откатить можно, данные нет. Зафиксировано в
    AGENTS.md §11: откат приложения ниже 0003 после появления таких данных не
    поддерживается; вернуться можно только удалив или сократив эти строки, а это
    потеря данных и решение человека, не миграции.

    Отказ делается громким и объясняющим: сырое `ProgramLimitExceeded` из
    середины `CREATE INDEX` оператору ничего не говорит.
    """
    # Проверка ДО первого DDL: Alembic на PostgreSQL откатил бы транзакцию и без
    # этого, но полагаться на откат там, где можно просто не начинать, незачем.
    _refuse_if_long_titles_present()
    op.execute("DROP INDEX IF EXISTS uq_catalog_positions_norm_hash_unit")
    op.execute(_OLD_NORM_INDEX)
    op.execute(
        "CREATE INDEX ix_catalog_positions_standard_job_title "
        "ON catalog_positions (standard_job_title)"
    )


def _refuse_if_long_titles_present() -> None:
    """Проверка перед восстановлением старых индексов.

    Порог приблизительный в одну сторону: точный предел btree применяется к
    индексному кортежу ПОСЛЕ сжатия, поэтому строка длиннее 2704 байт может
    оказаться индексируемой (хорошо сжимаемый текст). Отказ здесь, значит, иногда
    строже необходимого — и это верный размен: цена ложного отказа — ручная
    проверка оператором, цена пропуска — `ProgramLimitExceeded` из середины
    `CREATE INDEX` без объяснения, что произошло.
    """
    conn = op.get_bind()
    suspicious = conn.execute(
        sa.text(
            "SELECT count(*) FROM catalog_positions "
            "WHERE octet_length(normalized_job_title) > :limit "
            "   OR octet_length(standard_job_title) > :limit"
        ),
        {"limit": BTREE_MAX_INDEX_BYTES},
    ).scalar_one()
    if not suspicious:
        return
    raise RuntimeError(
        f"Откат миграции 0003 невозможен: в catalog_positions есть {suspicious} "
        f"строк(и) с наименованием длиннее {BTREE_MAX_INDEX_BYTES} байт. Индексы "
        "версии 0002 такие значения не индексируют (ProgramLimitExceeded), именно "
        "поэтому миграция и появилась. Откат ниже 0003 возможен только после "
        "удаления или сокращения этих строк — это потеря данных, решение человека. "
        "Найти их (условие то же, что в проверке): "
        "SELECT id, octet_length(normalized_job_title) AS norm_bytes, "
        "octet_length(standard_job_title) AS title_bytes FROM catalog_positions "
        f"WHERE octet_length(normalized_job_title) > {BTREE_MAX_INDEX_BYTES} "
        f"   OR octet_length(standard_job_title) > {BTREE_MAX_INDEX_BYTES} "
        "ORDER BY GREATEST(octet_length(normalized_job_title), "
        "octet_length(standard_job_title)) DESC;"
    )
