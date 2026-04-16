"""
Profiles router — manage user's first/last name for report signatures.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin
from app.services.email_service import send_email, render_email
from app.config import settings

router = APIRouter()


class ProfileUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None


@router.get("/me")
async def get_profile(user: dict = Depends(get_current_user)):
    """Get current user's profile."""
    try:
        res = (
            supabase_admin.table("profiles")
            .select("*")
            .eq("id", user["id"])
            .single()
            .execute()
        )
        if res.data:
            return res.data
    except Exception:
        pass

    # Profile doesn't exist — create one
    res = (
        supabase_admin.table("profiles")
        .upsert({
            "id": user["id"],
            "first_name": "",
            "last_name": "",
        })
        .execute()
    )
    return res.data[0] if res.data else {"id": user["id"], "first_name": "", "last_name": ""}


@router.put("/me")
async def update_profile(
    body: ProfileUpdate,
    user: dict = Depends(get_current_user),
):
    """Update user's first/last name."""
    updates = {}
    if body.first_name is not None:
        updates["first_name"] = body.first_name
    if body.last_name is not None:
        updates["last_name"] = body.last_name

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Upsert to handle case where profile doesn't exist yet
    data = {"id": user["id"], **updates}
    res = (
        supabase_admin.table("profiles")
        .upsert(data)
        .execute()
    )
    return res.data[0] if res.data else data


@router.post("/test-email")
async def send_test_email(user: dict = Depends(get_current_user)):
    """
    Send a test email to the authenticated user's own address.
    Used to verify the Resend integration & DNS setup after deployment.
    """
    recipient = user.get("email")
    if not recipient:
        raise HTTPException(status_code=400, detail="Your account has no email address on file")

    body = """
      <p>This is a test email from VoxSite.</p>
      <p>If you're reading this, the Resend integration is working correctly —
         DNS is configured, the API key is valid, and delivery is going through.</p>
      <p>You can safely ignore this message.</p>
    """
    html = render_email(
        title="Email delivery test",
        preheader="Verifying that VoxSite can send you emails.",
        body_html=body,
        cta_label="Open VoxSite",
        cta_url=settings.APP_URL,
    )

    ok = await send_email(
        to=recipient,
        subject="VoxSite · Email delivery test",
        html=html,
        tags=[{"name": "category", "value": "test"}],
    )

    if not ok:
        raise HTTPException(
            status_code=502,
            detail="Email send failed. Check server logs and RESEND_API_KEY configuration.",
        )

    return {
        "status": "sent",
        "to": recipient,
        "message": f"Test email sent to {recipient}. Check your inbox (and spam folder).",
    }
