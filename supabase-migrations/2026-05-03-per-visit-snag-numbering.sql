-- ─── Migration: per-visit snag numbering ───────────────────────────────
-- Date: 3 May 2026
-- Issue: snag_no was scoped by project_id, so the second visit's first
--        snag would be N+1 (where N is the highest snag in the project).
-- Fix:   scope by visit_id so each visit numbers from 1.
-- Existing snag_no values are preserved (no renumbering of historical data).

CREATE OR REPLACE FUNCTION public.auto_snag_no()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
  -- Lock the parent visit row to serialize concurrent snag creations
  -- within the same visit. This prevents two simultaneous inserts from
  -- both reading the same MAX(snag_no) and assigning the same number.
  PERFORM 1 FROM site_visits WHERE id = NEW.visit_id FOR UPDATE;

  NEW.snag_no := COALESCE(
    (SELECT MAX(snag_no) FROM snags WHERE visit_id = NEW.visit_id), 0
  ) + 1;
  RETURN NEW;
END;
$function$;

ALTER TABLE snags ALTER COLUMN visit_id SET NOT NULL;