--
-- PostgreSQL database dump
--

-- Dumped from database version 16.4
-- Dumped by pg_dump version 16.4

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: actor_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.actor_roles (
    actor_id text NOT NULL,
    role_name text NOT NULL
);


--
-- Name: actors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.actors (
    id text NOT NULL,
    actor_type text NOT NULL,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    display_name text NOT NULL,
    enabled integer DEFAULT 1 NOT NULL,
    metadata text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: agent_llm_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_llm_overrides (
    agent_id text NOT NULL,
    provider text,
    model text,
    api_key text,
    base_url text
);


--
-- Name: agent_mcp_bindings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_mcp_bindings (
    agent_id text NOT NULL,
    server_name text NOT NULL,
    tool_filter text
);


--
-- Name: api_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_credentials (
    id text NOT NULL,
    actor_id text NOT NULL,
    key_hash text NOT NULL,
    key_prefix text NOT NULL,
    enabled integer DEFAULT 1 NOT NULL,
    expires_at timestamp without time zone,
    last_used_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: approval_grants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.approval_grants (
    id text NOT NULL,
    task_id text NOT NULL,
    workflow_id text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    input_digest text NOT NULL,
    state text DEFAULT 'active'::text NOT NULL,
    created_by text NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    consumed_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: artifact_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifact_links (
    id text NOT NULL,
    source_version_id text NOT NULL,
    target_version_id text NOT NULL,
    relationship text NOT NULL,
    metadata text,
    created_by text NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: artifact_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifact_versions (
    id text NOT NULL,
    artifact_id text NOT NULL,
    version_number integer NOT NULL,
    content_digest text NOT NULL,
    storage_key text NOT NULL,
    media_type text NOT NULL,
    size_bytes integer NOT NULL,
    metadata text,
    created_by text NOT NULL,
    trace_id text NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: artifacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.artifacts (
    id text NOT NULL,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    artifact_type text NOT NULL,
    name text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    current_version_id text,
    created_by text NOT NULL,
    created_at timestamp without time zone NOT NULL,
    archived_at timestamp without time zone
);


--
-- Name: audit_chain_heads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_chain_heads (
    tenant_id text NOT NULL,
    event_hash text NOT NULL,
    event_id text NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: audit_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_events (
    id text NOT NULL,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    trace_id text NOT NULL,
    span_id text NOT NULL,
    parent_span_id text,
    workflow_id text,
    session_id text,
    actor_id text NOT NULL,
    actor_type text NOT NULL,
    event_type text NOT NULL,
    action text NOT NULL,
    resource_type text,
    resource_id text,
    decision text,
    reason text,
    input_digest text,
    output_digest text,
    metadata text,
    previous_hash text,
    event_hash text NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: conversation_turns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_turns (
    id bigint NOT NULL,
    session_id text NOT NULL,
    role text NOT NULL,
    content text NOT NULL,
    metadata text,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: conversation_turns_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversation_turns_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversation_turns_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conversation_turns_id_seq OWNED BY public.conversation_turns.id;


--
-- Name: evaluation_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evaluation_results (
    id text NOT NULL,
    run_id text NOT NULL,
    case_id text NOT NULL,
    status text NOT NULL,
    score real,
    output_json text,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: evaluation_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evaluation_runs (
    id text NOT NULL,
    suite_id text NOT NULL,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    trace_id text NOT NULL,
    status text NOT NULL,
    suite_snapshot_json text NOT NULL,
    summary_json text,
    created_by text NOT NULL,
    created_at timestamp without time zone NOT NULL,
    completed_at timestamp without time zone
);


--
-- Name: evaluation_suites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.evaluation_suites (
    id text NOT NULL,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    name text NOT NULL,
    category text NOT NULL,
    cases_json text NOT NULL,
    pass_threshold real DEFAULT 1.0 NOT NULL,
    created_by text NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: human_task_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.human_task_actions (
    id text NOT NULL,
    task_id text NOT NULL,
    actor_id text NOT NULL,
    action text NOT NULL,
    comment text,
    idempotency_key text,
    metadata text,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: human_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.human_tasks (
    id text NOT NULL,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    workflow_id text NOT NULL,
    checkpoint_id text NOT NULL,
    policy_decision_id text,
    title text NOT NULL,
    description text NOT NULL,
    state text NOT NULL,
    required_approvals integer DEFAULT 1 NOT NULL,
    requester_id text NOT NULL,
    assignee_id text,
    claim_expires_at timestamp without time zone,
    due_at timestamp without time zone,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    completed_at timestamp without time zone
);


--
-- Name: llm_providers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_providers (
    name text NOT NULL,
    provider text NOT NULL,
    api_key text NOT NULL,
    model text NOT NULL,
    base_url text,
    provider_label text,
    temperature real DEFAULT 0.2,
    max_tokens integer DEFAULT 4096,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: llm_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_settings (
    key text NOT NULL,
    value text
);


--
-- Name: mcp_servers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mcp_servers (
    name text NOT NULL,
    command text NOT NULL,
    args text,
    env text,
    enabled integer DEFAULT 1,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: outbox_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.outbox_events (
    id text NOT NULL,
    topic text NOT NULL,
    payload text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamp without time zone NOT NULL,
    processed_at timestamp without time zone,
    last_error text,
    lease_owner text,
    lease_expires_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.permissions (
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL
);


--
-- Name: policy_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.policy_decisions (
    id text NOT NULL,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    trace_id text NOT NULL,
    span_id text NOT NULL,
    actor_id text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    risk_level text NOT NULL,
    decision text NOT NULL,
    matched_rule_id text,
    matched_rule_version_id text,
    rule_digest text,
    reason text NOT NULL,
    input_digest text,
    context_data text,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: policy_rule_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.policy_rule_versions (
    id text NOT NULL,
    rule_id text NOT NULL,
    version_number integer NOT NULL,
    rule_digest text NOT NULL,
    snapshot text NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: policy_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.policy_rules (
    id text NOT NULL,
    name text NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    enabled integer DEFAULT 1 NOT NULL,
    effect text NOT NULL,
    resource_type text DEFAULT 'tool'::text NOT NULL,
    resource_pattern text DEFAULT '*'::text NOT NULL,
    risk_levels text,
    actor_roles text,
    conditions text,
    reason text DEFAULT ''::text NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


--
-- Name: role_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.role_permissions (
    role_name text NOT NULL,
    permission_name text NOT NULL
);


--
-- Name: roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roles (
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL
);


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    id text NOT NULL,
    user_id text DEFAULT 'default'::text NOT NULL,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    state text DEFAULT 'created'::text NOT NULL,
    current_agent text,
    context_data text,
    expires_at timestamp without time zone,
    last_activity timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: workflow_checkpoints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_checkpoints (
    id text NOT NULL,
    workflow_id text NOT NULL,
    step_id text NOT NULL,
    artifact_version_id text NOT NULL,
    policy_decision_id text,
    checkpoint_type text NOT NULL,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: workflow_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_runs (
    id text NOT NULL,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    session_id text,
    trace_id text NOT NULL,
    state text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    current_step_id text,
    input_digest text,
    output_data text,
    error text,
    created_by text NOT NULL,
    lease_owner text,
    lease_expires_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    completed_at timestamp without time zone,
    temporal_workflow_id text,
    temporal_run_id text,
    temporal_state text,
    temporal_updated_at timestamp without time zone,
    execution_engine text DEFAULT 'legacy'::text NOT NULL
);


--
-- Name: workflow_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_steps (
    id text NOT NULL,
    workflow_id text NOT NULL,
    sequence_number integer NOT NULL,
    step_type text NOT NULL,
    agent_id text,
    resource_id text,
    state text NOT NULL,
    input_digest text,
    output_data text,
    error text,
    created_at timestamp without time zone NOT NULL,
    started_at timestamp without time zone,
    completed_at timestamp without time zone
);


--
-- Name: workflow_transitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_transitions (
    id text NOT NULL,
    workflow_id text NOT NULL,
    from_state text,
    to_state text NOT NULL,
    reason text,
    actor_id text NOT NULL,
    metadata text,
    created_at timestamp without time zone NOT NULL
);


--
-- Name: conversation_turns id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_turns ALTER COLUMN id SET DEFAULT nextval('public.conversation_turns_id_seq'::regclass);


--
-- Name: actor_roles actor_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actor_roles
    ADD CONSTRAINT actor_roles_pkey PRIMARY KEY (actor_id, role_name);


--
-- Name: actors actors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actors
    ADD CONSTRAINT actors_pkey PRIMARY KEY (id);


--
-- Name: agent_llm_overrides agent_llm_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_llm_overrides
    ADD CONSTRAINT agent_llm_overrides_pkey PRIMARY KEY (agent_id);


--
-- Name: agent_mcp_bindings agent_mcp_bindings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_mcp_bindings
    ADD CONSTRAINT agent_mcp_bindings_pkey PRIMARY KEY (agent_id, server_name);


--
-- Name: api_credentials api_credentials_key_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_credentials
    ADD CONSTRAINT api_credentials_key_hash_key UNIQUE (key_hash);


--
-- Name: api_credentials api_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_credentials
    ADD CONSTRAINT api_credentials_pkey PRIMARY KEY (id);


--
-- Name: approval_grants approval_grants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approval_grants
    ADD CONSTRAINT approval_grants_pkey PRIMARY KEY (id);


--
-- Name: artifact_links artifact_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_links
    ADD CONSTRAINT artifact_links_pkey PRIMARY KEY (id);


--
-- Name: artifact_links artifact_links_source_version_id_target_version_id_relation_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_links
    ADD CONSTRAINT artifact_links_source_version_id_target_version_id_relation_key UNIQUE (source_version_id, target_version_id, relationship);


--
-- Name: artifact_versions artifact_versions_artifact_id_version_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_versions
    ADD CONSTRAINT artifact_versions_artifact_id_version_number_key UNIQUE (artifact_id, version_number);


--
-- Name: artifact_versions artifact_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_versions
    ADD CONSTRAINT artifact_versions_pkey PRIMARY KEY (id);


--
-- Name: artifacts artifacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifacts
    ADD CONSTRAINT artifacts_pkey PRIMARY KEY (id);


--
-- Name: audit_chain_heads audit_chain_heads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_chain_heads
    ADD CONSTRAINT audit_chain_heads_pkey PRIMARY KEY (tenant_id);


--
-- Name: audit_events audit_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_events
    ADD CONSTRAINT audit_events_pkey PRIMARY KEY (id);


--
-- Name: conversation_turns conversation_turns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_turns
    ADD CONSTRAINT conversation_turns_pkey PRIMARY KEY (id);


--
-- Name: evaluation_results evaluation_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evaluation_results
    ADD CONSTRAINT evaluation_results_pkey PRIMARY KEY (id);


--
-- Name: evaluation_results evaluation_results_run_id_case_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evaluation_results
    ADD CONSTRAINT evaluation_results_run_id_case_id_key UNIQUE (run_id, case_id);


--
-- Name: evaluation_runs evaluation_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evaluation_runs
    ADD CONSTRAINT evaluation_runs_pkey PRIMARY KEY (id);


--
-- Name: evaluation_suites evaluation_suites_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evaluation_suites
    ADD CONSTRAINT evaluation_suites_pkey PRIMARY KEY (id);


--
-- Name: evaluation_suites evaluation_suites_tenant_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evaluation_suites
    ADD CONSTRAINT evaluation_suites_tenant_id_name_key UNIQUE (tenant_id, name);


--
-- Name: human_task_actions human_task_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.human_task_actions
    ADD CONSTRAINT human_task_actions_pkey PRIMARY KEY (id);


--
-- Name: human_task_actions human_task_actions_task_id_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.human_task_actions
    ADD CONSTRAINT human_task_actions_task_id_idempotency_key_key UNIQUE (task_id, idempotency_key);


--
-- Name: human_tasks human_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.human_tasks
    ADD CONSTRAINT human_tasks_pkey PRIMARY KEY (id);


--
-- Name: llm_providers llm_providers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_providers
    ADD CONSTRAINT llm_providers_pkey PRIMARY KEY (name);


--
-- Name: llm_settings llm_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_settings
    ADD CONSTRAINT llm_settings_pkey PRIMARY KEY (key);


--
-- Name: mcp_servers mcp_servers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mcp_servers
    ADD CONSTRAINT mcp_servers_pkey PRIMARY KEY (name);


--
-- Name: outbox_events outbox_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.outbox_events
    ADD CONSTRAINT outbox_events_pkey PRIMARY KEY (id);


--
-- Name: permissions permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.permissions
    ADD CONSTRAINT permissions_pkey PRIMARY KEY (name);


--
-- Name: policy_decisions policy_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_decisions
    ADD CONSTRAINT policy_decisions_pkey PRIMARY KEY (id);


--
-- Name: policy_rule_versions policy_rule_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_rule_versions
    ADD CONSTRAINT policy_rule_versions_pkey PRIMARY KEY (id);


--
-- Name: policy_rule_versions policy_rule_versions_rule_id_rule_digest_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_rule_versions
    ADD CONSTRAINT policy_rule_versions_rule_id_rule_digest_key UNIQUE (rule_id, rule_digest);


--
-- Name: policy_rule_versions policy_rule_versions_rule_id_version_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_rule_versions
    ADD CONSTRAINT policy_rule_versions_rule_id_version_number_key UNIQUE (rule_id, version_number);


--
-- Name: policy_rules policy_rules_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_rules
    ADD CONSTRAINT policy_rules_name_key UNIQUE (name);


--
-- Name: policy_rules policy_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_rules
    ADD CONSTRAINT policy_rules_pkey PRIMARY KEY (id);


--
-- Name: role_permissions role_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT role_permissions_pkey PRIMARY KEY (role_name, permission_name);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (name);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- Name: workflow_checkpoints workflow_checkpoints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_checkpoints
    ADD CONSTRAINT workflow_checkpoints_pkey PRIMARY KEY (id);


--
-- Name: workflow_runs workflow_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_runs
    ADD CONSTRAINT workflow_runs_pkey PRIMARY KEY (id);


--
-- Name: workflow_steps workflow_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_steps
    ADD CONSTRAINT workflow_steps_pkey PRIMARY KEY (id);


--
-- Name: workflow_steps workflow_steps_workflow_id_sequence_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_steps
    ADD CONSTRAINT workflow_steps_workflow_id_sequence_number_key UNIQUE (workflow_id, sequence_number);


--
-- Name: workflow_transitions workflow_transitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_transitions
    ADD CONSTRAINT workflow_transitions_pkey PRIMARY KEY (id);


--
-- Name: idx_api_credentials_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_credentials_hash ON public.api_credentials USING btree (key_hash);


--
-- Name: idx_approval_grants_workflow; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_approval_grants_workflow ON public.approval_grants USING btree (workflow_id, state, created_at);


--
-- Name: idx_artifact_links_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_artifact_links_source ON public.artifact_links USING btree (source_version_id, relationship);


--
-- Name: idx_artifact_links_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_artifact_links_target ON public.artifact_links USING btree (target_version_id, relationship);


--
-- Name: idx_artifact_versions_digest; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_artifact_versions_digest ON public.artifact_versions USING btree (content_digest);


--
-- Name: idx_artifacts_tenant_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_artifacts_tenant_type ON public.artifacts USING btree (tenant_id, artifact_type, created_at);


--
-- Name: idx_audit_actor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_actor ON public.audit_events USING btree (actor_id, created_at);


--
-- Name: idx_audit_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_session ON public.audit_events USING btree (session_id, created_at);


--
-- Name: idx_audit_trace; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_trace ON public.audit_events USING btree (trace_id, created_at);


--
-- Name: idx_audit_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_type ON public.audit_events USING btree (event_type, created_at);


--
-- Name: idx_evaluation_runs_suite; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_evaluation_runs_suite ON public.evaluation_runs USING btree (suite_id, created_at);


--
-- Name: idx_human_task_actions_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_human_task_actions_task ON public.human_task_actions USING btree (task_id, created_at);


--
-- Name: idx_human_tasks_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_human_tasks_queue ON public.human_tasks USING btree (tenant_id, state, created_at);


--
-- Name: idx_human_tasks_workflow; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_human_tasks_workflow ON public.human_tasks USING btree (workflow_id, created_at);


--
-- Name: idx_outbox_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_outbox_pending ON public.outbox_events USING btree (status, available_at, lease_expires_at);


--
-- Name: idx_policy_decisions_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_policy_decisions_resource ON public.policy_decisions USING btree (resource_type, resource_id, created_at);


--
-- Name: idx_policy_decisions_trace; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_policy_decisions_trace ON public.policy_decisions USING btree (trace_id, created_at);


--
-- Name: idx_policy_rule_versions_rule; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_policy_rule_versions_rule ON public.policy_rule_versions USING btree (rule_id, version_number);


--
-- Name: idx_policy_rules_evaluation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_policy_rules_evaluation ON public.policy_rules USING btree (enabled, resource_type, priority);


--
-- Name: idx_sessions_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_tenant ON public.sessions USING btree (tenant_id, updated_at);


--
-- Name: idx_turns_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_turns_session ON public.conversation_turns USING btree (session_id);


--
-- Name: idx_workflow_checkpoints_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workflow_checkpoints_run ON public.workflow_checkpoints USING btree (workflow_id, created_at);


--
-- Name: idx_workflow_runs_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workflow_runs_session ON public.workflow_runs USING btree (session_id, created_at);


--
-- Name: idx_workflow_runs_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workflow_runs_state ON public.workflow_runs USING btree (state, updated_at);


--
-- Name: idx_workflow_runs_temporal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_workflow_runs_temporal_id ON public.workflow_runs USING btree (temporal_workflow_id) WHERE (temporal_workflow_id IS NOT NULL);


--
-- Name: idx_workflow_steps_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workflow_steps_run ON public.workflow_steps USING btree (workflow_id, sequence_number);


--
-- Name: idx_workflow_transitions_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workflow_transitions_run ON public.workflow_transitions USING btree (workflow_id, created_at);


--
-- Name: actor_roles actor_roles_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actor_roles
    ADD CONSTRAINT actor_roles_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actors(id) ON DELETE CASCADE;


--
-- Name: actor_roles actor_roles_role_name_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actor_roles
    ADD CONSTRAINT actor_roles_role_name_fkey FOREIGN KEY (role_name) REFERENCES public.roles(name) ON DELETE CASCADE;


--
-- Name: api_credentials api_credentials_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_credentials
    ADD CONSTRAINT api_credentials_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actors(id) ON DELETE CASCADE;


--
-- Name: approval_grants approval_grants_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approval_grants
    ADD CONSTRAINT approval_grants_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.human_tasks(id) ON DELETE RESTRICT;


--
-- Name: approval_grants approval_grants_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approval_grants
    ADD CONSTRAINT approval_grants_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.workflow_runs(id) ON DELETE RESTRICT;


--
-- Name: artifact_links artifact_links_source_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_links
    ADD CONSTRAINT artifact_links_source_version_id_fkey FOREIGN KEY (source_version_id) REFERENCES public.artifact_versions(id) ON DELETE RESTRICT;


--
-- Name: artifact_links artifact_links_target_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_links
    ADD CONSTRAINT artifact_links_target_version_id_fkey FOREIGN KEY (target_version_id) REFERENCES public.artifact_versions(id) ON DELETE RESTRICT;


--
-- Name: artifact_versions artifact_versions_artifact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.artifact_versions
    ADD CONSTRAINT artifact_versions_artifact_id_fkey FOREIGN KEY (artifact_id) REFERENCES public.artifacts(id) ON DELETE RESTRICT;


--
-- Name: conversation_turns conversation_turns_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_turns
    ADD CONSTRAINT conversation_turns_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(id) ON DELETE CASCADE;


--
-- Name: evaluation_results evaluation_results_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evaluation_results
    ADD CONSTRAINT evaluation_results_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.evaluation_runs(id) ON DELETE RESTRICT;


--
-- Name: evaluation_runs evaluation_runs_suite_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.evaluation_runs
    ADD CONSTRAINT evaluation_runs_suite_id_fkey FOREIGN KEY (suite_id) REFERENCES public.evaluation_suites(id) ON DELETE RESTRICT;


--
-- Name: human_task_actions human_task_actions_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.human_task_actions
    ADD CONSTRAINT human_task_actions_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.human_tasks(id) ON DELETE RESTRICT;


--
-- Name: human_tasks human_tasks_checkpoint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.human_tasks
    ADD CONSTRAINT human_tasks_checkpoint_id_fkey FOREIGN KEY (checkpoint_id) REFERENCES public.workflow_checkpoints(id) ON DELETE RESTRICT;


--
-- Name: human_tasks human_tasks_policy_decision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.human_tasks
    ADD CONSTRAINT human_tasks_policy_decision_id_fkey FOREIGN KEY (policy_decision_id) REFERENCES public.policy_decisions(id) ON DELETE SET NULL;


--
-- Name: human_tasks human_tasks_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.human_tasks
    ADD CONSTRAINT human_tasks_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.workflow_runs(id) ON DELETE RESTRICT;


--
-- Name: policy_decisions policy_decisions_matched_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_decisions
    ADD CONSTRAINT policy_decisions_matched_rule_id_fkey FOREIGN KEY (matched_rule_id) REFERENCES public.policy_rules(id) ON DELETE SET NULL;


--
-- Name: policy_rule_versions policy_rule_versions_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_rule_versions
    ADD CONSTRAINT policy_rule_versions_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES public.policy_rules(id) ON DELETE RESTRICT;


--
-- Name: role_permissions role_permissions_permission_name_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT role_permissions_permission_name_fkey FOREIGN KEY (permission_name) REFERENCES public.permissions(name) ON DELETE CASCADE;


--
-- Name: role_permissions role_permissions_role_name_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT role_permissions_role_name_fkey FOREIGN KEY (role_name) REFERENCES public.roles(name) ON DELETE CASCADE;


--
-- Name: workflow_checkpoints workflow_checkpoints_artifact_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_checkpoints
    ADD CONSTRAINT workflow_checkpoints_artifact_version_id_fkey FOREIGN KEY (artifact_version_id) REFERENCES public.artifact_versions(id) ON DELETE RESTRICT;


--
-- Name: workflow_checkpoints workflow_checkpoints_policy_decision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_checkpoints
    ADD CONSTRAINT workflow_checkpoints_policy_decision_id_fkey FOREIGN KEY (policy_decision_id) REFERENCES public.policy_decisions(id) ON DELETE SET NULL;


--
-- Name: workflow_checkpoints workflow_checkpoints_step_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_checkpoints
    ADD CONSTRAINT workflow_checkpoints_step_id_fkey FOREIGN KEY (step_id) REFERENCES public.workflow_steps(id) ON DELETE RESTRICT;


--
-- Name: workflow_checkpoints workflow_checkpoints_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_checkpoints
    ADD CONSTRAINT workflow_checkpoints_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.workflow_runs(id) ON DELETE RESTRICT;


--
-- Name: workflow_steps workflow_steps_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_steps
    ADD CONSTRAINT workflow_steps_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.workflow_runs(id) ON DELETE RESTRICT;


--
-- Name: workflow_transitions workflow_transitions_workflow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_transitions
    ADD CONSTRAINT workflow_transitions_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES public.workflow_runs(id) ON DELETE RESTRICT;


--
-- PostgreSQL database dump complete
--

