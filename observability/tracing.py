"""
LangSmith LLM trace integration — graceful degradation.

If LANGSMITH_API_KEY is not set or the langsmith package is unavailable,
all functions in this module are safe no-ops. Nothing executes automatically.

Usage in main.py:
    from observability.tracing import maybe_wrap_client, traceable_step
    client = maybe_wrap_client(anthropic.Anthropic(...))

Usage in agents:
    from observability.tracing import traceable_step
    @traceable_step(name="triage")
    def classify(self, incident): ...
"""

from __future__ import annotations

import functools
import os
from typing import Any, Callable, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

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
    This is purely observational — no side effects on the client's behaviour.
    """
    if not ENABLED:
        return client
    try:
        from langsmith.wrappers import wrap_anthropic
        wrapped = wrap_anthropic(client)
        return wrapped
    except Exception:
        # Never let tracing break the main pipeline
        return client


# ---------------------------------------------------------------------------
# Step decorator
# ---------------------------------------------------------------------------

def traceable_step(name: str, run_type: str = "chain") -> Callable[[_F], _F]:
    """
    Decorator that wraps a method with a LangSmith trace span.
    When LangSmith is disabled this is a transparent pass-through.
    """
    def decorator(fn: _F) -> _F:
        if not ENABLED:
            return fn
        try:
            from langsmith import traceable
            return traceable(name=name, run_type=run_type)(fn)  # type: ignore[return-value]
        except Exception:
            return fn

    return decorator


# ---------------------------------------------------------------------------
# Project setup (called once at startup)
# ---------------------------------------------------------------------------

def configure(project: str = "runbook-agent") -> None:
    """
    Set the LangSmith project name if tracing is enabled.
    Safe to call unconditionally — no-op when disabled.
    """
    if not ENABLED:
        return
    # LANGCHAIN_PROJECT / LANGSMITH_PROJECT are the env vars LangSmith checks
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
