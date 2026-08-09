// Host-run unit tests for the mtproxy_core decision engine.
// Compiled and executed by Tools/build_mtproxy_host.py against the real
// module objects (platform surface stubbed; RAND_bytes stub is a
// deterministic xorshift stream, so jitter is bounded but nonzero —
// assertions use envelopes, never exact jittered values).
//
// These cover the two decision paths whose regressions historically caused
// reconnect livelocks: terminal-diagnostic derivation (pre-I/O verdict
// clobber) and retry-hold computation (missing backoff).
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>

#include "MtProxyAdaptivePolicy.h"
#include "MtProxyClientHelloPolicy.h"
#include "MtProxyPhaseContract.h"
#include "MtProxyRetryAuthority.h"
#include "MtProxySecretDomain.h"
#include "MtProxyStartupTimeline.h"
#include "MtProxyTerminalDiagnostic.h"

static int failures = 0;

#define CHECK(cond) \
    do { \
        if (!(cond)) { \
            failures++; \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        } \
    } while (0)

static void write16(std::vector<uint8_t> &data, size_t offset, uint16_t value) {
    data[offset] = (uint8_t) (value >> 8);
    data[offset + 1] = (uint8_t) value;
}

static void write24(std::vector<uint8_t> &data, size_t offset, uint32_t value) {
    data[offset] = (uint8_t) (value >> 16);
    data[offset + 1] = (uint8_t) (value >> 8);
    data[offset + 2] = (uint8_t) value;
}

static std::vector<uint8_t> validRelayClientHello(const std::string &domain) {
    std::vector<uint8_t> data(MT_PROXY_CANONICAL_CLIENT_HELLO_BYTES, 0);
    data[0] = 0x16;
    data[1] = 0x03;
    data[2] = 0x01;
    write16(data, 3, (uint16_t) (data.size() - 5));
    data[5] = 0x01;
    write24(data, 6, (uint32_t) (data.size() - 9));
    data[9] = 0x03;
    data[10] = 0x03;

    size_t position = 43;
    data[position++] = 32;
    position += 32;
    write16(data, position, 2);
    position += 2;
    write16(data, position, 0x1301);
    position += 2;
    data[position++] = 1;
    data[position++] = 0;
    write16(data, position, (uint16_t) (data.size() - position - 2));
    position += 2;

    write16(data, position, 0x0000);
    write16(data, position + 2, (uint16_t) (domain.size() + 5));
    position += 4;
    write16(data, position, (uint16_t) (domain.size() + 3));
    position += 2;
    data[position++] = 0;
    write16(data, position, (uint16_t) domain.size());
    position += 2;
    memcpy(data.data() + position, domain.data(), domain.size());
    position += domain.size();

    size_t remaining = data.size() - position;
    CHECK(remaining >= 4);
    write16(data, position, 0x0015);
    write16(data, position + 2, (uint16_t) (remaining - 4));
    return data;
}

static void testClientHelloRelayContract() {
    const std::string domain = "example.com";
    const auto valid = validRelayClientHello(domain);
    CHECK(mtProxyCheckClientHelloContract(valid.data(), valid.size(), domain)
            == MtProxyClientHelloContractIssue::None);

    auto wrongSession = valid;
    wrongSession[43] = 31;
    CHECK(mtProxyCheckClientHelloContract(wrongSession.data(), wrongSession.size(), domain)
            == MtProxyClientHelloContractIssue::SessionIdNot32Bytes);

    auto wrongCipher = valid;
    write16(wrongCipher, 78, 0xc02f);
    CHECK(mtProxyCheckClientHelloContract(wrongCipher.data(), wrongCipher.size(), domain)
            == MtProxyClientHelloContractIssue::FirstCipherNotTls13);

    CHECK(mtProxyCheckClientHelloContract(valid.data(), valid.size(), "other.example")
            == MtProxyClientHelloContractIssue::SniMismatch);

    auto missingSni = valid;
    write16(missingSni, 84, 0x0015);
    CHECK(mtProxyCheckClientHelloContract(missingSni.data(), missingSni.size(), domain)
            == MtProxyClientHelloContractIssue::SniMissing);

    auto shortHello = valid;
    shortHello.resize(MT_PROXY_CANONICAL_CLIENT_HELLO_BYTES - 1);
    CHECK(mtProxyCheckClientHelloContract(shortHello.data(), shortHello.size(), domain)
            == MtProxyClientHelloContractIssue::TooShort);

    auto longHello = valid;
    longHello.resize(MT_PROXY_MAX_RELAY_CLIENT_HELLO_BYTES + 1);
    CHECK(mtProxyCheckClientHelloContract(longHello.data(), longHello.size(), domain)
            == MtProxyClientHelloContractIssue::TooLong);

    CHECK(mtProxyDefaultTlsProfile() == MT_PROXY_TLS_PROFILE_YANDEX);
    CHECK(mtProxyTlsProfileWithheld(MT_PROXY_TLS_PROFILE_CHROME_MODERN));
    CHECK(mtProxyTlsProfileWithheld(MT_PROXY_TLS_PROFILE_ANDROID_CHROME));
    CHECK(!mtProxyTlsProfileWithheld(MT_PROXY_TLS_PROFILE_YANDEX));
    CHECK(mtProxyEffectiveWireTlsProfile(MT_PROXY_TLS_PROFILE_AUTO)
            == MT_PROXY_TLS_PROFILE_YANDEX);
    CHECK(mtProxyEffectiveWireTlsProfile(MT_PROXY_TLS_PROFILE_CHROME_MODERN)
            == MT_PROXY_TLS_PROFILE_YANDEX);
}

static void testExactSecretDomainPolicy() {
    const auto exact = buildMtProxySecretDomainPlan("Example.COM");
    CHECK(exact.terminalDiagnostic == nullptr);
    CHECK(exact.originalDomain == "Example.COM");
    CHECK(exact.canonicalDomain == "Example.COM");
    CHECK(exact.allowedSniVariants
            == MtProxyAdaptivePolicy::sniVariantMask(MtProxyAdaptivePolicy::SNI_ORIGINAL));

    const auto spaced = buildMtProxySecretDomainPlan(" example.com");
    CHECK(spaced.terminalDiagnostic == MtProxyPhase::SecretParseInvalidDomain);
    CHECK(spaced.canonicalDomain.empty());
    CHECK(spaced.allowedSniVariants == 0);

    const auto controlled = buildMtProxySecretDomainPlan("exam\nple.com");
    CHECK(controlled.terminalDiagnostic == MtProxyPhase::SecretParseInvalidDomainControlChar);
    CHECK(controlled.canonicalDomain.empty());
    CHECK(controlled.allowedSniVariants == 0);
}

static void testRetryAuthorityReconnectHold() {
    using namespace MtProxyRetry;

    ReconnectHoldInput input;
    input.diagnostic = "ok";
    input.trafficClass = TrafficClass::Generic;
    input.previousBackoffMs = 1234;
    ReconnectHoldDecision decision = nextReconnectHold(input);
    CHECK(!decision.shouldHold);
    CHECK(decision.delayMs == 0);
    CHECK(decision.nextBackoffMs == 1234);
    CHECK(strcmp(decision.source, "phase_no_backoff") == 0);

    input.diagnostic = nullptr;
    decision = nextReconnectHold(input);
    CHECK(!decision.shouldHold);

    // First failure: base delay per traffic class + bounded jitter
    // (jitter limit is min(delay/4, 2000)).
    input.diagnostic = MtProxyPhase::TcpConnectTimeout;
    input.previousBackoffMs = 0;
    decision = nextReconnectHold(input);
    CHECK(decision.shouldHold);
    CHECK(decision.delayMs >= 1800 && decision.delayMs <= 1800 + 450);
    CHECK(decision.nextBackoffMs == decision.delayMs);
    CHECK(strcmp(decision.source, "exp_backoff") == 0);

    // Exponential doubling, capped at the class maximum (+jitter envelope).
    input.previousBackoffMs = 1800;
    decision = nextReconnectHold(input);
    CHECK(decision.delayMs >= 3600 && decision.delayMs <= 3600 + 900);
    input.previousBackoffMs = 8000;
    decision = nextReconnectHold(input);
    CHECK(decision.delayMs >= 8000 && decision.delayMs <= 8000 + 2000);
    input.previousBackoffMs = 100000;
    decision = nextReconnectHold(input);
    CHECK(decision.delayMs >= 8000 && decision.delayMs <= 8000 + 2000);

    input.trafficClass = TrafficClass::Download;
    input.previousBackoffMs = 0;
    decision = nextReconnectHold(input);
    CHECK(decision.delayMs >= 3500 && decision.delayMs <= 3500 + 875);
    input.previousBackoffMs = 100000;
    decision = nextReconnectHold(input);
    CHECK(decision.delayMs >= 16000 && decision.delayMs <= 16000 + 2000);

    // The coordinator's longer clock wins the delay but must not inflate
    // the connection's own exponential progression.
    input.trafficClass = TrafficClass::Generic;
    input.previousBackoffMs = 0;
    input.coordinatorHoldMs = 30000;
    decision = nextReconnectHold(input);
    CHECK(decision.shouldHold);
    CHECK(decision.delayMs == 30000);
    CHECK(strcmp(decision.source, "coordinator_hold") == 0);
    CHECK(decision.nextBackoffMs >= 1800 && decision.nextBackoffMs <= 1800 + 450);

    // A shorter coordinator hold must not shrink the exponential delay.
    input.coordinatorHoldMs = 100;
    decision = nextReconnectHold(input);
    CHECK(decision.delayMs >= 1800 && decision.delayMs <= 1800 + 450);
    CHECK(strcmp(decision.source, "exp_backoff") == 0);
}

static void testRetryAuthorityEndpointCooldown() {
    using namespace MtProxyRetry;

    // The endpoint cooldown clock must never be undercut by scheduler pacing.
    EndpointCooldownWaitInput input;
    input.now = 1000;
    input.cooldownUntil = 61000;
    input.cooldownRemainingMs = 60000;
    input.priority = 0;
    input.connectionPatternMode = 0;
    CHECK(endpointCooldownWaitMs(input) >= 60000);

    input.cooldownUntil = 1000;
    input.cooldownRemainingMs = 0;
    uint32_t waitMs = endpointCooldownWaitMs(input);
    CHECK(waitMs < 60000);
}

static void testTerminalDiagnosticDerivation() {
    using namespace MtProxyPhase;

    // Pre-I/O terminal verdicts survive the close path untouched — the
    // clobber of exactly this invariant caused the 30.06 and 02.07 livelocks.
    MtProxyStartupTimeline timeline;
    TerminalDiagnosticInput input;
    input.currentDiagnostic = HandshakeProfilesExhausted;
    input.timeline = &timeline;
    input.socketConnectedLogged = false;
    input.closeReason = 1;
    input.socketError = -1;
    TerminalDiagnosticResult result = deriveTerminalDiagnostic(input);
    CHECK(result.diagnostic == HandshakeProfilesExhausted);
    CHECK(!result.timelineDerived);

    // Real TCP failures split by errno before the generic timeline verdict.
    timeline.reset();
    timeline.beginTcpConnect(1000, 12);
    input.currentDiagnostic = "connect_start";
    input.socketError = ECONNREFUSED;
    result = deriveTerminalDiagnostic(input);
    CHECK(result.diagnostic == TcpConnectionRefused);
    CHECK(!result.timelineDerived);

    input.socketError = ETIMEDOUT;
    result = deriveTerminalDiagnostic(input);
    CHECK(result.diagnostic == TcpConnectTimeout);

    input.socketError = 0;
    input.closeReason = 2;
    result = deriveTerminalDiagnostic(input);
    CHECK(result.diagnostic == TcpConnectTimeout);

    // Cold socket, nothing attempted: timeline owns the verdict.
    timeline.reset();
    input.currentDiagnostic = "";
    input.closeReason = 1;
    input.socketError = 0;
    result = deriveTerminalDiagnostic(input);
    CHECK(result.timelineDerived);
    CHECK(result.diagnostic == "connection_not_started");

    // Startup finished (socket was connected): current diagnostic stands.
    input.currentDiagnostic = "post_handshake_no_appdata";
    input.socketConnectedLogged = true;
    result = deriveTerminalDiagnostic(input);
    CHECK(result.diagnostic == "post_handshake_no_appdata");
    CHECK(!result.timelineDerived);

    input.currentDiagnostic = "";
    result = deriveTerminalDiagnostic(input);
    CHECK(result.diagnostic == "unknown_fail");
}

static void testGeneratedClassification() {
    using namespace MtProxyPhase;

    // Local-scheduler skip list: probe-wait timeout is deliberately signal,
    // background aborts are deliberately local.
    CHECK(!isLocalSchedulerTimeout("mtproxy_probe_wait_timeout"));
    CHECK(isLocalSchedulerTimeout("background_handshake_aborted"));
    CHECK(isLocalSchedulerTimeout("endpoint_cooldown_timeout"));
    CHECK(isLocalSchedulerTimeout("connection_not_started"));
    CHECK(!isLocalSchedulerTimeout("tcp_not_connected"));
    CHECK(!isLocalSchedulerTimeout(nullptr));

    // A phase must never be both a preserved pre-I/O verdict and on the
    // local-scheduler skip list — the skip list would swallow the verdict.
    const char *preIo[] = {
        "dns_negative_cache_hit",
        "dns_blocked_zero_address",
        "secret_parse_invalid_domain_control_char",
        "secret_parse_invalid_domain",
        "faketls_not_mtproxy_response",
        "faketls_no_server_hello_terminal",
        "faketls_server_closed_terminal",
        "handshake_profiles_exhausted",
    };
    for (const char *phase : preIo) {
        CHECK(isPreIoTerminalVerdict(phase));
        CHECK(!isLocalSchedulerTimeout(phase));
    }

    CHECK(needsReconnectBackoff("tcp_connect_timeout"));
    CHECK(needsReconnectBackoff("handshake_profiles_exhausted"));
    CHECK(!needsReconnectBackoff("mtproxy_probe_wait_timeout"));
    CHECK(!needsReconnectBackoff("connection_not_started"));
}

int main() {
    testClientHelloRelayContract();
    testExactSecretDomainPolicy();
    testRetryAuthorityReconnectHold();
    testRetryAuthorityEndpointCooldown();
    testTerminalDiagnosticDerivation();
    testGeneratedClassification();
    if (failures == 0) {
        printf("mtproxy host tests passed\n");
        return 0;
    }
    printf("mtproxy host tests: %d failure(s)\n", failures);
    return 1;
}
