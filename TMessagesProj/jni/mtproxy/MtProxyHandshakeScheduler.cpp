#include "MtProxyHandshakeScheduler.h"

#include "MtProxyOptions.h"

#include <algorithm>
#include <map>
#include <openssl/rand.h>
#include <pthread.h>
#include <vector>

static pthread_mutex_t proxyHandshakeSchedulerMutex = PTHREAD_MUTEX_INITIALIZER;

static constexpr uint32_t MT_PROXY_HANDSHAKE_BROWSER_HEAVY_DELAY_BASE_MS = 2600;
static constexpr int64_t MT_PROXY_HANDSHAKE_RECENT_SUCCESS_WINDOW_MS = 120000;
static constexpr int64_t MT_PROXY_HANDSHAKE_SUCCESS_COOLDOWN_RESET_MS = 60000;
static constexpr int64_t MT_PROXY_HANDSHAKE_BROWSER_FREEZE_COOLDOWN_MAX_MS = 16000;
static constexpr int64_t MT_PROXY_HANDSHAKE_QUIET_FREEZE_COOLDOWN_MAX_MS = 12000;
static constexpr int64_t MT_PROXY_HANDSHAKE_STRICT_FREEZE_COOLDOWN_MAX_MS = 25000;
static constexpr int64_t MT_PROXY_HANDSHAKE_BROWSER_FAILURE_COOLDOWN_MAX_MS = 8000;
static constexpr int64_t MT_PROXY_HANDSHAKE_QUIET_FAILURE_COOLDOWN_MAX_MS = 5000;
static constexpr int64_t MT_PROXY_HANDSHAKE_STRICT_FAILURE_COOLDOWN_MAX_MS = 12000;
struct MtProxyHandshakeQueuedRequest {
    ConnectionSocket *socket = nullptr;
    uint32_t generation = 0;
    MtProxyRequestClass requestClass = MtProxyRequestClass::ProxyCheck;
    int32_t priority = MT_PROXY_HANDSHAKE_PRIORITY_PROXY_CHECK;
    int32_t timerMode = 0;
    int64_t queuedAt = 0;
    bool ipv6 = false;
};

struct MtProxyHandshakeEndpointState {
    int32_t activeHandshakes = 0;
    int32_t recentSuccesses = 0;
    int32_t freezePenalty = 0;
    int32_t tcpFailurePenalty = 0;
    int32_t handshakeFailurePenalty = 0;
    int64_t lastGrantTime = 0;
    int64_t lastSuccessTime = 0;
    int64_t cooldownUntil = 0;
    std::vector<MtProxyHandshakeQueuedRequest> queuedRequests;
};

static std::map<std::string, MtProxyHandshakeEndpointState> proxyHandshakeEndpoints;

struct MtProxyHandshakeGlobalState {
    int32_t activeHandshakes = 0;
    int32_t activeGenericPushHandshakes = 0;
    int64_t lastGrantTime = 0;
};

static MtProxyHandshakeGlobalState proxyHandshakeGlobal;
static std::map<ConnectionSocket *, MtProxyRequestClass> proxyHandshakeActiveRequestClasses;

int32_t mtProxyHandshakePriorityForRequestClass(MtProxyRequestClass requestClass) {
    switch (requestClass) {
        case MtProxyRequestClass::Generic:
            return MT_PROXY_HANDSHAKE_PRIORITY_GENERIC;
        case MtProxyRequestClass::Media:
            return MT_PROXY_HANDSHAKE_PRIORITY_MEDIA;
        case MtProxyRequestClass::Push:
            return MT_PROXY_HANDSHAKE_PRIORITY_PUSH;
        case MtProxyRequestClass::Download:
            return MT_PROXY_HANDSHAKE_PRIORITY_DOWNLOAD;
        case MtProxyRequestClass::Upload:
            return MT_PROXY_HANDSHAKE_PRIORITY_UPLOAD;
        case MtProxyRequestClass::ProxyCheck:
        default:
            return MT_PROXY_HANDSHAKE_PRIORITY_PROXY_CHECK;
    }
}

MtProxyRequestClass mtProxyRequestClassForPriority(int32_t priority) {
    if (priority == MT_PROXY_HANDSHAKE_PRIORITY_GENERIC) {
        return MtProxyRequestClass::Generic;
    }
    if (priority == MT_PROXY_HANDSHAKE_PRIORITY_MEDIA) {
        return MtProxyRequestClass::Media;
    }
    if (priority == MT_PROXY_HANDSHAKE_PRIORITY_PUSH) {
        return MtProxyRequestClass::Push;
    }
    if (priority == MT_PROXY_HANDSHAKE_PRIORITY_DOWNLOAD) {
        return MtProxyRequestClass::Download;
    }
    if (priority == MT_PROXY_HANDSHAKE_PRIORITY_UPLOAD) {
        return MtProxyRequestClass::Upload;
    }
    return MtProxyRequestClass::ProxyCheck;
}

const char *mtProxyRequestClassName(MtProxyRequestClass requestClass) {
    switch (requestClass) {
        case MtProxyRequestClass::Generic:
            return "generic";
        case MtProxyRequestClass::Media:
            return "media";
        case MtProxyRequestClass::Push:
            return "push";
        case MtProxyRequestClass::Download:
            return "download";
        case MtProxyRequestClass::Upload:
            return "upload";
        case MtProxyRequestClass::ProxyCheck:
        default:
            return "proxy_check";
    }
}

static bool mtProxyHandshakeIsGenericOrPush(MtProxyRequestClass requestClass) {
    return requestClass == MtProxyRequestClass::Generic || requestClass == MtProxyRequestClass::Push;
}

static bool mtProxyHandshakeIsHeavyRequestClass(MtProxyRequestClass requestClass) {
    return requestClass == MtProxyRequestClass::Download
            || requestClass == MtProxyRequestClass::Upload
            || requestClass == MtProxyRequestClass::ProxyCheck;
}

static uint32_t mtProxySecureRandomBounded(uint32_t bound) {
    if (bound <= 1) {
        return 0;
    }
    uint32_t threshold = (0u - bound) % bound;
    uint32_t v;
    do {
        RAND_bytes((uint8_t *) &v, sizeof(v));
    } while (v < threshold);
    return v % bound;
}

static bool mtProxyHandshakeSchedulerUsesCooldown(int32_t mode) {
    mode = normalizeMtProxyConnectionPatternOption(mode);
    return mode == MT_PROXY_CONNECTION_PATTERN_BROWSER || mode == MT_PROXY_CONNECTION_PATTERN_QUIET || mode == MT_PROXY_CONNECTION_PATTERN_STRICT;
}

bool mtProxyHandshakeSchedulerUsesAdmission(int32_t mode) {
    return normalizeMtProxyConnectionPatternOption(mode) != MT_PROXY_CONNECTION_PATTERN_OFF;
}

static bool mtProxyCooldownBlocksPriority(const MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode, int32_t priority) {
    if (!mtProxyHandshakeSchedulerUsesCooldown(mode) || state.cooldownUntil <= now) {
        return false;
    }
    if (priority <= MT_PROXY_HANDSHAKE_PRIORITY_BYPASS) {
        return false;
    }
    if (state.tcpFailurePenalty > 0) {
        return priority > MT_PROXY_HANDSHAKE_PRIORITY_BYPASS;
    }
    if (state.freezePenalty > 0 || state.handshakeFailurePenalty > 0) {
        return priority > MT_PROXY_HANDSHAKE_PRIORITY_BYPASS;
    }
    return priority > MT_PROXY_HANDSHAKE_PRIORITY_MEDIA;
}

static uint32_t mtProxyHandshakeGrantDelay(int32_t mode) {
    mode = normalizeMtProxyConnectionPatternOption(mode);
    if (mode == MT_PROXY_CONNECTION_PATTERN_STRICT) {
        return 3000 + mtProxySecureRandomBounded(3001);
    }
    if (mode == MT_PROXY_CONNECTION_PATTERN_QUIET) {
        return 1200 + mtProxySecureRandomBounded(1301);
    }
    if (mode == MT_PROXY_CONNECTION_PATTERN_BROWSER) {
        return 450 + mtProxySecureRandomBounded(551);
    }
    return 90 + mtProxySecureRandomBounded(161);
}

static uint32_t mtProxyHandshakeSpacingDelay(const MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode) {
    mode = normalizeMtProxyConnectionPatternOption(mode);
    if (mode != MT_PROXY_CONNECTION_PATTERN_BROWSER && mode != MT_PROXY_CONNECTION_PATTERN_QUIET && mode != MT_PROXY_CONNECTION_PATTERN_STRICT) {
        return 0;
    }
    if (state.lastGrantTime <= 0) {
        return 0;
    }
    uint32_t minGap;
    if (mode == MT_PROXY_CONNECTION_PATTERN_STRICT) {
        minGap = 1000 + mtProxySecureRandomBounded(751);
    } else if (mode == MT_PROXY_CONNECTION_PATTERN_QUIET) {
        minGap = 650 + mtProxySecureRandomBounded(451);
    } else {
        minGap = 450 + mtProxySecureRandomBounded(551);
    }
    int64_t elapsed = now - state.lastGrantTime;
    if (elapsed >= (int64_t) minGap) {
        return 0;
    }
    return (uint32_t) ((int64_t) minGap - elapsed);
}

static void mtProxyRecordHandshakeGrant(MtProxyHandshakeEndpointState &state, int64_t now, uint32_t delay) {
    int64_t grantTime = now + delay;
    if (state.lastGrantTime < grantTime) {
        state.lastGrantTime = grantTime;
    }
}

static void mtProxyRecordActiveRequestClassLocked(ConnectionSocket *socket, MtProxyRequestClass requestClass) {
    if (socket == nullptr) {
        return;
    }
    auto existing = proxyHandshakeActiveRequestClasses.find(socket);
    if (existing != proxyHandshakeActiveRequestClasses.end()) {
        if (mtProxyHandshakeIsGenericOrPush(existing->second) && proxyHandshakeGlobal.activeGenericPushHandshakes > 0) {
            proxyHandshakeGlobal.activeGenericPushHandshakes--;
        }
        proxyHandshakeActiveRequestClasses.erase(existing);
    }
    proxyHandshakeActiveRequestClasses[socket] = requestClass;
    if (mtProxyHandshakeIsGenericOrPush(requestClass)) {
        proxyHandshakeGlobal.activeGenericPushHandshakes++;
    }
}

static void mtProxyReleaseActiveRequestClassLocked(ConnectionSocket *socket) {
    auto existing = proxyHandshakeActiveRequestClasses.find(socket);
    if (existing == proxyHandshakeActiveRequestClasses.end()) {
        return;
    }
    if (mtProxyHandshakeIsGenericOrPush(existing->second) && proxyHandshakeGlobal.activeGenericPushHandshakes > 0) {
        proxyHandshakeGlobal.activeGenericPushHandshakes--;
    }
    proxyHandshakeActiveRequestClasses.erase(existing);
}

uint32_t mtProxyHandshakeSchedulerRetryDelay(int64_t now, int64_t cooldownUntil, int32_t priority, int32_t mode) {
    mode = normalizeMtProxyConnectionPatternOption(mode);
    uint32_t delay;
    if (mode == MT_PROXY_CONNECTION_PATTERN_STRICT) {
        uint32_t baseDelay = priority <= MT_PROXY_HANDSHAKE_PRIORITY_MEDIA ? 1800 : 3500;
        delay = baseDelay + mtProxySecureRandomBounded(priority <= MT_PROXY_HANDSHAKE_PRIORITY_MEDIA ? 2201 : 3001);
    } else if (mode == MT_PROXY_CONNECTION_PATTERN_QUIET) {
        uint32_t baseDelay = priority <= MT_PROXY_HANDSHAKE_PRIORITY_MEDIA ? 800 : 1600;
        delay = baseDelay + mtProxySecureRandomBounded(priority <= MT_PROXY_HANDSHAKE_PRIORITY_MEDIA ? 1001 : 1601);
    } else if (mode == MT_PROXY_CONNECTION_PATTERN_BROWSER) {
        uint32_t baseDelay;
        uint32_t jitter;
        if (priority <= MT_PROXY_HANDSHAKE_PRIORITY_GENERIC) {
            baseDelay = 500;
            jitter = 701;
        } else if (priority <= MT_PROXY_HANDSHAKE_PRIORITY_MEDIA) {
            baseDelay = 900;
            jitter = 1101;
        } else if (priority <= MT_PROXY_HANDSHAKE_PRIORITY_PUSH) {
            baseDelay = 1400;
            jitter = 1301;
        } else {
            baseDelay = MT_PROXY_HANDSHAKE_BROWSER_HEAVY_DELAY_BASE_MS;
            jitter = 2601;
        }
        delay = baseDelay + mtProxySecureRandomBounded(jitter);
    } else {
        uint32_t baseDelay = priority <= MT_PROXY_HANDSHAKE_PRIORITY_MEDIA ? 180 : 420;
        delay = baseDelay + mtProxySecureRandomBounded(priority <= MT_PROXY_HANDSHAKE_PRIORITY_MEDIA ? 181 : 421);
    }
    if (mtProxyHandshakeSchedulerUsesCooldown(mode) && cooldownUntil > now) {
        int64_t cooldownDelay = cooldownUntil - now;
        int64_t maxDelay = mode == MT_PROXY_CONNECTION_PATTERN_STRICT
                ? MT_PROXY_HANDSHAKE_STRICT_FAILURE_COOLDOWN_MAX_MS
                : (mode == MT_PROXY_CONNECTION_PATTERN_BROWSER ? MT_PROXY_HANDSHAKE_BROWSER_FAILURE_COOLDOWN_MAX_MS : MT_PROXY_HANDSHAKE_QUIET_FAILURE_COOLDOWN_MAX_MS);
        if (cooldownDelay > maxDelay) {
            cooldownDelay = maxDelay;
        }
        if ((int64_t) delay < cooldownDelay) {
            delay = (uint32_t) cooldownDelay;
        }
    }
    if (delay < 50) {
        delay = 50;
    }
    return delay;
}

static bool mtProxyHandshakeEndpointHasRecentSuccess(const MtProxyHandshakeEndpointState &state, int64_t now) {
    return state.recentSuccesses > 0
            && state.lastSuccessTime > 0
            && now - state.lastSuccessTime < MT_PROXY_HANDSHAKE_RECENT_SUCCESS_WINDOW_MS;
}

static int32_t mtProxyHandshakeActiveLimit(const MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode) {
    mode = normalizeMtProxyConnectionPatternOption(mode);
    if (mtProxyHandshakeSchedulerUsesCooldown(mode) && state.cooldownUntil > now) {
        return MT_PROXY_STARTUP_ENDPOINT_HANDSHAKES_COLD;
    }
    if (mtProxyHandshakeEndpointHasRecentSuccess(state, now)) {
        return MT_PROXY_STARTUP_ENDPOINT_HANDSHAKES_USABLE;
    }
    return MT_PROXY_STARTUP_ENDPOINT_HANDSHAKES_COLD;
}

static int32_t mtProxyHandshakeGlobalActiveLimit(int64_t now, int32_t mode) {
    (void) now;
    mode = normalizeMtProxyConnectionPatternOption(mode);
    if (mode == MT_PROXY_CONNECTION_PATTERN_SOFT) {
        return MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_SOFT;
    }
    if (mode == MT_PROXY_CONNECTION_PATTERN_BROWSER) {
        return MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_BROWSER;
    }
    if (mode == MT_PROXY_CONNECTION_PATTERN_QUIET) {
        return MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_QUIET;
    }
    if (mode == MT_PROXY_CONNECTION_PATTERN_STRICT) {
        return MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_STRICT;
    }
    return MT_PROXY_STARTUP_GLOBAL_HANDSHAKES_SOFT;
}

static uint32_t mtProxyHandshakeGlobalSpacingDelay(int64_t now, int32_t mode) {
    mode = normalizeMtProxyConnectionPatternOption(mode);
    if (mode != MT_PROXY_CONNECTION_PATTERN_BROWSER && mode != MT_PROXY_CONNECTION_PATTERN_QUIET && mode != MT_PROXY_CONNECTION_PATTERN_STRICT) {
        return 0;
    }
    if (proxyHandshakeGlobal.lastGrantTime <= 0) {
        return 0;
    }
    uint32_t minGap;
    if (mode == MT_PROXY_CONNECTION_PATTERN_STRICT) {
        minGap = 1200 + mtProxySecureRandomBounded(901);
    } else if (mode == MT_PROXY_CONNECTION_PATTERN_QUIET) {
        minGap = 800 + mtProxySecureRandomBounded(601);
    } else {
        minGap = 500 + mtProxySecureRandomBounded(501);
    }
    int64_t elapsed = now - proxyHandshakeGlobal.lastGrantTime;
    if (elapsed >= (int64_t) minGap) {
        return 0;
    }
    return (uint32_t) ((int64_t) minGap - elapsed);
}

static void mtProxyRecordGlobalHandshakeGrant(int64_t now, uint32_t delay) {
    int64_t grantTime = now + delay;
    if (proxyHandshakeGlobal.lastGrantTime < grantTime) {
        proxyHandshakeGlobal.lastGrantTime = grantTime;
    }
}

static bool mtProxyHandshakeHasHigherPriorityQueued(const MtProxyHandshakeEndpointState &state, int32_t priority) {
    for (const auto &request : state.queuedRequests) {
        if (request.priority < priority) {
            return true;
        }
    }
    return false;
}

static bool mtProxyHandshakeHasHigherPriorityQueuedGlobal(int32_t priority) {
    for (const auto &entry : proxyHandshakeEndpoints) {
        for (const auto &request : entry.second.queuedRequests) {
            if (request.priority < priority) {
                return true;
            }
        }
    }
    return false;
}

static bool mtProxyHandshakeHasGenericOrPushQueuedGlobal() {
    for (const auto &entry : proxyHandshakeEndpoints) {
        for (const auto &request : entry.second.queuedRequests) {
            if (mtProxyHandshakeIsGenericOrPush(request.requestClass)) {
                return true;
            }
        }
    }
    return false;
}

static bool mtProxyHandshakeHeavyBlockedBeforeUsable(const MtProxyHandshakeEndpointState &state, int64_t now, MtProxyRequestClass requestClass) {
    if (!mtProxyHandshakeIsHeavyRequestClass(requestClass) || mtProxyHandshakeEndpointHasRecentSuccess(state, now)) {
        return false;
    }
    return proxyHandshakeGlobal.activeGenericPushHandshakes > 0 || mtProxyHandshakeHasGenericOrPushQueuedGlobal();
}

static void mtProxyRemoveQueuedRequestLocked(ConnectionSocket *socket) {
    for (auto &entry : proxyHandshakeEndpoints) {
        auto &queue = entry.second.queuedRequests;
        queue.erase(std::remove_if(queue.begin(), queue.end(), [socket](const MtProxyHandshakeQueuedRequest &request) {
            return request.socket == socket;
        }), queue.end());
    }
}

void mtProxyHandshakeSchedulerCancel(ConnectionSocket *socket) {
    pthread_mutex_lock(&proxyHandshakeSchedulerMutex);
    mtProxyRemoveQueuedRequestLocked(socket);
    pthread_mutex_unlock(&proxyHandshakeSchedulerMutex);
}

static bool mtProxyTakeNextQueuedRequestGlobalLocked(int64_t now, int32_t mode, MtProxyHandshakeQueuedGrant &grant) {
    int32_t globalLimit = mtProxyHandshakeGlobalActiveLimit(now, mode);
    if (proxyHandshakeGlobal.activeHandshakes >= globalLimit) {
        return false;
    }

    std::string bestKey;
    int bestIndex = -1;
    int32_t bestPriority = MT_PROXY_HANDSHAKE_PRIORITY_PROXY_CHECK + 1;
    int64_t bestQueuedAt = 0;
    for (auto &entry : proxyHandshakeEndpoints) {
        int32_t endpointLimit = mtProxyHandshakeActiveLimit(entry.second, now, mode);
        if (entry.second.activeHandshakes >= endpointLimit || entry.second.queuedRequests.empty()) {
            continue;
        }
        auto &queue = entry.second.queuedRequests;
        for (int i = 0; i < (int) queue.size(); i++) {
            const auto &candidate = queue[i];
            if (mtProxyCooldownBlocksPriority(entry.second, now, mode, candidate.priority)) {
                continue;
            }
            if (mtProxyHandshakeHeavyBlockedBeforeUsable(entry.second, now, candidate.requestClass)) {
                continue;
            }
            if (bestIndex < 0 || candidate.priority < bestPriority || (candidate.priority == bestPriority && candidate.queuedAt < bestQueuedAt)) {
                bestKey = entry.first;
                bestIndex = i;
                bestPriority = candidate.priority;
                bestQueuedAt = candidate.queuedAt;
            }
        }
    }
    if (bestIndex < 0) {
        return false;
    }

    MtProxyHandshakeEndpointState &bestState = proxyHandshakeEndpoints[bestKey];
    MtProxyHandshakeQueuedRequest request = bestState.queuedRequests[bestIndex];
    bestState.queuedRequests.erase(bestState.queuedRequests.begin() + bestIndex);
    bestState.activeHandshakes++;
    proxyHandshakeGlobal.activeHandshakes++;
    mtProxyRecordActiveRequestClassLocked(request.socket, request.requestClass);
    grant.socket = request.socket;
    grant.key = bestKey;
    grant.generation = request.generation;
    grant.timerMode = request.timerMode;
    grant.requestClass = request.requestClass;
    grant.priority = request.priority;
    grant.ipv6 = request.ipv6;
    grant.endpointActive = bestState.activeHandshakes;
    grant.endpointQueued = (int32_t) bestState.queuedRequests.size();
    grant.globalActive = proxyHandshakeGlobal.activeHandshakes;
    grant.globalLimit = globalLimit;
    return true;
}

static void mtProxyClampCooldown(MtProxyHandshakeEndpointState &state, int64_t now, int64_t maxCooldownMs) {
    if (maxCooldownMs <= 0) {
        state.cooldownUntil = now;
        return;
    }
    int64_t maxCooldownUntil = now + maxCooldownMs;
    if (state.cooldownUntil > maxCooldownUntil) {
        state.cooldownUntil = maxCooldownUntil;
    }
}

static void mtProxyApplyFreezeCooldown(MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode) {
    mode = normalizeMtProxyConnectionPatternOption(mode);
    if (!mtProxyHandshakeSchedulerUsesCooldown(mode)) {
        return;
    }
    if (state.freezePenalty < 3) {
        state.freezePenalty++;
    }
    state.tcpFailurePenalty = 0;
    state.handshakeFailurePenalty = 0;
    state.recentSuccesses = 0;
    int64_t base;
    int64_t jitter;
    int64_t maxCooldown;
    if (mode == MT_PROXY_CONNECTION_PATTERN_STRICT) {
        maxCooldown = MT_PROXY_HANDSHAKE_STRICT_FREEZE_COOLDOWN_MAX_MS;
        if (state.freezePenalty <= 1) {
            base = 8000;
        } else if (state.freezePenalty == 2) {
            base = 14000;
        } else {
            base = 20000;
        }
        jitter = mtProxySecureRandomBounded(5001);
    } else if (mode == MT_PROXY_CONNECTION_PATTERN_BROWSER) {
        maxCooldown = MT_PROXY_HANDSHAKE_BROWSER_FREEZE_COOLDOWN_MAX_MS;
        if (state.freezePenalty <= 1) {
            base = 5000;
        } else if (state.freezePenalty == 2) {
            base = 9000;
        } else {
            base = 13000;
        }
        jitter = mtProxySecureRandomBounded(3001);
    } else {
        maxCooldown = MT_PROXY_HANDSHAKE_QUIET_FREEZE_COOLDOWN_MAX_MS;
        if (state.freezePenalty <= 1) {
            base = 4000;
        } else if (state.freezePenalty == 2) {
            base = 7000;
        } else {
            base = 10000;
        }
        jitter = mtProxySecureRandomBounded(2501);
    }
    state.cooldownUntil = now + base + jitter;
    mtProxyClampCooldown(state, now, maxCooldown);
}

static void mtProxyApplyTcpFailureCooldown(MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode) {
    mode = normalizeMtProxyConnectionPatternOption(mode);
    if (!mtProxyHandshakeSchedulerUsesCooldown(mode)) {
        return;
    }
    if (state.tcpFailurePenalty < 3) {
        state.tcpFailurePenalty++;
    }
    state.handshakeFailurePenalty = 0;
    state.recentSuccesses = 0;
    int64_t base;
    int64_t jitter;
    int64_t maxCooldown;
    if (mode == MT_PROXY_CONNECTION_PATTERN_STRICT) {
        maxCooldown = MT_PROXY_HANDSHAKE_STRICT_FAILURE_COOLDOWN_MAX_MS;
        if (state.tcpFailurePenalty <= 1) {
            base = 4500;
        } else if (state.tcpFailurePenalty == 2) {
            base = 8000;
        } else {
            base = 11000;
        }
        jitter = mtProxySecureRandomBounded(2501);
    } else if (mode == MT_PROXY_CONNECTION_PATTERN_BROWSER) {
        maxCooldown = MT_PROXY_HANDSHAKE_BROWSER_FAILURE_COOLDOWN_MAX_MS;
        if (state.tcpFailurePenalty <= 1) {
            base = 2200;
        } else if (state.tcpFailurePenalty == 2) {
            base = 4000;
        } else {
            base = 6500;
        }
        jitter = mtProxySecureRandomBounded(1801);
    } else {
        maxCooldown = MT_PROXY_HANDSHAKE_QUIET_FAILURE_COOLDOWN_MAX_MS;
        if (state.tcpFailurePenalty <= 1) {
            base = 1800;
        } else if (state.tcpFailurePenalty == 2) {
            base = 3000;
        } else {
            base = 4500;
        }
        jitter = mtProxySecureRandomBounded(1201);
    }
    int64_t nextCooldown = now + base + jitter;
    if (state.cooldownUntil < nextCooldown) {
        state.cooldownUntil = nextCooldown;
    }
    mtProxyClampCooldown(state, now, maxCooldown);
}

static void mtProxyApplyFailureCooldown(MtProxyHandshakeEndpointState &state, int64_t now, int32_t mode) {
    mode = normalizeMtProxyConnectionPatternOption(mode);
    if (!mtProxyHandshakeSchedulerUsesCooldown(mode)) {
        return;
    }
    state.tcpFailurePenalty = 0;
    if (state.handshakeFailurePenalty < 3) {
        state.handshakeFailurePenalty++;
    }
    state.recentSuccesses = 0;
    int64_t base;
    int64_t jitter;
    int64_t maxCooldown;
    if (mode == MT_PROXY_CONNECTION_PATTERN_STRICT) {
        base = 4000;
        jitter = mtProxySecureRandomBounded(4001);
        maxCooldown = MT_PROXY_HANDSHAKE_STRICT_FAILURE_COOLDOWN_MAX_MS;
    } else if (mode == MT_PROXY_CONNECTION_PATTERN_BROWSER) {
        base = 2500;
        jitter = mtProxySecureRandomBounded(2501);
        maxCooldown = MT_PROXY_HANDSHAKE_BROWSER_FAILURE_COOLDOWN_MAX_MS;
    } else {
        base = 1500;
        jitter = mtProxySecureRandomBounded(2001);
        maxCooldown = MT_PROXY_HANDSHAKE_QUIET_FAILURE_COOLDOWN_MAX_MS;
    }
    int64_t nextCooldown = now + base + jitter;
    if (state.cooldownUntil < nextCooldown) {
        state.cooldownUntil = nextCooldown;
    }
    mtProxyClampCooldown(state, now, maxCooldown);
}

static void mtProxyRecordHandshakeSuccess(MtProxyHandshakeEndpointState &state, int64_t now) {
    if (state.lastSuccessTime <= 0 || now - state.lastSuccessTime > MT_PROXY_HANDSHAKE_SUCCESS_COOLDOWN_RESET_MS) {
        state.recentSuccesses = 0;
    }
    if (state.recentSuccesses < 4) {
        state.recentSuccesses++;
    }
    state.lastSuccessTime = now;
    state.freezePenalty = 0;
    state.tcpFailurePenalty = 0;
    state.handshakeFailurePenalty = 0;
    state.cooldownUntil = 0;
}

MtProxyHandshakeAdmissionDecision mtProxyHandshakeSchedulerAdmit(const MtProxyHandshakeAdmissionRequest &request) {
    MtProxyHandshakeAdmissionDecision decision;
    pthread_mutex_lock(&proxyHandshakeSchedulerMutex);
    mtProxyRemoveQueuedRequestLocked(request.socket);
    MtProxyHandshakeEndpointState &state = proxyHandshakeEndpoints[request.key];
    int32_t mode = normalizeMtProxyConnectionPatternOption(request.connectionPatternMode);
    int32_t endpointLimit = mtProxyHandshakeActiveLimit(state, request.now, mode);
    int32_t globalLimit = mtProxyHandshakeGlobalActiveLimit(request.now, mode);
    MtProxyRequestClass requestClass = request.requestClass;
    bool cooldownBlocks = mtProxyCooldownBlocksPriority(state, request.now, mode, request.priority);
    bool globalLimitReached = proxyHandshakeGlobal.activeHandshakes >= globalLimit;
    bool higherPriorityQueued = mtProxyHandshakeHasHigherPriorityQueued(state, request.priority) || mtProxyHandshakeHasHigherPriorityQueuedGlobal(request.priority);
    bool heavyBlocked = mtProxyHandshakeHeavyBlockedBeforeUsable(state, request.now, requestClass);
    if (globalLimitReached || cooldownBlocks || state.activeHandshakes >= endpointLimit || higherPriorityQueued || heavyBlocked) {
        MtProxyHandshakeQueuedRequest queuedRequest;
        queuedRequest.socket = request.socket;
        queuedRequest.generation = request.generation;
        queuedRequest.requestClass = requestClass;
        queuedRequest.priority = request.priority;
        queuedRequest.timerMode = request.timerMode;
        queuedRequest.queuedAt = request.now;
        queuedRequest.ipv6 = request.ipv6;
        state.queuedRequests.push_back(queuedRequest);
        decision.queued = true;
        decision.publishQueue = !request.queueAlreadyPublished;
        decision.delayMs = mtProxyHandshakeSchedulerRetryDelay(request.now, state.cooldownUntil, request.priority, mode);
    } else {
        state.activeHandshakes++;
        proxyHandshakeGlobal.activeHandshakes++;
        mtProxyRecordActiveRequestClassLocked(request.socket, requestClass);
        decision.granted = true;
        decision.delayMs = mtProxyHandshakeSpacingDelay(state, request.now, mode);
        uint32_t globalDelay = mtProxyHandshakeGlobalSpacingDelay(request.now, mode);
        if (decision.delayMs < globalDelay) {
            decision.delayMs = globalDelay;
        }
        if (state.activeHandshakes > 1 || proxyHandshakeGlobal.activeHandshakes > 1) {
            uint32_t fanoutDelay = mtProxyHandshakeGrantDelay(mode);
            if (decision.delayMs < fanoutDelay) {
                decision.delayMs = fanoutDelay;
            }
        }
        mtProxyRecordHandshakeGrant(state, request.now, decision.delayMs);
        mtProxyRecordGlobalHandshakeGrant(request.now, decision.delayMs);
    }
    decision.endpointActive = state.activeHandshakes;
    decision.endpointLimit = endpointLimit;
    decision.globalActive = proxyHandshakeGlobal.activeHandshakes;
    decision.globalLimit = globalLimit;
    decision.queuedCount = (int32_t) state.queuedRequests.size();
    decision.recentSuccesses = state.recentSuccesses;
    decision.cooldownRemainingMs = std::max<int64_t>(0, state.cooldownUntil - request.now);
    pthread_mutex_unlock(&proxyHandshakeSchedulerMutex);
    return decision;
}

MtProxyHandshakeReleaseDecision mtProxyHandshakeSchedulerRelease(const MtProxyHandshakeReleaseRequest &request) {
    MtProxyHandshakeReleaseDecision decision;
    int32_t mode = normalizeMtProxyConnectionPatternOption(request.connectionPatternMode);
    if (!request.hadAdmission) {
        decision.ignored = true;
        decision.globalLimit = mtProxyHandshakeGlobalActiveLimit(request.now, mode);
        return decision;
    }

    pthread_mutex_lock(&proxyHandshakeSchedulerMutex);
    MtProxyHandshakeEndpointState &state = proxyHandshakeEndpoints[request.key];
    mtProxyRemoveQueuedRequestLocked(request.socket);
    if (request.wasActive && state.activeHandshakes > 0) {
        state.activeHandshakes--;
    }
    if (request.wasActive && proxyHandshakeGlobal.activeHandshakes > 0) {
        proxyHandshakeGlobal.activeHandshakes--;
    }
    if (request.wasActive) {
        mtProxyReleaseActiveRequestClassLocked(request.socket);
    }
    if (request.succeeded) {
        mtProxyRecordHandshakeSuccess(state, request.now);
    } else if (request.shouldApplyTcpFailureCooldown && mtProxyHandshakeSchedulerUsesCooldown(mode)) {
        mtProxyApplyTcpFailureCooldown(state, request.now, mode);
        decision.cooldownKind = MtProxyHandshakeCooldownKind::TcpFailure;
        decision.cooldownPenalty = state.tcpFailurePenalty;
    } else if (request.shouldApplyFreezeCooldown && mtProxyHandshakeSchedulerUsesCooldown(mode)) {
        mtProxyApplyFreezeCooldown(state, request.now, mode);
        decision.cooldownKind = MtProxyHandshakeCooldownKind::Freeze;
        decision.cooldownPenalty = state.freezePenalty;
    } else if (!request.succeeded && request.wasActive && !request.neutralSchedulerWaitRelease && mtProxyHandshakeSchedulerUsesCooldown(mode)) {
        mtProxyApplyFailureCooldown(state, request.now, mode);
        decision.cooldownKind = MtProxyHandshakeCooldownKind::Failure;
        decision.cooldownPenalty = state.handshakeFailurePenalty;
    } else if (request.shouldApplyFreezeCooldown) {
        state.recentSuccesses = 0;
        decision.cooldownKind = MtProxyHandshakeCooldownKind::FreezeObserved;
    }
    decision.cooldownRemainingMs = std::max<int64_t>(0, state.cooldownUntil - request.now);
    decision.queuedCount = (int32_t) state.queuedRequests.size();
    if (request.suppressQueuedGrant) {
        decision.publishHoldPhase = true;
    }
    if (!request.suppressQueuedGrant && mtProxyHandshakeSchedulerUsesAdmission(mode)) {
        decision.hasNextRequest = mtProxyTakeNextQueuedRequestGlobalLocked(request.now, mode, decision.nextRequest);
        if (decision.hasNextRequest) {
            MtProxyHandshakeEndpointState &nextState = proxyHandshakeEndpoints[decision.nextRequest.key];
            decision.nextRequest.delayMs = mtProxyHandshakeGrantDelay(mode);
            uint32_t nextGlobalDelay = mtProxyHandshakeGlobalSpacingDelay(request.now, mode);
            if (decision.nextRequest.delayMs < nextGlobalDelay) {
                decision.nextRequest.delayMs = nextGlobalDelay;
            }
            mtProxyRecordHandshakeGrant(nextState, request.now, decision.nextRequest.delayMs);
            mtProxyRecordGlobalHandshakeGrant(request.now, decision.nextRequest.delayMs);
        }
    }
    decision.globalActive = proxyHandshakeGlobal.activeHandshakes;
    decision.globalLimit = mtProxyHandshakeGlobalActiveLimit(request.now, mode);
    pthread_mutex_unlock(&proxyHandshakeSchedulerMutex);
    return decision;
}
