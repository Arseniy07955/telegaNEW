#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TGNET = ROOT / "TMessagesProj/jni/tgnet"
MESSENGER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger"


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
    adaptive_h = read(TGNET / "MtProxyAdaptivePolicy.h")
    adaptive_cpp = read(TGNET / "MtProxyAdaptivePolicy.cpp")
    coordinator_h = read(TGNET / "MtProxyProbeCoordinator.h")
    coordinator_cpp = read(TGNET / "MtProxyProbeCoordinator.cpp")
    socket = read(TGNET / "ConnectionSocket.cpp")
    state_machine = read(TGNET / "ConnectionSocketStateMachine.h")
    options_h = read(TGNET / "MtProxyOptions.h")
    phase_policy = read(MESSENGER / "ProxyPhasePolicy.java")
    runtime = read(MESSENGER / "ProxyRuntimeStateStore.java")
    check_all = read(ROOT / "Tools/check_mtproxy_all.py")

    for token in (
        "CLIENT_HELLO_CHROME_MODERN_SOFT_FRAGMENT",
        "CLIENT_HELLO_CHROME_MODERN_NO_FRAGMENT",
        "CLIENT_HELLO_ANDROID_CHROME_NO_FRAGMENT",
        "CLIENT_HELLO_FIREFOX_ANDROID_NO_FRAGMENT",
        "CLIENT_HELLO_LEGACY_NO_GREASE_NO_MODERN_EXTENSIONS",
        "CLIENT_HELLO_LEGACY_TLS12_MINIMAL",
        "SNI_ORIGINAL",
        "SNI_SANITIZED",
        "SNI_LOWERCASE_ASCII",
        "SNI_NO_TRAILING_DOT",
        "SNI_PUNYCODE",
        "SNI_OPTIONAL_NO_SNI",
        "PARSER_LENIENT_RECORD",
        "PARSER_TOLERATE_EXTRA_RECORDS_BEFORE_SERVER_HELLO",
        "PARSER_TOLERATE_CCS_TICKET_ORDERING",
        "PARSER_TOLERATE_FRAGMENTED_SERVER_HELLO",
        "PARSER_TOLERATE_TLS_ALERT_EXACT_DESC",
        "CLASSIC_STANDARD_INTERMEDIATE",
        "CLASSIC_RANDOMIZED_INTERMEDIATE",
        "CLASSIC_ABRIDGED_FALLBACK",
        "CLASSIC_INTERMEDIATE_FALLBACK",
        "RecipeCursor",
        "CompatibilityRecipe",
    ):
        require(token in adaptive_h, f"adaptive policy header must expose {token}", failures)

    ladder = block(adaptive_cpp, "static std::vector<MtProxyAdaptivePolicy::RecipeCursor> buildRecipeCursorLadder", "MtProxyAdaptivePolicy::RecipeCursor MtProxyAdaptivePolicy::initialCursor")
    normal_sni = ladder.find("for (int32_t sniVariant = MtProxyAdaptivePolicy::SNI_ORIGINAL; sniVariant <= MtProxyAdaptivePolicy::SNI_PUNYCODE")
    optional_no_sni = ladder.find("SNI_OPTIONAL_NO_SNI", normal_sni)
    classic = ladder.find("CLASSIC_STANDARD_INTERMEDIATE", optional_no_sni)
    require(
        normal_sni >= 0 and optional_no_sni > normal_sni and classic > optional_no_sni,
        "recipe ladder must traverse normal SNI variants first, optional_no_sni last, then classic/DD fallbacks",
        failures,
    )
    require(
        "CLIENT_HELLO_FAMILY_COUNT" in ladder
        and "PARSER_VARIANT_COUNT" in ladder
        and "classicFallbackAllowed" in ladder,
        "recipe ladder must cross product ClientHello family, SNI variant, parser variant, and gated classic variants",
        failures,
    )

    recipe_id = block(adaptive_cpp, "std::string MtProxyAdaptivePolicy::recipeId(const CompatibilityRecipe", "std::string MtProxyAdaptivePolicy::recipeId(const MtProxyRecipe")
    require(
        "familyName" in recipe_id
        and "sniVariantName" in recipe_id
        and "parserVariantName" in recipe_id
        and "classicVariantName" in recipe_id
        and "experimental_no_sni" in recipe_id,
        "recipe_id must include family/SNI/parser/classic and experimental no-SNI fields",
        failures,
    )

    require(
        "currentOriginalSecretDomain" in state_machine
        and "currentSanitizedSecretDomain" in state_machine
        and "currentLowercaseSecretDomain" in state_machine
        and "currentNoTrailingDotSecretDomain" in state_machine
        and "currentPunycodeSecretDomain" in state_machine
        and "currentAllowedSniVariants" in state_machine,
        "ConnectionSocket state must keep canonical SNI and all recipe SNI variants separately",
        failures,
    )
    require(
        "buildMtProxySecretDomainPlan" in socket
        and "secret_domain_sanitized" in socket
        and "currentClientHelloSni" in socket
        and "hello.setDomain(currentClientHelloSni)" in socket
        and "SNI_OPTIONAL_NO_SNI" in socket,
        "native SNI plan must sanitize invalid raw domains and feed recipe-specific clientHelloSni",
        failures,
    )
    require(
        "currentMtProxyProbeKey = currentMtProxyRecipeCacheKey" in socket
        and "allowedSniVariants" in coordinator_h
        and "probeKey.allowedSniVariants = currentAllowedSniVariants" in socket
        and "sniVariantName" in socket,
        "probe identity must stay exact config while SNI variants stay inside recipe_id/cursor logs",
        failures,
    )

    parser = block(socket, "static MtProxyServerHelloParseResult mtProxyParseServerHelloFlight", "static int32_t normalizeMtProxyConnectionPatternMode")
    require(
        "normalizeMtProxyServerHelloParserOption(parserMode)" in parser
        and "leading_record_wait" in parser
        and "tls_alert_exact_desc" in parser
        and "fragmented_server_hello_wait" in parser
        and "ccs_ticket_ordering_mismatch" in parser,
        "server-flight parser must implement named parser variant diagnostics instead of ignoring parserMode",
        failures,
    )
    for token in (
        "MT_PROXY_SERVER_HELLO_PARSER_LENIENT_RECORD",
        "MT_PROXY_SERVER_HELLO_PARSER_EXTRA_RECORDS",
        "MT_PROXY_SERVER_HELLO_PARSER_CCS_TICKET_ORDERING",
        "MT_PROXY_SERVER_HELLO_PARSER_FRAGMENTED_SERVER_HELLO",
        "MT_PROXY_SERVER_HELLO_PARSER_TLS_ALERT_EXACT_DESC",
    ):
        require(token in options_h and token in adaptive_cpp, f"parser option {token} must be declared and used by recipes", failures)

    require(
        "workingRecipe" in coordinator_h
        and "state.workingRecipe = recipe" in coordinator_cpp
        and "DecisionKind::UseWorkingRecipe" in socket
        and "MtProxyAdaptivePolicy::recipeId(probeDecision.workingRecipe)" in socket,
        "coordinator must cache and return the full working recipe descriptor",
        failures,
    )
    require(
        "return !currentSecretIsFakeTls" in socket
        and "classic_fallback_allowed" in socket,
        "classic/DD fallback variants must be gated by secret kind and never synthesized from ee",
        failures,
    )

    classify_policy = block(phase_policy, "private static PhaseInfo classify", "private static PhaseInfo live")
    unsupported_policy = block(classify_policy, "case ProxyCheckDiagnostics.UNSUPPORTED_FOR_CURRENT_CLIENT:", "case ProxyCheckDiagnostics.MTPROXY_PACKET_SENT_NO_RESPONSE:")
    require(
        "terminalExactFailure()" in unsupported_policy
        and "terminalExactConfig" in phase_policy
        and "terminalExactConfigVerdict" in runtime
        and "terminal_proxy_config_unsupported" in runtime,
        "unsupported_for_current_client must be terminal exact config, bypassing normal rotation hysteresis",
        failures,
    )
    native_stage = block(runtime, "public static Decision onNativeStage", "private static boolean isActiveProxyEvent")
    row_only_policy = block(runtime, "private static Decision updateProxyRowOnly", "private static Decision quarantineAndCancelEndpoint")
    require(
        "if (!isActiveProxyEvent(event))" in native_stage
        and "updateProxyRowOnly" in native_stage
        and native_stage.find("if (!isActiveProxyEvent(event))") < native_stage.find("ProxyPhasePolicy.isProxyUsableSuccessPhase"),
        "candidate/non-active events must enter row-only handling before active visible status/backoff policy",
        failures,
    )
    require(
        "terminalExactConfig" in row_only_policy
        and "terminal_proxy_config_unsupported" in row_only_policy
        and "row_only=1" in row_only_policy
        and "nativeCancelled = matchesActive ? 0" in row_only_policy,
        "candidate terminal unsupported must be row-only and must not cancel the active native attempt when it matches the active endpoint",
        failures,
    )

    require(
        '"check_mtproxy_compatibility_ladder_broad.py"' in check_all,
        "full MTProxy guard suite must include broad compatibility ladder guard",
        failures,
    )

    if failures:
        print("MTProxy broad compatibility ladder guard failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1
    print("MTProxy broad compatibility ladder guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
