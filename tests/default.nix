# SPDX-License-Identifier: MIT
#
# The flake's checks. Every one builds in the sandbox; the only network
# access is the fixed-output fetches of locked artifacts.
{
  pkgs,
  # The flake's default package overlay, which adds buildScalaCliProject.
  overlay,
  # The scala-cli-purifier command, whose build runs its unit tests.
  cli,
}:
let
  inherit (pkgs) lib;

  # Every fixture builds the way a consumer's package does: through
  # buildScalaCliProject of a package set the overlay applies to. The
  # fixtures were locked with tests/relock-fixtures.sh.
  withOverlay = pkgs.extend overlay;
  inherit (withOverlay) buildScalaCliProject;

  # A Scala 3 project with a dependency and a munit suite, locked with
  # --test, so its build runs the suite.
  hello = buildScalaCliProject { src = ./fixtures/hello; };

  # A single-file Scala 2.13 program.
  script = buildScalaCliProject { src = ./fixtures/script; };

  # Runs the program of `package` and passes when its output holds every
  # line of `expected`.
  runs =
    name: package: expected:
    pkgs.runCommand name { } ''
      ${lib.getExe package} > output
      cat output
      ${lib.concatMapStrings (line: ''
        grep -qxF ${lib.escapeShellArg line} output || { echo "missing output line: ${line}" >&2; exit 1; }
      '') expected}
      mkdir -p $out
      ln -s ${package} $out/package
    '';
in
{
  inherit cli;

  hello-dependencies = hello.dependencies;
  hello-runs = runs "hello-runs" hello.package [ ''{"hello":"Nix World"}'' ];

  # The lock names the package; the package set's attribute is that name.
  script-runs = runs "script-runs" script.packages.script [ "script on Scala 2.13" ];
}
