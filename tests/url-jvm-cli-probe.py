#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Test-only official JVM CLI launcher with properties set at VM startup."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

java, jar, *arguments = sys.argv[1:]
properties = []
while arguments and arguments[0].startswith("-D"):
    properties.append(arguments.pop(0))
argv = [java, "-Djava.net.preferIPv4Stack=true", *properties, "-jar", jar, *arguments]
version = subprocess.check_output([java, *properties, "-jar", jar, "version", "--cli-version"], text=True, timeout=30).strip()
if version != "1.18.0":
    raise RuntimeError("official diagnostic JVM CLI version mismatch")
settings = subprocess.run([java, "-XshowSettings:properties", "-version"], capture_output=True, text=True, timeout=30, check=True)
sdk = {}
for line in settings.stderr.splitlines():
    key, separator, value = line.strip().partition(" = ")
    if separator and key in ("java.version", "java.runtime.version", "java.vendor", "java.home"):
        sdk[key] = value
print("URL JVM CLI diagnostic: " + json.dumps({
    "scope": "test-only official JVM SDK; original native URL checks remain gating",
    "scalaCliVersion": version, "javaSdk": sdk,
    "argv": [a.split("=", 1)[0] + "=<redacted>" if a.startswith("-D") else a for a in argv],
    "binaries": [{"path": p, "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in (java, jar)],
    "launcherSource": str(Path(__file__).resolve()),
    "launcherSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
}, sort_keys=True), file=sys.stderr, flush=True)
os.execv(java, argv)
