#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "Tools"
MESSENGER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger"
TGNET_JAVA = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
ROTATION = MESSENGER / "ProxyRotationController.java"
JNI = ROOT / "TMessagesProj/jni"
TGNET = JNI / "tgnet"

RUNTIME_LOG_VERIFIER = TOOLS / "verify_mtproxy_runtime_logs.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def run_verifier(markers: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
        handle.write(markers)
        path = Path(handle.name)
    try:
        return subprocess.run(
            [sys.executable, str(RUNTIME_LOG_VERIFIER), str(path)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def base_log(*lines: str) -> str:
    result = [
        "logcat.txt:1: 06-30 13:20:30.000 connection(0x1) mtproxy_disconnect transport_state=closed epoll_registered=0 admission_active=0 tcp_gate_active=0",
        "logcat.txt:2: 06-30 13:20:30.010 connection(0x1) mtproxy_startup server_hello_hmac_ok bytes=196 len1=122 len2=58 flight=58 extra=0",
        "logcat.txt:3: 06-30 13:20:30.020 connection(0x1) mtproxy_startup endpoint_handshake_ok reason=server_hello_hmac_ok",
        "logcat.txt:4: 06-30 13:20:30.090 connection(0x1) mtproxy_startup first_tls_app_recv payload=1015",
        "logcat.txt:5: 06-30 13:20:30.100 connection(0x1) mtproxy_startup endpoint_data_path_success network_key=sberbank.dns.army:45631 key=sberbank.dns.army:45631:ee:sberbank.dns.army reason=first_tls_app_recv",
        "logcat.txt:6: 06-30 13:20:30.110 proxy_control decision=visible_usable_success source=native_stage origin=active_proxy account=0 phase=first_tls_app_recv endpoint=sberbank.dns.army:45631:ee:sberbank.dns.army",
    ]
    for index, line in enumerate(lines, start=7):
        result.append(f"logcat.txt:{index}: {line}")
    return "\n".join(result) + "\n"


def verify_runtime_contract(failures: list[str]) -> None:
    bad_proxy_check_overwrite = run_verifier(
        base_log(
            "06-30 13:20:31.000 proxy_control decision=visible_only source=proxy_check origin=proxy_check account=0 phase=socket_connect_start endpoint=fast2.mtproxy.zip:443:ee:wb.ru",
        )
    )
    require(
        bad_proxy_check_overwrite.returncode != 0
        and "non-active origin mirrored as active visible status" in bad_proxy_check_overwrite.stderr,
        "runtime verifier must reject proxy-check/candidate visible overwrite after fresh usable success",
        failures,
    )

    bad_same_endpoint_proxy_check_success = run_verifier(
        base_log(
            "06-30 13:20:31.000 proxy_control decision=visible_usable_success source=native_stage origin=proxy_check account=0 phase=first_tls_app_recv endpoint=sberbank.dns.army:45631:ee:sberbank.dns.army",
        )
    )
    require(
        bad_same_endpoint_proxy_check_success.returncode != 0
        and "non-active origin mirrored as active visible status" in bad_same_endpoint_proxy_check_success.stderr,
        "runtime verifier must reject same-endpoint proxy_check usable success as global visible success",
        failures,
    )

    good_proxy_check_isolated = run_verifier(
        base_log(
            "06-30 13:20:31.000 proxy_control decision=proxy_list_only source=proxy_check origin=proxy_check account=0 phase=socket_connect_start endpoint=fast2.mtproxy.zip:443:ee:wb.ru",
        )
    )
    require(
        good_proxy_check_isolated.returncode == 0,
        good_proxy_check_isolated.stderr.strip() or "runtime verifier must accept proxy_list_only candidate telemetry",
        failures,
    )

    good_same_endpoint_proxy_check_isolated = run_verifier(
        base_log(
            "06-30 13:20:31.000 proxy_control decision=proxy_list_only source=native_stage origin=proxy_check account=0 phase=first_tls_app_recv endpoint=sberbank.dns.army:45631:ee:sberbank.dns.army",
        )
    )
    require(
        good_same_endpoint_proxy_check_isolated.returncode == 0,
        good_same_endpoint_proxy_check_isolated.stderr.strip() or "runtime verifier must accept same-endpoint proxy_check row-only telemetry",
        failures,
    )

    bad_non_active_rotation = run_verifier(
        base_log(
            "06-30 13:20:31.000 proxy_rotation decision=trigger phase=mtproxy_packet_sent_no_response endpoint=sberbank.dns.army:45631:ee:sberbank.dns.army origin=proxy_check count=2",
        )
    )
    require(
        bad_non_active_rotation.returncode != 0
        and "proxy_rotation trigger from non-active origin" in bad_non_active_rotation.stderr,
        "runtime verifier must reject proxy rotation triggers from non-active origins",
        failures,
    )

    bad_terminal_hysteresis = run_verifier(
        base_log(
            "06-30 13:20:40.000 proxy_control decision=held_by_failure_hysteresis source=native_stage origin=active_proxy account=0 phase=unsupported_for_current_client endpoint=fast2.mtproxy.zip:443:ee:wb.ru failures=1",
        )
    )
    require(
        bad_terminal_hysteresis.returncode != 0
        and "one-shot terminal phase must not wait" in bad_terminal_hysteresis.stderr,
        "runtime verifier must reject unsupported_for_current_client hysteresis",
        failures,
    )

    good_terminal_quarantine = run_verifier(
        base_log(
            "06-30 13:20:40.000 proxy_control decision=terminal_proxy_config_unsupported source=native_stage origin=active_proxy account=0 phase=unsupported_for_current_client endpoint=fast2.mtproxy.zip:443:ee:wb.ru probe=fast2.mtproxy.zip:443:secret_hash=1111111111111111:wb.ru active_selected=1",
            "06-30 13:20:40.010 proxy_control decision=cancel_endpoint_attempts source=native_stage origin=active_proxy account=0 phase=unsupported_for_current_client endpoint=fast2.mtproxy.zip:443:ee:wb.ru probe=fast2.mtproxy.zip:443:secret_hash=1111111111111111:wb.ru proxy_check_cancelled=0 native_cancelled=3",
            "06-30 13:20:40.020 proxy_control decision=terminal_quarantine source=native_stage origin=active_proxy account=0 phase=unsupported_for_current_client endpoint=fast2.mtproxy.zip:443:ee:wb.ru probe=fast2.mtproxy.zip:443:secret_hash=1111111111111111:wb.ru",
            "06-30 13:20:40.030 proxy_control decision=ignored_cancelled_generation source=native_stage origin=active_proxy account=0 phase=ignored_cancelled_generation endpoint=fast2.mtproxy.zip:443:ee:wb.ru probe=fast2.mtproxy.zip:443:secret_hash=1111111111111111:wb.ru",
        )
    )
    require(
        good_terminal_quarantine.returncode == 0,
        good_terminal_quarantine.stderr.strip() or "runtime verifier must accept one-shot terminal quarantine with cancellation",
        failures,
    )

    bad_shadowed_reconnect = run_verifier(
        base_log(
            "06-30 13:20:35.000 connection(0x2, account0, dc2, type 2) mtproxy_startup reconnect_backoff phase=post_handshake_no_appdata delay_ms=2500 failed=1",
        )
    )
    require(
        bad_shadowed_reconnect.returncode != 0
        and "post_handshake_no_appdata created reconnect_backoff after fresh usable success" in bad_shadowed_reconnect.stderr,
        "runtime verifier must reject post_handshake_no_appdata reconnect_backoff after fresh app-data",
        failures,
    )

    good_shadowed_suppressed = run_verifier(
        base_log(
            "06-30 13:20:35.000 connection(0x2) mtproxy_startup shadowed_socket_failure phase=post_handshake_no_appdata held_by=first_tls_app_recv",
            "06-30 13:20:35.010 connection(0x2, account0, dc2, type 2) mtproxy_startup reconnect_backoff_suppressed phase=post_handshake_no_appdata",
        )
    )
    require(
        good_shadowed_suppressed.returncode == 0,
        good_shadowed_suppressed.stderr.strip() or "runtime verifier must accept shadowed post-handshake reconnect suppression",
        failures,
    )


def main() -> int:
    failures: list[str] = []
    event = read(MESSENGER / "ProxyConnectionEvent.java")
    runtime = read(MESSENGER / "ProxyRuntimeStateStore.java")
    health = read(MESSENGER / "ProxyHealthStore.java")
    phase_policy = read(MESSENGER / "ProxyPhasePolicy.java")
    diagnostics = read(MESSENGER / "ProxyCheckDiagnostics.java")
    scheduler = read(MESSENGER / "ProxyCheckScheduler.java")
    rotation = read(ROTATION)
    java_connections = read(TGNET_JAVA)
    wrapper = read(JNI / "TgNetWrapper.cpp")
    defines = read(TGNET / "Defines.h")
    manager_h = read(TGNET / "ConnectionsManager.h")
    manager_cpp = read(TGNET / "ConnectionsManager.cpp")
    socket_h = read(TGNET / "ConnectionSocket.h")
    socket_cpp = read(TGNET / "ConnectionSocket.cpp")
    connection_h = read(TGNET / "Connection.h")
    connection_cpp = read(TGNET / "Connection.cpp")
    endpoint_policy_h = read(TGNET / "MtProxyEndpointPolicy.h")
    endpoint_policy_cpp = read(TGNET / "MtProxyEndpointPolicy.cpp")
    file_operation = read(MESSENGER / "FileLoadOperation.java")
    check_all = read(TOOLS / "check_mtproxy_all.py")
    analyzer = read(TOOLS / "analyze_mtproxy_markers.py")
    phase_contract = read(TOOLS / "mtproxy_phase_contract.py")

    require("enum Origin" in event and "ACTIVE_PROXY" in event and "PROXY_CHECK" in event and "PROXY_LIST_ROW" in event, "ProxyConnectionEvent must carry explicit origin values", failures)
    require("origin" in wrapper and "probeKey" in wrapper and "onProxyConnectionStageChanged" in wrapper and "(ILjava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V" in wrapper, "JNI proxy stage callback must carry origin and probe key", failures)
    require("onProxyConnectionStageChanged(int32_t instanceNum, std::string diagnostic, std::string endpointKey, std::string probeKey, std::string origin)" in defines, "native delegate must expose proxy stage origin and probe key", failures)
    require(
        "if (!isActiveProxyEvent(event))" in runtime
        and "updateProxyRowOnly" in runtime
        and "active_origin_required" in runtime,
        "ProxyRuntimeStateStore must route non-active proxy events to row-only handling before active visible/backoff policy",
        failures,
    )
    require(
        "event.origin.wireName" in java_connections
        and "postNotificationName(NotificationCenter.proxyConnectionStageChanged, normalizedDiagnostic, endpointKey, event.origin.wireName)" in java_connections,
        "ConnectionsManager must propagate proxy event origin through proxyConnectionStageChanged notifications",
        failures,
    )
    require(
        "ignore_non_active_origin" in rotation
        and "ProxyConnectionEvent.Origin.ACTIVE_PROXY.wireName.equals(origin)" in rotation,
        "ProxyRotationController must ignore proxyConnectionStageChanged events whose origin is not active_proxy",
        failures,
    )

    require("isOneShotTerminal" in phase_policy, "ProxyPhasePolicy must expose one-shot terminal verdicts", failures)
    require("terminal_quarantine" in runtime and "quarantineAndCancelEndpoint" in runtime, "runtime store must centralize terminal quarantine", failures)
    require("oneShotTerminal" in health or "isOneShotTerminal" in health, "health store must bypass hysteresis for one-shot terminal phases", failures)

    require("cancelEndpointAttempts" in scheduler and "cancel_endpoint_attempts" in runtime, "Java scheduler/runtime must cancel endpoint attempts and log it", failures)
    require("cancelProxyEndpointAttemptsForAllAccounts" in java_connections and "native_cancelProxyEndpointAttempts" in java_connections, "Java ConnectionsManager must expose endpoint cancellation", failures)
    require("cancelProxyEndpointAttempts" in wrapper and "native_cancelProxyEndpointAttempts" in wrapper, "JNI wrapper must register endpoint cancellation", failures)
    require("cancelProxyEndpointAttempts" in manager_h and "cancelProxyEndpointAttempts" in manager_cpp, "native ConnectionsManager must cancel endpoint attempts", failures)
    require("matchesMtProxyEndpointKey" in socket_h and "cancelMtProxyEndpointAttempt" in socket_h, "ConnectionSocket must expose endpoint match/cancel helpers", failures)
    require("matchesMtProxyEndpointKey" in socket_cpp and "cancelMtProxyEndpointAttempt" in socket_cpp, "ConnectionSocket must implement endpoint match/cancel helpers", failures)
    require("ignored_cancelled_generation" in diagnostics and "ignored_cancelled_generation" in socket_cpp, "cancelled native generations must be a shared diagnostic", failures)

    require("freshDataPathSuccessRemainingMs" in endpoint_policy_h and "freshDataPathSuccessRemainingMs" in endpoint_policy_cpp, "endpoint policy must expose read-only fresh data-path success", failures)
    require("shadowed_socket_failure" in diagnostics and "shadowed_socket_failure" in socket_cpp, "shadowed socket failures must be published as neutral diagnostics", failures)
    require("reconnect_backoff_suppressed" in analyzer and "reconnect_backoff_suppressed" in connection_cpp, "shadowed/cancelled closes must suppress reconnect backoff", failures)

    require("TRANSPORT_SETTINGS_STARTUP_SETTLE_MS" in manager_cpp and "transportSettingsReconnectPending" in manager_h + manager_cpp, "native transport settings must debounce startup reconnect churn", failures)

    require("received_cancelled_chunk_after_cancelRequests_debug" in file_operation, "FileLoadOperation must drop cancelled chunks after cancelRequests without error logs", failures)
    require("file_load_cancelled_missing_temp" in file_operation, "FileLoadOperation must treat missing temp during cancellation as cancelled", failures)

    require("check_mtproxy_control_plane_one_pass.py" in check_all, "new guard must be included in Tools/check_mtproxy_all.py", failures)
    require("shadowed_socket_failure" in phase_contract and "ignored_cancelled_generation" in phase_contract, "phase contract must include new neutral diagnostics", failures)
    require("shadowed_socket_failure" in analyzer and "ignored_cancelled_generation" in analyzer, "analyzer must explain new neutral diagnostics", failures)

    verify_runtime_contract(failures)

    if failures:
        print("MTProxy control-plane one-pass guard failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1
    print("MTProxy control-plane one-pass guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
