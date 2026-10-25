# Connect the local app to Zoho QuickML

The competition brief requires a model trained and published in QuickML. Local scores and the bundled model are development evidence, not a replacement for that requirement.

## Train and publish

1. Create your Catalyst project and enable QuickML.
2. For the shared engineered-feature path, import `reports/quickml_training_features.csv` and select `target_demand_kw` as the regression target. All other columns are numeric inputs. `engine/features.py` generates precisely those inputs at inference time. Do not include the target as an input feature.
3. Add median imputation learned from the training partition. For a faithful comparison, use chronological partitions; do not let random splitting replace the local past-to-future assessment without clearly labeling the change. Alternatively upload the raw training CSV and implement equivalent timestamp features in QuickML, then use `QUICKML_FEATURE_MODE=raw`.
4. Use the built-in **CatBoost Regression** node with iterations 1000, learning rate 0.03, depth 3, L2 leaf regularization 3 (no subsampling options needed). Selection used only the first 3,200 rows: 4 expanding chronological folds of 400 h, a 160-config LightGBM search, 36 CatBoost and 60 XGB configs. This setting scored mean fold RMSE 9.97 kW; best tuned XGB 10.69, LightGBM 10.82, Random Forest defaults 10.96. Scored once afterwards on the locked last 800 rows: RMSE 11.01 kW (10.68-11.07 across seeds), R² 0.968, 98% of actual >=200 kW hours predicted >=200 kW. The CV-best LightGBM scored 15.31 kW on the same holdout, so it was rejected as less stable. Extra weather-history features (lags, rolling means) made CatBoost worse and were not adopted. These are starting parameters; QuickML's own split and training will not reproduce local scores exactly.
5. Evaluate, train the final pipeline, and publish its endpoint. Copy the endpoint URL and inspect the exact input schema and response in the endpoint's Test interface.

QuickML's regression list does not include Extra Trees (the bundled local model), so the built-in CatBoost route is the integration target.

## Server configuration

Set process environment variables on the backend:

```text
MODEL_PROVIDER=quickml
QUICKML_ENDPOINT_URL=<published HTTPS endpoint>
QUICKML_ACCESS_TOKEN=<OAuth access token>
QUICKML_ENDPOINT_KEY=<endpoint key>
QUICKML_ORG_ID=<organization ID>
QUICKML_ENVIRONMENT=Development
QUICKML_FEATURE_MODE=engineered
QUICKML_MODEL_NAME=CatBoost regression
```

The adapter sends `Authorization: Zoho-oauthtoken ...`, `X-QUICKML-ENDPOINT-KEY`, `CATALYST-ORG`, and `Environment` headers. The documented OAuth scope is `QuickML.deployment.READ`. Access tokens expire: the current adapter expects a valid token supplied by the server environment; automatic OAuth refresh is not implemented.

The default body follows the supplied kit's convention: `{"data": [{feature: value, ...}, ...]}`. It is a configurable **starting contract**, not a verified schema for your future endpoint:

- `QUICKML_REQUEST_KEY`: the wrapper key, default `data`; empty means a bare list of records.
- `QUICKML_RESPONSE_PATH`: dot path to prediction values, default `predictions`, e.g. `data.predictions`.
- `QUICKML_TARGET_KEY`: field extracted if each prediction is an object, default `target_demand_kw`.
- `QUICKML_FEATURE_MODE`: `engineered` for the exported numeric features or `raw` for timestamp and the three original weather fields.

If the endpoint requires another body structure or per-record calls, adjust `engine/inference.py` using the actual Test example before deploying. The integration does not invent an endpoint or pretend that a mocked response is a live prediction. Redirects are not followed with credentials attached.

## Verify before deployment

1. With the environment set, run `python predict.py --input sample_test_input.csv --output predictions.csv`.
2. Confirm that output explicitly names the `quickml` provider, preserves all 1,000 timestamps, and contains finite values.
3. Run `python test_eval.py` and exercise a 24-hour dashboard forecast against the real endpoint.
4. If the endpoint restricts batch size, implement its supported chunking scheme, preserving row order; the current adapter sends the supplied batch in one request.
5. Configure the app's environment in Catalyst, including `HOST=0.0.0.0` if HOST is explicitly set, and use `python app.py` as the startup command. Install Linux-compatible dependencies in the build environment; do not upload a macOS virtual environment to AppSail. `X_ZOHO_CATALYST_LISTEN_PORT` is honored automatically.

No Catalyst account operations or deployment have been performed for this local-first build.

## Official references

- [QuickML regression algorithms](https://docs.catalyst.zoho.com/en/quickml/help/ml-algorithms/regression-algorithms/)
- [QuickML custom code](https://docs.catalyst.zoho.com/en/quickml/help/custom-code/)
- [QuickML endpoint authentication](https://docs.catalyst.zoho.com/en/quickml/help/pipeline-endpoints/)
- [AppSail ports and startup configuration](https://docs.catalyst.zoho.com/en/serverless/help/appsail/appsail-configurations/)

## Deployed result (2026-10-24)

Pipeline `gridwise_catboost` (CatBoost Regression node: learning rate 0.03, max depth 3, L2 leaf regularization 3, RMSE loss; QuickML's CatBoost node has no iterations field, and CatBoost's default is 1000) is published as endpoint `gridwise-demand-catboost`. QuickML's node field is **Max Depth**; CatBoost allows at most 16, and a value of 1000 fails training.

QuickML 5-fold cross-validation (consecutive date blocks): CatBoost 9.23 kW mean RMSE, latest block 10.74 kW; the AutoML pipeline (GB Regression with Autolearn features) 9.83 kW and 12.63 kW. Local reproduction gave 9.26 / 11.01 and 9.78 / 12.72, so QuickML matches the local evidence.

An endpoint stays bound to its model: re-publishing a deployment with another model's version left the endpoint unusable (HTTP 500) until it was restored. Switch models by creating a new endpoint and changing `QUICKML_ENDPOINT_KEY`.

## Linear member (2026-10-25)

Dataset `quickml_linear_features` (`reports/quickml_linear_features.csv`: 4,000 rows, 52 features, target `target_demand_kw`) trains the Prediction pipeline `gridwise_linear`. It has a single **Linear Regression** node with Fit Intercept on, Positive off and no model explainer. Training used no feature selection or AutoML, because the design is full rank with hour 0 as the reference level.

Model `gridwise_linear model` V1 scored RMSE 8.77 kW, MAE 6.98 kW and R² 0.970 on QuickML's internal split. It is published as endpoint `gridwise-demand-linear` in Development.

The app sends the endpoint the 52 features from `engine.features.make_linear_features`, with temperature bounded to −5…42 °C. It averages the answer with CatBoost.

Set the key as `QUICKML_LINEAR_ENDPOINT_KEY` in `.env`. `scripts/build_appsail.sh` passes it to AppSail. Without that key, the app serves CatBoost alone.

The endpoint's predictions match a local least-squares fit of the same features within 1.2 kW. QuickML fits on its own training split, which explains the difference.
