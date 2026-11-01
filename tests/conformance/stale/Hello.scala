package hello

def greeting: String = upickle.default.write(Map("hello" -> "Nix World"))

@main def main(): Unit = println(greeting)
