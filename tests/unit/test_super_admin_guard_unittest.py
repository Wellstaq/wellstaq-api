import unittest
from fastapi import HTTPException
from app.routers.roles import _assert_role_target_mutable


class SuperAdminProtectionTests(unittest.TestCase):
    def test_super_admin_cannot_be_changed_by_another_super_admin(self):
        with self.assertRaises(HTTPException) as error:
            _assert_role_target_mutable(actor_id="actor", actor_role="super_admin", target_id="owner", target_role="super_admin")
        self.assertEqual(error.exception.status_code, 403)

    def test_super_admin_cannot_change_own_role(self):
        with self.assertRaises(HTTPException) as error:
            _assert_role_target_mutable(actor_id="owner", actor_role="super_admin", target_id="owner", target_role="super_admin")
        self.assertEqual(error.exception.status_code, 403)

    def test_branch_manager_cannot_change_super_admin(self):
        with self.assertRaises(HTTPException) as error:
            _assert_role_target_mutable(actor_id="manager", actor_role="branch_manager", target_id="owner", target_role="super_admin")
        self.assertEqual(error.exception.status_code, 403)

    def test_actor_cannot_change_own_non_super_admin_role(self):
        with self.assertRaises(HTTPException) as error:
            _assert_role_target_mutable(actor_id="manager", actor_role="branch_manager", target_id="manager", target_role="branch_manager")
        self.assertEqual(error.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
