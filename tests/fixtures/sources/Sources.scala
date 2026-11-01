package sources

import scala.io.Source

def resource(name: String): Option[String] =
  Option(getClass.getClassLoader.getResourceAsStream(name)).map(in => Source.fromInputStream(in).mkString)

@main def main(): Unit =
  // hiccup is a Clojure library from Clojars; its jar holds Clojure sources.
  if resource("hiccup/core.clj").isDefined then println("hiccup from Clojars")
  resource("greeting.txt").foreach(text => println(text.trim))
  println(s"sourcecode from a version range: ${sourcecode.Text(1 + 1).source}")
