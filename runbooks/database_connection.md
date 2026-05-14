# Database Connection Issues

## Overview

This runbook covers database connection pool exhaustion, connection timeouts, authentication
failures, replica lag, and general connectivity problems for PostgreSQL, MySQL, and Redis.

## Symptoms

- Alert: `DB connection pool exhausted` or `too many clients`
- Application errors: `connection refused`, `connection timed out`, `FATAL: remaining connection slots reserved`
- Query latency P99 spikes while error rate rises
- Health checks failing with database ping timeout
- Replica lag alert: replica is > N seconds behind primary
- `pg_stat_activity` / `SHOW PROCESSLIST` shows hundreds of idle connections

## Common Causes

| Cause | Signal |
|---|---|
| Connection pool too small for load | Pool wait time in app metrics climbing |
| Connection leak (not returned to pool) | Pool size grows; idle connections stay open |
| Long-running queries blocking others | `pg_stat_activity` shows `waiting` rows |
| Max connections limit reached | `FATAL: remaining connection slots reserved` |
| Database host OOM / crash | Connection refused from all clients |
| Replica falling behind | Replication lag metric, `pg_stat_replication` lag |
| Firewall / VPC rule change | Sudden connection refused after a deploy |
| Certificate rotation | TLS handshake errors in DB logs |

## Diagnosis

### 1. Check connectivity

```bash
# PostgreSQL
psql -h <db-host> -U <user> -c "SELECT 1"

# MySQL
mysql -h <db-host> -u <user> -p -e "SELECT 1"

# Redis
redis-cli -h <redis-host> -p 6379 ping
```

### 2. Current connection count and pool state

```sql
-- PostgreSQL: connections by state and application
SELECT state, count(*), application_name
FROM pg_stat_activity
GROUP BY state, application_name
ORDER BY count DESC;

-- PostgreSQL: connection limit
SHOW max_connections;
SELECT count(*) FROM pg_stat_activity;

-- MySQL
SHOW STATUS LIKE 'Threads_connected';
SHOW VARIABLES LIKE 'max_connections';
```

### 3. Long-running / blocking queries

```sql
-- PostgreSQL: queries running > 30 seconds
SELECT pid, now() - query_start AS duration, state, query
FROM pg_stat_activity
WHERE state != 'idle'
  AND now() - query_start > interval '30 seconds'
ORDER BY duration DESC;

-- MySQL
SHOW FULL PROCESSLIST;
```

### 4. Replica lag

```sql
-- PostgreSQL: on replica
SELECT now() - pg_last_xact_replay_timestamp() AS lag;

-- MySQL: on replica
SHOW REPLICA STATUS\G
-- Look at Seconds_Behind_Source
```

### 5. Application pool metrics

```bash
# Check pool metrics in app logs or Prometheus
# Common pool libraries: PgBouncer, HikariCP, SQLAlchemy
# PgBouncer:
psql -h localhost -p 6432 pgbouncer -c "SHOW POOLS;"
psql -h localhost -p 6432 pgbouncer -c "SHOW CLIENTS;"
```

## Remediation

### Phase 1 — Immediate relief

```sql
-- PostgreSQL: terminate idle connections older than 10 minutes (safe)
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state = 'idle'
  AND state_change < NOW() - INTERVAL '10 minutes'
  AND datname = '<your-db>';

-- Kill a specific long-running query by PID
SELECT pg_cancel_backend(<pid>);    -- graceful
SELECT pg_terminate_backend(<pid>); -- forceful
```

```bash
# MySQL: kill a blocking query
# First identify the ID from SHOW FULL PROCESSLIST, then:
mysql -e "KILL <process_id>;"
```

### Phase 2 — Increase connection headroom

```bash
# PgBouncer — increase pool_size without restarting
psql -h localhost -p 6432 pgbouncer -c "RELOAD;"
# Edit /etc/pgbouncer/pgbouncer.ini: increase pool_size, then reload

# PostgreSQL — raise max_connections (requires restart)
# Edit /etc/postgresql/<version>/main/postgresql.conf:
#   max_connections = 200
# Then:
pg_ctlcluster <version> main reload   # for most parameters
# Or full restart if max_connections change requires it:
systemctl restart postgresql
```

### Phase 3 — Address connection leak

```bash
# 1. Restart the leaking application instance
systemctl restart <app-service>
# OR Kubernetes:
kubectl rollout restart deployment/<name> -n <namespace>

# 2. If using SQLAlchemy — verify pool_recycle and pool_pre_ping are set
# In app config: create_engine(..., pool_recycle=3600, pool_pre_ping=True)

# 3. Deploy PgBouncer in transaction mode to act as connection multiplexer
# This allows hundreds of app connections to share a small DB pool
```

### Phase 4 — Replica lag

```bash
# Check replication slot bloat (can cause WAL accumulation and disk fill)
psql -c "SELECT slot_name, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained_wal FROM pg_replication_slots;"

# If replica is too far behind, consider promoting a different replica
# or re-syncing from a base backup:
pg_basebackup -h <primary> -U replication -D /var/lib/postgresql/data --wal-method=stream -P
```

### Verify

```sql
-- Connection count back to normal?
SELECT count(*) FROM pg_stat_activity;

-- No long-running queries?
SELECT count(*) FROM pg_stat_activity
WHERE state != 'idle' AND now() - query_start > interval '30 seconds';
```

```bash
# Application health check
curl -f http://localhost:<port>/health
```

## Escalation Criteria

Escalate to SEV1 if:
- Database is refusing all connections (max_connections exhausted, host down)
- Replica lag > RPO (risk of data loss if primary fails)
- Evidence of data corruption (`pg_dump` or `mysqlcheck` returning errors)
- Disk full on database host (see disk_space runbook)

## Prevention

- Deploy PgBouncer or ProxySQL as a connection pooler in front of every database
- Set `pool_pre_ping=True` and `pool_recycle=3600` in every ORM connection pool
- Alert on connection count > 80 % of max_connections
- Set `statement_timeout = 30s` and `idle_in_transaction_session_timeout = 60s` in PostgreSQL
- Monitor replica lag; alert at 30 s, page at 5 min
- Test connection failover quarterly using chaos engineering
