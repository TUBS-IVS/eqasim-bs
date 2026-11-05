# Scratch of the C5f investigation: which classes of the Guice 7.0.0 classes jar call StackTraceElements.forMember or forType (the source line lookup).
import zipfile, os
jar = os.path.expanduser("~/.m2/repository/com/google/inject/guice/7.0.0/guice-7.0.0-classes.jar")
with zipfile.ZipFile(jar) as z:
    for n in sorted(z.namelist()):
        if n.endswith(".class"):
            d = z.read(n)
            if b"StackTraceElements" in d and (b"forMember" in d or b"forType" in d):
                print(n, "forMember" if b"forMember" in d else "", "forType" if b"forType" in d else "")
