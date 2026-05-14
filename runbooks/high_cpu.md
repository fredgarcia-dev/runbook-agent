# High CPU Utilisation

## Overview

This runbook covers sustained high CPU load on Linux servers and Kubernetes nodes, including
single-process spikes, runaway threads, and system-wide saturation.

## Symptoms

- Alert: `CPU utilisation > 80 %` for more than 5 minutes
- Load average significantly above CPU core count (`uptime` / `top`)
- Application response times increasing; request queue growing
- SSH connections slow or timing out
- `OOMKiller` activity unrelated to memory (CPU starvation triggering cascades)

## Common Causes

| Cause | Indicator |
|---|---|
| Runaway process or thread | Single PID at near 100 % in `top` |
| Unoptimised query / N+1 loops | Database CPU spikes correlate with app load |
| Crypto mining / malware | Unexpected process name, unusual parent PID |
| GC storm (JVM / Node / Python) | Long GC pause logs, old-gen heap near limit |
| Kernel overhead (softirq, ksoftirqd) | High `%si` in `top`, NIC interrupt affinity issues |
| Traffic spike without auto-scaling | Even distribution across all cores |

## Diagnosis

### 1. Identify top CPU consumers

```bash
# Live top-10 by CPU
ps aux --sort=-%cpu | head -12

# Continuous view (press 1 to see per-core breakdown)
top -d 2

# Extended process info including threads
htop
```

### 2. Drill into the offending process

```bash
PID=<suspect_pid>

# Thread-level CPU breakdown
top -Hp $PID

# System calls (what is it doing?)
strace -p $PID -c -f -e trace=all 2>&1 | head -40

# Open files / network connections
lsof -p $PID | head -30
```

### 3. Check load average vs core count

```bash
nproc              # total logical CPUs
uptime             # load averages (1, 5, 15 min)
# Rule of thumb: load avg / nproc > 1.0 = CPU saturated
```

### 4. Kernel CPU breakdown

```bash
# Check %us (user) vs %sy (system) vs %si (softirq)
mpstat -P ALL 2 5

# Check interrupt distribution
cat /proc/interrupts | sort -k2 -rn | head -20
```

### 5. Check for known malware / coin miners

```bash
ps aux | grep -E "(xmrig|minerd|cryptonight)" 
netstat -tnp | grep -E "(ESTABLISHED|SYN)" | awk '{print $5}' | cut -d: -f1 | sort | uniq -c | sort -rn
```

## Remediation

### Immediate — Reduce load

```bash
# 1. Renice the offending process (reduce priority, does NOT kill it)
renice +15 -p $PID

# 2. If confirmed runaway / stuck — graceful kill
kill -TERM $PID
sleep 5
kill -9 $PID   # only if TERM did not work

# 3. If traffic spike — shed load via rate limiting (nginx example)
# Edit /etc/nginx/nginx.conf → add limit_req_zone + limit_req, then:
nginx -t && systemctl reload nginx
```

### Kubernetes — throttle or scale

```bash
# Check current resource limits
kubectl describe pod <pod-name> -n <namespace> | grep -A5 Limits

# Immediately scale up if traffic spike
kubectl scale deployment <name> --replicas=<N> -n <namespace>

# CPU limit that is too low can cause throttling — check:
kubectl top pod -n <namespace>
```

### JVM / Node.js — GC investigation

```bash
# JVM: force GC and take a heap dump
jcmd $PID GC.run
jcmd $PID VM.flags | grep -i heap
jmap -histo:live $PID | head -30

# Node.js: check event loop lag
kill -USR1 $PID   # produces CPU profile on some runtimes
```

### Verify

```bash
top -d 2        # CPU should be returning to baseline
uptime          # watch load average decline over next few minutes
```

## Escalation Criteria

Escalate to SEV1 if:
- All application processes killed yet CPU remains > 95 % (kernel / hardware issue)
- Evidence of active intrusion (unexpected process with external network connection)
- Service completely unresponsive and cannot be restarted

Escalate to SEV2 if:
- CPU > 80 % for > 15 minutes with user-facing latency impact
- Scaling / restart attempts have not restored normal utilisation

## Prevention

- Set `resources.requests` and `resources.limits.cpu` for every Kubernetes pod
- Configure horizontal pod autoscaling (HPA) on CPU utilisation
- Alert on load average > 0.8 × CPU count for 5 minutes
- Profile top endpoints monthly; set query timeouts and connection limits in database
- Run `rkhunter` or `aide` for intrusion detection; review `auditd` logs weekly
