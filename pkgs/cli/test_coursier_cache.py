# SPDX-License-Identifier: MIT
"""Tests of coursier_cache, with URL and cache path pairs that coursier
produces."""

import tempfile
import unittest
from pathlib import Path

import coursier_cache as cc

# (URL, cache-relative path) pairs. Coursier escapes ":" of a port, "+",
# and characters above 128, and stores the listing of a URL ending in "/"
# as ".directory".
VECTORS = [
    (
        "https://repo1.maven.org/maven2/org/scala-lang/scala3-library_3/3.3.4/scala3-library_3-3.3.4.jar",
        "https/repo1.maven.org/maven2/org/scala-lang/scala3-library_3/3.3.4/scala3-library_3-3.3.4.jar",
    ),
    (
        "https://repo.example.com:8443/maven/a/b/1.0/b-1.0.pom",
        "https/repo.example.com%3A8443/maven/a/b/1.0/b-1.0.pom",
    ),
    (
        "https://repo1.maven.org/maven2/org/example/lib/1.0+build.5/lib-1.0+build.5.jar",
        "https/repo1.maven.org/maven2/org/example/lib/1.0%2Bbuild.5/lib-1.0%2Bbuild.5.jar",
    ),
    (
        "https://repo.example.com/ivy/org.example/lib/",
        "https/repo.example.com/ivy/org.example/lib/.directory",
    ),
    (
        "https://repo.example.com/maven/café/1.0/café-1.0.jar",
        "https/repo.example.com/maven/caf%E9/1.0/caf%E9-1.0.jar",
    ),
    (
        "http://insecure.example.com/a b/c.jar",
        "http/insecure.example.com/a%20b/c.jar",
    ),
]


class PathTests(unittest.TestCase):
    def test_cache_path(self):
        for url, path in VECTORS:
            self.assertEqual(cc.cache_path(url), path, url)

    def test_url_of(self):
        for url, path in VECTORS:
            self.assertEqual(cc.url_of(path), url, path)

    def test_credentials_refused(self):
        with self.assertRaises(ValueError):
            cc.url_of("https/user%40repo.example.com/a/b.jar")

    def test_file_urls_refused(self):
        with self.assertRaises(ValueError):
            cc.cache_path("file:///home/me/.m2/repository/a.jar")

    def test_bookkeeping(self):
        self.assertTrue(cc.is_bookkeeping(".b-1.0.jar.checked"))
        self.assertTrue(cc.is_bookkeeping(".b-1.0.pom__sha1"))
        self.assertTrue(cc.is_bookkeeping(".lock"))
        self.assertFalse(cc.is_bookkeeping(".directory"))
        self.assertFalse(cc.is_bookkeeping("b-1.0.jar"))

    def test_version_listing(self):
        self.assertTrue(cc.is_version_listing("https/repo1.maven.org/maven2/a/b/maven-metadata.xml"))
        self.assertFalse(cc.is_version_listing("https/repo1.maven.org/maven2/a/b/1.0/b-1.0.pom"))


class EntriesTests(unittest.TestCase):
    def test_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            jar = cache / "https/repo1.maven.org/maven2/a/b/1.0/b-1.0.jar"
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b"")
            (jar.parent / ".b-1.0.jar.checked").write_bytes(b"")
            self.assertEqual(
                cc.entries(cache),
                [
                    {
                        "path": "https/repo1.maven.org/maven2/a/b/1.0/b-1.0.jar",
                        "url": "https://repo1.maven.org/maven2/a/b/1.0/b-1.0.jar",
                        "mirrors": [],
                        # The SRI sha256 of the empty file.
                        "hash": "sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=",
                    }
                ],
            )


    def test_checksums_of_downloads_are_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            version = cache / "https/maven.google.com/androidx/annotation/annotation-jvm/1.9.1"
            version.mkdir(parents=True)
            for name in (
                "annotation-jvm-1.9.1.jar",
                "annotation-jvm-1.9.1.jar.sha1",
                "annotation-jvm-1.9.1.jar.md5",
                "annotation-jvm-1.9.1.pom.sha256",
            ):
                (version / name).write_bytes(b"")
            self.assertEqual(
                [Path(p).name for p in cc.downloaded_files(cache)],
                # A checksum whose file was not downloaded stays.
                ["annotation-jvm-1.9.1.jar", "annotation-jvm-1.9.1.pom.sha256"],
            )

    def test_mirrored_entries(self):
        central = "https://repo1.maven.org/maven2"
        google = "https://maven-central.storage-download.googleapis.com/maven2"
        mirrors = {central: [google]}
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            mirrored = cache / "https/maven-central.storage-download.googleapis.com/maven2/a/b/1.0/b-1.0.jar"
            mirrored.parent.mkdir(parents=True)
            mirrored.write_bytes(b"")
            direct = cache / "https/repo1.maven.org/maven2/a/c/1.0/c-1.0.jar"
            direct.parent.mkdir(parents=True)
            direct.write_bytes(b"")
            other = cache / "https/repo.clojars.org/x/y/1.0/y-1.0.jar"
            other.parent.mkdir(parents=True)
            other.write_bytes(b"")
            result = cc.entries(cache, mirrors=mirrors)
            self.assertEqual(
                [(e["path"], e["url"], e["mirrors"]) for e in result],
                [
                    (
                        "https/repo.clojars.org/x/y/1.0/y-1.0.jar",
                        "https://repo.clojars.org/x/y/1.0/y-1.0.jar",
                        [],
                    ),
                    (
                        "https/repo1.maven.org/maven2/a/b/1.0/b-1.0.jar",
                        f"{central}/a/b/1.0/b-1.0.jar",
                        [f"{google}/a/b/1.0/b-1.0.jar"],
                    ),
                    (
                        "https/repo1.maven.org/maven2/a/c/1.0/c-1.0.jar",
                        f"{central}/a/c/1.0/c-1.0.jar",
                        [f"{google}/a/c/1.0/c-1.0.jar"],
                    ),
                ],
            )

    def test_inline_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            listing = cache / "https/repo1.maven.org/maven2/a/b/maven-metadata.xml"
            listing.parent.mkdir(parents=True)
            listing.write_text("<metadata/>\n")
            self.assertEqual(
                cc.inline_entries(cache, ["https/repo1.maven.org/maven2/a/b/maven-metadata.xml"]),
                [{"path": "https/repo1.maven.org/maven2/a/b/maven-metadata.xml", "contents": "<metadata/>\n"}],
            )


class ProxyTests(unittest.TestCase):
    def test_authenticated_proxy(self):
        props = cc.java_proxy_properties({"https_proxy": "http://me:p%40ss@proxy.example.com:3128"})
        self.assertIn("-Dhttps.proxyHost=proxy.example.com", props)
        self.assertIn("-Dhttps.proxyPort=3128", props)
        self.assertIn("-Dhttps.proxyUser=me", props)
        self.assertIn("-Dhttps.proxyPassword=p@ss", props)
        self.assertIn("-Djdk.http.auth.tunneling.disabledSchemes=", props)

    def test_no_proxy(self):
        self.assertEqual(cc.java_proxy_properties({}), [])


if __name__ == "__main__":
    unittest.main()
