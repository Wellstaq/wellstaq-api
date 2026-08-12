import inspect
import unittest

from app.routers import roles
from app.services.auth import token_service
from app.services import permission_service


class TwoRoleSlotContractTests(unittest.TestCase):
    def test_permission_loader_uses_roles_not_direct_user_grants(self):
        source = inspect.getsource(token_service.load_user_permissions)
        self.assertNotIn("join(UserPermission", source)
        self.assertNotIn("Permission.name, UserPermission.branch_id", source)
        self.assertIn("UserRoleAssignment", source)
        self.assertIn("Role.branch_id.is_(None)", source)

    def test_assignment_replaces_the_single_branch_slot(self):
        source = inspect.getsource(roles._assign_role_slot)
        self.assertIn("UserRoleAssignment.branch_id.is_not(None)", source)
        self.assertIn("delete(UserRoleAssignment)", source)
        self.assertIn("delete(UserPermission)", source)
        self.assertIn("UserPermission.branch_id == role.branch_id", source)
        self.assertIn("UserDevice.revoked.is_(False)", source)
        self.assertIn("device.revoked = True", source)
        self.assertIn("target.role_id = role.id", source)

    def test_role_counts_are_scope_specific(self):
        source = inspect.getsource(roles.list_organization_roles)
        self.assertIn("UserRoleAssignment.branch_id == branch_id", source)
        self.assertIn("User.role_id == role.id", source)

    def test_user_permission_view_is_derived_from_roles(self):
        source = inspect.getsource(permission_service.get_user_permissions)
        self.assertIn("load_user_permissions", source)
        self.assertNotIn("select(UserPermission", source)


if __name__ == "__main__":
    unittest.main()
