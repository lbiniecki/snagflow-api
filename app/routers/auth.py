"""
Auth router — signup, magic link login, token refresh.
Rate-limited to prevent brute force attacks.

Behaviour around Supabase email confirmation (see VOXSITE_EMAIL_SETUP.md §7):
  - Confirmation OFF: signup returns the user immediately. Our welcome email
    fires and the user can log in right away.
  - Confirmation ON: signup still returns 200 but the user must click the
    Supabase-sent confirmation link before login works. Login attempts before
    confirmation return a clear "please confirm" error, not a generic
    "invalid credentials".
"""
from fastapi import APIRouter, HTTPException, Request, Depends
from app.models.schemas import SignUpRequest, MagicLinkRequest, SetupAccountRequest, AuthResponse
from app.services.supabase_client import supabase, supabase_admin
from app.services.auth_dep import get_current_user
from app.services.rate_limiter import rate_limit
from app.services.emails import send_welcome_email

router = APIRouter()

MAX_EMAIL_LEN = 254
MAX_PASSWORD_LEN = 128

# Substrings Supabase uses in its "email not confirmed" error messages across
# SDK versions — we match case-insensitively against the raised exception text.
_UNCONFIRMED_MARKERS = (
    "email not confirmed",
    "email_not_confirmed",
    "confirmation",
    "not confirmed",
)


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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not res.user:
        raise HTTPException(status_code=400, detail="Signup failed")

    # Fire the welcome email (best-effort — never fail signup on email glitches).
    # We use req.email directly rather than re-fetching from profiles because
    # the profile-sync trigger may not have completed yet and req.email is
    # definitely what they just typed.
    await send_welcome_email(to_email=req.email)

    return {
        "message": "Check your email to confirm your account",
        "user_id": res.user.id,
    }


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
        # Distinguish "unconfirmed email" from "wrong password" so the
        # frontend can show the right prompt. We never leak whether the email
        # exists for the wrong-password branch — that stays a generic 401.
        msg = str(e).lower()
        if any(marker in msg for marker in _UNCONFIRMED_MARKERS):
            raise HTTPException(
                status_code=401,
                detail="Please confirm your email before logging in. Check your inbox (and spam folder) for the confirmation link.",
            )
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


@router.post("/setup-account")
async def setup_account(req: SetupAccountRequest, request: Request):
    """
    Complete account setup for an invited user.

    The owner's invite created their auth.users row (pre-confirmed, no
    password) and stored a one-time setup_token in user_metadata. The
    invitee clicks the link in the invite email, which lands them on the
    frontend's "Choose your password" screen. That screen calls this
    endpoint with the token + their chosen password.

    Flow:
      1. Find the user by email
      2. Verify setup_token matches
      3. Set their password via admin API
      4. Clear the setup_token (single-use)
      5. Sign them in and return a JWT session
    """
    rate_limit(request, max_requests=5, window_seconds=300)

    email = req.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    # Find the user
    target_user = None
    try:
        users_res = supabase_admin.auth.admin.list_users()
        for u in users_res:
            if hasattr(u, "email") and (u.email or "").lower() == email:
                target_user = u
                break
    except Exception:
        pass

    if not target_user:
        raise HTTPException(status_code=404, detail="No account found for this email")

    # Verify the setup token
    metadata = target_user.user_metadata or {}
    stored_token = metadata.get("setup_token", "")
    if not stored_token or stored_token != req.token:
        raise HTTPException(
            status_code=400,
            detail="This setup link is invalid or has already been used. Ask your team admin to send a new invite.",
        )

    # Set the password and clear the setup token
    try:
        supabase_admin.auth.admin.update_user_by_id(
            target_user.id,
            {
                "password": req.password,
                "user_metadata": {
                    **metadata,
                    "setup_token": None,
                    "needs_password": False,
                },
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set password: {e}")

    # Sign them in with the new password
    try:
        res = supabase.auth.sign_in_with_password(
            {"email": email, "password": req.password}
        )
        return AuthResponse(
            access_token=res.session.access_token,
            user_id=res.user.id,
            email=res.user.email,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Password set successfully but auto-login failed. Please go to the login page and sign in with your new password.",
        )


# ─── Forgot / reset password ──────────────────────────────────────────

from pydantic import BaseModel


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str  # Supabase recovery access_token from the email link
    password: str


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest, request: Request):
    """
    Send a password-reset email. Always returns success to avoid leaking
    whether an account exists for the given email. Supabase's recovery
    email contains a link of the form:
        {APP_URL}/#access_token=XXX&type=recovery&refresh_token=YYY...

    The frontend picks that up from the URL hash and posts the token to
    /reset-password below.
    """
    rate_limit(request, max_requests=5, window_seconds=300)

    email = (req.email or "").strip().lower()
    if not email or len(email) > MAX_EMAIL_LEN:
        # Generic response — never confirm/deny existence.
        return {"message": "If an account exists, a reset link has been sent."}

    # Resolve the APP_URL to use as redirect target. In prod this is
    # https://voxsite.app. Supabase also requires the URL to be on the
    # allowlist in Authentication → URL Configuration.
    import os
    app_url = os.environ.get("APP_URL", "https://voxsite.app")

    try:
        # Supabase SDK: reset_password_email (some SDK versions) or
        # reset_password_for_email. Try the common name first, fall
        # back if the SDK exposes a different method.
        try:
            supabase.auth.reset_password_for_email(
                email, {"redirect_to": app_url}
            )
        except AttributeError:
            supabase.auth.reset_password_email(
                email, {"redirect_to": app_url}
            )
    except Exception:
        # Swallow — we don't tell the caller whether the address existed
        # or whether Supabase had an internal blip. The user either gets
        # the email or they don't; either way the message below is safe.
        pass

    return {"message": "If an account exists, a reset link has been sent."}


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest, request: Request):
    """
    Consume a Supabase recovery access_token and set a new password.

    Flow:
      1. Verify the token identifies a real user (via supabase.auth.get_user)
      2. Use the admin API to update that user's password
      3. Sign them in with the new password and return a fresh session

    Supabase recovery tokens are short-lived (default 1 hour) and
    single-type ("recovery"), so this endpoint can't be misused to
    change arbitrary passwords.
    """
    rate_limit(request, max_requests=5, window_seconds=300)

    if not req.token or not req.password:
        raise HTTPException(status_code=400, detail="Token and password are required")
    if len(req.password) < 6 or len(req.password) > MAX_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail="Password must be 6-128 characters")

    # Step 1: verify the token and get the user
    try:
        user_res = supabase.auth.get_user(req.token)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="This reset link is invalid or has expired. Request a new one from the Forgot password screen.",
        )

    if not user_res or not getattr(user_res, "user", None):
        raise HTTPException(
            status_code=400,
            detail="This reset link is invalid or has expired. Request a new one from the Forgot password screen.",
        )

    target = user_res.user
    target_email = getattr(target, "email", None)
    target_id = getattr(target, "id", None)

    if not target_email or not target_id:
        raise HTTPException(status_code=400, detail="Reset token is missing user info")

    # Step 2: update password via admin API
    try:
        supabase_admin.auth.admin.update_user_by_id(
            target_id,
            {"password": req.password},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set password: {e}")

    # Step 3: sign them in with the new password and return a session,
    # so the frontend can skip the "please log in again" step.
    try:
        res = supabase.auth.sign_in_with_password(
            {"email": target_email, "password": req.password}
        )
        return AuthResponse(
            access_token=res.session.access_token,
            user_id=res.user.id,
            email=res.user.email,
        )
    except Exception:
        # Password IS updated at this point. Return 200 with no session
        # so the frontend tells the user to log in manually.
        return {
            "message": "Password updated. Please sign in with your new password.",
        }
