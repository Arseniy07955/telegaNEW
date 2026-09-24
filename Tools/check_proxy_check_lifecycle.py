#!/usr/bin/env python3
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionsManager.cpp"
HDR = ROOT / "TMessagesProj/jni/tgnet/ConnectionsManager.h"
INFO = ROOT / "TMessagesProj/jni/tgnet/ProxyCheckInfo.h"
COLLECT = ROOT / "Tools/collect_mtproxy_logs.ps1"
ANALYZE = ROOT / "Tools/analyze_mtproxy_markers.py"
WRAPPER = ROOT / "TMessagesProj/jni/TgNetWrapper.cpp"
JAVA_MANAGER = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
# В форке технический README ZaStoGram перенесён в docs/, корневой README — о форке.
README = ROOT / "docs" / "ZaStoGram.md"


def require(condition, message):
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def main():
    cpp = CPP.read_text(encoding="utf-8")
    hdr = HDR.read_text(encoding="utf-8")
    info = INFO.read_text(encoding="utf-8")
    collect = COLLECT.read_text(encoding="utf-8")
    analyze = ANALYZE.read_text(encoding="utf-8")
    wrapper = WRAPPER.read_text(encoding="utf-8")
    java_manager = JAVA_MANAGER.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    require(
        "finishProxyCheck(" in hdr and "scheduleNextProxyCheck(" in hdr,
        "ConnectionsManager must declare a single proxy-check finish path and next-queue helper",
    )
    require(
        "cancelProxyCheck(" in hdr,
        "ConnectionsManager must expose native proxy-check cancellation by ping id",
    )
    require(
        "eraseProxyCheckRequest(" in hdr,
        "ConnectionsManager must declare request-token cleanup for native proxy checks",
    )
    require(
        "isProxyCheckRequestActive(" in hdr,
        "ConnectionsManager must expose a proxy-check request liveness predicate for stale resend filtering",
    )
    require(
        "enum class ProxyCheckState" in info
        and "Queued" in info
        and "Connecting" in info
        and "PingSent" in info
        and "Finished" in info,
        "ProxyCheckInfo must carry an explicit state machine",
    )
    require(
        "bool finished = false" in info
        and "int64_t startedAtMillis = 0" in info
        and "uint32_t connectionToken = 0" in info,
        "ProxyCheckInfo must own finish, timing, and socket generation state",
    )
    require(
        "void ConnectionsManager::finishProxyCheck(" in cpp
        and "void ConnectionsManager::scheduleNextProxyCheck(" in cpp,
        "ConnectionsManager.cpp must define proxy-check lifecycle helpers",
    )
    require(
        "failProxyCheckStart(" in hdr and "void ConnectionsManager::failProxyCheckStart(" in cpp,
        "ConnectionsManager must have a terminal fail-start path for proxy checks that never became active",
    )
    require(
        "bool ConnectionsManager::eraseProxyCheckRequest(" in cpp,
        "ConnectionsManager.cpp must define request-token cleanup for native proxy checks",
    )
    require(
        "bool ConnectionsManager::isProxyCheckRequestActive(" in cpp,
        "ConnectionsManager.cpp must define stale proxy-check request detection",
    )
    require(
        "void ConnectionsManager::cancelProxyCheck(" in cpp
        and 'reason=cancelled' in cpp,
        "ConnectionsManager.cpp must implement explicit proxy-check cancellation without pretending it is a network failure",
    )
    require(
        'proxy_check_finish result=' in cpp and 'proxy_check_next queued=' in cpp,
        "proxy-check lifecycle must be visible in native logs",
    )
    require(
        "if (type == HandshakeTypeTemp && !proxyCheckQueue.empty()) {\n        scheduleNextProxyCheck();\n    }" in cpp,
        "temp-handshake completion must start queued proxy checks through the shared queue helper",
    )
    require(
        'proxy_check_request_erase' in cpp,
        "proxy-check request cleanup must be visible in native logs",
    )
    require(
        'proxy_check_cancel' in cpp,
        "explicit proxy-check cancellation must be visible in native logs",
    )
    require(
        "native_cancelProxyCheck" in wrapper
        and "cancelProxyCheck(JNIEnv" in wrapper,
        "JNI wrapper must register native proxy-check cancellation",
    )
    require(
        "public void cancelProxyCheck(long pingId)" in java_manager
        and "native_cancelProxyCheck(currentAccount, pingId)" in java_manager,
        "Java ConnectionsManager must expose proxy-check cancellation",
    )
    require(
        'proxy_check_start state=' in cpp
        and 'proxy_check_socket_connected' in cpp
        and 'proxy_check_connection_closed close_reason=' in cpp
        and 'proxy_check_connection_closed_ignored close_reason=' in cpp
        and 'proxy_check_request_stale' in cpp,
        "proxy-check state transitions, close reasons, ignored closes, and stale request drops must be visible in native logs",
    )
    require(
        'proxy_check_start_failed' in cpp
        and 'missing_datacenter' in cpp
        and 'connection_unavailable' in cpp,
        "proxy-check start failures must be logged and terminal instead of silently leaking state",
    )
    require(
        "request_missing" in cpp,
        "closed proxy-check must log when the original running request is already gone",
    )
    require(
        re.search(r"finishProxyCheck\(iter,\s*-1,\s*\"connection_closed\",\s*proxyCheckDiagnosticForClose\(proxyCheckInfo,\s*connection\),\s*connection,\s*true\)", cpp),
        "ConnectionTypeProxy close must finish active check as -1 even if request lookup fails",
    )
    require(
        "proxyCheckInfo->onRequestTime(-1)" not in cpp,
        "failure callback must go through finishProxyCheck instead of ad-hoc close-only handling",
    )
    require(
        cpp.count("proxyActiveChecks.erase(iter)") == 1,
        "active proxy-check erase must be centralized to avoid divergent lifecycle paths",
    )
    require(
        'finishProxyCheck(iter, ping, "pong", "ok", connection, true)' in cpp,
        "TL_pong success path must be successful by matching ping_id, not by finding the backing Request",
    )
    require(
        'finishProxyCheck(iter, -1, "cancelled", "cancelled", connection, false)' in cpp,
        "explicit cancellation must finish the native state without notifying Java as a failed network check",
    )
    require(
        '!notifyCallback ? "cancelled"' in cpp,
        "cancelled proxy checks must be logged as cancelled, not as network failures",
    )
    require(
        "connection->getConnectionToken() == request->connectionToken" not in cpp,
        "proxy-check close cleanup must not depend on connectionToken because failed checks can be resent with a different token",
    )
    require(
        "scheduleTask([&, connection, reason]" not in cpp,
        "ConnectionTypeProxy close cleanup must run immediately so stale pings cannot reconnect before cleanup",
    )
    require(
        "eraseProxyCheckRequest(proxyCheckInfo->requestToken" in cpp,
        "the finish path must remove the backing TL_ping by proxy check requestToken",
    )

    finish_match = re.search(
        r"void ConnectionsManager::finishProxyCheck\([^)]*\) \{(?P<body>.*?)\n\}",
        cpp,
        re.S,
    )
    require(finish_match, "finishProxyCheck body must be parseable")
    finish_body = finish_match.group("body")
    require(
        "proxyCheckInfo->finished = true;" in finish_body
        and "proxyCheckInfo->state = ProxyCheckState::Finished;" in finish_body,
        "finishProxyCheck must mark the native state as terminal before cleanup",
    )
    require(
        "auto callback = proxyCheckInfo->onRequestTime;" in finish_body
        and "proxyActiveChecks.erase(iter);" in finish_body
        and "callback(time, diagnostic == nullptr ? \"unknown_fail\" : diagnostic);" in finish_body
        and finish_body.index("proxyActiveChecks.erase(iter);") < finish_body.index("callback(time, diagnostic == nullptr ? \"unknown_fail\" : diagnostic);"),
        "finishProxyCheck must remove native active state before notifying Java",
    )
    require(
        "connection->suspendConnection(false);" in finish_body
        and finish_body.index("proxyActiveChecks.erase(iter);") < finish_body.index("connection->suspendConnection(false);"),
        "finishProxyCheck must detach the proxy-check socket after active state is gone",
    )
    require(
        "scheduleNextProxyCheck();" in finish_body
        and finish_body.index("connection->suspendConnection(false);") < finish_body.index("scheduleNextProxyCheck();"),
        "finishProxyCheck must detach the old proxy-check socket before starting the next queued check",
    )
    require(
        "proxyCheckInfo->ptr1 = nullptr;" in finish_body
        and "DeleteGlobalRef(requestTimeRef);" in finish_body
        and finish_body.index("callback(time, diagnostic == nullptr ? \"unknown_fail\" : diagnostic);") < finish_body.index("DeleteGlobalRef(requestTimeRef);"),
        "finishProxyCheck must keep the JNI callback ref alive until after Java notification",
    )
    require(
        "isProxyCheckRequestActive(request->requestToken)" in cpp,
        "processRequestQueue must drop stale ConnectionTypeProxy requests before send/resend",
    )
    require(
        "delete request;\n        return request->requestToken;" not in cpp,
        "sendRequestInternal must not read request fields after deleting a cancelled request",
    )
    require(
        "delete request;\n        }\n        if (!currentUserId" not in cpp,
        "scheduled sendRequest must return immediately after deleting a pre-cancelled request",
    )
    require(
        "int64_t pingId = proxyCheckInfo->pingId;" in cpp
        and "return pingId;" in cpp
        and "return proxyCheckInfo->pingId;" not in cpp,
        "checkProxy must return a stable ping id after handing ProxyCheckInfo to the async native lifecycle",
    )
    require(
        "proxy_check_" in collect and "proxy_check_scheduler" in collect and "proxy_rotation" in collect,
        "MTProxy log collector must include native, Java, and rotation proxy markers",
    )
    require(
        "Proxy-check lifecycle:" in analyze
        and "PROXY_CHECK_SCHEDULER_RE" in analyze
        and "PROXY_CHECK_RESULT_RE" in analyze,
        "MTProxy analyzer must summarize proxy-check lifecycle markers",
    )
    require(
        "PROXY_CHECK_START_FAILED_RE" in analyze
        and "Native start failures:" in analyze,
        "MTProxy analyzer must summarize proxy-check start failures separately from normal finish results",
    )
    require(
        "PROXY_CHECK_CLOSE_RE" in analyze
        and "Native close reasons:" in analyze,
        "MTProxy analyzer must summarize proxy-check socket close reasons separately from finish results",
    )
    require(
        "PROXY_CHECK_IGNORED_CLOSE_RE" in analyze
        and "Native ignored close reasons:" in analyze,
        "MTProxy analyzer must summarize ignored proxy-check closes separately from terminal close reasons",
    )
    require(
        "PROXY_ROTATION_RE" in analyze and "Rotation events:" in analyze,
        "MTProxy analyzer must summarize proxy rotation lifecycle markers",
    )
    require(
        "SCHEDULER_LISTENERS_RE" in analyze
        and "SCHEDULER_FORCE_RE" in analyze
        and "SCHEDULER_RESULT_RE" in analyze
        and "SCHEDULER_APPLIED_RE" in analyze
        and "Scheduler coalescing:" in analyze
        and "Scheduler listener peaks:" in analyze,
        "MTProxy analyzer must summarize Java scheduler coalescing, listener fan-in, and applied/callback result split",
    )
    require(
        "Scheduler finish results:" in analyze
        and "Scheduler preserved connected state:" in analyze
        and "Scheduler applied/callback split:" in analyze,
        "MTProxy analyzer must make preserved connected-state checks visible in the proxy-check summary",
    )
    require(
        "### Архитектура проверки прокси" in readme
        and "ProxyCheckScheduler" in readme
        and "finishProxyCheck" in readme
        and "Tools/analyze_mtproxy_markers.py" in readme
        and "connected_without_socket_connected_marker" in readme,
        "README must document the Java/native proxy-check lifecycle and analyzer verdicts",
    )

    print("proxy check native lifecycle guard OK")


if __name__ == "__main__":
    main()
