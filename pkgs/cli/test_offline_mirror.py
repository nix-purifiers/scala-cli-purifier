# SPDX-License-Identifier: MIT
"""Tests of offline_mirror: HEAD and GET through a TLS tunnel, answered
from a coursier cache, with the certificate authority the mirror made."""

import http.client
import json
import shutil
import ssl
import tempfile
import unittest
from pathlib import Path

import offline_mirror

URL = "https://repo1.maven.org/maven2/com/example/lib/1.0/lib-1.0.jar"


@unittest.skipUnless(shutil.which("openssl") and shutil.which("keytool"), "openssl or keytool is not on the PATH")
class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cache = root / "cache"
        file = self.cache / "https/repo1.maven.org/maven2/com/example/lib/1.0/lib-1.0.jar"
        file.parent.mkdir(parents=True)
        file.write_bytes(b"jar bytes")
        self.logged = []
        options, self.server = offline_mirror.start(
            self.cache, root / "state", ["repo1.maven.org"], shutil.which("keytool"), self.logged.append
        )
        self.port = int(next(o for o in options if o.startswith("-Dhttps.proxyPort=")).split("=")[1])
        self.context = ssl.create_default_context(cafile=str(root / "state" / "ca.crt"))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path):
        connection = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.context)
        connection.set_tunnel("repo1.maven.org", 443)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_head_and_get(self):
        path = URL.removeprefix("https://repo1.maven.org")
        self.assertEqual(self.request("HEAD", path), (200, b""))
        self.assertEqual(self.request("GET", path), (200, b"jar bytes"))
        events = [json.loads(line.removeprefix("offline mirror trace: "))
                  for line in self.logged if line.startswith("offline mirror trace: ")]
        self.assertTrue(any(e.get("event") == "TCP-accepted" for e in events))
        self.assertTrue(any(e.get("event") == "TLS-established" for e in events))
        self.assertTrue(any(e.get("event") == "HEAD" and e.get("status") == 200 for e in events))
        self.assertTrue(any(e.get("event") == "GET" and e.get("status") == 200 for e in events))

    def test_diagnostics_redact_and_bound(self):
        # Plain proxy URLs can carry credentials and query strings. They
        # still resolve by their original identity; diagnostics hash it.
        secret_url = "https://user:password@repo1.maven.org/private.jar?token=secret#fragment"
        offline_mirror.Handler.aliases[secret_url] = "https/repo1.maven.org/maven2/com/example/lib/1.0/lib-1.0.jar"
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request("GET", secret_url, headers={"Authorization": "Bearer private-header"})
        response = connection.getresponse()
        self.assertEqual((response.status, response.read()), (200, b"jar bytes"))
        connection.close()
        for secret in ["user", "password", "token", "secret", "fragment", "private-header"]:
            self.assertNotIn(secret, "\n".join(self.logged))
        self.server.trace_count = 64
        count = len(self.logged)
        self.assertEqual(self.request("HEAD", "/missing?private=query"), (404, b""))
        self.assertEqual(len(self.logged), count + 1)  # Original cache-miss diagnostic only.
        self.assertNotIn("private=query", "\n".join(self.logged))

    def test_alias(self):
        # A mirror URL whose file the cache holds under the canonical path.
        mirrored = "/maven2/com/example/lib/1.0/lib-1.0.jar?mirror"
        offline_mirror.Handler.aliases["https://repo1.maven.org" + mirrored] = (
            "https/repo1.maven.org/maven2/com/example/lib/1.0/lib-1.0.jar"
        )
        self.assertEqual(self.request("GET", mirrored), (200, b"jar bytes"))

    def test_lock_aliases(self):
        artifacts = [
            {"path": "p", "url": "https://a/x", "mirrors": ["https://b/x"], "hash": "h"},
            {"path": "q", "contents": "listing"},
        ]
        self.assertEqual(offline_mirror.lock_aliases(artifacts), {"https://a/x": "p", "https://b/x": "p"})

    def test_unlocked_url(self):
        self.assertEqual(self.request("HEAD", "/maven2/com/example/other/1.0/other-1.0.jar")[0], 404)
        self.assertTrue(any("is not in the locked downloads" in line for line in self.logged))

    def test_unknown_host(self):
        connection = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.context)
        connection.set_tunnel("elsewhere.example.com", 443)
        with self.assertRaises(OSError):
            connection.request("HEAD", "/")
        connection.close()


if __name__ == "__main__":
    unittest.main()
