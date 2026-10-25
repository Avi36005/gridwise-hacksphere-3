"""Compare models on past-to-future folds; assess the winner on untouched holdout."""
import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import joblib
import lightgbm
import numpy as np
import pandas as pd
import sklearn
from lightgbm import LGBMRegressor
from sklearn.base import clone
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from engine.features import TARGET, make_features, starter_baseline, validate_inputs

ROOT = Path(__file__).resolve().parent


def metrics(actual, predicted):
    return {"rmse_kw": float(np.sqrt(mean_squared_error(actual, predicted))),
            "mae_kw": float(mean_absolute_error(actual, predicted)),
            "r2": float(r2_score(actual, predicted))}


def candidates():
    return {
        "LightGBM · 15 leaves": LGBMRegressor(n_estimators=400, learning_rate=0.035, num_leaves=15, min_child_samples=25, reg_lambda=2, random_state=42, n_jobs=2, verbosity=-1),
        "LightGBM · 31 leaves": LGBMRegressor(n_estimators=400, learning_rate=0.04, num_leaves=31, min_child_samples=30, reg_lambda=5, random_state=42, n_jobs=2, verbosity=-1),
        "Extra Trees": ExtraTreesRegressor(n_estimators=350, min_samples_leaf=3, max_features=1.0, random_state=42, n_jobs=2),
        "Histogram Gradient Boosting": HistGradientBoostingRegressor(max_iter=350, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=5, early_stopping=False, random_state=42),
        "Ridge regression": make_pipeline(StandardScaler(), Ridge(alpha=10)),
    }


def train(input_path: Path, model_dir: Path):
    raw = pd.read_csv(input_path)
    validated = validate_inputs(raw)
    if TARGET not in raw:
        raise ValueError(f"Training data requires {TARGET}.")
    target = pd.to_numeric(raw[TARGET], errors="coerce")
    if target.isna().any() or not np.isfinite(target).all() or (target < 0).any():
        raise ValueError("Training targets must be finite, nonnegative numbers.")
    raw = validated.assign(**{TARGET: target}).sort_values("timestamp").reset_index(drop=True)
    if raw["timestamp"].duplicated().any() or len(raw) < 1000:
        raise ValueError("Training needs at least 1,000 rows with unique timestamps.")
    X, y = make_features(raw), raw[TARGET]
    cutoff = int(len(raw) * 0.8)
    development_X, development_y = X.iloc[:cutoff], y.iloc[:cutoff]
    splitter = TimeSeriesSplit(n_splits=3, test_size=min(480, cutoff // 5))
    folds = list(splitter.split(development_X))
    leaderboard, models = [], candidates()
    with threadpool_limits(limits=2):
        for name, estimator in models.items():
            fold_scores = []
            for train_idx, valid_idx in folds:
                pipeline = make_pipeline(SimpleImputer(strategy="median"), clone(estimator))
                pipeline.fit(development_X.iloc[train_idx], development_y.iloc[train_idx])
                forecast = np.maximum(0, pipeline.predict(development_X.iloc[valid_idx]))
                fold_scores.append(metrics(development_y.iloc[valid_idx], forecast))
            row = {"name": name, "cv_rmse_kw": float(np.mean([s["rmse_kw"] for s in fold_scores])), "folds": fold_scores}
            leaderboard.append(row)
            print(f'{name}: CV RMSE {row["cv_rmse_kw"]:.3f} kW', flush=True)
        leaderboard.sort(key=lambda item: item["cv_rmse_kw"])
        winner = leaderboard[0]["name"]
        best = make_pipeline(SimpleImputer(strategy="median"), clone(models[winner]))
        best.fit(development_X, development_y)
        holdout_predictions = np.maximum(0, best.predict(X.iloc[cutoff:]))
        holdout = metrics(y.iloc[cutoff:], holdout_predictions)
        baseline = metrics(y.iloc[cutoff:], starter_baseline(raw.iloc[cutoff:]))
        best.fit(X, y)
    model_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "model_name": winner, "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_rows": len(raw), "development_rows": cutoff, "holdout_rows": len(raw) - cutoff,
        "training_start": raw.iloc[0]["timestamp"], "training_end": raw.iloc[-1]["timestamp"],
        "holdout_start": raw.iloc[cutoff]["timestamp"], "holdout_end": raw.iloc[-1]["timestamp"],
        "holdout": holdout, "baseline_holdout": baseline, "leaderboard": leaderboard,
        "features": list(X.columns), "parameters": models[winner].get_params(),
        "weather_medians": raw[["ambient_temp_c", "humidity_pct", "solar_irradiance_wm2"]].median().to_dict(),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "validation": "Three expanding chronological folds on the first 80%. Select by mean fold RMSE. Evaluate the selected model once on the last 20%. Refit on all labeled rows for inference. Sample test CSV has no labels and is never scored.",
        "fold_periods": [{"train_end": raw.iloc[a[-1]]["timestamp"], "validation_start": raw.iloc[b[0]]["timestamp"], "validation_end": raw.iloc[b[-1]]["timestamp"]} for a, b in folds],
        "versions": {"python": platform.python_version(), "scikit_learn": sklearn.__version__, "lightgbm": lightgbm.__version__, "numpy": np.__version__, "pandas": pd.__version__},
    }
    # Only load the trusted, locally produced artifact; uploaded files are always CSV.
    joblib.dump({"pipeline": best, "metadata": metadata}, model_dir / "demand_model.joblib", compress=3)
    (model_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str))
    report_dir = ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    pd.DataFrame({"timestamp": raw.iloc[cutoff:]["timestamp"], "actual_demand_kw": y.iloc[cutoff:], "predicted_demand_kw": holdout_predictions}).to_csv(report_dir / "holdout_predictions.csv", index=False)
    # Shareable numeric features for a QuickML regression pipeline; the adapter generates the same fields.
    X.assign(**{TARGET: y}).to_csv(report_dir / "quickml_training_features.csv", index=False)
    print(f"Selected {winner}. Untouched holdout RMSE: {holdout['rmse_kw']:.3f} kW; starter: {baseline['rmse_kw']:.3f} kW.", flush=True)
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "train_telemetry.csv")
    parser.add_argument("--model-dir", type=Path, default=ROOT / "models")
    args = parser.parse_args()
    train(args.input, args.model_dir)
