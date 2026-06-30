#!/usr/bin/env python3
from pathlib import Path
import sys

from mtproxy_phase_contract import ENDPOINT_EXACT, phases


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def slice_between(text: str, start: str, end: str) -> str:
    start_idx = text.find(start)
    if start_idx == -1:
        return ""
    end_idx = text.find(end, start_idx)
    if end_idx == -1:
        return text[start_idx:]
    return text[start_idx:end_idx]


def main() -> int:
    failures: list[str] = []
    socket = read("TMessagesProj/jni/tgnet/ConnectionSocket.cpp")
    socket_h = read("TMessagesProj/jni/tgnet/ConnectionSocket.h")
    machine_h = read("TMessagesProj/jni/tgnet/ConnectionSocketStateMachine.h")
    connection = read("TMessagesProj/jni/tgnet/Connection.cpp")
    connection_h = read("TMessagesProj/jni/tgnet/Connection.h")
    manager = read("TMessagesProj/jni/tgnet/ConnectionsManager.cpp")
    manager_h = read("TMessagesProj/jni/tgnet/ConnectionsManager.h")
    endpoint_policy = read("TMessagesProj/jni/tgnet/MtProxyEndpointPolicy.cpp")
    phase_policy = read("TMessagesProj/src/main/java/org/telegram/messenger/ProxyPhasePolicy.java")
    runtime_store = read("TMessagesProj/src/main/java/org/telegram/messenger/ProxyRuntimeStateStore.java")
    diagnostics = read("TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckDiagnostics.java")
    endpoint_key = read("TMessagesProj/src/main/java/org/telegram/messenger/ProxyEndpointKey.java")
    all_checks = read("Tools/check_mtproxy_all.py")

    phase_contract = {phase.name: phase for phase in phases()}
    for phase_name in ("first_mtproxy_packet_sent", "first_mtproxy_packet_recv", "mtproxy_packet_sent_no_response"):
        require(
            phase_contract[phase_name].endpoint_key == ENDPOINT_EXACT,
            f"{phase_name} must be exact-config scoped, not host:port/network scoped",
            failures,
        )

    state_key = slice_between(endpoint_policy, "std::string MtProxyEndpointPolicy::stateKeyForPhase", "bool MtProxyEndpointPolicy::failureNeedsCooldown")
    require(
        '"mtproxy_packet_sent_no_response"' not in state_key
        and "networkEndpointKey" in state_key,
        "native endpoint policy must not route DD first-packet no-response to the host:port network key",
        failures,
    )
    require(
        "case ProxyCheckDiagnostics.FIRST_MTPROXY_PACKET_SENT:" in phase_policy
        and "return live(KeyScope.EXACT);" in slice_between(phase_policy, "case ProxyCheckDiagnostics.FIRST_MTPROXY_PACKET_SENT:", "case ProxyCheckDiagnostics.FIRST_TLS_APP_RECV:")
        and "case ProxyCheckDiagnostics.FIRST_MTPROXY_PACKET_RECV:" in phase_policy
        and "return success(KeyScope.EXACT);" in slice_between(phase_policy, "case ProxyCheckDiagnostics.FIRST_MTPROXY_PACKET_RECV:", "case ProxyCheckDiagnostics.CONNECTION_NOT_STARTED:")
        and "case ProxyCheckDiagnostics.MTPROXY_PACKET_SENT_NO_RESPONSE:" in phase_policy
        and "return failure(KeyScope.EXACT, true, true);" in slice_between(phase_policy, "case ProxyCheckDiagnostics.MTPROXY_PACKET_SENT_NO_RESPONSE:", "case ProxyCheckDiagnostics.UNKNOWN_FAIL:"),
        "Java phase policy must classify DD sent/recv/no-response as exact data-path phases",
        failures,
    )

    require(
        "firstTransportPacketSent" in machine_h
        and "firstTransportPacketReceived" in machine_h
        and "dataPathProven" in machine_h
        and "deadForWrites" in machine_h,
        "socket state machine must carry explicit data-path and dead-for-writes fields",
        failures,
    )
    require(
        "firstTransportPacketSent = true;" in socket
        and "firstTransportPacketReceived = true;" in socket
        and "dataPathProven = true;" in socket
        and 'recordMtProxyEndpointDataPathSuccess("first_mtproxy_packet_recv")' in socket
        and 'recordMtProxyEndpointDataPathSuccess("first_tls_app_recv")' in socket,
        "native socket must mark data-path proof only after first transport/appdata receive",
        failures,
    )

    close_body = slice_between(socket, "void ConnectionSocket::closeSocket", "void ConnectionSocket::onEvent")
    require(
        'terminalDiagnostic == "post_handshake_no_appdata"' in close_body
        and 'terminalDiagnostic == "mtproxy_packet_sent_no_response"' in close_body
        and 'context.networkEndpointKey = "";' in close_body
        and 'publishProxyConnectionStage("shadowed_socket_failure")' in close_body
        and "held_by=%s" in close_body,
        "native close shadowing must cover DD no-response on exact config and publish held_by",
        failures,
    )
    require(
        "bool ConnectionSocket::isClosingOrClosedForWrites() const" in socket
        and "void ConnectionSocket::markConnectionDeadForWrites" in socket
        and "markConnectionDeadForWrites(\"closeSocket\")" in close_body
        and "write_suppressed_dead_connection" in socket
        and "bool isClosingOrClosedForWrites() const;" in socket_h,
        "native socket must mark closing connections dead for writes before close cleanup",
        failures,
    )

    require(
        "bool Connection::sendData" in connection
        and "bool canSendRequestData" in connection_h
        and "write_gate_disconnected" in connection
        and "return false;" in slice_between(connection, "bool Connection::sendData", "inline std::string *Connection::getCurrentSecret"),
        "Connection::sendData must reject dead/disconnected writes with a boolean result",
        failures,
    )
    require(
        "requeueMessagesForDeadConnection" in manager
        and "removeQuickAckMappingForMessages" in manager
        and "process_running_request" in manager
        and "process_queued_request" in manager
        and "sendMessages_post_create" in manager
        and "bool sendMessagesToConnection" in manager_h,
        "ConnectionsManager must gate request assignment and requeue pending requests when a connection dies before send",
        failures,
    )

    require(
        "private static boolean isMtProxy" in runtime_store
        and "return ProxyHealthStore.hasFreshUsableSuccess(proxyInfo, now);" in slice_between(runtime_store, "private static boolean isCurrentProxyUsable", "public static boolean isEndpointRotatedAway")
        and "reason=mtproxy_wait_data_path" in runtime_store,
        "runtime store must not treat generic connected state as MTProxy usable without data-path success",
        failures,
    )
    require(
        "currentConnectionIsUsableForStatus" in diagnostics
        and "hasFreshLivePhase(proxyInfo) && isProxyUsableSuccessPhase(proxyInfo.lastCheckDiagnostic)" in diagnostics,
        "diagnostic status text/color must gate MTProxy connected status on data-path success",
        failures,
    )

    require(
        "secretHashForLiveStage" in endpoint_key
        and 'builder.append(":secret_hash=").append(hash);' in endpoint_key
        and '"secret_hash=" + mtProxySecretHashForRecipeKey(*proxySecret)' in socket
        and "currentMtProxyProbeKey = mtProxyRecipeCacheKeyFor(*proxyAddress, proxyPort, *proxySecret, \"\")" in socket,
        "DD/legacy live-stage keys and probe keys must include a secret hash so different secrets do not share exact data-path state",
        failures,
    )
    require(
        '"check_mtproxy_datapath_failure.py"' in all_checks,
        "check_mtproxy_all.py must include the data-path failure guard",
        failures,
    )

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("MTProxy data-path failure guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
