import json
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError, BadRequestError, OpenAI
from openai.types.chat import ChatCompletion

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent import CodingAgent, Usage
from model_requests import request_retry_delay, retryable_model_error
from tools import WorkspaceTools

PROXY_FAILURE = {
    "code": "proxy_error",
    "message": 'Post "https://api.taotoken.net/v1/chat/completions": read tcp '
    "192.168.0.12:56166->119.3.253.96:443: read: connection reset by peer "
    "(request id: 2026100113465864357590900133)",
    "type": "invalid_request_error",
}


def status_error(status: int, body: object, headers: dict | None = None) -> APIStatusError:
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "http://127.0.0.1/v1/chat/completions"),
        json=body,
        headers=headers,
    )
    return (BadRequestError if status == 400 else APIStatusError)(
        f"Error code: {status} - {body}", response=response, body=body
    )


@pytest.mark.parametrize(("nested",), [(False,), (True,)])  # noqa: PT006 - AGENTS.md requires tuples.
def test_captured_gateway_reset_is_retryable_despite_http_400(nested: bool) -> None:
    body = {"error": PROXY_FAILURE} if nested else PROXY_FAILURE
    error = status_error(400, body)
    assert isinstance(error, BadRequestError)
    assert retryable_model_error(error)
    assert not retryable_model_error(status_error(400, PROXY_FAILURE | {"code": "invalid_request_error"}))
    assert not retryable_model_error(status_error(400, PROXY_FAILURE | {"message": "Invalid model"}))


@pytest.mark.parametrize(
    ("status", "body", "expected"),  # noqa: PT006 - AGENTS.md requires tuples.
    [
        (400, {"message": "Unknown model", "code": "invalid_request_error"}, False),
        (401, PROXY_FAILURE, False),
        (403, PROXY_FAILURE, False),
        (422, PROXY_FAILURE, False),
        (408, {"message": "Request timeout"}, True),
        (409, {"message": "Conflict"}, True),
        (429, {"message": "Rate limit exceeded"}, True),
        (429, {"error": {"code": "insufficient_quota", "message": "Quota exhausted"}}, False),
        (500, {"message": "Internal server error"}, True),
        (502, PROXY_FAILURE, True),
        (503, {"message": "Service unavailable"}, True),
        (504, {"message": "Gateway timeout"}, True),
        (400, PROXY_FAILURE | {"message": "Invalid API key; upstream connection reset"}, False),
    ],
)
def test_transient_statuses_do_not_retry_configuration_or_quota_errors(
    status: int, body: object, expected: bool
) -> None:
    assert retryable_model_error(status_error(status, body)) is expected


def test_connection_exceptions_and_retry_delays_respect_numeric_provider_headers() -> None:
    request = httpx.Request("POST", "http://127.0.0.1/v1/chat/completions")
    for error in (APIConnectionError(request=request), APITimeoutError(request=request)):
        assert retryable_model_error(error)
        assert [request_retry_delay(error, attempt) for attempt in range(6)] == [2, 4, 8, 16, 30, 30]
    assert request_retry_delay(status_error(429, {}, {"retry-after": "7.5"}), 0) == 7.5
    assert request_retry_delay(status_error(429, {}, {"retry-after-ms": "3500"}), 0) == 3.5
    assert request_retry_delay(status_error(429, {}, {"retry-after": "240"}), 0) == 240
    assert request_retry_delay(status_error(429, {}, {"retry-after": "1"}), 1) == 4
    for value in ("NaN", "inf", "-1", "not-a-number"):
        assert request_retry_delay(status_error(429, {}, {"retry-after": value}), 0) == 2


def test_missing_usage_preserves_response_and_marks_later_totals_incomplete(tmp_path: Path) -> None:
    response = ChatCompletion(id="accounting-case", created=0, model="unused", object="chat.completion", choices=[])
    original = response.model_dump()
    with OpenAI(api_key="unused-accounting-test") as client:
        agent = CodingAgent(client, "unused", WorkspaceTools(tmp_path / "application"), tmp_path / "logs")
        usage = Usage()
        result = {"status": "running"}
        agent.record_usage(response, usage, result)
        assert usage.calls == usage.missing_usage_calls == 1
        assert usage.input_tokens == usage.output_tokens == usage.cached_tokens == 0
        assert result == {"status": "running", "token_accounting_complete": False}
        assert response.model_dump() == original
        agent.record_usage(
            ChatCompletion.model_validate(
                original
                | {
                    "usage": {
                        "prompt_tokens": 120,
                        "completion_tokens": 35,
                        "total_tokens": 155,
                        "prompt_tokens_details": {"cached_tokens": 80},
                    }
                }
            ),
            usage,
            result,
        )
        assert usage.calls == 2 and usage.missing_usage_calls == 1
        assert (usage.input_tokens, usage.output_tokens, usage.cached_tokens) == (120, 35, 80)
        assert result["token_accounting_complete"] is False
        assert not agent.token_accounting_complete
        assert not agent.requests_stopped


def test_known_usage_accumulates_reported_tokens_with_optional_cache_details(tmp_path: Path) -> None:
    with OpenAI(api_key="unused-accounting-test") as client:
        agent = CodingAgent(client, "unused", WorkspaceTools(tmp_path / "application"), tmp_path / "logs")
        usage = Usage()
        result = {"status": "running"}
        for details in (None, {"cached_tokens": 7}):
            agent.record_usage(
                ChatCompletion.model_validate(
                    {
                        "id": "accounting-case",
                        "created": 0,
                        "model": "unused",
                        "object": "chat.completion",
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 3,
                            "total_tokens": 13,
                            "prompt_tokens_details": details,
                        },
                    }
                ),
                usage,
                result,
            )
        assert usage.calls == 2 and usage.missing_usage_calls == 0
        assert (usage.input_tokens, usage.output_tokens, usage.cached_tokens) == (20, 6, 7)
        assert result == {"status": "running"}
        assert agent.token_accounting_complete


@contextmanager
def failing_tcp_endpoint(disconnect: bool = True):
    """Exercise actual sockets and SDK transport errors without returning model responses."""
    stop = Event()
    connections = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.05)

        def accept_connections() -> None:
            while not stop.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                connections.append(connection)
                if disconnect:
                    connection.close()

        thread = Thread(target=accept_connections)
        thread.start()
        try:
            yield f"http://127.0.0.1:{listener.getsockname()[1]}/v1", connections
        finally:
            stop.set()
            thread.join(timeout=2)
            for connection in connections:
                connection.close()
            assert not thread.is_alive()


def test_real_disconnect_exhaustion_preserves_progress_and_stops_later_requests(tmp_path: Path) -> None:
    secret = "private-local-transport-test-key"
    tools = WorkspaceTools(tmp_path / "application", (secret,))
    tools.write_file("existing.txt", "completed earlier stage")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "earlier.json").write_text('{"status":"finished"}')
    with (
        failing_tcp_endpoint() as (endpoint, connections),
        OpenAI(
            api_key=secret,
            base_url=endpoint,
            max_retries=4,
            http_client=httpx.Client(trust_env=False),
        ) as client,
    ):
        agent = CodingAgent(client, "transport-test", tools, logs, max_request_attempts=3, retry_delay=0)
        result = agent.run("Retain existing files; " + secret, "failed-stage")
        assert result["status"] == "api_unavailable"
        assert len(connections) == 3
        assert result["usage"]["request_attempts"] == 3
        assert result["usage"]["failed_requests"] == 3
        assert result["usage"]["calls"] == result["usage"]["input_tokens"] == 0
        assert result["token_accounting_complete"] is False
        assert agent.requests_stopped and not agent.token_accounting_complete
        assert len(result["api_failures"]) == 3
        assert all(failure["retryable"] for failure in result["api_failures"])
        later = agent.run("No further requests after exhaustion", "later-stage")
        assert later["status"] == "api_unavailable"
        assert len(connections) == 3
        assert agent.usage.request_attempts == agent.usage.failed_requests == 3
    checkpoint = json.loads((logs / "failed-stage.json").read_text(encoding="utf-8"))
    assert checkpoint["result"]["status"] == "api_unavailable"
    assert len(checkpoint["result"]["api_failures"]) == 3
    assert checkpoint["usage"]["request_attempts"] == 3
    assert "[REDACTED]" in json.dumps(checkpoint)
    assert secret not in json.dumps(checkpoint)
    assert (logs / "earlier.json").read_text() == '{"status":"finished"}'
    assert (tools.workspace / "existing.txt").read_text() == "completed earlier stage"
    assert not list(logs.glob("*.tmp"))


@pytest.mark.parametrize(("stage_deadline",), [(False,), (True,)])  # noqa: PT006 - AGENTS.md requires tuples.
def test_real_stalled_connection_obeys_request_budget_and_stage_deadline(tmp_path: Path, stage_deadline: bool) -> None:
    with (
        failing_tcp_endpoint(disconnect=False) as (endpoint, connections),
        OpenAI(
            api_key="local-timeout-test",
            base_url=endpoint,
            max_retries=4,
            http_client=httpx.Client(trust_env=False),
        ) as client,
    ):
        agent = CodingAgent(
            client,
            "transport-test",
            WorkspaceTools(tmp_path / "application"),
            tmp_path / "logs",
            deadline=time.monotonic() + 0.4 if stage_deadline else None,
            request_time_limit=10 if stage_deadline else 0.4,
            max_request_attempts=5,
            retry_delay=0,
        )
        started = time.monotonic()
        result = agent.run("Exercise actual transport timeout", "timeout-stage")
        assert time.monotonic() - started < 3
        assert result["status"] == "api_unavailable"
        assert len(connections) == result["usage"]["request_attempts"] == 1
        assert result["usage"]["failed_requests"] == 1
        assert result["api_failures"][0]["type"] == "APITimeoutError"
        assert not agent.token_accounting_complete
        assert agent.requests_stopped


def test_backoff_that_exceeds_remaining_budget_stops_without_sleeping(tmp_path: Path) -> None:
    with (
        failing_tcp_endpoint() as (endpoint, connections),
        OpenAI(
            api_key="local-budget-test",
            base_url=endpoint,
            http_client=httpx.Client(trust_env=False),
        ) as client,
    ):
        agent = CodingAgent(
            client,
            "transport-test",
            WorkspaceTools(tmp_path / "application"),
            tmp_path / "logs",
            request_time_limit=0.4,
            retry_delay=10,
        )
        started = time.monotonic()
        result = agent.run("Never wait beyond request budget", "backoff-stage")
        assert time.monotonic() - started < 3
        assert result["status"] == "api_unavailable"
        assert len(connections) == result["usage"]["request_attempts"] == 1
        assert result["api_failures"][0]["retry_in_seconds"] is None
        assert agent.requests_stopped


def test_expired_deadline_makes_no_transport_request(tmp_path: Path) -> None:
    with (
        failing_tcp_endpoint() as (endpoint, connections),
        OpenAI(
            api_key="unused-local-test",
            base_url=endpoint,
            http_client=httpx.Client(trust_env=False),
        ) as client,
    ):
        agent = CodingAgent(
            client,
            "unused",
            WorkspaceTools(tmp_path / "application"),
            tmp_path / "logs",
            deadline=time.monotonic() - 1,
        )
        result = agent.run("Must never reach network", "expired-stage")
        assert result["status"] == "time_limit"
        assert result["usage"]["request_attempts"] == result["usage"]["calls"] == 0
        assert connections == []
