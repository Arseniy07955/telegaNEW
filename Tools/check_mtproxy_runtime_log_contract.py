#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "Tools/verify_mtproxy_runtime_logs.py"
COLLECTOR = ROOT / "Tools/collect_mtproxy_logs.ps1"
README = ROOT / "README.md"
SOCKET = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
SOCKET_HEADER = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.h"
PUBLISHER_HEADER = ROOT / "TMessagesProj/jni/mtproxy/MtProxySocketPublisher.h"
PUBLISHER_CPP = ROOT / "TMessagesProj/jni/mtproxy/MtProxySocketPublisher.cpp"
CLASSIFICATION_H = ROOT / "TMessagesProj/jni/mtproxy/MtProxyPhaseClassification.h"
CMAKE = ROOT / "TMessagesProj/jni/CMakeLists.txt"


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        raise SystemExit(1)


def run_verifier(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFIER), str(path)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def write_markers(directory: Path, body: str) -> Path:
    marker_path = directory / "mtproxy_markers.txt"
    marker_path.write_text(body.strip() + "\n", encoding="utf-8")
    return marker_path


def main() -> int:
    collector = COLLECTOR.read_text(encoding="utf-8", errors="replace")
    readme = README.read_text(encoding="utf-8", errors="replace")
    socket = SOCKET.read_text(encoding="utf-8", errors="replace")
    socket_header = SOCKET_HEADER.read_text(encoding="utf-8", errors="replace")
    publisher_header = PUBLISHER_HEADER.read_text(encoding="utf-8", errors="replace")
    publisher_cpp = PUBLISHER_CPP.read_text(encoding="utf-8", errors="replace")
    cmake = CMAKE.read_text(encoding="utf-8", errors="replace")
    verifier = VERIFIER.read_text(encoding="utf-8", errors="replace")
    require(
        "mtproxy/MtProxySocketPublisher.cpp" in cmake,
        "CMake must compile the native MTProxy socket publisher facade",
    )
    require(
        "struct MtProxySocketObservation" in publisher_header
        and "const char *phase = MtProxyPhase::ConnectionNotStarted;" in publisher_header
        and 'const char *reason = "unknown";' in publisher_header
        and "std::string endpointKey;" in publisher_header
        and "std::string probeKey;" in publisher_header
        and "std::string networkEndpointKey;" in publisher_header
        and "bool publishVisibleStage = true;" in publisher_header
        and "bool recordEndpointFailure = false;" in publisher_header,
        "native publisher facade must expose typed observation fields",
    )
    require(
        "mtProxyPublishSocketObservation" in publisher_cpp
        and "callbacks.publishVisibleStage(normalized)" in publisher_cpp
        and "callbacks.recordEndpointFailure(normalized)" in publisher_cpp
        and "mtProxySocketObservationIsHighRiskPhase" in publisher_cpp,
        "native publisher facade must route normalized observations through publish/failure callbacks",
    )
    classification = CLASSIFICATION_H.read_text(encoding="utf-8", errors="replace")
    require(
        "MtProxyPhase::isObservationFacadePhase(phase)" in publisher_cpp,
        "publisher facade must classify high-risk phases via the generated phase classification",
    )
    facade_start = classification.find("inline bool isObservationFacadePhase")
    facade_body = classification[facade_start:classification.find("\n}", facade_start)]
    facade_phases = (
        "recipe_failed",
        "handshake_profiles_exhausted",
        "faketls_not_mtproxy_response",
        "faketls_no_server_hello_terminal",
        "faketls_server_closed_terminal",
        "secret_parse_invalid_domain_control_char",
        "secret_parse_invalid_domain",
        "dns_blocked_zero_address",
        "post_handshake_no_appdata",
        "first_tls_app_recv",
        "first_mtproxy_packet_recv",
    )
    for phase in facade_phases:
        require(
            f'"{phase}"' in facade_body and phase in verifier,
            f"generated facade classification and runtime verifier must preserve {phase}",
        )
    require(
        '#include "mtproxy/MtProxySocketPublisher.h"' in socket
        and "void publishMtProxySocketObservation(const MtProxySocketObservation &observation)" in socket_header
        and "void ConnectionSocket::publishMtProxySocketObservation" in socket
        and "publishProxyConnectionStage(publishedObservation.phase)" in socket
        and "recordMtProxyEndpointFailure(publishedObservation.phase, publishedObservation.reason)" in socket,
        "ConnectionSocket must bridge publisher observations to the existing visible-stage and endpoint-failure paths",
    )
    require(
        'recipeFailureObservation.phase = "recipe_failed"' in socket
        and "exhaustedObservation.phase = MtProxyPhase::HandshakeProfilesExhausted" in socket
        and "observation.phase = MtProxyPhase::DnsBlockedZeroAddress" in socket
        and "observation.phase = MtProxyPhase::PostHandshakeNoAppdata" in socket
        and "observation.phase = MtProxyPhase::FirstTlsAppRecv" in socket
        and "observation.phase = MtProxyPhase::FirstMtproxyPacketRecv" in socket
        and "mtProxySocketObservationIsHighRiskPhase(terminalDiagnostic.c_str())" in socket,
        "high-risk native publish paths must use MtProxySocketObservation without rewriting every stage",
    )
    require(
        "mtproxy_transport" in collector
        and "transport_state" in collector
        and "endpoint_handshake_ok" in collector
        and "endpoint_data_path_success" in collector,
        "collector must preserve transport-state and split endpoint-success markers in mtproxy_markers.txt",
    )
    require(
        "verify_mtproxy_runtime_logs.py" in collector
        and "mtproxy_runtime_contract.txt" in collector
        and "MTProxy runtime contract verifier" in collector,
        "collector must run the runtime log contract verifier and save its output in the session directory",
    )
    require(
        "mtproxy_runtime_contract.txt" in readme
        and "Tools/verify_mtproxy_runtime_logs.py" in readme
        and "transport_state=" in readme
        and "endpoint_handshake_ok" in readme
        and "endpoint_data_path_success" in readme,
        "README must document the runtime contract artifact and required live MTProxy markers",
    )
    require(
        "endpoint_data_path_success` должен появляться только после первого `first_tls_app_recv`" in readme
        and "`first_mtproxy_packet_recv`" in readme,
        "README must document that data-path success is ordered after first app-data evidence",
    )
    first_tls_recv_log = 'DEBUG_D("connection(%p) mtproxy_startup first_tls_app_recv payload=%d"'
    first_tls_recv_success = 'recordMtProxyEndpointDataPathSuccess("first_tls_app_recv")'
    require(
        first_tls_recv_log in socket
        and first_tls_recv_success in socket
        and socket.find(first_tls_recv_log) < socket.find(first_tls_recv_success),
        "ConnectionSocket must log first_tls_app_recv before endpoint_data_path_success",
    )
    first_mtproxy_recv_log = 'DEBUG_D("connection(%p) mtproxy_startup first_mtproxy_packet_recv bytes=%u secret_kind=%s"'
    first_mtproxy_recv_success = 'recordMtProxyEndpointDataPathSuccess("first_mtproxy_packet_recv")'
    require(
        first_mtproxy_recv_log in socket
        and first_mtproxy_recv_success in socket
        and socket.find(first_mtproxy_recv_log) < socket.find(first_mtproxy_recv_success),
        "ConnectionSocket must log first_mtproxy_packet_recv before endpoint_data_path_success",
    )
    require(
        "mtproxy_disconnect recv_eof" not in socket,
        "recv_eof marker must not masquerade as a full mtproxy_disconnect summary",
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        good_dir = tmp_path / "good-session"
        good_dir.mkdir()
        good_markers = write_markers(
            good_dir,
            """
logcat.txt:1: 06-20 15:00:00.000 connection(0x1) mtproxy_transport snapshot event=open reason=start transport_state=prepared epoll_registered=0 admission_active=0 admission_queued=0 tcp_gate_active=0 waiting_resolve=0 proxy_state=10 tls_state=0
logcat.txt:2: 06-20 15:00:00.050 connection(0x1) mtproxy_startup client_hello_sent bytes=1897
logcat.txt:3: 06-20 15:00:00.100 connection(0x1) mtproxy_startup server_hello_hmac_ok bytes=2219 len1=1210 len2=993 flight=993 extra=0
logcat.txt:4: 06-20 15:00:00.101 connection(0x1) mtproxy_startup endpoint_handshake_ok network_key=198.51.100.10:443 key=198.51.100.10:443:cdn.example reason=server_hello_hmac_ok
logcat.txt:5: 06-20 15:00:00.200 connection(0x1) mtproxy_startup first_tls_app_recv payload=105
logcat.txt:6: 06-20 15:00:00.201 connection(0x1) mtproxy_startup endpoint_data_path_success network_key=198.51.100.10:443 key=198.51.100.10:443:cdn.example reason=first_tls_app_recv
logcat.txt:7: 06-20 15:00:00.300 connection(0x1) mtproxy_disconnect reason=2 reason_text=peer_closed error=0 error_text=ok secret_kind=ee is_faketls=1 is_wss=0 transport_state=closing epoll_registered=1 admission_active=0 admission_queued=0 tcp_gate_active=0 waiting_resolve=0 proxy_state=0 tls_state=0 bytes_read=512 pending_hello=0/0 pending=0/0 first_tls_sent=1 first_tls_recv=1 first_plain_sent=0 first_plain_recv=0 tls_frames_completed=3
            """,
        )
        good_result = run_verifier(good_dir)
        require(good_result.returncode == 0, good_result.stderr.strip() or good_result.stdout)
        require(
            "MTProxy runtime log contract passed." in good_result.stdout,
            "verifier must report a clear pass message for a valid runtime marker session",
        )

        direct_file_result = run_verifier(good_markers)
        require(direct_file_result.returncode == 0, "verifier must accept mtproxy_markers.txt directly")

        bad_hmac_dir = tmp_path / "bad-hmac-session"
        bad_hmac_dir.mkdir()
        write_markers(
            bad_hmac_dir,
            """
logcat.txt:1: connection(0x2) mtproxy_transport snapshot event=open reason=start transport_state=prepared epoll_registered=0 admission_active=0 admission_queued=0 tcp_gate_active=0 waiting_resolve=0 proxy_state=10 tls_state=0
logcat.txt:2: connection(0x2) mtproxy_startup server_hello_hmac_ok bytes=2219
logcat.txt:3: connection(0x2) mtproxy_startup endpoint_handshake_ok network_key=198.51.100.11:443 key=198.51.100.11:443:cdn.example reason=server_hello_hmac_ok
logcat.txt:4: connection(0x2) mtproxy_startup endpoint_data_path_success network_key=198.51.100.11:443 key=198.51.100.11:443:cdn.example reason=server_hello_hmac_ok
            """,
        )
        bad_hmac_result = run_verifier(bad_hmac_dir)
        require(bad_hmac_result.returncode != 0, "verifier must reject data-path success attributed to server_hello_hmac_ok")
        require(
            "endpoint_data_path_success must not use reason=server_hello_hmac_ok" in bad_hmac_result.stderr,
            "verifier must explain the false data-path success regression",
        )

        missing_state_dir = tmp_path / "missing-state-session"
        missing_state_dir.mkdir()
        write_markers(
            missing_state_dir,
            """
logcat.txt:1: connection(0x3) mtproxy_startup server_hello_hmac_ok bytes=2219
logcat.txt:2: connection(0x3) mtproxy_startup endpoint_handshake_ok network_key=198.51.100.12:443 key=198.51.100.12:443:cdn.example reason=server_hello_hmac_ok
logcat.txt:3: connection(0x3) mtproxy_startup endpoint_data_path_success network_key=198.51.100.12:443 key=198.51.100.12:443:cdn.example reason=first_tls_app_recv
            """,
        )
        missing_state_result = run_verifier(missing_state_dir)
        require(missing_state_result.returncode != 0, "verifier must reject logs without transport_state fields")
        require(
            "missing transport_state=" in missing_state_result.stderr,
            "verifier must explain missing transport state evidence",
        )

        missing_split_dir = tmp_path / "missing-split-session"
        missing_split_dir.mkdir()
        write_markers(
            missing_split_dir,
            """
logcat.txt:1: connection(0x4) mtproxy_transport snapshot event=open reason=start transport_state=prepared epoll_registered=0 admission_active=0 admission_queued=0 tcp_gate_active=0 waiting_resolve=0 proxy_state=10 tls_state=0
logcat.txt:2: connection(0x4) mtproxy_startup server_hello_hmac_ok bytes=2219
            """,
        )
        missing_split_result = run_verifier(missing_split_dir)
        require(missing_split_result.returncode != 0, "verifier must reject logs without split endpoint success markers")
        require(
            "missing endpoint_handshake_ok" in missing_split_result.stderr
            and "missing endpoint_data_path_success" in missing_split_result.stderr,
            "verifier must explain missing split endpoint success markers",
        )

        early_data_path_dir = tmp_path / "early-data-path-session"
        early_data_path_dir.mkdir()
        write_markers(
            early_data_path_dir,
            """
logcat.txt:1: connection(0x5) mtproxy_transport snapshot event=open reason=start transport_state=prepared epoll_registered=0 admission_active=0 admission_queued=0 tcp_gate_active=0 waiting_resolve=0 proxy_state=10 tls_state=0
logcat.txt:2: connection(0x5) mtproxy_startup server_hello_hmac_ok bytes=2219
logcat.txt:3: connection(0x5) mtproxy_startup endpoint_handshake_ok network_key=198.51.100.13:443 key=198.51.100.13:443:cdn.example reason=server_hello_hmac_ok
logcat.txt:4: connection(0x5) mtproxy_startup endpoint_data_path_success network_key=198.51.100.13:443 key=198.51.100.13:443:cdn.example reason=first_tls_app_recv
            """,
        )
        early_data_path_result = run_verifier(early_data_path_dir)
        require(early_data_path_result.returncode != 0, "verifier must reject data-path success before first app-data evidence")
        require(
            "endpoint_data_path_success reason=first_tls_app_recv must be preceded by first_tls_app_recv" in early_data_path_result.stderr,
            "verifier must explain early data-path success without app-data",
        )

    print("MTProxy runtime log contract guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
