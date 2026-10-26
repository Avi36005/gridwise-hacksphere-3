import math
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient

from app import app
from engine.controller import BatteryConfig, dispatch_signal, simulate
from engine.features import make_features, validate_inputs
from engine.inference import ProviderError, checked_predictions, forecast

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def frame():
    return pd.read_csv(ROOT / "sample_test_input.csv").head(24)


@pytest.fixture(autouse=True)
def local_provider(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "local")


@pytest.mark.parametrize("hour,demand,mode,critical", [
    (17,160,"main_grid",False),(17,160.01,"renewable_storage",False),
    (18,100,"renewable_storage",False),(22,100,"renewable_storage",False),
    (23,100,"main_grid",False),(0,199.99,"renewable_storage",False),
    (0,200,"renewable_storage",True),(23,210,"renewable_storage",True),
])
def test_rule_boundaries(hour,demand,mode,critical):
    signal=dispatch_signal(demand, f"2025-01-01 {hour:02d}:00:00")
    assert signal["grid_mode"] == mode
    assert signal["critical_overload"] is critical


def test_battery_conserves_energy_and_reserve(frame):
    config=BatteryConfig(capacity_kwh=100,initial_soc_pct=21,reserve_soc_pct=20,solar_capacity_kw=0)
    frame["solar_irradiance_wm2"]=0
    result=simulate(frame,np.full(24,250.0),config)
    assert all(20 <= row["battery_soc_pct"] <= 100 for row in result)
    assert result[0]["battery_discharge_kw"] == pytest.approx(0.95)
    assert sum(row["battery_discharge_kw"] for row in result) == pytest.approx(0.95)
    assert all(row["grid_supply_kw"] + row["battery_discharge_kw"] == pytest.approx(250,abs=.002) for row in result)
    assert all(row["grid_mode"] == "renewable_storage" and row["storage_shortfall"] for row in result)


def test_battery_charges_without_exceeding_capacity(frame):
    frame["timestamp"]=pd.date_range("2025-01-01",periods=24,freq="h").strftime("%Y-%m-%d %H:%M:%S")
    frame["solar_irradiance_wm2"]=1000
    result=simulate(frame,np.full(24,100.0),BatteryConfig(capacity_kwh=100,initial_soc_pct=90))
    assert result[0]["battery_soc_pct"] == 100
    assert result[0]["battery_charge_kw"] == pytest.approx(10/.95,abs=.001)
    assert all(row["battery_soc_pct"] == 100 for row in result)


def test_features_are_row_local(frame):
    full=make_features(frame)
    isolated=pd.concat([make_features(frame.iloc[[i]]) for i in range(len(frame))])
    pd.testing.assert_frame_equal(full,isolated)
    assert not any("target" in key or "lag" in key for key in full)


@pytest.mark.parametrize("column,value",[("humidity_pct",101),("solar_irradiance_wm2",-1),("ambient_temp_c",float("inf")),("ambient_temp_c","broken"),("timestamp","2025-02-30 00:00:00")])
def test_invalid_inputs_rejected(frame,column,value):
    frame=frame.astype(object)
    frame.loc[0,column]=value
    with pytest.raises(ValueError): validate_inputs(frame)


def test_window_must_be_exact_and_consecutive(frame):
    with pytest.raises(ValueError): validate_inputs(frame.head(23),window=True)
    with pytest.raises(ValueError): validate_inputs(frame.iloc[::-1],window=True)
    frame.loc[1,"timestamp"]=frame.loc[0,"timestamp"]
    with pytest.raises(ValueError): validate_inputs(frame,window=True)


def test_predictions_missing_weather_and_input_order(frame):
    expected,_=forecast(frame)
    shuffled=frame.sample(frac=1,random_state=42)
    actual,_=forecast(shuffled)
    assert actual == pytest.approx(expected[shuffled.index])
    frame.loc[0,"ambient_temp_c"]=np.nan
    actual,_=forecast(frame)
    assert len(actual)==24 and np.isfinite(actual).all()


@pytest.mark.parametrize("values",[[1,2],[1,np.nan,3],[[1,2],[3,4],[5,6]],[-1,2,3]])
def test_malformed_model_output_rejected(values):
    with pytest.raises(ProviderError): checked_predictions(values,3)


def configure_quickml(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER","quickml")
    monkeypatch.setenv("QUICKML_ENDPOINT_URL","https://example.test/model")
    for key in ["QUICKML_ACCESS_TOKEN","QUICKML_ENDPOINT_KEY","QUICKML_ORG_ID"]: monkeypatch.setenv(key,"test-only")


def test_quickml_failure_never_falls_back(frame,monkeypatch):
    configure_quickml(monkeypatch)
    def fail(*args,**kwargs): raise requests.Timeout("test")
    monkeypatch.setattr("engine.inference.requests.post",fail)
    with pytest.raises(ProviderError,match="could not be reached"): forecast(frame)


def test_quickml_adapter_headers_features_and_output(frame,monkeypatch):
    configure_quickml(monkeypatch)
    class Reply:
        status_code=200
        def json(self): return {"predictions":[180]*24}
    def post(url,**kwargs):
        assert kwargs["headers"]["Authorization"] == "Zoho-oauthtoken test-only"
        assert len(kwargs["json"]["data"]) == 24
        assert "hour_sin" in kwargs["json"]["data"][0]
        assert "target_demand_kw" not in kwargs["json"]["data"][0]
        assert kwargs["allow_redirects"] is False
        return Reply()
    monkeypatch.setattr("engine.inference.requests.post",post)
    result,info=forecast(frame)
    assert (result==180).all() and info["provider"] == "quickml"


def test_quickml_configuration_cannot_be_bypassed(frame,monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER","quickml")
    monkeypatch.delenv("QUICKML_ENDPOINT_URL",raising=False)
    with pytest.raises(ProviderError): forecast(frame)


def test_forecast_api_and_csv_upload(frame):
    client=TestClient(app)
    response=client.post("/api/forecast",json={"rows":frame.to_dict(orient="records")})
    assert response.status_code==200, response.text
    result=response.json()
    assert len(result["rows"])==24
    assert result["summary"]["energy_kwh"]==pytest.approx(sum(r["target_demand_kw"] for r in result["rows"]),abs=.01)
    response=client.post("/api/inputs/csv",content=frame.to_csv(index=False),headers={"Content-Type":"text/csv"})
    assert response.status_code==200 and len(response.json()["rows"])==24
    response=client.post("/api/forecast",json={"rows":frame.head(23).to_dict(orient="records")})
    assert response.status_code==422
    response=client.post("/api/forecast",json={"rows":frame.to_dict(orient="records"),"battery":{"initial_soc_pct":10,"reserve_soc_pct":20}})
    assert response.status_code==422
    assert client.post("/api/inputs/csv",content="not,a,valid,csv\n1,2,3,4").status_code==422
    assert client.post("/api/inputs/csv",content=b"x"*(1024*1024+1)).status_code==413


def test_submission_cli_preserves_timestamps_from_another_directory(tmp_path):
    destination=tmp_path/"predictions.csv"
    result=subprocess.run([sys.executable,str(ROOT/"predict.py"),"--input",str(ROOT/"sample_test_input.csv"),"--output",str(destination)],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0, result.stderr
    output=pd.read_csv(destination)
    source=pd.read_csv(ROOT/"sample_test_input.csv")
    assert list(output.columns)==["timestamp","target_demand_kw"]
    assert output.timestamp.tolist()==source.timestamp.tolist()
    assert len(output)==1000 and np.isfinite(output.target_demand_kw).all()


def test_static_dashboard_available():
    client=TestClient(app)
    assert "Forecast demand." in client.get("/").text
    assert "view-forecast" in client.get("/dashboard").text
    assert client.get("/static/app.js").status_code==200
    assert client.get("/api/health").json()["status"]=="ready"
