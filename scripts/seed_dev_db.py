"""
scripts/seed_dev_db.py  ── FULL DEV SEED

Creates for each of 3 organizations:
  • 3 Branches
      └─ 3 Departments  (each dept belongs to a branch)
      └─ 3 Clubs        (each club belongs to a branch)
      └─ 3 Events       (each event belongs to a branch)
  • 10 Users  (distributed across the 3 branches and their departments)
  • 15 Posts  (distributed across users; 5 carry hashtags)
  • GroupChatMessages for every Club and every Event

The owner (super_admin) is counted as one of the 10 users and always
lives in Branch-1 / Department-1.

USER DISTRIBUTION (per org, 10 users total):
  Branch-1 → 4 users (owner + 3 extra): dept-1-1, dept-1-2, dept-1-3
  Branch-2 → 3 users:                   dept-2-1, dept-2-2, dept-2-3
  Branch-3 → 3 users:                   dept-3-1, dept-3-2, dept-3-3

POST DISTRIBUTION (15 per org):
  5 posts per branch, each attributed to a different user in that branch.
  Posts 11-15 (third branch) carry hashtags.

CHAT MESSAGES:
  Each club  → 3 messages from 3 different branch users.
  Each event → 3 messages from 3 different branch users.

All users share PASSWORD = "TestPass123!"

Run:
    python -m scripts.seed_dev_db
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, time, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_dev")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

RESET_DB = True
PASSWORD = "TestPass123!"

# ── 3 Organizations ───────────────────────────────────────────────────────────
ORGANIZATIONS = [
    {
        "business_name": "Apex Wellness Corp",
        "slug": "apex-wellness-corp",
        "employee_count": 50,
        "organization_type": "corporate",
        "work_model": "hybrid",
        "is_active": True,
    },
    {
        "business_name": "Horizon Health Ltd",
        "slug": "horizon-health-ltd",
        "employee_count": 30,
        "organization_type": "corporate",
        "work_model": "remote",
        "is_active": True,
    },
    {
        "business_name": "Zenith Active Inc",
        "slug": "zenith-active-inc",
        "employee_count": 20,
        "organization_type": "corporate",
        "work_model": "onsite",
        "is_active": True,
    },
]

# ── 3 Branches per org (names reused across orgs) ────────────────────────────
BRANCH_TEMPLATES = [
    {"name": "Head Office"},
    {"name": "North Branch"},
    {"name": "South Branch"},
]

# ── 3 Departments per branch  (names parameterized with branch index) ─────────
# dept_label is formatted with branch_num (1/2/3)
DEPARTMENT_TEMPLATES = [
    {"name_tpl": "Engineering B{b}", "description": "Tech and product teams", "avatar_initial": "EN"},
    {"name_tpl": "Marketing B{b}",   "description": "Growth and comms",        "avatar_initial": "MK"},
    {"name_tpl": "Operations B{b}",  "description": "Logistics and ops",        "avatar_initial": "OP"},
]

# ── 3 Clubs per branch ────────────────────────────────────────────────────────
CLUB_TEMPLATES = [
    {"name_tpl": "Running Club B{b}",   "description": "Weekly running sessions",      "privacy": "public"},
    {"name_tpl": "Mindfulness B{b}",    "description": "Meditation and mental health",  "privacy": "public"},
    {"name_tpl": "Book Circle B{b}",    "description": "Monthly book discussions",      "privacy": "private"},
]

# ── 3 Events per branch ───────────────────────────────────────────────────────
EVENT_TEMPLATES = [
    {
        "title_tpl": "Annual Wellness Day B{b}",
        "description": "Full-day health and wellness fair.",
        "start_date": date(2025, 9, 15),
        "end_date":   date(2025, 9, 15),
        "time":       time(9, 0),
        "status":     "scheduled",
    },
    {
        "title_tpl": "Q3 Team Offsite B{b}",
        "description": "Quarterly team building retreat.",
        "start_date": date(2025, 8, 20),
        "end_date":   date(2025, 8, 22),
        "time":       time(8, 30),
        "status":     "scheduled",
    },
    {
        "title_tpl": "Monthly Step Challenge B{b}",
        "description": "30-day step count competition.",
        "start_date": date(2025, 7, 1),
        "end_date":   date(2025, 7, 31),
        "time":       time(0, 0),
        "status":     "ongoing",
        "recurrence_rule": "weekly_monday",
    },
]

# ── 9 extra users per org (owner is #1; these are #2-#10) ────────────────────
# branch_idx  → 0-based index into the 3 branches created for the org
# dept_idx    → 0-based index into the 3 departments of that branch
EXTRA_USER_TEMPLATES = [
    # Branch-1 (idx 0) users
    {"first_name": "Chidi",   "last_name": "Nwosu",    "email_tpl": "chidi.{slug}@example.com",   "role": "admin",          "branch_idx": 0, "dept_idx": 0},
    {"first_name": "Tunde",   "last_name": "Balogun",  "email_tpl": "tunde.{slug}@example.com",   "role": "branch_manager", "branch_idx": 0, "dept_idx": 1},
    {"first_name": "Bisi",    "last_name": "Adeyemi",  "email_tpl": "bisi.{slug}@example.com",    "role": "team_leader",    "branch_idx": 0, "dept_idx": 2},
    # Branch-2 (idx 1) users
    {"first_name": "Femi",    "last_name": "Okafor",   "email_tpl": "femi.{slug}@example.com",    "role": "branch_manager", "branch_idx": 1, "dept_idx": 0},
    {"first_name": "Zainab",  "last_name": "Musa",     "email_tpl": "zainab.{slug}@example.com",  "role": "team_leader",    "branch_idx": 1, "dept_idx": 1},
    {"first_name": "Emeka",   "last_name": "Eze",      "email_tpl": "emeka.{slug}@example.com",   "role": "member",         "branch_idx": 1, "dept_idx": 2},
    # Branch-3 (idx 2) users
    {"first_name": "Ngozi",   "last_name": "Ibe",      "email_tpl": "ngozi.{slug}@example.com",   "role": "branch_manager", "branch_idx": 2, "dept_idx": 0},
    {"first_name": "Damilola","last_name": "Afolabi",  "email_tpl": "dami.{slug}@example.com",    "role": "team_leader",    "branch_idx": 2, "dept_idx": 1},
    {"first_name": "Seun",    "last_name": "Owolabi",  "email_tpl": "seun.{slug}@example.com",    "role": "member",         "branch_idx": 2, "dept_idx": 2},
]

# ── Hashtag names (5 per org) ─────────────────────────────────────────────────
HASHTAG_NAMES = ["wellness", "fitness", "teamwork", "mindfulness", "growth"]

# ── Sample chat messages ──────────────────────────────────────────────────────
CLUB_MESSAGES = [
    "Excited to be part of this club! 🙌",
    "Great session last week everyone, keep it up!",
    "Reminder: we meet every Thursday at 6pm.",
]
EVENT_MESSAGES = [
    "Can't wait for this event, it's going to be amazing!",
    "Who else is coming? Let's coordinate.",
    "Thanks for organizing this, the team really needs it.",
]

# ── Post content pool (15 items; last 5 will get hashtags) ────────────────────
POST_CONTENTS = [
    "Feeling energized after today's morning walk! 🌅",
    "Team lunch was a great way to bond today.",
    "Just completed my first 5K run this month. Progress!",
    "Grateful for the support system we have here at work.",
    "New productivity tip: take a 5-minute stretch every hour.",
    "Our branch had a fantastic workshop on mental health today.",
    "Collaboration across teams makes the impossible possible.",
    "Small wins matter. Celebrated a milestone with the team! 🎉",
    "Healthy snacks in the break room = happy employees.",
    "Proud of how far we've come this quarter.",
    "Big shoutout to everyone who joined the step challenge! #fitness",
    "Mindfulness session today was incredibly grounding. #mindfulness",
    "Team growth is real — we hit our wellness goals! #growth",
    "Nothing beats working with people who care. #teamwork",
    "Wellness Wednesday is now my favourite day. #wellness",
]

# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────

from app.core.database import engine, get_db
from app.models.base import Base

try:
    from app.auth.security import hash_password
except ImportError:
    logger.warning("Falling back to local bcrypt hasher.")
    from passlib.context import CryptContext
    _pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    def hash_password(raw: str) -> str:
        return _pwd_ctx.hash(raw)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Branch, Department, DepartmentMember,
    Club, ClubMember,
    Event, EventParticipant,
    Organization,
    User, UserAuthProvider, UserPermission, UserSettings,
    Post, PostHashtag,
    GroupChatMessage,
)
from app.models.hashtag import Hashtag
from app.models.permission import Permission, Role
from scripts.seed_permissions import seed_roles, seed_permissions

ROLE_NAMES = ["super_admin", "admin", "branch_manager", "team_leader", "member"]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

async def reset_schema() -> None:
    logger.info("Dropping and recreating all tables …")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Schema rebuilt.")


async def load_system_roles(session: AsyncSession) -> dict[str, Role]:
    result = await session.execute(
        select(Role).where(Role.name.in_(ROLE_NAMES), Role.is_system == True)  # noqa: E712
    )
    roles = {r.name: r for r in result.scalars().all()}
    missing = set(ROLE_NAMES) - set(roles)
    if missing:
        raise RuntimeError(f"System roles missing: {missing}")
    return roles


async def load_all_permissions(session: AsyncSession) -> list[Permission]:
    result = await session.execute(select(Permission))
    return list(result.scalars().all())


async def grant_all_permissions(
    session: AsyncSession,
    *,
    user: User,
    organization_id: str,
    permissions: list[Permission],
    granted_by: str,
) -> None:
    for perm in permissions:
        session.add(UserPermission(
            user_id=user.id,
            permission_id=perm.id,
            organization_id=organization_id,
            branch_id=None,
            granted_by=granted_by,
        ))
    await session.flush()


async def create_user(
    session: AsyncSession,
    *,
    organization_id: str,
    branch_id: str,
    role_id: str,
    first_name: str,
    last_name: str,
    email: str,
    phone_number: str | None = None,
) -> User:
    user = User(
        first_name=first_name,
        last_name=last_name,
        email=email.lower().strip(),
        phone_number=phone_number,
        hashed_password=hash_password(PASSWORD),
        status="active",
        organization_id=organization_id,
        branch_id=branch_id,
        role_id=role_id,
    )
    session.add(user)
    await session.flush()
    session.add(UserSettings(user_id=user.id, organization_id=organization_id))
    session.add(UserAuthProvider(
        user_id=user.id,
        organization_id=organization_id,
        provider="password",
        provider_user_id=None,
        access_token=None,
        refresh_token=None,
    ))
    await session.flush()
    return user


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SEED
# ─────────────────────────────────────────────────────────────────────────────

async def seed() -> None:
    if RESET_DB:
        await reset_schema()

    # Accumulate summary lines; printed once at the very end.
    summary: list[str] = []

    async for session in get_db():
        await seed_roles(session)
        await seed_permissions(session)

        roles = await load_system_roles(session)
        all_permissions = await load_all_permissions(session)

        for org_data in ORGANIZATIONS:
            slug = org_data["slug"]
            summary.append("")
            summary.append("=" * 70)
            summary.append(f"  ORGANIZATION: {org_data['business_name']}  (slug={slug})")
            summary.append("=" * 70)

            # ── Organization ─────────────────────────────────────────────
            org = Organization(**org_data)
            session.add(org)
            await session.flush()
            summary.append(f"  org_id            = {org.id}")

            # ── Branches, Departments, Clubs, Events ─────────────────────
            branches: list[Branch] = []
            # departments[branch_idx] → list of 3 Department rows
            branch_departments: list[list[Department]] = []
            # clubs[branch_idx] → list of 3 Club rows
            branch_clubs: list[list[Club]] = []
            # events[branch_idx] → list of 3 Event rows
            branch_events: list[list[Event]] = []

            for b_idx, b_tpl in enumerate(BRANCH_TEMPLATES):
                b_num = b_idx + 1  # 1-based for display / naming

                # ── Branch ───────────────────────────────────────────────
                branch = Branch(
                    organization_id=org.id,
                    name=b_tpl["name"],
                    created_by=None,  # backfilled after owner is created
                )
                session.add(branch)
                await session.flush()
                branches.append(branch)

                summary.append("")
                summary.append(f"  ── Branch {b_num}: {branch.name}")
                summary.append(f"     branch_id         = {branch.id}")

                # ── Departments (must exist before users) ─────────────────
                depts: list[Department] = []
                for d_idx, d_tpl in enumerate(DEPARTMENT_TEMPLATES):
                    dept = Department(
                        organization_id=org.id,
                        branch_id=branch.id,
                        name=d_tpl["name_tpl"].format(b=b_num),
                        description=d_tpl["description"],
                        avatar_initial=d_tpl["avatar_initial"],
                        created_by=None,  # backfilled after owner exists
                    )
                    session.add(dept)
                    await session.flush()
                    depts.append(dept)
                    summary.append(f"     dept_id  (d{d_idx+1})   = {dept.id}  [{dept.name}]")
                branch_departments.append(depts)

                # ── Clubs ─────────────────────────────────────────────────
                clubs_this_branch: list[Club] = []
                for c_idx, c_tpl in enumerate(CLUB_TEMPLATES):
                    club = Club(
                        organization_id=org.id,
                        branch_id=branch.id,
                        name=c_tpl["name_tpl"].format(b=b_num),
                        description=c_tpl["description"],
                        privacy=c_tpl["privacy"],
                        created_by=None,
                        member_count=0,
                    )
                    session.add(club)
                    await session.flush()
                    clubs_this_branch.append(club)
                    summary.append(f"     club_id  (c{c_idx+1})   = {club.id}  [{club.name}]")
                branch_clubs.append(clubs_this_branch)

                # ── Events ────────────────────────────────────────────────
                events_this_branch: list[Event] = []
                for e_idx, e_tpl in enumerate(EVENT_TEMPLATES):
                    event = Event(
                        organization_id=org.id,
                        branch_id=branch.id,
                        title=e_tpl["title_tpl"].format(b=b_num),
                        description=e_tpl["description"],
                        start_date=e_tpl["start_date"],
                        end_date=e_tpl.get("end_date"),
                        time=e_tpl["time"],
                        status=e_tpl["status"],
                        recurrence_rule=e_tpl.get("recurrence_rule"),
                        created_by=None,
                    )
                    session.add(event)
                    await session.flush()
                    events_this_branch.append(event)
                    summary.append(f"     event_id (e{e_idx+1})   = {event.id}  [{event.title}]")
                branch_events.append(events_this_branch)

            # ── Owner (super_admin) — always Branch-1, Dept-1 ────────────
            owner = await create_user(
                session,
                organization_id=org.id,
                branch_id=branches[0].id,
                role_id=roles["super_admin"].id,
                first_name="Ada",
                last_name="Owner",
                email=f"owner.{slug}@example.com",
                phone_number="+2348000000001",
            )
            # Backfill created_by on all branches/depts/clubs/events now
            # that the owner exists.
            for br in branches:
                br.created_by = owner.id
            for depts in branch_departments:
                for d in depts:
                    d.created_by = owner.id
            for clubs in branch_clubs:
                for c in clubs:
                    c.created_by = owner.id
                    c.leader_id = owner.id
            for events in branch_events:
                for e in events:
                    e.created_by = owner.id
            if hasattr(org, "owner_id"):
                org.owner_id = owner.id
            await session.flush()

            await grant_all_permissions(
                session,
                user=owner,
                organization_id=org.id,
                permissions=all_permissions,
                granted_by=owner.id,
            )

            summary.append("")
            summary.append(f"  ── Users (owner + 9 extra) for org {slug}")
            summary.append(f"     user_id  [owner]   = {owner.id}  (Ada Owner / super_admin / Branch-1 / Dept-1)")

            # Owner → department membership in Branch-1 / Dept-1
            dm_owner = DepartmentMember(
                department_id=branch_departments[0][0].id,
                user_id=owner.id,
                role="lead",
            )
            session.add(dm_owner)
            await session.flush()

            # ── 9 extra users ─────────────────────────────────────────────
            all_users_by_branch: list[list[User]] = [
                [owner],  # branch-0 starts with the owner
                [],
                [],
            ]

            for u_tpl in EXTRA_USER_TEMPLATES:
                b_idx = u_tpl["branch_idx"]
                d_idx = u_tpl["dept_idx"]
                email = u_tpl["email_tpl"].format(slug=slug.replace("-", ""))
                user = await create_user(
                    session,
                    organization_id=org.id,
                    branch_id=branches[b_idx].id,
                    role_id=roles[u_tpl["role"]].id,
                    first_name=u_tpl["first_name"],
                    last_name=u_tpl["last_name"],
                    email=email,
                )
                all_users_by_branch[b_idx].append(user)

                # Department membership
                session.add(DepartmentMember(
                    department_id=branch_departments[b_idx][d_idx].id,
                    user_id=user.id,
                    role="member",
                ))
                await session.flush()

                summary.append(
                    f"     user_id  [{u_tpl['first_name']:<10}] = {user.id}"
                    f"  ({u_tpl['role']} / Branch-{b_idx+1} / Dept-{d_idx+1})"
                )

            # Update member counts on departments (rough count, good enough for dev)
            for b_idx, depts in enumerate(branch_departments):
                for d_idx, dept in enumerate(depts):
                    dept.member_count = sum(
                        1 for u in all_users_by_branch[b_idx]
                        if True  # all users of branch go to some dept
                    )
            await session.flush()

            # ── Club memberships: assign each branch's users to that branch's clubs ──
            summary.append("")
            summary.append(f"  ── Club memberships for org {slug}")
            for b_idx, branch_users in enumerate(all_users_by_branch):
                clubs = branch_clubs[b_idx]
                for u_pos, user in enumerate(branch_users):
                    # Round-robin: user 0→club0, user 1→club1, user 2→club2, user 3→club0 …
                    club = clubs[u_pos % len(clubs)]
                    session.add(ClubMember(
                        club_id=club.id,
                        user_id=user.id,
                        joined_at=datetime.now(timezone.utc),
                    ))
                    club.member_count += 1
                summary.append(
                    f"     Branch-{b_idx+1}: {len(branch_users)} users distributed "
                    f"across {len(clubs)} clubs (round-robin)"
                )
            await session.flush()

            # ── Event participants ────────────────────────────────────────
            summary.append(f"  ── Event participants for org {slug}")
            for b_idx, branch_users in enumerate(all_users_by_branch):
                for event in branch_events[b_idx]:
                    for user in branch_users:
                        session.add(EventParticipant(
                            event_id=event.id,
                            user_id=user.id,
                            status="accepted",
                            attended=False,
                        ))
                summary.append(
                    f"     Branch-{b_idx+1}: {len(branch_users)} users added to each of {len(branch_events[b_idx])} events"
                )
            await session.flush()

            # ── Hashtags (5 per org) ──────────────────────────────────────
            hashtags: list[Hashtag] = []
            for tag_name in HASHTAG_NAMES:
                ht = Hashtag(
                    organization_id=org.id,
                    tag_name=tag_name,
                    total_post_count=0,
                )
                session.add(ht)
                hashtags.append(ht)
            await session.flush()
            summary.append("")
            summary.append(f"  ── Hashtags for org {slug}")
            for ht in hashtags:
                summary.append(f"     hashtag_id        = {ht.id}  [#{ht.tag_name}]")

            # ── Posts (15 per org, 5 per branch, last 5 get hashtags) ─────
            # Flatten users: branch-0 users first, then branch-1, branch-2
            flat_users: list[User] = []
            for branch_users in all_users_by_branch:
                flat_users.extend(branch_users)

            summary.append("")
            summary.append(f"  ── Posts for org {slug}")

            all_posts: list[Post] = []
            for p_idx, content in enumerate(POST_CONTENTS):
                b_idx = p_idx // 5  # posts 0-4→branch0, 5-9→branch1, 10-14→branch2
                poster = all_users_by_branch[b_idx][p_idx % len(all_users_by_branch[b_idx])]
                post = Post(
                    user_id=poster.id,
                    organization_id=org.id,
                    branch_id=branches[b_idx].id,
                    content=content,
                )
                session.add(post)
                await session.flush()
                all_posts.append(post)

                has_hashtag = p_idx >= 10  # last 5 posts get hashtags
                ht_tag = f" [#{hashtags[p_idx - 10].tag_name}]" if has_hashtag else ""
                summary.append(
                    f"     post_id  [p{p_idx+1:02d}]    = {post.id}"
                    f"  (poster={poster.first_name} {poster.last_name} / Branch-{b_idx+1}){ht_tag}"
                )

                if has_hashtag:
                    ht = hashtags[p_idx - 10]
                    session.add(PostHashtag(
                        organization_id=org.id,
                        post_id=post.id,
                        hashtag_id=ht.id,
                    ))
                    ht.total_post_count += 1

            await session.flush()

            # ── Group chat messages (clubs) ───────────────────────────────
            summary.append("")
            summary.append(f"  ── Club chat messages for org {slug}")
            for b_idx, clubs in enumerate(branch_clubs):
                branch_users = all_users_by_branch[b_idx]
                for club in clubs:
                    for m_idx, msg_content in enumerate(CLUB_MESSAGES):
                        speaker = branch_users[m_idx % len(branch_users)]
                        msg = GroupChatMessage(
                            organization_id=org.id,
                            conversation_type="club",
                            conversation_id=club.id,
                            user_id=speaker.id,
                            content=msg_content,
                            is_pinned=False,
                        )
                        session.add(msg)
                    await session.flush()
                    summary.append(
                        f"     club chat [{club.name}]: 3 messages "
                        f"(club_id={club.id})"
                    )

            # ── Group chat messages (events) ──────────────────────────────
            summary.append(f"  ── Event chat messages for org {slug}")
            for b_idx, events in enumerate(branch_events):
                branch_users = all_users_by_branch[b_idx]
                for event in events:
                    for m_idx, msg_content in enumerate(EVENT_MESSAGES):
                        speaker = branch_users[m_idx % len(branch_users)]
                        msg = GroupChatMessage(
                            organization_id=org.id,
                            conversation_type="event",
                            conversation_id=event.id,
                            user_id=speaker.id,
                            content=msg_content,
                            is_pinned=False,
                        )
                        session.add(msg)
                    await session.flush()
                    summary.append(
                        f"     event chat [{event.title}]: 3 messages "
                        f"(event_id={event.id})"
                    )

        # ── Commit everything at once ──────────────────────────────────────
        await session.commit()
        break

    # ── Print human-readable summary ──────────────────────────────────────────
    print()
    print("╔" + "═" * 70 + "╗")
    print("║  SEED COMPLETE — all IDs below                                      ║")
    print("╚" + "═" * 70 + "╝")
    for line in summary:
        print(line)
    print()
    print("  Quick reference key:")
    print("    org_id       → Organization")
    print("    branch_id    → Branch (b1/b2/b3 = Head Office / North / South)")
    print("    dept_id      → Department  (d1/d2/d3 under each branch)")
    print("    club_id      → Club        (c1/c2/c3 under each branch)")
    print("    event_id     → Event       (e1/e2/e3 under each branch)")
    print("    user_id      → User        (owner + 9 extras per org)")
    print("    post_id      → Post        (p01-p15 per org; p11-p15 have hashtags)")
    print("    hashtag_id   → Hashtag     (5 per org: wellness/fitness/teamwork/mindfulness/growth)")
    print()

    # ── Login credentials table ───────────────────────────────────────────────
    print("╔" + "═" * 100 + "╗")
    print("║  LOGIN CREDENTIALS  (all users share the same password)                                            ║")
    print("╠" + "═" * 100 + "╣")
    print(f"║  PASSWORD (all users): {PASSWORD:<77}║")
    print("╠" + "═" * 30 + "╦" + "═" * 37 + "╦" + "═" * 18 + "╦" + "═" * 12 + "╣")
    print(f"║  {'Organization':<28} ║ {'Email':<35} ║ {'Role':<16} ║ {'Branch':<10} ║")
    print("╠" + "═" * 30 + "╬" + "═" * 37 + "╬" + "═" * 18 + "╬" + "═" * 12 + "╣")

    for org_data in ORGANIZATIONS:
        slug = org_data["slug"]
        org_name = org_data["business_name"]
        slug_compact = slug.replace("-", "")

        credentials = [
            # (email, role, branch_label)
            (f"owner.{slug}@example.com",              "super_admin",    "Head Office"),
            (f"chidi.{slug_compact}@example.com",      "admin",          "Head Office"),
            (f"tunde.{slug_compact}@example.com",      "branch_manager", "Head Office"),
            (f"bisi.{slug_compact}@example.com",       "team_leader",    "Head Office"),
            (f"femi.{slug_compact}@example.com",       "branch_manager", "North Branch"),
            (f"zainab.{slug_compact}@example.com",     "team_leader",    "North Branch"),
            (f"emeka.{slug_compact}@example.com",      "member",         "North Branch"),
            (f"ngozi.{slug_compact}@example.com",      "branch_manager", "South Branch"),
            (f"dami.{slug_compact}@example.com",       "team_leader",    "South Branch"),
            (f"seun.{slug_compact}@example.com",       "member",         "South Branch"),
        ]

        first = True
        for email, role, branch in credentials:
            org_col = org_name if first else ""
            print(f"║  {org_col:<28} ║ {email:<35} ║ {role:<16} ║ {branch:<10} ║")
            first = False

        print("╠" + "═" * 30 + "╬" + "═" * 37 + "╬" + "═" * 18 + "╬" + "═" * 12 + "╣")

    print("╚" + "═" * 30 + "╩" + "═" * 37 + "╩" + "═" * 18 + "╩" + "═" * 12 + "╝")
    print()


if __name__ == "__main__":
    asyncio.run(seed())