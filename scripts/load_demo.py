"""
Synthetic load generator for the Runbook Agent observability stack.

Simulates realistic incident traffic across all severity levels and incident
types — no Claude API calls, no cost. Populates all 8 Prometheus metrics with
distributions that mirror real production traffic, giving Grafana panels
meaningful data to visualise.

Usage
-----
  python scripts/load_demo.py                  # 30 incidents, default mix
  python scripts/load_demo.py --count 100      # heavier load
  python scripts/load_demo.py --scrapes 3      # wait for N Prometheus scrapes before exit

The script keeps the metrics server alive until Prometheus has scraped at least
--scrapes times after the last incident, then prints a summary and exits.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
import urllib.request
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich import box
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from observability.metrics import (
    claude_api_latency_seconds,
    claude_api_requests_total,
    incident_duration_seconds,
    incidents_total,
    kb_query_duration_seconds,
    remediation_confidence,
    start_metrics_server,
    step_duration_seconds,
    triage_confidence,
)

console = Console()

SCRAPE_INTERVAL = 15  # seconds — must match prometheus.yml global.scrape_interval

# ---------------------------------------------------------------------------
# Realistic traffic distribution
# ---------------------------------------------------------------------------

_SEVERITY_WEIGHTS = {"SEV1": 0.10, "SEV2": 0.25, "SEV3": 0.65}

_INCIDENT_TYPES_BY_SEV = {
    "SEV1": ["database_connection", "service_down"],
    "SEV2": ["memory_leak", "high_cpu", "network_latency"],
    "SEV3": ["disk_space", "high_cpu", "memory_leak"],
}

_MODEL = "claude-opus-4-7"


def _beta_sample(mean: float, spread: float = 0.08) -> float:
    """Beta-distributed sample clipped to [0.01, 0.99]."""
    a = mean / spread
    b = (1 - mean) / spread
    return max(0.01, min(0.99, random.betavariate(a, b)))


def _simulate_incident() -> dict:
    """Return a dict of realistic metric values for one synthetic incident."""
    severity = random.choices(
        list(_SEVERITY_WEIGHTS.keys()), weights=list(_SEVERITY_WEIGHTS.values())
    )[0]
    incident_type = random.choice(_INCIDENT_TYPES_BY_SEV[severity])

    # Claude API latency (seconds) — triage is faster than remediation
    triage_lat = random.uniform(4, 18)
    remediation_lat = random.uniform(12, 55)

    # Step durations
    retrieval_dur = random.uniform(0.05, 0.4)
    routing_dur = random.uniform(0.001, 0.005)

    # Confidence scores
    triage_conf = _beta_sample(0.86)
    # Remediation is less confident for SEV1 (complex scenarios)
    remediation_mean = {"SEV1": 0.62, "SEV2": 0.74, "SEV3": 0.82}[severity]
    remediation_conf = _beta_sample(remediation_mean)

    # Routing action (deterministic logic mirroring SeverityRouter)
    if severity == "SEV1":
        routing_action = "escalate_human"
    elif severity == "SEV2":
        routing_action = "human_review"
    else:
        routing_action = "auto_execute" if remediation_conf > 0.75 else "human_review"

    mttr = triage_lat + retrieval_dur + remediation_lat + routing_dur
    kb_dur = random.uniform(0.04, 0.28)

    return {
        "severity": severity,
        "incident_type": incident_type,
        "routing_action": routing_action,
        "triage_lat": triage_lat,
        "remediation_lat": remediation_lat,
        "retrieval_dur": retrieval_dur,
        "routing_dur": routing_dur,
        "triage_conf": triage_conf,
        "remediation_conf": remediation_conf,
        "mttr": mttr,
        "kb_dur": kb_dur,
    }


def _record(inc: dict) -> None:
    """Push one synthetic incident into the Prometheus metrics registry."""
    sev = inc["severity"]

    incidents_total.labels(severity=sev, incident_type=inc["incident_type"]).inc()

    incident_duration_seconds.labels(
        severity=sev, routing_action=inc["routing_action"]
    ).observe(inc["mttr"])

    step_duration_seconds.labels(step="triage").observe(inc["triage_lat"])
    step_duration_seconds.labels(step="retrieval").observe(inc["retrieval_dur"])
    step_duration_seconds.labels(step="remediation").observe(inc["remediation_lat"])
    step_duration_seconds.labels(step="routing").observe(inc["routing_dur"])

    triage_confidence.observe(inc["triage_conf"])
    remediation_confidence.observe(inc["remediation_conf"])

    claude_api_requests_total.labels(agent="triage", model=_MODEL).inc()
    claude_api_latency_seconds.labels(agent="triage", model=_MODEL).observe(inc["triage_lat"])

    claude_api_requests_total.labels(agent="remediation", model=_MODEL).inc()
    claude_api_latency_seconds.labels(agent="remediation", model=_MODEL).observe(inc["remediation_lat"])

    kb_query_duration_seconds.labels(n_results="2").observe(inc["kb_dur"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(count: int, scrapes: int) -> None:
    start_metrics_server(8000)
    console.print()
    console.print(f"[bold blue]Runbook Agent — Synthetic Load Generator[/bold blue]")
    console.print(f"[dim]Generating {count} incidents · keeping server alive for {scrapes} Prometheus scrape(s)[/dim]")
    console.print()

    summary: dict[str, int] = {"SEV1": 0, "SEV2": 0, "SEV3": 0}
    routing_counts: dict[str, int] = {}
    total_mttr = 0.0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
        transient=True,
    ) as prog:
        task = prog.add_task("Simulating incidents…", total=count)
        for _ in range(count):
            inc = _simulate_incident()
            _record(inc)
            summary[inc["severity"]] += 1
            routing_counts[inc["routing_action"]] = routing_counts.get(inc["routing_action"], 0) + 1
            total_mttr += inc["mttr"]
            prog.advance(task)
            time.sleep(random.uniform(0.02, 0.08))  # slight spread so timestamps differ

    # Print summary table
    t = Table(box=box.ROUNDED, border_style="cyan", title="Synthetic Load Summary")
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")
    t.add_row("Total incidents", str(count))
    t.add_row("SEV1 (critical)", str(summary["SEV1"]))
    t.add_row("SEV2 (high)", str(summary["SEV2"]))
    t.add_row("SEV3 (low)", str(summary["SEV3"]))
    t.add_row("Avg MTTR", f"{total_mttr / count:.1f}s")
    for action, n in sorted(routing_counts.items()):
        t.add_row(f"  → {action}", str(n))
    console.print(t)
    console.print()

    # Wait for Prometheus to scrape the populated metrics
    console.print(f"[dim]Waiting for {scrapes} Prometheus scrape cycle(s) ({scrapes * SCRAPE_INTERVAL}s)…[/dim]")
    for i in range(1, scrapes + 1):
        time.sleep(SCRAPE_INTERVAL)
        console.print(f"[dim]  Scrape {i}/{scrapes} complete[/dim]")

    console.print()
    console.print("[bold green]Done.[/bold green] Open Grafana → Runbook Agent dashboard to see the data.")
    console.print("[dim]http://localhost:3000[/dim]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic load generator for the Runbook Agent")
    parser.add_argument("--count", type=int, default=30, help="Number of incidents to simulate (default: 30)")
    parser.add_argument("--scrapes", type=int, default=2, help="Prometheus scrape cycles to wait for after generation (default: 2)")
    args = parser.parse_args()
    run(args.count, args.scrapes)


if __name__ == "__main__":
    main()
