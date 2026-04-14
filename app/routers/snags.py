"""
Snags router — CRUD with multi-photo upload via Supabase Storage.
Supports up to 4 photos per snag, visit scoping, and close-with-photo.
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin
from app.services.plan_enforcement import check_snag_limit
from app.config import settings
from typing import List, Optional
from uuid import uuid4
from pydantic import BaseModel

router = APIRouter()

BUCKET = "snag-photos"
MAX_NOTE_LEN = 2000
MAX_LOCATION_LEN = 500


# ─── Response model (replaces schemas.SnagResponse) ───────────
class SnagOut(BaseModel):
    id: str
    project_id: str
    visit_id: Optional[str] = None
    snag_no: Optional[int] = None
    note: str
    location: Optional[str] = None
    status: str
    priority: str
    photo_url: Optional[str] = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


# ─── Helpers ──────────────────────────────────────────────────

async def upload_photo(file: UploadFile, user_id: str, snag_id: str, suffix: str = "") -> str:
    """Upload a photo to Supabase Storage and return the path."""
    ext = file.filename.split(".")[-1] if file.filename else "jpg"
    path = f"{user_id}/{snag_id}{suffix}.{ext}"
    content = await file.read()

    if len(content) > settings.MAX_IMAGE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 10MB)")

    supabase_admin.storage.from_(BUCKET).upload(
        path, content, {"content-type": file.content_type or "image/jpeg"}
    )
    return path


def get_signed_url(path: str) -> Optional[str]:
    """Generate a signed URL for a stored photo."""
    if not path:
        return None
    try:
        res = supabase_admin.storage.from_(BUCKET).create_signed_url(
            path, settings.SIGNED_URL_EXPIRY
        )
        return res.get("signedURL") or res.get("signedUrl")
    except Exception:
        return None


def _row_to_snag(row: dict) -> SnagOut:
    return SnagOut(
        id=row["id"],
        project_id=row["project_id"],
        visit_id=row.get("visit_id"),
        snag_no=row.get("snag_no"),
        note=row["note"],
        location=row.get("location"),
        status=row["status"],
        priority=row["priority"],
        photo_url=get_signed_url(row.get("photo_path")),
        created_at=row["created_at"],
        updated_at=row.get("updated_at", row["created_at"]),
    )


# ─── Endpoints ────────────────────────────────────────────────

@router.get("/", response_model=List[SnagOut])
async def list_snags(
    project_id: str,
    visit_id: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """List snags for a project. Optional filters: visit_id, status, priority."""
    proj = (
        supabase_admin.table("projects")
        .select("id")
        .eq("id", project_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not proj.data:
        raise HTTPException(status_code=404, detail="Project not found")

    query = (
        supabase_admin.table("snags")
        .select("*")
        .eq("project_id", project_id)
        .order("created_at", desc=True)
    )
    if visit_id:
        query = query.eq("visit_id", visit_id)
    if status:
        query = query.eq("status", status)
    if priority:
        query = query.eq("priority", priority)

    res = query.execute()
    return [_row_to_snag(row) for row in res.data]


@router.post("/", response_model=SnagOut, status_code=201)
async def create_snag(
    project_id: str = Form(...),
    note: str = Form(...),
    location: Optional[str] = Form(None),
    priority: str = Form("medium"),
    visit_id: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    photo2: Optional[UploadFile] = File(None),
    photo3: Optional[UploadFile] = File(None),
    photo4: Optional[UploadFile] = File(None),
    user: dict = Depends(get_current_user),
):
    """
    Create a new snag with up to 4 photos.
    Blocks creation if the visit is closed.
    """
    # Verify project ownership
    proj = (
        supabase_admin.table("projects")
        .select("id")
        .eq("id", project_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not proj.data:
        raise HTTPException(status_code=404, detail="Project not found")

    # Input validation
    if len(note) > MAX_NOTE_LEN:
        raise HTTPException(status_code=400, detail=f"Description too long (max {MAX_NOTE_LEN} chars)")
    if location and len(location) > MAX_LOCATION_LEN:
        raise HTTPException(status_code=400, detail=f"Location too long (max {MAX_LOCATION_LEN} chars)")
    if priority not in ("low", "medium", "high"):
        raise HTTPException(status_code=400, detail="Priority must be low, medium, or high")

    # Plan limit check
    await check_snag_limit(user["id"], project_id)

    # Block adding snags to closed visits
    if visit_id:
        visit = (
            supabase_admin.table("site_visits")
            .select("status")
            .eq("id", visit_id)
            .single()
            .execute()
        )
        if visit.data and visit.data.get("status") == "closed":
            raise HTTPException(
                status_code=400,
                detail="Cannot add snags to a closed visit. Reopen the visit first."
            )

    snag_id = str(uuid4())

    # Upload photos
    photo_path = None
    photo_path_2 = None
    photo_path_3 = None
    photo_path_4 = None

    if photo and photo.filename:
        photo_path = await upload_photo(photo, user["id"], snag_id)
    if photo2 and photo2.filename:
        photo_path_2 = await upload_photo(photo2, user["id"], snag_id, "_2")
    if photo3 and photo3.filename:
        photo_path_3 = await upload_photo(photo3, user["id"], snag_id, "_3")
    if photo4 and photo4.filename:
        photo_path_4 = await upload_photo(photo4, user["id"], snag_id, "_4")

    data = {
        "id": snag_id,
        "project_id": project_id,
        "note": note.strip()[:MAX_NOTE_LEN],
        "location": (location or "").strip()[:MAX_LOCATION_LEN] or None,
        "priority": priority,
        "status": "open",
        "photo_path": photo_path,
        "photo_path_2": photo_path_2,
        "photo_path_3": photo_path_3,
        "photo_path_4": photo_path_4,
    }
    if visit_id:
        data["visit_id"] = visit_id

    res = supabase_admin.table("snags").insert(data).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to create snag")

    return _row_to_snag(res.data[0])


class SnagUpdate(BaseModel):
    note: Optional[str] = None
    location: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None


@router.patch("/{snag_id}", response_model=SnagOut)
async def update_snag(
    snag_id: str,
    body: SnagUpdate,
    user: dict = Depends(get_current_user),
):
    """Update a snag's note, location, priority, or status."""
    snag = (
        supabase_admin.table("snags")
        .select("*, projects!inner(user_id)")
        .eq("id", snag_id)
        .execute()
    )
    if not snag.data or snag.data[0]["projects"]["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Snag not found")

    updates = {k: v for k, v in body.dict().items() if v is not None}

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    res = (
        supabase_admin.table("snags")
        .update(updates)
        .eq("id", snag_id)
        .execute()
    )
    return _row_to_snag(res.data[0])


@router.delete("/{snag_id}", status_code=204)
async def delete_snag(snag_id: str, user: dict = Depends(get_current_user)):
    """Delete a snag and its photos."""
    snag = (
        supabase_admin.table("snags")
        .select("*, projects!inner(user_id)")
        .eq("id", snag_id)
        .execute()
    )
    if not snag.data or snag.data[0]["projects"]["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Snag not found")

    # Delete all photos from storage
    for key in ["photo_path", "photo_path_2", "photo_path_3", "photo_path_4", "rectification_photo_path"]:
        path = snag.data[0].get(key)
        if path:
            try:
                supabase_admin.storage.from_(BUCKET).remove([path])
            except Exception:
                pass

    supabase_admin.table("snags").delete().eq("id", snag_id).execute()


@router.post("/{snag_id}/close", response_model=SnagOut)
async def close_with_photo(
    snag_id: str,
    photo: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Close a snag with a rectification photo proving the fix."""
    snag = (
        supabase_admin.table("snags")
        .select("*, projects!inner(user_id)")
        .eq("id", snag_id)
        .execute()
    )
    if not snag.data or snag.data[0]["projects"]["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Snag not found")

    contents = await photo.read()
    project_id = snag.data[0]["project_id"]
    ext = "jpg" if "jpeg" in (photo.content_type or "") or "jpg" in (photo.content_type or "") else "png"
    file_path = f"{project_id}/rectification-{snag_id}.{ext}"

    try:
        supabase_admin.storage.from_(BUCKET).upload(
            file_path, contents, {"content-type": photo.content_type or "image/jpeg"}
        )
    except Exception:
        try:
            supabase_admin.storage.from_(BUCKET).update(
                file_path, contents, {"content-type": photo.content_type or "image/jpeg"}
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to upload photo: {str(e)}")

    res = (
        supabase_admin.table("snags")
        .update({
            "status": "closed",
            "rectification_photo_path": file_path,
        })
        .eq("id", snag_id)
        .execute()
    )
    return _row_to_snag(res.data[0])
