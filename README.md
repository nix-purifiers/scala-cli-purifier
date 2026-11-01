# scala-cli-purifier

A command and a Nix library that build [scala-cli](https://scala-cli.virtuslab.org)
projects in the Nix sandbox. `scala-cli-purifier lock` records the files
scala-cli downloads for a project in a lockfile, and `buildScalaCliProject`
builds the project from that lockfile. The project needs no Nix files of
its own, and nothing generated is committed besides the lockfile.

- **A lockfile.** `scala-cli-purifier lock` writes
  `scala-cli-purifier.lock.json` at the project root. It lists every file
  scala-cli downloaded by URL and SHA-256, along with the scala-cli
  version, the Java version, and the scala-cli arguments of the build.
- **Sandboxed builds.** `buildScalaCliProject`, which the flake's overlay
  adds to a package set, fetches each locked file and runs
  `scala-cli package` offline. The package holds the project's assembly jar
  and a launcher for it.
- **scala-cli on the locked downloads.** `scala-cli-purifier exec` runs any
  scala-cli command offline, on the locked files, which it fetches through
  Nix.

## How the lock is made

`scala-cli-purifier lock` runs scala-cli with an empty coursier cache and
an empty home directory, the way the Nix build runs it:

```zsh
scala-cli package --power --server=false --java-home <jdk> \
  --assembly --preamble=false -o <jar> -f <arguments>
```

With `--test`, it also runs `scala-cli test` with the same arguments, so
the test dependencies are locked too, and the Nix build runs the tests.
Every file coursier downloaded goes into the lockfile. The arguments
default to `.`, the project directory; any other scala-cli inputs and
options go after the command, and they must suit both `package` and `test`.

Before it writes the lockfile, `lock` builds the project again, offline,
from the recorded files alone. The lockfile leaves out the
`maven-metadata.xml` version listings coursier fetches, because their
content changes upstream whenever a version is published, and a pinned
hash would then stop matching. If the offline build needs them, for a
dependency declared with a version range, `lock` locks them too and warns.

The lock run passes `--java-home`, so scala-cli never downloads a JVM. It
uses the JDK in `JAVA_HOME`, or this flake's JDK 21. The Nix build uses
the headless JDK of the same major version from the package set.

A proxy in `https_proxy` or `http_proxy`, with or without credentials,
reaches scala-cli as Java system properties.

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
returns `package` and `packages.<name>`, where the name is the project
directory's name unless `lock --name` gave another. It takes
`lockFile`, `jdk`, `fetchurl` (for credentials or mirrors), `doCheck` and
`srcFilter`; `nix/lib/build-scala-cli-project.nix` documents each one.

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
command offline on them. A dependency that is not in the lockfile fails to
resolve, and `lock` adds it. `exec` runs scala-cli without its Bloop build
server (`--server=false`), because the lock run does not lock Bloop.

## Credits

The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo). The Nix library
follows [sbt-nix-backend](https://github.com/clhodapp/sbt-nix-backend),
and the coursier cache code (`pkgs/cli/coursier_cache.py`) is shared with
the Mill purifier.

## License

MIT
