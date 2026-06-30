#!/usr/bin/env python3
from pathlib import Path
import re
import sys

from mtproxy_phase_contract import java_phase_names, native_phase_names


ROOT = Path(__file__).resolve().parents[1]
SOCKET = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
ENDPOINT_POLICY = ROOT / "TMessagesProj/jni/tgnet/MtProxyEndpointPolicy.cpp"
ENDPOINT_POLICY_H = ROOT / "TMessagesProj/jni/tgnet/MtProxyEndpointPolicy.h"
PROBE_COORDINATOR = ROOT / "TMessagesProj/jni/tgnet/MtProxyProbeCoordinator.cpp"
PROBE_COORDINATOR_H = ROOT / "TMessagesProj/jni/tgnet/MtProxyProbeCoordinator.h"
ADAPTIVE_POLICY = ROOT / "TMessagesProj/jni/tgnet/MtProxyAdaptivePolicy.cpp"
ADAPTIVE_POLICY_H = ROOT / "TMessagesProj/jni/tgnet/MtProxyAdaptivePolicy.h"
STATE_MACHINE_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocketStateMachine.h"
DIAGNOSTICS = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckDiagnostics.java"
PHASE_POLICY = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyPhasePolicy.java"
ENDPOINT_KEY = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyEndpointKey.java"
HEALTH = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyHealthStore.java"
RUNTIME_STORE = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyRuntimeStateStore.java"
CONNECTION = ROOT / "TMessagesProj/jni/tgnet/Connection.cpp"
ANALYZER = ROOT / "Tools/analyze_mtproxy_markers.py"
CHECK_ALL = ROOT / "Tools/check_mtproxy_all.py"
STRINGS = ROOT / "TMessagesProj/src/main/res/values/strings.xml"
STRINGS_RU = ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml"

RECIPE_FAILURES = {
    "true_client_hello_timeout",
    "faketls_server_hello_wait_timeout",
    "server_closed_after_client_hello",
    "client_hello_sent_no_server_hello",
    "tls_alert_after_client_hello",
    "short_tls_response_after_client_hello",
    "unrecognized_response_after_client_hello",
    "unrecognized_tls_response_after_client_hello",
    "server_hello_hmac_mismatch",
}
LEGACY_OR_JAVA_ONLY_RECIPE_FAILURES = {
    "true_client_hello_timeout",
    "client_hello_sent_no_server_hello",
    "unrecognized_tls_response_after_client_hello",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def block(source: str, start: str, end: str) -> str:
    start_index = source.find(start)
    if start_index < 0:
        return ""
    end_index = source.find(end, start_index + 1)
    return source[start_index:end_index if end_index >= 0 else len(source)]


def main() -> int:
    failures: list[str] = []
    socket = read(SOCKET)
    endpoint_policy = read(ENDPOINT_POLICY)
    endpoint_policy_h = read(ENDPOINT_POLICY_H)
    probe_coordinator = read(PROBE_COORDINATOR)
    probe_coordinator_h = read(PROBE_COORDINATOR_H)
    adaptive_policy = read(ADAPTIVE_POLICY)
    adaptive_policy_h = read(ADAPTIVE_POLICY_H)
    state_machine_h = read(STATE_MACHINE_H)
    diagnostics = read(DIAGNOSTICS)
    phase_policy = read(PHASE_POLICY)
    endpoint_key = read(ENDPOINT_KEY)
    health = read(HEALTH)
    runtime_store = read(RUNTIME_STORE)
    connection = read(CONNECTION)
    analyzer = read(ANALYZER)
    check_all = read(CHECK_ALL)
    strings = read(STRINGS)
    strings_ru = read(STRINGS_RU)

    for phase in RECIPE_FAILURES | {"unsupported_for_current_client", "secret_parse_invalid_domain_control_char", "secret_parse_invalid_domain"}:
        require(phase in java_phase_names(), f"phase contract must expose Java phase {phase}", failures)
        if phase not in LEGACY_OR_JAVA_ONLY_RECIPE_FAILURES:
            require(phase in native_phase_names(), f"phase contract must expose native phase {phase}", failures)
        require(phase.upper() in diagnostics, f"ProxyCheckDiagnostics must define {phase}", failures)
        require(phase in analyzer, f"analyzer must know {phase}", failures)

    require("secret_domain_sanitized" in java_phase_names(), "phase contract must expose Java live phase secret_domain_sanitized", failures)
    require("secret_domain_sanitized" in native_phase_names(), "phase contract must expose native live phase secret_domain_sanitized", failures)
    require("SECRET_DOMAIN_SANITIZED" in diagnostics, "ProxyCheckDiagnostics must define secret_domain_sanitized", failures)
    require("secret_domain_sanitized" in analyzer, "analyzer must know secret_domain_sanitized", failures)

    require(
        "mtproxy_tls_after_client_hello" in socket
        and "hex=" in socket
        and "record_len=" in socket
        and "alert_level" in socket
        and "alert_description" in socket,
        "ConnectionSocket must log first post-ClientHello bytes with TLS record length and alert fields",
        failures,
    )
    require(
        "Probable TLS alert after ClientHello" in strings
        and "Вероятный TLS alert после ClientHello" in strings_ru
        and "probable TLS alert / non-ServerHello record" in analyzer,
        "TLS alert wording must stay cautious until the raw post-ClientHello bytes are inspected",
        failures,
    )
    require(
        "looksLikeMtProxyTlsAlert" in socket
        and '"tls_alert_after_client_hello"' in socket
        and '"short_tls_response_after_client_hello"' in socket
        and '"unrecognized_response_after_client_hello"' in socket
        and '"unrecognized_tls_response_after_client_hello"' in probe_coordinator + adaptive_policy,
        "ConnectionSocket must split alert, short, and unrecognized post-ClientHello responses while policy retains the legacy TLS alias",
        failures,
    )
    require(
        'proxyCheckDiagnostic = "faketls_server_hello_wait_timeout"' in socket
        and 'proxyCheckDiagnostic = "server_closed_after_client_hello"' in socket
        and "bytesRead == 0" in block(socket, "void ConnectionSocket::markProxyHandshakeFreezeIfNeeded", "void ConnectionSocket::markProxyServerHelloHmacTimeoutIfNeeded"),
        "ServerHello wait must split no-byte deadline from EOF-after-ClientHello instead of publishing true_client_hello_timeout",
        failures,
    )

    recipe_body = block(probe_coordinator, "bool MtProxyProbeCoordinator::failureNeedsRecipe", "MtProxyAdaptivePolicy::RecipeCursor MtProxyProbeCoordinator::recipeCursorForProbe")
    cooldown_body = block(endpoint_policy, "bool MtProxyEndpointPolicy::failureNeedsCooldown", "int64_t MtProxyEndpointPolicy::cooldownMs")
    for phase in RECIPE_FAILURES:
        require(phase in recipe_body, f"native probe coordinator must treat {phase} as a recipe failure", failures)
        if phase not in LEGACY_OR_JAVA_ONLY_RECIPE_FAILURES:
            require(phase not in cooldown_body, f"{phase} must not cooldown/quarantine the endpoint directly", failures)
    for phase in ("secret_parse_invalid_domain_control_char", "secret_parse_invalid_domain"):
        require(phase in cooldown_body, f"native endpoint policy must quarantine invalid secret phase {phase}", failures)
    require(
        '"unsupported_for_current_client"' in cooldown_body
        and '"unsupported_for_current_client"' not in recipe_body,
        "only recipe exhaustion should become an endpoint-rotating failure",
        failures,
    )
    require(
        "recipeExhausted" in probe_coordinator_h
        and "workingCursor" in probe_coordinator
        and "workingRecipe" in probe_coordinator
        and "cachedCursor" in probe_coordinator_h,
        "probe coordinator must track failed recipe exhaustion and cache the full working recipe descriptor",
        failures,
    )
    require(
        "unsupported_for_current_client" in socket
        and "recipe_exhausted" in socket,
        "ConnectionSocket must publish unsupported_for_current_client only after recipe exhaustion",
        failures,
    )

    require(
        "RecipeCursor" in adaptive_policy_h
        and "CompatibilityRecipe" in adaptive_policy_h
        and "nextCursor" in adaptive_policy_h
        and "recipeForCursor" in adaptive_policy_h,
        "compatibility ladder must expose explicit cursor and recipe descriptor APIs instead of a fixed level count",
        failures,
    )
    require(
        "struct MtProxyRecipe" in adaptive_policy_h
        and "transportMode" in adaptive_policy_h
        and "tlsProfile" in adaptive_policy_h
        and "fragmentClientHello" in adaptive_policy_h
        and "useGrease" in adaptive_policy_h
        and "useModernExtensions" in adaptive_policy_h
        and "serverHelloParser" in adaptive_policy_h
        and "sni" in adaptive_policy_h,
        "adaptive policy must expose an explicit MtProxyRecipe identity",
        failures,
    )
    require(
        "recipeCacheKey" in endpoint_policy_h
        and "ProbeKey" in probe_coordinator_h
        and "currentMtProxyRecipeCacheKey" in state_machine_h
        and "currentMtProxyRecipeCacheKey" in socket
        and "mtProxySecretHashForRecipeKey" in socket,
        "recipe cache must be keyed separately by host:port + secret_hash + SNI without changing the public live endpoint key",
        failures,
    )
    require(
        "probeKey.key = currentMtProxyProbeKey" in socket
        and "recipeCursorForProbe(currentMtProxyProbeKey)" in socket
        and "lastRecipeDiagnosticForProbe(currentMtProxyProbeKey)" in socket,
        "recipe failure/adaptation must read and write the probe key, not the public endpoint key",
        failures,
    )
    require(
        "recipe_failed" in socket
        and "next_family" in socket
        and "next_sni_variant" in socket
        and "next_parser_variant" in socket
        and "next_classic_variant" in socket
        and "recipe_id=" in socket
        and "server_hello_parser=" in socket,
        "ConnectionSocket must log each recipe failure with the current recipe identity and next cursor",
        failures,
    )
    require(
        "CompatibilityRecipe MtProxyAdaptivePolicy::recipeForCursor" in adaptive_policy
        and "MtProxyRecipe MtProxyAdaptivePolicy::recipeForResult" in adaptive_policy
        and "std::string MtProxyAdaptivePolicy::recipeId" in adaptive_policy
        and "standard_hmac_parser" in adaptive_policy
        and "lenient_record_parser" in adaptive_policy
        and "tolerate_fragmented_server_hello" in adaptive_policy,
        "adaptive policy must derive a stable recipe id including named parser variants",
        failures,
    )
    require(
        "CLIENT_HELLO_CHROME_MODERN_SOFT_FRAGMENT" in adaptive_policy_h
        and "CLIENT_HELLO_LEGACY_NO_GREASE_NO_MODERN_EXTENSIONS" in adaptive_policy_h
        and "MT_PROXY_TLS_PROFILE_FIREFOX_ANDROID" in adaptive_policy
        and "MT_PROXY_TLS_PROFILE_ANDROID_CHROME" in adaptive_policy,
        "adaptive policy must try explicit ClientHello families and known-compatible TLS profiles",
        failures,
    )
    require(
        "SNI_OPTIONAL_NO_SNI" in adaptive_policy_h
        and "experimentalNoSni" in adaptive_policy_h
        and "optional_no_sni" in adaptive_policy,
        "optional no-SNI must be an explicit experimental recipe output, not a separate probe key",
        failures,
    )
    handshake_ok_body = block(probe_coordinator, "void MtProxyProbeCoordinator::completeSuccess", "void MtProxyProbeCoordinator::completeUnsupported")
    require(
        "server_hello_hmac_ok" in handshake_ok_body
        and "probeKey" in handshake_ok_body
        and "state.workingCursor = state.cursor" in handshake_ok_body
        and "state.workingRecipe = recipe" in handshake_ok_body,
        "server_hello_hmac_ok must cache the full current working recipe for the exact probe key",
        failures,
    )

    recipe_phases = block(phase_policy, "private static PhaseInfo classify", "private static PhaseInfo live")
    for phase in RECIPE_FAILURES:
        require(phase.upper() in recipe_phases, f"Java phase policy must classify {phase}", failures)
    require(
        "TLS_ALERT_AFTER_CLIENT_HELLO" in recipe_phases
        and "failure(KeyScope.EXACT, false, false)" in recipe_phases,
        "recipe failures must be visible but must not directly backoff/rotate the endpoint in Java",
        failures,
    )
    require(
        "UNSUPPORTED_FOR_CURRENT_CLIENT" in recipe_phases
        and "terminalExactFailure()" in recipe_phases
        and "terminalExactConfig" in phase_policy,
        "unsupported_for_current_client must be a terminal exact-config verdict, not a normal rotation/hysteresis failure",
        failures,
    )
    require(
        "tls_alert_after_client_hello" not in block(connection, "static bool mtProxyDiagnosticNeedsReconnectBackoff", "static uint32_t mtProxyReconnectBackoffBaseMs")
        and "unsupported_for_current_client" in connection,
        "connection reconnect backoff must wait for recipe exhaustion before endpoint-level backoff",
        failures,
    )
    require(
        "UNSUPPORTED_CLIENT_FAILURE_BACKOFF_MS = 15 * 60 * 1000L" in health
        and "INVALID_SECRET_FAILURE_BACKOFF_MS = 15 * 60 * 1000L" in health
        and "INVALID_SECRET_ROTATED_AWAY_HOLD_MS = 15 * 60 * 1000L" in health
        and "rotatedAwayHoldMs(normalized)" in health
        and "failureBackoffMs(state.lastDiagnostic" in health,
        "existing Java endpoint health policy must give unsupported-for-current-client and invalid-secret phases a longer exact-endpoint hold/backoff",
        failures,
    )
    invalid_secret_policy = block(
        phase_policy,
        "case ProxyCheckDiagnostics.SECRET_PARSE_INVALID_DOMAIN_CONTROL_CHAR:",
        "case ProxyCheckDiagnostics.HOST_RESOLVE_FAILED:",
    )
    require(
        "SECRET_PARSE_INVALID_DOMAIN_CONTROL_CHAR" in invalid_secret_policy
        and "SECRET_PARSE_INVALID_DOMAIN" in invalid_secret_policy
        and "return failure(KeyScope.EXACT, true, true)" in invalid_secret_policy,
        "invalid secret-domain phases must backoff and rotate/quarantine the exact proxy config in Java",
        failures,
    )
    require(
        "ProxyHealthStore.isEndpointRotatedAway(proxyInfo, now)" in block(runtime_store, "public static void markConnectionUsable", "public static ProxyHealthStore.EndpointFailureResult markEndpointFailure")
        and "source=usable_success" in runtime_store,
        "late usable-success callbacks from a rotated-away endpoint must not clear quarantine/backoff",
        failures,
    )

    require(
        "sanitizeMtProxySecretDomain" in socket
        and "secret_parse_invalid_domain_control_char" in socket
        and "validateMtProxySecretDomain" in socket,
        "native FakeTLS setup must sanitize and validate SNI before ClientHello construction",
        failures,
    )
    require(
        "secretDomainSanitized" in socket
        and 'publishProxyConnectionStage("secret_domain_sanitized")' in socket
        and "recordSecretDomainSanitized" in socket
        and "mtproxy_startup secret_domain_sanitized" in socket,
        "native FakeTLS setup must continue with a valid sanitized control-char SNI and publish it once per endpoint",
        failures,
    )
    require(
        "recordSecretDomainSanitized" in endpoint_policy
        and "recordSecretDomainSanitized" in endpoint_policy_h,
        "endpoint policy must deduplicate secret_domain_sanitized logs by endpoint key",
        failures,
    )
    require(
        "sanitizeSecretDomainForLiveStage" in endpoint_key
        and "IDN.toASCII" in endpoint_key
        and "Character.isISOControl" in endpoint_key,
        "Java endpoint key must sanitize control chars and normalize IDN domains",
        failures,
    )

    require(
        '"check_mtproxy_compatibility_recipe.py"' in check_all,
        "full MTProxy guard suite must include compatibility recipe guard",
        failures,
    )

    if failures:
        print("MTProxy compatibility recipe guard failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1
    print("MTProxy compatibility recipe guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
