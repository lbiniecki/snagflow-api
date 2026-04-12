"""
SnagFlow API — FastAPI Backend
Site snagging SaaS tool
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.routers import snags, projects, reports, transcribe, auth
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("🚀 SnagFlow API starting...")
    yield
    # Shutdown
    print("👋 SnagFlow API shutting down...")


app = FastAPI(
    title="SnagFlow API",
    description="Site snagging SaaS — capture, store, report",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow your Vercel frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(projects.router, prefix="/api/projects", tags=["Projects"])
app.include_router(snags.router, prefix="/api/snags", tags=["Snags"])
app.include_router(reports.router, prefix="/api/reports", tags=["Reports"])
app.include_router(transcribe.router, prefix="/api/transcribe", tags=["Transcribe"])


@app.get("/")
async def root():
    return {"service": "SnagFlow API", "status": "running", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}
