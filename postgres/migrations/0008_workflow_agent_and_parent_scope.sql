-- 0008_workflow_agent_and_parent_scope
-- Stateless master: runs are scoped to the orchestrator agent and record the
-- caller's workflow id so a completed child can cascade resume to its parent
-- (cross-service HITL).
ALTER TABLE public.workflow_runs ADD COLUMN IF NOT EXISTS agent_id text;
ALTER TABLE public.workflow_runs ADD COLUMN IF NOT EXISTS parent_workflow_id text;
CREATE INDEX IF NOT EXISTS idx_workflow_runs_agent
    ON public.workflow_runs(state, agent_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_parent
    ON public.workflow_runs(parent_workflow_id);
