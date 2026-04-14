"""
Auth router — signup, magic link login, token refresh
"""
from fastapi import APIRouter, HTTPException
from app.models.schemas import SignUpRequest, MagicLinkRequest, AuthResponse
from app.services.supabase_client import supabase

router = APIRouter()


@router.post("/signup")
async def signup(req: SignUpRequest):
    """Register a new user with email + password."""
    try:
        res = supabase.auth.sign_up({"email": req.email, "password": req.password})
        if res.user:
            return {
                "message": "Check your email to confirm your account",
                "user_id": res.user.id,
            }
        raise HTTPException(status_code=400, detail="Signup failed")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/magic-link")
async def magic_link(req: MagicLinkRequest):
    """Send a magic link to the user's email."""
    try:
        supabase.auth.sign_in_with_otp({"email": req.email})
        return {"message": f"Magic link sent to {req.email}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login")
async def login(req: SignUpRequest):
    """Login with email + password (returns JWT)."""
    try:
        res = supabase.auth.sign_in_with_password(
            {"email": req.email, "password": req.password}
        )
        return AuthResponse(
            access_token=res.session.access_token,
            user_id=res.user.id,
            email=res.user.email,
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid credentials")


@router.post("/refresh")
async def refresh(refresh_token: str):
    """Refresh an expired access token."""
    try:
        res = supabase.auth.refresh_session(refresh_token)
        return {
            "access_token": res.session.access_token,
            "refresh_token": res.session.refresh_token,
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail="Token refresh failed")
from app.services.auth_dep import get_current_user
from fastapi import Depends

@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Return current user info from token."""
    return {"id": user["id"], "email": user.get("email", "")}
