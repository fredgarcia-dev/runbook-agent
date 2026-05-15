# Runbook Automation Agent

An intelligent, multi-agent system that triages production incidents, retrieves relevant runbooks via semantic search, generates step-by-step remediation plans, and routes decisions based on severity — all fully observable through a production-grade Prometheus + Grafana stack.

Built as a portfolio project demonstrating **agentic AI**, **RAG**, and **LLM observability** working together.

---

## What It Does

```
Incident Description
        │
        ▼
┌─────────────────┐     Claude API (extended thinking)
│   TriageAgent   │────────────────────────────────────▶ SEV1 / SEV2 / SEV3
│                 │     confidence score · keywords
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ChromaDB + all-MiniLM-L6-v2
│ RunbookRetriever│────────────────────────────────────▶ Top-N relevant runbooks
│                 │     cosine similarity search
└────────┬────────┘
         │
         ▼
┌─────────────────┐     Claude API (streaming)
│RemediationAgent │────────────────────────────────────▶ Step-by-step plan
│                 │     commands · rollback · risk level
└────────┬────────┘
         │
         ▼
┌─────────────────┐     Pure Python — zero AI
│ SeverityRouter  │────────────────────────────────────▶ ESCALATE / REVIEW / AUTO
│                 │     deterministic safety boundary       (display only, never executes)
└─────────────────┘
         │
         ▼
  Prometheus metrics ──▶ Grafana dashboard
  LangSmith traces   ──▶ LLM observability
```

**Safety guarantee:** The system is purely advisory. `AUTO_EXECUTE` is a display label — no shell commands are ever run by the agent.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | Claude Opus 4.7 (Anthropic) — extended thinking + streaming |
| Vector DB | ChromaDB (local persistent) |
| Embeddings | `all-MiniLM-L6-v2` via sentence-transformers |
| Metrics | Prometheus + prometheus-client |
| Dashboards | Grafana 10 (fully provisioned) |
| System metrics | Node Exporter |
| LLM tracing | LangSmith (optional, graceful degradation) |
| CLI / display | Rich |
| Infrastructure | Docker Compose |

---

## Project Structure

```
runbook-agent/
├── main.py                          # CLI entry point + pipeline orchestrator
├── agents/
│   ├── triage_agent.py              # Claude-powered incident classifier
│   ├── runbook_retriever.py         # ChromaDB semantic search
│   ├── remediation_agent.py         # Claude-powered plan generator (streaming)
│   └── severity_router.py           # Deterministic routing (no AI)
├── observability/
│   ├── metrics.py                   # 8 custom Prometheus metrics
│   └── tracing.py                   # LangSmith integration (graceful degradation)
├── runbooks/                        # Markdown runbooks (RAG knowledge base)
├── scripts/
│   ├── load_demo.py                 # Synthetic load generator (no API calls)
│   └── health_check.py             # Stack validator
├── prometheus/prometheus.yml        # Scrape configuration
├── grafana/
│   ├── provisioning/               # Auto-wired datasource + dashboard loader
│   └── dashboards/runbook_agent.json  # 5-row, 17-panel dashboard
├── docker-compose.yml              # Full observability stack
├── Makefile                        # Convenience commands
└── BUILD_LOG.md                    # Phase-by-phase build history
```

---

## Quick Start

**Prerequisites:** Python 3.11+, Docker Desktop

```bash
# 1. Clone and set up
git clone https://github.com/YOUR_USERNAME/runbook-agent.git
cd runbook-agent
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env

# 3. Start the observability stack
make up

# 4. Run a demo incident
python main.py --demo sev1   # Critical: database cluster failure
python main.py --demo sev2   # High: memory leak / OOMKill
python main.py --demo sev3   # Low: disk space warning
python main.py --demo all    # All three scenarios sequentially

# 5. View dashboards
make open-grafana      # http://localhost:3000
make open-prometheus   # http://localhost:9090
```

---

## Observability Stack

### Prometheus Metrics (8 custom)

| Metric | Type | What It Measures |
|--------|------|-----------------|
| `runbook_incidents_total` | Counter | Incidents processed, by severity + type |
| `runbook_incident_duration_seconds` | Histogram | End-to-end pipeline time (MTTR proxy) |
| `runbook_step_duration_seconds` | Histogram | Per-step latency (triage / retrieval / remediation / routing) |
| `runbook_triage_confidence` | Histogram | Distribution of triage confidence scores |
| `runbook_remediation_confidence` | Histogram | Distribution of remediation confidence scores |
| `runbook_claude_api_requests_total` | Counter | Claude API calls by agent + model |
| `runbook_claude_api_latency_seconds` | Histogram | Claude API latency by agent + model |
| `runbook_kb_query_duration_seconds` | Histogram | ChromaDB retrieval latency |

### Grafana Dashboard (5 rows)

1. **Incident Overview** — total incidents, SEV breakdown, avg MTTR, API call count
2. **Pipeline Performance** — MTTR histogram by severity, step duration breakdown
3. **Agent Performance** — confidence score distributions (P50/P90/P99), routing decisions donut
4. **Claude API Metrics** — latency percentiles by agent, call rate over time
5. **Knowledge Base Health** — ChromaDB query duration, avg latency gauge, P99

### LangSmith Tracing (optional)

Add to `.env` to enable LLM trace monitoring:
```
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=runbook-agent
```
Gracefully disabled when key is absent — the agent runs identically either way.

---

## Makefile Commands

```bash
# Observability stack
make up              # Start Prometheus, Grafana, Node Exporter
make down            # Stop all containers
make logs            # Tail container logs
make ps              # Show running containers
make clean           # Remove containers + volumes (destructive)
make load            # Synthetic load generator — populates Grafana panels
make health          # Stack health validator — pass/warn/fail per component
make open-grafana    # Open http://localhost:3000
make open-prometheus # Open http://localhost:9090

# Kubernetes
make k8s-build       # Build + push Docker image to local registry
make k8s-deploy      # Deploy agent to Kubernetes cluster
make k8s-status      # Show pod and service status
make k8s-logs        # Tail live logs from the agent pod
make k8s-delete      # Remove agent from Kubernetes
```

---

## Kubernetes Deployment

The agent runs as a containerised workload on a local Kubernetes cluster (Docker Desktop). It exposes `/metrics` on port 8000 inside the pod, surfaced via a LoadBalancer service on `localhost:8001`. Prometheus scrapes it automatically.

### Handy kubectl Commands

```bash
# Status
kubectl get pods -l app=runbook-agent                      # pod name + ready state
kubectl get service runbook-agent                          # external IP + port mapping
kubectl get all -n default                                 # everything in the cluster

# Logs & debugging
kubectl logs -l app=runbook-agent --follow                 # live log stream
kubectl logs -l app=runbook-agent --previous               # logs from last crashed pod
kubectl describe pod -l app=runbook-agent                  # full detail + events
kubectl get events --sort-by=.metadata.creationTimestamp   # recent cluster events

# Shell into a running pod
kubectl exec -it $(kubectl get pod -l app=runbook-agent -o name) -- /bin/sh

# Rollouts
kubectl rollout status deployment/runbook-agent            # watch rollout progress
kubectl rollout restart deployment/runbook-agent           # rolling restart (zero downtime)
kubectl rollout undo deployment/runbook-agent              # rollback to previous version

# Scaling
kubectl scale deployment runbook-agent --replicas=2        # scale up
kubectl scale deployment runbook-agent --replicas=1        # scale back down

# Resource usage (requires metrics-server)
kubectl top pods -l app=runbook-agent                      # CPU + memory per pod

# Context management
kubectl config get-contexts                                # list all clusters
kubectl config use-context docker-desktop                  # switch to local cluster
```

---

## Demo Load Generator

Populates all Grafana panels with realistic data — no Claude API calls, no cost:

```bash
make load                                        # 30 incidents, default mix
venv/bin/python scripts/load_demo.py --count 100 # heavier load
```

Traffic distribution: 10% SEV1 · 25% SEV2 · 65% SEV3

---

## Health Check Validator

```bash
make health
```

Verifies: Prometheus up · Grafana up · Node Exporter up · all 8 metrics in TSDB ·
Prometheus targets configured · Grafana datasource + dashboard provisioned.

Exit code `0` = all pass (CI-friendly).

---

## Build Log

See [`BUILD_LOG.md`](BUILD_LOG.md) for a phase-by-phase record of what was built, key decisions, and architectural tradeoffs.
