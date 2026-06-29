#!/usr/bin/env python3
"""Static guard for the main chat-list brand title."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DIALOGS_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/DialogsActivity.java"
STRINGS = ROOT / "TMessagesProj/src/main/res/values/strings.xml"
STRINGS_RU = ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml"


def fail(message: str) -> None:
    raise SystemExit(f"main screen brand title check failed: {message}")


def slice_between(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        fail(f"missing block start {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        fail(f"missing block end {end!r}")
    return text[start_index:end_index]


def main() -> int:
    dialogs_activity = DIALOGS_ACTIVITY.read_text(encoding="utf-8")
    strings = STRINGS.read_text(encoding="utf-8")
    strings_ru = STRINGS_RU.read_text(encoding="utf-8")

    if '<string name="AppName">ZaStoGram</string>' not in strings:
        fail("base AppName resource must be ZaStoGram")
    if '<string name="AppName">ZaStoGram</string>' not in strings_ru:
        fail("Russian AppName resource must be ZaStoGram")

    main_title_block = slice_between(
        dialogs_activity,
        "logoDrawable = context.getResources().getDrawable(R.drawable.telegram_logo_2).mutate();",
        "actionBar.setTitle(ssb, statusDrawable);",
    )
    if "ApplicationLoader.applicationContext.getString(R.string.AppName)" not in main_title_block:
        fail("DialogsActivity main title must read AppName from local resources, bypassing cloud strings")
    if "new SpannableStringBuilder(getString(R.string.AppName))" in main_title_block:
        fail("DialogsActivity main title must not use LocaleController.getString for AppName")

    print("main screen brand title check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
