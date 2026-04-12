"""
Snags router — CRUD with photo upload via Supabase Storage
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from app.models.schemas import SnagCreate, SnagUpdate, SnagResponse
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin
from app.config import settings
from typing import List, Optional
from uuid import uuid4

router = APIRouter()

BUCKET = "snag-photos"


async def upload_photo(file: UploadFile, user_id: str, snag_id: str) -> str:
    """Upload a photo to Supabase Storage and return the path."""
    ext = file.filename.split(".")[-1] if file.filename else "jpg"
    path = f"{user_id}/{snag_id}.{ext}"
    content = await file.read()

    # Check size
    if len(content) > settings.MAX_IMAGE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 10MB)")

    supabase_admin.storage.from_(BUCKET).upload(
        path, content, {"content-type": file.content_type or "image/jpeg"}
    )
    return path


def get_signed_url(path: str) -> str:
    """Generate a signed URL for a stored photo."""
    if not path:
        return None
    res = supabase_admin.storage.from_(BUCKET).create_signed_url(
        path, settings.SIGNED_URL_EXPIRY
    )
    return res.get("signedURL")


@router.get("/", response_model=List[SnagResponse])
async def list_snags(
    project_id: str,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """List snags for a project. Optional filters: status, priority."""
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

    query = (
        supabase_admin.table("snags")
        .select("*")
        .eq("project_id", project_id)
        .order("created_at", desc=True)
    )
    if status:
        query = query.eq("status", status)
    if priority:
        query = query.eq("priority", priority)

    res = query.execute()
    snags = []
    for row in res.data:
        snags.append(
            SnagResponse(
                id=row["id"],
                project_id=row["project_id"],
                note=row["note"],
                location=row.get("location"),
                status=row["status"],
                priority=row["priority"],
                photo_url=get_signed_url(row.get("photo_path")),
                created_at=row["created_at"],
                updated_at=row.get("updated_at", row["created_at"]),
            )
        )
    return snags


@router.post("/", response_model=SnagResponse, status_code=201)
async def create_snag(
    project_id: str = Form(...),
    note: str = Form(...),
    location: Optional[str] = Form(None),
    priority: str = Form("medium"),
    photo: Optional[UploadFile] = File(None),
    user: dict = Depends(get_current_user),
):
    """
    Create a new snag with optional photo upload.
    Uses multipart/form-data to support file upload.
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

    snag_id = str(uuid4())
    photo_path = None

    if photo and photo.filename:
        photo_path = await upload_photo(photo, user["id"], snag_id)

    data = {
        "id": snag_id,
        "project_id": project_id,
        "note": note,
        "location": location,
        "priority": priority,
        "status": "open",
        "photo_path": photo_path,
    }

    res = supabase_admin.table("snags").insert(data).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to create snag")

    row = res.data[0]
    return SnagResponse(
        id=row["id"],
        project_id=row["project_id"],
        note=row["note"],
        location=row.get("location"),
        status=row["status"],
        priority=row["priority"],
        photo_url=get_signed_url(row.get("photo_path")),
        created_at=row["created_at"],
        updated_at=row.get("updated_at", row["created_at"]),
    )


@router.patch("/{snag_id}", response_model=SnagResponse)
async def update_snag(
    snag_id: str,
    update: SnagUpdate,
    user: dict = Depends(get_current_user),
):
    """Update a snag's note, location, priority, or status."""
    # Verify ownership via project
    snag = (
        supabase_admin.table("snags")
        .select("*, projects!inner(user_id)")
        .eq("id", snag_id)
        .execute()
    )
    if not snag.data or snag.data[0]["projects"]["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Snag not found")

    data = {k: v for k, v in update.dict().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")

    res = (
        supabase_admin.table("snags")
        .update(data)
        .eq("id", snag_id)
        .execute()
    )
    row = res.data[0]
    return SnagResponse(
        id=row["id"],
        project_id=row["project_id"],
        note=row["note"],
        location=row.get("location"),
        status=row["status"],
        priority=row["priority"],
        photo_url=get_signed_url(row.get("photo_path")),
        created_at=row["created_at"],
        updated_at=row.get("updated_at", row["created_at"]),
    )


@router.delete("/{snag_id}", status_code=204)
async def delete_snag(snag_id: str, user: dict = Depends(get_current_user)):
    """Delete a snag and its photo."""
    snag = (
        supabase_admin.table("snags")
        .select("*, projects!inner(user_id)")
        .eq("id", snag_id)
        .execute()
    )
    if not snag.data or snag.data[0]["projects"]["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Snag not found")

    # Delete photo from storage
    photo_path = snag.data[0].get("photo_path")
    if photo_path:
        try:
            supabase_admin.storage.from_(BUCKET).remove([photo_path])
        except Exception:
            pass  # Non-critical

    supabase_admin.table("snags").delete().eq("id", snag_id).execute()
