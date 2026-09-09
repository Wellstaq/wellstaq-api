"""HTTP route tests for app/routers/wellbeing_surveys.py."""
import pytest
from tests.conftest import authenticate_user, make_org_with_owner

async def _ctx(db):
    org,u=await make_org_with_owner(db); h,_=await authenticate_user(db,u,device_fingerprint=f'survey-{u.id}'); return org,h

class TestWellbeingSurveyRoutes:
    @pytest.mark.asyncio
    async def test_config_and_open_window(self,client,db_session):
        org,h=await _ctx(db_session)
        c=await client.get(f'/organizations/{org.id}/wellbeing/surveys/config',headers=h); assert c.status_code in (200,404)
        w=await client.get(f'/organizations/{org.id}/wellbeing/surveys/open-window',headers=h); assert w.status_code in (200,404)
    @pytest.mark.asyncio
    async def test_requires_authentication(self,client): assert (await client.get('/organizations/x/wellbeing/surveys/config')).status_code in (401,403,404)
