# Internal logging queue metrics

Enable the dedicated internal listener with `OBSERVABILITY_ENABLED=true` and
`OBSERVABILITY_PORT=9091`. Kubernetes exposes it through a separate ClusterIP
Service and permits only the monitoring namespace's Prometheus pods to connect.
Existing application authentication, health checks and ingress are unchanged.

Metrics have fixed appender/reason labels only. They never contain log messages,
credentials, user identifiers or exception text. Queue depth is a snapshot; exact
rejections come from admission counters, not from polling queue depth.

- `logging_queue_size`, `logging_queue_capacity`, `logging_queue_in_flight`
- `logging_queue_submitted_total`, `logging_queue_enqueued_total`, `logging_queue_processed_total`
- `logging_queue_dropped_total`: actual rejected records, with a bounded reason label
- `logging_queue_enqueue_seconds_total`: cumulative admission time, including blocking
- `logging_output_errors_total`: reported sink failures, not an inferred durable-write count
- `logging_queue_shutdown_pending_total`: records pending at a shutdown deadline, not confirmed loss

Processed means the output handler was invoked; it does not prove fsync, archival
or downstream delivery. Durable audit/outbox delivery retains its own status.
Counters reset with the process; use `rate`/`increase` rather than comparing raw
values across restarts. Shutdown counters are primarily for local verification:
a stopped pod cannot be scraped reliably after its final update.

Kubernetes records queue utilization, five-minute drop rates and enqueue seconds
per submitted record. No new alert destinations or notifications are configured.
Suggested investigation thresholds: utilization >70% for 1 minute, >90% for
30 seconds, or any increase in drops/output errors. Tune only after production
observation; do not treat low traffic as a peak-load capacity test.

Knowledge retains the bounded audit queue and its existing durable SQLite outbox. Full queues synchronously write the local file: `logging_queue_full_total` and `logging_queue_fallback_total` increase, while dropped stays zero. Audit shutdown keeps its 1-second deadline; no retention, capacity or delivery ownership changes.
