"""
LangSmith LLM trace integration — graceful degradation.

If LANGSMITH_API_KEY is not set or the langsmith package is unavailable,
all functions in this module are safe no-ops. Nothing executes automatically.

Usage in agents:
    from observability.tracing import trace_span
    with trace_span("triage_classify", run_type="llm"):
        result = self.client.messages.create(...)
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Generator

# ---------------------------------------------------------------------------
# Detect availability
# ---------------------------------------------------------------------------

def _langsmith_enabled() -> bool:
    if not os.getenv("LANGSMITH_API_KEY"):
        return False
    try:
        import langsmith  # noqa: F401
        return True
    except ImportError:
        return False


ENABLED = _langsmith_enabled()


# ---------------------------------------------------------------------------
# Client wrapper
# ---------------------------------------------------------------------------

def maybe_wrap_client(client: Any) -> Any:
    """
    Wrap an anthropic.Anthropic client with LangSmith tracing if enabled.
    Returns the original client unchanged when LangSmith is not configured.
    """
    if not ENABLED:
        return client
    try:
        from langsmith.wrappers import wrap_anthropic
        return wrap_anthropic(client)
    except Exception:
        return client


# ---------------------------------------------------------------------------
# Trace context manager (replaces decorator — works reliably on methods)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def trace_span(name: str, run_type: str = "chain", metadata: dict | None = None) -> Generator[None, None, None]:
    """
    Context manager that wraps a block with a LangSmith trace span.
    Silent no-op when LangSmith is disabled.

    Usage:
        with trace_span("triage_classify", run_type="llm"):
            result = client.messages.create(...)
    """
    if not ENABLED:
        yield
        return
    try:
        from langsmith import trace
        with trace(name=name, run_type=run_type, metadata=metadata or {}):
            yield
    except Exception:
        yield


# ---------------------------------------------------------------------------
# Project setup (called once at startup)
# ---------------------------------------------------------------------------

def configure(project: str = "runbook-agent") -> None:
    """Enable tracing and set project name. Safe no-op when disabled."""
    if not ENABLED:
        return
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGSMITH_PROJECT", project)
    os.environ.setdefault("LANGCHAIN_PROJECT", project)


def status() -> str:
    """Human-readable status string for startup logging."""
    if ENABLED:
        project = os.environ.get("LANGSMITH_PROJECT", "runbook-agent")
        return f"LangSmith tracing enabled → project '{project}'"
    if not os.getenv("LANGSMITH_API_KEY"):
        return "LangSmith tracing disabled (LANGSMITH_API_KEY not set)"
    return "LangSmith tracing disabled (langsmith package not installed)"
