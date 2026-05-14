"""
Intelligent Runbook Automation Agent
=====================================
Pipeline:  TriageAgent → RunbookRetriever → RemediationAgent → SeverityRouter

Usage
-----
  python main.py --demo sev1          # run the SEV1 demo scenario
  python main.py --demo sev2          # run the SEV2 demo scenario
  python main.py --demo sev3          # run the SEV3 demo scenario
  python main.py --demo all           # run all three scenarios sequentially
  python main.py --incident "text"    # process a custom incident description

Environment
-----------
  ANTHROPIC_API_KEY must be set in .env or the shell environment.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

import anthropic

from agents.triage_agent import TriageAgent
from agents.runbook_retriever import RunbookRetriever
from agents.remediation_agent import RemediationAgent
from agents.severity_router import SeverityRouter, RoutingAction
from observability.metrics import (
    incidents_total,
    incident_duration_seconds,
    start_metrics_server,
    time_step,
)
from observability.tracing import configure as configure_tracing, maybe_wrap_client, status as tracing_status

load_dotenv()

console = Console()

# ---------------------------------------------------------------------------
# Demo incident descriptions
# ---------------------------------------------------------------------------

DEMO_INCIDENTS: dict[str, str] = {
    "sev1": (
        "CRITICAL ALERT — Production database cluster failure\n\n"
        "Our primary PostgreSQL cluster (3-node) has gone completely offline. "
        "All three nodes are returning 'connection refused'. "
        "The application is throwing database connection errors on 100% of requests. "
        "We have approximately 50,000 active users affected and revenue-generating transactions "
        "are failing. Replica nodes also appear to be down. "
        "Last successful transaction was logged 4 minutes ago. "
        "Error logs show: 'FATAL: the database system is in recovery mode' on all nodes. "
        "On-call engineer suspects the primary had a catastrophic disk failure during peak hours."
    ),
    "sev2": (
        "HIGH PRIORITY — Memory spike causing widespread service degradation\n\n"
        "Our main API service (running in Kubernetes, 8 replicas) is experiencing severe memory "
        "pressure. Memory utilisation has climbed from a normal 2 GB to 7.5 GB per pod over "
        "the last 45 minutes and is still rising. "
        "3 of 8 pods have already been OOMKilled and restarted. "
        "Surviving pods are responding very slowly — P99 latency is 12 seconds vs normal 200 ms. "
        "Approximately 60% of user requests are timing out. "
        "The issue started after a deployment 50 minutes ago that added a new caching layer. "
        "Pod logs show: 'GC overhead limit exceeded' repeating every few seconds."
    ),
    "sev3": (
        "NOTICE — Disk space approaching limit on app-server-01\n\n"
        "Monitoring alert for app-server-01 (production web server): "
        "root partition (/dev/sda1) is at 78% capacity (156 GB used of 200 GB). "
        "Usage has been growing at approximately 2 GB per day for the past week. "
        "At current rate, the disk will be full in roughly 11 days. "
        "Services are operating normally with no current user impact. "
        "Initial investigation shows /var/log is consuming 45 GB, "
        "and /app/uploads contains 30 GB of files older than 60 days. "
        "Log rotation appears to be misconfigured."
    ),
}


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_SEV_STYLES = {"SEV1": "bold red", "SEV2": "bold yellow", "SEV3": "bold green"}
_ACTION_STYLES = {
    RoutingAction.ESCALATE_HUMAN: "bold red",
    RoutingAction.HUMAN_REVIEW: "bold yellow",
    RoutingAction.AUTO_EXECUTE: "bold green",
}
_ACTION_BANNERS = {
    RoutingAction.ESCALATE_HUMAN: (
        "red",
        "🚨  ESCALATING TO ON-CALL ENGINEER — Immediate human intervention required",
    ),
    RoutingAction.HUMAN_REVIEW: (
        "yellow",
        "📋  HUMAN REVIEW REQUIRED — Remediation plan queued for engineer approval",
    ),
    RoutingAction.AUTO_EXECUTE: (
        "green",
        "✅  AUTO-EXECUTING REMEDIATION — Running approved plan automatically",
    ),
}


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

class RunbookAutomationSystem:
    def __init__(self) -> None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            console.print("[bold red]ERROR:[/bold red] ANTHROPIC_API_KEY is not set.")
            console.print("Copy [dim].env.example[/dim] → [dim].env[/dim] and add your key.")
            sys.exit(1)

        configure_tracing()
        console.print(f"[dim]{tracing_status()}[/dim]")

        raw_client = anthropic.Anthropic(api_key=api_key)
        self._client = maybe_wrap_client(raw_client)

        base = Path(__file__).parent
        runbooks_dir = str(base / "runbooks")
        db_path = str(base / "data" / "chroma_db")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as prog:
            prog.add_task("Loading models and index…", total=None)
            self._triage = TriageAgent(self._client)
            self._retriever = RunbookRetriever(runbooks_dir, db_path)
            self._remediation = RemediationAgent(self._client)
            self._router = SeverityRouter()

    # ------------------------------------------------------------------

    def run(self, incident: str) -> None:
        import time as _time
        pipeline_start = _time.perf_counter()

        console.print()
        console.print(Rule("[bold blue]Runbook Automation Agent[/bold blue]"))
        console.print()
        console.print(
            Panel(incident.strip(), title="[bold]Incident Description[/bold]", border_style="blue")
        )
        console.print()

        # ── Step 1: Triage ────────────────────────────────────────────
        console.print(Rule("Step 1  ·  Triage", style="cyan"))
        with _spinner("Classifying incident with Claude…"):
            with time_step("triage"):
                triage = self._triage.classify(incident)

        sev_style = _SEV_STYLES.get(triage.severity, "white")
        t = Table(box=box.ROUNDED, show_header=False, border_style="cyan", padding=(0, 1))
        t.add_column("Field", style="bold", width=14)
        t.add_column("Value")
        t.add_row("Severity", Text(triage.severity, style=sev_style))
        t.add_row("Type", triage.incident_type)
        t.add_row("Confidence", f"{triage.confidence:.1%}")
        t.add_row("Summary", triage.summary)
        t.add_row("Keywords", ", ".join(triage.keywords))
        console.print(t)
        console.print()

        # ── Step 2: Runbook Retrieval ─────────────────────────────────
        console.print(Rule("Step 2  ·  Runbook Retrieval  (ChromaDB + all-MiniLM-L6-v2)", style="cyan"))
        query = f"{triage.incident_type} {' '.join(triage.keywords)}"
        with time_step("retrieval"):
            runbooks = self._retriever.search(query, n_results=2)

        rb_table = Table(box=box.ROUNDED, border_style="cyan", padding=(0, 1))
        rb_table.add_column("Runbook", style="bold")
        rb_table.add_column("Relevance", justify="right")
        for rb in runbooks:
            rb_table.add_row(rb.title, f"{rb.relevance_score:.1%}")
        console.print(rb_table)
        console.print()

        # ── Step 3: Remediation Plan ──────────────────────────────────
        console.print(Rule("Step 3  ·  Remediation Planning", style="cyan"))
        with _spinner("Generating remediation plan with Claude…"):
            with time_step("remediation"):
                plan = self._remediation.generate_plan(triage, runbooks)

        steps_table = Table(box=box.ROUNDED, border_style="cyan", padding=(0, 1))
        steps_table.add_column("#", style="bold", width=3, justify="right")
        steps_table.add_column("Action", ratio=3)
        steps_table.add_column("Command", ratio=2, style="dim")
        for i, step in enumerate(plan.steps, 1):
            cmd = step.get("command") or "—"
            steps_table.add_row(str(i), step.get("step", ""), cmd)
        console.print(steps_table)

        meta = Table(box=box.ROUNDED, show_header=False, border_style="cyan", padding=(0, 1))
        meta.add_column("", style="bold", width=22)
        meta.add_column("")
        meta.add_row("Confidence", f"{plan.confidence:.1%}")
        meta.add_row("Estimated time", f"{plan.estimated_time_minutes} min")
        meta.add_row("Risk level", plan.risk_level)
        meta.add_row("Summary", plan.summary)
        if plan.prerequisites:
            meta.add_row("Prerequisites", "\n".join(f"• {p}" for p in plan.prerequisites))
        if plan.rollback_steps:
            meta.add_row("Rollback", "\n".join(f"• {r}" for r in plan.rollback_steps[:3]))
        console.print(meta)
        console.print()

        # ── Step 4: Routing (deterministic) ──────────────────────────
        console.print(Rule("Step 4  ·  Routing Decision  [dim](pure Python — no AI)[/dim]", style="cyan"))
        with time_step("routing"):
            routing = self._router.route(triage.severity, plan.confidence)

        action_style = _ACTION_STYLES.get(routing.action, "white")
        r_table = Table(box=box.ROUNDED, show_header=False, border_style="cyan", padding=(0, 1))
        r_table.add_column("", style="bold", width=18)
        r_table.add_column("")
        r_table.add_row(
            "Decision",
            Text(routing.action.value.upper().replace("_", " "), style=action_style),
        )
        r_table.add_row("Reason", routing.reason)
        r_table.add_row("Requires human", "Yes" if routing.requires_human else "No")
        r_table.add_row("Can auto-execute", "Yes" if routing.can_auto_execute else "No")
        console.print(r_table)
        console.print()

        border_color, banner_text = _ACTION_BANNERS[routing.action]
        console.print(Panel(f"[bold {border_color}]{banner_text}[/bold {border_color}]", border_style=border_color))
        console.print()

        # ── Record pipeline-level metrics ────────────────────────────
        incidents_total.labels(
            severity=triage.severity,
            incident_type=triage.incident_type,
        ).inc()
        incident_duration_seconds.labels(
            severity=triage.severity,
            routing_action=routing.action.value,
        ).observe(_time.perf_counter() - pipeline_start)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spinner(msg: str):
    return Progress(
        SpinnerColumn(),
        TextColumn(f"[progress.description]{msg}"),
        console=console,
        transient=True,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    start_metrics_server(port=8000)

    parser = argparse.ArgumentParser(
        description="Intelligent Runbook Automation Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--demo",
        choices=["sev1", "sev2", "sev3", "all"],
        help="Run a built-in demo scenario",
    )
    group.add_argument(
        "--incident",
        metavar="DESCRIPTION",
        help="Process a custom incident description",
    )
    args = parser.parse_args()

    system = RunbookAutomationSystem()

    if args.incident:
        system.run(args.incident)
        console.input("\n[dim]Metrics live on :8000 — press Enter to exit[/dim]")
        return

    # Demo mode
    scenarios = ["sev1", "sev2", "sev3"] if args.demo == "all" else [args.demo]
    for name in scenarios:
        console.print()
        console.print(
            Panel(
                f"[bold]Demo Scenario: {name.upper()}[/bold]",
                style="bold blue",
                padding=(1, 4),
            )
        )
        system.run(DEMO_INCIDENTS[name])
        if name != scenarios[-1]:
            console.input("\n[dim]Press Enter to continue to the next scenario…[/dim]")

    console.input("\n[dim]Metrics live on :8000 — press Enter to exit[/dim]")


if __name__ == "__main__":
    main()
