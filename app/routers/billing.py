"""
Stripe billing router — checkout sessions, webhooks, customer portal.
"""
import os
import json
from typing import Optional
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin
from app.services.plan_limits import PLANS, get_plan, get_limits, is_unlimited, MAX_PHOTOS_PER_SNAG
from app.services.plan_enforcement import get_company_plan
from app.services.emails import send_subscription_confirmation_email, send_payment_failed_email
from datetime import datetime, timezone

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Environment gating for the webhook-verification fallback.
# In production we refuse to accept unsigned webhook payloads — that
# would let anyone POST a crafted "I'm now on Business tier" event.
# Local dev can set VOXSITE_ENV=development to skip signature checks
# when running against the Stripe CLI's `stripe listen`.
_STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
_VOXSITE_ENV = (os.getenv("VOXSITE_ENV") or "production").lower()
_ALLOW_UNSIGNED_WEBHOOKS = _VOXSITE_ENV == "development"

if not _STRIPE_WEBHOOK_SECRET and not _ALLOW_UNSIGNED_WEBHOOKS:
    # Fail loud at import. Railway's deploy log will catch this and
    # the deploy will fail rather than silently running with a
    # bypassable webhook endpoint.
    raise RuntimeError(
        "STRIPE_WEBHOOK_SECRET is not configured. Set it in Railway "
        "(or export VOXSITE_ENV=development for local testing)."
    )

# Price ID → plan name mapping.
#
# April 2026 refresh: completely refreshed pricing. Old VoxSite prices
# (€24/49/99/179) are archived in Stripe and intentionally absent here,
# so any dormant checkout session holding an old price ID will fail
# validation in create_checkout. That's the safe outcome — we'd rather
# reject a stale checkout than silently let someone subscribe at the
# old price.
PRICE_TO_PLAN = {
    # ── Monthly ──────────────────────────────────────────────────
    "price_1TNyLGIzCuyhGXgYJyUyNFoq": "solo",
    "price_1TNySxIzCuyhGXgY6gKc3Yxt": "starter",
    "price_1TNyUtIzCuyhGXgYZyfhzQoQ": "team",
    "price_1TNyWUIzCuyhGXgYyUrkzDZ4": "pro",
    "price_1TNyYiIzCuyhGXgYhOtdqwNy": "business",
    # ── Annual ───────────────────────────────────────────────────
    "price_1TNyPrIzCuyhGXgYvxlOVm8F": "solo",
    "price_1TNyTqIzCuyhGXgY2ma0qffR": "starter",
    "price_1TNyVVIzCuyhGXgYACbR2R44": "team",
    "price_1TNyXiIzCuyhGXgY4RbeEf6U": "pro",
    "price_1TNyZbIzCuyhGXgY2kiwRDs0": "business",
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

    Two guarantees this handler provides:
      1. Signature verification: unsigned requests are rejected in prod.
         Only local dev with VOXSITE_ENV=development can fall through
         to unsigned JSON parsing (for use with `stripe listen`).
      2. Idempotency: every Stripe event has a unique `evt_xxx` id.
         We CHECK stripe_events before dispatch, and INSERT only after
         the handler succeeds. This way a crashed handler (500) does
         NOT poison the idempotency table — Stripe's retry will get
         a fresh chance to process the event.
    """
    payload = await request.body()
    sig = request.headers.get("stripe-signature")

    # ── 1. Verify & parse ─────────────────────────────────────────
    if _STRIPE_WEBHOOK_SECRET:
        if not sig:
            # Missing signature in prod = hostile request. Reject.
            raise HTTPException(status_code=400, detail="Missing signature")
        try:
            # Verify signature against raw bytes. We discard the returned
            # StripeObject and re-parse the payload as a plain dict so all
            # downstream code can use normal dict access (.get, [...]).
            # Newer Stripe SDKs (v7+) changed StripeObject's behaviour so
            # that `.get()` on it raises KeyError instead of falling back,
            # which crashed the previous handler on every webhook.
            stripe.Webhook.construct_event(payload, sig, _STRIPE_WEBHOOK_SECRET)
        except (ValueError, stripe.error.SignatureVerificationError):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")
    elif not _ALLOW_UNSIGNED_WEBHOOKS:
        # Defensive: import-time guard should have prevented this, but
        # if config is tampered with at runtime, refuse.
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    # Parse the raw payload as a plain dict — consistent handling whether
    # or not the secret is configured, and avoids StripeObject method
    # gotchas (e.g. `.get()` not behaving like dict.get in newer SDKs).
    event = json.loads(payload)

    event_id = event.get("id")
    event_type = event.get("type")
    data = event.get("data", {}).get("object", {})

    # ── 2. Idempotency: check first, record only after success ────
    # Stripe retries events whenever they don't get a 2xx within
    # ~30s, and will occasionally re-send even on success.
    #
    # Order matters: we CHECK for existing event_id before dispatch,
    # but only INSERT after dispatch succeeds. If dispatch crashes
    # (500), nothing is recorded — Stripe retries, and we get another
    # chance to process the work. Previously we recorded first, and
    # a crashed handler would permanently lose the event because the
    # retry would see the row and skip processing.
    if event_id:
        existing = (
            supabase_admin.table("stripe_events")
            .select("event_id")
            .eq("event_id", event_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            return {"received": True, "duplicate": True}

    # ── 3. Dispatch ───────────────────────────────────────────────
    if event_type in ("checkout.session.completed", "customer.subscription.updated"):
        await _handle_subscription_change(data)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_cancelled(data)
    elif event_type == "invoice.payment_failed":
        await _handle_payment_failed(data)
    elif event_type == "invoice.payment_succeeded":
        await _handle_payment_succeeded(data)

    # ── 4. Record successful processing ───────────────────────────
    # Best-effort: if insert races with a concurrent delivery we ignore
    # it (both calls did equivalent idempotent work anyway).
    if event_id:
        try:
            supabase_admin.table("stripe_events").insert({
                "event_id": event_id,
                "event_type": event_type or "unknown",
            }).execute()
        except Exception:
            pass

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
    """
    Downgrade to free on cancellation.

    ── Enterprise guard (audit #6) ───────────────────────────────
    Enterprise customers are managed manually (plan='enterprise' set
    directly in Supabase; no matching Stripe price ID). If a Stripe
    subscription ever gets cancelled for an Enterprise account — whether
    by mistake, by us migrating them off Stripe, or by a test event — we
    do NOT auto-downgrade. Manual intervention only.

    ── Downgrade soft-lock semantics (audit #4) ──────────────────
    When downgrading to free, we intentionally do NOT delete or hide
    existing data:
      - All current team members remain in company_members.
      - All existing projects, visits, and items remain accessible.
      - Only NEW additions hit the free-plan caps (add_member refuses
        past max_users=1, check_project_limit refuses past 2 projects,
        etc.).
    This is the industry norm (Notion, Linear, Figma all soft-lock) and
    is more customer-friendly than hard truncation. If a customer
    reactivates, everything picks up where they left off.
    """
    customer_id = data.get("customer")
    if not customer_id:
        return

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

    # Enterprise guard — don't auto-downgrade manually-managed accounts.
    # Log the skip so there's a breadcrumb in Railway logs if Ops ever
    # needs to trace what happened.
    if old_plan_slug == "enterprise":
        print(f"[billing] Skipping auto-downgrade of Enterprise customer {customer_id} — manual review required")
        return

    free_max_users = get_limits("free")["max_users"]

    # Apply the downgrade. Also clear the past_due state since the sub
    # is now fully cancelled (past_due is a transient state that only
    # applies to active subscriptions being retried).
    supabase_admin.table("companies").update({
        "plan": "free",
        "max_users": free_max_users,
        "stripe_subscription_id": None,
        "subscription_status": "canceled",
        "past_due_since": None,
        "past_due_last_notified_at": None,
        "past_due_invoice_id": None,
    }).eq("id", company_row["id"]).execute()

    # Send downgrade notice (but not if they were already on free)
    if old_plan_slug != "free":
        await _send_subscription_email(
            owner_id=owner_id,
            new_plan_slug="free",
            old_plan_slug=old_plan_slug,
        )


async def _handle_payment_failed(data):
    """
    Handle invoice.payment_failed from Stripe.

    Design goals (per max-security session decisions):
      1. Flag past_due atomically, so the UI banner appears on next page load.
      2. Email the owner — BUT only once per invoice (per-invoice dedup via
         past_due_invoice_id + past_due_last_notified_at). Stripe retries the
         invoice up to 4 times; without dedup the owner would get spammed.
      3. Do NOT downgrade the plan. Members keep working normally during the
         Stripe retry window. Only when the subscription is actually cancelled
         (deleted webhook) do limits degrade.
      4. Email send failures are caught — they must never block the DB
         state transition. If we can flag past_due but not email, the UI
         banner will at least surface the problem.
    """
    customer_id = data.get("customer")
    if not customer_id:
        return

    invoice_id = data.get("id") or ""
    amount_due = data.get("amount_due")  # in minor currency units (e.g. cents)
    currency = (data.get("currency") or "").upper()
    next_payment_attempt = data.get("next_payment_attempt")  # unix ts or None

    # Look up the company by Stripe customer id.
    company = (
        supabase_admin.table("companies")
        .select("id, plan, owner_id, subscription_status, past_due_invoice_id, past_due_last_notified_at")
        .eq("stripe_customer_id", customer_id)
        .limit(1)
        .execute()
    )
    if not company.data:
        return

    company_row = company.data[0]
    company_id = company_row["id"]
    old_plan_slug = company_row.get("plan") or "free"
    owner_id = company_row.get("owner_id")

    # Enterprise guard — skip. Their billing is not through Stripe proper.
    if old_plan_slug == "enterprise":
        return

    # Decide whether to email. Email exactly once per invoice (not once per
    # retry attempt). If the invoice_id matches what we've already notified
    # about, skip the email. If it's a DIFFERENT invoice (customer had a
    # later one also fail), reset and send a fresh notification.
    already_notified_for_this_invoice = (
        company_row.get("past_due_invoice_id") == invoice_id
        and company_row.get("past_due_last_notified_at") is not None
    )

    now_iso = datetime.now(timezone.utc).isoformat()

    # Update DB first — state transition is the source of truth for the UI.
    # If past_due_since is already set (we're already in past_due state from
    # an earlier retry of this same invoice), don't overwrite it.
    update_fields = {
        "subscription_status": "past_due",
        "past_due_invoice_id": invoice_id,
    }
    if not company_row.get("past_due_since"):
        update_fields["past_due_since"] = now_iso
    # Also check: if the invoice changed, this is a fresh failure — reset
    # the "since" timestamp so the banner counts from now, not from the
    # previous invoice.
    if company_row.get("past_due_invoice_id") and company_row["past_due_invoice_id"] != invoice_id:
        update_fields["past_due_since"] = now_iso

    supabase_admin.table("companies").update(update_fields).eq("id", company_id).execute()

    # Email — best-effort, wrapped in try/except so a Resend outage doesn't
    # break the webhook response (which would trigger Stripe retries).
    if not already_notified_for_this_invoice and owner_id:
        try:
            email, first_name = _get_user_email_and_name(owner_id)
            if email:
                # Format amount like "€49.00" if we have enough info
                amount_formatted: Optional[str] = None
                if isinstance(amount_due, (int, float)) and currency:
                    symbols = {"EUR": "€", "USD": "$", "GBP": "£"}
                    sym = symbols.get(currency, currency + " ")
                    amount_formatted = f"{sym}{amount_due / 100:.2f}"

                # Format next retry date if provided
                next_retry_at: Optional[str] = None
                if isinstance(next_payment_attempt, (int, float)) and next_payment_attempt > 0:
                    try:
                        next_retry_at = datetime.fromtimestamp(
                            next_payment_attempt, tz=timezone.utc
                        ).strftime("%d %b %Y")
                    except (ValueError, OSError):
                        pass

                portal_url = f"{FRONTEND_URL}/#pricing"

                plan_display = get_plan(old_plan_slug).get("name") or old_plan_slug.title()

                sent = await send_payment_failed_email(
                    to_email=email,
                    first_name=first_name,
                    plan_name=plan_display,
                    amount_formatted=amount_formatted,
                    next_retry_at=next_retry_at,
                    portal_url=portal_url,
                )

                # Only record the notification timestamp if the send actually
                # succeeded. That way, if Resend was down and we retry via a
                # later Stripe webhook, we'll try to email again.
                if sent:
                    supabase_admin.table("companies").update({
                        "past_due_last_notified_at": now_iso,
                    }).eq("id", company_id).execute()
        except Exception as e:
            # Any email-pipeline failure — log and continue. The DB is
            # already updated, so the UI banner will surface the issue.
            print(f"[billing] payment_failed email failed for company {company_id}: {e}")


async def _handle_payment_succeeded(data):
    """
    Handle invoice.payment_succeeded from Stripe.

    When a customer's retried payment finally goes through (or any
    invoice succeeds), clear the past_due state so the banner
    disappears and the "last notified" dedup resets.

    Only acts if the company is currently past_due — a successful
    payment on an already-active subscription is a normal renewal
    and needs no action here.
    """
    customer_id = data.get("customer")
    if not customer_id:
        return

    company = (
        supabase_admin.table("companies")
        .select("id, subscription_status")
        .eq("stripe_customer_id", customer_id)
        .limit(1)
        .execute()
    )
    if not company.data:
        return

    company_row = company.data[0]

    # Only act if there's actually past_due state to clear. Normal
    # renewal payments on active subs shouldn't trigger any writes.
    if company_row.get("subscription_status") != "past_due":
        return

    supabase_admin.table("companies").update({
        "subscription_status": "active",
        "past_due_since": None,
        "past_due_last_notified_at": None,
        "past_due_invoice_id": None,
    }).eq("id", company_row["id"]).execute()


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
    tier = ["free", "solo", "starter", "team", "pro", "business", "enterprise"]
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
