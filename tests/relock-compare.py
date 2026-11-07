#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compare seeded relocks against an explicitly queried native SDK."""
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pkgs" / "cli"))
import lockfile


def compare(original_path, relocked_path, selected, system):
    original = lockfile.read(original_path)
    relocked = lockfile.read(relocked_path)
    if selected["scalaCliVersion"] != original["scalaCliVersion"]:
        raise ValueError("selected Scala CLI differs from the recorded CLI")
    if relocked["javaVersion"] != selected["javaVersion"]:
        raise ValueError("relock SDK differs from the queried native Java executable")
    expected = copy.deepcopy(original)
    if system.endswith("-darwin"):
        if original["javaVersion"].split(".")[0] != selected["javaVersion"].split(".")[0]:
            raise ValueError("native Java major differs from the recorded Java major")
        expected["javaVersion"] = selected["javaVersion"]
        expected["checksum"] = lockfile.checksum(expected)
    if expected != relocked:
        changed = sorted(k for k in expected.keys() | relocked.keys() if expected.get(k) != relocked.get(k))
        raise ValueError("relock differs from the expected native lock in fields: " + ", ".join(changed))
    if (not system.endswith("-darwin") or original["javaVersion"] == selected["javaVersion"]) and Path(original_path).read_bytes() != Path(relocked_path).read_bytes():
        raise ValueError("relock is not byte identical")
    return {"recordedSdk": {"javaVersion": original["javaVersion"], "scalaCliVersion": original["scalaCliVersion"]},
            "selectedNativeToolchain": selected, "system": system,
            "allOtherFieldsExact": True, "bothChecksumsValidated": True}


if __name__ == "__main__":
    original, relocked, receipt, system = sys.argv[1:]
    print(json.dumps(compare(original, relocked, json.loads(Path(receipt).read_text()), system), sort_keys=True))
