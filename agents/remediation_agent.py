"""
RemediationAgent: Generates step-by-step remediation plans using the Claude API.
Takes triage results and retrieved runbooks as context.
Uses streaming so the caller can observe progress in real time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import anthropic

from .triage_agent import TriageResult
from .runbook_retriever import RunbookResult
from observability.metrics import time_claude_call, remediation_confidence
from observability.tracing import traceable_step


@dataclass
class RemediationPlan:
    steps: list[dict]              # [{"step": str, "command": str, "expected_outcome": str}]
    confidence: float              # 0.0 – 1.0
    estimated_time_minutes: int
    risk_level: str                # low | medium | high
    prerequisites: list[str]
    rollback_steps: list[str]
    summary: str


_SYSTEM_PROMPT = """\
You are a senior Site Reliability Engineer generating precise, executable remediation plans.

Given an incident triage and the most relevant runbooks, produce a comprehensive plan that an
on-call engineer (or an automated system) can follow step by step.

## Output Format

Return ONLY a JSON object — no markdown fences, no commentary:

{
  "steps": [
    {
      "step": "<human-readable description of the action>",
      "command": "<exact shell command, or empty string for manual/UI actions>",
      "expected_outcome": "<what success looks like after this step>"
    }
  ],
  "confidence": <float 0.0 – 1.0>,
  "estimated_time_minutes": <integer>,
  "risk_level": "low" | "medium" | "high",
  "prerequisites": ["<required access, credentials, or tooling>"],
  "rollback_steps": ["<step to undo each irreversible action>"],
  "summary": "<2–3 sentence description of the overall remediation approach>"
}

## Guidelines

1. **Stabilise first** — stop active impact before investigating root cause.
2. **Diagnose before destroy** — read-only checks before any destructive commands.
3. **Verify each action** — include a check step after each significant change.
4. **Honest confidence** — if the runbooks don't fully cover the scenario, lower confidence.
5. **Calibrated for routing**: confidence > 0.75 signals the plan is safe for auto-execution
   on SEV3 incidents; lower confidence requires human review.
"""


class RemediationAgent:
    """Generates remediation plans grounded in retrieved runbooks."""

    MODEL = "claude-opus-4-7"

    def __init__(self, client: anthropic.Anthropic) -> None:
        self.client = client

    @traceable_step(name="remediation_generate_plan", run_type="llm")
    def generate_plan(
        self,
        triage: TriageResult,
        runbooks: list[RunbookResult],
    ) -> RemediationPlan:
        runbook_ctx = "\n\n---\n\n".join(
            f"### {rb.title}  (relevance {rb.relevance_score:.1%})\n\n{rb.content}"
            for rb in runbooks
        )

        user_msg = (
            "## Incident Triage\n"
            f"- **Severity**: {triage.severity}\n"
            f"- **Type**: {triage.incident_type}\n"
            f"- **Confidence**: {triage.confidence:.1%}\n"
            f"- **Summary**: {triage.summary}\n"
            f"- **Keywords**: {', '.join(triage.keywords)}\n\n"
            "## Relevant Runbooks\n\n"
            f"{runbook_ctx}\n\n"
            "Generate a comprehensive remediation plan."
        )

        # Stream the response — plans can be verbose; streaming prevents timeouts
        with time_claude_call("remediation", self.MODEL):
            with self.client.messages.stream(
                model=self.MODEL,
                max_tokens=4096,
                thinking={"type": "adaptive"},
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_msg}],
            ) as stream:
                response = stream.get_final_message()

        text = _first_text(response)
        data = _parse_json(text)

        plan = RemediationPlan(
            steps=data.get("steps", []),
            confidence=float(data.get("confidence", 0.5)),
            estimated_time_minutes=int(data.get("estimated_time_minutes", 30)),
            risk_level=data.get("risk_level", "medium").lower(),
            prerequisites=data.get("prerequisites", []),
            rollback_steps=data.get("rollback_steps", []),
            summary=data.get("summary", ""),
        )
        remediation_confidence.observe(plan.confidence)
        return plan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_text(response: anthropic.types.Message) -> str:
    for block in response.content:
        if block.type == "text":
            return block.text
    raise ValueError("No text block in Claude response")


def _parse_json(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    raw = m.group(1).strip() if m else text.strip()
    return json.loads(raw)
