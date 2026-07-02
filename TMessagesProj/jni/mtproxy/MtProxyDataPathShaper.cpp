#include "MtProxyDataPathShaper.h"

#include <algorithm>
#include "MtProxyPhaseContract.h"

static constexpr int64_t MT_PROXY_STARTUP_COVER_SOFT_WINDOW_MS = 12000;
static constexpr int64_t MT_PROXY_STARTUP_COVER_STRICT_WINDOW_MS = 20000;
static constexpr uint32_t MT_PROXY_STARTUP_COVER_SOFT_FRAMES = 8;
static constexpr uint32_t MT_PROXY_STARTUP_COVER_STRICT_FRAMES = 14;

static uint32_t mtProxyShaperRandomBounded(MtProxyShaperRandomBounded randomBounded, uint32_t bound) {
    if (bound <= 1 || randomBounded == nullptr) {
        return 0;
    }
    return randomBounded(bound);
}

MtProxyStartupCoverPolicy mtProxyStartupCoverPolicy(bool fakeTls, int32_t startupCoverMode) {
    MtProxyStartupCoverPolicy policy;
    policy.mode = normalizeMtProxyStartupCoverOption(startupCoverMode);
    if (!fakeTls || policy.mode == MT_PROXY_STARTUP_COVER_OFF) {
        return policy;
    }
    policy.enabled = true;
    if (policy.mode == MT_PROXY_STARTUP_COVER_STRICT) {
        policy.windowMs = MT_PROXY_STARTUP_COVER_STRICT_WINDOW_MS;
        policy.maxFrames = MT_PROXY_STARTUP_COVER_STRICT_FRAMES;
    } else {
        policy.windowMs = MT_PROXY_STARTUP_COVER_SOFT_WINDOW_MS;
        policy.maxFrames = MT_PROXY_STARTUP_COVER_SOFT_FRAMES;
    }
    return policy;
}

MtProxyStartupCoverEvaluation mtProxyEvaluateStartupCover(const MtProxyStartupCoverPolicy &policy, const MtProxyStartupCoverState &state, int64_t now) {
    MtProxyStartupCoverEvaluation evaluation;
    if (!policy.enabled || state.startTime == 0) {
        return evaluation;
    }
    evaluation.elapsedMs = now - state.startTime;
    if (evaluation.elapsedMs > policy.windowMs || state.frameCount >= policy.maxFrames) {
        evaluation.shouldEnd = true;
        return evaluation;
    }
    evaluation.active = true;
    return evaluation;
}

int32_t mtProxyEffectiveRecordSizingMode(int32_t recordSizingMode, int32_t startupCoverMode, bool startupCoverActive) {
    int32_t mode = normalizeMtProxyRecordSizingOption(recordSizingMode);
    if (!startupCoverActive) {
        return mode;
    }
    int32_t coverMode = normalizeMtProxyStartupCoverOption(startupCoverMode);
    if (coverMode == MT_PROXY_STARTUP_COVER_STRICT) {
        return MT_PROXY_RECORD_SIZING_VARIED;
    }
    return mode == MT_PROXY_RECORD_SIZING_OFF ? MT_PROXY_RECORD_SIZING_CONSERVATIVE : mode;
}

int32_t mtProxyEffectiveTimingMode(int32_t timingMode, int32_t startupCoverMode, bool startupCoverActive) {
    int32_t mode = normalizeMtProxyTimingOption(timingMode);
    if (!startupCoverActive) {
        return mode;
    }
    int32_t coverMode = normalizeMtProxyStartupCoverOption(startupCoverMode);
    if (coverMode == MT_PROXY_STARTUP_COVER_STRICT) {
        return MT_PROXY_TIMING_BALANCED;
    }
    return mode == MT_PROXY_TIMING_OFF ? MT_PROXY_TIMING_GENTLE : mode;
}

MtProxyRecordSizingDecision nextMtProxyTlsRecordPayloadSize(const MtProxyRecordSizingInput &input) {
    MtProxyRecordSizingDecision decision;
    decision.effectiveRecordSizingMode = mtProxyEffectiveRecordSizingMode(input.recordSizingMode, input.startupCoverMode, input.startupCoverActive);
    uint32_t cap = 2878;
    if (decision.effectiveRecordSizingMode == MT_PROXY_RECORD_SIZING_CONSERVATIVE) {
        static const uint32_t caps[] = {1440, 1728, 2016, 2304, 2580, 2878};
        cap = caps[mtProxyShaperRandomBounded(input.randomBounded, sizeof(caps) / sizeof(caps[0]))];
    } else if (decision.effectiveRecordSizingMode == MT_PROXY_RECORD_SIZING_VARIED) {
        uint32_t minCap = input.firstTlsFrameSent ? 768 : 1200;
        uint32_t maxCap = input.firstTlsFrameSent ? 2878 : 2016;
        cap = minCap + mtProxyShaperRandomBounded(input.randomBounded, maxCap - minCap + 1);
    }
    if (cap > 2878) {
        cap = 2878;
    }
    if (cap < 256) {
        cap = 256;
    }
    if (input.remaining < cap) {
        cap = input.remaining;
    }
    decision.payloadSize = cap;
    return decision;
}

uint32_t mtProxyDataAwareIptDelayMs(int32_t timingMode, MtProxyShaperRandomBounded randomBounded) {
    int32_t mode = normalizeMtProxyTimingOption(timingMode);
    if (mode == MT_PROXY_TIMING_BALANCED) {
        return 20 + mtProxyShaperRandomBounded(randomBounded, 28) + mtProxyShaperRandomBounded(randomBounded, 54);
    }
    if (mode == MT_PROXY_TIMING_GENTLE) {
        return 8 + mtProxyShaperRandomBounded(randomBounded, 14) + mtProxyShaperRandomBounded(randomBounded, 25);
    }
    return 0;
}

MtProxyDataTimingDecision mtProxyDataTimingDecision(const MtProxyDataTimingInput &input) {
    MtProxyDataTimingDecision decision;
    decision.effectiveTimingMode = mtProxyEffectiveTimingMode(input.timingMode, input.startupCoverMode, input.startupCoverActive);
    if (decision.effectiveTimingMode == MT_PROXY_TIMING_OFF || input.hasPendingTlsFrame || !input.hasOutgoingData) {
        return decision;
    }
    decision.delayMs = mtProxyDataAwareIptDelayMs(decision.effectiveTimingMode, input.randomBounded);
    decision.shouldDelay = decision.delayMs > 0;
    return decision;
}

MtProxyDataTimingWaitDecision mtProxyDataTimingWaitDecision(const MtProxyDataTimingWaitInput &input) {
    MtProxyDataTimingWaitDecision decision;
    int32_t mode = normalizeMtProxyTimingOption(input.timingMode);
    if (mode == MT_PROXY_TIMING_OFF || input.hasPendingTlsFrame || input.nextWriteTime == 0) {
        return decision;
    }
    if (input.now >= input.nextWriteTime) {
        decision.clearScheduledTime = true;
        return decision;
    }
    decision.shouldWait = true;
    decision.delayMs = (uint32_t) std::min<int64_t>(250, input.nextWriteTime - input.now);
    return decision;
}

MtProxyDataPathFailureAction mtProxyDataPathFailureActionForEvidence(MtProxyFailureEvidenceKind evidence) {
    MtProxyDataPathFailureAction action;
    if (evidence == MtProxyFailureEvidenceKind::PostHandshakeNoAppData) {
        action.dataPathShapingBackoff = true;
        action.allowParserVariants = false;
        action.name = "post_handshake_shaping_backoff";
    }
    return action;
}

MtProxyDataPathFailureAction mtProxyDataPathFailureActionForPhase(const std::string &phase, MtProxyFailureEvidenceKind evidence) {
    if (phase == MtProxyPhase::PostHandshakeNoAppdata) {
        return mtProxyDataPathFailureActionForEvidence(MtProxyFailureEvidenceKind::PostHandshakeNoAppData);
    }
    return mtProxyDataPathFailureActionForEvidence(evidence);
}
