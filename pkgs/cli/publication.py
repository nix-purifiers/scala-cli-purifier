# SPDX-License-Identifier: MIT
"""Publishing a publication: a directory in Maven repository layout, as
`scala-cli publish` writes it in the Nix sandbox.

`upload` puts every file of the directory into a Maven repository: by HTTP
PUT for an http(s) URL, which Nexus, Artifactory, GitHub Packages and
reposilite accept, or by copying for a directory. `sign` adds a detached
ASCII-armored gpg signature (.asc) next to each file, and `write_bundle`
zips the directory, which is the bundle the Maven Central Portal takes.
"""

import base64
import hashlib
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

CHECKSUMS = (".md5", ".sha1", ".sha256", ".sha512")


class PublishError(Exception):
    pass


def files(directory):
    """Every file of a publication, as sorted relative paths with `/`."""
    directory = Path(directory)
    return sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file())


def add_checksums(directory):
    """Adds the .sha256 and .sha512 files Maven Central asks for next to
    each artifact; `scala-cli publish` writes .md5 and .sha1 itself."""
    directory = Path(directory)
    for rel in files(directory):
        if rel.endswith(CHECKSUMS) or rel.endswith(".asc"):
            continue
        data = (directory / rel).read_bytes()
        for ext, algorithm in ((".sha256", "sha256"), (".sha512", "sha512")):
            target = directory / (rel + ext)
            if not target.exists():
                target.write_text(hashlib.new(algorithm, data).hexdigest())


def sign(directory, key, gpg="gpg"):
    """Signs every artifact of a publication with gpg as `key`, writing
    <file>.asc next to it. Checksum files are not signed."""
    directory = Path(directory)
    for rel in files(directory):
        if rel.endswith(CHECKSUMS) or rel.endswith(".asc"):
            continue
        result = subprocess.run(
            [gpg, "--batch", "--yes", "--armor", "--detach-sign", "--local-user", key, str(directory / rel)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise PublishError(f"gpg could not sign {rel}: {result.stderr.strip()}")


def write_bundle(directory, bundle):
    """Writes a zip of a publication, with checksums of every algorithm
    Maven Central reads: the bundle its Portal API takes for upload."""
    add_checksums(directory)
    directory = Path(directory)
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files(directory):
            # Files from the Nix store carry 1970 as their time, which zip
            # cannot hold; every entry gets the earliest time zip holds, so
            # the bundle is the same bytes on every run.
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (directory / rel).read_bytes())


def upload(directory, repository, user=None, password=None):
    """Puts every file of a publication into `repository` and returns their
    relative paths. Files go in path order, so the descriptors and
    checksums of each version arrive with its artifacts."""
    directory = Path(directory)
    parsed = urllib.parse.urlsplit(repository)
    if parsed.scheme in ("http", "https"):
        base = repository.rstrip("/") + "/"
        headers = {}
        if user is not None:
            token = base64.b64encode(f"{user}:{password or ''}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        for rel in files(directory):
            data = (directory / rel).read_bytes()
            url = base + urllib.parse.quote(rel)
            request = urllib.request.Request(url, data=data, method="PUT", headers=headers)
            try:
                with urllib.request.urlopen(request) as response:
                    response.read()
            except urllib.error.HTTPError as e:
                e.close()
                raise PublishError(f"PUT {url} failed: {e.code} {e.reason}") from None
            except urllib.error.URLError as e:
                raise PublishError(f"PUT {url} failed: {e.reason}") from None
        return files(directory)
    if parsed.scheme == "file":
        target = Path(urllib.parse.unquote(parsed.path))
    elif parsed.scheme == "":
        target = Path(repository)
    else:
        raise PublishError(f"cannot publish to {repository}: the repository is an http(s) URL, a file: URL or a path")
    for rel in files(directory):
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(directory / rel, destination)
    return files(directory)
