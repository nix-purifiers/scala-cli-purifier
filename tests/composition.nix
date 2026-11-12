# SPDX-License-Identifier: MIT
{
  pkgs,
  build,
  purifier,
}:
let
  provider =
    pkgs.runCommand "scala-cli-java-provider"
      {
        nativeBuildInputs = [ pkgs.jdk_headless ];
        passthru.projections = purifier.jvmProjections {
          repository = provider;
          inherit (pkgs) runCommand python3;
        };
      }
      ''
        mkdir -p classes
        cat > Greeting.java <<'JAVA'
        package projectinput;
        public final class Greeting {
          public static String message(String name) { return "[Nix] " + name; }
        }
        JAVA
        javac -d classes Greeting.java
        directory=$out/example/project-input/0.0.1
        mkdir -p "$directory"
        jar --create --file "$directory/project-input-0.0.1.jar" \
          --date=1980-01-01T00:00:02Z -C classes .
        cat > "$directory/project-input-0.0.1.pom" <<'POM'
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <modelVersion>4.0.0</modelVersion><groupId>example</groupId>
          <artifactId>project-input</artifactId><version>0.0.1</version>
        </project>
        POM
      '';
  transitiveProvider =
    pkgs.runCommand "nix-built-commons-lang3"
      {
        nativeBuildInputs = [ pkgs.jdk_headless ];
        passthru.projections = purifier.jvmProjections {
          repository = transitiveProvider;
          inherit (pkgs) runCommand python3;
        };
      }
      ''
        mkdir -p classes
        cat > StringUtils.java <<'JAVA'
        package org.apache.commons.lang3;
        public final class StringUtils {
          public static String purifierIdentity() { return "Nix"; }
          public static boolean isEmpty(CharSequence value) {
            return value == null || value.length() == 0;
          }
          public static String capitalize(String value) {
            if (value == null || value.isEmpty()) return value;
            return Character.toUpperCase(value.charAt(0)) + value.substring(1);
          }
        }
        JAVA
        javac --release 8 -d classes StringUtils.java
        dir=$out/org/apache/commons/commons-lang3/0.0.1
        mkdir -p "$dir"
        jar --create --file "$dir/commons-lang3-0.0.1.jar" \
          --date=1980-01-01T00:00:02Z -C classes .
        cat > "$dir/commons-lang3-0.0.1.pom" <<'POM'
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <modelVersion>4.0.0</modelVersion><groupId>org.apache.commons</groupId>
          <artifactId>commons-lang3</artifactId><version>0.0.1</version>
        </project>
        POM
      '';
  src = ./composition-source;
  original = builtins.fromJSON (builtins.readFile ./fixtures/library/scala-cli-purifier.lock.json);
  selected = removeAttrs (
    original
    // {
      directivesHash =
        (import ../lib-overlays/default/lock.nix { inherit (pkgs) lib; }).directivesHash src
          original.arguments;
    }
  ) [ "checksum" ];
  lockFile = builtins.toFile "scala-cli-purifier.lock.json" (
    builtins.toJSON (
      selected
      // {
        checksum = builtins.hashString "sha256" (builtins.toJSON selected);
      }
    )
  );
  consumer = build {
    inherit src lockFile;
    projectDependencies = [ provider ];
  };
in
pkgs.runCommand "scala-cli-nix-composition"
  {
    nativeBuildInputs = [ pkgs.python3 ];
    passthru = { inherit provider transitiveProvider consumer; };
  }
  ''
    output=$(${consumer.packages.library}/bin/library)
    test "$output" = '[Nix] publication'
    export CATALOG=${provider.projections.maven.catalog}
    export POM=${consumer.release.artifacts."greeting_3.pom"}
    python - <<'PY'
    import json, os, xml.etree.ElementTree as ET
    catalog = json.load(open(os.environ['CATALOG']))
    selected = next(m['version'] for m in catalog['modules'] if m['name'] == 'project-input')
    root = ET.parse(os.environ['POM']).getroot()
    ns = {'m': root.tag.split('}')[0][1:]}
    dependency = next(d for d in root.findall('m:dependencies/m:dependency', ns)
                      if d.findtext('m:artifactId', namespaces=ns) == 'project-input')
    assert dependency.findtext('m:version', namespaces=ns) == selected
    PY
    touch $out
  ''
