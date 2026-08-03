"""CRUD нормативов и переутверждение (фаза 5, §7.3).

Проверяется то, что фаза 5 добавила поверх схемы фазы 2:

* пересечение периодов — **400 с человеческим текстом**, а не 500 из EXCLUDE;
* переутверждение — `UPDATE valid_to` + `INSERT` в одной транзакции, история не
  мутируется, и через VIEW отклонений видно, что старая смета не изменилась (DoD);
* ставка и коэффициент — точные: `float` на входе отклоняется, произведение
  `ставка × индекс` не округляется;
* право `admin` — по букве §3.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from models import RateStandard, UserRole

pytestmark = pytest.mark.integration


@pytest.fixture
def member(client):
    client.auth_state["role"] = UserRole.member
    yield client
    client.auth_state["role"] = UserRole.admin


@pytest.fixture
def pair(factories):
    """Работа каталога и класс объектов — пара, для которой действует норматив."""
    return (
        factories.CatalogPositionFactory.create(standard_job_title="Кладка кирпичная"),
        factories.RateClassFactory.create(title="Жилые дома"),
    )


def _payload(position, rate_class, **extra) -> dict:
    body = {
        "catalog_position_id": position.id,
        "rate_class_id": rate_class.id,
        "standard_unit_rate": "1000.00",
        "valid_from": "2025-01-01",
    }
    body.update(extra)
    return body


def _deviation(db_session, item_id: int):
    row = db_session.execute(
        sa.text("SELECT * FROM v_position_deviations WHERE position_item_id = :id"),
        {"id": item_id},
    ).mappings().one()
    return row


# ---------------------------------------------------------------------------
#  Создание
# ---------------------------------------------------------------------------

def test_create_rate_standard_returns_money_as_string(client, pair):
    position, rate_class = pair
    response = client.post("/api/v1/rate-standards", json=_payload(position, rate_class))
    assert response.status_code == 201
    assert '"standard_unit_rate":"1000.00"' in response.text
    body = response.json()
    assert body["catalog_position_title"] == "Кладка кирпичная"
    assert body["rate_class_title"] == "Жилые дома"
    assert body["valid_to"] is None


def test_overlapping_period_gives_readable_400_not_500(client, pair):
    """§7.3: ошибку EXCLUDE обязан объяснить человек, а не трассировка."""
    position, rate_class = pair
    assert client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class)
    ).status_code == 201

    response = client.post(
        "/api/v1/rate-standards",
        json=_payload(position, rate_class, valid_from="2025-06-01"),
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "пересекается" in detail
    assert "Кладка кирпичная" in detail
    assert "Жилые дома" in detail


def test_adjacent_periods_are_allowed(client, pair):
    """Полуинтервал [from, to): окончание одного = начало следующего, без зазора."""
    position, rate_class = pair
    assert client.post(
        "/api/v1/rate-standards",
        json=_payload(position, rate_class, valid_from="2025-01-01", valid_to="2026-01-01"),
    ).status_code == 201
    assert client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class, valid_from="2026-01-01")
    ).status_code == 201


def test_same_period_for_another_class_is_allowed(client, pair, factories):
    """EXCLUDE запрещает пересечение у ПАРЫ, а не у работы вообще."""
    position, rate_class = pair
    other_class = factories.RateClassFactory.create(title="Промышленные")
    assert client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class)
    ).status_code == 201
    assert client.post(
        "/api/v1/rate-standards", json=_payload(position, other_class)
    ).status_code == 201


def test_non_positive_rate_gives_422(client, pair):
    position, rate_class = pair
    response = client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class, standard_unit_rate="0")
    )
    assert response.status_code == 422
    assert "больше нуля" in response.json()["detail"]


def test_end_before_start_gives_422(client, pair):
    position, rate_class = pair
    response = client.post(
        "/api/v1/rate-standards",
        json=_payload(position, rate_class, valid_from="2026-01-01", valid_to="2025-01-01"),
    )
    assert response.status_code == 422
    assert "строго позже" in response.json()["detail"]


def test_float_rate_is_rejected(client, pair):
    """§3: точные числа приходят строками. Коэффициент — тоже множитель ставки."""
    position, rate_class = pair
    assert client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class, standard_unit_rate=1000.5)
    ).status_code == 422
    assert client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class, inflation_index=1.07)
    ).status_code == 422


def test_unknown_references_give_404(client, pair, factories):
    position, rate_class = pair
    assert client.post(
        "/api/v1/rate-standards",
        json={
            "catalog_position_id": 999_999,
            "rate_class_id": rate_class.id,
            "standard_unit_rate": "1",
            "valid_from": "2025-01-01",
        },
    ).status_code == 404
    assert client.post(
        "/api/v1/rate-standards",
        json={
            "catalog_position_id": position.id,
            "rate_class_id": 999_999,
            "standard_unit_rate": "1",
            "valid_from": "2025-01-01",
        },
    ).status_code == 404


# ---------------------------------------------------------------------------
#  Список и фильтры
# ---------------------------------------------------------------------------

def test_on_date_filter_uses_half_open_interval(client, pair):
    """Тот же полуинтервал, что в EXCLUDE и VIEW: дата окончания уже НЕ входит."""
    position, rate_class = pair
    client.post(
        "/api/v1/rate-standards",
        json=_payload(position, rate_class, valid_from="2025-01-01", valid_to="2026-01-01"),
    )

    inside = client.get("/api/v1/rate-standards", params={"on_date": "2025-12-31"}).json()
    assert inside["total"] == 1

    on_boundary = client.get("/api/v1/rate-standards", params={"on_date": "2026-01-01"}).json()
    assert on_boundary["total"] == 0

    before = client.get("/api/v1/rate-standards", params={"on_date": "2024-12-31"}).json()
    assert before["total"] == 0


def test_open_ended_standard_is_active_on_any_later_date(client, pair):
    position, rate_class = pair
    client.post("/api/v1/rate-standards", json=_payload(position, rate_class))
    found = client.get("/api/v1/rate-standards", params={"on_date": "2030-07-01"}).json()
    assert found["total"] == 1


def test_list_filters_by_class_and_by_title(client, pair, factories):
    position, rate_class = pair
    other_position = factories.CatalogPositionFactory.create(standard_job_title="Стяжка пола")
    other_class = factories.RateClassFactory.create(title="Промышленные")
    client.post("/api/v1/rate-standards", json=_payload(position, rate_class))
    client.post("/api/v1/rate-standards", json=_payload(other_position, other_class))

    by_class = client.get(
        "/api/v1/rate-standards", params={"rate_class_id": rate_class.id}
    ).json()
    assert [item["catalog_position_title"] for item in by_class["items"]] == ["Кладка кирпичная"]

    by_title = client.get("/api/v1/rate-standards", params={"q": "стяжка"}).json()
    assert [item["catalog_position_title"] for item in by_title["items"]] == ["Стяжка пола"]


def test_list_returns_money_as_string(client, pair):
    position, rate_class = pair
    client.post("/api/v1/rate-standards", json=_payload(position, rate_class))
    assert '"standard_unit_rate":"1000.00"' in client.get("/api/v1/rate-standards").text


# ---------------------------------------------------------------------------
#  Переутверждение (§4, §7.3)
# ---------------------------------------------------------------------------

def test_reapprove_closes_old_period_exactly_at_new_start(client, pair, db_session):
    """§4: UPDATE valid_to старой строки + INSERT новой. История не мутируется."""
    position, rate_class = pair
    old_id = client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class)
    ).json()["id"]

    response = client.post(
        f"/api/v1/rate-standards/{old_id}/reapprove",
        json={"valid_from": "2026-01-01", "standard_unit_rate": "1070.00"},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["previous"]["id"] == old_id
    assert body["previous"]["valid_to"] == "2026-01-01"
    # Ставка прежнего периода не тронута — иначе отклонения старых смет уехали бы.
    assert body["previous"]["standard_unit_rate"] == "1000.00"

    assert body["current"]["id"] != old_id
    assert body["current"]["valid_from"] == "2026-01-01"
    assert body["current"]["valid_to"] is None
    assert body["current"]["standard_unit_rate"] == "1070.00"

    # Ровно две строки, зазора между периодами нет.
    rows = db_session.execute(
        sa.select(RateStandard)
        .where(RateStandard.catalog_position_id == position.id)
        .order_by(RateStandard.valid_from)
    ).scalars().all()
    assert [(r.valid_from, r.valid_to) for r in rows] == [
        (dt.date(2025, 1, 1), dt.date(2026, 1, 1)),
        (dt.date(2026, 1, 1), None),
    ]


def test_reapprove_computes_rate_from_index_without_rounding(client, pair):
    """Произведение точное: округление — дело слоя представления (§4)."""
    position, rate_class = pair
    old_id = client.post(
        "/api/v1/rate-standards",
        json=_payload(position, rate_class, standard_unit_rate="1000.33"),
    ).json()["id"]

    body = client.post(
        f"/api/v1/rate-standards/{old_id}/reapprove",
        json={"valid_from": "2026-01-01", "inflation_index": "1.075"},
    ).json()

    # 1000.33 × 1.075 = 1075.35475 — ни копейкой больше и ни копейкой меньше.
    assert Decimal(body["current"]["standard_unit_rate"]) == Decimal("1000.33") * Decimal("1.075")
    assert body["current"]["standard_unit_rate"] == "1075.35475"
    assert body["current"]["inflation_index"] == "1.075"


def test_reapprove_keeps_index_as_justification_when_rate_is_explicit(client, pair):
    """Индекс сохраняется как обоснование даже при явной ставке (форма §7.3)."""
    position, rate_class = pair
    old_id = client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class)
    ).json()["id"]

    body = client.post(
        f"/api/v1/rate-standards/{old_id}/reapprove",
        json={
            "valid_from": "2026-01-01",
            "standard_unit_rate": "1100.00",
            "inflation_index": "1.07",
            "approved_by": "Совет директоров",
            "note": "Округлено вверх решением совета",
        },
    ).json()
    assert body["current"]["standard_unit_rate"] == "1100.00"
    assert body["current"]["inflation_index"] == "1.07"
    assert body["current"]["approved_by"] == "Совет директоров"


def test_reapprove_without_rate_or_index_gives_422(client, pair):
    position, rate_class = pair
    old_id = client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class)
    ).json()["id"]

    response = client.post(
        f"/api/v1/rate-standards/{old_id}/reapprove", json={"valid_from": "2026-01-01"}
    )
    assert response.status_code == 422
    assert "взять неоткуда" in response.json()["detail"]


def test_reapprove_not_after_old_start_gives_422(client, pair):
    """Иначе прежний период стал бы пустым, и его история потерялась бы."""
    position, rate_class = pair
    old_id = client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class)
    ).json()["id"]

    response = client.post(
        f"/api/v1/rate-standards/{old_id}/reapprove",
        json={"valid_from": "2025-01-01", "standard_unit_rate": "1"},
    )
    assert response.status_code == 422
    assert "должен начинаться позже" in response.json()["detail"]


def test_reapprove_of_already_closed_standard_gives_422(client, pair):
    position, rate_class = pair
    old_id = client.post(
        "/api/v1/rate-standards",
        json=_payload(position, rate_class, valid_to="2025-07-01"),
    ).json()["id"]

    response = client.post(
        f"/api/v1/rate-standards/{old_id}/reapprove",
        json={"valid_from": "2026-01-01", "standard_unit_rate": "1"},
    )
    assert response.status_code == 422
    assert "уже закрыт" in response.json()["detail"]


def test_reapprove_from_index_only_creates_exactly_two_rows(client, pair, db_session):
    """Путь «только коэффициент» доходит до БД и оставляет ровно две строки.

    Тест НЕ доказывает необходимость двух flush-ов в `reapprove`: замером
    показано, что SQLAlchemy и в одном flush делает `UPDATE` раньше `INSERT`, и
    снятие этой границы тест не роняет. Граница оставлена защитной мерой — см.
    комментарий в `crud/rate_standards.py`.
    """
    position, rate_class = pair
    old_id = client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class)
    ).json()["id"]

    response = client.post(
        f"/api/v1/rate-standards/{old_id}/reapprove",
        json={"valid_from": "2026-01-01", "inflation_index": "1.07"},
    )
    assert response.status_code == 200, response.text
    assert db_session.execute(
        sa.select(sa.func.count()).select_from(RateStandard)
    ).scalar_one() == 2


def test_reapproval_does_not_change_deviation_of_an_older_estimate(
    client, factories, db_session
):
    """DoD: «переутверждение норматива не меняет отклонения старых смет».

    Проверяется через сам VIEW: он выбирает норматив по дате сметы, а
    переутверждение оставляет прежнюю ставку на её периоде. Здесь важно, что
    состояние создано **эндпоинтом**, а не фабрикой: тест на VIEW из фазы 2
    доказывает семантику выборки, а этот — что переутверждение создаёт ровно то
    состояние, которого VIEW ждёт.
    """
    contract = factories.ContractFactory.create()
    position = factories.CatalogPositionFactory.create()
    old_estimate = factories.EstimateFactory.create(
        contract=contract, amendment_no=None, data_prepared_on_date=dt.date(2025, 4, 1)
    )
    old_item = factories.PositionItemFactory.create(
        proposal=factories.ProposalFactory.create(
            lot=factories.LotFactory.create(estimate=old_estimate),
            contractor=contract.contractor,
        ),
        catalog_position=position,
        unit_cost_total=Decimal("120.00"),
    )
    new_estimate = factories.EstimateFactory.create(
        contract=contract, amendment_no=1, data_prepared_on_date=dt.date(2026, 4, 1)
    )
    new_item = factories.PositionItemFactory.create(
        proposal=factories.ProposalFactory.create(
            lot=factories.LotFactory.create(estimate=new_estimate),
            contractor=contract.contractor,
        ),
        catalog_position=position,
        unit_cost_total=Decimal("120.00"),
    )
    db_session.flush()

    standard_id = client.post(
        "/api/v1/rate-standards",
        json={
            "catalog_position_id": position.id,
            "rate_class_id": contract.rate_class_id,
            "standard_unit_rate": "100.00",
            "valid_from": "2025-01-01",
        },
    ).json()["id"]

    assert _deviation(db_session, old_item.id)["deviation_pct"] == Decimal("20")

    # Переутверждение с 2026-01-01: ставка выросла до 150.
    assert client.post(
        f"/api/v1/rate-standards/{standard_id}/reapprove",
        json={"valid_from": "2026-01-01", "standard_unit_rate": "150.00"},
    ).status_code == 200

    db_session.expire_all()
    # Старая смета сравнивается со старой ставкой — отклонение НЕ изменилось.
    assert _deviation(db_session, old_item.id)["standard_unit_rate"] == Decimal("100.00")
    assert _deviation(db_session, old_item.id)["deviation_pct"] == Decimal("20")
    # Новая смета — с новой ставкой: 120/150 - 1 = -20%.
    assert _deviation(db_session, new_item.id)["standard_unit_rate"] == Decimal("150.00")
    assert _deviation(db_session, new_item.id)["deviation_pct"] == Decimal("-20")


# ---------------------------------------------------------------------------
#  Правка и удаление
# ---------------------------------------------------------------------------

def test_patch_fixes_a_typo_without_touching_the_pair(client, pair):
    position, rate_class = pair
    standard_id = client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class)
    ).json()["id"]

    body = client.patch(
        f"/api/v1/rate-standards/{standard_id}", json={"standard_unit_rate": "1001.00"}
    ).json()
    assert body["standard_unit_rate"] == "1001.00"
    assert body["catalog_position_id"] == position.id
    assert body["rate_class_id"] == rate_class.id


def test_patch_into_overlapping_period_gives_400(client, pair):
    position, rate_class = pair
    first = client.post(
        "/api/v1/rate-standards",
        json=_payload(position, rate_class, valid_from="2025-01-01", valid_to="2026-01-01"),
    ).json()["id"]
    client.post("/api/v1/rate-standards", json=_payload(position, rate_class, valid_from="2026-01-01"))

    response = client.patch(f"/api/v1/rate-standards/{first}", json={"valid_to": "2026-06-01"})
    assert response.status_code == 400
    assert "пересекается" in response.json()["detail"]


def test_delete_rate_standard(client, pair):
    position, rate_class = pair
    standard_id = client.post(
        "/api/v1/rate-standards", json=_payload(position, rate_class)
    ).json()["id"]
    assert client.delete(f"/api/v1/rate-standards/{standard_id}").status_code == 204
    assert client.get(f"/api/v1/rate-standards/{standard_id}").status_code == 404


# ---------------------------------------------------------------------------
#  Права: нормативы за admin по букве §3
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/v1/rate-standards", {"catalog_position_id": 1, "rate_class_id": 1,
                                            "standard_unit_rate": "1", "valid_from": "2025-01-01"}),
        ("patch", "/api/v1/rate-standards/1", {"standard_unit_rate": "1"}),
        ("post", "/api/v1/rate-standards/1/reapprove", {"valid_from": "2026-01-01",
                                                        "standard_unit_rate": "1"}),
        ("delete", "/api/v1/rate-standards/1", None),
    ],
)
def test_member_cannot_change_rate_standards(member, method, path, payload):
    response = getattr(member, method)(path, **({"json": payload} if payload else {}))
    assert response.status_code == 403


def test_member_can_read_rate_standards(member):
    assert member.get("/api/v1/rate-standards").status_code == 200
