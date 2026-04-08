# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""
Data models for the OnCallOps Incident Response Environment.

Defines typed Action and Observation models for an SRE on-call simulation
where an AI agent diagnoses and resolves production incidents.
"""

from typing import Any, Dict, List, Optional

from openenv.core.env_server.types import Action, Observation
from pydantic import Field


class OnCallAction(Action):
    """Action for the OnCallOps environment -- an SRE tool invocation."""

    tool: str = Field(
        ...,
        description=(
            "Tool to use. One of: check_alerts, check_logs, check_metrics, "
            "check_status, check_dependencies, check_recent_deployments, "
            "restart_service, rollback_deployment, scale_service, "
            "update_config, resolve_incident"
        ),
    )
    target: str = Field(default="", description="Target service name")
    params: Dict[str, Any] = Field(
        default_factory=dict, description="Tool-specific parameters"
    )


class OnCallObservation(Observation):
    """Observation returned after each step in the OnCallOps environment."""

    alerts: List[Dict[str, str]] = Field(
        default_factory=list, description="Active alerts"
    )
    tool_output: str = Field(
        default="", description="Output from the last tool invocation"
    )
    time_elapsed_min: float = Field(
        default=0.0, description="Simulated minutes since incident start"
    )
    incident_status: str = Field(
        default="investigating",
        description="investigating | identified | mitigated | resolved",
    )
    services: List[str] = Field(
        default_factory=list, description="Available service names"
    )
    step_number: int = Field(default=0, description="Current step in episode")
    max_steps: int = Field(default=15, description="Maximum steps in episode")
    last_action_error: Optional[str] = Field(
        default=None, description="Error from last action, if any"
    )
