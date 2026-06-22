#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "socket": ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp",
    "socket_header": ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.h",
    "connection": ROOT / "TMessagesProj/jni/tgnet/Connection.cpp",
    "connection_header": ROOT / "TMessagesProj/jni/tgnet/Connection.h",
    "diagnostics": ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckDiagnostics.java",
    "values": ROOT / "TMessagesProj/src/main/res/values/strings.xml",
    "values_ru": ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml",
    "analyzer": ROOT / "Tools/analyze_mtproxy_markers.py",
}

REQUIRED_PHASES = [
    "endpoint_cooldown",
    "tcp_connect_gate",
    "dns_coalesce_wait",
    "dns_cache_hit",
    "dns_cache_store",
    "phase_adaptive_recipe",
]


def read(name):
    return FILES[name].read_text(encoding="utf-8", errors="replace")


def require(condition, message):
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def main():
    socket = read("socket")
    header = read("socket_header")
    connection = read("connection")
    connection_header = read("connection_header")
    diagnostics = read("diagnostics")
    analyzer = read("analyzer")
    values = read("values")
    values_ru = read("values_ru")
    combined = "\n".join([socket, header, connection, connection_header, diagnostics, analyzer, values, values_ru])

    require(
        "MT_PROXY_HANDSHAKE_TIMER_ENDPOINT_BACKOFF" in socket,
        "ConnectionSocket must have a separate endpoint cooldown timer before TCP connect",
    )
    require(
        "MT_PROXY_HANDSHAKE_TIMER_DNS_COALESCE" in socket
        and "MT_PROXY_ENDPOINT_DNS_COALESCE_MS" in socket,
        "ConnectionSocket must have a short DNS coalescing timer before delegate host resolve",
    )
    require(
        "MT_PROXY_HANDSHAKE_TIMER_TCP_CONNECT_GATE" in socket
        and "MT_PROXY_ENDPOINT_TCP_CONNECT_GATE_MS" in socket,
        "ConnectionSocket must have a short active-TCP-connect gate for repeated endpoint starts",
    )
    require(
        "MtProxyEndpointResilienceState" in socket and "proxyEndpointResilience" in socket,
        "ConnectionSocket must keep per-endpoint resilience state outside FakeTLS admission",
    )
    require(
        "currentMtProxyEndpointKey" in header and "proxyEndpointBackoffReady" in header,
        "ConnectionSocket must remember endpoint circuit-breaker state per socket",
    )
    require(
        "proxyEndpointTcpConnectActive" in header
        and "proxyEndpointTcpConnectReady" in header,
        "ConnectionSocket must remember active TCP connect gate state per socket",
    )
    require(
        "currentMtProxyDnsCacheKey" in header,
        "ConnectionSocket must keep a DNS cache key separate from the MTProxy secret/SNI endpoint key",
    )
    require(
        "currentMtProxyNetworkEndpointKey" in header,
        "ConnectionSocket must keep a host/port network endpoint key for pre-TLS DNS/TCP failures",
    )
    require(
        "proxyEndpointDnsCoalesceReady" in header,
        "ConnectionSocket must remember when a DNS coalescing delay has already fired for this socket",
    )
    require(
        "scheduleMtProxyEndpointCircuitBreakerIfNeeded(ipv6)" in socket,
        "openConnectionInternal must run endpoint circuit breaker before TCP connect",
    )
    require(
        "if (isCurrentMtProxyConnection() && scheduleMtProxyEndpointCircuitBreakerIfNeeded(ipv6))" in socket,
        "endpoint circuit breaker must cover all MTProxy secret kinds, not only ee FakeTLS",
    )
    require(
        "if (isCurrentMtProxyConnection() && scheduleMtProxyEndpointTcpConnectGateIfNeeded(ipv6))" in socket,
        "endpoint TCP connect gate must cover all MTProxy secret kinds before real connect()",
    )
    require(
        "scheduleMtProxyDnsCoalesceIfNeeded(ipv6)" in socket,
        "domain MTProxy endpoints must coalesce cold DNS resolves before calling delegate DNS",
    )
    schedule_start = socket.find("bool ConnectionSocket::scheduleMtProxyEndpointCircuitBreakerIfNeeded")
    schedule_end = socket.find("void ConnectionSocket::recordMtProxyEndpointFailure", schedule_start)
    schedule_body = socket[schedule_start:schedule_end]
    require(
        "!mtProxyConnectionPatternUsesCooldown(connectionPatternMode)" not in schedule_body,
        "endpoint circuit breaker must run in Default/Soft too; connection pattern may scale delay, not disable it",
    )
    cooldown_start = socket.find("static int64_t mtProxyEndpointCooldownMs")
    cooldown_end = socket.find("static uint32_t mtProxyDataAwareIptDelayMs", cooldown_start)
    cooldown_body = socket[cooldown_start:cooldown_end]
    require(
        "return 0;" not in cooldown_body,
        "endpoint failure recording must produce a small cooldown even in Default/Soft modes",
    )
    require(
        '"mtproxy_packet_sent_no_response"' in cooldown_body
        and '"dropped_early_after_appdata"' in cooldown_body
        and "plainNoResponseFailures" in cooldown_body,
        "dd/plain MTProxy first-packet/no-response and early post-appdata drops must feed endpoint backoff",
    )
    require(
        "networkFailure" in cooldown_body
        and 'diagnostic == "host_resolve_failed" || diagnostic == "tcp_not_connected"' in cooldown_body
        and "priority" in cooldown_body
        and "interactiveNetworkFailure" in cooldown_body
        and "MT_PROXY_ENDPOINT_INTERACTIVE_NETWORK_COOLDOWN_MAX_MS" in cooldown_body
        and "MT_PROXY_ENDPOINT_HEAVY_NETWORK_COOLDOWN_MAX_MS" in cooldown_body
        and "MT_PROXY_ENDPOINT_BROWSER_NETWORK_COOLDOWN_MAX_MS" not in cooldown_body,
        "pre-TCP host/DNS failures must use short priority-aware network cooldowns, not old long endpoint-wide waits",
    )
    require(
        "mtProxyEndpointCooldownMs(state, phase, connectionPatternMode, proxyHandshakeAdmissionPriority)" in socket
        and "priority=%d cooldown_ms" in socket,
        "endpoint failure recording must pass connection priority into cooldown calculation and log it",
    )
    require(
        "recordMtProxyEndpointFailure(proxyCheckDiagnostic.c_str()" in socket,
        "closeSocket must feed close diagnostics back into endpoint resilience state",
    )
    timer_start = socket.find("void ConnectionSocket::scheduleProxyHandshakeAdmissionTimer")
    timer_end = socket.find("void ConnectionSocket::grantProxyHandshakeAdmission", timer_start)
    timer_body = socket[timer_start:timer_end]
    require(
        "proxyHandshakeAdmissionTimerGeneration" in header
        and "proxyHandshakeAdmissionTimerGeneration = proxyHandshakeAdmissionGeneration;" in timer_body
        and "uint32_t timerGeneration = proxyHandshakeAdmissionTimerGeneration;" in timer_body
        and "timerGeneration != proxyHandshakeAdmissionGeneration || mode == 0" in timer_body
        and "admission_timer_ignored" in timer_body
        and '"admission_timer_ignored": "admission_timer_ignored"' in analyzer,
        "shared MTProxy timer must ignore callbacks from cancelled/replaced handshake generations",
    )
    close_start = socket.find("void ConnectionSocket::closeSocket")
    close_end = socket.find("void ConnectionSocket::onDisconnected", close_start)
    close_body = socket[close_start:close_end]
    open_connection_start = socket.find("void ConnectionSocket::openConnection(std::string address")
    open_connection_end = socket.find("void ConnectionSocket::openConnectionInternal", open_connection_start)
    open_connection_body = socket[open_connection_start:open_connection_end]
    require(
        "socketCloseNotified" in header
        and "socketCloseNotified = false;" in open_connection_body
        and "if (socketCloseNotified)" in close_body
        and "close_ignored_already_closed" in close_body
        and '"close_ignored_already_closed": "close_ignored_already_closed"' in analyzer
        and "socketCloseNotified = true;" in close_body,
        "closeSocket must be idempotent so one socket failure cannot publish multiple endpoint failures or reconnect backoffs",
    )
    suspend_start = connection.find("void Connection::suspendConnection(bool idle)")
    suspend_end = connection.find("void Connection::onReceivedData", suspend_start)
    suspend_body = connection[suspend_start:suspend_end]
    disconnect_start = connection.find("void Connection::onDisconnectedInternal")
    disconnect_end = connection.find("void Connection::onDisconnected(", disconnect_start)
    disconnect_body = connection[disconnect_start:disconnect_end]
    notify_start = connection.find("void Connection::notifyConnectionClosedOnce")
    notify_end = connection.find("void Connection::onDisconnectedInternal", notify_start)
    notify_body = connection[notify_start:notify_end]
    raw_manager_close = "ConnectionsManager::getInstance(currentDatacenter->instanceNum).onConnectionClosed(this,"
    require(
        "connectionCloseNotified" in connection_header
        and "void notifyConnectionClosedOnce(int32_t reason, const char *source);" in connection_header
        and notify_start != -1
        and "if (connectionCloseNotified)" in notify_body
        and "connection_close_ignored_already_notified" in notify_body
        and '"connection_close_ignored_already_notified": "connection_close_ignored_already_notified"' in analyzer
        and "connectionCloseNotified = true;" in notify_body,
        "Connection must centralize manager close notifications so socket close and suspend cannot both publish one close",
    )
    require(
        "connectionCloseNotified = false;" in connection[connection.find("void Connection::connect()"):connection.find("inline std::string *Connection::getCurrentSecret", connection.find("void Connection::connect()"))]
        and 'notifyConnectionClosedOnce(0, "network_unavailable_connect")' in connection,
        "new connect attempts and no-network exits must use the one-shot Connection close notifier",
    )
    require(
        'notifyConnectionClosedOnce(0, "suspendConnection")' in suspend_body
        and raw_manager_close not in suspend_body,
        "suspendConnection must not directly notify ConnectionsManager after dropConnection; use the one-shot notifier",
    )
    require(
        'notifyConnectionClosedOnce(reason, "onDisconnectedInternal")' in disconnect_body
        and raw_manager_close not in disconnect_body,
        "onDisconnectedInternal must use the one-shot close notifier before reconnect/backoff handling",
    )
    require(
        "!isProxyCloseDiagnosticSuppressed()" in disconnect_body
        and "reconnect_backoff_suppressed" in disconnect_body
        and '"reconnect_backoff_suppressed": "reconnect_backoff_suppressed"' in analyzer,
        "suppressed close diagnostics must not still trigger reconnect_backoff in Connection",
    )
    require(
        "suppressProxyCloseDiagnostic" in close_body
        and 'proxyCheckDiagnostic == "post_handshake_no_appdata"' in close_body
        and "!mtproxyFirstTlsFrameSentLogged" in close_body
        and "!mtproxyFirstPlainDataSentLogged" in close_body
        and 'proxyCheckDiagnostic == "dropped_after_appdata"' in close_body
        and "mtproxyFirstTlsDataReceivedLogged || mtproxyFirstPlainDataReceivedLogged" in close_body
        and "proxyCloseDiagnosticSuppressed = true;" in close_body
        and "isProxyCloseDiagnosticSuppressed()" in header
        and "close_diagnostic_suppressed" in close_body,
        "closeSocket must suppress idle post-handshake closes and already-usable post-appdata closes before publishing a proxy failure",
    )
    require(
        "!suppressProxyCloseDiagnostic && reason != 0 && isCurrentMtProxyConnection() && !proxyCheckDiagnostic.empty()" in close_body
        and "publishProxyConnectionStage(proxyCheckDiagnostic.c_str())" in close_body
        and "recordMtProxyEndpointFailure(proxyCheckDiagnostic.c_str(), \"closeSocket\")" in close_body,
        "closeSocket must still publish and record real non-suppressed MTProxy close diagnostics",
    )
    require(
        "activeTcpConnects" in socket
        and "releaseMtProxyEndpointTcpConnect" in socket,
        "endpoint resilience state must count active TCP connect attempts and release them",
    )
    require(
        'releaseMtProxyEndpointTcpConnect("socket_connected")' in socket
        and 'releaseMtProxyEndpointTcpConnect("closeSocket")' in socket,
        "active TCP connect gate must release on socket_connected and on closeSocket",
    )
    require(
        'releaseMtProxyEndpointTcpConnect("openConnection_reset")' in socket,
        "new openConnection must release any stale active TCP connect slot before resetting endpoint state",
    )
    for marker in [
        'recordMtProxyEndpointSuccess("server_hello_hmac_ok")',
        'recordMtProxyEndpointSuccess("first_tls_app_recv")',
        'recordMtProxyEndpointSuccess("first_mtproxy_packet_recv")',
    ]:
        require(marker in socket, f"endpoint success marker missing: {marker}")
    require(
        "mtProxyEndpointUseCachedHostAddress" in socket and "mtProxyEndpointStoreResolvedAddress" in socket,
        "DNS failures must have a last-good-IP fallback and store path",
    )
    require(
        "resolveInFlightUntil" in socket,
        "DNS cache state must record a short in-flight resolve window to suppress duplicate cold resolves",
    )
    sslip_start = socket.find("static bool mtProxyExtractSslipIpv4Address")
    sslip_end = socket.find("static const char *mtProxySecretKindName", sslip_start)
    sslip_body = socket[sslip_start:sslip_end]
    require(
        sslip_start != -1
        and "std::transform" in sslip_body
        and "::tolower" in sslip_body
        and ".sslip.io" in sslip_body,
        "sslip.io fast-path must be case-insensitive so valid domain spelling does not fall through to DNS",
    )
    sslip_call = socket.find("mtProxyExtractSslipIpv4Address(*proxyAddress")
    dns_cache_call = socket.find("mtProxyEndpointUseCachedHostAddress(*proxyAddress")
    host_resolve_call = socket.find("requestPendingHostResolve();", dns_cache_call)
    require(
        sslip_call != -1
        and dns_cache_call != -1
        and host_resolve_call != -1
        and sslip_call < dns_cache_call < host_resolve_call,
        "sslip.io fast-path must run before DNS cache lookup and before delegate DNS resolve",
    )
    coalesce_call = socket.find("scheduleMtProxyDnsCoalesceIfNeeded(ipv6)")
    require(
        dns_cache_call < coalesce_call < host_resolve_call,
        "DNS coalescing must run after last-good-IP cache lookup but before delegate DNS resolve",
    )
    require(
        "MtProxyDnsCacheState" in socket and "proxyEndpointDnsCache" in socket,
        "DNS last-good-IP cache must be stored separately from per-secret/SNI endpoint resilience state",
    )
    require(
        "mtProxyDnsCacheKeyFor" in socket,
        "DNS cache key must be host/port-scoped instead of secret/SNI-scoped",
    )
    require(
        "mtProxyNetworkEndpointKeyFor" in socket,
        "pre-TLS endpoint resilience must have a host/port-scoped network key separate from secret/SNI recipe state",
    )
    dns_key_start = socket.find("static std::string mtProxyDnsCacheKeyFor")
    dns_key_end = socket.find("static const char *mtProxyDisconnectReasonName", dns_key_start)
    dns_key_body = socket[dns_key_start:dns_key_end]
    require(
        "std::transform" in dns_key_body and "::tolower" in dns_key_body,
        "DNS cache key must lowercase host names so equivalent domains share one last-good-IP entry",
    )
    network_key_start = socket.find("static std::string mtProxyNetworkEndpointKeyFor")
    network_key_end = socket.find("static std::string mtProxyEndpointKeyFor", network_key_start)
    network_key_body = socket[network_key_start:network_key_end]
    require(
        network_key_start != -1
        and "std::transform" in network_key_body
        and "::tolower" in network_key_body
        and "secretKind" not in network_key_body
        and "domain" not in network_key_body,
        "network endpoint key must be lowercase host/port only; pre-TLS failures must not split by secret/SNI",
    )
    tcp_gate_start = socket.find("bool ConnectionSocket::scheduleMtProxyEndpointTcpConnectGateIfNeeded")
    tcp_gate_end = socket.find("void ConnectionSocket::releaseMtProxyEndpointTcpConnect", tcp_gate_start)
    tcp_gate_body = socket[tcp_gate_start:tcp_gate_end]
    require(
        "currentMtProxyNetworkEndpointKey" in tcp_gate_body
        and "currentMtProxyEndpointKey" not in tcp_gate_body,
        "TCP connect gate must use the host/port network key, not the secret/SNI FakeTLS endpoint key",
    )
    tcp_release_start = socket.find("void ConnectionSocket::releaseMtProxyEndpointTcpConnect")
    tcp_release_end = socket.find("bool ConnectionSocket::scheduleMtProxyDnsCoalesceIfNeeded", tcp_release_start)
    tcp_release_body = socket[tcp_release_start:tcp_release_end]
    require(
        "currentMtProxyNetworkEndpointKey" in tcp_release_body
        and "currentMtProxyEndpointKey" not in tcp_release_body,
        "TCP connect gate release must release the same host/port network key it acquired",
    )
    dns_cache_start = socket.find("bool ConnectionSocket::mtProxyEndpointUseCachedHostAddress")
    dns_cache_end = socket.find("void ConnectionSocket::applyMtProxyPhaseAdaptiveRecipe", dns_cache_start)
    dns_cache_body = socket[dns_cache_start:dns_cache_end]
    require(
        "currentMtProxyDnsCacheKey" in dns_cache_body
        and "proxyEndpointDnsCache" in dns_cache_body
        and "currentMtProxyEndpointKey" not in dns_cache_body
        and "proxyEndpointResilience" not in dns_cache_body,
        "DNS cache use/store path must not depend on the per-secret/SNI resilience endpoint key",
    )
    request_start = socket.find("void ConnectionSocket::requestPendingHostResolve")
    request_end = socket.find("void ConnectionSocket::onHostNameResolved", request_start)
    request_body = socket[request_start:request_end]
    no_delegate = request_body.find("manager.delegate == nullptr")
    no_delegate_cache = request_body.find("mtProxyEndpointUseCachedHostAddress(waitingForHostResolve, &cachedIpv6)")
    no_delegate_failure = request_body.find('proxyCheckDiagnostic = "host_resolve_failed"')
    require(
        no_delegate != -1
        and no_delegate_cache != -1
        and no_delegate_failure != -1
        and no_delegate < no_delegate_cache < no_delegate_failure,
        "missing resolver delegate must try last-good-IP cache before publishing host_resolve_failed",
    )
    resolved_start = socket.find("void ConnectionSocket::onHostNameResolved")
    resolved_end = socket.find("void ConnectionSocket::openConnectionInternal", resolved_start)
    resolved_body = socket[resolved_start:resolved_end]
    cache_after_delegate_failure = resolved_body.find("mtProxyEndpointUseCachedHostAddress(host, &cachedIpv6)")
    host_resolve_failure = resolved_body.find('proxyCheckDiagnostic = "host_resolve_failed"')
    store_resolved = resolved_body.find("mtProxyEndpointStoreResolvedAddress(host, ip)")
    require(
        cache_after_delegate_failure != -1
        and host_resolve_failure != -1
        and cache_after_delegate_failure < host_resolve_failure,
        "delegate DNS failure must try the last-good-IP cache before publishing host_resolve_failed",
    )
    require(
        store_resolved != -1
        and host_resolve_failure != -1
        and host_resolve_failure < store_resolved,
        "delegate DNS success must store the resolved IP only after the failure path has been bypassed",
    )
    require(
        "applyMtProxyPhaseAdaptiveRecipe" in socket,
        "FakeTLS failures must affect the next connection recipe, not only logs",
    )
    recipe_start = socket.find("static bool mtProxyEndpointFailureNeedsRecipe")
    recipe_end = socket.find("static int64_t mtProxyEndpointCooldownMs", recipe_start)
    recipe_body = socket[recipe_start:recipe_end]
    require(
        "client_hello_sent_no_server_hello" in recipe_body
        and "server_hello_hmac_mismatch" in recipe_body
        and "peer_closed_after_client_hello" in recipe_body
        and "post_handshake_no_appdata" in recipe_body,
        "phase-adaptive recipe must react only to FakeTLS/post-ClientHello semantic failures",
    )
    require(
        "tcp_not_connected" not in recipe_body
        and "host_resolve_failed" not in recipe_body
        and "mtproxy_packet_sent_no_response" not in recipe_body
        and "dropped_early_after_appdata" not in recipe_body
        and "tcp_connected_no_pong" not in recipe_body,
        "phase-adaptive recipe must not react to DNS/TCP/plain-dd failures where JA4/ClientHello did not help",
    )
    failure_start = socket.find("void ConnectionSocket::recordMtProxyEndpointFailure")
    failure_end = socket.find("void ConnectionSocket::recordMtProxyEndpointSuccess", failure_start)
    failure_body = socket[failure_start:failure_end]
    state_key_start = socket.find("std::string ConnectionSocket::mtProxyEndpointStateKeyForPhase")
    state_key_end = socket.find("void ConnectionSocket::resetMtProxyEndpointStateForKey", state_key_start)
    state_key_body = socket[state_key_start:state_key_end]
    require(
        "mtProxyEndpointStateKeyForPhase(phase)" in failure_body,
        "endpoint failure recording must route through the phase-aware endpoint-state key helper",
    )
    require(
        '"host_resolve_failed"' in state_key_body
        and '"tcp_not_connected"' in state_key_body
        and '"tcp_connected_no_pong"' in state_key_body
        and '"mtproxy_packet_sent_no_response"' in state_key_body
        and '"dropped_early_after_appdata"' in state_key_body
        and "currentMtProxyNetworkEndpointKey" in state_key_body,
        "DNS/TCP/plain-dd failures must record cooldown on the host/port network key, while FakeTLS recipe stays secret/SNI-scoped",
    )
    require(
        "proxyEndpointResilience[stateKey]" in failure_body
        and "proxyEndpointResilience[currentMtProxyEndpointKey]" in failure_body,
        "failure cooldown and FakeTLS recipe must use separate state entries when the phase requires it",
    )
    require(
        "currentSecretIsFakeTls && mtProxyEndpointFailureNeedsRecipe(phase)" in failure_body,
        "recipe level must only advance for FakeTLS connections, never for dd/legacy MTProxy",
    )
    success_start = socket.find("void ConnectionSocket::recordMtProxyEndpointSuccess")
    success_end = socket.find("bool ConnectionSocket::mtProxyEndpointUseCachedHostAddress", success_start)
    success_body = socket[success_start:success_end]
    require(
        "resetMtProxyEndpointStateForKey(currentMtProxyNetworkEndpointKey" in success_body
        and "resetMtProxyEndpointStateForKey(currentMtProxyEndpointKey" in success_body,
        "endpoint success must clear both host/port network cooldown and secret/SNI recipe cooldown",
    )
    require(
        "MT_PROXY_ENDPOINT_RECIPE_MAX_LEVEL = 3" in socket,
        "phase-adaptive recipe must have three levels: fragmentation, Android profile, quiet startup",
    )
    recipe_apply_start = socket.find("void ConnectionSocket::applyMtProxyPhaseAdaptiveRecipe")
    recipe_apply_end = socket.find("void ConnectionSocket::markProxyHandshakeClientHelloSent", recipe_apply_start)
    recipe_apply_body = socket[recipe_apply_start:recipe_apply_end]
    fragment_step = recipe_apply_body.find("currentClientHelloFragmentation = MT_PROXY_CLIENT_HELLO_FRAGMENTATION_SOFT")
    profile_step = recipe_apply_body.find("currentEffectiveProxyTlsProfile = mtProxyEndpointAdaptiveTlsProfile")
    quiet_step = recipe_apply_body.find("currentConnectionPatternMode = MT_PROXY_CONNECTION_PATTERN_QUIET")
    require(
        fragment_step != -1
        and profile_step != -1
        and quiet_step != -1
        and fragment_step < profile_step < quiet_step,
        "phase-adaptive recipe must progress in order: fragmentation, Android profile, then quieter startup",
    )
    require(
        "currentConnectionPatternMode == MT_PROXY_CONNECTION_PATTERN_BROWSER" in recipe_apply_body
        and "currentConnectionPatternMode = MT_PROXY_CONNECTION_PATTERN_QUIET" in recipe_apply_body,
        "phase-adaptive quiet-start step must make Browser mode quieter after repeated post-ClientHello failures",
    )
    require(
        "mtProxyEndpointAdaptiveTlsProfile" in socket
        and "currentEffectiveProxyTlsProfile = mtProxyEndpointAdaptiveTlsProfile" in socket,
        "phase-adaptive recipe must switch Auto/AutoRotate to another stable Android TLS profile",
    )
    require(
        "MT_PROXY_TLS_PROFILE_ANDROID_OKHTTP" in socket
        and "MT_PROXY_TLS_PROFILE_FIREFOX_ANDROID" in socket,
        "phase-adaptive profile step must stay inside Android-family TLS profiles",
    )
    require(
        "recipeLevel >= 3" in socket and "MT_PROXY_CONNECTION_PATTERN_QUIET" in socket,
        "quiet startup must be the third phase-adaptive step, after profile adaptation",
    )
    require(
        "mtProxyDataAwareIptDelayMs" in socket and "outgoingByteStream->hasData()" in socket,
        "post-handshake timing must be data-aware, not idle-thread sleeping",
    )

    for phase in REQUIRED_PHASES:
        require(phase in diagnostics, f"GUI diagnostics must know phase {phase}")
        require(phase in analyzer, f"log analyzer must know phase {phase}")
        require(phase in combined, f"phase {phase} must be present in codebase")

    for string_name in [
        "ProxyStatusEndpointCooldown",
        "ProxyStatusTcpConnectGate",
        "ProxyStatusDnsCoalesceWait",
        "ProxyStatusDnsCacheHit",
        "ProxyStatusDnsCacheStore",
        "ProxyStatusPhaseAdaptiveRecipe",
    ]:
        require(string_name in values, f"English string missing: {string_name}")
        require(string_name in values_ru, f"Russian string missing: {string_name}")

    print("MTProxy endpoint resilience layers guard passed.")


if __name__ == "__main__":
    main()
