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
from sqlalchemy.exc import IntegrityError

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


def test_response_carries_rates_as_strings_not_floats(admin_client, factories, db_session):
    """Ревью (Critical): голый `dict` из роутера идёт через `jsonable_encoder`
    ДО рендера, а тот превращает `Decimal` во `float` — роутер отвергал бы
    `float` на входе (`_reject_float_in_vat_rates`) и сам же отдавал бы его на
    выходе, нарушая `AGENTS.md` §3 на слое ответа. Ни один из тестов выше в
    ТЕЛО ответа не смотрел вовсе (только код статуса и состояние БД), так что
    этот дефект (унаследованный из буквального кода брифа — там был голый
    `return {...}`) ни один из них не поймал бы.

    Краснеет от возврата голого `dict` вместо `decimal_json(...)` — тогда
    `"0.1"` (строка) стала бы `0.1` (JSON-числом), и `isinstance(..., str)`
    ниже был бы `False`. Проверяется именно РАЗОБРАННЫЙ JSON (тип значения),
    а не факт наличия ключа — по итогам предыдущего ревью «утверждение о
    наличии поля вместо утверждения о его значении» само по себе не
    дискриминирует."""
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    response = admin_client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "0.1", "target": "0.1"}
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["vat_rate_base_override"], str)
    assert body["vat_rate_base_override"] == "0.1"
    assert isinstance(body["vat_rate_target"], str)
    assert body["vat_rate_target"] == "0.1"
    # Сырой текст ответа — вторая, независимая проверка формы: число без
    # кавычек в JSON выглядело бы как `"vat_rate_target":0.1`, со строкой —
    # как `"vat_rate_target":"0.1"`.
    assert '"vat_rate_target":"0.1"' in response.text
    assert '"vat_rate_base_override":"0.1"' in response.text
    # updated_at обязан доехать ISO-строкой (crud.common.iso), а не упасть
    # `TypeError`-ом внутри энкодера `decimal_json` (он умеет только Decimal).
    assert isinstance(body["vat_rate_updated_at"], str)


def test_declaring_base_then_target_succeeds(admin_client, factories, db_session):
    """Краснеет от двух РАЗНЫХ поломок (проверено мутацией по каждой):

    1. От непроводки объявленной базы между запросами — если бы первый `PATCH`
       не записывал `vat_rate_base_override` (или не коммитил запись), второй
       запрос увидел бы базу всё ещё неизвестной и получил бы 422 вместо 200.
    2. От безусловной проверки неизвестной базы — если бы условие
       `if new_target is not None and new_base is None` потеряло вторую
       половину (стало `if new_target is not None`), проверка сработала бы
       ДАЖЕ при уже объявленной базе, и второй запрос снова получил бы 422.

    (Различие «`new_base` пересчитанное внутри вызова» vs
    `estimate.vat_rate_base_override`, прочитанный внутри ТОГО ЖЕ вызова, тут
    не проверяется: `admin_client` использует один `db_session` на оба
    запроса, и к моменту второго запроса это один и тот же закешированный
    ORM-объект в identity map — оба выражения уже равны. Ту, другую, поломку
    ловит первый `PATCH` в `test_clearing_only_one_rate_keeps_the_audit` — там
    `base_override` и `target` заданы ОДНИМ запросом при неизвестной ставке
    предложения.)
    """
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


def test_clearing_base_and_target_together_is_accepted(admin_client, factories, db_session):
    """Ревью (Important): единственный тест, где ОБА поля снимаются ОДНИМ
    запросом при неизвестной ставке предложения — оправдывает архитектурный
    выбор «проверять ИТОГ, а не поле за полем» (докстринг `set_vat_rates`).
    Пополевая реализация («откажи, если снимают базу, а в БД лежит цель»)
    проходит ВЕСЬ остальной набор этого файла не хуже правильной —
    `test_clearing_base_with_target_kept_is_rejected` держит цель ЗАДАННОЙ
    (не снимает её тем же запросом), так что она эту дыру не закрывает.

    Краснеет, если инвариант проверяется по ПРОМЕЖУТОЧНОМУ состоянию
    («сначала применили снятие базы — target прочитанный ещё старый, `16` —
    отказ 422»), а не по ФИНАЛЬНОМУ (`new_base=None, new_target=None` —
    легально независимо от того, известны ли ставки предложений)."""
    estimate = factories.estimate_with_proposals(vat_rates=[None])
    db_session.commit()
    admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": "12"})
    admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})

    response = admin_client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"base_override": None, "target": None}
    )
    assert response.status_code == 200
    db_session.refresh(estimate)
    assert estimate.vat_rate_base_override is None
    assert estimate.vat_rate_target is None
    assert estimate.vat_rate_updated_by_id is None
    assert estimate.vat_rate_updated_at is None


def test_empty_patch_does_not_restamp_the_audit(admin_client, factories, db_session):
    """Ревью (Minor, но неверное поведение): замерено, что `PATCH {}`
    (ни одно поле не передано) отвечал 200, ничего не менял по ставкам, но
    ВСЁ РАВНО перештамповывал `vat_rate_updated_by_id`/`vat_rate_updated_at`
    текущим вызывающим — аудит «действующей поправки» доставался тому, кто
    просто дёрнул ручку.

    Второй запрос идёт от ВТОРОГО, отдельного admin (`admin_client.set_user`)
    — та же ловушка аудита, что и у `test_clearing_only_one_rate_keeps_the_
    audit`: если бы проверялось только «автор не None», тест не отличил бы
    «аудит не тронут» от «аудит переписан заново тем же значением». Здесь
    второй запрос — от ДРУГОГО пользователя, так что перештамповка обязана
    сдвинуть `vat_rate_updated_by_id` на второго автора, если она происходит;
    тест краснеет именно на этом сравнении (и на `updated_at`, который обязан
    остаться БУКВАЛЬНО тем же объектом времени, а не просто «не None»)."""
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    first_author = admin_client.user
    admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    db_session.refresh(estimate)
    assert estimate.vat_rate_updated_by_id == first_author.id
    first_updated_at = estimate.vat_rate_updated_at

    second_author = factories.UserFactory.create(role=first_author.role)
    db_session.commit()
    admin_client.set_user(second_author)

    response = admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={})
    assert response.status_code == 200
    db_session.refresh(estimate)
    assert estimate.vat_rate_target == Decimal("16")
    assert estimate.vat_rate_base_override is None
    assert estimate.vat_rate_updated_by_id == first_author.id
    assert estimate.vat_rate_updated_by_id != second_author.id
    assert estimate.vat_rate_updated_at == first_updated_at


def test_author_of_a_live_correction_cannot_be_deleted_but_can_after_clearing(
    admin_client, factories, db_session
):
    """Ревью (SPEC FAIL): спека §4.3 требует ПАРУ утверждений, не одно —
    пользователя с ДЕЙСТВУЮЩЕЙ поправкой удалить нельзя, а ПОСЛЕ снятия
    поправки — можно. До этого теста была проверена только защита (RESTRICT
    как таковой, косвенно — через CHECK/FK схемы других фич), а не пара
    целиком применительно именно к `vat_rate_updated_by_id`.

    Первая половина краснеет, если `ondelete="RESTRICT"` на
    `vat_rate_updated_by_id` (models.py) заменить на `SET NULL` или снять
    вовсе: `DELETE FROM users` тогда тихо прошёл бы, пока поправка ещё
    действует, — и её автор потерялся бы без предупреждения.

    Вторая половина краснеет, если снятие обеих ставок (тест
    `test_clearing_both_rates_clears_the_audit` проверяет это отдельно) НЕ
    освобождает автора — тогда второй `DELETE` здесь тоже упал бы
    `IntegrityError`, и пользователь остался бы неудаляемым НАВСЕГДА (ровно
    та цена бездумного безусловного аудита, которую называет докстринг
    `set_vat_rates`). Проверяется, что строка `users` РЕАЛЬНО удалена (счётчик
    по id), а не только что второй `DELETE` не упал."""
    estimate = factories.estimate_with_proposals(vat_rates=[Decimal("20")])
    db_session.commit()
    author_id = admin_client.user.id

    response = admin_client.patch(f"/api/v1/estimates/{estimate.id}/vat", json={"target": "16"})
    assert response.status_code == 200

    with (
        pytest.raises(IntegrityError, match="fk_estimates_vat_rate_updated_by_id"),
        db_session.begin_nested(),
    ):
        db_session.execute(sa.text("DELETE FROM users WHERE id = :uid"), {"uid": author_id})
        db_session.flush()

    clear_response = admin_client.patch(
        f"/api/v1/estimates/{estimate.id}/vat", json={"target": None}
    )
    assert clear_response.status_code == 200

    db_session.execute(sa.text("DELETE FROM users WHERE id = :uid"), {"uid": author_id})
    db_session.flush()
    remaining = db_session.execute(
        sa.text("SELECT count(*) FROM users WHERE id = :uid"), {"uid": author_id}
    ).scalar_one()
    assert remaining == 0


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
