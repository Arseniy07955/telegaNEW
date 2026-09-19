#!/usr/bin/env python3
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
CONNECTIONS_JAVA = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
NOTIFICATIONS_UI = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/NotificationsSettingsActivity.java"
APPLICATION_LOADER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ApplicationLoader.java"
NOTIFICATIONS_SERVICE = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/NotificationsService.java"
APP_START_RECEIVER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/AppStartReceiver.java"
MANIFEST = ROOT / "TMessagesProj/src/main/AndroidManifest.xml"
STRINGS = ROOT / "TMessagesProj/src/main/res/values/strings.xml"
STRINGS_RU = ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    connections = read(CONNECTIONS_JAVA)
    notifications = read(NOTIFICATIONS_UI)
    application_loader = read(APPLICATION_LOADER)
    notifications_service = read(NOTIFICATIONS_SERVICE)
    app_start_receiver = read(APP_START_RECEIVER)
    manifest = read(MANIFEST)

    require(
        'BACKGROUND_NETWORK_ALWAYS_ON = "backgroundNetworkAlwaysOn"' in connections,
        "ConnectionsManager must define a stable background network policy key",
        failures,
    )
    require(
        "isBackgroundNetworkAlwaysOn()" in connections
        and "MessagesController.getGlobalNotificationsSettings()" in connections,
        "ConnectionsManager must read the keep-awake policy from global notification settings",
        failures,
    )
    require(
        "public void applyBackgroundNetworkPolicy()" in connections
        and "public static void applyBackgroundNetworkPolicyForAllAccounts()" in connections
        and "UserConfig.getInstance(a).isClientActivated()" in connections
        and re.search(
            r"if\s*\(isBackgroundNetworkAlwaysOn\(\)\)\s*\{[^{}]*lastPauseTime\s*=\s*0;[^{}]*native_resumeNetwork\(currentAccount,\s*false\);[^{}]*return;",
            connections,
            re.DOTALL,
        )
        is not None
        and "applyBackgroundNetworkPolicy();" in connections,
        "setAppPaused() must route background sleep through a policy method that skips native_pauseNetwork() when keep-awake is enabled",
        failures,
    )
    require(
        re.search(
            r"native_init\([^;]+;\s*//[^\n]*\n(?:\s*//[^\n]*\n)*\s*setPushConnectionEnabled\(enablePushConnection\);\s*//[^\n]*\n(?:\s*//[^\n]*\n)*\s*applyBackgroundNetworkPolicy\(\);",
            connections,
        ) is not None,
        "Every cold-started ConnectionsManager must create its push connection and apply the persisted background policy immediately after native_init()",
        failures,
    )
    require(
        "public static void applyPushConnectionPolicyForAllAccounts()" in connections
        and "manager.setPushConnectionEnabled(manager.isPushConnectionEnabled());" in connections,
        "The global push-connection setting must be applied to every active account",
        failures,
    )
    require(
        "backgroundNetworkAlwaysOnRow" in notifications,
        "Notifications settings must include a row for the keep-awake policy",
        failures,
    )
    require(
        "ConnectionsManager.BACKGROUND_NETWORK_ALWAYS_ON" in notifications,
        "Notifications settings must persist the keep-awake policy with the shared ConnectionsManager key",
        failures,
    )
    require(
        "ConnectionsManager.applyBackgroundNetworkPolicyForAllAccounts()" in notifications,
        "Toggling the policy must re-apply the current native pause/resume state for all active accounts",
        failures,
    )
    require(
        notifications.count("MessagesController.getGlobalNotificationsSettings()") >= 4
        and "ConnectionsManager.applyPushConnectionPolicyForAllAccounts();" in notifications,
        "All three background controls must persist globally and pushConnection must update every account",
        failures,
    )
    require(
        "startForegroundService(serviceIntent)" in application_loader,
        "The keep-alive service must be launched as a foreground service on Android O+",
        failures,
    )
    require(
        "startForeground(NOTIFICATION_ID, builder.build());" in notifications_service
        and "return START_STICKY;" in notifications_service
        and "ConnectionsManager.applyBackgroundNetworkPolicyForAllAccounts();" in notifications_service,
        "NotificationsService must become foreground before keeping every account's network policy alive",
        failures,
    )
    require(
        'android.permission.FOREGROUND_SERVICE_REMOTE_MESSAGING' in manifest
        and re.search(
            r'<service\s+android:name="\.NotificationsService"[^>]*android:exported="false"[^>]*android:foregroundServiceType="remoteMessaging"',
            manifest,
            re.DOTALL,
        ) is not None,
        "NotificationsService must be a private remoteMessaging foreground service",
        failures,
    )
    require(
        '"org.telegram.start".equals(intent.getAction())' in app_start_receiver,
        "The explicit keep-alive restart broadcast must be handled",
        failures,
    )
    require(
        "NotificationsBackgroundNetworkAlwaysOn" in notifications
        and "NotificationsBackgroundNetworkAlwaysOnInfo" in notifications,
        "Notifications settings UI must render the keep-awake title and description",
        failures,
    )

    for path in (STRINGS, STRINGS_RU):
        text = read(path)
        require(
            'name="NotificationsBackgroundNetworkAlwaysOn"' in text,
            f"{path.relative_to(ROOT)} must define NotificationsBackgroundNetworkAlwaysOn",
            failures,
        )
        require(
            'name="NotificationsBackgroundNetworkAlwaysOnInfo"' in text,
            f"{path.relative_to(ROOT)} must define NotificationsBackgroundNetworkAlwaysOnInfo",
            failures,
        )
        require(
            'name="NotificationsBackgroundConnectionActive"' in text,
            f"{path.relative_to(ROOT)} must define the foreground connection notification text",
            failures,
        )

    if failures:
        print("Background network policy guard failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1

    print("Background network policy guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
