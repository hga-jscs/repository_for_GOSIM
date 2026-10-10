"""Classify temporary model transport failures and respect provider retry delays."""

import math

from openai import APIConnectionError, APIStatusError

HARD_QUOTA_CODES = {
    "insufficient_quota",
    "insufficient_balance",
    "balance_insufficient",
    "billing_hard_limit_reached",
    "credit_balance_exhausted",
}
TRANSIENT_MESSAGES = (
    "connection reset",
    "connection refused",
    "connection aborted",
    "connection closed",
    "connection timed out",
    "connect timeout",
    "read timeout",
    "i/o timeout",
    "broken pipe",
    "unexpected eof",
    "server disconnected",
    "remoteprotocolerror",
    "tls handshake timeout",
    "temporary failure in name resolution",
    "context deadline exceeded",
    "timeout awaiting response headers",
    "upstream timeout",
    "upstream timed out",
    "service unavailable",
    "gateway timeout",
)
PERMANENT_MESSAGES = (
    "invalid api key",
    "incorrect api key",
    "authentication",
    "unauthorized",
    "permission denied",
    "insufficient balance",
    "insufficient quota",
    "credit balance",
    "invalid parameter",
    "invalid payload",
    "unsupported parameter",
    "context length",
    "maximum context",
    "model not found",
)


def retryable_model_error(error: APIConnectionError | APIStatusError) -> bool:
    if isinstance(error, APIConnectionError):
        return True
    body = error.body if isinstance(error.body, dict) else {}
    details = body.get("error", body)
    details = details if isinstance(details, dict) else body
    code = str(details.get("code") or error.code or "").casefold()
    message = str(details.get("message") or error.message).casefold()
    if code in HARD_QUOTA_CODES or any(marker in message for marker in PERMANENT_MESSAGES):
        return False
    if error.status_code in {408, 409, 429} or 500 <= error.status_code <= 599:
        return True
    return (
        error.status_code == 400 and code == "proxy_error" and any(marker in message for marker in TRANSIENT_MESSAGES)
    )


def request_retry_delay(error: APIConnectionError | APIStatusError, attempt: int, initial_delay: float = 2) -> float:
    """Use zero-based attempts; leave long server delays for the caller's budget check."""
    delay = min(30.0, max(0.0, initial_delay) * 2 ** min(30, max(0, attempt)))
    if not isinstance(error, APIStatusError):
        return delay
    for header, divisor in (("retry-after", 1), ("retry-after-ms", 1000)):
        value = error.response.headers.get(header)
        if value is None:
            continue
        try:
            seconds = float(value) / divisor
        except ValueError:
            continue
        if math.isfinite(seconds) and seconds >= 0:
            delay = max(delay, seconds)
    return delay
