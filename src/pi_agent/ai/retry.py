from __future__ import annotations

import asyncio
import email.utils
import time
from datetime import UTC

from .cancellation import CancellationToken
from .errors import ProviderErrorDetails, ProviderRequestError


def parse_retry_after(headers: dict[str, str]) -> float | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    retry_after_ms = normalized.get("retry-after-ms")
    if retry_after_ms is not None:
        try:
            return max(float(retry_after_ms) / 1000.0, 0.0)
        except ValueError:
            pass

    retry_after = normalized.get("retry-after")
    if retry_after is not None:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            try:
                parsed = email.utils.parsedate_to_datetime(retry_after)
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return max(parsed.timestamp() - time.time(), 0.0)

    reset = normalized.get("x-ratelimit-reset")
    if reset is not None:
        try:
            value = float(reset)
            return max(value - time.time() if value > 1_000_000_000 else value, 0.0)
        except ValueError:
            pass
    return None


def retry_delay(error: ProviderRequestError, attempt: int, *, base: float, maximum: float) -> float:
    requested = error.details.retry_after
    if requested is not None:
        if maximum > 0 and requested > maximum:
            raise ProviderRequestError(
                ProviderErrorDetails(
                    message=(
                        f"Provider requested a retry delay of {requested:g}s, "
                        f"which exceeds the configured maximum of {maximum:g}s"
                    ),
                    status_code=error.details.status_code,
                    code=error.details.code,
                    retryable=False,
                    retry_after=requested,
                    headers=error.details.headers,
                    raw=error.details.raw,
                    response_started=error.details.response_started,
                )
            )
        return requested
    delay = max(base, 0.0) * float(2**attempt)
    if maximum > 0:
        return float(min(delay, maximum))
    return float(delay)


async def sleep_with_cancellation(delay: float, signal: CancellationToken | None) -> None:
    if delay <= 0:
        if signal is not None:
            signal.raise_if_cancelled()
        await asyncio.sleep(0)
        return
    sleep_task = asyncio.create_task(asyncio.sleep(delay))
    if signal is None:
        await sleep_task
        return
    cancel_task = asyncio.create_task(signal.wait())
    done, pending = await asyncio.wait(
        {sleep_task, cancel_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    if cancel_task in done:
        signal.raise_if_cancelled()
    await sleep_task
