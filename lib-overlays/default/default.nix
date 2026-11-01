# SPDX-License-Identifier: MIT
#
# The `scala-cli-purifier` library namespace. It holds nothing: the builder
# needs a package set, so it is the package `buildScalaCliProject` that the
# default package overlay adds.
{ ... }:
{

  overlay = final: prev: {
    scala-cli-purifier = prev.scala-cli-purifier or { };
  };

}
