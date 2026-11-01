# scala-cli-purifier

A command and a Nix library that build [scala-cli](https://scala-cli.virtuslab.org)
projects in the Nix sandbox. `scala-cli-purifier lock` records the files
scala-cli downloads for a project in a lockfile, and `buildScalaCliProject`
builds the project from that lockfile. The project needs no Nix files of
its own, and nothing generated is committed besides the lockfile.

- **A lockfile.** `scala-cli-purifier lock` writes
  `scala-cli-purifier.lock.json` at the project root. It lists every file
  scala-cli downloaded by URL and SHA-256, the scala-cli and Java versions,
  the project's platform and scala-cli arguments, and every output the
  build produces.
- **Sandboxed builds.** `buildScalaCliProject`, which the flake's overlay
  adds to a package set, fetches each locked file and runs scala-cli
  offline. JVM, Scala.js and Scala Native projects build, and their tests
  run in the build.
- **scala-cli on the locked downloads.** `scala-cli-purifier exec` runs any
  scala-cli command offline, on the locked files, which it fetches through
  Nix.
- **Publishing what Nix built.** `scala-cli-purifier publish` builds the
  files `scala-cli publish` writes in the Nix sandbox and uploads those
  files to a Maven repository, or writes a Maven Central bundle.

## How the lock is made

`scala-cli-purifier lock` runs scala-cli with an empty coursier cache, an
empty home directory and an empty scala-cli workspace, the way the Nix
build runs it. It first reads the project's platform from
`scala-cli export --json`, then packages the project for that platform:

| Platform | Package | Program |
|---|---|---|
| JVM | an assembly jar | `java -jar` on the JDK |
| Scala.js | a linked `.js` file | `node` |
| Scala Native | a native binary | the binary |

With `--test`, the lock run also runs `scala-cli test`, so the test
dependencies are locked too and the Nix build runs the tests. With
`--publish`, it also runs `scala-cli publish` to a local directory. The
arguments after the options are scala-cli inputs and options; the default
is `.`, the project directory, and they must suit each command the lock
runs.

Before it writes the lockfile, `lock` builds the project again, offline,
from the recorded files alone. It leaves out the `maven-metadata.xml`
version listings coursier fetches, because their content changes upstream
whenever a version is published. When the offline build needs them, for a
version range, a `latest` selector or Scala.js, whose linker scala-cli picks
from a listing, the lockfile holds the listings' text inline, so the
offline build resolves to the versions the lock run picked.

The lock run passes `--java-home`, so scala-cli never downloads a JVM. It
uses the JDK in `JAVA_HOME`, or this flake's JDK 21; the Nix build uses the
headless JDK of the same major version from the package set. Scala.js links
with `--js-cli-on-jvm`, because the default Scala.js linker is a prebuilt
native executable, which does not run in the Nix sandbox. Scala Native
compiles with the clang of the package set.

A proxy in `https_proxy` or `http_proxy`, with or without credentials,
reaches scala-cli as Java system properties.

### Dependency sources

Every dependency source that downloads works: Maven coordinates, any
repository named with `//> using repository`, version ranges, jars in the
project (`//> using jar`), and dependencies downloaded from a URL
(`//> using dep "org:name:version,url=https://..."`).

A URL dependency needs one more piece. Coursier checks that a URL
dependency exists by sending a HEAD request to its URL on every
resolution, outside its cache and even in offline mode. Offline, in the
sandbox and in `exec`, scala-cli-purifier runs a local proxy, the offline
mirror (`pkgs/cli/offline_mirror.py`), and points scala-cli at it. The
mirror terminates TLS with certificates from a certificate authority made
for that run, which scala-cli trusts through its trust store options, and
answers each request from the locked files. A request for a file the
lockfile lacks gets 404 and names the missing file.

The repositories `ivy2Local` and `m2Local`, and `file:` URLs outside the
project, cannot be locked. Coursier reads them in place on the machine
that locks, and they hold files that machine published locally, which no
URL serves, so the Nix build has nothing to fetch. Publish such a library
to a repository, or put its jar in the project and name it with
`//> using jar`.

## Use

Lock the project from its directory, and commit the lockfile:

```zsh
nix run github:nix-purifiers/scala-cli-purifier -- lock --test
```

The flake's `overlays.default` adds `buildScalaCliProject` to a package set,
and a package written for `callPackage` takes it by name:

```nix
# package.nix
{ buildScalaCliProject }:
(buildScalaCliProject { src = ./.; }).package
```

```nix
{
  inputs.scala-cli-purifier.url = "github:nix-purifiers/scala-cli-purifier";
  inputs.nixpkgs.follows = "scala-cli-purifier/nixpkgs";

  outputs = { nixpkgs, scala-cli-purifier, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [
          scala-cli-purifier.overlays.default
          (final: prev: { app = final.callPackage ./package.nix { }; })
        ];
      };
    in
    {
      packages.${system}.default = pkgs.app;
    };
}
```

`nix build` then builds the project, and `nix run` runs it. The builder
returns:

- `package`, the program, also under `packages.<name>`, where the name is
  the project directory's name unless `lock --name` gave another;
- `publication`, from a lock made with `--publish`: the files
  `scala-cli publish` writes, as a Maven repository directory;
- `artifacts`, every output file by name, known from the lockfile before
  anything builds: `program`, `<name>.jar` or `<name>.js`, and each
  published file under its name without the version, such as
  `greeting_3.jar`, `greeting_3-sources.jar`, `greeting_3-javadoc.jar` and
  `greeting_3.pom`;
- `dependencies`, the coursier cache of the locked files.

It takes `lockFile`, `jdk`, `fetchurl` (for credentials or mirrors),
`doCheck` and `srcFilter`; `nix/lib/build-scala-cli-project.nix` documents
each one.

The lockfile names the scala-cli version it was made with, because
scala-cli downloads tools of its own version. The builder refuses a lock
made with another version than the package set's `scala-cli`. Re-lock with
the `scala-cli-purifier` of the same flake revision, or pass the matching
scala-cli through `buildScalaCliProject.override`.

### Interactive use

```zsh
nix run github:nix-purifiers/scala-cli-purifier -- exec run .
```

`exec` fetches the locked files through Nix, links them into a coursier
cache under `.scala-cli-purifier/` with one GC root, and runs the scala-cli
command offline on them, through the offline mirror. A dependency that is
not in the lockfile fails to resolve, and `lock` adds it. `exec` runs
scala-cli without its Bloop build server (`--server=false`), as the lock
run and the Nix build do, so the lockfile holds no Bloop downloads.

### Publishing

Lock with `--publish`, then publish from the project directory:

```zsh
scala-cli-purifier publish https://maven.example.com/releases
scala-cli-purifier publish --gpg-key 0xKEYID --bundle central-bundle.zip
```

`publish` builds the publication with Nix: the jar, sources, Scaladoc and
POM that `scala-cli publish` writes in the sandbox, with their checksums.
It uploads those files by HTTP PUT to an http(s) repository, which Nexus,
Artifactory, GitHub Packages and reposilite accept, with the user name and
password from `SCALA_CLI_PURIFIER_PUBLISH_USER` and
`SCALA_CLI_PURIFIER_PUBLISH_PASSWORD` (`--user-env` and `--password-env`
name other variables). A path or a `file:` URL takes a copy. `--gpg-key`
signs every file, and `--bundle` writes a zip with SHA-256 and SHA-512
checksums too, which is the bundle the Maven Central Portal takes. Nothing
is compiled outside the sandbox.

## Limitations

- The native scala-cli writes Scala.js linker output and publications to
  `/tmp`, whatever `java.io.tmpdir` says, so locking a Scala.js project or
  a lock with `--publish` needs a writable `/tmp`. The Nix sandbox has one.
- A repository that needs credentials locks when scala-cli has them, and
  the Nix build fetches from it when `fetchurl` supplies them (nixpkgs'
  `fetchurl` takes `netrcPhase`). A download URL that carries credentials
  in its host part cannot be locked.

## Credits

The design comes from
[sbt-nix-backend](https://github.com/clhodapp/sbt-nix-backend), now
sbt-purifier: a lock run with an empty cache, a lockfile of every
downloaded file, a builder that realizes those files as the tool's cache,
an offline interactive mode, and publishing what Nix built. The Nix
library follows its generic files. The purifier idea was inspired by
nixcloud's [cargo (libnix)](https://github.com/nixcloud/cargo). The coursier
cache code (`pkgs/cli/coursier_cache.py`) is shared with the Mill
purifier.

## License

MIT
