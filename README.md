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

Local repositories work too: `ivy2Local`, which scala-cli reads by
default, `m2Local`, and any other `file:` repository, inside or outside
the project. Coursier reads a local repository in place, so nothing about
it lands in the cache. The lock run takes the build's class path instead,
and records every file of each module version directory the class path
reaches into, with its hash, by its path relative to the repository's
root. Its offline check builds from those files alone. The Nix build is
given each repository as a directory (`localRepositories`), copies the
recorded files from it, checks their hashes, and points scala-cli at the
copy. `exec` reads the real directories.

The lockfile names each local repository: `ivy2Local`, `m2Local`, or the
directory name of another `file:` repository. scala-cli finds `ivy2Local`
and `m2Local` under the JVM's `user.home`, which comes from the password
database rather than from `HOME`; `SCALA_CLI_PURIFIER_USER_HOME` replaces
it for `lock` and `exec`.

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

It takes `lockFile`, `jdk`, `doCheck`, `srcFilter` and
`localRepositories`; `nix/lib/build-scala-cli-project.nix` documents each
one. `buildScalaCliProject.override { fetchurl = ...; }` replaces the
fetcher of the locked files, for credentials or other mirrors; the
replacement takes the arguments nixpkgs' `fetchurl` takes. A project that
reads local repositories takes each one by the name the lockfile gives it:

```nix
(buildScalaCliProject {
  src = ./.;
  localRepositories = {
    ivy2Local = ./vendor/ivy-local;
    m2Local = ./vendor/m2;
  };
}).package
```

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
command offline on them, through the offline mirror. `exec` runs scala-cli
without its Bloop build server (`--server=false`), as the lock run and the
Nix build do, so the lockfile holds no Bloop downloads.

The everyday loop is `exec run .`, `exec test .` and `exec compile .`.
After adding or upgrading a dependency, run `lock` again: until then,
`exec` and the Nix build refuse to run, because the project's
`//> using` directives no longer match the ones the lockfile was made
from.

### Editors

```zsh
scala-cli-purifier setup-ide .
```

configures Metals, or any other editor that speaks the Build Server
Protocol, to build the project offline on the locked downloads. The lock
run opens the project the way an editor does, through scala-cli's build
server, so the lockfile holds what an editor asks for and no build needs,
such as the sources jars of the dependencies; its offline check opens the
project again and requires every dependency source to be there.
`lock --no-ide` skips both, for a lockfile without them. It runs
`scala-cli setup-ide` through `exec`, then points the launch command in
`.bsp/scala-cli.json` at `scala-cli-purifier exec bsp`, so the build server
starts with the locked cache and the offline mirror. Run it again after
`lock`.

### Mistakes and their errors

| Mistake | Error |
|---|---|
| A dependency was added or changed after the lock | `exec` and the Nix build say the project's `//> using` directives changed since the lock, and to run `scala-cli-purifier lock` |
| The lockfile was edited by hand | `exec` and Nix evaluation say its checksum does not match its content, and to run `scala-cli-purifier lock` |
| The lockfile is not JSON | `exec` says so, and to run `scala-cli-purifier lock`; Nix reports the JSON parse error |
| The lockfile lacks a file the build needs | the Nix build says the lockfile is incomplete, and to run `scala-cli-purifier lock` |
| The lockfile was made with another scala-cli | `exec` and Nix evaluation name both versions, and say to re-lock |
| A local repository directory differs from the locked one | the Nix build names the file whose hash differs, and to re-lock |

### Relocking behind a busy Maven Central

`SCALA_CLI_PURIFIER_MAVEN_CENTRAL_MIRROR` makes the lock run download the
files of Maven Central from a mirror, such as Google's mirror at
`https://maven-central.storage-download.googleapis.com/maven2`. The
lockfile still records each file under its Maven Central URL and cache
path, with the URL of the mirror in `mirrors`, which the Nix build falls
back to.

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
  the Nix build fetches from it when a `fetchurl` passed through
  `buildScalaCliProject.override` supplies them (nixpkgs' `fetchurl` takes
  `netrcPhase`). A download URL that carries credentials
  in its host part cannot be locked.
- The lock records a local repository by the module version directories
  of the class path. A module whose descriptor names a parent POM in
  another directory of the same local repository fails the offline check,
  which names the missing file; publishing the parent to a remote
  repository, or as part of the project's jars, avoids it.
- The offline check reads `ivy2Local` and `m2Local` from the recorded
  files alone. Another `file:` repository stays visible at its own path on
  the machine that locks, so for one of those only the Nix build proves
  that the recorded files suffice.

## Credits

The design comes from
[sbt-nix-backend](https://github.com/clhodapp/sbt-nix-backend), now
sbt-purifier: a lock run with an empty cache, a lockfile of every
downloaded file, a builder that realizes those files as the tool's cache,
an offline interactive mode, and publishing what Nix built. The locked
files are fetched and laid out by
[purifier](https://github.com/nix-purifiers/purifier), the library the
nix-purifiers share. The purifier idea was inspired by
nixcloud's [cargo (libnix)](https://github.com/nixcloud/cargo). The coursier
cache code (`pkgs/cli/coursier_cache.py`) is shared with the Mill
purifier.

## License

MIT
