"""HTTP-слой правки ставок НДС сметы (спека пересчёта §2.7, Задача 6).

`admin_client` — НАСТОЯЩИЙ пользователь-`admin` (не `client` из корневого
`tests/conftest.py`): тот подсовывает `MagicMock` с `id=1`, а этот роутер
кладёт `current_user.id` в `vat_rate_updated_by_id` — `RESTRICT`-FK на
`users.id` (models.py). Ни одного пользователя с `id=1` в свежей тестовой
транзакции нет, так что `client` уронил бы каждый успешный `PATCH` `Integrity
Error`-ом внутри сервиса (500 вместо 200) — та же ловушка, из-за которой
`test_category_overrides_api.py` держит `member_client`, а не корневой
`client` (см. докстринг `_role_client` в `tests/integration/conftest.py`).
"""
from __future__ import annotations

import threading
import time
from decimal import Decimal

import pytest
import sqlalchemy as sa

from models import Estimate
from services.estimate_vat import EstimateVatError, set_vat_rates

pytestmark = pytest.mark.integration


def test_target_rejected_when_any_proposal_has_unknown_base(admin_client, factories, db_session):
    """Краснеет от снятия проверки `if unknown: raise` в `set_vat_rates` (или от
    замены `unknown_base` на любой другой код) — без проверки запрос,
    объявляющий цель при неизвестной базе одного из двух предложений, ушёл бы
    в 200 и записал `vat_rate_target`."""
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20"), None])
    db_session.commit()
    response = admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    assert response.status_code == 422
    db_session.refresh(estimate)
    assert estimate.vat_rate_target is None


def test_declaring_base_then_target_succeeds(admin_client, factories, db_session):
    """Краснеет, если объявление базы НЕ снимает последующий запрет на цель —
    например, если проверка неизвестной базы смотрит на
    `estimate.vat_rate_base_override` вместо `new_base` (значения ПОСЛЕ
    применения текущего запроса) и потому не видит базу, объявленную этим же
    первым `PATCH`."""
    estimate = factories.estimate_with_proposals(vat_rates=[None])
    db_session.commit()
    assert admin_client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12"}
    ).status_code == 200
    assert admin_client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"}
    ).status_code == 200


def test_absent_field_is_not_touched(admin_client, factories, db_session):
    """Краснеет, если отсутствующее поле трактуется как `None` вместо «не
    менять» — например, если роутер всегда передаёт `body.base_override`
    вместо `UNSET` при отсутствии поля во входе. Тогда второй `PATCH` (только
    `base_override`) стёр бы `target`, выставленный первым запросом, и
    ассерт ниже поймал бы это как `estimate.vat_rate_target != Decimal('16')`."""
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12"})
    db_session.refresh(estimate)
    assert estimate.vat_rate_target == Decimal("16")


def test_null_clears_the_field(admin_client, factories, db_session):
    """Краснеет, если явный `null` не отличается от «не менять» — например,
    если роутер использует `body.target or UNSET`, и `None` (валидный `null`)
    ошибочно совпадает с отсутствием поля. Тогда второй `PATCH` не снял бы
    `target`, и ассерт поймал бы оставшееся `Decimal('16')`.

    Оба ответа проверяются на 200 явно: без этого тест был бы вакуозным —
    `vat_rate_target` по умолчанию и так `None` у свежесозданной сметы, и
    ассерт по значению прошёл бы даже если ОБА запроса безответно проваливались
    (например, на несуществующем маршруте, 404), ничего не записав."""
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    set_response = admin_client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"}
    )
    assert set_response.status_code == 200
    clear_response = admin_client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"target": None}
    )
    assert clear_response.status_code == 200
    db_session.refresh(estimate)
    assert estimate.vat_rate_target is None


def test_clearing_base_with_target_kept_is_rejected(admin_client, factories, db_session):
    """Краснеет, если сервис проверяет инвариант ПООЧЕРЁДНО по каждому полю
    вместо ИТОГОВОГО состояния — тогда попытка снять базу, оставив цель
    прежней (и предложение с неизвестной ставкой), молча прошла бы в 200
    вместо ожидаемого отказа 422."""
    estimate = factories.estimate_with_proposals(vat_rates=[None])
    db_session.commit()
    admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12"})
    admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    response = admin_client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": None}
    )
    assert response.status_code == 422


def test_clearing_both_rates_clears_the_audit(admin_client, factories, db_session):
    """Краснеет, если аудит не обнуляется вместе со снятием обеих ставок —
    например, если ветка `if new_base is None and new_target is None` удалена
    или заменена на безусловную запись `user_id`/`now()`. Тогда
    `vat_rate_updated_by_id`/`vat_rate_updated_at` остались бы заполненными
    после снятия последнего поля."""
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    db_session.refresh(estimate)
    assert estimate.vat_rate_updated_by_id is not None

    admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": None})
    db_session.refresh(estimate)
    assert estimate.vat_rate_updated_by_id is None
    assert estimate.vat_rate_updated_at is None


def test_clearing_only_one_rate_keeps_the_audit(admin_client, factories, db_session):
    """Ловушка аудита (приложение оркестратора §2): один и тот же автор на
    обоих запросах не отличил бы «аудит переписан заново» от «аудит вообще не
    писался» — обе картины оставили бы `vat_rate_updated_by_id` тем же самым
    значением. Второй запрос идёт от ВТОРОГО, отдельного admin
    (`admin_client.set_user`), и проверяется КОНКРЕТНОЕ значение (id второго
    автора), а не просто «не None» — так тест краснеет, если код НЕ
    перештамповывает аудит текущим автором при частичном снятии (оставляет
    id первого автора вместо второго), а не только если он ошибочно обнуляет
    аудит целиком."""
    estimate = factories.estimate_with_proposals(vat_rates=[None])
    db_session.commit()
    first_author = admin_client.user
    admin_client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12", "target": "16"}
    )
    db_session.refresh(estimate)
    assert estimate.vat_rate_updated_by_id == first_author.id

    second_author = factories.UserFactory.create(role=first_author.role)
    db_session.commit()
    admin_client.set_user(second_author)
    admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": None})
    db_session.refresh(estimate)
    assert estimate.vat_rate_updated_by_id is not None
    assert estimate.vat_rate_updated_by_id == second_author.id
    assert estimate.vat_rate_updated_by_id != first_author.id


@pytest.mark.parametrize("field", ["base_override", "target"])
def test_json_float_is_rejected(admin_client, factories, db_session, field):
    """`float` в ставке запрещён §3, а `Decimal | None` в Pydantic его принял бы.
    Краснеет от удаления `_reject_float_in_vat_rates` (или от переименования
    его в дубликат имени другого валидатора схемы, что тихо схлопнуло бы
    слот, `AGENTS.md` §11): без валидатора JSON-число `16.5` прошло бы как
    `Decimal` и запрос вернул бы 200."""
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    response = admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={field: 16.5})
    assert response.status_code == 422


@pytest.mark.parametrize("field", ["base_override", "target"])
def test_decimal_string_is_accepted(admin_client, factories, db_session, field):
    """Краснеет, если валидатор `_reject_float_in_vat_rates` слишком строг и
    отклоняет строковый вход тоже (например, `isinstance(value, (float, str))`)
    — тогда легитимная строка `"16.5"` тоже вернула бы 422 вместо 200."""
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    response = admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={field: "16.5"})
    assert response.status_code == 200


def test_member_is_forbidden(member_client, factories, db_session):
    """Краснеет от снятия `require_admin` (например, замены на просто
    `get_current_user`) — `member_client` держит НАСТОЯЩЕГО пользователя с
    ролью `member`, и без проверки роли запрос ушёл бы в 200/404, а не 403."""
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    response = member_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
#  Тест на гонку — форма как у test_category_override_concurrency.py: два
#  соединения, барьер между чтением и записью через факт настоящей блокировки
#  строки (`pg_stat_activity.wait_event_type = 'Lock'`), проверка итога.
#  Хелпер `_wait_until_a_backend_blocks` — копия оттуда, не импорт (тестовые
#  хелперы в этом репозитории не делятся между модулями тестов).
# ---------------------------------------------------------------------------


def _wait_until_a_backend_blocks(session_factory, *, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    with session_factory() as probe:
        while time.monotonic() < deadline:
            blocked = probe.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            probe.rollback()
            if blocked:
                return True
            time.sleep(0.05)
    return False


@pytest.fixture
def vat_race_scene(committing_db, committing_factories, committing_session_factory):
    """Смета с ДВУМЯ предложениями на разных лотах: у одного ставка НДС
    известна (20), у второго — нет. Стартовое состояние — `base_override`
    объявлена (20, покрывает неизвестную ставку второго предложения), `target`
    ещё не задана.

    Сценарий гонки: оператор А снимает `base_override` (цель на момент его
    чтения ещё не задана — его собственная проверка невозможности нарушить
    инвариант всегда проходит тривиально, T=None). Оператор Б, ПАРАЛЛЕЛЬНО,
    задаёт `target=16`, не трогая `base_override` явно (UNSET — держится
    прочитанным значением). Если Б успевает прочитать строку сметы ДО коммита
    А (без блокировки это неизбежно — обычный `SELECT` не ждёт чужую
    незакоммиченную запись), он видит ещё не снятую базу (20), его
    собственная проверка проходит, и он коммитит `target=16`, не трогая
    колонку `base_override` (SQLAlchemy не помечает колонку изменённой, если
    присвоенное значение равно уже загруженному). После этого коммитится А —
    и его собственная UPDATE трогает ТОЛЬКО `base_override` (по той же причине).
    Итог без блокировки: `base_override=None`, `target=16`, при этом второе
    предложение с неизвестной ставкой — комбинация, которую инвариант
    запрещает, и которую НИ ОДНА из двух транзакций не увидела на момент
    собственной проверки.

    `with_for_update()` в `set_vat_rates` не даёт этому случиться: Б обязан
    дождаться коммита А, прежде чем прочитать (и провалидировать) строку
    сметы, и тогда увидит уже снятую базу — его собственная проверка найдёт
    предложение с неизвестной ставкой и откажет 422.
    """
    estimate = committing_factories.EstimateFactory.create()
    lot_known = committing_factories.LotFactory.create(estimate=estimate)
    committing_factories.ProposalFactory.create(lot=lot_known, vat_rate=Decimal("20"))
    lot_unknown = committing_factories.LotFactory.create(estimate=estimate)
    committing_factories.ProposalFactory.create(lot=lot_unknown, vat_rate=None)
    estimate.vat_rate_base_override = Decimal("20")

    from models import UserRole

    admin_a = committing_factories.UserFactory.create(role=UserRole.admin)
    admin_b = committing_factories.UserFactory.create(role=UserRole.admin)
    committing_db.commit()

    from types import SimpleNamespace

    return SimpleNamespace(
        session_factory=committing_session_factory,
        estimate_id=estimate.id,
        admin_a_id=admin_a.id,
        admin_b_id=admin_b.id,
    )


class TestConcurrentVatPatchesKeepTheInvariant:
    """Мера, которую даёт именно `with_for_update()` в `set_vat_rates`."""

    def test_concurrent_patches_keep_the_invariant(self, vat_race_scene):
        scene = vat_race_scene
        outcome: dict[str, object] = {}
        b_started = threading.Event()

        def operator_b():
            with scene.session_factory() as db_b:
                b_started.set()
                try:
                    set_vat_rates(
                        db_b,
                        estimate_id=scene.estimate_id,
                        target=Decimal("16"),
                        user_id=scene.admin_b_id,
                    )
                    db_b.commit()
                    outcome["b"] = "committed"
                except EstimateVatError as exc:
                    db_b.rollback()
                    outcome["b"] = f"rejected:{exc.code}"
                except Exception as exc:  # pragma: no cover — диагностика
                    db_b.rollback()
                    outcome["b"] = f"error:{type(exc).__name__}:{exc}"

        with scene.session_factory() as db_a:
            set_vat_rates(
                db_a,
                estimate_id=scene.estimate_id,
                base_override=None,
                user_id=scene.admin_a_id,
            )
            thread = threading.Thread(target=operator_b, daemon=True)
            thread.start()
            assert b_started.wait(timeout=10)
            # Этот ассерт НЕ дискриминирует: Б рано или поздно упрётся в лок
            # строки сметы regardless — если не на `SELECT ... FOR UPDATE`, то
            # позже, на собственном `UPDATE estimates` (обычная запись тоже
            # берёт row-lock, независимо от того, кто и как читал строку до
            # неё). Проверено эмпирически (см. отчёт задачи): при снятом
            # `with_for_update()` это ожидание тоже наступает — просто СЛИШКОМ
            # ПОЗДНО, уже ПОСЛЕ того, как Б успел провалидировать инвариант по
            # устаревшему снимку базы. Тот же случай, что у
            # `TestReplaceRaceWithDecision` в `test_category_override_
            # concurrency.py`: блокировка есть в обоих случаях, мера — не она.
            assert _wait_until_a_backend_blocks(scene.session_factory), (
                "оператор Б не встал на ожидание замка — смета не блокируется"
            )
            db_a.commit()

        thread.join(timeout=20)
        assert not thread.is_alive()
        # МЕРА: единственный ассерт, который красный ИМЕННО и ТОЛЬКО при снятом
        # `with_for_update()`. С блокировкой на `SELECT` Б обязан перечитать
        # УЖЕ снятую базу ДО собственной проверки и отказать (unknown_base).
        # Без неё Б проверяет инвариант по устаревшему снимку (база ещё «20»),
        # проходит проверку и коммитит `target=16` — а к моменту его
        # собственного `UPDATE` лока на строке уже никакого нет (А ещё не
        # начал свой `UPDATE` в этой ветке событий), так что блокировка выше
        # тоже может пройти вхолостую или сработать не в ту сторону; красный
        # именно этот ассерт, а не предыдущий.
        assert outcome == {"b": "rejected:unknown_base"}, outcome

        with scene.session_factory() as check:
            row = check.execute(
                sa.select(Estimate.vat_rate_base_override, Estimate.vat_rate_target).where(
                    Estimate.id == scene.estimate_id
                )
            ).one()
            assert row.vat_rate_base_override is None
            assert row.vat_rate_target is None
