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
    if ((result["ambient_temp_c"] < -60) | (result["ambient_temp_c"] > 60)).any():
        raise ValueError("Temperature must be between -60 and 60 °C.")
    if (result["solar_irradiance_wm2"] < 0).any():
        raise ValueError("Solar irradiance cannot be negative.")
    if window:
        if len(result) != 24:
            raise ValueError("A dashboard forecast requires exactly 24 rows.")
        if not (np.diff(dates.to_numpy(dtype="datetime64[s]")) == np.timedelta64(3600, "s")).all():
            raise ValueError("The 24 forecast rows must be consecutive hours in increasing order.")
    return result


def coerce_inputs(frame: pd.DataFrame) -> pd.DataFrame:
    """Forgiving cleanup for batch scoring: once the columns exist, no row is rejected.

    Unparseable timestamps are interpolated from their neighbours, non-numeric weather becomes
    missing, and humidity / irradiance are clipped to their physical ranges.
    """
    missing = [column for column in INPUT_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError("Missing columns: " + ", ".join(missing))
    if not 1 <= len(frame) <= 100000:
        raise ValueError("Supply between 1 and 100,000 input rows.")
    result = frame[INPUT_COLUMNS].copy().reset_index(drop=True)
    stamps = pd.to_datetime(result["timestamp"].astype(str), errors="coerce", format="mixed")
    if stamps.isna().all():
        raise ValueError("No timestamp could be parsed.")
    if stamps.isna().any():
        nanos = stamps.astype("int64").where(stamps.notna()).astype(float)
        stamps = pd.to_datetime(nanos.interpolate(limit_direction="both").round(), unit="ns")
    result["timestamp"] = stamps.dt.strftime("%Y-%m-%d %H:%M:%S")
    for column in WEATHER_COLUMNS:
        values = pd.to_numeric(result[column], errors="coerce").astype(float)
        result[column] = values.where(np.isfinite(values))
    result["humidity_pct"] = result["humidity_pct"].clip(0, 100)
    result["ambient_temp_c"] = result["ambient_temp_c"].clip(-60, 60)
    result["solar_irradiance_wm2"] = result["solar_irradiance_wm2"].clip(lower=0)
    return result


def fill_weather(frame: pd.DataFrame, medians: dict) -> pd.DataFrame:
    """Fill weather gaps by interpolating between neighbouring hours, then with training medians."""
    if not frame[WEATHER_COLUMNS].isna().any().any():
        return frame
    order = pd.to_datetime(frame["timestamp"], format="mixed").argsort(kind="stable")
    filled = frame.iloc[order].copy()
    filled[WEATHER_COLUMNS] = filled[WEATHER_COLUMNS].interpolate(limit_direction="both")
    filled = filled.sort_index()
    return filled.fillna({column: medians.get(column, 0.0) for column in WEATHER_COLUMNS})


def make_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Identical features for training, CLI, API, and QuickML export."""
    dates = pd.to_datetime(frame["timestamp"], format="%Y-%m-%d %H:%M:%S")
    features = frame[WEATHER_COLUMNS].astype(float).copy()
    features["hour"] = dates.dt.hour.astype(float)
    features["day_of_week"] = dates.dt.day_of_week.astype(float)
    features["is_weekend"] = (dates.dt.day_of_week >= 5).astype(float)
    for name, values, period in [("hour", dates.dt.hour, 24), ("week", dates.dt.day_of_week, 7), ("year", dates.dt.dayofyear, 365.25)]:
        features[f"{name}_sin"] = np.sin(2 * np.pi * values / period)
        features[f"{name}_cos"] = np.cos(2 * np.pi * values / period)
    features["cooling_degrees"] = (features["ambient_temp_c"] - 24).clip(lower=0)
    features["heating_degrees"] = (18 - features["ambient_temp_c"]).clip(lower=0)
    return features


def make_linear_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Full-rank design for the QuickML linear model: an hourly profile plus an hourly cooling slope.

    Hour 0 is the reference level, so the intercept stays identifiable without regularization.
    """
    dates = pd.to_datetime(frame["timestamp"], format="%Y-%m-%d %H:%M:%S")
    hours = dates.dt.hour.to_numpy()
    temperature = frame["ambient_temp_c"].to_numpy(dtype=float)
    cooling = np.clip(temperature - 24, 0, None)
    features = {"ambient_temp_c": temperature,
                "humidity_frac": frame["humidity_pct"].to_numpy(dtype=float) / 100,
                "solar_kw_m2": frame["solar_irradiance_wm2"].to_numpy(dtype=float) / 1000,
                "heating_degrees": np.clip(18 - temperature, 0, None),
                "is_weekend": (dates.dt.day_of_week >= 5).to_numpy(dtype=float)}
    for hour in range(1, 24):
        features[f"hour_{hour:02d}"] = (hours == hour).astype(float)
    for hour in range(24):
        features[f"cooling_hour_{hour:02d}"] = (hours == hour) * cooling
    return pd.DataFrame(features, index=frame.index)


def starter_baseline(frame: pd.DataFrame) -> np.ndarray:
    hours = pd.to_datetime(frame["timestamp"]).dt.hour
    return (120 + 30 * np.sin(2 * np.pi * (hours - 6) / 24)
            + np.maximum(0, frame["ambient_temp_c"].to_numpy() - 24) * 8.5).to_numpy()
