#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compare seeded relocks against an explicitly queried native SDK."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pkgs" / "cli"))
import lockfile


def provided_sdk_files(root):
    root = Path(root)
    publication_path = root / "receipts/publication.json"
    publication = json.loads(publication_path.read_text())
    distribution = json.loads((root / "receipts/distribution.json").read_text())
    if publication["variant"] != distribution["publicationVersion"]:
        raise ValueError("SDK publication and distribution versions differ")
    paths = set()
    for artifact in publication["artifacts"]:
        relative = artifact["artifact"]
        if not relative.startswith("maven/org/scala-native/") or ".." in Path(relative).parts:
            raise ValueError("SDK catalog names a file outside its Native repository")
        data = (root / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
            raise ValueError("provided SDK file differs from its publication receipt")
        paths.add(relative.removeprefix("maven/"))
    return paths, {"package": str(root), "distribution": distribution,
                   "publicationSha256": hashlib.sha256(publication_path.read_bytes()).hexdigest()}


def compare(original_path, relocked_path, selected, system, native_sdk=None):
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
    removed = []
    provided = None
    if native_sdk and original.get("platform") == "Native":
        paths, provided = provided_sdk_files(native_sdk)
        kept = []
        for artifact in expected["artifacts"]:
            relative = unquote(urlsplit(artifact["url"]).path).removeprefix("/maven2/")
            if relative in paths:
                removed.append(artifact["path"])
            else:
                kept.append(artifact)
        expected["artifacts"] = kept
        expected["checksum"] = lockfile.checksum(expected)
    if expected != relocked:
        changed = sorted(k for k in expected.keys() | relocked.keys() if expected.get(k) != relocked.get(k))
        raise ValueError("relock differs from the expected native lock in fields: " + ", ".join(changed))
    if not removed and (not system.endswith("-darwin") or original["javaVersion"] == selected["javaVersion"]) and Path(original_path).read_bytes() != Path(relocked_path).read_bytes():
        raise ValueError("relock is not byte identical")
    return {"recordedSdk": {"javaVersion": original["javaVersion"], "scalaCliVersion": original["scalaCliVersion"]},
            "selectedNativeToolchain": selected, "system": system,
            "allOtherFieldsExact": True, "bothChecksumsValidated": True,
            "providedNativeSDK": provided, "providedSDKDownloadsRemoved": removed}


if __name__ == "__main__":
    original, relocked, receipt, system, *sdk = sys.argv[1:]
    print(json.dumps(compare(original, relocked, json.loads(Path(receipt).read_text()), system,
                             sdk[0] if sdk and sdk[0] else None), sort_keys=True))
