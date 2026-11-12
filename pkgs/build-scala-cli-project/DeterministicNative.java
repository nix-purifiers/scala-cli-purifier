// SPDX-License-Identifier: MIT
// Scala Native's class hierarchy sets use ScopeInfo's identity hash. Their
// iteration order determines runtime type IDs and module-array indices.
// Hash the unique NIR name instead, preserving identity equality and ABI.
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.jar.JarFile;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import jdk.internal.org.objectweb.asm.ClassReader;
import jdk.internal.org.objectweb.asm.ClassVisitor;
import jdk.internal.org.objectweb.asm.ClassWriter;
import jdk.internal.org.objectweb.asm.MethodVisitor;
import jdk.internal.org.objectweb.asm.Opcodes;

public final class DeterministicNative {
    private static final String SCOPE = "scala/scalanative/linker/ScopeInfo";

    private static byte[] patch(byte[] original) {
        ClassWriter writer = new ClassWriter(0);
        ClassVisitor visitor = new ClassVisitor(Opcodes.ASM8, writer) {
            boolean hasHashCode;

            @Override
            public MethodVisitor visitMethod(int access, String name, String descriptor,
                                             String signature, String[] exceptions) {
                if (name.equals("hashCode") && descriptor.equals("()I")) {
                    hasHashCode = true;
                }
                return super.visitMethod(access, name, descriptor, signature, exceptions);
            }

            @Override
            public void visitEnd() {
                if (!hasHashCode) {
                    MethodVisitor method = super.visitMethod(
                        Opcodes.ACC_PUBLIC, "hashCode", "()I", null, null);
                    method.visitCode();
                    method.visitVarInsn(Opcodes.ALOAD, 0);
                    method.visitMethodInsn(Opcodes.INVOKEVIRTUAL, SCOPE, "name",
                        "()Lscala/scalanative/nir/Global$Top;", false);
                    method.visitMethodInsn(Opcodes.INVOKEVIRTUAL, "java/lang/Object",
                        "hashCode", "()I", false);
                    method.visitInsn(Opcodes.IRETURN);
                    method.visitMaxs(1, 1);
                    method.visitEnd();
                }
                super.visitEnd();
            }
        };
        new ClassReader(original).accept(visitor, 0);
        return writer.toByteArray();
    }

    public static void main(String[] args) throws Exception {
        boolean found = false;
        try (JarFile input = new JarFile(args[0]);
             ZipOutputStream output = new ZipOutputStream(Files.newOutputStream(Path.of(args[1])))) {
            var entries = input.entries();
            while (entries.hasMoreElements()) {
                var entry = entries.nextElement();
                byte[] bytes;
                try (InputStream stream = input.getInputStream(entry)) {
                    bytes = stream.readAllBytes();
                }
                if (entry.getName().equals(SCOPE + ".class")) {
                    bytes = patch(bytes);
                    found = true;
                }
                ZipEntry target = new ZipEntry(entry.getName());
                target.setTime(0);
                output.putNextEntry(target);
                output.write(bytes);
                output.closeEntry();
            }
        }
        if (!found) {
            throw new IllegalArgumentException("Scala Native tools has no " + SCOPE);
        }
    }
}
