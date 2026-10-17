"""Network simulator: stateful fake telco backend for the DBA POC."""

import os

import uvicorn

from network_simulator.api import create_app
from network_simulator.engine import Network

__all__ = ["Network", "create_app", "main"]


def main() -> None:
    scenario_dir = os.getenv("SIMULATOR_SCENARIO_DIR", "data/scenarios")
    port = int(os.getenv("SIMULATOR_PORT", "8300"))
    app = create_app(Network.from_directory(scenario_dir))
    uvicorn.run(app, host=os.getenv("SIMULATOR_HOST", "0.0.0.0"), port=port)
