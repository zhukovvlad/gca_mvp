"""Параметры семьи без предела: роль, вид значений, границы диапазона, значения не
из списка, частичный запрос и правка схемы на месте (спека
`2026-10-19-catalog-parameters-design.md` §2.2).

Новых таблиц нет; меняются шесть существующих.

**`family_parameters`**: `role` (главный / второстепенный), `value_kind` (точные
значения / диапазон), `closed` («только из списка»), `range_from` / `range_to` /
`range_step`, `origin` (чей параметр для пересборки), `archived_at` (убранный
параметр архивируется). Номер параметра в версии — `BETWEEN 1 AND 100`, а
`UNIQUE (schema_id, ordinal)` остаётся и покрывает архивные параметры: номер не
используется повторно. Предел «не больше 10 активных» держит сервис, схема его не
выражает.

**`family_parameter_schemas`**: `revision` — счётчик изменений действующей версии
на месте.

**`family_parameter_values`**: `range_lo` / `range_hi` (границы значения
параметра-диапазона) и `retired_at` (значение, ушедшее из списка без слияния).

**`context_parameter_values`**: `off_list_text` (значение не из списка) и
`refresh_requested_at` («уточняется»). Источник `off_list` входит в перечень
источников, а равносильность «значение ⇔ источник» переписана под него.

**`semantic_jobs`**: `requested_ordinals smallint[]` — номера параметров
частичного запроса значений; в уникальный индекс заданий не входит.

**`semantic_events`**: тип `family_schema_edited`, предмет — семья.

**Заполнение.** Значения по умолчанию колонок (`'main'`, `'exact'`, `false`,
`'model'`, `revision 0`) заполняют существующие строки сами; отдельного `UPDATE`
нет. Ни одна строка `semantic_jobs`, `work_variants`, `context_parameter_values`
не меняется, ни один `request_hash` не пересчитывается.

**Трёхзначная логика CHECK** (`docs/pitfalls/db.md`): форма диапазона
`ck_family_parameters_range_shape` — один тотальный предикат из двух полных
ветвей; ветвь `range` открывается атомом `range_step IS NOT NULL`, ложным при
пустом шаге, поэтому недостающая граница даёт `false`, а не `NULL`, а проверка
`range_step > 0` стоит до деления на шаг.

**`downgrade`** отказывает при любой строке с непустым носителем (параметр с
ролью, видом, закрытым списком, происхождением, архивом или номером больше 3;
версия с `revision > 0`; значение с границами или `retired_at`; строка контекста
не из списка или с отметкой «уточняется»; задание с `requested_ordinals`; событие
нового типа) и называет каждый счётчик; на пустых носителях возвращает прежние
выражения CHECK, список источников и список типов журнала.

Выражения CHECK и списки `IN (...)` продублированы константами в `models.py`
(миграция не импортирует `models.py` — она обязана быть неизменной во времени);
parity — `test_catalog_parameters_schema.py`.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-19
"""
import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

# Литералы значений дублируют models.py: миграция обязана быть неизменной во
# времени, поэтому значения зафиксированы строками, а не импортированы.
PARAMETER_ROLES = "'main', 'secondary'"
PARAMETER_VALUE_KINDS = "'exact', 'range'"
PARAMETER_ORIGINS = "'model', 'human'"
VALUE_SOURCES = "'name', 'path', 'manual', 'path_conflict', 'none', 'off_list'"
SEMANTIC_EVENT_TYPES_SQL = (
    "'context_created', 'context_split', 'context_merged', 'members_moved', "
    "'members_marked_stale', 'kind_set', 'name_role_set', 'context_family_assigned', "
    "'context_archived', 'routing_rules_dropped', 'family_created', 'family_updated', "
    "'family_activated', 'family_archived', 'family_merged', "
    "'context_variant_assigned', 'context_family_pending', 'context_not_work', "
    "'family_schema_frozen', 'family_schema_value_added', 'family_variants_merged', "
    "'context_reopened', 'family_schema_edited'"
)

# Значения до 0022 — для возврата `downgrade` (дублируют 0019 и 0021 побуквенно).
PREVIOUS_VALUE_SOURCES = "'name', 'path', 'manual', 'path_conflict', 'none'"
PREVIOUS_SEMANTIC_EVENT_TYPES_SQL = (
    "'context_created', 'context_split', 'context_merged', 'members_moved', "
    "'members_marked_stale', 'kind_set', 'name_role_set', 'context_family_assigned', "
    "'context_archived', 'routing_rules_dropped', 'family_created', 'family_updated', "
    "'family_activated', 'family_archived', 'family_merged', "
    "'context_variant_assigned', 'context_family_pending', 'context_not_work', "
    "'family_schema_frozen', 'family_schema_value_added', 'family_variants_merged', "
    "'context_reopened'"
)
PREVIOUS_CK_PARAMETER_ORDINAL_RANGE = "ordinal BETWEEN 1 AND 3"
PREVIOUS_CK_CONTEXT_VALUE_SOURCE_PAIR = (
    "(value_id IS NULL) = (source IN ('path_conflict', 'none'))"
)
PREVIOUS_CK_EVENT_SUBJECT_BY_TYPE = (
    "(event_type IN ('family_created', 'family_updated', 'family_activated', "
    "'family_archived', 'family_merged', 'family_schema_frozen', "
    "'family_schema_value_added', 'family_variants_merged')) = (family_id IS NOT NULL)"
)

# Равносильности CHECK — побуквенно равны одноимённым в models.py; расхождение
# ловит test_catalog_parameters_schema.py::TestParityWithMigration.
CK_PARAMETER_ORDINAL_RANGE = "ordinal BETWEEN 1 AND 100"
CK_PARAMETER_ROLE = "role IN ('main', 'secondary')"
CK_PARAMETER_VALUE_KIND = "value_kind IN ('exact', 'range')"
CK_PARAMETER_ORIGIN = "origin IN ('model', 'human')"
CK_PARAMETER_RANGE_SHAPE = (
    "(value_kind = 'exact' AND range_from IS NULL AND range_to IS NULL "
    "AND range_step IS NULL) "
    "OR (value_kind = 'range' AND range_from IS NOT NULL AND range_to IS NOT NULL "
    "AND range_step IS NOT NULL AND range_step > 0 AND range_from < range_to "
    "AND mod(range_to - range_from, range_step) = 0 "
    "AND (range_to - range_from) / range_step <= 28 AND closed)"
)
CK_PARAMETER_VALUE_RANGE_ORDER = "range_lo IS NULL OR range_hi IS NULL OR range_lo < range_hi"
CK_PARAMETER_VALUE_RETIRED_NOT_MERGED = "retired_at IS NULL OR merged_into_id IS NULL"
CK_CONTEXT_VALUE_SOURCE_PAIR = (
    "(value_id IS NULL) = (source IN ('path_conflict', 'none', 'off_list'))"
)
CK_CONTEXT_VALUE_OFF_LIST_TEXT = "(source = 'off_list') = (off_list_text IS NOT NULL)"
CK_SEMANTIC_JOBS_REQUESTED_ORDINALS = "requested_ordinals IS NULL OR kind = 'context_values'"
CK_EVENT_SUBJECT_BY_TYPE = (
    "(event_type IN ('family_created', 'family_updated', 'family_activated', "
    "'family_archived', 'family_merged', 'family_schema_frozen', "
    "'family_schema_value_added', 'family_variants_merged', "
    "'family_schema_edited')) = (family_id IS NOT NULL)"
)


def upgrade() -> None:
    # --- 1. Версия схемы: счётчик правок на месте -----------------------------
    op.add_column(
        "family_parameter_schemas",
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    # --- 2. Параметр: роль, вид, границы, происхождение, архив ----------------
    op.add_column(
        "family_parameters",
        sa.Column("role", sa.Text(), nullable=False, server_default=sa.text("'main'")),
    )
    op.add_column(
        "family_parameters",
        sa.Column("value_kind", sa.Text(), nullable=False, server_default=sa.text("'exact'")),
    )
    op.add_column(
        "family_parameters",
        sa.Column("closed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("family_parameters", sa.Column("range_from", sa.Numeric(), nullable=True))
    op.add_column("family_parameters", sa.Column("range_to", sa.Numeric(), nullable=True))
    op.add_column("family_parameters", sa.Column("range_step", sa.Numeric(), nullable=True))
    op.add_column(
        "family_parameters",
        sa.Column("origin", sa.Text(), nullable=False, server_default=sa.text("'model'")),
    )
    op.add_column(
        "family_parameters", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_constraint("ck_family_parameters_ordinal_range", "family_parameters", type_="check")
    op.create_check_constraint(
        "ck_family_parameters_ordinal_range", "family_parameters", CK_PARAMETER_ORDINAL_RANGE
    )
    op.create_check_constraint("ck_family_parameters_role", "family_parameters", CK_PARAMETER_ROLE)
    op.create_check_constraint(
        "ck_family_parameters_value_kind", "family_parameters", CK_PARAMETER_VALUE_KIND
    )
    op.create_check_constraint(
        "ck_family_parameters_origin", "family_parameters", CK_PARAMETER_ORIGIN
    )
    op.create_check_constraint(
        "ck_family_parameters_range_shape", "family_parameters", CK_PARAMETER_RANGE_SHAPE
    )

    # --- 3. Значение списка: границы и «ушло из списка» -------------------------
    op.add_column("family_parameter_values", sa.Column("range_lo", sa.Numeric(), nullable=True))
    op.add_column("family_parameter_values", sa.Column("range_hi", sa.Numeric(), nullable=True))
    op.add_column(
        "family_parameter_values",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_family_parameter_values_range_order", "family_parameter_values",
        CK_PARAMETER_VALUE_RANGE_ORDER,
    )
    op.create_check_constraint(
        "ck_family_parameter_values_retired_not_merged", "family_parameter_values",
        CK_PARAMETER_VALUE_RETIRED_NOT_MERGED,
    )

    # --- 4. Строка контекста: значение не из списка и «уточняется» ---------------
    op.add_column("context_parameter_values", sa.Column("off_list_text", sa.Text(), nullable=True))
    op.add_column(
        "context_parameter_values",
        sa.Column("refresh_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(
        "ck_context_parameter_values_source", "context_parameter_values", type_="check"
    )
    op.create_check_constraint(
        "ck_context_parameter_values_source", "context_parameter_values",
        f"source IN ({VALUE_SOURCES})",
    )
    op.drop_constraint(
        "ck_context_parameter_values_source_value_pair", "context_parameter_values",
        type_="check",
    )
    op.create_check_constraint(
        "ck_context_parameter_values_source_value_pair", "context_parameter_values",
        CK_CONTEXT_VALUE_SOURCE_PAIR,
    )
    op.create_check_constraint(
        "ck_context_parameter_values_off_list_text", "context_parameter_values",
        CK_CONTEXT_VALUE_OFF_LIST_TEXT,
    )

    # --- 5. Задание: номера параметров частичного запроса -------------------------
    op.add_column(
        "semantic_jobs", sa.Column("requested_ordinals", sa.ARRAY(sa.SmallInteger()), nullable=True)
    )
    op.create_check_constraint(
        "ck_semantic_jobs_requested_ordinals", "semantic_jobs",
        CK_SEMANTIC_JOBS_REQUESTED_ORDINALS,
    )

    # --- 6. Журнал: тип `family_schema_edited` (предмет — семья) -------------------
    op.drop_constraint("ck_semantic_events_event_type", "semantic_events", type_="check")
    op.create_check_constraint(
        "ck_semantic_events_event_type", "semantic_events",
        f"event_type IN ({SEMANTIC_EVENT_TYPES_SQL})",
    )
    op.drop_constraint("ck_semantic_events_subject_by_type", "semantic_events", type_="check")
    op.create_check_constraint(
        "ck_semantic_events_subject_by_type", "semantic_events", CK_EVENT_SUBJECT_BY_TYPE
    )


def _downgrade_blockers(bind) -> dict[str, int]:
    """Шесть счётчиков, каждый сам по себе держит откат. Вынесены в функцию, чтобы
    тест проверил каждый отдельно: в валидной схеме значение с границей, строка не
    из списка и задание с частичным запросом существуют независимо друг от друга."""

    def count(query: str) -> int:
        return bind.execute(sa.text(query)).scalar_one()

    return {
        "family_parameters": count(
            "SELECT count(*) FROM family_parameters WHERE role <> 'main' "
            "OR value_kind <> 'exact' OR closed OR origin <> 'model' "
            "OR archived_at IS NOT NULL OR ordinal > 3"
        ),
        "family_parameter_schemas": count(
            "SELECT count(*) FROM family_parameter_schemas WHERE revision > 0"
        ),
        "family_parameter_values": count(
            "SELECT count(*) FROM family_parameter_values "
            "WHERE range_lo IS NOT NULL OR range_hi IS NOT NULL OR retired_at IS NOT NULL"
        ),
        "context_parameter_values": count(
            "SELECT count(*) FROM context_parameter_values WHERE source = 'off_list' "
            "OR off_list_text IS NOT NULL OR refresh_requested_at IS NOT NULL"
        ),
        "semantic_jobs_requested_ordinals": count(
            "SELECT count(*) FROM semantic_jobs WHERE requested_ordinals IS NOT NULL"
        ),
        "semantic_events_schema_edited": count(
            "SELECT count(*) FROM semantic_events WHERE event_type = 'family_schema_edited'"
        ),
    }


def _downgrade_refusal(blockers: dict[str, int]) -> str | None:
    if not any(blockers.values()):
        return None
    return (
        f"Откат 0022 невозможен: параметров с новыми свойствами — "
        f"{blockers['family_parameters']}, "
        f"версий схем с правками на месте — {blockers['family_parameter_schemas']}, "
        f"значений с границами или выведенных из списка — "
        f"{blockers['family_parameter_values']}, "
        f"строк контекстов не из списка или с отметкой — "
        f"{blockers['context_parameter_values']}, "
        f"заданий с частичным запросом — {blockers['semantic_jobs_requested_ordinals']}, "
        f"событий family_schema_edited — {blockers['semantic_events_schema_edited']}. Это "
        "решения человека и оплаченные ответы модели; удалить их — решение человека, а не "
        "миграции."
    )


def downgrade() -> None:
    bind = op.get_bind()
    refusal = _downgrade_refusal(_downgrade_blockers(bind))
    if refusal is not None:
        raise RuntimeError(refusal)

    # Журнал.
    op.drop_constraint("ck_semantic_events_subject_by_type", "semantic_events", type_="check")
    op.create_check_constraint(
        "ck_semantic_events_subject_by_type", "semantic_events",
        PREVIOUS_CK_EVENT_SUBJECT_BY_TYPE,
    )
    op.drop_constraint("ck_semantic_events_event_type", "semantic_events", type_="check")
    op.create_check_constraint(
        "ck_semantic_events_event_type", "semantic_events",
        f"event_type IN ({PREVIOUS_SEMANTIC_EVENT_TYPES_SQL})",
    )

    # Задание.
    op.drop_constraint("ck_semantic_jobs_requested_ordinals", "semantic_jobs", type_="check")
    op.drop_column("semantic_jobs", "requested_ordinals")

    # Строка контекста.
    op.drop_constraint(
        "ck_context_parameter_values_off_list_text", "context_parameter_values", type_="check"
    )
    op.drop_constraint(
        "ck_context_parameter_values_source_value_pair", "context_parameter_values",
        type_="check",
    )
    op.create_check_constraint(
        "ck_context_parameter_values_source_value_pair", "context_parameter_values",
        PREVIOUS_CK_CONTEXT_VALUE_SOURCE_PAIR,
    )
    op.drop_constraint(
        "ck_context_parameter_values_source", "context_parameter_values", type_="check"
    )
    op.create_check_constraint(
        "ck_context_parameter_values_source", "context_parameter_values",
        f"source IN ({PREVIOUS_VALUE_SOURCES})",
    )
    op.drop_column("context_parameter_values", "refresh_requested_at")
    op.drop_column("context_parameter_values", "off_list_text")

    # Значение списка.
    op.drop_constraint(
        "ck_family_parameter_values_retired_not_merged", "family_parameter_values",
        type_="check",
    )
    op.drop_constraint(
        "ck_family_parameter_values_range_order", "family_parameter_values", type_="check"
    )
    op.drop_column("family_parameter_values", "retired_at")
    op.drop_column("family_parameter_values", "range_hi")
    op.drop_column("family_parameter_values", "range_lo")

    # Параметр.
    op.drop_constraint("ck_family_parameters_range_shape", "family_parameters", type_="check")
    op.drop_constraint("ck_family_parameters_origin", "family_parameters", type_="check")
    op.drop_constraint("ck_family_parameters_value_kind", "family_parameters", type_="check")
    op.drop_constraint("ck_family_parameters_role", "family_parameters", type_="check")
    op.drop_constraint("ck_family_parameters_ordinal_range", "family_parameters", type_="check")
    op.create_check_constraint(
        "ck_family_parameters_ordinal_range", "family_parameters",
        PREVIOUS_CK_PARAMETER_ORDINAL_RANGE,
    )
    op.drop_column("family_parameters", "archived_at")
    op.drop_column("family_parameters", "origin")
    op.drop_column("family_parameters", "range_step")
    op.drop_column("family_parameters", "range_to")
    op.drop_column("family_parameters", "range_from")
    op.drop_column("family_parameters", "closed")
    op.drop_column("family_parameters", "value_kind")
    op.drop_column("family_parameters", "role")

    # Версия схемы.
    op.drop_column("family_parameter_schemas", "revision")
