#!/usr/bin/env python3
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
MESSENGER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger"
ROTATION = MESSENGER / "ProxyRotationController.java"
ENGINE = MESSENGER / "ProxyRotationEngine.java"
STORE = MESSENGER / "ProxyRuntimeStateStore.java"
DIAGNOSTICS = MESSENGER / "ProxyCheckDiagnostics.java"
POLICY = MESSENGER / "ProxyPhasePolicy.java"
SHARED_CONFIG = MESSENGER / "SharedConfig.java"
PHASE_CONTRACT = ROOT / "Tools/mtproxy_phase_contract.py"
CHECK_ALL = ROOT / "Tools/check_mtproxy_all.py"
STRINGS = ROOT / "TMessagesProj/src/main/res/values/strings.xml"
STRINGS_RU = ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml"


def read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def require_text(path: Path, needle: str, message: str, failures: list[str]) -> None:
    require(needle in read(path), f"{path.relative_to(ROOT)}: {message}", failures)


def require_not_text(path: Path, needle: str, message: str, failures: list[str]) -> None:
    require(needle not in read(path), f"{path.relative_to(ROOT)}: {message}", failures)


def method_body(text: str, signature: str) -> str:
    start = text.find(signature)
    if start == -1:
        return ""
    brace = text.find("{", start)
    if brace == -1:
        return ""
    depth = 0
    for index in range(brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return text[start:]


def main() -> int:
    failures: list[str] = []
    rotation = read(ROTATION)
    engine = read(ENGINE)
    shared_config = read(SHARED_CONFIG)

    require(engine, f"{ENGINE.relative_to(ROOT)}: missing ProxyRotationEngine", failures)
    require_text(ENGINE, "final class ProxyRotationEngine", "engine must be a package-private controller helper", failures)
    require_text(ENGINE, "static final int MAX_SWITCHES_PER_WINDOW = 4", "engine must cap switches per window", failures)
    require_text(ENGINE, "static final long SWITCH_WINDOW_MS = 60_000L", "engine must define a one-minute switch window", failures)
    require_text(ENGINE, "static final long NO_CANDIDATE_COOLDOWN_MS = 60_000L", "engine must define no-candidate cooldown", failures)
    require_text(ENGINE, "static final class Attempt", "engine must model scheduled attempts", failures)
    require_text(ENGINE, "final String proxyExactKey", "attempt must capture exact proxy identity", failures)
    require_text(ENGINE, "final int generation", "attempt must capture generation", failures)
    require_text(ENGINE, "final long startedAtMs", "attempt must capture start time", failures)
    require_text(ENGINE, "final String reason", "attempt must capture switch reason", failures)
    require_text(ENGINE, "final long timeoutAtMs", "attempt must capture timeout", failures)
    require_text(ENGINE, "boolean terminal", "attempt must become terminal exactly once", failures)
    require_text(ENGINE, "static final class RotationCycle", "engine must model a rotation cycle", failures)
    require_text(ENGINE, "HashSet<String> triedExactKeys", "cycle must remember endpoints already tried", failures)
    require_text(ENGINE, "ArrayDeque<Long> switchTimes", "cycle must remember recent switch times", failures)
    require_text(ENGINE, "long noCandidateUntilMs", "cycle must expose no-candidate cooldown", failures)
    require_text(ENGINE, "ProxyRuntimeStateStore.markEndpointFailure(currentProxy, ProxyCheckDiagnostics.CONNECTING_TIMEOUT)", "connecting timeout must become endpoint failure/backoff", failures)
    require_text(ENGINE, "ProxyRuntimeStateStore.isSwitchableCandidate(info)", "candidate filtering must stay delegated to runtime store", failures)
    require_text(ENGINE, "ProxyRuntimeStateStore.isFresh(info)", "fresh candidates must use shared freshness policy", failures)
    require_text(ENGINE, "triedExactKeys.contains", "candidate filtering must reject endpoints already tried in the cycle", failures)
    require_text(ENGINE, "switchTimes.size() >= MAX_SWITCHES_PER_WINDOW", "engine must enforce global switch rate-limit", failures)
    require_text(ENGINE, "decision = \"rate_limited\"", "engine must surface rate-limit decisions", failures)
    require_text(ENGINE, "decision = \"no_candidate\"", "engine must surface exhausted-candidate decisions", failures)
    require_text(ENGINE, "cycle.noCandidateUntilMs = now + NO_CANDIDATE_COOLDOWN_MS", "no candidates must set cooldown", failures)
    require_text(ENGINE, "attempt.generation != generation", "scheduled attempts must be stale-guarded by generation", failures)
    require_text(ENGINE, "ProxyEndpointKey.exact(currentProxy)", "scheduled attempts must be stale-guarded by endpoint key", failures)
    require_text(ENGINE, "completeScheduledAttempt", "engine must complete scheduled attempts centrally", failures)
    require_text(ENGINE, "recordSwitch", "engine must record switches centrally", failures)
    require_text(ENGINE, "onSettingsChanged", "engine must reset transient rotation state on settings changes", failures)
    require_text(ENGINE, "onConnected", "engine must reset rotation cycle on successful connection", failures)

    require_text(ROTATION, "ProxyRotationEngine engine = new ProxyRotationEngine()", "controller must delegate rotation decisions to engine", failures)
    require_text(ROTATION, "ProxyRotationEngine.Attempt attempt", "controller scheduled runnable must capture attempt identity", failures)
    require_text(ROTATION, "engine.beginScheduledAttempt", "controller must create scheduled attempts through engine", failures)
    require_text(ROTATION, "engine.completeScheduledAttempt(attempt", "controller must reject stale scheduled attempts through engine", failures)
    require_text(ROTATION, "scheduledSwitchRunnable", "controller must keep the exact scheduled runnable for cancellation", failures)
    require_not_text(ROTATION, "private boolean isCheckScheduled", "controller must not keep the old single boolean schedule model", failures)
    require_not_text(ROTATION, "ROTATION_TIMEOUTS.get(SharedConfig.proxyRotationTimeout)", "controller must use clamped timeout lookup", failures)
    require_not_text(ROTATION, "ProxyCheckScheduler.enqueueStale", "rotation must not start background proxy-check sweeps", failures)

    require_text(DIAGNOSTICS, "public static final String CONNECTING_TIMEOUT = \"connecting_timeout\"", "diagnostics must expose connecting_timeout", failures)
    require_text(POLICY, "case ProxyCheckDiagnostics.CONNECTING_TIMEOUT", "phase policy must classify connecting_timeout", failures)
    require_text(PHASE_CONTRACT, 'MtProxyPhase("connecting_timeout"', "phase contract must include connecting_timeout", failures)
    require_text(STRINGS, 'name="ProxyStatusConnectingTimeout"', "English strings must include connecting timeout status", failures)
    require_text(STRINGS_RU, 'name="ProxyStatusConnectingTimeout"', "Russian strings must include connecting timeout status", failures)

    timeout_load = re.search(r"proxyRotationTimeout = preferences\.getInt\([^;]+;", shared_config)
    require(
        timeout_load is not None and "clampProxyRotationTimeout" in shared_config,
        f"{SHARED_CONFIG.relative_to(ROOT)}: loaded proxyRotationTimeout must be clamped",
        failures,
    )
    require_text(SHARED_CONFIG, "private static int clampProxyRotationTimeout", "SharedConfig must own timeout index clamping", failures)
    require_text(SHARED_CONFIG, "proxySecret.equals(info.secret)", "currentProxy restore must match secret as part of identity", failures)
    load_proxy_list = method_body(shared_config, "public static void loadProxyList")
    current_matches = load_proxy_list.count("sameProxyIdentity(info, proxyAddress, proxyPort, proxyUsername, proxyPassword, proxySecret)")
    require(
        current_matches >= 2,
        f"{SHARED_CONFIG.relative_to(ROOT)}: V2/V3 and legacy proxy-list load branches must restore currentProxy by exact identity including secret",
        failures,
    )
    require(
        "samePlainSocksProxy(info, wssSocksAddress, wssSocksPort, wssSocksUsername, wssSocksPassword)" in load_proxy_list,
        f"{SHARED_CONFIG.relative_to(ROOT)}: WSS SOCKS proxy matching must remain secretless/plain SOCKS",
        failures,
    )
    require_text(CHECK_ALL, '"check_proxy_rotation_engine.py"', "full MTProxy guard suite must run rotation-engine guard", failures)

    if failures:
        print("Proxy rotation engine guard failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("Proxy rotation engine guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
