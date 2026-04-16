-- ============================================================================
-- profiles email sync + RLS fix
--
-- Context
--   public.profiles holds per-user display info (first_name, last_name, etc.)
--   but users' canonical email lives in auth.users.email. Until this migration
--   there was no automatic sync between the two, and several server-side code
--   paths (team invites, subscription confirmations, report emails) now depend
--   on profiles.email being populated.
--
-- What this does
--   1. Adds an `email` column to public.profiles (if missing).
--   2. Adds a trigger that mirrors auth.users.email into public.profiles on
--      every insert and on every email change. The trigger runs as
--      SECURITY DEFINER so it bypasses RLS — without this, the policies below
--      would block the trigger's own writes.
--   3. Rewrites RLS policies to the intended model:
--        - A user can read + update their own profile row.
--        - Teammates (members of the same company) can read each other's
--          profile — needed for member lists and sender names in emails.
--        - No one can INSERT or DELETE directly; those happen via the trigger
--          (insert) or cascade from auth.users delete.
--   4. Backfills existing profiles with emails pulled from auth.users, and
--      inserts profile rows for any auth users that are missing one.
--
-- Safe to run multiple times — all statements are idempotent.
-- ============================================================================

-- 1. ── Schema -------------------------------------------------------
ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS email      text,
  ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now(),
  ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();

-- An index on email helps member-lookup queries. Not unique because soft-deletes
-- or email changes could briefly cause duplicates; uniqueness is enforced in
-- auth.users, which is authoritative.
CREATE INDEX IF NOT EXISTS profiles_email_idx ON public.profiles (email);


-- 2. ── Sync trigger -------------------------------------------------
CREATE OR REPLACE FUNCTION public.sync_profile_from_auth()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER                -- bypass RLS: trigger is trusted infrastructure
SET search_path = public, auth
AS $$
BEGIN
  INSERT INTO public.profiles (id, email, first_name, last_name, created_at, updated_at)
  VALUES (NEW.id, NEW.email, '', '', now(), now())
  ON CONFLICT (id) DO UPDATE
    SET email      = EXCLUDED.email,
        updated_at = now();
  RETURN NEW;
END;
$$;

-- Fires on every new signup (auth.users INSERT)
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.sync_profile_from_auth();

-- Fires when a user's email changes in auth.users (rare but possible)
DROP TRIGGER IF EXISTS on_auth_user_email_updated ON auth.users;
CREATE TRIGGER on_auth_user_email_updated
  AFTER UPDATE OF email ON auth.users
  FOR EACH ROW
  WHEN (OLD.email IS DISTINCT FROM NEW.email)
  EXECUTE FUNCTION public.sync_profile_from_auth();


-- 3. ── Row Level Security ------------------------------------------
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- Drop any old/conflicting policies so this migration is deterministic
DROP POLICY IF EXISTS "profiles read own"         ON public.profiles;
DROP POLICY IF EXISTS "profiles update own"       ON public.profiles;
DROP POLICY IF EXISTS "profiles read teammates"   ON public.profiles;
DROP POLICY IF EXISTS "Users can view own profile"    ON public.profiles;
DROP POLICY IF EXISTS "Users can update own profile"  ON public.profiles;

-- A user can see their own row
CREATE POLICY "profiles read own"
  ON public.profiles FOR SELECT
  USING (auth.uid() = id);

-- A user can update their own row (but not id or email — those are controlled
-- by auth.users. Column-level check enforced at the app layer.)
CREATE POLICY "profiles update own"
  ON public.profiles FOR UPDATE
  USING (auth.uid() = id)
  WITH CHECK (auth.uid() = id);

-- A user can see the profile of anyone they share a company with. This is
-- needed so the frontend can render member lists / invite acceptance UIs
-- without funnelling every lookup through the backend.
CREATE POLICY "profiles read teammates"
  ON public.profiles FOR SELECT
  USING (
    id IN (
      SELECT cm2.user_id
      FROM public.company_members cm1
      JOIN public.company_members cm2 ON cm1.company_id = cm2.company_id
      WHERE cm1.user_id = auth.uid()
    )
  );

-- No INSERT / DELETE policies: inserts come from the trigger only, deletes
-- cascade from auth.users. Service-role key still bypasses all of this for
-- server-side admin operations.


-- 4. ── Backfill existing data --------------------------------------
-- 4a. Populate email on profile rows that are missing it
UPDATE public.profiles p
SET    email = u.email,
       updated_at = now()
FROM   auth.users u
WHERE  p.id = u.id
AND   (p.email IS NULL OR p.email = '');

-- 4b. Create profile rows for any auth users that don't have one yet
INSERT INTO public.profiles (id, email, first_name, last_name, created_at, updated_at)
SELECT u.id, u.email, '', '', now(), now()
FROM   auth.users u
LEFT JOIN public.profiles p ON p.id = u.id
WHERE  p.id IS NULL;


-- Done. Verify with:
--   SELECT count(*) FROM public.profiles WHERE email IS NULL OR email = '';
--   -- expect 0
--   SELECT count(*) FROM auth.users u
--   LEFT JOIN public.profiles p ON p.id = u.id
--   WHERE  p.id IS NULL;
--   -- expect 0
