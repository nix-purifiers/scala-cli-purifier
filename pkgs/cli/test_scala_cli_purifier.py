# SPDX-License-Identifier: MIT
"""Tests of scala_cli_purifier's pure parts: argument parsing, packaging
options, the names of published files, and the checks `prefetch`, the scala-cli wrapper and
`publish` make before they run."""

import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import lockfile
import scala_cli_purifier as scp


class EnvironmentTests(unittest.TestCase):
    def test_bsp_socket_fits_in_a_github_runner_work_directory(self):
        work = Path("/home/runner/work/_temp/scala-cli-purifier-85i0hn5y/check-listings")
        with mock.patch.object(Path, "mkdir"):
            env = scp.isolated_env(work, work / "coursier", offline=True)
        socket = Path(env["XDG_CACHE_HOME"]) / "scalacli/bsp-sockets/proc-2147483647"
        self.assertLessEqual(len(os.fsencode(socket)), 107)

    def test_long_bsp_socket_path_has_an_actionable_error(self):
        work = Path("/" + "w" * 72)
        with mock.patch.object(Path, "mkdir"):
            with self.assertRaisesRegex(scp.PurifierError, "TMPDIR"):
                scp.isolated_env(work, work / "coursier", offline=True)


class ArgumentTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(
            scp.parse_lock_args([]), ({"test": False, "publish": False, "ide": True, "name": None}, ["."])
        )

    def test_options_and_arguments(self):
        options, arguments = scp.parse_lock_args(
            ["--test", "--no-ide", "--name", "app", "--", "src", "--main-class", "app.Main"]
        )
        self.assertEqual(options, {"test": True, "publish": False, "ide": False, "name": "app"})
        self.assertEqual(arguments, ["src", "--main-class", "app.Main"])

    def test_name_needs_a_value(self):
        with self.assertRaises(scp.PurifierError):
            scp.parse_lock_args(["--name"])


class PackagingTests(unittest.TestCase):
    def test_package_options(self):
        out = Path("/out")
        self.assertEqual(scp.package_options("JVM", out), ["--assembly", "--preamble=false", "-o", "/out/program.jar"])
        self.assertEqual(scp.package_options("JS", out), ["-o", "/out/program.js"])
        self.assertEqual(scp.package_options("Native", out), ["-o", "/out/program"])

    def test_scala_js_links_on_the_jvm(self):
        self.assertIn("--js-cli-on-jvm", scp.common_options("/jdk", "JS", offline=True))
        self.assertNotIn("--js-cli-on-jvm", scp.common_options("/jdk", "JVM", offline=True))

    def test_published_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            version = Path(tmp, "org/example/lib_3/1.0.0")
            version.mkdir(parents=True)
            for name in ("lib_3-1.0.0.jar", "lib_3-1.0.0.jar.sha1", "lib_3-1.0.0-sources.jar", "lib_3-1.0.0.pom"):
                (version / name).write_text(name)
            self.assertEqual(
                [e["name"] for e in scp.published_files(tmp)],
                ["lib_3-sources.jar", "lib_3.jar", "lib_3.pom"],
            )


class ReadLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "Main.scala").write_text("//> using scala 3.3.4\n")
        os.environ["SCALA_CLI_PURIFIER_SCALA_CLI_VERSION"] = "1.18.0"

    def tearDown(self):
        self.tmp.cleanup()
        del os.environ["SCALA_CLI_PURIFIER_SCALA_CLI_VERSION"]

    def write(self, **changes):
        data = {
            "version": scp.LOCK_VERSION,
            "scalaCliVersion": "1.18.0",
            "arguments": ["."],
            "directivesHash": lockfile.directives_hash(self.root, ["."]),
            "artifacts": [],
            **changes,
        }
        data["checksum"] = lockfile.checksum(data)
        (self.root / scp.LOCK_FILE).write_text(json.dumps(data))

    def test_ok(self):
        self.write()
        scp.read_lock(self.root, self.root / scp.LOCK_FILE)

    def test_missing(self):
        with self.assertRaisesRegex(scp.PurifierError, "run `scala-cli-purifier lock` first"):
            scp.read_lock(self.root, self.root / scp.LOCK_FILE)

    def test_other_scala_cli(self):
        self.write(scalaCliVersion="1.0.0")
        with self.assertRaisesRegex(scp.PurifierError, "made with scala-cli 1.0.0.*scala-cli-purifier lock"):
            scp.read_lock(self.root, self.root / scp.LOCK_FILE)

    def test_other_format(self):
        self.write(version=99)
        with self.assertRaisesRegex(scp.PurifierError, "lock format 99"):
            scp.read_lock(self.root, self.root / scp.LOCK_FILE)

    def test_stale(self):
        self.write()
        (self.root / "Main.scala").write_text("//> using scala 3.3.4\n//> using dep a:b:1\n")
        with self.assertRaisesRegex(lockfile.LockfileError, "directives changed"):
            scp.read_lock(self.root, self.root / scp.LOCK_FILE)

    def test_outside_the_project(self):
        self.write()
        outside = Path(self.tmp.name) / "packaging"
        outside.mkdir()
        (self.root / scp.LOCK_FILE).rename(outside / scp.LOCK_FILE)
        scp.read_lock(self.root, outside / scp.LOCK_FILE)

    def test_not_prefetched(self):
        self.write()
        locked = scp.read_lock(self.root, self.root / scp.LOCK_FILE)
        with self.assertRaisesRegex(scp.PurifierError, "Run `scala-cli-purifier prefetch`"):
            scp.prefetched_cache(self.root / scp.LOCK_FILE, locked)

    def test_prefetched_for_another_lockfile(self):
        self.write()
        locked = scp.read_lock(self.root, self.root / scp.LOCK_FILE)
        state = self.root / scp.STATE_DIR
        (state / "downloads").mkdir(parents=True)
        (state / "prefetched").write_text("sha256-other\n")
        with self.assertRaisesRegex(scp.PurifierError, "Run `scala-cli-purifier prefetch`"):
            scp.prefetched_cache(self.root / scp.LOCK_FILE, locked)

    def test_prefetched(self):
        self.write()
        locked = scp.read_lock(self.root, self.root / scp.LOCK_FILE)
        state = self.root / scp.STATE_DIR
        jar = state / "downloads" / "https" / "repo" / "a.jar"
        jar.parent.mkdir(parents=True)
        jar.write_text("jar")
        (state / "prefetched").write_text(locked["checksum"] + "\n")
        cache = scp.prefetched_cache(self.root / scp.LOCK_FILE, locked)
        self.assertEqual((cache / "https" / "repo" / "a.jar").resolve(), jar.resolve())


class ListingTests(unittest.TestCase):
    SOURCECODE = "https/repo1.maven.org/maven2/com/lihaoyi/sourcecode_3/maven-metadata.xml"
    HICCUP = "https/repo.clojars.org/hiccup/hiccup/maven-metadata.xml"

    def test_module_of(self):
        self.assertEqual(scp.module_of(self.SOURCECODE), ("maven2/com/lihaoyi", "sourcecode_3"))
        self.assertEqual(scp.module_of(self.HICCUP), ("hiccup", "hiccup"))

    def test_selected(self):
        for spec in ["sourcecode", "com.lihaoyi::sourcecode", "com.lihaoyi:sourcecode_3"]:
            self.assertTrue(scp.selected(self.SOURCECODE, [spec]), spec)
        for spec in ["source", "org.other::sourcecode", "hiccup"]:
            self.assertFalse(scp.selected(self.SOURCECODE, [spec]), spec)

    def write_lock(self, root):
        data = {
            "artifacts": [
                {"path": self.SOURCECODE, "contents": "<sourcecode/>"},
                {"path": self.HICCUP, "contents": "<hiccup/>"},
                {"path": "https/repo1.maven.org/maven2/a/b/1/b-1.jar", "url": "u", "hash": "h"},
            ]
        }
        data["checksum"] = lockfile.checksum(data)
        path = root / scp.LOCK_FILE
        path.write_text(json.dumps(data))
        return path

    def test_kept_listings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_lock(Path(tmp))
            self.assertEqual(len(scp.kept_listings(path, None)), 2)
            self.assertEqual(scp.kept_listings(path, []), [])
            self.assertEqual([e["path"] for e in scp.kept_listings(path, ["sourcecode"])], [self.HICCUP])
            self.assertEqual(scp.kept_listings(Path(tmp) / "absent.json", None), [])

    def test_seed_through_a_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            mirror = "https://mirror.example/maven2"
            scp.seed_listings(cache, [{"path": self.SOURCECODE, "contents": "x"}], {scp.MAVEN_CENTRAL: [mirror]})
            listing = cache / "https/mirror.example/maven2/com/lihaoyi/sourcecode_3/maven-metadata.xml"
            self.assertEqual(listing.read_text(), "x")
            self.assertEqual(listing.with_name("maven-metadata.xml.sha1").read_text(), hashlib.sha1(b"x").hexdigest())
            self.assertTrue(listing.with_name(".maven-metadata.xml.checked").is_file())
            self.assertFalse((cache / self.SOURCECODE).exists())


class LockfileOptionTests(unittest.TestCase):
    def test_default(self):
        path, rest = scp.take_lockfile(["--test", "."])
        self.assertEqual(path, Path.cwd() / scp.LOCK_FILE)
        self.assertEqual(rest, ["--test", "."])

    def test_given(self):
        path, rest = scp.take_lockfile(["--lockfile", "/x/lock.json", "--test"])
        self.assertEqual(path, Path("/x/lock.json"))
        self.assertEqual(rest, ["--test"])

    def test_given_with_equals(self):
        path, rest = scp.take_lockfile(["--lockfile=/x/lock.json"])
        self.assertEqual(path, Path("/x/lock.json"))
        self.assertEqual(rest, [])

    def test_from_the_environment(self):
        os.environ["SCALA_CLI_PURIFIER_LOCKFILE"] = "/env/lock.json"
        try:
            self.assertEqual(scp.take_lockfile([])[0], Path("/env/lock.json"))
            self.assertEqual(scp.take_lockfile(["--lockfile", "/x/lock.json"])[0], Path("/x/lock.json"))
        finally:
            del os.environ["SCALA_CLI_PURIFIER_LOCKFILE"]

    def test_missing_value(self):
        with self.assertRaisesRegex(scp.PurifierError, "--lockfile needs a path"):
            scp.take_lockfile(["--lockfile"])


if __name__ == "__main__":
    unittest.main()
