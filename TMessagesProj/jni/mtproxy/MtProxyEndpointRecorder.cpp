#include "MtProxyEndpointRecorder.h"

#include "MtProxyDataPathShaper.h"
#include "MtProxyEndpointPolicy.h"
#include "MtProxyFailureEvidence.h"
#include "MtProxyPhaseContract.h"
#include "MtProxyProbeCoordinator.h"
#include "MtProxyRecoveryPolicy.h"

#include <cstring>
#include <sstream>

namespace {

MtProxyEndpointPolicy::MtProxyEndpointContext endpointContextFor(const MtProxyEndpointRecorder::CommonContext &context) {
    MtProxyEndpointPolicy::MtProxyEndpointContext endpointContext;
    endpointContext.endpointKey = context.endpointKey;
    endpointContext.recipeCacheKey = context.recipeCacheKey;
    endpointContext.networkEndpointKey = context.networkEndpointKey;
    endpointContext.fakeTls = context.fakeTls;
    return endpointContext;
}

MtProxyProbeCoordinator::ProbeKey probeKeyFor(const MtProxyEndpointRecorder::CommonContext &context) {
    MtProxyProbeCoordinator::ProbeKey probeKey;
    probeKey.key = context.probeKey;
    probeKey.endpointKey = context.endpointKey;
    probeKey.networkEndpointKey = context.networkEndpointKey;
    probeKey.allowedSniVariants = context.allowedSniVariants;
    probeKey.activationGeneration = context.activationGeneration;
    return probeKey;
}

std::string normalizeFakeTlsBudgetTerminalPhase(const std::string &phase) {
    if (phase == MtProxyPhase::FaketlsNotMtproxyResponse
            || phase == MtProxyPhase::FaketlsNoServerHelloTerminal
            || phase == MtProxyPhase::FaketlsServerClosedTerminal) {
        return phase;
    }
    return MtProxyPhase::FaketlsNotMtproxyResponse;
}

std::string recipeIdForCursor(const MtProxyEndpointRecorder::FailureContext &context, const MtProxyAdaptivePolicy::RecipeCursor &cursor) {
    if (!context.fakeTls || context.recipeCacheKey.empty()) {
        return "";
    }
    MtProxyAdaptivePolicy::RecipeInput recipeInput = context.recipeInput;
    recipeInput.endpointKey = context.recipeCacheKey;
    recipeInput.cursor = cursor;
    recipeInput.lastDiagnostic = MtProxyProbeCoordinator::lastRecipeDiagnosticForProbe(context.probeKey);
    MtProxyProbeCoordinator::GreaseProbeResult greaseProbe = MtProxyProbeCoordinator::readGreaseProbeState(context.probeKey);
    recipeInput.forceNoGrease = !greaseProbe.useGrease;
    recipeInput.probeGrease = greaseProbe.probe;
    recipeInput.greaseSupported = greaseProbe.supported;
    return MtProxyAdaptivePolicy::recipeId(MtProxyAdaptivePolicy::recipeForCursor(recipeInput, cursor));
}

void publishObservation(const MtProxyEndpointRecorder::Callbacks &callbacks, const MtProxySocketObservation &observation) {
    if (callbacks.publishObservation) {
        callbacks.publishObservation(observation);
    }
}

void setProxyCheckDiagnostic(const MtProxyEndpointRecorder::Callbacks &callbacks, const std::string &diagnostic) {
    if (callbacks.setProxyCheckDiagnostic) {
        callbacks.setProxyCheckDiagnostic(diagnostic);
    }
}

void setSuggestedReconnectHold(const MtProxyEndpointRecorder::Callbacks &callbacks, uint32_t holdMs) {
    if (callbacks.setSuggestedReconnectHold) {
        callbacks.setSuggestedReconnectHold(holdMs);
    }
}

template <typename... Args>
void debug(const MtProxyEndpointRecorder::Callbacks &callbacks, Args... args) {
    if (!callbacks.logDebug) {
        return;
    }
    std::ostringstream message;
    (message << ... << args);
    callbacks.logDebug(message.str());
}

}

void MtProxyEndpointRecorder::recordFailure(const FailureContext &context, const Callbacks &callbacks) {
    if ((context.endpointKey.empty() && context.networkEndpointKey.empty()) || context.diagnostic.empty()) {
        return;
    }
    if (MtProxyPhase::isLocalSchedulerTimeout(context.diagnostic.c_str())) {
        debug(callbacks, "connection(", context.socketTag, ") mtproxy_startup endpoint_failure_skipped_local phase=", context.diagnostic, " reason=", context.reason);
        return;
    }
    const std::string &phase = context.diagnostic;
    MtProxyFailureEvidenceKind evidenceKind = mtProxyEvidenceForPhase(phase, context.responseBytes);
    const char *failureEvidence = mtProxyFailureEvidenceName(evidenceKind);
    MtProxyRecoveryAction recoveryAction = mtProxyRecoveryActionForEvidence(evidenceKind);
    MtProxyDataPathFailureAction dataPathFailureAction = mtProxyDataPathFailureActionForPhase(phase, evidenceKind);
    bool silentAfterClientHello = context.responseBytes == 0
            && evidenceKind == MtProxyFailureEvidenceKind::NoBytesAfterClientHello;
    bool recipeFailure = context.fakeTls
            && !silentAfterClientHello
            && MtProxyProbeCoordinator::failureNeedsRecipe(phase)
            && mtProxyRecoveryActionAdvancesRecipe(recoveryAction);
    if (silentAfterClientHello) {
        debug(callbacks, "connection(", context.socketTag, ") mtproxy_startup silent_after_client_hello phase=", phase, " endpoint=", context.endpointKey, " recipe_held");
    }
    if (recipeFailure) {
        MtProxyProbeCoordinator::FailureResult failure = MtProxyProbeCoordinator::completeFailure(
                probeKeyFor(context),
                context.ownerToken,
                phase,
                context.responseSignature,
                context.recipeUsesGrease,
                context.recipeIsGreaseProbe,
                context.classicFallbackAllowed,
                context.now);
        if (!failure.recorded) {
            return;
        }
        std::string nextRecipeId = failure.terminalBudgetExhausted ? failure.terminalPhase : (failure.recipeExhausted ? MtProxyPhase::HandshakeProfilesExhausted : recipeIdForCursor(context, failure.cursor));
        bool fallbackAllowed = !failure.recipeExhausted && !failure.terminalBudgetExhausted ? true : context.classicFallbackAllowed;
        debug(
                callbacks,
                "connection(", context.socketTag, ") mtproxy_startup recipe_failed key=", context.endpointKey,
                " recipe_key=", context.probeKey,
                " endpoint_key=", context.endpointKey,
                " phase=", phase,
                " reason=", context.reason,
                " evidence=", failureEvidence,
                " response_bytes=", context.responseBytes,
                " response_signature=", (unsigned long long) failure.responseSignature,
                " recipe=", context.recipeId,
                " recipe_id=", context.recipeId,
                " family=", MtProxyAdaptivePolicy::clientHelloFamilyName(context.cursor.family),
                " sni_variant=", MtProxyAdaptivePolicy::sniVariantName(context.cursor.sniVariant),
                " parser_variant=", MtProxyAdaptivePolicy::parserVariantName(context.cursor.parserVariant),
                " classic_variant=", MtProxyAdaptivePolicy::classicVariantName(context.cursor.classicVariant),
                " next=", nextRecipeId,
                " next_recipe=", nextRecipeId,
                " next_family=", MtProxyAdaptivePolicy::clientHelloFamilyName(failure.cursor.family),
                " next_sni_variant=", MtProxyAdaptivePolicy::sniVariantName(failure.cursor.sniVariant),
                " next_parser_variant=", MtProxyAdaptivePolicy::parserVariantName(failure.cursor.parserVariant),
                " next_classic_variant=", MtProxyAdaptivePolicy::classicVariantName(failure.cursor.classicVariant),
                " fallback_allowed=", fallbackAllowed ? 1 : 0,
                " classic_fallback_allowed=", context.classicFallbackAllowed ? 1 : 0,
                " exhausted=", failure.recipeExhausted ? 1 : 0,
                " terminal_budget=", failure.terminalBudgetExhausted ? 1 : 0,
                " budget_attempts=", failure.budgetAttempts,
                " budget_elapsed_ms=", (long) failure.budgetElapsedMs,
                " owner_generation=", failure.generation,
                " cursor_generation=", failure.cursor.generation);
        MtProxySocketObservation recipeFailureObservation;
        recipeFailureObservation.phase = "recipe_failed";
        recipeFailureObservation.reason = context.reason.c_str();
        recipeFailureObservation.endpointKey = context.endpointKey;
        recipeFailureObservation.probeKey = context.probeKey;
        recipeFailureObservation.networkEndpointKey = context.networkEndpointKey;
        recipeFailureObservation.publishVisibleStage = false;
        publishObservation(callbacks, recipeFailureObservation);
        if (failure.terminalBudgetExhausted) {
            std::string terminalPhase = normalizeFakeTlsBudgetTerminalPhase(failure.terminalPhase);
            setProxyCheckDiagnostic(callbacks, terminalPhase);
            MtProxySocketObservation terminalObservation;
            terminalObservation.phase = terminalPhase.c_str();
            terminalObservation.reason = phase.c_str();
            terminalObservation.endpointKey = context.endpointKey;
            terminalObservation.probeKey = context.probeKey;
            terminalObservation.networkEndpointKey = context.networkEndpointKey;
            publishObservation(callbacks, terminalObservation);
            MtProxyEndpointPolicy::MtProxyEndpointContext endpointContext = endpointContextFor(context);
            endpointContext.connectionPatternMode = context.connectionPatternMode;
            endpointContext.priority = context.priority;
            MtProxyEndpointPolicy::FailureResult terminalFailure = MtProxyEndpointPolicy::recordFailure(endpointContext, terminalPhase, context.now);
            debug(
                    callbacks,
                    "connection(", context.socketTag, ") mtproxy_startup faketls_budget_exhausted key=", context.endpointKey,
                    " recipe_key=", context.probeKey,
                    " failed_phase=", phase,
                    " terminal_phase=", terminalPhase,
                    " evidence=", failureEvidence,
                    " response_bytes=", context.responseBytes,
                    " response_signature=", (unsigned long long) failure.responseSignature,
                    " attempts=", failure.budgetAttempts,
                    " elapsed_ms=", (long) failure.budgetElapsedMs,
                    " recorded=", terminalFailure.recorded ? 1 : 0,
                    " cooldown_ms=", (long) terminalFailure.cooldownMs,
                    " generation=", failure.generation);
            return;
        }
        if (failure.recipeExhausted) {
            setProxyCheckDiagnostic(callbacks, MtProxyPhase::HandshakeProfilesExhausted);
            MtProxySocketObservation exhaustedObservation;
            exhaustedObservation.phase = MtProxyPhase::HandshakeProfilesExhausted;
            exhaustedObservation.reason = phase.c_str();
            exhaustedObservation.endpointKey = context.endpointKey;
            exhaustedObservation.probeKey = context.probeKey;
            exhaustedObservation.networkEndpointKey = context.networkEndpointKey;
            publishObservation(callbacks, exhaustedObservation);
            MtProxyEndpointPolicy::MtProxyEndpointContext endpointContext = endpointContextFor(context);
            endpointContext.connectionPatternMode = context.connectionPatternMode;
            endpointContext.priority = context.priority;
            MtProxyEndpointPolicy::FailureResult exhaustedFailure = MtProxyEndpointPolicy::recordFailure(endpointContext, MtProxyPhase::HandshakeProfilesExhausted, context.now);
            debug(
                    callbacks,
                    "connection(", context.socketTag, ") mtproxy_startup recipe_exhausted key=", context.endpointKey,
                    " recipe_key=", context.probeKey,
                    " failed_phase=", phase,
                    " evidence=", failureEvidence,
                    " response_bytes=", context.responseBytes,
                    " next=handshake_profiles_exhausted exhausted_recorded=", exhaustedFailure.recorded ? 1 : 0,
                    " cooldown_ms=", (long) exhaustedFailure.cooldownMs,
                    " cached_family=", MtProxyAdaptivePolicy::clientHelloFamilyName(failure.cachedCursor.family),
                    " cached_sni_variant=", MtProxyAdaptivePolicy::sniVariantName(failure.cachedCursor.sniVariant),
                    " cached_parser_variant=", MtProxyAdaptivePolicy::parserVariantName(failure.cachedCursor.parserVariant),
                    " cached_classic_variant=", MtProxyAdaptivePolicy::classicVariantName(failure.cachedCursor.classicVariant),
                    " classic_fallback_allowed=", context.classicFallbackAllowed ? 1 : 0,
                    " generation=", failure.generation);
        }
        return;
    }
    if (dataPathFailureAction.dataPathShapingBackoff) {
        debug(callbacks, "connection(", context.socketTag, ") mtproxy_data shaping_failure phase=", phase, " evidence=", failureEvidence, " action=", dataPathFailureAction.name, " parser_variants=", dataPathFailureAction.allowParserVariants ? 1 : 0);
    }
    MtProxyEndpointPolicy::MtProxyEndpointContext endpointContext = endpointContextFor(context);
    endpointContext.connectionPatternMode = context.connectionPatternMode;
    endpointContext.priority = context.priority;
    MtProxyEndpointPolicy::FailureResult failure = MtProxyEndpointPolicy::recordFailure(endpointContext, phase, context.now);
    if (!failure.recorded) {
        return;
    }
    if (failure.shadowedByUsableSuccess) {
        debug(
                callbacks,
                "connection(", context.socketTag, ") mtproxy_startup endpoint_failure_shadowed_by_success key=", failure.stateKey,
                " phase=", phase,
                " reason=", context.reason,
                " evidence=", failureEvidence,
                " response_bytes=", context.responseBytes,
                " connection_pattern=", context.connectionPatternName,
                " priority=", context.priority,
                " hold_ms=", (long) failure.usableSuccessRemainingMs);
        return;
    }
    debug(
            callbacks,
            "connection(", context.socketTag, ") mtproxy_startup endpoint_failure key=", failure.stateKey,
            " phase=", phase,
            " reason=", context.reason,
            " evidence=", failureEvidence,
            " response_bytes=", context.responseBytes,
            " connection_pattern=", context.connectionPatternName,
            " priority=", context.priority,
            " cooldown_ms=", (long) failure.cooldownMs);
}

void MtProxyEndpointRecorder::recordHandshakeOk(const SuccessContext &context, const Callbacks &callbacks) {
    if (context.endpointKey.empty() && context.networkEndpointKey.empty()) {
        return;
    }
    MtProxyEndpointPolicy::recordHandshakeOk(endpointContextFor(context), context.reason.c_str());
    if (context.fakeTls && !context.probeKey.empty()) {
        MtProxyProbeCoordinator::completeSuccess(probeKeyFor(context), context.ownerToken, context.reason.c_str(), context.recipeUsesGrease, context.recipe, context.now);
        debug(callbacks, "connection(", context.socketTag, ") mtproxy_startup working_recipe_cached key=", context.probeKey, " endpoint=", context.endpointKey, " reason=", context.reason, " recipe=", context.recipeId, " recipe_id=", context.recipeId);
    }
    debug(callbacks, "connection(", context.socketTag, ") mtproxy_startup endpoint_handshake_ok network_key=", context.networkEndpointKey, " key=", context.endpointKey, " recipe_key=", context.recipeCacheKey, " reason=", context.reason, " working_recipe=", context.recipeId, " recipe_id=", context.recipeId);
}

void MtProxyEndpointRecorder::recordDataPathSuccess(const SuccessContext &context, const Callbacks &callbacks) {
    if (context.endpointKey.empty() && context.networkEndpointKey.empty()) {
        return;
    }
    if (context.reason != MtProxyPhase::FirstTlsAppRecv
            && context.reason != MtProxyPhase::FirstMtproxyPacketRecv) {
        if (callbacks.logInvariant) {
            callbacks.logInvariant("endpoint_data_path_success", "invalid_reason");
        }
        debug(callbacks, "connection(", context.socketTag, ") mtproxy_startup endpoint_data_path_success_rejected network_key=", context.networkEndpointKey, " key=", context.endpointKey, " reason=", context.reason);
        return;
    }
    MtProxyEndpointPolicy::DataPathSuccessResult success = MtProxyEndpointPolicy::recordDataPathSuccess(endpointContextFor(context), context.reason.c_str(), context.now);
    if (!success.accepted) {
        if (callbacks.logInvariant) {
            callbacks.logInvariant("endpoint_data_path_success", "policy_rejected");
        }
        return;
    }
    if (context.fakeTls && !context.probeKey.empty()) {
        MtProxyProbeCoordinator::completeSuccess(probeKeyFor(context), context.ownerToken, context.reason.c_str(), context.recipeUsesGrease, context.recipe, context.now);
    }
    debug(callbacks, "connection(", context.socketTag, ") mtproxy_startup endpoint_data_path_success network_key=", context.networkEndpointKey, " key=", context.endpointKey, " recipe_key=", context.probeKey, " reason=", context.reason, " working_recipe=", context.recipeId, " recipe_id=", context.recipeId);
}

void MtProxyEndpointRecorder::recordProfilesExhaustedBackoff(const ProbeBackoffContext &context, const Callbacks &callbacks) {
    setProxyCheckDiagnostic(callbacks, MtProxyPhase::HandshakeProfilesExhausted);
    setSuggestedReconnectHold(callbacks, context.holdMs);
    MtProxySocketObservation observation;
    observation.phase = MtProxyPhase::HandshakeProfilesExhausted;
    observation.reason = "probe_profiles_exhausted";
    observation.endpointKey = context.endpointKey;
    observation.probeKey = context.probeKey;
    observation.networkEndpointKey = context.networkEndpointKey;
    publishObservation(callbacks, observation);
    debug(callbacks, "connection(", context.socketTag, ") mtproxy_startup probe_profiles_exhausted key=", context.probeKey, " endpoint=", context.endpointKey, " owner_generation=", context.generation, " hold_ms=", context.holdMs);
}

void MtProxyEndpointRecorder::recordHandshakeBudgetBackoff(const ProbeBackoffContext &context, const Callbacks &callbacks) {
    std::string terminalPhase = normalizeFakeTlsBudgetTerminalPhase(context.terminalPhase);
    setProxyCheckDiagnostic(callbacks, terminalPhase);
    setSuggestedReconnectHold(callbacks, context.holdMs);
    MtProxySocketObservation observation;
    observation.phase = terminalPhase.c_str();
    observation.reason = "faketls_handshake_budget_backoff";
    observation.endpointKey = context.endpointKey;
    observation.probeKey = context.probeKey;
    observation.networkEndpointKey = context.networkEndpointKey;
    publishObservation(callbacks, observation);
    debug(callbacks, "connection(", context.socketTag, ") mtproxy_startup probe_faketls_budget_backoff key=", context.probeKey, " endpoint=", context.endpointKey, " phase=", terminalPhase, " owner_generation=", context.generation, " hold_ms=", context.holdMs);
}
