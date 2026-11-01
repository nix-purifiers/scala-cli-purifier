# SPDX-License-Identifier: MIT
#
# The `scala-cli-purifier` library namespace: the builder,
# lib.scala-cli-purifier.buildScalaCliProject (nix/lib/build-scala-cli-project.nix),
# on the library the nix-purifiers share, lib.purifier.
{ ... }:
{

  overlay = final: prev: {
    scala-cli-purifier = (prev.scala-cli-purifier or { }) // {
      buildScalaCliProject = import ../../nix/lib/build-scala-cli-project.nix {
        lib = final;
        inherit (final) purifier;
      };
    };
  };

}
