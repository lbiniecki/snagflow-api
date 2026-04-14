"""
Plan limits — defines what each pricing tier allows.
Only 3 limits: max_users, max_projects, max_snags_per_month.
All features (PDF, voice, offline, logo, viewer roles) available on all plans.
"""

PLAN_LIMITS = {
    "free": {
        "max_users": 1,
        "max_projects": 2,
        "max_snags_per_month": 20,
    },
    "starter": {
        "max_users": 3,
        "max_projects": 5,
        "max_snags_per_month": 100,
    },
    "team": {
        "max_users": 10,
        "max_projects": 15,
        "max_snags_per_month": 500,
    },
    "pro": {
        "max_users": 25,
        "max_projects": 999999,
        "max_snags_per_month": 999999,
    },
    "business": {
        "max_users": 50,
        "max_projects": 999999,
        "max_snags_per_month": 999999,
    },
    "enterprise": {
        "max_users": 999999,
        "max_projects": 999999,
        "max_snags_per_month": 999999,
    },
}


def get_limits(plan: str) -> dict:
    """Get limits for a plan. Defaults to free if unknown."""
    return PLAN_LIMITS.get(plan.lower(), PLAN_LIMITS["free"])
