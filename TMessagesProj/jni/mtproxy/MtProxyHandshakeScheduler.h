#ifndef MTPROXYHANDSHAKESCHEDULER_H
#define MTPROXYHANDSHAKESCHEDULER_H

#include <cstdint>
#include <string>

class ConnectionSocket;

static constexpr int32_t MT_PROXY_HANDSHAKE_PRIORITY_BYPASS = -1;
static constexpr int32_t MT_PROXY_HANDSHAKE_PRIORITY_GENERIC = 0;
static constexpr int32_t MT_PROXY_HANDSHAKE_PRIORITY_MEDIA = 1;
static constexpr int32_t MT_PROXY_HANDSHAKE_PRIORITY_PUSH = 2;
static constexpr int32_t MT_PROXY_HANDSHAKE_PRIORITY_DOWNLOAD = 3;
static constexpr int32_t MT_PROXY_HANDSHAKE_PRIORITY_UPLOAD = 4;
static constexpr int32_t MT_PROXY_HANDSHAKE_PRIORITY_PROXY_CHECK = 5;

enum class MtProxyRequestClass : uint8_t {
    Generic,
    Media,
    Push,
    Download,
    Upload,
    ProxyCheck,
};

struct MtProxyHandshakeAdmissionRequest {
    ConnectionSocket *socket = nullptr;
    std::string key;
    uint32_t generation = 0;
    MtProxyRequestClass requestClass = MtProxyRequestClass::Generic;
    int32_t priority = 0;
    int32_t timerMode = 0;
    int32_t connectionPatternMode = 0;
    bool ipv6 = false;
    bool queueAlreadyPublished = false;
    int64_t now = 0;
};

struct MtProxyHandshakeAdmissionDecision {
    bool queued = false;
    bool granted = false;
    bool publishQueue = false;
    uint32_t delayMs = 0;
    int32_t endpointActive = 0;
    int32_t endpointLimit = 0;
    int32_t globalActive = 0;
    int32_t globalLimit = 0;
    int32_t queuedCount = 0;
    int32_t recentSuccesses = 0;
    int64_t cooldownRemainingMs = 0;
};

struct MtProxyHandshakeQueuedGrant {
    ConnectionSocket *socket = nullptr;
    std::string key;
    uint32_t generation = 0;
    uint32_t delayMs = 0;
    int32_t timerMode = 0;
    MtProxyRequestClass requestClass = MtProxyRequestClass::Generic;
    int32_t priority = 0;
    int32_t endpointActive = 0;
    int32_t endpointQueued = 0;
    int32_t globalActive = 0;
    int32_t globalLimit = 0;
    bool ipv6 = false;
};

enum class MtProxyHandshakeCooldownKind {
    None,
    TcpFailure,
    Freeze,
    Failure,
    FreezeObserved,
};

struct MtProxyHandshakeReleaseRequest {
    ConnectionSocket *socket = nullptr;
    std::string key;
    bool hadAdmission = false;
    bool wasActive = false;
    bool succeeded = false;
    bool neutralSchedulerWaitRelease = false;
    bool lifecycleHandshakeAbort = false;
    bool suppressQueuedGrant = false;
    bool shouldApplyTcpFailureCooldown = false;
    bool shouldApplyFreezeCooldown = false;
    int32_t connectionPatternMode = 0;
    int64_t now = 0;
};

struct MtProxyHandshakeReleaseDecision {
    bool ignored = false;
    bool publishHoldPhase = false;
    bool hasNextRequest = false;
    MtProxyHandshakeQueuedGrant nextRequest;
    int32_t globalActive = 0;
    int32_t globalLimit = 0;
    int32_t queuedCount = 0;
    MtProxyHandshakeCooldownKind cooldownKind = MtProxyHandshakeCooldownKind::None;
    int32_t cooldownPenalty = 0;
    int64_t cooldownRemainingMs = 0;
};

bool mtProxyHandshakeSchedulerUsesAdmission(int32_t mode);
int32_t mtProxyHandshakePriorityForRequestClass(MtProxyRequestClass requestClass);
MtProxyRequestClass mtProxyRequestClassForPriority(int32_t priority);
const char *mtProxyRequestClassName(MtProxyRequestClass requestClass);
uint32_t mtProxyHandshakeSchedulerRetryDelay(int64_t now, int64_t cooldownUntil, int32_t priority, int32_t mode);
MtProxyHandshakeAdmissionDecision mtProxyHandshakeSchedulerAdmit(const MtProxyHandshakeAdmissionRequest &request);
void mtProxyHandshakeSchedulerCancel(ConnectionSocket *socket);
MtProxyHandshakeReleaseDecision mtProxyHandshakeSchedulerRelease(const MtProxyHandshakeReleaseRequest &request);

#endif
