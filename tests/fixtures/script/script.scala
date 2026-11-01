//> using scala 2.13.15
//> using dep com.lihaoyi::os-lib:0.11.3

object Script {
  def main(args: Array[String]): Unit =
    println(s"script on Scala ${scala.util.Properties.versionNumberString.split('.').take(2).mkString(".")}")
}
