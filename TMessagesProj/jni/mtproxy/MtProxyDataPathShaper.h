/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#ifndef MTPROXYDATAPATHSHAPER_H
#define MTPROXYDATAPATHSHAPER_H

#include <stddef.h>
#include <stdint.h>
#include <string>
#include "MtProxyFailureEvidence.h"
#include "MtProxyOptions.h"

typedef uint32_t (*MtProxyShaperRandomBounded)(uint32_t bound);

struct MtProxyStartupCoverPolicy {
    bool enabled = false;
    int32_t mode = MT_PROXY_STARTUP_COVER_OFF;
    int64_t windowMs = 0;
    uint32_t maxFrames = 0;
};

struct MtProxyStartupCoverState {
    int64_t startTime = 0;
    uint32_t frameCount = 0;
};

struct MtProxyStartupCoverEvaluation {
    bool active = false;
    bool shouldEnd = false;
    int64_t elapsedMs = 0;
};

struct MtProxyRecordSizingInput {
    int32_t recordSizingMode = MT_PROXY_RECORD_SIZING_OFF;
    int32_t startupCoverMode = MT_PROXY_STARTUP_COVER_OFF;
    bool startupCoverActive = false;
    bool firstTlsFrameSent = false;
    uint32_t remaining = 0;
    MtProxyShaperRandomBounded randomBounded = nullptr;
};

struct MtProxyRecordSizingDecision {
    uint32_t payloadSize = 0;
    int32_t effectiveRecordSizingMode = MT_PROXY_RECORD_SIZING_OFF;
};

struct MtProxyDataTimingInput {
    int32_t timingMode = MT_PROXY_TIMING_OFF;
    int32_t startupCoverMode = MT_PROXY_STARTUP_COVER_OFF;
    bool startupCoverActive = false;
    bool hasPendingTlsFrame = false;
    bool hasOutgoingData = false;
    MtProxyShaperRandomBounded randomBounded = nullptr;
};

struct MtProxyDataTimingDecision {
    bool shouldDelay = false;
    uint32_t delayMs = 0;
    int32_t effectiveTimingMode = MT_PROXY_TIMING_OFF;
};

struct MtProxyDataTimingWaitInput {
    int32_t timingMode = MT_PROXY_TIMING_OFF;
    bool hasPendingTlsFrame = false;
    int64_t nextWriteTime = 0;
    int64_t now = 0;
};

struct MtProxyDataTimingWaitDecision {
    bool shouldWait = false;
    bool clearScheduledTime = false;
    uint32_t delayMs = 0;
};

struct MtProxyDataPathFailureAction {
    bool dataPathShapingBackoff = false;
    bool allowParserVariants = false;
    const char *name = "none";
};

MtProxyStartupCoverPolicy mtProxyStartupCoverPolicy(bool fakeTls, int32_t startupCoverMode);
MtProxyStartupCoverEvaluation mtProxyEvaluateStartupCover(const MtProxyStartupCoverPolicy &policy, const MtProxyStartupCoverState &state, int64_t now);
int32_t mtProxyEffectiveRecordSizingMode(int32_t recordSizingMode, int32_t startupCoverMode, bool startupCoverActive);
int32_t mtProxyEffectiveTimingMode(int32_t timingMode, int32_t startupCoverMode, bool startupCoverActive);
MtProxyRecordSizingDecision nextMtProxyTlsRecordPayloadSize(const MtProxyRecordSizingInput &input);
uint32_t mtProxyDataAwareIptDelayMs(int32_t timingMode, MtProxyShaperRandomBounded randomBounded);
MtProxyDataTimingDecision mtProxyDataTimingDecision(const MtProxyDataTimingInput &input);
MtProxyDataTimingWaitDecision mtProxyDataTimingWaitDecision(const MtProxyDataTimingWaitInput &input);
MtProxyDataPathFailureAction mtProxyDataPathFailureActionForEvidence(MtProxyFailureEvidenceKind evidence);
MtProxyDataPathFailureAction mtProxyDataPathFailureActionForPhase(const std::string &phase, MtProxyFailureEvidenceKind evidence);

#endif
