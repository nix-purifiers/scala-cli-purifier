# SPDX-License-Identifier: MIT
#
# What `scala-cli-purifier prefetch` builds: every file of the lockfile for
# this system at its coursier-cache path, as one tree of links into the
# store, made by the `tree` of the shared library with the nixpkgs the
# command was built with. The fetchurl of nixpkgs tries the mirrors of a
# file when its URL fails. The lockfile holds the build's downloads, the
# test dependencies and what an editor asks for, so after the prefetch the
# everyday loop and the editor work offline; the command keeps the tree
# alive with a GC root beside the lockfile.
#
# The command passes the arguments as strings:
#
#   nixpkgs          the nixpkgs source directory
#   purifierSource   the source of the shared library, at the revision of
#                    the command's flake.lock
#   src              the project root
#   lockFile         the lockfile
{
  nixpkgs,
  purifierSource,
  src,
  lockFile,
}:
let
  pkgs = import nixpkgs { };
  inherit (pkgs) lib;
  purifier = import purifierSource { inherit lib; };
  inherit (import ../../../lib-overlays/default/lock.nix { inherit lib; }) readLock;
  lock = readLock {
    src = /. + src;
    lockFile = /. + lockFile;
  };
in
purifier.tree {
  inherit pkgs;
  name = "scala-cli-purifier-downloads";
  entries = purifier.entriesFor pkgs.stdenv.buildPlatform.system lock;
}
