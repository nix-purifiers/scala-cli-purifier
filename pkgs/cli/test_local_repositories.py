# SPDX-License-Identifier: MIT
"""Tests of local_repositories: finding a build's local repositories among
its resolvers, recording the files its class path reaches into, and
staging them with their hashes checked."""

import json
import tempfile
import unittest
from pathlib import Path

import local_repositories as lr

IVY_PATTERN = "[organisation]/[module]/(scala_[scalaVersion]/)(sbt_[sbtVersion]/)[revision]/[type]s/[artifact](-[classifier]).[ext]"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class RootsTests(unittest.TestCase):
    def test_roots(self):
        home = Path("/home/me")
        internal = Path("/tmp/run/home")
        resolvers = [
            "file:/home/me/.m2/repository",
            "https://repo1.maven.org/maven2",
            f"ivy:file:///tmp/run/home/.cache/scalacli/local-repo/1.18.0//{IVY_PATTERN}",
            f"ivy:file:/home/me/.ivy2/local/{IVY_PATTERN}",
            "file:///srv/shared/repository",
        ]
        found = lr.roots(resolvers, home, internal)
        # The roots are resolved: on macOS /home/me is
        # /System/Volumes/Data/home/me.
        self.assertEqual(
            [(r["name"], r["layout"], r["root"], r["pattern"]) for r in found],
            [
                ("m2Local", "maven", home.resolve() / ".m2/repository", ""),
                ("ivy2Local", "ivy", home.resolve() / ".ivy2/local", IVY_PATTERN),
                ("repository", "maven", Path("/srv/shared/repository").resolve(), ""),
            ],
        )


    def test_only_exact_configured_sdk_root_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk = root / "sdk/maven"
            sibling = root / "sdk/maven-extra"
            found = lr.roots(
                [sdk.as_uri(), sibling.as_uri(), (root / "home/.m2/repository").as_uri()],
                root / "home", root / "internal", provided=[sdk],
            )
            self.assertEqual([repo["root"] for repo in found],
                             [sibling.resolve(), (root / "home/.m2/repository").resolve()])


class RecordAndStageTests(unittest.TestCase):
    def test_record_and_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ivy = tmp / "home/.ivy2/local"
            m2 = tmp / "home/.m2/repository"
            write(ivy / "example/shout_3/0.1.0/jars/shout_3.jar", "ivy jar")
            write(ivy / "example/shout_3/0.1.0/ivys/ivy.xml", "<ivy-module/>")
            write(ivy / "example/unused_3/0.1.0/jars/unused_3.jar", "unused")
            write(m2 / "example/whisper_3/0.1.0/whisper_3-0.1.0.jar", "m2 jar")
            write(m2 / "example/whisper_3/0.1.0/whisper_3-0.1.0.pom", "<project/>")
            found = [
                {"name": "ivy2Local", "layout": "ivy", "root": ivy.resolve(), "pattern": IVY_PATTERN},
                {"name": "m2Local", "layout": "maven", "root": m2.resolve(), "pattern": ""},
            ]
            class_path = [
                str(ivy / "example/shout_3/0.1.0/jars/shout_3.jar"),
                str(m2 / "example/whisper_3/0.1.0/whisper_3-0.1.0.jar"),
                "/elsewhere/scala3-library_3-3.3.4.jar",
            ]
            entries = lr.record(found, class_path)
            self.assertEqual([e["name"] for e in entries], ["ivy2Local", "m2Local"])
            self.assertEqual(
                [f["path"] for f in entries[0]["files"]],
                ["example/shout_3/0.1.0/ivys/ivy.xml", "example/shout_3/0.1.0/jars/shout_3.jar"],
            )
            self.assertEqual(len(entries[1]["files"]), 2)

            spec = tmp / "spec.json"
            spec.write_text(
                json.dumps([{"name": "ivy2Local", "source": str(ivy), "target": "staged", "files": entries[0]["files"]}])
            )
            self.assertEqual(lr.main(["local_repositories.py", "stage", str(spec), str(tmp)]), 0)
            self.assertEqual((tmp / "staged/example/shout_3/0.1.0/jars/shout_3.jar").read_text(), "ivy jar")
            self.assertFalse((tmp / "staged/example/unused_3").exists())

            write(ivy / "example/shout_3/0.1.0/jars/shout_3.jar", "changed")
            with self.assertRaises(lr.LocalRepositoryError):
                lr.stage("ivy2Local", ivy, tmp / "again", entries[0]["files"])

    def test_resolver_for(self):
        repo = {"layout": "ivy", "pattern": IVY_PATTERN}
        self.assertEqual(lr.resolver_for(repo, "/build/local"), f"ivy:file:///build/local/{IVY_PATTERN}")
        self.assertEqual(lr.resolver_for({"layout": "maven", "pattern": ""}, "/build/m2"), "file:///build/m2")


if __name__ == "__main__":
    unittest.main()
