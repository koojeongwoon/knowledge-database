"""Bounded logging telemetry. Metrics contain no records, URLs, users or errors."""
import logging
import logging.handlers
import os
import queue
import threading
import time
import sys
from collections import Counter
from prometheus_client import REGISTRY, start_http_server
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily

_queues = {}
_registry_lock = threading.Lock()

class Telemetry:
    def __init__(self, name):
        self.name = name
        self._lock = threading.Lock()
        self.counts = Counter()
        self.in_flight = 0
    def increment(self, key, amount=1):
        with self._lock:
            self.counts[key] += amount
    def flight(self, change):
        with self._lock:
            self.in_flight += change
    def snapshot(self):
        with self._lock:
            return dict(self.counts), self.in_flight

class MeteredQueue(queue.Queue):
    def __init__(self, maxsize, name):
        if maxsize < 1:
            raise ValueError("Logging queues must have a finite capacity")
        super().__init__(maxsize=maxsize)
        self.telemetry = Telemetry(name)
        with _registry_lock:
            _queues[name] = self
    def put(self, item, block=True, timeout=None):
        measured = isinstance(item, logging.LogRecord)
        if measured:
            self.telemetry.increment("submitted")
        started = time.monotonic()
        try:
            super().put(item, block, timeout)
            if measured:
                self.telemetry.increment("enqueued")
        except queue.Full:
            if measured:
                self.telemetry.increment("full")
            raise
        finally:
            if measured:
                self.telemetry.increment("enqueue_seconds", time.monotonic() - started)

class MeteredQueueHandler(logging.handlers.QueueHandler):
    def handleError(self, record):
        # Never print the rejected record, credentials or traceback to stderr.
        self.queue.telemetry.increment("dropped_full" if isinstance(sys.exc_info()[1], queue.Full) else "enqueue_errors")

class MeteredQueueListener(logging.handlers.QueueListener):
    def __init__(self, log_queue, *handlers, **kwargs):
        super().__init__(log_queue, *handlers, **kwargs)
        self.telemetry = getattr(log_queue, "telemetry", Telemetry("unregistered"))
        self._stop_enqueued = False
    def handle(self, record):
        self.telemetry.flight(1)
        try:
            prepared = self.prepare(record)
            for handler in self.handlers:
                if self.respect_handler_level and prepared.levelno < handler.level:
                    continue
                try:
                    handler.handle(prepared)
                except Exception:
                    self.telemetry.increment("output_errors")
        finally:
            self.telemetry.increment("processed")
            self.telemetry.flight(-1)
    def stop(self, timeout=5):
        # One shared deadline for sentinel admission and joining the worker.
        thread = self._thread
        if thread is None:
            return True
        deadline = time.monotonic() + timeout
        try:
            if not self._stop_enqueued:
                self.queue.put(self._sentinel, timeout=max(0, deadline-time.monotonic()))
                self._stop_enqueued = True
        except queue.Full:
            self.telemetry.increment("shutdown_pending", self.queue.qsize())
            return False
        thread.join(max(0, deadline-time.monotonic()))
        if thread.is_alive():
            self.telemetry.increment("shutdown_pending", self.queue.qsize())
            return False
        self._thread = None
        self._stop_enqueued = False
        return True

class MeteredStreamHandler(logging.StreamHandler):
    def __init__(self, stream=None, *, telemetry):
        super().__init__(stream)
        self.telemetry = telemetry
    def handleError(self, record):
        self.telemetry.increment("output_errors")

class MeteredRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def __init__(self, *args, telemetry, **kwargs):
        super().__init__(*args, **kwargs)
        self.telemetry = telemetry
    def handleError(self, record):
        self.telemetry.increment("output_errors")

class QueueCollector:
    def collect(self):
        with _registry_lock:
            queues = list(_queues.values())
        gauges = {
            "size": GaugeMetricFamily("logging_queue_size", "Records waiting for output", labels=["appender"]),
            "capacity": GaugeMetricFamily("logging_queue_capacity", "Maximum queued records", labels=["appender"]),
            "flight": GaugeMetricFamily("logging_queue_in_flight", "Records being processed", labels=["appender"]),
        }
        keys = ["submitted", "enqueued", "processed", "full", "fallback", "enqueue_errors", "output_errors", "shutdown_pending", "enqueue_seconds"]
        counters = {k: CounterMetricFamily("logging_queue_"+k, "Logging queue "+k, labels=["appender"]) for k in keys}
        dropped = CounterMetricFamily("logging_queue_dropped", "Records rejected by logging", labels=["appender", "reason"])
        for q in queues:
            values, flight = q.telemetry.snapshot()
            name = q.telemetry.name
            gauges["size"].add_metric([name], q.qsize())
            gauges["capacity"].add_metric([name], q.maxsize)
            gauges["flight"].add_metric([name], flight)
            for k in keys:
                counters[k].add_metric([name], values.get(k, 0))
            dropped.add_metric([name, "full"], values.get("dropped_full", 0))
        yield from gauges.values()
        yield from counters.values()
        output_errors = CounterMetricFamily("logging_output_errors", "Reported output failures", labels=["appender"])
        for q in queues:
            output_errors.add_metric([q.telemetry.name], q.telemetry.snapshot()[0].get("output_errors", 0))
        yield output_errors
        yield dropped

REGISTRY.register(QueueCollector())

def start_internal_metrics():
    if os.getenv("OBSERVABILITY_ENABLED", "false").lower() != "true":
        return None
    return start_http_server(int(os.getenv("OBSERVABILITY_PORT", "9091")), addr=os.getenv("OBSERVABILITY_BIND", "0.0.0.0"))

def stop_internal_metrics(server):
    if server is not None:
        http, thread = server
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


class MetricsLifecycle:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "lifespan":
            return await self.app(scope, receive, send)
        server = start_internal_metrics()
        try:
            await self.app(scope, receive, send)
        finally:
            stop_internal_metrics(server)
