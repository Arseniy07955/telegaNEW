#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHARED_CONFIG = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/SharedConfig.java"
PROFILE = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProfileActivity.java"
LITE_MODE = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/LiteModeSettingsActivity.java"
STRINGS = ROOT / "TMessagesProj/src/main/res/values/strings.xml"
STRINGS_RU = ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml"


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(f"Missing required file: {path.relative_to(ROOT)}")


def require(text: str, literal: str, label: str, failures: list[str]) -> None:
    if literal not in text:
        failures.append(f"{label}: missing {literal!r}")


def main() -> int:
    shared_config = read(SHARED_CONFIG)
    profile = read(PROFILE)
    lite_mode = read(LITE_MODE)
    strings = read(STRINGS)
    strings_ru = read(STRINGS_RU)
    failures: list[str] = []

    for literal in (
        "public static boolean profileAvatarBlur;",
        'profileAvatarBlur = preferences.getBoolean("profile_avatar_blur", false);',
        "public static void setProfileAvatarBlur(boolean enabled)",
        'putBoolean("profile_avatar_blur", profileAvatarBlur)',
    ):
        require(shared_config, literal, "avatar blur preference defaults to off", failures)

    for literal in (
        "this.blurEnabled = SharedConfig.profileAvatarBlur;",
        "avatarsBlurView = SharedConfig.profileAvatarBlur ? new ProfileGalleryBlurView(context) : null;",
    ):
        require(profile, literal, "profile avatar blur guard", failures)

    for literal in (
        "public static final int SWITCH_TYPE_PROFILE_AVATAR_BLUR = 2;",
        "item.type == SWITCH_TYPE_PROFILE_AVATAR_BLUR",
        "SharedConfig.setProfileAvatarBlur(blur);",
        "R.string.LiteProfileAvatarBlur",
        "R.string.LiteProfileAvatarBlurInfo",
    ):
        require(lite_mode, literal, "avatar blur toggle UI", failures)

    for text, label in ((strings, "English strings"), (strings_ru, "Russian strings")):
        require(text, 'name="LiteProfileAvatarBlur"', label, failures)
        require(text, 'name="LiteProfileAvatarBlurInfo"', label, failures)

    if failures:
        print("Profile avatar blur toggle guard failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("Profile avatar blur toggle guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
