# SPDX-License-Identifier: MIT
"""Writes greeting.jar, the local jar of the sources fixture: a jar with one
resource, greeting.txt. The file times are fixed, so the jar is the same
bytes on every run."""

import zipfile
from pathlib import Path

here = Path(__file__).resolve().parent
with zipfile.ZipFile(here / "greeting.jar", "w", zipfile.ZIP_DEFLATED) as jar:
    for name, text in (("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n"), ("greeting.txt", "greeting from a local jar\n")):
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        jar.writestr(info, text)
