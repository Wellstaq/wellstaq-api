"""HTTP route tests for app/routers/dashboards.py."""
import pytest
from tests.conftest import authenticate_user, make_org_with_owner

async def _ctx(db):
    org,u=await make_org_with_owner(db); h,_=await authenticate_user(db,u,device_fingerprint=f'dash-{u.id}'); return org,u,h

class TestDashboardRoutes:
    @pytest.mark.asyncio
    async def test_bootstrap_and_trends(self,client,db_session):
        org,u,h=await _ctx(db_session)
        r=await client.get(f'/organizations/{org.id}/dashboard/bootstrap',headers=h); assert r.status_code==200
        body = r.json()
        assert body["user"]["business_name"] == org.business_name
        assert body["user"]["organization_slug"] == org.slug
        r=await client.get(f'/organizations/{org.id}/dashboard/engagement-wellbeing-trends',headers=h); assert r.status_code in (200,403)
    @pytest.mark.asyncio
    async def test_tenant_isolation(self,client,db_session):
        _,_,h=await _ctx(db_session); other,_=await make_org_with_owner(db_session,org_kwargs={'slug':'dash-other','business_name':'Dash Other'},user_kwargs={'email':'dash-other@example.com'})
        assert (await client.get(f'/organizations/{other.id}/dashboard/bootstrap',headers=h)).status_code==403
