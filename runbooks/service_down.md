# Service Down / Unavailable

## Overview

This runbook covers a service, process, or container that is completely unavailable, returning
HTTP 5xx / connection refused, or crashing in a restart loop.  Applies to systemd services,
Docker containers, and Kubernetes deployments.

## Symptoms

- Health-check alert: `Service unreachable` or `HTTP 503 / 502`
- `systemctl status <service>` shows `failed` or `activating` loop
- `kubectl get pods` shows `CrashLoopBackOff`, `Error`, or `OOMKilled`
- Uptime monitor reporting 100 % error rate
- Downstream services returning errors due to dependency unavailability
- Load balancer removing all backends from pool (0 healthy)

## Common Causes

| Cause | Signal |
|---|---|
| Out-of-memory kill | `dmesg` OOM, `kubectl describe pod` LastState OOMKilled |
| Configuration / env var error | Non-zero exit code immediately on start |
| Dependency unavailable (DB, cache, message bus) | Connection refused in startup logs |
| Port conflict | `Address already in use` in logs |
| Bad deploy / broken container image | Image pull error or immediate segfault |
| Certificate expired | TLS handshake errors in logs |
| Resource limits too tight | `CrashLoopBackOff` with OOMKilled or throttle signals |

## Diagnosis

### 1. Check current service state

```bash
# systemd
systemctl status <service-name>
journalctl -u <service-name> -n 100 --no-pager

# Docker
docker ps -a | grep <name>
docker logs --tail 100 <container>
docker inspect <container> | jq '.[0].State'

# Kubernetes
kubectl get pods -n <namespace>
kubectl describe pod <pod-name> -n <namespace>
kubectl logs <pod-name> -n <namespace> --previous   # logs from crashed instance
kubectl get events -n <namespace> --sort-by='.lastTimestamp' | tail -20
```

### 2. Check for port conflicts

```bash
ss -tlnp | grep :<port>
lsof -i :<port>
```

### 3. Check dependencies

```bash
# Can the service reach its database?
nc -zv <db-host> <db-port>
redis-cli -h <redis-host> ping
curl -f http://<dependency-service>/health
```

### 4. Check recent deployments

```bash
# Was there a recent deploy?
git log --oneline -10
kubectl rollout history deployment/<name> -n <namespace>
docker image inspect <image>:<tag> | jq '.[0].Created'
```

### 5. Check resource limits

```bash
kubectl describe node | grep -A5 "Allocated resources"
kubectl top pod -n <namespace>
systemctl show <service-name> | grep -E "MemoryMax|CPUQuota"
```

## Remediation

### Option A — Restart the service

```bash
# systemd
systemctl restart <service-name>
sleep 5
systemctl status <service-name>

# Docker
docker restart <container>
docker ps | grep <name>

# Kubernetes — rolling restart (no downtime if replicas > 1)
kubectl rollout restart deployment/<name> -n <namespace>
kubectl rollout status deployment/<name> -n <namespace>
```

### Option B — Roll back a bad deploy

```bash
# Kubernetes
kubectl rollout undo deployment/<name> -n <namespace>
kubectl rollout status deployment/<name> -n <namespace>

# Docker Compose
docker-compose pull <previous-image-tag>
docker-compose up -d

# systemd + git
git revert HEAD
systemctl restart <service-name>
```

### Option C — Fix a configuration error

```bash
# Kubernetes ConfigMap / Secret
kubectl edit configmap <name> -n <namespace>
kubectl rollout restart deployment/<name> -n <namespace>

# systemd environment file
nano /etc/systemd/system/<service>.d/override.conf
systemctl daemon-reload && systemctl restart <service-name>
```

### Option D — Free a port conflict

```bash
fuser -k <port>/tcp    # kill the process holding the port
systemctl restart <service-name>
```

### Verify

```bash
# Health check
curl -f http://localhost:<port>/health

# Watch for stable state
kubectl get pods -n <namespace> -w     # should reach Running / Ready
journalctl -u <service-name> -f        # watch for errors
```

## Escalation Criteria

Escalate to SEV1 if:
- Service cannot be restarted and impacts all users (auth, payment, core API)
- Rollback fails or previous image is also broken
- Data corruption suspected (database in read-only mode, fsck needed)

## Prevention

- Define liveness and readiness probes for every Kubernetes service
- Set restart policies: `RestartAlways` (containers) or `Restart=on-failure` (systemd)
- Test rollback procedure in staging before every release
- Monitor process restart count; alert on > 2 restarts in 10 minutes
- Store image tags in version control; never deploy `latest` in production
- Run dependency health checks in startup scripts before accepting traffic
