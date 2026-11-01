# SPDX-License-Identifier: MIT
"""The files of a coursier cache, as lockfile download entries.

A purifier's lock run points COURSIER_CACHE at an empty directory, runs the
build tool, and turns every file the tool downloaded into an entry
{"path", "url", "mirrors", "hash"}: the file's place in the cache, the URL
coursier downloaded it from, the URLs of mirrors that serve the same file,
and its SRI sha256 hash. The Nix side fetches each URL
and places the file at its path, which rebuilds the cache. Version listings
the build needs become inline entries {"path", "contents"} instead.

The path rule is the one coursier uses (coursier.paths.CachePath.localFile),
as sbt-nix-backend's CachePaths.scala implements it. The scala-cli and Mill
purifiers share this module.
"""

import base64
import hashlib
import os
import urllib.parse
from pathlib import Path

# Characters coursier percent-escapes in cache paths, besides anything above
# 128.
_UNSAFE = set(" %$&+,:;=?@<>#")


def cache_path(url):
    """The cache-relative path coursier stores `url` at.

    Raises ValueError for a file: URL, which coursier reads in place rather
    than caching, and for a URL with no absolute path.
    """
    protocol, sep, rest = url.partition(":")
    if not sep or not protocol:
        raise ValueError(f"no protocol in URL {url}")
    if protocol == "file":
        raise ValueError(f"file: URLs are not cached by coursier: {url}")
    if rest.startswith("///"):
        rest = rest[3:]
    elif rest.startswith("/"):
        rest = rest[1:]
    else:
        raise ValueError(f"URL {url} does not contain an absolute path")
    if rest.endswith("/"):
        rest += ".directory"
    rest = rest.lstrip("/")
    return "".join(f"%{ord(c):02X}" if ord(c) > 128 or c in _UNSAFE else c for c in protocol + "/" + rest)


def url_of(path):
    """The URL a cache-relative path was downloaded from.

    It is the inverse of cache_path for every URL without credentials. A
    path whose host part carries `user@` came from an authenticated
    download, which a lockfile cannot reproduce, so it raises ValueError.
    """
    decoded = _unescape(path)
    protocol, sep, rest = decoded.partition("/")
    if not sep or not protocol:
        raise ValueError(f"not a coursier cache path: {path}")
    host = rest.split("/", 1)[0]
    if "@" in host:
        raise ValueError(f"{path} was downloaded with credentials; a lockfile cannot record those")
    if rest.endswith("/.directory"):
        rest = rest[: -len(".directory")]
    return f"{protocol}://{rest}"


def _unescape(path):
    # Coursier escapes a character as % and two hex digits of its character
    # code, not of its UTF-8 bytes, so each %XX decodes to one character.
    out = []
    i = 0
    while i < len(path):
        c = path[i]
        if c == "%":
            digits = path[i + 1 : i + 3]
            if len(digits) < 2:
                raise ValueError(f"truncated escape in {path}")
            try:
                out.append(chr(int(digits, 16)))
            except ValueError:
                raise ValueError(f"bad escape in {path}") from None
            i += 3
        else:
            out.append(c)
            i += 1
    return "".join(out)


def is_bookkeeping(name):
    """Whether a file name in a coursier cache is coursier's own bookkeeping.

    Checksums, `.checked` and `.error` markers and lock files are all
    dot-prefixed. The one dot-prefixed download is `.directory`, which holds
    the listing of a URL that ends in `/`.
    """
    return name.startswith(".") and name != ".directory"


def is_version_listing(path):
    """Whether a cache path is mutable metadata or a directory listing.

    A listing's content changes upstream whenever a version is published,
    so a hash pinned in a lockfile stops matching what the URL serves. A
    lock run leaves listings out unless the offline build needs them.
    """
    return Path(path).name == ".directory" or Path(path).name.startswith("maven-metadata")


CHECKSUM_SUFFIXES = (".sha1", ".md5", ".sha256", ".sha512")


def downloaded_files(cache_dir):
    """Every downloaded file under `cache_dir`, as sorted cache-relative
    paths with `/` separators.

    A checksum file (`<file>.sha1`, `.md5`, `.sha256`, `.sha512`) of
    another downloaded file is left out. Coursier keeps the checksums it
    fetches as plain files or as hidden bookkeeping files, depending on the
    repository and on whether it reached it through a mirror, and an
    offline build reads no checksum, since Nix checks each file by its
    SHA-256; leaving them out gives the same entries whichever way the lock
    run reached the repository.
    """
    found = set()
    for dirpath, _, filenames in os.walk(cache_dir):
        for name in filenames:
            if not is_bookkeeping(name):
                found.add(Path(dirpath, name).relative_to(cache_dir).as_posix())
    return sorted(
        p
        for p in found
        if not (p.endswith(CHECKSUM_SUFFIXES) and p.rsplit(".", 1)[0] in found)
    )


def sri_sha256(file):
    """The SRI sha256 hash of a file, such as `sha256-47DEQpj8...`."""
    digest = hashlib.sha256()
    with open(file, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return "sha256-" + base64.b64encode(digest.digest()).decode()


def canonical(path, mirrors=None):
    """The canonical cache path and URL of a downloaded file, and the URLs of
    its mirrors.

    `mirrors` maps the base URL of a repository, such as
    https://repo1.maven.org/maven2 for Maven Central, to the base URLs of
    mirrors that serve the same files. A file coursier downloaded from a
    mirror is recorded under the URL and cache path of the repository, which
    an offline build that names the repository reads, with the URL of each
    mirror as a fallback.
    """
    url = url_of(path)
    for base, alternates in (mirrors or {}).items():
        base = base.rstrip("/")
        alternates = [a.rstrip("/") for a in alternates]
        for prefix in [base, *alternates]:
            if url.startswith(prefix + "/"):
                rest = url[len(prefix) :]
                return cache_path(base + rest), base + rest, [a + rest for a in alternates]
    return path, url, []


def entries(cache_dir, paths=None, mirrors=None):
    """The lockfile entries of a cache, sorted by path.

    Each entry is a dict with `path`, `url`, `mirrors` and `hash`: the
    canonical cache path and URL (see `canonical`), the URLs of the mirrors
    that serve the same bytes, and the SRI hash. `paths` limits the entries
    to those cache-relative paths; the default is every downloaded file.
    """
    cache_dir = Path(cache_dir)
    selected = downloaded_files(cache_dir) if paths is None else paths
    result = []
    for p in selected:
        path, url, alternates = canonical(p, mirrors)
        result.append({"path": path, "url": url, "mirrors": alternates, "hash": sri_sha256(cache_dir / p)})
    return sorted(result, key=lambda e: e["path"])


def inline_entries(cache_dir, paths, mirrors=None):
    """Inline lockfile entries for `paths` of a cache, sorted by path.

    Each entry is a dict with `path`, the canonical cache path, and
    `contents`, the file's text. A lock run keeps version listings this way
    when the offline build needs them: the listing's URL serves new content
    once a version is published, so the lockfile records the listing the
    lock run read rather than a hash.
    """
    cache_dir = Path(cache_dir)
    result = [
        {"path": canonical(p, mirrors)[0], "contents": (cache_dir / p).read_text(encoding="utf-8")} for p in paths
    ]
    return sorted(result, key=lambda e: e["path"])


def java_proxy_properties(environ=None):
    """JVM -D options for the proxy that http_proxy and https_proxy name.

    The JVM refuses Basic authentication to a proxy tunnel unless
    jdk.http.auth.tunneling.disabledSchemes is cleared, so the options clear
    it whenever a proxy is set. Environment variables that name no proxy
    give no options.
    """
    environ = os.environ if environ is None else environ
    props = []
    for scheme in ("http", "https"):
        proxy = environ.get(f"{scheme}_proxy") or environ.get(f"{scheme.upper()}_PROXY")
        if not proxy:
            continue
        parsed = urllib.parse.urlsplit(proxy)
        if not parsed.hostname:
            continue
        props += [f"-D{scheme}.proxyHost={parsed.hostname}", f"-D{scheme}.proxyPort={parsed.port or 80}"]
        if parsed.username:
            props += [
                f"-D{scheme}.proxyUser={urllib.parse.unquote(parsed.username)}",
                f"-D{scheme}.proxyPassword={urllib.parse.unquote(parsed.password or '')}",
            ]
    no_proxy = environ.get("no_proxy") or environ.get("NO_PROXY")
    if props and no_proxy:
        hosts = "|".join(h.strip() for h in no_proxy.split(",") if h.strip())
        props.append(f"-Dhttp.nonProxyHosts={hosts}")
    if props:
        props += ["-Djdk.http.auth.tunneling.disabledSchemes=", "-Djdk.http.auth.proxying.disabledSchemes="]
    return props
