"""
Plan enforcement — checks whether an action is allowed under the company's plan.

All checks scope to the company (not the individual user) so team members
share the same caps. Solo users without a company fall back to user-scoped
counting, which gives them the Free-plan limits correctly.
"""
from fastapi import HTTPException
from app.services.supabase_client import supabase_admin
from app.services.plan_limits import get_limits, has_feature, is_unlimited
from datetime import datetime, timezone


async def get_company_plan(user_id: str) -> tuple[str, str | None]:
    """
    Get the plan and company_id for a user.
    Returns (plan, company_id). Defaults to ('free', None) for solo users.
    """
    # Check if user owns a company
    try:
        res = (
            supabase_admin.table("companies")
            .select("id, plan")
            .eq("owner_id", user_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("plan", "free"), res.data[0]["id"]
    except Exception:
        pass

    # Check if user is a member of a company
    try:
        res = (
            supabase_admin.table("company_members")
            .select("company_id, companies(plan)")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if res.data and res.data[0].get("companies"):
            return res.data[0]["companies"].get("plan", "free"), res.data[0]["company_id"]
    except Exception:
        pass

    return "free", None


async def _count_company_projects(company_id: str | None, user_id: str) -> int:
    """
    Count projects for limit enforcement.

    When the user has a company: count ALL projects across the company
    (so a 5-project Starter team can't get 10 by having two members
    each create 5). When the user is solo: fall back to their own
    projects — same semantics as the pre-migration behaviour.
    """
    if company_id:
        res = (
            supabase_admin.table("projects")
            .select("id", count="exact")
            .eq("company_id", company_id)
            .execute()
        )
    else:
        # Solo user — no company yet. Count their personal projects.
        res = (
            supabase_admin.table("projects")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .is_("company_id", "null")
            .execute()
        )
    return res.count if res.count is not None else len(res.data or [])


async def _count_company_snags_this_month(company_id: str | None, user_id: str) -> int:
    """
    Count snags created this calendar month for limit enforcement.

    Scoped to the company's project set (not the single project being
    added to), so a free user can't dodge the 20/month cap by spreading
    across 2 projects.

    Uses UTC month boundaries. Acceptable for UK/IE customers; revisit
    if we onboard customers on the far edges of the date line.
    """
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    # Resolve the project set we're counting across
    if company_id:
        proj_rows = (
            supabase_admin.table("projects")
            .select("id")
            .eq("company_id", company_id)
            .execute()
        )
    else:
        proj_rows = (
            supabase_admin.table("projects")
            .select("id")
            .eq("user_id", user_id)
            .is_("company_id", "null")
            .execute()
        )

    project_ids = [p["id"] for p in (proj_rows.data or [])]
    if not project_ids:
        return 0

    snags = (
        supabase_admin.table("snags")
        .select("id", count="exact")
        .in_("project_id", project_ids)
        .gte("created_at", month_start)
        .execute()
    )
    return snags.count if snags.count is not None else len(snags.data or [])


async def check_project_limit(user_id: str):
    """Raise 403 if the user's company has hit its project cap."""
    plan, company_id = await get_company_plan(user_id)
    limits = get_limits(plan)

    # Unlimited tier — skip the query entirely
    if is_unlimited(limits["max_projects"]):
        return

    current = await _count_company_projects(company_id, user_id)

    if current >= limits["max_projects"]:
        raise HTTPException(
            status_code=403,
            detail=f"Project limit reached ({current}/{limits['max_projects']}). Upgrade your plan to create more projects."
        )


async def check_snag_limit(user_id: str, project_id: str):
    """
    Raise 403 if the user's company has hit its monthly item cap.

    `project_id` is accepted for API compatibility but the count spans
    the whole company — see _count_company_snags_this_month above.
    """
    plan, company_id = await get_company_plan(user_id)
    limits = get_limits(plan)

    # Unlimited tier — skip the query entirely
    if is_unlimited(limits["max_snags_per_month"]):
        return

    current = await _count_company_snags_this_month(company_id, user_id)

    if current >= limits["max_snags_per_month"]:
        raise HTTPException(
            status_code=403,
            detail=f"Monthly item limit reached ({current}/{limits['max_snags_per_month']}). Upgrade your plan to add more items."
        )


def require_feature(plan: str, feature: str, error_detail: str | None = None):
    """Raise 403 if the plan doesn't have the given feature."""
    if not has_feature(plan, feature):
        raise HTTPException(
            status_code=403,
            detail=error_detail or f"'{feature}' is not available on your current plan. Upgrade to unlock it."
        )


# check_member_limit was removed — the actual enforcement for members
# lives inline in routers/companies.py add_member (which correctly
# counts pending invites as "seats taken", something this file's old
# helper didn't). Kept as a note in case anyone greps for it.
