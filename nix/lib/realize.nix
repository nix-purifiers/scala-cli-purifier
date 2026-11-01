# SPDX-License-Identifier: MIT
#
# What `scala-cli-purifier exec` builds, without nixpkgs.
#
# `fetches` is every fetched artifact; `nix-build -A fetches` downloads them
# in parallel. `manifest` is then a store file mapping each artifact's
# coursier-cache path to its store path. The file refers to every artifact,
# so one GC root on it keeps them all alive, and `exec` builds its writable
# cache directory from it.
#
# A file made with builtins.toFile may refer to store paths but not to
# derivation outputs, so the manifest names each fetched artifact through
# builtins.fetchurl: with the same name and hash, it is the same
# fixed-output store path a fetch derivation produces, and it is already
# valid once `fetches` is built, so nothing downloads twice. An inline
# entry is a builtins.toFile store path, which the manifest may name.
{
  lockFile,
  libDir ? ./.,
}:
let
  lock = import (libDir + "/lock.nix") { inherit lockFile; };
  fetch = import (libDir + "/fetch.nix") { inherit lock; };
  byPath =
    f: artifacts:
    builtins.listToAttrs (
      map (artifact: {
        name = artifact.path;
        value = f artifact;
      }) artifacts
    );
in
{
  fetches = byPath fetch lock.fetched;
  manifest = builtins.toFile "scala-cli-purifier-coursier-cache.json" (
    builtins.toJSON (
      byPath (
        artifact:
        builtins.fetchurl {
          inherit (artifact) url;
          name = lock.artifactName artifact;
          sha256 = artifact.hash;
        }
      ) lock.fetched
      // byPath lock.inlineFile lock.inlined
    )
  );
}
