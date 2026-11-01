# SPDX-License-Identifier: MIT
#
# The scala-cli-purifier command: `lock` and `exec` (see
# scala_cli_purifier.py). It runs the scala-cli of this package set, which
# is the one buildScalaCliProject checks locks against, and runs scala-cli's
# compiler on a JDK of this set unless JAVA_HOME names another.
{
  lib,
  stdenvNoCC,
  makeWrapper,
  python3,
  scala-cli,
  jdk21_headless,
}:
stdenvNoCC.mkDerivation {
  pname = "scala-cli-purifier";
  version = lib.trim (builtins.readFile ../../version.txt);

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./scala_cli_purifier.py
      ./coursier_cache.py
      ./test_coursier_cache.py
    ];
  };

  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ python3 ];

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    python3 -m unittest -v test_coursier_cache
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -Dm644 -t "$out/libexec/scala-cli-purifier" scala_cli_purifier.py coursier_cache.py
    makeWrapper ${python3.interpreter} "$out/bin/scala-cli-purifier" \
      --add-flags "-I $out/libexec/scala-cli-purifier/scala_cli_purifier.py" \
      --set SCALA_CLI_PURIFIER_SCALA_CLI ${lib.getExe' scala-cli "scala-cli"} \
      --set SCALA_CLI_PURIFIER_SCALA_CLI_VERSION ${scala-cli.version} \
      --set SCALA_CLI_PURIFIER_JAVA_HOME ${jdk21_headless.home} \
      --set SCALA_CLI_PURIFIER_LIB ${../../nix/lib}
    runHook postInstall
  '';

  meta = {
    description = "Lock the downloads of a scala-cli project, and run scala-cli on them offline";
    mainProgram = "scala-cli-purifier";
    license = lib.licenses.mit;
  };
}
