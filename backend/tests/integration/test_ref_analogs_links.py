"""Связи семей: сервис и маршруты (спека справочных аналогов §2.6, §2.16, §2.19).

Маршруты — через `admin_client`/`member_client`; состояние семей меняют боевые
`set_unit`, `merge_families`, `archive_family`. Эталоны — независимые литералы.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from models import FamilyLink, KpLineTarget
from services.work_families import archive_family, merge_families, set_unit
from tests.integration.test_analog_schema import _target
from tests.integration.test_work_variants_schema import (
    _family,
    _now,
    _param,
    _schema,
    _uid,
)

pytestmark = pytest.mark.integration

BASE = "/api/v1/semantic"


# ---------------------------------------------------------------------------
#  Помощники
# ---------------------------------------------------------------------------

def _unit(db, symbol: str) -> int:
    return db.execute(
        sa.text("SELECT id FROM units_of_measure WHERE symbol = :s"), {"s": symbol}
    ).scalar_one()


def _fam(db, symbol: str | None, *, status: str = "active"):
    """Подготовка закоммичена: откат отказавшего запроса не стирает сцену теста."""
    family = _family(
        db, title=f"Семья {_uid()}", status=status, definition="Определение",
        unit_id=None if symbol is None else _unit(db, symbol),
    )
    db.commit()
    return family


def _fam_with_param(db, factories, symbol: str | None, **param_overrides):
    family = _fam(db, symbol)
    schema = _schema(db, factories, family)
    param = _param(db, schema, name=f"Параметр {_uid()}")
    for key, value in param_overrides.items():
        setattr(param, key, value)
    db.commit()
    return family, schema, param


def _rebuild(db, factories, family, old_schema):
    """Версия схемы пересобрана: прежняя замещена, действующая — новая."""
    old_schema.status = "superseded"
    old_schema.superseded_at = _now()
    db.flush()
    return _schema(db, factories, family, version=old_schema.version + 1)


def _ordered(first, second):
    """Пара семей по возрастанию id: (lo, hi)."""
    return tuple(sorted((first, second), key=lambda f: f.id))


def _thickness(a, b, pa, pb, **overrides) -> dict:
    body = {
        "family_a_id": a.id, "family_b_id": b.id, "kind": "thickness",
        "param_a_id": None if pa is None else pa.id,
        "param_b_id": None if pb is None else pb.id,
        "factor_from_family_id": None, "factor": None, "basis": None,
    }
    body.update(overrides)
    return body


def _constant(a, b, *, from_family=None, factor="0.1", basis="основание", **overrides) -> dict:
    body = {
        "family_a_id": a.id, "family_b_id": b.id, "kind": "constant",
        "param_a_id": None, "param_b_id": None,
        "factor_from_family_id": (from_family or a).id, "factor": factor, "basis": basis,
    }
    body.update(overrides)
    return body


def _post(client, body):
    return client.post(f"{BASE}/family-links", json=body)


def _links_in_db(db) -> list[FamilyLink]:
    return list(db.execute(sa.select(FamilyLink).order_by(FamilyLink.id)).scalars())


def _row(db, link_id) -> FamilyLink:
    db.expire_all()
    return db.execute(sa.select(FamilyLink).where(FamilyLink.id == link_id)).scalar_one()


def _links_of(client, family):
    response = client.get(f"{BASE}/families/{family.id}/links")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _created(client, body) -> int:
    response = _post(client, body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _refused(response, status: int, code: str, reason: str | None = None) -> None:
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    if reason is not None:
        assert detail["reason"] == reason


# ---------------------------------------------------------------------------
#  Создание: порядок пары, единицы, хранение как пришло
# ---------------------------------------------------------------------------

class TestCreate:
    def test_a_pair_given_in_descending_order_is_stored_ascending_with_its_parameters(
        self, admin_client, db_session, factories
    ):
        first, _, first_param = _fam_with_param(db_session, factories, "м²")
        second, _, second_param = _fam_with_param(db_session, factories, "м³")
        (lo, lo_param), (hi, hi_param) = sorted(
            [(first, first_param), (second, second_param)], key=lambda p: p[0].id
        )
        # a — старший по id, b — младший
        link_id = _created(admin_client, _thickness(hi, lo, hi_param, lo_param))
        row = _row(db_session, link_id)
        assert (row.family_lo_id, row.family_hi_id) == (lo.id, hi.id)
        assert (row.param_lo_id, row.param_hi_id) == (lo_param.id, hi_param.id)
        assert row.kind == "thickness"
        assert (row.factor, row.factor_from_id, row.basis) == (None, None, None)

    def test_the_units_of_both_sides_are_remembered(self, admin_client, db_session, factories):
        square, _, sq_param = _fam_with_param(db_session, factories, "м²")
        cube, _, cu_param = _fam_with_param(db_session, factories, "м³")
        link_id = _created(admin_client, _thickness(cube, square, cu_param, sq_param))
        row = _row(db_session, link_id)
        units = {row.family_lo_id: row.unit_lo_id, row.family_hi_id: row.unit_hi_id}
        assert units == {square.id: _unit(db_session, "м²"), cube.id: _unit(db_session, "м³")}

    def test_the_author_comes_from_the_current_user(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        row = _row(db_session, _created(admin_client, _thickness(a, b, pa, pb)))
        assert row.created_by == row.updated_by == admin_client.user.id

    def test_one_parameter_is_enough_for_a_parameter_kind(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b = _fam(db_session, "м³")
        row = _row(db_session, _created(admin_client, _thickness(a, b, pa, None)))
        assert {row.param_lo_id, row.param_hi_id} == {pa.id, None}

    @pytest.mark.parametrize(
        ("kind", "units"),
        [("thickness", ("м²", "м³")), ("width", ("м", "м²")), ("density", ("м³", "т"))],
    )
    def test_every_parameter_kind_takes_its_unit_pair_in_either_order(
        self, admin_client, db_session, factories, kind, units
    ):
        a, _, pa = _fam_with_param(db_session, factories, units[0])
        b, _, pb = _fam_with_param(db_session, factories, units[1])
        assert _post(admin_client, _thickness(a, b, pa, pb, kind=kind)).status_code == 201
        c, _, pc = _fam_with_param(db_session, factories, units[1])
        d, _, pd = _fam_with_param(db_session, factories, units[0])
        assert _post(admin_client, _thickness(c, d, pc, pd, kind=kind)).status_code == 201

    def test_a_decimal_factor_is_stored_with_the_digits_it_came_with(
        self, admin_client, db_session, factories
    ):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        row = _row(db_session, _created(admin_client, _constant(a, b, factor="0.10")))
        assert row.factor == Decimal("0.10")
        assert str(row.factor) == "0.10"
        assert _links_of(admin_client, a)[0]["factor"] == "0.10"

    def test_a_json_number_is_not_a_factor(self, admin_client, db_session, factories):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        response = _post(admin_client, _constant(a, b, factor=0.1))
        assert response.status_code == 422
        assert _links_in_db(db_session) == []

    def test_the_second_link_of_a_pair_is_409_in_either_order(
        self, admin_client, db_session, factories
    ):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        _created(admin_client, _constant(a, b))
        _refused(_post(admin_client, _constant(a, b, factor="3")), 409, "link_exists")
        _refused(_post(admin_client, _constant(b, a, factor="3")), 409, "link_exists")
        assert len(_links_in_db(db_session)) == 1

    def test_a_family_that_does_not_exist_is_404(self, admin_client, db_session, factories):
        a = _fam(db_session, "м²")
        body = _constant(a, a, factor_from_family_id=a.id)
        body["family_b_id"] = 987654321
        _refused(_post(admin_client, body), 404, "family_not_found")


# ---------------------------------------------------------------------------
#  Направление множителя не обращается (К8)
# ---------------------------------------------------------------------------

class TestFactorDirection:
    @pytest.mark.parametrize("a_is_lower", [True, False])
    def test_a_constant_created_from_card_a_is_read_from_card_b_without_inversion(
        self, admin_client, db_session, factories, a_is_lower
    ):
        first, second = _fam(db_session, "м²"), _fam(db_session, "м³")
        a, b = (first, second) if a_is_lower else (second, first)
        # 1 [ед. a] = 0.1 [ед. b], левая часть — семья a
        link_id = _created(admin_client, _constant(a, b, from_family=a, factor="0.1"))

        (from_b,) = _links_of(admin_client, b)
        assert from_b["id"] == link_id
        assert from_b["factor"] == "0.1"
        assert from_b["factor_from_family_id"] == a.id
        assert from_b["shown"] == {"from_family_id": b.id, "factor": "10"}

        (from_a,) = _links_of(admin_client, a)
        assert from_a["factor"] == "0.1"
        assert from_a["factor_from_family_id"] == a.id
        assert from_a["shown"] == {"from_family_id": a.id, "factor": "0,1"}

    def test_a_shown_factor_that_is_rounded_carries_the_approximation_sign(
        self, admin_client, db_session, factories
    ):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        _created(admin_client, _constant(a, b, from_family=a, factor="0.667"))
        (from_b,) = _links_of(admin_client, b)
        assert from_b["factor"] == "0.667"
        assert from_b["shown"]["factor"] == "≈1,4993"
        (from_a,) = _links_of(admin_client, a)
        assert from_a["shown"]["factor"] == "0,667"

    @pytest.mark.parametrize("from_card", ["a", "b"])
    @pytest.mark.parametrize("swap_sides", [False, True])
    def test_a_put_with_the_stored_fields_keeps_factor_and_direction_to_the_digit(
        self, admin_client, db_session, factories, from_card, swap_sides
    ):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        link_id = _created(admin_client, _constant(a, b, from_family=a, factor="0.667"))
        card, other = (a, b) if from_card == "a" else (b, a)
        (item,) = _links_of(admin_client, card)
        # форма правки берёт хранимые поля, не `shown`
        first, second = (other, card) if swap_sides else (card, other)
        body = _constant(
            first, second, factor=item["factor"],
            factor_from_family_id=item["factor_from_family_id"], basis="основание",
        )
        response = admin_client.put(f"{BASE}/family-links/{link_id}", json=body)
        assert response.status_code == 200, response.text
        row = _row(db_session, link_id)
        assert str(row.factor) == "0.667"
        assert row.factor_from_id == a.id


# ---------------------------------------------------------------------------
#  Отказы записи: 422 link_invalid с причиной, ничего не записано
# ---------------------------------------------------------------------------

class TestRefusals:
    def _refused_without_rows(self, admin_client, db_session, body, reason):
        _refused(_post(admin_client, body), 422, "link_invalid", reason)
        assert _links_in_db(db_session) == []

    def test_the_same_family_on_both_sides(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        self._refused_without_rows(admin_client, db_session, _thickness(a, a, pa, pa), "same_family")

    @pytest.mark.parametrize("status", ["draft", "archived"])
    def test_a_family_that_is_not_active(self, admin_client, db_session, factories, status):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        b.status = status
        if status == "archived":
            b.archived_at = _now()
        db_session.commit()
        self._refused_without_rows(
            admin_client, db_session, _thickness(a, b, pa, pb), "family_inactive"
        )

    @pytest.mark.parametrize(
        ("kind", "units"),
        [
            ("thickness", ("м²", "м")),
            ("thickness", ("м²", "м²")),
            ("width", ("м", "т")),
            ("density", ("м³", "м²")),
        ],
    )
    def test_units_that_do_not_fit_the_kind(
        self, admin_client, db_session, factories, kind, units
    ):
        a, _, pa = _fam_with_param(db_session, factories, units[0])
        b, _, pb = _fam_with_param(db_session, factories, units[1])
        self._refused_without_rows(
            admin_client, db_session, _thickness(a, b, pa, pb, kind=kind), "units_mismatch"
        )

    def test_a_constant_on_equal_units(self, admin_client, db_session, factories):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м²")
        self._refused_without_rows(admin_client, db_session, _constant(a, b), "units_mismatch")

    def test_a_constant_on_a_family_without_a_unit(self, admin_client, db_session, factories):
        a, b = _fam(db_session, None), _fam(db_session, "м³")
        self._refused_without_rows(admin_client, db_session, _constant(a, b), "units_mismatch")

    def test_a_parameter_kind_on_a_family_without_a_unit(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, None)
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        self._refused_without_rows(
            admin_client, db_session, _thickness(a, b, pa, pb), "units_mismatch"
        )

    def test_a_parameter_of_another_family(self, admin_client, db_session, factories):
        a, _, _ = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        _, _, stranger = _fam_with_param(db_session, factories, "м²")
        self._refused_without_rows(
            admin_client, db_session, _thickness(a, b, stranger, pb), "param_invalid"
        )

    def test_an_archived_parameter(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²", archived_at=_now())
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        self._refused_without_rows(
            admin_client, db_session, _thickness(a, b, pa, pb), "param_invalid"
        )

    def test_a_parameter_of_a_replaced_schema_version(self, admin_client, db_session, factories):
        a, schema_a, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        _rebuild(db_session, factories, a, schema_a)
        self._refused_without_rows(
            admin_client, db_session, _thickness(a, b, pa, pb), "param_invalid"
        )

    def test_a_range_parameter(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(
            db_session, factories, "м²", value_kind="range", closed=True,
            range_from=Decimal("0"), range_to=Decimal("10"), range_step=Decimal("1"),
        )
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        self._refused_without_rows(
            admin_client, db_session, _thickness(a, b, pa, pb), "param_invalid"
        )

    def test_a_parameter_kind_without_parameters(self, admin_client, db_session, factories):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        self._refused_without_rows(
            admin_client, db_session, _thickness(a, b, None, None), "params_missing"
        )

    @pytest.mark.parametrize("factor", ["0", "-1", "abc", "NaN", "Infinity", "1e5", "", "0,5"])
    def test_a_constant_with_a_factor_that_is_not_positive_plain_decimal(
        self, admin_client, db_session, factories, factor
    ):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        self._refused_without_rows(
            admin_client, db_session, _constant(a, b, factor=factor), "constant_invalid"
        )

    def test_a_constant_without_a_factor(self, admin_client, db_session, factories):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        self._refused_without_rows(
            admin_client, db_session, _constant(a, b, factor=None), "constant_invalid"
        )

    @pytest.mark.parametrize("basis", ["", "   ", None, "я" * 301])
    def test_a_constant_with_a_basis_that_is_empty_or_too_long(
        self, admin_client, db_session, factories, basis
    ):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        self._refused_without_rows(
            admin_client, db_session, _constant(a, b, basis=basis), "constant_invalid"
        )

    def test_a_basis_of_exactly_three_hundred_characters_is_accepted(
        self, admin_client, db_session, factories
    ):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        assert _post(admin_client, _constant(a, b, basis="я" * 300)).status_code == 201

    def test_a_factor_from_outside_the_pair(self, admin_client, db_session, factories):
        a, b, c = _fam(db_session, "м²"), _fam(db_session, "м³"), _fam(db_session, "т")
        self._refused_without_rows(
            admin_client, db_session, _constant(a, b, factor_from_family_id=c.id),
            "constant_invalid",
        )

    def test_a_constant_without_a_direction(self, admin_client, db_session, factories):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        self._refused_without_rows(
            admin_client, db_session, _constant(a, b, factor_from_family_id=None),
            "constant_invalid",
        )

    def test_a_parameter_on_a_constant(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b = _fam(db_session, "м³")
        self._refused_without_rows(
            admin_client, db_session, _constant(a, b, param_a_id=pa.id), "constant_invalid"
        )

    def test_a_factor_on_a_parameter_kind(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        self._refused_without_rows(
            admin_client, db_session, _thickness(a, b, pa, pb, factor="0.1"), "constant_invalid"
        )

    def test_the_first_violation_in_the_fixed_order_wins(self, admin_client, db_session, factories):
        """Семья не активна И единицы не подходят: сначала `family_inactive`."""
        a, b = _fam(db_session, "м²"), _fam(db_session, "м²", status="draft")
        self._refused_without_rows(
            admin_client, db_session, _constant(a, b, factor="0"), "family_inactive"
        )


# ---------------------------------------------------------------------------
#  PUT и DELETE
# ---------------------------------------------------------------------------

class TestUpdateAndDelete:
    def test_a_put_with_another_pair_is_422(self, admin_client, db_session, factories):
        a, b, c = _fam(db_session, "м²"), _fam(db_session, "м³"), _fam(db_session, "м³")
        link_id = _created(admin_client, _constant(a, b))
        response = admin_client.put(f"{BASE}/family-links/{link_id}", json=_constant(a, c))
        _refused(response, 422, "link_invalid", "pair_changed")
        row = _row(db_session, link_id)
        assert {row.family_lo_id, row.family_hi_id} == {a.id, b.id}

    def test_a_put_of_a_missing_link_is_404(self, admin_client, db_session, factories):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        response = admin_client.put(f"{BASE}/family-links/987654321", json=_constant(a, b))
        _refused(response, 404, "link_not_found")

    def test_a_delete_of_a_missing_link_is_404(self, admin_client):
        _refused(admin_client.delete(f"{BASE}/family-links/987654321"), 404, "link_not_found")

    def test_a_put_changes_the_kind_and_the_parameters(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        link_id = _created(admin_client, _constant(a, b, factor="0.2"))
        response = admin_client.put(
            f"{BASE}/family-links/{link_id}", json=_thickness(b, a, pb, pa)
        )
        assert response.status_code == 200, response.text
        row = _row(db_session, link_id)
        assert row.kind == "thickness"
        assert (row.factor, row.factor_from_id, row.basis) == (None, None, None)
        assert {row.param_lo_id, row.param_hi_id} == {pa.id, pb.id}
        assert row.updated_by == admin_client.user.id

    def test_a_put_is_refused_by_the_same_rules_as_a_post(self, admin_client, db_session, factories):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        link_id = _created(admin_client, _constant(a, b, factor="0.2"))
        response = admin_client.put(
            f"{BASE}/family-links/{link_id}", json=_constant(a, b, factor="0")
        )
        _refused(response, 422, "link_invalid", "constant_invalid")
        assert str(_row(db_session, link_id).factor) == "0.2"

    def test_a_delete_removes_the_link_and_detaches_the_targets(
        self, admin_client, db_session, factories
    ):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        link_id = _created(admin_client, _constant(a, b))
        target = _target(
            db_session, factories, kind="reference",
            analog_position_item_id=factories.PositionItemFactory.create().id,
            ref_origin="link", ref_without_analogs=False, ref_method="link",
            ref_factor=Decimal("2"), ref_basis="основание", ref_link_id=link_id,
        )
        db_session.commit()
        response = admin_client.delete(f"{BASE}/family-links/{link_id}")
        assert response.status_code == 204
        assert _links_in_db(db_session) == []
        db_session.expire_all()
        kept = db_session.get(KpLineTarget, target.position_item_id)
        assert kept.ref_link_id is None
        assert (kept.ref_method, kept.ref_factor, kept.ref_basis) == ("link", Decimal("2"), "основание")


# ---------------------------------------------------------------------------
#  Права (К7)
# ---------------------------------------------------------------------------

class TestRights:
    def test_a_member_reads_but_cannot_write(self, member_client, db_session, factories):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        lo, hi = _ordered(a, b)
        author = factories.UserFactory.create()
        row = FamilyLink(
            family_lo_id=lo.id, family_hi_id=hi.id, unit_lo_id=lo.unit_id, unit_hi_id=hi.unit_id,
            kind="constant", factor=Decimal("0.1"), factor_from_id=lo.id, basis="основание",
            created_by=author.id, updated_by=author.id,
        )
        db_session.add(row)
        db_session.commit()
        link_id = row.id
        assert member_client.get(f"{BASE}/families/{a.id}/links").status_code == 200
        assert _post(member_client, _constant(b, a, factor="2")).status_code == 403
        put = member_client.put(f"{BASE}/family-links/{link_id}", json=_constant(a, b, factor="2"))
        assert put.status_code == 403
        assert member_client.delete(f"{BASE}/family-links/{link_id}").status_code == 403
        assert str(_row(db_session, link_id).factor) == "0.1"

    def test_a_member_cannot_create(self, member_client, db_session, factories):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        assert _post(member_client, _constant(a, b)).status_code == 403
        assert _links_in_db(db_session) == []


# ---------------------------------------------------------------------------
#  Состояние связи при чтении
# ---------------------------------------------------------------------------

class TestState:
    def test_a_fresh_link_is_active_on_both_cards(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        _created(admin_client, _thickness(a, b, pa, pb))
        for card in (a, b):
            (item,) = _links_of(admin_client, card)
            assert item["state"] == "active"
            assert item["inactive_reason"] is None
            assert item["stale_sides"] == []

    def test_a_merged_family_makes_the_link_inactive(self, admin_client, db_session, factories):
        user = factories.UserFactory.create()
        source, target = _fam(db_session, "м²"), _fam(db_session, "м²")
        other = _fam(db_session, "м³")
        _created(admin_client, _constant(source, other))
        merge_families(
            db_session, source_family_id=source.id, target_family_id=target.id, actor_id=user.id
        )
        db_session.commit()
        (item,) = _links_of(admin_client, other)
        assert (item["state"], item["inactive_reason"]) == ("inactive", "family_inactive")

    def test_an_archived_family_makes_the_link_inactive_and_the_row_stays(
        self, admin_client, db_session, factories
    ):
        user = factories.UserFactory.create()
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        _created(admin_client, _constant(a, b))
        archive_family(db_session, family_id=a.id, actor_id=user.id)
        db_session.commit()
        (item,) = _links_of(admin_client, b)
        assert (item["state"], item["inactive_reason"]) == ("inactive", "family_inactive")
        assert len(_links_in_db(db_session)) == 1

    def test_a_changed_unit_makes_the_link_inactive_and_a_put_restores_it(
        self, admin_client, db_session, factories
    ):
        user = factories.UserFactory.create()
        tonnes, cubes = _fam(db_session, "т"), _fam(db_session, "м³")
        link_id = _created(admin_client, _constant(tonnes, cubes, factor="0.667"))
        (item,) = _links_of(admin_client, cubes)
        assert item["state"] == "active"

        set_unit(db_session, family_id=tonnes.id, unit_name="кг", actor_id=user.id)
        db_session.commit()
        (item,) = _links_of(admin_client, cubes)
        assert (item["state"], item["inactive_reason"]) == ("inactive", "units_changed")

        response = admin_client.put(
            f"{BASE}/family-links/{link_id}", json=_constant(tonnes, cubes, factor="0.667")
        )
        assert response.status_code == 200, response.text
        (item,) = _links_of(admin_client, cubes)
        assert (item["state"], item["inactive_reason"]) == ("active", None)
        row = _row(db_session, link_id)
        remembered = {row.family_lo_id: row.unit_lo_id, row.family_hi_id: row.unit_hi_id}
        assert remembered[tonnes.id] == _unit(db_session, "кг")
        assert str(row.factor) == "0.667"

    def test_a_put_after_a_unit_change_that_does_not_fit_the_kind_stays_inactive(
        self, admin_client, db_session, factories
    ):
        user = factories.UserFactory.create()
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        link_id = _created(admin_client, _thickness(a, b, pa, pb))
        set_unit(db_session, family_id=b.id, unit_name="т", actor_id=user.id)
        db_session.commit()
        response = admin_client.put(f"{BASE}/family-links/{link_id}", json=_thickness(a, b, pa, pb))
        _refused(response, 422, "link_invalid", "units_mismatch")
        (item,) = _links_of(admin_client, a)
        assert item["inactive_reason"] == "units_changed"

    def test_a_rebuilt_schema_leaves_one_live_side_and_the_link_stays_active(
        self, admin_client, db_session, factories
    ):
        a, schema_a, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        _created(admin_client, _thickness(a, b, pa, pb))
        _rebuild(db_session, factories, a, schema_a)
        (from_a,) = _links_of(admin_client, a)
        assert from_a["state"] == "active"
        assert from_a["stale_sides"] == ["this"]
        (from_b,) = _links_of(admin_client, b)
        assert from_b["state"] == "active"
        assert from_b["stale_sides"] == ["other"]

    def test_an_archived_parameter_is_a_stale_side(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        _created(admin_client, _thickness(a, b, pa, pb))
        pb.archived_at = _now()
        db_session.commit()
        (item,) = _links_of(admin_client, a)
        assert item["state"] == "active"
        assert item["stale_sides"] == ["other"]

    def test_both_sides_dead_is_no_live_side(self, admin_client, db_session, factories):
        a, schema_a, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        _created(admin_client, _thickness(a, b, pa, pb))
        _rebuild(db_session, factories, a, schema_a)
        pb.archived_at = _now()
        db_session.commit()
        (item,) = _links_of(admin_client, a)
        assert (item["state"], item["inactive_reason"]) == ("inactive", "no_live_side")
        assert item["stale_sides"] == ["this", "other"]

    def test_a_single_parameter_that_dies_is_no_live_side(self, admin_client, db_session, factories):
        a, schema_a, pa = _fam_with_param(db_session, factories, "м²")
        b = _fam(db_session, "м³")
        _created(admin_client, _thickness(a, b, pa, None))
        _rebuild(db_session, factories, a, schema_a)
        (item,) = _links_of(admin_client, b)
        assert (item["state"], item["inactive_reason"]) == ("inactive", "no_live_side")

    def test_a_constant_has_no_stale_sides(self, admin_client, db_session, factories):
        a, b = _fam(db_session, "м²"), _fam(db_session, "м³")
        _created(admin_client, _constant(a, b))
        (item,) = _links_of(admin_client, a)
        assert (item["state"], item["stale_sides"]) == ("active", [])

    def test_the_service_reports_the_live_flags_per_side(self, admin_client, db_session, factories):
        from services.family_links import links_of_families

        a, schema_a, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        link_id = _created(admin_client, _thickness(a, b, pa, pb))
        _rebuild(db_session, factories, a, schema_a)
        (state,) = links_of_families(db_session, [a.id, b.id])
        assert state.link.id == link_id
        live = {state.link.family_lo_id: state.live_lo, state.link.family_hi_id: state.live_hi}
        assert live == {a.id: False, b.id: True}
        assert (state.active, state.inactive_reason) == (True, None)

    def test_a_link_between_two_asked_families_is_returned_once(
        self, admin_client, db_session, factories
    ):
        from services.family_links import links_of_families

        a, b, c = _fam(db_session, "м²"), _fam(db_session, "м³"), _fam(db_session, "т")
        first = _created(admin_client, _constant(a, b))
        second = _created(admin_client, _constant(b, c, factor="2"))
        ids = [s.link.id for s in links_of_families(db_session, [a.id, b.id])]
        assert ids == [first, second]
        assert [s.link.id for s in links_of_families(db_session, [c.id])] == [second]
        assert links_of_families(db_session, []) == []


# ---------------------------------------------------------------------------
#  Форма чтения
# ---------------------------------------------------------------------------

class TestRead:
    def test_the_view_has_the_listed_fields(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        link_id = _created(admin_client, _thickness(a, b, pa, pb))
        (item,) = _links_of(admin_client, a)
        assert set(item) == {
            "id", "other_family", "kind", "this_param", "other_param",
            "factor_from_family_id", "factor", "basis", "shown", "state", "inactive_reason",
            "stale_sides", "created_by", "created_at", "updated_by", "updated_at",
        }
        assert item["id"] == link_id
        assert item["other_family"]["id"] == b.id
        assert item["other_family"]["title"] == b.title
        assert item["other_family"]["unit"] == "м³"
        assert item["this_param"]["id"] == pa.id
        assert item["other_param"]["id"] == pb.id
        assert item["shown"] is None
        assert item["created_by"] == {"id": admin_client.user.id, "email": admin_client.user.email}
        assert item["updated_by"] == item["created_by"]

    def test_the_params_are_turned_to_the_card(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        _created(admin_client, _thickness(b, a, pb, pa))
        (item,) = _links_of(admin_client, b)
        assert (item["this_param"]["id"], item["other_param"]["id"]) == (pb.id, pa.id)

    def test_a_card_lists_only_its_own_links(self, admin_client, db_session, factories):
        a, b, c, d = (_fam(db_session, s) for s in ("м²", "м³", "т", "м"))
        ab = _created(admin_client, _constant(a, b))
        _created(admin_client, _constant(c, d, factor="3"))
        assert [i["id"] for i in _links_of(admin_client, a)] == [ab]
        assert _links_of(admin_client, _fam(db_session, "кг")) == []

    def test_a_missing_family_is_404(self, admin_client):
        _refused(admin_client.get(f"{BASE}/families/987654321/links"), 404, "family_not_found")

    def test_a_read_writes_nothing(self, admin_client, db_session, factories):
        a, _, pa = _fam_with_param(db_session, factories, "м²")
        b, _, pb = _fam_with_param(db_session, factories, "м³")
        c = _fam(db_session, "т")
        _created(admin_client, _thickness(a, b, pa, pb))
        _created(admin_client, _constant(b, c, factor="0.5"))
        seen: list[str] = []

        def _tick(_conn, _cursor, statement, *_args, **_kwargs):
            seen.append(statement)

        engine = db_session.get_bind()
        sa.event.listen(engine, "before_cursor_execute", _tick)
        try:
            body = _links_of(admin_client, b)
        finally:
            sa.event.remove(engine, "before_cursor_execute", _tick)
        assert len(body) == 2
        assert seen
        writes = [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        assert writes == []

    def test_the_read_does_not_issue_a_query_per_link(self, admin_client, db_session, factories):
        hub = _fam(db_session, "м³")
        for _ in range(6):
            _created(admin_client, _constant(hub, _fam(db_session, "т"), factor="2"))
        seen: list[str] = []

        def _tick(_conn, _cursor, statement, *_args, **_kwargs):
            if not statement.startswith(("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
                seen.append(statement)

        engine = db_session.get_bind()
        sa.event.listen(engine, "before_cursor_execute", _tick)
        try:
            body = _links_of(admin_client, hub)
        finally:
            sa.event.remove(engine, "before_cursor_execute", _tick)
        assert len(body) == 6
        assert len(seen) <= 12
