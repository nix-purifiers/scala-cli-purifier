# SPDX-License-Identifier: MIT
"""Tests of lockfile: the checksum, the hash of a project's directives, and
the errors for a malformed, a foreign, a hand-edited and a stale
lockfile."""

import json
import tempfile
import unittest
from pathlib import Path

import lockfile


def write_lock(path, data):
    data = dict(data)
    data["checksum"] = lockfile.checksum(data)
    path.write_text(json.dumps(data, indent=2))
    return data


class ChecksumTests(unittest.TestCase):
    def test_canonical_matches_nix_to_json(self):
        # builtins.toJSON sorts keys and writes no spaces.
        self.assertEqual(lockfile.canonical({"b": [1, True], "a": "x/y"}), '{"a":"x/y","b":[1,true]}')

    def test_checksum_ignores_its_own_field(self):
        data = {"version": 1, "artifacts": []}
        self.assertEqual(lockfile.checksum(data), lockfile.checksum({**data, "checksum": "anything"}))

    def test_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, lockfile.LOCK_FILE)
            data = write_lock(path, {"version": 1, "artifacts": [{"path": "a", "url": "https://x/a", "hash": "h"}]})
            self.assertEqual(lockfile.read(path), data)

    def test_edited(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, lockfile.LOCK_FILE)
            data = write_lock(path, {"version": 1, "artifacts": [{"path": "a", "url": "https://x/a", "hash": "h"}]})
            data["artifacts"][0]["hash"] = "tampered"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(lockfile.LockfileError, "was edited.*scala-cli-purifier lock"):
                lockfile.read(path)

    def test_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, lockfile.LOCK_FILE)
            path.write_text('{"version": 1, "artifacts": [')
            with self.assertRaisesRegex(lockfile.LockfileError, "not valid JSON.*scala-cli-purifier lock"):
                lockfile.read(path)

    def test_not_a_lockfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, lockfile.LOCK_FILE)
            path.write_text("[1, 2, 3]")
            with self.assertRaisesRegex(lockfile.LockfileError, "not a scala-cli-purifier lockfile"):
                lockfile.read(path)


class DirectivesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "project.scala").write_text("//> using scala 3.3.4\n//> using dep com.lihaoyi::os-lib:0.11.3\n")
        (self.root / "Main.scala").write_text("@main def main() = println(1)\n")
        build = self.root / ".scala-build"
        build.mkdir()
        (build / "Generated.scala").write_text("//> using dep ignored:ignored:1\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_code_changes_keep_the_hash(self):
        before = lockfile.directives_hash(self.root, ["."])
        (self.root / "Main.scala").write_text("@main def main() = println(2)\n")
        self.assertEqual(lockfile.directives_hash(self.root, ["."]), before)

    def test_build_output_is_skipped(self):
        before = lockfile.directives_hash(self.root, ["."])
        (self.root / ".scala-build" / "Generated.scala").write_text("//> using dep other:other:2\n")
        self.assertEqual(lockfile.directives_hash(self.root, ["."]), before)

    def test_a_new_dependency_is_stale(self):
        data = {"arguments": ["."], "directivesHash": lockfile.directives_hash(self.root, ["."])}
        lockfile.check_directives(self.root, data, lockfile.LOCK_FILE)
        (self.root / "Main.scala").write_text("//> using dep com.lihaoyi::pprint:0.9.0\n@main def main() = ()\n")
        with self.assertRaisesRegex(lockfile.LockfileError, "directives changed.*scala-cli-purifier lock"):
            lockfile.check_directives(self.root, data, lockfile.LOCK_FILE)

    def test_options_are_not_inputs(self):
        self.assertEqual(lockfile.source_files(self.root, ["--main-class", "x", "."]), ["Main.scala", "project.scala"])


if __name__ == "__main__":
    unittest.main()
