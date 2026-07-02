#include "MtProxyHandshakePlan.h"

MtProxyHandshakePlan mtProxyBuildHandshakePlan(const MtProxyAdaptivePolicy::RecipeInput &input,
                                               const MtProxyAdaptivePolicy::RecipeCursor &cursor,
                                               const std::string &endpointKey,
                                               const std::string &probeKey) {
    MtProxyHandshakePlan plan;
    plan.cursor = cursor;
    plan.recipe = MtProxyAdaptivePolicy::recipeForCursor(input, cursor);
    plan.recipeId = MtProxyAdaptivePolicy::recipeId(plan.recipe);
    plan.endpointKey = endpointKey;
    plan.probeKey = probeKey;
    plan.clientHelloSni = plan.recipe.clientHelloSni;
    plan.tlsProfile = plan.recipe.effectiveTlsProfile;
    plan.clientHelloFragmentation = plan.recipe.clientHelloFragmentation;
    plan.serverHelloParserMode = plan.recipe.serverHelloParserMode;
    plan.recordSizingMode = plan.recipe.recordSizingMode;
    plan.timingMode = plan.recipe.timingMode;
    plan.startupCoverMode = plan.recipe.startupCoverMode;
    plan.fakeTls = input.fakeTls;
    plan.greaseProbe = input.probeGrease;
    plan.greaseSupported = input.greaseSupported;
    return plan;
}
