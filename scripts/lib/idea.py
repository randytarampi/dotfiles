import os
import sys
import shutil
import re
import glob
import subprocess
from pathlib import Path


def resolve_idea_app() -> str:
    """
    Resolve the IntelliJ IDEA app bundle path.
    Returns: Path to the app bundle or empty string if not found.
    """
    home = Path.expanduser(Path("~"))
    candidates = []

    # 1a. Try parsing Toolbox CLI script for the IDE app path
    idea_cmd = shutil.which("idea")
    if idea_cmd:
        candidates.append(idea_cmd)

    try:
        # Check Homebrew prefix
        result = subprocess.run(
            ["brew", "--prefix"], capture_output=True, text=True, timeout=2
        )
        brew_prefix = result.stdout.strip()
        if brew_prefix:
            brew_idea = os.path.join(brew_prefix, "bin", "idea")
            if os.path.isfile(brew_idea):
                candidates.append(brew_idea)
    except Exception:
        pass

    candidates.extend(
        [
            "/usr/local/bin/idea",
            str(home / ".local/share/JetBrains/Toolbox/scripts/idea"),
        ]
    )

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                # Find IntelliJ IDEA.app path inside the shell script
                match = re.search(r"/.*/IntelliJ IDEA(?:\s+CE)?\.app", content)
                if match:
                    app_path = match.group(0)
                    if os.path.isdir(app_path):
                        return app_path
            except Exception:
                pass

    # 1b. Look in standard locations (macOS)
    search_dirs = [
        home / "Applications",
        Path("/Applications"),
        home / ".local/share/JetBrains/Toolbox/apps",
    ]

    # Check ultimate editions first, then CE editions
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for p in glob.glob(os.path.join(search_dir, "IntelliJ IDEA*.app")):
            if os.path.isdir(p):
                return p

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for p in glob.glob(os.path.join(search_dir, "IntelliJ IDEA CE*.app")):
            if os.path.isdir(p):
                return p

    # 1d. Linux: check Toolbox apps path
    if sys.platform.startswith("linux"):
        pattern = str(home / ".local/share/JetBrains/Toolbox/apps/Core/ch-0/*/idea-*/")
        dirs = glob.glob(pattern)
        if dirs and os.path.isdir(dirs[0]):
            return dirs[0]

        pattern = str(home / ".local/share/JetBrains/Toolbox/apps/IDEA*/ch-0/*/IDEA-*/")
        dirs = glob.glob(pattern)
        if dirs and os.path.isdir(dirs[0]):
            return dirs[0]

    return ""


def resolve_idea_java(idea_app_path: str) -> str:
    """
    Resolve the Java binary from the IDEA installation.
    """
    if idea_app_path:
        java_candidates = [
            os.path.join(idea_app_path, "Contents/jbr/Contents/Home/bin/java"),
            os.path.join(idea_app_path, "jbr/bin/java"),
        ]
        for candidate in java_candidates:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

    # Try macOS java_home
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/usr/libexec/java_home"], capture_output=True, text=True, timeout=2
            )
            java_home = result.stdout.strip()
            if java_home:
                candidate = os.path.join(java_home, "bin/java")
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
        except Exception:
            pass

    # Fall back to PATH
    cmd_java = shutil.which("java")
    if cmd_java:
        return cmd_java

    return ""


def resolve_idea_classpath(idea_app_path: str) -> str:
    """
    Resolve the MCP server classpath from the IDEA installation.
    """
    if not idea_app_path:
        return ""

    contents = os.path.join(idea_app_path, "Contents")
    if not os.path.isdir(contents):
        contents = idea_app_path

    plugin_lib = os.path.join(contents, "plugins/mcpserver/lib")
    app_lib = os.path.join(contents, "lib")

    classpath_jars = []

    # Plugin jars (mcpserver-frontend.jar is the entry point)
    if os.path.isdir(plugin_lib):
        for jar in glob.glob(os.path.join(plugin_lib, "*.jar")):
            if os.path.isfile(jar):
                classpath_jars.append(jar)

    # Required runtime jars from app lib
    required_prefixes = [
        "util-8",
        "intellij.libraries.kotlinx.coroutines.core",
        "intellij.libraries.ktor.client.cio",
        "intellij.libraries.ktor.client",
        "intellij.libraries.ktor.network.tls",
        "intellij.libraries.ktor.io",
        "intellij.libraries.ktor.utils",
        "intellij.libraries.kotlinx.io",
        "intellij.libraries.kotlinx.serialization.core",
        "intellij.libraries.kotlinx.serialization.json",
    ]

    if os.path.isdir(app_lib):
        for prefix in required_prefixes:
            for jar in glob.glob(os.path.join(app_lib, f"{prefix}.jar")):
                if os.path.isfile(jar):
                    classpath_jars.append(jar)
                    break

    if classpath_jars:
        return os.pathsep.join(classpath_jars)

    return ""


def resolve_idea_mcp_server(transport: str = None) -> tuple[str, str]:
    """
    Full resolution: find IJ app, resolve java + classpath.
    Returns (java_binary, classpath).
    """
    if transport is None:
        transport = os.environ.get("IJ_MCP_TRANSPORT", "sse")

    if transport == "sse":
        return "", ""

    idea_app = resolve_idea_app()
    if not idea_app:
        return "", ""

    java_bin = resolve_idea_java(idea_app)
    classpath = resolve_idea_classpath(idea_app)

    if java_bin and classpath:
        return java_bin, classpath

    return "", ""
