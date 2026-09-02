from integration.rate_limit import ClovaRateLimiter, RateLimitBlocked, parse_reset_seconds


def test_parse_reset_seconds_accepts_provider_duration_and_negative_values():
    assert parse_reset_seconds("23s") == 23.0
    assert parse_reset_seconds("-2s") == 0.0


def test_limiter_blocks_when_provider_reports_no_tokens():
    limiter = ClovaRateLimiter(default_qpm=90, default_tpm=80000)
    limiter.observe({
        "x-ratelimit-remaining-requests": "89",
        "x-ratelimit-remaining-tokens": "0",
        "x-ratelimit-reset-tokens": "30s",
    })
    try:
        limiter.before_call(100)
    except RateLimitBlocked as error:
        assert error.retry_after > 0
    else:  # pragma: no cover
        raise AssertionError("provider token exhaustion must block the call")


def test_limiter_blocks_local_tpm_budget_without_network():
    limiter = ClovaRateLimiter(default_qpm=90, default_tpm=100)
    limiter.before_call(80)
    try:
        limiter.before_call(30)
    except RateLimitBlocked as error:
        assert "TPM" in str(error)
    else:  # pragma: no cover
        raise AssertionError("local TPM budget must block the call")


def test_limiter_snapshot_contains_only_safe_budget_state():
    limiter = ClovaRateLimiter(default_qpm=60, default_tpm=40000)
    limiter.observe({
        "x-ratelimit-remaining-requests": "59",
        "x-ratelimit-remaining-tokens": "39000",
    })
    snapshot = limiter.snapshot()
    assert snapshot["remaining_requests"] == 59
    assert snapshot["remaining_tokens"] == 39000
    assert "api_key" not in snapshot


