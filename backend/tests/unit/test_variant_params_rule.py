"""Правило «при подборе» параметров варианта (спека параметров варианта §2.3)."""

from __future__ import annotations

import itertools

import pytest

from services.analog_ladder import FamilyLadderInfo, NearestParam, _form, _values_key, params_rule

# Главные: 10 — неотпускаемый, 11 и 12 — отпускаемые. Схема семьи — 7.
INFO = FamilyLadderInfo(
    schema_id=7,
    main_parameter_ids=(10, 11, 12),
    releasable_ids=frozenset({11, 12}),
    names={10: "A", 11: "B", 12: "C"},
)
INFO_NO_RELEASABLE = FamilyLadderInfo(
    schema_id=7,
    main_parameter_ids=(10, 11, 12),
    releasable_ids=frozenset(),
    names={10: "A", 11: "B", 12: "C"},
)


def rule(**over):
    args = dict(
        main_ids=(10, 11, 12),
        has_variant=True,
        is_system=False,
        schema_id=7,
        forms={},
        conflicted=frozenset(),
        info=INFO,
        work_rung=None,
        system_level=None,
    )
    args.update(over)
    return params_rule(**args)


@pytest.mark.parametrize("rung", [1, 3])
def test_variant_params_work_rung_1_3_all_required(rung):
    r = rule(work_rung=rung, forms={11: 501})
    assert r.basis == "rung"
    assert dict(r.main) == {10: "required", 11: "required", 12: "required"}
    assert r.secondary == "ignored"


@pytest.mark.parametrize("rung", [2, 4])
def test_variant_params_work_rung_2_4_by_roles(rung):
    r = rule(work_rung=rung, forms={11: 501})
    assert r.basis == "rung"
    assert dict(r.main) == {10: "required", 11: "same_set", 12: "ignored"}
    assert r.secondary == "ignored"


def test_variant_params_rung_2_conflicted_composite_is_ignored():
    r = rule(work_rung=2, forms={11: 501, 12: 502}, conflicted=frozenset({11}))
    assert dict(r.main) == {10: "required", 11: "ignored", 12: "same_set"}


def test_variant_params_rung_2_conflict_on_required_stays_required():
    r = rule(work_rung=2, conflicted=frozenset({10}))
    assert r.main[10] == "required"


def test_variant_params_rung_2_main_unknown_to_info_is_required():
    r = rule(work_rung=2, main_ids=(10, 99), forms={99: 501})
    assert dict(r.main) == {10: "required", 99: "required"}


def test_variant_params_rung_2_without_info_all_required():
    r = rule(work_rung=2, info=None)
    assert r.basis == "rung"
    assert set(r.main.values()) == {"required"}


def test_variant_params_catalog_current_schema_with_releasable():
    r = rule(forms={11: 501})
    assert r.basis == "catalog"
    assert dict(r.main) == {10: "required", 11: "same_set", 12: "ignored"}
    assert r.secondary == "ignored"


ALL_REQUIRED = {10: "required", 11: "required", 12: "required"}


def test_variant_params_catalog_old_schema_all_required():
    r = rule(schema_id=6, forms={11: 501})
    assert r.basis == "catalog"
    assert dict(r.main) == ALL_REQUIRED
    assert r.secondary == "ignored"


def test_variant_params_catalog_family_without_releasable_all_required():
    r = rule(info=INFO_NO_RELEASABLE, forms={11: 501})
    assert r.basis == "catalog"
    assert dict(r.main) == ALL_REQUIRED
    assert r.secondary == "ignored"


def test_variant_params_catalog_without_info_all_required():
    r = rule(info=None)
    assert r.basis == "catalog"
    assert dict(r.main) == ALL_REQUIRED
    assert r.secondary == "ignored"


def test_variant_params_catalog_rung_is_not_basis_rung_on_current_schema():
    assert rule().basis != "rung"
    assert rule(work_rung=2).basis == "rung"


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("v", {10: "required", 11: "required", 12: "required"}),
        ("p", {10: "required", 11: "ignored", 12: "ignored"}),
        ("f", {10: "ignored", 11: "ignored", 12: "ignored"}),
    ],
)
def test_variant_params_system_levels(level, expected):
    r = rule(is_system=True, system_level=level, info=None)
    assert r.basis == "rung"
    assert dict(r.main) == expected
    assert r.secondary == "ignored"


def test_variant_params_system_p_first_main_by_order_of_main_ids():
    r = rule(is_system=True, system_level="p", main_ids=(12, 10, 11))
    assert dict(r.main) == {12: "required", 10: "ignored", 11: "ignored"}
    assert list(r.main) == [12, 10, 11]


@pytest.mark.parametrize(
    ("level", "expected"),
    [("v", {20: "required", 21: "required"}), ("f", {20: "ignored", 21: "ignored"})],
)
def test_variant_params_system_on_old_schema(level, expected):
    """Вариант системы на прежней схеме (главные 20, 21 — не схемы 7 семьи): правило уровня то же."""
    r = rule(is_system=True, system_level=level, schema_id=6, main_ids=(20, 21), forms={})
    assert r.basis == "rung"
    assert dict(r.main) == expected
    assert r.secondary == "ignored"


def test_variant_params_system_without_level_has_no_basis():
    r = rule(is_system=True, system_level=None)
    assert r.basis is None
    assert dict(r.main) == {}
    assert r.secondary is None


def test_variant_params_system_ignores_work_rung():
    r = rule(is_system=True, system_level="f", work_rung=1)
    assert set(r.main.values()) == {"ignored"}


@pytest.mark.parametrize("is_system", [False, True])
def test_variant_params_no_variant_has_no_basis(is_system):
    r = rule(
        has_variant=False,
        is_system=is_system,
        work_rung=2,
        system_level="v",
        forms={11: 501},
    )
    assert r.basis is None
    assert dict(r.main) == {}
    assert r.secondary is None


# Роли главного на ступени 2: (отпускаем?, составной?, конфликт?)
ROLES = {
    "required": (False, False, False),
    "releasable_simple": (True, False, False),
    "releasable_composite": (True, True, False),
    "releasable_composite_conflicted": (True, True, True),
}


@pytest.mark.parametrize("role_names", list(itertools.product(ROLES, repeat=3)))
def test_variant_params_agrees_with_values_key(role_names):
    ids = (10, 11, 12)
    releasable = frozenset(p for p, n in zip(ids, role_names, strict=True) if ROLES[n][0])
    info = FamilyLadderInfo(schema_id=7, main_parameter_ids=ids, releasable_ids=releasable, names={})
    forms = {p: 500 + p for p, n in zip(ids, role_names, strict=True) if ROLES[n][1]}
    conflicted = frozenset(p for p, n in zip(ids, role_names, strict=True) if ROLES[n][2])
    r = params_rule(
        main_ids=ids,
        has_variant=True,
        is_system=False,
        schema_id=7,
        forms=forms,
        conflicted=conflicted,
        info=info,
        work_rung=2,
        system_level=None,
    )
    required = [p for p in ids if p not in releasable]
    rel = [p for p in ids if p in releasable]

    # Два набора: A, B равны по ключу; B строим из A подменой того, что ключ не сравнивает.
    values_a = {p: 1 for p in ids}
    forms_a = dict(forms)
    # B: отпускаемые простые — подменяем значение; составным форму сохраняем; неотпускаемым значение сохраняем.
    values_b = {p: (1 if p in required else 2) for p in ids}
    forms_b = dict(forms)
    key_a = _values_key(values_a, forms_a, conflicted, required, rel)
    key_b = _values_key(values_b, forms_b, conflicted, required, rel)
    assert key_a == key_b
    for p in ids:
        if r.main[p] == "required":
            assert values_a[p] == values_b[p]
        if r.main[p] == "same_set":
            assert _form(forms_a, conflicted, p) == _form(forms_b, conflicted, p) != "simple"

    # Обратно: наборы, различающиеся там, что правило требует, дают разные ключи.
    for p in ids:
        if r.main[p] == "required":
            changed = {**values_a, p: 99}
            assert _values_key(changed, forms_a, conflicted, required, rel) != key_a
        if r.main[p] == "same_set":
            changed_forms = {**forms_a, p: 999}
            assert _values_key(values_a, changed_forms, conflicted, required, rel) != key_a
        if r.main[p] == "ignored":
            # «Не учитывается»: подходит строка с другим значением простой формы — сборная
            # строка КП, где лестница сравнивает форму, так не подписывается.
            changed = {**values_a, p: 99}
            simple_forms = {q: f for q, f in forms_a.items() if q != p}
            assert _values_key(changed, simple_forms, conflicted, required, rel) == key_a


# ---------------------------------------------------------------------------
#  Ближайшее и совпавшее (спека ближайшего значения §3 п. 3)
# ---------------------------------------------------------------------------

_NEAREST = {
    11: NearestParam(
        parameter_id=11,
        name="B",
        kp_value="B35",
        values=("B40",),
        kp_contracts=0,
        direction="up",
        without_value=False,
        after=(),
    )
}


@pytest.mark.parametrize("rung", [2, 4])
def test_variant_params_nearest_and_matched_replace_the_rule_on_rung_2_4(rung):
    r = rule(work_rung=rung, forms={}, nearest=_NEAREST, matched=frozenset({12}))
    assert r.basis == "rung"
    assert dict(r.main) == {10: "required", 11: "nearest", 12: "matched"}
    assert r.secondary == "ignored"


@pytest.mark.parametrize("rung", [2, 4])
def test_variant_params_other_releasable_keep_the_old_rule_next_to_nearest(rung):
    r = rule(work_rung=rung, forms={12: 502}, nearest=_NEAREST)
    assert dict(r.main) == {10: "required", 11: "nearest", 12: "same_set"}


@pytest.mark.parametrize("rung", [1, 3, None])
def test_variant_params_nearest_does_not_apply_to_rungs_1_3_or_catalog(rung):
    plain = rule(work_rung=rung)
    with_new = rule(work_rung=rung, nearest=_NEAREST, matched=frozenset({12}))
    assert with_new == plain


@pytest.mark.parametrize("level", ["v", "p", "f"])
def test_variant_params_nearest_does_not_apply_to_systems(level):
    plain = rule(is_system=True, system_level=level, work_rung=2)
    assert rule(is_system=True, system_level=level, work_rung=2, nearest=_NEAREST, matched=frozenset({12})) == plain


def test_variant_params_nearest_does_not_change_the_catalog_rule_of_a_scale():
    plain = rule(forms={})
    assert dict(plain.main) == {10: "required", 11: "ignored", 12: "ignored"}
    assert rule(nearest=_NEAREST, matched=frozenset({12})) == plain


def test_variant_params_without_new_arguments_matches_the_old_result():
    assert rule(work_rung=2, forms={11: 501}) == rule(
        work_rung=2, forms={11: 501}, nearest={}, matched=frozenset()
    )
