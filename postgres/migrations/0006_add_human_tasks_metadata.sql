-- Add a metadata column to human_tasks so pipeline stage tasks can carry the
-- produced artifact_version_id (surfaced in the frontend approval card).
ALTER TABLE public.human_tasks ADD COLUMN IF NOT EXISTS metadata text;