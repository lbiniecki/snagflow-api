"""
Companies router — company settings, logo upload, member management.
Enforces user limits based on plan.
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin

router = APIRouter()

# Plan limits
PLAN_LIMITS = {
    "free": 1,
    "starter": 2,
    "team": 5,
    "pro": 10,
    "business": 25,
    "enterprise": 999,
}


class CreateCompany(BaseModel):
    name: str


class UpdateCompany(BaseModel):
    name: Optional[str] = None


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
    member_count = (members.count or 0) + 1  # +1 for owner

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
    existing = (
        supabase_admin.table("companies")
        .select("id")
        .eq("owner_id", user["id"])
        .execute()
    )
    if existing.data:
        raise HTTPException(status_code=400, detail="You already have a company")

    result = (
        supabase_admin.table("companies")
        .insert({
            "name": body.name,
            "owner_id": user["id"],
            "plan": "free",
            "max_users": PLAN_LIMITS["free"],
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


@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload company logo. Replaces existing logo. Owner only."""
    company = _get_user_company(user["id"])
    if not company or company["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner can upload a logo")

    # Validate file type
    if file.content_type not in ("image/png", "image/jpeg", "image/jpg"):
        raise HTTPException(status_code=400, detail="Only PNG and JPEG logos are supported")

    # Read file
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo must be under 5MB")

    # Upload to Supabase storage
    ext = "png" if "png" in (file.content_type or "") else "jpg"
    path = f"{company['id']}/logo.{ext}"

    # Delete old logo if exists
    if company.get("logo_path"):
        try:
            supabase_admin.storage.from_("company-logos").remove([company["logo_path"]])
        except Exception:
            pass

    # Upload new logo
    try:
        supabase_admin.storage.from_("company-logos").upload(
            path, contents, {"content-type": file.content_type}
        )
    except Exception:
        # If file exists, update it
        try:
            supabase_admin.storage.from_("company-logos").update(
                path, contents, {"content-type": file.content_type}
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to upload logo: {str(e)}")

    # Update company record
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


# ─── Member management ────────────────────────────────────────

@router.get("/members")
async def list_members(user: dict = Depends(get_current_user)):
    """List all members of the user's company."""
    company = _get_user_company(user["id"])
    if not company:
        raise HTTPException(status_code=404, detail="No company found")

    members = (
        supabase_admin.table("company_members")
        .select("*")
        .eq("company_id", company["id"])
        .execute()
    )
    return members.data


@router.post("/members")
async def add_member(
    body: InviteMember,
    user: dict = Depends(get_current_user),
):
    """Add a member to the company. Enforces license limits."""
    company = _get_user_company(user["id"])
    if not company or company["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner can add members")

    # Count current members
    members = (
        supabase_admin.table("company_members")
        .select("id", count="exact")
        .eq("company_id", company["id"])
        .execute()
    )
    current_count = (members.count or 0)
    max_users = company.get("max_users", PLAN_LIMITS.get(company.get("plan", "free"), 1))

    if current_count >= max_users:
        raise HTTPException(
            status_code=403,
            detail=f"User limit reached ({current_count}/{max_users}). Upgrade your plan to add more users."
        )

    # Look up user by email in auth
    # Note: This requires the user to already have an account
    users_res = supabase_admin.auth.admin.list_users()
    target_user = None
    for u in users_res:
        if hasattr(u, 'email') and u.email == body.email:
            target_user = u
            break

    if not target_user:
        raise HTTPException(status_code=404, detail="No user found with that email. They must create an account first.")

    # Check if already a member
    existing = (
        supabase_admin.table("company_members")
        .select("id")
        .eq("company_id", company["id"])
        .eq("user_id", str(target_user.id))
        .execute()
    )
    if existing.data:
        raise HTTPException(status_code=400, detail="User is already a member")

    result = (
        supabase_admin.table("company_members")
        .insert({
            "company_id": company["id"],
            "user_id": str(target_user.id),
            "role": body.role,
        })
        .execute()
    )
    return result.data[0] if result.data else {"message": "Member added"}


@router.delete("/members/{member_id}")
async def remove_member(
    member_id: str,
    user: dict = Depends(get_current_user),
):
    """Remove a member from the company. Owner only. Cannot remove self."""
    company = _get_user_company(user["id"])
    if not company or company["owner_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the owner can remove members")

    # Get the member
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

    if member.data["user_id"] == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    supabase_admin.table("company_members").delete().eq("id", member_id).execute()
    return {"message": "Member removed"}
