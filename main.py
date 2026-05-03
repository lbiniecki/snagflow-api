"""
VoxSite API — main application entry point.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

from app.routers import auth, projects, snags, reports, transcribe, site_visits, companies, profiles, billing

app = FastAPI(
    title="VoxSite API",
    version="2.0.0",
    description="Mobile-first construction snagging tool",
)

# ─── CORS ─────────────────────────────────────────────────────
origins = settings.ALLOWED_ORIGINS
if isinstance(origins, str):
    import json
    try:
        origins = json.loads(origins)
    except (json.JSONDecodeError, TypeError):
        origins = [o.strip() for o in origins.split(",") if o.strip()]

# Vercel auto-generates a preview subdomain per feature branch:
#   snagflow-app-git-<branch>-snag-flow.vercel.app
#   snagflow-<hash>-snag-flow.vercel.app
# Allow them via regex so feature-branch testing doesn't require
# re-deploying the API every time a new branch is pushed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https://snagflow(-app)?-(git-[\w-]+|[\w]+)-snag-flow\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ─── Routers ──────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(projects.router, prefix="/api/projects", tags=["Projects"])
app.include_router(snags.router, prefix="/api/snags", tags=["Snags"])
app.include_router(reports.router, prefix="/api/reports", tags=["Reports"])
app.include_router(transcribe.router, prefix="/api/transcribe", tags=["Transcription"])
app.include_router(site_visits.router, prefix="/api/site-visits", tags=["Site Visits"])
app.include_router(companies.router, prefix="/api/companies", tags=["Companies"])
app.include_router(profiles.router, prefix="/api/profiles", tags=["Profiles"])
app.include_router(billing.router, prefix="/api/billing", tags=["Billing"])


@app.get("/")
async def root():
    return {"name": "VoxSite API", "version": "2.0.0", "status": "online"}


@app.on_event("startup")
async def startup():
    print("🚀 VoxSite API starting up...")


@app.on_event("shutdown")
async def shutdown():
    print("👋 VoxSite API shutting down...")
