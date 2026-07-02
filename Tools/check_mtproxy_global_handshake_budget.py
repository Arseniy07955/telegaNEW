#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOCKET = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
SCHEDULER = ROOT / "TMessagesProj/jni/mtproxy/MtProxyHandshakeScheduler.cpp"
SCHEDULER_HEADER = ROOT / "TMessagesProj/jni/mtproxy/MtProxyHandshakeScheduler.h"
CONNECTION = ROOT / "TMessagesProj/jni/tgnet/Connection.cpp"
DEFINES = ROOT / "TMessagesProj/jni/tgnet/Defines.h"
OPTIONS = ROOT / "TMessagesProj/jni/mtproxy/MtProxyOptions.h"
CMAKE = ROOT / "TMessagesProj/jni/CMakeLists.txt"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        raise SystemExit(1)


def slice_between(source: str, start: str, end: str) -> str:
    start_idx = source.find(start)
    require(start_idx >= 0, f"missing start marker: {start}")
    end_idx = source.find(end, start_idx)
    require(end_idx >= 0, f"missing end marker after {start}: {end}")
    return source[start_idx:end_idx]


def slice_from(source: str, start: str) -> str:
    start_idx = source.find(start)
    require(start_idx >= 0, f"missing start marker: {start}")
    return source[start_idx:]


def main() -> None:
    socket = read(SOCKET)
    scheduler = read(SCHEDULER)
    scheduler_header = read(SCHEDULER_HEADER)
    connection = read(CONNECTION)
    defines = read(DEFINES)
    options = read(OPTIONS)
    cmake = read(CMAKE)
    scheduler_helpers = slice_between(
        scheduler,
        "static pthread_mutex_t proxyHandshakeSchedulerMutex",
        "MtProxyHandshakeAdmissionDecision mtProxyHandshakeSchedulerAdmit",
    )
    scheduler_admission = slice_between(
        scheduler,
        "MtProxyHandshakeAdmissionDecision mtProxyHandshakeSchedulerAdmit",
        "MtProxyHandshakeReleaseDecision mtProxyHandshakeSchedulerRelease",
    )
    scheduler_release = slice_from(
        scheduler,
        "MtProxyHandshakeReleaseDecision mtProxyHandshakeSchedulerRelease",
    )
    socket_admission = slice_between(
        socket,
        "bool ConnectionSocket::scheduleProxyHandshakeAdmissionIfNeeded",
        "void ConnectionSocket::scheduleProxyHandshakeAdmissionTimer",
    )
    socket_release = slice_between(
        socket,
        "void ConnectionSocket::releaseProxyHandshakeAdmission",
        "bool ConnectionSocket::scheduleMtProxyEndpointCircuitBreakerIfNeeded",
    )

    require(
        "mtproxy/MtProxyHandshakeScheduler.cpp" in cmake,
        "native build must compile MtProxyHandshakeScheduler.cpp",
    )
    require(
        "enum class MtProxyRequestClass : uint8_t" in scheduler_header
        and "Generic" in scheduler_header
        and "Media" in scheduler_header
        and "Push" in scheduler_header
        and "Download" in scheduler_header
        and "Upload" in scheduler_header
        and "ProxyCheck" in scheduler_header
        and "mtProxyHandshakePriorityForRequestClass" in scheduler_header
        and "mtProxyRequestClassForPriority" in scheduler_header
        and "mtProxyRequestClassName" in scheduler_header,
        "scheduler API must name MTProxy request classes instead of exposing only raw priority integers",
    )
    require(
        "MtProxyHandshakeAdmissionRequest" in scheduler_header
        and "ConnectionSocket *socket = nullptr;" in scheduler_header
        and "std::string key;" in scheduler_header
        and "uint32_t generation = 0;" in scheduler_header
        and "MtProxyRequestClass requestClass = MtProxyRequestClass::Generic;" in scheduler_header
        and "int32_t priority = 0;" in scheduler_header
        and "int32_t timerMode = 0;" in scheduler_header
        and "int32_t connectionPatternMode = 0;" in scheduler_header
        and "bool ipv6 = false;" in scheduler_header
        and "int64_t now = 0;" in scheduler_header
        and "MtProxyHandshakeAdmissionDecision" in scheduler_header
        and "bool queued = false;" in scheduler_header
        and "bool granted = false;" in scheduler_header
        and "bool publishQueue = false;" in scheduler_header
        and "uint32_t delayMs = 0;" in scheduler_header
        and "int32_t endpointActive = 0;" in scheduler_header
        and "int32_t endpointLimit = 0;" in scheduler_header
        and "int32_t globalActive = 0;" in scheduler_header
        and "int32_t globalLimit = 0;" in scheduler_header
        and "int64_t cooldownRemainingMs = 0;" in scheduler_header,
        "scheduler facade must expose typed admission request/decision structs",
    )
    require(
        "proxyHandshakeSchedulerMutex" not in socket
        and "proxyHandshakeEndpoints" not in socket
        and "proxyHandshakeGlobal" not in socket
        and "struct MtProxyHandshakeQueuedRequest" not in socket
        and "struct MtProxyHandshakeEndpointState" not in socket
        and "mtProxyHandshakeActiveLimit" not in socket
        and "mtProxyHandshakeGlobalActiveLimit" not in socket
        and "mtProxyTakeNextQueuedRequestGlobalLocked" not in socket
        and "mtProxyRecordHandshakeSuccess" not in socket,
        "ConnectionSocket must not own global handshake scheduler state or policy helpers",
    )
    require(
        "MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_SOFT = 2" in options
        and "MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_BROWSER = 2" in options
        and "MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_QUIET = 1" in options
        and "MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_STRICT = 1" in options
        and "MT_PROXY_STARTUP_ENDPOINT_HANDSHAKES_COLD = 1" in options
        and "MT_PROXY_STARTUP_ENDPOINT_HANDSHAKES_USABLE = 2" in options,
        "startup handshake fanout limits must live in mtproxy/MtProxyOptions.h (module-owned), separate from established connection counts",
    )
    require(
        "MT_PROXY_STARTUP_GLOBAL_HANDSHAKES" not in defines
        and "MT_PROXY_STARTUP_ENDPOINT_HANDSHAKES" not in defines,
        "Defines.h must not re-grow handshake fanout limits; they are owned by mtproxy/MtProxyOptions.h",
    )
    require(
        "struct MtProxyHandshakeGlobalState" in scheduler_helpers
        and "activeHandshakes" in scheduler_helpers
        and "lastGrantTime" in scheduler_helpers
        and "static MtProxyHandshakeGlobalState proxyHandshakeGlobal" in scheduler_helpers,
        "scheduler must keep app-wide handshake state shared across account instances",
    )
    require(
        "mtProxyHandshakeGlobalActiveLimit" in scheduler_helpers
        and "MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_SOFT" in scheduler_helpers
        and "MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_BROWSER" in scheduler_helpers
        and "MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_QUIET" in scheduler_helpers
        and "MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_STRICT" in scheduler_helpers,
        "global active limit helper must be mode-aware",
    )
    require(
        "mtProxyHandshakeEndpointHasRecentSuccess" in scheduler_helpers
        and "return MT_PROXY_STARTUP_ENDPOINT_HANDSHAKES_COLD;" in scheduler_helpers
        and "return MT_PROXY_STARTUP_ENDPOINT_HANDSHAKES_USABLE;" in scheduler_helpers
        and "MT_PROXY_HANDSHAKE_SOFT_ACTIVE_LIMIT" not in scheduler,
        "endpoint active limit must be 1 until recent usable success explicitly allows 2",
    )
    require(
        "activeGenericPushHandshakes" in scheduler_helpers
        and "mtProxyHandshakeIsGenericOrPush" in scheduler_helpers
        and "mtProxyHandshakeIsHeavyRequestClass" in scheduler_helpers
        and "mtProxyHandshakeHeavyBlockedBeforeUsable" in scheduler_helpers
        and "activeGenericPushHandshakes > 0" in scheduler_helpers
        and "mtProxyHandshakeHasGenericOrPushQueuedGlobal" in scheduler_helpers,
        "heavy request classes must wait behind generic/push while the endpoint is not yet usable",
    )
    require(
        "mtProxyHandshakeGlobalSpacingDelay" in scheduler_helpers
        and "proxyHandshakeGlobal.lastGrantTime" in scheduler_helpers
        and "mtProxyRecordGlobalHandshakeGrant" in scheduler_helpers,
        "global budget must space grants across endpoints and accounts",
    )
    require(
        "mtProxyHandshakeHasHigherPriorityQueuedGlobal" in scheduler_helpers
        and "for (const auto &entry : proxyHandshakeEndpoints)" in scheduler_helpers
        and "request.priority < priority" in scheduler_helpers,
        "priority checks must see queued handshakes from every endpoint/account",
    )
    require(
        "mtProxyTakeNextQueuedRequestGlobalLocked" in scheduler_helpers
        and "proxyHandshakeGlobal.activeHandshakes >= globalLimit" in scheduler_helpers
        and "entry.second.activeHandshakes >= endpointLimit" in scheduler_helpers
        and "proxyHandshakeGlobal.activeHandshakes++" in scheduler_helpers,
        "release must dequeue the next best request globally, not only from the same endpoint",
    )
    require(
        "globalLimit = mtProxyHandshakeGlobalActiveLimit" in scheduler_admission
        and "globalLimitReached = proxyHandshakeGlobal.activeHandshakes >= globalLimit" in scheduler_admission
        and "globalLimitReached || cooldownBlocks || state.activeHandshakes >= endpointLimit" in scheduler_admission,
        "scheduler admission must queue when the app-wide handshake budget is full",
    )
    require(
        "mtProxyHandshakeHasHigherPriorityQueuedGlobal(request.priority)" in scheduler_admission
        and "proxyHandshakeGlobal.activeHandshakes++" in scheduler_admission
        and "mtProxyRecordGlobalHandshakeGrant(request.now, decision.delayMs)" in scheduler_admission,
        "scheduler immediate grants must reserve and record an app-wide slot",
    )
    require(
        "request.requestClass = mtProxyRequestClassForPriority(proxyHandshakeAdmissionPriority);" in socket_admission
        and "request_class=%s" in socket_admission
        and "MtProxyRequestClass requestClass = request.requestClass;" in scheduler_admission
        and "mtProxyRequestClassForConnectionType" in connection
        and "mtProxyHandshakePriorityForRequestClass(mtProxyRequestClassForConnectionType(connectionType))" in connection,
        "Connection.cpp and ConnectionSocket must feed typed request class into scheduler admission while preserving priority ordering",
    )
    require(
        "mtProxyHandshakeSchedulerAdmit(request)" in socket_admission
        and "decision.queued" in socket_admission
        and "decision.granted" in socket_admission
        and "global_active=%d" in socket_admission
        and "global_limit=%d" in socket_admission
        and "mtProxyHandshakeSchedulerRelease(releaseRequest)" in socket_release
        and "global_active=%d" in socket_release
        and "global_limit=%d" in socket_release,
        "startup diagnostics must expose global active/limit budget decisions",
    )
    require(
        "if (request.wasActive && proxyHandshakeGlobal.activeHandshakes > 0)" in scheduler_release
        and "proxyHandshakeGlobal.activeHandshakes--" in scheduler_release,
        "scheduler release must return an app-wide slot exactly once",
    )
    require(
        "mtProxyTakeNextQueuedRequestGlobalLocked(request.now, mode, decision.nextRequest)" in scheduler_release
        and "MtProxyHandshakeEndpointState &nextState = proxyHandshakeEndpoints[decision.nextRequest.key]" in scheduler_release
        and "mtProxyRecordGlobalHandshakeGrant(request.now, decision.nextRequest.delayMs)" in scheduler_release
        and "admission_dequeue_global" in socket_release
        and "grantProxyHandshakeAdmission" in socket_release,
        "scheduler release must grant the next queued handshake across all accounts/endpoints",
    )

    print("MTProxy global handshake budget guard passed.")


if __name__ == "__main__":
    main()
