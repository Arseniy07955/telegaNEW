#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/NotificationsController.java"
DISABLED_RECEIVER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/NotificationsDisabledReceiver.java"
MANIFEST = ROOT / "TMessagesProj/src/main/AndroidManifest.xml"


def method_body(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        return ""
    brace = source.find("{", start)
    if brace < 0:
        return ""
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace:index + 1]
    return ""


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    controller = CONTROLLER.read_text(encoding="utf-8", errors="replace")
    manifest = MANIFEST.read_text(encoding="utf-8", errors="replace")
    validate_channel = method_body(controller, "private String validateChannelId(")
    recreate_blocked = method_body(controller, "private boolean shouldRecreateBlockedChannel(")

    require(validate_channel, "validateChannelId() must remain present", failures)
    for forbidden in (
        "updateServerNotificationsSettings(",
        "getGlobalNotificationsKey(",
        'putInt("notify2_',
        'putBoolean("EnableAllStories"',
        'putBoolean("EnableReactionsMessages"',
    ):
        require(
            forbidden not in validate_channel,
            f"validateChannelId() must not turn Android channel state into Telegram mute policy ({forbidden})",
            failures,
        )

    require(
        "hasUserSetImportance()" in recreate_blocked
        and "Build.VERSION_CODES.Q" in recreate_blocked
        and "requestedImportance == NotificationManager.IMPORTANCE_NONE" in recreate_blocked,
        "stale blocked channels may be recreated only when Android can prove the user did not set their importance",
        failures,
    )
    require(
        "NotificationsDisabledReceiver" not in manifest
        and "NOTIFICATION_CHANNEL_BLOCK_STATE_CHANGED" not in manifest,
        "Android channel broadcasts must not be registered as Telegram mute-policy writers",
        failures,
    )
    require(
        not DISABLED_RECEIVER.exists(),
        "the obsolete Android-to-Telegram mute receiver must stay removed",
        failures,
    )
    require(
        "private void deleteStoredNotificationChannels(" in controller
        and '"org.telegram.key" + dialogId + "_" + topicId' in controller
        and 'deleteStoredNotificationChannels(preferences, editor, baseKey, baseKey + "_ia")' in controller,
        "channel cleanup must delete hashed current keys for one dialog/topic or global category without crossing ownership boundaries",
        failures,
    )
    require(
        "public void setDialogNotificationsSettings(" in controller
        and "public void muteDialog(" in controller,
        "explicit Telegram notification-setting owners must remain available",
        failures,
    )

    if failures:
        print("Notification mute ownership guard failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1

    print("Notification mute ownership guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
