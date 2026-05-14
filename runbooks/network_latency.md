# High Network Latency

## Overview

This runbook covers elevated network latency, packet loss, and throughput degradation between
services, to external APIs, or across datacentre / cloud regions.

## Symptoms

- Alert: P99 inter-service latency > 500 ms (or > baseline × 3)
- `ping` or `traceroute` showing elevated RTT or packet loss
- HTTP request timeouts between services that were previously fast
- Database query times elevated even for simple indexed lookups
- TCP retransmit counters climbing (`netstat -s | grep retransmit`)
- Throughput (MB/s) to object storage or message bus significantly below baseline

## Common Causes

| Cause | Typical scenario |
|---|---|
| Network congestion / bandwidth saturation | Inter-DC traffic spike, backup jobs |
| MTU mismatch / fragmentation | VPN or overlay network misconfiguration |
| DNS resolution latency | Stale resolver cache, missing DNS caching sidecar |
| NIC offload or buffer issues | NIC ring buffer overflow, interrupt coalescing |
| Cloud provider incident | Region-wide or AZ-specific degradation |
| Firewall / security group rule storm | Too many stateful connections |
| Noisy neighbour on shared network | Hypervisor / container host resource contention |

## Diagnosis

### 1. Baseline latency check

```bash
# ICMP round-trip to a known internal and external target
ping -c 20 <internal-service-ip>
ping -c 20 8.8.8.8

# MTR for continuous per-hop analysis
mtr --report --report-cycles 30 <destination>
```

### 2. Identify path and hop with issues

```bash
traceroute -n <destination>
traceroute -n -T -p 443 <destination>   # TCP traceroute (bypasses ICMP filters)
```

### 3. Check local interface stats

```bash
# Errors, drops, and overruns per interface
ip -s link show
netstat -i
ethtool -S <interface> | grep -E "(drop|error|miss|over)"

# TCP retransmits and connection state
netstat -s | grep -E "(retransmit|fail|error)"
ss -s
```

### 4. Bandwidth saturation

```bash
# Real-time bandwidth per interface
iftop -i <interface> -n
nload <interface>

# Historical bandwidth (if collectd / prometheus / cloudwatch available)
# Query: rate(node_network_receive_bytes_total[5m])
```

### 5. DNS latency

```bash
# Time a DNS lookup
time dig <hostname> @<dns-server>
time dig <hostname> @127.0.0.1   # local cache

# Check resolver config
cat /etc/resolv.conf
```

### 6. Cloud provider status

- AWS: https://health.aws.amazon.com
- GCP: https://status.cloud.google.com
- Azure: https://status.azure.com

## Remediation

### DNS latency

```bash
# 1. Flush local DNS cache
systemd-resolve --flush-caches   # systemd
nscd -i hosts                     # nscd

# 2. Verify / add local caching (dnsmasq example)
apt-get install -y dnsmasq
echo "cache-size=1000" >> /etc/dnsmasq.conf
systemctl restart dnsmasq

# 3. In Kubernetes — check CoreDNS pods
kubectl get pods -n kube-system -l k8s-app=kube-dns
kubectl logs -n kube-system -l k8s-app=kube-dns --tail=50
kubectl rollout restart deployment/coredns -n kube-system
```

### MTU mismatch (common with VPNs / overlay networks)

```bash
# 2. Detect optimal MTU (PMTUD probe)
ping -M do -s 1472 <destination>    # 1472 + 28 byte ICMP header = 1500
ping -M do -s 1400 <destination>    # try smaller if above fails

# 3. Set MTU on interface
ip link set <interface> mtu 1400
# Persist in /etc/netplan/... or /etc/network/interfaces
```

### NIC ring buffer exhaustion

```bash
# 4. Increase NIC ring buffer size
ethtool -g <interface>
ethtool -G <interface> rx 4096 tx 4096

# 5. Set IRQ affinity to spread NIC interrupts across cores
irqbalance
```

### Traffic shaping / rate limit relief

```bash
# 6. Identify and deprioritise bandwidth-heavy background jobs
tc qdisc show dev <interface>
# Reschedule large backup / replication jobs to off-peak hours
crontab -e   # move backup job to 02:00–04:00
```

### Kubernetes — Service mesh / CNI issues

```bash
# 7. Check for network policy blocking
kubectl get networkpolicy -n <namespace>

# 8. Restart CNI plugin pods (example: Calico)
kubectl rollout restart daemonset/calico-node -n kube-system

# 9. Check service mesh control plane (Istio example)
istioctl proxy-status
kubectl rollout restart deployment/istiod -n istio-system
```

### Verify

```bash
ping -c 10 <destination>          # RTT should return to baseline
mtr --report --report-cycles 10 <destination>
netstat -s | grep retransmit      # count should stop climbing
```

## Escalation Criteria

Escalate to SEV1 if:
- Packet loss > 5 % on the primary network path between datacentres
- Cloud provider incident confirmed affecting the production region
- Latency prevents authentication or payment processing

## Prevention

- Set service-level network latency SLOs and alert at 2× baseline
- Deploy DNS caching sidecars (dnsmasq / CoreDNS) next to latency-sensitive services
- Run `mtr` baselines monthly and store for comparison
- Enable TCP BBR congestion control: `sysctl -w net.ipv4.tcp_congestion_control=bbr`
- Test cross-AZ latency in staging before launching distributed features
