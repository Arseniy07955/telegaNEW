#!/usr/bin/env python3
"""Static guard for PluginUtils JavaDoc comments that must survive javac."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_UTILS = ROOT / "TMessagesProj/src/main/java/org/telegram/plugins/PluginUtils.java"


def main() -> int:
    java = PLUGIN_UTILS.read_text(encoding="utf-8")
    errors: list[str] = []
    in_multiline_javadoc = False

    for line_number, line in enumerate(java.splitlines(), start=1):
        if not in_multiline_javadoc:
            start = line.find("/**")
            if start >= 0 and "*/" not in line[start + 3:]:
                in_multiline_javadoc = True
            continue

        closer = line.find("*/")
        if closer < 0:
            continue

        if line.strip() != "*/":
            errors.append(
                f"line {line_number}: JavaDoc closer appears inside comment text"
            )
        in_multiline_javadoc = False

    if in_multiline_javadoc:
        errors.append("unterminated JavaDoc comment")

    if errors:
        print("PluginUtils JavaDoc check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("PluginUtils JavaDoc check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
