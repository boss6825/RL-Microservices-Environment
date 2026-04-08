---
title: OnCallOps
emoji: 🚨
colorFrom: red
colorTo: blue
sdk: docker
app_port: 8000
---

# OnCallOps: Production Incident Response Environment

An OpenEnv environment that simulates **production incident response** -- the high-stakes, real-world task that SRE and DevOps engineers perform when paged at 3 AM. An AI agent must diagnose root causes across a microservices architecture using observability tools (logs, metrics, service status, configs, deployment history) and apply the correct remediation under time pressure.

---

## Motivation

Production incident response is one of the most expensive, stressful, and error-prone tasks in software engineering. Companies spend millions on SRE teams, and mean-time-to-resolution (MTTR) directly impacts revenue and user trust. This environment provides a realistic, reproducible testbed for training and evaluating AI agents on:

- **Multi-step diagnostic reasoning** across interconnected services
- **Tool-using investigation** (reading logs, checking metrics, tracing dependencies)
- **Decision-making under uncertainty** (red herrings, cascading failures)
- **Safe remediation** (avoiding unnecessary destructive actions)

---

## Environment Overview

### Architecture

The environment simulates a microservices infrastructure with 8 services:

| Service | Dependencies | Role |
|---------|-------------|------|
| `api-gateway` | auth-service, user-service, order-service | Entry point, routes requests |
| `auth-service` | database, cache | Authentication and token management |
| `user-service` | database, cache | User profile management |
| `order-service` | database, payment-service, message-queue | Order processing |
| `payment-service` | database | Payment processing |
| `database` | (none) | Primary data store |
| `cache` | (none) | Redis-like caching layer |
| `message-queue` | (none) | Async message broker |

### Action Space

Actions are tool invocations with a `tool` name, optional `target` service, and optional `params` dict.

```python
class OnCallAction(Action):
    tool: str       # Tool name (see table below)
    target: str     # Target service name (for service-specific tools)
    params: dict    # Tool-specific parameters
```

**Available Tools:**

| Tool | Target Required | Params | Description |
|------|----------------|--------|-------------|
| `check_alerts` | No | - | View all active alerts |
| `check_logs` | Yes | `keyword` (optional) | View service logs, optionally filtered |
| `check_metrics` | Yes | `metric` (optional: cpu, memory, error_rate, latency, connections) | View service metrics |
| `check_status` | Yes | - | Check service health status |
| `check_dependencies` | Yes | - | View upstream/downstream dependency map |
| `check_recent_deployments` | No | - | View recent deployment history across all services |
| `check_config` | Yes | - | View service configuration |
| `restart_service` | Yes | - | Restart a service |
| `rollback_deployment` | Yes | - | Rollback to previous deployed version |
| `scale_service` | Yes | `replicas` (int) | Scale service horizontally |
| `update_config` | Yes | `key`, `value` | Update a specific configuration value |
| `resolve_incident` | No | `root_cause`, `remediation` | Declare the incident resolved |

### Observation Space

```python
class OnCallObservation(Observation):
    alerts: list[dict]          # Active alerts with severity, service, summary
    tool_output: str            # Human-readable output from last tool call
    time_elapsed_min: float     # Simulated minutes since incident start
    incident_status: str        # investigating | identified | mitigated | resolved
    services: list[str]         # Available service names
    step_number: int            # Current step (0 to max_steps)
    max_steps: int              # Maximum steps allowed (15)
    done: bool                  # Whether episode has ended
    reward: float               # Step reward (final score on last step)
    last_action_error: str      # Error message if last action failed, else null
```

---

## Tasks

### Task 1: Memory Leak (Easy)

**Scenario:** The `user-service` is experiencing out-of-memory kills. A critical alert has fired, logs show OOM errors, memory usage is at 97%.

**Expected approach:** Check alerts -> check logs/metrics for user-service -> restart user-service -> resolve incident.

**Target score:** 0.7 - 0.9 (straightforward single-service issue)

### Task 2: Database Connection Storm (Medium)

**Scenario:** Multiple services are timing out. The `database` connection pool is nearly exhausted (198/200) because `order-service` had a bad deployment that increased its pool_size from 50 to 150, hogging connections.

**Expected approach:** Check alerts -> investigate multiple failing services -> trace timeouts to database -> check deployments -> rollback order-service deployment.

**Target score:** 0.4 - 0.6 (requires tracing through dependency chain)

### Task 3: Config Drift Chaos (Hard)

**Scenario:** Intermittent 5xx errors across the API gateway. The `auth-service` had a deployment that set `token_ttl_seconds` to 1 (instead of 3600), causing rapid token expiration, auth storms, cache pressure, and cascading failures. Red herrings abound.

**Expected approach:** Check alerts -> investigate auth anomalies -> check deployments and configs -> identify token_ttl misconfiguration -> update config or rollback auth-service.

**Target score:** 0.1 - 0.3 (subtle root cause, many red herrings)

---

## Reward Function

The reward function provides signal over the full trajectory:

| Component | Weight | Description |
|-----------|--------|-------------|
| Diagnosis accuracy | 30% | Keyword match on root cause description + correct service identified |
| Remediation quality | 30% | Correct fix applied to correct service |
| Time efficiency | 20% | Fewer steps used = higher score |
| Investigation quality | 10% | Checked relevant services (not random) |
| No collateral damage | 10% | Penalty for restarting/rolling back healthy services |

**Per-step rewards:**
- Small positive (+0.01 to +0.03) for productive investigation of relevant services
- Larger positive (+0.10 to +0.25) for correct remediation actions
- Small negative (-0.01 to -0.05) for errors, redundant actions, or destructive actions on healthy services

---

## Setup Instructions

### Prerequisites

- Python 3.10+
- Docker (for containerized deployment)
- `openenv-core` package
- `python-dotenv` package for local `.env` loading

### Local Environment Configuration

Create a local env file from the repository root before running the server or
the baseline inference script:

```bash
cp .env.example .env
```

Then edit `.env` and provide real values for:

- `HF_TOKEN`
- `API_BASE_URL`
- `MODEL_NAME`
- Optional: `ONCALL_TASK`

The `.env` file is gitignored and must not be committed.

### Required Hugging Face Token Permissions

Create `HF_TOKEN` with the following permissions enabled.

**User permissions (ArpitSolanki)**

- **Repositories**
  - Read access to contents of all repos under your personal namespace
  - View access requests for all gated repos under your personal namespace
  - Read access to contents of all public gated repos you can access
  - Write access to contents/settings of all repos under your personal namespace
- **Inference**
  - Make calls to Inference Providers
  - Make calls to your Inference Endpoints
  - Manage your Inference Endpoints
- **Webhooks**
  - Access webhooks data
  - Create and manage webhooks
- **Collections**
  - Read access to all collections under your personal namespace
  - Write access to all collections under your personal namespace
- **Discussions & Posts**
  - Interact with discussions / Open PRs on repos under your personal namespace
  - Interact with discussions / Open PRs on external repos
  - Interact with posts
- **Billing**
  - Read access to your billing usage and know if a payment method is set
- **Jobs**
  - Start and manage Jobs on your behalf

**Repository permissions**

- Read access to contents of selected repos
- View access requests for selected gated repos
- Interact with discussions / Open pull requests on selected repos
- Write access to contents/settings of selected repos

**Organization permissions**

- **Repositories**
  - Read access to contents of all repos in selected organizations
  - View access requests for gated repos in selected organizations
  - Interact with discussions / Open pull requests on repos in selected organizations
  - Write access to contents/settings of all repos in selected organizations
- **Inference**
  - Make calls to Inference Providers on behalf of the selected organizations
  - Make calls to the organization's Inference Endpoints
  - Manage the organization's Inference Endpoints
- **Org settings**
  - Read access to organizations settings
  - Write access to organizations settings / member management
- **Collections**
  - Read access to all collections in selected organizations
  - Write access to all collections in selected organizations
- **Resource Groups**
  - Write access to resource groups in selected organizations
- **Jobs**
  - Start and manage Jobs in selected organizations

### Local Development

```bash
# Install dependencies
pip install openenv-core openai python-dotenv

# Start the server
cd oncall_env
python -m server.app --port 8000

# In another terminal, test with curl
curl http://localhost:8000/health
```

### Docker

```bash
# Build from the project root
docker build -t oncall-env:latest -f server/Dockerfile .

# Run
docker run -p 8000:8000 oncall-env:latest
```

### Running the Baseline Inference

```bash
# Create a local config file from the repository root
cp .env.example .env

# Edit .env with your real Hugging Face token and model settings

# Run inference on all 3 tasks
python inference.py
```

### Setting the Task

The task can be set via:
- Environment variable: `ONCALL_TASK=db_connection_storm`
- Reset kwargs: `env.reset(task_id="config_drift")`

---

## Baseline Scores

Scores using `Qwen/Qwen2.5-72B-Instruct`:

| Task | Difficulty | Expected Score |
|------|-----------|---------------|
| memory_leak | Easy | 0.70 - 0.90 |
| db_connection_storm | Medium | 0.40 - 0.60 |
| config_drift | Hard | 0.10 - 0.30 |

---

## Project Structure

```
oncall_env/
  __init__.py              # Package exports
  models.py                # OnCallAction, OnCallObservation (Pydantic)
  client.py                # EnvClient WebSocket client
  openenv.yaml             # OpenEnv manifest with 3 tasks
  pyproject.toml           # Dependencies
  inference.py             # Baseline inference script
  README.md                # This file
  server/
    __init__.py
    app.py                 # FastAPI server via create_app()
    oncall_env_environment.py   # Core environment logic
    scenarios.py           # 3 incident scenario definitions + grading
    infrastructure.py      # Simulated microservices infrastructure
    Dockerfile             # Container definition
    requirements.txt       # Server dependencies
```

---

## License

BSD-style license. See LICENSE file for details.
