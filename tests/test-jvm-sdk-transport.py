#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Execute the built transport with owned fake operands; no CLI parser claims."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import subprocess
import sys
import tempfile
import unittest

class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sdk = SDK
        cls.manifest = json.loads((SDK / "share/scala-cli-jvm-sdk/manifest.json").read_text())
        cls.source = (SDK / "bin/scala-cli").read_text()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        java = self.root / "owned fake java"
        java.write_text(f"#!{sys.executable}\n" + '''import os,sys,json,signal
if os.environ.get("PROBE_WAIT"):
    print("READY", flush=True)
    signal.pause()
else:
    keys = ["JAVA_HOME", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "JAVA_OPTS", "SCALA_CLI_PURIFIER_JAVA_HOME", "PATH"]
    print(json.dumps({"argv": sys.argv[1:], "environment": {k:os.environ.get(k) for k in keys}, "stdin": sys.stdin.read()}))
    print("probe stderr", file=sys.stderr)
    sys.exit(23)
''')
        java.chmod(0o755)
        self.jar = str(self.root / "owned fake jar.jar")
        # Only the fixed Java/JAR operands change; the built launcher and
        # its requested platform startup properties execute unchanged.
        source = self.source.replace(self.manifest["javaExecutable"], shlex.quote(str(java)))
        source = source.replace(self.manifest["jar"], shlex.quote(self.jar))
        self.launcher = self.root / "transport"
        self.launcher.write_text(source)
        self.launcher.chmod(0o755)
        self.environment = dict(os.environ, JAVA_HOME="/caller/compiler", JAVA_TOOL_OPTIONS="-Dcaller=one",
                                JDK_JAVA_OPTIONS="-Dcaller=two", _JAVA_OPTIONS="-Dcaller=three",
                                JAVA_OPTS="-Dcaller=four", SCALA_CLI_PURIFIER_JAVA_HOME="/caller/override",
                                PATH="/unusable/host/path")

    def test_manifest_matches_exact_initial_launcher_and_payload(self):
        self.assertEqual(hashlib.sha256(self.source.encode()).hexdigest(), self.manifest["launcherSha256"])
        self.assertEqual(hashlib.sha256(Path(self.manifest["jar"]).read_bytes()).hexdigest(), self.manifest["payloadHash"])
        self.assertEqual(self.manifest["scope"], "initial-frontend-transport")
        requested = self.manifest["requestedStartupProperties"]
        self.assertEqual(requested, {"java.net.preferIPv4Stack": "true"} if self.manifest["platform"].endswith("-darwin") else {})

    def test_all_arguments_environment_streams_and_exit_are_preserved(self):
        args = ["--cli-version", "other-version", "--power", "-Djava.home=/caller", "compile", "--scalac-option",
                "--", "--jvm", "17", "--", "-j21", "@args file", "", "a b", "-Dduplicate=1", "-Dduplicate=2"]
        process = subprocess.Popen([str(self.launcher), *args], env=self.environment,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate("input bytes", timeout=5)
        self.assertEqual(process.returncode, 23)
        self.assertEqual(stderr, "probe stderr\n")
        value = json.loads(stdout)
        startup = ["-D" + k + "=" + v for k, v in self.manifest["requestedStartupProperties"].items()]
        self.assertEqual(value["argv"], [*startup, "-jar", self.jar, *args])
        self.assertEqual(value["stdin"], "input bytes")
        self.assertEqual(value["environment"], {k:self.environment[k] for k in value["environment"]})

    def test_signal_reaches_the_exec_process(self):
        process = subprocess.Popen([str(self.launcher), "version"], env=dict(self.environment, PROBE_WAIT="1"),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=5))
            self.assertEqual(process.stdout.readline(), "READY\n")
            process.terminate()
            self.assertEqual(process.wait(timeout=5), -signal.SIGTERM)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--sdk", required=True, type=Path)
    options, test_args = parser.parse_known_args()
    SDK = options.sdk.resolve()
    unittest.main(argv=[sys.argv[0], *test_args])
