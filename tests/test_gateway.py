from types import SimpleNamespace

from harness.security.gateway import GatewayMiddleware, trace_id_from_traceparent


def test_traceparent_maps_to_trace_id():
    assert trace_id_from_traceparent(
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    ) == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_invalid_traceparent_is_ignored():
    assert trace_id_from_traceparent("not-a-traceparent") is None


def test_gateway_identity_requires_oidc_mtls_and_waf_allow():
    settings = SimpleNamespace(
        gateway_oidc_subject_header="x-auth-request-sub",
        gateway_oidc_email_header="x-auth-request-email",
        gateway_mtls_subject_header="x-forwarded-client-cert",
        gateway_quota_header="x-harness-quota-subject",
        gateway_waf_header="x-harness-waf-verdict",
    )
    request = SimpleNamespace(
        headers={
            "x-auth-request-sub": "user-1",
            "x-auth-request-email": "user@example.com",
            "x-forwarded-client-cert": "Subject=CN=user-1",
            "x-harness-waf-verdict": "allow",
        }
    )

    identity = GatewayMiddleware._gateway_identity(request, settings)

    assert identity["subject"] == "user-1"
    assert identity["email"] == "user@example.com"
    assert identity["quota_subject"] == "user-1"


def test_gateway_identity_rejects_blocked_waf_verdict():
    settings = SimpleNamespace(
        gateway_oidc_subject_header="x-auth-request-sub",
        gateway_oidc_email_header="x-auth-request-email",
        gateway_mtls_subject_header="x-forwarded-client-cert",
        gateway_quota_header="x-harness-quota-subject",
        gateway_waf_header="x-harness-waf-verdict",
    )
    request = SimpleNamespace(
        headers={
            "x-auth-request-sub": "user-1",
            "x-forwarded-client-cert": "Subject=CN=user-1",
            "x-harness-waf-verdict": "block",
        }
    )

    assert GatewayMiddleware._gateway_identity(request, settings) is None
