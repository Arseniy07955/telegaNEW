#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MEDIA_DATA = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/MediaDataController.java"
CHAT = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ChatActivity.java"
PREVIEW = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/Cells/ThemePreviewMessagesCell.java"
SETTINGS = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ReactionsDoubleTapManageActivity.java"
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
    media_data = read(MEDIA_DATA)
    chat = read(CHAT)
    preview = read(PREVIEW)
    settings = read(SETTINGS)
    strings = read(STRINGS)
    strings_ru = read(STRINGS_RU)
    failures: list[str] = []

    for literal in (
        'getBoolean("reaction_on_double_tap_enabled", true)',
        "public boolean isDoubleTapReactionEnabled()",
        "public void setDoubleTapReactionEnabled(boolean enabled)",
        'putBoolean("reaction_on_double_tap_enabled", enabled)',
    ):
        require(media_data, literal, "per-account enabled preference", failures)

    require(chat, "chatMode == MODE_QUICK_REPLIES || !getMediaDataController().isDoubleTapReactionEnabled()", "double-tap eligibility guard", failures)
    require(chat, "if (!getMediaDataController().isDoubleTapReactionEnabled() || getParentActivity() == null", "double-tap execution guard", failures)
    require(preview, "!mediaDataController.isDoubleTapReactionEnabled()", "preview double-tap guard", failures)

    for literal in (
        "int enabledRow;",
        "view = new TextCheckCell(context);",
        "R.string.EnableDoubleTapReactions",
        "R.string.DoubleTapReactionsDisabledInfo",
        "mediaDataController.setDoubleTapReactionEnabled(enabled);",
        "enabledRow = rowCount++;",
    ):
        require(settings, literal, "quick-reaction toggle UI", failures)

    for text, label in ((strings, "English strings"), (strings_ru, "Russian strings")):
        require(text, 'name="EnableDoubleTapReactions"', label, failures)
        require(text, 'name="DoubleTapReactionsDisabledInfo"', label, failures)

    if failures:
        print("Double-tap reaction toggle guard failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("Double-tap reaction toggle guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
