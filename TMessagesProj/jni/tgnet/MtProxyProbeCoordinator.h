/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#ifndef MTPROXYPROBECOORDINATOR_H
#define MTPROXYPROBECOORDINATOR_H

#include <stdint.h>
#include <string>
#include "MtProxyAdaptivePolicy.h"

class MtProxyProbeCoordinator {
public:
    enum class DecisionKind : uint8_t {
        StartOwner,
        JoinExisting,
        UseWorkingRecipe,
        TerminalUnsupported,
    };

    struct ProbeKey {
        std::string key;
        std::string endpointKey;
        std::string networkEndpointKey;
        uint32_t allowedSniVariants = 0;
    };

    struct GreaseProbeResult {
        bool useGrease = false;
        bool probe = false;
        bool supported = false;
        bool rejected = false;
    };

    struct Decision {
        DecisionKind kind = DecisionKind::StartOwner;
        uint32_t generation = 0;
        uint32_t waitMs = 0;
        MtProxyAdaptivePolicy::RecipeCursor cursor;
        MtProxyAdaptivePolicy::RecipeCursor workingCursor;
        MtProxyAdaptivePolicy::CompatibilityRecipe workingRecipe;
        std::string lastRecipeDiagnostic;
        GreaseProbeResult greaseProbe;
    };

    struct FailureResult {
        bool recorded = false;
        bool recipeExhausted = false;
        uint32_t generation = 0;
        MtProxyAdaptivePolicy::RecipeCursor cursor;
        MtProxyAdaptivePolicy::RecipeCursor cachedCursor;
        std::string lastRecipeDiagnostic;
    };

    static Decision beginOrJoin(const ProbeKey &probeKey, const void *owner, int64_t now);
    static FailureResult completeFailure(const ProbeKey &probeKey,
                                         const void *owner,
                                         const std::string &diagnostic,
                                         bool recipeUsesGrease,
                                         bool recipeIsGreaseProbe,
                                         bool classicFallbackAllowed,
                                         int64_t now);
    static void completeSuccess(const ProbeKey &probeKey,
                                const void *owner,
                                const char *reason,
                                bool recipeUsesGrease,
                                const MtProxyAdaptivePolicy::CompatibilityRecipe &recipe,
                                int64_t now);
    static void completeUnsupported(const ProbeKey &probeKey, const void *owner, int64_t now);
    static void cancelOwner(const ProbeKey &probeKey, const void *owner);
    static void touchOwner(const ProbeKey &probeKey, const void *owner, int64_t now);
    static void reapExpired(int64_t now);

    static bool failureNeedsRecipe(const std::string &diagnostic);
    static MtProxyAdaptivePolicy::RecipeCursor recipeCursorForProbe(const std::string &probeKey);
    static MtProxyAdaptivePolicy::RecipeCursor workingRecipeCursorForProbe(const std::string &probeKey);
    static MtProxyAdaptivePolicy::CompatibilityRecipe workingRecipeForProbe(const std::string &probeKey);
    static std::string lastRecipeDiagnosticForProbe(const std::string &probeKey);
    static GreaseProbeResult readGreaseProbeState(const std::string &probeKey);
};

#endif
