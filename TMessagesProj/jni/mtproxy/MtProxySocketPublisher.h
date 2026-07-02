#ifndef MTPROXYSOCKETPUBLISHER_H
#define MTPROXYSOCKETPUBLISHER_H

#include "MtProxyPhaseContract.h"

#include <functional>
#include <string>

struct MtProxySocketObservation {
    const char *phase = MtProxyPhase::ConnectionNotStarted;
    const char *reason = "unknown";
    std::string endpointKey;
    std::string probeKey;
    std::string networkEndpointKey;
    bool publishVisibleStage = true;
    bool recordEndpointFailure = false;
};

struct MtProxySocketPublisherCallbacks {
    std::function<void(const MtProxySocketObservation &observation)> publishVisibleStage;
    std::function<void(const MtProxySocketObservation &observation)> recordEndpointFailure;
};

struct MtProxySocketPublisherContext {
    std::string endpointKey;
    std::string probeKey;
    std::string networkEndpointKey;
};

MtProxySocketObservation mtProxyNormalizeSocketObservation(const MtProxySocketObservation &observation);
void mtProxyPublishSocketObservation(const MtProxySocketObservation &observation, const MtProxySocketPublisherCallbacks &callbacks);
void mtProxyPublishSocketObservation(const MtProxySocketObservation &observation, const MtProxySocketPublisherContext &context, const MtProxySocketPublisherCallbacks &callbacks);
bool mtProxySocketObservationIsHighRiskPhase(const char *phase);

#endif
