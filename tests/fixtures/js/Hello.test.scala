package hellojs

class HelloSuite extends munit.FunSuite {
  test("greeting names Scala.js") {
    assert(greeting.contains("Scala.js"))
  }
}
