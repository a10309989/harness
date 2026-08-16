# Harness Gateway Deployment

This gateway is the production ingress boundary for a single-tenant intranet deployment.

## Responsibilities

- Terminate TLS and require client certificates before traffic reaches FastAPI.
- Forward OIDC identity headers from an upstream auth proxy such as oauth2-proxy or an enterprise gateway.
- Apply request size limits, request-rate limits, connection limits, and WAF verdict headers.
- Preserve `traceparent` and emit `X-Trace-Id` so API, Temporal, Redis, Runner Manager, and LLM/MCP calls share one trace.

## Required API Settings

```env
HARNESS_GATEWAY_EXTERNAL_REQUIRED=true
HARNESS_GATEWAY_OIDC_SUBJECT_HEADER=x-auth-request-sub
HARNESS_GATEWAY_OIDC_EMAIL_HEADER=x-auth-request-email
HARNESS_GATEWAY_MTLS_SUBJECT_HEADER=x-forwarded-client-cert
HARNESS_GATEWAY_QUOTA_HEADER=x-harness-quota-subject
HARNESS_GATEWAY_WAF_HEADER=x-harness-waf-verdict
```

## Deployment Notes

- Start with `docker compose -f deploy/gateway/docker-compose.yaml up -d` after the API service joins `harness-temporal_control-plane` with the `harness-api` alias.
- Put oauth2-proxy, Envoy Gateway, NGINX Plus, or the enterprise L7 gateway in front of this config when native OIDC login is required.
- Replace `X-Harness-WAF-Verdict allow` with the verdict emitted by the selected WAF module or appliance.
- Mount `tls.crt`, `tls.key`, and `client-ca.crt` from the enterprise PKI secret store.
