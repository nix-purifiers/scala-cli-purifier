// Dependencies from the local repositories: shout from ivy2Local, which
// scala-cli reads by default, and whisper from m2Local.
//> using scala 3.3.4
//> using repository m2Local
//> using dep example.local::shout:0.1.0
//> using dep example.local::whisper:0.1.0
package local

@main def main(): Unit =
  println(shout.shout("from ivy2Local"))
  println(whisper.whisper("FROM M2LOCAL"))
