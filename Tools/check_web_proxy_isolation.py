#!/usr/bin/env python3
"""WEB proxy must reach tgnet as a plain MTProxy on the local browser bridge.

The WebView bridge carries an ordinary obfuscated2 stream keyed by the WEB
proxy's own plain secret. None of ZaStoGram's MTProxy stealth layers (FakeTLS
profiles, ClientHello fragmentation, connection pattern, record sizing, timing,
startup cover, soft mux) may be put on top of it, and the stream must never be
keyed by the DC secret or an empty secret.
"""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "TMessagesProj/src/main/java/org/telegram"
CONNECTIONS = JAVA / "tgnet/ConnectionsManager.java"
PROXY_LIST = JAVA / "ui/ProxyListActivity.java"
LINK_HELPER = JAVA / "messenger/ProxyLinkHelper.java"
SHARED_CONFIG = JAVA / "messenger/SharedConfig.java"
CONNECTION_CPP = ROOT / "TMessagesProj/jni/tgnet/Connection.cpp"
CONNECTION_SOCKET_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"


def method_body(text: str, signature: str) -> str:
    start = text.find(signature)
    if start < 0:
        return ""
    brace = text.find("{", start)
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[brace:index + 1]
    return ""


def main() -> int:
    failures: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    connections = CONNECTIONS.read_text(encoding="utf-8")
    init = method_body(connections, "public void init(int version")
    set_proxy = method_body(connections, "public static void setProxySettings(boolean enabled, ProxySettings settings, ProxyConnectionEvent.Origin origin)")
    check_web = method_body(connections, "private void checkWebProxyInternal(ProxySettings settings, int port, RequestTimeDelegate requestTimeDelegate)")
    soft_mux = method_body(connections, "private static boolean isMtProxySoftMuxEnabled()")

    require(
        'native_setProxySettings(currentAccount, "127.0.0.1", localPort != 0 ? localPort : 9, "", "", proxySecret, MtProxyOptions.disabled(),' in init,
        "startup restore must dial the WEB bridge with the proxy secret and all MTProxy options disabled",
    )
    require(
        "WebProxyTransport.start(address, secret)" in set_proxy
        and "webProxy = true;" in set_proxy
        and "hasSelectedProxy && !webProxy ? MtProxyOptions.resolve(address, port, secret) : MtProxyOptions.disabled()" in set_proxy,
        "selecting a WEB proxy must start the bridge and pass MtProxyOptions.disabled()",
    )
    require(
        'native_checkProxy(currentAccount, "127.0.0.1", port, "", "", settings.getSecret(), MtProxyOptions.disabled(), requestTimeDelegate)' in check_web,
        "WEB proxy checks must use the proxy secret and disabled MTProxy options",
    )
    require(
        "ProxySettings.Type.MTPROTO" in soft_mux,
        "soft mux must stay an MTProto-proxy policy and skip WEB proxies",
    )
    require(
        "tlsProfileRow = rowCount++" in PROXY_LIST.read_text(encoding="utf-8")
        and "SharedConfig.currentProxy.settings.getType() == ProxySettings.Type.MTPROTO" in PROXY_LIST.read_text(encoding="utf-8"),
        "MTProxy stealth settings rows must be hidden for WEB proxies",
    )

    link_helper = LINK_HELPER.read_text(encoding="utf-8")
    require('"tg://webproxy?"' in link_helper and '"t.me/webproxy?"' in link_helper,
            "tg://webproxy and t.me/webproxy links must be recognised")
    require("WebProxyTransport.isValidSecret(secret)" in link_helper,
            "WEB links must carry a valid plain MTProxy secret")

    shared_config = SHARED_CONFIG.read_text(encoding="utf-8")
    require("PROXY_SCHEMA_V5" in shared_config and "version >= PROXY_SCHEMA_V5" in shared_config,
            "the proxy type must be stored in its own schema version, not in ZaStoGram's WSS-era V3")

    connection_cpp = CONNECTION_CPP.read_text(encoding="utf-8")
    require(
        "proxyAddress.empty() && !ConnectionsManager::getInstance(currentDatacenter->instanceNum).proxySecret.empty()) {\n            useSecret = 1;" in connection_cpp,
        "a proxied connection must be keyed by the proxy secret, not the DC secret",
    )
    socket_cpp = CONNECTION_SOCKET_CPP.read_text(encoding="utf-8")
    require(
        "bool shouldUseWss = overrideProxyAddress.empty()\n            && manager.wssEnabled\n            && proxyAddress->empty();" in socket_cpp,
        "WSS substitution must never apply while a proxy (including the WEB bridge) is selected",
    )

    if failures:
        print("WEB proxy isolation guard failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("WEB proxy isolation guard passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
