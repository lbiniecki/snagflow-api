"""
Plan limits — single source of truth for what each pricing tier allows.

Shape:
  limits:    numeric caps (users, projects, snags/month)
  features:  boolean feature flags (watermark, email, logo)

Everything (API, billing webhook, PDF generator, pricing screen) reads
from here. Don't duplicate this dict anywhere else.
"""
from typing import TypedDict


# Hard-coded constant — same across all plans, so not a per-plan limit.
MAX_PHOTOS_PER_SNAG = 4

# Sentinel for "unlimited". We use a large int rather than math.inf so
# JSON serialisation and `int` comparisons stay trivial.
UNLIMITED = 999_999


class PlanLimits(TypedDict):
    max_users: int
    max_projects: int
    max_snags_per_month: int


class PlanFeatures(TypedDict):
    pdf_watermark: bool   # Free plan only — adds "VOXSITE · FREE PLAN" across every page
    email_reports: bool   # Team+ — send PDF report by email
    company_logo: bool    # Starter+ — render company logo on PDF cover/header


class Plan(TypedDict):
    slug: str
    name: str
    limits: PlanLimits
    features: PlanFeatures


PLANS: dict[str, Plan] = {
    "free": {
        "slug": "free",
        "name": "Free",
        "limits": {
            "max_users": 1,
            "max_projects": 2,
            "max_snags_per_month": 20,
        },
        "features": {
            "pdf_watermark": True,
            "email_reports": False,
            "company_logo": False,
        },
    },
    "starter": {
        "slug": "starter",
        "name": "Starter",
        "limits": {
            "max_users": 3,
            "max_projects": 5,
            "max_snags_per_month": 100,
        },
        "features": {
            "pdf_watermark": False,
            "email_reports": False,
            "company_logo": True,
        },
    },
    "team": {
        "slug": "team",
        "name": "Team",
        "limits": {
            "max_users": 10,
            "max_projects": 15,
            "max_snags_per_month": 500,
        },
        "features": {
            "pdf_watermark": False,
            "email_reports": True,
            "company_logo": True,
        },
    },
    "pro": {
        "slug": "pro",
        "name": "Pro",
        "limits": {
            "max_users": 25,
            "max_projects": UNLIMITED,
            "max_snags_per_month": UNLIMITED,
        },
        "features": {
            "pdf_watermark": False,
            "email_reports": True,
            "company_logo": True,
        },
    },
    "business": {
        "slug": "business",
        "name": "Business",
        "limits": {
            "max_users": 50,
            "max_projects": UNLIMITED,
            "max_snags_per_month": UNLIMITED,
        },
        "features": {
            "pdf_watermark": False,
            "email_reports": True,
            "company_logo": True,
        },
    },
    "enterprise": {
        "slug": "enterprise",
        "name": "Enterprise",
        "limits": {
            "max_users": UNLIMITED,
            "max_projects": UNLIMITED,
            "max_snags_per_month": UNLIMITED,
        },
        "features": {
            "pdf_watermark": False,
            "email_reports": True,
            "company_logo": True,
        },
    },
}


# ─── Public helpers ─────────────────────────────────────────────

def get_plan(slug: str) -> Plan:
    """Resolve a plan by slug. Falls back to 'free' for unknown/empty values."""
    return PLANS.get((slug or "").lower(), PLANS["free"])


def get_limits(slug: str) -> PlanLimits:
    """Legacy helper — returns just the numeric limits for a plan."""
    return get_plan(slug)["limits"]


def has_feature(slug: str, feature: str) -> bool:
    """Return True if the plan has a given feature flag enabled."""
    plan = get_plan(slug)
    return bool(plan["features"].get(feature, False))


def is_unlimited(value: int) -> bool:
    """Whether a numeric limit should be treated as unlimited."""
    return value >= UNLIMITED
