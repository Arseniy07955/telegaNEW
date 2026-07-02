#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "Tools"
TGNET = ROOT / "TMessagesProj/jni/tgnet"
MESSENGER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger"
VALUES = ROOT / "TMessagesProj/src/main/res/values/strings.xml"
VALUES_RU = ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml"
VERIFIER = TOOLS / "verify_mtproxy_runtime_logs.py"

TERMINAL_PHASES = {
    "faketls_not_mtproxy_response": "ProxyStatusFaketlsNotMtproxyResponse",
    "faketls_no_server_hello_terminal": "ProxyStatusFaketlsNoServerHelloTerminal",
    "faketls_server_closed_terminal": "ProxyStatusFaketlsServerClosedTerminal",
}

# Phases decided before the socket opens (HandshakeBudgetBackoff and
# ProfilesExhaustedBackoff decisions in mtProxyProbeBeginOrJoin). Each must be
# preserved by the generated MtProxyPhase::isPreIoTerminalVerdict, engage a
# reconnect hold via the generated MtProxyPhase::needsReconnectBackoff and be
# flagged pre_io_terminal in the Python contract, otherwise closeSocket
# re-derives "connection_not_started" and connect() hot-loops
# (02.07 log: ~1300 reconnects/sec).
PRE_IO_TERMINAL_PHASES = (
    "faketls_not_mtproxy_response",
    "faketls_no_server_hello_terminal",
    "faketls_server_closed_terminal",
    "handshake_profiles_exhausted",
)

RETRY_LIVE_PHASES = (
    "admission_hold_after_client_hello_failure",
    "phase_adaptive_recipe",
    "dns_cache_hit",
    "connect_start",
    "socket_connect_start",
    "socket_connected",
    "client_hello_sent",
    "mtproxy_probe_wait",
)

FRESH_FAILURE_BREAKTHROUGH_PHASES = (
    "SERVER_HELLO_HMAC_OK",
    "ON_CONNECTED",
    "FIRST_TLS_APP_RECV",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def run_verifier(markers: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
        handle.write(markers.strip() + "\n")
        path = Path(handle.name)
    try:
        return subprocess.run(
            [sys.executable, str(VERIFIER), str(path)],
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
        "logcat.txt:1: 07-01 20:59:30.000 connection(0x9) mtproxy_transport snapshot event=open reason=start transport_state=prepared epoll_registered=0 admission_active=0 admission_queued=0 tcp_gate_active=0 waiting_resolve=0 proxy_state=10 tls_state=0",
        "logcat.txt:2: 07-01 20:59:30.010 connection(0x9) mtproxy_startup server_hello_hmac_ok bytes=196 flight=58 extra=0",
        "logcat.txt:3: 07-01 20:59:30.020 connection(0x9) mtproxy_startup endpoint_handshake_ok network_key=good.example:443 key=good.example:443:ee:good.example reason=server_hello_hmac_ok",
        "logcat.txt:4: 07-01 20:59:30.030 connection(0x9) mtproxy_startup first_tls_app_recv payload=100",
        "logcat.txt:5: 07-01 20:59:30.040 connection(0x9) mtproxy_startup endpoint_data_path_success network_key=good.example:443 key=good.example:443:ee:good.example reason=first_tls_app_recv",
        "logcat.txt:6: 07-01 20:59:30.050 proxy_control decision=visible_usable_success source=native_stage origin=active_socket account=0 phase=first_tls_app_recv endpoint=good.example:443:ee:good.example",
    ]
    for index, line in enumerate(lines, start=7):
        result.append(f"logcat.txt:{index}: {line}")
    return "\n".join(result) + "\n"


def verify_runtime_replays(failures: list[str]) -> None:
    bad_terminal_loop = run_verifier(
        base_log(
            "07-01 21:00:00.000 proxy_control decision=backoff source=native_stage origin=active_socket account=0 phase=faketls_not_mtproxy_response endpoint=avito.mosru.v6.rocks:443:ee:avito.mosru.v6.rocks probe=avito.mosru.v6.rocks:443:secret_hash=aaaaaaaaaaaaaaaa:avito.mosru.v6.rocks",
            "07-01 21:00:00.001 connection(0xa1) mtproxy_startup client_hello_sent bytes=1897 expected=1897 domain_len=18",
        )
    )
    require(
        bad_terminal_loop.returncode != 0
        and "FakeTLS terminal budget overwritten by client_hello_sent" in bad_terminal_loop.stderr,
        "runtime verifier must reject client_hello_sent after a FakeTLS terminal budget verdict",
        failures,
    )

    bad_exact_overwrite = run_verifier(
        base_log(
            "07-01 21:00:10.000 proxy_control decision=visible_only source=native_stage origin=active_socket account=0 phase=server_hello_hmac_mismatch endpoint=avito.mosru.v6.rocks:443:ee:avito.mosru.v6.rocks probe=avito.mosru.v6.rocks:443:secret_hash=aaaaaaaaaaaaaaaa:avito.mosru.v6.rocks",
            "07-01 21:00:10.000 proxy_control decision=visible_only source=native_stage origin=active_socket account=0 phase=client_hello_sent endpoint=avito.mosru.v6.rocks:443:ee:avito.mosru.v6.rocks probe=avito.mosru.v6.rocks:443:secret_hash=aaaaaaaaaaaaaaaa:avito.mosru.v6.rocks",
        )
    )
    require(
        bad_exact_overwrite.returncode != 0
        and "exact FakeTLS failure overwritten by visible retry/live phase" in bad_exact_overwrite.stderr,
        "runtime verifier must reject exact FakeTLS failures being overwritten by visible retry/live phases",
        failures,
    )

    good_held_retry = run_verifier(
        base_log(
            "07-01 21:00:20.000 proxy_control decision=visible_only source=native_stage origin=active_socket account=0 phase=faketls_server_hello_wait_timeout endpoint=get.utkanos.life:443:ee:get.utkanos.life probe=get.utkanos.life:443:secret_hash=bbbbbbbbbbbbbbbb:get.utkanos.life",
            "07-01 21:00:20.000 proxy_control decision=held_by_fresh_failure source=native_stage origin=active_socket account=0 phase=mtproxy_probe_wait endpoint=get.utkanos.life:443:ee:get.utkanos.life probe=get.utkanos.life:443:secret_hash=bbbbbbbbbbbbbbbb:get.utkanos.life held_by=faketls_server_hello_wait_timeout",
        )
    )
    require(
        good_held_retry.returncode == 0,
        good_held_retry.stderr.strip() or "runtime verifier must accept held retry/live phases after exact FakeTLS failures",
        failures,
    )

    bad_clobbered_close = run_verifier(
        base_log(
            "07-01 21:00:40.000 connection(0xa2) mtproxy_startup probe_faketls_budget_backoff key=avito.mosru.v6.rocks:443:secret_hash=aaaaaaaaaaaaaaaa:avito.mosru.v6.rocks endpoint=avito.mosru.v6.rocks:443:ee:avito.mosru.v6.rocks phase=faketls_not_mtproxy_response owner_generation=6",
            "07-01 21:00:40.003 connection(0xa2) mtproxy_startup close_diagnostic phase=connection_not_started",
        )
    )
    require(
        bad_clobbered_close.returncode != 0
        and "pre-I/O terminal verdict clobbered to connection_not_started" in bad_clobbered_close.stderr,
        "runtime verifier must reject a pre-I/O terminal verdict clobbered to connection_not_started at close",
        failures,
    )

    good_preserved_close = run_verifier(
        base_log(
            "07-01 21:00:50.000 connection(0xa3) mtproxy_startup probe_faketls_budget_backoff key=avito.mosru.v6.rocks:443:secret_hash=aaaaaaaaaaaaaaaa:avito.mosru.v6.rocks endpoint=avito.mosru.v6.rocks:443:ee:avito.mosru.v6.rocks phase=faketls_not_mtproxy_response owner_generation=6",
            "07-01 21:00:50.003 connection(0xa3) mtproxy_startup close_diagnostic phase=faketls_not_mtproxy_response",
        )
    )
    require(
        good_preserved_close.returncode == 0,
        good_preserved_close.stderr.strip()
        or "runtime verifier must accept a preserved pre-I/O terminal verdict at close",
        failures,
    )

    storm_lines = [
        f"07-01 21:01:00.{millis:03d} connection(0xa4) connecting via proxy avito.mosru.v6.rocks:443 secret[37] secret_kind=ee"
        for millis in range(0, 33, 3)
    ]
    bad_reconnect_storm = run_verifier(base_log(*storm_lines))
    require(
        bad_reconnect_storm.returncode != 0
        and "reconnect storm" in bad_reconnect_storm.stderr,
        "runtime verifier must reject a single connection re-dialing the proxy faster than reconnect backoff allows",
        failures,
    )

    good_paced_reconnects = run_verifier(
        base_log(
            *[
                f"07-01 21:02:{second:02d}.000 connection(0xa5) connecting via proxy avito.mosru.v6.rocks:443 secret[37] secret_kind=ee"
                for second in range(0, 10, 2)
            ]
        )
    )
    require(
        good_paced_reconnects.returncode == 0,
        good_paced_reconnects.stderr.strip()
        or "runtime verifier must accept backoff-paced reconnect attempts",
        failures,
    )

    good_success_breakthrough = run_verifier(
        base_log(
            "07-01 21:00:30.000 proxy_control decision=visible_only source=native_stage origin=active_socket account=0 phase=server_hello_hmac_mismatch endpoint=avito.mosru.v6.rocks:443:ee:avito.mosru.v6.rocks probe=avito.mosru.v6.rocks:443:secret_hash=aaaaaaaaaaaaaaaa:avito.mosru.v6.rocks",
            "07-01 21:00:30.010 proxy_control decision=visible_only source=native_stage origin=active_socket account=0 phase=server_hello_hmac_ok endpoint=avito.mosru.v6.rocks:443:ee:avito.mosru.v6.rocks probe=avito.mosru.v6.rocks:443:secret_hash=aaaaaaaaaaaaaaaa:avito.mosru.v6.rocks",
        )
    )
    require(
        good_success_breakthrough.returncode == 0,
        good_success_breakthrough.stderr.strip() or "runtime verifier must allow real FakeTLS progress to break fresh exact failures",
        failures,
    )


def main() -> int:
    failures: list[str] = []
    coordinator_h = read(TGNET.parent / "mtproxy/MtProxyProbeCoordinator.h")
    coordinator_cpp = read(TGNET.parent / "mtproxy/MtProxyProbeCoordinator.cpp")
    socket_cpp = read(TGNET / "ConnectionSocket.cpp")
    connection_cpp = read(TGNET / "Connection.cpp")
    phase_contract_h = read(TGNET.parent / "mtproxy/MtProxyPhaseContract.h")
    phase_contract_py = read(TOOLS / "mtproxy_phase_contract.py")
    diagnostics = read(MESSENGER / "ProxyCheckDiagnostics.java")
    phase_policy = read(MESSENGER / "ProxyPhasePolicy.java")
    analyzer = read(TOOLS / "analyze_mtproxy_markers.py")
    verifier = read(VERIFIER)
    values = read(VALUES)
    values_ru = read(VALUES_RU)

    require("HandshakeBudgetBackoff" in coordinator_h, "coordinator must expose HandshakeBudgetBackoff decision", failures)
    require("FakeTlsHandshakeBudget" in coordinator_cpp, "coordinator must own FakeTlsHandshakeBudget state", failures)
    require("uint32_t activationGeneration" in coordinator_h and "activationGeneration" in coordinator_cpp, "ProbeKey must carry activationGeneration", failures)
    require("responseSignature" in coordinator_h and "responseSignature" in coordinator_cpp, "completeFailure must accept and report responseSignature", failures)
    require("terminalBudgetExhausted" in coordinator_h and "terminalPhase" in coordinator_h, "FailureResult must expose terminal budget verdict fields", failures)
    require("MT_PROXY_FAKETLS_BUDGET_HOLD_MS" in coordinator_cpp and "30 * 1000" in coordinator_cpp, "terminal budget hold must be 30 seconds", failures)
    require("MT_PROXY_FAKETLS_BUDGET_WINDOW_MS" in coordinator_cpp and "8000" in coordinator_cpp, "FakeTLS budget wall-clock window must be 8 seconds", failures)
    require("MT_PROXY_FAKETLS_BUDGET_MAX_OWNER_ATTEMPTS" in coordinator_cpp and "3" in coordinator_cpp, "FakeTLS budget must cap owner attempts at 3", failures)
    require("MT_PROXY_FAKETLS_BUDGET_REPEATED_SIGNATURE_LIMIT" in coordinator_cpp and "2" in coordinator_cpp, "bad-flight budget must cap repeated signatures at 2", failures)
    require("HandshakeBudgetBackoff" in socket_cpp and "terminalBudgetExhausted" in socket_cpp, "ConnectionSocket must stop before TCP on budget backoff and publish terminal budget exhaustion", failures)
    require("mtProxyFailureResponseSignature" in socket_cpp, "ConnectionSocket must compute stable response signatures for post-ClientHello bytes", failures)
    require("ProxyStatusFaketlsHandshakeFailedShort" in values and "MTProxy/FakeTLS handshake failed" in values, "English strings must define the short FakeTLS terminal user-facing text", failures)
    require("ProxyStatusFaketlsHandshakeFailedShort" in values_ru and "Сервер доступен по TCP, но MTProxy/FakeTLS рукопожатие не прошло." in values_ru, "Russian strings must define the short FakeTLS terminal user-facing text", failures)
    require("isFakeTlsTerminalHandshakeFailure" in diagnostics and "ProxyStatusFaketlsHandshakeFailedShort" in diagnostics, "terminal FakeTLS failures must use a short shared title/list text while keeping detailed diagnosticText resources", failures)

    classification_h = read(TGNET.parent / "mtproxy/MtProxyPhaseClassification.h")
    helper_start = classification_h.find("inline bool isPreIoTerminalVerdict")
    helper_body = (
        classification_h[helper_start:classification_h.find("\n}", helper_start)]
        if helper_start >= 0
        else ""
    )
    require(
        helper_start >= 0,
        "MtProxyPhaseClassification.h must define the generated MtProxyPhase::isPreIoTerminalVerdict",
        failures,
    )
    backoff_start = classification_h.find("inline bool needsReconnectBackoff")
    backoff_body = (
        classification_h[backoff_start:classification_h.find("\n}", backoff_start)]
        if backoff_start >= 0
        else ""
    )
    require(
        "MtProxyPhase::needsReconnectBackoff(diagnostic)" in connection_cpp,
        "Connection.cpp must classify reconnect backoff via the generated phase classification",
        failures,
    )
    # Unified hold: the coordinator's terminal-hold clock must reach the
    # Connection reconnect timer instead of being re-derived from strings.
    require(
        "decision.waitMs = (uint32_t) (state.fakeTlsHandshakeBudget.terminalUntilMs - now);" in coordinator_cpp
        and "decision.waitMs = (uint32_t) (state.profilesExhaustedUntil - now);" in coordinator_cpp,
        "coordinator backoff decisions must carry the remaining terminal hold in waitMs",
        failures,
    )
    require(
        socket_cpp.count("proxySuggestedReconnectHoldMs = probeDecision.waitMs;") >= 2,
        "both pre-TCP backoff branches must capture the coordinator hold for the Connection layer",
        failures,
    )
    retry_authority_cpp = read(TGNET.parent / "mtproxy/MtProxyRetryAuthority.cpp")
    require(
        "consumeSuggestedReconnectHoldMs()" in connection_cpp
        and "holdInput.coordinatorHoldMs = mtProxySuggestedHoldMs" in connection_cpp
        and "input.coordinatorHoldMs > decision.delayMs" in retry_authority_cpp,
        "Connection reconnect backoff must feed the coordinator hold into MtProxyRetryAuthority, which waits out the longer clock",
        failures,
    )
    derive_start = socket_cpp.find("std::string ConnectionSocket::deriveMtProxyTerminalDiagnostic")
    derive_body = (
        socket_cpp[derive_start:socket_cpp.find("\n}", derive_start)]
        if derive_start >= 0
        else ""
    )
    terminal_module_cpp = read(TGNET.parent / "mtproxy/MtProxyTerminalDiagnostic.cpp")
    require(
        "MtProxyPhase::deriveTerminalDiagnostic(terminalInput)" in derive_body
        and "isPreIoTerminalVerdict(current.c_str())" in terminal_module_cpp,
        "deriveMtProxyTerminalDiagnostic must preserve pre-I/O terminal verdicts via the shared helper",
        failures,
    )
    for phase in PRE_IO_TERMINAL_PHASES:
        require(
            f'"{phase}"' in helper_body,
            f"MtProxyPhase::isPreIoTerminalVerdict must preserve {phase} across closeSocket "
            "(otherwise the diagnostic is clobbered to connection_not_started and reconnect hot-loops)",
            failures,
        )
        require(
            f'"{phase}"' in backoff_body,
            f"generated needsReconnectBackoff must hold on {phase}",
            failures,
        )
        contract_line = next(
            (line for line in phase_contract_py.splitlines() if f'MtProxyPhase("{phase}"' in line),
            "",
        )
        require(
            "pre_io_terminal=True" in contract_line,
            f"Python phase contract must flag {phase} as pre_io_terminal",
            failures,
        )

    for phase, resource in TERMINAL_PHASES.items():
        require(phase in phase_contract_h, f"native phase contract must expose {phase}", failures)
        require(phase in phase_contract_py, f"Python phase contract must expose {phase}", failures)
        require(phase.upper() in diagnostics or phase in diagnostics, f"ProxyCheckDiagnostics must expose {phase}", failures)
        require(phase.upper() in phase_policy or phase in phase_policy, f"ProxyPhasePolicy must classify {phase}", failures)
        require(phase in analyzer, f"analyzer must understand {phase}", failures)
        require(phase in verifier, f"runtime verifier must understand {phase}", failures)
        require(resource in values, f"English strings must define {resource}", failures)
        require(resource in values_ru, f"Russian strings must define {resource}", failures)

    weak_method_start = diagnostics.find("public static boolean isWeakRetryLivePhase")
    weak_method_end = diagnostics.find("public static boolean isFreshFailureBreakthroughPhase", weak_method_start)
    weak_method = diagnostics[weak_method_start:weak_method_end if weak_method_end >= 0 else len(diagnostics)]
    require("ADMISSION_HOLD_AFTER_CLIENT_HELLO_FAILURE" in weak_method, "admission_hold_after_client_hello_failure must be weak retry/live telemetry", failures)
    require("CLIENT_HELLO_SENT" in weak_method, "client_hello_sent must be weak retry/live telemetry", failures)
    breakthrough_start = diagnostics.find("public static boolean isFreshFailureBreakthroughPhase")
    breakthrough_end = diagnostics.find("public static boolean hasFreshLivePhase", breakthrough_start)
    breakthrough_method = diagnostics[breakthrough_start:breakthrough_end if breakthrough_end >= 0 else len(diagnostics)]
    keep_failure_start = diagnostics.find("private static boolean shouldKeepFreshFailure")
    keep_failure_end = diagnostics.find("static long freshFailureHoldEarlyRetryMs", keep_failure_start)
    keep_failure_method = diagnostics[keep_failure_start:keep_failure_end if keep_failure_end >= 0 else len(diagnostics)]
    require("isFreshFailureBreakthroughPhase(incomingDiagnostic)" in keep_failure_method, "fresh failure hold must explicitly allow real success/progress breakthroughs", failures)
    for constant in FRESH_FAILURE_BREAKTHROUGH_PHASES:
        require(constant in breakthrough_method, f"{constant} must break fresh failure hold", failures)
        require(constant not in weak_method, f"{constant} must not be classified as weak retry/live telemetry", failures)

    for phase in RETRY_LIVE_PHASES:
        require(phase in verifier, f"runtime verifier must cover retry/live overwrite phase {phase}", failures)

    verify_runtime_replays(failures)

    if failures:
        print("MTProxy FakeTLS budget guard failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1
    print("MTProxy FakeTLS budget guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
