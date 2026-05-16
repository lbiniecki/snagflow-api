-- ============================================================================
-- editable-report-date
--
-- Context
--   Peter (Coyle Civil & Structural) reported the following workflow problem
--   on 16 May 2026:
--
--     "Did a site visit on 27 April. Wrote up the report a few days later.
--      Could not change the date printed on the cover of the report."
--
--   Currently four places in backend/app/services/report_generator.py call
--   datetime.now() to print "today's date" on the report. This means:
--     - A report issued on 30 April for a visit on 27 April is dated 30 April
--       (correct) — but only if generated on 30 April.
--     - The same report regenerated on 5 May is dated 5 May (wrong, should
--       remain 30 April since that's when it was issued).
--     - There is no way to set the date independently of the visit date or
--       the generation timestamp.
--
--   Engineering practice is to print a deliberate "issue date" on the
--   report cover — when the engineer formally issues the document, not when
--   the file was generated. This migration introduces persistent storage
--   for that date.
--
-- What this migration does
--   1. Adds a nullable `report_date DATE` column to public.site_visits.
--   2. Backfills existing rows: report_date = visit_date.
--      Rationale: for reports already in customer hands, regenerating
--      should produce the same date that was printed before. Setting
--      report_date = visit_date matches the current behaviour (the cover
--      printed datetime.now() at generation time, which was usually within
--      a day of visit_date for the most common workflow).
--
--   Backfilling to today's date would have created date drift on
--   regeneration. Backfilling to visit_date is the conservative choice.
--
--   The column is left nullable so future visits created before the
--   frontend wires up the date picker still work; the backend falls back
--   to visit_date when report_date is null.
--
-- Safe to run multiple times — ADD COLUMN uses IF NOT EXISTS, UPDATE
-- only sets rows where report_date IS NULL.
-- ============================================================================

ALTER TABLE public.site_visits
  ADD COLUMN IF NOT EXISTS report_date DATE;

UPDATE public.site_visits
SET    report_date = visit_date
WHERE  report_date IS NULL;
