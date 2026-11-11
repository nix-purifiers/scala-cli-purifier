#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check exact relock expectations when the Native SDK becomes a Nix input."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

source = Path(sys.argv.pop(1))
spec = importlib.util.spec_from_file_location("relock_compare", source)
comparator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparator)


class RelockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sdk = self.root / "sdk"
        relative = "org/scala-native/nscplugin_3.3.4/0.5.12/nscplugin_3.3.4-0.5.12.jar"
        target = self.sdk / "maven" / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(b"source-built SDK")
        (self.sdk / "receipts").mkdir()
        publication = {"variant": "0.5.12", "artifacts": [{
            "artifact": "maven/" + relative,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }]}
        (self.sdk / "receipts/publication.json").write_text(json.dumps(publication))
        (self.sdk / "receipts/distribution.json").write_text(json.dumps({
            "publicationVersion": "0.5.12", "distribution": {"id": "owned-test"},
        }))
        sdk_entry = {"path": "https/repo1.maven.org/maven2/" + relative,
                     "url": "https://repo1.maven.org/maven2/" + relative, "hash": "old-sdk-hash"}
        ordinary = {"path": "https/repo1.maven.org/maven2/example/library/1/library-1.jar",
                    "url": "https://repo1.maven.org/maven2/example/library/1/library-1.jar", "hash": "locked-hash"}
        self.original = {"scalaCliVersion": "1.18.0", "javaVersion": "21.0.12.1",
                         "platform": "Native", "arguments": ["."], "artifacts": [sdk_entry, ordinary]}
        self.selected = {"scalaCliVersion": "1.18.0", "javaVersion": "21.0.12.1"}
        self.migrated = copy.deepcopy(self.original)
        self.migrated["artifacts"] = self.migrated["artifacts"][1:]

    def write(self, name, value):
        value = copy.deepcopy(value)
        value["checksum"] = comparator.lockfile.checksum(value)
        path = self.root / name
        path.write_text(json.dumps(value, indent=2) + "\n")
        return path

    def compare(self, relocked, original=None, sdk=True):
        return comparator.compare(self.write("original.json", original or self.original),
                                  self.write("relocked.json", relocked), self.selected,
                                  "aarch64-darwin", self.sdk if sdk else None)

    def test_only_published_sdk_downloads_leave_the_lock(self):
        result = self.compare(self.migrated)
        self.assertEqual(len(result["providedSDKDownloadsRemoved"]), 1)
        self.assertTrue(result["allOtherFieldsExact"])
        result = self.compare(self.migrated, original=self.migrated)
        self.assertEqual(result["providedSDKDownloadsRemoved"], [])

    def test_ordinary_artifact_loss_or_change_is_rejected(self):
        for artifacts in ([], [{**self.migrated["artifacts"][0], "hash": "wrong"}]):
            changed = {**self.migrated, "artifacts": artifacts}
            with self.assertRaisesRegex(ValueError, "artifacts"):
                self.compare(changed)

    def test_argument_changes_are_rejected(self):
        changed = {**self.migrated, "arguments": [".", "--native-version", "0.5.11"]}
        with self.assertRaisesRegex(ValueError, "arguments"):
            self.compare(changed)

    def test_existing_checksums_are_checked(self):
        original = self.write("original.json", self.original)
        relocked = self.write("relocked.json", self.migrated)
        data = json.loads(relocked.read_text())
        data["artifacts"] = []
        relocked.write_text(json.dumps(data))
        with self.assertRaises(comparator.lockfile.LockfileError):
            comparator.compare(original, relocked, self.selected, "aarch64-darwin", self.sdk)

    def test_provided_sdk_content_is_verified(self):
        next((self.sdk / "maven").rglob("*.jar")).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "publication receipt"):
            self.compare(self.migrated)

    def test_unconfigured_and_jvm_locks_remain_exact(self):
        with self.assertRaises(ValueError):
            self.compare(self.migrated, sdk=False)
        jvm = {**self.original, "platform": "JVM"}
        self.compare(jvm, original=jvm)
        with self.assertRaises(ValueError):
            self.compare({**jvm, "artifacts": self.migrated["artifacts"]}, original=jvm)


if __name__ == "__main__":
    unittest.main()
