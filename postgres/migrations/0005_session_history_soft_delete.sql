ALTER TABLE public.sessions
    ADD COLUMN IF NOT EXISTS deleted_by text;

ALTER TABLE public.sessions
    ADD COLUMN IF NOT EXISTS deleted_at timestamp without time zone;

CREATE INDEX IF NOT EXISTS idx_sessions_active_archive
    ON public.sessions USING btree (tenant_id, user_id, deleted_at, updated_at);
