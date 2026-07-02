#!/usr/bin/env python3
from pathlib import Path
import re
import sys

from mtproxy_phase_contract import reconnect_backoff_phases


ROOT = Path(__file__).resolve().parents[1]
CONNECTIONS_JAVA = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
PROXY_LIST = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxyListActivity.java"
SHARED_CONFIG = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/SharedConfig.java"
SOCKET_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
SOCKET_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.h"
MACHINE_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocketStateMachine.h"
CONNECTION_CPP = ROOT / "TMessagesProj/jni/tgnet/Connection.cpp"
CONNECTION_H = ROOT / "TMessagesProj/jni/tgnet/Connection.h"
MANAGER_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionsManager.cpp"
MANAGER_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionsManager.h"
PROXY_CHECK = ROOT / "TMessagesProj/jni/tgnet/ProxyCheckInfo.h"
STRINGS = ROOT / "TMessagesProj/src/main/res/values/strings.xml"
STRINGS_RU = ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml"
NATIVE_PHASE_CONTRACT = ROOT / "TMessagesProj/jni/mtproxy/MtProxyPhaseContract.h"
NATIVE_PHASE_CLASSIFICATION = ROOT / "TMessagesProj/jni/mtproxy/MtProxyPhaseClassification.h"
RETRY_AUTHORITY_CPP = ROOT / "TMessagesProj/jni/mtproxy/MtProxyRetryAuthority.cpp"
HANDSHAKE_SCHEDULER_H = ROOT / "TMessagesProj/jni/mtproxy/MtProxyHandshakeScheduler.h"
HANDSHAKE_SCHEDULER_CPP = ROOT / "TMessagesProj/jni/mtproxy/MtProxyHandshakeScheduler.cpp"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def native_phase_constants() -> dict[str, str]:
    contract = text(NATIVE_PHASE_CONTRACT)
    return {
        value: name
        for name, value in re.findall(r'constexpr const char \*([A-Za-z0-9_]+)\s*=\s*"([a-z0-9_]+)"', contract)
    }


def has_phase(source: str, phase: str, constants: dict[str, str]) -> bool:
    constant = constants.get(phase)
    return f'"{phase}"' in source or (constant is not None and f"MtProxyPhase::{constant}" in source)


def main() -> None:
    connections = text(CONNECTIONS_JAVA)
    proxy_list = text(PROXY_LIST)
    shared_config = text(SHARED_CONFIG)
    socket_cpp = text(SOCKET_CPP)
    socket_h = text(SOCKET_H)
    socket_state = socket_h + "\n" + text(MACHINE_H) + "\n" + socket_cpp
    connection_cpp = text(CONNECTION_CPP)
    connection_h = text(CONNECTION_H)
    handshake_scheduler_h = text(HANDSHAKE_SCHEDULER_H)
    handshake_scheduler_cpp = text(HANDSHAKE_SCHEDULER_CPP)
    manager_cpp = text(MANAGER_CPP)
    manager_h = text(MANAGER_H)
    proxy_check = text(PROXY_CHECK)
    phase_constants = native_phase_constants()

    for name in ("OFF", "SOFT", "BROWSER", "QUIET", "STRICT"):
        require(
            f"MT_PROXY_CONNECTION_PATTERN_{name}" in connections
            and f"MT_PROXY_CONNECTION_PATTERN_{name}" in socket_cpp,
            f"Java and native must define MTProxy connection pattern {name}",
        )

    require(
        "public static int mtProxyConnectionPatternMode" in shared_config
        and 'getInt("mtProxyConnectionPatternMode"' in shared_config
        and 'putInt("mtProxyConnectionPatternMode", mtProxyConnectionPatternMode)' in shared_config,
        "SharedConfig must persist integer connection-pattern mode",
    )
    require(
        'getBoolean("mtProxyHandshakeAdmission"' not in shared_config,
        "SharedConfig must not migrate the old admission boolean into connection-pattern mode",
    )
    require(
        "static int resolveMtProxyConnectionPatternMode()" in connections
        and "SharedConfig.mtProxyConnectionPatternMode" in connections
        and "mtProxyConnectionPatternMode" in connections
        and "MtProxyOptions.resolve(proxyAddress, proxyPort, proxySecret)" in connections,
        "Java must pass the selected connection-pattern mode through MtProxyOptions",
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
        "MtProxyOptions proxyMtProxyOptions" in manager_h
        and "normalizeMtProxyOptions(options)" in manager_cpp
        and "optionsChanged" in manager_cpp,
        "native ConnectionsManager must store MTProxy options and reconnect when connection-pattern mode changes",
    )
    require(
        "MtProxyOptions mtProxyOptions" in proxy_check
        and "overrideMtProxyOptions" in socket_h
        and "currentConnectionPatternMode" in socket_state
        and "setOverrideProxy(std::string address, uint16_t port, std::string username, std::string password, std::string secret, const MtProxyOptions &options)" in socket_h,
        "proxy-check override sockets must carry the same MtProxyOptions as real sockets",
    )
    require(
        "mtProxyConnectionPatternModeName" in socket_cpp
        and "mtProxyHandshakeSchedulerUsesAdmission" in socket_cpp
        and "mtProxyHandshakeSchedulerUsesAdmission(int32_t mode)" in handshake_scheduler_cpp
        and "mtProxyHandshakeSchedulerUsesCooldown" in handshake_scheduler_cpp
        and "mtProxyHandshakeGrantDelay(int32_t mode)" in handshake_scheduler_cpp
        and "mtProxyHandshakeSpacingDelay" in handshake_scheduler_cpp
        and "lastGrantTime" in handshake_scheduler_cpp
        and "mtProxyHandshakeSchedulerRetryDelay(int64_t now, int64_t cooldownUntil, int32_t priority, int32_t mode)" in handshake_scheduler_cpp
        and "mtProxyHandshakeActiveLimit(const MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode)" in handshake_scheduler_cpp,
        "extracted scheduler must be mode-aware for admission, delay, retry, limit, and cooldown policy",
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
        "MT_PROXY_CONNECTION_PATTERN_STRICT" in handshake_scheduler_cpp
        and "3000 + mtProxySecureRandomBounded(3001)" in handshake_scheduler_cpp
        and "MT_PROXY_CONNECTION_PATTERN_QUIET" in handshake_scheduler_cpp
        and "1200 + mtProxySecureRandomBounded(1301)" in handshake_scheduler_cpp,
        "quiet and strict modes must slow sequential grants instead of only limiting concurrency",
    )
    require(
        "MT_PROXY_STARTUP_ENDPOINT_HANDSHAKES_COLD" in handshake_scheduler_cpp
        and "MT_PROXY_STARTUP_ENDPOINT_HANDSHAKES_USABLE" in handshake_scheduler_cpp
        and "mtProxyHandshakeEndpointHasRecentSuccess" in handshake_scheduler_cpp
        and "return MT_PROXY_STARTUP_ENDPOINT_HANDSHAKES_COLD;" in handshake_scheduler_cpp,
        "endpoint startup fanout must stay at one until recent usable success allows a second handshake",
    )
    require(
        "MT_PROXY_CONNECTION_PATTERN_BROWSER" in handshake_scheduler_cpp
        and "MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_BROWSER" in handshake_scheduler_cpp
        and "MT_PROXY_HANDSHAKE_BROWSER_HEAVY_DELAY_BASE_MS" in handshake_scheduler_cpp
        and "mode == MT_PROXY_CONNECTION_PATTERN_BROWSER" in handshake_scheduler_cpp
        and "mtProxyHandshakeEndpointHasRecentSuccess" in handshake_scheduler_cpp,
        "browser-like mode must start with one real handshake, then gently fan out after server_hello_hmac_ok",
    )
    require(
        "MT_PROXY_HANDSHAKE_QUIET_FREEZE_COOLDOWN_MAX_MS" in handshake_scheduler_cpp
        and "MT_PROXY_HANDSHAKE_STRICT_FREEZE_COOLDOWN_MAX_MS" in handshake_scheduler_cpp
        and "mtProxyClampCooldown" in handshake_scheduler_cpp
        and "mtProxyApplyFreezeCooldown(MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode)" in handshake_scheduler_cpp,
        "quiet/strict cooldown must be capped and mode-aware instead of growing to minute-scale waits",
    )
    require(
        "MT_PROXY_HANDSHAKE_FREEZE_COOLDOWN_ENABLED" not in socket_cpp,
        "stale global cooldown flag must not contradict mode-aware cooldown policy",
    )
    require(
        "MT_PROXY_HANDSHAKE_SUCCESS_COOLDOWN_RESET_MS" in handshake_scheduler_cpp
        and "state.cooldownUntil = 0;" in handshake_scheduler_cpp
        and "state.freezePenalty = 0;" in handshake_scheduler_cpp,
        "a successful server_hello_hmac_ok must quickly clear admission penalty",
    )
    require(
        "tcpFailurePenalty" in handshake_scheduler_cpp
        and "handshakeFailurePenalty" in handshake_scheduler_cpp
        and "mtProxyApplyTcpFailureCooldown(MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode)" in handshake_scheduler_cpp
        and "admission_tcp_failure_cooldown" in socket_cpp
        and "proxyHandshakeClientHelloSentTime <= 0" in socket_cpp,
        "pre-ClientHello TCP failures and post-ClientHello handshake failures must have separate cooldown markers instead of being mixed together",
    )
    require(
        "mtProxyCooldownBlocksPriority" in handshake_scheduler_cpp
        and "state.tcpFailurePenalty > 0" in handshake_scheduler_cpp
        and "state.handshakeFailurePenalty > 0" in handshake_scheduler_cpp
        and "return priority > MT_PROXY_HANDSHAKE_PRIORITY_BYPASS;" in handshake_scheduler_cpp
        and "mtProxyCooldownBlocksPriority(entry.second, now, mode, candidate.priority)" in handshake_scheduler_cpp
        and "mtProxyCooldownBlocksPriority(state, request.now, mode, request.priority)" in handshake_scheduler_cpp,
        "TCP and post-ClientHello cooldowns must throttle generic/media reconnect storms, not only low-priority download/upload attempts",
    )
    require(
        "if ((int64_t) delay < cooldownDelay)" in handshake_scheduler_cpp
        and "delay = (uint32_t) cooldownDelay;" in handshake_scheduler_cpp,
        "queued admission retry timers must wait until cooldown expires instead of waking repeatedly inside cooldown",
    )
    require(
        "bool suppressQueuedGrant = !succeeded && wasActive" in socket_cpp
        and "!isNeutralSchedulerWaitRelease" in socket_cpp
        and "proxyHandshakeClientHelloSentTime > 0" in socket_cpp
        and "admission_hold_after_client_hello_failure" in socket_cpp
        and "releaseRequest.suppressQueuedGrant = suppressQueuedGrant" in socket_cpp
        and "if (!request.suppressQueuedGrant && mtProxyHandshakeSchedulerUsesAdmission(mode))" in handshake_scheduler_cpp,
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
        and "releaseRequest.hadAdmission = hadAdmission;" in socket_cpp
        and "if (!request.suppressQueuedGrant && mtProxyHandshakeSchedulerUsesAdmission(mode))" in handshake_scheduler_cpp,
        "admission release must be idempotent so post-handshake close/suspend cannot dequeue a second queued request",
    )
    classification = text(NATIVE_PHASE_CLASSIFICATION)
    backoff_start = classification.find("inline bool needsReconnectBackoff")
    reconnect_backoff = classification[
        backoff_start:classification.find("\n}", backoff_start)
    ]
    expected_reconnect_backoff = reconnect_backoff_phases()
    require(
        "mtProxyDiagnosticNeedsReconnectBackoff" in connection_cpp
        and "MtProxyPhase::needsReconnectBackoff(diagnostic)" in connection_cpp
        and all(has_phase(reconnect_backoff, phase, phase_constants) for phase in expected_reconnect_backoff)
        and "mtproxy_startup reconnect_backoff" in connection_cpp
        and "mtproxy_startup reconnect_hold" in connection_cpp
        and "getProxyCheckDiagnostic()" in connection_cpp
        and "connectionState == TcpConnectionStageIdle && connectionType != ConnectionTypeProxy && !isProxyCloseDiagnosticSuppressed() && mtProxyDiagnosticNeedsReconnectBackoff" in connection_cpp
        and "reconnect_backoff_suppressed" in connection_cpp
        and "mtProxyReconnectBackoffMs" in connection_h
        and "mtProxyReconnectHoldUntil" in connection_h,
        "Connection layer must back off real MTProxy failures via the generated phase classification",
    )
    require(
        "network_block_suspected" not in reconnect_backoff,
        "network_block_suspected is a Java scheduler display/escalation phase and must not be a native reconnect diagnostic",
    )
    retry_authority_cpp = text(RETRY_AUTHORITY_CPP)
    require(
        "MtProxyRetry::nextReconnectHold(holdInput)" in connection_cpp
        and "holdDecision.nextBackoffMs" in connection_cpp
        and "mtProxyTrafficClassFor(connectionType)" in connection_cpp,
        "Connection must delegate reconnect hold computation to MtProxyRetryAuthority (single hold owner)",
    )
    require(
        "mtProxyNextReconnectBackoffMs" not in connection_cpp
        and "mtProxyReconnectBackoffBaseMs" not in connection_cpp
        and "mtProxyReconnectBackoffMaxMs" not in connection_cpp
        and "mtProxyHandshakeSchedulerRetryDelay" not in socket_cpp
        and "MtProxyRetry::endpointCooldownWaitMs" in socket_cpp,
        "retry-hold computation must not re-grow outside MtProxyRetryAuthority: no local backoff math in Connection, no direct scheduler-delay merge in ConnectionSocket",
    )
    reconnect_base = retry_authority_cpp[
        retry_authority_cpp.find("uint32_t backoffBaseMs"):
        retry_authority_cpp.find("uint32_t backoffMaxMs")
    ]
    reconnect_max = retry_authority_cpp[
        retry_authority_cpp.find("uint32_t backoffMaxMs"):
        retry_authority_cpp.find("static uint32_t reconnectJitterMs")
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
