#!/usr/bin/env python3
"""Verify MCP tools list coverage.

This script reads ``mcp-tools-list.txt`` at the repository root and ensures that
each listed tool name appears somewhere in the test suite or in the server
configuration.  It helps detect drift between the canonical tools list and the
code exercising or exposing those tools.

The script exits with a non-zero status if any tool name is missing.
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
TOOLS_FILE = ROOT / "mcp-tools-list.txt"
TEST_PATHS = [ROOT / "tests", ROOT / "tests_http"]
CONFIG_PATHS = [ROOT / "function_app.py", ROOT / "app" / "services" / "tools.py"]


def _read_text(paths: Iterable[Path]) -> str:
    content = []
    for path in paths:
        if path.is_file():
            try:
                content.append(path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                pass
        elif path.is_dir():
            for sub in path.rglob("*"):
                if sub.is_file():
                    try:
                        content.append(sub.read_text(encoding="utf-8", errors="ignore"))
                    except Exception:
                        pass
    return "\n".join(content)


def main() -> int:
    tools = []
    for line in TOOLS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split(":", 1)[0].strip()
        if name:
            tools.append(name)

    tests_text = _read_text(TEST_PATHS)
    config_text = _read_text(CONFIG_PATHS)

    missing = [t for t in tools if t not in tests_text and t not in config_text]
    if missing:
        print("Missing MCP tool references:")
        for name in missing:
            print(f" - {name}")
        return 1

    print("All MCP tools are referenced in tests or configuration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
