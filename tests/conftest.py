from collections import defaultdict, deque
import os
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def frame():
    return pd.read_csv(ROOT / "sample_test_input.csv").head(24)


@pytest.fixture(autouse=True)
def local_provider(monkeypatch):
    # Hermetic: drop real credentials loaded from app/.env so no test can reach QuickML.
    for key in list(os.environ):
        if key.startswith(("QUICKML_", "GRIDWISE_")):
            monkeypatch.delenv(key)
    monkeypatch.setattr("engine.inference._token", {"value": None, "expires": 0.0})
    monkeypatch.setattr("app._predict_calls", defaultdict(deque))
    monkeypatch.setenv("MODEL_PROVIDER", "local")
