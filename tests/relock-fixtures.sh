#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Re-locks the test fixtures with the scala-cli-purifier command of this
# checkout. Run from the repository root; it needs the network.
set -euo pipefail
cli=$(nix build --no-link --print-out-paths '.#cli')/bin/scala-cli-purifier
(cd tests/fixtures/hello && "$cli" lock --test)
(cd tests/fixtures/script && "$cli" lock)
