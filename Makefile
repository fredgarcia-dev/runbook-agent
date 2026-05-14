.PHONY: up down logs ps clean open-grafana open-prometheus load health help

COMPOSE = docker compose
PYTHON  = venv/bin/python

help:
	@echo "Runbook Agent — Observability Stack"
	@echo ""
	@echo "  make up              Start Prometheus, Grafana, and Node Exporter"
	@echo "  make down            Stop all containers"
	@echo "  make logs            Tail logs from all containers"
	@echo "  make ps              Show running containers and ports"
	@echo "  make clean           Remove containers and all persistent volumes"
	@echo "  make open-grafana    Open Grafana in the browser  (http://localhost:3000)"
	@echo "  make open-prometheus Open Prometheus in the browser  (http://localhost:9090)"
	@echo "  make load            Run synthetic load generator (populates Grafana)"
	@echo "  make health          Run stack health check validator"

up:
	$(COMPOSE) up -d
	@echo ""
	@echo "  Grafana    → http://localhost:3000  (admin / admin)"
	@echo "  Prometheus → http://localhost:9090"
	@echo "  Node Exp.  → http://localhost:9100/metrics"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

clean:
	$(COMPOSE) down -v --remove-orphans

open-grafana:
	open http://localhost:3000

open-prometheus:
	open http://localhost:9090

load:
	$(PYTHON) scripts/load_demo.py

health:
	$(PYTHON) scripts/health_check.py --verbose
