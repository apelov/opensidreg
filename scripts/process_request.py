#!/usr/bin/env python3

import os
import json
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

# ---------- CONFIG ----------

REGISTRY_FILE = Path("registry.json")
REQUESTS_DIR = Path("requests")
CONTEXTS_DIR = Path("contexts")
MONTHLY_LIMIT = 2500
YEARLY_LIMIT = 10000

# ---------- HELPER FUNCTIONS ----------

def load_registry():
    if REGISTRY_FILE.exists():
        return json.loads(REGISTRY_FILE.read_text())
    return {"registry_range": [1000000, 1999999], "last_updated": None, "assigned": []}

def save_registry(registry):
    registry["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d")
    REGISTRY_FILE.write_text(json.dumps(registry, indent=2))

def allocate_sid_range(existing, preferred_start, size):
    registry_range = load_registry()["registry_range"]
    used = set()
    for entry in existing:
        r = entry["range"]
        used.update(range(r[0], r[1] + 1))

    start = preferred_start if preferred_start else registry_range[0]
    while any((start + i) in used for i in range(size)) or start + size - 1 > registry_range[1]:
        start += size
        if start + size - 1 > registry_range[1]:
            raise ValueError("No available SID range within registry bounds.")

    return [start, start + size - 1]

def compile_yang(context_path):
    yang_files = list(context_path.glob("*.yang"))
    cmd = ["pyang"] + [str(f) for f in yang_files]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(e.stderr.decode())
        raise

def generate_sid_file(yang_file: Path, sid_file: Path, base_sid):
    content = {
        "module": yang_file.stem,
        "base_sid": base_sid,
        "generated_on": datetime.utcnow().isoformat()
    }
    sid_file.write_text(json.dumps(content, indent=2))

def check_user_quota(registry, user, request_size):
    now = datetime.utcnow()
    year_ago = now - timedelta(days=365)
    month_ago = now - timedelta(days=30)

    yearly_total = 0
    monthly_total = 0

    for entry in registry["assigned"]:
        if entry["author"] == user:
            date = datetime.strptime(entry["assigned_on"], "%Y-%m-%d")
            size = entry["range"][1] - entry["range"][0] + 1
            if date > year_ago:
                yearly_total += size
            if date > month_ago:
                monthly_total += size

    if monthly_total + request_size > MONTHLY_LIMIT:
        raise ValueError(f"User '{user}' exceeds monthly limit ({MONTHLY_LIMIT}). Requested: {request_size}, Used: {monthly_total}")
    if yearly_total + request_size > YEARLY_LIMIT:
        raise ValueError(f"User '{user}' exceeds yearly limit ({YEARLY_LIMIT}). Requested: {request_size}, Used: {yearly_total}")

def process_request(request_path):
    request = json.loads(Path(request_path).read_text())
    user = Path(request_path).parts[-2]
    module = request["module"]
    size = request.get("range_hint", {}).get("size", 1000)
    preferred_start = request.get("range_hint", {}).get("preferred_start")

    context_path = CONTEXTS_DIR / module / user
    context_path.mkdir(parents=True, exist_ok=True)

    for f in Path(request_path).parent.glob("*.yang"):
        (context_path / f.name).write_bytes(f.read_bytes())

    compile_yang(context_path)

    registry = load_registry()
    check_user_quota(registry, user, size)
    sid_range = allocate_sid_range(registry["assigned"], preferred_start, size)

    for f in context_path.glob("*.yang"):
        sid_file = context_path / (f.stem + ".sid")
        generate_sid_file(f, sid_file, sid_range[0])

    registry["assigned"].append({
        "module": module,
        "author": user,
        "range": sid_range,
        "location": str(context_path),
        "assigned_on": datetime.utcnow().strftime("%Y-%m-%d")
    })
    save_registry(registry)

# ---------- GITHUB ACTION WORKFLOW EXAMPLE ----------
# .github/workflows/validate-and-assign.yml
#
# name: Validate and Assign SID
#
# on:
#   pull_request:
#     paths:
#       - 'requests/**'
#
# jobs:
#   validate:
#     runs-on: ubuntu-latest
#     steps:
#       - uses: actions/checkout@v3
#       - name: Install pyang
#         run: sudo apt-get install -y pyang
#       - name: Process Request
#         run: |
#           python3 scripts/process_request.py requests/alice/2025-04-example.json
