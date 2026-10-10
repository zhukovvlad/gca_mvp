"""Кривая порога систем: метки, доли, достаточность (спека 3б §2.7, решения 15
и 16). Скрипт `scripts/measure_system_threshold.py` приложение не импортирует;
тест вправе импортировать скрипт и гоняет его загрузчики на тестовой базе
через соединение тестовой сессии.
"""
from __future__ import annotations

import ast
import datetime as dt
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from models import CatalogContext, SemanticEvent
from scripts import measure_system_threshold as script
from scripts.measure_system_threshold import (
    HIGH_CONFIDENCE,
    MIN_HIGH_LABELS,
    MIN_LABELS,
    THRESHOLDS,
    Label,
    curve,
    load_confidences,
    load_labels,
    main,
    sufficient,
)
from tests.integration.test_semantic_queue_api import _published, _scene

pytestmark = pytest.mark.integration

_SCRIPT = Path(script.__file__)


def _label(confidence: str, correct: bool = True, suggestion_id: int = 1) -> Label:
    return Label(suggestion_id=suggestion_id, confidence=Decimal(confidence), correct=correct)


# ---------------------------------------------------------------------------
#  Чистые функции: пороги, кривая, достаточность
# ---------------------------------------------------------------------------

class TestThresholds:
    def test_grid_runs_from_eighty_to_ninety_nine_hundredths_by_one(self):
        assert THRESHOLDS[0] == Decimal("0.80") and THRESHOLDS[-1] == Decimal("0.99")
        assert len(THRESHOLDS) == 20
        steps = {b - a for a, b in zip(THRESHOLDS, THRESHOLDS[1:], strict=False)}
        assert steps == {Decimal("0.01")}

    def test_constants(self):
        assert (300, 100, Decimal("0.9")) == (MIN_LABELS, MIN_HIGH_LABELS, HIGH_CONFIDENCE)


class TestCurve:
    def test_shares_and_precision_at_three_thresholds(self):
        labels = [
            _label("0.99", True), _label("0.95", True), _label("0.92", False),
            _label("0.90", False), _label("0.85", True), _label("0.85", False),
            _label("0.81", True), _label("0.70", False),
        ]
        confidences = [Decimal(x) for x in (
            "0.99", "0.95", "0.92", "0.90", "0.85", "0.85", "0.81", "0.70", "0.97", "0.99",
        )]

        by_threshold = {p.threshold: p for p in curve(labels, confidences)}

        low = by_threshold[Decimal("0.80")]
        assert (low.pass_share, low.precision, low.labels) == (
            Decimal("0.9000"), Decimal("0.5714"), 7,
        )
        mid = by_threshold[Decimal("0.90")]
        assert (mid.pass_share, mid.precision, mid.labels) == (
            Decimal("0.6000"), Decimal("0.5000"), 4,
        )
        high = by_threshold[Decimal("0.95")]
        assert (high.pass_share, high.precision, high.labels) == (
            Decimal("0.4000"), Decimal("1.0000"), 2,
        )

    def test_a_value_exactly_on_the_threshold_passes_and_counts(self):
        points = {p.threshold: p for p in curve([_label("0.90")], [Decimal("0.90")])}

        assert points[Decimal("0.90")].labels == 1
        assert points[Decimal("0.90")].pass_share == Decimal("1.0000")
        assert points[Decimal("0.91")].labels == 0
        assert points[Decimal("0.91")].pass_share == Decimal("0.0000")

    def test_no_labels_above_the_threshold_gives_no_precision(self):
        (first, *_rest) = curve([_label("0.5")], [Decimal("0.99")])

        assert (first.threshold, first.precision, first.labels) == (Decimal("0.80"), None, 0)
        assert first.pass_share == Decimal("1.0000")

    def test_no_suggestions_at_all_gives_zero_share_and_no_precision(self):
        points = curve([], [])

        assert len(points) == 20
        assert {(p.pass_share, p.precision, p.labels) for p in points} == {
            (Decimal(0), None, 0)
        }


def _labels(total: int, high: int) -> list[Label]:
    return [_label("0.95") for _ in range(high)] + [_label("0.5") for _ in range(total - high)]


class TestSufficient:
    def test_exactly_the_limits_is_sufficient(self):
        assert sufficient(_labels(300, 100)) is True

    def test_one_label_short_of_the_total_is_not(self):
        assert sufficient(_labels(299, 100)) is False

    def test_one_label_short_in_the_high_band_is_not(self):
        assert sufficient(_labels(300, 99)) is False

    def test_the_high_band_boundary_is_inclusive_on_confidence(self):
        on_the_line = [_label("0.9") for _ in range(100)] + [_label("0.5") for _ in range(200)]
        under_the_line = [_label("0.89") for _ in range(100)] + [_label("0.5") for _ in range(200)]

        assert sufficient(on_the_line) is True
        assert sufficient(under_the_line) is False


# ---------------------------------------------------------------------------
#  Метки на базе (решение 15): по входу на каждое правило
# ---------------------------------------------------------------------------

def _system(db, context_id: int, user_id: int) -> None:
    db.execute(
        sa.update(CatalogContext)
        .where(CatalogContext.id == context_id)
        .values(
            semantic_kind="SYSTEM", semantic_kind_source="manual",
            semantic_kind_by=user_id, semantic_kind_at=dt.datetime.now(dt.UTC),
        )
    )
    db.expire_all()


def _pending_event(db, suggestion, *, outcome: str, event_type="context_family_pending"):
    db.add(
        SemanticEvent(
            context_id=suggestion.context_id,
            family_id=None,
            event_type=event_type,
            payload={
                "pending_family_id": suggestion.family_id,
                "source": "manual",
                "suggestion_id": suggestion.id,
                "outcome": outcome,
            },
            actor_id=None,
        )
    )
    db.flush()


class _World:
    pass


def _world(db, factories, admin, count: int) -> _World:
    world = _World()
    world.scene = _scene(db, factories, admin, titles=tuple(f"Строка {i}" for i in range(count)))
    world.free = list(world.scene.context_ids)
    world.admin = admin
    world.db = db
    return world


def _make(world, *, confidence, decision, kind="SYSTEM", family=True, by_human=None):
    """Предложение на очередном контексте сцены; вид контекста задан до
    публикации: отпечаток предложения считается по виду."""
    context_id = world.free.pop(0)
    if kind == "SYSTEM":
        _system(world.db, context_id, world.admin.id)
    human = decision in ("accepted", "accepted_pending", "rejected", "other_family") if (
        by_human is None
    ) else by_human
    return _published(
        world.db, context_id,
        family_id=world.scene.family.id if family else None,
        confidence=confidence, decision=decision,
        decided_by=world.admin.id if human else None,
    )


class TestLabels:
    def test_decisions_map_to_labels_and_everything_else_is_not_a_label(
        self, db_session, factories, admin_user
    ):
        world = _world(db_session, factories, admin_user, 12)
        accepted = _make(world, confidence="0.95", decision="accepted")
        accepted_pending = _make(world, confidence="0.94", decision="accepted_pending")
        rejected = _make(world, confidence="0.93", decision="rejected")
        other = _make(world, confidence="0.92", decision="other_family")
        _make(world, confidence="0.91", decision="auto_accepted")
        _make(world, confidence="0.90", decision="auto_pending")
        _make(world, confidence="0.89", decision="auto_superseded")
        _make(world, confidence="0.88", decision=None)
        _make(world, confidence="0.87", decision="rejected", family=False)
        _make(world, confidence="0.86", decision="accepted", kind="WORK")
        _make(world, confidence="0.85", decision="rejected", kind="WORK")

        labels, excluded = load_labels(db_session.connection())

        assert excluded == 0
        assert {lab.suggestion_id: lab.correct for lab in labels} == {
            accepted.id: True, accepted_pending.id: True, rejected.id: False, other.id: False,
        }
        assert {lab.suggestion_id: lab.confidence for lab in labels} == {
            accepted.id: Decimal("0.95"), accepted_pending.id: Decimal("0.94"),
            rejected.id: Decimal("0.93"), other.id: Decimal("0.92"),
        }

    @pytest.mark.parametrize("outcome", ["cancelled", "superseded"])
    def test_a_rejection_named_by_a_cancelled_pending_is_excluded_and_counted(
        self, db_session, factories, admin_user, outcome
    ):
        world = _world(db_session, factories, admin_user, 2)
        kept = _make(world, confidence="0.95", decision="rejected")
        dropped = _make(world, confidence="0.96", decision="rejected")
        _pending_event(db_session, dropped, outcome=outcome)

        labels, excluded = load_labels(db_session.connection())

        assert [lab.suggestion_id for lab in labels] == [kept.id]
        assert excluded == 1

    def test_a_pending_event_with_another_outcome_does_not_exclude(
        self, db_session, factories, admin_user
    ):
        world = _world(db_session, factories, admin_user, 1)
        rejected = _make(world, confidence="0.95", decision="rejected")
        _pending_event(db_session, rejected, outcome="applied")

        labels, excluded = load_labels(db_session.connection())

        assert [lab.suggestion_id for lab in labels] == [rejected.id]
        assert excluded == 0

    def test_an_event_of_another_type_does_not_exclude(self, db_session, factories, admin_user):
        world = _world(db_session, factories, admin_user, 1)
        rejected = _make(world, confidence="0.95", decision="rejected")
        _pending_event(
            db_session, rejected, outcome="cancelled", event_type="context_family_assigned"
        )

        labels, excluded = load_labels(db_session.connection())

        assert [lab.suggestion_id for lab in labels] == [rejected.id]
        assert excluded == 0

    def test_a_cancelled_pending_does_not_exclude_an_accepted_decision(
        self, db_session, factories, admin_user
    ):
        world = _world(db_session, factories, admin_user, 1)
        accepted = _make(world, confidence="0.95", decision="accepted")
        _pending_event(db_session, accepted, outcome="cancelled")

        labels, excluded = load_labels(db_session.connection())

        assert [(lab.suggestion_id, lab.correct) for lab in labels] == [(accepted.id, True)]
        assert excluded == 0

    def test_event_naming_another_suggestion_does_not_exclude_this_one(
        self, db_session, factories, admin_user
    ):
        world = _world(db_session, factories, admin_user, 2)
        named = _make(world, confidence="0.95", decision="rejected")
        other = _make(world, confidence="0.96", decision="rejected")
        _pending_event(db_session, named, outcome="cancelled")

        labels, excluded = load_labels(db_session.connection())

        assert [lab.suggestion_id for lab in labels] == [other.id]
        assert excluded == 1


class TestConfidences:
    def test_all_system_suggestions_with_a_family_count_whatever_the_decision(
        self, db_session, factories, admin_user
    ):
        world = _world(db_session, factories, admin_user, 7)
        _make(world, confidence="0.99", decision=None)
        _make(world, confidence="0.98", decision="accepted")
        _make(world, confidence="0.97", decision="auto_accepted")
        _make(world, confidence="0.96", decision="rejected")
        _make(world, confidence="0.95", decision="rejected", family=False)
        _make(world, confidence="0.94", decision=None, kind="WORK")
        _make(world, confidence="0.93", decision=None, family=False)

        values = load_confidences(db_session.connection())

        assert sorted(values) == [Decimal("0.96"), Decimal("0.97"), Decimal("0.98"), Decimal("0.99")]


class TestCurveOnTheDatabase:
    def test_end_to_end_shares_and_precision(self, db_session, factories, admin_user):
        world = _world(db_session, factories, admin_user, 14)
        for confidence, decision in (
            ("0.99", "accepted"), ("0.95", "accepted_pending"), ("0.92", "other_family"),
            ("0.90", "rejected"), ("0.85", "accepted"), ("0.85", "rejected"),
            ("0.81", "accepted"), ("0.70", "rejected"),
        ):
            _make(world, confidence=confidence, decision=decision)
        _make(world, confidence="0.97", decision="auto_accepted")
        _make(world, confidence="0.99", decision=None)
        cancelled = _make(world, confidence="0.96", decision="rejected")
        _pending_event(db_session, cancelled, outcome="cancelled")
        not_excluded = _make(world, confidence="0.83", decision="rejected")
        _pending_event(db_session, not_excluded, outcome="applied")
        _make(world, confidence="0.94", decision=None, kind="WORK")

        conn = db_session.connection()
        labels, excluded = load_labels(conn)
        points = {p.threshold: p for p in curve(labels, load_confidences(conn))}

        assert (len(labels), excluded) == (9, 1)
        low = points[Decimal("0.80")]
        assert (low.pass_share, low.precision, low.labels) == (
            Decimal("0.9167"), Decimal("0.5000"), 8,
        )
        mid = points[Decimal("0.90")]
        assert (mid.pass_share, mid.precision, mid.labels) == (
            Decimal("0.5833"), Decimal("0.5000"), 4,
        )
        high = points[Decimal("0.95")]
        assert (high.pass_share, high.precision, high.labels) == (
            Decimal("0.4167"), Decimal("1.0000"), 2,
        )


# ---------------------------------------------------------------------------
#  Скрипт: только чтение, без импорта приложения, main
# ---------------------------------------------------------------------------

_ALLOWED_ROOTS = set(sys.stdlib_module_names) | {"sqlalchemy", "psycopg"}


def _imported_roots(source: str) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "относительный импорт тянет приложение"
            roots.add((node.module or "").split(".")[0])
    return roots


def _sql_strings(source: str) -> list[str]:
    """Строковые литералы модуля, кроме его докстроки."""
    tree = ast.parse(source)
    docstring = tree.body[0].value if isinstance(tree.body[0], ast.Expr) else None
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node is not docstring
    ]


class TestScriptDiscipline:
    def test_imports_only_the_standard_library_and_sqlalchemy(self):
        roots = _imported_roots(_SCRIPT.read_text(encoding="utf-8"))

        assert roots <= _ALLOWED_ROOTS, sorted(roots - _ALLOWED_ROOTS)

    def test_the_import_check_sees_an_application_import(self):
        assert "models" in _imported_roots("from models import CatalogContext\n")
        assert "services" in _imported_roots("import services.semantic_request\n")

    def test_the_text_opens_a_read_only_transaction(self):
        statements = _sql_strings(_SCRIPT.read_text(encoding="utf-8"))

        assert statements.count("SET TRANSACTION READ ONLY") == 1

    def test_no_sql_string_of_the_script_writes(self):
        statements = _sql_strings(_SCRIPT.read_text(encoding="utf-8"))

        writers = [
            text for text in statements
            if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE)\b", text, re.I)
        ]
        assert writers == []

    def test_the_sql_scan_sees_a_writing_statement(self):
        assert _sql_strings("x = 'DELETE FROM t'") == ["DELETE FROM t"]

    def test_the_connection_helper_really_refuses_a_write(self, db_engine):
        with script.connect_readonly(db_engine) as conn:
            assert conn.execute(sa.text("SHOW transaction_read_only")).scalar_one() == "on"
            with pytest.raises(sa.exc.DBAPIError):
                conn.execute(sa.text("CREATE TEMP TABLE must_not_exist (x int)"))


class TestMain:
    def test_without_the_environment_variable_it_refuses(self, monkeypatch, capsys):
        monkeypatch.delenv("DATABASE_URL", raising=False)

        assert main([]) == 2
        assert capsys.readouterr().out == ""

    def test_prints_the_table_the_verdict_and_the_excluded_count(
        self, db_engine, monkeypatch, capsys
    ):
        # База ЭТОГО прогона (`db_engine` мигрирован), а не голый TEST_DATABASE_URL:
        # под xdist у воркёра своя база, а базовая может быть не мигрирована.
        monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))

        assert main([]) == 0

        lines = capsys.readouterr().out.splitlines()
        rows = [line for line in lines if line[:4] in {f"0.{n}" for n in range(80, 100)}]
        assert [row[:4] for row in rows] == [f"{t:.2f}" for t in THRESHOLDS]
        assert any(line.startswith("Достаточность: нет") for line in lines)
        assert any("решений 0 из 300" in line and "0 из 100" in line for line in lines)
        assert lines[-1].endswith(": 0")

    def test_prints_what_the_loaders_returned(self, db_engine, monkeypatch, capsys):
        """`main` печатает именно загруженное: число исключённых, вердикт
        достаточности и точки кривой по меткам и уверенностям загрузчиков, а
        не константы (вход с ненулевыми значениями)."""
        monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
        labels = _labels(300, 100)
        monkeypatch.setattr(script, "load_labels", lambda conn: (labels, 7))
        monkeypatch.setattr(
            script, "load_confidences", lambda conn: [Decimal("0.95"), Decimal("0.5")]
        )

        assert main([]) == 0

        lines = capsys.readouterr().out.splitlines()
        assert lines[-1].endswith(": 7")
        assert any(line.startswith("Достаточность: да") for line in lines)
        assert any("решений 300 из 300" in line and "100 из 100" in line for line in lines)
        (row_080,) = (line for line in lines if line.startswith("0.80"))
        assert row_080.split() == ["0.80", "0.5000", "1.0000", "100"]
