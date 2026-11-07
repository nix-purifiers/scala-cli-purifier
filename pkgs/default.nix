# SPDX-License-Identifier: MIT
#
# The scope `pkgs.scala-cli-purifier`, which the default package overlay
# adds to the package set. `scala-cli-purifier.overrideScope` replaces what
# its members share, and `.override` on one of them replaces what that one
# takes.
{ callPackage, lib, ... }:
{
  # Initial official JVM frontend; project compiler selection stays upstream.
  jvm-sdk = callPackage ./jvm-sdk { };

  # The scala-cli-purifier command, which locks, updates, prefetches and
  # publishes a project, and the scala-cli wrapper of the development loop.
  cli = callPackage ./cli { };

  # The builder (nix/lib/build-scala-cli-project.nix), with the purifier
  # library of this flake.
  buildScalaCliProject = callPackage ../nix/lib/build-scala-cli-project.nix {
    inherit (lib) purifier;
  };
}
