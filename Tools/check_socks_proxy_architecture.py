#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SHARED_CONFIG = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/SharedConfig.java"
CONNECTIONS = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
PROXY_LIST = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxyListActivity.java"
PROXY_SETTINGS = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxySettingsActivity.java"
WSS_CPP = ROOT / "TMessagesProj/jni/tgnet/wss/WssSocket.cpp"
WSS_H = ROOT / "TMessagesProj/jni/tgnet/wss/WssSocket.h"
MINI_BRIDGE = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/WssMiniAppProxyBridge.java"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    shared_config = text(SHARED_CONFIG)
    connections = text(CONNECTIONS)
    proxy_list = text(PROXY_LIST)
    proxy_settings = text(PROXY_SETTINGS)
    wss_cpp = text(WSS_CPP)
    wss_h = text(WSS_H)

    require("currentProxy" in shared_config and "proxy_enabled" in shared_config,
            "legacy SOCKS5/MTProxy selection must remain available")
    require("currentWssSocksProxy" not in shared_config + connections + proxy_list + proxy_settings,
            "WSS must not have a second proxy selection")
    require("saveWssSocksProxy" not in shared_config + proxy_settings,
            "WSS must not persist a SOCKS upstream")
    require("TYPE_SOCKS5" in proxy_settings and "TYPE_MTPROTO" in proxy_settings,
            "proxy editor must keep only normal SOCKS5 and MTProxy types")
    require("TYPE_WSS" not in proxy_settings,
            "WSS must not masquerade as a proxy type")
    require("SharedConfig.currentProxy == info" in proxy_list,
            "normal proxy rows must still select currentProxy")
    require("SharedConfig.setWssTransportEnabled(false)" in proxy_list
            and "hasSelectedProxy && SharedConfig.wssTransportEnabled" in connections,
            "activating a normal proxy must turn the WSS checkbox off")
    require("buildSocks5" not in wss_cpp and "upstreamSocks" not in wss_h,
            "the WSS socket must contain no SOCKS proxy handshake")
    require(not MINI_BRIDGE.exists(),
            "the obsolete local proxy bridge must be removed")

    print("SOCKS/WSS separation guard passed.")


if __name__ == "__main__":
    main()
