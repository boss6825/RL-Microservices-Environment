# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""OnCallOps Environment Client."""

from typing import Dict

from openenv.core import EnvClient
from openenv.core.client_types import StepResult
from openenv.core.env_server.types import State

from .models import OnCallAction, OnCallObservation


class OnCallEnv(EnvClient[OnCallAction, OnCallObservation, State]):
    """
    Client for the OnCallOps Incident Response Environment.

    Maintains a persistent WebSocket connection for multi-step incident
    diagnosis and remediation workflows.

    Example:
        >>> with OnCallEnv(base_url="http://localhost:8000") as client:
        ...     result = client.reset()
        ...     result = client.step(OnCallAction(tool="check_alerts"))
        ...     print(result.observation.tool_output)
    """

    def _step_payload(self, action: OnCallAction) -> Dict:
        return {
            "tool": action.tool,
            "target": action.target,
            "params": action.params,
        }

    def _parse_result(self, payload: Dict) -> StepResult[OnCallObservation]:
        obs_data = payload.get("observation", {})
        observation = OnCallObservation(
            alerts=obs_data.get("alerts", []),
            tool_output=obs_data.get("tool_output", ""),
            time_elapsed_min=obs_data.get("time_elapsed_min", 0.0),
            incident_status=obs_data.get("incident_status", "investigating"),
            services=obs_data.get("services", []),
            step_number=obs_data.get("step_number", 0),
            max_steps=obs_data.get("max_steps", 15),
            done=payload.get("done", False),
            reward=payload.get("reward", 0.0),
            last_action_error=obs_data.get("last_action_error"),
            metadata=obs_data.get("metadata", {}),
        )
        return StepResult(
            observation=observation,
            reward=payload.get("reward"),
            done=payload.get("done", False),
        )

    def _parse_state(self, payload: Dict) -> State:
        return State(
            episode_id=payload.get("episode_id"),
            step_count=payload.get("step_count", 0),
        )
