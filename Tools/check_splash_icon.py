#!/usr/bin/env python3
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
STYLE_FILES = [
    ROOT / "TMessagesProj/src/main/res/values-v31/styles.xml",
    ROOT / "TMessagesProj/src/main/res/values-night/styles.xml",
]
# telegaNEW: Use the app's launcher icon for the splash screen
SPLASH_REFERENCE = "@mipmap/ic_launcher"


def fail(message: str) -> None:
    raise SystemExit(f"splash icon check failed: {message}")


def main() -> None:
    for style_file in STYLE_FILES:
        ElementTree.parse(style_file)
        text = style_file.read_text(encoding="utf-8")
        if SPLASH_REFERENCE not in text:
            fail(f"{style_file.relative_to(ROOT)} must point startup splash at {SPLASH_REFERENCE}")

    print("splash icon check passed")


if __name__ == "__main__":
    main()
