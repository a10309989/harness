CREATE TABLE public.runner_tasks (
    id text NOT NULL,
    status text NOT NULL,
    image text NOT NULL,
    script_id text NOT NULL,
    test_case_id text NOT NULL,
    execution_request_id text NOT NULL,
    timeout_seconds integer NOT NULL,
    result_json text,
    error text,
    created_at timestamp without time zone NOT NULL,
    started_at timestamp without time zone,
    completed_at timestamp without time zone
);

ALTER TABLE ONLY public.runner_tasks
    ADD CONSTRAINT runner_tasks_pkey PRIMARY KEY (id);

CREATE INDEX idx_runner_tasks_status ON public.runner_tasks USING btree (status, created_at);
