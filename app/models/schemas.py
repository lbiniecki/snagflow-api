"""
Pydantic schemas for request/response validation
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime
from uuid import uuid4


# ─── Auth ──────────────────────────────────────────────────────────
class SignUpRequest(BaseModel):
    email: str
    password: str

class MagicLinkRequest(BaseModel):
    email: str

class AuthResponse(BaseModel):
    access_token: str
    user_id: str
    email: str


# ─── Projects ─────────────────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    client: Optional[str] = None
    address: Optional[str] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    client: Optional[str] = None
    address: Optional[str] = None

class ProjectResponse(BaseModel):
    id: str
    name: str
    client: Optional[str]
    address: Optional[str]
    user_id: str
    snag_count: int = 0
    created_at: str


# ─── Snags ─────────────────────────────────────────────────────────
class SnagCreate(BaseModel):
    project_id: str
    note: str = Field(..., min_length=1)
    location: Optional[str] = None
    priority: Literal["low", "medium", "high"] = "medium"

class SnagUpdate(BaseModel):
    note: Optional[str] = None
    location: Optional[str] = None
    priority: Optional[Literal["low", "medium", "high"]] = None
    status: Optional[Literal["open", "closed"]] = None

class SnagResponse(BaseModel):
    id: str
    project_id: str
    note: str
    location: Optional[str]
    status: str
    priority: str
    photo_url: Optional[str]
    created_at: str
    updated_at: str


# ─── Reports ───────────────────────────────────────────────────────
class ReportRequest(BaseModel):
    project_id: str
    include_closed: bool = True
    include_photos: bool = True


# ─── Transcription ─────────────────────────────────────────────────
class TranscribeResponse(BaseModel):
    text: str
    duration: Optional[float] = None
