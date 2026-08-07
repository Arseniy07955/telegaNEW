#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from zipfile import BadZipFile, ZipFile


VALID_ABIS = ("armeabi-v7a", "arm64-v8a", "x86", "x86_64")
ABI_PATH_PATTERNS = (
    re.compile(r"^lib/(?P<abi>armeabi-v7a|arm64-v8a|x86|x86_64)/"),
    re.compile(r"^assets/chaquopy/bootstrap-native/(?P<abi>armeabi-v7a|arm64-v8a|x86|x86_64)/"),
    re.compile(r"^assets/chaquopy/(?:requirements|stdlib)-(?P<abi>armeabi-v7a|arm64-v8a|x86|x86_64)\.imy$"),
)


def usage() -> int:
    print(f"Usage: {Path(sys.argv[0]).name} <apk> <{'|'.join(VALID_ABIS)}>", file=sys.stderr)
    return 2


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[2] not in VALID_ABIS:
        return usage()

    apk = Path(sys.argv[1])
    expected_abi = sys.argv[2]
    if not apk.is_file():
        print(f"FAIL: APK does not exist: {apk}", file=sys.stderr)
        return 1

    try:
        with ZipFile(apk) as archive:
            names = archive.namelist()
    except BadZipFile:
        print(f"FAIL: Invalid APK/ZIP archive: {apk}", file=sys.stderr)
        return 1

    foreign_entries: list[str] = []
    seen_expected_native = False
    seen_expected_chaquopy = False
    for name in names:
        for pattern in ABI_PATH_PATTERNS:
            match = pattern.match(name)
            if match is None:
                continue
            abi = match.group("abi")
            if abi != expected_abi:
                foreign_entries.append(name)
            elif name.startswith(f"lib/{expected_abi}/"):
                seen_expected_native = True
            elif name.startswith(f"assets/chaquopy/bootstrap-native/{expected_abi}/"):
                seen_expected_chaquopy = True
            break

    failures: list[str] = []
    if foreign_entries:
        sample = "\n  ".join(foreign_entries[:12])
        suffix = "" if len(foreign_entries) <= 12 else f"\n  ... and {len(foreign_entries) - 12} more"
        failures.append(f"foreign ABI payload is present:\n  {sample}{suffix}")
    if not seen_expected_native:
        failures.append(f"no native libraries found for expected ABI {expected_abi}")
    if not seen_expected_chaquopy:
        failures.append(f"no Chaquopy bootstrap runtime found for expected ABI {expected_abi}")

    if failures:
        print(f"APK ABI isolation check failed for {apk}:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"APK ABI isolation passed: {apk.name} contains only {expected_abi} native payload.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
