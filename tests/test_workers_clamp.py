import logging
from unittest.mock import MagicMock

from quantumbpm.workers import DEFAULT_MAX_ERROR_MESSAGE_BYTES, Worker


def make_worker(max_bytes: int | None = None) -> Worker:
    api_client = MagicMock()
    kwargs = {}
    if max_bytes is not None:
        kwargs["max_error_message_bytes"] = max_bytes
    return Worker(api_client, "00000000-0000-0000-0000-000000000001", **kwargs)


def test_clamp_passes_short_message_through(caplog):
    w = make_worker()
    caplog.set_level(logging.WARNING, logger="quantumbpm.workers")
    out = w._clamp_worker_error_message("payment", "boom")
    assert out == "boom"
    assert caplog.records == []


def test_clamp_truncates_at_default_and_warns(caplog):
    w = make_worker()
    caplog.set_level(logging.WARNING, logger="quantumbpm.workers")
    huge = "x" * 100_000
    out = w._clamp_worker_error_message("payment", huge)
    assert len(out.encode("utf-8")) <= DEFAULT_MAX_ERROR_MESSAGE_BYTES
    assert out.endswith(" bytes]")
    warn_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warn_msgs) == 1
    assert "WORKER_ERROR message truncated" in warn_msgs[0]


def test_clamp_honors_override():
    w = make_worker(max_bytes=256)
    out = w._clamp_worker_error_message("payment", "x" * 10_000)
    assert len(out.encode("utf-8")) <= 256


def test_clamp_cuts_on_utf8_boundary():
    # "é" is 2 bytes in UTF-8 → naive byte-slice could split it.
    w = make_worker(max_bytes=200)
    msg = "é" * 1000
    out = w._clamp_worker_error_message("t", msg)
    # If we cut cleanly, round-tripping through utf-8 is lossless.
    assert out.encode("utf-8").decode("utf-8") == out
    assert "�" not in out
