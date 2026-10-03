import inspect
import unittest

from app.services.auth import invite_service


class InviteRegistrationContractTests(unittest.TestCase):
    def test_role_without_overview_does_not_issue_tokens(self):
        source = inspect.getsource(invite_service.invite_register)
        denied_branch = source.split("if not has_overview_permission:", 1)[1].split("tokens = await issue_tokens", 1)[0]
        self.assertIn('Permission.name == "overview"', source)
        self.assertIn("RolePermission.role_id == role.id", source)
        self.assertIn('"requires_app": True', denied_branch)
        self.assertNotIn("issue_tokens", denied_branch)

    def test_role_with_overview_issues_tokens(self):
        source = inspect.getsource(invite_service.invite_register)
        self.assertIn("tokens = await issue_tokens", source)
        self.assertIn('"requires_app": False', source)


if __name__ == "__main__":
    unittest.main()
