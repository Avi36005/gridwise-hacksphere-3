#!/bin/sh
# Assemble a Linux-ready Catalyst AppSail bundle in build/appsail.
# AppSail's managed Python runtime runs on Linux and expects dependencies vendored
# into the build directory, so wheels are fetched for manylinux, not this machine.
set -eu
cd "$(dirname "$0")/.."
PY_VERSION="${APPSAIL_PYTHON_VERSION:-3.12}"
OUT=build/appsail
PYTHON="${PYTHON:-python3}"

rm -rf "$OUT"
mkdir -p "$OUT"
cp -R app.py predict.py sample_test_input.csv engine models static "$OUT"/

# LightGBM is only used by train.py; inference never imports it.
grep -v '^lightgbm' requirements.txt > "$OUT/requirements.txt"
"$PYTHON" -m pip install --quiet --no-compile --target "$OUT" \
  --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux_2_28_x86_64 \
  --implementation cp --python-version "$PY_VERSION" --only-binary=:all: \
  -r "$OUT/requirements.txt"

# Test suites and caches are not needed at runtime and push the bundle past AppSail's disk quota.
find "$OUT" -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +
find "$OUT" -name '*.pyi' -delete
rm -rf "$OUT/bin"

# Server settings come from app/.env (git-ignored); they land only in build/ and in Catalyst.
# With the endpoint key present the app serves QuickML predictions, authenticating with its own OAuth
# settings if given, otherwise with the admin credential AppSail injects into each request.
"$PYTHON" - "$OUT/app-config.json" "python_${PY_VERSION%.*}_${PY_VERSION#*.}" <<'PY'
import json, os, sys
from pathlib import Path
env = {}
if Path(".env").exists():
    for line in Path(".env").read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            env[key.strip()] = value.strip()
env.update({k: v for k, v in os.environ.items() if k.startswith(("QUICKML_", "ZOHO_"))})
secrets = ["QUICKML_ENDPOINT_KEY", "QUICKML_LINEAR_ENDPOINT_KEY", "QUICKML_CLIENT_ID", "QUICKML_CLIENT_SECRET", "QUICKML_REFRESH_TOKEN"]
passed = secrets + ["QUICKML_ENDPOINT_URL", "QUICKML_ORG_ID", "QUICKML_ENVIRONMENT", "QUICKML_MODEL_NAME", "QUICKML_LINEAR_MODEL_NAME", "ZOHO_ACCOUNTS_URL"]
settings = {k: env[k] for k in passed if env.get(k)}
settings["MODEL_PROVIDER"] = env.get("MODEL_PROVIDER") or ("quickml" if env.get("QUICKML_ENDPOINT_KEY") else "local")
config = {"command": "python3 -u app.py", "buildPath": "./", "stack": sys.argv[2], "env_variables": settings, "memory": 512}
Path(sys.argv[1]).write_text(json.dumps(config, indent=2) + "\n")
print("AppSail model provider:", settings["MODEL_PROVIDER"])
PY
du -sh "$OUT"
