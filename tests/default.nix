# SPDX-License-Identifier: MIT
#
# The flake's checks. Every one builds in the sandbox; the only network
# access is the fixed-output fetches of locked artifacts. The checks named
# conformance-* are those of the purifier contract (CONTRACT.md of the
# shared library); conformance-relock needs the network, so CI runs it as a
# job of that name (.github/workflows/check.yml).
{
  pkgs,
  # The flake's default package overlay, which adds the scope
  # pkgs.scala-cli-purifier with buildScalaCliProject.
  overlay,
  # The flake's library: lib.scala-cli-purifier.readLock, and the shared
  # library as lib.purifier.
  flakeLib,
  # The scala-cli-purifier command, whose build runs its unit tests.
  cli,
}:
let
  inherit (pkgs) lib;

  # Every fixture builds the way a consumer's package does: through the
  # builder that the overlay adds to a package set. The fixtures were
  # locked with tests/relock-fixtures.sh.
  withOverlay = pkgs.extend overlay;
  build = withOverlay.scala-cli-purifier.buildScalaCliProject;

  # A Scala 3 project with a dependency and a munit suite, locked with
  # --test.
  hello = build { src = ./fixtures/hello; };

  # A single-file Scala 2.13 program.
  script = build { src = ./fixtures/script; };

  # A dependency from a repository besides Maven Central (Clojars), a jar
  # in the project, and a version range, whose version listings the lock
  # holds inline.
  sources = build { src = ./fixtures/sources; };

  # A dependency downloaded from a URL, whose existence coursier checks with
  # a HEAD request that the offline mirror answers in the sandbox.
  url = build { src = ./fixtures/url; };

  # Dependencies from ivy2Local and m2Local, which the build is given as
  # directories; the lock was made with those of fixtures/local-home.
  local = build {
    src = ./fixtures/local;
    localRepositories = {
      ivy2Local = ./fixtures/local-home/.ivy2/local;
      m2Local = ./fixtures/local-home/.m2/repository;
    };
  };

  # A Scala.js program, linked on the JVM and run on Node.js, whose munit
  # suite runs on Node.js.
  js = build { src = ./fixtures/js; };

  # A Scala Native program, compiled and linked with clang, whose munit
  # suite runs natively.
  native = build { src = ./fixtures/native; };

  # A library locked with --publish: its release is what
  # `scala-cli publish` writes in the sandbox.
  library = build { src = ./fixtures/library; };

  # The core module of VirtusLab/besom, a Scala 3 library, built from its
  # sources upstream at the tag v0.5.2 with a lockfile kept in this
  # repository, as the Examples section of the README describes.
  besomSource = builtins.fetchTree {
    type = "github";
    owner = "VirtusLab";
    repo = "besom";
    rev = "6017c8814ac885df3c10d06969d90e2c2d8b3f9d";
    narHash = "sha256-5xGg+8oQBmjO6oHCsTjIlQQar/xd/n1ouzfArj2vh70=";
  };
  besomCore = build {
    src = besomSource + "/core";
    lockFile = ./examples/besom-core/scala-cli-purifier.lock.json;
  };

  # The hello fixture kept external: its source holds only the project's
  # own files, and the purifier lockfile lies outside it.
  externalSource = lib.fileset.toSource {
    root = ./fixtures/hello;
    fileset = lib.fileset.difference ./fixtures/hello ./fixtures/hello/scala-cli-purifier.lock.json;
  };
  external = build {
    src = externalSource;
    lockFile = ./fixtures/hello/scala-cli-purifier.lock.json;
  };

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

  helloLock = builtins.fromJSON (builtins.readFile ./fixtures/hello/scala-cli-purifier.lock.json);
  # A lockfile with `data`, under a checksum that matches it, as if
  # `scala-cli-purifier lock` had written it. The lockfiles of these checks
  # are written at evaluation with builtins.toFile, because readLock reads
  # them at evaluation.
  writeLock =
    name: data:
    builtins.toFile name (
      builtins.toJSON (
        data
        // {
          checksum = builtins.hashString "sha256" (builtins.toJSON (removeAttrs data [ "checksum" ]));
        }
      )
    );
  helloWith =
    lockFile:
    build {
      src = ./fixtures/hello;
      inherit lockFile;
    };
  # The jar of upickle, which the hello program needs.
  neededJar = lib.findFirst (
    a: lib.hasSuffix "/upickle_3-4.0.2.jar" a.path
  ) (throw "the hello lockfile holds no upickle jar") helloLock.artifacts;

  # The hello lockfile with a hash changed and the checksum left as it was.
  tamperedLock = builtins.toFile "tampered.lock.json" (
    builtins.toJSON (
      helloLock
      // {
        artifacts = map (
          a:
          if a == neededJar then a // { hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="; } else a
        ) helloLock.artifacts;
      }
    )
  );
  # The first half of the hello lockfile, which is not JSON.
  malformedLock = builtins.toFile "malformed.lock.json" (
    let
      text = builtins.readFile ./fixtures/hello/scala-cli-purifier.lock.json;
    in
    builtins.substring 0 (builtins.stringLength text / 2) text
  );
  # The hello fixture with a dependency added after it was locked, without
  # a lockfile.
  staleHello = ./conformance/stale;

  # Passes when every line of `expected` is in the file `log`.
  expectLines = log: expected: ''
    ${lib.concatMapStrings (line: ''
      grep -qF ${lib.escapeShellArg line} ${log} || {
        cat ${log} >&2
        echo "missing in the failure log: ${line}" >&2
        exit 1
      }
    '') expected}
  '';

  # A check that `lockFile` fails in both places the contract names, with
  # every line of `expected` in each message: at Nix evaluation of readLock
  # (nix-instantiate in the sandbox, on a store that holds nothing), and in
  # the command, run in the project `src`.
  lockFails =
    {
      name,
      lockFile,
      src ? ./fixtures/hello,
      expected,
    }:
    pkgs.runCommand name { nativeBuildInputs = [ pkgs.nix ]; } ''
      export HOME=$TMPDIR/home NIX_STATE_DIR=$TMPDIR/nix-state NIX_CONF_DIR=$TMPDIR
      mkdir -p $HOME
      if nix-instantiate --store dummy:// --eval --strict -E "
        let
          inherit (import ${../nix/lib}/lock.nix { lib = import ${pkgs.path}/lib; }) readLock;
          lock = readLock { src = ${src}; lockFile = ${lockFile}; };
        in
        if lock.problems == [ ] then lock else throw (builtins.concatStringsSep \"\n\" lock.problems)
      " 2> eval.log; then
        echo "${name}: Nix evaluated the lockfile" >&2
        exit 1
      fi
      ${expectLines "eval.log" expected}
      cp -r ${src} project
      chmod -R u+w project
      cd project
      if ${lib.getExe cli} prefetch --lockfile ${lockFile} 2> command.log; then
        echo "${name}: the command read the lockfile" >&2
        exit 1
      fi
      ${expectLines "command.log" expected}
      touch $out
    '';

  # Passes when building `package` fails with every line of `expected` in
  # its log.
  buildFails =
    name: package: expected:
    pkgs.runCommand name { failure = pkgs.testers.testBuildFailure package; } ''
      ${expectLines "$failure/testBuildFailure.log" expected}
      touch $out
    '';

  # Nix knows every output of a project from its lockfile, before it builds:
  # the check requires that `built.artifacts` names exactly `expected`, then
  # builds each one and links it under its name.
  knownOutputs =
    name: built: expected:
    assert
      builtins.attrNames built.artifacts == lib.sort lib.lessThan expected
      || throw "${name}: the outputs are ${builtins.toJSON (builtins.attrNames built.artifacts)}";
    pkgs.runCommand name { } ''
      mkdir $out
      ${lib.concatStrings (
        lib.mapAttrsToList (key: file: ''
          test -f ${file} || { echo "${key}: ${file} is not a file" >&2; exit 1; }
          ln -s ${file} $out/${lib.escapeShellArg key}
        '') built.artifacts
      )}
    '';

  # A second build of `drv`, which differs from the first only in an
  # attribute the build does not read, so Nix builds it again.
  again = drv: drv.overrideAttrs { rebuildProbe = "again"; };

  # The downloads of the hello lockfile as `scala-cli-purifier prefetch`
  # realizes them.
  helloDownloads = flakeLib.purifier.tree {
    inherit pkgs;
    name = "scala-cli-purifier-downloads";
    entries = flakeLib.purifier.entriesFor pkgs.stdenv.buildPlatform.system (
      flakeLib.scala-cli-purifier.readLock { src = ./fixtures/hello; }
    );
  };
  # A scala-cli of another version than the lock was made with, which fails
  # evaluation, naming both versions.
  otherScalaCli = pkgs.scala-cli.overrideAttrs {
    version = "0.0.1";
    __intentionallyOverridingVersion = true;
  };
  # Passes when evaluating the hello fixture with `builder` fails.
  failsToEvaluate =
    name: builder:
    let
      attempt = builtins.tryEval (builder { src = ./fixtures/hello; }).packages.hello.drvPath;
    in
    assert !attempt.success || throw "${name}: a scala-cli of another version evaluated";
    pkgs.runCommand name { } "touch $out";
in
{
  java-major =
    pkgs.runCommand "java-major"
      {
        nativeBuildInputs = [
          pkgs.nix
          pkgs.python3
        ];
      }
      ''
        python3 -B ${./java-major.py} ${../nix/lib/build-scala-cli-project.nix} ${pkgs.path}/lib
        touch $out
      '';

  # A producer needs a complete runtime view even with no Nix project inputs.
  standalone-projection = pkgs.runCommand "scala-cli-standalone-projection" { } ''
    test -n "$(find ${library.projections.maven}/org/scala-lang/scala3-library_3 -name '*.jar')"
    touch $out
  '';
  composition = import ./composition.nix {
    inherit pkgs build;
    purifier = withOverlay.lib.purifier;
  };

  jvm-projection = library.projections.maven;

  inherit cli;

  # The Darwin local-networking exception must allow the locked mirror's
  # loopback listener while keeping external outbound connections denied.
  local-networking =
    pkgs.runCommand "scala-cli-local-networking"
      {
        nativeBuildInputs = [ pkgs.python3 ];
        __darwinAllowLocalNetworking = pkgs.stdenv.buildPlatform.isDarwin;
      }
      ''
        python3 -B ${./local-networking.py} > result
        mkdir "$out"
        cp result "$out/"
      '';

  hello-runs = runs "hello-runs" hello.packages.hello [ ''{"hello":"Nix World"}'' ];
  hello-tests = hello.tests;
  script-runs = runs "script-runs" script.packages.script [ "script on Scala 2.13" ];
  sources-runs = runs "sources-runs" sources.packages.sources [
    "hiccup from Clojars"
    "greeting from a local jar"
    "sourcecode from a version range: 1 + 1"
  ];
  url-runs = runs "url-runs" url.packages.url [ "hiccup from a URL: true" ];
  local-runs = runs "local-runs" local.packages.local [
    "FROM IVY2LOCAL!"
    "from m2local..."
  ];
  js-runs = runs "js-runs" js.packages.js [ "hello from Scala.js" ];
  js-tests = js.tests;
  native-runs = runs "native-runs" native.packages.native [ "hello from Scala Native" ];
  native-tests = native.tests;

  # The outputs of each platform, and of a release.
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

  # The release holds the library's jar, sources, Scaladoc and POM with
  # their checksums, and the jar holds the library's classes.
  library-release =
    pkgs.runCommand "library-release"
      {
        nativeBuildInputs = [
          pkgs.unzip
          pkgs.libxml2
        ];
      }
      ''
        cd ${library.release}/${libraryVersion}
        for f in greeting_3-1.0.0.jar greeting_3-1.0.0-sources.jar greeting_3-1.0.0-javadoc.jar greeting_3-1.0.0.pom; do
          test -f "$f" || { echo "the release lacks $f" >&2; exit 1; }
          test "$(sha1sum < "$f" | cut -d' ' -f1)" = "$(cat "$f.sha1")" || { echo "$f.sha1 does not match" >&2; exit 1; }
        done
        unzip -l greeting_3-1.0.0.jar | grep -q 'greeting/Greeting\$package.class'
        test "$(xmllint --xpath 'string(/*[local-name()="project"]/*[local-name()="artifactId"])' greeting_3-1.0.0.pom)" = greeting_3
        mkdir -p $out
        ln -s ${library.release} $out/release
      '';

  # A project whose directives changed since the lock fails, naming the
  # command to run: at evaluation and in the command.
  conformance-stale = lockFails {
    name = "conformance-stale";
    src = staleHello;
    lockFile = ./fixtures/hello/scala-cli-purifier.lock.json;
    expected = [
      "directives changed"
      "scala-cli-purifier lock"
    ];
  };

  # A lockfile with a changed hash fails at evaluation and in the command.
  conformance-tampered = lockFails {
    name = "conformance-tampered";
    lockFile = tamperedLock;
    expected = [
      "its checksum does not match its content"
      "Run `scala-cli-purifier lock`"
    ];
  };

  # A lockfile that lacks a download the build needs fails in the build,
  # saying that the lockfile is incomplete and what to run.
  conformance-missing =
    buildFails "conformance-missing"
      (helloWith (
        writeLock "missing-entry.lock.json" (
          helloLock // { artifacts = lib.remove neededJar helloLock.artifacts; }
        )
      )).packages.hello
      [
        "the lockfile does not hold"
        "scala-cli-purifier lock"
      ];

  # A lockfile that is not JSON fails at evaluation and in the command.
  conformance-malformed = lockFails {
    name = "conformance-malformed";
    lockFile = malformedLock;
    expected = [
      "not a scala-cli-purifier lockfile"
      "scala-cli-purifier lock"
    ];
  };

  # Two builds of each platform and of a release give the same bytes. The
  # program wrappers name their own output paths, so the check compares the
  # files the wrappers run.
  conformance-rebuild =
    let
      pairs = {
        "hello.jar" = [
          hello.packages.hello
          "share/java/hello.jar"
        ];
        "js.js" = [
          js.packages.js
          "share/js/js.js"
        ];
        native = [
          native.packages.native
          "bin/native"
        ];
      };
    in
    pkgs.runCommand "conformance-rebuild" { } ''
      ${lib.concatStrings (
        lib.mapAttrsToList (
          key: pair:
          let
            drv = builtins.elemAt pair 0;
            rel = builtins.elemAt pair 1;
          in
          ''
            cmp ${drv}/${rel} ${again drv}/${rel} || { echo "${key} differs between two builds" >&2; exit 1; }
          ''
        ) pairs
      )}
      diff -r ${library.release} ${again library.release} || { echo "the release differs between two builds" >&2; exit 1; }
      touch $out
    '';

  # After a prefetch, the everyday loop of the hello fixture works through
  # the scala-cli wrapper with no network (the sandbox has none): compile,
  # test and run, then `setup-ide` and an editor session against the build
  # server it configures, which compiles and opens dependency sources.
  conformance-offline =
    pkgs.runCommand "conformance-offline"
      {
        __darwinAllowLocalNetworking = pkgs.stdenv.buildPlatform.isDarwin;
        nativeBuildInputs = [
          cli
          pkgs.python3
        ];
      }
      ''
        export HOME=$TMPDIR/home
        mkdir -p $HOME
        python3 -B ${./local-networking.py}
        ${lib.optionalString pkgs.stdenv.buildPlatform.isDarwin ''
          # On macOS the build user's home in the password database is
          # /var/empty, which nothing can write, and scala-cli and the Bloop
          # server take it as user.home. scala-cli keeps its directories in
          # SCALA_CLI_HOME instead; the wrapper passes
          # SCALA_CLI_PURIFIER_USER_HOME to scala-cli as user.home (with
          # /var/empty it builds the project as a virtual one elsewhere, and
          # setup-ide fails); Bloop, a JVM, takes it from JAVA_TOOL_OPTIONS.
          export SCALA_CLI_HOME=$HOME/.scala-cli SCALA_CLI_PURIFIER_USER_HOME=$HOME
          export JAVA_TOOL_OPTIONS=-Duser.home=$HOME
        ''}
        cp -r ${./fixtures/hello} project
        chmod -R u+w project
        cd project
        # What `scala-cli-purifier prefetch` leaves beside the lockfile.
        mkdir .scala-cli-purifier
        ln -s ${helloDownloads} .scala-cli-purifier/downloads
        echo ${helloLock.checksum} > .scala-cli-purifier/prefetched
        scala-cli compile .
        scala-cli test .
        scala-cli run . | tee run.log
        grep -qxF '{"hello":"Nix World"}' run.log
        scala-cli setup-ide .
        python3 -I ${cli}/libexec/scala-cli-purifier/bsp.py .
        touch $out
      '';

  # readLock gives every fixture a lock that checkLock of the shared
  # library accepts, with the outputs the builder makes.
  read-lock = pkgs.writeText "read-lock" (
    builtins.toJSON (
      lib.mapAttrs
        (
          name: src:
          let
            lock = flakeLib.purifier.checkLock (flakeLib.scala-cli-purifier.readLock { inherit src; });
          in
          {
            entries = builtins.length lock.entries;
            outputs = builtins.attrNames lock.outputs;
          }
        )
        {
          hello = ./fixtures/hello;
          script = ./fixtures/script;
          sources = ./fixtures/sources;
          url = ./fixtures/url;
          local = ./fixtures/local;
          js = ./fixtures/js;
          native = ./fixtures/native;
          library = ./fixtures/library;
        }
    )
  );

  # A package written for callPackage takes the builder through the scope,
  # as the Quickstart of the README shows.
  consumer-package = runs "consumer-package" (withOverlay.callPackage (
    { scala-cli-purifier }:
    (scala-cli-purifier.buildScalaCliProject { src = ./fixtures/hello; }).packages.hello
  ) { }) [ ''{"hello":"Nix World"}'' ];

  # buildScalaCliProject.override replaces what the builder takes from the
  # package set.
  builder-override = failsToEvaluate "builder-override" (
    withOverlay.scala-cli-purifier.buildScalaCliProject.override { scala-cli = otherScalaCli; }
  );

  # scala-cli-purifier.overrideScope replaces what the members of the scope
  # take, the builder among them.
  scope-override =
    failsToEvaluate "scope-override"
      (withOverlay.scala-cli-purifier.overrideScope (final: prev: { scala-cli = otherScalaCli; }))
      .buildScalaCliProject;

  # The jar of besom core holds its classes. The listing goes to a file
  # first: grep -q stops at the first match, and unzip, still writing the
  # listing of the whole assembly, would fail with SIGPIPE.
  example-besom-core =
    pkgs.runCommand "example-besom-core"
      {
        nativeBuildInputs = [
          pkgs.unzip
          pkgs.jdk21_headless
        ];
      }
      ''
        unzip -l ${besomCore.packages.besom-core}/share/java/besom-core.jar > listing
        grep -q 'besom/internal/Context.class' listing
        cat > BesomRuntime.java <<'JAVA'
        class BesomRuntime {
          public static void main(String[] args) throws Exception {
            Class<?> api = Class.forName("besom.util.NonEmptyString$package$NonEmptyString$");
            Object module = api.getField("MODULE$").get(null);
            java.lang.reflect.Method apply = api.getMethod("apply", String.class);
            Class<?> option = Class.forName("scala.Option");
            Object blank = apply.invoke(module, "   ");
            Object value = apply.invoke(module, "besom-runtime");
            if (!(Boolean) option.getMethod("isEmpty").invoke(blank))
              throw new AssertionError("Besom accepted a blank string");
            if (!"besom-runtime".equals(option.getMethod("get").invoke(value)))
              throw new AssertionError("Besom lost the nonempty value");
            System.out.println("BESOM CORE RUNTIME PASSED");
          }
        }
        JAVA
        java --class-path ${besomCore.packages.besom-core}/share/java/besom-core.jar BesomRuntime.java
        mkdir -p $out
        ln -s ${besomCore.packages.besom-core} $out/package
      '';

  # The hello fixture kept external builds from the purifier lockfile
  # outside its source.
  conformance-external =
    assert
      !builtins.pathExists (externalSource + "/scala-cli-purifier.lock.json")
      || throw "conformance-external: the source holds a purifier lockfile";
    runs "conformance-external" external.packages.hello [ ''{"hello":"Nix World"}'' ];
}
