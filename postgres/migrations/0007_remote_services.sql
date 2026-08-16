-- Agent 远程 A2A 服务配置表：Agent 管理作为远程服务管理面。
CREATE TABLE IF NOT EXISTS public.remote_services (
    id text NOT NULL,
    endpoint text NOT NULL,
    token text,
    timeout_seconds integer DEFAULT 300,
    skip_local integer DEFAULT 1,
    enabled integer DEFAULT 1,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    PRIMARY KEY (id)
);

-- 唯一索引：同一端点只允许一条记录（幂等添加）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_services_endpoint ON public.remote_services(endpoint);