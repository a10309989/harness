CREATE TABLE public.knowledge_collections (
    id text NOT NULL,
    tenant_id text NOT NULL DEFAULT 'default',
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    metadata text,
    created_by text NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);

ALTER TABLE ONLY public.knowledge_collections
    ADD CONSTRAINT knowledge_collections_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_collections
    ADD CONSTRAINT knowledge_collections_tenant_name_key UNIQUE (tenant_id, name);

CREATE TABLE public.knowledge_documents (
    id text NOT NULL,
    tenant_id text NOT NULL DEFAULT 'default',
    collection_id text NOT NULL,
    source_type text NOT NULL,
    source_uri text NOT NULL,
    title text NOT NULL,
    content_digest text NOT NULL,
    metadata text,
    acl_tags text,
    created_by text NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);

ALTER TABLE ONLY public.knowledge_documents
    ADD CONSTRAINT knowledge_documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_documents
    ADD CONSTRAINT knowledge_documents_tenant_collection_source_key UNIQUE (tenant_id, collection_id, source_uri);

CREATE INDEX idx_knowledge_documents_collection
    ON public.knowledge_documents USING btree (tenant_id, collection_id, updated_at);

CREATE TABLE public.knowledge_chunks (
    id text NOT NULL,
    tenant_id text NOT NULL DEFAULT 'default',
    collection_id text NOT NULL,
    document_id text NOT NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    keywords text,
    metadata text,
    artifact_id text,
    artifact_version_id text,
    created_at timestamp without time zone NOT NULL
);

ALTER TABLE ONLY public.knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_document_index_key UNIQUE (document_id, chunk_index);

CREATE INDEX idx_knowledge_chunks_collection
    ON public.knowledge_chunks USING btree (tenant_id, collection_id, created_at);

CREATE INDEX idx_knowledge_chunks_artifact
    ON public.knowledge_chunks USING btree (artifact_version_id);

CREATE TABLE public.agent_knowledge_profiles (
    tenant_id text NOT NULL DEFAULT 'default',
    agent_id text NOT NULL,
    collection_names text NOT NULL,
    retrieval_policy text NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamp without time zone NOT NULL
);

ALTER TABLE ONLY public.agent_knowledge_profiles
    ADD CONSTRAINT agent_knowledge_profiles_pkey PRIMARY KEY (tenant_id, agent_id);

CREATE TABLE public.retrieval_audit_events (
    id text NOT NULL,
    tenant_id text NOT NULL DEFAULT 'default',
    trace_id text NOT NULL,
    agent_id text,
    query text NOT NULL,
    collection_names text NOT NULL,
    result_count integer NOT NULL DEFAULT 0,
    filtered_count integer NOT NULL DEFAULT 0,
    source_refs text,
    created_by text NOT NULL,
    created_at timestamp without time zone NOT NULL
);

ALTER TABLE ONLY public.retrieval_audit_events
    ADD CONSTRAINT retrieval_audit_events_pkey PRIMARY KEY (id);

CREATE INDEX idx_retrieval_audit_trace
    ON public.retrieval_audit_events USING btree (tenant_id, trace_id, created_at);

CREATE INDEX idx_retrieval_audit_agent
    ON public.retrieval_audit_events USING btree (tenant_id, agent_id, created_at);

CREATE TABLE public.artifact_knowledge_links (
    id text NOT NULL,
    artifact_id text NOT NULL,
    artifact_version_id text NOT NULL,
    document_id text NOT NULL,
    chunk_id text,
    relationship text NOT NULL,
    created_at timestamp without time zone NOT NULL
);

ALTER TABLE ONLY public.artifact_knowledge_links
    ADD CONSTRAINT artifact_knowledge_links_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.artifact_knowledge_links
    ADD CONSTRAINT artifact_knowledge_links_version_document_chunk_key UNIQUE (artifact_version_id, document_id, chunk_id, relationship);

CREATE INDEX idx_artifact_knowledge_links_artifact
    ON public.artifact_knowledge_links USING btree (artifact_id, artifact_version_id);

ALTER TABLE ONLY public.knowledge_documents
    ADD CONSTRAINT knowledge_documents_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.knowledge_collections(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_collection_id_fkey FOREIGN KEY (collection_id) REFERENCES public.knowledge_collections(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.knowledge_documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.artifact_knowledge_links
    ADD CONSTRAINT artifact_knowledge_links_artifact_id_fkey FOREIGN KEY (artifact_id) REFERENCES public.artifacts(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.artifact_knowledge_links
    ADD CONSTRAINT artifact_knowledge_links_artifact_version_id_fkey FOREIGN KEY (artifact_version_id) REFERENCES public.artifact_versions(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.artifact_knowledge_links
    ADD CONSTRAINT artifact_knowledge_links_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.knowledge_documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.artifact_knowledge_links
    ADD CONSTRAINT artifact_knowledge_links_chunk_id_fkey FOREIGN KEY (chunk_id) REFERENCES public.knowledge_chunks(id) ON DELETE CASCADE;
