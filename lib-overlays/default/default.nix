# SPDX-License-Identifier: MIT
#
# The `scala-cli-purifier` library namespace. It holds `readLock`
# (nix/lib/lock.nix), which reads the lockfile of a project into the lock in
# memory that every nix-purifier shares. The builder needs a package set, so
# it is the package `buildScalaCliProject` that the default package overlay
# adds.
{ ... }:
{

  overlay = final: prev: {
    scala-cli-purifier = (prev.scala-cli-purifier or { }) // {
      inherit (import ../../nix/lib/lock.nix { lib = final; }) readLock;
    };
  };

}
