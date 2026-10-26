"""Explicit local/QuickML providers. Remote errors never fall back to local output."""
import json
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import joblib
import numpy as np
import pandas as pd
import requests

from .features import TARGET, WEATHER_COLUMNS, make_features, validate_inputs

ROOT = Path(__file__).resolve().parents[1]


class ProviderError(RuntimeError):
    pass


def provider_name():
    provider = os.getenv("MODEL_PROVIDER", "local").lower()
    if provider not in {"local", "quickml"}:
        raise ProviderError("MODEL_PROVIDER must be local or quickml.")
    return provider


@lru_cache(maxsize=1)
def local_bundle():
    path = ROOT / "models" / "demand_model.joblib"
    if not path.exists():
        raise ProviderError("No trained local model. Run python train.py first.")
    return joblib.load(path)


def metadata():
    path = ROOT / "models" / "metadata.json"
    return json.loads(path.read_text()) if path.exists() else None


def checked_predictions(values, count):
    try:
        result = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as error:
        raise ProviderError("The model returned non-numeric predictions.") from error
    if result.ndim == 2 and result.shape[1] == 1:
        result = result[:, 0]
    if result.ndim != 1 or len(result) != count or not np.isfinite(result).all():
        raise ProviderError("The model must return exactly one finite prediction per input row.")
    if (result < 0).any():
        raise ProviderError("The model returned negative energy demand.")
    return result


def quickml_predict(frame):
    endpoint = os.getenv("QUICKML_ENDPOINT_URL", "")
    if urlparse(endpoint).scheme != "https" or not urlparse(endpoint).netloc:
        raise ProviderError("Configure an HTTPS QUICKML_ENDPOINT_URL before using QuickML.")
    required = ["QUICKML_ACCESS_TOKEN", "QUICKML_ENDPOINT_KEY", "QUICKML_ORG_ID"]
    if any(not os.getenv(key) for key in required):
        raise ProviderError("QuickML credentials are incomplete. Configure the server environment.")
    headers = {"Authorization": "Zoho-oauthtoken " + os.environ["QUICKML_ACCESS_TOKEN"],
               "X-QUICKML-ENDPOINT-KEY": os.environ["QUICKML_ENDPOINT_KEY"],
               "CATALYST-ORG": os.environ["QUICKML_ORG_ID"],
               "Environment": os.getenv("QUICKML_ENVIRONMENT", "Development")}
    use_features = os.getenv("QUICKML_FEATURE_MODE", "engineered")
    if use_features not in {"raw", "engineered"}:
        raise ProviderError("QUICKML_FEATURE_MODE must be raw or engineered.")
    payload_frame = make_features(frame) if use_features == "engineered" else frame
    records = json.loads(payload_frame.to_json(orient="records"))
    request_key = os.getenv("QUICKML_REQUEST_KEY", "data")
    payload = {request_key: records} if request_key else records
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=(5, 20), allow_redirects=False)
        if not 200 <= response.status_code < 300:
            raise ProviderError(f"QuickML returned HTTP {response.status_code}. Check endpoint credentials and its input schema.")
        result = response.json()
    except requests.RequestException as error:
        raise ProviderError("QuickML could not be reached. Check connectivity and endpoint configuration.") from error
    except ValueError as error:
        raise ProviderError("QuickML returned invalid JSON.") from error
    # Match this path to the endpoint's Test response; default follows the supplied starter contract.
    try:
        for part in os.getenv("QUICKML_RESPONSE_PATH", "predictions").split("."):
            if part:
                result = result[int(part)] if isinstance(result, list) else result[part]
        if isinstance(result, list) and result and isinstance(result[0], dict):
            result = [row[os.getenv("QUICKML_TARGET_KEY", TARGET)] for row in result]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ProviderError("QuickML response does not match QUICKML_RESPONSE_PATH / QUICKML_TARGET_KEY.") from error
    return checked_predictions(result, len(frame))


def forecast(frame: pd.DataFrame):
    clean = validate_inputs(frame)
    provider = provider_name()
    if provider == "local":
        bundle = local_bundle()
        predictions = np.maximum(0, bundle["pipeline"].predict(make_features(clean)))
        name = bundle["metadata"]["model_name"]
    else:
        predictions = quickml_predict(clean)
        name = os.getenv("QUICKML_MODEL_NAME", "QuickML regression")
    return checked_predictions(predictions, len(clean)), {"provider": provider, "name": name}
