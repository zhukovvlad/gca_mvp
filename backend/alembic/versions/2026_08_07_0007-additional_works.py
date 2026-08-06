"""Допработы: расшивка «Сведений по дополнительным работам» по статьям.

Агрегатная строка «Дополнительные работы» с версии парсера 2.0.0 больше не
попадает в `positions` (спека Ф4 §2.1) — её деньги живут расшитыми по строкам
текстового поля «Сведения по дополнительным работам» в этой таблице.

**`proposal_id`, а не `estimate_id` (отступление от брифа, спека §2.3).**
Агрегатная строка принадлежит предложению (её деньги лежат в колоночном блоке
конкретного подрядчика), и резолв ссылки в статью определён В ПРЕДЕЛАХ
предложения, а не сметы (спека §2.5): номера разделов между лотами могут
повторяться, и хранение на смете смешало бы их и сделало бы контрольную сумму
по предложению нечем сверить. Путь до сметы — `estimate → lot → proposal`, два
join'а, ровно как у `position_items`.

**Пять CHECK — каждый закрывает свою ложь:**
- `ck_..._total_amount` и `ck_..._ordinal` — деньги и порядок строк не могут
  быть отрицательными; ноль — валидная сумма (замер: такая строка есть в
  реальном файле, спека §1.2);
- `ck_..._title_not_blank` — та же идиома, что у `work_categories` (миграция
  0005): `btrim` по явному набору пробельных символов, включая неразрывный
  (`chr(160)`), поэтому вердикт не зависит от `LC_CTYPE`;
- `ck_..._unresolved_ref` делает непредставимым состояние «статья есть, а
  ссылки, из которой она получена, — нет»: единственный источник статьи в v1 —
  ссылка, поэтому обратное было бы ложью о происхождении;
- `ck_..._raw_line_pairs` закрывает вторую половину того же: без исходной
  строки текста не может быть ни ссылки, ни статьи — иначе схема разрешала бы
  привязку, происхождение которой нечем проверить. `raw_line IS NULL` — только
  у нераспределённой записи (остаток `T − P` или полная сумма при пустых
  «Сведениях»).

**Отдельного индекса по `proposal_id` нет намеренно.** Составной UNIQUE
`uq_estimate_additional_works_proposal_ordinal` обслуживает поиск по левому
префиксу — тот же приём, которым в миграции 0006 `uq_position_items_proposal_id_id`
заменил `idx_position_items_proposal_id` (замерено в Ф3 на 60 000 строк).

**Оба FK именуются явно** в `ALTER TABLE`, иначе `alembic check` и `downgrade`
опирались бы на автогенерируемые имена. `proposal_id` — `ON DELETE CASCADE`
(replace-флоу удаляет предложение одним DELETE, §5 общего контракта схемы);
`work_category_id` — `ON DELETE RESTRICT` (статью нельзя удалить, пока на неё
ссылается запись допработ — тот же принцип, что у `fk_position_items_work_category_id`).

Частичный индекс `idx_..._work_category_id` объявлен ДЕКЛАРАТИВНО
(`postgresql_where`), а не через `op.execute`, — чтобы не выпасть из
детектора дрейфа `alembic check` (замер Ф3 §1.3 факт 6).

`downgrade()` снимает индекс, таблицу целиком (CASCADE constraints уходят
вместе с таблицей). Защитного отказа, как в 0003, не требуется: данные этой
таблицы производные от неизменяемого `estimate_raw_data.raw_data` — при
необходимости смета перезагружается штатным `replace=true`. Но обещать
«всегда восстановимо перезагрузкой файла» нельзя: исходный файл может уйти по
ретенции (`AGENTS.md` §8).

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-07
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# Выражение дублирует models.TITLE_BLANK_CHARS_SQL намеренно: миграция обязана
# быть неизменной во времени (то же правило, что у литералов в 0004/0005/0006).
# Расхождение ловят parity-тесты test_schema_constraints.py::TestAdditionalWorksSchema.
TITLE_BLANK_CHARS = "' ' || chr(9) || chr(10) || chr(13) || chr(160)"


def upgrade() -> None:
    op.create_table(
        "estimate_additional_works",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("proposal_id", sa.BigInteger(), nullable=False),
        # Порядок строк «Сведений»; нераспределённая запись (остаток или полная
        # сумма) получает последний ordinal.
        sa.Column("ordinal", sa.Integer(), nullable=False),
        # «3.2.2» как в тексте; NULL у нераспределённой записи.
        sa.Column("chapter_ref_raw", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("total_amount", sa.Numeric(), nullable=False),
        sa.Column("work_category_id", sa.BigInteger(), nullable=True),
        # Исходная строка «Сведений»; NULL у нераспределённой записи.
        sa.Column("raw_line", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposals.id"], ondelete="CASCADE",
            name="fk_estimate_additional_works_proposal_id",
        ),
        sa.ForeignKeyConstraint(
            ["work_category_id"], ["work_categories.id"], ondelete="RESTRICT",
            name="fk_estimate_additional_works_work_category_id",
        ),
        sa.CheckConstraint("total_amount >= 0", name="ck_estimate_additional_works_total_amount"),
        sa.CheckConstraint("ordinal > 0", name="ck_estimate_additional_works_ordinal"),
        sa.CheckConstraint(
            f"btrim(title, {TITLE_BLANK_CHARS}) <> ''",
            name="ck_estimate_additional_works_title_not_blank",
        ),
        sa.CheckConstraint(
            "chapter_ref_raw IS NOT NULL OR work_category_id IS NULL",
            name="ck_estimate_additional_works_unresolved_ref",
        ),
        sa.CheckConstraint(
            "raw_line IS NOT NULL OR (chapter_ref_raw IS NULL AND work_category_id IS NULL)",
            name="ck_estimate_additional_works_raw_line_pairs",
        ),
        sa.UniqueConstraint(
            "proposal_id", "ordinal", name="uq_estimate_additional_works_proposal_ordinal"
        ),
    )
    # Отдельного индекса по proposal_id нет — см. докстринг модуля: обслуживает
    # левый префикс uq_estimate_additional_works_proposal_ordinal.
    op.create_index(
        "idx_estimate_additional_works_work_category_id",
        "estimate_additional_works",
        ["work_category_id"],
        postgresql_where=sa.text("work_category_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_estimate_additional_works_work_category_id",
        table_name="estimate_additional_works",
    )
    op.drop_table("estimate_additional_works")
