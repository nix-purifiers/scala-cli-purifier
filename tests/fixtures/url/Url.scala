// A dependency downloaded from a URL rather than resolved from a repository.
//> using scala 3.3.4
//> using dep "example:geny-by-url:1.1.1,url=https://repo1.maven.org/maven2/com/lihaoyi/geny_3/1.1.1/geny_3-1.1.1.jar"
package url

@main def main(): Unit =
  println(geny.Generator(1, 2, 3).map(_ * 2).toSeq.mkString("geny from a URL: ", ",", ""))
