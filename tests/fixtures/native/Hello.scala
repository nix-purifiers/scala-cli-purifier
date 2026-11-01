//> using scala 3.3.4
//> using platform scala-native
//> using test.dep org.scalameta::munit::1.0.2
package hellonative

def greeting: String = "hello from Scala Native"

@main def main(): Unit = println(greeting)
