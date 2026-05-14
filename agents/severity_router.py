"""
SeverityRouter: Deterministic routing layer — ZERO AI involvement.

This module is the safety boundary for escalation decisions.
All logic is pure Python: no model calls, no network I/O, no randomness.

Rules
-----
SEV1  →  ESCALATE_HUMAN   (always; no exceptions)
SEV2  →  HUMAN_REVIEW     (always; no exceptions)
SEV3  →  AUTO_EXECUTE     iff plan.confidence > SEV3_THRESHOLD (0.75)
          HUMAN_REVIEW     otherwise
Other →  ESCALATE_HUMAN   (fail-safe default for unknown severities)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RoutingAction(Enum):
    AUTO_EXECUTE    = "auto_execute"
    HUMAN_REVIEW    = "human_review"
    ESCALATE_HUMAN  = "escalate_human"


@dataclass(frozen=True)
class RoutingDecision:
    action: RoutingAction
    reason: str
    severity: str
    confidence: float
    requires_human: bool
    can_auto_execute: bool


class SeverityRouter:
    """
    Deterministic router — no AI, no network calls.

    The SEV3 auto-execute threshold is the only tuneable knob.
    Changing it requires a deliberate code review, not a prompt change.
    """

    SEV3_THRESHOLD: float = 0.75   # confidence must strictly exceed this

    def route(self, severity: str, confidence: float) -> RoutingDecision:
        """Return a deterministic RoutingDecision based on severity and confidence."""
        sev = severity.strip().upper()

        if sev == "SEV1":
            return RoutingDecision(
                action=RoutingAction.ESCALATE_HUMAN,
                reason=(
                    "SEV1 critical incidents always require immediate human intervention. "
                    "Automated execution is prohibited regardless of confidence."
                ),
                severity=sev,
                confidence=confidence,
                requires_human=True,
                can_auto_execute=False,
            )

        if sev == "SEV2":
            return RoutingDecision(
                action=RoutingAction.HUMAN_REVIEW,
                reason=(
                    "SEV2 incidents require engineer review before any execution "
                    "to prevent cascading failures or unintended data loss."
                ),
                severity=sev,
                confidence=confidence,
                requires_human=True,
                can_auto_execute=False,
            )

        if sev == "SEV3":
            if confidence > self.SEV3_THRESHOLD:
                return RoutingDecision(
                    action=RoutingAction.AUTO_EXECUTE,
                    reason=(
                        f"SEV3 with confidence {confidence:.1%} exceeds the "
                        f"{self.SEV3_THRESHOLD:.0%} threshold — safe for automated execution."
                    ),
                    severity=sev,
                    confidence=confidence,
                    requires_human=False,
                    can_auto_execute=True,
                )
            return RoutingDecision(
                action=RoutingAction.HUMAN_REVIEW,
                reason=(
                    f"SEV3 but confidence {confidence:.1%} is at or below the "
                    f"{self.SEV3_THRESHOLD:.0%} auto-execute threshold — human review required."
                ),
                severity=sev,
                confidence=confidence,
                requires_human=True,
                can_auto_execute=False,
            )

        # Unknown severity — fail safe
        return RoutingDecision(
            action=RoutingAction.ESCALATE_HUMAN,
            reason=(
                f"Unrecognised severity '{sev}'. "
                "Defaulting to human escalation as a safety measure."
            ),
            severity=sev,
            confidence=confidence,
            requires_human=True,
            can_auto_execute=False,
        )
