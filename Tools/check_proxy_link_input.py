#!/usr/bin/env python3
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
PROXY_SETTINGS = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxySettingsActivity.java"
ANDROID_UTILITIES = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/AndroidUtilities.java"
PROXY_LINK_HELPER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyLinkHelper.java"
LAUNCH_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/LaunchActivity.java"
STRINGS = ROOT / "TMessagesProj/src/main/res/values/strings.xml"
STRINGS_RU = ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml"


def fail(message: str) -> None:
    raise SystemExit(f"proxy link input check failed: {message}")


def slice_between(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        fail(f"missing block start {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        fail(f"missing block end {end!r}")
    return text[start_index:end_index]


def main() -> int:
    java = PROXY_SETTINGS.read_text(encoding="utf-8")
    android_utilities = ANDROID_UTILITIES.read_text(encoding="utf-8")
    launch_activity = LAUNCH_ACTIVITY.read_text(encoding="utf-8")
    if not PROXY_LINK_HELPER.exists():
        fail("shared ProxyLinkHelper.java must exist")
    proxy_link_helper = PROXY_LINK_HELPER.read_text(encoding="utf-8")
    strings = STRINGS.read_text(encoding="utf-8")
    strings_ru = STRINGS_RU.read_text(encoding="utf-8")

    if 'name="UseProxyLink"' not in strings:
        fail("base strings must define UseProxyLink")
    if 'name="UseProxyLink"' not in strings_ru:
        fail("Russian strings must define UseProxyLink")

    for needle in (
        "private EditTextBoldCursor quickProxyLinkField;",
        "private boolean ignoreQuickProxyLinkChange;",
        "quickProxyLinkField.setHintText(LocaleController.getString(R.string.UseProxyLink));",
        "applyParsedProxyLink(parsedProxyLink, true);",
    ):
        if needle not in java:
            fail(f"ProxySettingsActivity missing {needle}")

    for needle in (
        "public final class ProxyLinkHelper",
        "public static ProxyLink parse(String text)",
        "public static ProxyLink firstFromText(String text)",
        "public static ProxyLink firstFromClipboard(Context context)",
        "public static String dedupeKey(ProxyLink link)",
        "public static final class ProxyLink",
        "public static final int TYPE_SOCKS5",
        "public static final int TYPE_MTPROTO",
    ):
        if needle not in proxy_link_helper:
            fail(f"ProxyLinkHelper missing {needle}")

    for link_marker in (
        "t.me/socks?",
        "tg://socks?",
        "tg:socks?",
        "t.me/proxy?",
        "tg://proxy?",
        "tg:proxy?",
    ):
        if link_marker not in proxy_link_helper:
            fail(f"shared parser must recognize {link_marker}")
    for forbidden in ("TYPE_WSS", "zastogram://wss?", "tg://wss?"):
        if forbidden in proxy_link_helper:
            fail(f"WSS must not be parsed as proxy link data: {forbidden}")
    if "android.content.ClipboardManager" not in proxy_link_helper or "getPrimaryClip()" not in proxy_link_helper:
        fail("shared helper must own safe clipboard extraction")
    if "URLDecoder.decode" not in proxy_link_helper:
        fail("shared parser must URL-decode server, port, secret, user and pass values")
    if "IDN.toASCII" not in proxy_link_helper:
        fail("shared parser must normalize punycode hosts")

    if "private static final class ParsedProxyLink" in java or "URLDecoder.decode" in java:
        fail("ProxySettingsActivity must not keep a duplicate proxy link parser")

    parse_body = slice_between(
        java,
        "private ProxyLinkHelper.ProxyLink parseProxyLink(String text)",
        "private void applyParsedProxyLink",
    )
    if "ProxyLinkHelper.parse(text)" not in parse_body:
        fail("ProxySettingsActivity parser wrapper must delegate to ProxyLinkHelper")

    apply_body = slice_between(
        java,
        "private void applyParsedProxyLink(ProxyLinkHelper.ProxyLink parsedProxyLink, boolean animated)",
        "private void updatePasteCell()",
    )
    for assignment in (
        "inputFields[i].setText(proxyLinkField(parsedProxyLink, i));",
        "setProxyType(parsedProxyLink.type, animated",
        "inputFields[FIELD_IP].setSelection(inputFields[FIELD_IP].length());",
        "AndroidUtilities.hideKeyboard(inputFieldsContainer.findFocus());",
    ):
        if assignment not in apply_body:
            fail(f"link application must contain {assignment}")

    paste_body = slice_between(java, "private void updatePasteCell()", "private void setShareDoneEnabled")
    if "ProxyLinkHelper.ProxyLink parsedProxyLink = parseProxyLink(clipText);" not in paste_body:
        fail("clipboard paste flow must reuse the same parser as the visible link field")
    if "pasteProxyLink = parsedProxyLink;" not in paste_body:
        fail("clipboard paste flow must keep the shared ProxyLink object")

    for needle in (
        "public static boolean showClipboardProxyAlertIfNeeded(Activity activity)",
        "ProxyLinkHelper.firstFromClipboard(activity)",
        "lastClipboardProxyAlertKey",
        "ProxyLinkHelper.dedupeKey(link)",
        "showProxyAlert(activity, link.address, String.valueOf(link.port), link.username, link.password, link.secret)",
    ):
        if needle not in android_utilities:
            fail(f"AndroidUtilities clipboard prompt missing {needle}")
    if "AndroidUtilities.showClipboardProxyAlertIfNeeded(this);" not in launch_activity:
        fail("LaunchActivity.onResume must check clipboard proxy links")

    print("proxy link input check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
