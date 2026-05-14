# Memory Leak Detection and Remediation

## Overview

This runbook covers unbounded memory growth in application processes, containers, and virtual
machines.  Includes OOMKiller activations, swap exhaustion, and per-process heap diagnostics.

## Symptoms

- Alert: `Memory usage > 85 %` sustained for more than 10 minutes
- OOMKiller activating: `dmesg | grep -i "out of memory"` shows recent kills
- Process RSS growing without bound over hours / days (no plateau)
- Swap usage climbing even though physical RAM is available
- Container restarting with `OOMKilled` exit status
- Application latency increasing as GC frequency rises to cope with heap pressure

## Common Causes

| Cause | Typical runtime |
|---|---|
| Unbounded in-memory cache / queue | Any |
| Circular references preventing GC | Python, JavaScript |
| JVM old-gen heap filling without release | Java, Scala, Kotlin |
| Connection or file descriptor leak | Any |
| Repeated large allocations not freed | C, C++, Rust unsafe |
| Library bug retaining large buffers | Any |

## Diagnosis

### 1. Confirm memory pressure

```bash
free -h
vmstat -s | grep -E "(total|free|used|swap)"
cat /proc/meminfo | grep -E "(MemTotal|MemFree|MemAvailable|SwapUsed|Cached)"
```

### 2. Identify top memory consumers

```bash
ps aux --sort=-%mem | head -12
# %MEM column = RSS / total RAM
```

### 3. Track memory growth over time for a specific PID

```bash
PID=<suspect_pid>

# Watch RSS every 5 seconds
watch -n 5 "cat /proc/$PID/status | grep -E '(VmRSS|VmSwap|VmSize)'"

# Smaps summary (heap, stack, anon breakdown)
cat /proc/$PID/smaps_rollup
```

### 4. OOMKiller history

```bash
dmesg -T | grep -i "out of memory" | tail -20
journalctl -k | grep -i oom | tail -20
```

### 5. Kubernetes — OOMKilled containers

```bash
kubectl describe pod <pod-name> -n <namespace> | grep -A3 "Last State"
kubectl top pod -n <namespace> --containers
```

### 6. JVM heap analysis

```bash
# Live heap histogram (no heap dump required)
jmap -histo:live $PID | head -40

# Full heap dump for offline analysis with Eclipse MAT / VisualVM
jmap -dump:format=b,live,file=/tmp/heap.hprof $PID
```

### 7. Python memory profiling

```bash
# Attach to running process (requires py-spy installed)
py-spy dump --pid $PID
py-spy record -o /tmp/profile.svg --pid $PID --duration 30
```

## Remediation

### Immediate — Restore service stability

```bash
# 1. Gracefully restart the leaking process / service
systemctl restart <service-name>

# Kubernetes: rolling restart (zero downtime)
kubectl rollout restart deployment/<name> -n <namespace>

# Docker: restart the container
docker restart <container-name-or-id>
```

### Short-term — Add memory guard-rails

```bash
# 2. Set a hard memory limit to prevent OOM affecting other processes
# systemd:
# In /etc/systemd/system/<service>.d/override.conf:
#   [Service]
#   MemoryMax=2G

systemctl edit <service-name>
# Add [Service] / MemoryMax=2G, then:
systemctl daemon-reload && systemctl restart <service-name>

# 3. Kubernetes — add/lower memory limit to trigger OOMKill early rather than
#    dragging down the entire node:
kubectl patch deployment <name> -n <namespace> --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/memory","value":"1Gi"}]'
```

### Medium-term — Clear accumulated state (if restart is not possible)

```bash
# 4. Drop caches (safe — kernel will refill on demand)
sync && echo 3 > /proc/sys/vm/drop_caches

# 5. If process exposes a heap-trim endpoint (e.g. gRPC health / admin):
curl -X POST http://localhost:9090/admin/trim_heap

# 6. Manually free Python object caches if using a WSGI server
# Send SIGUSR1 if the app handles it as a cache-clear signal
kill -USR1 $PID
```

### Verify

```bash
free -h
ps aux --sort=-%mem | head -5
watch -n 10 "cat /proc/$PID/status | grep VmRSS"   # should plateau after restart
```

## Escalation Criteria

Escalate to SEV1 if:
- OOMKiller is killing critical database or auth service processes
- Memory pressure is causing kernel panic (check `dmesg` for `BUG:` or `panic:`)
- Restarting the service does not resolve growth (leak reproduces within minutes)

## Prevention

- Set `MemoryMax` (systemd) or `resources.limits.memory` (K8s) for every service
- Configure OOM alerting at 75 %, 85 %, 95 %
- Enable heap profiling in staging for every JVM service (`-XX:+HeapDumpOnOutOfMemoryError`)
- Add readiness probes that fail before OOM to enable graceful traffic rerouting
- Schedule weekly restarts for services with known gradual leaks while fix is in progress
- Use memory-profiling tools (`valgrind`, `py-spy`, `async-profiler`) in CI on every merge
