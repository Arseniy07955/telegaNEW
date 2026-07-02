#ifndef MTPROXYENDPOINTRECORDER_H
#define MTPROXYENDPOINTRECORDER_H

#include "MtProxyAdaptivePolicy.h"
#include "MtProxyOptions.h"
#include "MtProxySocketPublisher.h"

#include <cstddef>
#include <functional>
#include <stdint.h>
#include <string>

class MtProxyEndpointRecorder {
public:
    struct CommonContext {
        const void *socketTag = nullptr;
        std::string endpointKey;
        std::string recipeCacheKey;
        std::string networkEndpointKey;
        std::string probeKey;
        bool fakeTls = false;
        uint32_t allowedSniVariants = 0;
        uint32_t activationGeneration = 0;
        uint64_t ownerToken = 0;
        int64_t now = 0;
        MtProxyAdaptivePolicy::CompatibilityRecipe recipe;
        std::string recipeId;
        bool recipeUsesGrease = false;
        bool recipeIsGreaseProbe = false;
        bool classicFallbackAllowed = false;
    };

    struct FailureContext : CommonContext {
        std::string diagnostic;
        std::string reason;
        size_t responseBytes = 0;
        uint64_t responseSignature = 0;
        int32_t connectionPatternMode = MT_PROXY_CONNECTION_PATTERN_OFF;
        std::string connectionPatternName;
        int32_t priority = 0;
        MtProxyAdaptivePolicy::RecipeCursor cursor;
        MtProxyAdaptivePolicy::RecipeInput recipeInput;
    };

    struct SuccessContext : CommonContext {
        std::string reason;
    };

    struct ProbeBackoffContext : CommonContext {
        uint32_t holdMs = 0;
        uint32_t generation = 0;
        std::string terminalPhase;
    };

    struct Callbacks {
        std::function<void(const MtProxySocketObservation &observation)> publishObservation;
        std::function<void(const std::string &diagnostic)> setProxyCheckDiagnostic;
        std::function<void(uint32_t holdMs)> setSuggestedReconnectHold;
        std::function<void(const char *action, const char *reason)> logInvariant;
        std::function<void(const std::string &message)> logDebug;
    };

    static void recordFailure(const FailureContext &context, const Callbacks &callbacks);
    static void recordHandshakeOk(const SuccessContext &context, const Callbacks &callbacks);
    static void recordDataPathSuccess(const SuccessContext &context, const Callbacks &callbacks);
    static void recordProfilesExhaustedBackoff(const ProbeBackoffContext &context, const Callbacks &callbacks);
    static void recordHandshakeBudgetBackoff(const ProbeBackoffContext &context, const Callbacks &callbacks);
};

#endif
