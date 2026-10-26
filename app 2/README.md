# Gridwise — HackSphere 3.0

A working local energy-forecasting app with a trained regression model, 24-hour dashboard, CSV import/export, required dispatch rules, and an adjustable battery simulation.

## Run locally

On this Mac, double-click **start.command**. It opens the dashboard at **http://127.0.0.1:8000**. Keep its Terminal window open while using the app; press Control-C to stop it. The project has a local environment linked for immediate use on this computer. That environment is excluded from the portable ZIP.

On a new computer, use Python **3.12** (the tested version is 3.12.14):

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

The trained model is included. On macOS, LightGBM training requires OpenMP (`brew install libomp`). The current selected Extra Trees model does not require LightGBM at inference time. The launcher can bootstrap its own environment when the included local link is absent.

## What works

- Responsive dashboard with demand curves, exact switching rules, independent overload alerts, a 24-hour dispatch timeline, and an hourly table.
- CSV import with a choice of any consecutive 24-hour window; per-hour editing and a manual scenario form.
- Training-fitted imputation for missing weather values in the local model. Invalid numeric values, malformed timestamps, negative solar irradiance, and humidity outside 0–100% are rejected.
- CSV prediction entry point with the original `predict(input_df)` interface and working `--input` / `--output` arguments.
- Battery simulation with capacity, initial charge, reserve, PV capacity, and power limits. Battery reserve constraints do not alter the required control signal; separate grid support covers a simulated shortfall.
- Local model comparison and holdout scores shown in the app.
- A QuickML adapter with server-only credentials and explicit error reporting. Live QuickML integration has not been verified because no endpoint is configured.

The sample view uses **2015 historical data**, not live readings. Uploaded inputs and forecast history are not persisted. The app emits planning signals and does not control physical equipment.

## Model results

| Candidate | Mean chronological validation RMSE |
| --- | ---: |
| Extra Trees — selected | 11.80 kW |
| Histogram Gradient Boosting | 12.22 kW |
| LightGBM, 15 leaves | 12.50 kW |
| LightGBM, 31 leaves | 12.69 kW |
| Ridge regression | 28.60 kW |

The selected model's untouched final holdout RMSE is approximately **13.69 kW**, versus **45.85 kW** for the starter formula. Exact values, parameters, date ranges, dependency versions, and training-file fingerprint are in `models/metadata.json`.

Validation uses the first 3,200 labeled rows for three expanding chronological folds. The winner is chosen by mean validation RMSE, then assessed once on the last 800 rows. The selected model is finally refitted on all 4,000 rows for inference. The 1,000-row sample file has no target labels and is never used for scoring or model selection. These are local benchmark results, not hidden-test or QuickML scores. The holdout is longer than the app's 24-hour window and tests later dates; no target-lag features or future target values are used.

Features include temperature, humidity, solar irradiance, hour, weekday, weekend, cyclical hour/week/year encodings, and heating/cooling degrees. They are generated identically for training and prediction. The model was trained on a limited historical period; manually entered scenarios far outside its weather/date patterns may be unreliable.

## Retrain, predict, and test

```sh
python train.py
python predict.py --input sample_test_input.csv --output predictions.csv
python -m pip install -r requirements-dev.txt
python -m pytest -q
python test_eval.py
```

Training writes the selected model, metadata, holdout predictions, and an engineered training CSV for QuickML. Restart the app after retraining because it caches the model.

Output CSV columns are exactly `timestamp,target_demand_kw`, with one result per input row and input ordering preserved. Unlike the starter, remote failures never return dummy predictions.

## Switching rules

- Demand **> 160 kW OR hour 18–22 inclusive**: `renewable_storage`.
- Otherwise: `main_grid`.
- Demand **>= 200 kW**: critical overload, independent of grid mode.

Hourly timestamps are interpreted as local grid time, without converting time zones. Dashboard forecasts require 24 ordered consecutive hours. The standalone prediction function accepts arbitrary row order and batches of up to 10,000 rows.

## Battery assumptions

Defaults: 1,200 kWh, 80% initial charge, 20% reserve, 150 kW PV, 150 kW max charge, 200 kW max discharge, 95% efficiency in each direction. Each row represents one hour. PV output is capped at its assumed capacity. In storage mode, PV serves the load first, battery discharge covers the remaining load within its limits, and grid support covers any deficit. In main-grid mode, grid supply serves the load and PV charges the battery. Excess PV beyond charge/capacity limits is curtailed. These are illustrative assumptions, not specifications supplied by the organizers.

## Architecture

```text
Browser (HTML/CSS/JavaScript)
  → FastAPI validation
  → shared feature engineering
  → local model OR published QuickML endpoint
  → switching controller + separate battery simulation
  → dashboard + CSV export
```

`engine/features.py` owns feature construction and validation; `engine/inference.py` owns provider selection; `engine/controller.py` owns dispatch and simulation. `app.py` serves the API and static dashboard together, avoiding separate frontend infrastructure.

No JavaScript package install or frontend build is required. `requirements.lock.txt` captures the exact full environment used for validation; `requirements.txt` pins the main runtime packages.

## QuickML and Catalyst

See **docs/QUICKML.md** for the integration steps. LightGBM is explicitly listed in QuickML's supported regression algorithms. The local Extra Trees artifact is not a QuickML deployment.

Before submission, train and publish a model in QuickML, configure the adapter against its actual request/response schema, verify a real prediction, and deploy the app on Catalyst. Those account-dependent steps are intentionally pending in this local-first version.

The app reads `X_ZOHO_CATALYST_LISTEN_PORT` and binds to all interfaces automatically when that variable is present. Use `python app.py` as the startup command. If `HOST` is set, it overrides that default; use `HOST=0.0.0.0` for AppSail. The Dockerfile is a deployment starting point and has not been built in this environment.

The example environment file documents settings but is not automatically loaded. Set variables in your terminal or Catalyst console. Do not put tokens in frontend code or commit credentials. Public deployment also needs an access/rate-control policy for the prediction endpoint, since it can make billable QuickML calls.

## Verification

28 automated tests passed, including boundary rules, battery reserve/energy accounting, missing data, input ordering, malformed outputs, QuickML adapter behavior with mocked responses, upload validation and limits, API forecasts, and the CLI from another directory. The original supplied self-test passes on all 1,000 sample rows. Browser checks covered initial forecasting, model results, and changing battery assumptions. Live QuickML and Catalyst deployment are not yet tested.

## Dashboard design

The dashboard uses a white workspace, locally bundled Geist typography, black highlights, and responsive cards. An interactive Three.js microgrid has architectural details, neutral lighting, animated energy routes, and keyboard-accessible rotate, pause/resume, and reset controls. The hour slider, dispatch schedule, and chart points share one selected hour, updating demand, battery charge, and grid support. Chart controls switch between demand and simulated battery charge. The scene is illustrative; it is not a physical site model or live telemetry.

UI checks: the local preview rendered the 3D scene, selecting hour 10 showed 00:00 / main grid / 98.0 kW / 20% battery, and the Battery control switched to the charge chart. Browser error log was empty during these checks. The automated application suite passed 28 tests.

The monochrome redesign uses only neutral grayscale colors for UI, charts, and 3D materials. View transitions and chart updates use short eased animations; the 3D scene has a pause control and respects reduced-motion preferences.

## Landing page

The home URL `/` opens the black-and-white landing page with an animated interactive 3D microgrid, illustrative solar/storage/grid modes, platform capabilities, and a three-step walkthrough. `/dashboard` opens the working forecast workspace; `/dashboard#inputs` opens scenario inputs. Landing-page flow examples are illustrative and do not represent model predictions.
