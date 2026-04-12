"""
Reports router — generate professional PDF snagging reports
Uses Jinja2 HTML template → WeasyPrint PDF conversion
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin
from app.services.report_generator import generate_report_pdf
from app.config import settings
from io import BytesIO

router = APIRouter()


@router.get("/{project_id}")
async def get_report(
    project_id: str,
    include_closed: bool = True,
    include_photos: bool = True,
    user: dict = Depends(get_current_user),
):
    """
    Generate a PDF snagging report for a project.
    Returns a downloadable PDF file.
    """
    # Get project
    proj = (
        supabase_admin.table("projects")
        .select("*")
        .eq("id", project_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )
    if not proj.data:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get snags
    query = (
        supabase_admin.table("snags")
        .select("*")
        .eq("project_id", project_id)
        .order("created_at", desc=False)
    )
    if not include_closed:
        query = query.eq("status", "open")

    snags_res = query.execute()

    # Generate signed URLs for photos
    snags = []
    for s in snags_res.data:
        photo_url = None
        if include_photos and s.get("photo_path"):
            try:
                url_res = supabase_admin.storage.from_("snag-photos").create_signed_url(
                    s["photo_path"], 300
                )
                photo_url = url_res.get("signedURL")
            except Exception:
                pass
        snags.append({**s, "photo_url": photo_url})

    # Generate PDF
    pdf_bytes = generate_report_pdf(
        project=proj.data,
        snags=snags,
        inspector_email=user["email"],
    )

    # Return as downloadable PDF
    filename = f"snagging-report-{proj.data['name'][:30].replace(' ', '-').lower()}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project_id}/preview")
async def preview_report(
    project_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Return report data as JSON for frontend preview.
    """
    proj = (
        supabase_admin.table("projects")
        .select("*")
        .eq("id", project_id)
        .eq("user_id", user["id"])
        .single()
        .execute()
    )
    if not proj.data:
        raise HTTPException(status_code=404, detail="Project not found")

    snags_res = (
        supabase_admin.table("snags")
        .select("*")
        .eq("project_id", project_id)
        .order("created_at", desc=False)
        .execute()
    )

    open_snags = [s for s in snags_res.data if s["status"] == "open"]
    closed_snags = [s for s in snags_res.data if s["status"] == "closed"]
    high_priority = [s for s in open_snags if s["priority"] == "high"]

    return {
        "project": proj.data,
        "summary": {
            "total": len(snags_res.data),
            "open": len(open_snags),
            "closed": len(closed_snags),
            "high_priority": len(high_priority),
        },
        "snags": snags_res.data,
    }
