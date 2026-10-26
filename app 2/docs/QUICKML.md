# Connect the local app to Zoho QuickML

The competition brief requires a model trained and published in QuickML. Local scores and the bundled model are development evidence, not a replacement for that requirement.

## Train and publish

1. Create your Catalyst project and enable QuickML.
2. For the shared engineered-feature path, import `reports/quickml_training_features.csv` and select `target_demand_kw` as the regression target. All other columns are numeric inputs. `engine/features.py` generates precisely those inputs at inference time. Do not include the target as an input feature.
3. Add median imputation learned from the training partition. For a faithful comparison, use chronological partitions; do not let random splitting replace the local past-to-future assessment without clearly labeling the change. Alternatively upload the raw training CSV and implement equivalent timestamp features in QuickML, then use `QUICKML_FEATURE_MODE=raw`.
4. Try the built-in **LGBM Regression** node. A tested local starting configuration is 400 estimators, learning rate 0.035, 15 leaves, minimum 25 child samples, and lambda 2. These are starting parameters, not a claim that the endpoint will reproduce local scores.
5. Evaluate, train the final pipeline, and publish its endpoint. Copy the endpoint URL and inspect the exact input schema and response in the endpoint's Test interface.

Extra Trees was the best local candidate. It may be explored through QuickML's custom-algorithm feature if the environment supports it, but that path has not been implemented or tested here. The documented built-in LightGBM route is the initial integration target.

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
QUICKML_MODEL_NAME=LightGBM regression
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
