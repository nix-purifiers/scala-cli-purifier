# SPDX-License-Identifier: MIT
"""Tests of publication: upload to a directory and to an HTTP repository,
gpg signing, and the Maven Central bundle."""

import base64
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

import publication

ARTIFACTS = [
    "org/example/lib_3/1.0.0/lib_3-1.0.0.jar",
    "org/example/lib_3/1.0.0/lib_3-1.0.0.jar.sha1",
    "org/example/lib_3/1.0.0/lib_3-1.0.0.pom",
    "org/example/lib_3/1.0.0/lib_3-1.0.0.pom.sha1",
]


def make_publication(root):
    for rel in ARTIFACTS:
        path = Path(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode())


class PutHandler(http.server.BaseHTTPRequestHandler):
    """A Maven repository that takes PUT with one user and records what it
    received."""

    received = {}
    expected_auth = "Basic " + base64.b64encode(b"me:secret").decode()

    def do_PUT(self):
        if self.headers.get("Authorization") != self.expected_auth:
            self.send_response(401)
            self.end_headers()
            return
        length = int(self.headers["Content-Length"])
        self.received[self.path] = self.rfile.read(length)
        self.send_response(201)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class UploadTests(unittest.TestCase):
    def test_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp, "pub"), Path(tmp, "repo")
            make_publication(source)
            self.assertEqual(publication.upload(source, str(target)), sorted(ARTIFACTS))
            self.assertEqual(Path(target, ARTIFACTS[2]).read_bytes(), ARTIFACTS[2].encode())

    def test_file_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp, "pub"), Path(tmp, "repo")
            make_publication(source)
            publication.upload(source, target.as_uri())
            self.assertTrue(Path(target, ARTIFACTS[0]).is_file())

    def test_http_put(self):
        PutHandler.received = {}
        server = http.server.HTTPServer(("127.0.0.1", 0), PutHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                make_publication(tmp)
                repo = f"http://127.0.0.1:{server.server_port}/maven/releases"
                publication.upload(tmp, repo, "me", "secret")
                self.assertEqual(
                    PutHandler.received,
                    {f"/maven/releases/{rel}": rel.encode() for rel in ARTIFACTS},
                )
                with self.assertRaises(publication.PublishError):
                    publication.upload(tmp, repo, "me", "wrong")
        finally:
            server.shutdown()

    def test_unknown_scheme(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(publication.PublishError):
                publication.upload(tmp, "s3://bucket/repo")


class BundleTests(unittest.TestCase):
    def test_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "pub")
            make_publication(source)
            # Files from the Nix store carry 1970 as their time.
            for rel in ARTIFACTS:
                os.utime(Path(source, rel), (1, 1))
            bundle = Path(tmp, "bundle.zip")
            publication.write_bundle(source, bundle)
            with zipfile.ZipFile(bundle) as z:
                names = set(z.namelist())
            self.assertIn("org/example/lib_3/1.0.0/lib_3-1.0.0.jar.sha256", names)
            self.assertIn("org/example/lib_3/1.0.0/lib_3-1.0.0.pom.sha512", names)
            self.assertNotIn("org/example/lib_3/1.0.0/lib_3-1.0.0.jar.sha1.sha256", names)


@unittest.skipUnless(shutil.which("gpg"), "gpg is not on the PATH")
class SignTests(unittest.TestCase):
    def test_sign(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp, "gnupg")
            home.mkdir(mode=0o700)
            env = dict(os.environ, GNUPGHOME=str(home))
            subprocess.run(
                ["gpg", "--batch", "--passphrase", "", "--quick-gen-key", "Test <test@example.com>", "ed25519", "sign", "never"],
                env=env,
                check=True,
                capture_output=True,
            )
            source = Path(tmp, "pub")
            make_publication(source)
            os.environ["GNUPGHOME"] = str(home)
            try:
                publication.sign(source, "test@example.com")
            finally:
                del os.environ["GNUPGHOME"]
            jar = Path(source, ARTIFACTS[0])
            self.assertTrue(Path(str(jar) + ".asc").is_file())
            self.assertFalse(Path(str(jar) + ".sha1.asc").exists())
            subprocess.run(["gpg", "--verify", str(jar) + ".asc", str(jar)], env=env, check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
