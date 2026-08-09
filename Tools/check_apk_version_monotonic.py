#!/usr/bin/env python3
"""Проверка, что versionCode собранного APK строго выше опубликованного.

Android отказывается ставить пакет с меньшим или равным versionCode поверх
установленного: пользователь видит только «Приложение не установлено», без
намёка на причину. Ошибка стоила одного выпущенного и неустанавливаемого
релиза (1.1.13, versionCode 699101451 против 699101531 у 1.1.12), поэтому
проверка обязательна перед публикацией.

Использование:
    check_apk_version_monotonic.py <apk> [<apk> ...] --baseline <versionCode>
    check_apk_version_monotonic.py <apk> [<apk> ...] --repo zastogram/ZaStoGram

Во втором варианте базовый versionCode берётся из последнего стабильного
релиза Forgejo: скачивается его arm64-APK и читается versionCode. Требуется
доступ к сети; при его отсутствии используйте --baseline.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path


def find_aapt() -> str:
    candidates = sorted(Path("/opt/android-sdk/build-tools").glob("*/aapt"), reverse=True)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise SystemExit("FAIL: не найден aapt в /opt/android-sdk/build-tools")


def version_code(apk: Path, aapt: str) -> int:
    if not apk.is_file():
        raise SystemExit(f"FAIL: нет файла {apk}")
    if not zipfile.is_zipfile(apk):
        raise SystemExit(f"FAIL: {apk} не является APK")
    out = subprocess.run(
        [aapt, "d", "badging", str(apk)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    match = re.search(r"versionCode='(\d+)'", out)
    if match is None:
        raise SystemExit(f"FAIL: в {apk.name} не найден versionCode")
    return int(match.group(1))


def published_baseline(repo: str, aapt: str) -> tuple[int, str]:
    import json
    import tempfile
    import urllib.request

    api = f"https://git.zapret.moe/api/v1/repos/{repo}/releases/latest"
    with urllib.request.urlopen(api, timeout=30) as response:
        release = json.load(response)
    tag = release.get("tag_name", "?")
    asset = next(
        (a for a in release.get("assets", []) if "arm64" in a.get("name", "")),
        None,
    )
    if asset is None:
        raise SystemExit(f"FAIL: в релизе {tag} нет arm64-APK для сравнения")
    with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as tmp:
        with urllib.request.urlopen(asset["browser_download_url"], timeout=600) as src:
            while chunk := src.read(1 << 20):
                tmp.write(chunk)
        path = Path(tmp.name)
    try:
        return version_code(path, aapt), tag
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("apks", nargs="+", type=Path)
    parser.add_argument("--baseline", type=int)
    parser.add_argument("--repo")
    args = parser.parse_args()

    if (args.baseline is None) == (args.repo is None):
        raise SystemExit("FAIL: укажите ровно одно из --baseline или --repo")

    aapt = find_aapt()
    if args.baseline is not None:
        baseline, source = args.baseline, "переданное значение"
    else:
        baseline, tag = published_baseline(args.repo, aapt)
        source = f"опубликованный релиз {tag}"

    failures = []
    for apk in args.apks:
        code = version_code(apk, aapt)
        status = "OK" if code > baseline else "НИЖЕ ИЛИ РАВЕН"
        print(f"{apk.name}: versionCode {code} — {status}")
        if code <= baseline:
            failures.append(
                f"{apk.name}: versionCode {code} не выше базового {baseline} "
                f"({source}) — Android откажет в установке"
            )

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1

    print(f"\nAPK version monotonicity guard passed (база {baseline}, {source}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
