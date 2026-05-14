-- ============================================================================
-- security-advisor-cleanup
--
-- Context
--   Supabase Security Advisor (May 2026) flagged 11 warnings on this project:
--     - 4 × "Function Search Path Mutable" on trigger functions
--     - 3 × "Public Can Execute SECURITY DEFINER Function"
--     - 3 × "Signed-In Users Can Execute SECURITY DEFINER Function"
--     - 1 × "Leaked Password Protection Disabled" (handled separately in
--           Supabase dashboard → Authentication → Sign In/Up, not SQL)
--
--   Plus 2 "Info" items (RLS-enabled-no-policy on company_invites and
--   stripe_events) deliberately left as-is — those tables are service_role
--   only and don't need authenticated/anon policies.
--
-- What this migration does
--   1. Pins search_path on the trigger functions where it was missing.
--      Postgres best-practice hardening; not exploitable in Supabase's
--      managed environment but silences the linter.
--
--   2. Revokes EXECUTE on three SECURITY DEFINER functions from anon,
--      authenticated, and PUBLIC. These functions are only invoked by
--      triggers or as event triggers — never via the Data API — so
--      revoking does not affect application behaviour. PostgreSQL defaults
--      EXECUTE to PUBLIC; the three-way revoke covers all paths.
--
--   3. Drops public.sync_profile_email — an orphaned duplicate of
--      public.sync_profile_from_auth left over from the dashboard era.
--      Confirmed before drop: zero triggers referenced it (pg_trigger
--      query returned no rows), no application code called it (grep
--      across frontend/ and backend/).
--
-- Safe to run multiple times — REVOKE is idempotent, DROP uses IF EXISTS,
-- ALTER FUNCTION ... SET is idempotent.
-- ============================================================================

-- 1. ── Pin search_path on trigger functions --------------------------------
ALTER FUNCTION public.auto_visit_no()     SET search_path = public, pg_temp;
ALTER FUNCTION public.auto_snag_no()      SET search_path = public, pg_temp;
ALTER FUNCTION public.update_updated_at() SET search_path = public, pg_temp;


-- 2. ── Lock down SECURITY DEFINER functions to service_role only -----------
-- rls_auto_enable is an EVENT trigger function. Anon/authenticated can't
-- run DDL anyway, but the linter flags the grant; revoke for cleanliness.
REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM anon;
REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM authenticated;

-- sync_profile_from_auth is invoked only by the on_auth_user_created and
-- on_auth_user_email_updated triggers (see 2026-04-16-profiles-email-sync).
-- Triggers fire with their own privileges, so revoking EXECUTE from
-- normal roles is safe.
REVOKE EXECUTE ON FUNCTION public.sync_profile_from_auth() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.sync_profile_from_auth() FROM anon;
REVOKE EXECUTE ON FUNCTION public.sync_profile_from_auth() FROM authenticated;


-- 3. ── Drop orphaned sync function -----------------------------------------
-- public.sync_profile_email predates public.sync_profile_from_auth.
-- It was created via the Supabase dashboard early on, never wired up to
-- a trigger that survives in the current schema, and was duplicated by
-- the sync_profile_from_auth function in the April 2026 migration.
-- Verified before drop:
--   SELECT t.tgname FROM pg_trigger t JOIN pg_proc p ON t.tgfoid = p.oid
--   WHERE p.proname = 'sync_profile_email' AND NOT t.tgisinternal;
--   → 0 rows
DROP FUNCTION IF EXISTS public.sync_profile_email();


-- ============================================================================
-- NOT included in this migration (handled separately or out of scope)
-- ----------------------------------------------------------------------------
-- * Leaked Password Protection: toggled in dashboard, not SQL.
-- * RLS-enabled-no-policy on company_invites and stripe_events: intentional
--   (service_role only, no frontend access needed).
-- ============================================================================
