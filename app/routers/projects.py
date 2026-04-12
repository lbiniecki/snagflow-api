"""
Projects router — CRUD, scoped to authenticated user
"""
from fastapi import APIRouter, Depends, HTTPException
from app.models.schemas import ProjectCreate, ProjectUpdate, ProjectResponse
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin
from typing import List

router = APIRouter()


@router.get("/", response_model=List[ProjectResponse])
async def list_projects(user: dict = Depends(get_current_user)):
    """List all projects for the current user."""
    res = (
        supabase_admin.table("projects")
        .select("*, snags(count)")
        .eq("user_id", user["id"])
        .order("created_at", desc=True)
        .execute()
    )
    projects = []
    for row in res.data:
        projects.append(
            ProjectResponse(
                id=row["id"],
                name=row["name"],
                client=row.get("client"),
                address=row.get("address"),
                user_id=row["user_id"],
                snag_count=row.get("snags", [{}])[0].get("count", 0)
                if row.get("snags")
                else 0,
                created_at=row["created_at"],
            )
        )
    return projects


@router.post("/", response_model=ProjectResponse, status_code=201)
async def create_project(
    project: ProjectCreate, user: dict = Depends(get_current_user)
):
    """Create a new project."""
    data = {
        "name": project.name,
        "client": project.client,
        "address": project.address,
        "user_id": user["id"],
    }
    res = supabase_admin.table("projects").insert(data).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to create project")
    row = res.data[0]
    return ProjectResponse(
        id=row["id"],
        name=row["name"],
        client=row.get("client"),
        address=row.get("address"),
        user_id=row["user_id"],
        snag_count=0,
        created_at=row["created_at"],
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, user: dict = Depends(get_current_user)):
    """Get a single project by ID (must belong to user)."""
    res = (
        supabase_admin.table("projects")
        .select("*, snags(count)")
        .eq("id", project_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Project not found")
    row = res.data
    return ProjectResponse(
        id=row["id"],
        name=row["name"],
        client=row.get("client"),
        address=row.get("address"),
        user_id=row["user_id"],
        snag_count=row.get("snags", [{}])[0].get("count", 0)
        if row.get("snags")
        else 0,
        created_at=row["created_at"],
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    update: ProjectUpdate,
    user: dict = Depends(get_current_user),
):
    """Update a project."""
    # Verify ownership
    existing = (
        supabase_admin.table("projects")
        .select("id")
        .eq("id", project_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail="Project not found")

    data = {k: v for k, v in update.dict().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")

    res = (
        supabase_admin.table("projects")
        .update(data)
        .eq("id", project_id)
        .execute()
    )
    row = res.data[0]
    return ProjectResponse(
        id=row["id"],
        name=row["name"],
        client=row.get("client"),
        address=row.get("address"),
        user_id=row["user_id"],
        snag_count=0,
        created_at=row["created_at"],
    )


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, user: dict = Depends(get_current_user)):
    """Delete a project and all its snags."""
    existing = (
        supabase_admin.table("projects")
        .select("id")
        .eq("id", project_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail="Project not found")

    # Delete snags first (cascade)
    supabase_admin.table("snags").delete().eq("project_id", project_id).execute()
    supabase_admin.table("projects").delete().eq("id", project_id).execute()
