import io
import logging
import queue
import threading
import time
from prometheus_client import CollectorRegistry, generate_latest
from src.core.logging.queue_metrics import (MeteredQueue, MeteredQueueHandler, MeteredQueueListener,
                    MeteredStreamHandler, QueueCollector, start_internal_metrics)


def record():
    return logging.LogRecord("test", logging.INFO, "", 1, "PRIVATE_TOKEN_BODY", (), None)


def test_overflow_is_counted_without_printing_the_record(capsys):
    q = MeteredQueue(1, "test-overflow")
    handler = MeteredQueueHandler(q)
    handler.handle(record())
    handler.handle(record())
    counts, _ = q.telemetry.snapshot()
    assert counts["submitted"] == 2
    assert counts["enqueued"] == 1
    assert counts["dropped_full"] == 1
    assert capsys.readouterr().err == ""
    registry = CollectorRegistry()
    registry.register(QueueCollector())
    text = generate_latest(registry).decode()
    assert 'logging_queue_dropped_total{appender="test-overflow",reason="full"} 1.0' in text
    assert "PRIVATE_TOKEN_BODY" not in text


def test_output_error_is_counted_without_killing_worker(capsys):
    class BrokenStream:
        def write(self, value):
            raise OSError("PRIVATE_IO_ERROR")
        def flush(self):
            pass
    q = MeteredQueue(4, "test-output")
    sink = MeteredStreamHandler(BrokenStream(), telemetry=q.telemetry)
    listener = MeteredQueueListener(q, sink)
    listener.start()
    q.put(record())
    assert listener.stop(timeout=1)
    counts, flight = q.telemetry.snapshot()
    assert counts["output_errors"] == 1
    assert counts["processed"] == 1
    assert flight == 0
    assert capsys.readouterr().err == ""


def test_full_queue_shutdown_has_one_deadline():
    entered = threading.Event()
    release = threading.Event()
    class Blocked(logging.Handler):
        def emit(self, record):
            entered.set()
            release.wait(3)
    q = MeteredQueue(1, "test-shutdown")
    listener = MeteredQueueListener(q, Blocked())
    listener.start()
    try:
        q.put(record())
        assert entered.wait(1)
        q.put(record())
        started = time.monotonic()
        assert not listener.stop(timeout=0.02)
        assert time.monotonic() - started < 0.3
        assert q.telemetry.snapshot()[0]["shutdown_pending"] == 1
    finally:
        release.set()
        assert listener.stop(timeout=1)


def test_metrics_listener_is_opt_in(monkeypatch):
    monkeypatch.delenv("OBSERVABILITY_ENABLED", raising=False)
    assert start_internal_metrics() is None


def test_enabled_listener_exports_queue_counters_and_stops(monkeypatch):
    from urllib.request import urlopen
    from src.core.logging.queue_metrics import stop_internal_metrics
    monkeypatch.setenv("OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("OBSERVABILITY_BIND", "127.0.0.1")
    monkeypatch.setenv("OBSERVABILITY_PORT", "0")
    q = MeteredQueue(2, "test-http")
    q.put(record())
    server = start_internal_metrics()
    try:
        with urlopen(f"http://127.0.0.1:{server[0].server_port}/metrics", timeout=2) as response:
            text = response.read().decode()
        assert 'logging_queue_enqueued_total{appender="test-http"} 1.0' in text
        assert "PRIVATE_TOKEN_BODY" not in text
    finally:
        stop_internal_metrics(server)
    assert not server[1].is_alive()
