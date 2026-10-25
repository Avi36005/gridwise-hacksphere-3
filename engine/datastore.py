"""Persist forecast runs, telemetry, and controller events in Catalyst Data Store.

Works inside AppSail, where Catalyst injects the project and admin-credential headers into
every request; elsewhere persistence is simply off. Failures are logged and never affect a forecast.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)
# Same resolution as the Catalyst Python SDK: AppSail provides the API host in X_ZOHO_CATALYST_CONSOLE_URL.
API_URL = (os.getenv("CATALYST_API_URL") or os.getenv("X_ZOHO_CATALYST_CONSOLE_URL") or "https://api.catalyst.zoho.in").rstrip("/")
if not API_URL.startswith("http"):
    API_URL = "https://" + API_URL
RUNS, TELEMETRY, ALERTS = "ForecastRuns", "Telemetry", "AlertLog"


def context_from(headers):
    """Project id, environment, and admin credential from AppSail's injected headers, or None."""
    token, project = headers.get("x-zc-admin-cred-token"), headers.get("x-zc-projectid")
    if not token or not project:
        return None
    kind = headers.get("x-zc-admin-cred-type", "token")
    environment = headers.get("x-zc-environment", "Development")
    return {"project": project, "headers": {
        "Authorization": ("Zoho-ticket " if kind == "ticket" else "Zoho-oauthtoken ") + token,
        "PROJECT_ID": headers.get("x-zc-project-key", ""), "X-Catalyst-Environment": environment,
        "Environment": environment, "X-CATALYST-USER": "admin",
        "CATALYST-ORG": os.getenv("QUICKML_ORG_ID", "60093139810"),
        # Data Store also requires the per-project secret AppSail injects (never logged or returned).
        "X-ZC-PROJECT-SECRET-KEY": headers.get("x-zc-project-secret-key", "")}}


def controller_events(rows):
    """Mode switches and overload alert transitions, in forecast order."""
    events = []
    for index, row in enumerate(rows):
        previous = rows[index - 1] if index else None
        mode = "Renewable Storage Mode" if row["grid_mode"] == "renewable_storage" else "Main Grid Mode"
        if previous is None or previous["grid_mode"] != row["grid_mode"]:
            events.append({"event_hour": row["timestamp"], "event_type": "switch",
                           "message": f"{'Switch to' if previous else 'Start in'} {mode}: {row['reason']}"})
        if row["critical_overload"] and not (previous and previous["critical_overload"]):
            events.append({"event_hour": row["timestamp"], "event_type": "alert",
                           "message": f"Critical Overload Alert triggered: {row['target_demand_kw']:.1f} kW >= 200 kW"})
        if previous and previous["critical_overload"] and not row["critical_overload"]:
            events.append({"event_hour": row["timestamp"], "event_type": "clear",
                           "message": f"Critical Overload Alert cleared: {row['target_demand_kw']:.1f} kW < 200 kW"})
    return events


def _insert(context, table, rows):
    url = f"{API_URL}/baas/v1/project/{context['project']}/table/{table}/row"
    response = requests.post(url, json=rows, headers=context["headers"], timeout=(5, 20))
    _check(response)
    return response.json()["data"]


def _check(response):
    if response.status_code >= 400:
        detail = getattr(response, "text", "")[:300]
        raise requests.HTTPError(f"HTTP {response.status_code} from {response.url if hasattr(response, 'url') else API_URL}: {detail}")


def _query(context, zcql, table):
    url = f"{API_URL}/baas/v1/project/{context['project']}/query"
    response = requests.post(url, json={"query": zcql}, headers=context["headers"], timeout=(5, 20))
    _check(response)
    return [item[table] for item in response.json().get("data", []) if table in item]  # ZCQL wraps rows by table


def persist_run(context, source, rows, summary, model, blank_values):
    try:
        events = controller_events(rows)
        run = _insert(context, RUNS, [{
            "source": source[:120], "window_start": rows[0]["timestamp"], "window_end": rows[-1]["timestamp"],
            "model": f"{model['provider']} / {model['name']}"[:120], "peak_kw": summary["peak_demand_kw"],
            "energy_kwh": summary["energy_kwh"], "storage_hours": summary["storage_hours"],
            "overload_hours": summary["overload_hours"], "blank_values": blank_values,
            "mode_switches": sum(e["event_type"] == "switch" for e in events) - 1}])[0]
        run_id = run["ROWID"]
        _insert(context, TELEMETRY, [{
            "run_id": run_id, "forecast_hour": r["timestamp"], "ambient_temp_c": r.get("ambient_temp_c"),
            "humidity_pct": r.get("humidity_pct"), "solar_irradiance_wm2": r.get("solar_irradiance_wm2"),
            # The column keeps two decimals and truncates the rest; round so a reopened run matches the forecast.
            "predicted_kw": round(r["target_demand_kw"], 2), "grid_mode": r["grid_mode"],
            "overload": int(r["critical_overload"]), "battery_soc_pct": r["battery_soc_pct"]} for r in rows])
        if events:
            _insert(context, ALERTS, [{"run_id": run_id, **event} for event in events])
        return run_id
    except (requests.RequestException, KeyError, IndexError, ValueError) as error:
        logger.warning("Data Store persistence failed: %s", error)
        return None


def recent(context, runs=8, events=12):
    # ROWIDs are allocated in blocks, not in insertion order, so sort by creation time.
    runs_rows = _query(context, f"SELECT ROWID, CREATEDTIME, source, window_start, window_end, model, peak_kw, storage_hours, overload_hours, mode_switches FROM {RUNS} ORDER BY CREATEDTIME DESC LIMIT 0, {runs}", RUNS)
    event_rows = _query(context, f"SELECT ROWID, CREATEDTIME, run_id, event_hour, event_type, message FROM {ALERTS} ORDER BY CREATEDTIME DESC LIMIT 0, {events}", ALERTS)
    return runs_rows, event_rows


def run_rows(context, run_id):
    """The 24 saved telemetry rows of one run, in forecast order. run_id must be a Data Store ROWID."""
    if not str(run_id).isdigit():
        raise ValueError("Run ids are numeric.")
    columns = "forecast_hour, ambient_temp_c, humidity_pct, solar_irradiance_wm2, predicted_kw"
    run = _query(context, f"SELECT ROWID, source, model FROM {RUNS} WHERE ROWID = {run_id}", RUNS)
    try:
        rows = _query(context, f"SELECT {columns} FROM {TELEMETRY} WHERE run_id = {run_id} ORDER BY forecast_hour LIMIT 0, 48", TELEMETRY)
    except requests.HTTPError:  # a text-typed run_id column needs a quoted literal
        rows = _query(context, f"SELECT {columns} FROM {TELEMETRY} WHERE run_id = '{run_id}' ORDER BY forecast_hour LIMIT 0, 48", TELEMETRY)
    return (run[0] if run else None), rows
