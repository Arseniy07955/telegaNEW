#!/usr/bin/env python3
"""Host compile + unit-test run of the mtproxy module with MSVC Build Tools.

There is no local NDK, so this is the only local compile gate for native
MTProxy changes. The module is dependency-free by design (see
mtproxy/README.md); the tiny system surface it touches (pthread
mutex, RAND_bytes, HMAC, inet_pton) is stubbed in Tools/mtproxy_host_stubs.

After compiling every module translation unit, this links them with
Tools/mtproxy_host_tests/mtproxy_host_tests.cpp and RUNS the resulting
executable: the retry-hold and terminal-diagnostic decision paths (both
historical livelock sources) are exercised with real assertions, not just
compiled. The RAND_bytes stub keeps buffers zeroed, so jitter is
deterministic in tests.

Exits non-zero on any compile, link or test failure, so it can run inside
guard suites.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "TMessagesProj/jni/mtproxy"
STUBS = ROOT / "Tools/mtproxy_host_stubs"
TESTS = ROOT / "Tools/mtproxy_host_tests/mtproxy_host_tests.cpp"

VSWHERE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
    "Microsoft Visual Studio/Installer/vswhere.exe"


def find_vsdevcmd():
    if not VSWHERE.exists():
        return None
    try:
        out = subprocess.run(
            [str(VSWHERE), "-latest", "-products", "*",
             "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"],
            capture_output=True, text=True, timeout=60, check=False,
        ).stdout.strip().splitlines()
    except OSError:
        return None
    for line in out:
        candidate = Path(line.strip()) / "Common7/Tools/VsDevCmd.bat"
        if candidate.exists():
            return candidate
    return None


def main() -> int:
    sources = sorted(MODULE.glob("*.cpp"))
    if not sources:
        print(f"mtproxy host build failed: no sources in {MODULE}")
        return 1
    vsdevcmd = find_vsdevcmd()
    if vsdevcmd is None:
        print("mtproxy host build SKIPPED: MSVC Build Tools not found (vswhere).")
        # Skip, don't fail: CI and other machines may not have MSVC.
        return 0
    objdir = Path(tempfile.mkdtemp(prefix="mtproxy_host_"))
    cl_args = " ".join(
        ["cl", "/nologo", "/c", "/std:c++17", "/EHsc", "/W3",
         "/DWIN32_LEAN_AND_MEAN",
         f'/I"{STUBS}"',
         f'/Fo"{objdir}\\\\"']
        + [f'"{src}"' for src in sources]
    )
    test_compile_args = " ".join(
        ["cl", "/nologo", "/c", "/std:c++17", "/EHsc", "/W3",
         "/DWIN32_LEAN_AND_MEAN",
         f'/I"{STUBS}"', f'/I"{MODULE}"',
         f'/Fo"{objdir}\\\\"',
         f'"{TESTS}"']
    )
    link_args = " ".join(
        ["cl", "/nologo", f'/Fe:"{objdir}\\mtproxy_host_tests.exe"',
         f'"{objdir}\\*.obj"']
    )
    batch = objdir / "build.bat"
    batch.write_text(
        "@echo off\r\n"
        f'call "{vsdevcmd}" -arch=x64 -no_logo\r\n'
        "if errorlevel 1 exit /b 1\r\n"
        f"{cl_args}\r\n"
        "if errorlevel 1 exit /b 2\r\n"
        f"{test_compile_args}\r\n"
        "if errorlevel 1 exit /b 3\r\n"
        f"{link_args}\r\n"
        "if errorlevel 1 exit /b 4\r\n"
        f'"{objdir}\\mtproxy_host_tests.exe"\r\n'
        "if errorlevel 1 exit /b 5\r\n",
        encoding="ascii",
    )
    try:
        proc = subprocess.run(
            ["cmd.exe", "/d", "/c", str(batch)],
            capture_output=True, text=True, timeout=180, check=False,
        )
    except subprocess.TimeoutExpired:
        print("mtproxy host build FAILED: timed out (likely an infinite loop "
              "in the test binary - see rejection-sampling note in "
              "Tools/mtproxy_host_stubs/openssl/rand.h)")
        return 1
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        stage = {2: "compile", 3: "test compile", 4: "link", 5: "test run"}.get(proc.returncode, "environment")
        print(f"mtproxy host build FAILED ({stage}):")
        for line in output.splitlines():
            line = line.strip()
            if line and not line.endswith(".cpp"):
                print(f"  {line}")
        return 1
    warnings = [l for l in output.splitlines() if ": warning" in l]
    for line in warnings:
        print(f"  {line.strip()}")
    tests_passed = "mtproxy host tests passed" in output
    if not tests_passed:
        print("mtproxy host build FAILED: test binary produced no pass marker")
        return 1
    print(f"mtproxy host build + tests passed ({len(sources)} translation units"
          f"{', ' + str(len(warnings)) + ' warnings' if warnings else ''}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
