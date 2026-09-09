"""HTTP route tests for app/routers/payments.py."""
import pytest
from tests.conftest import authenticate_user, make_org_with_owner

async def _ctx(db):
    org,u=await make_org_with_owner(db); h,_=await authenticate_user(db,u,permissions=('org.billing.manage',),device_fingerprint=f'payment-{u.id}'); return org,h

class TestPaymentRoutes:
    @pytest.mark.asyncio
    async def test_subscription_and_transactions(self,client,db_session):
        org,h=await _ctx(db_session)
        s=await client.get(f'/organizations/{org.id}/subscription',headers=h); assert s.status_code in (200,404)
        t=await client.get(f'/organizations/{org.id}/transactions',headers=h); assert t.status_code==200
        missing=await client.get(f'/organizations/{org.id}/transactions/missing',headers=h); assert missing.status_code==404
    @pytest.mark.asyncio
    async def test_requires_billing_permission(self,client,db_session):
        org,u=await make_org_with_owner(db_session); h,_=await authenticate_user(db_session,u,device_fingerprint=f'payment-no-{u.id}')
        assert (await client.get(f'/organizations/{org.id}/transactions',headers=h)).status_code==403
