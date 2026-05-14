# Disk Space Critical / Warning

## Overview

This runbook covers disk space exhaustion and warnings on Linux servers.  Applies to the root
partition `/`, application data volumes, log partitions, and ephemeral storage mounts.

## Symptoms

- Alert: `Disk usage > 80 %` (warning) or `> 90 %` (critical)
- Application errors: `No space left on device (ENOSPC)`
- Log writes failing silently; log files not growing when expected
- Database refusing writes or failing to create temporary files
- Container volume mounts reporting `no space left` errors
- Inode exhaustion: `df -i` shows 100 % inode usage even with byte space remaining

## Common Causes

| Cause | Typical location |
|---|---|
| Log accumulation without rotation | `/var/log/`, app-specific log dirs |
| Core dumps from crashing processes | `/var/crash/`, `/tmp/`, working dir |
| Docker image / layer / volume bloat | `/var/lib/docker/` |
| Database WAL / redo log growth | `/var/lib/postgresql/`, `/var/lib/mysql/` |
| Large uploaded or pipeline output files | `/app/uploads/`, `/data/` |
| Deleted-but-open file descriptors | Anywhere (space not freed until FD closed) |

## Diagnosis

### 1. Identify the full partition

```bash
df -h
df -i    # inode usage — may be full even when bytes are available
```

### 2. Find the top space consumers

```bash
# Top directories
du -sh /* 2>/dev/null | sort -rh | head -20
du -sh /var/log/* 2>/dev/null | sort -rh | head -10

# Files larger than 500 MB anywhere on this device
find / -xdev -size +500M -printf '%s\t%p\n' 2>/dev/null | sort -rn | head -20
```

### 3. Detect deleted-but-open file descriptors (space held hostage)

```bash
# Shows files deleted but still open by a process (common with logs)
lsof +L1 2>/dev/null | awk 'NR==1 || /deleted/' | sort -k 7 -rn | head -15
```

### 4. Docker footprint

```bash
docker system df
docker image ls --format "{{.Repository}}\t{{.Tag}}\t{{.Size}}" | sort -k3 -rh
```

### 5. Core dumps

```bash
ls -lh /var/crash/ /var/core/ /tmp/*.core 2>/dev/null
coredumpctl list 2>/dev/null | head -20
```

## Remediation

### Phase 1 — Immediate stabilisation (safe, low risk)

```bash
# 1. Vacuum systemd journal to 500 MB
journalctl --vacuum-size=500M

# 2. Remove compressed / rotated logs older than 3 days
find /var/log -name "*.gz" -mtime +3 -delete
find /var/log -name "*.log.[0-9]*" -mtime +3 -delete

# 3. Clear package manager caches
apt-get clean 2>/dev/null || yum clean all 2>/dev/null || dnf clean all 2>/dev/null

# 4. Remove core dumps
find /var/crash /var/core /tmp -maxdepth 2 -name "*.core" -o -name "core" -delete 2>/dev/null

# 5. Docker safe cleanup (does NOT remove running containers or used images)
docker system prune -f
docker volume prune -f
```

### Phase 2 — Restart processes holding deleted FDs

If `lsof +L1` shows large deleted files still open:

```bash
# Get the PID and restart the service
systemctl restart <service-name>
# Verify space freed
df -h
```

### Phase 3 — Application data cleanup

```bash
# Force immediate log rotation
logrotate -f /etc/logrotate.conf

# Remove application artefacts older than 30 days (adjust path)
find /app/uploads -mtime +30 -type f -delete
find /data/exports -mtime +14 -name "*.csv" -delete
```

### Phase 4 — Verify

```bash
df -h
df -i
```

## Escalation Criteria

Escalate to SEV1 if:
- Disk at **> 95 %** and writes are **actively failing** (ENOSPC in app logs)
- Database cannot write WAL / redo logs → potential data loss
- All safe cleanup methods exhausted; root cause still unknown

## Prevention

- `logrotate` with `maxsize 200M daily` for every application log
- Disk alerts at 70 %, 80 %, 90 % plus inode alerts
- Weekly `docker system prune -f` via cron
- Mount `/var/log`, `/tmp`, and database WAL on separate partitions
- Set `ulimit -c 0` for non-debug production services (no core dumps)
