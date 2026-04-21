"""
Companies router — company settings, logo upload, member management.
Enforces user limits based on plan.

FIXED: add_member now handles both existing and non-existing users.
NEW: /join endpoint for auto-joining on signup if pending invite exists.
"""
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin
from app.services.plan_limits import get_limits
from app.services.emails import send_team_invite_email

router = APIRouter()


def _get_profile_name(user_id: str) -> str:
    """Resolve 'first last' from the profiles table. Returns '' if missing."""
    try:
        res = (
            supabase_admin.table("profiles")
            .select("first_name, last_name")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if res.data:
            first = (res.data.get("first_name") or "").strip()
            last = (res.data.get("last_name") or "").strip()
            return f"{first} {last}".strip()
    except Exception:
        pass
    return ""


class CreateCompany(BaseModel):
    name: str


class UpdateCompany(BaseModel):
    name: Optional[str] = None
    # ── Report settings (Phase 1) ────────────────────────────────
    # All optional — PATCH accepts any subset. The hex-colour check,
    # photos_per_page {1,2,4} constraint, and title_align {center,left}
    # check are enforced at the DB level (see migrations), so we just
    # pass through here.
    report_brand_colour: Optional[str] = None
    report_footer_text: Optional[str] = None
    report_include_rectification: Optional[bool] = None
    report_include_cover_page: Optional[bool] = None
    report_photos_per_page: Optional[int] = None
    report_title_align: Optional[str] = None

class InviteMember(BaseModel):
    email: str
    role: str = "member"


def _get_user_company(user_id: str):
    """Get the company the user belongs to (as owner or member)."""
    # Check if owner
    res = (
        supabase_admin.table("companies")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]

    # Check if member
    mem = (
        supabase_admin.table("company_members")
        .select("company_id, companies(*)")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if mem.data and mem.data[0].get("companies"):
        return mem.data[0]["companies"]

    return None


# ─── Company info ──────────────────────────────────────────────

@router.get("/me")
async def get_my_company(user: dict = Depends(get_current_user)):
    """Get the current user's company, or null if none."""
    company = _get_user_company(user["id"])
    if not company:
        return None

    # Count current members
    members = (
        supabase_admin.table("company_members")
        .select("id", count="exact")
        .eq("company_id", company["id"])
        .execute()
    )
    member_count = members.count or 0

    return {
        **company,
        "member_count": member_count,
        "is_owner": company["owner_id"] == user["id"],
    }


@router.post("/")
async def create_company(
    body: CreateCompany,
    user: dict = Depends(get_current_user),
):
    """Create a company. Each user can only own one company."""
    # Check if user already belongs to any company (as owner OR member)
    existing_company = _get_user_company(user["id"])
    if existing_company:
        raise HTTPException(
            status_code=400,
            detail="You already belong to a company. Leave your current company first."
        )

    result = (
        supabase_admin.table("companies")
        .insert({
            "name": body.name,
            "owner_id": user["id"],
            "plan": "free",
            "max_users": get_limits("free")["max_users"],
        })
        .execute()
    )
    company = result.data[0]

    # Add owner as first member
    supabase_admin.table("company_members").insert({
        "company_id": company["id"],
        "user_id": user["id"],
        "role": "owner",
    }).execute()

    return company


@router.patch("/me")
async def update_company(
    body: UpdateCompany,
    user: dict = Depends(get_current_user),
):
    """Update company settings. Owner only."""
    company = _get_user_company(user["id"])
    if not company or company["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner can update company settings")

    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        return company

    result = (
        supabase_admin.table("companies")
        .update(updates)
        .eq("id", company["id"])
        .execute()
    )
    return result.data[0] if result.data else company


# ─── Logo ──────────────────────────────────────────────────────

@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload company logo. Replaces existing logo. Owner only."""
    company = _get_user_company(user["id"])
    if not company or company["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner can upload a logo")

    if file.content_type not in ("image/png", "image/jpeg", "image/jpg"):
        raise HTTPException(status_code=400, detail="Only PNG and JPEG logos are supported")

    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo must be under 5MB")

    ext = "png" if "png" in (file.content_type or "") else "jpg"
    path = f"{company['id']}/logo.{ext}"

    if company.get("logo_path"):
        try:
            supabase_admin.storage.from_("company-logos").remove([company["logo_path"]])
        except Exception:
            pass

    try:
        supabase_admin.storage.from_("company-logos").upload(
            path, contents, {"content-type": file.content_type}
        )
    except Exception:
        try:
            supabase_admin.storage.from_("company-logos").update(
                path, contents, {"content-type": file.content_type}
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to upload logo: {str(e)}")

    supabase_admin.table("companies").update({"logo_path": path}).eq("id", company["id"]).execute()
    return {"logo_path": path, "message": "Logo uploaded successfully"}


@router.delete("/logo")
async def delete_logo(user: dict = Depends(get_current_user)):
    """Remove company logo. Owner only."""
    company = _get_user_company(user["id"])
    if not company or company["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner can delete the logo")

    if company.get("logo_path"):
        try:
            supabase_admin.storage.from_("company-logos").remove([company["logo_path"]])
        except Exception:
            pass

    supabase_admin.table("companies").update({"logo_path": None}).eq("id", company["id"]).execute()
    return {"message": "Logo removed"}


# ─── Member management (FIXED) ────────────────────────────────

@router.get("/members")
async def list_members(user: dict = Depends(get_current_user)):
    """List all members of the user's company with their profile info."""
    company = _get_user_company(user["id"])
    if not company:
        raise HTTPException(status_code=404, detail="No company found")

    # Fetch members (no join — profiles FK may not exist)
    members = (
        supabase_admin.table("company_members")
        .select("id, user_id, role, created_at")
        .eq("company_id", company["id"])
        .execute()
    )

    if not members.data:
        return []

    # Fetch profiles separately for all member user_ids
    user_ids = [m["user_id"] for m in members.data]
    profiles_map = {}
    try:
        profiles_res = (
            supabase_admin.table("profiles")
            .select("id, first_name, last_name, email")
            .in_("id", user_ids)
            .execute()
        )
        for p in (profiles_res.data or []):
            profiles_map[p["id"]] = p
    except Exception:
        pass  # If profiles table doesn't exist or query fails, continue without names

    # Merge
    result = []
    for m in members.data:
        profile = profiles_map.get(m["user_id"], {})
        result.append({
            "id": m["id"],
            "user_id": m["user_id"],
            "role": m["role"],
            "email": profile.get("email", ""),
            "full_name": f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip(),
            "created_at": m["created_at"],
        })

    return result


@router.get("/pending-invites")
async def list_pending_invites(user: dict = Depends(get_current_user)):
    """List pending invites for the user's company."""
    company = _get_user_company(user["id"])
    if not company:
        raise HTTPException(status_code=404, detail="No company found")

    invites = (
        supabase_admin.table("company_invites")
        .select("*")
        .eq("company_id", company["id"])
        .eq("status", "pending")
        .order("created_at", desc=True)
        .execute()
    )

    return invites.data or []


@router.post("/members")
async def add_member(
    body: InviteMember,
    user: dict = Depends(get_current_user),
):
    """
    Invite a member to the company.
    
    - If user with that email exists in auth → add directly to company_members
    - If user doesn't exist yet → create a pending invite in company_invites
    
    Either way, enforces license limits BEFORE adding.
    """
    company = _get_user_company(user["id"])
    if not company or company["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner can add members")

    # Normalize email — Supabase stores auth.users.email lowercased internally,
    # and the JWT that /join checks against also carries the lowercased form.
    # We normalize here once so every downstream lookup and the invite row
    # itself use the canonical form. Fixes case-mismatch bugs where an invite
    # created with "User@Example.com" would never auto-join the user who
    # signed up (since JWT would read "user@example.com").
    body.email = (body.email or "").strip().lower()
    if not body.email:
        raise HTTPException(status_code=400, detail="Email is required")

    # ── Enforce license limits ─────────────────────────────────
    current_members = (
        supabase_admin.table("company_members")
        .select("id", count="exact")
        .eq("company_id", company["id"])
        .execute()
    )
    pending_invites = (
        supabase_admin.table("company_invites")
        .select("id", count="exact")
        .eq("company_id", company["id"])
        .eq("status", "pending")
        .execute()
    )
    # company_members already includes the owner (added at create time),
    # so we don't add +1 here. Pending invites count as "seats used".
    current_count = (current_members.count or 0) + (pending_invites.count or 0)
    max_users = get_limits(company.get("plan", "free"))["max_users"]

    if current_count >= max_users:
        raise HTTPException(
            status_code=403,
            detail=f"User limit reached ({current_count}/{max_users}). Upgrade your plan to add more users."
        )

    # ── Check if already a member ──────────────────────────────
    # Search auth users for this email — compare lowercased since Supabase
    # stores it that way.
    target_user = None
    try:
        users_res = supabase_admin.auth.admin.list_users()
        for u in users_res:
            if hasattr(u, 'email') and (u.email or "").lower() == body.email:
                target_user = u
                break
    except Exception:
        pass

    if target_user:
        # User exists in auth — check if already a member
        existing = (
            supabase_admin.table("company_members")
            .select("id")
            .eq("company_id", company["id"])
            .eq("user_id", str(target_user.id))
            .execute()
        )
        if existing.data:
            raise HTTPException(status_code=400, detail="User is already a member")

        # Check if user belongs to another company
        other_company = _get_user_company(str(target_user.id))
        if other_company and other_company["id"] != company["id"]:
            raise HTTPException(
                status_code=400,
                detail="This user already belongs to another company"
            )

        # Add directly to company_members
        result = (
            supabase_admin.table("company_members")
            .insert({
                "company_id": company["id"],
                "user_id": str(target_user.id),
                "role": body.role,
            })
            .execute()
        )

        # Send notification email (best-effort — don't fail the invite if email fails)
        inviter_name = _get_profile_name(user["id"])
        await send_team_invite_email(
            to_email=body.email,
            company_name=company["name"],
            inviter_name=inviter_name,
            inviter_email=user.get("email", ""),
            is_new_user=False,
        )

        return {
            "status": "added",
            "message": f"{body.email} has been added to your team",
            "member": result.data[0] if result.data else None,
        }

    else:
        # User does NOT exist yet — create their account (pre-confirmed,
        # no password) and send a single branded invite email with a setup
        # link. No Supabase confirmation email, no separate signup step.
        #
        # Check if already invited
        existing_invite = (
            supabase_admin.table("company_invites")
            .select("id")
            .eq("company_id", company["id"])
            .eq("email", body.email)
            .eq("status", "pending")
            .execute()
        )
        if existing_invite.data:
            raise HTTPException(status_code=400, detail="This email already has a pending invite")

        # Generate a one-time setup token
        setup_token = secrets.token_urlsafe(36)

        # Create the auth user — pre-confirmed, no password.
        # The setup_token is stored in user_metadata so the
        # /auth/setup-account endpoint can verify it later.
        try:
            new_user = supabase_admin.auth.admin.create_user({
                "email": body.email,
                "email_confirm": True,
                "user_metadata": {
                    "setup_token": setup_token,
                    "needs_password": True,
                    "invited_to_company": company["name"],
                },
            })
        except Exception as e:
            err_msg = str(e).lower()
            if "already" in err_msg or "exists" in err_msg or "duplicate" in err_msg:
                raise HTTPException(
                    status_code=400,
                    detail="An account with this email already exists. Try adding them as an existing user.",
                )
            raise HTTPException(status_code=500, detail=f"Failed to create account: {e}")

        # Create invite row for tracking / revocation
        token = secrets.token_urlsafe(36)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()

        supabase_admin.table("company_invites").insert({
            "company_id": company["id"],
            "email": body.email,
            "role": body.role,
            "invited_by": user["id"],
            "token": token,
            "status": "pending",
            "expires_at": expires_at,
        }).execute()

        # Pre-add them to the company so they're a member the moment they
        # set their password (no need for /join auto-match).
        user_id = new_user.user.id if hasattr(new_user, "user") and new_user.user else None
        if user_id:
            try:
                supabase_admin.table("company_members").insert({
                    "company_id": company["id"],
                    "user_id": user_id,
                    "role": body.role or "member",
                }).execute()
            except Exception:
                pass  # Non-fatal — /join will catch it on first login

        # Send the branded invite email with the setup link
        inviter_name = _get_profile_name(user["id"])
        await send_team_invite_email(
            to_email=body.email,
            company_name=company["name"],
            inviter_name=inviter_name,
            inviter_email=user.get("email", ""),
            is_new_user=True,
            setup_token=setup_token,
        )

        return {
            "status": "invited",
            "message": f"Invite sent to {body.email}. They'll join your team when they sign up.",
        }


@router.delete("/members/{member_id}")
async def remove_member(
    member_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Remove a member from the company AND fully delete their Supabase
    account (Auth user, profile, any lingering invite rows, members row).
    Owner-only. Cannot remove self.

    This is intentionally destructive — clicking the × next to a team
    member nukes their account everywhere. This matches the current app
    model where a user can only belong to one company: after being
    removed from their sole company they have no use-case left, and
    leaving their Auth record behind would be both a GDPR compliance
    risk and a source of user confusion ("why can I still log in if
    I was kicked off the team?"). The frontend MUST show a confirmation
    dialog before calling this endpoint.

    Deletion order matters:
      1. Drop the company_members row (removes the FK to the Auth user
         so subsequent deletes don't cascade-cause issues).
      2. Delete any company_invites rows for this email (the user may
         have been added via the pending-invite path).
      3. Delete the profiles row (gets us the email before we delete
         the Auth user, since Auth-user-email is the only way to find
         those invites if profile.email got out of sync).
      4. Delete the Supabase Auth user last — irreversible.

    Each step is wrapped in try/except. A partial failure leaves the
    user in an inconsistent state (e.g. Auth user deleted but profile
    remains) but this is preferable to leaving them fully present when
    the owner intended to remove them. Errors are logged.
    """
    company = _get_user_company(user["id"])
    if not company or company["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner can remove members")

    member = (
        supabase_admin.table("company_members")
        .select("*")
        .eq("id", member_id)
        .eq("company_id", company["id"])
        .single()
        .execute()
    )
    if not member.data:
        raise HTTPException(status_code=404, detail="Member not found")

    target_user_id = member.data["user_id"]
    if target_user_id == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    # Grab the email before we start deleting — we need it to sweep
    # any invite rows, and to log a clean message. Try profiles first,
    # fall back to Auth if the profile row is already missing.
    target_email = ""
    try:
        prof_res = (
            supabase_admin.table("profiles")
            .select("email")
            .eq("id", target_user_id)
            .single()
            .execute()
        )
        if prof_res.data:
            target_email = (prof_res.data.get("email") or "").strip().lower()
    except Exception:
        pass

    if not target_email:
        try:
            auth_user = supabase_admin.auth.admin.get_user_by_id(target_user_id)
            if auth_user and getattr(auth_user, "user", None):
                target_email = (auth_user.user.email or "").strip().lower()
        except Exception:
            pass

    # 1. Drop the company_members row
    try:
        supabase_admin.table("company_members").delete().eq("id", member_id).execute()
    except Exception as e:
        print(f"[remove_member] Failed to delete company_members row {member_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove member")

    # 2. Delete any pending/accepted invite rows for this email within
    #    this company. Case-insensitive match — older invites may have
    #    mixed-case emails.
    if target_email:
        try:
            supabase_admin.table("company_invites").delete().ilike(
                "email", target_email
            ).eq("company_id", company["id"]).execute()
        except Exception as e:
            print(f"[remove_member] Failed to delete invites for {target_email}: {e}")

    # 3. Delete the profiles row
    try:
        supabase_admin.table("profiles").delete().eq("id", target_user_id).execute()
    except Exception as e:
        print(f"[remove_member] Failed to delete profile {target_user_id}: {e}")

    # 4. Delete the Supabase Auth user — IRREVERSIBLE
    try:
        supabase_admin.auth.admin.delete_user(target_user_id)
    except Exception as e:
        print(f"[remove_member] Failed to delete auth user {target_user_id}: {e}")
        # Don't raise — the member is already gone from the company; the
        # Auth leftover is a follow-up cleanup problem, not a blocker.

    return {"message": "Member removed and account deleted"}


@router.delete("/invites/{invite_id}")
async def revoke_invite(
    invite_id: str,
    user: dict = Depends(get_current_user),
):
    """Revoke a pending invite. Owner only."""
    company = _get_user_company(user["id"])
    if not company or company["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner can revoke invites")

    result = (
        supabase_admin.table("company_invites")
        .update({"status": "revoked"})
        .eq("id", invite_id)
        .eq("company_id", company["id"])
        .eq("status", "pending")
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Invite not found or already processed")

    return {"message": "Invite revoked"}


# ─── Auto-join on login (NEW) ─────────────────────────────────

@router.post("/join")
async def auto_join_company(user: dict = Depends(get_current_user)):
    """
    Called after login/signup. Checks if there's a pending invite
    for this user's email and auto-joins them to the company.
    
    Returns:
      - The company if joined successfully
      - null if no pending invite found
    """
    user_email = user.get("email")
    if not user_email:
        return None
    user_email_normalized = user_email.strip().lower()

    # Already in a company?
    existing = _get_user_company(user["id"])
    if existing:
        # The user was pre-added to company_members by add_member's
        # "new user" branch, but the matching company_invites row is
        # still sitting at status='pending'. Without this sweep the UI
        # shows the user twice: once as an active member and once as a
        # pending invite that never resolves. Mark any such invites
        # accepted now so the pending list stays clean.
        try:
            supabase_admin.table("company_invites").update(
                {"status": "accepted"}
            ).ilike("email", user_email_normalized).eq("status", "pending").execute()
        except Exception:
            pass  # Non-fatal — a stale invite row is cosmetic, not blocking
        return {"status": "already_member", "company": existing}

    # Check for pending invites matching this email (case-insensitive — invites
    # created by older code paths may have mixed-case emails).
    invite_res = (
        supabase_admin.table("company_invites")
        .select("*")
        .ilike("email", user_email_normalized)
        .eq("status", "pending")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if not invite_res.data:
        return None

    invite = invite_res.data[0]

    # Check if invite expired
    expires_at = invite.get("expires_at", "")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp:
                supabase_admin.table("company_invites").update(
                    {"status": "expired"}
                ).eq("id", invite["id"]).execute()
                return None
        except Exception:
            pass

    # Join the company!
    supabase_admin.table("company_members").insert({
        "company_id": invite["company_id"],
        "user_id": user["id"],
        "role": invite.get("role", "member"),
    }).execute()

    # Mark invite as accepted
    supabase_admin.table("company_invites").update(
        {"status": "accepted"}
    ).eq("id", invite["id"]).execute()

    # Get company name
    company_name = "the team"
    try:
        c_res = (
            supabase_admin.table("companies")
            .select("name")
            .eq("id", invite["company_id"])
            .single()
            .execute()
        )
        if c_res.data:
            company_name = c_res.data["name"]
    except Exception:
        pass

    return {
        "status": "joined",
        "message": f"You've been added to {company_name}!",
        "company_id": invite["company_id"],
    }
