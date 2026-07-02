#include "MtProxyProbeLease.h"

bool MtProxyProbeLease::active() const {
    return token != 0 && !probeKey.key.empty();
}

uint64_t MtProxyProbeLease::ownerToken() const {
    return token;
}

const std::string &MtProxyProbeLease::key() const {
    return probeKey.key;
}

void MtProxyProbeLease::acquire(const MtProxyProbeCoordinator::ProbeKey &capturedProbeKey, uint64_t ownerToken) {
    release();
    probeKey = capturedProbeKey;
    token = ownerToken;
}

void MtProxyProbeLease::release() {
    if (active()) {
        MtProxyProbeCoordinator::cancelOwner(probeKey, token);
    }
    token = 0;
    probeKey = MtProxyProbeCoordinator::ProbeKey();
}

void MtProxyProbeLease::touch(int64_t now) {
    if (!active()) {
        return;
    }
    MtProxyProbeCoordinator::touchOwner(probeKey, token, now);
}
