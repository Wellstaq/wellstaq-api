import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.routers.roles import _remove_revoked_role_access


class RoleRevokeCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE TABLE role_permissions (id TEXT, role_id TEXT, permission_id TEXT)"))
            await conn.execute(text("CREATE TABLE user_permissions (id TEXT, user_id TEXT, permission_id TEXT, organization_id TEXT, branch_id TEXT, granted_by TEXT, created_at TEXT, updated_at TEXT)"))
            await conn.execute(text("CREATE TABLE user_role_assignments (id TEXT, user_id TEXT, role_id TEXT, organization_id TEXT, branch_id TEXT, department_id TEXT, created_at TEXT, updated_at TEXT)"))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_branch_revoke_removes_only_role_permission_intersection_in_that_branch(self):
        async with self.sessions() as session:
            await session.execute(text("INSERT INTO role_permissions VALUES ('rp1','manager','p1')"))
            await session.execute(text("INSERT INTO user_permissions VALUES ('g1','user','p1','org','branch-a',NULL,NULL,NULL), ('g2','user','p1','org','branch-b',NULL,NULL,NULL), ('g3','user','p1','org',NULL,NULL,NULL,NULL), ('g4','user','unrelated','org','branch-a','admin',NULL,NULL), ('g5','other','p1','org','branch-a',NULL,NULL,NULL)"))
            await session.execute(text("INSERT INTO user_role_assignments VALUES ('a1','user','manager','org','branch-a',NULL,NULL,NULL), ('a2','user','other','org','branch-a',NULL,NULL,NULL)"))
            removed_grants, removed_assignments = await _remove_revoked_role_access(
                session, user_id="user", organization_id="org", role_id="manager", role_branch_id="branch-a"
            )
            self.assertEqual((removed_grants, removed_assignments), (1, 1))
            grants = set((await session.execute(text("SELECT permission_id, branch_id FROM user_permissions"))).all())
            self.assertEqual(grants, {("p1", "branch-b"), ("p1", None), ("unrelated", "branch-a"), ("p1", "branch-a")})

    async def test_org_revoke_removes_only_org_scoped_role_permissions(self):
        async with self.sessions() as session:
            await session.execute(text("INSERT INTO role_permissions VALUES ('rp1','admin','p1')"))
            await session.execute(text("INSERT INTO user_permissions VALUES ('g1','user','p1','org',NULL,NULL,NULL,NULL), ('g2','user','p1','org','branch-a',NULL,NULL,NULL), ('g3','user','unrelated','org',NULL,'admin',NULL,NULL)"))
            removed_grants, _ = await _remove_revoked_role_access(
                session, user_id="user", organization_id="org", role_id="admin", role_branch_id=None
            )
            self.assertEqual(removed_grants, 1)
            grants = set((await session.execute(text("SELECT permission_id, branch_id FROM user_permissions"))).all())
            self.assertEqual(grants, {("p1", "branch-a"), ("unrelated", None)})


if __name__ == "__main__":
    unittest.main()
