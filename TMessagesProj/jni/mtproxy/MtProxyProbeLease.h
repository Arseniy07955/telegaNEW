#ifndef MTPROXYPROBELEASE_H
#define MTPROXYPROBELEASE_H

#include "MtProxyProbeCoordinator.h"

#include <stdint.h>
#include <string>

class MtProxyProbeLease {
public:
    bool active() const;
    uint64_t ownerToken() const;
    const std::string &key() const;

    void acquire(const MtProxyProbeCoordinator::ProbeKey &probeKey, uint64_t token);
    void release();
    void touch(int64_t now);

private:
    MtProxyProbeCoordinator::ProbeKey probeKey;
    uint64_t token = 0;
};

#endif
