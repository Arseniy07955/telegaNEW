#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

SOCKET = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
SOCKET_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.h"
CONNECTIONS_JAVA = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
PROXY_LIST = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxyListActivity.java"
DIAGNOSTICS = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckDiagnostics.java"
SCHEDULER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckScheduler.java"
ANALYZER = ROOT / "Tools/analyze_mtproxy_markers.py"
README = ROOT / "README.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        raise SystemExit(1)


def slice_between(text: str, start: str, end: str) -> str:
    start_idx = text.find(start)
    require(start_idx >= 0, f"missing start marker: {start}")
    end_idx = text.find(end, start_idx)
    require(end_idx >= 0, f"missing end marker after {start}: {end}")
    return text[start_idx:end_idx]


def main() -> None:
    socket = read(SOCKET)
    socket_h = read(SOCKET_H)
    connections_java = read(CONNECTIONS_JAVA)
    proxy_list = read(PROXY_LIST)
    diagnostics = read(DIAGNOSTICS)
    scheduler = read(SCHEDULER)
    analyzer = read(ANALYZER)
    readme = read(README)

    open_connection = slice_between(
        socket,
        "void ConnectionSocket::openConnection(std::string address",
        "void ConnectionSocket::openConnectionInternal(bool ipv6)",
    )
    open_internal = slice_between(
        socket,
        "void ConnectionSocket::openConnectionInternal(bool ipv6)",
        "void ConnectionSocket::requestPendingHostResolve",
    )
    endpoint_key = slice_between(
        socket,
        "std::string ConnectionSocket::mtProxyEndpointStateKeyForPhase",
        "void ConnectionSocket::resetMtProxyEndpointStateForKey",
    )
    recipe = slice_between(
        socket,
        "static bool mtProxyEndpointFailureNeedsRecipe",
        "static int64_t mtProxyEndpointCooldownMs",
    )
    cooldown = slice_between(
        socket,
        "static int64_t mtProxyEndpointCooldownMs",
        "static uint32_t mtProxyDataAwareIptDelayMs",
    )
    failure = slice_between(
        socket,
        "void ConnectionSocket::recordMtProxyEndpointFailure",
        "void ConnectionSocket::recordMtProxyEndpointSuccess",
    )
    send_frame = slice_between(
        socket,
        "bool ConnectionSocket::sendPendingTlsFrame()",
        "void ConnectionSocket::openConnection",
    )
    timing_wait = slice_between(
        socket,
        "bool ConnectionSocket::scheduleMtProxyDataTimingIfNeeded()",
        "void ConnectionSocket::startMtProxyStartupCover",
    )
    stage_bridge = slice_between(
        connections_java,
        "public static void onProxyConnectionStageChanged",
        "public static void onLogout",
    )
    ui_notifications = slice_between(
        proxy_list,
        "public void didReceivedNotification",
        "private class ListAdapter",
    )
    status_text = slice_between(
        diagnostics,
        "public static String statusText",
        "public static String headerStatusText",
    )
    header_text = slice_between(
        diagnostics,
        "public static String headerStatusText",
        "public static String shortDiagnosticText",
    )

    # Layer 1: DNS and endpoint stability. These phases happen before JA4 exists.
    sslip_pos = open_connection.find("mtProxyExtractSslipIpv4Address(*proxyAddress")
    cache_pos = open_connection.find("mtProxyEndpointUseCachedHostAddress(*proxyAddress")
    coalesce_pos = open_connection.find("scheduleMtProxyDnsCoalesceIfNeeded(ipv6)")
    resolve_pos = open_connection.find("requestPendingHostResolve();")
    require(
        -1 not in (sslip_pos, cache_pos, coalesce_pos, resolve_pos)
        and sslip_pos < cache_pos < coalesce_pos < resolve_pos,
        "host_resolve_failed path must be sslip.io -> last-good IP cache -> DNS coalesce -> delegate DNS",
    )
    require(
        "currentMtProxyNetworkEndpointKey" in socket_h
        and "currentMtProxyDnsCacheKey" in socket_h
        and "proxyEndpointDnsCoalesceReady" in socket_h,
        "DNS/TCP endpoint state must be stored separately from the FakeTLS recipe key",
    )
    require(
        'phase == "host_resolve_failed"' in endpoint_key
        and 'phase == "tcp_not_connected"' in endpoint_key
        and "currentMtProxyNetworkEndpointKey" in endpoint_key,
        "host_resolve_failed and tcp_not_connected must use the host:port network key",
    )
    require(
        '"host_resolve_failed"' not in recipe
        and '"tcp_not_connected"' not in recipe,
        "pre-TLS DNS/TCP failures must not change JA4/profile/ClientHello recipe",
    )

    # Layer 2: all-MTProxy endpoint circuit breaker, including dd/legacy.
    require(
        "if (isCurrentMtProxyConnection() && scheduleMtProxyEndpointCircuitBreakerIfNeeded(ipv6))" in socket
        and "if (isCurrentMtProxyConnection() && scheduleMtProxyEndpointTcpConnectGateIfNeeded(ipv6))" in socket,
        "endpoint circuit-breaker and TCP connect gate must cover every MTProxy secret kind",
    )
    require(
        '"tcp_not_connected"' in cooldown
        and '"mtproxy_packet_sent_no_response"' in cooldown
        and "plainNoResponseFailures" in cooldown,
        "tcp failures and dd/plain no-response must feed endpoint cooldown",
    )
    require(
        "mtProxyEndpointStateKeyForPhase(phase)" in failure
        and "proxyEndpointResilience[stateKey]" in failure,
        "endpoint failures must route through the phase-aware state-key helper",
    )
    require(
        '"mtproxy_packet_sent_no_response"' in endpoint_key
        and '"mtproxy_packet_sent_no_response"' not in recipe,
        "dd/plain first-packet no-response must be endpoint backoff, never FakeTLS recipe adaptation",
    )

    # Layer 3: FakeTLS phase-adaptive recipe only after post-ClientHello evidence.
    for phase in (
        "client_hello_sent_no_server_hello",
        "server_hello_hmac_mismatch",
        "post_handshake_no_appdata",
    ):
        require(phase in recipe, f"FakeTLS recipe must react to {phase}")
    require(
        "currentSecretIsFakeTls && mtProxyEndpointFailureNeedsRecipe(phase)" in failure,
        "recipe level must advance only for FakeTLS connections",
    )
    require(
        "currentClientHelloFragmentation = MT_PROXY_CLIENT_HELLO_FRAGMENTATION_SOFT" in socket
        and "currentEffectiveProxyTlsProfile = mtProxyEndpointAdaptiveTlsProfile" in socket
        and "currentConnectionPatternMode = MT_PROXY_CONNECTION_PATTERN_QUIET" in socket,
        "phase-adaptive recipe must progress by fragmentation, Android profile, then quiet startup",
    )

    # Layer 4: data path is guarded and data-aware; no idle sleeps that stall MTProto.
    require(
        "timingMode != MT_PROXY_TIMING_OFF && pendingTlsFrame == nullptr && outgoingByteStream->hasData()" in send_frame,
        "IPT must be scheduled only when another MTProto payload is already pending",
    )
    require(
        "timingMode == MT_PROXY_TIMING_OFF || pendingTlsFrame != nullptr || nextTlsFrameWriteTime == 0" in timing_wait,
        "IPT wait must not run during partial TLS-frame writes or idle state",
    )
    require(
        "outgoingByteStream->discard(pendingTlsPayloadSize);" in send_frame
        and "mtproxy_data tls_frame_complete" in send_frame
        and "clearPendingTlsFrame();" in send_frame,
        "FakeTLS ApplicationData must keep a clear complete-frame boundary before discarding payload",
    )

    # GUI/log analyzer must expose the same phase vocabulary immediately.
    for phase in (
        "host_resolve_failed",
        "tcp_not_connected",
        "client_hello_sent_no_server_hello",
        "mtproxy_packet_sent_no_response",
        "endpoint_cooldown",
    ):
        require(phase in diagnostics, f"ProxyCheckDiagnostics must know {phase}")
        require(phase in analyzer, f"analyzer must know {phase}")
    require(
        "currentProxy.lastCheckDiagnostic = normalizedDiagnostic" in stage_bridge
        and "postNotificationName(NotificationCenter.proxyConnectionStageChanged" in stage_bridge,
        "native live stages must update the current proxy and notify the proxy UI immediately",
    )
    require(
        "id == NotificationCenter.proxyConnectionStageChanged" in ui_notifications
        and "updateCurrentProxyStatusCell();" in ui_notifications,
        "proxy UI must repaint the selected proxy row and header on live stage changes",
    )
    require(
        "hasFreshFailure(proxyInfo)" in status_text
        and "hasFreshLivePhase(proxyInfo)" in status_text
        and "hasFreshFailure(proxyInfo)" in header_text
        and "hasFreshLivePhase(proxyInfo)" in header_text,
        "proxy row and window header must prefer fresh concrete phases over generic connecting text",
    )
    require(
        "endpointStateKeyForDiagnostic" in scheduler
        and "NETWORK_BLOCK_SUSPECTED" in scheduler
        and "MTPROXY_PACKET_SENT_NO_RESPONSE" in scheduler,
        "Java proxy-check scheduler must share endpoint-layer classification with native diagnostics",
    )

    # Documentation must keep future work scoped: DRS is valuable, but not first.
    require(
        "DRS пока не первым" in readme
        and "data-aware" in readme
        and "host_resolve_failed" in readme
        and "mtproxy_packet_sent_no_response" in readme,
        "README must document the current layer split and why DRS is not the first fix",
    )

    print("MTProxy resilience contract guard passed.")


if __name__ == "__main__":
    main()
