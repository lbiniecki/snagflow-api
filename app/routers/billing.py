"""
Stripe billing router — checkout sessions, webhooks, customer portal.
"""
import os
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin
from app.services.plan_limits import PLANS, get_plan, get_limits, is_unlimited, MAX_PHOTOS_PER_SNAG
from app.services.plan_enforcement import get_company_plan
from app.services.emails import send_subscription_confirmation_email
from datetime import datetime, timezone

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Price ID → plan name mapping
PRICE_TO_PLAN = {
    # Monthly
    "price_1TM9TGIzCuyhGXgYAI34UPiO": "starter",
    "price_1TM9U3IzCuyhGXgYIFVd7fs1": "team",
    "price_1TM9USIzCuyhGXgY8gYqlcMP": "pro",
    "price_1TM9UlIzCuyhGXgYCVxWXZsC": "business",
    # Annual
    "price_1TM9aIIzCuyhGXgY9TeUq3ch": "starter",
    "price_1TM9akIzCuyhGXgYmbDAHROz": "team",
    "price_1TM9bJIzCuyhGXgYEpO1hAUF": "pro",
    "price_1TM9bmIzCuyhGXgYv9aUGOGR": "business",
}

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://voxsite.app")


class CheckoutRequest(BaseModel):
    price_id: str


def _get_user_company(user_id: str):
    """Get company for user (as owner)."""
    res = (
        supabase_admin.table("companies")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


# ─── Plan matrix endpoint (used by frontend for pricing & paywalls) ───

@router.get("/plans")
async def list_plans():
    """
    Public endpoint — returns the full plan matrix.
    Frontend uses this to render the pricing screen and upgrade prompts.
    """
    return {
        "plans": list(PLANS.values()),
        "max_photos_per_snag": MAX_PHOTOS_PER_SNAG,
    }


@router.get("/plan")
async def get_my_plan(user: dict = Depends(get_current_user)):
    """
    Returns the current user's plan + current usage numbers.
    Used for showing "17 / 20 snags this month" style indicators.
    """
    plan_slug, company_id = await get_company_plan(user["id"])
    plan = get_plan(plan_slug)
    limits = plan["limits"]

    # Current usage — project count
    projects = (
        supabase_admin.table("projects")
        .select("id", count="exact")
        .eq("user_id", user["id"])
        .execute()
    )
    project_count = projects.count or 0

    # Current usage — snags this month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    snag_count = 0
    if company_id:
        # Count snags across all the user's projects this month
        user_projects = (
            supabase_admin.table("projects")
            .select("id")
            .eq("user_id", user["id"])
            .execute()
        )
        project_ids = [p["id"] for p in (user_projects.data or [])]
        if project_ids:
            snags_res = (
                supabase_admin.table("snags")
                .select("id", count="exact")
                .in_("project_id", project_ids)
                .gte("created_at", month_start)
                .execute()
            )
            snag_count = snags_res.count or 0

    # Current usage — members
    member_count = 1  # owner
    if company_id:
        members = (
            supabase_admin.table("company_members")
            .select("id", count="exact")
            .eq("company_id", company_id)
            .execute()
        )
        member_count = members.count or 1

    return {
        "plan": plan,
        "usage": {
            "projects": project_count,
            "snags_this_month": snag_count,
            "users": member_count,
        },
        "limits_reached": {
            "projects": not is_unlimited(limits["max_projects"]) and project_count >= limits["max_projects"],
            "snags_this_month": not is_unlimited(limits["max_snags_per_month"]) and snag_count >= limits["max_snags_per_month"],
            "users": not is_unlimited(limits["max_users"]) and member_count >= limits["max_users"],
        },
    }


# ─── Checkout / portal ─────────────────────────────────────────

@router.post("/checkout")
async def create_checkout(body: CheckoutRequest, user: dict = Depends(get_current_user)):
    """Create a Stripe Checkout session for a plan upgrade."""
    if body.price_id not in PRICE_TO_PLAN:
        raise HTTPException(status_code=400, detail="Invalid price ID")

    company = _get_user_company(user["id"])
    if not company:
        raise HTTPException(status_code=400, detail="Create a company first in Settings")

    # Check if company already has a Stripe customer
    stripe_customer_id = company.get("stripe_customer_id")

    if not stripe_customer_id:
        # Create Stripe customer
        customer = stripe.Customer.create(
            email=user.get("email", ""),
            metadata={
                "company_id": company["id"],
                "user_id": user["id"],
            },
        )
        stripe_customer_id = customer.id
        # Save to DB
        supabase_admin.table("companies").update(
            {"stripe_customer_id": stripe_customer_id}
        ).eq("id", company["id"]).execute()

    # Create checkout session
    session = stripe.checkout.Session.create(
        customer=stripe_customer_id,
        mode="subscription",
        line_items=[{"price": body.price_id, "quantity": 1}],
        success_url=f"{FRONTEND_URL}?checkout=success",
        cancel_url=f"{FRONTEND_URL}?checkout=cancel",
        metadata={
            "company_id": company["id"],
            "user_id": user["id"],
        },
    )

    return {"checkout_url": session.url}


@router.post("/portal")
async def create_portal(user: dict = Depends(get_current_user)):
    """Create a Stripe Customer Portal session to manage subscription."""
    company = _get_user_company(user["id"])
    if not company or not company.get("stripe_customer_id"):
        raise HTTPException(status_code=400, detail="No active subscription")

    session = stripe.billing_portal.Session.create(
        customer=company["stripe_customer_id"],
        return_url=f"{FRONTEND_URL}",
    )
    return {"portal_url": session.url}


# ─── Webhook ────────────────────────────────────────────────────

@router.post("/webhook")
async def stripe_webhook(request: Request):
    """
    Handle Stripe webhook events.
    Updates company plan on successful subscription changes.
    """
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    if webhook_secret and sig:
        try:
            event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
        except (ValueError, stripe.error.SignatureVerificationError):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")
    else:
        # No webhook secret configured — parse raw (dev mode)
        import json
        event = json.loads(payload)

    event_type = event.get("type") if isinstance(event, dict) else event.type
    data = event.get("data", {}).get("object", {}) if isinstance(event, dict) else event.data.object

    if event_type in ("checkout.session.completed", "customer.subscription.updated"):
        await _handle_subscription_change(data)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_cancelled(data)

    return {"received": True}


async def _handle_subscription_change(data):
    """
    Update company plan based on subscription.

    Fires the subscription-confirmation email only when the plan actually
    changes (new subscription or upgrade/downgrade). Silent for renewals.
    """
    customer_id = data.get("customer")
    if not customer_id:
        return

    # Get the subscription to find the price
    subscription_id = data.get("subscription") or data.get("id")
    if not subscription_id:
        return

    try:
        sub = stripe.Subscription.retrieve(subscription_id)
        price_id = sub["items"]["data"][0]["price"]["id"]
    except Exception:
        return

    new_plan_slug = PRICE_TO_PLAN.get(price_id, "free")
    max_users = get_limits(new_plan_slug)["max_users"]

    # Find company by stripe_customer_id (also grab the owner + old plan so we
    # can decide whether to send a confirmation email).
    company = (
        supabase_admin.table("companies")
        .select("id, plan, owner_id")
        .eq("stripe_customer_id", customer_id)
        .limit(1)
        .execute()
    )
    if not company.data:
        return

    company_row = company.data[0]
    old_plan_slug = company_row.get("plan") or "free"
    owner_id = company_row.get("owner_id")

    # Apply the update
    supabase_admin.table("companies").update({
        "plan": new_plan_slug,
        "max_users": max_users,
        "stripe_subscription_id": subscription_id,
    }).eq("id", company_row["id"]).execute()

    # Email confirmation only on actual change — renewals re-use the same plan
    # slug so old == new and we skip.
    if new_plan_slug != old_plan_slug and new_plan_slug != "free":
        await _send_subscription_email(
            owner_id=owner_id,
            new_plan_slug=new_plan_slug,
            old_plan_slug=old_plan_slug,
        )


async def _handle_subscription_cancelled(data):
    """Downgrade to free on cancellation."""
    customer_id = data.get("customer")
    if not customer_id:
        return

    free_max_users = get_limits("free")["max_users"]

    company = (
        supabase_admin.table("companies")
        .select("id, plan, owner_id")
        .eq("stripe_customer_id", customer_id)
        .limit(1)
        .execute()
    )
    if not company.data:
        return

    company_row = company.data[0]
    old_plan_slug = company_row.get("plan") or "free"
    owner_id = company_row.get("owner_id")

    supabase_admin.table("companies").update({
        "plan": "free",
        "max_users": free_max_users,
        "stripe_subscription_id": None,
    }).eq("id", company_row["id"]).execute()

    # Send downgrade notice (but not if they were already on free)
    if old_plan_slug != "free":
        await _send_subscription_email(
            owner_id=owner_id,
            new_plan_slug="free",
            old_plan_slug=old_plan_slug,
        )


# ─── Subscription-email helper ──────────────────────────────────

def _get_user_email_and_name(user_id: str) -> tuple[str, str]:
    """
    Look up (email, first_name) for a user.

    Tries public.profiles first — it's fast and populated by the email-sync
    trigger (see supabase-migrations/2026-04-16-profiles-email-sync.sql).
    Falls back to the auth admin API if profiles doesn't have the email yet
    (e.g. migration hasn't been run).

    Returns ("", "") on any failure so callers can branch gracefully.
    """
    try:
        res = (
            supabase_admin.table("profiles")
            .select("email, first_name")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if res.data and res.data.get("email"):
            return res.data["email"], (res.data.get("first_name") or "").strip()
    except Exception:
        pass

    # Fallback: auth admin API
    try:
        auth_res = supabase_admin.auth.admin.get_user_by_id(user_id)
        # shape varies across supabase-py versions
        user_obj = getattr(auth_res, "user", None) or auth_res
        email = getattr(user_obj, "email", None) or ""
        return email, ""
    except Exception:
        return "", ""


async def _send_subscription_email(
    *,
    owner_id: str,
    new_plan_slug: str,
    old_plan_slug: str,
):
    """Fire the subscription confirmation/change email. Best-effort."""
    if not owner_id:
        return

    email, first_name = _get_user_email_and_name(owner_id)
    if not email:
        return  # can't reach the user — silently skip

    # Lookup plan display name
    new_plan = get_plan(new_plan_slug)
    plan_name = new_plan.get("name") or new_plan_slug.title()

    # Is this an upgrade or a downgrade?
    tier = ["free", "starter", "team", "pro", "business", "enterprise"]
    try:
        is_upgrade = tier.index(new_plan_slug) >= tier.index(old_plan_slug)
    except ValueError:
        is_upgrade = True  # unknown slug — default to upgrade copy

    # Portal URL for the manage-subscription link. Stripe portal sessions are
    # short-lived, so we link to /pricing which has the "Manage subscription"
    # button rather than trying to mint a portal URL here.
    portal_url = f"{FRONTEND_URL}/#pricing"

    await send_subscription_confirmation_email(
        to_email=email,
        first_name=first_name,
        plan_name=plan_name,
        plan_slug=new_plan_slug,
        is_upgrade=is_upgrade,
        portal_url=portal_url,
    )
