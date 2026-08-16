-- 0009_agent_routing
-- Multi-version agent routing (AB/canary): which version of an agent to call
-- and with what traffic weight. The master picks a version per request; a
-- forced version (request header) pins it for canary/golden testing.
CREATE TABLE IF NOT EXISTS public.agent_routing (
    agent_id text NOT NULL,
    version text NOT NULL,
    endpoint text NOT NULL,
    weight integer NOT NULL DEFAULT 0,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (agent_id, version)
);
CREATE INDEX IF NOT EXISTS idx_agent_routing_enabled
    ON public.agent_routing(agent_id, enabled);
