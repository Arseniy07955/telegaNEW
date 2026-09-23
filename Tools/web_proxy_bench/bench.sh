#!/bin/bash
# WEB proxy bench: the app's org.telegram.proxy classes in a small harness APK
# on an Android emulator, a real WebView loading the real bridge page from a
# real tproxy-server (websocket carrier) behind nginx, all traffic shaped by
# netem. Only tgnet (Bench.java, a loopback workload) and Telegram
# (backend.py, a pipelined RPC server) are stand-ins. No Telegram account.
#
#   TPROXY_SERVER=/path/to/tproxy-server ./bench.sh up     # netns relay, backend, CONNECT proxy
#   WPB_AVD=<avd> ./bench.sh emulator                      # headless emulator (KVM)
#   ./bench.sh shape 150 50mbit                            # RTT ms, rate (or "none")
#   ./bench.sh build <dir-with-WebProxy*.java | git-rev> out.apk
#   ./bench.sh install out.apk                             # install + AOT compile
#   ./bench.sh run label up=32 upPart=524288 upParallel=64 # one run -> JSON line
#   ./bench.sh matrix OUT REPS apkA apkB ...               # conditions x workloads
#   ./bench.sh summary OUT
#   ./bench.sh down
#
# Run parameters (--es key value): mode=java|page, idle, dc (backend delay
# ms), up/down (MiB), upPart/upParallel/upConns, downPart/downParallel/
# downConns, ping (ms), uiload (busy ms per 16 ms frame), stuck (MiB for a
# connection whose reader stops), recover (seconds of reconnecting pings).
# Requires: sudo, ip/tc, nginx, openssl, python3, adb; Android SDK
# at $ANDROID_HOME (build-tools 36, platform 36) and androidx.webkit 1.14 in
# the Gradle cache.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
WORK=${WPB_WORK:-/tmp/wpbench}
NS=wpbench
PROXY_PORT=${WPB_PROXY_PORT:-28444}
SERIAL=emulator-${WPB_EMU_PORT:-5580}
SDK=${ANDROID_HOME:-/opt/android-sdk}
BT=$SDK/build-tools/36.0.0
AJ=$SDK/platforms/android-36/android.jar
export ANDROID_SERIAL=$SERIAL
mkdir -p "$WORK"

libs() {
    local L=$WORK/libs
    [ -f "$L/webkit.jar" ] && { echo "$L"; return; }
    mkdir -p "$L"
    local aar
    aar=$(find ~/.gradle/caches/modules-2/files-2.1/androidx.webkit/webkit/1.14.0 -name 'webkit-1.14.0.aar' | head -1)
    (cd "$L" && unzip -o -q "$aar" classes.jar && mv classes.jar webkit.jar)
    cp "$(find ~/.gradle/caches/modules-2/files-2.1/androidx.annotation/annotation-jvm -name 'annotation-jvm-*.jar' ! -name '*sources*' | head -1)" "$L/annotation.jar"
    echo "$L"
}

cert() {
    [ -f "$WORK/cert.pem" ] && return
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj /CN=w.bench.test \
        -addext subjectAltName=DNS:w.bench.test -addext basicConstraints=critical,CA:TRUE \
        -keyout "$WORK/key.pem" -out "$WORK/cert.pem" 2>/dev/null
}

cmd_up() {
    : "${TPROXY_SERVER:?set TPROXY_SERVER to a tproxy-server binary}"
    cert
    mkdir -p "$WORK/site" "$WORK/nginx/logs" "$WORK/nginx/tmp"
    echo "bench" > "$WORK/site/index.html"
    head -c 32 /dev/urandom > "$WORK/token.key"
    cat > "$WORK/profiles.json" <<EOF
{"profiles":[{"name":"p-websocket","secret":"000000000000000000000000000000a2","backend":"127.0.0.1:2398","carrier_mode":"websocket"}]}
EOF
    cat > "$WORK/config.json" <<EOF
{"public_hostname":"w.bench.test","base_path":"","listen":"127.0.0.1:8080","admin_listen":"127.0.0.1:8081",
 "public_dir":"$WORK/site","static_routes":"exact","token_key_file":"$WORK/token.key","profiles_file":"$WORK/profiles.json",
 "limits":{"max_profiles":8,"max_sessions_global":256,"max_streams_global":4096,"max_backend_dials_in_flight":256,
  "new_sessions_per_minute":3000,"new_sessions_burst":512,"new_streams_per_minute":30000,"new_streams_burst":2048,
  "max_bootstraps_global":256,"new_bootstraps_per_minute":6000,"new_bootstraps_burst":1024,"max_pending_items_global":1048576}}
EOF
    chmod 600 "$WORK"/*.json "$WORK/token.key" "$WORK/key.pem"
    # Same vhost as the deployed nodes: body buffers raised, request and
    # response buffering off, WebSocket upgrade.
    cat > "$WORK/nginx/nginx.conf" <<EOF
worker_processes 2;
pid $WORK/nginx/nginx.pid;
error_log $WORK/nginx/logs/error.log warn;
events { worker_connections 1024; }
http {
    access_log off;
    client_body_temp_path $WORK/nginx/tmp/body; proxy_temp_path $WORK/nginx/tmp/proxy;
    fastcgi_temp_path $WORK/nginx/tmp/f; uwsgi_temp_path $WORK/nginx/tmp/u; scgi_temp_path $WORK/nginx/tmp/s;
    map \$http_upgrade \$conn_upgrade { default upgrade; '' close; }
    server {
        listen 10.78.0.1:443 ssl; http2 on; server_name w.bench.test;
        ssl_certificate $WORK/cert.pem; ssl_certificate_key $WORK/key.pem; ssl_protocols TLSv1.2 TLSv1.3;
        http2_body_preread_size 1m;
        location / {
            proxy_pass http://127.0.0.1:8080; proxy_http_version 1.1;
            proxy_set_header Host \$host; proxy_set_header Upgrade \$http_upgrade; proxy_set_header Connection \$conn_upgrade;
            proxy_read_timeout 180s; proxy_send_timeout 180s;
            client_max_body_size 4m; client_body_buffer_size 4m;
            proxy_request_buffering off; proxy_buffering off;
        }
    }
}
EOF
    sudo ip netns add $NS
    sudo ip link add wpbh type veth peer name wpbn
    sudo ip link set wpbn netns $NS
    sudo ip addr add 10.78.0.2/24 dev wpbh; sudo ip link set wpbh up
    sudo ip netns exec $NS ip addr add 10.78.0.1/24 dev wpbn
    sudo ip netns exec $NS ip link set wpbn up
    sudo ip netns exec $NS ip link set lo up
    sudo ip netns exec $NS sysctl -qw net.ipv4.ip_unprivileged_port_start=0
    sudo ip netns exec $NS sudo -u "$(id -un)" bash -c "cd '$WORK'; \
        nohup python3 '$HERE/backend.py' 2398 > backend.out 2>&1 & \
        nohup '$TPROXY_SERVER' -config '$WORK/config.json' > tproxy.out 2>&1 & \
        /usr/sbin/nginx -p '$WORK/nginx' -c '$WORK/nginx/nginx.conf'"
    (setsid nohup python3 "$HERE/connect_proxy.py" "$PROXY_PORT" > "$WORK/connect_proxy.out" 2>&1 &)
    cmd_shape 50 50mbit
}

cmd_down() {
    for p in $(pgrep -f "connect_proxy.py $PROXY_PORT"); do kill "$p"; done
    sudo ip netns pids $NS 2>/dev/null | xargs -r sudo kill
    sleep 0.5
    sudo ip netns del $NS 2>/dev/null || true
    sudo ip link del wpbh 2>/dev/null || true
}

cmd_shape() {
    local half extra=()
    half="$(python3 -c "print($1/2)")ms"
    [ "$2" != none ] && extra=(rate "$2")
    sudo tc qdisc replace dev wpbh root netem delay "$half" "${extra[@]}" limit 100000
    sudo ip netns exec $NS tc qdisc replace dev wpbn root netem delay "$half" "${extra[@]}" limit 100000
}

cmd_emulator() {
    : "${WPB_AVD:?set WPB_AVD to an x86_64 google_apis AVD}"
    (setsid nohup "$SDK/emulator/emulator" -avd "$WPB_AVD" -read-only -no-snapshot -no-window -no-audio \
        -no-boot-anim -gpu swiftshader_indirect -port "${WPB_EMU_PORT:-5580}" -cores 4 -memory 3072 \
        > "$WORK/emulator.log" 2>&1 &)
    adb wait-for-device
    until [ "$(adb shell getprop sys.boot_completed | tr -d '\r')" = 1 ]; do sleep 2; done
    adb root >/dev/null; sleep 2
}

# build <dir with WebProxy*.java | git rev> <out.apk>
cmd_build() {
    local src=$1 out=$2 L W
    L=$(libs); W=$(mktemp -d); cert
    mkdir -p "$W/src/org/telegram/proxy" "$W/classes" "$W/dex" "$W/res/raw" "$W/res/xml"
    if [ -d "$src" ]; then
        cp "$src"/WebProxy*.java "$W/src/org/telegram/proxy/"
    else
        for f in $(git -C "$REPO" ls-tree --name-only "$src" TMessagesProj/src/main/java/org/telegram/proxy/ | grep '/WebProxy'); do
            git -C "$REPO" show "$src:$f" > "$W/src/org/telegram/proxy/$(basename "$f")"
        done
    fi
    rm -f "$W/src/org/telegram/proxy/WebProxyConnectionTester.java"
    cp "$WORK/cert.pem" "$W/res/raw/benchca.pem"
    cp "$HERE/android/res/xml/nsc.xml" "$W/res/xml/"
    javac -nowarn --release 8 -cp "$AJ:$L/webkit.jar:$L/annotation.jar" -d "$W/classes" \
        $(find "$W/src" "$HERE/android/src" "$HERE/android/stubs" -name '*.java') 2>&1 | grep -v '^Note:' || true
    [ -f "$W/classes/org/zastogram/wpbench/BenchActivity.class" ] || { echo "compile failed" >&2; exit 1; }
    "$BT/d8" --min-api 24 --lib "$AJ" --output "$W/dex" $(find "$W/classes" -name '*.class') "$L/webkit.jar" 2>&1 | grep -iv warning || true
    "$BT/aapt2" compile --dir "$W/res" -o "$W/res.zip"
    "$BT/aapt2" link -o "$W/base.apk" --manifest "$HERE/android/AndroidManifest.xml" -I "$AJ" -A "$HERE/android/assets" "$W/res.zip"
    (cd "$W/dex" && zip -q "$W/base.apk" classes*.dex)
    "$BT/zipalign" -f 4 "$W/base.apk" "$W/aligned.apk"
    [ -f "$WORK/debug.keystore" ] || keytool -genkeypair -keystore "$WORK/debug.keystore" -storepass android -keypass android \
        -alias d -keyalg RSA -dname CN=bench -validity 3650 >/dev/null 2>&1
    "$BT/apksigner" sign --ks "$WORK/debug.keystore" --ks-pass pass:android --out "$out" "$W/aligned.apk"
    rm -rf "$W"
    echo "built $out"
}

cmd_install() {
    if ! adb install -r "$1" >/dev/null 2>&1; then
        # Signed by another bench work dir: replace it.
        adb uninstall org.zastogram.wpbench >/dev/null 2>&1 || true
        adb install "$1" >/dev/null
    fi
    adb shell cmd package compile -m speed -f org.zastogram.wpbench >/dev/null
}

# run label key=value ...: one run, prints {"label","args","renderer_cpu_ms","r":{...}}
cmd_run() {
    local label=$1 extras=() line=""
    shift
    for kv in "$@"; do extras+=(--es "${kv%%=*}" "${kv#*=}"); done
    adb logcat -c
    adb shell am start -S -W -n org.zastogram.wpbench/.BenchActivity "${extras[@]}" >/dev/null
    for _ in $(seq 1 400); do
        line=$(adb logcat -d -s WPBENCH:I | grep RESULT | tail -1)
        [ -n "$line" ] && break
        sleep 1
    done
    local rcpu
    rcpu=$(adb shell 'for p in $(pgrep -f sandboxed); do cat /proc/$p/stat; done' | awk '{s+=($14+$15)*10} END {print s+0}')
    echo "{\"label\":\"$label\",\"args\":\"$*\",\"renderer_cpu_ms\":$rcpu,\"r\":${line#*RESULT }}"
}

# matrix OUT REPS apk...: RTT/rate conditions x app-like and raw workloads
cmd_matrix() {
    local out=$1 reps=$2 cond c apk name
    shift 2
    for cond in "50 50mbit" "150 50mbit" "300 50mbit" "100 20mbit"; do
        cmd_shape $cond
        c=${cond// /-}
        for apk in "$@"; do
            name=$(basename "$apk" .apk)
            cmd_install "$apk"
            # app*: FileUploadOperation (128 KiB x 16 over 4 upload
            # connections) and FileLoadOperation (128 KiB x 4, or 512 KiB x 8
            # with experimental params); raw*: far more in flight than any
            # window, the transport's own ceiling.
            for _ in $(seq 1 "$reps"); do
                cmd_run "$name-$c-appup" idle=5 up=32 upConns=4 >> "$out"
                cmd_run "$name-$c-rawup" idle=3 up=32 upPart=524288 upParallel=64 upConns=4 >> "$out"
                cmd_run "$name-$c-appdown" idle=3 down=32 >> "$out"
                cmd_run "$name-$c-appdown8" idle=3 down=32 downPart=524288 downParallel=8 >> "$out"
                cmd_run "$name-$c-rawdown" idle=3 down=32 downPart=524288 downParallel=32 >> "$out"
                cmd_run "$name-$c-mixed" idle=3 up=16 upConns=4 down=16 downPart=524288 downParallel=8 >> "$out"
            done
        done
    done
}

cmd_summary() {
    python3 - "$1" <<'EOF'
import collections, json, statistics, sys
rows = collections.defaultdict(list)
for line in open(sys.argv[1]):
    if line.startswith("{"):
        d = json.loads(line)
        rows[d["label"]].append(d["r"])
def med(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None
for label, runs in rows.items():
    get = lambda f: med([f(r) for r in runs])
    print(f"{label:28} n={len(runs)} up={get(lambda r: r.get('up_MBps'))} down={get(lambda r: r.get('down_MBps'))}"
          f" idle={get(lambda r: (r.get('idle_ms') or {}).get('p50'))}"
          f" loaded_p50={get(lambda r: (r.get('loaded_ms') or {}).get('p50'))}"
          f" loaded_p90={get(lambda r: (r.get('loaded_ms') or {}).get('p90'))}"
          f" cpu/MB={get(lambda r: r.get('cpu_ms_per_MB'))} main/MB={get(lambda r: r.get('main_ms_per_MB'))}"
          + ("" if all(r.get("ok") for r in runs) else " FAILED"))
EOF
}

case "${1:-}" in
    up|down|shape|emulator|build|install|run|matrix|summary) c=$1; shift; "cmd_$c" "$@" ;;
    *) sed -n '2,24p' "$0"; exit 2 ;;
esac
