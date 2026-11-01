# SPDX-License-Identifier: MIT
{ callPackage, ... }:
{
  # The scala-cli-purifier command, which locks a project and runs
  # scala-cli on its locked downloads.
  cli = callPackage ./cli { };
}
