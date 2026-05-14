"""
Health check validator for the Runbook Agent observability stack.

Checks every component of the stack and reports pass/fail with details.
Exit code 0 = all checks passed. Exit code 1 = one or more failures.

Usage
-----
  python scripts/health_check.py
  python scripts/health_check.py --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.table import Table

load_dotenv(Path(__file__).parent.parent / ".env")

console = Console()

PROMETHEUS_URL = "http://localhost:9090"
GRAFANA_URL    = "http://localhost:3000"
AGENT_URL      = "http://localhost:8000"
NODE_EXP_URL   = "http://localhost:9100"

GRAFANA_USER = os.getenv("GRAFANA_USER", "admin")
GRAFANA_PASS = os.getenv("GRAFANA_PASSWORD", "admin")

EXPECTED_METRICS = [
    "runbook_incidents_total",
    "runbook_incident_duration_seconds_bucket",
    "runbook_step_duration_seconds_bucket",
    "runbook_triage_confidence_bucket",
    "runbook_remediation_confidence_bucket",
    "runbook_claude_api_requests_total",
    "runbook_claude_api_latency_seconds_bucket",
    "runbook_kb_query_duration_seconds_bucket",
]

EXPECTED_TARGETS = {"prometheus", "node_exporter", "runbook_agent"}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str, auth: tuple[str, str] | None = None, timeout: int = 5) -> tuple[int, bytes]:
    req = urllib.request.Request(url)
    if auth:
        import base64
        creds = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return 0, str(e).encode()


# ---------------------------------------------------------------------------
# Check result
# ---------------------------------------------------------------------------

@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    warning: bool = False  # True = pass but worth noting


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_prometheus_up() -> Check:
    code, body = _get(f"{PROMETHEUS_URL}/-/healthy")
    if code == 200:
        return Check("Prometheus reachable", True, f"HTTP {code}")
    return Check("Prometheus reachable", False, f"HTTP {code} — is `make up` running?")


def check_grafana_up() -> Check:
    code, body = _get(f"{GRAFANA_URL}/api/health")
    if code == 200:
        data = json.loads(body)
        return Check("Grafana reachable", True, f"version {data.get('version', '?')}")
    return Check("Grafana reachable", False, f"HTTP {code}")


def check_node_exporter_up() -> Check:
    code, _ = _get(f"{NODE_EXP_URL}/metrics")
    if code == 200:
        return Check("Node Exporter reachable", True, f"HTTP {code}")
    return Check("Node Exporter reachable", False, f"HTTP {code}")


def check_agent_metrics_up() -> Check:
    code, body = _get(f"{AGENT_URL}/metrics")
    if code == 200:
        return Check("Agent /metrics endpoint", True, f"{len(body)} bytes")
    return Check(
        "Agent /metrics endpoint", False,
        "Not running — start with: python main.py --demo sev3",
        warning=True,
    )


def check_prometheus_targets() -> list[Check]:
    code, body = _get(f"{PROMETHEUS_URL}/api/v1/targets")
    if code != 200:
        return [Check("Prometheus targets API", False, f"HTTP {code}")]

    data = json.loads(body)
    targets = data.get("data", {}).get("activeTargets", [])
    checks = []
    seen = {}
    for t in targets:
        job = t["labels"].get("job", "?")
        health = t["health"]
        err = t.get("lastError", "")
        seen[job] = (health, err)

    for job in EXPECTED_TARGETS:
        if job not in seen:
            checks.append(Check(f"Target: {job}", False, "not found in Prometheus config"))
        else:
            health, err = seen[job]
            passed = health == "up"
            detail = f"health={health}" + (f" — {err}" if err else "")
            # runbook_agent being down is expected when main.py isn't running
            is_warning = (job == "runbook_agent" and not passed)
            checks.append(Check(f"Target: {job}", passed or is_warning, detail, warning=is_warning and not passed))

    return checks


def check_metrics_in_prometheus() -> list[Check]:
    code, body = _get(f"{PROMETHEUS_URL}/api/v1/label/__name__/values")
    if code != 200:
        return [Check("Prometheus metric names API", False, f"HTTP {code}")]

    data = json.loads(body)
    known = set(data.get("data", []))
    checks = []
    for metric in EXPECTED_METRICS:
        # Check if any metric starting with this name exists (handles _bucket/_count/_sum suffixes)
        base = metric.replace("_bucket", "").replace("_count", "").replace("_sum", "")
        found = any(m.startswith(base) for m in known)
        checks.append(Check(
            f"Metric: {metric}",
            found,
            "present in TSDB" if found else "not yet scraped — run `make load` first",
            warning=not found,
        ))
    return checks


def check_grafana_datasource() -> Check:
    code, body = _get(f"{GRAFANA_URL}/api/datasources", auth=(GRAFANA_USER, GRAFANA_PASS))
    if code == 401:
        return Check("Grafana Prometheus datasource", True,
                     "skipped — wrong credentials (set GRAFANA_PASSWORD in .env)", warning=True)
    if code != 200:
        return Check("Grafana Prometheus datasource", False, f"HTTP {code}")
    sources = json.loads(body)
    prom = next((s for s in sources if s.get("type") == "prometheus"), None)
    if not prom:
        return Check("Grafana Prometheus datasource", False, "no Prometheus datasource found")
    return Check("Grafana Prometheus datasource", True, f"url={prom.get('url')} isDefault={prom.get('isDefault')}")


def check_grafana_dashboard() -> Check:
    code, body = _get(
        f"{GRAFANA_URL}/api/dashboards/uid/runbook-agent-v1",
        auth=(GRAFANA_USER, GRAFANA_PASS),
    )
    if code == 401:
        return Check("Grafana dashboard provisioned", True,
                     "skipped — wrong credentials (set GRAFANA_PASSWORD in .env)", warning=True)
    if code == 200:
        data = json.loads(body)
        title = data.get("dashboard", {}).get("title", "?")
        panels = len(data.get("dashboard", {}).get("panels", []))
        return Check("Grafana dashboard provisioned", True, f"'{title}' · {panels} panels")
    return Check("Grafana dashboard provisioned", False, f"HTTP {code} — dashboard not found")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(verbose: bool) -> bool:
    console.print()
    console.print("[bold blue]Runbook Agent — Stack Health Check[/bold blue]")
    console.print()

    all_checks: list[Check] = []

    all_checks.append(check_prometheus_up())
    all_checks.append(check_grafana_up())
    all_checks.append(check_node_exporter_up())
    all_checks.append(check_agent_metrics_up())
    all_checks.extend(check_prometheus_targets())
    all_checks.append(check_grafana_datasource())
    all_checks.append(check_grafana_dashboard())
    all_checks.extend(check_metrics_in_prometheus())

    t = Table(box=box.ROUNDED, border_style="cyan", show_header=True)
    t.add_column("Check", style="bold", ratio=2)
    t.add_column("Status", width=8, justify="center")
    t.add_column("Detail", ratio=3)

    passed = failed = warned = 0
    for c in all_checks:
        if c.passed and not c.warning:
            status = "[bold green]PASS[/bold green]"
            passed += 1
        elif c.warning:
            status = "[bold yellow]WARN[/bold yellow]"
            warned += 1
        else:
            status = "[bold red]FAIL[/bold red]"
            failed += 1

        if verbose or not c.passed:
            t.add_row(c.name, status, c.detail)
        elif c.warning:
            t.add_row(c.name, status, c.detail)
        else:
            t.add_row(c.name, status, c.detail if verbose else "")

    console.print(t)
    console.print()

    color = "green" if failed == 0 else "red"
    console.print(
        f"[bold {color}]{passed} passed · {warned} warnings · {failed} failed[/bold {color}]"
    )

    if warned > 0:
        console.print("[dim]Warnings: runbook_agent target and metrics are expected to be absent when main.py is not running.[/dim]")

    console.print()
    return failed == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Health check validator for the Runbook Agent stack")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detail for all checks, not just failures")
    parser.add_argument("--grafana-user", default=None, help="Grafana username (overrides .env)")
    parser.add_argument("--grafana-pass", default=None, help="Grafana password (overrides .env)")
    args = parser.parse_args()

    global GRAFANA_USER, GRAFANA_PASS
    if args.grafana_user:
        GRAFANA_USER = args.grafana_user
    if args.grafana_pass:
        GRAFANA_PASS = args.grafana_pass

    ok = run(args.verbose)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
