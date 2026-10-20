"""Finite CI smoke phases; service lifecycle commands are separate Actions steps."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time
from urllib.request import urlopen

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("prepare", "start", "health", "doctor", "stop"))
parser.add_argument("--release", type=Path, required=True)
parser.add_argument("--copy", type=Path, required=True)
args = parser.parse_args()
if args.phase == "prepare":
    # Test only a disposable clone; never place generated state in the public ZIP.
    shutil.copytree(args.release.resolve(), args.copy.resolve(), symlinks=True)
elif args.phase == "health":
    deadline = time.monotonic() + 30
    state = json.loads((args.copy / "data/macos-services.json").read_text())
    ok = False
    while time.monotonic() < deadline:
        try:
            with urlopen(state["api_url"] + "/health", timeout=1) as api:
                api_ok = api.status == 200
            with urlopen(state["web_url"], timeout=1) as web:
                web_ok = web.status == 200
            ok = api_ok and web_ok
            if ok:
                break
        except Exception:
            pass
        time.sleep(0.5)
    print(json.dumps({"phase": "health", "ok": ok, "paid_api_calls": 0}))
    raise SystemExit(0 if ok else 1)
else:
    executable = args.copy.resolve() / "MathPlatform.app/Contents/MacOS/MathPlatform"
    if not executable.exists() and args.phase == "stop":
        raise SystemExit(0)
    command = [str(executable), args.phase, "--json"]
    # Direct CLI, no foreground service and no shell nesting.
    result = subprocess.run(command, timeout=10 if args.phase == "stop" else 20)
    raise SystemExit(result.returncode)
