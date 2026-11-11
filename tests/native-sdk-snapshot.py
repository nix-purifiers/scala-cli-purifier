#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Retain the bounded source publication for cross-job byte comparisons."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

sdk = Path(sys.argv[1]).resolve()
destination = Path(sys.argv[2]).resolve()
publication = json.loads((sdk / "receipts/publication.json").read_text())
artifacts = publication["artifacts"]
if len(artifacts) != 88 or sum(item["bytes"] for item in artifacts) > 100_000_000:
    raise ValueError("Native publication exceeds the reviewed diagnostic bounds")
destination.mkdir(parents=True, exist_ok=False)
for item in artifacts:
    relative = Path(item["artifact"])
    if relative.is_absolute() or ".." in relative.parts or relative.parts[:3] != ("maven", "org", "scala-native"):
        raise ValueError("publication file is outside the Native Maven repository")
    source = sdk / relative
    data = source.read_bytes()
    if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("publication bytes differ from their receipt")
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
(destination / "receipts").mkdir()
for name in ("publication.json", "distribution.json"):
    data = (sdk / "receipts" / name).read_bytes()
    if len(data) > 1_000_000:
        raise ValueError("SDK receipt exceeds the diagnostic size bound")
    (destination / "receipts" / name).write_bytes(data)
identity = {
    "package": str(sdk),
    "derivation": subprocess.check_output(["nix-store", "-q", "--deriver", str(sdk)], text=True).strip(),
    "narHash": subprocess.check_output(["nix", "hash", "path", str(sdk)], text=True).strip(),
    "publicationSha256": hashlib.sha256((sdk / "receipts/publication.json").read_bytes()).hexdigest(),
    "artifactCount": len(artifacts),
    "artifactBytes": sum(item["bytes"] for item in artifacts),
}
(destination / "identity.json").write_text(json.dumps(identity, indent=2) + "\n")
print(json.dumps(identity, sort_keys=True))
