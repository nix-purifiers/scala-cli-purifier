#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Test-only launcher: record the exact CLI and set its own IPv4 property."""
import hashlib
import json
import os
from pathlib import Path
import sys

raw = Path(sys.argv[1])
argv = [str(raw), "-Djava.net.preferIPv4Stack=true", *sys.argv[2:]]
paths = [raw]
wrapped = raw.with_name(".scala-cli-wrapped")
if wrapped.is_file():
    paths.append(wrapped)
print("URL CLI diagnostic: " + json.dumps({
    "scope": "test-only selected CLI process; original URL check remains gating",
    "argv": argv,
    "binaries": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
    "launcherSource": str(Path(__file__).resolve()),
    "launcherSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
}, sort_keys=True), file=sys.stderr, flush=True)
os.execv(str(raw), argv)
