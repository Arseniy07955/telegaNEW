#!/usr/bin/env python3
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SOCKET_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
SOCKET_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.h"
MACHINE_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocketStateMachine.cpp"
MACHINE_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocketStateMachine.h"
CMAKE = ROOT / "TMessagesProj/jni/CMakeLists.txt"
ENDPOINT_RECORDER_CPP = ROOT / "TMessagesProj/jni/mtproxy/MtProxyEndpointRecorder.cpp"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def fail(message: str, failures: list[str]) -> None:
    failures.append(message)


def private_section(header: str) -> str:
    start = header.find("private:")
    if start == -1:
        return ""
    return header[start:]


def has_member_field(section: str, field: str) -> bool:
    return re.search(rf"\b{re.escape(field)}\b\s*(?:=|;)", section) is not None


def main() -> int:
    failures: list[str] = []
    socket_cpp = read(SOCKET_CPP)
    socket_h = read(SOCKET_H)
    machine_cpp = read(MACHINE_CPP)
    machine_h = read(MACHINE_H)
    endpoint_recorder_cpp = read(ENDPOINT_RECORDER_CPP)
    cmake = read(CMAKE)
    private = private_section(socket_h)
    marker_source = socket_cpp + machine_cpp + endpoint_recorder_cpp

    if not MACHINE_H.exists() or not MACHINE_CPP.exists():
        fail("ConnectionSocketStateMachine.h/.cpp must exist", failures)
    if "tgnet/ConnectionSocketStateMachine.cpp" not in cmake:
        fail("ConnectionSocketStateMachine.cpp must be compiled in tgnet CMake target", failures)

    for required in (
        "enum class LifecycleState",
        "enum class TransportMode",
        "struct SocketSubstate",
        "struct EpollSubstate",
        "struct HostResolveSubstate",
        "struct NotificationSubstate",
        "struct SocksSubstate",
        "struct FakeTlsSubstate",
        "struct WssSubstate",
        "struct AdmissionSubstate",
        "struct EndpointGateSubstate",
        "struct PendingWriteSubstate",
        "struct DiagnosticsSubstate",
        "struct ActionRule",
        "bool can(",
        "void setTransportMode(",
        "ssize_t sendBytes(",
        "ssize_t recvBytes(",
        "bool epollCtlAdd(",
        "bool epollCtlMod(",
        "bool epollCtlDel(",
        "bool closeNativeSocket(",
    ):
        if required not in machine_h + machine_cpp:
            fail(f"ConnectionSocketStateMachine must define {required}", failures)

    for legacy in (
        "socketFd",
        "epollRegistered",
        "currentTransportState",
        "proxyAuthState",
        "tlsState",
        "onConnectedSent",
        "socketCloseNotified",
        "waitingForHostResolve",
        "adjustWriteOpAfterResolve",
        "currentTransportWss",
        "pendingClientHello",
        "pendingTlsFrame",
        "proxyHandshakeAdmissionQueued",
        "proxyHandshakeAdmissionActive",
        "proxyEndpointBackoffReady",
        "proxyEndpointTcpConnectActive",
        "proxyEndpointDnsCoalesceReady",
        "mtproxyFirstTlsFrameSentLogged",
        "mtproxyFirstPlainDataSentLogged",
    ):
        if has_member_field(private, legacy):
            fail(f"ConnectionSocket.h must not own legacy runtime field {legacy}", failures)

    if "ConnectionSocketStateMachine stateMachine" not in socket_h:
        fail("ConnectionSocket must own exactly one ConnectionSocketStateMachine member", failures)

    for mode in (
        "TransportMode::Direct",
        "TransportMode::Socks5",
        "TransportMode::PlainMtProxy",
        "TransportMode::FakeTlsMtProxy",
        "TransportMode::Wss",
    ):
        if mode not in socket_cpp:
            fail(f"ConnectionSocket open path must set {mode}", failures)

    for forbidden in (
        "allowedActionStates",
        "TransportTransitionRule",
        "static const TransportActionRule",
    ):
        if forbidden in socket_cpp:
            fail(f"ConnectionSocket.cpp must not keep duplicate state-machine policy table {forbidden}", failures)

    for side_effect, wrapper in (
        ("send(", "stateMachine.sendBytes("),
        ("recv(", "stateMachine.recvBytes("),
        ("connect(", "stateMachine.connectNativeSocket("),
        ("epoll_ctl(", "stateMachine.epollCtl"),
        ("close(socketFd", "stateMachine.closeNativeSocket("),
    ):
        if side_effect in socket_cpp and wrapper not in socket_cpp:
            fail(f"ConnectionSocket.cpp raw side effect {side_effect} must be routed through stateMachine", failures)

    for marker in (
        "server_hello_hmac_ok",
        "endpoint_handshake_ok",
        "endpoint_data_path_success",
        "first_tls_app_recv",
        "first_mtproxy_packet_recv",
        "transport_state=%s",
    ):
        if marker not in marker_source:
            fail(f"state-machine rewrite must preserve marker {marker}", failures)

    if failures:
        print("ConnectionSocket state-machine rewrite guard failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1
    print("ConnectionSocket state-machine rewrite guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
