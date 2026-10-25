"""Explicit local / QuickML / Gridwise-API providers. Remote errors never fall back inside this module."""
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import requests

from .features import TARGET, WEATHER_COLUMNS, coerce_inputs, fill_weather, make_features, make_linear_features, validate_inputs

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)
# Published Gridwise endpoint (Catalyst IN data center). Not secret; credentials come from the environment.
DEFAULT_QUICKML_URL = "https://api.catalyst.zoho.in/quickml/v1/project/102738000000013053/endpoints/predict"
DEFAULT_QUICKML_ORG = "60093139810"
DEFAULT_API_URL = "https://gridwise-50047143069.development.catalystappsail.in"
RESPONSE_PATHS = ["data.result", "result", "predictions", "data.predictions"]
PROVIDERS = {"local", "quickml", "api"}
_token_lock = threading.Lock()
_token = {"value": None, "expires": 0.0}
status = {"last_quickml_success": None}  # ISO time of the latest successful QuickML response
# The linear member never sees temperatures far outside the 4,000 training hours (-1.4 to 38.3 °C).
LINEAR_TEMP_RANGE = (-5.0, 42.0)


class ProviderError(RuntimeError):
    pass


def load_env_file(path=ROOT / ".env"):
    """Fill unset variables from a local, git-ignored .env so secrets never live in the code."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


load_env_file()


def provider_name():
    provider = os.getenv("MODEL_PROVIDER", "local").lower()
    if provider not in PROVIDERS:
        raise ProviderError("MODEL_PROVIDER must be local, quickml, or api.")
    return provider


REFRESH_NAMES = ["QUICKML_CLIENT_ID", "QUICKML_CLIENT_SECRET", "QUICKML_REFRESH_TOKEN"]


def quickml_configured(platform=False):
    """True when QuickML can be called: own OAuth credentials, or (inside AppSail) the platform credential."""
    has_token = os.getenv("QUICKML_ACCESS_TOKEN") or all(os.getenv(key) for key in REFRESH_NAMES) or platform
    return bool(has_token and os.getenv("QUICKML_ENDPOINT_KEY"))


@lru_cache(maxsize=1)
def local_bundle():
    path = ROOT / "models" / "demand_model.joblib"
    if not path.exists():
        raise ProviderError("No trained local model. Run python train.py first.")
    import joblib  # only the local provider needs scikit-learn / joblib
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


def auth_header(refresh=False, platform=None):
    """Authorization for QuickML, in priority order: static token, self-client refresh token,
    then the admin credential AppSail injects into each request (platform = (type, token))."""
    if os.getenv("QUICKML_ACCESS_TOKEN"):
        return "Zoho-oauthtoken " + os.environ["QUICKML_ACCESS_TOKEN"]
    if all(os.getenv(name) for name in REFRESH_NAMES):
        return "Zoho-oauthtoken " + refreshed_token(refresh)
    if platform:
        kind, token = platform
        return ("Zoho-ticket " if kind == "ticket" else "Zoho-oauthtoken ") + token
    raise ProviderError("QuickML credentials are incomplete. Configure the server environment.")


def refreshed_token(refresh=False):
    names = REFRESH_NAMES
    with _token_lock:
        if refresh or not _token["value"] or time.time() >= _token["expires"]:
            accounts = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.in")
            try:
                reply = requests.post(f"{accounts}/oauth/v2/token", timeout=(5, 20), data={
                    "grant_type": "refresh_token", "client_id": os.environ[names[0]],
                    "client_secret": os.environ[names[1]], "refresh_token": os.environ[names[2]]})
                body = reply.json()
            except (requests.RequestException, ValueError) as error:
                raise ProviderError("Zoho accounts could not issue a QuickML access token.") from error
            if "access_token" not in body:
                raise ProviderError("Zoho accounts rejected the QuickML refresh credentials.")
            # Renew five minutes early so in-flight requests never carry an expired token.
            _token.update(value=body["access_token"], expires=time.time() + int(body.get("expires_in", 3600)) - 300)
        return _token["value"]


def extract_predictions(result, count):
    configured = os.getenv("QUICKML_RESPONSE_PATH")
    for path in [configured] if configured else RESPONSE_PATHS:
        value = result
        try:
            for part in path.split("."):
                if part:
                    value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            value = [row.get(os.getenv("QUICKML_TARGET_KEY", TARGET)) for row in value]
        if isinstance(value, list) and len(value) == count:
            return value
    raise ProviderError("QuickML response does not contain one prediction per row; check QUICKML_RESPONSE_PATH.")


def quickml_batch(endpoint, records, platform=None, key=None):
    request_key = os.getenv("QUICKML_REQUEST_KEY", "data")
    payload = {request_key: records} if request_key else records
    renewable = all(os.getenv(name) for name in REFRESH_NAMES) and not os.getenv("QUICKML_ACCESS_TOKEN")
    for attempt in range(2):
        headers = {"Authorization": auth_header(refresh=attempt == 1, platform=platform),
                   "X-QUICKML-ENDPOINT-KEY": key or os.environ["QUICKML_ENDPOINT_KEY"],
                   "CATALYST-ORG": os.getenv("QUICKML_ORG_ID", DEFAULT_QUICKML_ORG),
                   "Environment": os.getenv("QUICKML_ENVIRONMENT", "Development")}
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=(5, 30), allow_redirects=False)
            # An expired or revoked token surfaces as 400 INVALID_TICKET / 401: renew once and retry.
            if response.status_code in (400, 401) and attempt == 0 and renewable:
                continue
            if not 200 <= response.status_code < 300:
                raise ProviderError(f"QuickML returned HTTP {response.status_code}. Check endpoint credentials and its input schema.")
            values = extract_predictions(response.json(), len(records))
            status["last_quickml_success"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return values
        except requests.RequestException as error:
            raise ProviderError("QuickML could not be reached. Check connectivity and endpoint configuration.") from error
        except ValueError as error:
            raise ProviderError("QuickML returned invalid JSON.") from error
    raise ProviderError("QuickML rejected the renewed access token.")


def quickml_members():
    """Published QuickML endpoints: CatBoost on calendar/weather features, plus (when configured) a
    linear regression on hourly profile and hourly cooling features. Their average beat either alone
    on every late chronological split."""
    members = [{"name": os.getenv("QUICKML_MODEL_NAME", "QuickML regression"), "key": os.getenv("QUICKML_ENDPOINT_KEY"),
                "features": make_features}]
    if os.getenv("QUICKML_LINEAR_ENDPOINT_KEY"):
        members.append({"name": os.getenv("QUICKML_LINEAR_MODEL_NAME", "Linear regression (QuickML)"),
                        "key": os.environ["QUICKML_LINEAR_ENDPOINT_KEY"], "features": linear_payload})
    return members


def linear_payload(frame):
    bounded = frame.assign(ambient_temp_c=frame["ambient_temp_c"].clip(*LINEAR_TEMP_RANGE))
    return make_linear_features(bounded)


def member_predict(endpoint, member, frame, platform=None):
    mode = os.getenv("QUICKML_FEATURE_MODE", "engineered")
    if mode not in {"raw", "engineered"}:
        raise ProviderError("QUICKML_FEATURE_MODE must be raw or engineered.")
    payload_frame = member["features"](frame) if mode == "engineered" else frame
    records = json.loads(payload_frame.to_json(orient="records"))
    size = max(1, int(os.getenv("QUICKML_BATCH_SIZE", "200")))
    values = []
    for start in range(0, len(records), size):
        values.extend(quickml_batch(endpoint, records[start:start + size], platform, member["key"]))
    return checked_predictions(values, len(frame))


def quickml_predict(frame, platform=None):
    """Average of every published member; a failed member is skipped, and only all failing is an error.

    Returns (predictions, names of the members that answered).
    """
    endpoint = os.getenv("QUICKML_ENDPOINT_URL", DEFAULT_QUICKML_URL)
    if urlparse(endpoint).scheme != "https" or not urlparse(endpoint).netloc:
        raise ProviderError("Configure an HTTPS QUICKML_ENDPOINT_URL before using QuickML.")
    if not os.getenv("QUICKML_ENDPOINT_KEY"):
        raise ProviderError("QuickML credentials are incomplete. Configure the server environment.")
    members = quickml_members()
    with ThreadPoolExecutor(max_workers=len(members)) as pool:
        futures = [pool.submit(member_predict, endpoint, member, frame, platform) for member in members]
    answered, errors = [], []
    for member, future in zip(members, futures):
        try:
            answered.append((member["name"], future.result()))
        except ProviderError as error:
            logger.warning("QuickML member %s failed: %s", member["name"], error)
            errors.append(error)
    if not answered:
        raise errors[0]
    return np.mean([values for _, values in answered], axis=0), [name for name, _ in answered]


def api_predict(frame):
    """Forward raw rows to the deployed Gridwise app, which holds the QuickML credentials server-side."""
    base = os.getenv("GRIDWISE_API_URL", DEFAULT_API_URL).rstrip("/")
    if urlparse(base).scheme != "https" or not urlparse(base).netloc:
        raise ProviderError("GRIDWISE_API_URL must be an HTTPS URL.")
    rows = json.loads(frame.to_json(orient="records"))
    try:
        response = requests.post(f"{base}/api/predict", json={"rows": rows}, timeout=(10, 180))
        if not 200 <= response.status_code < 300:
            raise ProviderError(f"The Gridwise API returned HTTP {response.status_code}.")
        body = response.json()
    except requests.RequestException as error:
        raise ProviderError("The Gridwise API could not be reached.") from error
    except ValueError as error:
        raise ProviderError("The Gridwise API returned invalid JSON.") from error
    return checked_predictions(body.get("predictions"), len(frame)), body.get("model") or {}


def forecast(frame: pd.DataFrame, provider=None, platform=None, lenient=False):
    """Predict one demand value per row. lenient=True repairs inputs instead of rejecting them."""
    clean = coerce_inputs(frame) if lenient else validate_inputs(frame)
    provider = provider or provider_name()
    if provider not in PROVIDERS:
        raise ProviderError("MODEL_PROVIDER must be local, quickml, or api.")
    # QuickML pipelines have no imputation stage, so gaps are filled before any model sees the rows.
    filled = fill_weather(clean, (metadata() or {}).get("weather_medians", {})) if provider != "api" else clean
    if provider == "local":
        bundle = local_bundle()
        predictions = np.maximum(0, bundle["pipeline"].predict(make_features(filled)))
        name = bundle["metadata"]["model_name"]
    elif provider == "quickml":
        predictions, members = quickml_predict(filled, platform)
        name = f"QuickML ensemble: {' + '.join(members)}" if len(members) > 1 else members[0]
    else:
        predictions, remote = api_predict(clean)
        name = f"{remote.get('provider', 'unknown')} / {remote.get('name', 'unknown')} via Gridwise API"
    return checked_predictions(predictions, len(clean)), {"provider": provider, "name": name}


def forecast_with_backup(frame: pd.DataFrame, platform=None, lenient=False):
    """forecast(), but if QuickML or the relay fails, answer with the bundled local model and say so.

    Returns ((predictions, model_info), fallback_reason or None). Input errors still raise ValueError.
    """
    try:
        return forecast(frame, platform=platform, lenient=lenient), None
    except ProviderError as error:
        if provider_name() == "local":
            raise
        logger.warning("Remote model failed; serving the local backup: %s", error)
        predictions, info = forecast(frame, "local", lenient=lenient)
        return (predictions, {**info, "provider": "local-backup", "name": f"{info['name']} (backup: QuickML unavailable)"}), str(error)
