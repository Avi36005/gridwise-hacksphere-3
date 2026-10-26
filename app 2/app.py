"""FastAPI service and same-origin dashboard; run with python app.py."""
import io
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from engine.controller import BatteryConfig, simulate
from engine.features import WEATHER_COLUMNS, validate_inputs
from engine.inference import ProviderError, forecast, metadata, provider_name

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 1024 * 1024
app = FastAPI(title="Gridwise · HackSphere", version="1.0.0")
logger = logging.getLogger(__name__)


class ForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[dict] = Field(min_length=24, max_length=24)
    battery: dict[str, float] = Field(default_factory=dict)


@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ProviderError)
async def provider_error(request, error):
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.get("/api/health")
def health():
    provider = provider_name()
    ready = (ROOT / "models/demand_model.joblib").exists() if provider == "local" else all(os.getenv(k) for k in ["QUICKML_ENDPOINT_URL", "QUICKML_ACCESS_TOKEN", "QUICKML_ENDPOINT_KEY", "QUICKML_ORG_ID"])
    return {"status": "ready" if ready else "configuration_needed", "provider": provider, "remote_verified": False}


@app.get("/api/model")
def model_info():
    return {"provider": provider_name(), "local_training": metadata(),
            "battery_defaults": asdict(BatteryConfig()), "sample_label": "Historical starter data · June–July 2015"}


@app.get("/api/sample")
def sample():
    frame = pd.read_csv(ROOT / "sample_test_input.csv")
    return {"rows": json.loads(frame.to_json(orient="records")), "source": "Historical starter data"}


@app.get("/api/template.csv")
def template():
    return FileResponse(ROOT / "sample_test_input.csv", media_type="text/csv", filename="sample_test_input.csv")


async def limited_body(request: Request):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BYTES:
            raise HTTPException(413, "Input is too large. Maximum size is 1 MB.")
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


def build_forecast(payload: ForecastRequest):
    try:
        frame = validate_inputs(pd.DataFrame(payload.rows), window=True)
        config = BatteryConfig(**payload.battery)
        config.validate()
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from error
    predictions, model = forecast(frame)
    missing = int(frame[WEATHER_COLUMNS].isna().sum().sum())
    warnings = []
    if missing:
        warnings.append(f"{missing} blank weather values: the model's configured imputer handles them.")
    # Battery simulation needs finite irradiance independently of the model's imputation.
    simulation_frame = frame.copy()
    medians = (metadata() or {}).get("weather_medians", {})
    if simulation_frame["solar_irradiance_wm2"].isna().any():
        simulation_frame["solar_irradiance_wm2"] = simulation_frame["solar_irradiance_wm2"].fillna(medians.get("solar_irradiance_wm2", 0))
        warnings.append("Missing irradiance uses the local training median (or zero if unavailable) for the battery simulation only.")
    rows = simulate(simulation_frame, predictions, config)
    storage = sum(row["grid_mode"] == "renewable_storage" for row in rows)
    overload = sum(row["critical_overload"] for row in rows)
    backup = sum(row["storage_shortfall"] for row in rows)
    peak_index = int(np.argmax(predictions))
    return {"rows": rows, "model": model, "warnings": warnings, "battery": asdict(config),
            "summary": {"peak_demand_kw": round(float(np.max(predictions)), 2), "peak_timestamp": rows[peak_index]["timestamp"],
                        "energy_kwh": round(float(np.sum(predictions)), 2), "storage_hours": storage,
                        "overload_hours": overload, "backup_hours": backup,
                        "ending_soc_pct": rows[-1]["battery_soc_pct"]}}


@app.post("/api/forecast")
async def forecast_endpoint(request: Request):
    body = await limited_body(request)
    try:
        payload = ForecastRequest.model_validate_json(body)
    except ValueError as error:
        raise HTTPException(422, "Supply exactly 24 input rows and numeric battery settings.") from error
    return await run_in_threadpool(build_forecast, payload)


@app.get("/")
def index():
    return FileResponse(ROOT / "static/landing.html")


@app.get("/dashboard")
def dashboard():
    return FileResponse(ROOT / "static/index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("X_ZOHO_CATALYST_LISTEN_PORT", os.getenv("PORT", "8000")))
    default_host = "0.0.0.0" if os.getenv("X_ZOHO_CATALYST_LISTEN_PORT") else "127.0.0.1"
    uvicorn.run(app, host=os.getenv("HOST", default_host), port=port)
