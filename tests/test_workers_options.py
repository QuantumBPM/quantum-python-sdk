import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from quantumbpm import QuantumBPM, StaticTokenProvider
from quantumbpm.workers import Worker, _heartbeat_interval_s, _parse_duration_s

PROJECT_ID = "00000000-0000-0000-0000-000000000001"
NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)


async def handler(job):
    return None


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("0", 0.0),
        ("30s", 30.0),
        ("12.5s", 12.5),
        ("1m30s", 90.0),
        ("1h2m3.5s", 3723.5),
        ("500ms", 0.5),
        ("1.5h", 5400.0),
        (".5s", 0.5),
        ("5.s", 5.0),
        ("100us", 1e-4),
        ("100µs", 1e-4),
        ("100μs", 1e-4),
        ("1000000ns", 1e-3),
        ("+2m", 120.0),
        ("-1m30s", -90.0),
        (" 30s", 30.0),
        ("30s ", 30.0),
    ],
)
def test_parse_duration_accepts_go_grammar(value, seconds):
    assert _parse_duration_s(value) == pytest.approx(seconds)


@pytest.mark.parametrize(
    "value", ["", "30", "s", ".s", "12.5", "1d", "5mss", "1m 30s", "--1s", "1e3s"]
)
def test_parse_duration_rejects_what_go_rejects(value):
    with pytest.raises(ValueError):
        _parse_duration_s(value)


def test_handle_rejects_invalid_lock_duration():
    w = Worker(MagicMock(), PROJECT_ID)
    with pytest.raises(ValueError, match="'payment'.*'12.5x'"):
        w.handle("payment", handler, lock_duration="12.5x")
    assert "payment" not in w._registrations


def test_handler_decorator_rejects_invalid_poll_timeout():
    w = Worker(MagicMock(), PROJECT_ID)
    with pytest.raises(ValueError, match="'soon'"):
        w.handler("payment", poll_timeout="soon")(handler)


def test_handle_keeps_fractional_lock_duration():
    w = Worker(MagicMock(), PROJECT_ID)
    w.handle("payment", handler, lock_duration="12.5s")
    r = w._registrations["payment"]
    assert r.lock_duration == "12.5s"
    assert r.lock_duration_s == 12.5


def test_handle_strips_the_durations_it_sends():
    w = Worker(MagicMock(), PROJECT_ID)
    w.handle("payment", handler, lock_duration=" 10s ", poll_timeout="2s ")
    r = w._registrations["payment"]
    assert (r.lock_duration, r.poll_timeout, r.lock_duration_s) == ("10s", "2s", 10.0)


def test_poll_timeout_is_capped_per_request():
    w = Worker(MagicMock(), PROJECT_ID)
    w._default = MagicMock()
    w._default.poll_bpmn_external_jobs.return_value = []
    w.handle("long", handler, poll_timeout="45s")
    w.handle("short", handler, poll_timeout="2s")
    w.handle("default", handler)

    for task_type in ("long", "short", "default"):
        asyncio.run(w._poll(w._registrations[task_type], 1))

    sent = [c.args[1].timeout for c in w._default.poll_bpmn_external_jobs.call_args_list]
    assert sent == ["5s", "2s", "5s"]


@pytest.mark.parametrize("client_id", ["   ", "", None, "\t\n"])
def test_blank_client_id_falls_back_to_generated(client_id):
    w = Worker(MagicMock(), PROJECT_ID, client_id=client_id)
    assert w.client_id.startswith("worker-")


def test_client_id_is_stripped():
    w = Worker(MagicMock(), PROJECT_ID, client_id="  billing-svc ")
    assert w.client_id == "billing-svc"


def test_heartbeat_interval_uses_granted_lock_when_server_clamps():
    # lockDuration 11m, the server grants 5m.
    assert _heartbeat_interval_s(660.0, NOW + timedelta(minutes=5), NOW) == 150.0


def test_heartbeat_interval_uses_requested_lock_when_shorter():
    # A client clock slightly behind the server makes the grant look longer.
    assert _heartbeat_interval_s(12.5, NOW + timedelta(seconds=13), NOW) == 6.25


def test_heartbeat_interval_falls_back_to_requested_without_expiry():
    assert _heartbeat_interval_s(30.0, None, NOW) == 15.0


def test_heartbeat_interval_falls_back_to_requested_on_clock_skew():
    assert _heartbeat_interval_s(30.0, NOW - timedelta(seconds=5), NOW) == 15.0
    assert _heartbeat_interval_s(30.0, NOW, NOW) == 15.0


def test_heartbeat_interval_has_a_floor():
    assert _heartbeat_interval_s(0.5, NOW + timedelta(seconds=10), NOW) == 1.0


def test_new_worker_forwards_options():
    client = QuantumBPM(
        base_url="http://localhost:1",
        project_id=PROJECT_ID,
        token_provider=StaticTokenProvider("t"),
    )
    w = client.new_worker(client_id="svc", max_error_message_bytes=256, shutdown_timeout=3.0)
    assert w.client_id == "svc"
    assert w._max_error_message_bytes == 256
    assert w._shutdown_timeout == 3.0
    assert w._refresh is not None
