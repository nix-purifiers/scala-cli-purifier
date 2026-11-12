# SPDX-License-Identifier: MIT
#
# The scope `pkgs.scala-cli-purifier`, which the default package overlay
# adds to the package set. `scala-cli-purifier.overrideScope` replaces what
# its members share, and `.override` on one of them replaces what that one
# takes.
{ callPackage, lib, ... }:
{
  # An optional source-built Native backend supplied by the Nix scope.
  native-sdk = null;

  # Initial official JVM frontend; project compiler selection stays upstream.
  jvm-sdk = callPackage ./jvm-sdk { };

  # The scala-cli-purifier command, which locks, updates, prefetches and
  # publishes a project, and the scala-cli wrapper of the development loop.
  cli = callPackage ./cli { };

  # The builder (pkgs/build-scala-cli-project/default.nix), with the purifier
  # library of this flake.
  buildScalaCliProject = callPackage ./build-scala-cli-project {
    inherit (lib) purifier;
  };
}
