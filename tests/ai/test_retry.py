from __future__ import annotations

import asyncio
from email.utils import formatdate

import pytest

from pi_agent.ai import CancellationToken, ProviderErrorDetails, ProviderRequestError
from pi_agent.ai.retry import parse_retry_after, retry_delay, sleep_with_cancellation


def test_parse_retry_after_supports_seconds_milliseconds_http_date_and_reset() -> None:
    assert parse_retry_after({"Retry-After": "2.5"}) == 2.5
    assert parse_retry_after({"retry-after-ms": "250"}) == 0.25
    future = formatdate(usegmt=True)
    assert parse_retry_after({"Retry-After": future}) == pytest.approx(0.0, abs=1.0)
    assert parse_retry_after({"x-ratelimit-reset": "3"}) == 3.0


def test_retry_delay_uses_server_value_and_rejects_excessive_wait() -> None:
    error = ProviderRequestError(
        ProviderErrorDetails("rate limited", retryable=True, retry_after=2.0)
    )
    assert retry_delay(error, 4, base=0.5, maximum=10.0) == 2.0
    with pytest.raises(ProviderRequestError, match="exceeds the configured maximum"):
        retry_delay(error, 0, base=0.5, maximum=1.0)
    generated = ProviderRequestError(ProviderErrorDetails("network", retryable=True))
    assert retry_delay(generated, 3, base=0.5, maximum=3.0) == 3.0


@pytest.mark.asyncio
async def test_retry_sleep_observes_cancellation() -> None:
    token = CancellationToken()
    task = asyncio.create_task(sleep_with_cancellation(60.0, token))
    await asyncio.sleep(0)
    token.cancel("cancel retry")
    with pytest.raises(asyncio.CancelledError, match="cancel retry"):
        await task
