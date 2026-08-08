#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CONNECTIONS = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"


def method_body(source: str, signature: str) -> str:
    start = source.find(signature)
    if start == -1:
        return ""
    brace = source.find("{", start)
    if brace == -1:
        return ""
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return source[start:]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    source = CONNECTIONS.read_text(encoding="utf-8", errors="replace")
    callback = method_body(
        source,
        "public static void onProxyConnectionStageChanged(final int currentAccount, final String diagnostic, final String endpointKey, final String probeKey, final String origin, final String socketRole, final int activationGeneration, final int suggestedHoldMs)",
    )
    enqueue = method_body(source, "private static void enqueueProxyConnectionStage(ProxyConnectionEvent event)")
    key = method_body(source, "private static String proxyStageEventKey(ProxyConnectionEvent event)")
    drain = method_body(source, "private static void drainProxyConnectionStages()")
    process = method_body(source, "private static void processProxyConnectionStage(ProxyConnectionEvent event)")
    failures: list[str] = []

    require(
        "ProxyConnectionEvent.nativeStage" in callback
        and "enqueueProxyConnectionStage(event)" in callback
        and "AndroidUtilities.runOnUIThread" not in callback,
        "JNI callback must enqueue a semantic event before posting work to the main looper",
        failures,
    )
    require(
        "LinkedHashMap<String, ProxyConnectionEvent> pendingProxyStageEvents" in source
        and "pendingProxyStageEvents.remove(key)" in enqueue
        and "pendingProxyStageEvents.put(key, event)" in enqueue,
        "pending native stages must coalesce identical events and refresh their latest ordering",
        failures,
    )
    require(
        "MAX_PENDING_PROXY_STAGE_EVENTS" in enqueue
        and "iterator.remove()" in enqueue,
        "pending proxy-stage memory must remain bounded during a native callback storm",
        failures,
    )
    for field in (
        "event.account",
        "event.phase",
        "event.endpointKey",
        "event.probeKey",
        "event.origin.wireName",
        "event.socketRole.wireName",
        "event.activationGeneration",
    ):
        require(field in key, f"coalescing identity must include {field}", failures)
    require(
        "MAX_PROXY_STAGE_EVENTS_PER_FRAME" in drain
        and "processProxyConnectionStage" in drain
        and "PROXY_STAGE_NEXT_BATCH_DELAY_MS" in drain
        and "AndroidUtilities.runOnUIThread" in drain,
        "UI reducer work must be bounded and yield between pending batches",
        failures,
    )
    require(
        drain.count("synchronized (proxyStageDispatchLock)") >= 2
        and drain.find("processProxyConnectionStage") < drain.find("hasMore = !pendingProxyStageEvents.isEmpty()"),
        "dispatcher must stay scheduled while a batch is processed so callbacks cannot post immediate drains between frames",
        failures,
    )
    require(
        "ProxyRuntimeStateStore.onNativeStage(event)" in process
        and "shouldNotifyProxyConnectionStage(decision)" in process,
        "coalesced stages must still pass through the reducer and notification gate",
        failures,
    )

    if failures:
        print("MTProxy UI stage backpressure guard failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1
    print("MTProxy UI stage backpressure guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
