/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#include "MtProxyProbeCoordinator.h"

#include <algorithm>
#include <cstring>
#include <map>
#include <pthread.h>

// ProbeKey.key is the existing exact recipe key: host:port:secret_hash:SNI.
static constexpr int64_t MT_PROXY_PROBE_UNSUPPORTED_HOLD_MS = 15 * 60 * 1000;
static constexpr uint32_t MT_PROXY_PROBE_JOIN_WAIT_MS = 250;
// A PROBING owner that neither completes nor advances its recipe within this window is treated
// as wedged/leaked and reclaimed (read-side in beginOrJoin and by the select() reaper) so joiners
// can never be stranded forever. Sized above the worst legitimate single owner attempt: Upload's
// ~40s connect timeout (Connection.cpp) plus the FakeTLS handshake margin.
static constexpr int64_t MT_PROXY_PROBE_OWNER_DEADLINE_MS = 45000;
// A joiner stops waiting on the current owner and self-connects if the owner makes no recipe-cursor
// progress and no handshake heartbeat within this budget. Sized above one FakeTLS server-hello
// freeze window (MT_PROXY_HANDSHAKE_FREEZE_TIMEOUT_MS = 4500) so a healthy-but-slow owner always
// either succeeds or advances its cursor before the budget elapses; only a genuinely wedged owner
// trips it.
static constexpr int64_t MT_PROXY_PROBE_JOIN_TOTAL_BUDGET_MS = 6000;

enum class ProbeStatus : uint8_t {
    IDLE,
    PROBING,
    WORKING_RECIPE_FOUND,
    UNSUPPORTED,
    NETWORK_FAILED,
    QUARANTINED,
};

struct MtProxyProbeState {
    ProbeStatus status = ProbeStatus::IDLE;
    const void *owner = nullptr;
    uint32_t generation = 0;
    int64_t unsupportedUntil = 0;
    int64_t probingUntil = 0;
    int64_t joinBudgetAnchorMs = 0;
    uint32_t joinBudgetAnchorCursorGen = 0;
    uint32_t allowedSniVariants = 0;
    MtProxyAdaptivePolicy::RecipeCursor cursor;
    MtProxyAdaptivePolicy::RecipeCursor workingCursor;
    MtProxyAdaptivePolicy::CompatibilityRecipe workingRecipe;
    bool greaseProbePending = false;
    bool greaseSupported = false;
    bool greaseRejected = false;
    std::string endpointKey;
    std::string networkEndpointKey;
    std::string lastRecipeDiagnostic;
};

static pthread_mutex_t mtProxyProbeCoordinatorMutex = PTHREAD_MUTEX_INITIALIZER;
static std::map<std::string, MtProxyProbeState> mtProxyProbeStates;

static MtProxyProbeCoordinator::Decision decisionFromState(MtProxyProbeCoordinator::DecisionKind kind, const MtProxyProbeState &state) {
    MtProxyProbeCoordinator::Decision decision;
    decision.kind = kind;
    decision.generation = state.generation;
    decision.waitMs = MT_PROXY_PROBE_JOIN_WAIT_MS;
    decision.cursor = state.cursor;
    decision.workingCursor = state.workingCursor;
    decision.workingRecipe = state.workingRecipe;
    decision.lastRecipeDiagnostic = state.lastRecipeDiagnostic;
    decision.greaseProbe.probe = state.greaseProbePending && !state.greaseRejected;
    decision.greaseProbe.supported = state.greaseSupported;
    decision.greaseProbe.rejected = state.greaseRejected;
    decision.greaseProbe.useGrease = decision.greaseProbe.supported || decision.greaseProbe.probe;
    return decision;
}

MtProxyProbeCoordinator::Decision MtProxyProbeCoordinator::beginOrJoin(const ProbeKey &probeKey, const void *owner, int64_t now) {
    if (probeKey.key.empty()) {
        return Decision();
    }

    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    MtProxyProbeState &state = mtProxyProbeStates[probeKey.key];
    state.endpointKey = probeKey.endpointKey;
    state.networkEndpointKey = probeKey.networkEndpointKey;
    if (probeKey.allowedSniVariants != 0) {
        state.allowedSniVariants = probeKey.allowedSniVariants;
    }
    if (state.allowedSniVariants == 0) {
        state.allowedSniVariants = MtProxyAdaptivePolicy::sniVariantMask(MtProxyAdaptivePolicy::SNI_SANITIZED);
    }
    if (state.status == ProbeStatus::UNSUPPORTED && state.unsupportedUntil > now) {
        Decision decision = decisionFromState(DecisionKind::TerminalUnsupported, state);
        pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
        return decision;
    }
    if (state.status == ProbeStatus::UNSUPPORTED && state.unsupportedUntil <= now) {
        state.status = ProbeStatus::IDLE;
        state.owner = nullptr;
        state.unsupportedUntil = 0;
    }
    if (state.status == ProbeStatus::WORKING_RECIPE_FOUND) {
        Decision decision = decisionFromState(DecisionKind::UseWorkingRecipe, state);
        pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
        return decision;
    }
    // Reclaim a PROBING registration whose owner has vanished or whose deadline has lapsed:
    // a leaked or wedged owner must never strand joiners forever. The recipe cursor is kept so
    // the next owner resumes the ladder instead of restarting it (INV-1b).
    if (state.status == ProbeStatus::PROBING
            && (state.owner == nullptr
                || (state.probingUntil != 0 && state.probingUntil <= now))) {
        state.status = ProbeStatus::IDLE;
        state.owner = nullptr;
        state.joinBudgetAnchorMs = 0;
        state.joinBudgetAnchorCursorGen = 0;
    }
    if (state.status == ProbeStatus::PROBING && state.owner != nullptr && state.owner != owner) {
        // A live owner is probing. Join it only while it makes forward progress within a bounded
        // budget; a wedged owner (no recipe-cursor advance and no handshake heartbeat) must not
        // strand this caller, so fall through to StartOwner and let it self-connect (INV-4).
        bool ownerMakingProgress;
        if (state.joinBudgetAnchorMs == 0 || state.cursor.generation != state.joinBudgetAnchorCursorGen) {
            state.joinBudgetAnchorMs = now;
            state.joinBudgetAnchorCursorGen = state.cursor.generation;
            ownerMakingProgress = true;
        } else {
            ownerMakingProgress = (now - state.joinBudgetAnchorMs) < MT_PROXY_PROBE_JOIN_TOTAL_BUDGET_MS;
        }
        if (ownerMakingProgress) {
            Decision decision = decisionFromState(DecisionKind::JoinExisting, state);
            pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
            return decision;
        }
    }
    if (state.cursor.generation == 0
            && state.cursor.family == MtProxyAdaptivePolicy::CLIENT_HELLO_CHROME_MODERN_SOFT_FRAGMENT
            && state.cursor.sniVariant == MtProxyAdaptivePolicy::SNI_ORIGINAL
            && !((state.allowedSniVariants & MtProxyAdaptivePolicy::sniVariantMask(MtProxyAdaptivePolicy::SNI_ORIGINAL)) != 0)) {
        state.cursor = MtProxyAdaptivePolicy::initialCursor(state.allowedSniVariants);
    }
    state.status = ProbeStatus::PROBING;
    state.owner = owner;
    state.probingUntil = now + MT_PROXY_PROBE_OWNER_DEADLINE_MS;
    state.joinBudgetAnchorMs = 0;
    state.joinBudgetAnchorCursorGen = 0;
    state.generation++;
    Decision decision = decisionFromState(DecisionKind::StartOwner, state);
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
    return decision;
}

MtProxyProbeCoordinator::FailureResult MtProxyProbeCoordinator::completeFailure(const ProbeKey &probeKey,
                                                                                const void *owner,
                                                                                const std::string &diagnostic,
                                                                                bool recipeUsesGrease,
                                                                                bool recipeIsGreaseProbe,
                                                                                bool classicFallbackAllowed,
                                                                                int64_t now) {
    FailureResult result;
    if (probeKey.key.empty() || !failureNeedsRecipe(diagnostic)) {
        return result;
    }

    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    MtProxyProbeState &state = mtProxyProbeStates[probeKey.key];
    if (state.owner != nullptr && state.owner != owner) {
        result.generation = state.generation;
        pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
        return result;
    }
    state.status = ProbeStatus::PROBING;
    state.owner = owner;
    state.probingUntil = now + MT_PROXY_PROBE_OWNER_DEADLINE_MS;
    state.endpointKey = probeKey.endpointKey;
    state.networkEndpointKey = probeKey.networkEndpointKey;
    if (probeKey.allowedSniVariants != 0) {
        state.allowedSniVariants = probeKey.allowedSniVariants;
    }
    if (state.allowedSniVariants == 0) {
        state.allowedSniVariants = MtProxyAdaptivePolicy::sniVariantMask(MtProxyAdaptivePolicy::SNI_SANITIZED);
    }

    if (recipeUsesGrease && recipeIsGreaseProbe) {
        state.greaseProbePending = false;
        state.greaseSupported = false;
        state.greaseRejected = true;
    }

    MtProxyAdaptivePolicy::RecipeCursor nextCursor = state.cursor;
    bool hasNextCursor = MtProxyAdaptivePolicy::nextCursor(&nextCursor, diagnostic, state.allowedSniVariants, classicFallbackAllowed);
    if (hasNextCursor) {
        state.cursor = nextCursor;
    } else {
        result.recipeExhausted = true;
    }

    state.lastRecipeDiagnostic = diagnostic;
    if (result.recipeExhausted) {
        state.status = ProbeStatus::UNSUPPORTED;
        state.unsupportedUntil = now + MT_PROXY_PROBE_UNSUPPORTED_HOLD_MS;
        state.owner = nullptr;
        state.generation++;
    }
    result.recorded = true;
    result.generation = state.generation;
    result.cursor = state.cursor;
    result.cachedCursor = state.workingCursor;
    result.lastRecipeDiagnostic = state.lastRecipeDiagnostic;
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
    return result;
}

void MtProxyProbeCoordinator::completeSuccess(const ProbeKey &probeKey,
                                              const void *owner,
                                              const char *reason,
                                              bool recipeUsesGrease,
                                              const MtProxyAdaptivePolicy::CompatibilityRecipe &recipe,
                                              int64_t now) {
    (void) now;
    if (probeKey.key.empty() || reason == nullptr) {
        return;
    }
    if (strcmp(reason, "server_hello_hmac_ok") != 0
            && strcmp(reason, "first_tls_app_recv") != 0
            && strcmp(reason, "first_mtproxy_packet_recv") != 0) {
        return;
    }

    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    MtProxyProbeState &state = mtProxyProbeStates[probeKey.key];
    if (state.owner != nullptr && state.owner != owner && state.status == ProbeStatus::PROBING) {
        pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
        return;
    }
    state.endpointKey = probeKey.endpointKey;
    state.networkEndpointKey = probeKey.networkEndpointKey;
    if (probeKey.allowedSniVariants != 0) {
        state.allowedSniVariants = probeKey.allowedSniVariants;
    }
    state.workingCursor = state.cursor;
    state.workingRecipe = recipe;
    state.status = ProbeStatus::WORKING_RECIPE_FOUND;
    state.owner = nullptr;
    state.lastRecipeDiagnostic.clear();
    if (recipeUsesGrease) {
        state.greaseProbePending = false;
        state.greaseSupported = true;
        state.greaseRejected = false;
    } else if (strcmp(reason, "first_tls_app_recv") == 0 && !state.greaseSupported && !state.greaseRejected) {
        state.greaseProbePending = true;
    }
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
}

void MtProxyProbeCoordinator::completeUnsupported(const ProbeKey &probeKey, const void *owner, int64_t now) {
    if (probeKey.key.empty()) {
        return;
    }
    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    MtProxyProbeState &state = mtProxyProbeStates[probeKey.key];
    if (state.owner != nullptr && state.owner != owner) {
        pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
        return;
    }
    state.status = ProbeStatus::UNSUPPORTED;
    state.owner = nullptr;
    state.unsupportedUntil = now + MT_PROXY_PROBE_UNSUPPORTED_HOLD_MS;
    state.generation++;
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
}

void MtProxyProbeCoordinator::cancelOwner(const ProbeKey &probeKey, const void *owner) {
    if (probeKey.key.empty()) {
        return;
    }
    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    auto it = mtProxyProbeStates.find(probeKey.key);
    if (it != mtProxyProbeStates.end() && it->second.owner == owner) {
        it->second.owner = nullptr;
        it->second.joinBudgetAnchorMs = 0;
        it->second.joinBudgetAnchorCursorGen = 0;
        if (it->second.status == ProbeStatus::PROBING) {
            it->second.status = it->second.workingRecipe.familyName.empty() ? ProbeStatus::IDLE : ProbeStatus::WORKING_RECIPE_FOUND;
        }
    }
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
}

void MtProxyProbeCoordinator::touchOwner(const ProbeKey &probeKey, const void *owner, int64_t now) {
    if (probeKey.key.empty()) {
        return;
    }
    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    auto it = mtProxyProbeStates.find(probeKey.key);
    if (it != mtProxyProbeStates.end()
            && it->second.status == ProbeStatus::PROBING
            && it->second.owner == owner) {
        // The owner reached a handshake milestone: refresh its deadline and reset the joiner
        // budget so a healthy-but-slow owner keeps joiners parked instead of being abandoned (INV-4b).
        it->second.probingUntil = now + MT_PROXY_PROBE_OWNER_DEADLINE_MS;
        it->second.joinBudgetAnchorMs = 0;
        it->second.joinBudgetAnchorCursorGen = it->second.cursor.generation;
    }
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
}

void MtProxyProbeCoordinator::reapExpired(int64_t now) {
    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    for (auto &entry : mtProxyProbeStates) {
        MtProxyProbeState &state = entry.second;
        if (state.status == ProbeStatus::PROBING
                && state.probingUntil != 0 && state.probingUntil <= now) {
            // Wedged/leaked owner that no joiner is re-querying: demote to ownerless IDLE,
            // preserving the recipe cursor. A PROBING entry is never erased here.
            state.status = ProbeStatus::IDLE;
            state.owner = nullptr;
            state.joinBudgetAnchorMs = 0;
            state.joinBudgetAnchorCursorGen = 0;
        } else if (state.status == ProbeStatus::UNSUPPORTED
                && state.unsupportedUntil != 0 && state.unsupportedUntil <= now) {
            state.status = ProbeStatus::IDLE;
            state.owner = nullptr;
            state.unsupportedUntil = 0;
        }
    }
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
}

bool MtProxyProbeCoordinator::failureNeedsRecipe(const std::string &diagnostic) {
    if (diagnostic == "tcp_not_connected") {
        return false;
    }
    return diagnostic == "true_client_hello_timeout"
           || diagnostic == "faketls_server_hello_wait_timeout"
           || diagnostic == "server_closed_after_client_hello"
           || diagnostic == "client_hello_sent_no_server_hello"
           || diagnostic == "tls_alert_after_client_hello"
           || diagnostic == "short_tls_response_after_client_hello"
           || diagnostic == "unrecognized_response_after_client_hello"
           || diagnostic == "unrecognized_tls_response_after_client_hello"
           || diagnostic == "server_hello_hmac_mismatch"
           || diagnostic == "post_handshake_no_appdata";
}

MtProxyAdaptivePolicy::RecipeCursor MtProxyProbeCoordinator::recipeCursorForProbe(const std::string &probeKey) {
    MtProxyAdaptivePolicy::RecipeCursor cursor;
    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    auto it = mtProxyProbeStates.find(probeKey);
    if (it != mtProxyProbeStates.end()) {
        cursor = it->second.cursor;
    }
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
    return cursor;
}

MtProxyAdaptivePolicy::RecipeCursor MtProxyProbeCoordinator::workingRecipeCursorForProbe(const std::string &probeKey) {
    MtProxyAdaptivePolicy::RecipeCursor cursor;
    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    auto it = mtProxyProbeStates.find(probeKey);
    if (it != mtProxyProbeStates.end()) {
        cursor = it->second.workingCursor;
    }
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
    return cursor;
}

MtProxyAdaptivePolicy::CompatibilityRecipe MtProxyProbeCoordinator::workingRecipeForProbe(const std::string &probeKey) {
    MtProxyAdaptivePolicy::CompatibilityRecipe recipe;
    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    auto it = mtProxyProbeStates.find(probeKey);
    if (it != mtProxyProbeStates.end()) {
        recipe = it->second.workingRecipe;
    }
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
    return recipe;
}

std::string MtProxyProbeCoordinator::lastRecipeDiagnosticForProbe(const std::string &probeKey) {
    std::string diagnostic;
    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    auto it = mtProxyProbeStates.find(probeKey);
    if (it != mtProxyProbeStates.end()) {
        diagnostic = it->second.lastRecipeDiagnostic;
    }
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
    return diagnostic;
}

MtProxyProbeCoordinator::GreaseProbeResult MtProxyProbeCoordinator::readGreaseProbeState(const std::string &probeKey) {
    GreaseProbeResult result;
    pthread_mutex_lock(&mtProxyProbeCoordinatorMutex);
    auto it = mtProxyProbeStates.find(probeKey);
    if (it != mtProxyProbeStates.end()) {
        result.probe = it->second.greaseProbePending && !it->second.greaseRejected;
        result.supported = it->second.greaseSupported;
        result.rejected = it->second.greaseRejected;
        result.useGrease = result.supported || result.probe;
    }
    pthread_mutex_unlock(&mtProxyProbeCoordinatorMutex);
    return result;
}
