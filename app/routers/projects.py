"""
Projects router — CRUD, scoped to authenticated user.
Enforces plan limits on project creation.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.models.schemas import ProjectCreate, ProjectUpdate, ProjectResponse
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin
from app.services.plan_enforcement import check_project_limit, get_company_plan
from typing import List

router = APIRouter()

# Input limits
MAX_NAME_LEN = 200
MAX_FIELD_LEN = 500


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
    """Create a new project. Enforces plan project limit."""
    # Input validation
    if len(project.name or "") > MAX_NAME_LEN:
        raise HTTPException(status_code=400, detail=f"Project name too long (max {MAX_NAME_LEN} chars)")
    if len(project.client or "") > MAX_FIELD_LEN:
        raise HTTPException(status_code=400, detail=f"Client name too long (max {MAX_FIELD_LEN} chars)")
    if len(project.address or "") > MAX_FIELD_LEN:
        raise HTTPException(status_code=400, detail=f"Address too long (max {MAX_FIELD_LEN} chars)")

    # Plan limit check
    await check_project_limit(user["id"])

    # Resolve which company this project belongs to. For solo free
    # users this returns (None) and company_id stays NULL, matching
    # the solo counting path in plan_enforcement.
    _plan, company_id = await get_company_plan(user["id"])

    data = {
        "name": project.name.strip()[:MAX_NAME_LEN],
        "client": (project.client or "").strip()[:MAX_FIELD_LEN],
        "address": (project.address or "").strip()[:MAX_FIELD_LEN],
        "user_id": user["id"],
        "company_id": company_id,
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

    # Validate lengths
    if "name" in data and len(data["name"]) > MAX_NAME_LEN:
        raise HTTPException(status_code=400, detail=f"Name too long (max {MAX_NAME_LEN} chars)")
    if "client" in data and len(data["client"]) > MAX_FIELD_LEN:
        raise HTTPException(status_code=400, detail=f"Client too long (max {MAX_FIELD_LEN} chars)")
    if "address" in data and len(data["address"]) > MAX_FIELD_LEN:
        raise HTTPException(status_code=400, detail=f"Address too long (max {MAX_FIELD_LEN} chars)")

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

    # Delete snags, site visits, then the project (cascade)
    supabase_admin.table("snags").delete().eq("project_id", project_id).execute()
    supabase_admin.table("site_visits").delete().eq("project_id", project_id).execute()
    supabase_admin.table("projects").delete().eq("id", project_id).execute()
