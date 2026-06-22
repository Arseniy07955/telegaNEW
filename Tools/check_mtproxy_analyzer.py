#!/usr/bin/env python3
from pathlib import Path
import csv
import re
import subprocess
import sys
import tempfile

from analyze_mtproxy_markers import Attempt


ROOT = Path(__file__).resolve().parents[1]
ANALYZER = ROOT / "Tools/analyze_mtproxy_markers.py"
SOCKET = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
DIAGNOSTICS = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckDiagnostics.java"


def require(condition, message):
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def check_phase_contract():
    socket = SOCKET.read_text(encoding="utf-8", errors="replace")
    analyzer = ANALYZER.read_text(encoding="utf-8", errors="replace")
    diagnostics = DIAGNOSTICS.read_text(encoding="utf-8", errors="replace")

    diagnostic_values = set(re.findall(r'public static final String [A-Z0-9_]+ = "([a-z0-9_]+)";', diagnostics))
    native_published = set(re.findall(r'publishProxyConnectionStage\("([a-z0-9_]+)"\)', socket))
    native_terminal = set(re.findall(r'proxyCheckDiagnostic = "([a-z0-9_]+)";', socket))
    mtproxy_terminal = native_terminal - {"wss_tls_handshake"}

    require(
        not (native_published - diagnostic_values),
        f"Java proxy diagnostics must know every native-published MTProxy phase: {sorted(native_published - diagnostic_values)}",
    )
    require(
        not (mtproxy_terminal - diagnostic_values),
        f"Java proxy diagnostics must know every native MTProxy terminal phase: {sorted(mtproxy_terminal - diagnostic_values)}",
    )
    require(
        "wss_tls_handshake" not in diagnostic_values,
        "WSS TLS handshake is a WSS diagnostic and must not be mixed into the MTProxy GUI diagnostic map",
    )
    missing_analyzer = sorted(phase for phase in native_published | mtproxy_terminal if phase not in analyzer)
    require(
        not missing_analyzer,
        f"MTProxy analyzer must know every native MTProxy phase used by GUI/logs: {missing_analyzer}",
    )


def main():
    check_phase_contract()

    attempt = Attempt(key="synthetic")
    attempt.add(1, "connection(0x1) mtproxy_startup socket_connect_start ipv6=0 state=0")
    attempt.add(2, "connection(0x1) mtproxy_startup on_connected tls=0")
    require(
        attempt.verdict() == "connected_without_socket_connected_marker",
        "an attempt that reached on_connected must not be reported as tcp_not_connected",
    )

    suppressed_drop = Attempt(key="suppressed-drop")
    suppressed_drop.add(1, "connection(0x11) mtproxy_startup socket_connect_start ipv6=0 state=10")
    suppressed_drop.add(2, "connection(0x11) mtproxy_startup socket_connected elapsed=70")
    suppressed_drop.add(3, "connection(0x11) mtproxy_startup client_hello_sent bytes=1897")
    suppressed_drop.add(4, "connection(0x11) mtproxy_startup server_hello_hmac_ok bytes=2219 len1=1210 len2=993 flight=993 extra=0")
    suppressed_drop.add(5, "connection(0x11) mtproxy_startup on_connected tls=1")
    suppressed_drop.add(6, "connection(0x11) mtproxy_startup first_tls_app_recv payload=105")
    suppressed_drop.add(
        7,
        "connection(0x11) mtproxy_startup close_diagnostic_suppressed "
        "phase=dropped_after_appdata reason=peer_closed first_tls_sent=1 first_tls_recv=1 first_plain_sent=0 first_plain_recv=0",
    )
    require(
        suppressed_drop.verdict() == "ok",
        "suppressed close diagnostics after first app-data must not turn a usable connection into a drop",
    )

    idle_after_handshake = Attempt(key="idle-after-handshake")
    idle_after_handshake.add(1, "connection(0x12) mtproxy_startup socket_connect_start ipv6=0 state=10")
    idle_after_handshake.add(2, "connection(0x12) mtproxy_startup socket_connected elapsed=70")
    idle_after_handshake.add(3, "connection(0x12) mtproxy_startup client_hello_sent bytes=1897")
    idle_after_handshake.add(4, "connection(0x12) mtproxy_startup server_hello_hmac_ok bytes=2219 len1=1210 len2=993 flight=993 extra=0")
    idle_after_handshake.add(5, "connection(0x12) mtproxy_startup on_connected tls=1")
    idle_after_handshake.add(
        6,
        "connection(0x12) mtproxy_startup close_diagnostic_suppressed "
        "phase=post_handshake_no_appdata reason=peer_closed first_tls_sent=0 first_tls_recv=0 first_plain_sent=0 first_plain_recv=0",
    )
    require(
        idle_after_handshake.verdict() == "handshake_ok_no_appdata_sent",
        "idle post-handshake sockets that never sent app-data must not be reported as post_handshake_no_appdata",
    )

    reused_pointer_latency = Attempt(key="reused-pointer-latency")
    reused_pointer_latency.add(
        1,
        "06-20 15:00:02.000 connection(0x13) mtproxy_startup client_hello_sent bytes=1897",
    )
    reused_pointer_latency.add(
        2,
        "06-20 15:00:01.900 connection(0x13) mtproxy_startup server_hello_hmac_ok bytes=2219",
    )
    require(
        reused_pointer_latency.timing_ms("client_hello_sent", "server_hello_hmac_ok") == "",
        "analyzer must ignore negative event latency caused by reused pointers or out-of-order marker grouping",
    )

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        marker_path = Path(handle.name)
        handle.write("logcat.txt:1: connection(0x1) mtproxy_startup socket_connect_start ipv6=0 state=0\n")
        handle.write("logcat.txt:2: connection(0x1) mtproxy_startup on_connected tls=0\n")
        handle.write(
            "logcat.txt:3: 06-20 15:00:00.000 connection(0x2) mtproxy_startup connect_start proxy_state=10 secret_kind=ee "
            "is_faketls=1 domain_len=17 profile=android_chrome connection_pattern=strict address=203.0.113.10 port=443\n"
        )
        handle.write(
            "logcat.txt:3: 06-20 15:00:00.010 connection(0x2) mtproxy_startup admission_queue "
            "admission_mode=strict connection_pattern=strict key=blocked.example:443:cdn.example "
            "priority=20 active=1 max=1 queued=1\n"
        )
        handle.write("logcat.txt:4: connection(0x2) mtproxy_startup socket_connect_start ipv6=0 state=10\n")
        handle.write("logcat.txt:5: connection(0x2) mtproxy_startup socket_connected elapsed=90\n")
        handle.write("logcat.txt:6: connection(0x2) mtproxy_startup client_hello_sent bytes=1897\n")
        handle.write(
            "logcat.txt:7: connection(0x2) mtproxy_startup admission_freeze_detected "
            "key=blocked.example:443:cdn.example elapsed=4500\n"
        )
        handle.write(
            "logcat.txt:7: connection(0x2) mtproxy_startup admission_hold_after_client_hello_failure "
            "admission_mode=strict connection_pattern=strict reason=freeze_timeout "
            "key=blocked.example:443:cdn.example queued=2 cooldown_ms=5200\n"
        )
        handle.write("logcat.txt:8: connection(0x2) mtproxy_startup server_hello_timeout_close elapsed=4500\n")
        handle.write(
            "logcat.txt:9: 06-20 15:00:00.200 connection(0x2) mtproxy_startup connect_start proxy_state=10 secret_kind=ee "
            "is_faketls=1 domain_len=17 profile=android_chrome connection_pattern=strict address=203.0.113.10 port=443\n"
        )
        handle.write(
            "logcat.txt:9: 06-20 15:00:00.200 connection(0x2) mtproxy_startup admission_grant "
            "admission_mode=strict connection_pattern=strict key=blocked.example:443:cdn.example priority=20 active=1 max=1\n"
        )
        handle.write(
            "logcat.txt:9: 06-20 15:00:00.250 connection(0x2) mtproxy_startup admission_tcp_failure_cooldown "
            "admission_mode=strict connection_pattern=strict reason=closeSocket key=blocked.example:443:cdn.example "
            "penalty=1 cooldown_ms=5200\n"
        )
        handle.write("logcat.txt:10: connection(0x2) mtproxy_startup socket_connect_start ipv6=0 state=10\n")
        handle.write("logcat.txt:11: connection(0x2) mtproxy_startup socket_connected elapsed=80\n")
        handle.write("logcat.txt:12: connection(0x2) mtproxy_startup client_hello_sent bytes=1897\n")
        handle.write("logcat.txt:13: connection(0x2) mtproxy_startup server_hello_hmac_ok bytes=2219 len1=1210 len2=993 flight=993 extra=0\n")
        handle.write("logcat.txt:14: connection(0x2) mtproxy_startup on_connected tls=1\n")
        handle.write("logcat.txt:14: connection(0x2) mtproxy_data tls_frame_complete index=1 payload=96 frame=101 record_sizing=1 timing=1 startup_cover=1 more_data=1\n")
        handle.write("logcat.txt:15: connection(0x2) mtproxy_startup first_tls_app_recv payload=105\n")
        handle.write("logcat.txt:15: connection(0x2) mtproxy_disconnect reason=2 reason_text=peer_closed error=0 error_text=ok secret_kind=ee is_faketls=1 is_wss=0 proxy_state=0 tls_state=0 bytes_read=512 pending_hello=0/0 pending=0/0 first_tls_sent=1 first_tls_recv=1 first_plain_sent=0 first_plain_recv=0 tls_frames_completed=3\n")
        handle.write("logcat.txt:15: proxy_connection_stage account=0 phase=client_hello_sent\n")
        handle.write("logcat.txt:15: proxy_connection_stage account=0 phase=server_hello_hmac_ok\n")
        handle.write(
            "logcat.txt:15: 06-20 15:00:01.000 connection(0x5) mtproxy_startup connect_start proxy_state=10 secret_kind=ee "
            "is_faketls=1 domain_len=17 profile=android_chrome connection_pattern=strict address=198.51.100.55 port=443\n"
        )
        handle.write("logcat.txt:15: connection(0x5) mtproxy_startup socket_connect_start ipv6=0 state=10\n")
        handle.write("logcat.txt:15: connection(0x5) mtproxy_startup socket_connected elapsed=80\n")
        handle.write("logcat.txt:15: connection(0x5) mtproxy_startup client_hello_sent bytes=1897\n")
        handle.write("logcat.txt:15: connection(0x5) mtproxy_startup server_hello_hmac_ok bytes=2219 len1=1210 len2=993 flight=993 extra=0\n")
        handle.write("logcat.txt:15: connection(0x5) mtproxy_startup on_connected tls=1\n")
        handle.write("logcat.txt:15: connection(0x5) mtproxy_startup first_tls_app_recv payload=105\n")
        handle.write("logcat.txt:15: connection(0x5) mtproxy_startup close_diagnostic phase=dropped_early_after_appdata\n")
        handle.write(
            "logcat.txt:15: 06-20 15:00:01.200 connection(0x7) mtproxy_startup connect_start proxy_state=10 secret_kind=ee "
            "is_faketls=1 domain_len=17 profile=android_chrome connection_pattern=strict address=198.51.100.77 port=443\n"
        )
        handle.write("logcat.txt:15: connection(0x7) mtproxy_startup socket_connect_start ipv6=0 state=10\n")
        handle.write("logcat.txt:15: connection(0x7) mtproxy_startup socket_connected elapsed=80\n")
        handle.write("logcat.txt:15: connection(0x7) mtproxy_startup client_hello_sent bytes=1897\n")
        handle.write("logcat.txt:15: connection(0x7) mtproxy_startup server_hello_hmac_ok bytes=2219 len1=1210 len2=993 flight=993 extra=0\n")
        handle.write("logcat.txt:15: connection(0x7) mtproxy_startup on_connected tls=1\n")
        handle.write(
            "logcat.txt:15: connection(0x7) mtproxy_startup close_diagnostic_suppressed "
            "phase=post_handshake_no_appdata reason=peer_closed first_tls_sent=0 first_tls_recv=0 first_plain_sent=0 first_plain_recv=0\n"
        )
        handle.write(
            "logcat.txt:15: 06-20 15:00:01.300 connection(0x8) mtproxy_startup connect_start proxy_state=10 secret_kind=ee "
            "is_faketls=1 domain_len=17 profile=android_chrome connection_pattern=strict address=198.51.100.88 port=443\n"
        )
        handle.write("logcat.txt:15: connection(0x8) mtproxy_startup socket_connect_start ipv6=0 state=10\n")
        handle.write("logcat.txt:15: connection(0x8) mtproxy_startup socket_connected elapsed=80\n")
        handle.write("logcat.txt:15: connection(0x8) mtproxy_startup client_hello_sent bytes=1897\n")
        handle.write("logcat.txt:15: connection(0x8) mtproxy_startup server_hello_hmac_ok bytes=2219 len1=1210 len2=993 flight=993 extra=0\n")
        handle.write("logcat.txt:15: connection(0x8) mtproxy_startup on_connected tls=1\n")
        handle.write("logcat.txt:15: connection(0x8) mtproxy_startup first_tls_app_recv payload=105\n")
        handle.write(
            "logcat.txt:15: connection(0x8) mtproxy_startup close_diagnostic_suppressed "
            "phase=dropped_after_appdata reason=peer_closed first_tls_sent=1 first_tls_recv=1 first_plain_sent=0 first_plain_recv=0\n"
        )
        handle.write(
            "logcat.txt:15: 06-20 15:00:01.500 connection(0x6) mtproxy_startup connect_start proxy_state=10 secret_kind=ee "
            "is_faketls=1 domain_len=17 profile=android_chrome connection_pattern=strict address=198.51.100.66 port=443\n"
        )
        handle.write("logcat.txt:15: connection(0x6) mtproxy_startup host_resolve_failed host=blocked-dns.example reason=no_delegate\n")
        handle.write(
            "logcat.txt:16: proxy_check_start state=ping_sent ping_id=1 request_token=1 "
            "address=dead.example:443 connection_num=0\n"
        )
        handle.write("logcat.txt:17: proxy_check_connection_closed close_reason=2 ping_id=1 request_token=1 connection_num=0 state=1\n")
        handle.write(
            "logcat.txt:18: proxy_check_finish result=fail reason=connection_closed request_found=1 "
            "ping_id=1 address=dead.example:443 connection_num=0 state=finished\n"
        )
        handle.write(
            "logcat.txt:19: proxy_check_start state=ping_sent ping_id=2 request_token=2 "
            "address=frozen.example:443 connection_num=0\n"
        )
        handle.write("logcat.txt:20: proxy_check_socket_connected ping_id=2 request_token=2 connection_num=0 connection_token=2\n")
        handle.write("logcat.txt:21: proxy_check_connection_closed close_reason=2 ping_id=2 request_token=2 connection_num=0 state=1\n")
        handle.write(
            "logcat.txt:22: proxy_check_finish result=fail reason=connection_closed request_found=1 "
            "ping_id=2 address=frozen.example:443 connection_num=0 state=finished\n"
        )
        handle.write(
            "logcat.txt:23: proxy_check_start state=ping_sent ping_id=3 request_token=3 "
            "address=ok.example:443 connection_num=0\n"
        )
        handle.write("logcat.txt:24: proxy_check_socket_connected ping_id=3 request_token=3 connection_num=0 connection_token=3\n")
        handle.write(
            "logcat.txt:25: proxy_check_finish result=ok reason=pong request_found=1 "
            "ping_id=3 address=ok.example:443 connection_num=0 state=finished\n"
        )
        handle.write("logcat.txt:25: proxy_check_scheduler enqueue endpoint=dead.example:443 queued=1\n")
        handle.write("logcat.txt:25: proxy_check_scheduler start endpoint=dead.example:443 queued=0\n")
        handle.write(
            "logcat.txt:25: proxy_check_scheduler finish result=fail phase=tcp_not_connected "
            "diagnostic=network_block_suspected time=-1 applied_time=-1 raw_time=-1 "
            "endpoint=dead.example:443 queued=0 cancelled=false listeners=1\n"
        )
        handle.write(
            "logcat.txt:25: proxy_check_scheduler backoff endpoint=dead.example:443 "
            "wait_ms=120000 failures=2 phase=network_block_suspected source=proxy_check\n"
        )
        handle.write(
            "logcat.txt:25: proxy_check_scheduler skip_backoff endpoint=dead.example:443 "
            "wait_ms=119000 phase=network_block_suspected\n"
        )
        handle.write("logcat.txt:25: proxy_check_scheduler finish_keep_connected endpoint=ok.example:443\n")
        handle.write(
            "logcat.txt:26: 06-20 15:00:02.000 connection(0x3, account0, dc2, type 1) connecting "
            "(149.154.167.51:443)\n"
        )
        handle.write(
            "logcat.txt:27: connection(0x3) connecting via proxy plain.example:443 "
            "secret[17] secret_kind=dd\n"
        )
        handle.write(
            "logcat.txt:28: 06-20 15:00:02.050 connection(0x3) mtproxy_startup connect_start proxy_state=0 "
            "secret_kind=dd is_faketls=0 domain_len=0 profile=android_chrome address=149.154.167.51 port=443\n"
        )
        handle.write("logcat.txt:28: connection(0x3) mtproxy_startup socket_connected state=0 tls=0 secret_kind=dd\n")
        handle.write("logcat.txt:29: connection(0x3) mtproxy_startup on_connected tls=0\n")
        handle.write("logcat.txt:30: connection(0x3) mtproxy_startup first_mtproxy_packet_sent bytes=128 secret_kind=dd\n")
        handle.write("logcat.txt:31: connection(0x3, account0, dc2, type 1) send message invokeWithLayer\n")
        handle.write("logcat.txt:32: connection(0x3) mtproxy_startup first_mtproxy_packet_recv bytes=98 secret_kind=dd\n")
        handle.write("logcat.txt:33: connection(0x3, account0, dc2, type 1) received message len 98\n")
        handle.write("logcat.txt:32: connection(0x3, account0, dc2, type 1) received object TL_rpc_result\n")
        handle.write("logcat.txt:33: connection(0x3, account0, dc2, type 1) received rpc_result with TL_boolTrue\n")
        handle.write(
            "logcat.txt:34: 06-20 15:00:03.000 connection(0x4, account0, dc2, type 2) connecting "
            "(149.154.167.51:443)\n"
        )
        handle.write(
            "logcat.txt:35: connection(0x4) connecting via proxy plain.example:443 "
            "secret[17] secret_kind=dd\n"
        )
        handle.write(
            "logcat.txt:36: 06-20 15:00:03.050 connection(0x4) mtproxy_startup connect_start proxy_state=0 "
            "secret_kind=dd is_faketls=0 domain_len=0 profile=android_chrome address=149.154.167.51 port=443\n"
        )
        handle.write("logcat.txt:36: connection(0x4) mtproxy_startup socket_connected state=0 tls=0 secret_kind=dd\n")
        handle.write("logcat.txt:37: connection(0x4) mtproxy_startup on_connected tls=0\n")
        handle.write("logcat.txt:38: connection(0x4) mtproxy_startup first_mtproxy_packet_sent bytes=96 secret_kind=dd\n")
        handle.write("logcat.txt:39: connection(0x4, account0, dc2, type 2) send message getFile\n")
        handle.write("logcat.txt:39: connection(0x4, account0, dc2, type 2) reset auth key due to -404 error\n")
        handle.write("logcat.txt:40: connection(0x4, account0, dc2, type 2) received invalid packet length\n")
        handle.write("logcat.txt:40: connection(0x4) mtproxy_startup close_diagnostic phase=mtproxy_packet_sent_no_response\n")
        handle.write("logcat.txt:41: connection(0x4, account0, dc2, type 2) disconnected with reason 2\n")
    try:
        with tempfile.TemporaryDirectory() as csv_dir:
            result = subprocess.run(
                [sys.executable, str(ANALYZER), str(marker_path), "--out-dir", csv_dir],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            attempts_csv = Path(csv_dir) / "mtproxy_attempts.csv"
            endpoint_csv = Path(csv_dir) / "mtproxy_endpoint_profile_stats.csv"
            proxy_check_csv = Path(csv_dir) / "mtproxy_proxy_check_stats.csv"
            scheduler_csv = Path(csv_dir) / "mtproxy_scheduler_stats.csv"
            require(attempts_csv.exists(), "analyzer must write per-attempt CSV when --out-dir is passed")
            require(endpoint_csv.exists(), "analyzer must write endpoint/profile stats CSV when --out-dir is passed")
            require(proxy_check_csv.exists(), "analyzer must write proxy-check endpoint stats CSV when --out-dir is passed")
            require(scheduler_csv.exists(), "analyzer must write Java scheduler endpoint stats CSV when --out-dir is passed")
            endpoint_csv_text = endpoint_csv.read_text(encoding="utf-8")
            proxy_check_csv_text = proxy_check_csv.read_text(encoding="utf-8")
            scheduler_csv_text = scheduler_csv.read_text(encoding="utf-8")
            require(
                "blocked.example:443" in endpoint_csv_text,
                "endpoint/profile CSV must include FakeTLS endpoint names",
            )
            require(
                "early_drop" in endpoint_csv_text.splitlines()[0],
                "endpoint/profile CSV must expose early_drop as a compact aggregate column",
            )
            require(
                "tls_frames_completed" in endpoint_csv_text.splitlines()[0],
                "endpoint/profile CSV must expose completed FakeTLS record count for data-path diagnosis",
            )
            rows = list(csv.DictReader(endpoint_csv_text.splitlines()))
            blocked_row = next((row for row in rows if row["endpoint"] == "blocked.example:443" and row["profile"] == "android_chrome"), None)
            require(
                blocked_row is not None and blocked_row["tls_frames_completed"] == "3",
                "endpoint/profile CSV must prefer the final disconnect FakeTLS record count when per-frame markers are sampled",
            )
            early_drop_row = next((row for row in rows if row["endpoint"] == "198.51.100.55:443" and row["profile"] == "android_chrome"), None)
            require(
                early_drop_row is not None and early_drop_row["early_drop"] == "1",
                "endpoint/profile CSV must count early post-appdata drops in the compact early_drop column",
            )
            proxy_check_rows = list(csv.DictReader(proxy_check_csv_text.splitlines()))
            dead_proxy_row = next((row for row in proxy_check_rows if row["endpoint"] == "dead.example:443"), None)
            frozen_proxy_row = next((row for row in proxy_check_rows if row["endpoint"] == "frozen.example:443"), None)
            ok_proxy_row = next((row for row in proxy_check_rows if row["endpoint"] == "ok.example:443"), None)
            require(
                dead_proxy_row is not None and dead_proxy_row["tcp_not_connected"] == "1",
                "proxy-check CSV must expose endpoint TCP-open failures separately from FakeTLS attempts",
            )
            require(
                frozen_proxy_row is not None and frozen_proxy_row["tcp_connected_no_pong"] == "1",
                "proxy-check CSV must expose endpoint post-TCP/no-pong failures",
            )
            require(
                ok_proxy_row is not None and ok_proxy_row["ok"] == "1",
                "proxy-check CSV must expose endpoint pong successes",
            )
            scheduler_rows = list(csv.DictReader(scheduler_csv_text.splitlines()))
            dead_scheduler_row = next((row for row in scheduler_rows if row["endpoint"] == "dead.example:443"), None)
            ok_scheduler_row = next((row for row in scheduler_rows if row["endpoint"] == "ok.example:443"), None)
            require(
                dead_scheduler_row is not None
                and dead_scheduler_row["enqueue"] == "1"
                and dead_scheduler_row["start"] == "1"
                and dead_scheduler_row["finish_fail"] == "1"
                and dead_scheduler_row["backoff"] == "1"
                and dead_scheduler_row["skip_backoff"] == "1"
                and dead_scheduler_row["phase_network_block_suspected"] == "3",
                "scheduler CSV must expose endpoint queue/start/fail/backoff phases for diagnosing retry storms",
            )
            require(
                ok_scheduler_row is not None and ok_scheduler_row["finish_keep_connected"] == "1",
                "scheduler CSV must expose preserved-connected outcomes separately from failures",
            )
    finally:
        marker_path.unlink(missing_ok=True)

    require(result.returncode == 0, result.stderr.strip() or "analyzer exited with failure")
    require(
        "connected_without_socket_connected_marker:" in result.stdout,
        "analyzer summary must expose connected attempts that are missing the socket_connected marker",
    )
    require(
        "tcp_not_connected: 1" not in result.stdout,
        "analyzer must not classify on_connected attempts as TCP failures",
    )
    require(
        "client_hello_sent_no_server_hello: 1" in result.stdout,
        "analyzer must classify a connected ClientHello timeout as a pre-ServerHello failure",
    )
    require(
        "dropped_early_after_appdata: 1" in result.stdout,
        "analyzer must classify quick drops after first app data as a distinct post-handshake endpoint/lifecycle phase",
    )
    require(
        "handshake_ok_no_appdata_sent: 1" in result.stdout,
        "analyzer must expose idle post-handshake sockets separately from data-path failures",
    )
    require(
        "FakeTLS reliability:" in result.stdout and "ok_rate=" in result.stdout,
        "analyzer must print profile reliability percentages for comparing profiles",
    )
    require(
        "early_drop=1" in result.stdout,
        "analyzer profile reliability summary must expose early post-appdata drops as a first-class counter",
    )
    require(
        "tls_frames=3" in result.stdout,
        "analyzer profile reliability summary must prefer final disconnect FakeTLS record counts over sampled per-frame markers",
    )
    require(
        "Endpoint handshake bursts:" in result.stdout,
        "analyzer must expose per-endpoint handshake bursts",
    )
    require(
        "By connection pattern:" in result.stdout and "strict:" in result.stdout,
        "analyzer must summarize FakeTLS attempts by connection-pattern mode",
    )
    require(
        result.stdout.count("  By connection pattern:") == 1,
        "analyzer must not print the connection-pattern section header twice",
    )
    require(
        "patterns=strict=" in result.stdout,
        "analyzer burst summary must include connection-pattern mix",
    )
    require(
        "admission_queue" in result.stdout,
        "analyzer must preserve the queued-admission marker so GUI 'waiting slot' stalls are visible in logs",
    )
    require(
        "admission_tcp_failure_cooldown" in result.stdout,
        "analyzer must preserve the pre-ClientHello TCP-failure cooldown marker",
    )
    require(
        "admission_hold_after_client_hello_failure" in result.stdout,
        "analyzer must preserve the queued-admission hold marker after post-ClientHello failures",
    )
    require(
        "Java live connection stages:" in result.stdout
        and "client_hello_sent: 1" in result.stdout
        and "server_hello_hmac_ok: 1" in result.stdout,
        "analyzer must summarize Java-side live proxy stage updates",
    )
    require(
        "blocked.example:443 client_hello_sent_no_server_hello: 1" in result.stdout,
        "analyzer must summarize FakeTLS phase verdicts by endpoint",
    )
    require(
        "198.51.100.55:443 dropped_early_after_appdata: 1" in result.stdout,
        "analyzer must include early post-appdata drops in endpoint phase summaries",
    )
    require(
        "198.51.100.77:443 handshake_ok_no_appdata_sent: 1" in result.stdout,
        "analyzer must classify suppressed idle post-handshake closes by endpoint without calling them data-path drops",
    )
    require(
        "198.51.100.88:443 ok: 1" in result.stdout
        and "198.51.100.88:443 dropped_after_appdata" not in result.stdout,
        "suppressed post-appdata close diagnostics must stay out of endpoint drop summaries",
    )
    require(
        "plain.example:443 android_chrome" not in result.stdout,
        "plain dd MTProxy attempts must not be reported in FakeTLS endpoint/profile phases",
    )
    require(
        "Plain MTProxy lifecycle:" in result.stdout,
        "analyzer must summarize non-FakeTLS MTProxy traffic separately from FakeTLS",
    )
    require(
        "Layer recommendations:" in result.stdout,
        "analyzer must print phase-to-layer recommendations so TCP/DNS, ClientHello, dd, and data-path failures are not confused",
    )
    require(
        "dns_endpoint_stability host_resolve_failed=1" in result.stdout
        and "proxy_check_tcp_not_connected=1" in result.stdout
        and "proxy_check_tcp_connected_no_pong=1" in result.stdout
        and "not_ja4_or_drs" in result.stdout,
        "analyzer recommendations must route native and proxy-check DNS/TCP failures to endpoint stability, not JA4/DRS",
    )
    require(
        "faketls_handshake_recipe client_hello_sent_no_server_hello=1" in result.stdout,
        "analyzer recommendations must route pre-ServerHello failures to the phase-adaptive FakeTLS recipe",
    )
    require(
        "plain_dd_endpoint_backoff mtproxy_packet_sent_no_response=1" in result.stdout
        and "dd_no_ja4" in result.stdout,
        "analyzer recommendations must route dd no-response to endpoint backoff/fallback, not FakeTLS JA4",
    )
    require(
        "faketls_data_path post_handshake_no_appdata=0 dropped_early_after_appdata=1 tls_frames_completed=3" in result.stdout,
        "analyzer recommendations must use completed FakeTLS records to identify post-handshake data-path failures",
    )
    require(
        "plain.example:443 dd account0 dc2 type1: socket_connected=1 connected=1 first_packet_sent=1 first_packet_recv=1 packet_sent_no_response=0 send=1 recv=1 rpc_result=1" in result.stdout,
        "plain MTProxy summary must show successful account/dc/type traffic",
    )
    require(
        "plain.example:443 dd account0 dc2 type2: socket_connected=1 connected=1 first_packet_sent=1 first_packet_recv=0 packet_sent_no_response=1 send=1 recv=0 rpc_result=0 invalid_packet_length=1 auth_404=1 disconnect_2=1" in result.stdout,
        "plain MTProxy summary must expose broken download/media lifecycle separately",
    )
    require(
        "dead.example:443 fail:tcp_not_connected close_reason=2: 1" in result.stdout,
        "proxy-check without socket_connected must be visible as a network/TCP-layer failure",
    )
    require(
        "frozen.example:443 fail:tcp_connected_no_pong close_reason=2: 1" in result.stdout,
        "proxy-check with TCP but without pong must be visible as a post-TCP no-response failure",
    )
    require(
        "ok.example:443 ok:pong: 1" in result.stdout,
        "proxy-check pong success must be visible per endpoint",
    )

    print("MTProxy analyzer guard passed.")


if __name__ == "__main__":
    main()
