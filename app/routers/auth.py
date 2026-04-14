"""
Auth router — signup, magic link login, token refresh.
Rate-limited to prevent brute force attacks.
"""
from fastapi import APIRouter, HTTPException, Request, Depends
from app.models.schemas import SignUpRequest, MagicLinkRequest, AuthResponse
from app.services.supabase_client import supabase
from app.services.auth_dep import get_current_user
from app.services.rate_limiter import rate_limit

router = APIRouter()

MAX_EMAIL_LEN = 254
MAX_PASSWORD_LEN = 128


@router.post("/signup")
async def signup(req: SignUpRequest, request: Request):
    """Register a new user with email + password."""
    rate_limit(request, max_requests=5, window_seconds=300)  # 5 signups per 5 min

    if len(req.email) > MAX_EMAIL_LEN:
        raise HTTPException(status_code=400, detail="Email too long")
    if len(req.password) > MAX_PASSWORD_LEN or len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be 6-128 characters")

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
async def magic_link(req: MagicLinkRequest, request: Request):
    """Send a magic link to the user's email."""
    rate_limit(request, max_requests=5, window_seconds=300)  # 5 magic links per 5 min

    try:
        supabase.auth.sign_in_with_otp({"email": req.email})
        return {"message": f"Magic link sent to {req.email}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login")
async def login(req: SignUpRequest, request: Request):
    """Login with email + password (returns JWT)."""
    rate_limit(request, max_requests=10, window_seconds=60)  # 10 login attempts per min

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
async def refresh(refresh_token: str, request: Request):
    """Refresh an expired access token."""
    rate_limit(request, max_requests=10, window_seconds=60)

    try:
        res = supabase.auth.refresh_session(refresh_token)
        return {
            "access_token": res.session.access_token,
            "refresh_token": res.session.refresh_token,
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail="Token refresh failed")


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Return current user info from token."""
    return {"id": user["id"], "email": user.get("email", "")}
