# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""
Incident scenario definitions for OnCallOps.

Each scenario injects faults into the Infrastructure, defines the correct
diagnosis and remediation, and provides a grading function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .infrastructure import Infrastructure


@dataclass
class GradingCriteria:
    root_cause_service: str
    root_cause_keywords: List[str]
    valid_remediations: List[Dict[str, Any]]
    partial_credit_services: List[str] = field(default_factory=list)


@dataclass
class Scenario:
    name: str
    task_id: str
    difficulty: str
    description: str
    inject_fn: Callable[["Infrastructure"], None]
    grading: GradingCriteria


def _match_any_keyword(text: str, keywords: List[str]) -> float:
    text_lower = text.lower()
    matched = sum(1 for kw in keywords if kw.lower() in text_lower)
    return min(1.0, matched / max(len(keywords), 1))


# ---------------------------------------------------------------------------
# Scenario 1: Memory Leak (Easy)
# ---------------------------------------------------------------------------

def _inject_memory_leak(infra: Infrastructure) -> None:
    svc = infra.services["user-service"]
    svc.health = "degraded"
    svc.memory_pct = 97.3
    svc.cpu_pct = 88.5
    svc.error_rate_pct = 34.2
    svc.latency_p99_ms = 4500.0

    infra.inject_error_logs("user-service", "oom", count=8, mem=97)

    infra.add_alert("critical", "user-service",
                    "Container OOMKilled: memory usage 97% (threshold: 85%)",
                    "2026-04-07 05:02:00")
    infra.add_alert("warning", "user-service",
                    "High error rate: 34.2% (threshold: 5%)",
                    "2026-04-07 05:01:30")
    infra.add_alert("warning", "api-gateway",
                    "Elevated 5xx responses from upstream user-service",
                    "2026-04-07 05:03:00")

    gw = infra.services["api-gateway"]
    gw.error_rate_pct = 8.5
    gw.latency_p99_ms = 2200.0
    infra.inject_error_logs("api-gateway", "timeout", count=3, target="user-service")


MEMORY_LEAK_SCENARIO = Scenario(
    name="Memory Leak",
    task_id="memory_leak",
    difficulty="easy",
    description=(
        "The user-service is experiencing an out-of-memory condition. "
        "A critical alert has fired indicating the container was OOM-killed. "
        "Diagnose the issue and restore service health."
    ),
    inject_fn=_inject_memory_leak,
    grading=GradingCriteria(
        root_cause_service="user-service",
        root_cause_keywords=["memory", "oom", "out of memory", "leak"],
        valid_remediations=[
            {"tool": "restart_service", "target": "user-service"},
            {"tool": "scale_service", "target": "user-service"},
        ],
        partial_credit_services=["user-service"],
    ),
)


# ---------------------------------------------------------------------------
# Scenario 2: Database Connection Storm (Medium)
# ---------------------------------------------------------------------------

def _inject_db_connection_storm(infra: Infrastructure) -> None:
    db = infra.services["database"]
    db.health = "degraded"
    db.active_connections = 198
    db.max_connections = 200
    db.cpu_pct = 72.0
    db.error_rate_pct = 15.3
    db.latency_p99_ms = 8500.0

    infra.inject_error_logs("database", "connection_pool", count=6,
                            conns=198, max_conns=200)

    order_svc = infra.services["order-service"]
    order_svc.health = "degraded"
    order_svc.error_rate_pct = 42.1
    order_svc.latency_p99_ms = 12000.0
    order_svc.active_connections = 145
    order_svc.config["pool_size"] = 150

    infra.add_deployment("order-service", "2.4.0",
                         "Updated DB connection pool: pool_size 50 -> 150 for performance",
                         "2026-04-07 04:30:00")

    infra.inject_error_logs("order-service", "connection_pool", count=5,
                            conns=145, max_conns=150)
    infra.inject_error_logs("order-service", "timeout", count=3, target="database")

    for svc_name in ["user-service", "payment-service", "auth-service"]:
        svc = infra.services[svc_name]
        svc.error_rate_pct = round(infra._rng.uniform(8, 20), 1)
        svc.latency_p99_ms = round(infra._rng.uniform(3000, 8000), 1)
        infra.inject_error_logs(svc_name, "timeout", count=3, target="database")

    infra.add_alert("critical", "database",
                    "Connection pool near exhaustion: 198/200 connections",
                    "2026-04-07 05:00:00")
    infra.add_alert("critical", "order-service",
                    "High error rate: 42.1% -- upstream database timeouts",
                    "2026-04-07 05:01:00")
    infra.add_alert("warning", "user-service",
                    "Elevated latency: p99 > 3000ms (database connection delays)",
                    "2026-04-07 05:02:00")
    infra.add_alert("warning", "payment-service",
                    "Transaction failures increasing -- database timeout",
                    "2026-04-07 05:02:30")


DB_CONNECTION_STORM_SCENARIO = Scenario(
    name="Database Connection Storm",
    task_id="db_connection_storm",
    difficulty="medium",
    description=(
        "Multiple services are experiencing timeouts and elevated error rates. "
        "The database is running low on available connections. "
        "Investigate, find the root cause, and remediate."
    ),
    inject_fn=_inject_db_connection_storm,
    grading=GradingCriteria(
        root_cause_service="order-service",
        root_cause_keywords=["connection", "pool", "deployment", "order-service", "pool_size"],
        valid_remediations=[
            {"tool": "rollback_deployment", "target": "order-service"},
            {"tool": "update_config", "target": "order-service", "params_key": "pool_size"},
        ],
        partial_credit_services=["database", "order-service"],
    ),
)


# ---------------------------------------------------------------------------
# Scenario 3: Config Drift Chaos (Hard)
# ---------------------------------------------------------------------------

def _inject_config_drift(infra: Infrastructure) -> None:
    auth = infra.services["auth-service"]
    auth.config["token_ttl_seconds"] = 1
    auth.health = "healthy"
    auth.error_rate_pct = 2.8
    auth.latency_p99_ms = 350.0
    auth.cpu_pct = 65.0

    infra.add_deployment("auth-service", "3.1.0",
                         "Refactored token validation logic; updated default configs",
                         "2026-04-07 04:00:00")

    infra.inject_error_logs("auth-service", "auth_storm", count=6,
                            ttl=1, rate=1500, normal=10)
    infra.inject_error_logs("auth-service", "config_drift", count=2,
                            key="token_ttl_seconds", actual=1, expected=3600,
                            version="3.1.0")

    cache = infra.services["cache"]
    cache.cpu_pct = 78.0
    cache.memory_pct = 89.0
    cache.latency_p99_ms = 450.0

    infra.inject_error_logs("cache", "generic_error", count=3, rate=12)

    gw = infra.services["api-gateway"]
    gw.error_rate_pct = 6.5
    gw.latency_p99_ms = 2800.0

    infra.inject_error_logs("api-gateway", "timeout", count=4, target="auth-service")

    for svc_name in ["user-service", "order-service"]:
        svc = infra.services[svc_name]
        svc.error_rate_pct = round(infra._rng.uniform(4, 12), 1)
        svc.latency_p99_ms = round(infra._rng.uniform(1500, 4000), 1)
        infra.inject_error_logs(svc_name, "timeout", count=2, target="auth-service")

    infra.add_alert("warning", "api-gateway",
                    "Intermittent 5xx errors: error rate 6.5% (threshold: 5%)",
                    "2026-04-07 05:10:00")
    infra.add_alert("warning", "cache",
                    "High memory usage: 89% -- possible cache pressure",
                    "2026-04-07 05:08:00")
    infra.add_alert("info", "auth-service",
                    "Token refresh rate anomaly: 1500/min (baseline ~10/min)",
                    "2026-04-07 05:05:00")


CONFIG_DRIFT_SCENARIO = Scenario(
    name="Config Drift Chaos",
    task_id="config_drift",
    difficulty="hard",
    description=(
        "Intermittent 5xx errors are appearing across the API gateway. "
        "Multiple services seem affected, cache is under pressure, "
        "and auth token refresh rates are abnormally high. "
        "Find the root cause and fix it."
    ),
    inject_fn=_inject_config_drift,
    grading=GradingCriteria(
        root_cause_service="auth-service",
        root_cause_keywords=["token", "ttl", "config", "auth", "token_ttl"],
        valid_remediations=[
            {"tool": "update_config", "target": "auth-service", "params_key": "token_ttl_seconds"},
            {"tool": "rollback_deployment", "target": "auth-service"},
        ],
        partial_credit_services=["auth-service", "cache", "api-gateway"],
    ),
)


ALL_SCENARIOS: Dict[str, Scenario] = {
    "memory_leak": MEMORY_LEAK_SCENARIO,
    "db_connection_storm": DB_CONNECTION_STORM_SCENARIO,
    "config_drift": CONFIG_DRIFT_SCENARIO,
}


# ---------------------------------------------------------------------------
# Grading logic
# ---------------------------------------------------------------------------

def grade_episode(
    scenario: Scenario,
    actions_taken: List[Dict[str, Any]],
    resolve_called: bool,
    resolve_root_cause: str,
    resolve_remediation: str,
    steps_used: int,
    max_steps: int,
    services_investigated: List[str],
    destructive_actions_on_healthy: int,
    remediation_applied: bool,
    remediation_correct: bool,
) -> float:
    """
    Compute a final episode score in [0.0, 1.0].

    Components:
      - diagnosis_accuracy (0.3): keyword match on root cause description
      - remediation_quality (0.3): did they apply the right fix?
      - time_efficiency (0.2): fewer steps = better
      - investigation_quality (0.1): did they look at relevant services?
      - collateral_damage (0.1): penalty for unnecessary destructive actions
    """
    g = scenario.grading

    # 1. Diagnosis accuracy
    diagnosis_score = 0.0
    if resolve_called:
        keyword_score = _match_any_keyword(resolve_root_cause, g.root_cause_keywords)
        service_mentioned = 1.0 if g.root_cause_service.lower() in resolve_root_cause.lower() else 0.0
        diagnosis_score = 0.6 * keyword_score + 0.4 * service_mentioned
    elif remediation_correct:
        diagnosis_score = 0.4

    # 2. Remediation quality
    remediation_score = 0.0
    if remediation_correct:
        remediation_score = 1.0
    elif remediation_applied:
        for action in actions_taken:
            if action.get("tool") in ("restart_service", "rollback_deployment", "update_config", "scale_service"):
                if action.get("target") == g.root_cause_service:
                    remediation_score = 0.4
                    break
                elif action.get("target") in g.partial_credit_services:
                    remediation_score = 0.2
                    break

    # 3. Time efficiency
    time_score = max(0.0, 1.0 - (steps_used / max_steps))

    # 4. Investigation quality
    relevant_investigated = sum(
        1 for s in services_investigated
        if s == g.root_cause_service or s in g.partial_credit_services
    )
    total_relevant = 1 + len(g.partial_credit_services)
    investigation_score = min(1.0, relevant_investigated / total_relevant)

    # 5. Collateral damage
    collateral_score = max(0.0, 1.0 - destructive_actions_on_healthy * 0.25)

    final = (
        0.30 * diagnosis_score
        + 0.30 * remediation_score
        + 0.20 * time_score
        + 0.10 * investigation_score
        + 0.10 * collateral_score
    )

    return round(min(1.0, max(0.0, final)), 4)
