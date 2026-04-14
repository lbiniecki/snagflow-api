"""
Plan enforcement — checks whether an action is allowed under the company's plan.
"""
from fastapi import HTTPException
from app.services.supabase_client import supabase_admin
from app.services.plan_limits import get_limits
from datetime import datetime, timezone


async def get_company_plan(user_id: str) -> tuple[str, str | None]:
    """
    Get the plan and company_id for a user.
    Returns (plan, company_id). Defaults to 'free' if no company.
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


async def check_project_limit(user_id: str):
    """Raise 403 if user has hit their project limit."""
    plan, company_id = await get_company_plan(user_id)
    limits = get_limits(plan)

    count = (
        supabase_admin.table("projects")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .execute()
    )
    current = count.count if count.count is not None else len(count.data)

    if current >= limits["max_projects"]:
        raise HTTPException(
            status_code=403,
            detail=f"Project limit reached ({current}/{limits['max_projects']}). Upgrade your plan to create more projects."
        )


async def check_snag_limit(user_id: str, project_id: str):
    """Raise 403 if user has hit their monthly snag limit."""
    plan, company_id = await get_company_plan(user_id)
    limits = get_limits(plan)

    # Count snags created this month by the user
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    count = (
        supabase_admin.table("snags")
        .select("id", count="exact")
        .eq("project_id", project_id)
        .gte("created_at", month_start)
        .execute()
    )
    current = count.count if count.count is not None else len(count.data)

    if current >= limits["max_snags_per_month"]:
        raise HTTPException(
            status_code=403,
            detail=f"Monthly snag limit reached ({current}/{limits['max_snags_per_month']}). Upgrade your plan to add more snags."
        )


async def check_member_limit(user_id: str):
    """Raise 403 if company has hit their user limit."""
    plan, company_id = await get_company_plan(user_id)
    if not company_id:
        raise HTTPException(status_code=400, detail="Create a company first.")

    limits = get_limits(plan)

    count = (
        supabase_admin.table("company_members")
        .select("id", count="exact")
        .eq("company_id", company_id)
        .execute()
    )
    current = count.count if count.count is not None else len(count.data)
    # +1 for the owner who isn't in company_members
    current += 1

    if current >= limits["max_users"]:
        raise HTTPException(
            status_code=403,
            detail=f"Team member limit reached ({current}/{limits['max_users']}). Upgrade your plan to add more users."
        )
