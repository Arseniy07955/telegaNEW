#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CONNECTIONS_JAVA = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
PROXY_LIST = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxyListActivity.java"
SHARED_CONFIG = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/SharedConfig.java"
SOCKET_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
SOCKET_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.h"
CONNECTION_CPP = ROOT / "TMessagesProj/jni/tgnet/Connection.cpp"
CONNECTION_H = ROOT / "TMessagesProj/jni/tgnet/Connection.h"
MANAGER_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionsManager.cpp"
MANAGER_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionsManager.h"
PROXY_CHECK = ROOT / "TMessagesProj/jni/tgnet/ProxyCheckInfo.h"
STRINGS = ROOT / "TMessagesProj/src/main/res/values/strings.xml"
STRINGS_RU = ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    connections = text(CONNECTIONS_JAVA)
    proxy_list = text(PROXY_LIST)
    shared_config = text(SHARED_CONFIG)
    socket_cpp = text(SOCKET_CPP)
    socket_h = text(SOCKET_H)
    connection_cpp = text(CONNECTION_CPP)
    connection_h = text(CONNECTION_H)
    manager_cpp = text(MANAGER_CPP)
    manager_h = text(MANAGER_H)
    proxy_check = text(PROXY_CHECK)

    for name in ("OFF", "SOFT", "BROWSER", "QUIET", "STRICT"):
        require(
            f"MT_PROXY_CONNECTION_PATTERN_{name}" in connections
            and f"MT_PROXY_CONNECTION_PATTERN_{name}" in socket_cpp,
            f"Java and native must define MTProxy connection pattern {name}",
        )

    require(
        "public static int mtProxyConnectionPatternMode" in shared_config
        and 'getInt("mtProxyConnectionPatternMode"' in shared_config
        and 'getBoolean("mtProxyHandshakeAdmission", false)' in shared_config
        and 'putInt("mtProxyConnectionPatternMode", mtProxyConnectionPatternMode)' in shared_config,
        "SharedConfig must persist integer connection-pattern mode and migrate the old admission boolean",
    )
    require(
        "private static int resolveMtProxyConnectionPatternMode()" in connections
        and "SharedConfig.mtProxyConnectionPatternMode" in connections
        and "mtProxyConnectionPatternMode" in connections
        and "native_setProxySettings(currentAccount, proxyAddress, proxyPort, proxyUsername, proxyPassword, proxySecret, mtProxyTlsProfile, mtProxyClientHelloFragmentation, mtProxyConnectionPatternMode, mtProxyRecordSizingMode, mtProxyTimingMode, mtProxyStartupCoverMode)" in connections,
        "Java must pass the selected connection-pattern mode into native proxy settings",
    )
    require(
        "mtProxyConnectionPatternRow" in proxy_list
        and "MT_PROXY_CONNECTION_PATTERN_OPTIONS" in proxy_list
        and "getMtProxyConnectionPatternLabels()" in proxy_list
        and "MtProxyConnectionPatternBrowser" in proxy_list
        and "SharedConfig.mtProxyConnectionPatternMode" in proxy_list
        and "VIEW_TYPE_SLIDE_CHOOSER" in proxy_list,
        "proxy settings UI must expose connection pattern as a SlideChooseView, not a checkbox",
    )
    require(
        "SharedConfig.mtProxyHandshakeAdmission = !" not in proxy_list
        and "mtProxyHandshakeAdmissionRow" not in proxy_list,
        "old admission checkbox row must be replaced by the connection-pattern chooser",
    )
    require(
        "int32_t proxyConnectionPatternMode = 0" in manager_h
        and "normalizeMtProxyConnectionPatternMode" in manager_cpp
        and "connectionPatternChanged" in manager_cpp
        and "proxyConnectionPatternMode = normalizeMtProxyConnectionPatternMode" in manager_cpp,
        "native ConnectionsManager must store connection-pattern mode and reconnect when it changes",
    )
    require(
        "int32_t mtProxyConnectionPatternMode" in proxy_check
        and "overrideProxyConnectionPatternMode" in socket_h
        and "currentConnectionPatternMode" in socket_h
        and "setOverrideProxy(std::string address, uint16_t port, std::string username, std::string password, std::string secret, int32_t mtProxyTlsProfile, int32_t mtProxyClientHelloFragmentation, int32_t mtProxyConnectionPatternMode, int32_t mtProxyRecordSizingMode, int32_t mtProxyTimingMode, int32_t mtProxyStartupCoverMode)" in socket_h,
        "proxy-check override sockets must carry the same connection-pattern mode as real sockets",
    )
    require(
        "mtProxyConnectionPatternModeName" in socket_cpp
        and "mtProxyConnectionPatternUsesAdmission" in socket_cpp
        and "mtProxyConnectionPatternUsesCooldown" in socket_cpp
        and "mtProxyHandshakeGrantDelay(int32_t mode)" in socket_cpp
        and "mtProxyHandshakeSpacingDelay" in socket_cpp
        and "lastGrantTime" in socket_cpp
        and "mtProxyHandshakeRetryDelay(int64_t now, int64_t cooldownUntil, int32_t priority, int32_t mode)" in socket_cpp
        and "mtProxyHandshakeActiveLimit(const MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode)" in socket_cpp,
        "ConnectionSocket scheduler must be mode-aware for admission, delay, retry, limit, and cooldown policy",
    )
    require(
        "admission_mode=%s" in socket_cpp
        and "connection_pattern=%s" in socket_cpp
        and 'return "browser";' in socket_cpp
        and 'return "strict";' in socket_cpp
        and 'return "quiet";' in socket_cpp
        and "admission_failure_cooldown" in socket_cpp,
        "startup diagnostics must log readable connection-pattern/admission mode and cooldown decisions",
    )
    require(
        "MT_PROXY_CONNECTION_PATTERN_STRICT" in socket_cpp
        and "3000 + secureRandomBounded(3001)" in socket_cpp
        and "MT_PROXY_CONNECTION_PATTERN_QUIET" in socket_cpp
        and "1200 + secureRandomBounded(1301)" in socket_cpp,
        "quiet and strict modes must slow sequential grants instead of only limiting concurrency",
    )
    require(
        "MT_PROXY_HANDSHAKE_SOFT_ACTIVE_LIMIT = 2" in socket_cpp
        and "return MT_PROXY_HANDSHAKE_SOFT_ACTIVE_LIMIT;" in socket_cpp,
        "soft mode must allow two cold-start handshakes so it cannot serialize every blocked endpoint",
    )
    require(
        "MT_PROXY_CONNECTION_PATTERN_BROWSER" in socket_cpp
        and "MT_PROXY_HANDSHAKE_BROWSER_RECENT_SUCCESS_LIMIT" in socket_cpp
        and "MT_PROXY_HANDSHAKE_BROWSER_HEAVY_DELAY_BASE_MS" in socket_cpp
        and "mode == MT_PROXY_CONNECTION_PATTERN_BROWSER" in socket_cpp
        and "state.recentSuccesses >= 1" in socket_cpp,
        "browser-like mode must start with one real handshake, then gently fan out after server_hello_hmac_ok",
    )
    require(
        "MT_PROXY_HANDSHAKE_QUIET_FREEZE_COOLDOWN_MAX_MS" in socket_cpp
        and "MT_PROXY_HANDSHAKE_STRICT_FREEZE_COOLDOWN_MAX_MS" in socket_cpp
        and "mtProxyClampCooldown" in socket_cpp
        and "mtProxyApplyFreezeCooldown(MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode)" in socket_cpp,
        "quiet/strict cooldown must be capped and mode-aware instead of growing to minute-scale waits",
    )
    require(
        "MT_PROXY_HANDSHAKE_FREEZE_COOLDOWN_ENABLED" not in socket_cpp,
        "stale global cooldown flag must not contradict mode-aware cooldown policy",
    )
    require(
        "MT_PROXY_HANDSHAKE_SUCCESS_COOLDOWN_RESET_MS" in socket_cpp
        and "state.cooldownUntil = 0;" in socket_cpp
        and "state.freezePenalty = 0;" in socket_cpp,
        "a successful server_hello_hmac_ok must quickly clear admission penalty",
    )
    require(
        "tcpFailurePenalty" in socket_cpp
        and "handshakeFailurePenalty" in socket_cpp
        and "mtProxyApplyTcpFailureCooldown(MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode)" in socket_cpp
        and "admission_tcp_failure_cooldown" in socket_cpp
        and "proxyHandshakeClientHelloSentTime <= 0" in socket_cpp,
        "pre-ClientHello TCP failures and post-ClientHello handshake failures must have separate cooldown markers instead of being mixed together",
    )
    require(
        "mtProxyCooldownBlocksPriority" in socket_cpp
        and "state.tcpFailurePenalty > 0" in socket_cpp
        and "state.handshakeFailurePenalty > 0" in socket_cpp
        and "return priority > MT_PROXY_HANDSHAKE_PRIORITY_BYPASS;" in socket_cpp
        and "mtProxyCooldownBlocksPriority(state, now, mode, candidate.priority)" in socket_cpp
        and "mtProxyCooldownBlocksPriority(state, now, connectionPatternMode, proxyHandshakeAdmissionPriority)" in socket_cpp,
        "TCP and post-ClientHello cooldowns must throttle generic/media reconnect storms, not only low-priority download/upload attempts",
    )
    require(
        "if ((int64_t) delay < cooldownDelay)" in socket_cpp
        and "delay = (uint32_t) cooldownDelay;" in socket_cpp,
        "queued admission retry timers must wait until cooldown expires instead of waking repeatedly inside cooldown",
    )
    require(
        "bool suppressQueuedGrant = !succeeded && wasActive && proxyHandshakeClientHelloSentTime > 0" in socket_cpp
        and "admission_hold_after_client_hello_failure" in socket_cpp
        and "if (hadAdmission && !suppressQueuedGrant && mtProxyConnectionPatternUsesAdmission(connectionPatternMode))" in socket_cpp,
        "post-ClientHello failures must not immediately dequeue another socket and recreate the slot -> ClientHello loop",
    )
    require(
        "MT_PROXY_HANDSHAKE_TIMER_HOST_RESOLVE" in socket_cpp
        and "scheduleProxyHandshakeAdmissionIfNeeded(bool ipv6, int32_t timerMode)" in socket_h
        and "timerMode" in socket_cpp
        and "requestPendingHostResolve()" in socket_cpp
        and "scheduleProxyHandshakeAdmissionIfNeeded(ipv6, MT_PROXY_HANDSHAKE_TIMER_HOST_RESOLVE)" in socket_cpp,
        "FakeTLS DNS resolution must be admitted before delegate getHostByName, otherwise DNS/TCP failures bypass the browser-like gate",
    )
    require(
        "bool hadAdmission = proxyHandshakeAdmissionActive || proxyHandshakeAdmissionQueued;" in socket_cpp
        and "admission_release_ignored" in socket_cpp
        and "proxyHandshakeAdmissionKey.clear();" in socket_cpp
        and "if (hadAdmission && !suppressQueuedGrant && mtProxyConnectionPatternUsesAdmission(connectionPatternMode))" in socket_cpp,
        "admission release must be idempotent so post-handshake close/suspend cannot dequeue a second queued request",
    )
    require(
        "mtProxyDiagnosticNeedsReconnectBackoff" in connection_cpp
        and "host_resolve_failed" in connection_cpp
        and "tcp_not_connected" in connection_cpp
        and "network_block_suspected" in connection_cpp
        and "client_hello_sent_no_server_hello" in connection_cpp
        and "server_hello_hmac_mismatch" in connection_cpp
        and "mtproxy_startup reconnect_backoff" in connection_cpp
        and "mtproxy_startup reconnect_hold" in connection_cpp
        and "getProxyCheckDiagnostic()" in connection_cpp
        and "connectionState == TcpConnectionStageIdle && connectionType != ConnectionTypeProxy && !isProxyCloseDiagnosticSuppressed() && mtProxyDiagnosticNeedsReconnectBackoff" in connection_cpp
        and "reconnect_backoff_suppressed" in connection_cpp
        and "mtProxyReconnectBackoffMs" in connection_h
        and "mtProxyReconnectHoldUntil" in connection_h,
        "Connection layer must back off real MTProxy failures but not suppressed idle/post-appdata closes",
    )
    reconnect_base = connection_cpp[
        connection_cpp.find("static uint32_t mtProxyReconnectBackoffBaseMs"):
        connection_cpp.find("static uint32_t mtProxyReconnectBackoffMaxMs")
    ]
    reconnect_max = connection_cpp[
        connection_cpp.find("static uint32_t mtProxyReconnectBackoffMaxMs"):
        connection_cpp.find("static uint32_t mtProxyReconnectJitterMs")
    ]
    require(
        "return 1800;" in reconnect_base
        and "return 2500;" in reconnect_base
        and "return 3500;" in reconnect_base
        and "return 8000;" not in reconnect_base,
        "Connection reconnect base backoff must stay responsive; endpoint/TCP gates already prevent retry storms",
    )
    require(
        "return 8000;" in reconnect_max
        and "return 12000;" in reconnect_max
        and "return 16000;" in reconnect_max
        and "return 30000;" not in reconnect_max,
        "Connection reconnect max backoff must not grow to 30s for media/download while the user is actively waiting",
    )
    host_timer_index = socket_cpp.find("if (mode == MT_PROXY_HANDSHAKE_TIMER_HOST_RESOLVE)")
    host_request_index = socket_cpp.find("requestPendingHostResolve();", host_timer_index)
    host_method_index = socket_cpp.find("void ConnectionSocket::requestPendingHostResolve()")
    delegate_index = socket_cpp.find("delegate->getHostByName", host_method_index)
    require(
        host_timer_index >= 0
        and host_request_index >= 0
        and host_method_index >= 0
        and delegate_index >= 0,
        "host-resolve admission timer must start delegate DNS through requestPendingHostResolve",
    )
    for path in (STRINGS, STRINGS_RU):
        source = text(path)
        require(
            'name="MtProxyConnectionPattern"' in source
            and 'name="MtProxyConnectionPatternInfo"' in source
            and 'name="MtProxyConnectionPatternOff"' in source
            and 'name="MtProxyConnectionPatternSoft"' in source
            and 'name="MtProxyConnectionPatternBrowser"' in source
            and 'name="MtProxyConnectionPatternQuiet"' in source
            and 'name="MtProxyConnectionPatternStrict"' in source,
            f"{path.name} must define connection-pattern UI strings",
        )

    print("MTProxy connection pattern modes guard passed.")


if __name__ == "__main__":
    main()
