#!/usr/bin/env python3
from pathlib import Path
import re
import sys

from mtproxy_phase_contract import analyzer_failure_phases, java_phase_names


ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "diagnostics": ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckDiagnostics.java",
    "policy": ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyPhasePolicy.java",
    "scheduler": ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckScheduler.java",
    "shared": ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/SharedConfig.java",
    "proxy_list": ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxyListActivity.java",
    "request_time": ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/RequestTimeDelegate.java",
    "tgnet_wrapper": ROOT / "TMessagesProj/jni/TgNetWrapper.cpp",
    "defines": ROOT / "TMessagesProj/jni/tgnet/Defines.h",
    "connections": ROOT / "TMessagesProj/jni/tgnet/ConnectionsManager.cpp",
    "socket_header": ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.h",
    "socket": ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp",
    "values": ROOT / "TMessagesProj/src/main/res/values/strings.xml",
    "values_ru": ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml",
    "analyzer": ROOT / "Tools/analyze_mtproxy_markers.py",
}


def text(name):
    path = FILES[name]
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition, message):
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def java_diagnostic_values(diagnostics):
    return set(re.findall(r'public static final String \w+\s*=\s*"([a-z0-9_]+)"', diagnostics))


def native_published_phases(socket):
    published = set(re.findall(r'publishProxyConnectionStage\("([a-z0-9_]+)"\)', socket))
    terminal = set(re.findall(r'proxyCheckDiagnostic\s*=\s*"([a-z0-9_]+)"', socket))
    # WSS has its own transport diagnostics and is not part of the MTProxy phase map.
    terminal.discard("wss_tls_handshake")
    terminal.discard("wss_appdata_no_response_timeout")
    return published | terminal


def main():
    diagnostics = text("diagnostics")
    combined = "\n".join(text(name) for name in FILES)
    java_values = java_diagnostic_values(diagnostics)
    native_values = native_published_phases(text("socket"))

    require(diagnostics, "ProxyCheckDiagnostics.java must be the single Java source of truth for proxy-check phases")
    for phase in sorted(java_phase_names()):
        require(phase in diagnostics, f"ProxyCheckDiagnostics must define phase '{phase}'")
        require(phase in combined, f"phase '{phase}' must be used outside the diagnostics map")
    require(
        native_values <= java_values,
        "native MTProxy phases missing from ProxyCheckDiagnostics: " + ", ".join(sorted(native_values - java_values)),
    )

    require(
        "void run(long time, String diagnostic)" in text("request_time"),
        "RequestTimeDelegate must pass a string diagnostic next to the ping time",
    )
    require(
        'GetMethodID(jclass_RequestTimeDelegate, "run", "(JLjava/lang/String;)V")' in text("tgnet_wrapper"),
        "JNI bridge must call RequestTimeDelegate.run(long, String)",
    )
    require(
        "typedef std::function<void(int64_t requestTime, const std::string &diagnostic)> onRequestTimeFunc" in text("defines"),
        "native proxy-check callback must carry a string diagnostic, not a magic number",
    )
    require(
        "lastCheckDiagnostic" in text("shared") and "lastCheckDiagnosticTime" in text("shared"),
        "ProxyInfo must remember the last diagnostic phase and timestamp for GUI rendering",
    )
    require(
        "ProxyCheckDiagnostics.statusText" in text("proxy_list"),
        "Proxy list UI must render status through ProxyCheckDiagnostics",
    )
    require(
        "phase=" in text("scheduler") and "diagnostic=" in text("scheduler"),
        "ProxyCheckScheduler logs must include stable string phase/diagnostic fields",
    )
    require(
        "getProxyCheckDiagnostic()" in text("socket_header") and "proxyCheckDiagnostic" in text("socket"),
        "ConnectionSocket must expose the active MTProxy/FakeTLS diagnostic phase",
    )
    require(
        "proxyCheckDiagnosticForClose" in text("connections"),
        "ConnectionsManager must classify proxy-check close failures from native phase evidence",
    )
    require(
        "ProxyStatusTcpConnectedNoPong" in text("values") and "TCP открылся" in text("values_ru"),
        "localized GUI strings must describe tcp_connected_no_pong clearly",
    )
    live_phase = text("policy")
    require(
        "case ProxyCheckDiagnostics.WAITING_TCP:" in live_phase,
        "waiting_tcp is a live waiting state and must not be classified as a failure",
    )
    analyzer = text("analyzer")
    for phase in sorted(analyzer_failure_phases()):
        require(phase in analyzer, f"log analyzer must know contract failure phase '{phase}'")
    failure_verdicts_match = re.search(r"FAKETLS_FAILURE_VERDICTS = \{(?P<body>.*?)\n\}", analyzer, re.S)
    require(failure_verdicts_match is not None, "analyzer must expose explicit FakeTLS failure verdicts")
    analyzer_failure_verdicts = set(re.findall(r'"([a-z0-9_]+)"', failure_verdicts_match.group("body")))
    legacy_analyzer_aliases = {"unsupported_for_current_client"}
    require(
        analyzer_failure_verdicts - legacy_analyzer_aliases == analyzer_failure_phases(),
        "analyzer FakeTLS failure verdicts must match mtproxy_phase_contract except legacy aliases: " + ", ".join(sorted((analyzer_failure_verdicts - legacy_analyzer_aliases) ^ analyzer_failure_phases())),
    )
    require(
        legacy_analyzer_aliases <= analyzer_failure_verdicts and "legacy alias" in analyzer,
        "analyzer must document legacy FakeTLS failure aliases separately from the active phase contract",
    )
    require("-1001" not in combined and "-1002" not in combined, "diagnostics must not use magic negative IDs")
    require(
        "post_handshake_no_server_appdata" not in combined,
        "diagnostics must use the shared post_handshake_no_appdata phase name everywhere",
    )
    require(
        "peer_closed_after_client_hello" not in text("socket")
        and "peer_closed_after_client_hello" not in text("analyzer"),
        "diagnostics must use client_hello_sent_no_server_hello instead of the stale peer_closed_after_client_hello phase",
    )

    print("Proxy check diagnostic map guard passed.")


if __name__ == "__main__":
    main()
