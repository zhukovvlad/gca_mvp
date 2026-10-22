"""Пересборка схемы с параметрами человека (спека 3в §2.8).

Тело запроса схемы: блок «уже заданы человеком», предел «от 0 до N» и номера
1..N появляются только при параметрах человека, без них тело прежнее. Предел
ответа берётся из материала задания: ответ при восьми параметрах человека —
не больше двух, при десяти модель не вызывается вовсе. Запись версии: номера
уплотняются, параметры человека и строки их значений переносятся, «уточняется»
остаётся недостающим. Оценка пересборки считает значения по нынешним
параметрам модели и несёт `revision` версии. Путь опросчика — настоящий:
`process_one` с подменой клиента модели, `claim_next` и `record_result`.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

import services.semantic_worker as worker
from config import settings as app_settings
from crud.work_variants import context_variant_out, rebuild_preview
from models import (
    CatalogContext,
    ContextParameterValue,
    FamilyParameter,
    FamilyParameterSchema,
    FamilyParameterValue,
    SemanticEvent,
    SemanticJob,
    SemanticJobAttempt,
    SemanticJobKind,
    WorkVariant,
)
from services.semantic_client import ModelResponse
from services.semantic_reconcile import estimate_enqueue, values_need
from services.semantic_worker import process_one
from services.variant_answer import SchemaAnswer
from services.variant_request import (
    SCHEMA_PROMPT,
    SchemaParameterIn,
    SchemaRequestMaterial,
    load_schema_material,
    load_values_material,
    paths_hash_of,
    render_schema_request,
    schema_prompt_for,
)
from services.work_families import WorkFamilyError
from services.work_variants import (
    apply_values,
    bump_revision,
    freeze_schema,
    merge_parameter_values,
    rebuild_schema,
)
from tests.integration.parameters_edit_support import PARAMS, draft, edit, same_drafts
from tests.integration.test_work_variants_core import _answer, _named, _world
from tests.integration.test_work_variants_material import (
    _bind,
    _chain_context,
    _settings,
    _uid,
)
from tests.integration.test_work_variants_schema import _family, _param, _schema, _value
from tests.integration.test_work_variants_schema_life import (
    _active_world,
    _actor,
    _second_context,
    _settle_context,
)

pytestmark = pytest.mark.integration

#: Позже любого `now()` базы: задание, созданное в тесте, уже «созрело».
NOW = dt.datetime(2099, 1, 1, 12, 0, tzinfo=dt.UTC)

S = app_settings.model_copy(
    update={
        "SEMANTIC_AUTO_ACCEPT_THRESHOLD": None,
        "SEMANTIC_DAILY_BUDGET_USD": Decimal("30"),
    }
)

BASE = "/api/v1/semantic"


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _response(content: str) -> ModelResponse:
    return ModelResponse(
        content=content, actual_model="anthropic/claude-test", provider="P1",
        prompt_tokens=8274, completion_tokens=156, cache_write_tokens=8058, cached_tokens=0,
        cost_usd=Decimal("0.01"),
    )


class _ScriptedClient:
    """Клиент модели с готовым ответом; число вызовов — для проверки, что модель
    звали или не звали."""

    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    def complete(self, body, *, timeout_s):
        self.calls += 1
        return _response(self.content)


class _NeverCalledClient:
    def __init__(self):
        self.calls = 0

    def complete(self, body, *, timeout_s):
        self.calls += 1
        raise AssertionError("модель не должна вызываться")


def _schema_answer_json(*names: str) -> str:
    """Ответ модели схемы: параметры с номерами 1..N и двумя значениями."""
    return json.dumps(
        {
            "parameters": [
                {"ordinal": i, "name": name, "values": [f"{name} один", f"{name} два"]}
                for i, name in enumerate(names, 1)
            ]
        },
        ensure_ascii=False,
    )


def _params(count: int, *, prefix: str = "Параметр"):
    return tuple(
        (i, f"{prefix} {i}", [f"значение {i}а", f"значение {i}б"]) for i in range(1, count + 1)
    )


def _humanize(db, world, *ordinals: int):
    """Параметры версии `ordinals` — параметры человека."""
    db.execute(
        sa.update(FamilyParameter)
        .where(
            FamilyParameter.id.in_([world.parameters[o] for o in ordinals]),
        )
        .values(origin="human")
    )
    db.flush()
    db.expire_all()


def _family_scene(db, factories, *, human: int, model: int = 0):
    """Активная семья с текущей версией из `human` параметров человека и `model`
    параметров модели (человека — первые по номеру) и контекстом."""
    world = _active_world(db, factories, _params(human + model))
    if human:
        _humanize(db, world, *range(1, human + 1))
    return world


def _frozen_of(db, family_id) -> FamilyParameterSchema | None:
    db.expire_all()
    return db.execute(
        sa.select(FamilyParameterSchema).where(
            FamilyParameterSchema.family_id == family_id,
            FamilyParameterSchema.status == "frozen",
        )
    ).scalar_one_or_none()


def _building_of(db, family_id) -> FamilyParameterSchema | None:
    db.expire_all()
    return db.execute(
        sa.select(FamilyParameterSchema).where(
            FamilyParameterSchema.family_id == family_id,
            FamilyParameterSchema.status == "building",
        )
    ).scalar_one_or_none()


def _parameters_of(db, schema_id):
    """`[(номер, имя, происхождение)]` параметров версии по возрастанию номера."""
    db.expire_all()
    return [
        tuple(row)
        for row in db.execute(
            sa.select(FamilyParameter.ordinal, FamilyParameter.name, FamilyParameter.origin)
            .where(FamilyParameter.schema_id == schema_id)
            .order_by(FamilyParameter.ordinal)
        ).all()
    ]


def _rows(db, context_id, schema_id) -> dict:
    """`{номер параметра: (текст значения, источник, off_list_text)}` строк
    значений контекста по версии."""
    db.expire_all()
    return {
        row.ordinal: (row.value, row.source, row.off_list_text)
        for row in db.execute(
            sa.select(
                FamilyParameter.ordinal, FamilyParameterValue.value,
                ContextParameterValue.source, ContextParameterValue.off_list_text,
            )
            .select_from(ContextParameterValue)
            .join(FamilyParameter, FamilyParameter.id == ContextParameterValue.parameter_id)
            .outerjoin(
                FamilyParameterValue, FamilyParameterValue.id == ContextParameterValue.value_id
            )
            .where(
                ContextParameterValue.context_id == context_id,
                ContextParameterValue.schema_id == schema_id,
            )
        ).all()
    }


def _jobs(db, kind, **where):
    db.expire_all()
    stmt = sa.select(SemanticJob).where(SemanticJob.kind == kind)
    for column, value in where.items():
        stmt = stmt.where(getattr(SemanticJob, column) == value)
    return list(db.execute(stmt.order_by(SemanticJob.id)).scalars().all())


def _attempts(db, job_id):
    db.expire_all()
    return list(
        db.execute(
            sa.select(SemanticJobAttempt)
            .where(SemanticJobAttempt.job_id == job_id)
            .order_by(SemanticJobAttempt.id)
        ).scalars()
    )


def _frozen_events(db) -> int:
    db.expire_all()
    return db.execute(
        sa.select(sa.func.count())
        .select_from(SemanticEvent)
        .where(SemanticEvent.event_type == "family_schema_frozen")
    ).scalar_one()


def _material(human: int) -> SchemaRequestMaterial:
    return SchemaRequestMaterial(
        family_id=7, schema_id=0, title="Устройство пола", unit_code="M2",
        definition="Пол по грунту",
        names=("Стяжка пола 50 мм", "Стяжка пола 80 мм"),
        human_parameters=tuple(
            (f"Заданный {i}", (f"значение {i}а", f"значение {i}б")) for i in range(1, human + 1)
        ),
    )


def _system_text(material: SchemaRequestMaterial) -> str:
    rendered = render_schema_request(material, settings=_settings())
    (block,) = rendered.body["messages"][0]["content"]
    return block["text"]


def _rebuild(db, family_id):
    return rebuild_schema(db, family_id=family_id, actor_id=1)


def _claim_job(db, kind: SemanticJobKind):
    """Захват задания вида `kind`: остальные ждущие задания (предложения и
    значения, поставленные привязкой) отменены, чтобы опросчик взял именно его."""
    db.execute(
        sa.update(SemanticJob)
        .where(SemanticJob.status == "pending", SemanticJob.kind != kind.value)
        .values(status="cancelled", cancel_reason="not_applicable")
    )
    db.flush()
    claim = worker.claim_next(db, settings=S, now=NOW)
    assert claim is not None and claim.kind == kind
    return claim


def _claim_schema_job(db):
    return _claim_job(db, SemanticJobKind.family_schema)


# ---------------------------------------------------------------------------
#  Тело запроса схемы
# ---------------------------------------------------------------------------

class TestSchemaRequestBody:
    def test_without_human_parameters_the_prompt_is_the_old_one(self):
        material = _material(0)

        assert schema_prompt_for(material) == SCHEMA_PROMPT
        assert _system_text(material) == SCHEMA_PROMPT
        assert "от 0 до 3 ценовых" in SCHEMA_PROMPT
        assert '"ordinal" — 1, 2, 3 по порядку' in SCHEMA_PROMPT
        assert "заданы человеком" not in SCHEMA_PROMPT

    @pytest.mark.parametrize(
        ("human", "limit_text", "ordinals_text"),
        [
            (1, "от 0 до 3 ценовых", '"ordinal" — 1, 2, 3 по порядку'),
            (3, "от 0 до 3 ценовых", '"ordinal" — 1, 2, 3 по порядку'),
            (8, "от 0 до 2 ценовых", '"ordinal" — 1, 2 по порядку'),
            (9, "от 0 до 1 ценовых", '"ordinal" — 1 по порядку'),
        ],
    )
    def test_with_human_parameters_the_limit_and_numbers_follow_the_free_room(
        self, human, limit_text, ordinals_text
    ):
        text = _system_text(_material(human))

        assert limit_text in text
        assert ordinals_text in text
        assert text.count("Уже заданы человеком") == 1
        for i in range(1, human + 1):
            assert f"Заданный {i}: значение {i}а, значение {i}б" in text
        if human > 3:
            assert "от 0 до 3 ценовых" not in text
            assert "1, 2, 3 по порядку" not in text

    def test_the_human_block_stands_before_the_task_and_the_rest_is_unchanged(self):
        text = _system_text(_material(8))

        assert text.index("Уже заданы человеком") < text.index("Задача:")
        # Всё, кроме трёх изменённых мест, — прежний текст.
        restored = (
            text.replace("от 0 до 2 ценовых", "от 0 до 3 ценовых")
            .replace('"ordinal" — 1, 2 по порядку', '"ordinal" — 1, 2, 3 по порядку')
        )
        head, _block, tail = restored.partition("\n\nУже заданы человеком")
        rest = tail.split("\n\nЗадача:\n", 1)[1]
        assert head + "\n\nЗадача:\n" + rest == SCHEMA_PROMPT

    @pytest.mark.parametrize(("human", "limit"), [(0, 3), (3, 3), (8, 2), (10, 0)])
    def test_model_limit_of_the_material(self, human, limit):
        assert _material(human).model_limit == limit

    def test_the_hash_depends_on_human_parameters_name_and_values_each(self):
        base = _material(1)
        hashes = {
            "none": render_schema_request(_material(0), settings=_settings()).request_hash,
            "base": render_schema_request(base, settings=_settings()).request_hash,
        }
        renamed = SchemaRequestMaterial(
            **{**base.__dict__, "human_parameters": (("Другое имя", base.human_parameters[0][1]),)}
        )
        revalued = SchemaRequestMaterial(
            **{**base.__dict__, "human_parameters": ((base.human_parameters[0][0], ("иное",)),)}
        )
        hashes["renamed"] = render_schema_request(renamed, settings=_settings()).request_hash
        hashes["revalued"] = render_schema_request(revalued, settings=_settings()).request_hash

        assert len(set(hashes.values())) == 4


# ---------------------------------------------------------------------------
#  Материал: параметры человека действующей версии
# ---------------------------------------------------------------------------

class TestSchemaMaterial:
    def test_only_active_human_parameters_of_the_current_version_with_active_values(
        self, db_session, factories
    ):
        family = _family(db_session, status="active", definition="Стяжка")
        old = _schema(db_session, factories, family, version=1, status="superseded")
        _value(db_session, _param(db_session, old, 1, "Прежний человек"), "x")
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.schema_id == old.id)
            .values(origin="human")
        )
        current = _schema(db_session, factories, family, version=2)
        model = _param(db_session, current, 1, "Модельный")
        _value(db_session, model, "м")
        human = _param(db_session, current, 2, "Человеческий")
        first = _value(db_session, human, "а")
        _value(db_session, human, "б")
        retired = _value(db_session, human, "выведено")
        merged = _value(db_session, human, "слито")
        no_values = _param(db_session, current, 4, "Без значений")
        archived = _param(db_session, current, 3, "Архивный")
        _value(db_session, archived, "в")
        db_session.execute(
            sa.update(FamilyParameter)
            .where(FamilyParameter.id.in_([human.id, no_values.id, archived.id]))
            .values(origin="human")
        )
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == archived.id)
            .values(archived_at=sa.func.now())
        )
        db_session.execute(
            sa.update(FamilyParameterValue).where(FamilyParameterValue.id == retired.id)
            .values(retired_at=sa.func.now())
        )
        db_session.execute(
            sa.update(FamilyParameterValue).where(FamilyParameterValue.id == merged.id)
            .values(merged_into_id=first.id)
        )
        db_session.flush()

        material = load_schema_material(db_session, family.id, 0)

        assert material.human_parameters == (("Человеческий", ("а", "б")), ("Без значений", ()))

    def test_a_family_without_a_current_version_or_human_parameters_has_none(
        self, db_session, factories
    ):
        bare = _family(db_session, status="active", definition="Пустая")
        model_only = _family(db_session, status="active", definition="Модель")
        schema = _schema(db_session, factories, model_only)
        _value(db_session, _param(db_session, schema, 1, "Модельный"), "м")

        assert load_schema_material(db_session, bare.id, 0).human_parameters == ()
        assert load_schema_material(db_session, model_only.id, 0).human_parameters == ()


# ---------------------------------------------------------------------------
#  Предел ответа и путь опросчика
# ---------------------------------------------------------------------------

class TestAnswerLimitThroughTheWorker:
    def test_eight_human_parameters_and_two_model_ones_freeze_a_version_of_ten(
        self, committing_session_factory, committing_db, committing_factories
    ):
        world = _family_scene(committing_db, committing_factories, human=8)
        _rebuild(committing_db, world.family.id)
        committing_db.commit()
        client = _ScriptedClient(_schema_answer_json("Новый А", "Новый Б"))

        assert process_one(
            committing_session_factory, client, settings=S, clock=lambda: NOW
        ) is True

        assert client.calls == 1
        frozen = _frozen_of(committing_db, world.family.id)
        assert frozen.version == 2
        assert _parameters_of(committing_db, frozen.id) == [
            *((i, f"Параметр {i}", "human") for i in range(1, 9)),
            (9, "Новый А", "model"),
            (10, "Новый Б", "model"),
        ]

    def test_eight_human_parameters_and_three_model_ones_are_a_schema_error(
        self, committing_session_factory, committing_db, committing_factories
    ):
        world = _family_scene(committing_db, committing_factories, human=8)
        building = _rebuild(committing_db, world.family.id)
        committing_db.commit()
        [job] = _jobs(committing_db, "family_schema")
        client = _ScriptedClient(_schema_answer_json("А", "Б", "В"))

        process_one(committing_session_factory, client, settings=S, clock=lambda: NOW)

        assert client.calls == 1
        job = _jobs(committing_db, "family_schema")[0]
        assert (job.status, job.last_error_class) == ("error", "schema_error")
        [attempt] = _attempts(committing_db, job.id)
        assert attempt.outcome == "schema_error"
        assert _building_of(committing_db, world.family.id).id == building.id
        assert _frozen_of(committing_db, world.family.id).version == 1
        assert _frozen_events(committing_db) == 0

    @pytest.mark.parametrize(
        ("names", "frozen"),
        [(("А", "Б", "В"), True), (("А", "Б", "В", "Г"), False)],
    )
    def test_without_human_parameters_the_limit_stays_three(
        self, committing_session_factory, committing_db, committing_factories, names, frozen
    ):
        world = _family_scene(committing_db, committing_factories, human=0, model=2)
        _rebuild(committing_db, world.family.id)
        committing_db.commit()
        client = _ScriptedClient(_schema_answer_json(*names))

        process_one(committing_session_factory, client, settings=S, clock=lambda: NOW)

        current = _frozen_of(committing_db, world.family.id)
        if frozen:
            assert current.version == 2
            assert [p[:2] for p in _parameters_of(committing_db, current.id)] == [
                (1, "А"), (2, "Б"), (3, "В"),
            ]
        else:
            assert current.version == 1
            [job] = _jobs(committing_db, "family_schema")
            assert (job.status, job.last_error_class) == ("error", "schema_error")

    def test_ten_human_parameters_freeze_the_version_without_calling_the_model(
        self, committing_session_factory, committing_db, committing_factories
    ):
        world = _family_scene(committing_db, committing_factories, human=10)
        _rebuild(committing_db, world.family.id)
        committing_db.commit()
        client = _NeverCalledClient()

        assert process_one(
            committing_session_factory, client, settings=S, clock=lambda: NOW
        ) is True

        assert client.calls == 0
        [job] = _jobs(committing_db, "family_schema")
        assert (job.status, job.claim_token) == ("done", None)
        assert _attempts(committing_db, job.id) == []
        frozen = _frozen_of(committing_db, world.family.id)
        assert frozen.version == 2
        assert _parameters_of(committing_db, frozen.id) == [
            (i, f"Параметр {i}", "human") for i in range(1, 11)
        ]
        assert _frozen_events(committing_db) == 1

    def test_a_lost_synthetic_claim_does_not_freeze_the_version(self, db_session, factories):
        # Синтетический захват задания схемы потерян (задание взято заново
        # другим токеном): заморозка сверяет токен ЗАХВАТА, а не нынешний токен
        # задания, и версию не трогает.
        world = _family_scene(db_session, factories, human=10)
        building = _rebuild(db_session, world.family.id)
        claim = _claim_schema_job(db_session)
        assert claim.synthesized is True
        other = uuid.uuid4()
        db_session.execute(
            sa.update(SemanticJob).where(SemanticJob.id == claim.job_id).values(claim_token=other)
        )
        db_session.flush()

        worker.complete_without_model(db_session, claim, now=NOW, settings=S)

        assert _building_of(db_session, world.family.id).id == building.id
        assert _frozen_of(db_session, world.family.id).version == 1
        [job] = _jobs(db_session, "family_schema")
        assert (job.status, job.claim_token) == ("running", other)

    def test_an_unclaimed_schema_job_is_not_completed_without_the_model(
        self, db_session, factories
    ):
        world = _family_scene(db_session, factories, human=10)
        building = _rebuild(db_session, world.family.id)
        [job] = _jobs(db_session, "family_schema")
        assert (job.status, job.claim_token) == ("pending", None)

        with pytest.raises(ValueError):
            worker.complete_schema_without_model(db_session, job, settings=S)

        assert _building_of(db_session, world.family.id).id == building.id
        assert _frozen_of(db_session, world.family.id).version == 1

    def test_nine_human_parameters_still_ask_the_model(
        self, committing_session_factory, committing_db, committing_factories
    ):
        world = _family_scene(committing_db, committing_factories, human=9)
        _rebuild(committing_db, world.family.id)
        committing_db.commit()
        client = _ScriptedClient(_schema_answer_json("Десятый"))

        process_one(committing_session_factory, client, settings=S, clock=lambda: NOW)

        assert client.calls == 1
        frozen = _frozen_of(committing_db, world.family.id)
        assert _parameters_of(committing_db, frozen.id)[-1] == (10, "Десятый", "model")

    @pytest.mark.parametrize(
        ("proposed", "frozen"),
        [
            ("  ПАРАМЕТР   1 ", False),  # то же имя по `normalize_value`
            ("Параметр 2", False),  # имя второго параметра человека
            ("Параметр 3", True),  # соседний вход: другое имя проходит
        ],
    )
    def test_a_model_parameter_named_like_a_human_one_is_a_schema_error(
        self, committing_session_factory, committing_db, committing_factories, proposed, frozen
    ):
        world = _family_scene(committing_db, committing_factories, human=2)
        building = _rebuild(committing_db, world.family.id)
        committing_db.commit()
        client = _ScriptedClient(_schema_answer_json(proposed))

        process_one(committing_session_factory, client, settings=S, clock=lambda: NOW)

        assert client.calls == 1
        current = _frozen_of(committing_db, world.family.id)
        [job] = _jobs(committing_db, "family_schema")
        if frozen:
            assert current.version == 2 and job.status == "done"
            assert _parameters_of(committing_db, current.id)[-1] == (3, proposed, "model")
        else:
            assert current.version == 1
            assert (job.status, job.last_error_class) == ("error", "schema_error")
            [attempt] = _attempts(committing_db, job.id)
            assert attempt.outcome == "schema_error"
            assert _building_of(committing_db, world.family.id).id == building.id
            assert _frozen_events(committing_db) == 0

    def test_the_name_of_a_model_parameter_of_the_old_version_may_be_proposed_again(
        self, committing_session_factory, committing_db, committing_factories
    ):
        # Параметр модели прежней версии не переносится, и его имя модель вправе
        # предложить снова: запрет только на имена активных параметров человека.
        world = _family_scene(committing_db, committing_factories, human=1, model=1)
        assert world.parameters
        _rebuild(committing_db, world.family.id)
        committing_db.commit()
        client = _ScriptedClient(_schema_answer_json("Параметр 2"))

        process_one(committing_session_factory, client, settings=S, clock=lambda: NOW)

        current = _frozen_of(committing_db, world.family.id)
        assert current.version == 2
        assert _parameters_of(committing_db, current.id) == [
            (1, "Параметр 1", "human"), (2, "Параметр 2", "model"),
        ]

    def test_the_name_of_an_archived_human_parameter_may_be_proposed_again(
        self, db_session, factories
    ):
        # Архивный параметр человека не переносится, и запрет имени на него не
        # распространяется.
        world = _family_scene(db_session, factories, human=2)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == world.parameters[2])
            .values(archived_at=sa.func.now())
        )
        db_session.flush()
        _rebuild(db_session, world.family.id)
        claim = _claim_schema_job(db_session)

        worker.record_result(
            db_session, claim, _response(_schema_answer_json("Параметр 2")), now=NOW, settings=S
        )

        current = _frozen_of(db_session, world.family.id)
        assert current.version == 2
        assert _parameters_of(db_session, current.id) == [
            (1, "Параметр 1", "human"), (2, "Параметр 2", "model"),
        ]

    def test_a_human_parameter_of_another_family_does_not_forbid_the_name(
        self, db_session, factories
    ):
        # Запрет — на имена параметров человека действующей версии ЭТОЙ семьи.
        world = _family_scene(db_session, factories, human=1)
        other = _family_scene(db_session, factories, human=2)
        assert other.family.id != world.family.id
        _rebuild(db_session, world.family.id)
        claim = _claim_schema_job(db_session)

        worker.record_result(
            db_session, claim, _response(_schema_answer_json("Параметр 2")), now=NOW, settings=S
        )

        current = _frozen_of(db_session, world.family.id)
        assert current.version == 2
        assert _parameters_of(db_session, current.id)[-1] == (2, "Параметр 2", "model")

    @pytest.mark.parametrize(("human", "free"), [(10, True), (9, False)])
    def test_the_schema_job_is_free_only_when_the_model_has_no_room(
        self, db_session, factories, human, free
    ):
        world = _family_scene(db_session, factories, human=human)
        _schema(db_session, factories, world.family, version=2, status="building")

        fingerprints, reserve, cached = estimate_enqueue(db_session, [], [world.family.id])

        assert [f.kind for f in fingerprints] == [SemanticJobKind.family_schema]
        assert (reserve == 0 and cached == 0) is free
        assert (reserve > 0 and cached > 0) is not free

    def test_the_job_body_reproduces_from_the_same_material_the_answer_is_checked_by(
        self, db_session, factories
    ):
        world = _family_scene(db_session, factories, human=8)
        _rebuild(db_session, world.family.id)
        [job] = _jobs(db_session, "family_schema")

        render = worker.render_job_request(db_session, job, settings=S)

        material = load_schema_material(db_session, job.family_id, job.schema_id)
        assert render.rendered.request_hash == job.request_hash
        assert render.rendered.request_hash == render_schema_request(
            material, settings=S
        ).request_hash
        assert render.without_model is False
        assert material.model_limit == 2


# ---------------------------------------------------------------------------
#  Запись версии
# ---------------------------------------------------------------------------

def _freeze_empty(db, family_id) -> FamilyParameterSchema:
    """Версия `building` замораживается пустым ответом модели без задания."""
    building = _building_of(db, family_id)
    outcome = freeze_schema(
        db, schema_id=building.id, answer=SchemaAnswer(parameters=()), guard=None,
        settings=S,
    )
    assert outcome.applied
    return _frozen_of(db, family_id)


class TestFrozenVersionCarriesHumanParameters:
    def test_numbers_are_compacted_in_the_order_of_the_old_ones_and_model_ones_follow(
        self, db_session, factories
    ):
        # Прежние номера: модель 1, человек 2 и 4, архивный человек 3, модель 5.
        family = _family(db_session, status="active", definition="Стяжка")
        current = _schema(db_session, factories, family, version=1)
        for ordinal, name in ((1, "Модель 1"), (2, "Человек Б"), (3, "Архивный"),
                              (4, "Человек Г"), (5, "Модель 5")):
            _value(db_session, _param(db_session, current, ordinal, name), f"{name} знач")
        human_ids = db_session.execute(
            sa.select(FamilyParameter.id).where(
                FamilyParameter.schema_id == current.id,
                FamilyParameter.ordinal.in_([2, 3, 4]),
            )
        ).scalars().all()
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id.in_(human_ids))
            .values(origin="human")
        )
        db_session.execute(
            sa.update(FamilyParameter)
            .where(FamilyParameter.schema_id == current.id, FamilyParameter.ordinal == 3)
            .values(archived_at=sa.func.now())
        )
        db_session.flush()
        context_id, _ = _context_of(db_session, factories, family)
        _rebuild(db_session, family.id)
        [job] = _jobs(db_session, "family_schema")
        claim = _claim_schema_job(db_session)
        assert claim.job_id == job.id

        worker.record_result(
            db_session, claim, _response(_schema_answer_json("Новый А", "Новый Б")),
            now=NOW, settings=S,
        )

        new = _frozen_of(db_session, family.id)
        assert _parameters_of(db_session, new.id) == [
            (1, "Человек Б", "human"), (2, "Человек Г", "human"),
            (3, "Новый А", "model"), (4, "Новый Б", "model"),
        ]
        assert context_id is not None

    def test_role_kind_bounds_closed_origin_and_active_values_are_carried(
        self, db_session, factories
    ):
        family = _family(db_session, status="active", definition="Стяжка")
        current = _schema(db_session, factories, family, version=1)
        listed = _param(db_session, current, 1, "Класс")
        a = _value(db_session, listed, "В15", origin="manual")
        _value(db_session, listed, "В25", origin="extension")
        _value(db_session, listed, "В30")
        retired = _value(db_session, listed, "В40")
        merged = _value(db_session, listed, "В45")
        ranged = _param(db_session, current, 2, "Толщина")
        _value(db_session, ranged, "до 50", range_hi=Decimal("50"))
        _value(db_session, ranged, "50–150", range_lo=Decimal("50"), range_hi=Decimal("150"))
        _value(db_session, ranged, "150 и более", range_lo=Decimal("150"))
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == listed.id)
            .values(origin="human", role="secondary", closed=True)
        )
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == ranged.id)
            .values(
                # Три границы различны: перепутанные местами поля не совпали бы.
                origin="human", value_kind="range", closed=True, range_from=Decimal("50"),
                range_to=Decimal("150"), range_step=Decimal("100"),
            )
        )
        db_session.execute(
            sa.update(FamilyParameterValue).where(FamilyParameterValue.id == retired.id)
            .values(retired_at=sa.func.now())
        )
        db_session.execute(
            sa.update(FamilyParameterValue).where(FamilyParameterValue.id == merged.id)
            .values(merged_into_id=a.id)
        )
        db_session.flush()
        _context_of(db_session, factories, family)
        _rebuild(db_session, family.id)

        new = _freeze_empty(db_session, family.id)

        rows = db_session.execute(
            sa.select(
                FamilyParameter.ordinal, FamilyParameter.name, FamilyParameter.role,
                FamilyParameter.value_kind, FamilyParameter.closed, FamilyParameter.origin,
                FamilyParameter.range_from, FamilyParameter.range_to, FamilyParameter.range_step,
                FamilyParameter.archived_at,
            )
            .where(FamilyParameter.schema_id == new.id)
            .order_by(FamilyParameter.ordinal)
        ).all()
        assert [tuple(r) for r in rows] == [
            (1, "Класс", "secondary", "exact", True, "human", None, None, None, None),
            (2, "Толщина", "main", "range", True, "human",
             Decimal("50"), Decimal("150"), Decimal("100"), None),
        ]
        values = db_session.execute(
            sa.select(
                FamilyParameter.ordinal, FamilyParameterValue.value, FamilyParameterValue.origin,
                FamilyParameterValue.range_lo, FamilyParameterValue.range_hi,
                FamilyParameterValue.merged_into_id, FamilyParameterValue.retired_at,
            )
            .join(FamilyParameterValue, FamilyParameterValue.parameter_id == FamilyParameter.id)
            .where(FamilyParameter.schema_id == new.id)
            .order_by(FamilyParameterValue.id)
        ).all()
        assert [tuple(v) for v in values] == [
            (1, "В15", "manual", None, None, None, None),
            (1, "В25", "extension", None, None, None, None),
            (1, "В30", "schema", None, None, None, None),
            (2, "до 50", "schema", None, Decimal("50"), None, None),
            (2, "50–150", "schema", Decimal("50"), Decimal("150"), None, None),
            (2, "150 и более", "schema", Decimal("150"), None, None, None),
        ]

    def test_context_rows_of_human_parameters_are_copied_and_a_refreshing_one_is_not(
        self, db_session, factories
    ):
        world = _active_world(
            db_session, factories,
            ((1, "Материал", ["бетон", "кирпич"]), (2, "Толщина", ["50 мм", "100 мм"]),
             (3, "Класс", ["В15", "В25"])),
        )
        _humanize(db_session, world, 2, 3)
        first = world.context_id
        second = _second_context(db_session, factories, world)
        _settle_context(
            db_session, world, first, _answer(_named(1, "бетон"), _named(2, "50 мм"),
                                              _named(3, "В15")),
        )
        _settle_context(
            db_session, world, second,
            _answer(_named(1, "кирпич"), (2, "none", None, None), (3, "conflict", None, None)),
        )
        # «Уточняется» у класса первого контекста; «не из списка» у толщины второго.
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == first,
                ContextParameterValue.parameter_id == world.parameters[3],
            )
            .values(refresh_requested_at=sa.func.now())
        )
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == second,
                ContextParameterValue.parameter_id == world.parameters[2],
            )
            .values(source="off_list", value_id=None, off_list_text="70 мм")
        )
        db_session.flush()
        old_rows = (_rows(db_session, first, world.schema.id), _rows(db_session, second, world.schema.id))
        _rebuild(db_session, world.family.id)
        claim = _claim_schema_job(db_session)

        worker.record_result(
            db_session, claim, _response(_schema_answer_json("Новый А", "Новый Б")),
            now=NOW, settings=S,
        )

        new = _frozen_of(db_session, world.family.id)
        assert _parameters_of(db_session, new.id) == [
            (1, "Толщина", "human"), (2, "Класс", "human"),
            (3, "Новый А", "model"), (4, "Новый Б", "model"),
        ]
        assert _rows(db_session, first, new.id) == {1: ("50 мм", "name", None)}
        assert _rows(db_session, second, new.id) == {
            1: (None, "off_list", "70 мм"), 2: (None, "path_conflict", None),
        }
        # История прежней версии не тронута.
        assert (
            _rows(db_session, first, world.schema.id), _rows(db_session, second, world.schema.id)
        ) == old_rows
        # Спросят параметры модели и прерванное «уточняется», остальное известно.
        assert values_need(db_session, first, new.id).ordinals == (2, 3, 4)
        assert values_need(db_session, second, new.id).ordinals == (3, 4)
        assert values_need(db_session, first, new.id).variant_due is True
        # Задания значений, поставленные заморозкой, спрашивают ровно их.
        [job_first] = _jobs(db_session, "context_values", context_id=first, schema_id=new.id)
        [job_second] = _jobs(db_session, "context_values", context_id=second, schema_id=new.id)
        assert list(job_first.requested_ordinals) == [2, 3, 4]
        assert list(job_second.requested_ordinals) == [3, 4]

    def test_an_empty_row_marked_by_an_extension_wave_is_not_copied(self, db_session, factories):
        # «Уточняется» без значения (волна после расширения помечает строки
        # «не уточнено»): значение не выведено, отсеивает её только отметка.
        world = _active_world(
            db_session, factories,
            ((1, "Материал", ["бетон", "кирпич"]), (2, "Толщина", ["50 мм", "100 мм"])),
        )
        _humanize(db_session, world, 2)
        context_id = world.context_id
        _settle_context(
            db_session, world, context_id, _answer(_named(1, "бетон"), (2, "none", None, None))
        )
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == context_id,
                ContextParameterValue.parameter_id == world.parameters[2],
            )
            .values(refresh_requested_at=sa.func.now())
        )
        db_session.flush()
        _rebuild(db_session, world.family.id)

        new = _freeze_empty(db_session, world.family.id)

        assert _parameters_of(db_session, new.id) == [(1, "Толщина", "human")]
        assert _rows(db_session, context_id, new.id) == {}
        assert values_need(db_session, context_id, new.id).ordinals == (1,)

    def _merge_scene(self, db, factories):
        """Материал — параметр модели: «газоблок» ещё отдельное значение, контекст
        назвал его."""
        world = _active_world(
            db, factories, ((1, "Материал", ["газобетон", "газоблок", "кирпич"]),)
        )
        _settle_context(db, world, world.context_id, _answer(_named(1, "газоблок")))
        return world

    def _origin(self, db, parameter_id):
        db.expire_all()
        return db.get(FamilyParameter, parameter_id).origin

    def test_merging_values_of_a_model_parameter_makes_it_human_and_the_rebuild_keeps_it(
        self, db_session, factories
    ):
        """Правка значений человеком делает параметр человеческим (спека 3в §2.5), и
        пересборка переносит его вместе со значениями строк контекстов (§2.8)."""
        world = self._merge_scene(db_session, factories)
        assert self._origin(db_session, world.parameters[1]) == "model"

        merge_parameter_values(
            db_session, parameter_id=world.parameters[1],
            source_value_id=world.values[(1, "газоблок")],
            target_value_id=world.values[(1, "газобетон")], actor_id=_actor(factories),
        )

        assert self._origin(db_session, world.parameters[1]) == "human"
        _rebuild(db_session, world.family.id)
        new = _freeze_empty(db_session, world.family.id)
        assert _parameters_of(db_session, new.id) == [(1, "Материал", "human")]
        assert _rows(db_session, world.context_id, new.id) == {1: ("газобетон", "name", None)}
        carried = db_session.execute(
            sa.select(FamilyParameterValue.value)
            .join(FamilyParameter, FamilyParameter.id == FamilyParameterValue.parameter_id)
            .where(
                FamilyParameter.schema_id == new.id,
                FamilyParameterValue.merged_into_id.is_(None),
            )
            .order_by(FamilyParameterValue.id)
        ).scalars().all()
        assert carried == ["газобетон", "кирпич"]

    def test_merging_values_of_a_human_parameter_changes_nothing_else_on_it(
        self, db_session, factories
    ):
        world = self._merge_scene(db_session, factories)
        _humanize(db_session, world, 1)
        before = tuple(db_session.execute(
            sa.select(
                FamilyParameter.name, FamilyParameter.role, FamilyParameter.value_kind,
                FamilyParameter.closed, FamilyParameter.origin, FamilyParameter.archived_at,
            ).where(FamilyParameter.id == world.parameters[1])
        ).one())

        merge_parameter_values(
            db_session, parameter_id=world.parameters[1],
            source_value_id=world.values[(1, "газоблок")],
            target_value_id=world.values[(1, "газобетон")], actor_id=_actor(factories),
        )

        after = tuple(db_session.execute(
            sa.select(
                FamilyParameter.name, FamilyParameter.role, FamilyParameter.value_kind,
                FamilyParameter.closed, FamilyParameter.origin, FamilyParameter.archived_at,
            ).where(FamilyParameter.id == world.parameters[1])
        ).one())
        assert after == before
        assert after[4] == "human"

    def test_a_refused_merge_leaves_a_model_parameter_a_model_one(self, db_session, factories):
        world = self._merge_scene(db_session, factories)
        merge_parameter_values(
            db_session, parameter_id=world.parameters[1],
            source_value_id=world.values[(1, "кирпич")],
            target_value_id=world.values[(1, "газобетон")], actor_id=_actor(factories),
        )
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == world.parameters[1])
            .values(origin="model")
        )
        db_session.flush()

        with pytest.raises(WorkFamilyError) as raised:
            merge_parameter_values(
                db_session, parameter_id=world.parameters[1],
                source_value_id=world.values[(1, "кирпич")],
                target_value_id=world.values[(1, "газоблок")], actor_id=_actor(factories),
            )

        assert raised.value.code == "merge_source_merged"
        assert self._origin(db_session, world.parameters[1]) == "model"

    def test_when_nothing_is_missing_the_values_job_completes_without_the_model(
        self, db_session, factories
    ):
        world = _active_world(
            db_session, factories,
            ((1, "Толщина", ["50 мм", "100 мм"]), (2, "Класс", ["В15", "В25"])),
        )
        _humanize(db_session, world, 1, 2)
        context_id = world.context_id
        _settle_context(
            db_session, world, context_id, _answer(_named(1, "50 мм"), _named(2, "В15"))
        )
        _rebuild(db_session, world.family.id)
        claim = _claim_schema_job(db_session)

        worker.record_result(
            db_session, claim, _response('{"parameters": []}'), now=NOW, settings=S
        )

        new = _frozen_of(db_session, world.family.id)
        assert values_need(db_session, context_id, new.id).ordinals == ()
        [job] = _jobs(db_session, "context_values", context_id=context_id, schema_id=new.id)
        assert job.requested_ordinals is None
        render = worker.render_job_request(db_session, job, settings=S)
        assert render.without_model is True
        synthetic = _claim_job(db_session, SemanticJobKind.context_values)
        assert synthetic.synthesized is True
        worker.complete_without_model(db_session, synthetic, now=NOW, settings=S)
        db_session.expire_all()
        variant = db_session.get(WorkVariant, db_session.get(CatalogContext, context_id).work_variant_id)
        assert variant.schema_id == new.id


class TestCardAcrossARebuild:
    """Карточка контекста показывает значения одной версии схемы (спека 3в §2.8,
    §2.10): между заморозкой новой версии и первым ответом по ней вариант контекста
    ещё из прежней, и копии строк человека в новой версии карточку не дублируют."""

    def _scene(self, db, factories):
        world = _active_world(
            db, factories,
            ((1, "Толщина", ["50 мм", "100 мм"]), (2, "Материал", ["бетон", "кирпич"])),
        )
        _humanize(db, world, 1)
        _settle_context(db, world, world.context_id, _answer(_named(1, "50 мм"), _named(2, "бетон")))
        return world

    def _card(self, db, context_id):
        db.expire_all()
        card = context_variant_out(db, db.get(CatalogContext, context_id))
        return [(v["ordinal"], v["parameter_id"]) for v in card["values"]]

    def _parameter_ids(self, db, schema_id):
        return {
            row.ordinal: row.id
            for row in db.execute(
                sa.select(FamilyParameter.id, FamilyParameter.ordinal).where(
                    FamilyParameter.schema_id == schema_id
                )
            ).all()
        }

    def test_between_the_freeze_and_the_first_answer_the_card_shows_the_old_version_only(
        self, db_session, factories
    ):
        world = self._scene(db_session, factories)
        old_ids = self._parameter_ids(db_session, world.schema.id)
        _rebuild(db_session, world.family.id)
        new = _freeze_empty(db_session, world.family.id)
        new_ids = self._parameter_ids(db_session, new.id)
        # предусловие: копия строки человека в новой версии есть, ответа по ней нет
        assert _rows(db_session, world.context_id, new.id) == {1: ("50 мм", "name", None)}
        assert db_session.get(CatalogContext, world.context_id).work_variant_id is not None

        card = self._card(db_session, world.context_id)

        assert sorted(card) == [(1, old_ids[1]), (2, old_ids[2])]
        assert new_ids[1] not in {parameter_id for _ordinal, parameter_id in card}

    def test_after_the_answer_the_card_shows_the_new_version_only(self, db_session, factories):
        world = self._scene(db_session, factories)
        _rebuild(db_session, world.family.id)
        new = _freeze_empty(db_session, world.family.id)
        new_ids = self._parameter_ids(db_session, new.id)

        outcome = apply_values(
            db_session, context_id=world.context_id, schema_id=new.id, answer=_answer(),
            paths_hash=paths_hash_of(
                load_values_material(db_session, [world.context_id])[world.context_id].paths
            ),
            guard=None, settings=_settings(), requested_ordinals=(),
        )
        assert outcome.applied
        assert db_session.get(CatalogContext, world.context_id).work_variant_id is not None

        card = self._card(db_session, world.context_id)

        assert card == [(1, new_ids[1])]

    def test_a_variant_whose_version_lost_its_rows_shows_the_current_version(
        self, db_session, factories
    ):
        """Ответ по недостающему параметру удалил строки прежней версии, а главный
        параметр человека в новой версии снова «уточняется» — вариант остаётся
        прежним, карточка показывает строки действующей версии, а не пустой список."""
        world = self._scene(db_session, factories)
        old_variant = db_session.get(CatalogContext, world.context_id).work_variant_id
        _rebuild(db_session, world.family.id)
        building = _building_of(db_session, world.family.id)
        # строки прежней версии у другого контекста не делают её «версией с
        # строками» для этого (выборка коррелирована по контексту)
        other = _second_context(db_session, factories, world)
        _settle_context(db_session, world, other, _answer(_named(1, "100 мм"), _named(2, "кирпич")))
        outcome = freeze_schema(
            db_session, schema_id=building.id,
            answer=SchemaAnswer(
                parameters=(SchemaParameterIn(ordinal=1, name="Материал", values=("бетон",)),)
            ),
            guard=None, settings=S,
        )
        assert outcome.applied
        new = _frozen_of(db_session, world.family.id)
        new_ids = self._parameter_ids(db_session, new.id)
        db_session.execute(
            sa.update(ContextParameterValue)
            .where(
                ContextParameterValue.context_id == world.context_id,
                ContextParameterValue.parameter_id == new_ids[1],
            )
            .values(refresh_requested_at=sa.func.now())
        )
        db_session.flush()
        applied = apply_values(
            db_session, context_id=world.context_id, schema_id=new.id,
            answer=_answer(_named(2, "бетон")),
            paths_hash=paths_hash_of(
                load_values_material(db_session, [world.context_id])[world.context_id].paths
            ),
            guard=None, settings=_settings(), requested_ordinals=(2,),
        )
        assert applied.applied
        db_session.expire_all()
        assert db_session.get(CatalogContext, world.context_id).work_variant_id == old_variant
        assert _rows(db_session, world.context_id, world.schema.id) == {}
        assert _rows(db_session, other, world.schema.id) != {}

        card = self._card(db_session, world.context_id)

        assert sorted(card) == [(1, new_ids[1]), (2, new_ids[2])]

    def test_a_context_without_a_variant_shows_the_current_version_only(
        self, db_session, factories
    ):
        world = self._scene(db_session, factories)
        old_ids = self._parameter_ids(db_session, world.schema.id)
        db_session.execute(
            sa.update(CatalogContext).where(CatalogContext.id == world.context_id)
            .values(work_variant_id=None, variant_at=None, variant_paths_hash=None)
        )
        db_session.flush()
        before_rebuild = self._card(db_session, world.context_id)
        _rebuild(db_session, world.family.id)
        new = _freeze_empty(db_session, world.family.id)
        new_ids = self._parameter_ids(db_session, new.id)

        after_rebuild = self._card(db_session, world.context_id)

        assert sorted(before_rebuild) == [(1, old_ids[1]), (2, old_ids[2])]
        assert after_rebuild == [(1, new_ids[1])]


def _context_of(db, factories, family):
    """Контекст, привязанный к семье (наименование уходит в запрос схемы)."""
    context_id, _ = _chain_context(
        db, factories, title=f"Стяжка {_uid()}", path_specs=[((), 1)]
    )
    _bind(db, factories, context_id, family=family)
    return context_id, family


# ---------------------------------------------------------------------------
#  Оценка пересборки
# ---------------------------------------------------------------------------

def _preview_tariffs(monkeypatch):
    """Схема бесплатна, значения — с ненулевой ценой входа: разница оценки
    значений видна в сумме."""
    zero = Decimal("0")
    for suffix in ("INPUT", "CACHE_WRITE", "CACHE_READ", "OUTPUT"):
        monkeypatch.setattr(app_settings, f"SEMANTIC_SCHEMA_PRICE_{suffix}_PER_M", zero)
    monkeypatch.setattr(app_settings, "SEMANTIC_VALUES_PRICE_INPUT_PER_M", Decimal("5"))
    monkeypatch.setattr(app_settings, "SEMANTIC_VALUES_PRICE_CACHE_WRITE_PER_M", Decimal("6"))
    monkeypatch.setattr(app_settings, "SEMANTIC_VALUES_PRICE_CACHE_READ_PER_M", Decimal("0.5"))
    monkeypatch.setattr(app_settings, "SEMANTIC_VALUES_PRICE_OUTPUT_PER_M", Decimal("30"))


def _preview_family(db, factories, mark="А", *, human_second: bool, with_second: bool = True):
    """Семья с контекстом наименования той же длины, что у других (отличается
    одной буквой `mark`: наименования каталога уникальны); первый параметр —
    модели, второй — человека либо тоже модели (или его нет)."""
    params = [(1, "Толщина", ["50 мм", "100 мм"])]
    if with_second:
        params.append((2, "Материал", ["бетон", "кирпич"]))
    world = _world(db, factories, params=params, title=f"Стяжка {mark}1")
    if with_second and human_second:
        _humanize(db, world, 2)
    return world


class TestRebuildPreview:
    def test_values_are_estimated_by_the_current_model_parameters_only(
        self, db_session, factories, monkeypatch
    ):
        _preview_tariffs(monkeypatch)
        with_human = _preview_family(db_session, factories, "А", human_second=True)
        model_only = _preview_family(
            db_session, factories, "Б", human_second=False, with_second=False
        )
        both_model = _preview_family(db_session, factories, "В", human_second=False)

        reserve = {
            name: rebuild_preview(db_session, world.family.id).reserve_usd
            for name, world in (
                ("with_human", with_human), ("model_only", model_only), ("both_model", both_model),
            )
        }

        # Запрос по нынешним параметрам модели совпадает с запросом семьи, у
        # которой других параметров нет вовсе, и короче запроса по всем.
        assert reserve["with_human"] == reserve["model_only"] > 0
        assert reserve["both_model"] > reserve["with_human"]

    def test_a_current_version_without_model_parameters_adds_no_values_estimate(
        self, db_session, factories, monkeypatch
    ):
        _preview_tariffs(monkeypatch)
        world = _family_scene(db_session, factories, human=2)

        preview = rebuild_preview(db_session, world.family.id)

        assert preview.reserve_usd == 0
        assert preview.values_included is True

    def test_an_archived_model_parameter_adds_no_values_estimate(
        self, db_session, factories, monkeypatch
    ):
        # Параметр модели убран (архивный): спрашивать о нём нечего, как и
        # при версии без параметров модели вовсе.
        _preview_tariffs(monkeypatch)
        world = _family_scene(db_session, factories, human=2, model=1)
        db_session.execute(
            sa.update(FamilyParameter).where(FamilyParameter.id == world.parameters[3])
            .values(archived_at=sa.func.now())
        )
        db_session.flush()

        assert rebuild_preview(db_session, world.family.id).reserve_usd == 0

    def test_the_summary_names_the_approximation_only_when_values_are_included(
        self, db_session, factories
    ):
        with_schema = _preview_family(db_session, factories, human_second=True)
        bare = _family(db_session, status="active", definition="Без схемы")

        assert rebuild_preview(db_session, with_schema.family.id).values_approximate is True
        assert rebuild_preview(db_session, bare.id).values_approximate is False

    def test_the_hash_follows_the_revision_of_the_current_version(
        self, db_session, factories
    ):
        world = _preview_family(db_session, factories, human_second=True)
        first = rebuild_preview(db_session, world.family.id).preview_hash
        again = rebuild_preview(db_session, world.family.id).preview_hash

        bump_revision(db_session, world.schema.id)
        after = rebuild_preview(db_session, world.family.id).preview_hash

        assert again == first
        assert after != first

    def test_a_cosmetic_rename_between_two_previews_changes_the_hash(
        self, db_session, factories
    ):
        # Правка на месте, не меняющая ни номера версии, ни сумм оценки
        # (переименование параметра человека той же длины): хэш меняется только
        # через `revision`. Параметр модели не трогается — его правка сделала бы
        # его параметром человека и сменила бы оценку значений.
        world = _active_world(db_session, factories, PARAMS)
        _humanize(db_session, world, 2)
        first = rebuild_preview(db_session, world.family.id)
        drafts = same_drafts()
        drafts[1] = draft(2, "МАТЕРИАЛ", "бетон", "кирпич")
        edit(db_session, world.family.id, drafts, factories.UserFactory.create().id)

        after = rebuild_preview(db_session, world.family.id)

        assert _frozen_of(db_session, world.family.id).id == world.schema.id
        assert (after.reserve_usd, after.expected_cached_usd, after.context_count) == (
            first.reserve_usd, first.expected_cached_usd, first.context_count,
        )
        assert after.preview_hash != first.preview_hash

    def test_ten_human_parameters_leave_the_schema_job_out_of_the_estimate(
        self, db_session, factories
    ):
        full = _family_scene(db_session, factories, human=10)
        room = _family_scene(db_session, factories, human=9)

        assert rebuild_preview(db_session, full.family.id).reserve_usd == 0
        assert rebuild_preview(db_session, room.family.id).reserve_usd > 0

    def test_the_route_returns_the_approximation_flag(self, admin_client, db_session, factories):
        world = _preview_family(db_session, factories, human_second=True)
        bare = _family(db_session, status="active", definition="Без схемы")

        with_schema = admin_client.post(f"{BASE}/families/{world.family.id}/schema/rebuild/preview")
        without = admin_client.post(f"{BASE}/families/{bare.id}/schema/rebuild/preview")

        assert with_schema.status_code == 200, with_schema.text
        assert with_schema.json()["values_approximate"] is True
        assert without.json()["values_approximate"] is False
