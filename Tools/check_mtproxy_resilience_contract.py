#!/usr/bin/env python3
from pathlib import Path
import sys

from mtproxy_phase_contract import analyzer_failure_phases, java_phase_names


ROOT = Path(__file__).resolve().parents[1]

SOCKET = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
SOCKET_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.h"
MACHINE_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocketStateMachine.h"
ENDPOINT_POLICY = ROOT / "TMessagesProj/jni/tgnet/MtProxyEndpointPolicy.cpp"
ADAPTIVE_POLICY = ROOT / "TMessagesProj/jni/tgnet/MtProxyAdaptivePolicy.cpp"
PROBE_COORDINATOR = ROOT / "TMessagesProj/jni/tgnet/MtProxyProbeCoordinator.cpp"
CONNECTIONS_JAVA = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
PROXY_LIST = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxyListActivity.java"
DIAGNOSTICS = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckDiagnostics.java"
SCHEDULER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckScheduler.java"
STORE = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyRuntimeStateStore.java"
HEALTH = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyHealthStore.java"
STATUS_MIRROR = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyStatusMirror.java"
POLICY = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyPhasePolicy.java"
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
    endpoint_policy = read(ENDPOINT_POLICY)
    adaptive_policy = read(ADAPTIVE_POLICY)
    probe_coordinator = read(PROBE_COORDINATOR)
    socket_h = read(SOCKET_H)
    socket_state = socket_h + "\n" + read(MACHINE_H) + "\n" + socket
    connections_java = read(CONNECTIONS_JAVA)
    proxy_list = read(PROXY_LIST)
    diagnostics = read(DIAGNOSTICS)
    scheduler = read(SCHEDULER)
    store = read(STORE)
    health = read(HEALTH)
    status_mirror = read(STATUS_MIRROR)
    policy = read(POLICY)
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
        endpoint_policy,
        "std::string MtProxyEndpointPolicy::stateKeyForPhase",
        "bool MtProxyEndpointPolicy::failureNeedsCooldown",
    )
    recipe = slice_between(
        probe_coordinator,
        "bool MtProxyProbeCoordinator::failureNeedsRecipe",
        "MtProxyAdaptivePolicy::RecipeCursor MtProxyProbeCoordinator::recipeCursorForProbe",
    )
    cooldown = slice_between(
        endpoint_policy,
        "static int64_t cooldownMs",
        "bool MtProxyEndpointPolicy::extractSslipIpv4Address",
    )
    failure = slice_between(
        socket,
        "void ConnectionSocket::recordMtProxyEndpointFailure",
        "void ConnectionSocket::recordMtProxyEndpointHandshakeOk",
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
    sslip_pos = open_connection.find("MtProxyEndpointPolicy::extractSslipIpv4Address(*proxyAddress")
    cache_pos = open_connection.find("mtProxyEndpointUseCachedHostAddress(*proxyAddress")
    coalesce_pos = open_connection.find("scheduleMtProxyDnsCoalesceIfNeeded(ipv6)")
    resolve_pos = open_connection.find("requestPendingHostResolve();")
    require(
        -1 not in (sslip_pos, cache_pos, coalesce_pos, resolve_pos)
        and sslip_pos < cache_pos < coalesce_pos < resolve_pos,
        "host_resolve_failed path must be sslip.io -> last-good IP cache -> DNS coalesce -> delegate DNS",
    )
    require(
        "currentMtProxyNetworkEndpointKey" in socket_state
        and "currentMtProxyDnsCacheKey" in socket_state
        and "proxyEndpointDnsCoalesceReady" in socket_state,
        "DNS/TCP endpoint state must be stored separately from the FakeTLS recipe key",
    )
    require(
        'phase == "host_resolve_failed"' in endpoint_key
        and 'phase == "tcp_not_connected"' in endpoint_key
        and "networkEndpointKey" in endpoint_key,
        "host_resolve_failed and tcp_not_connected must use the host:port network key",
    )
    require(
        '"host_resolve_failed"' not in recipe
        and 'diagnostic == "tcp_not_connected"' in recipe
        and "return false;" in recipe,
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
        "MtProxyEndpointPolicy::recordFailure" in failure
        and "stateKeyForPhase" in endpoint_policy,
        "endpoint failures must route through the phase-aware state-key helper",
    )
    require(
        '"mtproxy_packet_sent_no_response"' not in endpoint_key
        and '"mtproxy_packet_sent_no_response"' not in recipe,
        "dd/plain first-packet no-response must be exact-config backoff, never network backoff or FakeTLS recipe adaptation",
    )

    # Layer 3: FakeTLS phase-adaptive recipe only after post-ClientHello evidence.
    for phase in (
        "true_client_hello_timeout",
        "faketls_server_hello_wait_timeout",
        "server_closed_after_client_hello",
        "client_hello_sent_no_server_hello",
        "tls_alert_after_client_hello",
        "short_tls_response_after_client_hello",
        "unrecognized_response_after_client_hello",
        "unrecognized_tls_response_after_client_hello",
        "server_hello_hmac_mismatch",
        "post_handshake_no_appdata",
    ):
        require(phase in recipe, f"FakeTLS recipe must react to {phase}")
    require(
        "currentSecretIsFakeTls && MtProxyProbeCoordinator::failureNeedsRecipe(phase)" in failure,
        "recipe level must advance only for FakeTLS connections",
    )
    require(
        "result.clientHelloFragmentation = MT_PROXY_CLIENT_HELLO_FRAGMENTATION_OFF" in adaptive_policy
        and "MT_PROXY_TLS_PROFILE_LEGACY_NO_GREASE" in adaptive_policy
        and "CLIENT_HELLO_ANDROID_CHROME_NO_FRAGMENT" in adaptive_policy
        and "CLIENT_HELLO_FIREFOX_ANDROID_NO_FRAGMENT" in adaptive_policy
        and "MT_PROXY_SERVER_HELLO_PARSER_EXTRA_RECORDS" in adaptive_policy
        and "MT_PROXY_SERVER_HELLO_PARSER_FRAGMENTED_SERVER_HELLO" in adaptive_policy
        and "currentClientHelloFragmentation = recipe.clientHelloFragmentation" in socket,
        "phase-adaptive recipe must progress by explicit ClientHello families and parser variants",
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
    for phase in sorted((analyzer_failure_phases() & java_phase_names()) | {"endpoint_cooldown"}):
        require(phase in diagnostics, f"ProxyCheckDiagnostics must know {phase}")
    for phase in sorted(analyzer_failure_phases() | {"endpoint_cooldown"}):
        require(phase in analyzer, f"analyzer must know {phase}")
    require(
        "ProxyConnectionEvent.nativeStage" in stage_bridge
        and "ProxyRuntimeStateStore.onNativeStage(event)" in stage_bridge
        and "ProxyStatusMirror.mirrorVisiblePhase(currentProxy, event.phase, event.timestamp)" in store
        and "static void mirrorVisiblePhase" in status_mirror
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
        ("ProxyEndpointKey.forPhase(proxyInfo, normalizedDiagnostic)" in health
         or "ProxyEndpointKey.forPhase(proxyInfo, normalized)" in health)
        and "NETWORK_BLOCK_SUSPECTED" in policy
        and "MTPROXY_PACKET_SENT_NO_RESPONSE" in policy,
        "Java proxy health store must share endpoint-layer classification with native diagnostics",
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
