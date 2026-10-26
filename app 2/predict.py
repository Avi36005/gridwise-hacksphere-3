"""Submission entry point: predict(input_df), plus timestamp-preserving CSV CLI."""
import argparse
from pathlib import Path
import sys

import pandas as pd

from engine.inference import ProviderError, forecast


def predict(input_df: pd.DataFrame):
    return forecast(input_df)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Input and output files must be different.")
    try:
        frame = pd.read_csv(args.input)
        predictions, info = forecast(frame)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"timestamp": frame["timestamp"].to_numpy(), "target_demand_kw": predictions}).to_csv(args.output, index=False)
    except (OSError, ValueError, ProviderError) as error:
        print(f"Prediction failed: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {len(predictions)} predictions using {info['provider']} / {info['name']} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
