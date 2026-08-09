#!/usr/bin/env python3
"""Static guard for the permanent ZaStoGram promo and free-proxy shortcuts."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DIALOGS_ADAPTER = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/Adapters/DialogsAdapter.java"
DIALOGS_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/DialogsActivity.java"
DIALOG_CELL = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/Cells/DialogCell.java"
SETTINGS_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/SettingsActivity.java"
FREE_PROXY_SETTINGS_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/FreeProxySettingsActivity.java"
STRINGS = ROOT / "TMessagesProj/src/main/res/values/strings.xml"


EXPECTED_STRINGS = {
    "ZapretVpnBot": "ZaSto VPN",
    "FreeProxyChannels": "Бесплатные прокси",
    "FreeProxyMtProxyEveryday": "MTProxy everyday",
    "FreeProxyProxyMtProto": "Proxy MTProto",
    "FreeProxyProxyFreeRu": "Proxy Free Ru",
    "FreeProxyTgMtProxyLol": "TG MTProxy LOL",
    "FreeProxyMemtproxy": "memtproxy",
    "FreeProxyTProxyRu": "TProxy RU",
    "FreeProxyProxyFreeMTProto": "Proxy Free MTProto",
    "FreeProxyTelMTProto": "Tel MTProto",
}

EXPECTED_LINKS = {
    28: ("FreeProxyMtProxyEveryday", "https://t.me/MTProxy_everyday"),
    29: ("FreeProxyProxyMtProto", "https://t.me/ProxyMTProto"),
    30: ("FreeProxyProxyFreeRu", "https://t.me/ProxyFree_Ru"),
    31: ("FreeProxyTgMtProxyLol", "https://t.me/tgmtproxylol"),
    32: ("FreeProxyMemtproxy", "https://t.me/memtproxy"),
    33: ("FreeProxyTProxyRu", "https://t.me/TProxyRU"),
    34: ("FreeProxyProxyFreeMTProto", "https://t.me/ProxyFreeMTProto"),
    35: ("FreeProxyTelMTProto", "https://t.me/TelMTProto"),
}


def main() -> int:
    dialogs_adapter = DIALOGS_ADAPTER.read_text(encoding="utf-8")
    dialogs_activity = DIALOGS_ACTIVITY.read_text(encoding="utf-8")
    dialog_cell = DIALOG_CELL.read_text(encoding="utf-8")
    settings_activity = SETTINGS_ACTIVITY.read_text(encoding="utf-8")
    free_proxy_settings_activity = FREE_PROXY_SETTINGS_ACTIVITY.read_text(encoding="utf-8")
    strings = STRINGS.read_text(encoding="utf-8")
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    for name, value in EXPECTED_STRINGS.items():
        require(
            f'<string name="{name}">{value}</string>' in strings,
            f"Missing string resource {name}={value}",
        )

    require(
        "VIEW_TYPE_ZASTOGRAM_PROMO" in dialogs_adapter,
        "ZaStoGram promo must use a stable dedicated item type",
    )
    require(
        "shouldShowZastogramPromo()" in dialogs_adapter,
        "DialogsAdapter must gate the ZaStoGram row to the default chat list",
    )
    should_show_start = dialogs_adapter.find("private boolean shouldShowZastogramPromo()")
    should_show_end = dialogs_adapter.find("public boolean isZastogramPromoDialog", should_show_start)
    should_show_body = dialogs_adapter[should_show_start:should_show_end]
    require(
        "SharedConfig" not in should_show_body and "ZaStoPrivacy" not in should_show_body,
        "ZaStoGram promo must not depend on proxy, ad, or user-toggle state",
    )
    require(
        'ZASTOGRAM_PROMO_USERNAME = "zastogram"' in dialogs_adapter,
        "DialogsAdapter must use the real ZaStoGram channel username",
    )
    require(
        "filterLegacyProxySponsorDialogs(" in dialogs_adapter
        and "messagesController.promoDialogType == MessagesController.PROMO_TYPE_PROXY" in dialogs_adapter,
        "DialogsAdapter must remove the legacy Telegram proxy promo dialog",
    )
    require(
        "removeZastogramPromoDialogFromArray(" in dialogs_adapter
        and "insertZastogramPromoItem(" in dialogs_adapter
        and "itemInternals.add(0, new ItemInternal(VIEW_TYPE_ZASTOGRAM_PROMO))" in dialogs_adapter,
        "DialogsAdapter must keep ZaStoGram as a separate first item without replacing chats",
    )
    is_promo_method = dialogs_adapter[
        dialogs_adapter.find("public boolean isZastogramPromoDialog"):
        dialogs_adapter.find("private ArrayList<TLRPC.Dialog> filterLegacyProxySponsorDialogs")
    ]
    require(
        "!shouldShowZastogramPromo()" in is_promo_method,
        "ZaStoGram row detection must be limited to the main chat list",
    )
    require(
        "DialogCell.CustomDialog" not in extract_promo_adapter_region(dialogs_adapter)
        and "customDialog.name" not in extract_promo_adapter_region(dialogs_adapter)
        and "customDialog.message" not in extract_promo_adapter_region(dialogs_adapter),
        "ZaStoGram row must not be rendered as a fake CustomDialog",
    )
    require(
        "getUserNameResolver().resolve(ZASTOGRAM_PROMO_USERNAME" in dialogs_adapter
        and "messagesController.dialogs_dict.get(dialogId)" in dialogs_adapter,
        "DialogsAdapter must resolve @zastogram and prefer an existing real dialog",
    )
    require(
        "cell.setZastogramPromo(true)" in dialogs_adapter
        and "cell.setDialog(promoDialog, dialogsType, folderId)" in dialogs_adapter
        and "cell.setDialog(zastogramPromoDialogId, null, 0, false, false)" in dialogs_adapter
        and "timeString = getString(R.string.AppName)" in dialog_cell,
        "ZaStoGram row must bind the real peer and render the app-name promo badge",
    )
    require(
        "isZastogramPromoDialog(position)" in dialogs_activity
        and "openByUserName(DialogsAdapter.ZASTOGRAM_PROMO_USERNAME" in dialogs_activity,
        "DialogsActivity must open @zastogram through Telegram username resolution",
    )
    custom_dialog_start = dialog_cell.find("public void setDialog(CustomDialog dialog)")
    custom_dialog_end = dialog_cell.find("private void checkOnline()", custom_dialog_start)
    custom_dialog_body = dialog_cell[custom_dialog_start:custom_dialog_end]
    require(
        "currentDialogId = 0;" in custom_dialog_body
        and "message = null;" in custom_dialog_body
        and "user = null;" in custom_dialog_body
        and "chat = null;" in custom_dialog_body
        and "encryptedChat = null;" in custom_dialog_body,
        "DialogCell custom rows must clear recycled real-dialog state",
    )
    require(
        dialog_cell.count("customDialog = null;") >= 4,
        "DialogCell real chat/topic setters must clear recycled custom-dialog state",
    )
    real_dialog_start = dialog_cell.find("public void setDialog(TLRPC.Dialog dialog, int type, int folder)")
    real_dialog_end = dialog_cell.find("protected boolean drawLock2()", real_dialog_start)
    real_dialog_body = dialog_cell[real_dialog_start:real_dialog_end]
    require(
        "forumTopic = null;" in real_dialog_body
        and "isTopic = false;" in real_dialog_body
        and "isForum = false;" in real_dialog_body
        and "groupMessages = null;" in real_dialog_body,
        "DialogCell real dialog binding must clear recycled topic/folder state",
    )

    require(
        "SettingCell.Factory.of(1004," in settings_activity
        and "getString(R.string.FreeProxyChannels)" in settings_activity
        and "case 1004:" in settings_activity
        and "presentSettingFragment(new FreeProxySettingsActivity())" in settings_activity,
        "Settings must open the dedicated FreeProxySettingsActivity",
    )
    require(
        "items.add(UItem.asHeader(LocaleController.getString(R.string.FreeProxyChannels)))" in free_proxy_settings_activity,
        "FreeProxySettingsActivity must include the FreeProxyChannels block",
    )
    require(
        "SettingCell.Factory.of(27," in free_proxy_settings_activity
        and "LocaleController.getString(R.string.ZapretVpnBot)" in free_proxy_settings_activity
        and '"@zapretvpns_bot"' in free_proxy_settings_activity,
        "FreeProxySettingsActivity must pin the ZaSto VPN bot above the catalog",
    )
    require(
        free_proxy_settings_activity.find("SettingCell.Factory.of(27,")
        < free_proxy_settings_activity.find("SettingCell.Factory.of(1002,")
        < free_proxy_settings_activity.find("items.add(UItem.asHeader(LocaleController.getString(R.string.FreeProxyChannels)))"),
        "ZaSto VPN bot must be the first row in FreeProxySettingsActivity",
    )
    require(
        'getMessagesController().openByUserName("zapretvpns_bot", this, 1)' in free_proxy_settings_activity,
        "FreeProxySettingsActivity must open the pinned bot inside Telegram",
    )

    for item_id, (string_name, url) in EXPECTED_LINKS.items():
        require(
            re.search(
                rf"SettingCell\.Factory\.of\({item_id},[^;]+LocaleController\.getString\(R\.string\.{string_name}\)\)",
                free_proxy_settings_activity,
                re.DOTALL,
            )
            is not None,
            f"FreeProxySettingsActivity item {item_id} must use {string_name}",
        )
        require(
            re.search(
                rf"case {item_id}:\s+Browser\.openUrl\(getParentActivity\(\), \"{re.escape(url)}\"\);\s+break;",
                free_proxy_settings_activity,
                re.DOTALL,
            )
            is not None,
            f"FreeProxySettingsActivity item {item_id} must open {url}",
        )

    if errors:
        print("Zapret proxy sponsor check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Zapret proxy sponsor check passed")
    return 0


def extract_promo_adapter_region(dialogs_adapter: str) -> str:
    start = dialogs_adapter.find("ZASTOGRAM_PROMO")
    if start < 0:
        return ""
    end = dialogs_adapter.find("case VIEW_TYPE_FORWARD_TO_STORIES_CELL", start)
    if end < 0:
        end = dialogs_adapter.find("case VIEW_TYPE_EMPTY", start)
    return dialogs_adapter[start:end]


if __name__ == "__main__":
    sys.exit(main())
