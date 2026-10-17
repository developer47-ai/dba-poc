from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from network_simulator.api import create_app
from network_simulator.engine import Network

SCENARIO_DIR = Path(__file__).resolve().parents[3] / "data" / "scenarios"


@pytest.fixture
def network() -> Network:
    return Network.from_directory(SCENARIO_DIR)


@pytest.fixture
def client(network) -> TestClient:
    return TestClient(create_app(network))
