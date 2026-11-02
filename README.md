# scala-cli-purifier

scala-cli-purifier keeps Scala CLI's development workflow on locked,
Nix-resolved dependencies. Use the normal build and test commands in your
working tree; building the project itself in the Nix sandbox is an
optional next step.

## Installation

Install the integration into your Nix profile once:

```sh
nix profile install 'github:nix-purifiers/scala-cli-purifier'
```

This installs the `scala-cli` wrapper. Use it for the locked
development workflow below. With no purifier lockfile, it warns on standard
error and runs the ordinary tool with impure resolution.

`nix shell` makes an integration available temporarily instead of installing
it. Keep using the activation steps below where a project environment or
prefetched cache is required.

## Quickstart

Install Nix, then run the following from your existing project's root.
The setup prepares the locked dependencies and the integration that
Scala CLI's normal commands use:

```zsh
nix shell github:nix-purifiers/scala-cli-purifier
scala-cli-purifier lock --test
scala-cli-purifier prefetch
scala-cli run .
scala-cli test .
```

Commit the lockfiles that the lock command writes, including the build
tool's own lockfile when it has one. Keep using the project's usual build,
test and editor commands with this setup. Nix provides the locked
dependencies; the development commands above run outside the Nix sandbox.
No project Nix files are needed for this workflow.

Sandboxed builds of the whole project are an optional
[next step](#building-the-project-with-nix).

## Development loop

```zsh
scala-cli-purifier prefetch
scala-cli run .
scala-cli test .
```

The package installs a `scala-cli` beside `scala-cli-purifier`. In a
project with a lockfile, it runs the real scala-cli offline on the locked
downloads, with the options the lock run used. Outside such a project, it
is scala-cli itself.

After adding or upgrading a dependency, run `lock` again. Until then the
wrapper, the commands and Nix evaluation refuse the lockfile, because the
project's `//> using` directives no longer match the ones it was made from.
A relock keeps the version listings the lockfile holds, so a version range
resolves as it did. scala-cli has no command that updates a resolution, so
`update` downloads the version listings again and relocks: it moves
version ranges and `latest` selectors to what is published now, for every
module or for the named ones:

```zsh
scala-cli-purifier update com.lihaoyi::sourcecode
```

The commands run scala-cli on the JDK 21 of the package, which
`SCALA_CLI_PURIFIER_JAVA_HOME` can replace; `JAVA_HOME` does not choose it,
so a relock records the same Java version on every machine. The Nix build
uses the headless JDK of that major version from the package set.

## Building the project with Nix

Once the native development workflow is working, you can also build and
test the whole project in the Nix sandbox. For this step, keep a flake or
another Nix build definition in the project's repository, or maintain an
external packaging repository that points at its sources and lockfiles.
The builder consumes the same lockfiles as the development workflow.

Here `app` is the name of the program, which is the name of the project
directory unless `lock --name` gave another. `result/bin/app` runs the
program.

The `overlays.default` of the flake adds one attribute to a package set,
the scope `scala-cli-purifier`, which holds the builder
`buildScalaCliProject` and the command `cli`. A project whose authors want a
flake that packages it can use the builder in that flake, which can live in
the repository of the project or in another one:

```nix
{
  inputs.scala-cli-purifier.url = "github:nix-purifiers/scala-cli-purifier";
  inputs.nixpkgs.follows = "scala-cli-purifier/nixpkgs";

  outputs =
    {
      self,
      nixpkgs,
      scala-cli-purifier,
      ...
    }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ scala-cli-purifier.overlays.default ];
      };
    in
    {
      packages.${system}.default =
        (pkgs.scala-cli-purifier.buildScalaCliProject { src = self; })
        .packages.app;
    };
}
```

`nix build` then builds the project, and `nix run` runs it. The builder
returns these attributes:

- `packages.<name>`, the program, where the name is the project
  directory's name unless `lock --name` gave another. A JVM project builds
  an assembly jar that `bin/<name>` runs on the JDK, a Scala.js project a
  linked `.js` file that it runs on Node.js, and a Scala Native project a
  native binary.
- `tests`, the project's `scala-cli test` run in the sandbox, from a lock
  made with `--test`.
- `release`, from a lock made with `--publish`: the files
  `scala-cli publish` writes, as a Maven repository directory.
- `fetches`, the locked downloads as entries of the shared purifier
  library, which SBOM, license and vulnerability tools read.
- `artifacts`, every output file by name, known from the lockfile before
  anything builds.

The builder takes `src`, `lockFile`, `fetchers`, `jdk`, `srcFilter` and
`localRepositories`; `nix/lib/build-scala-cli-project.nix` documents each
one. `fetchers.fetchurl` replaces the fetcher of the locked files, for
credentials or a company mirror, and takes the arguments the `fetchurl` of
nixpkgs takes. `buildScalaCliProject.override` replaces what the builder
takes from the package set, such as `scala-cli`, and
`scala-cli-purifier.overrideScope` replaces it for every member of the
scope.
`lib.scala-cli-purifier.readLock { src; }` gives the lock of a project in
the shape every purifier shares, with its problems listed, for tools that
read locks.

We recommend committing `scala-cli-purifier.lock.json` to the repository of
the project, where every command and the builder find it by default. When
you package a project you do not change, a flake of your own can keep the
lockfile instead and pass it as `lockFile`. Every command then takes
`--lockfile <path>`, or reads the path from `SCALA_CLI_PURIFIER_LOCKFILE`,
which the dev shell of that flake can set.

### Publishing the release

Lock with `--publish`, then publish from the project directory:

```zsh
scala-cli-purifier publish https://maven.example.com/releases
scala-cli-purifier publish --gpg-key 0xKEYID --bundle \
  central-bundle.zip
```

`publish` builds the `release` with Nix: the jar, sources, Scaladoc and
POM that `scala-cli publish` writes in the sandbox, with their checksums.
It uploads those files by HTTP PUT to an http(s) repository, with the user
name and password from `SCALA_CLI_PURIFIER_PUBLISH_USER` and
`SCALA_CLI_PURIFIER_PUBLISH_PASSWORD` (`--user-env` and `--password-env`
name other variables). A path or a `file:` URL takes a copy. `--gpg-key`
signs every file, and `--bundle` writes a zip with SHA-256 and SHA-512
checksums too, which is the bundle the Maven Central Portal takes.

### One-off builds without a project Nix file

For an ad hoc build, the builder can also be evaluated directly from the
project root. Replace the example package name with one from your project:

```zsh
nix build --impure --expr 'let
  purifier = builtins.getFlake "github:nix-purifiers/scala-cli-purifier";
  pkgs = import purifier.inputs.nixpkgs {
    system = builtins.currentSystem;
    overlays = [ purifier.overlays.default ];
  };
in
(pkgs.scala-cli-purifier.buildScalaCliProject { src = ./.; })
.packages.app'
```

This uses the purifier's pinned nixpkgs, matching the lock command above.
To build with ambient `<nixpkgs>` from your Nix search path instead,
replace `import purifier.inputs.nixpkgs` with `import <nixpkgs>`.
Both forms need no Nix files in your project. The build tool and language
versions must match the lockfile; with ambient nixpkgs, lock with the
purifier from that package set or pass a compatible toolchain to the builder.

## Working offline

`prefetch` realizes every file of the lockfile in the Nix store, as one
tree that `.scala-cli-purifier/downloads` beside the lockfile points at
and keeps alive as a garbage-collector root. The lockfile holds the
build's downloads, the test dependencies and what an editor asks for, so
after a prefetch the development loop and the editor work with the network
cut off. The wrapper asks for `prefetch` again whenever the lockfile
changes.

Coursier, the dependency resolver scala-cli uses, checks that a dependency
downloaded from a URL exists by sending a HEAD request to the URL on every
resolution, outside its cache and even in offline mode. Offline, in the
sandbox and in the wrapper, scala-cli-purifier runs a local proxy, the
offline mirror (`pkgs/cli/offline_mirror.py`), and points scala-cli at it.
The mirror terminates TLS with certificates from a certificate authority
made for that run, which scala-cli trusts through its trust store options,
and answers each request from the locked files.

## Editor

```zsh
scala-cli setup-ide .
```

run through the wrapper, configures Metals, or any other editor that speaks
the Build Server Protocol, to build the project offline on the prefetched
downloads: it points the launch command in `.bsp/scala-cli.json` at the
wrapper, which starts scala-cli's build server with the locked cache and
the offline mirror. Run it again after `lock`.

The lock run opens the project the way an editor does, and compiles it on
the Bloop compilation server that serves an editor, so the lockfile holds
what an editor asks for, such as the source jars of the dependencies and
the compiler bridge of Bloop. `lock --no-ide` locks without them, for a
smaller lockfile.

## Troubleshooting

Each check runs in the commands, and Nix runs the same checks when it
evaluates the builder. Every message names the command that fixes it.

| Situation | Message and fix |
|---|---|
| A dependency was added or changed after the lock | the project's `//> using` directives changed since the lock; run `scala-cli-purifier lock` |
| The lockfile was edited by hand | its checksum does not match its content; run `scala-cli-purifier lock` |
| The lockfile is not JSON, or lacks fields | it is not a scala-cli-purifier lockfile; run `scala-cli-purifier lock` |
| The lockfile lacks a file the build needs | the build says the lockfile is incomplete; run `scala-cli-purifier lock` |
| The lockfile was made with another scala-cli | both versions are named; run `scala-cli-purifier lock` with this flake's command, or pass that scala-cli through `buildScalaCliProject.override` |
| The prefetched downloads are those of an older lockfile | the wrapper says so; run `scala-cli-purifier prefetch` |
| A local repository directory differs from the locked one | the build names the file whose hash differs; run `scala-cli-purifier lock` |
| `TMPDIR` is so long that the build server's socket path is too long | `lock` says so; set `TMPDIR` to a shorter directory |

## Supported language repositories

scala-cli-purifier locks Maven coordinates from Maven Central and from any
repository named with `//> using repository`, version ranges and `latest`
selectors, jars in the project (`//> using jar`), and dependencies
downloaded from a URL (`//> using dep "org:name:version,url=https://..."`).
A version listing (`maven-metadata.xml`) changes upstream whenever a
version is published, so the lockfile holds the text of the listings the
offline build needs, and the build resolves to the versions the lock
picked.

Local repositories work too: `ivy2Local`, which scala-cli reads by default,
`m2Local`, and any other `file:` repository, inside or outside the project.
The lockfile records every file of each module version directory the
class path reaches into, with its hash, by its path relative to the
repository root. The Nix build is given each repository as a directory
(`localRepositories`), by the name the lockfile gives it: `ivy2Local`,
`m2Local`, or the directory name of another `file:` repository. scala-cli
finds `ivy2Local` and `m2Local` under the `user.home` of the JVM, which
comes from the password database rather than from `HOME`;
`SCALA_CLI_PURIFIER_USER_HOME` replaces it for the commands.

JVM, Scala.js and Scala Native projects build. Scala.js links with
`--js-cli-on-jvm`, the Scala.js linker that runs on the JVM, and Scala
Native compiles with the clang of the package set.

For reproducible Native builds, the Nix builder patches Scala Native's
hierarchy metadata to hash types by their NIR names. Its identity-based
hash sets otherwise assign different runtime type IDs on different runs.
The patch changes only the build's copy of the tools jar; the downloaded
jar and its locked hash stay intact. The rebuild check compares the Native
binary byte for byte, as it does the JVM and Scala.js artifacts.

These limits apply:

- A download URL that carries credentials in its host part cannot be
  locked. A repository that needs credentials locks when scala-cli has
  them, and the Nix build fetches from it when `fetchers.fetchurl`
  supplies them.
- A module in a local repository whose descriptor names a parent POM in
  another directory of that repository fails the lock, which names the
  missing file.
- Locking a Scala.js project, or locking with `--publish`, needs a
  writable `/tmp`, where the native scala-cli writes Scala.js linker output
  and publications.

`SCALA_CLI_PURIFIER_MAVEN_CENTRAL_MIRROR` makes the lock run download the
files of Maven Central from a mirror, such as Google's mirror at
`https://maven-central.storage-download.googleapis.com/maven2`. The
lockfile records each file under its Maven Central URL, and the Nix build
falls back to Google's mirror when Maven Central fails.

## Examples

For day-to-day work on these projects, use the native-tool setup and
commands in [Quickstart](#quickstart) and [Development loop](#development-loop).
The Nix expressions below show optional sandboxed packaging of those
projects.

### Packaging besom core

[besom](https://github.com/VirtusLab/besom), the Scala SDK for Pulumi,
builds its `core` module with scala-cli. From a besom checkout, lock the
module and build it from its directory:

```zsh
cd besom/core
nix run github:nix-purifiers/scala-cli-purifier -- lock --name \
  besom-core
nix build --impure --expr 'let
  purifier = builtins.getFlake "github:nix-purifiers/scala-cli-purifier";
  pkgs = import purifier.inputs.nixpkgs {
    system = builtins.currentSystem;
    overlays = [ purifier.overlays.default ];
  };
in
(pkgs.scala-cli-purifier.buildScalaCliProject { src = ./.; })
.packages.besom-core'
```

`result/share/java/besom-core.jar` is the jar of the module. To work on it
offline, run `scala-cli-purifier prefetch` once, and use `scala-cli` and
your editor in `besom/core` as usual.

To package besom without changing its repository, keep the lockfile in a
flake of your own. Lock the module into that flake's directory:

```zsh
cd besom/core
scala-cli-purifier lock --name besom-core --lockfile \
  ~/besom-packaging/scala-cli-purifier.lock.json
```

The flake in `~/besom-packaging` builds the module from besom's sources:

```nix
{
  inputs.scala-cli-purifier.url = "github:nix-purifiers/scala-cli-purifier";
  inputs.nixpkgs.follows = "scala-cli-purifier/nixpkgs";
  inputs.besom = {
    url = "github:VirtusLab/besom/v0.5.2";
    flake = false;
  };

  outputs =
    {
      nixpkgs,
      scala-cli-purifier,
      besom,
      ...
    }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ scala-cli-purifier.overlays.default ];
      };
      core = pkgs.scala-cli-purifier.buildScalaCliProject {
        src = "${besom}/core";
        lockFile = ./scala-cli-purifier.lock.json;
      };
    in
    {
      packages.${system}.default = core.packages.besom-core;
    };
}
```

`nix build` then puts the jar at `result/share/java/besom-core.jar`. To work
on besom offline with that lockfile, set `SCALA_CLI_PURIFIER_LOCKFILE` to it
before `scala-cli-purifier prefetch`.

## Credits

The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo).

## License

MIT
