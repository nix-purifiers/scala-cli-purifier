# SPDX-License-Identifier: MIT
#
# The scala-cli-purifier command: `lock`, `exec` and `publish` (see
# scala_cli_purifier.py). It runs the scala-cli of this package set, which
# is the one buildScalaCliProject checks locks against, and runs scala-cli's
# compiler on a JDK of this set unless JAVA_HOME names another. Node.js,
# clang and `which` come first on the PATH, as in the Nix build, for
# Scala.js tests and for Scala Native, which finds clang with `which`, and
# openssl makes the offline mirror's certificates.
{
  lib,
  path,
  stdenvNoCC,
  makeWrapper,
  python3,
  gnupg,
  scala-cli,
  jdk21_headless,
  nodejs,
  clang,
  which,
  openssl,
}:
let
  # The Nix library, which `exec` and `publish` evaluate, with the offline
  # mirror it runs, at the same paths relative to each other as in the
  # repository.
  librarySource = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [
      ../../nix/lib
      ./offline_mirror.py
      ./coursier_cache.py
    ];
  };
in
stdenvNoCC.mkDerivation {
  pname = "scala-cli-purifier";
  version = lib.trim (builtins.readFile ../../version.txt);

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./scala_cli_purifier.py
      ./coursier_cache.py
      ./offline_mirror.py
      ./publication.py
      ./test_coursier_cache.py
      ./test_offline_mirror.py
      ./test_publication.py
    ];
  };

  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [
    python3
    gnupg
    openssl
    jdk21_headless
  ];

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    export HOME="$TMPDIR/home"
    python3 -m unittest -v test_coursier_cache test_publication test_offline_mirror
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -Dm644 -t "$out/libexec/scala-cli-purifier" \
      scala_cli_purifier.py coursier_cache.py offline_mirror.py publication.py
    makeWrapper ${python3.interpreter} "$out/bin/scala-cli-purifier" \
      --add-flags "-I $out/libexec/scala-cli-purifier/scala_cli_purifier.py" \
      --prefix PATH : ${
        lib.makeBinPath [
          nodejs
          clang
          which
          openssl
        ]
      } \
      --set-default SCALA_CLI_PURIFIER_SCALA_CLI ${lib.getExe' scala-cli "scala-cli"} \
      --set SCALA_CLI_PURIFIER_SCALA_CLI_VERSION ${scala-cli.version} \
      --set SCALA_CLI_PURIFIER_JAVA_HOME ${jdk21_headless.home} \
      --set SCALA_CLI_PURIFIER_LIB ${librarySource}/nix/lib \
      --set SCALA_CLI_PURIFIER_NIXPKGS ${path}
    runHook postInstall
  '';

  meta = {
    description = "Lock the downloads of a scala-cli project, run scala-cli on them offline, and publish what Nix built";
    mainProgram = "scala-cli-purifier";
    license = lib.licenses.mit;
  };
}
