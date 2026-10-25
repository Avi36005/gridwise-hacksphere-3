"""Property and matrix tests: each invariant is checked on many generated or hand-picked inputs."""
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient

import predict as submission
from app import app
from engine.controller import BatteryConfig, battery_scenarios, dispatch_signal, simulate
from engine.datastore import controller_events
from engine.features import make_features, make_linear_features, validate_inputs

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = pd.read_csv(ROOT / "sample_test_input.csv")
EVENING = range(18, 23)


def random_window(seed, hours=24):
    """Consecutive hours somewhere in 2015 with random weather and random predicted demand."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2015-01-01") + pd.to_timedelta(int(rng.integers(0, 24 * 360)), unit="h")
    frame = pd.DataFrame({"timestamp": pd.date_range(start, periods=hours, freq="h").strftime("%Y-%m-%d %H:%M:%S"),
                          "ambient_temp_c": rng.uniform(-5, 40, hours).round(2),
                          "humidity_pct": rng.uniform(5, 100, hours).round(2),
                          "solar_irradiance_wm2": np.clip(rng.normal(250, 300, hours), 0, None).round(1)})
    return frame, rng.uniform(0, 320, hours).round(3)


def random_config(seed):
    rng = np.random.default_rng(seed + 10_000)
    reserve = float(rng.uniform(0, 50))
    return BatteryConfig(capacity_kwh=float(rng.uniform(10, 5000)), initial_soc_pct=float(rng.uniform(reserve, 100)),
                         reserve_soc_pct=reserve, solar_capacity_kw=float(rng.uniform(0, 400)),
                         max_charge_kw=float(rng.uniform(0, 400)), max_discharge_kw=float(rng.uniform(0, 400)),
                         efficiency=float(rng.uniform(0.7, 1.0)))


# Battery simulation: energy balance, ratings, and state of charge on random days and batteries.
@pytest.mark.parametrize("seed", range(150))
def test_simulation_balances_energy_and_respects_ratings(seed):
    frame, demand = random_window(seed)
    config = random_config(seed)
    rows = simulate(frame, demand, config)
    energy = config.capacity_kwh * config.initial_soc_pct / 100
    reserve = config.capacity_kwh * config.reserve_soc_pct / 100
    tolerance = 0.01
    for row, value in zip(rows, demand):
        signal = dispatch_signal(float(value), row["timestamp"])
        assert row["grid_mode"] == signal["grid_mode"] and row["critical_overload"] == signal["critical_overload"]
        solar, battery, charge = row["solar_kw"], row["battery_discharge_kw"], row["battery_charge_kw"]
        assert 0 <= solar <= config.solar_capacity_kw + tolerance
        assert 0 <= battery <= config.max_discharge_kw + tolerance and 0 <= charge <= config.max_charge_kw + tolerance
        if row["grid_mode"] == "renewable_storage":
            deficit = max(0.0, value - solar)
            assert battery + row["grid_backup_kw"] == pytest.approx(deficit, abs=0.002)  # load is always served
            assert row["grid_supply_kw"] == row["grid_backup_kw"]
            assert charge <= max(0.0, solar - value) + tolerance  # only true surplus charges the battery
            if row["storage_shortfall"]:  # grid support only when the battery is power- or energy-limited
                limited_by_power = battery >= config.max_discharge_kw - tolerance
                limited_by_energy = energy - battery / config.efficiency <= reserve + 0.05
                assert limited_by_power or limited_by_energy
        else:
            assert row["grid_supply_kw"] == pytest.approx(value, abs=0.001) and battery == 0 and not row["storage_shortfall"]
            assert charge <= solar + tolerance
        energy += charge * config.efficiency - battery / config.efficiency
        assert config.reserve_soc_pct - 0.01 <= row["battery_soc_pct"] <= 100.01
        assert row["battery_soc_pct"] == pytest.approx(energy / config.capacity_kwh * 100, abs=0.02)


# Battery sizing: more storage never hurts, and the recommendation is the smallest size that works.
@pytest.mark.parametrize("seed", range(60))
def test_scenarios_are_monotonic_and_the_recommendation_is_minimal(seed):
    frame, demand = random_window(seed + 500)
    config = random_config(seed + 500)
    result = battery_scenarios(frame, demand, config)
    scenarios, rec = result["scenarios"], result["recommendation"]
    sizes = [s["capacity_kwh"] for s in scenarios]
    assert sizes == sorted(sizes) and len(set(sizes)) == len(sizes)
    for smaller, larger in zip(scenarios, scenarios[1:]):
        assert larger["grid_backup_kwh"] <= smaller["grid_backup_kwh"] + 0.01
        assert larger["shortfall_hours"] <= smaller["shortfall_hours"]
    rows = simulate(frame, demand, config)
    assert rec["current_shortfall_hours"] == sum(r["storage_shortfall"] for r in rows)
    assert rec["discharge_kw"] >= max(config.max_discharge_kw, rec["required_discharge_kw"] - 0.05)
    sizing = replace(config, max_discharge_kw=rec["discharge_kw"])
    covers = lambda kwh: not any(r["storage_shortfall"] for r in simulate(frame, demand, replace(sizing, capacity_kwh=kwh)))
    if rec["capacity_kwh"] is None:
        assert not covers(100000)
    else:
        assert covers(rec["capacity_kwh"])
        assert rec["capacity_kwh"] == 1 or not covers(rec["capacity_kwh"] - 1)


# Feature parity: the self-contained predict.py must build exactly what the server sends to QuickML.
@pytest.mark.parametrize("seed", range(40))
def test_submission_builds_the_same_features_as_the_server(seed):
    rng = np.random.default_rng(seed + 900)
    stamps = pd.Timestamp("2012-01-01") + pd.to_timedelta(rng.integers(0, 24 * 365 * 8, 120), unit="h")
    frame = pd.DataFrame({"timestamp": stamps.strftime("%Y-%m-%d %H:%M:%S"), "ambient_temp_c": rng.uniform(-60, 60, 120),
                          "humidity_pct": rng.uniform(0, 100, 120), "solar_irradiance_wm2": rng.uniform(0, 1500, 120)})
    pd.testing.assert_frame_equal(submission._features(frame), make_features(frame))
    pd.testing.assert_frame_equal(submission._linear_features(frame), make_linear_features(frame))


@pytest.mark.parametrize("hour", range(24))
def test_linear_design_encodes_each_hour_once(hour):
    frame = pd.DataFrame({"timestamp": [f"2015-06-17 {hour:02d}:00:00"] * 3, "ambient_temp_c": [10.0, 24.0, 31.5],
                          "humidity_pct": [50.0] * 3, "solar_irradiance_wm2": [0.0] * 3})
    features = make_linear_features(frame)
    indicators = features[[f"hour_{h:02d}" for h in range(1, 24)]].to_numpy()
    assert (indicators.sum(axis=1) == (0 if hour == 0 else 1)).all()  # hour 0 is the reference level
    slopes = features[[f"cooling_hour_{h:02d}" for h in range(24)]]
    assert slopes[f"cooling_hour_{hour:02d}"].tolist() == [0.0, 0.0, 7.5]
    assert slopes.drop(columns=f"cooling_hour_{hour:02d}").to_numpy().sum() == 0
    assert features["heating_degrees"].tolist() == [8.0, 0.0, 0.0]


# Transition controller: monotonic in demand, readable reasons, and strict input checks.
@pytest.mark.parametrize("hour", range(24))
def test_controller_signals_only_escalate_as_demand_rises(hour):
    stamp = f"2015-06-17 {hour:02d}:00:00"
    signals = [dispatch_signal(kw, stamp) for kw in np.arange(0, 400.5, 0.5)]
    storage = [s["grid_mode"] == "renewable_storage" for s in signals]
    alerts = [s["critical_overload"] for s in signals]
    assert storage == sorted(storage) and alerts == sorted(alerts)  # False... then True, never back
    assert all(storage) if hour in EVENING else storage.index(True) == 321  # first value above 160 kW is 160.5
    assert alerts.index(True) == 400  # exactly 200 kW


@pytest.mark.parametrize("hour", range(24))
@pytest.mark.parametrize("demand", [100, 160, 180, 250])
def test_controller_reason_explains_the_decision(hour, demand):
    reason = dispatch_signal(demand, f"2015-06-17 {hour:02d}:00:00")["reason"]
    high, evening = demand > 160, hour in EVENING
    expected = {(True, True): "Demand above 160 kW + evening window", (True, False): "Demand above 160 kW",
                (False, True): "Evening window (18:00–22:00)", (False, False): "Demand at or below 160 kW, outside evening window"}
    assert reason == expected[(high, evening)]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -0.01, -1e9])
def test_controller_rejects_impossible_demand_at_every_hour(value):
    for hour in range(24):
        with pytest.raises(ValueError, match="finite and nonnegative"):
            dispatch_signal(value, f"2015-06-17 {hour:02d}:00:00")


@pytest.mark.parametrize("seed", range(60))
def test_controller_log_records_every_switch_and_alert_edge(seed):
    frame, demand = random_window(seed + 2000)
    rows = simulate(frame, demand, BatteryConfig())
    events = controller_events(rows)
    modes = [r["grid_mode"] for r in rows]
    alerts = [r["critical_overload"] for r in rows]
    switches = [e for e in events if e["event_type"] == "switch"]
    assert switches[0]["event_hour"] == rows[0]["timestamp"] and switches[0]["message"].startswith("Start in")
    assert len(switches) == 1 + sum(a != b for a, b in zip(modes, modes[1:]))
    assert sum(e["event_type"] == "alert" for e in events) == alerts[0] + sum(not a and b for a, b in zip(alerts, alerts[1:]))
    assert sum(e["event_type"] == "clear" for e in events) == sum(a and not b for a, b in zip(alerts, alerts[1:]))
    hours = [e["event_hour"] for e in events]
    assert hours == sorted(hours)
    for event in events:
        row = rows[[r["timestamp"] for r in rows].index(event["event_hour"])]
        if event["event_type"] == "alert":
            assert row["critical_overload"] and f"{row['target_demand_kw']:.1f} kW" in event["message"]
        if event["event_type"] == "switch":
            assert ("Renewable Storage Mode" if row["grid_mode"] == "renewable_storage" else "Main Grid Mode") in event["message"]


# Dashboard input validation: every impossible value is refused with a message naming the problem.
@pytest.mark.parametrize("column,value,message", [
    ("timestamp", "2015-13-01 00:00:00", "not valid calendar dates"), ("timestamp", "2015-02-29 00:00:00", "not valid calendar dates"),
    ("timestamp", "2015-06-17 24:00:00", "not valid calendar dates"), ("timestamp", "2015-06-17 10:30:00", "hourly"),
    ("timestamp", "2015-06-17T10:00:00", "hourly"), ("timestamp", "17/06/2015 10:00", "hourly"), ("timestamp", "", "hourly"),
    ("timestamp", "yesterday", "hourly"), ("humidity_pct", -0.01, "Humidity"), ("humidity_pct", 100.01, "Humidity"),
    ("humidity_pct", 1e9, "Humidity"), ("humidity_pct", "wet", "humidity_pct must contain finite numbers"),
    ("solar_irradiance_wm2", -0.001, "Solar irradiance cannot be negative"), ("solar_irradiance_wm2", float("inf"), "finite numbers"),
    ("solar_irradiance_wm2", "sunny", "finite numbers"), ("ambient_temp_c", -60.01, "Temperature"), ("ambient_temp_c", 60.01, "Temperature"),
    ("ambient_temp_c", float("-inf"), "finite numbers"), ("ambient_temp_c", "hot", "finite numbers"), ("ambient_temp_c", "1e400", "finite numbers"),
])
def test_dashboard_inputs_refuse_impossible_values(frame, column, value, message):
    frame = frame.astype(object)
    frame.loc[5, column] = value
    with pytest.raises(ValueError, match=message):
        validate_inputs(frame, window=True)


@pytest.mark.parametrize("column,value", [("humidity_pct", 0), ("humidity_pct", 100), ("ambient_temp_c", -60), ("ambient_temp_c", 60),
                                          ("solar_irradiance_wm2", 0), ("solar_irradiance_wm2", 1400), ("ambient_temp_c", " "),
                                          ("humidity_pct", None), ("solar_irradiance_wm2", "  12.5 "), ("timestamp", "2016-02-29 00:00:00")])
def test_dashboard_inputs_accept_boundary_and_blank_values(column, value):
    frame = SAMPLE.head(1).astype(object)
    frame.loc[0, column] = value
    clean = validate_inputs(frame)
    assert len(clean) == 1 and clean[["ambient_temp_c", "humidity_pct", "solar_irradiance_wm2"]].dtypes.eq(float).all()


@pytest.mark.parametrize("change,message", [
    (lambda f: f.drop(columns="humidity_pct"), "Missing columns: humidity_pct"),
    (lambda f: f.iloc[:23], "exactly 24 rows"), (lambda f: pd.concat([f, f.iloc[[0]]]), "exactly 24 rows"),
    (lambda f: f.iloc[::-1], "consecutive hours in increasing order"),
    (lambda f: f.assign(timestamp=pd.date_range("2015-06-16", periods=24, freq="2h").strftime("%Y-%m-%d %H:%M:%S")), "consecutive"),
    (lambda f: f.iloc[[0] * 24], "consecutive"),
])
def test_forecast_windows_must_be_24_consecutive_hours(frame, change, message):
    with pytest.raises(ValueError, match=message):
        validate_inputs(change(frame), window=True)


# The grader's entry point: any input with rows gets one finite, nonnegative prediction per row, in order.
MESSY = {
    "upper-case and padded headers": lambda f: f.rename(columns=lambda c: f"  {c.upper()} "),
    "extra columns": lambda f: f.assign(site="A", target_demand_kw=0),
    "columns reordered": lambda f: f[f.columns[::-1]],
    "missing humidity column": lambda f: f.drop(columns="humidity_pct"),
    "missing every weather column": lambda f: f[["timestamp"]],
    "all temperatures blank": lambda f: f.assign(ambient_temp_c=np.nan),
    "text in weather": lambda f: f.astype(object).assign(solar_irradiance_wm2="n/a"),
    "numbers stored as text": lambda f: f.astype(str),
    "humidity above 100": lambda f: f.assign(humidity_pct=180.0),
    "negative irradiance": lambda f: f.assign(solar_irradiance_wm2=-50.0),
    "temperature far out of range": lambda f: f.assign(ambient_temp_c=999.0),
    "datetime column": lambda f: f.assign(timestamp=pd.to_datetime(f.timestamp)),
    "ISO timestamps": lambda f: f.assign(timestamp=pd.to_datetime(f.timestamp).dt.strftime("%Y-%m-%dT%H:%M:%S")),
    "one unparseable timestamp": lambda f: f.astype(object).assign(timestamp=["garbage"] + f.timestamp.tolist()[1:]),
    "half the timestamps unparseable": lambda f: f.assign(timestamp=[t if i % 2 else "?" for i, t in enumerate(f.timestamp)]),
    "duplicated timestamps": lambda f: f.assign(timestamp=f.timestamp.iloc[0]),
    "rows shuffled": lambda f: f.sample(frac=1, random_state=3),
    "single row": lambda f: f.head(1),
    "non-default index": lambda f: f.set_index(np.arange(100, 100 + len(f))),
    "years outside training": lambda f: f.assign(timestamp=pd.to_datetime(f.timestamp).dt.strftime("2030-%m-%d %H:00:00")),
    "leap day": lambda f: f.assign(timestamp=pd.date_range("2016-02-29", periods=len(f), freq="h").strftime("%Y-%m-%d %H:%M:%S")),
    "blank strings everywhere": lambda f: f.astype(object).assign(ambient_temp_c="", humidity_pct=" ", solar_irradiance_wm2=""),
}


@pytest.mark.parametrize("name", MESSY)
def test_submission_scores_messy_inputs(name, monkeypatch, capsys):
    monkeypatch.setattr(submission, "_load_env_file", lambda: None)
    messy = MESSY[name](SAMPLE.head(48).copy())
    values, info = submission.predict_with_info(messy)
    assert len(values) == len(messy) and np.isfinite(values).all() and (values >= 0).all()
    assert info["provider"] == "backup"


@pytest.mark.parametrize("seed", range(20))
def test_submission_predictions_follow_input_order(seed, monkeypatch):
    monkeypatch.setattr(submission, "_load_env_file", lambda: None)
    sample = SAMPLE.head(200)
    order = np.random.default_rng(seed).permutation(len(sample))
    expected = submission.predict(sample)[order]
    np.testing.assert_allclose(submission.predict(sample.iloc[order]), expected)


@pytest.mark.parametrize("size", [1, 2, 1999, 2000, 2001, 4000, 4001, 6001])
def test_submission_chunks_large_inputs_and_keeps_order(size, monkeypatch):
    calls = []
    def post(url, payload, headers=None, attempts=5):
        calls.append(len(payload["rows"]))
        return {"predictions": [row["ambient_temp_c"] for row in payload["rows"]], "model": {"provider": "quickml", "name": "fake"}}
    monkeypatch.delenv("MODEL_PROVIDER")
    monkeypatch.setattr(submission, "_load_env_file", lambda: None)
    monkeypatch.setattr(submission, "_post", post)
    frame = pd.concat([SAMPLE] * 7, ignore_index=True).head(size)
    frame["ambient_temp_c"] = np.arange(size) / 1000  # a unique, nonnegative marker per row
    values, info = submission.predict_with_info(frame)
    np.testing.assert_allclose(values, frame.ambient_temp_c.to_numpy())
    assert calls == [min(2000, size - start) for start in range(0, size, 2000)] and info["provider"] == "api"


@pytest.mark.parametrize("failed", [0, 1, 2])
def test_submission_scores_only_the_failed_chunk_locally(failed, monkeypatch, capsys):
    calls = []
    def post(url, payload, headers=None, attempts=5):
        calls.append(len(payload["rows"]))
        if len(calls) - 1 == failed:
            raise requests.ConnectionError("down")
        return {"predictions": [1000.0] * len(payload["rows"]), "model": {"provider": "quickml", "name": "fake"}}
    monkeypatch.delenv("MODEL_PROVIDER")
    monkeypatch.setattr(submission, "_load_env_file", lambda: None)
    monkeypatch.setattr(submission, "_post", post)
    frame = pd.concat([SAMPLE] * 5, ignore_index=True).head(5000)
    values, info = submission.predict_with_info(frame)
    remote = values == 1000.0
    assert info["provider"] == "mixed" and len(values) == 5000 and np.isfinite(values).all()
    assert not remote[failed * 2000:(failed + 1) * 2000].any() and remote.sum() == 5000 - min(2000, 5000 - failed * 2000)
    assert f"rows {failed * 2000}-" in capsys.readouterr().err


# The deployed API: every 24-hour window of the sample, and a matrix of bad requests.
@pytest.mark.parametrize("start", range(0, len(SAMPLE) - 23, 24))
def test_forecast_endpoint_on_every_sample_window(start):
    window = SAMPLE.iloc[start:start + 24]
    reply = TestClient(app).post("/api/forecast", json={"rows": window.to_dict(orient="records")})
    assert reply.status_code == 200
    body = reply.json()
    rows, summary = body["rows"], body["summary"]
    demand = np.array([r["target_demand_kw"] for r in rows])
    assert [r["timestamp"] for r in rows] == window.timestamp.tolist() and np.isfinite(demand).all() and (demand >= 0).all()
    for row in rows:
        signal = dispatch_signal(row["target_demand_kw"], row["timestamp"])
        assert row["grid_mode"] == signal["grid_mode"] and row["critical_overload"] == signal["critical_overload"]
    assert summary["peak_demand_kw"] == pytest.approx(demand.max(), abs=0.01)
    assert summary["peak_timestamp"] == rows[int(np.argmax(demand))]["timestamp"]
    assert summary["energy_kwh"] == pytest.approx(demand.sum(), abs=0.05)
    assert summary["storage_hours"] == sum(r["grid_mode"] == "renewable_storage" for r in rows)
    assert summary["overload_hours"] == sum(r["critical_overload"] for r in rows)
    assert body["persisted"] is False and body["warnings"] == []


@pytest.mark.parametrize("battery,message", [
    ({"capacity_kwh": 0}, "capacity"), ({"capacity_kwh": 100001}, "capacity"), ({"initial_soc_pct": 10, "reserve_soc_pct": 20}, "reserve"),
    ({"reserve_soc_pct": -1}, "0–100%"), ({"initial_soc_pct": 101}, "0–100%"), ({"efficiency": 0}, "efficiency"),
    ({"efficiency": 1.01}, "efficiency"), ({"max_charge_kw": -1}, "power ratings"), ({"solar_capacity_kw": 100001}, "power ratings"),
    ({"max_discharge_kw": -0.5}, "power ratings"), ({"unknown_setting": 5}, ""), ({"capacity_kwh": "large"}, ""),
])
def test_forecast_refuses_impossible_battery_settings(frame, battery, message):
    reply = TestClient(app).post("/api/forecast", json={"rows": frame.to_dict(orient="records"), "battery": battery})
    assert reply.status_code == 422 and message in reply.json()["detail"]


@pytest.mark.parametrize("change,status", [
    (lambda rows: rows[:23], 422), (lambda rows: rows + rows[:1], 422), (lambda rows: rows[::-1], 422),
    (lambda rows: [dict(r, humidity_pct=101) if i == 3 else r for i, r in enumerate(rows)], 422),
    (lambda rows: [dict(r, ambient_temp_c="warm") if i == 3 else r for i, r in enumerate(rows)], 422),
    (lambda rows: [{k: v for k, v in r.items() if k != "timestamp"} for r in rows], 422),
    (lambda rows: [dict(r, timestamp=r["timestamp"].replace(" ", "T")) for r in rows], 422),
    (lambda rows: [dict(r, ambient_temp_c=None) for r in rows], 200),  # blanks are filled, with a warning
])
def test_forecast_request_matrix(frame, change, status):
    reply = TestClient(app).post("/api/forecast", json={"rows": change(frame.to_dict(orient="records"))})
    assert reply.status_code == status
    if status == 200:
        assert any("blank weather values were filled" in w for w in reply.json()["warnings"])


@pytest.mark.parametrize("body,status", [
    ({}, 422), ({"rows": "all"}, 422), ({"rows": []}, 422), ({"rows": [{}] * 10001}, 422), ({"rows": [1, 2]}, 422),
    ({"rows": [{"timestamp": "2015-06-16 15:00:00"}]}, 422), ({"rows": [{"timestamp": "nope", "ambient_temp_c": 30}]}, 422),
    ({"rows": [{"timestamp": "2015-06-16 15:00:00", "ambient_temp_c": "x", "humidity_pct": 500, "solar_irradiance_wm2": -9}]}, 200),
])
def test_batch_predict_request_matrix(body, status):
    # The API needs every input column (predict.py adds missing ones first); values themselves are repaired.
    reply = TestClient(app).post("/api/predict", json=body)
    assert reply.status_code == status
    if status == 200:
        assert len(reply.json()["predictions"]) == len(body["rows"])


def scenario_rows(frame, demand=150.0):
    return [dict(row, target_demand_kw=demand) for row in frame.to_dict(orient="records")]


@pytest.mark.parametrize("change", [
    lambda rows: rows[:23], lambda rows: [dict(r, target_demand_kw=-1) for r in rows],
    lambda rows: [dict(r, target_demand_kw="high") for r in rows], lambda rows: [{k: v for k, v in r.items() if k != "target_demand_kw"} for r in rows],
    lambda rows: [dict(r, humidity_pct=-5) for r in rows], lambda rows: rows[::-1],
])
def test_scenarios_refuse_invalid_windows(frame, change):
    assert TestClient(app).post("/api/scenarios", json={"rows": change(scenario_rows(frame))}).status_code == 422


@pytest.mark.parametrize("demand", [0.0, 100.0, 160.0, 160.5, 199.0, 200.0, 350.0])
def test_scenarios_for_flat_demand_levels(frame, demand):
    reply = TestClient(app).post("/api/scenarios", json={"rows": scenario_rows(frame, demand)})
    assert reply.status_code == 200
    rec = reply.json()["recommendation"]
    storage_hours = sum(dispatch_signal(demand, t)["grid_mode"] == "renewable_storage" for t in frame.timestamp)
    if storage_hours == 0 or demand == 0:
        assert rec["current_shortfall_hours"] == 0 and rec["required_discharge_kw"] <= demand
    assert rec["capacity_kwh"] is None or rec["capacity_kwh"] >= 1


@pytest.mark.parametrize("csv_text,status", [
    ("", 422), ("timestamp,ambient_temp_c\n2015-06-16 15:00:00,30", 422), ("\xff\xfe", 422),
    ("not,a,csv,\"unterminated", 422),
    ("timestamp,ambient_temp_c,humidity_pct,solar_irradiance_wm2\n" + "\n".join(
        f"2015-06-16 {h:02d}:00:00,30,50,0" for h in range(10)), 422),
])
def test_csv_upload_request_matrix(csv_text, status):
    reply = TestClient(app).post("/api/inputs/csv", content=csv_text.encode("latin-1"), headers={"Content-Type": "text/csv"})
    assert reply.status_code == status


@pytest.mark.parametrize("path", ["/", "/dashboard", "/favicon.ico", "/api/health", "/api/model", "/api/sample", "/api/template.csv", "/api/runs"])
def test_every_page_and_read_endpoint_is_served_with_security_headers(path):
    reply = TestClient(app).get(path)
    assert reply.status_code == 200
    assert reply.headers["X-Content-Type-Options"] == "nosniff" and "frame-ancestors 'none'" in reply.headers["Content-Security-Policy"]
    if path.startswith("/api/"):
        assert reply.headers["Cache-Control"] == "no-store"
