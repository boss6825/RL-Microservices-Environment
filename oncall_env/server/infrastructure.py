# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""
Simulated microservices infrastructure for OnCallOps.

Pure-Python simulation of a microservices stack with health states,
log generation, metrics, configs, and deployment history.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


@dataclass
class Deployment:
    version: str
    timestamp: str
    changes: str
    rolled_back: bool = False


@dataclass
class ServiceState:
    name: str
    health: str = "healthy"  # healthy | degraded | down
    cpu_pct: float = 15.0
    memory_pct: float = 30.0
    error_rate_pct: float = 0.1
    latency_p99_ms: float = 50.0
    active_connections: int = 20
    max_connections: int = 200
    replicas: int = 2
    config: Dict[str, Any] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)
    deployments: List[Deployment] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)


DEPENDENCY_MAP: Dict[str, List[str]] = {
    "api-gateway": ["auth-service", "user-service", "order-service"],
    "auth-service": ["database", "cache"],
    "user-service": ["database", "cache"],
    "order-service": ["database", "payment-service", "message-queue"],
    "payment-service": ["database"],
    "database": [],
    "cache": [],
    "message-queue": [],
}

DEFAULT_CONFIGS: Dict[str, Dict[str, Any]] = {
    "api-gateway": {"rate_limit": 1000, "timeout_ms": 5000, "retry_count": 3},
    "auth-service": {"token_ttl_seconds": 3600, "max_sessions": 10000, "bcrypt_rounds": 12},
    "user-service": {"page_size": 50, "cache_ttl_seconds": 300, "max_batch_size": 100},
    "order-service": {"pool_size": 50, "checkout_timeout_ms": 30000, "max_retries": 3},
    "payment-service": {"gateway_timeout_ms": 10000, "max_amount": 50000},
    "database": {"max_connections": 200, "query_timeout_ms": 5000, "slow_query_threshold_ms": 1000},
    "cache": {"max_memory_mb": 512, "eviction_policy": "lru", "ttl_default_seconds": 600},
    "message-queue": {"max_queue_depth": 10000, "consumer_timeout_ms": 30000, "retention_hours": 72},
}


_NORMAL_LOG_TEMPLATES = [
    "[INFO] Request processed successfully in {latency}ms",
    "[INFO] Health check passed",
    "[INFO] Connection pool: {conns}/{max_conns} active",
    "[DEBUG] Cache hit ratio: {cache_hit}%",
    "[INFO] Processed {count} requests in last minute",
]

_ERROR_LOG_TEMPLATES = {
    "oom": [
        "[ERROR] OutOfMemoryError: Java heap space",
        "[FATAL] Container killed by OOM killer (exit code 137)",
        "[ERROR] Memory usage at {mem}% - exceeding threshold",
        "[WARN] GC overhead limit exceeded, pausing for {pause}ms",
        "[ERROR] Failed to allocate {size}MB - insufficient memory",
    ],
    "connection_pool": [
        "[ERROR] Connection pool exhausted: {conns}/{max_conns}",
        "[ERROR] Timed out waiting for connection from pool after {timeout}ms",
        "[WARN] Connection leak detected - {leaked} connections not returned",
        "[ERROR] Cannot acquire connection: pool at capacity",
        "[ERROR] Database connection refused - max connections reached",
    ],
    "timeout": [
        "[ERROR] Request timeout after {timeout}ms to {target}",
        "[WARN] Upstream {target} responded with 504 Gateway Timeout",
        "[ERROR] Circuit breaker OPEN for {target} - too many failures",
        "[ERROR] Connection to {target} timed out",
    ],
    "auth_storm": [
        "[WARN] Token validation failed: token expired",
        "[ERROR] Auth token TTL unexpectedly short ({ttl}s)",
        "[WARN] Abnormal token refresh rate: {rate}/min (normal: ~{normal}/min)",
        "[ERROR] Session cache miss storm detected - {misses} misses/sec",
        "[WARN] Auth service latency spike: {latency}ms (p99)",
    ],
    "config_drift": [
        "[WARN] Config value for '{key}' differs from expected: got {actual}, expected {expected}",
        "[INFO] Configuration reloaded from deployment v{version}",
    ],
    "generic_error": [
        "[ERROR] Internal server error: status 500",
        "[ERROR] Unhandled exception in request handler",
        "[WARN] High error rate detected: {rate}% (threshold: 5%)",
    ],
}


class Infrastructure:
    """Simulates a microservices infrastructure."""

    def __init__(self, rng: random.Random):
        self._rng = rng
        self.services: Dict[str, ServiceState] = {}
        self.alerts: List[Dict[str, str]] = []
        self.incident_start: str = ""
        self._build_baseline()

    @property
    def service_names(self) -> List[str]:
        return list(self.services.keys())

    def _build_baseline(self) -> None:
        base_time = datetime(2026, 4, 7, 3, 0, 0)
        self.incident_start = (base_time + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

        for name, deps in DEPENDENCY_MAP.items():
            svc = ServiceState(
                name=name,
                config=dict(DEFAULT_CONFIGS.get(name, {})),
                dependencies=list(deps),
            )
            svc.cpu_pct = round(self._rng.uniform(8, 25), 1)
            svc.memory_pct = round(self._rng.uniform(20, 45), 1)
            svc.error_rate_pct = round(self._rng.uniform(0.0, 0.5), 2)
            svc.latency_p99_ms = round(self._rng.uniform(20, 80), 1)
            svc.active_connections = self._rng.randint(10, 40)

            stable_version = f"1.{self._rng.randint(2, 9)}.{self._rng.randint(0, 5)}"
            svc.deployments.append(Deployment(
                version=stable_version,
                timestamp=(base_time - timedelta(days=self._rng.randint(2, 14))).strftime("%Y-%m-%d %H:%M:%S"),
                changes="Routine maintenance release",
            ))

            for _ in range(self._rng.randint(3, 6)):
                tpl = self._rng.choice(_NORMAL_LOG_TEMPLATES)
                log_line = self._fmt_log(tpl, base_time, svc)
                svc.logs.append(log_line)

            self.services[name] = svc

    def _fmt_log(self, tpl: str, ts: datetime, svc: ServiceState) -> str:
        offset = timedelta(seconds=self._rng.randint(0, 7200))
        stamp = (ts + offset).strftime("%Y-%m-%d %H:%M:%S")
        line = tpl.format(
            latency=self._rng.randint(10, 200),
            conns=svc.active_connections,
            max_conns=svc.max_connections,
            cache_hit=self._rng.randint(85, 99),
            count=self._rng.randint(50, 500),
        )
        return f"{stamp} {svc.name} {line}"

    # ---- tool implementations ------------------------------------------------

    def get_alerts(self) -> str:
        if not self.alerts:
            return "No active alerts."
        lines = []
        for a in self.alerts:
            lines.append(
                f"[{a['severity'].upper()}] {a['service']}: {a['summary']}  "
                f"(fired at {a['timestamp']})"
            )
        return "\n".join(lines)

    def get_logs(self, service: str, keyword: Optional[str] = None) -> str:
        svc = self.services.get(service)
        if svc is None:
            return f"Unknown service: {service}"
        logs = svc.logs
        if keyword:
            logs = [l for l in logs if keyword.lower() in l.lower()]
        if not logs:
            return f"No log entries found for {service}" + (f" matching '{keyword}'" if keyword else "")
        return "\n".join(logs[-20:])

    def get_metrics(self, service: str, metric: str = "") -> str:
        svc = self.services.get(service)
        if svc is None:
            return f"Unknown service: {service}"
        if metric:
            metric_map = {
                "cpu": f"cpu_usage: {svc.cpu_pct}%",
                "memory": f"memory_usage: {svc.memory_pct}%",
                "error_rate": f"error_rate: {svc.error_rate_pct}%",
                "latency": f"latency_p99: {svc.latency_p99_ms}ms",
                "connections": f"active_connections: {svc.active_connections}/{svc.max_connections}",
            }
            return metric_map.get(metric, f"Unknown metric '{metric}'. Available: cpu, memory, error_rate, latency, connections")
        return (
            f"=== {service} metrics ===\n"
            f"cpu_usage: {svc.cpu_pct}%\n"
            f"memory_usage: {svc.memory_pct}%\n"
            f"error_rate: {svc.error_rate_pct}%\n"
            f"latency_p99: {svc.latency_p99_ms}ms\n"
            f"active_connections: {svc.active_connections}/{svc.max_connections}\n"
            f"replicas: {svc.replicas}"
        )

    def get_status(self, service: str) -> str:
        svc = self.services.get(service)
        if svc is None:
            return f"Unknown service: {service}"
        return (
            f"Service: {service}\n"
            f"Health: {svc.health}\n"
            f"Replicas: {svc.replicas}\n"
            f"Uptime: {'normal' if svc.health == 'healthy' else 'degraded/restarting'}"
        )

    def get_dependencies(self, service: str) -> str:
        svc = self.services.get(service)
        if svc is None:
            return f"Unknown service: {service}"
        upstream = [n for n, deps in DEPENDENCY_MAP.items() if service in deps]
        downstream = svc.dependencies
        lines = [f"=== Dependency map for {service} ==="]
        lines.append(f"Upstream (depends ON {service}): {', '.join(upstream) if upstream else 'none'}")
        lines.append(f"Downstream ({service} depends ON): {', '.join(downstream) if downstream else 'none'}")
        return "\n".join(lines)

    def get_recent_deployments(self) -> str:
        all_deps = []
        for svc in self.services.values():
            for d in svc.deployments:
                all_deps.append((d.timestamp, svc.name, d))
        all_deps.sort(key=lambda x: x[0], reverse=True)
        lines = ["=== Recent deployments ==="]
        for ts, name, d in all_deps[:10]:
            rb = " [ROLLED BACK]" if d.rolled_back else ""
            lines.append(f"{ts}  {name} v{d.version}: {d.changes}{rb}")
        return "\n".join(lines)

    def get_config(self, service: str) -> str:
        svc = self.services.get(service)
        if svc is None:
            return f"Unknown service: {service}"
        lines = [f"=== {service} config ==="]
        for k, v in svc.config.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    # ---- remediation actions ------------------------------------------------

    def restart_service(self, service: str) -> str:
        svc = self.services.get(service)
        if svc is None:
            return f"Unknown service: {service}"
        old_health = svc.health
        svc.health = "healthy"
        svc.error_rate_pct = round(self._rng.uniform(0.0, 0.5), 2)
        svc.latency_p99_ms = round(self._rng.uniform(20, 80), 1)
        svc.cpu_pct = round(self._rng.uniform(8, 25), 1)
        svc.memory_pct = round(self._rng.uniform(20, 45), 1)
        svc.active_connections = self._rng.randint(10, 40)
        return f"Service {service} restarted. Health: {old_health} -> healthy"

    def rollback_deployment(self, service: str) -> str:
        svc = self.services.get(service)
        if svc is None:
            return f"Unknown service: {service}"
        if len(svc.deployments) < 2:
            return f"No previous deployment to rollback to for {service}"
        current = svc.deployments[-1]
        current.rolled_back = True
        previous = svc.deployments[-2]
        return (
            f"Rolled back {service} from v{current.version} to v{previous.version}. "
            f"Restarting with previous configuration..."
        )

    def scale_service(self, service: str, replicas: int) -> str:
        svc = self.services.get(service)
        if svc is None:
            return f"Unknown service: {service}"
        old = svc.replicas
        svc.replicas = max(1, min(replicas, 10))
        return f"Scaled {service} from {old} to {svc.replicas} replicas."

    def update_config(self, service: str, key: str, value: Any) -> str:
        svc = self.services.get(service)
        if svc is None:
            return f"Unknown service: {service}"
        if key not in svc.config:
            return f"Unknown config key '{key}' for {service}. Available: {', '.join(svc.config.keys())}"
        old = svc.config[key]
        svc.config[key] = value
        return f"Updated {service} config: {key} = {value} (was {old}). Service reloading..."

    # ---- helpers for scenario injection -------------------------------------

    def add_alert(self, severity: str, service: str, summary: str, ts: str) -> None:
        self.alerts.append({
            "severity": severity,
            "service": service,
            "summary": summary,
            "timestamp": ts,
        })

    def inject_error_logs(self, service: str, category: str, count: int = 5, **kwargs: Any) -> None:
        svc = self.services.get(service)
        if svc is None:
            return
        templates = _ERROR_LOG_TEMPLATES.get(category, _ERROR_LOG_TEMPLATES["generic_error"])
        base_ts = datetime(2026, 4, 7, 5, 0, 0)
        for i in range(count):
            tpl = self._rng.choice(templates)
            ts = (base_ts + timedelta(seconds=i * 30 + self._rng.randint(0, 15))).strftime("%Y-%m-%d %H:%M:%S")
            line = tpl.format(
                mem=kwargs.get("mem", self._rng.randint(92, 99)),
                pause=self._rng.randint(500, 3000),
                size=self._rng.randint(128, 1024),
                conns=kwargs.get("conns", self._rng.randint(190, 200)),
                max_conns=kwargs.get("max_conns", 200),
                timeout=self._rng.randint(5000, 30000),
                target=kwargs.get("target", "unknown"),
                leaked=self._rng.randint(5, 30),
                ttl=kwargs.get("ttl", 1),
                rate=kwargs.get("rate", self._rng.randint(500, 2000)),
                normal=kwargs.get("normal", self._rng.randint(5, 20)),
                misses=self._rng.randint(100, 1000),
                latency=self._rng.randint(500, 5000),
                key=kwargs.get("key", "unknown"),
                actual=kwargs.get("actual", "unknown"),
                expected=kwargs.get("expected", "unknown"),
                version=kwargs.get("version", "unknown"),
            )
            svc.logs.append(f"{ts} {service} {line}")

    def add_deployment(self, service: str, version: str, changes: str, ts: str) -> None:
        svc = self.services.get(service)
        if svc is None:
            return
        svc.deployments.append(Deployment(version=version, timestamp=ts, changes=changes))
