# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""
OnCallOps Environment Implementation.

A production incident response simulator where an AI agent acts as an
SRE on-call engineer, diagnosing and remediating real-world incidents
across a microservices architecture.
"""

from __future__ import annotations

import os
import random
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openenv.core.env_server.interfaces import Environment
from openenv.core.env_server.types import State

try:
    from ..models import OnCallAction, OnCallObservation
except ImportError:
    from models import OnCallAction, OnCallObservation

try:
    from .infrastructure import Infrastructure
    from .scenarios import ALL_SCENARIOS, Scenario, grade_episode
except ImportError:
    from server.infrastructure import Infrastructure
    from server.scenarios import ALL_SCENARIOS, Scenario, grade_episode

MAX_STEPS = 15
MINUTES_PER_STEP = 3.0

VALID_TOOLS = {
    "check_alerts",
    "check_logs",
    "check_metrics",
    "check_status",
    "check_dependencies",
    "check_recent_deployments",
    "check_config",
    "restart_service",
    "rollback_deployment",
    "scale_service",
    "update_config",
    "resolve_incident",
}

DESTRUCTIVE_TOOLS = {"restart_service", "rollback_deployment", "scale_service", "update_config"}


class OnCallEnvironment(Environment):
    """
    SRE On-Call Incident Response Environment.

    The agent receives alerts about production incidents and must use
    observability tools to diagnose root causes, then apply the correct
    remediation across a simulated microservices infrastructure.
    """

    SUPPORTS_CONCURRENT_SESSIONS: bool = True

    def __init__(self) -> None:
        super().__init__()
        self._state = State(episode_id=str(uuid4()), step_count=0)
        self._task_id: str = os.environ.get("ONCALL_TASK", "memory_leak")
        self._scenario: Optional[Scenario] = None
        self._infra: Optional[Infrastructure] = None
        self._rng = random.Random()

        self._actions_taken: List[Dict[str, Any]] = []
        self._services_investigated: set = set()
        self._destructive_on_healthy: int = 0
        self._remediation_applied: bool = False
        self._remediation_correct: bool = False
        self._resolve_called: bool = False
        self._resolve_root_cause: str = ""
        self._resolve_remediation: str = ""
        self._cumulative_reward: float = 0.0
        self._step_rewards: List[float] = []
        self._done: bool = False
        self._last_error: Optional[str] = None

    def reset(self, seed: Optional[int] = None, **kwargs: Any) -> OnCallObservation:
        task_id = kwargs.get("task_id") or kwargs.get("episode_id") or self._task_id

        if task_id in ALL_SCENARIOS:
            self._task_id = task_id
        else:
            self._task_id = "memory_leak"

        effective_seed = seed if seed is not None else 42
        self._rng = random.Random(effective_seed)
        self._state = State(episode_id=str(uuid4()), step_count=0)

        self._scenario = ALL_SCENARIOS[self._task_id]
        self._infra = Infrastructure(self._rng)
        self._scenario.inject_fn(self._infra)

        self._actions_taken = []
        self._services_investigated = set()
        self._destructive_on_healthy = 0
        self._remediation_applied = False
        self._remediation_correct = False
        self._resolve_called = False
        self._resolve_root_cause = ""
        self._resolve_remediation = ""
        self._cumulative_reward = 0.0
        self._step_rewards = []
        self._done = False
        self._last_error = None

        alert_text = self._infra.get_alerts()

        return OnCallObservation(
            alerts=[dict(a) for a in self._infra.alerts],
            tool_output=(
                f"=== INCIDENT DETECTED ===\n"
                f"Task: {self._scenario.name} ({self._scenario.difficulty})\n"
                f"{self._scenario.description}\n\n"
                f"Active alerts:\n{alert_text}\n\n"
                f"Available services: {', '.join(self._infra.service_names)}\n"
                f"Available tools: {', '.join(sorted(VALID_TOOLS))}\n\n"
                f"You have {MAX_STEPS} steps to diagnose and resolve the incident.\n"
                f"Use resolve_incident(root_cause, remediation) when done."
            ),
            time_elapsed_min=0.0,
            incident_status="investigating",
            services=self._infra.service_names,
            step_number=0,
            max_steps=MAX_STEPS,
            done=False,
            reward=0.0,
            last_action_error=None,
        )

    def step(self, action: OnCallAction, **kwargs: Any) -> OnCallObservation:  # type: ignore[override]
        if self._done:
            return self._terminal_observation("Episode already ended.")

        if self._infra is None or self._scenario is None:
            return self._terminal_observation("Environment not reset. Call reset() first.")

        self._state.step_count += 1
        self._last_error = None
        step_reward = 0.0

        tool = action.tool
        target = action.target
        params = action.params

        self._actions_taken.append({"tool": tool, "target": target, "params": params})

        if tool not in VALID_TOOLS:
            self._last_error = (
                f"Unknown tool '{tool}'. Valid tools: {', '.join(sorted(VALID_TOOLS))}"
            )
            step_reward = -0.02
        else:
            tool_output, step_reward = self._dispatch_tool(tool, target, params)

        if self._last_error:
            output_text = f"ERROR: {self._last_error}"
        else:
            output_text = tool_output  # type: ignore[assignment]

        self._cumulative_reward += step_reward
        self._step_rewards.append(round(step_reward, 4))

        at_step_limit = self._state.step_count >= MAX_STEPS
        if at_step_limit and not self._done:
            self._done = True
            output_text += "\n\n[EPISODE ENDED: Step limit reached without resolution]"

        time_elapsed = self._state.step_count * MINUTES_PER_STEP

        status = "investigating"
        if self._done and self._resolve_called:
            status = "resolved"
        elif self._done:
            status = "timed_out"
        elif self._remediation_applied:
            status = "mitigated"

        final_reward = step_reward
        if self._done:
            final_reward = self._compute_final_score()

        return OnCallObservation(
            alerts=[dict(a) for a in self._infra.alerts],
            tool_output=output_text,
            time_elapsed_min=time_elapsed,
            incident_status=status,
            services=self._infra.service_names,
            step_number=self._state.step_count,
            max_steps=MAX_STEPS,
            done=self._done,
            reward=round(final_reward, 4),
            last_action_error=self._last_error,
        )

    @property
    def state(self) -> State:
        return self._state

    # ---- tool dispatch -------------------------------------------------------

    def _dispatch_tool(self, tool: str, target: str, params: Dict[str, Any]) -> tuple:
        """Returns (output_text, step_reward)."""
        assert self._infra is not None
        assert self._scenario is not None

        g = self._scenario.grading

        if tool == "check_alerts":
            return self._infra.get_alerts(), 0.01

        if tool == "check_logs":
            if not target:
                self._last_error = "check_logs requires a target service"
                return "", -0.01
            self._services_investigated.add(target)
            keyword = params.get("keyword")
            reward = 0.02 if target in (g.root_cause_service, *g.partial_credit_services) else 0.0
            return self._infra.get_logs(target, keyword), reward

        if tool == "check_metrics":
            if not target:
                self._last_error = "check_metrics requires a target service"
                return "", -0.01
            self._services_investigated.add(target)
            metric = params.get("metric", "")
            reward = 0.02 if target in (g.root_cause_service, *g.partial_credit_services) else 0.0
            return self._infra.get_metrics(target, metric), reward

        if tool == "check_status":
            if not target:
                self._last_error = "check_status requires a target service"
                return "", -0.01
            self._services_investigated.add(target)
            reward = 0.01 if target in (g.root_cause_service, *g.partial_credit_services) else 0.0
            return self._infra.get_status(target), reward

        if tool == "check_dependencies":
            if not target:
                self._last_error = "check_dependencies requires a target service"
                return "", -0.01
            self._services_investigated.add(target)
            return self._infra.get_dependencies(target), 0.01

        if tool == "check_recent_deployments":
            return self._infra.get_recent_deployments(), 0.02

        if tool == "check_config":
            if not target:
                self._last_error = "check_config requires a target service"
                return "", -0.01
            self._services_investigated.add(target)
            reward = 0.03 if target == g.root_cause_service else 0.01
            return self._infra.get_config(target), reward

        if tool == "restart_service":
            return self._handle_restart(target)

        if tool == "rollback_deployment":
            return self._handle_rollback(target)

        if tool == "scale_service":
            return self._handle_scale(target, params)

        if tool == "update_config":
            return self._handle_update_config(target, params)

        if tool == "resolve_incident":
            return self._handle_resolve(params)

        self._last_error = f"Unimplemented tool: {tool}"
        return "", 0.0

    def _handle_restart(self, target: str) -> tuple:
        assert self._infra is not None and self._scenario is not None
        if not target:
            self._last_error = "restart_service requires a target service"
            return "", -0.01
        svc = self._infra.services.get(target)
        if svc is None:
            self._last_error = f"Unknown service: {target}"
            return "", -0.01

        was_healthy = svc.health == "healthy"
        output = self._infra.restart_service(target)
        self._remediation_applied = True

        g = self._scenario.grading
        if target == g.root_cause_service:
            is_valid = any(
                r["tool"] == "restart_service" and r["target"] == target
                for r in g.valid_remediations
            )
            if is_valid:
                self._remediation_correct = True
                return output, 0.15
            return output, 0.05
        else:
            if was_healthy:
                self._destructive_on_healthy += 1
                return output + "\n(Warning: this service was healthy before restart)", -0.05
            return output, 0.02

    def _handle_rollback(self, target: str) -> tuple:
        assert self._infra is not None and self._scenario is not None
        if not target:
            self._last_error = "rollback_deployment requires a target service"
            return "", -0.01
        svc = self._infra.services.get(target)
        if svc is None:
            self._last_error = f"Unknown service: {target}"
            return "", -0.01

        was_healthy = svc.health == "healthy"
        output = self._infra.rollback_deployment(target)
        self._remediation_applied = True

        g = self._scenario.grading
        is_valid = any(
            r["tool"] == "rollback_deployment" and r["target"] == target
            for r in g.valid_remediations
        )
        if is_valid:
            self._remediation_correct = True
            self._infra.restart_service(target)
            return output + "\nService restarted with previous version.", 0.20
        elif was_healthy:
            self._destructive_on_healthy += 1
            return output + "\n(Warning: rolled back a healthy service)", -0.05
        return output, 0.02

    def _handle_scale(self, target: str, params: Dict[str, Any]) -> tuple:
        assert self._infra is not None and self._scenario is not None
        if not target:
            self._last_error = "scale_service requires a target service"
            return "", -0.01
        replicas = params.get("replicas", 3)
        if not isinstance(replicas, int):
            try:
                replicas = int(replicas)
            except (ValueError, TypeError):
                self._last_error = "replicas must be an integer"
                return "", -0.01

        output = self._infra.scale_service(target, replicas)
        self._remediation_applied = True

        g = self._scenario.grading
        is_valid = any(
            r["tool"] == "scale_service" and r["target"] == target
            for r in g.valid_remediations
        )
        if is_valid:
            self._remediation_correct = True
            return output, 0.10
        return output, 0.01

    def _handle_update_config(self, target: str, params: Dict[str, Any]) -> tuple:
        assert self._infra is not None and self._scenario is not None
        if not target:
            self._last_error = "update_config requires a target service"
            return "", -0.01

        key = params.get("key", "")
        value = params.get("value")
        if not key:
            self._last_error = "update_config requires params.key"
            return "", -0.01
        if value is None:
            self._last_error = "update_config requires params.value"
            return "", -0.01

        output = self._infra.update_config(target, key, value)
        if "Unknown config key" in output:
            self._last_error = output
            return "", -0.01

        self._remediation_applied = True

        g = self._scenario.grading
        is_valid = any(
            r["tool"] == "update_config"
            and r["target"] == target
            and r.get("params_key", "") == key
            for r in g.valid_remediations
        )
        if is_valid:
            self._remediation_correct = True
            return output, 0.25
        return output, 0.02

    def _handle_resolve(self, params: Dict[str, Any]) -> tuple:
        root_cause = str(params.get("root_cause", ""))
        remediation = str(params.get("remediation", ""))

        if not root_cause:
            self._last_error = "resolve_incident requires params.root_cause"
            return "", -0.01
        if not remediation:
            self._last_error = "resolve_incident requires params.remediation"
            return "", -0.01

        self._resolve_called = True
        self._resolve_root_cause = root_cause
        self._resolve_remediation = remediation
        self._done = True

        return (
            f"Incident resolved.\n"
            f"Root cause: {root_cause}\n"
            f"Remediation: {remediation}"
        ), 0.0

    # ---- scoring -------------------------------------------------------------

    def _compute_final_score(self) -> float:
        if self._scenario is None:
            return 0.0

        return grade_episode(
            scenario=self._scenario,
            actions_taken=self._actions_taken,
            resolve_called=self._resolve_called,
            resolve_root_cause=self._resolve_root_cause,
            resolve_remediation=self._resolve_remediation,
            steps_used=self._state.step_count,
            max_steps=MAX_STEPS,
            services_investigated=list(self._services_investigated),
            destructive_actions_on_healthy=self._destructive_on_healthy,
            remediation_applied=self._remediation_applied,
            remediation_correct=self._remediation_correct,
        )

    def _terminal_observation(self, message: str) -> OnCallObservation:
        return OnCallObservation(
            tool_output=message,
            done=True,
            reward=0.0,
            step_number=self._state.step_count,
            max_steps=MAX_STEPS,
            incident_status="resolved" if self._resolve_called else "timed_out",
        )
