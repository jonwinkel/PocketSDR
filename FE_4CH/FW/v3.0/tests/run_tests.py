#!/usr/bin/env python3
"""Compile and run firmware lifecycle tests using the installed FX3 SDK headers."""

import argparse
from pathlib import Path
import subprocess
import tempfile


# Build a native harness without modifying the firmware or SDK.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-root", required=True, type=Path)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    headers = args.sdk_root.resolve() / "fw_lib/1_3_5/inc"
    if not (headers / "cyu3dma.h").is_file():
        parser.error("--sdk-root must contain fw_lib/1_3_5/inc")
    with tempfile.TemporaryDirectory(prefix="pocketsdr-firmware-test-") as folder:
        binary = Path(folder) / "lifecycle"
        subprocess.run([args.cc, "-std=c99", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", "-ffunction-sections", "-fdata-sections",
                        "-DREV_A", "-D__CYU3P_TX__=1", "-I", str(headers),
                        str(Path(__file__).with_name("test_lifecycle.c")),
                        "-Wl,--gc-sections", "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=10)


if __name__ == "__main__":
    main()
