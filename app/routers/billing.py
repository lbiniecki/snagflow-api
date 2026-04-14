"""
Stripe billing router — checkout sessions, webhooks, customer portal.
"""
import os
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin

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

PLAN_MAX_USERS = {
    "free": 1,
    "starter": 3,
    "team": 10,
    "pro": 25,
    "business": 50,
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
    """Update company plan based on subscription."""
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

    plan = PRICE_TO_PLAN.get(price_id, "free")
    max_users = PLAN_MAX_USERS.get(plan, 1)

    # Find company by stripe_customer_id
    company = (
        supabase_admin.table("companies")
        .select("id")
        .eq("stripe_customer_id", customer_id)
        .limit(1)
        .execute()
    )
    if company.data:
        supabase_admin.table("companies").update({
            "plan": plan,
            "max_users": max_users,
            "stripe_subscription_id": subscription_id,
        }).eq("id", company.data[0]["id"]).execute()


async def _handle_subscription_cancelled(data):
    """Downgrade to free on cancellation."""
    customer_id = data.get("customer")
    if not customer_id:
        return

    company = (
        supabase_admin.table("companies")
        .select("id")
        .eq("stripe_customer_id", customer_id)
        .limit(1)
        .execute()
    )
    if company.data:
        supabase_admin.table("companies").update({
            "plan": "free",
            "max_users": 1,
            "stripe_subscription_id": None,
        }).eq("id", company.data[0]["id"]).execute()
