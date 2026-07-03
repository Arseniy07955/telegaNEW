#!/usr/bin/env python3
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
SPLASH_DRAWABLE = ROOT / "TMessagesProj/src/main/res/drawable/tg_splash_320.xml"
STYLE_FILES = [
    ROOT / "TMessagesProj/src/main/res/values-v31/styles.xml",
    ROOT / "TMessagesProj/src/main/res/values-night/styles.xml",
]
SPLASH_REFERENCE = "@drawable/tg_splash_320"
REQUIRED_PLANE_MARKERS = [
    'android:name="plane"',
]


def fail(message: str) -> None:
    raise SystemExit(f"splash icon check failed: {message}")


def main() -> None:
    for style_file in STYLE_FILES:
        ElementTree.parse(style_file)
        text = style_file.read_text(encoding="utf-8")
        if SPLASH_REFERENCE not in text:
            fail(f"{style_file.relative_to(ROOT)} must point startup splash at {SPLASH_REFERENCE}")

    ElementTree.parse(SPLASH_DRAWABLE)
    splash_text = SPLASH_DRAWABLE.read_text(encoding="utf-8")
    if 'android:name="glass"' in splash_text:
        fail("startup splash still contains the ZaStoGram glass icon")

    for marker in REQUIRED_PLANE_MARKERS:
        if marker not in splash_text:
            fail(f"startup splash is missing plane logo marker {marker}")

    print("splash icon check passed")


if __name__ == "__main__":
    main()
