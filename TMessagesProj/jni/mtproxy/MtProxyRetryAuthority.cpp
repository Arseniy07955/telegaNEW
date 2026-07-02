/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#include "MtProxyRetryAuthority.h"

#include <algorithm>
#include <openssl/rand.h>

#include "MtProxyHandshakeScheduler.h"
#include "MtProxyPhaseContract.h"

namespace MtProxyRetry {

uint32_t backoffBaseMs(TrafficClass trafficClass) {
    switch (trafficClass) {
        case TrafficClass::Generic:
            return 1800;
        case TrafficClass::GenericMedia:
        case TrafficClass::Push:
            return 2500;
        case TrafficClass::Download:
        case TrafficClass::Upload:
            return 3500;
        default:
            return 2500;
    }
}

uint32_t backoffMaxMs(TrafficClass trafficClass) {
    switch (trafficClass) {
        case TrafficClass::Generic:
            return 8000;
        case TrafficClass::GenericMedia:
        case TrafficClass::Push:
            return 12000;
        case TrafficClass::Download:
        case TrafficClass::Upload:
            return 16000;
        default:
            return 12000;
    }
}

static uint32_t reconnectJitterMs(uint32_t limit) {
    if (limit == 0) {
        return 0;
    }
    uint32_t value = 0;
    RAND_bytes(reinterpret_cast<uint8_t *>(&value), sizeof(value));
    return value % (limit + 1);
}

ReconnectHoldDecision nextReconnectHold(const ReconnectHoldInput &input) {
    ReconnectHoldDecision decision;
    if (!MtProxyPhase::needsReconnectBackoff(input.diagnostic)) {
        decision.nextBackoffMs = input.previousBackoffMs;
        return decision;
    }
    uint32_t base = backoffBaseMs(input.trafficClass);
    uint32_t maxDelay = backoffMaxMs(input.trafficClass);
    uint32_t delay = input.previousBackoffMs == 0
            ? base
            : std::min(input.previousBackoffMs * 2, maxDelay);
    delay = std::max(delay, base);
    delay = std::min(delay, maxDelay);
    delay += reconnectJitterMs(std::min(delay / 4, 2000U));
    decision.shouldHold = true;
    decision.nextBackoffMs = delay;
    decision.delayMs = delay;
    decision.source = "exp_backoff";
    // The probe coordinator's terminal hold (budget backoff / profiles
    // exhausted) is the authoritative clock: retrying earlier is guaranteed
    // to be denied pre-TCP anyway, so wait out whichever is longer.
    if (input.coordinatorHoldMs > decision.delayMs) {
        decision.delayMs = input.coordinatorHoldMs;
        decision.source = "coordinator_hold";
    }
    return decision;
}

uint32_t endpointCooldownWaitMs(const EndpointCooldownWaitInput &input) {
    uint32_t delay = mtProxyHandshakeSchedulerRetryDelay(
            input.now, input.cooldownUntil, input.priority, input.connectionPatternMode);
    if (input.cooldownRemainingMs > 0 && (int64_t) delay < input.cooldownRemainingMs) {
        delay = (uint32_t) input.cooldownRemainingMs;
    }
    return delay;
}

}
