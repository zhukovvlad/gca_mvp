"""Кэш сцен этапа на маршрутах «КП против нашей цены» (спека кэша этапа §2, §3, §4.3).

Попадание считает шпион над `crud.our_price._build_scene`: в продуктовом коде счётчика нет. Каждое
сравнение ответа идёт с ответом, собранным при выключенных обоих кэшах (сцен и ставок) в том же тесте.
Транзакционная фикстура `db_session` коммитит как внешняя, поэтому клиент перед каждым запросом закрепляет
накопленные записи сцены (как в соседних наборах): первый запрос после этого видит чистую транзакцию, а
следующие — только записи самого маршрута.
"""

from __future__ import annotations

import inspect
import pathlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

import crud.our_price as our_price
import services.import_pipeline as import_pipeline
from config import settings
from models import Estimate
from services.stage_cache import stage_scene_cache
from services.status_cache import typical_rates_cache
from tests.integration.test_analog_targets import _scene as _work_scene
from tests.integration.test_our_price_load import World
from tests.integration.test_our_price_offer_api import _inflation_scene
from tests.integration.test_our_price_offer_api import _system_scene as _sys_scene
from tests.integration.test_our_price_round_api import TARGET_MISSING_TEXT, Stage
from tests.integration.test_ref_analogs_target import Ctx, _body, _link_conversion, _linked, _panel_row

pytestmark = pytest.mark.integration

D = Decimal

TTL_ON = 600


@pytest.fixture
def world(db_session, factories, admin_user):
    return World(db_session, factories, admin_user)


@pytest.fixture
def stage(world, db_session, factories):
    return Stage(world, db_session, factories)


@pytest.fixture
def api(admin_client, db_session, tmp_storage):
    """Администратор; перед каждым запросом закрепляются записи сцены, хранилище — каталог теста."""
    from main import app
    from storage import get_storage

    send = admin_client.request

    def request(*args, **kwargs):
        Session.commit(db_session)
        return send(*args, **kwargs)

    admin_client.request = request
    app.dependency_overrides[get_storage] = lambda: tmp_storage
    yield admin_client
    app.dependency_overrides.pop(get_storage, None)


@pytest.fixture
def builds(monkeypatch):
    """Шпион над `_build_scene`: сколько раз сцена собиралась холодным путём."""
    box = SimpleNamespace(n=0)
    real = our_price._build_scene

    def spy(*args, **kwargs):
        box.n += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(our_price, "_build_scene", spy)
    return box


class Caches:
    """Оба кэша сразу: включены (`on`) или выключены (`off`) вместе, и пусты после переключения."""

    def __init__(self, monkeypatch):
        self._mp = monkeypatch

    def _set(self, ttl: int) -> None:
        self._mp.setattr(settings, "STAGE_SCENE_CACHE_TTL_S", ttl)
        self._mp.setattr(settings, "TYPICAL_RATES_CACHE_TTL_S", ttl)
        typical_rates_cache.invalidate()
        stage_scene_cache.invalidate()

    def on(self) -> None:
        self._set(TTL_ON)

    def off(self) -> None:
        self._set(0)


@pytest.fixture
def caches(monkeypatch):
    return Caches(monkeypatch)


# ---------------------------------------------------------------------------
#  Сцены и помощники
# ---------------------------------------------------------------------------


@dataclass
class Sc:
    stage: object
    rnd: object
    part: object
    item: object
    extra: object = None
    series: int | None = None
    ctx: Ctx | None = field(default=None)

    @property
    def base(self) -> str:
        return f"/api/v1/tenders/{self.stage.tender.id}/rounds/{self.rnd.id}/our-price"

    @property
    def offer_url(self) -> str:
        return f"{self.base}/offers/{self.part.offer.id}"

    @property
    def line_url(self) -> str:
        return f"{self.offer_url}/lines/{self.item.id}"

    def paths(self) -> dict[str, str]:
        return {
            "round": self.base,
            "offer": self.offer_url,
            "panel": self.line_url,
            "preview": f"{self.offer_url}/export-preview",
        }


def work_sc(stage, world) -> Sc:
    scene = _work_scene(stage, world)
    return Sc(stage, scene.rnd, scene.part, scene.item, extra=scene)


def system_sc(stage) -> Sc:
    rnd, part, items = _sys_scene(stage)
    stage.db.flush()
    return Sc(stage, rnd, part, items[0])


def linked_sc(stage) -> Sc:
    scene, ctx = _linked(stage, price=D("1107.24"), kp_price=D("14400"))
    return Sc(stage, scene.rnd, scene.part, scene.kp_item, extra=scene, ctx=ctx)


def fetch(api, sc: Sc, *, series: int | None = None, names=None) -> dict[str, tuple[int, str]]:
    """Ответы четырёх экранов: статус и полный текст."""
    params = {} if series is None else {"inflation_series_id": series}
    out = {}
    for name, path in sc.paths().items():
        if names is not None and name not in names:
            continue
        response = api.get(path, params=params)
        out[name] = (response.status_code, response.text)
    return out


def ok_all(responses: dict[str, tuple[int, str]]) -> None:
    for name, (status, text) in responses.items():
        assert status == 200, f"{name}: {text}"


def _put(api, sc: Sc, suffix: str, body, **params):
    return api.put(f"{sc.line_url}/{suffix}", json=body, params=params)


def _delete(api, sc: Sc, suffix: str):
    return api.delete(f"{sc.line_url}/{suffix}")


def _ref_put(api, sc: Sc):
    row = _panel_row(sc.ctx.panel(api), sc.extra.ref_item.id)
    body = _body(sc.extra.ref_item.id, origin="link", conversion=_link_conversion(row))
    return _put(api, sc, "target", body)


def _export(api, sc: Sc):
    return api.post(f"{sc.offer_url}/export")


# (идентификатор, сцена, подготовка при выключенных кэшах, действие, ожидаемый статус действия)
WRITES = [
    ("agreed_put", "work", None, lambda a, s: _put(a, s, "agreed", {"value": "130", "basis": "net"}), 204),
    (
        "agreed_delete",
        "work",
        lambda a, s: _put(a, s, "agreed", {"value": "130", "basis": "net"}),
        lambda a, s: _delete(a, s, "agreed"),
        204,
    ),
    ("comment_put", "work", None, lambda a, s: _put(a, s, "comment", {"text": "Просим снизить"}), 204),
    (
        "comment_delete",
        "work",
        lambda a, s: _put(a, s, "comment", {"text": "Просим снизить"}),
        lambda a, s: _delete(a, s, "comment"),
        204,
    ),
    ("rung_put", "system", None, lambda a, s: _put(a, s, "rung", {"level": "v", "scope": "cls"}), 204),
    (
        "rung_delete",
        "system",
        lambda a, s: _put(a, s, "rung", {"level": "v", "scope": "cls"}),
        lambda a, s: _delete(a, s, "rung"),
        204,
    ),
    ("target_put", "work", None, lambda a, s: _put(a, s, "target", {"kind": "freshest"}), 204),
    (
        "target_delete",
        "work",
        lambda a, s: _put(a, s, "target", {"kind": "freshest"}),
        lambda a, s: _delete(a, s, "target"),
        204,
    ),
    ("reference_target_put", "linked", None, _ref_put, 204),
    ("reference_target_delete", "linked", _ref_put, lambda a, s: _delete(a, s, "target"), 204),
    ("export", "work", None, _export, 200),
]


def _scene_of(kind: str, stage, world) -> Sc:
    if kind == "work":
        return work_sc(stage, world)
    if kind == "system":
        return system_sc(stage)
    return linked_sc(stage)


# ---------------------------------------------------------------------------
#  Одна точка входа
# ---------------------------------------------------------------------------


def test_stage_cache_api_build_scene_is_called_only_inside_scene_for() -> None:
    """`grep _build_scene( crud/` находит один вызов, и он — в `_scene_for`."""
    crud = pathlib.Path(our_price.__file__).parent
    calls = []
    for path in sorted(crud.glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\b_build_scene\(", line) and not line.lstrip().startswith("def "):
                calls.append((path.name, number))
    assert len(calls) == 1, calls
    assert "_build_scene(" in inspect.getsource(our_price._scene_for)


# ---------------------------------------------------------------------------
#  Попадания и ключи
# ---------------------------------------------------------------------------


def test_stage_cache_api_second_pass_of_all_four_screens_is_a_hit(api, stage, world, caches, builds) -> None:
    sc = work_sc(stage, world)
    caches.on()

    first = fetch(api, sc)
    ok_all(first)
    assert builds.n == 2  # ключ этапа и ключ участника: разбор, панель и предпросмотр делят второй
    second = fetch(api, sc)

    assert builds.n == 2
    assert second == first
    caches.off()
    assert fetch(api, sc) == second


def test_stage_cache_api_offer_then_round_are_two_keys_and_two_builds(api, stage, world, caches, builds) -> None:
    sc = work_sc(stage, world)
    caches.on()

    offer = fetch(api, sc, names={"offer"})
    assert builds.n == 1
    stage_view = fetch(api, sc, names={"round"})
    assert builds.n == 2
    assert fetch(api, sc, names={"offer", "round"}) == {**offer, **stage_view}
    assert builds.n == 2

    caches.off()
    assert fetch(api, sc, names={"round"}) == stage_view


def test_stage_cache_api_series_and_no_series_are_different_keys(api, stage, db_session, caches, builds) -> None:
    rnd, part, item, series_id = _inflation_scene(stage, db_session, "ok")
    sc = Sc(stage, rnd, part, item)
    caches.on()

    plain = fetch(api, sc, names={"round", "offer"})
    assert builds.n == 2
    with_series = fetch(api, sc, series=series_id, names={"round", "offer"})
    assert builds.n == 4
    assert plain != with_series
    assert fetch(api, sc, names={"round", "offer"}) == plain
    assert fetch(api, sc, series=series_id, names={"round", "offer"}) == with_series
    assert builds.n == 4

    caches.off()
    assert fetch(api, sc, names={"round", "offer"}) == plain
    assert fetch(api, sc, series=series_id, names={"round", "offer"}) == with_series


def test_stage_cache_api_inflation_refusal_is_not_cached(api, stage, db_session, caches, builds) -> None:
    rnd, part, item, series_id = _inflation_scene(stage, db_session, "target")
    sc = Sc(stage, rnd, part, item)
    caches.on()

    first = fetch(api, sc, series=series_id, names={"offer"})
    second = fetch(api, sc, series=series_id, names={"offer"})

    assert builds.n == 2
    assert first["offer"][0] == second["offer"][0] == 422
    assert TARGET_MISSING_TEXT in first["offer"][1]
    assert second == first
    caches.off()
    assert fetch(api, sc, series=series_id, names={"offer"}) == first


def test_stage_cache_api_zero_ttl_builds_every_request(api, stage, world, caches, builds) -> None:
    sc = work_sc(stage, world)
    caches.on()
    cached = fetch(api, sc)
    ok_all(cached)
    caches.off()
    builds.n = 0

    first = fetch(api, sc)
    assert builds.n == 4
    second = fetch(api, sc)

    assert builds.n == 8
    assert first == second == cached


# ---------------------------------------------------------------------------
#  Закрытый список: решения и выгрузка не сбрасывают кэш
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", WRITES, ids=[case[0] for case in WRITES])
def test_stage_cache_api_decision_writes_keep_the_scenes_and_show_fresh_decisions(
    api, stage, world, caches, builds, case
) -> None:
    _, kind, setup, action, expected_status = case
    sc = _scene_of(kind, stage, world)
    if setup is not None:
        caches.off()
        response = setup(api, sc)
        assert response.status_code == 204, response.text
    caches.on()
    builds.n = 0
    before = fetch(api, sc)
    ok_all(before)
    warm_builds = builds.n
    assert warm_builds == 2

    response = action(api, sc)
    assert response.status_code == expected_status, response.text
    after = fetch(api, sc)

    assert builds.n == warm_builds, "запись закрытого списка сбросила кэш"
    ok_all(after)
    assert after != before, "запись ничего не изменила в ответах: тест пуст"
    caches.off()
    assert fetch(api, sc) == after


def test_stage_cache_api_export_with_a_reference_target_and_a_chapter_path_keeps_the_scenes(
    api, stage, caches, builds
) -> None:
    """Критерий 8a: выгрузка со справочной целью читает путь разделов (CTE) и не сбрасывает кэш."""
    sc = linked_sc(stage)
    caches.off()
    row = _panel_row(sc.ctx.panel(api), sc.extra.ref_item.id)
    assert row["chapter_paths"] and all(row["chapter_paths"]), "в сцене нет пути разделов"
    assert _ref_put(api, sc).status_code == 204
    caches.on()
    builds.n = 0
    before = fetch(api, sc)
    ok_all(before)
    warm_builds = builds.n
    assert warm_builds == 2

    response = _export(api, sc)
    assert response.status_code == 200, response.text
    after = fetch(api, sc)

    assert builds.n == warm_builds
    ok_all(after)
    assert after != before
    caches.off()
    assert fetch(api, sc) == after


# ---------------------------------------------------------------------------
#  Вне закрытого списка: сброс
# ---------------------------------------------------------------------------


def test_stage_cache_api_vat_base_of_a_kp_estimate_resets(api, stage, world, caches, builds, db_session) -> None:
    sc = work_sc(stage, world)
    sc.part.proposal.vat_rate = None  # база КП неизвестна: участник пуст, пока её не назначат
    db_session.flush()
    caches.on()
    before = fetch(api, sc)
    assert before["preview"][0] == 422  # без базы файла подрядчику нет
    warm_builds = builds.n

    response = api.patch(f"/api/v1/estimates/{sc.part.estimate.id}/vat", json={"base_override": "20"})
    assert response.status_code == 200, response.text
    after = fetch(api, sc)

    assert builds.n == warm_builds + 2
    ok_all(after)  # с назначенной базой файл есть
    assert after["offer"] != before["offer"]
    caches.off()
    assert fetch(api, sc) == after


def test_stage_cache_api_not_analog_mark_resets(api, stage, world, caches, builds) -> None:
    sc = work_sc(stage, world)
    caches.on()
    before = fetch(api, sc)
    ok_all(before)
    warm_builds = builds.n

    analog = sc.extra.ids["low"]
    response = api.put(f"{sc.offer_url}/lines/{sc.item.id}/not-analog/{analog}", json={"reason": "другая работа"})
    assert response.status_code == 200, response.text
    after = fetch(api, sc)

    assert builds.n == warm_builds + 2
    ok_all(after)
    assert after["panel"] != before["panel"]
    caches.off()
    assert fetch(api, sc) == after


def test_stage_cache_api_background_import_job_commit_resets(api, stage, world, caches, builds, db_session, monkeypatch):
    sc = work_sc(stage, world)
    caches.on()
    before = fetch(api, sc)
    ok_all(before)
    warm_builds = builds.n
    estimate_id = sc.part.estimate.id

    def background_session() -> Session:
        return Session(bind=db_session.get_bind(), autoflush=False, join_transaction_mode="create_savepoint")

    def fake_run(job_id, *, session_factory, **_kwargs):
        with session_factory() as session:
            session.execute(sa.update(Estimate).where(Estimate.id == estimate_id).values(vat_rate_target=D("7")))
            session.commit()

    monkeypatch.setattr(import_pipeline, "_run_import_job", fake_run)
    import_pipeline.run_import_job(1, session_factory=background_session, storage=None)
    after = fetch(api, sc)

    assert builds.n == warm_builds + 2
    caches.off()
    assert fetch(api, sc) == after


def test_stage_cache_api_deleting_a_participant_gives_404_on_both_screens(api, stage, world, caches, builds) -> None:
    sc = work_sc(stage, world)
    second = stage.participant(sc.rnd, "Подрядчик Б")
    other = Sc(stage, sc.rnd, second, stage.kp(second, D("150")))
    package_id = second.offer.package_id
    offer_url, line_url = other.offer_url, other.line_url
    tender_id = stage.tender.id
    caches.on()
    warm = fetch(api, other, names={"offer", "panel"})
    ok_all(warm)

    url = f"/api/v1/tenders/{tender_id}/participants/{package_id}"
    preview = api.delete(url)
    assert preview.status_code == 409, preview.text
    token = preview.json()["detail"]["confirmation_token"]
    assert api.delete(url, params={"confirmation_token": token}).status_code == 204

    cached = {name: api.get(path) for name, path in (("offer", offer_url), ("panel", line_url))}
    caches.off()
    cold = {name: api.get(path) for name, path in (("offer", offer_url), ("panel", line_url))}
    for name in ("offer", "panel"):
        assert cached[name].status_code == 404
        assert (cached[name].status_code, cached[name].text) == (cold[name].status_code, cold[name].text)


# ---------------------------------------------------------------------------
#  Попадание с require_line
# ---------------------------------------------------------------------------


def test_stage_cache_api_hit_with_a_line_missing_in_the_kp_is_the_same_404(api, stage, world, caches, builds) -> None:
    sc = work_sc(stage, world)
    foreign = sc.extra.ids["low"]  # строка договора, в КП оффера её нет
    caches.on()
    ok_all(fetch(api, sc, names={"offer"}))
    warm_builds = builds.n

    hit = api.get(f"{sc.offer_url}/lines/{foreign}")

    assert builds.n == warm_builds
    assert hit.status_code == 404
    assert f"Строка {foreign} не найдена в КП оффера {sc.part.offer.id}." in hit.text
    caches.off()
    cold = api.get(f"{sc.offer_url}/lines/{foreign}")
    assert (hit.status_code, hit.text) == (cold.status_code, cold.text)


def test_stage_cache_api_panel_of_a_real_line_after_the_offer_is_a_hit(api, stage, world, caches, builds) -> None:
    sc = work_sc(stage, world)
    caches.on()
    offer = fetch(api, sc, names={"offer"})
    ok_all(offer)
    warm_builds = builds.n

    panel = fetch(api, sc, names={"panel"})

    assert builds.n == warm_builds
    ok_all(panel)
    caches.off()
    assert fetch(api, sc, names={"panel"}) == panel

