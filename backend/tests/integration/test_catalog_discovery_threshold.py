"""Свой порог автопринятия систем (спека 3б §2.7, решение 14, DoD 4).

Порог систем читается рядом с порогом работ: `None` - правило к системе не
применяется целиком, включая строку «та же семья». Вид контекста читается под
блокировкой. Массовое автопринятие считает исход каждого кандидата по порогу
его вида.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa
from click.testing import CliRunner
from pydantic import ValidationError
from sqlalchemy.orm.attributes import set_committed_value

import cli
from config import Settings
from config import settings as app_settings
from models import CatalogContext, SemanticEvent
from services.family_change import (
    AutoAcceptError,
    Thresholds,
    apply_auto_accept,
    apply_publication_rules,
    preview_auto_accept,
    threshold_for,
    thresholds_from,
)
from tests.integration.test_work_variants_auto_accept import (
    _committed_deploy_scene,
    _deploy_scene,
    _read,
    cli_db,  # noqa: F401 - фикстура
)
from tests.integration.test_work_variants_family_change import (
    _bind_source,
    _ctx,
    _decision,
    _pending_columns,
    _publish,
    _rendered,
    _two_families,
    _unpublish,
    _with_variant,
)

pytestmark = pytest.mark.integration

WORK = Decimal("0.95")
SYSTEM = Decimal("0.80")
STEP_BELOW_SYSTEM = Decimal("0.79")


@pytest.fixture(autouse=True)
def _no_default_thresholds(monkeypatch):
    monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", None)
    monkeypatch.setattr(app_settings, "SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD", None)


def _set_kind(db, context_id: int, kind: str, user_id: int) -> None:
    db.flush()
    db.execute(
        sa.update(CatalogContext)
        .where(CatalogContext.id == context_id)
        .values(
            semantic_kind=kind, semantic_kind_source="manual",
            semantic_kind_by=user_id,
            semantic_kind_at=sa.func.now(),
        )
    )
    db.expire_all()


# ---------------------------------------------------------------------------
#  Настройка и выбор порога
# ---------------------------------------------------------------------------

def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, SECRET_KEY="x" * 32, **overrides)


class TestSettings:
    def test_defaults_to_none(self):
        assert _settings().SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD is None

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_env_string_is_none(self, blank):
        assert _settings(SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD=blank).SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD is None

    @pytest.mark.parametrize("bad", ["0", "-0.1", "1.01", "abc"])
    def test_out_of_range_is_rejected(self, bad):
        with pytest.raises(ValidationError):
            _settings(SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD=bad)

    @pytest.mark.parametrize("ok", ["0.85", "1", "0.0001"])
    def test_in_range_is_kept_as_exact_decimal(self, ok):
        got = _settings(SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD=ok).SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD
        assert got == Decimal(ok) and isinstance(got, Decimal)

    def test_thresholds_from_reads_both_settings(self):
        source = _settings(
            SEMANTIC_AUTO_ACCEPT_THRESHOLD="0.9", SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD="0.85"
        )
        assert thresholds_from(source) == Thresholds(Decimal("0.9"), Decimal("0.85"))
        assert thresholds_from(_settings()) == Thresholds(None, None)


class _Ctx:
    def __init__(self, kind):
        self.semantic_kind = kind


class TestThresholdFor:
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [("SYSTEM", Decimal("0.7")), ("WORK", Decimal("0.9")), ("UNKNOWN", Decimal("0.9"))],
    )
    def test_system_takes_the_system_threshold_every_other_kind_the_work_one(
        self, kind, expected
    ):
        assert threshold_for(_Ctx(kind), Thresholds(Decimal("0.9"), Decimal("0.7"))) == expected

    def test_none_is_returned_as_none_for_its_own_kind_only(self):
        thresholds = Thresholds(None, Decimal("0.7"))
        assert threshold_for(_Ctx("WORK"), thresholds) is None
        assert threshold_for(_Ctx("SYSTEM"), thresholds) == Decimal("0.7")


# ---------------------------------------------------------------------------
#  Правило публикации: порог систем
# ---------------------------------------------------------------------------

def _arm(db, scene, index: int, row: str, kind: str):
    """Строки таблицы публикации 3а, в которых правило действует: 1 - семьи
    нет, 3 - предложена текущая семья, 4 - автосемья без варианта, 5 -
    автосемья с вариантом. Предложение публикуется на отпечаток запроса вида
    контекста. Возвращает предложение."""
    confidence = {"1": "0.9", "3": "0.2", "4": "0.9", "5": "0.9"}[row]
    context_id = scene.context_ids[index]
    _set_kind(db, context_id, kind, scene.user.id)
    target = scene.family if row == "3" else scene.family_b
    suggestion = _publish(db, context_id, family_id=target.id)
    suggestion.confidence = Decimal(confidence)
    db.flush()
    if row == "3":
        _bind_source(db, scene, context_id, scene.family, "manual")
    elif row == "4":
        _bind_source(db, scene, context_id, scene.family, "auto_suggestion")
    elif row == "5":
        _with_variant(db, scene, context_id, scene.family, "auto_suggestion")
    return suggestion


def _row_scene(db, factories, row: str, kind: str):
    scene = _two_families(db, factories)
    scene.context_id = scene.context_ids[0]
    scene.suggestion = _arm(db, scene, 0, row, kind)
    return scene


def _kinds_scene(db, factories):
    """Один контекст-система и один контекст-работа в строке 1 таблицы."""
    scene = _two_families(db, factories, titles=("Система", "Работа"))
    scene.system_suggestion = _arm(db, scene, 0, "1", "SYSTEM")
    scene.work_suggestion = _arm(db, scene, 1, "1", "WORK")
    return scene


_ROWS = ["1", "3", "4", "5"]


class TestEmptySystemThreshold:
    """Порог работ задан, порог систем пуст: система не принимается ни по одной
    строке таблицы, а работа - как до фичи."""

    @pytest.mark.parametrize("row", _ROWS)
    def test_system_is_left_to_the_human_in_every_row(self, db_session, factories, row):
        """Порог работ (0,8) НИЖЕ уверенности строк 1, 4, 5 (0,9): та же строка
        у работы действует (соседний тест), поэтому система, взявшая порог
        работ вместо пустого своего, здесь была бы принята - на каждой строке,
        а не только на «той же семье»."""
        scene = _row_scene(db_session, factories, row, "SYSTEM")
        before = _pending_columns(_ctx(db_session, scene.context_id))
        context_before = _ctx(db_session, scene.context_id)
        family_before = (context_before.work_family_id, context_before.family_source)

        outcome = apply_publication_rules(
            db_session, suggestion_id=scene.suggestion.id,
            thresholds=Thresholds(Decimal("0.8"), None),
        )

        assert outcome is None
        context = _ctx(db_session, scene.context_id)
        assert (context.work_family_id, context.family_source) == family_before
        assert _pending_columns(context) == before
        assert _decision(db_session, scene.suggestion.id) == (None, None)

    @pytest.mark.parametrize("row", _ROWS)
    def test_the_same_row_for_work_acts(self, db_session, factories, row):
        scene = _row_scene(db_session, factories, row, "WORK")

        outcome = apply_publication_rules(
            db_session, suggestion_id=scene.suggestion.id, thresholds=Thresholds(Decimal("0.8"), None)
        )

        assert outcome is not None
        assert _decision(db_session, scene.suggestion.id)[0] in ("auto_accepted", "auto_pending")

    def test_both_thresholds_empty_touch_nothing(self, db_session, factories):
        scene = _row_scene(db_session, factories, "1", "SYSTEM")

        outcome = apply_publication_rules(
            db_session, suggestion_id=scene.suggestion.id, thresholds=Thresholds(None, None)
        )

        assert outcome is None
        assert _decision(db_session, scene.suggestion.id) == (None, None)


class TestSystemThreshold:
    def test_exactly_on_the_system_threshold_is_accepted(self, db_session, factories):
        scene = _row_scene(db_session, factories, "1", "SYSTEM")
        scene.suggestion.confidence = SYSTEM
        db_session.flush()

        outcome = apply_publication_rules(
            db_session, suggestion_id=scene.suggestion.id, thresholds=Thresholds(WORK, SYSTEM)
        )

        assert outcome is not None and outcome.kind == "assigned"
        assert _decision(db_session, scene.suggestion.id) == ("auto_accepted", None)

    def test_one_step_below_the_system_threshold_is_not_accepted(self, db_session, factories):
        scene = _row_scene(db_session, factories, "1", "SYSTEM")
        scene.suggestion.confidence = STEP_BELOW_SYSTEM
        db_session.flush()

        outcome = apply_publication_rules(
            db_session, suggestion_id=scene.suggestion.id, thresholds=Thresholds(WORK, SYSTEM)
        )

        assert outcome is None
        assert _decision(db_session, scene.suggestion.id) == (None, None)

    def test_system_follows_its_own_threshold_not_the_work_one(self, db_session, factories):
        """Уверенность 0,90 ниже порога работ 0,95 и выше порога систем 0,80:
        система принимается, работа - нет."""
        scene = _kinds_scene(db_session, factories)
        thresholds = Thresholds(WORK, SYSTEM)

        system_outcome = apply_publication_rules(
            db_session, suggestion_id=scene.system_suggestion.id, thresholds=thresholds
        )
        work_outcome = apply_publication_rules(
            db_session, suggestion_id=scene.work_suggestion.id, thresholds=thresholds
        )

        assert system_outcome is not None and system_outcome.kind == "assigned"
        assert work_outcome is None

    def test_work_follows_the_work_threshold_when_the_system_one_is_higher(
        self, db_session, factories
    ):
        work = _row_scene(db_session, factories, "1", "WORK")

        outcome = apply_publication_rules(
            db_session, suggestion_id=work.suggestion.id,
            thresholds=Thresholds(Decimal("0.80"), Decimal("0.99")),
        )

        assert outcome is not None and outcome.kind == "assigned"

    def test_system_threshold_alone_leaves_work_untouched(self, db_session, factories):
        scene = _kinds_scene(db_session, factories)

        applied = apply_publication_rules(
            db_session, suggestion_id=scene.system_suggestion.id,
            thresholds=Thresholds(None, SYSTEM),
        )
        skipped = apply_publication_rules(
            db_session, suggestion_id=scene.work_suggestion.id,
            thresholds=Thresholds(None, SYSTEM),
        )

        assert applied is not None and skipped is None

    def test_the_pending_family_records_the_system_threshold(self, db_session, factories):
        scene = _row_scene(db_session, factories, "5", "SYSTEM")

        outcome = apply_publication_rules(
            db_session, suggestion_id=scene.suggestion.id, thresholds=Thresholds(WORK, SYSTEM)
        )

        assert outcome is not None and outcome.kind == "pending"
        assert _pending_columns(_ctx(db_session, scene.context_id))[4] == SYSTEM

    def test_the_same_family_is_accepted_at_any_confidence_with_the_system_threshold_set(
        self, db_session, factories
    ):
        scene = _row_scene(db_session, factories, "3", "SYSTEM")

        outcome = apply_publication_rules(
            db_session, suggestion_id=scene.suggestion.id, thresholds=Thresholds(None, SYSTEM)
        )

        assert outcome is not None and outcome.kind == "unchanged"
        assert _decision(db_session, scene.suggestion.id) == ("auto_accepted", None)


class TestWorkerPassesBothThresholds:
    def test_rules_after_an_answer_get_both_thresholds_of_the_passed_settings(
        self, monkeypatch
    ):
        """Исполнитель передаёт правилу оба порога из СВОИХ настроек (тех, что
        ему переданы), а не только порог работ."""
        import contextlib

        import services.semantic_worker as worker

        seen: dict[str, object] = {}

        def _capture(db, *, suggestion_id, thresholds):
            seen["thresholds"] = thresholds

        monkeypatch.setattr(worker, "apply_publication_rules", _capture)
        passed = app_settings.model_copy(
            update={
                "SEMANTIC_AUTO_ACCEPT_THRESHOLD": Decimal("0.93"),
                "SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD": Decimal("0.81"),
            }
        )

        worker._apply_rules_in_new_session(
            lambda: contextlib.nullcontext(object()), 1, settings=passed
        )

        assert seen["thresholds"] == Thresholds(Decimal("0.93"), Decimal("0.81"))


class TestKindIsReadUnderTheLock:
    """Вид - часть тела запроса, поэтому смена вида между ответом и правилом
    делает предложение устаревшим; а вот объект контекста, уже лежащий в
    сессии, может нести прежний вид. Порог выбирается по виду, перечитанному
    под блокировкой."""

    @pytest.mark.parametrize(
        ("db_kind", "cached_kind", "thresholds"),
        [
            ("SYSTEM", "WORK", Thresholds(None, SYSTEM)),
            ("WORK", "SYSTEM", Thresholds(SYSTEM, None)),
        ],
        ids=["became_system", "became_work"],
    )
    def test_threshold_follows_the_kind_in_the_database_not_the_cached_object(
        self, db_session, factories, db_kind, cached_kind, thresholds
    ):
        scene = _row_scene(db_session, factories, "1", db_kind)
        cached = db_session.get(CatalogContext, scene.context_id)
        set_committed_value(cached, "semantic_kind", cached_kind)
        assert cached.semantic_kind == cached_kind

        outcome = apply_publication_rules(
            db_session, suggestion_id=scene.suggestion.id, thresholds=thresholds
        )

        assert outcome is not None and outcome.kind == "assigned"
        assert _decision(db_session, scene.suggestion.id) == ("auto_accepted", None)

    @pytest.mark.parametrize(
        ("answered_kind", "before_lock_kind", "thresholds"),
        [
            ("SYSTEM", "WORK", Thresholds(None, SYSTEM)),
            ("WORK", "SYSTEM", Thresholds(SYSTEM, None)),
        ],
        ids=["system_again_at_the_lock", "work_again_at_the_lock"],
    )
    def test_threshold_follows_the_kind_at_the_lock_not_before_it(
        self, db_session, factories, monkeypatch, answered_kind, before_lock_kind, thresholds
    ):
        """Вид сменился после ответа и вернулся к виду ответа к моменту
        блокировки: отпечаток снова текущий, и порог обязан быть порогом вида
        под блокировкой. Чтение вида ДО блокировки увидело бы промежуточный вид
        и взяло бы его (пустой) порог."""
        import services.family_change as family_change

        scene = _row_scene(db_session, factories, "1", answered_kind)
        _set_kind(db_session, scene.context_id, before_lock_kind, scene.user.id)
        original = family_change._acquire_single

        def _kind_returns_then_lock(db, context_id, family_id, **kwargs):
            _set_kind(db, context_id, answered_kind, scene.user.id)
            return original(db, context_id, family_id, **kwargs)

        monkeypatch.setattr(family_change, "_acquire_single", _kind_returns_then_lock)

        outcome = apply_publication_rules(
            db_session, suggestion_id=scene.suggestion.id, thresholds=thresholds
        )

        assert outcome is not None and outcome.kind == "assigned"
        assert _decision(db_session, scene.suggestion.id) == ("auto_accepted", None)


# ---------------------------------------------------------------------------
#  Массовое автопринятие
# ---------------------------------------------------------------------------

def _mixed_scene(db, factories):
    """Пять контекстов сцены автопринятия с уверенностью 0,9; `c_assign` -
    система. При пороге работ 0,95 и пороге систем 0,80 действует только он
    (назначение) и `c_confirm` (та же семья)."""
    scene = _deploy_scene(db, factories)
    _set_kind(db, scene.c_assign, "SYSTEM", scene.user.id)
    _unpublish(db, scene.suggestions[scene.c_assign])
    scene.suggestions[scene.c_assign] = _publish(
        db, scene.c_assign, family_id=scene.family_b.id
    )
    db.flush()
    return scene


def _set_thresholds(monkeypatch, work, system):
    monkeypatch.setattr(app_settings, "SEMANTIC_AUTO_ACCEPT_THRESHOLD", work)
    monkeypatch.setattr(app_settings, "SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD", system)


class TestMassAutoAccept:
    def test_outcome_is_counted_by_the_threshold_of_the_kind(
        self, db_session, factories, monkeypatch
    ):
        _mixed_scene(db_session, factories)
        _set_thresholds(monkeypatch, WORK, SYSTEM)

        preview = preview_auto_accept(db_session)

        assert dict(preview.by_outcome) == {"confirm": 1, "assign": 1, "pending": 0, "none": 3}
        assert preview.total == 5

    def test_the_preview_carries_both_thresholds(self, db_session, factories, monkeypatch):
        _mixed_scene(db_session, factories)
        _set_thresholds(monkeypatch, WORK, SYSTEM)

        preview = preview_auto_accept(db_session)

        assert (preview.threshold, preview.system_threshold) == (WORK, SYSTEM)

    def test_apply_changes_only_the_context_the_thresholds_allow(
        self, db_session, factories, monkeypatch
    ):
        scene = _mixed_scene(db_session, factories)
        _set_thresholds(monkeypatch, WORK, SYSTEM)
        preview = preview_auto_accept(db_session)

        applied = apply_auto_accept(db_session, preview_hash=preview.preview_hash)

        assert dict(applied) == {"confirm": 1, "assign": 1, "pending": 0}
        assert _ctx(db_session, scene.c_assign).work_family_id == scene.family_b.id
        assert _ctx(db_session, scene.c_pending).pending_family_id is None
        assert _ctx(db_session, scene.c_auto).work_family_id == scene.family.id
        # Назначение системы записано с порогом систем, а не работ.
        logged = [
            event.payload.get("threshold")
            for event in db_session.execute(
                sa.select(SemanticEvent).where(
                    SemanticEvent.context_id == scene.c_assign,
                    SemanticEvent.event_type == "context_family_assigned",
                )
            ).scalars()
        ]
        assert logged == ["0.80"]

    def test_empty_system_threshold_leaves_systems_out_of_the_candidates(
        self, db_session, factories, monkeypatch
    ):
        scene = _mixed_scene(db_session, factories)
        _set_thresholds(monkeypatch, Decimal("0.80"), None)

        preview = preview_auto_accept(db_session)

        assert preview.total == 4
        assert dict(preview.by_outcome) == {"confirm": 1, "assign": 1, "pending": 1, "none": 1}
        applied = apply_auto_accept(db_session, preview_hash=preview.preview_hash)
        assert dict(applied) == {"confirm": 1, "assign": 1, "pending": 1}
        assert _ctx(db_session, scene.c_assign).work_family_id is None
        assert _decision(db_session, scene.suggestions[scene.c_assign].id) == (None, None)

    def test_system_threshold_alone_is_enough_to_preview(
        self, db_session, factories, monkeypatch
    ):
        _mixed_scene(db_session, factories)
        _set_thresholds(monkeypatch, None, SYSTEM)

        preview = preview_auto_accept(db_session)

        assert preview.threshold is None and preview.system_threshold == SYSTEM
        assert preview.total == 1
        assert dict(preview.by_outcome)["assign"] == 1

    def test_without_any_threshold_the_preview_refuses(self, db_session, factories):
        _mixed_scene(db_session, factories)

        with pytest.raises(AutoAcceptError) as excinfo:
            preview_auto_accept(db_session)

        assert excinfo.value.code == "threshold_missing"

    def test_the_hash_changes_when_only_the_system_threshold_changes(
        self, db_session, factories, monkeypatch
    ):
        _mixed_scene(db_session, factories)
        _set_thresholds(monkeypatch, WORK, SYSTEM)
        first = preview_auto_accept(db_session)
        _set_thresholds(monkeypatch, WORK, Decimal("0.85"))
        second = preview_auto_accept(db_session)

        assert dict(first.by_outcome) == dict(second.by_outcome)
        assert first.preview_hash != second.preview_hash

    def test_the_hash_changes_when_only_the_work_threshold_changes(
        self, db_session, factories, monkeypatch
    ):
        _mixed_scene(db_session, factories)
        _set_thresholds(monkeypatch, WORK, SYSTEM)
        first = preview_auto_accept(db_session)
        _set_thresholds(monkeypatch, Decimal("0.96"), SYSTEM)
        second = preview_auto_accept(db_session)

        assert dict(first.by_outcome) == dict(second.by_outcome)
        assert first.preview_hash != second.preview_hash

    def test_a_kind_change_that_keeps_the_request_is_still_a_changed_preview(
        self, db_session, factories, monkeypatch
    ):
        """Вид кандидата входит в строку хэша: смена WORK -> UNKNOWN между показом
        и применением тело запроса не меняет (предложение остаётся текущим), но
        показанное состояние уже другое - применение отказывает."""
        scene = _deploy_scene(db_session, factories)
        _set_thresholds(monkeypatch, Decimal("0.80"), None)
        before = _rendered(db_session, scene.c_assign).request_hash
        preview_hash = preview_auto_accept(db_session).preview_hash
        _set_kind(db_session, scene.c_assign, "UNKNOWN", scene.user.id)
        assert _rendered(db_session, scene.c_assign).request_hash == before

        with pytest.raises(AutoAcceptError) as excinfo:
            apply_auto_accept(db_session, preview_hash=preview_hash)

        assert excinfo.value.code == "preview_changed"

    def test_apply_refuses_a_hash_taken_under_another_system_threshold(
        self, db_session, factories, monkeypatch
    ):
        scene = _mixed_scene(db_session, factories)
        _set_thresholds(monkeypatch, WORK, SYSTEM)
        stale_hash = preview_auto_accept(db_session).preview_hash
        _set_thresholds(monkeypatch, WORK, Decimal("0.85"))

        with pytest.raises(AutoAcceptError) as excinfo:
            apply_auto_accept(db_session, preview_hash=stale_hash)

        assert excinfo.value.code == "preview_changed"
        assert _ctx(db_session, scene.c_assign).work_family_id is None


class TestAutoAcceptRoute:
    def test_preview_returns_both_thresholds(
        self, admin_client, db_session, factories, monkeypatch
    ):
        _mixed_scene(db_session, factories)
        _set_thresholds(monkeypatch, WORK, SYSTEM)

        response = admin_client.post("/api/v1/semantic/auto-accept/preview")

        assert response.status_code == 200
        body = response.json()
        assert (body["threshold"], body["system_threshold"]) == ("0.95", "0.80")

    def test_preview_returns_null_for_an_empty_threshold(
        self, admin_client, db_session, factories, monkeypatch
    ):
        _mixed_scene(db_session, factories)
        _set_thresholds(monkeypatch, None, SYSTEM)

        body = admin_client.post("/api/v1/semantic/auto-accept/preview").json()

        assert (body["threshold"], body["system_threshold"]) == (None, "0.80")

    def test_the_preview_hash_of_the_route_applies(
        self, admin_client, db_session, factories, monkeypatch
    ):
        scene = _mixed_scene(db_session, factories)
        _set_thresholds(monkeypatch, WORK, SYSTEM)
        preview_hash = admin_client.post("/api/v1/semantic/auto-accept/preview").json()[
            "preview_hash"
        ]

        response = admin_client.post(
            "/api/v1/semantic/auto-accept", json={"preview_hash": preview_hash}
        )

        assert response.status_code == 200
        assert response.json() == {"applied": {"confirm": 1, "assign": 1, "pending": 0}}
        assert _ctx(db_session, scene.c_assign).work_family_id == scene.family_b.id


class TestAutoAcceptCommand:
    def test_command_prints_both_thresholds_and_applies_by_kind(
        self, cli_db, committing_db, committing_factories, monkeypatch  # noqa: F811
    ):
        scene = _committed_deploy_scene(committing_db, committing_factories)
        _set_kind(committing_db, scene.c_assign, "SYSTEM", scene.user.id)
        committing_db.commit()
        # предложение системы публикуется на отпечаток вида системы
        _unpublish(committing_db, scene.suggestions[scene.c_assign])
        _publish(committing_db, scene.c_assign, family_id=scene.family_b.id)
        committing_db.commit()
        _set_thresholds(monkeypatch, WORK, SYSTEM)

        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept", "--yes"])

        assert result.exit_code == 0, result.output
        assert "Порог работ: 0.95" in result.output
        assert "Порог систем: 0.80" in result.output
        assert "assign: 1" in result.output
        context = _read(cli_db, CatalogContext, scene.c_assign)
        assert context.work_family_id == scene.family_b.id
        assert _read(cli_db, CatalogContext, scene.c_pending).pending_family_id is None

    def test_only_the_system_threshold_is_not_a_refusal(
        self, cli_db, committing_db, committing_factories, monkeypatch  # noqa: F811
    ):
        _committed_deploy_scene(committing_db, committing_factories)
        _set_thresholds(monkeypatch, None, SYSTEM)

        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept", "--yes"])

        assert result.exit_code == 0, result.output
        assert "Порог работ: не задан" in result.output

    def test_no_thresholds_refuse_and_name_both_settings(
        self, cli_db, committing_db, committing_factories  # noqa: F811
    ):
        _committed_deploy_scene(committing_db, committing_factories)

        result = CliRunner().invoke(cli.cli, ["semantic-auto-accept", "--yes"])

        assert result.exit_code == 1
        assert "SEMANTIC_AUTO_ACCEPT_THRESHOLD" in result.output
        assert "SEMANTIC_SYSTEM_AUTO_ACCEPT_THRESHOLD" in result.output

