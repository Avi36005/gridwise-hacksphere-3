"""Shared, row-local feature construction. No target lags or future-row access."""
import numpy as np
import pandas as pd

INPUT_COLUMNS = ["timestamp", "ambient_temp_c", "humidity_pct", "solar_irradiance_wm2"]
WEATHER_COLUMNS = INPUT_COLUMNS[1:]
TARGET = "target_demand_kw"


def validate_inputs(frame: pd.DataFrame, *, window: bool = False) -> pd.DataFrame:
    missing = [column for column in INPUT_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError("Missing columns: " + ", ".join(missing))
    if not 1 <= len(frame) <= 10000:
        raise ValueError("Supply between 1 and 10,000 input rows.")
    result = frame[INPUT_COLUMNS].copy().reset_index(drop=True)
    stamps = result["timestamp"].astype(str)
    if not stamps.str.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:00:00").all():
        raise ValueError("Timestamps must be hourly, in YYYY-MM-DD HH:00:00 format (local grid time).")
    dates = pd.to_datetime(stamps, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    if dates.isna().any():
        raise ValueError("One or more timestamps are not valid calendar dates.")
    for column in WEATHER_COLUMNS:
        original = result[column].map(lambda value: np.nan if isinstance(value, str) and not value.strip() else value)
        values = pd.to_numeric(original, errors="coerce")
        if (original.notna() & values.isna()).any() or np.isinf(values.to_numpy(dtype=float)).any():
            raise ValueError(f"{column} must contain finite numbers or blank values.")
        result[column] = values.astype(float)
    if ((result["humidity_pct"] < 0) | (result["humidity_pct"] > 100)).any():
        raise ValueError("Humidity must be between 0 and 100 percent.")
    if (result["solar_irradiance_wm2"] < 0).any():
        raise ValueError("Solar irradiance cannot be negative.")
    if window:
        if len(result) != 24:
            raise ValueError("A dashboard forecast requires exactly 24 rows.")
        if not (np.diff(dates.to_numpy(dtype="datetime64[s]")) == np.timedelta64(3600, "s")).all():
            raise ValueError("The 24 forecast rows must be consecutive hours in increasing order.")
    return result


def make_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Identical features for training, CLI, API, and QuickML export."""
    dates = pd.to_datetime(frame["timestamp"], format="%Y-%m-%d %H:%M:%S")
    features = frame[WEATHER_COLUMNS].astype(float).copy()
    features["hour"] = dates.dt.hour.astype(float)
    features["day_of_week"] = dates.dt.dayofweek.astype(float)
    features["is_weekend"] = (dates.dt.dayofweek >= 5).astype(float)
    for name, values, period in [("hour", dates.dt.hour, 24), ("week", dates.dt.dayofweek, 7), ("year", dates.dt.dayofyear, 365.25)]:
        features[f"{name}_sin"] = np.sin(2 * np.pi * values / period)
        features[f"{name}_cos"] = np.cos(2 * np.pi * values / period)
    features["cooling_degrees"] = (features["ambient_temp_c"] - 24).clip(lower=0)
    features["heating_degrees"] = (18 - features["ambient_temp_c"]).clip(lower=0)
    return features


def starter_baseline(frame: pd.DataFrame) -> np.ndarray:
    hours = pd.to_datetime(frame["timestamp"]).dt.hour
    return (120 + 30 * np.sin(2 * np.pi * (hours - 6) / 24)
            + np.maximum(0, frame["ambient_temp_c"].to_numpy() - 24) * 8.5).to_numpy()
