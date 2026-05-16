-- ============================================================================
-- report-issues
--
-- Context
--   Until now `issue_no = visit_no` (hardcoded in report_generator.py:439).
--   Every PDF for "Visit 1" prints "Issue No: 1" regardless of whether it's
--   a draft preview or the official issued version. This breaks the
--   Document Control Sheet's whole purpose (revision history is meaningless
--   when every PDF is "Issue 1") and risks confusion when a client
--   receives multiple PDFs with the same issue number but different content.
--
-- Workflow this enables
--   1. User clicks "Generate report"
--   2. Modal opens with two buttons:
--      - "Preview PDF"      → generates a working copy. No row added here.
--                             Cover shows "Working copy" marker if a prior
--                             issue exists, else nothing.
--      - "Issue this report" → inserts a row into report_issues with the
--                             next sequential issue_no for the visit.
--                             Cover shows "Issue N" + the issue date.
--   3. The Document Control Sheet renders all rows from report_issues for
--      the visit, newest at top — multi-row history table (Q3 = B).
--
-- Soft-lock model (decided 16 May 2026)
--   Issuing a report does NOT prevent further edits to the visit or its
--   snags. The frontend shows a warning at issue time ("clients
--   shouldn't see different versions with the same issue number") and an
--   "Issued" badge on the visit card afterwards, but the database does
--   not enforce immutability. If the user edits after issuing and wants
--   a fresh authoritative version, they issue a revision (Issue N+1).
--
-- What this migration does
--   1. Creates public.report_issues with FK to site_visits.
--   2. Indexes (visit_id, issue_no) for the per-visit history query.
--   3. Unique constraint on (visit_id, issue_no) so two concurrent
--      "Issue" clicks can't both produce Issue 1 — the second one fails
--      and the backend retries with N+1.
--   4. Enables RLS and adds a sensible default policy. The backend uses
--      service_role and bypasses RLS anyway, but RLS is enabled for
--      consistency with the rest of the schema (see 2026-05-11
--      security-advisor-cleanup for context).
--   5. NO data backfill. Existing visits that have already had PDFs
--      generated are NOT retroactively "issued." Anyone running this
--      migration who wants historical visits marked as issued must do
--      that manually via the UI. This is the safe default — backfilling
--      would imply we know which PDFs were actually issued vs which
--      were drafts, and we don't.
--
-- Safe to run multiple times — CREATE TABLE uses IF NOT EXISTS, indexes
-- use IF NOT EXISTS, policy uses DROP IF EXISTS + CREATE.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.report_issues (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  visit_id        uuid NOT NULL REFERENCES public.site_visits(id) ON DELETE CASCADE,
  issue_no        integer NOT NULL,
  issue_date      date NOT NULL,
  issued_by       uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  -- Optional revision description (e.g. "Added snag 12 after follow-up walk").
  -- Nullable: in v1 the frontend doesn't expose this field; reserved for v2.
  notes           text,
  created_at      timestamptz NOT NULL DEFAULT now(),

  -- A visit can't have two Issue 1s. If a race condition does manage to
  -- bypass our backend's "compute next issue_no" logic, the DB hard-stops
  -- the second insert and the backend retries with N+1.
  CONSTRAINT report_issues_visit_no_unique UNIQUE (visit_id, issue_no),
  CONSTRAINT report_issues_issue_no_positive CHECK (issue_no > 0)
);

-- Index for the most common query: "list all issues for this visit"
-- (used by the PDF generator when rendering the Document Control Sheet).
CREATE INDEX IF NOT EXISTS report_issues_visit_id_idx
  ON public.report_issues (visit_id, issue_no DESC);

-- ── Standard hygiene per the May 11 cleanup pattern ────────────────
ALTER TABLE public.report_issues ENABLE ROW LEVEL SECURITY;

-- Drop any pre-existing policy so this migration is deterministic on re-run
DROP POLICY IF EXISTS "report_issues read own"  ON public.report_issues;
DROP POLICY IF EXISTS "report_issues write own" ON public.report_issues;

-- A user can read issues for visits in projects they own. service_role
-- bypasses RLS for backend writes; this policy is for any future direct
-- frontend reads via the Data API.
CREATE POLICY "report_issues read own"
  ON public.report_issues FOR SELECT
  USING (
    visit_id IN (
      SELECT v.id
      FROM public.site_visits v
      JOIN public.projects p ON v.project_id = p.id
      WHERE p.user_id = auth.uid()
    )
  );

-- No frontend-facing INSERT/UPDATE/DELETE policies. All mutations go
-- through the backend on service_role, which bypasses RLS. This keeps
-- "issue this report" a server-controlled action (next-issue-no
-- computation, issued_by attribution, atomic INSERT) rather than
-- something the client can directly fabricate.

-- Done. Verify with:
--   SELECT * FROM public.report_issues LIMIT 5;
--   -- expect 0 rows initially (no backfill)
--
--   SELECT count(*) FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'report_issues';
--   -- expect 7 (id, visit_id, issue_no, issue_date, issued_by, notes, created_at)
