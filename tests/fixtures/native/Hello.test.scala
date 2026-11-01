package hellonative

class HelloSuite extends munit.FunSuite {
  test("greeting names Scala Native") {
    assert(greeting.contains("Scala Native"))
  }
}
