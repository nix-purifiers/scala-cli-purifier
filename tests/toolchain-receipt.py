#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Query the actual executables selected for the default CLI and builder."""
import json
import subprocess
import sys

scala_cli, java = sys.argv[1:]
scala_version = subprocess.check_output(
    [scala_cli, "version", "--cli-version"], text=True, timeout=30).strip()
process = subprocess.run([java, "-XshowSettings:properties", "-version"],
                         text=True, capture_output=True, timeout=30, check=True)
properties = {}
for line in process.stderr.splitlines():
    key, separator, value = line.strip().partition(" = ")
    if separator and key in ("java.version", "java.runtime.version", "java.vendor", "java.home"):
        properties[key] = value
if not scala_version or any(not properties.get(key) for key in (
    "java.version", "java.runtime.version", "java.vendor", "java.home"
)):
    raise RuntimeError("selected executables did not report their versions")
print(json.dumps({"scalaCliVersion": scala_version,
                  "javaVersion": properties["java.version"],
                  "javaRuntimeVersion": properties["java.runtime.version"],
                  "javaVendor": properties["java.vendor"],
                  "javaHome": properties["java.home"],
                  "scalaCliExecutable": scala_cli, "javaExecutable": java}, indent=2))
