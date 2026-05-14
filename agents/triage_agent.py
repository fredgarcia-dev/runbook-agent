"""
TriageAgent: Classifies incidents using the Claude API.
Returns severity (SEV1/SEV2/SEV3), incident type, confidence, and keywords.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import anthropic

from observability.metrics import time_claude_call, triage_confidence
from observability.tracing import trace_span


@dataclass
class TriageResult:
    severity: str        # SEV1 | SEV2 | SEV3
    incident_type: str   # disk_space | high_cpu | memory_leak | service_down | network_latency | database_connection
    confidence: float    # 0.0 – 1.0
    keywords: list[str]
    summary: str

    def __str__(self) -> str:
        return (
            f"[{self.severity}] {self.incident_type} "
            f"(confidence={self.confidence:.1%}): {self.summary}"
        )


_SYSTEM_PROMPT = """\
You are an expert Site Reliability Engineer (SRE) specialising in incident triage.

## Severity Levels

**SEV1 — Critical**
Complete service outage, active data loss or corruption risk, security breach, or
revenue-impacting failure affecting all/most users.  Requires immediate 24/7 response.
Examples: database cluster down, auth service offline, payment processing failing for all users,
          active ransomware, full disk blocking all writes.

**SEV2 — High**
Major feature unavailable, significant performance degradation, or impact on many users where
limited workarounds exist.  Requires response within 1–4 hours.
Examples: 50 %+ elevated error rate, memory pressure causing widespread timeouts, replica lag
          exceeding RPO, disk > 90 % and climbing.

**SEV3 — Low / Medium**
Minor issues, isolated degradation, slow-moving trends, or single-user impact.
Requires response within one business day.
Examples: disk at 70–85 % and stable, non-critical service flapping once, single-user
          connectivity issue, cache hit-rate slightly low.

## Incident Types
disk_space          — disk / storage capacity issues
high_cpu            — CPU utilisation spikes or sustained high load
memory_leak         — memory consumption growing unbounded
service_down        — process / container / service unavailable or crashing
network_latency     — network connectivity or latency degradation
database_connection — DB connection pool, query performance, or connectivity problems

## Output Format

Return ONLY a JSON object — no markdown fences, no commentary:

{
  "severity": "SEV1" | "SEV2" | "SEV3",
  "incident_type": "<one of the types above>",
  "confidence": <float 0.0 – 1.0>,
  "keywords": ["<word>", ...],
  "summary": "<1–2 sentence description of the incident>"
}

When uncertain between two severities, pick the higher one.
Confidence reflects how precisely the description maps to a known incident type.
"""


class TriageAgent:
    """Classifies incidents into severity levels via the Claude API."""

    MODEL = "claude-opus-4-7"

    def __init__(self, client: anthropic.Anthropic) -> None:
        self.client = client

    def classify(self, incident_description: str) -> TriageResult:
        with trace_span("triage_classify", run_type="llm"):
            with time_claude_call("triage", self.MODEL):
                response = self.client.messages.create(
                    model=self.MODEL,
                    max_tokens=1024,
                    thinking={"type": "adaptive"},
                    system=[
                        {
                            "type": "text",
                            "text": _SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[
                        {
                            "role": "user",
                            "content": f"Classify this incident:\n\n{incident_description}",
                        }
                    ],
                )

        text = _first_text(response)
        data = _parse_json(text)

        result = TriageResult(
            severity=data["severity"].upper(),
            incident_type=data["incident_type"].lower(),
            confidence=float(data["confidence"]),
            keywords=[str(k) for k in data.get("keywords", [])],
            summary=data["summary"],
        )
        triage_confidence.observe(result.confidence)
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_text(response: anthropic.types.Message) -> str:
    for block in response.content:
        if block.type == "text":
            return block.text
    raise ValueError("No text block in Claude response")


def _parse_json(text: str) -> dict:
    # Strip markdown fences if Claude added them despite instructions
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    raw = m.group(1).strip() if m else text.strip()
    return json.loads(raw)
