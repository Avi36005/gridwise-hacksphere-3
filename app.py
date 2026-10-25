"""FastAPI service and same-origin dashboard; run with python app.py."""
import io
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from engine import datastore
from engine.controller import BatteryConfig, battery_scenarios, simulate
from engine.features import WEATHER_COLUMNS, fill_weather, validate_inputs
from engine.inference import ProviderError, forecast, forecast_with_backup, metadata, provider_name, quickml_configured, quickml_members
from engine.inference import status as inference_status

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 1024 * 1024
MAX_PREDICT_BYTES = 4 * 1024 * 1024
# Each /api/predict call can trigger billable QuickML requests, so clients are rate-limited.
RATE_LIMITS, RATE_WINDOW_S = {"predict": 30, "forecast": 60, "scenario": 120}, 60
_predict_calls: dict[tuple, deque] = defaultdict(deque)
app = FastAPI(title="Gridwise · HackSphere", version="1.0.0")
logger = logging.getLogger(__name__)


class ForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[dict] = Field(min_length=24, max_length=24)
    battery: dict[str, float] = Field(default_factory=dict)
    source: str = Field(default="Dashboard forecast", max_length=120)


class BatteryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    battery: dict[str, float] = Field(default_factory=dict)


class ScenarioRequest(BatteryRequest):
    rows: list[dict] = Field(min_length=24, max_length=24)


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[dict] = Field(min_length=1, max_length=10000)


@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Exception)
async def unexpected_error(request, error):
    # Last line of defence: log the details server-side, never leak internals to the client.
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Unexpected server error. Please try again."})


@app.exception_handler(ProviderError)
async def provider_error(request, error):
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.get("/api/health")
def health():
    provider = provider_name()
    on_appsail = bool(os.getenv("X_ZOHO_CATALYST_LISTEN_PORT"))
    ready = {"local": (ROOT / "models/demand_model.joblib").exists(), "quickml": quickml_configured(platform=on_appsail), "api": False}[provider]
    return {"status": "ready" if ready else "configuration_needed", "provider": provider, "last_quickml_success_utc": inference_status["last_quickml_success"]}


@app.get("/api/model")
def model_info():
    evidence = ROOT / "models" / "quickml.json"
    members = [member["name"] for member in quickml_members()] if provider_name() == "quickml" else []
    return {"provider": provider_name(), "members": members, "local_training": metadata(),
            "quickml": json.loads(evidence.read_text()) if evidence.exists() else None,
            "battery_defaults": asdict(BatteryConfig()), "sample_label": "Historical starter data · June–July 2015"}


@app.get("/api/sample")
def sample():
    frame = pd.read_csv(ROOT / "sample_test_input.csv")
    return {"rows": json.loads(frame.to_json(orient="records")), "source": "Historical starter data"}


@app.get("/api/template.csv")
def template():
    return FileResponse(ROOT / "sample_test_input.csv", media_type="text/csv", filename="sample_test_input.csv")


async def limited_body(request: Request, limit: int = MAX_BYTES):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"Input is too large. Maximum size is {limit // (1024 * 1024)} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/api/inputs/csv")
async def upload_csv(request: Request):
    body = await limited_body(request)
    try:
        frame = pd.read_csv(io.StringIO(body.decode("utf-8-sig")))
        clean = validate_inputs(frame)
        if len(clean) < 24:
            raise ValueError("Upload at least 24 hourly rows.")
        dates = pd.to_datetime(clean["timestamp"])
        if not (np.diff(dates.to_numpy(dtype="datetime64[s]")) == np.timedelta64(3600, "s")).all():
            raise ValueError("Uploaded rows must be consecutive hours in increasing order.")
    except (UnicodeError, ValueError, pd.errors.ParserError) as error:
        raise HTTPException(422, str(error)) from error
    return {"rows": json.loads(clean.to_json(orient="records")), "missing_values": int(clean[WEATHER_COLUMNS].isna().sum().sum())}


def run_in_background(target, *args):
    threading.Thread(target=target, args=args, daemon=True).start()


def platform_credential(request: Request):
    """The admin credential AppSail injects into proxied requests; used for QuickML, never echoed or logged."""
    token = request.headers.get("x-zc-admin-cred-token")
    return (request.headers.get("x-zc-admin-cred-type", "token"), token) if token else None


def battery_from(settings):
    try:
        config = BatteryConfig(**settings)
        config.validate()
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from error
    return config


def forecast_result(frame, predictions, model, config, warnings, missing):
    """Dispatch, battery simulation, and summary for one 24-hour window of predictions."""
    # The battery simulation uses the same filled weather the model saw.
    simulation_frame = fill_weather(frame, (metadata() or {}).get("weather_medians", {}))
    rows = simulate(simulation_frame, predictions, config)
    for row, weather in zip(rows, simulation_frame[WEATHER_COLUMNS].to_dict(orient="records")):
        row.update({key: round(float(value), 3) for key, value in weather.items()})
    storage = sum(row["grid_mode"] == "renewable_storage" for row in rows)
    overload = sum(row["critical_overload"] for row in rows)
    backup = sum(row["storage_shortfall"] for row in rows)
    peak_index = int(np.argmax(predictions))
    return {"rows": rows, "model": model, "warnings": warnings, "battery": asdict(config), "blank_values": missing,
            "summary": {"peak_demand_kw": round(float(np.max(predictions)), 2), "peak_timestamp": rows[peak_index]["timestamp"],
                        "energy_kwh": round(float(np.sum(predictions)), 2), "storage_hours": storage,
                        "overload_hours": overload, "backup_hours": backup,
                        "ending_soc_pct": rows[-1]["battery_soc_pct"]}}


def build_forecast(payload: ForecastRequest, platform=None):
    try:
        frame = validate_inputs(pd.DataFrame(payload.rows), window=True)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    config = battery_from(payload.battery)
    (predictions, model), fallback_reason = forecast_with_backup(frame, platform=platform)
    missing = int(frame[WEATHER_COLUMNS].isna().sum().sum())
    warnings = []
    if fallback_reason:
        warnings.append("QuickML was unavailable, so this forecast uses the bundled backup model. Run it again shortly for QuickML results.")
    if missing:
        warnings.append(f"{missing} blank weather values were filled by interpolating between neighbouring hours (training medians at the edges).")
    return forecast_result(frame, predictions, model, config, warnings, missing)


def scenario_window(rows):
    """Validate a forecast window sent back by the browser: 24 consecutive hours plus their predictions."""
    frame = validate_inputs(pd.DataFrame(rows), window=True)
    demand = pd.to_numeric(pd.Series([row.get("target_demand_kw") for row in rows]), errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(demand).all() or (demand < 0).any():
        raise ValueError("Every hour needs a finite, nonnegative target_demand_kw from a forecast.")
    return frame, demand


@app.post("/api/forecast")
async def forecast_endpoint(request: Request):
    if not within_rate_limit(request, "forecast"):
        raise HTTPException(429, "Too many forecasts. Try again in a minute.")
    body = await limited_body(request)
    try:
        payload = ForecastRequest.model_validate_json(body)
    except ValueError as error:
        raise HTTPException(422, "Supply exactly 24 input rows and numeric battery settings.") from error
    result = await run_in_threadpool(build_forecast, payload, platform_credential(request))
    # Save the run, its telemetry, and controller events to Catalyst Data Store without delaying the reply.
    context = datastore.context_from(request.headers)
    if context:
        run_in_background(datastore.persist_run, context, payload.source, result["rows"], result["summary"],
                          result["model"], result["blank_values"])
    result["persisted"] = bool(context)
    return result


@app.post("/api/scenarios")
async def scenarios_endpoint(request: Request):
    """Battery sizing on an existing forecast: no model call, so it is instant and free."""
    if not within_rate_limit(request, "scenario"):
        raise HTTPException(429, "Too many scenario requests. Try again in a minute.")
    body = await limited_body(request)
    try:
        payload = ScenarioRequest.model_validate_json(body)
    except ValueError as error:
        raise HTTPException(422, "Send the 24 forecast rows and numeric battery settings.") from error
    try:
        frame, demand = scenario_window(payload.rows)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    config = battery_from(payload.battery)
    filled = fill_weather(frame, (metadata() or {}).get("weather_medians", {}))
    return {"battery": asdict(config), **battery_scenarios(filled, demand, config)}


@app.post("/api/runs/{run_id}/replay")
async def replay_run(run_id: str, request: Request):
    """Reopen a saved run: predictions come from Data Store; dispatch and battery are recomputed."""
    context = datastore.context_from(request.headers)
    if not context:
        raise HTTPException(404, "Saved runs are available on the deployed app only.")
    if not run_id.isdigit() or len(run_id) > 25:
        raise HTTPException(422, "Run ids are numeric.")
    body = await limited_body(request)
    try:
        payload = BatteryRequest.model_validate_json(body or b"{}")
    except ValueError as error:
        raise HTTPException(422, "Battery settings must be numbers.") from error
    config = battery_from(payload.battery)
    try:
        run, saved = await run_in_threadpool(datastore.run_rows, context, run_id)
    except Exception as error:
        logger.warning("Data Store replay failed: %s", error)
        raise HTTPException(503, "Data Store is unavailable right now.") from error
    if not run or len(saved) != 24:
        raise HTTPException(404, "That run is not available.")
    frame = pd.DataFrame({"timestamp": pd.to_datetime([r["forecast_hour"] for r in saved]).strftime("%Y-%m-%d %H:%M:%S"),
                          **{column: pd.to_numeric([r.get(column) for r in saved], errors="coerce") for column in WEATHER_COLUMNS}})
    predictions = np.asarray(pd.to_numeric(pd.Series([r["predicted_kw"] for r in saved]), errors="coerce"), dtype=float)
    if not np.isfinite(predictions).all():
        raise HTTPException(404, "That run is incomplete.")
    model = {"provider": "datastore", "name": f"Saved run …{run_id[-4:]} · {run.get('model') or 'unknown model'}"}
    result = forecast_result(frame, predictions, model, config, [f"Reopened from Catalyst Data Store ({run.get('source') or 'saved run'}); no new model call."], 0)
    return {**result, "persisted": False, "replayed_run": run_id}


@app.get("/api/runs")
async def recent_runs(request: Request):
    """Recent forecast runs and controller events from Catalyst Data Store (deployed app only)."""
    context = datastore.context_from(request.headers)
    if not context:
        return {"enabled": False, "runs": [], "events": []}
    try:
        runs, events = await run_in_threadpool(datastore.recent, context)
    except Exception as error:  # the log is informational; never fail the dashboard over it
        logger.warning("Data Store read failed: %s", error)
        return {"enabled": True, "error": "Data Store is unavailable right now.", "runs": [], "events": []}
    return {"enabled": True, "runs": runs, "events": events}


def within_rate_limit(request: Request, bucket: str = "predict"):
    forwarded = request.headers.get("x-forwarded-for", "")
    client = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    calls, now = _predict_calls[(bucket, client)], time.monotonic()
    while calls and now - calls[0] > RATE_WINDOW_S:
        calls.popleft()
    if len(calls) >= RATE_LIMITS[bucket]:
        return False
    calls.append(now)
    return True


@app.post("/api/predict")
async def predict_endpoint(request: Request):
    """Batch predictions for predict.py: the server holds the QuickML credentials, callers need none."""
    if not within_rate_limit(request):
        raise HTTPException(429, "Too many prediction requests. Try again in a minute.")
    if provider_name() == "api":
        raise HTTPException(503, "This server is configured to forward to another Gridwise API, not to serve predictions.")
    body = await limited_body(request, MAX_PREDICT_BYTES)
    try:
        payload = PredictRequest.model_validate_json(body)
    except ValueError as error:
        raise HTTPException(422, "Supply between 1 and 10,000 rows with timestamp and weather fields.") from error

    platform = platform_credential(request)

    def run():
        try:
            return forecast_with_backup(pd.DataFrame(payload.rows), platform=platform, lenient=True)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    (predictions, model), fallback_reason = await run_in_threadpool(run)
    reply = {"predictions": [round(float(value), 4) for value in predictions], "model": model}
    if fallback_reason:
        reply["warning"] = "QuickML was unavailable; these predictions come from the bundled backup model."
    return reply


@app.get("/")
def index():
    return FileResponse(ROOT / "static/landing.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(ROOT / "static/favicon.svg", media_type="image/svg+xml")


@app.get("/dashboard")
def dashboard():
    return FileResponse(ROOT / "static/index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("X_ZOHO_CATALYST_LISTEN_PORT", os.getenv("PORT", "8000")))
    default_host = "0.0.0.0" if os.getenv("X_ZOHO_CATALYST_LISTEN_PORT") else "127.0.0.1"
    uvicorn.run(app, host=os.getenv("HOST", default_host), port=port)
