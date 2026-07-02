#ifndef MTPROXYHANDSHAKEPLAN_H
#define MTPROXYHANDSHAKEPLAN_H

#include "MtProxyAdaptivePolicy.h"

#include <string>

struct MtProxyHandshakePlan {
    MtProxyAdaptivePolicy::RecipeCursor cursor;
    MtProxyAdaptivePolicy::CompatibilityRecipe recipe;
    std::string recipeId;
    std::string endpointKey;
    std::string probeKey;
    std::string clientHelloSni;
    int32_t tlsProfile = 0;
    int32_t clientHelloFragmentation = 0;
    int32_t serverHelloParserMode = 0;
    int32_t recordSizingMode = 0;
    int32_t timingMode = 0;
    int32_t startupCoverMode = 0;
    bool fakeTls = false;
    bool greaseProbe = false;
    bool greaseSupported = false;
};

MtProxyHandshakePlan mtProxyBuildHandshakePlan(const MtProxyAdaptivePolicy::RecipeInput &input,
                                               const MtProxyAdaptivePolicy::RecipeCursor &cursor,
                                               const std::string &endpointKey,
                                               const std::string &probeKey);

#endif
