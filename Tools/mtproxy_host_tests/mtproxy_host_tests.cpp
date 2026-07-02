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

#include "MtProxyPhaseContract.h"
#include "MtProxyRetryAuthority.h"
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
