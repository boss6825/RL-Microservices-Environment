# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""
FastAPI application for the OnCallOps Incident Response Environment.

Exposes the OnCallEnvironment over HTTP and WebSocket endpoints.
"""

try:
    from openenv.core.env_server.http_server import create_app
except Exception as e:  # pragma: no cover
    raise ImportError(
        "openenv is required. Install with: pip install openenv-core"
    ) from e

try:
    from ..models import OnCallAction, OnCallObservation
    from .oncall_env_environment import OnCallEnvironment
except (ImportError, ModuleNotFoundError):
    from models import OnCallAction, OnCallObservation
    from server.oncall_env_environment import OnCallEnvironment


app = create_app(
    OnCallEnvironment,
    OnCallAction,
    OnCallObservation,
    env_name="oncall_env",
    max_concurrent_envs=4,
)


def main(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()
    main(host=args.host, port=args.port)  # main() callable entry point
