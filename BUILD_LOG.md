# Runbook Agent — Observability Build Log

Tracks what was built in each phase, why, and key decisions.

---

## Phase 1 — Docker Compose Observability Stack ✅
**Date:** 2026-05-13

### What was built
| File | Purpose |
|------|---------|
| `docker-compose.yml` | Prometheus 2.52, Grafana 10.4, Node Exporter 1.8 |
| `prometheus/prometheus.yml` | Scrape configs for Prometheus, Node Exporter, and the agent (stub) |
| `grafana/provisioning/datasources/prometheus.yml` | Auto-wires Prometheus as Grafana's default datasource |
| `grafana/provisioning/dashboards/default.yml` | File-based dashboard loader pointing at `grafana/dashboards/` |
| `Makefile` | `up`, `down`, `logs`, `ps`, `clean`, `open-grafana`, `open-prometheus` |

### Key decisions
- **Node Exporter** — removed `/:/host:ro,rslave` bind-mount and `--path.rootfs=/host`; Docker Desktop on macOS cannot share `/` with rslave propagation (Linux-only). Node Exporter still reports CPU/memory/network from inside Docker's VM.
- **Prometheus retention** — 7-day TSDB retention; fine for a demo stack.
- **Grafana auth** — defaults to `admin/admin`, overridable via `GRAFANA_USER` / `GRAFANA_PASSWORD` env vars.
- **Port layout** — Prometheus: 9090, Grafana: 3000, Node Exporter: 9100, Agent metrics (Phase 2): 8000.

---

## Phase 2 — Custom Prometheus Metrics Instrumentation ✅
**Date:** 2026-05-13

### What was built
| File | Purpose |
|------|---------|
| `observability/__init__.py` | Package marker |
| `observability/metrics.py` | All 8 metric definitions + context-manager helpers |
| `agents/triage_agent.py` | Instrumented: Claude API latency, call count, confidence score |
| `agents/remediation_agent.py` | Instrumented: Claude API latency, call count, confidence score |
| `agents/runbook_retriever.py` | Instrumented: ChromaDB query duration |
| `main.py` | Starts `/metrics` HTTP server on :8000; records incident counter, step durations, MTTR |

### 8 custom metrics
| Metric | Type | Labels | What it measures |
|--------|------|--------|-----------------|
| `runbook_incidents_total` | Counter | `severity`, `incident_type` | Incidents processed |
| `runbook_incident_duration_seconds` | Histogram | `severity`, `routing_action` | End-to-end pipeline time (MTTR proxy) |
| `runbook_step_duration_seconds` | Histogram | `step` | Per-step latency (triage / retrieval / remediation / routing) |
| `runbook_triage_confidence` | Histogram | — | Distribution of triage confidence scores |
| `runbook_remediation_confidence` | Histogram | — | Distribution of remediation confidence scores |
| `runbook_claude_api_requests_total` | Counter | `agent`, `model` | Claude API calls by agent |
| `runbook_claude_api_latency_seconds` | Histogram | `agent`, `model` | Claude API wall-clock latency |
| `runbook_kb_query_duration_seconds` | Histogram | `n_results` | ChromaDB retrieval latency |

### Key decisions
- **`prometheus_client.start_http_server(8000)`** runs a background daemon thread; zero impact on CLI behaviour.
- All metrics live in `observability/metrics.py`; agents import only what they need — no circular deps.
- Histograms use custom buckets tuned to expected ranges (e.g., Claude API 0.1–30 s, MTTR 0–600 s).

---

## Phase 3 — LangSmith LLM Trace Integration ✅
**Date:** 2026-05-13

### What was built
| File | Purpose |
|------|---------|
| `observability/tracing.py` | LangSmith setup with full graceful degradation |
| `agents/triage_agent.py` | `@traceable_step` on `classify()` |
| `agents/remediation_agent.py` | `@traceable_step` on `generate_plan()` |
| `main.py` | Calls `configure_tracing()` + `maybe_wrap_client()` at startup; prints status |
| `.env.example` | Documents `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` as optional |

### Key decisions
- **Opt-in only** — tracing is completely disabled unless `LANGSMITH_API_KEY` is present in the environment. No key = no network calls, no side effects, no exceptions.
- **`wrap_anthropic`** — LangSmith's official Anthropic wrapper traces all `messages.create` calls automatically, including inputs/outputs and token counts.
- **`@traceable_step`** — decorates `classify()` and `generate_plan()` so each pipeline run appears as a named span in LangSmith.
- **Never breaks the pipeline** — every tracing call is wrapped in `try/except`; failures are silently swallowed.
- **Safety** — no subprocess calls, no shell execution, no file writes, no background threads beyond what Phase 2 already started. Purely observational.

---

## Phase 4 — Grafana Dashboard (Provisioned JSON) ✅
**Date:** 2026-05-13

### What was built
| File | Purpose |
|------|---------|
| `grafana/dashboards/runbook_agent.json` | Fully provisioned dashboard, auto-loaded by Grafana |

### Dashboard layout — 5 rows, 17 data panels
| Row | Panels |
|-----|--------|
| Incident Overview | Total incidents (stat), SEV1/2/3 counts (stat), Avg MTTR (stat), Claude API calls (stat) |
| Pipeline Performance | Pipeline duration by severity P50/P90/P99 (timeseries), Avg step duration (timeseries) |
| Agent Performance | Triage confidence P50/P90/P99 (timeseries), Remediation confidence (timeseries), Routing decisions (donut) |
| Claude API Metrics | API latency by agent P50/P90/P99 (timeseries), API call rate by agent (timeseries) |
| Knowledge Base Health | ChromaDB query duration P50/P90/P99 (timeseries), Avg latency (gauge), Total queries (stat), P99 (stat) |

### Key decisions
- **Provisioned via file** — Grafana picks up the JSON automatically via `grafana/provisioning/dashboards/default.yml`; no manual import needed.
- **`${datasource}` template variable** — dashboard is datasource-agnostic; works with any Prometheus instance.
- **`$__rate_interval`** — Grafana's automatic rate interval prevents histogram errors when the scrape interval is sparse.
- **`or vector(0)`** on counters — prevents "No data" on stat panels before the first pipeline run.
- **30s auto-refresh** — matches Prometheus scrape interval for near-real-time updates.

---

## Phase 5 — Demo Load Scripts & Health Check Validators ✅
**Date:** 2026-05-14

### What was built
| File | Purpose |
|------|---------|
| `scripts/load_demo.py` | Synthetic load generator — simulates realistic incident traffic across all severities with no Claude API calls |
| `scripts/health_check.py` | Stack validator — checks all 8 custom metrics, Prometheus targets, Grafana datasource + dashboard |
| `Makefile` | Added `make load` and `make health` targets |

### Key decisions
- **No real API calls in load script** — uses beta distributions to generate realistic confidence scores and latency values; free to run as many times as needed.
- **Realistic traffic mix** — 10% SEV1, 25% SEV2, 65% SEV3 by default, matching production incident distributions.
- **`--scrapes N`** — waits for N × 15s scrape cycles before exiting so Prometheus captures the data.
- **Health check reads Grafana creds from `.env`** — falls back to `admin/admin`; also accepts `--grafana-pass` CLI override.
- **Exit code** — health check exits 0 (all pass) or 1 (any hard failure), making it CI-friendly.
- **Warnings vs failures** — `runbook_agent` target being DOWN and metrics missing from TSDB are WARNs (expected when `main.py` isn't running), not FAILs.

---

## Phase 6 — Documentation ✅
**Date:** 2026-05-14

### What was built
| File | Purpose |
|------|---------|
| `README.md` | Full project documentation — architecture diagram, quick start, observability details, Makefile reference |

---

## LinkedIn Post + GitHub Publish ✅
GitHub: https://github.com/fredgarcia-dev/runbook-agent

---

## Phase 7 — Local Kubernetes Deployment (Docker Desktop) ✅
**Date:** 2026-05-15

### What was built
| File | Purpose |
|------|---------|
| `Dockerfile` | Containerizes the agent using python:3.12-slim |
| `.dockerignore` | Excludes venv, .env, data/, docs/ from the image |
| `scripts/serve.py` | K8s-friendly server mode — generates synthetic metrics in a loop, no API calls |
| `k8s/deployment.yaml` | Kubernetes Deployment with readiness + liveness probes |
| `k8s/service.yaml` | LoadBalancer service exposing metrics on :8001 |
| `prometheus/prometheus.yml` | Added `runbook_agent_k8s` scrape job on host.docker.internal:8001 |
| `Makefile` | Added `k8s-build`, `k8s-deploy`, `k8s-status`, `k8s-logs`, `k8s-delete` |

### Key decisions
- **Local registry** — Docker Desktop K8s uses `containerd`, not Docker's daemon. Images must be pushed to a local registry (`localhost:5001`) to be available to pods.
- **LoadBalancer over NodePort** — NodePort services aren't accessible on `localhost` on Mac with Docker Desktop. LoadBalancer type gets a reachable external IP automatically.
- **Port 8001** — K8s service uses port 8001 externally (targeting pod :8000) to avoid conflicting with local `main.py` metrics server on :8000.
- **serve.py** — separate server mode for K8s that generates synthetic metrics in a loop; no Claude API key or ChromaDB required inside the container.
- **Prometheus reload** — `docker compose restart prometheus` required after adding the new scrape job (volume-mounted config, `/-/reload` endpoint had a caching issue).
