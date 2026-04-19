"""
Site Visits router — manage site visits per project.
Each visit groups snags and produces its own report.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin

router = APIRouter()


class CreateVisit(BaseModel):
    project_id: str
    weather: str = ""
    inspector: str = ""
    attendees: str = ""
    access_notes: str = ""
    checker: str = ""
    reviewer: str = ""
    approver: str = ""
    closing_notes: str = ""


class UpdateVisit(BaseModel):
    weather: Optional[str] = None
    status: Optional[str] = None
    inspector: Optional[str] = None
    attendees: Optional[str] = None
    access_notes: Optional[str] = None
    checker: Optional[str] = None
    reviewer: Optional[str] = None
    approver: Optional[str] = None
    closing_notes: Optional[str] = None


@router.get("/")
async def list_visits(
    project_id: str,
    user: dict = Depends(get_current_user),
):
    """
    List all site visits for a project, newest first.

    Response includes snag_count / open_count / closed_count for each
    visit so the UI can render summary pills without a second round-trip.
    Counts are computed client-side after a single SELECT of this
    project's snags — much faster than N per-visit queries.
    """
    # Verify project ownership
    proj = (
        supabase_admin.table("projects")
        .select("id")
        .eq("id", project_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )
    if not proj.data:
        raise HTTPException(status_code=404, detail="Project not found")

    visits = (
        supabase_admin.table("site_visits")
        .select("*")
        .eq("project_id", project_id)
        .order("visit_no", desc=True)
        .execute()
    )
    visit_rows = visits.data or []

    # Fetch all snags for this project in ONE query, then group by visit_id.
    # Cheap for typical project sizes (tens to low hundreds of items);
    # switching to a per-visit count() query would be N round-trips.
    counts_by_visit: dict[str, dict[str, int]] = {}
    if visit_rows:
        try:
            snags = (
                supabase_admin.table("snags")
                .select("visit_id, status")
                .eq("project_id", project_id)
                .execute()
            )
            for row in (snags.data or []):
                vid = row.get("visit_id")
                if not vid:
                    continue
                bucket = counts_by_visit.setdefault(
                    vid, {"snag_count": 0, "open_count": 0, "closed_count": 0}
                )
                bucket["snag_count"] += 1
                if row.get("status") == "closed":
                    bucket["closed_count"] += 1
                else:
                    bucket["open_count"] += 1
        except Exception:
            # Non-fatal — if the snag query fails we still return visits
            # without counts. Frontend treats missing counts as "no pills".
            pass

    # Merge counts onto visit rows. Missing visit_ids default to zero.
    enriched = []
    for v in visit_rows:
        counts = counts_by_visit.get(v["id"], {"snag_count": 0, "open_count": 0, "closed_count": 0})
        enriched.append({**v, **counts})

    return enriched


@router.post("/")
async def create_visit(
    body: CreateVisit,
    user: dict = Depends(get_current_user),
):
    """Create a new site visit for a project. visit_no auto-increments."""
    # Verify project ownership
    proj = (
        supabase_admin.table("projects")
        .select("id")
        .eq("id", body.project_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )
    if not proj.data:
        raise HTTPException(status_code=404, detail="Project not found")

    # Calculate next visit_no for this project
    latest = (
        supabase_admin.table("site_visits")
        .select("visit_no")
        .eq("project_id", body.project_id)
        .order("visit_no", desc=True)
        .limit(1)
        .execute()
    )
    next_no = (latest.data[0]["visit_no"] + 1) if latest.data else 1

    visit = (
        supabase_admin.table("site_visits")
        .insert({
            "project_id": body.project_id,
            "visit_no": next_no,
            "weather": body.weather,
            "inspector": body.inspector or user.get("email", ""),
            "attendees": body.attendees,
            "access_notes": body.access_notes,
            "checker": body.checker,
            "reviewer": body.reviewer,
            "approver": body.approver,
            "closing_notes": body.closing_notes,
        })
        .execute()
    )
    return visit.data[0] if visit.data else visit.data


@router.patch("/{visit_id}")
async def update_visit(
    visit_id: str,
    body: UpdateVisit,
    user: dict = Depends(get_current_user),
):
    """Update a site visit (weather, status, notes, etc.)."""
    # Verify ownership through project
    visit = (
        supabase_admin.table("site_visits")
        .select("*, projects!inner(user_id)")
        .eq("id", visit_id)
        .single()
        .execute()
    )
    if not visit.data or visit.data.get("projects", {}).get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Visit not found")

    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        return visit.data

    result = (
        supabase_admin.table("site_visits")
        .update(updates)
        .eq("id", visit_id)
        .execute()
    )
    return result.data[0] if result.data else result.data


@router.post("/{visit_id}/close")
async def close_visit(
    visit_id: str,
    user: dict = Depends(get_current_user),
):
    """Close a site visit. Marks status as 'closed'."""
    visit = (
        supabase_admin.table("site_visits")
        .select("*, projects!inner(user_id)")
        .eq("id", visit_id)
        .single()
        .execute()
    )
    if not visit.data or visit.data.get("projects", {}).get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Visit not found")

    result = (
        supabase_admin.table("site_visits")
        .update({"status": "closed"})
        .eq("id", visit_id)
        .execute()
    )
    return result.data[0] if result.data else result.data


@router.post("/{visit_id}/reopen")
async def reopen_visit(
    visit_id: str,
    user: dict = Depends(get_current_user),
):
    """Reopen a closed site visit so new items can be added."""
    visit = (
        supabase_admin.table("site_visits")
        .select("*, projects!inner(user_id)")
        .eq("id", visit_id)
        .single()
        .execute()
    )
    if not visit.data or visit.data.get("projects", {}).get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Visit not found")

    result = (
        supabase_admin.table("site_visits")
        .update({"status": "open"})
        .eq("id", visit_id)
        .execute()
    )
    return result.data[0] if result.data else result.data


@router.delete("/{visit_id}")
async def delete_visit(
    visit_id: str,
    user: dict = Depends(get_current_user),
):
    """Delete a site visit and all its snags."""
    visit = (
        supabase_admin.table("site_visits")
        .select("*, projects!inner(user_id)")
        .eq("id", visit_id)
        .single()
        .execute()
    )
    if not visit.data or visit.data.get("projects", {}).get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Visit not found")

    # Delete snags first
    supabase_admin.table("snags").delete().eq("visit_id", visit_id).execute()
    # Delete visit
    supabase_admin.table("site_visits").delete().eq("id", visit_id).execute()

    return {"ok": True}
