"""Unit-тесты матрицы прав org-самообслуживания (чистые функции, без БД)."""
from crud.admin import can_manage_target, can_set_role
from models import OrgRole


class TestCanSetRole:
    def test_superadmin_can_set_admin_and_member(self):
        assert can_set_role(OrgRole.superadmin, OrgRole.admin)
        assert can_set_role(OrgRole.superadmin, OrgRole.member)

    def test_nobody_can_set_superadmin(self):
        assert not can_set_role(OrgRole.superadmin, OrgRole.superadmin)
        assert not can_set_role(OrgRole.admin, OrgRole.superadmin)

    def test_admin_can_set_only_member(self):
        assert can_set_role(OrgRole.admin, OrgRole.member)
        assert not can_set_role(OrgRole.admin, OrgRole.admin)

    def test_member_can_set_nothing(self):
        assert not can_set_role(OrgRole.member, OrgRole.member)


class TestCanManageTarget:
    def test_superadmin_manages_admin_and_member(self):
        assert can_manage_target(OrgRole.superadmin, OrgRole.admin)
        assert can_manage_target(OrgRole.superadmin, OrgRole.member)

    def test_superadmin_cannot_manage_peer_superadmin(self):
        # Управление другими superadmin'ами — только через /api/admin, не self-service
        assert not can_manage_target(OrgRole.superadmin, OrgRole.superadmin)

    def test_admin_manages_only_member(self):
        assert can_manage_target(OrgRole.admin, OrgRole.member)
        assert not can_manage_target(OrgRole.admin, OrgRole.admin)
        assert not can_manage_target(OrgRole.admin, OrgRole.superadmin)

    def test_member_manages_nobody(self):
        assert not can_manage_target(OrgRole.member, OrgRole.member)
