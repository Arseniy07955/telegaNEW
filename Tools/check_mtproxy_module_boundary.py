#!/usr/bin/env python3
"""Guard: the jni/mtproxy module must stay dependency-free.

The MTProxy core (TMessagesProj/jni/mtproxy) is pure policy/parsing
logic: no sockets, no logging, no ConnectionsManager, and since the
MT_PROXY_STARTUP_* limits moved into MtProxyOptions.h, no tgnet headers
at all. Any quoted include of a non-module header re-entangles the module
with the transport layer and defeats the extraction.

System includes are whitelisted too: the module must keep compiling on the
host (Tools/build_mtproxy_host.py stubs exactly this surface), so growing
a new platform dependency is a deliberate act, not an accident.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "TMessagesProj/jni/mtproxy"

ALLOWED_EXTERNAL = set()

# Platform surface mirrored by Tools/mtproxy_host_stubs. Extend both together.
ALLOWED_SYSTEM = {
    # C/C++ standard library
    "algorithm", "array", "cctype", "climits", "cstddef", "cstdint",
    "cstring", "deque", "errno.h", "functional", "map", "memory", "set",
    "sstream", "stddef.h", "stdint.h", "string", "string.h", "time.h",
    "unordered_map", "unordered_set", "utility", "vector",
    # platform / third-party (stubbed for host build)
    "arpa/inet.h", "netinet/in.h", "openssl/hmac.h", "openssl/rand.h",
    "pthread.h",
}

INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)
SYSTEM_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+<([^>]+)>', re.MULTILINE)


def main() -> int:
    failures = []
    if not MODULE.is_dir():
        print(f"MTProxy module boundary check failed: {MODULE} does not exist")
        return 1
    local = {p.name for p in MODULE.iterdir() if p.suffix in (".h", ".cpp")}
    if not local:
        print("MTProxy module boundary check failed: module directory is empty")
        return 1
    for path in sorted(MODULE.iterdir()):
        if path.suffix not in (".h", ".cpp"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for inc in INCLUDE_RE.findall(text):
            if inc in local or inc in ALLOWED_EXTERNAL:
                continue
            failures.append(
                f"{path.name} includes \"{inc}\" - mtproxy/ may only include "
                f"its own headers"
            )
        for inc in SYSTEM_INCLUDE_RE.findall(text):
            if inc in ALLOWED_SYSTEM:
                continue
            failures.append(
                f"{path.name} includes <{inc}> which is not in the module's "
                f"platform whitelist - extend ALLOWED_SYSTEM and "
                f"Tools/mtproxy_host_stubs together if this is deliberate"
            )
    if failures:
        print("MTProxy module boundary check failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("MTProxy module boundary guard passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
