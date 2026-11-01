// A dependency downloaded from a URL rather than resolved from a repository.
//> using scala 3.3.4
//> using dep "example:hiccup-by-url:1.0.5,url=https://repo.clojars.org/hiccup/hiccup/1.0.5/hiccup-1.0.5.jar"
package url

@main def main(): Unit =
  val found = Option(getClass.getClassLoader.getResource("hiccup/core.clj")).isDefined
  println(s"hiccup from a URL: $found")
