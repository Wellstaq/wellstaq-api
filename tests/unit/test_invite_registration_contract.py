import inspect
import unittest

from app.services.auth import invite_service


class InviteRegistrationContractTests(unittest.TestCase):
    def test_member_registration_does_not_issue_tokens(self):
        source = inspect.getsource(invite_service.invite_register)
        member_branch = source.split("if role.name == RoleName.MEMBER:", 1)[1].split("tokens = await issue_tokens", 1)[0]
        self.assertIn('"requires_app": True', member_branch)
        self.assertNotIn("issue_tokens", member_branch)

    def test_non_member_registration_issues_tokens(self):
        source = inspect.getsource(invite_service.invite_register)
        self.assertIn("tokens = await issue_tokens", source)
        self.assertIn('"requires_app": False', source)


if __name__ == "__main__":
    unittest.main()
