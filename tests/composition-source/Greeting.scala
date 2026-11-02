//> using scala 3.3.4
//> using dep example:project-input:99.0
//> using publish.organization io.github.nix-purifiers.example
//> using publish.name greeting
//> using publish.version 1.0.0
//> using publish.license MIT
//> using publish.url https://github.com/nix-purifiers/scala-cli-purifier
//> using publish.vcs github:nix-purifiers/scala-cli-purifier
//> using publish.developer "nix-purifiers|nix-purifiers|https://github.com/nix-purifiers"
package greeting

def greet(name: String): String = projectinput.Greeting.message(name)
@main def main(): Unit = println(greet("publication"))
