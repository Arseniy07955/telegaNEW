#!/usr/bin/env python3
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "diagnostics": ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckDiagnostics.java",
    "connections_java": ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java",
    "notification_center": ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/NotificationCenter.java",
    "proxy_list": ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxyListActivity.java",
    "launch": ROOT / "TMessagesProj/src/main/java/org/telegram/ui/LaunchActivity.java",
    "dialogs": ROOT / "TMessagesProj/src/main/java/org/telegram/ui/DialogsActivity.java",
    "chat_avatar": ROOT / "TMessagesProj/src/main/java/org/telegram/ui/Components/ChatAvatarContainer.java",
    "profile": ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProfileActivity.java",
    "rotation": ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyRotationController.java",
    "values": ROOT / "TMessagesProj/src/main/res/values/strings.xml",
    "values_ru": ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml",
    "defines": ROOT / "TMessagesProj/jni/tgnet/Defines.h",
    "wrapper": ROOT / "TMessagesProj/jni/TgNetWrapper.cpp",
    "socket": ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp",
    "socket_h": ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.h",
    "collector": ROOT / "Tools/collect_mtproxy_logs.ps1",
    "scheduler": ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckScheduler.java",
}

LIVE_PHASES = [
    "admission_queue",
    "host_resolve_start",
    "connect_start",
    "socket_connect_start",
    "socket_connected",
    "client_hello_sent",
    "admission_hold_after_client_hello_failure",
    "server_hello_hmac_ok",
    "on_connected",
    "first_tls_app_sent",
    "first_tls_app_recv",
    "first_mtproxy_packet_sent",
    "first_mtproxy_packet_recv",
]


def text(name: str) -> str:
    return FILES[name].read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    diagnostics = text("diagnostics")
    combined = "\n".join(text(name) for name in FILES)

    for phase in LIVE_PHASES:
        require(phase in diagnostics, f"ProxyCheckDiagnostics must define live phase '{phase}'")
        require(phase in text("socket") or phase in text("connections_java"), f"live phase '{phase}' must be emitted or consumed")
    for phase in sorted(set(re.findall(r'publishProxyConnectionStage\("([^"]+)"\)', text("socket")))):
        require(phase in diagnostics, f"native published phase '{phase}' must be present in ProxyCheckDiagnostics for GUI rendering")

    require(
        "isLivePhase" in diagnostics
        and "hasFreshLivePhase" in diagnostics
        and "ProxyStatusHostResolve" in diagnostics
        and "ProxyStatusClientHelloSent" in diagnostics
        and "ProxyStatusServerHelloOk" in diagnostics,
        "ProxyCheckDiagnostics must map live native stages to user-facing status text",
    )
    header_idx = diagnostics.find("public static String headerStatusText")
    header_checking_idx = diagnostics.find("if (proxyInfo.checking)", header_idx)
    header_live_idx = diagnostics.find("if (hasFreshLivePhase(proxyInfo))", header_idx)
    require(
        header_idx >= 0
        and header_live_idx >= 0
        and header_checking_idx >= 0
        and header_live_idx < header_checking_idx,
        "proxy window header must show fresh live stages before generic checking text",
    )
    status_idx = diagnostics.find("public static String statusText")
    status_live_idx = diagnostics.find("if (hasFreshLivePhase(proxyInfo))", status_idx)
    status_failure_idx = diagnostics.find("if (hasFreshFailure(proxyInfo))", status_idx)
    status_connected_idx = diagnostics.find("currentConnectionState == ConnectionsManager.ConnectionStateConnected", status_idx)
    status_connecting_idx = diagnostics.find("currentConnectionState == ConnectionsManager.ConnectionStateConnectingToProxy", status_idx)
    inactive_checking_idx = diagnostics.find("if (proxyInfo.checking)", status_connecting_idx)
    inactive_failure_idx = diagnostics.find("if (hasFreshFailure(proxyInfo))", inactive_checking_idx)
    inactive_live_idx = diagnostics.find("hasFreshLivePhase(proxyInfo)", inactive_checking_idx)
    inactive_available_idx = diagnostics.find("if (proxyInfo.available && ProxyCheckScheduler.isFresh(proxyInfo))", inactive_checking_idx)
    header_failure_idx = diagnostics.find("if (hasFreshFailure(proxyInfo))", header_idx)
    header_connected_idx = diagnostics.find("currentConnectionState == ConnectionsManager.ConnectionStateConnected", header_idx)
    header_connecting_idx = diagnostics.find("currentConnectionState == ConnectionsManager.ConnectionStateConnectingToProxy", header_idx)
    require(
        status_idx >= 0
        and status_live_idx >= 0
        and status_failure_idx >= 0
        and status_failure_idx < status_live_idx
        and header_failure_idx < header_live_idx,
        "current proxy terminal failures must override live stages in row and header text",
    )
    require(
        status_failure_idx >= 0
        and status_connected_idx >= 0
        and status_failure_idx < status_connected_idx
        and status_live_idx >= 0
        and status_live_idx < status_connected_idx
        and header_failure_idx >= 0
        and header_connected_idx >= 0
        and header_failure_idx < header_connected_idx,
        "fresh concrete proxy phases must override generic Connected text, otherwise the GUI can show connected while the proxy data path is still being proven",
    )
    require(
        status_failure_idx >= 0
        and status_connecting_idx >= 0
        and status_failure_idx < status_connecting_idx
        and header_failure_idx >= 0
        and header_connecting_idx >= 0
        and header_failure_idx < header_connecting_idx,
        "fresh terminal failures must render before generic ConnectionStateConnectingToProxy text, otherwise the UI shows red 'waiting TCP'",
    )
    require(
        inactive_checking_idx >= 0
        and inactive_failure_idx >= 0
        and inactive_live_idx >= 0
        and inactive_available_idx >= 0
        and inactive_failure_idx < inactive_available_idx
        and inactive_live_idx < inactive_available_idx,
        "fresh concrete proxy phases must override stale Available text for non-current proxy rows",
    )
    color_idx = diagnostics.find("public static int statusColorKey")
    color_failure_idx = diagnostics.find("if (hasFreshFailure(proxyInfo))", color_idx)
    color_live_idx = diagnostics.find("hasFreshLivePhase(proxyInfo)", color_idx)
    color_connected_idx = diagnostics.find("currentConnectionState == ConnectionsManager.ConnectionStateConnected", color_idx)
    color_inactive_start_idx = diagnostics.find("if (proxyInfo == null)", color_idx)
    color_inactive_failure_idx = diagnostics.find("if (hasFreshFailure(proxyInfo))", color_inactive_start_idx)
    color_inactive_live_idx = diagnostics.find("hasFreshLivePhase(proxyInfo)", color_inactive_start_idx)
    color_inactive_available_idx = diagnostics.find("if (proxyInfo.available && ProxyCheckScheduler.isFresh(proxyInfo))", color_inactive_start_idx)
    require(
        color_idx >= 0
        and color_failure_idx >= 0
        and color_live_idx >= 0
        and color_connected_idx >= 0
        and color_failure_idx < color_connected_idx,
        "current proxy terminal failures must color the row as failure before generic connected blue",
    )
    require(
        color_live_idx >= 0
        and color_live_idx < color_connected_idx
        and "isProxyUsableSuccessPhase(proxyInfo.lastCheckDiagnostic)" in diagnostics[color_live_idx:color_connected_idx],
        "fresh live proxy phases must choose row color before generic connected blue",
    )
    require(
        color_inactive_start_idx >= 0
        and color_inactive_failure_idx >= 0
        and color_inactive_live_idx >= 0
        and color_inactive_available_idx >= 0
        and color_inactive_failure_idx < color_inactive_available_idx
        and color_inactive_live_idx < color_inactive_available_idx,
        "fresh concrete proxy phases must choose row color before stale Available green for non-current proxy rows",
    )
    has_failure_idx = diagnostics.find("public static boolean hasFreshFailure")
    has_failure_body = diagnostics[has_failure_idx:diagnostics.find("public static String statusText", has_failure_idx)]
    require(
        "lastCheckDiagnosticTime" in has_failure_body
        and "isFailure(proxyInfo.lastCheckDiagnostic)" in has_failure_body,
        "fresh failure phases must use diagnostic timestamp, not only proxy-check availability timestamp",
    )
    require(
        "ProxyCheckDiagnostics.isFailure(normalizedDiagnostic)" in text("connections_java")
        and "!ProxyCheckDiagnostics.UNKNOWN_FAIL.equals(normalizedDiagnostic)" in text("connections_java"),
        "current proxy stage callback must accept concrete failure phases while rejecting unknown_fail noise",
    )
    require(
        "shouldKeepFreshFailure" in diagnostics
        and "isEarlyRetryPhase" in diagnostics
        and "ProxyCheckDiagnostics.shouldKeepFreshFailure(currentProxy, normalizedDiagnostic)" in text("connections_java"),
        "fresh terminal failures must not be overwritten by early retry phases such as admission_queue or host_resolve_start",
    )
    require(
        "isProxyUsableSuccessPhase" in diagnostics
        and "FIRST_TLS_APP_RECV" in diagnostics
        and "FIRST_MTPROXY_PACKET_RECV" in diagnostics,
        "ProxyCheckDiagnostics must define concrete data-path success phases that prove a proxy is usable again",
    )
    usable_method = diagnostics[diagnostics.find("public static boolean isProxyUsableSuccessPhase"):]
    usable_method = usable_method[:usable_method.find("\n    public static", 1)]
    require(
        "SERVER_HELLO_HMAC_OK" not in usable_method
        and "FIRST_TLS_APP_RECV" in usable_method
        and "FIRST_MTPROXY_PACKET_RECV" in usable_method,
        "server_hello_hmac_ok must remain a handshake live phase, not a data-path usable success",
    )
    require(
        "ProxyCheckDiagnostics.isProxyUsableSuccessPhase(normalizedDiagnostic)" in text("connections_java")
        and "ProxyCheckScheduler.markConnectionUsable(currentProxy, normalizedDiagnostic)" in text("connections_java"),
        "concrete success phases from native must clear stale Java endpoint backoff and fresh terminal failures",
    )
    require(
        "markCheckingIfNoFreshConcretePhase(proxyInfo);" in text("scheduler")
        and "markCheckingIfNoFreshConcretePhase(listener.proxyInfo);" in text("scheduler")
        and "hasFreshConcreteProxyPhase(proxyInfo)" in text("scheduler"),
        "background proxy-check must not overwrite a fresh live/failure phase of the selected proxy with generic checking",
    )
    scheduler_text = text("scheduler")
    cooldown_idx = scheduler_text.find("private static void markEndpointCooldown")
    cooldown_body = scheduler_text[cooldown_idx:scheduler_text.find("private static EndpointState endpointStateForKey", cooldown_idx)]
    require(
        cooldown_idx >= 0
        and "hasFreshConcreteProxyPhase(proxyInfo)" in cooldown_body
        and "ProxyCheckDiagnostics.ENDPOINT_COOLDOWN" in cooldown_body,
        "endpoint cooldown must not overwrite a fresher concrete proxy phase",
    )
    require(
        "boolean selectedAccountStage = currentAccount == UserConfig.selectedAccount;" in text("connections_java")
        and "if (selectedAccountStage && currentProxy != null && concreteDiagnostic && currentProxyMatchesStage)" in text("connections_java"),
        "native proxy live stages from background accounts must not overwrite the shared visible proxy diagnostic",
    )
    require(
        "final String endpointKey" in text("connections_java")
        and "boolean currentProxyMatchesStage = ProxyCheckScheduler.matchesEndpointStageKey(currentProxy, endpointKey);" in text("connections_java")
        and "if (selectedAccountStage && currentProxy != null && concreteDiagnostic && currentProxyMatchesStage)" in text("connections_java"),
        "native proxy live stages from stale endpoint/secret keys must not overwrite the currently selected proxy diagnostic",
    )
    require(
        "proxyConnectionStageChanged" in text("notification_center")
        and "onProxyConnectionStageChanged" in text("connections_java")
        and "NotificationCenter.proxyConnectionStageChanged" in text("connections_java"),
        "Java must expose a NotificationCenter event for current proxy live stages",
    )
    require(
        "NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.proxyConnectionStageChanged" in text("connections_java"),
        "proxy live stages must also be posted globally because SharedConfig.currentProxy is global across accounts",
    )
    require(
        "onProxyConnectionStageChanged" in text("defines")
        and "jclass_ConnectionsManager_onProxyConnectionStageChanged" in text("wrapper")
        and 'GetStaticMethodID(jclass_ConnectionsManager, "onProxyConnectionStageChanged", "(ILjava/lang/String;Ljava/lang/String;)V")' in text("wrapper"),
        "JNI bridge must forward native proxy live stages with an endpoint key to ConnectionsManager",
    )
    require(
        "publishProxyConnectionStage" in text("socket_h")
        and "publishProxyConnectionStage(" in text("socket")
        and "currentMtProxyNetworkEndpointKey" in text("socket")
        and "currentMtProxyEndpointKey" in text("socket")
        and "isCurrentMtProxyConnection()" in text("socket_h")
        and "markMtProxyFirstPlainDataSent" in text("socket_h")
        and "markMtProxyFirstPlainDataReceived" in text("socket_h")
        and "void ConnectionSocket::markMtProxyFirstPlainDataSent" in text("socket")
        and "void ConnectionSocket::markMtProxyFirstPlainDataReceived" in text("socket")
        and "!isCurrentMtProxyConnection()" in text("socket")
        and "!overrideProxyAddress.empty()" in text("socket")
        and 'publishProxyConnectionStage("host_resolve_start")' in text("socket")
        and 'proxyCheckDiagnostic = "host_resolve_failed"' in text("socket")
        and 'publishProxyConnectionStage("client_hello_sent")' in text("socket")
        and 'publishProxyConnectionStage("admission_hold_after_client_hello_failure")' in text("socket")
        and 'publishProxyConnectionStage("server_hello_hmac_ok")' in text("socket")
        and 'publishProxyConnectionStage("first_tls_app_recv")' in text("socket"),
        "ConnectionSocket must publish live stages for plain dd/legacy MTProxy too, not only FakeTLS ee",
    )
    require(
        "public static boolean matchesEndpointStageKey" in text("scheduler")
        and "endpointStageKeyForLiveStage" in text("scheduler")
        and "decodedSecretForLiveStage" in text("scheduler")
        and "if (args == null || args.length < 2 || !(args[1] instanceof String))" in text("proxy_list")
        and "ProxyCheckScheduler.matchesEndpointStageKey(selectedProxy, endpointKey)" in text("proxy_list")
        and "ProxyCheckScheduler.matchesEndpointStageKey(SharedConfig.currentProxy, (String) args[1])" in text("rotation")
        and "currentProxyMatchesStage" in text("connections_java"),
        "UI and Java lifecycle code must ignore proxy live stages from stale endpoint/secret keys",
    )
    require(
        "postNotificationName(NotificationCenter.proxyConnectionStageChanged, normalizedDiagnostic, endpointKey)" in text("connections_java"),
        "proxy live stage notifications must carry endpoint key so UI and rotation can ignore stale endpoint events",
    )
    require(
        "publishProxyConnectionStage(proxyCheckDiagnostic.c_str())" in text("socket"),
        "ConnectionSocket must publish a concrete terminal diagnostic on failed current-proxy disconnects",
    )
    require(
        "NotificationCenter.getInstance(currentAccount).addObserver(this, NotificationCenter.proxyConnectionStageChanged)" in text("proxy_list")
        and "NotificationCenter.getInstance(currentAccount).removeObserver(this, NotificationCenter.proxyConnectionStageChanged)" in text("proxy_list")
        and "NotificationCenter.getGlobalInstance().addObserver(this, NotificationCenter.proxyConnectionStageChanged)" not in text("proxy_list")
        and "NotificationCenter.getGlobalInstance().removeObserver(this, NotificationCenter.proxyConnectionStageChanged)" not in text("proxy_list")
        and "id == NotificationCenter.proxyConnectionStageChanged" in text("proxy_list"),
        "Proxy list must refresh header and current row only on current-account live proxy stage updates",
    )
    require(
        "NotificationCenter.getInstance(currentAccount).addObserver(this, NotificationCenter.proxyConnectionStageChanged)" in text("launch")
        and "NotificationCenter.getInstance(currentAccount).removeObserver(this, NotificationCenter.proxyConnectionStageChanged)" in text("launch")
        and "id == NotificationCenter.proxyConnectionStageChanged" in text("launch")
        and "updateCurrentConnectionState(account)" in text("launch"),
        "main screen proxy title must refresh on live proxy stages even when the generic connection state does not change",
    )
    require(
        ".add(NotificationCenter.proxyConnectionStageChanged)" in text("dialogs")
        and "id == NotificationCenter.proxyConnectionStageChanged" in text("dialogs")
        and "ProxyCheckDiagnostics.headerStatusText" in text("dialogs")
        and "proxyMenuSubItem.setSubtext(proxyStatusText)" in text("dialogs"),
        "dialogs proxy menu must show the same concrete proxy phase as the proxy settings UI",
    )
    require(
        "NotificationCenter.getInstance(currentAccount).addObserver(this, NotificationCenter.proxyConnectionStageChanged)" in text("chat_avatar")
        and "NotificationCenter.getInstance(currentAccount).removeObserver(this, NotificationCenter.proxyConnectionStageChanged)" in text("chat_avatar")
        and "id == NotificationCenter.proxyConnectionStageChanged" in text("chat_avatar")
        and "ProxyCheckDiagnostics.headerStatusText" in text("chat_avatar")
        and "title = getString(R.string.ConnectingToProxy)" not in text("chat_avatar"),
        "chat header must show concrete proxy stages instead of generic ConnectingToProxy",
    )
    require(
        "ProxyCheckDiagnostics.headerStatusText" in text("profile")
        and "LocaleController.getString(R.string.ConnectingToProxy)" not in text("profile"),
        "profile header must use the shared concrete proxy status text instead of generic ConnectingToProxy",
    )
    require(
        "proxy_connection_stage" in text("collector"),
        "live Java proxy stages must be collected into mtproxy marker logs",
    )
    for name in ("values", "values_ru"):
        source = text(name)
        for string_name in (
            "ProxyStatusAdmissionQueue",
            "ProxyStatusHostResolve",
            "ProxyStatusHostResolveFailed",
            "ProxyStatusTcpConnecting",
            "ProxyStatusTcpConnected",
            "ProxyStatusClientHelloSent",
            "ProxyStatusAdmissionHoldAfterClientHelloFailure",
            "ProxyStatusServerHelloOk",
            "ProxyStatusMtprotoStarting",
            "ProxyStatusFirstDataSent",
            "ProxyStatusFirstDataReceived",
            "ProxyStatusFirstMtproxyPacketSent",
            "ProxyStatusFirstMtproxyPacketReceived",
        ):
            require(f'name="{string_name}"' in source, f"{name} must define {string_name}")

    print("Proxy live connection stages guard passed.")


if __name__ == "__main__":
    main()
