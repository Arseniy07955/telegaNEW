#!/usr/bin/env python3
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
APPLICATION_LOADER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ApplicationLoader.java"
SHARED_CONFIG = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/SharedConfig.java"
CONNECTIONS_JAVA = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
CHAT_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ChatActivity.java"
CONNECTION_SOCKET = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
CONNECTIONS_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionsManager.cpp"
TRANSPORT_SOCKET = ROOT / "TMessagesProj/jni/tgnet/transport/TransportSocket.h"
WSS_SOCKET_H = ROOT / "TMessagesProj/jni/tgnet/wss/WssSocket.h"
WSS_SOCKET_CPP = ROOT / "TMessagesProj/jni/tgnet/wss/WssSocket.cpp"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def method_body(source: str, signature: str, next_signature: str) -> str:
    start = source.index(signature)
    end = source.index(next_signature, start)
    return source[start:end]


def main() -> None:
    application_loader = text(APPLICATION_LOADER)
    shared_config = text(SHARED_CONFIG)
    connections_java = text(CONNECTIONS_JAVA)
    chat_activity = text(CHAT_ACTIVITY)
    connection_socket = text(CONNECTION_SOCKET)
    connections_cpp = text(CONNECTIONS_CPP)
    transport_socket = text(TRANSPORT_SOCKET)
    wss_socket_h = text(WSS_SOCKET_H)
    wss_socket_cpp = text(WSS_SOCKET_CPP)

    require("volatile boolean wssTransportEnabled" in shared_config,
            "the persisted WSS preference must be safely visible to network callbacks")
    require("public static boolean isVpnActive()" in application_loader
            and "NetworkCapabilities.TRANSPORT_VPN" in application_loader
            and "ConnectivityManager.TYPE_VPN" in application_loader,
            "ApplicationLoader must detect the active Android VPN from capabilities with a legacy fallback")
    for callback in ("onAvailable", "onCapabilitiesChanged", "onLinkPropertiesChanged", "onLost"):
        require(callback in application_loader, f"network snapshot callback must handle {callback}")
    require("NETWORK_SNAPSHOT_DEBOUNCE_MS" in application_loader
            and "removeCallbacks(publishNetworkSnapshotRunnable)" in application_loader
            and "snapshot.sameAs(lastPublishedNetworkSnapshot)" in application_loader,
            "network changes must be coalesced and deduplicated through one current-state snapshot")

    effective = method_body(
        connections_java,
        "public static boolean isWssTransportActive()",
        "public static boolean supportsCdnFileRedirects()")
    require("SharedConfig.wssTransportEnabled" in effective
            and "!ApplicationLoader.isVpnActive()" in effective,
            "effective WSS must keep the user preference but bypass WSS on the active VPN route")
    setter = method_body(
        connections_java,
        "public static void setWssTransportEnabled()",
        "public static boolean isWssTransportActive()")
    require("applyWssTransport(SharedConfig.isProxyEnabled())" in setter
            and "SharedConfig.wssTransportEnabled" in setter
            and "!proxyActive" in setter
            and "!ApplicationLoader.isVpnActive()" in setter
            and "native_setWssTransportEnabled(a, enabled)" in setter,
            "native WSS propagation must use the effective transport state")
    check_connection = method_body(
        connections_java,
        "public void checkConnection()",
        "public void setPushConnectionEnabled")
    require("native_setWssTransportEnabled(currentAccount, isWssTransportActive())" in check_connection,
            "every published network snapshot must reapply effective WSS before network availability")
    cdn = method_body(
        connections_java,
        "public static boolean supportsCdnFileRedirects()",
        "static int resolveMtProxyClientHelloFragmentationMode()")
    require("return !isWssTransportActive();" in cdn,
            "CDN redirects must be the inverse of the effective WSS route")
    for diagnostic in ("vpn_active", "wss_configured", "wss_active", "cdn_redirects_supported"):
        require(f'"{diagnostic}"' in chat_activity,
                f"message diagnostics must expose {diagnostic}")

    require("virtual bool canWriteApplicationData() const = 0" in transport_socket
            and "bool canWriteApplicationData() const override" in wss_socket_h
            and "state == State::Ready && ioWait != IoWait::Read" in wss_socket_cpp,
            "WSS must expose whether queued app data can currently drive EPOLLOUT")
    adjust_write = method_body(connection_socket, "void ConnectionSocket::adjustWriteOp()", "void ConnectionSocket::setTimeout")
    require("if (isCurrentTransportWss())" in adjust_write
            and "currentWssTransport->wantsWrite()" in adjust_write
            and "currentWssTransport->canWriteApplicationData()" in adjust_write,
            "WSS EPOLLOUT interest must follow the TLS wait direction")
    require(re.search(
        r"dispatchWssPayloads\([^)]*\).*?payload\.empty\(\).*?lastEventTime\s*=",
        connection_socket,
        re.DOTALL) is not None,
        "every non-empty WSS payload must refresh the connection activity timeout")

    require(connections_cpp.count(
        "auto &map = request->isCancelRequest() ? downloadCancelRunningRequestCount : downloadRunningRequestCount;") == 2,
        "both active and queued download counters must update their selected map by reference")
    require("map[datacenterId] = currentCount + 1;" in connections_cpp,
            "queued normal/cancel admission must increment the selected counter")
    require("downloadRunningRequestCount[datacenterId] = currentCount + 1;" not in connections_cpp,
            "cancel requests must not be written into the normal download counter")

    print("Transport throughput contract passed.")


if __name__ == "__main__":
    main()
