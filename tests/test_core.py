from collections import defaultdict, deque
import json
import math
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
    with pytest.raises(ProviderError,match="incomplete"): forecast(frame)
    monkeypatch.setenv("QUICKML_ENDPOINT_URL","http://insecure.test/model")
    with pytest.raises(ProviderError,match="HTTPS"): forecast(frame)


class FakeReply:
    def __init__(self,status,body): self.status_code,self.body=status,body
    def json(self): return self.body


def configure_refresh(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER","quickml")
    for key in ["QUICKML_CLIENT_ID","QUICKML_CLIENT_SECRET","QUICKML_REFRESH_TOKEN","QUICKML_ENDPOINT_KEY"]: monkeypatch.setenv(key,"test-only")


def test_quickml_refreshes_token_batches_and_reads_result(frame,monkeypatch):
    configure_refresh(monkeypatch)
    monkeypatch.setenv("QUICKML_BATCH_SIZE","10")
    calls={"token":0,"predict":[]}
    def post(url,**kwargs):
        if url.endswith("/oauth/v2/token"):
            assert kwargs["data"]["grant_type"]=="refresh_token"
            calls["token"]+=1
            return FakeReply(200,{"access_token":"fresh","expires_in":3600})
        assert url.startswith("https://api.catalyst.zoho.in/quickml/")
        assert kwargs["headers"]["Authorization"]=="Zoho-oauthtoken fresh"
        rows=kwargs["json"]["data"]
        calls["predict"].append(len(rows))
        return FakeReply(200,{"data":{"result":[r["hour"]*10 for r in rows],"status":"success"}})
    monkeypatch.setattr("engine.inference.requests.post",post)
    result,info=forecast(frame)
    assert calls=={"token":1,"predict":[10,10,4]}
    assert result.tolist()==(make_features(frame)["hour"]*10).tolist() and info["provider"]=="quickml"


def test_quickml_renews_rejected_token_once(frame,monkeypatch):
    configure_refresh(monkeypatch)
    replies=iter([FakeReply(400,{"code":"INVALID_TICKET"}),FakeReply(200,{"result":[150]*24})])
    tokens=[]
    def post(url,**kwargs):
        if url.endswith("/oauth/v2/token"):
            tokens.append(1); return FakeReply(200,{"access_token":f"t{len(tokens)}"})
        return next(replies)
    monkeypatch.setattr("engine.inference.requests.post",post)
    assert (forecast(frame)[0]==150).all() and len(tokens)==2


def test_appsail_platform_credential_authorizes_quickml(frame,monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER","quickml")
    monkeypatch.setenv("QUICKML_ENDPOINT_KEY","test-only")
    seen=[]
    def post(url,**kwargs):
        seen.append(kwargs["headers"]["Authorization"])
        return FakeReply(200,{"result":[120]*len(kwargs["json"]["data"])})
    monkeypatch.setattr("engine.inference.requests.post",post)
    client=TestClient(app)
    rows=frame.to_dict(orient="records")
    backup=client.post("/api/predict",json={"rows":rows})  # no credential anywhere: labelled backup, not an error
    assert backup.status_code==200 and backup.json()["model"]["provider"]=="local-backup" and "warning" in backup.json()
    response=client.post("/api/predict",json={"rows":rows},headers={"X-ZC-Admin-Cred-Token":"platform","X-ZC-Admin-Cred-Type":"ticket"})
    assert response.status_code==200 and response.json()["predictions"]==[120]*24
    response=client.post("/api/forecast",json={"rows":rows},headers={"X-ZC-Admin-Cred-Token":"platform"})
    assert response.status_code==200
    assert seen==["Zoho-ticket platform","Zoho-oauthtoken platform"]


def test_predict_api_serves_batches_and_rate_limits(frame):
    client=TestClient(app)
    rows=pd.read_csv(ROOT/"sample_test_input.csv").head(200).to_dict(orient="records")
    response=client.post("/api/predict",json={"rows":rows})
    assert response.status_code==200, response.text
    body=response.json()
    assert len(body["predictions"])==200 and body["model"]["provider"]=="local"
    assert client.post("/api/predict",json={"rows":[]}).status_code==422
    assert client.post("/api/predict",json={"rows":[{"timestamp":"bad","ambient_temp_c":1,"humidity_pct":1,"solar_irradiance_wm2":1}]}).status_code==422
    statuses=[client.post("/api/predict",json={"rows":rows[:1]}).status_code for _ in range(40)]
    assert 429 in statuses


def test_predict_uses_gridwise_api_without_credentials(frame,monkeypatch):
    import predict as submission
    monkeypatch.delenv("MODEL_PROVIDER")
    monkeypatch.setattr(submission,"_load_env_file",lambda: None)
    client=TestClient(app)
    def post(url,json=None,**kwargs):
        assert url.endswith("/api/predict") and isinstance(json["rows"][0]["timestamp"],str)
        with monkeypatch.context() as inner:
            inner.setenv("MODEL_PROVIDER","local")
            reply=client.post("/api/predict",json=json)
        return FakeReply(reply.status_code,reply.json())
    FakeReply.raise_for_status=lambda self: None
    monkeypatch.setattr("predict.requests.post",post)
    timestamps=frame.assign(timestamp=pd.to_datetime(frame.timestamp))  # datetime dtype must not become epochs
    result,info=submission.predict_with_info(timestamps)
    assert info["provider"]=="api" and len(result)==24 and np.isfinite(result).all()


def test_predict_falls_back_to_local_with_warning(frame,monkeypatch,capsys):
    import predict as submission
    monkeypatch.delenv("MODEL_PROVIDER")
    monkeypatch.setattr(submission,"_load_env_file",lambda: None)
    monkeypatch.setattr("predict.time.sleep",lambda s: None)
    def fail(*args,**kwargs): raise requests.ConnectionError("offline")
    monkeypatch.setattr("predict.requests.post",fail)
    result,info=submission.predict_with_info(frame)
    assert info["provider"]=="backup" and len(result)==24 and np.isfinite(result).all()
    assert "WARNING" in capsys.readouterr().err


def test_standalone_predict_features_match_server(frame):
    import predict as submission
    pd.testing.assert_frame_equal(submission._features(frame),make_features(frame))


def test_lenient_batch_scoring_repairs_inputs_and_never_sends_blanks(monkeypatch):
    rows=pd.read_csv(ROOT/"sample_test_input.csv").head(6)
    rows.loc[1,"ambient_temp_c"]=None
    rows.loc[2,"humidity_pct"]=140
    rows.loc[3,"solar_irradiance_wm2"]=-5
    rows.loc[4,"timestamp"]="not a time"
    rows.loc[5,"timestamp"]="2015-06-16T20:00:00"
    monkeypatch.setenv("MODEL_PROVIDER","quickml")
    monkeypatch.setenv("QUICKML_ENDPOINT_KEY","test-only")
    monkeypatch.setenv("QUICKML_ACCESS_TOKEN","test-only")
    sent=[]
    def post(url,**kwargs):
        sent.extend(kwargs["json"]["data"])
        return FakeReply(200,{"result":[100]*len(kwargs["json"]["data"])})
    monkeypatch.setattr("engine.inference.requests.post",post)
    response=TestClient(app).post("/api/predict",json={"rows":json.loads(rows.to_json(orient="records"))})
    assert response.status_code==200, response.text
    assert all(v is not None and math.isfinite(v) for record in sent for v in record.values())
    assert sent[1]["ambient_temp_c"]==pytest.approx((rows.ambient_temp_c[0]+rows.ambient_temp_c[2])/2)
    assert sent[2]["humidity_pct"]==100 and sent[3]["solar_irradiance_wm2"]==0
    assert [r["hour"] for r in sent]==[15,16,17,18,19,20]


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


APPSAIL_HEADERS={"X-ZC-Admin-Cred-Token":"platform","X-ZC-Admin-Cred-Type":"ticket","X-ZC-ProjectId":"42","X-ZC-Project-Key":"key","X-ZC-Environment":"Development"}


def test_controller_events_follow_the_rules():
    from engine.datastore import controller_events
    rows=[{"timestamp":f"2015-06-16 {h:02d}:00:00","grid_mode":m,"reason":"r","critical_overload":o,"target_demand_kw":d}
          for h,m,o,d in [(16,"main_grid",False,150),(17,"renewable_storage",True,205),(18,"renewable_storage",False,150),(23,"main_grid",False,120)]]
    events=controller_events(rows)
    assert [(e["event_hour"][11:13],e["event_type"]) for e in events]==[("16","switch"),("17","switch"),("17","alert"),("18","clear"),("23","switch")]


def test_forecast_runs_are_persisted_to_data_store(frame,monkeypatch):
    monkeypatch.setattr("app.run_in_background",lambda target,*args: target(*args))
    calls=[]
    def post(url,json=None,headers=None,**kwargs):
        calls.append((url.rsplit("/",2)[-2],json,headers))
        return FakeReply(200,{"data":[{"ROWID":"9001"}]})
    FakeReply.raise_for_status=lambda self: None
    monkeypatch.setattr("engine.datastore.requests.post",post)
    response=TestClient(app).post("/api/forecast",json={"rows":frame.to_dict(orient="records"),"source":"Uploaded: test.csv"},headers=APPSAIL_HEADERS)
    assert response.status_code==200 and response.json()["persisted"] is True
    tables=[c[0] for c in calls]
    assert tables==["ForecastRuns","Telemetry","AlertLog"]
    run,telemetry,alerts=(c[1] for c in calls)
    assert run[0]["source"]=="Uploaded: test.csv" and run[0]["window_start"]==frame.timestamp[0]
    assert len(telemetry)==24 and telemetry[0]["run_id"]=="9001" and telemetry[0]["ambient_temp_c"]==frame.ambient_temp_c[0]
    assert all(t["predicted_kw"]==round(t["predicted_kw"],2) for t in telemetry)  # rounded, not truncated by Data Store
    assert all(c[2]["Authorization"]=="Zoho-ticket platform" and c[2]["PROJECT_ID"]=="key" for c in calls)
    assert TestClient(app).post("/api/forecast",json={"rows":frame.to_dict(orient="records")}).json()["persisted"] is False


def test_recent_runs_endpoint(monkeypatch):
    client=TestClient(app)
    assert client.get("/api/runs").json()=={"enabled":False,"runs":[],"events":[]}
    queries=[]
    def post(url,json=None,**kwargs):
        queries.append(json["query"])
        table="ForecastRuns" if "FROM ForecastRuns" in json["query"] else "AlertLog"
        return FakeReply(200,{"status":"success","data":[{table:{"ROWID":"1","message":"m"}}]})
    FakeReply.raise_for_status=lambda self: None
    monkeypatch.setattr("engine.datastore.requests.post",post)
    body=client.get("/api/runs",headers=APPSAIL_HEADERS).json()
    assert body["enabled"] and body["runs"]==[{"ROWID":"1","message":"m"}] and body["events"]==[{"ROWID":"1","message":"m"}]
    assert all("ORDER BY CREATEDTIME DESC" in q for q in queries)  # ROWIDs are not in insertion order


def test_dashboard_survives_quickml_outage_with_labelled_backup(frame,monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER","quickml")
    monkeypatch.setenv("QUICKML_ENDPOINT_KEY","test-only")
    monkeypatch.setenv("QUICKML_ACCESS_TOKEN","test-only")
    def down(*args,**kwargs): raise requests.ConnectionError("QuickML down")
    monkeypatch.setattr("engine.inference.requests.post",down)
    body=TestClient(app).post("/api/forecast",json={"rows":frame.to_dict(orient="records")}).json()
    assert body["model"]["provider"]=="local-backup" and len(body["rows"])==24
    assert any("QuickML was unavailable" in w for w in body["warnings"])
    with pytest.raises(ProviderError): forecast(frame)  # the engine itself stays strict


def test_forecast_rate_limit_and_temperature_range(frame,monkeypatch):
    monkeypatch.setattr("app.RATE_LIMITS",{"predict":30,"forecast":2})
    client=TestClient(app)
    rows=frame.to_dict(orient="records")
    assert [client.post("/api/forecast",json={"rows":rows}).status_code for _ in range(3)]==[200,200,429]
    monkeypatch.setattr("app._predict_calls",defaultdict(deque))
    hot=frame.assign(ambient_temp_c=frame.ambient_temp_c.where(frame.index!=3,500)).to_dict(orient="records")
    response=client.post("/api/forecast",json={"rows":hot})
    assert response.status_code==422 and "Temperature" in response.json()["detail"]
    clipped=client.post("/api/predict",json={"rows":hot}).json()  # batch scoring clips instead of rejecting
    assert len(clipped["predictions"])==24


def test_unexpected_errors_return_generic_json(frame,monkeypatch):
    def explode(*args,**kwargs): raise RuntimeError("internal detail that must not leak")
    monkeypatch.setattr("app.build_forecast",explode)
    response=TestClient(app,raise_server_exceptions=False).post("/api/forecast",json={"rows":frame.to_dict(orient="records")})
    assert response.status_code==500 and response.json()=={"detail":"Unexpected server error. Please try again."}


def test_standalone_predict_never_crashes_on_messy_inputs(monkeypatch,capsys):
    import predict as submission
    monkeypatch.setattr(submission,"_load_env_file",lambda: None)
    sample=pd.read_csv(ROOT/"sample_test_input.csv").head(30)
    messy=sample.rename(columns={"timestamp":" Timestamp ","ambient_temp_c":"AMBIENT_TEMP_C"}).drop(columns=["humidity_pct"])
    messy.loc[2,"AMBIENT_TEMP_C"]="hot"
    values,info=submission.predict_with_info(messy)
    assert len(values)==30 and np.isfinite(values).all() and info["provider"]=="backup"
    assert "missing columns humidity_pct" in capsys.readouterr().err
    garbage=pd.DataFrame({"timestamp":["nope"]*5,"ambient_temp_c":[20]*5,"humidity_pct":[50]*5,"solar_irradiance_wm2":[0]*5})
    values,info=submission.predict_with_info(garbage)
    assert info["provider"]=="constant" and np.allclose(values,submission.TRAINING_MEAN_KW)


def test_backup_model_is_sane_on_training_data():
    import predict as submission
    raw=pd.read_csv(ROOT/"train_telemetry.csv")
    values,_=submission._via_backup(raw)
    assert np.sqrt(np.mean((raw.target_demand_kw.to_numpy()-values)**2))<15


def configure_ensemble(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER","quickml")
    monkeypatch.setenv("QUICKML_ACCESS_TOKEN","test-only")
    monkeypatch.setenv("QUICKML_ENDPOINT_KEY","cat-key")
    monkeypatch.setenv("QUICKML_LINEAR_ENDPOINT_KEY","linear-key")
    monkeypatch.setenv("QUICKML_MODEL_NAME","CatBoost")
    monkeypatch.setenv("QUICKML_LINEAR_MODEL_NAME","Linear")


def test_quickml_ensemble_averages_members_with_their_own_features(frame,monkeypatch):
    configure_ensemble(monkeypatch)
    seen={}
    def post(url,**kwargs):
        key,rows=kwargs["headers"]["X-QUICKML-ENDPOINT-KEY"],kwargs["json"]["data"]
        seen[key]=set(rows[0])
        return FakeReply(200,{"result":[100.0 if key=="cat-key" else 140.0]*len(rows)})
    monkeypatch.setattr("engine.inference.requests.post",post)
    result,info=forecast(frame)
    assert np.allclose(result,120) and info["name"]=="QuickML ensemble: CatBoost + Linear"
    assert "hour" in seen["cat-key"] and "cooling_hour_23" in seen["linear-key"] and "hour" not in seen["linear-key"]


def test_quickml_ensemble_survives_one_member_failing(frame,monkeypatch):
    configure_ensemble(monkeypatch)
    def post(url,**kwargs):
        if kwargs["headers"]["X-QUICKML-ENDPOINT-KEY"]=="linear-key":
            return FakeReply(500,{"code":"DOWN"})
        return FakeReply(200,{"result":[130.0]*len(kwargs["json"]["data"])})
    monkeypatch.setattr("engine.inference.requests.post",post)
    result,info=forecast(frame)
    assert np.allclose(result,130) and info["name"]=="CatBoost"


def test_quickml_ensemble_all_down_uses_labelled_backup(frame,monkeypatch):
    from engine.inference import forecast_with_backup
    configure_ensemble(monkeypatch)
    monkeypatch.setattr("engine.inference.requests.post",lambda url,**kwargs: FakeReply(503,{}))
    (result,info),reason=forecast_with_backup(frame)
    assert info["provider"]=="local-backup" and "HTTP 503" in reason and len(result)==24


def test_linear_member_never_extrapolates_temperature(frame):
    from engine.inference import LINEAR_TEMP_RANGE, linear_payload
    hot=frame.assign(ambient_temp_c=55.0)
    assert linear_payload(hot)["ambient_temp_c"].max()==LINEAR_TEMP_RANGE[1]
    assert linear_payload(frame)["ambient_temp_c"].tolist()==frame["ambient_temp_c"].tolist()


def test_linear_features_are_full_rank_and_match_submission():
    import predict as submission
    from engine.features import make_linear_features
    raw=pd.read_csv(ROOT/"train_telemetry.csv")
    features=make_linear_features(raw)
    design=np.c_[np.ones(len(features)),features.to_numpy()]
    assert features.shape[1]==52 and np.linalg.matrix_rank(design)==53 and not features.isna().any().any()
    pd.testing.assert_frame_equal(submission._linear_features(raw),features)
    exported=pd.read_csv(ROOT/"reports/quickml_linear_features.csv")
    assert list(exported.columns)==[*features.columns,"target_demand_kw"] and len(exported)==len(raw)


def test_backup_is_the_linear_member_and_validates_chronologically():
    import predict as submission
    from engine.features import make_linear_features
    raw=pd.read_csv(ROOT/"train_telemetry.csv").sort_values("timestamp").reset_index(drop=True)
    design=np.c_[np.ones(len(raw)),make_linear_features(raw).to_numpy()]
    y=raw.target_demand_kw.to_numpy()
    refit,*_=np.linalg.lstsq(design,y,rcond=None)
    assert np.allclose(refit,submission.BACKUP_COEFFICIENTS,atol=5e-4)
    early,*_=np.linalg.lstsq(design[:3200],y[:3200],rcond=None)  # fit on earlier hours, score the locked last 800
    assert np.sqrt(np.mean((design[3200:]@early-y[3200:])**2))<9.5


def test_direct_route_averages_both_endpoints(monkeypatch):
    import predict as submission
    monkeypatch.setattr(submission,"_load_env_file",lambda: None)
    monkeypatch.setenv("MODEL_PROVIDER","quickml")
    monkeypatch.setenv("QUICKML_ACCESS_TOKEN","test-only")
    monkeypatch.setenv("QUICKML_ENDPOINT_KEY","cat-key")
    monkeypatch.setenv("QUICKML_LINEAR_ENDPOINT_KEY","linear-key")
    def post(url,payload,headers=None,attempts=5):
        return {"result":[100.0 if headers["X-QUICKML-ENDPOINT-KEY"]=="cat-key" else 120.0]*len(payload["data"])}
    monkeypatch.setattr(submission,"_post",post)
    sample=pd.read_csv(ROOT/"sample_test_input.csv").head(450)
    values,info=submission.predict_with_info(sample)
    assert np.allclose(values,110) and info["provider"]=="quickml" and "2 averaged" in info["name"]


@pytest.mark.parametrize("hour", range(24))
@pytest.mark.parametrize("demand", [0, 120, 159.99, 160, 160.01, 199.99, 200, 260])
def test_controller_matches_the_rules_for_every_hour(hour, demand):
    # Independent oracle written straight from the problem statement.
    signal=dispatch_signal(demand, f"2015-06-20 {hour:02d}:00:00")
    expected="renewable_storage" if demand>160 or 18<=hour<=22 else "main_grid"
    assert signal["grid_mode"]==expected and signal["critical_overload"]==(demand>=200)


def forecast_rows(frame):
    return TestClient(app).post("/api/forecast",json={"rows":frame.to_dict(orient="records")}).json()["rows"]


def test_scenarios_recommend_the_smallest_battery_that_covers_storage(frame):
    from dataclasses import replace
    rows=forecast_rows(frame)
    for row in rows:
        if row["grid_mode"]=="renewable_storage": row["target_demand_kw"]=230.0
    reply=TestClient(app).post("/api/scenarios",json={"rows":rows,"battery":{"capacity_kwh":400}})
    assert reply.status_code==200
    body=reply.json(); rec=body["recommendation"]
    assert [s["capacity_kwh"] for s in body["scenarios"]]==[100,200,300,400,600,800]
    assert rec["raise_discharge"] and rec["discharge_kw"]==230 and rec["capacity_kwh"]>0
    base=replace(BatteryConfig(),capacity_kwh=400,max_discharge_kw=rec["discharge_kw"])
    covers=lambda kwh: not any(r["storage_shortfall"] for r in simulate(frame,[r["target_demand_kw"] for r in rows],replace(base,capacity_kwh=kwh)))
    assert covers(rec["capacity_kwh"]) and not covers(rec["capacity_kwh"]-1)


def test_scenarios_reject_windows_without_predictions(frame):
    rows=frame.to_dict(orient="records")
    reply=TestClient(app).post("/api/scenarios",json={"rows":rows})
    assert reply.status_code==422 and "target_demand_kw" in reply.json()["detail"]
    reply=TestClient(app).post("/api/scenarios",json={"rows":rows[:5]})
    assert reply.status_code==422


def test_replay_needs_the_deployed_data_store(frame):
    assert TestClient(app).post("/api/runs/123/replay",json={}).status_code==404


def test_replay_rebuilds_a_saved_run_without_calling_the_model(frame,monkeypatch):
    saved=[{"forecast_hour":r["timestamp"],"ambient_temp_c":r["ambient_temp_c"],"humidity_pct":r["humidity_pct"],
            "solar_irradiance_wm2":r["solar_irradiance_wm2"],"predicted_kw":150.0+i} for i,r in enumerate(frame.to_dict(orient="records"))]
    monkeypatch.setattr("engine.datastore.run_rows",lambda context,run_id:({"ROWID":run_id,"source":"Dashboard","model":"quickml / CatBoost"},saved))
    monkeypatch.setattr("engine.inference.forecast",lambda *a,**k: pytest.fail("replay must not call the model"))
    reply=TestClient(app).post("/api/runs/102738000000099001/replay",json={"battery":{"capacity_kwh":500}},headers=APPSAIL_HEADERS)
    body=reply.json()
    assert reply.status_code==200 and body["model"]["provider"]=="datastore" and body["replayed_run"]=="102738000000099001"
    assert [r["target_demand_kw"] for r in body["rows"]]==[150.0+i for i in range(24)] and body["battery"]["capacity_kwh"]==500
    assert all(r["grid_mode"]==dispatch_signal(r["target_demand_kw"],r["timestamp"])["grid_mode"] for r in body["rows"])
    assert body["persisted"] is False


def test_replay_rejects_bad_ids_and_incomplete_runs(frame,monkeypatch):
    from engine import datastore
    with pytest.raises(ValueError):
        datastore.run_rows({"project":"1","headers":{}},"1 OR 1=1")
    client=TestClient(app)
    assert client.post("/api/runs/1%20OR%201=1/replay",json={},headers=APPSAIL_HEADERS).status_code==422
    monkeypatch.setattr("engine.datastore.run_rows",lambda context,run_id:(None,[]))
    assert client.post("/api/runs/42/replay",json={},headers=APPSAIL_HEADERS).status_code==404
