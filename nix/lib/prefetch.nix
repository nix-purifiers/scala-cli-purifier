# SPDX-License-Identifier: MIT
#
# What `scala-cli-purifier prefetch` builds: every file of the lockfile at
# its coursier-cache path, as one tree of links into the store, made by the
# `tree` of the shared library with the nixpkgs the command was built with.
# nixpkgs' fetchurl tries each file's mirrors when its URL fails. The
# lockfile holds the build's downloads, the test dependencies and what an
# editor asks for, so after the prefetch the everyday loop and the editor
# work offline; the command keeps the tree alive with a GC root in the
# project.
#
# The command passes:
#
#   nixpkgs          the nixpkgs source directory
#   purifierSource   the source of the shared library, at the revision of
#                    the command's flake.lock
#   lockFile         the lockfile
{
  nixpkgs,
  purifierSource,
  lockFile,
}:
let
  pkgs = import nixpkgs { };
  purifier = import purifierSource { inherit (pkgs) lib; };
  lock = import ./lock.nix { inherit lockFile; };
in
purifier.tree {
  inherit pkgs;
  name = "scala-cli-purifier-downloads";
  entries = lock.artifacts;
}
