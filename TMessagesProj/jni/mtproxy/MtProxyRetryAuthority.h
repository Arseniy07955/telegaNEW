/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#ifndef MTPROXYRETRYAUTHORITY_H
#define MTPROXYRETRYAUTHORITY_H

#include <stdint.h>

// Single owner of the "how long until the next attempt" answer.
//
// Before this authority existed the hold was computed in four places with
// four clocks: Connection::onDisconnectedInternal (exponential reconnect
// backoff), MtProxyEndpointPolicy (endpoint cooldown), the handshake
// scheduler (retry pacing) and the probe coordinator (terminal holds).
// Every past MTProxy livelock was a disagreement between those owners.
// The authority merges them: callers feed it the raw clocks and it returns
// THE delay plus the source that dominated, so logs can say whose clock won.
namespace MtProxyRetry {

// Mirrors tgnet ConnectionType semantics without importing tgnet headers;
// the Connection layer maps its ConnectionType to a traffic class.
enum class TrafficClass : uint8_t {
    Generic,
    GenericMedia,
    Push,
    Download,
    Upload,
    Other,
};

struct ReconnectHoldInput {
    // Close diagnostic phase; the authority re-validates it against the
    // generated reconnect_backoff classification even if the caller gated.
    const char *diagnostic = nullptr;
    TrafficClass trafficClass = TrafficClass::Other;
    // Previous exponential value (per-connection state kept by the caller).
    uint32_t previousBackoffMs = 0;
    // Probe coordinator terminal hold consumed on the close path.
    uint32_t coordinatorHoldMs = 0;
};

struct ReconnectHoldDecision {
    bool shouldHold = false;
    uint32_t delayMs = 0;
    // Next exponential value the caller stores back (without the
    // coordinator override, so the coordinator clock never inflates the
    // connection's own progression).
    uint32_t nextBackoffMs = 0;
    // "exp_backoff" | "coordinator_hold" | "phase_no_backoff"
    const char *source = "phase_no_backoff";
};

ReconnectHoldDecision nextReconnectHold(const ReconnectHoldInput &input);

// Pre-TCP endpoint cooldown wait: merges the endpoint cooldown clock with
// the handshake scheduler's pacing for the same wake-up so neither can
// schedule a retry the other would deny.
struct EndpointCooldownWaitInput {
    int64_t now = 0;
    int64_t cooldownUntil = 0;
    int64_t cooldownRemainingMs = 0;
    int32_t priority = 0;
    int32_t connectionPatternMode = 0;
};

uint32_t endpointCooldownWaitMs(const EndpointCooldownWaitInput &input);

// Exposed for tests/guards: the exponential envelope per traffic class.
uint32_t backoffBaseMs(TrafficClass trafficClass);
uint32_t backoffMaxMs(TrafficClass trafficClass);

}

#endif
