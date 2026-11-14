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
  # Local adoption resolves the project's ordinary upstream toolchain.
  # A source-built backend belongs to pure builds, or an explicit CLI
  # override, rather than the default capture/development closure.
  cli = callPackage ./cli { native-sdk = null; };

  # The builder (pkgs/build-scala-cli-project/default.nix), with the purifier
  # library of this flake.
  buildScalaCliProject = callPackage ./build-scala-cli-project {
    inherit (lib) purifier;
  };
}
