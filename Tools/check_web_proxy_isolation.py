#!/usr/bin/env python3
"""WEB proxy must reach tgnet as a plain MTProxy on the local browser bridge.

The WebView bridge carries an ordinary obfuscated2 stream keyed by the WEB
proxy's own plain secret. None of ZaStoGram's MTProxy stealth layers (FakeTLS
profiles, ClientHello fragmentation, connection pattern, record sizing, timing,
startup cover, soft mux) may be put on top of it, and the stream must never be
keyed by the DC secret or an empty secret.

The bridge is also exempt from MTProxy dial pacing. Java marks it with
MtProxyOptions.webBridge(); native code then skips the per-endpoint TCP
connect gate, the endpoint cooldown, the handshake admission, the DNS
coalescing and the Connection reconnect backoff for it. Those exist to spare a
remote relay under DPI; on loopback they only slow connection setup. Real
MTProxy connections keep every one of those gates.
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
MT_PROXY_OPTIONS_JAVA = JAVA / "tgnet/MtProxyOptions.java"
MT_PROXY_OPTIONS_H = ROOT / "TMessagesProj/jni/mtproxy/MtProxyOptions.h"
TGNET_WRAPPER_CPP = ROOT / "TMessagesProj/jni/TgNetWrapper.cpp"


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


def check_web_bridge_bypasses_pacing(require) -> None:
    """The WEB loopback bridge must bypass MTProxy pacing; MTProxy keeps it."""
    options_java = MT_PROXY_OPTIONS_JAVA.read_text(encoding="utf-8")
    web_bridge_java = method_body(options_java, "public static MtProxyOptions webBridge()")
    disabled_java = method_body(options_java, "public static MtProxyOptions disabled()")
    resolve_java = method_body(options_java, "public static MtProxyOptions resolve(String address, int port, String secret)")
    require("public final boolean webBridge;" in options_java,
            "Java MtProxyOptions must carry an explicit webBridge marker")
    require("MT_PROXY_STARTUP_COVER_OFF,\n                true);" in web_bridge_java,
            "MtProxyOptions.webBridge() must be the disabled option set with webBridge=true")
    require("true" not in disabled_java and "true" not in resolve_java,
            "MtProxyOptions.disabled() and resolve() must never mark a connection as the WEB bridge")

    options_h = MT_PROXY_OPTIONS_H.read_text(encoding="utf-8")
    require("bool webBridge = false;" in options_h
            and "&& webBridge == other.webBridge;" in options_h
            and "normalized.webBridge = options.webBridge;" in options_h,
            "native MtProxyOptions must default webBridge to false, compare it and keep it through normalization")

    wrapper = TGNET_WRAPPER_CPP.read_text(encoding="utf-8")
    require('env->GetFieldID(jclass_MtProxyOptions, "webBridge", "Z")' in wrapper
            and "nativeOptions.webBridge = env->GetBooleanField(options, jclass_MtProxyOptions_webBridge) == JNI_TRUE;" in wrapper,
            "the JNI bridge must read MtProxyOptions.webBridge into native options")

    socket_cpp = CONNECTION_SOCKET_CPP.read_text(encoding="utf-8")
    open_connection = method_body(socket_cpp, "void ConnectionSocket::openConnection(std::string address")
    require("stateMachine.endpointGate.webProxyBridge = proxyOptions.webBridge && !proxyAddress->empty() && !proxySecret->empty();" in open_connection,
            "openConnection must take the WEB bridge marker from the effective proxy options")
    gates = {
        "bool ConnectionSocket::scheduleMtProxyEndpointTcpConnectGateIfNeeded(bool ipv6)": "MtProxyEndpointPolicy::beginTcpConnect(",
        "bool ConnectionSocket::scheduleMtProxyEndpointCircuitBreakerIfNeeded(bool ipv6)": "MtProxyEndpointPolicy::readCooldown(",
        "bool ConnectionSocket::scheduleProxyHandshakeAdmissionIfNeeded(bool ipv6, int32_t timerMode)": "scheduleProxyHandshakeAdmissionTimer(",
        "bool ConnectionSocket::scheduleMtProxyDnsCoalesceIfNeeded(bool ipv6)": "MtProxyEndpointPolicy::beginDnsCoalesce(",
    }
    for signature, mtproxy_gate in gates.items():
        body = method_body(socket_cpp, signature)
        bypass = body.find("isCurrentWebProxyBridge()")
        gate = body.find(mtproxy_gate)
        require(bypass >= 0 and gate >= 0 and bypass < gate,
                f"{signature} must return early for the WEB bridge before its MTProxy gate {mtproxy_gate}")
    tcp_gate = method_body(socket_cpp, "bool ConnectionSocket::scheduleMtProxyEndpointTcpConnectGateIfNeeded(bool ipv6)")
    require("if (isCurrentWebProxyBridge()) {" in tcp_gate and "return false;" in tcp_gate.split("if (isCurrentWebProxyBridge()) {", 1)[-1].split("}", 1)[0],
            "the per-endpoint TCP connect queue must never hold a WEB bridge connect")
    stage = method_body(socket_cpp, "void ConnectionSocket::publishProxyConnectionStage(const char *diagnostic)")
    require("!isCurrentWebProxyBridge() && MtProxyEndpointPolicy::failureNeedsCooldown(diagnostic)" in stage,
            "a WEB bridge failure must not raise an endpoint-cooldown reconnect hold")
    internal = method_body(socket_cpp, "void ConnectionSocket::openConnectionInternal(bool ipv6)")
    require("isCurrentMtProxyConnection() && scheduleMtProxyEndpointCircuitBreakerIfNeeded(ipv6)" in internal
            and "isCurrentMtProxyConnection() && scheduleMtProxyEndpointTcpConnectGateIfNeeded(ipv6)" in internal,
            "real MTProxy connections must still pass the endpoint cooldown and TCP connect gate")

    connection_cpp = CONNECTION_CPP.read_text(encoding="utf-8")
    pacing = method_body(connection_cpp, "bool Connection::isMtProxyReconnectPacingActive() const")
    require("return !overrideMtProxyOptions.webBridge;" in pacing
            and "proxyMtProxyOptions.webBridge;" in pacing
            and "if (!isMtProxyRouteActive()) {" in pacing,
            "reconnect pacing must be active for MTProxy routes and off for the WEB bridge")
    connect = method_body(connection_cpp, "void Connection::connect()")
    require("if (mtProxyReconnectPacing && connectionType != ConnectionTypeProxy && mtProxyReconnectHoldUntil > now) {" in connect,
            "Connection::connect must honour the MTProxy reconnect hold only while reconnect pacing is active")
    disconnected = method_body(connection_cpp, "void Connection::onDisconnectedInternal(int32_t reason, int32_t error)")
    require("if (mtProxyReconnectPacing && connectionState == TcpConnectionStageIdle && connectionType != ConnectionTypeProxy && !isProxyCloseDiagnosticSuppressed()" in disconnected,
            "the MTProxy reconnect backoff must be skipped for the WEB bridge")
    require("mtProxyRouteActive && connectionState == TcpConnectionStageIdle" not in disconnected,
            "no reconnect backoff branch may key on the bare MTProxy route (it would include the WEB bridge)")


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
        'native_setProxySettings(currentAccount, "127.0.0.1", localPort != 0 ? localPort : 9, "", "", proxySecret, MtProxyOptions.webBridge(),' in init,
        "startup restore must dial the WEB bridge with the proxy secret and the webBridge() options",
    )
    require(
        "WebProxyTransport.start(address, secret)" in set_proxy
        and "webProxy = true;" in set_proxy
        and "!hasSelectedProxy ? MtProxyOptions.disabled() : webProxy ? MtProxyOptions.webBridge() : MtProxyOptions.resolve(address, port, secret)" in set_proxy,
        "selecting a WEB proxy must start the bridge and pass MtProxyOptions.webBridge(); other proxies keep resolve()",
    )
    require(
        'native_checkProxy(currentAccount, "127.0.0.1", port, "", "", settings.getSecret(), MtProxyOptions.webBridge(), requestTimeDelegate)' in check_web,
        "WEB proxy checks must use the proxy secret and the webBridge() options",
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

    check_web_bridge_bypasses_pacing(require)

    if failures:
        print("WEB proxy isolation guard failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("WEB proxy isolation guard passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
