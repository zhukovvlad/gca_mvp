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

    # Кавычка после имени констрейнта в сообщении psycopg — якорь: без него
    # `match` был бы неразличимым префиксом `ck_..._category_source_pairs` и
    # прошёл бы, даже если сработал соседний парный констрейнт.
    with pytest.raises(IntegrityError, match='"ck_position_items_category_source"'):
        db_session.execute(
            sa.text("UPDATE position_items SET category_source = 'guess' WHERE id = :rid"),
            {"rid": chapter_row.id},
        )
        db_session.flush()


def test_pairs_constraint_is_not_weakened(db_session, chapter_row):
    """Источник без статьи по-прежнему невозможен.

    Обратная пара — статья без источника — покрыта
    `test_schema_constraints.py::TestPositionItemCategoryColumns::test_category_and_source_come_only_together`,
    не здесь: этот тест бьёт РОВНО в одно ограничение (source без category).
    """
    with pytest.raises(IntegrityError, match="ck_position_items_category_source_pairs"):
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
    with pytest.raises(
        IntegrityError, match="fk_estimate_category_overrides_work_category_id"
    ):
        db_session.execute(
            sa.text("DELETE FROM work_categories WHERE id = :cid"),
            {"cid": override_row.work_category_id},
        )
        db_session.flush()


def test_author_of_a_live_decision_cannot_be_deleted(db_session, override_row):
    with pytest.raises(IntegrityError, match="fk_estimate_category_overrides_assigned_by"):
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
