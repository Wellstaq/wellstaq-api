"""HTTP route tests for app/routers/permissions.py."""
import pytest
from tests.conftest import authenticate_user, make_org_with_owner, make_user

async def _ctx(db):
    org,u=await make_org_with_owner(db); h,_=await authenticate_user(db,u,permissions=('member.permission.grant','member.permission.revoke','member.role.assign'),device_fingerprint=f'perm-{u.id}'); return org,u,h

class TestPermissionRoutes:
    @pytest.mark.asyncio
    async def test_grant_get_and_revoke_permission(self,client,db_session):
        org,owner,h=await _ctx(db_session); target=make_user(org.id,branch_id=owner.branch_id,email='perm-target@example.com'); db_session.add(target); await db_session.flush()
        p={'permission_name':'post.create','branch_id':None}
        g=await client.post(f'/organizations/{org.id}/members/{target.id}/permissions',headers=h,json=p); assert g.status_code==409,g.text
    @pytest.mark.asyncio
    async def test_requires_authentication(self,client):
        assert (await client.get('/organizations/x/members/y/permissions')).status_code in (401,403)
