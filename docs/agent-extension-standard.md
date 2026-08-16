# Standard Agent Extension Architecture

Harness uses a single production extension mode:

- The local `MasterAgent` owns classification, routing, and orchestration.
- All specialized agents expose A2A JSON-RPC and are wrapped by `A2ARemoteAgentAdapter`.
- New specialized capabilities must not be added under `src/harness/agents`.

## Required Agent Card

Every production agent must publish an Agent Card:

```json
{
  "agent_id": "requirements-agent",
  "name": "Requirements Agent",
  "version": "1.0.0",
  "description": "Analyze product requirements and produce RequirementSpec",
  "capabilities": [
    {
      "name": "requirements.analysis",
      "description": "Turn raw requirement text into structured RequirementSpec",
      "input_schema": {"type": "object", "required": ["message"]},
      "output_schema": {"type": "object", "required": ["requirements"]}
    }
  ],
  "risk_level": "low",
  "protocols": ["a2a-jsonrpc"]
}
```

## A2A Methods

- `agent.discover`: returns one Agent Card or all cards.
- `agent.invoke`: invokes an agent with `agent_id`, `message`, `session_id`, optional `input_data`, `idempotency_key`, and `trace_id`.
- `agent.status`: returns current agent state.

## Standard Pipeline Contracts

- Requirements Agent outputs `RequirementSpec`.
- Test Case Agent consumes `RequirementSpec` and outputs `TestCaseSet`.
- Script Agent consumes `TestCaseSet` and outputs `ScriptBundle`.
- Execution Agent consumes `ScriptBundle` and outputs `ExecutionReport`.
- Diagnosis Agent consumes `ExecutionReport` and logs, then outputs `DiagnosisReport`.

## Recommended Production Rules

- Store large outputs as immutable artifacts and pass references between agents.
- Keep Redis as realtime projection only; PostgreSQL and MinIO remain authoritative.
- Use Temporal for long-running multi-agent workflows, retries, signals, and cancellation.
- Require idempotency keys for external A2A invocations.
- Propagate `trace_id` and audit every cross-agent call.
