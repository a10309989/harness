from harness.security.ratelimit import RateLimitConfig, RateLimiter


def test_request_budget_blocks_after_limit():
    limiter = RateLimiter(RateLimitConfig(requests_per_minute=3, enabled=True))

    assert limiter.allow_request("ip-1")
    assert limiter.allow_request("ip-1")
    assert limiter.allow_request("ip-1")
    assert not limiter.allow_request("ip-1")
    # A different caller is unaffected.
    assert limiter.allow_request("ip-2")


def test_auth_failure_lockout_blocks_and_resets_on_success():
    limiter = RateLimiter(RateLimitConfig(auth_failures_per_minute=2, enabled=True))

    assert not limiter.is_locked_out("ip-1")
    limiter.record_auth_failure("ip-1")
    limiter.record_auth_failure("ip-1")
    assert limiter.is_locked_out("ip-1")

    limiter.record_auth_success("ip-1")
    assert not limiter.is_locked_out("ip-1")


def test_disabled_limiter_always_allows():
    limiter = RateLimiter(RateLimitConfig(enabled=False))

    assert limiter.allow_request("ip-1")
    assert not limiter.is_locked_out("ip-1")
    limiter.record_auth_failure("ip-1")
    assert not limiter.is_locked_out("ip-1")