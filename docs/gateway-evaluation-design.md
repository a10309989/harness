# PR-4 Gateway and Evaluation Design

## Gateway boundary

The FastAPI application is the internal Agent Gateway. `GatewayMiddleware`
enforces a configurable request-body limit and converts a valid W3C
`traceparent` into the internal `X-Trace-ID` before authentication and audit
middleware run. Authentication, RBAC, policy checks, approval gates, and audit
remain mandatory downstream controls; the gateway does not bypass them.

For production, enforce TLS, edge rate limits, WAF policy, and request quotas
at the ingress proxy. Redis-backed distributed rate limiting is intentionally
not implemented in-process, because an in-memory limiter would be incorrect
across API replicas.

## Evaluation and regression contract

An evaluation suite contains uniquely identified cases and a pass threshold.
Starting a run copies the suite into `evaluation_runs.suite_snapshot_json`; a
later suite edit therefore cannot alter the evidence used by an existing run.
Result submission requires exactly one result per snapshot case. Final status
is `passed` only when the measured pass rate satisfies the immutable threshold.

Every suite creation, run start, and run completion emits an audit event. The
current API accepts executor-produced results; PR-5 should bind a Temporal
activity to execute the selected suite through the hardened Docker Runner and
persist artifact references as result outputs.

## Promotion gates

- Require all critical regression suites to pass at their configured threshold.
- Retain run snapshots, result payloads, trace IDs, and audit links.
- Reject missing, duplicate, or unknown case results.
- Do not use PR-4's evaluation API as proof of isolated execution until the
  PostgreSQL adapter and Docker Runner are production-ready.
