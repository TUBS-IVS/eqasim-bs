import subprocess as sp
import os, shutil

def configure(context):
    context.config("maven_binary", "mvn")
    context.config("maven_skip_tests", False)
    # Optional JDK home for the Maven build. Default "" inherits the process
    # environment (byte-identical to the previous behaviour). Set it when the build
    # needs a specific JDK that is not the environment default -- e.g. eqasim-java
    # 2.2.0 requires JDK 25 while the felix system default is JDK 21. When set, it is
    # exported as JAVA_HOME (and prepended to PATH) for the Maven subprocess only.
    context.config("java_home", "")

def run(context, arguments = [], cwd = None):
    """
        This function calls Maven.
    """
    # Make sure there is a dependency
    context.stage("matsim.runtime.maven")

    if cwd is None:
        cwd = context.path()

    # Prepare temp folder
    temp_path = "%s/__java_temp" % context.path()
    if not os.path.exists(temp_path):
        os.mkdir(temp_path)

    vm_arguments = [
        "-Djava.io.tmpdir=%s" % temp_path
    ]

    if context.config("maven_skip_tests"):
        vm_arguments.append("-DskipTests=true")

    command_line = [
        shutil.which(context.config("maven_binary"))
    ] + vm_arguments + arguments

    # Build the subprocess environment. When java_home is configured, export it as
    # JAVA_HOME so Maven compiles/runs tests with that JDK (the eqasim-java 2.2.0 jar
    # targets Java 25); otherwise inherit the parent environment unchanged.
    build_env = dict(os.environ)
    java_home = context.config("java_home")
    if java_home:
        if not os.path.exists(java_home):
            raise RuntimeError("java_home does not exist: %s" % java_home)
        build_env["JAVA_HOME"] = java_home
        build_env["PATH"] = os.path.join(java_home, "bin") + os.pathsep + build_env.get("PATH", "")

    return_code = sp.check_call(command_line, cwd = cwd, env = build_env)

    if not return_code == 0:
        raise RuntimeError("Maven return code: %d" % return_code)

def validate(context):
    if shutil.which(context.config("maven_binary")) in ["", None]:
        raise RuntimeError("Cannot find Maven binary at: %s" % context.config("maven_binary"))

    if not b"3." in sp.check_output([
        shutil.which(context.config("maven_binary")),
        "-version"
    ], stderr = sp.STDOUT):
        print("WARNING! Maven of at least version 3.x.x is recommended!")

def execute(context):
    pass
