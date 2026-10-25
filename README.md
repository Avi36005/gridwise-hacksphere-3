# Gridwise — HackSphere 3.0 Smart Energy Microgrid

Hourly energy-demand forecasting for a microgrid, with power-source switching between Main Grid and Renewable Battery Storage, built on **Zoho Catalyst** (AppSail + QuickML).

- **Live app:** https://gridwise-50047143069.development.catalystappsail.in (landing page) · [`/dashboard`](https://gridwise-50047143069.development.catalystappsail.in/dashboard)
- **Models:** two regressions trained and published in **Catalyst QuickML** (IN data center): CatBoost (endpoint `gridwise-demand-catboost`) and a linear model on hourly cooling features (endpoint `gridwise-demand-linear`). Every forecast is the plain average of the two.
- **Submission script:** `predict.py` (self-contained: numpy, pandas, requests) — `python test_eval.py` prints `SUCCESS`

## Requirements → implementation

| Brief requirement | Where it lives |
| --- | --- |
| 1. Train and publish a regression model in QuickML | Two QuickML pipelines: `gridwise_catboost` → endpoint `gridwise-demand-catboost`, and `gridwise_linear` → endpoint `gridwise-demand-linear`; the choice is in [Model results](#model-results) and `docs/QUICKML.md` |
| 2. Ingest telemetry via UI form or CSV; clean, validate, handle missing values | Dashboard **Inputs** view: CSV upload (any consecutive 24-hour window), per-hour editing, manual scenario form. `engine/features.py` validates timestamps and ranges; blank weather values are interpolated between neighbouring hours (training medians at the edges) before any model sees them |
| 3. Forecast hourly demand for a 24-hour window via the QuickML endpoint | `engine/inference.py` builds each model's features (14 for CatBoost, 52 for the linear model), calls both endpoints in parallel in batches, and averages them; the dashboard forecasts 24 consecutive hours |
| 4. Threshold and time-based switching rules | `engine/controller.py` (tested at every boundary): **Renewable Storage Mode** if demand > 160 kW or hour 18:00–22:00 inclusive, else **Main Grid Mode**; **Critical Overload Alert** at ≥ 200 kW, independent of mode |
| 5. Dashboard: load curves, active grid states, battery SOC, overload alerts | Overview: demand chart with 160/200 kW thresholds, active grid state strip, 24-hour dispatch timeline, transition controller log, battery state-of-charge chart and gauge, alert banner, hourly table, exports (predictions CSV, control schedule CSV, dispatch commands JSON). **Scenario lab:** battery sizing on the same forecast |
| Recommended architecture: persist telemetry and system logs in Data Store | Every dashboard forecast is saved to **Catalyst Data Store**: `ForecastRuns` (one row per run), `Telemetry` (its 24 input hours with predictions, grid mode, overload flag, battery charge), and `AlertLog` (each mode switch and overload alert). The Overview's **System log** panel reads them back with ZCQL. Uploaded CSVs are persisted as telemetry rows rather than as files in Stratus. |
| `predict.py` contract | `predict(input_df)` returns one prediction per row in input order; CLI `--input/--output` writes `timestamp,target_demand_kw` |

## Model results

All candidates are algorithms QuickML offers, scored on the same 14 features. Selection used only the first 3,200 hours (four past-to-future folds); finalists were scored once on the locked last 800 hours.

| QuickML algorithm (tuned) | Mean fold RMSE | Locked holdout RMSE |
| --- | ---: | ---: |
| **CatBoost Regression — deployed** (learning rate 0.03, depth 3, L2 3, 1000 iterations) | **9.97 kW** | **11.01 kW** |
| XGB Regression (1200 trees, learning rate 0.03, depth 3) | 10.69 kW | 13.16 kW |
| LGBM Regression (800 trees, learning rate 0.03, 7 leaves) | 10.82 kW | 15.31 kW |
| Random-Forest Regression (defaults) | 10.96 kW | 12.57 kW |
| Starter-kit formula | — | 45.85 kW |

On the holdout the deployed model explains 96.8% of demand variance and flags 98% of the hours whose actual demand reached 200 kW. Searches: 160 LightGBM, 36 CatBoost, and 60 XGBoost configurations; extra weather-history features (lags, rolling means) were tested and rejected because they made CatBoost worse.

QuickML's own 5-fold cross-validation on consecutive date blocks agrees: **CatBoost 9.23 kW** (latest block 10.74 kW) vs **AutoML 9.83 kW** (latest block 12.63 kW). Details and caveats are in `docs/QUICKML.md`.

### Two-model ensemble (served)

CatBoost is strongest in spring; a linear model with a separate cooling slope for each hour extrapolates better into the hot weeks that the hidden test covers. Each model was refit only on hours before a split and scored on the hours after it:

| RMSE, kW | Last 800 h | Last 600 h | Last 400 h |
| --- | ---: | ---: | ---: |
| **CatBoost + linear, plain average (served)** | **9.22** | **9.03** | **8.40** |
| Linear regression (52 features) | 9.27 | 9.51 | 8.58 |
| CatBoost alone | 10.74 | 9.57 | 8.64 |

The last 800 training hours average 29.1 °C, as does the 1,000-row test sample (29.2 °C), so these late splits are the closest stand-in for the hidden test. The 50/50 weight is not tuned. The linear member uses an hourly profile (hour 0 as the reference), a cooling slope per hour (degrees above 24 °C), heating degrees, humidity, irradiance, and a weekend flag: 52 full-rank columns, ordinary least squares, exported as `reports/quickml_linear_features.csv`. Its temperature input is bounded to −5…42 °C so it never extrapolates far beyond the training range. If either endpoint fails, the other answers alone.

Features (identical for training, the app, and `predict.py`): temperature, humidity, solar irradiance, hour, weekday, weekend flag, sine/cosine of hour, weekday, and day of year, and heating/cooling degrees. No target lags are used, so any row can be predicted from its own weather and timestamp.

## Run locally

Use Python **3.12** (tested 3.12.14):

```sh
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py                       # http://127.0.0.1:8000 (local model unless MODEL_PROVIDER=quickml)
```

On this Mac, `start.command` does the same. Credentials are optional locally; see [QuickML and Catalyst](#quickml-and-catalyst).

## Predict, test, retrain

```sh
python predict.py --input sample_test_input.csv --output predictions.csv
python test_eval.py                 # organisers' self-test → SUCCESS
python -m pip install -r requirements-dev.txt && python -m pytest -q
python train.py                     # retrains the bundled local fallback model and regenerates the QuickML training CSV
```

`predict.py` needs only numpy, pandas, and requests. It calls QuickML directly when credentials are in the environment; otherwise it sends rows to the deployed app's `/api/predict`, which calls the same QuickML endpoint server-side, so graders need no secrets. Requests are chunked and retried. If every remote route fails, it prints a `WARNING` and scores the affected chunks with a built-in copy of the linear model (numpy only; 9.3 kW on the locked final 800 hours when fitted on the earlier hours) rather than crashing. Batch scoring is forgiving: unparseable timestamps are interpolated, out-of-range humidity and irradiance are clipped, and blank weather is filled.

## Switching rules

| Condition (per forecast hour) | Signal |
| --- | --- |
| Demand **> 160 kW** OR hour **18:00–22:00 inclusive** | Renewable Storage Mode |
| Otherwise (demand ≤ 160 kW and outside 18:00–22:00) | Main Grid Mode |
| Demand **≥ 200 kW** | Critical Overload Alert (independent of mode) |

Timestamps are local grid time. Exactly 160 kW outside the evening window is Main Grid Mode; exactly 200 kW triggers the alert.

## Battery assumptions

Defaults: 1,200 kWh, 80% initial charge, 20% reserve, 150 kW PV, 150 kW max charge, 200 kW max discharge, 95% efficiency each way, one-hour steps. In storage mode PV serves the load first, the battery covers the rest within its limits, and grid support covers any shortfall (shown in the table). In Main Grid Mode the grid serves the load and PV charges the battery. Reserve limits never change the required control signal. All values are adjustable in the dashboard; they are illustrative, not organiser specifications.

## Architecture

```text
Browser dashboard (HTML/CSS/JS, Chart.js) ─┐
predict.py (graders) ──────────────────────┤
                                           ▼
Catalyst AppSail · FastAPI (app.py)
  ├─ engine/features.py    validation, gap filling, 14 features
  ├─ engine/inference.py   QuickML client (platform credential, batching, retry, two-model average)
  │        ├──▶ Catalyst QuickML endpoint · CatBoost Regression (14 features)
  │        └──▶ Catalyst QuickML endpoint · Linear Regression (52 features)
  ├─ engine/controller.py  switching rules, overload alerts, battery simulation
  └─ engine/datastore.py   runs, telemetry, controller events
           └──▶ Catalyst Data Store · ForecastRuns / Telemetry / AlertLog (ZCQL for the system log)
```

One AppSail service serves the dashboard and the API on the same origin, so no CORS or separate frontend hosting is needed. Inside AppSail the app authenticates to QuickML and Data Store with the admin credential and project headers Catalyst injects into each request; no OAuth secret is stored in the code or the browser. Data Store writes run in the background after the forecast is returned, so a logging failure can never delay or break a forecast.

## Reliability and guardrails

| Failure | What happens |
| --- | --- |
| One QuickML endpoint failing | The other model answers alone (both are called in parallel); the model label shows which answered |
| Both QuickML endpoints failing | Dashboard and `/api/predict` answer with the bundled local model, labelled `local-backup`, plus a visible warning; the engine itself never substitutes silently |
| App unreachable during grading | `predict.py` retries patiently (honouring `Retry-After`), tries each app URL, then scores only the failed chunk with its built-in copy of the linear model (9.3 kW holdout RMSE); the training mean is the last resort, used only when no timestamp can be read. For any input with rows it never crashes or returns NaN |
| Large inputs | `predict.py` sends 2,000-row chunks; the server batches 200 rows per QuickML call. 10,000 rows took 21 s end to end with both models |
| Messy inputs | Column names matched case- and space-insensitively; missing weather columns, blanks, and non-numeric values filled; out-of-range values clipped; unparseable timestamps interpolated; datetime columns formatted |
| Impossible dashboard inputs | Rejected with a clear message (humidity outside 0–100%, negative irradiance, temperature outside −60…60 °C, gaps in the 24 hours) |
| Abuse / cost | Per-client rate limits (30 batch calls, 60 forecasts and 120 battery scenarios per minute), 1 MB and 4 MB body limits, 10,000 rows per batch call; saved-run ids must be numeric before any ZCQL query |
| Data Store problems | Persistence runs in the background after the reply; failures are logged and never affect a forecast |
| Unexpected exceptions | A catch-all handler returns a generic JSON error; details go only to the server log |
| Browser / network blips | Dashboard requests time out after 60 s and retry twice on network errors and 502/503/504 (cold starts) |
| Secrets | Endpoint key only in git-ignored `.env` and Catalyst environment settings; QuickML and Data Store use the credential AppSail injects per request; strict CSP and `frame-ancestors 'none'` |

## QuickML and Catalyst

**Live app:** https://gridwise-50047143069.development.catalystappsail.in (Catalyst AppSail, IN data center, project Gridwise).

**QuickML:** the 14-feature training file `reports/quickml_training_features.csv` trains the CatBoost Regression pipeline `gridwise_catboost` (endpoint `gridwise-demand-catboost`), and the 52-feature file `reports/quickml_linear_features.csv` trains the Linear Regression pipeline `gridwise_linear` (endpoint `gridwise-demand-linear`). Both are published at `https://api.catalyst.zoho.in/quickml/v1/project/102738000000013053/endpoints/predict`. Requests are `POST {"data": [feature records]}` with headers `Authorization: Zoho-oauthtoken …`, `X-QUICKML-ENDPOINT-KEY`, `CATALYST-ORG`, and `Environment`; the response carries one prediction per record under `result`. See **docs/QUICKML.md** for the model choice: CatBoost Regression (1000 iterations, learning rate 0.03, depth 3, L2 3) was best on chronological validation, and QuickML's own 5-fold cross-validation (consecutive date blocks) agreed:

| QuickML model | Mean 5-fold RMSE | Latest block | Max error |
| --- | ---: | ---: | ---: |
| **CatBoost Regression (deployed)** | **9.23 kW** | **10.74 kW** | 54.9 kW |
| AutoML (GB Regression + Autolearn) | 9.83 kW | 12.63 kW | 61.0 kW |

QuickML's default single random 80/20 split ranks them the other way (8.47 vs 8.94 kW) because a random hour's neighbours are in training; it is not a fair test for forecasting later dates.

**How predictions flow (no secrets in the repository):**

```text
predict.py ──(QuickML credentials in env/.env?)── yes ─→ QuickML endpoint
     │                                              
     └─ no ─→ POST {app}/api/predict ─→ server holds credentials ─→ QuickML endpoint
```

Inside AppSail the server authenticates with the admin credential Catalyst injects into each request; anywhere else it renews the hourly OAuth token from a Zoho self-client refresh token and retries once if a token is rejected. Rows are sent in batches of 200. `/api/predict` accepts 1–10,000 rows, is limited to 30 requests per client per minute, and is the only path that spends QuickML calls without credentials. If both remote paths fail, `predict.py` prints a `WARNING` and uses its built-in backup regression so a grading run still produces output; the dashboard and API fall back only with a visible label and warning.

**Credentials** live in `app/.env` (git-ignored; template in `.env.example`). The deployed app needs only the endpoint keys: `QUICKML_ENDPOINT_KEY` (CatBoost) and `QUICKML_LINEAR_ENDPOINT_KEY` (linear; optional, without it the app serves CatBoost alone). Calling QuickML from outside Catalyst also needs `QUICKML_CLIENT_ID`, `QUICKML_CLIENT_SECRET`, and `QUICKML_REFRESH_TOKEN`. Create the self client at api-console.zoho.in with scope `QuickML.deployment.READ`, put the one-time grant code in `QUICKML_AUTH_CODE`, and run `python scripts/exchange_quickml_code.py` to replace it with the refresh token.

The app reads `X_ZOHO_CATALYST_LISTEN_PORT` and binds to all interfaces automatically when that variable is present. If `HOST` is set, it overrides that default. The Dockerfile is a deployment starting point and has not been built in this environment.

### Deploy to Catalyst AppSail

AppSail's managed Python runtime is Linux and expects dependencies vendored into the build directory, so the bundle is built with Linux wheels rather than this Mac's:

```sh
./scripts/build_appsail.sh          # → build/appsail (~234 MB, Python 3.12 manylinux wheels, app-config.json)
catalyst login --dc in
catalyst init --org <org_id> --project <project_id> -ni
catalyst appsail:add --name gridwise --stack python_3_12 --source "$PWD/build/appsail" --build ./ --command "python3 -u app.py" -ni
catalyst deploy --only appsail -ni
```

The build script copies the QuickML settings from `.env` into `build/appsail/app-config.json` (git-ignored) as AppSail environment variables, and sets `MODEL_PROVIDER=quickml` when the endpoint key is present. The bundle omits LightGBM (training only) and test suites to stay within AppSail's 256 MB default disk. Memory is 512 MB (about 350 MB in use). The bundle was verified in a `linux/amd64` `python:3.12-slim` container and on the live URL: health, landing page, dashboard, a 24-hour forecast, and CSV upload.

## Verification

891 automated tests pass. `tests/test_core.py` (248) covers: every hour of the day against the switching rules at each boundary (192 cases checked against an oracle written from the problem statement), battery accounting, missing data, input ordering, malformed outputs, QuickML token refresh, retry, batching, response parsing, AppSail platform credentials, the two-model average (each endpoint gets its own features; one failing leaves the other; both failing uses the labelled backup), the linear member's temperature bound, full-rank linear features matching `predict.py` and the exported QuickML dataset, the embedded backup coefficients and their chronological score, `/api/predict` validation and rate limiting, the standalone `predict.py` (API route, direct two-endpoint route, datetime inputs, local fallback, feature parity), battery sizing (the recommended capacity covers every storage hour and one kWh less does not), reopening saved runs without a model call and rejecting non-numeric ids, Data Store persistence and the system-log read path (mocked), QuickML-outage backup with warnings, forecast rate limits and temperature ranges, the catch-all error handler, messy-input and constant fallbacks, controller event generation, upload limits, and the CLI from another directory. Tests clear real credentials so none can reach QuickML.

`tests/test_properties.py` (643 of the 891) checks invariants on many generated or hand-picked inputs:
- **Battery simulation** on 150 random days and batteries: energy balance every hour, rating limits, state of charge, and grid support only when the battery is power- or energy-limited.
- **Battery sizing** on 60 random cases: more capacity never needs more grid support, and the recommendation is minimal.
- **Feature parity:** `predict.py` and the server build identical features on 40 random batches spanning 2012–2019.
- **Controller:** every hour is monotonic in demand, the reason text explains each decision, and impossible demand is rejected. The controller log has every switch and alert edge on 60 random days.
- **Input validation:** 36 impossible or boundary dashboard inputs.
- **`predict.py` robustness:** 22 messy grader inputs, input order preserved under 20 shuffles, chunking at 1–6,001 rows, and partial outages scored locally per chunk.
- **API:** every 24-hour window of the sample through `/api/forecast`, plus request matrices for the forecast, batch, scenario and CSV endpoints, and security headers on every page.

A mutation check confirmed the suite fails when the 160 kW comparison, the evening window, the 200 kW alert or battery efficiency is altered.

**Two-model release, 2026-10-25.** The linear pipeline `gridwise_linear` reports RMSE 8.77 kW, MAE 6.98 kW and R² 0.970 on QuickML's internal random split; CatBoost reported 8.94 kW on the same kind of split. These numbers are not comparable with the chronological results above. On the deployed app:
- `/api/model` lists both members.
- Every forecast is labelled `QuickML ensemble: CatBoost regression (QuickML) + Linear regression (QuickML)`.
- The linear endpoint's answers match a local least-squares fit of the same features within 1.2 kW, with a mean difference of 0.0 kW.

Checks after the switch:
- The grader simulation printed SUCCESS in 2.7 s.
- 10,000 rows took 21 s.
- 26 of 26 live API checks passed. They cover pages and security headers, health, CSV upload with blanks and gap rejection, the 24-hour forecast with all rules matching the brief, Data Store persistence, newest-first run listing, run replay within 0.005 kW, scenarios, and validation, size and rate limits.
- The dashboard shows "QuickML · CatBoost + Linear" with no console errors on desktop or at 390 px.

Two Data Store fixes shipped with this release:
- Runs are listed by creation time, because Catalyst ROWIDs are not in insertion order.
- Predictions are rounded to 2 decimals before they are saved, because the column truncates.

Live checks on 2026-10-25 (headless Chrome against the deployed app): the Scenario lab sized the battery for a QuickML forecast and "Apply" re-ran it with zero shortfall hours; all three exports downloaded; a saved run reopened from Data Store with 24 hours and no model call; the 390 px mobile layout had no horizontal overflow; no console errors.

Live checks on 2026-10-24 against the deployed app and the published QuickML endpoint: `python test_eval.py` printed SUCCESS with no credentials (predict.py → `/api/predict` → QuickML, 1,000 rows in about 4 s); the CLI wrote 1,000 timestamp-preserving predictions; a dashboard forecast reported `provider: quickml`. After switching to CatBoost, the same checks passed again, with `model: CatBoost regression (QuickML)`; direct endpoint predictions for the first five sample hours matched the app's (247.78, 237.79, 236.81, 294.86, 270.98 kW).

**Data Store:** a live dashboard forecast returned `persisted: true`; `ForecastRuns` gained the run row, `Telemetry` exactly 24 rows for that run (checked with ZCQL `COUNT`), and `AlertLog` its 6 controller events, which `/api/runs` and the System log panel read back.

**Grader simulation:** a fresh environment with only numpy, pandas 3.1, and requests (no scikit-learn, no `engine/`, no `.env`) and a folder holding just `predict.py`, `test_eval.py`, and the sample CSV: `python test_eval.py` printed SUCCESS in 2.3 s, and the CLI wrote 1,000 QuickML CatBoost predictions with timestamps preserved. Live batch scoring repaired blank, out-of-range, and unparseable inputs; a dashboard forecast with four blank weather values ran with a visible warning; impossible dashboard inputs are rejected with a clear message.

Inside AppSail the app authenticates to QuickML with the admin credential Catalyst injects into each proxied request (`X-ZC-Admin-Cred-Token`), so no OAuth client is required there; `QUICKML_CLIENT_ID` / `QUICKML_REFRESH_TOKEN` are only needed to call QuickML directly from elsewhere.

## Dashboard design

`/` is a landing page with an animated, illustrative Three.js microgrid (not a site model or telemetry). `/dashboard` is the workspace:

- **Overview:** forecast window, peak demand, energy, storage hours, overload count, an active grid state strip (mode, alert state, mode switches), a demand chart with the 160 kW and 200 kW thresholds (toggle to battery state of charge), the battery outlook gauge, a 24-hour dispatch schedule, the transition controller log (every mode switch and overload alert with its reason), an hourly table with filters, exports (predictions CSV in the `predict.py` format, a control schedule CSV, and 24 dispatch commands as JSON), and the System log of recent runs and controller events from Catalyst Data Store.
- **Telemetry inputs:** CSV upload with a choice of 24-hour window, per-hour editing (blanks allowed), a manual scenario form, and battery assumptions.
- **Scenario lab:** the same forecast re-simulated with six battery capacities (grid support hours and energy, battery delivery, lowest and ending charge), plus the smallest battery that covers every storage hour, to the nearest kWh. If the discharge rating is the limit, it is raised first. "Apply" sets the size and re-runs the forecast. No extra model calls.
- **Saved runs:** every run in the System log has an **Open** button that reloads it from Data Store (predictions as saved; dispatch and battery recomputed with the current assumptions).
- **Model performance:** the served two-model ensemble and its late-split scores, QuickML's 5-fold results, and the algorithm-selection table.

Monochrome design with locally bundled fonts and icons; layouts adapt at 1200, 960, 760, and 680 px; animations respect reduced-motion preferences. The live dashboard was checked in a browser on 2026-10-24: all views rendered from QuickML forecasts with no console errors.
