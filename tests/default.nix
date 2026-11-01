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

  # A dependency from a repository besides Maven Central (Clojars), a jar
  # in the project, and a version range, whose version listings the lock
  # holds inline.
  sources = buildScalaCliProject { src = ./fixtures/sources; };

  # A dependency downloaded from a URL, whose existence coursier checks with
  # a HEAD request that the offline mirror answers in the sandbox.
  url = buildScalaCliProject { src = ./fixtures/url; };

  # Dependencies from ivy2Local and m2Local, which the build is given as
  # directories; the lock was made with those of fixtures/local-home.
  local = buildScalaCliProject {
    src = ./fixtures/local;
    localRepositories = {
      ivy2Local = ./fixtures/local-home/.ivy2/local;
      m2Local = ./fixtures/local-home/.m2/repository;
    };
  };

  # A Scala.js program, linked on the JVM and run on Node.js, whose munit
  # suite runs on Node.js in the build.
  js = buildScalaCliProject { src = ./fixtures/js; };

  # A Scala Native program, compiled and linked with clang, whose munit
  # suite runs in the build.
  native = buildScalaCliProject { src = ./fixtures/native; };

  # A library locked with --publish: its publication is what
  # `scala-cli publish` writes in the sandbox.
  library = buildScalaCliProject { src = ./fixtures/library; };

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

  libraryVersion = "io/github/nix-purifiers/example/greeting_3/1.0.0";

  # Nix knows every output of a project from its lockfile, before it builds:
  # the check requires that `build.artifacts` names exactly `expected`, then
  # builds each one and links it under its name.
  knownOutputs =
    name: build: expected:
    assert
      builtins.attrNames build.artifacts == lib.sort lib.lessThan expected
      || throw "${name}: the outputs are ${builtins.toJSON (builtins.attrNames build.artifacts)}";
    pkgs.runCommand name { } ''
      mkdir $out
      ${lib.concatStrings (
        lib.mapAttrsToList (key: file: ''
          test -f ${file} || { echo "${key}: ${file} is not a file" >&2; exit 1; }
          ln -s ${file} $out/${lib.escapeShellArg key}
        '') build.artifacts
      )}
    '';
in
{
  inherit cli;

  hello-dependencies = hello.dependencies;
  hello-runs = runs "hello-runs" hello.package [ ''{"hello":"Nix World"}'' ];

  # The lock names the package; the package set's attribute is that name.
  script-runs = runs "script-runs" script.packages.script [ "script on Scala 2.13" ];

  sources-runs = runs "sources-runs" sources.package [
    "hiccup from Clojars"
    "greeting from a local jar"
    "sourcecode from a version range: 1 + 1"
  ];

  url-runs = runs "url-runs" url.package [ "geny from a URL: 2,4,6" ];

  local-runs = runs "local-runs" local.package [
    "FROM IVY2LOCAL!"
    "from m2local..."
  ];

  js-runs = runs "js-runs" js.package [ "hello from Scala.js" ];

  native-runs = runs "native-runs" native.package [ "hello from Scala Native" ];

  # The outputs of each platform, and of a publication.
  hello-outputs = knownOutputs "hello-outputs" hello [
    "program"
    "hello.jar"
  ];
  js-outputs = knownOutputs "js-outputs" js [
    "program"
    "js.js"
  ];
  native-outputs = knownOutputs "native-outputs" native [ "program" ];
  library-outputs = knownOutputs "library-outputs" library [
    "program"
    "library.jar"
    "greeting_3.jar"
    "greeting_3-sources.jar"
    "greeting_3-javadoc.jar"
    "greeting_3.pom"
  ];

  # The publication holds the library's jar, sources, Scaladoc and POM with
  # their checksums, and the jar holds the library's classes.
  library-publication =
    pkgs.runCommand "library-publication"
      {
        nativeBuildInputs = [
          pkgs.unzip
          pkgs.libxml2
        ];
      }
      ''
        cd ${library.publication}/${libraryVersion}
        for f in greeting_3-1.0.0.jar greeting_3-1.0.0-sources.jar greeting_3-1.0.0-javadoc.jar greeting_3-1.0.0.pom; do
          test -f "$f" || { echo "the publication lacks $f" >&2; exit 1; }
          test "$(sha1sum < "$f" | cut -d' ' -f1)" = "$(cat "$f.sha1")" || { echo "$f.sha1 does not match" >&2; exit 1; }
        done
        unzip -l greeting_3-1.0.0.jar | grep -q 'greeting/Greeting\$package.class'
        test "$(xmllint --xpath 'string(/*[local-name()="project"]/*[local-name()="artifactId"])' greeting_3-1.0.0.pom)" = greeting_3
        mkdir -p $out
        ln -s ${library.publication} $out/publication
      '';
}
