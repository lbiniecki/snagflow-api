"""
Reports router — generate professional PDF snagging reports.
Now scoped to a site visit (or falls back to all project snags).
Downloads photos from Supabase storage and embeds them in the PDF.
Fetches company logo for branding.
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


async def _get_company_logo(user_id: str) -> bytes | None:
    """Fetch the company logo bytes for branding."""
    try:
        # Find user's company
        company = (
            supabase_admin.table("companies")
            .select("logo_path")
            .eq("owner_id", user_id)
            .limit(1)
            .execute()
        )
        if not company.data:
            # Check as member
            mem = (
                supabase_admin.table("company_members")
                .select("companies(logo_path)")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            if mem.data and mem.data[0].get("companies"):
                logo_path = mem.data[0]["companies"].get("logo_path")
            else:
                return None
        else:
            logo_path = company.data[0].get("logo_path")

        if not logo_path:
            return None

        url_res = supabase_admin.storage.from_("company-logos").create_signed_url(logo_path, 300)
        url = url_res.get("signedURL") or url_res.get("signedUrl")
        if url:
            return await _download_photo(url)
    except Exception:
        pass
    return None


@router.get("/{project_id}")
async def get_report(
    project_id: str,
    visit_id: str = Query("", description="Site visit ID (optional, generates for specific visit)"),
    include_closed: bool = True,
    include_photos: bool = True,
    weather: str = Query("", description="Weather conditions"),
    visit_no: str = Query("", description="Visit number"),
    user: dict = Depends(get_current_user),
):
    """
    Generate a PDF snagging report.
    If visit_id is provided, scopes to that visit.
    Otherwise generates for all project snags (backwards compatible).
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

    # Get visit info if scoped
    visit_data = None
    if visit_id:
        visit_res = (
            supabase_admin.table("site_visits")
            .select("*")
            .eq("id", visit_id)
            .single()
            .execute()
        )
        if visit_res.data:
            visit_data = visit_res.data
            # Override weather/visit_no from visit record if not explicitly passed
            if not weather:
                weather = visit_data.get("weather", "")
            if not visit_no:
                visit_no = str(visit_data.get("visit_no", ""))

    # Get snags — scoped to visit or whole project
    query = (
        supabase_admin.table("snags")
        .select("*")
        .eq("project_id", project_id)
        .order("created_at", desc=False)
    )
    if visit_id:
        query = query.eq("visit_id", visit_id)
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
                if photo_url:
                    img_bytes = await _download_photo(photo_url)
                    if img_bytes:
                        photo_data[s["id"]] = img_bytes
            except Exception:
                pass
        snags.append({**s, "photo_url": photo_url})

    # Fetch company logo
    logo_bytes = await _get_company_logo(user["id"])

    # Build inspector name
    inspector = user.get("email", "")
    if visit_data:
        inspector = visit_data.get("inspector", "") or inspector

    # Generate PDF with embedded photos and logo
    pdf_bytes = generate_report_pdf(
        project=proj.data,
        snags=snags,
        inspector_email=inspector,
        logo_bytes=logo_bytes,
        photo_data=photo_data,
        weather=weather,
        visit_no=visit_no,
        attendees=visit_data.get("attendees", "") if visit_data else "",
        access_notes=visit_data.get("access_notes", "") if visit_data else "",
    )

    # Return as downloadable PDF
    project_name = proj.data['name'][:30].replace(' ', '-').lower()
    visit_suffix = f"-visit-{visit_no}" if visit_no else ""
    filename = f"snagging-report-{project_name}{visit_suffix}.pdf"

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project_id}/preview")
async def preview_report(
    project_id: str,
    visit_id: str = Query("", description="Site visit ID"),
    user: dict = Depends(get_current_user),
):
    """Return report data as JSON for frontend preview."""
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

    query = (
        supabase_admin.table("snags")
        .select("*")
        .eq("project_id", project_id)
        .order("created_at", desc=False)
    )
    if visit_id:
        query = query.eq("visit_id", visit_id)

    snags_res = query.execute()

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
