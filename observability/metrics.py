"""
Prometheus metrics for the Runbook Automation Agent.

All metric objects are module-level singletons — import and use them directly.
Call start_metrics_server() once at process startup to expose /metrics on :8000.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from prometheus_client import Counter, Histogram, start_http_server

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

# 1. Incidents processed
incidents_total = Counter(
    "runbook_incidents_total",
    "Total incidents processed by the pipeline",
    ["severity", "incident_type"],
)

# 2. End-to-end pipeline duration (MTTR proxy)
incident_duration_seconds = Histogram(
    "runbook_incident_duration_seconds",
    "End-to-end pipeline wall-clock time per incident",
    ["severity", "routing_action"],
    buckets=[5, 10, 20, 30, 60, 90, 120, 180, 300, 600],
)

# 3. Per-step latency (triage / retrieval / remediation / routing)
step_duration_seconds = Histogram(
    "runbook_step_duration_seconds",
    "Duration of each pipeline step",
    ["step"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 20, 30, 60],
)

# 4. Triage confidence score distribution
triage_confidence = Histogram(
    "runbook_triage_confidence",
    "Confidence scores returned by the triage agent",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# 5. Remediation plan confidence score distribution
remediation_confidence = Histogram(
    "runbook_remediation_confidence",
    "Confidence scores returned by the remediation agent",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# 6. Claude API call counter
claude_api_requests_total = Counter(
    "runbook_claude_api_requests_total",
    "Total Claude API calls",
    ["agent", "model"],
)

# 7. Claude API latency
claude_api_latency_seconds = Histogram(
    "runbook_claude_api_latency_seconds",
    "Claude API wall-clock latency per call",
    ["agent", "model"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 15, 20, 30],
)

# 8. ChromaDB knowledge-base query duration
kb_query_duration_seconds = Histogram(
    "runbook_kb_query_duration_seconds",
    "ChromaDB knowledge-base query latency",
    ["n_results"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@contextmanager
def time_step(step_name: str):
    """Context manager that records a step_duration_seconds observation."""
    start = time.perf_counter()
    try:
        yield
    finally:
        step_duration_seconds.labels(step=step_name).observe(
            time.perf_counter() - start
        )


@contextmanager
def time_claude_call(agent_name: str, model: str):
    """Context manager that records Claude API latency and increments call counter."""
    claude_api_requests_total.labels(agent=agent_name, model=model).inc()
    start = time.perf_counter()
    try:
        yield
    finally:
        claude_api_latency_seconds.labels(agent=agent_name, model=model).observe(
            time.perf_counter() - start
        )


def start_metrics_server(port: int = 8000) -> None:
    """Start the Prometheus /metrics HTTP server in a background daemon thread."""
    start_http_server(port)
