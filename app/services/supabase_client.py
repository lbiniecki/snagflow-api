"""
Supabase client — singleton for DB, storage, auth
"""
from supabase import create_client, Client
from app.config import settings

# Public client (respects RLS)
supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

# Service-role client (bypasses RLS — use carefully)
supabase_admin: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def get_user_client(access_token: str) -> Client:
    """Create a Supabase client authenticated as a specific user."""
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    client.auth.set_session(access_token, "")
    return client
