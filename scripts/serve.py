"""
Kubernetes server mode — runs continuously, generating synthetic metrics.
No Claude API calls required. Safe to run in a container.
Exposes /metrics on :8000 for Prometheus to scrape.

Usage
-----
  python scripts/serve.py                    # 10 incidents every 60s
  python scripts/serve.py --batch 20         # larger batches
  python scripts/serve.py --interval 30      # faster cycle
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from observability.metrics import start_metrics_server
from scripts.load_demo import _record, _simulate_incident

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="Runbook Agent — Kubernetes server mode")
    parser.add_argument("--batch", type=int, default=10, help="Incidents per cycle (default: 10)")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between cycles (default: 60)")
    args = parser.parse_args()

    start_metrics_server(8000)

    console.print()
    console.print("[bold blue]Runbook Agent — Kubernetes Server Mode[/bold blue]")
    console.print(f"[dim]Metrics → :8000/metrics · {args.batch} incidents every {args.interval}s[/dim]")
    console.print()

    batch = 0
    while True:
        batch += 1
        counts: dict[str, int] = {}
        for _ in range(args.batch):
            inc = _simulate_incident()
            _record(inc)
            counts[inc["severity"]] = counts.get(inc["severity"], 0) + 1

        console.print(
            f"[dim]Batch {batch} — "
            + "  ".join(f"{sev}: {n}" for sev, n in sorted(counts.items()))
            + "[/dim]"
        )
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
