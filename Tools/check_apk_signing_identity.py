#!/usr/bin/env python3
"""Проверка, что APK подписан настоящим релизным ключом ZaStoGram.

Android отказывается обновлять установленное приложение, если подпись сменилась,
и сообщает об этом единственной фразой «Приложение не установлено»; Play Protect
вдобавок помечает неизвестного подписанта как угрозу. Именно так вышли 1.1.13 и
1.1.14: сборка не получила внешний ключ и молча взяла запасной из репозитория.

Использование:
    check_apk_signing_identity.py <apk> [<apk> ...]
    check_apk_signing_identity.py <apk> --expect <SHA-256 без двоеточий>
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


# Отпечаток ключа, которым подписаны все стабильные релизы начиная с 1.1.х.
# Меняется только вместе с осознанной сменой ключа, что ломает обновление у
# всех установленных клиентов, поэтому правка этой строки — отдельное решение.
RELEASE_CERT_SHA256 = "84315d38dddf07283ad9d0f607b2fbb2d712214a436ed81923925fd10f9502a4"


def find_apksigner() -> str:
    candidates = sorted(
        Path("/opt/android-sdk/build-tools").glob("*/apksigner"), reverse=True
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise SystemExit("FAIL: не найден apksigner в /opt/android-sdk/build-tools")


def signer_fingerprint(apk: Path, apksigner: str) -> str:
    if not apk.is_file():
        raise SystemExit(f"FAIL: нет файла {apk}")
    result = subprocess.run(
        [apksigner, "verify", "--print-certs", str(apk)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"FAIL: {apk.name} не проходит проверку подписи")
    match = re.search(
        r"certificate SHA-256 digest:\s*([0-9a-fA-F]{64})", result.stdout
    )
    if match is None:
        raise SystemExit(f"FAIL: в {apk.name} не найден отпечаток сертификата")
    return match.group(1).lower()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("apks", nargs="+", type=Path)
    parser.add_argument(
        "--expect",
        default=RELEASE_CERT_SHA256,
        help="ожидаемый SHA-256 сертификата (по умолчанию — релизный ключ)",
    )
    args = parser.parse_args()

    expected = args.expect.replace(":", "").lower()
    apksigner = find_apksigner()
    failures = []
    for apk in args.apks:
        actual = signer_fingerprint(apk, apksigner)
        ok = actual == expected
        print(f"{apk.name}: {actual} — {'OK' if ok else 'ЧУЖОЙ КЛЮЧ'}")
        if not ok:
            failures.append(
                f"{apk.name} подписан ключом {actual}, а нужен {expected}. "
                "Обновление поверх установленного приложения будет отвергнуто."
            )

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1

    print("\nAPK signing identity guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
