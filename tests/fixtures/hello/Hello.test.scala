package hello

class HelloSuite extends munit.FunSuite {
  test("greeting is JSON") {
    assertEquals(greeting, """{"hello":"Nix World"}""")
  }
}
