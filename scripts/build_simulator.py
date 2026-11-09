#!/usr/bin/env python3
"""Compile the very same C++ stock engine used by the ESP32 firmware."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [ROOT / "simulator/main.cpp", ROOT / "firmware/stockweight/stock_engine.h", Path(__file__)]


def build() -> Path:
    target = ROOT / "build/stockweight-simulator"
    if not target.exists() or any(p.stat().st_mtime > target.stat().st_mtime for p in SOURCES):
        target.parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".compile-", dir=target.parent) as temporary:
            compiled = Path(temporary) / "simulator"
            subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
                "-I", str(ROOT / "firmware/stockweight"), str(SOURCES[0]), "-o", str(compiled),
            ], check=True)
            os.replace(compiled, target)
    return target


if __name__ == "__main__":
    print(build())
