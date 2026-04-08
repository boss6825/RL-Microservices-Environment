"""
Inference Script for OnCallOps: Production Incident Response Environment
========================================================================

MANDATORY
- Before submitting, ensure the following variables are defined in your local `.env`
  file or exported in your shell environment:
    API_BASE_URL   The API endpoint for the LLM.
    MODEL_NAME     The model identifier to use for inference.
    HF_TOKEN       Your Hugging Face / API key.

STDOUT FORMAT
- [START] task=<task_name> env=<benchmark> model=<model_name>
- [STEP]  step=<n> action=<action_str> reward=<0.00> done=<true|false> error=<msg|null>
- [END]   success=<true|false> steps=<n> score=<score> rewards=<r1,r2,...,rn>
"""

import json
import os
import sys
import textwrap
from typing import List, Optional

from openai import OpenAI

# ---------------------------------------------------------------------------
# Environment imports -- works both standalone and as a package
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from oncall_env.env_config import get_env, load_local_env
from oncall_env.models import OnCallAction, OnCallObservation
from oncall_env.server.oncall_env_environment import OnCallEnvironment

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_local_env()

API_KEY = get_env("HF_TOKEN", "API_KEY") or ""
API_BASE_URL = get_env("API_BASE_URL") or ""
MODEL_NAME = get_env("MODEL_NAME") or ""

BENCHMARK = "oncall_env"
TASKS = ["memory_leak", "db_connection_storm", "config_drift"]
MAX_STEPS = 15
TEMPERATURE = 0.3
MAX_TOKENS = 512

SYSTEM_PROMPT = textwrap.dedent("""\
You are an expert SRE on-call engineer responding to a production incident.
You have access to the following tools to diagnose and remediate the issue:

INVESTIGATION tools (use target= to specify a service):
  check_alerts          - View all active alerts
  check_logs            - View service logs (optional params.keyword to filter)
  check_metrics         - View service metrics (optional params.metric: cpu|memory|error_rate|latency|connections)
  check_status          - Check service health status
  check_dependencies    - View service dependency map
  check_recent_deployments - View recent deployment history
  check_config          - View service configuration

REMEDIATION tools:
  restart_service       - Restart a service (target=service_name)
  rollback_deployment   - Rollback to previous version (target=service_name)
  scale_service         - Scale replicas (target=service_name, params.replicas=N)
  update_config         - Update config (target=service_name, params.key=..., params.value=...)
  resolve_incident      - Declare resolved (params.root_cause=..., params.remediation=...)

STRATEGY:
1. Start by checking alerts to understand what's happening
2. Investigate the affected services (logs, metrics, status)
3. Trace dependencies to find the root cause
4. Check recent deployments and configs for changes
5. Apply the correct remediation
6. Call resolve_incident with a clear root_cause and remediation summary

Respond with ONLY a JSON object (no markdown, no explanation):
{"tool": "tool_name", "target": "service_name", "params": {"key": "value"}}
""")


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
def log_start(task: str, env: str, model: str) -> None:
    print(f"[START] task={task} env={env} model={model}")


def log_step(step: int, action: str, reward: float, done: bool, error: Optional[str]) -> None:
    err_str = "null" if error is None else error
    done_str = "true" if done else "false"
    print(f"[STEP] step={step} action={action} reward={reward:.2f} done={done_str} error={err_str}")


def log_end(success: bool, steps: int, score: float, rewards: List[float]) -> None:
    success_str = "true" if success else "false"
    rewards_str = ",".join(f"{r:.2f}" for r in rewards)
    print(f"[END] success={success_str} steps={steps} score={score:.2f} rewards={rewards_str}")


# ---------------------------------------------------------------------------
# Parse LLM response into an action
# ---------------------------------------------------------------------------
def parse_action(text: str) -> OnCallAction:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
            except json.JSONDecodeError:
                return OnCallAction(
                    tool="check_alerts",
                    target="",
                    params={},
                )
        else:
            return OnCallAction(tool="check_alerts", target="", params={})

    return OnCallAction(
        tool=data.get("tool", "check_alerts"),
        target=data.get("target", ""),
        params=data.get("params", {}),
    )


# ---------------------------------------------------------------------------
# Run one episode
# ---------------------------------------------------------------------------
def run_episode(task_id: str, client: OpenAI) -> float:
    env = OnCallEnvironment()
    obs = env.reset(seed=42, task_id=task_id)

    log_start(task=task_id, env=BENCHMARK, model=MODEL_NAME)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": obs.tool_output},
    ]

    rewards: List[float] = []
    final_score = 0.0

    for step_num in range(1, MAX_STEPS + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
            )
            assistant_text = response.choices[0].message.content or ""
        except Exception as e:
            assistant_text = '{"tool": "check_alerts"}'
            print(f"  [WARN] LLM call failed: {e}", file=sys.stderr)

        action = parse_action(assistant_text)
        action_str = f"{action.tool}({action.target})"

        obs = env.step(action)

        reward = obs.reward if obs.reward is not None else 0.0
        rewards.append(reward)
        error = obs.last_action_error

        log_step(step_num, action_str, reward, obs.done, error)

        if obs.done:
            final_score = reward
            break

        messages.append({"role": "assistant", "content": assistant_text})
        messages.append({"role": "user", "content": obs.tool_output})

    if not obs.done:
        final_score = rewards[-1] if rewards else 0.0

    success = final_score > 0.3
    log_end(success=success, steps=len(rewards), score=final_score, rewards=rewards)

    return final_score


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    missing_config = []
    if not API_KEY:
        missing_config.append("HF_TOKEN (or legacy API_KEY)")
    if not API_BASE_URL:
        missing_config.append("API_BASE_URL")
    if not MODEL_NAME:
        missing_config.append("MODEL_NAME")

    if missing_config:
        print(
            "ERROR: Missing required configuration: "
            f"{', '.join(missing_config)}. "
            "Copy .env.example to .env and set real values before running inference.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = OpenAI(api_key=API_KEY, base_url=API_BASE_URL)

    scores = {}
    for task_id in TASKS:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  Running task: {task_id}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        score = run_episode(task_id, client)
        scores[task_id] = score

    print(f"\n{'='*60}", file=sys.stderr)
    print("  BASELINE SCORES", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    for task_id, score in scores.items():
        print(f"  {task_id}: {score:.4f}", file=sys.stderr)
    avg = sum(scores.values()) / len(scores) if scores else 0.0
    print(f"  Average: {avg:.4f}", file=sys.stderr)


if __name__ == "__main__":
    main()
