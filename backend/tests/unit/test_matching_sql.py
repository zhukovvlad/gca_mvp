"""Форма SQL матчинга, от которой зависит попадание в raw-SQL индексы (§11)."""
from __future__ import annotations

from sqlalchemy.dialects import postgresql

from services.matching import NO_UNIT_SENTINEL, catalog_get_or_create_statement


class TestOnConflictArbiter:
    def test_arbiter_is_rendered_as_a_literal_expression(self):
        """`ON CONFLICT (normalized_job_title, coalesce(unit_id, -1))` — литералом.

        Со связанным параметром вместо `-1` PostgreSQL перестаёт сопоставлять
        выражение с `uq_catalog_positions_norm_unit`, как только psycopg3
        подготовит запрос (`prepare_threshold = 5`). То есть падало бы не в
        маленьком тесте, а на реальной смете. Интеграционный дубль этой проверки —
        `test_matching.py::test_batch_larger_than_the_prepare_threshold`.
        """
        sql = str(catalog_get_or_create_statement().compile(dialect=postgresql.dialect()))

        assert (
            f"ON CONFLICT (normalized_job_title, coalesce(unit_id, {NO_UNIT_SENTINEL})) DO NOTHING"
            in sql
        )
        assert "coalesce(unit_id, %" not in sql
