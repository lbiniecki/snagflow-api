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


class UpdateVisit(BaseModel):
    weather: Optional[str] = None
    status: Optional[str] = None
    inspector: Optional[str] = None
    attendees: Optional[str] = None
    access_notes: Optional[str] = None
    checker: Optional[str] = None
    reviewer: Optional[str] = None
    approver: Optional[str] = None


@router.get("/")
async def list_visits(
    project_id: str,
    user: dict = Depends(get_current_user),
):
    """List all site visits for a project, newest first."""
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
    return visits.data


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

    visit = (
        supabase_admin.table("site_visits")
        .insert({
            "project_id": body.project_id,
            "weather": body.weather,
            "inspector": body.inspector or user.get("email", ""),
            "attendees": body.attendees,
            "access_notes": body.access_notes,
            "checker": body.checker,
            "reviewer": body.reviewer,
            "approver": body.approver,
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
    """Reopen a closed site visit so new snags can be added."""
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
