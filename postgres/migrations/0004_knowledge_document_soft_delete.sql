ALTER TABLE public.knowledge_documents
    ADD COLUMN status text NOT NULL DEFAULT 'active';

ALTER TABLE public.knowledge_documents
    ADD COLUMN deleted_by text;

ALTER TABLE public.knowledge_documents
    ADD COLUMN deleted_at timestamp without time zone;

CREATE INDEX idx_knowledge_documents_active
    ON public.knowledge_documents USING btree (tenant_id, collection_id, status, updated_at);
