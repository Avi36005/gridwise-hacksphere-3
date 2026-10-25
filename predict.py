"""HackSphere 3.0 submission entry point: predict(input_df), plus a timestamp-preserving CSV CLI.

Predictions come from the published Zoho Catalyst QuickML endpoints (project Gridwise, IN data
center): the average of a CatBoost regression and a linear regression on hourly cooling features. This file is self-contained: it needs only numpy, pandas,
and requests, like the starter kit, and runs even when copied on its own.

Routes, in order:
1. QuickML directly, when credentials are in the environment (QUICKML_ENDPOINT_KEY, optionally
   QUICKML_LINEAR_ENDPOINT_KEY, plus QUICKML_ACCESS_TOKEN or a self-client refresh token).
2. Otherwise the deployed Gridwise app (POST /api/predict). It holds the QuickML credentials
   server-side, builds the same features, and calls the same endpoint, so no secrets are needed.
3. For any chunk both fail on: a built-in copy of the linear member (numpy only; 9.3 kW RMSE on the
   locked holdout when fitted on the earlier hours), with a WARNING. As a last resort, the training
   mean. For any input with rows, predict() never crashes and never returns NaN.

Guardrails: column names are matched case- and space-insensitively; missing weather columns, blanks,
and non-numeric values are filled; out-of-range values are clipped; datetime columns are formatted.

    python predict.py --input sample_test_input.csv --output predictions.csv
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
import requests

QUICKML_ENDPOINT_URL = "https://api.catalyst.zoho.in/quickml/v1/project/102738000000013053/endpoints/predict"
QUICKML_ORG_ID = "60093139810"
# Deployed Gridwise app(s), tried in order; each relays to the QuickML endpoint above.
GRIDWISE_API_URLS = ["https://gridwise-50047143069.development.catalystappsail.in"]
INPUT_COLUMNS = ["timestamp", "ambient_temp_c", "humidity_pct", "solar_irradiance_wm2"]
CHUNK_ROWS = 2000
ROOT = Path(__file__).resolve().parent
TRAINING_MEAN_KW = 156.57
WEATHER_MEDIANS = {"ambient_temp_c": 18.545, "humidity_pct": 70.745, "solar_irradiance_wm2": 8.0}
LINEAR_TEMP_RANGE = (-5.0, 42.0)
WEATHER_LIMITS = {"ambient_temp_c": (-60, 60), "humidity_pct": (0, 100), "solar_irradiance_wm2": (0, 1500)}
# Backup: the linear member refitted by least squares on all 4,000 training hours. Order: intercept,
# temperature, humidity/100, irradiance/1000, heating degrees, weekend, hours 1-23, cooling x hours 0-23.
BACKUP_COEFFICIENTS = [
    93.5526, -0.1575, -2.1902, -0.1193, -0.1567, -0.2876, 1.2368, 1.9239,
    1.1596, 0.9318, 0.9939, 43.8022, 83.8271, 84.4169, 85.5509, 55.8789,
    54.8460, 54.4181, 55.5568, 54.5826, 54.2534, 55.8340, 55.1395, 109.4181,
    110.4160, 109.9996, 109.6794, 108.4619, 21.0473, 9.6830, 9.4963, 10.3690,
    9.8653, 8.6493, 9.6775, 9.5930, 9.0239, 9.5648, 9.1422, 8.8267,
    8.8860, 8.7119, 8.6313, 8.9720, 8.8533, 8.7299, 8.7013, 8.5509,
    8.5657, 8.7652, 8.4757, 8.9638, 9.1255]


def _load_env_file():
    """Fill unset variables from a git-ignored .env next to this file (local development only)."""
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())


def _normalize(frame: pd.DataFrame):
    """Match columns case- and space-insensitively; a missing weather column becomes blanks to fill."""
    renamed = frame.rename(columns={c: str(c).strip().lower() for c in frame.columns})
    if "timestamp" not in renamed.columns:
        raise ValueError("Input needs a timestamp column.")
    missing = [c for c in INPUT_COLUMNS[1:] if c not in renamed.columns]
    if missing:
        print(f"WARNING: missing columns {', '.join(missing)} are treated as blanks and filled.", file=sys.stderr)
        renamed = renamed.assign(**{c: np.nan for c in missing})
    return renamed[INPUT_COLUMNS].reset_index(drop=True)


def _rows(frame: pd.DataFrame):
    """JSON-ready rows with string timestamps (datetime columns would otherwise serialise as epochs)."""
    data = _normalize(frame)
    if pd.api.types.is_datetime64_any_dtype(data["timestamp"]):
        data["timestamp"] = data["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    data["timestamp"] = data["timestamp"].astype(str)
    return json.loads(data.to_json(orient="records"))


def _post(url, payload, headers=None, attempts=5):
    """POST with patient retries for cold starts, rate limits (honouring Retry-After), and outages."""
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=(10, 180))
            if response.status_code in (429, 500, 502, 503, 504) and not last:
                retry_after = getattr(response, "headers", {}).get("Retry-After")
                time.sleep(min(float(retry_after) if retry_after else 2 * 2 ** attempt, 30))
                continue
            response.raise_for_status()
            return response.json()
        except (requests.ConnectionError, requests.Timeout):
            if last:
                raise
            time.sleep(min(2 * 2 ** attempt, 30))


def _api_bases():
    configured = os.getenv("GRIDWISE_API_URL")
    return [configured.rstrip("/")] if configured else GRIDWISE_API_URLS


def _via_gridwise_api(frame):
    """Score in chunks; a chunk that every URL fails on is scored locally, so earlier QuickML results are kept."""
    rows, values, sources = _rows(frame), [], []
    for start in range(0, len(rows), CHUNK_ROWS):
        chunk, failures = rows[start:start + CHUNK_ROWS], []
        for base in _api_bases():
            try:
                body = _post(f"{base}/api/predict", {"rows": chunk})
                if len(body["predictions"]) != len(chunk):
                    raise ValueError("prediction count mismatch")
                values.extend(body["predictions"])
                model = body.get("model") or {}
                sources.append(f"{model.get('provider', '?')} / {model.get('name', '?')} via {base}")
                break
            except Exception as error:  # try the next URL, then the local model for this chunk only
                failures.append(f"{base}: {error}")
        else:
            backup, _ = _via_backup(frame.iloc[start:start + len(chunk)])
            print(f"WARNING: rows {start}-{start + len(chunk) - 1} used the built-in backup model ({'; '.join(failures)}).", file=sys.stderr)
            values.extend(backup)
            sources.append("backup model")
    remote = [s for s in sources if s != "backup model"]
    provider = "api" if len(remote) == len(sources) else "backup" if not remote else "mixed"
    return values, {"provider": provider, "name": ", ".join(dict.fromkeys(sources))}


def _features(frame):
    """Identical to engine.features.make_features (a test keeps them in sync)."""
    dates = pd.to_datetime(frame["timestamp"])
    features = frame[INPUT_COLUMNS[1:]].astype(float).copy()
    features["hour"] = dates.dt.hour.astype(float)
    features["day_of_week"] = dates.dt.day_of_week.astype(float)
    features["is_weekend"] = (dates.dt.day_of_week >= 5).astype(float)
    for name, values, period in [("hour", dates.dt.hour, 24), ("week", dates.dt.day_of_week, 7), ("year", dates.dt.dayofyear, 365.25)]:
        features[f"{name}_sin"] = np.sin(2 * np.pi * values / period)
        features[f"{name}_cos"] = np.cos(2 * np.pi * values / period)
    features["cooling_degrees"] = (features["ambient_temp_c"] - 24).clip(lower=0)
    features["heating_degrees"] = (18 - features["ambient_temp_c"]).clip(lower=0)
    return features


def _linear_features(frame):
    """Identical to engine.features.make_linear_features (a test keeps them in sync)."""
    dates = pd.to_datetime(frame["timestamp"])
    hours = dates.dt.hour.to_numpy()
    temperature = frame["ambient_temp_c"].to_numpy(dtype=float)
    cooling = np.clip(temperature - 24, 0, None)
    features = {"ambient_temp_c": temperature,
                "humidity_frac": frame["humidity_pct"].to_numpy(dtype=float) / 100,
                "solar_kw_m2": frame["solar_irradiance_wm2"].to_numpy(dtype=float) / 1000,
                "heating_degrees": np.clip(18 - temperature, 0, None),
                "is_weekend": (dates.dt.day_of_week >= 5).to_numpy(dtype=float)}
    for hour in range(1, 24):
        features[f"hour_{hour:02d}"] = (hours == hour).astype(float)
    for hour in range(24):
        features[f"cooling_hour_{hour:02d}"] = (hours == hour) * cooling
    return pd.DataFrame(features, index=frame.index)


def _bounded(data):
    """The linear model never extrapolates far beyond the training temperatures (-1.4 to 38.3 C)."""
    return data.assign(ambient_temp_c=data["ambient_temp_c"].clip(LINEAR_TEMP_RANGE[0], LINEAR_TEMP_RANGE[1]))


def _direct_token():
    if os.getenv("QUICKML_ACCESS_TOKEN"):
        return os.environ["QUICKML_ACCESS_TOKEN"]
    accounts = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.in")
    reply = requests.post(f"{accounts}/oauth/v2/token", timeout=(5, 20), data={
        "grant_type": "refresh_token", "client_id": os.environ["QUICKML_CLIENT_ID"],
        "client_secret": os.environ["QUICKML_CLIENT_SECRET"], "refresh_token": os.environ["QUICKML_REFRESH_TOKEN"]})
    return reply.json()["access_token"]


def _direct_configured():
    refresh = all(os.getenv(k) for k in ["QUICKML_CLIENT_ID", "QUICKML_CLIENT_SECRET", "QUICKML_REFRESH_TOKEN"])
    return bool(os.getenv("QUICKML_ENDPOINT_KEY") and (os.getenv("QUICKML_ACCESS_TOKEN") or refresh))


def _clean(frame):
    """Numeric, clipped, gap-free weather plus parsed timestamps (unparseable ones interpolated)."""
    data = pd.DataFrame(_rows(frame))
    for column, (low, high) in WEATHER_LIMITS.items():
        values = pd.to_numeric(data[column], errors="coerce").astype(float).clip(low, high)
        data[column] = values.interpolate(limit_direction="both").fillna(WEATHER_MEDIANS[column])
    stamps = pd.to_datetime(data["timestamp"], errors="coerce")
    if stamps.isna().any():
        nanos = stamps.astype("int64").where(stamps.notna()).astype(float).interpolate(limit_direction="both")
        stamps = pd.to_datetime(nanos.round(), unit="ns")
    data["timestamp"] = stamps.dt.strftime("%Y-%m-%d %H:%M:%S")
    return data


def _via_quickml(frame):
    """Direct QuickML: each configured endpoint scores its own features; the answers are averaged."""
    data = _clean(frame)
    token = _direct_token()
    members = [(os.environ["QUICKML_ENDPOINT_KEY"], _features(data))]
    if os.getenv("QUICKML_LINEAR_ENDPOINT_KEY"):
        members.append((os.environ["QUICKML_LINEAR_ENDPOINT_KEY"], _linear_features(_bounded(data))))
    answers = []
    for key, features in members:
        records = json.loads(features.to_json(orient="records"))
        headers = {"Authorization": "Zoho-oauthtoken " + token, "X-QUICKML-ENDPOINT-KEY": key,
                   "CATALYST-ORG": os.getenv("QUICKML_ORG_ID", QUICKML_ORG_ID),
                   "Environment": os.getenv("QUICKML_ENVIRONMENT", "Development")}
        values = []
        for start in range(0, len(records), 200):
            body = _post(os.getenv("QUICKML_ENDPOINT_URL", QUICKML_ENDPOINT_URL), {"data": records[start:start + 200]}, headers)
            values.extend(body.get("result") or body["data"]["result"])
        answers.append(np.asarray(values, dtype=float))
    return np.mean(answers, axis=0), {"provider": "quickml", "name": f"QuickML endpoints (direct, {len(answers)} averaged)"}


def _via_backup(frame):
    """Built-in linear regression: numpy only, so it works wherever this file runs."""
    data = _clean(frame)
    if data["timestamp"].isna().any():  # no usable hour at all: the training mean is the honest answer
        raise ValueError("no parseable timestamps")
    features = _linear_features(_bounded(data))
    return BACKUP_COEFFICIENTS[0] + features.to_numpy() @ np.asarray(BACKUP_COEFFICIENTS[1:]), {
        "provider": "backup", "name": "built-in backup regression"}


def _via_constant(frame):
    return np.full(len(frame), TRAINING_MEAN_KW), {"provider": "constant", "name": "training mean demand"}


def predict_with_info(input_df: pd.DataFrame):
    _load_env_file()
    if os.getenv("MODEL_PROVIDER") == "local":  # offline development and tests
        routes = [_via_backup, _via_constant]
    else:
        routes = ([_via_quickml] if _direct_configured() else []) + [_via_gridwise_api, _via_backup, _via_constant]
    errors = []
    for route in routes:
        try:
            values, info = route(input_df)
            result = np.asarray(values, dtype=float).reshape(-1)
            if len(result) != len(input_df) or not np.isfinite(result).all():
                raise ValueError("expected one finite prediction per input row")
            if errors:
                print("WARNING: " + "; ".join(errors) + f". Used {info['provider']} instead.", file=sys.stderr)
            return np.maximum(result, 0), info
        except Exception as error:  # try the next route; report every failure if all fail
            errors.append(f"{route.__name__.removeprefix('_via_')} failed ({error})")
    raise RuntimeError("All prediction routes failed: " + "; ".join(errors))


def predict(input_df: pd.DataFrame):
    """Return a numpy array with one demand prediction (kW) per input row, in input order."""
    return predict_with_info(input_df)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Input and output files must be different.")
    try:
        frame = pd.read_csv(args.input)
        predictions, info = predict_with_info(frame)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"timestamp": _normalize(frame)["timestamp"].to_numpy(), "target_demand_kw": np.round(predictions, 4)}).to_csv(args.output, index=False)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Prediction failed: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {len(predictions)} predictions using {info['provider']} / {info['name']} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
