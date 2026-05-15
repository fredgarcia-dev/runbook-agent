.PHONY: up down logs ps clean open-grafana open-prometheus load health \
        k8s-build k8s-deploy k8s-status k8s-logs k8s-delete help

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
	@echo "  make k8s-build       Build the agent Docker image for Kubernetes"
	@echo "  make k8s-deploy      Deploy agent to local Kubernetes cluster"
	@echo "  make k8s-status      Show pod and service status"
	@echo "  make k8s-logs        Tail logs from the agent pod"
	@echo "  make k8s-delete      Remove agent from Kubernetes"

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

k8s-build:
	docker build -t runbook-agent:latest .
	docker tag runbook-agent:latest localhost:5001/runbook-agent:latest
	docker push localhost:5001/runbook-agent:latest
	@echo "Image built and pushed to local registry"

k8s-deploy:
	kubectl apply -f k8s/deployment.yaml
	kubectl apply -f k8s/service.yaml
	@echo ""
	@echo "Deployed — metrics will be available at http://localhost:30800/metrics"
	@echo "Run 'make k8s-status' to check pod readiness"

k8s-status:
	kubectl get pods -l app=runbook-agent
	@echo ""
	kubectl get service runbook-agent

k8s-logs:
	kubectl logs -l app=runbook-agent --follow

k8s-delete:
	kubectl delete -f k8s/deployment.yaml --ignore-not-found
	kubectl delete -f k8s/service.yaml --ignore-not-found
