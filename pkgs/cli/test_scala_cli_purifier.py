# SPDX-License-Identifier: MIT
"""Tests of scala_cli_purifier's pure parts: argument parsing, packaging
options, the names of published files, and the checks `prefetch`, the scala-cli wrapper and
`publish` make before they run."""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import lockfile
import scala_cli_purifier as scp


class ProjectRuntimeTests(unittest.TestCase):
    def sdk(self, root, name, version="99.1", status=0):
        home = root / name
        (home / "bin").mkdir(parents=True)
        (home / "release").write_text(f'JAVA_VERSION="{version}"\n')
        java = home / "bin/java"
        java.write_text(
            f"#!{sys.executable}\nimport sys\n"
            f"print('    java.home = ' + {str(home)!r}, file=sys.stderr)\n"
            f"print('UnsupportedClassVersionError' if {status} else '    java.version = {version}', file=sys.stderr)\n"
            f"sys.exit({status})\n"
        )
        java.chmod(0o755)
        (home / "bin/keytool").touch()
        return home

    def test_installed_future_version_needs_no_maintainer_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.sdk(Path(tmp), "host")
            with mock.patch.dict(os.environ, {"JAVA_HOME": str(home)}, clear=True):
                self.assertEqual(scp.default_java_home(), str(home))
                self.assertEqual(scp.java_version(home), "99.1")

    def test_explicit_selection_precedes_ambient_java(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.sdk(Path(tmp), "host")
            explicit = self.sdk(Path(tmp), "explicit")
            with mock.patch.dict(os.environ, {"JAVA_HOME": str(home),
                    "SCALA_CLI_PURIFIER_JAVA_HOME": str(explicit)}, clear=True):
                self.assertEqual(scp.default_java_home(), str(explicit))
                self.assertEqual(scp.default_java_home(["--java-home", str(home)]), str(home))
                self.assertEqual(scp.default_java_home(["--java-home=" + str(home)]), str(home))
                self.assertEqual(scp.default_java_home(["--", "--java-home", "/application-argument"]), str(explicit))

    def test_path_launcher_reports_the_actual_runtime_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.sdk(Path(tmp), "host")
            launchers = Path(tmp) / "launchers"
            launchers.mkdir()
            (launchers / "java").symlink_to(home / "bin/java")
            with mock.patch.dict(os.environ, {"PATH": str(launchers)}, clear=True):
                self.assertEqual(scp.default_java_home(), str(home))

    def test_missing_host_does_not_select_a_nix_sdk(self):
        with mock.patch.dict(os.environ, {"PATH": "/does-not-exist"}, clear=True):
            with self.assertRaisesRegex(scp.PurifierError, "no project Java runtime"):
                scp.default_java_home()

    def test_invalid_explicit_host_never_falls_back_to_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.sdk(Path(tmp), "host")
            with mock.patch.dict(os.environ, {"PATH": str(home / "bin"),
                    "JAVA_HOME": "/invalid-host"}, clear=True):
                with self.assertRaisesRegex(scp.PurifierError, "cannot execute selected project Java"):
                    scp.default_java_home()

    def test_runtime_execution_error_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.sdk(Path(tmp), "host", status=1)
            with mock.patch.dict(os.environ, {"JAVA_HOME": str(home)}, clear=True):
                with self.assertRaisesRegex(scp.PurifierError, "UnsupportedClassVersionError"):
                    scp.default_java_home()

    def test_required_mirror_capability_has_a_precise_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self.sdk(Path(tmp), "host")
            (home / "bin/keytool").unlink()
            with mock.patch.dict(os.environ, {"JAVA_HOME": str(home)}, clear=True):
                with self.assertRaisesRegex(scp.PurifierError, "keytool.*offline mirror"):
                    scp.default_java_home()


class ReleaseSelectionTests(unittest.TestCase):
    def test_publish_passes_the_selected_initial_frontend_to_nix(self):
        environment = {
            "SCALA_CLI_PURIFIER_LIB": "/library",
            "SCALA_CLI_PURIFIER_SCALA_CLI_PACKAGE": "/nix/store/selected-jvm-sdk",
            "SCALA_CLI_PURIFIER_SCALA_CLI_VERSION": "1.18.0",
            "SCALA_CLI_PURIFIER_NIXPKGS": "/nixpkgs",
            "SCALA_CLI_PURIFIER_PURIFIER": "/purifier",
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(scp.subprocess, "run") as run:
            run.return_value.stdout = "/nix/store/release\n"
            self.assertEqual(scp.build_release(Path("/project"), Path("/project/lock")), Path("/nix/store/release"))
        arguments = run.call_args.args[0]
        index = arguments.index("scalaCliPath")
        self.assertEqual(arguments[index - 1:index + 5],
                         ["--argstr", "scalaCliPath", "/nix/store/selected-jvm-sdk", "--argstr", "scalaCliVersion", "1.18.0"])


class EnvironmentTests(unittest.TestCase):
    def test_bsp_socket_fits_in_a_github_runner_work_directory(self):
        work = Path("/home/runner/work/_temp/scala-cli-purifier-85i0hn5y/check-listings")
        with mock.patch.object(Path, "mkdir"), mock.patch.object(scp.sys, "platform", "linux"):
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

    def test_native_sdk_keeps_version_selection_and_caller_repository_order(self):
        configured = {"SCALA_CLI_PURIFIER_NATIVE_SDK_PACKAGE": "/nix/store/sdk"}
        with mock.patch.dict(os.environ, configured):
            options = scp.common_options("/jdk", "Native", offline=True)
            self.assertNotIn("--native-version", options)
            self.assertNotIn("--repository", options)
            caller = {"COURSIER_REPOSITORIES": "https://company.example/maven"}
            scp.configure_native_sdk(caller, "Native")
            self.assertEqual(caller["COURSIER_REPOSITORIES"],
                             "https://company.example/maven|file:///nix/store/sdk/maven")
            default = {}
            scp.configure_native_sdk(default, "Native")
            self.assertEqual(default["COURSIER_REPOSITORIES"],
                             "file:///nix/store/sdk/maven|https://repo1.maven.org/maven2")
            for platform in ("JVM", "JS"):
                other = {}
                scp.configure_native_sdk(other, platform)
                self.assertEqual(other, {})

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



class UnlockedWrapperTest(unittest.TestCase):
    def test_unlocked_delegation_preserves_arguments_streams_and_status(self):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = root / "native"
            native.write_text(
                "#!" + sys.executable + "\n"
                "import json, sys\n"
                "print(json.dumps(sys.argv[1:]))\n"
                "print('native stderr', file=sys.stderr)\n"
                "sys.exit(23)\n"
            )
            native.chmod(0o755)
            environment = dict(os.environ)
            environment['SCALA_CLI_PURIFIER_SCALA_CLI'] = str(native)
            environment["SCALA_CLI_PURIFIER_AS_SCALA_CLI"] = "1"
            arguments = ["build", "argument with spaces", "--example=a=b"]
            command = [sys.executable, str(Path(__file__).with_name('scala_cli_purifier.py')), *[], *arguments]
            result = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertEqual(json.loads(result.stdout), arguments)
            self.assertIn("warning:", result.stderr)
            self.assertIn("impurely", result.stderr)
            self.assertIn("native stderr", result.stderr)
            self.assertEqual([p.name for p in root.iterdir()], ["native"])

            # An existing malformed lock must never use this fallback.
            for name, contents in {}.items():
                (root / name).write_text(contents)
            (root / 'scala-cli-purifier.lock.json').write_text("{broken json")
            result = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 23)
            self.assertNotIn("native stderr", result.stderr)

if __name__ == "__main__":
    unittest.main()
