# SPDX-License-Identifier: MIT
"""Pass scala-cli's native classpath to the shared repository exporter."""
import os
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
import coursier_runtime

classpath, workspace, cache, catalog, destination = map(Path, sys.argv[2:])
lines = classpath.read_text().strip().splitlines()
if not lines:
    raise ValueError('scala-cli did not print its native classpath')
paths = []
for entry in lines[-1].split(os.pathsep):
    path = Path(entry)
    # The project's own classes are published separately. An unmanaged
    # class directory elsewhere still requires an explicit projection.
    if path.is_dir() and path.is_relative_to(workspace):
        continue
    paths.append(entry)
coursier_runtime.export(cache, paths, catalog, destination)
