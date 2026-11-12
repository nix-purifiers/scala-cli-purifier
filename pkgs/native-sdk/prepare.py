#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verify immutable inputs and materialize only an owned offline repository."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile

manifest = json.loads(Path(sys.argv[1]).read_text())
root = Path(sys.argv[2]).resolve()
repository = root / "input-maven"
repository.mkdir()
(root / "output-maven").mkdir()
(root / "receipts").mkdir()
verified = []
for item in manifest["inputs"]:
    source = Path(item["storePath"])
    content = source.read_bytes()
    if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise RuntimeError(f"frozen input mismatch: {item['name']}")
    if item["kind"] == "source":
        destination = root / ("core" if item["repository"].endswith("/scala-native") else "native-cli")
        destination.mkdir()
        prefix = item["repository"].rsplit("/", 1)[1] + "-" + item["revision"] + "/"
        with tarfile.open(source) as archive:
            # data_filter rejects paths/links outside the owned extraction root.
            for member in archive.getmembers():
                if member.isdir() and member.name == prefix.rstrip("/"):
                    continue
                if not member.name.startswith(prefix):
                    raise RuntimeError("source archive prefix differs from pinned revision")
                member.name = member.name[len(prefix):]
                archive.extract(member, destination, filter="data")
    else:
        relative = Path(item["repositoryPath"])
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("invalid frozen Maven path")
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != content:
            raise RuntimeError("conflicting frozen Maven projection")
        shutil.copyfile(source, target)
        target.with_name(target.name + ".sha1").write_text(hashlib.sha1(content).hexdigest() + "\n")
        target.with_name(target.name + ".sha256").write_text(item["sha256"] + "\n")
    verified.append({key: item[key] for key in ("name", "sha256", "bytes", "kind")})
(root / "receipts" / "verified-inputs.json").write_text(json.dumps(verified, indent=2) + "\n")
(root / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
