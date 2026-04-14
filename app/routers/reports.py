"""
Reports router — generate professional PDF snagging reports
Downloads photos from Supabase storage and embeds them in the PDF.
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from app.services.auth_dep import get_current_user
from app.services.supabase_client import supabase_admin
from app.services.report_generator import generate_report_pdf
from app.config import settings
from io import BytesIO

router = APIRouter()


async def _download_photo(url: str) -> bytes | None:
    """Download photo bytes from a signed Supabase URL."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.content
    except Exception:
        pass
    return None


@router.get("/{project_id}")
async def get_report(
    project_id: str,
    include_closed: bool = True,
    include_photos: bool = True,
    weather: str = Query("", description="Weather conditions"),
    visit_no: str = Query("", description="Visit number"),
    user: dict = Depends(get_current_user),
):
    """
    Generate a PDF snagging report for a project.
    Returns a downloadable PDF file with embedded photos.
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

    # Generate signed URLs and download photo bytes
    snags = []
    photo_data: dict[str, bytes] = {}

    for s in snags_res.data:
        photo_url = None
        if include_photos and s.get("photo_path"):
            try:
                url_res = supabase_admin.storage.from_("snag-photos").create_signed_url(
                    s["photo_path"], 300
                )
                photo_url = url_res.get("signedURL") or url_res.get("signedUrl")
                # Download the actual image bytes for PDF embedding
                if photo_url:
                    img_bytes = await _download_photo(photo_url)
                    if img_bytes:
                        photo_data[s["id"]] = img_bytes
            except Exception:
                pass
        snags.append({**s, "photo_url": photo_url})

    # Generate PDF with embedded photos
    pdf_bytes = generate_report_pdf(
        project=proj.data,
        snags=snags,
        inspector_email=user["email"],
        photo_data=photo_data,
        weather=weather,
        visit_no=visit_no,
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
