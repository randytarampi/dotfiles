#!/usr/bin/env python3
"""
detect-ij-mcp.py — Resolve JetBrains MCP server java + classpath.
"""

import sys
import os
import shlex

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from idea import resolve_idea_mcp_server


def main():
    transport = os.environ.get("IJ_MCP_TRANSPORT", "sse")

    if transport == "sse":
        # SSE transport: no java/classpath needed
        sys.exit(0)

    # Stdio transport: resolve java + classpath
    java_bin, classpath = resolve_idea_mcp_server(transport)

    if java_bin and classpath:
        print(f"export IJ_MCP_SERVER_JAVA={shlex.quote(java_bin)}")
        print(f"export IJ_MCP_SERVER_CLASSPATH={shlex.quote(classpath)}")
        sys.exit(0)

    # Partial or failed resolution
    if not java_bin:
        print(
            "# IJ_MCP_SERVER_JAVA not found — install IntelliJ IDEA or set manually",
            file=sys.stderr,
        )
    if not classpath:
        print(
            "# IJ_MCP_SERVER_CLASSPATH not found — MCP server jars not detected in IntelliJ install",
            file=sys.stderr,
        )

    sys.exit(1)


if __name__ == "__main__":
    main()
