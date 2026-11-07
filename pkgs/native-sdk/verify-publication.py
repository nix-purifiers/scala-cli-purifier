#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verify genuine source publications; canonicalize ZIP containers, not contents."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

manifest = json.loads(Path(sys.argv[1]).read_text())
repository = Path(sys.argv[2])
output = Path(sys.argv[3])
output.mkdir()
ns = {"m": "http://maven.apache.org/POM/4.0.0"}
variant = manifest["variant"]
receipts = []
expected = {(item["module"], item["version"]) for item in manifest["publications"]}
for module, version in sorted(expected):
    relative = Path("org/scala-native") / module / version
    source = repository / relative
    jar = source / f"{module}-{version}.jar"
    pom = source / f"{module}-{version}.pom"
    if not jar.is_file() or not pom.is_file():
        raise RuntimeError(f"source publication missing: {module}:{version}")
    tree = ET.parse(pom).getroot()
    if (tree.findtext("m:groupId", namespaces=ns) != "org.scala-native"
            or tree.findtext("m:artifactId", namespaces=ns) != module
            or tree.findtext("m:version", namespaces=ns) != version):
        raise RuntimeError("generated POM identity differs from source variant")
    for dependency in tree.findall("m:dependencies/m:dependency", ns):
        group = dependency.findtext("m:groupId", namespaces=ns)
        scope = dependency.findtext("m:scope", default="compile", namespaces=ns)
        if group != "org.scala-native" or scope == "test":
            continue
        coordinate = (dependency.findtext("m:artifactId", namespaces=ns), dependency.findtext("m:version", namespaces=ns))
        if coordinate not in expected:
            raise RuntimeError(f"unclosed genuine Native publication dependency: {coordinate}")
    destination = output / "maven" / relative
    destination.mkdir(parents=True)
    for artifact in sorted(source.iterdir()):
        if artifact.suffix not in (".jar", ".pom"):
            continue
        target = destination / artifact.name
        original = artifact.read_bytes()
        if artifact.suffix == ".jar":
            with zipfile.ZipFile(io.BytesIO(original)) as before, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as after:
                if len(set(before.namelist())) != len(before.namelist()):
                    raise RuntimeError("duplicate source-produced JAR entry")
                for name in sorted(before.namelist()):
                    entry = before.getinfo(name)
                    stable = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    stable.create_system = 3
                    stable.external_attr = entry.external_attr
                    stable.compress_type = zipfile.ZIP_DEFLATED
                    after.writestr(stable, before.read(name))
            with zipfile.ZipFile(io.BytesIO(original)) as before, zipfile.ZipFile(target) as after:
                if any(before.read(name) != after.read(name) for name in before.namelist()):
                    raise RuntimeError("JAR entry content changed during ZIP canonicalization")
        else:
            shutil.copyfile(artifact, target)
        content = target.read_bytes()
        for algorithm in ("sha1", "sha256"):
            target.with_name(target.name + "." + algorithm).write_text(hashlib.new(algorithm, content).hexdigest() + "\n")
        receipts.append({"module": module, "version": version, "artifact": str(target.relative_to(output)), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), "pomBytesUnchanged": artifact.suffix == ".pom", "jarEntryBytesUnchanged": artifact.suffix == ".jar"})
(output / "publication.json").write_text(json.dumps({"variant": variant, "normalization": "ZIP container timestamps/order only; all JAR entry contents and generated POM bytes verified unchanged", "artifacts": receipts}, indent=2) + "\n")
