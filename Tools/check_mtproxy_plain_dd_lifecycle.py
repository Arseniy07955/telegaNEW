#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONNECTION_CPP = ROOT / "TMessagesProj/jni/tgnet/Connection.cpp"
CLASSIFICATION_H = ROOT / "TMessagesProj/jni/mtproxy/MtProxyPhaseClassification.h"
SOCKET_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
DIAGNOSTICS = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckDiagnostics.java"
STRINGS = ROOT / "TMessagesProj/src/main/res/values/strings.xml"
STRINGS_RU = ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml"
ANALYZER = ROOT / "Tools/analyze_mtproxy_markers.py"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def slice_between(text: str, start: str, end: str) -> str:
    start_idx = text.find(start)
    require(start_idx >= 0, f"missing start marker {start!r}")
    end_idx = text.find(end, start_idx)
    require(end_idx >= 0, f"missing end marker {end!r}")
    return text[start_idx:end_idx]


def main() -> None:
    connection_cpp = CONNECTION_CPP.read_text(encoding="utf-8")
    socket_cpp = SOCKET_CPP.read_text(encoding="utf-8")
    diagnostics = DIAGNOSTICS.read_text(encoding="utf-8")
    strings = STRINGS.read_text(encoding="utf-8")
    strings_ru = STRINGS_RU.read_text(encoding="utf-8")
    analyzer = ANALYZER.read_text(encoding="utf-8")

    classification_h = CLASSIFICATION_H.read_text(encoding="utf-8")
    backoff_body = slice_between(classification_h, "inline bool needsReconnectBackoff", "inline bool isObservationFacadePhase")
    require(
        "MtProxyPhase::needsReconnectBackoff(diagnostic)" in connection_cpp
        and 'strcmp(phase, "mtproxy_packet_sent_no_response") == 0' in backoff_body,
        "dd/plain MTProxy no-response failures must use reconnect backoff",
    )
    require(
        'strcmp(phase, "tcp_connected_no_pong") == 0' in backoff_body,
        "post-TCP MTProxy no-response failures must use reconnect backoff",
    )

    on_connected = slice_between(connection_cpp, "void Connection::onConnected()", "bool Connection::hasPendingRequests()")
    require(
        "mtProxyReconnectBackoffMs = 0" not in on_connected
        and "mtProxyReconnectHoldUntil = 0" not in on_connected,
        "TCP connect must not reset MTProxy reconnect backoff before useful incoming data",
    )

    on_received = slice_between(connection_cpp, "void Connection::onReceivedData", "bool Connection::hasUsefullData")
    require(
        "mtProxyReconnectBackoffMs = 0" in on_received
        and "mtProxyReconnectHoldUntil = 0" in on_received,
        "MTProxy reconnect backoff should reset after incoming MTProto data",
    )

    mark_sent = slice_between(socket_cpp, "void ConnectionSocket::markMtProxyFirstPlainDataSent", "void ConnectionSocket::markMtProxyFirstPlainDataReceived")
    require(
        'proxyCheckDiagnostic = "mtproxy_packet_sent_no_response"' in mark_sent,
        "dd/plain MTProxy should keep a specific no-response diagnostic after first packet send",
    )
    require(
        'publishProxyConnectionStage("first_mtproxy_packet_sent")' in mark_sent,
        "dd/plain MTProxy should still expose first packet send as a live stage",
    )
    require(
        "mtproxyFirstPlainDataSentTime" in mark_sent
        and "getCurrentTimeMonotonicMillis()" in mark_sent,
        "dd/plain MTProxy should timestamp the first packet for a phase-specific timeout",
    )

    timeout_block = slice_between(socket_cpp, "bool ConnectionSocket::checkTimeout", "bool ConnectionSocket::hasTlsHashMismatch")
    require(
        "MT_PROXY_PLAIN_NO_RESPONSE_TIMEOUT_MS" in socket_cpp,
        "dd/plain MTProxy no-response timeout must be explicit",
    )
    require(
        "mtproxyFirstPlainDataSentLogged" in timeout_block
        and "!mtproxyFirstPlainDataReceivedLogged" in timeout_block
        and "!currentSecretIsFakeTls" in timeout_block
        and 'proxyCheckDiagnostic == "mtproxy_packet_sent_no_response"' in timeout_block,
        "dd/plain MTProxy should close early only after first packet send and before first reply",
    )
    require(
        "mtproxy_packet_no_response_timeout" in timeout_block
        and "closeSocket(2, 0)" in timeout_block,
        "dd/plain MTProxy no-response timeout should be logged and close as timeout",
    )

    for text, name in ((diagnostics, "ProxyCheckDiagnostics"), (analyzer, "analyzer")):
        require(
            "mtproxy_packet_sent_no_response" in text,
            f"{name} must know mtproxy_packet_sent_no_response",
        )
    require(
        '"mtproxy_packet_no_response_timeout": "mtproxy_packet_sent_no_response"' in analyzer,
        "analyzer must fold the native timeout marker into the shared dd no-response phase",
    )
    require(
        "MTPROXY_PACKET_SENT_NO_RESPONSE" in diagnostics
        and "ProxyStatusMtproxyPacketSentNoResponse" in diagnostics,
        "Java diagnostic map must expose the dd no-response status",
    )
    require(
        "ProxyStatusMtproxyPacketSentNoResponse" in strings
        and "ProxyStatusMtproxyPacketSentNoResponse" in strings_ru,
        "localized dd no-response strings are required",
    )

    print("MTProxy plain dd lifecycle guard passed.")


if __name__ == "__main__":
    main()
