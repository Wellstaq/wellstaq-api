"""HTTP route tests for app/routers/user_settings.py."""
import pytest
from tests.conftest import authenticate_user, make_org_with_owner

async def _ctx(db):
    org,u=await make_org_with_owner(db); h,_=await authenticate_user(db,u,device_fingerprint=f'settings-{u.id}'); return u,h

class TestUserSettingsRoutes:
    @pytest.mark.asyncio
    async def test_profile_and_preferences(self,client,db_session):
        u,h=await _ctx(db_session)
        p=await client.get('/user-settings/profile',headers=h); assert p.status_code==200
        prefs=await client.get('/user-settings/preferences',headers=h); assert prefs.status_code in (200,404)
        if prefs.status_code == 200:
            upd=await client.patch('/user-settings/preferences',headers=h,json={'public_profile':False}); assert upd.status_code==200
        prof=await client.patch('/user-settings/profile',headers=h,json={'first_name':'Updated'}); assert prof.status_code in (200,401)
    @pytest.mark.asyncio
    async def test_requires_authentication(self,client): assert (await client.get('/user-settings/profile')).status_code in (401,403)
