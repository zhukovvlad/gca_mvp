"""Форма SQL матчинга, от которой зависит попадание в raw-SQL индексы (§11)."""
from __future__ import annotations

from sqlalchemy.dialects import postgresql

from services.matching import NO_UNIT_SENTINEL, catalog_get_or_create_statement

BACKSLASH = chr(92)


class TestOnConflictArbiter:
    def test_arbiter_is_rendered_as_a_literal_expression(self):
        """Арбитр `ON CONFLICT` — выражение из литералов, без связанных параметров.

        Со связанным параметром вместо литерала PostgreSQL перестаёт сопоставлять
        выражение с `uq_catalog_positions_norm_hash_unit`, как только psycopg3
        подготовит запрос (`prepare_threshold = 5`). То есть падало бы не в
        маленьком тесте, а на реальной смете. Интеграционный дубль этой проверки —
        `test_matching.py::test_batch_larger_than_the_prepare_threshold`.

        Требование касается всех трёх литералов выражения: сентинела единицы и
        обоих аргументов `replace()`, которым обратные слэши удваиваются перед
        приведением к `bytea` (миграция 0003).
        """
        sql = str(catalog_get_or_create_statement().compile(dialect=postgresql.dialect()))

        arbiter = sql.split("ON CONFLICT", 1)[1]
        assert f"coalesce(unit_id, {NO_UNIT_SENTINEL})" in arbiter
        assert "sha256(" in arbiter
        # E-строки, а не обычные литералы: при standard_conforming_strings=off
        # обычный литерал из одного обратного слэша даёт синтаксическую ошибку,
        # и арбитр перестал бы
        # разбираться вовсе. Интеграционный дубль —
        # test_matching.py::test_expression_does_not_depend_on_standard_conforming_strings.
        assert (
            f"replace(normalized_job_title, E'{BACKSLASH * 2}', E'{BACKSLASH * 4}')" in arbiter
        )
        assert "DO NOTHING" in arbiter
        # Ни один аргумент выражения не должен уехать в параметр.
        assert "%" not in arbiter
