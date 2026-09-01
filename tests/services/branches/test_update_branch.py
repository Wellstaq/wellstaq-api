"""
tests/services/branches/test_update_branch.py

Unit tests for app/services/branch/update_branch.py

Key notes:
  - assign_user_to_branch() requires department_id (mandatory since the
    user's branch and department must always move together).
  - edit_branch() raises 403 when branch.is_headquarters=True.
  - User.branch_id is NOT NULL — _make_user() requires branch_id.
  - make_org_with_owner() is unpacked as (org, owner) everywhere so
    owner.branch_id is always available for seeding other users.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Branch, User
from app.models.department import Department
from app.services.branch.update_branch import (
    assign_branch_manager,
    assign_user_to_branch,
    edit_branch,
    unassign_branch_manager,
)
from tests.conftest import make_org_with_owner, make_user_with_role, get_or_create_system_role
from app.services.organization_roles import ensure_organization_roles, ensure_branch_roles


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_branch(
    db_session: AsyncSession,
    organization_id: str,
    name: str = "Lagos Branch",
    manager_id: str | None = None,
    is_headquarters: bool = False,
) -> Branch:
    for name_of_role in ("member", "branch_manager"):
        await get_or_create_system_role(db_session, name_of_role)
    await ensure_organization_roles(db_session, organization_id)
    branch = Branch(
        organization_id=organization_id,
        name=name,
        manager_id=manager_id,
        is_headquarters=is_headquarters,
    )
    db_session.add(branch)
    await db_session.flush()
    await ensure_branch_roles(db_session, organization_id, branch.id)
    await db_session.flush()
    return branch


async def _make_department(
    db_session: AsyncSession,
    organization_id: str,
    branch_id: str,
    name: str = "General",
) -> Department:
    dept = Department(
        name=name,
        organization_id=organization_id,
        branch_id=branch_id,
        member_count=0,
    )
    db_session.add(dept)
    await db_session.flush()
    return dept


async def _make_user(
    db_session: AsyncSession,
    organization_id: str,
    email: str,
    branch_id: str,            # required — User.branch_id is NOT NULL
) -> User:
    user = await make_user_with_role(
        db_session,
        organization_id=organization_id,
        email=email,
        role_name="member",
        branch_id=branch_id,
    )
    db_session.add(user)
    await db_session.flush()
    return user


# ── edit_branch ────────────────────────────────────────────────────────────────

class TestEditBranch:

    @pytest.mark.asyncio
    async def test_updates_name_successfully(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, name="Old Name")

        result = await edit_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="New Name",
        )

        assert result.name == "New Name"

    @pytest.mark.asyncio
    async def test_name_is_stripped_of_whitespace(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await edit_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="  Abuja Branch  ",
        )

        assert result.name == "Abuja Branch"

    @pytest.mark.asyncio
    async def test_allows_updating_name_to_same_value(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, name="Lagos")

        result = await edit_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Lagos",
        )

        assert result.name == "Lagos"

    @pytest.mark.asyncio
    async def test_allows_casing_change_on_same_name(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, name="lagos branch")

        result = await edit_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Lagos Branch",
        )

        assert result.name == "Lagos Branch"

    @pytest.mark.asyncio
    async def test_passing_name_none_changes_nothing(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, name="Unchanged")

        result = await edit_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name=None,
        )

        assert result.name == "Unchanged"

    @pytest.mark.asyncio
    async def test_returns_updated_branch(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        result = await edit_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            name="Updated",
        )

        assert isinstance(result, Branch)
        assert result.id == branch.id

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await edit_branch(
                session=db_session,
                organization_id=org.id,
                branch_id="00000000-0000-0000-0000-000000000000",
                name="New Name",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch = await _make_branch(db_session, org1.id)

        with pytest.raises(HTTPException) as exc_info:
            await edit_branch(
                session=db_session,
                organization_id=org2.id,
                branch_id=branch.id,
                name="New Name",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_403_when_branch_is_headquarters(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, is_headquarters=True)

        with pytest.raises(HTTPException) as exc_info:
            await edit_branch(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                name="New HQ Name",
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_raises_409_when_name_conflicts_exact_match(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        await _make_branch(db_session, org.id, name="Kano Branch")
        branch = await _make_branch(db_session, org.id, name="Lagos Branch")

        with pytest.raises(HTTPException) as exc_info:
            await edit_branch(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                name="Kano Branch",
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_409_when_name_conflicts_case_insensitive(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        await _make_branch(db_session, org.id, name="Kano Branch")
        branch = await _make_branch(db_session, org.id, name="Lagos Branch")

        with pytest.raises(HTTPException) as exc_info:
            await edit_branch(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                name="KANO BRANCH",
            )

        assert exc_info.value.status_code == 409


# ── assign_user_to_branch ──────────────────────────────────────────────────────

class TestAssignUserToBranch:

    @pytest.mark.asyncio
    async def test_assigns_user_to_branch_successfully(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        dept = await _make_department(db_session, org.id, branch.id)
        user = await _make_user(db_session, org.id, "user@example.com", owner.branch_id)

        result = await assign_user_to_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            user_id=user.id,
            department_id=dept.id,
        )

        assert result.branch_id == branch.id

    @pytest.mark.asyncio
    async def test_returns_updated_user_with_correct_branch_id(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        dept = await _make_department(db_session, org.id, branch.id)
        user = await _make_user(db_session, org.id, "user@example.com", owner.branch_id)

        result = await assign_user_to_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            user_id=user.id,
            department_id=dept.id,
        )

        assert isinstance(result, User)
        assert result.id == user.id
        assert result.branch_id == branch.id

    @pytest.mark.asyncio
    async def test_overwrites_previous_branch_id(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        old_branch = await _make_branch(db_session, org.id, name="Old Branch")
        new_branch = await _make_branch(db_session, org.id, name="New Branch")
        new_dept = await _make_department(db_session, org.id, new_branch.id)
        user = await _make_user(
            db_session, org.id, "user@example.com", old_branch.id
        )

        result = await assign_user_to_branch(
            session=db_session,
            organization_id=org.id,
            branch_id=new_branch.id,
            user_id=user.id,
            department_id=new_dept.id,
        )

        assert result.branch_id == new_branch.id

    @pytest.mark.asyncio
    async def test_raises_404_when_target_branch_not_found(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        user = await _make_user(db_session, org.id, "user@example.com", owner.branch_id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_branch(
                session=db_session,
                organization_id=org.id,
                branch_id="00000000-0000-0000-0000-000000000000",
                user_id=user.id,
                department_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_target_branch_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, owner2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch_org2 = await _make_branch(db_session, org2.id)
        dept_org2 = await _make_department(db_session, org2.id, branch_org2.id)
        user = await _make_user(db_session, org1.id, "user@example.com", owner1.branch_id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_branch(
                session=db_session,
                organization_id=org1.id,
                branch_id=branch_org2.id,
                user_id=user.id,
                department_id=dept_org2.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_user_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        dept = await _make_department(db_session, org.id, branch.id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_branch(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                user_id="00000000-0000-0000-0000-000000000000",
                department_id=dept.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_user_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, owner2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch = await _make_branch(db_session, org1.id)
        dept = await _make_department(db_session, org1.id, branch.id)
        user_org2 = await _make_user(
            db_session, org2.id, "user@example.com", owner2.branch_id
        )

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_branch(
                session=db_session,
                organization_id=org1.id,
                branch_id=branch.id,
                user_id=user_org2.id,
                department_id=dept.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_409_when_user_already_in_target_branch(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        dept = await _make_department(db_session, org.id, branch.id)
        # Create user already in the target branch
        user = await _make_user(
            db_session, org.id, "user@example.com", branch.id
        )

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_branch(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                user_id=user.id,
                department_id=dept.id,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_404_when_department_not_in_target_branch(
        self, db_session: AsyncSession
    ):
        """Department exists in the org but belongs to a different branch —
        must be rejected since branch/dept must always match."""
        org, owner = await make_org_with_owner(db_session)
        target_branch = await _make_branch(db_session, org.id, name="Target Branch")
        other_branch = await _make_branch(db_session, org.id, name="Other Branch")
        dept_in_other_branch = await _make_department(
            db_session, org.id, other_branch.id
        )
        user = await _make_user(
            db_session, org.id, "user@example.com", owner.branch_id
        )

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_branch(
                session=db_session,
                organization_id=org.id,
                branch_id=target_branch.id,
                user_id=user.id,
                department_id=dept_in_other_branch.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_department_not_found(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        user = await _make_user(
            db_session, org.id, "user@example.com", owner.branch_id
        )

        with pytest.raises(HTTPException) as exc_info:
            await assign_user_to_branch(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                user_id=user.id,
                department_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404


# ── assign_branch_manager ──────────────────────────────────────────────────────

class TestAssignBranchManager:

    @pytest.mark.asyncio
    async def test_assigns_manager_successfully(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner = await _make_user(db_session, org.id, "manager@example.com", branch.id)

        result = await assign_branch_manager(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            manager_id=owner.id,
        )

        assert result.manager_id == owner.id

    @pytest.mark.asyncio
    async def test_sets_branch_manager_id(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner = await _make_user(db_session, org.id, "manager@example.com", branch.id)

        await assign_branch_manager(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            manager_id=owner.id,
        )

        await db_session.refresh(branch)
        assert branch.manager_id == owner.id

    @pytest.mark.asyncio
    async def test_returns_updated_branch(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)
        owner = await _make_user(db_session, org.id, "manager@example.com", branch.id)

        result = await assign_branch_manager(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
            manager_id=owner.id,
        )

        assert isinstance(result, Branch)
        assert result.id == branch.id

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_not_found(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await assign_branch_manager(
                session=db_session,
                organization_id=org.id,
                branch_id="00000000-0000-0000-0000-000000000000",
                manager_id=owner.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch = await _make_branch(db_session, org1.id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_branch_manager(
                session=db_session,
                organization_id=org2.id,
                branch_id=branch.id,
                manager_id=owner1.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_manager_user_not_found(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_branch_manager(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                manager_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_manager_user_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, owner2 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch = await _make_branch(db_session, org1.id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_branch_manager(
                session=db_session,
                organization_id=org1.id,
                branch_id=branch.id,
                manager_id=owner2.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_409_when_branch_already_has_different_manager(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, manager_id=owner.id)
        new_manager = await _make_user(
            db_session, org.id, "newmanager@example.com", owner.branch_id
        )

        with pytest.raises(HTTPException) as exc_info:
            await assign_branch_manager(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                manager_id=new_manager.id,
            )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_409_when_assigning_same_manager_already_set(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, manager_id=owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_branch_manager(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
                manager_id=owner.id,
            )

        assert exc_info.value.status_code == 409


# ── unassign_branch_manager ────────────────────────────────────────────────────

class TestUnassignBranchManager:

    @pytest.mark.asyncio
    async def test_clears_manager_id_successfully(self, db_session: AsyncSession):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, manager_id=owner.id)

        result = await unassign_branch_manager(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
        )

        assert result.manager_id is None

    @pytest.mark.asyncio
    async def test_returns_updated_branch_with_manager_id_none(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, manager_id=owner.id)

        result = await unassign_branch_manager(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
        )

        assert isinstance(result, Branch)
        assert result.id == branch.id
        assert result.manager_id is None

    @pytest.mark.asyncio
    async def test_branch_manager_id_is_none_in_db_after_unassign(
        self, db_session: AsyncSession
    ):
        org, owner = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id, manager_id=owner.id)

        await unassign_branch_manager(
            session=db_session,
            organization_id=org.id,
            branch_id=branch.id,
        )

        await db_session.refresh(branch)
        assert branch.manager_id is None

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_not_found(self, db_session: AsyncSession):
        org, _ = await make_org_with_owner(db_session)

        with pytest.raises(HTTPException) as exc_info:
            await unassign_branch_manager(
                session=db_session,
                organization_id=org.id,
                branch_id="00000000-0000-0000-0000-000000000000",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_branch_belongs_to_different_org(
        self, db_session: AsyncSession
    ):
        org1, owner1 = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-one"),
            user_kwargs=dict(email="owner1@example.com"),
        )
        org2, _ = await make_org_with_owner(
            db_session,
            org_kwargs=dict(slug="org-two"),
            user_kwargs=dict(email="owner2@example.com"),
        )
        branch = await _make_branch(db_session, org1.id, manager_id=owner1.id)

        with pytest.raises(HTTPException) as exc_info:
            await unassign_branch_manager(
                session=db_session,
                organization_id=org2.id,
                branch_id=branch.id,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_409_when_branch_has_no_manager(
        self, db_session: AsyncSession
    ):
        org, _ = await make_org_with_owner(db_session)
        branch = await _make_branch(db_session, org.id)

        with pytest.raises(HTTPException) as exc_info:
            await unassign_branch_manager(
                session=db_session,
                organization_id=org.id,
                branch_id=branch.id,
            )

        assert exc_info.value.status_code == 409
